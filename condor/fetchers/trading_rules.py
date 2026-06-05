"""Fetch trading rules from Hummingbot API."""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def fetch_trading_rules(
    client, connector_name: str = "", **_kw
) -> Dict[str, Dict[str, Any]]:
    """Fetch trading rules for a connector.

    Returns:
        Dict of trading_pair -> rules
    """
    try:
        result = await client.connectors.get_trading_rules(
            connector_name=connector_name
        )
        return result if result else {}
    except Exception as e:
        error_str = str(e)
        if "404" in error_str or "401" in error_str or "not found" in error_str.lower():
            logger.debug(
                "Connector '%s' not available for trading rules: %s",
                connector_name, e,
            )
        else:
            logger.error(
                "Error fetching trading rules for %s: %s",
                connector_name, e, exc_info=True,
            )
        return {}
