"""
SignalFilter — 국면 적응형 신호 필터

핵심 원칙: "추세의 친구가 되자"
- 추세장: 추세를 따라간다 (하락추세 → SELL만, 상승추세 → BUY만)
- 횡보장: 역추세 매매 허용 (RSI 과매도 → BUY, 과매수 → SELL)
- 극단적 공포: 컨트리언 전략 (바닥 신호)

17% 승률의 원인: 하락추세에서 RSI 과매도 = BUY → 떨어지는 칼날
수정: 하락추세에서 RSI 과매도는 무시, 반등 SELL만 허용
"""

import logging
import time
import pandas as pd
from data.indicators import IndicatorEngine

logger = logging.getLogger(__name__)


class SignalFilter:
    """국면 적응형 신호 필터 — 추세의 친구가 되는 전략"""

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
        self._cooldown_seconds = 900  # 15분
        self._last_trigger_time: dict[str, float] = {}

    def check(
        self, symbol: str, candles_1h: list[dict],
        candles_15m: list[dict] = None,
        funding_rate: float = 0,
        market_regime: dict = None,
    ) -> dict:
        """국면 적응형 신호 감지"""
        if not candles_1h or len(candles_1h) < 50:
            return self._no_signal("데이터 부족")

        df_1h = pd.DataFrame(candles_1h)
        df_15m = pd.DataFrame(candles_15m) if candles_15m and len(candles_15m) > 20 else None
        indicators = IndicatorEngine.compute_all(df_1h)

        # 국면 정보
        regime_label = ""
        if market_regime:
            regime_label = market_regime.get("regime_label", "")

        is_strong_uptrend = regime_label in ("강한 상승추세", "상승추세")
        is_strong_downtrend = regime_label in ("강한 하락추세", "하락추세")
        is_ranging = regime_label in ("횡보", "")
        is_volatile = regime_label == "급변"

        signals = []
        buy_signals = 0
        sell_signals = 0

        # 기본 지표
        rsi = indicators.get("rsi", 50)
        adx = indicators.get("adx", 0)
        price = indicators.get("current_price", 0)
        ema_20 = indicators.get("ema_20", 0)
        ema_50 = indicators.get("ema_50", 0)
        stoch_k = indicators.get("stochastic_k", 50)
        stoch_d = indicators.get("stochastic_d", 50)
        bb_upper = indicators.get("bollinger_upper", 0)
        bb_lower = indicators.get("bollinger_lower", 0)
        bb_mid = indicators.get("bollinger_mid", 0)

        # ══════════════════════════════════════════════════
        # 국면별 신호 해석 (핵심 변경)
        # ══════════════════════════════════════════════════

        if is_strong_downtrend:
            # ── 하락추세: 추세 추종 (SELL만) ────────────────
            # RSI 과매도 = 더 떨어진다 (BUY 아님!)
            # RSI 과매수/반등 = SELL 기회

            if rsi >= 55 and rsi <= 70:
                # 하락추세 중 반등 → 숏 기회
                signals.append(f"하락추세 반등 SELL (RSI {rsi:.0f})")
                sell_signals += 2

            if adx >= self.adx_trend_threshold and ema_20 and ema_50 and ema_20 < ema_50:
                signals.append(f"하락추세 확인 (ADX {adx:.0f})")
                sell_signals += 1

            # MACD 데드크로스
            if len(df_1h) >= 3:
                macd_data = IndicatorEngine.macd(df_1h)
                hist = macd_data["macd_histogram"]
                if len(hist) >= 2 and float(hist.iloc[-2]) > 0 and float(hist.iloc[-1]) < 0:
                    signals.append("MACD 데드크로스")
                    sell_signals += 1

            # EMA 데드크로스 15m
            if df_15m is not None and len(df_15m) >= 50:
                ema20_15m = IndicatorEngine.ema(df_15m, 20)
                ema50_15m = IndicatorEngine.ema(df_15m, 50)
                if len(ema20_15m) >= 2 and len(ema50_15m) >= 2:
                    prev = float(ema20_15m.iloc[-2] - ema50_15m.iloc[-2])
                    curr = float(ema20_15m.iloc[-1] - ema50_15m.iloc[-1])
                    if prev > 0 and curr < 0:
                        signals.append("EMA 데드크로스(15m)")
                        sell_signals += 1

        elif is_strong_uptrend:
            # ── 상승추세: 추세 추종 (BUY만) ────────────────
            # RSI 과매수 = 더 올라간다 (SELL 아님!)
            # RSI 과매도/눌림목 = BUY 기회

            if rsi <= 45 and rsi >= 30:
                # 상승추세 중 눌림목 → 롱 기회
                signals.append(f"상승추세 눌림목 BUY (RSI {rsi:.0f})")
                buy_signals += 2

            if adx >= self.adx_trend_threshold and ema_20 and ema_50 and ema_20 > ema_50:
                signals.append(f"상승추세 확인 (ADX {adx:.0f})")
                buy_signals += 1

            # MACD 골든크로스
            if len(df_1h) >= 3:
                macd_data = IndicatorEngine.macd(df_1h)
                hist = macd_data["macd_histogram"]
                if len(hist) >= 2 and float(hist.iloc[-2]) < 0 and float(hist.iloc[-1]) > 0:
                    signals.append("MACD 골든크로스")
                    buy_signals += 1

            # EMA 골든크로스 15m
            if df_15m is not None and len(df_15m) >= 50:
                ema20_15m = IndicatorEngine.ema(df_15m, 20)
                ema50_15m = IndicatorEngine.ema(df_15m, 50)
                if len(ema20_15m) >= 2 and len(ema50_15m) >= 2:
                    prev = float(ema20_15m.iloc[-2] - ema50_15m.iloc[-2])
                    curr = float(ema20_15m.iloc[-1] - ema50_15m.iloc[-1])
                    if prev < 0 and curr > 0:
                        signals.append("EMA 골든크로스(15m)")
                        buy_signals += 1

        else:
            # ── 횡보장/급변장: 역추세 매매 허용 ───────────
            # 여기서만 RSI 과매수/과매도 역추세 사용

            if rsi <= self.rsi_oversold:
                signals.append(f"RSI 과매도({rsi:.0f})")
                buy_signals += 1
            elif rsi >= self.rsi_overbought:
                signals.append(f"RSI 과매수({rsi:.0f})")
                sell_signals += 1

            if stoch_k <= 20 and stoch_d <= 20:
                signals.append(f"스토캐스틱 과매도({stoch_k:.0f})")
                buy_signals += 1
            elif stoch_k >= 80 and stoch_d >= 80:
                signals.append(f"스토캐스틱 과매수({stoch_k:.0f})")
                sell_signals += 1

            # BB 이탈 (횡보장에서 효과적)
            if price and bb_lower and price <= bb_lower:
                signals.append("BB 하단 이탈")
                buy_signals += 1
            elif price and bb_upper and price >= bb_upper:
                signals.append("BB 상단 이탈")
                sell_signals += 1

            # MACD 크로스
            if len(df_1h) >= 3:
                macd_data = IndicatorEngine.macd(df_1h)
                hist = macd_data["macd_histogram"]
                if len(hist) >= 2:
                    if float(hist.iloc[-2]) < 0 and float(hist.iloc[-1]) > 0:
                        signals.append("MACD 골든크로스")
                        buy_signals += 1
                    elif float(hist.iloc[-2]) > 0 and float(hist.iloc[-1]) < 0:
                        signals.append("MACD 데드크로스")
                        sell_signals += 1

        # ══════════════════════════════════════════════════
        # 공통 신호 (모든 국면)
        # ══════════════════════════════════════════════════

        # 거래량 급등
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

        # 펀딩비 극단값
        if funding_rate:
            if funding_rate > 0.01:
                signals.append(f"펀딩비 롱과열({funding_rate:.4f}%)")
                sell_signals += 1
            elif funding_rate < -0.01:
                signals.append(f"펀딩비 숏과열({funding_rate:.4f}%)")
                buy_signals += 1

        # 멀티 타임프레임 정렬
        if market_regime and market_regime.get("timeframe_alignment"):
            align_dir = market_regime.get("alignment_direction", "MIXED")
            if align_dir == "BUY":
                signals.append(f"TF정렬 BUY")
                buy_signals += 1
            elif align_dir == "SELL":
                signals.append(f"TF정렬 SELL")
                sell_signals += 1

        # RSI 다이버전스 (모든 국면에서 강력)
        div = indicators.get("divergence", {})
        if div.get("bullish"):
            signals.append("RSI 강세 다이버전스")
            buy_signals += 2
        elif div.get("bearish"):
            signals.append("RSI 약세 다이버전스")
            sell_signals += 2

        # 지지/저항 컨플루언스
        support_levels = indicators.get("support_levels", [])
        resistance_levels = indicators.get("resistance_levels", [])
        if price and support_levels:
            nearest = min(support_levels, key=lambda s: abs(price - s))
            if abs(price - nearest) / price < 0.005:
                signals.append(f"지지선 근처")
                buy_signals += 1
        if price and resistance_levels:
            nearest = min(resistance_levels, key=lambda r: abs(price - r))
            if abs(price - nearest) / price < 0.005:
                signals.append(f"저항선 근처")
                sell_signals += 1

        # ══════════════════════════════════════════════════
        # 판정
        # ══════════════════════════════════════════════════
        signal_count = len(signals)
        direction_count = max(buy_signals, sell_signals)

        should_trigger = (
            signal_count >= self.min_signals_to_trigger
            and direction_count >= 2
        )

        # 쿨다운
        now = time.time()
        last_trigger = self._last_trigger_time.get(symbol, 0)
        if should_trigger and (now - last_trigger) < self._cooldown_seconds:
            should_trigger = False

        # 방향 결정
        if buy_signals > sell_signals:
            direction = "BUY"
        elif sell_signals > buy_signals:
            direction = "SELL"
        else:
            direction = "NEUTRAL"

        if direction == "NEUTRAL":
            should_trigger = False

        # ── 추세 역행 최종 차단 ──────────────────────────
        # 강한 하락추세에서 BUY 신호 → 차단
        if is_strong_downtrend and direction == "BUY":
            should_trigger = False
            if signal_count >= 2:
                logger.debug(f"[{symbol}] 하락추세에서 BUY 차단 (떨어지는 칼날 방지)")

        # 강한 상승추세에서 SELL 신호 → 차단
        if is_strong_uptrend and direction == "SELL":
            should_trigger = False
            if signal_count >= 2:
                logger.debug(f"[{symbol}] 상승추세에서 SELL 차단 (추세 역행 방지)")

        if should_trigger:
            self._last_trigger_time[symbol] = now

        reason = " + ".join(signals) if signals else "신호 없음"
        if should_trigger:
            logger.info(
                f"🔔 [{symbol}] 신호 감지! ({signal_count}개, "
                f"{direction} 방향 {direction_count}개, 국면={regime_label}) | {reason}"
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
            "market_regime": market_regime or {},
        }

    def _no_signal(self, reason: str) -> dict:
        return {
            "should_trigger": False, "signals": [], "signal_count": 0,
            "direction_hint": "NEUTRAL", "buy_signals": 0, "sell_signals": 0,
            "direction_strength": 0, "indicators": {}, "reason": reason,
        }
