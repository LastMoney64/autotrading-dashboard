"""
Settings — 전역 설정

환경변수와 기본값을 관리한다.
.env 파일 또는 환경변수에서 읽어옴.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    """전역 설정"""

    # ── 프로젝트 경로 ───────────────────────────────────
    base_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent)
    db_path: str = ""

    # ── Claude API ──────────────────────────────────────
    anthropic_api_key: str = ""

    # ── 모델 설정 ───────────────────────────────────────
    analyst_model: str = "claude-haiku-4-5-20251001"     # 분석 에이전트
    special_model: str = "claude-sonnet-4-6-20250819"    # 특수 에이전트

    # ── 트레이딩 설정 ───────────────────────────────────
    trading_pair: str = "BTC/USDT"
    timeframes: list[str] = field(default_factory=lambda: ["15m", "1h", "4h"])
    decision_interval_seconds: int = 60  # 의사결정 주기

    # ── 리스크 관리 ─────────────────────────────────────
    max_position_risk_pct: float = 2.0       # 거래당 최대 리스크 %
    max_drawdown_pct: float = 15.0           # 최대 드로다운 %
    max_concurrent_positions: int = 3        # 최대 동시 포지션
    min_risk_reward_ratio: float = 2.0       # 최소 손익비

    # ── 에이전트 설정 ───────────────────────────────────
    min_confidence_threshold: float = 0.6    # 최소 확신도
    debate_rounds: int = 2                   # 토론 라운드 수
    max_agents: int = 20                     # 최대 에이전트 수

    # ── 진화 설정 ───────────────────────────────────────
    weight_update_interval: int = 50         # N거래마다 가중치 조정
    isolation_win_rate: float = 0.40         # 이 이하면 격리
    isolation_weeks: int = 3                 # N주 연속이면 격리
    probation_win_rate: float = 0.55         # 수습 복귀 기준 승률
    probation_min_trades: int = 30           # 수습 기간 최소 시뮬 거래

    # ── 거래소 (Phase 10에서 채울 것) ───────────────────
    exchange_name: str = ""
    exchange_api_key: str = ""
    exchange_api_secret: str = ""
    exchange_testnet: bool = True

    # ── 텔레그램 ────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    def __post_init__(self):
        if not self.db_path:
            self.db_path = str(self.base_dir / "data" / "trading.db")

    @classmethod
    def from_env(cls) -> "Settings":
        """환경변수에서 설정 로드"""
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            trading_pair=os.getenv("TRADING_PAIR", "BTC/USDT"),
            exchange_name=os.getenv("EXCHANGE_NAME", ""),
            exchange_api_key=os.getenv("EXCHANGE_API_KEY", ""),
            exchange_api_secret=os.getenv("EXCHANGE_API_SECRET", ""),
            exchange_testnet=os.getenv("EXCHANGE_TESTNET", "true").lower() == "true",
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        )
