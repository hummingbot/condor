"""run_agent — the single execution primitive under tick / consult / experiment.

Refactor-02: the ways an agent's brain gets invoked used to be independent
execution stacks. This module owns the duplicated middle — model resolution,
MCP-server wiring, event streaming, timeout, and client reaping — exactly
once. ACP is the only model runner (§9.3). Everything kind-specific stays at
the call sites:

    consult    = run_agent(policy=human_gate(run_id=…))       → answer inline
    experiment = run_agent(policy=risk_gate(experiment=True)) → report inline
    tick       = run_agent(policy=risk_gate(journal))         → RunStore write-back
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from condor.accounts.model import AccountRef
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

from .agent import Agent

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 900


@dataclass
class RunResult:
    """What one agent run produced, in every view a caller needs.

    ``events`` is the chronological transcript (thoughts + tool calls +
    text); ``tool_calls`` is the folded tool-call list (tick-style).
    Both come from the same stream — no caller re-derives either.
    """

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    duration: float = 0.0
    error: str = ""
    timed_out: bool = False
    model: str = ""


def persist_prompt_companion(agent_id: str, prompt: str) -> None:
    """Write the run's ``prompt.md`` once (guarded on existence, so a per-tick
    re-entry only writes on tick 1). The single owner of that companion for
    every run kind — see :func:`run_agent`."""
    from condor.agents.runstore import get_run_store

    try:
        store = get_run_store()
        rp = store.find_run_path(agent_id)
        if rp is not None and not (rp.parent / "prompt.md").exists():
            store.write_companion(agent_id, "prompt.md", prompt)
    except Exception:
        log.exception("run_agent %s: prompt.md write failed", agent_id)


async def run_agent(
    agent: Agent,
    prompt: str,
    *,
    permission_policy: Callable | None,
    execution_mode: str = "loop",
    model: str | None = None,
    risk_limits: dict | None = None,
    account_ref: AccountRef | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    event_sink: Callable[[Any], None] | None = None,
    on_client: Callable[[Any], None] | None = None,
    agent_id: str = "",
    on_tool_call: Callable[[dict], None] | None = None,
    prompt_companion: str | None = None,
) -> RunResult:
    """Run ``agent``'s brain to completion on ``prompt``; return a RunResult.

    Args:
        permission_policy: ``None`` (AUTO) or a permission callback — see
            :mod:`condor.agents.policies`.
        agent_id: The run's opaque RunStore id (ULID, §7.1) threaded into the
            condor MCP subprocess so native executors are attributed to the
            run that created them; empty for plain chat sessions.
        model: Resolved model key; None → the agent's default. Callers own
            any override order (tick: config > agent).
        event_sink: Optional callback fed every raw streamed event as it
            arrives (live progress views); the folded views are always
            returned on the result regardless.
        on_client: Optional hook called with the live client after creation
            and with ``None`` after reaping — lets TickEngine.stop() keep its
            cancellation backstop on the in-flight subprocess.
        on_tool_call: Optional hook fired once per FOLDED tool call when it
            reaches a terminal status (completed/failed), so streamed
            arguments/output are present; calls still open at stream end are
            flushed as-is (RunStore streaming persistence) — the same dicts
            that land on ``result.tool_calls``.
    """
    import asyncio

    from condor.agents.context import (
        build_mcp_servers_for_session,
        get_project_dir,
    )

    result = RunResult()
    started = time.time()

    # Mint the run capability (§6.2): the opaque execution authority injected
    # into the tool session. Any invocation with an identity (tick run ids,
    # consult/experiment ephemeral ids) gets one — plain chat sessions
    # register condor-direct in the MCP wrapper instead. Revoked in the
    # finally below (run end).
    run_capability = None
    if agent_id:
        from condor.executors.capabilities import get_capability_registry

        run_capability = get_capability_registry().mint_run_capability(
            agent.slug,
            agent_id,
            risk_limits=risk_limits,
            account_ref=account_ref,
        )

    result.model = model or agent.agent_key

    # Persist the run's prompt companion (prompt.md) once — the single owner
    # of that write. The tick engine passes its stable prefix via
    # ``prompt_companion`` (so the prefix/suffix reconstruction contract
    # holds). Run-less verbs (consult/experiment) pass ephemeral ids with no
    # RunStore stream behind them, so this lookup no-ops and nothing lands
    # on disk.
    if agent_id:
        persist_prompt_companion(agent_id, prompt_companion or prompt)

    # -- MCP toolset wiring (agent_slug scopes memory/skills to this Agent) --
    mcp_servers = build_mcp_servers_for_session(
        execution_mode=execution_mode,
        agent_slug=agent.slug,
        agent_id=agent_id,
        capability=run_capability.id if run_capability else None,
    )

    # Enforce the agent's declared tool scope for system mutations: ACP agents
    # get the full MCP surface, so an undeclared agent/routine/skill mutation is
    # denied at the gate here (#8). Empty tools = unrestricted (unchanged).
    from condor.agents.policies import scope_gate

    permission_policy = scope_gate(permission_policy, agent.tools)

    agent_cmd, model_env, model_pref = resolve_acp(result.model)
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
    persisted_tool_ids: set[str] = set()

    def _persist(tc: dict) -> None:
        # Fire the audit hook once per call, when it reaches a terminal
        # status — arguments/output stream in on updates AFTER the create
        # event, so firing at create recorded `input: {}` for MCP calls.
        # Calls still open at stream end are flushed by _persist_remaining.
        if on_tool_call is None or tc.get("id") in persisted_tool_ids:
            return
        persisted_tool_ids.add(tc.get("id"))
        try:
            on_tool_call(tc)
        except Exception:
            log.exception("on_tool_call hook failed")

    def _persist_remaining() -> None:
        for tc in tc_map.values():
            _persist(tc)

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
            live = tc_map.get(event.tool_call_id)
            if live is not None and live.get("status") in ("completed", "failed"):
                _persist(live)

    await client.start()
    # The id the ACP bridge actually runs (session/set_model resolution) —
    # more truthful than the requested alias, and it makes bridge-side model
    # switches visible per tick on the run stream.
    result.model = getattr(client, "active_model_id", None) or result.model
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
        _persist_remaining()
        try:
            await client.stop()
        finally:
            if on_client is not None:
                on_client(None)
            # Run ended → its execution capability dies with it (§6.2).
            if run_capability is not None:
                from condor.executors.capabilities import get_capability_registry

                get_capability_registry().revoke_run(agent_id)

    result.text = "".join(chunks)
    result.duration = time.time() - started
    return result
