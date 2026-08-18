"""Build identity for the dashboard.

One endpoint, and it exists for a single reason: a bug report that does not say
which commit it came from costs a round trip to find out. The fields are the
same ones the telemetry context already computes for its own envelope — a short
commit, a branch, a runtime — so nothing new is collected here, it is only shown
to the logged-in user who is about to paste it into an issue.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

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
