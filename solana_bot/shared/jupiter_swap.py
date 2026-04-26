"""
JupiterSwap — Jupiter Aggregator를 통한 솔라나 DEX 스왑

Jupiter는 Raydium, Orca, Meteora 등 모든 솔라나 DEX 통합 라우터.
가장 좋은 가격 자동으로 찾아줌.

기능:
- SOL → 토큰 매수
- 토큰 → SOL 매도
- 견적 조회 (가격 영향, 수수료)
- MEV 보호 (Priority Fee)
"""

import logging
import asyncio
import base64
import os
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)

# Jupiter API endpoints (2025년 변경: quote-api.jup.ag → lite-api.jup.ag)
# 무료 lite 엔드포인트 사용 (rate limit 있지만 충분)
# 환경변수로 override 가능 (URL 다시 바뀌면 즉시 대응)
JUPITER_QUOTE = os.getenv("JUPITER_QUOTE_URL", "https://lite-api.jup.ag/swap/v1/quote").strip()
JUPITER_SWAP = os.getenv("JUPITER_SWAP_URL", "https://lite-api.jup.ag/swap/v1/swap").strip()

# 솔라나 토큰 주소
SOL_MINT = "So11111111111111111111111111111111111111112"  # Wrapped SOL
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class JupiterSwap:
    """Jupiter Aggregator 스왑 인터페이스"""

    def __init__(self, solana_client, default_slippage_bps: int = 300):
        """
        solana_client: SolanaClient 인스턴스
        default_slippage_bps: 슬리피지 (300 = 3%)
        """
        self.client = solana_client
        self.slippage_bps = default_slippage_bps

    # ──────────────────────────────────────────────
    # 견적
    # ──────────────────────────────────────────────

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: int = None,
    ) -> Optional[dict]:
        """
        스왑 견적 조회

        amount: 입력 토큰 수량 (lamports / 토큰의 최소 단위)
        slippage_bps: 100 = 1%
        """
        slippage = slippage_bps or self.slippage_bps

        try:
            params = {
                "inputMint": input_mint,
                "outputMint": output_mint,
                "amount": str(amount),
                "slippageBps": str(slippage),
                "swapMode": "ExactIn",
                "onlyDirectRoutes": "false",
                "asLegacyTransaction": "false",
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    JUPITER_QUOTE,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.warning(f"Jupiter quote HTTP {resp.status}: {text[:200]}")
                        return None
                    return await resp.json()
        except Exception as e:
            logger.warning(f"Jupiter 견적 실패: {e}")
            return None

    async def get_swap_transaction(
        self, quote: dict, priority_fee_lamports: int = 100000
    ) -> Optional[str]:
        """
        스왑 트랜잭션 생성 (서명되지 않음)

        priority_fee_lamports: MEV 보호용 우선 수수료 (100000 = 0.0001 SOL)

        Returns: base64 인코딩된 트랜잭션
        """
        try:
            payload = {
                "quoteResponse": quote,
                "userPublicKey": self.client.public_key,
                "wrapAndUnwrapSol": True,
                "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": priority_fee_lamports,
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    JUPITER_SWAP,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.warning(f"Jupiter swap HTTP {resp.status}: {text[:200]}")
                        return None
                    data = await resp.json()
                    return data.get("swapTransaction")
        except Exception as e:
            logger.warning(f"Jupiter 트랜잭션 생성 실패: {e}")
            return None

    async def sign_and_send(self, swap_tx_b64: str) -> Optional[str]:
        """트랜잭션 서명 + 전송"""
        try:
            from solders.transaction import VersionedTransaction
            from solders.keypair import Keypair

            # 트랜잭션 디코딩
            tx_bytes = base64.b64decode(swap_tx_b64)
            tx = VersionedTransaction.from_bytes(tx_bytes)

            # 서명 (VersionedTransaction은 새 인스턴스 만드는 방식)
            signed = VersionedTransaction(tx.message, [self.client.keypair])

            # base64 인코딩
            signed_b64 = base64.b64encode(bytes(signed)).decode()

            # RPC로 전송
            sig = await self.client.send_signed_transaction(signed_b64)
            return sig
        except Exception as e:
            logger.error(f"트랜잭션 서명/전송 실패: {e}")
            return None

    # ──────────────────────────────────────────────
    # 매수 / 매도 통합 함수
    # ──────────────────────────────────────────────

    async def buy_token(
        self,
        token_mint: str,
        sol_amount: float,
        slippage_bps: int = None,
        priority_fee_lamports: int = 100000,
    ) -> Optional[dict]:
        """
        SOL → 토큰 매수

        Returns: {
            "signature": str,
            "input_amount_sol": float,
            "output_amount": int,  (토큰 수량, raw)
            "price_impact_pct": float,
        }
        """
        if sol_amount <= 0:
            return None

        lamports = int(sol_amount * 1e9)

        # 1. 견적
        quote = await self.get_quote(SOL_MINT, token_mint, lamports, slippage_bps)
        if not quote:
            return None

        out_amount = int(quote.get("outAmount", 0))
        price_impact = float(quote.get("priceImpactPct", 0) or 0) * 100

        # 2. 트랜잭션 생성
        swap_tx = await self.get_swap_transaction(quote, priority_fee_lamports)
        if not swap_tx:
            return None

        # 3. 서명 + 전송
        sig = await self.sign_and_send(swap_tx)
        if not sig:
            return None

        # 4. 확정 대기
        confirmed = await self.client.confirm_transaction(sig, timeout=45)

        return {
            "signature": sig,
            "confirmed": confirmed,
            "input_amount_sol": sol_amount,
            "output_amount": out_amount,
            "price_impact_pct": price_impact,
            "side": "BUY",
            "token_mint": token_mint,
        }

    async def sell_token(
        self,
        token_mint: str,
        token_amount_raw: int,
        slippage_bps: int = None,
        priority_fee_lamports: int = 100000,
    ) -> Optional[dict]:
        """
        토큰 → SOL 매도

        token_amount_raw: 토큰 raw 수량 (decimals 적용)
        """
        if token_amount_raw <= 0:
            return None

        # 1. 견적
        quote = await self.get_quote(
            token_mint, SOL_MINT, token_amount_raw, slippage_bps
        )
        if not quote:
            return None

        out_lamports = int(quote.get("outAmount", 0))
        price_impact = float(quote.get("priceImpactPct", 0) or 0) * 100

        # 2. 트랜잭션
        swap_tx = await self.get_swap_transaction(quote, priority_fee_lamports)
        if not swap_tx:
            return None

        # 3. 서명 + 전송
        sig = await self.sign_and_send(swap_tx)
        if not sig:
            return None

        confirmed = await self.client.confirm_transaction(sig, timeout=45)

        return {
            "signature": sig,
            "confirmed": confirmed,
            "input_amount_token": token_amount_raw,
            "output_amount_sol": out_lamports / 1e9,
            "price_impact_pct": price_impact,
            "side": "SELL",
            "token_mint": token_mint,
        }

    async def get_token_price_in_sol(
        self, token_mint: str, sample_amount_lamports: int = 1_000_000_000  # 1 SOL
    ) -> Optional[float]:
        """
        토큰 가격 조회 (SOL 단위)

        1 SOL로 살 수 있는 토큰 수량 → 1 토큰 가격 환산
        """
        quote = await self.get_quote(SOL_MINT, token_mint, sample_amount_lamports, 100)
        if not quote:
            return None
        out_amount = int(quote.get("outAmount", 0))
        if out_amount <= 0:
            return None
        # 1 SOL → out_amount 토큰
        # 1 토큰 = (1 / out_amount) SOL
        # out_amount는 raw (decimals 모름) → 그대로 비율만 사용
        return 1.0 / out_amount  # 토큰 1 raw unit = X SOL
