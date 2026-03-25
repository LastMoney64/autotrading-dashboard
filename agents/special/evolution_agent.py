"""
Evolution Agent — 전략 진화 전문 에이전트

주간 성과를 분석하고 에이전트 팀 최적화를 제안한다.
StrategyEvolver에서 호출되어 LLM 분석을 수행.
"""

from core.base_agent import BaseAgent, AgentConfig, AnalysisResult, Signal


class EvolutionAgent(BaseAgent):

    def __init__(self, config: AgentConfig):
        super().__init__(config)

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        """Evolution Agent는 직접 시장 분석하지 않음"""
        return AnalysisResult(
            agent_id=self.agent_id,
            signal=Signal.HOLD,
            confidence=0.0,
            reasoning="Evolution Agent는 주간 성과 분석만 수행합니다.",
            key_indicators={},
        )

    async def respond_to_debate(
        self,
        own_analysis: AnalysisResult,
        other_analyses: list[AnalysisResult],
        debate_context: str,
    ) -> str:
        """Evolution Agent는 토론에 참여하지 않음"""
        return "Evolution Agent는 주간 전략 진화만 담당합니다."

    async def analyze_performance(self, performance_data: str) -> str:
        """성과 데이터를 분석하고 개선안 제시"""
        return await self.call_llm(performance_data, max_tokens=1500)

    async def suggest_parameter_changes(self, agent_id: str, stats: dict) -> str:
        """특정 에이전트의 파라미터 변경 제안"""
        prompt = f"""에이전트 '{agent_id}' 전략 파라미터 최적화 요청

현재 성과:
- 승률: {stats.get('win_rate', 0):.0%}
- 평균 확신도: {stats.get('avg_confidence', 0):.2f}
- 총 거래: {stats.get('total', 0)}회

이 에이전트가 사용하는 지표의 파라미터를 어떻게 조정하면 성과를 개선할 수 있을까요?
구체적인 파라미터 변경 값을 JSON으로 제안해주세요."""

        return await self.call_llm(prompt, max_tokens=800)
