"""The canonical wire event.

Before this module each surface invented its own rendering of the ACP event
dataclasses: ``condor/web/routes/chat_ws.py`` serialized them to JSON inline and
``handlers/agents/stream.py`` pattern-matched the dataclasses to build Telegram
messages. Two renderers sharing an implicit contract drift the moment either
side changes.

``RuntimeEvent`` is that contract, made explicit. It is a Pydantic model so the
same value can be rendered in-process today and pushed through SSE tomorrow
without either surface noticing.
"""

from __future__ import annotations

import base64
import dataclasses
import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from condor.acp import (
    Heartbeat,
    PromptDone,
    TextChunk,
    ThoughtChunk,
    ToolCallEvent,
    ToolCallUpdate,
)


class EventType(str, Enum):
    """Every shape that can cross the runtime boundary."""

    TEXT = "text"
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_UPDATE = "tool_update"
    PERMISSION = "permission_request"
    HEARTBEAT = "heartbeat"
    ERROR = "error"
    DONE = "done"
    # The turn was accepted but has not started: it is waiting behind the one
    # in front of it. Emitted only when a caller opted into waiting, so a
    # surface can say "waiting its turn" instead of showing a dead composer.
    QUEUED = "queued"


def serialize(value: Any) -> Any:
    """Recursively coerce ``value`` into something JSON can hold.

    Bytes become base64 strings (images already flow through PydanticAIClient as
    bytes and must survive the trip), Pydantic models and dataclasses flatten to
    dicts, and anything unrecognized degrades to ``str`` rather than exploding
    mid-stream.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, BaseModel):
        return serialize(value.model_dump())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return serialize(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serialize(v) for v in value]
    return str(value)


class RuntimeEvent(BaseModel):
    """One event streamed out of a session."""

    type: EventType
    session_key: str = ""
    data: Any = None
    ts: float = Field(default_factory=time.time)

    # ── Constructors ──

    @classmethod
    def from_acp(cls, event: Any, session_key: str = "") -> "RuntimeEvent":
        """Map an ACP client event onto the canonical union.

        An unrecognized event becomes an ``ERROR`` rather than raising: a new
        event type appearing upstream must never kill a live prompt stream.
        """
        if isinstance(event, TextChunk):
            return cls(
                type=EventType.TEXT, session_key=session_key, data={"text": event.text}
            )
        if isinstance(event, ThoughtChunk):
            return cls(
                type=EventType.THOUGHT,
                session_key=session_key,
                data={"text": event.text},
            )
        if isinstance(event, ToolCallEvent):
            return cls(
                type=EventType.TOOL_CALL,
                session_key=session_key,
                data={
                    "tool_call_id": event.tool_call_id,
                    "title": event.title,
                    "status": event.status,
                    "kind": event.kind,
                    "input": serialize(event.input),
                },
            )
        if isinstance(event, ToolCallUpdate):
            return cls(
                type=EventType.TOOL_UPDATE,
                session_key=session_key,
                data={
                    "tool_call_id": event.tool_call_id,
                    "status": event.status,
                    "title": event.title,
                    "output": event.output,
                },
            )
        if isinstance(event, Heartbeat):
            return cls(
                type=EventType.HEARTBEAT,
                session_key=session_key,
                data={"elapsed_seconds": event.elapsed_seconds},
            )
        if isinstance(event, PromptDone):
            return cls(
                type=EventType.DONE,
                session_key=session_key,
                data={"stop_reason": event.stop_reason},
            )
        return cls(
            type=EventType.ERROR,
            session_key=session_key,
            data={"message": f"Unknown event type: {type(event).__name__}"},
        )

    @classmethod
    def error(cls, message: str, session_key: str = "") -> "RuntimeEvent":
        return cls(
            type=EventType.ERROR, session_key=session_key, data={"message": message}
        )

    @classmethod
    def queued(cls, session_key: str = "") -> "RuntimeEvent":
        """This turn is waiting for the one ahead of it to finish."""
        return cls(type=EventType.QUEUED, session_key=session_key, data={})

    @classmethod
    def done(
        cls, stop_reason: str = "end_turn", session_key: str = ""
    ) -> "RuntimeEvent":
        return cls(
            type=EventType.DONE,
            session_key=session_key,
            data={"stop_reason": stop_reason},
        )

    # ── Accessors ──
    #
    # Surfaces read payload fields through these instead of indexing ``data``,
    # so a payload key can move without breaking every renderer.

    @property
    def text(self) -> str:
        return str((self.data or {}).get("text", ""))

    @property
    def stop_reason(self) -> str:
        return str((self.data or {}).get("stop_reason", ""))

    def field(self, name: str, default: Any = None) -> Any:
        return (self.data or {}).get(name, default)

    def to_wire(self) -> dict:
        """JSON-safe dict for SSE frames and WebSocket messages."""
        return {
            "type": self.type.value,
            "session_key": self.session_key,
            "data": serialize(self.data),
            "ts": self.ts,
        }
