"""A BRL-quoted run must not be reported behind a dollar sign.

Most of the archived history here is BRL-quoted. PnL, fees and volume come out
of a run denominated in its market's quote currency, so rendering them as USD
without conversion overstated every such run by the whole BRL/USD rate — a
BTC-BRL run reading "+$395.41" had in fact made about $75.

These pin the conversion and, just as importantly, the refusal to guess: a quote
with no path to USD is reported in its own currency and flagged, never passed
off as dollars at parity.
"""

import asyncio

import pytest

from condor import quote_conversion as qc
from condor.archived_chart_series import build_chart_series
from condor.archived_pnl import calculate_pnl_from_executors
from tests.test_archived_chart_series import _ex

BRL_RATE = 0.19136741587009978


@pytest.fixture(autouse=True)
def _no_ambient_rates(monkeypatch):
    """Fail loudly if a test reaches the network instead of stubbing rates."""

    async def _boom(server, trading_pairs, connector=None):
        raise AssertionError(f"unstubbed rate lookup for {trading_pairs}")

    monkeypatch.setattr("condor.market_rates.get_rates", _boom)


def _stub_rates(monkeypatch, table: dict[str, float | None]):
    async def _fake(server, trading_pairs, connector=None):
        return {p: table.get(p) for p in trading_pairs}

    monkeypatch.setattr("condor.market_rates.get_rates", _fake)


def test_quote_of_reads_the_quote_side():
    assert qc.quote_of("BTC-BRL") == "BRL"
    assert qc.quote_of("btc-usdt") == "USDT"
    assert qc.quote_of("") == ""
    assert qc.quote_of("NOTAPAIR") == ""


def test_quotes_in_collects_distinct_quotes():
    assert qc.quotes_in(["BTC-BRL", "ETH-BRL", "SOL-USDC", ""]) == {"BRL", "USDC"}


def test_stablecoin_quotes_resolve_without_a_lookup():
    """The autouse fixture makes any network call an error, so this proves it."""
    rates = asyncio.run(qc.resolve_usd_rates("srv", {"USDT", "USDC", "FDUSD"}))

    assert rates.converted is True
    assert rates.rates == {"USDT": 1.0, "USDC": 1.0, "FDUSD": 1.0}
    assert rates.for_pair("BTC-USDT") == 1.0


def test_fiat_quote_resolves_through_the_rate_engine(monkeypatch):
    _stub_rates(monkeypatch, {"BRL-USDT": BRL_RATE})

    rates = asyncio.run(qc.resolve_usd_rates("srv", {"BRL"}))

    assert rates.converted is True
    assert rates.for_pair("BTC-BRL") == pytest.approx(BRL_RATE)


def test_unresolvable_quote_is_flagged_not_defaulted(monkeypatch):
    """1.0 would silently claim 1 BRL is 1 USD — the exact bug being fixed."""
    _stub_rates(monkeypatch, {"BRL-USDT": None})

    rates = asyncio.run(qc.resolve_usd_rates("srv", {"BRL"}))

    assert rates.converted is False
    assert "BRL" not in rates.rates
    # Callers still render *something*, but the flag says it is not dollars.
    assert rates.for_pair("BTC-BRL") == 1.0


def test_unreachable_rate_service_degrades_without_raising(monkeypatch):
    async def _down(server, trading_pairs, connector=None):
        raise RuntimeError("ticker pool unreachable")

    monkeypatch.setattr("condor.market_rates.get_rates", _down)

    rates = asyncio.run(qc.resolve_usd_rates("srv", {"BRL", "USDT"}))

    assert rates.converted is False
    # The stablecoin half still resolved; one bad quote is not fatal to the rest.
    assert rates.rates == {"USDT": 1.0}


def test_mixed_quotes_each_get_their_own_rate(monkeypatch):
    _stub_rates(monkeypatch, {"BRL-USDT": 0.2, "EUR-USDT": 1.1})

    rates = asyncio.run(qc.resolve_usd_rates("srv", {"BRL", "EUR", "USDC"}))

    assert rates.converted is True
    assert rates.for_pair("BTC-BRL") == 0.2
    assert rates.for_pair("BTC-EUR") == 1.1
    assert rates.for_pair("BTC-USDC") == 1.0


# ── Trade conversion ──


def test_converting_trades_restates_price_and_fee_only():
    """Amount is base-asset, so scaling price alone yields USD PnL and volume."""
    trades = [
        {
            "trading_pair": "BTC-BRL",
            "price": 400_000.0,
            "amount": 0.5,
            "trade_fee_in_quote": 10.0,
        }
    ]
    changed = qc.convert_trades_to_usd(trades, qc.QuoteRates({"BRL": 0.2}, True))

    assert changed == 1
    assert trades[0]["price"] == pytest.approx(80_000.0)
    assert trades[0]["trade_fee_in_quote"] == pytest.approx(2.0)
    assert trades[0]["amount"] == 0.5, "base amount is currency-agnostic"


def test_converting_trades_leaves_dollar_quotes_alone():
    trades = [{"trading_pair": "BTC-USDT", "price": 80_000.0, "amount": 1.0}]

    assert qc.convert_trades_to_usd(trades, qc.QuoteRates({"USDT": 1.0}, True)) == 0
    assert trades[0]["price"] == 80_000.0


def test_converting_trades_skips_an_unresolved_quote():
    """Untouched rows are what make the caller's `converted` flag honest."""
    trades = [{"trading_pair": "BTC-BRL", "price": 400_000.0, "amount": 1.0}]

    assert qc.convert_trades_to_usd(trades, qc.QuoteRates({}, False)) == 0
    assert trades[0]["price"] == 400_000.0


# ── Downstream aggregates ──


def test_executor_summary_converts_pnl_fees_and_volume():
    executors = [
        _ex(opened=100, closed=200, pnl=10.0, fees=1.0, volume=500.0, pair="BTC-BRL")
    ]
    rates = qc.QuoteRates({"BRL": 0.2}, True)

    stats = calculate_pnl_from_executors(executors, rates)

    assert stats["total_pnl"] == pytest.approx(2.0)
    assert stats["total_fees"] == pytest.approx(0.2)
    assert stats["total_volume"] == pytest.approx(100.0)
    assert stats["pnl_by_pair"] == {"BTC-BRL": pytest.approx(2.0)}
    assert stats["cumulative_pnl"][-1]["pnl"] == pytest.approx(2.0)


def test_mixed_quote_run_totals_in_one_currency():
    """Adding BRL to USDT without conversion is how a total becomes nonsense."""
    executors = [
        _ex(opened=100, closed=200, pnl=10.0, volume=100.0, pair="BTC-BRL"),
        _ex(opened=100, closed=300, pnl=10.0, volume=100.0, pair="BTC-USDT"),
    ]
    rates = qc.QuoteRates({"BRL": 0.2, "USDT": 1.0}, True)

    stats = calculate_pnl_from_executors(executors, rates)

    assert stats["total_pnl"] == pytest.approx(12.0)
    assert stats["total_volume"] == pytest.approx(120.0)


def test_chart_series_money_is_converted_but_position_is_not():
    executors = [
        _ex(
            opened=100,
            closed=200,
            side="BUY",
            pnl=10.0,
            fees=1.0,
            volume=500.0,
            pair="BTC-BRL",
            config={"amount": 3},
        )
    ]
    rates = qc.QuoteRates({"BRL": 0.2}, True)

    series = build_chart_series(executors, rates)["binance:BTC-BRL"]

    assert series["volume_buckets"][0]["buy_vol"] == pytest.approx(100.0)
    assert series["pnl_evolution"][-1]["net_pnl"] == pytest.approx(2.0)
    assert series["pnl_evolution"][-1]["cum_fees"] == pytest.approx(-0.2)
    # Base-asset exposure is not money and must not be scaled by an FX rate.
    assert series["position_deltas"][0]["delta"] == pytest.approx(3.0)


def test_chart_series_without_rates_stays_native():
    executors = [_ex(opened=100, closed=200, pnl=10.0, volume=500.0, pair="BTC-BRL")]

    series = build_chart_series(executors)["binance:BTC-BRL"]

    assert series["volume_buckets"][0]["buy_vol"] == pytest.approx(500.0)
    assert series["pnl_evolution"][-1]["net_pnl"] == pytest.approx(10.0)
