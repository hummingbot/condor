"""DELEGATE -- fire-and-forget background agent tasks.

DELEGATE is the async, *unattended* sibling of CONSULT
(:mod:`condor.agents.consult`). Where CONSULT runs an Agent's brain to completion
and blocks until it can return an answer (mutations human-gated), DELEGATE hands a
one-off, goal-oriented task to a *detached* Agent instance that works autonomously
until ``client.prompt()`` returns -- the natural "task done" signal -- then notifies
the user with the result.

It is NOT a new engine. It reuses 100% of consult's client/toolset/prompt wiring
via :func:`condor.agents.consult._run_agent_to_completion`, passing
``permission_callback=None`` so an ACP agent auto-approves its own tool calls
(:meth:`condor.acp.client.ACPClient._on_request_permission`). This is the user's
chosen authorization model: full auto-approve, no sandbox (see FEAT-006 Risks).

The registry is in-memory and ephemeral -- a delegation dies with the process. The
*result transcript* is persisted under
``.condor/users/{user_id}/delegations/{task_id}/transcript.md`` so nothing is lost
if you weren't watching -- keyed by the person who asked, beside their
conversations, rather than by the agent that did the work (FEAT-051). Since
FEAT-012 a small ``status.json`` is written alongside it when the task starts, so
a delegation killed by a restart is reported as ``interrupted`` instead of
vanishing without a trace. It is never auto-restarted:
delegations are one-shot and re-running could duplicate side effects.

Ephemeral does not mean free: the registry is bounded on both axes since
CORR-143. A finished delegation is retired once its transcript, events sidecar
and status record are on disk, and only the most recent
:data:`MAX_FINISHED_DELEGATIONS` of them stay resident -- older ones are served
from :mod:`condor.agents.delegation_history` instead. Within one delegation the
event stream is bounded too (:data:`MAX_EVENTS_PER_DELEGATION`, with tool
payloads clipped to what a reader would have been shown anyway), so no single
runaway session can pin an unbounded transcript.

The files behind that fallback are bounded too, since PERF-222. Every finishing
delegation sweeps its own owner's directory
(:func:`prune_delegation_records`), keeping the most recent
:data:`MAX_DELEGATION_RECORDS` terminal records and dropping the rest whole --
so neither the disk nor the walk a history listing does grows with the age of
the install. Nothing still running is ever a candidate.

Since FEAT-058 that directory is no longer only delegations: a *consult* records
itself there too, discriminated by a ``kind`` field, so an agent's page can list
every run it performed rather than only the rare ones handed to the background.
The writing lives in :mod:`condor.agents.run_records`; what stays here is what a
delegation specifically has to say (a transcript, an event stream, a tool count)
and a retention cap of its own, so the plentiful kind cannot evict the rare one.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from condor.agents.run_records import (
    KIND_CONSULT,
    KIND_DELEGATE,
    RECORD_KINDS,
    TERMINAL_STATES,
    record_run,
)

log = logging.getLogger(__name__)

# Module-level registry of live delegations (mirrors engine._engines).
_delegations: dict[str, "DelegateTask"] = {}

# Default per-task wall-clock budget; a hung ACP subprocess is cancelled after this.
DEFAULT_TIMEOUT_S = 900

# Ceiling on a single tool payload wherever a transcript is *read* -- the on-disk
# markdown and the wire projection share it so the two can never disagree about
# where a huge output was cut. Since CORR-143 the event sink applies the same
# ceiling as it folds, so what a delegation keeps in RAM is what every reader
# would have shown anyway; :func:`_clip_output` is idempotent precisely so that
# clipping once at write time and again at read time yields the same string.
MAX_TOOL_OUTPUT = 2000
TRUNCATION_MARKER = "\n… (truncated)"

# How many events one delegation retains. The event stream is the heavy half of
# a DelegateTask, so it is bounded per task and not only across tasks: past this
# the sink drops the oldest events. Generous on purpose -- an ordinary
# delegation never reaches it; what it stops is a runaway session pinning an
# unbounded transcript in a process designed to run for weeks.
MAX_EVENTS_PER_DELEGATION = 500

# How much of one merged thought/text run stays in a single event. Consecutive
# chunks are folded into the last entry, so without a roll-over a chatty agent
# grows one string without bound -- which no count of events can cap. Past this
# the sink opens a new entry, subject to the event bound like every other one.
MAX_CHUNK_CHARS = 4000

# The in-memory record of a cut: the sink drops the oldest events past the bound
# and folds how many into this marker, so a transcript never silently claims to
# be complete. It reaches readers as a plain text note (``events_for_wire``), so
# no client has to learn a new event type.
DROPPED_EVENT_TYPE = "dropped"

# How many *terminal* delegations stay resident (CORR-143). Deliberately a count
# bound and not a TTL: the MCP ``delegate`` contract is submit-now/collect-later
# with no deadline, so there is no grace window that is the right one to expire
# on. What makes a late collect safe is the disk fallback, not a timer --
# ``GET /agents/delegations/{task_id}`` and its ``/events`` sibling both fall
# through to :mod:`condor.agents.delegation_history`, which rebuilds the record
# from the ``status.json`` and ``events.json`` files written
# in ``_run``'s finally block, i.e. before an entry can ever be evicted. The two
# registry-only readers (``GET /agents/delegations`` and the ``/delegations``
# Telegram list) are per-process snapshots by contract and have the merged
# ``/delegations/history`` route as their complete view. In-flight delegations
# are never evicted, at any count.
MAX_FINISHED_DELEGATIONS = 25

# How many *terminal* records one owner keeps on disk (PERF-222). The disk twin
# of the bound above: that one caps what stays in RAM, this one caps what stays
# in ``.condor/users/{id}/delegations/``, which until now nothing ever removed --
# so a listing walked, and stat'd, every delegation the install had ever run.
#
# Deliberately generous: this is a history a person may legitimately scroll, the
# cost of an over-large cap is only a slower listing, and the cost of an
# over-small one is history that is gone for good. ``$CONDOR_MAX_DELEGATION_RECORDS``
# overrides it, and a value of zero or less turns retention off entirely -- an
# install that wants to keep everything says so rather than discovering the
# default the hard way.
MAX_DELEGATION_RECORDS = int(os.environ.get("CONDOR_MAX_DELEGATION_RECORDS", "") or 500)

# The same bound for the *other* kind of run in the same directory (FEAT-058).
# Separate, and swept separately, because the two kinds are nothing alike: a
# consult happens whenever one agent asks another a question, a delegation is a
# deliberate act that cost minutes of unattended work and carries a transcript
# nothing can rebuild. Sharing one budget would let a busy afternoon of consults
# evict every delegation an owner has. 300 is what ``CodeRunStore.MAX_RUNS``
# already settled on for a high-frequency run ledger.
MAX_CONSULT_RECORDS = int(os.environ.get("CONDOR_MAX_CONSULT_RECORDS", "") or 300)


def max_records_for(kind: str) -> int:
    """How many terminal records of ``kind`` one owner keeps on disk.

    A function rather than a mapping so the caps stay *readable at call time*:
    both are module-level names an install (or a test) may repoint, and a dict
    built at import would freeze whatever they were then.
    """
    return MAX_CONSULT_RECORDS if kind == KIND_CONSULT else MAX_DELEGATION_RECORDS


# What happens to the conversation that asked for the work when the task ends.
#
# ``notify`` -- Telegram push, transcript note, and the same line shown in the
#              live conversation (CORR-262/263). The outcome is for the human to
#              read; the agent gets no turn.
# ``resume`` -- additionally wake the live session with the result so the agent
#              continues whatever it was going to do with it (FEAT-034).
ON_COMPLETE_NOTIFY = "notify"
ON_COMPLETE_RESUME = "resume"
ON_COMPLETE_CHOICES = (ON_COMPLETE_NOTIFY, ON_COMPLETE_RESUME)


@dataclass
class DelegateTask:
    task_id: str
    agent_slug: str
    user_id: int
    chat_id: int
    server_name: str | None
    task: str
    status: str = "running"  # running | done | error | stopped
    result: str = ""  # final answer text once done
    error: str = ""
    # The conversation that started this task, when there was one. Empty for
    # delegations with no conversation behind them (a consult, a tick engine, or
    # anything started before provenance existed) -- honest rather than guessed.
    conversation_id: str = ""
    # The session that asked, and what it wants when the task ends. The
    # conversation id alone cannot *prompt* anything -- the runtime is addressed
    # by key -- so a wake needs both (FEAT-034).
    session_key: str = ""
    on_complete: str = ON_COMPLETE_NOTIFY
    # Wall-clock start, so a watcher can show elapsed time without having been
    # there when the task began.
    started_at: float = field(default_factory=time.time)
    # Chronological session transcript: thoughts, tool calls, and text chunks as
    # they streamed from the agent. Populated live by the runner's event sink.
    events: list[dict] = field(default_factory=list, repr=False)
    _task: asyncio.Task | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "agent": self.agent_slug,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "server_name": self.server_name,
            "task": self.task,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "conversation_id": self.conversation_id,
            # One word, and the dock can say "will continue in chat".
            # ``session_key`` deliberately stays out: it is plumbing, and this
            # dict is polled straight into a chat agent's context.
            "on_complete": self.on_complete,
            "started_at": self.started_at,
            # Which channel this run came through (FEAT-058). Everything in the
            # live registry is a delegation by construction; it is stated
            # anyway so a row from memory and a row from disk are the same
            # shape to the reader that merges them.
            "kind": KIND_DELEGATE,
            # NOTE: `events` is deliberately omitted. This dict is what the MCP
            # `delegate` tool polls, so including the session stream would dump
            # the whole untruncated reasoning + tool output into a *chat agent's*
            # context on every check. The human-facing transcript is served
            # separately by `events_for_wire` (FEAT-022).
        }


def get_delegation(task_id: str) -> DelegateTask | None:
    return _delegations.get(task_id)


def get_all_delegations() -> dict[str, DelegateTask]:
    return dict(_delegations)


async def start_delegation(
    *,
    agent_slug: str,
    user_id: int,
    chat_id: int,
    server_name: str | None,
    task: str,
    bot=None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    conversation_id: str = "",
    session_key: str = "",
    on_complete: str = ON_COMPLETE_NOTIFY,
) -> DelegateTask:
    """Create a DelegateTask, spawn the detached runner, register it, return now.

    Returns immediately -- the caller gets a ``task_id`` to poll/stop while the
    agent works in the background.
    """
    short_id = uuid.uuid4().hex[:8]
    dt = DelegateTask(
        task_id=f"{agent_slug}-delegate-{short_id}",
        agent_slug=agent_slug,
        user_id=user_id,
        chat_id=chat_id,
        server_name=server_name,
        task=task,
        conversation_id=conversation_id,
        session_key=session_key,
        # Unknown values degrade to the passive behaviour rather than raising
        # here: the caller-facing validation is at the edges (the MCP tool and
        # the route), and a delegation must not fail to *start* over a flag.
        on_complete=(
            on_complete if on_complete in ON_COMPLETE_CHOICES else ON_COMPLETE_NOTIFY
        ),
    )
    _delegations[dt.task_id] = dt
    _record_delegation_status(dt)
    dt._task = asyncio.create_task(_run(dt, bot, timeout_s))
    return dt


def _record_dir(dt: "DelegateTask") -> Path:
    """This delegation's directory, under the user who asked for the work.

    ``user_id`` 0 means nobody -- a delegation with no person behind it. That is
    a real directory and not a special case: ``_can_see_delegation`` already
    reads an unowned record as admin-only.
    """
    from condor import paths

    return paths.delegation_dir(dt.user_id or 0, dt.task_id)


def _record_delegation_status(dt: "DelegateTask") -> None:
    """Persist this delegation's *record* next to its transcript.

    Written at start (not only at the end) precisely so a task the process dies
    on leaves something to reconcile — a transcript is only written when the
    task finishes.

    It carries the whole record, not just the state, because this file is what
    :mod:`condor.agents.delegation_history` rebuilds a row from once the
    in-memory registry is gone (FEAT-035). ``write_status`` merges, so the
    fields the start-time write cannot know (result, tool count, end time)
    simply arrive with the final write.

    This function is now only *which fields a delegation has*: the writing, the
    end stamp and the retention sweep moved to
    :func:`condor.agents.run_records.record_run`, which a consult reaches too
    (FEAT-058), so a run becomes files in exactly one place.
    """
    record_run(
        user_id=dt.user_id,
        run_id=dt.task_id,
        agent_slug=dt.agent_slug,
        kind=KIND_DELEGATE,
        state=dt.status,
        task=dt.task,
        started_at=dt.started_at,
        chat_id=dt.chat_id,
        conversation_id=dt.conversation_id,
        session_key=dt.session_key,
        on_complete=dt.on_complete,
        server_name=dt.server_name,
        result=dt.result,
        error=dt.error,
        tool_count=sum(1 for e in dt.events if e.get("type") == "tool"),
    )


def _is_live_delegation(task_id: str) -> bool:
    """True while this task could still be writing into its record directory.

    The on-disk state alone cannot answer this: ``_record_delegation_status``
    stamps the terminal state from ``_run``'s ``finally``, while that very task
    is still on the loop and still has a notification and a wake to do. So the
    registry is consulted the way :meth:`RoutineStore._has_live_task` is --
    unknown here means finished elsewhere (a previous process), which is the
    only reading a restart leaves available anyway.
    """
    dt = _delegations.get(task_id)
    if dt is None:
        return False
    if dt.status not in TERMINAL_STATES:
        return True
    return dt._task is not None and not dt._task.done()


def prune_delegation_records(user_id: int | str, kind: str | None = None) -> int:
    """Evict this owner's oldest terminal records of ``kind`` past its cap.

    The disk counterpart of :func:`retire_delegation`, and modelled on
    ``RoutineStore._prune_instances`` -- the repo's existing answer to this
    exact shape of problem -- so the rules are the same three:

    * **Only terminal records are candidates.** A delegation still running, one
      whose task is still on the loop, and one whose status file says anything
      other than :data:`TERMINAL_STATES` (a state from a process that died, a
      record with no status file at all) is exempt from eviction *and* from the
      count. No amount of churn can reap or squeeze out something still live.
    * **Oldest first**, by the same key the listing sorts on, so what is dropped
      is what the listing would have shown last anyway.
    * **The whole record directory goes** -- status, events and transcript
      together -- so both the disk and the walk shrink, and a reader gets a
      clean ``None`` rather than half a record.

    Since FEAT-058 the directory holds two kinds of run and each has its own
    cap (:func:`max_records_for`), for the reason stated at
    :data:`MAX_CONSULT_RECORDS`: one budget shared between them would let the
    cheap, plentiful kind push out the expensive, rare one. ``kind=None`` sweeps
    every kind, which is what an owner-wide clean-up means.

    Returns how many directories were evicted. Public so retention can be
    exercised (and, on a large install, run) directly rather than only as a
    side effect of finishing a delegation.
    """
    from condor.agents.delegation_history import terminal_record_dirs

    if kind is None:
        return sum(prune_delegation_records(user_id, k) for k in RECORD_KINDS)

    cap = max_records_for(kind)
    if cap <= 0:  # retention off: keep everything
        return 0

    candidates = [
        record
        for record in terminal_record_dirs(user_id, TERMINAL_STATES, kind=kind)
        if not _is_live_delegation(record[1])
    ]
    excess = len(candidates) - cap
    if excess <= 0:
        return 0

    evicted = 0
    for _key, task_id, record_dir in candidates[:excess]:
        try:
            shutil.rmtree(record_dir)
        except OSError:
            log.warning(
                "Could not evict delegation record %s", record_dir, exc_info=True
            )
            continue
        evicted += 1
    if evicted:
        log.debug(
            "Evicted %s delegation record(s) from disk for user %s", evicted, user_id
        )
    return evicted


def _make_event_sink(dt: DelegateTask):
    """Build a callback that folds streamed ACP events into ``dt.events``.

    Consecutive thought/text chunks are merged here; the tool-call create/patch
    reduction (a ``ToolCallUpdate`` patches the matching ``ToolCallEvent`` entry in
    place so each tool call shows its final input/output) is shared with
    :class:`condor.agents.engine.TickEngine` via
    :func:`condor.acp.client.fold_tool_call_event`.
    """
    from condor.acp.client import (
        TextChunk,
        ThoughtChunk,
        ToolCallEvent,
        ToolCallUpdate,
        fold_tool_call_event,
    )

    tl = dt.events
    tc_map: dict[str, dict] = {}

    def append(entry: dict) -> None:
        tl.append(entry)
        _bound_events(tl)

    def merge_chunk(kind: str, text: str) -> None:
        # Roll into a new entry once the current run is long enough: merging for
        # ever would grow one string without bound, which no count of events can
        # cap (CORR-143).
        if tl and tl[-1]["type"] == kind and len(tl[-1]["text"]) < MAX_CHUNK_CHARS:
            tl[-1]["text"] += text
        else:
            append({"type": kind, "text": text})

    def sink(event) -> None:
        if isinstance(event, ThoughtChunk):
            merge_chunk("thought", event.text)
        elif isinstance(event, TextChunk):
            merge_chunk("text", event.text)
        elif isinstance(event, (ToolCallEvent, ToolCallUpdate)):
            tc = fold_tool_call_event(tc_map, event)
            if tc is not None:
                tc["type"] = "tool"
                append(tc)
            # An output arrives on a *patch*, which the fold applies in place and
            # reports as ``None`` -- so the payload bound is applied to the
            # folded entry looked up here, not to the return value.
            folded = tc_map.get(event.tool_call_id)
            if folded is not None:
                _bound_tool_payloads(folded)

    return sink


async def _run(dt: DelegateTask, bot, timeout_s: int) -> None:
    """Background runner: drive the agent to completion, persist, notify."""
    from condor.agents.consult import _run_agent_to_completion

    try:
        dt.result = await asyncio.wait_for(
            _run_agent_to_completion(
                slug=dt.agent_slug,
                user_id=dt.user_id,
                chat_id=dt.chat_id,
                server_name=dt.server_name,
                task=dt.task,
                context="",
                permission_callback=None,  # unattended -> ACP auto-approves
                event_sink=_make_event_sink(dt),
                delegate_worker=True,  # background seat: worker framing, no recursion
            ),
            timeout=timeout_s,
        )
        dt.status = "done"
    except asyncio.CancelledError:
        dt.status = "stopped"
        raise
    except asyncio.TimeoutError:
        dt.status = "error"
        dt.error = f"Timed out after {timeout_s}s"
        log.warning("Delegation %s timed out after %ss", dt.task_id, timeout_s)
    except Exception as e:  # noqa: BLE001 -- surface any runtime failure as task error
        dt.status = "error"
        dt.error = str(e)
        log.exception("Delegation %s failed", dt.task_id)
    finally:
        try:
            _persist_transcript(dt)
        except Exception:
            log.exception("Failed to persist delegation transcript for %s", dt.task_id)
        _record_delegation_status(dt)
        if dt.status != "stopped":
            _record_completion_turn(dt)
            try:
                await _notify_done(dt, bot)
            except Exception:
                log.exception("Failed to notify delegation %s done", dt.task_id)
            # Last, and never before the notification: a wake that fails must
            # not cost the user their message. Only a task that actually
            # produced something is worth continuing from -- a failed or timed
            # out one gives the agent nothing to work with, and the error is
            # already in the chat and in the transcript.
            #
            # Exactly one of the two reaches the session: the resume turn already
            # carries the outcome, so pushing the note as well would say the same
            # thing twice and wake the conversation twice for one task. Every
            # other outcome -- "notify", or a "resume" that failed -- gets the
            # free note, which is what makes a finished task visible in a
            # dashboard that is already open (CORR-262).
            if dt.on_complete == ON_COMPLETE_RESUME and dt.status == "done":
                await _resume_conversation(dt)
            else:
                await _show_completion(dt)
        # Last of all, and only once the transcript, the sidecar and the status
        # record are on disk: this may drop older finished entries, and nothing
        # may be evicted that a reader cannot still find in history.
        retire_delegation(dt)


async def stop_delegation(task_id: str) -> bool:
    """Cancel a running delegation. Returns False if unknown/already finished."""
    dt = _delegations.get(task_id)
    if dt is None or dt._task is None or dt._task.done():
        return False
    dt._task.cancel()
    dt.status = "stopped"
    return True


def _clip_output(value) -> str:
    """Stringify a tool payload, bounded at :data:`MAX_TOOL_OUTPUT`.

    Idempotent: an already-clipped string comes back unchanged, so the sink can
    apply the bound as the stream arrives (CORR-143) and the two readers can
    keep applying it on the way out without stacking a second marker.
    """
    out = str(value)
    if out.endswith(TRUNCATION_MARKER) or len(out) <= MAX_TOOL_OUTPUT:
        return out
    return out[:MAX_TOOL_OUTPUT] + TRUNCATION_MARKER


def _bound_tool_payloads(tc: dict) -> None:
    """Clip a folded tool entry's payloads to what a reader would have shown.

    The output is what gets big (a market-data dump, a file read), and both
    projections already cut it at :data:`MAX_TOOL_OUTPUT` -- keeping the full
    string in RAM bought nothing. The input is normally small and is left in its
    original shape so the dashboard can render it as JSON; only one that
    serializes past the same ceiling degrades to its clipped string form, since
    an ``Edit``/``Write`` call can carry a whole file in its arguments.
    """
    import json

    out = tc.get("output")
    if out is not None:
        tc["output"] = _clip_output(out)
    inp = tc.get("input")
    if inp is not None and not isinstance(inp, str):
        serialized = json.dumps(inp, default=str)
        if len(serialized) > MAX_TOOL_OUTPUT:
            tc["input"] = _clip_output(serialized)


def _bound_events(events: list[dict]) -> None:
    """Keep an event stream under :data:`MAX_EVENTS_PER_DELEGATION`, oldest first.

    The head is what gets dropped: a reader of a runaway session cares about how
    it ended, and the tail is also what the completion notice is drawn from. The
    count of dropped events is folded into a leading marker so the cut is
    visible in the transcript rather than silent.
    """
    if len(events) <= MAX_EVENTS_PER_DELEGATION:
        return
    if not events or events[0].get("type") != DROPPED_EVENT_TYPE:
        events.insert(0, {"type": DROPPED_EVENT_TYPE, "count": 0})
    marker = events[0]
    while len(events) > MAX_EVENTS_PER_DELEGATION:
        events.pop(1)  # index 0 is the marker
        marker["count"] += 1


def _dropped_note(count: int) -> str:
    return f"_… {count} earlier event(s) dropped to bound memory (CORR-143)_"


def retire_delegation(dt: "DelegateTask") -> int:
    """Move a finished delegation to the young end and evict the oldest ones.

    Called once ``_run`` has persisted the transcript, the events sidecar and
    the status record, so everything evicted here is already readable from disk.
    Re-inserting refreshes recency (dicts keep insertion order), which makes the
    registry's tail *finish* order rather than start order -- a slow delegation
    started first must not be the first evicted when it finally lands.

    Returns how many entries were evicted. Public so the same retirement can be
    exercised directly rather than only through a live task.
    """
    if _delegations.get(dt.task_id) is dt:
        # Never re-register a task that was already dropped: the pop/insert pair
        # would resurrect it as the newest entry.
        _delegations.pop(dt.task_id)
        _delegations[dt.task_id] = dt

    finished = [t for t, d in _delegations.items() if d.status in TERMINAL_STATES]
    evicted = 0
    for task_id in finished[: max(len(finished) - MAX_FINISHED_DELEGATIONS, 0)]:
        _delegations.pop(task_id, None)
        evicted += 1
    if evicted:
        log.debug("Evicted %s finished delegation(s) from the registry", evicted)
    return evicted


def events_for_wire(events: list[dict]) -> list[dict]:
    """Serializable copy of an event stream, with tool output bounded.

    A *copy*, not the live list: the sink patches tool entries in place (see
    :func:`condor.acp.client.fold_tool_call_event`), so handing the originals to
    a serializer would race with a running delegation. Truncation reuses
    :func:`_clip_output`, so what a reader sees on the wire is cut at exactly the
    boundary the on-disk transcript uses.
    """
    import json

    wire: list[dict] = []
    for ev in list(events):  # snapshot: the sink may append while we iterate
        kind = ev.get("type")
        if kind in ("thought", "text"):
            wire.append({"type": kind, "text": ev.get("text") or ""})
        elif kind == DROPPED_EVENT_TYPE:
            # Projected as a text note rather than a new wire type: every client
            # already renders text, and a cut nobody renders is a silent lie.
            wire.append({"type": "text", "text": _dropped_note(ev.get("count") or 0)})
        elif kind == "tool":
            inp = ev.get("input")
            out = ev.get("output")
            wire.append(
                {
                    "type": "tool",
                    # Stable across the fold's in-place patches -- the client
                    # keys rows by it so an expanded row survives a status flip.
                    "id": ev.get("id") or "",
                    "name": ev.get("name") or "unknown",
                    "status": ev.get("status") or "",
                    "kind": ev.get("kind") or "",
                    # A tool input can hold anything the agent passed; round-trip
                    # it through JSON so the response can't fail to serialize.
                    "input": json.loads(json.dumps(inp, default=str)) if inp else None,
                    "output": _clip_output(out) if out else None,
                }
            )
    return wire


def _render_session(events: list[dict]) -> str:
    """Render the chronological session transcript (thoughts, tool calls, text)."""
    import json

    parts: list[str] = []
    tool_n = 0
    for ev in events:
        kind = ev.get("type")
        if kind == DROPPED_EVENT_TYPE:
            parts.append(_dropped_note(ev.get("count") or 0))
        elif kind == "thought":
            text = (ev.get("text") or "").strip()
            if text:
                quoted = "\n".join(f"> {line}" for line in text.splitlines())
                parts.append(f"💭 **Reasoning**\n\n{quoted}")
        elif kind == "text":
            text = (ev.get("text") or "").strip()
            if text:
                parts.append(f"💬 {text}")
        elif kind == "tool":
            tool_n += 1
            name = ev.get("name") or "unknown"
            status = ev.get("status") or ""
            block = [f"🔧 **{tool_n}. {name}** ({status})"]
            if ev.get("input"):
                inp = ev["input"]
                inp_str = (
                    json.dumps(inp, indent=2, default=str)
                    if isinstance(inp, dict)
                    else str(inp)
                )
                block.append(f"**Input:**\n```json\n{inp_str}\n```")
            if ev.get("output"):
                block.append(f"**Output:**\n```\n{_clip_output(ev['output'])}\n```")
            parts.append("\n".join(block))
    return "\n\n".join(parts)


def _persist_transcript(dt: DelegateTask) -> None:
    """Write a session transcript into this delegation's own directory.

    Captures the full session: the agent's reasoning, every tool call (with
    input/output), and the final result, so nothing about *how* the task was
    solved is lost.

    An ``events.json`` sidecar goes out alongside it (FEAT-035): the markdown is
    for a human reading the file, the JSON is what lets the dashboard render a
    finished delegation with the same collapsible transcript it shows while the
    task is running. Both are projections of the same ``dt.events`` through the
    same output bound, so they cannot drift apart.
    """
    import json

    from condor.agents.delegation_history import (
        DELEGATION_EVENTS_FILENAME,
        DELEGATION_TRANSCRIPT_FILENAME,
    )

    record_dir = _record_dir(dt)
    record_dir.mkdir(parents=True, exist_ok=True)

    tool_count = sum(1 for e in dt.events if e.get("type") == "tool")
    body = dt.error if dt.status == "error" else dt.result
    session = _render_session(dt.events)

    content = (
        f"# Delegation {dt.task_id}\n\n"
        f"- **Status:** {dt.status}\n"
        f"- **Agent:** {dt.agent_slug}\n"
        f"- **Server:** {dt.server_name or '-'}\n"
        f"- **Tool calls:** {tool_count}\n\n"
        f"## Task\n\n{dt.task}\n\n"
        f"## Session\n\n{session or '(no events captured)'}\n\n"
        f"## {'Error' if dt.status == 'error' else 'Result'}\n\n"
        f"{body or '(none)'}\n"
    )
    (record_dir / DELEGATION_TRANSCRIPT_FILENAME).write_text(content)
    (record_dir / DELEGATION_EVENTS_FILENAME).write_text(
        json.dumps({"events": events_for_wire(dt.events)}, indent=2)
    )


def _completion_text(dt: DelegateTask) -> str:
    """The one-line outcome of a finished delegation.

    Single source for both places the outcome is announced -- the chat push and
    the conversation transcript -- so the two can never tell the user different
    stories about the same task. The result is clipped here, not by the caller:
    a long answer must not bloat a transcript that is replayed into the next
    session's context.
    """
    if dt.status == "error":
        return f"❌ Delegated task {dt.task_id} failed: {dt.error}"

    snippet = (dt.result or "").strip()
    if len(snippet) > 1500:
        snippet = snippet[:1500] + "…"
    return f"✅ Delegated task {dt.task_id} done\n\n{snippet}".rstrip()


def _record_completion_turn(dt: DelegateTask) -> None:
    """Write the outcome back to the conversation that asked for it.

    Without this the chat that started the task ends on "I started a background
    task" and never learns the answer -- and ``replay_context`` tells the next
    session the same false story. Recorded as a ``system`` turn so the replay
    reads it as a parenthetical note rather than as the agent's own words.

    A delegation with no conversation behind it (consult- or tick-started) is a
    no-op: ``record_system`` already ignores an empty id. Imported lazily like
    the rest of this module's runtime touchpoints, and never allowed to raise --
    a failed note must not cost the user their notification.
    """
    if not dt.conversation_id:
        return
    try:
        from condor.runtime.conversations import record_system

        record_system(
            dt.user_id, dt.conversation_id, _completion_text(dt), kind="delegation"
        )
    except Exception:
        log.debug(
            "Could not record delegation %s in conversation %s",
            dt.task_id,
            dt.conversation_id,
            exc_info=True,
        )


RESUME_INSTRUCTION = (
    "You started this task earlier in this conversation. Continue with whatever "
    "you were going to do with the result. If nothing remains, say so in one "
    "line -- do not restate the result."
)


def _resume_text(dt: DelegateTask) -> str:
    """The turn a woken conversation reads.

    Built on :func:`_completion_text` so the wake, the chat push and the
    transcript note cannot tell three different stories about one task. The
    closing instruction is the cost control that matters in practice: without
    it the overwhelmingly common wake turn is the agent paraphrasing an answer
    the user can already read.
    """
    return f"[background task complete] {_completion_text(dt)}\n\n{RESUME_INSTRUCTION}"


async def _resume_conversation(dt: DelegateTask) -> None:
    """Hand the result back to the live session that asked for it (FEAT-034).

    Never allowed to raise: by here the user has already been notified and the
    transcript already carries the outcome, so a wake that cannot happen costs
    nothing. Imported lazily like the rest of this module's runtime touchpoints.
    """
    if not dt.session_key or not dt.conversation_id:
        return
    try:
        from condor.runtime import wake

        woke = await wake.resume_session(
            session_key=dt.session_key,
            conversation_id=dt.conversation_id,
            text=_resume_text(dt),
            kind="resume",
        )
        if not woke:
            log.debug(
                "Delegation %s asked to resume %s but there was nothing live to wake",
                dt.task_id,
                dt.conversation_id,
            )
    except Exception:
        log.exception("Failed to resume conversation for delegation %s", dt.task_id)


async def _show_completion(dt: DelegateTask) -> None:
    """Show the outcome in the live session that asked for it (CORR-262).

    The cheap sibling of :func:`_resume_conversation`, and the same shape
    ``RoutineStore`` already uses after its own ``record_system``. The transcript
    note written by :func:`_record_completion_turn` is read at *load* time, so an
    already-open dashboard learned nothing until someone reloaded the page. This
    pushes the same line immediately and pays for no model turn: a finished
    delegation is worth showing, not worth an agent paraphrasing it.

    Fires only where the resume turn does not -- the caller picks one -- so a
    ``resume`` task is never woken twice for one outcome. A delegation with no
    session or no conversation behind it (consult- or tick-started) is a no-op,
    and nothing here may raise: by now the user has already been notified and
    the transcript already carries the outcome.
    """
    if not dt.session_key or not dt.conversation_id:
        return
    try:
        from condor.runtime import wake

        await wake.deliver_note(
            session_key=dt.session_key,
            conversation_id=dt.conversation_id,
            text=_completion_text(dt),
            kind="delegation",
            # Who to address when the session is gone: reaped on idle, evicted
            # by the per-user cap, or dead. The tab is still open in all three
            # -- the bell fired microseconds earlier proves it (CORR-263).
            user_id=dt.user_id,
        )
    except Exception:
        log.debug(
            "Could not show delegation %s in conversation %s",
            dt.task_id,
            dt.conversation_id,
            exc_info=True,
        )


def resolve_bot(bot=None):
    """The best Telegram sender available in this process.

    Prefer the passed live ``bot``; otherwise fall back to the registered routine
    bot, then the ``_HttpBot`` Telegram-HTTP path (``TELEGRAM_TOKEN``) that
    routines/notification already use, so a process with no live bot still
    delivers. With no token at all the last rung is ``NotifyBot`` (FEAT-048),
    which records the message as a dashboard notification -- an install with no
    Telegram now tells the user things instead of dropping them. Public because a
    Telegram wake sink (FEAT-034) must reach the user through exactly the same
    ladder a completion notice does -- two ladders would eventually disagree
    about who can talk to a chat.
    """
    target = bot
    if target is None:
        try:
            from condor.routine_store import get_routine_store

            target = get_routine_store().get_bot()
        except Exception:
            target = None
    if target is None:
        from condor.routine_store import _HttpBot

        http = _HttpBot()
        if http.has_token():
            return http
        # No Telegram at all: record on the dashboard instead of handing the
        # message to a sender that drops it (FEAT-048). This rung sits *below*
        # both Telegram rungs, so an install with a bot is byte-for-byte
        # unchanged and nothing is ever delivered twice to Telegram.
        from condor.notifications import NotifyBot

        return NotifyBot()
    return target


async def _notify_done(dt: DelegateTask, bot) -> None:
    """Notify the user the delegation finished."""
    if not dt.chat_id:
        return

    from condor.notifications import announce

    # Telegram *and* the dashboard bell (FEAT-048), with the same text the
    # transcript note carries, so the three surfaces cannot tell three stories
    # about one task. ``announce`` owns the "don't file it twice when the
    # resolved sender is the bell itself" rule (ARCH-212).
    #
    # The link is what makes the bell entry worth clicking (CORR-262): a
    # delegation is the one notification whose whole value is a transcript the
    # user wants to open, and an entry with no link is inert in the bell. There
    # is no single-delegation route in the SPA, so the honest target is the
    # agent's own page -- one the router actually resolves.
    await announce(
        dt.user_id,
        dt.chat_id,
        _completion_text(dt),
        kind="delegation",
        bot=bot,
        title=f"Delegation · {dt.agent_slug}",
        link=f"/agents/{dt.agent_slug}",
    )
