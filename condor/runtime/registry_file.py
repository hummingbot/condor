"""Durable runtime status, written next to the data it describes.

A ``TickEngine`` lives in memory, so a restart used to erase every trace that a
loop had been running: the read side then reported a hardcoded "idle", which is
a guess, not a fact. A tiny ``status.json`` beside each session's journal makes
the distinction between "stopped cleanly" and "the process died" a recorded
fact that survives the process.

Writes are atomic (temp file + ``os.replace``) so a crash mid-write leaves the
previous status intact rather than a truncated file.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

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


# States that only make sense while the owning process is alive. Finding one of
# these under a foreign boot id means the run died without recording an end.
LIVE_STATES = frozenset({LoopState.RUNNING, LoopState.PAUSED})


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
    """
    if session_dir is None:
        return

    path = status_path(session_dir, filename)
    current = read_status(session_dir, filename) or {}
    current.update(fields)
    current["boot_id"] = BOOT_ID
    current["pid"] = os.getpid()
    current["updated_at"] = time.time()

    tmp = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
        os.replace(tmp, path)  # atomic within a filesystem
    except Exception:
        log.warning("Could not write status for %s", session_dir, exc_info=True)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


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
