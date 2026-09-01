"""Admin-only people administration for the web dashboard (ARCH-177, FEAT-088).

The noun here is a **person**: who they are, what role they hold, and which
servers they can reach. Before FEAT-088 this module carried one capability
toggle over a list of bare user ids, and server access — the thing an admin
actually has to decide when someone joins — had no web surface at all. It could
only be granted through a one-shot screen during Telegram approval, and could
never be revoked from anywhere.

Nothing here is a new authorization primitive. Every mutation goes through a
``ConfigManager`` verb that already enforced its own rules and already wrote the
audit log; these routes only make those verbs reachable, and report the refusals
faithfully rather than papering over them.

Every route is admin-only, checked server-side by ``require_admin``. The
dashboard hides the panel from non-admins, but that is cosmetic — hiding a
control is not a gate, so each handler asks again and answers 403 whatever the
client believes it is.

**This module is not hot-reloaded**: ``main.py``'s reload list does not cover
``condor/web/routes/*.py``, so a change here needs a full bot restart. The route
logic is pinned by ``tests/test_admin_people_routes.py`` for exactly that reason.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from condor.web.auth import get_current_user, require_admin
from condor.web.models import WebUser
from config_manager import (
    ServerPermission,
    UserRole,
    get_config_manager,
    user_display_name,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

# How many `getChat` round-trips the backfill has in flight at once, and how long
# any one of them may take. Both matter because this runs on the bot's own event
# loop: a dozen sequential Telegram calls would stall polling for the duration,
# and a single unresponsive one would stall it indefinitely.
_REFRESH_CONCURRENCY = 5
_REFRESH_TIMEOUT_SEC = 10


class ServerGrant(BaseModel):
    """One person's access to one server."""

    server: str
    permission: str = ""  # "" | "trader" | "owner"
    # True when the access follows from being an admin rather than from a grant.
    # `get_server_permission` short-circuits to OWNER for every admin, so
    # rendering that as a toggle would promise a revoke that cannot happen.
    implicit: bool = False


class AdminPerson(BaseModel):
    """A person as the admin panel needs them: identity, role, and reach."""

    user_id: int
    display_name: str
    username: str = ""
    first_name: str = ""
    last_name: str = ""
    role: str = ""  # admin | user | pending | blocked
    is_admin: bool = False
    # Whether the explicit grant is set on this record. Admins are reported
    # separately via `is_admin`: they can already run code without a grant, and
    # showing them as "granted" would misrepresent what revoking would do.
    code_run: bool = False
    created_at: float = 0.0
    approved_at: float = 0.0
    approved_by: int | None = None
    last_seen: float = 0.0
    servers: list[ServerGrant] = []
    # False for an id that appears only in some server's `shared_with` and has
    # no `users` record at all. Such a grant is live and unrevokable from
    # anywhere in the product today; surfacing the row is what makes it fixable.
    known: bool = True


class AdminUser(BaseModel):
    """The pre-FEAT-088 row shape, kept for the ``/users`` alias below."""

    user_id: int
    username: str = ""
    role: str = ""
    code_run: bool = False
    is_admin: bool = False


class AuditEntry(BaseModel):
    """One audit-log line, with both parties named rather than numbered."""

    timestamp: float = 0.0
    action: str = ""
    actor_id: int | None = None
    actor_name: str = ""
    target_type: str = ""
    target_id: str = ""
    target_name: str = ""
    details: dict | None = None


# ── reading people ──


def _grants_for(cm, user_id: int, is_admin: bool) -> list[ServerGrant]:
    """Every registered server, and what this person's access to it is.

    Reports the *real* grant. An admin's blanket access is marked ``implicit``
    and left at ``owner`` because that is what ``get_server_permission`` answers
    for them — it is a consequence of the role, and the panel must not offer to
    take away something revoking cannot touch.
    """
    grants = []
    for server in cm.list_registered_servers():
        if is_admin:
            grants.append(ServerGrant(server=server, permission="owner", implicit=True))
            continue
        perm = cm.get_server_permission(user_id, server)
        grants.append(ServerGrant(server=server, permission=perm.value if perm else ""))
    return grants


def _person(cm, user_id: int, record: dict | None) -> AdminPerson:
    """Build one row from a users record, or from nothing at all."""
    record = record or {}
    known = bool(record)
    role = str(record.get("role") or "")
    is_admin = role == UserRole.ADMIN.value

    return AdminPerson(
        user_id=user_id,
        display_name=user_display_name(record, user_id),
        username=str(record.get("username") or ""),
        first_name=str(record.get("first_name") or ""),
        last_name=str(record.get("last_name") or ""),
        role=role,
        is_admin=is_admin,
        code_run=cm.has_code_run_grant(user_id) if known else False,
        created_at=float(record.get("created_at") or 0.0),
        approved_at=float(record.get("approved_at") or 0.0),
        approved_by=record.get("approved_by"),
        last_seen=float(record.get("last_seen") or 0.0),
        servers=_grants_for(cm, user_id, is_admin),
        known=known,
    )


def _all_people(cm) -> list[AdminPerson]:
    """Everyone the config knows about, in the order the panel reads them.

    The list is the union of ``users`` with every id that appears in any
    server's ``shared_with``. Without the union, a grant to a user record that
    no longer exists is an invisible hole in config.yml: nothing in the product
    can show it and nothing can revoke it. With it, that grant is a row.
    """
    users = {r["user_id"]: r for r in cm.get_all_users() if r.get("user_id")}
    ids = set(users) | cm.get_granted_user_ids()

    rows = [_person(cm, uid, users.get(uid)) for uid in ids]
    # Admins first, then pending (the reason anyone opens this tab), then the
    # rest by name; unknown ids sink to the bottom, where the warning belongs.
    order = {
        UserRole.ADMIN.value: 0,
        UserRole.PENDING.value: 1,
        UserRole.USER.value: 2,
        UserRole.BLOCKED.value: 3,
    }
    rows.sort(
        key=lambda r: (
            order.get(r.role, 4),
            r.display_name.lower(),
            r.user_id,
        )
    )
    return rows


@router.get("/people", response_model=list[AdminPerson])
async def list_people(user: WebUser = Depends(get_current_user)):
    """Every person, with their role, their grants and their server access."""
    require_admin(user)
    return _all_people(get_config_manager())


@router.get("/users", response_model=list[AdminUser])
async def list_users(user: WebUser = Depends(get_current_user)):
    """The pre-FEAT-088 shape of :func:`list_people`, kept for one release.

    ``Settings.tsx`` decides whether the Admin *and* Updates tabs exist at all
    from whether this module's list route answers or 403s. A browser holding the
    previous bundle still probes this path, and moving it without leaving the
    alias would hide the Updates tab from an admin mid-session.
    """
    require_admin(user)
    cm = get_config_manager()
    rows = [
        AdminUser(
            user_id=p.user_id,
            username=p.username,
            role=p.role,
            code_run=p.code_run,
            is_admin=p.is_admin,
        )
        for p in _all_people(cm)
        if p.known
    ]
    rows.sort(key=lambda r: (not r.is_admin, r.username.lower(), r.user_id))
    return rows


# ── role ──


class RoleRequest(BaseModel):
    """The state the caller wants this person to end up in.

    A destination, not a transition: the client says where the person should
    land and the server picks the verb, so the table of illegal moves lives in
    one place instead of being re-derived by every caller. ``rejected`` is the
    absence of a record — it deletes the registration outright.
    """

    role: str  # "user" | "pending" | "blocked" | "rejected"


_ROLE_TARGETS = {"user", "pending", "blocked", "rejected"}


def _apply_role(cm, target_id: int, current: str, target: str, admin_id: int) -> None:
    """Move ``target_id`` to ``target``, or refuse with the reason why.

    The refusal table, once, in the order the panel can hit it. Every arm either
    delegates to a ConfigManager verb — which audits — or raises; a verb that
    returns False is a rule this function failed to anticipate, and it becomes a
    409 rather than a silent no-op that the UI would render as success.
    """
    if target not in _ROLE_TARGETS:
        raise HTTPException(status_code=400, detail=f"Unknown role '{target}'")

    if current == UserRole.ADMIN.value:
        raise HTTPException(
            status_code=409,
            detail="An admin's role is set by ADMIN_USER_ID, not from here",
        )

    if target == current or (target == "user" and current == UserRole.USER.value):
        return  # already there; idempotent by design, the UI re-reads either way

    if target == "user":
        if current == UserRole.BLOCKED.value:
            raise HTTPException(
                status_code=409,
                detail="Unblock this user first — unblocking returns them to pending",
            )
        ok = cm.approve_user(target_id, admin_id)
    elif target == "rejected":
        if current != UserRole.PENDING.value:
            raise HTTPException(
                status_code=409,
                detail="Only a pending request can be rejected; block them instead",
            )
        ok = cm.reject_user(target_id, admin_id)
    elif target == "blocked":
        if target_id == admin_id:
            raise HTTPException(status_code=409, detail="You cannot block yourself")
        ok = cm.block_user(target_id, admin_id)
    else:  # "pending"
        if current != UserRole.BLOCKED.value:
            raise HTTPException(
                status_code=409,
                detail="Only a blocked user can be returned to pending",
            )
        ok = cm.unblock_user(target_id, admin_id)

    if not ok:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot move {target_id} from '{current or 'unknown'}' to '{target}'",
        )


@router.post("/people/{user_id}/role", response_model=AdminPerson)
async def set_person_role(
    user_id: int,
    body: RoleRequest,
    user: WebUser = Depends(get_current_user),
):
    """Approve, reject, block or unblock one person.

    Answers with the person as they now are, so the client renders the server's
    answer rather than the transition it predicted.
    """
    require_admin(user)
    cm = get_config_manager()

    record = cm.get_user(user_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown user {user_id}")

    _apply_role(cm, user_id, str(record.get("role") or ""), body.role, user.id)
    log.warning("Admin %s set role of user %s to %s", user.id, user_id, body.role)

    # `reject` deletes the record; the row survives only if some server still
    # grants it access, which is exactly the case the panel must keep showing.
    return _person(cm, user_id, cm.get_user(user_id))


# ── server access ──


class ServerGrantRequest(BaseModel):
    permission: str = ""  # "" revokes | "trader" grants


@router.put("/people/{user_id}/servers/{server}", response_model=AdminPerson)
async def set_person_server_access(
    user_id: int,
    server: str,
    body: ServerGrantRequest,
    user: WebUser = Depends(get_current_user),
):
    """Grant or revoke one person's access to one server.

    ``share_server``/``revoke_server_access`` accept an admin as the actor for
    any server, and audit both edges. Two things are deliberately refused here
    rather than delegated:

    * **Granting ``owner``.** ``share_server`` cannot express it — it writes
      ``shared_with``, never ``owner_id`` — so ownership transfer would need a
      second writer on the owner field. It is a different act with different
      consequences and it is out of scope.
    * **Touching an admin's access.** They reach every server by role; a grant
      would be a record that survives a demotion, and a revoke would appear to
      do nothing.
    """
    require_admin(user)
    cm = get_config_manager()

    if server not in cm.list_registered_servers():
        raise HTTPException(status_code=404, detail=f"Unknown server '{server}'")
    if cm.is_admin(user_id):
        raise HTTPException(
            status_code=409,
            detail="Admins reach every server by role; there is no grant to change",
        )
    if user_id == cm.get_server_owner(server):
        raise HTTPException(
            status_code=409,
            detail=f"This user owns '{server}'; ownership is not editable here",
        )

    permission = (body.permission or "").strip().lower()

    if permission == "":
        # Revoking is the safe direction and must work for a record that no
        # longer exists — that is the whole point of surfacing the orphan grant.
        # A user who had no grant is already where the caller asked for.
        cm.revoke_server_access(server, user.id, user_id)
    elif permission == ServerPermission.TRADER.value:
        if not cm.is_approved(user_id):
            raise HTTPException(
                status_code=409,
                detail="Approve this user before granting server access",
            )
        if not cm.share_server(server, user.id, user_id, ServerPermission.TRADER):
            raise HTTPException(
                status_code=409,
                detail=f"Could not grant '{server}' to {user_id}",
            )
    elif permission == ServerPermission.OWNER.value:
        raise HTTPException(
            status_code=409,
            detail="Ownership transfer is not supported here",
        )
    else:
        raise HTTPException(
            status_code=400, detail=f"Unknown permission '{body.permission}'"
        )

    log.warning(
        "Admin %s set %s access on '%s' to '%s'",
        user.id,
        user_id,
        server,
        permission or "none",
    )
    return _person(cm, user_id, cm.get_user(user_id))


# ── capabilities ──


class CodeRunGrantRequest(BaseModel):
    granted: bool


async def _set_code_run(user_id: int, granted: bool, user: WebUser):
    """Grant or revoke ``code_run`` for one user.

    The grant confers arbitrary Python execution inside the bot process — it is
    admin-equivalent in practice — so it is admin-only, audited by
    ``set_code_run_grant``, and takes effect on the next request without a
    restart because the gate re-reads config.yml every time.

    Granting to an admin is refused rather than silently stored: they already
    pass the gate on the admin arm, so the grant would be a no-op record that
    survives a demotion and quietly outlives the role that justified it.
    """
    require_admin(user)
    cm = get_config_manager()

    if cm.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown user {user_id}")
    if granted and cm.is_admin(user_id):
        raise HTTPException(
            status_code=409,
            detail="Admins can already run code; no grant is needed",
        )

    changed = cm.set_code_run_grant(user_id, granted, admin_id=user.id)
    if changed:
        log.warning(
            "Admin %s %s code_run for user %s",
            user.id,
            "granted" if granted else "revoked",
            user_id,
        )
    return cm


@router.put("/people/{user_id}/code-run", response_model=AdminPerson)
async def set_person_code_run(
    user_id: int,
    body: CodeRunGrantRequest,
    user: WebUser = Depends(get_current_user),
):
    """Set the ``code_run`` capability, answering with the whole person."""
    cm = await _set_code_run(user_id, body.granted, user)
    return _person(cm, user_id, cm.get_user(user_id))


@router.put("/users/{user_id}/code-run")
async def set_code_run_grant(
    user_id: int,
    body: CodeRunGrantRequest,
    user: WebUser = Depends(get_current_user),
):
    """The pre-FEAT-088 path and response shape, kept beside ``/users``."""
    cm = await _set_code_run(user_id, body.granted, user)
    return {"user_id": user_id, "code_run": cm.has_code_run_grant(user_id)}


# ── identity backfill ──


def _chat_identity(result) -> dict:
    """Pull names out of whatever ``get_chat`` returned.

    A live python-telegram-bot returns a ``Chat`` object; ``_HttpBot`` returns
    the raw ``{"ok": ..., "result": {...}}`` envelope. Both rungs of
    ``resolve_bot`` are legitimate here, so the difference is absorbed once.
    """
    if isinstance(result, dict):
        result = result.get("result") or {}
        get = result.get
    else:
        get = lambda key: getattr(result, key, None)  # noqa: E731

    return {
        "username": get("username") or "",
        "first_name": get("first_name") or "",
        "last_name": get("last_name") or "",
    }


@router.post("/people/refresh-names")
async def refresh_names(user: WebUser = Depends(get_current_user)):
    """Ask Telegram what the people with no stored name are called.

    Someone who has not spoken to the bot since their record was written is
    never re-captured by ``utils/auth.py`` — the admin's own record is the
    likeliest example, having been minted from ``ADMIN_USER_ID`` with nothing
    but an id. ``getChat`` answers for a private chat the bot knows, which is
    every registered user by construction.

    This runs on the bot's own event loop, so it is capped and timed out: a
    handful of concurrent calls rather than a dozen sequential ones, and no
    single unresponsive chat can hold polling. A user who has blocked the bot is
    a per-record ``failed``, never a 500 — one unreachable person must not cost
    the other nineteen their names.
    """
    require_admin(user)
    cm = get_config_manager()

    from condor.agents.delegate import resolve_bot

    bot = resolve_bot()
    if not hasattr(bot, "get_chat"):
        raise HTTPException(
            status_code=503,
            detail="No Telegram sender available to resolve names",
        )

    targets = [
        record["user_id"]
        for record in cm.get_all_users()
        if record.get("user_id")
        and not record.get("first_name")
        and not record.get("username")
    ]

    semaphore = asyncio.Semaphore(_REFRESH_CONCURRENCY)

    async def resolve(user_id: int) -> bool:
        async with semaphore:
            try:
                chat = await asyncio.wait_for(
                    bot.get_chat(chat_id=user_id), timeout=_REFRESH_TIMEOUT_SEC
                )
            except Exception as e:  # noqa: BLE001 - one unreachable chat, not a 500
                log.info("refresh-names: could not resolve %s: %s", user_id, e)
                return False
            names = _chat_identity(chat)
            if not any(names.values()):
                return False
            cm.touch_user_identity(user_id, **names)
            return True

    outcomes = await asyncio.gather(*(resolve(uid) for uid in targets))
    resolved = sum(1 for ok in outcomes if ok)

    log.info(
        "refresh-names: resolved %s of %s nameless records", resolved, len(targets)
    )
    return {
        "checked": len(targets),
        "resolved": resolved,
        "failed": len(targets) - resolved,
    }


# ── audit ──


@router.get("/audit", response_model=list[AuditEntry])
async def list_audit(limit: int = 50, user: WebUser = Depends(get_current_user)):
    """The audit log, with actor and target resolved to display names.

    ``share_server``, ``revoke_server_access``, ``approve_user``, ``block_user``
    and ``set_code_run_grant`` have been writing this all along and nothing has
    ever shown it. Reading it in the panel that writes it is the point.
    """
    require_admin(user)
    cm = get_config_manager()
    users = {r["user_id"]: r for r in cm.get_all_users() if r.get("user_id")}

    def name_of(raw) -> str:
        try:
            uid = int(raw)
        except (TypeError, ValueError):
            return ""
        return user_display_name(users.get(uid), uid)

    entries = []
    for entry in cm.get_audit_log(limit=max(1, min(limit, 500))):
        target_id = str(entry.get("target_id") or "")
        entries.append(
            AuditEntry(
                timestamp=float(entry.get("timestamp") or 0.0),
                action=str(entry.get("action") or ""),
                actor_id=entry.get("actor_id"),
                actor_name=name_of(entry.get("actor_id")),
                target_type=str(entry.get("target_type") or ""),
                target_id=target_id,
                # Only a user target is a person; a server target is its name.
                target_name=(
                    name_of(target_id) if entry.get("target_type") == "user" else ""
                ),
                details=entry.get("details"),
            )
        )
    return entries
