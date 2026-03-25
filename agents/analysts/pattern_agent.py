"""
Pattern Agent — 차트 패턴 인식 전문 분석 에이전트

지지/저항, 캔들 패턴, 클래식 차트 패턴을 식별.
패턴 확인은 거래량 동반이 필요.
"""

import json
from core.base_agent import BaseAgent, AgentConfig, AnalysisResult, Signal
from data.indicators import IndicatorEngine


class PatternAgent(BaseAgent):

    INDICATORS = ["support_levels", "resistance_levels", "ema_20", "ema_50", "volume_24h"]

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        candles_1h = market_data.get("candles", {}).get("1h")
        candles_4h = market_data.get("candles", {}).get("4h")

        ind_1h = IndicatorEngine.compute_for_agent(candles_1h, self.INDICATORS) if candles_1h is not None else {}
        sr_1h = IndicatorEngine.support_resistance(candles_1h) if candles_1h is not None else {}
        sr_4h = IndicatorEngine.support_resistance(candles_4h) if candles_4h is not None else {}

        # 최근 캔들 패턴 데이터 (마지막 10개)
        recent_candles = []
        if candles_1h is not None and len(candles_1h) >= 10:
            for _, row in candles_1h.tail(10).iterrows():
                recent_candles.append({
                    "O": round(row["open"], 2),
                    "H": round(row["high"], 2),
                    "L": round(row["low"], 2),
                    "C": round(row["close"], 2),
                    "V": round(row["volume"], 0),
                })

        prompt = f"""현재 {market_data.get('symbol', 'BTC/USDT')} 시장을 차트 패턴 관점에서 분석해주세요.

## 지지/저항 (1H)
{json.dumps(sr_1h, indent=2, ensure_ascii=False)}

## 지지/저항 (4H)
{json.dumps(sr_4h, indent=2, ensure_ascii=False)}

## 최근 10개 1H 캔들 (시간순)
{json.dumps(recent_candles, indent=2, ensure_ascii=False)}

## 추가 지표
{json.dumps(ind_1h, indent=2, ensure_ascii=False)}

## 추가 정보
- 현재가: {market_data.get('current_price')}

## 분석 기준
- 헤드앤숄더 / 역헤드앤숄더 = 추세 반전
- 이중바닥(W) / 이중천장(M) = 반전
- 삼각수렴 (대칭/상승/하락) = 돌파 방향으로 진행
- 쐐기형 (상승/하락) = 반대 방향 돌파
- 가격이 주요 지지선 접근 = 매수 기회
- 가격이 주요 저항선 접근 = 매도 기회
- 패턴 확인에는 거래량 동반이 필수

## 응답 형식 (반드시 JSON)
{{"signal": "BUY/SELL/HOLD", "confidence": 0.0~1.0, "reasoning": "분석 근거"}}"""

        response = await self.call_llm(prompt)
        indicators = {**ind_1h, **sr_1h, "sr_4h": sr_4h}
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

차트 패턴/지지저항 관점에서 반론하거나 동의해주세요. 간결하게 핵심만."""

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
