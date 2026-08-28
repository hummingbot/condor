"""The automatic producer: share what is finished, for users who said Always.

Everything else in this package runs because somebody pressed a button. This
module is the one that does not, and that single difference is what the rules
below are for.

In FEAT-054 the scrubber is the second-to-last gate and a human reading the
dialog is the last one. Here **the scrubber is the last gate**, so every
compensating control has to come from somewhere else: from visibility while it
is happening (a chip in the chat header that cannot be dismissed) and from
revocation after (per-conversation Unshare, and a withdrawal that empties the
back catalogue). This file holds the third: a narrow definition of what the
sweep is allowed to take at all.

Five rules, each of which can refuse a conversation on its own:

**Forward-only.** Only conversations created after ``opted_in_at``. Turning a
setting on is consent to a policy from here, not a licence over the archive —
and a bulk upload of months of chats the user has not looked at in a year is
the reading of "share my conversations" nobody agreed to. An old chat can still
be handed over deliberately with the button, which is the right amount of
friction for that act.

**Idle.** Conversations never formally end — ``updated_at`` is stamped on every
merge — so "nothing has happened for :data:`IDLE_S`" is the only available
notion of "finished". It is a heuristic and it will sometimes take a
conversation the user was about to continue; ``revision`` makes that harmless,
because the next sweep supersedes the short transcript with the longer one.

**Grown.** ``turn_count`` past what was shared last time. Without it the sweep
would re-post an unchanged conversation on every tick and spend the rate limit
on nothing.

**Not excluded.** One click in the share dialog sets ``share_excluded``, and it
is honoured forever. Per-conversation *exclusion* rather than per-conversation
*confirmation*: a prompt on every chat would just be FEAT-054 with extra steps,
and it would train the user to click through it.

**Single-author.** A user can consent for themselves; they cannot consent for
the other people in the room. A turn records which brain answered but not which
human typed, so this is not something the transcript can be asked after the
fact — it is recorded at birth as ``multi_author``, and the surface allowlist
below is the second half of the same rule: a surface this module has not been
taught about is excluded rather than assumed solo.

The rate limit is a hard constraint, not a guideline. The collector's per-IP
bucket for this endpoint is 12/h (FEAT-054 §7), and it is per *install*, not
per user — so the budget is spent by the tick, oldest waiting first, and a
backlog drains steadily instead of bursting and failing the rest.
"""

from __future__ import annotations

import asyncio
import logging
import time

from condor.runtime import conversations
from condor.runtime.conversations import ConversationMeta
from condor.sharing import consent, share, wire

log = logging.getLogger(__name__)

# How long a conversation must have been untouched to count as finished.
IDLE_S = 30 * 60

# How often the job runs, and how many shares one run is allowed to queue.
#
# The pair is what matters, not either number: the collector allows
# ``COLLECTOR_HOURLY_ALLOWANCE`` requests an hour from one IP, and the sweep is
# not the only producer sharing that budget — a user pressing the button spends
# from the same bucket. ``PER_TICK`` is therefore set so that a *saturated*
# sweep still leaves room for the explicit path, and
# ``tests/test_sharing_sweep.py`` asserts the arithmetic rather than trusting
# that a later edit to one constant remembers the other.
SWEEP_INTERVAL_S = 15 * 60
PER_TICK = 3
COLLECTOR_HOURLY_ALLOWANCE = 12

# Surfaces whose conversations can be attributed to their owner.
#
# On ``web`` and ``mcp`` the session key's owner *is* the Condor user and the
# route authenticates them, so nobody else can speak into the transcript. On
# ``tg`` the key's owner is the chat, which is why a Telegram conversation also
# has to clear ``multi_author``. A conversation with an empty or unrecognised
# surface — one written before surfaces were recorded, or by a frontend added
# after this module — is refused: the failure this ordering avoids is a new
# surface silently inheriting permission to upload other people's words.
ATTRIBUTABLE_SURFACES = ("tg", "web", "mcp")

SWEEP_JOB = "sharing_sweep"


def attributable(meta: ConversationMeta) -> bool:
    """True when every turn in this conversation is the owner's to consent to."""
    return meta.surface in ATTRIBUTABLE_SURFACES and not meta.multi_author


def covered(meta: ConversationMeta, user_id: int | str) -> bool:
    """Would the sweep ever take this conversation?

    The standing rules only — consent, forward-only, exclusion, attribution —
    and deliberately not the timing ones. This is what the share dialog renders,
    and a chip that appeared only once a conversation went idle would show up
    thirty minutes after the moment the user needed to see it. "This will be
    shared" is true from the first turn; *when* is the sweep's business.
    """
    return (
        consent.can_sweep(user_id)
        and not meta.share_excluded
        and attributable(meta)
        and meta.created_at.timestamp() >= consent.opted_in_at(user_id)
    )


def eligible(user_id: int | str, now: float | None = None) -> list[ConversationMeta]:
    """This user's conversations the sweep may take right now, oldest first.

    Pure but for reading ``meta.json``: no network, no queue, no writes. Every
    rule in the module docstring is applied here and nowhere else, so each one
    can be tested on its own against a conversation that fails only that rule.
    """
    if not consent.can_sweep(user_id):
        return []

    now = time.time() if now is None else now
    since = consent.opted_in_at(user_id)
    if not since:
        # ``always`` without a timestamp is a config somebody hand-edited.
        # Forward-only cannot be enforced against nothing, so nothing is taken.
        log.warning(
            "User %s is at always with no opt-in time; sweeping nothing", user_id
        )
        return []

    ready: list[ConversationMeta] = []
    for meta in conversations.list_conversations(user_id, limit=0):
        if not covered(meta, user_id):
            continue
        if now - meta.updated_at.timestamp() <= IDLE_S:
            continue  # still being used
        if meta.turn_count <= meta.share_turn_count:
            continue  # never grown since it was last sent
        ready.append(meta)

    ready.sort(key=lambda m: m.updated_at)
    return ready


def _candidates(now: float) -> list[tuple[str, ConversationMeta]]:
    """Everything ready across every consenting user, oldest waiting first.

    Pooled and sorted together rather than swept user by user, so the oldest
    waiting conversation on the install goes first whoever it belongs to. One
    user with a large backlog therefore drains at the same rate as everyone else
    instead of monopolising the tick.
    """
    found: list[tuple[str, ConversationMeta]] = []
    for user_id in consent.users_sweeping():
        try:
            found.extend((user_id, meta) for meta in eligible(user_id, now))
        except Exception:  # noqa: BLE001 - one unreadable store is not the tick
            log.debug(
                "Could not list shareable conversations for %s", user_id, exc_info=True
            )
    found.sort(key=lambda pair: pair[1].updated_at)
    return found


async def sweep(now: float | None = None) -> int:
    """Share what is finished. Never raises; returns how many were queued.

    Runs on the job queue, so a failure here must not be able to take the bot
    down or stall the other jobs behind it — hence the blanket guards. A user
    whose store is unreadable is skipped and the rest of the tick continues.

    Both halves run in a worker thread, and that is a requirement rather than a
    tidiness preference: listing a store reads a ``meta.json`` per conversation
    and a submit scrubs a whole transcript, both of them blocking. This was once
    justified here by saying the explicit path can afford the same work inline
    because the user asked for it — which was wrong, and the routes now use
    ``asyncio.to_thread`` too. Only the *latency* is the asker's; the *blocking*
    is the whole install's, because uvicorn shares this loop (PERF-235).
    """
    if not consent.env_allows() or not consent.install_allows():
        return 0

    now = time.time() if now is None else now
    candidates = await asyncio.to_thread(_candidates, now)
    if not candidates:
        return 0

    queued = 0
    for user_id, meta in candidates[:PER_TICK]:
        try:
            await asyncio.to_thread(
                share.submit, int(user_id), meta.id, kind=wire.KIND_PASSIVE
            )
            queued += 1
        except Exception:  # noqa: BLE001 - one bad conversation is not the tick
            log.warning(
                "Could not share conversation %s automatically", meta.id, exc_info=True
            )

    if len(candidates) > PER_TICK:
        log.info(
            "Sweep queued %d of %d ready conversations; the rest wait for the next tick",
            queued,
            len(candidates),
        )
    return queued


def withdraw(user_id: int | str, state: str = consent.OFF) -> int:
    """Leave Always, and destroy what it queued but never sent. Returns how many.

    Destruction, not "decide not to send it": the rule
    ``condor/telemetry/consent.py`` set for a withdrawn consent, applied to the
    one thing this user's Always actually produced. Their deliberate shares and
    anybody's pending unshare are left alone — see
    :func:`condor.sharing.outbox.purge_user_shares` for why the scope is not the
    whole queue.

    What is already *on the collector* is not touched here. That is the second,
    separate button, because a user who shared 200 conversations may be turning
    off future sharing without wanting to withdraw the past — and doing it for
    them would be as presumptuous as not offering it at all.
    """
    from condor.sharing import outbox

    consent.set_user_state(user_id, state)
    return outbox.purge_user_shares(user_id, kind=wire.KIND_PASSIVE)


# ── The job ──────────────────────────────────────────────────────────────


async def _sweep_job(context) -> None:  # pragma: no cover - PTB plumbing
    try:
        await sweep()
    except Exception:  # noqa: BLE001 - a sweep must never take the bot down
        log.debug("Sharing sweep job failed", exc_info=True)


def register_jobs(application) -> None:
    """Register the sweep beside the delivery job, the house pattern.

    Registered unconditionally and free when nobody has opted in: the first
    thing :func:`sweep` does is read the stored answers, and on an install where
    every user is at the default it returns without touching a conversation.

    ``first`` is a long way out on purpose. A conversation has to be idle for
    :data:`IDLE_S` to qualify anyway, so there is nothing for a sweep at boot to
    find that a sweep a few minutes later will miss — and the first minutes
    after a restart are when the rest of the runtime is reconciling.
    """
    try:
        queue = getattr(application, "job_queue", None)
        if queue is None:
            return
        for job in queue.get_jobs_by_name(SWEEP_JOB):
            job.schedule_removal()
        queue.run_repeating(
            _sweep_job, interval=SWEEP_INTERVAL_S, first=300, name=SWEEP_JOB
        )
    except Exception:  # noqa: BLE001
        log.debug("Could not register the sharing sweep job", exc_info=True)
