"""
PumpFunSniperEngine — Bot 2: Pump.fun 졸업 스나이퍼

전략:
1. 1분마다 Pump.fun 본딩커브 80~95% 진행 토큰 스캔
2. 거래량 + 홀더 + 매수비율 필터
3. 졸업 임박 토큰 스나이프 매수
4. 졸업 직후 펌프에서 단계별 청산
5. 졸업 실패 (24H 내 본딩커브 정체) 시 손절

리스크 관리:
- 매수당 0.03 SOL (~$4.5)
- 동시 보유 최대 5개
- 일일 손실 -50% 도달 시 매매 중지
- 매수 후 6시간 내 졸업 못 하면 손절

자기학습:
- 졸업 성공 패턴 분석
- 시간대별 (KST) 성공률 추적
- 진행률 임계값 자동 튜닝
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
from solana_bot.pumpfun_sniper_bot.pumpportal_client import PumpPortalClient

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


class PumpFunSniperEngine:
    """Pump.fun 졸업 스나이퍼"""

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
        self.pump = PumpPortalClient()

        # 매매 파라미터
        self.mode = settings.solana_mode
        self.max_buy_sol = 0.03  # 매수당 0.03 SOL (~$4.5)
        self.scan_interval = 60   # 1분마다 (가장 빠름)
        self.max_positions = 5    # 동시 보유 최대 5개

        # 진입 필터
        self.min_progress_pct = 80   # 본딩커브 80%+
        self.max_progress_pct = 97   # 너무 임박해도 안 됨 (이미 늦음)
        self.min_volume_1h_sol = 1.0 # 1시간 거래량 1 SOL+
        self.min_unique_traders = 15 # 매수자 분산
        self.min_buy_ratio = 0.55    # 매수가 55%+ (매도 우세 X)

        # 청산 파라미터
        self.stop_loss_pct = -50          # -50% 손절
        self.timeout_hours = 6            # 6시간 내 졸업 못 하면 손절
        # 다단계 익절 (조기 +30% 추가)
        self.tp_levels = [
            (30, 30),   # +30% → 30% 청산 (조기 익절)
            (50, 30),   # +50% → 30% 청산
            (100, 25),  # +100% → 25% 청산
            (200, 100), # +200% → 나머지 전부
        ]
        # 트레일링 스탑
        self.trailing_activate_pct = 50   # +50% 한 번 도달 시 활성화
        self.trailing_drop_pct = 30       # peak 대비 -30% 청산

        # 일일 손실 한도
        self.daily_loss_limit_pct = 50  # -50% 도달 시 매매 중지
        self.daily_pnl = 0
        self.last_reset_date = None

        # 상태
        self.scan_count = 0
        self.positions: dict[str, dict] = {}
        self.recent_buys: dict[str, int] = {}  # mint → 마지막 매수 시각 (중복 방지)

        # 포지션 영구 저장
        self.positions_file = _persistent_dir() / "pumpfun_positions.json"

        self._init_db()

    # ──────────────────────────────────────────────
    # 포지션 영구 저장
    # ──────────────────────────────────────────────

    def _save_positions(self):
        try:
            self.positions_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.positions_file, "w", encoding="utf-8") as f:
                json.dump(self.positions, f, ensure_ascii=False, default=str)
        except Exception as e:
            logger.warning(f"PumpFun positions 저장 실패: {e}")

    def _load_positions(self):
        try:
            if not self.positions_file.exists():
                return
            with open(self.positions_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.positions = loaded
                logger.info(f"  📂 PumpFun 포지션 {len(self.positions)}개 복원")
        except Exception as e:
            logger.warning(f"PumpFun positions 로드 실패: {e}")

    async def _restore_from_db(self):
        """DB에서 미청산 포지션 복원"""
        try:
            cur = self.db.conn.execute(
                """SELECT token_mint, symbol, amount_sol, token_amount, progress_pct, timestamp
                   FROM pumpfun_trades
                   WHERE side='BUY'
                   AND token_mint NOT IN (
                       SELECT DISTINCT token_mint FROM pumpfun_trades WHERE side='SELL'
                   )
                   ORDER BY timestamp DESC"""
            )
            rows = cur.fetchall()
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
                    "name": "",
                    "entry_sol": amount_sol,
                    "token_amount_raw": int(token_amount_ui * (10 ** decimals)),
                    "token_amount_ui": token_amount_ui,
                    "decimals": decimals,
                    "entry_price_sol": amount_sol / token_amount_ui,
                    "entry_progress_pct": row["progress_pct"] or 0,
                    "entry_time": int(time.time()),
                    "tp_done": [False, False, False, False],
                    "graduated": False,
                    "peak_pnl_pct": 0,
                    "trailing_active": False,
                }
                restored += 1
                logger.info(f"  ♻️  PumpFun DB 복원: ${row['symbol']} ({mint[:8]}..)")

            if restored > 0:
                self._save_positions()
                logger.info(f"  ✅ PumpFun {restored}개 미청산 포지션 복원")
        except Exception as e:
            logger.warning(f"PumpFun DB 복원 실패: {e}")

    def _init_db(self):
        try:
            self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS pumpfun_trades (
                    id INTEGER PRIMARY KEY,
                    timestamp TEXT,
                    mode TEXT,
                    side TEXT,
                    token_mint TEXT,
                    symbol TEXT,
                    progress_pct REAL,
                    amount_sol REAL,
                    token_amount REAL,
                    pnl_pct REAL,
                    signature TEXT,
                    note TEXT
                )
            """)
            self.db.conn.commit()
        except Exception as e:
            logger.warning(f"pumpfun_trades 테이블 생성 실패: {e}")

    # ──────────────────────────────────────────────
    # 메인 사이클
    # ──────────────────────────────────────────────

    async def run_cycle(self):
        """1회 사이클: 졸업 임박 토큰 스나이프 + 보유 관리"""
        self.scan_count += 1
        self._reset_daily_if_needed()

        logger.info(f"💎 [PumpFun #{self.scan_count}] 스캔 시작 (mode: {self.mode})")

        try:
            # 0. 일일 손실 한도 체크
            if self.daily_pnl <= -self.daily_loss_limit_pct:
                if self.scan_count % 60 == 0:
                    logger.info(f"  🛑 일일 손실 한도 ({self.daily_pnl:.1f}%) — 매매 중지")
                await self._check_exits()  # 청산은 계속
                return

            # 1. 보유 포지션 청산 체크 (먼저)
            await self._check_exits()

            # 2. 동시 포지션 한도 체크
            if len(self.positions) >= self.max_positions:
                logger.info(f"  📊 보유 포지션 {len(self.positions)}개 (한도 {self.max_positions})")
                return

            # 3. 졸업 임박 토큰 수집
            candidates = await self.pump.get_almost_graduated(limit=50)
            logger.info(f"  📊 본딩커브 80%+ 토큰: {len(candidates)}개")

            # 4. 필터링 (진행률, 거래량, 홀더)
            filtered = await self._filter_candidates(candidates)
            logger.info(f"  ✅ 필터 통과: {len(filtered)}개")

            # 5. 안전성 검증 + 매수
            buys_made = 0
            for token in filtered[:5]:  # 사이클당 최대 5건 시도
                if buys_made >= 2:
                    break  # 사이클당 최대 2건 매수
                if await self._try_buy(token):
                    buys_made += 1
                await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"PumpFun 사이클 에러: {e}", exc_info=True)

    def _reset_daily_if_needed(self):
        from datetime import date
        today = date.today()
        if self.last_reset_date != today:
            if self.last_reset_date is not None:
                logger.info(f"  📅 일일 PnL 리셋: {self.daily_pnl:+.1f}%")
            self.daily_pnl = 0
            self.last_reset_date = today

    # ──────────────────────────────────────────────
    # 필터링
    # ──────────────────────────────────────────────

    async def _filter_candidates(self, candidates: list[dict]) -> list[dict]:
        """진입 가능 후보만 필터"""
        filtered = []

        for c in candidates:
            try:
                mint = c.get("mint")
                progress = c.get("progress_pct", 0)

                # 1. 진행률 범위
                if progress < self.min_progress_pct or progress > self.max_progress_pct:
                    continue

                # 2. 이미 보유 중
                if mint in self.positions:
                    continue

                # 3. 최근 매수 시도한 토큰 (5분 쿨다운)
                if mint in self.recent_buys:
                    if time.time() - self.recent_buys[mint] < 300:
                        continue

                # 4. 거래량 + 매수 비율 체크
                volume = await self.pump.get_volume_recent(mint, minutes=60)
                if volume["volume_sol"] < self.min_volume_1h_sol:
                    continue
                if volume["unique_traders"] < self.min_unique_traders:
                    continue
                if volume["buy_ratio"] < self.min_buy_ratio:
                    continue

                # 5. 토큰 정보 추가
                c["volume_1h"] = volume
                c["score"] = (
                    progress / 100  # 진행률
                    + volume["buy_ratio"]  # 매수 우세
                    + min(volume["unique_traders"] / 50, 1.0)  # 분산
                )
                filtered.append(c)

                await asyncio.sleep(0.3)  # rate limit 보호
            except Exception as e:
                logger.debug(f"필터 에러 {c.get('mint', '?')[:10]}: {e}")

        # 점수 순 정렬
        filtered.sort(key=lambda x: x["score"], reverse=True)
        return filtered

    # ──────────────────────────────────────────────
    # 매수
    # ──────────────────────────────────────────────

    async def _try_buy(self, token: dict) -> bool:
        """안전 검증 → 매수"""
        mint = token["mint"]
        symbol = token.get("symbol", "?")
        progress = token.get("progress_pct", 0)

        # 안전성 5중 검증 (Pump.fun 토큰은 상대적으로 안전하지만 검증)
        report = await self.safety.check_token(mint)
        if not report["passed"]:
            reasons = " / ".join(report["fail_reasons"][:2])
            logger.info(f"  🚫 ${symbol} 안전 차단: {reasons}")
            self.recent_buys[mint] = int(time.time())
            return False

        # 잔고 체크
        sol_balance = await self.client.get_sol_balance()
        if sol_balance < self.max_buy_sol + 0.01:
            logger.info(f"  💸 SOL 잔고 부족: {sol_balance:.4f}")
            return False

        buy_amount = self.max_buy_sol
        volume = token.get("volume_1h", {})

        logger.info(
            f"  🛒 [{self.mode.upper()}] BUY ${symbol} {buy_amount:.4f} SOL "
            f"(progress {progress:.1f}%, 1H vol {volume.get('volume_sol', 0):.2f} SOL, "
            f"buy ratio {volume.get('buy_ratio', 0):.0%})"
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
                    logger.warning(f"  ❌ 매수 실패")
                    self.recent_buys[mint] = int(time.time())
                    return False
                signature = result["signature"]
                output_amount = result["output_amount"]
                logger.info(f"  ✅ 체결: {signature[:20]}...")
            except Exception as e:
                logger.error(f"  ❌ 매수 에러: {e}")
                return False
        else:
            # paper
            signature = "PAPER_" + str(int(time.time()))
            decimals = report["details"].get("decimals", 6)
            # 가상 진입가
            output_amount = int(buy_amount * 1e9 * (10 ** decimals) / 1000)

        # 포지션 등록
        decimals = report["details"].get("decimals", 6)
        token_amount_ui = output_amount / (10 ** decimals)

        self.positions[mint] = {
            "mint": mint,
            "symbol": symbol,
            "name": token.get("name", ""),
            "entry_sol": buy_amount,
            "token_amount_raw": output_amount,
            "token_amount_ui": token_amount_ui,
            "decimals": decimals,
            "entry_price_sol": buy_amount / token_amount_ui if token_amount_ui > 0 else 0,
            "entry_progress_pct": progress,
            "entry_time": int(time.time()),
            "tp_done": [False, False, False, False],  # 4단계로 변경
            "graduated": False,
            # 트레일링 스탑
            "peak_pnl_pct": 0,
            "trailing_active": False,
        }
        self.recent_buys[mint] = int(time.time())

        # 영구 저장
        self._save_positions()

        # DB
        try:
            self.db.conn.execute(
                """INSERT INTO pumpfun_trades
                (timestamp, mode, side, token_mint, symbol, progress_pct,
                 amount_sol, token_amount, pnl_pct, signature, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(KST).isoformat(),
                    self.mode, "BUY", mint, symbol, progress,
                    buy_amount, token_amount_ui, 0, signature,
                    f"vol_1h={volume.get('volume_sol', 0):.2f} buyratio={volume.get('buy_ratio', 0):.2f}",
                ),
            )
            self.db.conn.commit()
        except Exception:
            pass

        # 텔레그램
        try:
            await self.telegram.send(
                f"💎 <b>PumpFun {self.mode.upper()}: BUY</b>\n\n"
                f"<b>토큰:</b> ${symbol}\n"
                f"<b>진행률:</b> {progress:.1f}% (졸업 임박)\n"
                f"<b>매수:</b> {buy_amount:.4f} SOL\n"
                f"<b>1H 거래량:</b> {volume.get('volume_sol', 0):.2f} SOL\n"
                f"<b>매수 비율:</b> {volume.get('buy_ratio', 0):.0%}\n"
                f"<b>고유 매수자:</b> {volume.get('unique_traders', 0)}\n"
                f"<b>주소:</b> <code>{mint[:8]}...{mint[-6:]}</code>"
            )
        except Exception:
            pass

        return True

    # ──────────────────────────────────────────────
    # 청산
    # ──────────────────────────────────────────────

    async def _check_exits(self):
        """보유 포지션 청산 조건 체크"""
        if not self.positions:
            return

        for mint in list(self.positions.keys()):
            try:
                await self._check_position(mint)
            except Exception as e:
                logger.warning(f"포지션 체크 실패 {mint[:10]}: {e}")

    async def _check_position(self, mint: str):
        """단일 포지션 청산 결정"""
        pos = self.positions.get(mint)
        if not pos:
            return

        # 1. 졸업 여부 + 가격 체크
        token_info = await self.pump.get_token_info(mint)

        if token_info and token_info.get("complete"):
            # 졸업 완료!
            if not pos["graduated"]:
                pos["graduated"] = True
                logger.info(f"  🎓 ${pos['symbol']} 졸업 완료! Raydium 마이그레이션됨")
                try:
                    await self.telegram.send(
                        f"🎓 <b>${pos['symbol']} 졸업!</b>\n\n"
                        f"Raydium DEX 마이그레이션 완료\n"
                        f"펌프 청산 시작..."
                    )
                except Exception:
                    pass

        # 현재 가격
        current_price = await self._get_token_price_sol(mint, pos["decimals"])
        if not current_price or pos["entry_price_sol"] <= 0:
            return

        pnl_pct = (current_price - pos["entry_price_sol"]) / pos["entry_price_sol"] * 100

        # peak 갱신 (트레일링 스탑용)
        if pnl_pct > pos.get("peak_pnl_pct", 0):
            pos["peak_pnl_pct"] = pnl_pct
        peak = pos.get("peak_pnl_pct", 0)

        # 2. 손절
        if pnl_pct <= self.stop_loss_pct:
            await self._sell(mint, 100, f"💸 손절 {pnl_pct:.1f}%")
            return

        # 3. 트레일링 스탑 (+50% 한 번 찍은 경우만 활성)
        if peak >= self.trailing_activate_pct:
            pos["trailing_active"] = True
            drop_from_peak = peak - pnl_pct
            if drop_from_peak >= self.trailing_drop_pct:
                await self._sell(
                    mint, 100,
                    f"🎯 트레일링 청산 (peak +{peak:.0f}% → 현재 {pnl_pct:+.0f}%)"
                )
                return

        # 4. 익절 단계
        for i, (target_pct, sell_pct) in enumerate(self.tp_levels):
            if pos["tp_done"][i]:
                continue
            if pnl_pct >= target_pct:
                stage_name = ["조기익절", "1단계", "2단계", "전량익절"][i]
                await self._sell(
                    mint, sell_pct,
                    f"💰 +{pnl_pct:.0f}% {stage_name} ({sell_pct}%)"
                )
                pos["tp_done"][i] = True
                # 마지막 단계면 전부 청산
                if i == len(self.tp_levels) - 1:
                    self.positions.pop(mint, None)
                return

        # 5. 졸업 타임아웃
        elapsed_hours = (int(time.time()) - pos["entry_time"]) / 3600
        if elapsed_hours >= self.timeout_hours and not pos["graduated"]:
            await self._sell(mint, 100, f"⏰ 졸업 실패 타임아웃 ({elapsed_hours:.1f}h)")
            return

    async def _get_token_price_sol(self, mint: str, decimals: int) -> Optional[float]:
        """1 토큰당 SOL 가격"""
        try:
            sample = 10 ** decimals
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

    async def _sell(self, mint: str, percent: int, reason: str):
        """청산"""
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
                    logger.warning(f"  ❌ 매도 실패")
                    return
            except Exception as e:
                logger.error(f"  ❌ 매도 에러: {e}")
                return
        else:
            current = await self._get_token_price_sol(mint, pos["decimals"])
            sol_received = sell_raw * (current or 0) / (10 ** pos["decimals"])

        # PnL
        partial_entry = pos["entry_sol"] * percent / 100
        pnl_pct = ((sol_received - partial_entry) / partial_entry * 100) if partial_entry else 0
        self.daily_pnl += pnl_pct * (percent / 100)  # 부분 매도 비례

        # DB
        try:
            self.db.conn.execute(
                """INSERT INTO pumpfun_trades
                (timestamp, mode, side, token_mint, symbol, progress_pct,
                 amount_sol, token_amount, pnl_pct, signature, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(KST).isoformat(),
                    self.mode, "SELL", mint, symbol, pos.get("entry_progress_pct", 0),
                    sol_received, sell_raw / (10 ** pos["decimals"]),
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
                f"{emoji} <b>PumpFun {self.mode.upper()}: SELL {percent}%</b>\n\n"
                f"<b>토큰:</b> ${symbol}\n"
                f"<b>사유:</b> {reason}\n"
                f"<b>받음:</b> {sol_received:.4f} SOL\n"
                f"<b>PnL:</b> {pnl_pct:+.2f}%\n"
                f"<b>일일 PnL:</b> {self.daily_pnl:+.1f}%"
            )
        except Exception:
            pass

        # 포지션 정리
        if percent >= 100:
            self.positions.pop(mint, None)
        else:
            pos["token_amount_raw"] -= sell_raw

        # 영구 저장
        self._save_positions()

    # ──────────────────────────────────────────────
    # 초기화
    # ──────────────────────────────────────────────

    async def initialize(self):
        sol = await self.client.get_sol_balance()
        s = self.client.get_status()

        # 포지션 복원: JSON → DB 백업
        self._load_positions()
        await self._restore_from_db()

        logger.info(
            f"💎 PumpFun 봇 초기화\n"
            f"  지갑: {s['address_short']}\n"
            f"  SOL: {sol:.4f}\n"
            f"  모드: {self.mode}\n"
            f"  복원된 포지션: {len(self.positions)}개"
        )
        positions_msg = (
            f"\n<b>복원된 포지션:</b> {len(self.positions)}개"
            if self.positions else ""
        )
        try:
            await self.telegram.send(
                f"💎 <b>PumpFun 졸업 스나이퍼 시작</b>\n\n"
                f"<b>지갑:</b> <code>{s['address_short']}</code>\n"
                f"<b>SOL 잔고:</b> {sol:.4f}\n"
                f"<b>모드:</b> {self.mode}\n"
                f"<b>주기:</b> {self.scan_interval}초마다\n"
                f"<b>전략:</b> 본딩커브 80~97% → 졸업 펌프 노림"
                f"{positions_msg}"
            )
        except Exception:
            pass

    async def close(self):
        await self.pump.close()
