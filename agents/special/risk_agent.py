"""
Risk Agent — 리스크 관리자 (VETO 파워)

Judge의 모든 결정을 최종 검토.
위험 조건 충족 시 거부권(VETO)을 행사한다.

VETO 조건:
1. 계좌 드로다운 > 10%
2. 동방향 포지션 3개 이상
3. 평균 확신도 < 0.5
4. 뉴스 이벤트 30분 이내
5. 포지션 크기 > 계좌의 3%
"""

import json
from core.base_agent import BaseAgent, AgentConfig, AnalysisResult, Signal
from debate.debate_record import JudgmentResult, RiskReviewResult


class RiskAgent(BaseAgent):

    # 하드코딩된 리스크 한도 (config에서 오버라이드 가능)
    MAX_DRAWDOWN_PCT = 10.0
    MAX_SAME_DIRECTION = 3
    MIN_AVG_CONFIDENCE = 0.5
    MAX_POSITION_SIZE_PCT = 3.0
    NEWS_BLACKOUT_MINUTES = 30

    def get_system_prompt(self) -> str:
        return self.config.system_prompt

    async def analyze(self, market_data: dict) -> AnalysisResult:
        """Risk Agent는 직접 분석하지 않음"""
        return AnalysisResult(
            agent_id=self.agent_id,
            signal=Signal.HOLD,
            confidence=0.0,
            reasoning="Risk Agent는 리스크 검토만 수행합니다.",
            key_indicators={},
        )

    async def respond_to_debate(
        self,
        own_analysis: AnalysisResult,
        other_analyses: list[AnalysisResult],
        debate_context: str,
    ) -> str:
        """Risk Agent는 토론에 참여하지 않음"""
        return "Risk Agent는 Judge 판결 이후 검토만 수행합니다."

    async def review(
        self,
        judgment: JudgmentResult,
        analyses: list[AnalysisResult],
        account_state: dict | None = None,
        open_positions: list[dict] | None = None,
        recent_news: list[dict] | None = None,
    ) -> RiskReviewResult:
        """Judge 판결을 리스크 관점에서 검토"""

        # ── 규칙 기반 자동 VETO 체크 ──────────────────────
        veto_reason = self._check_hard_veto(
            judgment, analyses, account_state, open_positions
        )
        if veto_reason:
            return RiskReviewResult(
                approved=False,
                veto_reason=veto_reason,
                risk_score=1.0,
            )

        # ── HOLD일 경우 무조건 승인 ──────────────────────
        if judgment.signal == Signal.HOLD:
            return RiskReviewResult(approved=True, risk_score=0.0)

        # ── AI 기반 소프트 리스크 검토 ────────────────────
        return await self._ai_review(judgment, analyses, account_state, open_positions)

    def _check_hard_veto(
        self,
        judgment: JudgmentResult,
        analyses: list[AnalysisResult],
        account_state: dict | None,
        open_positions: list[dict] | None,
    ) -> str | None:
        """하드 VETO 조건 (무조건 거부)"""

        # 1. 평균 확신도 < 0.5
        if analyses:
            avg_conf = sum(a.confidence for a in analyses) / len(analyses)
            if avg_conf < self.MIN_AVG_CONFIDENCE:
                return f"평균 확신도 {avg_conf:.1%} < {self.MIN_AVG_CONFIDENCE:.0%} 기준 미달"

        # 2. 포지션 크기 초과
        if judgment.position_size_pct > self.MAX_POSITION_SIZE_PCT:
            return f"포지션 크기 {judgment.position_size_pct:.1f}% > 최대 {self.MAX_POSITION_SIZE_PCT:.0f}%"

        # 3. 계좌 드로다운 체크
        if account_state:
            drawdown = account_state.get("drawdown_pct", 0)
            if drawdown > self.MAX_DRAWDOWN_PCT:
                return f"계좌 드로다운 {drawdown:.1f}% > 최대 {self.MAX_DRAWDOWN_PCT:.0f}%"

        # 4. 동방향 포지션 과다
        if open_positions:
            direction = "long" if judgment.signal == Signal.BUY else "short"
            same_direction = sum(
                1 for p in open_positions if p.get("direction") == direction
            )
            if same_direction >= self.MAX_SAME_DIRECTION:
                return f"동방향({direction}) 포지션 {same_direction}개 >= 최대 {self.MAX_SAME_DIRECTION}개"

        return None

    async def _ai_review(
        self,
        judgment: JudgmentResult,
        analyses: list[AnalysisResult],
        account_state: dict | None,
        open_positions: list[dict] | None,
    ) -> RiskReviewResult:
        """AI 기반 상세 리스크 검토"""

        signal_dist = {"BUY": 0, "SELL": 0, "HOLD": 0}
        for a in analyses:
            signal_dist[a.signal.value] += 1

        prompt = f"""리스크 검토를 수행해주세요.

## Judge 판결
- 신호: {judgment.signal.value}
- 확신도: {judgment.confidence:.2f}
- 포지션 크기: {judgment.position_size_pct:.1f}%
- 손절가: {judgment.stop_loss}
- 익절가: {judgment.take_profit}
- 근거: {judgment.reasoning[:300]}

## 에이전트 신호 분포
- BUY: {signal_dist['BUY']}명
- SELL: {signal_dist['SELL']}명
- HOLD: {signal_dist['HOLD']}명
- 반대 의견 비율: {signal_dist.get('SELL' if judgment.signal == Signal.BUY else 'BUY', 0) / len(analyses) * 100:.0f}%

## 소프트 리스크 체크
1. 에이전트 의견 분산도가 높은가?
2. 손익비(R:R)가 충분한가? (최소 2:1 권장)
3. 포지션 크기가 확신도에 적절한가?
4. 감정적 판단이 아닌 근거 기반인가?

## 반드시 JSON으로 응답
{{"approved": true/false, "veto_reason": "거부 사유 (승인 시 null)", "risk_score": 0.0~1.0, "adjustments": {{"position_size_pct": 조정값}} 또는 null}}"""

        try:
            response = await self.call_llm(prompt, max_tokens=1024)
            return self._parse_response(response)
        except Exception:
            # AI 실패 시 보수적으로 승인 (하드 VETO 통과했으므로)
            return RiskReviewResult(approved=True, risk_score=0.5)

    def _parse_response(self, response: str) -> RiskReviewResult:
        try:
            text = response
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text.strip())

            return RiskReviewResult(
                approved=bool(data.get("approved", True)),
                veto_reason=data.get("veto_reason"),
                adjustments=data.get("adjustments"),
                risk_score=max(0.0, min(1.0, float(data.get("risk_score", 0.5)))),
            )
        except (json.JSONDecodeError, ValueError, KeyError):
            return RiskReviewResult(approved=True, risk_score=0.5)
