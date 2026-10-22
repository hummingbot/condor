"""The LP skills and the loop playbook say the same thing (LP-9).

Five topics, five disagreements, and every one ran the same way: the skill was
vaguer than the loop, stricter than the loop, or reimplemented something the loop
does in a single call. An agent reading both got a different strategy depending
on which text it reached for.

- **Range shape** — the skill said the RANGE case is "centered" on P. The loop
  places P *asymmetrically* by ``base_pct``, because even (Spot) liquidity
  spreads uniformly and a centred range therefore delivers 50/50 whatever
  ``base_pct`` asked for. Following the skill silently ignored the setting.
- **Venue clamp** — the skill bounded Orca/Raydium by "the connector's
  per-position limit". The loop knows the number is ≤120 spacings and carries the
  incident that establishes it: identical 20.8% widths, ``tick_spacing`` 16 → 118
  and it opened, 8 → 237 and it failed with no funds moved.
- **Idle exit** — the skill required OUT_OF_RANGE *and* an OHLCV trend
  confirmation. The loop measures ``out_of_range_seconds`` against
  ``out_of_range_max_sec`` and treats OHLCV as an exception. Requiring it held
  dead slots open exactly when the trend was ambiguous.
- **Pool selection** — the skill spelled out a hand-scan over GeckoTerminal. The
  loop says "do NOT hand-scan" and runs ``lp_scanner`` once.
- **Tick budget** — the loop allows eight tool calls and one OHLCV check. The
  skill's procedure spent more than that before choosing a pool.

These assert agreement on the *facts*, not matching prose: a threshold stated in
both places has to be the same number.
"""

import re
from pathlib import Path

import pytest

LP = Path("agents/solana_dex_lp_expert")
LOOP = LP / "loops/lp_slot_operator/loop.md"
SKILLS = {p.parent.name: p for p in (LP / "skills").glob("*/SKILL.md")}


def _flat(path: Path) -> str:
    """File text with runs of whitespace collapsed, so wrapping cannot hide a
    phrase — the failure mode that bit three assertions in this effort."""
    return " ".join(path.read_text().split())


@pytest.fixture(scope="module")
def loop() -> str:
    return _flat(LOOP)


def test_the_skills_under_test_exist():
    """A glob that matches nothing passes everything below."""
    assert {"pool_ranking", "lp_range_config", "slot_exit"} <= set(SKILLS)


# ── range shape ─────────────────────────────────────────────────────────────


def test_no_skill_still_calls_the_range_centered(loop):
    assert "asymmetrically" in loop

    body = _flat(SKILLS["lp_range_config"])
    assert "**centered** on P" not in body
    assert "asymmetric" in body


def test_the_skill_names_base_pct_as_what_splits_the_width():
    body = _flat(SKILLS["lp_range_config"])

    assert "base_pct" in body
    assert "NOT centred" in body or "NOT centered" in body


def test_it_defers_on_the_per_venue_flip():
    """Which side is the memecoin side flips with the venue's price convention,
    and the loop works it out per venue — the skill must not re-derive it."""
    body = _flat(SKILLS["lp_range_config"])

    assert "flips with the venue" in body
    assert "loop playbook" in body


# ── the venue clamp is the same number in both ──────────────────────────────


def _limits(text: str) -> dict[str, int]:
    """``{venue: cap}`` for the bin/spacing caps a text states."""
    out = {}
    if m := re.search(r"bins\s*[=≈][^<]{0,90}?<\s*\*{0,2}(\d+)", text):
        out["meteora"] = int(m.group(1))
    if m := re.search(r"spacings\s*=[^≤]{0,120}?≤\s*\*{0,2}(\d+)", text):
        out["orca"] = int(m.group(1))
    return out


def test_the_meteora_bin_cap_matches(loop):
    assert _limits(loop)["meteora"] == 69
    assert _limits(_flat(SKILLS["lp_range_config"]))["meteora"] == 69


def test_the_orca_spacing_cap_is_stated_in_both(loop):
    """The skill said "within the connector's per-position limit", which is not
    a number anyone can check a range against."""
    assert _limits(loop)["orca"] == 120
    assert _limits(_flat(SKILLS["lp_range_config"]))["orca"] == 120


def test_both_say_the_cap_is_read_per_pool(loop):
    body = _flat(SKILLS["lp_range_config"])

    assert "per pool" in loop and "per pool" in body
    assert "tick_spacing" in body


# ── the idle exit is a timer, with OHLCV as the exception ───────────────────


def test_the_exit_threshold_is_the_same_field(loop):
    body = _flat(SKILLS["slot_exit"])

    assert "out_of_range_max_sec" in loop
    assert "out_of_range_max_sec" in body
    assert "out_of_range_seconds" in body


def test_the_default_window_agrees(loop):
    assert "1800" in loop
    assert "1800" in _flat(SKILLS["slot_exit"])


def test_ohlcv_is_an_exception_not_a_requirement():
    """``AND OHLCV trend continues away`` made the confirmation mandatory, so an
    ambiguous trend kept a dead slot open."""
    body = _flat(SKILLS["slot_exit"])
    # Before the note that quotes the old wording to explain why it was wrong —
    # the prose is not the rule.
    triggers = body.split("> This trigger used to read")[0]

    assert "Exception, not a requirement" in body
    assert "sustained period AND OHLCV" not in triggers


def test_the_exit_skill_knows_a_slot_can_close_itself():
    """The autonomous limits fire in the executor with no tick involved."""
    body = _flat(SKILLS["slot_exit"])

    assert "lower_limit_price" in body
    assert "no tick involved" in body


# ── pool selection runs the routine ─────────────────────────────────────────


def test_the_loop_forbids_hand_scanning(loop):
    assert "not** hand-scan" in loop.lower()


def test_the_skill_runs_the_routine_instead():
    body = _flat(SKILLS["pool_ranking"])

    assert 'manage_routines(action="run", name="lp_scanner"' in body
    assert "do NOT hand-scan" in body


def test_the_skill_no_longer_spells_out_the_scan():
    """Several ``explore_geckoterminal`` calls plus a per-candidate pool-info and
    OHLCV read — a dozen calls against a budget of eight."""
    body = _flat(SKILLS["pool_ranking"])

    assert 'explore_geckoterminal(action="trending_pools"' not in body
    assert 'explore_geckoterminal(action="top_pools"' not in body


def test_the_venue_ids_match_the_scanner_default():
    from agents.solana_dex_lp_expert.routines.lp_scanner import Config

    body = _flat(SKILLS["pool_ranking"])

    for venue in Config().venues:
        assert venue in body, venue
    # ...and the AMM that holds no CLMM positions is not offered as one.
    assert "`raydium`," not in body


# ── the tick budget is known to the skills ──────────────────────────────────


def test_the_loops_budget_is_stated(loop):
    assert "≤ 8 tool calls per tick" in loop
    assert "ONE slot per tick" in loop


@pytest.mark.parametrize("skill", ["pool_ranking", "slot_exit"])
def test_the_skills_respect_the_budget(skill):
    body = _flat(SKILLS[skill])

    assert "one slot per tick" in body.lower() or "OHLCV check per tick" in body


def test_one_ohlcv_check_per_tick_in_both(loop):
    assert "one OHLCV check max" in loop
    assert "OHLCV check per tick" in _flat(SKILLS["pool_ranking"])


# ── the precedence rule is written down ─────────────────────────────────────


def test_the_agent_says_which_source_wins():
    """So a sixth divergence is resolved rather than argued."""
    body = _flat(LP / "AGENT.md")

    assert "the loop wins" in body
    assert "LP-9" in body


def test_it_says_what_the_skills_are_still_for():
    """The rule must not read as "skills are worthless" — they carry the
    reasoning the loop has no room for."""
    body = _flat(LP / "AGENT.md")

    assert "carry the *reasoning*" in body
