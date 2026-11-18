"""The [CURRENT CONFIG] block no longer tells the agent to ignore the loop (FEAT-183).

``trading_requirements`` and ``total_amount_quote`` are read by the loop prompt
and by ``condor/preflight.py`` from the same ``config.yml`` (see
``condor.agents.config.load_full_config``), so the block that injects them had
no reason to frame itself as overriding the loop's own instructions — a loop
written to this convention is already describing the same values, not a
competing set. The old wording ("IGNORE them") could read as contradicting a
loop that correctly deferred to [CURRENT CONFIG] in its own prose.
"""

from __future__ import annotations

from types import SimpleNamespace

from condor.agents.prompts import build_tick_prompt


def _tick_prompt(config: dict) -> str:
    agent = SimpleNamespace(instructions="", agent_key="claude-code", slug="brigado")
    strategy = SimpleNamespace(
        instructions="Do the thing.",
        agent_key="claude-code",
        slug="grid",
        agent_slug="brigado",
        dir=None,
    )
    return build_tick_prompt(
        agent=agent,
        strategy=strategy,
        config=config,
        core_data={},
        learnings="",
        summary="",
        recent_decisions="",
        risk_state={},
        cached_routines_section="",
    )


def test_current_config_no_longer_says_ignore_the_loop():
    prompt = _tick_prompt({"execution_mode": "loop", "total_amount_quote": 500.0})

    assert "[CURRENT CONFIG]" in prompt
    assert "IGNORE" not in prompt


def test_current_config_names_the_shared_source_with_preflight():
    prompt = _tick_prompt({"execution_mode": "loop", "total_amount_quote": 500.0})

    assert "config.yml" in prompt
    assert "preflight" in prompt


def test_current_config_still_carries_trading_requirements_and_capital():
    prompt = _tick_prompt(
        {
            "execution_mode": "loop",
            "total_amount_quote": 500.0,
            "trading_requirements": {
                "connector": "binance",
                "trading_pairs": ["SOL-USDC"],
            },
        }
    )

    assert "total_amount_quote: 500.0" in prompt
    assert "trading_requirements:" in prompt
