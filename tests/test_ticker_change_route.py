"""Tests for FEAT-053: the tickers route joins change onto the rows it serves.

The contract the market browser's column rests on. A pair present in the
reference gets a percentage *and* the window it was measured over; a pair that
was not listed then gets neither, rather than a change against zero; and with no
history at all the route answers exactly what it answered before the feature.
"""

import asyncio

import pytest

import condor.web.routes.market as market_module
from condor import ticker_history
from condor.web.models import WebUser

_USER = WebUser(id=1, role="admin")

HOUR = ticker_history.SNAPSHOT_INTERVAL_S


@pytest.fixture(autouse=True)
def _fresh_rings():
    ticker_history.reset()
    yield
    ticker_history.reset()


def _pool(prices: dict[str, float]) -> dict:
    return {
        "connectors": {
            "binance": {p: {"price": v, "quote_volume": 1.0} for p, v in prices.items()}
        }
    }


def _tickers_now(prices: dict[str, float]) -> dict:
    return {
        "tickers": {
            p: {"price": v, "base_volume": 1.0, "quote_volume": v, "usd_volume": v}
            for p, v in prices.items()
        },
        "updated_at": 1.0,
    }


def _call(monkeypatch, live: dict[str, float]):
    async def fake_get_connector_tickers(server, connector):
        return _tickers_now(live)

    import condor.market_rates as market_rates

    monkeypatch.setattr(
        market_rates, "get_connector_tickers", fake_get_connector_tickers
    )
    response = asyncio.run(
        market_module.get_tickers(name="srv", connector="binance", user=_USER)
    )
    return {t.trading_pair: t for t in response.tickers}


def test_change_and_window_ride_each_row(monkeypatch):
    ticker_history.record("srv", _pool({"BTC-USDT": 100.0}), now=0.0)

    rows = _call(monkeypatch, {"BTC-USDT": 110.0})

    assert rows["BTC-USDT"].change_pct == pytest.approx(10.0)
    # The window is whatever was actually measured, not a promised 24h.
    assert rows["BTC-USDT"].change_window_s == pytest.approx(
        ticker_history.reference("srv")[1], rel=1e-3
    )


def test_a_pair_listed_after_the_reference_has_no_change(monkeypatch):
    ticker_history.record("srv", _pool({"BTC-USDT": 100.0}), now=0.0)

    rows = _call(monkeypatch, {"BTC-USDT": 110.0, "NEW-USDT": 3.0})

    assert rows["NEW-USDT"].change_pct is None
    assert rows["NEW-USDT"].change_window_s is None


def test_no_history_leaves_every_row_untouched(monkeypatch):
    rows = _call(monkeypatch, {"BTC-USDT": 110.0, "ETH-USDT": 3.0})

    assert [t.change_pct for t in rows.values()] == [None, None]
    assert [t.change_window_s for t in rows.values()] == [None, None]
    # And the rest of the payload is what it always was.
    assert rows["BTC-USDT"].price == 110.0
    assert rows["BTC-USDT"].usd_volume == 110.0


def test_a_zero_price_never_reads_as_minus_one_hundred_percent(monkeypatch):
    ticker_history.record("srv", _pool({"BTC-USDT": 100.0}), now=0.0)

    rows = _call(monkeypatch, {"BTC-USDT": 0.0})

    assert rows["BTC-USDT"].change_pct is None


def test_change_is_scoped_to_the_connector_asked_for(monkeypatch):
    """A pair on kucoin must not price binance's row."""
    ticker_history.record(
        "srv",
        {"connectors": {"kucoin": {"BTC-USDT": {"price": 50.0}}}},
        now=0.0,
    )

    rows = _call(monkeypatch, {"BTC-USDT": 100.0})

    assert rows["BTC-USDT"].change_pct is None
