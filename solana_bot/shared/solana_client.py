"""
SolanaClient — 솔라나 지갑 + RPC 연결

기능:
- 지갑 주소 + 프라이빗 키 관리
- SOL 잔고 조회
- SPL 토큰 잔고 조회
- 트랜잭션 서명 + 전송
- Helius RPC 우선 사용 (가장 빠름)
"""

import logging
import asyncio
from typing import Optional
import aiohttp
import base58

logger = logging.getLogger(__name__)


def get_rpc_url(helius_key: str = "") -> str:
    """Helius RPC 우선, 없으면 공개 RPC 폴백"""
    if helius_key:
        return f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
    return "https://api.mainnet-beta.solana.com"


class SolanaClient:
    """솔라나 지갑 + RPC 인터페이스"""

    def __init__(
        self,
        private_key: str,
        helius_api_key: str = "",
        wallet_address: str = "",
    ):
        if not private_key:
            raise ValueError("Solana private key 필수")

        self.helius_key = helius_api_key
        self.rpc_url = get_rpc_url(helius_api_key)

        # solders로 키페어 생성
        self.keypair = self._load_keypair(private_key)
        self.public_key = str(self.keypair.pubkey())

        # 환경변수의 wallet_address와 일치 검증
        if wallet_address and wallet_address != self.public_key:
            logger.warning(
                f"⚠️ 지갑 주소 불일치: 환경변수 {wallet_address[:10]}... "
                f"vs 키 추출 {self.public_key[:10]}..."
            )

        logger.info(f"솔라나 지갑 연결: {self.public_key[:10]}...{self.public_key[-6:]}")

    def _load_keypair(self, private_key: str):
        """프라이빗 키를 Keypair 객체로 변환

        지원 형식:
        1. base58 문자열 (Phantom 기본)
        2. JSON 배열 [12, 34, ...]
        3. hex 문자열 (0x... 또는 raw)
        """
        from solders.keypair import Keypair

        pk = private_key.strip()

        # JSON 배열 형식
        if pk.startswith("["):
            import json
            arr = json.loads(pk)
            return Keypair.from_bytes(bytes(arr))

        # hex 형식
        if pk.startswith("0x"):
            pk = pk[2:]

        # hex 길이로 판단 (64바이트 = 128 hex chars)
        try:
            if len(pk) == 128:
                # hex string
                return Keypair.from_bytes(bytes.fromhex(pk))
        except Exception:
            pass

        # 기본: base58 (Phantom 형식)
        try:
            decoded = base58.b58decode(pk)
            return Keypair.from_bytes(decoded)
        except Exception as e:
            raise ValueError(
                f"프라이빗 키 형식 인식 실패. base58/hex/JSON 배열 형식을 사용하세요. ({e})"
            )

    # ──────────────────────────────────────────────
    # 잔고 조회
    # ──────────────────────────────────────────────

    async def get_sol_balance(self) -> float:
        """SOL 잔고 (lamports → SOL 변환)"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getBalance",
                        "params": [self.public_key],
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    lamports = data.get("result", {}).get("value", 0)
                    return lamports / 1e9
        except Exception as e:
            logger.warning(f"SOL 잔고 조회 실패: {e}")
            return 0

    async def get_token_balance(self, token_mint: str) -> dict:
        """SPL 토큰 잔고

        Returns: {"amount": float, "decimals": int, "ata": str}
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTokenAccountsByOwner",
                        "params": [
                            self.public_key,
                            {"mint": token_mint},
                            {"encoding": "jsonParsed"},
                        ],
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    accounts = data.get("result", {}).get("value", [])
                    if not accounts:
                        return {"amount": 0, "decimals": 0, "ata": ""}

                    info = accounts[0]
                    ata = info.get("pubkey", "")
                    parsed = info["account"]["data"]["parsed"]["info"]
                    token_amount = parsed.get("tokenAmount", {})
                    return {
                        "amount": float(token_amount.get("uiAmount", 0) or 0),
                        "decimals": int(token_amount.get("decimals", 0)),
                        "ata": ata,
                    }
        except Exception as e:
            logger.debug(f"토큰 잔고 조회 실패 {token_mint[:10]}: {e}")
            return {"amount": 0, "decimals": 0, "ata": ""}

    async def get_all_token_balances(self) -> list[dict]:
        """보유 중인 모든 SPL 토큰 잔고

        Returns: [{"mint": str, "amount": float, "decimals": int}, ...]
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTokenAccountsByOwner",
                        "params": [
                            self.public_key,
                            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                            {"encoding": "jsonParsed"},
                        ],
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    data = await resp.json()
                    accounts = data.get("result", {}).get("value", [])
                    result = []
                    for a in accounts:
                        try:
                            parsed = a["account"]["data"]["parsed"]["info"]
                            token_amount = parsed.get("tokenAmount", {})
                            amount = float(token_amount.get("uiAmount", 0) or 0)
                            if amount > 0:
                                result.append({
                                    "mint": parsed.get("mint", ""),
                                    "amount": amount,
                                    "decimals": int(token_amount.get("decimals", 0)),
                                    "ata": a.get("pubkey", ""),
                                })
                        except Exception:
                            continue
                    return result
        except Exception as e:
            logger.warning(f"전체 토큰 잔고 조회 실패: {e}")
            return []

    # ──────────────────────────────────────────────
    # 트랜잭션
    # ──────────────────────────────────────────────

    async def send_signed_transaction(
        self, signed_tx_b64: str, max_retries: int = 3
    ) -> Optional[str]:
        """서명된 트랜잭션 전송 (base64)

        Returns: 트랜잭션 해시 (signature) 또는 None
        """
        for attempt in range(max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.rpc_url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "sendTransaction",
                            "params": [
                                signed_tx_b64,
                                {
                                    "encoding": "base64",
                                    "skipPreflight": False,
                                    "maxRetries": 3,
                                    "preflightCommitment": "confirmed",
                                },
                            ],
                        },
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        data = await resp.json()
                        if "result" in data:
                            return data["result"]
                        if "error" in data:
                            err_msg = data["error"].get("message", "")
                            logger.warning(f"트랜잭션 에러 (시도 {attempt+1}): {err_msg}")
                            if "blockhash" in err_msg.lower():
                                # blockhash 만료 → 재시도 가능
                                await asyncio.sleep(1)
                                continue
                            return None
            except Exception as e:
                logger.warning(f"트랜잭션 전송 실패 (시도 {attempt+1}): {e}")
                await asyncio.sleep(1)
        return None

    async def confirm_transaction(self, signature: str, timeout: int = 60) -> bool:
        """트랜잭션 확정 대기"""
        import time
        start = time.time()
        while time.time() - start < timeout:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.rpc_url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getSignatureStatuses",
                            "params": [[signature]],
                        },
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        data = await resp.json()
                        statuses = data.get("result", {}).get("value", [])
                        if statuses and statuses[0]:
                            status = statuses[0]
                            if status.get("err"):
                                logger.warning(f"트랜잭션 실패: {status['err']}")
                                return False
                            confirmation = status.get("confirmationStatus", "")
                            if confirmation in ("confirmed", "finalized"):
                                return True
            except Exception as e:
                logger.debug(f"확정 체크 에러: {e}")

            await asyncio.sleep(2)

        logger.warning(f"트랜잭션 확정 타임아웃: {signature[:20]}")
        return False

    # ──────────────────────────────────────────────
    # 유틸리티
    # ──────────────────────────────────────────────

    def get_status(self) -> dict:
        """봇 상태 (동기 버전)"""
        return {
            "address": self.public_key,
            "address_short": f"{self.public_key[:6]}...{self.public_key[-4:]}",
            "rpc": "Helius" if self.helius_key else "Public",
        }
