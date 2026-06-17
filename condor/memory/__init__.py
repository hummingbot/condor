"""User memory — persistent, shared facts about a user.

Pure-filesystem store (no MCP/Telegram deps) keyed by ``user_id``, shared
between the ``/agent`` chat and the trading agents. See ``store.MemoryStore``.
"""

from .store import MemoryStore

__all__ = ["MemoryStore"]
