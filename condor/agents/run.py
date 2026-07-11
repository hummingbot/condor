"""run_agent — the single execution primitive under tick / delegation / consult.

Refactor-02: the three ways an agent's brain gets invoked used to be two
independent execution stacks (TickEngine's client/stream code vs the
consult/delegate shared runner). This module owns the duplicated middle —
model resolution, MCP-server wiring, the pydantic-ai-vs-ACP decision, event
streaming, timeout, and client reaping — exactly once. Everything
kind-specific stays at the call sites:

    consult    = run_agent(policy=human_gate(chat_id))   → return text inline
    delegation = run_agent(policy=risk_gate(zero) | AUTO) → session + notify
    tick       = run_agent(policy=risk_gate(journal))     → journal write-back
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from condor.acp.client import (
    ACPClient,
    PromptDone,
    TextChunk,
    ThoughtChunk,
    ToolCallEvent,
    ToolCallUpdate,
    fold_tool_call_event,
    resolve_acp,
)
from condor.acp.pydantic_ai_client import (
    PydanticAIClient,
    healthcheck_local_backend,
    is_pydantic_ai_model,
)

from .agent import Agent

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 900


@dataclass
class RunResult:
    """What one agent run produced, in every view a caller needs.

    ``events`` is the chronological transcript (thoughts + tool calls + text,
    delegate-style); ``tool_calls`` is the folded tool-call list (tick-style).
    Both come from the same stream — no caller re-derives either.
    """

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    duration: float = 0.0
    error: str = ""
    timed_out: bool = False
    model: str = ""
    fallback_note: str = ""


async def run_agent(
    agent: Agent,
    prompt: str,
    *,
    permission_policy: Callable | None,
    user_id: int,
    chat_id: int,
    server_name: str | None = None,
    execution_mode: str = "loop",
    model: str | None = None,
    model_base_url: str | None = None,
    tool_filter_mode: str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    event_sink: Callable[[Any], None] | None = None,
    fallback_on_unhealthy: bool = False,
    on_client: Callable[[Any], None] | None = None,
    condor_tool_profile: str | None = None,
) -> RunResult:
    """Run ``agent``'s brain to completion on ``prompt``; return a RunResult.

    Args:
        permission_policy: ``None`` (AUTO) or a permission callback — see
            :mod:`condor.agents.policies`.
        server_name: The EFFECTIVE hummingbot server for this run (caller
            resolves pin-vs-ambient). None → session wiring (ambient server,
            or none for serverless agents).
        model: Resolved model key; None → the agent's default. Callers own
            any override triad (tick: config > strategy > agent).
        fallback_on_unhealthy: When True and the (pydantic-ai) backend is
            down, fall back to CONSULT_FALLBACK_MODEL (default claude-code)
            with a visible note — consult/delegate behavior. When False, a
            dead backend raises so a tick never silently trades on an
            unintended model.
        event_sink: Optional callback fed every raw streamed event as it
            arrives (live progress views); the folded views are always
            returned on the result regardless.
        on_client: Optional hook called with the live client after creation
            and with ``None`` after reaping — lets TickEngine.stop() keep its
            cancellation backstop on the in-flight subprocess.
        condor_tool_profile: Named tool profile for the condor MCP subprocess
            (e.g. "curation") — call-time enforcement that holds even for ACP
            models, which cannot be tool-allowlisted client-side.

    Raises:
        RuntimeError: model backend unavailable and fallback disabled/unset.
    """
    import asyncio

    from handlers.agents._shared import (
        build_mcp_servers_for_agent,
        build_mcp_servers_for_session,
        get_project_dir,
    )

    result = RunResult()
    started = time.time()

    # -- model resolution (+ optional healthcheck/fallback) --
    model_key = model or agent.agent_key
    if is_pydantic_ai_model(model_key):
        backend_err = await healthcheck_local_backend(model_key)
        if backend_err:
            fallback = os.environ.get("CONSULT_FALLBACK_MODEL", "claude-code").strip()
            if fallback_on_unhealthy and fallback and fallback != model_key:
                log.warning(
                    "Backend for '%s' (%s) unavailable (%s); falling back to %s",
                    agent.slug,
                    model_key,
                    backend_err,
                    fallback,
                )
                result.fallback_note = (
                    f"_(note: {agent.name}'s configured model was unavailable — "
                    f"{backend_err} Answered with fallback `{fallback}`.)_\n\n"
                )
                model_key = fallback
            else:
                raise RuntimeError(
                    f"The '{agent.slug}' agent's model backend is unavailable: "
                    f"{backend_err}"
                )
    result.model = model_key

    # -- MCP toolset wiring (agent_slug scopes memory/skills to this Agent) --
    if server_name:
        mcp_servers = build_mcp_servers_for_agent(
            server_name,
            user_id,
            chat_id,
            agent_slug=agent.slug,
            execution_mode=execution_mode,
            tool_profile=condor_tool_profile,
        )
    else:
        mcp_servers = build_mcp_servers_for_session(
            user_id,
            chat_id,
            execution_mode=execution_mode,
            agent_slug=agent.slug,
            tool_profile=condor_tool_profile,
        )

    # -- client factory: pydantic-ai (allowlist enforced) or ACP subprocess --
    if is_pydantic_ai_model(model_key):
        client = PydanticAIClient(
            model=model_key,
            mcp_servers=mcp_servers,
            permission_callback=permission_policy,
            base_url=model_base_url or None,
            tool_filter_mode=tool_filter_mode
            or os.environ.get("PYDANTIC_AI_TOOL_FILTER")
            or None,
            allowed_tools=agent.tools or None,
        )
    else:
        agent_cmd, model_env, model_pref = resolve_acp(model_key)
        client = ACPClient(
            command=agent_cmd,
            working_dir=get_project_dir(),
            mcp_servers=mcp_servers,
            permission_callback=permission_policy,
            extra_env=model_env or None,
            model=model_pref or None,
        )

    if on_client is not None:
        on_client(client)

    # -- drive the stream, folding both views once --
    chunks: list[str] = []
    tc_map: dict[str, dict] = {}

    def _fold(event: Any) -> None:
        if event_sink is not None:
            event_sink(event)
        if isinstance(event, ThoughtChunk):
            ev = result.events
            if ev and ev[-1]["type"] == "thought":
                ev[-1]["text"] += event.text
            else:
                ev.append({"type": "thought", "text": event.text})
        elif isinstance(event, TextChunk):
            chunks.append(event.text)
            ev = result.events
            if ev and ev[-1]["type"] == "text":
                ev[-1]["text"] += event.text
            else:
                ev.append({"type": "text", "text": event.text})
        elif isinstance(event, (ToolCallEvent, ToolCallUpdate)):
            tc = fold_tool_call_event(tc_map, event)
            if tc is not None:
                tc["type"] = "tool"
                result.tool_calls.append(tc)
                result.events.append(tc)

    await client.start()
    try:
        async with asyncio.timeout(timeout_s):
            async for event in client.prompt_stream(prompt):
                _fold(event)
                if isinstance(event, PromptDone):
                    break
    except asyncio.TimeoutError:
        log.warning("run_agent(%s): timed out after %ss", agent.slug, timeout_s)
        chunks.append("(timed out)")
        result.timed_out = True
        result.error = f"Timed out after {timeout_s}s"
    finally:
        try:
            await client.stop()
        finally:
            if on_client is not None:
                on_client(None)

    result.text = "".join(chunks)
    result.duration = time.time() - started
    return result
