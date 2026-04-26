"""
TelegramMonitor — 텔레그램 알림 및 제어

매매 알림, 에이전트 상태 변경, 성과 리포트를 텔레그램으로 전송하고
제어 명령어를 처리한다.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional, Callable, Awaitable

import aiohttp

from core.agent_registry import AgentRegistry
from core.base_agent import AgentRole, AgentStatus
from config.settings import Settings
from db.database import Database
from evolution.performance_tracker import PerformanceTracker

logger = logging.getLogger(__name__)


class TelegramMonitor:
    """텔레그램 봇 모니터링 시스템"""

    def __init__(
        self,
        settings: Settings,
        registry: AgentRegistry,
        db: Database,
        tracker: PerformanceTracker,
    ):
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.registry = registry
        self.db = db
        self.tracker = tracker
        self.settings = settings

        self._base_url = f"https://api.telegram.org/bot{self.token}"
        self._last_update_id = 0
        self._running = False

        # 제어 명령 핸들러
        self._commands: dict[str, Callable] = {
            "!status": self._cmd_status,
            "!agents": self._cmd_agents,
            "!performance": self._cmd_performance,
            "!weights": self._cmd_weights,
            "!evolution": self._cmd_evolution_status,
            "!pause": self._cmd_pause,
            "!resume": self._cmd_resume,
            "!help": self._cmd_help,
            "!report": self._cmd_report,
            # 솔라나 봇 모니터링
            "!positions": self._cmd_positions,
            "!solana": self._cmd_positions,        # 별칭
            "!포지션": self._cmd_positions,        # 한국어 별칭
            "!stats": self._cmd_solana_stats,
            "!wallets": self._cmd_wallets,
            "!지갑": self._cmd_wallets,            # 한국어 별칭
        }

        # 외부 콜백
        self._on_pause: Optional[Callable] = None
        self._on_resume: Optional[Callable] = None
        self._feedback = None  # TradeFeedback 인스턴스 (외부 주입)
        # 솔라나 봇 참조 (main.py에서 주입)
        self.solana_engines: dict = {}
        self.polymarket_engine = None

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def set_callbacks(
        self,
        on_pause: Optional[Callable] = None,
        on_resume: Optional[Callable] = None,
    ):
        """외부 콜백 등록"""
        self._on_pause = on_pause
        self._on_resume = on_resume

    # ── 메시지 전송 ────────────────────────────────────────

    async def send(self, text: str, parse_mode: str = "HTML"):
        """텔레그램 메시지 전송"""
        if not self.is_configured:
            logger.debug(f"Telegram 미설정, 메시지 스킵: {text[:50]}...")
            return

        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{self._base_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": parse_mode,
                        "disable_web_page_preview": True,
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                )
        except Exception as e:
            logger.error(f"Telegram 전송 실패: {e}")

    # ── 알림 포맷터 ────────────────────────────────────────

    async def notify_trade_open(self, record_dict: dict):
        """매매 진입 알림"""
        j = record_dict.get("judgment", {})
        r = record_dict.get("risk_review", {})
        consensus = record_dict.get("signal_consensus", {})

        signal = j.get("signal", "?")
        emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⏸"}.get(signal, "❓")

        entry = j.get('entry_price') or 0
        sl = j.get('stop_loss') or 0
        tp = j.get('take_profit') or 0
        conf = j.get('confidence') or 0
        pos_pct = j.get('position_size_pct') or 0
        order = record_dict.get("order", {})
        lev = record_dict.get("leverage") or order.get("leverage", 0)
        exposure = record_dict.get("exposure", 0)

        # 실제 주문 정보가 있으면 사용
        if order:
            entry = order.get("price") or entry
            sl = order.get("stop_loss") or sl
            tp = order.get("take_profit") or tp

        text = f"""{emoji} <b>포지션 진입</b>

<b>신호:</b> {signal}
<b>확신도:</b> {conf:.0%}
<b>레버리지:</b> {lev}x
<b>진입가:</b> ${entry:,.2f}
<b>마진:</b> ${order.get('usdt_amount', 0) or 0:,.2f}
<b>노출:</b> ${exposure:,.2f}
<b>손절가:</b> ${sl:,.2f}
<b>익절가:</b> ${tp:,.2f}

<b>투표:</b> BUY {consensus.get('BUY', 0)} / SELL {consensus.get('SELL', 0)} / HOLD {consensus.get('HOLD', 0)}
<b>리스크:</b> {'✅ 승인' if r.get('approved') else '❌ 거부'}

<i>{(j.get('reasoning') or '')[:200]}</i>"""

        await self.send(text)

    async def notify_trade_close(self, cycle_id: str, pnl_pct: float, pnl_usd: float):
        """매매 청산 알림"""
        emoji = "💰" if pnl_pct > 0 else "💸"
        color = "🟢" if pnl_pct > 0 else "🔴"

        text = f"""{emoji} <b>포지션 청산</b>

{color} <b>PnL:</b> {pnl_pct:+.2f}% (${pnl_usd:+,.2f})
<b>Cycle:</b> <code>{cycle_id[:12]}</code>"""

        await self.send(text)

    async def notify_evolution(self, evo_result: dict):
        """진화 사이클 결과 알림"""
        changes = evo_result.get("weight_changes", [])
        isolations = evo_result.get("isolations", [])
        reactivations = evo_result.get("reactivations", [])

        parts = ["🧬 <b>진화 사이클 완료</b>\n"]

        if changes:
            parts.append(f"<b>가중치 변경:</b> {len(changes)}건")
            for c in changes[:5]:
                arrow = "↑" if c["delta"] > 0 else "↓"
                parts.append(f"  {c['name']}: {c['old_weight']:.2f} → {c['new_weight']:.2f} {arrow}")

        if isolations:
            parts.append(f"\n🚫 <b>격리:</b>")
            for i in isolations:
                parts.append(f"  {i['name']} — {i['reason']}")

        if reactivations:
            parts.append(f"\n✅ <b>복귀:</b>")
            for r in reactivations:
                parts.append(f"  {r['name']} — {r['reason']}")

        if not changes and not isolations and not reactivations:
            parts.append("변경 사항 없음")

        await self.send("\n".join(parts))

    async def notify_agent_recruited(self, agent_name: str, specialty: str):
        """신규 에이전트 영입 알림"""
        text = f"""🆕 <b>신규 에이전트 영입</b>

<b>이름:</b> {agent_name}
<b>전문분야:</b> {specialty}
<b>상태:</b> 수습 (PROBATION)"""

        await self.send(text)

    async def notify_error(self, error_msg: str):
        """에러 알림"""
        await self.send(f"⚠️ <b>시스템 에러</b>\n\n<code>{error_msg[:500]}</code>")

    # ── 제어 명령어 폴링 ───────────────────────────────────

    async def start_polling(self):
        """텔레그램 명령어 폴링 시작"""
        if not self.is_configured:
            logger.info("Telegram 미설정, 폴링 건너뜀")
            return

        self._running = True
        logger.info("Telegram 명령어 폴링 시작")

        while self._running:
            try:
                await self._poll_updates()
            except Exception as e:
                logger.error(f"Telegram 폴링 에러: {e}")
            await asyncio.sleep(2)

    async def stop_polling(self):
        """폴링 중지"""
        self._running = False

    async def _poll_updates(self):
        """새 메시지 확인 및 명령 처리"""
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.get(
                    f"{self._base_url}/getUpdates",
                    params={
                        "offset": self._last_update_id + 1,
                        "timeout": 1,
                    },
                    timeout=aiohttp.ClientTimeout(total=5),
                )
                data = await resp.json()
        except Exception:
            return

        if not data.get("ok"):
            return

        for update in data.get("result", []):
            self._last_update_id = update["update_id"]
            message = update.get("message", {})
            text = message.get("text", "").strip()
            chat_id = str(message.get("chat", {}).get("id", ""))

            # 등록된 chat_id만 처리
            if chat_id != self.chat_id:
                continue

            # 명령어 처리
            cmd = text.split()[0].lower() if text else ""
            handler = self._commands.get(cmd)
            if handler:
                await handler(text)

    # ── 명령 핸들러 ────────────────────────────────────────

    async def _cmd_status(self, _: str):
        """시스템 상태"""
        summary = self.registry.get_summary()
        overall = self.db.get_overall_stats()

        total_trades = overall.get("total_trades", 0)
        win_rate = 0
        if total_trades and overall.get("wins"):
            win_rate = overall["wins"] / total_trades

        text = f"""📊 <b>시스템 상태</b>

<b>에이전트:</b> {summary['total_agents']}개 (활성 {summary['active']}, 격리 {summary['isolated']}, 수습 {summary['probation']})
<b>총 거래:</b> {total_trades}회
<b>승률:</b> {win_rate:.0%}
<b>누적 PnL:</b> {overall.get('total_pnl', 0):.2f}%
<b>최고 수익:</b> {overall.get('best_trade', 0):.2f}%
<b>최악 손실:</b> {overall.get('worst_trade', 0):.2f}%"""

        await self.send(text)

    async def _cmd_agents(self, _: str):
        """에이전트 목록"""
        agents = self.registry.get_all()
        status_emoji = {
            "active": "🟢",
            "isolated": "🔴",
            "probation": "🟡",
            "disabled": "⚫",
        }

        lines = ["👥 <b>에이전트 목록</b>\n"]
        for a in agents:
            emoji = status_emoji.get(a.status.value, "❓")
            role_tag = f"[{a.role.value}]" if a.role.value != "analyst" else ""
            lines.append(
                f"{emoji} <b>{a.name}</b> {role_tag} "
                f"— W:{a.weight:.2f} WR:{a.win_rate:.0%}"
            )

        await self.send("\n".join(lines))

    async def _cmd_performance(self, _: str):
        """에이전트 성과 리포트"""
        reports = self.tracker.analyze_all()
        grade_emoji = {"S": "🏆", "A": "🥇", "B": "🥈", "C": "🥉", "D": "⚠️", "F": "❌", "N": "🆕"}

        lines = ["📈 <b>에이전트 성과</b>\n"]
        for r in sorted(reports, key=lambda x: x.win_rate, reverse=True):
            emoji = grade_emoji.get(r.grade, "❓")
            lines.append(
                f"{emoji} <b>{r.name}</b> [{r.grade}] "
                f"승률 {r.win_rate:.0%} (최근 {r.recent_win_rate:.0%}) "
                f"W:{r.weight:.2f}"
            )

        await self.send("\n".join(lines))

    async def _cmd_weights(self, _: str):
        """정규화된 가중치"""
        weights = self.registry.get_normalized_weights()
        if not weights:
            await self.send("⚖️ 활성 에이전트 없음")
            return

        lines = ["⚖️ <b>에이전트 가중치</b> (합=100%)\n"]
        for aid, w in sorted(weights.items(), key=lambda x: x[1], reverse=True):
            agent = self.registry.get(aid)
            name = agent.name if agent else aid
            bar = "█" * int(w * 30)
            lines.append(f"<code>{name:12s}</code> {bar} {w:.0%}")

        await self.send("\n".join(lines))

    async def _cmd_evolution_status(self, _: str):
        """진화 엔진 상태"""
        text = f"""🧬 <b>진화 엔진</b>

<b>자동 조정 주기:</b> 매 {self.settings.weight_update_interval}거래
<b>격리 기준:</b> 승률 {self.settings.isolation_win_rate:.0%} 이하
<b>복귀 기준:</b> 승률 {self.settings.probation_win_rate:.0%} 이상
<b>수습 최소 거래:</b> {self.settings.probation_min_trades}회"""

        await self.send(text)

    async def _cmd_pause(self, _: str):
        """시스템 일시 중지"""
        if self._on_pause:
            self._on_pause()
        await self.send("⏸ <b>시스템 일시 중지</b>")

    async def _cmd_resume(self, _: str):
        """시스템 재개"""
        if self._on_resume:
            self._on_resume()
        await self.send("▶️ <b>시스템 재개</b>")

    async def _cmd_help(self, _: str):
        """명령어 목록"""
        text = """📋 <b>사용 가능한 명령어</b>

<b>━━ 솔라나 봇 ━━</b>
<code>!positions</code> — 현재 보유 포지션 (실시간 PnL)
<code>!stats</code> — 봇별 누적 매매 통계 (7일)
<code>!wallets</code> — 추적 지갑 목록 + 승률

<b>━━ OKX 시스템 ━━</b>
<code>!status</code> — 시스템 상태
<code>!agents</code> — 에이전트 목록
<code>!performance</code> — 성과 리포트
<code>!weights</code> — 에이전트 가중치
<code>!evolution</code> — 진화 엔진 상태
<code>!report</code> — 트레이딩 성과 리포트
<code>!pause</code> — 시스템 일시 중지
<code>!resume</code> — 시스템 재개

한국어: <code>!포지션</code>, <code>!지갑</code>"""

        await self.send(text)

    # ────────────────────────────────────────────
    # 솔라나 봇 명령어
    # ────────────────────────────────────────────

    async def _cmd_positions(self, _: str):
        """솔라나 봇 현재 보유 포지션 (실시간 PnL)"""
        if not self.solana_engines:
            await self.send("🟡 솔라나 봇 비활성")
            return

        bot_emoji = {
            "smart_money": "🐋",
            "pumpfun_sniper": "💎",
            "momentum_social": "📈",
        }
        bot_name = {
            "smart_money": "SmartMoney",
            "pumpfun_sniper": "PumpFun",
            "momentum_social": "Momentum",
        }

        sections = []
        total_positions = 0
        total_invested_sol = 0.0
        total_current_value = 0.0

        for bot_key, engine in self.solana_engines.items():
            positions = getattr(engine, "positions", {}) or {}
            if not positions:
                continue

            emoji = bot_emoji.get(bot_key, "🤖")
            name = bot_name.get(bot_key, bot_key)
            sec = [f"\n<b>{emoji} {name}</b> ({len(positions)}개)"]

            for mint, pos in positions.items():
                symbol = pos.get("symbol", "?")
                entry_sol = pos.get("entry_sol", 0)
                peak = pos.get("peak_pnl_pct", 0)

                # 현재 가격으로 PnL 계산
                pnl_pct = 0.0
                current_value_sol = entry_sol  # 폴백
                try:
                    current_price = await engine._get_token_price_sol(
                        mint, pos.get("decimals", 9)
                    )
                    entry_price = pos.get("entry_price_sol", 0)
                    if current_price and entry_price > 0:
                        pnl_pct = (current_price - entry_price) / entry_price * 100
                        current_value_sol = (
                            pos.get("token_amount_raw", 0)
                            * current_price
                            / (10 ** pos.get("decimals", 9))
                        )
                        # peak 갱신
                        if pnl_pct > peak:
                            peak = pnl_pct
                except Exception:
                    pass

                pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"

                # 단계 진행 표시 (문샷 친화 — +50/+200/+500)
                stages = []
                if pos.get("stage_50_done") or (pos.get("tp_done") and len(pos["tp_done"])>0 and pos["tp_done"][0]):
                    stages.append("✅+50%")
                if pos.get("stage_200_done") or (pos.get("tp_done") and len(pos["tp_done"])>1 and pos["tp_done"][1]):
                    stages.append("✅+200%")
                if pos.get("stage_500_done") or (pos.get("tp_done") and len(pos["tp_done"])>2 and pos["tp_done"][2]):
                    stages.append("✅+500%")
                if pos.get("trailing_active"):
                    stages.append("🎯트레일링")
                stages_str = " ".join(stages) if stages else "초기"

                sec.append(
                    f"  {pnl_emoji} <b>${symbol}</b> "
                    f"{pnl_pct:+.1f}% (peak +{peak:.0f}%)\n"
                    f"     진입: {entry_sol:.4f} SOL → 현재: {current_value_sol:.4f} SOL\n"
                    f"     단계: {stages_str}\n"
                    f"     <code>{mint[:8]}...{mint[-6:]}</code>"
                )

                total_positions += 1
                total_invested_sol += entry_sol
                total_current_value += current_value_sol

            sections.append("\n".join(sec))

        if total_positions == 0:
            await self.send(
                "📊 <b>현재 보유 포지션</b>\n\n"
                "<i>보유 포지션 없음</i>"
            )
            return

        # 총합 PnL
        total_pnl_pct = (
            (total_current_value - total_invested_sol) / total_invested_sol * 100
            if total_invested_sol > 0 else 0
        )
        total_emoji = "🟢" if total_pnl_pct >= 0 else "🔴"

        header = (
            f"📊 <b>현재 보유 포지션</b> ({total_positions}개)\n\n"
            f"💼 <b>총 투입:</b> {total_invested_sol:.4f} SOL\n"
            f"💰 <b>현재 가치:</b> {total_current_value:.4f} SOL\n"
            f"{total_emoji} <b>전체 PnL:</b> {total_pnl_pct:+.2f}%"
        )

        await self.send(header + "\n" + "\n".join(sections))

    async def _cmd_solana_stats(self, _: str):
        """솔라나 봇 누적 통계 (7일)"""
        from datetime import timedelta
        week_ago = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")

        bots = [
            ("smart_money_trades", "🐋 SmartMoney"),
            ("pumpfun_trades", "💎 PumpFun"),
            ("momentum_social_trades", "📈 Momentum"),
        ]

        lines = ["📈 <b>솔라나 봇 7일 통계</b>\n"]

        for table, name in bots:
            try:
                buys = self.db.conn.execute(
                    f"SELECT COUNT(*) FROM {table} "
                    f"WHERE side='BUY' AND date(timestamp) >= ?",
                    (week_ago,),
                ).fetchone()[0]
                sells = self.db.conn.execute(
                    f"SELECT COUNT(*), SUM(pnl_pct), AVG(pnl_pct), "
                    f"SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) "
                    f"FROM {table} WHERE side='SELL' AND date(timestamp) >= ?",
                    (week_ago,),
                ).fetchone()

                sell_count = sells[0] or 0
                total_pnl = sells[1] or 0
                avg_pnl = sells[2] or 0
                wins = sells[3] or 0
                win_rate = (wins / sell_count * 100) if sell_count > 0 else 0

                lines.append(
                    f"<b>{name}</b>\n"
                    f"  매수: {buys}건 / 매도: {sell_count}건\n"
                    f"  승률: {win_rate:.0f}% ({wins}/{sell_count})\n"
                    f"  누적 PnL: {total_pnl:+.1f}% / 평균: {avg_pnl:+.1f}%"
                )
            except Exception as e:
                lines.append(f"<b>{name}</b>: 데이터 없음")

        await self.send("\n\n".join(lines))

    async def _cmd_wallets(self, _: str):
        """추적 지갑 목록 + 승률"""
        try:
            from solana_bot.smart_money_bot.wallets import TRACKED_WALLETS
        except Exception as e:
            await self.send(f"⚠️ 추적 지갑 로드 실패: {e}")
            return

        if not TRACKED_WALLETS:
            await self.send("🟡 추적 지갑 없음")
            return

        active = [w for w in TRACKED_WALLETS if w.get("active", False)]
        inactive = [w for w in TRACKED_WALLETS if not w.get("active", False)]

        # 승률 높은 순 정렬
        active.sort(key=lambda w: w.get("win_rate", 0), reverse=True)

        lines = [
            f"🐋 <b>추적 지갑</b> (활성 {len(active)}/총 {len(TRACKED_WALLETS)})\n"
        ]

        for w in active[:20]:  # 최대 20개 표시
            wr = w.get("win_rate", 0)
            weight = w.get("weight", 1.0)
            tag = w.get("tag", "")
            addr = w.get("address", "")
            wr_emoji = "🟢" if wr >= 0.6 else ("🟡" if wr >= 0.5 else "🔴")
            lines.append(
                f"{wr_emoji} <code>{addr[:8]}..{addr[-4:]}</code> "
                f"WR:{wr:.0%} W:{weight:.2f} <i>[{tag}]</i>"
            )

        if len(active) > 20:
            lines.append(f"\n<i>... 외 {len(active)-20}개 더</i>")

        if inactive:
            lines.append(f"\n⚫ <b>비활성:</b> {len(inactive)}개 (승률 40% 미달)")

        await self.send("\n".join(lines))

    async def _cmd_report(self, _: str):
        """트레이딩 성과 리포트"""
        if self._feedback:
            await self.send(self._feedback.get_telegram_report())
        else:
            await self.send("📊 아직 거래 데이터가 없습니다.")

    # ── 일간/주간 리포트 ───────────────────────────────────

    async def send_daily_report(self):
        """일간 리포트"""
        episodes = self.db.get_recent_episodes(limit=24)
        if not episodes:
            return

        today_trades = [e for e in episodes if e.get("pnl_pct") is not None]
        if not today_trades:
            return

        wins = sum(1 for t in today_trades if t["pnl_pct"] > 0)
        total_pnl = sum(t["pnl_pct"] for t in today_trades)
        best = max(t["pnl_pct"] for t in today_trades)
        worst = min(t["pnl_pct"] for t in today_trades)

        text = f"""📅 <b>일간 리포트</b> ({datetime.utcnow().strftime('%Y-%m-%d')})

<b>거래:</b> {len(today_trades)}건
<b>승률:</b> {wins}/{len(today_trades)} ({wins/len(today_trades):.0%})
<b>일간 PnL:</b> {total_pnl:+.2f}%
<b>최고:</b> {best:+.2f}%
<b>최악:</b> {worst:+.2f}%"""

        await self.send(text)

    async def send_weekly_report(self):
        """주간 리포트"""
        episodes = self.db.get_recent_episodes(limit=200)
        if not episodes:
            return

        closed = [e for e in episodes if e.get("pnl_pct") is not None]
        if not closed:
            return

        wins = sum(1 for t in closed if t["pnl_pct"] > 0)
        total_pnl = sum(t["pnl_pct"] for t in closed)

        # 에이전트 성과 요약
        reports = self.tracker.analyze_all()
        top = sorted(reports, key=lambda r: r.win_rate, reverse=True)[:3]
        bottom = sorted(reports, key=lambda r: r.win_rate)[:3]

        lines = [
            f"📊 <b>주간 리포트</b>\n",
            f"<b>총 거래:</b> {len(closed)}건",
            f"<b>승률:</b> {wins/len(closed):.0%}",
            f"<b>주간 PnL:</b> {total_pnl:+.2f}%\n",
            f"<b>TOP 3 에이전트:</b>",
        ]
        for r in top:
            lines.append(f"  🏆 {r.name} — {r.win_rate:.0%}")

        lines.append(f"\n<b>BOTTOM 3:</b>")
        for r in bottom:
            lines.append(f"  ⚠️ {r.name} — {r.win_rate:.0%}")

        await self.send("\n".join(lines))
