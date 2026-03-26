"""
DebateRecord — 토론 전 과정 기록

하나의 의사결정 사이클에서 발생하는 토론 전체를 기록.
분석 → 토론 → 판결 → 리스크 검토 → 실행까지 전 과정 보관.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from core.base_agent import AnalysisResult, Signal


@dataclass
class DebateRound:
    """토론 한 라운드"""
    round_number: int
    opinions: dict[str, str]   # agent_id → 발언 내용
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class JudgmentResult:
    """Judge의 최종 판결"""
    signal: Signal
    confidence: float
    position_size_pct: float = 0.0        # 계좌 대비 포지션 크기 (%)
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reasoning: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "signal": self.signal.value,
            "confidence": self.confidence,
            "position_size_pct": self.position_size_pct,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "reasoning": self.reasoning,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class RiskReviewResult:
    """Risk Agent의 검토 결과"""
    approved: bool
    veto_reason: Optional[str] = None
    adjustments: Optional[dict] = None   # 포지션 크기 조정 등
    risk_score: float = 0.0              # 0.0 (안전) ~ 1.0 (위험)
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "approved": self.approved,
            "veto_reason": self.veto_reason,
            "adjustments": self.adjustments,
            "risk_score": self.risk_score,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class DebateRecord:
    """하나의 의사결정 사이클 전체 기록"""
    cycle_id: str
    symbol: str
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None

    # 1단계: 개별 분석
    analyses: list[AnalysisResult] = field(default_factory=list)

    # 2단계: 토론 라운드
    debate_rounds: list[DebateRound] = field(default_factory=list)

    # 3단계: Moderator 요약
    moderator_summary: str = ""

    # 4단계: Memory 유사 상황
    similar_episodes: list[dict] = field(default_factory=list)

    # 5단계: Judge 판결
    judgment: Optional[JudgmentResult] = None

    # 6단계: Risk 검토
    risk_review: Optional[RiskReviewResult] = None

    # 최종 결과
    final_action: str = ""   # "EXECUTED" / "VETOED" / "SKIPPED"
    execution_result: Optional[dict] = None

    def add_analysis(self, result: AnalysisResult):
        self.analyses.append(result)

    def add_debate_round(self, round_num: int, opinions: dict[str, str]):
        self.debate_rounds.append(DebateRound(
            round_number=round_num,
            opinions=opinions,
        ))

    def set_moderator_summary(self, summary: str):
        self.moderator_summary = summary

    def set_judgment(self, judgment: JudgmentResult):
        self.judgment = judgment

    def set_risk_review(self, review: RiskReviewResult):
        self.risk_review = review
        if not review.approved:
            self.final_action = "VETOED"

    def finalize(self, action: str, execution_result: Optional[dict] = None):
        self.final_action = action
        self.execution_result = execution_result
        self.finished_at = datetime.utcnow()

    @property
    def signal_consensus(self) -> dict:
        """분석 에이전트들의 신호 분포"""
        counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
        for a in self.analyses:
            counts[a.signal.value] += 1
        return counts

    @property
    def avg_confidence(self) -> float:
        """분석 에이전트 평균 확신도"""
        if not self.analyses:
            return 0.0
        return sum(a.confidence for a in self.analyses) / len(self.analyses)

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.finished_at and self.started_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    def to_dict(self) -> dict:
        return {
            "cycle_id": self.cycle_id,
            "symbol": self.symbol,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": self.duration_seconds,
            "analyses": [a.to_dict() for a in self.analyses],
            "signal_consensus": self.signal_consensus,
            "avg_confidence": self.avg_confidence,
            "debate_rounds": [
                {"round": r.round_number, "opinions": r.opinions}
                for r in self.debate_rounds
            ],
            "moderator_summary": self.moderator_summary,
            "similar_episodes": self.similar_episodes,
            "judgment": self.judgment.to_dict() if self.judgment else None,
            "risk_review": self.risk_review.to_dict() if self.risk_review else None,
            "final_action": self.final_action,
            "execution_result": self.execution_result,
        }

    def to_summary(self) -> str:
        """텔레그램 알림용 간단 요약"""
        consensus = self.signal_consensus
        parts = [
            f"[{self.symbol}] 의사결정 사이클 #{self.cycle_id}",
            f"분석 컨센서스: BUY {consensus['BUY']} / SELL {consensus['SELL']} / HOLD {consensus['HOLD']}",
            f"평균 확신도: {self.avg_confidence:.1%}",
        ]
        if self.judgment:
            parts.append(f"Judge 판결: {self.judgment.signal.value} ({self.judgment.confidence:.1%})")
            parts.append(f"포지션 크기: {self.judgment.position_size_pct:.1f}%")
        if self.risk_review:
            if self.risk_review.approved:
                parts.append("Risk: APPROVED")
            else:
                parts.append(f"Risk: VETOED — {self.risk_review.veto_reason}")
        parts.append(f"최종: {self.final_action}")
        return "\n".join(parts)
