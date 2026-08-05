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
*result transcript* is persisted to a flat file under
``agents/{slug}/delegations/{task_id}.md`` so nothing is lost if you weren't
watching. Since FEAT-012 a small ``{task_id}.status.json`` is written alongside it
when the task starts, so a delegation killed by a restart is reported as
``interrupted`` instead of vanishing without a trace. It is never auto-restarted:
delegations are one-shot and re-running could duplicate side effects.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Module-level registry of live delegations (mirrors engine._engines).
_delegations: dict[str, "DelegateTask"] = {}

# Default per-task wall-clock budget; a hung ACP subprocess is cancelled after this.
DEFAULT_TIMEOUT_S = 900

# Ceiling on a single tool output wherever a transcript is *read* -- the on-disk
# markdown and the wire projection share it so the two can never disagree about
# where a huge output was cut. ``dt.events`` itself keeps the full output.
MAX_TOOL_OUTPUT = 2000


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
            "started_at": self.started_at,
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
    )
    _delegations[dt.task_id] = dt
    _record_delegation_status(dt)
    dt._task = asyncio.create_task(_run(dt, bot, timeout_s))
    return dt


def _delegation_status_name(task_id: str) -> str:
    return f"{task_id}.status.json"


def _record_delegation_status(dt: "DelegateTask") -> None:
    """Persist this delegation's state next to its transcript.

    Written at start (not only at the end) precisely so a task the process dies
    on leaves something to reconcile — a transcript is only written when the
    task finishes.
    """
    try:
        from condor.agents.agent import AgentStore
        from condor.runtime.registry_file import write_status

        agent = AgentStore().get(dt.agent_slug)
        if agent is None:
            return
        delegations_dir = agent.agent_dir / "delegations"
        delegations_dir.mkdir(parents=True, exist_ok=True)
        write_status(
            delegations_dir,
            _delegation_status_name(dt.task_id),
            state=dt.status,
            task_id=dt.task_id,
            agent_slug=dt.agent_slug,
            chat_id=dt.chat_id,
            user_id=dt.user_id,
            conversation_id=dt.conversation_id,
            started_at=dt.started_at,
        )
    except Exception:
        log.debug(
            "Could not record delegation status for %s", dt.task_id, exc_info=True
        )


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

    def sink(event) -> None:
        if isinstance(event, ThoughtChunk):
            if tl and tl[-1]["type"] == "thought":
                tl[-1]["text"] += event.text
            else:
                tl.append({"type": "thought", "text": event.text})
        elif isinstance(event, TextChunk):
            if tl and tl[-1]["type"] == "text":
                tl[-1]["text"] += event.text
            else:
                tl.append({"type": "text", "text": event.text})
        elif isinstance(event, (ToolCallEvent, ToolCallUpdate)):
            tc = fold_tool_call_event(tc_map, event)
            if tc is not None:
                tc["type"] = "tool"
                tl.append(tc)

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


async def stop_delegation(task_id: str) -> bool:
    """Cancel a running delegation. Returns False if unknown/already finished."""
    dt = _delegations.get(task_id)
    if dt is None or dt._task is None or dt._task.done():
        return False
    dt._task.cancel()
    dt.status = "stopped"
    return True


def _clip_output(value) -> str:
    """Stringify a tool output, bounded at :data:`MAX_TOOL_OUTPUT`."""
    out = str(value)
    if len(out) > MAX_TOOL_OUTPUT:
        return out[:MAX_TOOL_OUTPUT] + "\n… (truncated)"
    return out


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
        if kind == "thought":
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
    """Write a session transcript under agents/{slug}/delegations/{task_id}.md.

    Mirrors the ``dry_runs/experiment_N.md`` flat-file convention, not the
    heavyweight ``sessions/`` tree -- a delegate has no ticks to journal. Captures
    the full session: the agent's reasoning, every tool call (with input/output),
    and the final result, so nothing about *how* the task was solved is lost.
    """
    from condor.agents.agent import AgentStore

    agent = AgentStore().get(dt.agent_slug)
    if agent is None:
        return
    delegations_dir = agent.agent_dir / "delegations"
    delegations_dir.mkdir(parents=True, exist_ok=True)

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
    (delegations_dir / f"{dt.task_id}.md").write_text(content)


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


async def _notify_done(dt: DelegateTask, bot) -> None:
    """Notify the user the delegation finished.

    Prefer the passed live ``bot``; otherwise fall back to the registered routine
    bot, and finally the ``_HttpBot`` Telegram-HTTP path (``TELEGRAM_TOKEN``) that
    routines/notification already use, so a process with no live bot still delivers.
    """
    if not dt.chat_id:
        return

    text = _completion_text(dt)

    target = bot
    if target is None:
        try:
            from condor.routine_store import get_routine_store

            target = get_routine_store().get_bot()
        except Exception:
            target = None
    if target is None:
        from condor.routine_store import _HttpBot

        target = _HttpBot()

    await target.send_message(chat_id=dt.chat_id, text=text)
