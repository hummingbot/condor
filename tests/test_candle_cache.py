"""Tests for the bounded candle cache in condor/web/routes/market.py (PERF-060)."""

import pytest

import condor.web.routes.market as market


class _Clock:
    """Stand-in for the `time` module inside condor.web.routes.market."""

    def __init__(self, start=1000.0):
        self.now = start

    def monotonic(self):
        return self.now

    def time(self):
        return 1_700_000_000.0

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture(autouse=True)
def _clean_cache():
    market._candle_cache.clear()
    yield
    market._candle_cache.clear()


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(market, "time", c, raising=True)
    return c


def test_cache_bounded_under_advancing_bucketed_starts(clock):
    """Simulate a chart minting a new bucketed_start key every request."""
    for i in range(500):
        key = ("srv", "binance", "BTC-USDT", "1m", 1000, i * 60, None)
        market._candle_cache_put(key, [i])
        clock.advance(1)
    assert len(market._candle_cache) <= market._CANDLE_CACHE_MAX


def test_cache_bounded_under_burst_at_same_timestamp(clock):
    """Even with no time advancing (nothing expires), the size cap holds."""
    for i in range(500):
        key = ("srv", "binance", "BTC-USDT", "1m", 1000, i * 60, None)
        market._candle_cache_put(key, [i])
    assert len(market._candle_cache) <= market._CANDLE_CACHE_MAX


def test_fresh_entry_still_hits_within_ttl(clock):
    """Repeated identical requests within the TTL keep hitting the cache."""
    key = ("srv", "binance", "BTC-USDT", "1m", 1000, 0, None)
    market._candle_cache_put(key, ["candles"])
    # Another key inserted within the TTL must not evict the fresh entry
    clock.advance(market._CANDLE_CACHE_TTL - 1)
    market._candle_cache_put(("other",), ["x"])
    cached = market._candle_cache.get(key)
    assert cached is not None
    assert cached[1] == ["candles"]


def test_expired_entries_swept_on_write(clock):
    market._candle_cache_put(("old",), ["x"])
    clock.advance(market._CANDLE_CACHE_TTL)
    market._candle_cache_put(("new",), ["y"])
    assert ("old",) not in market._candle_cache
    assert ("new",) in market._candle_cache


def test_entry_is_stamped_at_insert_time_not_before_the_fetch(clock):
    """The stamp is the clock at write time (CORR-584)."""
    clock.advance(45.0)  # a fetch that outran the TTL
    market._candle_cache_put(("k",), ["candles"])
    assert market._candle_cache[("k",)][0] == clock.now
