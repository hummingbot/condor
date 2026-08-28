"""Server-initiated turns: a finished background task continues the chat.

Every turn in this codebase is consumed by the surface that asked for it — the
dashboard drives ``client.prompt()`` from its WebSocket handler, Telegram from
its message handler. Nothing was able to start a turn that *nobody typed*, so a
delegation's outcome could only ever be pushed at the user (a Telegram message,
a note in the transcript) and the agent that asked for the work never learned
the answer until a later session replayed the transcript.

This module is the missing driver. :func:`resume_session` prompts a live session
from the background and hands the resulting events to a **sink** resolved from
the key's surface, so each frontend renders a woken turn exactly the way it
renders a typed one. There is no new store and no new protocol: ``client.prompt``
records the turn itself, so the transcript is written whether or not anyone is
watching.

Shaped like :mod:`condor.runtime.confirmations`, and for the same reason: a
server-side event has to reach whichever surface happens to be attached, and the
entry must survive a channel that dies. Sinks are *registered* by the surfaces at
import time rather than imported from here — ``condor.runtime`` must not depend
on handler or web code (see ``client._local()``).

Two deliveries, one shape. :func:`resume_session` spends a model turn — the
agent continues from the result. :func:`deliver_note` spends nothing: it shows a
note the transcript already holds, which is all a finished routine needs (its
outcome is text, not work to continue from). Both degrade to silence when nobody
is attached, but they do not have the same precondition: a wake needs a live
subprocess to prompt, a note needs only an owner and a slot to address.

Deliberately narrow: a background producer passes its session key, the
conversation it belongs to, and the line to show. Do not generalise further.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Protocol

from condor.runtime.events import RuntimeEvent
from condor.runtime.keys import WEB, SessionKey
from condor.runtime.models import PromptRequest

log = logging.getLogger(__name__)


class TurnSink(Protocol):
    """Renders one server-initiated turn on a single surface.

    ``open`` runs before the prompt (a Telegram sink sends its placeholder
    there), ``on_event`` per streamed event, ``close`` once the turn ends —
    including when it ends by failing.
    """

    async def open(self) -> None: ...

    async def on_event(self, event: RuntimeEvent) -> None: ...

    async def close(self) -> None: ...


# surface -> factory(key, user_id) -> TurnSink | None. Returning None means "no
# client is attached to this surface right now", which is not a failure: the
# turn still runs and is still recorded.
SinkFactory = Callable[[SessionKey, "int | None"], "TurnSink | None"]

_sink_factories: dict[str, SinkFactory] = {}

# surface -> deliver(key, user_id, text, kind). A note is not a turn: nothing is
# prompted and nothing is recorded — the transcript already holds it — so a sink
# only has to render one line into whatever is attached.
NoteSink = Callable[[SessionKey, "int | None", str, str], Awaitable[None]]

_note_sinks: dict[str, NoteSink] = {}

# Conversations with a wake turn in flight, counted. Read by the delegate route
# to force a delegation started *from inside* a wake back to ``notify`` — which
# is what bounds the recursion to depth 1 without a rate limiter.
#
# Counted rather than a set because two delegations can finish onto the same
# conversation at once: the second wake queues behind the first on the session
# lock, so the first one's ``finally`` runs while the second is still to come.
# A set would drop the mark there, and the turn that queued behind it would be
# free to start another resuming delegation — exactly the depth-1 bound this
# exists to hold.
_in_flight: dict[str, int] = {}


# What the conversation that asked for background work gets when it ends. One
# vocabulary for both producers -- a delegation (``condor.agents.delegate``) and
# a routine run (``condor.routine_store``) -- because they are the same question
# asked of the same two deliveries below.
#
# ``notify`` -- the bell, the transcript note, and :func:`deliver_note` into
#              whatever surface is attached. The outcome is for the human to
#              read; the agent gets no turn.
# ``resume`` -- :func:`resume_session` instead, so the agent that asked is handed
#              the outcome and continues from it (FEAT-034).
ON_COMPLETE_NOTIFY = "notify"
ON_COMPLETE_RESUME = "resume"
ON_COMPLETE_CHOICES = (ON_COMPLETE_NOTIFY, ON_COMPLETE_RESUME)


def normalize_on_complete(value: str | None) -> str:
    """A caller's ``on_complete`` reduced to a value the producers can trust."""
    return value if value in ON_COMPLETE_CHOICES else ON_COMPLETE_NOTIFY


def register_sink_factory(surface: str, factory: SinkFactory) -> None:
    """Bind a surface's renderer. Called by the surface, at import time."""
    _sink_factories[surface] = factory


def register_note_sink(surface: str, sink: NoteSink) -> None:
    """Bind a surface's out-of-band note renderer. Called by the surface."""
    _note_sinks[surface] = sink


def is_waking(conversation_id: str) -> bool:
    """True while at least one wake turn is running on this conversation."""
    return bool(conversation_id) and _in_flight.get(conversation_id, 0) > 0


async def _guard(awaitable: Awaitable, what: str) -> bool:
    """Await a sink call; report failure instead of propagating it.

    A surface that blew up must not kill the turn — the transcript is the
    record, and the same discipline applies to a confirmation channel that
    fails to deliver.
    """
    try:
        await awaitable
        return True
    except Exception:  # noqa: BLE001 - a dead sink is not a dead turn
        log.warning("Wake sink failed to %s", what, exc_info=True)
        return False


def _build_sink(key: SessionKey, user_id: int | None) -> "TurnSink | None":
    factory = _sink_factories.get(key.surface)
    if factory is None:
        return None
    try:
        return factory(key, user_id)
    except Exception:  # noqa: BLE001 - same reason as _guard
        log.warning("Wake sink factory failed for %s", key, exc_info=True)
        return None


def _parse_key(session_key: str) -> "SessionKey | None":
    """The canonical key, or None when the caller's string is not one."""
    try:
        return SessionKey.parse(session_key)
    except ValueError:
        log.debug("Cannot reach %r: not a canonical session key", session_key)
        return None


async def _session_info(key: SessionKey):
    """The registry's view of a session, or None when there is no view.

    A lookup that blew up is "nothing registered": a background producer must
    not lose its delivery to a registry hiccup any more than to a closed tab.
    """
    from condor.runtime import client

    try:
        return await client.get_info(key)
    except Exception:  # noqa: BLE001 - a lookup failure is "nothing to reach"
        log.warning("Could not look up session %s", key, exc_info=True)
        return None


async def _live_session(session_key: str, conversation_id: str):
    """The session a background task can still *prompt*, or None.

    What "there is nobody to talk to" means for :func:`resume_session`: a
    malformed key, no session, a dead one, or a session that has since moved to
    another conversation. That last check is load-bearing on Telegram, where the
    key ``tg:{chat_id}`` is stable but the conversation behind it is not —
    without it a task started before ``/new`` would report into a chat about
    something else.

    Only the wake goes through here. A note is not a turn: it needs a socket,
    not a subprocess, so :func:`deliver_note` deliberately does not share this
    precondition (CORR-263).

    Returns ``(key, info)`` so the caller can address a sink by slot.
    """
    key = _parse_key(session_key)
    if key is None:
        return None

    info = await _session_info(key)

    if info is None or not info.alive:
        log.debug("Not reaching %s: no live session", key)
        return None
    if info.conversation_id != conversation_id:
        log.debug(
            "Not reaching %s: session has moved to conversation %s, task was from %s",
            key,
            info.conversation_id,
            conversation_id,
        )
        return None
    return key, info


async def deliver_note(
    *,
    session_key: str,
    conversation_id: str,
    text: str,
    kind: str,
    user_id: int | None = None,
) -> bool:
    """Show an already-recorded transcript note on the surface that is attached.

    The cheap half of :func:`resume_session`. A background producer that only
    writes a ``system`` turn is invisible to a dashboard that is already open —
    the transcript is read at load, so the user learns their routine failed
    thirty seconds in only by reloading the page. This pushes the same note,
    with no prompt behind it: a finished routine is worth *showing*, not worth
    paying for a model turn to announce.

    Addressed like the bell it fires next to, not like a wake (CORR-263): a note
    needs an owner and a slot, never a live subprocess. Requiring one dropped
    the note in every state where the tab is still on screen but the session
    behind it is gone — reaped on ``session_idle``, evicted by the per-user cap,
    or simply dead — while the bell for the same event arrived on the same
    socket. ``user_id`` is the caller's fallback for exactly those states, where
    there is no session left to read the owner off; the delegation knows it as
    ``dt.user_id`` and a routine run as its instance's owner.

    The conversation guard survives where it means something. On ``tg`` the key
    is the stable ``tg:{chat_id}`` and the conversation behind it moves, so a
    task started before ``/new`` must still stay quiet — that check needs the
    live session and keeps it. On ``web`` the slot *is* the conversation, so the
    same guard is an identity between the key and the caller's id, checked here
    without asking the registry anything.

    Returns False — never raises — when there is nobody to show it to. The note
    is in the transcript either way, so this degrades to exactly the passive
    behaviour it extends.
    """
    if not session_key or not conversation_id or not text:
        return False

    key = _parse_key(session_key)
    if key is None:
        return False

    info = await _session_info(key)
    owner = info.user_id if info is not None and info.user_id is not None else user_id
    if owner is None:
        log.debug("Not showing a note on %s: nobody to address it to", key)
        return False

    if key.surface == WEB:
        if key.slot != conversation_id:
            log.debug(
                "Not showing a note on %s: slot is not conversation %s",
                key,
                conversation_id,
            )
            return False
    elif info is None or not info.alive or info.conversation_id != conversation_id:
        log.debug(
            "Not showing a note on %s: no live session on conversation %s",
            key,
            conversation_id,
        )
        return False

    sink = _note_sinks.get(key.surface)
    if sink is None:
        return False
    return await _guard(sink(key, owner, text, kind), "deliver a note")


async def resume_session(
    *,
    session_key: str,
    conversation_id: str,
    text: str,
    kind: str,
) -> bool:
    """Drive one server-initiated turn into a live session.

    Returns False — never raises — when there was nothing to wake: a malformed
    key, no session, a dead one, or a session that has since moved to another
    conversation. That last check is load-bearing on Telegram, where the key
    ``tg:{chat_id}`` is stable but the conversation behind it is not: without it
    a task started before ``/new`` would wake a chat about something else.

    A session that cannot be woken is not an error. The outcome is already in
    the user's chat and in the transcript, and the next session replays it — so
    this degrades to exactly the passive behaviour it extends. It deliberately
    does **not** spawn a replacement session: paying for a subprocess to talk to
    nobody is the cost this must not incur.
    """
    from condor.runtime import client

    if not session_key or not conversation_id or not text:
        return False

    found = await _live_session(session_key, conversation_id)
    if found is None:
        return False
    key, info = found

    sink = _build_sink(key, info.user_id)
    if sink is not None and not await _guard(sink.open(), "open"):
        sink = None

    _in_flight[conversation_id] = _in_flight.get(conversation_id, 0) + 1
    try:
        # ``queue`` and never ``steer``: a wake must not take a slot away from
        # the human. A user message sent during a wake turn steers it aside and
        # the human wins, which is the correct precedence.
        stream = client.prompt(
            key, PromptRequest(text=text, user_kind=kind), on_busy="queue"
        )
        async for event in stream:
            if sink is not None and not await _guard(sink.on_event(event), "render"):
                sink = None
        return True
    finally:
        remaining = _in_flight.get(conversation_id, 0) - 1
        if remaining > 0:
            _in_flight[conversation_id] = remaining
        else:
            _in_flight.pop(conversation_id, None)
        if sink is not None:
            await _guard(sink.close(), "close")
