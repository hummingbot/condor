"""Durable conversations. The transcript outlives the subprocess.

A **session** is a live agent subprocess. A **conversation** is the text that
was said. They used to be the same object, so a restart, a hot reload, a
budget eviction or a crashed ACP bridge silently deleted the chat.

They are separate here. The session still dies with the process — that is what
a subprocess is. The conversation is a directory:

    condor/.runtime/conversations/{user_id}/{conv_id}/
        meta.json          # atomic merge, via registry_file.write_status()
        transcript.jsonl   # append-only, one JSON object per line

Same idiom as the rest of the runtime's durable facts: ``registry_file`` for
atomic status, ``journal.py`` for an append-only narrative. No database.

**Resuming is a replay, not a reattach.** ACP advertises no ``session/load``
and its session ids are bridge-local, so there is no primitive to reattach to.
``replay_context()`` renders the tail of the transcript into the next session's
opening context: the resumed agent has *read* the conversation, it has not
*lived* it. That is uniform across every client, which is the point — buying
true fidelity for ``PydanticAIClient`` only would give two recovery behaviors
the user cannot predict.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from condor.runtime.events import EventType
from condor.runtime.models import DEFAULT_MODE
from condor.runtime.registry_file import read_status, write_status

log = logging.getLogger(__name__)

META_FILENAME = "meta.json"
TRANSCRIPT_FILENAME = "transcript.jsonl"

# conv_id and user_id both become directory names, so neither may escape one.
# Same guard, same reason, as state.py's namespace check.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")

# Upper bound on the replayed transcript, in characters. Load-bearing, not
# cosmetic: a 300-turn conversation replayed whole would eat the context
# window. It lives here rather than in ``timeouts.py`` because that policy is
# deadlines-in-seconds, parsed from ``CONDOR_TIMEOUT_*``; a character count
# would not survive its loader.
REPLAY_MAX_CHARS = int(os.environ.get("CONDOR_REPLAY_MAX_CHARS", "6000") or 6000)

REPLAY_HEADER = "Previously in this chat:"
REPLAY_OMITTED = "(older turns omitted)"

TITLE_MAX_CHARS = 80
SNIPPET_MAX_CHARS = 160

# Recorders that have observed events but not yet written them. A prompt
# generator abandoned by a page reload normally flushes through its own
# ``finally``; this set is the shutdown backstop for one that never gets
# collected in time.
_live_recorders: set["Recorder"] = set()


class ConversationIdError(ValueError):
    """Raised for an id that could not be turned into a safe path."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate(value: str) -> str:
    if not value or not _SAFE_ID.match(value):
        raise ConversationIdError(
            f"Invalid conversation id {value!r}: "
            "use letters, digits, dot, dash or underscore."
        )
    return value


def _truncate(text: str, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


# ── Models ──


class ConversationMeta(BaseModel):
    """The listable facts about a conversation.

    ``updated_at`` is stamped by ``write_status`` on every merge, so it arrives
    from disk as a Unix float rather than the ISO string we wrote. Pydantic
    reads both as an aware UTC datetime, and "last write" is exactly the
    semantic the list ordering wants.
    """

    id: str
    user_id: int
    surface: str = Field(default="", description="Where it was born: tg/web/mcp.")
    title: str = ""
    agent_key: str = Field(default="", description="Last model used.")
    agent_slug: str = Field(default="", description="Last bound Agent; '' = assistant.")
    mode: str = DEFAULT_MODE
    server_name: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    turn_count: int = 0
    last_snippet: str = ""


class TurnEntry(BaseModel):
    """One line of the transcript."""

    role: str = Field(description="user | assistant | system")
    text: str = ""
    thought: str = ""
    tool_calls: list[dict] = Field(default_factory=list)
    kind: str = Field(default="", description="System entries only: switch | error.")
    ts: float = Field(default_factory=time.time)


# ── Paths ──


def _root() -> Path:
    """Where every conversation lives.

    Derived from ``_DATA_ROOT`` exactly as ``state.py`` derives its own root, so
    a test that repoints one repoints both.
    """
    from condor.agents.agent import _DATA_ROOT

    return Path(_DATA_ROOT).parent / "condor" / ".runtime" / "conversations"


def _user_dir(user_id: int | str) -> Path:
    return _root() / _validate(str(user_id))


def _conv_dir(user_id: int | str, conv_id: str) -> Path:
    return _user_dir(user_id) / _validate(str(conv_id))


# ── Store ──


def new_conversation(
    user_id: int,
    surface: str = "",
    *,
    agent_key: str = "",
    agent_slug: str = "",
    mode: str = DEFAULT_MODE,
    server_name: str | None = None,
) -> ConversationMeta:
    """Mint an empty conversation and persist its meta."""
    now = _utcnow()
    meta = ConversationMeta(
        id=uuid.uuid4().hex[:12],
        user_id=int(user_id),
        surface=surface,
        agent_key=agent_key,
        agent_slug=agent_slug,
        mode=mode,
        server_name=server_name,
        created_at=now,
        updated_at=now,
    )
    write_status(_conv_dir(user_id, meta.id), META_FILENAME, **_meta_fields(meta))
    return meta


def _meta_fields(meta: ConversationMeta) -> dict:
    return meta.model_dump(mode="json")


def get_conversation(user_id: int, conv_id: str) -> ConversationMeta | None:
    """Read one conversation's meta, or None when it is absent or unreadable."""
    data = read_status(_conv_dir(user_id, conv_id), META_FILENAME)
    if not data:
        return None
    try:
        return ConversationMeta(**data)
    except Exception:  # noqa: BLE001 - a half-written meta reads as absent
        log.debug("Unparseable conversation meta for %s/%s", user_id, conv_id)
        return None


def list_conversations(user_id: int, *, limit: int = 100) -> list[ConversationMeta]:
    """This user's conversations, newest first.

    Reads only ``meta.json`` per directory. One without a readable meta (hand
    deleted, half written) is skipped rather than failing the whole listing.
    """
    base = _user_dir(user_id)
    if not base.is_dir():
        return []

    metas: list[ConversationMeta] = []
    try:
        children = sorted(base.iterdir())
    except OSError:
        return []
    for child in children:
        if not child.is_dir():
            continue
        meta = get_conversation(user_id, child.name)
        if meta is not None:
            metas.append(meta)

    metas.sort(key=lambda m: m.updated_at, reverse=True)
    return metas[:limit] if limit else metas


def read_transcript(user_id: int, conv_id: str, *, limit: int = 200) -> list[TurnEntry]:
    """The tail of a transcript. ``limit=0`` means all of it.

    A line that fails to parse is skipped, never fatal — the same tolerance
    ``read_status()`` shows for a truncated status file.
    """
    path = _conv_dir(user_id, conv_id) / TRANSCRIPT_FILENAME
    entries: list[TurnEntry] = []
    try:
        if not path.is_file():
            return []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(TurnEntry(**json.loads(line)))
                except Exception:  # noqa: BLE001 - one bad line is not the file
                    continue
    except OSError:
        log.debug("Unreadable transcript at %s", path, exc_info=True)
        return []
    return entries[-limit:] if limit else entries


def append_turn(user_id: int, conv_id: str, entry: TurnEntry) -> None:
    """Append one turn and touch the conversation's meta.

    Never raises: losing a transcript line must not take down a live prompt.
    """
    conv_dir = _conv_dir(user_id, conv_id)
    try:
        conv_dir.mkdir(parents=True, exist_ok=True)
        with (conv_dir / TRANSCRIPT_FILENAME).open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(entry.model_dump(mode="json"), ensure_ascii=False) + "\n"
            )
    except OSError:
        log.warning("Could not append turn to %s", conv_dir, exc_info=True)
        return

    meta = get_conversation(user_id, conv_id)
    if meta is None:
        # The turn is on disk either way; without a meta there is nothing
        # coherent to merge into, and inventing one would fabricate an owner.
        log.debug("Appended a turn to %s/%s with no meta", user_id, conv_id)
        return

    fields: dict = {"turn_count": meta.turn_count + 1}
    if entry.role == "user" and not meta.title and entry.text:
        fields["title"] = _truncate(entry.text, TITLE_MAX_CHARS)
    if entry.role == "assistant" and entry.text:
        fields["last_snippet"] = _truncate(entry.text, SNIPPET_MAX_CHARS)
    write_status(conv_dir, META_FILENAME, **fields)


def update_meta(user_id: int, conv_id: str, **fields) -> bool:
    """Merge fields into an existing conversation's meta.

    Used when a session (re)attaches, so the list shows the model that actually
    answered last rather than the one it was born with.
    """
    if get_conversation(user_id, conv_id) is None:
        return False
    write_status(_conv_dir(user_id, conv_id), META_FILENAME, **fields)
    return True


def rename(user_id: int, conv_id: str, title: str) -> bool:
    """Set a conversation's title. Returns False when it does not exist."""
    return update_meta(user_id, conv_id, title=_truncate(title, TITLE_MAX_CHARS))


def delete_conversation(user_id: int, conv_id: str) -> bool:
    """Remove a conversation and its transcript. The only destructive verb."""
    conv_dir = _conv_dir(user_id, conv_id)
    if not conv_dir.is_dir():
        return False
    shutil.rmtree(conv_dir, ignore_errors=True)
    return not conv_dir.exists()


# ── Replay ──


def _render_turn(turn: TurnEntry) -> str:
    """One transcript line as the resumed agent will read it.

    Tool calls become "→ used X" rather than their results: the point is
    continuity of the conversation, not replaying outputs that are stale by
    now. An agent that reads a stale result will treat it as current.
    """
    if turn.role == "system":
        body = turn.text.strip()
        return f"({body})" if body else ""

    parts: list[str] = []
    if turn.text.strip():
        parts.append(turn.text.strip())
    for call in turn.tool_calls:
        title = str((call or {}).get("title") or "").strip()
        if title:
            parts.append(f"→ used {title}")

    body = "\n".join(parts).strip()
    if not body:
        return ""
    label = "user" if turn.role == "user" else "assistant"
    return f"[{label}] {body}"


def replay_context(
    user_id: int, conv_id: str, *, max_chars: int = REPLAY_MAX_CHARS
) -> str:
    """The tail of a conversation, framed for the next session's opening context.

    Walks backwards from the newest turn so the most recent ones always survive
    the bound, then reverses. The returned string never exceeds ``max_chars``.
    """
    turns = read_transcript(user_id, conv_id, limit=0)
    if not turns:
        return ""

    # Budget the body so header and footer fit inside the caller's bound.
    overhead = len(REPLAY_HEADER) + 1 + len(REPLAY_OMITTED) + 1
    budget = max_chars - overhead
    if budget <= 0:
        return REPLAY_HEADER[:max_chars]

    lines: list[str] = []
    used = 0
    omitted = False
    for turn in reversed(turns):
        rendered = _render_turn(turn)
        if not rendered:
            continue
        if used + len(rendered) + 1 > budget:
            omitted = True
            break
        lines.append(rendered)
        used += len(rendered) + 1

    if not lines:
        # Even the newest turn alone overruns the budget. Keeping a truncated
        # head of it beats returning nothing at all.
        newest = next((r for r in (_render_turn(t) for t in reversed(turns)) if r), "")
        if not newest:
            return ""
        lines = [newest[:budget]]
        omitted = True

    lines.reverse()
    parts = [REPLAY_HEADER, *lines]
    if omitted:
        parts.append(REPLAY_OMITTED)
    return "\n".join(parts)[:max_chars]


# ── Recorder ──


class Recorder:
    """Accumulates one turn's events in memory and writes it once.

    Wrapped around ``client.prompt()``, so every surface — Telegram, the
    dashboard, MCP — is recorded by one piece of code. Exactly **two** appends
    per turn (the user line and the assistant line); a per-chunk write would
    put file IO on the token hot path.

    Flushing happens in the caller's ``finally``, not on ``DONE``. The
    dashboard abandons that generator constantly (page reload, ``abort_prompt``
    cancelling the task, WS disconnect), and an abandoned async generator gets
    ``GeneratorExit`` — only a ``finally`` guarantees the partial answer is
    recorded. Losing the half-written reply is the bug this feature exists to
    fix; it must not survive in a new form.
    """

    def __init__(self, user_id: int | None, conv_id: str, user_text: str):
        self.enabled = bool(conv_id) and user_id is not None
        self.user_id = user_id
        self.conv_id = conv_id
        self._user_text = user_text
        self._text: list[str] = []
        self._thought: list[str] = []
        self._tools: dict[str, dict] = {}
        self._error = ""
        self._flushed = False
        if self.enabled:
            _live_recorders.add(self)

    def observe(self, event) -> None:
        """Accumulate one ``RuntimeEvent``. Never writes."""
        if not self.enabled:
            return
        if event.type == EventType.TEXT:
            self._text.append(event.text)
        elif event.type == EventType.THOUGHT:
            self._thought.append(event.text)
        elif event.type == EventType.TOOL_CALL:
            call_id = str(event.field("tool_call_id") or len(self._tools))
            self._tools[call_id] = {
                "id": call_id,
                "title": str(event.field("title") or ""),
                "status": str(event.field("status") or ""),
            }
        elif event.type == EventType.TOOL_UPDATE:
            call_id = str(event.field("tool_call_id") or "")
            call = self._tools.get(call_id)
            if call:
                call["status"] = str(event.field("status") or call["status"])
        elif event.type == EventType.ERROR:
            self._error = str(event.field("message", "") or "")

    def flush(self) -> None:
        """Write the user turn and the accumulated assistant turn. Idempotent.

        Synchronous on purpose: it runs from a ``finally`` that may be executing
        under ``GeneratorExit``, where awaiting is not allowed.
        """
        if not self.enabled or self._flushed:
            return
        self._flushed = True
        _live_recorders.discard(self)

        try:
            append_turn(
                self.user_id, self.conv_id, TurnEntry(role="user", text=self._user_text)
            )
            text = "".join(self._text)
            tools = list(self._tools.values())
            if text or tools or self._thought:
                append_turn(
                    self.user_id,
                    self.conv_id,
                    TurnEntry(
                        role="assistant",
                        text=text,
                        thought="".join(self._thought),
                        tool_calls=tools,
                    ),
                )
            elif self._error:
                append_turn(
                    self.user_id,
                    self.conv_id,
                    TurnEntry(role="system", text=self._error, kind="error"),
                )
        except Exception:  # noqa: BLE001 - recording must not break a prompt
            log.warning("Could not record turn for %s", self.conv_id, exc_info=True)


def record_system(user_id: int | None, conv_id: str, text: str, kind: str = "") -> None:
    """Note a non-conversational event in the transcript (e.g. an agent switch)."""
    if not conv_id or user_id is None:
        return
    append_turn(user_id, conv_id, TurnEntry(role="system", text=text, kind=kind))


def flush_all() -> None:
    """Write out every recorder still in flight (shutdown hook)."""
    for recorder in list(_live_recorders):
        recorder.flush()
