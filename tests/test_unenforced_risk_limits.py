"""A risk limit this build cannot enforce is said out loud, not dropped (LP-3, CO-4).

``RiskLimits.from_dict`` filtered its input to its own dataclass fields, so a key
it did not recognise vanished without a word. ``lp_slot_operator`` ships
``min_wallet_sol_reserve: 0.3`` and ``max_open_slots: 3`` in its ``risk_limits``
to this day: to whoever wrote the loop those are configured limits, to the
runtime they were never there. QA found neither in ``[CURRENT CONFIG]`` nor
``[RISK STATE]`` while the loop sized its slots as though the reserve were held
back. ``agent_builder`` hit the same silence from the other side, offering
``max_open_bots: 1`` and watching the session keep five executors (CO-4).

The fix is to make the gap *sayable*. An unrecognised key is carried on
``RiskLimits.unrecognized`` and surfaced three times, once per audience:

- ``[RISK STATE]`` in the tick prompt and every snapshot — the agent, which
  would otherwise plan around a cap that bounds nothing;
- a WARNING at engine start — the operator, who is reading logs, not prompts;
- the risk-state dict itself, so the dashboard and the journal inherit it.

Deliberately NOT a refusal: raising would stop a shipped agent from starting,
which trades a limit that does nothing for a loop that does nothing. These tests
pin that choice too, since it is the one a later reader is most likely to undo.
"""

import logging

import pytest

from condor.agents.journal import render_risk_lines
from condor.agents.risk import RiskLimits, RiskState

LP_LIMITS = {
    "max_position_size_quote": 500,
    "min_wallet_sol_reserve": 0.3,
    "max_open_slots": 3,
}


# ── what from_dict now keeps ─────────────────────────────────────────────────


def test_an_unknown_key_is_recorded_instead_of_dropped():
    """The LP-3 regression, in the shape lp_slot_operator actually ships."""
    limits = RiskLimits.from_dict(LP_LIMITS)

    assert sorted(limits.unrecognized) == ["max_open_slots", "min_wallet_sol_reserve"]
    assert limits.unrecognized["min_wallet_sol_reserve"] == 0.3


def test_the_known_limits_are_unaffected():
    """Recording the rest must not disturb what the gate actually enforces."""
    limits = RiskLimits.from_dict(LP_LIMITS)

    assert limits.max_position_size_quote == 500
    assert limits.max_open_executors == RiskLimits().max_open_executors
    assert limits.max_leverage == -1.0


def test_a_config_this_build_understands_records_nothing():
    """No new noise for the configs that were always fine."""
    limits = RiskLimits.from_dict(
        {"max_position_size_quote": 250, "max_open_executors": 3, "max_leverage": 5}
    )
    assert limits.unrecognized == {}


def test_an_empty_config_is_the_plain_defaults():
    assert RiskLimits.from_dict({}) == RiskLimits()


def test_a_typo_lands_where_it_can_be_seen():
    """The case this helps most: a limit the author believes they set."""
    limits = RiskLimits.from_dict({"max_postion_size_quote": 50})

    assert "max_postion_size_quote" in limits.unrecognized
    # ...and the real limit kept its default rather than the typo's value.
    assert limits.max_position_size_quote == 500.0


def test_unrecognized_cannot_be_set_from_config():
    """A field, but never a setting — a config must not describe its own leftovers."""
    limits = RiskLimits.from_dict({"unrecognized": {"smuggled": 1}})

    assert "smuggled" not in limits.unrecognized
    assert list(limits.unrecognized) == ["unrecognized"]


def test_an_unknown_key_is_not_a_refusal():
    """The deliberate choice: lp_slot_operator must still start.

    Raising here would be louder and worse — a shipped agent that cannot start
    at all, instead of one whose extra limit is visibly inert.
    """
    limits = RiskLimits.from_dict({"max_open_bots": 1})  # CO-4's key
    assert limits.unrecognized == {"max_open_bots": 1}


# ── who gets told ────────────────────────────────────────────────────────────


def test_the_risk_state_carries_them_to_the_prompt_and_snapshots():
    state = RiskState(limits=RiskLimits.from_dict(LP_LIMITS)).to_dict()

    assert state["unrecognized_limits"] == ["max_open_slots", "min_wallet_sol_reserve"]


def test_the_agent_is_told_they_bound_nothing():
    """The half that prevents the LP-3 harm: the loop planned around a slot cap."""
    state = RiskState(limits=RiskLimits.from_dict(LP_LIMITS)).to_dict()

    line = next(line for line in render_risk_lines(state) if "NOT ENFORCED" in line)
    assert "min_wallet_sol_reserve" in line
    assert "max_open_slots" in line
    assert "bound nothing" in line


def test_a_clean_config_adds_no_line():
    """No standing noise in the prompt for the configs that were always fine."""
    state = RiskState(limits=RiskLimits.from_dict({"max_open_executors": 5})).to_dict()

    assert not any("NOT ENFORCED" in line for line in render_risk_lines(state))


def test_a_risk_block_with_no_such_key_still_renders():
    """``render_risk_lines`` keeps a default for every value: a failed dry run
    records ``risk_state={}`` and must still render."""
    assert render_risk_lines({})
    assert not any("NOT ENFORCED" in line for line in render_risk_lines({}))


@pytest.mark.parametrize("value", [None, [], {}])
def test_an_empty_or_missing_value_adds_no_line(value):
    assert not any(
        "NOT ENFORCED" in line
        for line in render_risk_lines({"unrecognized_limits": value})
    )


def _engine(tmp_path, monkeypatch, risk_limits: dict):
    """A real ``TickEngine``, built the way the start route builds one."""
    from condor.agents.agent import Agent
    from condor.agents.engine import TickEngine
    from condor.agents.strategy import Strategy

    monkeypatch.setenv("CONDOR_AGENTS_ROOT", str(tmp_path / "agents"))
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(tmp_path / "reports"))
    strategy = Strategy(agent_slug="lp", name="Slots")
    strategy.home.mkdir(parents=True, exist_ok=True)
    return TickEngine(
        agent=Agent(slug="lp", name="LP"),
        strategy=strategy,
        config={"execution_mode": "loop", "risk_limits": risk_limits},
        chat_id=1,
        user_id=1,
    )


def test_the_operator_gets_a_warning_at_engine_start(tmp_path, monkeypatch, caplog):
    """The other audience: whoever wrote the limit reads logs, not prompts.

    Built through a real engine rather than asserted against the source text,
    so it covers the edge that actually fires — a construction that stopped
    calling this would still have matched a grep for the attribute name.
    """
    from condor.agents import engine as engine_mod

    with caplog.at_level(logging.WARNING, logger=engine_mod.log.name):
        engine = _engine(tmp_path, monkeypatch, LP_LIMITS)

    assert "not enforced" in caplog.text
    assert "min_wallet_sol_reserve" in caplog.text
    assert "max_open_slots" in caplog.text
    # The engine is still usable: the limits it DOES know are in force.
    assert engine.risk.limits.max_position_size_quote == 500


def test_a_clean_config_warns_about_nothing(tmp_path, monkeypatch, caplog):
    from condor.agents import engine as engine_mod

    with caplog.at_level(logging.WARNING, logger=engine_mod.log.name):
        _engine(tmp_path, monkeypatch, {"max_open_executors": 3})

    assert "not enforced" not in caplog.text
