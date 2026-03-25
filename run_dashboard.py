"""
대시보드 단독 실행 스크립트

트레이딩 봇 없이 대시보드만 실행할 때 사용.
DB에 저장된 데이터를 읽어서 보여준다.

실행: python run_dashboard.py
접속: http://localhost:8080
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

import uvicorn

from core.agent_registry import AgentRegistry
from config.settings import Settings
from config.agent_configs import AGENT_CONFIGS
from db.database import Database
from evolution.performance_tracker import PerformanceTracker
from dashboard.app import create_app

# 에이전트 클래스 임포트
from agents.analysts.trend_agent import TrendAgent
from agents.analysts.momentum_agent import MomentumAgent
from agents.analysts.volatility_agent import VolatilityAgent
from agents.analysts.volume_agent import VolumeAgent
from agents.analysts.macro_agent import MacroAgent
from agents.analysts.pattern_agent import PatternAgent
from agents.analysts.whale_agent import WhaleAgent
from agents.analysts.copytrade_agent import CopyTradeAgent
from agents.analysts.onchain_agent import OnChainAgent
from agents.special.moderator_agent import ModeratorAgent
from agents.special.judge_agent import JudgeAgent
from agents.special.risk_agent import RiskAgent
from agents.special.memory_agent import MemoryAgent
from agents.special.evolution_agent import EvolutionAgent
from agents.special.recruiter_agent import RecruiterAgent

AGENT_CLASSES = {
    "trend": TrendAgent, "momentum": MomentumAgent,
    "volatility": VolatilityAgent, "volume": VolumeAgent,
    "macro": MacroAgent, "pattern": PatternAgent,
    "whale": WhaleAgent, "copytrade": CopyTradeAgent,
    "onchain": OnChainAgent, "moderator": ModeratorAgent,
    "judge": JudgeAgent, "risk": RiskAgent,
    "memory": MemoryAgent, "evolution": EvolutionAgent,
    "recruiter": RecruiterAgent,
}


def main():
    settings = Settings.from_env()
    db = Database(settings.db_path)
    registry = AgentRegistry()

    # 에이전트 등록 (대시보드에서 상태 표시용)
    for agent_id, config in AGENT_CONFIGS.items():
        cls = AGENT_CLASSES.get(agent_id)
        if cls:
            registry.register(cls(config))

    tracker = PerformanceTracker(db, registry)
    app = create_app(registry, db, tracker, settings)

    port = int(os.environ.get("PORT", 8080))

    print("=" * 50)
    print("  AutoTrading Dashboard")
    print(f"  http://localhost:{port}")
    print("=" * 50)

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
