"""In-flight coalescing at the GeckoTerminal OHLCV fetch (PERF-169).

PERF-137 coalesced GET /market/candles on the *route's* cache key. That key
carries things the upstream window does not — the trading pair, the server name
— so two requests that resolve to the very same GeckoTerminal window still opened
one upstream request each and spent the rate budget twice. The dedup that matters
for the budget lives at the fetch, keyed by pool + timeframe + window.
"""

import asyncio

import pytest

import condor.web.routes.market as market
from condor import dex_candles, pool_data
from condor.web.models import WebUser

POOL = "So11111111111111111111111111111111111111112"
ROWS = [[1_700_000_000, 1.0, 2.0, 0.5, 1.5, 10.0]]


@pytest.fixture(autouse=True)
def _clean_state():
    def _reset():
        market._candle_cache.clear()
        market._candle_inflight.clear()
        dex_candles._ohlcv_cache.clear()
        pool_data.reset_gecko_throttle()

    _reset()
    yield
    _reset()


class _CountingGecko:
    """Counts get_ohlcv calls and holds them open until the gate is released."""

    def __init__(self, gate=None, fail=False):
        self.calls = 0
        self._gate = gate
        self._fail = fail

    async def __call__(self, method, *args, **kwargs):
        assert method == "get_ohlcv"
        self.calls += 1
        if self._gate is not None:
            await self._gate
        if self._fail:
            raise RuntimeError("gecko down")
        return list(ROWS)


def _route_call(trading_pair: str):
    """One GET /market/candles for a pool, charted under a given pair label."""
    return market.get_candles(
        "srv",
        connector="meteora",
        trading_pair=trading_pair,
        interval="1m",
        limit=100,
        start_time=1_699_999_900.0,
        end_time=1_700_000_100.0,
        pool_address=POOL,
        user=WebUser(id=1, role="user"),
    )


class _Cm:
    def has_server_access(self, user_id, name):
        return True

    async def get_client(self, name):
        raise AssertionError("the DEX path must not touch the API server")


def test_two_route_keys_on_one_upstream_window_share_one_fetch(monkeypatch):
    """The acceptance criterion, driven through the real route.

    Both requests name the same pool, timeframe and window, but different trading
    pairs — so the route's own cache key differs and PERF-137's guard cannot see
    that they converge. Only the fetch-level dedup collapses them.
    """

    async def scenario():
        gate = asyncio.get_running_loop().create_future()
        gecko = _CountingGecko(gate=gate)
        monkeypatch.setattr(pool_data, "gecko_call", gecko)
        monkeypatch.setattr(market, "get_config_manager", lambda: _Cm())

        tasks = [
            asyncio.ensure_future(_route_call("SOL-USDC")),
            asyncio.ensure_future(_route_call("So111111-USDC")),
        ]
        # Let both requests reach the (gated) upstream call before releasing it.
        for _ in range(6):
            await asyncio.sleep(0)
        assert len(market._candle_inflight) == 2, "the route keys must differ"
        gate.set_result(None)
        results = await asyncio.gather(*tasks)
        return gecko.calls, results

    calls, results = asyncio.run(scenario())
    assert calls == 1, "two route keys on one window must spend one gecko request"
    assert [c.close for c in results[0]] == [c.close for c in results[1]]
    assert results[0], "both callers get the candles, not an empty chart"


def test_distinct_caller_caches_still_share_one_fetch(monkeypatch):
    """Dedup keys on the upstream request, never on where the caller caches it."""

    async def scenario():
        gate = asyncio.get_running_loop().create_future()
        gecko = _CountingGecko(gate=gate)
        monkeypatch.setattr(pool_data, "gecko_call", gecko)

        # Three callers: two with their own private caches, one with none at all.
        caches = [{}, {}, None]
        tasks = [
            asyncio.ensure_future(
                pool_data.fetch_ohlcv(
                    POOL, "meteora", timeframe="1m", user_data=cache, limit=50
                )
            )
            for cache in caches
        ]
        for _ in range(6):
            await asyncio.sleep(0)
        gate.set_result(None)
        results = await asyncio.gather(*tasks)
        return gecko.calls, results

    calls, results = asyncio.run(scenario())
    assert calls == 1
    assert all(err is None and rows == ROWS for rows, err in results)


def test_a_different_window_is_not_coalesced(monkeypatch):
    """Same pool, different candle count: two genuinely different answers."""

    async def scenario():
        gate = asyncio.get_running_loop().create_future()
        gecko = _CountingGecko(gate=gate)
        monkeypatch.setattr(pool_data, "gecko_call", gecko)

        tasks = [
            asyncio.ensure_future(
                pool_data.fetch_ohlcv(POOL, "meteora", timeframe="1m", limit=limit)
            )
            for limit in (50, 100)
        ]
        for _ in range(6):
            await asyncio.sleep(0)
        gate.set_result(None)
        await asyncio.gather(*tasks)
        return gecko.calls

    assert asyncio.run(scenario()) == 2


def test_a_failing_fetch_reaches_every_waiter_and_is_not_cached(monkeypatch):
    """A failure is shared, never remembered: the next call retries clean."""

    async def scenario():
        gate = asyncio.get_running_loop().create_future()
        failing = _CountingGecko(gate=gate, fail=True)
        monkeypatch.setattr(pool_data, "gecko_call", failing)

        cache = {}
        tasks = [
            asyncio.ensure_future(
                pool_data.fetch_ohlcv(
                    POOL, "meteora", timeframe="1m", user_data=cache, limit=50
                )
            )
            for _ in range(3)
        ]
        for _ in range(6):
            await asyncio.sleep(0)
        gate.set_result(None)
        failed = await asyncio.gather(*tasks)

        # Nothing was written to the caller's cache, so the retry goes upstream.
        healthy = _CountingGecko()
        monkeypatch.setattr(pool_data, "gecko_call", healthy)
        retried = await pool_data.fetch_ohlcv(
            POOL, "meteora", timeframe="1m", user_data=cache, limit=50
        )
        return failing.calls, failed, healthy.calls, retried

    fail_calls, failed, retry_calls, retried = asyncio.run(scenario())
    assert fail_calls == 1, "the failing fetch is shared, not repeated per waiter"
    assert all(rows is None and err for rows, err in failed), "every waiter sees it"
    assert retry_calls == 1, "a failure is not cached; the next call retries"
    assert retried == (ROWS, None)


def test_a_cancelled_waiter_does_not_cancel_the_shared_fetch(monkeypatch):
    """One viewer navigating away must not take the others' chart down with it."""

    async def scenario():
        gate = asyncio.get_running_loop().create_future()
        gecko = _CountingGecko(gate=gate)
        monkeypatch.setattr(pool_data, "gecko_call", gecko)

        leaver = asyncio.ensure_future(
            pool_data.fetch_ohlcv(POOL, "meteora", timeframe="1m", limit=50)
        )
        stayer = asyncio.ensure_future(
            pool_data.fetch_ohlcv(POOL, "meteora", timeframe="1m", limit=50)
        )
        for _ in range(6):
            await asyncio.sleep(0)
        leaver.cancel()
        gate.set_result(None)
        return gecko.calls, await stayer

    calls, rows = asyncio.run(scenario())
    assert calls == 1
    assert rows == (ROWS, None)
