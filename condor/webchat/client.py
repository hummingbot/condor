"""HTTP client for condor.hummingbot.org API."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

SITE_URL = os.getenv("CONDOR_SITE_URL", "https://condor.hummingbot.org")


class SiteClient:
    """Thin async HTTP client for the Condor website API."""

    def __init__(self, token: str):
        self.token = token
        self._client = httpx.AsyncClient(base_url=SITE_URL, timeout=10.0)

    async def close(self):
        await self._client.aclose()

    async def push_message(
        self,
        *,
        agent_id: str | None = None,
        source: str = "tick",
        prompt: str | None = None,
        response: str,
        actions_json: Any = None,
        exchange: str | None = None,
        pair: str | None = None,
        pnl_snapshot: float | None = None,
        is_public: bool = False,
    ) -> str | None:
        """Push a chat message to the site. Returns the created message ID."""
        try:
            r = await self._client.post("/api/condor/push", json={
                "token": self.token,
                "agentId": agent_id,
                "source": source,
                "prompt": prompt,
                "response": response,
                "actionsJson": actions_json,
                "exchange": exchange,
                "pair": pair,
                "pnlSnapshot": pnl_snapshot,
                "isPublic": is_public,
            })
            r.raise_for_status()
            return r.json().get("id")
        except Exception as e:
            log.warning("Failed to push message to site: %s", e)
            return None

    async def push_snapshot(
        self,
        *,
        competition_id: str,
        agent_name: str,
        pnl: float,
        volume: float,
        exposure: float,
        exchange: str | None = None,
        pair: str | None = None,
        trades_count: int = 0,
    ) -> bool:
        """Push a competition snapshot."""
        try:
            r = await self._client.post("/api/condor/snapshot", json={
                "token": self.token,
                "competitionId": competition_id,
                "agentName": agent_name,
                "pnl": pnl,
                "volume": volume,
                "exposure": exposure,
                "exchange": exchange,
                "pair": pair,
                "tradesCount": trades_count,
            })
            r.raise_for_status()
            return True
        except Exception as e:
            log.warning("Failed to push snapshot to site: %s", e)
            return False

    async def publish_agent(
        self,
        *,
        name: str,
        description: str,
        agent_key: str,
        skills: list[str],
        default_config: dict,
    ) -> str | None:
        """Publish an agent to the site directory."""
        try:
            r = await self._client.post("/api/condor/publish-agent", json={
                "token": self.token,
                "name": name,
                "description": description,
                "agentKey": agent_key,
                "skills": skills,
                "defaultConfig": default_config,
            })
            r.raise_for_status()
            return r.json().get("id")
        except Exception as e:
            log.warning("Failed to publish agent to site: %s", e)
            return None

    async def get_pending_messages(self) -> list[dict]:
        """Fetch pending messages from the site (sent by web users)."""
        try:
            r = await self._client.get(f"/api/condor/pending?token={self.token}")
            r.raise_for_status()
            return r.json().get("messages", [])
        except Exception as e:
            log.warning("Failed to fetch pending messages: %s", e)
            return []

    async def mark_done(self, message_id: str, response: str) -> None:
        """Mark a pending message as done and write the response."""
        try:
            await self._client.post("/api/condor/pending/done", json={
                "token": self.token,
                "id": message_id,
                "response": response,
            })
        except Exception as e:
            log.warning("Failed to mark message done: %s", e)
