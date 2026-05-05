"""Chat WebSocket endpoint for the AI assistant.

Dedicated WS at /ws/chat (separate from the channel-based /ws).
Manages multiple agent sessions per user and streams ACPEvents as JSON.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from condor.acp.client import (
    Heartbeat,
    PromptDone,
    TextChunk,
    ThoughtChunk,
    ToolCallEvent,
    ToolCallUpdate,
)
from condor.web.auth import decode_jwt, get_current_user
from condor.web.models import WebUser
from handlers.agents._shared import (
    AGENT_MODES,
    AGENT_OPTIONS,
    DEFAULT_AGENT,
    DEFAULT_MODE,
    is_dangerous_tool_call,
)
from handlers.agents.confirmation import _format_tool_summary
from handlers.agents.session import (
    destroy_session,
    get_or_create_session,
    get_session,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# Pending permission futures for web clients: request_id -> Future[bool]
_pending_permissions: dict[str, asyncio.Future] = {}

# Track which session slots exist per user: user_id -> [slot_id, ...]
_user_slots: dict[int, list[str]] = {}

PERMISSION_TIMEOUT = 120  # seconds
MAX_SESSIONS_PER_USER = 5


def _session_key(user_id: int, slot_id: str) -> str:
    """Build a session key that won't collide with Telegram int chat_ids."""
    return f"web_{user_id}_{slot_id}"


def _get_user_sessions(user_id: int) -> list[dict]:
    """List all alive sessions for a user."""
    slots = _user_slots.get(user_id, [])
    result = []
    for slot_id in slots:
        key = _session_key(user_id, slot_id)
        session = get_session(key)
        if session and session.client.alive:
            result.append({
                "slot_id": slot_id,
                "agent_key": session.agent_key,
                "mode": session.mode,
                "is_busy": session.is_busy,
            })
    return result


async def _send(ws: WebSocket, event: dict) -> None:
    """Send a JSON event to the client, ignoring closed connections."""
    try:
        await ws.send_text(json.dumps(event))
    except Exception:
        pass


async def _web_permission_callback(
    ws: WebSocket,
    tool_call: dict[str, Any],
    options: list[dict[str, Any]],
) -> dict[str, Any]:
    """Permission callback for web sessions."""
    if not is_dangerous_tool_call(tool_call):
        for opt in options:
            if opt.get("kind") in ("allow_once", "allow_always"):
                return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
        if options:
            return {"outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}}
        return {"outcome": {"outcome": "cancelled"}}

    request_id = str(uuid.uuid4())[:8]
    summary = _format_tool_summary(tool_call)

    await _send(ws, {
        "event": "permission_request",
        "request_id": request_id,
        "summary": summary,
    })

    future: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending_permissions[request_id] = future

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
            return {"outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}}

    return {"outcome": {"outcome": "cancelled"}}


@router.websocket("/ws/chat")
async def chat_websocket(ws: WebSocket, token: str = Query(...)):
    """Chat WebSocket endpoint. Authenticates via JWT query param."""
    payload = decode_jwt(token)
    if not payload:
        await ws.close(code=4001, reason="Invalid token")
        return

    user_id = int(payload["sub"])
    await ws.accept()

    # Send list of existing alive sessions on connect
    sessions = _get_user_sessions(user_id)
    await _send(ws, {"event": "sessions_list", "sessions": sessions})

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
                await _handle_start_session(ws, user_id, msg)
            elif action == "send_message":
                await _handle_send_message(ws, user_id, msg)
            elif action == "destroy_session":
                await _handle_destroy_session(ws, user_id, msg)
            elif action == "list_sessions":
                sessions = _get_user_sessions(user_id)
                await _send(ws, {"event": "sessions_list", "sessions": sessions})
            elif action == "resolve_permission":
                _handle_resolve_permission(msg)
            else:
                await _send(ws, {"event": "error", "message": f"Unknown action: {action}"})

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Chat WS error for user %d", user_id)


async def _handle_start_session(
    ws: WebSocket, user_id: int, msg: dict,
) -> None:
    agent_key = msg.get("agent_key", DEFAULT_AGENT)
    mode = msg.get("mode", DEFAULT_MODE)

    # Check slot limit
    slots = _user_slots.get(user_id, [])
    # Clean dead slots
    alive_slots = []
    for s in slots:
        session = get_session(_session_key(user_id, s))
        if session and session.client.alive:
            alive_slots.append(s)
    _user_slots[user_id] = alive_slots

    if len(alive_slots) >= MAX_SESSIONS_PER_USER:
        await _send(ws, {"event": "error", "message": f"Max {MAX_SESSIONS_PER_USER} sessions"})
        return

    slot_id = str(uuid.uuid4())[:8]
    session_key = _session_key(user_id, slot_id)

    async def perm_cb(tool_call: dict, options: list[dict]) -> dict:
        return await _web_permission_callback(ws, tool_call, options)

    try:
        await get_or_create_session(
            chat_id=session_key,
            agent_key=agent_key,
            permission_callback=perm_cb,
            user_id=user_id,
            mode=mode,
            platform="web",
        )
        _user_slots.setdefault(user_id, []).append(slot_id)
        await _send(ws, {
            "event": "session_started",
            "slot_id": slot_id,
            "agent_key": agent_key,
            "mode": mode,
        })
    except Exception as e:
        log.exception("Failed to start chat session for user %d", user_id)
        await _send(ws, {"event": "error", "message": f"Failed to start session: {e}"})


async def _handle_send_message(
    ws: WebSocket, user_id: int, msg: dict,
) -> None:
    slot_id = msg.get("slot_id", "")
    text = msg.get("text", "").strip()
    if not text:
        await _send(ws, {"event": "error", "message": "Empty message"})
        return
    if not slot_id:
        await _send(ws, {"event": "error", "message": "No slot_id"})
        return

    session_key = _session_key(user_id, slot_id)
    session = get_session(session_key)

    if not session or not session.client.alive:
        await _send(ws, {"event": "error", "message": "Session not found. Create a new one."})
        return

    if session.is_busy:
        await _send(ws, {"event": "error", "message": "Agent is busy"})
        return

    try:
        async for event in session.prompt_stream(text):
            if isinstance(event, TextChunk):
                await _send(ws, {"event": "text_chunk", "slot_id": slot_id, "text": event.text})
            elif isinstance(event, ThoughtChunk):
                await _send(ws, {"event": "thought_chunk", "slot_id": slot_id, "text": event.text})
            elif isinstance(event, ToolCallEvent):
                await _send(ws, {
                    "event": "tool_call",
                    "slot_id": slot_id,
                    "tool_call_id": event.tool_call_id,
                    "title": event.title,
                    "status": event.status,
                })
            elif isinstance(event, ToolCallUpdate):
                await _send(ws, {
                    "event": "tool_call_update",
                    "slot_id": slot_id,
                    "tool_call_id": event.tool_call_id,
                    "status": event.status,
                })
            elif isinstance(event, Heartbeat):
                await _send(ws, {
                    "event": "heartbeat",
                    "slot_id": slot_id,
                    "elapsed_seconds": event.elapsed_seconds,
                })
            elif isinstance(event, PromptDone):
                await _send(ws, {
                    "event": "prompt_done",
                    "slot_id": slot_id,
                    "stop_reason": event.stop_reason,
                })
    except RuntimeError as e:
        await _send(ws, {"event": "error", "slot_id": slot_id, "message": str(e)})
    except Exception:
        log.exception("Error streaming prompt for user %d", user_id)
        await _send(ws, {"event": "error", "slot_id": slot_id, "message": "Stream error"})


async def _handle_destroy_session(ws: WebSocket, user_id: int, msg: dict) -> None:
    slot_id = msg.get("slot_id", "")
    if not slot_id:
        await _send(ws, {"event": "error", "message": "No slot_id"})
        return

    session_key = _session_key(user_id, slot_id)
    destroyed = await destroy_session(session_key)

    # Remove from user slots
    slots = _user_slots.get(user_id, [])
    if slot_id in slots:
        slots.remove(slot_id)

    await _send(ws, {"event": "session_destroyed", "slot_id": slot_id, "had_session": destroyed})


def _handle_resolve_permission(msg: dict) -> None:
    request_id = msg.get("request_id", "")
    approved = msg.get("approved", False)
    future = _pending_permissions.get(request_id)
    if future and not future.done():
        future.set_result(approved)


# ── REST endpoint for chat options ──

@router.get("/chat/options")
async def get_chat_options(user: WebUser = Depends(get_current_user)):
    """Return available agent models and modes."""
    return {
        "agents": [
            {"key": k, "label": v["label"]}
            for k, v in AGENT_OPTIONS.items()
        ],
        "modes": [
            {"key": k, "label": v["label"], "description": v["description"]}
            for k, v in AGENT_MODES.items()
        ],
        "default_agent": DEFAULT_AGENT,
        "default_mode": DEFAULT_MODE,
    }
