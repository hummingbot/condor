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


async def run(template: list[str], outbox: Path = OUTBOX, once: bool = False) -> None:
    # Tail from EOF: history is available via get_notifications / the outbox
    offset = outbox.stat().st_size if outbox.exists() else 0
    partial = ""
    log.info("relay started; tailing %s -> %s", outbox, template[0])
    while True:
        if outbox.exists():
            size = outbox.stat().st_size
            if size < offset:  # truncated/rotated
                offset, partial = 0, ""
            if size > offset:
                with outbox.open("r", encoding="utf-8") as f:
                    f.seek(offset)
                    chunk = f.read()
                    offset = f.tell()
                partial += chunk
                lines = partial.split("\n")
                partial = lines.pop()
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ok = _deliver(template, entry)
                    log.info("delivered=%s agent=%s: %s", ok, entry.get("agent_id"),
                             str(entry.get("text", ""))[:60])
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
