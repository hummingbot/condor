"""Build identity for the dashboard: what this process is, and whether it is stale.

``/env`` exists for a single reason: a bug report that does not say which commit
it came from costs a round trip to find out. The fields are the same ones the
telemetry context already computes for its own envelope — a short commit, a
branch, a runtime — so nothing new is collected here, it is only shown to the
logged-in user who is about to paste it into an issue.

``/relaunch`` answers the other half of the same question. A Condor update lands
the new code and rebuilds the dashboard bundle, but deliberately does not exec
itself (see :mod:`condor.updates.run`), so between the update and the relaunch
the browser is running new frontend against old backend. Every seat is told,
not just the admin's: the mismatch is what a non-admin actually experiences,
and "relaunch to apply" explains a page that otherwise just looks broken.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from condor import updates
from condor.telemetry import context
from condor.web.auth import get_current_user
from condor.web.models import WebUser

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/env")
async def get_env(user: WebUser = Depends(get_current_user)) -> dict:
    """Version and platform of this install.

    Authenticated: the commit an install runs is not a secret, but it is a
    detail about someone's deployment and there is no reason to hand it to an
    anonymous caller.
    """
    app = context.app()
    return {
        "version": app["version"],
        "branch": app["branch"],
        "python": app["python"],
        "os": app["os"],
        "arch": app["arch"],
        "in_docker": app["in_docker"],
    }


@router.get("/relaunch")
async def get_relaunch(user: WebUser = Depends(get_current_user)) -> dict:
    """Whether this process is older than the code on disk, and by how much.

    ``required`` is false for every install that has not updated in place —
    which is nearly all of them, nearly all of the time — so the banner this
    feeds costs one cheap poll and renders nothing.
    """
    pending = updates.relaunch_pending()
    if pending is None:
        return {"required": False}
    return {
        "required": True,
        "branch": pending.get("branch") or "",
        "from_commit": (pending.get("from_commit") or "")[:7],
        "target_commit": (pending.get("target_commit") or "")[:7],
        "at": pending.get("at"),
    }
