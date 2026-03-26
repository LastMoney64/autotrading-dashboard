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
    analyst_model: str = "claude-haiku-4-5-20251001"      # 분석 에이전트 ($1/MTok)
    special_model: str = "claude-sonnet-4-5-20250929"    # 특수 에이전트 ($3/MTok)

    # ── 트레이딩 설정 ───────────────────────────────────
    trading_pairs: list[str] = field(default_factory=lambda: ["BTC/USDT:USDT", "ETH/USDT:USDT"])
    timeframes: list[str] = field(default_factory=lambda: ["15m", "1h", "4h"])
    decision_interval_seconds: int = 60  # 의사결정 주기

    # ── 리스크 관리 (고레버리지 전략) ─────────────────────
    max_position_risk_pct: float = 1.0       # 거래당 최대 리스크 % (고레버 → 작게)
    max_drawdown_pct: float = 30.0           # 최대 드로다운 %
    max_concurrent_positions: int = 4        # 최대 동시 포지션 (페어당 2개)
    min_risk_reward_ratio: float = 2.5       # 최소 손익비 (고레버 → 높게)
    stop_loss_pct: float = 1.5              # 손절 % (레버리지 적용 전 가격 기준)
    take_profit_pct: float = 4.0            # 익절 % (손절의 ~2.5배)

    # ── 에이전트 설정 ───────────────────────────────────
    min_confidence_threshold: float = 0.4    # 최소 확신도 (적극적 매매)
    debate_rounds: int = 0                   # 토론 라운드 수 (0=분석만, 비용 절감)
    max_agents: int = 20                     # 최대 에이전트 수

    # ── 진화 설정 ───────────────────────────────────────
    weight_update_interval: int = 50         # N거래마다 가중치 조정
    isolation_win_rate: float = 0.40         # 이 이하면 격리
    isolation_weeks: int = 3                 # N주 연속이면 격리
    probation_win_rate: float = 0.55         # 수습 복귀 기준 승률
    probation_min_trades: int = 30           # 수습 기간 최소 시뮬 거래

    # ── OKX 거래소 ──────────────────────────────────────
    okx_api_key: str = ""
    okx_api_secret: str = ""
    okx_passphrase: str = ""
    exchange_testnet: bool = True
    leverage_min: int = 10                    # 최소 레버리지
    leverage_max: int = 50                    # 최대 레버리지
    initial_capital: float = 100.0           # 초기 자본 ($)

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
            trading_pairs=[p.strip() for p in os.getenv("TRADING_PAIRS", "BTC/USDT:USDT,ETH/USDT:USDT").split(",") if p.strip()],
            okx_api_key=os.getenv("OKX_API_KEY", ""),
            okx_api_secret=os.getenv("OKX_API_SECRET", ""),
            okx_passphrase=os.getenv("OKX_PASSPHRASE", ""),
            exchange_testnet=os.getenv("EXCHANGE_TESTNET", "true").lower() == "true",
            leverage_min=int(os.getenv("LEVERAGE_MIN", "10")),
            leverage_max=int(os.getenv("LEVERAGE_MAX", "50")),
            initial_capital=float(os.getenv("INITIAL_CAPITAL", "100")),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        )
