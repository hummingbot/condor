"""Agent session lifecycle manager."""

import asyncio
import logging
from dataclasses import dataclass, field

from condor.acp import ACP_COMMANDS, ACP_PROTOCOL, ACPClient, PermissionCallback
from handlers.agents._shared import (
    build_initial_context,
    build_mcp_servers_for_session,
    get_project_dir,
)

log = logging.getLogger(__name__)

# Module-level session storage (not persisted -- subprocesses can't survive restarts)
_sessions: dict[int, "AgentSession"] = {}


@dataclass
class AgentSession:
    chat_id: int
    agent_key: str  # "claude-code", "gemini", "codex"
    client: ACPClient
    is_busy: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def prompt_stream(self, text: str):
        """Stream a prompt, managing the busy flag and lock."""
        async with self._lock:
            self.is_busy = True
            try:
                async for event in self.client.prompt_stream(text):
                    yield event
            finally:
                self.is_busy = False


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
    protocol = ACP_PROTOCOL.get(agent_key, "claude")

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
        protocol=protocol,
        mcp_servers=mcp_servers,
        permission_callback=permission_callback,
        extra_env=extra_env,
    )

    await client.start()

    # Send initial context about server and permissions
    if user_id and mcp_servers:
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
