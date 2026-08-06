"""Fetch trading rules from Hummingbot API."""

import logging
from typing import Any, Dict

from condor.fetchers._identifiers import validate_identifier

logger = logging.getLogger(__name__)


async def fetch_trading_rules(
    client, connector_name: str = "", strict: bool = False, **_kw
) -> Dict[str, Dict[str, Any]]:
    """Fetch trading rules for a connector.

    Args:
        strict: Raise when the request fails, instead of reporting the connector
            as having no rules. Callers that cache the answer want the
            distinction: it is what lets the SDS drop the subscription of a
            connector whose endpoint 404s instead of polling it forever.

    Returns:
        Dict of trading_pair -> rules

    Raises:
        IdentifierError: if ``connector_name`` is not a safe URL path segment.
    """
    # Before the try: the except below turns everything into {}, which would
    # cache a bogus entry instead of surfacing the rejection.
    validate_identifier(connector_name, "connector name")

    try:
        result = await client.connectors.get_trading_rules(
            connector_name=connector_name
        )
        return result if result else {}
    except Exception as e:
        if strict:
            # The SDS logs the failure itself and needs the raise to mark the
            # connector unavailable; re-logging here would only be noise.
            raise
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
