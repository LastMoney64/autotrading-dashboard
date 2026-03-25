"""
Memory Agent — 기억/패턴 검색 전문 에이전트

과거 거래 에피소드에서 현재 상황과 유사한 사례를 찾아
토론에 참고 자료로 제공한다.
"""

import json
from core.base_agent import BaseAgent, AgentConfig, AnalysisResult, Signal
from memory.episode_memory import EpisodeMemory
from memory.pattern_memory import PatternMemory


class MemoryAgent(BaseAgent):

    def __init__(self, config: AgentConfig):
        super().__init__(config)
        self.episode_memory: EpisodeMemory | None = None
        self.pattern_memory: PatternMemory | None = None

    def set_memory(self, episode: EpisodeMemory, pattern: PatternMemory):
        """메모리 시스템 주입"""
        self.episode_memory = episode
        self.pattern_memory = pattern

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        """Memory Agent는 직접 시장 분석하지 않음"""
        return AnalysisResult(
            agent_id=self.agent_id,
            signal=Signal.HOLD,
            confidence=0.0,
            reasoning="Memory Agent는 유사 과거 상황 검색만 수행합니다.",
            key_indicators={},
        )

    async def respond_to_debate(
        self,
        own_analysis: AnalysisResult,
        other_analyses: list[AnalysisResult],
        debate_context: str,
    ) -> str:
        """Memory Agent는 토론에 직접 참여하지 않음"""
        return "Memory Agent는 Judge 요청 시 유사 과거 상황을 제공합니다."

    async def search_similar(
        self,
        judge_signal: str,
        avg_confidence: float,
        market_conditions: dict | None = None,
    ) -> list[dict]:
        """유사 과거 에피소드 검색"""
        results = []

        # 1. 에피소드 메모리에서 유사 상황 검색
        if self.episode_memory:
            similar = self.episode_memory.find_similar(
                judge_signal=judge_signal,
                avg_confidence=avg_confidence,
            )
            for ep in similar:
                results.append({
                    "type": "episode",
                    "cycle_id": ep.get("cycle_id"),
                    "signal": ep.get("judge_signal"),
                    "confidence": ep.get("avg_confidence"),
                    "result_pnl": ep.get("pnl_pct"),
                    "date": ep.get("started_at", "")[:10],
                })

        # 2. 패턴 메모리에서 관련 패턴 검색
        if self.pattern_memory and market_conditions:
            patterns = self.pattern_memory.get_relevant_patterns(market_conditions)
            for p in patterns[:3]:
                results.append({
                    "type": "pattern",
                    "description": p.get("description"),
                    "success_rate": p.get("success_rate"),
                    "sample_count": p.get("sample_count"),
                })

        return results

    async def summarize_history(
        self,
        similar_episodes: list[dict],
    ) -> str:
        """유사 과거 상황을 자연어로 요약"""
        if not similar_episodes:
            return "유사한 과거 사례가 아직 충분하지 않습니다."

        episodes = [e for e in similar_episodes if e["type"] == "episode"]
        patterns = [e for e in similar_episodes if e["type"] == "pattern"]

        prompt_parts = ["과거 유사 상황을 요약해주세요.\n"]

        if episodes:
            prompt_parts.append("## 유사 거래 에피소드")
            for ep in episodes:
                result = f"+{ep['result_pnl']:.1f}%" if ep.get('result_pnl') and ep['result_pnl'] > 0 else f"{ep.get('result_pnl', '미정')}"
                prompt_parts.append(
                    f"- {ep.get('date', '?')}: {ep.get('signal', '?')} "
                    f"(확신도 {ep.get('confidence', 0):.1%}) → 결과: {result}"
                )

            wins = sum(1 for e in episodes if e.get("result_pnl") and e["result_pnl"] > 0)
            total = sum(1 for e in episodes if e.get("result_pnl") is not None)
            if total > 0:
                prompt_parts.append(f"\n유사 상황 승률: {wins}/{total} ({wins/total:.0%})")

        if patterns:
            prompt_parts.append("\n## 관련 패턴")
            for p in patterns:
                prompt_parts.append(
                    f"- {p['description']} (성공률 {p.get('success_rate', 0):.0%}, "
                    f"샘플 {p.get('sample_count', 0)}개)"
                )

        prompt_parts.append("\n이 정보를 바탕으로 Judge에게 참고할 핵심 포인트를 정리해주세요.")

        prompt = "\n".join(prompt_parts)
        return await self.call_llm(prompt, max_tokens=512)
