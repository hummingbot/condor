"""Realized-PnL reconstruction from archived-bot trade rows.

Pure computation over trade dicts as returned by the Hummingbot API's archived
database endpoints — no plotting, no Telegram, no client. It lives here rather
than in ``handlers/`` because routines and the web routes need it too, and a
routine published to ``agents/_shared/routines`` must not reach into the
Telegram UI package to get it.

``handlers.bots.archived_chart`` re-exports :func:`calculate_pnl_from_trades`
and :func:`parse_timestamp`, so existing handler imports keep working.
"""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def parse_timestamp(ts) -> datetime | None:
    """Parse a trade timestamp from any of the shapes the API returns."""
    if ts is None:
        return None

    try:
        # Handle millisecond timestamp (integer or float)
        if isinstance(ts, (int, float)):
            # If timestamp > 1e12, it's milliseconds
            if ts > 1e12:
                ts = ts / 1000
            return datetime.fromtimestamp(ts)

        if isinstance(ts, datetime):
            return ts

        if hasattr(ts, "to_pydatetime"):  # pandas Timestamp
            return ts.to_pydatetime()

        if isinstance(ts, str) and ts:
            # Try parsing as ISO format
            if "T" in ts:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            else:
                return datetime.fromisoformat(ts)
    except Exception as e:
        logger.debug(f"Failed to parse timestamp {ts}: {e}")

    return None


def calculate_pnl_from_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Calculate realized PnL from a list of trades using position tracking.

    Supports two modes:
    1. Perpetual futures: Uses OPEN/CLOSE position tracking
    2. Spot/Market Making (NIL positions): Uses average cost basis inventory tracking

    Args:
        trades: List of trade dicts with timestamp, trading_pair, trade_type,
                position, price, amount, trade_fee_in_quote

    Returns:
        Dict with:
        - total_pnl: Total realized PnL
        - total_fees: Total fees paid
        - pnl_by_pair: Dict mapping trading_pair to PnL
        - cumulative_pnl: List of (timestamp, pnl) for charting
        - total_volume: Total traded volume in quote
    """
    if not trades:
        return {
            "total_pnl": 0,
            "total_fees": 0,
            "pnl_by_pair": {},
            "cumulative_pnl": [],
            "total_volume": 0,
        }

    # Detect if this is OPEN/CLOSE mode or NIL mode (market making)
    position_types = set(t.get("position", "").upper() for t in trades)
    has_open_close = "OPEN" in position_types or "CLOSE" in position_types
    is_nil_mode = "NIL" in position_types and not has_open_close

    if is_nil_mode:
        return _calculate_pnl_average_cost(trades)
    else:
        return _calculate_pnl_open_close(trades)


def _calculate_pnl_average_cost(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Calculate PnL using average cost basis for spot/market making trades.

    This handles NIL position trades where:
    - BUY adds to inventory at that price
    - SELL realizes PnL based on weighted average cost of inventory
    """
    # Track inventory per trading pair using average cost
    # inventory = {amount: float, total_cost: float}
    inventory: dict[str, dict[str, float]] = {}

    pnl_by_pair: dict[str, float] = defaultdict(float)
    cumulative_pnl: list[dict[str, Any]] = []
    running_pnl = 0.0
    total_fees = 0.0
    total_volume = 0.0

    # Debug counters
    buy_count = 0
    sell_count = 0
    realized_trades = 0

    # Sort trades by timestamp
    sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", 0))

    for trade in sorted_trades:
        pair = trade.get("trading_pair", "Unknown")
        amount = float(trade.get("amount", 0))
        price = float(trade.get("price", 0))
        trade_type = trade.get("trade_type", "").upper()
        fee = float(trade.get("trade_fee_in_quote", 0))
        timestamp = trade.get("timestamp", 0)

        total_fees += fee
        total_volume += amount * price

        # Parse timestamp for cumulative chart
        ts = parse_timestamp(timestamp)

        # Initialize inventory for this pair if needed
        if pair not in inventory:
            inventory[pair] = {"amount": 0.0, "total_cost": 0.0}

        inv = inventory[pair]

        if trade_type == "BUY":
            buy_count += 1
            # Add to inventory at this price
            inv["amount"] += amount
            inv["total_cost"] += amount * price

        elif trade_type == "SELL":
            sell_count += 1
            # Realize PnL if we have inventory
            if inv["amount"] > 0:
                realized_trades += 1
                # Calculate average cost of inventory
                avg_cost = inv["total_cost"] / inv["amount"] if inv["amount"] > 0 else 0

                # Determine how much we can actually sell from inventory
                sell_amount = min(amount, inv["amount"])

                # PnL = (sell_price - avg_cost) * amount - fee
                pnl = (price - avg_cost) * sell_amount - fee

                pnl_by_pair[pair] += pnl
                running_pnl += pnl

                # Reduce inventory
                if sell_amount >= inv["amount"]:
                    # Fully depleted
                    inv["amount"] = 0.0
                    inv["total_cost"] = 0.0
                else:
                    # Partially depleted - reduce proportionally
                    ratio = sell_amount / inv["amount"]
                    inv["amount"] -= sell_amount
                    inv["total_cost"] -= inv["total_cost"] * ratio
            else:
                # Short selling (no inventory) - track as negative PnL for now
                # This means we're selling something we don't have (going short)
                # For simplicity, just count fees
                running_pnl -= fee

        # Record cumulative PnL point for charting
        if ts:
            cumulative_pnl.append(
                {
                    "timestamp": ts,
                    "pnl": running_pnl,
                    "pair": pair,
                }
            )

    logger.info(
        f"PnL calculation (avg cost): {len(trades)} trades, {buy_count} BUY, {sell_count} SELL, "
        f"{realized_trades} realized, total_pnl=${running_pnl:.4f}"
    )

    return {
        "total_pnl": running_pnl,
        "total_fees": total_fees,
        "pnl_by_pair": dict(pnl_by_pair),
        "cumulative_pnl": cumulative_pnl,
        "total_volume": total_volume,
    }


def _calculate_pnl_open_close(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Calculate PnL using OPEN/CLOSE position tracking for perpetual futures.

    For perpetual futures:
    - OPEN trades establish positions (long or short)
    - CLOSE trades realize PnL
    """
    # Track positions per trading pair
    # position = {amount: float, total_cost: float, direction: int (1=long, -1=short)}
    positions: dict[str, dict[str, Any]] = {}

    pnl_by_pair: dict[str, float] = defaultdict(float)
    cumulative_pnl: list[dict[str, Any]] = []
    running_pnl = 0.0
    total_fees = 0.0
    total_volume = 0.0

    # Debug counters
    open_count = 0
    close_count = 0
    close_with_position = 0

    # Sort trades by timestamp
    sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", 0))

    for trade in sorted_trades:
        pair = trade.get("trading_pair", "Unknown")
        amount = float(trade.get("amount", 0))
        price = float(trade.get("price", 0))
        trade_type = trade.get("trade_type", "").upper()  # BUY or SELL
        position_action = trade.get("position", "").upper()  # OPEN or CLOSE
        fee = float(trade.get("trade_fee_in_quote", 0))
        timestamp = trade.get("timestamp", 0)

        total_fees += fee
        total_volume += amount * price

        # Parse timestamp for cumulative chart
        ts = parse_timestamp(timestamp)

        if position_action == "OPEN":
            open_count += 1
            # Opening a new position or adding to existing
            if pair not in positions:
                positions[pair] = {"amount": 0, "total_cost": 0, "direction": 0}

            pos = positions[pair]

            if trade_type == "BUY":
                # Opening/adding to long position
                pos["amount"] += amount
                pos["total_cost"] += price * amount
                pos["direction"] = 1
            else:  # SELL
                # Opening/adding to short position
                pos["amount"] += amount
                pos["total_cost"] += price * amount
                pos["direction"] = -1

        elif position_action == "CLOSE":
            close_count += 1
            # Closing a position - realize PnL
            pos = positions.get(pair)

            if pos and pos["amount"] > 0:
                close_with_position += 1
                # Calculate average entry price
                avg_entry = pos["total_cost"] / pos["amount"]

                if trade_type == "SELL":
                    # Closing long: PnL = (exit - entry) * amount
                    pnl = (price - avg_entry) * amount
                else:  # BUY
                    # Closing short: PnL = (entry - exit) * amount
                    pnl = (avg_entry - price) * amount

                # Subtract fee from PnL
                pnl -= fee

                pnl_by_pair[pair] += pnl
                running_pnl += pnl

                # Update position
                if amount >= pos["amount"]:
                    # Fully closed
                    del positions[pair]
                else:
                    # Partially closed
                    close_ratio = amount / pos["amount"]
                    pos["amount"] -= amount
                    pos["total_cost"] -= pos["total_cost"] * close_ratio

        # Record cumulative PnL point for charting
        if ts:
            cumulative_pnl.append(
                {
                    "timestamp": ts,
                    "pnl": running_pnl,
                    "pair": pair,
                }
            )

    logger.info(
        f"PnL calculation (open/close): {len(trades)} trades, {open_count} OPEN, {close_count} CLOSE, "
        f"{close_with_position} CLOSE with matching position, total_pnl=${running_pnl:.4f}"
    )

    return {
        "total_pnl": running_pnl,
        "total_fees": total_fees,
        "pnl_by_pair": dict(pnl_by_pair),
        "cumulative_pnl": cumulative_pnl,
        "total_volume": total_volume,
    }
