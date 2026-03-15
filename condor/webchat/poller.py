"""WebChatPoller -- polls site for pending messages and processes via ACP."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from condor.acp.client import ACP_COMMANDS, ACPClient

log = logging.getLogger(__name__)

# Module-level registry of running pollers
_pollers: dict[int, "WebChatPoller"] = {}


def get_poller(user_id: int) -> "WebChatPoller | None":
    return _pollers.get(user_id)


class WebChatPoller:
    """Polls condor.hummingbot.org for pending web chat messages and
    processes them via an ACP session, then writes the response back."""

    def __init__(self, user_id: int, chat_id: int, token: str, agent_key: str = "claude-code"):
        self.user_id = user_id
        self.chat_id = chat_id
        self.token = token
        self.agent_key = agent_key
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        _pollers[self.user_id] = self
        log.info("WebChatPoller started for user %d", self.user_id)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        _pollers.pop(self.user_id, None)
        log.info("WebChatPoller stopped for user %d", self.user_id)

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    async def _loop(self) -> None:
        from condor.webchat.client import SiteClient
        client = SiteClient(self.token)
        try:
            while self._running:
                try:
                    messages = await client.get_pending_messages()
                    for msg in messages:
                        await self._process(client, msg)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning("WebChatPoller error: %s", e)
                await asyncio.sleep(2)
        finally:
            await client.close()

    async def _process(self, client: Any, msg: dict) -> None:
        """Run the message through an ACP session and push the response."""
        from handlers.agents._shared import (
            build_mcp_servers_for_session,
            get_project_dir,
        )

        message_id = msg["id"]
        text = msg["message"]
        log.info("WebChatPoller: processing message %s", message_id)

        agent_cmd = ACP_COMMANDS.get(self.agent_key, ACP_COMMANDS["claude-code"])
        mcp_servers = build_mcp_servers_for_session(self.user_id, self.chat_id, 0)

        acp = ACPClient(
            command=agent_cmd,
            working_dir=get_project_dir(),
            mcp_servers=mcp_servers,
        )

        response = "(no response)"
        await acp.start()
        try:
            response = await asyncio.wait_for(acp.prompt(text), timeout=120)
        except asyncio.TimeoutError:
            response = "(timed out)"
        except Exception as e:
            response = f"(error: {e})"
        finally:
            await acp.stop()

        await client.mark_done(message_id, response)
        log.info("WebChatPoller: completed message %s", message_id)
