"""Generic notification relay — deliver Condor's outbox into ANY harness's
conversation via that harness's own send primitive.

The output-path counterpart to the MCP input path: "no harness is
privileged" applied to notifications. Condor's own Telegram bot is just
one sink; this relay lets Hermes, OpenClaw, or anything with a
send-a-message command receive the same notifications IN the conversation
the user drives Condor from — no Condor core changes, it only tails the
outbox (store/notifications.jsonl, written by condor/notifications.py).

Configure with an argv TEMPLATE (JSON list); placeholders are substituted
as whole argv elements — never shell-interpolated, so a notification's
text can't inject arguments. Placeholders: {text} {agent_id} {kind} {ts}.

    # OpenClaw (delivers into the user's OpenClaw Telegram conversation)
    export CONDOR_NOTIFY_CMD='["openclaw","message","send",
        "--channel","telegram","--target","<CHAT>","--text","{text}"]'

    # Hermes (POST to its API-server ingress, same chat)
    export CONDOR_NOTIFY_CMD='["curl","-sS","-X","POST",
        "https://localhost:8000/message",
        "-H","Content-Type: application/json",
        "-d","{json}"]'   # {json} = the whole entry as JSON

    python -m integrations.notify_relay.relay

The target conversation is whatever the command addresses — set it to the
one you use with that harness, and the notification lands there.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s relay %(message)s")
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUTBOX = REPO_ROOT / "store" / "notifications.jsonl"
POLL_S = float(os.environ.get("CONDOR_NOTIFY_POLL_S", "2"))
STATE_PATH = Path(
    os.environ.get("CONDOR_NOTIFY_STATE", REPO_ROOT / "store" / ".notify-relay-cursor")
)


def _read_cursor(state_path: Path) -> str:
    """Last delivered notification id (§4.1) — "" when fresh/unreadable."""
    try:
        data = json.loads(state_path.read_text())
        return str(data.get("last_id", ""))
    except Exception:  # noqa: BLE001 — a missing/corrupt cursor means fresh
        return ""


def _persist_cursor(state_path: Path, last_id: str) -> None:
    """Atomic tmp+rename, written only AFTER delivery: a crash in between
    re-delivers that entry (same id — receivers dedup by it), never loses it."""
    try:
        tmp = state_path.with_name(f"{state_path.name}.tmp{os.getpid()}")
        tmp.write_text(json.dumps({"last_id": last_id}))
        tmp.replace(state_path)
    except Exception:  # noqa: BLE001
        log.warning("could not persist relay cursor", exc_info=True)


def _render(template: list[str], entry: dict) -> list[str]:
    """Substitute placeholders as whole argv elements (no shell, no injection)."""
    subs = {
        "{text}": str(entry.get("text", "")),
        "{agent_id}": str(entry.get("agent_id", "")),
        "{kind}": str(entry.get("kind", "")),
        "{ts}": str(entry.get("ts", "")),
        "{json}": json.dumps(entry, ensure_ascii=False),
    }
    return [subs.get(arg, arg) for arg in template]


def _deliver(template: list[str], entry: dict) -> bool:
    argv = _render(template, entry)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except Exception as e:  # noqa: BLE001 — a bad send must not kill the relay
        log.warning("delivery raised: %s", e)
        return False
    if proc.returncode != 0:
        log.warning("delivery exit %s: %s", proc.returncode, (proc.stderr or "").strip()[:200])
        return False
    return True


def _read_entries(outbox: Path) -> tuple[list[dict], int]:
    """All good entries + the current end offset. A torn final line (crash
    mid-append) is skipped and picked up whole on the next poll."""
    entries: list[dict] = []
    size = outbox.stat().st_size
    with outbox.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.endswith("\n"):
                break  # torn tail
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries, size


def _start_index(entries: list[dict], last_id: str, fallback: int) -> int:
    """First undelivered index: strictly after ``last_id`` when it is found,
    else the in-memory fallback (id-less/legacy entries)."""
    if last_id:
        for i, e in enumerate(entries):
            if str(e.get("id", "")) == last_id:
                return i + 1
    return fallback


async def run(
    template: list[str],
    outbox: Path = OUTBOX,
    once: bool = False,
    state_path: Path | None = None,
) -> None:
    """Tail the outbox by DELIVERED ID (§4.1): the cursor is the last
    delivered entry's id, persisted atomically after each delivery — a crash
    between delivery and cursor advance re-delivers that one entry with the
    same id (receivers dedup by it); nothing is silently lost. On a fresh
    start (no cursor) history is skipped — it stays available via
    get_notifications."""
    state_path = state_path or STATE_PATH
    last_id = _read_cursor(state_path)
    processed = 0
    offset = -1
    if outbox.exists():
        entries, offset = _read_entries(outbox)
        if last_id:
            processed = _start_index(entries, last_id, 0)
            for entry in entries[processed:]:
                ok = _deliver(template, entry)
                log.info("delivered=%s (catch-up) agent=%s: %s", ok,
                         entry.get("agent_id"), str(entry.get("text", ""))[:60])
                eid = str(entry.get("id", ""))
                if eid:
                    last_id = eid
                    _persist_cursor(state_path, last_id)
            processed = len(entries)
        else:
            # Fresh start: anchor at the current tail, skip history.
            processed = len(entries)
            for e in reversed(entries):
                if e.get("id"):
                    last_id = str(e["id"])
                    _persist_cursor(state_path, last_id)
                    break
    log.info("relay started; tailing %s -> %s", outbox, template[0])
    while True:
        if outbox.exists():
            size = outbox.stat().st_size
            if size != offset:
                entries, offset = _read_entries(outbox)
                start = _start_index(entries, last_id, min(processed, len(entries)))
                for entry in entries[start:]:
                    ok = _deliver(template, entry)
                    log.info("delivered=%s agent=%s: %s", ok, entry.get("agent_id"),
                             str(entry.get("text", ""))[:60])
                    eid = str(entry.get("id", ""))
                    if eid:
                        last_id = eid
                        _persist_cursor(state_path, last_id)
                processed = len(entries)
        if once:
            return
        await asyncio.sleep(POLL_S)


def _load_template() -> list[str]:
    raw = os.environ.get("CONDOR_NOTIFY_CMD", "").strip()
    if not raw:
        log.error("CONDOR_NOTIFY_CMD not set — a JSON argv template is required")
        sys.exit(2)
    try:
        template = json.loads(raw)
    except json.JSONDecodeError as e:
        log.error("CONDOR_NOTIFY_CMD is not valid JSON: %s", e)
        sys.exit(2)
    if not isinstance(template, list) or not template or not all(isinstance(a, str) for a in template):
        log.error("CONDOR_NOTIFY_CMD must be a non-empty JSON list of strings")
        sys.exit(2)
    return template


if __name__ == "__main__":
    asyncio.run(run(_load_template()))
