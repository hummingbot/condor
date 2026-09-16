"""
Market data operations business logic.

Two functions, and neither renders a series into a table (ARCH-308). ``get_prices``
answers a single quote in text because a quote read once and not computed on is
answered completely by its text. ``get_market_data`` answers candles in **rows** —
a list of ``{timestamp, open, high, low, close, volume}`` dicts, the same shape
``client.market_data.*`` returns inside ``run_code`` — so nothing read here has to
be fetched a second time to be averaged or compared.

Its reason to exist is dry-run (CORR-625). A snippet and a routine both hold the
unrestricted API client, so a rehearsal auto-approves neither (SEC-616, SEC-626),
which left a dry run with no structured candle read at all. This tool takes
parameters, not code: there is nothing in it to write with, in any mode.
"""

from datetime import datetime
from typing import Any

from condor.fetchers.market_data import (
    fetch_candle_connectors,
    fetch_historical_candles,
)
from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.formatters import format_prices_as_table

#: The tool's actions. Every one of them reads; the set is deliberately closed,
#: and ``condor.runtime.danger`` pins it against this literal so an action added
#: here without a thought about what it touches cannot quietly become callable
#: in a dry run.
MARKET_DATA_ACTIONS = ("candles", "historical_candles", "connectors")

#: Rows per candle request. The cap is context, not throughput: a tick that asks
#: for 5000 1m candles spends its whole window reading them back.
MAX_CANDLE_RECORDS = 1000


async def get_prices(
    client: Any, connector_name: str, trading_pairs: list[str]
) -> dict[str, Any]:
    """
    Get latest prices for trading pairs.

    Args:
        client: Hummingbot API client
        connector_name: Exchange connector name
        trading_pairs: List of trading pairs

    Returns:
        Dictionary containing prices data and formatted table
    """
    prices = await client.market_data.get_prices(
        connector_name=connector_name, trading_pairs=trading_pairs
    )

    # Format prices as table
    prices_table = format_prices_as_table(prices)

    timestamp = prices.get("timestamp", 0)
    time_str = (
        datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        if timestamp
        else "N/A"
    )

    return {
        "prices": prices,
        "prices_table": prices_table,
        "connector_name": connector_name,
        "timestamp": time_str,
    }


async def get_market_data(
    client: Any,
    action: str,
    connector_name: str = "",
    trading_pair: str = "",
    interval: str = "1m",
    max_records: int = 200,
    start_time: int | None = None,
    end_time: int | None = None,
) -> dict[str, Any]:
    """Read candles as rows, or list the connectors that serve them.

    Delegates to the fetchers every other candle consumer in Condor already uses
    (``condor.fetchers.market_data``), so this tool inherits their payload-shape
    handling and their fallback ladder rather than restating it: the two candle
    actions are the same fetcher with and without a time range.

    Args:
        client: Hummingbot API client
        action: One of :data:`MARKET_DATA_ACTIONS`
        connector_name: Exchange connector name (candle actions)
        trading_pair: Pair to read (candle actions)
        interval: Candle interval, e.g. "1m", "1h", "4h", "1d"
        max_records: How many rows to return, capped at :data:`MAX_CANDLE_RECORDS`
        start_time / end_time: Unix epoch seconds, for "historical_candles"

    Returns:
        A dict whose candle rows are floats, not a rendered table.
    """
    if action == "connectors":
        return {
            "action": action,
            "connectors": await fetch_candle_connectors(client),
        }

    if action not in ("candles", "historical_candles"):
        raise ToolError(
            f"unknown action {action!r} — expected one of "
            f"{', '.join(MARKET_DATA_ACTIONS)}"
        )

    if not connector_name:
        raise ToolError(f"connector_name is required for the {action} action")
    if not trading_pair:
        raise ToolError(f"trading_pair is required for the {action} action")

    limit = max(1, min(int(max_records), MAX_CANDLE_RECORDS))

    if action == "historical_candles":
        if start_time is None:
            raise ToolError(
                "start_time is required for the historical_candles action — use "
                "action='candles' for the most recent window instead"
            )
        rows = await fetch_historical_candles(
            client,
            connector_name,
            trading_pair,
            interval,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            fallback_on_error=True,
        )
    else:
        # No start_time skips the ranged call entirely and the `limit` fallback
        # answers, which is exactly "the most recent `limit` candles".
        rows = await fetch_historical_candles(
            client,
            connector_name,
            trading_pair,
            interval,
            start_time=None,
            limit=limit,
        )

    return {
        "action": action,
        "connector_name": connector_name,
        "trading_pair": trading_pair,
        "interval": interval,
        "count": len(rows),
        "candles": rows,
    }
