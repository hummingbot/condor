"""Ad-hoc code execution — the crossing into the main process (FEAT-047).

The MCP subprocess cannot run a snippet itself: the cached API clients, the
report index's single writer and the real bot all live here. So ``run_code``
POSTs its snippet to this route, exactly as ``manage_routines(action="run")``
already delegates a routine run — same destination, same context, one pattern
for the next reader of ``tools/code.py`` beside ``tools/routines.py``.

There are no GET endpoints on purpose: history is read straight off disk by the
subprocess, which shares the filesystem, just as routine CRUD is.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from condor.code_runner import DEFAULT_TIMEOUT, MAX_TIMEOUT, execute_code
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from config_manager import get_config_manager

log = logging.getLogger(__name__)
router = APIRouter(prefix="/code", tags=["code"])


class RunCodeRequest(BaseModel):
    code: str
    # The server the snippet's client resolves against. Empty is legal: a
    # snippet that only needs pandas must not be refused for lack of a server.
    server_name: str = ""
    label: str = ""
    timeout: float = DEFAULT_TIMEOUT
    # Which assistant produced this run's reports, same field and same meaning
    # as RunRequestV2.attribute_to.
    attribute_to: str = ""
    session_key: str = ""


@router.post("/run")
async def run_code(
    body: RunCodeRequest,
    user: WebUser = Depends(get_current_user),
):
    """Execute a Python snippet in this process and return its recorded run."""
    if not body.code.strip():
        raise HTTPException(400, "code is required")
    if body.server_name:
        cm = get_config_manager()
        if not cm.has_server_access(user.id, body.server_name):
            raise HTTPException(status_code=403, detail="No access")

    return await execute_code(
        body.code,
        server_name=body.server_name,
        agent=body.attribute_to,
        label=body.label,
        timeout=max(1.0, min(float(body.timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT)),
        # The caller's own id, never a value from the body: it is what resolves
        # the snippet's client, so it must be the authenticated identity — the
        # same one RoutineStore.execute runs a routine under.
        chat_id=user.id,
        session_key=body.session_key,
    )
