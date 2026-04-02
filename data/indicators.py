"""
Indicators — 기술적 지표 계산 엔진

pandas DataFrame에 지표를 추가한다.
각 에이전트가 필요한 지표만 선택적으로 계산.
"""

import pandas as pd
import numpy as np
from typing import Optional


class IndicatorEngine:
    """기술적 지표 계산기"""

    # ── 추세 지표 ───────────────────────────────────────

    @staticmethod
    def ema(df: pd.DataFrame, period: int, column: str = "close") -> pd.Series:
        """지수이동평균 (EMA)"""
        return df[column].ewm(span=period, adjust=False).mean()

    @staticmethod
    def sma(df: pd.DataFrame, period: int, column: str = "close") -> pd.Series:
        """단순이동평균 (SMA)"""
        return df[column].rolling(window=period).mean()

    @staticmethod
    def macd(
        df: pd.DataFrame,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> dict[str, pd.Series]:
        """MACD (이동평균 수렴확산)"""
        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return {
            "macd": macd_line,
            "macd_signal": signal_line,
            "macd_histogram": histogram,
        }

    @staticmethod
    def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """ADX (평균방향지수) — 추세 강도"""
        high, low, close = df["high"], df["low"], df["close"]

        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        atr = tr.ewm(span=period, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
        minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)

        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10))
        adx_val = dx.ewm(span=period, adjust=False).mean()
        return adx_val

    # ── 모멘텀 지표 ─────────────────────────────────────

    @staticmethod
    def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """RSI (상대강도지수)"""
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)

        avg_gain = gain.ewm(span=period, adjust=False).mean()
        avg_loss = loss.ewm(span=period, adjust=False).mean()

        rs = avg_gain / (avg_loss + 1e-10)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def stochastic(
        df: pd.DataFrame,
        k_period: int = 14,
        d_period: int = 3,
    ) -> dict[str, pd.Series]:
        """스토캐스틱 오실레이터"""
        low_min = df["low"].rolling(window=k_period).min()
        high_max = df["high"].rolling(window=k_period).max()
        k = 100 * ((df["close"] - low_min) / (high_max - low_min + 1e-10))
        d = k.rolling(window=d_period).mean()
        return {"stochastic_k": k, "stochastic_d": d}

    @staticmethod
    def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
        """CCI (상품채널지수)"""
        tp = (df["high"] + df["low"] + df["close"]) / 3
        sma = tp.rolling(window=period).mean()
        mad = tp.rolling(window=period).apply(
            lambda x: np.abs(x - x.mean()).mean(), raw=True
        )
        return (tp - sma) / (0.015 * mad + 1e-10)

    # ── 변동성 지표 ─────────────────────────────────────

    @staticmethod
    def bollinger_bands(
        df: pd.DataFrame, period: int = 20, std_dev: float = 2.0
    ) -> dict[str, pd.Series]:
        """볼린저 밴드"""
        mid = df["close"].rolling(window=period).mean()
        std = df["close"].rolling(window=period).std()
        return {
            "bollinger_upper": mid + std_dev * std,
            "bollinger_mid": mid,
            "bollinger_lower": mid - std_dev * std,
        }

    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """ATR (평균진폭)"""
        high, low, close = df["high"], df["low"], df["close"]
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    # ── 거래량 지표 ─────────────────────────────────────

    @staticmethod
    def obv(df: pd.DataFrame) -> pd.Series:
        """OBV (거래량 균형)"""
        direction = np.sign(df["close"].diff())
        return (direction * df["volume"]).cumsum()

    @staticmethod
    def vwap(df: pd.DataFrame) -> pd.Series:
        """VWAP (거래량가중평균가격)"""
        tp = (df["high"] + df["low"] + df["close"]) / 3
        cum_tp_vol = (tp * df["volume"]).cumsum()
        cum_vol = df["volume"].cumsum()
        return cum_tp_vol / (cum_vol + 1e-10)

    # ── 다이버전스 감지 ─────────────────────────────────

    @staticmethod
    def detect_divergence(df: pd.DataFrame, lookback: int = 20) -> dict:
        """
        RSI 다이버전스 감지

        - 강세 다이버전스: 가격 신저가 + RSI 올라감 → BUY 신호
        - 약세 다이버전스: 가격 신고가 + RSI 내려감 → SELL 신호
        """
        if len(df) < lookback + 5:
            return {"bullish": False, "bearish": False, "type": None}

        close = df["close"].values
        rsi_series = IndicatorEngine.rsi(df)
        rsi_vals = rsi_series.values

        recent = slice(-lookback, None)
        price_recent = close[recent]
        rsi_recent = rsi_vals[recent]

        # 유효 데이터 체크
        if len(price_recent) < 10 or any(pd.isna(rsi_recent[-10:])):
            return {"bullish": False, "bearish": False, "type": None}

        # 최근 구간을 절반으로 나눠서 비교
        half = len(price_recent) // 2
        price_first = price_recent[:half]
        price_second = price_recent[half:]
        rsi_first = rsi_recent[:half]
        rsi_second = rsi_recent[half:]

        price_low1 = float(np.min(price_first))
        price_low2 = float(np.min(price_second))
        price_high1 = float(np.max(price_first))
        price_high2 = float(np.max(price_second))
        rsi_low1 = float(np.nanmin(rsi_first))
        rsi_low2 = float(np.nanmin(rsi_second))
        rsi_high1 = float(np.nanmax(rsi_first))
        rsi_high2 = float(np.nanmax(rsi_second))

        # 강세 다이버전스: 가격 더 낮은데 RSI는 더 높음
        bullish = price_low2 < price_low1 and rsi_low2 > rsi_low1 + 3

        # 약세 다이버전스: 가격 더 높은데 RSI는 더 낮음
        bearish = price_high2 > price_high1 and rsi_high2 < rsi_high1 - 3

        div_type = None
        if bullish:
            div_type = "bullish"
        elif bearish:
            div_type = "bearish"

        return {"bullish": bullish, "bearish": bearish, "type": div_type}

    # ── 지지/저항 ───────────────────────────────────────

    @staticmethod
    def support_resistance(
        df: pd.DataFrame, window: int = 20, num_levels: int = 3
    ) -> dict[str, list[float]]:
        """간단한 지지/저항 레벨 계산 (피봇 포인트 기반)"""
        recent = df.tail(window)
        pivot = (recent["high"].max() + recent["low"].min() + recent["close"].iloc[-1]) / 3

        r1 = 2 * pivot - recent["low"].min()
        r2 = pivot + (recent["high"].max() - recent["low"].min())
        s1 = 2 * pivot - recent["high"].max()
        s2 = pivot - (recent["high"].max() - recent["low"].min())

        return {
            "support_levels": sorted([round(s2, 2), round(s1, 2), round(pivot, 2)]),
            "resistance_levels": sorted([round(pivot, 2), round(r1, 2), round(r2, 2)]),
        }

    @staticmethod
    def fibonacci_levels(df: pd.DataFrame, lookback: int = 100) -> dict[str, float]:
        """피보나치 되돌림 레벨"""
        recent = df.tail(lookback)
        high = recent["high"].max()
        low = recent["low"].min()
        diff = high - low

        return {
            "fib_0": round(high, 2),
            "fib_236": round(high - 0.236 * diff, 2),
            "fib_382": round(high - 0.382 * diff, 2),
            "fib_500": round(high - 0.500 * diff, 2),
            "fib_618": round(high - 0.618 * diff, 2),
            "fib_786": round(high - 0.786 * diff, 2),
            "fib_1": round(low, 2),
        }

    # ── 일괄 계산 ───────────────────────────────────────

    @classmethod
    def compute_all(cls, df: pd.DataFrame) -> dict:
        """모든 지표를 한 번에 계산하여 딕셔너리로 반환"""
        # list → DataFrame 자동 변환
        if isinstance(df, list):
            df = pd.DataFrame(df)
        if len(df) < 5:
            return {"current_price": 0}
        last = df.iloc[-1]
        result = {}

        # EMA
        for p in [20, 50, 200]:
            ema_series = cls.ema(df, p)
            if len(ema_series) > 0:
                result[f"ema_{p}"] = round(float(ema_series.iloc[-1]), 2)

        # MACD
        macd_data = cls.macd(df)
        for key, series in macd_data.items():
            result[key] = round(float(series.iloc[-1]), 4)

        # ADX
        result["adx"] = round(float(cls.adx(df).iloc[-1]), 2)

        # RSI
        result["rsi"] = round(float(cls.rsi(df).iloc[-1]), 2)

        # Stochastic
        stoch = cls.stochastic(df)
        for key, series in stoch.items():
            result[key] = round(float(series.iloc[-1]), 2)

        # CCI
        result["cci"] = round(float(cls.cci(df).iloc[-1]), 2)

        # Bollinger Bands
        bb = cls.bollinger_bands(df)
        for key, series in bb.items():
            result[key] = round(float(series.iloc[-1]), 2)

        # 현재가 (ATR % 계산에 필요하므로 먼저 정의)
        current_price = float(last["close"])

        # ATR (절대값 + 현재가 대비 %)
        atr_val = float(cls.atr(df).iloc[-1])
        result["atr"] = round(atr_val, 2)
        result["atr_pct"] = round(atr_val / current_price * 100, 4) if current_price else 0

        # OBV
        result["obv"] = round(float(cls.obv(df).iloc[-1]), 2)

        # VWAP
        result["vwap"] = round(float(cls.vwap(df).iloc[-1]), 2)

        # Divergence (RSI 다이버전스)
        div = cls.detect_divergence(df)
        result["divergence"] = div

        # Support/Resistance
        sr = cls.support_resistance(df)
        result.update(sr)

        # Fibonacci
        fib = cls.fibonacci_levels(df)
        result.update(fib)

        # 기본 가격 정보
        result["current_price"] = round(current_price, 2)
        result["volume_24h"] = round(float(df["volume"].tail(24).sum()), 2)

        return result

    @classmethod
    def compute_for_agent(cls, df, indicator_names: list[str]) -> dict:
        """특정 에이전트가 필요한 지표만 선택적으로 계산"""
        if isinstance(df, list):
            df = pd.DataFrame(df)
        all_indicators = cls.compute_all(df)
        result = {"current_price": all_indicators.get("current_price")}
        for name in indicator_names:
            if name in all_indicators:
                result[name] = all_indicators[name]
        return result
