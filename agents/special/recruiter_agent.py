"""
Recruiter Agent — 신규 에이전트 영입 전문 에이전트

팀에 부족한 관점을 파악하고 새로운 분석 에이전트를 설계한다.
StrategyEvolver에서 호출되어 에이전트 스펙을 생성.
"""

import json
from typing import Optional

from core.base_agent import BaseAgent, AgentConfig, AgentRole, AnalysisResult, Signal


class RecruiterAgent(BaseAgent):

    def __init__(self, config: AgentConfig):
        super().__init__(config)

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        """Recruiter Agent는 직접 시장 분석하지 않음"""
        return AnalysisResult(
            agent_id=self.agent_id,
            signal=Signal.HOLD,
            confidence=0.0,
            reasoning="Recruiter Agent는 신규 에이전트 설계만 수행합니다.",
            key_indicators={},
        )

    async def respond_to_debate(
        self,
        own_analysis: AnalysisResult,
        other_analyses: list[AnalysisResult],
        debate_context: str,
    ) -> str:
        """Recruiter Agent는 토론에 참여하지 않음"""
        return "Recruiter Agent는 신규 에이전트 영입만 담당합니다."

    async def evaluate_need(self, team_summary: str) -> str:
        """신규 에이전트 필요성 평가"""
        return await self.call_llm(team_summary, max_tokens=800)

    async def design_agent(self, requirement: str) -> Optional[AgentConfig]:
        """새 에이전트 스펙 설계"""
        prompt = f"""다음 요구사항에 맞는 새로운 분석 에이전트를 설계해주세요.

요구사항: {requirement}

JSON으로 응답해주세요:
{{
    "agent_id": "새 에이전트 ID (영문 소문자, 언더스코어)",
    "name": "에이전트 이름 (한글)",
    "indicators": ["사용할 지표 목록"],
    "system_prompt": "에이전트의 시스템 프롬프트",
    "parameters": {{
        "key": "value 형태로 초기 파라미터"
    }}
}}"""

        response = await self.call_llm(prompt, max_tokens=1000)

        try:
            # JSON 추출 시도
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                spec = json.loads(response[start:end])
                return AgentConfig(
                    agent_id=spec["agent_id"],
                    name=spec["name"],
                    role=AgentRole.ANALYST,
                    model="claude-haiku-4-5-20251001",
                    weight=0.5,  # 신입은 낮은 가중치로 시작
                    system_prompt=spec.get("system_prompt", ""),
                    parameters=spec.get("parameters", {}),
                )
        except (json.JSONDecodeError, KeyError) as e:
            return None

        return None
