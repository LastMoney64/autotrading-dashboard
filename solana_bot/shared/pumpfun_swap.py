"""
PumpFunSwap — Pump.fun 본딩커브 매매 인터페이스

Jupiter는 졸업 후 (Raydium 등록) 토큰만 거래 가능.
본딩커브 단계 (졸업 전) 토큰은 Pump.fun 직접 매매 필요.

가격 조회: Pump.fun 공식 API (frontend-api-v3.pump.fun)
매매 실행: PumpPortal Local API (https://pumpportal.fun/api/trade-local)
  - API 키 불필요 (Local 모드)
  - 우리가 직접 트랜잭션 서명 (개인키 안 보냄)

paper 모드: 가격 조회만 실제, 매매는 시뮬레이션
live 모드: PumpPortal API로 실제 트랜잭션 (별도 구현 필요)
"""

import logging
import asyncio
import base64
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)

# Pump.fun 공식 API
PUMPFUN_API = "https://frontend-api-v3.pump.fun"
# PumpPortal Local API (서명은 우리가)
PUMPPORTAL_LOCAL = "https://pumpportal.fun/api/trade-local"

SOL_MINT = "So11111111111111111111111111111111111111112"


class PumpFunSwap:
    """Pump.fun 본딩커브 토큰 매매"""

    def __init__(self, solana_client):
        """
        solana_client: SolanaClient 인스턴스 (지갑 주소, RPC)
        """
        self.client = solana_client

    # ──────────────────────────────────────────────
    # 토큰 정보 + 본딩커브 데이터
    # ──────────────────────────────────────────────

    async def get_token_info(self, mint: str) -> Optional[dict]:
        """
        Pump.fun 토큰 정보 조회

        Returns: {
            "mint": str,
            "complete": bool,                 # 졸업 여부
            "virtual_sol_reserves": float,    # SOL (lamports → SOL 변환됨)
            "virtual_token_reserves": float,  # 토큰 (raw)
            "real_sol_reserves": float,
            "market_cap_usd": float,
            "progress_pct": float,
            "decimals": int,
        }
        """
        try:
            url = f"{PUMPFUN_API}/coins/{mint}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    if not data:
                        return None

                    v_sol_raw = float(data.get("virtual_sol_reserves", 0) or 0)
                    v_token_raw = float(data.get("virtual_token_reserves", 0) or 0)
                    real_sol_raw = float(data.get("real_sol_reserves", 0) or 0)
                    usd_mc = float(data.get("usd_market_cap", 0) or 0)

                    return {
                        "mint": mint,
                        "name": data.get("name", ""),
                        "symbol": data.get("symbol", ""),
                        "complete": data.get("complete", False),
                        "virtual_sol_reserves": v_sol_raw / 1e9,  # SOL 단위
                        "virtual_token_reserves": v_token_raw,    # raw (토큰 decimals 포함)
                        "real_sol_reserves": real_sol_raw / 1e9,
                        "market_cap_usd": usd_mc,
                        "progress_pct": min(100.0, (usd_mc / 69000.0 * 100)) if usd_mc else 0,
                        "raydium_pool": data.get("raydium_pool"),
                    }
        except Exception as e:
            logger.debug(f"PumpFun 토큰 정보 실패 {mint[:10]}: {e}")
            return None

    # ──────────────────────────────────────────────
    # 가격 조회 (1 토큰당 SOL)
    # ──────────────────────────────────────────────

    async def get_token_price_sol(self, mint: str, decimals: int = 6) -> Optional[float]:
        """
        본딩커브 토큰 1개당 SOL 가격

        AMM constant product: price = v_sol / v_token
        Pump.fun 표준 토큰: 6 decimals

        Returns: 1 토큰당 SOL 가격 (UI 단위)
        """
        info = await self.get_token_info(mint)
        if not info:
            return None

        # 졸업한 토큰은 PumpFun에서 거래 X (Jupiter로 가야 함)
        if info.get("complete"):
            return None

        v_sol = info["virtual_sol_reserves"]      # SOL 단위
        v_token_raw = info["virtual_token_reserves"]
        if v_token_raw <= 0 or v_sol <= 0:
            return None

        # raw → UI 단위 변환
        v_token_ui = v_token_raw / (10 ** decimals)
        if v_token_ui <= 0:
            return None

        # 1 토큰당 SOL 가격
        return v_sol / v_token_ui

    # ──────────────────────────────────────────────
    # 매수 가능 여부 (라우팅 체크)
    # ──────────────────────────────────────────────

    async def is_buyable(self, mint: str) -> bool:
        """본딩커브에서 매수 가능한 토큰인지 (졸업 안 됨 + 유동성 있음)"""
        info = await self.get_token_info(mint)
        if not info:
            return False
        if info.get("complete"):
            return False  # 졸업 후 → Jupiter 사용
        # 최소 유동성: virtual_sol_reserves > 5 SOL (너무 초기는 위험)
        if info.get("virtual_sol_reserves", 0) < 5:
            return False
        return True

    # ──────────────────────────────────────────────
    # 매수 (paper 시뮬레이션 / live 실거래)
    # ──────────────────────────────────────────────

    async def buy_token(
        self,
        token_mint: str,
        sol_amount: float,
        slippage_bps: int = 300,
        priority_fee_lamports: int = 100000,
        mode: str = "paper",
    ) -> Optional[dict]:
        """
        본딩커브 토큰 매수

        paper: 가격 조회 → 받을 토큰 양 계산 → 가상 매수
        live: PumpPortal Local API로 실제 트랜잭션

        Returns: {
            "confirmed": bool,
            "signature": str,
            "output_amount": int,  # 받은 토큰 raw
        }
        """
        info = await self.get_token_info(token_mint)
        if not info:
            return None

        if info.get("complete"):
            logger.warning(f"  ⚠️ {token_mint[:10]}... 이미 졸업 (complete=true) — Jupiter 사용 필요")
            return None

        decimals = 6  # Pump.fun 표준 (대부분)
        v_sol = info["virtual_sol_reserves"]
        v_token_raw = info["virtual_token_reserves"]

        if v_sol <= 0 or v_token_raw <= 0:
            return None

        # AMM constant product 공식 (수수료 1% 차감)
        # k = v_sol * v_token
        # 새 v_sol = v_sol + (sol_amount * 0.99)
        # 새 v_token = k / 새 v_sol
        # 받을 토큰 = v_token - 새 v_token
        sol_in_after_fee = sol_amount * 0.99
        new_v_sol = v_sol + sol_in_after_fee
        new_v_token_raw = (v_sol * v_token_raw) / new_v_sol
        tokens_out_raw = v_token_raw - new_v_token_raw

        if tokens_out_raw <= 0:
            return None

        if mode == "paper":
            # 시뮬레이션: 트랜잭션 X
            return {
                "confirmed": True,
                "signature": f"PAPER_PF_BUY_{token_mint[:8]}",
                "output_amount": int(tokens_out_raw),
                "decimals": decimals,
            }

        # ── Live 모드: PumpPortal Local API ──
        try:
            payload = {
                "publicKey": str(self.client.public_key),
                "action": "buy",
                "mint": token_mint,
                "denominatedInSol": "true",
                "amount": sol_amount,
                "slippage": slippage_bps / 100,  # bps → %
                "priorityFee": priority_fee_lamports / 1e9,
                "pool": "pump",  # bonding curve 단계
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    PUMPPORTAL_LOCAL,
                    data=payload,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.warning(f"PumpPortal HTTP {resp.status}: {text[:200]}")
                        return None
                    tx_bytes = await resp.read()
                    if not tx_bytes:
                        return None
            # 트랜잭션 서명 + 전송
            return await self._sign_and_send(tx_bytes, decimals, int(tokens_out_raw))
        except Exception as e:
            logger.error(f"PumpPortal 매수 에러: {e}")
            return None

    async def sell_token(
        self,
        token_mint: str,
        token_amount_raw: int,
        slippage_bps: int = 300,
        priority_fee_lamports: int = 100000,
        mode: str = "paper",
    ) -> Optional[dict]:
        """본딩커브 토큰 매도"""
        info = await self.get_token_info(token_mint)
        if not info or info.get("complete"):
            return None

        decimals = 6
        v_sol = info["virtual_sol_reserves"]
        v_token_raw = info["virtual_token_reserves"]

        if v_sol <= 0 or v_token_raw <= 0 or token_amount_raw <= 0:
            return None

        # AMM 공식 (매도)
        new_v_token_raw = v_token_raw + token_amount_raw
        new_v_sol = (v_sol * v_token_raw) / new_v_token_raw
        sol_out = (v_sol - new_v_sol) * 0.99  # 1% 수수료

        if sol_out <= 0:
            return None

        if mode == "paper":
            return {
                "confirmed": True,
                "signature": f"PAPER_PF_SELL_{token_mint[:8]}",
                "output_amount_sol": sol_out,
            }

        # ── Live 모드 ──
        try:
            payload = {
                "publicKey": str(self.client.public_key),
                "action": "sell",
                "mint": token_mint,
                "denominatedInSol": "false",
                "amount": str(token_amount_raw / (10 ** decimals)),
                "slippage": slippage_bps / 100,
                "priorityFee": priority_fee_lamports / 1e9,
                "pool": "pump",
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    PUMPPORTAL_LOCAL,
                    data=payload,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        return None
                    tx_bytes = await resp.read()
                    if not tx_bytes:
                        return None
            result = await self._sign_and_send_sell(tx_bytes, sol_out)
            return result
        except Exception as e:
            logger.error(f"PumpPortal 매도 에러: {e}")
            return None

    # ──────────────────────────────────────────────
    # 트랜잭션 서명 + 전송 (Live 모드 전용)
    # ──────────────────────────────────────────────

    async def _sign_and_send(
        self, tx_bytes: bytes, decimals: int, expected_out: int
    ) -> Optional[dict]:
        """PumpPortal 응답 트랜잭션 서명 후 RPC로 전송 (매수)"""
        try:
            from solders.transaction import VersionedTransaction
            from solders.keypair import Keypair as SoldersKeypair

            # 직렬화된 트랜잭션 디코딩
            tx = VersionedTransaction.from_bytes(tx_bytes)
            # 우리 keypair로 서명
            keypair = self.client.keypair  # solders.keypair.Keypair
            signed = VersionedTransaction(tx.message, [keypair])
            # base64 인코딩 후 RPC 전송
            signed_b64 = base64.b64encode(bytes(signed)).decode()

            # RPC 호출
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.client.rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "sendTransaction",
                        "params": [
                            signed_b64,
                            {"encoding": "base64", "skipPreflight": False, "maxRetries": 3},
                        ],
                    },
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    data = await resp.json()
                    sig = data.get("result")
                    if not sig:
                        logger.warning(f"PumpFun 트랜잭션 전송 실패: {data}")
                        return None
                    return {
                        "confirmed": True,
                        "signature": sig,
                        "output_amount": expected_out,
                        "decimals": decimals,
                    }
        except Exception as e:
            logger.error(f"PumpFun 서명/전송 에러: {e}")
            return None

    async def _sign_and_send_sell(self, tx_bytes: bytes, expected_sol: float) -> Optional[dict]:
        """매도 트랜잭션 서명 + 전송"""
        try:
            from solders.transaction import VersionedTransaction

            tx = VersionedTransaction.from_bytes(tx_bytes)
            keypair = self.client.keypair
            signed = VersionedTransaction(tx.message, [keypair])
            signed_b64 = base64.b64encode(bytes(signed)).decode()

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.client.rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "sendTransaction",
                        "params": [
                            signed_b64,
                            {"encoding": "base64", "skipPreflight": False, "maxRetries": 3},
                        ],
                    },
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    data = await resp.json()
                    sig = data.get("result")
                    if not sig:
                        return None
                    return {
                        "confirmed": True,
                        "signature": sig,
                        "output_amount_sol": expected_sol,
                    }
        except Exception as e:
            logger.error(f"PumpFun 매도 서명/전송 에러: {e}")
            return None
