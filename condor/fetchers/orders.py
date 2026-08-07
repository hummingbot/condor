"""Fetch active orders from Hummingbot API."""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def fetch_active_orders(
    client, limit: str = "5", strict: bool = False, **_kw
) -> List[Dict[str, Any]]:
    """Fetch active orders from a server.

    Args:
        strict: Raise when the request itself fails, instead of reporting an
            empty book. Callers that cache the answer want the distinction: an
            unreachable server is worth retrying, "no active orders" is not.
    """
    try:
        result = await client.trading.get_active_orders(limit=int(limit))
        return result.get("data", [])
    except Exception as e:
        if strict:
            raise
        logger.warning("Error fetching active orders: %s", e)
        return []
