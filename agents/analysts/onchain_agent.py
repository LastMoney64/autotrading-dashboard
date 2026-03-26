"""
OnChain Agent — 온체인 데이터 분석 (코드 기반, 비용 $0)

거래소 유입/유출, 가격 구조로 온체인 메트릭 추정.
(CryptoQuant/Glassnode API 연동 시 확장 가능)
"""

from core.base_agent import BaseAgent, AnalysisResult
from agents.analysts.rule_based_mixin import RuleBasedAnalyst


class OnChainAgent(BaseAgent):

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        buy_score = 0.0
        sell_score = 0.0
        reasons = []
        ind = {}

        candles = market_data.get("candles", {}).get("1h")
        if candles and len(candles) >= 48:
            import pandas as pd
            df = pd.DataFrame(candles)
            closes = df["close"].astype(float)
            volumes = df["volume"].astype(float)

            current = float(closes.iloc[-1])

            # MVRV 프록시: 현재가 vs 평균 취득가 추정 (30일 평균)
            avg_price_30 = float(closes.tail(30).mean())
            if avg_price_30 > 0:
                mvrv_proxy = current / avg_price_30
                ind["mvrv_proxy"] = round(mvrv_proxy, 3)

                if mvrv_proxy > 1.15:
                    sell_score += 1.5
                    reasons.append(f"MVRV 과열({mvrv_proxy:.2f}) — 차익실현 구간")
                elif mvrv_proxy < 0.85:
                    buy_score += 1.5
                    reasons.append(f"MVRV 저평가({mvrv_proxy:.2f}) — 매집 구간")

            # SOPR 프록시: 단기 수익/손실 비율
            if len(closes) >= 10:
                price_5d_ago = float(closes.iloc[-5])
                sopr = current / price_5d_ago if price_5d_ago > 0 else 1.0
                ind["sopr_proxy"] = round(sopr, 4)

                if sopr < 0.97:
                    buy_score += 1.0
                    reasons.append(f"SOPR<1({sopr:.3f}) — 손실 매도 중, 바닥 근접")
                elif sopr > 1.05:
                    sell_score += 0.5
                    reasons.append(f"SOPR>1({sopr:.3f}) — 이익 실현 구간")

            # 거래소 유입/유출 프록시: 급격한 거래량 변화
            vol_recent = float(volumes.tail(6).mean())
            vol_older = float(volumes.tail(24).head(18).mean())
            if vol_older > 0:
                vol_change = vol_recent / vol_older
                ind["vol_flow_ratio"] = round(vol_change, 2)

                if vol_change > 2.5:
                    reasons.append(f"거래량 급변({vol_change:.1f}x) — 대규모 자금 이동")
                    # 가격 방향으로 판단
                    if current > float(closes.iloc[-6]):
                        buy_score += 1.0
                    else:
                        sell_score += 1.0

        if not reasons:
            reasons.append("온체인 메트릭 중립")

        return RuleBasedAnalyst.build_result(self.agent_id, buy_score, sell_score, reasons, ind)

    async def respond_to_debate(self, own, others, context) -> str:
        return f"{self.agent_id}: {own.signal.value} ({own.confidence:.0%}) — {own.reasoning}"
