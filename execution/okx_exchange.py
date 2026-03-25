"""
OKX Exchange — ccxt 기반 OKX 거래소 연결

실시간 데이터 수집 + 주문 실행을 담당한다.
"""

import asyncio
import logging
from typing import Optional

import ccxt.async_support as ccxt

logger = logging.getLogger(__name__)


class OKXExchange:
    """OKX 거래소 연결 및 거래 실행"""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        testnet: bool = True,
        leverage_min: int = 10,
        leverage_max: int = 50,
    ):
        self.leverage_min = leverage_min
        self.leverage_max = leverage_max
        self.testnet = testnet

        config = {
            "apiKey": api_key,
            "secret": api_secret,
            "password": passphrase,  # OKX는 passphrase 필요
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",       # 선물(무기한 계약)
                "adjustForTimeDifference": True,
            },
        }

        if testnet:
            config["sandbox"] = True

        self.exchange = ccxt.okx(config)
        self._initialized = False

    async def initialize(self):
        """거래소 초기화 — 마켓 로드 + 레버리지 설정"""
        try:
            await self.exchange.load_markets()
            self._initialized = True
            mode = "테스트넷" if self.testnet else "실거래"
            logger.info(f"OKX 연결 성공 ({mode})")
            return True
        except Exception as e:
            logger.error(f"OKX 연결 실패: {e}")
            return False

    async def close(self):
        """거래소 연결 종료"""
        await self.exchange.close()

    # ── 잔고 조회 ──────────────────────────────────────

    async def get_balance(self) -> dict:
        """USDT 잔고 조회"""
        try:
            balance = await self.exchange.fetch_balance({"type": "swap"})
            usdt = balance.get("USDT", {})
            return {
                "total": float(usdt.get("total", 0) or 0),
                "free": float(usdt.get("free", 0) or 0),
                "used": float(usdt.get("used", 0) or 0),
            }
        except Exception as e:
            logger.error(f"잔고 조회 실패: {e}")
            return {"total": 0, "free": 0, "used": 0}

    # ── 시장 데이터 ────────────────────────────────────

    async def get_candles(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> list[dict]:
        """OHLCV 캔들 데이터 조회"""
        try:
            ohlcv = await self.exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=limit
            )
            candles = []
            for c in ohlcv:
                candles.append({
                    "timestamp": c[0],
                    "open": c[1],
                    "high": c[2],
                    "low": c[3],
                    "close": c[4],
                    "volume": c[5],
                })
            return candles
        except Exception as e:
            logger.error(f"캔들 조회 실패 [{symbol} {timeframe}]: {e}")
            return []

    async def get_ticker(self, symbol: str) -> dict:
        """현재가 조회"""
        try:
            ticker = await self.exchange.fetch_ticker(symbol)
            return {
                "symbol": symbol,
                "last": ticker["last"],
                "bid": ticker["bid"],
                "ask": ticker["ask"],
                "high_24h": ticker["high"],
                "low_24h": ticker["low"],
                "volume_24h": ticker["quoteVolume"],
                "change_24h_pct": ticker.get("percentage", 0),
            }
        except Exception as e:
            logger.error(f"현재가 조회 실패 [{symbol}]: {e}")
            return {}

    async def get_orderbook(self, symbol: str, limit: int = 20) -> dict:
        """호가창 조회"""
        try:
            ob = await self.exchange.fetch_order_book(symbol, limit=limit)
            return {
                "bids": ob["bids"][:limit],
                "asks": ob["asks"][:limit],
                "spread": ob["asks"][0][0] - ob["bids"][0][0] if ob["asks"] and ob["bids"] else 0,
            }
        except Exception as e:
            logger.error(f"호가창 조회 실패 [{symbol}]: {e}")
            return {"bids": [], "asks": [], "spread": 0}

    async def get_funding_rate(self, symbol: str) -> dict:
        """펀딩비 조회"""
        try:
            funding = await self.exchange.fetch_funding_rate(symbol)
            return {
                "symbol": symbol,
                "funding_rate": funding.get("fundingRate", 0),
                "next_funding_time": funding.get("fundingDatetime", ""),
            }
        except Exception as e:
            logger.error(f"펀딩비 조회 실패 [{symbol}]: {e}")
            return {"symbol": symbol, "funding_rate": 0, "next_funding_time": ""}

    async def get_market_data(self, symbol: str, timeframes: list[str] = None) -> dict:
        """에이전트에게 전달할 종합 시장 데이터 수집"""
        if timeframes is None:
            timeframes = ["15m", "1h", "4h"]

        # 병렬로 모든 데이터 수집
        tasks = []
        tasks.append(self.get_ticker(symbol))
        tasks.append(self.get_orderbook(symbol))
        tasks.append(self.get_funding_rate(symbol))
        for tf in timeframes:
            tasks.append(self.get_candles(symbol, tf, limit=100))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        ticker = results[0] if not isinstance(results[0], Exception) else {}
        orderbook = results[1] if not isinstance(results[1], Exception) else {}
        funding = results[2] if not isinstance(results[2], Exception) else {}

        candles = {}
        for i, tf in enumerate(timeframes):
            idx = 3 + i
            if not isinstance(results[idx], Exception):
                candles[tf] = results[idx]
            else:
                candles[tf] = []

        return {
            "symbol": symbol,
            "ticker": ticker,
            "orderbook": orderbook,
            "funding": funding,
            "candles": candles,
            "timestamp": "live",
        }

    # ── 포지션 조회 ────────────────────────────────────

    async def get_positions(self, symbol: str = None) -> list[dict]:
        """열린 포지션 조회"""
        try:
            positions = await self.exchange.fetch_positions([symbol] if symbol else None)
            active = []
            for p in positions:
                size = float(p.get("contracts", 0) or 0)
                if size == 0:
                    continue
                active.append({
                    "symbol": p["symbol"],
                    "side": p["side"],
                    "size": size,
                    "entry_price": float(p.get("entryPrice", 0) or 0),
                    "mark_price": float(p.get("markPrice", 0) or 0),
                    "unrealized_pnl": float(p.get("unrealizedPnl", 0) or 0),
                    "leverage": float(p.get("leverage", 0) or 0),
                    "liquidation_price": float(p.get("liquidationPrice", 0) or 0),
                    "margin": float(p.get("initialMargin", 0) or 0),
                })
            return active
        except Exception as e:
            logger.error(f"포지션 조회 실패: {e}")
            return []

    # ── 주문 실행 ──────────────────────────────────────

    def calculate_leverage(self, confidence: float, volatility: str = "normal") -> int:
        """
        상황에 맞는 동적 레버리지 계산

        확신도 + 변동성 기반:
        - 확신도 높음 + 변동성 낮음 → 고레버 (최대 50x)
        - 확신도 낮음 + 변동성 높음 → 저레버 (최소 10x)
        """
        # 확신도 기반 베이스 레버리지 (0.6~1.0 → 10~50)
        conf_range = max(0, min(1, (confidence - 0.6) / 0.4))  # 0~1 정규화
        base = self.leverage_min + conf_range * (self.leverage_max - self.leverage_min)

        # 변동성 보정
        vol_multiplier = {
            "extreme": 0.4,   # 극단적 변동 → 레버 대폭 축소
            "high": 0.6,      # 높은 변동 → 레버 축소
            "normal": 1.0,    # 보통
            "low": 1.2,       # 낮은 변동 → 레버 약간 증가
        }.get(volatility, 1.0)

        leverage = int(base * vol_multiplier)
        leverage = max(self.leverage_min, min(self.leverage_max, leverage))

        return leverage

    async def set_leverage(self, symbol: str, leverage: int):
        """레버리지 설정"""
        try:
            await self.exchange.set_leverage(leverage, symbol)
            logger.info(f"[{symbol}] 레버리지 {leverage}x 설정 완료")
        except Exception as e:
            logger.warning(f"레버리지 설정 실패 [{symbol}]: {e}")

    async def open_position(
        self,
        symbol: str,
        side: str,              # "buy" or "sell"
        usdt_amount: float,     # 투입할 USDT 금액 (마진)
        leverage: int = 20,     # 동적 레버리지
        stop_loss: float = None,
        take_profit: float = None,
    ) -> Optional[dict]:
        """포지션 오픈 (시장가, 동적 레버리지)"""
        try:
            # 동적 레버리지 설정
            await self.set_leverage(symbol, leverage)

            # 현재가 조회
            ticker = await self.get_ticker(symbol)
            if not ticker:
                return None

            price = ticker["last"]

            # 수량 계산: USDT 금액 × 레버리지 / 현재가
            amount = (usdt_amount * leverage) / price

            # 마켓 최소 수량에 맞게 조정
            market = self.exchange.market(symbol)
            amount = self.exchange.amount_to_precision(symbol, amount)
            amount = float(amount)

            if amount <= 0:
                logger.error(f"수량이 0 이하: {amount}")
                return None

            # 시장가 주문
            params = {"tdMode": "cross"}  # 교차 마진

            order = await self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=amount,
                params=params,
            )

            logger.info(
                f"주문 체결: {side.upper()} {symbol} "
                f"수량={amount} 가격≈{price} "
                f"투입=${usdt_amount} 레버리지={leverage}x"
            )

            # 손절/익절 설정
            if stop_loss:
                await self._set_stop_loss(symbol, side, amount, stop_loss)
            if take_profit:
                await self._set_take_profit(symbol, side, amount, take_profit)

            return {
                "order_id": order["id"],
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "price": price,
                "usdt_amount": usdt_amount,
                "leverage": leverage,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "status": order["status"],
            }

        except Exception as e:
            logger.error(f"주문 실패 [{symbol} {side}]: {e}")
            return None

    async def close_position(self, symbol: str, side: str, amount: float) -> Optional[dict]:
        """포지션 청산 (시장가)"""
        try:
            # 롱 청산 → sell, 숏 청산 → buy
            close_side = "sell" if side == "buy" or side == "long" else "buy"

            params = {"tdMode": "cross", "reduceOnly": True}

            order = await self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=close_side,
                amount=amount,
                params=params,
            )

            logger.info(f"포지션 청산: {symbol} {close_side} 수량={amount}")
            return {
                "order_id": order["id"],
                "symbol": symbol,
                "side": close_side,
                "amount": amount,
                "status": order["status"],
            }

        except Exception as e:
            logger.error(f"청산 실패 [{symbol}]: {e}")
            return None

    async def close_all_positions(self) -> list[dict]:
        """모든 포지션 청산"""
        positions = await self.get_positions()
        results = []
        for pos in positions:
            result = await self.close_position(
                pos["symbol"], pos["side"], pos["size"]
            )
            if result:
                results.append(result)
        return results

    # ── 손절/익절 ──────────────────────────────────────

    async def _set_stop_loss(
        self, symbol: str, side: str, amount: float, price: float
    ):
        """손절 주문"""
        try:
            sl_side = "sell" if side == "buy" else "buy"
            trigger_direction = "below" if side == "buy" else "above"

            await self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=sl_side,
                amount=amount,
                params={
                    "tdMode": "cross",
                    "reduceOnly": True,
                    "stopLossPrice": price,
                },
            )
            logger.info(f"손절 설정: {symbol} @ {price}")
        except Exception as e:
            logger.warning(f"손절 설정 실패 [{symbol}]: {e}")

    async def _set_take_profit(
        self, symbol: str, side: str, amount: float, price: float
    ):
        """익절 주문"""
        try:
            tp_side = "sell" if side == "buy" else "buy"

            await self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=tp_side,
                amount=amount,
                params={
                    "tdMode": "cross",
                    "reduceOnly": True,
                    "takeProfitPrice": price,
                },
            )
            logger.info(f"익절 설정: {symbol} @ {price}")
        except Exception as e:
            logger.warning(f"익절 설정 실패 [{symbol}]: {e}")

    # ── 유틸리티 ───────────────────────────────────────

    async def get_account_summary(self) -> dict:
        """계좌 전체 요약"""
        balance = await self.get_balance()
        positions = await self.get_positions()

        total_pnl = sum(p["unrealized_pnl"] for p in positions)
        total_margin = sum(p["margin"] for p in positions)

        return {
            "balance": balance,
            "positions": positions,
            "position_count": len(positions),
            "total_unrealized_pnl": round(total_pnl, 4),
            "total_margin_used": round(total_margin, 4),
            "available_pct": round(
                (balance["free"] / balance["total"] * 100) if balance["total"] > 0 else 0, 1
            ),
        }
