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

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from condor.runtime import PromptRequest, SessionInfo, SessionKey, SessionSpec
from condor.runtime import client as runtime
from condor.runtime import conversations
from condor.runtime.binding import UnknownAgent
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
    """Agents and servers a session can be started with.

    Sole owner of this payload: agents, custom providers and bindable Agents,
    plus the caller's accessible servers. ``picker`` marks sentinel keys like
    "openrouter:" that open a model list rather than naming a startable model —
    the key's shape does not tell you, since "ollama:" also ends in a colon but
    is a real key.
    """
    from condor.agents.agent import AgentStore
    from condor.preferences import get_custom_providers, load_user_data_for
    from handlers.agents._shared import AGENT_OPTIONS, DEFAULT_AGENT

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
        "servers": cm.get_accessible_servers(user.id),
        # Every Agent is chattable for the same reason it is consultable: it has
        # an identity and a toolset. No separate flag (FEAT-004 rule).
        #
        # Condor is the exception the picker already has a row for: it answers
        # when nothing is bound, so `list_specialists` leaves it out rather than
        # offering the same identity twice.
        "agent_bindings": [
            {
                "slug": a.slug,
                "name": a.name,
                "description": a.description,
                "when_to_consult": a.when_to_consult,
            }
            for a in AgentStore().list_specialists()
        ],
        "default_agent": DEFAULT_AGENT,
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
    except UnknownAgent as exc:
        raise HTTPException(status_code=404, detail=str(exc))
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


class SessionAction(BaseModel):
    """Body of the multiplexed action endpoint."""

    action: str
    agent_slug: str | None = None
    agent_key: str | None = None
    server_name: str | None = Field(
        default=None,
        description="Move the conversation to this Hummingbot API server. The "
        "server is baked into the MCP subprocess at spawn, so this is a "
        "respawn like any other switch. None keeps the current one.",
    )
    handoff: str = Field(
        default="",
        description="Short recap of the conversation so far, carried into the "
        "new session's opening context when switching.",
    )


@router.post("/{key}/action")
async def session_action(
    key: str,
    body: SessionAction,
    user: WebUser = Depends(get_current_user),
):
    """One multiplexed endpoint for the small lifecycle verbs."""
    parsed = _parse_key(key)
    info = await _authorized_info(parsed, user)

    if body.action == "cancel":
        return {"ok": await runtime.abort(parsed)}

    if body.action in ("new", "switch"):
        return await _respawn(parsed, info, body)

    raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")


async def _respawn(key: SessionKey, info: SessionInfo, body: SessionAction) -> dict:
    """Tear the session down and bring it back under the same key.

    ACP has no identity hot-swap, so switching who is answering means reaping
    the subprocess and spawning a new one. What carries over is the
    conversation: ``switch`` keeps answering into the same transcript, which the
    runtime replays into the new session's opening context, so the switch now
    happens *inside* one conversation instead of ending it. ``new`` deliberately
    does not — a new session is a new chat.

    ``handoff`` stays on the wire for the shipped dashboard, appended after the
    replay when a client still sends one.
    """
    from condor.preferences import load_user_data_for

    switching = body.action == "switch"

    # The new session's tools are built against this server's credentials, so
    # gate it exactly like consult/delegate do — otherwise a switch would be a
    # way to reach a server the caller was never granted (IDOR). Checked before
    # the teardown, so a refused switch leaves the session running as it was.
    if body.server_name and not get_config_manager().has_server_access(
        info.user_id or 0, body.server_name
    ):
        raise HTTPException(status_code=403, detail="No access")

    # Unspecified fields keep their current value, so "new" is just a switch
    # to the same identity.
    agent_slug = info.agent_slug if body.agent_slug is None else body.agent_slug
    spec = SessionSpec(
        key=str(key),
        agent_key=body.agent_key if body.agent_key is not None else info.agent_key,
        user_id=info.user_id,
        # A pinned Agent still wins inside binding._resolve_agent — resolution
        # lives in one place, so nothing is special-cased here.
        server_name=info.server_name if body.server_name is None else body.server_name,
        agent_slug=agent_slug,
        conversation_id=info.conversation_id if switching else "",
        # Deferred so the new identity lands on the user's next prompt rather
        # than blocking this request on a model round trip.
        lazy_context=True,
        extra_context=(
            f"Previously in this chat:\n{body.handoff}" if body.handoff else ""
        ),
    )

    await runtime.destroy(key)
    try:
        new_info = await runtime.create_session(
            spec, user_data=load_user_data_for(info.user_id)
        )
    except UnknownAgent as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - surfaced as an HTTP error
        log.exception("Failed to respawn session %s", key)
        raise HTTPException(status_code=400, detail=str(exc))

    if switching and info.conversation_id and body.server_name is not None:
        # The conversation, not the subprocess, is what a reload comes back to
        # (chat_ws resumes from ``conv.server_name``), so a switch that only
        # touched the live session would be undone by the next page load.
        conversations.update_meta(
            info.user_id,
            info.conversation_id,
            server_name=new_info.server_name,
        )
        # Tool results before and after this line came from different accounts.
        # Only mark it when the effective server actually moved: a pinned Agent
        # ignores the request, and a divider claiming otherwise would lie.
        if new_info.server_name != info.server_name:
            conversations.record_system(
                info.user_id,
                info.conversation_id,
                f"Now using server {new_info.server_name}",
                kind="switch",
            )

    if switching and info.conversation_id and body.agent_slug is not None:
        # The handover, marked in the transcript itself, so it reads as a
        # divider between the two identities' turns rather than as a header
        # that silently changed. Recorded after the respawn: nothing is
        # appended in between, so the position is the same, and a switch that
        # failed to spawn leaves no divider claiming it happened.
        conversations.record_system(
            info.user_id,
            info.conversation_id,
            f"Switched to {new_info.label}",
            kind="switch",
        )

    return {"ok": True, "session": new_info.model_dump(mode="json")}
