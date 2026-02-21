"""청약홈 API로 분양정보 수집"""
import logging
from datetime import datetime, timedelta

import requests

from config import DATA_GO_KR_API_KEY, SUBSCRIPTION_API_BASE, SUBSCRIPTION_AREA_CODES

logger = logging.getLogger(__name__)


def fetch_all_subscriptions() -> list[dict]:
    """서울+수도권 청약 분양정보를 수집하여 반환 (APT + 오피스텔/도시형 + 무순위).

    - APT: 최근 3개월 전체 수집
    - 오피스텔/도시형, 무순위/잔여: 접수예정/접수중만 수집 (마감 건은 제외하여 속도 최적화)
    """
    all_items = []
    seen_ids = set()
    now = datetime.now()

    # 수집할 청약 유형별 엔드포인트
    endpoints = [
        ("APT", "getAPTLttotPblancDetail", "getAPTLttotPblancMdl", False),
        ("오피스텔/도시형", "getUrbtyOfctlLttotPblancDetail", "getUrbtyOfctlLttotPblancMdl", True),
        ("무순위/잔여", "getRemndrLttotPblancDetail", "getRemndrLttotPblancMdl", True),
    ]

    for type_name, detail_ep, model_ep, upcoming_only in endpoints:
        for region_name, area_code in SUBSCRIPTION_AREA_CODES.items():
            logger.info(f"[청약수집] {type_name} - {region_name} (코드: {area_code})")
            details = _fetch_detail_list(area_code, detail_ep)

            kept = []
            for detail in details:
                if upcoming_only and _is_closed(detail, now):
                    continue
                kept.append(detail)

            logger.info(f"  → {len(details)}건 수집, {len(kept)}건 유효")

            for detail in kept:
                house_manage_no = detail.get("HOUSE_MANAGE_NO")
                if not house_manage_no or house_manage_no in seen_ids:
                    continue
                seen_ids.add(house_manage_no)
                # 주택형별 상세 조회
                models = _fetch_model_list(house_manage_no, model_ep)
                detail["models"] = models
                detail["_region"] = region_name
                detail["_type"] = type_name
                # upcoming_only 필터를 통과한 건 → 접수예정/접수중으로 태그
                if upcoming_only:
                    detail["_is_upcoming"] = True
                all_items.append(detail)

    logger.info(f"[청약수집] 총 {len(all_items)}건 수집 완료")
    return all_items


def _is_closed(item: dict, now: datetime) -> bool:
    """이미 마감된 건인지 판단. 당첨발표일 전이면 아직 진행 중."""
    winner_date = item.get("PRZWNER_PRESNATN_DE", "")

    try:
        # 당첨발표일이 아직 안 지났으면 진행 중
        if winner_date:
            winner_dt = datetime.strptime(winner_date, "%Y-%m-%d")
            if now <= winner_dt:
                return False
            return True
    except ValueError:
        pass

    # 당첨발표일 없으면 접수종료일 기준
    receipt_end = item.get("RCEPT_ENDDE", "") or item.get("SUBSCRPT_RCEPT_ENDDE", "") or ""
    try:
        if receipt_end:
            end_dt = datetime.strptime(receipt_end, "%Y-%m-%d")
            if now > end_dt:
                return True
    except ValueError:
        pass

    return False


def _fetch_detail_list(area_code: str, endpoint: str = "getAPTLttotPblancDetail") -> list[dict]:
    """분양 상세 목록 조회."""
    url = f"{SUBSCRIPTION_API_BASE}/{endpoint}"

    # 최근 3개월 ~ 향후 2개월
    now = datetime.now()

    items = []
    page = 1
    per_page = 100

    while True:
        params = {
            "page": page,
            "perPage": per_page,
            "serviceKey": DATA_GO_KR_API_KEY,
            "cond[SUBSCRPT_AREA_CODE_NM::EQ]": _area_code_to_name(area_code),
        }
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"  API 호출 실패: {e}")
            break

        batch = data.get("data", [])
        if not batch:
            break

        # 날짜 필터: 접수시작일 기준 최근 3개월
        for item in batch:
            rcept_bgnde = item.get("RCEPT_BGNDE", "")
            if rcept_bgnde:
                try:
                    rcept_dt = datetime.strptime(rcept_bgnde, "%Y-%m-%d")
                    if rcept_dt >= now - timedelta(days=90):
                        items.append(item)
                except ValueError:
                    items.append(item)
            else:
                items.append(item)

        total_count = data.get("totalCount", 0)
        if page * per_page >= total_count:
            break
        page += 1

    return items


def _fetch_model_list(house_manage_no: str, endpoint: str = "getAPTLttotPblancMdl") -> list[dict]:
    """주택형별 상세 조회."""
    url = f"{SUBSCRIPTION_API_BASE}/{endpoint}"
    params = {
        "page": 1,
        "perPage": 100,
        "serviceKey": DATA_GO_KR_API_KEY,
        "cond[HOUSE_MANAGE_NO::EQ]": house_manage_no,
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])
    except Exception as e:
        logger.error(f"  주택형 조회 실패 (관리번호: {house_manage_no}): {e}")
        return []


def _area_code_to_name(code: str) -> str:
    """지역코드 → 지역명."""
    mapping = {"100": "서울", "200": "인천", "400": "경기"}
    return mapping.get(code, "")
