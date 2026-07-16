"""Chat WebSocket endpoint for the AI assistant.

Dedicated WS at /ws/chat (separate from the channel-based /ws).
Manages multiple agent sessions per browser client and streams ACPEvents as
JSON. Loopback posture (§5.5) is the trust boundary — no per-request auth.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from condor.acp.client import (
    Heartbeat,
    PromptDone,
    TextChunk,
    ThoughtChunk,
    ToolCallEvent,
    ToolCallUpdate,
)
from condor.agents.context import AGENT_OPTIONS, DEFAULT_AGENT
from condor.agents.gating import is_dangerous_tool_call
from condor.agents.confirmation import _format_tool_summary
from condor.agents.chat_session import destroy_session, get_or_create_session, get_session

log = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# A client_id identifies one browser (persisted in its localStorage) so a
# reconnect resumes the same session slots; it carries no authority — the
# loopback Origin/Host check in create_app() (§5.5) is the trust boundary.
_CLIENT_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# Pending permission futures for web clients: request_id -> (client_id, Future[bool])
_pending_permissions: dict[str, tuple[str, asyncio.Future]] = {}

# Track which session slots exist per client: client_id -> [slot_id, ...]
_client_slots: dict[str, list[str]] = {}

# Track active prompt tasks per slot: "client_id:slot_id" -> asyncio.Task
_active_prompt_tasks: dict[str, asyncio.Task] = {}

PERMISSION_TIMEOUT = 120  # seconds
MAX_SESSIONS_PER_CLIENT = 5


def _session_key(client_id: str, slot_id: str) -> str:
    return f"web_{client_id}_{slot_id}"


def _resolve_client_id(requested: str | None) -> str:
    """Mint a fresh uuid4 hex client id unless the caller offered a
    well-formed one (a returning browser resuming its slots)."""
    if requested and _CLIENT_ID_RE.match(requested):
        return requested
    return uuid.uuid4().hex


def _get_client_sessions(client_id: str) -> list[dict]:
    """List all alive sessions for a client."""
    slots = _client_slots.get(client_id, [])
    result = []
    for slot_id in slots:
        key = _session_key(client_id, slot_id)
        session = get_session(key)
        if session and session.client.alive:
            result.append(
                {
                    "slot_id": slot_id,
                    "agent_key": session.agent_key,
                    "is_busy": session.is_busy,
                    "server_name": session.server_name,
                }
            )
    return result


async def _send(ws: WebSocket, event: dict) -> None:
    """Send a JSON event to the client, ignoring closed connections."""
    try:
        await ws.send_text(json.dumps(event))
    except Exception:
        pass


async def _web_permission_callback(
    ws: WebSocket,
    client_id: str,
    tool_call: dict[str, Any],
    options: list[dict[str, Any]],
) -> dict[str, Any]:
    """Permission callback for web sessions."""
    if not is_dangerous_tool_call(tool_call):
        for opt in options:
            if opt.get("kind") in ("allow_once", "allow_always"):
                return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
        if options:
            return {
                "outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}
            }
        return {"outcome": {"outcome": "cancelled"}}

    request_id = str(uuid.uuid4())[:8]
    summary = _format_tool_summary(tool_call)

    await _send(
        ws,
        {
            "event": "permission_request",
            "request_id": request_id,
            "summary": summary,
        },
    )

    future: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending_permissions[request_id] = (client_id, future)

    try:
        approved = await asyncio.wait_for(future, timeout=PERMISSION_TIMEOUT)
    except asyncio.TimeoutError:
        _pending_permissions.pop(request_id, None)
        return {"outcome": {"outcome": "cancelled"}}
    finally:
        _pending_permissions.pop(request_id, None)

    if approved:
        for opt in options:
            if opt.get("kind") in ("allow_once", "allow_always"):
                return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
        if options:
            return {
                "outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}
            }

    return {"outcome": {"outcome": "cancelled"}}


@router.websocket("/ws/chat")
async def chat_websocket(ws: WebSocket, client: str | None = Query(default=None)):
    """Chat WebSocket endpoint.

    Loopback posture (§5.5) is the sole gate — no per-request identity. The
    optional ``?client=`` query param lets a returning browser resume its
    session slots across reconnects; a missing or unrecognized value mints a
    fresh uuid4 hex, which the client is expected to persist (localStorage)
    and send back on the next connect.
    """
    from condor.web.security import websocket_origin_allowed

    if not websocket_origin_allowed(ws):
        await ws.close(code=4003, reason="non-loopback origin")
        return

    client_id = _resolve_client_id(client)
    await ws.accept()

    # Send list of existing alive sessions on connect
    sessions = _get_client_sessions(client_id)
    await _send(
        ws, {"event": "sessions_list", "client_id": client_id, "sessions": sessions}
    )

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
                _spawn(_handle_start_session(ws, client_id, msg))
            elif action == "send_message":
                _spawn(_handle_send_message(ws, client_id, msg))
            elif action == "destroy_session":
                _spawn(_handle_destroy_session(ws, client_id, msg))
            elif action == "list_sessions":
                sessions = _get_client_sessions(client_id)
                await _send(ws, {"event": "sessions_list", "sessions": sessions})
            elif action == "resolve_permission":
                _handle_resolve_permission(client_id, msg)
            elif action == "abort_prompt":
                _spawn(_handle_abort_prompt(ws, client_id, msg))
            else:
                await _send(
                    ws, {"event": "error", "message": f"Unknown action: {action}"}
                )

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Chat WS error for client %s", client_id)
    finally:
        # Cancel any in-flight background tasks on disconnect
        for task in bg_tasks:
            task.cancel()
        if bg_tasks:
            await asyncio.gather(*bg_tasks, return_exceptions=True)


async def _handle_start_session(
    ws: WebSocket,
    client_id: str,
    msg: dict,
) -> None:
    agent_key = msg.get("agent_key", DEFAULT_AGENT)
    server_name = msg.get("server_name")  # From frontend's selected server

    # Check slot limit
    slots = _client_slots.get(client_id, [])
    # Clean dead slots
    alive_slots = []
    for s in slots:
        session = get_session(_session_key(client_id, s))
        if session and session.client.alive:
            alive_slots.append(s)
    _client_slots[client_id] = alive_slots

    if len(alive_slots) >= MAX_SESSIONS_PER_CLIENT:
        await _send(
            ws, {"event": "error", "message": f"Max {MAX_SESSIONS_PER_CLIENT} sessions"}
        )
        return

    slot_id = str(uuid.uuid4())[:8]
    session_key = _session_key(client_id, slot_id)

    async def perm_cb(tool_call: dict, options: list[dict]) -> dict:
        return await _web_permission_callback(ws, client_id, tool_call, options)

    try:
        session = await get_or_create_session(
            chat_id=session_key,
            agent_key=agent_key,
            permission_callback=perm_cb,
            platform="web",
            lazy_context=True,  # Don't block — inject context on first message
            server_name=server_name,
        )

        _client_slots.setdefault(client_id, []).append(slot_id)
        await _send(
            ws,
            {
                "event": "session_started",
                "slot_id": slot_id,
                "agent_key": agent_key,
                "server_name": session.server_name,
            },
        )
    except Exception as e:
        log.exception("Failed to start chat session for client %s", client_id)
        await _send(ws, {"event": "error", "message": f"Failed to start session: {e}"})


async def _handle_send_message(
    ws: WebSocket,
    client_id: str,
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

    session_key = _session_key(client_id, slot_id)
    session = get_session(session_key)

    if not session or not session.client.alive:
        # Session died — clean up and notify frontend
        await destroy_session(session_key)
        slots = _client_slots.get(client_id, [])
        if slot_id in slots:
            slots.remove(slot_id)
        await _send(
            ws,
            {
                "event": "error",
                "slot_id": slot_id,
                "message": "Session ended. Start a new one.",
            },
        )
        await _send(
            ws, {"event": "session_destroyed", "slot_id": slot_id, "had_session": True}
        )
        return

    if session.is_busy:
        await _send(ws, {"event": "error", "message": "Agent is busy"})
        return

    task_key = f"{client_id}:{slot_id}"
    task = asyncio.current_task()
    if task:
        _active_prompt_tasks[task_key] = task

    try:
        async for event in session.prompt_stream(text):
            if isinstance(event, TextChunk):
                await _send(
                    ws, {"event": "text_chunk", "slot_id": slot_id, "text": event.text}
                )
            elif isinstance(event, ThoughtChunk):
                await _send(
                    ws,
                    {"event": "thought_chunk", "slot_id": slot_id, "text": event.text},
                )
            elif isinstance(event, ToolCallEvent):
                await _send(
                    ws,
                    {
                        "event": "tool_call",
                        "slot_id": slot_id,
                        "tool_call_id": event.tool_call_id,
                        "title": event.title,
                        "status": event.status,
                    },
                )
            elif isinstance(event, ToolCallUpdate):
                await _send(
                    ws,
                    {
                        "event": "tool_call_update",
                        "slot_id": slot_id,
                        "tool_call_id": event.tool_call_id,
                        "status": event.status,
                    },
                )
            elif isinstance(event, Heartbeat):
                await _send(
                    ws,
                    {
                        "event": "heartbeat",
                        "slot_id": slot_id,
                        "elapsed_seconds": event.elapsed_seconds,
                    },
                )
            elif isinstance(event, PromptDone):
                await _send(
                    ws,
                    {
                        "event": "prompt_done",
                        "slot_id": slot_id,
                        "stop_reason": event.stop_reason,
                    },
                )
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
        log.exception("Error streaming prompt for client %s", client_id)
        await _send(
            ws, {"event": "error", "slot_id": slot_id, "message": "Stream error"}
        )
        await _send(
            ws, {"event": "prompt_done", "slot_id": slot_id, "stop_reason": "error"}
        )
    finally:
        _active_prompt_tasks.pop(task_key, None)


async def _handle_abort_prompt(ws: WebSocket, client_id: str, msg: dict) -> None:
    slot_id = msg.get("slot_id", "")
    if not slot_id:
        await _send(ws, {"event": "error", "message": "No slot_id"})
        return

    # Abort the ACP-level prompt so stale events don't leak into the next message.
    # session.abort() sets an event flag that makes prompt_stream break out on the
    # next iteration, triggering its finally block to release the lock properly.
    session_key = _session_key(client_id, slot_id)
    session = get_session(session_key)
    if session:
        session.abort()

    task_key = f"{client_id}:{slot_id}"
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
            ws, {"event": "prompt_done", "slot_id": slot_id, "stop_reason": "cancelled"}
        )


async def _handle_destroy_session(ws: WebSocket, client_id: str, msg: dict) -> None:
    slot_id = msg.get("slot_id", "")
    if not slot_id:
        await _send(ws, {"event": "error", "message": "No slot_id"})
        return

    session_key = _session_key(client_id, slot_id)
    destroyed = await destroy_session(session_key)

    # Clean up active prompt task to prevent memory leaks
    task_key = f"{client_id}:{slot_id}"
    _active_prompt_tasks.pop(task_key, None)

    # Remove from user slots
    slots = _client_slots.get(client_id, [])
    if slot_id in slots:
        slots.remove(slot_id)

    await _send(
        ws, {"event": "session_destroyed", "slot_id": slot_id, "had_session": destroyed}
    )


def _handle_resolve_permission(client_id: str, msg: dict) -> None:
    request_id = msg.get("request_id", "")
    approved = msg.get("approved", False)
    entry = _pending_permissions.get(request_id)
    if entry is None:
        return
    owner_id, future = entry
    if owner_id != client_id:
        # Ignore attempts to resolve another client's pending permission
        log.warning(
            "Client %s tried to resolve permission %s owned by another client",
            client_id,
            request_id,
        )
        return
    if not future.done():
        future.set_result(approved)


# ── REST endpoint for chat options ──


@router.get("/chat/options")
async def get_chat_options():
    """Return available agent models."""
    return {
        "agents": [{"key": k, "label": v["label"]} for k, v in AGENT_OPTIONS.items()],
        "default_agent": DEFAULT_AGENT,
    }
