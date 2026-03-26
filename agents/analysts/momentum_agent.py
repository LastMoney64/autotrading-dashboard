"""
Momentum Agent — 모멘텀/반전 (코드 기반, 비용 $0)

RSI, 스토캐스틱, CCI 기반으로 과매수/과매도 및 모멘텀을 분석.
"""

from core.base_agent import BaseAgent, AnalysisResult
from data.indicators import IndicatorEngine
from agents.analysts.rule_based_mixin import RuleBasedAnalyst


class MomentumAgent(BaseAgent):

    INDICATORS = ["rsi", "stochastic_k", "stochastic_d", "cci"]

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        candles_1h = market_data.get("candles", {}).get("1h")
        ind = IndicatorEngine.compute_for_agent(candles_1h, self.INDICATORS) if candles_1h is not None else {}

        buy_score = 0.0
        sell_score = 0.0
        reasons = []

        rsi = ind.get("rsi", 50)
        if rsi <= 25:
            buy_score += 2.5
            reasons.append(f"RSI 극단 과매도({rsi:.0f})")
        elif rsi <= 35:
            buy_score += 1.5
            reasons.append(f"RSI 과매도({rsi:.0f})")
        elif rsi >= 75:
            sell_score += 2.5
            reasons.append(f"RSI 극단 과매수({rsi:.0f})")
        elif rsi >= 65:
            sell_score += 1.5
            reasons.append(f"RSI 과매수({rsi:.0f})")

        stoch_k = ind.get("stochastic_k", 50)
        stoch_d = ind.get("stochastic_d", 50)
        if stoch_k <= 20 and stoch_d <= 25:
            buy_score += 1.5
            reasons.append(f"스토캐스틱 과매도(K:{stoch_k:.0f})")
        elif stoch_k >= 80 and stoch_d >= 75:
            sell_score += 1.5
            reasons.append(f"스토캐스틱 과매수(K:{stoch_k:.0f})")

        if stoch_k > stoch_d and stoch_k < 30:
            buy_score += 1.0
            reasons.append("스토캐스틱 골든크로스(저점)")
        elif stoch_k < stoch_d and stoch_k > 70:
            sell_score += 1.0
            reasons.append("스토캐스틱 데드크로스(고점)")

        cci = ind.get("cci", 0)
        if cci and cci < -100:
            buy_score += 1.0
            reasons.append(f"CCI 과매도({cci:.0f})")
        elif cci and cci > 100:
            sell_score += 1.0
            reasons.append(f"CCI 과매수({cci:.0f})")

        return RuleBasedAnalyst.build_result(self.agent_id, buy_score, sell_score, reasons, ind)

    async def respond_to_debate(self, own, others, context) -> str:
        return f"{self.agent_id}: {own.signal.value} ({own.confidence:.0%}) — {own.reasoning}"
