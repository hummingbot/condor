"""Money parameters are range-checked, and a '$' amount is preflighted (MH-09).

Two gaps on the create tools:

- ``stop_loss=2`` (meant as 2%) was accepted and means 200% — a stop that never
  triggers — and zero or negative sizes were accepted and failed late or oddly;
- ``create_order_executor(amount="$100")``, the first example in its own
  docstring, made the preflight's amount reader answer "not a number", so both
  the venue-minimum check and the balance check passed it by default.

Every refusal here happens before the POST and names the parameter.

The repo has no async test setup, so coroutines are driven with asyncio.run().
"""

import asyncio

import pytest

from mcp_servers.hummingbot_api.hummingbot_client import trading_rules_cache
from mcp_servers.hummingbot_api.tools import executor_create

RULES = {"SOL-USDT": {"min_order_size": 0.01, "min_notional_size": 5.0}}


class _Client:
    """Records creates; states rules, a price and (optionally) balances."""

    def __init__(self, rules=None, price=150.0, balances=None):
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
                return rules or {}

        class _MarketData:
            @staticmethod
            async def get_prices(connector_name, trading_pairs):
                pairs = (
                    [trading_pairs]
                    if isinstance(trading_pairs, str)
                    else list(trading_pairs)
                )
                return {"prices": {pairs[0]: price}} if price else {}

        class _Portfolio:
            @staticmethod
            async def get_state(
                account_names=None, connector_names=None, refresh=False
            ):
                return {"master_account": {"binance": balances}} if balances else {}

        self.executors = _Executors()
        self.connectors = _Connectors()
        self.market_data = _MarketData()
        self.portfolio = _Portfolio()


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


def _position(client, **overrides):
    kwargs = {
        "connector_name": "binance_perpetual",
        "trading_pair": "SOL-USDT",
        "side": 1,
        "amount": 1.0,
    }
    kwargs.update(overrides)
    return asyncio.run(executor_create.create_position_executor(client, **kwargs))


def _order(client, amount, side=1, **overrides):
    kwargs = {
        "connector_name": "binance",
        "trading_pair": "SOL-USDT",
        "side": side,
        "amount": amount,
        "execution_strategy": "MARKET",
    }
    kwargs.update(overrides)
    return asyncio.run(executor_create.create_order_executor(client, **kwargs))


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


# ---------------------------------------------------------------------------
# stop_loss is a fraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stop_loss", [2, 1, 1.5, 0, -0.02, 100])
def test_a_stop_loss_outside_zero_to_one_is_refused(stop_loss):
    client = _Client()

    with pytest.raises(ValueError, match="stop_loss is a decimal FRACTION"):
        _position(client, stop_loss=stop_loss)
    with pytest.raises(ValueError, match="stop_loss is a decimal FRACTION"):
        _dca(client, stop_loss=stop_loss)

    assert client.creates == []


def test_a_percent_stop_loss_is_told_the_fraction_it_meant():
    with pytest.raises(ValueError) as caught:
        _position(_Client(), stop_loss=2)

    assert "For a 2% stop pass 0.02" in str(caught.value)


@pytest.mark.parametrize("stop_loss", [0.02, 0.5, 0.999, None])
def test_a_real_fraction_or_no_stop_is_let_through(stop_loss):
    client = _Client()

    _position(client, stop_loss=stop_loss)

    assert len(client.creates) == 1


# ---------------------------------------------------------------------------
# Sizes are positive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("amount", [0, -1.0, -0.001])
def test_a_position_size_must_be_positive(amount):
    client = _Client()

    with pytest.raises(ValueError, match="amount must be greater than 0"):
        _position(client, amount=amount)

    assert client.creates == []


@pytest.mark.parametrize("total", [0, -500])
def test_a_grid_must_be_funded_with_a_positive_amount(total):
    client = _Client()

    with pytest.raises(ValueError, match="total_amount_quote must be greater than 0"):
        asyncio.run(
            executor_create.create_grid_executor(
                client,
                connector_name="binance_perpetual",
                trading_pair="SOL-USDT",
                side=1,
                start_price=140,
                end_price=150,
                limit_price=138,
                total_amount_quote=total,
            )
        )

    assert client.creates == []


def test_every_dca_level_must_be_positive_and_the_bad_one_is_named():
    client = _Client()

    with pytest.raises(ValueError, match=r"amounts_quote\[1\] \(level 2\)"):
        _dca(client, amounts_quote=[100, 0])
    with pytest.raises(ValueError, match=r"prices\[0\] \(level 1\)"):
        _dca(client, prices=[-150, 140])

    assert client.creates == []


@pytest.mark.parametrize("amount", ["0", "-1", "$0", "$-5", "abc", "", "1,000", "$"])
def test_an_order_amount_must_be_a_positive_number_or_a_dollar_value(amount):
    client = _Client()

    with pytest.raises(ValueError, match="amount must be a positive number"):
        _order(client, amount)

    assert client.creates == []


def test_an_lp_amount_cannot_be_negative():
    client = _Client()

    with pytest.raises(ValueError, match="quote_amount cannot be negative"):
        asyncio.run(
            executor_create.create_lp_executor(
                client,
                connector_name="solana-mainnet-beta",
                lp_provider="meteora/clmm",
                trading_pair="SOL-USDC",
                pool_address="Pool1",
                lower_price=130,
                upper_price=170,
                side=3,
                base_amount=1.0,
                quote_amount=-5,
            )
        )

    assert client.creates == []


# ---------------------------------------------------------------------------
# A '$' amount is preflighted like any other
# ---------------------------------------------------------------------------


def test_a_dollar_order_below_the_venue_minimum_is_refused_before_the_post():
    client = _Client(rules=RULES)

    result = _order(client, "$3")

    assert client.creates == []
    assert "$3.00" in result["error"] and "$5.00" in result["error"]
    assert "'$5'" in result["error"], "the fix, in the units the caller passes"


def test_a_dollar_order_below_the_minimum_size_is_refused_too():
    """$1 of SOL at 150 is 0.0067 SOL, under a 0.01 SOL minimum."""
    client = _Client(rules={"SOL-USDT": {"min_order_size": 0.01}})

    result = _order(client, "$1")

    assert client.creates == []
    assert "0.01 SOL" in result["error"]


def test_a_dollar_buy_the_account_cannot_fund_is_refused_before_the_post():
    client = _Client(
        rules=RULES,
        balances=[{"token": "USDT", "units": 40, "available_units": 40}],
    )

    result = _order(client, "$100")

    assert client.creates == []
    assert "Insufficient balance on binance" in result["error"]
    assert "needs 100 USDT" in result["error"]
    assert "only 40 USDT" in result["error"]


def test_a_dollar_sell_is_checked_against_the_base_token():
    """$300 of SOL at 150 is 2 SOL; the account holds 0.5."""
    client = _Client(
        balances=[{"token": "SOL", "units": 0.5, "available_units": 0.5}],
    )

    result = _order(client, "$300", side=2)

    assert client.creates == []
    assert "needs 2 SOL" in result["error"]


def test_a_funded_dollar_order_above_the_minimum_is_sent_as_written():
    client = _Client(
        rules=RULES,
        balances=[{"token": "USDT", "units": 500, "available_units": 500}],
    )

    _order(client, "$100")

    assert client.creates[0]["amount"] == "$100"


def test_a_dollar_order_with_no_price_and_no_rules_still_goes_through():
    """Unknown is not a refusal: a price outage must not stop trading."""
    client = _Client(price=None)

    _order(client, "$100", side=2)

    assert len(client.creates) == 1
