"""Autonomous Trading Agent Framework.

Spawns ACP sessions on a configurable tick loop. Each tick reads a persistent
journal, analyzes market state via skills, makes trading decisions through
executors, and writes observations back to the journal.
"""

from .config import AgentConfig, RiskLimitsConfig, load_agent_config, load_full_config, sanitize_config_dict, save_agent_config, save_full_config
from .engine import TickEngine
from .journal import JournalManager, get_session_dir, next_session_number
from .risk import RiskEngine, RiskLimits, resolve_risk_limits
from .strategy import Strategy, StrategyStore

__all__ = [
    "AgentConfig",
    "RiskLimitsConfig",
    "load_agent_config",
    "load_full_config",
    "save_agent_config",
    "save_full_config",
    "sanitize_config_dict",
    "TickEngine",
    "JournalManager",
    "get_session_dir",
    "next_session_number",
    "RiskEngine",
    "RiskLimits",
    "resolve_risk_limits",
    "Strategy",
    "StrategyStore",
]
