import re
from pathlib import Path
from typing import get_args

import yaml

from agents.lp_agent_lite.routines import (
    calculate_lp_requirements,
    inspect_orca_positions,
    register_gateway_token,
    scan_orca_pools,
    snapshot_lp_metrics,
)
from condor.agents.agent import AgentStore
from condor.agents.prompts import build_tick_prompt
from condor.agents.strategy import StrategyStore
from condor.memory.skills import SkillStore

AGENT_ROOT = Path(__file__).resolve().parents[1]
STRATEGY_PATH = AGENT_ROOT / "strategies" / "orca" / "strategy.md"
EXAMPLE_CONFIG_PATH = AGENT_ROOT / "strategies" / "orca" / "config.example.yml"
LOCAL_CONFIG_PATH = AGENT_ROOT / "strategies" / "orca" / "config.yml"
LEARNINGS_PATH = AGENT_ROOT / "strategies" / "orca" / "learnings.md"
AGENT_PATH = AGENT_ROOT / "AGENT.md"
OPERATIONS_SKILL_PATH = AGENT_ROOT / "skills" / "orca_lp_operations" / "SKILL.md"
SELECTION_SKILL_PATH = AGENT_ROOT / "skills" / "orca_pool_selection" / "SKILL.md"
CLEANUP_SKILL_PATH = AGENT_ROOT / "skills" / "solana_inventory_cleanup" / "SKILL.md"
ACTIVE_POLICY_PATHS = (
    AGENT_PATH,
    STRATEGY_PATH,
    OPERATIONS_SKILL_PATH,
    SELECTION_SKILL_PATH,
    CLEANUP_SKILL_PATH,
)


def _prose(path: Path) -> str:
    return " ".join(path.read_text().split())


def _frontmatter(path: Path) -> dict:
    return yaml.safe_load(path.read_text().split("---", 2)[1])


def _strategy_frontmatter() -> dict:
    return _frontmatter(STRATEGY_PATH)


def _guided_config(path: Path, marker: str) -> dict[str, str]:
    text = path.read_text()
    start = f"<!-- routine-config:{marker} -->"
    assert text.count(start) == 1
    block = text.split(start, 1)[1].split("<!-- /routine-config -->", 1)[0]
    rows = re.findall(
        r"^\| `([^`]+)` \| (required|optional) \|", block, flags=re.MULTILINE
    )
    assert rows
    assert len(rows) == len({name for name, _ in rows})
    return dict(rows)


def _guided_descriptions(path: Path, marker: str) -> dict[str, str]:
    text = path.read_text()
    start = f"<!-- routine-config:{marker} -->"
    block = text.split(start, 1)[1].split("<!-- /routine-config -->", 1)[0]
    return dict(
        re.findall(
            r"^\| `([^`]+)` \| (?:required|optional) \| (.*?) \|$",
            block,
            flags=re.MULTILINE,
        )
    )


def _model_config(model) -> dict[str, str]:
    return {
        name: "required" if field.is_required() else "optional"
        for name, field in model.model_fields.items()
    }


def test_skill_routine_guides_match_executable_config_models():
    guides = {
        "scan_orca_pools": (SELECTION_SKILL_PATH, scan_orca_pools.Config),
        "scan_orca_pools.mcda_weights": (
            SELECTION_SKILL_PATH,
            scan_orca_pools.McdaWeights,
        ),
        "calculate_lp_requirements": (
            OPERATIONS_SKILL_PATH,
            calculate_lp_requirements.Config,
        ),
        "snapshot_lp_metrics": (
            OPERATIONS_SKILL_PATH,
            snapshot_lp_metrics.Config,
        ),
        "snapshot_lp_metrics.positions": (
            OPERATIONS_SKILL_PATH,
            snapshot_lp_metrics.PositionMetric,
        ),
        "snapshot_lp_metrics.residuals": (
            OPERATIONS_SKILL_PATH,
            snapshot_lp_metrics.ResidualMetric,
        ),
        "snapshot_lp_metrics.last": (
            OPERATIONS_SKILL_PATH,
            snapshot_lp_metrics.LastMutation,
        ),
        "inspect_orca_positions": (
            OPERATIONS_SKILL_PATH,
            inspect_orca_positions.Config,
        ),
        "register_gateway_token": (
            OPERATIONS_SKILL_PATH,
            register_gateway_token.Config,
        ),
    }
    for marker, (path, model) in guides.items():
        assert _guided_config(path, marker) == _model_config(model), marker


def test_skill_routine_tables_have_visible_markdown_titles():
    table_titles = {
        "scan_orca_pools": (SELECTION_SKILL_PATH, "### Top-Level Config Parameters"),
        "scan_orca_pools.mcda_weights": (
            SELECTION_SKILL_PATH,
            "### Nested `mcda_weights` Parameters",
        ),
        "calculate_lp_requirements": (
            OPERATIONS_SKILL_PATH,
            "#### Top-Level Config Parameters",
        ),
        "snapshot_lp_metrics": (
            OPERATIONS_SKILL_PATH,
            "#### Top-Level Config Parameters",
        ),
        "snapshot_lp_metrics.positions": (
            OPERATIONS_SKILL_PATH,
            "#### Nested `positions[]` Item Parameters",
        ),
        "snapshot_lp_metrics.residuals": (
            OPERATIONS_SKILL_PATH,
            "#### Nested `residuals[]` Item Parameters",
        ),
        "snapshot_lp_metrics.last": (
            OPERATIONS_SKILL_PATH,
            "#### Nested `last` Parameters",
        ),
        "inspect_orca_positions": (
            OPERATIONS_SKILL_PATH,
            "#### Top-Level Config Parameters",
        ),
        "register_gateway_token": (
            OPERATIONS_SKILL_PATH,
            "#### Top-Level Config Parameters",
        ),
    }
    for marker, (path, title) in table_titles.items():
        anchor = f"<!-- routine-config:{marker} -->"
        before = path.read_text().split(anchor, 1)[0].rstrip()
        assert before.endswith(title), marker


def test_skill_routine_guides_define_exact_calls_results_and_enums():
    selection = _prose(SELECTION_SKILL_PATH)
    operations = _prose(OPERATIONS_SKILL_PATH)
    assert 'name="scan_orca_pools"' in selection
    assert 'agent="lp_agent_lite"' in selection
    for name in (
        "calculate_lp_requirements",
        "snapshot_lp_metrics",
        "inspect_orca_positions",
        "register_gateway_token",
    ):
        assert f"### `{name}`" in OPERATIONS_SKILL_PATH.read_text()
    for prose in (selection, operations):
        assert "Config" in prose and "exact" in prose
        assert "inner" in prose and "`Invalid config:`" in prose

    enum_fields = (
        (
            OPERATIONS_SKILL_PATH,
            "snapshot_lp_metrics.positions",
            snapshot_lp_metrics.PositionMetric,
            "state",
        ),
        (
            OPERATIONS_SKILL_PATH,
            "snapshot_lp_metrics.residuals",
            snapshot_lp_metrics.ResidualMetric,
            "status",
        ),
        (
            OPERATIONS_SKILL_PATH,
            "snapshot_lp_metrics.last",
            snapshot_lp_metrics.LastMutation,
            "kind",
        ),
        (
            OPERATIONS_SKILL_PATH,
            "snapshot_lp_metrics.last",
            snapshot_lp_metrics.LastMutation,
            "status",
        ),
        (
            OPERATIONS_SKILL_PATH,
            "register_gateway_token",
            register_gateway_token.Config,
            "network",
        ),
    )
    for path, marker, model, field_name in enum_fields:
        description = _guided_descriptions(path, marker)[field_name]
        for literal in get_args(model.model_fields[field_name].annotation):
            assert f"`{literal}`" in description, (marker, field_name, literal)


def test_runtime_config_defaults_are_aligned_and_not_runtime_fallbacks():
    defaults = _strategy_frontmatter()["default_config"]
    example = yaml.safe_load(EXAMPLE_CONFIG_PATH.read_text())
    local = yaml.safe_load(LOCAL_CONFIG_PATH.read_text())
    assert {key: example[key] for key in defaults} == defaults
    assert set(defaults) <= set(local)
    assert set(local) - set(defaults) <= {"server_name", "trading_context"}
    assert "max_active_lp_positions" not in defaults
    assert (
        0
        < defaults["target_active_lp_positions"]
        <= defaults["risk_limits"]["max_open_executors"]
    )
    assert (
        0
        <= defaults["lp_open_balance_buffer_pct"]
        <= defaults["capital_headroom_pct"]
        < 100
    )
    assert (
        0
        < defaults["max_amount_quote_per_lp_position"]
        <= defaults["total_amount_quote"]
        <= defaults["risk_limits"]["max_position_size_quote"]
    )
    assert (
        0
        < defaults["position_time_limit_minutes"]
        < defaults["session_time_limit_minutes"]
    )
    for key in ("max_drawdown_pct", "shutdown_drawdown_pct"):
        assert defaults["risk_limits"][key] == -1 or defaults["risk_limits"][key] >= 0
    assert sum(defaults["mcda_weights"].values()) == 1
    prose = _prose(STRATEGY_PATH)
    for phrase in (
        "[CURRENT CONFIG]",
        "never read a strategy-root config",
        "Reject unknown, non-finite, malformed, unsupported, or contradictory",
        "never invent a key, value, synonym, default",
    ):
        assert phrase.casefold() in (_prose(AGENT_PATH) + " " + prose).casefold()


def test_always_injected_contract_is_compact_and_keeps_guardian_close_authority():
    agent = AGENT_PATH.read_text()
    strategy = STRATEGY_PATH.read_text()
    assert len(agent.encode()) + len(strategy.encode()) <= 32_000
    assert "## Live Mutation Authority" in strategy
    assert strategy.rstrip().endswith("without the failed-close recovery contract.")
    tail = strategy[strategy.index("## Live Mutation Authority") :]
    for text in (
        'manage_executors(action="create")',
        'manage_executors(action="stop"',
        "keep_position=false",
        "age_minutes >= position_time_limit_minutes",
        "standalone",
    ):
        assert text in tail
    assert "position_time_limit_minutes: 30" not in strategy


def test_runtime_stores_build_live_prompt_with_close_authority_before_current_config():
    agent = AgentStore().get("lp_agent_lite")
    strategy = StrategyStore().get_by_key("lp_agent_lite.orca")
    assert agent is not None and strategy is not None
    config = yaml.safe_load(LOCAL_CONFIG_PATH.read_text())
    config["execution_mode"] = "loop"
    prompt = build_tick_prompt(
        agent,
        strategy,
        config,
        core_data={},
        learnings="",
        summary="",
        recent_decisions="",
        risk_state={},
        tick_number=1,
        agent_id="lp_agent_lite.orca_1",
        cached_routines_section="",
        skills_index=SkillStore("lp_agent_lite").list_index(),
    )
    authority = prompt.index("## Live Mutation Authority")
    skill_index = prompt.rindex("[AVAILABLE SKILLS & ROUTINES]")
    current_config = prompt.rindex("[CURRENT CONFIG]")
    assert authority < skill_index < current_config
    assert "position_time_limit_minutes: 30" in prompt[current_config:]
    assert "Mere quarantine presence" in prompt


def test_normal_routine_parameters_remain_always_loaded_and_forbid_guessing():
    strategy = STRATEGY_PATH.read_text()
    models = (
        scan_orca_pools.Config,
        scan_orca_pools.McdaWeights,
        calculate_lp_requirements.Config,
        snapshot_lp_metrics.Config,
        snapshot_lp_metrics.PositionMetric,
        snapshot_lp_metrics.ResidualMetric,
        snapshot_lp_metrics.LastMutation,
        register_gateway_token.Config,
        inspect_orca_positions.Config,
    )
    for model in models:
        for field_name in model.model_fields:
            assert f"`{field_name}`" in strategy, (model.__name__, field_name)
    prose = _prose(AGENT_PATH) + " " + _prose(STRATEGY_PATH)
    assert "Pydantic Config models forbid unknown fields" in prose
    assert "never infer a parameter" in prose
    assert "Never list/describe routines" in prose


def test_skill_index_and_triggers_are_exact_and_demand_driven():
    declared = _strategy_frontmatter()["skills"]
    local_dirs = sorted(
        path.parent.name for path in AGENT_ROOT.glob("skills/*/SKILL.md")
    )
    assert (
        sorted(declared)
        == local_dirs
        == [
            "orca_lp_operations",
            "orca_pool_selection",
            "solana_inventory_cleanup",
        ]
    )

    index = SkillStore("lp_agent_lite").list_index()
    for path in (OPERATIONS_SKILL_PATH, SELECTION_SKILL_PATH, CLEANUP_SKILL_PATH):
        meta = _frontmatter(path)
        assert f"[{meta['name']}] {meta['when_to_use']}" in index

    agent = _prose(AGENT_PATH)
    operations = _prose(OPERATIONS_SKILL_PATH)
    selection = _prose(SELECTION_SKILL_PATH)
    cleanup = _prose(CLEANUP_SKILL_PATH)
    assert "Mere quarantine presence" in agent and operations
    assert "this tick actively" in operations
    assert "clear eligible winner" in selection
    assert "actively starts or reconciles" in cleanup
    assert "Load `solana_inventory_cleanup` once only" in agent
    assert "Never load more than one skill in a tick" in agent
    assert "ordinary deployment" in agent


def test_finalized_non_native_selection_must_register_in_the_same_tick():
    agent = _prose(AGENT_PATH)
    strategy = _prose(STRATEGY_PATH)
    selection = _prose(SELECTION_SKILL_PATH)
    for text in (
        "SELECT -> REGISTER",
        "Every finalized loop-mode non-SOL/non-QUOTE selection must fold directly into registration",
        "Difficult judgment, degraded-but-admissible evidence",
        "loading `orca_pool_selection`",
        "registration the tick's only external mutation",
        "add plus exact registry read-back is the entire verification",
        "Never size, prepare, open, preview",
        "do not finalize or commit selection",
        "Wrapped SOL or QUOTE needs no registration",
        "REGISTER -> PREPARE",
    ):
        assert text in agent + " " + strategy
    assert "Do not first check registry presence" in strategy
    assert "registers in the same tick" in selection
    assert "registration is the same tick's sole external mutation" in selection
    active_policy = "\n".join(path.read_text() for path in ACTIVE_POLICY_PATHS)
    for stale in (
        "Fresh selection never mutates",
        "Fresh selection commits and ends without mutation",
        "Difficult/degraded selection always defers registration",
        "Select And Optionally Register",
    ):
        assert stale not in active_policy


def test_lp_open_uses_numeric_range_side_everywhere():
    active_policy = "\n".join(path.read_text() for path in ACTIVE_POLICY_PATHS)
    assert "numeric `side=3`" in STRATEGY_PATH.read_text()
    assert "`side=3`" in OPERATIONS_SKILL_PATH.read_text()
    assert 'side="RANGE"' not in active_policy


def test_confirmed_registration_is_trusted_until_an_exact_gateway_error():
    agent = _prose(AGENT_PATH)
    strategy = _prose(STRATEGY_PATH)
    operations = _prose(OPERATIONS_SKILL_PATH)
    for text in (
        "Confirmed registration remains authoritative",
        "Go directly to `SIZE`",
        "do not preview, check registry presence, verify, or register again",
        "Only a later exact Gateway error",
        "proving missing or conflicting registry/token metadata",
        "Proven missing registration permits one registration-only recovery",
        "exact metadata conflict blocks only the chain",
        "An uncertain original add permits one later read-only `preview=true` reconciliation",
        "only normal standalone `REGISTER` ticks",
    ):
        assert text in strategy
    assert "Finalized loop-mode non-SOL/non-QUOTE selection always folds" in agent
    assert "Trust it for the unchanged chain" in operations
    assert "Registration is a separate loop transition" not in operations


def test_phase_budget_and_committed_chain_remain_complete():
    prose = _prose(AGENT_PATH) + " " + _prose(STRATEGY_PATH)
    for text in (
        "at most two adjacent lifecycle phases",
        "at most one external mutation phase",
        "PREPARE -> OPEN",
        "CLOSE -> CLEANUP",
        "committed deployment chain",
        "pool, pair, BASE symbol/mint/decimals",
        "resumes any committed lifecycle",
        "max_lp_deployments_per_tick",
    ):
        assert text in prose


def test_late_deployment_requires_a_full_position_lifetime_remaining():
    strategy = _prose(STRATEGY_PATH)
    for text in (
        "Before `SELECT`, `REGISTER`, `PREPARE`, or `OPEN`",
        "remaining_session_minutes = session_time_limit_minutes - session.age_min",
        "strictly greater than `position_time_limit_minutes`",
        "equality, a shorter remainder, or an unavailable clock prohibits new risk",
        "clean any confirmed prepared inventory",
    ):
        assert text in strategy


def test_exact_identity_pool_and_risk_contracts_survive_compaction():
    prose = _prose(AGENT_PATH) + " " + _prose(STRATEGY_PATH)
    for text in (
        "full executor `id` is its sole lifecycle identity",
        "Eight-character prefixes are display-only",
        "embedded executor-config `controller_id`",
        "including `main`",
        "current-controller filtered search",
        "risk_limits.max_open_executors",
        "Never have two active, possibly landed, or unresolved",
        "one exact pool",
    ):
        assert text in prose


def test_native_evidence_and_executor_paths_remain_bounded():
    agent = AGENT_PATH.read_text()
    prose = _prose(AGENT_PATH) + " " + _prose(STRATEGY_PATH)
    assert "explore_geckoterminal" not in _frontmatter(AGENT_PATH)["tools"]
    assert "Never call GeckoTerminal" in prose
    assert "Never query or use HAPI LP portfolio data" in prose
    assert "Never call `manage_gateway_swaps`" in prose
    assert "market `order_executor` exclusively" in prose
    assert "standalone tool execution" in prose
    assert "Only after the stop result" in prose
    assert "- manage_gateway_swaps" not in agent


def test_prepare_open_and_cleanup_receipts_defer_reconciliation():
    strategy = _prose(STRATEGY_PATH)
    cleanup = _prose(CLEANUP_SKILL_PATH)
    assert "The create receipt is `submitted`" in strategy
    assert "end" in strategy and "Reconcile next tick" in strategy
    assert "successful receipt is `submitted` and ends the tick" in strategy
    assert "A create receipt is `submitted`" in cleanup
    assert "end without post-create reads" in cleanup
    assert "PREPARE -> OPEN" in _prose(AGENT_PATH)
    assert "CLOSE -> CLEANUP" in _prose(AGENT_PATH)


def test_open_buffer_precision_and_retry_contract_remain_fail_closed():
    defaults = _strategy_frontmatter()["default_config"]
    strategy = _prose(STRATEGY_PATH)
    assert defaults["lp_open_balance_buffer_pct"] == 2
    assert "lp_open_balance_buffer_pct" in strategy
    assert "floored `base_amount`/`quote_amount`" in strategy
    assert "terminal with no position identity" in strategy
    assert "unchanged within reported precision" in strategy
    assert "strictly smaller feasible size" in strategy
    assert "native display precision cannot represent" in strategy
    assert "Any possible position, balance change, timeout" in strategy
    assert "never retried" in strategy


def test_pnl_grace_and_configured_exit_authorize_standalone_close():
    defaults = _strategy_frontmatter()["default_config"]
    strategy = _prose(STRATEGY_PATH)
    agent = _prose(AGENT_PATH)
    assert defaults["lp_pnl_grace_period_minutes"] == 5
    assert "lp_pnl_grace_period_minutes >= 0" in strategy
    assert "position and aggregate PnL are provisional" in strategy
    assert "Time limits, operator wind-down" in strategy
    assert "fresh exact PnL evidence" in strategy
    assert "operator-authorized strategy exit" in agent
    assert "not a request for operator confirmation" in agent
    assert "Submit the stop as one standalone tool execution" in agent


def test_failed_close_quarantine_is_scoped_and_skill_is_active_only():
    prose = _prose(AGENT_PATH) + " " + _prose(STRATEGY_PATH)
    for text in (
        "Continue independently proven siblings",
        "close-only `inspect_orca_positions`",
        "at most one corrected later stop",
        "A second failure or `404`",
        "exact executor, position, pool, and attributable capital",
        "exact on-chain position closed",
        "refreshed wallet can pass ordinary new-LP sizing",
        "cleanup cannot keep quarantine active forever",
    ):
        assert text in prose


def test_cleanup_uses_precision_safe_balance_and_bounded_sol_excess():
    strategy = _prose(STRATEGY_PATH)
    cleanup = _prose(CLEANUP_SKILL_PATH)
    assert "amount=<entire available BASE>" not in strategy
    for text in (
        "Never request the full rounded display balance",
        "safe_raw = max(raw_balance - 1, 0)",
        "safe_amount = max(display_balance - quantum, 0)",
        "value that safe amount in QUOTE",
        "not strictly above `residual_base_dust_quote`",
        "displayed `512.0476` becomes `512.0475`",
        "displayed `9.5398` becomes `9.5397`",
        "side=2",
        'execution_strategy="MARKET"',
        "protected_sol = min_sol_reserve * (1 + capital_headroom_pct / 100)",
        "excess_sol = max(fresh_sol_balance - protected_sol, 0)",
        "sol_to_sell = min(excess_sol, quote_shortfall / sol_price_quote)",
        "Never call `manage_gateway_swaps`",
    ):
        assert text in cleanup
    assert "load `solana_inventory_cleanup` exactly once" in strategy.casefold()


def test_cleanup_and_stop_guidance_has_no_stale_full_balance_or_hard_stall():
    active_policy = "\n".join(path.read_text() for path in ACTIVE_POLICY_PATHS)
    for stale in (
        "sell the entire refreshed balance",
        "every affected inventory chain is quote-clean",
        "Read the live executor schema before cleanup create",
    ):
        assert stale not in active_policy
    operations = _prose(OPERATIONS_SKILL_PATH)
    assert "`solana_inventory_cleanup`" in operations
    assert "`STOP_MANUAL_RECOVERY`" in operations


def test_cleanup_retry_quarantine_and_manual_handoff_are_bounded():
    cleanup = _prose(CLEANUP_SKILL_PATH)
    strategy = _prose(STRATEGY_PATH)
    for text in (
        "Jupiter `6024`/`0x1788`",
        "zero execution or transaction effect",
        "refreshed exact-mint wallet balance unchanged",
        "strictly smaller than the prior request",
        "Never repeat an identical amount",
        "One corrected cleanup is the maximum",
        "quarantines only that exact residual mint and amount",
        "does not block healthy LP supervision/exits",
        "Never blacklist a token or pool because cleanup sizing, precision",
        "STOP_MANUAL_RECOVERY",
        "manual-recovery handoff, not successful cleanup",
    ):
        assert text in cleanup
    assert "stop the Agent instead of repeating `HOLD` forever" in strategy
    assert "must not claim the wallet is quote-clean" in strategy


def test_cleanup_request_is_exact_executor_only_and_metrics_use_mints():
    cleanup = _prose(CLEANUP_SKILL_PATH)
    strategy = _prose(STRATEGY_PATH)
    for text in (
        'manage_executors(action="create", executor_type="order_executor")',
        "exact current `controller_id`",
        "`account_name=<config.account_name>`",
        "`connector_name=<config.network>`",
        "exact canonical `trading_pair`",
        "Do not add provider, slippage, quote, pool, mint, or invented fields",
        "request a Gateway quote",
        "register the token",
    ):
        assert text in cleanup
    assert "exact on-chain Solana mint address, never its symbol" in strategy
    assert "A submitted cleanup ends its tick" in strategy


def test_receive_difference_blacklist_uses_wallet_precision_not_executor_fill():
    defaults = _strategy_frontmatter()["default_config"]
    strategy = _prose(STRATEGY_PATH)
    operations = _prose(OPERATIONS_SKILL_PATH)
    selection = _prose(SELECTION_SKILL_PATH)
    assert defaults["preparation_receive_difference_blacklist_pct"] == 5
    for text in (
        "observed_delta = post_value - pre_value",
        "error_bound = pre_quantum + post_quantum",
        "minimum_difference_pct",
        "executed_amount_base` is the requested BASE amount",
        "Small differences only resize the same LP",
        "BLACKLIST_POOL=<pool> BLACKLIST_TOKEN=<base_mint>",
    ):
        assert text in strategy
    assert "strictly above `preparation_receive_difference_blacklist_pct`" in operations
    assert (
        "Equality, ambiguity, or insufficient precision never blacklists" in operations
    )
    assert "Exclude the exact pool" in selection
    assert "## Execution Notes" in LEARNINGS_PATH.read_text()


def test_mutation_outcomes_journal_and_mode_contract_remain_exact():
    prose = _prose(AGENT_PATH) + " " + _prose(STRATEGY_PATH)
    for outcome in (
        "rejected_before_submit",
        "submitted",
        "confirmed",
        "uncertain",
        "ambiguous",
        "unavailable",
    ):
        assert f"`{outcome}`" in prose
    for text in (
        "Intent is not submission proof",
        "written=true",
        "exact `agent_id`",
        "exact `tick`",
        "OBSERVATION ONLY",
        "[EXECUTION MODE — RUN ONCE]",
        "An `_eN` suffix does not distinguish",
        "Conflicting mode evidence requires observation-only `HOLD`",
    ):
        assert text in prose
