"""
CopyTrade Agent — 탑트레이더 추적 전문 분석 에이전트

Hyperliquid 리더보드, 거래소 탑트레이더 포지션,
유명 트레이더 지갑을 추적하여 스마트머니 방향을 감지한다.
"""

import json
from core.base_agent import BaseAgent, AgentConfig, AnalysisResult, Signal


class CopyTradeAgent(BaseAgent):

    INDICATORS = [
        "top_traders_long_ratio",    # 탑트레이더 롱 비율
        "top_traders_positions",     # 탑트레이더 포지션 상세
        "leaderboard_consensus",     # 리더보드 컨센서스
        "smart_money_flow",          # 스마트머니 흐름
    ]

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        copytrade_data = market_data.get("copytrade", {})

        trader_metrics = {
            "top10_long_ratio": copytrade_data.get("top10_long_ratio", 0.5),
            "top10_avg_leverage": copytrade_data.get("top10_avg_leverage", 1.0),
            "top10_avg_entry_price": copytrade_data.get("top10_avg_entry_price", 0),
            "top10_position_changes": copytrade_data.get("position_changes", []),
            "top_pnl_traders_direction": copytrade_data.get("top_pnl_direction", "mixed"),
            "recent_opens": copytrade_data.get("recent_opens", 0),
            "recent_closes": copytrade_data.get("recent_closes", 0),
            "hyperliquid_leaderboard": copytrade_data.get("hyperliquid_top", []),
            "binance_top_traders_ratio": copytrade_data.get("binance_ratio", {}),
        }

        prompt = f"""현재 {market_data.get('symbol', 'BTC/USDT')} 탑트레이더 동향을 분석해주세요.

## 탑트레이더 데이터
{json.dumps(trader_metrics, indent=2, ensure_ascii=False)}

## 추가 시장 정보
- 현재가: {market_data.get('current_price')}
- 펀딩비: {market_data.get('funding_rate')}

## 분석 기준
- Top 10 수익률 트레이더 중 70%+ 같은 방향 = 강한 컨센서스
- 탑트레이더 평균 레버리지 상승 = 확신도 높은 방향
- 탑트레이더 동시 포지션 청산 = 단기 조정 가능성
- 스마트머니와 일반 투자자 방향 반대 = 스마트머니 따라가기
- Hyperliquid 리더보드 포지션 추적
- 탑트레이더 진입가 대비 현재가 위치 = 추세 유지/이탈 판단

## 응답 형식 (반드시 JSON)
{{"signal": "BUY/SELL/HOLD", "confidence": 0.0~1.0, "reasoning": "분석 근거"}}"""

        response = await self.call_llm(prompt)
        return self._parse_response(response, trader_metrics)

    async def respond_to_debate(
        self,
        own_analysis: AnalysisResult,
        other_analyses: list[AnalysisResult],
        debate_context: str,
    ) -> str:
        others_summary = "\n".join(
            f"- {a.agent_id}: {a.signal.value} (확신도 {a.confidence:.2f}) — {a.reasoning[:100]}"
            for a in other_analyses
        )
        prompt = f"""당신의 분석: {own_analysis.signal.value} (확신도 {own_analysis.confidence:.2f})
근거: {own_analysis.reasoning}

다른 에이전트들의 의견:
{others_summary}

토론 맥락:
{debate_context}

탑트레이더/스마트머니 관점에서 반론하거나 동의해주세요.
"돈을 가장 많이 번 사람들이 어떻게 베팅하고 있는가"가 핵심 논거입니다.
기술적 지표가 아무리 좋아도 스마트머니가 반대로 갈 때의 위험성을 지적하세요.
간결하게 핵심만."""

        return await self.call_llm(prompt)

    def _parse_response(self, response: str, indicators: dict) -> AnalysisResult:
        try:
            text = response
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text.strip())
            signal = Signal(data.get("signal", "HOLD").upper())
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
            reasoning = data.get("reasoning", "")
        except (json.JSONDecodeError, ValueError, KeyError):
            signal = Signal.HOLD
            confidence = 0.3
            reasoning = response[:500]

        return AnalysisResult(
            agent_id=self.agent_id,
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            key_indicators=indicators,
        )
