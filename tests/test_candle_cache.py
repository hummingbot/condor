"""Tests for the bounded candle cache in condor/web/routes/market.py (PERF-060)."""

import pytest

import condor.web.routes.market as market


@pytest.fixture(autouse=True)
def _clean_cache():
    market._candle_cache.clear()
    yield
    market._candle_cache.clear()


def test_cache_bounded_under_advancing_bucketed_starts():
    """Simulate a chart minting a new bucketed_start key every request."""
    now = 1000.0
    for i in range(500):
        key = ("srv", "binance", "BTC-USDT", "1m", 1000, i * 60, None)
        market._candle_cache_put(key, [i], now + i)
    assert len(market._candle_cache) <= market._CANDLE_CACHE_MAX


def test_cache_bounded_under_burst_at_same_timestamp():
    """Even with no time advancing (nothing expires), the size cap holds."""
    now = 1000.0
    for i in range(500):
        key = ("srv", "binance", "BTC-USDT", "1m", 1000, i * 60, None)
        market._candle_cache_put(key, [i], now)
    assert len(market._candle_cache) <= market._CANDLE_CACHE_MAX


def test_fresh_entry_still_hits_within_ttl():
    """Repeated identical requests within the TTL keep hitting the cache."""
    key = ("srv", "binance", "BTC-USDT", "1m", 1000, 0, None)
    market._candle_cache_put(key, ["candles"], 100.0)
    # Another key inserted within the TTL must not evict the fresh entry
    market._candle_cache_put(("other",), ["x"], 100.0 + market._CANDLE_CACHE_TTL - 1)
    cached = market._candle_cache.get(key)
    assert cached is not None
    assert cached[1] == ["candles"]


def test_expired_entries_swept_on_write():
    market._candle_cache_put(("old",), ["x"], 100.0)
    market._candle_cache_put(("new",), ["y"], 100.0 + market._CANDLE_CACHE_TTL)
    assert ("old",) not in market._candle_cache
    assert ("new",) in market._candle_cache
