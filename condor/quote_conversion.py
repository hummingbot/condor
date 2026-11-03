"""Restate a run's quote-denominated money figures in USDT.

PnL, fees and volume come out of an archived run denominated in its market's
*quote* currency. Most of this history is BRL-quoted, so rendering those figures
behind a ``$`` overstated them by the whole BRL/USD rate -- a BTC-BRL run
reading "+$395.41" had actually made about $75.

Rates come from :func:`condor.market_rates.get_rates`, which answers from the
cached ticker pool and only falls back to the API for pairs the pool cannot
bridge. Stablecoin quotes short-circuit to 1.0 off the same list the ticker
fetchers use, so a quote with no market at all still prices correctly.

Following the convention in ``condor/web/routes/executors.py``, a quote with no
path to USD is passed through at face value and *reported* via a ``converted``
flag rather than silently treated as a dollar.

Conversion applies one spot rate per quote rather than a per-timestamp
historical curve: every non-stable quote in this history is fiat (BRL, EUR),
where drift across a run is far below the precision these numbers are read at.
A crypto-quoted run would want the historical treatment, and
:func:`resolve_usd_rates` is the seam where that would go.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from condor.dex_candles import split_pair
from condor.fetchers.market_data import USD_QUOTES

logger = logging.getLogger(__name__)


class QuoteRates(NamedTuple):
    """USD rate per quote currency, plus whether every quote resolved."""

    rates: dict[str, float]
    converted: bool

    def for_pair(self, trading_pair: str) -> float:
        """Multiplier for a pair's quote; 1.0 when it could not be resolved."""
        return self.rates.get(quote_of(trading_pair), 1.0)


def quote_of(trading_pair: str) -> str:
    """The quote currency of a trading pair, uppercased."""
    return split_pair(trading_pair or "")[1].strip().upper()


def quotes_in(trading_pairs: Any) -> set[str]:
    """Every distinct quote currency across an iterable of trading pairs."""
    return {q for q in (quote_of(p) for p in trading_pairs or []) if q}


async def resolve_usd_rates(server: str, quotes: set[str]) -> QuoteRates:
    """Map each quote currency to its value in USDT.

    One lookup per quote asset, not per trade or executor. Degrades to face
    value on an unreachable pool rather than raising: a run is still worth
    showing in its own quote, but the caller must be able to say so.
    """
    wanted = {q.strip().upper() for q in quotes if q and q.strip()}
    if not wanted:
        return QuoteRates({}, True)

    rates: dict[str, float] = {q: 1.0 for q in wanted if q in USD_QUOTES}
    missing = sorted(wanted - set(rates))
    if not missing:
        return QuoteRates(rates, True)

    from condor.market_rates import get_rates

    try:
        resolved = await get_rates(server, [f"{q}-USDT" for q in missing])
    except Exception as e:
        logger.warning("Rates unavailable for %s on %s: %s", missing, server, e)
        return QuoteRates(rates, False)

    converted = True
    for quote in missing:
        rate = resolved.get(f"{quote}-USDT")
        if not rate or rate <= 0:
            logger.warning("No path from %s to USD on %s", quote, server)
            converted = False
            continue
        rates[quote] = float(rate)

    return QuoteRates(rates, converted)


def convert_trades_to_usd(trades: list[dict], rates: QuoteRates) -> int:
    """Restate trade prices and quote-denominated fees in USD, in place.

    Converting ``price`` rather than the derived totals means the existing
    position walk yields USD PnL *and* USD volume with no further changes --
    ``amount`` is in the base asset, which is already currency-agnostic.

    Returns how many trades were actually restated. Trades whose quote had no
    rate are left untouched, which is what makes the caller's ``converted``
    flag meaningful.
    """
    changed = 0
    for trade in trades:
        rate = rates.for_pair(str(trade.get("trading_pair", "") or ""))
        if rate == 1.0:
            continue
        for field in ("price", "trade_fee_in_quote"):
            value = trade.get(field)
            if value is None:
                continue
            try:
                trade[field] = float(value) * rate
            except (TypeError, ValueError):
                continue
        changed += 1
    return changed
