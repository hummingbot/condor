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


def _size(turns: list[TurnEntry]) -> int:
    return sum(
        len(json.dumps(t.model_dump(mode="json"), ensure_ascii=False).encode("utf-8"))
        + 1
        for t in turns
    )


def _omitted_marker(count: int) -> TurnEntry:
    return TurnEntry(
        role="system",
        kind=OMITTED_KIND,
        text=f"({count} turns omitted: this share was over the size limit)",
    )


def _turn_bytes(turn: TurnEntry) -> int:
    return (
        len(
            json.dumps(turn.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
        )
        + 1
    )


def bound(turns: list[TurnEntry]) -> tuple[list[TurnEntry], bool]:
    """``(turns, truncated)`` — the transcript, trimmed from the middle.

    The middle is what goes, because the head and the tail are what a reader
    needs: how the conversation was opened and how it ended. The middle is what
    a reader skips anyway.

    Turns are taken alternately from the two ends until the budget is spent, so
    a share keeps a balanced number of turns from each end rather than a long
    opening and a single closing line. The marker's own cost is reserved up
    front, so the result is under the cap including it.
    """
    if len(turns) <= 2 or _size(turns) <= MAX_SHARE_BYTES:
        return list(turns), False

    budget = MAX_SHARE_BYTES - _turn_bytes(_omitted_marker(len(turns)))
    sizes = [_turn_bytes(t) for t in turns]

    head: list[TurnEntry] = []
    tail: list[TurnEntry] = []
    low, high, used, from_head = 0, len(turns) - 1, 0, True
    while low <= high:
        index = low if from_head else high
        if used + sizes[index] > budget:
            break
        used += sizes[index]
        if from_head:
            head.append(turns[index])
            low += 1
        else:
            tail.insert(0, turns[index])
            high -= 1
        from_head = not from_head

    dropped = len(turns) - len(head) - len(tail)
    if dropped <= 0:  # the marker's reservation was all that did not fit
        return list(turns), False
    return head + [_omitted_marker(dropped)] + tail, True


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
