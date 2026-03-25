"""
PatternMemory — 장기 패턴 저장/검색

시스템이 발견한 반복 패턴을 저장한다.
예: "월요일 아시아 시간대 변동성 낮음"
    "OI 급증 후 24H 내 청산 확률 67%"
"""

from db.database import Database


class PatternMemory:
    """장기 패턴 메모리"""

    def __init__(self, db: Database):
        self.db = db

    def save(
        self, pattern_type: str, description: str,
        conditions: dict, success_rate: float, sample_count: int
    ):
        """새 패턴 저장"""
        self.db.save_pattern(pattern_type, description, conditions, success_rate, sample_count)

    def get_patterns(self, pattern_type: str = "", min_samples: int = 5) -> list[dict]:
        """패턴 조회"""
        return self.db.get_patterns(pattern_type, min_samples)

    def get_relevant_patterns(self, market_conditions: dict) -> list[dict]:
        """현재 시장 조건에 해당하는 패턴 조회

        TODO: 조건 매칭 로직 고도화 (Evolution Agent가 주간 분석 시 패턴 추가)
        """
        all_patterns = self.get_patterns(min_samples=3)
        # 현재는 전체 반환, 추후 조건 필터링 추가
        return all_patterns
