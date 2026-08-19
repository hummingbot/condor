"""Archived-bot performance cache is bounded and single-flight (PERF-185).

condor/web/routes/archived.py used to keep two unbounded module-level dicts —
one full ``ArchivedBotPerformance`` (up to ~5000 PnL points) plus a duplicate
executor list per (server, db_path) ever viewed — for the process lifetime, and
its check-then-fetch had no in-flight coalescing, so concurrent cold-cache
requests each walked the entire paginated trade history independently.

These pin the fix: a small LRU with eviction, one shared fetch per key for
concurrent callers, executors stored once (inside the performance entry), and
cached responses identical to fresh ones.
"""

import asyncio

from condor.web.routes import archived


class FakeArchivedBots:
    """Counts backend calls; optional delay widens the concurrency window."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.summary_calls = 0
        self.trades_calls = 0
        self.executors_calls = 0

    async def get_database_summary(self, db_path):
        self.summary_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return {
            "bot_name": f"bot-{db_path}",
            "total_trades": 0,
            "total_orders": 0,
            "trading_pairs": ["SOL-USDC"],
            "exchanges": ["binance"],
        }

    async def get_database_trades(self, db_path, limit=500, offset=0):
        self.trades_calls += 1
        return {"trades": []}

    async def get_database_executors(self, db_path):
        self.executors_calls += 1
        return {
            "executors": [
                {
                    "id": "e1",
                    "connector": "binance",
                    "trading_pair": "SOL-USDC",
                    "side": "BUY",
                    "net_pnl_quote": 1.5,
                    "timestamp": 1700000000,
                }
            ]
        }


class FakeClient:
    def __init__(self, delay: float = 0.0):
        self.archived_bots = FakeArchivedBots(delay=delay)


def _reset_caches():
    archived._performance_cache.clear()
    archived._performance_inflight.clear()


def test_cache_is_bounded_lru():
    """Viewing cap+5 bots keeps at most cap entries; oldest are evicted."""
    _reset_caches()
    cap = archived._PERFORMANCE_CACHE_MAX
    client = FakeClient()

    async def run():
        for i in range(cap + 5):
            await archived._fetch_and_cache_performance(client, "srv", f"db{i}.sqlite")

    asyncio.run(run())

    assert len(archived._performance_cache) <= cap
    # The five oldest were evicted, the newest survive
    assert ("srv", "db0.sqlite") not in archived._performance_cache
    assert ("srv", "db4.sqlite") not in archived._performance_cache
    assert ("srv", f"db{cap + 4}.sqlite") in archived._performance_cache
    _reset_caches()


def test_lru_hit_refreshes_recency():
    """A cache hit moves the entry to most-recently-used, deferring eviction."""
    _reset_caches()
    cap = archived._PERFORMANCE_CACHE_MAX
    client = FakeClient()

    async def run():
        for i in range(cap):
            await archived._fetch_and_cache_performance(client, "srv", f"db{i}.sqlite")
        # Touch the oldest entry, then overflow by one
        await archived._fetch_and_cache_performance(client, "srv", "db0.sqlite")
        await archived._fetch_and_cache_performance(client, "srv", "extra.sqlite")

    asyncio.run(run())

    assert ("srv", "db0.sqlite") in archived._performance_cache  # refreshed, kept
    assert (
        "srv",
        "db1.sqlite",
    ) not in archived._performance_cache  # now oldest, evicted
    _reset_caches()


def test_concurrent_first_requests_share_one_fetch():
    """Two concurrent cold-cache requests issue exactly one backend walk."""
    _reset_caches()
    client = FakeClient(delay=0.02)

    async def run():
        return await asyncio.gather(
            archived._fetch_and_cache_performance(client, "srv", "db.sqlite"),
            archived._fetch_and_cache_performance(client, "srv", "db.sqlite"),
        )

    first, second = asyncio.run(run())

    assert client.archived_bots.summary_calls == 1
    assert client.archived_bots.trades_calls == 1
    assert client.archived_bots.executors_calls == 1
    assert first is second  # both awaited the same task's result
    assert not archived._performance_inflight  # cleared when done
    _reset_caches()


def test_executors_stored_once_inside_performance_entry():
    """No second executors cache: the perf entry is the single source."""
    _reset_caches()
    client = FakeClient()

    perf = asyncio.run(
        archived._fetch_and_cache_performance(client, "srv", "db.sqlite")
    )

    assert not hasattr(archived, "_executors_cache")
    cached = archived._cache_get(("srv", "db.sqlite"))
    assert cached is perf
    assert cached.executors is perf.executors
    assert len(perf.executors) == 1
    _reset_caches()


def test_cached_response_identical_to_fresh():
    """A cached answer matches a fresh fetch of the same db_path exactly."""
    _reset_caches()
    fresh = asyncio.run(
        archived._fetch_and_cache_performance(FakeClient(), "srv", "db.sqlite")
    )
    cached = asyncio.run(
        archived._fetch_and_cache_performance(FakeClient(), "srv", "db.sqlite")
    )
    assert cached is fresh  # served from cache, no refetch

    _reset_caches()
    refetched = asyncio.run(
        archived._fetch_and_cache_performance(FakeClient(), "srv", "db.sqlite")
    )
    assert refetched.model_dump() == fresh.model_dump()
    _reset_caches()
