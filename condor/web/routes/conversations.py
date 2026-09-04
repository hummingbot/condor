"""Conversations as an addressable resource.

The counterpart to ``sessions.py``: that one lists what is *live*, this one
lists what was *said*. A session dies with the process; a conversation here
outlives it, which is what lets a chat held yesterday in Telegram be continued
today from the dashboard.

Ownership follows the same idiom as ``sessions.py``: admins see everything,
everyone else only their own. The store is keyed by owner, so reading someone
else's requires naming them explicitly via ``?user_id=``.

Note: modules under ``condor/web/routes/`` are not in ``main.py``'s hot-reload
list, so changes here need a full bot restart.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from condor import paths
from condor.runtime import attachments
from condor.runtime import client as runtime
from condor.runtime import conversations
from condor.runtime.conversations import ConversationIdError, ConversationMeta
from condor.web.auth import get_current_user
from condor.web.models import WebUser
from condor.web.routes.agents import DeploymentRow
from config_manager import get_config_manager

log = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _owner(user: WebUser, user_id: int | None) -> int:
    """Whose conversations the caller is asking for.

    Defaults to themselves. Naming someone else is an admin-only act — the
    same rule ``_require_ownership`` applies to sessions, moved to the front
    because the store is partitioned by owner rather than looked up by id.
    """
    if user_id is None or user_id == user.id:
        return user.id
    if get_config_manager().is_admin(user.id):
        return user_id
    raise HTTPException(status_code=403, detail="Not your conversation")


def _meta_or_404(owner_id: int, conversation_id: str) -> ConversationMeta:
    try:
        meta = conversations.get_conversation(owner_id, conversation_id)
    except ConversationIdError:
        raise HTTPException(status_code=400, detail="Malformed conversation id")
    if meta is None:
        raise HTTPException(
            status_code=404, detail=f"No conversation {conversation_id}"
        )
    return meta


class RenameRequest(BaseModel):
    title: str


@router.get("", response_model=list[ConversationMeta])
async def list_conversations(
    limit: int = Query(default=100, ge=1, le=1000),
    user_id: int | None = None,
    user: WebUser = Depends(get_current_user),
):
    """Every conversation the caller has ever held, newest first.

    One keyspace across surfaces, so a chat started in Telegram is listed here
    exactly like one started in the dashboard.
    """
    return conversations.list_conversations(_owner(user, user_id), limit=limit)


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    limit: int = Query(default=200, ge=1, le=2000),
    user_id: int | None = None,
    user: WebUser = Depends(get_current_user),
):
    """One conversation's meta plus the tail of its transcript."""
    owner_id = _owner(user, user_id)
    meta = _meta_or_404(owner_id, conversation_id)
    turns = conversations.read_transcript(owner_id, conversation_id, limit=limit)
    return {
        "meta": meta.model_dump(mode="json"),
        "turns": [t.model_dump(mode="json") for t in turns],
    }


@router.patch("/{conversation_id}", response_model=ConversationMeta)
async def rename_conversation(
    conversation_id: str,
    body: RenameRequest,
    user_id: int | None = None,
    user: WebUser = Depends(get_current_user),
):
    owner_id = _owner(user, user_id)
    _meta_or_404(owner_id, conversation_id)
    conversations.rename(owner_id, conversation_id, body.title)
    return _meta_or_404(owner_id, conversation_id)


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    user_id: int | None = None,
    user: WebUser = Depends(get_current_user),
):
    """Forget a conversation, and reap whatever session is still answering it.

    This is now the *only* way to lose a transcript. Destroying a session, or
    the health monitor reaping a dead one, no longer costs the user anything.

    A conversation that was shared (FEAT-054) is unshared *first*. Deleting it
    locally would otherwise destroy the delete token, and with it the only thing
    that could ever remove the copy on the collector — "delete" would then mean
    "delete here, keep there", which is not what anybody pressing it believes.
    The revocation is queued, so a network failure does not block the local
    delete; the queue carries the token and finishes the job later.
    """
    owner_id = _owner(user, user_id)
    _meta_or_404(owner_id, conversation_id)

    unshared = False
    try:
        from condor.sharing import share as sharing

        unshared = sharing.unshare(owner_id, conversation_id)
    except Exception:  # noqa: BLE001 - a local delete must not need the network
        log.warning(
            "Could not queue an unshare for %s before deleting it",
            conversation_id,
            exc_info=True,
        )

    detached = 0
    for info in await runtime.list_sessions(user_id=owner_id):
        if info.conversation_id == conversation_id:
            from condor.runtime import SessionKey

            if await runtime.destroy(SessionKey.parse(info.key)):
                detached += 1

    return {
        "deleted": conversations.delete_conversation(owner_id, conversation_id),
        "sessions_destroyed": detached,
        "unshared": unshared,
    }


# ── Attachments ──
#
# Beside the transcript because that is what they belong to: the ownership rule
# (``_owner``) and the "does this conversation exist" check are already here, and
# the store writes *inside* the conversation directory, so the two resources have
# exactly the same lifetime. See :mod:`condor.runtime.attachments`.


@router.post("/{conversation_id}/attachments")
async def upload_attachment(
    conversation_id: str,
    file: UploadFile = File(...),
    user_id: int | None = None,
    user: WebUser = Depends(get_current_user),
):
    """Store one image against a conversation and return the id to send with it.

    Modelled on ``/api/v1/transcribe``: multipart, a size cap, a bearer-guarded
    dependency, driven from the frontend by ``authFetch`` with a ``FormData``
    body. The upload happens at *send* time, not at paste time, so a file is only
    ever written for a message that is actually going out and there is nothing to
    sweep (FEAT-098).

    The client's ``content_type`` is not consulted: the store sniffs the bytes.
    """
    owner_id = _owner(user, user_id)
    _meta_or_404(owner_id, conversation_id)

    data = await file.read()
    try:
        stored = attachments.save(owner_id, conversation_id, data)
    except attachments.TooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except attachments.UnsupportedTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc))
    except attachments.NoConversationError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {"id": stored.id, "mime": stored.mime, "bytes": stored.bytes}


@router.get("/{conversation_id}/attachments/{attachment_id}")
async def get_attachment(
    conversation_id: str,
    attachment_id: str,
    user_id: int | None = None,
    user: WebUser = Depends(get_current_user),
):
    """The stored bytes, as their own media type.

    Guarded by ``get_current_user`` like everything else here, which is an
    ``HTTPBearer`` — so this is *fetched* and turned into an object URL by the
    frontend's ``useAuthedImage``, never pointed at by a bare ``<img src>``,
    which has no way to carry the header.
    """
    owner_id = _owner(user, user_id)
    _meta_or_404(owner_id, conversation_id)
    try:
        data, mime = attachments.load(owner_id, conversation_id, attachment_id)
    except attachments.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    # Private by construction (a per-user path behind a bearer token), and the
    # bytes never change under an id, so the browser may keep it for the session.
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "private, max-age=3600"},
    )


# ── What a conversation put into the world (FEAT-110) ──
#
# The conversation-shaped sibling of a run's ledger
# (``/agents/{slug}/strategies/{sslug}/sessions/{n}/executors``). It lives here
# rather than there because the ownership rule and the "does this conversation
# exist" check are already here, and the record it reads is written *inside* the
# conversation directory — the same reason the attachments above are here.
#
# The assembly is FEAT-100's ``build_deployments``, unchanged and unwrapped: its
# own docstring gives being pure over values on the caller's stack as the reason
# it is a function rather than an endpoint, and this is the second caller that
# reason was written for.


class ConversationDeployments(BaseModel):
    """Everything one conversation created, and whether it could have recorded it.

    ``predates_ledger`` is the difference between two answers that look
    identical on screen and are not: *this conversation deployed nothing*, and
    *this conversation ran before Condor wrote down what it did*. Every
    conversation older than FEAT-105 is the second one, and telling a reader the
    first about it would be a lie the panel tells confidently.
    """

    deployments: list[DeploymentRow] = []
    predates_ledger: bool = False


def _predates_ledger(meta: ConversationMeta, since: float) -> bool:
    """Did this conversation finish before any door began recording deeds?

    ``since`` is :attr:`~condor.agents.deed_index.DeedIndex.since` — the instant
    Condor's record of its own work became complete. A conversation whose *last*
    activity is older than that could have deployed anything and left no trace;
    one that was still being talked to afterwards would have left one.

    ``since`` of ``0.0`` means no deed has ever been recorded on this install, so
    there is nothing to date the cut with. FEAT-106 already settled what to do
    with that: without a cut, nothing can be judged against it.
    """
    if since <= 0:
        return False
    try:
        return meta.updated_at.timestamp() < since
    except Exception:  # noqa: BLE001 - an unreadable stamp judges nothing
        return False


@router.get("/{conversation_id}/deployments", response_model=ConversationDeployments)
async def get_conversation_deployments(
    conversation_id: str,
    user_id: int | None = None,
    user: WebUser = Depends(get_current_user),
):
    """The bots, controllers and executors this conversation created.

    The same four steps the session route takes, in the same order: read the
    ledger and the actions log, work out which bases the run still owns, fetch
    performance for them, and hand all four to ``build_deployments``.

    Two things differ from the sibling, both because a chat is not a loop:

    - **Liveness comes from the deed index, not from ``current_owner_bases``.**
      A chat's ledger is never *released* — nothing winds a conversation down —
      so "does this conversation still own that bot" can only be answered by
      asking whether anybody has claimed the base since, which is exactly the
      newest-claim rule FEAT-106's index already applies across every run on
      disk. It reads only the filesystem and is memoised, so this costs nothing.
    - **A conversation's standalone executors join on the same tag a loop's
      do.** The join is on ``controller_id``, and a chat is now told what to put
      there: :func:`~condor.runtime.context.conversation_attribution` hands the
      model :func:`~condor.agents.deeds.attribution_tag` at session start, which
      is the same call this route asks the API with (CORR-325). Before that only
      a tick was told to set a tag, so this ``agent_id`` named the conversation
      and matched nothing — bots and their controllers were the whole of what a
      chat could be credited with. A conversation that predates the instruction
      still shows only those two, because its executors were born untagged and
      no tag can be recovered after the fact.

    A conversation that recorded nothing costs no Hummingbot API call at all,
    which is what lets the rail badge this without polling the fleet.
    """
    from condor.agents.actions import ACTIONS_FILENAME, MAX_ACTION_LINES, read_actions
    from condor.agents.deed_index import build_deed_index
    from condor.agents.deeds import attribution_tag, for_conversation
    from condor.agents.ownership import read_owned
    from condor.agents.performance import AgentPerformance, fetch_agent_performance
    from condor.web.routes.agents import build_deployments

    owner_id = _owner(user, user_id)
    meta = _meta_or_404(owner_id, conversation_id)

    index = build_deed_index()
    try:
        directory = paths.conversation_dir(owner_id, conversation_id)
    except Exception:  # noqa: BLE001 - an unsafe id has no record, and no rows
        return ConversationDeployments()

    owned = read_owned(directory)
    actions = (
        read_actions(directory, limit=MAX_ACTION_LINES)
        if (directory / ACTIONS_FILENAME).exists()
        else []
    )
    if not owned and not actions:
        return ConversationDeployments(
            predates_ledger=_predates_ledger(meta, index.since)
        )

    agent_id = attribution_tag(
        for_conversation(owner_id, conversation_id, meta.agent_slug)
    )
    # Only the bases nobody has claimed since. A base this conversation deployed
    # and another run redeployed belongs to that run now, and crediting both
    # with the same money is the mistake `current_owner_bases` exists to prevent.
    bases_now = [
        bot.base
        for bot in owned
        if (ref := index.owner_of(bot.base)) is not None
        and ref.run_id == conversation_id
    ]
    since = min((b.since for b in owned if b.since > 0), default=0.0)

    perf: Any = AgentPerformance(agent_id=agent_id)
    client = await _client_for(meta)
    if client is not None:
        try:
            perf = await fetch_agent_performance(
                client, agent_id, bot_names=bases_now, since=since
            )
        except Exception:  # noqa: BLE001 - a priceless row still names the bot
            log.warning(
                "deployments: performance for %s failed", conversation_id, exc_info=True
            )

    rows = build_deployments(owned, bases_now, perf, actions, agent_id)
    return ConversationDeployments(deployments=rows)


async def _client_for(meta: ConversationMeta):
    """The API client for the server this conversation trades on, or ``None``.

    A chat pinned to one server must be priced against that one; a chat that
    never named a server has no fleet to ask about, and its recorded bots are
    still worth listing without money beside them.
    """
    if not meta.server_name:
        return None
    try:
        return await get_config_manager().get_client(meta.server_name)
    except Exception as e:  # noqa: BLE001 - an offline server is not a 500
        log.warning("get_client(%s) failed: %s", meta.server_name, e)
        return None
