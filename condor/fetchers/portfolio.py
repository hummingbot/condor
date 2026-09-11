"""Fetch portfolio / balance data from Hummingbot API."""

import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from condor.fetchers.connectors import fetch_available_cex_connectors

logger = logging.getLogger(__name__)

# Selectable history windows: range key -> (lookback seconds, candle interval).
# Lives here rather than in the REST route because both the route and the WS
# manager (which subscribes one SDS key per range) need it, and neither layer
# may import the other.
PORTFOLIO_HISTORY_RANGES: Dict[str, Tuple[int, str]] = {
    "1D": (86400, "5m"),
    "1W": (604800, "1h"),
    "1M": (2592000, "4h"),
    "3M": (7776000, "1d"),
}

# Hyperliquid stable/quote symbols (perp margin reports "USD", spot holds "USDC").
HL_STABLES = frozenset({"USD", "USDC"})

# Reported for the perp connector whose duplicated collateral was dropped, so a
# surface that can render per-connector annotations (the dashboard) can explain
# the missing USDC instead of looking like it lost money.
UNIFIED_ACCOUNT_NOTE = (
    "Unified account — USDC balance is reflected in the hyperliquid (spot) balance."
)

# The poll cadence is not declared here: each range is polled at its own TTL,
# which ``ServerDataType.PORTFOLIO_HISTORY``'s defaults already state (see
# ``DataTypeDefaults.interval_for``). A second table here would only be one
# more thing that has to agree with that one.


def _balance_value(item: Dict[str, Any]) -> float:
    """USD value of one raw balance row, tolerating both payload spellings."""
    try:
        return float(item.get("value", item.get("usd_value", 0)) or 0)
    except (TypeError, ValueError):
        return 0.0


def _stable_value(balances: List[Any]) -> float:
    """Total USD held in Hyperliquid's shared stable collateral within one connector."""
    return sum(
        _balance_value(item)
        for item in balances
        if isinstance(item, dict)
        and item.get("token", item.get("asset", "")) in HL_STABLES
    )


def dedupe_unified_accounts(
    state: Any,
) -> Tuple[Any, Set[Tuple[str, str]]]:
    """Avoid double-counting Hyperliquid balances for unified / portfolio-margin accounts.

    In unified or portfolio-margin mode the SAME USDC collateral is reported by BOTH the spot
    (``hyperliquid``) and perp (``hyperliquid_perpetual``) clearinghouse states, which inflates the
    portfolio. Hyperliquid exposes no account-mode flag, so detect the unified case by the near-equal
    shared stable balance and drop the perp duplicate — Hyperliquid's own guidance is to use the spot
    clearinghouse state as the unified account balance. No-op in standard mode, where the spot and
    perp balances are separate and differ.

    Operates on the raw ``{account: {connector: [balances]}}`` payload so every surface that reads
    ``portfolio.get_state()`` — the REST route, the Telegram overview, the MCP overview — applies the
    same rule. The detection is per account, since two accounts on one server can be in different
    modes.

    Never mutates ``state``: callers hand in the SDS-cached object. Returns the (possibly copied)
    payload plus the set of ``(account, connector)`` pairs whose collateral was dropped, for surfaces
    that annotate the connector (see ``UNIFIED_ACCOUNT_NOTE``).
    """
    deduped: Set[Tuple[str, str]] = set()
    if not isinstance(state, dict):
        return state, deduped

    result = state
    for account_name, account_data in state.items():
        if not isinstance(account_data, dict):
            continue
        spot = account_data.get("hyperliquid")
        perp = account_data.get("hyperliquid_perpetual")
        if not isinstance(spot, list) or not isinstance(perp, list):
            continue

        spot_stable = _stable_value(spot)
        perp_stable = _stable_value(perp)
        if spot_stable <= 0 or perp_stable <= 0:
            continue

        # Unified when the perp stable collateral matches the spot stable within
        # ~1% (one shared pool).
        if abs(perp_stable - spot_stable) > max(0.01, 0.01 * spot_stable):
            continue

        if result is state:
            # Copy-on-write, one level deep: the account dicts get replaced, the
            # balance rows themselves are only ever read.
            result = {
                name: dict(data) if isinstance(data, dict) else data
                for name, data in state.items()
            }
        # Drop the duplicated stable collateral from the perp side; keep any
        # non-stable (perp-only) items.
        result[account_name]["hyperliquid_perpetual"] = [
            item
            for item in perp
            if not isinstance(item, dict)
            or item.get("token", item.get("asset", "")) not in HL_STABLES
        ]
        deduped.add((account_name, "hyperliquid_perpetual"))

    return result, deduped


async def fetch_portfolio(client, **_kw) -> Any:
    """Fetch full portfolio state from a server."""
    return await client.portfolio.get_state()


async def fetch_portfolio_history(client, range_key: str = "1D", **_kw) -> Any:
    """Fetch the raw portfolio history snapshots for one range window."""
    range_seconds, interval = PORTFOLIO_HISTORY_RANGES[range_key]
    start_time = int(time.time()) - range_seconds
    return await client.portfolio.get_history(
        start_time=start_time, interval=interval, limit=500
    )


async def fetch_portfolio_refreshed(client, **_kw) -> Any:
    """Fetch portfolio state with refresh=True to force exchange re-fetch."""
    return await client.portfolio.get_state(refresh=True)


async def fetch_cex_balances(
    client, account_name: str, refresh: bool = False
) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch balances for all CEX connectors on an account.

    Returns:
        Dict of connector_name -> list of balances

    Raises:
        IdentifierError: if ``account_name`` is not a safe URL path segment.
    """
    # Outside the try: this validates ``account_name`` and the except below
    # turns everything into {}, which would cache a bogus entry instead of
    # surfacing the rejection.
    cex = await fetch_available_cex_connectors(client, account_name)
    if not cex:
        return {}

    try:
        portfolio_state = await client.portfolio.get_state(
            account_names=[account_name],
            connector_names=cex,
            refresh=refresh,
        )
        account_data = portfolio_state.get(account_name, {})
        return {k: v for k, v in account_data.items() if v}

    except Exception as e:
        logger.error("Error fetching CEX balances: %s", e, exc_info=True)
        return {}
