from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from condor.agents.agent import AgentStore
from condor.agents.config import load_full_config
from condor.agents.prompts import build_tick_prompt
from condor.agents.strategy import StrategyStore
from condor.memory.skills import SkillStore
from routines.base import discover_routines_from_path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROUTINES = {"lp_snapshot", "lp_swap", "lp_create"}
SKILLS = {
    "lp_pool_review",
    "lp_range_and_inventory",
    "lp_portfolio_supervision",
    "lp_close_and_recovery",
}
OLD_ROUTINES = {
    "orca_pool_scan",
    "clmm_position_plan",
    "gateway_swap",
    "lp_portfolio_limits",
    "solana_transaction_reconcile",
    "lp_create_guard",
}
OLD_SKILLS = {
    "hummingbot_mcp_operations",
    "hummingbot_api_contracts",
    "gateway_dex_operations",
    "orca_venue_intelligence",
}


def _objects():
    agent = AgentStore().get("lp_expert")
    strategy = StrategyStore().get("lp_expert", "orca")
    assert agent is not None
    assert strategy is not None
    return agent, strategy


def _prompt(mode: str) -> str:
    agent, strategy = _objects()
    return build_tick_prompt(
        agent,
        strategy,
        {**strategy.default_config, "execution_mode": mode},
        {},
        "",
        "",
        "",
        {},
        agent_id=("lp_expert.orca_1" if mode == "loop" else "lp_expert.orca_e1"),
    )


def test_dynamic_discovery_has_exactly_three_public_routines():
    discovered = discover_routines_from_path(
        ROOT / "routines", agent_slug="lp_expert", force_reload=True
    )
    assert set(discovered) == PUBLIC_ROUTINES
    assert all(info.config_class.model_json_schema() for info in discovered.values())
    assert all(callable(info.run_fn) for info in discovered.values())


def test_public_routine_schemas_keep_agent_inputs_small_and_explicit():
    discovered = discover_routines_from_path(
        ROOT / "routines", agent_slug="lp_expert", force_reload=True
    )
    schemas = {
        name: info.config_class.model_json_schema() for name, info in discovered.items()
    }
    assert set(schemas["lp_snapshot"]["properties"]) == {
        "controller_id",
        "tick",
        "prior_closes",
    }
    assert set(schemas["lp_create"]["properties"]) == {
        "controller_id",
        "tick",
        "operation_id",
        "candidate",
        "amount_quote",
        "range_half_width_pct",
        "preparation_operation_id",
    }
    swap_fields = set(schemas["lp_swap"]["properties"])
    assert {"candidate", "amount_quote", "range_half_width_pct"} <= swap_fields
    assert {"slippage_pct", "plan", "plan_digest"} & swap_fields == set()


def test_old_public_routine_modules_and_names_are_absent():
    public_files = {
        path.stem
        for path in (ROOT / "routines").glob("*.py")
        if not path.name.startswith("_")
    }
    assert public_files == PUBLIC_ROUTINES
    for path in (
        ROOT / "AGENT.md",
        ROOT / "strategies" / "orca" / "strategy.md",
    ):
        content = path.read_text()
        assert not OLD_ROUTINES & set(content.replace("`", " ").split())


def test_agent_action_policy_is_narrow_and_explicit():
    agent, _ = _objects()
    assert set(agent.tools) == {
        "manage_routines",
        "manage_skill",
        "manage_executors",
        "trading_agent_journal_write",
    }
    text = (ROOT / "AGENT.md").read_text()
    compact = " ".join(text.split())
    assert "`run` only `lp_snapshot`, `lp_swap`, or `lp_create`" in text
    assert "exact current-controller `search` and exact `stop` only" in text
    assert "Never create, list schemas" in compact
    assert "Never call `consult`" in text
    assert "Never call it in dry-run or run-once mode" in text


def test_skill_store_and_filesystem_have_exactly_four_relevant_skills():
    directories = {path.parent.name for path in (ROOT / "skills").glob("*/SKILL.md")}
    assert directories == SKILLS
    assert not (directories & OLD_SKILLS)
    stored = {item["name"] for item in SkillStore("lp_expert").search("", limit=20)}
    assert stored >= SKILLS
    assert not stored & OLD_SKILLS


@pytest.mark.parametrize(
    ("name", "required_terms"),
    [
        ("lp_pool_review", ("candidate", "pool", "evidence")),
        ("lp_range_and_inventory", ("range", "inventory", "attribution")),
        ("lp_portfolio_supervision", ("executor", "capacity", "lifecycle")),
        ("lp_close_and_recovery", ("close", "cleanup", "following tick")),
    ],
)
def test_each_skill_is_routed_to_its_current_critical_path(name, required_terms):
    content = (ROOT / "skills" / name / "SKILL.md").read_text().casefold()
    assert all(term in content for term in required_terms)
    strategy = (ROOT / "strategies" / "orca" / "strategy.md").read_text()
    assert f"`{name}`" in strategy


def test_ordinary_complete_snapshot_path_reads_no_skill():
    strategy = (ROOT / "strategies" / "orca" / "strategy.md").read_text()
    assert "ordinary complete-snapshot paths are self-contained" in strategy
    assert "Read at most the one relevant playbook" in strategy


def test_strategy_and_example_ship_configurable_capacity_defaults():
    _, strategy = _objects()
    engine = load_full_config(strategy.dir, strategy.default_config)
    example = yaml.safe_load(
        (ROOT / "strategies" / "orca" / "config.example.yml").read_text()
    )
    for config in (strategy.default_config, engine, example):
        assert config["max_open_executors"] == 3
        assert config["max_slot_deployments_per_tick"] == 1
        assert config["candidate_scan_limit"] == 3
        assert config["risk_limits"]["max_open_executors"] == 3


def test_prompt_has_only_three_routine_paths_and_next_tick_cleanup():
    prompt = _prompt("loop")
    compact = " ".join(prompt.split())
    assert all(name in prompt for name in PUBLIC_ROUTINES)
    assert not any(name in prompt for name in OLD_ROUTINES)
    assert "The close tick never submits residual cleanup" in prompt
    assert "first following tick" in prompt
    assert "material attributable residual" in prompt
    assert "Never sell the wallet's total base balance" in compact
    assert "available_deployments_this_tick" in prompt
    assert "Start the next selection only after the prior create is confirmed" in prompt
    assert "Treat shipped capacity, deployment, and scan values as defaults" in prompt
    assert "candidate_limit" in prompt
    assert "do not send" in prompt.casefold()


def test_dry_run_prompt_is_observation_only():
    prompt = _prompt("dry_run")
    assert "🧪 DRY RUN mode" in prompt
    assert "This is OBSERVATION ONLY" in prompt
    assert "Never mutate or journal" in prompt
    assert "Do NOT call trading_agent_journal_write" in prompt


def test_run_once_prompt_is_live_without_journal_or_following_tick():
    prompt = _prompt("run_once")
    compact = " ".join(prompt.split())
    assert "[EXECUTION MODE — RUN ONCE]" in prompt
    assert "LIVE execution" in prompt
    assert "Do NOT call trading_agent_journal_write" in prompt
    assert "no following Agent tick" in prompt
    assert "manual post-close verification" in compact


def test_loop_prompt_has_one_journal_and_later_tick_recovery():
    prompt = _prompt("loop")
    assert "🧪 DRY RUN mode" not in prompt.splitlines()[0]
    assert "[EXECUTION MODE — RUN ONCE]" not in prompt.splitlines()[0]
    assert "Write ONE action entry per tick" in prompt
    assert "Following-Tick Residual Verification" in prompt
    assert "first tick after a stop prioritizes" in prompt
