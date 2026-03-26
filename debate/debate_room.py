"""
DebateRoom — 하이브리드 토론 엔진 (거의 무료)

비용 구조:
- 분석 에이전트 9개: 코드 기반 ($0)
- Moderator: 코드 기반 ($0)
- Judge: 코드 기반 ($0) + 강한 신호일 때만 AI 확인
- Risk: 코드 기반 ($0)

AI 호출: 하루 0~5회 → $0.05~0.15/일
"""

import asyncio
from typing import Optional

from core.base_agent import BaseAgent, AgentRole, AnalysisResult, Signal
from core.agent_registry import AgentRegistry
from core.message_bus import MessageBus, MessageType
from debate.debate_record import DebateRecord, JudgmentResult, RiskReviewResult


class DebateRoom:
    """하이브리드 토론 엔진 — 코드 기반 + AI 보조"""

    def __init__(
        self,
        registry: AgentRegistry,
        message_bus: MessageBus,
        debate_rounds: int = 0,
        analysis_timeout: float = 30.0,
        **kwargs,
    ):
        self.registry = registry
        self.bus = message_bus
        self.analysis_timeout = analysis_timeout
        self._cycle_count = 0

    async def run_cycle(self, market_data: dict) -> DebateRecord:
        self._cycle_count += 1
        symbol = market_data.get("symbol", "BTC/USDT")
        record = DebateRecord(cycle_id=f"{self._cycle_count:04d}", symbol=symbol)

        # ── 1단계: 전체 분석 에이전트 실행 (코드 기반, $0) ───
        analyses = await self._run_all_analyses(market_data)
        for a in analyses:
            record.add_analysis(a)

        if not analyses:
            record.finalize("SKIPPED", {"reason": "No analysis results"})
            return record

        # ── 2단계: 코드 기반 Moderator ($0) ──────────────
        summary = self._rule_based_summary(analyses, record)
        record.set_moderator_summary(summary)

        # ── 3단계: Judge 판결 (코드 기반 + 선택적 AI) ─────
        judgment = await self._hybrid_judge(market_data, record)
        record.set_judgment(judgment)

        # ── 4단계: Risk 검토 (코드 기반, $0) ──────────────
        if judgment.signal != Signal.HOLD:
            risk_review = self._code_based_risk(market_data, record)
        else:
            risk_review = RiskReviewResult(approved=True, risk_score=0.0)
        record.set_risk_review(risk_review)

        # ── 최종 결정 ────────────────────────────────────
        if not risk_review.approved:
            record.finalize("VETOED")
        elif judgment.signal == Signal.HOLD:
            record.finalize("HOLD")
        else:
            record.finalize("EXECUTED")

        await self.bus.broadcast(
            MessageType.JUDGMENT, sender_id="debate_room",
            payload=record.to_dict(),
        )
        return record

    # ══════════════════════════════════════════════════════
    # 1단계: 전체 분석 (코드 기반, $0)
    # ══════════════════════════════════════════════════════

    async def _run_all_analyses(self, market_data: dict) -> list[AnalysisResult]:
        analysts = self.registry.get_active_analysts()
        if not analysts:
            return []
        tasks = [self._safe_analyze(a, market_data) for a in analysts]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

    async def _safe_analyze(self, agent: BaseAgent, market_data: dict) -> Optional[AnalysisResult]:
        try:
            return await asyncio.wait_for(agent.analyze(market_data), timeout=self.analysis_timeout)
        except Exception:
            return None

    # ══════════════════════════════════════════════════════
    # 2단계: Moderator (코드 기반, $0)
    # ══════════════════════════════════════════════════════

    def _rule_based_summary(self, analyses: list[AnalysisResult], record: DebateRecord) -> str:
        consensus = record.signal_consensus
        avg_conf = record.avg_confidence

        buy_count = consensus.get("BUY", 0)
        sell_count = consensus.get("SELL", 0)
        hold_count = consensus.get("HOLD", 0)
        total = buy_count + sell_count + hold_count

        parts = [f"# 토론 요약"]

        # 에이전트별 의견
        for a in analyses:
            parts.append(f"- **{a.agent_id}**: {a.signal.value} ({a.confidence:.0%}) — {a.reasoning[:80]}")

        parts.append(f"\n**신호 분포**: BUY {buy_count} / SELL {sell_count} / HOLD {hold_count}")
        parts.append(f"**평균 확신도**: {avg_conf:.0%}")

        if total > 0:
            max_signal = max(consensus, key=consensus.get)
            max_pct = consensus[max_signal] / total
            if max_pct >= 0.7:
                parts.append(f"**합의**: 강한 {max_signal} ({max_pct:.0%})")
            elif max_pct >= 0.5:
                parts.append(f"**합의**: 약한 {max_signal} ({max_pct:.0%})")
            else:
                parts.append("**합의**: 분산 — 신중 필요")

        return "\n".join(parts)

    # ══════════════════════════════════════════════════════
    # 3단계: Judge (코드 기반 + 강한 신호일 때만 AI)
    # ══════════════════════════════════════════════════════

    async def _hybrid_judge(self, market_data: dict, record: DebateRecord) -> JudgmentResult:
        """코드로 판결하고, 매우 강한 신호일 때만 AI에게 확인"""
        # 확신도 30% 이하인 에이전트 제외 (데이터 없는 에이전트)
        valid_analyses = [a for a in record.analyses if a.confidence > 0.3]
        if not valid_analyses:
            valid_analyses = record.analyses  # 전부 낮으면 그냥 전체 사용

        buy_count = sum(1 for a in valid_analyses if a.signal == Signal.BUY)
        sell_count = sum(1 for a in valid_analyses if a.signal == Signal.SELL)
        hold_count = sum(1 for a in valid_analyses if a.signal == Signal.HOLD)
        total = buy_count + sell_count + hold_count
        avg_conf = sum(a.confidence for a in valid_analyses) / len(valid_analyses) if valid_analyses else 0

        if total == 0:
            return JudgmentResult(
                signal=Signal.HOLD, confidence=0.2, position_size_pct=0.0,
                reasoning="분석 결과 없음"
            )

        # 가중 평균 확신도 계산 (유효 에이전트만)
        buy_conf = sum(a.confidence for a in valid_analyses if a.signal == Signal.BUY)
        sell_conf = sum(a.confidence for a in valid_analyses if a.signal == Signal.SELL)

        # ── 코드 기반 판결 (적극적) ──────────────────────────
        if buy_count >= total * 0.5 and buy_conf >= sell_conf:
            signal = Signal.BUY
            confidence = min(0.9, avg_conf * 1.3 + (buy_count / max(total, 1)) * 0.2)
        elif sell_count >= total * 0.5 and sell_conf >= buy_conf:
            signal = Signal.SELL
            confidence = min(0.9, avg_conf * 1.3 + (sell_count / max(total, 1)) * 0.2)
        elif buy_count > sell_count:
            signal = Signal.BUY
            confidence = min(0.75, avg_conf * 1.1)
        elif sell_count > buy_count:
            signal = Signal.SELL
            confidence = min(0.75, avg_conf * 1.1)
        else:
            signal = Signal.HOLD
            confidence = avg_conf * 0.5

        # 포지션 사이징
        if signal != Signal.HOLD:
            if confidence >= 0.7:
                position_pct = 2.0
            elif confidence >= 0.5:
                position_pct = 1.5
            else:
                position_pct = 1.0
        else:
            position_pct = 0.0

        # 판결 근거 생성
        top_reasons = sorted(record.analyses, key=lambda a: a.confidence, reverse=True)[:3]
        reasoning = " → ".join(
            f"{a.agent_id}({a.signal.value} {a.confidence:.0%})"
            for a in top_reasons
        )

        judgment = JudgmentResult(
            signal=signal,
            confidence=round(confidence, 2),
            position_size_pct=position_pct,
            entry_price=market_data.get("current_price"),
            reasoning=f"코드 판결: {reasoning}",
        )

        # ── 강한 신호일 때만 AI Judge 확인 (선택적) ───────
        if signal != Signal.HOLD and confidence >= 0.6:
            ai_judgment = await self._ai_judge_confirm(market_data, record, judgment)
            if ai_judgment:
                return ai_judgment

        return judgment

    async def _ai_judge_confirm(
        self, market_data: dict, record: DebateRecord, code_judgment: JudgmentResult
    ) -> Optional[JudgmentResult]:
        """AI Judge에게 코드 판결 확인 요청 (강한 신호에서만)"""
        judge = self.registry.get_special_agent(AgentRole.JUDGE)
        if not judge:
            return None

        analyses_text = "\n".join(
            f"- {a.agent_id}: {a.signal.value} ({a.confidence:.0%}) — {a.reasoning[:100]}"
            for a in record.analyses
        )

        prompt = f"""코드 기반 자동 판결을 확인해주세요.

## 코드 판결
- 신호: {code_judgment.signal.value}
- 확신도: {code_judgment.confidence:.0%}
- 근거: {code_judgment.reasoning}

## 에이전트 분석
{analyses_text}

## 시장 정보
- 심볼: {record.symbol}
- 현재가: {market_data.get('current_price')}

## 지시사항
- 코드 판결이 맞으면 그대로 확인
- 적극적으로 매매 — BUY/SELL을 선호
- HOLD는 정말 불확실할 때만

## JSON 응답
{{"signal": "BUY/SELL/HOLD", "confidence": 0.0~1.0, "position_size_pct": 0.5~3.0, "reasoning": "확인/수정 근거"}}"""

        try:
            response = await asyncio.wait_for(judge.call_llm(prompt), timeout=30.0)
            return self._parse_ai_judgment(response, market_data)
        except Exception:
            return None  # AI 실패 시 코드 판결 유지

    def _parse_ai_judgment(self, response: str, market_data: dict) -> Optional[JudgmentResult]:
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
                entry_price=market_data.get("current_price"),
                reasoning=f"AI 확인: {data.get('reasoning', '')}",
            )
        except (json.JSONDecodeError, ValueError, KeyError):
            return None

    # ══════════════════════════════════════════════════════
    # 4단계: Risk (코드 기반, $0)
    # ══════════════════════════════════════════════════════

    def _code_based_risk(self, market_data: dict, record: DebateRecord) -> RiskReviewResult:
        """코드로 리스크 검토 — AI 호출 없음 ($0)"""
        issues = []
        risk_score = 0.0

        # 1. 확신도 체크 (40% 이상이면 통과)
        avg_conf = record.avg_confidence
        if avg_conf < 0.3:
            issues.append(f"확신도 너무 낮음({avg_conf:.0%})")
            risk_score += 0.4

        # 2. 에이전트 의견 분산 체크
        consensus = record.signal_consensus
        total = sum(consensus.values())
        if total > 0:
            max_signal_count = max(consensus.values())
            agreement = max_signal_count / total
            if agreement < 0.4:
                issues.append(f"의견 과도 분산(최대 합의 {agreement:.0%})")
                risk_score += 0.3

        # 3. 변동성 체크
        change_24h = abs(market_data.get("ticker", {}).get("change_24h_pct", 0) or 0)
        if change_24h > 10:
            issues.append(f"극단 변동성(24H {change_24h:.1f}%)")
            risk_score += 0.3

        # 판정
        if risk_score >= 0.6:
            return RiskReviewResult(
                approved=False,
                veto_reason=" + ".join(issues),
                risk_score=risk_score,
            )
        return RiskReviewResult(approved=True, risk_score=risk_score)
