"""Autonomous Trading Agent Framework.

Spawns ACP sessions on a configurable tick loop. Each tick reads a persistent
journal, analyzes market state via skills, makes trading decisions through
executors, and writes observations back to the journal.

Nothing is re-exported eagerly. ``condor/agents/bundle_review.py`` declares its
import edges to be "the stdlib, plus two modules that are themselves
stdlib-only", because a submission hub has to be able to run the receiver's own
review — and importing a submodule imports its package first, so a plain
``from .engine import TickEngine`` up here handed that pure module the whole
tick engine, the ACP client and ``pydantic`` (1196 modules, ~280 ms). The names
below still resolve; the submodule behind one loads on first access, the way
``mcp_servers/hummingbot_api`` does it for the same reason ([[ARCH-744]]).
"""

from importlib import import_module

#: Public name → the submodule that defines it.
_LAZY = {
    "Agent": "agent",
    "AgentStore": "agent",
    "AgentConfig": "config",
    "RiskLimitsConfig": "config",
    "load_full_config": "config",
    "save_full_config": "config",
    "TickEngine": "engine",
    "JournalManager": "journal",
    "next_session_number": "journal",
    "RiskEngine": "risk",
    "RiskLimits": "risk",
    "Strategy": "strategy",
    "StrategyStore": "strategy",
    "split_key": "strategy",
}

__all__ = [
    "Agent",
    "AgentStore",
    "AgentConfig",
    "RiskLimitsConfig",
    "load_full_config",
    "save_full_config",
    "TickEngine",
    "JournalManager",
    "next_session_number",
    "RiskEngine",
    "RiskLimits",
    "Strategy",
    "StrategyStore",
    "split_key",
]


def __getattr__(name: str):
    """Import the submodule that owns ``name``, once, and cache the name here."""
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))
