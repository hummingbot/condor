"""Fetch the positions Condor's executors believe they hold (``PositionHold``)."""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def fetch_tracked_positions(
    client,
    controller_id: Optional[str] = None,
    strict: bool = False,
    **_kw,
) -> List[Dict[str, Any]]:
    """Held positions as the executors believe them (``PositionHold``).

    The sibling of :func:`condor.fetchers.positions.fetch_positions`, which reads
    the same question off the exchange. Neither is a substitute for the other:
    this is what Condor filled, that is what the venue holds, and
    :mod:`condor.venue_drift` is where they meet.

    Rows carry ``account_name``, ``connector_name``, ``trading_pair``,
    ``position_side``, ``net_amount_base``, ``buy_breakeven_price``,
    ``controller_id`` and ``executor_ids``. Nothing on this path ever asks the
    venue.

    Args:
        controller_id: Narrow to one controller's holds. The drift check calls
            this **unscoped**: the venue answers for the whole account, so the
            tracked side must too or every sibling controller's position reads
            as an orphan.
        strict: Raise when the request itself fails, instead of reporting the
            book as empty — the same distinction ``fetch_positions`` draws. A
            comparison must never read a failed call as "we hold nothing".
    """
    try:
        result = await client.executors.get_positions_summary(
            controller_id=controller_id or None,
        )
    except Exception as e:
        if strict:
            raise
        logger.error("Error fetching tracked positions: %s", e, exc_info=True)
        return []

    positions = result.get("positions", result) if isinstance(result, dict) else result
    if isinstance(positions, list):
        return [p for p in positions if isinstance(p, dict)]
    return [positions] if isinstance(positions, dict) else []
