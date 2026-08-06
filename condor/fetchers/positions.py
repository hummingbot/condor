"""Fetch position data from Hummingbot API."""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Cap on how many positions a single walk accumulates (not on iterations:
# the loop's own empty-page and cursor-progress guards end a stalled walk).
MAX_POSITIONS_FETCH = 2000
POSITIONS_PAGE_SIZE = 200


def _next_cursor(result: Any) -> Optional[str]:
    """Extract the pagination cursor from a positions response."""
    if not isinstance(result, dict):
        return None
    cursor = result.get("next_cursor") or result.get("cursor")
    pagination = result.get("pagination")
    if not cursor and isinstance(pagination, dict):
        cursor = pagination.get("next_cursor") or pagination.get("cursor")
    return cursor


async def fetch_positions(
    client,
    connector_name: Optional[str] = None,
    limit: int = MAX_POSITIONS_FETCH,
    strict: bool = False,
    **_kw,
) -> List[Dict[str, Any]]:
    """Fetch open positions, optionally filtered by connector.

    When ``connector_name`` is given it is sent to the API as
    ``connector_names=[connector_name]``, so the filter is applied server-side
    (before any cap). The client-side filter is kept only as a defensive pass
    for servers that ignore the argument, and therefore runs *after* the cap.

    Walks the cursor in ``POSITIONS_PAGE_SIZE`` pages until exhausted or until
    ``limit`` rows are accumulated; reaching that cap truncates the result and
    logs a warning.

    Args:
        strict: Raise when the request itself fails, instead of reporting the
            account as flat. Callers that cache the answer want the distinction:
            an unreachable server is worth retrying, "no open positions" is not.
    """
    try:
        positions: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            remaining = limit - len(positions)
            if remaining <= 0:
                logger.warning(
                    "fetch_positions reached the %s-position safety cap; "
                    "results are truncated",
                    limit,
                )
                break
            page_size = min(POSITIONS_PAGE_SIZE, remaining)
            kwargs: Dict[str, Any] = {"limit": page_size}
            if connector_name:
                kwargs["connector_names"] = [connector_name]
            if cursor:
                kwargs["cursor"] = cursor

            result = await client.trading.get_positions(**kwargs)
            page = result.get("data", []) if isinstance(result, dict) else []
            positions.extend(page)

            next_cursor = _next_cursor(result)
            if not page or not next_cursor or len(page) < page_size:
                break
            if next_cursor == cursor:
                # The API echoed the cursor we sent: the walk is not progressing.
                break
            cursor = next_cursor

        if connector_name and positions:
            positions = [
                p for p in positions if p.get("connector_name") == connector_name
            ]

        return positions

    except Exception as e:
        if strict:
            raise
        logger.error("Error fetching positions: %s", e, exc_info=True)
        return []
