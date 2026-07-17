"""Run an Agent consult to completion in the main process.

``condor`` (the coordinator) calls the ``consult`` MCP tool, which calls back
into the main process (where the agent runtime lives) and lands here. We load
the Agent, build its prompt, and drive :func:`condor.agents.run.run_agent`
under the ``human_gate`` policy — every mutating tool call needs a human
approval.

The answer returns inline; the run itself is recorded like every other run —
a ``kind: consult`` RunStore stream (§7.1) carrying the task, tool calls, and
the result, so consult-created executors attribute to a real run id.
"""

from __future__ import annotations

import logging

from condor.agents.agent import AgentStore

log = logging.getLogger(__name__)

# Wall-clock budget for one consult; without it a hung backend blocks the
# caller forever. Matches the delegation default.
CONSULT_TIMEOUT_S = 900


async def run_consult(
    slug: str,
    task: str,
    context: str = "",
) -> str:
    """Consult the Agent ``slug`` with ``task`` and return its answer.

    CONSULT is synchronous and human-gated: mutating tools need a human
    approval. Its async, unattended sibling is DELEGATE
    (:mod:`condor.agents.delegate`), which runs the same primitive under a
    ``risk_gate``/AUTO policy.
    """
    from condor.agents.context import build_agent_context
    from condor.agents.policies import human_gate
    from condor.agents.run import run_agent
    from condor.agents.runstore import get_run_store

    store = AgentStore()
    agent = store.get(slug)
    if agent is None:
        index = store.list_consultable_index()
        available = f"\n\nAvailable agents:\n{index}" if index else ""
        return f"No agent named '{slug}' is available.{available}"

    prompt = build_agent_context(agent, task, context)

    account_ref = None
    if agent.can_trade:
        from condor.agents.spec import resolve_agent_account

        account_ref = resolve_agent_account(agent)

    run_store = get_run_store()
    run_id = run_store.start_run(
        slug,
        "consult",
        payload={
            "task": task,
            "model": agent.agent_key,
            **({"account_ref": account_ref.as_dict()} if account_ref else {}),
        },
    )

    def _persist_tool_call(tc: dict) -> None:
        from condor.agents.gating import is_dangerous_tool_call

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
            log.exception("consult %s: tool_call emit failed", run_id)

    try:
        result = await run_agent(
            agent,
            prompt,
            # Consults are runs (§7.1): the run_id routes approvals through
            # the durable queue, so no interactive chat transport is needed.
            permission_policy=human_gate(0, run_id=run_id, agent_slug=slug),
            timeout_s=CONSULT_TIMEOUT_S,
            agent_id=run_id,
            account_ref=account_ref,
            on_tool_call=_persist_tool_call,
        )
    except BaseException:
        run_store.end_run(run_id, "error", "consult crashed/cancelled")
        raise

    answer = result.text or "(the agent returned no answer)"
    try:
        run_store.emit(run_id, "state_snapshot", {"result": answer})
    finally:
        run_store.end_run(
            run_id,
            "error" if result.timed_out else "completed",
            result.error if result.timed_out else "",
        )
    return answer
