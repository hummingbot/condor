"""Fetch active orders from Hummingbot API."""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def fetch_active_orders(
    client,
    limit: str = "5",
    strict: bool = False,
    connector_names: Optional[List[str]] = None,
    trading_pairs: Optional[List[str]] = None,
    **_kw,
) -> List[Dict[str, Any]]:
    """Fetch active orders from a server.

    Args:
        strict: Raise when the request itself fails, instead of reporting an
            empty book. Callers that cache the answer want the distinction: an
            unreachable server is worth retrying, "no active orders" is not.
        connector_names: Narrow the read to these connectors, server-side.
        trading_pairs: Narrow the read to these pairs, server-side.

    The filters matter beyond cost: the backend sorts every account's in-flight
    orders by client order id and returns the first ``limit`` of them, so a
    caller after one market's orders on a busy server can have them paged out
    entirely and read the empty page as an empty book.
    """
    try:
        narrowed = {}
        if connector_names:
            narrowed["connector_names"] = list(connector_names)
        if trading_pairs:
            narrowed["trading_pairs"] = list(trading_pairs)
        result = await client.trading.get_active_orders(limit=int(limit), **narrowed)
        return result.get("data", [])
    except Exception as e:
        if strict:
            raise
        logger.warning("Error fetching active orders: %s", e)
        return []
