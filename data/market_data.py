"""
MarketData — 시장 데이터 수집 레이어

추상 인터페이스 + Mock 구현.
Phase 10에서 ccxt 기반 실제 거래소 구현으로 교체.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
import random
import math

import numpy as np
import pandas as pd


@dataclass
class OHLCV:
    """단일 캔들 데이터"""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass
class MarketSnapshot:
    """한 시점의 전체 시장 데이터 (에이전트에게 제공)"""
    symbol: str
    timestamp: datetime
    candles: dict[str, pd.DataFrame]     # timeframe → OHLCV DataFrame
    current_price: float
    bid: float
    ask: float
    funding_rate: Optional[float] = None
    open_interest: Optional[float] = None
    fear_greed_index: Optional[int] = None
    news_headlines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        candles_info = {
            tf: {"rows": len(df), "last_close": float(df["close"].iloc[-1])}
            for tf, df in self.candles.items()
        }
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "current_price": self.current_price,
            "bid": self.bid,
            "ask": self.ask,
            "funding_rate": self.funding_rate,
            "open_interest": self.open_interest,
            "fear_greed_index": self.fear_greed_index,
            "news_headlines": self.news_headlines,
            "candles": candles_info,
        }


# ── 추상 인터페이스 ─────────────────────────────────────

class BaseMarketData(ABC):
    """시장 데이터 소스 추상 인터페이스"""

    @abstractmethod
    async def get_candles(
        self, symbol: str, timeframe: str, limit: int = 200
    ) -> pd.DataFrame:
        """OHLCV 캔들 DataFrame 반환"""
        ...

    @abstractmethod
    async def get_current_price(self, symbol: str) -> float:
        ...

    @abstractmethod
    async def get_orderbook(self, symbol: str) -> dict:
        """{'bid': float, 'ask': float, 'spread': float}"""
        ...

    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        ...

    @abstractmethod
    async def get_open_interest(self, symbol: str) -> Optional[float]:
        ...

    async def get_snapshot(
        self,
        symbol: str,
        timeframes: list[str] = None,
    ) -> MarketSnapshot:
        """전체 시장 스냅샷 생성"""
        if timeframes is None:
            timeframes = ["15m", "1h", "4h"]

        candles = {}
        for tf in timeframes:
            candles[tf] = await self.get_candles(symbol, tf)

        price = await self.get_current_price(symbol)
        orderbook = await self.get_orderbook(symbol)
        funding = await self.get_funding_rate(symbol)
        oi = await self.get_open_interest(symbol)

        return MarketSnapshot(
            symbol=symbol,
            timestamp=datetime.utcnow(),
            candles=candles,
            current_price=price,
            bid=orderbook["bid"],
            ask=orderbook["ask"],
            funding_rate=funding,
            open_interest=oi,
        )


# ── Mock 구현 ───────────────────────────────────────────

class MockMarketData(BaseMarketData):
    """테스트용 가짜 시장 데이터 생성기

    실제 BTC 가격 움직임과 비슷한 패턴을 생성한다.
    - 랜덤 워크 + 추세 + 변동성 클러스터링
    - 타임프레임별 현실적인 캔들 생성
    """

    # 타임프레임 → 분 단위 매핑
    TF_MINUTES = {
        "1m": 1, "5m": 5, "15m": 15,
        "1h": 60, "4h": 240, "1d": 1440,
    }

    def __init__(
        self,
        base_price: float = 82000.0,
        volatility: float = 0.002,
        trend: float = 0.0001,
        seed: Optional[int] = None,
    ):
        self.base_price = base_price
        self.volatility = volatility
        self.trend = trend
        self._current_price = base_price
        self._rng = np.random.default_rng(seed)
        # 캔들 캐시 (재현성)
        self._candle_cache: dict[str, pd.DataFrame] = {}

    def _generate_candles(self, timeframe: str, limit: int) -> pd.DataFrame:
        """현실적인 OHLCV 캔들 생성"""
        minutes = self.TF_MINUTES.get(timeframe, 60)
        now = datetime.utcnow()

        timestamps = []
        opens, highs, lows, closes, volumes = [], [], [], [], []

        price = self.base_price * (1 + self._rng.normal(0, 0.05))
        vol_scale = minutes ** 0.5  # 타임프레임에 따른 변동성 스케일링

        for i in range(limit):
            ts = now - timedelta(minutes=minutes * (limit - i))
            timestamps.append(ts)

            # 추세 + 랜덤 워크 + 변동성 클러스터링
            drift = self.trend * minutes
            shock = self._rng.normal(0, self.volatility * vol_scale)
            # 간헐적 큰 변동 (팻 테일)
            if self._rng.random() < 0.05:
                shock *= 3.0

            ret = drift + shock
            new_price = price * (1 + ret)

            o = price
            c = new_price
            # 캔들 내 변동
            wick = abs(c - o) * self._rng.uniform(0.1, 0.8)
            h = max(o, c) + wick
            l = min(o, c) - wick * self._rng.uniform(0.5, 1.0)

            # 거래량 (가격 변동 클수록 거래량 높음)
            base_vol = 100 + self._rng.exponential(200)
            vol = base_vol * (1 + abs(ret) * 50) * minutes

            opens.append(round(o, 2))
            highs.append(round(h, 2))
            lows.append(round(max(l, 0.01), 2))
            closes.append(round(c, 2))
            volumes.append(round(vol, 2))

            price = new_price

        self._current_price = closes[-1]

        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        })
        df.set_index("timestamp", inplace=True)
        return df

    async def get_candles(
        self, symbol: str, timeframe: str, limit: int = 200
    ) -> pd.DataFrame:
        cache_key = f"{symbol}_{timeframe}"
        if cache_key not in self._candle_cache:
            self._candle_cache[cache_key] = self._generate_candles(timeframe, limit)
        return self._candle_cache[cache_key]

    async def get_current_price(self, symbol: str) -> float:
        return round(self._current_price, 2)

    async def get_orderbook(self, symbol: str) -> dict:
        spread = self._current_price * 0.0001  # 0.01% 스프레드
        return {
            "bid": round(self._current_price - spread / 2, 2),
            "ask": round(self._current_price + spread / 2, 2),
            "spread": round(spread, 2),
        }

    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        # -0.1% ~ +0.1% 사이의 펀딩비
        return round(self._rng.normal(0.01, 0.03), 4)

    async def get_open_interest(self, symbol: str) -> Optional[float]:
        # 가상 OI (BTC 기준)
        return round(self._rng.uniform(8_000_000_000, 15_000_000_000), 0)

    def refresh(self) -> None:
        """캐시 초기화 → 다음 호출 시 새 데이터 생성"""
        self._candle_cache.clear()
