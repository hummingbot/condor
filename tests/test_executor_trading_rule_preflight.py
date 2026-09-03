"""An executor below a venue minimum is refused before it reaches the API (CORR-309).

The five ``create_*_executor`` tools used to assemble a config and POST it. A size
under ``min_order_size``, or a notional under ``min_notional_size``, came back as an
opaque backend error — or worse, as an accepted executor that simply never filled,
with nothing anywhere naming the minimum that was missed.

What is pinned here is the shape of the fix, not the arithmetic:

- the refusal happens BEFORE the backend call, so no order is attempted;
- the message names the venue, the pair, and the minimum, so the caller can fix it
  in one step;
- absence of rules never blocks — a venue with no rules endpoint (every gateway
  one), an empty body, a failing request all fall through to the create;
- the rules are fetched once per ``(connector, pair)`` per TTL, so a DCA ladder or
  a grid rebuild does not put a round trip in front of every order.

The repo has no async test setup, so coroutines are driven with asyncio.run().
"""

import asyncio

import pytest

from mcp_servers.hummingbot_api.hummingbot_client import (
    TradingRulesCache,
    trading_rules_cache,
)
from mcp_servers.hummingbot_api.tools import executor_create

BINANCE_RULES = {
    "BTC-USDT": {
        "min_order_size": 0.0001,
        "min_notional_size": 5.0,
        "min_price_increment": 0.01,
    }
}


class _Client:
    """A backend that records creates, and a venue that states its rules."""

    def __init__(self, rules=None, price=None, rules_error=None):
        self.creates = []
        self.rules_calls = []
        self.price_calls = []
        outer = self

        class _Executors:
            @staticmethod
            async def create_executor(executor_config, account_name, controller_id):
                outer.creates.append(executor_config)
                return {"executor_id": "exec-1"}

        class _Connectors:
            @staticmethod
            async def get_trading_rules(connector_name, trading_pairs=None):
                outer.rules_calls.append((connector_name, tuple(trading_pairs or ())))
                if rules_error is not None:
                    raise rules_error
                return rules if rules is not None else {}

        class _MarketData:
            @staticmethod
            async def get_prices(connector_name, trading_pairs):
                outer.price_calls.append((connector_name, tuple(trading_pairs)))
                return {"prices": {trading_pairs[0]: price}} if price else {}

        self.executors = _Executors()
        self.connectors = _Connectors()
        self.market_data = _MarketData()


@pytest.fixture(autouse=True)
def _fresh_cache():
    """The cache is process-level, so a test must not inherit another's answer."""
    trading_rules_cache.clear()
    yield
    trading_rules_cache.clear()


def _position(client, **overrides):
    kwargs = {
        "connector_name": "binance",
        "trading_pair": "BTC-USDT",
        "side": 1,
        "amount": 0.001,
    }
    kwargs.update(overrides)
    return asyncio.run(executor_create.create_position_executor(client, **kwargs))


# ---------------------------------------------------------------------------
# A violated rule refuses the create, and says what the minimum is
# ---------------------------------------------------------------------------


def test_amount_below_min_order_size_never_reaches_the_backend():
    client = _Client(rules=BINANCE_RULES, price=60000.0)

    result = _position(client, amount=0.00001)

    assert client.creates == []
    assert "0.0001" in result["error"]
    assert "binance" in result["error"] and "BTC-USDT" in result["error"]
    assert result["error"] in result["formatted_output"]


def test_notional_below_min_notional_names_the_amount_to_use():
    """Size clears ``min_order_size`` but the $ value does not clear the minimum."""
    client = _Client(rules=BINANCE_RULES, price=1000.0)

    result = _position(client, amount=0.002)  # $2.00 against a $5.00 minimum

    assert client.creates == []
    assert "$2.00" in result["error"] and "$5.00" in result["error"]
    # The fix, in the units the caller passes: 5 / 1000 = 0.005 BTC.
    assert "0.005 BTC" in result["error"]


def test_the_entry_price_is_used_when_given_and_no_price_is_fetched():
    client = _Client(rules=BINANCE_RULES, price=60000.0)

    result = _position(client, amount=0.002, entry_price=1000.0)

    assert client.creates == []
    assert "$2.00" in result["error"]
    assert client.price_calls == []


def test_grid_min_order_amount_quote_below_the_venue_minimum_is_refused():
    client = _Client(rules=BINANCE_RULES)

    result = asyncio.run(
        executor_create.create_grid_executor(
            client,
            connector_name="binance",
            trading_pair="BTC-USDT",
            side=1,
            start_price=100.0,
            end_price=110.0,
            limit_price=95.0,
            total_amount_quote=500.0,
            min_order_amount_quote=2.0,
        )
    )

    assert client.creates == []
    assert "min_order_amount_quote" in result["error"]
    assert "$5.00" in result["error"]


def test_a_grid_funded_below_one_order_is_refused():
    client = _Client(rules=BINANCE_RULES)

    result = asyncio.run(
        executor_create.create_grid_executor(
            client,
            connector_name="binance",
            trading_pair="BTC-USDT",
            side=1,
            start_price=100.0,
            end_price=110.0,
            limit_price=95.0,
            total_amount_quote=3.0,
        )
    )

    assert client.creates == []
    assert "total_amount_quote" in result["error"] and "$5.00" in result["error"]


def test_a_dca_level_below_the_minimum_is_named_by_its_position():
    client = _Client(rules=BINANCE_RULES)

    result = asyncio.run(
        executor_create.create_dca_executor(
            client,
            connector_name="binance",
            trading_pair="BTC-USDT",
            side=1,
            amounts_quote=[50.0, 2.5, 50.0],
            prices=[100.0, 95.0, 90.0],
        )
    )

    assert client.creates == []
    assert "level 2 of 3" in result["error"]
    assert "$2.50" in result["error"] and "$5.00" in result["error"]


def test_an_lp_position_funded_below_the_minimum_is_refused():
    client = _Client(rules=BINANCE_RULES)

    result = asyncio.run(
        executor_create.create_lp_executor(
            client,
            connector_name="binance",
            lp_provider="raydium/clmm",
            trading_pair="BTC-USDT",
            pool_address="pool",
            lower_price=90.0,
            upper_price=110.0,
            side=1,
            quote_amount=1.0,
        )
    )

    assert client.creates == []
    assert "quote_amount" in result["error"] and "$5.00" in result["error"]


# ---------------------------------------------------------------------------
# Absence of rules blocks nothing
# ---------------------------------------------------------------------------


def test_a_compliant_order_still_reaches_the_backend():
    client = _Client(rules=BINANCE_RULES, price=60000.0)

    result = _position(client, amount=0.001)  # $60 against a $5 minimum

    assert len(client.creates) == 1
    assert result["executor_id"] == "exec-1"


def test_an_empty_rules_response_does_not_block_the_create():
    client = _Client(rules={})

    _position(client, amount=0.00000001)

    assert len(client.creates) == 1


def test_a_failing_rules_endpoint_does_not_block_the_create():
    client = _Client(rules_error=RuntimeError("404 not found"))

    _position(client, amount=0.00000001)

    assert len(client.creates) == 1


def test_an_unknown_price_does_not_block_the_notional_check():
    """Size is fine, no price to value it with — the create goes through."""
    client = _Client(rules=BINANCE_RULES, price=None)

    _position(client, amount=0.001)

    assert len(client.creates) == 1
    assert client.price_calls  # it did try


def test_a_client_without_a_connectors_router_does_not_block_the_create():
    client = _Client(rules=BINANCE_RULES)
    del client.connectors

    _position(client, amount=0.00000001)

    assert len(client.creates) == 1


# ---------------------------------------------------------------------------
# The rules are fetched once per (connector, pair) per TTL
# ---------------------------------------------------------------------------


def test_two_creates_on_the_same_pair_make_one_rules_fetch():
    client = _Client(rules=BINANCE_RULES, price=60000.0)

    _position(client, amount=0.001)
    _position(client, amount=0.001)

    assert len(client.creates) == 2
    assert len(client.rules_calls) == 1


def test_a_different_pair_is_fetched_on_its_own():
    client = _Client(rules=BINANCE_RULES, price=60000.0)

    _position(client, amount=0.001)
    _position(client, amount=0.001, trading_pair="ETH-USDT")

    assert len(client.rules_calls) == 2


def test_the_cached_answer_expires_with_the_ttl():
    cache = TradingRulesCache(ttl=0.0)
    client = _Client(rules=BINANCE_RULES)

    asyncio.run(cache.get(client, "binance", "BTC-USDT"))
    asyncio.run(cache.get(client, "binance", "BTC-USDT"))

    assert len(client.rules_calls) == 2


def test_a_venue_that_spells_the_pair_its_own_way_still_answers():
    """One pair asked for, one entry back: that entry is the answer."""
    cache = TradingRulesCache()
    client = _Client(rules={"BTCUSDT": {"min_notional_size": 5.0}})

    rules = asyncio.run(cache.get(client, "binance", "BTC-USDT"))

    assert rules == {"min_notional_size": 5.0}
