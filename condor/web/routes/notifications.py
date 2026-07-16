"""Notifications outbox read API (see condor/notifications.py)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from condor.notifications import read_notifications

router = APIRouter(tags=["notifications"])


@router.get("/notifications")
async def list_notifications(
    limit: int = 50,
    since_ts: Optional[float] = None,
    agent_id: Optional[str] = None,
):
    return {
        "notifications": read_notifications(
            limit=limit, since_ts=since_ts, agent_id=agent_id
        )
    }
