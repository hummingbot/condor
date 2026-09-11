"""FEAT-079: the routine that charts an archived run stops walking it itself.

``archived_analyzer``'s detail mode had its own trade walk: no retries, no
executor fallback, and no quote conversion, so a BRL-quoted run was printed
behind a bare "$" and a run whose trades table was empty charted nothing at all.
It now reads through ``condor.fetchers.archived_run`` — the dashboard's own
fetch — gains a controller axis, and stamps the report with what it is about so
the same controller is never charted twice.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict

import pytest

from condor.archived_chart_series import _MAX_PNL_POINTS
from condor.fetchers import archived_run
from condor.quote_conversion import QuoteRates
from condor.reports import subjects
from tests.conftest import load_shared_routine

aa = load_shared_routine("archived_analyzer")

DB = "/data/bots/archive/run-20260830-120000.sqlite"
SERVER = "brigado"


def _executor(controller_id, pnl, pair="BTC-BRL", opened=1_700_000_000, closed=None):
    return {
        "id": f"{controller_id}-{pnl}-{opened}",
        "controller_id": controller_id,
        "connector": "binance",
        "trading_pair": pair,
        "side": "BUY",
        "net_pnl_quote": pnl,
        "filled_amount_quote": 1000.0,
        "cum_fees_quote": 1.0,
        "timestamp": opened,
        "close_timestamp": closed if closed is not None else opened + 60,
    }


EXECUTORS = [
    _executor("alpha", 100.0, opened=1_700_000_000),
    _executor("alpha", -20.0, opened=1_700_000_600),
    _executor("beta", 50.0, opened=1_700_001_200),
    # An LP leg that ran under no controller at all.
    _executor("", 10.0, opened=1_700_001_800),
]


class FakeArchivedBots:
    """A run with executors and an *empty* trades table — the fallback path."""

    def __init__(self, executors=None):
        self.executors = executors if executors is not None else EXECUTORS

    async def get_database_summary(self, db_path):
        return {
            "bot_name": "brigado-bot",
            "total_trades": 0,
            "trading_pairs": ["BTC-BRL"],
            "exchanges": ["binance"],
        }

    async def get_database_trades(self, db_path, limit=500, offset=0):
        return {"trades": []}

    async def get_database_executors(self, db_path):
        return {"executors": self.executors}


class FakeClient:
    def __init__(self, executors=None):
        self.archived_bots = FakeArchivedBots(executors)


@pytest.fixture(autouse=True)
def _brl_rate_and_cold_cache(monkeypatch):
    """One BRL rate, and never a warm entry from a neighbouring test."""

    async def _rates(server, quotes):
        return QuoteRates({"BRL": 0.2}, True)

    monkeypatch.setattr("condor.quote_conversion.resolve_usd_rates", _rates)
    monkeypatch.setattr(archived_run, "_performance_cache", OrderedDict())
    monkeypatch.setattr(archived_run, "_performance_inflight", {})


def _detail(controller_id="", client=None):
    return asyncio.run(
        aa._mode_detail(client or FakeClient(), SERVER, DB, controller_id)
    )


# ── The run ──


def test_a_run_with_no_trade_rows_still_charts():
    """stats_source == "executors": the old walk found nothing and gave up."""
    out = _detail()

    assert out.figure is not None
    assert out.volume_figure is not None
    assert "brigado-bot" in out.text


def test_money_is_usd_not_the_runs_own_quote():
    """140 BRL of executor PnL at 0.2 is $28.00, not "$140.00"."""
    out = _detail()

    pnl = next(kpi for kpi in out.kpis if kpi["label"] == "PnL")
    assert pnl["value"] == "$28.00"


def test_the_run_level_table_is_its_controllers():
    out = _detail()

    assert out.columns[0] == "Controller"
    assert [row["Controller"] for row in out.table] == [
        "alpha",
        "beta",
        "(no controller)",
    ]


# ── One controller of it ──


def test_a_controller_reports_only_its_own_executors():
    out = _detail("alpha")

    pnl = next(kpi for kpi in out.kpis if kpi["label"] == "PnL")
    executors = next(kpi for kpi in out.kpis if kpi["label"] == "Executors")
    assert pnl["value"] == "$16.00"  # (100 - 20) BRL at 0.2
    assert executors["value"] == "2"


def test_a_controller_level_table_is_its_markets():
    out = _detail("alpha")

    assert out.columns[0] == "Pair"
    assert [row["Pair"] for row in out.table] == ["BTC-BRL"]


def test_a_controller_that_never_ran_says_so_rather_than_charting_nothing():
    out = _detail("ghost")

    assert "no executors ran under controller 'ghost'" in out.text
    assert out.figure is None


def test_the_unattributed_executors_are_reachable_as_their_own_controller():
    out = _detail("")
    assert out.table  # the run

    only_lp = _detail("beta")
    assert next(k for k in only_lp.kpis if k["label"] == "Executors")["value"] == "1"


# ── Figures stay bounded ──


def test_the_curve_comes_from_the_bucketed_series_not_from_every_executor():
    """A 5k-executor run must not put 5k points on the line (PERF-108)."""
    many = [
        _executor("alpha", 1.0, opened=1_700_000_000 + i * 60) for i in range(5_000)
    ]
    out = _detail(client=FakeClient(many))

    # Integer-step thinning, so the bound is twice the target plus the final
    # point it always keeps — an order of magnitude under one point per executor.
    points = len(out.figure.data[0].x)
    assert 0 < points <= 2 * _MAX_PNL_POINTS + 1


def test_volume_buckets_are_bounded_by_the_candle_ladder():
    many = [
        _executor("alpha", 1.0, opened=1_700_000_000 + i * 60) for i in range(5_000)
    ]
    out = _detail(client=FakeClient(many))

    # 5000 minutes ≈ 3.5 days, so the ladder picks 15m: ~333 buckets, not 5000.
    assert len(out.volume_figure.data[0].x) < 500


# ── The report says what it is about ──


def test_the_report_subject_names_the_server_run_and_controller():
    assert aa.subjects is subjects
    key = subjects.bot_run(SERVER, DB, "alpha")

    assert key != subjects.bot_run(SERVER, DB)
    assert key != subjects.bot_run("other", DB, "alpha")


def test_the_title_names_the_controller_it_charted():
    run_title = aa._report_title(aa.Config(mode="detail", db_path=DB))
    ctrl_title = aa._report_title(
        aa.Config(mode="detail", db_path=DB, controller_id="alpha")
    )

    assert run_title == "Archived Bot — run-20260830-120000"
    assert ctrl_title == "Archived Bot — run-20260830-120000 · alpha"
