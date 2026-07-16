"""Chat session lifecycle manager (web dashboard transport).

Transport-agnostic: sessions are keyed by an opaque session key (an int, or
an opaque string minted by the web chat at WS connect). The health monitor
takes an injected async notifier instead of a transport handle.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from condor.acp import ACPClient, PermissionCallback, PromptDone, resolve_acp
from condor.agents.context import (
    build_initial_context,
    build_mcp_servers_for_session,
    get_project_dir,
)

log = logging.getLogger(__name__)

# Timeout for acquiring the session lock (seconds).
# If another prompt is running, we wait this long before giving up.
PROMPT_LOCK_TIMEOUT = 30

# Maximum wall-clock time for a single prompt (seconds).
# Prevents infinite loops when the agent subprocess stalls.
PROMPT_OVERALL_TIMEOUT = 1800  # 30 minutes

# Module-level session storage (not persisted -- subprocesses can't survive restarts)
_sessions: dict[int | str, "AgentSession"] = {}

# Health monitor state
_health_task: asyncio.Task | None = None
# Injected notifier: async (session_key) -> None, called when a dead session
# is reaped. Transport decides how (WS event, nothing).
_health_notifier: Callable[[int | str], Awaitable[None]] | None = None


@dataclass
class AgentSession:
    chat_id: int | str
    agent_key: str  # ACP key: "claude-code", "claude-acp:opus", "gemini", ...
    client: ACPClient
    # Kept for the web session_started payload; no server is wired anymore.
    server_name: str | None = None
    is_busy: bool = False
    pending_context: str | None = None  # Lazy context: injected on first prompt
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _abort_event: asyncio.Event = field(default_factory=asyncio.Event)

    async def prompt_stream(self, text: str):
        """Stream a prompt, managing the busy flag and lock.

        Includes a lock-acquisition timeout (PROMPT_LOCK_TIMEOUT) to avoid
        waiting forever when a previous prompt is stuck, and an overall
        wall-clock timeout (PROMPT_OVERALL_TIMEOUT) to kill runaway prompts.
        """
        # Consume pending context on first prompt (lazy injection)
        if self.pending_context:
            ctx = self.pending_context
            self.pending_context = None
            text = f"{ctx}\n\n---\n\nUser message:\n{text}"

        # Clear abort flag for this new prompt
        self._abort_event.clear()

        # Acquire lock with timeout -- prevents infinite wait when previous prompt is stuck
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=PROMPT_LOCK_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("Lock acquisition timed out for chat %s", self.chat_id)
            # Force-clear busy flag if subprocess is dead (stuck state recovery)
            if not self.client.alive:
                self.is_busy = False
            raise RuntimeError(
                "Agent is busy and not responding. Try /agent → New Session."
            )

        self.is_busy = True
        try:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + PROMPT_OVERALL_TIMEOUT
            async for event in self.client.prompt_stream(text):
                # Check if abort was requested between events
                if self._abort_event.is_set():
                    yield PromptDone(stop_reason="cancelled")
                    break
                yield event
                if isinstance(event, PromptDone):
                    break
                if loop.time() > deadline:
                    log.warning(
                        "Prompt overall timeout (%ds) for chat %s",
                        PROMPT_OVERALL_TIMEOUT,
                        self.chat_id,
                    )
                    yield PromptDone(stop_reason="timeout")
                    break
        finally:
            self.is_busy = False
            self._lock.release()

    def abort(self) -> None:
        """Abort the current in-flight prompt.

        Sets the abort event so prompt_stream breaks out on the next iteration,
        which triggers its finally block to properly release the lock.
        Also cancels the ACP-level request future and drains the event queue
        so the next prompt starts clean.
        """
        # Signal prompt_stream to stop iterating
        self._abort_event.set()
        if isinstance(self.client, ACPClient):
            self.client.abort_prompt()
        log.info("Session %s: prompt aborted", self.chat_id)


async def get_or_create_session(
    chat_id: int | str,
    agent_key: str,
    permission_callback: PermissionCallback | None = None,
    user_data: dict | None = None,
    platform: str = "web",  # kept for caller compat; "web" is the only surface
    lazy_context: bool = False,
    server_name: str | None = None,
) -> AgentSession:
    """Get existing session or create a new one.

    ``chat_id`` is an opaque session key (auth pass will rename it) — it
    carries no user identity (§4.3).
    """
    session = _sessions.get(chat_id)

    # Reuse existing session if same agent and still alive
    if session and session.agent_key == agent_key and session.client.alive:
        return session

    # Destroy old session if exists
    if session:
        await _destroy_session_internal(chat_id)

    # Build dynamic MCP servers (condor MCP only — §9.2)
    mcp_servers: list[dict] = build_mcp_servers_for_session()

    # ACP subprocess models: claude-code, gemini, codex. A Claude model can be
    # pinned via a suffix, e.g. "claude-acp:opus" / "claude-acp:sonnet";
    # ACPClient selects it via session/set_model after handshake (the bridge
    # ignores ANTHROPIC_MODEL). Bare key = agent default.
    command, model_env, model_pref = resolve_acp(agent_key)
    client = ACPClient(
        command=command,
        working_dir=get_project_dir(),
        mcp_servers=mcp_servers,
        permission_callback=permission_callback,
        extra_env=model_env,
        model=model_pref,
    )

    await client.start()

    try:
        # Build initial context (chat brain + tool preload + indexes)
        initial_context = build_initial_context(agent_key=agent_key)

        if initial_context and not lazy_context:
            # Eager: send context now (blocks until agent processes it)
            try:
                await client.prompt(initial_context)
            except Exception:
                log.warning("Failed to send initial context for chat %s", chat_id)
            initial_context = ""  # Already sent

        session = AgentSession(
            chat_id=chat_id,
            agent_key=agent_key,
            client=client,
            server_name=server_name,
            pending_context=initial_context or None,
        )
    except Exception:
        # Something failed after start -- stop client to prevent orphan subprocess
        await client.stop()
        raise

    _sessions[chat_id] = session
    log.info("Created agent session for chat %s: %s", chat_id, agent_key)
    return session


def get_session(chat_id: int | str) -> AgentSession | None:
    """Get existing session for a chat, or None."""
    return _sessions.get(chat_id)


async def destroy_session(chat_id: int | str) -> bool:
    """Destroy session for a chat. Returns True if a session existed."""
    return await _destroy_session_internal(chat_id)


async def _destroy_session_internal(chat_id: int | str) -> bool:
    session = _sessions.pop(chat_id, None)
    if session:
        try:
            await session.client.stop()
        except Exception:
            log.exception("Error stopping agent session for chat %s", chat_id)
        log.info("Destroyed agent session for chat %s", chat_id)
        return True
    return False


async def destroy_all_sessions() -> None:
    """Destroy all active sessions. Called on shutdown."""
    chat_ids = list(_sessions.keys())
    for chat_id in chat_ids:
        await _destroy_session_internal(chat_id)
    log.info("Destroyed all %d agent session(s)", len(chat_ids))


# --- Background health monitor ---


async def start_health_monitor(
    notifier: Callable[[int | str], Awaitable[None]] | None = None,
) -> None:
    """Start periodic background check for dead sessions.

    ``notifier`` is called with the session key after a dead session is
    reaped; the transport decides how to surface it (WS event, nothing).
    """
    global _health_task, _health_notifier
    _health_notifier = notifier
    _health_task = asyncio.create_task(_health_check_loop())
    log.info("Agent health monitor started")


async def stop_health_monitor() -> None:
    """Cancel the health monitor task."""
    global _health_task, _health_notifier
    if _health_task and not _health_task.done():
        _health_task.cancel()
        try:
            await _health_task
        except asyncio.CancelledError:
            pass
    _health_task = None
    _health_notifier = None
    log.info("Agent health monitor stopped")


async def _health_check_loop() -> None:
    """Every 15s, check for dead sessions (including stuck ones with is_busy=True)."""
    try:
        while True:
            await asyncio.sleep(15)
            dead_chats: list[int | str] = []
            for chat_id, session in list(_sessions.items()):
                if not session.client.alive:
                    if session.is_busy:
                        # Force-clear stuck busy flag on dead sessions
                        session.is_busy = False
                        log.warning(
                            "Health monitor: force-cleared is_busy for dead session chat %s",
                            chat_id,
                        )
                    dead_chats.append(chat_id)

            for chat_id in dead_chats:
                log.warning(
                    "Health monitor: dead session for chat %s, cleaning up", chat_id
                )
                await _destroy_session_internal(chat_id)
                if _health_notifier is not None:
                    try:
                        await _health_notifier(chat_id)
                    except Exception:
                        log.warning(
                            "Failed to notify chat %s about dead session", chat_id
                        )
    except asyncio.CancelledError:
        pass
