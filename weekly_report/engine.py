"""
WeeklyReportEngine — 매주 일요일 21시 KST 통합 리포트

내용:
1. 한 주 시장 요약 (BTC/ETH 변화, 공포탐욕)
2. 봇별 주간 성과 (거래 수, 승률, PnL)
3. 베스트/워스트 거래
4. 자기학습 결과 (가중치 변화, 비활성화된 지갑)
5. 새로 발굴한 스마트머니 지갑
6. 다음 주 전망 + 액션 아이템
"""

import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


class WeeklyReportEngine:
    """주간 통합 리포트"""

    def __init__(
        self, settings, telegram, db,
        polymarket_engine=None, solana_engines=None,
        wallet_discovery=None,
    ):
        self.settings = settings
        self.telegram = telegram
        self.db = db
        self.polymarket_engine = polymarket_engine
        self.solana_engines = solana_engines or {}
        self.wallet_discovery = wallet_discovery
        self._last_run_date = None

    # ──────────────────────────────────────────────
    # 트리거
    # ──────────────────────────────────────────────

    async def should_run_now(self) -> bool:
        """매주 일요일 21시 KST에 실행"""
        if not getattr(self.settings, "weekly_report_enabled", True):
            return False

        now = datetime.now(KST)
        target_hour = getattr(self.settings, "weekly_report_hour_kst", 21)

        # 일요일(weekday=6) 21시 + 오늘 안 돌았으면
        if now.weekday() == 6 and now.hour == target_hour and self._last_run_date != now.date():
            return True
        return False

    async def generate_and_send(self):
        """리포트 생성 + 발송"""
        logger.info("📊 주간 리포트 시작...")

        try:
            # 1. 봇별 주간 통계 수집
            stats = await self._collect_all_stats()

            # 2. 자동 지갑 발굴 (옵션)
            discovery_result = None
            if self.wallet_discovery:
                try:
                    logger.info("🔍 스마트머니 지갑 자동 발굴 중...")
                    discovery_result = await self.wallet_discovery.discover_and_add()
                except Exception as e:
                    logger.warning(f"지갑 발굴 실패: {e}")

            # 3. 리포트 포맷
            message = self._format_report(stats, discovery_result)

            # 4. 발송
            await self.telegram.send(message)
            self._last_run_date = datetime.now(KST).date()
            logger.info("✅ 주간 리포트 발송 완료")
        except Exception as e:
            logger.error(f"주간 리포트 에러: {e}", exc_info=True)

    # ──────────────────────────────────────────────
    # 통계 수집
    # ──────────────────────────────────────────────

    async def _collect_all_stats(self) -> dict:
        """7일간 봇별 통계"""
        now = datetime.now(KST)
        week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")

        stats = {
            "polymarket": {},
            "smart_money": {},
            "pumpfun": {},
            "momentum": {},
            "okx": {},
        }

        # Polymarket
        try:
            row = self.db.conn.execute(
                """SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN final_action='EXECUTED' THEN 1 ELSE 0 END) as exec,
                    AVG(ev_pct) as avg_ev
                FROM polymarket_trades WHERE date(timestamp) >= ?""",
                (week_ago,),
            ).fetchone()
            if row:
                stats["polymarket"] = {
                    "total": row["total"] or 0,
                    "executed": row["exec"] or 0,
                    "avg_ev": float(row["avg_ev"] or 0),
                }
        except Exception:
            pass

        # 솔라나 봇별
        bot_tables = {
            "smart_money": "smart_money_trades",
            "pumpfun": "pumpfun_trades",
            "momentum": "momentum_social_trades",
        }

        for key, table in bot_tables.items():
            try:
                # 매도 거래 (PnL 계산용)
                rows = self.db.conn.execute(
                    f"""SELECT pnl_pct FROM {table}
                        WHERE date(timestamp) >= ? AND side='SELL'""",
                    (week_ago,),
                ).fetchall()

                wins = sum(1 for r in rows if (r["pnl_pct"] or 0) > 0)
                losses = sum(1 for r in rows if (r["pnl_pct"] or 0) <= 0)
                total = wins + losses
                pnls = [r["pnl_pct"] or 0 for r in rows]

                # 매수 거래 수
                buy_rows = self.db.conn.execute(
                    f"""SELECT COUNT(*) as cnt FROM {table}
                        WHERE date(timestamp) >= ? AND side='BUY'""",
                    (week_ago,),
                ).fetchone()

                # 베스트/워스트
                best = max(pnls) if pnls else 0
                worst = min(pnls) if pnls else 0

                stats[key] = {
                    "buys": buy_rows["cnt"] if buy_rows else 0,
                    "sells": total,
                    "wins": wins,
                    "losses": losses,
                    "win_rate": (wins / total) if total > 0 else 0,
                    "total_pnl_pct": sum(pnls),
                    "avg_pnl_pct": (sum(pnls) / total) if total > 0 else 0,
                    "best_trade": best,
                    "worst_trade": worst,
                }
            except Exception as e:
                logger.debug(f"{key} 통계 실패: {e}")

        # OKX (활성 시만)
        if self.settings.okx_trading_enabled:
            try:
                row = self.db.conn.execute(
                    """SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END) as wins,
                        SUM(CASE WHEN pnl_pct < 0 THEN 1 ELSE 0 END) as losses,
                        SUM(pnl_pct) as total_pnl
                    FROM episodes
                    WHERE date(created_at) >= ? AND final_action='EXECUTED'""",
                    (week_ago,),
                ).fetchone()
                if row and row["total"]:
                    stats["okx"] = {
                        "total": row["total"],
                        "wins": row["wins"] or 0,
                        "losses": row["losses"] or 0,
                        "total_pnl_pct": row["total_pnl"] or 0,
                    }
            except Exception:
                pass

        return stats

    # ──────────────────────────────────────────────
    # 포맷
    # ──────────────────────────────────────────────

    def _format_report(self, stats: dict, discovery: Optional[dict]) -> str:
        """텔레그램 HTML 포맷"""
        now = datetime.now(KST)
        date_str = now.strftime("%m월 %d일")
        week_start = (now - timedelta(days=7)).strftime("%m/%d")

        lines = [
            f"📊 <b>주간 통합 리포트</b>",
            f"<i>{week_start} ~ {now.strftime('%m/%d')} (일요일 {now.strftime('%H:%M')})</i>",
            "",
        ]

        # ── 봇별 성과 ──────────────────────────
        bot_emojis = {
            "polymarket": "🌤️",
            "smart_money": "🐋",
            "pumpfun": "💎",
            "momentum": "📈",
            "okx": "📊",
        }
        bot_names = {
            "polymarket": "Polymarket",
            "smart_money": "SmartMoney",
            "pumpfun": "PumpFun",
            "momentum": "Momentum",
            "okx": "OKX 선물",
        }

        # 통합 PnL 계산
        total_pnl = 0
        active_bots = 0

        # Polymarket
        pm = stats.get("polymarket", {})
        if pm.get("total", 0) > 0:
            lines.append("🌤️ <b>Polymarket 날씨봇</b>")
            lines.append(f"  거래 시도: {pm.get('total', 0)}건")
            lines.append(f"  실행: {pm.get('executed', 0)}건")
            lines.append(f"  평균 EV: +{pm.get('avg_ev', 0):.2f}%")
            lines.append("")
            active_bots += 1

        # 솔라나 봇 3개
        for key in ["smart_money", "pumpfun", "momentum"]:
            s = stats.get(key, {})
            if s.get("buys", 0) == 0 and s.get("sells", 0) == 0:
                continue

            emoji = bot_emojis[key]
            name = bot_names[key]
            sells = s.get("sells", 0)
            wr = s.get("win_rate", 0) * 100
            total_pnl_bot = s.get("total_pnl_pct", 0)
            total_pnl += total_pnl_bot
            active_bots += 1

            lines.append(f"{emoji} <b>{name}</b>")
            lines.append(f"  매수: {s.get('buys', 0)}건 / 매도: {sells}건")
            if sells > 0:
                lines.append(
                    f"  성적: ✅{s.get('wins', 0)} / ❌{s.get('losses', 0)} "
                    f"(승률 {wr:.0f}%)"
                )
                lines.append(f"  주간 PnL: {total_pnl_bot:+.2f}%")
                lines.append(f"  평균 거래: {s.get('avg_pnl_pct', 0):+.2f}%")

                best = s.get("best_trade", 0)
                worst = s.get("worst_trade", 0)
                if abs(best) > 0 or abs(worst) > 0:
                    lines.append(f"  베스트: {best:+.1f}% / 워스트: {worst:+.1f}%")
            lines.append("")

        # OKX (활성 시만)
        okx = stats.get("okx", {})
        if okx.get("total", 0) > 0:
            wr = (okx["wins"] / okx["total"] * 100) if okx["total"] > 0 else 0
            lines.append(f"📊 <b>OKX 선물</b>")
            lines.append(f"  거래: {okx['total']}건 (✅{okx.get('wins', 0)}/❌{okx.get('losses', 0)})")
            lines.append(f"  주간 PnL: {okx.get('total_pnl_pct', 0):+.2f}%")
            lines.append("")

        # ── 통합 요약 ─────────────────────────
        lines.append("📈 <b>주간 요약</b>")
        lines.append(f"  활성 봇: {active_bots}개")
        lines.append(f"  통합 PnL: {total_pnl:+.2f}%")
        lines.append("")

        # ── 자동 발굴된 지갑 ───────────────────
        if discovery and discovery.get("added", 0) > 0:
            lines.append(f"🔍 <b>스마트머니 자동 발굴</b>")
            lines.append(f"  검사: {discovery.get('checked', 0)}개 / 통과: {discovery.get('qualified', 0)}개")
            lines.append(f"  ✅ {discovery.get('added', 0)}개 신규 추가")
            for w in discovery.get("new_wallets", [])[:3]:
                stats_w = w.get("stats", {})
                lines.append(
                    f"  • <code>{w['address'][:8]}...</code> "
                    f"(승률 {stats_w.get('win_rate', 0)*100:.0f}%, "
                    f"PnL {stats_w.get('avg_pnl_pct', 0):+.0f}%)"
                )
            lines.append("")

        # ── 다음 주 추천 ───────────────────────
        recommendations = self._generate_recommendations(stats)
        if recommendations:
            lines.append("💡 <b>다음 주 액션</b>")
            for r in recommendations:
                lines.append(f"• {r}")

        return "\n".join(lines)

    def _generate_recommendations(self, stats: dict) -> list[str]:
        """봇별 성과 기반 추천"""
        recs = []

        # 솔라나 봇 분석
        for key in ["smart_money", "pumpfun", "momentum"]:
            s = stats.get(key, {})
            sells = s.get("sells", 0)
            if sells < 3:
                continue

            wr = s.get("win_rate", 0)
            pnl = s.get("total_pnl_pct", 0)
            name = {"smart_money": "🐋 SmartMoney", "pumpfun": "💎 PumpFun", "momentum": "📈 Momentum"}[key]

            if wr >= 0.6 and pnl > 30:
                recs.append(f"{name}: 승률 {wr*100:.0f}% / PnL {pnl:+.0f}% — Live 전환 검토")
            elif wr < 0.3 or pnl < -30:
                recs.append(f"{name}: 부진 (승률 {wr*100:.0f}%, PnL {pnl:+.0f}%) — 파라미터 조정 필요")
            elif wr >= 0.5 and pnl > 0:
                recs.append(f"{name}: 안정적 (Paper 계속 검증)")

        # 거래 활동 적은 봇
        for key in ["smart_money", "pumpfun", "momentum"]:
            s = stats.get(key, {})
            if s.get("buys", 0) < 3:
                name = {"smart_money": "🐋 SmartMoney", "pumpfun": "💎 PumpFun", "momentum": "📈 Momentum"}[key]
                recs.append(f"{name}: 활동 적음 — 진입 기준 완화 검토")

        if not recs:
            recs.append("모든 봇 안정적 — 검증 계속 진행")

        return recs[:5]  # 최대 5개
