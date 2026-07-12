"""Run an Agent consult to completion in the main process.

``condor`` (the coordinator) calls the ``consult`` MCP tool, which calls back into
the main process (where ``ConfigManager`` and the agent runtime live) and lands
here. We load the Agent, build its prompt, and drive :func:`condor.agents.run.
run_agent` under the ``human_gate`` policy — every mutating tool call is
confirmed via the user's Telegram chat (the confirmation registry is
process-global, so the Approve/Reject tap resolves even while condor's own chat
session is busy awaiting the consult result). No strategy is involved — CONSULT
runs the Agent's identity + shared memory/skills.

A consult persists nothing (refactor-07): the answer returns inline and that
is the whole artifact. Anything long or durable enough to deserve a record is
delegation-shaped — route it through :mod:`condor.agents.delegate`, which
leaves a flat transcript.
"""

from __future__ import annotations

import logging

from condor.agents.agent import AgentStore

log = logging.getLogger(__name__)

# Wall-clock budget for one consult; without it a hung backend blocks the
# user's Telegram chat forever. Matches the delegation default.
CONSULT_TIMEOUT_S = 900


async def run_consult(
    slug: str,
    user_id: int,
    chat_id: int,
    server_name: str | None,
    task: str,
    context: str = "",
) -> str:
    """Consult the Agent ``slug`` with ``task`` and return its answer.

    CONSULT is synchronous and human-gated: mutating tools are confirmed via the
    user's Telegram chat. Its async, unattended sibling is DELEGATE
    (:mod:`condor.agents.delegate`), which runs the same primitive under a
    ``risk_gate``/AUTO policy.
    """
    from condor.agents.policies import human_gate
    from condor.agents.run import run_agent
    from handlers.agents._shared import build_agent_context

    store = AgentStore()
    agent = store.get(slug)
    if agent is None:
        index = store.list_consultable_index()
        available = f"\n\nAvailable agents:\n{index}" if index else ""
        return f"No agent named '{slug}' is available.{available}"

    # A server pinned on the Agent itself wins over the ambient chat server; a
    # serverless agent gets session wiring (its own memory/skill scope, no pin).
    effective_server = (agent.server_name or server_name) if agent.server_required else None

    prompt = build_agent_context(agent, user_id, task, context)

    try:
        result = await run_agent(
            agent,
            prompt,
            permission_policy=human_gate(chat_id),
            user_id=user_id,
            chat_id=chat_id,
            server_name=effective_server,
            timeout_s=CONSULT_TIMEOUT_S,
            fallback_on_unhealthy=True,
        )
    except RuntimeError as e:
        # Model backend down and no fallback configured — fail with the fix.
        return (
            f"{e}\n\n"
            "Start the model backend, or set CONSULT_FALLBACK_MODEL to a "
            "reachable model to auto-fall-back."
        )

    return result.fallback_note + (result.text or "(the agent returned no answer)")
