"""Fetch active orders from Hummingbot API."""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def fetch_active_orders(client, limit: str = "5", **_kw) -> List[Dict[str, Any]]:
    """Fetch active orders from a server."""
    try:
        result = await client.trading.get_active_orders(limit=int(limit))
        return result.get("data", [])
    except Exception as e:
        logger.warning("Error fetching active orders: %s", e)
        return []
