"""The portfolio's P&L is what prices did, not what was deposited.

It used to be ``last.total_usd - first.total_usd``, so putting $200 into an
account read as a $200 gain while the holdings were losing money.
"""

from condor.web.models import PortfolioHistoryPoint
from condor.web.routes.portfolio import _apply_market_pnl


def _snap(ts, rows):
    return (ts, {"timestamp": ts, "state": {"master": {"binance": rows}}})


def _run(entries):
    points = [PortfolioHistoryPoint(timestamp=ts) for ts, _ in entries]
    _apply_market_pnl(entries, False, points)
    return [round(p.pnl_usd, 6) for p in points]


def test_a_deposit_is_not_a_gain():
    entries = [
        _snap(1, [{"token": "USDT", "units": 100, "price": 1, "value": 100}]),
        _snap(2, [{"token": "USDT", "units": 300, "price": 1, "value": 300}]),
    ]
    assert _run(entries) == [0, 0]


def test_a_loss_shows_through_a_deposit():
    # 1 SOL falls from 150 to 120 (-30) while $200 of USDT is deposited.
    entries = [
        _snap(
            1,
            [
                {"token": "SOL", "units": 1, "price": 150, "value": 150},
                {"token": "USDT", "units": 0, "price": 1, "value": 0},
            ],
        ),
        _snap(
            2,
            [
                {"token": "SOL", "units": 1, "price": 120, "value": 120},
                {"token": "USDT", "units": 200, "price": 1, "value": 200},
            ],
        ),
    ]
    assert _run(entries) == [0, -30]


def test_a_withdrawal_is_not_a_loss():
    entries = [
        _snap(1, [{"token": "BTC", "units": 2, "price": 100, "value": 200}]),
        _snap(2, [{"token": "BTC", "units": 1, "price": 110, "value": 110}]),
    ]
    # Price moved +10 on the 2 BTC held going in; the 1 BTC that left is not a loss.
    assert _run(entries) == [0, 20]


def test_price_is_derived_from_value_when_missing():
    entries = [
        _snap(1, [{"token": "ETH", "units": 2, "value": 200}]),
        _snap(2, [{"token": "ETH", "units": 2, "value": 260}]),
    ]
    assert _run(entries) == [0, 60]


def test_rows_without_units_fall_back_to_value_change():
    entries = [
        _snap(1, [{"token": "BTC", "value": 100.0}]),
        _snap(2, [{"token": "BTC", "value": 120.0}]),
    ]
    assert _run(entries) == [0, 20]


def test_a_connector_missing_from_a_snapshot_carries_no_move():
    entries = [
        _snap(1, [{"token": "SOL", "units": 1, "price": 100, "value": 100}]),
        (2, {"timestamp": 2, "state": {"master": {}}}),
        _snap(3, [{"token": "SOL", "units": 1, "price": 90, "value": 90}]),
    ]
    assert _run(entries) == [0, 0, -10]
