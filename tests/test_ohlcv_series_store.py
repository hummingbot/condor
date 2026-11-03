"""A pool's candles are one growing series, not a set of independent windows.

The OHLCV cache used to be keyed by the caller's *window* — its limit and its
before_timestamp — so the same pool charted over 7 days, over 3 days, and polled
five candles at a time was three entries holding three overlapping copies, and a
window differing by one candle from a cached one paid a fresh GeckoTerminal
request for rows already in memory.

Two facts drive the store this file exercises:

* a request costs the same whatever its ``limit``, so a cold series buys the
  maximum one request can carry;
* a closed candle never changes, so only the tail is perishable.

Everything below is counted in *upstream requests*, because that — not candles,
not bytes — is what GeckoTerminal's budget is denominated in.
"""

import asyncio
import time

import pytest

from condor import pool_data

POOL = "So11111111111111111111111111111111111111112"
NET = "meteora"
MINUTE = 60


@pytest.fixture(autouse=True)
def _clean_state():
    pool_data.reset_gecko_throttle()
    yield
    pool_data.reset_gecko_throttle()


class _Gecko:
    """A pool with `total` one-minute candles ending now, served like upstream."""

    def __init__(self, total=3000, now=None):
        self.calls = []
        self.now = now or time.time()
        newest = int(self.now // MINUTE) * MINUTE
        self.rows = [
            [float(newest - i * MINUTE), 1.0, 2.0, 0.5, 1.5, 10.0]
            for i in range(total - 1, -1, -1)
        ]

    async def __call__(self, method, *args, **kwargs):
        assert method == "get_ohlcv"
        limit = kwargs["limit"]
        before = kwargs.get("before_timestamp")
        self.calls.append((limit, before))
        rows = [r for r in self.rows if before is None or r[0] < before]
        # Upstream answers with the newest `limit` candles before `before`.
        return [list(r) for r in rows[-limit:]]

    @property
    def limits(self):
        return [limit for limit, _ in self.calls]


def run(coro):
    return asyncio.run(coro)


# ── always buy the biggest page a request can carry ──


def test_a_cold_series_requests_the_maximum_however_little_is_asked_for(monkeypatch):
    """A five-candle poll and a 700-candle chart cost the same one request."""
    gecko = _Gecko()

    async def scenario():
        monkeypatch.setattr(pool_data, "gecko_call", gecko)
        return await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=5)

    rows, err = run(scenario())
    assert err is None
    assert len(rows) == 5, "the caller still gets exactly the window it asked for"
    assert gecko.limits == [
        pool_data.GECKO_OHLCV_MAX
    ], "but the request behind it buys the whole page, since it costs the same"


def test_a_wider_window_is_free_once_the_page_is_in_memory(monkeypatch):
    """The pool page's 3d/7d/14d buttons, in the order a trader clicks them."""
    gecko = _Gecko()

    async def scenario():
        monkeypatch.setattr(pool_data, "gecko_call", gecko)
        first, _ = await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=100)
        wider, _ = await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=900)
        return first, wider

    first, wider = run(scenario())
    assert len(first) == 100 and len(wider) == 900
    assert len(gecko.calls) == 1, "widening the window spends nothing"


# ── only add the candles that are actually missing ──


def test_a_live_poll_asks_only_for_the_candles_minted_since_the_last_one(monkeypatch):
    """The poll loop's steady state: one page once, then a handful of candles."""
    gecko = _Gecko()

    async def scenario():
        monkeypatch.setattr(pool_data, "gecko_call", gecko)
        await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=700)
        # Three minutes later the chart polls again, bypassing the cached tail.
        gecko.now += 3 * MINUTE
        await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=7, use_cache=False)

    run(scenario())
    assert gecko.limits[0] == pool_data.GECKO_OHLCV_MAX, "the cold page"
    assert (
        gecko.limits[1] <= 10
    ), f"the top-up must not re-buy the series it already holds: {gecko.limits[1]}"


def test_the_poll_keeps_the_history_it_already_had(monkeypatch):
    """Topping up the tail must not shrink the chart to the tail."""
    gecko = _Gecko()

    async def scenario():
        monkeypatch.setattr(pool_data, "gecko_call", gecko)
        await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=700)
        await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=7, use_cache=False)
        # The chart re-renders its full window after the poll.
        rows, _ = await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=700)
        return rows

    assert len(run(scenario())) == 700


def test_reaching_further_back_fetches_only_the_older_page(monkeypatch):
    """Scrolling into history walks back a page at a time, never re-reading."""
    gecko = _Gecko()

    async def scenario():
        monkeypatch.setattr(pool_data, "gecko_call", gecko)
        await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=900)
        # Past the first page: needs history the series does not hold yet.
        await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=1000)
        return gecko.calls

    calls = run(scenario())
    assert len(calls) == 2
    assert calls[1][1] is not None, "the second request walks back from the oldest row"


# ── a closed window is immutable: served from memory however stale ──


def test_a_window_that_has_already_closed_is_never_re_fetched(monkeypatch):
    """The executor-chart path: a finished position, charted again next week."""
    # Deep enough that the closed window is real history, not the pool's start.
    gecko = _Gecko(total=6000)
    closed_end = int(gecko.now - 2 * 86400)

    async def scenario():
        monkeypatch.setattr(pool_data, "gecko_call", gecko)
        await pool_data.fetch_ohlcv(
            POOL, NET, timeframe="1m", limit=200, before_timestamp=closed_end
        )
        # Long past any TTL — but those candles cannot have changed.
        series = next(iter(pool_data._ohlcv_series.values()))
        series.tail_fetched_at = 0.0
        rows, _ = await pool_data.fetch_ohlcv(
            POOL, NET, timeframe="1m", limit=200, before_timestamp=closed_end
        )
        return rows, gecko.calls

    rows, calls = run(scenario())
    assert len(rows) == 200
    assert len(calls) == 1, "immutable history has no TTL"


def test_a_stale_live_edge_is_re_fetched(monkeypatch):
    """The other half of the same rule: the forming candle does expire."""
    gecko = _Gecko()

    async def scenario():
        monkeypatch.setattr(pool_data, "gecko_call", gecko)
        await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=100)
        series = next(iter(pool_data._ohlcv_series.values()))
        series.tail_fetched_at = time.time() - pool_data.OHLCV_CACHE_TTL - 1
        await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=100)
        return gecko.calls

    assert len(run(scenario())) == 2


def test_a_pool_shorter_than_the_window_is_not_re_asked_forever(monkeypatch):
    """A young pool answers short; asking again would spend the budget on nothing."""
    gecko = _Gecko(total=40)

    async def scenario():
        monkeypatch.setattr(pool_data, "gecko_call", gecko)
        rows, _ = await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=900)
        again, _ = await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=900)
        return rows, again, gecko.calls

    rows, again, calls = run(scenario())
    assert len(rows) == 40 and len(again) == 40
    assert len(calls) == 1, "upstream said that is all there is; believe it"


# ── bounded memory ──


def test_the_store_evicts_least_recently_used_pools(monkeypatch):
    """The bound the old per-window cache did not have."""

    async def scenario():
        for i in range(pool_data._SERIES_MAX + 10):
            monkeypatch.setattr(pool_data, "gecko_call", _Gecko(total=5))
            await pool_data.fetch_ohlcv(f"{POOL}{i}", NET, timeframe="1m", limit=5)

    run(scenario())
    assert len(pool_data._ohlcv_series) == pool_data._SERIES_MAX


def test_one_pool_cannot_grow_without_limit(monkeypatch):
    """Paging back through a deep pool trims the oldest rows, not the live edge."""
    gecko = _Gecko(total=6000)

    async def scenario():
        monkeypatch.setattr(pool_data, "gecko_call", gecko)
        for _ in range(6):
            await pool_data.fetch_ohlcv(
                POOL, NET, timeframe="1m", limit=pool_data.GECKO_OHLCV_MAX
            )
        series = next(iter(pool_data._ohlcv_series.values()))
        return series

    series = run(scenario())
    assert len(series.rows) <= pool_data._SERIES_MAX_ROWS
    assert series.newest == gecko.rows[-1][0], "the live edge survives the trim"


def test_a_distant_closed_window_lands_in_one_request(monkeypatch):
    """Charting last month's executor must not page back a thousand at a time."""
    gecko = _Gecko(total=20000)
    far = int(gecko.now - 10 * 86400)

    async def scenario():
        monkeypatch.setattr(pool_data, "gecko_call", gecko)
        # A live chart on the same pool holds the tail...
        await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=500)
        # ...then a finished executor from ten days ago is charted.
        rows, _ = await pool_data.fetch_ohlcv(
            POOL, NET, timeframe="1m", limit=300, before_timestamp=far
        )
        return rows, gecko.calls

    rows, calls = run(scenario())
    assert len(rows) == 300
    assert len(calls) == 2, "one request for the tail, one for the distant window"
    assert calls[1][1] == far, "which goes straight to the window, not paging down"


# ── two blocks with a gap between them are not one covered stretch ──


def _assert_window(rows, end, limit):
    """Every row lies inside the window that was actually asked for."""
    assert len(rows) == limit
    oldest_wanted = end - limit * MINUTE
    stray = [r[0] for r in rows if not oldest_wanted <= r[0] <= end]
    assert not stray, f"rows from outside the requested window: {stray[:3]}"


def test_a_window_between_two_fetched_blocks_is_not_served_from_either(monkeypatch):
    """Last month's executor, then the live chart — a week sits unfetched between.

    The distant jump and the live poll each buy one page, so the series holds two
    blocks with a gap. A window inside that gap has never been fetched and must
    not be answered with the tail of the older block.
    """
    gecko = _Gecko(total=20000)
    far = int(gecko.now - 10 * 86400)
    gap = int(gecko.now - 3 * 86400)

    async def scenario():
        monkeypatch.setattr(pool_data, "gecko_call", gecko)
        await pool_data.fetch_ohlcv(
            POOL, NET, timeframe="1m", limit=300, before_timestamp=far
        )
        await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=500)
        rows, _ = await pool_data.fetch_ohlcv(
            POOL, NET, timeframe="1m", limit=100, before_timestamp=gap
        )
        return rows, gecko.calls

    rows, calls = run(scenario())
    assert len(calls) == 3, "the gap was never fetched; it has to cost a request"
    assert calls[2][1] == gap, "and the request goes straight to the missing window"
    _assert_window(rows, gap, 100)


def test_the_gap_holds_in_the_other_order_too(monkeypatch):
    """The same two blocks, live chart first — the gap is just as unfetched."""
    gecko = _Gecko(total=20000)
    far = int(gecko.now - 10 * 86400)
    gap = int(gecko.now - 3 * 86400)

    async def scenario():
        monkeypatch.setattr(pool_data, "gecko_call", gecko)
        await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=500)
        await pool_data.fetch_ohlcv(
            POOL, NET, timeframe="1m", limit=300, before_timestamp=far
        )
        rows, _ = await pool_data.fetch_ohlcv(
            POOL, NET, timeframe="1m", limit=100, before_timestamp=gap
        )
        return rows, gecko.calls

    rows, calls = run(scenario())
    assert len(calls) == 3, "the gap was never fetched; it has to cost a request"
    assert calls[2][1] == gap, "and the request goes straight to the missing window"
    _assert_window(rows, gap, 100)


def test_a_contiguous_walk_back_still_costs_one_request_per_page(monkeypatch):
    """The other half of the rule: no gap, so nothing is re-bought or thrown away.

    Two pages walked back-to-back abut, so they stay one block — and a window in
    the middle of that block is free, and drawn from the right period.
    """
    gecko = _Gecko(total=6000)
    inside = int(gecko.now - 1500 * MINUTE)

    async def scenario():
        monkeypatch.setattr(pool_data, "gecko_call", gecko)
        await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=900)
        # Past the first page: one more request, walking back from the oldest row.
        await pool_data.fetch_ohlcv(POOL, NET, timeframe="1m", limit=1000)
        # A window well inside the two pages: already fetched, so already answered.
        rows, _ = await pool_data.fetch_ohlcv(
            POOL, NET, timeframe="1m", limit=400, before_timestamp=inside
        )
        return rows, gecko.calls

    rows, calls = run(scenario())
    assert len(calls) == 2, f"one request per page and not one more: {calls}"
    assert calls[0][1] is None and calls[1][1] is not None, "the second walks back"
    _assert_window(rows, inside, 400)
