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
``{"op": "share"|"unshare", "url": …, "body": …, "queued_at": …}`` plus the
local-only ``user_id``/``kind`` bookkeeping a scoped withdrawal needs, none of
which is in ``body`` and none of which is posted. A retry
re-posts exactly what failed — including an unshare, which is the one operation
that must survive a restart to be worth promising. A user who pressed Unshare
and then lost the network has still revoked; the revocation is in this file
with its delete token, and the next flush completes it.

**The queue has two writers, so it is never rewritten from a snapshot.** The
sweep appends from a worker thread (``asyncio.to_thread``) while the web routes
append from the event loop, and a flush of a full queue can spend minutes in
``await post(...)``. So every record carries a local-only ``id``, every
read-modify-write goes through :func:`_rewrite` under one lock, and removals are
by identity: whatever arrived while a POST was in flight is still queued when it
lands. Nothing is posted with the lock held (CORR-232).

The collector address is compiled in, like telemetry's, and no environment
variable redirects it. Whether anything is sent at all is decided by
:mod:`condor.sharing.consent` and by the user pressing a button.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

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

# Every read-modify-write of the queue file runs under this lock. "Read it,
# decide, write it back" is not safe on its own here: there are two genuine
# producers — the sweep's worker thread and the web routes on the event loop —
# and ``atomic_write_text``'s rename would erase whatever either of them
# appended in between.
#
# Reentrant because ``enqueue`` appends and then trims, and both take it. It is
# **never held across an ``await``**: a POST can take ``POST_TIMEOUT_S``, and
# blocking every producer for that long would be its own bug.
_QUEUE_LOCK = threading.RLock()

# Set while a flush is posting. The flush job fires every 300s, and a full queue
# of ``POST_TIMEOUT_S`` timeouts can outlast that, so two flushes can overlap.
# The second stands down rather than re-posting records the first still owns —
# a share sent twice is a duplicate row, a revocation sent twice is a 4xx that
# ``post`` treats as terminal.
_flushing = False


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


def _rewrite(select: Callable[[list[dict]], list[dict]]) -> int:
    """Remove records from the queue. The one guarded read-modify-write.

    Every path that drops records comes through here — :func:`trim`, the scoped
    withdrawal, and the flush retiring what it delivered. It re-reads the file
    *under the lock*, so anything appended since the caller last looked is in
    the list ``select`` sees and stays in the file; and it rewrites only when
    something was actually dropped, so a no-op costs no rename.

    ``select`` picks the survivors out of that fresh list. It must only drop
    records and must never reorder them: a share and the unshare that revokes it
    must not be able to swap. It runs with the lock held, so it must not block
    and must not await.

    Returns how many records were dropped.
    """
    with _QUEUE_LOCK:
        records = _read()
        kept = select(records)
        dropped = len(records) - len(kept)
        if dropped:
            _write(kept)
        return dropped


def _retain(records: list[dict]) -> list[dict]:
    """The retention policy: which queued records survive the cap, oldest first.

    Split out of :func:`trim` so *what survives* is one function to read and to
    change, separate from the locking that makes changing it safe.
    """
    cutoff = time.time() - MAX_QUEUE_AGE_S
    kept = [r for r in records if float(r.get("queued_at") or 0) >= cutoff]
    return kept[-MAX_QUEUED_SHARES:]


def enqueue(
    op: str,
    url: str,
    body: dict,
    *,
    share_id: str = "",
    user_id: int | str = "",
    kind: str = "",
) -> dict:
    """Park one request until a flush delivers it. Returns the queued record.

    ``user_id`` and ``kind`` are **bookkeeping, not payload**: only ``body`` is
    posted, and neither of them is in it. They exist so
    :func:`purge_user_shares` can find exactly the records a withdrawal is
    entitled to destroy — this user's, produced without them looking — and leave
    everything else alone (FEAT-055).

    ``id`` is local-only in the same way. It is how :func:`flush` retires
    exactly the records it delivered instead of rewriting the file from the list
    it read before the first POST (CORR-232).
    """
    record = {
        "id": uuid4().hex,
        "op": op,
        "url": url,
        "share_id": share_id,
        "user_id": str(user_id or ""),
        "kind": kind,
        "body": body,
        "queued_at": time.time(),
    }
    path = queue_path()
    with _QUEUE_LOCK:
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
    """Enforce the cap, oldest first. See :func:`_retain` for the policy."""
    _rewrite(_retain)


def purge() -> None:
    """Delete everything queued. The off switch's counterpart."""
    with _QUEUE_LOCK:
        try:
            queue_path().unlink()
        except OSError:
            pass


def purge_user_shares(user_id: int | str, *, kind: str) -> int:
    """Destroy this user's undelivered shares of one ``kind``. Returns how many.

    The counterpart of ``condor/telemetry/consent.py``'s ``_purge_collected``:
    withdrawing consent destroys what was collected but unsent rather than
    merely deciding not to send it, so a later bug has nothing left to deliver.

    It is scoped rather than a bare :func:`purge` because this queue is not one
    user's. A single file holds every share on the install **and every pending
    unshare** — the record that carries a delete token for something already
    revoked. Emptying it to honour one user turning Always off would silently
    un-revoke somebody else's withdrawal and throw away a conversation a third
    person deliberately pressed Share on. So a withdrawal takes what it is owed:
    the passive shares this user never looked at, still sitting in the queue.
    """
    wanted = str(user_id)

    def _keep(records: list[dict]) -> list[dict]:
        return [
            r
            for r in records
            if not (
                r.get("op") == OP_SHARE
                and str(r.get("user_id") or "") == wanted
                and str(r.get("kind") or "") == kind
            )
        ]

    dropped = _rewrite(_keep)
    if dropped:
        log.info("Dropped %d undelivered %s share(s) on withdrawal", dropped, kind)
    return dropped


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


def _ensure_ids() -> list[dict]:
    """Read the queue, stamping an ``id`` on anything queued before ids existed.

    The queue survives restarts and lives for up to ``MAX_QUEUE_AGE_S``, so an
    upgrade can find records written by a version that removed them by rewriting
    a snapshot. They are given an identity here, once, so :func:`flush` can
    retire them the same way as everything else rather than guessing by
    position.
    """
    with _QUEUE_LOCK:
        records = _read()
        anonymous = [r for r in records if not r.get("id")]
        if anonymous:
            for record in anonymous:
                record["id"] = uuid4().hex
            _write(records)
        return records


async def flush() -> tuple[int, int]:
    """Try every queued request in order. Returns ``(delivered, still queued)``.

    Order is preserved and a failure does not skip ahead: a share and the
    unshare that revokes it must not be able to arrive out of order.

    Delivery is by identity, not by snapshot. Posting the whole queue can take
    ``MAX_QUEUED_SHARES`` × ``POST_TIMEOUT_S``, and the sweep and the web routes
    keep appending throughout, so the file is not rewritten from the list read
    before the first POST — the delivered ids are collected and handed to
    :func:`_rewrite`, which re-reads under the lock. Whatever arrived meanwhile
    is still queued afterwards, in order. Losing one of those would usually cost
    a share; once, it cost the only copy of a delete token (CORR-232).
    """
    global _flushing
    with _QUEUE_LOCK:
        if _flushing:
            # A flush already in flight owns these records; posting them from
            # here too would duplicate the share, or the revocation.
            return 0, len(_read())
        _flushing = True
    try:
        records = _ensure_ids()
        if not records:
            return 0, 0

        delivered: set[str] = set()
        stalled = False
        for record in records:
            if stalled:
                continue  # keep order once something has stalled
            if await post(record):
                delivered.add(record["id"])
            else:
                stalled = True

        _rewrite(lambda queued: [r for r in queued if r.get("id") not in delivered])
        return len(delivered), len(_read())
    finally:
        _flushing = False
