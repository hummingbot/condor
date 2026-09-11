"""Ad-hoc code execution — the crossing into the main process (FEAT-047).

The MCP subprocess cannot run a snippet itself: the cached API clients, the
report index's single writer and the real bot all live here. So ``run_code``
POSTs its snippet to this route, exactly as ``manage_routines(action="run")``
already delegates a routine run — same destination, same context, one pattern
for the next reader of ``tools/code.py`` beside ``tools/routines.py``.

The *history* is not served here for the subprocess's sake: it reads the store
straight off disk, which it can, because it shares the filesystem. A browser
does not — so FEAT-061 added exactly one GET, for one run's body, and left the
listing where the dashboard already reads every other kind of run
(``/agents/delegations/history``).

A snippet runs unsandboxed in this process, so reaching this route *is* reaching
everything the bot can reach — config.yml, the web JWT secret, os.environ. The
server ACL below only picks which client is pre-resolved; it cannot constrain a
body. The real gate is therefore ``_may_run_code`` (SEC-151).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from condor.code_runner import DEFAULT_TIMEOUT, MAX_TIMEOUT, execute_code
from condor.code_runs import get_code_run_store
from condor.web.auth import check_server_access, get_current_user
from condor.web.models import WebUser
from config_manager import CODE_RUN_PREFERENCE, get_config_manager

log = logging.getLogger(__name__)
router = APIRouter(prefix="/code", tags=["code"])

# The preference an admin sets on a user record to hand them the code runner.
# The spelling lives in config_manager beside the setter an admin drives it
# from (ARCH-177); re-exported here because this is where it is enforced.
RUN_CODE_PREFERENCE = CODE_RUN_PREFERENCE


def _may_run_code(user_id: int) -> bool:
    """Who is allowed to execute a snippet in the bot's own process.

    Being approved is not enough. ``execute_code`` compiles with full builtins
    and no sandbox, so any snippet can read every server's credentials, the web
    JWT secret and ``os.environ`` no matter which servers its caller was granted
    — running code here is admin-equivalent by construction, and gating it on
    ``UserRole.USER`` silently voided the whole role model (SEC-151).

    Admins therefore pass, and anyone else only with the explicit
    ``code_run`` preference an admin sets on their record. That opt-in is what
    keeps the door open for a non-admin seat whose MCP ``run_code`` tool is a
    legitimate part of their workflow: it is a grant, not a default.
    """
    cm = get_config_manager()
    return cm.is_admin(user_id) or bool(
        cm.get_user_preference(user_id, RUN_CODE_PREFERENCE, False)
    )


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
    if not _may_run_code(user.id):
        # Before the 400 and before any store write: a refused caller must not
        # learn anything, and must leave no CodeRun behind.
        log.warning("Denied /code/run to user %s (not admin, no grant)", user.id)
        raise HTTPException(status_code=403, detail="Not allowed to run code")
    if not body.code.strip():
        raise HTTPException(400, "code is required")
    if body.server_name:
        check_server_access(user.id, body.server_name)

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


@router.get("/runs/{run_id}")
async def get_code_run(run_id: str, user: WebUser = Depends(get_current_user)):
    """One recorded run in full — its code, stdout, result and traceback.

    Gated twice, and both gates are the point. ``_may_run_code`` first, because
    a run's stdout is whatever the snippet printed and a snippet can print
    ``os.environ``: reading one is as sensitive as producing it, so it sits
    behind the same door in the same file. Then ownership, because the store is
    global and a grant is not a licence to read a colleague's output.

    A run with no owner — everything written before FEAT-061 — is admin-only
    rather than world-readable, the same fail-closed rule ownerless reports
    follow (SEC-196).

    404, never 403, for a run this caller may not have: the id is opaque and
    guessable ids are not a thing worth confirming. Unknown, illegal and
    forbidden ids are one answer, and ``CodeRunStore.get`` already refuses an id
    that is not a legal id rather than turning it into a path.
    """
    if not _may_run_code(user.id):
        log.warning("Denied /code/runs to user %s (not admin, no grant)", user.id)
        raise HTTPException(status_code=403, detail="Not allowed to run code")

    run = get_code_run_store().get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No code run '{run_id}'")

    owner = run.get("user_id") or 0
    if not get_config_manager().is_admin(user.id) and owner != user.id:
        raise HTTPException(status_code=404, detail=f"No code run '{run_id}'")
    return run
