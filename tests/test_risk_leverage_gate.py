"""The leverage half of the loop's risk envelope (SEC-558).

The other limits bound the capital a session can commit. Leverage decides how
far the market has to move before that capital is gone, so a 90-quote grid at
1x and the same grid at 20x are the same number to ``max_position_size_quote``
and twenty times apart on the liquidation distance.

Two gates here, both keyed on ``max_leverage``: the leverage a create asks for,
and ``set_account_position_mode_and_leverage``, which raises leverage on
positions that are already open. Both are off unless the session sets a limit —
``-1.0`` means disabled, the way ``max_drift_quote`` does, so nothing an
existing session does changes on upgrade.
"""

import asyncio
from types import SimpleNamespace

import pytest

from condor.agents.config import RiskLimitsConfig
from condor.agents.prompts import build_tick_prompt
from condor.agents.risk import (
    RefusalLog,
    RiskEngine,
    RiskLimits,
    RiskState,
    auto_approve_with_risk_check,
)
from condor.runtime.danger import (
    DANGEROUS_TOOLS,
    LEVERAGE_TOOL,
    format_tool_summary,
    is_dangerous_tool_call,
    is_mutating_tool_call,
    is_recordable_tool_call,
)

_OPTIONS = [{"kind": "allow_once", "optionId": "allow"}]


def _grid_call(leverage=None, amount: float = 100.0) -> dict:
    args = {
        "controller_id": "test_controller",
        "connector_name": "binance_perpetual",
        "trading_pair": "SOL-USDT",
        "total_amount_quote": amount,
    }
    if leverage is not None:
        args["leverage"] = leverage
    return {"tool": "create_grid_executor", "input": args}


def _position_call(leverage=None) -> dict:
    args = {
        "controller_id": "test_controller",
        "connector_name": "binance_perpetual",
        "trading_pair": "SOL-USDT",
        "side": 1,
        "amount": 1.0,
    }
    if leverage is not None:
        args["leverage"] = leverage
    return {"tool": "create_position_executor", "input": args}


def _leverage_call(leverage=None, **extra) -> dict:
    args = {
        "account_name": "master_account",
        "connector_name": "binance_perpetual",
        "trading_pair": "SOL-USDT",
    }
    if leverage is not None:
        args["leverage"] = leverage
    args.update(extra)
    return {"tool": LEVERAGE_TOOL, "input": args}


class _PriceClient:
    """Prices a base-denominated create so the exposure gate can run."""

    def __init__(self, price: float = 1.0):
        self.price = price

    async def get_price(self, *_args, **_kwargs):
        return self.price


# ---------------------------------------------------------------------------
# Disabled by default: nothing an existing session does changes
# ---------------------------------------------------------------------------


def test_the_leverage_limit_is_off_by_default():
    assert RiskLimits().max_leverage == -1.0
    assert RiskLimitsConfig().max_leverage == -1.0


def test_a_session_without_the_limit_keeps_every_path_untouched():
    """-1 leaves creates — leveraged, unleveraged, unreadable — exactly as today."""
    engine = RiskEngine(RiskLimits(max_position_size_quote=10_000.0))
    for call in (
        _grid_call(leverage=20),
        _grid_call(leverage=None),
        _grid_call(leverage="not a number"),
    ):
        allowed, reason = engine.check_executor_action(call, RiskState(), 100.0)
        assert allowed, reason


def test_a_session_without_the_limit_leaves_the_leverage_tool_alone():
    engine = RiskEngine(RiskLimits())
    allowed, reason = engine.check_leverage_action(_leverage_call(leverage=125))
    assert allowed, reason


def test_the_limit_is_carried_into_the_risk_state_dict():
    engine = RiskEngine(RiskLimits(max_leverage=5.0))
    state = engine.get_state(
        SimpleNamespace(
            get_total_exposure=lambda: 0.0,
            get_open_executor_count=lambda: 0,
            get_drawdown_pct=lambda: 0.0,
        )
    )
    assert state.to_dict()["max_leverage"] == 5.0
    assert RiskState().to_dict()["max_leverage"] == -1


# ---------------------------------------------------------------------------
# A create is gated on the leverage it asks for
# ---------------------------------------------------------------------------


def test_an_over_limit_create_is_refused_naming_leverage():
    engine = RiskEngine(RiskLimits(max_leverage=5.0))
    allowed, reason = engine.check_executor_action(
        _grid_call(leverage=20), RiskState(), 100.0
    )
    assert not allowed
    assert "leverage" in reason.lower()
    assert "20x" in reason and "5x" in reason


def test_an_at_limit_create_is_allowed():
    engine = RiskEngine(RiskLimits(max_leverage=5.0))
    state = RiskState()
    allowed, reason = engine.check_executor_action(_grid_call(leverage=5), state, 100.0)
    assert allowed, reason
    assert state.total_exposure == 100.0


def test_leverage_is_weighed_before_the_position_limit():
    """The reason has to name the dimension that is actually breached.

    Both limits are breached here; only the leverage one explains why the same
    quote figure is fine at 1x and not at 20x.
    """
    engine = RiskEngine(RiskLimits(max_position_size_quote=50.0, max_leverage=5.0))
    allowed, reason = engine.check_executor_action(
        _grid_call(leverage=20), RiskState(), 100.0
    )
    assert not allowed
    assert "leverage" in reason.lower()
    assert "position limit" not in reason


def test_an_omitted_leverage_is_refused_when_the_limit_is_on():
    """The backend picks its own default for an omitted one — 20 for a grid."""
    engine = RiskEngine(RiskLimits(max_leverage=5.0))
    allowed, reason = engine.check_executor_action(_grid_call(), RiskState(), 100.0)
    assert not allowed
    assert "no leverage declared" in reason


@pytest.mark.parametrize("value", ["", "twenty", None, float("nan"), 0, -3, True])
def test_an_unreadable_leverage_fails_closed(value):
    engine = RiskEngine(RiskLimits(max_leverage=5.0))
    call = _grid_call()
    call["input"]["leverage"] = value
    allowed, _ = engine.check_executor_action(call, RiskState(), 100.0)
    assert not allowed


def test_a_string_leverage_is_read_as_a_number():
    engine = RiskEngine(RiskLimits(max_leverage=5.0))
    assert engine.check_executor_action(_grid_call(leverage="3"), RiskState(), 100.0)[0]
    assert engine.check_executor_action(_grid_call(leverage="5x"), RiskState(), 100.0)[
        0
    ]
    assert not engine.check_executor_action(
        _grid_call(leverage="20x"), RiskState(), 100.0
    )[0]


def test_an_lp_create_is_never_asked_for_leverage():
    """A CLMM position is not margined and the tool takes no leverage at all."""
    engine = RiskEngine(RiskLimits(max_leverage=5.0))
    call = {
        "tool": "create_lp_executor",
        "input": {
            "controller_id": "test_controller",
            "connector_name": "raydium/clmm",
            "trading_pair": "SOL-USDC",
            "quote_amount": 50.0,
        },
    }
    allowed, reason = engine.check_executor_action(call, RiskState(), 50.0)
    assert allowed, reason


# ---------------------------------------------------------------------------
# The loop callback: the same limit, end to end
# ---------------------------------------------------------------------------


def test_loop_mode_cancels_an_over_limit_create_and_records_why():
    refusals = RefusalLog()
    callback = auto_approve_with_risk_check(
        RiskEngine(RiskLimits(max_leverage=5.0)),
        RiskState(),
        execution_mode="loop",
        refusals=refusals,
        price_client=_PriceClient(),
    )

    result = asyncio.run(callback(_grid_call(leverage=20), _OPTIONS))

    assert result["outcome"]["outcome"] == "cancelled"
    assert "leverage" in result["reason"].lower()
    noted = refusals.drain()
    assert len(noted) == 1
    assert noted[0]["tool"] == "create_grid_executor"
    assert "leverage" in noted[0]["reason"].lower()


def test_loop_mode_still_approves_an_at_limit_create():
    callback = auto_approve_with_risk_check(
        RiskEngine(RiskLimits(max_leverage=5.0)),
        RiskState(),
        execution_mode="loop",
        price_client=_PriceClient(),
    )

    result = asyncio.run(callback(_grid_call(leverage=5), _OPTIONS))

    assert result["outcome"]["outcome"] == "selected"


def test_loop_mode_refuses_raising_account_leverage_above_the_limit():
    refusals = RefusalLog()
    callback = auto_approve_with_risk_check(
        RiskEngine(RiskLimits(max_leverage=5.0)),
        RiskState(),
        execution_mode="loop",
        refusals=refusals,
    )

    result = asyncio.run(callback(_leverage_call(leverage=20), _OPTIONS))

    assert result["outcome"]["outcome"] == "cancelled"
    assert "leverage" in result["reason"].lower()
    assert refusals.drain()[0]["tool"] == LEVERAGE_TOOL


def test_loop_mode_allows_account_leverage_at_the_limit():
    callback = auto_approve_with_risk_check(
        RiskEngine(RiskLimits(max_leverage=5.0)),
        RiskState(),
        execution_mode="loop",
    )
    result = asyncio.run(callback(_leverage_call(leverage=5), _OPTIONS))
    assert result["outcome"]["outcome"] == "selected"


def test_a_position_mode_only_call_sets_no_leverage_and_passes():
    """No ``leverage`` on this tool means it changes none — nothing to bound."""
    callback = auto_approve_with_risk_check(
        RiskEngine(RiskLimits(max_leverage=5.0)),
        RiskState(),
        execution_mode="loop",
    )
    result = asyncio.run(callback(_leverage_call(position_mode="HEDGE"), _OPTIONS))
    assert result["outcome"]["outcome"] == "selected"


def test_an_unreadable_account_leverage_fails_closed():
    engine = RiskEngine(RiskLimits(max_leverage=5.0))
    allowed, reason = engine.check_leverage_action(_leverage_call(leverage="lots"))
    assert not allowed
    assert "could not be read" in reason


def test_a_position_create_is_gated_too():
    engine = RiskEngine(RiskLimits(max_leverage=5.0))
    assert not engine.check_executor_action(
        _position_call(leverage=20), RiskState(), 100.0
    )[0]
    assert engine.check_executor_action(_position_call(leverage=2), RiskState(), 100.0)[
        0
    ]


# ---------------------------------------------------------------------------
# The attended seat, and the log
# ---------------------------------------------------------------------------


def test_the_leverage_tool_now_needs_a_human():
    assert LEVERAGE_TOOL in DANGEROUS_TOOLS
    assert is_dangerous_tool_call(_leverage_call(leverage=20))
    assert is_dangerous_tool_call(
        {"tool": f"mcp__mcp-hummingbot__{LEVERAGE_TOOL}", "input": {}}
    )


def test_the_leverage_tool_is_recorded():
    call = _leverage_call(leverage=20)
    assert is_mutating_tool_call(call)
    assert is_recordable_tool_call(call)


def test_the_confirmation_line_names_connector_pair_and_leverage():
    summary = format_tool_summary(_leverage_call(leverage=20))
    assert "20x" in summary
    assert "SOL-USDT" in summary
    assert "binance_perpetual" in summary

    both = format_tool_summary(_leverage_call(leverage=3, position_mode="HEDGE"))
    assert "3x" in both and "HEDGE" in both


# ---------------------------------------------------------------------------
# The agent is told about the limit it is held to
# ---------------------------------------------------------------------------


def _tick_prompt(risk_state: dict) -> str:
    agent = SimpleNamespace(instructions="", agent_key="claude-code", slug="agt")
    strategy = SimpleNamespace(
        instructions="Run the grid.",
        agent_key="claude-code",
        slug="grid",
        agent_slug="agt",
        dir=None,
    )
    return build_tick_prompt(
        agent=agent,
        strategy=strategy,
        config={"execution_mode": "loop"},
        core_data={},
        learnings="",
        summary="",
        recent_decisions="",
        risk_state=risk_state,
        cached_routines_section="",
    )


def test_the_risk_state_block_shows_the_leverage_limit_when_set():
    # Built the way the engine builds it, from RiskState.to_dict
    state = RiskState()
    state._limits = RiskLimits(max_leverage=5.0)
    prompt = _tick_prompt(state.to_dict())
    assert "Max Leverage: 5x" in prompt


def test_the_risk_state_block_stays_quiet_when_the_limit_is_off():
    state = RiskState()
    state._limits = RiskLimits()
    assert "Max Leverage" not in _tick_prompt(state.to_dict())
