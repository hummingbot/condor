"""Autonomous Trading Agent Framework.

Spawns ACP sessions on a configurable tick loop. Each tick reads a persistent
journal, analyzes market state via skills, makes trading decisions through
executors, and writes observations back to the journal.
"""

from .engine import TickEngine
from .journal import JournalManager, get_session_dir, next_session_number
from .risk import RiskEngine, RiskLimits
from .strategy import Strategy, StrategyStore

__all__ = [
    "TickEngine",
    "JournalManager",
    "get_session_dir",
    "next_session_number",
    "RiskEngine",
    "RiskLimits",
    "Strategy",
    "StrategyStore",
]
