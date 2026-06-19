"""Run an Agent consult to completion in the main process.

``condor`` (the coordinator) calls the ``consult`` MCP tool, which calls back into
the main process (where ``ConfigManager`` and the agent runtime live) and lands
here. We load the Agent, build its restricted toolset, run its own brain to
completion on a pydantic-ai model, and return its answer text. No strategy is
involved — CONSULT runs the Agent's identity + shared memory/skills.

The Agent may call mutating tools; those are gated by the SAME interactive
confirmation flow condor uses (:func:`handlers.agents.confirmation.permission_callback`),
routed to the user's Telegram chat. The confirmation registry is process-global, so
the user's Approve/Reject tap resolves the pending future even while condor's own
session is busy awaiting the consult result.
"""

from __future__ import annotations

import functools
import logging

from condor.acp.pydantic_ai_client import PydanticAIClient, is_pydantic_ai_model
from condor.trading_agent.agent import AgentStore

log = logging.getLogger(__name__)


async def run_consult(
    slug: str,
    user_id: int,
    chat_id: int,
    server_name: str | None,
    task: str,
    context: str = "",
) -> str:
    """Consult the Agent ``slug`` with ``task`` and return its answer."""
    agent = AgentStore().get(slug)
    if agent is None:
        return f"No agent named '{slug}' is available."
    if not is_pydantic_ai_model(agent.agent_key):
        return (
            f"Agent '{slug}' is configured with agent_key='{agent.agent_key}', but "
            "consults require a pydantic-ai model (ollama/lmstudio/openai/groq/"
            "openrouter) so the tool allowlist can be enforced."
        )

    # Build the Agent's MCP toolset in the main process (ConfigManager is here).
    # agent_slug scopes the condor MCP tools' memory/skills to this Agent (its brain).
    from handlers.agents._shared import (
        build_agent_context,
        build_mcp_servers_for_agent,
        build_mcp_servers_for_session,
    )

    if agent.server_required and server_name:
        mcp_servers = build_mcp_servers_for_agent(
            server_name,
            user_id,
            chat_id,
            agent_slug=slug,
            execution_mode="loop",
        )
    else:
        mcp_servers = build_mcp_servers_for_session(
            user_id, chat_id, execution_mode="loop"
        )

    # Route the expert's dangerous-tool confirmations to the user's Telegram chat,
    # reusing the live bot registered at startup (main.py: routine_store.set_bot).
    permission_cb = None
    try:
        from condor.routine_store import get_routine_store
        from handlers.agents import confirmation

        bot = get_routine_store().get_bot()
        if bot is not None:
            permission_cb = functools.partial(
                confirmation.permission_callback, bot, chat_id
            )
    except Exception:
        log.exception(
            "Could not build consult permission callback; mutations will error"
        )

    client = PydanticAIClient(
        model=agent.agent_key,
        mcp_servers=mcp_servers,
        permission_callback=permission_cb,
        allowed_tools=agent.tools or None,
    )

    prompt = build_agent_context(agent, user_id, task, context)

    await client.start()
    try:
        answer = await client.prompt(prompt)
    finally:
        await client.stop()

    return answer or "(the agent returned no answer)"
