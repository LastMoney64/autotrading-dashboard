"""
Judge Agent — 최종 판결자

Moderator 요약 + 토론 로그 + Memory 유사 상황을 종합하여
최종 매매 결정을 내린다:
- BUY / SELL / HOLD
- 포지션 크기 (계좌 대비 %)
- 진입가, 손절가, 익절가
- 확신도 + 판결 근거
"""

import json
from core.base_agent import BaseAgent, AgentConfig, AnalysisResult, Signal
from debate.debate_record import JudgmentResult


class JudgeAgent(BaseAgent):

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        """Judge는 직접 분석하지 않음"""
        return AnalysisResult(
            agent_id=self.agent_id,
            signal=Signal.HOLD,
            confidence=0.0,
            reasoning="Judge는 토론 후 판결만 수행합니다.",
            key_indicators={},
        )

    async def respond_to_debate(
        self,
        own_analysis: AnalysisResult,
        other_analyses: list[AnalysisResult],
        debate_context: str,
    ) -> str:
        """Judge는 토론에 참여하지 않음 — 판결만"""
        return "Judge는 토론에 참여하지 않고 최종 판결만 수행합니다."

    async def make_judgment(
        self,
        market_data: dict,
        analyses: list[AnalysisResult],
        moderator_summary: str,
        similar_episodes: list[dict],
        account_state: dict | None = None,
    ) -> JudgmentResult:
        """최종 판결 수행"""
        analyses_text = "\n".join(
            f"[{a.agent_id}] {a.signal.value} (확신도 {a.confidence:.2f}): {a.reasoning[:200]}"
            for a in analyses
        )

        # 신호 분포
        buy_agents = [a for a in analyses if a.signal == Signal.BUY]
        sell_agents = [a for a in analyses if a.signal == Signal.SELL]
        hold_agents = [a for a in analyses if a.signal == Signal.HOLD]

        # 가중 평균 확신도 (BUY 방향 양수, SELL 방향 음수)
        weighted_signal = sum(
            a.confidence * (1 if a.signal == Signal.BUY else -1 if a.signal == Signal.SELL else 0)
            for a in analyses
        ) / len(analyses) if analyses else 0

        episodes_text = ""
        if similar_episodes:
            episodes_text = "\n".join(
                f"- {ep.get('date', '?')}: {ep.get('signal', '?')} → {ep.get('result', '?')}"
                for ep in similar_episodes[:5]
            )
        else:
            episodes_text = "유사 과거 데이터 없음"

        account_text = ""
        if account_state:
            account_text = f"""
## 계좌 상태
- 잔고: ${account_state.get('balance', 0):,.2f}
- 열린 포지션: {account_state.get('open_positions', 0)}개
- 오늘 PnL: {account_state.get('daily_pnl', 0):+.2f}%
- 주간 PnL: {account_state.get('weekly_pnl', 0):+.2f}%"""

        prompt = f"""최종 매매 결정을 내려주세요.

## 시장 정보
- 심볼: {market_data.get('symbol', 'BTC/USDT')}
- 현재가: {market_data.get('current_price')}
- 펀딩비: {market_data.get('funding_rate')}

## 에이전트 분석 결과 ({len(analyses)}명)
- BUY: {len(buy_agents)}명 (평균 확신도 {sum(a.confidence for a in buy_agents) / len(buy_agents):.2f})" if buy_agents else "- BUY: 0명"
- SELL: {len(sell_agents)}명 (평균 확신도 {sum(a.confidence for a in sell_agents) / len(sell_agents):.2f})" if sell_agents else "- SELL: 0명"
- HOLD: {len(hold_agents)}명
- 가중 신호 강도: {weighted_signal:+.3f} (양수=매수, 음수=매도)

## 상세 분석
{analyses_text}

## Moderator 요약
{moderator_summary}

## 유사 과거 상황
{episodes_text}
{account_text}

## 판결 원칙
- 에이전트 의견이 강하게 분산되면 HOLD 선호
- 확신도 높은 에이전트의 의견에 더 큰 비중
- Whale, OnChain, CopyTrade 에이전트의 의견은 실제 자금 흐름을 반영하므로 중요
- 포지션 크기는 확신도에 비례: 낮으면 0.5%, 높으면 최대 3%

## 반드시 JSON으로 응답
{{"signal": "BUY/SELL/HOLD", "confidence": 0.0~1.0, "position_size_pct": 0.5~3.0, "entry_price": 현재가, "stop_loss": 가격, "take_profit": 가격, "reasoning": "판결 근거 (한국어)"}}"""

        response = await self.call_llm(prompt, max_tokens=2048)
        return self._parse_judgment(response, market_data)

    def _parse_judgment(self, response: str, market_data: dict) -> JudgmentResult:
        """JSON 응답 파싱"""
        try:
            text = response
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text.strip())

            return JudgmentResult(
                signal=Signal(data.get("signal", "HOLD").upper()),
                confidence=max(0.0, min(1.0, float(data.get("confidence", 0.5)))),
                position_size_pct=max(0.0, min(5.0, float(data.get("position_size_pct", 1.0)))),
                entry_price=data.get("entry_price"),
                stop_loss=data.get("stop_loss"),
                take_profit=data.get("take_profit"),
                reasoning=data.get("reasoning", ""),
            )
        except (json.JSONDecodeError, ValueError, KeyError):
            return JudgmentResult(
                signal=Signal.HOLD,
                confidence=0.3,
                position_size_pct=0.0,
                entry_price=market_data.get("current_price"),
                stop_loss=None,
                take_profit=None,
                reasoning=f"Judge 응답 파싱 실패: {response[:300]}",
            )
