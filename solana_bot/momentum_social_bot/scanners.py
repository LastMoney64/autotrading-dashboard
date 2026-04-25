"""
DexScreener + Twitter 멘션 스캐너

Bot 3 데이터 소스:
1. DexScreener — 거래량 급등 토큰
2. FxTwitter — 무료 트위터 데이터 (멘션량)
3. Birdeye — 가격/볼륨 정확도 검증
"""

import logging
import asyncio
import re
import time
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)

DEXSCREENER_API = "https://api.dexscreener.com"
FXTWITTER_API = "https://api.fxtwitter.com"
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
]


class DexScreenerScanner:
    """DexScreener 거래량 급등 토큰 발굴"""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

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

    async def get_trending_solana(self) -> list[dict]:
        """
        솔라나 트렌딩 토큰 (거래량 급등 기준)

        Returns: [{
            "mint": str, "symbol": str, "name": str,
            "price_usd": float,
            "volume_24h_usd": float,
            "volume_1h_usd": float,
            "volume_change_1h_pct": float,
            "price_change_24h_pct": float,
            "liquidity_usd": float,
            "market_cap": float,
            "fdv": float,
            "txn_buys_1h": int, "txn_sells_1h": int,
            "buy_ratio_1h": float,
            "pair_address": str,
        }, ...]
        """
        try:
            session = await self._get_session()

            # token-profiles는 간헐적이라 search로 우회
            # boosts API 활용 (실제 트렌딩)
            url = f"{DEXSCREENER_API}/token-boosts/latest/v1"
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.debug(f"DexScreener boosts HTTP {resp.status}")
                    return []
                boosts = await resp.json()
                if not isinstance(boosts, list):
                    return []

            # 솔라나 토큰만 필터
            sol_boosts = [b for b in boosts if b.get("chainId") == "solana"]

            # 각 토큰의 페어 데이터 조회
            tokens = []
            for b in sol_boosts[:30]:
                token_addr = b.get("tokenAddress")
                if not token_addr:
                    continue
                try:
                    async with session.get(
                        f"{DEXSCREENER_API}/latest/dex/tokens/{token_addr}"
                    ) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                        pairs = data.get("pairs", []) or []
                        if not pairs:
                            continue

                        # 솔라나 페어 중 유동성 가장 큰 것
                        sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
                        if not sol_pairs:
                            continue
                        sol_pairs.sort(
                            key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0),
                            reverse=True,
                        )
                        p0 = sol_pairs[0]

                        tokens.append(self._parse_pair(p0))
                except Exception:
                    continue

                await asyncio.sleep(0.2)  # rate limit

            return [t for t in tokens if t]
        except Exception as e:
            logger.warning(f"DexScreener 트렌딩 조회 실패: {e}")
            return []

    async def search_solana(self, query: str = "SOL") -> list[dict]:
        """검색 기반 (트렌딩 백업)"""
        try:
            session = await self._get_session()
            url = f"{DEXSCREENER_API}/latest/dex/search"
            async with session.get(url, params={"q": query}) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                pairs = data.get("pairs", []) or []
                sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
                return [self._parse_pair(p) for p in sol_pairs[:30] if p]
        except Exception as e:
            logger.debug(f"DexScreener 검색 실패: {e}")
            return []

    def _parse_pair(self, p: dict) -> Optional[dict]:
        try:
            base = p.get("baseToken", {})
            volume_24h = float(p.get("volume", {}).get("h24", 0) or 0)
            volume_1h = float(p.get("volume", {}).get("h1", 0) or 0)
            volume_6h = float(p.get("volume", {}).get("h6", 0) or 0)

            txns_1h = p.get("txns", {}).get("h1", {}) or {}
            buys_1h = int(txns_1h.get("buys", 0) or 0)
            sells_1h = int(txns_1h.get("sells", 0) or 0)
            buy_ratio = buys_1h / max(buys_1h + sells_1h, 1)

            # 거래량 변화율 (1h vs 6h 평균)
            avg_1h_in_6h = volume_6h / 6 if volume_6h else 0
            volume_change = (volume_1h / avg_1h_in_6h * 100) if avg_1h_in_6h > 0 else 0

            return {
                "mint": base.get("address", ""),
                "symbol": base.get("symbol", ""),
                "name": base.get("name", ""),
                "price_usd": float(p.get("priceUsd", 0) or 0),
                "price_change_24h_pct": float(p.get("priceChange", {}).get("h24", 0) or 0),
                "price_change_1h_pct": float(p.get("priceChange", {}).get("h1", 0) or 0),
                "volume_24h_usd": volume_24h,
                "volume_1h_usd": volume_1h,
                "volume_change_1h_pct": volume_change,
                "txn_buys_1h": buys_1h,
                "txn_sells_1h": sells_1h,
                "buy_ratio_1h": buy_ratio,
                "liquidity_usd": float(p.get("liquidity", {}).get("usd", 0) or 0),
                "market_cap": float(p.get("marketCap", 0) or 0),
                "fdv": float(p.get("fdv", 0) or 0),
                "pair_address": p.get("pairAddress", ""),
                "dex": p.get("dexId", ""),
            }
        except Exception:
            return None


class TwitterMentionScanner:
    """FxTwitter 기반 멘션량 추적 (무료)"""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: dict = {}  # symbol → (count, timestamp)
        self._cache_ttl = 600  # 10분

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_mention_score(self, symbol: str) -> dict:
        """
        토큰 멘션 점수 계산

        간이 구현: Nitter 검색 결과 카운트
        (정식 Twitter API 대신 무료 대안)

        Returns: {
            "symbol": str,
            "mention_count": int,
            "is_trending": bool,
            "score": float (0~1)
        }
        """
        if not symbol or len(symbol) < 2:
            return {"symbol": symbol, "mention_count": 0, "is_trending": False, "score": 0}

        # 캐시 체크
        if symbol in self._cache:
            count, ts = self._cache[symbol]
            if time.time() - ts < self._cache_ttl:
                return self._build_score(symbol, count)

        # Nitter 검색 시도
        count = await self._search_nitter(symbol)
        self._cache[symbol] = (count, time.time())

        return self._build_score(symbol, count)

    async def _search_nitter(self, symbol: str) -> int:
        """Nitter에서 $SYMBOL 멘션 수 추정"""
        query = f"${symbol}"
        for instance in NITTER_INSTANCES:
            try:
                session = await self._get_session()
                url = f"{instance}/search"
                params = {"f": "tweets", "q": query}
                async with session.get(
                    url, params=params,
                    timeout=aiohttp.ClientTimeout(total=8),
                    allow_redirects=True,
                ) as resp:
                    if resp.status != 200:
                        continue
                    html = await resp.text()
                    # 트윗 개수 대략 카운트 (timeline-item 수)
                    count = len(re.findall(r'timeline-item', html))
                    if count > 0:
                        return min(count, 100)
            except Exception:
                continue
        return 0

    def _build_score(self, symbol: str, count: int) -> dict:
        # 점수: 0~1 정규화 (50개 멘션 = 1.0)
        score = min(count / 50.0, 1.0)
        return {
            "symbol": symbol,
            "mention_count": count,
            "is_trending": count >= 10,
            "score": round(score, 3),
        }
