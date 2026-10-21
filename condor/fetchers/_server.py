"""Which API server a client talks to — the package's one cache-key rule.

Every process-wide fetcher cache is keyed on this, so one server's answer can
never be served for another. It lived in ``bot_performance`` while that module
held every such cache; ``executors`` needs it too and cannot import from
``bot_performance`` (which imports *from* ``executors``), so the rule moved to a
module below both rather than being copied into the second one (PERF-736).
"""

from __future__ import annotations

from typing import Any


def _server_key(client: Any) -> str:
    """Identify the API server a client talks to, or ``""`` if unidentifiable.

    Keyed on ``base_url`` — never ``id(client)``, whose reuse after GC would hand
    one server's snapshot to another. A client with no ``base_url`` (test doubles)
    cannot be told apart from any other, so it is not cached at all rather than
    sharing a blank key with unrelated servers.
    """
    return str(getattr(client, "base_url", "") or "")
