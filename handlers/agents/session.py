"""Deprecated — session management moved to ``condor.runtime``.

This module is listed in ``reload_handlers()``, so it is re-executed on every
handler edit. It therefore must not hold any state: the registry now lives in
``condor.runtime.sessions``, which is never reloaded, so hot-reloading handlers
no longer orphans live agent subprocesses.

Kept only so this migration is reviewable in isolation; it is removed once the
last caller is on ``condor.runtime.client``. Deliberately does NOT re-export the
registry or ``AgentSession`` — anything reaching for those must be migrated.
"""

from condor.runtime.client import destroy, get_info  # noqa: F401

__all__ = ["destroy", "get_info"]
