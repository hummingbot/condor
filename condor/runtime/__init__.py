"""Process-lifetime runtime infrastructure.

``condor/runtime/`` owns things whose lifetime is the process: chat sessions
today, and later loops, state and confirmations. ``condor/agents/`` owns the
*domain* model (Agent, strategies, journal, delegate). Keeping the two
apart is deliberate — merging them is what produces an unmaintainable runtime
god-module.

Nothing here is listed in ``reload_handlers()``: re-executing a module that
holds live subprocess handles orphans them.

The key and boundary models are re-exported, but nothing is imported until a
name is asked for. ``danger.py`` and ``secrets.py`` are stdlib-only on purpose
— everything that dispatches on a tool name imports ``danger`` — and importing
a submodule imports its package first, so the eager block that used to sit here
pulled ``condor.acp`` and ``pydantic`` in behind both of them (1135 modules,
~260 ms). Import the facade itself as ``from condor.runtime import client as
runtime``; it in turn pulls in the registry lazily to keep the handler import
graph acyclic ([[ARCH-744]]).
"""

from importlib import import_module

#: Public name → the submodule that defines it.
_LAZY = {
    "EventType": "events",
    "RuntimeEvent": "events",
    "MCP": "keys",
    "TELEGRAM": "keys",
    "WEB": "keys",
    "SessionKey": "keys",
    "slot_of": "keys",
    "PromptImage": "models",
    "PromptRequest": "models",
    "SessionInfo": "models",
    "SessionSpec": "models",
}

__all__ = [
    "MCP",
    "TELEGRAM",
    "WEB",
    "EventType",
    "PromptImage",
    "PromptRequest",
    "RuntimeEvent",
    "SessionInfo",
    "SessionKey",
    "slot_of",
    "SessionSpec",
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
