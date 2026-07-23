"""Unit tests for the risk-limit gate (CORR-046).

The per-tick RiskState snapshot must accumulate approved executor creates so
that multiple ``manage_executors(create)`` calls within the same tick are gated
against the running totals (executor count and exposure), not the frozen
pre-tick numbers.
"""

import asyncio
import math

from condor.agents.risk import (
    RiskEngine,
    RiskLimits,
    RiskState,
    auto_approve_with_risk_check,
)
from condor.agents.performance import _executor_row


def _create_call(amount: float = 100.0) -> dict:
    return {
        "tool": "manage_executors",
        "input": {
            "action": "create",
            "executor_config": {
                "controller_id": "test_controller",
                "total_amount_quote": amount,
            },
        },
    }


def _lp_create_call(*, quote_amount: float = 0.0, base_amount: float = 0.0) -> dict:
    return {
        "tool": "manage_executors",
        "input": {
            "action": "create",
            "executor_type": "lp_executor",
            "controller_id": "test_controller",
            "executor_config": {
                "quote_amount": quote_amount,
                "base_amount": base_amount,
                "lower_price": 0.002,
                "upper_price": 0.003,
            },
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

    allowed, _ = engine.check_executor_action(_create_call(), state)
    assert allowed
    assert state.executor_count == 5

    allowed, reason = engine.check_executor_action(_create_call(), state)
    assert not allowed
    assert "Max open executors" in reason


def test_cumulative_exposure_blocks_second_create():
    """Two creates that individually fit but jointly exceed the limit: second blocked."""
    engine = RiskEngine(RiskLimits(max_position_size_quote=500.0))
    state = RiskState(total_exposure=300.0)

    allowed, _ = engine.check_executor_action(_create_call(150.0), state)
    assert allowed
    assert state.total_exposure == 450.0

    allowed, reason = engine.check_executor_action(_create_call(150.0), state)
    assert not allowed
    assert "position limit" in reason


def test_rejected_create_does_not_accumulate():
    engine = RiskEngine(RiskLimits(max_position_size_quote=500.0))
    state = RiskState(total_exposure=400.0)

    allowed, _ = engine.check_executor_action(_create_call(200.0), state)
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


def test_lp_quote_amount_is_counted_as_quote_exposure():
    engine = RiskEngine(RiskLimits(max_position_size_quote=2.0))
    state = RiskState(total_exposure=1.25)

    allowed, reason = engine.check_executor_action(
        _lp_create_call(quote_amount=1.0), state
    )

    assert not allowed
    assert "quote units" in reason
    assert state.total_exposure == 1.25


def test_legacy_lp_amount_quote_is_counted():
    call = _lp_create_call()
    call["input"]["executor_config"].pop("quote_amount")
    call["input"]["executor_config"]["amount_quote"] = 1.5
    engine = RiskEngine(RiskLimits(max_position_size_quote=2.0))
    state = RiskState(total_exposure=0.75)

    allowed, _ = engine.check_executor_action(call, state)

    assert not allowed


def test_two_sided_lp_values_base_at_range_upper_bound():
    engine = RiskEngine(RiskLimits(max_position_size_quote=3.0))
    state = RiskState()
    call = _lp_create_call(quote_amount=1.0, base_amount=400.0)

    allowed, _ = engine.check_executor_action(call, state)

    expected = 1.0 + 400.0 * 0.003
    assert allowed
    assert math.isclose(state.total_exposure, expected)


def test_zero_generic_default_does_not_mask_lp_amounts():
    engine = RiskEngine(RiskLimits(max_position_size_quote=2.0))
    state = RiskState()
    call = _lp_create_call(quote_amount=1.0)
    call["input"]["executor_config"]["total_amount_quote"] = 0

    allowed, _ = engine.check_executor_action(call, state)

    assert allowed
    assert state.total_exposure == 1.0


def test_live_performance_row_preserves_lp_quote_exposure():
    row = _executor_row(
        {
            "id": "lp1",
            "status": "RUNNING",
            "config": {
                "type": "lp_executor",
                "base_amount": 400.0,
                "quote_amount": 1.0,
                "lower_price": 0.002,
                "upper_price": 0.003,
            },
        }
    )

    assert math.isclose(row["amount"], 1.0 + 400.0 * 0.003)


def test_create_without_measurable_exposure_is_blocked():
    call = _create_call()
    call["input"]["executor_config"].pop("total_amount_quote")
    engine = RiskEngine(RiskLimits())

    allowed, reason = engine.check_executor_action(call, RiskState())

    assert not allowed
    assert "exposure is missing" in reason


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


def test_callback_accepts_top_level_controller_id_for_lp_create():
    engine = RiskEngine(RiskLimits(max_position_size_quote=2.0))
    state = RiskState()
    callback = auto_approve_with_risk_check(engine, state)

    result = asyncio.run(callback(_lp_create_call(quote_amount=1.0), _OPTIONS))

    assert result["outcome"]["outcome"] == "selected"


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
