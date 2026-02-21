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

from config import DATA_GO_KR_API_KEY, TRADE_API_BASE, ALL_LAWD, LAWD_BY_SIDO

logger = logging.getLogger(__name__)

# KB 데이터 캐시
_kb_cache = {}

# 실거래 데이터 캐시: (lawd_cd, area_bucket) → trades
_trade_cache = {}


def _load_kb_cache():
    """KB 시세 데이터를 한 번만 로드하여 캐시."""
    if _kb_cache:
        return
    if not HAS_PDR:
        logger.warning("PublicDataReader 미설치. KB시세 사용 불가.")
        return

    try:
        kb = pdr.Kbland()

        # ㎡당 평균가격 (시도별)
        df_price = kb.get_average_price_per_squaremeter("01", "01")
        latest_date = df_price["날짜"].max()
        latest_price = df_price[df_price["날짜"] == latest_date]
        for _, row in latest_price.iterrows():
            region = row["지역명"]
            price_per_m2 = float(row["㎡당 평균가격"])
            _kb_cache[region] = {"price_per_m2": price_per_m2}
        logger.info(f"  KB ㎡당 평균가격 로드 ({len(latest_price)}개 지역, 기준: {latest_date})")

        # 전세가율 (시도별)
        df_jeonse = kb.get_jeonse_price_ratio("01")
        latest_jdate = df_jeonse["날짜"].max()
        latest_jeonse = df_jeonse[df_jeonse["날짜"] == latest_jdate]
        for _, row in latest_jeonse.iterrows():
            region = row["지역명"]
            ratio = float(row["전세가격비율"]) / 100.0
            if region in _kb_cache:
                _kb_cache[region]["jeonse_ratio"] = ratio
            else:
                _kb_cache[region] = {"jeonse_ratio": ratio}
        logger.info(f"  KB 전세가율 로드 ({len(latest_jeonse)}개 지역, 기준: {latest_jdate})")

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


def _fetch_trades_raw(lawd_cd: str, months: int = 6) -> list[dict]:
    """시군구 전체 실거래 조회 (면적 필터 없이, 캐시용)."""
    if not DATA_GO_KR_API_KEY:
        return []

    all_trades = []
    now = datetime.now()

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
            resp = requests.get(TRADE_API_BASE, params=params, timeout=30)
            resp.raise_for_status()
            data = xmltodict.parse(resp.text)
        except Exception as e:
            logger.debug(f"  실거래 API ({lawd_cd}, {deal_ymd}): {e}")
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
            except (ValueError, TypeError):
                continue
            if amount > 0:
                all_trades.append({
                    "price": amount,
                    "area": exclu_ar,
                    "apt_name": item.get("aptNm", ""),
                    "dong": item.get("umdNm", ""),
                })

    return all_trades


def fetch_recent_trades(
    lawd_cd: str, area_m2: float, months: int = 6, dong: str = "",
) -> list[dict]:
    """시군구별 최근 N개월 실거래. 단계적 필터링:

    1단계: 같은 동 + ±5m² (≥5건이면 사용)
    2단계: 시군구 전체 + ±5m² (≥10건이면 사용)
    3단계: 시군구 전체 + ±10m² (폴백)
    """
    cache_key = lawd_cd
    if cache_key not in _trade_cache:
        _trade_cache[cache_key] = _fetch_trades_raw(lawd_cd, months)

    all_trades = _trade_cache[cache_key]

    # 1단계: 같은 동 + ±5m²
    if dong:
        dong_narrow = [
            t for t in all_trades
            if t["dong"] == dong and abs(t["area"] - area_m2) <= 5
        ]
        if len(dong_narrow) >= 5:
            return dong_narrow

    # 2단계: 시군구 전체 + ±5m²
    sgg_narrow = [t for t in all_trades if abs(t["area"] - area_m2) <= 5]
    if len(sgg_narrow) >= 10:
        return sgg_narrow

    # 3단계: 시군구 전체 + ±10m²
    return [t for t in all_trades if abs(t["area"] - area_m2) <= 10]


def get_median_price(trades: list[dict]) -> int:
    """중위가 계산."""
    if not trades:
        return 0
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

def estimate_market_price(area_m2: float, sido_nm: str, address: str = "") -> dict:
    """실거래 중위가 우선, 없으면 KB ㎡당 평균가격으로 추정.

    실거래 최소 5건 이상일 때만 사용, 미만이면 KB 폴백.
    """
    _load_kb_cache()

    region = _get_kb_region(sido_nm)
    kb_data = _kb_cache.get(region, {})
    jeonse_ratio = kb_data.get("jeonse_ratio", 0.6)

    # 1. 실거래 중위가 시도
    trade_price = 0
    trade_count = 0
    lawd_cd = get_lawd_cd_for_address(address) if address else ""
    dong = _extract_dong_from_address(address)

    if lawd_cd:
        trades = fetch_recent_trades(lawd_cd, area_m2, months=6, dong=dong)
        trade_count = len(trades)
        # 대형(120m²+)은 거래 적어 편향 위험 → 최소 20건, 일반은 5건
        min_trades = 20 if area_m2 > 120 else 5
        if trade_count >= min_trades:
            trade_price = get_median_price(trades)

    if trade_price > 0:
        # 동 단위 매칭 여부 표시
        source_detail = f"실거래 중위가 ({trade_count}건"
        if dong and trades and trades[0].get("dong") == dong:
            source_detail += f", {dong}"
        source_detail += ")"
        return {
            "estimated_price": trade_price,
            "jeonse_ratio": jeonse_ratio,
            "estimated_jeonse": int(trade_price * jeonse_ratio),
            "trade_count": trade_count,
            "source": source_detail,
        }

    # 2. KB ㎡당 평균가격 폴백
    price_per_m2 = kb_data.get("price_per_m2", 0)
    if price_per_m2 > 0:
        estimated = int(price_per_m2 * area_m2 * 10000)
        return {
            "estimated_price": estimated,
            "jeonse_ratio": jeonse_ratio,
            "estimated_jeonse": int(estimated * jeonse_ratio),
            "trade_count": trade_count,
            "source": f"KB {region} 평균",
        }

    return {
        "estimated_price": 0,
        "jeonse_ratio": jeonse_ratio,
        "estimated_jeonse": 0,
        "trade_count": 0,
        "source": "데이터 없음",
    }
