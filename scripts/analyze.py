"""분양가 vs 시세 비교, 필요자금 계산, 필터링"""
import logging
import re

from config import MIN_PROFIT_THRESHOLD, INTEREST_RATE
from rules import evaluate_regulations
from fetch_prices import estimate_market_price

logger = logging.getLogger(__name__)


def analyze_subscriptions(subscriptions: list[dict]) -> list[dict]:
    """청약 목록 분석.

    - 접수예정/접수중: 차익 무관하게 전부 포함
    - 접수마감/마감: 차익 1억 이상만 포함
    """
    results = []

    for idx, item in enumerate(subscriptions):
        house_nm = item.get("HOUSE_NM", "알 수 없음")
        logger.info(f"[분석 {idx+1}/{len(subscriptions)}] {house_nm}")

        models = item.get("models", [])
        if not models:
            logger.info(f"  → 주택형 정보 없음, 건너뜀")
            continue

        # 시도명 추출
        sido = (
            item.get("SUBSCRPT_AREA_CODE_NM", "")
            or item.get("_region", "")
            or ""
        )

        # 상태 미리 판단
        status = _determine_status(item)
        # _is_upcoming 플래그: 무순위/잔여, 오피스텔 중 아직 마감 안 된 건
        is_upcoming = status in ("접수예정", "접수중") or item.get("_is_upcoming", False)

        # 규제 정보
        regulations = evaluate_regulations(item)

        # 주소
        address = item.get("HSSPLY_ADRES", "") or ""

        # 주택형별 분석
        analyzed_models = []
        max_profit = 0

        for model in models:
            result = _analyze_model(model, sido, address)
            if result:
                analyzed_models.append(result)
                if result["profit"] > max_profit:
                    max_profit = result["profit"]

        # 접수예정/접수중 → 무조건 포함, 마감 → 1억+ 필터
        if not is_upcoming and max_profit < MIN_PROFIT_THRESHOLD:
            logger.info(f"  → 마감 + 최대차익 {max_profit/10000:,.0f}만원 < 1억, 제외")
            continue

        # 분석 모델이 없어도 접수예정이면 포함 (분양가 정보만 표시)
        entry = _build_entry(item, analyzed_models, regulations, max_profit)
        results.append(entry)
        label = "예정" if is_upcoming else "차익"
        logger.info(f"  → {label} | 최대차익 {max_profit/10000:,.0f}만원 ✓")

    upcoming = sum(1 for r in results if r["status"] in ("접수예정", "접수중"))
    logger.info(f"[분석완료] {len(results)}건 (접수예정/중 {upcoming}건, 마감+차익1억 {len(results)-upcoming}건)")
    return results


def _parse_exclusive_area(model: dict) -> float:
    """전용면적 추출: EXCLUSE_AR 필드 또는 HOUSE_TY에서 파싱.

    HOUSE_TY 예시: '059.7419B' → 59.7419㎡, '084.9900A' → 84.99㎡
    """
    # 1. 명시적 필드
    try:
        val = float(model.get("EXCLUSE_AR", 0))
        if val > 0:
            return val
    except (ValueError, TypeError):
        pass

    # 2. HOUSE_TY에서 추출
    house_ty = model.get("HOUSE_TY", "")
    match = re.match(r"(\d+\.?\d*)", house_ty)
    if match:
        return float(match.group(1))

    return 0.0


def _analyze_model(model: dict, sido: str, address: str = "") -> dict | None:
    """개별 주택형 분석."""
    # 분양가 (최고가, 만원 단위)
    try:
        supply_price_raw = model.get("LTTOT_TOP_AMOUNT", "0")
        supply_price_str = str(supply_price_raw).replace(",", "").strip()
        supply_price = int(float(supply_price_str)) * 10000  # 만원 → 원
    except (ValueError, TypeError):
        return None

    if supply_price <= 0:
        return None

    # 면적
    try:
        supply_area = float(model.get("SUPLY_AR", 0))
    except (ValueError, TypeError):
        supply_area = 0

    exclusive_area = _parse_exclusive_area(model)
    if exclusive_area <= 0:
        return None

    # 실거래 + KB 시세 추정
    market = estimate_market_price(exclusive_area, sido, address)
    market_price = market["estimated_price"]

    if market_price <= 0:
        return None

    # 차익
    profit = market_price - supply_price

    # 3.3m2당 분양가
    pyeong = supply_area / 3.3058 if supply_area > 0 else 1
    price_per_pyeong = int(supply_price / pyeong)

    # 필요자금 계산
    funding = _calculate_funding(supply_price, market_price, market["jeonse_ratio"])

    # 세대수
    try:
        supply_count = int(model.get("SUPLY_HSHLDCO", 0))
    except (ValueError, TypeError):
        supply_count = 0

    return {
        "housing_type": model.get("HOUSE_TY", ""),
        "supply_area": supply_area,
        "exclusive_area": exclusive_area,
        "supply_price": supply_price,
        "price_per_pyeong": price_per_pyeong,
        "market_price": market_price,
        "profit": profit,
        "household_count": supply_count,
        "price_source": market.get("source", ""),
        "funding": funding,
    }


def _calculate_funding(supply_price: int, market_price: int, jeonse_ratio: float) -> dict:
    """예상 필요자금 계산."""
    down_payment = int(supply_price * 0.1)          # 계약금 10%
    interim_payment = int(supply_price * 0.6)       # 중도금 60%
    balance = int(supply_price * 0.3)               # 잔금 30%

    estimated_jeonse = int(market_price * jeonse_ratio)
    # 실투자금 = 계약금 + max(잔금 - 전세가, 0)
    actual_investment = down_payment + max(balance - estimated_jeonse, 0)

    # 중도금 이자 (약 2년 기준)
    interim_interest = int(interim_payment * INTEREST_RATE * 2)

    return {
        "down_payment": down_payment,
        "interim_payment": interim_payment,
        "balance": balance,
        "estimated_jeonse": estimated_jeonse,
        "actual_investment": actual_investment,
        "interim_interest": interim_interest,
        "jeonse_ratio": jeonse_ratio,
    }


def _build_entry(item: dict, models: list[dict], regulations: dict, max_profit: int) -> dict:
    """최종 출력 데이터 구성."""
    status = _determine_status(item)
    # 무순위/잔여/오피스텔 중 마감 안 된 건 → 접수예정으로 표시
    if status == "마감" and item.get("_is_upcoming", False):
        status = "접수예정"

    return {
        "id": item.get("HOUSE_MANAGE_NO", ""),
        "name": item.get("HOUSE_NM", ""),
        "region": item.get("SUBSCRPT_AREA_CODE_NM", "") or item.get("_region", ""),
        "sido": item.get("SUBSCRPT_AREA_CODE_NM", ""),
        "sigungu": "",
        "address": item.get("HSSPLY_ADRES", ""),
        "constructor": item.get("BSNS_MBY_NM", ""),
        "homepage": item.get("HMPG_ADRES", ""),
        "total_households": item.get("TOT_SUPLY_HSHLDCO", 0),
        "status": status,
        "subscription_type": item.get("_type", "APT"),
        "max_profit": max_profit,

        # 일정
        "schedule": {
            "announcement_date": item.get("RCRIT_PBLANC_DE", ""),
            "special_supply_date": item.get("SPSPLY_RCEPT_BGNDE", ""),
            "first_priority_date": item.get("GNRL_RNK1_CRSPAREA_RCPTDE", ""),
            "second_priority_date": item.get("GNRL_RNK2_CRSPAREA_RCPTDE", ""),
            "winner_announce_date": item.get("PRZWNER_PRESNATN_DE", ""),
            "contract_start": item.get("CNTRCT_CNCLS_BGNDE", ""),
            "contract_end": item.get("CNTRCT_CNCLS_ENDDE", ""),
            "move_in_date": item.get("MVN_PREARNGE_YM", ""),
            "receipt_start": item.get("RCEPT_BGNDE", ""),
            "receipt_end": item.get("RCEPT_ENDDE", ""),
        },

        # 자격요건
        "qualification": {
            "region_limit": item.get("SUBSCRPT_RCEPT_TY_NM", ""),
            "house_type": item.get("HOUSE_SECD_NM", ""),
            "rent_secd": item.get("RENT_SECD_NM", ""),
            "apply_url": item.get("PBLANC_URL", ""),
        },

        # 규제정보
        "regulations": regulations,

        # 주택형별 분석
        "models": models,
    }


def _determine_status(item: dict) -> str:
    """청약 상태 판단."""
    from datetime import datetime
    now = datetime.now()

    receipt_start = item.get("RCEPT_BGNDE", "")
    receipt_end = item.get("RCEPT_ENDDE", "")
    winner_date = item.get("PRZWNER_PRESNATN_DE", "")

    try:
        if receipt_start:
            start_dt = datetime.strptime(receipt_start, "%Y-%m-%d")
            if now < start_dt:
                return "접수예정"
        if receipt_end:
            end_dt = datetime.strptime(receipt_end, "%Y-%m-%d")
            if now <= end_dt:
                return "접수중"
        if winner_date:
            winner_dt = datetime.strptime(winner_date, "%Y-%m-%d")
            if now <= winner_dt:
                return "접수마감"
    except ValueError:
        pass

    return "마감"
