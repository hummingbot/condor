"""Run an Agent consult to completion in the main process.

``condor`` (the coordinator) calls the ``consult`` MCP tool, which calls back into
the main process (where ``ConfigManager`` and the agent runtime live) and lands
here. We load the Agent, build its restricted toolset, run its own brain to
completion on its configured pydantic-ai model — or, if that model's local backend
is down, on a claude-code/ACP fallback — and return its answer text. No strategy is
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
import os

from condor.acp.pydantic_ai_client import (
    PydanticAIClient,
    healthcheck_local_backend,
    is_pydantic_ai_model,
)
from condor.agents.agent import AgentStore

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
    store = AgentStore()
    agent = store.get(slug)
    if agent is None:
        index = store.list_consultable_index()
        available = f"\n\nAvailable experts:\n{index}" if index else ""
        return f"No agent named '{slug}' is available.{available}"
    if not is_pydantic_ai_model(agent.agent_key):
        return (
            f"Agent '{slug}' is configured with agent_key='{agent.agent_key}', but "
            "consults require a pydantic-ai model (ollama/lmstudio/openai/groq/"
            "openrouter) so the tool allowlist can be enforced."
        )

    # Preflight the model backend so a stopped Ollama/LM Studio fails fast with a
    # clear reason instead of a deep httpx error mid-run. If a local backend is
    # down we fall back to claude-code (ACP), which needs no local backend.
    # Override the fallback with CONSULT_FALLBACK_MODEL, or set it to "" to disable.
    model_key = agent.agent_key
    fallback_note = ""
    backend_err = await healthcheck_local_backend(model_key)
    if backend_err:
        fallback = os.environ.get("CONSULT_FALLBACK_MODEL", "claude-code").strip()
        if fallback and fallback != model_key:
            log.warning(
                "Consult backend for '%s' unavailable (%s); falling back to %s",
                slug,
                backend_err,
                fallback,
            )
            model_key = fallback
            fallback_note = (
                f"_(note: {agent.name}'s configured model was unavailable — "
                f"{backend_err} Answered with fallback `{fallback}`.)_\n\n"
            )
        else:
            return (
                f"The '{slug}' expert is unavailable: {backend_err}\n\n"
                "Start the model backend, or set CONSULT_FALLBACK_MODEL to a "
                "reachable model to auto-fall-back."
            )

    # Build the Agent's MCP toolset in the main process (ConfigManager is here).
    # agent_slug scopes the condor MCP tools' memory/skills to this Agent (its brain).
    from handlers.agents._shared import (
        build_agent_context,
        build_mcp_servers_for_agent,
        build_mcp_servers_for_session,
        get_project_dir,
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

    # Build the client for the (possibly fallback) model. A pydantic-ai model gets
    # the agent's tool allowlist enforced; an ACP fallback (claude-code) cannot
    # enforce an allowlist, so it runs the consult unrestricted — acceptable since
    # it is the trusted coordinator model and mutations are still confirmation-gated.
    if is_pydantic_ai_model(model_key):
        client = PydanticAIClient(
            model=model_key,
            mcp_servers=mcp_servers,
            permission_callback=permission_cb,
            allowed_tools=agent.tools or None,
        )
    else:
        from condor.acp.client import ACPClient, resolve_acp

        agent_cmd, model_env = resolve_acp(model_key)
        client = ACPClient(
            command=agent_cmd,
            working_dir=get_project_dir(),
            mcp_servers=mcp_servers,
            permission_callback=permission_cb,
            extra_env=model_env or None,
        )

    prompt = build_agent_context(agent, user_id, task, context)

    await client.start()
    try:
        answer = await client.prompt(prompt)
    finally:
        await client.stop()

    return fallback_note + (answer or "(the agent returned no answer)")
