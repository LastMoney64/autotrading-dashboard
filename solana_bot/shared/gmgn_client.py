"""
GMGN OpenAPI 클라이언트

핵심 용도:
- Smart Money 지갑 거래 가져오기 (검증된 지갑 자동 발굴)
- KOL 인플루언서 지갑 거래
- 토큰/지갑 통계 (선택)

인증:
- Normal auth (smartmoney/kol/portfolio): X-APIKEY 헤더 + timestamp + client_id 쿼리
- Critical auth (swap/follow_wallet): 추가로 Ed25519 서명 필요 (현재 미구현)

Base URL: https://openapi.gmgn.ai
Docs: https://github.com/GMGNAI/gmgn-skills
"""

import logging
import time
import uuid
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)


class GmgnClient:
    """GMGN OpenAPI 통합 (normal auth만)"""

    BASE_URL = "https://openapi.gmgn.ai"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _auth_query(self) -> dict:
        """timestamp + client_id (서버가 ±5초 검증, UUID는 7초 안에 재사용 거부)"""
        return {
            "timestamp": int(time.time()),
            "client_id": str(uuid.uuid4()),
        }

    async def _normal_get(
        self, sub_path: str, query: Optional[dict] = None
    ) -> Optional[dict]:
        """Normal auth GET — X-APIKEY만 필요"""
        if not self.api_key:
            logger.warning("GMGN API 키 없음 — 호출 스킵")
            return None

        params = dict(query or {})
        params.update(self._auth_query())

        headers = {
            "X-APIKEY": self.api_key,
            "Content-Type": "application/json",
        }

        url = f"{self.BASE_URL}{sub_path}"

        try:
            session = await self._get_session()
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 401 or resp.status == 403:
                    body = await resp.text()
                    logger.error(
                        f"GMGN 인증 실패 ({resp.status}): {body[:200]} "
                        f"— IPv6 차단 가능성 / API 키 확인 / 화이트리스트 확인"
                    )
                    return None
                if resp.status == 429:
                    body = await resp.text()
                    logger.warning(f"GMGN 레이트리밋: {body[:200]}")
                    return None
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(
                        f"GMGN {sub_path} 실패 (HTTP {resp.status}): {body[:200]}"
                    )
                    return None
                data = await resp.json()
                # GMGN 응답 envelope: { code, data, message }
                if isinstance(data, dict) and "code" in data:
                    if data["code"] == 0 or data["code"] == "0":
                        return data.get("data")
                    logger.warning(
                        f"GMGN {sub_path} API 에러 code={data.get('code')} "
                        f"msg={data.get('message') or data.get('error')}"
                    )
                    return None
                # envelope 없이 raw 반환 케이스
                return data
        except Exception as e:
            logger.warning(f"GMGN {sub_path} 요청 실패: {e}")
            return None

    # ──────────────────────────────────────────────────
    # Smart Money / KOL — 검증된 지갑 거래 (핵심)
    # ──────────────────────────────────────────────────

    async def get_smart_money_trades(
        self, chain: str = "sol", limit: int = 100
    ) -> list[dict]:
        """
        GMGN 알고리즘이 검증한 스마트머니 지갑들의 최근 거래

        Returns: [
            {
                "maker": "지갑주소",
                "base_address": "토큰주소",
                "side": "buy"|"sell",
                "is_open_or_close": 0|1,  # 0=신규/추가, 1=청산/감소
                "amount_usd": float,
                "price_change": float,    # 1.5 = +50% (거래 후 가격)
                "maker_info": {"tags": [...], "name": str, ...},
                "timestamp": int,
                ...
            },
            ...
        ]
        """
        data = await self._normal_get(
            "/v1/user/smartmoney",
            {"chain": chain, "limit": min(limit, 200)},
        )
        if not data:
            return []
        # 응답이 {list: [...]} 또는 [...] 두 형태 모두 지원
        if isinstance(data, dict) and "list" in data:
            return data["list"] or []
        if isinstance(data, list):
            return data
        return []

    async def get_kol_trades(
        self, chain: str = "sol", limit: int = 100
    ) -> list[dict]:
        """KOL 인플루언서 지갑 거래"""
        data = await self._normal_get(
            "/v1/user/kol",
            {"chain": chain, "limit": min(limit, 200)},
        )
        if not data:
            return []
        if isinstance(data, dict) and "list" in data:
            return data["list"] or []
        if isinstance(data, list):
            return data
        return []

    # ──────────────────────────────────────────────────
    # 헬퍼: 거래 데이터에서 unique 지갑 주소 추출
    # ──────────────────────────────────────────────────

    @staticmethod
    def extract_buy_wallets(trades: list[dict]) -> list[dict]:
        """
        거래 데이터에서 매수 지갑들 추출 (중복 제거, 최신순)

        Smart money 매수 (side=buy + is_open_or_close=0) = 신규 포지션 진입.
        가장 시그널이 강한 거래.

        Returns: [
            {"address": str, "tag": str, "name": str, "trades": int}, ...
        ]
        """
        wallet_map: dict[str, dict] = {}

        for t in trades:
            maker = t.get("maker")
            if not maker:
                continue

            side = t.get("side")
            # 매수 거래만 (또는 side 정보 없는 경우 통과)
            if side and side != "buy":
                continue

            maker_info = t.get("maker_info") or {}
            tags = maker_info.get("tags") or []
            name = maker_info.get("name") or ""

            # 우선순위: smart_degen > kol > 기타
            primary_tag = "smart_money"
            if "smart_degen" in tags:
                primary_tag = "smart_degen"
            elif "kol" in tags:
                primary_tag = "kol"
            elif "renowned" in tags:
                primary_tag = "renowned"

            if maker in wallet_map:
                wallet_map[maker]["trades"] += 1
            else:
                wallet_map[maker] = {
                    "address": maker,
                    "tag": primary_tag,
                    "name": name,
                    "trades": 1,
                }

        # 거래 횟수 많은 순 (활성도 높은 지갑 우선)
        return sorted(
            wallet_map.values(),
            key=lambda w: w["trades"],
            reverse=True,
        )
