"""
RuleBasedAnalyst — 코드 기반 분석 믹스인

AI API 호출 없이 코드로 지표를 계산하고 매매 신호를 생성.
비용: $0 (완전 무료)
"""

from core.base_agent import AnalysisResult, Signal


class RuleBasedAnalyst:
    """코드 기반 분석 — AI 호출 없음, 비용 $0"""

    @staticmethod
    def build_result(
        agent_id: str,
        buy_score: float,
        sell_score: float,
        reasons: list[str],
        indicators: dict,
    ) -> AnalysisResult:
        """점수 기반으로 신호 생성"""
        total = buy_score + sell_score
        if total == 0:
            return AnalysisResult(
                agent_id=agent_id,
                signal=Signal.HOLD,
                confidence=0.3,
                reasoning="신호 없음",
                key_indicators=indicators,
            )

        if buy_score > sell_score:
            signal = Signal.BUY
            confidence = min(0.95, buy_score / (total + 1))
        elif sell_score > buy_score:
            signal = Signal.SELL
            confidence = min(0.95, sell_score / (total + 1))
        else:
            signal = Signal.HOLD
            confidence = 0.4

        reasoning = " | ".join(reasons) if reasons else "규칙 기반 판단"

        return AnalysisResult(
            agent_id=agent_id,
            signal=signal,
            confidence=round(confidence, 2),
            reasoning=reasoning,
            key_indicators=indicators,
        )
