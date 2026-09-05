"""The archived-run chart must describe the whole run, not one executor page."""

from types import SimpleNamespace

from condor.archived_chart_series import (
    activity_range,
    build_chart_series,
    pick_interval,
)
from condor.archived_pnl import calculate_pnl_from_executors


def _ex(
    *,
    opened: float,
    closed: float = 0.0,
    side: str = "BUY",
    pnl: float = 0.0,
    fees: float = 0.0,
    volume: float = 0.0,
    entry_price: float = 0.0,
    connector: str = "binance",
    pair: str = "BTC-USDT",
    config: dict | None = None,
    custom_info: dict | None = None,
):
    return SimpleNamespace(
        timestamp=opened,
        close_timestamp=closed,
        side=side,
        pnl=pnl,
        cum_fees_quote=fees,
        volume=volume,
        entry_price=entry_price,
        connector=connector,
        trading_pair=pair,
        config=config or {},
        custom_info=custom_info or {},
    )


def test_interval_matches_run_length_not_archive_lag():
    """A 28-minute run charts at 1m, however long ago it was archived."""
    assert pick_interval(28 * 60).name == "1m"
    assert pick_interval(6 * 3600).name == "5m"
    assert pick_interval(2 * 86400).name == "15m"
    assert pick_interval(7 * 86400).name == "1h"
    assert pick_interval(30 * 86400).name == "4h"


def test_activity_range_spans_first_open_to_last_close():
    executors = [
        _ex(opened=1000, closed=1600),
        _ex(opened=1200, closed=2400),
        _ex(opened=900, closed=1100),
    ]
    assert activity_range(executors) == (900, 2400)


def test_activity_range_uses_open_stamp_for_never_closed_executor():
    """An open position must not pull the window start to the epoch."""
    executors = [_ex(opened=5000, closed=0), _ex(opened=4000, closed=4500)]
    assert activity_range(executors) == (4000, 5000)


def test_activity_range_empty():
    assert activity_range([]) == (0.0, 0.0)


def test_series_aggregates_every_executor():
    """The whole point: 500 executors, all of them in the volume histogram."""
    executors = [
        _ex(
            opened=1_700_000_000 + i * 30,
            closed=1_700_000_000 + i * 30 + 15,
            side="BUY" if i % 2 == 0 else "SELL",
            volume=10.0,
            pnl=0.01,
            fees=0.001,
        )
        for i in range(500)
    ]

    series = build_chart_series(executors)
    market = series["binance:BTC-USDT"]

    assert market["executor_count"] == 500
    # 500 executors 30s apart is a ~4h span -> 5m candles.
    assert market["interval"] == "5m"

    total_volume = sum(b["buy_vol"] + b["sell_vol"] for b in market["volume_buckets"])
    assert total_volume == 5000.0
    total_counted = sum(
        b["buy_count"] + b["sell_count"] for b in market["volume_buckets"]
    )
    assert total_counted == 500

    # Buckets arrive sorted, so the chart can draw them without re-sorting.
    times = [b["time"] for b in market["volume_buckets"]]
    assert times == sorted(times)


def test_series_splits_by_market():
    executors = [
        _ex(opened=100, closed=200, pair="BTC-USDT", volume=5),
        _ex(opened=100, closed=200, pair="ETH-USDT", volume=7),
        _ex(opened=100, closed=200, connector="kraken", pair="BTC-USDT", volume=9),
    ]
    series = build_chart_series(executors)
    assert set(series) == {"binance:BTC-USDT", "binance:ETH-USDT", "kraken:BTC-USDT"}
    assert series["binance:ETH-USDT"]["executor_count"] == 1


def test_series_skips_executors_without_a_market():
    """Nothing to chart them against, so they must not create a phantom key."""
    executors = [_ex(opened=100, closed=200, connector="", pair=""), _ex(opened=100)]
    series = build_chart_series(executors)
    assert set(series) == {"binance:BTC-USDT"}


def test_pnl_evolution_is_cumulative_and_ends_at_the_run_total():
    executors = [
        _ex(opened=100, closed=200, pnl=1.0, fees=0.1),
        _ex(opened=150, closed=300, pnl=-0.5, fees=0.2),
        _ex(opened=250, closed=400, pnl=2.0, fees=0.3),
    ]
    points = build_chart_series(executors)["binance:BTC-USDT"]["pnl_evolution"]

    assert [p["time"] for p in points] == [200, 300, 400]
    assert points[-1]["net_pnl"] == 2.5
    # Net is fee-inclusive; gross adds the fees back.
    assert round(points[-1]["trade_pnl"], 6) == round(2.5 + 0.6, 6)
    assert round(points[-1]["cum_fees"], 6) == -0.6


def test_pnl_evolution_ignores_open_executors():
    """An unrealized executor must not step the curve before the run earned it."""
    executors = [_ex(opened=100, closed=200, pnl=1.0), _ex(opened=150, pnl=99.0)]
    points = build_chart_series(executors)["binance:BTC-USDT"]["pnl_evolution"]
    assert len(points) == 1
    assert points[0]["net_pnl"] == 1.0


def test_pnl_evolution_downsamples_but_keeps_the_final_total():
    executors = [_ex(opened=i, closed=i + 1, pnl=1.0) for i in range(1_000, 11_000)]
    points = build_chart_series(executors)["binance:BTC-USDT"]["pnl_evolution"]

    assert len(points) <= 2_001
    assert points[-1]["net_pnl"] == 10_000.0


def test_position_deltas_running_sum_returns_to_flat():
    """Every close reverses its open, so a fully closed run ends flat."""
    executors = [
        _ex(opened=100, closed=200, side="BUY", config={"amount": 2}),
        _ex(opened=150, closed=900, side="SELL", config={"amount": 3}),
    ]
    deltas = build_chart_series(executors)["binance:BTC-USDT"]["position_deltas"]
    assert round(sum(d["delta"] for d in deltas), 8) == 0.0


def test_position_amount_falls_back_to_volume_over_entry_price():
    executors = [_ex(opened=100, closed=200, volume=100.0, entry_price=50.0)]
    deltas = build_chart_series(executors)["binance:BTC-USDT"]["position_deltas"]
    assert deltas[0]["delta"] == 2.0


def test_pool_address_is_carried_through_for_dex_runs():
    executors = [
        _ex(opened=100, closed=200),
        _ex(opened=100, closed=200, custom_info={"pool_address": "POOL123"}),
    ]
    series = build_chart_series(executors)["binance:BTC-USDT"]
    assert series["pool_address"] == "POOL123"


def test_pool_address_absent_for_cex_runs():
    series = build_chart_series([_ex(opened=100, closed=200)])["binance:BTC-USDT"]
    assert series["pool_address"] is None


# ── Executor-derived summary fallback ──


def test_summary_from_executors_matches_the_chart():
    """The header and the chart must not disagree about the same run."""
    executors = [
        _ex(opened=100, closed=200, pnl=1.5, fees=0.1, volume=100),
        _ex(opened=150, closed=300, pnl=-0.5, fees=0.2, volume=50, pair="ETH-USDT"),
    ]
    stats = calculate_pnl_from_executors(executors)

    assert stats["total_pnl"] == 1.0
    assert round(stats["total_fees"], 6) == 0.3
    assert stats["total_volume"] == 150
    assert stats["pnl_by_pair"] == {"BTC-USDT": 1.5, "ETH-USDT": -0.5}
    assert [p["pnl"] for p in stats["cumulative_pnl"]] == [1.5, 1.0]


def test_summary_from_executors_empty():
    stats = calculate_pnl_from_executors([])
    assert stats["total_pnl"] == 0
    assert stats["cumulative_pnl"] == []


def test_summary_from_executors_orders_by_close_time():
    """Executors arrive in database order; the curve must still be monotonic."""
    executors = [
        _ex(opened=100, closed=900, pnl=1.0),
        _ex(opened=100, closed=200, pnl=2.0),
    ]
    stats = calculate_pnl_from_executors(executors)
    assert [p["timestamp"] for p in stats["cumulative_pnl"]] == [200, 900]
    assert [p["pnl"] for p in stats["cumulative_pnl"]] == [2.0, 3.0]
