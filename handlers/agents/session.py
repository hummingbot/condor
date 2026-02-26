"""Agent session lifecycle manager."""

import asyncio
import logging
from dataclasses import dataclass, field

from condor.acp import ACPClient, ACP_COMMANDS, ACP_PROTOCOL, PermissionCallback
from handlers.agents._shared import get_project_dir

log = logging.getLogger(__name__)

# Module-level session storage (not persisted -- subprocesses can't survive restarts)
_sessions: dict[int, "AgentSession"] = {}


@dataclass
class AgentSession:
    chat_id: int
    agent_key: str           # "claude-code", "gemini", "codex"
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
) -> AgentSession:
    """Get existing session or create a new one.

    The agent subprocess auto-discovers stdio MCP servers from .mcp.json
    in the working directory, so we don't pass them explicitly.
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

    client = ACPClient(
        command=command,
        working_dir=get_project_dir(),
        protocol=protocol,
        permission_callback=permission_callback,
    )

    await client.start()

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
