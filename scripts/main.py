"""전체 파이프라인 실행: 수집 → 분석 → JSON 저장"""
import json
import logging
import os
import sys
from datetime import datetime

# scripts 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetch_subscriptions import fetch_all_subscriptions
from analyze import analyze_subscriptions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=" * 60)
    logger.info("부동산 청약 대시보드 데이터 업데이트 시작")
    logger.info("=" * 60)

    # 1. 청약 데이터 수집
    logger.info("[1/3] 청약 분양정보 수집 중...")
    subscriptions = fetch_all_subscriptions()

    if not subscriptions:
        logger.warning("수집된 청약 데이터가 없습니다.")
        _save_empty_result()
        return

    # 2. 분석 (시세 비교, 차익 계산, 필터링)
    logger.info("[2/3] 분양가 vs 시세 분석 중...")
    analyzed = analyze_subscriptions(subscriptions)

    # 차익 기준 정렬 (높은 순)
    analyzed.sort(key=lambda x: x["max_profit"], reverse=True)

    # 3. JSON 저장
    logger.info("[3/3] 데이터 저장 중...")
    output = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_collected": len(subscriptions),
        "total_filtered": len(analyzed),
        "items": analyzed,
    }

    output_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data",
        "subscriptions.json",
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    logger.info(f"저장 완료: {output_path}")
    logger.info(f"총 {len(subscriptions)}건 수집 → {len(analyzed)}건 통과 (차익 1억+)")
    logger.info("=" * 60)


def _save_empty_result():
    """빈 결과 저장."""
    output = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_collected": 0,
        "total_filtered": 0,
        "items": [],
    }
    output_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data",
        "subscriptions.json",
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
