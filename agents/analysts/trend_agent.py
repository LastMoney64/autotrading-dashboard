"""
Trend Agent — 추세 추종 (코드 기반, 비용 $0)

EMA (20/50/200), MACD, ADX 기반으로 추세 방향과 강도를 분석.
"""

from core.base_agent import BaseAgent, AnalysisResult
from data.indicators import IndicatorEngine
from agents.analysts.rule_based_mixin import RuleBasedAnalyst


class TrendAgent(BaseAgent):

    INDICATORS = ["ema_20", "ema_50", "ema_200", "macd", "macd_signal", "macd_histogram", "adx"]

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        candles_1h = market_data.get("candles", {}).get("1h")
        candles_4h = market_data.get("candles", {}).get("4h")

        ind_1h = IndicatorEngine.compute_for_agent(candles_1h, self.INDICATORS) if candles_1h is not None else {}
        ind_4h = IndicatorEngine.compute_for_agent(candles_4h, self.INDICATORS) if candles_4h is not None else {}

        buy_score = 0.0
        sell_score = 0.0
        reasons = []

        # EMA 정배열/역배열
        ema20 = ind_1h.get("ema_20", 0)
        ema50 = ind_1h.get("ema_50", 0)
        ema200 = ind_1h.get("ema_200", 0)

        if ema20 and ema50 and ema200:
            if ema20 > ema50 > ema200:
                buy_score += 2.0
                reasons.append("EMA 정배열(20>50>200) — 강한 상승")
            elif ema20 < ema50 < ema200:
                sell_score += 2.0
                reasons.append("EMA 역배열(20<50<200) — 강한 하락")
            elif ema20 > ema50:
                buy_score += 1.0
                reasons.append("EMA 20>50 — 단기 상승")
            elif ema20 < ema50:
                sell_score += 1.0
                reasons.append("EMA 20<50 — 단기 하락")

        # MACD 크로스
        macd_hist = ind_1h.get("macd_histogram", 0)
        if macd_hist and macd_hist > 0:
            buy_score += 1.0
            reasons.append(f"MACD 양수({macd_hist:.2f})")
        elif macd_hist and macd_hist < 0:
            sell_score += 1.0
            reasons.append(f"MACD 음수({macd_hist:.2f})")

        # ADX 추세 강도
        adx = ind_1h.get("adx", 0)
        if adx and adx > 30:
            reasons.append(f"강한 추세(ADX {adx:.0f})")
            # 추세 방향에 가점
            if buy_score > sell_score:
                buy_score += 1.0
            elif sell_score > buy_score:
                sell_score += 1.0
        elif adx and adx < 20:
            reasons.append(f"추세 약함(ADX {adx:.0f})")

        # 4H 추세 확인
        ema20_4h = ind_4h.get("ema_20", 0)
        ema50_4h = ind_4h.get("ema_50", 0)
        if ema20_4h and ema50_4h:
            if ema20_4h > ema50_4h:
                buy_score += 0.5
                reasons.append("4H 상승 추세")
            else:
                sell_score += 0.5
                reasons.append("4H 하락 추세")

        return RuleBasedAnalyst.build_result(
            self.agent_id, buy_score, sell_score, reasons, ind_1h
        )

    async def respond_to_debate(self, own, others, context) -> str:
        return f"{self.agent_id}: {own.signal.value} ({own.confidence:.0%}) — {own.reasoning}"
