"""
DebateRoom — 비용 최적화 토론 엔진

비용 절감 전략:
1. 관련 에이전트만 선택적 참여 (9개 → 3~4개)
2. 토론 라운드 제거 (0라운드 = 분석만)
3. Moderator 규칙 기반 (AI 호출 없음)
4. HOLD일 때 Risk 호출 안 함

API 호출 최소화: 분석 3~4회 + Judge 1회 + Risk 1회 = 5~6회/사이클
"""

import asyncio
from datetime import datetime
from typing import Optional

from core.base_agent import BaseAgent, AgentRole, AnalysisResult, Signal
from core.agent_registry import AgentRegistry
from core.message_bus import MessageBus, MessageType
from debate.debate_record import DebateRecord, JudgmentResult, RiskReviewResult


# 방향별 관련 에이전트 매핑
DIRECTION_AGENTS = {
    "BUY": ["trend", "momentum", "volume", "whale"],
    "SELL": ["trend", "momentum", "volatility", "onchain"],
    "NEUTRAL": ["trend", "momentum", "volatility"],
}


class DebateRoom:
    """비용 최적화 토론 엔진"""

    def __init__(
        self,
        registry: AgentRegistry,
        message_bus: MessageBus,
        debate_rounds: int = 0,  # 토론 라운드 제거
        analysis_timeout: float = 30.0,
        debate_timeout: float = 20.0,
    ):
        self.registry = registry
        self.bus = message_bus
        self.debate_rounds = debate_rounds
        self.analysis_timeout = analysis_timeout
        self.debate_timeout = debate_timeout
        self._cycle_count = 0

    async def run_cycle(self, market_data: dict) -> DebateRecord:
        """하나의 완전한 의사결정 사이클 실행"""
        self._cycle_count += 1
        symbol = market_data.get("symbol", "BTC/USDT")

        record = DebateRecord(
            cycle_id=f"{self._cycle_count:04d}",
            symbol=symbol,
        )

        # ── 1단계: 선택적 분석 (관련 에이전트만) ───────────
        direction = market_data.get("pre_filter", {}).get("direction_hint", "NEUTRAL")
        analyses = await self._run_selective_analyses(market_data, direction)
        for a in analyses:
            record.add_analysis(a)

        if not analyses:
            record.finalize("SKIPPED", {"reason": "No analysis results"})
            return record

        # ── 2단계: 규칙 기반 Moderator (AI 호출 없음) ──────
        summary = self._rule_based_summary(analyses, record)
        record.set_moderator_summary(summary)

        # ── 3단계: Judge 판결 ──────────────────────────────
        judgment = await self._run_judge(market_data, record)
        if judgment:
            record.set_judgment(judgment)
        else:
            record.finalize("SKIPPED", {"reason": "Judge failed"})
            return record

        # ── 4단계: Risk 검토 (BUY/SELL일 때만) ─────────────
        if judgment.signal != Signal.HOLD:
            risk_review = await self._run_risk_review(market_data, record)
            if risk_review:
                record.set_risk_review(risk_review)
        else:
            # HOLD면 Risk 호출 안 함 (비용 절감)
            record.set_risk_review(RiskReviewResult(approved=True, risk_score=0.0))

        # ── 최종 결정 ──────────────────────────────────────
        if record.risk_review and not record.risk_review.approved:
            record.finalize("VETOED")
        elif record.judgment.signal == Signal.HOLD:
            record.finalize("HOLD")
        else:
            record.finalize("EXECUTED")

        await self.bus.broadcast(
            MessageType.JUDGMENT,
            sender_id="debate_room",
            payload=record.to_dict(),
        )

        return record

    # ── 선택적 분석 ──────────────────────────────────────

    async def _run_selective_analyses(
        self, market_data: dict, direction: str
    ) -> list[AnalysisResult]:
        """방향에 관련된 에이전트만 선택적 실행 (3~4개)"""
        all_analysts = self.registry.get_active_analysts()
        if not all_analysts:
            return []

        # 방향에 맞는 에이전트 선택
        target_ids = DIRECTION_AGENTS.get(direction, DIRECTION_AGENTS["NEUTRAL"])
        selected = [
            a for a in all_analysts
            if any(tid in a.agent_id.lower() for tid in target_ids)
        ]

        # 선택된 게 없으면 상위 4개
        if not selected:
            selected = all_analysts[:4]

        # 최대 4개로 제한
        selected = selected[:4]

        tasks = [
            self._safe_analyze(agent, market_data)
            for agent in selected
        ]

        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

    async def _safe_analyze(
        self, agent: BaseAgent, market_data: dict
    ) -> Optional[AnalysisResult]:
        try:
            return await asyncio.wait_for(
                agent.analyze(market_data),
                timeout=self.analysis_timeout,
            )
        except (asyncio.TimeoutError, Exception):
            return None

    # ── 규칙 기반 Moderator (무료) ───────────────────────

    def _rule_based_summary(
        self, analyses: list[AnalysisResult], record: DebateRecord
    ) -> str:
        """AI 호출 없이 규칙으로 요약 (비용 $0)"""
        consensus = record.signal_consensus
        avg_conf = record.avg_confidence

        buy_count = consensus.get("BUY", 0)
        sell_count = consensus.get("SELL", 0)
        hold_count = consensus.get("HOLD", 0)
        total = buy_count + sell_count + hold_count

        parts = [
            f"# 토론 요약 보고서",
            f"## 1. 신호 분포 - **HOLD**: {hold_count}명 - **BUY**: {buy_count}명 - **SELL**: {sell_count}명",
            f"**확신도**: 평균 {avg_conf:.1%}",
        ]

        # 컨센서스 강도 판단
        if total > 0:
            max_signal = max(consensus, key=consensus.get)
            max_pct = consensus[max_signal] / total

            if max_pct >= 0.75:
                parts.append(f"## 2. 합의: 강한 {max_signal} 컨센서스 ({max_pct:.0%})")
            elif max_pct >= 0.5:
                parts.append(f"## 2. 합의: 약한 {max_signal} 컨센서스 ({max_pct:.0%})")
            else:
                parts.append("## 2. 합의: 의견 분산 — 신중한 판단 필요")

        # 각 에이전트 핵심 의견
        parts.append("## 3. 에이전트 의견")
        for a in analyses:
            parts.append(f"- **{a.agent_id}**: {a.signal.value} ({a.confidence:.0%}) — {a.reasoning[:100]}")

        return "\n".join(parts)

    # ── Judge ────────────────────────────────────────────

    async def _run_judge(
        self, market_data: dict, record: DebateRecord
    ) -> Optional[JudgmentResult]:
        judge = self.registry.get_special_agent(AgentRole.JUDGE)
        if not judge:
            return self._fallback_judgment(record)

        pre_filter = market_data.get("pre_filter", {})
        direction_hint = pre_filter.get("direction_hint", "NEUTRAL")

        analyses_text = "\n".join(
            f"[{a.agent_id}] {a.signal.value} (확신도 {a.confidence:.2f}): {a.reasoning[:200]}"
            for a in record.analyses
        )

        prompt = f"""최종 매매 결정을 내려주세요.

## 시장 정보
- 심볼: {record.symbol}
- 현재가: {market_data.get('current_price')}
- 사전 필터 신호: {pre_filter.get('reason', 'N/A')}
- 방향 힌트: {direction_hint}

## 에이전트 분석 (총 {len(record.analyses)}명)
{analyses_text}

## Moderator 요약
{record.moderator_summary}

## 매매 성향
- 적극적으로 매매하되, 손실 관리에 집중
- 방향이 명확하면 BUY 또는 SELL을 적극 추천
- HOLD는 정말 판단이 어려울 때만 사용
- 확신도 0.6 이상이면 진입 추천

## 반드시 JSON으로 응답
{{"signal": "BUY/SELL/HOLD", "confidence": 0.0~1.0, "position_size_pct": 0.5~3.0, "entry_price": 가격, "stop_loss": 가격, "take_profit": 가격, "reasoning": "판결 근거"}}"""

        try:
            response = await asyncio.wait_for(
                judge.call_llm(prompt),
                timeout=30.0,
            )
            return self._parse_judgment(response, market_data)
        except (asyncio.TimeoutError, Exception):
            return self._fallback_judgment(record)

    def _parse_judgment(self, response: str, market_data: dict) -> JudgmentResult:
        import json
        try:
            text = response
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text.strip())

            return JudgmentResult(
                signal=Signal(data.get("signal", "HOLD").upper()),
                confidence=max(0.0, min(1.0, float(data.get("confidence", 0.5)))),
                position_size_pct=max(0.0, min(5.0, float(data.get("position_size_pct", 1.0)))),
                entry_price=data.get("entry_price"),
                stop_loss=data.get("stop_loss"),
                take_profit=data.get("take_profit"),
                reasoning=data.get("reasoning", ""),
            )
        except (json.JSONDecodeError, ValueError, KeyError):
            return JudgmentResult(
                signal=Signal.HOLD,
                confidence=0.3,
                position_size_pct=0.0,
                entry_price=market_data.get("current_price"),
                stop_loss=None,
                take_profit=None,
                reasoning=f"Judge 응답 파싱 실패: {response[:200]}",
            )

    def _fallback_judgment(self, record: DebateRecord) -> JudgmentResult:
        consensus = record.signal_consensus
        max_signal = max(consensus, key=consensus.get)

        return JudgmentResult(
            signal=Signal(max_signal),
            confidence=record.avg_confidence * 0.8,
            position_size_pct=1.0 if max_signal != "HOLD" else 0.0,
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            reasoning=f"Fallback: 다수결 {max_signal} (Judge 응답 없음)",
        )

    # ── Risk 검토 ────────────────────────────────────────

    async def _run_risk_review(
        self, market_data: dict, record: DebateRecord
    ) -> Optional[RiskReviewResult]:
        risk_agent = self.registry.get_special_agent(AgentRole.RISK)
        if not risk_agent:
            return self._fallback_risk_review(record)

        judgment = record.judgment

        prompt = f"""Judge의 결정을 리스크 관점에서 검토해주세요.

## Judge 판결
- 신호: {judgment.signal.value}
- 확신도: {judgment.confidence:.2f}
- 포지션 크기: {judgment.position_size_pct:.1f}%
- 손절가: {judgment.stop_loss}
- 익절가: {judgment.take_profit}
- 근거: {judgment.reasoning[:200]}

## 에이전트 컨센서스
- 신호 분포: {record.signal_consensus}
- 평균 확신도: {record.avg_confidence:.2f}

## 리스크 성향: 적극적
- 확신도 40% 이상이면 승인 (기존 50%)
- 방향이 명확하면 포지션 크기 유지
- 과도한 리스크만 거부 (레버리지 고려)

## 반드시 JSON으로 응답
{{"approved": true/false, "veto_reason": "거부 사유 (승인 시 null)", "risk_score": 0.0~1.0, "adjustments": null}}"""

        try:
            response = await asyncio.wait_for(
                risk_agent.call_llm(prompt),
                timeout=20.0,
            )
            return self._parse_risk_review(response)
        except (asyncio.TimeoutError, Exception):
            return self._fallback_risk_review(record)

    def _parse_risk_review(self, response: str) -> RiskReviewResult:
        import json
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

    def _fallback_risk_review(self, record: DebateRecord) -> RiskReviewResult:
        # 적극적: 확신도 40% 이상이면 승인
        if record.avg_confidence < 0.4:
            return RiskReviewResult(
                approved=False,
                veto_reason=f"평균 확신도 {record.avg_confidence:.1%} < 40%",
                risk_score=0.8,
            )
        return RiskReviewResult(approved=True, risk_score=0.3)
