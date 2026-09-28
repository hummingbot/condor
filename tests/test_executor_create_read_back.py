"""A created executor is read back before the create is reported.

The API answers the POST before the executor has started, and the executor checks
its budget in ``on_start``: an unfunded one terminates at once with
``INSUFFICIENT_BALANCE``. The tool used to answer "Executor created successfully!"
with the id regardless, and an agent turned that into "Position opened" for a
position that never existed.

What is pinned here:

- an executor that terminates as INSUFFICIENT_BALANCE or FAILED is an error that
  says nothing was opened, and names the balances the connector holds;
- a running executor with no fill is reported as unconfirmed, never as success;
- a filled one reports the fill;
- a read-back that cannot see the executor says so instead of claiming either.

The repo has no async test setup, so coroutines are driven with asyncio.run().
"""

import asyncio

import pytest

from mcp_servers.hummingbot_api.hummingbot_client import trading_rules_cache
from mcp_servers.hummingbot_api.tools import executor_create


class _Client:
    """A backend that accepts the create and then shows a scripted executor state."""

    def __init__(self, states, balances=None, get_error=None):
        self.states = list(states)
        self.reads = 0
        outer = self

        class _Executors:
            @staticmethod
            async def create_executor(executor_config, account_name, controller_id):
                return {"executor_id": "exec-1"}

            @staticmethod
            async def get_executor(executor_id):
                outer.reads += 1
                if get_error is not None:
                    raise get_error
                index = min(outer.reads, len(outer.states)) - 1
                return outer.states[index]

        class _Portfolio:
            @staticmethod
            async def get_state(account_names=None, connector_names=None, **_kw):
                return balances or {}

        class _Connectors:
            @staticmethod
            async def get_trading_rules(connector_name, trading_pairs=None):
                return {}

        self.executors = _Executors()
        self.portfolio = _Portfolio()
        self.connectors = _Connectors()


@pytest.fixture(autouse=True)
def _fast_read_back(monkeypatch):
    monkeypatch.setattr(executor_create, "READ_BACK_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(executor_create, "READ_BACK_WINDOW_SECONDS", 0.004)
    monkeypatch.setattr(
        executor_create.executor_preferences, "get_defaults", lambda _type: {}
    )
    trading_rules_cache.clear()
    yield
    trading_rules_cache.clear()


def _position(client, connector="binance"):
    return asyncio.run(
        executor_create.create_position_executor(
            client,
            connector_name=connector,
            trading_pair="AVAX-USDT",
            side=1,
            amount=0.748,
            stop_loss=0.02,
            take_profit=0.03,
        )
    )


def test_an_unfunded_executor_is_an_error_naming_the_balances():
    """The AVAX case: accepted, then terminated at start for lack of USDT."""
    client = _Client(
        states=[
            {"status": "RUNNING"},
            {"status": "TERMINATED", "close_type": "INSUFFICIENT_BALANCE"},
        ],
        balances={
            "master_account": {
                "binance": [
                    {
                        "token": "USDC",
                        "units": 0.08,
                        "available_units": 0.08,
                        "value": 0.08,
                    },
                    {
                        "token": "AVAX",
                        "units": 9.89,
                        "available_units": 9.89,
                        "value": 79.41,
                    },
                ]
            }
        },
    )

    result = _position(client)

    assert "INSUFFICIENT_BALANCE" in result["error"]
    assert "No position was opened" in result["error"]
    assert "USDC 0.08" in result["error"] and "AVAX 9.89" in result["error"]
    assert result["formatted_output"].startswith("Error creating position_executor")
    assert "successfully" not in result["formatted_output"]
    # It stopped watching once the executor terminated.
    assert client.reads == 2


def test_a_failed_start_is_an_error_too():
    client = _Client(states=[{"status": "TERMINATED", "close_type": "FAILED"}])

    result = _position(client)

    assert "FAILED" in result["error"]
    assert "No position was opened" in result["error"]


def test_a_running_executor_with_no_fill_is_unconfirmed_not_successful():
    client = _Client(states=[{"status": "RUNNING", "filled_amount_quote": 0}])

    result = _position(client)

    assert "error" not in result
    assert result["executor_id"] == "exec-1"
    assert "nothing filled yet" in result["formatted_output"]
    assert "not confirmed" in result["formatted_output"]
    assert "successfully" not in result["formatted_output"]


def test_a_filled_executor_reports_the_fill():
    client = _Client(states=[{"status": "RUNNING", "filled_amount_quote": 6.01}])

    result = _position(client)

    assert "error" not in result
    assert "filled $6.01" in result["formatted_output"]
    assert result["filled_amount_quote"] == pytest.approx(6.01)


def test_an_executor_that_finished_normally_is_not_an_error():
    """A market order executor can fill and complete inside the window."""
    client = _Client(
        states=[
            {
                "status": "TERMINATED",
                "close_type": "POSITION_HOLD",
                "filled_amount_quote": 6,
            }
        ]
    )

    result = _position(client)

    assert "error" not in result
    assert "already finished" in result["formatted_output"]
    assert "POSITION_HOLD" in result["formatted_output"]


def test_an_unreadable_executor_is_reported_as_unconfirmed():
    client = _Client(states=[], get_error=RuntimeError("404 not found"))

    result = _position(client)

    assert "error" not in result
    assert "could not be read back" in result["formatted_output"]
    assert "NOT confirmed" in result["formatted_output"]


def test_an_insufficient_balance_without_readable_balances_still_errors():
    client = _Client(
        states=[{"status": "TERMINATED", "close_type": "INSUFFICIENT_BALANCE"}]
    )
    del client.portfolio

    result = _position(client)

    assert "INSUFFICIENT_BALANCE" in result["error"]
