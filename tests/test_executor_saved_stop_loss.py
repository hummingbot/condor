"""A saved stop-loss default is range-checked like an argument (MH-09 follow-up).

The argument check only saw the ``stop_loss`` the call passed. Saved defaults
merge underneath the call, so omitting the argument let a hand-edited or legacy
``stop_loss: 2`` on file reach the backend as a 200% stop that never triggers.
The effective config is now checked after the merge, before anything is sent,
and the refusal says the bad value came from the saved defaults.
"""

import asyncio

import pytest

from mcp_servers.hummingbot_api.hummingbot_client import trading_rules_cache
from mcp_servers.hummingbot_api.tools import executor_create


class _Client:
    def __init__(self):
        self.creates = []
        outer = self

        class _Executors:
            @staticmethod
            async def create_executor(executor_config, account_name, controller_id):
                outer.creates.append(executor_config)
                return {"executor_id": "exec-1"}

        class _Connectors:
            @staticmethod
            async def get_trading_rules(connector_name, trading_pairs=None):
                return {}

        class _MarketData:
            @staticmethod
            async def get_prices(connector_name, trading_pairs):
                return {}

        class _Portfolio:
            @staticmethod
            async def get_state(
                account_names=None, connector_names=None, refresh=False
            ):
                return {}

        self.executors = _Executors()
        self.connectors = _Connectors()
        self.market_data = _MarketData()
        self.portfolio = _Portfolio()


@pytest.fixture
def saved(monkeypatch):
    """Set the saved defaults per executor type for one test."""
    store: dict[str, dict] = {}
    monkeypatch.setattr(executor_create, "READ_BACK_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(executor_create, "READ_BACK_WINDOW_SECONDS", 0.001)
    monkeypatch.setattr(
        executor_create.executor_preferences,
        "get_defaults",
        lambda executor_type: dict(store.get(executor_type, {})),
    )
    trading_rules_cache.clear()
    yield store
    trading_rules_cache.clear()


def _position(client, **overrides):
    kwargs = {
        "connector_name": "binance_perpetual",
        "trading_pair": "SOL-USDT",
        "side": 1,
        "amount": 1.0,
    }
    kwargs.update(overrides)
    return asyncio.run(executor_create.create_position_executor(client, **kwargs))


def _dca(client, **overrides):
    kwargs = {
        "connector_name": "binance_perpetual",
        "trading_pair": "SOL-USDT",
        "side": 1,
        "amounts_quote": [100, 100],
        "prices": [150, 140],
    }
    kwargs.update(overrides)
    return asyncio.run(executor_create.create_dca_executor(client, **kwargs))


@pytest.mark.parametrize("bad", [2, 1, 0, -0.02, "2", True])
def test_a_saved_position_stop_loss_that_is_not_a_fraction_is_refused(saved, bad):
    saved["position_executor"] = {"triple_barrier_config": {"stop_loss": bad}}
    client = _Client()

    result = _position(client)

    assert client.creates == []
    assert "stop_loss is a decimal FRACTION" in result["error"]
    assert "saved defaults for position_executor" in result["error"]
    assert "triple_barrier_config.stop_loss" in result["error"]
    assert "Nothing was created" in result["formatted_output"]


def test_a_saved_dca_stop_loss_that_is_not_a_fraction_is_refused(saved):
    saved["dca_executor"] = {"stop_loss": 2}
    client = _Client()

    result = _dca(client)

    assert client.creates == []
    assert "saved defaults for dca_executor" in result["error"]
    assert "For a 2% stop pass 0.02" in result["error"]


def test_a_valid_argument_overrides_a_bad_saved_stop_loss(saved):
    saved["position_executor"] = {"triple_barrier_config": {"stop_loss": 2}}
    saved["dca_executor"] = {"stop_loss": 2}
    client = _Client()

    position = _position(client, stop_loss=0.02)
    dca = _dca(client, stop_loss=0.03)

    assert "error" not in position and "error" not in dca
    assert client.creates[0]["triple_barrier_config"]["stop_loss"] == 0.02
    assert client.creates[1]["stop_loss"] == 0.03


def test_a_valid_saved_stop_loss_still_applies(saved):
    saved["position_executor"] = {"triple_barrier_config": {"stop_loss": 0.02}}
    client = _Client()

    result = _position(client)

    assert "error" not in result
    assert client.creates[0]["triple_barrier_config"]["stop_loss"] == 0.02


def test_a_bad_stop_loss_in_any_executor_types_defaults_is_refused(saved):
    # Grid never saves a stop_loss itself, but a hand-edited file can carry one.
    saved["grid_executor"] = {"triple_barrier_config": {"stop_loss": 5}}
    client = _Client()

    result = asyncio.run(
        executor_create.create_grid_executor(
            client,
            connector_name="binance",
            trading_pair="SOL-USDT",
            side=1,
            start_price=140,
            end_price=160,
            limit_price=130,
            total_amount_quote=100,
            take_profit=0.002,
        )
    )

    assert client.creates == []
    assert "saved defaults for grid_executor" in result["error"]


def test_the_check_names_an_argument_as_the_source_when_it_was_passed():
    message = executor_create.effective_stop_loss_violation(
        "dca_executor", {"stop_loss": 2}, {"stop_loss": 2}
    )

    assert "was passed in this call" in message
    assert "saved defaults" not in message
