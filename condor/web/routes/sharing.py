"""Sharing a conversation, over HTTP (FEAT-054).

Four routes and one rule that is not inherited from anywhere: **sharing is the
owner's act alone.** ``conversations.py``'s ``_owner`` lets an admin *read*
someone else's conversation by naming them in ``?user_id=``, which is right for
support and for the admin panel. It is not right here — the content belongs to
the person who said it, and an admin holding the keys to the box is not the
same as an admin holding consent for its contents. So the check is written out
explicitly below rather than reached through the shared helper, because a rule
that only holds while nobody refactors the helper is not a rule.

The other three gates live in :mod:`condor.sharing.consent` and are checked
here, at the boundary, where the caller's identity is actually known:
``CONDOR_SHARING=off``, the admin's install-wide veto, and — for the veto's own
route — that the caller is the admin.

Note: modules under ``condor/web/routes/`` are not in ``main.py``'s hot-reload
list, so changes here need a full bot restart.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from condor.runtime.conversations import ConversationIdError
from condor.sharing import consent, share
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from config_manager import get_config_manager

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sharing", tags=["sharing"])

SHARING_DISABLED = "Conversation sharing is turned off on this install"


def _sharer(user: WebUser) -> int:
    """The only id this module will ever act on: the caller's own.

    There is deliberately no ``?user_id=`` here. An admin may read another
    user's conversation through ``/conversations`` and still cannot share it,
    because there is no parameter through which to name it.
    """
    if not consent.can_share(user.id):
        raise HTTPException(status_code=403, detail=SHARING_DISABLED)
    return user.id


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, ConversationIdError):
        return HTTPException(status_code=400, detail="Malformed conversation id")
    return HTTPException(status_code=404, detail="No such conversation")


class SharingSettings(BaseModel):
    """What this install allows, and who may change it."""

    enabled: bool
    env_overridden: bool
    can_change: bool
    endpoint_configured: bool
    pending: int


class SharingUpdate(BaseModel):
    enabled: bool


# ── One conversation ─────────────────────────────────────────────────────


@router.get("/conversations/{conversation_id}/preview")
async def preview_share(
    conversation_id: str,
    user: WebUser = Depends(get_current_user),
):
    """The redacted transcript exactly as it would be sent. Sends nothing.

    This is what the dialog renders, and :func:`submit_share` runs the identical
    code path — so what the user approves is what leaves.
    """
    owner_id = _sharer(user)
    try:
        return share.preview(owner_id, conversation_id).model_dump(mode="json")
    except (share.ConversationMissing, ConversationIdError) as exc:
        raise _handle(exc) from exc


@router.post("/conversations/{conversation_id}")
async def submit_share(
    conversation_id: str,
    user: WebUser = Depends(get_current_user),
):
    """Queue this conversation for delivery. The consent is the request itself."""
    owner_id = _sharer(user)
    try:
        receipt = share.submit(owner_id, conversation_id)
    except (share.ConversationMissing, ConversationIdError) as exc:
        raise _handle(exc) from exc
    return receipt.model_dump(mode="json")


@router.delete("/conversations/{conversation_id}")
async def unshare_conversation(
    conversation_id: str,
    user: WebUser = Depends(get_current_user),
):
    """Take a shared conversation back.

    Reachable even when the install veto is on: an admin turning sharing off
    must not strand a user with something they can no longer revoke. The gate
    for this route is ownership alone.
    """
    try:
        removed = share.unshare(user.id, conversation_id)
    except (share.ConversationMissing, ConversationIdError) as exc:
        raise _handle(exc) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="That conversation is not shared")
    return {"unshared": True, "conversation_id": conversation_id}


@router.get("/conversations")
async def list_shared(user: WebUser = Depends(get_current_user)):
    """Everything this user currently has out there, for Settings → Privacy."""
    return share.list_shares(user.id)


# ── The install-wide switch ──────────────────────────────────────────────


@router.get("/settings", response_model=SharingSettings)
async def get_sharing_settings(user: WebUser = Depends(get_current_user)):
    """Readable by every seat: anyone using an install should be able to see
    what it is allowed to send. ``can_change`` reports who may answer."""
    from condor.sharing import outbox

    return SharingSettings(
        enabled=consent.install_allows() and consent.env_allows(),
        env_overridden=consent.env_overridden(),
        can_change=get_config_manager().is_admin(user.id),
        endpoint_configured=bool(outbox.endpoint()),
        pending=len(outbox.pending()),
    )


@router.put("/settings", response_model=SharingSettings)
async def set_sharing_settings(
    body: SharingUpdate,
    user: WebUser = Depends(get_current_user),
):
    """Flip the install-wide veto. Admin only, and reversible.

    The wording of the refusal mirrors the telemetry setting's, because the
    shape of the rule is the same one: an install-wide switch belongs to whoever
    owns the install.
    """
    if not get_config_manager().is_admin(user.id):
        raise HTTPException(
            status_code=403,
            detail=(
                "Conversation sharing is an install-wide setting; "
                "only the admin can change it"
            ),
        )
    if consent.env_overridden():
        raise HTTPException(
            status_code=409,
            detail=(
                f"{consent.ENV_VAR} is set in the environment "
                "and overrides this setting"
            ),
        )
    consent.set_install_allows(body.enabled)
    return await get_sharing_settings(user)
