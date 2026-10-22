"""The Market Making agent's four findings (MM-2, MM-5, MM-6, MM-8).

**MM-2** is the one that cost money. ``min_skew`` floors the order-SIZE skew
multiplier — each side is ``max(min(skew, 1), min_skew)`` — so a value above 1
pins both multipliers at that value, multiplying every order by it and disabling
skew entirely. The playbook shipped ``2.0`` for conservative and ``1.5`` for
balanced, turning a ~12.5 USDT order into ~24.7 while providing none of the
inventory protection it claimed. Every description called it a *spread*
multiplier, which it is not, and the balanced tuning note advised raising it
further. Same defect as hummingbot/condor#283.

**MM-5**: both operator loops deployed bots while running in *executor* mode, and
named them outside the session's namespace (``jto-mm``), so the ownership ledger
enforced nothing on what they deployed.

**MM-6**: four sources stated the controller's settings and all four disagreed.
There is now one order — the class for defaults, the profiles for overrides, the
session for capital — asserted here field by field.

**MM-8**: the ``mm_bot_report`` skill ran a routine of that name, which
``mm_dashboard`` had absorbed and which therefore does not exist, and documented
a ``recent_closes`` option that is in no config.
"""

import re
from pathlib import Path

import pytest
import yaml

MM = Path("agents/market_making_expert")
CONTROLLER = MM / "controllers/pmm_mister/pmm_mister.py"
PLAYBOOK = MM / "skills/pmm_config_playbook"
PROFILES = ["conservative", "balanced", "aggressive"]

#: Settings a profile and its sample config must agree on: the ones that shape
#: RISK. Deliberately not every field — a profile is regime-driven guidance and a
#: sample is a shipped starting file, so spreads, cooldowns, effectivization and
#: tolerances differ by design (the aggressive pair differs on fourteen). Claiming
#: they match "field for field" was wrong, and a test asserting it would either
#: fail or force someone to invent trading parameters to satisfy it.
#: ``total_amount_quote`` is absent for a different reason: it belongs to the
#: session, not to a profile.
SHARED_FIELDS = [
    "portfolio_allocation",
    "leverage",
    "min_skew",
    "max_active_executors_by_level",
    "executor_refresh_time",
    "global_stop_loss",
]


def _profile_json(name: str) -> dict:
    text = (PLAYBOOK / f"config_{name}.md").read_text()
    block = text.split("```json")[1].split("```")[0]
    # The capital is a placeholder pointing at the session, not a number.
    block = re.sub(r'"total_amount_quote":\s*"[^"]*"', '"total_amount_quote": 0', block)
    import json

    return json.loads(block)


def _sample_yaml(name: str) -> dict:
    return yaml.safe_load(
        (MM / f"controllers/pmm_mister/sample_configs/{name}.yml").read_text()
    )


def _class_defaults() -> dict[str, str]:
    src = CONTROLLER.read_text()
    out = {}
    for m in re.finditer(r"^\s+(\w+):.*?Field\(default=([^,)]+)", src, re.M):
        out[m.group(1)] = m.group(2).strip().replace("Decimal(", "").strip('"')
    return out


# ── MM-2: min_skew never above 1 ─────────────────────────────────────────────


@pytest.mark.parametrize("profile", PROFILES)
def test_no_profile_ships_a_skew_floor_above_one(profile):
    """Above 1 it multiplies EVERY order and turns skew off — the #283 defect."""
    assert _profile_json(profile)["min_skew"] <= 1.0


@pytest.mark.parametrize("profile", PROFILES)
def test_no_sample_config_ships_one_either(profile):
    assert float(_sample_yaml(profile)["min_skew"]) <= 1.0


def test_the_class_default_disables_skew_rather_than_inflating_orders():
    """1.0 is the one value at the boundary that is safe: it disables skew."""
    assert float(_class_defaults()["min_skew"]) == 1.0


@pytest.mark.parametrize("profile", PROFILES)
def test_the_profile_notes_call_it_a_size_floor(profile):
    """Every description called it a *spread* multiplier, which it is not — and
    a model that believes that will reach for a bigger number."""
    text = (PLAYBOOK / f"config_{profile}.md").read_text()
    note = next(line for line in text.splitlines() if line.startswith("- `min_skew`"))

    assert "spread" not in note.lower()


def test_nothing_advises_raising_it():
    """The balanced tuning note said "increase to 2.0+", which is backwards:
    lower is stronger protection."""
    for path in list(PLAYBOOK.glob("*.md")) + [MM / "AGENT.md"]:
        text = path.read_text()
        assert "min_skew` to 2.0" not in text, path
        assert "min_skew=2.0" not in text, path


def test_the_agent_md_description_is_accurate():
    text = (MM / "AGENT.md").read_text()
    body = text.split("**`min_skew`**")[1][:700]

    assert "SIZE" in body
    assert "Never set it above 1" in body


# ── MM-5: bots are deployed inside the namespace ─────────────────────────────


@pytest.mark.parametrize("loop", ["pmm_mister_operator", "hip_3_mm_operator"])
def test_both_operator_loops_run_in_controller_mode(loop):
    """They deploy bots; without this the ledger owns nothing they deploy."""
    front = yaml.safe_load((MM / f"loops/{loop}/loop.md").read_text().split("---")[1])

    assert front["default_config"]["bot_mode"] == "bot"


@pytest.mark.parametrize("loop", ["pmm_mister_operator", "hip_3_mm_operator"])
def test_the_derived_name_is_the_session_namespace(loop):
    from condor.agents.ownership import bot_namespace, resolve_bot_name

    front = yaml.safe_load((MM / f"loops/{loop}/loop.md").read_text().split("---")[1])

    assert resolve_bot_name(
        front["default_config"], "market_making_expert", loop
    ) == bot_namespace("market_making_expert", loop)


def test_the_playbook_no_longer_names_bots_outside_the_namespace():
    """``{base}-mm`` is refused by the risk gate and owned by nobody."""
    body = (MM / "loops/pmm_mister_operator/loop.md").read_text()

    assert "`bot_name` = `{base}-mm`" not in body
    assert "{namespace}-{base}" in body


# ── MM-6: one order of precedence, asserted ──────────────────────────────────


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("field", SHARED_FIELDS)
def test_each_profile_agrees_with_its_sample_config(profile, field):
    """Two sources for the same profile must not disagree."""
    playbook = float(_profile_json(profile)[field])
    sample = float(_sample_yaml(profile)[field])

    assert (
        playbook == sample
    ), f"{profile}.{field}: playbook {playbook} vs sample {sample}"


@pytest.mark.parametrize("profile", PROFILES)
def test_no_profile_names_its_own_capital(profile):
    """``total_amount_quote`` is the session's budget. A profile naming its own
    would contradict the loop, or breach the position limit the gate enforces."""
    raw = (PLAYBOOK / f"config_{profile}.md").read_text()

    assert '"total_amount_quote": "<total_amount_quote from [CURRENT CONFIG]>"' in raw


@pytest.mark.parametrize(
    "field,expected",
    [
        ("leverage", "20"),
        ("portfolio_allocation", "0.1"),
        ("max_active_executors_by_level", "4"),
        ("executor_refresh_time", "30"),
        ("buy_cooldown_time", "60"),
        ("position_profit_protection", "False"),
        ("take_profit", "0.0001"),
    ],
)
def test_agent_md_documents_the_class_default_not_a_guess(field, expected):
    """AGENT.md annotated defaults the class does not have — so an agent omitting
    a field got a value the docs never mentioned."""
    assert _class_defaults()[field] == expected

    header = re.search(
        rf"\*\*`{field}`\*\* \([a-z]+, default: ([^)]+)\)",
        (MM / "AGENT.md").read_text(),
    )
    assert header, f"no documented default for {field}"
    documented = header.group(1).strip().strip("`").split()[0].strip('"')

    assert (
        float(documented) == float(expected)
        if expected.replace(".", "").isdigit()
        else documented == expected
    )


def test_the_playbook_does_not_overclaim_the_match():
    """It said "field for field", which is false — and a reader who believed it
    would treat a legitimate tuning difference as a defect."""
    text = " ".join((PLAYBOOK / "SKILL.md").read_text().split())

    assert "field for field" not in text
    assert "settings that shape RISK" in text
    assert "not** copies of each other" in text or "not copies of each other" in text


@pytest.mark.parametrize("field", SHARED_FIELDS)
def test_the_risk_fields_are_named_in_the_doc(field):
    """The list the rule binds has to be visible, or it is not a rule."""
    text = " ".join((PLAYBOOK / "SKILL.md").read_text().split())

    assert f"`{field}`" in text


def test_the_playbook_states_the_order():
    text = " ".join((PLAYBOOK / "SKILL.md").read_text().split())

    assert "only authority on a *default*" in text
    assert "sample_configs" in text
    assert "[CURRENT CONFIG]" in text


# ── MM-8: the skill runs a routine that exists ───────────────────────────────


def test_the_agents_routines_are_what_the_skill_can_run():
    shipped = {p.stem for p in (MM / "routines").glob("*.py")}

    assert shipped == {"mm_dashboard", "market_analyzer", "hip3_market_scanner"}
    assert "mm_bot_report" not in shipped


def test_the_skill_points_at_a_routine_that_exists():
    text = (MM / "skills/mm_bot_report/SKILL.md").read_text()
    front = yaml.safe_load(text.split("---")[1])

    assert front["references_routine"] == "mm_dashboard"
    assert 'name="mm_dashboard"' in text
    assert 'name="mm_bot_report"' not in text


def test_the_documented_options_are_the_routines_real_fields():
    """``recent_closes`` was documented and is in no config — an agent passing it
    gets a rejection, not a filtered report."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mm_dashboard", MM / "routines/mm_dashboard.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fields = set(module.Config.model_fields)

    text = (MM / "skills/mm_bot_report/SKILL.md").read_text()
    documented = set(re.findall(r"^- `(\w+)` — ", text, re.M))

    assert "recent_closes" not in documented
    assert documented <= fields, f"documented but not in Config: {documented - fields}"
