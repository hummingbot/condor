"""Scratch key/value state for loops.

Condor already has two places to put a fact, and neither fits a cursor:

===========  ==============  ===================  ======================
Layer        Lifetime        Audience             Written by
===========  ==============  ===================  ======================
Memory       durable         the agent's          manage_memory,
             curated         reasoning            deliberately
Journal      append-only     humans + the next    every tick
             narrative       tick's prompt
State        ephemeral       code, not prose      loop code and tools
             overwritable
===========  ==============  ===================  ======================

If a fact deserves to be *remembered*, it belongs in memory. State is for the
things a loop otherwise re-derives by re-querying the API every tick: the last
processed executor id, a cooldown deadline, when an alert last fired.

It is persisted (a cooldown that resets on deploy is worse than no cooldown)
but it is not precious: writes are debounced, so a hard kill can lose the last
second of updates. That is an accepted trade — state is a cursor, not a ledger.

Deliberately NOT exposed to chat sessions: chat has memory for this, and a
second "remember" verb on the assistant surface only invites confusion.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from condor.runtime.registry_file import read_status, write_status

log = logging.getLogger(__name__)

STATE_FILENAME = "state.json"

# At most one disk write per namespace per second. Values are in memory
# immediately; the file is a restart cushion, not the source of truth.
WRITE_DEBOUNCE_S = 1.0

# Namespaces map to directories, so they must not be able to escape one.
_SAFE_NAMESPACE = re.compile(r"^[A-Za-z0-9._-]+$")

# namespace -> {key: {"value": ..., "expires_at": float | None}}
_cache: dict[str, dict[str, dict]] = {}
_last_write: dict[str, float] = {}
_dirty: set[str] = set()


class NamespaceError(ValueError):
    """Raised for a namespace that could not be turned into a safe path."""


def _validate(namespace: str) -> str:
    if not namespace or not _SAFE_NAMESPACE.match(namespace):
        raise NamespaceError(
            f"Invalid state namespace {namespace!r}: "
            "use letters, digits, dot, dash or underscore."
        )
    return namespace


def _state_dir(namespace: str) -> Path:
    """Where a namespace's file lives.

    ``{agent}.{strategy}`` namespaces sit under the strategy they belong to, so
    deleting a strategy takes its state with it. Everything else falls back to
    a shared runtime directory.
    """
    from condor.agents.agent import _DATA_ROOT

    if namespace.count(".") == 1:
        agent_slug, strategy_slug = namespace.split(".", 1)
        candidate = _DATA_ROOT / agent_slug / "strategies" / strategy_slug
        if candidate.is_dir():
            return candidate

    return Path(_DATA_ROOT).parent / "condor" / ".runtime" / "state" / namespace


# ── Read/write ──


def load_namespace(namespace: str) -> int:
    """Hydrate a namespace from disk. Returns the number of keys loaded."""
    _validate(namespace)
    data = read_status(_state_dir(namespace), STATE_FILENAME) or {}
    entries = data.get("entries")
    _cache[namespace] = entries if isinstance(entries, dict) else {}
    return len(_cache[namespace])


def _entries(namespace: str) -> dict[str, dict]:
    if namespace not in _cache:
        load_namespace(namespace)
    return _cache[namespace]


def _flush(namespace: str, force: bool = False) -> None:
    """Persist a namespace, at most once per WRITE_DEBOUNCE_S unless forced."""
    now = time.time()
    if not force and now - _last_write.get(namespace, 0.0) < WRITE_DEBOUNCE_S:
        _dirty.add(namespace)
        return
    write_status(
        _state_dir(namespace),
        STATE_FILENAME,
        namespace=namespace,
        entries=_cache.get(namespace, {}),
    )
    _last_write[namespace] = now
    _dirty.discard(namespace)


def flush_all() -> None:
    """Force-write every namespace with pending changes (shutdown hook)."""
    for namespace in list(_dirty):
        _flush(namespace, force=True)


def set_state(
    namespace: str, key: str, value: Any, *, expires_in: int | None = None
) -> None:
    """Store a JSON-serializable value under ``namespace``.

    Rejects non-serializable values loudly rather than at flush time, when the
    caller is long gone and the traceback says nothing useful.
    """
    _validate(namespace)
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            f"State values must be JSON-serializable; {type(value).__name__} "
            f"is not (key={key!r}). Convert it first — e.g. a datetime to an "
            f"ISO string or a timestamp."
        ) from exc

    _entries(namespace)[key] = {
        "value": value,
        "expires_at": time.time() + expires_in if expires_in else None,
    }
    _flush(namespace)


def get_state(namespace: str, key: str, default: Any = None) -> Any:
    """Read a value, treating an expired key as absent."""
    _validate(namespace)
    entry = _entries(namespace).get(key)
    if entry is None:
        return default
    if _expired(entry):
        _entries(namespace).pop(key, None)
        _flush(namespace)
        return default
    return entry.get("value", default)


def list_state(namespace: str) -> dict[str, Any]:
    """Every live key in a namespace, expired ones dropped."""
    _validate(namespace)
    entries = _entries(namespace)
    expired = [k for k, e in entries.items() if _expired(e)]
    for k in expired:
        entries.pop(k, None)
    if expired:
        _flush(namespace)
    return {k: e.get("value") for k, e in entries.items()}


def clear_state(namespace: str, key: str | None = None) -> int:
    """Drop one key, or the whole namespace. Returns how many keys went."""
    _validate(namespace)
    entries = _entries(namespace)
    if key is None:
        count = len(entries)
        _cache[namespace] = {}
    else:
        count = 1 if entries.pop(key, None) is not None else 0
    _flush(namespace, force=True)
    return count


def _expired(entry: dict) -> bool:
    expires_at = entry.get("expires_at")
    return bool(expires_at) and expires_at <= time.time()


# ── Namespace helpers ──


def namespace_for(engine) -> str:
    """The namespace a running strategy owns.

    Keyed on (agent, strategy) rather than the session number so a cursor
    survives into the next session — which is the point of persisting it.
    """
    return f"{engine.agent.slug}.{engine.strategy.slug}"


def namespace_for_session(key) -> str:
    return str(key).replace(":", ".")


class BoundState:
    """A view pinned to one namespace, so callers cannot reach another's."""

    def __init__(self, namespace: str):
        self.namespace = _validate(namespace)

    def get(self, key: str, default: Any = None) -> Any:
        return get_state(self.namespace, key, default)

    def set(self, key: str, value: Any, *, expires_in: int | None = None) -> None:
        set_state(self.namespace, key, value, expires_in=expires_in)

    def list(self) -> dict[str, Any]:
        return list_state(self.namespace)

    def clear(self, key: str | None = None) -> int:
        return clear_state(self.namespace, key)


def cleanup_orphans(agents_root: Path | None = None) -> int:
    """Forget namespaces whose strategy directory is gone. Returns the count."""
    from condor.agents.agent import _DATA_ROOT

    root = Path(agents_root) if agents_root is not None else _DATA_ROOT
    removed = 0
    for namespace in list(_cache):
        if namespace.count(".") != 1:
            continue
        agent_slug, strategy_slug = namespace.split(".", 1)
        if not (root / agent_slug / "strategies" / strategy_slug).is_dir():
            _cache.pop(namespace, None)
            _dirty.discard(namespace)
            _last_write.pop(namespace, None)
            removed += 1
    return removed
