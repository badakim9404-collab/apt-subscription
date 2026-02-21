"""분양가 vs 시세 비교, 필요자금 계산, 필터링"""
import logging
import re
from datetime import datetime

from config import (
    MIN_PROFIT_THRESHOLD, INTEREST_RATE,
    HOUSEHOLD_INCOME, EXISTING_DEBT_ANNUAL,
    MORTGAGE_RATE, MORTGAGE_YEARS, DSR_LIMIT, IS_FIRST_HOME,
)
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

        # 타입 판별 (오피스텔 vs 아파트)
        sub_type = item.get("_type", "APT")

        # 주택형별 분석
        analyzed_models = []

        for model in models:
            result = _analyze_model(model, sido, address, regulations, sub_type)
            if result:
                analyzed_models.append(result)

        # 동일 단지 내 교차 추정 (실거래 → KB 폴백 모델에 적용)
        analyzed_models = _apply_cross_model_estimation(analyzed_models, regulations)

        # max_profit: 분석 모델 있으면 음수 허용, 없으면 0 (시세 데이터 없음)
        if analyzed_models:
            max_profit = max(m["profit"] for m in analyzed_models)
        else:
            max_profit = 0

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


def _analyze_model(model: dict, sido: str, address: str = "", regulations: dict | None = None, subscription_type: str = "APT") -> dict | None:
    """개별 주택형 분석."""
    # 분양가 (만원 단위) — APT: LTTOT_TOP_AMOUNT, 오피스텔: SUPLY_AMOUNT
    try:
        supply_price_raw = model.get("LTTOT_TOP_AMOUNT") or model.get("SUPLY_AMOUNT") or "0"
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

    # 전용면적 — APT: EXCLUSE_AR/HOUSE_TY 파싱, 오피스텔: EXCLUSE_AR
    exclusive_area = _parse_exclusive_area(model)
    if exclusive_area <= 0:
        return None

    # 실거래 + KB 시세 추정
    market = estimate_market_price(exclusive_area, sido, address, subscription_type)
    market_price = market["estimated_price"]

    if market_price <= 0:
        return None

    # 차익
    profit = market_price - supply_price

    # 3.3m2당 분양가
    pyeong = supply_area / 3.3058 if supply_area > 0 else 1
    price_per_pyeong = int(supply_price / pyeong)

    # 필요자금 계산
    funding = _calculate_funding(supply_price, market_price, market["jeonse_ratio"], regulations or {})

    # 세대수
    try:
        supply_count = int(model.get("SUPLY_HSHLDCO", 0))
    except (ValueError, TypeError):
        supply_count = 0

    return {
        "housing_type": model.get("HOUSE_TY") or model.get("TP") or "",
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


def _apply_cross_model_estimation(analyzed_models: list[dict], regulations: dict | None = None) -> list[dict]:
    """동일 단지 내 실거래 기반 ₩/m² 교차 추정.

    실거래 데이터가 충분한 주택형의 ₩/m²를 사용하여
    데이터 부족(KB 폴백) 주택형의 시세를 재추정.
    """
    if not analyzed_models:
        return analyzed_models

    # 실거래 기반 모델에서 ₩/m² 수집
    trade_prices_per_m2 = []
    for m in analyzed_models:
        if "실거래" in m.get("price_source", ""):
            ppm2 = m["market_price"] / m["exclusive_area"]
            trade_prices_per_m2.append(ppm2)

    if not trade_prices_per_m2:
        return analyzed_models

    avg_ppm2 = sum(trade_prices_per_m2) / len(trade_prices_per_m2)

    # 실거래 아닌 모델에 교차 추정 적용
    for m in analyzed_models:
        if "실거래" not in m.get("price_source", ""):
            new_market = int(avg_ppm2 * m["exclusive_area"])
            jeonse_ratio = m["funding"]["jeonse_ratio"]
            m["market_price"] = new_market
            m["profit"] = new_market - m["supply_price"]
            m["price_source"] = f"단지내 m²단가 추정 ({avg_ppm2/10000:.0f}만/m²)"
            m["funding"] = _calculate_funding(m["supply_price"], new_market, jeonse_ratio, regulations or {})
            logger.info(f"    → {m['housing_type']} 교차추정: {new_market/100000000:.2f}억 ({avg_ppm2/10000:.0f}만/m²)")

    return analyzed_models


def _calculate_max_loan(supply_price: int, regulations: dict) -> dict:
    """LTV·DSR 기반 최대 대출가능액 계산."""
    is_speculative = regulations.get("is_speculative_zone", False)
    is_adjusted = regulations.get("is_adjusted_zone", False)

    # --- LTV 한도 ---
    if IS_FIRST_HOME:
        if is_speculative:
            if supply_price <= 900_000_000:
                ltv_rate = 0.80
                ltv_limit = min(int(supply_price * ltv_rate), 600_000_000)
            else:
                ltv_rate = 0.50
                ltv_limit = int(supply_price * ltv_rate)
        elif is_adjusted:
            if supply_price <= 900_000_000:
                ltv_rate = 0.80
                ltv_limit = min(int(supply_price * ltv_rate), 600_000_000)
            else:
                ltv_rate = 0.70
                ltv_limit = int(supply_price * ltv_rate)
        else:
            ltv_rate = 0.80
            ltv_limit = int(supply_price * ltv_rate)
    else:
        # 일반(비 생애최초) — 단순화
        ltv_rate = 0.70 if (is_speculative or is_adjusted) else 0.80
        ltv_limit = int(supply_price * ltv_rate)

    # --- DSR 한도 ---
    r = MORTGAGE_RATE / 12
    n = MORTGAGE_YEARS * 12
    annual_repay_factor = 12 * r * (1 + r) ** n / ((1 + r) ** n - 1)
    dsr_limit_amount = int(
        (HOUSEHOLD_INCOME * DSR_LIMIT - EXISTING_DEBT_ANNUAL) / annual_repay_factor
    )
    dsr_limit_amount = max(dsr_limit_amount, 0)

    max_loan = min(ltv_limit, dsr_limit_amount)

    return {
        "ltv_rate": ltv_rate,
        "ltv_limit": ltv_limit,
        "dsr_limit_amount": dsr_limit_amount,
        "max_loan": max_loan,
    }


def _calculate_funding(supply_price: int, market_price: int, jeonse_ratio: float, regulations: dict) -> dict:
    """예상 필요자금 계산 (전세 투자 / 대출 매수 시나리오)."""
    down_payment = int(supply_price * 0.1)          # 계약금 10%
    interim_payment = int(supply_price * 0.6)       # 중도금 60%
    balance = int(supply_price * 0.3)               # 잔금 30%

    # 시나리오 1: 전세 투자
    estimated_jeonse = int(market_price * jeonse_ratio)
    jeonse_investment = supply_price - estimated_jeonse  # 마이너스 허용

    # 시나리오 2: 대출 매수
    loan_info = _calculate_max_loan(supply_price, regulations)
    loan_amount = loan_info["max_loan"]
    loan_investment = supply_price - loan_amount

    # 중도금 이자 (약 2년 기준)
    interim_interest = int(interim_payment * INTEREST_RATE * 2)

    return {
        "down_payment": down_payment,
        "interim_payment": interim_payment,
        "balance": balance,
        "estimated_jeonse": estimated_jeonse,
        "jeonse_investment": jeonse_investment,
        "loan_amount": loan_amount,
        "loan_investment": loan_investment,
        "ltv_rate": loan_info["ltv_rate"],
        "ltv_limit": loan_info["ltv_limit"],
        "dsr_limit_amount": loan_info["dsr_limit_amount"],
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
            "receipt_start": item.get("RCEPT_BGNDE", "") or item.get("SUBSCRPT_RCEPT_BGNDE", "") or "",
            "receipt_end": item.get("RCEPT_ENDDE", "") or item.get("SUBSCRPT_RCEPT_ENDDE", "") or "",
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
    now = datetime.now()

    receipt_start = item.get("RCEPT_BGNDE", "") or item.get("SUBSCRPT_RCEPT_BGNDE", "") or ""
    receipt_end = item.get("RCEPT_ENDDE", "") or item.get("SUBSCRPT_RCEPT_ENDDE", "") or ""
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
