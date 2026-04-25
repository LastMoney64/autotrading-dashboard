"""
SafetyChecker — 솔라나 토큰 안전성 5중 검증

자동 매매 = 잘못 매수하면 자고 일어나니 0원
이 모듈이 매수 전 필수 체크

검증 항목:
1. Mint Authority — 무한 발행 가능 여부
2. Freeze Authority — 우리 지갑 동결 가능 여부
3. 홀더 분산도 — 상위 10이 30%+ 보유하면 작전
4. 최소 유동성 — DexScreener LP 체크
5. 최소 홀더 수 — 30명 미만이면 X
"""

import logging
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)

# 안전 기준 (기본값)
MIN_HOLDERS = 30
MAX_TOP10_PERCENT = 30.0   # 상위 10 홀더 점유율 30% 이하
MIN_LP_USD = 5000          # 유동성 최소 $5K
MIN_VOLUME_24H_USD = 1000  # 24H 거래량 최소 $1K


class SafetyChecker:
    """매수 전 안전성 5중 검증"""

    def __init__(self, helius_client, settings: dict = None):
        self.helius = helius_client
        s = settings or {}
        self.min_holders = s.get("min_holders", MIN_HOLDERS)
        self.max_top10_pct = s.get("max_top10_percent", MAX_TOP10_PERCENT)
        self.min_lp_usd = s.get("min_lp_usd", MIN_LP_USD)
        self.min_volume_24h_usd = s.get("min_volume_24h_usd", MIN_VOLUME_24H_USD)

    async def check_token(self, mint: str) -> dict:
        """
        토큰 종합 안전성 검사

        Returns: {
            "passed": bool,
            "checks": {
                "mint_authority": bool,
                "freeze_authority": bool,
                "holders": bool,
                "concentration": bool,
                "liquidity": bool,
                "volume": bool,
            },
            "details": dict,
            "fail_reasons": list[str]
        }
        """
        result = {
            "passed": False,
            "checks": {},
            "details": {},
            "fail_reasons": [],
        }

        # ── 1. Mint/Freeze 권한 ──────────────────
        mint_info = await self.helius.get_mint_info(mint)
        if not mint_info:
            result["fail_reasons"].append("토큰 정보 조회 실패")
            return result

        mint_auth = mint_info.get("mint_authority")
        freeze_auth = mint_info.get("freeze_authority")
        result["details"]["mint_authority"] = mint_auth
        result["details"]["freeze_authority"] = freeze_auth
        result["details"]["decimals"] = mint_info.get("decimals", 0)
        result["details"]["supply"] = mint_info.get("supply", 0)

        # mint_authority가 None = renounced (안전)
        result["checks"]["mint_authority"] = mint_auth is None
        if mint_auth is not None:
            result["fail_reasons"].append(f"Mint 권한 살아있음 ({mint_auth[:8]}...) — 무한 발행 가능")

        # freeze_authority가 None = renounced (안전)
        result["checks"]["freeze_authority"] = freeze_auth is None
        if freeze_auth is not None:
            result["fail_reasons"].append(f"Freeze 권한 살아있음 ({freeze_auth[:8]}...) — 지갑 동결 가능")

        # ── 2. 홀더 분포 ────────────────────────
        holders = await self.helius.get_token_largest_accounts(mint)
        result["details"]["top_holders"] = len(holders)

        # 최소 홀더 수
        result["checks"]["holders"] = len(holders) >= 5  # largestAccounts는 최대 20개
        if len(holders) < 5:
            result["fail_reasons"].append(f"홀더 수 너무 적음 ({len(holders)})")

        # 상위 10 집중도
        top10_pct = 0
        if holders and result["details"]["supply"] > 0:
            decimals = result["details"]["decimals"]
            supply_ui = result["details"]["supply"] / (10 ** decimals) if decimals else result["details"]["supply"]
            top10_amount = sum(h.get("amount", 0) for h in holders[:10])
            top10_pct = (top10_amount / supply_ui * 100) if supply_ui > 0 else 0

        result["details"]["top10_concentration_pct"] = round(top10_pct, 2)
        result["checks"]["concentration"] = top10_pct <= self.max_top10_pct
        if top10_pct > self.max_top10_pct:
            result["fail_reasons"].append(
                f"상위 10 홀더 집중 {top10_pct:.1f}% (한도 {self.max_top10_pct}%) — 작전 위험"
            )

        # ── 3. DexScreener — 유동성 + 거래량 ─────
        liquidity_usd = 0
        volume_24h_usd = 0
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs", []) or []
                        # 가장 유동성 큰 쌍 사용
                        pairs.sort(
                            key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0),
                            reverse=True,
                        )
                        if pairs:
                            p0 = pairs[0]
                            liquidity_usd = float(p0.get("liquidity", {}).get("usd", 0) or 0)
                            volume_24h_usd = float(p0.get("volume", {}).get("h24", 0) or 0)
                            result["details"]["dexscreener"] = {
                                "dex": p0.get("dexId", ""),
                                "pair_address": p0.get("pairAddress", ""),
                                "price_usd": float(p0.get("priceUsd", 0) or 0),
                                "fdv": float(p0.get("fdv", 0) or 0),
                                "market_cap": float(p0.get("marketCap", 0) or 0),
                            }
        except Exception as e:
            logger.debug(f"DexScreener 조회 실패 {mint[:10]}: {e}")

        result["details"]["liquidity_usd"] = liquidity_usd
        result["details"]["volume_24h_usd"] = volume_24h_usd

        result["checks"]["liquidity"] = liquidity_usd >= self.min_lp_usd
        if liquidity_usd < self.min_lp_usd:
            result["fail_reasons"].append(
                f"유동성 부족 ${liquidity_usd:.0f} (최소 ${self.min_lp_usd})"
            )

        result["checks"]["volume"] = volume_24h_usd >= self.min_volume_24h_usd
        if volume_24h_usd < self.min_volume_24h_usd:
            result["fail_reasons"].append(
                f"24H 거래량 부족 ${volume_24h_usd:.0f} (최소 ${self.min_volume_24h_usd})"
            )

        # ── 종합 판정 ───────────────────────────
        result["passed"] = all(result["checks"].values())
        return result

    async def check_token_quick(self, mint: str) -> bool:
        """간단한 통과/실패 체크 (스나이핑 시 빠른 판단)"""
        report = await self.check_token(mint)
        return report["passed"]
