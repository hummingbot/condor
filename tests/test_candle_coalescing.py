"""In-flight coalescing for GET /market/candles (PERF-137).

Concurrent identical chart requests (a grid of executor panels on the same pair
and window) must share ONE upstream fetch instead of firing N.
"""

import asyncio

import pytest

import condor.web.routes.market as market
from condor.web.models import WebUser


@pytest.fixture(autouse=True)
def _clean_state():
    market._candle_cache.clear()
    market._candle_inflight.clear()
    yield
    market._candle_cache.clear()
    market._candle_inflight.clear()


class _Cm:
    """Config manager double: grants access and hands back a fake client."""

    def __init__(self, client):
        self._client = client

    def has_server_access(self, user_id, name):
        return True

    async def get_client(self, name):
        return self._client


class _CountingClient:
    """Counts historical-candle calls and holds them open until released."""

    def __init__(self, gate=None, fail=False):
        self.calls = 0
        self._gate = gate
        self._fail = fail

    @property
    def market_data(self):
        return self

    async def get_historical_candles(self, *a, **kw):
        self.calls += 1
        if self._gate is not None:
            await self._gate
        if self._fail:
            raise RuntimeError("upstream down")
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
        # The fallback leg of the ladder. `fetch_historical_candles` is called
        # with fallback_on_error=True, so a fetch only fails outright when this
        # leg fails too.
        if self._fail:
            raise RuntimeError("upstream down")
        return []


def _install(monkeypatch, client):
    monkeypatch.setattr(
        "condor.web.routes.market.get_config_manager", lambda: _Cm(client), raising=True
    )


def _call(**overrides):
    params = dict(
        connector="binance",
        trading_pair="BTC-USDT",
        interval="1m",
        limit=100,
        start_time=1_700_000_000.0,
        end_time=1_700_003_600.0,
        pool_address=None,
        user=WebUser(id=1, role="user"),
    )
    params.update(overrides)
    return market.get_candles("srv", **params)


def test_concurrent_identical_requests_share_one_upstream_fetch(monkeypatch):
    async def scenario():
        gate = asyncio.get_running_loop().create_future()
        client = _CountingClient(gate=gate)
        _install(monkeypatch, client)

        tasks = [asyncio.ensure_future(_call()) for _ in range(5)]
        # Let every request reach the (gated) upstream call before releasing it.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        gate.set_result(None)
        results = await asyncio.gather(*tasks)
        return client.calls, results

    calls, results = asyncio.run(scenario())
    assert calls == 1
    assert all(r == results[0] for r in results)
    assert len(results[0]) == 1


def test_requests_with_different_keys_are_not_coalesced(monkeypatch):
    async def scenario():
        gate = asyncio.get_running_loop().create_future()
        client = _CountingClient(gate=gate)
        _install(monkeypatch, client)

        tasks = [
            asyncio.ensure_future(_call()),
            asyncio.ensure_future(_call(trading_pair="ETH-USDT")),
            asyncio.ensure_future(_call(interval="5m")),
        ]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        gate.set_result(None)
        await asyncio.gather(*tasks)
        return client.calls

    assert asyncio.run(scenario()) == 3


def test_failing_fetch_reaches_every_waiter_and_is_not_cached(monkeypatch):
    async def scenario():
        gate = asyncio.get_running_loop().create_future()
        failing = _CountingClient(gate=gate, fail=True)
        _install(monkeypatch, failing)

        tasks = [asyncio.ensure_future(_call()) for _ in range(3)]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        gate.set_result(None)
        errors = await asyncio.gather(*tasks, return_exceptions=True)

        # Nothing cached, nothing left in flight: the next request retries.
        cached = dict(market._candle_cache)
        inflight = dict(market._candle_inflight)

        healthy = _CountingClient()
        _install(monkeypatch, healthy)
        retried = await _call()
        return errors, cached, inflight, failing.calls, healthy.calls, retried

    errors, cached, inflight, fail_calls, ok_calls, retried = asyncio.run(scenario())
    assert fail_calls == 1
    assert len(errors) == 3
    assert all(isinstance(e, Exception) for e in errors)
    assert cached == {}
    assert inflight == {}
    # The retry actually hit upstream and succeeded.
    assert ok_calls == 1
    assert len(retried) == 1


def test_cache_still_answers_after_the_shared_fetch_completes(monkeypatch):
    """Coalescing does not replace the TTL cache — the second wave is a hit."""

    async def scenario():
        client = _CountingClient()
        _install(monkeypatch, client)
        first = await _call()
        second = await _call()
        return client.calls, first, second

    calls, first, second = asyncio.run(scenario())
    assert calls == 1
    assert first is second
