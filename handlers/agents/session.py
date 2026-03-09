"""Agent session lifecycle manager."""

import asyncio
import logging
from dataclasses import dataclass, field

from telegram import Bot

from condor.acp import ACP_COMMANDS, ACPClient, PermissionCallback, PromptDone, UsageUpdate
from handlers.agents._shared import (
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
_sessions: dict[int, "AgentSession"] = {}

# Health monitor state
_health_task: asyncio.Task | None = None
_health_bot: Bot | None = None


@dataclass
class AgentSession:
    chat_id: int
    agent_key: str  # "claude-code", "gemini", "codex"
    client: ACPClient
    is_busy: bool = False
    tokens_used: int = 0
    context_window: int = 200000
    cost_usd: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def prompt_stream(self, text: str):
        """Stream a prompt, managing the busy flag and lock.

        Includes a lock-acquisition timeout (PROMPT_LOCK_TIMEOUT) to avoid
        waiting forever when a previous prompt is stuck, and an overall
        wall-clock timeout (PROMPT_OVERALL_TIMEOUT) to kill runaway prompts.
        """
        # Acquire lock with timeout -- prevents infinite wait when previous prompt is stuck
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=PROMPT_LOCK_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("Lock acquisition timed out for chat %d", self.chat_id)
            # Force-clear busy flag if subprocess is dead (stuck state recovery)
            if not self.client.alive:
                self.is_busy = False
            raise RuntimeError("Agent is busy and not responding. Try /agent → New Session.")

        self.is_busy = True
        try:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + PROMPT_OVERALL_TIMEOUT
            async for event in self.client.prompt_stream(text):
                if isinstance(event, UsageUpdate):
                    self.tokens_used = event.used
                    self.context_window = event.size
                    self.cost_usd = event.cost_usd
                yield event
                if isinstance(event, PromptDone):
                    break
                if loop.time() > deadline:
                    log.warning(
                        "Prompt overall timeout (%ds) for chat %d",
                        PROMPT_OVERALL_TIMEOUT,
                        self.chat_id,
                    )
                    yield PromptDone(stop_reason="timeout")
                    break
        finally:
            self.is_busy = False
            self._lock.release()


async def get_or_create_session(
    chat_id: int,
    agent_key: str,
    permission_callback: PermissionCallback | None = None,
    user_id: int | None = None,
) -> AgentSession:
    """Get existing session or create a new one.

    When user_id is provided, dynamically configures MCP servers using
    the user's Condor server permissions instead of static .mcp.json.
    """
    session = _sessions.get(chat_id)

    # Reuse existing session if same agent and still alive
    if session and session.agent_key == agent_key and session.client.alive:
        return session

    # Destroy old session if exists
    if session:
        await _destroy_session_internal(chat_id)

    # Create new session
    command = ACP_COMMANDS.get(agent_key, ACP_COMMANDS["claude-code"])

    from condor.widget_bridge import get_widget_bridge

    bridge = get_widget_bridge()

    extra_env = {
        "CONDOR_WIDGET_PORT": str(bridge.port),
        "CONDOR_CHAT_ID": str(chat_id),
    }

    # Build dynamic MCP servers from user's Condor permissions
    mcp_servers: list[dict] = []
    if user_id:
        mcp_servers = build_mcp_servers_for_session(user_id, chat_id, bridge.port)

    client = ACPClient(
        command=command,
        working_dir=get_project_dir(),
        mcp_servers=mcp_servers,
        permission_callback=permission_callback,
        extra_env=extra_env,
    )

    await client.start()

    # Send initial context about server and permissions
    if user_id:
        initial_context = build_initial_context(user_id, chat_id)
        if initial_context:
            try:
                await client.prompt(initial_context)
            except Exception:
                log.warning("Failed to send initial context for chat %d", chat_id)

    session = AgentSession(
        chat_id=chat_id,
        agent_key=agent_key,
        client=client,
    )
    _sessions[chat_id] = session
    log.info("Created agent session for chat %d: %s", chat_id, agent_key)
    return session


def get_session(chat_id: int) -> AgentSession | None:
    """Get existing session for a chat, or None."""
    return _sessions.get(chat_id)


async def destroy_session(chat_id: int) -> bool:
    """Destroy session for a chat. Returns True if a session existed."""
    return await _destroy_session_internal(chat_id)


async def _destroy_session_internal(chat_id: int) -> bool:
    session = _sessions.pop(chat_id, None)
    if session:
        # Cancel any pending widget futures for this chat
        from condor.widget_bridge import get_widget_bridge

        get_widget_bridge().cancel_for_chat(chat_id)

        try:
            await session.client.stop()
        except Exception:
            log.exception("Error stopping agent session for chat %d", chat_id)
        log.info("Destroyed agent session for chat %d", chat_id)
        return True
    return False


async def destroy_all_sessions() -> None:
    """Destroy all active sessions. Called on bot shutdown."""
    chat_ids = list(_sessions.keys())
    for chat_id in chat_ids:
        await _destroy_session_internal(chat_id)
    log.info("Destroyed all %d agent session(s)", len(chat_ids))


# --- Background health monitor ---


async def start_health_monitor(bot: Bot) -> None:
    """Start periodic background check for dead sessions."""
    global _health_task, _health_bot
    _health_bot = bot
    _health_task = asyncio.create_task(_health_check_loop())
    log.info("Agent health monitor started")


async def stop_health_monitor() -> None:
    """Cancel the health monitor task."""
    global _health_task, _health_bot
    if _health_task and not _health_task.done():
        _health_task.cancel()
        try:
            await _health_task
        except asyncio.CancelledError:
            pass
    _health_task = None
    _health_bot = None
    log.info("Agent health monitor stopped")


async def _health_check_loop() -> None:
    """Every 15s, check for dead sessions (including stuck ones with is_busy=True)."""
    try:
        while True:
            await asyncio.sleep(15)
            dead_chats: list[int] = []
            for chat_id, session in list(_sessions.items()):
                if not session.client.alive:
                    if session.is_busy:
                        # Force-clear stuck busy flag on dead sessions
                        session.is_busy = False
                        log.warning(
                            "Health monitor: force-cleared is_busy for dead session chat %d",
                            chat_id,
                        )
                    dead_chats.append(chat_id)

            for chat_id in dead_chats:
                log.warning("Health monitor: dead session for chat %d, cleaning up", chat_id)
                await _destroy_session_internal(chat_id)
                if _health_bot:
                    try:
                        await _health_bot.send_message(
                            chat_id=chat_id,
                            text="Agent session ended unexpectedly. Send a message to start a new session.",
                        )
                    except Exception:
                        log.warning("Failed to notify chat %d about dead session", chat_id)
    except asyncio.CancelledError:
        pass
