"""Fetch market data (prices, candles) from Hummingbot API."""

import logging
from typing import Any, Dict, List, Optional, Tuple

from condor.rates import find_rate, merge_price_pool

logger = logging.getLogger(__name__)


async def fetch_current_price(
    client, connector_name: str, trading_pair: str, **_kw
) -> Optional[float]:
    """Fetch current price for a trading pair."""
    try:
        prices = await client.market_data.get_prices(
            connector_name=connector_name, trading_pairs=trading_pair
        )
        return prices.get("prices", {}).get(trading_pair)
    except Exception as e:
        logger.warning("Error fetching price for %s: %s", trading_pair, e)
        return None


async def fetch_candles(
    client,
    connector_name: str,
    trading_pair: str,
    interval: str = "1m",
    max_records: int = 420,
    **_kw,
) -> Optional[Dict[str, Any]]:
    """Fetch candle data for a trading pair."""
    try:
        candles = await client.market_data.get_candles(
            connector_name=connector_name,
            trading_pair=trading_pair,
            interval=interval,
            max_records=max_records,
        )
        if not candles:
            return None
        data = candles if isinstance(candles, list) else candles.get("data", [])
        if not data:
            return None
        return candles
    except Exception as e:
        logger.error(
            "Error fetching candles for %s: %s", trading_pair, e, exc_info=True
        )
        return None


async def fetch_candle_connectors(client, **_kw) -> List[str]:
    """Fetch available candle connectors."""
    return await client.market_data.get_available_candle_connectors()


async def fetch_rates(
    client,
    trading_pairs: List[str],
    connector_name: Optional[str] = None,
    strict: bool = False,
    **_kw,
) -> Dict[str, Optional[float]]:
    """Resolve cross-rates for `trading_pairs` from the API's ticker pool.

    Replaces the removed `/rate-oracle/rates` endpoint. Rates are resolved via
    direct, reverse or bridged ticker paths; pass `connector_name` to restrict
    resolution to a single exchange, otherwise the merged pool is used.

    Args:
        strict: Raise when the request itself fails, instead of reporting every pair
            as unresolvable. Callers that cache the answer want the distinction: an
            unreachable server is worth retrying, "no such market" is not.

    Returns:
        {"BASE-QUOTE": rate|None} — None when the pair can't be resolved.
    """
    if not trading_pairs:
        return {}

    try:
        result = await client.market_data.get_rates(
            trading_pairs, connector=connector_name or None
        )
    except Exception as e:
        if strict:
            raise
        logger.warning("Error fetching rates for %s: %s", trading_pairs, e)
        return {pair: None for pair in trading_pairs}

    rates = (result or {}).get("rates") or {}
    return {pair: rates.get(pair) for pair in trading_pairs}


# Quote assets already denominated in (approximately) USD, used when the pool has
# no market to price them off.
_USD_QUOTES = frozenset(
    {"USDT", "USDC", "USD", "BUSD", "FDUSD", "TUSD", "DAI", "USDE", "PYUSD"}
)


def _usd_rate(quote: str, prices: Dict[str, float]) -> Optional[float]:
    """USD value of one unit of `quote`, resolved off `prices`.

    Returns None when the quote asset can't be priced (volume stays quote-denominated).
    """
    rate = find_rate(prices, f"{quote}-USDT")
    if rate:
        return rate

    # Quote-only assets (fiat such as JPY or TRY) are never a base, so `find_rate`'s
    # bridge finds no path. Invert a market quoted in it and price that base instead.
    suffix = f"-{quote}"
    for pair, price in prices.items():
        if not price or not pair.endswith(suffix):
            continue
        base_usd = find_rate(prices, f"{pair.partition('-')[0]}-USDT")
        if base_usd:
            return base_usd / price

    return 1.0 if quote in _USD_QUOTES else None


def _normalize_tickers(
    raw: Dict[str, Any], prices: Dict[str, float]
) -> Tuple[Dict[str, Dict[str, Any]], float]:
    """Normalize one connector's raw tickers, adding `usd_volume`.

    The Hummingbot API returns `quote_volume` denominated in the *quote asset*, so
    BTC-quoted pairs aren't comparable with USDT-quoted ones. Each quote asset is
    priced off `prices` — no extra API call.

    Returns:
        ({pair: {price, base_volume, quote_volume, usd_volume}}, latest_timestamp)
    """
    rate_cache: Dict[str, Optional[float]] = {}
    tickers: Dict[str, Dict[str, Any]] = {}
    latest_ts = 0.0

    for pair, t in raw.items():
        if not isinstance(t, dict):
            continue
        parts = pair.split("-")
        quote = parts[-1] if len(parts) > 1 else ""
        if quote not in rate_cache:
            rate_cache[quote] = _usd_rate(quote, prices)
        rate = rate_cache[quote]

        price = float(t.get("price") or 0)
        # Older API versions expose a single `volume` field (quote-denominated)
        # instead of the base/quote split.
        quote_volume = float(t.get("quote_volume") or t.get("volume") or 0)
        base_volume = float(t.get("base_volume") or 0)
        if not base_volume and quote_volume and price:
            base_volume = quote_volume / price

        latest_ts = max(latest_ts, float(t.get("timestamp") or 0))
        tickers[pair] = {
            "price": price,
            "base_volume": base_volume,
            "quote_volume": quote_volume,
            "usd_volume": quote_volume * rate if rate is not None else None,
        }

    return tickers, latest_ts


async def fetch_ticker_pool(client, **_kw) -> Dict[str, Any]:
    """Fetch the API's whole ticker pool in a single call.

    Without a `connectors` filter the endpoint is a plain read of the pool the API
    already refreshes in the background — no upstream exchange requests, so it is
    cheap enough to poll. Condor caches the result (see `ServerDataType.TICKER_POOL`)
    and serves both per-connector tickers and currency conversion from it, instead of
    hitting `/market-data/tickers` and `/market-data/rates` per request.

    Returns:
        {
          "connectors": {connector: {pair: {price, base_volume, quote_volume, usd_volume}}},
          "prices": {pair: price},   # merged across connectors, most liquid wins
          "updated_at": {connector: float|None},
        }
    """
    empty: Dict[str, Any] = {"connectors": {}, "prices": {}, "updated_at": {}}
    try:
        result = await client.market_data.get_tickers()
    except Exception as e:
        error_str = str(e)
        if "404" in error_str or "not found" in error_str.lower():
            logger.debug("Server has no /market-data/tickers endpoint: %s", e)
        else:
            logger.warning("Error fetching ticker pool: %s", e)
        return empty

    raw_by_connector = (result or {}).get("tickers") or {}
    if not isinstance(raw_by_connector, dict):
        return empty

    prices = merge_price_pool(raw_by_connector)
    reported_updates = (result or {}).get("updated_at") or {}

    connectors: Dict[str, Dict[str, Any]] = {}
    updated_at: Dict[str, Optional[float]] = {}
    for connector, raw in raw_by_connector.items():
        if not isinstance(raw, dict):
            continue
        tickers, latest_ts = _normalize_tickers(raw, prices)
        connectors[connector] = tickers
        # `updated_at` is absent on older API versions — fall back to ticker timestamps.
        updated_at[connector] = (
            reported_updates.get(connector)
            if isinstance(reported_updates, dict)
            else None
        ) or (latest_ts or None)

    return {"connectors": connectors, "prices": prices, "updated_at": updated_at}


async def fetch_tickers(client, connector_name: str = "", **_kw) -> Dict[str, Any]:
    """Fetch 24h tickers (price + volumes) for a single connector.

    Fallback for connectors that aren't in the cached pool yet: naming the connector
    makes the API fetch it on demand and enroll it in its background refresh cycle,
    so subsequent pool reads include it. Prefer `fetch_ticker_pool` where possible.

    Returns:
        {"tickers": {pair: {price, base_volume, quote_volume, usd_volume}}, "updated_at": float|None}
    """
    if not connector_name:
        return {"tickers": {}, "updated_at": None}

    try:
        result = await client.market_data.get_tickers(connector_name)
    except Exception as e:
        error_str = str(e)
        if "404" in error_str or "not found" in error_str.lower():
            logger.debug(
                "Server has no /market-data/tickers endpoint (connector=%s): %s",
                connector_name,
                e,
            )
        else:
            logger.warning("Error fetching tickers for %s: %s", connector_name, e)
        return {"tickers": {}, "updated_at": None}

    raw = ((result or {}).get("tickers") or {}).get(connector_name) or {}
    if not isinstance(raw, dict):
        return {"tickers": {}, "updated_at": None}

    prices = {
        pair: float(t.get("price") or 0)
        for pair, t in raw.items()
        if isinstance(t, dict)
    }
    tickers, latest_ts = _normalize_tickers(raw, prices)

    updated_at = (result or {}).get("updated_at") or {}
    return {
        "tickers": tickers,
        # `updated_at` is absent on older API versions — fall back to the ticker timestamps.
        "updated_at": (
            updated_at.get(connector_name) if isinstance(updated_at, dict) else None
        )
        or (latest_ts or None),
    }
