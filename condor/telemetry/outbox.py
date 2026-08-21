"""Durable spool and the (deliberately inert) send path.

Two files live under ``.condor/telemetry/``, the same gitignored place
the rest of the runtime keeps its append-only facts:

``spool.<pid>.jsonl``
    Written by processes that have no flush job of their own — in practice the
    MCP server, which the agent spawns in its own process group with its own
    interpreter and no ``job_queue``. Each process owns its own file, so there
    is no locking and no interleaving; the host drains and deletes them.

``outbox.jsonl``
    Where a batch goes when it could not be delivered. Capped at
    :data:`MAX_OUTBOX_EVENTS` events and :data:`MAX_OUTBOX_AGE_S`, oldest first,
    so an install that never reaches a collector accumulates a bounded file and
    then quietly drops the excess. That is the intended behaviour, not a bug.

The collector address is :data:`COLLECTOR_URL`, compiled in and not
configurable. Whether anything is sent to it is decided entirely by consent: at
level ``off`` — the default — no event is ever created, so :func:`post` is never
reached, and :func:`purge` deletes whatever a withdrawn opt-in left behind.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from condor.fsutil import atomic_write_text

log = logging.getLogger(__name__)

MAX_OUTBOX_EVENTS = 5000
MAX_OUTBOX_AGE_S = 7 * 24 * 3600
MAX_BATCH_BYTES = 512 * 1024
# Collectors cap an envelope by event count as well as by size — the reference
# collector refuses more than 500 with 413 too_many_events. Splitting on bytes
# alone let a backlog build envelopes no collector would ever accept, so the
# outbox could never drain. Keep this at or below the smallest cap we target.
MAX_BATCH_EVENTS = 500
POST_TIMEOUT_S = 10
COLLECTOR_URL = "https://telemetry.hummingbot.org/v1/events"


def root() -> Path:
    """Where the spool lives. One runtime root, resolved in ``condor.paths``."""
    from condor import paths

    return paths.telemetry_dir()


def spool_path(pid: int | None = None) -> Path:
    return root() / f"spool.{pid or os.getpid()}.jsonl"


def outbox_path() -> Path:
    return root() / "outbox.jsonl"


def _append(path: Path, records: list[dict]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue  # a torn last line from a killed process
    except OSError:
        log.debug("Telemetry could not read %s", path, exc_info=True)
    return events


def spool(event: dict) -> None:
    """Append one event to this process's own spool file."""
    _append(spool_path(), [event])


def drain_spools() -> list[dict]:
    """Take everything other processes left behind, including our own file.

    A spool file still being written by a live process is left alone unless it
    is ours: deleting it would lose whatever landed between read and unlink.
    """
    directory = root()
    if not directory.is_dir():
        return []
    mine = os.getpid()
    events: list[dict] = []
    for path in sorted(directory.glob("spool.*.jsonl")):
        try:
            pid = int(path.name.split(".")[1])
        except (IndexError, ValueError):
            continue
        if pid != mine and _alive(pid):
            continue
        events.extend(_read(path))
        try:
            path.unlink()
        except OSError:
            pass
    return events


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True


def stash(events: list[dict]) -> None:
    """Park undeliverable events, then enforce the cap."""
    if not events:
        return
    _append(outbox_path(), events)
    _trim()


def take_stashed() -> list[dict]:
    """Read and clear the outbox, so a flush can retry it alongside fresh events."""
    path = outbox_path()
    events = _read(path)
    try:
        path.unlink()
    except OSError:
        pass
    return events


def _trim() -> None:
    path = outbox_path()
    events = _read(path)
    cutoff = time.time() - MAX_OUTBOX_AGE_S
    kept = [e for e in events if _epoch(e) >= cutoff][-MAX_OUTBOX_EVENTS:]
    if len(kept) == len(events):
        return
    try:
        atomic_write_text(
            path,
            "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in kept),
        )
    except OSError:
        log.debug("Telemetry could not trim the outbox", exc_info=True)


def _epoch(event: dict) -> float:
    try:
        return time.mktime(time.strptime(event["ts"], "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return time.time()


def purge() -> None:
    """Delete every trace. Called when consent is denied or withdrawn."""
    directory = root()
    if not directory.is_dir():
        return
    for path in list(directory.glob("*.jsonl")) + list(directory.glob("*.tmp")):
        try:
            path.unlink()
        except OSError:
            pass
    try:
        directory.rmdir()
    except OSError:
        pass


def batches(events: list[dict]) -> list[list[dict]]:
    """Split into envelopes within both :data:`MAX_BATCH_BYTES` and
    :data:`MAX_BATCH_EVENTS`."""
    out: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for event in events:
        length = len(json.dumps(event, separators=(",", ":")))
        full = size + length > MAX_BATCH_BYTES or len(current) >= MAX_BATCH_EVENTS
        if current and full:
            out.append(current)
            current, size = [], 0
        current.append(event)
        size += length
    if current:
        out.append(current)
    return out


def endpoint() -> str:
    """The collector URL."""
    return COLLECTOR_URL


async def post(envelope: dict) -> bool:
    """Deliver one envelope."""
    url = endpoint()
    if not url:
        return False
    try:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=POST_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=envelope) as response:
                return 200 <= response.status < 300
    except Exception:
        log.debug("Telemetry POST failed; events stay in the outbox", exc_info=True)
        return False
