"""Notification outbox — the single chokepoint for user pings (§4.1).

Every emitter calls notify(): the entry is ALWAYS appended to the outbox
(``store/notifications.jsonl``) — the channel-agnostic source any harness
consumes. Claude Code tails it (Monitor today, a channels plugin later),
the dashboard can poll it, relays (integrations/notify_relay) forward it
into any harness's conversation.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

OUTBOX_PATH = Path(__file__).resolve().parent.parent / "store" / "notifications.jsonl"

_lock = threading.Lock()


def _append_outbox(entry: dict) -> None:
    """Serialized main-process append with fsync (§4.1) — the outbox is the
    authoritative record, so it gets the durability the title implies. The
    MCP subprocess never calls this directly: it enqueues over the control
    socket ("notify.emit"), keeping one writer per file."""
    OUTBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with OUTBOX_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())


async def notify(
    text: str,
    *,
    user_id: int = 0,
    chat_id: int = 0,
    agent_id: str = "",
    kind: str = "info",
    origin: str = "",
) -> dict:
    """Record a notification in the outbox. Returns the outbox entry."""
    from condor.agents.runstore import new_ulid

    entry = {
        # Stable notification id (§4.1): relay cursors track the last
        # DELIVERED id — never a byte offset — for at-least-once delivery
        # with id-based dedup across crashes.
        "id": new_ulid(),
        "ts": time.time(),
        "user_id": user_id,
        "chat_id": chat_id,
        "agent_id": agent_id,
        "kind": kind,
        "origin": origin,
        "text": text,
    }

    try:
        _append_outbox(entry)
    except Exception:
        # The outbox is load-bearing: log loudly, still return the entry
        log.exception("failed to append notification outbox")

    return entry


def read_notifications(
    limit: int = 50,
    since_ts: Optional[float] = None,
    agent_id: Optional[str] = None,
    since_id: Optional[str] = None,
) -> list[dict]:
    """Read recent outbox entries, oldest first. A torn final line (crash
    mid-append) is skipped, like any malformed line. ``since_id`` returns
    entries strictly AFTER the entry carrying that id (the relay-cursor
    read; unknown id → everything, the caller caps replay)."""
    if not OUTBOX_PATH.exists():
        return []
    entries: list[dict] = []
    with OUTBOX_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since_ts is not None and e.get("ts", 0) <= since_ts:
                continue
            if agent_id and e.get("agent_id") != agent_id:
                continue
            entries.append(e)
    if since_id:
        for i, e in enumerate(entries):
            if e.get("id") == since_id:
                entries = entries[i + 1 :]
                break
    return entries[-limit:]
