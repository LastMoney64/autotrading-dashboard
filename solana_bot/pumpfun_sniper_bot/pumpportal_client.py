"""
PumpPortalClient — Pump.fun 데이터 수집

데이터 소스 우선순위:
1. PumpPortal.fun (REST + WebSocket, 무료)
2. Bitquery (백업, 본딩커브 진행률)
3. Helius (트랜잭션 모니터링)

핵심 정보:
- 신규 토큰 (런칭 직후)
- 본딩커브 진행률 (0~100%)
- 거래량 / 홀더
- 졸업 (Raydium 마이그레이션) 감지
"""

import logging
import asyncio
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)

# Pump.fun 컨트랙트 (참고용)
PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPFUN_API = "https://frontend-api-v3.pump.fun"  # Pump.fun 공식 API


class PumpPortalClient:
    """Pump.fun 데이터 클라이언트"""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": "Mozilla/5.0 (compatible; PumpBot/1.0)"},
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ──────────────────────────────────────────────
    # 본딩커브 임박 토큰 (졸업 직전)
    # ──────────────────────────────────────────────

    async def get_almost_graduated(self, limit: int = 50) -> list[dict]:
        """
        본딩커브 80%+ 진행된 토큰 (졸업 임박)

        Pump.fun 졸업 조건: 시총 ~$69K (약 85 SOL 모금)
        매수량이 늘수록 본딩커브 진행률 ↑

        Returns: [{
            "mint": str,
            "name": str, "symbol": str,
            "progress_pct": float,    # 본딩커브 0~100
            "market_cap_sol": float,
            "liquidity_sol": float,
            "holders": int,
            "volume_24h_sol": float,
        }, ...]
        """
        try:
            session = await self._get_session()
            # market_cap 정렬 → 본딩커브 진행률 높은 토큰 우선
            url = f"{PUMPFUN_API}/coins"
            params = {
                "offset": 0,
                "limit": max(limit, 200),  # 최소 200개 (필터 통과 늘리기)
                "sort": "market_cap",       # 시총 높은 순 = 본딩커브 진행도 높음
                "order": "DESC",
                "includeNsfw": "false",
            }
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.debug(f"Pump.fun API HTTP {resp.status}")
                    return []
                data = await resp.json()
                if not isinstance(data, list):
                    return []

                results = []
                for c in data:
                    try:
                        mint = c.get("mint")
                        if not mint:
                            continue

                        # 본딩커브 진행률 계산
                        # virtual_sol_reserves: 본딩커브에 쌓인 가상 SOL
                        # 졸업 시 ~85 SOL
                        v_sol = float(c.get("virtual_sol_reserves", 0) or 0) / 1e9
                        # 또는 real_sol_reserves
                        real_sol = float(c.get("real_sol_reserves", 0) or 0) / 1e9
                        market_cap_sol = float(c.get("market_cap", 0) or 0)
                        usd_mc = float(c.get("usd_market_cap", 0) or 0)

                        # Pump.fun 졸업 = 시총 ~69K USD
                        progress_pct = min(100.0, (usd_mc / 69000.0 * 100)) if usd_mc else 0

                        results.append({
                            "mint": mint,
                            "name": c.get("name", ""),
                            "symbol": c.get("symbol", ""),
                            "image_uri": c.get("image_uri", ""),
                            "progress_pct": round(progress_pct, 2),
                            "market_cap_usd": usd_mc,
                            "market_cap_sol": market_cap_sol,
                            "real_sol_reserves": real_sol,
                            "virtual_sol_reserves": v_sol,
                            "complete": c.get("complete", False),  # 졸업 완료 여부
                            "raydium_pool": c.get("raydium_pool"),
                            "twitter": c.get("twitter"),
                            "telegram": c.get("telegram"),
                            "website": c.get("website"),
                            "creator": c.get("creator"),
                            "created_timestamp": c.get("created_timestamp", 0),
                            "reply_count": c.get("reply_count", 0),
                            "king_of_the_hill_timestamp": c.get("king_of_the_hill_timestamp"),
                        })
                    except Exception:
                        continue

                # 졸업 안 된 + 진행률 80% 이상만
                results = [r for r in results if not r["complete"] and r["progress_pct"] >= 80]
                results.sort(key=lambda x: x["progress_pct"], reverse=True)
                return results
        except Exception as e:
            logger.warning(f"Pump.fun 데이터 조회 실패: {e}")
            return []

    async def get_token_info(self, mint: str) -> Optional[dict]:
        """단일 토큰 상세 정보"""
        try:
            session = await self._get_session()
            url = f"{PUMPFUN_API}/coins/{mint}"
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if not data:
                    return None

                usd_mc = float(data.get("usd_market_cap", 0) or 0)
                progress_pct = min(100.0, (usd_mc / 69000.0 * 100)) if usd_mc else 0

                return {
                    "mint": mint,
                    "name": data.get("name", ""),
                    "symbol": data.get("symbol", ""),
                    "progress_pct": round(progress_pct, 2),
                    "market_cap_usd": usd_mc,
                    "complete": data.get("complete", False),
                    "raydium_pool": data.get("raydium_pool"),
                    "real_sol_reserves": float(data.get("real_sol_reserves", 0) or 0) / 1e9,
                }
        except Exception as e:
            logger.debug(f"Pump.fun 토큰 정보 실패 {mint[:10]}: {e}")
            return None

    async def get_recent_trades(self, mint: str, limit: int = 50) -> list[dict]:
        """토큰 최근 거래 (매수/매도 패턴 분석)"""
        try:
            session = await self._get_session()
            url = f"{PUMPFUN_API}/trades/all/{mint}"
            params = {"limit": limit}
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                if not isinstance(data, list):
                    return []
                return data
        except Exception as e:
            logger.debug(f"Pump.fun 거래 조회 실패: {e}")
            return []

    async def get_volume_recent(self, mint: str, minutes: int = 60) -> dict:
        """
        최근 N분 거래량 + 매수/매도 비율

        Returns: {
            "buys_count": int, "sells_count": int,
            "volume_sol": float, "unique_traders": int
        }
        """
        try:
            import time
            now = int(time.time())
            cutoff = now - minutes * 60

            trades = await self.get_recent_trades(mint, limit=100)
            buys = 0
            sells = 0
            volume = 0.0
            traders = set()

            for t in trades:
                ts = int(t.get("timestamp", 0))
                if ts < cutoff:
                    continue
                is_buy = t.get("is_buy", False)
                sol_amount = float(t.get("sol_amount", 0) or 0) / 1e9
                trader = t.get("user", "")

                if is_buy:
                    buys += 1
                else:
                    sells += 1
                volume += sol_amount
                if trader:
                    traders.add(trader)

            return {
                "buys_count": buys,
                "sells_count": sells,
                "volume_sol": round(volume, 4),
                "unique_traders": len(traders),
                "buy_ratio": buys / max(buys + sells, 1),
            }
        except Exception as e:
            logger.debug(f"거래량 분석 실패: {e}")
            return {"buys_count": 0, "sells_count": 0, "volume_sol": 0, "unique_traders": 0, "buy_ratio": 0}
