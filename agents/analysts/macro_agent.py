"""
Macro Agent — 거시/감성 전문 분석 에이전트

뉴스 감성, 공포탐욕지수, 피보나치 레벨 기반으로 거시적 판단.
외부 이벤트가 기술적 분석을 압도할 수 있음을 인지.
"""

import json
from core.base_agent import BaseAgent, AgentConfig, AnalysisResult, Signal
from data.indicators import IndicatorEngine


class MacroAgent(BaseAgent):

    INDICATORS = ["current_price"]

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        candles_4h = market_data.get("candles", {}).get("4h")

        # 피보나치 레벨 계산
        fib_levels = {}
        if candles_4h is not None:
            fib_levels = IndicatorEngine.fibonacci_levels(candles_4h)

        # 감성 데이터
        sentiment = market_data.get("sentiment", {})
        news_headlines = market_data.get("news_headlines", [])

        prompt = f"""현재 {market_data.get('symbol', 'BTC/USDT')} 시장을 거시/감성 관점에서 분석해주세요.

## 피보나치 레벨 (4H 기준)
{json.dumps(fib_levels, indent=2, ensure_ascii=False)}

## 감성 데이터
- 공포탐욕지수: {sentiment.get('fear_greed_index', 'N/A')} ({sentiment.get('fear_greed_label', 'N/A')})
- 뉴스 평균 감성: {sentiment.get('news_sentiment_avg', 'N/A')}

## 최근 뉴스
{json.dumps(news_headlines[:5], indent=2, ensure_ascii=False) if news_headlines else '뉴스 없음'}

## 추가 정보
- 현재가: {market_data.get('current_price')}
- 펀딩비: {market_data.get('funding_rate')}

## 분석 기준
- 공포탐욕 < 25 = 극도 공포 → 역발상 매수 기회
- 공포탐욕 > 75 = 극도 탐욕 → 조정 주의
- 피보나치 61.8% 지지/저항 = 핵심 레벨
- 부정적 뉴스 (규제, 해킹) = 단기 급락 가능, 기술적 분석 무효화
- 긍정적 뉴스 (ETF, 기관 채택) = 추세 강화

## 응답 형식 (반드시 JSON)
{{"signal": "BUY/SELL/HOLD", "confidence": 0.0~1.0, "reasoning": "분석 근거"}}"""

        response = await self.call_llm(prompt)
        indicators = {
            **fib_levels,
            "fear_greed_index": sentiment.get("fear_greed_index"),
            "news_sentiment_avg": sentiment.get("news_sentiment_avg"),
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

거시/감성 관점에서 반론하거나 동의해주세요. 특히 뉴스나 시장 심리가 기술적 분석을 압도할 수 있는 상황인지 판단해주세요. 간결하게 핵심만."""

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
