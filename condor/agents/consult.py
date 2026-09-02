"""Run an Agent consult to completion in the main process.

``condor`` (the coordinator) calls the ``consult`` MCP tool, which calls back into
the main process (where ``ConfigManager`` and the agent runtime live) and lands
here. We load the Agent, build its toolset, run its own brain to completion on its
configured model — a pydantic-ai key (allowlist enforced) or an ACP key like
claude-code (unrestricted, mutations still confirmation-gated); a pydantic-ai key
whose local backend is down falls back to claude-code — and return its answer text.
No strategy is involved — CONSULT runs the Agent's identity + shared memory/skills.

Since FEAT-058 a consult also *leaves a record*. It is the channel every other
agent, the Telegram bot and the dashboard actually use -- dozens of runs where a
delegation happens once -- and until now every one of them ran and then vanished
without a trace. Each consult now writes a small ledger entry into the same
per-user store delegations use (:mod:`condor.agents.run_records`): the ask, the
caller, the outcome, the timing. Not a transcript -- see that module for why the
ledger and the tape are different questions.

The Agent may call mutating tools; those are gated by the SAME interactive
confirmation flow condor uses, routed to the user's Telegram chat. Approvals live
in :mod:`condor.runtime.confirmations` as addressable entries, so the user's
Approve/Reject resolves the pending request even while condor's own session is busy
awaiting the consult result — and it can be answered from the dashboard instead.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid

from condor.acp.pydantic_ai_client import (
    healthcheck_local_backend,
    is_pydantic_ai_model,
)
from condor.agents.agent import AgentStore
from condor.agents.run_records import KIND_CONSULT, record_run
from condor.preferences import resolve_custom_endpoint
from condor.runtime import context as runtime_context
from condor.runtime import toolsets
from condor.runtime.channels import TelegramChannel

log = logging.getLogger(__name__)

# How much of an answer the ledger keeps -- the same 2000 characters every other
# body in this store is cut at (``delegate.MAX_TOOL_OUTPUT``), stated separately
# because it bounds a different thing: a record is a row in a list, not a second
# copy of the answer. The answer itself is returned to the caller uncut.
MAX_RECORDED_RESULT = 2000


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


def _clip(text: str) -> str:
    """An answer, bounded for the ledger."""
    text = text or ""
    return (
        text
        if len(text) <= MAX_RECORDED_RESULT
        else text[:MAX_RECORDED_RESULT] + "\n… (truncated)"
    )


async def run_consult(
    slug: str,
    user_id: int,
    chat_id: int,
    server_name: str | None,
    task: str,
    context: str = "",
    caller: str = "",
) -> str:
    """Consult the Agent ``slug`` with ``task`` and return its answer.

    CONSULT is synchronous and human-gated: mutating tools are confirmed via the
    user's Telegram chat. Its async, unattended sibling is DELEGATE
    (:mod:`condor.agents.delegate`), which reuses :func:`_run_agent_to_completion`
    with ``permission_callback=None`` (auto-approve).

    Every consult leaves a **record** (FEAT-058), which is the whole reason this
    function is more than a one-line forward to the engine. Consults are the
    channel every other agent, the bot and the dashboard actually use; before
    this each one ran, spent tokens, possibly called mutating tools, and then
    vanished. Now the same store delegations use gets a small ledger entry: what
    was asked, who asked (``caller`` -- an agent's slug, "" when a person asked
    directly), when, and how it ended.

    The write at the *start* is the load-bearing one -- it is what makes a
    consult the process died during read back as ``interrupted`` rather than as
    nothing at all. Every write is best-effort and swallowed inside
    :func:`~condor.agents.run_records.record_run`: bookkeeping must never be why
    a consult failed, and a failing consult must still raise to its caller.

    **Which thread each write runs on is part of the contract** (PERF-293). The
    start write is sub-millisecond -- one small merge, no retention -- and must
    land before the engine starts, so it stays inline. A *terminal* write also
    prunes, and pruning reads a status file per record this owner has; on a
    store at its caps that is tens of milliseconds of blocking IO, and this
    coroutine is awaited on the loop uvicorn and the Telegram poller share. So
    the terminal writes go through ``asyncio.to_thread``, exactly as
    ``condor.sharing.sweep`` and the sharing routes do (PERF-235):
    :func:`~condor.agents.run_records.record_run` never awaits and
    ``write_status`` takes only its per-file thread lock, so it is safe to call
    from a worker. The latency is still the consult's; the blocking never was.
    """
    permission_cb = _build_consult_permission_cb(slug, user_id, chat_id)

    run_id = f"{slug}-consult-{uuid.uuid4().hex[:8]}"
    started_at = time.time()
    # Same shape as a delegation id, and the ``-consult-`` infix is what keeps
    # the two from ever colliding in a directory they now share.
    stamp = {
        "user_id": user_id,
        "run_id": run_id,
        "agent_slug": slug,
        "kind": KIND_CONSULT,
        "task": task,
        "started_at": started_at,
        "chat_id": chat_id,
        "server_name": server_name,
        "caller": caller,
    }
    record_run(state="running", **stamp)

    try:
        answer = await _run_agent_to_completion(
            slug=slug,
            user_id=user_id,
            chat_id=chat_id,
            server_name=server_name,
            task=task,
            context=context,
            permission_callback=permission_cb,
        )
    except asyncio.CancelledError:
        # The caller disconnected or the MCP timeout fired. A record left saying
        # "running" would read as interrupted on the next boot and as live until
        # then; stopped is what actually happened.
        #
        # This one stays *synchronous*, unlike its two siblings below. A
        # cancelled task cannot reliably await anything -- a fresh
        # ``to_thread`` here would be cancelled at the next suspension point and
        # the run would keep its "running" record -- and it is also the write
        # closest to the start write, so offloading it is the one ordering the
        # per-file lock cannot save us from. It is a single small merge and,
        # since ``stopped`` is terminal, it does prune; that cost is paid on the
        # rare cancelled consult rather than on every one.
        record_run(state="stopped", **stamp)
        raise
    except Exception as exc:
        await asyncio.to_thread(record_run, state="error", error=str(exc), **stamp)
        raise

    await asyncio.to_thread(record_run, state="done", result=_clip(answer), **stamp)
    return answer


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

    ``delegate_worker`` is DELEGATE's flag (FEAT-032): it tells the subprocess it
    is the detached background seat rather than the interactive one. Every agent
    gets it now, not just Condor (FEAT-041) — an agent can start a delegation of
    *itself*, so a specialist's background session needs the same marker, both to
    read the unattended framing and so the guard in ``tools/delegate.py`` can stop
    it from spawning a copy of itself in turn. Handing work to a PEER stays open
    for a specialist worker; only self-recursion is closed.
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
    # A server pinned on the Agent itself wins over the ambient chat server; when
    # the agent isn't pinned, fall back to the caller's (chat's) resolved server.
    # Passing server_name=None lets the builder resolve the chat's server.
    # Serverless agents still need their own memory/skill scope — without
    # agent_slug the condor MCP tools would target the CHAT's stores.
    effective_server = agent.server_name or server_name

    mcp_servers = toolsets.build_mcp_servers_for_session(
        user_id,
        chat_id,
        server_name=effective_server if agent.server_required else None,
        agent_slug=slug,
        delegate_worker=delegate_worker,
    )

    # ``permission_callback`` is passed in: CONSULT routes dangerous-tool
    # confirmations to the user's Telegram chat; DELEGATE passes None so an ACP
    # agent auto-approves (unattended).
    permission_cb = permission_callback

    # Build the client for the (possibly fallback) model through the shared
    # factory (ARCH-192). A pydantic-ai model gets the agent's tool allowlist
    # enforced; an ACP fallback (claude-code) cannot enforce an allowlist, so it
    # runs the consult unrestricted — acceptable since it is the trusted
    # coordinator model and mutations are still confirmation-gated. The factory
    # re-resolves the custom endpoint (same lenient inputs as the healthcheck
    # above), so a fallback model never inherits the original's credentials.
    from condor.runtime.llm_client import build_llm_client

    client = build_llm_client(
        model_key,
        mcp_servers=mcp_servers,
        permission_callback=permission_cb,
        allowed_tools=agent.tools or None,
        user_id=user_id,
    )

    prompt = runtime_context.build_agent_context(agent, user_id, task, context)

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
