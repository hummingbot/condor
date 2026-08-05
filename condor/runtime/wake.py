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

Deliberately narrow. ``resume_session(session_key, conversation_id, text, kind)``
is everything a background producer needs; routines will call it unchanged once
they carry a session key (ARCH-089). Do not generalise it before then.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Protocol

from condor.runtime.events import RuntimeEvent
from condor.runtime.keys import SessionKey
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

# Conversations with a wake turn in flight. Read by the delegate route to force
# a delegation started *from inside* a wake back to ``notify`` — which is what
# bounds the recursion to depth 1 without a counter or a rate limiter.
_in_flight: set[str] = set()


def register_sink_factory(surface: str, factory: SinkFactory) -> None:
    """Bind a surface's renderer. Called by the surface, at import time."""
    _sink_factories[surface] = factory


def is_waking(conversation_id: str) -> bool:
    """True while a wake turn is running on this conversation."""
    return bool(conversation_id) and conversation_id in _in_flight


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

    try:
        key = SessionKey.parse(session_key)
    except ValueError:
        log.debug("Cannot wake %r: not a canonical session key", session_key)
        return False

    try:
        info = await client.get_info(key)
    except Exception:  # noqa: BLE001 - a lookup failure is "nothing to wake"
        log.warning("Could not look up session %s to wake it", key, exc_info=True)
        return False

    if info is None or not info.alive:
        log.debug("Not waking %s: no live session", key)
        return False
    if info.conversation_id != conversation_id:
        log.debug(
            "Not waking %s: session has moved to conversation %s, task was from %s",
            key,
            info.conversation_id,
            conversation_id,
        )
        return False

    sink = _build_sink(key, info.user_id)
    if sink is not None and not await _guard(sink.open(), "open"):
        sink = None

    _in_flight.add(conversation_id)
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
        _in_flight.discard(conversation_id)
        if sink is not None:
            await _guard(sink.close(), "close")
