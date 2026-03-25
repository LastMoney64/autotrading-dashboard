"""
WeightAdjuster — 에이전트 가중치 자동 조정

성과 기반으로 에이전트의 투표 가중치를 조정한다.
- 승률 높은 에이전트 → 가중치 증가
- 승률 낮은 에이전트 → 가중치 감소
- 최소/최대 범위 보장 (0.2 ~ 3.0)
"""

import logging
from datetime import datetime

from core.agent_registry import AgentRegistry
from core.base_agent import AgentStatus
from db.database import Database
from memory.performance_memory import PerformanceMemory
from evolution.performance_tracker import PerformanceTracker, AgentReport

logger = logging.getLogger(__name__)


class WeightAdjuster:
    """에이전트 가중치 자동 조정기"""

    MIN_WEIGHT = 0.2
    MAX_WEIGHT = 3.0
    DEFAULT_WEIGHT = 1.0

    def __init__(
        self,
        registry: AgentRegistry,
        db: Database,
        perf_memory: PerformanceMemory,
        tracker: PerformanceTracker,
    ):
        self.registry = registry
        self.db = db
        self.perf_memory = perf_memory
        self.tracker = tracker

    def adjust_all(self, last_n: int = 100) -> list[dict]:
        """전체 분석 에이전트 가중치 조정 — 변경 내역 반환"""
        reports = self.tracker.analyze_all(last_n)
        changes = []

        for report in reports:
            agent = self.registry.get(report.agent_id)
            if not agent or agent.status != AgentStatus.ACTIVE:
                continue

            if report.total_trades < 10:
                continue  # 데이터 부족

            old_weight = agent.weight
            new_weight = self._calculate_weight(report)

            if abs(new_weight - old_weight) < 0.05:
                continue  # 변동 무시

            # 가중치 적용
            self.registry.update_weight(report.agent_id, new_weight)

            # DB 기록
            reason = self._make_reason(report, old_weight, new_weight)
            self.db.save_weight_change(report.agent_id, old_weight, new_weight, reason)

            change = {
                "agent_id": report.agent_id,
                "name": report.name,
                "old_weight": round(old_weight, 2),
                "new_weight": round(new_weight, 2),
                "delta": round(new_weight - old_weight, 2),
                "grade": report.grade,
                "win_rate": round(report.win_rate, 3),
                "reason": reason,
            }
            changes.append(change)
            logger.info(f"Weight adjusted: {report.agent_id} {old_weight:.2f} → {new_weight:.2f} ({reason})")

        return changes

    def _calculate_weight(self, report: AgentReport) -> float:
        """성과 기반 새 가중치 계산"""
        # 기본 가중치: 승률 기반 (50% = 1.0, 선형 스케일)
        base = report.win_rate * 2.0  # 50% → 1.0, 70% → 1.4

        # 최근 성과 보정 (모멘텀)
        if report.recent_trades >= 5:
            momentum = report.recent_win_rate - report.win_rate
            base += momentum * 0.5  # 최근에 더 잘하면 보너스

        # 확신도 보정 (확신도 높으면서 잘 맞추면 보너스)
        if report.avg_confidence > 0.7 and report.win_rate > 0.55:
            base *= 1.1

        # 연패 페널티
        if report.losing_streak >= 3:
            base *= 0.85
        if report.losing_streak >= 5:
            base *= 0.75

        # 범위 제한
        return max(self.MIN_WEIGHT, min(base, self.MAX_WEIGHT))

    def _make_reason(self, report: AgentReport, old_w: float, new_w: float) -> str:
        """변경 사유 생성"""
        direction = "증가" if new_w > old_w else "감소"
        parts = [f"등급 {report.grade}, 승률 {report.win_rate:.0%}"]

        if report.recent_trades >= 5:
            parts.append(f"최근 {report.recent_win_rate:.0%}")

        if report.losing_streak >= 3:
            parts.append(f"연패 {report.losing_streak}회")

        return f"자동 {direction}: {', '.join(parts)}"
