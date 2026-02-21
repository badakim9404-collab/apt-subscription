"""청약홈 API로 분양정보 수집"""
import logging
from datetime import datetime, timedelta

import requests

from config import DATA_GO_KR_API_KEY, SUBSCRIPTION_API_BASE, SUBSCRIPTION_AREA_CODES

logger = logging.getLogger(__name__)


def fetch_all_subscriptions() -> list[dict]:
    """서울+수도권 청약 분양정보를 수집하여 반환."""
    all_items = []

    for region_name, area_code in SUBSCRIPTION_AREA_CODES.items():
        logger.info(f"[청약수집] {region_name} (코드: {area_code})")
        details = _fetch_detail_list(area_code)
        logger.info(f"  → {len(details)}건 수집")

        for detail in details:
            house_manage_no = detail.get("HOUSE_MANAGE_NO")
            if not house_manage_no:
                continue
            # 주택형별 상세 조회
            models = _fetch_model_list(house_manage_no)
            detail["models"] = models
            detail["_region"] = region_name
            all_items.append(detail)

    logger.info(f"[청약수집] 총 {len(all_items)}건 수집 완료")
    return all_items


def _fetch_detail_list(area_code: str) -> list[dict]:
    """분양 상세 목록 조회 (getAPTLttotPblancDetail)."""
    url = f"{SUBSCRIPTION_API_BASE}/getAPTLttotPblancDetail"

    # 최근 3개월 ~ 향후 2개월
    now = datetime.now()
    start_date = (now - timedelta(days=90)).strftime("%Y-%m")
    end_date = (now + timedelta(days=60)).strftime("%Y-%m")

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

        # 날짜 필터: 모집공고일 기준
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


def _fetch_model_list(house_manage_no: str) -> list[dict]:
    """주택형별 상세 조회 (getAPTLttotPblancMdl)."""
    url = f"{SUBSCRIPTION_API_BASE}/getAPTLttotPblancMdl"
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
