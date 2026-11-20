"""Archived-bot performance cache is bounded and single-flight (PERF-185).

The archived-run fetcher (then still inside condor/web/routes/archived.py) used
to keep two unbounded module-level dicts —
one full ``ArchivedBotPerformance`` (up to ~5000 PnL points) plus a duplicate
executor list per (server, db_path) ever viewed — for the process lifetime, and
its check-then-fetch had no in-flight coalescing, so concurrent cold-cache
requests each walked the entire paginated trade history independently.

These pin the fix: a small LRU with eviction, one shared fetch per key for
concurrent callers, executors stored once (inside the performance entry), and
cached responses identical to fresh ones.
"""

import asyncio

from condor.fetchers import archived_run as archived


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
            await archived.fetch_archived_run(client, "srv", f"db{i}.sqlite")

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
            await archived.fetch_archived_run(client, "srv", f"db{i}.sqlite")
        # Touch the oldest entry, then overflow by one
        await archived.fetch_archived_run(client, "srv", "db0.sqlite")
        await archived.fetch_archived_run(client, "srv", "extra.sqlite")

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
            archived.fetch_archived_run(client, "srv", "db.sqlite"),
            archived.fetch_archived_run(client, "srv", "db.sqlite"),
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

    perf = asyncio.run(archived.fetch_archived_run(client, "srv", "db.sqlite"))

    assert not hasattr(archived, "_executors_cache")
    cached = archived.cached_run("srv", "db.sqlite")
    assert cached is perf
    assert cached.executors is perf.executors
    assert len(perf.executors) == 1
    _reset_caches()


def test_cached_response_identical_to_fresh():
    """A cached answer matches a fresh fetch of the same db_path exactly."""
    _reset_caches()
    fresh = asyncio.run(archived.fetch_archived_run(FakeClient(), "srv", "db.sqlite"))
    cached = asyncio.run(archived.fetch_archived_run(FakeClient(), "srv", "db.sqlite"))
    assert cached is fresh  # served from cache, no refetch

    _reset_caches()
    refetched = asyncio.run(
        archived.fetch_archived_run(FakeClient(), "srv", "db.sqlite")
    )
    assert refetched.model_dump() == fresh.model_dump()
    _reset_caches()


# ── DL-06: a degraded result is not pinned for the life of the process ──


class _Rates:
    """``resolve_usd_rates`` stand-in that cannot price the quote at first."""

    def __init__(self, failures: int):
        self.failures = failures

    async def __call__(self, server, quotes):
        from condor.quote_conversion import QuoteRates

        if self.failures > 0:
            self.failures -= 1
            return QuoteRates({}, False)
        return QuoteRates({q: 1.0 for q in quotes}, True)


def test_an_unconverted_result_expires_and_the_next_read_converts(monkeypatch):
    """One failed rate lookup on first open used to pin ``converted=False``
    figures under the no-TTL key until Condor restarted."""
    import condor.quote_conversion as qc

    _reset_caches()
    monkeypatch.setattr(qc, "resolve_usd_rates", _Rates(failures=1))
    client = FakeClient()

    first = asyncio.run(archived.fetch_archived_run(client, "srv", "db.sqlite"))
    assert first.converted is False
    assert archived.run_is_degraded("srv", "db.sqlite")

    # Inside the short TTL the page's follow-up requests share the one walk.
    assert archived.cached_run("srv", "db.sqlite") is first
    assert client.archived_bots.summary_calls == 1

    # Past it the entry is gone and the run is read again — converted this time.
    key = ("srv", "db.sqlite")
    archived._degraded_until[key] -= archived._DEGRADED_TTL + 1
    assert archived.cached_run("srv", "db.sqlite") is None
    second = asyncio.run(archived.fetch_archived_run(client, "srv", "db.sqlite"))
    assert second.converted is True
    assert client.archived_bots.summary_calls == 2

    # A complete result is kept for good, as before.
    assert not archived.run_is_degraded("srv", "db.sqlite")
    assert archived._degraded_until == {}
    assert (
        asyncio.run(archived.fetch_archived_run(client, "srv", "db.sqlite")) is second
    )
    assert client.archived_bots.summary_calls == 2
    _reset_caches()


def test_a_failed_executor_fetch_marks_the_result_degraded():
    _reset_caches()
    client = FakeClient()

    async def boom(db_path):
        raise RuntimeError("502 Bad Gateway")

    client.archived_bots.get_database_executors = boom
    perf = asyncio.run(archived.fetch_archived_run(client, "srv", "db.sqlite"))

    assert perf.executors == []
    assert archived.run_is_degraded("srv", "db.sqlite")
    _reset_caches()
