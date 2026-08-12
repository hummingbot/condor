"""Tests for PERF-118: the ticker pool resolves each USD quote rate once.

``fetch_ticker_pool`` normalizes every connector against the *same* merged price
pool, but ``_normalize_tickers`` used to allocate a fresh rate memo per call, so
``_usd_rate`` — O(pool) on a miss — was recomputed once per (connector, quote).
These tests pin both halves of the fix: the memo is now shared across the
connector loop, and sharing it changes nothing about the output.
"""

import asyncio

from condor.fetchers import market_data
from condor.fetchers.market_data import fetch_ticker_pool, fetch_tickers

# Two connectors quoting against the same three assets: USDT (trivially 1.0),
# JPY (quote-only fiat, priced by inverting a market quoted in it) and NOSUCH
# (unpriceable — the expensive case, a full pool scan that always fails).
POOL_PAYLOAD = {
    "tickers": {
        "binance": {
            "BTC-USDT": {
                "price": 64_000.0,
                "base_volume": 10.0,
                "quote_volume": 640_000.0,
                "timestamp": 100.0,
            },
            "BTC-JPY": {
                "price": 9_600_000.0,
                "base_volume": 2.0,
                "quote_volume": 19_200_000.0,
                "timestamp": 120.0,
            },
            "SOL-NOSUCH": {"price": 5.0, "quote_volume": 100.0, "timestamp": 50.0},
        },
        "kucoin": {
            "ETH-USDT": {
                "price": 3_000.0,
                "base_volume": 5.0,
                "quote_volume": 15_000.0,
                "timestamp": 300.0,
            },
            # Older API shape: quote-denominated `volume`, no base volume.
            "ETH-JPY": {"price": 450_000.0, "volume": 900_000.0, "timestamp": 310.0},
            "XRP-NOSUCH": {"price": 2.0, "quote_volume": 40.0, "timestamp": 20.0},
        },
    },
    # Only one connector reports a refresh time; the other falls back to timestamps.
    "updated_at": {"binance": 111.0},
}

# JPY has no market as a base, so it prices off the inverted BTC-JPY market.
_JPY_USD = 64_000.0 / 9_600_000.0


class FakeClient:
    """Minimal client recording how the ticker endpoint was called."""

    def __init__(self, payload):
        self.market_data = self._MarketData(payload)

    class _MarketData:
        def __init__(self, payload):
            self._payload = payload
            self.calls = []

        async def get_tickers(self, connector_name=None):
            self.calls.append(connector_name)
            return self._payload


def test_ticker_pool_output_is_unchanged_by_the_shared_memo():
    """Golden dict: sharing the rate cache across connectors is value-neutral."""
    result = asyncio.run(fetch_ticker_pool(FakeClient(POOL_PAYLOAD)))

    assert result == {
        "connectors": {
            "binance": {
                "BTC-USDT": {
                    "price": 64_000.0,
                    "base_volume": 10.0,
                    "quote_volume": 640_000.0,
                    "usd_volume": 640_000.0 * 1.0,
                },
                "BTC-JPY": {
                    "price": 9_600_000.0,
                    "base_volume": 2.0,
                    "quote_volume": 19_200_000.0,
                    "usd_volume": 19_200_000.0 * _JPY_USD,
                },
                "SOL-NOSUCH": {
                    "price": 5.0,
                    "base_volume": 100.0 / 5.0,
                    "quote_volume": 100.0,
                    # Unpriceable quote: volume stays quote-denominated, not faked.
                    "usd_volume": None,
                },
            },
            "kucoin": {
                "ETH-USDT": {
                    "price": 3_000.0,
                    "base_volume": 5.0,
                    "quote_volume": 15_000.0,
                    "usd_volume": 15_000.0 * 1.0,
                },
                "ETH-JPY": {
                    "price": 450_000.0,
                    "base_volume": 900_000.0 / 450_000.0,
                    "quote_volume": 900_000.0,
                    "usd_volume": 900_000.0 * _JPY_USD,
                },
                "XRP-NOSUCH": {
                    "price": 2.0,
                    "base_volume": 40.0 / 2.0,
                    "quote_volume": 40.0,
                    "usd_volume": None,
                },
            },
        },
        "prices": {
            "BTC-USDT": 64_000.0,
            "BTC-JPY": 9_600_000.0,
            "SOL-NOSUCH": 5.0,
            "ETH-USDT": 3_000.0,
            "ETH-JPY": 450_000.0,
            "XRP-NOSUCH": 2.0,
        },
        "updated_at": {"binance": 111.0, "kucoin": 310.0},
    }


def test_usd_rate_is_resolved_once_per_quote_across_the_whole_pool(monkeypatch):
    """Each distinct quote costs one `_usd_rate` call, not one per connector."""
    real_usd_rate = market_data._usd_rate
    calls = []

    def counting_usd_rate(quote, prices):
        calls.append(quote)
        return real_usd_rate(quote, prices)

    monkeypatch.setattr(market_data, "_usd_rate", counting_usd_rate)
    asyncio.run(fetch_ticker_pool(FakeClient(POOL_PAYLOAD)))

    # Both connectors quote against USDT, JPY and NOSUCH: 3 calls, not 6.
    assert sorted(calls) == ["JPY", "NOSUCH", "USDT"]


def test_single_connector_fetch_prices_off_its_own_local_pool(monkeypatch):
    """`fetch_tickers` must not share a memo keyed off a different price pool."""
    real_usd_rate = market_data._usd_rate
    seen_pools = []

    def recording_usd_rate(quote, prices):
        seen_pools.append(prices)
        return real_usd_rate(quote, prices)

    monkeypatch.setattr(market_data, "_usd_rate", recording_usd_rate)
    client = FakeClient(POOL_PAYLOAD)
    result = asyncio.run(fetch_tickers(client, "binance"))

    # The connector is named, so the API enrolls it in its refresh cycle.
    assert client.market_data.calls == ["binance"]
    assert result["tickers"]["BTC-USDT"]["usd_volume"] == 640_000.0
    assert result["tickers"]["BTC-JPY"]["usd_volume"] == 19_200_000.0 * _JPY_USD
    assert result["tickers"]["SOL-NOSUCH"]["usd_volume"] is None
    assert result["updated_at"] == 111.0

    # Every rate came from binance's own prices — never the merged pool.
    assert seen_pools and all(
        pool == {"BTC-USDT": 64_000.0, "BTC-JPY": 9_600_000.0, "SOL-NOSUCH": 5.0}
        for pool in seen_pools
    )
