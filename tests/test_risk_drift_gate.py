"""The drift gate: an untrustworthy book refuses new exposure ([[FEAT-113]]).

Off by default — drift is normal in small amounts, and an install that blocked
on dust would be taught to raise the limit until it never fired. Switched on, it
is a mandate the agent cannot argue with: creates, deploys and signing DEX calls
are refused while every exposure-reducing call is left alone.
"""

from types import SimpleNamespace

import pytest

from condor.agents.engine import TickEngine
from condor.agents.providers.base import ProviderResult
from condor.agents.risk import RiskEngine, RiskLimits, RiskState


def _create_call(amount: float = 100.0) -> dict:
    return {
        "tool": "create_grid_executor",
        "input": {
            "controller_id": "brigado.mm_1",
            "connector_name": "binance_perpetual",
            "trading_pair": "SOL-USDT",
            "total_amount_quote": amount,
        },
    }


def _untrusted(reason: str = "venue unanswered: timeout") -> RiskState:
    return RiskState(book_trusted=False, drift_reason=reason, drift_quote=250.0)


# ── Defaults: the gate is off and changes nothing ──


def test_the_limit_is_disabled_by_default():
    assert RiskLimits().max_drift_quote == -1.0
    assert RiskState().book_trusted is True
    assert RiskState().drift_quote is None


def test_a_trusted_book_gates_nothing():
    engine = RiskEngine(RiskLimits())
    state = RiskState()
    allowed, reason = engine.check_executor_action(_create_call(), state, 100.0)
    assert allowed and reason == ""


def test_the_verdict_reaches_the_prompt():
    """The agent reads the same verdict the gate acted on."""
    engine = RiskEngine(RiskLimits(max_drift_quote=50.0))
    state = engine.get_state(
        SimpleNamespace(
            get_total_exposure=lambda: 0.0,
            get_open_executor_count=lambda: 0,
            get_drawdown_pct=lambda: 0.0,
        )
    )
    d = state.to_dict()
    assert d["book_trusted"] is True
    assert d["drift_quote"] is None
    assert d["drift_reason"] == ""
    assert d["max_drift_quote"] == 50.0


# ── Enabled and breached: creates refused ──


def test_an_untrusted_book_refuses_an_executor_create():
    engine = RiskEngine(RiskLimits(max_drift_quote=100.0))
    allowed, reason = engine.check_executor_action(
        _create_call(), _untrusted("drift $250.00 on your controllers"), 10.0
    )
    assert not allowed
    assert "Book untrusted" in reason
    assert "drift $250.00" in reason


def test_an_untrusted_book_refuses_a_bot_deploy():
    engine = RiskEngine(RiskLimits(max_drift_quote=100.0))
    call = {
        "tool": "manage_bots",
        "input": {
            "action": "deploy",
            "bot_name": "b1",
            "max_global_drawdown_quote": 10.0,
        },
    }
    allowed, reason = engine.check_bot_action(call, _untrusted())
    assert not allowed
    assert "Book untrusted" in reason


def test_an_untrusted_book_refuses_a_signing_dex_call():
    engine = RiskEngine(RiskLimits(max_drift_quote=100.0))
    call = {
        "tool": "execute_swap",
        "input": {"connector": "jupiter", "amount": 1, "base_token": "SOL"},
    }
    allowed, reason = engine.check_dex_action(call, _untrusted(), 10.0)
    assert not allowed
    assert "Book untrusted" in reason


def test_a_venue_that_did_not_answer_refuses_creates():
    engine = RiskEngine(RiskLimits(max_drift_quote=100.0))
    state = RiskState(
        book_trusted=False, drift_reason="venue unanswered: connection reset"
    )
    allowed, reason = engine.check_executor_action(_create_call(), state, 10.0)
    assert not allowed
    assert "connection reset" in reason


def test_a_refused_create_does_not_accumulate_exposure():
    engine = RiskEngine(RiskLimits(max_drift_quote=100.0))
    state = _untrusted()
    engine.check_executor_action(_create_call(), state, 10.0)
    assert state.total_exposure == 0.0
    assert state.executor_count == 0


# ── ...and the brakes are never touched ──


def test_stop_executor_passes_under_an_untrusted_book():
    engine = RiskEngine(RiskLimits(max_drift_quote=100.0))
    call = {"tool": "stop_executor", "input": {"executor_id": "x"}}
    allowed, _ = engine.check_executor_action(call, _untrusted())
    assert allowed


@pytest.mark.parametrize("action", ["stop_bot", "stop_controllers"])
def test_a_bot_stop_passes_under_an_untrusted_book(action):
    engine = RiskEngine(RiskLimits(max_drift_quote=100.0))
    call = {"tool": "manage_bots", "input": {"action": action, "bot_name": "b1"}}
    allowed, _ = engine.check_bot_action(call, _untrusted())
    assert allowed


@pytest.mark.parametrize("action", ["remove_liquidity", "close", "collect_fees"])
def test_an_exposure_reducing_dex_call_passes_under_an_untrusted_book(action):
    engine = RiskEngine(RiskLimits(max_drift_quote=100.0))
    call = {"tool": "manage_clmm", "input": {"action": action, "position_address": "p"}}
    allowed, _ = engine.check_dex_action(call, _untrusted(), None)
    assert allowed


def test_a_read_only_dex_call_is_not_a_signature():
    engine = RiskEngine(RiskLimits(max_drift_quote=100.0))
    call = {"tool": "manage_clmm", "input": {"action": "positions_owned"}}
    allowed, _ = engine.check_dex_action(call, _untrusted(), None)
    assert allowed


# ── The engine wiring: provider verdict → risk state ──


def _apply(limit: float, data: dict | None) -> RiskState:
    engine = TickEngine.__new__(TickEngine)
    engine.risk = RiskEngine(RiskLimits(max_drift_quote=limit))
    state = RiskState()
    result = (
        None if data is None else ProviderResult(name="drift", data=data, summary="")
    )
    engine._apply_drift_verdict(state, result)
    return state


def test_disabled_limit_records_the_drift_without_binding():
    state = _apply(-1.0, {"trusted": True, "worst_quote": 9999.0})
    assert state.book_trusted is True
    assert state.drift_quote == 9999.0


def test_enabled_limit_binds_once_breached():
    state = _apply(100.0, {"trusted": True, "worst_quote": 250.0})
    assert state.book_trusted is False
    assert "250.00" in state.drift_reason and "100.00" in state.drift_reason


def test_drift_under_the_limit_leaves_the_book_trusted():
    state = _apply(100.0, {"trusted": True, "worst_quote": 99.0})
    assert state.book_trusted is True


def test_an_unpriced_drift_cannot_breach_a_quote_limit():
    """None is not zero and it is not a breach either — it is unpriced."""
    state = _apply(100.0, {"trusted": True, "worst_quote": None})
    assert state.book_trusted is True
    assert state.drift_quote is None


def test_an_unanswered_venue_unbooks_trust_whatever_the_drift():
    state = _apply(100.0, {"trusted": False, "reason": "502", "worst_quote": None})
    assert state.book_trusted is False
    assert "502" in state.drift_reason


def test_a_failed_drift_provider_fails_closed_only_when_enabled():
    """`run_core_providers` hands back empty data; the tick runs either way."""
    assert _apply(-1.0, {}).book_trusted is True
    failed = _apply(100.0, {})
    assert failed.book_trusted is False
    assert "did not run" in failed.drift_reason


def test_no_drift_provider_at_all_changes_nothing():
    assert _apply(100.0, None).book_trusted is True
