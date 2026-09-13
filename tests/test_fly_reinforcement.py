"""P&L delta → dopamine pulse kind, and the controller net-P&L reader."""

from decimal import Decimal

import pytest

from condor.fly.reinforcement import controller_net, reinforcement


@pytest.mark.parametrize(
    "equity,expected",
    [("100.03", "reward"), ("99.97", "aversive"), ("100.001", "none"), ("100", "none")],
)
def test_explicit_feedback(equity, expected):
    kind, delta = reinforcement(equity, "100", ".01")
    assert kind == expected
    assert delta == Decimal(equity) - Decimal("100")


def test_deadband_must_be_positive():
    with pytest.raises(ValueError):
        reinforcement("1", "0", "0")


def test_controller_net_includes_unrealized():
    assert (
        controller_net({"realized_pnl_quote": "1.5", "unrealized_pnl_quote": -2})
        == -0.5
    )
    with pytest.raises(ValueError):
        controller_net({"realized_pnl_quote": 1})
    with pytest.raises(ValueError):
        controller_net({"realized_pnl_quote": float("inf"), "unrealized_pnl_quote": 0})
