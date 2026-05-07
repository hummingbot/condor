"""Fetch server status from Hummingbot API."""

import logging

logger = logging.getLogger(__name__)


async def fetch_server_status(client, **_kw) -> dict:
    """Check if a server is online by listing accounts (lightweight call)."""
    try:
        await client.accounts.list_accounts()
        return {"status": "online"}
    except Exception as e:
        return {"status": "error", "message": str(e)[:80]}
