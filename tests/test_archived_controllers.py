"""An archived run splits into the controllers that ran inside it (FEAT-079).

Condor had no controller dimension on its history: ``controller_id`` arrives on
every archived executor and nothing grouped by it, so a run with three
controllers read as one number. These pin the rollup's three obligations — that
a controller-less executor still appears, that money is USD, and that the parts
add up to the run.
"""

from condor.archived_controllers import group_by_controller
from condor.archived_pnl import calculate_pnl_from_executors
from condor.quote_conversion import QuoteRates
from condor.web.models import NormalizedExecutor


def _ex(**kwargs) -> NormalizedExecutor:
    base = dict(
        id="e",
        connector="binance",
        trading_pair="SOL-USDC",
        side="BUY",
        pnl=1.0,
        volume=100.0,
        cum_fees_quote=0.1,
        timestamp=1_700_000_000.0,
        close_timestamp=1_700_000_060.0,
        usd_rate=1.0,
    )
    base.update(kwargs)
    return NormalizedExecutor(**base)


def test_one_row_per_controller_ordered_by_absolute_pnl():
    rollups = group_by_controller(
        [
            _ex(controller_id="alpha", pnl=1.0),
            _ex(controller_id="beta", pnl=-9.0),
            _ex(controller_id="alpha", pnl=2.0),
        ]
    )

    assert [r.controller_id for r in rollups] == ["beta", "alpha"]
    assert rollups[1].executor_count == 2
    assert rollups[1].pnl_usd == 3.0


def test_executors_without_a_controller_collapse_into_one_row():
    """An LP or manual run has no controller and still has to appear."""
    rollups = group_by_controller(
        [_ex(controller_id=""), _ex(controller_id=""), _ex(controller_id="alpha")]
    )

    empty = [r for r in rollups if r.controller_id == ""]
    assert len(empty) == 1
    assert empty[0].executor_count == 2


def test_money_is_usd_per_the_executors_own_market():
    """A BRL-quoted controller is scaled by its own rate, not the run's."""
    rollups = group_by_controller(
        [
            _ex(
                controller_id="brl",
                trading_pair="BTC-BRL",
                pnl=100.0,
                volume=1000.0,
                cum_fees_quote=10.0,
                usd_rate=0.19,
            ),
            _ex(controller_id="usd", pnl=100.0, volume=1000.0, cum_fees_quote=10.0),
        ]
    )

    brl = next(r for r in rollups if r.controller_id == "brl")
    assert brl.pnl_usd == 19.0
    assert brl.volume_usd == 190.0
    assert round(brl.fees_usd, 6) == 1.9


def test_rollups_sum_to_the_run_totals():
    """The parts add up: same executors, same USD totals as the run header."""
    executors = [
        _ex(controller_id="alpha", pnl=3.5, volume=120.0, cum_fees_quote=0.2),
        _ex(controller_id="beta", pnl=-1.25, volume=80.0, cum_fees_quote=0.1),
        _ex(
            controller_id="",
            trading_pair="BTC-BRL",
            pnl=10.0,
            volume=500.0,
            cum_fees_quote=1.0,
            usd_rate=0.19,
        ),
    ]
    rates = QuoteRates({"USDC": 1.0, "BRL": 0.19}, True)

    run = calculate_pnl_from_executors(executors, rates)
    rollups = group_by_controller(executors)

    assert round(sum(r.pnl_usd for r in rollups), 6) == round(run["total_pnl"], 6)
    assert round(sum(r.volume_usd for r in rollups), 6) == round(run["total_volume"], 6)
    assert round(sum(r.fees_usd for r in rollups), 6) == round(run["total_fees"], 6)
    assert sum(r.executor_count for r in rollups) == len(executors)


def test_timestamps_span_the_controllers_activity():
    rollups = group_by_controller(
        [
            _ex(controller_id="a", timestamp=100.0, close_timestamp=200.0),
            # Never closed: its open stamp must not drag the window to zero.
            _ex(controller_id="a", timestamp=300.0, close_timestamp=0.0),
        ]
    )

    assert rollups[0].first_ts == 100.0
    assert rollups[0].last_ts == 300.0


def test_markets_are_listed_once_in_the_order_they_appeared():
    rollups = group_by_controller(
        [
            _ex(controller_id="a", trading_pair="SOL-USDC", connector="binance"),
            _ex(controller_id="a", trading_pair="BTC-USDC", connector="binance"),
            _ex(controller_id="a", trading_pair="SOL-USDC", connector="kucoin"),
        ]
    )

    assert rollups[0].trading_pairs == ["SOL-USDC", "BTC-USDC"]
    assert rollups[0].connectors == ["binance", "kucoin"]


def test_no_executors_is_no_rows():
    assert group_by_controller([]) == []
