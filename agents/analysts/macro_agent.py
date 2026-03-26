"""
Macro Agent — 거시/감성 (코드 기반, 비용 $0)

피보나치 되돌림, 가격 위치, 변동 추세 기반 분석.
(뉴스/감성 데이터는 외부 API 연동 시 확장 가능)
"""

from core.base_agent import BaseAgent, AnalysisResult
from data.indicators import IndicatorEngine
from agents.analysts.rule_based_mixin import RuleBasedAnalyst


class MacroAgent(BaseAgent):

    INDICATORS = ["current_price", "ema_200"]

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        candles_1h = market_data.get("candles", {}).get("1h")
        ind = IndicatorEngine.compute_for_agent(candles_1h, self.INDICATORS) if candles_1h is not None else {}

        buy_score = 0.0
        sell_score = 0.0
        reasons = []

        if candles_1h and len(candles_1h) >= 50:
            import pandas as pd
            df = pd.DataFrame(candles_1h)
            prices = df["close"].astype(float)

            # 피보나치 되돌림 (최근 50봉 고저 기준)
            high = float(prices.max())
            low = float(prices.min())
            current = float(prices.iloc[-1])

            if high > low:
                fib_range = high - low
                fib_382 = high - fib_range * 0.382
                fib_500 = high - fib_range * 0.500
                fib_618 = high - fib_range * 0.618

                if current <= fib_618 * 1.01:
                    buy_score += 2.0
                    reasons.append(f"피보나치 61.8% 지지({fib_618:.0f}) 근처")
                elif current <= fib_500 * 1.01:
                    buy_score += 1.5
                    reasons.append(f"피보나치 50% 지지({fib_500:.0f}) 근처")
                elif current <= fib_382 * 1.01:
                    buy_score += 1.0
                    reasons.append(f"피보나치 38.2% 지지({fib_382:.0f}) 근처")
                elif current >= high * 0.99:
                    sell_score += 1.5
                    reasons.append(f"최고점 근처({high:.0f}) — 저항")

                ind["fib_382"] = round(fib_382, 2)
                ind["fib_500"] = round(fib_500, 2)
                ind["fib_618"] = round(fib_618, 2)

            # 200 EMA 기준
            ema200 = ind.get("ema_200", 0)
            if ema200 and current:
                if current > ema200 * 1.05:
                    buy_score += 0.5
                    reasons.append("EMA200 위 5%+ — 강세")
                elif current < ema200 * 0.95:
                    sell_score += 0.5
                    reasons.append("EMA200 아래 5%+ — 약세")

            # 24시간 변동
            if len(prices) >= 24:
                change_24h = (current - float(prices.iloc[-24])) / float(prices.iloc[-24]) * 100
                if change_24h > 5:
                    sell_score += 0.5
                    reasons.append(f"24H +{change_24h:.1f}% — 과열 주의")
                elif change_24h < -5:
                    buy_score += 0.5
                    reasons.append(f"24H {change_24h:.1f}% — 과매도")

        return RuleBasedAnalyst.build_result(self.agent_id, buy_score, sell_score, reasons, ind)

    async def respond_to_debate(self, own, others, context) -> str:
        return f"{self.agent_id}: {own.signal.value} ({own.confidence:.0%}) — {own.reasoning}"
