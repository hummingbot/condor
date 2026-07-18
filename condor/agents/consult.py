"""Run an Agent consult to completion in the main process.

``condor`` (the coordinator) calls the ``consult`` MCP tool, which calls back
into the main process (where the agent runtime lives) and lands here. We load
the Agent, build its prompt, and drive :func:`condor.agents.run.run_agent`
under the ``human_gate`` policy — every mutating tool call needs a human
approval.

A consult is a QUESTION, not a run: the answer returns inline and NOTHING is
persisted — no run id, no event stream, no folder. The ephemeral ULID minted
here only keys the in-memory approval queue (``durable=False``) and the run
capability that attributes any HUMAN-APPROVED executor to this consult; the
executor store remains the financial authority for anything actually created.
"""

from __future__ import annotations

import logging

from condor.agents.agent import AgentStore

log = logging.getLogger(__name__)

# Wall-clock budget for one consult; without it a hung backend blocks the
# caller forever.
CONSULT_TIMEOUT_S = 900


async def run_consult(
    slug: str,
    task: str,
    context: str = "",
) -> str:
    """Consult the Agent ``slug`` with ``task`` and return its answer.

    CONSULT is synchronous and human-gated: mutating tools need a human
    approval (resolved via ``resolve_approval``/dashboard, default deny on
    timeout).
    """
    from condor.agents.context import build_agent_context
    from condor.agents.policies import human_gate
    from condor.agents.run import run_agent
    from condor.agents.runstore import new_ulid

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

    # Ephemeral identity: keys the in-memory approval queue and the executor
    # attribution capability. Never lands on disk.
    consult_id = new_ulid()

    try:
        result = await run_agent(
            agent,
            prompt,
            # The id routes approvals through the (in-memory) queue, so no
            # interactive chat transport is needed.
            permission_policy=human_gate(
                0, run_id=consult_id, agent_slug=slug, durable=False
            ),
            timeout_s=CONSULT_TIMEOUT_S,
            agent_id=consult_id,
            account_ref=account_ref,
        )
    finally:
        # Pending approvals die with the consult — a grant could never be
        # consumed by a caller that no longer waits.
        from condor.agents.approvals import get_approval_manager

        get_approval_manager().void_run(consult_id)

    if result.timed_out:
        return f"(consult timed out after {CONSULT_TIMEOUT_S}s) {result.text}".strip()
    return result.text or "(the agent returned no answer)"
