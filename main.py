"""
멀티 에이전트 자동매매 AI 시스템 — 메인 엔트리포인트

시스템 초기화 → 에이전트 등록 → 메인 루프 실행
"""

import asyncio
import logging
import sys
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

    # 에이전트 등록
    for agent_id, config in AGENT_CONFIGS.items():
        cls = AGENT_CLASSES.get(agent_id)
        if cls:
            agent = cls(config)
            # MemoryAgent에 메모리 주입
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
    }


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

    trades_since_evolution = 0
    scan_count = 0
    trigger_count = 0
    paused = False

    # pause/resume 콜백
    def on_pause():
        nonlocal paused
        paused = True

    def on_resume():
        nonlocal paused
        paused = False

    telegram.set_callbacks(on_pause=on_pause, on_resume=on_resume)

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

                for pair in settings.trading_pairs:
                    # ═══════════════════════════════════════════
                    # STAGE 1: 지표 스캔 (무료 — 매 60초)
                    # ═══════════════════════════════════════════
                    scan_count += 1

                    if not okx:
                        logger.debug(f"[{pair}] OKX 미연결 — 스킵")
                        continue

                    # 캔들 데이터만 가져오기 (가벼운 API 호출)
                    candles_1h = await okx.get_candles(pair, "1h", 100)
                    candles_15m = await okx.get_candles(pair, "15m", 100)

                    if not candles_1h:
                        logger.warning(f"[{pair}] 캔들 데이터 없음")
                        continue

                    # 지표 기반 필터링 (코드로 계산 — 무료)
                    filter_result = signal_filter.check(pair, candles_1h, candles_15m)

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
                    trades_since_evolution += 1

                    # ── 주문 실행 ──────────────────────────────
                    if (
                        record.final_action == "EXECUTED"
                        and record.judgment
                        and record.risk_review
                        and record.risk_review.approved
                    ):
                        signal = record.judgment.signal.value
                        confidence = record.judgment.confidence
                        balance = await okx.get_balance()

                        # 확신도 기반 포지션 사이징
                        if confidence >= 0.85:
                            position_pct = min(30, 20 + (confidence - 0.85) * 100)
                        elif confidence >= 0.7:
                            position_pct = min(20, 10 + (confidence - 0.7) * 67)
                        else:
                            position_pct = min(10, 5 + (confidence - 0.6) * 50)

                        usdt_amount = balance["free"] * (position_pct / 100)

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

                            # 동적 레버리지
                            dynamic_leverage = okx.calculate_leverage(confidence, volatility)

                            # 동적 손절/익절
                            price = market_data.get("ticker", {}).get("last", 0)
                            sl_base = settings.stop_loss_pct / 100
                            tp_base = settings.take_profit_pct / 100
                            lev_factor = 20 / dynamic_leverage
                            sl_pct = sl_base * lev_factor
                            tp_pct = tp_base * lev_factor

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
