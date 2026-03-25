from .trend_agent import TrendAgent
from .momentum_agent import MomentumAgent
from .volatility_agent import VolatilityAgent
from .volume_agent import VolumeAgent
from .macro_agent import MacroAgent
from .pattern_agent import PatternAgent
from .whale_agent import WhaleAgent
from .copytrade_agent import CopyTradeAgent
from .onchain_agent import OnChainAgent

ANALYST_CLASSES = {
    "trend": TrendAgent,
    "momentum": MomentumAgent,
    "volatility": VolatilityAgent,
    "volume": VolumeAgent,
    "macro": MacroAgent,
    "pattern": PatternAgent,
    "whale": WhaleAgent,
    "copytrade": CopyTradeAgent,
    "onchain": OnChainAgent,
}
