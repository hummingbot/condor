"""Run an Agent consult to completion in the main process.

``condor`` (the coordinator) calls the ``consult`` MCP tool, which calls back into
the main process (where ``ConfigManager`` and the agent runtime live) and lands
here. We load the Agent, build its toolset, run its own brain to completion on its
configured model — a pydantic-ai key (allowlist enforced) or an ACP key like
claude-code (unrestricted, mutations still confirmation-gated); a pydantic-ai key
whose local backend is down falls back to claude-code — and return its answer text.
No strategy is involved — CONSULT runs the Agent's identity + shared memory/skills.

The Agent may call mutating tools; those are gated by the SAME interactive
confirmation flow condor uses, routed to the user's Telegram chat. Approvals live
in :mod:`condor.runtime.confirmations` as addressable entries, so the user's
Approve/Reject resolves the pending request even while condor's own session is busy
awaiting the consult result — and it can be answered from the dashboard instead.
"""

from __future__ import annotations

import logging
import os

from condor.acp.pydantic_ai_client import (
    PydanticAIClient,
    healthcheck_local_backend,
    is_pydantic_ai_model,
)
from condor.agents.agent import AgentStore
from condor.preferences import resolve_custom_endpoint

log = logging.getLogger(__name__)


def _build_consult_permission_cb(slug: str, user_id: int, chat_id: int):
    """Build the human-confirm callback for a consult.

    Registers into the shared confirmation registry, so the approval is also
    listed by ``GET /api/v1/confirmations`` and can be answered from the
    dashboard rather than only by tapping in Telegram. Returns ``None`` when no
    bot is available, which keeps today's behavior: mutations then error rather
    than being silently auto-approved.
    """
    try:
        from condor.routine_store import get_routine_store
        from condor.runtime.confirmations import build_permission_callback
        from handlers.agents.confirmation import TelegramChannel

        bot = get_routine_store().get_bot()
        if bot is not None:
            return build_permission_callback(
                session_key=f"consult:{slug}",
                user_id=user_id,
                channels=[TelegramChannel(bot, chat_id)],
            )
    except Exception:
        log.exception(
            "Could not build consult permission callback; mutations will error"
        )
    return None


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
    (:mod:`condor.agents.delegate`), which reuses :func:`_run_agent_to_completion`
    with ``permission_callback=None`` (auto-approve).
    """
    permission_cb = _build_consult_permission_cb(slug, user_id, chat_id)
    return await _run_agent_to_completion(
        slug=slug,
        user_id=user_id,
        chat_id=chat_id,
        server_name=server_name,
        task=task,
        context=context,
        permission_callback=permission_cb,
    )


async def _run_agent_to_completion(
    slug: str,
    user_id: int,
    chat_id: int,
    server_name: str | None,
    task: str,
    context: str = "",
    permission_callback=None,
    event_sink=None,
    delegate_worker: bool = False,
) -> str:
    """Load the Agent ``slug``, run its brain to completion on ``task``, return text.

    Shared engine of a single agent run. CONSULT passes a human-confirm
    ``permission_callback``; DELEGATE passes ``None`` so an ACP agent auto-approves
    its own tool calls (unattended). No strategy is involved — the Agent's identity
    + shared memory/skills drive the run, and ``client.prompt()`` returning IS the
    "task done" signal.

    If ``event_sink`` is provided, it is called with every streamed
    :data:`condor.acp.client.ACPEvent` (thoughts, tool calls, text) as they arrive,
    so a caller can persist the full session transcript. When ``None`` (CONSULT's
    path) the cheaper one-shot ``client.prompt()`` is used and behavior is unchanged.

    ``delegate_worker`` is DELEGATE's flag (FEAT-032). It only bites when the
    delegated agent IS Condor: chat and background worker are then the same record
    and the subprocess needs telling which one it is. A specialist already reads
    its own ``_agent_base`` framing — and is explicitly invited there to delegate
    long work onward — so its delegations are left exactly as they were.
    """
    store = AgentStore()
    agent = store.get(slug)
    if agent is None:
        index = store.list_index()
        available = f"\n\nAvailable agents:\n{index}" if index else ""
        return f"No agent named '{slug}' is available.{available}"
    # Every Agent is consultable — there is no separate "expert" kind and no
    # capability gate. Only a pydantic-ai key has a local backend to preflight, so
    # a stopped Ollama/LM Studio fails fast with a clear reason (and falls back to
    # claude-code) instead of a deep httpx error mid-run. ACP keys (claude-code/
    # gemini/copilot) need no backend and route straight to the ACP client below.
    # Override the fallback with CONSULT_FALLBACK_MODEL, or set it to "" to disable.
    model_key = agent.agent_key
    fallback_note = ""
    # A custom endpoint's URL/key live in the user's saved endpoints, not in the
    # agent record — resolve them here so consult can reach the same provider
    # the user's chat is using. Returns (None, None) for every other key type.
    base_url, api_key = resolve_custom_endpoint(model_key, user_id=user_id)
    if is_pydantic_ai_model(model_key):
        backend_err = await healthcheck_local_backend(
            model_key, base_url=base_url, api_key=api_key
        )
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
                # The fallback is a different model — re-resolve, or a custom
                # endpoint's credentials would leak into an unrelated backend.
                base_url, api_key = resolve_custom_endpoint(model_key, user_id=user_id)
                fallback_note = (
                    f"_(note: {agent.name}'s configured model was unavailable — "
                    f"{backend_err} Answered with fallback `{fallback}`.)_\n\n"
                )
            else:
                return (
                    f"The '{slug}' agent is unavailable: {backend_err}\n\n"
                    "Start the model backend, or set CONSULT_FALLBACK_MODEL to a "
                    "reachable model to auto-fall-back."
                )

    # Build the Agent's MCP toolset in the main process (ConfigManager is here).
    # agent_slug scopes the condor MCP tools' memory/skills to this Agent (its brain).
    from handlers.agents._shared import (
        build_agent_context,
        build_mcp_servers_for_session,
        get_project_dir,
    )

    # A server pinned on the Agent itself wins over the ambient chat server; when
    # the agent isn't pinned, fall back to the caller's (chat's) resolved server.
    # Passing server_name=None lets the builder resolve the chat's server.
    # Serverless agents still need their own memory/skill scope — without
    # agent_slug the condor MCP tools would target the CHAT's stores.
    effective_server = agent.server_name or server_name
    from condor.memory.paths import CHAT_SLUG

    mcp_servers = build_mcp_servers_for_session(
        user_id,
        chat_id,
        server_name=effective_server if agent.server_required else None,
        agent_slug=slug,
        delegate_worker=delegate_worker and slug == CHAT_SLUG,
    )

    # ``permission_callback`` is passed in: CONSULT routes dangerous-tool
    # confirmations to the user's Telegram chat; DELEGATE passes None so an ACP
    # agent auto-approves (unattended).
    permission_cb = permission_callback

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
            base_url=base_url,
            api_key=api_key,
        )
    else:
        from condor.acp.client import ACPClient, resolve_acp

        agent_cmd, model_env, model_pref = resolve_acp(model_key)
        client = ACPClient(
            command=agent_cmd,
            working_dir=get_project_dir(),
            mcp_servers=mcp_servers,
            permission_callback=permission_cb,
            extra_env=model_env or None,
            model=model_pref or None,
        )

    prompt = build_agent_context(agent, user_id, task, context)

    await client.start()
    try:
        if event_sink is None:
            answer = await client.prompt(prompt)
        else:
            from condor.acp.client import TextChunk

            chunks: list[str] = []
            async for event in client.prompt_stream(prompt):
                event_sink(event)
                if isinstance(event, TextChunk):
                    chunks.append(event.text)
            answer = "".join(chunks)
    finally:
        await client.stop()

    return fallback_note + (answer or "(the agent returned no answer)")
