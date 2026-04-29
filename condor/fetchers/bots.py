"""Fetch bot data from Hummingbot API."""

import logging

logger = logging.getLogger(__name__)


async def fetch_bots_status(client, **_kw):
    """Fetch active bots status."""
    return await client.bot_orchestration.get_active_bots_status()


async def fetch_bot_runs(client, **_kw):
    """Fetch bot run history."""
    return await client.bot_orchestration.get_bot_runs()
