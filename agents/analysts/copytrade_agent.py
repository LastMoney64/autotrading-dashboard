"""
CopyTrade Agent — 탑트레이더 추적 (코드 기반, 비용 $0)

가격 모멘텀 + 거래량으로 스마트머니 방향 추정.
(Hyperliquid/OKX 리더보드 API 연동 시 확장 가능)
"""

from core.base_agent import BaseAgent, AnalysisResult
from agents.analysts.rule_based_mixin import RuleBasedAnalyst


class CopyTradeAgent(BaseAgent):

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        buy_score = 0.0
        sell_score = 0.0
        reasons = []
        ind = {}

        candles = market_data.get("candles", {}).get("1h")
        if candles and len(candles) >= 24:
            import pandas as pd
            df = pd.DataFrame(candles)
            closes = df["close"].astype(float)
            volumes = df["volume"].astype(float)

            # 스마트머니 추정: 거래량 가중 가격 추세
            recent_5 = closes.tail(5)
            recent_vol = volumes.tail(5)
            older_5 = closes.tail(10).head(5)
            older_vol = volumes.tail(10).head(5)

            vwap_recent = float((recent_5 * recent_vol).sum() / (recent_vol.sum() + 1e-10))
            vwap_older = float((older_5 * older_vol).sum() / (older_vol.sum() + 1e-10))

            if vwap_older > 0:
                vwap_change = (vwap_recent - vwap_older) / vwap_older * 100
                ind["vwap_trend"] = round(vwap_change, 2)

                if vwap_change > 1.5:
                    buy_score += 1.5
                    reasons.append(f"VWAP 상승 추세(+{vwap_change:.1f}%) — 스마트머니 매수")
                elif vwap_change < -1.5:
                    sell_score += 1.5
                    reasons.append(f"VWAP 하락 추세({vwap_change:.1f}%) — 스마트머니 매도")

            # 거래량+가격 다이버전스
            price_trend = float(closes.iloc[-1] - closes.iloc[-5])
            vol_trend = float(volumes.tail(5).mean() - volumes.tail(10).head(5).mean())

            if price_trend > 0 and vol_trend < 0:
                sell_score += 1.0
                reasons.append("가격↑ 거래량↓ 다이버전스 — 약세 경고")
            elif price_trend < 0 and vol_trend > 0:
                buy_score += 1.0
                reasons.append("가격↓ 거래량↑ 다이버전스 — 매집 가능성")
            elif price_trend > 0 and vol_trend > 0:
                buy_score += 0.5
                reasons.append("가격↑ 거래량↑ — 건강한 상승")

        if not reasons:
            reasons.append("스마트머니 방향 불명확")

        return RuleBasedAnalyst.build_result(self.agent_id, buy_score, sell_score, reasons, ind)

    async def respond_to_debate(self, own, others, context) -> str:
        return f"{self.agent_id}: {own.signal.value} ({own.confidence:.0%}) — {own.reasoning}"
