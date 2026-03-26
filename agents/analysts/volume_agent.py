"""
Volume Agent — 거래량/자금 흐름 (코드 기반, 비용 $0)

OBV, VWAP, OI, 펀딩비 기반으로 자금 흐름을 분석.
"""

from core.base_agent import BaseAgent, AnalysisResult
from data.indicators import IndicatorEngine
from agents.analysts.rule_based_mixin import RuleBasedAnalyst


class VolumeAgent(BaseAgent):

    INDICATORS = ["obv", "vwap", "current_price"]

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        candles_1h = market_data.get("candles", {}).get("1h")
        ind = IndicatorEngine.compute_for_agent(candles_1h, self.INDICATORS) if candles_1h is not None else {}

        buy_score = 0.0
        sell_score = 0.0
        reasons = []

        # 펀딩비
        funding = market_data.get("funding_rate")
        if funding is not None:
            try:
                fr = float(funding)
                if fr < -0.01:
                    buy_score += 2.0
                    reasons.append(f"펀딩비 극단 음수({fr:.4f}) — 숏 과열, 롱 유리")
                elif fr < -0.005:
                    buy_score += 1.0
                    reasons.append(f"펀딩비 음수({fr:.4f}) — 숏 우세")
                elif fr > 0.01:
                    sell_score += 2.0
                    reasons.append(f"펀딩비 극단 양수({fr:.4f}) — 롱 과열")
                elif fr > 0.005:
                    sell_score += 1.0
                    reasons.append(f"펀딩비 양수({fr:.4f}) — 롱 우세")
            except (ValueError, TypeError):
                pass

        # 거래량 트렌드
        if candles_1h and len(candles_1h) >= 20:
            import pandas as pd
            df = pd.DataFrame(candles_1h)
            vol_recent = float(df["volume"].tail(5).mean())
            vol_avg = float(df["volume"].tail(20).mean())
            if vol_avg > 0:
                ratio = vol_recent / vol_avg
                if ratio > 2.0:
                    reasons.append(f"거래량 급등({ratio:.1f}x)")
                    price_change = float(df["close"].iloc[-1] - df["close"].iloc[-5])
                    if price_change > 0:
                        buy_score += 1.5
                    else:
                        sell_score += 1.5
                elif ratio < 0.5:
                    reasons.append(f"거래량 감소({ratio:.1f}x)")

        # OI (미결제약정)
        oi = market_data.get("open_interest")
        if oi:
            try:
                reasons.append(f"미결제약정: {float(oi):,.0f}")
            except (ValueError, TypeError):
                pass

        return RuleBasedAnalyst.build_result(self.agent_id, buy_score, sell_score, reasons, ind)

    async def respond_to_debate(self, own, others, context) -> str:
        return f"{self.agent_id}: {own.signal.value} ({own.confidence:.0%}) — {own.reasoning}"
