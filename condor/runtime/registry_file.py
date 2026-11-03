"""Durable runtime status, written next to the data it describes.

A ``TickEngine`` lives in memory, so a restart used to erase every trace that a
loop had been running: the read side then reported a hardcoded "idle", which is
a guess, not a fact. A tiny ``status.json`` beside each session's journal makes
the distinction between "stopped cleanly" and "the process died" a recorded
fact that survives the process.

Writes are atomic (temp file + ``os.replace``) so a crash mid-write leaves the
previous status intact rather than a truncated file, and each file's
read-modify-write merge runs under its own lock so two threads updating
different fields of one record cannot erase each other.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from condor.fsutil import atomic_write_json

log = logging.getLogger(__name__)

STATUS_FILENAME = "status.json"

# Identifies this process. A status file carrying a different boot id is by
# definition left over from a process that is no longer running.
BOOT_ID = str(uuid.uuid4())


class LoopState:
    """States a supervised run can be in."""

    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    ERROR = "error"
    # Ended by the process going down, not by its owner. A clean shutdown stops
    # every engine, which used to record STOPPED — indistinguishable on disk
    # from "the owner pressed stop", so the next boot had nothing to resume and
    # ``restart_on_boot`` only ever fired after a crash.
    SUSPENDED = "suspended"


# States that only make sense while the owning process is alive. Finding one of
# these under a foreign boot id means the run died without recording an end.
LIVE_STATES = frozenset({LoopState.RUNNING, LoopState.PAUSED})


# One lock per status file, guarding the read-decide-write in ``write_status``.
# The atomic rename only promises that a *reader* never sees half a file; it
# says nothing about two writers, and there are genuinely two here in one
# process: the sharing verbs run off the loop (``asyncio.to_thread`` in
# ``condor/web/routes/sharing.py`` and in the sweep) and stamp the share
# receipt onto a conversation's ``meta.json`` while the event loop keeps
# writing ``turn_count`` to the same file. ``read_text`` releases the GIL, so
# without this a whole merge fits inside the other writer's read→replace
# window and is then erased by it — losing ``share_delete_token`` (the only
# local copy of the capability that revokes a share) or ``share_excluded``
# (the flag the sweep honours forever).
#
# Keyed per file so unrelated status files stay concurrent, and reentrant so a
# caller that already holds one cannot wedge itself. Held only across the
# read, the merge and the rename — all synchronous, microseconds — and
# **never across an ``await``**: ``write_status`` has no await in it and must
# not grow one.
_MERGE_LOCKS: dict[str, threading.RLock] = {}
_MERGE_LOCKS_GUARD = threading.Lock()


def _merge_lock(path: Path) -> threading.RLock:
    """The lock for one status file, minted on first use.

    Keyed by the resolved path so two spellings of the same file (a symlinked
    root, a relative session dir) share one lock. The dictionary only grows —
    one small entry per status file this process has written — because
    discarding a lock while a writer holds it would quietly hand the next
    writer a different one.
    """
    try:
        key = str(path.resolve())
    except OSError:  # pragma: no cover - resolve is non-strict, but be safe
        key = str(path.absolute())
    with _MERGE_LOCKS_GUARD:
        lock = _MERGE_LOCKS.get(key)
        if lock is None:
            lock = _MERGE_LOCKS[key] = threading.RLock()
    return lock


def status_path(session_dir: Path, filename: str = STATUS_FILENAME) -> Path:
    return Path(session_dir) / filename


def write_status(
    session_dir: Path | None, filename: str = STATUS_FILENAME, **fields: Any
) -> None:
    """Atomically merge ``fields`` into a status file in ``session_dir``.

    Never raises: losing a status write must not take down a running loop.
    Experiments pass ``session_dir=None`` (they have no session directory and
    deliberately no journal), which is a silent no-op.

    ``filename`` exists for records that share a directory — delegations live
    side by side under ``delegations/``, so each gets ``{task_id}.status.json``.

    What makes concurrent merges safe is ``_merge_lock``, not the atomic
    rename: the rename keeps a reader from seeing half a file, while the lock
    is what stops one thread's whole merge from landing inside another's
    read-modify-write window and being overwritten by it. The lock is held
    across the read, the merge and the rename, so this function stays
    synchronous — do not put an ``await`` in it.
    """
    if session_dir is None:
        return

    path = status_path(session_dir, filename)
    with _merge_lock(path):
        current = read_status(session_dir, filename) or {}
        current.update(fields)
        current["boot_id"] = BOOT_ID
        current["pid"] = os.getpid()
        current["updated_at"] = time.time()

        try:
            # Unique temp file per writer, inside the target's directory:
            # several processes update the same status/meta file (loops,
            # state, delegate), and a shared temp name would let them tear
            # each other's write.
            atomic_write_json(path, current, indent=2)
        except Exception:
            log.warning("Could not write status for %s", session_dir, exc_info=True)


def read_status(
    session_dir: Path, filename: str = STATUS_FILENAME
) -> dict[str, Any] | None:
    """Read a status file, tolerating absence and corruption.

    A truncated or hand-edited file reads as "no status" rather than raising:
    the caller's fallback (an mtime heuristic, for sessions that predate this
    file) is always better than a crash while browsing.
    """
    path = status_path(session_dir, filename)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.debug("Unreadable status file at %s", path, exc_info=True)
        return None
    return data if isinstance(data, dict) else None


def is_stale(status: dict[str, Any]) -> bool:
    """True when this status was written by a process that is no longer us."""
    return status.get("state") in LIVE_STATES and status.get("boot_id") not in (
        BOOT_ID,
    )


def is_suspended(status: dict[str, Any]) -> bool:
    """True when a previous process wound this run down on its way out.

    The clean counterpart of :func:`is_stale`: nothing was lost, the run simply
    ended because the process did. A boot pass settles it — resuming it when the
    session opted in — but must never report it as an interrupted run.
    """
    return status.get("state") == LoopState.SUSPENDED and status.get("boot_id") not in (
        BOOT_ID,
    )
