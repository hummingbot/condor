"""Preview, submit, unshare — the three verbs a conversation share has.

:func:`preview` and :func:`submit` run the *same* build. That is not a tidiness
argument: the acceptance criterion is that pressing Share sends exactly the
bytes the dialog displayed, and the only way to promise that is for the dialog
and the sender to call one function. :func:`_build` is that function; neither
verb has a scrubbing path of its own.

Nothing here decides *whether* to share. That is
:mod:`condor.sharing.consent`'s job, checked at the HTTP boundary where the
caller's identity is known, and the answer in this feature is always "because a
human pressed the button".
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from condor.runtime import conversations
from condor.runtime.conversations import TurnEntry
from condor.sharing import consent, outbox, scrub, wire

log = logging.getLogger(__name__)


class ConversationMissing(LookupError):
    """No such conversation for this owner."""


class ScrubbedShare(BaseModel):
    """Exactly what would be sent, plus what is already on the server.

    Rendered by the dialog and — with the same turns, from the same call —
    posted by :func:`submit`.
    """

    conversation_id: str
    title: str = ""
    surface: str = ""
    agent_slug: str = ""
    agent_key: str = ""
    turns: list[TurnEntry] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    truncated: bool = False
    turns_omitted: int = 0
    revision: int = 0
    shared: bool = False
    share_id: str = ""
    shared_at: datetime | None = None


class ShareReceipt(BaseModel):
    """What a submitted share left behind on the conversation.

    ``delete_token`` is deliberately absent: it is the capability that revokes
    the share and it belongs on this box, in ``meta.json``, never in an HTTP
    response. :func:`unshare` reads it from there.
    """

    conversation_id: str
    share_id: str
    revision: int
    shared_at: datetime
    queued: bool = True


def _meta(user_id: int, conv_id: str):
    meta = conversations.get_conversation(user_id, conv_id)
    if meta is None:
        raise ConversationMissing(f"No conversation {conv_id}")
    return meta


def _build(user_id: int, conv_id: str) -> tuple[ScrubbedShare, object]:
    """Scrub and bound one conversation. The single source of the payload.

    The whole record is read — live transcript *and* archive — because a share
    is an export, which is the case ``include_archive`` exists for. What the
    size cap drops is then a decision this function makes and states, rather
    than one the retention policy already made silently.

    Minting the identity here rather than in :func:`submit` is what makes the
    preview honest: the pseudonyms in the dialog are salted with the same
    ``share_secret`` the sent payload will use, so the user is looking at the
    real bytes and not a rehearsal of them.
    """
    meta = _meta(user_id, conv_id)
    consent.ensure_identity()

    turns = conversations.read_transcript(
        user_id, conv_id, limit=0, include_archive=True
    )
    scrubbed, counts = scrub.scrub(
        turns, secret=consent.share_secret(), user_id=user_id
    )
    bounded, truncated = wire.bound(scrubbed)

    return (
        ScrubbedShare(
            conversation_id=conv_id,
            title=meta.title,
            surface=meta.surface,
            agent_slug=meta.agent_slug,
            agent_key=meta.agent_key,
            turns=bounded,
            counts=counts,
            truncated=truncated,
            turns_omitted=max(
                0, len(scrubbed) - len(bounded) + (1 if truncated else 0)
            ),
            revision=meta.share_revision,
            shared=bool(meta.share_id),
            share_id=meta.share_id,
            shared_at=meta.shared_at,
        ),
        meta,
    )


def preview(user_id: int, conv_id: str) -> ScrubbedShare:
    """The redacted transcript as it would be sent. Sends nothing."""
    return _build(user_id, conv_id)[0]


def submit(
    user_id: int, conv_id: str, *, kind: str = wire.KIND_EXPLICIT
) -> ShareReceipt:
    """Queue this conversation for delivery, and record the receipt locally.

    ``share_id`` and ``delete_token`` are minted once per conversation and
    reused: re-sharing a chat that has grown is an upsert with a higher
    revision, not a second row, and the token that revokes it does not change
    underneath the user.

    ``kind`` says who decided. The sweep passes ``passive``; everything else is
    ``explicit``. It is the one thing the two producers do differently, and it
    is a parameter rather than a second function on purpose — the acceptance
    criterion that the automatic path sends exactly what the button would send
    only holds while there is one build and one send (FEAT-055).
    """
    share, meta = _build(user_id, conv_id)

    share_id = meta.share_id or wire.new_share_id()
    delete_token = meta.share_delete_token or wire.new_delete_token()
    revision = meta.share_revision + 1

    envelope = wire.envelope(
        share_install_id=consent.share_install_id(),
        share_id=share_id,
        delete_token=delete_token,
        revision=revision,
        turns=share.turns,
        counts=share.counts,
        truncated=share.truncated,
        agent_slug=meta.agent_slug,
        agent_key=meta.agent_key,
        surface=meta.surface,
        kind=kind,
    )
    outbox.enqueue(
        outbox.OP_SHARE,
        outbox.endpoint(),
        envelope,
        share_id=share_id,
        user_id=user_id,
        kind=kind,
    )

    shared_at = datetime.now(timezone.utc)
    conversations.update_meta(
        user_id,
        conv_id,
        share_id=share_id,
        share_revision=revision,
        share_delete_token=delete_token,
        shared_at=shared_at.isoformat(),
        # What "it has grown since" is measured against. Stamped from the meta
        # read at the top of this function rather than re-read, so a turn landing
        # mid-share is counted as growth by the next sweep instead of being
        # silently folded into this revision and never sent.
        share_turn_count=meta.turn_count,
    )
    log.info(
        "Queued conversation %s as %s share %s r%d", conv_id, kind, share_id, revision
    )
    return ShareReceipt(
        conversation_id=conv_id,
        share_id=share_id,
        revision=revision,
        shared_at=shared_at,
    )


def unshare(user_id: int, conv_id: str) -> bool:
    """Revoke a share. False when there was nothing shared to revoke.

    The revocation is queued, not fired and forgotten: a user who pressed
    Unshare and then lost the network has still revoked, and the record in the
    queue carries the delete token needed to finish the job after a restart.

    The local receipt is cleared straight away. Keeping it until delivery would
    mean the UI shows "shared" for a conversation the user has already taken
    back, and the queue — not the meta — is what actually owes the server a
    request.

    Revoking also **excludes** the conversation (CORR-231). Clearing the receipt
    alone is not a revocation for a user at ``always``: it resets
    ``share_turn_count`` to zero, which is precisely the state the sweep's
    growth gate reads as "never sent", so the next tick would re-upload the same
    transcript under a fresh ``share_id`` half an hour later. ``share_excluded``
    is the one flag the sweep honours forever, and it is inert for users at
    ``off`` or ``explicit`` — nothing was going to be taken from them anyway. The
    way back in is the header chip's *Include it*, which is a deliberate act
    rather than an automatic one.
    """
    meta = _meta(user_id, conv_id)
    if not meta.share_id or not meta.share_delete_token:
        return False

    outbox.enqueue(
        outbox.OP_UNSHARE,
        outbox.unshare_endpoint(meta.share_id),
        wire.unshare_body(meta.share_delete_token),
        share_id=meta.share_id,
        user_id=user_id,
        kind=outbox.OP_UNSHARE,
    )
    conversations.update_meta(
        user_id,
        conv_id,
        share_id="",
        share_revision=0,
        share_delete_token="",
        shared_at=None,
        share_turn_count=0,
        share_excluded=True,
    )
    log.info("Queued an unshare for conversation %s", conv_id)
    return True


def unshare_all(user_id: int) -> int:
    """Take back everything this user has out there. Returns how many.

    The back catalogue, as one button. It is offered rather than performed when
    a user turns Always off, because withdrawing consent to *future* sharing and
    withdrawing the conversations already given are two different decisions —
    somebody who deliberately pressed Share two hundred times has not asked for
    those to disappear (FEAT-055).

    Each conversation goes through :func:`unshare`, so every revocation is
    queued with its own delete token and survives a restart exactly like a
    single one does. A conversation that fails is logged and the rest continue:
    a partial withdrawal is strictly better than an aborted one.

    Each one is also excluded from the sweep, per :func:`unshare`. For a user
    still at ``always`` that means the whole back catalogue stops being swept,
    permanently and with no bulk way back — they just asked for all of it
    deleted, so re-uploading any of it on the next tick would be the wrong
    reading. Settings says so on the button, and an individual chat can be
    re-included from its header chip.
    """
    removed = 0
    for meta in conversations.list_conversations(user_id, limit=0):
        if not meta.share_id:
            continue
        try:
            if unshare(user_id, meta.id):
                removed += 1
        except Exception:  # noqa: BLE001 - one failure must not strand the rest
            log.warning("Could not unshare conversation %s", meta.id, exc_info=True)
    log.info("Queued %d unshare(s) for user %s", removed, user_id)
    return removed


def list_shares(user_id: int) -> list[dict]:
    """Every conversation of this user's that is currently shared.

    What Settings → Privacy lists, so the user can see what left and take any
    of it back without hunting through the rail for it.
    """
    out = []
    for meta in conversations.list_conversations(user_id, limit=0):
        if not meta.share_id:
            continue
        out.append(
            {
                "conversation_id": meta.id,
                "title": meta.title,
                "share_id": meta.share_id,
                "revision": meta.share_revision,
                "shared_at": meta.shared_at.isoformat() if meta.shared_at else None,
                "turn_count": meta.turn_count,
            }
        )
    return out


# ── Delivery ─────────────────────────────────────────────────────────────


async def flush(reason: str = "job") -> tuple[int, int]:
    """Deliver whatever is queued. Safe to call when nothing is."""
    delivered, remaining = await outbox.flush()
    if delivered or remaining:
        log.debug(
            "Sharing flush (%s): %d delivered, %d still queued",
            reason,
            delivered,
            remaining,
        )
    return delivered, remaining


FLUSH_JOB = "sharing_flush"
FLUSH_INTERVAL_S = 300


async def _flush_job(context) -> None:  # pragma: no cover - PTB plumbing
    try:
        await flush("job")
    except Exception:  # noqa: BLE001 - a flush must never take the bot down
        log.debug("Sharing flush job failed", exc_info=True)


def register_jobs(application) -> None:
    """Register the delivery job beside telemetry's, the house pattern.

    Registered unconditionally and cheap when idle: the queue is empty on an
    install that has never shared, so the job returns without touching the
    network. That also means a share submitted minutes after boot is delivered
    without a restart.
    """
    try:
        queue = getattr(application, "job_queue", None)
        if queue is None:
            return
        for job in queue.get_jobs_by_name(FLUSH_JOB):
            job.schedule_removal()
        queue.run_repeating(
            _flush_job, interval=FLUSH_INTERVAL_S, first=90, name=FLUSH_JOB
        )
    except Exception:  # noqa: BLE001
        log.debug("Could not register the sharing flush job", exc_info=True)
