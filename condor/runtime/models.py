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


class PromptRequest(BaseModel):
    """One user turn sent to a session."""

    text: str
    image_b64: str | None = None
    image_mime: str | None = None
