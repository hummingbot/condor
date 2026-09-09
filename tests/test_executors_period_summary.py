"""Tests for CORR-129: the KPI strip's period totals cover the period.

The dashboard used to sum the executor list it already had in the browser. That
list is the SDS cache, which PERF-117 bounded to a single page, so the 1W and 1M
tiles reported the newest page's PnL, volume and count as if they were the
window's — a wrong number with nothing to mark it as one.

These tests pin the replacement endpoint: it walks the whole history with
``fetch_all_executors``, filters on the executor's start timestamp, converts each
quote asset to USD once, and caches the answer per period so the walk is not
re-run per browser tab. The 2s poll is not part of any of it.
"""

import asyncio
import time

import pytest
from fastapi import HTTPException

from condor.fetchers.executors import EXECUTORS_PAGE_SIZE, summarize_executors_by_quote
from condor.web.models import WebUser
from condor.web.routes.executors import (
    _summary_cache,
    _summary_walks,
    executors_summary,
)

_USER = WebUser(id=1, role="admin")
_DAY = 86400


def _executor(i, *, age_days=0.0, pair="BTC-USDT", pnl=1.0, volume=10.0):
    """One raw executor row, started ``age_days`` ago."""
    return {
        "id": f"e{i}",
        "config": {
            "type": "position_executor",
            "trading_pair": pair,
            "timestamp": time.time() - age_days * _DAY,
        },
        "status": "TERMINATED",
        "net_pnl_quote": pnl,
        "filled_amount_quote": volume,
    }


class FakeClient:
    """Client serving ``rows`` one page at a time, recording every request."""

    def __init__(self, rows, fail=False):
        self.calls = []
        self.executors = self._Executors(self)
        self._rows = rows
        self.fail = fail

    @property
    def walks(self):
        """How many full-history walks were started.

        A walk is one ``fetch_all_executors`` pass: it opens with a cursor-less
        page and then follows ``next_cursor``. Counting pages hides the thing
        PERF-580 is about — a second walk over a history short enough to fit in
        one page adds exactly one page — so the tests below count openings.
        """
        return sum(1 for kw in self.calls if not kw.get("cursor"))

    class _Executors:
        def __init__(self, outer):
            self._outer = outer

        async def search_executors(self, **kwargs):
            outer = self._outer
            outer.calls.append(kwargs)
            # A real page is I/O and yields to the loop. Without a yield here a
            # "concurrent" test is not concurrent at all: the first caller runs
            # to completion before the second is scheduled, and even uncoalesced
            # walks look like one.
            await asyncio.sleep(0)
            if outer.fail:
                raise RuntimeError("backend unreachable")
            start = int(kwargs.get("cursor") or 0)
            page = outer._rows[start : start + kwargs["limit"]]
            end = start + len(page)
            return {
                "executors": page,
                "next_cursor": str(end) if end < len(outer._rows) else None,
            }


class _FakeCM:
    def __init__(self, client):
        self._client = client

    def has_server_access(self, user_id, name, *a, **kw):
        return True

    async def get_client(self, name):
        return self._client


@pytest.fixture(autouse=True)
def clean_cache():
    """The summary cache is module state — no test may inherit another's."""
    _summary_cache.clear()
    _summary_walks.clear()
    yield
    _summary_cache.clear()
    _summary_walks.clear()


@pytest.fixture
def summary_env(monkeypatch):
    """Bind the endpoint to a fake client and a fixed rate table."""

    def _bind(rows, rates=None, fail=False):
        client = FakeClient(rows, fail=fail)
        monkeypatch.setattr(
            "condor.web.routes.executors.get_config_manager", lambda: _FakeCM(client)
        )

        async def _fake_rates(server, pairs, connector=None):
            return {pair: (rates or {}).get(pair) for pair in pairs}

        monkeypatch.setattr("condor.market_rates.get_rates", _fake_rates)
        return client

    return _bind


def _summary(period="1D", server="srv"):
    return asyncio.run(executors_summary(name=server, period=period, user=_USER))


# ── the truncation this item exists to fix ──


def test_summary_spans_the_whole_history_not_one_page(summary_env):
    """More executors than a page fit in the window, and all of them count."""
    rows = [_executor(i, age_days=0.5) for i in range(EXECUTORS_PAGE_SIZE + 120)]
    client = summary_env(rows, {"USDT-USDT": 1.0})

    result = _summary("1D")

    assert (
        result.count == EXECUTORS_PAGE_SIZE + 120
    ), "the window was truncated to a page"
    assert result.pnl == pytest.approx(EXECUTORS_PAGE_SIZE + 120)
    assert len(client.calls) > 1, "a full-history total needs the whole walk"
    assert client.calls[0]["limit"] == EXECUTORS_PAGE_SIZE


def test_executors_outside_the_window_are_excluded(summary_env):
    """A 1D total holds yesterday's executors and not last week's."""
    rows = [
        _executor(0, age_days=0.5),
        _executor(1, age_days=3),
        _executor(2, age_days=20),
    ]
    summary_env(rows, {"USDT-USDT": 1.0})

    assert _summary("1D").count == 1
    assert _summary("1W").count == 2
    assert _summary("1M").count == 3


# ── USD denomination ──


def test_totals_are_converted_to_usd_per_quote_asset(summary_env):
    """A BTC-quoted executor is priced in USD, not added to USDT at face value."""
    rows = [
        _executor(0, pair="ETH-BTC", pnl=2.0, volume=5.0),
        _executor(1, pair="SOL-USDT", pnl=3.0, volume=7.0),
    ]
    summary_env(rows, {"BTC-USDT": 50_000.0, "USDT-USDT": 1.0})

    result = _summary("1D")

    assert result.pnl == pytest.approx(2.0 * 50_000 + 3.0)
    assert result.volume == pytest.approx(5.0 * 50_000 + 7.0)
    assert result.converted is True


def test_an_unpriceable_quote_is_reported_not_hidden(summary_env):
    """No path to USD: the rows still count, and the total says it is approximate."""
    rows = [_executor(0, pair="FOO-XYZ", pnl=4.0, volume=9.0)]
    summary_env(rows, {})  # every rate resolves to None

    result = _summary("1D")

    assert result.pnl == pytest.approx(
        4.0
    ), "an unconvertible row is kept at face value"
    assert result.converted is False


def test_a_rate_lookup_failure_still_returns_the_totals(summary_env, monkeypatch):
    """Rates down is not a reason to blank the tile; it is a reason to flag it."""
    rows = [_executor(0, pair="BTC-BRL", pnl=6.0)]
    summary_env(rows, {"BRL-USDT": 0.2})

    async def _boom(server, pairs, connector=None):
        raise RuntimeError("ticker pool unreachable")

    monkeypatch.setattr("condor.market_rates.get_rates", _boom)

    result = _summary("1D")

    assert result.pnl == pytest.approx(6.0)
    assert result.converted is False


# ── CORR-602: stablecoin quotes price off the shared helper, not a market ──


def test_a_stablecoin_quote_prices_without_a_market(summary_env):
    """DAI is a dollar even on a server whose pool lists no DAI market.

    The KPI strip used to resolve rates itself and ask the pool for ``DAI-USDT``;
    a server without that market got ``converted: false`` here while the
    archived-run path — which has always gone through ``resolve_usd_rates`` —
    called the same history converted. Both now short-circuit the stablecoin.
    """
    rows = [_executor(0, pair="ETH-DAI", pnl=4.0, volume=9.0)]
    summary_env(rows, {})  # no DAI-USDT market anywhere in the pool

    result = _summary("1D")

    assert result.pnl == pytest.approx(4.0), "a DAI dollar is a dollar"
    assert result.volume == pytest.approx(9.0)
    assert result.converted is True, "a stablecoin total is exact, not approximate"


def test_a_stablecoin_total_survives_a_rate_outage(summary_env, monkeypatch):
    """No quote needs a lookup, so an unreachable pool cannot make it approximate."""
    rows = [_executor(0, pair="SOL-USDC", pnl=5.0, volume=11.0)]
    summary_env(rows, {})

    async def _boom(server, pairs, connector=None):
        raise RuntimeError("ticker pool unreachable")

    monkeypatch.setattr("condor.market_rates.get_rates", _boom)

    result = _summary("1D")

    assert result.pnl == pytest.approx(5.0)
    assert result.volume == pytest.approx(11.0)
    assert result.converted is True


def test_a_mixed_total_flags_only_the_quote_that_failed(summary_env):
    """A resolvable stable plus an unpriceable quote: dollars right, flag honest."""
    rows = [
        _executor(0, pair="ETH-DAI", pnl=4.0, volume=9.0),
        _executor(1, pair="FOO-XYZ", pnl=2.0, volume=3.0),
    ]
    summary_env(rows, {})

    result = _summary("1D")

    assert result.pnl == pytest.approx(6.0)
    assert result.converted is False


# ── cost control ──


def test_the_aggregate_is_cached_per_period(summary_env):
    """A second reader inside the TTL re-walks nothing."""
    rows = [_executor(i, age_days=0.5) for i in range(10)]
    client = summary_env(rows, {"USDT-USDT": 1.0})

    _summary("1D")
    _summary("1D")

    assert client.walks == 1, "the cached period must not re-walk"


# ── PERF-580: one walk feeds every window, and concurrent callers share it ──


def test_every_period_is_totalled_from_a_single_walk(summary_env):
    """Switching the strip's period re-reads nothing.

    The walk does not depend on the window — ``summarize_executors_by_quote``
    filters the same rows on their start timestamp — so the three windows are
    three folds over one history, not three histories. This test used to assert
    the opposite ("a different period is a different total"), which pinned the
    defect: it counted the walk growing on every period switch.
    """
    rows = [_executor(i, age_days=0.5) for i in range(10)]
    client = summary_env(rows, {"USDT-USDT": 1.0})

    _summary("1D")
    _summary("1W")
    _summary("1M")

    assert client.walks == 1, "each period switch re-walked the whole history"
    assert set(_summary_cache) == {("srv", p) for p in ("1D", "1W", "1M")}


def test_the_windows_computed_together_match_the_windows_computed_apart(summary_env):
    """The coalesced totals are the same dollars, to the last float.

    Same rows, same order, same cutoff arithmetic — folding three windows out of
    one pass may not move a single PnL, volume or count, nor the ``converted``
    flag, which each window still resolves over its own quote assets.
    """
    rows = [
        _executor(0, age_days=0.5, pair="ETH-BTC", pnl=0.1, volume=0.3),
        _executor(1, age_days=0.5, pair="SOL-USDT", pnl=2.7, volume=9.1),
        _executor(2, age_days=3, pair="SOL-USDT", pnl=-1.3, volume=4.4),
        _executor(3, age_days=20, pair="ETH-BTC", pnl=0.02, volume=0.05),
    ]
    rates = {"BTC-USDT": 50_000.0, "USDT-USDT": 1.0}
    summary_env(rows, rates)

    together = {p: _summary(p) for p in ("1D", "1W", "1M")}

    apart = {}
    for period in ("1D", "1W", "1M"):
        _summary_cache.clear()
        _summary_walks.clear()
        summary_env(rows, rates)
        apart[period] = _summary(period)

    for period in ("1D", "1W", "1M"):
        assert together[period].pnl == apart[period].pnl
        assert together[period].volume == apart[period].volume
        assert together[period].count == apart[period].count
        assert together[period].converted == apart[period].converted


def test_concurrent_readers_of_a_cold_cache_share_one_walk(summary_env):
    """Two tabs opening at once are one walk, not two.

    A TTL cache only helps a request that arrives after an answer has landed.
    The strip refetches every 60s against a 60s TTL for 1D, so a second tab
    reliably misses the same cold cache the first one is already filling.
    """
    rows = [_executor(i, age_days=0.5) for i in range(10)]
    client = summary_env(rows, {"USDT-USDT": 1.0})

    async def _both():
        return await asyncio.gather(
            executors_summary(name="srv", period="1D", user=_USER),
            executors_summary(name="srv", period="1D", user=_USER),
        )

    first, second = asyncio.run(_both())

    assert client.walks == 1, "concurrent readers each ran their own walk"
    assert first.pnl == second.pnl == pytest.approx(10.0)
    assert first.count == second.count == 10


def test_concurrent_readers_of_different_periods_share_one_walk(summary_env):
    """The walk is keyed by server, so 1D and 1W in flight together is one walk."""
    rows = [_executor(0, age_days=0.5), _executor(1, age_days=3)]
    client = summary_env(rows, {"USDT-USDT": 1.0})

    async def _both():
        return await asyncio.gather(
            executors_summary(name="srv", period="1D", user=_USER),
            executors_summary(name="srv", period="1W", user=_USER),
        )

    day, week = asyncio.run(_both())

    assert client.walks == 1
    assert day.count == 1 and day.period == "1D"
    assert week.count == 2 and week.period == "1W"


def test_a_failed_walk_is_not_cached_and_is_retried(summary_env):
    """A walk that raises leaves no entry behind, and the shape stays upstream_error."""
    client = summary_env([_executor(0, age_days=0.5)], {"USDT-USDT": 1.0}, fail=True)

    with pytest.raises(HTTPException) as exc:
        _summary("1D")

    assert exc.value.status_code == 502
    assert "Failed to fetch executors" in exc.value.detail
    assert _summary_cache == {}, "a failure must not be served as a total"
    assert not _summary_walks, "a settled walk must not be joined by the next caller"

    client.fail = False
    assert _summary("1D").count == 1, "the next request must retry the walk"


def test_concurrent_readers_of_a_failing_walk_all_see_the_error(summary_env):
    """Sharing a walk shares its failure — nobody gets a silent zero."""
    summary_env([_executor(0, age_days=0.5)], {"USDT-USDT": 1.0}, fail=True)

    async def _both():
        return await asyncio.gather(
            executors_summary(name="srv", period="1D", user=_USER),
            executors_summary(name="srv", period="1W", user=_USER),
            return_exceptions=True,
        )

    first, second = asyncio.run(_both())

    assert isinstance(first, HTTPException) and first.status_code == 502
    assert isinstance(second, HTTPException) and second.status_code == 502
    assert _summary_cache == {}


def test_an_expired_entry_is_recomputed(summary_env):
    """The cache ages out: past the TTL the walk runs again."""
    rows = [_executor(0, age_days=0.5)]
    client = summary_env(rows, {"USDT-USDT": 1.0})

    _summary("1D")
    walked = len(client.calls)
    stamped, value = _summary_cache[("srv", "1D")]
    _summary_cache[("srv", "1D")] = (stamped - 10_000, value)
    _summary()

    assert len(client.calls) > walked


def test_an_unknown_period_is_a_bad_request(summary_env):
    """Only the windows the strip offers are answerable."""
    summary_env([], {})

    with pytest.raises(HTTPException) as exc:
        _summary("1Y")

    assert exc.value.status_code == 400


def test_access_is_checked_before_any_work(monkeypatch):
    """No server access, no walk.

    Since SEC-147 the guard is the ``require_server_access`` dependency, which
    FastAPI resolves before the endpoint coroutine is ever awaited — so "before
    any work" is structural, not a matter of statement order in the body. That
    the route carries the dependency is pinned by
    ``tests/test_web_server_access_dependency.py``.
    """
    from condor.web.auth import require_server_access
    from condor.web.models import WebUser

    monkeypatch.setattr(
        "condor.web.auth.get_config_manager",
        lambda: type(
            "_Denied",
            (),
            {"has_server_access": lambda self, *a, **kw: False},
        )(),
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_server_access("srv", user=WebUser(id=1, role="user")))

    assert exc.value.status_code == 403


# ── the pure aggregation ──


def test_summarize_groups_by_quote_and_stays_currency_blind():
    """The helper never mixes quotes: conversion is the caller's job."""
    now = time.time()
    rows = [
        _executor(0, pair="ETH-BTC", pnl=1.0, volume=2.0),
        _executor(1, pair="ETH-BTC", pnl=3.0, volume=4.0),
        _executor(2, pair="SOL-USDT", pnl=5.0, volume=6.0),
        _executor(3, age_days=30, pair="SOL-USDT", pnl=99.0, volume=99.0),
    ]

    totals = summarize_executors_by_quote(rows, now - _DAY)

    assert totals["BTC"] == {"pnl": 4.0, "volume": 6.0, "count": 2}
    assert totals["USDT"] == {"pnl": 5.0, "volume": 6.0, "count": 1}
