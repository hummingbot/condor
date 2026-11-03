"""Tests for CORR-188: ticker/price fetchers expose a strict mode for the SDS.

``fetch_current_price``, ``fetch_tickers`` and ``fetch_ticker_pool`` are cached
reads in the ServerDataService, whose invariant is that a failed fetch must
*raise* so ``_do_fetch_and_cache`` keeps the last good value, records the error
and backs off — instead of caching an empty shape as a fresh success. These
tests pin the ``strict=True`` re-raise, the 404 "endpoint absent" carve-out
(a real answer, never a failure), and the lenient default for direct callers.
"""

import asyncio

import pytest

from condor.fetchers.market_data import (
    fetch_current_price,
    fetch_ticker_pool,
    fetch_tickers,
)

EMPTY_POOL = {"connectors": {}, "prices": {}, "updated_at": {}}
EMPTY_TICKERS = {"tickers": {}, "updated_at": None}


class FailingClient:
    """Client whose market-data endpoints raise a given exception."""

    def __init__(self, exc: Exception):
        self.market_data = self._MarketData(exc)

    class _MarketData:
        def __init__(self, exc):
            self._exc = exc

        async def get_tickers(self, connector_name=None):
            raise self._exc

        async def get_prices(self, connector_name="", trading_pairs=""):
            raise self._exc


def test_ticker_pool_strict_reraises_transient_errors():
    client = FailingClient(RuntimeError("500 upstream exploded"))
    with pytest.raises(RuntimeError):
        asyncio.run(fetch_ticker_pool(client, strict=True))


def test_ticker_pool_404_is_a_real_empty_answer_even_in_strict_mode():
    client = FailingClient(RuntimeError("404 Not Found"))
    assert asyncio.run(fetch_ticker_pool(client, strict=True)) == EMPTY_POOL


def test_ticker_pool_lenient_default_degrades_to_empty():
    client = FailingClient(RuntimeError("500 upstream exploded"))
    assert asyncio.run(fetch_ticker_pool(client)) == EMPTY_POOL


def test_tickers_strict_reraises_transient_errors():
    client = FailingClient(TimeoutError("timed out"))
    with pytest.raises(TimeoutError):
        asyncio.run(fetch_tickers(client, "binance", strict=True))


def test_tickers_404_is_a_real_empty_answer_even_in_strict_mode():
    client = FailingClient(RuntimeError("404 Not Found"))
    assert asyncio.run(fetch_tickers(client, "binance", strict=True)) == EMPTY_TICKERS


def test_tickers_lenient_default_degrades_to_empty():
    client = FailingClient(RuntimeError("500 upstream exploded"))
    assert asyncio.run(fetch_tickers(client, "binance")) == EMPTY_TICKERS


def test_current_price_strict_reraises():
    client = FailingClient(RuntimeError("connection refused"))
    with pytest.raises(RuntimeError):
        asyncio.run(fetch_current_price(client, "binance", "BTC-USDT", strict=True))


def test_current_price_lenient_default_degrades_to_none():
    client = FailingClient(RuntimeError("connection refused"))
    assert asyncio.run(fetch_current_price(client, "binance", "BTC-USDT")) is None


# ---------------------------------------------------------------------------
# ARCH-320: a payload that came back but cannot be read is an empty answer,
# never a raise. These two guards used to live only in a private copy of the
# unwrap (`executor_create._current_price`), which made the shared helper the
# strictly worse one to reach for.
# ---------------------------------------------------------------------------


class _PriceClient:
    """Client whose ``get_prices`` returns a canned payload."""

    def __init__(self, payload):
        self.market_data = self._MarketData(payload)

    class _MarketData:
        def __init__(self, payload):
            self._payload = payload

        async def get_prices(self, connector_name="", trading_pairs=""):
            return self._payload


@pytest.mark.parametrize(
    "payload",
    [
        {"prices": None},
        {"prices": []},
        {"prices": "BTC-USDT=60000"},
        {},
        None,
        "not a payload at all",
    ],
)
def test_current_price_reads_an_unreadable_payload_as_none(payload):
    client = _PriceClient(payload)
    assert asyncio.run(fetch_current_price(client, "binance", "BTC-USDT")) is None


def test_an_unreadable_payload_is_an_answer_not_a_failure_in_strict_mode():
    """Strict re-raises a failed *request*; a payload that arrived is not one."""
    client = _PriceClient({"prices": ["BTC-USDT", 60000.0]})
    assert (
        asyncio.run(fetch_current_price(client, "binance", "BTC-USDT", strict=True))
        is None
    )


def test_a_lone_entry_answers_a_one_pair_request_under_the_venues_own_key():
    """A venue that normalises the pair its own way still answers the question."""
    client = _PriceClient({"prices": {"SOL/USDC": 210.5}})
    assert asyncio.run(fetch_current_price(client, "jupiter", "SOL-USDC")) == 210.5


def test_two_entries_under_other_keys_stay_ambiguous_and_yield_none():
    client = _PriceClient({"prices": {"SOL/USDC": 210.5, "SOL/USDT": 210.4}})
    assert asyncio.run(fetch_current_price(client, "jupiter", "SOL-USDC")) is None


def test_the_requested_key_still_wins_over_the_lone_entry_fallback():
    client = _PriceClient({"prices": {"BTC-USDT": 60000.0}})
    assert asyncio.run(fetch_current_price(client, "binance", "BTC-USDT")) == 60000.0
