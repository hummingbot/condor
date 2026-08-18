"""CORR-168: a malformed upstream row costs one candle, not the whole chart.

The REST candle route used to run the fetcher with ``strict=True``, so a single
row whose value did not parse raised out of the handler as a 500 and the client
got no chart at all — while the WS paths on the very same payload dropped that
one row and carried on. The route now degrades the same way, and the drop is
logged so a payload bug is still discoverable instead of a silent hole.
"""

import asyncio
import logging

import pytest

import condor.web.routes.market as market
from condor.fetchers.market_data import fetch_historical_candles
from condor.web.models import WebUser

GOOD = {
    "timestamp": 1000.0,
    "open": 1.0,
    "high": 2.0,
    "low": 0.5,
    "close": 1.5,
    "volume": 10.0,
}
# `open` is not a number: float() raises ValueError on this row and only this row.
MALFORMED = {**GOOD, "timestamp": 1060.0, "open": "n/a"}
ALSO_GOOD = {**GOOD, "timestamp": 1120.0}


@pytest.fixture(autouse=True)
def _clean_state():
    market._candle_cache.clear()
    market._candle_inflight.clear()
    yield
    market._candle_cache.clear()
    market._candle_inflight.clear()


class _Cm:
    def __init__(self, client):
        self._client = client

    def has_server_access(self, user_id, name):
        return True

    async def get_client(self, name):
        return self._client


class _Client:
    """Returns a window with one unparseable row in the middle."""

    def __init__(self, rows=None, fail=False):
        self._rows = rows if rows is not None else [GOOD, MALFORMED, ALSO_GOOD]
        self._fail = fail

    @property
    def market_data(self):
        return self

    async def get_historical_candles(self, *a, **kw):
        if self._fail:
            raise RuntimeError("upstream down")
        return self._rows

    async def get_candles(self, *a, **kw):
        if self._fail:
            raise RuntimeError("upstream down")
        return []


def _call(monkeypatch, client, **overrides):
    monkeypatch.setattr(
        "condor.web.routes.market.get_config_manager", lambda: _Cm(client), raising=True
    )
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
    return asyncio.run(market.get_candles("srv", **params))


def test_one_malformed_row_no_longer_fails_the_whole_request(monkeypatch):
    candles = _call(monkeypatch, _Client())

    # The request succeeds, minus exactly the bad row.
    assert [c.timestamp for c in candles] == [1000.0, 1120.0]


def test_the_dropped_row_is_logged_with_enough_context(caplog):
    """The gap is not silent: one warning per fetch names the row and the pair."""

    class _Upstream:
        @property
        def market_data(self):
            return self

        async def get_historical_candles(self, *a, **kw):
            return [GOOD, MALFORMED, ALSO_GOOD]

        async def get_candles(self, *a, **kw):
            return []

    with caplog.at_level(logging.WARNING, logger="condor.fetchers.market_data"):
        rows = asyncio.run(
            fetch_historical_candles(
                _Upstream(),
                "binance",
                "BTC-USDT",
                "1m",
                start_time=1_700_000_000,
                end_time=1_700_003_600,
                limit=100,
                fallback_on_error=True,
            )
        )

    assert len(rows) == 2
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "one line per fetch, not one per bad row"
    message = warnings[0].getMessage()
    assert "1 of 3" in message
    assert "BTC-USDT" in message
    assert "n/a" in message, "the offending row itself must be in the log"


def test_a_clean_window_logs_nothing(caplog):
    class _Upstream:
        @property
        def market_data(self):
            return self

        async def get_historical_candles(self, *a, **kw):
            return [GOOD, ALSO_GOOD]

        async def get_candles(self, *a, **kw):
            return []

    with caplog.at_level(logging.WARNING, logger="condor.fetchers.market_data"):
        rows = asyncio.run(
            fetch_historical_candles(
                _Upstream(),
                "binance",
                "BTC-USDT",
                "1m",
                start_time=1_700_000_000,
                limit=100,
            )
        )

    assert len(rows) == 2
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_a_genuine_upstream_outage_is_still_an_upstream_error(monkeypatch):
    """Degrading on a bad row must not swallow a real failure."""
    with pytest.raises(Exception) as exc:
        _call(monkeypatch, _Client(fail=True))

    # Wrapped as the route's upstream error, not leaked as the raw RuntimeError.
    assert not isinstance(exc.value, RuntimeError)
    assert getattr(exc.value, "status_code", None) in (502, 503)
