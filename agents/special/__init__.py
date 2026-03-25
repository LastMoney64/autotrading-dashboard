from .moderator_agent import ModeratorAgent
from .judge_agent import JudgeAgent
from .risk_agent import RiskAgent
from .memory_agent import MemoryAgent

SPECIAL_CLASSES = {
    "moderator": ModeratorAgent,
    "judge": JudgeAgent,
    "risk": RiskAgent,
    "memory": MemoryAgent,
}
