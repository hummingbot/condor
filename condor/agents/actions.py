"""What an agent actually did, per tick (FEAT-097).

Every agent surface used to answer *"what did it do?"* with the agent's own
narration: the fleet band's line is the journal's ``Last action:``, which is
``response_text[:100]`` — the first hundred characters of what the model
*wrote*. The deed itself left no structured trace anywhere. A deploy wrote only
``owned_bots.json``; an executor create, a stop and a config edit wrote nothing
local at all. The one record was the ``## Tool Calls`` section of a snapshot —
human markdown, output clipped at 2000 chars, the last 100 ticks only, and
nothing anywhere parsed it back.

This module is that record, and it is almost entirely a *recovered* one: the
tick's streaming loop already folds every tool call into
``{"id", "name", "status", "kind", "input", "output"}`` (``fold_tool_call_event``,
:mod:`condor.acp.client`) and then hands the list to exactly one consumer, the
markdown snapshot writer. Everything a row needs — the name, the arguments, the
outcome, and a written-and-tested one-line rendering of all three
(:func:`condor.runtime.danger.format_tool_summary`) — is already assembled on
the stack at the end of the tick. What was missing was a file.

**One JSON object per line, append-only, bounded, read tail-first.** The shape
``transcript.jsonl`` already uses (:mod:`condor.runtime.conversations`), for the
same reasons and with the same retention rule: past
:data:`MAX_ACTION_LINES` the oldest rows are **moved** to
``actions_archive.jsonl``, never deleted.

Two things this deliberately does not do. It does not record *reads* — a tick
reads far more than it writes and the read is already in the snapshot — and it
does not store a tool's **result**, only its arguments as rendered for the
confirmation prompt. "Created executor X" is therefore not in the log; the id is
recoverable by joining the tick to the executors the fleet already lists, and
parsing tool results is exactly the brittleness this design exists to avoid.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from condor.runtime.danger import (
    format_tool_summary,
    is_mutating_tool_call,
    tool_call_input,
    tool_call_name,
)

log = logging.getLogger(__name__)

ACTIONS_FILENAME = "actions.jsonl"
ACTIONS_ARCHIVE_FILENAME = "actions_archive.jsonl"

# Retention for the live file. ~0.3 actions per tick on a 60s loop is ~400
# rows/day, so this is roughly five days live with everything older in the
# archive beside it. Bounded in lines rather than bytes (the transcript's
# choice) because a row here is a fixed handful of short fields, so a line
# count pins the footprint closely and keeps the trim a single read.
MAX_ACTION_LINES = 2000

# How much of a failed call's output is kept. Enough to name the failure,
# nowhere near enough to make the log a second copy of the snapshot.
MAX_ERROR_CHARS = 400

# The tools that carry their verb in an ``action`` argument. For these the
# queryable key is ``tool:action`` — ``manage_bots`` alone would collapse a
# deploy and a stop into one verb. ``manage_gateway_config`` is here for the
# same reason even though the *gate* reads its resource type instead: the log's
# question is whether it edited, and the action is what says so.
_DISPATCH_TOOLS = frozenset(
    {
        "manage_bots",
        "manage_clmm",
        "manage_amm",
        "control_agent",
        "manage_gateway_config",
    }
)

# Statuses that mean the call never ran because something said no. They read as
# not-ok with no output at all, so they need a word of their own.
_REFUSED_STATUSES = frozenset({"blocked", "cancelled", "canceled", "rejected"})


@dataclass(frozen=True)
class AgentAction:
    """One mutating tool call, as the page reports it."""

    #: Joins to ``snapshot_{tick}.md``, the journal's Ticks line and the canvas.
    tick: int
    #: Epoch seconds — the ``LiveLoop.last_tick_at`` convention.
    at: float
    #: ``"create_lp_executor"``, with any MCP prefix stripped.
    tool: str
    #: ``"create_lp_executor"`` or ``"manage_bots:deploy"`` — the queryable key.
    verb: str
    #: The human line, from the confirmation prompt's own renderer.
    summary: str
    #: Whether the call completed. Anything else is not a success.
    ok: bool
    #: Clipped output when not ok, else ``""``.
    error: str = ""


def _as_gate_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    """A folded tool call in the shape :mod:`condor.runtime.danger` reads.

    The two shapes disagree on one key and it matters: ``fold_tool_call_event``
    stores the tool's name under ``name``, while everything in ``danger`` reads
    ``tool`` (or ``title``). Left untranslated every call would resolve to the
    empty name, so nothing would be mutating and every summary would read
    "Unknown" — the log would be silently, uniformly empty.

    Translated here rather than by widening ``tool_call_name``: that function is
    the confirmation gate's own, and a new name fallback there changes which
    calls stop a human.
    """
    return {
        "tool": tool_call.get("name") or tool_call.get("tool") or "",
        "title": tool_call.get("title") or "",
        "input": tool_call.get("input"),
    }


def _verb(tool: str, call: dict[str, Any]) -> str:
    """``tool`` for a tool that is its own verb, ``tool:action`` otherwise."""
    if tool not in _DISPATCH_TOOLS:
        return tool
    args = tool_call_input(call)
    action = args.get("action") if isinstance(args, dict) else None
    return f"{tool}:{action}" if isinstance(action, str) and action else tool


def _error_text(tool_call: dict[str, Any], status: str) -> str:
    """Why a call is not a success, in as few characters as say it."""
    output = tool_call.get("output")
    text = str(output)[:MAX_ERROR_CHARS].strip() if output else ""
    if text:
        return text
    if status in _REFUSED_STATUSES:
        return "refused"
    return status or "did not complete"


def actions_from_tool_calls(
    tool_calls: list[dict[str, Any]], *, tick: int, at: float
) -> list[AgentAction]:
    """The mutating calls of one tick, in stream order.

    Pure: no clock, no filesystem, no engine. Every row of a tick shares that
    tick's timestamp — ordering *within* a tick is list order, which is the
    order the model made the calls in.
    """
    actions: list[AgentAction] = []
    for tool_call in tool_calls:
        try:
            call = _as_gate_call(tool_call)
            if not is_mutating_tool_call(call):
                continue
            status = str(tool_call.get("status") or "")
            ok = status == "completed"
            tool = tool_call_name(call)
            actions.append(
                AgentAction(
                    tick=tick,
                    at=at,
                    tool=tool,
                    verb=_verb(tool, call),
                    summary=format_tool_summary(call),
                    ok=ok,
                    error="" if ok else _error_text(tool_call, status),
                )
            )
        except Exception:  # noqa: BLE001 - one odd call must not lose the rest
            log.debug("actions: skipping an unreadable tool call", exc_info=True)
    return actions


def append_actions(session_dir: Path | None, actions: list[AgentAction]) -> None:
    """Append a tick's actions to the session's log. Never raises.

    A failed write must never cost a tick, so every failure is swallowed the way
    :func:`condor.agents.run_records.record_run` swallows its own. Experiments
    keep no journal and have no ``session_dir``; they keep no action log either
    — their snapshot already holds their tool calls.
    """
    if session_dir is None or not actions:
        return
    path = session_dir / ACTIONS_FILENAME
    try:
        with path.open("a", encoding="utf-8") as fh:
            for action in actions:
                fh.write(json.dumps(asdict(action), ensure_ascii=False) + "\n")
    except OSError:
        log.debug("actions: could not append to %s", path, exc_info=True)
        return

    try:
        _trim(path)
    except OSError:
        # The rows are on disk either way; the file stays over the cap and the
        # next tick's trim retries. Nothing is dropped on a failed trim.
        log.debug("actions: could not trim %s", path, exc_info=True)


def _trim(path: Path) -> None:
    """Move the head of an over-long log to the archive beside it.

    Moved, not deleted — the journal (``_archive_lines``) and the chat
    transcript both park their overflow rather than destroy it, and a record of
    what an agent did with real money is not the place to start deleting.
    """
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if len(lines) <= MAX_ACTION_LINES:
        return
    dropped = lines[: len(lines) - MAX_ACTION_LINES]
    with (path.parent / ACTIONS_ARCHIVE_FILENAME).open("a", encoding="utf-8") as fh:
        fh.writelines(dropped)
    # Only after the archive write landed: a crash between the two leaves the
    # rows duplicated, which is recoverable, rather than gone, which is not.
    path.write_text("".join(lines[len(lines) - MAX_ACTION_LINES :]), encoding="utf-8")


def _parse(line: str) -> AgentAction | None:
    """One line into a row, or ``None`` for anything that is not one.

    A half-written line after a crash must not 500 the route that reads it.
    """
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return AgentAction(
            tick=int(data.get("tick", 0) or 0),
            at=float(data.get("at", 0.0) or 0.0),
            tool=str(data.get("tool", "") or ""),
            verb=str(data.get("verb", "") or ""),
            summary=str(data.get("summary", "") or ""),
            ok=bool(data.get("ok", False)),
            error=str(data.get("error", "") or ""),
        )
    except (TypeError, ValueError):
        return None


def read_actions(session_dir: Path | None, *, limit: int = 100) -> list[AgentAction]:
    """The last ``limit`` actions of a session, oldest-first. Never raises.

    Only the live file: the archive is the record's tail, not its head, and a
    reader asking for the last hundred deeds has never wanted it.
    """
    if session_dir is None:
        return []
    try:
        lines = (
            (session_dir / ACTIONS_FILENAME).read_text(encoding="utf-8").splitlines()
        )
    except OSError:
        return []
    rows = [row for row in (_parse(line) for line in lines[-limit:]) if row]
    return rows


def latest_action(session_dir: Path | None) -> AgentAction | None:
    """The last thing this session did, or ``None`` if it has done nothing.

    One tail read per live engine, which is what the fleet band costs. It is not
    memoised for the same reason the band's journal read is not: the band's job
    is to say what the loop is doing *now*.
    """
    if session_dir is None:
        return None
    try:
        lines = (
            (session_dir / ACTIONS_FILENAME).read_text(encoding="utf-8").splitlines()
        )
    except OSError:
        return None
    for line in reversed(lines):
        row = _parse(line)
        if row is not None:
            return row
    return None


# ── The tick a record came from (FEAT-100) ──

# Which verbs create which kind of thing. The log records arguments only — never
# a tool's result — so a created bot's instance name and a created executor's id
# are not in it. The join is therefore on *time*: a record that started at ``at``
# most likely came from the nearest preceding call that creates that kind of
# thing. That is a heuristic, and the ledger labels it as one by leaving the cell
# blank rather than guessing when nothing matches.
CREATE_VERBS: dict[str, frozenset[str]] = {
    "bot": frozenset({"manage_bots:deploy"}),
    "executor": frozenset(
        {
            "create_grid_executor",
            "create_position_executor",
            "create_dca_executor",
            "create_lp_executor",
            "create_order_executor",
        }
    ),
}

# How far back a create may sit from the record it produced. A deploy is slow and
# a tick is typically 30–60 minutes, so a nearest-preceding match inside fifteen
# minutes is safe in practice; two creates of the same kind inside one tick both
# credit that tick, which is correct. If this proves noisy the honest fix is
# recording result ids in the log, not a cleverer heuristic here.
TICK_JOIN_WINDOW = 900.0


def tick_for(
    rows: list[AgentAction],
    kind: str,
    at: float,
    window: float = TICK_JOIN_WINDOW,
) -> int | None:
    """The tick whose creating call most likely produced a record starting at ``at``.

    The nearest **preceding** successful call of the right kind, inside
    ``window`` seconds. ``None`` for everything else — no log at all, a stale
    window, a bot adopted rather than deployed — because a fabricated tick number
    would be worse than a blank cell.

    Pure: no HTTP and no filesystem in the signature, so the whole rule is
    reachable from a test.
    """
    verbs = CREATE_VERBS.get(kind)
    if not verbs or at <= 0 or window <= 0:
        return None
    best: AgentAction | None = None
    for row in rows:
        # A call that failed created nothing, so it can never explain a record.
        if not row.ok or row.verb not in verbs:
            continue
        delta = at - row.at
        if delta < 0 or delta > window:
            continue
        if best is None or row.at >= best.at:
            best = row
    return best.tick if best is not None else None
