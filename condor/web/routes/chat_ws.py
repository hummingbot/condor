"""Chat WebSocket endpoint for the AI assistant.

Dedicated WS at /ws/chat (separate from the channel-based /ws).
Manages multiple agent sessions per user and streams ACPEvents as JSON.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from condor.llm.openrouter_models import fetch_models
from condor.llm.options import DEFAULT_AGENT
from condor.notifications import Notification, register_push_sink
from condor.runtime import WEB, EventType, PromptRequest, SessionKey, SessionSpec
from condor.runtime import client as runtime
from condor.runtime import conversations, secrets
from condor.runtime.binding import remember_model_choice
from condor.runtime.confirmations import (
    PendingConfirmation,
    build_permission_callback,
    get_registry,
)
from condor.runtime.events import RuntimeEvent
from condor.runtime.timeouts import TIMEOUTS
from condor.runtime.wake import register_note_sink, register_sink_factory
from condor.web.auth import decode_jwt, extract_ws_token, get_current_user
from condor.web.models import WebUser

log = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# Track active prompt tasks per slot: "user_id:slot_id" -> asyncio.Task.
# Connection-scoped, not session state, so this stays local to the WS layer.
_active_prompt_tasks: dict[str, asyncio.Task] = {}

# Spawns still in flight, keyed like _active_prompt_tasks. Subprocess start plus
# the ACP handshake takes seconds, and the dashboard now lets the user type
# through it: a message that arrives mid-spawn waits here instead of reading the
# not-yet-registered session as "ended".
_pending_spawns: dict[str, asyncio.Task] = {}

# One gate per slot, keyed like _active_prompt_tasks. Every WS action is
# fire-and-forget, so two `send_message` frames for the same conversation run
# concurrently: without this, #2 registers itself in _active_prompt_tasks while
# #1 is still being cancelled, a later Stop cancels the wrong task and #1's is
# never reaped. Held only across *taking over the slot* — deciding to steer,
# stopping the turn ahead, claiming the task entry — never across the answer
# itself, which would turn steering into queueing.
_slot_gates: dict[str, asyncio.Lock] = {}

# Hard cap on the page-context block a frame may carry (FEAT-059). Load-bearing,
# not defensive: this is a client-supplied string that is prepended to the
# prompt, so an unbounded one is an unbounded turn. The frontend renders at
# most 1200 chars; the slack covers a client a version ahead.
VIEW_CONTEXT_MAX_CHARS = 1500

# Chat sockets currently attached, per user. One socket carries every
# conversation a user has open, so this is all the addressing a server-initiated
# turn needs: the slot travels in the frame like it does for a typed turn.
#
# The only reason it exists: a turn nobody typed (FEAT-034) has no request to
# answer into, so it cannot reach a client the way every other event here does.
# A user with no tab open simply has no entry — the turn still runs and is still
# recorded, and the dashboard picks it up from the transcript on reconnect.
_attached_sockets: dict[int, set[WebSocket]] = {}


def _session_key(user_id: int, slot_id: str) -> SessionKey:
    """Build the canonical runtime key for one web chat slot."""
    return SessionKey.web(user_id, slot_id)


def _slot_gate(task_key: str) -> asyncio.Lock:
    """The gate serialising takeovers of one slot, created on first use."""
    gate = _slot_gates.get(task_key)
    if gate is None:
        gate = asyncio.Lock()
        _slot_gates[task_key] = gate
    return gate


async def _await_spawn(user_id: int, slot_id: str) -> None:
    """Block until a spawn for this slot finishes, if one is running.

    Shielded so a timeout here abandons the *wait*, never the spawn — killing
    the half-started subprocess would turn a slow start into a dead session.
    """
    task = _pending_spawns.get(f"{user_id}:{slot_id}")
    if task is None or task.done():
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=TIMEOUTS.prompt_lock)
    except Exception:  # noqa: BLE001 - a spawn that failed already told the client
        # Timed out or raised: the liveness checks below decide what happens
        # next, and the spawn reported its own error over the same socket.
        pass


async def _get_user_sessions(user_id: int) -> list[dict]:
    """This user's web slots, as the frontend expects them.

    Derived from the runtime rather than from local slot bookkeeping, so a
    session survives a WebSocket reconnect and a session killed elsewhere
    (Telegram, the REST API) disappears here without any cross-talk.

    The roster describes the user's conversations on this surface, not the
    subprocesses behind them (CORR-265): a slot the runtime reaped while the
    socket was down — the idle detach, an eviction, a subprocess that died — is
    still listed, with ``alive`` false, so its tab and its messages survive the
    reconnect and the next message reattaches it. A slot the runtime has no
    memory of is the one the user really did end, and it is still absent.
    """
    return [
        {
            "slot_id": info.slot,
            "conversation_id": info.conversation_id,
            "agent_key": info.agent_key,
            "is_busy": info.is_busy,
            "server_name": info.server_name,
            # ...and whether it is the chat's to change. A pinned server is
            # the Agent's decision, so the chip locks instead of offering a
            # picker that could not take effect.
            "server_pinned": info.server_pinned,
            # Who is answering, so the header can name a bound Agent rather
            # than the model it happens to run on.
            "agent_slug": info.agent_slug,
            "label": info.label,
            # Recency, so a reconnecting client can land on the conversation
            # its user was last in instead of on whatever this list yields
            # first. Null until the session has been prompted once.
            "last_prompt_at": (
                info.last_prompt_at.isoformat() if info.last_prompt_at else None
            ),
            # Whether a subprocess is behind the slot right now. Added, never
            # substituted: every key above keeps its name and its meaning, so a
            # bundle shipped before this one reads the roster exactly as it did
            # and simply does not know the difference.
            "alive": info.alive,
        }
        for info in await runtime.list_sessions(user_id, include_detached=True)
        if info.surface == WEB
    ]


def _to_ws_message(event: RuntimeEvent, slot_id: str) -> dict | None:
    """Render a RuntimeEvent in this endpoint's wire format.

    These shapes are a live contract with the shipped dashboard
    (``frontend/src/lib/api.ts``): this refactor re-plumbs the inside of the
    endpoint, not its outside, so the keys here must not change. Returns None
    for events the WS protocol carries out-of-band (permission requests go
    through the confirmation channel).
    """
    if event.type == EventType.TEXT:
        return {"event": "text_chunk", "slot_id": slot_id, "text": event.text}
    if event.type == EventType.THOUGHT:
        return {"event": "thought_chunk", "slot_id": slot_id, "text": event.text}
    if event.type == EventType.TOOL_CALL:
        return {
            "event": "tool_call",
            "slot_id": slot_id,
            "tool_call_id": event.field("tool_call_id"),
            "title": event.field("title"),
            "status": event.field("status"),
        }
    if event.type == EventType.TOOL_UPDATE:
        return {
            "event": "tool_call_update",
            "slot_id": slot_id,
            "tool_call_id": event.field("tool_call_id"),
            "status": event.field("status"),
        }
    if event.type == EventType.HEARTBEAT:
        return {
            "event": "heartbeat",
            "slot_id": slot_id,
            "elapsed_seconds": event.field("elapsed_seconds"),
        }
    if event.type == EventType.QUEUED:
        return {"event": "queued", "slot_id": slot_id}
    if event.type == EventType.RELOADED:
        # The session behind this slot was rebuilt to pick up a configuration
        # change (FEAT-093). Part *names* only — the values behind them carry
        # the MCP servers' env. The dashboard composes the sentence; the same
        # one is already in the transcript, so a page reload agrees.
        return {
            "event": "reload",
            "slot_id": slot_id,
            "parts": event.field("parts", []),
        }
    if event.type == EventType.DONE:
        return {
            "event": "prompt_done",
            "slot_id": slot_id,
            "stop_reason": event.stop_reason,
        }
    if event.type == EventType.ERROR:
        return {
            "event": "error",
            "slot_id": slot_id,
            "message": event.field("message", "Stream error"),
        }
    return None


def _slot_of(session_key: str) -> str:
    """The slot a registry entry belongs to, or "" if the key is not canonical.

    Never raises: a confirmation that cannot be attributed is still worth
    delivering unaddressed, which is what the dashboard did for all of them
    before this became a field.
    """
    try:
        return SessionKey.parse(session_key).slot
    except ValueError:
        return ""


async def _send(ws: WebSocket, event: dict) -> None:
    """Send a JSON event to the client, ignoring closed connections."""
    try:
        await ws.send_text(json.dumps(event))
    except Exception:
        pass


class WebSocketChannel:
    """Renders a pending confirmation as a `permission_request` event.

    The event shape is unchanged for the shipped dashboard; ``request_id`` is
    now the registry's id rather than a locally-minted one, which is what lets
    the same request also be answered from Telegram or over HTTP after a page
    reload kills this socket.
    """

    def __init__(self, ws: WebSocket):
        self._ws = ws

    async def deliver(self, pending: PendingConfirmation) -> None:
        await _send(
            self._ws,
            {
                "event": "permission_request",
                # Addressed like every other chat event (CORR-101). One socket
                # carries every conversation this user has open, so without the
                # slot the dashboard can only render the approval in whichever
                # one is on screen — and a click meant for one agent, on one
                # trading server, authorizes a live tool call in another.
                "slot_id": _slot_of(pending.session_key),
                "request_id": pending.id,
                "summary": pending.summary,
                # Which agent, on which server, is asking. The slot addresses
                # the request; this says out loud what the user is authorizing.
                "origin": pending.origin,
            },
        )


class _WakeSink:
    """Streams a server-initiated turn into this user's open dashboard tabs.

    Reuses ``_to_ws_message`` verbatim, so a woken turn is indistinguishable on
    the wire from one the user typed and the shipped dashboard renders it with
    no protocol change. Broadcast to every socket the user has open for the same
    reason a typed turn's events are addressed by ``slot_id``: which tab is
    "the" one is not knowable here, and the client already routes by slot.
    """

    def __init__(self, user_id: int, slot_id: str):
        self._user_id = user_id
        self._slot_id = slot_id

    async def open(self) -> None:
        return None

    async def on_event(self, event: RuntimeEvent) -> None:
        message = _to_ws_message(event, self._slot_id)
        if not message:
            return
        for ws in list(_attached_sockets.get(self._user_id, ())):
            await _send(ws, message)

    async def close(self) -> None:
        return None


def _wake_sink(key: SessionKey, user_id: int | None) -> _WakeSink | None:
    """Resolve a renderer for a woken web turn, or None if nobody is watching."""
    if user_id is None or not _attached_sockets.get(user_id):
        return None
    return _WakeSink(user_id, key.slot)


async def _deliver_note(
    key: SessionKey, user_id: int | None, text: str, kind: str
) -> None:
    """Show an out-of-band transcript note in this user's open tabs.

    A background producer that only records a ``system`` turn — a finished
    routine, a delegation's outcome — is invisible to a tab that is already
    open, because the transcript is read at load. This is the push that closes
    that gap; the event carries the same ``role``/``kind`` pair the hydrated
    turn does, so a later reload agrees with what was shown live.

    Broadcast to every socket the user has open, addressed by ``slot_id`` like
    every other chat event (CORR-101): which tab is "the" one is not knowable
    here, and the client already routes by slot.
    """
    if user_id is None:
        return
    message = {
        "event": "system_note",
        "slot_id": key.slot,
        "text": text,
        "kind": kind,
    }
    for ws in list(_attached_sockets.get(user_id, ())):
        await _send(ws, message)


async def _push_notification(notification: Notification) -> None:
    """Light this user's bell in every tab they have open (FEAT-048).

    A third out-of-band push on this socket, next to ``permission_request`` and
    ``system_note``. Unlike those two it is addressed to the *user*, not to a
    conversation — a finished background task has no slot — so the frame
    carries no ``slot_id`` and the store, not this push, is what guarantees the
    notice is seen: a user with no tab open simply has no entry here, and the
    bell picks it up from ``GET /notifications`` on the next load.
    """
    for ws in list(_attached_sockets.get(notification.user_id, ())):
        await _send(
            ws,
            {
                "event": "notification",
                "id": notification.id,
                "kind": notification.kind,
                "title": notification.title,
                "text": notification.text,
                "link": notification.link,
                "ts": notification.ts,
            },
        )


# Registered here rather than imported by the runtime: ``condor.runtime`` must
# not depend on web or handler code (see ``client._local()``). ``condor.notifications``
# is registered the same way and for the same reason.
register_sink_factory(WEB, _wake_sink)
register_note_sink(WEB, _deliver_note)
register_push_sink(_push_notification)


@router.websocket("/ws/chat")
async def chat_websocket(ws: WebSocket, token: str | None = Query(default=None)):
    """Chat WebSocket endpoint.

    Authenticates via the JWT passed in the ``Sec-WebSocket-Protocol``
    subprotocol header (preferred), falling back to the deprecated ``?token=``
    query param for older clients / live sessions.
    """
    auth_token, accept_subprotocol = extract_ws_token(ws, token)
    payload = decode_jwt(auth_token) if auth_token else None
    if not payload:
        await ws.close(code=4001, reason="Invalid token")
        return

    from config_manager import UserRole, get_config_manager

    user_id = int(payload["sub"])
    cm = get_config_manager()
    role = cm.get_user_role(user_id)
    if role not in (UserRole.USER, UserRole.ADMIN):
        await ws.close(code=4003, reason="Forbidden")
        return

    await ws.accept(subprotocol=accept_subprotocol)

    # Reachable from now until this socket goes away, so a turn started by
    # something other than this connection can still be rendered on it.
    _attached_sockets.setdefault(user_id, set()).add(ws)

    # Send list of existing alive sessions on connect
    sessions = await _get_user_sessions(user_id)
    await _send(ws, {"event": "sessions_list", "sessions": sessions})

    # Background tasks so long-running operations don't block the receive loop
    bg_tasks: set[asyncio.Task] = set()

    def _spawn(coro):
        task = asyncio.create_task(coro)
        bg_tasks.add(task)
        task.add_done_callback(bg_tasks.discard)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send(ws, {"event": "error", "message": "Invalid JSON"})
                continue

            action = msg.get("action")

            if action == "start_session":
                _spawn(_handle_start_session(ws, user_id, msg))
            elif action == "resume_conversation":
                _spawn(_handle_resume_conversation(ws, user_id, msg))
            elif action == "send_message":
                _spawn(_handle_send_message(ws, user_id, msg))
            elif action == "destroy_session":
                _spawn(_handle_destroy_session(ws, user_id, msg))
            elif action == "list_sessions":
                sessions = await _get_user_sessions(user_id)
                await _send(ws, {"event": "sessions_list", "sessions": sessions})
            elif action == "resolve_permission":
                await _handle_resolve_permission(user_id, msg)
            elif action == "abort_prompt":
                _spawn(_handle_abort_prompt(ws, user_id, msg))
            else:
                await _send(
                    ws, {"event": "error", "message": f"Unknown action: {action}"}
                )

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Chat WS error for user %d", user_id)
    finally:
        attached = _attached_sockets.get(user_id)
        if attached is not None:
            attached.discard(ws)
            if not attached:
                # Dropped rather than left empty: an entry that outlives the
                # last tab would read as "someone is watching" forever.
                _attached_sockets.pop(user_id, None)
        # Cancel any in-flight background tasks on disconnect
        for task in bg_tasks:
            task.cancel()
        if bg_tasks:
            await asyncio.gather(*bg_tasks, return_exceptions=True)


async def _handle_start_session(
    ws: WebSocket,
    user_id: int,
    msg: dict,
) -> None:
    """Open a chat on a brand new conversation."""
    server_name = msg.get("server_name")  # From frontend's selected server
    # A dashboard chat can be born already bound to a domain Agent. Without
    # this, "Chat" on an agent's page would have to start-then-switch, which
    # spawns two subprocesses for one click.
    agent_slug = str(msg.get("agent_slug") or "")
    # A bound Agent brings its own model, so an omitted (or empty) key means
    # "ask whoever is bound" — the semantics binding.resolve already has for an
    # empty spec.agent_key. Only an unbound chat needs Condor's default named,
    # which makes a non-empty key here a *deliberate* pick, worth remembering.
    picked = str(msg.get("agent_key") or "")
    agent_key = picked or ("" if agent_slug else DEFAULT_AGENT)

    # The conversation is minted first and *is* the slot, so the key
    # web:{user}:{conversation} is stable forever instead of being a throwaway
    # uuid that dies with the subprocess.
    conv = conversations.new_conversation(
        user_id,
        WEB,
        agent_key=agent_key,
        agent_slug=agent_slug,
        server_name=server_name,
    )
    await _start(
        ws,
        user_id,
        conv.id,
        agent_key,
        server_name,
        restored=False,
        agent_slug=agent_slug,
        client_ref=str(msg.get("client_ref") or ""),
    )
    remember_model_choice(user_id, agent_slug, picked)


async def _handle_resume_conversation(
    ws: WebSocket,
    user_id: int,
    msg: dict,
) -> None:
    """Reattach a session to a conversation that already exists.

    The transcript is replayed into the new session's opening context by the
    runtime, so the agent picks up where the last one left off — a fresh brain
    that has read the chat, which is the same trade a model switch already
    makes today.
    """
    conversation_id = str(msg.get("conversation_id") or "")
    conv = _conversation_for(user_id, conversation_id)
    if conv is None:
        await _send(ws, {"event": "error", "message": "No such conversation"})
        return

    picked = str(msg.get("agent_key") or "")
    agent_key = _resume_agent_key(conv, picked)

    await _start(
        ws,
        user_id,
        conv.id,
        agent_key,
        msg.get("server_name") or conv.server_name,
        restored=True,
        agent_slug=conv.agent_slug,
        client_ref=str(msg.get("client_ref") or ""),
    )
    remember_model_choice(user_id, conv.agent_slug, picked)


def _conversation_for(
    user_id: int, conversation_id: str
) -> conversations.ConversationMeta | None:
    """This user's conversation record, or None — an unusable id included.

    An id that is not even a safe path is "no such conversation" to every
    caller here, so the validation error is folded into the same answer.
    """
    try:
        return conversations.get_conversation(user_id, conversation_id)
    except conversations.ConversationIdError:
        return None


def _resume_agent_key(conv: conversations.ConversationMeta, picked: str) -> str:
    """Which model answers when a conversation is picked back up.

    A bound conversation resumes on its Agent's *current* model, not on whatever
    answered last: ``conv.agent_key`` is a record of what answered, never a pin,
    and honouring it here is what let a reload re-override the Agent with
    DEFAULT_AGENT. ``picked`` is a deliberate choice by the user and always wins.
    """
    if conv.agent_slug:
        return picked
    return picked or conv.agent_key or DEFAULT_AGENT


async def _start(
    ws: WebSocket,
    user_id: int,
    conversation_id: str,
    agent_key: str,
    server_name: str | None,
    *,
    restored: bool,
    agent_slug: str = "",
    client_ref: str = "",
) -> bool:
    """Spawn the session behind a conversation and announce it.

    Shared by ``start_session`` and ``resume_conversation``: the two differ only
    in whether the conversation already had turns, and the runtime decides that
    by looking at the transcript, not at which action was called.

    ``client_ref`` is echoed back untouched. The dashboard renders a tab the
    instant the user asks for one, before any id exists, and uses the echo to
    reconcile that optimistic tab with the conversation it turned out to be.
    """
    # Registered before the first await, so a send_message dispatched in the
    # same batch of WS frames finds the spawn and waits for it. The task here is
    # the handler's own — awaiting it means awaiting the whole announce.
    #
    # Which is why the reattach in ``_handle_send_message`` calls ``_spawn``
    # directly instead: registering *that* task would make a second message
    # wait for the whole answer, not for the spawn, and steering would turn
    # back into queueing. It holds the slot gate throughout, which is the
    # serialisation that path actually needs.
    task_key = f"{user_id}:{conversation_id}"
    current = asyncio.current_task()
    if current is not None:
        _pending_spawns[task_key] = current
    try:
        return await _spawn(
            ws,
            user_id,
            conversation_id,
            agent_key,
            server_name,
            restored=restored,
            agent_slug=agent_slug,
            client_ref=client_ref,
        )
    finally:
        _pending_spawns.pop(task_key, None)


async def _spawn(
    ws: WebSocket,
    user_id: int,
    conversation_id: str,
    agent_key: str,
    server_name: str | None,
    *,
    restored: bool,
    agent_slug: str = "",
    client_ref: str = "",
) -> bool:
    """Create the subprocess for one conversation and announce it.

    Returns whether the session is up; a failure has already told the client,
    so a caller that wanted to continue only needs to stop.
    """
    # The per-user session cap now lives in the runtime, so Telegram and the
    # dashboard draw on one budget; exceeding it raises out of create_session.
    slot_id = conversation_id
    session_key = _session_key(user_id, slot_id)

    perm_cb = build_permission_callback(
        session_key=str(session_key),
        user_id=user_id,
        channels=[WebSocketChannel(ws)],
    )

    # Hydrate the user's stored preferences so web sessions resolve the same
    # things a Telegram session would — notably the saved custom endpoint
    # behind a "custom@<endpoint>:<model>" agent key.
    from condor.preferences import load_user_data_for

    try:
        info = await runtime.create_session(
            SessionSpec(
                key=str(session_key),
                agent_key=agent_key,
                user_id=user_id,
                platform="web",
                lazy_context=True,  # Don't block — inject context on first message
                server_name=server_name,
                agent_slug=agent_slug,
                conversation_id=conversation_id,
            ),
            permission_callback=perm_cb,
            user_data=load_user_data_for(user_id),
        )

        await _send(
            ws,
            {
                "event": "session_started",
                # The wire still calls it slot_id — that is the shipped
                # dashboard's contract. It now *is* the conversation id, sent
                # alongside under its real name.
                "slot_id": slot_id,
                "conversation_id": conversation_id,
                "agent_key": info.agent_key,
                "server_name": info.server_name,
                "server_pinned": info.server_pinned,
                "restored": restored,
                "agent_slug": info.agent_slug,
                "label": info.label,
                "client_ref": client_ref,
            },
        )
        return True
    except Exception as e:
        log.exception("Failed to start chat session for user %d", user_id)
        await _send(
            ws,
            {
                "event": "error",
                "message": f"Failed to start session: {e}",
                # Named so the client can drop the optimistic tab it opened
                # rather than leaving one that will never have a session.
                "client_ref": client_ref,
                "slot_id": slot_id,
            },
        )
        return False


# ── Pasted key material (FEAT-056) ───────────────────────────────────────
#
# Safety is the funnel's: ``runtime.prompt`` has already replaced the certain
# shapes by the time anything below runs. This surface cannot delete what the
# user typed the way Telegram can, so all it does is say what happened, and say
# it at most once per conversation per kind — a web slot id *is* its
# conversation id, so that bound is exactly the one the Telegram side keeps in
# ``chat_data``.
_secret_notices_sent: dict[str, set[str]] = {}


async def _notify_secret_shapes(
    ws: WebSocket, user_id: int, slot_id: str, text: str
) -> None:
    """Emit ``secret_notice`` for each key shape this message carried."""
    findings = secrets.scan(text)
    if not findings:
        return

    told = _secret_notices_sent.setdefault(f"{user_id}:{slot_id}", set())
    allowed: bool | None = None  # the preference, read only if it is needed
    for kind in dict.fromkeys(finding.kind for finding in findings):
        if kind in told:
            continue
        certain = secrets.KINDS.get(kind, False)
        if not certain:
            if allowed is None:
                from condor.preferences import (
                    load_user_data_for,
                    secret_notices_enabled,
                )

                allowed = secret_notices_enabled(load_user_data_for(user_id))
            if not allowed:
                continue
        told.add(kind)
        await _send(
            ws,
            {
                "event": "secret_notice",
                "slot_id": slot_id,
                "kind": kind,
                "certain": certain,
            },
        )


async def _handle_send_message(
    ws: WebSocket,
    user_id: int,
    msg: dict,
) -> None:
    slot_id = msg.get("slot_id", "")
    text = msg.get("text", "").strip()
    # What the user was looking at while asking (FEAT-059). Rides beside the
    # text to the funnel, which prepends it to this one prompt and never
    # records it — the transcript keeps only the user's words.
    view_context = str(msg.get("view_context") or "")[:VIEW_CONTEXT_MAX_CHARS]
    if not text:
        await _send(ws, {"event": "error", "message": "Empty message"})
        return
    if not slot_id:
        await _send(ws, {"event": "error", "message": "No slot_id"})
        return

    # Said before the spawn wait, so the notice lands with the message it is
    # about rather than after the answer to it.
    await _notify_secret_shapes(ws, user_id, slot_id, text)

    # The user is allowed to type through a spawn, so a message can legitimately
    # arrive before the subprocess exists. Waiting for it is the difference
    # between a warm session and "Session ended. Start a new one."
    await _await_spawn(user_id, slot_id)

    session_key = _session_key(user_id, slot_id)
    task_key = f"{user_id}:{slot_id}"
    task = asyncio.current_task()
    stream = None

    gate = _slot_gate(task_key)
    await gate.acquire()
    gate_held = True
    try:
        info = await runtime.get_info(session_key)

        if info is None or not info.alive:
            # No session behind this slot. That is not the same as "gone": the
            # budget detaches idle sessions on purpose and keeps the
            # conversation, and a bot restart leaves every one of them on disk.
            # For a web slot the slot id *is* the conversation id, so if the
            # record is still there this is a reattach — the same spawn
            # ``resume_conversation`` does — and the message the user just typed
            # goes on to be answered instead of being dropped on the floor.
            if info is not None:
                # Only a subprocess that really died needs reaping first; a
                # detached slot has nothing left to tear down.
                await runtime.destroy(session_key)
            conv = _conversation_for(user_id, slot_id)
            if conv is None:
                await _send(
                    ws,
                    {
                        "event": "error",
                        "slot_id": slot_id,
                        "message": "Session ended. Start a new one.",
                    },
                )
                await _send(
                    ws,
                    {
                        "event": "session_destroyed",
                        "slot_id": slot_id,
                        "had_session": True,
                    },
                )
                return

            # Said apart from the crash case on purpose: an evicted slot that
            # reattaches is the budget working, not a fault to go looking for.
            log.info(
                "Reattaching %s slot %s for user %d before its message",
                "dead" if info is not None else "detached",
                slot_id,
                user_id,
            )
            # ``_spawn`` and not ``_start``: see the note there on why this path
            # must not register itself as a pending spawn.
            if not await _spawn(
                ws,
                user_id,
                conv.id,
                _resume_agent_key(conv, ""),
                conv.server_name,
                restored=True,
                agent_slug=conv.agent_slug,
            ):
                return
            info = await runtime.get_info(session_key)
            if info is None:
                await _send(
                    ws,
                    {
                        "event": "error",
                        "slot_id": slot_id,
                        "message": "Session ended. Start a new one.",
                    },
                )
                return

        # This turn owns the slot from here on, so a Stop that arrives next
        # cancels *this* task and not the one it is replacing.
        if task:
            _active_prompt_tasks[task_key] = task

        # Busy is no longer a refusal: the composer stays live and sending is
        # how the user redirects an answer that is heading the wrong way.
        stream = runtime.prompt(
            session_key,
            PromptRequest(text=text, view_context=view_context),
            on_busy="steer",
        )

        if info.is_busy:
            # Said before the stream starts, so the partial answer on screen is
            # marked interrupted instead of being left looking finished.
            await _send(ws, {"event": "prompt_interrupted", "slot_id": slot_id})
            # One pull, still under the gate: in the steer path the runtime
            # stops the turn ahead and *then* yields QUEUED, so this is bounded
            # by TIMEOUTS.prompt_cancel and is exactly the window a third
            # message must not race through.
            await _pump_one(ws, stream, slot_id)

        gate.release()
        gate_held = False

        async for event in stream:
            message = _to_ws_message(event, slot_id)
            if message:
                await _send(ws, message)
    except asyncio.CancelledError:
        await _send(
            ws, {"event": "prompt_done", "slot_id": slot_id, "stop_reason": "cancelled"}
        )
    except RuntimeError as e:
        await _send(ws, {"event": "error", "slot_id": slot_id, "message": str(e)})
        await _send(
            ws, {"event": "prompt_done", "slot_id": slot_id, "stop_reason": "error"}
        )
    except Exception:
        log.exception("Error streaming prompt for user %d", user_id)
        await _send(
            ws, {"event": "error", "slot_id": slot_id, "message": "Stream error"}
        )
        await _send(
            ws, {"event": "prompt_done", "slot_id": slot_id, "stop_reason": "error"}
        )
    finally:
        if gate_held:
            gate.release()
        # Only if it is still ours. A turn that was steered aside must not reap
        # the entry belonging to the turn that replaced it — that is the race
        # that left Stop cancelling the wrong task.
        if _active_prompt_tasks.get(task_key) is task:
            _active_prompt_tasks.pop(task_key, None)


async def _pump_one(ws: WebSocket, stream, slot_id: str) -> None:
    """Forward exactly one event from a prompt stream, if it has one."""
    try:
        event = await stream.__anext__()
    except StopAsyncIteration:
        return
    message = _to_ws_message(event, slot_id)
    if message:
        await _send(ws, message)


async def _handle_abort_prompt(ws: WebSocket, user_id: int, msg: dict) -> None:
    slot_id = msg.get("slot_id", "")
    if not slot_id:
        await _send(ws, {"event": "error", "message": "No slot_id"})
        return

    # Stop generation at the agent, not just here: runtime.abort() sends ACP's
    # session/cancel and waits (bounded) for the agent to settle the turn, so
    # the model's context ends where the user's screen did. It also sets the
    # event flag that makes the prompt stream break out on the next iteration,
    # triggering its finally block to release the lock. The frontend resets
    # optimistically, so this await costs the user nothing visible.
    session_key = _session_key(user_id, slot_id)
    task_key = f"{user_id}:{slot_id}"

    # Same gate the send path takes, so a Stop landing between two sends cannot
    # interleave with a takeover and cancel a task that is already being
    # replaced — or worse, the one replacing it.
    async with _slot_gate(task_key):
        await runtime.abort(session_key)

        task = _active_prompt_tasks.get(task_key)
        if task and not task.done():
            task.cancel()
            # Wait briefly for the task to finish so the lock is released
            # before the next message can acquire it.
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=3)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
        else:
            # No active task to cancel — send prompt_done directly so the frontend resets
            await _send(
                ws,
                {
                    "event": "prompt_done",
                    "slot_id": slot_id,
                    "stop_reason": "cancelled",
                },
            )


async def _handle_destroy_session(ws: WebSocket, user_id: int, msg: dict) -> None:
    slot_id = msg.get("slot_id", "")
    if not slot_id:
        await _send(ws, {"event": "error", "message": "No slot_id"})
        return

    # Ordered after any in-flight spawn, so closing a tab mid-start reaps the
    # subprocess instead of racing past it and orphaning one.
    await _await_spawn(user_id, slot_id)

    session_key = _session_key(user_id, slot_id)
    destroyed = await runtime.destroy(session_key)

    # Clean up active prompt task to prevent memory leaks
    task_key = f"{user_id}:{slot_id}"
    _active_prompt_tasks.pop(task_key, None)
    # The gate goes with the slot. A waiter still holds the object it is queued
    # on, so dropping the entry only means the next send for a *new* session
    # under this key starts with a fresh one.
    _slot_gates.pop(task_key, None)

    await _send(
        ws, {"event": "session_destroyed", "slot_id": slot_id, "had_session": destroyed}
    )


async def _handle_resolve_permission(user_id: int, msg: dict) -> None:
    """Forward a dashboard answer to the shared registry.

    The registry enforces ownership and is idempotent, so a stale click after
    the request was answered in Telegram is a silent no-op.
    """
    await get_registry().resolve(
        msg.get("request_id", ""),
        approved=msg.get("approved", False),
        by_user_id=user_id,
    )


# ── REST endpoint for OpenRouter models ──


@router.get("/chat/openrouter/models")
async def get_openrouter_models(user: WebUser = Depends(get_current_user)):
    """OpenRouter models that support tool-calling, for the web model picker.

    Mirrors the Telegram OpenRouter picker: the catalog is public/unauthenticated,
    so this works without OPENROUTER_API_KEY set. Starting a session with one of
    these models still requires the key, and raises a clear error if it is unset.
    """
    models = await fetch_models()
    return {
        "models": [
            {
                "slug": m.slug,
                "name": m.name,
                "context_length": m.context_length,
                "prompt_price": m.prompt_price,
                "completion_price": m.completion_price,
            }
            for m in models
        ],
    }
