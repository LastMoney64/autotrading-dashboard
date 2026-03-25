"""
PerformanceTracker — 에이전트 성과 분석기

DB에서 에이전트 성과 데이터를 집계하고,
성과 등급, 추세, 시장 국면별 강약점을 분석한다.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from db.database import Database
from core.agent_registry import AgentRegistry
from core.base_agent import AgentStatus


@dataclass
class AgentReport:
    """에이전트 성과 리포트"""
    agent_id: str
    name: str
    status: str
    weight: float
    # 전체 성과
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_confidence: float = 0.0
    # 최근 성과 (최근 20거래)
    recent_win_rate: float = 0.0
    recent_trades: int = 0
    # 연속 패배 수
    losing_streak: int = 0
    # 등급
    grade: str = "C"  # S, A, B, C, D, F
    # 권고 사항
    recommendation: str = ""  # "maintain", "boost", "warn", "isolate", "reactivate"

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "status": self.status,
            "weight": self.weight,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 3),
            "avg_confidence": round(self.avg_confidence, 3),
            "recent_win_rate": round(self.recent_win_rate, 3),
            "recent_trades": self.recent_trades,
            "losing_streak": self.losing_streak,
            "grade": self.grade,
            "recommendation": self.recommendation,
        }


class PerformanceTracker:
    """에이전트 성과 추적 및 분석"""

    def __init__(self, db: Database, registry: AgentRegistry):
        self.db = db
        self.registry = registry

    def analyze_agent(self, agent_id: str, last_n: int = 100) -> AgentReport:
        """단일 에이전트 종합 분석"""
        agent = self.registry.get(agent_id)
        if not agent:
            return AgentReport(agent_id=agent_id, name="unknown", status="unknown", weight=0)

        # DB에서 성과 통계
        stats = self.db.get_agent_stats(agent_id, last_n)
        recent_stats = self.db.get_agent_stats(agent_id, last_n=20)
        streak = self._get_losing_streak(agent_id)

        report = AgentReport(
            agent_id=agent_id,
            name=agent.name,
            status=agent.status.value,
            weight=agent.weight,
            total_trades=stats.get("total", 0),
            wins=stats.get("wins", 0),
            losses=stats.get("losses", 0),
            win_rate=stats.get("win_rate", 0),
            avg_confidence=stats.get("avg_confidence", 0),
            recent_win_rate=recent_stats.get("win_rate", 0),
            recent_trades=recent_stats.get("total", 0),
            losing_streak=streak,
        )

        # 등급 산정
        report.grade = self._calculate_grade(report)
        # 권고 사항
        report.recommendation = self._get_recommendation(report, agent.status)

        return report

    def analyze_all(self, last_n: int = 100) -> list[AgentReport]:
        """전체 분석 에이전트 성과 분석"""
        analysts = self.registry.get_all_analysts()
        return [self.analyze_agent(a.agent_id, last_n) for a in analysts]

    def get_summary(self, last_n: int = 100) -> dict:
        """전체 시스템 성과 요약"""
        reports = self.analyze_all(last_n)
        overall = self.db.get_overall_stats()

        grade_counts = {}
        for r in reports:
            grade_counts[r.grade] = grade_counts.get(r.grade, 0) + 1

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "overall": overall,
            "agent_reports": [r.to_dict() for r in reports],
            "grade_distribution": grade_counts,
            "agents_to_isolate": [r.agent_id for r in reports if r.recommendation == "isolate"],
            "agents_to_reactivate": [r.agent_id for r in reports if r.recommendation == "reactivate"],
            "agents_to_boost": [r.agent_id for r in reports if r.recommendation == "boost"],
        }

    # ── 내부 메서드 ────────────────────────────────────────

    def _get_losing_streak(self, agent_id: str) -> int:
        """현재 연속 패배 수 계산"""
        rows = self.db.conn.execute("""
            SELECT was_correct FROM agent_performance
            WHERE agent_id = ? AND was_correct IS NOT NULL
            ORDER BY id DESC LIMIT 20
        """, (agent_id,)).fetchall()

        streak = 0
        for row in rows:
            if row["was_correct"] == 0:
                streak += 1
            else:
                break
        return streak

    def _calculate_grade(self, report: AgentReport) -> str:
        """성과 등급 산정"""
        if report.total_trades < 10:
            return "N"  # 데이터 부족

        wr = report.win_rate
        recent_wr = report.recent_win_rate

        # 최근 성과에 가중치 (전체 60% + 최근 40%)
        blended = wr * 0.6 + recent_wr * 0.4 if report.recent_trades >= 5 else wr

        if blended >= 0.75:
            return "S"
        elif blended >= 0.65:
            return "A"
        elif blended >= 0.55:
            return "B"
        elif blended >= 0.45:
            return "C"
        elif blended >= 0.35:
            return "D"
        else:
            return "F"

    def _get_recommendation(self, report: AgentReport, status: AgentStatus) -> str:
        """상태별 권고 사항"""
        # 격리 상태인 에이전트: 복귀 가능 여부 확인
        if status == AgentStatus.ISOLATED:
            if report.recent_win_rate >= 0.55 and report.recent_trades >= 10:
                return "reactivate"
            return "maintain"

        # 수습 상태: 졸업 여부
        if status == AgentStatus.PROBATION:
            if report.total_trades >= 30 and report.win_rate >= 0.55:
                return "reactivate"
            return "maintain"

        # 활성 상태: 성과 판단
        if report.grade in ("S", "A"):
            return "boost"
        elif report.grade in ("D", "F"):
            if report.losing_streak >= 5 or report.total_trades >= 30:
                return "isolate"
            return "warn"
        else:
            return "maintain"
