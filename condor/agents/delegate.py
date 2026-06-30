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

The registry is in-memory and ephemeral -- a delegation dies with the process, like
a running ``TickEngine`` in ``_engines``. The *result transcript* is persisted to a
flat file under ``agents/{slug}/delegations/{task_id}.md`` so nothing is lost if you
weren't watching, but an unfinished task does not resume after a restart.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Module-level registry of live delegations (mirrors engine._engines).
_delegations: dict[str, "DelegateTask"] = {}

# Default per-task wall-clock budget; a hung ACP subprocess is cancelled after this.
DEFAULT_TIMEOUT_S = 900


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
    )
    _delegations[dt.task_id] = dt
    dt._task = asyncio.create_task(_run(dt, bot, timeout_s))
    return dt


def _make_event_sink(dt: DelegateTask):
    """Build a callback that folds streamed ACP events into ``dt.events``.

    Mirrors :class:`condor.agents.engine.TickEngine`'s event reduction: consecutive
    thought/text chunks are merged, and a ``ToolCallUpdate`` patches the matching
    ``ToolCallEvent`` entry in place so each tool call shows its final input/output.
    """
    from condor.acp.client import TextChunk, ThoughtChunk, ToolCallEvent, ToolCallUpdate

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
        elif isinstance(event, ToolCallEvent):
            tc = tc_map.get(event.tool_call_id)
            if tc is None:
                tc = {
                    "type": "tool",
                    "id": event.tool_call_id,
                    "name": event.title,
                    "status": event.status,
                    "kind": event.kind,
                    "input": event.input,
                    "output": None,
                }
                tc_map[event.tool_call_id] = tc
                tl.append(tc)
            else:
                tc["status"] = event.status
                if event.title:
                    tc["name"] = event.title
                if event.input:
                    tc["input"] = event.input
        elif isinstance(event, ToolCallUpdate):
            tc = tc_map.get(event.tool_call_id)
            if tc is not None:
                if event.status:
                    tc["status"] = event.status
                if event.title:
                    tc["name"] = event.title
                if event.output:
                    tc["output"] = event.output

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
        if dt.status != "stopped":
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
                out_str = str(ev["output"])
                if len(out_str) > 2000:
                    out_str = out_str[:2000] + "\n… (truncated)"
                block.append(f"**Output:**\n```\n{out_str}\n```")
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


async def _notify_done(dt: DelegateTask, bot) -> None:
    """Notify the user the delegation finished.

    Prefer the passed live ``bot``; otherwise fall back to the registered routine
    bot, and finally the ``_HttpBot`` Telegram-HTTP path (``TELEGRAM_TOKEN``) that
    routines/notification already use, so a process with no live bot still delivers.
    """
    if not dt.chat_id:
        return

    if dt.status == "error":
        text = f"❌ Delegated task {dt.task_id} failed: {dt.error}"
    else:
        snippet = (dt.result or "").strip()
        if len(snippet) > 1500:
            snippet = snippet[:1500] + "…"
        text = f"✅ Delegated task {dt.task_id} done\n\n{snippet}".rstrip()

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
