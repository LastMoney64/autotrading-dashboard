"""
Trend Agent — 추세 추종 전문 분석 에이전트

EMA (20/50/200), MACD, ADX 기반으로 추세 방향과 강도를 분석.
강한 추세장에서 진입 타이밍을 포착한다.
"""

import json
from core.base_agent import BaseAgent, AgentConfig, AnalysisResult, Signal
from data.indicators import IndicatorEngine


class TrendAgent(BaseAgent):

    INDICATORS = ["ema_20", "ema_50", "ema_200", "macd", "macd_signal", "macd_histogram", "adx"]

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        candles_1h = market_data.get("candles", {}).get("1h")
        candles_4h = market_data.get("candles", {}).get("4h")

        indicators_1h = IndicatorEngine.compute_for_agent(candles_1h, self.INDICATORS) if candles_1h is not None else {}
        indicators_4h = IndicatorEngine.compute_for_agent(candles_4h, self.INDICATORS) if candles_4h is not None else {}

        prompt = f"""현재 {market_data.get('symbol', 'BTC/USDT')} 시장 상황을 추세 관점에서 분석해주세요.

## 1H 지표
{json.dumps(indicators_1h, indent=2, ensure_ascii=False)}

## 4H 지표
{json.dumps(indicators_4h, indent=2, ensure_ascii=False)}

## 추가 정보
- 현재가: {market_data.get('current_price')}
- 펀딩비: {market_data.get('funding_rate')}

## 분석 기준
- EMA 20 > 50 > 200 = 강한 상승 추세
- EMA 20 < 50 < 200 = 강한 하락 추세
- MACD 골든/데드크로스 = 추세 전환 신호
- ADX > 25 = 추세 존재, ADX < 20 = 추세 없음 (횡보)

## 응답 형식 (반드시 JSON)
{{"signal": "BUY/SELL/HOLD", "confidence": 0.0~1.0, "reasoning": "분석 근거"}}"""

        response = await self.call_llm(prompt)
        return self._parse_response(response, {**indicators_1h, **{f"4h_{k}": v for k, v in indicators_4h.items()}})

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

추세 관점에서 다른 에이전트의 의견에 대해 반론하거나 동의해주세요. 간결하게 핵심만."""

        return await self.call_llm(prompt)

    def _parse_response(self, response: str, indicators: dict) -> AnalysisResult:
        try:
            # JSON 블록 추출
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
