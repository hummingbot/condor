"""Autonomous Trading Agent Framework.

Spawns ACP sessions on a configurable tick loop. Each tick folds run context
from the RunStore event stream (§7.1), analyzes market state via providers,
makes trading decisions through executors, and emits events back to the run's
append-only stream. Markdown views are generated exports.
"""

from .agent import Agent, AgentStore
from .config import (
    AgentConfig,
    RiskLimitsConfig,
    load_agent_config,
    normalize_config,
    save_agent_config,
)
from .engine import TickEngine
from .risk import RiskEngine, RiskLimits
from .runstore import RunStore, get_run_store, new_ulid

__all__ = [
    "Agent",
    "AgentStore",
    "AgentConfig",
    "RiskLimitsConfig",
    "load_agent_config",
    "normalize_config",
    "save_agent_config",
    "TickEngine",
    "RunStore",
    "get_run_store",
    "new_ulid",
    "RiskEngine",
    "RiskLimits",
]
