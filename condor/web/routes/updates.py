"""The admin's update surface on the web (FEAT-071).

A *view* over :mod:`condor.updates`, in the same sense ``handlers/admin/update``
is one. There is no git, docker or build call in this module and no response
shaping beyond the engine's own ``to_wire()`` — if a version question needs
answering, it is answered in the engine, where Telegram gets the same answer.

The shape of this router is set by one fact: **starting a Condor update kills
the process serving these requests.** So nothing here is a session. ``start``
returns 202 with a run id and lets the engine run in the background; the panel
learns what happened by polling ``GET /updates/run``, which reads the journal
the engine writes at every transition. The restart is then just a run of failed
fetches between two reads of a file that outlives the process.

Because the engine keys on a single current run, an update started from
Telegram is visible here and vice versa. That is not built, it falls out:
``updates.start`` hands back the run already in flight instead of queueing a
second one.

Admin-only, re-checked in every handler. An update restarts the process and can
reap running executors — exactly the authority the admin role already carries,
so there is no new permission concept here.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from condor import updates
from condor.web.auth import get_current_user, require_admin
from condor.web.models import WebUser

log = logging.getLogger(__name__)
router = APIRouter(prefix="/updates", tags=["updates"])


class ComponentsRequest(BaseModel):
    """The components an action applies to. Unknown keys are dropped by the engine."""

    components: list[str] = Field(default_factory=list)


class ResolveRequest(BaseModel):
    """Act on a blocker's offered resolution.

    Deliberately carries no path list. The engine recomputes which paths
    conflict at the moment the button is pressed rather than trusting the
    screen, which may be minutes old — discarding a path that has since stopped
    conflicting would destroy work nobody was warned about. A route that
    accepted paths would be handing the caller a delete-arbitrary-files
    primitive dressed as an update control.
    """

    component: str
    action: str


@router.get("")
async def get_status(user: WebUser = Depends(get_current_user)):
    """What each component is running and what is available. 60s engine cache."""
    require_admin(user)
    statuses = await updates.check()
    return {"components": [s.to_wire() for s in statuses]}


@router.post("/check")
async def force_check(user: WebUser = Depends(get_current_user)):
    """The same, past the cache — the panel's Refresh button."""
    require_admin(user)
    statuses = await updates.check(force=True)
    return {"components": [s.to_wire() for s in statuses]}


@router.post("/preflight")
async def get_preflight(
    body: ComponentsRequest, user: WebUser = Depends(get_current_user)
):
    """Blockers, warnings and the ordered plan, without starting anything."""
    require_admin(user)
    result = await updates.preflight(body.components)
    return result.to_wire()


@router.post("/resolve")
async def resolve_block(
    body: ResolveRequest, user: WebUser = Depends(get_current_user)
):
    """Clear a blocker with one of the resolutions it offered."""
    require_admin(user)
    ok, message = await updates.resolve(body.component, body.action)
    return {"ok": ok, "message": message}


@router.post("/start", status_code=202)
async def start_update(
    body: ComponentsRequest, user: WebUser = Depends(get_current_user)
):
    """Begin an update and answer immediately with the run to watch.

    202, never 200: the work outlives the request by design, and for a Condor
    update it outlives the *process*. If a run is already in flight its id comes
    back rather than a 409 — a second caller is a second surface watching, not a
    second update, and answering 409 would break "start in Telegram, watch here".
    """
    require_admin(user)
    run = await updates.start(
        body.components,
        actor_user_id=user.id,
        actor_chat_id=user.id,
    )
    return {"run_id": run.id, "state": run.state}


@router.get("/run")
async def get_run(user: WebUser = Depends(get_current_user)):
    """The run in flight, the one this process last finished, or the journal.

    In-process state first so a live run reports its current step without a
    file read; the journal is the fallback that carries a run across the
    restart that interrupted it — by the time this answers again, the boot hook
    has already judged it.
    """
    require_admin(user)
    run = updates.current() or updates.read_journal()
    return {"run": run.to_wire() if run is not None else None}
