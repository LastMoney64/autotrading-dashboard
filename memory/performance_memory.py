"""
PerformanceMemory — 에이전트별 성과 기록

각 에이전트의 매 사이클 예측과 실제 결과를 기록하여
승률, 정확도, 수익 기여도를 추적한다.
"""

from db.database import Database


class PerformanceMemory:
    """에이전트 성과 메모리"""

    def __init__(self, db: Database):
        self.db = db

    def record(
        self, agent_id: str, cycle_id: str,
        signal: str, confidence: float, reasoning: str = ""
    ):
        """에이전트 예측 기록"""
        self.db.save_agent_performance(agent_id, cycle_id, signal, confidence, reasoning)

    def mark_results(self, cycle_id: str, correct_signal: str):
        """거래 결과 확정 후 정답 여부 일괄 업데이트"""
        self.db.update_agent_correctness(cycle_id, correct_signal)

    def get_stats(self, agent_id: str, last_n: int = 100) -> dict:
        """에이전트 통계"""
        return self.db.get_agent_stats(agent_id, last_n)

    def get_all_stats(self, agent_ids: list[str], last_n: int = 100) -> dict[str, dict]:
        """전체 에이전트 통계"""
        return {aid: self.get_stats(aid, last_n) for aid in agent_ids}

    def save_weight_change(self, agent_id: str, old_w: float, new_w: float, reason: str = ""):
        self.db.save_weight_change(agent_id, old_w, new_w, reason)
