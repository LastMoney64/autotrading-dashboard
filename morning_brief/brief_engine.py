"""
MorningBriefEngine — 매일 아침 7시 자동 시장 브리핑

데이터 수집 → 분석 → 텔레그램 포맷 → 발송
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from morning_brief.collectors import (
    fetch_fear_greed_index,
    fetch_price_and_funding,
    fetch_onchain_flows,
    fetch_whale_activity,
    fetch_trending_memes,
    fetch_bot_status,
    fetch_polymarket_status,
    fetch_solana_status,
)

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


class MorningBriefEngine:
    """매일 아침 7시 KST 자동 브리핑"""

    def __init__(self, settings, okx, feedback, db, telegram,
                 polymarket_engine=None, solana_engines=None):
        self.settings = settings
        self.okx = okx
        self.feedback = feedback
        self.db = db
        self.telegram = telegram
        self.polymarket_engine = polymarket_engine
        self.solana_engines = solana_engines or {}
        self._last_run_date = None

    async def should_run_now(self) -> bool:
        """현재 7시 KST이고 오늘 아직 안 돌았으면 True"""
        if not self.settings.morning_brief_enabled:
            return False

        now_kst = datetime.now(KST)
        target_hour = self.settings.morning_brief_hour_kst

        # 7시~7시59분 사이, 오늘 아직 안 돌았으면 실행
        if now_kst.hour == target_hour and self._last_run_date != now_kst.date():
            return True
        return False

    async def generate_and_send(self):
        """브리핑 생성 + 발송"""
        logger.info("🌅 모닝 브리프 시작...")

        # 데이터 병렬 수집 (빠르게)
        results = await asyncio.gather(
            fetch_fear_greed_index(),
            fetch_price_and_funding(self.okx),
            fetch_onchain_flows(),
            fetch_whale_activity(self.settings.etherscan_api_key),
            fetch_trending_memes(),
            fetch_bot_status(self.db, self.feedback, self.okx),
            fetch_polymarket_status(self.db, self.polymarket_engine),
            fetch_solana_status(self.db, self.solana_engines),
            return_exceptions=True,
        )

        fg, price, onchain, whales, memes, bot, polymarket, solana = results

        # 예외면 빈 dict로 대체
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning(f"수집기 {i} 예외: {r}")

        message = self._format_brief(
            fg if not isinstance(fg, Exception) else {},
            price if not isinstance(price, Exception) else {},
            onchain if not isinstance(onchain, Exception) else {},
            whales if not isinstance(whales, Exception) else {},
            memes if not isinstance(memes, Exception) else {},
            bot if not isinstance(bot, Exception) else {},
            polymarket if not isinstance(polymarket, Exception) else {},
            solana if not isinstance(solana, Exception) else {},
        )

        await self.telegram.send(message)
        self._last_run_date = datetime.now(KST).date()
        logger.info("✅ 모닝 브리프 발송 완료")

    def _format_brief(self, fg, price, onchain, whales, memes, bot,
                      polymarket=None, solana=None) -> str:
        """텔레그램 HTML 포맷"""
        now_kst = datetime.now(KST)
        date_str = now_kst.strftime("%m월 %d일 %H:%M KST")

        lines = [f"📊 <b>모닝 브리프 — {date_str}</b>", ""]

        # ── 1. 가격 ────────────────────────────────
        lines.append("💰 <b>가격 (24H)</b>")
        for sym_key, emoji in [("BTC", "🟠"), ("ETH", "🔵")]:
            p = price.get(sym_key, {}) if isinstance(price, dict) else {}
            if p.get("price"):
                change = p.get("change_24h", 0) or 0
                arrow = "📈" if change > 0 else "📉"
                lines.append(
                    f"{emoji} {sym_key}: <b>${p['price']:,.0f}</b> "
                    f"{arrow} {change:+.2f}%"
                )
            else:
                lines.append(f"{emoji} {sym_key}: 데이터 없음")
        lines.append("")

        # ── 2. 심리 ────────────────────────────────
        lines.append("😱 <b>시장 심리</b>")
        if fg.get("value") is not None:
            v = fg["value"]
            kr = fg.get("kr_class", "")
            w = fg.get("week_ago", v)
            lines.append(f"공포탐욕지수: <b>{v}</b> ({kr})")
            lines.append(f"일주일 전: {w} → 변화: {v - w:+d}")
            if fg.get("interpretation"):
                lines.append(f"💡 <i>{fg['interpretation']}</i>")
        else:
            lines.append("데이터 수집 실패")
        lines.append("")

        # ── 3. 펀딩비 (OKX + Bitget + Hyperliquid) ────────
        lines.append("🔥 <b>펀딩비 크로스</b>")
        for sym_key in ["BTC", "ETH"]:
            p = price.get(sym_key, {}) if isinstance(price, dict) else {}
            okx_f = p.get("funding_okx", 0) or 0
            bg_f = p.get("funding_bitget", 0) or 0
            hl_f = p.get("funding_hyperliquid", 0) or 0

            # 유효한 값만 평균
            valid = [x for x in [okx_f, bg_f, hl_f] if abs(x) > 1e-8]
            avg_f = (sum(valid) / len(valid) * 100) if valid else 0

            if abs(avg_f) > 0.05:
                signal = "⚠️ 롱 과열" if avg_f > 0.05 else "⚠️ 숏 과열"
            else:
                signal = "중립"

            lines.append(
                f"<b>{sym_key}</b>: OKX {okx_f*100:.3f}% / "
                f"Bitget {bg_f*100:.3f}% / HL {hl_f*100:.3f}% → {signal}"
            )
        lines.append("")

        # ── 4. 온체인 ──────────────────────────────
        lines.append("⛓️ <b>온체인</b>")
        if onchain.get("stablecoin_mc_usd"):
            mc = onchain["stablecoin_mc_usd"] / 1e9
            sd24 = onchain.get("stablecoin_change_24h_pct", 0)
            arrow = "📈" if sd24 > 0 else "📉" if sd24 < 0 else ""
            lines.append(f"스테이블코인: ${mc:,.1f}B {arrow} {sd24:+.2f}% (24H)")
        if onchain.get("defi_tvl_usd"):
            tvl = onchain["defi_tvl_usd"] / 1e9
            d24 = onchain.get("tvl_change_24h_pct", 0)
            d7 = onchain.get("tvl_change_7d_pct", 0)
            lines.append(f"DeFi TVL: ${tvl:,.1f}B (24H {d24:+.2f}% / 7D {d7:+.2f}%)")
        lines.append("")

        # ── 5. 고래 ────────────────────────────────
        lines.append("🐋 <b>고래 동향</b>")
        if whales.get("whales"):
            for w in whales["whales"][:3]:
                lines.append(
                    f"• {w['name']}: {w['eth_balance']:,.0f} ETH"
                )
        else:
            lines.append("데이터 수집 중")
        lines.append("")

        # ── 6. 밈코인 트렌딩 ──────────────────────
        lines.append("💎 <b>솔라나 밈코인 TOP 5</b>")
        if memes.get("trending"):
            for i, m in enumerate(memes["trending"][:5], 1):
                ch = m.get("change_24h", 0)
                vol = m.get("volume_24h", 0) / 1000
                arrow = "🚀" if ch > 50 else "📈" if ch > 0 else "📉"
                lines.append(
                    f"{i}. <b>${m['name']}</b> {arrow} "
                    f"{ch:+.1f}% | ${vol:,.0f}K vol"
                )
        else:
            lines.append("데이터 수집 중")
        lines.append("")

        # ── 7. 운영 봇 현황 (전체 통합) ──────────────
        lines.append("🤖 <b>운영 봇 현황</b>")

        # OKX 봇 (비활성 시 표시 안 함)
        if self.settings.okx_trading_enabled and bot.get("balance_usd") is not None:
            lines.append("")
            lines.append("📊 <b>OKX 선물</b>")
            lines.append(f"  잔고: ${bot['balance_usd']:.2f}")
            wr = bot.get("win_rate", 0) * 100
            lines.append(f"  승률: {wr:.0f}% ({bot.get('wins', 0)}승/{bot.get('losses', 0)}패)")
            lines.append(f"  누적 PnL: {bot.get('total_pnl_pct', 0):+.2f}%")
            if bot.get("consecutive_losses", 0) >= 3:
                lines.append(f"  ⚠️ 연속 {bot['consecutive_losses']}패")

        # Polymarket
        if polymarket and polymarket.get("active"):
            lines.append("")
            lines.append(f"🌤️ <b>Polymarket 날씨봇</b> ({polymarket.get('mode', '?')})")
            lines.append(f"  USDC.e: ${polymarket.get('balance_usdce', 0):.2f}")
            lines.append(f"  POL: {polymarket.get('balance_pol', 0):.2f}")
            lines.append(
                f"  거래: 어제 {polymarket.get('yesterday_trades', 0)}건 "
                f"/ 누적 {polymarket.get('total_trades', 0)}건"
            )

        # 솔라나 봇 3종
        if solana:
            for bot_key, info in solana.items():
                if not info.get("active"):
                    continue
                emoji = info.get("emoji", "🤖")
                name = info.get("name", bot_key)
                lines.append("")
                lines.append(f"{emoji} <b>{name}</b> ({info.get('mode', '?')})")
                lines.append(f"  지갑: <code>{info.get('wallet_short', '?')}</code>")
                lines.append(f"  SOL: {info.get('sol_balance', 0):.4f}")
                trades = info.get("yesterday_trades", 0)
                wins = info.get("yesterday_wins", 0)
                losses = info.get("yesterday_losses", 0)
                pnl = info.get("yesterday_pnl_pct", 0)

                if trades > 0:
                    sells_total = wins + losses
                    if sells_total > 0:
                        wr = wins / sells_total * 100
                        lines.append(f"  어제: {trades}건 (✅{wins}/❌{losses}, 승률 {wr:.0f}%)")
                    else:
                        lines.append(f"  어제: {trades}건 (매수만)")
                    if abs(pnl) > 0.01:
                        lines.append(f"  어제 PnL: {pnl:+.2f}%")
                else:
                    lines.append(f"  어제: 거래 없음")

                if info.get("open_positions"):
                    lines.append(f"  보유: {info['open_positions']}개")

        lines.append("")

        # ── 8. 오늘의 전략 (AI 요약) ───────────────
        strategy = self._derive_strategy(fg, price, onchain, bot, polymarket, solana)
        if strategy:
            lines.append("💡 <b>오늘의 전략</b>")
            for s in strategy:
                lines.append(f"• {s}")

        return "\n".join(lines)

    def _derive_strategy(self, fg, price, onchain, bot, polymarket=None, solana=None) -> list[str]:
        """코드 기반 전략 도출 (AI 호출 없음 — 비용 $0)"""
        tips = []

        # 공포탐욕 극단
        fg_val = fg.get("value", 50)
        if fg_val < 15:
            tips.append("극단적 공포 → BTC/ETH 롱 관심 (역사적 매수 구간)")
        elif fg_val > 80:
            tips.append("극단적 탐욕 → 수익 실현 + 포지션 축소 권고")

        # 펀딩비 극단
        btc_p = price.get("BTC", {}) if isinstance(price, dict) else {}
        fund_vals = [
            btc_p.get("funding_okx", 0) or 0,
            btc_p.get("funding_bitget", 0) or 0,
            btc_p.get("funding_hyperliquid", 0) or 0,
        ]
        valid_funds = [x for x in fund_vals if abs(x) > 1e-8]
        avg_fund = (sum(valid_funds) / len(valid_funds) * 100) if valid_funds else 0
        if avg_fund < -0.05:
            tips.append(f"BTC 숏 과열 ({avg_fund:.3f}%) → 반등 기대 롱 관심")
        elif avg_fund > 0.05:
            tips.append(f"BTC 롱 과열 ({avg_fund:.3f}%) → 청산 주의")

        # 봇 연패
        if bot.get("consecutive_losses", 0) >= 3:
            tips.append("OKX 연패 중 — 진입 기준 강화, 확신도 높은 신호만")

        # TVL 하락
        if onchain.get("tvl_change_24h_pct", 0) < -3:
            tips.append("DeFi TVL 급락 → 위험 자산 회피 모드")

        # 솔라나 봇별 추천
        if solana:
            for bot_key, info in solana.items():
                if not info.get("active"):
                    continue
                trades = info.get("yesterday_trades", 0)
                pnl = info.get("yesterday_pnl_pct", 0)
                name = info.get("name", "")
                emoji = info.get("emoji", "🤖")

                if trades > 0 and pnl > 50:
                    tips.append(f"{emoji} {name} 어제 PnL +{pnl:.0f}% — 좋은 흐름")
                elif trades > 0 and pnl < -30:
                    tips.append(f"{emoji} {name} 어제 PnL {pnl:.0f}% — 파라미터 점검 필요")

        # 밈코인 시장 활발도
        if memes := None:
            pass  # placeholder

        # 모드별 추천
        modes = []
        if polymarket and polymarket.get("mode") == "paper":
            modes.append("Polymarket")
        if solana:
            paper_solana = [s.get("name") for s in solana.values() if s.get("mode") == "paper"]
            if paper_solana:
                modes.extend(paper_solana)
        if modes and len(modes) >= 2:
            tips.append(f"📝 Paper 검증 중: {', '.join(modes[:3])}")

        if not tips:
            tips.append("뚜렷한 극단 신호 없음 — 기존 전략 유지")

        return tips
