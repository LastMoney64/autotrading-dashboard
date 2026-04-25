"""
WalletDiscovery — 스마트머니 지갑 자동 발굴 시스템

전략:
1. 트렌딩 토큰 (DexScreener) 추출
2. 그 토큰을 초기 매수한 지갑들 조회 (Helius)
3. 각 지갑의 30일 거래 내역 분석
4. 수익률 높은 지갑 선별 → wallets.py에 자동 추가

검증 기준:
- 30일 거래 횟수 ≥ 10건
- win rate ≥ 55%
- 평균 수익률 ≥ 30%
- 활성 (최근 7일 거래)

자동 실행: 주 1회 (일요일)
"""

import logging
import asyncio
import time
from typing import Optional
import aiohttp

from solana_bot.shared import HeliusClient

logger = logging.getLogger(__name__)


class WalletDiscovery:
    """스마트머니 지갑 자동 발굴"""

    def __init__(self, helius_client: HeliusClient):
        self.helius = helius_client
        self._session: Optional[aiohttp.ClientSession] = None

        # 발굴 기준
        self.min_trades_30d = 10
        self.min_win_rate = 0.55
        self.min_avg_pnl_pct = 30
        self.max_wallets_to_check = 50  # 한 번에 검사할 지갑 수
        self.max_new_wallets = 5         # 한 번에 추가할 지갑 수

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ──────────────────────────────────────────────
    # 1. 트렌딩 토큰 후보 추출
    # ──────────────────────────────────────────────

    async def get_trending_tokens(self, limit: int = 20) -> list[str]:
        """
        DexScreener 트렌딩 토큰 추출 (스마트머니가 사고 있을 가능성 높음)

        Returns: [token_mint, ...]
        """
        try:
            session = await self._get_session()
            url = "https://api.dexscreener.com/token-boosts/latest/v1"
            async with session.get(url) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                if not isinstance(data, list):
                    return []

                tokens = []
                for b in data:
                    if b.get("chainId") == "solana":
                        addr = b.get("tokenAddress")
                        if addr:
                            tokens.append(addr)
                    if len(tokens) >= limit:
                        break
                return tokens
        except Exception as e:
            logger.warning(f"트렌딩 토큰 조회 실패: {e}")
            return []

    # ──────────────────────────────────────────────
    # 2. 토큰 매수자 조회
    # ──────────────────────────────────────────────

    async def get_token_early_buyers(self, mint: str, limit: int = 30) -> list[str]:
        """
        토큰의 초기 매수자 지갑들

        Returns: [wallet_address, ...]
        """
        try:
            # Helius API로 토큰 트랜잭션 조회
            url = f"https://api.helius.xyz/v0/addresses/{mint}/transactions"
            params = {
                "api-key": self.helius.api_key,
                "limit": min(limit, 100),
                "type": "SWAP",
            }
            session = await self._get_session()
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return []
                txs = await resp.json()
                if not isinstance(txs, list):
                    return []

                buyers = set()
                for tx in txs:
                    transfers = tx.get("tokenTransfers", []) or []
                    for t in transfers:
                        # 토큰 받은 사람 = 매수자
                        if t.get("mint") == mint and t.get("toUserAccount"):
                            buyers.add(t["toUserAccount"])
                return list(buyers)
        except Exception as e:
            logger.debug(f"토큰 매수자 조회 실패 {mint[:10]}: {e}")
            return []

    # ──────────────────────────────────────────────
    # 3. 지갑 수익률 분석
    # ──────────────────────────────────────────────

    async def analyze_wallet(self, wallet: str) -> Optional[dict]:
        """
        지갑의 30일 거래 분석

        Returns: {
            "address": str,
            "trades_30d": int,
            "win_rate": float,
            "avg_pnl_pct": float,
            "active_recent": bool,
            "qualifies": bool,
        }
        """
        try:
            txs = await self.helius.get_wallet_transactions(wallet, limit=100)
            if not txs:
                return None

            now = int(time.time())
            cutoff_30d = now - 30 * 86400
            cutoff_7d = now - 7 * 86400

            wallet_lower = wallet.lower()
            recent_active = False

            # 토큰별 매수/매도 추적
            token_positions: dict[str, dict] = {}
            # mint → {sol_in, sol_out, completed_pnl_pct}

            for tx in txs:
                ts = tx.get("timestamp", 0)
                if ts < cutoff_30d:
                    continue
                if ts >= cutoff_7d:
                    recent_active = True

                if tx.get("type") != "SWAP":
                    continue

                transfers = tx.get("tokenTransfers", []) or []
                sol_in = 0
                sol_out = 0
                token_in: dict = {}
                token_out: dict = {}

                for t in transfers:
                    from_addr = (t.get("fromUserAccount") or "").lower()
                    to_addr = (t.get("toUserAccount") or "").lower()
                    mint = t.get("mint", "")
                    amt = float(t.get("tokenAmount", 0) or 0)

                    if from_addr == wallet_lower:
                        if mint == "So11111111111111111111111111111111111111112":
                            sol_out += amt
                        else:
                            token_out = {"mint": mint, "amount": amt}
                    if to_addr == wallet_lower:
                        if mint == "So11111111111111111111111111111111111111112":
                            sol_in += amt
                        else:
                            token_in = {"mint": mint, "amount": amt}

                # 매수: SOL 보내고 토큰 받음
                if sol_out > 0 and token_in:
                    mint = token_in["mint"]
                    if mint not in token_positions:
                        token_positions[mint] = {"buy_sol": 0, "buy_tokens": 0, "sell_sol": 0, "sell_tokens": 0}
                    token_positions[mint]["buy_sol"] += sol_out
                    token_positions[mint]["buy_tokens"] += token_in["amount"]

                # 매도: 토큰 보내고 SOL 받음
                elif sol_in > 0 and token_out:
                    mint = token_out["mint"]
                    if mint not in token_positions:
                        token_positions[mint] = {"buy_sol": 0, "buy_tokens": 0, "sell_sol": 0, "sell_tokens": 0}
                    token_positions[mint]["sell_sol"] += sol_in
                    token_positions[mint]["sell_tokens"] += token_out["amount"]

            # 완료된 거래 (매수 + 매도 둘 다 있는 토큰)만 PnL 계산
            wins = 0
            losses = 0
            pnl_list = []

            for mint, pos in token_positions.items():
                if pos["buy_sol"] > 0 and pos["sell_sol"] > 0:
                    pnl_pct = (pos["sell_sol"] - pos["buy_sol"]) / pos["buy_sol"] * 100
                    pnl_list.append(pnl_pct)
                    if pnl_pct > 0:
                        wins += 1
                    else:
                        losses += 1

            total_completed = wins + losses
            if total_completed < 3:
                return {
                    "address": wallet,
                    "trades_30d": total_completed,
                    "win_rate": 0,
                    "avg_pnl_pct": 0,
                    "active_recent": recent_active,
                    "qualifies": False,
                    "reason": "거래 부족",
                }

            win_rate = wins / total_completed
            avg_pnl = sum(pnl_list) / len(pnl_list) if pnl_list else 0

            qualifies = (
                total_completed >= self.min_trades_30d
                and win_rate >= self.min_win_rate
                and avg_pnl >= self.min_avg_pnl_pct
                and recent_active
            )

            return {
                "address": wallet,
                "trades_30d": total_completed,
                "wins": wins,
                "losses": losses,
                "win_rate": round(win_rate, 3),
                "avg_pnl_pct": round(avg_pnl, 2),
                "active_recent": recent_active,
                "qualifies": qualifies,
            }
        except Exception as e:
            logger.debug(f"지갑 {wallet[:10]} 분석 실패: {e}")
            return None

    # ──────────────────────────────────────────────
    # 4. 발굴 + 추가
    # ──────────────────────────────────────────────

    async def discover_and_add(self) -> dict:
        """
        전체 발굴 프로세스:
        1. 트렌딩 토큰 → 매수자 후보
        2. 각 후보 30일 분석
        3. 기준 통과한 지갑 → wallets.py에 추가

        Returns: {
            "checked": int,
            "qualified": int,
            "added": int,
            "new_wallets": [{"address": str, "stats": dict}, ...]
        }
        """
        from solana_bot.smart_money_bot.wallets import TRACKED_WALLETS

        existing_addrs = {w["address"] for w in TRACKED_WALLETS}

        # 1. 트렌딩 토큰
        tokens = await self.get_trending_tokens(limit=15)
        logger.info(f"  📊 트렌딩 토큰 {len(tokens)}개")

        # 2. 매수자 후보 수집 (중복 제거)
        candidates: set[str] = set()
        for token in tokens[:10]:
            try:
                buyers = await self.get_token_early_buyers(token, limit=15)
                for b in buyers:
                    if b not in existing_addrs:
                        candidates.add(b)
                if len(candidates) >= self.max_wallets_to_check:
                    break
            except Exception:
                continue
            await asyncio.sleep(0.5)

        candidates = list(candidates)[:self.max_wallets_to_check]
        logger.info(f"  🔍 후보 지갑 {len(candidates)}개 분석 시작")

        # 3. 각 지갑 분석
        qualified = []
        for i, wallet in enumerate(candidates):
            try:
                stats = await self.analyze_wallet(wallet)
                if stats and stats.get("qualifies"):
                    qualified.append(stats)
                    logger.info(
                        f"  ✅ 검증 통과: {wallet[:10]}... "
                        f"({stats['trades_30d']}회, "
                        f"승률 {stats['win_rate']:.0%}, "
                        f"평균 PnL {stats['avg_pnl_pct']:.0f}%)"
                    )
                if len(qualified) >= self.max_new_wallets:
                    break
            except Exception:
                continue
            await asyncio.sleep(0.5)
            if (i + 1) % 10 == 0:
                logger.info(f"  진행: {i+1}/{len(candidates)} (통과 {len(qualified)})")

        # 4. wallets.py에 추가
        new_wallets_added = []
        for stats in qualified:
            new_wallet = {
                "address": stats["address"],
                "tag": "auto_discovered",
                "win_rate": stats["win_rate"],
                "weight": 1.0,
                "active": True,
            }
            TRACKED_WALLETS.append(new_wallet)
            new_wallets_added.append({
                "address": stats["address"],
                "stats": stats,
            })

        return {
            "checked": len(candidates),
            "qualified": len(qualified),
            "added": len(new_wallets_added),
            "new_wallets": new_wallets_added,
        }
