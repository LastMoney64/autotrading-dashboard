"""
MarketRegime — 시장 국면 감지기 + 멀티 타임프레임 정렬

시장 상태를 자동 분류하고, 국면별로 전략 파라미터를 조정한다.
비용: $0 (코드 기반)
"""

import pandas as pd
from data.indicators import IndicatorEngine
from enum import Enum


class Regime(Enum):
    STRONG_UPTREND = "strong_uptrend"    # 강한 상승추세
    UPTREND = "uptrend"                  # 상승추세
    RANGING = "ranging"                  # 횡보
    DOWNTREND = "downtrend"              # 하락추세
    STRONG_DOWNTREND = "strong_downtrend" # 강한 하락추세
    VOLATILE = "volatile"                # 급변


class MarketRegimeDetector:
    """시장 국면 감지 + 멀티 타임프레임 정렬"""

    def detect(
        self,
        candles_15m: list[dict],
        candles_1h: list[dict],
        candles_4h: list[dict],
    ) -> dict:
        """
        시장 국면 + 타임프레임 정렬 분석

        Returns:
            {
                "regime": Regime,
                "regime_label": "강한 상승추세",
                "timeframe_alignment": True/False,
                "alignment_direction": "BUY"/"SELL"/"MIXED",
                "tf_signals": {"15m": "BUY", "1h": "BUY", "4h": "BUY"},
                "confidence_boost": 0.0~0.3,
                "strategy_params": { ... },
            }
        """
        # 각 타임프레임 분석
        tf_15m = self._analyze_timeframe(candles_15m, "15m") if candles_15m and len(candles_15m) > 50 else None
        tf_1h = self._analyze_timeframe(candles_1h, "1h") if candles_1h and len(candles_1h) > 50 else None
        tf_4h = self._analyze_timeframe(candles_4h, "4h") if candles_4h and len(candles_4h) > 50 else None

        # 1H 기준으로 국면 결정
        regime = self._classify_regime(tf_1h, tf_4h)

        # 멀티 타임프레임 정렬 확인
        tf_signals = {}
        directions = []
        for label, tf in [("15m", tf_15m), ("1h", tf_1h), ("4h", tf_4h)]:
            if tf:
                tf_signals[label] = tf["direction"]
                directions.append(tf["direction"])

        # 정렬 판정
        if len(directions) >= 2:
            buy_count = directions.count("BUY")
            sell_count = directions.count("SELL")

            if buy_count >= 2:
                alignment = True
                alignment_dir = "BUY"
            elif sell_count >= 2:
                alignment = True
                alignment_dir = "SELL"
            else:
                alignment = False
                alignment_dir = "MIXED"
        else:
            alignment = False
            alignment_dir = "MIXED"

        # 3개 전부 같은 방향이면 확신도 부스트
        if len(directions) >= 3 and len(set(directions)) == 1 and directions[0] != "NEUTRAL":
            confidence_boost = 0.2
        elif alignment:
            confidence_boost = 0.1
        else:
            confidence_boost = 0.0

        # 국면별 전략 파라미터
        strategy_params = self._get_strategy_params(regime)

        regime_labels = {
            Regime.STRONG_UPTREND: "강한 상승추세",
            Regime.UPTREND: "상승추세",
            Regime.RANGING: "횡보",
            Regime.DOWNTREND: "하락추세",
            Regime.STRONG_DOWNTREND: "강한 하락추세",
            Regime.VOLATILE: "급변",
        }

        return {
            "regime": regime,
            "regime_label": regime_labels.get(regime, "불명"),
            "timeframe_alignment": alignment,
            "alignment_direction": alignment_dir,
            "tf_signals": tf_signals,
            "confidence_boost": confidence_boost,
            "strategy_params": strategy_params,
        }

    def _analyze_timeframe(self, candles: list[dict], label: str) -> dict:
        """단일 타임프레임 분석"""
        df = pd.DataFrame(candles)
        indicators = IndicatorEngine.compute_all(df)

        ema_20 = indicators.get("ema_20", 0)
        ema_50 = indicators.get("ema_50", 0)
        rsi = indicators.get("rsi", 50)
        adx = indicators.get("adx", 0)
        macd_hist = indicators.get("macd_histogram", 0)

        # 방향 판단
        score = 0
        if ema_20 and ema_50:
            if ema_20 > ema_50:
                score += 1
            else:
                score -= 1

        if rsi > 55:
            score += 1
        elif rsi < 45:
            score -= 1

        if macd_hist and macd_hist > 0:
            score += 1
        elif macd_hist and macd_hist < 0:
            score -= 1

        if score >= 2:
            direction = "BUY"
        elif score <= -2:
            direction = "SELL"
        else:
            direction = "NEUTRAL"

        return {
            "label": label,
            "direction": direction,
            "score": score,
            "adx": adx,
            "rsi": rsi,
            "ema_trend": "up" if ema_20 and ema_50 and ema_20 > ema_50 else "down",
        }

    def _classify_regime(self, tf_1h: dict, tf_4h: dict) -> Regime:
        """1H + 4H 데이터로 시장 국면 분류"""
        if not tf_1h:
            return Regime.RANGING

        adx_1h = tf_1h.get("adx", 0)
        score_1h = tf_1h.get("score", 0)
        adx_4h = tf_4h.get("adx", 0) if tf_4h else 0

        # ATR 급등 체크 (급변장)
        if adx_1h > 50:
            return Regime.VOLATILE

        # 추세 강도 분류
        if adx_1h > 30:
            if score_1h >= 2:
                return Regime.STRONG_UPTREND
            elif score_1h <= -2:
                return Regime.STRONG_DOWNTREND
            elif score_1h > 0:
                return Regime.UPTREND
            else:
                return Regime.DOWNTREND
        elif adx_1h > 20:
            if score_1h > 0:
                return Regime.UPTREND
            elif score_1h < 0:
                return Regime.DOWNTREND
            else:
                return Regime.RANGING
        else:
            return Regime.RANGING

    def _get_strategy_params(self, regime: Regime) -> dict:
        """국면별 전략 파라미터"""
        if regime in (Regime.STRONG_UPTREND, Regime.STRONG_DOWNTREND):
            return {
                "use_trailing_stop": True,
                "trailing_pct": 0.5,  # 0.5% 트레일링
                "position_size_multiplier": 1.3,  # 큰 포지션
                "min_confidence": 0.35,
                "leverage_multiplier": 1.2,
                "description": "강한 추세 — 트레일링 스탑, 큰 포지션",
            }
        elif regime in (Regime.UPTREND, Regime.DOWNTREND):
            return {
                "use_trailing_stop": True,
                "trailing_pct": 0.8,
                "position_size_multiplier": 1.0,
                "min_confidence": 0.4,
                "leverage_multiplier": 1.0,
                "description": "추세장 — 트레일링 스탑, 표준 포지션",
            }
        elif regime == Regime.RANGING:
            return {
                "use_trailing_stop": False,
                "trailing_pct": 0,
                "position_size_multiplier": 0.7,  # 작은 포지션
                "min_confidence": 0.5,  # 높은 확신 필요
                "leverage_multiplier": 0.8,
                "description": "횡보장 — 고정 TP/SL, 작은 포지션",
            }
        else:  # VOLATILE
            return {
                "use_trailing_stop": False,
                "trailing_pct": 0,
                "position_size_multiplier": 0.5,  # 최소 포지션
                "min_confidence": 0.6,  # 매우 높은 확신 필요
                "leverage_multiplier": 0.5,  # 레버리지 절반
                "description": "급변장 — 최소 포지션, 저레버리지",
            }
