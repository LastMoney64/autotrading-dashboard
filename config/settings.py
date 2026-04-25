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

    # ── 리스크 관리 (공격적 + 스마트) ─────────────────────
    max_position_risk_pct: float = 1.0       # 거래당 최대 리스크 %
    max_drawdown_pct: float = 25.0           # 일일 최대 드로다운 %
    max_concurrent_positions: int = 3        # 최대 동시 포지션
    min_risk_reward_ratio: float = 2.0       # 최소 손익비
    stop_loss_pct: float = 1.5              # 손절 % (ATR 폴백용)
    take_profit_pct: float = 4.0            # 익절 % (ATR 폴백용)

    # ── 수수료 ────────────────────────────────────────
    fee_maker_pct: float = 0.02             # Maker 수수료 0.02%
    fee_taker_pct: float = 0.05             # Taker 수수료 0.05% (시장가)

    # ── 에이전트 설정 ───────────────────────────────────
    min_confidence_threshold: float = 0.45   # 최소 확신도 (공격적이되 너무 약한 신호는 제외)
    debate_rounds: int = 0                   # 토론 라운드 수 (0=분석만, 비용 절감)
    max_agents: int = 20                     # 최대 에이전트 수

    # ── 진화 설정 ───────────────────────────────────────
    weight_update_interval: int = 50         # N거래마다 가중치 조정
    isolation_win_rate: float = 0.40         # 이 이하면 격리
    isolation_weeks: int = 3                 # N주 연속이면 격리
    probation_win_rate: float = 0.55         # 수습 복귀 기준 승률
    probation_min_trades: int = 30           # 수습 기간 최소 시뮬 거래

    # ── OKX 거래소 ──────────────────────────────────────
    okx_trading_enabled: bool = False     # OKX 자동매매 활성화 (false = 비활성)
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

    # ── 모닝 브리프 API ────────────────────────────────
    etherscan_api_key: str = ""
    morning_brief_hour_kst: int = 7   # 아침 7시 KST
    morning_brief_enabled: bool = True

    # ── Polymarket 날씨봇 ──────────────────────────────
    polymarket_enabled: bool = True
    polygon_private_key: str = ""
    polygon_wallet_address: str = ""
    vc_api_key: str = ""                          # Visual Crossing 날씨 API
    polymarket_mode: str = "paper"                # "paper" or "live"
    polymarket_min_ev: float = 0.10               # 최소 10% EV
    polymarket_max_bet: float = 2.0               # 거래당 최대 $2
    polymarket_kelly_fraction: float = 0.25       # Kelly의 1/4
    polymarket_scan_interval: int = 3600          # 60분마다 스캔
    polymarket_funder: str = ""                   # Polymarket Proxy 지갑 주소
    polymarket_signature_type: int = 0            # 0=EOA, 1=Magic, 2=Browser/MetaMask

    # ── 솔라나 밈코인 봇 (3개) ─────────────────────────
    solana_enabled: bool = True
    solana_mode: str = "paper"                    # paper or live

    # 봇 1: 스마트머니 카피
    solana_bot1_enabled: bool = True
    solana_bot1_private_key: str = ""
    solana_bot1_wallet: str = ""

    # 봇 2: Pump.fun 졸업 스나이퍼
    solana_bot2_enabled: bool = True
    solana_bot2_private_key: str = ""
    solana_bot2_wallet: str = ""

    # 봇 3: 모멘텀 + 소셜
    solana_bot3_enabled: bool = True
    solana_bot3_private_key: str = ""
    solana_bot3_wallet: str = ""

    # 공통 설정
    helius_api_key: str = ""
    birdeye_api_key: str = ""                     # 선택 (무료 티어)
    solana_max_buy_sol: float = 0.05              # 매수당 최대 0.05 SOL
    solana_default_slippage_bps: int = 300        # 3% 슬리피지
    solana_priority_fee_lamports: int = 100000    # 0.0001 SOL 우선 수수료

    # ── 주간 리포트 ────────────────────────────────────
    weekly_report_enabled: bool = True
    weekly_report_hour_kst: int = 21              # 일요일 21시 KST
    auto_wallet_discovery: bool = True            # 자동 지갑 발굴 활성

    def __post_init__(self):
        if not self.db_path:
            self.db_path = str(self.base_dir / "data" / "trading.db")

    @classmethod
    def from_env(cls) -> "Settings":
        """환경변수에서 설정 로드"""
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            trading_pairs=[p.strip() for p in os.getenv("TRADING_PAIRS", "BTC/USDT:USDT,ETH/USDT:USDT").split(",") if p.strip()],
            okx_trading_enabled=os.getenv("OKX_TRADING_ENABLED", "false").strip().lower() == "true",
            okx_api_key=os.getenv("OKX_API_KEY", "").strip(),
            okx_api_secret=os.getenv("OKX_API_SECRET", "").strip(),
            okx_passphrase=os.getenv("OKX_PASSPHRASE", "").strip(),
            exchange_testnet=os.getenv("EXCHANGE_TESTNET", "true").strip().lower() == "true",
            leverage_min=int(os.getenv("LEVERAGE_MIN", "10").strip()),
            leverage_max=int(os.getenv("LEVERAGE_MAX", "50").strip()),
            initial_capital=float(os.getenv("INITIAL_CAPITAL", "100").strip()),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            etherscan_api_key=os.getenv("ETHERSCAN_API_KEY", "").strip(),
            morning_brief_hour_kst=int(os.getenv("MORNING_BRIEF_HOUR_KST", "7").strip()),
            morning_brief_enabled=os.getenv("MORNING_BRIEF_ENABLED", "true").strip().lower() == "true",
            polymarket_enabled=os.getenv("POLYMARKET_ENABLED", "true").strip().lower() == "true",
            polygon_private_key=os.getenv("POLYGON_PRIVATE_KEY", "").strip(),
            polygon_wallet_address=os.getenv("POLYGON_WALLET_ADDRESS", "").strip(),
            vc_api_key=os.getenv("VC_API_KEY", "").strip(),
            polymarket_mode=os.getenv("POLYMARKET_MODE", "paper").strip(),
            polymarket_min_ev=float(os.getenv("POLYMARKET_MIN_EV", "0.10").strip()),
            polymarket_max_bet=float(os.getenv("POLYMARKET_MAX_BET", "2.0").strip()),
            polymarket_kelly_fraction=float(os.getenv("POLYMARKET_KELLY", "0.25").strip()),
            polymarket_scan_interval=int(os.getenv("POLYMARKET_SCAN_INTERVAL", "3600").strip()),
            polymarket_funder=os.getenv("POLYGON_FUNDER", "").strip(),
            polymarket_signature_type=int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "0").strip()),
            # 솔라나
            solana_enabled=os.getenv("SOLANA_ENABLED", "true").strip().lower() == "true",
            solana_mode=os.getenv("SOLANA_MODE", "paper").strip(),
            solana_bot1_enabled=os.getenv("SOLANA_BOT1_ENABLED", "true").strip().lower() == "true",
            solana_bot1_private_key=os.getenv("SOLANA_BOT1_PRIVATE_KEY", "").strip(),
            solana_bot1_wallet=os.getenv("SOLANA_BOT1_WALLET", "G4MEDsDfzFadeeYxn6UxcYJ2C4fMJPmx1i7UyfQHQmsi").strip(),
            solana_bot2_enabled=os.getenv("SOLANA_BOT2_ENABLED", "true").strip().lower() == "true",
            solana_bot2_private_key=os.getenv("SOLANA_BOT2_PRIVATE_KEY", "").strip(),
            solana_bot2_wallet=os.getenv("SOLANA_BOT2_WALLET", "E1SGnXKMudseFBLE8A6ojpCC5dXjQpSvBhD85ErvPWna").strip(),
            solana_bot3_enabled=os.getenv("SOLANA_BOT3_ENABLED", "true").strip().lower() == "true",
            solana_bot3_private_key=os.getenv("SOLANA_BOT3_PRIVATE_KEY", "").strip(),
            solana_bot3_wallet=os.getenv("SOLANA_BOT3_WALLET", "CZyiWa7TnwMhWVDeaDjc9TDMs51bZqXj3WSTAFBqYmvn").strip(),
            helius_api_key=os.getenv("HELIUS_API_KEY", "").strip(),
            birdeye_api_key=os.getenv("BIRDEYE_API_KEY", "").strip(),
            solana_max_buy_sol=float(os.getenv("SOLANA_MAX_BUY_SOL", "0.05").strip()),
            solana_default_slippage_bps=int(os.getenv("SOLANA_SLIPPAGE_BPS", "300").strip()),
            solana_priority_fee_lamports=int(os.getenv("SOLANA_PRIORITY_FEE", "100000").strip()),
            # 주간 리포트
            weekly_report_enabled=os.getenv("WEEKLY_REPORT_ENABLED", "true").strip().lower() == "true",
            weekly_report_hour_kst=int(os.getenv("WEEKLY_REPORT_HOUR_KST", "21").strip()),
            auto_wallet_discovery=os.getenv("AUTO_WALLET_DISCOVERY", "true").strip().lower() == "true",
        )
