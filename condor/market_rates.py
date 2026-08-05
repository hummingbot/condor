"""Currency conversion and ticker reads served from Condor's cached ticker pool.

The ticker pool (`ServerDataType.TICKER_POOL`) is polled once per server and holds
every connector's tickers plus a merged price map, so conversions resolve in-process
instead of round-tripping to the API's `/market-data/rates` on each call. The API is
only touched when the local pool can't answer — e.g. pairs priced by externally
pushed (Gateway/DEX) prices that never appear in tickers, or a connector that hasn't
joined the API's refresh cycle yet.
"""

import logging
from typing import Any, Dict, List, Optional

from condor.rates import resolve_rates, unresolved

logger = logging.getLogger(__name__)


async def get_price_pool(
    server: str, connector: Optional[str] = None
) -> Dict[str, float]:
    """Merged `{pair: price}` map for a server, or one connector's slice of it.

    Degrades to an empty map rather than raising: a cold or unreachable pool must
    leave callers on the API fallback, not fail the whole conversion.
    """
    from condor.server_data_service import ServerDataType, get_server_data_service

    try:
        pool = await get_server_data_service().get_or_fetch(
            server, ServerDataType.TICKER_POOL
        )
    except Exception as e:
        logger.warning("Ticker pool unavailable for %s: %s", server, e)
        return {}

    if not isinstance(pool, dict):
        return {}
    if not connector:
        return pool.get("prices") or {}

    tickers = (pool.get("connectors") or {}).get(connector) or {}
    return {
        pair: float(t.get("price") or 0)
        for pair, t in tickers.items()
        if isinstance(t, dict) and t.get("price")
    }


async def get_rates(
    server: str, trading_pairs: List[str], connector: Optional[str] = None
) -> Dict[str, Optional[float]]:
    """Resolve cross-rates for `trading_pairs`, cache-first.

    Pairs the cached pool can't resolve fall back to the API's rate endpoint, which
    also knows externally pushed prices.

    Raises when that fallback request fails, so the caller can retry: clients cache
    the response, and a null rate is indistinguishable from "this pair has no market".
    """
    if not trading_pairs:
        return {}

    prices = await get_price_pool(server, connector)
    rates = resolve_rates(prices, trading_pairs)

    missing = unresolved(rates)
    if not missing:
        return rates

    logger.debug(
        "Rates not resolvable from the cached pool for %s: %s — asking the API",
        server,
        missing,
    )
    from condor.fetchers.market_data import fetch_rates
    from config_manager import get_config_manager

    client = await get_config_manager().get_client(server)
    remote = await fetch_rates(client, missing, connector_name=connector, strict=True)

    rates.update({pair: rate for pair, rate in remote.items() if rate is not None})
    return rates


async def get_connector_tickers(server: str, connector: str) -> Dict[str, Any]:
    """24h tickers for one connector, cache-first.

    Returns `{"tickers": {pair: {...}}, "updated_at": float|None}`. Connectors absent
    from the pool are fetched by name, which also enrolls them in the API's background
    refresh cycle so later pool reads include them.
    """
    from condor.server_data_service import ServerDataType, get_server_data_service

    sds = get_server_data_service()
    pool = await sds.get_or_fetch(server, ServerDataType.TICKER_POOL)
    if isinstance(pool, dict):
        tickers = (pool.get("connectors") or {}).get(connector)
        if tickers:
            return {
                "tickers": tickers,
                "updated_at": (pool.get("updated_at") or {}).get(connector),
            }

    result = await sds.get_or_fetch(
        server, ServerDataType.TICKERS, connector_name=connector
    )
    return result if isinstance(result, dict) else {"tickers": {}, "updated_at": None}
