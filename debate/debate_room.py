"""
DebateRoom — 토론 진행 엔진

9개 분석 에이전트의 독립 분석 결과를 받아서:
1. 전체 공유
2. 2라운드 토론 (반론/보완)
3. Moderator가 요약
4. Judge가 최종 판결
5. Risk가 거부권 검토
"""

import asyncio
from datetime import datetime
from typing import Optional

from core.base_agent import BaseAgent, AgentRole, AnalysisResult, Signal
from core.agent_registry import AgentRegistry
from core.message_bus import MessageBus, MessageType
from debate.debate_record import DebateRecord, JudgmentResult, RiskReviewResult


class DebateRoom:
    """토론 진행 엔진"""

    def __init__(
        self,
        registry: AgentRegistry,
        message_bus: MessageBus,
        debate_rounds: int = 2,
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

        # ── 1단계: 독립 분석 (병렬) ─────────────────────────
        analyses = await self._run_analyses(market_data)
        for a in analyses:
            record.add_analysis(a)

        if not analyses:
            record.finalize("SKIPPED", {"reason": "No analysis results"})
            return record

        # ── 2단계: 토론 라운드 ───────────────────────────────
        for round_num in range(1, self.debate_rounds + 1):
            opinions = await self._run_debate_round(
                round_num, analyses, record
            )
            record.add_debate_round(round_num, opinions)

        # ── 3단계: Moderator 요약 ────────────────────────────
        summary = await self._run_moderator(analyses, record)
        record.set_moderator_summary(summary)

        # ── 4단계: Memory 검색 (유사 상황) ───────────────────
        similar = await self._query_memory(market_data, analyses)
        record.similar_episodes = similar

        # ── 5단계: Judge 판결 ────────────────────────────────
        judgment = await self._run_judge(market_data, record)
        if judgment:
            record.set_judgment(judgment)
        else:
            record.finalize("SKIPPED", {"reason": "Judge failed"})
            return record

        # ── 6단계: Risk 검토 ─────────────────────────────────
        risk_review = await self._run_risk_review(market_data, record)
        if risk_review:
            record.set_risk_review(risk_review)

        # ── 최종 결정 ────────────────────────────────────────
        if record.risk_review and not record.risk_review.approved:
            record.finalize("VETOED")
        elif record.judgment.signal == Signal.HOLD:
            record.finalize("HOLD")
        else:
            record.finalize("READY_TO_EXECUTE")

        # 메시지 버스에 결과 브로드캐스트
        await self.bus.broadcast(
            MessageType.JUDGMENT,
            sender_id="debate_room",
            payload=record.to_dict(),
        )

        return record

    # ── 1단계: 병렬 분석 ──────────────────────────────────

    async def _run_analyses(self, market_data: dict) -> list[AnalysisResult]:
        """모든 활성 분석 에이전트를 병렬로 실행"""
        analysts = self.registry.get_active_analysts()
        if not analysts:
            return []

        tasks = [
            self._safe_analyze(agent, market_data)
            for agent in analysts
        ]

        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

    async def _safe_analyze(
        self, agent: BaseAgent, market_data: dict
    ) -> Optional[AnalysisResult]:
        """타임아웃 포함 안전한 분석 실행"""
        try:
            return await asyncio.wait_for(
                agent.analyze(market_data),
                timeout=self.analysis_timeout,
            )
        except asyncio.TimeoutError:
            return None
        except Exception:
            return None

    # ── 2단계: 토론 라운드 ────────────────────────────────

    async def _run_debate_round(
        self,
        round_num: int,
        analyses: list[AnalysisResult],
        record: DebateRecord,
    ) -> dict[str, str]:
        """토론 한 라운드 실행"""
        analysts = self.registry.get_active_analysts()
        debate_context = self._build_debate_context(record, round_num)

        tasks = {}
        for agent in analysts:
            own = next((a for a in analyses if a.agent_id == agent.agent_id), None)
            if own is None:
                continue
            others = [a for a in analyses if a.agent_id != agent.agent_id]
            tasks[agent.agent_id] = self._safe_debate(
                agent, own, others, debate_context
            )

        if not tasks:
            return {}

        results = await asyncio.gather(*tasks.values())
        opinions = {}
        for agent_id, result in zip(tasks.keys(), results):
            if result:
                opinions[agent_id] = result

        return opinions

    async def _safe_debate(
        self,
        agent: BaseAgent,
        own: AnalysisResult,
        others: list[AnalysisResult],
        context: str,
    ) -> Optional[str]:
        """타임아웃 포함 안전한 토론 응답"""
        try:
            return await asyncio.wait_for(
                agent.respond_to_debate(own, others, context),
                timeout=self.debate_timeout,
            )
        except (asyncio.TimeoutError, Exception):
            return None

    def _build_debate_context(self, record: DebateRecord, current_round: int) -> str:
        """이전 라운드 토론 내용을 컨텍스트로 구성"""
        if current_round == 1:
            return "첫 번째 토론 라운드입니다. 다른 에이전트의 분석을 보고 의견을 제시해주세요."

        parts = [f"이전 {current_round - 1}라운드 토론 내용:"]
        for dr in record.debate_rounds:
            parts.append(f"\n--- 라운드 {dr.round_number} ---")
            for agent_id, opinion in dr.opinions.items():
                parts.append(f"[{agent_id}] {opinion[:200]}")

        parts.append(f"\n이제 라운드 {current_round}입니다. 핵심 쟁점에 집중해주세요.")
        return "\n".join(parts)

    # ── 3단계: Moderator ──────────────────────────────────

    async def _run_moderator(
        self, analyses: list[AnalysisResult], record: DebateRecord
    ) -> str:
        """Moderator가 토론을 요약"""
        moderator = self.registry.get_special_agent(AgentRole.MODERATOR)
        if not moderator:
            return self._fallback_summary(analyses, record)

        analyses_text = "\n".join(
            f"[{a.agent_id}] {a.signal.value} (확신도 {a.confidence:.2f}): {a.reasoning[:150]}"
            for a in analyses
        )

        debate_text = ""
        for dr in record.debate_rounds:
            debate_text += f"\n--- 라운드 {dr.round_number} ---\n"
            for agent_id, opinion in dr.opinions.items():
                debate_text += f"[{agent_id}] {opinion[:200]}\n"

        prompt = f"""다음 토론을 요약해주세요.

## 개별 분석 결과
{analyses_text}

## 토론 내용
{debate_text}

## 요약 형식
1. 신호 분포 (BUY/SELL/HOLD 각 몇 명)
2. 핵심 합의 사항
3. 핵심 충돌 사항
4. 가장 강한 논거 (매수/매도 각각)
5. Judge에게 전달할 권고사항"""

        try:
            return await asyncio.wait_for(
                moderator.call_llm(prompt),
                timeout=30.0,
            )
        except (asyncio.TimeoutError, Exception):
            return self._fallback_summary(analyses, record)

    def _fallback_summary(
        self, analyses: list[AnalysisResult], record: DebateRecord
    ) -> str:
        """Moderator 실패 시 규칙 기반 요약"""
        consensus = record.signal_consensus
        avg_conf = record.avg_confidence
        return (
            f"신호 분포: BUY {consensus['BUY']} / SELL {consensus['SELL']} / HOLD {consensus['HOLD']}\n"
            f"평균 확신도: {avg_conf:.1%}\n"
            f"Moderator 요약 불가 — 규칙 기반 요약 제공"
        )

    # ── 4단계: Memory 검색 ────────────────────────────────

    async def _query_memory(
        self, market_data: dict, analyses: list[AnalysisResult]
    ) -> list[dict]:
        """Memory Agent에게 유사 상황 검색 요청"""
        memory_agent = self.registry.get_special_agent(AgentRole.MEMORY)
        if not memory_agent:
            return []

        # TODO: Memory 시스템 구현 후 연동 (PHASE 6)
        return []

    # ── 5단계: Judge ──────────────────────────────────────

    async def _run_judge(
        self, market_data: dict, record: DebateRecord
    ) -> Optional[JudgmentResult]:
        """Judge Agent가 최종 판결"""
        judge = self.registry.get_special_agent(AgentRole.JUDGE)
        if not judge:
            return self._fallback_judgment(record)

        analyses_text = "\n".join(
            f"[{a.agent_id}] {a.signal.value} (확신도 {a.confidence:.2f}): {a.reasoning[:150]}"
            for a in record.analyses
        )

        prompt = f"""최종 매매 결정을 내려주세요.

## 시장 정보
- 심볼: {record.symbol}
- 현재가: {market_data.get('current_price')}

## 에이전트 분석 (총 {len(record.analyses)}명)
{analyses_text}

## Moderator 요약
{record.moderator_summary}

## 유사 과거 상황
{record.similar_episodes if record.similar_episodes else '데이터 없음'}

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
        """Judge 응답 파싱"""
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
        """Judge 실패 시 다수결 기반 판결"""
        consensus = record.signal_consensus
        max_signal = max(consensus, key=consensus.get)

        return JudgmentResult(
            signal=Signal(max_signal),
            confidence=record.avg_confidence * 0.7,
            position_size_pct=0.5 if max_signal != "HOLD" else 0.0,
            entry_price=None,
            stop_loss=None,
            take_profit=None,
            reasoning=f"Fallback: 다수결 {max_signal} (Judge 응답 없음)",
        )

    # ── 6단계: Risk 검토 ──────────────────────────────────

    async def _run_risk_review(
        self, market_data: dict, record: DebateRecord
    ) -> Optional[RiskReviewResult]:
        """Risk Agent가 최종 검토"""
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

## VETO 조건 확인
1. 평균 확신도 < 0.5?
2. 에이전트 간 의견 심하게 분산?
3. 포지션 크기 과다?

## 반드시 JSON으로 응답
{{"approved": true/false, "veto_reason": "거부 사유 (승인 시 null)", "risk_score": 0.0~1.0, "adjustments": {{"position_size_pct": 조정값}} 또는 null}}"""

        try:
            response = await asyncio.wait_for(
                risk_agent.call_llm(prompt),
                timeout=20.0,
            )
            return self._parse_risk_review(response)
        except (asyncio.TimeoutError, Exception):
            return self._fallback_risk_review(record)

    def _parse_risk_review(self, response: str) -> RiskReviewResult:
        """Risk 응답 파싱"""
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
        """Risk Agent 실패 시 규칙 기반 검토"""
        # 평균 확신도 0.5 미만이면 자동 거부
        if record.avg_confidence < 0.5:
            return RiskReviewResult(
                approved=False,
                veto_reason=f"평균 확신도 {record.avg_confidence:.1%} < 50%",
                risk_score=0.8,
            )
        return RiskReviewResult(approved=True, risk_score=0.3)
