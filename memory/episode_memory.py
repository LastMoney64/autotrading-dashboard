"""
EpisodeMemory — 거래 에피소드 저장/검색

모든 의사결정 사이클을 저장하고,
현재 상황과 유사한 과거 에피소드를 검색한다.
"""

from db.database import Database


class EpisodeMemory:
    """거래 에피소드 메모리"""

    def __init__(self, db: Database):
        self.db = db

    def save(self, record_dict: dict) -> int:
        """DebateRecord.to_dict() 저장"""
        return self.db.save_episode(record_dict)

    def update_result(self, cycle_id: str, exit_price: float, pnl_pct: float, pnl_usd: float):
        """거래 결과 업데이트"""
        self.db.update_trade_result(cycle_id, exit_price, pnl_pct, pnl_usd)

    def get_recent(self, symbol: str = "", limit: int = 50) -> list[dict]:
        return self.db.get_recent_episodes(symbol, limit)

    def find_similar(
        self,
        judge_signal: str,
        avg_confidence: float,
        confidence_margin: float = 0.15,
        limit: int = 5,
    ) -> list[dict]:
        """현재 상황과 유사한 과거 에피소드 검색"""
        low = max(0.0, avg_confidence - confidence_margin)
        high = min(1.0, avg_confidence + confidence_margin)
        return self.db.get_similar_episodes(judge_signal, (low, high), limit)

    def get_stats(self) -> dict:
        return self.db.get_overall_stats()
