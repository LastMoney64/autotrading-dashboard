"""
Whale Agent — 고래 지갑 추적 전문 분석 에이전트

대량 이체, 거래소 입출금, 고래 지갑 동향을 추적하여
큰손들의 매매 방향을 감지한다.
"""

import json
from core.base_agent import BaseAgent, AgentConfig, AnalysisResult, Signal


class WhaleAgent(BaseAgent):

    INDICATORS = [
        "whale_transactions",       # 대량 이체 (1000 BTC 이상)
        "exchange_inflow",          # 거래소 입금량
        "exchange_outflow",         # 거래소 출금량
        "exchange_netflow",         # 순유입 (입금-출금)
        "top_holders_change",       # 상위 지갑 보유량 변화
    ]

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        whale_data = market_data.get("whale", {})
        onchain = market_data.get("onchain", {})

        # 고래 데이터 통합
        whale_metrics = {
            "whale_transactions": whale_data.get("large_transactions", []),
            "exchange_inflow_btc": whale_data.get("exchange_inflow", 0),
            "exchange_outflow_btc": whale_data.get("exchange_outflow", 0),
            "exchange_netflow_btc": whale_data.get("exchange_netflow", 0),
            "top_100_balance_change_24h": whale_data.get("top_holders_change", 0),
            "whale_buy_count": whale_data.get("buy_count", 0),
            "whale_sell_count": whale_data.get("sell_count", 0),
        }

        prompt = f"""현재 {market_data.get('symbol', 'BTC/USDT')} 고래 동향을 분석해주세요.

## 고래 지갑 데이터
{json.dumps(whale_metrics, indent=2, ensure_ascii=False)}

## 추가 시장 정보
- 현재가: {market_data.get('current_price')}
- 24H 거래량: {market_data.get('volume_24h')}

## 분석 기준
- 거래소 대량 입금 = 매도 준비 (약세 신호)
- 거래소 대량 출금 = 장기 보유 의도 (강세 신호)
- 고래 지갑 순매수 증가 = 축적 단계 (강세)
- 고래 지갑 순매도 증가 = 분배 단계 (약세)
- 대량 이체 직후 가격 변동 주의
- 거래소 순유입 > 5000 BTC = 강한 매도 압력
- 거래소 순유출 > 5000 BTC = 강한 매수 신호

## 응답 형식 (반드시 JSON)
{{"signal": "BUY/SELL/HOLD", "confidence": 0.0~1.0, "reasoning": "분석 근거"}}"""

        response = await self.call_llm(prompt)
        return self._parse_response(response, whale_metrics)

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

고래 동향 관점에서 반론하거나 동의해주세요.
특히 기술적 지표와 고래 움직임이 엇갈릴 때 왜 고래를 따라야 하는지 또는
왜 이번에는 기술적 신호가 맞는지 근거를 대세요. 간결하게 핵심만."""

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
