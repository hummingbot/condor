"""DELEGATE -- fire-and-forget background agent tasks.

DELEGATE is the async, *unattended* sibling of CONSULT
(:mod:`condor.agents.consult`). Where CONSULT blocks until it can return an
answer (mutations human-gated), DELEGATE hands a one-off, goal-oriented task to
a *detached* Agent instance that works autonomously until the run completes --
the natural "task done" signal -- then notifies the user with the result.

It is NOT a new engine: it drives the same :func:`condor.agents.run.run_agent`
primitive under an unattended policy (refactor-02 §4.1):

- **Trading agents** (``server_required: true``) run under a zero-seeded
  ``risk_gate``: tool calls auto-approve *within caps* (the caps act as a
  per-run budget), uncapped bot deploys and ``place_order`` are blocked. The
  limits come from the per-call ``risk_limits`` override when given (it
  REPLACES the baseline — what you pass is exactly what governs), else the
  agent's AGENT.md ``risk_limits:`` baseline. A trading delegation with
  NEITHER errors loudly at start.
- **Serverless specialists** (e.g. ``routine_builder``) keep full auto-approve.

The in-memory registry dies with the process, like a running ``TickEngine`` in
``_engines``. Each delegation is persisted as a ``kind: delegation`` session
under ``agents/{slug}/sessions/session_N/`` (meta.yml written at start — a
crash leaves an inspectable husk; transcript.md + final status in ``finally``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Module-level registry of live delegations (mirrors engine._engines).
_delegations: dict[str, "DelegateTask"] = {}

# Default per-task wall-clock budget; a hung subprocess is cancelled after this.
DEFAULT_TIMEOUT_S = 900


@dataclass
class DelegateTask:
    task_id: str  # == the session id "{agent_slug}_{N}"
    agent_slug: str
    user_id: int
    chat_id: int
    server_name: str | None
    task: str
    session_dir: Path | None = None
    risk_limits: dict | None = None  # per-call override; None → agent baseline
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


def _resolve_delegation_limits(agent, risk_limits: dict | None) -> dict | None:
    """Resolve the risk caps governing an unattended run of ``agent``.

    Returns the caps dict for trading agents, or None for serverless agents
    (full auto-approve). Raises when a trading delegation has neither a
    baseline nor an override — unbounded-capital delegations must say their
    numbers out loud.
    """
    if not agent.server_required:
        return None
    limits = risk_limits or agent.risk_limits
    if not limits:
        raise ValueError(
            f"Agent '{agent.slug}' can touch live trading but has no risk baseline: "
            "set `risk_limits:` in its AGENT.md frontmatter, or pass an explicit "
            "risk_limits dict on this delegation (e.g. "
            '{"max_position_size_quote": 500, "max_open_executors": 5}).'
        )
    return dict(limits)


async def start_delegation(
    *,
    agent_slug: str,
    user_id: int,
    chat_id: int,
    server_name: str | None,
    task: str,
    bot=None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    risk_limits: dict | None = None,
) -> DelegateTask:
    """Create a DelegateTask, spawn the detached runner, register it, return now.

    Returns immediately -- the caller gets a ``task_id`` to poll/stop while the
    agent works in the background.

    Raises:
        ValueError: unknown agent, or a trading delegation with neither an
            AGENT.md ``risk_limits`` baseline nor a per-call override.
    """
    from condor.agents.agent import AgentStore
    from condor.agents.journal import allocate_session_dir, write_session_meta

    agent = AgentStore().get(agent_slug)
    if agent is None:
        raise ValueError(f"No agent named '{agent_slug}' exists.")

    # Validate the policy up front so the caller gets the loud error, not a
    # background failure notification.
    effective_limits = _resolve_delegation_limits(agent, risk_limits)

    num, session_dir = allocate_session_dir(agent.agent_dir)
    task_id = f"{agent_slug}_{num}"
    write_session_meta(
        session_dir,
        {
            "kind": "delegation",
            "status": "running",
            "task": task[:500],
            "started_at": datetime.now(timezone.utc).isoformat(),
            **({"risk_limits": effective_limits} if effective_limits else {}),
        },
    )

    dt = DelegateTask(
        task_id=task_id,
        agent_slug=agent_slug,
        user_id=user_id,
        chat_id=chat_id,
        server_name=server_name,
        task=task,
        session_dir=session_dir,
        risk_limits=effective_limits,
    )
    _delegations[dt.task_id] = dt
    dt._task = asyncio.create_task(_run(dt, agent, bot, timeout_s))
    return dt


def _make_event_sink(dt: DelegateTask):
    """Build a callback that folds streamed events into ``dt.events`` live.

    ``run_agent`` folds the same stream into ``RunResult.events`` for the
    persisted transcript; this sink keeps the in-memory task record streaming
    for pollers (``delegate(action="get")``) while the run is in flight.
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


async def _run(dt: DelegateTask, agent, bot, timeout_s: int) -> None:
    """Background runner: drive the agent to completion, persist, notify."""
    from condor.agents.policies import AUTO, risk_gate
    from condor.agents.run import run_agent
    from handlers.agents._shared import build_agent_context

    if dt.risk_limits is not None:
        policy = risk_gate(dt.risk_limits)  # zero-seeded: caps = per-run budget
    else:
        policy = AUTO  # serverless specialist — full auto-approve

    effective_server = (
        (agent.server_name or dt.server_name) if agent.server_required else None
    )
    prompt = build_agent_context(agent, dt.user_id, dt.task)

    try:
        result = await run_agent(
            agent,
            prompt,
            permission_policy=policy,
            user_id=dt.user_id,
            chat_id=dt.chat_id,
            server_name=effective_server,
            timeout_s=timeout_s,
            event_sink=_make_event_sink(dt),
            fallback_on_unhealthy=True,
        )
        if result.timed_out:
            dt.status = "error"
            dt.error = result.error
            log.warning("Delegation %s timed out after %ss", dt.task_id, timeout_s)
        else:
            dt.result = result.fallback_note + (
                result.text or "(the agent returned no answer)"
            )
            dt.status = "done"
    except asyncio.CancelledError:
        dt.status = "stopped"
        raise
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


def _persist_transcript(dt: DelegateTask) -> None:
    """Finalize the delegation's session: transcript.md + terminal meta status.

    Captures the full session — the agent's reasoning, every tool call (with
    input/output), and the final result — so nothing about *how* the task was
    solved is lost.
    """
    from condor.agents.journal import finalize_session_meta, render_transcript

    if dt.session_dir is None:
        return

    body = dt.error if dt.status == "error" else dt.result
    tool_count = sum(1 for e in dt.events if e.get("type") == "tool")
    session = render_transcript(dt.events)

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
    (dt.session_dir / "transcript.md").write_text(content)
    finalize_session_meta(
        dt.session_dir,
        dt.status,
        **({"error": dt.error} if dt.error else {}),
    )


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
