"""
HeliusClient — Solana DAS API + Enhanced API

기능:
- 토큰 메타데이터 (이름, 심볼, 디시멀, 권한)
- 지갑 거래 히스토리 (최근 매매)
- 토큰 보유자 분포
- mint/freeze 권한 체크
"""

import logging
import asyncio
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)


class HeliusClient:
    """Helius API 통합"""

    def __init__(self, api_key: str):
        if not api_key:
            logger.warning("Helius API 키 없음 — 일부 기능 제한")
        self.api_key = api_key
        self.rpc_url = (
            f"https://mainnet.helius-rpc.com/?api-key={api_key}"
            if api_key else "https://api.mainnet-beta.solana.com"
        )
        self.api_url = f"https://api.helius.xyz/v0"

    # ──────────────────────────────────────────────
    # 토큰 메타데이터
    # ──────────────────────────────────────────────

    async def get_token_metadata(self, mint: str) -> Optional[dict]:
        """
        토큰 메타데이터 + 권한 정보

        Returns: {
            "name": str, "symbol": str, "decimals": int,
            "supply": int, "mint_authority": str|None,
            "freeze_authority": str|None
        }
        """
        try:
            async with aiohttp.ClientSession() as session:
                # DAS API getAsset
                async with session.post(
                    self.rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getAsset",
                        "params": {"id": mint},
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    asset = data.get("result", {})
                    if not asset:
                        return None

                    content = asset.get("content", {})
                    metadata = content.get("metadata", {})
                    token_info = asset.get("token_info", {})
                    auth = asset.get("authorities", [])

                    mint_auth = None
                    freeze_auth = None
                    for a in auth:
                        scopes = a.get("scopes", [])
                        if "mint" in scopes or "full" in scopes:
                            mint_auth = a.get("address")

                    return {
                        "name": metadata.get("name", ""),
                        "symbol": metadata.get("symbol", ""),
                        "decimals": int(token_info.get("decimals", 0)),
                        "supply": int(token_info.get("supply", 0)),
                        "mint_authority": mint_auth,
                        "freeze_authority": freeze_auth,
                        "raw": asset,
                    }
        except Exception as e:
            logger.warning(f"토큰 메타데이터 실패 {mint[:10]}: {e}")
            return None

    async def get_mint_info(self, mint: str) -> Optional[dict]:
        """
        getAccountInfo로 mint 권한 직접 확인

        SPL Token Mint 데이터 파싱:
        bytes 0-3: COption<Pubkey> tag (mint authority 존재 여부)
        bytes 4-35: mint authority pubkey (있으면)
        bytes 36-43: supply
        bytes 44: decimals
        bytes 45: is_initialized
        bytes 46-49: COption<Pubkey> tag (freeze authority)
        bytes 50-81: freeze authority pubkey
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getAccountInfo",
                        "params": [
                            mint,
                            {"encoding": "jsonParsed"},
                        ],
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    info = data.get("result", {}).get("value", {})
                    if not info:
                        return None
                    parsed = info.get("data", {}).get("parsed", {}).get("info", {})
                    return {
                        "mint_authority": parsed.get("mintAuthority"),
                        "freeze_authority": parsed.get("freezeAuthority"),
                        "decimals": int(parsed.get("decimals", 0)),
                        "supply": int(parsed.get("supply", 0)),
                        "is_initialized": parsed.get("isInitialized", False),
                    }
        except Exception as e:
            logger.warning(f"Mint 정보 실패: {e}")
            return None

    # ──────────────────────────────────────────────
    # 토큰 홀더 분석
    # ──────────────────────────────────────────────

    async def get_token_largest_accounts(self, mint: str) -> list[dict]:
        """상위 홀더 20명"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTokenLargestAccounts",
                        "params": [mint],
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    accounts = data.get("result", {}).get("value", [])
                    result = []
                    for a in accounts[:20]:
                        result.append({
                            "address": a.get("address", ""),
                            "amount": float(a.get("uiAmount", 0) or 0),
                            "decimals": int(a.get("decimals", 0)),
                        })
                    return result
        except Exception as e:
            logger.debug(f"홀더 조회 실패: {e}")
            return []

    async def get_holder_count(self, mint: str) -> int:
        """홀더 수 (대략)"""
        # Helius DAS는 정확한 holder count 직접 제공 안 함
        # getProgramAccounts로 추정 (느림, 비용 높음)
        # 우선 largestAccounts 길이로 추정
        accounts = await self.get_token_largest_accounts(mint)
        return len(accounts)

    # ──────────────────────────────────────────────
    # 지갑 거래 히스토리 (Bot 1 스마트머니 추적용)
    # ──────────────────────────────────────────────

    async def get_wallet_transactions(
        self, wallet: str, limit: int = 20
    ) -> list[dict]:
        """
        지갑 최근 거래 (Helius Enhanced API)

        Returns: [{
            "signature": str,
            "timestamp": int,
            "type": "SWAP" | "TRANSFER" | ...,
            "tokenTransfers": [...]
        }, ...]
        """
        if not self.api_key:
            return []
        try:
            url = f"{self.api_url}/addresses/{wallet}/transactions"
            params = {
                "api-key": self.api_key,
                "limit": min(limit, 100),
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    if not isinstance(data, list):
                        return []
                    return data
        except Exception as e:
            logger.debug(f"지갑 거래 조회 실패: {e}")
            return []

    async def get_recent_token_buys(
        self, wallet: str, since_seconds: int = 600
    ) -> list[dict]:
        """
        최근 N초 내 지갑이 매수한 토큰

        Returns: [{
            "signature": str,
            "timestamp": int,
            "token_mint": str,
            "amount_in_sol": float,  (대략)
        }, ...]
        """
        import time
        now = int(time.time())
        cutoff = now - since_seconds

        txs = await self.get_wallet_transactions(wallet, limit=30)
        buys = []
        for tx in txs:
            ts = tx.get("timestamp", 0)
            if ts < cutoff:
                continue
            # SWAP 타입만 (또는 transfers에 SOL out + 토큰 in 패턴)
            if tx.get("type") != "SWAP":
                continue
            transfers = tx.get("tokenTransfers", []) or []
            sol_out = 0.0
            tokens_in = []
            wallet_lower = wallet.lower()
            for t in transfers:
                from_addr = (t.get("fromUserAccount") or "").lower()
                to_addr = (t.get("toUserAccount") or "").lower()
                mint = t.get("mint", "")
                amt = float(t.get("tokenAmount", 0) or 0)
                if from_addr == wallet_lower:
                    if mint == "So11111111111111111111111111111111111111112":
                        sol_out += amt
                if to_addr == wallet_lower:
                    if mint != "So11111111111111111111111111111111111111112":
                        tokens_in.append({"mint": mint, "amount": amt})

            # SOL 보내고 토큰 받았으면 = 매수
            if sol_out > 0 and tokens_in:
                for tok in tokens_in:
                    buys.append({
                        "signature": tx.get("signature", ""),
                        "timestamp": ts,
                        "token_mint": tok["mint"],
                        "token_amount": tok["amount"],
                        "sol_spent": sol_out,
                    })

        return buys
