"""A drawdown cap below one position is a kill switch that fires on deploy."""

import asyncio

import pytest

from condor.agents.risk import (
    RiskEngine,
    RiskLimits,
    RiskState,
    _largest_position_quote,
)


class FakeControllers:
    def __init__(self, configs):
        self.configs = configs

    async def get_controller_config(self, config_id):
        if config_id not in self.configs:
            raise RuntimeError(f"404 {config_id}")
        return self.configs[config_id]


class FakeClient:
    def __init__(self, configs):
        self.controllers = FakeControllers(configs)


LP_CONFIG = {
    "orca_s10": {
        "total_amount_quote": 12,
        "lp_provider": "orca/clmm",
        "lp_positions": [{"allocation_pct": 50}, {"allocation_pct": 50}],
    }
}
SPOT_CONFIG = {
    "churn_default": {"total_amount_quote": 12, "trading_pair": "USD1-USDT"},
}


def _deploy(cap, configs=("orca_s10",)):
    return {
        "tool": "manage_bots",
        "input": {
            "action": "deploy",
            "bot_name": "b",
            "controllers_config": list(configs),
            "max_global_drawdown_quote": cap,
        },
    }


# ----------------------------------------------------------------------
# Resolving the largest position
# ----------------------------------------------------------------------


def test_position_size_comes_from_the_lp_allocations():
    got = asyncio.run(
        _largest_position_quote({"controllers_config": ["orca_s10"]},
                                FakeClient(LP_CONFIG))
    )
    assert got == pytest.approx(6.0), "12 USDC split 50/50 is two 6 USDC positions"


def test_even_split_when_only_max_positions_is_declared():
    configs = {"c": {"total_amount_quote": 12, "lp_provider": "orca/clmm",
                     "max_positions": 3}}
    got = asyncio.run(_largest_position_quote({"controllers_config": ["c"]},
                                              FakeClient(configs)))
    assert got == pytest.approx(4.0)


def test_the_whole_book_when_nothing_says_how_it_is_split():
    configs = {"c": {"total_amount_quote": 12, "lp_provider": "orca/clmm"}}
    got = asyncio.run(_largest_position_quote({"controllers_config": ["c"]},
                                              FakeClient(configs)))
    assert got == pytest.approx(12.0)


def test_a_spot_market_maker_is_not_measured():
    """Its fills are matched orders; the phantom tick does not apply to it."""
    got = asyncio.run(
        _largest_position_quote({"controllers_config": ["churn_default"]},
                                FakeClient(SPOT_CONFIG))
    )
    assert got is None


def test_no_configs_or_no_client_measures_nothing():
    assert asyncio.run(_largest_position_quote({"controllers_config": []},
                                               FakeClient(LP_CONFIG))) is None
    assert asyncio.run(_largest_position_quote({"controllers_config": ["orca_s10"]},
                                               None)) is None


# ----------------------------------------------------------------------
# The refusal
# ----------------------------------------------------------------------


def _gate(cap, largest, configs=("orca_s10",)):
    engine = RiskEngine(RiskLimits(max_position_size_quote=1000))
    return engine.check_bot_action(
        _deploy(cap, configs),
        RiskState(),
        capital_quote=12.0,
        largest_position_quote=largest,
    )


def test_a_cap_below_one_position_is_refused():
    allowed, reason = _gate(3.0, 6.0)
    assert allowed is False
    assert "$6.00 of the largest position" in reason
    assert "breached on the deploy tick" in reason


def test_a_cap_above_one_position_and_below_the_book_is_allowed():
    assert _gate(9.0, 6.0) == (True, "")


def test_a_cap_equal_to_one_position_is_allowed():
    assert _gate(6.0, 6.0) == (True, "")


def test_an_unknown_position_size_leaves_the_gate_unchanged():
    assert _gate(3.0, None) == (True, "")


def test_the_floor_does_not_apply_without_a_declared_cap():
    """An omitted cap has nothing to compare against the floor."""
    engine = RiskEngine(RiskLimits(max_position_size_quote=1000))
    call = {
        "tool": "manage_bots",
        "input": {"action": "deploy", "bot_name": "b",
                  "controllers_config": ["orca_s10"]},
    }
    assert engine.check_bot_action(call, RiskState(), 12.0, 6.0) == (True, "")
