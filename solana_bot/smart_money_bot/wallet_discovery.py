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

from solana_bot.shared import HeliusClient, GmgnClient

logger = logging.getLogger(__name__)


class WalletDiscovery:
    """스마트머니 지갑 자동 발굴"""

    def __init__(
        self,
        helius_client: HeliusClient,
        gmgn_client: Optional[GmgnClient] = None,
    ):
        self.helius = helius_client
        self.gmgn = gmgn_client
        self._session: Optional[aiohttp.ClientSession] = None

        # 발굴 기준 (완화 — 더 많은 통과)
        self.min_trades_30d = 5         # 10 → 5 (짧은 히스토리도 OK)
        self.min_win_rate = 0.45         # 55% → 45%
        self.min_avg_pnl_pct = 15        # 30% → 15%
        self.max_wallets_to_check = 100  # 50 → 100 (더 많이 검사)
        self.max_new_wallets = 10         # 5 → 10 (더 많이 추가)

        # GMGN 검증된 지갑 우선 추가 (최대)
        self.max_gmgn_direct_add = 15    # GMGN 검증된 지갑은 직접 추가 (검증 스킵)

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
        tokens = []
        try:
            session = await self._get_session()
            # 1. DexScreener boosts (트렌딩)
            url = "https://api.dexscreener.com/token-boosts/latest/v1"
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list):
                        for b in data:
                            if b.get("chainId") == "solana":
                                addr = b.get("tokenAddress")
                                if addr and addr not in tokens:
                                    tokens.append(addr)
                            if len(tokens) >= limit:
                                break

            # 2. DexScreener token-profiles (보충)
            if len(tokens) < limit:
                url2 = "https://api.dexscreener.com/token-profiles/latest/v1"
                async with session.get(url2) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, list):
                            for p in data:
                                if p.get("chainId") == "solana":
                                    addr = p.get("tokenAddress")
                                    if addr and addr not in tokens:
                                        tokens.append(addr)
                                if len(tokens) >= limit:
                                    break

        except Exception as e:
            logger.warning(f"트렌딩 토큰 조회 실패: {e}")

        return tokens

    async def get_pumpfun_graduated_tokens(self, limit: int = 15) -> list[str]:
        """
        Pump.fun에서 최근 졸업한 토큰 (성공한 밈코인 = 좋은 매수자)

        Returns: [token_mint, ...]
        """
        try:
            session = await self._get_session()
            # Pump.fun API에서 졸업한(complete=true) 코인
            url = "https://frontend-api-v3.pump.fun/coins"
            params = {
                "offset": 0,
                "limit": 100,
                "sort": "market_cap",
                "order": "DESC",
                "includeNsfw": "false",
            }
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                if not isinstance(data, list):
                    return []

                tokens = []
                for c in data:
                    # 졸업 완료한 + 시총 큰 토큰
                    if c.get("complete") and c.get("usd_market_cap", 0) > 100000:
                        mint = c.get("mint")
                        if mint:
                            tokens.append(mint)
                    if len(tokens) >= limit:
                        break
                return tokens
        except Exception as e:
            logger.debug(f"Pump.fun 졸업 토큰 조회 실패: {e}")
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

    # ──────────────────────────────────────────────
    # 4-A. GMGN 검증된 스마트머니 직접 가져오기 (NEW)
    # ──────────────────────────────────────────────

    async def discover_from_gmgn(self) -> list[dict]:
        """
        GMGN 알고리즘이 이미 검증한 스마트머니 지갑 가져오기

        - smartmoney 거래 (smart_degen 태그)
        - kol 거래 (KOL 태그)
        매수 거래만 필터링 → 매수 활동 활발한 지갑 우선

        Returns: [
            {"address": str, "tag": str, "name": str, "trades": int}, ...
        ]
        """
        if not self.gmgn:
            return []

        all_wallets: dict[str, dict] = {}

        # 1. Smart Money 매수 거래 (가장 강한 시그널)
        try:
            sm_trades = await self.gmgn.get_smart_money_trades(chain="sol", limit=200)
            sm_wallets = GmgnClient.extract_buy_wallets(sm_trades)
            for w in sm_wallets:
                all_wallets[w["address"]] = w
            logger.info(f"  🎯 GMGN Smart Money: 거래 {len(sm_trades)}건 → 지갑 {len(sm_wallets)}개")
        except Exception as e:
            logger.warning(f"GMGN smartmoney 조회 실패: {e}")

        # 2. KOL 매수 거래 (보조 시그널)
        try:
            kol_trades = await self.gmgn.get_kol_trades(chain="sol", limit=200)
            kol_wallets = GmgnClient.extract_buy_wallets(kol_trades)
            for w in kol_wallets:
                # smart_money에 이미 있으면 smart_money 태그 우선
                if w["address"] not in all_wallets:
                    all_wallets[w["address"]] = w
            logger.info(f"  🎤 GMGN KOL: 거래 {len(kol_trades)}건 → 지갑 {len(kol_wallets)}개")
        except Exception as e:
            logger.warning(f"GMGN KOL 조회 실패: {e}")

        # 거래 활성도 순 정렬
        return sorted(all_wallets.values(), key=lambda w: w["trades"], reverse=True)

    # ──────────────────────────────────────────────
    # 4-B. 통합 발굴 + 추가
    # ──────────────────────────────────────────────

    async def discover_and_add(self) -> dict:
        """
        전체 발굴 프로세스 (3단계):

        1단계: GMGN 검증된 지갑 직접 추가 (smart_degen 태그)
        2단계: 트렌딩 토큰 → 매수자 → 30일 분석 (보강)
        3단계: 기준 통과한 지갑 추가

        Returns: {
            "gmgn_added": int,           # GMGN 직접 추가 (검증 스킵)
            "checked": int,              # Helius 분석 검사 수
            "qualified": int,            # 검증 통과
            "added": int,                # 총 추가 지갑 수
            "new_wallets": [...]
        }
        """
        from solana_bot.smart_money_bot.wallets import TRACKED_WALLETS, add_wallet, save_wallets

        existing_addrs = {w["address"] for w in TRACKED_WALLETS}
        new_wallets_added: list[dict] = []

        # ════════════════════════════════════════════════
        # 1단계: GMGN 직접 추가 (검증된 지갑이라 분석 스킵)
        # ════════════════════════════════════════════════
        gmgn_added = 0
        if self.gmgn:
            logger.info("  🎯 GMGN OpenAPI에서 검증된 지갑 가져오기")
            gmgn_wallets = await self.discover_from_gmgn()
            logger.info(f"  📥 GMGN 후보 {len(gmgn_wallets)}개")

            for w in gmgn_wallets[: self.max_gmgn_direct_add]:
                if w["address"] in existing_addrs:
                    continue

                # GMGN이 이미 검증한 지갑 — 즉시 추가
                tag = w.get("tag", "smart_money")
                # 초기 win_rate는 태그 기반 추정
                initial_wr = 0.65 if tag == "smart_degen" else 0.55

                new_wallet = {
                    "address": w["address"],
                    "tag": f"gmgn_{tag}",
                    "win_rate": initial_wr,
                    "weight": 1.0,
                    "active": True,
                }
                if not add_wallet(new_wallet, save=False):
                    continue  # 중복
                existing_addrs.add(w["address"])
                new_wallets_added.append({
                    "address": w["address"],
                    "stats": {
                        "source": "gmgn",
                        "tag": tag,
                        "name": w.get("name", ""),
                        "trades": w.get("trades", 0),
                        "win_rate": initial_wr,
                    },
                })
                gmgn_added += 1
                logger.info(
                    f"  ✅ GMGN 추가: {w['address'][:10]}... "
                    f"(태그={tag}, 거래={w.get('trades',0)}회"
                    f"{', '+w['name'] if w.get('name') else ''})"
                )
        else:
            logger.info("  ⚠️ GMGN 클라이언트 없음 — DexScreener 단독 모드")

        # ════════════════════════════════════════════════
        # 2단계: DexScreener + Pump.fun 토큰 매수자 분석 (보강)
        # ════════════════════════════════════════════════
        # GMGN으로 충분히 채웠으면 스킵
        if gmgn_added >= self.max_new_wallets:
            logger.info(f"  GMGN으로 {gmgn_added}개 추가됨 — Helius 분석 스킵")
            return {
                "gmgn_added": gmgn_added,
                "checked": 0,
                "qualified": gmgn_added,
                "added": gmgn_added,
                "new_wallets": new_wallets_added,
            }

        # 1. 트렌딩 토큰 (다중 소스)
        trending = await self.get_trending_tokens(limit=20)
        graduated = await self.get_pumpfun_graduated_tokens(limit=15)

        # 중복 제거하면서 합침
        all_tokens = list(dict.fromkeys(trending + graduated))
        logger.info(
            f"  📊 토큰 {len(all_tokens)}개 "
            f"(트렌딩 {len(trending)} + 졸업 {len(graduated)})"
        )

        # 2. 매수자 후보 수집 (중복 제거)
        candidates: set[str] = set()
        for token in all_tokens[:25]:
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

        candidates_list = list(candidates)[:self.max_wallets_to_check]
        logger.info(f"  🔍 후보 지갑 {len(candidates_list)}개 분석 시작")

        # 3. 각 지갑 분석
        qualified = []
        remaining_slots = self.max_new_wallets - gmgn_added

        for i, wallet in enumerate(candidates_list):
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
                if len(qualified) >= remaining_slots:
                    break
            except Exception:
                continue
            await asyncio.sleep(0.5)
            if (i + 1) % 10 == 0:
                logger.info(f"  진행: {i+1}/{len(candidates_list)} (통과 {len(qualified)})")

        # 4. 분석 통과한 지갑 추가
        for stats in qualified:
            if stats["address"] in existing_addrs:
                continue
            new_wallet = {
                "address": stats["address"],
                "tag": "auto_discovered",
                "win_rate": stats["win_rate"],
                "weight": 1.0,
                "active": True,
            }
            if not add_wallet(new_wallet, save=False):
                continue  # 중복
            existing_addrs.add(stats["address"])
            new_wallets_added.append({
                "address": stats["address"],
                "stats": stats,
            })

        # 발굴 결과 영구 저장 (재배포 시 보존)
        if new_wallets_added:
            save_wallets()
            logger.info(f"  💾 추적 지갑 영구 저장 완료 ({len(TRACKED_WALLETS)}개)")

        return {
            "gmgn_added": gmgn_added,
            "checked": len(candidates_list),
            "qualified": len(qualified),
            "added": len(new_wallets_added),
            "new_wallets": new_wallets_added,
        }
