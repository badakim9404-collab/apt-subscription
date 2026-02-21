"""규제 정보 규칙 엔진: 전매제한, 재당첨제한, 거주의무"""


def evaluate_regulations(item: dict) -> dict:
    """청약 API 응답 필드 기반 규제 정보 자동 판단."""
    is_speculative = item.get("SPECLT_RDN_EARTH_AT", "N") == "Y"  # 투기과열지구
    is_adjusted = item.get("MDAT_TRGET_AREA_SECD", "N") == "Y"    # 조정대상지역
    is_price_cap = item.get("PARCPRC_ULS_AT", "N") == "Y"         # 분양가상한제
    is_public = item.get("PUBLIC_HOUSE_EARTH_AT", "N") == "Y"     # 공공주택지구

    # 전매제한
    resale_restriction = _get_resale_restriction(
        is_speculative, is_adjusted, is_price_cap, is_public
    )

    # 재당첨 제한
    rewin_restriction = _get_rewin_restriction(is_speculative, is_adjusted)

    # 거주의무
    residency_obligation = _get_residency_obligation(is_price_cap, is_speculative)

    return {
        "is_speculative_zone": is_speculative,
        "is_adjusted_zone": is_adjusted,
        "is_price_cap": is_price_cap,
        "is_public_zone": is_public,
        "resale_restriction": resale_restriction,
        "rewin_restriction": rewin_restriction,
        "residency_obligation": residency_obligation,
    }


def _get_resale_restriction(
    speculative: bool, adjusted: bool, price_cap: bool, public: bool
) -> dict:
    if speculative and price_cap:
        return {
            "period": "소유권이전등기 시까지 (최대 10년)",
            "detail": "투기과열지구 + 분양가상한제 적용. 가장 강한 전매제한.",
            "severity": "매우 강함",
        }
    if speculative:
        return {
            "period": "소유권이전등기 시까지",
            "detail": "투기과열지구 내 전매 금지. 소유권이전등기 완료 전까지 전매 불가.",
            "severity": "강함",
        }
    if adjusted and price_cap:
        return {
            "period": "5년",
            "detail": "조정대상지역 + 분양가상한제 적용.",
            "severity": "강함",
        }
    if adjusted:
        return {
            "period": "3년",
            "detail": "조정대상지역 내 3년간 전매 제한.",
            "severity": "보통",
        }
    if price_cap:
        return {
            "period": "3~5년",
            "detail": "분양가상한제 적용 단지. 시세 차이에 따라 3~5년.",
            "severity": "보통",
        }
    if public:
        return {
            "period": "3년",
            "detail": "공공주택지구 내 전매 제한.",
            "severity": "보통",
        }
    return {
        "period": "6개월~1년",
        "detail": "비규제지역. 최소 전매제한 적용.",
        "severity": "약함",
    }


def _get_rewin_restriction(speculative: bool, adjusted: bool) -> dict:
    if speculative:
        return {
            "period": "10년",
            "detail": "투기과열지구 당첨 시 10년간 재당첨 제한.",
        }
    if adjusted:
        return {
            "period": "7년",
            "detail": "조정대상지역 당첨 시 7년간 재당첨 제한.",
        }
    return {
        "period": "없음",
        "detail": "비규제지역은 재당첨 제한 없음.",
    }


def _get_residency_obligation(price_cap: bool, speculative: bool) -> dict:
    if price_cap and speculative:
        return {
            "period": "5년",
            "detail": "투기과열지구 내 분양가상한제 단지. 5년 실거주 의무.",
            "required": True,
        }
    if price_cap:
        return {
            "period": "2~3년",
            "detail": "분양가상한제 적용 단지. 시세 차이에 따라 2~3년 실거주 의무.",
            "required": True,
        }
    return {
        "period": "없음",
        "detail": "거주의무 없음.",
        "required": False,
    }
