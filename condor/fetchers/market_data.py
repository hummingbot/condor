"""Fetch market data (prices, candles) from Hummingbot API."""

import logging
from typing import Any, Dict, List, Optional

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
    **_kw,
) -> Dict[str, Optional[float]]:
    """Resolve cross-rates for `trading_pairs` from the API's ticker pool.

    Replaces the removed `/rate-oracle/rates` endpoint. Rates are resolved via
    direct, reverse or bridged ticker paths; pass `connector_name` to restrict
    resolution to a single exchange, otherwise the merged pool is used.

    Returns:
        {"BASE-QUOTE": rate|None} — None when the pair can't be resolved.
    """
    if not trading_pairs:
        return {}

    body: Dict[str, Any] = {"trading_pairs": trading_pairs}
    if connector_name:
        body["connector"] = connector_name

    try:
        # No client-lib method for this endpoint yet — call it directly.
        result = await client.market_data._post("/market-data/rates", json=body)
    except Exception as e:
        logger.warning("Error fetching rates for %s: %s", trading_pairs, e)
        return {pair: None for pair in trading_pairs}

    rates = (result or {}).get("rates") or {}
    return {pair: rates.get(pair) for pair in trading_pairs}


# Quote assets already denominated in (approximately) USD.
_USD_QUOTES = frozenset(
    {"USDT", "USDC", "USD", "BUSD", "FDUSD", "TUSD", "DAI", "USDE", "PYUSD"}
)


def _usd_rate(quote: str, prices: Dict[str, float]) -> Optional[float]:
    """USD value of one unit of `quote`, using the connector's own tickers.

    Returns None when the quote asset can't be priced (volume stays quote-denominated).
    """
    if quote in _USD_QUOTES:
        return 1.0
    for usd_quote in ("USDT", "USDC", "USD"):
        price = prices.get(f"{quote}-{usd_quote}")
        if price:
            return price
    # Fiat quotes are usually listed the other way round (USDT-TRY, USDT-BRL).
    for usd_quote in ("USDT", "USDC", "USD"):
        price = prices.get(f"{usd_quote}-{quote}")
        if price:
            return 1 / price
    return None


async def fetch_tickers(client, connector_name: str = "", **_kw) -> Dict[str, Any]:
    """Fetch 24h tickers (price + volumes) for a connector.

    The Hummingbot API returns `quote_volume` denominated in the *quote asset*, so
    BTC-quoted pairs aren't comparable with USDT-quoted ones. We add `usd_volume`
    by pricing each quote asset off the same ticker payload — no extra API call.

    Returns:
        {"tickers": {pair: {price, base_volume, quote_volume, usd_volume}}, "updated_at": float|None}
    """
    if not connector_name:
        return {"tickers": {}, "updated_at": None}

    try:
        # No client-lib method for this endpoint yet — call it directly.
        result = await client.market_data._get(
            "/market-data/tickers",
            params={"connectors": connector_name, "refresh": "false"},
        )
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

    updated_at = (result or {}).get("updated_at") or {}
    return {
        "tickers": tickers,
        # `updated_at` is absent on older API versions — fall back to the ticker timestamps.
        "updated_at": (
            updated_at.get(connector_name) if isinstance(updated_at, dict) else None
        )
        or (latest_ts or None),
    }
