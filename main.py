"""
멀티 에이전트 자동매매 AI 시스템 — 메인 엔트리포인트

시스템 초기화 → 에이전트 등록 → 메인 루프 실행
"""

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent))

load_dotenv()

from core import BaseAgent, AgentRegistry, MessageBus
from config import Settings, AGENT_CONFIGS
from db.database import Database
from memory.episode_memory import EpisodeMemory
from memory.performance_memory import PerformanceMemory
from memory.pattern_memory import PatternMemory
from debate.debate_room import DebateRoom
from evolution.strategy_evolver import StrategyEvolver
from evolution.performance_tracker import PerformanceTracker
from monitoring.telegram_monitor import TelegramMonitor
from dashboard.app import create_app
from execution.okx_exchange import OKXExchange
from data.signal_filter import SignalFilter
from data.market_regime import MarketRegimeDetector
from evolution.trade_feedback import TradeFeedback
from morning_brief import MorningBriefEngine

# ── 에이전트 임포트 ──────────────────────────────────────
from agents.analysts.trend_agent import TrendAgent
from agents.analysts.momentum_agent import MomentumAgent
from agents.analysts.volatility_agent import VolatilityAgent
from agents.analysts.volume_agent import VolumeAgent
from agents.analysts.macro_agent import MacroAgent
from agents.analysts.pattern_agent import PatternAgent
from agents.analysts.whale_agent import WhaleAgent
from agents.analysts.copytrade_agent import CopyTradeAgent
from agents.analysts.onchain_agent import OnChainAgent
from agents.special.moderator_agent import ModeratorAgent
from agents.special.judge_agent import JudgeAgent
from agents.special.risk_agent import RiskAgent
from agents.special.memory_agent import MemoryAgent
from agents.special.evolution_agent import EvolutionAgent
from agents.special.recruiter_agent import RecruiterAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

# 에이전트 ID → 클래스 매핑
AGENT_CLASSES = {
    "trend": TrendAgent,
    "momentum": MomentumAgent,
    "volatility": VolatilityAgent,
    "volume": VolumeAgent,
    "macro": MacroAgent,
    "pattern": PatternAgent,
    "whale": WhaleAgent,
    "copytrade": CopyTradeAgent,
    "onchain": OnChainAgent,
    "moderator": ModeratorAgent,
    "judge": JudgeAgent,
    "risk": RiskAgent,
    "memory": MemoryAgent,
    "evolution": EvolutionAgent,
    "recruiter": RecruiterAgent,
}


async def initialize_system():
    """시스템 초기화 — 모든 컴포넌트 생성 및 연결"""
    settings = Settings.from_env()

    # 인프라
    db = Database(settings.db_path)
    registry = AgentRegistry()
    bus = MessageBus()

    # 메모리
    episode_memory = EpisodeMemory(db)
    perf_memory = PerformanceMemory(db)
    pattern_memory = PatternMemory(db)

    print("=" * 60)
    print("  멀티 에이전트 자동매매 AI 시스템")
    print("=" * 60)
    print(f"  Trading Pairs: {', '.join(settings.trading_pairs)}")
    print(f"  Decision Interval: {settings.decision_interval_seconds}s")
    print(f"  Max Drawdown: {settings.max_drawdown_pct}%")
    print(f"  Debate Rounds: {settings.debate_rounds}")
    print(f"  Evolution Interval: 매 {settings.weight_update_interval}거래")
    print("=" * 60)

    # 비활성화할 에이전트 (가짜 데이터로 신호 희석시키는 에이전트)
    DISABLED_AGENTS = {"whale", "copytrade", "onchain"}

    # 에이전트 등록
    for agent_id, config in AGENT_CONFIGS.items():
        if agent_id in DISABLED_AGENTS:
            print(f"  [x] {config.name} — 비활성화 (실제 데이터 없음)")
            continue
        cls = AGENT_CLASSES.get(agent_id)
        if cls:
            agent = cls(config)
            if isinstance(agent, MemoryAgent):
                agent.set_memory(episode_memory, pattern_memory)
            registry.register(agent)
            print(f"  [+] {config.name} ({config.role.value}) [{config.model}]")
        else:
            print(f"  [!] {agent_id}: 클래스 미구현, 건너뜀")

    print(f"\n  총 {len(registry)}개 에이전트 등록 완료")
    print("=" * 60)

    # 토론방
    debate_room = DebateRoom(
        registry=registry,
        message_bus=bus,
        debate_rounds=settings.debate_rounds,
    )

    # 진화 엔진
    tracker = PerformanceTracker(db, registry)
    evolver = StrategyEvolver(
        registry=registry,
        bus=bus,
        db=db,
        perf_memory=perf_memory,
        settings=settings,
    )

    # 텔레그램 모니터
    telegram = TelegramMonitor(
        settings=settings,
        registry=registry,
        db=db,
        tracker=tracker,
    )

    # 사전 필터링 엔진 (무료 — AI 호출 전 게이트키퍼)
    signal_filter = SignalFilter()

    # OKX 거래소
    okx = None
    if settings.okx_api_key:
        okx = OKXExchange(
            api_key=settings.okx_api_key,
            api_secret=settings.okx_api_secret,
            passphrase=settings.okx_passphrase,
            testnet=settings.exchange_testnet,
            leverage_min=settings.leverage_min,
            leverage_max=settings.leverage_max,
        )
        if await okx.initialize():
            balance = await okx.get_balance()
            print(f"  OKX 연결 성공! 잔고: ${balance['total']:.2f}")
        else:
            print("  ⚠️ OKX 연결 실패 — Mock 모드로 전환")
            okx = None
    else:
        print("  OKX API 키 미설정 — Mock 모드")

    # 대시보드
    dashboard_app = create_app(
        registry=registry,
        db=db,
        tracker=tracker,
        settings=settings,
    )
    # OKX 객체를 대시보드에 연결 (캔들 차트용)
    if okx:
        dashboard_app.state.okx = okx

    return {
        "settings": settings,
        "db": db,
        "registry": registry,
        "bus": bus,
        "episode_memory": episode_memory,
        "perf_memory": perf_memory,
        "pattern_memory": pattern_memory,
        "debate_room": debate_room,
        "evolver": evolver,
        "telegram": telegram,
        "dashboard_app": dashboard_app,
        "okx": okx,
        "signal_filter": signal_filter,
        "feedback": TradeFeedback(db, registry=registry, fee_taker_pct=settings.fee_taker_pct),
        "morning_brief": None,  # main_loop에서 초기화
    }


def _check_position_exit(
    position: dict, filter_result: dict, candles_1h: list[dict]
) -> tuple[bool, str, dict]:
    """
    열린 포지션 능동 청산 판단 (코드 기반, $0)
    트레일링 스탑 + 기술적 신호 + 수익권 SL 이동

    Returns: (should_close, reason, sl_update)
        sl_update: {"new_sl": price, "new_stage": N} or {}
    """
    side = position.get("side", "buy")
    is_long = side in ("buy", "long")
    indicators = filter_result.get("indicators", {})
    current_price = indicators.get("current_price", 0)
    entry_price = position.get("entry_price", 0)
    sl_update = {}  # SL 이동 정보

    rsi = indicators.get("rsi", 50)
    adx = indicators.get("adx", 0)
    ema_20 = indicators.get("ema_20", 0)
    ema_50 = indicators.get("ema_50", 0)
    macd_hist = indicators.get("macd_histogram", 0)
    direction = filter_result.get("direction_hint", "NEUTRAL")
    direction_strength = filter_result.get("direction_strength", 0)

    # ── 트레일링 스탑 ────────────────────────────────
    if position.get("use_trailing") and current_price and entry_price:
        trailing_pct = position.get("trailing_pct", 0.5) / 100

        if is_long:
            # 최고가 업데이트
            highest = position.get("highest_price", entry_price)
            if current_price > highest:
                position["highest_price"] = current_price
                highest = current_price

            # 최고가 대비 trailing_pct만큼 하락하면 청산
            trailing_stop = highest * (1 - trailing_pct)
            if current_price <= trailing_stop and current_price > entry_price:
                pnl = (current_price - entry_price) / entry_price * 100
                return True, f"트레일링 스탑 (최고 ${highest:,.0f} → 현재 ${current_price:,.0f}, 수익 {pnl:.1f}% 확보)", sl_update
        else:
            # 최저가 업데이트
            lowest = position.get("lowest_price", entry_price)
            if current_price < lowest:
                position["lowest_price"] = current_price
                lowest = current_price

            # 최저가 대비 trailing_pct만큼 상승하면 청산
            trailing_stop = lowest * (1 + trailing_pct)
            if current_price >= trailing_stop and current_price < entry_price:
                pnl = (entry_price - current_price) / entry_price * 100
                return True, f"트레일링 스탑 (최저 ${lowest:,.0f} → 현재 ${current_price:,.0f}, 수익 {pnl:.1f}% 확보)", sl_update

    # ── 수익권 SL 이동 (Breakeven → Lock-in) ─────────
    if current_price and entry_price:
        atr_value = position.get("atr_value", 0)
        sl_stage = position.get("sl_stage", 0)
        fee_be = position.get("fee_breakeven_pct", 0.001)  # 수수료 손익분기 %

        if atr_value and atr_value > 0:
            if is_long:
                profit_pct = (current_price - entry_price) / entry_price
            else:
                profit_pct = (entry_price - current_price) / entry_price

            # 단계별 SL 이동
            if sl_stage < 1 and profit_pct >= fee_be * 2:
                # 1단계: 수수료 × 2 수익 → SL을 진입가 (Breakeven)
                new_sl = entry_price
                sl_update = {"new_sl": new_sl, "new_stage": 1, "label": "Breakeven"}
                position["sl_stage"] = 1

            elif sl_stage < 2 and profit_pct >= atr_value / entry_price:
                # 2단계: 1 ATR 수익 → SL 이동 + 부분 익절 50%
                if is_long:
                    new_sl = entry_price + atr_value * 0.5
                else:
                    new_sl = entry_price - atr_value * 0.5
                sl_update = {
                    "new_sl": new_sl, "new_stage": 2, "label": "Lock 0.5ATR",
                    "partial_tp": True,  # 부분 익절 신호
                    "partial_pct": 50,   # 50% 청산
                }
                position["sl_stage"] = 2

            elif sl_stage < 3 and profit_pct >= (atr_value * 2) / entry_price:
                # 3단계: 2 ATR 수익 → SL을 진입가 + 1 ATR
                if is_long:
                    new_sl = entry_price + atr_value
                else:
                    new_sl = entry_price - atr_value
                sl_update = {"new_sl": new_sl, "new_stage": 3, "label": "Lock 1ATR"}
                position["sl_stage"] = 3

    # ── 기술적 신호 기반 청산 (완화: 수익 키우기) ──────

    # 수익/손실 상태 확인
    in_profit = False
    profit_pct_raw = 0
    if current_price and entry_price and entry_price > 0:
        if is_long:
            profit_pct_raw = (current_price - entry_price) / entry_price * 100
        else:
            profit_pct_raw = (entry_price - current_price) / entry_price * 100
        in_profit = profit_pct_raw > 0

    # 1. 반대 방향 매우 강한 신호 → 수익중이면 즉시 청산
    #    (기존: 3개 → 변경: 4개 이상, 수익중일 때만)
    if is_long and direction == "SELL" and direction_strength >= 4 and in_profit:
        return True, f"반대 강신호 (SELL {direction_strength}개, 수익 {profit_pct_raw:.2f}%)", sl_update
    if not is_long and direction == "BUY" and direction_strength >= 4 and in_profit:
        return True, f"반대 강신호 (BUY {direction_strength}개, 수익 {profit_pct_raw:.2f}%)", sl_update

    # 2. 추세 전환 감지 (수익중 + ADX 강할 때만)
    if in_profit and adx > 35:
        if is_long and ema_20 and ema_50 and ema_20 < ema_50:
            return True, f"추세 전환 (EMA 역배열 + ADX {adx:.0f})", sl_update
        if not is_long and ema_20 and ema_50 and ema_20 > ema_50:
            return True, f"추세 전환 (EMA 정배열 + ADX {adx:.0f})", sl_update

    # 3. RSI 극단 반전 (수익 +1% 이상일 때만)
    if in_profit and profit_pct_raw > 1.0:
        if is_long and rsi > 85:
            return True, f"RSI 극단 과매수 ({rsi:.0f}) — 수익 {profit_pct_raw:.2f}% 확보", sl_update
        if not is_long and rsi < 15:
            return True, f"RSI 극단 과매도 ({rsi:.0f}) — 수익 {profit_pct_raw:.2f}% 확보", sl_update

    # 4. MACD 반전은 제거 — 너무 빨리 청산해서 수익을 못 키움
    #    대신 SL 이동 + 트레일링이 수익 보호 역할

    return False, "", sl_update


def _update_agent_scores(
    db: Database, position: dict, pnl_pct_lev: float, exit_price: float
) -> tuple[list[str], list[str]]:
    """
    거래 종료 시 에이전트 정답/오답 판정 + DB 업데이트

    Returns: (agents_correct, agents_wrong)
    """
    cycle_id = position.get("cycle_id")
    if not cycle_id:
        return [], []

    judge_signal = position.get("judge_signal", "")

    # 수익이면 judge_signal이 맞은 것, 손실이면 반대 방향이 맞은 것
    if pnl_pct_lev > 0:
        correct_signal = judge_signal
    else:
        correct_signal = "SELL" if judge_signal == "BUY" else "BUY"

    # DB 업데이트: 에이전트별 정답/오답 기록
    agents_correct, agents_wrong = db.update_agent_correctness(cycle_id, correct_signal)

    # 거래 결과 업데이트
    pnl_usd = position.get("margin", 0) * (pnl_pct_lev / 100)
    db.update_trade_result(cycle_id, exit_price, pnl_pct_lev, pnl_usd)

    logger.info(
        f"📊 에이전트 성과 업데이트: cycle={cycle_id} "
        f"PnL={pnl_pct_lev:+.2f}% 정답={correct_signal} "
        f"맞춘={agents_correct} 틀린={agents_wrong}"
    )

    return agents_correct, agents_wrong


async def main_loop(system: dict):
    """메인 트레이딩 루프"""
    settings = system["settings"]
    registry = system["registry"]
    debate_room = system["debate_room"]
    evolver = system["evolver"]
    db = system["db"]
    telegram = system["telegram"]
    okx = system["okx"]
    signal_filter = system["signal_filter"]
    feedback = system["feedback"]

    # 모닝 브리프 엔진 초기화
    morning_brief = MorningBriefEngine(
        settings=settings, okx=okx, feedback=feedback,
        db=db, telegram=telegram,
    )
    logger.info(
        f"🌅 모닝 브리프 활성 (매일 {settings.morning_brief_hour_kst}시 KST)"
        if settings.morning_brief_enabled else "🌅 모닝 브리프 비활성"
    )

    trades_since_evolution = 0
    scan_count = 0
    trigger_count = 0
    paused = False
    known_positions: dict[str, dict] = {}  # symbol → position 추적

    # 일일 손실 한도 추적
    daily_pnl = 0.0
    daily_trade_count = 0
    daily_max_trades = 999  # 거래 제한 없음
    from datetime import date as _date
    current_date = _date.today()

    # pause/resume 콜백
    def on_pause():
        nonlocal paused
        paused = True

    def on_resume():
        nonlocal paused
        paused = False

    telegram.set_callbacks(on_pause=on_pause, on_resume=on_resume)
    telegram._feedback = feedback

    # 텔레그램 폴링을 백그라운드로 시작
    polling_task = asyncio.create_task(telegram.start_polling())

    # 시작 알림
    await telegram.send(
        "🚀 <b>자동매매 시스템 시작</b>\n\n"
        "📊 2단계 구조: 지표 스캔(무료) → AI 토론(유료)\n"
        "<code>!help</code>로 명령어 확인"
    )

    logger.info("메인 루프 시작 (2단계: 지표 스캔 → AI 토론)")

    try:
        while True:
            try:
                if paused:
                    await asyncio.sleep(2)
                    continue

                # ── 모닝 브리프 체크 (매일 7시 KST) ──────
                try:
                    if await morning_brief.should_run_now():
                        await morning_brief.generate_and_send()
                except Exception as e:
                    logger.warning(f"모닝 브리프 에러: {e}")

                # ── 일일 리셋 ────────────────────────────
                today = _date.today()
                if today != current_date:
                    logger.info(f"📅 일일 리셋: PnL {daily_pnl:+.2f}%, 거래 {daily_trade_count}회")
                    daily_pnl = 0.0
                    daily_trade_count = 0
                    current_date = today

                # ── 일일 손실 한도 체크 ──────────────────
                if daily_pnl <= -settings.max_drawdown_pct:
                    if scan_count % 60 == 0:
                        logger.warning(
                            f"🛑 일일 손실 한도 도달: {daily_pnl:+.2f}% "
                            f"(한도 -{settings.max_drawdown_pct}%) → 매매 중지"
                        )
                    await asyncio.sleep(settings.decision_interval_seconds)
                    scan_count += 1
                    continue

                # ── 일일 거래 횟수 제한 ──────────────────
                if daily_trade_count >= daily_max_trades and not known_positions:
                    if scan_count % 60 == 0:
                        logger.info(
                            f"⏸️ 일일 거래 한도 {daily_max_trades}회 도달 → 신규 진입 중지"
                        )

                # ═══════════════════════════════════════════
                # STAGE 0: 포지션 동기화 (OKX 실제 상태 확인)
                # ═══════════════════════════════════════════
                if okx:
                    try:
                        current_positions = await okx.get_positions()
                        current_symbols = {p["symbol"] for p in current_positions}

                        # 사라진 포지션 감지 (수동 청산 또는 SL/TP 체결)
                        for sym, pos in list(known_positions.items()):
                            if sym not in current_symbols:
                                # 현재가 조회해서 PnL 추정
                                try:
                                    ticker = await okx.get_ticker(sym)
                                    exit_price = ticker.get("last", 0) if ticker else 0
                                except Exception:
                                    exit_price = 0

                                entry = pos.get("entry_price", 0)
                                lev = pos.get("leverage", 1)
                                if entry and exit_price:
                                    if pos.get("side") in ("buy", "long"):
                                        pnl_pct = (exit_price - entry) / entry * 100
                                    else:
                                        pnl_pct = (entry - exit_price) / entry * 100
                                    pnl_pct_lev = pnl_pct * lev
                                else:
                                    pnl_pct = 0
                                    pnl_pct_lev = 0

                                # ★ 에이전트 정답/오답 판정 (먼저!)
                                agents_correct, agents_wrong = _update_agent_scores(
                                    db, pos, pnl_pct_lev, exit_price
                                )

                                # 피드백 기록 + 에이전트 가중치 실시간 업데이트
                                feedback.record_trade({
                                    "symbol": sym,
                                    "side": pos.get("side", "?"),
                                    "entry_price": entry,
                                    "exit_price": exit_price,
                                    "pnl_pct": pnl_pct,
                                    "pnl_pct_leveraged": pnl_pct_lev,
                                    "leverage": lev,
                                    "margin": pos.get("margin", 0),
                                    "exit_reason": "manual_or_sltp",
                                    "entry_signals": pos.get("entry_signals", []),
                                    "entry_confidence": pos.get("entry_confidence", 0),
                                    "agents_correct": agents_correct,
                                    "agents_wrong": agents_wrong,
                                })

                                emoji = "💰" if pnl_pct_lev > 0 else "💸"
                                # 에이전트 가중치 변화 요약
                                weight_info = ""
                                if agents_correct:
                                    weight_info += f"\n✅ 정답: {', '.join(agents_correct[:3])}"
                                if agents_wrong:
                                    weight_info += f"\n❌ 오답: {', '.join(agents_wrong[:3])}"

                                await telegram.send(
                                    f"{emoji} <b>포지션 청산 감지</b>\n\n"
                                    f"<b>심볼:</b> {sym}\n"
                                    f"<b>방향:</b> {pos.get('side', '?')}\n"
                                    f"<b>진입:</b> ${entry:,.2f}\n"
                                    f"<b>청산:</b> ${exit_price:,.2f}\n"
                                    f"<b>PnL:</b> {pnl_pct_lev:+.2f}%"
                                    f"{weight_info}\n"
                                    f"<b>누적:</b> {feedback.stats['total_trades']}거래 "
                                    f"승률 {feedback.win_rate:.0%}"
                                )
                                del known_positions[sym]
                                trades_since_evolution += 1
                                daily_trade_count += 1
                                daily_pnl += pnl_pct_lev

                        # 새 포지션 업데이트 (커스텀 필드 보존)
                        for pos in current_positions:
                            sym = pos["symbol"]
                            if sym in known_positions:
                                # 기존 커스텀 필드 유지 + OKX 데이터만 업데이트
                                existing = known_positions[sym]
                                existing.update({
                                    "size": pos.get("size", existing.get("size", 0)),
                                    "mark_price": pos.get("mark_price", 0),
                                    "unrealized_pnl": pos.get("unrealized_pnl", 0),
                                    "liquidation_price": pos.get("liquidation_price", 0),
                                })
                            else:
                                # 외부에서 열린 포지션 (수동 등)
                                known_positions[sym] = pos

                    except Exception as e:
                        logger.debug(f"포지션 동기화 에러: {e}")

                for pair in settings.trading_pairs:
                    # ═══════════════════════════════════════════
                    # STAGE 1: 지표 스캔 (무료 — 매 60초)
                    # ═══════════════════════════════════════════
                    scan_count += 1

                    if not okx:
                        logger.debug(f"[{pair}] OKX 미연결 — 스킵")
                        continue

                    # 캔들 데이터 (멀티 타임프레임)
                    candles_1h = await okx.get_candles(pair, "1h", 100)
                    candles_15m = await okx.get_candles(pair, "15m", 100)
                    candles_4h = await okx.get_candles(pair, "4h", 100)

                    if not candles_1h:
                        logger.warning(f"[{pair}] 캔들 데이터 없음")
                        continue

                    # 펀딩비 조회
                    try:
                        funding_data = await okx.get_funding_rate(pair)
                        funding_rate = funding_data.get("funding_rate", 0)
                    except Exception:
                        funding_rate = 0

                    # 시장 국면 감지 + 멀티 타임프레임 정렬
                    regime_detector = MarketRegimeDetector()
                    market_regime = regime_detector.detect(candles_15m, candles_1h, candles_4h)

                    # 지표 기반 필터링 (펀딩비 + 국면 포함)
                    filter_result = signal_filter.check(
                        pair, candles_1h, candles_15m,
                        funding_rate=funding_rate,
                        market_regime=market_regime,
                    )

                    # 30분마다 국면 상태 로그
                    if scan_count % 30 == 1:
                        regime_label = market_regime.get("regime_label", "?")
                        tf_aligned = market_regime.get("timeframe_alignment", False)
                        tf_dir = market_regime.get("alignment_direction", "?")
                        logger.info(
                            f"[{pair}] 국면={regime_label} TF정렬={tf_aligned}({tf_dir}) "
                            f"펀딩={funding_rate:.4f}%"
                        )

                    # ═══════════════════════════════════════════
                    # STAGE 1.5: 열린 포지션 능동 관리 (무료)
                    # ═══════════════════════════════════════════
                    if pair in known_positions and okx:
                        pos = known_positions[pair]
                        should_close, close_reason, sl_update = _check_position_exit(
                            pos, filter_result, candles_1h
                        )

                        # ── 수익권 SL 이동 (거래소 주문 업데이트) ──
                        if sl_update and not should_close:
                            new_sl = sl_update.get("new_sl")
                            new_stage = sl_update.get("new_stage", 0)
                            old_stage = pos.get("sl_stage", 0)
                            label = sl_update.get("label", "")

                            if new_sl and new_stage > old_stage:
                                # OKX에 SL 업데이트
                                size = pos.get("size", 0)
                                updated = await okx.update_tp_sl(
                                    pair, pos.get("side", "buy"), size,
                                    new_sl=new_sl,
                                )
                                if updated:
                                    pos["current_sl"] = new_sl
                                    pos["sl_stage"] = new_stage
                                    logger.info(
                                        f"🔒 [{pair}] SL 이동: "
                                        f"단계{old_stage}→{new_stage} ({label}) "
                                        f"SL=${new_sl:,.2f}"
                                    )
                                    await telegram.send(
                                        f"🔒 <b>SL 이동</b>\n\n"
                                        f"<b>심볼:</b> {pair}\n"
                                        f"<b>단계:</b> {old_stage} → {new_stage} ({label})\n"
                                        f"<b>새 SL:</b> ${new_sl:,.2f}\n"
                                        f"<b>진입가:</b> ${pos.get('entry_price', 0):,.2f}"
                                    )

                                    # ── 부분 익절 (50% 청산) ──────────
                                    if sl_update.get("partial_tp") and not pos.get("partial_closed"):
                                        partial_pct = sl_update.get("partial_pct", 50)
                                        partial_size = size * (partial_pct / 100)
                                        if partial_size > 0:
                                            try:
                                                await okx.close_position(
                                                    pair, pos.get("side", "buy"), partial_size
                                                )
                                                pos["partial_closed"] = True
                                                pos["size"] = size - partial_size
                                                logger.info(
                                                    f"💰 [{pair}] 부분 익절: "
                                                    f"{partial_pct}% 청산 (수익 확보), "
                                                    f"나머지 {100-partial_pct}% 트레일링"
                                                )
                                                await telegram.send(
                                                    f"💰 <b>부분 익절 {partial_pct}%</b>\n\n"
                                                    f"<b>심볼:</b> {pair}\n"
                                                    f"<b>수익 확보:</b> 포지션의 절반 청산\n"
                                                    f"<b>나머지:</b> 트레일링 스탑으로 수익 극대화"
                                                )
                                            except Exception as e:
                                                logger.warning(f"부분 익절 실패 [{pair}]: {e}")

                        if should_close:
                            logger.info(f"🔄 [{pair}] 능동 청산: {close_reason}")
                            result = await okx.close_position(
                                pair, pos.get("side", "buy"), pos.get("size", 0)
                            )
                            if result:
                                # PnL 계산
                                entry = pos.get("entry_price", 0)
                                current = filter_result["indicators"].get("current_price", 0)
                                if entry and current:
                                    if pos.get("side") == "buy" or pos.get("side") == "long":
                                        pnl_pct = (current - entry) / entry * 100
                                    else:
                                        pnl_pct = (entry - current) / entry * 100
                                    lev = pos.get("leverage", 1)
                                    pnl_pct_lev = pnl_pct * lev
                                else:
                                    pnl_pct_lev = 0

                                # ★ 에이전트 정답/오답 판정 (먼저!)
                                agents_correct, agents_wrong = _update_agent_scores(
                                    db, pos, pnl_pct_lev, current
                                )

                                # 피드백 기록 + 에이전트 가중치 실시간 업데이트
                                fb = feedback.record_trade({
                                    "symbol": pair,
                                    "side": pos.get("side", "?"),
                                    "entry_price": entry,
                                    "exit_price": current,
                                    "pnl_pct": pnl_pct if entry else 0,
                                    "pnl_pct_leveraged": pnl_pct_lev,
                                    "leverage": lev,
                                    "margin": pos.get("margin", 0),
                                    "exit_reason": "active_exit",
                                    "entry_signals": pos.get("entry_signals", []),
                                    "entry_confidence": pos.get("entry_confidence", 0),
                                    "agents_correct": agents_correct,
                                    "agents_wrong": agents_wrong,
                                })

                                emoji = "💰" if pnl_pct_lev > 0 else "💸"
                                # 에이전트 가중치 변화 요약
                                weight_info = ""
                                if agents_correct:
                                    weight_info += f"\n✅ 정답: {', '.join(agents_correct[:3])}"
                                if agents_wrong:
                                    weight_info += f"\n❌ 오답: {', '.join(agents_wrong[:3])}"

                                await telegram.send(
                                    f"{emoji} <b>능동 청산</b>\n\n"
                                    f"<b>심볼:</b> {pair}\n"
                                    f"<b>방향:</b> {pos.get('side', '?')}\n"
                                    f"<b>사유:</b> {close_reason}\n"
                                    f"<b>PnL:</b> {pnl_pct_lev:+.2f}%\n"
                                    f"<b>교훈:</b> {fb.get('lesson', 'N/A')[:150]}"
                                    f"{weight_info}\n"
                                    f"<b>누적:</b> {feedback.stats['total_trades']}거래 "
                                    f"승률 {feedback.win_rate:.0%}"
                                )
                                del known_positions[pair]
                                trades_since_evolution += 1
                                daily_trade_count += 1
                                daily_pnl += pnl_pct_lev

                    if not filter_result["should_trigger"]:
                        # 신호 없음 → AI 호출 안 함 → 비용 $0
                        if scan_count % 30 == 0:  # 30분마다 상태 로그
                            logger.info(
                                f"[{pair}] 스캔 #{scan_count} — "
                                f"신호 {filter_result['signal_count']}개 (임계값 미달) | "
                                f"AI 트리거: {trigger_count}회"
                            )
                        continue

                    # ═══════════════════════════════════════════
                    # STAGE 2: AI 토론 (유료 — 신호 감지 시만)
                    # ═══════════════════════════════════════════
                    trigger_count += 1
                    logger.info(
                        f"🔔 [{pair}] AI 토론 발동! (스캔 #{scan_count}, "
                        f"트리거 #{trigger_count}) | {filter_result['reason']}"
                    )

                    # 전체 시장 데이터 수집 (AI에게 전달)
                    market_data = await okx.get_market_data(pair, settings.timeframes)
                    market_data["pre_filter"] = filter_result

                    # AI 에이전트 토론
                    record = await debate_room.run_cycle(market_data)

                    # 에피소드 저장
                    db.save_episode(record.to_dict())
                    for analysis in record.analyses:
                        db.save_agent_performance(
                            agent_id=analysis.agent_id,
                            cycle_id=record.cycle_id,
                            signal=analysis.signal.value,
                            confidence=analysis.confidence,
                            reasoning=analysis.reasoning[:500],
                        )

                    # ── 주문 실행 ──────────────────────────────
                    if (
                        record.final_action == "EXECUTED"
                        and record.judgment
                        and record.risk_review
                        and record.risk_review.approved
                    ):
                        signal = record.judgment.signal.value
                        confidence = record.judgment.confidence

                        # ── 중복 포지션 차단 ─────────────────
                        # 같은 페어에 이미 포지션이 있으면 새 포지션 안 열음
                        if pair in known_positions:
                            existing = known_positions[pair]
                            existing_side = existing.get("side", "")
                            new_side = "buy" if signal == "BUY" else "sell"
                            if existing_side == new_side:
                                logger.info(f"🚫 [{pair}] 이미 {existing_side} 포지션 있음 → 스킵")
                            else:
                                logger.info(
                                    f"🚫 [{pair}] 반대 방향 포지션 차단: "
                                    f"기존={existing_side}, 신규={new_side} → 양방향 금지"
                                )
                            continue

                        # ── 일일 거래 횟수 제한 ──────────────
                        if daily_trade_count >= daily_max_trades:
                            logger.info(f"🚫 [{pair}] 일일 거래 한도 {daily_max_trades}회 → 스킵")
                            continue

                        # ── 동시 포지션 수 제한 ──────────────
                        if len(known_positions) >= settings.max_concurrent_positions:
                            logger.info(
                                f"🚫 [{pair}] 최대 동시 포지션 "
                                f"{settings.max_concurrent_positions}개 도달 → 스킵"
                            )
                            continue

                        balance = await okx.get_balance()

                        # ── 피드백 기반 진입 필터 ─────────────
                        fb_adj = feedback.get_adjustments()
                        min_conf = fb_adj.get(
                            "min_confidence_override",
                            settings.min_confidence_threshold  # 기본 0.55
                        )
                        if confidence < min_conf:
                            logger.info(
                                f"🚫 [{pair}] 확신도 {confidence:.0%} < 최소 {min_conf:.0%} (피드백 조정) → 스킵"
                            )
                            continue

                        # 최악 조건 회피
                        avoid = fb_adj.get("avoid_condition")
                        if avoid:
                            entry_signals = filter_result.get("signals", [])
                            if any(avoid in s for s in entry_signals):
                                logger.info(f"🚫 [{pair}] 최악 조건 '{avoid}' 감지 → 스킵")
                                continue

                        # 국면별 전략 파라미터 적용
                        regime_params = market_regime.get("strategy_params", {})
                        regime_size_mult = regime_params.get("position_size_multiplier", 1.0)
                        regime_lev_mult = regime_params.get("leverage_multiplier", 1.0)
                        tf_boost = market_regime.get("confidence_boost", 0)
                        confidence = min(1.0, confidence + tf_boost)  # TF 정렬 보너스

                        # 확신도 기반 포지션 사이징 (적극적)
                        if confidence >= 0.8:
                            position_pct = 40  # 매우 강한 신호
                        elif confidence >= 0.6:
                            position_pct = 30  # 강한 신호
                        elif confidence >= 0.4:
                            position_pct = 20  # 보통 신호
                        else:
                            position_pct = 15  # 약한 신호 (최소)

                        # 국면별 포지션 크기 조정
                        position_pct *= regime_size_mult

                        # 피드백 기반 포지션 조정 (연패복구/연승공격)
                        position_multiplier = fb_adj.get("position_size_multiplier", 1.0)
                        position_pct *= position_multiplier

                        # 시간대별 포지션 보정
                        session_mult = fb_adj.get("session_multiplier", 1.0)
                        position_pct *= session_mult

                        if position_multiplier != 1.0 or session_mult != 1.0:
                            reason = fb_adj.get("reason", "")
                            session = fb_adj.get("session", "")
                            logger.info(
                                f"📊 [{pair}] 포지션 조정: "
                                f"x{position_multiplier:.1f}({reason}) "
                                f"세션 x{session_mult:.1f}({session})"
                            )

                        usdt_amount = max(0, balance["free"] * (position_pct / 100))

                        if usdt_amount >= 3:
                            side = "buy" if signal == "BUY" else "sell"

                            # 변동성 판단
                            change_24h = abs(market_data.get("ticker", {}).get("change_24h_pct", 0) or 0)
                            if change_24h > 8:
                                volatility = "extreme"
                            elif change_24h > 5:
                                volatility = "high"
                            elif change_24h < 2:
                                volatility = "low"
                            else:
                                volatility = "normal"

                            # 동적 레버리지 (국면 보정)
                            dynamic_leverage = okx.calculate_leverage(confidence, volatility)
                            dynamic_leverage = max(
                                settings.leverage_min,
                                int(dynamic_leverage * regime_lev_mult)
                            )

                            # ── ATR 기반 동적 TP/SL ─────────────────
                            price = market_data.get("ticker", {}).get("last", 0)
                            atr_pct = filter_result.get("indicators", {}).get("atr_pct", 0)
                            atr_value = filter_result.get("indicators", {}).get("atr", 0)

                            # 수수료 손익분기
                            fee_breakeven_pct = (settings.fee_taker_pct * 2) / 100

                            if atr_pct and atr_pct > 0:
                                # ATR 기반: SL = 1.5 ATR, TP = 3 ATR (손익비 2:1)
                                sl_pct = (atr_pct / 100) * 1.5
                                tp_pct = (atr_pct / 100) * 3.0

                                # 최소 TP = 수수료의 3배 (수익 보장)
                                tp_pct = max(tp_pct, fee_breakeven_pct * 3)
                                # 최소 SL = 수수료의 2배 (너무 가까우면 노이즈에 청산됨)
                                sl_pct = max(sl_pct, fee_breakeven_pct * 2)
                            else:
                                # ATR 없으면 기존 방식 폴백
                                lev_factor = 20 / dynamic_leverage
                                sl_pct = (settings.stop_loss_pct / 100) * lev_factor
                                tp_pct = max(
                                    (settings.take_profit_pct / 100) * lev_factor,
                                    fee_breakeven_pct * 3,
                                )

                            logger.info(
                                f"[{pair}] TP/SL: ATR={atr_value:.0f}({atr_pct:.3f}%) "
                                f"SL={sl_pct*100:.3f}% TP={tp_pct*100:.3f}% "
                                f"손익비={tp_pct/sl_pct:.1f}:1"
                            )

                            if side == "buy":
                                stop_loss = price * (1 - sl_pct)
                                take_profit = price * (1 + tp_pct)
                            else:
                                stop_loss = price * (1 + sl_pct)
                                take_profit = price * (1 - tp_pct)

                            order = await okx.open_position(
                                symbol=pair,
                                side=side,
                                usdt_amount=usdt_amount,
                                leverage=dynamic_leverage,
                                stop_loss=stop_loss,
                                take_profit=take_profit,
                            )

                            if order:
                                exposure = usdt_amount * dynamic_leverage
                                # 포지션 추적에 추가
                                use_trailing = regime_params.get("use_trailing_stop", False)
                                trailing_pct = regime_params.get("trailing_pct", 0.5)
                                known_positions[pair] = {
                                    "symbol": pair,
                                    "side": side,
                                    "entry_price": price,
                                    "size": order.get("amount", 0),  # 계약 수량 (SL 이동/청산용)
                                    "margin": usdt_amount,
                                    "leverage": dynamic_leverage,
                                    "entry_signals": filter_result.get("signals", []),
                                    "entry_confidence": confidence,
                                    "entry_time": datetime.utcnow().isoformat(),
                                    "cycle_id": record.cycle_id,
                                    "judge_signal": signal,
                                    "use_trailing": use_trailing,
                                    "trailing_pct": trailing_pct,
                                    "highest_price": price if side == "buy" else None,
                                    "lowest_price": price if side == "sell" else None,
                                    "regime": market_regime.get("regime_label", ""),
                                    # ATR 기반 SL 이동용
                                    "atr_value": atr_value,
                                    "atr_pct": atr_pct,
                                    "sl_stage": 0,  # 0=초기, 1=BE, 2=lock1, 3=lock2
                                    "current_sl": stop_loss,
                                    "current_tp": take_profit,
                                    "fee_breakeven_pct": fee_breakeven_pct,
                                }
                                logger.info(
                                    f"✅ 주문 체결: {side.upper()} {pair} "
                                    f"마진=${usdt_amount:.2f} 노출=${exposure:.2f} "
                                    f"레버리지={dynamic_leverage}x @ {price}"
                                )
                                await telegram.notify_trade_open({
                                    **record.to_dict(),
                                    "order": order,
                                    "leverage": dynamic_leverage,
                                    "exposure": exposure,
                                })
                        else:
                            logger.info(f"주문 금액 부족: ${usdt_amount:.2f} < $3")

                    # 결과 출력
                    if record.judgment:
                        logger.info(
                            f"판결: {record.judgment.signal.value} "
                            f"(확신도 {record.judgment.confidence:.0%}, "
                            f"포지션 {record.judgment.position_size_pct:.1f}%)"
                        )
                    if record.risk_review:
                        status = "승인" if record.risk_review.approved else f"거부: {record.risk_review.veto_reason}"
                        logger.info(f"리스크: {status}")
                    logger.info(f"최종: {record.final_action}")

                    # 진화 체크
                    if evolver.should_run(trades_since_evolution):
                        logger.info("=== 진화 사이클 트리거 ===")
                        evo_result = await evolver.run_evolution_cycle()
                        await telegram.notify_evolution(evo_result)
                        trades_since_evolution = 0

                await asyncio.sleep(settings.decision_interval_seconds)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"루프 에러: {e}", exc_info=True)
                await telegram.notify_error(str(e))
                await asyncio.sleep(10)

    finally:
        await telegram.send("🛑 <b>자동매매 시스템 종료</b>")
        await telegram.stop_polling()
        polling_task.cancel()
        if okx:
            await okx.close()
        db.close()
        logger.info("시스템 종료")


async def main():
    """엔트리포인트"""
    import uvicorn

    system = await initialize_system()

    summary = system["registry"].get_summary()
    tg_status = "연결됨" if system["telegram"].is_configured else "미설정"
    print(f"\n[Registry] Active: {summary['active']}, Isolated: {summary['isolated']}, Probation: {summary['probation']}")
    print(f"[Evolution] 매 {system['settings'].weight_update_interval}거래마다 자동 진화")
    print(f"[Evolution] 격리 기준: 승률 {system['settings'].isolation_win_rate:.0%} 이하")
    print(f"[Telegram] {tg_status}")
    print(f"[Dashboard] http://localhost:8080")
    print()

    # 대시보드 서버를 백그라운드에서 실행
    config = uvicorn.Config(
        system["dashboard_app"],
        host="0.0.0.0",
        port=8080,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    dashboard_task = asyncio.create_task(server.serve())

    # 메인 트레이딩 루프
    await main_loop(system)


if __name__ == "__main__":
    asyncio.run(main())
