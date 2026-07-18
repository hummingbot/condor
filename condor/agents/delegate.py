"""DELEGATE -- fire-and-forget background agent tasks.

DELEGATE is the async, *unattended* sibling of CONSULT
(:mod:`condor.agents.consult`). Where CONSULT blocks until it can return an
answer (mutations human-gated), DELEGATE hands a one-off, goal-oriented task to
a *detached* Agent instance that works autonomously until the run completes --
the natural "task done" signal -- then notifies the user with the result.

It is NOT a new engine: it drives the same :func:`condor.agents.run.run_agent`
primitive under an unattended policy (refactor-02 §4.1):

- **Trading agents** run under a zero-seeded ``risk_gate``: tool calls
  auto-approve *within caps* (the caps act as a per-run budget). "Trading"
  means: carries an AGENT.md ``risk_limits:`` baseline, OR the caller passed
  caps, OR ``agent.can_trade`` (declares ``manage_executors``, or declares no
  tool scope at all). The limits come from the per-call ``risk_limits``
  override when given (it REPLACES the baseline — what you pass is exactly
  what governs), else the baseline. A can-trade delegation with NEITHER
  errors loudly at start.
- **Specialists that cannot trade and carry no baseline** keep full
  auto-approve.

**A delegation IS a run (§7.1).** Its durable record is the
``kind: delegation`` RunStore stream — ``run_started`` before the run (a crash
leaves an inspectable husk the startup sweep closes as interrupted), tool calls
as they land, ``run_ended`` at the terminal status, and the result as a
``state_snapshot``. So status/history come from ``get_run``/``list_runs`` and
stopping from ``control_run`` — exactly like any run; there is no separate
delegation status/stop surface. The ONLY process-local state is the asyncio
task handle, kept SOLELY so a live delegation can be cancelled (mirrors a live
``TickEngine`` in ``_engines``); it dies with the process, and the RunStore
stream is the source of truth.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

# run_id -> the background asyncio task, kept ONLY so `control_run(run_id,
# "stop")` (lifecycle.apply_verb → cancel_delegation) can cancel a live
# delegation, like _engines holds live TickEngines. NOT a status store —
# status/result/transcript all live in the RunStore stream.
_delegations: dict[str, asyncio.Task] = {}

# Default per-task wall-clock budget; a hung subprocess is cancelled after this.
DEFAULT_TIMEOUT_S = 900


def _resolve_delegation_limits(agent, risk_limits: dict | None) -> dict | None:
    """Resolve the risk caps governing an unattended run of ``agent``.

    An agent counts as TRADING when it carries an AGENT.md ``risk_limits:``
    baseline, the caller passed caps, or ``agent.can_trade`` (declares
    ``manage_executors`` — or no tool scope at all, which is unrestricted and
    therefore trading). Returns the caps dict for trading agents, or None only
    for specialists that cannot trade and carry no baseline/override (full
    auto-approve). Raises when a can-trade delegation
    has neither a baseline nor an override — unbounded-capital delegations
    must say their numbers out loud.
    """
    limits = risk_limits or agent.risk_limits
    if limits:
        return dict(limits)
    # Full auto-approve (None) is only for specialists that cannot trade. An
    # agent that can reach the executor tool must be bounded (#4).
    if not agent.can_trade:
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
    task: str,
    timeout_s: int | None = DEFAULT_TIMEOUT_S,
    risk_limits: dict | None = None,
) -> str:
    """Spawn the detached runner and return its ``run_id`` immediately.

    Track it afterward like any run: ``get_run(run_id)`` for status/result,
    ``control_run(run_id, "stop")`` to cancel. The user is also notified
    automatically when it finishes.

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
    account_ref = None
    if agent.can_trade:
        from condor.agents.spec import resolve_agent_account

        # Q2.1: venue comes from the agent's identity, not the loop config —
        # a task never reaches into default_config.
        account_ref = resolve_agent_account(agent)

    # run_started persists BEFORE the run: a crash mid-delegation leaves a
    # stream the startup sweep closes as interrupted — the inspectable husk.
    run_id = get_run_store().start_run(
        agent_slug,
        "delegation",
        payload={
            "task": task,
            "model": agent.agent_key,
            "risk_limits": effective_limits or {},
            **({"account_ref": account_ref.as_dict()} if account_ref else {}),
        },
    )
    _delegations[run_id] = asyncio.create_task(
        _run(
            run_id,
            agent,
            task,
            effective_limits,
            account_ref,
            timeout_s or DEFAULT_TIMEOUT_S,
        )
    )
    return run_id


def cancel_delegation(run_id: str) -> bool:
    """Cancel a live delegation's background task. False if unknown/finished.

    The wire-in for ``control_run(run_id, "stop")`` (lifecycle.apply_verb):
    a delegation isn't a TickEngine, so stopping it means cancelling its task
    here (its executors are stopped by the same apply_verb via run attribution).
    """
    task = _delegations.get(run_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


async def _run(run_id, agent, task, risk_limits, account_ref, timeout_s: int) -> None:
    """Background runner: drive the agent to completion, persist, notify.

    Persistence matches CONSULT: tool calls stream to the RunStore via
    ``on_tool_call``, the result lands as a ``state_snapshot``, and
    ``run_ended`` carries the terminal status — no in-memory transcript.
    """
    from condor.agents.context import build_agent_context
    from condor.agents.gating import is_dangerous_tool_call
    from condor.agents.policies import AUTO, risk_gate
    from condor.agents.run import run_agent
    from condor.agents.runstore import get_run_store

    run_store = get_run_store()
    # zero-seeded: caps = per-run budget; AUTO for non-trading specialists.
    policy = risk_gate(risk_limits) if risk_limits is not None else AUTO
    prompt = build_agent_context(agent, task)

    def _persist_tool_call(tc: dict) -> None:
        try:
            run_store.emit(
                run_id,
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
            log.exception("delegation %s: tool_call emit failed", run_id)

    end_status, end_reason = "completed", ""
    result_text, error_text = "", ""
    stopped = False
    try:
        result = await run_agent(
            agent,
            prompt,
            permission_policy=policy,
            risk_limits=risk_limits,
            account_ref=account_ref,
            timeout_s=timeout_s,
            # Executor attribution: the run id is the one execution key.
            agent_id=run_id,
            on_tool_call=_persist_tool_call,
        )
        if result.timed_out:
            error_text = result.error
            end_status, end_reason = "error", result.error
            log.warning("Delegation %s timed out after %ss", run_id, timeout_s)
        else:
            result_text = result.text or "(the agent returned no answer)"
    except asyncio.CancelledError:
        end_status, end_reason = "stopped", "cancelled by operator"
        stopped = True
        raise
    except Exception as e:  # noqa: BLE001 -- surface any runtime failure as task error
        error_text = str(e)
        end_status, end_reason = "error", str(e)
        log.exception("Delegation %s failed", run_id)
    finally:
        try:
            if result_text:
                run_store.emit(run_id, "state_snapshot", {"result": result_text})
            run_store.end_run(run_id, end_status, end_reason)
        except Exception:
            log.exception("Failed to close delegation run %s", run_id)
        _delegations.pop(run_id, None)
        if not stopped:
            try:
                await _notify_done(run_id, end_status, result_text, error_text)
            except Exception:
                log.exception("Failed to notify delegation %s done", run_id)


async def _notify_done(
    run_id: str, status: str, result_text: str, error_text: str
) -> None:
    """Notify the user the delegation finished (outbox chokepoint)."""
    if status == "error":
        text = f"❌ Delegated task {run_id} failed: {error_text}"
    else:
        snippet = (result_text or "").strip()
        if len(snippet) > 1500:
            snippet = snippet[:1500] + "…"
        text = f"✅ Delegated task {run_id} done\n\n{snippet}".rstrip()

    from condor.notifications import notify

    await notify(text, agent_id=run_id, kind="delegation")
