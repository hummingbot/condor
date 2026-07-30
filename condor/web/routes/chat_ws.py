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

from condor.runtime import WEB, EventType, PromptRequest, SessionKey, SessionSpec
from condor.runtime import client as runtime
from condor.runtime.events import RuntimeEvent
from condor.web.auth import decode_jwt, extract_ws_token, get_current_user
from condor.web.models import WebUser
from handlers.agents._shared import (
    AGENT_MODES,
    AGENT_OPTIONS,
    DEFAULT_AGENT,
    DEFAULT_MODE,
    is_dangerous_tool_call,
    load_assistant,
)
from handlers.agents.confirmation import _format_tool_summary
from handlers.agents.openrouter_models import fetch_models

log = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# Pending permission futures for web clients: request_id -> (user_id, Future[bool])
_pending_permissions: dict[str, tuple[int, asyncio.Future]] = {}

# Track active prompt tasks per slot: "user_id:slot_id" -> asyncio.Task.
# Connection-scoped, not session state, so this stays local to the WS layer.
_active_prompt_tasks: dict[str, asyncio.Task] = {}

PERMISSION_TIMEOUT = 120  # seconds


def _session_key(user_id: int, slot_id: str) -> SessionKey:
    """Build the canonical runtime key for one web chat slot."""
    return SessionKey.web(user_id, slot_id)


async def _get_user_sessions(user_id: int) -> list[dict]:
    """This user's live web sessions, as the frontend expects them.

    Derived from the runtime rather than from local slot bookkeeping, so a
    session survives a WebSocket reconnect and a session killed elsewhere
    (Telegram, the REST API) disappears here without any cross-talk.
    """
    return [
        {
            "slot_id": info.slot,
            "agent_key": info.agent_key,
            "mode": info.mode,
            "is_busy": info.is_busy,
            "server_name": info.server_name,
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
    through ``_web_permission_callback``).
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


async def _send(ws: WebSocket, event: dict) -> None:
    """Send a JSON event to the client, ignoring closed connections."""
    try:
        await ws.send_text(json.dumps(event))
    except Exception:
        pass


async def _web_permission_callback(
    ws: WebSocket,
    user_id: int,
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
    _pending_permissions[request_id] = (user_id, future)

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
            elif action == "send_message":
                _spawn(_handle_send_message(ws, user_id, msg))
            elif action == "destroy_session":
                _spawn(_handle_destroy_session(ws, user_id, msg))
            elif action == "list_sessions":
                sessions = await _get_user_sessions(user_id)
                await _send(ws, {"event": "sessions_list", "sessions": sessions})
            elif action == "resolve_permission":
                _handle_resolve_permission(user_id, msg)
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
    agent_key = msg.get("agent_key", DEFAULT_AGENT)
    mode = msg.get("mode", DEFAULT_MODE)
    server_name = msg.get("server_name")  # From frontend's selected server

    # The per-user session cap now lives in the runtime, so Telegram and the
    # dashboard draw on one budget; exceeding it raises out of create_session.
    slot_id = str(uuid.uuid4())[:8]
    session_key = _session_key(user_id, slot_id)

    async def perm_cb(tool_call: dict, options: list[dict]) -> dict:
        return await _web_permission_callback(ws, user_id, tool_call, options)

    # Hydrate the user's stored preferences so web sessions resolve the same
    # things a Telegram session would — notably the saved custom endpoint
    # behind a "custom@<endpoint>:<model>" agent key.
    from condor.preferences import load_user_data_for

    # Mode-specific assistant context (e.g. agent_builder instructions) travels
    # as spec data rather than by mutating the live session after creation.
    mode_context = load_assistant(mode) if mode != DEFAULT_MODE else ""

    try:
        info = await runtime.create_session(
            SessionSpec(
                key=str(session_key),
                agent_key=agent_key,
                mode=mode,
                user_id=user_id,
                platform="web",
                lazy_context=True,  # Don't block — inject context on first message
                server_name=server_name,
                extra_context=mode_context or "",
            ),
            permission_callback=perm_cb,
            user_data=load_user_data_for(user_id),
        )

        await _send(
            ws,
            {
                "event": "session_started",
                "slot_id": slot_id,
                "agent_key": agent_key,
                "mode": mode,
                "server_name": info.server_name,
            },
        )
    except Exception as e:
        log.exception("Failed to start chat session for user %d", user_id)
        await _send(ws, {"event": "error", "message": f"Failed to start session: {e}"})


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

    session_key = _session_key(user_id, slot_id)
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
            ws, {"event": "session_destroyed", "slot_id": slot_id, "had_session": True}
        )
        return

    if info.is_busy:
        await _send(ws, {"event": "error", "message": "Agent is busy"})
        return

    task_key = f"{user_id}:{slot_id}"
    task = asyncio.current_task()
    if task:
        _active_prompt_tasks[task_key] = task

    try:
        async for event in runtime.prompt(session_key, PromptRequest(text=text)):
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
        _active_prompt_tasks.pop(task_key, None)


async def _handle_abort_prompt(ws: WebSocket, user_id: int, msg: dict) -> None:
    slot_id = msg.get("slot_id", "")
    if not slot_id:
        await _send(ws, {"event": "error", "message": "No slot_id"})
        return

    # Abort the ACP-level prompt so stale events don't leak into the next message.
    # runtime.abort() sets an event flag that makes the prompt stream break out
    # on the next iteration, triggering its finally block to release the lock.
    session_key = _session_key(user_id, slot_id)
    await runtime.abort(session_key)

    task_key = f"{user_id}:{slot_id}"
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


async def _handle_destroy_session(ws: WebSocket, user_id: int, msg: dict) -> None:
    slot_id = msg.get("slot_id", "")
    if not slot_id:
        await _send(ws, {"event": "error", "message": "No slot_id"})
        return

    session_key = _session_key(user_id, slot_id)
    destroyed = await runtime.destroy(session_key)

    # Clean up active prompt task to prevent memory leaks
    task_key = f"{user_id}:{slot_id}"
    _active_prompt_tasks.pop(task_key, None)

    await _send(
        ws, {"event": "session_destroyed", "slot_id": slot_id, "had_session": destroyed}
    )


def _handle_resolve_permission(user_id: int, msg: dict) -> None:
    request_id = msg.get("request_id", "")
    approved = msg.get("approved", False)
    entry = _pending_permissions.get(request_id)
    if entry is None:
        return
    owner_id, future = entry
    if owner_id != user_id:
        # Ignore attempts to resolve another user's pending permission
        log.warning(
            "User %d tried to resolve permission %s owned by another user",
            user_id,
            request_id,
        )
        return
    if not future.done():
        future.set_result(approved)


# ── REST endpoint for chat options ──


@router.get("/chat/options")
async def get_chat_options(user: WebUser = Depends(get_current_user)):
    """Return available agent models and modes.

    Every entry carries a ``picker`` flag. Picker sentinels ("openrouter:",
    "custom:") are not startable agent keys — they stand for "open a model
    list" — so a client must render them as a drill-down, never as a
    selectable model. The flag is sent explicitly because the shape of the key
    doesn't tell you: "ollama:" and "lmstudio:" also end in a colon but are
    real keys meaning "that backend's default model".

    The user's saved custom endpoints come back separately, with their models
    resolved on demand via /settings/custom-providers/{name}/models.
    """
    from condor.preferences import get_custom_providers, load_user_data_for

    providers = get_custom_providers(load_user_data_for(user.id))
    return {
        "agents": [
            {"key": k, "label": v["label"], "picker": bool(v.get("picker"))}
            for k, v in AGENT_OPTIONS.items()
        ],
        "custom_providers": [
            {
                "name": p["name"],
                "base_url": p["base_url"],
                "has_key": bool(p.get("api_key")),
            }
            for p in providers
        ],
        "modes": [
            {"key": k, "label": v["label"], "description": v["description"]}
            for k, v in AGENT_MODES.items()
        ],
        "default_agent": DEFAULT_AGENT,
        "default_mode": DEFAULT_MODE,
    }


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
