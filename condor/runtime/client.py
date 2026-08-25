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
import time
from typing import AsyncIterator, Literal

from condor.acp.pydantic_ai_client import model_prefix
from condor.runtime import conversations, secrets
from condor.runtime.events import EventType, RuntimeEvent
from condor.runtime.keys import SessionKey
from condor.runtime.models import PromptRequest, SessionInfo, SessionSpec
from condor.runtime.timeouts import TIMEOUTS
from condor.telemetry import taps as telemetry_taps

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


def bound_agent_context(bound, user_id: int, platform: str) -> str:
    """Opening context for a chat bound to a specialist Agent."""
    return _local().bound_agent_context(bound, user_id, platform)


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


async def conversation_for_session(session_key: str) -> str:
    """Resolve a session key to the conversation currently on that session.

    The one place that knows how to turn a raw key into a conversation id, so
    every producer of async work that reports back to its origin — delegations,
    notifications, routine runs — agrees on what "the conversation behind this
    call" means.

    Answered on demand rather than cached at spawn because the conversation id
    does not exist when the MCP subprocess starts (``get_or_create_session``
    mints it after the client is up) — by call time it is settled.

    A missing, malformed or dead key is not an error: it means "no conversation
    behind this call", which is the truth for a consult-, tick- or
    scheduler-started run and for anything predating this provenance.
    """
    if not session_key:
        return ""
    try:
        info = await get_info(SessionKey.parse(session_key))
        return info.conversation_id if info else ""
    except Exception:
        log.debug("Could not resolve session key %r", session_key, exc_info=True)
        return ""


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


# Session surfaces are short codes; the telemetry taxonomy spells them out.
_SURFACES = {"tg": "telegram", "web": "web", "mcp": "mcp", "": "other"}


def _turn_kind(req: PromptRequest, key: SessionKey) -> str:
    """Which kind of turn this was, from what the request already carries."""
    kind = (getattr(req, "user_kind", "") or "").lower()
    if kind in ("consult", "delegate", "tick"):
        return kind
    return "chat"


def _model_of(agent_key: str) -> str:
    """The model id without any custom-endpoint nickname.

    ``custom@venice:llama-3.3-70b`` reports ``llama-3.3-70b``: the nickname is
    something the operator typed and may name their employer or their host.
    """
    return agent_key.split(":", 1)[1] if ":" in agent_key else agent_key


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

    # Key material never gets past this line (FEAT-056). It is the same
    # argument the funnel already makes for recording and for ``on_busy``,
    # applied to a third cross-cutting concern: one variable feeds both the
    # Recorder below and ``prompt_stream`` seven lines further down, so
    # sanitising it here covers Telegram, the dashboard, background wakes,
    # delegations and MCP at once — and covers surface number four, which
    # would otherwise inherit the omission. Only *certain* shapes are replaced;
    # ``findings`` carries the ambiguous ones too, for telemetry to count and
    # for the surfaces to warn about. See :mod:`condor.runtime.secrets`.
    clean, findings = secrets.redact(req.text)

    # Stamped from the live session, not from the conversation meta: the meta's
    # agent_key/agent_slug are last-write-wins, so a chat that switches models
    # mid-way would attribute its whole history to whatever answered last.
    recorder = conversations.Recorder(
        session.user_id,
        session.conversation_id,
        clean,
        agent_key=session.agent_key,
        agent_slug=session.agent_slug,
        # Empty for a turn the user typed; set when something else drove it
        # (a background task waking the chat), so the opening line is recorded
        # as a system note rather than as the user's words.
        user_kind=req.user_kind,
    )
    # Turn shape for telemetry (FEAT-023): counts and categories only. Nothing
    # about what was asked, answered, or which file a tool touched.
    started = time.monotonic()
    tool_kinds: dict[str, int] = {}
    outcome = "aborted"
    try:
        async for event in session.prompt_stream(clean, lock_timeout=lock_timeout):
            runtime_event = RuntimeEvent.from_acp(event, session_key=raw_key)
            if runtime_event.type is EventType.TOOL_CALL:
                # The ACP `kind` ("read", "execute", …), never the `title`: a
                # title is free text and routinely contains a file path.
                kind = str(runtime_event.field("kind") or "other")
                tool_kinds[kind] = tool_kinds.get(kind, 0) + 1
            elif runtime_event.type is EventType.DONE:
                outcome = "done"
            recorder.observe(runtime_event)
            yield runtime_event
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller as an event
        outcome = "error"
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
        # Here for the same reason: this is the one funnel Telegram, the
        # dashboard and MCP all cross, and it is the only place that sees an
        # abandoned turn end.
        telemetry_taps.agent_turn(
            kind=_turn_kind(req, key),
            provider=model_prefix(session.agent_key) or "acp",
            model=_model_of(session.agent_key),
            tool_calls=sum(tool_kinds.values()),
            duration_ms=int((time.monotonic() - started) * 1000),
            outcome=outcome,
            surface=_SURFACES.get(getattr(key, "surface", ""), "other"),
            tools=tool_kinds,
            # Counts per kind, never an offset and never a value — the same
            # "counts and categories only" contract the rest of this call
            # already holds to.
            secrets=secrets.counts(findings),
        )


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
