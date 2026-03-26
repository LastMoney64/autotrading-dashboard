"""
SignalFilter — 사전 필터링 엔진 (무료, 코드 기반)

비용 최적화: 지표 스캔은 무료, AI 토론은 강한 신호에서만 발동
방향이 명확할 때만 토론 → 적극적 매매
"""

import logging
import time
import pandas as pd
from data.indicators import IndicatorEngine

logger = logging.getLogger(__name__)


class SignalFilter:
    """지표 기반 사전 필터링 — AI 호출 전 게이트키퍼"""

    def __init__(
        self,
        rsi_oversold: float = 30,
        rsi_overbought: float = 70,
        adx_trend_threshold: float = 25,
        bb_squeeze_threshold: float = 0.02,
        volume_spike_multiplier: float = 1.8,
        min_signals_to_trigger: int = 2,
    ):
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.adx_trend_threshold = adx_trend_threshold
        self.bb_squeeze_threshold = bb_squeeze_threshold
        self.volume_spike_multiplier = volume_spike_multiplier
        self.min_signals_to_trigger = min_signals_to_trigger

        # 쿨다운: 30분 (초 단위)
        self._cooldown_seconds = 1800
        self._last_trigger_time: dict[str, float] = {}

    def check(self, symbol: str, candles_1h: list[dict], candles_15m: list[dict] = None) -> dict:
        """신호 감지 여부 확인"""
        if not candles_1h or len(candles_1h) < 50:
            return self._no_signal("데이터 부족")

        df_1h = pd.DataFrame(candles_1h)
        df_15m = pd.DataFrame(candles_15m) if candles_15m and len(candles_15m) > 20 else None

        indicators = IndicatorEngine.compute_all(df_1h)

        signals = []
        buy_signals = 0
        sell_signals = 0

        # ── 1. RSI 과매수/과매도 ───────────────────────
        rsi = indicators.get("rsi", 50)
        if rsi <= self.rsi_oversold:
            signals.append(f"RSI 과매도({rsi:.0f})")
            buy_signals += 1
        elif rsi >= self.rsi_overbought:
            signals.append(f"RSI 과매수({rsi:.0f})")
            sell_signals += 1

        # ── 2. MACD 크로스 ─────────────────────────────
        if len(df_1h) >= 3:
            macd_data = IndicatorEngine.macd(df_1h)
            hist_series = macd_data["macd_histogram"]
            if len(hist_series) >= 2:
                prev_hist = float(hist_series.iloc[-2])
                curr_hist = float(hist_series.iloc[-1])
                if prev_hist < 0 and curr_hist > 0:
                    signals.append("MACD 골든크로스")
                    buy_signals += 1
                elif prev_hist > 0 and curr_hist < 0:
                    signals.append("MACD 데드크로스")
                    sell_signals += 1

        # ── 3. 볼린저밴드 이탈 ──────────────────────────
        price = indicators.get("current_price", 0)
        bb_upper = indicators.get("bollinger_upper", 0)
        bb_lower = indicators.get("bollinger_lower", 0)
        bb_mid = indicators.get("bollinger_mid", 0)

        if price and bb_lower and price <= bb_lower:
            signals.append("BB 하단 이탈")
            buy_signals += 1
        elif price and bb_upper and price >= bb_upper:
            signals.append("BB 상단 이탈")
            sell_signals += 1

        if bb_upper and bb_lower and bb_mid:
            bb_width = (bb_upper - bb_lower) / (bb_mid + 1e-10)
            if bb_width < self.bb_squeeze_threshold:
                signals.append(f"BB 스퀴즈(폭 {bb_width:.3f})")

        # ── 4. ADX 강한 추세 ───────────────────────────
        adx = indicators.get("adx", 0)
        if adx >= self.adx_trend_threshold:
            ema_20 = indicators.get("ema_20", 0)
            ema_50 = indicators.get("ema_50", 0)
            if ema_20 and ema_50:
                if ema_20 > ema_50:
                    signals.append(f"강한 상승추세(ADX {adx:.0f})")
                    buy_signals += 1
                else:
                    signals.append(f"강한 하락추세(ADX {adx:.0f})")
                    sell_signals += 1

        # ── 5. 거래량 급등 ─────────────────────────────
        if len(df_1h) >= 20:
            vol_current = float(df_1h["volume"].iloc[-1])
            vol_avg = float(df_1h["volume"].tail(20).mean())
            if vol_avg > 0 and vol_current > vol_avg * self.volume_spike_multiplier:
                ratio = vol_current / vol_avg
                signals.append(f"거래량 급등({ratio:.1f}x)")
                price_change = float(df_1h["close"].iloc[-1] - df_1h["close"].iloc[-2])
                if price_change > 0:
                    buy_signals += 1
                else:
                    sell_signals += 1

        # ── 6. EMA 크로스 (15분봉) ─────────────────────
        if df_15m is not None and len(df_15m) >= 50:
            ema20_15m = IndicatorEngine.ema(df_15m, 20)
            ema50_15m = IndicatorEngine.ema(df_15m, 50)
            if len(ema20_15m) >= 2 and len(ema50_15m) >= 2:
                prev_diff = float(ema20_15m.iloc[-2] - ema50_15m.iloc[-2])
                curr_diff = float(ema20_15m.iloc[-1] - ema50_15m.iloc[-1])
                if prev_diff < 0 and curr_diff > 0:
                    signals.append("EMA 20/50 골든크로스(15m)")
                    buy_signals += 1
                elif prev_diff > 0 and curr_diff < 0:
                    signals.append("EMA 20/50 데드크로스(15m)")
                    sell_signals += 1

        # ── 7. 스토캐스틱 극단값 ───────────────────────
        stoch_k = indicators.get("stochastic_k", 50)
        stoch_d = indicators.get("stochastic_d", 50)
        if stoch_k <= 20 and stoch_d <= 20:
            signals.append(f"스토캐스틱 과매도({stoch_k:.0f})")
            buy_signals += 1
        elif stoch_k >= 80 and stoch_d >= 80:
            signals.append(f"스토캐스틱 과매수({stoch_k:.0f})")
            sell_signals += 1

        # ── 판정 ───────────────────────────────────────
        signal_count = len(signals)
        direction_count = max(buy_signals, sell_signals)

        # 방향이 같은 신호가 2개 이상이어야 발동
        should_trigger = (
            signal_count >= self.min_signals_to_trigger
            and direction_count >= 2
        )

        # 쿨다운 체크 (30분)
        now = time.time()
        last_trigger = self._last_trigger_time.get(symbol, 0)
        if should_trigger and (now - last_trigger) < self._cooldown_seconds:
            remaining = int(self._cooldown_seconds - (now - last_trigger))
            logger.debug(f"[{symbol}] 쿨다운 중 (잔여 {remaining}초)")
            should_trigger = False

        # 방향 결정
        if buy_signals > sell_signals:
            direction = "BUY"
        elif sell_signals > buy_signals:
            direction = "SELL"
        else:
            direction = "NEUTRAL"

        # NEUTRAL이면 발동 안 함 (방향 불명확)
        if direction == "NEUTRAL":
            should_trigger = False

        # 쿨다운 업데이트
        if should_trigger:
            self._last_trigger_time[symbol] = now

        reason = " + ".join(signals) if signals else "신호 없음"

        if should_trigger:
            logger.info(
                f"🔔 [{symbol}] 신호 감지! ({signal_count}개, "
                f"{direction} 방향 {direction_count}개) | {reason}"
            )

        return {
            "should_trigger": should_trigger,
            "signals": signals,
            "signal_count": signal_count,
            "direction_hint": direction,
            "buy_signals": buy_signals,
            "sell_signals": sell_signals,
            "direction_strength": direction_count,
            "indicators": indicators,
            "reason": reason,
        }

    def _no_signal(self, reason: str) -> dict:
        return {
            "should_trigger": False,
            "signals": [],
            "signal_count": 0,
            "direction_hint": "NEUTRAL",
            "buy_signals": 0,
            "sell_signals": 0,
            "direction_strength": 0,
            "indicators": {},
            "reason": reason,
        }
