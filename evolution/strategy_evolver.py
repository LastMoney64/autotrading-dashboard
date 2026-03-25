"""
StrategyEvolver — 전략 진화 엔진

주간 자기반성 루프를 실행한다:
1. 전체 에이전트 성과 집계
2. Claude (EvolutionAgent)에게 분석 요청
3. 가중치 조정 실행
4. 에이전트 격리/복귀 결정
5. Recruiter에게 신규 에이전트 필요성 판단 요청

이 파일이 Evolution 시스템의 최상위 오케스트레이터.
"""

import json
import logging
from datetime import datetime
from typing import Optional

from core.agent_registry import AgentRegistry
from core.base_agent import AgentRole, AgentStatus, BaseAgent
from core.message_bus import MessageBus, MessageType
from config.settings import Settings
from db.database import Database
from memory.performance_memory import PerformanceMemory
from evolution.performance_tracker import PerformanceTracker
from evolution.weight_adjuster import WeightAdjuster

logger = logging.getLogger(__name__)


class StrategyEvolver:
    """전략 진화 오케스트레이터"""

    def __init__(
        self,
        registry: AgentRegistry,
        bus: MessageBus,
        db: Database,
        perf_memory: PerformanceMemory,
        settings: Settings,
    ):
        self.registry = registry
        self.bus = bus
        self.db = db
        self.perf_memory = perf_memory
        self.settings = settings

        self.tracker = PerformanceTracker(db, registry)
        self.adjuster = WeightAdjuster(registry, db, perf_memory, self.tracker)

        # 마지막 진화 실행 시점
        self.last_evolution: Optional[datetime] = None
        self.total_evolution_runs = 0

    async def run_evolution_cycle(self) -> dict:
        """
        전체 진화 사이클 실행

        Returns:
            진화 결과 리포트
        """
        logger.info("=== Evolution Cycle 시작 ===")
        started_at = datetime.utcnow()

        result = {
            "started_at": started_at.isoformat(),
            "performance_summary": {},
            "weight_changes": [],
            "isolations": [],
            "reactivations": [],
            "llm_analysis": "",
            "recruiter_suggestion": "",
        }

        # 1. 전체 성과 분석
        summary = self.tracker.get_summary()
        result["performance_summary"] = summary
        logger.info(f"성과 분석 완료: {len(summary['agent_reports'])}개 에이전트")

        # 2. Claude EvolutionAgent에 분석 요청
        evolution_agent = self.registry.get_special_agent(AgentRole.EVOLUTION)
        if evolution_agent:
            try:
                llm_analysis = await self._request_llm_analysis(evolution_agent, summary)
                result["llm_analysis"] = llm_analysis
                logger.info("EvolutionAgent LLM 분석 완료")
            except Exception as e:
                logger.error(f"EvolutionAgent LLM 분석 실패: {e}")
                result["llm_analysis"] = f"LLM 분석 실패: {e}"

        # 3. 가중치 조정
        weight_changes = self.adjuster.adjust_all()
        result["weight_changes"] = weight_changes
        logger.info(f"가중치 조정: {len(weight_changes)}개 변경")

        # 4. 격리/복귀 실행
        isolations = self._execute_isolations(summary["agents_to_isolate"])
        reactivations = self._execute_reactivations(summary["agents_to_reactivate"])
        result["isolations"] = isolations
        result["reactivations"] = reactivations

        # 5. Recruiter에게 신규 에이전트 필요성 문의
        recruiter_agent = self.registry.get_special_agent(AgentRole.RECRUITER)
        if recruiter_agent:
            try:
                suggestion = await self._request_recruiter(recruiter_agent, summary)
                result["recruiter_suggestion"] = suggestion
            except Exception as e:
                logger.error(f"Recruiter 문의 실패: {e}")

        # 6. MessageBus로 결과 브로드캐스트
        await self.bus.broadcast(
            MessageType.EVOLUTION_UPDATE,
            sender_id="evolution_engine",
            payload={
                "weight_changes": weight_changes,
                "isolations": isolations,
                "reactivations": reactivations,
            },
        )

        # 완료
        result["finished_at"] = datetime.utcnow().isoformat()
        self.last_evolution = started_at
        self.total_evolution_runs += 1
        logger.info(f"=== Evolution Cycle 완료 (#{self.total_evolution_runs}) ===")

        return result

    def should_run(self, total_trades_since_last: int) -> bool:
        """진화 사이클 실행 여부 판단"""
        return total_trades_since_last >= self.settings.weight_update_interval

    # ── 내부 메서드 ────────────────────────────────────────

    async def _request_llm_analysis(self, agent: BaseAgent, summary: dict) -> str:
        """EvolutionAgent에게 종합 분석 요청"""
        # 에이전트 리포트 간결하게 정리
        agent_lines = []
        for r in summary["agent_reports"]:
            line = (
                f"- {r['name']} ({r['agent_id']}): "
                f"승률 {r['win_rate']:.0%}, "
                f"최근 {r['recent_win_rate']:.0%}, "
                f"가중치 {r['weight']:.2f}, "
                f"등급 {r['grade']}, "
                f"연패 {r['losing_streak']}"
            )
            agent_lines.append(line)

        overall = summary.get("overall", {})

        prompt = f"""## 주간 성과 분석 요청

### 시스템 전체 성과
- 총 거래: {overall.get('total_trades', 0)}회
- 승률: {overall.get('wins', 0)}/{overall.get('total_trades', 0)}
- 평균 PnL: {overall.get('avg_pnl', 0):.2f}%
- 누적 PnL: {overall.get('total_pnl', 0):.2f}%
- 최고 수익: {overall.get('best_trade', 0):.2f}%
- 최악 손실: {overall.get('worst_trade', 0):.2f}%

### 에이전트별 성과
{chr(10).join(agent_lines)}

### 등급 분포
{json.dumps(summary.get('grade_distribution', {}), ensure_ascii=False)}

### 분석 요청
1. 어떤 에이전트가 가장 잘 했고, 왜 잘 했는지?
2. 어떤 에이전트가 부진하고, 원인이 뭔지?
3. 현재 팀 구성에서 빠진 관점이 있는지?
4. 가중치 조정 외 추가로 해야 할 전략 변경이 있는지?
5. 새로운 에이전트를 영입해야 할 필요가 있는지?

간결하게 핵심만 답변해주세요."""

        return await agent.call_llm(prompt, max_tokens=1500)

    async def _request_recruiter(self, agent: BaseAgent, summary: dict) -> str:
        """Recruiter에게 신규 에이전트 필요성 판단 요청"""
        grade_dist = summary.get("grade_distribution", {})
        isolated = summary.get("agents_to_isolate", [])

        prompt = f"""## 신규 에이전트 영입 검토

현재 팀 구성: {len(summary['agent_reports'])}명
등급 분포: {json.dumps(grade_dist, ensure_ascii=False)}
격리 예정: {isolated if isolated else "없음"}

현재 에이전트 전문 분야:
- 추세 추종 (EMA, MACD, ADX)
- 모멘텀/반전 (RSI, 스토캐스틱, CCI)
- 변동성 (볼린저밴드, ATR)
- 거래량 (OBV, VWAP, OI, 펀딩비)
- 거시/감성 (뉴스, 공포탐욕, 피보나치)
- 차트 패턴 (헤드앤숄더, 삼각수렴)
- 고래 추적 (거래소 입출금, 대량 이체)
- 카피트레이딩 (탑트레이더 포지션)
- 온체인 (MVRV, SOPR, NUPL)

질문:
1. 현재 커버하지 못하는 중요한 분석 관점이 있나?
2. 새 에이전트를 영입한다면 어떤 전략의 에이전트가 좋을까?
3. 영입하지 않아도 된다면 "불필요"라고 답해주세요.

JSON 형식으로 답변:
{{"needed": true/false, "agent_spec": {{"name": "...", "specialty": "...", "indicators": [...], "reason": "..."}}}}"""

        return await agent.call_llm(prompt, max_tokens=800)

    def _execute_isolations(self, agent_ids: list[str]) -> list[dict]:
        """성과 저조 에이전트 격리"""
        results = []
        for agent_id in agent_ids:
            agent = self.registry.get(agent_id)
            if not agent or agent.status != AgentStatus.ACTIVE:
                continue

            stats = self.db.get_agent_stats(agent_id, last_n=50)
            reason = f"성과 저조: 승률 {stats.get('win_rate', 0):.0%} (기준: {self.settings.isolation_win_rate:.0%})"

            self.registry.isolate_agent(agent_id, reason)
            results.append({
                "agent_id": agent_id,
                "name": agent.name,
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat(),
            })
            logger.warning(f"에이전트 격리: {agent.name} — {reason}")

        return results

    def _execute_reactivations(self, agent_ids: list[str]) -> list[dict]:
        """복귀 조건 충족 에이전트 활성화"""
        results = []
        for agent_id in agent_ids:
            agent = self.registry.get(agent_id)
            if not agent:
                continue
            if agent.status not in (AgentStatus.ISOLATED, AgentStatus.PROBATION):
                continue

            stats = self.db.get_agent_stats(agent_id, last_n=30)
            reason = f"복귀: 승률 {stats.get('win_rate', 0):.0%} 달성"

            self.registry.activate_agent(agent_id)
            # 복귀 시 가중치 보수적으로 시작
            self.registry.update_weight(agent_id, 0.8)
            self.db.save_weight_change(agent_id, agent.weight, 0.8, reason)

            results.append({
                "agent_id": agent_id,
                "name": agent.name,
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat(),
            })
            logger.info(f"에이전트 복귀: {agent.name} — {reason}")

        return results

    def get_status(self) -> dict:
        """진화 엔진 상태"""
        return {
            "total_runs": self.total_evolution_runs,
            "last_evolution": self.last_evolution.isoformat() if self.last_evolution else None,
            "update_interval": self.settings.weight_update_interval,
            "isolation_threshold": self.settings.isolation_win_rate,
            "probation_threshold": self.settings.probation_win_rate,
        }
