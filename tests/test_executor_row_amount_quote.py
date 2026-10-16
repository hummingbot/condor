"""An executor row's ``amount`` is its size in QUOTE currency, for every type.

The providers sum it into ``total_exposure``, which is both the drawdown's
denominator (``JournalManager.get_drawdown_pct``) and the book the position cap
adds a new create to (``RiskEngine.check_executor_action``). A position/order
executor's config ``amount`` is BASE, and it used to flow through unpriced: a
~$200 LTC position counted as $2.90, so a $0.70 loss read as a 24% drawdown and
tripped the kill switch -- while a $300 BTC position counted as $0.003 and left
the $500 cap effectively open.
"""

import asyncio

import pytest

from condor.agents.journal import JournalManager
from condor.agents.performance import AgentPerformance, _executor_row
from condor.agents.providers.executors import ExecutorsProvider
from condor.agents.risk import RiskEngine, RiskLimits, RiskState
from condor.fetchers.executors import build_executor_row


def _ltc_position(**overrides):
    ex = {
        "id": "ltc-1",
        "status": "RUNNING",
        "filled_amount_quote": 200.1,
        "config": {
            "type": "position_executor",
            "connector_name": "bitget_perpetual",
            "trading_pair": "LTC-USDT",
            "side": "BUY",
            "amount": 2.9,
            "entry_price": 69.0,
        },
        "custom_info": {"current_price": 68.76},
    }
    ex.update(overrides)
    return ex


def test_position_executor_base_amount_is_priced_at_entry():
    assert build_executor_row(_ltc_position())["amount"] == pytest.approx(200.1)


def test_market_position_without_entry_is_priced_at_average_fill():
    ex = _ltc_position()
    del ex["config"]["entry_price"]
    ex["custom_info"]["current_position_average_price"] = 70.0

    assert build_executor_row(ex)["amount"] == pytest.approx(203.0)


def test_limit_order_executor_is_priced_at_its_limit_price():
    ex = {
        "config": {"type": "order_executor", "amount": 0.003, "price": 100_000.0},
    }

    assert build_executor_row(ex)["amount"] == pytest.approx(300.0)


def test_market_order_executor_is_priced_at_its_fill():
    ex = {
        "config": {"type": "order_executor", "amount": 0.003},
        "custom_info": {"held_position_orders": [{"price": 100_000.0}]},
    }

    assert build_executor_row(ex)["amount"] == pytest.approx(300.0)


def test_unpriceable_base_amount_falls_back_to_filled_quote_never_base():
    ex = {"filled_amount_quote": 150.0, "config": {"amount": 2.9}}
    assert build_executor_row(ex)["amount"] == 150.0

    assert build_executor_row({"config": {"amount": 2.9}})["amount"] == 0.0


@pytest.mark.parametrize(
    "ex,expected",
    [
        ({"config": {"type": "grid_executor", "total_amount_quote": 300}}, 300.0),
        ({"config": {"type": "dca_executor", "amounts_quote": [50, "50"]}}, 100.0),
        (
            {
                "config": {"type": "lp_executor"},
                "custom_info": {"total_value_quote": 9.97},
            },
            9.97,
        ),
    ],
)
def test_quote_denominated_types_are_read_as_is(ex, expected):
    assert build_executor_row(ex)["amount"] == pytest.approx(expected)


def test_ltc_loss_is_measured_against_its_quote_size(monkeypatch, tmp_path):
    """The reported incident, end to end: provider exposure -> journal drawdown."""

    async def _fake(client, agent_id, **_kw):
        return AgentPerformance(
            agent_id=agent_id, executors=[_executor_row(_ltc_position())]
        )

    monkeypatch.setattr("condor.agents.performance.fetch_agent_performance", _fake)
    result = asyncio.run(ExecutorsProvider().execute(object(), {}, agent_id="a_1"))
    exposure = result.data["total_exposure"]
    assert exposure == pytest.approx(200.1)

    journal = JournalManager("a_1", session_dir=tmp_path)
    journal.record_snapshot(
        total_pnl=0.0, total_volume=200, open_count=1, position_size=exposure
    )
    journal.record_snapshot(
        total_pnl=-0.70, total_volume=200, open_count=1, position_size=exposure
    )

    assert journal.get_drawdown_pct() == pytest.approx(0.35, abs=0.01)


def _pending_market_order():
    """A market order still pending at the next tick: no price, no fill."""
    return {
        "id": "hype-1",
        "status": "RUNNING",
        "config": {
            "type": "order_executor",
            "connector_name": "hyperliquid_perpetual",
            "trading_pair": "HYPE-USD",
            "side": "BUY",
            "amount": 2.0,
        },
    }


def _provider_data(monkeypatch, price):
    async def _fake(client, agent_id, **_kw):
        return AgentPerformance(
            agent_id=agent_id, executors=[_executor_row(_pending_market_order())]
        )

    async def _price(client, connector_name="", trading_pair="", **_kw):
        assert (connector_name, trading_pair) == ("hyperliquid_perpetual", "HYPE-USD")
        return price

    monkeypatch.setattr("condor.agents.performance.fetch_agent_performance", _fake)
    monkeypatch.setattr("condor.fetchers.market_data.fetch_current_price", _price)
    return asyncio.run(ExecutorsProvider().execute(object(), {}, agent_id="a_1")).data


def test_pending_market_order_keeps_its_exposure_on_the_next_tick(monkeypatch):
    """$150 cap, a $100 market order still pending: a further $60 is refused."""
    data = _provider_data(monkeypatch, price=50.0)
    assert data["total_exposure"] == pytest.approx(100.0)
    assert data["unpriced_exposure"] == []

    state = RiskState(total_exposure=data["total_exposure"], executor_count=1)
    allowed, reason = RiskEngine(
        RiskLimits(max_position_size_quote=150.0)
    ).check_executor_action(
        {"tool": "create_order_executor", "input": {"amount": 1.2}}, state, 60.0
    )

    assert not allowed
    assert "position limit" in reason


def test_pending_order_with_no_price_is_reported_unpriced_not_flat(monkeypatch):
    data = _provider_data(monkeypatch, price=None)

    assert data["total_exposure"] == 0.0
    assert data["unpriced_exposure"] == ["hype-1"]
