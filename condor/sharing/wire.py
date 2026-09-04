"""The wire format for one shared conversation, and the bound on its size.

``TurnEntry`` *is* the payload. The transcript on disk already has the shape a
corpus wants — role, text, thought, tool trajectory, stop reason, which brain
answered — so a share carries the scrubbed turns themselves rather than a
parallel extraction of them. Anything that can read a transcript can read a
share.

Two things in the envelope are worth reading twice.

**``delete_token_hash``.** The objective wants a corpus you can delete from, on
a server that must not know who you are. That is resolved with a capability, not
an account: the client mints a random ``delete_token``, sends only its SHA-256,
and keeps the token in the conversation's ``meta.json``. Unsharing posts the
token; the server hashes it and matches the row. Nobody is identified, and the
server holds no credential it could leak — the same shape as
``install_secret``, a local secret that only ever *proves* something.

**``revision``.** ``share_id`` is stable per conversation, so re-sharing a chat
that has grown is an upsert that bumps the revision rather than a second row.
The events pipeline gets the same idempotence from ``ON CONFLICT DO NOTHING``;
that will not do here, because a transcript legitimately changes.

``schema`` is an integer and the collector refuses an unknown one *whole*. A
client ahead of its server fails loudly rather than quietly dropping shares.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from collections.abc import Callable

from condor.runtime.conversations import TurnEntry

SCHEMA = 1
REDACTION_SCHEMA = 1

KIND_EXPLICIT = "explicit"
KIND_PASSIVE = "passive"  # FEAT-055's automatic producer

# The cap on a scrubbed transcript, in bytes of JSON. The collector's own body
# cap sits above it (2 MB there) so a share that is legal here is never refused
# there for size alone.
MAX_SHARE_BYTES = 1_500_000

# The turn left behind in place of the ones dropped for size. A real ``system``
# entry with its own kind, exactly like ``ARCHIVE_MARKER_KIND`` in
# ``condor.runtime.conversations``: every existing reader of a transcript
# already renders a system note, so nothing needs to learn a bespoke line format
# — and the payload never claims to be a whole conversation when it is not.
OMITTED_KIND = "share_omitted"


def new_share_id() -> str:
    return uuid.uuid4().hex


def new_delete_token() -> str:
    """A capability, not a password: 32 random bytes, used once per unshare."""
    return secrets.token_hex(32)


def token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _turn_bytes(turn: TurnEntry) -> int:
    return (
        len(
            json.dumps(turn.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
        )
        + 1
    )


def _size(turns: list[TurnEntry]) -> int:
    """The wire cost of a whole list. :func:`bound` no longer needs it — it
    accumulates the same total a turn at a time — but "how big is this
    transcript" is the question a size-cap test asks, so it stays."""
    return sum(_turn_bytes(t) for t in turns)


def _omitted_marker(count: int) -> TurnEntry:
    return TurnEntry(
        role="system",
        kind=OMITTED_KIND,
        text=f"({count} turns omitted: this share was over the size limit)",
    )


def _unscrubbed(turn: TurnEntry) -> TurnEntry:
    return turn


def bound(
    turns: list[TurnEntry],
    *,
    scrub_one: Callable[[TurnEntry], TurnEntry] | None = None,
) -> tuple[list[TurnEntry], bool]:
    """``(turns, truncated)`` — the transcript, trimmed from the middle.

    The middle is what goes, because the head and the tail are what a reader
    needs: how the conversation was opened and how it ended. The middle is what
    a reader skips anyway.

    Turns are taken alternately from the two ends until the budget is spent, so
    a share keeps a balanced number of turns from each end rather than a long
    opening and a single closing line. The marker's own cost is reserved up
    front, so the result is under the cap including it.

    ``scrub_one`` is how a caller pays for redaction only on what it sends. A
    share's archive can be many times what fits, and scrubbing a turn is ~50
    substring and regex passes over every string in it, so :func:`bound` calls
    ``scrub_one`` as it *admits* each turn rather than being handed a whole
    scrubbed transcript. It measures the scrubbed turn, because a pseudonym can
    be longer than the value it replaced and the raw turn is therefore not an
    upper bound on the wire cost. Left unset it is the identity, so a caller
    that hands over already-scrubbed turns — or raw ones it does not want
    redacted — gets the behaviour this function always had.
    """
    scrub = scrub_one or _unscrubbed
    if len(turns) <= 2:
        return [scrub(t) for t in turns], False

    budget = MAX_SHARE_BYTES - _turn_bytes(_omitted_marker(len(turns)))

    head: list[TurnEntry] = []
    tail: list[TurnEntry] = []
    low, high, used, from_head = 0, len(turns) - 1, 0, True
    held: TurnEntry | None = None
    index = 0
    while low <= high:
        index = low if from_head else high
        held = scrub(turns[index])
        size = _turn_bytes(held)
        if used + size > budget:
            break
        used += size
        if from_head:
            head.append(held)
            low += 1
        else:
            tail.insert(0, held)
            high -= 1
        held = None
        from_head = not from_head

    if low > high:  # the whole transcript fits, marker reservation included
        return head + tail, False

    # Spending the budget is not yet a reason to truncate: the budget holds back
    # the marker's own bytes, and a transcript that fits ``MAX_SHARE_BYTES``
    # whole is still sent whole — that is the ``dropped <= 0`` case this
    # function has always had. So walk what is left, and stop the moment it
    # cannot fit. The extra scrubbing that costs is bounded by the one turn that
    # overflowed plus the marker's reservation, not by the size of the archive.
    rest: list[TurnEntry] = []
    total = used
    for i in range(low, high + 1):
        entry = held if i == index else scrub(turns[i])
        total += _turn_bytes(entry)
        if total > MAX_SHARE_BYTES:
            dropped = len(turns) - len(head) - len(tail)
            return head + [_omitted_marker(dropped)] + tail, True
        rest.append(entry)
    return head + rest + tail, False


def envelope(
    *,
    share_install_id: str,
    share_id: str,
    delete_token: str,
    revision: int,
    turns: list[TurnEntry],
    counts: dict[str, int],
    truncated: bool,
    agent_slug: str = "",
    agent_key: str = "",
    surface: str = "",
    kind: str = KIND_EXPLICIT,
) -> dict:
    """One share, ready to POST.

    ``app`` comes from :func:`condor.telemetry.context.app` — the deployment
    block, not the taxonomy. "Which build produced this reasoning" is a real
    question to ask of a corpus, and that function already answers it without
    naming a host, a user or a path. Importing it is not a breach of the
    separation rule: the rule is about ``condor.telemetry.schema`` and the
    ``events`` table, neither of which this package touches.
    """
    from condor.telemetry import context

    app = context.app()
    return {
        "schema": SCHEMA,
        "share_install_id": share_install_id,
        "share_id": share_id,
        "delete_token_hash": token_hash(delete_token),
        "revision": int(revision),
        "kind": kind,
        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "app": {
            "version": app.get("version", "unknown"),
            "branch": app.get("branch", "unknown"),
            "os": app.get("os", "unknown"),
            "python": app.get("python", "unknown"),
        },
        "agent": {"slug": agent_slug, "key": agent_key},
        "surface": surface,
        "turns": [t.model_dump(mode="json") for t in turns],
        "redaction": {
            "schema": REDACTION_SCHEMA,
            "counts": dict(counts),
            "truncated": bool(truncated),
        },
    }


def unshare_body(delete_token: str) -> dict:
    return {"delete_token": delete_token}
