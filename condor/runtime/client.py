"""Transport-agnostic entry point to the session runtime.

Every caller outside ``condor/runtime/`` goes through this module — never
through ``condor.runtime.sessions`` — so that swapping the in-process registry
for an HTTP client is a config flip rather than a rewrite.

``CONDOR_RUNTIME_MODE`` selects the transport:

* ``local`` (default) — thin await-through to ``condor.runtime.sessions``.
* ``http`` — reserved for the runtime process split; not implemented yet.
"""

from __future__ import annotations

import logging
import os
from typing import AsyncIterator, Literal

from condor.runtime import conversations
from condor.runtime.events import RuntimeEvent
from condor.runtime.keys import SessionKey
from condor.runtime.models import PromptRequest, SessionInfo, SessionSpec
from condor.runtime.timeouts import TIMEOUTS

log = logging.getLogger(__name__)

# What a caller wants done when the session is already answering something.
#
# ``reject``  — do not opt in. The session's stuck-prompt deadline applies, so
#               a genuinely busy session fails fast, exactly as before.
# ``queue``   — wait for the turn ahead to finish, then run. The session lock is
#               FIFO, so several queued messages are answered in order.
# ``steer``   — stop the turn ahead first, then run. The session is long-lived,
#               so the new turn keeps everything the interrupted one had worked
#               out; this is "interrupt and redirect", not a restart.
BusyPolicy = Literal["reject", "queue", "steer"]

LOCAL = "local"
HTTP = "http"

MODE = os.environ.get("CONDOR_RUNTIME_MODE", LOCAL)


def _local():
    """Return the local registry module.

    Imported lazily: ``condor.runtime.sessions`` pulls in handler helpers that
    in turn import this module, so a module-level import would be circular.
    """
    if MODE != LOCAL:
        raise NotImplementedError(
            f"CONDOR_RUNTIME_MODE={MODE!r} is not implemented yet; only "
            f"{LOCAL!r} is supported."
        )
    from condor.runtime import sessions

    return sessions


async def create_session(
    spec: SessionSpec,
    *,
    permission_callback=None,
    user_data: dict | None = None,
) -> SessionInfo:
    """Provision (or reuse) the session described by ``spec``."""
    session = await _local().get_or_create_session(
        spec, permission_callback=permission_callback, user_data=user_data
    )
    return session.info()


async def get_info(key: SessionKey) -> SessionInfo | None:
    """Serializable view of a session, or None when it does not exist."""
    session = _local().get_session(key)
    return session.info() if session else None


async def list_sessions(user_id: int | None = None) -> list[SessionInfo]:
    """List sessions, optionally filtered by owning user."""
    return _local().list_sessions(user_id)


async def destroy(key: SessionKey) -> bool:
    """Destroy a session. Returns True if one existed."""
    return await _local().destroy_session(key)


async def destroy_all() -> None:
    """Destroy every session. Called on shutdown."""
    await _local().destroy_all_sessions()


def _deny_pending_confirmations(raw_key: str) -> None:
    """Resolve as denied whatever the interrupted turn was still asking about.

    Imported lazily for the same reason ``_local()`` is: the confirmation
    registry pulls in handler helpers through its permission callback.
    """
    from condor.runtime.confirmations import get_registry

    try:
        denied = get_registry().deny_pending_for_session(raw_key)
    except Exception:  # noqa: BLE001 - never let cleanup break the new turn
        log.warning("Could not deny pending confirmations for %s", raw_key)
        return
    if denied:
        log.info("Denied %d pending confirmation(s) on steering %s", denied, raw_key)


async def prompt(
    key: SessionKey,
    req: PromptRequest,
    *,
    on_busy: BusyPolicy = "reject",
) -> AsyncIterator[RuntimeEvent]:
    """Stream one user turn through the session as canonical RuntimeEvents.

    Both surfaces consume this, so neither can drift from the other's idea of
    what an event looks like. A mid-stream failure is surfaced as an ``ERROR``
    followed by a ``DONE`` — a consumer that stops only on ``DONE`` must never
    be left hanging by an exception.

    Every turn is recorded here, on the one funnel every surface goes through,
    so Telegram, the dashboard and MCP all get a durable transcript without any
    per-surface work.

    ``on_busy`` is what a message sent mid-answer does. It lives here rather
    than on each surface because this is the one funnel both go through, and
    because the surfaces genuinely differ: the dashboard has a live composer and
    a Stop button, so sending *steers*; Telegram has neither, so sending
    *queues*. It defaults to ``reject`` — the pre-FEAT-030 behaviour — so
    delegations, strategy ticks and every other caller are untouched.

    ``req.image_b64``/``req.image_mime`` are accepted but not yet forwarded:
    the underlying ACP and Pydantic AI clients are text-only today.
    """
    raw_key = str(key)
    session = _local().get_session(key)
    if session is None:
        yield RuntimeEvent.error(f"No session for {raw_key}", session_key=raw_key)
        yield RuntimeEvent.done("no_session", session_key=raw_key)
        return

    lock_timeout: float | None = None
    if on_busy != "reject":
        # A deliberate wait is not a stuck prompt, so it gets its own deadline.
        lock_timeout = TIMEOUTS.prompt_queue
        if session.is_busy:
            if on_busy == "steer":
                # Order matters: a turn parked on a confirmation nobody will
                # answer cannot notice a cancel, so the dialog goes first and
                # the abort lands on an agent that can act on it.
                _deny_pending_confirmations(raw_key)
                await abort(key)
            # Said before blocking, so a surface can show "waiting its turn"
            # rather than a composer that looks broken.
            yield RuntimeEvent.queued(session_key=raw_key)

    # Stamped from the live session, not from the conversation meta: the meta's
    # agent_key/agent_slug are last-write-wins, so a chat that switches models
    # mid-way would attribute its whole history to whatever answered last.
    recorder = conversations.Recorder(
        session.user_id,
        session.conversation_id,
        req.text,
        agent_key=session.agent_key,
        agent_slug=session.agent_slug,
    )
    try:
        async for event in session.prompt_stream(req.text, lock_timeout=lock_timeout):
            runtime_event = RuntimeEvent.from_acp(event, session_key=raw_key)
            recorder.observe(runtime_event)
            yield runtime_event
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as an event
        log.exception("Prompt failed for session %s", raw_key)
        failure = RuntimeEvent.error(str(exc), session_key=raw_key)
        recorder.observe(failure)
        yield failure
        yield RuntimeEvent.done("error", session_key=raw_key)
    finally:
        # Not on DONE: the dashboard abandons this generator constantly (page
        # reload, abort_prompt cancelling the task, WS disconnect), and an
        # abandoned async generator only ever gets GeneratorExit. Losing the
        # half-written reply is the bug this feature exists to fix.
        recorder.flush()


async def prompt_once(key: SessionKey, text: str) -> str:
    """Send a prompt and wait for the whole reply.

    For turns the user never sees streamed — injecting mode context, asking the
    agent to summarize itself for /compact. Raises KeyError when the session is
    gone, since every caller here has just checked that it exists.
    """
    session = _local().get_session(key)
    if session is None:
        raise KeyError(f"No session for {key}")
    return await session.client.prompt(text)


async def abort(key: SessionKey) -> bool:
    """Abort the in-flight prompt. Returns True if a session existed."""
    session = _local().get_session(key)
    if session is None:
        return False
    await session.abort()
    return True


# The FEAT-008 ``get_live()`` escape hatch is gone: prompting, context
# injection and liveness checks all go through the functions above, so no
# caller outside this package holds a live session object any more.
