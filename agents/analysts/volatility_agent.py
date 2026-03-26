"""
Volatility Agent — 변동성 돌파 (코드 기반, 비용 $0)

볼린저밴드, ATR 기반으로 변동성 상태와 돌파 신호를 분석.
"""

from core.base_agent import BaseAgent, AnalysisResult
from data.indicators import IndicatorEngine
from agents.analysts.rule_based_mixin import RuleBasedAnalyst


class VolatilityAgent(BaseAgent):

    INDICATORS = ["bollinger_upper", "bollinger_lower", "bollinger_mid", "atr", "current_price"]

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        candles_1h = market_data.get("candles", {}).get("1h")
        ind = IndicatorEngine.compute_for_agent(candles_1h, self.INDICATORS) if candles_1h is not None else {}

        buy_score = 0.0
        sell_score = 0.0
        reasons = []

        price = ind.get("current_price", 0)
        bb_upper = ind.get("bollinger_upper", 0)
        bb_lower = ind.get("bollinger_lower", 0)
        bb_mid = ind.get("bollinger_mid", 0)
        atr = ind.get("atr", 0)

        # BB 이탈
        if price and bb_lower and price <= bb_lower:
            buy_score += 2.0
            reasons.append("BB 하단 돌파 — 반등 기대")
        elif price and bb_upper and price >= bb_upper:
            sell_score += 2.0
            reasons.append("BB 상단 돌파 — 과열")

        # BB 스퀴즈
        if bb_upper and bb_lower and bb_mid:
            bb_width = (bb_upper - bb_lower) / (bb_mid + 1e-10)
            if bb_width < 0.02:
                reasons.append(f"BB 극단 스퀴즈(폭 {bb_width:.3f}) — 큰 움직임 임박")
                buy_score += 0.5
                sell_score += 0.5
            elif bb_width > 0.08:
                reasons.append(f"BB 확장(폭 {bb_width:.3f}) — 추세 진행 중")

        # BB 중간 기준 위치
        if price and bb_mid and bb_upper and bb_lower:
            bb_pct = (price - bb_lower) / (bb_upper - bb_lower + 1e-10)
            if bb_pct < 0.2:
                buy_score += 1.0
                reasons.append(f"BB 하단 20% 위치")
            elif bb_pct > 0.8:
                sell_score += 1.0
                reasons.append(f"BB 상단 80% 위치")

        # ATR 변동성
        if atr and price:
            atr_pct = atr / price * 100
            if atr_pct > 3:
                reasons.append(f"높은 변동성(ATR {atr_pct:.1f}%)")
            elif atr_pct < 1:
                reasons.append(f"낮은 변동성(ATR {atr_pct:.1f}%)")

        return RuleBasedAnalyst.build_result(self.agent_id, buy_score, sell_score, reasons, ind)

    async def respond_to_debate(self, own, others, context) -> str:
        return f"{self.agent_id}: {own.signal.value} ({own.confidence:.0%}) — {own.reasoning}"
