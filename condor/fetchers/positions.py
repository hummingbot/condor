"""Fetch position data from Hummingbot API."""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def fetch_positions(
    client, connector_name: Optional[str] = None, **_kw
) -> List[Dict[str, Any]]:
    """Fetch open positions, optionally filtered by connector."""
    try:
        result = await client.trading.get_positions(limit=100)
        positions = result.get("data", [])

        if connector_name and positions:
            positions = [
                p for p in positions if p.get("connector_name") == connector_name
            ]

        return positions

    except Exception as e:
        logger.error("Error fetching positions: %s", e, exc_info=True)
        return []
