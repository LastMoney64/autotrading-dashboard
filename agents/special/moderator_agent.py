"""
Moderator Agent — 토론 사회자

9명의 분석 에이전트 의견을 종합하여:
- 핵심 합의점 / 충돌점 정리
- 가장 강한 논거 추출
- Judge에게 전달할 요약본 작성
"""

from core.base_agent import BaseAgent, AgentConfig, AnalysisResult, Signal


class ModeratorAgent(BaseAgent):

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        """Moderator는 직접 분석하지 않음 — 요약만 수행"""
        return AnalysisResult(
            agent_id=self.agent_id,
            signal=Signal.HOLD,
            confidence=0.0,
            reasoning="Moderator는 분석을 수행하지 않습니다.",
            key_indicators={},
        )

    async def respond_to_debate(
        self,
        own_analysis: AnalysisResult,
        other_analyses: list[AnalysisResult],
        debate_context: str,
    ) -> str:
        """토론 내용을 보고 요약 작성"""
        return await self.summarize_debate(other_analyses, debate_context)

    async def summarize_debate(
        self,
        analyses: list[AnalysisResult],
        debate_context: str,
    ) -> str:
        """토론 전체 요약"""
        analyses_text = "\n".join(
            f"[{a.agent_id}] {a.signal.value} (확신도 {a.confidence:.2f}): {a.reasoning[:200]}"
            for a in analyses
        )

        # 신호 분포 계산
        buy_count = sum(1 for a in analyses if a.signal == Signal.BUY)
        sell_count = sum(1 for a in analyses if a.signal == Signal.SELL)
        hold_count = sum(1 for a in analyses if a.signal == Signal.HOLD)
        avg_conf = sum(a.confidence for a in analyses) / len(analyses) if analyses else 0

        prompt = f"""토론을 정리해주세요.

## 신호 분포
- BUY: {buy_count}명
- SELL: {sell_count}명
- HOLD: {hold_count}명
- 평균 확신도: {avg_conf:.2f}

## 각 에이전트 분석
{analyses_text}

## 토론 내용
{debate_context}

## 요약 형식
1. **컨센서스**: 전체적인 방향 (매수 우세/매도 우세/혼조)
2. **핵심 합의**: 대부분 동의하는 점
3. **핵심 충돌**: 에이전트 간 의견이 갈리는 쟁점
4. **매수 최강 논거**: 매수를 지지하는 가장 강한 근거 + 어떤 에이전트
5. **매도 최강 논거**: 매도를 지지하는 가장 강한 근거 + 어떤 에이전트
6. **리스크 요소**: 주의해야 할 점
7. **Judge 권고**: 종합적으로 어떤 방향이 합리적인지"""

        return await self.call_llm(prompt, max_tokens=2048)
