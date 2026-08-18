"""Admin-only user administration for the web dashboard (ARCH-177).

Today this carries exactly one capability, the ``code_run`` grant SEC-151
introduced: the gate in ``routes/code.py`` is *admin or an explicit per-user
grant*, and until now the only way to set that grant was hand-editing
config.yml. This is where an admin sets it instead.

Every route here is admin-only, checked server-side by ``_require_admin``. The
dashboard hides the panel from non-admins, but that is cosmetic — hiding a
control is not a gate, so each handler asks again and answers 403 whatever the
client believes it is.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from condor.web.auth import get_current_user
from condor.web.models import WebUser
from config_manager import UserRole, get_config_manager

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(user: WebUser) -> None:
    """Refuse anyone who is not a Condor admin.

    Mirrors the shape of the telemetry gate in ``routes/settings.py``: the role
    is re-read from the ConfigManager rather than trusted from the JWT claim,
    so demoting a user takes effect on their next request.
    """
    if not get_config_manager().is_admin(user.id):
        raise HTTPException(
            status_code=403,
            detail="Only an admin can administer users",
        )


class AdminUser(BaseModel):
    """A user row as the admin panel needs it."""

    user_id: int
    username: str = ""
    role: str = ""
    # Whether the explicit grant is set on this record. Admins are reported
    # separately via `is_admin`: they can already run code without a grant, and
    # showing them as "granted" would misrepresent what revoking would do.
    code_run: bool = False
    is_admin: bool = False


@router.get("/users", response_model=list[AdminUser])
async def list_users(user: WebUser = Depends(get_current_user)):
    """Every user record, with the code_run grant each one carries."""
    _require_admin(user)
    cm = get_config_manager()
    rows = []
    for record in cm.get_all_users():
        uid = record.get("user_id")
        if uid is None:
            continue
        rows.append(
            AdminUser(
                user_id=uid,
                username=record.get("username") or "",
                role=record.get("role") or "",
                code_run=cm.has_code_run_grant(uid),
                is_admin=record.get("role") == UserRole.ADMIN.value,
            )
        )
    rows.sort(key=lambda r: (not r.is_admin, r.username.lower(), r.user_id))
    return rows


class CodeRunGrantRequest(BaseModel):
    granted: bool


@router.put("/users/{user_id}/code-run")
async def set_code_run_grant(
    user_id: int,
    body: CodeRunGrantRequest,
    user: WebUser = Depends(get_current_user),
):
    """Grant or revoke ``code_run`` for one user.

    The grant confers arbitrary Python execution inside the bot process — it is
    admin-equivalent in practice — so it is admin-only, audited by
    ``set_code_run_grant``, and takes effect on the next request without a
    restart because the gate re-reads config.yml every time.

    Granting to an admin is refused rather than silently stored: they already
    pass the gate on the admin arm, so the grant would be a no-op record that
    survives a demotion and quietly outlives the role that justified it.
    """
    _require_admin(user)
    cm = get_config_manager()

    if cm.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown user {user_id}")
    if body.granted and cm.is_admin(user_id):
        raise HTTPException(
            status_code=409,
            detail="Admins can already run code; no grant is needed",
        )

    changed = cm.set_code_run_grant(user_id, body.granted, admin_id=user.id)
    if changed:
        log.warning(
            "Admin %s %s code_run for user %s",
            user.id,
            "granted" if body.granted else "revoked",
            user_id,
        )
    return {"user_id": user_id, "code_run": cm.has_code_run_grant(user_id)}
