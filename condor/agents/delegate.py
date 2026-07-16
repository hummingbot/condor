"""DELEGATE -- fire-and-forget background agent tasks.

DELEGATE is the async, *unattended* sibling of CONSULT
(:mod:`condor.agents.consult`). Where CONSULT blocks until it can return an
answer (mutations human-gated), DELEGATE hands a one-off, goal-oriented task to
a *detached* Agent instance that works autonomously until the run completes --
the natural "task done" signal -- then notifies the user with the result.

It is NOT a new engine: it drives the same :func:`condor.agents.run.run_agent`
primitive under an unattended policy (refactor-02 §4.1):

- **Trading agents** run under a zero-seeded ``risk_gate``: tool calls
  auto-approve *within caps* (the caps act as a per-run budget), uncapped bot
  deploys and ``place_order`` are blocked. "Trading" means: needs a hummingbot
  server, OR carries an AGENT.md ``risk_limits:`` baseline, OR the caller
  passed caps. The limits come from the per-call ``risk_limits`` override when
  given (it REPLACES the baseline — what you pass is exactly what governs),
  else the baseline. A server-backed delegation with NEITHER errors loudly at
  start.
- **Serverless specialists with no baseline** (e.g. ``routine_builder``) keep
  full auto-approve.

The in-memory registry dies with the process, like a running ``TickEngine`` in
``_engines``. Each delegation is persisted as a ``kind: delegation`` RunStore
stream (§7.1): ``run_started`` lands before the run (a crash leaves an
inspectable stream that the startup sweep closes as interrupted), tool calls
stream in as they land, and ``run_ended`` carries the terminal status. The
markdown transcript is a generated export, never parsed back.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Module-level registry of live delegations (mirrors engine._engines).
_delegations: dict[str, "DelegateTask"] = {}

# Default per-task wall-clock budget; a hung subprocess is cancelled after this.
DEFAULT_TIMEOUT_S = 900


@dataclass
class DelegateTask:
    task_id: str  # the RunStore run_id (opaque ULID)
    agent_slug: str
    user_id: int
    chat_id: int
    server_name: str | None
    task: str
    started_at: str = ""
    ended_at: str = ""
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
            "run_id": self.task_id,
            "agent": self.agent_slug,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "server_name": self.server_name,
            "task": self.task,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


def get_delegation(task_id: str) -> DelegateTask | None:
    return _delegations.get(task_id)


def get_all_delegations() -> dict[str, DelegateTask]:
    return dict(_delegations)


def _resolve_delegation_limits(agent, risk_limits: dict | None) -> dict | None:
    """Resolve the risk caps governing an unattended run of ``agent``.

    An agent counts as TRADING when it needs a hummingbot server, carries an
    AGENT.md ``risk_limits:`` baseline, or the caller passed caps — NOT just
    ``server_required``: serverless agents can trade through condor-native
    executors (gateway venues), so a baseline or override must gate them too.
    Returns the caps dict for trading agents, or None only for true
    serverless specialists with no baseline and no override (full
    auto-approve, e.g. routine_builder). Raises when a server-backed
    delegation has neither a baseline nor an override — unbounded-capital
    delegations must say their numbers out loud.
    """
    limits = risk_limits or agent.risk_limits
    if limits:
        return dict(limits)
    # Full auto-approve (None) is only for true serverless specialists that
    # cannot trade: neither server-backed NOR owning manage_executors. A
    # serverless agent that owns the executor tool must still be bounded (#4).
    if not agent.server_required and not agent.can_trade:
        return None
    raise ValueError(
        f"Agent '{agent.slug}' can touch live trading but has no risk baseline: "
        "set `risk_limits:` in its AGENT.md frontmatter, or pass an explicit "
        "risk_limits dict on this delegation (e.g. "
        '{"max_position_size_quote": 500, "max_open_executors": 5}).'
    )


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

    Returns immediately -- the caller gets a ``task_id`` (the run id) to
    poll/stop while the agent works in the background.

    Raises:
        ValueError: unknown agent, or a trading delegation with neither an
            AGENT.md ``risk_limits`` baseline nor a per-call override.
    """
    from condor.agents.agent import AgentStore
    from condor.agents.runstore import get_run_store

    agent = AgentStore().get(agent_slug)
    if agent is None:
        raise ValueError(f"No agent named '{agent_slug}' exists.")

    # Validate the policy up front so the caller gets the loud error, not a
    # background failure notification.
    effective_limits = _resolve_delegation_limits(agent, risk_limits)

    started_at = datetime.now(timezone.utc).isoformat()
    # run_started persists BEFORE the run: a crash mid-delegation leaves a
    # stream the startup sweep closes as interrupted — the inspectable husk.
    run_id = get_run_store().start_run(
        agent_slug,
        "delegation",
        payload={
            "task": task,
            "model": agent.agent_key,
            "risk_limits": effective_limits or {},
            "server": server_name or "",
        },
    )
    dt = DelegateTask(
        task_id=run_id,
        agent_slug=agent_slug,
        user_id=user_id,
        chat_id=chat_id,
        server_name=server_name,
        task=task,
        started_at=started_at,
        risk_limits=effective_limits,
    )
    _delegations[dt.task_id] = dt
    dt._task = asyncio.create_task(_run(dt, agent, bot, timeout_s))
    return dt


def _make_event_sink(dt: DelegateTask):
    """Build a callback that folds streamed events into ``dt.events`` live.

    ``run_agent`` folds the same stream into ``RunResult.events`` for the
    persisted record; this sink keeps the in-memory task record streaming
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
    from condor.agents.gating import is_dangerous_tool_call
    from condor.agents.policies import AUTO, risk_gate
    from condor.agents.run import run_agent
    from condor.agents.context import build_agent_context
    from condor.agents.runstore import get_run_store

    run_store = get_run_store()

    if dt.risk_limits is not None:
        policy = risk_gate(dt.risk_limits)  # zero-seeded: caps = per-run budget
    else:
        policy = AUTO  # serverless specialist — full auto-approve

    effective_server = (
        (agent.server_name or dt.server_name) if agent.server_required else None
    )
    prompt = build_agent_context(agent, dt.user_id, dt.task)

    def _persist_tool_call(tc: dict) -> None:
        try:
            run_store.emit(
                dt.task_id,
                "tool_call",
                {
                    "name": tc.get("name") or tc.get("title", ""),
                    "status": tc.get("status", ""),
                    "input": tc.get("input"),
                    "output": tc.get("output"),
                    "mutating": is_dangerous_tool_call(tc),
                },
                tool_call_id=str(tc.get("id") or "") or None,
            )
        except Exception:
            log.exception("delegation %s: tool_call emit failed", dt.task_id)

    end_status, end_reason = "completed", ""
    try:
        result = await run_agent(
            agent,
            prompt,
            permission_policy=policy,
            user_id=dt.user_id,
            chat_id=dt.chat_id,
            server_name=effective_server,
            risk_limits=dt.risk_limits,
            timeout_s=timeout_s,
            event_sink=_make_event_sink(dt),
            fallback_on_unhealthy=True,
            # Executor attribution: the run id is the one execution key.
            agent_id=dt.task_id,
            on_tool_call=_persist_tool_call,
        )
        if result.timed_out:
            dt.status = "error"
            dt.error = result.error
            end_status, end_reason = "error", result.error
            log.warning("Delegation %s timed out after %ss", dt.task_id, timeout_s)
        else:
            dt.result = result.fallback_note + (
                result.text or "(the agent returned no answer)"
            )
            dt.status = "done"
    except asyncio.CancelledError:
        dt.status = "stopped"
        end_status, end_reason = "stopped", "cancelled by operator"
        raise
    except Exception as e:  # noqa: BLE001 -- surface any runtime failure as task error
        dt.status = "error"
        dt.error = str(e)
        end_status, end_reason = "error", str(e)
        log.exception("Delegation %s failed", dt.task_id)
    finally:
        dt.ended_at = datetime.now(timezone.utc).isoformat()
        try:
            if dt.result:
                run_store.emit(dt.task_id, "state_snapshot", {"result": dt.result})
            run_store.end_run(dt.task_id, end_status, end_reason)
        except Exception:
            log.exception("Failed to close delegation run %s", dt.task_id)
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


async def _notify_done(dt: DelegateTask, bot) -> None:
    """Notify the user the delegation finished (outbox chokepoint)."""
    if dt.status == "error":
        text = f"❌ Delegated task {dt.task_id} failed: {dt.error}"
    else:
        snippet = (dt.result or "").strip()
        if len(snippet) > 1500:
            snippet = snippet[:1500] + "…"
        text = f"✅ Delegated task {dt.task_id} done\n\n{snippet}".rstrip()

    from condor.notifications import notify

    await notify(
        text,
        user_id=dt.user_id,
        chat_id=dt.chat_id,
        agent_id=dt.task_id,
        kind="delegation",
        bot=bot,
    )
