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

from pydantic import BaseModel, ConfigDict, Field

from condor.runtime.events import EventType
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

# Upper bounds on the tool IO kept per call, in characters. Load-bearing, not
# defensive: a tool result is routinely a market-data dump, a whole portfolio
# payload or a base64 image, and the transcript has no retention sweep — kept
# verbatim, one turn could outweigh the entire conversation and slow every
# read of the file. They live here, beside ``REPLAY_MAX_CHARS`` and for the
# same reason, rather than in ``timeouts.py``: that policy is
# deadlines-in-seconds parsed from ``CONDOR_TIMEOUT_*``, and a character count
# would not survive its loader.
TOOL_INPUT_MAX_CHARS = 1000
TOOL_OUTPUT_MAX_CHARS = 2000

# Argument names whose value must never reach disk. Tool arguments are written
# by the model, and at least one tool in the agents' own toolset takes a
# credential directly — mcp-hummingbot's ``configure_server(password=…)`` — so
# a verbatim transcript would persist a plaintext password. Matched as a
# substring of the lowered key, so ``api_key``, ``secretKey`` and
# ``wallet_private_key`` all land on it.
_SECRET_KEY_HINTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "privatekey",
    "passphrase",
    "credential",
    "mnemonic",
    "authorization",
)
REDACTED = "[redacted]"

# Nesting past this is not walked. The payload comes from a tool call we do not
# own, and ``observe()`` runs on the live event path with no guard around it —
# unlike ``flush()``, a raise here would take down the prompt stream.
_REDACT_MAX_DEPTH = 6

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


def _is_secret_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(hint in lowered for hint in _SECRET_KEY_HINTS)


def _redact(value, depth: int = 0):
    """``value`` with every credential-looking entry replaced by a marker.

    Redacts by key name, never by value: guessing which *string* is a secret
    is how a redactor either misses one or mangles a trading pair. A key is a
    contract the tool author wrote down; a value is not.
    """
    if depth >= _REDACT_MAX_DEPTH:
        return "…"
    if isinstance(value, dict):
        return {
            str(k): (REDACTED if _is_secret_key(k) else _redact(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v, depth + 1) for v in value]
    return value


def _tool_input(raw, limit: int = TOOL_INPUT_MAX_CHARS) -> dict | None:
    """A tool call's arguments as they go to disk: redacted and bounded.

    Always a dict or ``None``, so a dataset consumer reading ``input`` sees one
    shape. An argument set too large to keep whole collapses into a single
    ``_clipped`` rendering rather than into a different type — the call is
    still recorded as having had arguments, which is what the trajectory needs.
    """
    if not isinstance(raw, dict) or not raw:
        return None
    safe = _redact(raw)
    try:
        dumped = json.dumps(safe, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # a payload json cannot describe at all
        dumped = str(safe)
    return safe if len(dumped) <= limit else {"_clipped": _truncate(dumped, limit)}


# ── Models ──


class ConversationMeta(BaseModel):
    """The listable facts about a conversation.

    ``updated_at`` is stamped by ``write_status`` on every merge, so it arrives
    from disk as a Unix float rather than the ISO string we wrote. Pydantic
    reads both as an aware UTC datetime, and "last write" is exactly the
    semantic the list ordering wants.

    Extra keys on disk are ignored (pydantic's default), so a meta written
    before a field was dropped — ``mode``, retired with the persona axis in
    FEAT-033 — still loads instead of failing the whole conversation list.
    """

    id: str
    user_id: int
    surface: str = Field(default="", description="Where it was born: tg/web/mcp.")
    title: str = ""
    agent_key: str = Field(default="", description="Last model used.")
    agent_slug: str = Field(
        default="", description="Last bound Agent; '' = the default one, Condor."
    )
    server_name: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    turn_count: int = 0
    last_snippet: str = ""


class TurnEntry(BaseModel):
    """One line of the transcript.

    The shape grows over time, so the line format is deliberately tolerant in
    both directions: every field past ``role`` carries a default, so a line
    written before a field existed still parses, and unknown keys are ignored,
    so a line written by a newer build still loads here. Anything added later
    must keep both halves of that bargain — optional, with a default that reads
    as "not recorded" rather than as a real value.

    The attribution fields say which brain produced the turn. They live on the
    turn rather than only on ``ConversationMeta`` because the meta is
    last-write-wins: a chat that switches models mid-way would otherwise
    attribute its whole history to whatever answered last. Empty means
    unattributed — turns written before this existed are left that way rather
    than backfilled with a guess.

    ``stop_reason`` says how the stream ended, so a truncated answer can be
    told apart from a finished one: a reply cut short by a cancel, a timeout or
    a backend error otherwise lands on disk looking exactly like a complete
    one. Empty means the stream never reported an ending — the abandoned
    generator (page reload, WS disconnect), and every turn written before this
    field existed.
    """

    model_config = ConfigDict(extra="ignore")

    role: str = Field(description="user | assistant | system")
    text: str = ""
    thought: str = ""
    tool_calls: list[dict] = Field(
        default_factory=list,
        description="Per call: id, title, status, kind, input, output.",
    )
    kind: str = Field(
        default="", description="System entries only: switch | error | delegation."
    )
    ts: float = Field(default_factory=time.time)
    agent_key: str = Field(default="", description="Model that produced this turn.")
    agent_slug: str = Field(
        default="", description="Bound Agent; '' = the default one, Condor."
    )
    stop_reason: str = Field(
        default="", description="Assistant turns: how the stream ended; '' = unknown."
    )


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

    def __init__(
        self,
        user_id: int | None,
        conv_id: str,
        user_text: str,
        *,
        agent_key: str = "",
        agent_slug: str = "",
    ):
        self.enabled = bool(conv_id) and user_id is not None
        self.user_id = user_id
        self.conv_id = conv_id
        self._user_text = user_text
        # Keyword-only and defaulted: a caller that does not know who answered
        # records an unattributed turn instead of failing to record at all.
        self._agent_key = agent_key
        self._agent_slug = agent_slug
        self._text: list[str] = []
        self._thought: list[str] = []
        self._tools: dict[str, dict] = {}
        self._error = ""
        # Stays empty unless a DONE arrives: an abandoned generator never
        # reports an ending, and "unknown" is the honest record of that.
        self._stop = ""
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
                "kind": str(event.field("kind") or ""),
                "input": _tool_input(event.field("input")),
                "output": "",
            }
        elif event.type == EventType.TOOL_UPDATE:
            call_id = str(event.field("tool_call_id") or "")
            call = self._tools.get(call_id)
            if call:
                call["status"] = str(event.field("status") or call["status"])
                # Merge an output only when there is one. The pydantic-ai path
                # emits a bare "completed" update the moment the call is issued
                # and carries the real result on the following
                # ``ModelRequestNode``; on ACP the order is the other way
                # round. Either way a later empty value must not erase a
                # result that already arrived.
                output = _truncate(event.field("output") or "", TOOL_OUTPUT_MAX_CHARS)
                if output:
                    call["output"] = output
        elif event.type == EventType.ERROR:
            self._error = str(event.field("message", "") or "")
        elif event.type == EventType.DONE:
            self._stop = event.stop_reason

    def _attribution(self) -> dict:
        """What this recorder knows about who produced the turn.

        One place for the fields a turn is stamped with, so a later field joins
        the record without touching every ``TurnEntry`` construction here.
        """
        return {
            "agent_key": self._agent_key,
            "agent_slug": self._agent_slug,
        }

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
                        stop_reason=self._stop,
                        **self._attribution(),
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
