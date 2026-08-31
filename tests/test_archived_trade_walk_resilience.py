"""A flaky trade page must never become a run reported as $0.00.

The archived-run header (PnL, volume, fees, trade counts) is built by walking
every page of the run's trade history — dozens of round trips for a busy run.
That walk used to swallow any exception and ``break``, so one transient 5xx
produced a truncated or empty trade list, a header of zeros, and a cache entry
pinning those zeros for the life of the process — all while the chart below,
which reads executors, plotted the run's real PnL.

These pin both halves of the fix: pages are retried, and a walk that still
fails falls back to the (complete) executor set instead of publishing zeros.
"""

import asyncio

import pytest

from condor.fetchers import archived_run as archived

TRADES = [
    {
        "timestamp": 1_700_000_000_000 + i * 1000,
        "trading_pair": "BTC-USDT",
        "trade_type": "BUY" if i % 2 == 0 else "SELL",
        "position": "NIL",
        "price": 100.0 + i,
        "amount": 1.0,
        "trade_fee_in_quote": 0.01,
    }
    for i in range(1200)
]

EXECUTORS = [
    {
        "id": f"e{i}",
        "connector": "binance",
        "trading_pair": "BTC-USDT",
        "side": "BUY" if i % 2 == 0 else "SELL",
        "net_pnl_quote": 0.5,
        "cum_fees_quote": 0.02,
        "filled_amount_quote": 25.0,
        "timestamp": 1_700_000_000 + i,
        "close_timestamp": 1_700_000_100 + i,
    }
    for i in range(40)
]


class FlakyArchivedBots:
    """Fails the trade page at ``fail_offset`` for its first ``fail_times`` tries."""

    def __init__(self, fail_offset: int | None = None, fail_times: int = 0):
        self.fail_offset = fail_offset
        self.fail_times = fail_times
        self.attempts: list[int] = []

    async def get_database_summary(self, db_path):
        return {
            "bot_name": "flaky-bot",
            "total_trades": len(TRADES),
            "total_orders": 0,
            "trading_pairs": ["BTC-USDT"],
            "exchanges": ["binance"],
        }

    async def get_database_trades(self, db_path, limit=500, offset=0):
        self.attempts.append(offset)
        if offset == self.fail_offset and self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("500 Internal Server Error")
        return {"trades": TRADES[offset : offset + limit]}

    async def get_database_executors(self, db_path):
        return {"executors": EXECUTORS}


class FlakyClient:
    def __init__(self, **kwargs):
        self.archived_bots = FlakyArchivedBots(**kwargs)


@pytest.fixture(autouse=True)
def _clear_cache():
    archived._performance_cache.clear()
    archived._performance_inflight.clear()
    yield
    archived._performance_cache.clear()
    archived._performance_inflight.clear()


def _fetch(client):
    return asyncio.run(archived._fetch_performance(client, "srv", "db.sqlite"))


def test_clean_walk_reports_every_trade():
    perf = _fetch(FlakyClient())

    assert perf.stats_source == "trades"
    assert perf.trade_count == 1200
    assert perf.buy_count == 600
    assert perf.sell_count == 600


def test_transient_page_failure_is_retried_not_swallowed():
    """One 5xx mid-walk must not cost the run 700 of its 1200 trades."""
    client = FlakyClient(fail_offset=500, fail_times=1)

    perf = _fetch(client)

    assert perf.stats_source == "trades"
    assert perf.trade_count == 1200
    # The failed page was actually re-requested rather than skipped.
    assert client.archived_bots.attempts.count(500) == 2


def test_persistent_page_failure_falls_back_to_executors_not_zeros():
    client = FlakyClient(fail_offset=0, fail_times=99)

    perf = _fetch(client)

    assert perf.stats_source == "executors"
    # The run did trade; the header must say so from the source that answered.
    assert perf.total_pnl == pytest.approx(40 * 0.5)
    assert perf.total_volume == pytest.approx(40 * 25.0)
    assert perf.total_fees == pytest.approx(40 * 0.02)
    assert perf.trade_count == 40
    assert perf.buy_count == 20
    assert perf.sell_count == 20
    assert perf.cumulative_pnl, "a fallback header still needs its PnL curve"


def test_failure_partway_still_prefers_the_complete_executor_set():
    """Half a trade history is a wrong answer, not a partial one."""
    client = FlakyClient(fail_offset=500, fail_times=99)

    perf = _fetch(client)

    assert perf.stats_source == "executors"
    assert perf.trade_count == 40


def test_every_executor_survives_the_walk_for_whoever_charts_it():
    """The chart is built from these, by whoever draws it, so none may be lost."""
    from condor.archived_chart_series import build_chart_series

    perf = _fetch(FlakyClient())

    series = build_chart_series(perf.executors)["binance:BTC-USDT"]
    assert series["executor_count"] == len(EXECUTORS)
    assert series["interval"] == "1m"
    assert series["start"] == EXECUTORS[0]["timestamp"]
    assert series["end"] == EXECUTORS[-1]["close_timestamp"]
