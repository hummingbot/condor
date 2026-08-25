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
# quietly drops the oldest excess *share*. That is the intended behaviour, not a
# bug — same contract as telemetry's outbox, with a count low enough that the
# file stays small even though each record is a whole transcript. Queued
# unshares are exempt and uncapped: see :func:`_retain`.
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

    **The cap is scoped to shares.** It exists to bound the disk cost of queued
    *transcripts*, and an unshare is not one: it is a URL and a 64-character
    delete token, so thousands of them are a few hundred kB. Dropping one is not
    a delayed revocation, it is a destroyed capability — ``share.unshare``
    clears ``share_delete_token`` from the meta the moment it queues, so this
    file holds the only copy, and evicting it leaves the transcript on the
    collector with nothing on the box able to take it back. That is not
    hypothetical: ``unshare_all`` enqueues one record per shared conversation
    with no flush in between, so a user with sixty of them used to lose the
    first ten and be told it worked (CORR-234).

    So the count cap and the age cutoff apply to ``OP_SHARE`` records; anything
    else is kept. **Order is never disturbed** — the survivors are filtered out
    of ``records`` in place rather than partitioned and recombined, because a
    share and the unshare that revokes it swapping would be its own bug, and
    :func:`_rewrite` forbids a ``select`` that reorders.
    """
    cutoff = time.time() - MAX_QUEUE_AGE_S
    fresh = [
        i
        for i, record in enumerate(records)
        if record.get("op") == OP_SHARE
        and float(record.get("queued_at") or 0) >= cutoff
    ]
    kept = set(fresh[-MAX_QUEUED_SHARES:])
    return [
        record
        for i, record in enumerate(records)
        if record.get("op") != OP_SHARE or i in kept
    ]


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


def purge_shares() -> int:
    """Destroy every undelivered share. The off switch's counterpart.

    Shares only, never the pending unshares. A veto is the install being
    forbidden to *send* a conversation; a queued unshare is the install still
    owing the collector a *deletion* it already promised a user, and the delete
    token lives nowhere else once ``unshare`` has cleared it from the meta
    (CORR-232). Turning sharing off must not strand somebody with a transcript
    they can no longer take back — which is the same reason the unshare route is
    deliberately ungated.

    So this deletes records rather than the file: a bare ``unlink`` would take
    every revocation with it. Returns how many shares were dropped.
    """
    dropped = _rewrite(lambda records: [r for r in records if r.get("op") != OP_SHARE])
    if dropped:
        log.info("Dropped %d undelivered share(s) on veto", dropped)
    return dropped


def purge_user_shares(user_id: int | str, *, kind: str) -> int:
    """Destroy this user's undelivered shares of one ``kind``. Returns how many.

    The counterpart of ``condor/telemetry/consent.py``'s ``_purge_collected``:
    withdrawing consent destroys what was collected but unsent rather than
    merely deciding not to send it, so a later bug has nothing left to deliver.

    It is scoped to one user rather than the install-wide :func:`purge_shares`
    because this queue is not one user's. A single file holds every share on the
    install, and emptying it to honour one user turning Always off would throw
    away a conversation a second person deliberately pressed Share on. So a
    withdrawal takes what it is owed and no more: the passive shares this user
    never looked at, still sitting in the queue.
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
                if record.get("op") == OP_UNSHARE:
                    # A refused revocation is not a dropped share. The delete
                    # token lives nowhere else once ``unshare`` cleared it from
                    # the meta, so giving up here leaves the transcript on the
                    # collector with nothing able to ask again — an operator
                    # should see that, not find it in a debug log (CORR-234).
                    log.error(
                        "The collector refused an unshare of %s (%s); the "
                        "transcript stays there and the delete token is gone",
                        record.get("share_id") or "?",
                        response.status,
                    )
                else:
                    log.warning(
                        "The collector refused a share (%s); dropping it",
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


def _share_vetoed() -> bool:
    """May this install still send a *share* right now?

    Consulted per record on the send path, not only when the share was created:
    a queue survives restarts and lives for ``MAX_QUEUE_AGE_S``, so consent
    checked once at creation would let an operator export ``CONDOR_SHARING=off``
    and still watch every transcript queued before the switch was flipped go out
    (CORR-233). Re-read each time and never cached, for the reason
    :func:`condor.sharing.consent.env_allows` gives: the operator should not have
    to restart to be obeyed. Both reads are in-memory and neither blocks.

    Imported lazily to keep this module free of a cycle through ``consent``.
    """
    from condor.sharing import consent

    return not consent.env_allows() or not consent.install_allows()


async def flush() -> tuple[int, int]:
    """Try every queued request in order. Returns ``(delivered, still queued)``.

    Order is preserved and a failure does not skip ahead: a share and the
    unshare that revokes it must not be able to arrive out of order.

    Consent is a *send*-time gate as well as a creation-time one. A share the
    install is no longer allowed to make is dropped here rather than held: the
    kill switch says nothing on this box can share, and leaving it queued only
    postpones the leak to whenever the switch is flipped back. An unshare is
    delivered under both vetoes — an admin turning sharing off must not strand a
    user with a transcript they can no longer take back.

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
        vetoed: set[str] = set()
        stalled = False
        for record in records:
            if record.get("op") == OP_SHARE and _share_vetoed():
                # Checked ahead of the stall: a share this install is forbidden
                # to send should not survive because something ahead of it in
                # the queue could not reach the collector.
                vetoed.add(record["id"])
                continue
            if stalled:
                continue  # keep order once something has stalled
            if await post(record):
                delivered.add(record["id"])
            else:
                stalled = True

        if vetoed:
            log.info("Sharing is off; dropped %d queued share(s) unsent", len(vetoed))
        retired = delivered | vetoed
        _rewrite(lambda queued: [r for r in queued if r.get("id") not in retired])
        return len(delivered), len(_read())
    finally:
        _flushing = False
