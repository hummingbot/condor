"""A limit is rendered in the token it is measured in, on both surfaces (LP-2).

The risk gate's limits have always been denominated in "quote", and quote is
whatever the strategy trades against: USDT for the CEX market makers, SOL for
``solana_dex_lp_expert``'s LP operator. Every surface printed a ``$`` anyway, so
the LP operator's 500 SOL ceiling was shown as ``$500`` — three orders of
magnitude out, to the one audience that acts on the figure.

The first fix reached only the tick prompt's risk block. The gate's *refusals*
kept their hardcoded ``$``, and those are the more consequential of the two: a
refusal is the message the model reasons from when it decides what to try
instead, so an LP strategy was told it had exceeded "$500.00" of a ceiling
measured in SOL.

Both now go through ``condor.agents.quote_render``, and these tests hold the two
together — a limit described in two places must not be described in two units.
"""

import asyncio

from condor.agents.journal import render_risk_lines
from condor.agents.quote_render import DOLLAR_QUOTES, format_quote, is_dollar_quote
from condor.agents.risk import (
    RiskEngine,
    RiskLimits,
    RiskState,
    auto_approve_with_risk_check,
)

OPTIONS = [{"optionId": "allow", "kind": "allow_once"}]


class _Controllers:
    def __init__(self, sizes: dict):
        self.sizes = sizes

    async def get_controller_config(self, config_id: str):
        return {"total_amount_quote": self.sizes[config_id]}


class _Client:
    def __init__(self, sizes: dict):
        self.controllers = _Controllers(sizes)


def _deploy(limit: float, quote: str, sizes: dict, **args) -> dict:
    engine = RiskEngine(RiskLimits(max_position_size_quote=limit), quote_asset=quote)
    gate = auto_approve_with_risk_check(
        engine,
        RiskState(),
        execution_mode="loop",
        price_client=_Client(sizes),
    )
    call = {
        "tool": "manage_bots",
        "input": {"action": "deploy", "bot_name": "b", **args},
    }
    return asyncio.run(gate(call, OPTIONS))


# ── the formatter ───────────────────────────────────────────────────────────


def test_a_dollar_quote_renders_as_dollars():
    assert format_quote(500, "USDT") == "$500.00"
    assert format_quote(1234.5, "USDC") == "$1,234.50"


def test_an_unset_quote_renders_as_dollars():
    """The default, and what every caller that does not say gets."""
    assert format_quote(500) == "$500.00"
    assert format_quote(500, "") == "$500.00"
    assert format_quote(500, None) == "$500.00"


def test_a_token_quote_renders_in_that_token():
    assert format_quote(500, "SOL") == "500.0000 SOL"
    assert format_quote(0.03, "sol") == "0.0300 SOL"


def test_a_token_figure_keeps_enough_decimals_to_be_read():
    """An LP slot is a fraction of one SOL. Two places would render 0.0275 as
    ``0.03``, which reads as a rounding of anything from 0.025 to 0.035."""
    assert format_quote(0.0275, "SOL") == "0.0275 SOL"


def test_a_value_that_is_not_a_number_renders_rather_than_raises():
    """Every caller is building a message, and on the refusal path raising here
    would escape the permission callback and skip the RefusalLog entirely."""
    assert format_quote(None, "SOL") == "0.0000 SOL"
    assert format_quote("", "USDT") == "$0.00"
    assert format_quote("not a number", "USDT") == "$0.00"


def test_every_dollar_quote_is_treated_as_one():
    assert all(is_dollar_quote(q) for q in DOLLAR_QUOTES)
    assert all(is_dollar_quote(q.lower()) for q in DOLLAR_QUOTES)
    assert not is_dollar_quote("SOL")
    assert not is_dollar_quote("ETH")


# ── the prompt block ────────────────────────────────────────────────────────


def test_the_prompt_block_uses_the_strategys_unit():
    lines = " ".join(
        render_risk_lines(
            {
                "quote_asset": "SOL",
                "total_exposure": 0.12,
                "max_position_size": 0.5,
                "max_drawdown_pct": 15.0,
            }
        )
    )

    assert "0.1200 SOL" in lines
    assert "0.5000 SOL" in lines
    assert "$" not in lines


def test_the_prompt_block_still_renders_dollars_by_default():
    lines = " ".join(
        render_risk_lines({"total_exposure": 120.0, "max_position_size": 500.0})
    )

    assert "$120.00" in lines
    assert "$500.00" in lines


# ── the refusals ────────────────────────────────────────────────────────────


def test_a_refused_deploy_names_the_limit_in_the_strategys_unit():
    """The gap this closes: the LP operator reading "$0.50" for 0.5 SOL."""
    result = _deploy(
        0.5,
        "SOL",
        {"cfg": 2.0},
        controllers_config=["cfg"],
        max_global_drawdown_quote=0.1,
    )

    assert result["outcome"]["outcome"] == "cancelled"
    reason = result["reason"]
    assert "2.0000 SOL" in reason, reason
    assert "0.5000 SOL" in reason, reason
    assert "$" not in reason, reason


def test_a_refused_kill_switch_cap_is_in_the_strategys_unit():
    """MM-4's refusal: a cap at or above the capital it guards."""
    result = _deploy(
        10.0,
        "SOL",
        {"cfg": 2.0},
        controllers_config=["cfg"],
        max_global_drawdown_quote=2.0,
    )

    assert result["outcome"]["outcome"] == "cancelled"
    reason = result["reason"]
    assert "2.0000 SOL" in reason, reason
    assert "$" not in reason, reason


def test_a_cap_over_the_position_limit_is_in_the_strategys_unit():
    result = _deploy(
        0.5,
        "SOL",
        {"cfg": 0.1},
        controllers_config=["cfg"],
        max_global_drawdown_quote=5.0,
    )

    assert result["outcome"]["outcome"] == "cancelled"
    reason = result["reason"]
    assert "5.0000 SOL" in reason and "0.5000 SOL" in reason, reason
    assert "$" not in reason, reason


def test_a_missing_cap_is_no_longer_refused_regardless_of_unit():
    """condor#303: the cap is optional, so a deploy within the position limit
    goes through with no cap declared, whatever the strategy's quote asset."""
    result = _deploy(0.5, "SOL", {"cfg": 0.1}, controllers_config=["cfg"])

    assert result["outcome"]["outcome"] == "selected"


def test_an_executor_create_over_the_limit_is_in_the_strategys_unit():
    """``check_executor_action``'s refusal, which the LP loop reaches too."""
    engine = RiskEngine(RiskLimits(max_position_size_quote=0.5), quote_asset="SOL")
    state = RiskState(total_exposure=0.4)

    allowed, reason = engine.check_executor_action(
        {"tool": "create_position_executor", "input": {}},
        state,
        planned_amount_quote=0.3,
    )

    assert allowed is False
    assert "0.7000 SOL" in reason and "0.5000 SOL" in reason, reason
    assert "$" not in reason, reason


def test_a_dollar_quoted_strategy_is_refused_in_dollars_exactly_as_before():
    """The regression guard. Every CEX strategy quotes in a dollar stablecoin,
    and none of those messages may change."""
    result = _deploy(
        10,
        "USDT",
        {"cfg": 60},
        controllers_config=["cfg"],
        max_global_drawdown_quote=5,
    )

    assert result["outcome"]["outcome"] == "cancelled"
    assert "$60.00" in result["reason"]
    assert "$10.00" in result["reason"]


def test_the_two_surfaces_describe_one_limit_in_one_unit():
    """What the module exists for: the prompt and the refusal are read by the
    same agent, about the same ceiling, within one tick."""
    limit = 0.5
    engine = RiskEngine(RiskLimits(max_position_size_quote=limit), quote_asset="SOL")
    allowed, reason = engine.check_executor_action(
        {"tool": "create_position_executor", "input": {}},
        RiskState(),
        planned_amount_quote=0.9,
    )
    prompt = " ".join(
        render_risk_lines({"quote_asset": "SOL", "max_position_size": limit})
    )

    assert allowed is False
    rendered = format_quote(limit, "SOL")
    assert rendered in reason and rendered in prompt


def test_a_refused_dex_signature_is_in_the_strategys_unit():
    """``check_dex_action`` is the only thing bounding a loop's DEX capital, and
    the LP operator is exactly the strategy that quotes in SOL."""
    engine = RiskEngine(RiskLimits(max_position_size_quote=0.5), quote_asset="SOL")

    allowed, reason = engine.check_dex_action(
        {"tool": "execute_swap", "input": {}},
        RiskState(total_exposure=0.4),
        notional_quote=0.3,
    )

    assert allowed is False
    assert "0.7000 SOL" in reason and "0.5000 SOL" in reason, reason
    assert "$" not in reason, reason


def test_a_refused_update_config_is_in_the_strategys_unit():
    engine = RiskEngine(RiskLimits(max_position_size_quote=0.5), quote_asset="SOL")

    allowed, reason = engine.check_bot_action(
        {
            "tool": "manage_bots",
            "input": {
                "action": "update_config",
                "config_data": {"total_amount_quote": 2.0},
            },
        },
        RiskState(),
    )

    assert allowed is False
    assert "2.0000 SOL" in reason and "0.5000 SOL" in reason, reason
    assert "$" not in reason, reason
