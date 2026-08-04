"""Unit tests for the backtest comparison routine's pure logic.

Everything here runs against in-memory fakes: the real store holds ~16 MB
result files, so the tests exercise resolution, curve building and the
best-per-column ranking without touching disk.
"""

import pytest

from routines.backtest_compare import (
    MAX_CURVE_POINTS,
    Run,
    _best_task_ids,
    _build_curve,
    _downsample,
    _metrics_rows,
    _pct,
    _resolve_ids,
    _usd,
)


class FakeStore:
    """Stands in for BacktestStore: only the index is needed for resolution."""

    def __init__(self, task_ids, server="local"):
        self._index = {tid: {"server": server, "config": ""} for tid in task_ids}


def make_run(task_id, **metrics):
    return Run(
        task_id=task_id,
        config_id=metrics.pop("config_id", task_id),
        controller="pmm",
        connector="binance",
        pair="BTC-USDT",
        resolution="1m",
        start_ts=1776211200,
        end_ts=1776816000,
        metrics=metrics,
        close_types={},
    )


# -- ID resolution --


def test_resolves_exact_and_prefix_ids():
    store = FakeStore(["25edff0c", "43cf5b1b", "b6a27ba2"])
    resolved, errors = _resolve_ids(store, "25edff0c, 43cf")
    assert resolved == ["25edff0c", "43cf5b1b"]
    assert errors == []


def test_reports_unknown_and_ambiguous_ids():
    store = FakeStore(["25edff0c", "2de46530"])
    resolved, errors = _resolve_ids(store, "zzzz, 2")
    assert resolved == []
    assert "matches no saved backtest" in errors[0]
    assert "ambiguous" in errors[1]
    # Both candidates are named so the user can disambiguate.
    assert "25edff0c" in errors[1] and "2de46530" in errors[1]


def test_deduplicates_repeated_ids():
    store = FakeStore(["25edff0c"])
    resolved, errors = _resolve_ids(store, "25edff0c, 25ed, 25edff0c")
    assert resolved == ["25edff0c"]
    assert errors == []


def test_blank_input_resolves_to_nothing():
    store = FakeStore(["25edff0c"])
    assert _resolve_ids(store, "") == ([], [])
    assert _resolve_ids(store, " , ,") == ([], [])


# -- curve building --


def test_curve_prefers_pnl_timeseries():
    result = {
        "pnl_timeseries": [
            {"timestamp": 100, "total_pnl": 0.0},
            {"timestamp": 200, "total_pnl": 5.5},
        ],
        "executors": [],
    }
    assert _build_curve(result) == [(100.0, 0.0), (200.0, 5.5)]


def test_curve_falls_back_to_cumulative_executor_pnl():
    result = {
        "pnl_timeseries": [],
        "executors": [
            # Out of order on purpose: the curve must come out sorted by close.
            {
                "close_timestamp": 300,
                "filled_amount_quote": 10,
                "net_pnl_quote": 2.0,
                "close_type": "TAKE_PROFIT",
            },
            {
                "close_timestamp": 200,
                "filled_amount_quote": 10,
                "net_pnl_quote": 1.0,
                "close_type": "TAKE_PROFIT",
            },
        ],
    }
    assert _build_curve(result) == [(200.0, 1.0), (300.0, 3.0)]


def test_curve_fallback_excludes_holds_and_unfilled():
    result = {
        "pnl_timeseries": [],
        "executors": [
            {
                "close_timestamp": 100,
                "filled_amount_quote": 10,
                "net_pnl_quote": 1.0,
                "close_type": "TAKE_PROFIT",
            },
            # Still open at the end of the run — counting it double-counts PnL.
            {
                "close_timestamp": 200,
                "filled_amount_quote": 10,
                "net_pnl_quote": 99.0,
                "close_type": "POSITION_HOLD",
            },
            # Never filled, so it contributed nothing.
            {
                "close_timestamp": 300,
                "filled_amount_quote": 0,
                "net_pnl_quote": 50.0,
                "close_type": "EARLY_STOP",
            },
        ],
    }
    assert _build_curve(result) == [(100.0, 1.0)]


def test_curve_handles_empty_result():
    assert _build_curve({}) == []


def test_downsample_caps_points_and_keeps_endpoints():
    points = [(float(i), float(i)) for i in range(10_000)]
    sampled = _downsample(points)
    assert len(sampled) <= MAX_CURVE_POINTS + 1
    assert sampled[0] == points[0]
    assert sampled[-1] == points[-1]


def test_downsample_leaves_short_curves_untouched():
    points = [(1.0, 1.0), (2.0, 2.0)]
    assert _downsample(points) is points


# -- ranking --


def test_best_picks_highest_for_gains_and_smallest_drawdown():
    runs = [
        make_run("a", net_pnl_quote=10.0, sharpe_ratio=0.5, max_drawdown_usd=-100.0),
        make_run("b", net_pnl_quote=25.0, sharpe_ratio=0.1, max_drawdown_usd=-5.0),
    ]
    winners = _best_task_ids(runs)
    assert winners["net_pnl_quote"] == "b"
    assert winners["sharpe_ratio"] == "a"
    # Drawdown is judged on magnitude, so the shallowest loss wins.
    assert winners["max_drawdown_usd"] == "b"


def test_best_ignores_metrics_missing_everywhere():
    runs = [make_run("a", net_pnl_quote=1.0), make_run("b", net_pnl_quote=2.0)]
    assert "sharpe_ratio" not in _best_task_ids(runs)


def test_metrics_rows_star_the_winner_only():
    runs = [
        make_run("a", config_id="alpha", net_pnl_quote=10.0),
        make_run("b", config_id="beta", net_pnl_quote=2.0),
    ]
    rows = _metrics_rows(runs)
    assert rows[0]["Net PnL"] == "★ $10.00"
    assert rows[1]["Net PnL"] == "$2.00"
    # Absent metrics render as a dash rather than a bogus zero.
    assert rows[0]["Sharpe"] == "—"


# -- formatting --


@pytest.mark.parametrize(
    "value,expected",
    [
        (2549843.14, "$2,549,843.14"),
        (-59.81, "-$59.81"),
        (0, "$0.00"),
        (None, "—"),
    ],
)
def test_usd_uses_dollar_sign_and_thousands_separators(value, expected):
    assert _usd(value) == expected


def test_usd_honours_decimal_override():
    assert _usd(11964.56, 0) == "$11,965"


@pytest.mark.parametrize(
    "value,expected",
    [(0.0028, "0.28%"), (-0.0551, "-5.51%"), (None, "—")],
)
def test_pct_renders_stored_fractions_as_percentages(value, expected):
    assert _pct(value) == expected
