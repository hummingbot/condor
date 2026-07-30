"""Session API — REST for lifecycle, SSE for prompt streaming.

This is the runtime's public surface. Both frontends and any third caller (MCP
subprocess, CLI, cron) drive sessions through these routes instead of importing
Condor internals, which is what lets the dashboard list and kill a session that
was started from Telegram.

Note: modules under ``condor/web/routes/`` are not in ``main.py``'s hot-reload
list, so changes here need a full bot restart.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse

from condor.runtime import PromptRequest, SessionInfo, SessionKey, SessionSpec
from condor.runtime import client as runtime
from condor.runtime.sse import SSE_HEADERS, event_stream
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from config_manager import get_config_manager

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


# ── Helpers ──


def _parse_key(raw: str) -> SessionKey:
    """Parse a key from the URL, accepting the pre-facade forms too."""
    try:
        return SessionKey.legacy_from(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Malformed session key: {raw}")


async def _authorized_info(key: SessionKey, user: WebUser) -> SessionInfo:
    """Fetch a session, 404 if absent and 403 if it belongs to someone else."""
    info = await runtime.get_info(key)
    if info is None:
        raise HTTPException(status_code=404, detail=f"No session {key}")
    _require_ownership(info, user)
    return info


def _require_ownership(info: SessionInfo, user: WebUser) -> None:
    """Admins see everything; everyone else only their own sessions.

    Ownership is compared against the session's recorded ``user_id`` rather
    than the key's owner segment: a Telegram key's owner is a chat id, which
    only coincides with the user id in private chats.
    """
    if get_config_manager().is_admin(user.id):
        return
    if info.user_id is not None and info.user_id == user.id:
        return
    raise HTTPException(status_code=403, detail="Not your session")


# ── Lifecycle ──


@router.get("", response_model=list[SessionInfo])
async def list_sessions(user: WebUser = Depends(get_current_user)):
    """Every live session owned by the caller, across all surfaces."""
    return await runtime.list_sessions(user_id=user.id)


@router.get("/options")
async def session_options(user: WebUser = Depends(get_current_user)):
    """Agents, modes and servers a session can be started with.

    Payload mirrors the older ``/chat/options`` (which stays for the shipped
    dashboard) and adds the caller's accessible servers. ``picker`` marks
    sentinel keys like "openrouter:" that open a model list rather than naming
    a startable model — the key's shape does not tell you, since "ollama:" also
    ends in a colon but is a real key.
    """
    from condor.preferences import get_custom_providers, load_user_data_for
    from handlers.agents._shared import (
        AGENT_MODES,
        AGENT_OPTIONS,
        DEFAULT_AGENT,
        DEFAULT_MODE,
    )

    cm = get_config_manager()
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
        "servers": cm.get_accessible_servers(user.id),
        "default_agent": DEFAULT_AGENT,
        "default_mode": DEFAULT_MODE,
    }


@router.post("", response_model=SessionInfo)
async def create_session(
    spec: SessionSpec,
    user: WebUser = Depends(get_current_user),
):
    """Provision a session. The caller may only create sessions for itself."""
    cm = get_config_manager()
    if spec.user_id is None:
        spec = spec.model_copy(update={"user_id": user.id})
    elif spec.user_id != user.id and not cm.is_admin(user.id):
        raise HTTPException(status_code=403, detail="Cannot create for another user")

    from condor.preferences import load_user_data_for

    try:
        return await runtime.create_session(
            spec, user_data=load_user_data_for(spec.user_id)
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as an HTTP error
        log.exception("Failed to create session %s", spec.key)
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{key}", response_model=SessionInfo)
async def get_session(key: str, user: WebUser = Depends(get_current_user)):
    return await _authorized_info(_parse_key(key), user)


@router.delete("/{key}")
async def destroy_session(key: str, user: WebUser = Depends(get_current_user)):
    parsed = _parse_key(key)
    await _authorized_info(parsed, user)
    return {"destroyed": await runtime.destroy(parsed)}


# ── Prompting ──


@router.post("/{key}/prompt")
async def prompt_session(
    key: str,
    req: PromptRequest,
    user: WebUser = Depends(get_current_user),
):
    """Stream one turn as ``text/event-stream``.

    ``X-Accel-Buffering: no`` is required: without it nginx buffers the whole
    response and the stream arrives as one delayed blob.
    """
    parsed = _parse_key(key)
    await _authorized_info(parsed, user)
    return StreamingResponse(
        event_stream(runtime.prompt(parsed, req)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/{key}/action")
async def session_action(
    key: str,
    action: str = Body(..., embed=True),
    user: WebUser = Depends(get_current_user),
):
    """One multiplexed endpoint for the small lifecycle verbs."""
    parsed = _parse_key(key)
    info = await _authorized_info(parsed, user)

    if action == "cancel":
        return {"ok": await runtime.abort(parsed)}
    if action == "new":
        # Rebuild the session in place, keeping the agent and mode it had.
        await runtime.destroy(parsed)
        from condor.preferences import load_user_data_for

        spec = SessionSpec(
            key=str(parsed),
            agent_key=info.agent_key,
            mode=info.mode,
            user_id=info.user_id,
            server_name=info.server_name,
            agent_slug=info.agent_slug,
        )
        await runtime.create_session(spec, user_data=load_user_data_for(info.user_id))
        return {"ok": True}

    raise HTTPException(status_code=400, detail=f"Unknown action: {action}")
