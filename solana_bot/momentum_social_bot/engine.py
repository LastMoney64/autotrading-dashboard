"""
MomentumSocialEngine — Bot 3: 모멘텀 + 소셜 결합 트레이더

전략:
"진짜 펌프는 거래량 + 트윗 동시 폭발"
가짜 펌프는 거래량만 오르거나 트윗만 오름.

진입 조건 (둘 다 충족):
1. 24H 거래량 5x+ 증가
2. 트위터 멘션 10+개 (또는 급증)
3. 시총 $100K~$5M (적당한 크기)
4. 매수 비율 60%+
5. 안전성 5중 검증 통과

청산:
- +50% → 50% 빠른 익절
- +100% → 25% 추가
- 거래량 정점 후 50% 감소 시 즉시 청산
- -25% 손절

특징:
- 15분마다 스캔 (적당한 빈도)
- 단기 보유 (3~24시간)
- 시간대별 성공률 학습
"""

import logging
import asyncio
import time
import json
import os
from pathlib import Path


def _persistent_dir() -> Path:
    """영구 저장 디렉토리 (Railway Volume 우선)"""
    db_path = os.getenv("DB_PATH", "").strip()
    if db_path:
        return Path(db_path).parent
    return Path(__file__).parent.parent.parent / "data"
from datetime import datetime, timezone, timedelta
from typing import Optional

from solana_bot.shared import SolanaClient, JupiterSwap, HeliusClient, SafetyChecker
from solana_bot.momentum_social_bot.scanners import (
    DexScreenerScanner, TwitterMentionScanner,
)

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


class MomentumSocialEngine:
    """모멘텀 + 소셜 결합 매매 봇"""

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
        self.dex_scanner = DexScreenerScanner()
        self.twitter_scanner = TwitterMentionScanner()

        # 매매 파라미터
        self.mode = settings.solana_mode
        self.max_buy_sol = 0.04   # 거래당 0.04 SOL (~$6)
        self.scan_interval = 900   # 15분마다
        self.max_positions = 4    # 동시 보유 최대 4개

        # 진입 필터 (현실 반영 완화 — 트위터 멘션 의존도 낮춤)
        self.min_volume_change_pct = 150  # 1H가 6H 평균의 1.5배+ (200→150)
        self.min_volume_24h_usd = 30_000  # 최소 거래량 (50K→30K)
        self.min_buy_ratio = 0.52         # 매수 우세 (55→52)
        self.min_market_cap = 30_000      # $30K (50K→30K, 더 일찍)
        self.max_market_cap = 5_000_000   # $5M
        self.min_mention_count = 0        # 트윗 의존 제거 (5→0, Nitter 불안정)
        self.min_combined_score = 0.35    # 합산 점수 (0.5→0.35)

        # 청산 — 문샷 친화 (모멘텀도 1000x 잡기)
        self.stop_loss_pct = -25
        # 부분 청산 — 45% 회수, 55% moonbag
        self.tp_levels = [
            (50, 25),    # +50% → 25% 청산
            (200, 20),   # +200% → 20% 청산
            (0, 0),      # placeholder
            (0, 0),      # placeholder
        ]
        self.timeout_hours = 24  # 24시간 보유 한도

        # 동적 트레일링 (모멘텀은 빠른 보호 — drop% 살짝 좁게)
        self.trailing_activate_pct = 30
        self.trailing_tiers = [
            (200, 20),     # +30~200% → -20% (모멘텀 빠른 보호)
            (1000, 35),    # +200~1000% → -35%
            (10_000, 50),  # +1000~10000% → -50%
            (100_000, 65), # +10000~100000% → -65%
            (10**9, 75),   # +100000%+ → -75%
        ]

        # 상태
        self.scan_count = 0
        self.positions: dict[str, dict] = {}
        self.recent_attempts: dict[str, int] = {}  # mint → ts
        self.session_stats: dict[int, dict] = {}   # hour KST → {wins, losses}

        # 포지션 영구 저장
        self.positions_file = _persistent_dir() / "momentum_positions.json"

        # Paper 가상 잔고
        self.paper_balance_file = _persistent_dir() / "momentum_paper_balance.json"
        self.paper_balance: Optional[float] = None

        self._init_db()

    # ──────────────────────────────────────────────
    # Paper 모드 가상 잔고
    # ──────────────────────────────────────────────

    def _save_paper_balance(self):
        try:
            self.paper_balance_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.paper_balance_file, "w") as f:
                json.dump({"balance": self.paper_balance}, f)
        except Exception as e:
            logger.warning(f"Momentum paper 잔고 저장 실패: {e}")

    def _load_paper_balance(self, default_sol: float):
        try:
            if self.paper_balance_file.exists():
                with open(self.paper_balance_file, "r") as f:
                    data = json.load(f)
                self.paper_balance = float(data.get("balance", default_sol))
                return
        except Exception:
            pass
        self.paper_balance = default_sol
        self._save_paper_balance()

    async def _get_available_sol(self) -> float:
        if self.mode == "paper":
            return self.paper_balance if self.paper_balance is not None else 0.0
        return await self.client.get_sol_balance()

    # ──────────────────────────────────────────────
    # 포지션 영구 저장
    # ──────────────────────────────────────────────

    def _save_positions(self):
        try:
            self.positions_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.positions_file, "w", encoding="utf-8") as f:
                json.dump(self.positions, f, ensure_ascii=False, default=str)
        except Exception as e:
            logger.warning(f"Momentum positions 저장 실패: {e}")

    def _load_positions(self):
        try:
            if not self.positions_file.exists():
                return
            with open(self.positions_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.positions = loaded
                logger.info(f"  📂 Momentum 포지션 {len(self.positions)}개 복원")
        except Exception as e:
            logger.warning(f"Momentum positions 로드 실패: {e}")

    async def _restore_from_db(self):
        """DB에서 미청산 포지션 복원"""
        try:
            cur = self.db.conn.execute(
                """SELECT token_mint, symbol, amount_sol, token_amount, timestamp
                   FROM momentum_social_trades
                   WHERE side='BUY'
                   AND token_mint NOT IN (
                       SELECT DISTINCT token_mint FROM momentum_social_trades WHERE side='SELL'
                   )
                   ORDER BY timestamp DESC"""
            )
            rows = cur.fetchall()
            from datetime import datetime as _dt
            restored = 0
            for row in rows:
                mint = row["token_mint"]
                if mint in self.positions:
                    continue
                meta = await self.helius.get_token_metadata(mint)
                if not meta:
                    continue
                decimals = meta.get("decimals", 6)
                amount_sol = float(row["amount_sol"] or 0)
                token_amount_ui = float(row["token_amount"] or 0)
                if amount_sol <= 0 or token_amount_ui <= 0:
                    continue

                self.positions[mint] = {
                    "mint": mint,
                    "symbol": row["symbol"] or meta.get("symbol", "?"),
                    "entry_sol": amount_sol,
                    "token_amount_raw": int(token_amount_ui * (10 ** decimals)),
                    "decimals": decimals,
                    "entry_price_sol": amount_sol / token_amount_ui,
                    "entry_volume_24h": 0,
                    "peak_volume_24h": 0,
                    "entry_mentions": 0,
                    "entry_time": int(time.time()),
                    "entry_hour_kst": _dt.now(KST).hour,
                    "tp_done": [False, False, False, False],
                    "peak_pnl_pct": 0,
                    "trailing_active": False,
                }
                restored += 1
                logger.info(f"  ♻️  Momentum DB 복원: ${row['symbol']} ({mint[:8]}..)")

            if restored > 0:
                self._save_positions()
                logger.info(f"  ✅ Momentum {restored}개 미청산 포지션 복원")
        except Exception as e:
            logger.warning(f"Momentum DB 복원 실패: {e}")

    def _init_db(self):
        try:
            self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS momentum_social_trades (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    mode TEXT,
                    side TEXT,
                    token_mint TEXT,
                    symbol TEXT,
                    amount_sol REAL,
                    token_amount REAL,
                    volume_change_pct REAL,
                    mention_count INTEGER,
                    pnl_pct REAL,
                    signature TEXT,
                    note TEXT
                )
            """)
            self.db.conn.commit()
        except Exception as e:
            logger.warning(f"momentum_social_trades 테이블 생성 실패: {e}")

    # ──────────────────────────────────────────────
    # 메인 사이클
    # ──────────────────────────────────────────────

    async def run_cycle(self):
        self.scan_count += 1
        logger.info(f"📈 [Momentum #{self.scan_count}] 스캔 시작 (mode: {self.mode})")

        try:
            # 1. 청산 체크 먼저
            await self._check_exits()

            # 2. 한도 체크
            if len(self.positions) >= self.max_positions:
                logger.info(f"  📊 보유 {len(self.positions)}개 (한도 {self.max_positions})")
                return

            # 3. 트렌딩 토큰 수집
            trending = await self.dex_scanner.get_trending_solana()
            logger.info(f"  📊 솔라나 트렌딩: {len(trending)}개")

            # 4. 1차 필터 (거래량/시총)
            volume_filtered = self._filter_volume(trending)
            logger.info(f"  ✅ 거래량 필터 통과: {len(volume_filtered)}개")

            # 5. 2차 필터 (트위터 멘션 결합)
            opportunities = await self._filter_with_social(volume_filtered)
            logger.info(f"  🎯 합산 점수 통과: {len(opportunities)}개")

            # 6. 매수 시도
            buys = 0
            for opp in opportunities[:5]:
                if buys >= 2:
                    break
                if await self._try_buy(opp):
                    buys += 1
                await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Momentum 사이클 에러: {e}", exc_info=True)

    # ──────────────────────────────────────────────
    # 필터링
    # ──────────────────────────────────────────────

    def _filter_volume(self, tokens: list[dict]) -> list[dict]:
        """1차: 거래량 + 시총 + 매수비율 필터"""
        filtered = []
        for t in tokens:
            try:
                # 시총 범위
                mcap = t.get("market_cap", 0) or t.get("fdv", 0)
                if not (self.min_market_cap <= mcap <= self.max_market_cap):
                    continue

                # 거래량
                if t.get("volume_24h_usd", 0) < self.min_volume_24h_usd:
                    continue

                # 거래량 급증 (1H가 6H 평균보다 큰가)
                if t.get("volume_change_1h_pct", 0) < self.min_volume_change_pct:
                    continue

                # 매수 비율
                if t.get("buy_ratio_1h", 0) < self.min_buy_ratio:
                    continue

                # 가격 변화 (-50% ~ +200% 사이만)
                pc24 = t.get("price_change_24h_pct", 0)
                if pc24 < -50 or pc24 > 200:
                    continue

                # 이미 보유 중이거나 최근 시도
                mint = t.get("mint")
                if mint in self.positions:
                    continue
                if mint in self.recent_attempts:
                    if time.time() - self.recent_attempts[mint] < 1800:  # 30분 쿨다운
                        continue

                filtered.append(t)
            except Exception:
                continue

        return filtered

    async def _filter_with_social(self, tokens: list[dict]) -> list[dict]:
        """2차: 트위터 멘션 결합"""
        result = []
        for t in tokens[:15]:  # 상위 15개만 트윗 체크 (rate limit)
            try:
                symbol = t.get("symbol", "")
                if not symbol or len(symbol) < 2:
                    continue

                mention = await self.twitter_scanner.get_mention_score(symbol)
                if mention["mention_count"] < self.min_mention_count:
                    continue

                # 합산 점수: 거래량 변화 + 매수비율 + 멘션 점수
                volume_score = min(t.get("volume_change_1h_pct", 0) / 500, 1.0)
                combined = (
                    volume_score * 0.4
                    + t.get("buy_ratio_1h", 0) * 0.3
                    + mention["score"] * 0.3
                )

                if combined < self.min_combined_score:
                    continue

                t["mention_data"] = mention
                t["combined_score"] = round(combined, 3)
                result.append(t)

                await asyncio.sleep(0.3)
            except Exception:
                continue

        result.sort(key=lambda x: x["combined_score"], reverse=True)
        return result

    # ──────────────────────────────────────────────
    # 매수
    # ──────────────────────────────────────────────

    async def _try_buy(self, opp: dict) -> bool:
        mint = opp["mint"]
        symbol = opp.get("symbol", "?")

        # 안전 검증
        report = await self.safety.check_token(mint)
        if not report["passed"]:
            reasons = " / ".join(report["fail_reasons"][:2])
            logger.info(f"  🚫 ${symbol} 안전 차단: {reasons}")
            self.recent_attempts[mint] = int(time.time())
            return False

        # 잔고 (paper=가상잔고)
        sol = await self._get_available_sol()
        if sol < self.max_buy_sol + 0.01:
            logger.info(f"  💸 SOL 부족 [{self.mode}]: {sol:.4f}")
            return False

        # 매수 사전 체크 (Jupiter에 매수 라우팅 있는지)
        SOL_MINT = "So11111111111111111111111111111111111111112"
        test_quote = await self.jupiter.get_quote(
            input_mint=SOL_MINT,
            output_mint=mint,
            amount=int(0.005 * 1e9),
            slippage_bps=300,
        )
        if not test_quote or int(test_quote.get("outAmount", 0)) <= 0:
            logger.info(f"  🚫 ${symbol} 매수 라우팅 불가 (Jupiter) — 매수 거부")
            self.recent_attempts[mint] = int(time.time())
            return False

        buy_amount = self.max_buy_sol
        mention = opp.get("mention_data", {})

        logger.info(
            f"  🛒 [{self.mode.upper()}] BUY ${symbol} {buy_amount:.4f} SOL "
            f"(vol Δ {opp.get('volume_change_1h_pct', 0):.0f}%, "
            f"mentions {mention.get('mention_count', 0)}, "
            f"score {opp.get('combined_score', 0):.2f})"
        )

        signature = ""
        output_amount = 0

        if self.mode == "live":
            try:
                result = await self.jupiter.buy_token(
                    token_mint=mint,
                    sol_amount=buy_amount,
                    slippage_bps=self.settings.solana_default_slippage_bps,
                    priority_fee_lamports=self.settings.solana_priority_fee_lamports,
                )
                if not result or not result.get("confirmed"):
                    logger.warning("  ❌ 매수 실패")
                    self.recent_attempts[mint] = int(time.time())
                    return False
                signature = result["signature"]
                output_amount = result["output_amount"]
                logger.info(f"  ✅ 체결: {signature[:20]}...")
            except Exception as e:
                logger.error(f"  ❌ 매수 에러: {e}")
                return False
        else:
            signature = "PAPER_" + str(int(time.time()))
            decimals = report["details"].get("decimals", 6)
            price_usd = opp.get("price_usd", 0)
            output_amount = (
                int(buy_amount * 1e9 * (10 ** decimals) / 1000)
                if not price_usd else
                int(buy_amount * 150 / price_usd * (10 ** decimals))  # SOL ~$150
            )

        decimals = report["details"].get("decimals", 6)
        token_amount_ui = output_amount / (10 ** decimals)

        self.positions[mint] = {
            "mint": mint,
            "symbol": symbol,
            "entry_sol": buy_amount,
            "token_amount_raw": output_amount,
            "decimals": decimals,
            "entry_price_sol": buy_amount / token_amount_ui if token_amount_ui > 0 else 0,
            "entry_volume_24h": opp.get("volume_24h_usd", 0),
            "peak_volume_24h": opp.get("volume_24h_usd", 0),
            "entry_mentions": mention.get("mention_count", 0),
            "entry_time": int(time.time()),
            "entry_hour_kst": datetime.now(KST).hour,
            "tp_done": [False, False, False, False],  # 4단계
            # 트레일링 스탑
            "peak_pnl_pct": 0,
            "trailing_active": False,
            "price_fail_count": 0,  # 가격 조회 연속 실패 카운터
        }
        self.recent_attempts[mint] = int(time.time())

        # 영구 저장
        self._save_positions()

        # Paper 가상 잔고 차감
        if self.mode == "paper":
            self.paper_balance -= buy_amount
            self._save_paper_balance()
            logger.info(f"  💰 paper 잔고: {self.paper_balance:.4f} SOL (-{buy_amount:.4f})")

        # DB
        try:
            self.db.conn.execute(
                """INSERT INTO momentum_social_trades
                (timestamp, mode, side, token_mint, symbol, amount_sol, token_amount,
                 volume_change_pct, mention_count, pnl_pct, signature, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(KST).isoformat(),
                    self.mode, "BUY", mint, symbol,
                    buy_amount, token_amount_ui,
                    opp.get("volume_change_1h_pct", 0),
                    mention.get("mention_count", 0),
                    0, signature,
                    f"score={opp.get('combined_score', 0):.2f} buyratio={opp.get('buy_ratio_1h', 0):.2f}",
                ),
            )
            self.db.conn.commit()
        except Exception:
            pass

        # 텔레그램
        try:
            await self.telegram.send(
                f"📈 <b>Momentum {self.mode.upper()}: BUY</b>\n\n"
                f"<b>토큰:</b> ${symbol}\n"
                f"<b>매수:</b> {buy_amount:.4f} SOL\n"
                f"<b>거래량 변화:</b> +{opp.get('volume_change_1h_pct', 0):.0f}% (1H vs 6H)\n"
                f"<b>24H 거래량:</b> ${opp.get('volume_24h_usd', 0):,.0f}\n"
                f"<b>매수 비율:</b> {opp.get('buy_ratio_1h', 0):.0%}\n"
                f"<b>트위터 멘션:</b> {mention.get('mention_count', 0)}\n"
                f"<b>합산 점수:</b> {opp.get('combined_score', 0):.2f}\n"
                f"<b>주소:</b> <code>{mint[:8]}...{mint[-6:]}</code>"
            )
        except Exception:
            pass

        return True

    # ──────────────────────────────────────────────
    # 청산
    # ──────────────────────────────────────────────

    async def _check_exits(self):
        if not self.positions:
            return
        for mint in list(self.positions.keys()):
            try:
                await self._check_position(mint)
            except Exception as e:
                logger.warning(f"포지션 체크 실패 {mint[:10]}: {e}")

    def _get_trailing_drop(self, peak_pnl_pct: float) -> Optional[float]:
        """peak PnL에 따라 동적 트레일링 drop% (문샷 친화)"""
        if peak_pnl_pct < self.trailing_activate_pct:
            return None
        for tier_max, drop_pct in self.trailing_tiers:
            if peak_pnl_pct < tier_max:
                return drop_pct
        return self.trailing_tiers[-1][1]

    async def _check_position(self, mint: str):
        pos = self.positions.get(mint)
        if not pos:
            return

        # 현재 가격 (실패 시 카운터 증가, 5회 연속 실패 → 강제 청산)
        current_price = await self._get_token_price_sol(mint, pos["decimals"])
        if not current_price:
            pos["price_fail_count"] = pos.get("price_fail_count", 0) + 1
            if pos["price_fail_count"] >= 5:
                logger.warning(
                    f"  ⚠️  ${pos.get('symbol','?')} 가격 조회 5회 연속 실패 → 강제 청산"
                )
                await self._sell(mint, 100, "⚠️ 좀비 포지션 강제 청산 (가격 조회 불가)")
            return
        pos["price_fail_count"] = 0  # 성공 시 리셋

        if pos["entry_price_sol"] <= 0:
            return

        pnl_pct = (current_price - pos["entry_price_sol"]) / pos["entry_price_sol"] * 100

        # peak 갱신 (트레일링 스탑용)
        if pnl_pct > pos.get("peak_pnl_pct", 0):
            pos["peak_pnl_pct"] = pnl_pct
        peak = pos.get("peak_pnl_pct", 0)

        # 1. 손절
        if pnl_pct <= self.stop_loss_pct:
            await self._sell(mint, 100, f"💸 손절 {pnl_pct:.1f}%")
            return

        # 2. 동적 트레일링 (peak 클수록 wider — 문샷 따라가기, 비율 기반)
        trailing_drop = self._get_trailing_drop(peak)
        if trailing_drop is not None:
            pos["trailing_active"] = True
            peak_value = 1.0 + peak / 100.0
            current_value = 1.0 + pnl_pct / 100.0
            if peak_value > 0:
                drop_ratio_pct = (peak_value - current_value) / peak_value * 100.0
                if drop_ratio_pct >= trailing_drop:
                    await self._sell(
                        mint, 100,
                        f"🎯 트레일링 청산 (peak +{peak:.0f}% → 현재 {pnl_pct:+.0f}%, "
                        f"가격 -{drop_ratio_pct:.0f}% ≥ 한도 -{trailing_drop:.0f}%)"
                    )
                    return

        # 3. 부분 익절 (45% 회수, 55% moonbag)
        if pnl_pct >= 50 and not pos["tp_done"][0]:
            await self._sell(mint, 25, f"💰 +{pnl_pct:.0f}% 1단계 (25% — 수수료+익절)")
            pos["tp_done"][0] = True
            return

        if pnl_pct >= 200 and not pos["tp_done"][1]:
            await self._sell(mint, 20, f"💰 +{pnl_pct:.0f}% 2단계 (20% — 원금 회수)")
            pos["tp_done"][1] = True
            return

        # +200% 이후는 트레일링이 처리 (55% moonbag)

        # 4. 거래량 정점 후 50% 감소 (모멘텀 소진) — Momentum 특화 시그널
        try:
            current_data = await self.dex_scanner.search_solana(pos.get("symbol", ""))
            for t in current_data:
                if t.get("mint") == mint:
                    cur_vol = t.get("volume_24h_usd", 0)
                    if cur_vol > pos["peak_volume_24h"]:
                        pos["peak_volume_24h"] = cur_vol
                    elif pos["peak_volume_24h"] > 0 and cur_vol < pos["peak_volume_24h"] * 0.5:
                        await self._sell(
                            mint, 100,
                            f"📉 거래량 50% 하락 (모멘텀 소진) {pnl_pct:+.1f}%"
                        )
                        return
                    break
        except Exception:
            pass

        # 5. 24시간 타임아웃
        elapsed_h = (int(time.time()) - pos["entry_time"]) / 3600
        if elapsed_h >= self.timeout_hours:
            await self._sell(mint, 100, f"⏰ 타임아웃 {elapsed_h:.1f}h ({pnl_pct:+.1f}%)")

    async def _get_token_price_sol(self, mint: str, decimals: int) -> Optional[float]:
        """1 토큰당 SOL 가격 (라우팅 가능한 양으로 견적 후 단위 변환)"""
        SOL_MINT = "So11111111111111111111111111111111111111112"
        for sample_tokens in [100, 10_000, 1_000_000]:
            try:
                sample = sample_tokens * (10 ** decimals)
                quote = await self.jupiter.get_quote(
                    input_mint=mint,
                    output_mint=SOL_MINT,
                    amount=sample,
                    slippage_bps=300,
                )
                if not quote:
                    continue
                out_lamports = int(quote.get("outAmount", 0))
                if out_lamports <= 0:
                    continue
                return out_lamports / 1e9 / sample_tokens
            except Exception:
                continue
        return None

    async def _sell(self, mint: str, percent: int, reason: str):
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
                    return
            except Exception:
                return
        else:
            current = await self._get_token_price_sol(mint, pos["decimals"])
            sol_received = sell_raw * (current or 0) / (10 ** pos["decimals"])

        partial_entry = pos["entry_sol"] * percent / 100
        pnl_pct = ((sol_received - partial_entry) / partial_entry * 100) if partial_entry else 0

        # 시간대별 학습 (자기학습 데이터)
        hour = pos.get("entry_hour_kst", 0)
        if hour not in self.session_stats:
            self.session_stats[hour] = {"wins": 0, "losses": 0}
        if pnl_pct > 0:
            self.session_stats[hour]["wins"] += 1
        else:
            self.session_stats[hour]["losses"] += 1

        # DB
        try:
            self.db.conn.execute(
                """INSERT INTO momentum_social_trades
                (timestamp, mode, side, token_mint, symbol, amount_sol, token_amount,
                 volume_change_pct, mention_count, pnl_pct, signature, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(KST).isoformat(),
                    self.mode, "SELL", mint, symbol,
                    sol_received, sell_raw / (10 ** pos["decimals"]),
                    0, pos.get("entry_mentions", 0),
                    pnl_pct, signature, reason,
                ),
            )
            self.db.conn.commit()
        except Exception:
            pass

        # 텔레그램
        emoji = "💰" if pnl_pct > 0 else "💸"
        try:
            await self.telegram.send(
                f"{emoji} <b>Momentum {self.mode.upper()}: SELL {percent}%</b>\n\n"
                f"<b>토큰:</b> ${symbol}\n"
                f"<b>사유:</b> {reason}\n"
                f"<b>받음:</b> {sol_received:.4f} SOL\n"
                f"<b>PnL:</b> {pnl_pct:+.2f}%"
            )
        except Exception:
            pass

        if percent >= 100:
            self.positions.pop(mint, None)
        else:
            pos["token_amount_raw"] -= sell_raw

        # 영구 저장
        self._save_positions()

        # Paper 가상 잔고 증가
        if self.mode == "paper" and sol_received > 0:
            self.paper_balance += sol_received
            self._save_paper_balance()

    # ──────────────────────────────────────────────
    # 초기화
    # ──────────────────────────────────────────────

    async def initialize(self):
        sol = await self.client.get_sol_balance()
        s = self.client.get_status()

        # Paper 가상 잔고 로드
        self._load_paper_balance(default_sol=sol)

        # 포지션 복원
        self._load_positions()
        await self._restore_from_db()

        # 가격 조회 안 되는 포지션 정리
        invalid_mints = []
        for mint, pos in list(self.positions.items()):
            try:
                price = await self._get_token_price_sol(mint, pos.get("decimals", 6))
                if price is None or price <= 0:
                    invalid_mints.append(mint)
            except Exception:
                invalid_mints.append(mint)

        if invalid_mints:
            for mint in invalid_mints:
                pos = self.positions.pop(mint, None)
                if pos and self.mode == "paper":
                    self.paper_balance += pos.get("entry_sol", 0)
                logger.info(f"  🗑️  Momentum 가격 조회 불가: ${pos.get('symbol','?')}")
            self._save_positions()
            self._save_paper_balance()

        logger.info(
            f"📈 Momentum 봇 초기화\n"
            f"  지갑: {s['address_short']}\n"
            f"  실제 SOL: {sol:.4f}\n"
            f"  Paper 가상 잔고: {self.paper_balance:.4f}\n"
            f"  모드: {self.mode}\n"
            f"  유효 포지션: {len(self.positions)}개"
            f"{f' (정리: {len(invalid_mints)}개)' if invalid_mints else ''}"
        )
        positions_msg = (
            f"\n<b>유효 포지션:</b> {len(self.positions)}개"
            if self.positions else ""
        )
        invalid_msg = (
            f"\n<b>제거된 포지션:</b> {len(invalid_mints)}개 (가격 조회 불가)"
            if invalid_mints else ""
        )
        balance_msg = (
            f"\n<b>Paper 가상 잔고:</b> {self.paper_balance:.4f} SOL"
            if self.mode == "paper" else ""
        )
        try:
            await self.telegram.send(
                f"📈 <b>Momentum + 소셜 봇 시작</b>\n\n"
                f"<b>지갑:</b> <code>{s['address_short']}</code>\n"
                f"<b>실제 SOL:</b> {sol:.4f}"
                f"{balance_msg}\n"
                f"<b>모드:</b> {self.mode}\n"
                f"<b>주기:</b> {self.scan_interval//60}분마다\n"
                f"<b>전략:</b> 거래량 + 트윗 동시 급증 토큰만"
                f"{positions_msg}"
                f"{invalid_msg}"
            )
        except Exception:
            pass

    async def close(self):
        await self.dex_scanner.close()
        await self.twitter_scanner.close()
