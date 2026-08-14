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
from typing import Any, NamedTuple

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


class _ParsedTrade(NamedTuple):
    """One trade row after the field extraction every mode shares."""

    pair: str
    amount: float
    price: float
    trade_type: str
    position: str
    fee: float
    timestamp: Any


class _TradeOutcome(NamedTuple):
    """What an accounting mode decided about one trade.

    ``running_delta`` is added to the running total unconditionally.
    ``pair_delta`` is credited to ``pnl_by_pair`` only when it is not ``None``,
    so a mode that touches the running total *without* attributing the amount
    to a pair (average cost does exactly that for the fee on an unmatched SELL)
    does not create a spurious ``pnl_by_pair`` entry.
    """

    running_delta: float = 0.0
    pair_delta: float | None = None


_NO_CHANGE = _TradeOutcome()


class _AverageCostAccounting:
    """Average cost basis inventory tracking, for spot / market making (NIL).

    BUY adds to inventory at its price; SELL realizes against the weighted
    average cost of the inventory on hand, clamped to that inventory. A SELL
    with no inventory (a short) is not modelled: only its fee is charged, and
    only to the running total.
    """

    def __init__(self) -> None:
        # inventory = {amount: float, total_cost: float}
        self.inventory: dict[str, dict[str, float]] = {}
        self.buy_count = 0
        self.sell_count = 0
        self.realized_trades = 0

    def __call__(self, trade: _ParsedTrade) -> _TradeOutcome:
        # Initialize inventory for this pair if needed
        if trade.pair not in self.inventory:
            self.inventory[trade.pair] = {"amount": 0.0, "total_cost": 0.0}

        inv = self.inventory[trade.pair]

        if trade.trade_type == "BUY":
            self.buy_count += 1
            # Add to inventory at this price
            inv["amount"] += trade.amount
            inv["total_cost"] += trade.amount * trade.price
            return _NO_CHANGE

        if trade.trade_type == "SELL":
            self.sell_count += 1
            # Realize PnL if we have inventory
            if inv["amount"] > 0:
                self.realized_trades += 1
                # Calculate average cost of inventory
                avg_cost = inv["total_cost"] / inv["amount"] if inv["amount"] > 0 else 0

                # Determine how much we can actually sell from inventory
                sell_amount = min(trade.amount, inv["amount"])

                # PnL = (sell_price - avg_cost) * amount - fee
                pnl = (trade.price - avg_cost) * sell_amount - trade.fee

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

                return _TradeOutcome(running_delta=pnl, pair_delta=pnl)

            # Short selling (no inventory) - track as negative PnL for now
            # This means we're selling something we don't have (going short)
            # For simplicity, just count fees. Note the fee hits the running
            # total but is deliberately NOT attributed to pnl_by_pair.
            return _TradeOutcome(running_delta=-trade.fee)

        return _NO_CHANGE

    def summary(self, trade_count: int, running_pnl: float) -> str:
        return (
            f"PnL calculation (avg cost): {trade_count} trades, {self.buy_count} BUY, "
            f"{self.sell_count} SELL, {self.realized_trades} realized, "
            f"total_pnl=${running_pnl:.4f}"
        )


class _OpenClosePositionAccounting:
    """OPEN/CLOSE position tracking, for perpetual futures.

    OPEN trades establish or add to a position (long on BUY, short on SELL);
    CLOSE trades realize PnL against the average entry price. Unlike average
    cost, a CLOSE realizes its full amount even when that exceeds the position,
    and a CLOSE with no position is a complete no-op - not even its fee is
    charged to the running total.
    """

    def __init__(self) -> None:
        # position = {amount: float, total_cost: float, direction: int (1=long, -1=short)}
        self.positions: dict[str, dict[str, Any]] = {}
        self.open_count = 0
        self.close_count = 0
        self.close_with_position = 0

    def __call__(self, trade: _ParsedTrade) -> _TradeOutcome:
        if trade.position == "OPEN":
            self.open_count += 1
            # Opening a new position or adding to existing
            if trade.pair not in self.positions:
                self.positions[trade.pair] = {
                    "amount": 0,
                    "total_cost": 0,
                    "direction": 0,
                }

            pos = self.positions[trade.pair]
            pos["amount"] += trade.amount
            pos["total_cost"] += trade.price * trade.amount
            # 1 = long (BUY), -1 = short (SELL). Recorded for inspection only;
            # the close side reads trade_type, not this field.
            pos["direction"] = 1 if trade.trade_type == "BUY" else -1
            return _NO_CHANGE

        if trade.position == "CLOSE":
            self.close_count += 1
            # Closing a position - realize PnL
            pos = self.positions.get(trade.pair)

            if pos and pos["amount"] > 0:
                self.close_with_position += 1
                # Calculate average entry price
                avg_entry = pos["total_cost"] / pos["amount"]

                if trade.trade_type == "SELL":
                    # Closing long: PnL = (exit - entry) * amount
                    pnl = (trade.price - avg_entry) * trade.amount
                else:  # BUY
                    # Closing short: PnL = (entry - exit) * amount
                    pnl = (avg_entry - trade.price) * trade.amount

                # Subtract fee from PnL
                pnl -= trade.fee

                # Update position
                if trade.amount >= pos["amount"]:
                    # Fully closed
                    del self.positions[trade.pair]
                else:
                    # Partially closed
                    close_ratio = trade.amount / pos["amount"]
                    pos["amount"] -= trade.amount
                    pos["total_cost"] -= pos["total_cost"] * close_ratio

                return _TradeOutcome(running_delta=pnl, pair_delta=pnl)

            # CLOSE with nothing open: no PnL and, unlike average cost mode,
            # no fee charged to the running total either.
            return _NO_CHANGE

        return _NO_CHANGE

    def summary(self, trade_count: int, running_pnl: float) -> str:
        return (
            f"PnL calculation (open/close): {trade_count} trades, {self.open_count} OPEN, "
            f"{self.close_count} CLOSE, {self.close_with_position} CLOSE with matching "
            f"position, total_pnl=${running_pnl:.4f}"
        )


def _walk_trades(trades: list[dict[str, Any]], accounting) -> dict[str, Any]:
    """Run the per-trade frame both PnL modes share.

    Sorts by timestamp, extracts the fields, accumulates fees and volume,
    delegates the accounting decision to ``accounting``, and assembles the
    cumulative series and the result dict. The accounting object owns all
    mode-specific state and arithmetic; this function owns none of it.
    """
    pnl_by_pair: dict[str, float] = defaultdict(float)
    cumulative_pnl: list[dict[str, Any]] = []
    running_pnl = 0.0
    total_fees = 0.0
    total_volume = 0.0

    # Sort trades by timestamp
    sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", 0))

    for trade in sorted_trades:
        parsed = _ParsedTrade(
            pair=trade.get("trading_pair", "Unknown"),
            amount=float(trade.get("amount", 0)),
            price=float(trade.get("price", 0)),
            trade_type=trade.get("trade_type", "").upper(),  # BUY or SELL
            position=trade.get("position", "").upper(),  # OPEN, CLOSE or NIL
            fee=float(trade.get("trade_fee_in_quote", 0)),
            timestamp=trade.get("timestamp", 0),
        )

        total_fees += parsed.fee
        total_volume += parsed.amount * parsed.price

        # Parse timestamp for cumulative chart
        ts = parse_timestamp(parsed.timestamp)

        outcome = accounting(parsed)
        if outcome.pair_delta is not None:
            pnl_by_pair[parsed.pair] += outcome.pair_delta
        running_pnl += outcome.running_delta

        # Record cumulative PnL point for charting
        if ts:
            cumulative_pnl.append(
                {
                    "timestamp": ts,
                    "pnl": running_pnl,
                    "pair": parsed.pair,
                }
            )

    logger.info(accounting.summary(len(trades), running_pnl))

    return {
        "total_pnl": running_pnl,
        "total_fees": total_fees,
        "pnl_by_pair": dict(pnl_by_pair),
        "cumulative_pnl": cumulative_pnl,
        "total_volume": total_volume,
    }


def _calculate_pnl_average_cost(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate PnL using average cost basis for spot/market making trades."""
    return _walk_trades(trades, _AverageCostAccounting())


def _calculate_pnl_open_close(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate PnL using OPEN/CLOSE position tracking for perpetual futures."""
    return _walk_trades(trades, _OpenClosePositionAccounting())
