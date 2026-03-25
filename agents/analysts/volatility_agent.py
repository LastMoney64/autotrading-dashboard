"""
Volatility Agent — 변동성 전문 분석 에이전트

볼린저밴드, ATR 기반으로 변동성 돌파/수축, 평균 회귀를 감지.
"""

import json
from core.base_agent import BaseAgent, AgentConfig, AnalysisResult, Signal
from data.indicators import IndicatorEngine


class VolatilityAgent(BaseAgent):

    INDICATORS = ["bollinger_upper", "bollinger_mid", "bollinger_lower", "atr"]

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        candles_15m = market_data.get("candles", {}).get("15m")
        candles_1h = market_data.get("candles", {}).get("1h")

        ind_15m = IndicatorEngine.compute_for_agent(candles_15m, self.INDICATORS) if candles_15m is not None else {}
        ind_1h = IndicatorEngine.compute_for_agent(candles_1h, self.INDICATORS) if candles_1h is not None else {}

        prompt = f"""현재 {market_data.get('symbol', 'BTC/USDT')} 시장을 변동성 관점에서 분석해주세요.

## 15M 지표
{json.dumps(ind_15m, indent=2, ensure_ascii=False)}

## 1H 지표
{json.dumps(ind_1h, indent=2, ensure_ascii=False)}

## 추가 정보
- 현재가: {market_data.get('current_price')}

## 분석 기준
- 가격이 볼린저 하단 터치/이탈 = 과매도 반등 가능 (매수)
- 가격이 볼린저 상단 터치/이탈 = 과매수 조정 가능 (매도)
- 볼린저밴드 폭 축소(스퀴즈) = 큰 움직임 임박
- ATR 급등 = 변동성 확대, 포지션 크기 축소 필요
- 볼린저 밴드 워킹 = 강한 추세 (밴드 터치해도 반전 아님)

## 응답 형식 (반드시 JSON)
{{"signal": "BUY/SELL/HOLD", "confidence": 0.0~1.0, "reasoning": "분석 근거"}}"""

        response = await self.call_llm(prompt)
        return self._parse_response(response, {**ind_15m, **{f"1h_{k}": v for k, v in ind_1h.items()}})

    async def respond_to_debate(self, own_analysis: AnalysisResult, other_analyses: list[AnalysisResult], debate_context: str) -> str:
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

변동성 관점에서 반론하거나 동의해주세요. 간결하게 핵심만."""

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
