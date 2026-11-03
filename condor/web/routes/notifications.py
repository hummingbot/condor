"""Task notifications as an addressable resource (FEAT-048).

The WS ``notification`` event (``chat_ws.py``) is the low-latency path; this is
the durable one, so a notice raised while no tab was open is still in the bell
on the next page load.

Everything here is scoped to ``user.id`` from the JWT and never to a
body-supplied id — a notification is addressed to a person, and the store is the
only thing standing between one person's finished tasks and another's.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from condor import notifications
from condor.web.auth import get_current_user
from condor.web.models import WebUser

router = APIRouter(prefix="/notifications", tags=["notifications"])


class MarkReadRequest(BaseModel):
    """Which notifications to mark read. ``None`` (the default) means all."""

    ids: list[str] | None = None


@router.get("")
async def list_notifications(
    limit: int = Query(default=50, ge=1, le=100),
    user: WebUser = Depends(get_current_user),
):
    """This user's notifications, newest first, with the unread count."""
    items = notifications.list_for(user.id, limit=limit)
    return {
        "items": [n.to_wire() for n in items],
        "unread": notifications.unread_count(user.id),
    }


@router.post("/read")
async def mark_notifications_read(
    body: MarkReadRequest, user: WebUser = Depends(get_current_user)
):
    """Mark some (or all) of this user's notifications read."""
    return {"unread": notifications.mark_read(user.id, body.ids)}
