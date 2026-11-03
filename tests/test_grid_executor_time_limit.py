"""A grid can be given a lifetime, and it reaches the backend (CORR: grid dead-man switch).

``TripleBarrierConfig.time_limit`` has always been there, and the position and DCA
tools have always exposed it. The grid tool did not: it built its barrier from
``take_profit`` and the two order types only, so an unattended strategy that
required a hard 12-hour lifetime had no way to ask for one — the grid ran until
``limit_price`` or until a human stopped it.

What is pinned here is the wiring, not the barrier semantics: the seconds the
caller passes arrive under ``triple_barrier_config.time_limit`` in the posted
config, and omitting it still posts a barrier with no lifetime rather than a
zero one (which the backend would read as "expire immediately").
"""

import asyncio

import pytest

from mcp_servers.hummingbot_api.hummingbot_client import trading_rules_cache
from mcp_servers.hummingbot_api.tools import executor_create

TWELVE_HOURS = 43_200


class _Client:
    """A backend that records the config it was posted, and states no rules."""

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

        self.executors = _Executors()
        self.connectors = _Connectors()


@pytest.fixture(autouse=True)
def _fresh_cache():
    trading_rules_cache.clear()
    yield
    trading_rules_cache.clear()


@pytest.fixture(autouse=True)
def _no_saved_defaults(monkeypatch):
    """What is pinned here is the wiring, not the developer's own preferences file.

    Saved defaults merge underneath a create — a nested barrier block included — so
    without this the assertions below would read whichever barrier the machine
    running the tests happens to have on disk.
    """
    monkeypatch.setattr(
        executor_create.executor_preferences, "get_defaults", lambda executor_type: {}
    )


def _grid(client, **overrides):
    kwargs = {
        "connector_name": "binance_perpetual",
        "trading_pair": "SOL-USDC",
        "side": 1,
        "start_price": 140.0,
        "end_price": 150.0,
        "limit_price": 138.0,
        "total_amount_quote": 500.0,
    }
    kwargs.update(overrides)
    return asyncio.run(executor_create.create_grid_executor(client, **kwargs))


def test_the_lifetime_reaches_the_backend_under_the_triple_barrier():
    client = _Client()

    _grid(client, time_limit=TWELVE_HOURS)

    assert client.creates[0]["triple_barrier_config"]["time_limit"] == TWELVE_HOURS


def test_a_grid_with_no_lifetime_posts_no_time_limit_at_all():
    """Not ``0`` — the backend reads that as a barrier that has already expired."""
    client = _Client()

    _grid(client, take_profit=0.002)

    assert "time_limit" not in client.creates[0]["triple_barrier_config"]


def test_the_lifetime_sits_beside_the_other_barriers_rather_than_replacing_them():
    client = _Client()

    _grid(client, take_profit=0.002, time_limit=TWELVE_HOURS, take_profit_order_type=3)

    barrier = client.creates[0]["triple_barrier_config"]
    assert barrier["take_profit"] == 0.002
    assert barrier["time_limit"] == TWELVE_HOURS
    assert barrier["take_profit_order_type"] == 3


def test_the_mcp_tool_forwards_the_lifetime_it_was_given():
    """The wrapper is the surface an agent actually calls; it must not drop it."""
    import inspect

    from mcp_servers.hummingbot_api import server

    fn = getattr(server.create_grid_executor, "fn", server.create_grid_executor)
    assert "time_limit" in inspect.signature(fn).parameters
