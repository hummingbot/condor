"""Durable conversations. The transcript outlives the subprocess.

A **session** is a live agent subprocess. A **conversation** is the text that
was said. They used to be the same object, so a restart, a hot reload, a
budget eviction or a crashed ACP bridge silently deleted the chat.

They are separate here. The session still dies with the process — that is what
a subprocess is. The conversation is a directory:

    .condor/users/{user_id}/conversations/{conv_id}/
        meta.json                  # atomic merge, via registry_file.write_status()
        transcript.jsonl           # append-only, bounded; one JSON object per line
        transcript_archive.jsonl   # turns retired out of the file above

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
from collections.abc import Iterator
from datetime import datetime, timezone
from itertools import chain, islice
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from condor import paths
from condor.acp.client import (
    ToolCallEvent,
    ToolCallUpdate,
    fold_tool_call_event,
    normalize_tool_title,
)
from condor.fsutil import atomic_write_bytes
from condor.runtime.events import EventType
from condor.runtime.registry_file import read_status, write_status

log = logging.getLogger(__name__)

META_FILENAME = "meta.json"
TRANSCRIPT_FILENAME = "transcript.jsonl"
TRANSCRIPT_ARCHIVE_FILENAME = "transcript_archive.jsonl"

# Upper bound on the replayed transcript, in characters. Load-bearing, not
# cosmetic: a 300-turn conversation replayed whole would eat the context
# window. It lives here rather than in ``timeouts.py`` because that policy is
# deadlines-in-seconds, parsed from ``CONDOR_TIMEOUT_*``; a character count
# would not survive its loader.
REPLAY_MAX_CHARS = int(os.environ.get("CONDOR_REPLAY_MAX_CHARS", "6000") or 6000)

REPLAY_HEADER = "Previously in this chat:"
REPLAY_OMITTED = "(older turns omitted)"

# Bytes pulled per backwards read of a transcript. One block already covers a
# replay's whole char budget many times over; it only ever grows when a single
# turn is bigger than this.
_TAIL_BLOCK_BYTES = 64 * 1024

# Retention for the live transcript. The file is append-only, so without a
# sweep its footprint grows for the life of the conversation — PERF-138 made
# *reading* it cheap, not writing it. Past ``MAX`` bytes the oldest turns are
# **moved**, never deleted, to ``transcript_archive.jsonl`` beside it; the live
# file keeps the newest ``KEEP`` bytes plus one marker turn naming the sidecar
# and how many turns went into it. Same shape as PERF-136's journal archive and
# for the same reason: this is the user's own chat history, so a retention
# policy whose failure mode is "the chat is gone" is not one worth having.
#
# Bounded in bytes rather than in turns because bytes are what grow: one turn
# runs from ~100 B to ~3 KB (a tool call keeps up to ``TOOL_INPUT_MAX_CHARS`` +
# ``TOOL_OUTPUT_MAX_CHARS`` of payload), so a turn count would pin the
# footprint only to within a factor of thirty. It also makes the trigger a
# single ``stat()`` on the append path rather than a line count.
#
# MAX and KEEP are deliberately far apart: a trim leaves the file at KEEP, so
# the next one is a whole MAX−KEEP of appends away and the rewrite amortizes to
# a constant per byte appended. They are read from the environment here, beside
# ``REPLAY_MAX_CHARS`` and for the same reason, rather than from
# ``timeouts.py``, whose loader only understands deadlines in seconds.
TRANSCRIPT_MAX_BYTES = int(
    os.environ.get("CONDOR_TRANSCRIPT_MAX_BYTES", "2097152") or 2097152
)
TRANSCRIPT_KEEP_BYTES = int(
    os.environ.get("CONDOR_TRANSCRIPT_KEEP_BYTES", "1048576") or 1048576
)

# The kept tail is never allowed below this multiple of the replay budget, and
# that is the load-bearing half of the policy: ``replay_context`` reads the live
# file only, so a trim must never be able to change what a resumed session is
# handed. Holding the tail well above ``REPLAY_MAX_CHARS`` guarantees a trim
# only ever removes turns that were already past the replay bound — the replay
# is identical before and after. A rendered replay line is a strict subset of
# the JSON line it came from, so bytes here dominate chars there; the multiple
# is slack on top of that.
_REPLAY_HEADROOM = 4

# The turn left behind in place of the archived ones. A real ``system`` entry
# rather than a bespoke line format, so every existing reader already handles
# it: the dashboard renders it like any other system note, ``read_transcript``
# returns it, and ``_render_turn`` would replay it as "(N older turns moved to
# …)" if a replay window ever reached back that far. The count is folded
# forward on each trim, so the file states the running total and never claims
# to be a complete record when it is not.
ARCHIVE_MARKER_KIND = "archived"
_ARCHIVE_MARKER_RE = re.compile(r"^(\d+) older turns? moved to ")

TITLE_MAX_CHARS = 80
SNIPPET_MAX_CHARS = 160

# Upper bounds on the tool IO kept per call, in characters. Load-bearing, not
# defensive: a tool result is routinely a market-data dump, a whole portfolio
# payload or a base64 image — kept verbatim, one turn could outweigh the
# entire conversation, slow every read of the file and, since PERF-170, push
# the rest of the chat into the archive on its own. They live here, beside
# ``REPLAY_MAX_CHARS`` and ``TRANSCRIPT_MAX_BYTES``, and for the
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
    """``paths.safe_id`` under this module's own error type.

    The guard is shared (one regex, in ``condor.paths``); the exception is not,
    because ``chat_ws`` and the conversations route both catch
    :class:`ConversationIdError` to answer 400 rather than 500.
    """
    try:
        return paths.safe_id(value)
    except paths.UnsafeIdError as exc:
        raise ConversationIdError(
            f"Invalid conversation id {value!r}: "
            "use letters, digits, dot, dash or underscore."
        ) from exc


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


def _dict_or_none(raw) -> dict | None:
    """``raw`` as arguments the shared fold can read, or ``None``.

    ``fold_tool_call_event`` guards ``input`` with a plain truthiness test so a
    late, fuller payload can fill a field an earlier event left empty. Handing
    it anything that is not a non-empty dict — a serialized scalar, ``{}`` from
    an announcement whose input is still streaming — would either be ignored or
    stored as something a reader of ``input`` cannot use.
    """
    return raw if isinstance(raw, dict) and raw else None


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

    ``share_delete_token`` is the one field that is read from disk but never
    serialized. It is a *capability*: whoever holds it can delete the shared
    row from the collector, which is exactly why the owner keeps it and nobody
    else receives it. Every route that returns a conversation dumps this model,
    and one of them (``?user_id=``, admin only) returns someone else's — so a
    plain field would hand an admin the token for a share that is not theirs to
    revoke. ``exclude=True`` keeps it out of every dump; writes go through
    ``update_meta``'s explicit kwargs, which are unaffected.
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

    # ── Sharing (FEAT-054) ──
    # Optional, defaulted, and tolerated in both directions like every other
    # field here: a meta written before this existed loads unchanged, and one
    # written by a newer build still loads in an older one.
    share_id: str = Field(
        default="",
        description="Stable per conversation once shared; '' = never shared.",
    )
    share_revision: int = Field(
        default=0, description="Bumped on each re-share; the server upserts on it."
    )
    shared_at: datetime | None = None
    share_delete_token: str = Field(
        default="",
        exclude=True,
        description="The capability that revokes the share. Local only.",
    )

    # ── Automatic sharing (FEAT-055) ──
    share_excluded: bool = Field(
        default=False,
        description="The user took this one chat out of the sweep. Honoured forever.",
    )
    share_turn_count: int = Field(
        default=0,
        description="``turn_count`` at the last share; growth past it re-shares.",
    )
    multi_author: bool = Field(
        default=False,
        description=(
            "Another human can speak in the room this was born in — a Telegram "
            "group. The sweep never takes one: a user consents for themselves."
        ),
    )

    # ── Reflection (FEAT-073) ──
    # The marker is the *attempt*, not the success: a conversation whose answer
    # could not be parsed is stamped all the same, because retrying an
    # unparseable answer forever on a job queue is how a background pass becomes
    # a token leak. ``reflected_ok`` is what distinguishes "we learned nothing
    # from it" from "we never looked", which is a support question, not a
    # scheduling one.
    reflected_at: datetime | None = None
    reflected_ok: bool = Field(
        default=False, description="Did the pass actually learn something?"
    )


class TurnEntry(BaseModel):
    """One line of the transcript.

    The shape grows over time, so the line format is deliberately tolerant in
    both directions: every field past ``role`` carries a default, so a line
    written before a field existed still parses, and unknown keys are ignored,
    so a line written by a newer build still loads here. Anything added later
    must keep both halves of that bargain — optional, with a default that reads
    as "not recorded" rather than as a real value. That default is also what
    makes the shape safe to grow: ``condor.sharing.scrub`` reads its redaction
    coverage off these fields rather than naming them, so a text field added
    here is scrubbed the day it is added, and one whose type the scrubber cannot
    walk is dropped to its default instead of being shared raw.

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

    ``events`` is the *order* the run happened in, which ``thought`` and
    ``tool_calls`` cannot express: a turn that thinks, calls a tool, thinks
    again and calls a second one lands in those two fields as one merged
    reasoning blob beside a flat list, and no reader can put the four steps
    back. It is additive and derived — the two flat fields are still written
    in full — so every existing reader (``replay_context``, ``condor.sharing``,
    Telegram, an older client) is untouched, and a turn recorded before this
    existed simply has an empty list, which reads as "the order was not kept"
    rather than as "nothing happened".
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
        default="",
        description=(
            "System entries only: switch | error | delegation | resume | "
            "notification."
        ),
    )
    ts: float = Field(default_factory=time.time)
    agent_key: str = Field(default="", description="Model that produced this turn.")
    agent_slug: str = Field(
        default="", description="Bound Agent; '' = the default one, Condor."
    )
    stop_reason: str = Field(
        default="", description="Assistant turns: how the stream ended; '' = unknown."
    )
    attachments: list[dict] = Field(
        default_factory=list,
        description=(
            "User turns: what was handed over with the words. Per element "
            "{id, mime, bytes} — an id under this conversation's attachments/ "
            "directory, its type, and its size. Never the payload and never a "
            "filename."
        ),
    )
    events: list[dict] = Field(
        default_factory=list,
        description=(
            "The run in the order it happened: {type: 'thought', text} and "
            "{type: 'tool', id} naming an entry of tool_calls. Derived — the "
            "reasoning is the same text as thought, and the tool detail is not "
            "repeated here. Empty = order not recorded (pre-ARCH-330 turns)."
        ),
    )


# ── Paths ──


def _user_dir(user_id: int | str) -> Path:
    """This user's conversations. The store is partitioned by owner (FEAT-051)."""
    return paths.conversations_dir(_validate(str(user_id)))


def _conv_dir(user_id: int | str, conv_id: str) -> Path:
    return paths.conversation_dir(_validate(str(user_id)), _validate(str(conv_id)))


# ── Store ──


def new_conversation(
    user_id: int,
    surface: str = "",
    *,
    agent_key: str = "",
    agent_slug: str = "",
    server_name: str | None = None,
    multi_author: bool = False,
) -> ConversationMeta:
    """Mint an empty conversation and persist its meta.

    ``multi_author`` is recorded at birth because that is the only moment the
    fact is known: a turn carries which *brain* produced it but not which human
    typed it, and the recorder is built from the session's owner, so by the time
    a second person's words are on disk they are indistinguishable from the
    owner's. The caller who provisioned the session knows whether the room admits
    anyone else, and says so here (FEAT-055).
    """
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
        multi_author=bool(multi_author),
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


def _meta_mtime(base: Path, name: str) -> int:
    """When this conversation's meta was last written, in ns, or 0 if unreadable.

    Nanoseconds and not ``st_mtime``: a float epoch loses resolution below a
    fraction of a microsecond, and conversations minted back to back are that
    close. A missing or unstatable meta sorts last, which is also where a
    directory with nothing to parse belongs.
    """
    try:
        return (base / name / META_FILENAME).stat().st_mtime_ns
    except OSError:
        return 0


def list_conversations(user_id: int, *, limit: int = 100) -> list[ConversationMeta]:
    """This user's conversations, newest first.

    Only as many ``meta.json`` files are parsed as the caller asked for. The
    store is never pruned, so N grows for the life of the install, and reading
    every meta to hand back one row is a cost the dashboard rail and its
    prewarm used to pay on the same event loop as the chat socket (PERF-328).
    Instead each directory is stat'ed — one syscall, no read, no parse — and
    the candidates are walked newest ``meta.json`` first, stopping as soon as
    ``limit`` metas have parsed. ``write_status`` stamps ``updated_at`` in the
    same breath it renames the file into place, so the mtime order and the
    ``updated_at`` order are the same order; the result is still sorted on
    ``updated_at`` so the contract does not lean on the filesystem's clock.

    ``limit=0`` walks everything, which the sharing sweep and reflection need.
    Reads only ``meta.json`` per directory. One without a readable meta (hand
    deleted, half written) is skipped rather than failing the whole listing.
    """
    base = _user_dir(user_id)
    if not base.is_dir():
        return []

    try:
        with os.scandir(base) as entries:
            names = [entry.name for entry in entries if entry.is_dir()]
    except OSError:
        return []

    if limit:
        # Name breaks an mtime tie so two metas written inside one filesystem
        # tick keep the ascending-name order the old full sort gave them.
        names.sort(key=lambda name: (-_meta_mtime(base, name), name))
    else:
        # Every meta is parsed anyway, so a caller asking for all of them
        # should not also pay for a stat per conversation.
        names.sort()

    metas: list[ConversationMeta] = []
    for name in names:
        meta = get_conversation(user_id, name)
        if meta is None:
            continue
        metas.append(meta)
        if limit and len(metas) >= limit:
            break

    metas.sort(key=lambda m: m.updated_at, reverse=True)
    return metas


def _iter_lines_reverse(path: Path, *, block: int | None = None) -> Iterator[bytes]:
    """The file's non-blank lines, newest first, reading only what is consumed.

    Opened in binary and split on ``b"\\n"``: no UTF-8 continuation byte is
    ``0x0A``, so a block boundary can cut a character in half but never a line.
    A record is always one line — ``append_turn`` writes ``json.dumps`` output,
    which escapes newlines — so a line is a whole turn, and the last one is
    yielded whether or not it ends in a newline (a torn append reads as a
    malformed line, exactly as it does on the forward path).
    """
    size = block or _TAIL_BLOCK_BYTES
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        carry = b""
        while pos > 0:
            step = min(size, pos)
            pos -= step
            fh.seek(pos)
            lines = (fh.read(step) + carry).split(b"\n")
            # The first piece may continue into the block before this one.
            carry = lines.pop(0)
            for line in reversed(lines):
                line = line.strip()
                if line:
                    yield line
        carry = carry.strip()
        if carry:
            yield carry


def _iter_turns_reverse(path: Path) -> Iterator[TurnEntry]:
    """Parsed turns, newest first, parsing only the lines the caller pulls.

    Same tolerance as the forward read: a line that will not parse is skipped,
    an unreadable file is empty rather than fatal.
    """
    try:
        if not path.is_file():
            return
        for line in _iter_lines_reverse(path):
            try:
                yield TurnEntry(**json.loads(line.decode("utf-8")))
            except Exception:  # noqa: BLE001 - one bad line is not the file
                continue
    except OSError:
        log.debug("Unreadable transcript at %s", path, exc_info=True)


def _read_turns(path: Path) -> list[TurnEntry]:
    """Every turn in one file, oldest first. Absent or unreadable reads empty."""
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
    return entries


def read_transcript(
    user_id: int,
    conv_id: str,
    *,
    limit: int = 200,
    include_archive: bool = False,
) -> list[TurnEntry]:
    """The tail of a transcript. ``limit=0`` means all of it.

    A line that fails to parse is skipped, never fatal — the same tolerance
    ``read_status()`` shows for a truncated status file.

    A bounded tail is read backwards from the end, so a conversation with
    thousands of turns costs its tail rather than its whole history; only
    ``limit=0`` still walks the file.

    "All of it" means the live transcript, which retention bounds — a long
    conversation's older turns live in the sidecar and are represented in the
    result by the archive marker turn, so a caller is never quietly handed a
    partial history that looks whole. ``include_archive=True`` is the full
    record: the sidecar is read too and the marker is dropped, because with
    both files in hand nothing is missing for it to mark. It costs the whole
    history by definition, so it belongs to an export or a support dig, never
    to a request path.
    """
    conv_dir = _conv_dir(user_id, conv_id)
    path = conv_dir / TRANSCRIPT_FILENAME
    archive = conv_dir / TRANSCRIPT_ARCHIVE_FILENAME

    if limit:
        turns: Iterator[TurnEntry] = _iter_turns_reverse(path)
        if include_archive:
            turns = (
                turn
                for turn in chain(turns, _iter_turns_reverse(archive))
                if not _is_archive_marker(turn)
            )
        tail = list(islice(turns, limit))
        tail.reverse()
        return tail

    if not include_archive:
        return _read_turns(path)
    return [
        turn
        for turn in chain(_read_turns(archive), _read_turns(path))
        if not _is_archive_marker(turn)
    ]


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

    try:
        _trim_transcript(conv_dir)
    except Exception:  # noqa: BLE001 - retention must not break a live prompt
        log.warning("Could not trim transcript for %s", conv_dir, exc_info=True)

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


# ── Retention ──


def _keep_bytes() -> int:
    """How much of the newest transcript a trim has to leave behind."""
    return max(TRANSCRIPT_KEEP_BYTES, REPLAY_MAX_CHARS * _REPLAY_HEADROOM)


def _is_archive_marker(turn: TurnEntry) -> bool:
    return turn.role == "system" and turn.kind == ARCHIVE_MARKER_KIND


def _archived_count(line: bytes) -> int | None:
    """Turns already archived according to ``line``; None if it is no marker.

    A marker we cannot read the count out of returns 0 rather than None: it is
    still a marker, so it must be replaced instead of kept as a turn, and
    under-counting the total beats emitting two markers.
    """
    try:
        data = json.loads(line.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or data.get("kind") != ARCHIVE_MARKER_KIND:
        return None
    match = _ARCHIVE_MARKER_RE.match(str(data.get("text") or ""))
    return int(match.group(1)) if match else 0


def _archive_marker(count: int) -> bytes:
    entry = TurnEntry(
        role="system",
        kind=ARCHIVE_MARKER_KIND,
        text=f"{count} older turns moved to {TRANSCRIPT_ARCHIVE_FILENAME}",
    )
    return json.dumps(entry.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")


def _append_archive(conv_dir: Path, lines: list[bytes]) -> None:
    """Append retired turns to the sidecar, durably.

    ``fsync``-ed before the caller republishes the live file, because until
    this returns the live file is the only copy of these turns.
    """
    with (conv_dir / TRANSCRIPT_ARCHIVE_FILENAME).open("ab") as fh:
        fh.write(b"\n".join(lines) + b"\n")
        fh.flush()
        os.fsync(fh.fileno())


def _trim_transcript(conv_dir: Path) -> None:
    """Move the oldest turns of an oversized transcript into the sidecar.

    Archive first, then republish the live file, so every crash window fails
    safe. Before the archive write: the transcript is untouched. Between the
    two: the moved turns exist in *both* files, and a duplicate in a sidecar
    nobody reads on the hot path is a far better outcome than a hole in
    someone's chat. During the republish: ``atomic_write_bytes`` publishes by
    rename, so a reader sees the whole old file or the whole new one and a
    crash leaves the old one intact — the trim simply happens again on the
    next append. There is no ordering in which a turn is lost.

    Whole lines only, and the newest turn is kept unconditionally: a single
    turn larger than the entire budget has to shrink the file, not empty it.
    """
    path = conv_dir / TRANSCRIPT_FILENAME
    try:
        if path.stat().st_size <= TRANSCRIPT_MAX_BYTES:
            return
        raw = path.read_bytes()
    except OSError:
        log.debug("Could not read transcript for trimming at %s", path, exc_info=True)
        return

    lines = [line for line in raw.split(b"\n") if line.strip()]
    if not lines:
        return
    archived = _archived_count(lines[0])
    if archived is not None:
        lines.pop(0)
    if not lines:
        return

    budget = _keep_bytes()
    cut = len(lines) - 1
    kept = len(lines[-1]) + 1
    for index in range(len(lines) - 2, -1, -1):
        size = len(lines[index]) + 1
        if kept + size > budget:
            break
        kept += size
        cut = index

    dropped, keep = lines[:cut], lines[cut:]
    if not dropped:
        return

    try:
        _append_archive(conv_dir, dropped)
    except OSError:
        # Nowhere safe to park them, so nothing moves: the file stays over the
        # bound and the next append retries the trim.
        log.warning("Could not archive turns for %s", conv_dir, exc_info=True)
        return

    marker = _archive_marker((archived or 0) + len(dropped))
    atomic_write_bytes(path, b"\n".join([marker, *keep]) + b"\n")


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

    The walk reads the transcript backwards and parses lazily, so it stops at
    the turn that overruns the budget instead of parsing the whole file first:
    this runs on the session-spawn path.

    Only the live transcript is read, never the archive, and retention is sized
    so that is not a compromise: ``_keep_bytes()`` holds the tail at several
    times ``REPLAY_MAX_CHARS``, so a trim can only ever remove turns this walk
    had already stopped short of. The replay a resumed session receives is
    therefore byte-identical before and after a trim. Where the replay does
    stop is stated in the output itself, by the ``REPLAY_OMITTED`` footer —
    which has always been the honest boundary here, since the budget cuts the
    history long before retention does.
    """
    turns = _iter_turns_reverse(_conv_dir(user_id, conv_id) / TRANSCRIPT_FILENAME)
    newest_turn = next(turns, None)
    if newest_turn is None:
        return ""

    # Budget the body so header and footer fit inside the caller's bound.
    overhead = len(REPLAY_HEADER) + 1 + len(REPLAY_OMITTED) + 1
    budget = max_chars - overhead
    if budget <= 0:
        return REPLAY_HEADER[:max_chars]

    lines: list[str] = []
    used = 0
    omitted = False
    newest = ""
    for turn in chain([newest_turn], turns):
        rendered = _render_turn(turn)
        if not rendered:
            continue
        if not newest:
            newest = rendered
        if used + len(rendered) + 1 > budget:
            omitted = True
            break
        lines.append(rendered)
        used += len(rendered) + 1

    if not lines:
        # Even the newest turn alone overruns the budget. Keeping a truncated
        # head of it beats returning nothing at all.
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
        user_kind: str = "",
        attachments: list[dict] | None = None,
    ):
        self.enabled = bool(conv_id) and user_id is not None
        self.user_id = user_id
        self.conv_id = conv_id
        self._user_text = user_text
        # A turn nobody typed (a background task waking the chat, FEAT-034) is
        # recorded as a system entry of this kind. Recording it as ``user``
        # would put words in the user's mouth, and ``_render_turn`` would then
        # replay them to the next session as something they said.
        self._user_kind = user_kind
        # Keyword-only and defaulted: a caller that does not know who answered
        # records an unattributed turn instead of failing to record at all.
        self._agent_key = agent_key
        self._agent_slug = agent_slug
        # What was handed over with the words (FEAT-098). Beside ``user_text``
        # because it belongs to the same turn and is written by the same append:
        # the bytes are already on disk under the conversation, so this is the
        # reference that lets a reload put the picture back in the user's own
        # bubble instead of only the model remembering it.
        self._attachments = list(attachments or [])
        self._text: list[str] = []
        self._thought: list[str] = []
        # Keyed by tool_call_id, in the shape ``fold_tool_call_event`` folds
        # into — the fold's spelling, not disk's. ``_recorded_calls`` renames
        # and bounds it on the way out.
        self._tools: dict[str, dict] = {}
        # The order those two were produced in (ARCH-330). A third accumulator
        # rather than a replacement for either, because ``thought`` and
        # ``tool_calls`` are the shape every existing reader of a transcript
        # already knows: this says how they interleaved, and they still say
        # what was in them.
        self._events: list[dict] = []
        self._error = ""
        # Stays empty unless a DONE arrives: an abandoned generator never
        # reports an ending, and "unknown" is the honest record of that.
        self._stop = ""
        self._flushed = False
        if self.enabled:
            _live_recorders.add(self)

    def observe(self, event) -> None:
        """Accumulate one ``RuntimeEvent``. Never writes.

        The tool-call reduction is :func:`fold_tool_call_event`, the same one
        the tick engine, the delegate sink and ``Session.prompt_stream`` run —
        not a fourth copy of the create/patch rules. The recorder used to fold
        by hand and disagreed with the shared one on the part that matters: a
        repeat announcement replaced the entry wholesale, and an update merged
        only ``status`` and ``output``, so arguments that the ACP adapter
        supplies late (FEAT-102) never reached disk and every ACP-bridged chat
        turn was persisted with ``"input": null``.

        What stays the recorder's own business is what disk asks of it, and it
        happens at :meth:`_recorded_calls` rather than here: redaction and
        clipping of the arguments, truncation of the output, and the on-disk
        key naming. Doing it after the fold rather than during is deliberate —
        redacting on arrival would hand the fold an already-clipped ``input``
        and a later, fuller one could no longer replace it.

        Arrival order is the one thing that *cannot* be recovered later, so it
        is recorded here as it happens (ARCH-330): a reasoning step extends the
        run's trailing thought entry, a newly announced call appends a step
        naming it. Both accumulators keep being filled beside it — this only
        remembers how they took turns.
        """
        if not self.enabled:
            return
        if event.type == EventType.TEXT:
            self._text.append(event.text)
        elif event.type == EventType.THOUGHT:
            self._thought.append(event.text)
            self._note_thought(event.text)
        elif event.type == EventType.TOOL_CALL:
            # Not keyed on the id alone: an adapter that does not identify its
            # calls still gets one entry per call rather than one entry
            # overwritten N times.
            call_id = str(event.field("tool_call_id") or len(self._tools))
            created = fold_tool_call_event(
                self._tools,
                ToolCallEvent(
                    tool_call_id=call_id,
                    # The transcript is the record, and it is read forever, so
                    # a title that says nothing is recognised as nothing here
                    # rather than written down verbatim (CORR-327). The same
                    # seam the ACP client normalizes on, applied again at the
                    # last gate before disk because a producer that is not the
                    # ACP wire (the pydantic-ai client, the tick relay) reaches
                    # the recorder without passing it.
                    title=normalize_tool_title(event.field("title"))
                    or normalize_tool_title(event.field("kind")),
                    status=str(event.field("status") or ""),
                    kind=str(event.field("kind") or ""),
                    input=_dict_or_none(event.field("input")),
                ),
            )
            # The fold returns the entry only when it *created* one, so a
            # re-announcement of a call already in flight patches it without
            # putting a second step in the run.
            if created is not None:
                self._events.append({"type": "tool", "id": call_id})
        elif event.type == EventType.TOOL_UPDATE:
            fold_tool_call_event(
                self._tools,
                ToolCallUpdate(
                    tool_call_id=str(event.field("tool_call_id") or ""),
                    status=str(event.field("status") or ""),
                    # No ``kind`` fallback on an update, unlike the create
                    # above: the fold overwrites the name whenever the update
                    # carries one, so an update whose title says nothing must
                    # stay empty and leave the announced name standing.
                    title=normalize_tool_title(event.field("title")),
                    output=str(event.field("output") or ""),
                    input=_dict_or_none(event.field("input")),
                ),
            )
        elif event.type == EventType.ERROR:
            self._error = str(event.field("message", "") or "")
        elif event.type == EventType.DONE:
            self._stop = event.stop_reason

    def _note_thought(self, text: str) -> None:
        """Extend the run's trailing reasoning step, or open a new one.

        Reasoning arrives one token-sized chunk at a time, so a step per chunk
        would be thousands of entries saying nothing about order. Consecutive
        chunks are one step, and only a tool call landing between them starts
        another — which is exactly the interleaving this list exists to keep.
        Merging is also what makes the list a faithful projection: the steps'
        text concatenates back to ``thought`` byte for byte.
        """
        if not text:
            return
        if self._events and self._events[-1]["type"] == "thought":
            self._events[-1]["text"] += text
        else:
            self._events.append({"type": "thought", "text": text})

    def _recorded_events(self, calls: list[dict]) -> list[dict]:
        """The run's order in the shape the transcript stores.

        A tool step *names* its call rather than repeating it. The payload is
        redacted, clipped and truncated once, in ``tool_calls``, and copying it
        here would both double the line and give a reader two versions of one
        call to disagree about. A step naming a call that did not reach disk is
        dropped, so the two fields can never contradict each other.
        """
        known = {str(call.get("id") or "") for call in calls}
        return [
            dict(event)
            for event in self._events
            if event["type"] != "tool" or event["id"] in known
        ]

    def _recorded_calls(self) -> list[dict]:
        """The folded calls in the shape the transcript stores.

        The fold speaks the key names the tick and delegate paths share
        (``name``); a transcript has always said ``title``, and
        ``TurnEntry.tool_calls`` documents that spelling to every reader of a
        share. One rename, in one place, instead of a second fold.

        ``output`` is defaulted to ``""`` rather than omitted so a call that
        never reported a result still lands on disk with the same key set as
        one that did.
        """
        return [
            {
                "id": str(call.get("id") or ""),
                "title": str(call.get("name") or ""),
                "status": str(call.get("status") or ""),
                "kind": str(call.get("kind") or ""),
                "input": _tool_input(call.get("input")),
                "output": _truncate(call.get("output") or "", TOOL_OUTPUT_MAX_CHARS),
            }
            for call in self._tools.values()
        ]

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
            opening = (
                TurnEntry(role="system", text=self._user_text, kind=self._user_kind)
                if self._user_kind
                else TurnEntry(
                    role="user",
                    text=self._user_text,
                    attachments=self._attachments,
                )
            )
            append_turn(self.user_id, self.conv_id, opening)
            text = "".join(self._text)
            tools = self._recorded_calls()
            if text or tools or self._thought:
                append_turn(
                    self.user_id,
                    self.conv_id,
                    TurnEntry(
                        role="assistant",
                        text=text,
                        thought="".join(self._thought),
                        tool_calls=tools,
                        events=self._recorded_events(tools),
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
