"""
BaseAgent — 모든 에이전트의 부모 클래스

모든 분석/특수 에이전트는 이 클래스를 상속받아 구현한다.
에이전트의 생명주기, Claude API 호출, 상태 관리를 담당.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional
import asyncio
import anthropic


# ── 열거형 ──────────────────────────────────────────────

class Signal(str, Enum):
    """매매 신호"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class AgentRole(str, Enum):
    """에이전트 역할 분류"""
    ANALYST = "analyst"       # 분석 에이전트
    MODERATOR = "moderator"   # 토론 사회자
    JUDGE = "judge"           # 최종 판결
    RISK = "risk"             # 리스크 관리
    MEMORY = "memory"         # 메모리 검색
    EVOLUTION = "evolution"   # 전략 진화
    RECRUITER = "recruiter"   # 에이전트 영입


class AgentStatus(str, Enum):
    """에이전트 상태"""
    ACTIVE = "active"           # 실전 참여
    ISOLATED = "isolated"       # 격리 (시뮬만)
    PROBATION = "probation"     # 수습 기간 (신규 에이전트)
    DISABLED = "disabled"       # 비활성


# ── 데이터 클래스 ───────────────────────────────────────

@dataclass
class AnalysisResult:
    """분석 에이전트의 출력 형식"""
    agent_id: str
    signal: Signal
    confidence: float              # 0.0 ~ 1.0
    reasoning: str                 # 분석 근거
    key_indicators: dict           # 주요 지표 값
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "signal": self.signal.value,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "key_indicators": self.key_indicators,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class AgentConfig:
    """에이전트 개별 설정"""
    agent_id: str
    name: str
    role: AgentRole
    model: str = "claude-haiku-4-5-20251001"
    weight: float = 1.0            # 토론 시 발언 가중치
    max_tokens: int = 1024
    temperature: float = 0.3
    system_prompt: str = ""
    parameters: dict = field(default_factory=dict)


# ── 베이스 에이전트 ─────────────────────────────────────

class BaseAgent(ABC):
    """모든 에이전트의 부모 클래스"""

    def __init__(self, config: AgentConfig):
        self.config = config
        self.agent_id = config.agent_id
        self.name = config.name
        self.role = config.role
        self.status = AgentStatus.ACTIVE
        self.weight = config.weight

        # 성과 추적
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0

        # Claude API 클라이언트
        self._client: Optional[anthropic.AsyncAnthropic] = None

    # ── 프로퍼티 ────────────────────────────────────────

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.wins / self.total_trades

    @property
    def is_active(self) -> bool:
        return self.status == AgentStatus.ACTIVE

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic()
        return self._client

    # ── 추상 메서드 (하위 클래스에서 반드시 구현) ────────

    @abstractmethod
    async def analyze(self, market_data: dict) -> AnalysisResult:
        """시장 데이터를 받아 분석 결과를 반환"""
        ...

    @abstractmethod
    async def respond_to_debate(
        self,
        own_analysis: AnalysisResult,
        other_analyses: list[AnalysisResult],
        debate_context: str,
    ) -> str:
        """다른 에이전트의 분석을 보고 토론 의견 반환"""
        ...

    @abstractmethod
    def get_system_prompt(self) -> str:
        """에이전트의 시스템 프롬프트 반환"""
        ...

    # ── Claude API 호출 ─────────────────────────────────

    async def call_llm(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Claude API 호출 공통 메서드"""
        response = await self.client.messages.create(
            model=self.config.model,
            max_tokens=max_tokens or self.config.max_tokens,
            temperature=temperature or self.config.temperature,
            system=system_prompt or self.get_system_prompt(),
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text

    # ── 성과 기록 ───────────────────────────────────────

    def record_trade_result(self, pnl: float):
        """거래 결과 기록"""
        self.total_trades += 1
        self.total_pnl += pnl
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1

    # ── 상태 관리 ───────────────────────────────────────

    def isolate(self):
        """에이전트를 격리 상태로 전환"""
        self.status = AgentStatus.ISOLATED

    def activate(self):
        """에이전트를 활성 상태로 전환"""
        self.status = AgentStatus.ACTIVE

    def set_probation(self):
        """에이전트를 수습 상태로 전환"""
        self.status = AgentStatus.PROBATION

    # ── 자기 평가 ───────────────────────────────────────

    async def self_evaluate(self) -> dict:
        """에이전트 자기 평가 (Evolution에서 호출)"""
        prompt = f"""당신의 최근 성과를 분석해주세요:
- 총 거래: {self.total_trades}회
- 승률: {self.win_rate:.1%}
- 누적 PnL: {self.total_pnl:+.2f}%
- 현재 상태: {self.status.value}

어떤 점을 개선해야 할지, 어떤 시장 상황에서 강점/약점이 있는지 분석해주세요."""

        evaluation = await self.call_llm(prompt)
        return {
            "agent_id": self.agent_id,
            "win_rate": self.win_rate,
            "total_pnl": self.total_pnl,
            "total_trades": self.total_trades,
            "evaluation": evaluation,
        }

    # ── 직렬화 ──────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role.value,
            "status": self.status.value,
            "weight": self.weight,
            "win_rate": self.win_rate,
            "total_trades": self.total_trades,
            "total_pnl": self.total_pnl,
        }

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} "
            f"id={self.agent_id} "
            f"status={self.status.value} "
            f"win_rate={self.win_rate:.1%}>"
        )
