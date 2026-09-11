"""A slow upstream fetch must still leave a usable cache entry (CORR-584).

GET /market/candles used to stamp the cache with the timestamp captured
*before* the fetch, so an entry was born aged by the whole fetch duration. A
GeckoTerminal pool chart under the shared rate limiter can outrun the 30s TTL,
which made the entry stale the instant it was written and re-fired the upstream
call on the very next request — exactly the path the cache exists to protect.
"""

import asyncio

import pytest

import condor.web.routes.market as market
from condor.web.models import WebUser


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


class _Cm:
    def __init__(self, client):
        self._client = client

    def has_server_access(self, user_id, name):
        return True

    async def get_client(self, name):
        return self._client


class _SlowClient:
    """Counts upstream calls; each one burns `duration` seconds of the clock."""

    def __init__(self, clock, duration):
        self.clock = clock
        self.duration = duration
        self.calls = 0

    @property
    def market_data(self):
        return self

    async def get_historical_candles(self, *a, **kw):
        self.calls += 1
        self.clock.advance(self.duration)
        return [
            {
                "timestamp": 1000.0,
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 10.0,
            }
        ]

    async def get_candles(self, *a, **kw):
        return []


@pytest.fixture(autouse=True)
def _clean_state():
    market._candle_cache.clear()
    market._candle_inflight.clear()
    yield
    market._candle_cache.clear()
    market._candle_inflight.clear()


def _call():
    return market.get_candles(
        "srv",
        connector="binance",
        trading_pair="BTC-USDT",
        interval="1m",
        limit=100,
        start_time=1_700_000_000.0,
        end_time=1_700_003_600.0,
        pool_address=None,
        user=WebUser(id=1, role="user"),
    )


def test_fetch_longer_than_the_ttl_still_caches(monkeypatch):
    clock = _Clock()
    client = _SlowClient(clock, duration=market._CANDLE_CACHE_TTL + 15.0)
    monkeypatch.setattr(market, "time", clock, raising=True)
    monkeypatch.setattr(market, "get_config_manager", lambda: _Cm(client), raising=True)

    async def scenario():
        first = await _call()
        assert len(market._candle_cache) == 1
        # A request arriving immediately afterwards is served from the cache.
        second = await _call()
        return first, second

    first, second = asyncio.run(scenario())
    assert client.calls == 1, "the slow fetch's entry was already stale on write"
    assert second is first
    # The entry is stamped with the clock as of insert time, not request start.
    ((stamp, cached_value),) = market._candle_cache.values()
    assert stamp == clock.now
    assert cached_value is first
