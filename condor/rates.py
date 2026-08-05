"""Cross-rate resolution over a `{trading_pair: price}` pool.

Port of Hummingbot's `find_rate` so Condor can convert between currencies from a
locally cached ticker pool instead of paying a round trip to the API's
`/market-data/rates` endpoint on every conversion.
"""

from typing import Dict, Iterable, List, Optional

# Symbols the exchanges quote interchangeably with USDT (mirrors the rate oracle's
# `USD_EQUIVALENT_TOKENS`), so USD-denominated values price off USDT markets.
_USD_EQUIVALENT = frozenset({"USD"})


def _normalize(token: str) -> str:
    return "USDT" if token in _USD_EQUIVALENT else token


def merge_price_pool(
    tickers_by_connector: Dict[str, Dict[str, dict]]
) -> Dict[str, float]:
    """Merge per-connector tickers into one `{pair: price}` pool.

    On pairs listed by several exchanges the most liquid one wins — quote volume is
    the only cross-exchange comparable measure, since the same pair implies the same
    quote token.
    """
    prices: Dict[str, float] = {}
    volumes: Dict[str, float] = {}
    for tickers in tickers_by_connector.values():
        if not isinstance(tickers, dict):
            continue
        for pair, ticker in tickers.items():
            if not isinstance(ticker, dict):
                continue
            price = float(ticker.get("price") or 0)
            if price <= 0:
                continue
            volume = float(ticker.get("quote_volume") or 0)
            if pair not in prices or volume > volumes[pair]:
                prices[pair] = price
                volumes[pair] = volume
    return prices


def find_rate(prices: Dict[str, float], pair: str) -> Optional[float]:
    """Resolve `pair` from `prices` via a direct, reverse or bridged path.

    Given {"HBOT-USDT": 100, "AAVE-USDT": 50, "USDT-GBP": 0.75}, USDT-HBOT resolves
    to 1/100, HBOT-AAVE to 100/50 and HBOT-GBP to 100 * 0.75.

    Returns None when no path exists.
    """
    direct = prices.get(pair)
    if direct:
        return direct

    base, _, quote = pair.partition("-")
    if not quote:
        return None
    base = _normalize(base.upper())
    quote = _normalize(quote.upper())
    if base == quote:
        return 1.0

    # Re-check the direct pair after normalizing (e.g. HYPE-USD -> HYPE-USDT).
    normalized = prices.get(f"{base}-{quote}")
    if normalized:
        return normalized

    reverse = prices.get(f"{quote}-{base}")
    if reverse and reverse > 0:
        return 1.0 / reverse

    # Bridge through any pair the base is quoted against.
    base_prefix = f"{base}-"
    for base_pair, proxy_price in prices.items():
        if not base_pair.startswith(base_prefix) or not proxy_price:
            continue
        link_quote = base_pair.partition("-")[2]
        link = prices.get(f"{link_quote}-{quote}")
        if link:
            return proxy_price * link
        common_denom = prices.get(f"{quote}-{link_quote}")
        if common_denom and common_denom > 0:
            return proxy_price / common_denom
    return None


def resolve_rates(
    prices: Dict[str, float], trading_pairs: Iterable[str]
) -> Dict[str, Optional[float]]:
    """Resolve several pairs at once. Unresolvable pairs map to None."""
    return {pair: find_rate(prices, pair) for pair in trading_pairs}


def unresolved(rates: Dict[str, Optional[float]]) -> List[str]:
    """Pairs in `rates` that could not be resolved locally."""
    return [pair for pair, rate in rates.items() if rate is None]
