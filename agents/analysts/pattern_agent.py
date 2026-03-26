"""
Pattern Agent — 차트 패턴 인식 (코드 기반, 비용 $0)

지지/저항, 캔들 패턴, 가격 구조 분석.
"""

from core.base_agent import BaseAgent, AnalysisResult
from data.indicators import IndicatorEngine
from agents.analysts.rule_based_mixin import RuleBasedAnalyst


class PatternAgent(BaseAgent):

    INDICATORS = ["current_price", "ema_20", "ema_50"]

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        candles_1h = market_data.get("candles", {}).get("1h")
        ind = IndicatorEngine.compute_for_agent(candles_1h, self.INDICATORS) if candles_1h is not None else {}

        buy_score = 0.0
        sell_score = 0.0
        reasons = []

        if candles_1h and len(candles_1h) >= 20:
            import pandas as pd
            df = pd.DataFrame(candles_1h)

            highs = df["high"].astype(float)
            lows = df["low"].astype(float)
            closes = df["close"].astype(float)
            opens = df["open"].astype(float)
            current = float(closes.iloc[-1])

            # 지지/저항선 (최근 20봉)
            recent_high = float(highs.tail(20).max())
            recent_low = float(lows.tail(20).min())
            resistance = recent_high
            support = recent_low

            # 지지선 근처
            if current <= support * 1.005:
                buy_score += 2.0
                reasons.append(f"지지선({support:.0f}) 터치 — 반등 기대")
            elif current >= resistance * 0.995:
                # 저항 돌파 시도
                if current > resistance:
                    buy_score += 1.5
                    reasons.append(f"저항선({resistance:.0f}) 돌파 — 상승 추세")
                else:
                    sell_score += 1.5
                    reasons.append(f"저항선({resistance:.0f}) 근처 — 반락 가능")

            ind["support"] = round(support, 2)
            ind["resistance"] = round(resistance, 2)

            # 캔들 패턴 — 최근 3봉
            if len(df) >= 3:
                c1 = float(closes.iloc[-1])
                o1 = float(opens.iloc[-1])
                h1 = float(highs.iloc[-1])
                l1 = float(lows.iloc[-1])
                c2 = float(closes.iloc[-2])
                o2 = float(opens.iloc[-2])

                body1 = abs(c1 - o1)
                wick_upper1 = h1 - max(c1, o1)
                wick_lower1 = min(c1, o1) - l1

                # 해머 (하락 후 긴 아래꼬리)
                if body1 > 0 and wick_lower1 > body1 * 2 and wick_upper1 < body1 * 0.5:
                    buy_score += 1.5
                    reasons.append("해머 캔들 — 반등 신호")

                # 슈팅스타 (상승 후 긴 위꼬리)
                if body1 > 0 and wick_upper1 > body1 * 2 and wick_lower1 < body1 * 0.5:
                    sell_score += 1.5
                    reasons.append("슈팅스타 캔들 — 하락 신호")

                # 강한 양봉/음봉
                if c1 > o1 and body1 > (h1 - l1) * 0.7:
                    buy_score += 0.5
                    reasons.append("강한 양봉")
                elif o1 > c1 and body1 > (h1 - l1) * 0.7:
                    sell_score += 0.5
                    reasons.append("강한 음봉")

                # 장악형 (Engulfing)
                if c1 > o1 and o2 > c2 and c1 > o2 and o1 < c2:
                    buy_score += 1.5
                    reasons.append("상승 장악형 — 강한 매수")
                elif o1 > c1 and c2 > o2 and o1 > c2 and c1 < o2:
                    sell_score += 1.5
                    reasons.append("하락 장악형 — 강한 매도")

        return RuleBasedAnalyst.build_result(self.agent_id, buy_score, sell_score, reasons, ind)

    async def respond_to_debate(self, own, others, context) -> str:
        return f"{self.agent_id}: {own.signal.value} ({own.confidence:.0%}) — {own.reasoning}"
