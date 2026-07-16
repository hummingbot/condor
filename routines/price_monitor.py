"""One-shot price threshold check — designed to be scheduled (the scheduler
provides repetition)."""

CATEGORY = "Monitoring"

import logging

import aiohttp
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Public Binance REST — no credentials, works from the scrubbed routine worker.
_TICKER_URLS = {
    "binance": "https://api.binance.com/api/v3/ticker/price",
    "binance_perpetual": "https://fapi.binance.com/fapi/v1/ticker/price",
}
_KLINES_URLS = {
    "binance": "https://api.binance.com/api/v3/klines",
    "binance_perpetual": "https://fapi.binance.com/fapi/v1/klines",
}


class Config(BaseModel):
    """Check a price against alert thresholds once; schedule it for monitoring."""

    connector: str = Field(
        default="binance",
        description="Data source: binance (spot) or binance_perpetual (futures)",
    )
    trading_pair: str = Field(
        default="BTC-USDT", description="Trading pair to check (e.g. BTC-USDT)"
    )
    upper_price: float = Field(
        default=0.0, description="Alert if price is at/above this (0 = disabled)"
    )
    lower_price: float = Field(
        default=0.0, description="Alert if price is at/below this (0 = disabled)"
    )
    move_threshold_pct: float = Field(
        default=1.0,
        description="Alert if |price change| over the lookback exceeds this % (0 = disabled)",
    )
    lookback_min: int = Field(
        default=15, description="Lookback window in minutes for the move check"
    )


def _symbol(trading_pair: str) -> str:
    return trading_pair.replace("-", "").upper()


async def _fetch_json(session: aiohttp.ClientSession, url: str, params: dict) -> object:
    async with session.get(url, params=params) as resp:
        resp.raise_for_status()
        return await resp.json()


async def run(config: Config, context=None) -> str:
    """One-shot check: fetch the current price, compare against thresholds.

    Returns an ``ALERT:``-prefixed summary when any threshold is crossed,
    otherwise an ``OK:`` status line.
    """
    if config.connector not in _TICKER_URLS:
        raise ValueError(
            f"Unsupported connector {config.connector!r} — "
            f"supported: {', '.join(sorted(_TICKER_URLS))}"
        )

    symbol = _symbol(config.trading_pair)
    async with aiohttp.ClientSession() as session:
        ticker = await _fetch_json(
            session, _TICKER_URLS[config.connector], {"symbol": symbol}
        )
        price = float(ticker["price"])

        ref_price = None
        change_pct = None
        if config.move_threshold_pct > 0 and config.lookback_min > 0:
            # First 1m candle of the lookback window: its open is the reference.
            klines = await _fetch_json(
                session,
                _KLINES_URLS[config.connector],
                {
                    "symbol": symbol,
                    "interval": "1m",
                    "limit": min(max(config.lookback_min, 1), 1000),
                },
            )
            if klines:
                ref_price = float(klines[0][1])  # open of the oldest candle
                if ref_price > 0:
                    change_pct = (price - ref_price) / ref_price * 100

    alerts: list[str] = []
    if config.upper_price > 0 and price >= config.upper_price:
        alerts.append(f"price {price:,.6g} >= upper threshold {config.upper_price:,.6g}")
    if config.lower_price > 0 and price <= config.lower_price:
        alerts.append(f"price {price:,.6g} <= lower threshold {config.lower_price:,.6g}")
    if (
        change_pct is not None
        and config.move_threshold_pct > 0
        and abs(change_pct) >= config.move_threshold_pct
    ):
        alerts.append(
            f"moved {change_pct:+.2f}% in {config.lookback_min}m "
            f"(threshold {config.move_threshold_pct}%)"
        )

    move_note = (
        f" | {config.lookback_min}m change: {change_pct:+.2f}%"
        if change_pct is not None
        else ""
    )
    status = f"{config.trading_pair} @ {price:,.6g} ({config.connector}){move_note}"
    if alerts:
        return f"ALERT: {status}\n" + "\n".join(f"- {a}" for a in alerts)
    return f"OK: {status} — no thresholds crossed"
