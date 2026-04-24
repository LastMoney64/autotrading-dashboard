"""
PolymarketClient — Polymarket CLOB API 통합

기능:
- 마켓 목록 조회 (Weather 카테고리 필터)
- 호가 조회 (mid price, best bid/ask)
- 매매 주문 실행 (limit/market)
- 포지션 조회
"""

import logging
import asyncio
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
# Polymarket API 엔드포인트
# ═══════════════════════════════════════════════════════
GAMMA_API = "https://gamma-api.polymarket.com"  # 마켓 메타데이터 (공개)
CLOB_API = "https://clob.polymarket.com"        # 주문 실행 (인증 필요)
DATA_API = "https://data-api.polymarket.com"    # 가격/볼륨


class PolymarketClient:
    """Polymarket REST API 통합 클라이언트"""

    def __init__(self, polygon_client=None):
        """
        polygon_client: PolygonClient 인스턴스 (서명용)
        """
        self.polygon = polygon_client
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": "PolymarketBot/1.0"},
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ──────────────────────────────────────────────
    # 마켓 조회 (공개 API)
    # ──────────────────────────────────────────────

    async def get_active_markets(
        self, tag: str = "Weather", limit: int = 100
    ) -> list[dict]:
        """
        활성 마켓 목록 조회

        tag: "Weather", "Crypto", "Sports", "Politics" 등
        """
        try:
            session = await self._get_session()
            url = f"{GAMMA_API}/markets"
            params = {
                "active": "true",
                "closed": "false",
                "limit": limit,
                "tag_slug": tag.lower(),
            }
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"마켓 조회 HTTP {resp.status}")
                    return []
                data = await resp.json()
                if not isinstance(data, list):
                    return []

                # 정리해서 반환
                markets = []
                for m in data:
                    if not m.get("active") or m.get("closed"):
                        continue

                    # 토큰 ID (token IDs for outcomes)
                    token_ids = []
                    try:
                        token_ids_str = m.get("clobTokenIds", "[]")
                        if isinstance(token_ids_str, str):
                            import json
                            token_ids = json.loads(token_ids_str)
                    except Exception:
                        pass

                    markets.append({
                        "id": m.get("id"),
                        "slug": m.get("slug"),
                        "question": m.get("question", ""),
                        "description": (m.get("description", "") or "")[:200],
                        "end_date": m.get("endDate", ""),
                        "outcomes": m.get("outcomes", "[]"),
                        "outcome_prices": m.get("outcomePrices", "[]"),
                        "volume_num": float(m.get("volumeNum", 0) or 0),
                        "liquidity": float(m.get("liquidityNum", 0) or 0),
                        "token_ids": token_ids,
                        "category": m.get("category", ""),
                        "tag": tag,
                    })
                return markets
        except Exception as e:
            logger.warning(f"마켓 조회 실패: {e}")
            return []

    async def get_market_orderbook(self, token_id: str) -> dict:
        """특정 토큰의 호가창 조회"""
        try:
            session = await self._get_session()
            url = f"{CLOB_API}/book"
            params = {"token_id": token_id}
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return {}
                return await resp.json()
        except Exception as e:
            logger.warning(f"호가창 조회 실패: {e}")
            return {}

    async def get_market_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        """
        특정 토큰의 현재 가격

        side: "BUY" (매수가) 또는 "SELL" (매도가)
        """
        try:
            session = await self._get_session()
            url = f"{CLOB_API}/price"
            params = {"token_id": token_id, "side": side}
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return float(data.get("price", 0)) if data else None
        except Exception as e:
            logger.warning(f"가격 조회 실패: {e}")
            return None

    async def get_midpoint(self, token_id: str) -> Optional[float]:
        """중간 가격 (bid + ask) / 2"""
        try:
            session = await self._get_session()
            url = f"{CLOB_API}/midpoint"
            params = {"token_id": token_id}
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return float(data.get("mid", 0)) if data else None
        except Exception as e:
            logger.warning(f"midpoint 조회 실패: {e}")
            return None

    # ──────────────────────────────────────────────
    # 포지션 조회
    # ──────────────────────────────────────────────

    async def get_positions(self, address: str) -> list[dict]:
        """현재 보유 포지션"""
        try:
            session = await self._get_session()
            url = f"{DATA_API}/positions"
            params = {"user": address}
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                if not isinstance(data, list):
                    return []
                return data
        except Exception as e:
            logger.warning(f"포지션 조회 실패: {e}")
            return []

    # ──────────────────────────────────────────────
    # 주문 실행 (py-clob-client 사용)
    # ──────────────────────────────────────────────

    def _build_clob_client(self):
        """py-clob-client 인스턴스 생성 (지연 import)"""
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.constants import POLYGON

            client = ClobClient(
                host=CLOB_API,
                key=self.polygon.account.key.hex() if self.polygon else None,
                chain_id=POLYGON,
            )
            # API credentials 자동 생성 또는 가져오기
            try:
                creds = client.create_or_derive_api_creds()
                client.set_api_creds(creds)
            except Exception as e:
                logger.warning(f"API creds 생성 실패: {e}")
            return client
        except ImportError:
            logger.error("py-clob-client 미설치 — pip install py-clob-client")
            return None
        except Exception as e:
            logger.error(f"CLOB 클라이언트 생성 실패: {e}")
            return None

    async def place_market_order(
        self, token_id: str, side: str, size: float
    ) -> Optional[dict]:
        """
        시장가 주문 실행

        token_id: 토큰 ID
        side: "BUY" or "SELL"
        size: USD 금액 (BUY) 또는 토큰 수량 (SELL)
        """
        try:
            client = self._build_clob_client()
            if not client:
                return None

            from py_clob_client.clob_types import MarketOrderArgs, OrderType

            args = MarketOrderArgs(
                token_id=token_id,
                amount=size,
                side=side,
            )

            # 비동기로 실행 (블로킹 호출이라 별도 스레드)
            loop = asyncio.get_event_loop()
            signed = await loop.run_in_executor(None, client.create_market_order, args)
            response = await loop.run_in_executor(
                None, client.post_order, signed, OrderType.FOK
            )
            return response
        except Exception as e:
            logger.error(f"시장가 주문 실패: {e}")
            return None
