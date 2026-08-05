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

from condor.runtime import WEB, EventType, PromptRequest, SessionKey, SessionSpec
from condor.runtime import client as runtime
from condor.runtime import conversations
from condor.runtime.confirmations import (
    PendingConfirmation,
    build_permission_callback,
    get_registry,
)
from condor.runtime.events import RuntimeEvent
from condor.runtime.timeouts import TIMEOUTS
from condor.web.auth import decode_jwt, extract_ws_token, get_current_user
from condor.web.models import WebUser
from handlers.agents._shared import DEFAULT_AGENT
from handlers.agents.openrouter_models import fetch_models

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
    """This user's live web sessions, as the frontend expects them.

    Derived from the runtime rather than from local slot bookkeeping, so a
    session survives a WebSocket reconnect and a session killed elsewhere
    (Telegram, the REST API) disappears here without any cross-talk.
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
        }
        for info in await runtime.list_sessions(user_id)
        if info.surface == WEB and info.alive
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
            },
        )


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
    agent_key = msg.get("agent_key", DEFAULT_AGENT)
    server_name = msg.get("server_name")  # From frontend's selected server
    # A dashboard chat can be born already bound to a domain Agent. Without
    # this, "Chat" on an agent's page would have to start-then-switch, which
    # spawns two subprocesses for one click.
    agent_slug = str(msg.get("agent_slug") or "")

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
    try:
        conv = conversations.get_conversation(user_id, conversation_id)
    except conversations.ConversationIdError:
        conv = None
    if conv is None:
        await _send(ws, {"event": "error", "message": "No such conversation"})
        return

    await _start(
        ws,
        user_id,
        conv.id,
        msg.get("agent_key") or conv.agent_key or DEFAULT_AGENT,
        msg.get("server_name") or conv.server_name,
        restored=True,
        agent_slug=conv.agent_slug,
        client_ref=str(msg.get("client_ref") or ""),
    )


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
) -> None:
    """Spawn the session behind a conversation and announce it.

    Shared by ``start_session`` and ``resume_conversation``: the two differ only
    in whether the conversation already had turns, and the runtime decides that
    by looking at the transcript, not at which action was called.

    ``client_ref`` is echoed back untouched. The dashboard renders a tab the
    instant the user asks for one, before any id exists, and uses the echo to
    reconcile that optimistic tab with the conversation it turned out to be.
    """
    # The per-user session cap now lives in the runtime, so Telegram and the
    # dashboard draw on one budget; exceeding it raises out of create_session.
    slot_id = conversation_id
    session_key = _session_key(user_id, slot_id)

    # Registered before the first await, so a send_message dispatched in the
    # same batch of WS frames finds the spawn and waits for it. The task here is
    # the handler's own — awaiting it means awaiting the whole announce.
    task_key = f"{user_id}:{slot_id}"
    current = asyncio.current_task()
    if current is not None:
        _pending_spawns[task_key] = current

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
    finally:
        _pending_spawns.pop(task_key, None)


async def _handle_send_message(
    ws: WebSocket,
    user_id: int,
    msg: dict,
) -> None:
    slot_id = msg.get("slot_id", "")
    text = msg.get("text", "").strip()
    if not text:
        await _send(ws, {"event": "error", "message": "Empty message"})
        return
    if not slot_id:
        await _send(ws, {"event": "error", "message": "No slot_id"})
        return

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
            # Session died — clean up and notify frontend
            await runtime.destroy(session_key)
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
                {"event": "session_destroyed", "slot_id": slot_id, "had_session": True},
            )
            return

        # This turn owns the slot from here on, so a Stop that arrives next
        # cancels *this* task and not the one it is replacing.
        if task:
            _active_prompt_tasks[task_key] = task

        # Busy is no longer a refusal: the composer stays live and sending is
        # how the user redirects an answer that is heading the wrong way.
        stream = runtime.prompt(session_key, PromptRequest(text=text), on_busy="steer")

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
