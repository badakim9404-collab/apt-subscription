"""시세 수집: 실거래 API (XML) + KB부동산 ㎡당 평균가격"""
import logging
from datetime import datetime, timedelta

import requests
import xmltodict

try:
    import PublicDataReader as pdr
    HAS_PDR = True
except ImportError:
    HAS_PDR = False

import re

from config import DATA_GO_KR_API_KEY, TRADE_API_BASE, OFFICETEL_TRADE_API_BASE, ALL_LAWD, LAWD_BY_SIDO

logger = logging.getLogger(__name__)

# KB 데이터 캐시: "APT_서울_강남구" → 시군구, "APT_서울" → 시도 폴백
_kb_cache = {}

# 오피스텔/아파트 면적당 가격 비율 (아파트 실거래/KB 폴백 시 적용)
_OFFI_APT_RATIO = {
    "서울": 0.775,
    "경기": 0.65,
    "인천": 0.65,
}

# 실거래 데이터 캐시: lawd_cd → trades
_trade_cache = {}

# KB 시도별 지역코드 (10자리)
_KB_SIDO_CODES = {
    "서울": "1100000000",
    "경기": "4100000000",
    "인천": "2800000000",
}


def _load_kb_cache():
    """KB 시세 데이터를 한 번만 로드하여 캐시.

    KB는 아파트("01")만 지원 (오피스텔 미지원).
    캐시 키: "APT_서울_강남구" (시군구), "APT_서울" (시도 폴백).
    오피스텔은 실거래 API 전용으로 시세 추정.
    """
    if _kb_cache:
        return
    if not HAS_PDR:
        logger.warning("PublicDataReader 미설치. KB시세 사용 불가.")
        return

    try:
        kb = pdr.Kbland()

        # 1. 시군구 레벨 로드 (서울/경기/인천) — 아파트만
        for sido, region_code in _KB_SIDO_CODES.items():
            try:
                df_price = kb.get_average_price_per_squaremeter(
                    "01", "01", 지역코드=region_code,
                )
                latest_date = df_price["날짜"].max()
                latest = df_price[df_price["날짜"] == latest_date]
                for _, row in latest.iterrows():
                    name = row["지역명"]
                    price_per_m2 = float(row["㎡당 평균가격"])
                    _kb_cache[f"APT_{sido}_{name}"] = {"price_per_m2": price_per_m2}
                logger.info(f"  KB ㎡당 평균가격 - {sido} ({len(latest)}개 시군구, 기준: {latest_date})")

                df_jeonse = kb.get_jeonse_price_ratio("01", 지역코드=region_code)
                latest_jdate = df_jeonse["날짜"].max()
                latest_j = df_jeonse[df_jeonse["날짜"] == latest_jdate]
                for _, row in latest_j.iterrows():
                    name = row["지역명"]
                    ratio = float(row["전세가격비율"]) / 100.0
                    key = f"APT_{sido}_{name}"
                    if key in _kb_cache:
                        _kb_cache[key]["jeonse_ratio"] = ratio
                    else:
                        _kb_cache[key] = {"jeonse_ratio": ratio}
                logger.info(f"  KB 전세가율 - {sido} ({len(latest_j)}개 시군구, 기준: {latest_jdate})")
            except Exception as e:
                logger.warning(f"  KB {sido} 시군구 로드 실패: {e}")

        # 2. 시도 레벨 폴백용 로드
        df_price = kb.get_average_price_per_squaremeter("01", "01")
        latest_date = df_price["날짜"].max()
        latest_price = df_price[df_price["날짜"] == latest_date]
        for _, row in latest_price.iterrows():
            region = row["지역명"]
            price_per_m2 = float(row["㎡당 평균가격"])
            _kb_cache[f"APT_{region}"] = {"price_per_m2": price_per_m2}
        logger.info(f"  KB ㎡당 평균가격 시도 폴백 ({len(latest_price)}개 지역)")

        df_jeonse = kb.get_jeonse_price_ratio("01")
        latest_jdate = df_jeonse["날짜"].max()
        latest_jeonse = df_jeonse[df_jeonse["날짜"] == latest_jdate]
        for _, row in latest_jeonse.iterrows():
            region = row["지역명"]
            ratio = float(row["전세가격비율"]) / 100.0
            key = f"APT_{region}"
            if key in _kb_cache:
                _kb_cache[key]["jeonse_ratio"] = ratio
            else:
                _kb_cache[key] = {"jeonse_ratio": ratio}

    except Exception as e:
        logger.error(f"  KB 데이터 로드 실패: {e}")


def _get_kb_region(sido_nm: str) -> str:
    """시도명 → KB 지역명."""
    mapping = {
        "서울특별시": "서울", "서울": "서울",
        "경기도": "경기", "경기": "경기",
        "인천광역시": "인천", "인천": "인천",
    }
    for key, val in mapping.items():
        if key in sido_nm:
            return val
    return sido_nm


# ===== 실거래 API (XML) =====

def _area_bucket(area_m2: float) -> int:
    """면적을 10㎡ 버킷으로 구분 (캐시키 용)."""
    return int(area_m2 // 10) * 10


def _fetch_trades_raw(lawd_cd: str, months: int = 6, property_type: str = "APT") -> list[dict]:
    """시군구 전체 실거래 조회 (면적 필터 없이, 캐시용).

    property_type: "APT" → 아파트 API, "OFFI" → 오피스텔 API (실패 시 아파트 폴백)
    """
    if not DATA_GO_KR_API_KEY:
        return []

    api_base = OFFICETEL_TRADE_API_BASE if property_type == "OFFI" else TRADE_API_BASE

    all_trades = []
    now = datetime.now()
    offi_failed = False

    for i in range(months):
        dt = now - timedelta(days=30 * i)
        deal_ymd = dt.strftime("%Y%m")

        params = {
            "serviceKey": DATA_GO_KR_API_KEY,
            "LAWD_CD": lawd_cd,
            "DEAL_YMD": deal_ymd,
            "pageNo": 1,
            "numOfRows": 1000,
        }
        try:
            resp = requests.get(api_base, params=params, timeout=30)
            resp.raise_for_status()
            data = xmltodict.parse(resp.text)
        except Exception as e:
            logger.debug(f"  실거래 API ({lawd_cd}, {deal_ymd}): {e}")
            if property_type == "OFFI" and not offi_failed:
                offi_failed = True
            continue

        header = data.get("response", {}).get("header", {})
        if header.get("resultCode") != "000":
            continue

        body = data.get("response", {}).get("body", {})
        items = body.get("items", {})
        if not items:
            continue
        item_list = items.get("item", [])
        if isinstance(item_list, dict):
            item_list = [item_list]

        for item in item_list:
            try:
                exclu_ar = float(item.get("excluUseAr", 0))
                amount_str = str(item.get("dealAmount", "0")).replace(",", "").strip()
                amount = int(amount_str) * 10000
                build_year = int(item.get("buildYear", 0))
            except (ValueError, TypeError):
                continue
            if amount > 0:
                all_trades.append({
                    "price": amount,
                    "area": exclu_ar,
                    "apt_name": item.get("aptNm", ""),
                    "dong": item.get("umdNm", ""),
                    "build_year": build_year,
                })

    # 오피스텔 API 실패 시 아파트 API로 폴백
    if property_type == "OFFI" and not all_trades and offi_failed:
        logger.warning(f"  오피스텔 실거래 API 미지원 → 아파트 실거래로 폴백 ({lawd_cd})")
        return _fetch_trades_raw(lawd_cd, months, "APT")

    return all_trades


MIN_BUILD_YEAR = 2015  # 신축 기준 (최근 ~10년)


def fetch_recent_trades(
    lawd_cd: str, area_m2: float, months: int = 6, dong: str = "",
    property_type: str = "APT",
) -> list[dict]:
    """시군구별 최근 N개월 실거래. 신축 우선 단계적 필터링:

    신축(2015+) 거래를 먼저 시도하고, 부족하면 전체로 폴백.
    1단계: 신축 + 같은 동 + ±5m²
    2단계: 신축 + 시군구 + ±5m²
    3단계: 신축 + 시군구 + ±10m²
    4단계: 전체 + 시군구 + ±5m²
    5단계: 전체 + 시군구 + ±10m²
    """
    cache_key = f"{property_type}_{lawd_cd}"
    if cache_key not in _trade_cache:
        _trade_cache[cache_key] = _fetch_trades_raw(lawd_cd, months, property_type)

    all_trades = _trade_cache[cache_key]
    recent = [t for t in all_trades if t.get("build_year", 0) >= MIN_BUILD_YEAR]

    # 1단계: 신축 + 같은 동 + ±5m²
    if dong:
        result = [t for t in recent if t["dong"] == dong and abs(t["area"] - area_m2) <= 5]
        if len(result) >= 5:
            return result

    # 2단계: 신축 + 시군구 + ±5m²
    result = [t for t in recent if abs(t["area"] - area_m2) <= 5]
    if len(result) >= 10:
        return result

    # 3단계: 신축 + 시군구 + ±10m²
    result = [t for t in recent if abs(t["area"] - area_m2) <= 10]
    if len(result) >= 10:
        return result

    # 4단계: 전체 + 시군구 + ±5m²
    result = [t for t in all_trades if abs(t["area"] - area_m2) <= 5]
    if len(result) >= 10:
        return result

    # 5단계: 전체 + 시군구 + ±10m²
    return [t for t in all_trades if abs(t["area"] - area_m2) <= 10]


def get_median_price(trades: list[dict], target_area: float = 0) -> int:
    """면적 보정 중위가 계산.

    target_area > 0이면: 각 거래를 ₩/m²로 환산 → 중위 ₩/m² → target_area에 곱.
    target_area = 0이면: 기존 방식 (거래가 그대로 중위가).
    """
    if not trades:
        return 0

    if target_area > 0:
        # ₩/m² 기반 보정
        ppm2_list = sorted(
            t["price"] / t["area"] for t in trades if t.get("area", 0) > 0
        )
        if not ppm2_list:
            return 0
        mid = len(ppm2_list) // 2
        if len(ppm2_list) % 2 == 0:
            median_ppm2 = (ppm2_list[mid - 1] + ppm2_list[mid]) / 2
        else:
            median_ppm2 = ppm2_list[mid]
        return int(median_ppm2 * target_area)

    prices = sorted(t["price"] for t in trades)
    mid = len(prices) // 2
    if len(prices) % 2 == 0:
        return (prices[mid - 1] + prices[mid]) // 2
    return prices[mid]


def get_lawd_cd_for_address(address: str) -> str:
    """주소에서 LAWD_CD 5자리 추출. 시도명을 우선 확인하여 동명 구 충돌 방지."""
    if not address:
        return ""

    # 1단계: 시도 판별
    sido = None
    if "서울" in address:
        sido = "서울"
    elif "인천" in address:
        sido = "인천"
    elif "경기" in address:
        sido = "경기"

    # 2단계: 시도가 확인된 경우 해당 시도의 LAWD만 검색
    if sido:
        sido_lawd = LAWD_BY_SIDO.get(sido, {})
        for code, name in sido_lawd.items():
            if name in address:
                return code
        for code, name in sido_lawd.items():
            if "구" in name:
                gu_name = name.split()[-1] if " " in name else name
                if gu_name in address:
                    return code

    # 3단계: 시도 불명시 전체 검색 (기존 로직)
    for code, name in ALL_LAWD.items():
        if name in address:
            return code
    for code, name in ALL_LAWD.items():
        if "구" in name:
            gu_name = name.split()[-1] if " " in name else name
            if gu_name in address:
                return code

    return ""


def _extract_dong_from_address(address: str) -> str:
    """주소에서 법정동(읍면동) 추출."""
    if not address:
        return ""
    match = re.search(r"(\S+[동리읍면])\s+\d", address)
    if match:
        return match.group(1)
    return ""


# ===== 통합 시세 추정 =====

def _get_kb_data(sido_nm: str, lawd_cd: str, property_type: str = "APT") -> tuple[dict, str]:
    """시군구 레벨 KB 데이터 조회. 없으면 시도 폴백.

    KB는 아파트만 지원. 오피스텔이면 빈 데이터 반환 (실거래 전용).
    Returns: (kb_data dict, source_label str)
    """
    # 오피스텔은 KB 미지원 → 아파트 KB를 참고용 폴백
    is_offi = property_type == "OFFI"

    region = _get_kb_region(sido_nm)

    # 1. 시군구 레벨 시도
    if lawd_cd:
        sigungu_name = ALL_LAWD.get(lawd_cd, "")
        if sigungu_name:
            key = f"APT_{region}_{sigungu_name}"
            data = _kb_cache.get(key)
            if data:
                label = f"KB(APT폴백) {sigungu_name}" if is_offi else f"KB {sigungu_name}"
                return data, label

    # 2. 시도 레벨 폴백
    data = _kb_cache.get(f"APT_{region}", {})
    label = f"KB(APT폴백) {region} 평균" if is_offi else f"KB {region} 평균"
    return data, label


def estimate_market_price(area_m2: float, sido_nm: str, address: str = "", subscription_type: str = "APT") -> dict:
    """실거래 중위가 우선, 없으면 KB ㎡당 평균가격으로 추정.

    subscription_type: "APT" 또는 "무순위/잔여" → 아파트 데이터
                       "오피스텔/도시형" → 아파트 데이터 × 오피스텔 비율
    오피스텔 전용 API 미구독 시 아파트 데이터에 지역별 비율을 적용하여 추정.
    """
    _load_kb_cache()

    # 오피스텔 판별 + 비율
    is_offi = subscription_type == "오피스텔/도시형"
    property_type = "OFFI" if is_offi else "APT"
    region = _get_kb_region(sido_nm)
    offi_ratio = _OFFI_APT_RATIO.get(region, 0.65) if is_offi else 1.0

    lawd_cd = get_lawd_cd_for_address(address) if address else ""
    dong = _extract_dong_from_address(address)

    # KB 데이터 (시군구 우선 → 시도 폴백)
    kb_data, kb_source = _get_kb_data(sido_nm, lawd_cd, property_type)
    jeonse_ratio = kb_data.get("jeonse_ratio", 0.6)

    # 1. 실거래 중위가 시도
    trade_price = 0
    trade_count = 0

    if lawd_cd:
        trades = fetch_recent_trades(lawd_cd, area_m2, months=6, dong=dong, property_type=property_type)
        trade_count = len(trades)
        # 대형(120m²+)은 거래 적어 편향 위험 → 최소 20건, 일반은 5건
        min_trades = 20 if area_m2 > 120 else 5
        if trade_count >= min_trades:
            trade_price = get_median_price(trades, target_area=area_m2)

    if trade_price > 0:
        # 오피스텔 + 아파트 실거래 폴백인 경우 비율 적용
        if is_offi and offi_ratio < 1.0:
            trade_price = int(trade_price * offi_ratio)

        # 동 단위 매칭 여부 표시
        source_detail = f"실거래 중위가 ({trade_count}건"
        if dong and trades and trades[0].get("dong") == dong:
            source_detail += f", {dong}"
        if is_offi:
            source_detail += f", x{offi_ratio:.0%}"
        source_detail += ")"
        return {
            "estimated_price": trade_price,
            "jeonse_ratio": jeonse_ratio,
            "estimated_jeonse": int(trade_price * jeonse_ratio),
            "trade_count": trade_count,
            "source": source_detail,
        }

    # 2. KB ㎡당 평균가격 폴백 (시군구 → 시도)
    price_per_m2 = kb_data.get("price_per_m2", 0)
    if price_per_m2 > 0:
        estimated = int(price_per_m2 * area_m2 * 10000 * offi_ratio)
        if is_offi:
            kb_source += f" x{offi_ratio:.0%}"
        return {
            "estimated_price": estimated,
            "jeonse_ratio": jeonse_ratio,
            "estimated_jeonse": int(estimated * jeonse_ratio),
            "trade_count": trade_count,
            "source": kb_source,
        }

    return {
        "estimated_price": 0,
        "jeonse_ratio": jeonse_ratio,
        "estimated_jeonse": 0,
        "trade_count": 0,
        "source": "데이터 없음",
    }
