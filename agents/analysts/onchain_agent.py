"""
OnChain Agent — 온체인 데이터 분석 전문 에이전트

거래소 순유입/유출, MVRV, 채굴자 보유량, 스테이블코인 흐름 등
블록체인 온체인 메트릭을 기반으로 시장 방향을 판단한다.
"""

import json
from core.base_agent import BaseAgent, AgentConfig, AnalysisResult, Signal


class OnChainAgent(BaseAgent):

    INDICATORS = [
        "exchange_reserve",          # 거래소 보유량
        "mvrv_ratio",               # Market Value / Realized Value
        "stablecoin_exchange_flow",  # 스테이블코인 거래소 유입
        "miner_reserve",            # 채굴자 보유량
        "active_addresses",         # 활성 주소 수
    ]

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        onchain_data = market_data.get("onchain", {})

        onchain_metrics = {
            "exchange_reserve_btc": onchain_data.get("exchange_reserve", 0),
            "exchange_reserve_change_24h": onchain_data.get("exchange_reserve_change", 0),
            "mvrv_ratio": onchain_data.get("mvrv_ratio", 1.0),
            "mvrv_z_score": onchain_data.get("mvrv_z_score", 0),
            "stablecoin_exchange_inflow_usd": onchain_data.get("stablecoin_inflow", 0),
            "stablecoin_supply_change_7d": onchain_data.get("stablecoin_supply_change", 0),
            "miner_reserve_btc": onchain_data.get("miner_reserve", 0),
            "miner_outflow_24h": onchain_data.get("miner_outflow", 0),
            "active_addresses_24h": onchain_data.get("active_addresses", 0),
            "active_addresses_change_7d": onchain_data.get("active_addresses_change", 0),
            "sopr": onchain_data.get("sopr", 1.0),
            "nupl": onchain_data.get("nupl", 0),
        }

        prompt = f"""현재 {market_data.get('symbol', 'BTC/USDT')} 온체인 데이터를 분석해주세요.

## 온체인 메트릭
{json.dumps(onchain_metrics, indent=2, ensure_ascii=False)}

## 추가 시장 정보
- 현재가: {market_data.get('current_price')}
- 24H 거래량: {market_data.get('volume_24h')}

## 분석 기준
### 거래소 보유량
- 거래소 BTC 감소 = 장기 홀딩 전환 (강세)
- 거래소 BTC 증가 = 매도 준비 (약세)

### MVRV
- MVRV > 3.5 = 과열, 고점 경고
- MVRV < 1.0 = 저평가, 바닥 신호
- MVRV Z-Score > 7 = 버블 영역

### 스테이블코인
- 거래소 USDT/USDC 유입 급증 = 매수 대기자금 (강세)
- 스테이블코인 전체 공급 증가 = 시장 유동성 확대

### 채굴자
- 채굴자 보유량 감소 = 매도 압력
- 채굴자 보유량 유지/증가 = 확신 보유

### SOPR (Spent Output Profit Ratio)
- SOPR > 1 = 이익 실현 중 (매도 압력)
- SOPR < 1 = 손실 매도 중 (항복 신호 → 바닥 가능)

### NUPL (Net Unrealized Profit/Loss)
- NUPL > 0.75 = 탐욕 (위험)
- NUPL < 0 = 항복 (기회)

## 응답 형식 (반드시 JSON)
{{"signal": "BUY/SELL/HOLD", "confidence": 0.0~1.0, "reasoning": "분석 근거"}}"""

        response = await self.call_llm(prompt)
        return self._parse_response(response, onchain_metrics)

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

온체인 데이터 관점에서 반론하거나 동의해주세요.
온체인 데이터는 "실제 자금 흐름"을 보여주므로 기술적 지표보다 후행하지만 더 근본적입니다.
특히 거래소 유입/유출과 스테이블코인 흐름이 가격 방향의 선행 지표임을 강조하세요.
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
