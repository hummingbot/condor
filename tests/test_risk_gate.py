"""Unit tests for the risk-limit gate (CORR-046).

The per-tick RiskState snapshot must accumulate approved executor creates so
that multiple ``manage_executors(create)`` calls within the same tick are gated
against the running totals (executor count and exposure), not the frozen
pre-tick numbers.
"""

import asyncio

import pytest

from condor.agents.risk import (
    RiskEngine,
    RiskLimits,
    RiskState,
    _planned_amount_quote,
    auto_approve_with_risk_check,
)


def _create_call(amount: float = 100.0) -> dict:
    return {
        "tool": "manage_executors",
        "input": {
            "action": "create",
            "executor_config": {
                "controller_id": "test_controller",
                "type": "grid_executor",
                "total_amount_quote": amount,
            },
        },
    }


def _order_call(amount: str | float) -> dict:
    config = {
        "controller_id": "test_controller",
        "type": "order_executor",
        "connector_name": "solana-mainnet-beta",
        "trading_pair": "PUMP-USDC",
        "amount": amount,
    }
    return {
        "tool": "manage_executors",
        "input": {
            "action": "create",
            "executor_type": "order_executor",
            "executor_config": config,
        },
    }


_OPTIONS = [{"kind": "allow_once", "optionId": "allow"}]


# ---------------------------------------------------------------------------
# check_executor_action accumulates approvals into the shared state
# ---------------------------------------------------------------------------


def test_second_create_blocked_by_executor_count():
    """With max_open_executors=N and N-1 open, only one create is approved."""
    engine = RiskEngine(RiskLimits(max_open_executors=5))
    state = RiskState(executor_count=4)

    allowed, _ = engine.check_executor_action(_create_call(), state, 100.0)
    assert allowed
    assert state.executor_count == 5

    allowed, reason = engine.check_executor_action(_create_call(), state, 100.0)
    assert not allowed
    assert "Max open executors" in reason


def test_cumulative_exposure_blocks_second_create():
    """Two creates that individually fit but jointly exceed the limit: second blocked."""
    engine = RiskEngine(RiskLimits(max_position_size_quote=500.0))
    state = RiskState(total_exposure=300.0)

    allowed, _ = engine.check_executor_action(_create_call(150.0), state, 150.0)
    assert allowed
    assert state.total_exposure == 450.0

    allowed, reason = engine.check_executor_action(_create_call(150.0), state, 150.0)
    assert not allowed
    assert "position limit" in reason


def test_rejected_create_does_not_accumulate():
    engine = RiskEngine(RiskLimits(max_position_size_quote=500.0))
    state = RiskState(total_exposure=400.0)

    allowed, _ = engine.check_executor_action(_create_call(200.0), state, 200.0)
    assert not allowed
    assert state.total_exposure == 400.0
    assert state.executor_count == 0


def test_non_create_actions_do_not_accumulate():
    engine = RiskEngine(RiskLimits())
    state = RiskState(executor_count=2, total_exposure=100.0)

    call = {"tool": "manage_executors", "input": {"action": "stop", "executor_id": "x"}}
    allowed, _ = engine.check_executor_action(call, state)
    assert allowed
    assert state.executor_count == 2
    assert state.total_exposure == 100.0


class _MarketData:
    def __init__(self, price):
        self.price = price

    async def get_prices(self, connector_name, trading_pairs):
        return {"prices": {trading_pairs: self.price} if self.price else {}}


class _PriceClient:
    def __init__(self, price):
        self.market_data = _MarketData(price)


@pytest.mark.parametrize(
    ("executor_type", "config", "expected"),
    [
        ("grid_executor", {"total_amount_quote": 12}, 12),
        ("twap_executor", {"total_amount_quote": 13}, 13),
        ("dca_executor", {"amounts_quote": [4, 5], "total_amount_quote": 1}, 9),
        ("order_executor", {"amount": 3, "total_amount_quote": 1}, 6),
        ("position_executor", {"amount": 4, "total_amount_quote": 0}, 8),
        (
            "lp_executor",
            {"base_amount": 3, "quote_amount": 4, "total_amount_quote": 1},
            10,
        ),
    ],
)
def test_planned_amount_quote_uses_each_executor_capital_field(
    executor_type, config, expected
):
    config.update(connector_name="solana-mainnet-beta", trading_pair="PUMP-USDC")

    amount = asyncio.run(
        _planned_amount_quote(
            {"executor_type": executor_type, "executor_config": config},
            _PriceClient(2),
        )
    )

    assert amount == pytest.approx(expected)


def test_oracle_prices_market_order_before_risk_check():
    state = RiskState()
    callback = auto_approve_with_risk_check(
        RiskEngine(RiskLimits(max_position_size_quote=10.0)),
        state,
        price_client=_PriceClient(0.002285209543),
    )

    result = asyncio.run(callback(_order_call("653.238363"), _OPTIONS))

    assert result["outcome"]["outcome"] == "selected"
    assert state.total_exposure == pytest.approx(653.238363 * 0.002285209543)


def test_missing_oracle_price_blocks_market_order():
    state = RiskState()
    callback = auto_approve_with_risk_check(
        RiskEngine(RiskLimits(max_position_size_quote=10.0)),
        state,
        price_client=_PriceClient(None),
    )

    result = asyncio.run(callback(_order_call("653.238363"), _OPTIONS))

    assert result["outcome"]["outcome"] == "cancelled"
    assert state.total_exposure == 0


# ---------------------------------------------------------------------------
# auto_approve_with_risk_check driven twice with the same RiskState instance
# ---------------------------------------------------------------------------


def test_callback_second_create_cancelled_same_tick():
    """Driving the permission callback twice with one RiskState: second create is cancelled."""
    engine = RiskEngine(RiskLimits(max_open_executors=5))
    state = RiskState(executor_count=4)
    callback = auto_approve_with_risk_check(engine, state)

    async def _drive():
        first = await callback(_create_call(), _OPTIONS)
        second = await callback(_create_call(), _OPTIONS)
        return first, second

    first, second = asyncio.run(_drive())
    assert first["outcome"]["outcome"] == "selected"
    assert second["outcome"]["outcome"] == "cancelled"


# ---------------------------------------------------------------------------
# get_state fails closed when the tracker raises (CORR-055)
# ---------------------------------------------------------------------------


class _BrokenTracker:
    """Tracker whose metrics raise (e.g. corrupted journal)."""

    def get_total_exposure(self) -> float:
        raise ValueError("could not convert string to float: 'garbage'")

    def get_open_executor_count(self) -> int:
        return 0

    def get_drawdown_pct(self) -> float:
        return 0.0


def test_get_state_fails_closed_when_tracker_raises():
    """A tracker error must yield a blocked state, not a clean zeroed one."""
    state = RiskEngine(RiskLimits()).get_state(_BrokenTracker())

    assert state.is_blocked
    assert "risk state unavailable" in state.block_reason
    assert "garbage" in state.block_reason


def test_get_state_null_tracker_stays_unblocked():
    """Experiments use _NullTracker, which never raises: state stays clean."""
    from condor.agents.engine import _NullTracker

    state = RiskEngine(RiskLimits()).get_state(_NullTracker())

    assert not state.is_blocked
    assert state.block_reason == ""
    assert state.total_exposure == 0.0
    assert state.executor_count == 0


def test_callback_cumulative_exposure_cancelled_same_tick():
    engine = RiskEngine(RiskLimits(max_position_size_quote=500.0))
    state = RiskState(total_exposure=250.0)
    callback = auto_approve_with_risk_check(engine, state)

    async def _drive():
        first = await callback(_create_call(150.0), _OPTIONS)
        second = await callback(_create_call(150.0), _OPTIONS)
        return first, second

    first, second = asyncio.run(_drive())
    assert first["outcome"]["outcome"] == "selected"
    assert second["outcome"]["outcome"] == "cancelled"


# ---------------------------------------------------------------------------
# dry_run must block manage_bots deploy/mutate actions too, not just
# manage_executors/place_order/gateway swaps — manage_bots(deploy) places real
# capital via a controller-based bot, a separate path from manage_executors.
# ---------------------------------------------------------------------------


def _bot_call(action: str, **extra) -> dict:
    return {
        "tool": "mcp__mcp-hummingbot__manage_bots",
        "input": {"action": action, **extra},
    }


def test_dry_run_blocks_manage_bots_deploy():
    engine = RiskEngine(RiskLimits())
    state = RiskState()
    callback = auto_approve_with_risk_check(engine, state, execution_mode="dry_run")

    result = asyncio.run(
        callback(
            _bot_call("deploy", bot_name="x", controllers_config=["cfg"]), _OPTIONS
        )
    )
    assert result["outcome"]["outcome"] == "cancelled"


def test_dry_run_blocks_manage_bots_update_config():
    engine = RiskEngine(RiskLimits())
    state = RiskState()
    callback = auto_approve_with_risk_check(engine, state, execution_mode="dry_run")

    result = asyncio.run(callback(_bot_call("update_config", bot_name="x"), _OPTIONS))
    assert result["outcome"]["outcome"] == "cancelled"


def test_dry_run_allows_manage_bots_status():
    """Read-only manage_bots actions must not be blocked in dry_run."""
    engine = RiskEngine(RiskLimits())
    state = RiskState()
    callback = auto_approve_with_risk_check(engine, state, execution_mode="dry_run")

    result = asyncio.run(callback(_bot_call("status"), _OPTIONS))
    assert result["outcome"]["outcome"] == "selected"


# ---------------------------------------------------------------------------
# loop mode: manage_bots(deploy) is risk-gated on a declared loss cap — the
# capital lives in saved controller configs on the API server, so the deploy
# must carry a max_global_drawdown_quote bounded by the position limit.
# ---------------------------------------------------------------------------


def test_loop_mode_blocks_bot_deploy_without_loss_cap():
    """A deploy with no declared max_global_drawdown_quote is blocked."""
    engine = RiskEngine(RiskLimits())
    state = RiskState()
    callback = auto_approve_with_risk_check(engine, state, execution_mode="loop")

    result = asyncio.run(
        callback(
            _bot_call("deploy", bot_name="x", controllers_config=["cfg"]), _OPTIONS
        )
    )
    assert result["outcome"]["outcome"] == "cancelled"


def test_loop_mode_approves_bot_deploy_with_bounded_loss_cap():
    engine = RiskEngine(RiskLimits(max_position_size_quote=500.0))
    state = RiskState()
    callback = auto_approve_with_risk_check(engine, state, execution_mode="loop")

    result = asyncio.run(
        callback(
            _bot_call(
                "deploy",
                bot_name="x",
                controllers_config=["cfg"],
                max_global_drawdown_quote=400.0,
            ),
            _OPTIONS,
        )
    )
    assert result["outcome"]["outcome"] == "selected"


def test_loop_mode_blocks_bot_deploy_with_excessive_loss_cap():
    engine = RiskEngine(RiskLimits(max_position_size_quote=500.0))
    state = RiskState()
    callback = auto_approve_with_risk_check(engine, state, execution_mode="loop")

    result = asyncio.run(
        callback(
            _bot_call(
                "deploy",
                bot_name="x",
                controllers_config=["cfg"],
                max_global_drawdown_quote=5000.0,
            ),
            _OPTIONS,
        )
    )
    assert result["outcome"]["outcome"] == "cancelled"


def test_loop_mode_blocks_update_config_raising_amount_beyond_limit():
    engine = RiskEngine(RiskLimits(max_position_size_quote=500.0))
    state = RiskState()
    callback = auto_approve_with_risk_check(engine, state, execution_mode="loop")

    result = asyncio.run(
        callback(
            _bot_call(
                "update_config",
                bot_name="x",
                config_name="cfg",
                config_data={"total_amount_quote": 900.0},
            ),
            _OPTIONS,
        )
    )
    assert result["outcome"]["outcome"] == "cancelled"


def test_loop_mode_approves_update_config_within_limit():
    engine = RiskEngine(RiskLimits(max_position_size_quote=500.0))
    state = RiskState()
    callback = auto_approve_with_risk_check(engine, state, execution_mode="loop")

    result = asyncio.run(
        callback(
            _bot_call(
                "update_config",
                bot_name="x",
                config_name="cfg",
                config_data={"total_amount_quote": 300.0},
            ),
            _OPTIONS,
        )
    )
    assert result["outcome"]["outcome"] == "selected"


def test_loop_mode_still_approves_bot_stop():
    """Stops are risk-reducing — never blocked by the loss-cap gate."""
    engine = RiskEngine(RiskLimits())
    state = RiskState()
    callback = auto_approve_with_risk_check(engine, state, execution_mode="loop")

    result = asyncio.run(callback(_bot_call("stop_bot", bot_name="x"), _OPTIONS))
    assert result["outcome"]["outcome"] == "selected"


# ---------------------------------------------------------------------------
# controller_id is the ONLY link between a real position and the session that
# opened it, and the model supplies it — so the gate checks the value, not just
# that something is there.
# ---------------------------------------------------------------------------


def _tagged_call(controller_id) -> dict:
    call = _create_call()
    call["input"]["executor_config"]["controller_id"] = controller_id
    return call


def test_create_with_a_foreign_controller_id_is_cancelled():
    """A mistyped tag would open a live position no session could ever claim."""
    engine = RiskEngine(RiskLimits())
    callback = auto_approve_with_risk_check(
        engine, RiskState(), agent_id="acme.scalper_1"
    )

    outcome = asyncio.run(callback(_tagged_call("some_other_controller"), _OPTIONS))
    assert outcome["outcome"]["outcome"] == "cancelled"


def test_create_with_the_sessions_own_controller_id_is_allowed():
    engine = RiskEngine(RiskLimits())
    callback = auto_approve_with_risk_check(
        engine, RiskState(), agent_id="acme.scalper_1"
    )

    outcome = asyncio.run(callback(_tagged_call("acme.scalper_1"), _OPTIONS))
    assert outcome["outcome"]["outcome"] == "selected"


def test_missing_controller_id_still_cancelled_without_an_agent_id():
    """Consults/chat pass no agent_id and keep the presence-only check."""
    engine = RiskEngine(RiskLimits())
    callback = auto_approve_with_risk_check(engine, RiskState())

    assert (
        asyncio.run(callback(_tagged_call(""), _OPTIONS))["outcome"]["outcome"]
        == "cancelled"
    )
    # ...but any non-empty tag passes, exactly as before.
    assert (
        asyncio.run(callback(_tagged_call("anything"), _OPTIONS))["outcome"]["outcome"]
        == "selected"
    )
