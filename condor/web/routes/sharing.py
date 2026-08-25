"""Sharing a conversation, over HTTP (FEAT-054, FEAT-055).

One rule that is not inherited from anywhere: **sharing is the
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

from condor.runtime import conversations
from condor.runtime.conversations import ConversationIdError
from condor.sharing import consent, share, sweep
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


class SharingPreference(BaseModel):
    """This user's standing answer, and what it means right now.

    ``allowed`` is not the same question as ``state``: a user can be at
    ``always`` on an install whose admin has since vetoed sharing, and the UI
    has to be able to say "your answer is Always, and nothing is going out"
    rather than picking one of the two and hiding the other.
    """

    state: str
    opted_in_at: float = 0.0
    allowed: bool = False
    sweeping: bool = False
    shared_count: int = 0


class SharingPreferenceUpdate(BaseModel):
    state: str


class ConversationSharingStatus(BaseModel):
    """What the chat header chip renders for one conversation."""

    conversation_id: str
    excluded: bool = False
    covered: bool = False
    shared: bool = False
    shared_at: str | None = None


class ExclusionUpdate(BaseModel):
    excluded: bool


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


@router.delete("/conversations")
async def unshare_everything(user: WebUser = Depends(get_current_user)):
    """Take back the whole back catalogue.

    Ungated like the single unshare above and for the same reason: a user must
    always be able to withdraw, whatever the admin has since decided about
    sending. Separate from turning Always off, because they are separate
    decisions (FEAT-055).
    """
    return {"unshared": share.unshare_all(user.id)}


@router.get(
    "/conversations/{conversation_id}/status", response_model=ConversationSharingStatus
)
async def conversation_status(
    conversation_id: str,
    user: WebUser = Depends(get_current_user),
):
    """Whether the sweep would ever take this conversation. What the chip reads.

    Answers for the caller's own conversation only, like every other route here.
    A conversation that does not exist is a 404 rather than an all-false status:
    the chip must not be able to say "this will not be shared" about something it
    simply failed to find.
    """
    try:
        meta = conversations.get_conversation(user.id, conversation_id)
    except ConversationIdError as exc:
        raise _handle(exc) from exc
    if meta is None:
        raise HTTPException(status_code=404, detail="No such conversation")

    return ConversationSharingStatus(
        conversation_id=conversation_id,
        excluded=meta.share_excluded,
        covered=sweep.covered(meta, user.id),
        shared=bool(meta.share_id),
        shared_at=meta.shared_at.isoformat() if meta.shared_at else None,
    )


@router.put(
    "/conversations/{conversation_id}/exclusion",
    response_model=ConversationSharingStatus,
)
async def set_exclusion(
    conversation_id: str,
    body: ExclusionUpdate,
    user: WebUser = Depends(get_current_user),
):
    """Take this one conversation out of the sweep, or put it back.

    Ungated, again: excluding is a refusal, and a refusal must never be behind
    the switch it is refusing. Excluding does **not** unshare a conversation
    already sent — the two verbs are next to each other in the UI and they mean
    different things.
    """
    try:
        meta = conversations.get_conversation(user.id, conversation_id)
    except ConversationIdError as exc:
        raise _handle(exc) from exc
    if meta is None:
        raise HTTPException(status_code=404, detail="No such conversation")

    conversations.update_meta(
        user.id, conversation_id, share_excluded=bool(body.excluded)
    )
    return await conversation_status(conversation_id, user)


# ── The user's standing answer ───────────────────────────────────────────


@router.get("/preference", response_model=SharingPreference)
async def get_preference(user: WebUser = Depends(get_current_user)):
    """This user's own answer. Never another's — there is no id to name one."""
    return SharingPreference(
        state=consent.user_state(user.id),
        opted_in_at=consent.opted_in_at(user.id),
        allowed=consent.can_share(user.id),
        sweeping=consent.can_sweep(user.id),
        shared_count=len(share.list_shares(user.id)),
    )


@router.put("/preference", response_model=SharingPreference)
async def set_preference(
    body: SharingPreferenceUpdate,
    user: WebUser = Depends(get_current_user),
):
    """Record Off / Ask / Always for the caller.

    Choosing ``always`` is the only answer that authorizes anything, so it is
    the only one that has to clear the install's gates first — an admin veto or
    ``CONDOR_SHARING=off`` refuses it rather than storing a consent the install
    would not honour. Choosing ``off`` is never refused: withdrawing must work
    on an install where sharing has already been turned off above the user's
    head, or a user could be locked into a standing yes.

    Leaving ``always`` goes through :func:`condor.sharing.sweep.withdraw`, which
    destroys what the sweep queued but never sent. The back catalogue is a
    separate button and is deliberately not touched here.
    """
    state = (body.state or "").strip().lower()
    if state not in consent.USER_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown sharing preference {body.state!r}",
        )

    if state == consent.ALWAYS:
        if not consent.can_share(user.id):
            raise HTTPException(status_code=403, detail=SHARING_DISABLED)
        consent.set_user_state(user.id, state)
    elif consent.user_state(user.id) == consent.ALWAYS:
        sweep.withdraw(user.id, state)
    else:
        consent.set_user_state(user.id, state)

    return await get_preference(user)


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
        pending=outbox.count(),
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
