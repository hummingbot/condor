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
from condor.web.routes.executors import _summary_cache, executors_summary

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

    def __init__(self, rows):
        self.calls = []
        self.executors = self._Executors(self)
        self._rows = rows

    class _Executors:
        def __init__(self, outer):
            self._outer = outer

        async def search_executors(self, **kwargs):
            outer = self._outer
            outer.calls.append(kwargs)
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
    yield
    _summary_cache.clear()


@pytest.fixture
def summary_env(monkeypatch):
    """Bind the endpoint to a fake client and a fixed rate table."""

    def _bind(rows, rates=None):
        client = FakeClient(rows)
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
    rows = [_executor(0, pnl=6.0)]
    summary_env(rows, {"USDT-USDT": 1.0})

    async def _boom(server, pairs, connector=None):
        raise RuntimeError("ticker pool unreachable")

    monkeypatch.setattr("condor.market_rates.get_rates", _boom)

    result = _summary("1D")

    assert result.pnl == pytest.approx(6.0)
    assert result.converted is False


# ── cost control ──


def test_the_aggregate_is_cached_per_period(summary_env):
    """A second reader inside the TTL re-walks nothing; another period does."""
    rows = [_executor(i, age_days=0.5) for i in range(10)]
    client = summary_env(rows, {"USDT-USDT": 1.0})

    _summary("1D")
    first_walk = len(client.calls)
    _summary("1D")

    assert len(client.calls) == first_walk, "the cached period must not re-walk"

    _summary("1W")
    assert len(client.calls) > first_walk, "a different period is a different total"


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
