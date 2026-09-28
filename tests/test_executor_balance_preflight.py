"""A spot executor the account cannot fund is refused before it is created.

Without this the API accepts the config, the executor checks its budget at start
and terminates itself with INSUFFICIENT_BALANCE — an executor id for a position
that never existed. The check reads the API's CACHED balances, so the common case
costs no exchange call.

What is pinned here:

- an unfunded spot BUY (quote) or SELL (base) never reaches the backend, and the
  refusal names the token, the amount needed and what the connector holds;
- a cached shortfall is re-read with refresh=True before refusing, so a stale
  cache cannot block a trade — and a funded order never pays for that refresh;
- grid and DCA are sized in quote, summed across levels for a DCA;
- perpetuals, unreadable balances and an unknown price all let the create through.

The repo has no async test setup, so coroutines are driven with asyncio.run().
"""

import asyncio

import pytest

from mcp_servers.hummingbot_api.hummingbot_client import trading_rules_cache
from mcp_servers.hummingbot_api.tools import executor_create


def _state(rows, connector="binance"):
    return {"master_account": {connector: rows}}


AVAX_ONLY = [
    {"token": "USDC", "units": 0.08, "available_units": 0.08, "value": 0.08},
    {"token": "AVAX", "units": 9.89, "available_units": 9.89, "value": 79.41},
]
FUNDED = AVAX_ONLY + [{"token": "USDT", "units": 500, "available_units": 500}]


class _Client:
    def __init__(self, cached=None, fresh=None, price=8.0, state_error=None):
        self.creates = []
        self.state_calls = []
        outer = self

        class _Executors:
            @staticmethod
            async def create_executor(executor_config, account_name, controller_id):
                outer.creates.append(executor_config)
                return {"executor_id": "exec-1"}

        class _Portfolio:
            @staticmethod
            async def get_state(
                account_names=None, connector_names=None, refresh=False
            ):
                outer.state_calls.append(refresh)
                if state_error is not None:
                    raise state_error
                if refresh and fresh is not None:
                    return fresh
                return cached

        class _Connectors:
            @staticmethod
            async def get_trading_rules(connector_name, trading_pairs=None):
                return {}

        class _MarketData:
            @staticmethod
            async def get_prices(connector_name, trading_pairs):
                pairs = (
                    [trading_pairs]
                    if isinstance(trading_pairs, str)
                    else list(trading_pairs)
                )
                return {"prices": {pairs[0]: price}} if price else {}

        self.executors = _Executors()
        self.portfolio = _Portfolio()
        self.connectors = _Connectors()
        self.market_data = _MarketData()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(executor_create, "READ_BACK_INTERVAL_SECONDS", 0.001)
    monkeypatch.setattr(executor_create, "READ_BACK_WINDOW_SECONDS", 0.001)
    monkeypatch.setattr(
        executor_create.executor_preferences, "get_defaults", lambda _type: {}
    )
    trading_rules_cache.clear()
    yield
    trading_rules_cache.clear()


def _position(client, connector="binance", side=1, amount=0.748):
    return asyncio.run(
        executor_create.create_position_executor(
            client,
            connector_name=connector,
            trading_pair="AVAX-USDT",
            side=side,
            amount=amount,
        )
    )


def test_an_unfunded_spot_buy_never_reaches_the_backend():
    """The AVAX case: ~$6 of USDT needed, none held."""
    client = _Client(cached=_state(AVAX_ONLY))

    result = _position(client)

    assert client.creates == []
    assert "Insufficient balance on binance" in result["error"]
    assert "5.984 USDT" in result["error"]  # 0.748 AVAX at 8
    assert "only 0 USDT" in result["error"]
    assert "USDC 0.08" in result["error"] and "AVAX 9.89" in result["error"]
    assert "Nothing was created" in result["error"]
    # Refused only after the cache was re-read from the exchange.
    assert client.state_calls == [False, True]


def test_a_stale_cached_shortfall_is_rechecked_and_let_through():
    client = _Client(cached=_state(AVAX_ONLY), fresh=_state(FUNDED))

    result = _position(client)

    assert len(client.creates) == 1
    assert "error" not in result


def test_a_funded_order_costs_one_cached_read_and_no_refresh():
    client = _Client(cached=_state(FUNDED))

    _position(client)

    assert len(client.creates) == 1
    assert client.state_calls == [False]


def test_a_spot_sell_needs_the_base_token():
    client = _Client(cached=_state(AVAX_ONLY))

    result = _position(client, side=2, amount=12)

    assert client.creates == []
    assert "needs 12 AVAX" in result["error"] and "9.89 AVAX" in result["error"]


def test_a_grid_buy_is_sized_in_quote():
    client = _Client(cached=_state(AVAX_ONLY))

    result = asyncio.run(
        executor_create.create_grid_executor(
            client,
            connector_name="binance",
            trading_pair="AVAX-USDT",
            side=1,
            start_price=7.0,
            end_price=9.0,
            limit_price=6.5,
            total_amount_quote=100.0,
        )
    )

    assert client.creates == []
    assert "needs 100 USDT" in result["error"]


def test_a_dca_sums_its_levels():
    client = _Client(
        cached=_state([{"token": "USDT", "units": 25, "available_units": 25}])
    )

    result = asyncio.run(
        executor_create.create_dca_executor(
            client,
            connector_name="binance",
            trading_pair="AVAX-USDT",
            side=1,
            amounts_quote=[10.0, 10.0, 10.0],
            prices=[8.0, 7.5, 7.0],
        )
    )

    assert client.creates == []
    assert "needs 30 USDT" in result["error"]


def test_a_perpetual_is_left_to_the_executor():
    client = _Client(cached=_state(AVAX_ONLY, connector="binance_perpetual"))

    _position(client, connector="binance_perpetual")

    assert len(client.creates) == 1
    assert client.state_calls == []


@pytest.mark.parametrize(
    "client",
    [
        _Client(state_error=RuntimeError("portfolio down")),
        _Client(cached={}),  # connector absent from the state
        _Client(cached=_state(AVAX_ONLY), price=None),  # buy with no price
    ],
    ids=["portfolio-error", "no-rows", "no-price"],
)
def test_anything_unknown_lets_the_create_through(client):
    _position(client)

    assert len(client.creates) == 1
