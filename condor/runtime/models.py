"""Typed boundary between the runtime and its callers.

These are Pydantic models on purpose: the same shapes travel in-process today
and over HTTP once the runtime is extracted, so nothing on the boundary may be
a live process object. Process objects (permission callbacks, ``user_data``)
are passed as separate arguments, never inside a model.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

# Events streamed back from a prompt. Re-exported so callers can keep importing
# the whole boundary from one place; the definition lives in events.py.
from condor.runtime.events import EventType, RuntimeEvent  # noqa: F401


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SessionSpec(BaseModel):
    """Everything needed to provision a session, and nothing more."""

    key: str = Field(description="Canonical SessionKey string, e.g. 'tg:12345'")
    agent_key: str = Field(description="LLM/agent identifier, e.g. 'claude-code'")
    user_id: int | None = None
    chat_id: int | None = Field(
        default=None,
        description="Telegram chat id. None for surfaces without a chat (web, mcp).",
    )
    server_name: str | None = None
    platform: str = "telegram"
    lazy_context: bool = Field(
        default=False,
        description="Inject the initial context on the first prompt instead of now.",
    )
    extra_context: str = Field(
        default="",
        description="Appended to the initial context, e.g. a caller's handoff recap.",
    )
    agent_slug: str = Field(
        default="",
        description="Binds the session to a specific Agent. Empty resolves the "
        "default one, Condor — not the absence of an agent (FEAT-033).",
    )
    conversation_id: str = Field(
        default="",
        description="Durable conversation this session answers into. Empty mints "
        "a new one; a supplied id resumes that transcript.",
    )

    def effective_ids(self) -> tuple[int, int]:
        """``(user_id, chat_id)`` as the MCP subprocess must see them.

        Both ids reach the subprocess twice — in its environment
        (``CONDOR_USER_ID``/``CONDOR_CHAT_ID``) and on its argv
        (``--user-id``/``--chat-id``, via ``binding.resolve``) — and argv wins
        (mcp_servers/condor/settings.py). So the fallback for an ownerless or
        chatless spec is derived here, once, and both channels read it: two
        hand-copied ``or 0`` expressions used to disagree, and the subprocess
        ran as the phantom user 0 that argv carried (SEC-180).

        A spec with neither id is legitimately ownerless — sessions.py supports
        that shape deliberately — and lands on 0, which fails closed everywhere.
        """
        chat_id = self.chat_id if self.chat_id is not None else (self.user_id or 0)
        return self.user_id or chat_id, chat_id


class SessionInfo(BaseModel):
    """Serializable view of a live session."""

    key: str
    agent_key: str
    user_id: int | None = Field(
        default=None,
        description="Owning Condor user. Authorization compares against this.",
    )
    surface: str = Field(default="", description="Originating frontend: tg/web/mcp.")
    slot: str = Field(default="", description="Web slot id; empty on other surfaces.")
    server_name: str | None = None
    server_pinned: bool = Field(
        default=False,
        description="The bound Agent pins this server; the chat's ambient "
        "selection is overridden and cannot be changed here.",
    )
    is_busy: bool = False
    alive: bool = False
    created_at: datetime = Field(default_factory=_utcnow)
    last_prompt_at: datetime | None = None
    agent_slug: str = ""
    label: str = Field(
        default="Condor", description="Display name of whoever is answering."
    )
    conversation_id: str = Field(
        default="",
        description="Durable conversation behind this session. Survives it.",
    )


class PromptImage(BaseModel):
    """One picture given to the model alongside a turn's text.

    The naming is the boundary: the route and the transcript say *attachment*
    (what a user handed over), the prompt says *image* (what a model is given),
    and the mime allowlist in :mod:`condor.runtime.attachments` is the line
    between the two — the one place a second kind of attachment would later have
    to be taught.

    ``id`` is the stored attachment this came from, so the funnel can record what
    was attached without a second field the caller has to keep in step with this
    one. Empty for an image that was never stored (nothing does that today; it is
    what a surface handing over bytes it holds in memory would look like), and
    such an image is simply not written to the transcript.
    """

    data: bytes
    mime: str
    id: str = Field(
        default="",
        description="The attachment it was loaded from; '' = not stored.",
    )


class PromptRequest(BaseModel):
    """One user turn sent to a session."""

    text: str
    images: list[PromptImage] = Field(
        default_factory=list,
        description=(
            "Pictures to send beside the text. They travel *beside* it and never "
            "inside it: ``text`` stays a str all the way down the funnel, where "
            "pending_context and the view block are prepended by string surgery."
        ),
    )
    user_kind: str = Field(
        default="",
        description="Set when the turn was NOT typed by the user: the opening "
        "line is recorded as a system turn of this kind instead of as the "
        "user's words. Empty (the default) is a real user turn.",
    )
    view_context: str = Field(
        default="",
        description="What the user was looking at while asking. Prepended to "
        "this one prompt and never recorded: it is true of a moment, not of "
        "the conversation, so replaying it on resume would state a page the "
        "user left long ago.",
    )
