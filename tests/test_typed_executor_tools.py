"""The typed executor tools: one call, host-validated, defaults still merged.

``manage_executors`` was one tool wearing twelve hats behind an
``executor_config: dict[str, Any]`` the MCP protocol could not validate, dispatched
by a ``get_flow_stage()`` that silently answered a different question when an
argument was missing. FEAT-062 replaced it with five typed create tools and nine
named read/control tools.

What is worth pinning is what the split BUYS, since the shape is now enforced by
the host rather than by code here:

- a create is ONE call — no schema fetch first, and the fields are parameters, so a
  wrong one is rejected before the server is reached;
- saved defaults still merge underneath the call, which only works because an
  omitted parameter is dropped rather than sent as an explicit default;
- the invariants the guides state in prose (grid direction, parallel DCA lists) are
  checked where the caller can still fix them;
- the risk gate can value every type from top-level parameters.

The repo has no async test setup, so coroutines are driven with asyncio.run().
"""

import asyncio
import inspect

import pytest

from condor.agents.risk import (
    RiskEngine,
    RiskLimits,
    RiskState,
    auto_approve_with_risk_check,
)
from mcp_servers.hummingbot_api import server
from mcp_servers.hummingbot_api.tools import executor_create

CREATE_TOOLS = (
    "create_position_executor",
    "create_grid_executor",
    "create_dca_executor",
    "create_order_executor",
    "create_lp_executor",
)


class _RecordingClient:
    """Captures the config the tool would have sent to the backend."""

    def __init__(self):
        calls = self.calls = []

        class _Executors:
            @staticmethod
            async def create_executor(executor_config, account_name, controller_id):
                calls.append(
                    {
                        "config": executor_config,
                        "account_name": account_name,
                        "controller_id": controller_id,
                    }
                )
                return {"executor_id": "exec-1"}

        self.executors = _Executors()


# ---------------------------------------------------------------------------
# One call, and the signature is the schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", CREATE_TOOLS)
def test_a_create_takes_no_config_blob(tool_name):
    """Every field is a typed parameter, so the host can validate it."""
    params = inspect.signature(getattr(server, tool_name)).parameters

    assert "executor_config" not in params
    assert "executor_type" not in params
    assert "action" not in params
    # The required ones have no default, so omitting one fails client-side.
    required = [n for n, p in params.items() if p.default is inspect.Parameter.empty]
    assert required, f"{tool_name} requires nothing — it would create on an empty call"


@pytest.mark.parametrize("tool_name", CREATE_TOOLS)
def test_every_create_names_its_controller_and_account(tool_name):
    params = inspect.signature(getattr(server, tool_name)).parameters

    assert "controller_id" in params
    assert "account_name" in params
    assert "save_as_default" in params


def test_creating_an_executor_is_a_single_backend_call():
    """No schema fetch first: the client is touched exactly once."""
    client = _RecordingClient()

    result = asyncio.run(
        executor_create.create_grid_executor(
            client,
            connector_name="binance",
            trading_pair="SOL-USDT",
            side=1,
            start_price=140,
            end_price=150,
            limit_price=138,
            total_amount_quote=500,
        )
    )

    assert len(client.calls) == 1
    assert result["executor_id"] == "exec-1"
    assert client.calls[0]["config"]["type"] == "grid_executor"


# ---------------------------------------------------------------------------
# Omitted means omitted — which is what makes saved defaults still work
# ---------------------------------------------------------------------------


def test_omitted_parameters_are_not_sent(monkeypatch):
    """An unset optional must not reach the backend as an explicit None.

    Sending it would overwrite the backend's own default, and — the reason this is
    load-bearing — would overwrite the user's saved default too, since defaults
    merge underneath the call.
    """
    client = _RecordingClient()

    asyncio.run(
        executor_create.create_position_executor(
            client,
            connector_name="binance_perpetual",
            trading_pair="BTC-USDT",
            side=1,
            amount=0.01,
        )
    )

    config = client.calls[0]["config"]
    assert "entry_price" not in config
    assert "leverage" not in config
    assert "triple_barrier_config" not in config
    assert config["amount"] == 0.01


def test_saved_defaults_merge_under_the_call(monkeypatch):
    """A default fills in what the call omitted and never overrides what it set."""
    client = _RecordingClient()
    monkeypatch.setattr(
        executor_create.executor_preferences,
        "get_defaults",
        lambda executor_type: {"leverage": 5, "entry_price": 60000},
    )

    asyncio.run(
        executor_create.create_position_executor(
            client,
            connector_name="binance_perpetual",
            trading_pair="BTC-USDT",
            side=1,
            amount=0.01,
            leverage=1,
        )
    )

    config = client.calls[0]["config"]
    assert config["leverage"] == 1, "the explicit argument must win"
    assert config["entry_price"] == 60000, "the saved default must fill the gap"


def test_the_controller_tag_never_travels_inside_the_config(monkeypatch):
    """The explicit tag is what the risk gate attributed the position to."""
    client = _RecordingClient()
    monkeypatch.setattr(
        executor_create.executor_preferences,
        "get_defaults",
        lambda executor_type: {"controller_id": "a_stale_default"},
    )

    asyncio.run(
        executor_create.create_order_executor(
            client,
            connector_name="binance",
            trading_pair="SOL-USDT",
            side=1,
            amount="1",
            execution_strategy="MARKET",
            controller_id="acme.scalper_1",
        )
    )

    assert client.calls[0]["controller_id"] == "acme.scalper_1"
    assert "controller_id" not in client.calls[0]["config"]


# ---------------------------------------------------------------------------
# Invariants the types cannot express are checked before the round trip
# ---------------------------------------------------------------------------


def test_an_inverted_long_grid_is_refused():
    """The backend accepts it and it simply never fills."""
    with pytest.raises(ValueError, match="LONG grid"):
        asyncio.run(
            executor_create.create_grid_executor(
                _RecordingClient(),
                connector_name="binance",
                trading_pair="SOL-USDT",
                side=1,
                start_price=140,
                end_price=150,
                limit_price=155,  # above the grid: this is a SHORT layout
                total_amount_quote=500,
            )
        )


def test_a_short_grid_wants_its_limit_above_the_range():
    with pytest.raises(ValueError, match="SHORT grid"):
        asyncio.run(
            executor_create.create_grid_executor(
                _RecordingClient(),
                connector_name="binance",
                trading_pair="SOL-USDT",
                side=2,
                start_price=140,
                end_price=150,
                limit_price=138,
                total_amount_quote=500,
            )
        )


def test_a_dca_ladder_with_mismatched_lists_is_refused():
    with pytest.raises(ValueError, match="parallel lists"):
        asyncio.run(
            executor_create.create_dca_executor(
                _RecordingClient(),
                connector_name="binance",
                trading_pair="BTC-USDT",
                side=1,
                amounts_quote=[100, 100, 150],
                prices=[50000, 48000],
            )
        )


def test_a_limit_order_without_a_price_is_refused():
    with pytest.raises(ValueError, match="requires a price"):
        asyncio.run(
            executor_create.create_order_executor(
                _RecordingClient(),
                connector_name="binance",
                trading_pair="SOL-USDT",
                side=1,
                amount="1",
                execution_strategy="LIMIT",
            )
        )


def test_an_lp_range_that_is_not_a_range_is_refused():
    with pytest.raises(ValueError, match="lower_price"):
        asyncio.run(
            executor_create.create_lp_executor(
                _RecordingClient(),
                connector_name="solana-mainnet-beta",
                lp_provider="meteora/clmm",
                trading_pair="SOL-USDC",
                pool_address="pool",
                lower_price=150,
                upper_price=140,
                side=3,
                quote_amount=25,
            )
        )


def test_an_unfunded_lp_position_is_refused():
    with pytest.raises(ValueError, match="needs funding"):
        asyncio.run(
            executor_create.create_lp_executor(
                _RecordingClient(),
                connector_name="solana-mainnet-beta",
                lp_provider="meteora/clmm",
                trading_pair="SOL-USDC",
                pool_address="pool",
                lower_price=140,
                upper_price=150,
                side=3,
            )
        )


# ---------------------------------------------------------------------------
# Risk limits still block an over-notional create, for every type
# ---------------------------------------------------------------------------


class _MarketData:
    async def get_prices(self, connector_name, trading_pairs):
        # The real client accepts either a bare pair or a list and wraps
        # the bare one before it posts; mirror that so this stub answers
        # the same way for both spellings.
        pairs = (
            [trading_pairs] if isinstance(trading_pairs, str) else list(trading_pairs)
        )
        return {"prices": {pairs[0]: 100.0}}


class _PriceClient:
    def __init__(self):
        self.market_data = _MarketData()


_OPTIONS = [{"kind": "allow_once", "optionId": "allow"}]

#: One over-limit create per type, valued the way that type sizes itself.
_OVER_LIMIT_CREATES = {
    # 5 base x $100 = $500
    "create_position_executor": {"amount": 5.0},
    "create_grid_executor": {"total_amount_quote": 500.0},
    "create_dca_executor": {"amounts_quote": [250.0, 250.0]},
    "create_order_executor": {"amount": "5.0"},
    "create_lp_executor": {"base_amount": 5.0},
}


@pytest.mark.parametrize(("tool_name", "extra"), sorted(_OVER_LIMIT_CREATES.items()))
def test_risk_limits_block_an_over_notional_create_of_every_type(tool_name, extra):
    state = RiskState()
    callback = auto_approve_with_risk_check(
        RiskEngine(RiskLimits(max_position_size_quote=100.0)),
        state,
        price_client=_PriceClient(),
    )
    call = {
        "tool": f"mcp__mcp-hummingbot__{tool_name}",
        "input": {
            "controller_id": "test_controller",
            "connector_name": "binance",
            "trading_pair": "SOL-USDT",
            **extra,
        },
    }

    result = asyncio.run(callback(call, _OPTIONS))

    assert result["outcome"]["outcome"] == "cancelled"
    assert state.total_exposure == 0


@pytest.mark.parametrize("tool_name", sorted(_OVER_LIMIT_CREATES))
def test_a_create_within_the_limit_is_approved(tool_name):
    """The gate must not simply refuse everything: the same shape, sized to fit."""
    within = {
        "create_position_executor": {"amount": 0.1},
        "create_grid_executor": {"total_amount_quote": 10.0},
        "create_dca_executor": {"amounts_quote": [5.0, 5.0]},
        "create_order_executor": {"amount": "0.1"},
        "create_lp_executor": {"base_amount": 0.1},
    }[tool_name]
    state = RiskState()
    callback = auto_approve_with_risk_check(
        RiskEngine(RiskLimits(max_position_size_quote=100.0)),
        state,
        price_client=_PriceClient(),
    )
    call = {
        "tool": f"mcp__mcp-hummingbot__{tool_name}",
        "input": {
            "controller_id": "test_controller",
            "connector_name": "binance",
            "trading_pair": "SOL-USDT",
            **within,
        },
    }

    result = asyncio.run(callback(call, _OPTIONS))

    assert result["outcome"]["outcome"] == "selected"
    assert state.total_exposure == pytest.approx(10.0)


def test_a_read_tool_is_never_gated():
    """Reads pass without a human and without a risk check."""
    callback = auto_approve_with_risk_check(
        RiskEngine(RiskLimits(max_position_size_quote=1.0)), RiskState()
    )

    for name in ("list_executors", "get_executor", "get_performance_report"):
        result = asyncio.run(
            callback(
                {"tool": f"mcp__mcp-hummingbot__{name}", "input": {}},
                _OPTIONS,
            )
        )
        assert result["outcome"]["outcome"] == "selected", f"{name} was gated"
