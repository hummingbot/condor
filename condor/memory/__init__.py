"""Memory — persistent facts an assistant keeps.

Pure-filesystem store (no MCP/transport deps) keyed by the assistant alone
(§4.3): the global/chat tier plus one isolated store per trading agent,
co-located with its definition. See ``paths.store_root`` and
``store.MemoryStore``.
"""

from .paths import iter_stores, store_root
from .skills import SkillStore
from .store import MemoryStore

__all__ = [
    "MemoryStore",
    "SkillStore",
    "store_root",
    "iter_stores",
]
