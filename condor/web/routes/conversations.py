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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from condor.runtime import client as runtime
from condor.runtime import conversations
from condor.runtime.conversations import ConversationIdError, ConversationMeta
from condor.web.auth import get_current_user
from condor.web.models import WebUser
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
    """
    owner_id = _owner(user, user_id)
    _meta_or_404(owner_id, conversation_id)

    detached = 0
    for info in await runtime.list_sessions(user_id=owner_id):
        if info.conversation_id == conversation_id:
            from condor.runtime import SessionKey

            if await runtime.destroy(SessionKey.parse(info.key)):
                detached += 1

    return {
        "deleted": conversations.delete_conversation(owner_id, conversation_id),
        "sessions_destroyed": detached,
    }
