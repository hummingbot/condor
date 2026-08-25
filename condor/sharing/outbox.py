"""The durable queue for shares, and the send path.

Modelled on ``condor/telemetry/outbox.py`` — append-only JSONL under the one
runtime root, capped by count and by age, trimmed with
``condor.fsutil.atomic_write_text`` — and different from it in the two ways the
payload demands.

**One share per request, never a batch.** A batch of transcripts has no
consumer: the collector stores a conversation as a row, and there is no
aggregate over five of them that anybody wants. Telemetry batches because an
envelope of 500 counters is cheaper than 500 envelopes; a transcript is not a
counter.

**The queue holds whole requests, not events.** Each line is
``{"op": "share"|"unshare", "url": …, "body": …, "queued_at": …}``, so a retry
re-posts exactly what failed — including an unshare, which is the one operation
that must survive a restart to be worth promising. A user who pressed Unshare
and then lost the network has still revoked; the revocation is in this file
with its delete token, and the next flush completes it.

The collector address is compiled in, like telemetry's, and no environment
variable redirects it. Whether anything is sent at all is decided by
:mod:`condor.sharing.consent` and by the user pressing a button.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from condor.fsutil import atomic_write_text

log = logging.getLogger(__name__)

COLLECTOR_URL = "https://telemetry.hummingbot.org/v1/conversations"
POST_TIMEOUT_S = 10

# An install that never reaches a collector accumulates a bounded file and then
# quietly drops the oldest excess. That is the intended behaviour, not a bug —
# same contract as telemetry's outbox, with a count low enough that the file
# stays small even though each record is a whole transcript.
MAX_QUEUED_SHARES = 50
MAX_QUEUE_AGE_S = 14 * 24 * 3600

OP_SHARE = "share"
OP_UNSHARE = "unshare"


def root() -> Path:
    """Where the queue lives. One runtime root, resolved in ``condor.paths``.

    ``state_dir`` rather than a sibling of ``telemetry/``: the two pipelines
    share no file, and a directory listing should say so.
    """
    from condor import paths

    return paths.state_dir("sharing")


def queue_path() -> Path:
    return root() / "queue.jsonl"


def endpoint() -> str:
    return COLLECTOR_URL


def unshare_endpoint(share_id: str) -> str:
    return f"{COLLECTOR_URL}/{share_id}/delete"


def _read() -> list[dict]:
    path = queue_path()
    if not path.is_file():
        return []
    records: list[dict] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue  # a torn last line from a killed process
    except OSError:
        log.debug("Sharing could not read %s", path, exc_info=True)
    return records


def _write(records: list[dict]) -> None:
    path = queue_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path,
            "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records),
        )
    except OSError:
        log.warning("Sharing could not write its queue", exc_info=True)


def enqueue(op: str, url: str, body: dict, *, share_id: str = "") -> dict:
    """Park one request until a flush delivers it. Returns the queued record."""
    record = {
        "op": op,
        "url": url,
        "share_id": share_id,
        "body": body,
        "queued_at": time.time(),
    }
    path = queue_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        log.warning("Sharing could not queue a %s", op, exc_info=True)
        return record
    trim()
    return record


def pending() -> list[dict]:
    return _read()


def trim() -> None:
    """Enforce the cap, oldest first."""
    records = _read()
    cutoff = time.time() - MAX_QUEUE_AGE_S
    kept = [r for r in records if float(r.get("queued_at") or 0) >= cutoff]
    kept = kept[-MAX_QUEUED_SHARES:]
    if len(kept) != len(records):
        _write(kept)


def purge() -> None:
    """Delete everything queued. The off switch's counterpart."""
    try:
        queue_path().unlink()
    except OSError:
        pass


async def post(record: dict) -> bool:
    """Deliver one queued request.

    A 4xx other than 429 is *terminal*: the collector refused this share's shape
    and re-posting it forever would only keep a permanently-rejected record at
    the head of the queue. It is reported as delivered so the queue drains, and
    the refusal is logged. 5xx and transport failures stay queued.
    """
    url = record.get("url") or ""
    if not url:
        return True
    try:
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=POST_TIMEOUT_S)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=record.get("body") or {}) as response:
                if 200 <= response.status < 300:
                    return True
                if response.status == 429 or response.status >= 500:
                    return False
                log.warning(
                    "The collector refused a %s share (%s); dropping it",
                    record.get("op"),
                    response.status,
                )
                return True
    except Exception:
        log.debug("Sharing POST failed; the record stays queued", exc_info=True)
        return False


async def flush() -> tuple[int, int]:
    """Try every queued request in order. Returns ``(delivered, still queued)``.

    Order is preserved and a failure does not skip ahead: a share and the
    unshare that revokes it must not be able to arrive out of order.
    """
    records = _read()
    if not records:
        return 0, 0

    delivered = 0
    remaining: list[dict] = []
    for record in records:
        if remaining:
            remaining.append(record)  # keep order once something has stalled
            continue
        if await post(record):
            delivered += 1
        else:
            remaining.append(record)
    _write(remaining)
    return delivered, len(remaining)
