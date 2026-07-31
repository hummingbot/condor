"""User memory — persistent facts the agent keeps about a user.

Pure-filesystem store (no MCP/Telegram deps) keyed by ``(assistant, user_id)``
(FEAT-003): each assistant — the ``/agent`` chat and every trading agent — has
its own isolated store, co-located with its definition. See ``paths.store_root``
and ``store.MemoryStore``.
"""

from .paths import iter_user_stores, store_root
from .skills import SkillStore
from .store import MemoryStore

__all__ = ["MemoryStore", "SkillStore", "store_root", "iter_user_stores"]
