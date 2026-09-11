"""Pending approvals as an addressable resource.

Lets an approval raised while the chat panel was closed — or stranded by a page
reload that killed the WebSocket — still be answered. The WS
``permission_request`` event remains the low-latency path; this is the durable
one.

There is deliberately no ``register`` endpoint: only the runtime may invent an
approval request. Everything here stays behind ``get_current_user``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from condor.runtime import slot_of
from condor.runtime.confirmations import get_registry
from condor.web.auth import get_current_user
from condor.web.models import WebUser

log = logging.getLogger(__name__)

router = APIRouter(prefix="/confirmations", tags=["confirmations"])


class ResolveRequest(BaseModel):
    approved: bool
    option_id: str = ""


@router.get("")
async def list_confirmations(user: WebUser = Depends(get_current_user)):
    """Approvals this user still needs to answer.

    Read by the dashboard on every socket open (``useChatSocket``), which is
    what makes the reload case above real: the ``permission_request`` event was
    pushed to a socket that no longer exists, and this is the only way the new
    page learns the agent is still waiting. ``slot_id`` addresses each entry the
    same way that event does, so a click meant for one conversation cannot
    authorize another one's tool call.
    """
    return [
        {**p.to_wire(), "slot_id": slot_of(p.session_key)}
        for p in get_registry().list_pending(user_id=user.id)
    ]


@router.get("/{confirmation_id}")
async def get_confirmation(
    confirmation_id: str, user: WebUser = Depends(get_current_user)
):
    pending = get_registry().get(confirmation_id)
    if pending is None:
        raise HTTPException(status_code=404, detail="No such confirmation")
    if pending.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your confirmation")
    return pending.to_wire()


@router.post("/{confirmation_id}/resolve")
async def resolve_confirmation(
    confirmation_id: str,
    body: ResolveRequest,
    user: WebUser = Depends(get_current_user),
):
    """Answer an approval.

    ``ok: false`` covers unknown, expired, already-answered and not-yours
    alike — the registry never leaks whether someone else's id exists, and an
    answer that lost the race to a Telegram tap is not an error.
    """
    ok = await get_registry().resolve(
        confirmation_id,
        approved=body.approved,
        by_user_id=user.id,
        option_id=body.option_id,
    )
    return {"ok": ok}
