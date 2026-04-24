"""
PolygonClient — Polygon 체인 + Polymarket 컨트랙트 자동 승인

기능:
- 지갑 잔고 조회 (USDC.e, POL)
- Polymarket 4개 컨트랙트 자동 max approve
- 트랜잭션 실행
"""

import logging
import asyncio
from typing import Optional
from web3 import Web3
from eth_account import Account
import aiohttp

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
# Polygon 컨트랙트 주소
# ═══════════════════════════════════════════════════════

# USDC.e (Polymarket 거래 통화)
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Polymarket 컨트랙트 (사용자가 승인할 대상)
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
ROUTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
CONDITIONAL_TOKENS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# 승인 대상 (USDC.e + ConditionalTokens)
SPENDERS = [CTF_EXCHANGE, NEG_RISK_EXCHANGE, ROUTER]

# Polygon 공개 RPC (Railway/미국 서버에서도 작동하는 것들)
RPC_URLS = [
    "https://polygon-bor-rpc.publicnode.com",          # PublicNode (안정적)
    "https://polygon.drpc.org",                         # dRPC
    "https://rpc-mainnet.maticvigil.com",              # MaticVigil
    "https://rpc.ankr.com/polygon",                     # Ankr
    "https://polygon.gateway.tenderly.co",             # Tenderly
    "https://1rpc.io/matic",                            # 1RPC
    "https://polygon-rpc.com",                          # 공식
    "https://polygon.llamarpc.com",                    # LlamaRPC
]

# ERC20 ABI (필수 메서드만)
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
]

# ConditionalTokens ABI (setApprovalForAll)
CTF_ABI = [
    {
        "inputs": [
            {"name": "operator", "type": "address"},
            {"name": "approved", "type": "bool"},
        ],
        "name": "setApprovalForAll",
        "outputs": [],
        "type": "function",
    },
    {
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "operator", "type": "address"},
        ],
        "name": "isApprovedForAll",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]

MAX_UINT256 = 2**256 - 1


class PolygonClient:
    """Polygon 체인 트랜잭션 + Polymarket 승인"""

    def __init__(self, private_key: str):
        if not private_key:
            raise ValueError("POLYGON_PRIVATE_KEY 환경변수 필수")
        if not private_key.startswith("0x"):
            private_key = "0x" + private_key

        self.account = Account.from_key(private_key)
        self.address = self.account.address
        self.w3: Optional[Web3] = None
        self._connect_rpc()

        logger.info(f"Polygon 지갑 연결: {self.address}")

    def _connect_rpc(self):
        """여러 RPC 시도하여 연결"""
        # 사용자 정의 RPC가 있으면 우선 사용
        import os
        custom_rpc = os.getenv("POLYGON_RPC_URL", "").strip()
        rpc_list = [custom_rpc] if custom_rpc else []
        rpc_list.extend(RPC_URLS)

        errors = []
        for url in rpc_list:
            if not url:
                continue
            try:
                w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 15}))
                # is_connected() + chain_id 더블 체크
                if w3.is_connected():
                    chain_id = w3.eth.chain_id
                    if chain_id == 137:  # Polygon mainnet
                        self.w3 = w3
                        logger.info(f"✅ Polygon RPC 연결: {url}")
                        return
                    else:
                        errors.append(f"{url}: chain_id={chain_id} (Polygon=137 아님)")
                else:
                    errors.append(f"{url}: is_connected=False")
            except Exception as e:
                errors.append(f"{url}: {type(e).__name__}: {str(e)[:80]}")

        # 전체 실패 시 모든 에러 출력
        for err in errors:
            logger.warning(f"  ❌ {err}")
        raise RuntimeError(f"모든 Polygon RPC 연결 실패 ({len(rpc_list)}개 시도)")

    # ──────────────────────────────────────────────
    # 잔고 조회
    # ──────────────────────────────────────────────

    def get_pol_balance(self) -> float:
        """POL (가스비) 잔고"""
        try:
            wei = self.w3.eth.get_balance(self.address)
            return wei / 1e18
        except Exception as e:
            logger.warning(f"POL 잔고 조회 실패: {e}")
            return 0

    def get_usdc_balance(self) -> float:
        """USDC.e 잔고 (Polymarket 거래용)"""
        try:
            contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI
            )
            balance = contract.functions.balanceOf(self.address).call()
            return balance / 1e6  # USDC는 6 decimals
        except Exception as e:
            logger.warning(f"USDC.e 잔고 조회 실패: {e}")
            return 0

    def get_status(self) -> dict:
        """전체 상태"""
        return {
            "address": self.address,
            "pol_balance": self.get_pol_balance(),
            "usdc_balance": self.get_usdc_balance(),
        }

    # ──────────────────────────────────────────────
    # 컨트랙트 승인 자동화
    # ──────────────────────────────────────────────

    def _check_usdc_allowance(self, spender: str) -> int:
        """USDC.e 승인 잔량 조회"""
        try:
            contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI
            )
            return contract.functions.allowance(
                self.address, Web3.to_checksum_address(spender)
            ).call()
        except Exception as e:
            logger.warning(f"allowance 조회 실패: {e}")
            return 0

    def _check_ctf_approval(self, spender: str) -> bool:
        """ConditionalTokens setApprovalForAll 여부"""
        try:
            contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(CONDITIONAL_TOKENS), abi=CTF_ABI
            )
            return contract.functions.isApprovedForAll(
                self.address, Web3.to_checksum_address(spender)
            ).call()
        except Exception as e:
            logger.warning(f"isApprovedForAll 조회 실패: {e}")
            return False

    async def setup_approvals(self) -> dict:
        """
        Polymarket 4개 컨트랙트 자동 승인 (이미 승인됐으면 스킵)

        Returns: {"approved": [...], "skipped": [...], "failed": [...]}
        """
        result = {"approved": [], "skipped": [], "failed": []}

        # USDC.e 승인 — 3개 spender
        for spender in SPENDERS:
            try:
                current = self._check_usdc_allowance(spender)
                if current >= MAX_UINT256 // 2:
                    logger.info(f"  ✅ USDC.e -> {spender[:10]}... 이미 승인됨")
                    result["skipped"].append(f"USDC.e->{spender[:10]}")
                    continue

                tx_hash = await self._approve_usdc(spender)
                if tx_hash:
                    logger.info(f"  ✅ USDC.e -> {spender[:10]}... 승인: {tx_hash[:20]}")
                    result["approved"].append(f"USDC.e->{spender[:10]}")
                else:
                    result["failed"].append(f"USDC.e->{spender[:10]}")
            except Exception as e:
                logger.warning(f"USDC.e 승인 실패 {spender}: {e}")
                result["failed"].append(f"USDC.e->{spender[:10]}: {e}")

            await asyncio.sleep(2)  # 트랜잭션 간격

        # ConditionalTokens setApprovalForAll — 3개 operator
        for spender in SPENDERS:
            try:
                if self._check_ctf_approval(spender):
                    logger.info(f"  ✅ CTF -> {spender[:10]}... 이미 승인됨")
                    result["skipped"].append(f"CTF->{spender[:10]}")
                    continue

                tx_hash = await self._approve_ctf(spender)
                if tx_hash:
                    logger.info(f"  ✅ CTF -> {spender[:10]}... 승인: {tx_hash[:20]}")
                    result["approved"].append(f"CTF->{spender[:10]}")
                else:
                    result["failed"].append(f"CTF->{spender[:10]}")
            except Exception as e:
                logger.warning(f"CTF 승인 실패 {spender}: {e}")
                result["failed"].append(f"CTF->{spender[:10]}: {e}")

            await asyncio.sleep(2)

        return result

    async def _approve_usdc(self, spender: str) -> Optional[str]:
        """USDC.e max approve"""
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(USDC_E), abi=ERC20_ABI
        )
        try:
            tx = contract.functions.approve(
                Web3.to_checksum_address(spender), MAX_UINT256
            ).build_transaction({
                "from": self.address,
                "nonce": self.w3.eth.get_transaction_count(self.address),
                "maxFeePerGas": self.w3.to_wei(200, "gwei"),
                "maxPriorityFeePerGas": self.w3.to_wei(40, "gwei"),
                "chainId": 137,
                "type": 2,
            })

            signed = self.account.sign_transaction(tx)
            raw = signed.raw_transaction if hasattr(signed, "raw_transaction") else signed.rawTransaction
            tx_hash = self.w3.eth.send_raw_transaction(raw)

            # 확인 대기 (최대 60초)
            await asyncio.sleep(0)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt.status == 1:
                return tx_hash.hex()
            return None
        except Exception as e:
            logger.warning(f"USDC.e approve 실패: {e}")
            return None

    async def _approve_ctf(self, spender: str) -> Optional[str]:
        """ConditionalTokens setApprovalForAll(true)"""
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(CONDITIONAL_TOKENS), abi=CTF_ABI
        )
        try:
            tx = contract.functions.setApprovalForAll(
                Web3.to_checksum_address(spender), True
            ).build_transaction({
                "from": self.address,
                "nonce": self.w3.eth.get_transaction_count(self.address),
                "maxFeePerGas": self.w3.to_wei(200, "gwei"),
                "maxPriorityFeePerGas": self.w3.to_wei(40, "gwei"),
                "chainId": 137,
                "type": 2,
            })

            signed = self.account.sign_transaction(tx)
            raw = signed.raw_transaction if hasattr(signed, "raw_transaction") else signed.rawTransaction
            tx_hash = self.w3.eth.send_raw_transaction(raw)

            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt.status == 1:
                return tx_hash.hex()
            return None
        except Exception as e:
            logger.warning(f"CTF approve 실패: {e}")
            return None
