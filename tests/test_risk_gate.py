"""Unit tests for the risk-limit gate (CORR-046).

The per-tick RiskState snapshot must accumulate approved executor creates so
that multiple ``manage_executors(create)`` calls within the same tick are gated
against the running totals (executor count and exposure), not the frozen
pre-tick numbers.
"""

import asyncio

from condor.agents.risk import (
    RiskEngine,
    RiskLimits,
    RiskState,
    risk_gate,
)


def _create_call(amount: float = 100.0) -> dict:
    # Condor-native create shape: the type's own RiskDeclaration computes the
    # notional (position_pred: max_notional_quote == amount_quote).
    return {
        "tool": "manage_executors",
        "input": {
            "action": "create",
            "executor_type": "position_pred",
            "config": {"market": "world-cup-winner", "amount_quote": amount},
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


# ---------------------------------------------------------------------------
# risk_gate driven twice with the same RiskState instance
# ---------------------------------------------------------------------------


def test_callback_second_create_cancelled_same_tick():
    """Driving the permission callback twice with one RiskState: second create is cancelled."""
    engine = RiskEngine(RiskLimits(max_open_executors=5))
    state = RiskState(executor_count=4)
    callback = risk_gate(engine, state)

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


def test_get_state_fresh_tracker_stays_unblocked():
    """A fresh RunMetricsTracker never raises: state stays clean."""
    from condor.agents.projections import RunMetricsTracker

    state = RiskEngine(RiskLimits()).get_state(RunMetricsTracker())

    assert not state.is_blocked
    assert state.block_reason == ""
    assert state.total_exposure == 0.0
    assert state.executor_count == 0


def test_callback_cumulative_exposure_cancelled_same_tick():
    engine = RiskEngine(RiskLimits(max_position_size_quote=500.0))
    state = RiskState(total_exposure=250.0)
    callback = risk_gate(engine, state)

    async def _drive():
        first = await callback(_create_call(150.0), _OPTIONS)
        second = await callback(_create_call(150.0), _OPTIONS)
        return first, second

    first, second = asyncio.run(_drive())
    assert first["outcome"]["outcome"] == "selected"
    assert second["outcome"]["outcome"] == "cancelled"


# ---------------------------------------------------------------------------
# experiment mode blocks the mutating executor actions and nothing else
# ---------------------------------------------------------------------------


def test_experiment_blocks_create_and_stop():
    engine = RiskEngine(RiskLimits())
    state = RiskState()
    callback = risk_gate(engine, state, experiment=True)

    async def _drive():
        create = await callback(_create_call(), _OPTIONS)
        stop = await callback(
            {"tool": "manage_executors", "input": {"action": "stop", "executor_id": "x"}},
            _OPTIONS,
        )
        return create, stop

    create, stop = asyncio.run(_drive())
    assert create["outcome"]["outcome"] == "cancelled"
    assert stop["outcome"]["outcome"] == "cancelled"


def test_experiment_allows_read_only_executor_actions():
    """Read-only manage_executors actions must not be blocked in an experiment."""
    engine = RiskEngine(RiskLimits())
    state = RiskState()
    callback = risk_gate(engine, state, experiment=True)

    result = asyncio.run(
        callback({"tool": "manage_executors", "input": {"action": "status"}}, _OPTIONS)
    )
    assert result["outcome"]["outcome"] == "selected"


# ---------------------------------------------------------------------------
# native creates fail CLOSED when the risk can't be computed
# ---------------------------------------------------------------------------


def test_unknown_executor_type_fails_closed():
    engine = RiskEngine(RiskLimits())
    state = RiskState()
    callback = risk_gate(engine, state)

    result = asyncio.run(
        callback(
            {
                "tool": "manage_executors",
                "input": {"action": "create", "executor_type": "mystery"},
            },
            _OPTIONS,
        )
    )
    assert result["outcome"]["outcome"] == "cancelled"


def test_loop_mode_still_approves_stop():
    """Stops are risk-reducing — never blocked by the gate."""
    engine = RiskEngine(RiskLimits())
    state = RiskState()
    callback = risk_gate(engine, state)

    result = asyncio.run(
        callback(
            {"tool": "manage_executors", "input": {"action": "stop", "executor_id": "x"}},
            _OPTIONS,
        )
    )
    assert result["outcome"]["outcome"] == "selected"


def test_drawdown_measured_against_capital_not_peak_pnl():
    """A maker whose PnL oscillates near zero (peak +0.01, current -0.03) must
    read a tiny drawdown vs its capital, not 400% vs peak PnL (which permanently
    paused the perp MM)."""
    from condor.agents.risk import RiskEngine, RiskLimits

    class _T:
        def get_total_exposure(self): return 0.0
        def get_open_executor_count(self): return 0
        def get_pnl_series(self): return [{"pnl": 0.0}, {"pnl": 0.01}, {"pnl": -0.03}]
        def get_drawdown_pct(self): return 400.0  # the old (wrong) metric — must be ignored

    eng = RiskEngine(RiskLimits(max_position_size_quote=200, max_drawdown_pct=15))
    st = eng.get_state(_T())
    assert st.drawdown_pct < 1.0          # (0.01 - -0.03)/200*100 = 0.02%
    assert not st.is_blocked


def test_drawdown_falls_back_without_capital_base():
    from condor.agents.risk import RiskEngine, RiskLimits

    class _T:
        def get_total_exposure(self): return 0.0
        def get_open_executor_count(self): return 0
        def get_drawdown_pct(self): return 12.5

    eng = RiskEngine(RiskLimits(max_drawdown_pct=15))  # no position-size base
    assert eng.get_state(_T()).drawdown_pct == 12.5
