"""Process-lifetime runtime infrastructure.

``condor/runtime/`` owns things whose lifetime is the process: chat sessions
today, and later loops, state and confirmations. ``condor/agents/`` owns the
*domain* model (Agent, strategies, journal, consult/delegate). Keeping the two
apart is deliberate — merging them is what produces an unmaintainable runtime
god-module.

Nothing here is listed in ``reload_handlers()``: re-executing a module that
holds live subprocess handles orphans them.

Only the key and boundary models are re-exported eagerly. Import the facade
itself as ``from condor.runtime import client as runtime`` — it pulls in the
registry lazily to keep the handler import graph acyclic.
"""

from condor.runtime.events import EventType, RuntimeEvent
from condor.runtime.keys import MCP, TELEGRAM, WEB, SessionKey
from condor.runtime.models import PromptRequest, SessionInfo, SessionSpec

__all__ = [
    "MCP",
    "TELEGRAM",
    "WEB",
    "EventType",
    "PromptRequest",
    "RuntimeEvent",
    "SessionInfo",
    "SessionKey",
    "SessionSpec",
]
