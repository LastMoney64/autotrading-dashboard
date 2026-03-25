"""
AgentRegistry — 에이전트 등록/관리/격리/영입 창구

모든 에이전트의 생명주기를 관리한다.
- 에이전트 등록/제거
- 상태별 조회 (활성/격리/수습)
- 가중치 관리
- 동적 에이전트 추가 (Recruiter 연동)
"""

from typing import Optional
from datetime import datetime
from .base_agent import BaseAgent, AgentRole, AgentStatus


class AgentRegistry:
    """에이전트 레지스트리 — 모든 에이전트를 관리하는 중앙 허브"""

    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}
        self._registration_log: list[dict] = []

    # ── 등록 / 제거 ────────────────────────────────────

    def register(self, agent: BaseAgent) -> None:
        """에이전트 등록"""
        if agent.agent_id in self._agents:
            raise ValueError(f"Agent '{agent.agent_id}' already registered")

        self._agents[agent.agent_id] = agent
        self._registration_log.append({
            "action": "register",
            "agent_id": agent.agent_id,
            "name": agent.name,
            "role": agent.role.value,
            "timestamp": datetime.utcnow().isoformat(),
        })

    def unregister(self, agent_id: str) -> Optional[BaseAgent]:
        """에이전트 제거 (완전 삭제)"""
        agent = self._agents.pop(agent_id, None)
        if agent:
            self._registration_log.append({
                "action": "unregister",
                "agent_id": agent_id,
                "timestamp": datetime.utcnow().isoformat(),
            })
        return agent

    # ── 조회 ────────────────────────────────────────────

    def get(self, agent_id: str) -> Optional[BaseAgent]:
        """에이전트 ID로 조회"""
        return self._agents.get(agent_id)

    def get_all(self) -> list[BaseAgent]:
        """전체 에이전트 목록"""
        return list(self._agents.values())

    def get_by_role(self, role: AgentRole) -> list[BaseAgent]:
        """특정 역할의 에이전트 목록"""
        return [a for a in self._agents.values() if a.role == role]

    def get_by_status(self, status: AgentStatus) -> list[BaseAgent]:
        """특정 상태의 에이전트 목록"""
        return [a for a in self._agents.values() if a.status == status]

    def get_active_analysts(self) -> list[BaseAgent]:
        """활성 분석 에이전트만 조회 (토론 참여 가능한)"""
        return [
            a for a in self._agents.values()
            if a.role == AgentRole.ANALYST and a.status == AgentStatus.ACTIVE
        ]

    def get_all_analysts(self) -> list[BaseAgent]:
        """격리/수습 포함 전체 분석 에이전트"""
        return [
            a for a in self._agents.values()
            if a.role == AgentRole.ANALYST
        ]

    def get_special_agent(self, role: AgentRole) -> Optional[BaseAgent]:
        """특수 에이전트 1개 조회 (Judge, Risk 등)"""
        agents = self.get_by_role(role)
        return agents[0] if agents else None

    # ── 상태 관리 ───────────────────────────────────────

    def isolate_agent(self, agent_id: str, reason: str = "") -> bool:
        """에이전트 격리"""
        agent = self.get(agent_id)
        if not agent:
            return False
        agent.isolate()
        self._registration_log.append({
            "action": "isolate",
            "agent_id": agent_id,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
        })
        return True

    def activate_agent(self, agent_id: str) -> bool:
        """에이전트 활성화 (격리 해제)"""
        agent = self.get(agent_id)
        if not agent:
            return False
        agent.activate()
        self._registration_log.append({
            "action": "activate",
            "agent_id": agent_id,
            "timestamp": datetime.utcnow().isoformat(),
        })
        return True

    def set_probation(self, agent_id: str) -> bool:
        """에이전트 수습 상태 설정 (신규 에이전트용)"""
        agent = self.get(agent_id)
        if not agent:
            return False
        agent.set_probation()
        self._registration_log.append({
            "action": "probation",
            "agent_id": agent_id,
            "timestamp": datetime.utcnow().isoformat(),
        })
        return True

    # ── 가중치 관리 ─────────────────────────────────────

    def update_weight(self, agent_id: str, new_weight: float) -> bool:
        """에이전트 가중치 업데이트"""
        agent = self.get(agent_id)
        if not agent:
            return False
        old_weight = agent.weight
        agent.weight = max(0.0, min(new_weight, 5.0))  # 0~5 범위 제한
        self._registration_log.append({
            "action": "weight_update",
            "agent_id": agent_id,
            "old_weight": old_weight,
            "new_weight": agent.weight,
            "timestamp": datetime.utcnow().isoformat(),
        })
        return True

    def get_normalized_weights(self) -> dict[str, float]:
        """활성 분석 에이전트들의 정규화된 가중치 (합 = 1.0)"""
        active = self.get_active_analysts()
        if not active:
            return {}
        total = sum(a.weight for a in active)
        if total == 0:
            equal = 1.0 / len(active)
            return {a.agent_id: equal for a in active}
        return {a.agent_id: a.weight / total for a in active}

    # ── 통계 ────────────────────────────────────────────

    def get_summary(self) -> dict:
        """레지스트리 전체 요약"""
        agents = self.get_all()
        return {
            "total_agents": len(agents),
            "active": len([a for a in agents if a.status == AgentStatus.ACTIVE]),
            "isolated": len([a for a in agents if a.status == AgentStatus.ISOLATED]),
            "probation": len([a for a in agents if a.status == AgentStatus.PROBATION]),
            "analysts": len(self.get_all_analysts()),
            "agents": [a.to_dict() for a in agents],
            "normalized_weights": self.get_normalized_weights(),
        }

    @property
    def registration_log(self) -> list[dict]:
        """등록/변경 이력"""
        return list(self._registration_log)

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._agents
