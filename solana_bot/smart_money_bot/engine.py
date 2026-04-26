"""
SmartMoneyEngine — Bot 1: 스마트머니 카피트레이더

전략:
1. 추적 지갑 5~10개 모니터링 (5분마다)
2. 그들이 새 토큰 매수 감지 → 카피 매수
3. 안전성 5중 검증 통과만
4. 진입: $1~3 (작게 분산)
5. 청산: 추적 지갑이 50%+ 청산 시 즉시 청산
6. 손절: -30%
7. 익절: +100%/+200% 부분 청산
8. 자기학습: 좋은 지갑 가중치 ↑, 나쁜 지갑 ↓
"""

import logging
import asyncio
import time
import json
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

from solana_bot.shared import SolanaClient, JupiterSwap, HeliusClient, SafetyChecker
from solana_bot.smart_money_bot.wallets import (
    get_active_wallets,
    update_wallet_stats,
    TRACKED_WALLETS,
)

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


def _persistent_dir() -> Path:
    """영구 저장 디렉토리 (Railway Volume 우선)"""
    # DB_PATH env 사용해서 같은 영구 디렉토리에 저장
    db_path = os.getenv("DB_PATH", "").strip()
    if db_path:
        return Path(db_path).parent
    # 폴백: 로컬 data 폴더
    return Path(__file__).parent.parent.parent / "data"


class SmartMoneyEngine:
    """스마트머니 카피 매매 봇"""

    def __init__(
        self, settings, telegram, db,
        client: SolanaClient,
        jupiter: JupiterSwap,
        helius: HeliusClient,
        safety: SafetyChecker,
    ):
        self.settings = settings
        self.telegram = telegram
        self.db = db
        self.client = client
        self.jupiter = jupiter
        self.helius = helius
        self.safety = safety

        # 매매 파라미터
        self.mode = settings.solana_mode  # "paper" or "live"
        self.max_buy_sol = settings.solana_max_buy_sol
        self.scan_interval = 300  # 5분마다 스캔
        self.consensus_threshold = 2  # 같은 토큰 2명 이상 매수 시 진입
        self.high_winrate_solo = 0.75  # win_rate 75%+ 단독 시그널 OK

        # 청산 파라미터 — 4단계 익절 + 트레일링 스탑 + 추적자 청산 카피
        self.stop_loss_pct = -30                 # 손절 -30%
        # 단계별 익절: (PnL%, 매도% of 남은 잔량)
        # 30%→33%, 50%→33%, 100%→50%, 200%→100% (마지막은 전부)
        self.take_profit_stages = [
            (30, 33),    # +30%  → 남은 100% 중 33% 청산 (총 ~33%)
            (50, 33),    # +50%  → 남은 67% 중 33% 청산 (총 ~55%)
            (100, 50),   # +100% → 남은 45% 중 50% 청산 (총 ~78%)
            (200, 100),  # +200% → 나머지 전부
        ]
        # 트레일링 스탑: +30% 한 번 찍으면 활성, peak 대비 -25% 떨어지면 청산
        self.trailing_activate_pct = 30          # +30% 한 번 도달 시 활성화
        self.trailing_drop_pct = 25              # peak 대비 -25% 청산
        # 추적자 청산 카피: 추적자 절반 이상이 50%+ 매도 시 우리도 청산
        self.tracker_sold_threshold_pct = 50     # 추적자 잔고 50% 미만 = 매도
        self.tracker_majority_pct = 0.5          # 추적자 50%+ 매도 시 발동

        # 상태
        self.scan_count = 0
        self.trades_count = 0
        self.last_seen_signatures: dict[str, set] = {}  # wallet → 본 sig 캐시
        self.positions: dict[str, dict] = {}  # mint → 포지션 정보

        # 포지션 영구 저장 (Railway Volume)
        self.positions_file = _persistent_dir() / "smart_money_positions.json"

        self._init_db()

    def _init_db(self):
        """DB 테이블 생성"""
        try:
            self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS smart_money_trades (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    mode TEXT,
                    side TEXT,
                    token_mint TEXT,
                    amount_sol REAL,
                    token_amount REAL,
                    source_wallet TEXT,
                    signature TEXT,
                    pnl_pct REAL,
                    note TEXT
                )
            """)
            self.db.conn.commit()
        except Exception as e:
            logger.warning(f"smart_money_trades 테이블 생성 실패: {e}")

    # ──────────────────────────────────────────────
    # 메인 사이클
    # ──────────────────────────────────────────────

    async def run_cycle(self):
        """1회 사이클: 추적 지갑 모니터링 → 카피"""
        self.scan_count += 1
        logger.info(f"🐋 [SmartMoney #{self.scan_count}] 스캔 시작 (mode: {self.mode})")

        try:
            # 1. 활성 추적 지갑들의 최근 매수 감지
            buy_signals = await self._collect_recent_buys()
            logger.info(f"  최근 매수 신호: {len(buy_signals)}개 토큰")

            # 2. 합의/고승률 필터링
            opportunities = self._filter_opportunities(buy_signals)
            logger.info(f"  합의/고승률 통과: {len(opportunities)}개")

            # 3. 안전성 검증 + 매수 실행
            for opp in opportunities[:3]:  # 사이클당 최대 3건
                if opp["mint"] in self.positions:
                    continue  # 이미 보유 중
                await self._try_buy(opp)
                await asyncio.sleep(2)

            # 4. 보유 포지션 청산 체크
            await self._check_exits()

        except Exception as e:
            logger.error(f"SmartMoney 사이클 에러: {e}", exc_info=True)

    # ──────────────────────────────────────────────
    # 매수 신호 수집
    # ──────────────────────────────────────────────

    async def _collect_recent_buys(self) -> dict[str, list[dict]]:
        """
        활성 지갑들의 최근 5분 내 매수 토큰 수집

        Returns: {mint: [{wallet, sol_spent, ts, win_rate}, ...]}
        """
        active = get_active_wallets()
        if not active:
            return {}

        signals: dict[str, list[dict]] = {}

        for w in active[:10]:  # 최대 10개 지갑 (rate limit)
            try:
                buys = await self.helius.get_recent_token_buys(
                    w["address"], since_seconds=600  # 10분
                )

                # 신규 시그너처만 (이미 본 것 제외)
                seen = self.last_seen_signatures.setdefault(w["address"], set())
                for buy in buys:
                    sig = buy.get("signature")
                    if not sig or sig in seen:
                        continue
                    seen.add(sig)

                    mint = buy.get("token_mint")
                    if not mint:
                        continue

                    signals.setdefault(mint, []).append({
                        "wallet": w["address"],
                        "wallet_tag": w.get("tag", ""),
                        "win_rate": w.get("win_rate", 0.5),
                        "weight": w.get("weight", 1.0),
                        "sol_spent": buy.get("sol_spent", 0),
                        "token_amount": buy.get("token_amount", 0),
                        "timestamp": buy.get("timestamp", 0),
                        "signature": sig,
                    })

                # 캐시 크기 제한
                if len(seen) > 200:
                    seen.clear()

                await asyncio.sleep(0.3)
            except Exception as e:
                logger.debug(f"지갑 {w['address'][:10]} 조회 실패: {e}")

        return signals

    def _filter_opportunities(self, signals: dict) -> list[dict]:
        """
        진입 기회 필터:
        - 같은 토큰을 N명 이상이 매수 (consensus)
        - 또는 win_rate 75%+ 단독 매수
        """
        opps = []
        for mint, buyers in signals.items():
            if len(buyers) >= self.consensus_threshold:
                # 합의 신호
                avg_winrate = sum(b["win_rate"] for b in buyers) / len(buyers)
                opps.append({
                    "mint": mint,
                    "buyers": buyers,
                    "buyer_count": len(buyers),
                    "avg_winrate": avg_winrate,
                    "signal_type": "CONSENSUS",
                    "score": len(buyers) * avg_winrate,
                })
            else:
                # 단독 신호 — 고승률 지갑만
                top = max(buyers, key=lambda b: b["win_rate"])
                if top["win_rate"] >= self.high_winrate_solo:
                    opps.append({
                        "mint": mint,
                        "buyers": [top],
                        "buyer_count": 1,
                        "avg_winrate": top["win_rate"],
                        "signal_type": "SOLO_HIGH",
                        "score": top["win_rate"],
                    })

        # 점수 높은 순 정렬
        opps.sort(key=lambda x: x["score"], reverse=True)
        return opps

    # ──────────────────────────────────────────────
    # 매수 실행
    # ──────────────────────────────────────────────

    async def _try_buy(self, opp: dict):
        """안전 검증 → 매수 → 포지션 등록"""
        mint = opp["mint"]
        logger.info(f"  🔍 안전 검증: {mint[:10]}... ({opp['signal_type']})")

        # 안전성 5중 검증
        report = await self.safety.check_token(mint)
        if not report["passed"]:
            reasons = " / ".join(report["fail_reasons"][:3])
            logger.info(f"  🚫 차단: {reasons}")
            return

        # 잔고 체크
        sol_balance = await self.client.get_sol_balance()
        if sol_balance < self.max_buy_sol + 0.01:  # 가스비 여유
            logger.info(f"  💸 SOL 잔고 부족: {sol_balance:.4f} < {self.max_buy_sol + 0.01}")
            return

        buy_amount = min(self.max_buy_sol, sol_balance * 0.3)  # 30% 단위 분산

        # 매수 실행
        token_symbol = report["details"].get("dexscreener", {}).get("dex", "?") or "?"
        meta = await self.helius.get_token_metadata(mint)
        if meta:
            token_symbol = meta.get("symbol", "?")

        buyers_str = ", ".join(b["wallet_tag"] or b["wallet"][:6] for b in opp["buyers"][:3])
        logger.info(
            f"  🛒 [{self.mode.upper()}] BUY ${token_symbol} {buy_amount:.4f} SOL "
            f"(by {buyers_str}, winrate {opp['avg_winrate']:.0%})"
        )

        if self.mode == "live":
            try:
                result = await self.jupiter.buy_token(
                    token_mint=mint,
                    sol_amount=buy_amount,
                    slippage_bps=self.settings.solana_default_slippage_bps,
                    priority_fee_lamports=self.settings.solana_priority_fee_lamports,
                )
                if not result or not result.get("confirmed"):
                    logger.warning(f"  ❌ 매수 실패")
                    return
                signature = result["signature"]
                output_amount = result["output_amount"]
                logger.info(f"  ✅ 체결: {signature[:20]}...")
            except Exception as e:
                logger.error(f"  ❌ 매수 에러: {e}")
                return
        else:
            # paper 모드
            signature = "PAPER_" + str(int(time.time()))
            output_amount = int(buy_amount * 1e9 / 0.0001)  # 가상 환산

        # 포지션 등록
        decimals = report["details"].get("decimals", 9)
        token_amount_ui = output_amount / (10 ** decimals)

        # 추적자들의 매수 시점 토큰 잔고 기록 (청산 카피 감지용)
        tracked_balances = {}
        for b in opp["buyers"]:
            wallet_addr = b["wallet"]
            try:
                bal = await self.helius.get_wallet_token_balance(wallet_addr, mint)
                if bal is not None:
                    tracked_balances[wallet_addr] = bal
                    logger.debug(f"  추적자 {wallet_addr[:8]} 잔고: {bal}")
            except Exception:
                pass

        self.positions[mint] = {
            "mint": mint,
            "symbol": token_symbol,
            "entry_sol": buy_amount,
            "token_amount_raw": output_amount,
            "token_amount_ui": token_amount_ui,
            "decimals": decimals,
            "entry_price_sol": buy_amount / token_amount_ui if token_amount_ui > 0 else 0,
            "entry_time": int(time.time()),
            "buyers": [b["wallet"] for b in opp["buyers"]],
            "signal_type": opp["signal_type"],
            # 단계별 청산 추적 (4단계)
            "stage_30_done": False,    # +30%
            "stage_50_done": False,    # +50%
            "stage_100_done": False,   # +100%
            "stage_200_done": False,   # +200%
            # 트레일링 스탑
            "peak_pnl_pct": 0,
            "trailing_active": False,
            # 추적자 청산 감지
            "tracked_balances": tracked_balances,    # {wallet: 매수 시점 잔고}
            "tracker_check_interval": 0,             # 추적자 체크 카운터 (5분마다 체크 비싸서)
        }

        # 영구 저장 (재배포 시 손실 방지)
        self._save_positions()

        # DB 저장
        try:
            self.db.conn.execute(
                """INSERT INTO smart_money_trades
                (timestamp, mode, side, token_mint, amount_sol, token_amount,
                 source_wallet, signature, pnl_pct, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(KST).isoformat(),
                    self.mode, "BUY", mint, buy_amount, token_amount_ui,
                    opp["buyers"][0]["wallet"], signature, 0,
                    f"{opp['signal_type']}, {opp['buyer_count']} buyers, winrate {opp['avg_winrate']:.0%}",
                ),
            )
            self.db.conn.commit()
        except Exception as e:
            logger.debug(f"DB 저장 실패: {e}")

        # 텔레그램 알림
        try:
            await self.telegram.send(
                f"🐋 <b>SmartMoney {self.mode.upper()}: BUY</b>\n\n"
                f"<b>토큰:</b> ${token_symbol}\n"
                f"<b>주소:</b> <code>{mint[:8]}...{mint[-6:]}</code>\n"
                f"<b>매수:</b> {buy_amount:.4f} SOL\n"
                f"<b>신호:</b> {opp['signal_type']}\n"
                f"<b>추적자:</b> {buyers_str}\n"
                f"<b>평균 승률:</b> {opp['avg_winrate']:.0%}\n"
                f"<b>유동성:</b> ${report['details'].get('liquidity_usd', 0):,.0f}"
            )
        except Exception as e:
            logger.debug(f"Telegram 알림 실패: {e}")

    # ──────────────────────────────────────────────
    # 청산 체크
    # ──────────────────────────────────────────────

    async def _check_exits(self):
        """보유 포지션 청산 조건 체크"""
        if not self.positions:
            return

        for mint in list(self.positions.keys()):
            try:
                await self._check_position_exit(mint)
            except Exception as e:
                logger.warning(f"포지션 청산 체크 실패 {mint[:10]}: {e}")

    async def _check_position_exit(self, mint: str):
        """
        단일 포지션 청산 결정 — 우선순위 기반:
        1순위: 추적자 청산 카피 (가장 강한 알파)
        2순위: 손절 -30%
        3순위: 트레일링 스탑 (peak 대비 -25%)
        4순위: 단계별 익절 (+30/+50/+100/+200)
        """
        pos = self.positions.get(mint)
        if not pos:
            return

        # 현재 가격 조회
        current_price = await self._get_token_price_sol(mint, pos["decimals"])
        if not current_price:
            return

        entry_price = pos["entry_price_sol"]
        if entry_price <= 0:
            return

        pnl_pct = (current_price - entry_price) / entry_price * 100

        # peak 갱신
        if pnl_pct > pos["peak_pnl_pct"]:
            pos["peak_pnl_pct"] = pnl_pct
        peak = pos["peak_pnl_pct"]

        # ════════════════════════════════════════════════
        # 1순위: 추적자 청산 카피 (가장 강한 시그널)
        # ════════════════════════════════════════════════
        # 매 사이클 호출하면 API 부담 → 5번에 1번만 (5분 × 5 = 25분마다)
        pos["tracker_check_interval"] += 1
        if pos["tracker_check_interval"] >= 5:
            pos["tracker_check_interval"] = 0
            sold_info = await self._tracked_wallet_sold(
                mint, pos["buyers"], pos.get("tracked_balances", {})
            )
            if sold_info["sold"]:
                await self._sell_position(
                    mint, 100,
                    f"🐋 추적자 청산 카피 ({sold_info['detail']}) PnL {pnl_pct:+.1f}%"
                )
                return

        # ════════════════════════════════════════════════
        # 2순위: 손절 -30%
        # ════════════════════════════════════════════════
        if pnl_pct <= self.stop_loss_pct:
            await self._sell_position(mint, 100, f"💸 손절 {pnl_pct:.1f}%")
            return

        # ════════════════════════════════════════════════
        # 3순위: 트레일링 스탑 (+30% 한 번 찍은 경우만 활성)
        # ════════════════════════════════════════════════
        if peak >= self.trailing_activate_pct:
            pos["trailing_active"] = True
            drop_from_peak = peak - pnl_pct
            if drop_from_peak >= self.trailing_drop_pct:
                await self._sell_position(
                    mint, 100,
                    f"🎯 트레일링 청산 (peak +{peak:.0f}% → 현재 {pnl_pct:+.0f}%)"
                )
                return

        # ════════════════════════════════════════════════
        # 4순위: 단계별 익절
        # ════════════════════════════════════════════════
        # +30% → 33% 청산
        if pnl_pct >= 30 and not pos["stage_30_done"]:
            await self._sell_position(mint, 33, f"💰 +{pnl_pct:.0f}% 1단계 익절 (33%)")
            pos["stage_30_done"] = True
            return

        # +50% → 추가 33% 청산
        if pnl_pct >= 50 and not pos["stage_50_done"]:
            await self._sell_position(mint, 33, f"💰 +{pnl_pct:.0f}% 2단계 익절 (33%)")
            pos["stage_50_done"] = True
            return

        # +100% → 추가 50% 청산
        if pnl_pct >= 100 and not pos["stage_100_done"]:
            await self._sell_position(mint, 50, f"💰 +{pnl_pct:.0f}% 3단계 익절 (50%)")
            pos["stage_100_done"] = True
            return

        # +200% → 나머지 전부
        if pnl_pct >= 200 and not pos["stage_200_done"]:
            await self._sell_position(mint, 100, f"🚀 +{pnl_pct:.0f}% 4단계 익절 (전량)")
            pos["stage_200_done"] = True
            return

    async def _get_token_price_sol(self, mint: str, decimals: int) -> Optional[float]:
        """1 토큰당 SOL 가격"""
        try:
            sample = 10 ** decimals  # 1 토큰
            quote = await self.jupiter.get_quote(
                input_mint=mint,
                output_mint="So11111111111111111111111111111111111111112",
                amount=sample,
                slippage_bps=300,
            )
            if not quote:
                return None
            out_lamports = int(quote.get("outAmount", 0))
            return out_lamports / 1e9
        except Exception:
            return None

    async def _tracked_wallet_sold(
        self, mint: str, source_wallets: list[str], initial_balances: dict
    ) -> dict:
        """
        추적 지갑이 이 토큰을 청산했는지 체크 (Helius 잔고 조회)

        절반 이상의 추적자가 50%+ 매도 시 → 우리도 청산

        Returns: {
            "sold": bool,
            "detail": str  # "2/3 매도" 형태
        }
        """
        if not source_wallets or not initial_balances:
            return {"sold": False, "detail": ""}

        sold_count = 0
        sold_wallets = []
        checked = 0

        for wallet in source_wallets:
            initial = initial_balances.get(wallet, 0)
            if initial <= 0:
                continue
            checked += 1
            current = await self.helius.get_wallet_token_balance(wallet, mint)
            if current is None:
                continue
            # 매수 시점 대비 50% 미만 보유 = "매도했다"
            if current < initial * (self.tracker_sold_threshold_pct / 100):
                sold_count += 1
                sold_wallets.append(wallet[:8])
            await asyncio.sleep(0.3)  # API rate limit 보호

        if checked == 0:
            return {"sold": False, "detail": ""}

        # 절반 이상 매도 시 발동
        threshold = max(1, int(checked * self.tracker_majority_pct))
        sold = sold_count >= threshold

        return {
            "sold": sold,
            "detail": f"{sold_count}/{checked} 매도 ({', '.join(sold_wallets[:2])})"
                      if sold else f"{sold_count}/{checked} 매도",
        }

    async def _sell_position(self, mint: str, percent: int, reason: str):
        """포지션 일부 또는 전부 청산"""
        pos = self.positions.get(mint)
        if not pos:
            return

        sell_raw = int(pos["token_amount_raw"] * percent / 100)
        if sell_raw <= 0:
            return

        symbol = pos.get("symbol", "?")
        logger.info(f"  💰 [{self.mode.upper()}] SELL ${symbol} {percent}% — {reason}")

        sol_received = 0
        signature = "PAPER_SELL"
        won = False

        if self.mode == "live":
            try:
                result = await self.jupiter.sell_token(
                    token_mint=mint,
                    token_amount_raw=sell_raw,
                    slippage_bps=self.settings.solana_default_slippage_bps,
                )
                if result and result.get("confirmed"):
                    signature = result["signature"]
                    sol_received = result["output_amount_sol"]
                else:
                    logger.warning("  ❌ 매도 실패")
                    return
            except Exception as e:
                logger.error(f"  ❌ 매도 에러: {e}")
                return
        else:
            # paper: 가상 매도
            current_price = await self._get_token_price_sol(mint, pos["decimals"])
            sol_received = sell_raw * (current_price or 0) / (10 ** pos["decimals"])

        # PnL 계산
        partial_entry = pos["entry_sol"] * percent / 100
        pnl_pct = ((sol_received - partial_entry) / partial_entry * 100) if partial_entry else 0
        won = pnl_pct > 0

        # 자기학습: 추적 지갑 통계 업데이트
        for wallet in pos.get("buyers", []):
            update_wallet_stats(wallet, won)

        # DB
        try:
            self.db.conn.execute(
                """INSERT INTO smart_money_trades
                (timestamp, mode, side, token_mint, amount_sol, token_amount,
                 source_wallet, signature, pnl_pct, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(KST).isoformat(),
                    self.mode, "SELL", mint, sol_received,
                    sell_raw / (10 ** pos["decimals"]),
                    pos["buyers"][0] if pos.get("buyers") else "",
                    signature, pnl_pct,
                    reason,
                ),
            )
            self.db.conn.commit()
        except Exception as e:
            logger.debug(f"DB 저장 실패: {e}")

        # 텔레그램 — 단계 진행 표시
        emoji = "💰" if won else "💸"
        peak_pct = pos.get("peak_pnl_pct", pnl_pct)
        stages_done = []
        if pos.get("stage_30_done"): stages_done.append("✅ +30%")
        if pos.get("stage_50_done"): stages_done.append("✅ +50%")
        if pos.get("stage_100_done"): stages_done.append("✅ +100%")
        if pos.get("stage_200_done"): stages_done.append("✅ +200%")
        if pos.get("trailing_active"): stages_done.append("🎯 트레일링 ON")
        stages_str = " ".join(stages_done) if stages_done else "—"

        try:
            await self.telegram.send(
                f"{emoji} <b>SmartMoney {self.mode.upper()}: SELL {percent}%</b>\n\n"
                f"<b>토큰:</b> ${symbol}\n"
                f"<b>사유:</b> {reason}\n"
                f"<b>받음:</b> {sol_received:.4f} SOL\n"
                f"<b>PnL:</b> {pnl_pct:+.2f}% (peak +{peak_pct:.0f}%)\n"
                f"<b>진행:</b> {stages_str}"
            )
        except Exception:
            pass

        # 포지션 업데이트
        if percent >= 100:
            self.positions.pop(mint, None)
        else:
            pos["token_amount_raw"] -= sell_raw

        # 영구 저장 (포지션 변화 반영)
        self._save_positions()

        self.trades_count += 1

    # ──────────────────────────────────────────────
    # 포지션 영구 저장 (Railway Volume)
    # ──────────────────────────────────────────────

    def _save_positions(self):
        """positions를 JSON으로 저장 (재배포 시 손실 방지)"""
        try:
            self.positions_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.positions_file, "w", encoding="utf-8") as f:
                json.dump(self.positions, f, ensure_ascii=False, default=str)
        except Exception as e:
            logger.warning(f"positions 저장 실패: {e}")

    def _load_positions(self):
        """저장된 positions 로드"""
        try:
            if not self.positions_file.exists():
                return
            with open(self.positions_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.positions = loaded
                logger.info(f"  📂 포지션 {len(self.positions)}개 복원 (JSON)")
        except Exception as e:
            logger.warning(f"positions 로드 실패: {e}")

    async def _restore_from_db(self):
        """
        DB에서 미청산 포지션 복원
        SELL 기록이 없는 BUY 거래들을 positions로 복원
        (JSON 파일 없거나 빠진 경우의 안전망)
        """
        try:
            cur = self.db.conn.execute(
                """SELECT token_mint, amount_sol, token_amount, source_wallet, timestamp, note
                   FROM smart_money_trades
                   WHERE side = 'BUY'
                   AND token_mint NOT IN (
                       SELECT DISTINCT token_mint FROM smart_money_trades
                       WHERE side = 'SELL'
                   )
                   ORDER BY timestamp DESC"""
            )
            rows = cur.fetchall()
            restored = 0
            for row in rows:
                mint = row["token_mint"]
                if mint in self.positions:
                    continue  # JSON에 이미 있음

                # 토큰 메타 다시 조회
                meta = await self.helius.get_token_metadata(mint)
                if not meta:
                    continue
                decimals = meta.get("decimals", 9)
                symbol = meta.get("symbol", "?")

                amount_sol = float(row["amount_sol"] or 0)
                token_amount_ui = float(row["token_amount"] or 0)
                if amount_sol <= 0 or token_amount_ui <= 0:
                    continue

                self.positions[mint] = {
                    "mint": mint,
                    "symbol": symbol,
                    "entry_sol": amount_sol,
                    "token_amount_raw": int(token_amount_ui * (10 ** decimals)),
                    "token_amount_ui": token_amount_ui,
                    "decimals": decimals,
                    "entry_price_sol": amount_sol / token_amount_ui,
                    "entry_time": int(time.time()),  # 정확한 시점 모름 → 지금
                    "buyers": [row["source_wallet"]] if row["source_wallet"] else [],
                    "signal_type": "RESTORED",
                    "stage_30_done": False,
                    "stage_50_done": False,
                    "stage_100_done": False,
                    "stage_200_done": False,
                    "peak_pnl_pct": 0,
                    "trailing_active": False,
                    "tracked_balances": {},  # 복원 시점에 추적 못 함
                    "tracker_check_interval": 0,
                }
                restored += 1
                logger.info(f"  ♻️  DB 복원: ${symbol} ({mint[:8]}..) {amount_sol:.4f} SOL")

            if restored > 0:
                self._save_positions()
                logger.info(f"  ✅ DB에서 {restored}개 미청산 포지션 복원")
        except Exception as e:
            logger.warning(f"DB 포지션 복원 실패: {e}")

    # ──────────────────────────────────────────────
    # 초기화
    # ──────────────────────────────────────────────

    async def initialize(self):
        """봇 시작 시 1회: 잔고 확인 + 포지션 복원 + 알림"""
        sol_balance = await self.client.get_sol_balance()
        active = get_active_wallets()
        status = self.client.get_status()

        # 포지션 복원: 1) JSON 파일 → 2) DB 백업
        self._load_positions()
        await self._restore_from_db()

        logger.info(
            f"🐋 SmartMoney 봇 초기화\n"
            f"  지갑: {status['address_short']}\n"
            f"  SOL: {sol_balance:.4f}\n"
            f"  추적 지갑: {len(active)}개\n"
            f"  모드: {self.mode}\n"
            f"  복원된 포지션: {len(self.positions)}개"
        )

        positions_msg = (
            f"\n<b>복원된 포지션:</b> {len(self.positions)}개"
            if self.positions else ""
        )

        try:
            await self.telegram.send(
                f"🐋 <b>SmartMoney 봇 시작</b>\n\n"
                f"<b>지갑:</b> <code>{status['address_short']}</code>\n"
                f"<b>SOL 잔고:</b> {sol_balance:.4f}\n"
                f"<b>추적 지갑:</b> {len(active)}개\n"
                f"<b>모드:</b> {self.mode}\n"
                f"<b>주기:</b> {self.scan_interval//60}분마다 스캔"
                f"{positions_msg}"
            )
        except Exception:
            pass
