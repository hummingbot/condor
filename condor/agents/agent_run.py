"""Run an Agent's brain to completion in the main process.

The shared engine behind every non-chat Agent run. It never knows which door it
was reached through: it loads the Agent, builds its toolset, and runs its own
brain on its configured model — a pydantic-ai key (allowlist enforced) or an ACP
key like claude-code (unrestricted); a pydantic-ai key whose local backend is
down falls back to claude-code. No strategy is involved — a run here is the
Agent's identity + shared memory/skills.

**Two doors, one engine**, and the difference is only who waits:

* ``delegate(action="start")`` — :mod:`condor.agents.delegate`. A *detached*
  session that carries one task to completion, writes a transcript, and notifies
  the user. The asking turn does not wait.
* ``delegate(action="ask")`` — :func:`run_ask`, below. The asking turn *does*
  wait, and gets the answer back as a string. This is inter-agent communication:
  one agent needs another domain's answer to carry on with its own reasoning.

The second door exists because the first cannot serve an **unattended** seat. A
tick and a background worker run for nobody's conversation and pass no
``session_key`` (see :func:`condor.runtime.toolsets.build_mcp_servers_for_session`),
so ``on_complete="resume"`` has nothing to wake and returns ``False`` silently,
while ``notify`` sends the answer to the *user* rather than to the agent that
needed it. For those seats a blocking ask is not a worse delegation — it is the
only shape that works, and the cheap one: one ``client.prompt()`` and a ledger
row, against a detached task with an event sidecar, a transcript and a bell.

Every run here is **unattended**: no ``permission_callback`` is built, so an ACP
agent auto-approves its own tool calls. See :mod:`condor.runtime.confirmations`,
which still gates the *chat* seats where a human is actually watching. Because
an ask is auto-approved and its target could ask onward, depth is bounded at one
— see ``ask_target`` below.
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

log = logging.getLogger(__name__)

# How much of an answer the ledger keeps -- the same 2000 characters every other
# body in this store is cut at (``delegate.MAX_TOOL_OUTPUT``), stated separately
# because it bounds a different thing: a record is a row in a list, not a second
# copy of the answer. The answer itself is returned to the caller uncut.
MAX_RECORDED_RESULT = 2000


async def run_agent_to_completion(
    slug: str,
    user_id: int,
    chat_id: int,
    server_name: str | None,
    task: str,
    context: str = "",
    event_sink=None,
    delegate_worker: bool = False,
    ask_target: bool = False,
) -> str:
    """Load the Agent ``slug``, run its brain to completion on ``task``, return text.

    The run is unattended: no ``permission_callback`` is built, so an ACP agent
    auto-approves its own tool calls and ``client.prompt()`` returning IS the
    "task done" signal. No strategy is involved — the Agent's identity + shared
    memory/skills drive the run.

    If ``event_sink`` is provided, it is called with every streamed
    :data:`condor.acp.client.ACPEvent` (thoughts, tool calls, text) as they arrive,
    so a caller can persist the full session transcript. When ``None`` the cheaper
    one-shot ``client.prompt()`` is used.

    ``delegate_worker`` is DELEGATE's flag (FEAT-032): it tells the subprocess it
    is the detached background seat rather than the interactive one. Every agent
    gets it now, not just Condor (FEAT-041) — an agent can start a delegation of
    *itself*, so a specialist's background session needs the same marker, both to
    read the unattended framing and so the guard in ``tools/delegate.py`` can stop
    it from spawning a copy of itself in turn. Handing work to a PEER stays open
    for a specialist worker; only self-recursion is closed.

    ``ask_target`` is the same idea for the *other* door: it marks a run that is
    already answering someone else's ``delegate(action="ask")``. An ask is
    auto-approved and returns to a caller that is blocked on it, so a target free
    to ask onward would nest blocked callers to arbitrary depth with nobody
    watching any of them. The flag rides argv to the subprocess and the guard in
    ``tools/delegate.py`` refuses a second ask; ``start`` stays open, because a
    detached task does not hold the asking turn open behind it.
    """
    store = AgentStore()
    agent = store.get(slug)
    if agent is None:
        index = store.list_index()
        available = f"\n\nAvailable agents:\n{index}" if index else ""
        return f"No agent named '{slug}' is available.{available}"
    # Every Agent can be delegated to — there is no separate "expert" kind and no
    # capability gate. Only a pydantic-ai key has a local backend to preflight, so
    # a stopped Ollama/LM Studio fails fast with a clear reason (and falls back to
    # claude-code) instead of a deep httpx error mid-run. ACP keys (claude-code/
    # gemini/copilot) need no backend and route straight to the ACP client below.
    # Override the fallback with AGENT_FALLBACK_MODEL, or set it to "" to disable.
    # ``CONSULT_FALLBACK_MODEL`` was this knob's name when the only door was the
    # consult tool, and is still honoured so a deployment that set it does not
    # silently lose its override.
    model_key = agent.agent_key
    fallback_note = ""
    # A custom endpoint's URL/key live in the user's saved endpoints, not in the
    # agent record — resolve them here so the run can reach the same provider
    # the user's chat is using. Returns (None, None) for every other key type.
    base_url, api_key = resolve_custom_endpoint(model_key, user_id=user_id)
    if is_pydantic_ai_model(model_key):
        backend_err = await healthcheck_local_backend(
            model_key, base_url=base_url, api_key=api_key
        )
        if backend_err:
            fallback = os.environ.get(
                "AGENT_FALLBACK_MODEL",
                os.environ.get("CONSULT_FALLBACK_MODEL", "claude-code"),
            ).strip()
            if fallback and fallback != model_key:
                log.warning(
                    "Backend for '%s' unavailable (%s); falling back to %s",
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
                    "Start the model backend, or set AGENT_FALLBACK_MODEL to a "
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
        ask_target=ask_target,
    )

    # Build the client for the (possibly fallback) model through the shared
    # factory (ARCH-192). A pydantic-ai model gets the agent's tool allowlist
    # enforced; an ACP model (claude-code) cannot enforce an allowlist, so it
    # runs unrestricted — acceptable for a delegation, which is unattended by
    # design and only started for a trusted agent. The factory re-resolves the
    # custom endpoint (same lenient inputs as the healthcheck above), so a
    # fallback model never inherits the original's credentials.
    from condor.runtime.llm_client import build_llm_client

    client = build_llm_client(
        model_key,
        mcp_servers=mcp_servers,
        # Unattended: nobody is watching, so there is no one to ask. An ACP
        # agent auto-approves its own tool calls.
        permission_callback=None,
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


def _clip(text: str) -> str:
    """An answer, bounded for the ledger."""
    text = text or ""
    return (
        text
        if len(text) <= MAX_RECORDED_RESULT
        else text[:MAX_RECORDED_RESULT] + "\n… (truncated)"
    )


async def run_ask(
    slug: str,
    user_id: int,
    chat_id: int,
    server_name: str | None,
    task: str,
    context: str = "",
    caller: str = "",
) -> str:
    """Ask the Agent ``slug`` ``task`` and return its answer to a waiting caller.

    The synchronous door onto :func:`run_agent_to_completion` — inter-agent
    communication, reached as ``delegate(action="ask")``. Its asynchronous
    sibling is ``delegate(action="start")``
    (:func:`condor.agents.delegate.start_delegation`), which detaches instead.

    Every ask leaves a **record** (FEAT-058), which is the whole reason this
    function is more than a one-line forward to the engine. Asks are the channel
    agents actually use — dozens of runs where a delegation happens once — and
    without this each one would run, spend tokens, possibly call mutating tools,
    and vanish. The same store delegations use gets a small ledger entry: what
    was asked, who asked (``caller`` — an agent's slug, "" when a person asked
    directly), when, and how it ended. The kind on disk is
    :data:`~condor.agents.run_records.KIND_CONSULT`: the door was renamed, the
    thing recorded was not, and one spelling keeps an install's history in one
    filter rather than splitting it across two that mean the same thing.

    The write at the *start* is the load-bearing one — it is what makes an ask
    the process died during read back as ``interrupted`` rather than as nothing
    at all. Every write is best-effort and swallowed inside
    :func:`~condor.agents.run_records.record_run`: bookkeeping must never be why
    an ask failed, and a failing ask must still raise to its caller.

    **Which thread each write runs on is part of the contract** (PERF-293). The
    start write is sub-millisecond — one small merge, no retention — and must
    land before the engine starts, so it stays inline. A *terminal* write also
    prunes, and pruning reads a status file per record this owner has; on a
    store at its caps that is tens of milliseconds of blocking IO, and this
    coroutine is awaited on the loop uvicorn and the Telegram poller share. So
    the terminal writes go through ``asyncio.to_thread``, exactly as
    ``condor.sharing.sweep`` and the sharing routes do (PERF-235):
    :func:`~condor.agents.run_records.record_run` never awaits and
    ``write_status`` takes only its per-file thread lock, so it is safe to call
    from a worker. The latency is still the ask's; the blocking never was.
    """
    run_id = f"{slug}-consult-{uuid.uuid4().hex[:8]}"
    started_at = time.time()
    # Same shape as a delegation id, and the ``-consult-`` infix is what keeps
    # the two from ever colliding in a directory they share.
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
        answer = await run_agent_to_completion(
            slug=slug,
            user_id=user_id,
            chat_id=chat_id,
            server_name=server_name,
            task=task,
            context=context,
            # Depth 1: whoever answers this may not ask onward (see the flag's
            # note on ``run_agent_to_completion``).
            ask_target=True,
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
        # rare cancelled ask rather than on every one.
        record_run(state="stopped", **stamp)
        raise
    except Exception as exc:
        await asyncio.to_thread(record_run, state="error", error=str(exc), **stamp)
        raise

    await asyncio.to_thread(record_run, state="done", result=_clip(answer), **stamp)
    return answer
