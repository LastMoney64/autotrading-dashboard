"""
Whale Agent — 고래 추적 (코드 기반, 비용 $0)

펀딩비, OI, 거래량 급변으로 고래 활동을 추정.
(실제 Whale Alert API 연동 시 확장 가능)
"""

from core.base_agent import BaseAgent, AnalysisResult
from agents.analysts.rule_based_mixin import RuleBasedAnalyst


class WhaleAgent(BaseAgent):

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        buy_score = 0.0
        sell_score = 0.0
        reasons = []
        ind = {}

        # 펀딩비로 대형 포지션 방향 추정
        funding = market_data.get("funding_rate")
        if funding is not None:
            try:
                fr = float(funding)
                ind["funding_rate"] = fr
                if fr < -0.02:
                    buy_score += 2.0
                    reasons.append(f"극단 음수 펀딩비({fr:.4f}) — 숏 청산 펌프 가능")
                elif fr > 0.02:
                    sell_score += 2.0
                    reasons.append(f"극단 양수 펀딩비({fr:.4f}) — 롱 청산 덤프 가능")
            except (ValueError, TypeError):
                pass

        # 거래량 스파이크로 고래 활동 추정
        candles = market_data.get("candles", {}).get("1h")
        if candles and len(candles) >= 20:
            import pandas as pd
            df = pd.DataFrame(candles)
            vol_last = float(df["volume"].iloc[-1])
            vol_avg = float(df["volume"].tail(20).mean())
            if vol_avg > 0:
                ratio = vol_last / vol_avg
                ind["volume_ratio"] = round(ratio, 2)
                if ratio > 3.0:
                    reasons.append(f"이상 거래량({ratio:.1f}x) — 고래 활동 추정")
                    price_change = float(df["close"].iloc[-1] - df["close"].iloc[-2])
                    if price_change > 0:
                        buy_score += 1.5
                    else:
                        sell_score += 1.5

        # 호가창 불균형
        orderbook = market_data.get("orderbook", {})
        if orderbook:
            bid_vol = sum(b[1] for b in orderbook.get("bids", [])[:10]) if orderbook.get("bids") else 0
            ask_vol = sum(a[1] for a in orderbook.get("asks", [])[:10]) if orderbook.get("asks") else 0
            if bid_vol and ask_vol:
                imbalance = bid_vol / (bid_vol + ask_vol)
                ind["bid_ratio"] = round(imbalance, 3)
                if imbalance > 0.65:
                    buy_score += 1.0
                    reasons.append(f"호가 매수벽({imbalance:.0%})")
                elif imbalance < 0.35:
                    sell_score += 1.0
                    reasons.append(f"호가 매도벽({1-imbalance:.0%})")

        if not reasons:
            reasons.append("고래 활동 감지 안됨")

        return RuleBasedAnalyst.build_result(self.agent_id, buy_score, sell_score, reasons, ind)

    async def respond_to_debate(self, own, others, context) -> str:
        return f"{self.agent_id}: {own.signal.value} ({own.confidence:.0%}) — {own.reasoning}"
