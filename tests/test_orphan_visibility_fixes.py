"""Adversarial-review fixes: orphan-recovery honesty and signal grading.

Two ways an agent was misled about what it was looking at:

- format_clmm_result's position_info branch read camelCase keys against the
  API's snake_case CLMMPositionInfo fields, so every field — including the
  position address — rendered as None and "the position is gone" was a
  reasonable (wrong) conclusion.
- options_flow confidence graded agreement against the dominant sub-signal vote
  instead of the emitted direction, stamping MEDIUM on entries the majority of
  signals disagreed with.
"""

import asyncio

from condor.agents.performance import AgentPerformance
from condor.agents.providers.executors import ExecutorsProvider


def _run_provider(executors):
    import condor.agents.performance as perf_mod

    perf = AgentPerformance(agent_id="agent.strat_1", bot_names=["bot_1"])
    perf.executors = executors

    async def _fake(*a, **kw):
        return perf

    original = perf_mod.fetch_agent_performance
    perf_mod.fetch_agent_performance = _fake
    try:
        return asyncio.run(
            ExecutorsProvider().execute(
                object(), {"bot_name": "bot_1"}, agent_id="agent.strat_1"
            )
        )
    finally:
        perf_mod.fetch_agent_performance = original


def _row(**overrides):
    row = {
        "id": "exec-1",
        "type": "lp_executor",
        "pair": "SOL-USDC",
        "status": "TERMINATED",
        "close_type": "",
        "custom_info": {},
        "pnl": 0.0,
        "volume": 0.0,
        "amount": 0.0,
    }
    row.update(overrides)
    return row


def test_address_bearing_orphan_still_emits_the_close_call():
    result = _run_provider(
        [
            _row(
                close_type="POSITION_HOLD",
                custom_info={
                    "hold_reason": "close_retries_exhausted",
                    "position_address": "H4vD69Ds",
                },
            )
        ]
    )
    assert "🚨 ORPHANED POSITION" in result.summary
    assert 'manage_clmm(action="close"' in result.summary


def test_position_info_formats_the_apis_snake_case_fields():
    """The formatter must read what /gateway/clmm/positions_owned sends."""
    from mcp_servers.hummingbot_api.formatters.gateway import format_clmm_result

    out = format_clmm_result(
        "position_info",
        {
            "action": "position_info",
            "connector": "meteora/clmm",
            "network": "solana-mainnet-beta",
            "pool_address": "PoolAddr",
            "result": [
                {
                    "position_address": "PosAddr123",
                    "base_token_amount": 1.5,
                    "quote_token_amount": 200.0,
                    "lower_price": 100.0,
                    "upper_price": 140.0,
                    "current_price": 120.0,
                    "base_fee_amount": 0.01,
                    "quote_fee_amount": 1.2,
                }
            ],
        },
    )
    assert "PosAddr123" in out
    assert "1.5" in out and "200.0" in out
    assert "100.0–140.0" in out
    assert "120.0" in out
    assert "0.01" in out and "1.2" in out
    assert "None" not in out


def test_confidence_is_graded_against_the_emitted_direction():
    from agents.derive_options_trader.routines.options_flow import _confidence

    # The finding's exact scenario: rr=+1.0, oi=-0.25, ts=-0.5 with the
    # negative-GEX 1.25 amplifier → composite +0.42 → LONG, while two of three
    # votes say SHORT. Dominant-vote grading called this MEDIUM; only one vote
    # agrees with LONG, so it is LOW — below the entry threshold.
    assert _confidence(+0.42, 1.0, -0.25, -0.5) == "LOW"
    # All three agreeing with the emitted direction is HIGH either way.
    assert _confidence(+0.6, 0.5, 0.3, 0.2) == "HIGH"
    assert _confidence(-0.6, -0.5, -0.3, -0.2) == "HIGH"
    # Two of three agreeing with the emitted direction is MEDIUM.
    assert _confidence(+0.45, 0.5, 0.3, -0.2) == "MEDIUM"
    # A zero composite never grades above LOW.
    assert _confidence(0.0, 1.0, 1.0, 1.0) == "LOW"
