"""
Volume Agent — 거래량/자금 흐름 전문 분석 에이전트

OBV, VWAP, 미결제약정(OI), 펀딩비 기반으로 자금 흐름을 분석.
"""

import json
from core.base_agent import BaseAgent, AgentConfig, AnalysisResult, Signal
from data.indicators import IndicatorEngine


class VolumeAgent(BaseAgent):

    INDICATORS = ["obv", "vwap", "volume_24h"]

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        candles_1h = market_data.get("candles", {}).get("1h")
        candles_4h = market_data.get("candles", {}).get("4h")

        ind_1h = IndicatorEngine.compute_for_agent(candles_1h, self.INDICATORS) if candles_1h is not None else {}
        ind_4h = IndicatorEngine.compute_for_agent(candles_4h, self.INDICATORS) if candles_4h is not None else {}

        prompt = f"""현재 {market_data.get('symbol', 'BTC/USDT')} 시장을 거래량/자금흐름 관점에서 분석해주세요.

## 1H 지표
{json.dumps(ind_1h, indent=2, ensure_ascii=False)}

## 4H 지표
{json.dumps(ind_4h, indent=2, ensure_ascii=False)}

## 추가 정보
- 현재가: {market_data.get('current_price')}
- 펀딩비: {market_data.get('funding_rate')}%
- 미결제약정(OI): {market_data.get('open_interest')}

## 분석 기준
- OBV 상승 + 가격 횡보 = 매집 (매수 신호)
- OBV 하락 + 가격 횡보 = 분배 (매도 신호)
- 가격 > VWAP = 매수 우위, 가격 < VWAP = 매도 우위
- 펀딩비 극단적 양수 (+0.05%↑) = 롱 과열 → 숏 청산 주의
- 펀딩비 극단적 음수 (-0.05%↓) = 숏 과열 → 숏 스퀴즈 가능
- OI 급증 + 가격 횡보 = 큰 움직임 임박

## 응답 형식 (반드시 JSON)
{{"signal": "BUY/SELL/HOLD", "confidence": 0.0~1.0, "reasoning": "분석 근거"}}"""

        response = await self.call_llm(prompt)
        indicators = {
            **ind_1h,
            "funding_rate": market_data.get("funding_rate"),
            "open_interest": market_data.get("open_interest"),
        }
        return self._parse_response(response, indicators)

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

거래량/자금흐름 관점에서 반론하거나 동의해주세요. 간결하게 핵심만."""

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
