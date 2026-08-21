import re
from pathlib import Path

import yaml

from agents.multi_lp_rebalancer_manager.routines import (
    allocate_controller_capital,
    register_gateway_token,
    scan_orca_pools,
    snapshot_trend_aware_lp_bots,
)
from condor.agents.agent import AgentStore
from condor.agents.prompts import build_tick_prompt
from condor.agents.strategy import StrategyStore
from condor.memory.skills import SkillStore

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "AGENT.md"
STRATEGY = ROOT / "strategies" / "orca" / "strategy.md"
EXAMPLE = ROOT / "strategies" / "orca" / "config.example.yml"
LEARNINGS = ROOT / "strategies" / "orca" / "learnings.md"
SELECTION = ROOT / "skills" / "orca_pool_selection" / "SKILL.md"
OPERATIONS = ROOT / "skills" / "multi_lp_controller_operations" / "SKILL.md"
LIFECYCLE = ROOT / "skills" / "bot_lifecycle_reconciliation" / "SKILL.md"


def _frontmatter(path: Path) -> dict:
    return yaml.safe_load(path.read_text().split("---", 2)[1])


def _prose(path: Path) -> str:
    return " ".join(path.read_text().split())


def _guide(path: Path, marker: str) -> dict[str, str]:
    text = path.read_text()
    anchor = f"<!-- routine-config:{marker} -->"
    block = text.split(anchor, 1)[1].split("<!-- /routine-config -->", 1)[0]
    return dict(
        re.findall(r"^\| `([^`]+)` \| (required|optional) \|", block, re.MULTILINE)
    )


def _model(model) -> dict[str, str]:
    return {
        key: "required" if field.is_required() else "optional"
        for key, field in model.model_fields.items()
    }


def _prompt(mode: str) -> str:
    agent = AgentStore().get("multi_lp_rebalancer_manager")
    strategy = StrategyStore().get_by_key("multi_lp_rebalancer_manager.orca")
    assert agent and strategy
    config = dict(strategy.default_config)
    config["execution_mode"] = mode
    risk_limits = config["risk_limits"]
    suffix = "e1" if mode in {"dry_run", "run_once"} else "1"
    return build_tick_prompt(
        agent,
        strategy,
        config,
        core_data={},
        learnings="",
        summary="",
        recent_decisions="",
        risk_state={
            "total_exposure": 0,
            "max_position_size": risk_limits["max_position_size_quote"],
            "executor_count": 0,
            "max_open_executors": risk_limits["max_open_executors"],
            "max_drawdown_pct": risk_limits["max_drawdown_pct"],
            "is_blocked": False,
        },
        tick_number=1,
        agent_id=f"multi_lp_rebalancer_manager.orca_{suffix}",
        cached_routines_section="",
        skills_index=SkillStore("multi_lp_rebalancer_manager").list_index(),
    )


def test_agent_strategy_and_skills_load_through_current_stores():
    agent = AgentStore().get("multi_lp_rebalancer_manager")
    strategy = StrategyStore().get_by_key("multi_lp_rebalancer_manager.orca")
    assert agent is not None and strategy is not None
    assert agent.slug == "multi_lp_rebalancer_manager"
    assert strategy.agent_slug == agent.slug
    assert sorted(strategy.skills) == [
        "bot_lifecycle_reconciliation",
        "multi_lp_controller_operations",
        "orca_pool_selection",
    ]
    index = SkillStore(agent.slug).list_index()
    assert "[bot_lifecycle_reconciliation]" in index
    assert "[orca_pool_selection]" in index
    assert "[multi_lp_controller_operations]" in index


def test_declared_routines_are_exact_and_every_reference_resolves():
    routine_files = sorted(path.stem for path in (ROOT / "routines").glob("*.py"))
    assert routine_files == [
        "allocate_controller_capital",
        "register_gateway_token",
        "scan_orca_pools",
        "snapshot_trend_aware_lp_bots",
    ]
    text = " ".join(
        path.read_text() for path in (AGENT, STRATEGY, SELECTION, OPERATIONS, LIFECYCLE)
    )
    for name in routine_files:
        assert name in text
    assert "agents.lp_agent_lite" not in text
    for path in (ROOT / "routines").glob("*.py"):
        assert "agents.lp_agent_lite" not in path.read_text()


def test_visible_routine_guides_match_config_models():
    assert _guide(SELECTION, "scan_orca_pools") == _model(scan_orca_pools.Config)
    assert _guide(SELECTION, "scan_orca_pools.mcda_weights") == _model(
        scan_orca_pools.McdaWeights
    )
    assert _guide(SELECTION, "scan_orca_pools.trend_thresholds_pct") == _model(
        scan_orca_pools.TrendThresholds
    )
    assert _guide(SELECTION, "scan_orca_pools.range_profile") == _model(
        scan_orca_pools.RangeProfile
    )
    assert _guide(OPERATIONS, "snapshot_trend_aware_lp_bots") == _model(
        snapshot_trend_aware_lp_bots.Config
    )


def test_defaults_and_example_are_aligned_and_learnings_are_empty():
    defaults = _frontmatter(STRATEGY)["default_config"]
    example = yaml.safe_load(EXAMPLE.read_text())
    assert {key: example[key] for key in defaults} == defaults
    assert set(example) - set(defaults) == {"server_name", "trading_context"}
    assert defaults["bot_mode"] == "bot"
    assert (
        defaults["target_active_controllers"] == defaults["max_active_controllers"] == 3
    )
    assert (
        defaults["total_amount_quote"]
        <= defaults["risk_limits"]["max_position_size_quote"]
    )
    assert (
        0
        < defaults["deployment_signal_min_remaining_seconds"]
        < defaults["trend_signal_max_age_seconds"]
    )
    assert defaults["lp_sizing_buffer_pct"] == 2
    assert (
        "`0 < deployment_signal_min_remaining_seconds < trend_signal_max_age_seconds`"
        in _prose(STRATEGY)
    )
    assert sum(defaults["mcda_weights"].values()) == 1
    assert sum(defaults["allocation_safety_weights"].values()) == 1
    assert LEARNINGS.read_text().strip() == ""


def test_native_tool_policy_excludes_direct_execution_and_gateway_mutation():
    tools = _frontmatter(AGENT)["tools"]
    assert "manage_executors" not in tools
    assert "manage_gateway_swaps" not in tools
    assert "place_order" not in tools
    assert "manage_memory" not in tools
    assert "search_history" not in tools
    prose = (_prose(AGENT) + " " + _prose(STRATEGY)).casefold()
    for phrase in (
        "never call `manage_executors`",
        "never use `stop_controllers`",
        "never use `start_controllers`",
        "at most one runtime/capital mutation per tick",
        "one saved-config upsert may immediately precede one deploy",
        "no other mutation may fold",
        "no executors were created (dry run)",
    ):
        assert phrase.casefold() in prose
    assert "exit_requested=true" in prose
    assert 'manage_bots(action="stop_bot")' in prose


def test_balance_scope_and_bot_emergency_cap_match_platform_contract():
    agent = _prose(AGENT)
    strategy = _prose(STRATEGY)
    assert "`connector_names=[network]`" in agent
    assert "Use `solana-mainnet-beta`, not `orca`" in agent
    assert "allocation-sized `max_global_drawdown_quote`" in strategy
    assert "`max_controller_drawdown_quote`" in strategy
    assert "Omit `image`" in strategy
    assert "hummingbot_image" not in _frontmatter(STRATEGY)["default_config"]
    assert "hummingbot_image" not in EXAMPLE.read_text()


def test_controller_creation_uses_one_canonical_execution_symbol():
    agent = _prose(AGENT)
    strategy = _prose(STRATEGY)
    assert "same uppercase execution symbol" in agent
    for phrase in (
        "canonical `execution_symbol=base_symbol.upper()`",
        "scanner/display `cbBTC` registers as `CBBTC` and configures `CBBTC-USDC`",
        '`trading_pair == base_symbol.upper() + "-" + quote_token_symbol.upper()`',
        "confirmed registration symbol to equal that uppercase base component",
    ):
        assert phrase in strategy
    assert "never use `cbBTC-USDC`" in strategy


def test_saved_config_reuse_and_corrected_retry_cannot_stall_creation():
    strategy = _prose(STRATEGY)
    for phrase in (
        "`confirm_override=true`",
        "Slot config names are intentionally stable",
        "is a materially corrected attempt",
        "A saved-config 404 after such rejection confirms that no config was created",
        "must not turn the slot into indefinite `HOLD`",
    ):
        assert phrase.casefold() in strategy.casefold()


def test_allocation_slots_and_exit_ownership_match_controller_contract():
    strategy = _prose(STRATEGY)
    for phrase in (
        "Use integer slots `1`–`3`",
        'never `"slot-1"`',
        "The controller alone evaluates active-LP PnL grace",
        "Never recompute or independently trigger them",
        "mirror that same reason once",
    ):
        assert phrase in strategy


def test_exit_update_and_archive_contract_are_explicit():
    prose = (_prose(STRATEGY) + " " + _prose(OPERATIONS)).casefold()
    for phrase in (
        "exact timestamped instance",
        "exact config file/id",
        "complete current config",
        "confirm_override=true",
        "raw `custom_info.exit` proves runtime application",
        "archive only an exact `EXITED` controller",
        "terminal/non-active status, not a null envelope",
        "manual kill switch terminates the control loop before cleanup",
        "archive_check_bots",
        "archive_confirmed",
    ):
        assert phrase.casefold() in prose


def test_staged_deploy_duplicate_exclusion_and_unknown_trend_block_are_explicit():
    prose = _prose(AGENT) + " " + _prose(STRATEGY)
    for phrase in (
        "wait for authoritative `ACTIVE` before another slot",
        "distinct pools and base mints",
        "`UNKNOWN` trend or missing deterministic range is never deployable or rearmable",
        "zero or more than one expected controller",
        "more than `deployment_signal_min_remaining_seconds`",
        "fresh wallet USDC observation minus",
        "compact ranked candidate table",
    ):
        assert phrase in prose


def test_prompt_mode_markers_and_run_once_policy():
    dry = _prompt("dry_run")
    once = _prompt("run_once")
    loop = _prompt("loop")
    assert dry.startswith(
        "You are an autonomous trading agent running inside Condor in 🧪 DRY RUN mode"
    )
    assert dry.count("[EXECUTION MODE — RUN ONCE]") == 1
    assert once.count("[EXECUTION MODE — RUN ONCE]") == 2
    assert once.count("Single-tick session with LIVE execution") == 1
    assert loop.count("[EXECUTION MODE — RUN ONCE]") == 1
    assert loop.count("Single-tick session with LIVE execution") == 0
    for prompt in (dry, once, loop):
        current = prompt[prompt.rindex("[CURRENT CONFIG]") :]
        assert "execution_mode:" not in current
        assert "frequency_sec:" not in current
        assert "deployment_signal_min_remaining_seconds: 200" in current
    assert "observation/proposal only" in once
    assert "observation/proposal only" in dry


def test_session_two_empty_namespace_and_risk_admission_contract():
    prompt = _prompt("loop")
    current_config = prompt[prompt.rindex("[CURRENT CONFIG]") :].split(
        "[CONTROLLER MODE]", 1
    )[0]
    risk_state = prompt[prompt.rindex("[RISK STATE]") :].split("[CURRENT STATUS]", 1)[0]
    strategy = _prose(STRATEGY)
    agent = _prose(AGENT)
    operations = _prose(OPERATIONS)

    assert "risk_limits" not in current_config
    assert "Position Size: $0.00 / $30.00 limit" in risk_state
    assert "Open Executors: 0 / 12 limit" in risk_state
    for phrase in (
        "`[RISK STATE]` owns framework risk",
        "Complete zero proves `VACANT`",
    ):
        assert phrase.casefold() in agent.casefold()
    for phrase in (
        "omit `expected_bots` on the ordinary discovery snapshot",
        "complete ordinary snapshot with `owned_bot_count=0` and no rows",
        "never pass requested slot bases, vacant slots, or a partial fleet",
    ):
        assert phrase.casefold() in strategy.casefold()
    assert "all three slots are `vacant`" not in operations.casefold()
    assert "previously verified live exact bot" in strategy.casefold()
    assert (
        "a blocked risk state still permits read-only supervision"
        in strategy.casefold()
    )


def test_lifecycle_skill_resolves_stale_and_transitional_bot_evidence():
    skill = _prose(LIFECYCLE)
    agent = _prose(AGENT)
    for phrase in (
        "Historical PnL outside that live instance",
        "do not use injected executor rows/counts to reserve a slot",
        "Any injected adopted bot or active executor is stale historical context",
        "Do not quarantine it or repeat reconciliation",
        "A bot previously proved live in this session disappears",
        "Immediate absence is not rejection or terminal no-effect",
        "Active absence and the background receipt are not archive proof",
        "appears in snapshot `archive_confirmed`",
        "INVALID_SLOT_IDENTITY",
        "Preserve full names; do not choose the newest by timestamp",
        "YAML success is not controller application",
        "Treat `idle` as stale MQTT evidence",
        "do not spend another tick proving the same absence",
        "INHERITED_WIND_DOWN",
        "Never resume, rearm, retune, or include it in the new portfolio",
        "Current-session provenance requires a complete deployment intent/result",
        'manage_controllers(action="describe"',
        "Exact full-field match proves saved config",
    ):
        assert phrase.casefold() in skill.casefold()
    assert "load `bot_lifecycle_reconciliation`" in agent.casefold()
    assert "load more than one in a tick" in agent.casefold()


def test_registration_receipt_and_planned_bot_identity_are_durable():
    agent = _prose(AGENT)
    strategy = _prose(STRATEGY)
    combined = agent + " " + strategy

    for phrase in (
        "registration is the sole post-mutation journal exception",
        "write the tick's one action journal from its actual inner JSON",
        "expected output is never a receipt",
        "a later tick may re-register only the identical token tuple once",
        "stable `planned_bot_base`",
        "absent `bot_instance`",
        "never invent it",
        "set `bot_instance` from a deploy result or the sole fresh raw bot",
    ):
        assert phrase.casefold() in combined.casefold()


def test_empty_namespace_does_not_repeat_stale_conflict_work():
    agent = _prose(AGENT)
    skill = _prose(LIFECYCLE)
    assert "run `snapshot_trend_aware_lp_bots` first and once" in agent
    assert (
        "never load a lifecycle skill from injected ownership alone" in agent.casefold()
    )
    assert (
        "do not load this skill again, repeat status, or refresh balances"
        in skill.casefold()
    )
    assert (
        "balance refresh is allowed only if funded admission is now the next action"
        in skill.casefold()
    )


def test_fresh_session_never_resumes_inherited_bots():
    agent = _prose(AGENT)
    strategy = _prose(STRATEGY)
    operations = _prose(OPERATIONS)
    combined = " ".join((agent, strategy, operations)).casefold()

    for phrase in (
        "any other live namespaced bot is inherited and termination-only",
        "never resume another session's bot",
        "inherited prior-session bot exit/archive",
        "exit_requested=true`, `exit_reason=operator",
        "block all new admission until inherited cleanup is complete",
        "startup cleanup alone is not permission to stop it",
        "empty raw status overrides stale injected adoption/performance",
        "condor adoption prevents orphans",
        "discovery authority only",
    ):
        assert phrase.casefold() in combined

    assert "native adoption preserves durable bots" not in strategy.casefold()
    assert "are adoptable" not in agent.casefold()
    assert "2 * frequency_sec" not in strategy


def test_lp_sizing_and_same_tick_terminal_fault_exit_are_explicit():
    agent = _prose(AGENT)
    strategy = _prose(STRATEGY)
    operations = _prose(OPERATIONS)
    combined = " ".join((agent, strategy, operations)).casefold()

    for phrase in (
        "2 <= lp_sizing_buffer_pct < 10",
        "98%, 96.04%, then 94.1192%",
        "preparation failures do not alter lp sizing",
        "fault_reason=consecutive_failure_limit_reached",
        "config read may fold into this same tick",
        "exit_requested=true`, `exit_reason=fault",
        "do not spend a separate observational `hold` tick",
        "any hard fault, active/uncertain executor",
    ):
        assert phrase in combined


def test_source_has_one_qualified_empty_rule_and_controller_owned_exits():
    agent = _prose(AGENT).casefold()
    strategy = _prose(STRATEGY).casefold()
    operations = _prose(OPERATIONS).casefold()
    selection = _prose(SELECTION).casefold()

    assert "unless this session deployed or lost a verified bot" in agent
    assert (
        "only when this session has no submitted deploy or previously verified live exact bot"
        in strategy
    )
    assert "proves the namespace is empty and all three slots" not in operations
    assert (
        "the controller alone triggers take-profit, stop-loss, and time-limit exits"
        in operations
    )
    assert "request an exit when net pnl" not in strategy
    assert "one suitable candidate is valid underfilled occupancy" in selection


def test_mutations_end_the_tick_and_archive_requires_bot_run_confirmation():
    prose = (
        _prose(AGENT) + " " + _prose(STRATEGY) + " " + _prose(LIFECYCLE)
    ).casefold()
    for phrase in (
        "deploy always ends the tick",
        "deploy/update/archive ends its tick",
        "exact hapi bot-run `archived` confirmation",
        "active absence and the background receipt are not archive proof",
        "framework risk `active` is additionally required for admission/rearm",
        "empty_bot_terminal_no_effect",
    ):
        assert phrase in prose


def test_deploy_name_is_exact_and_empty_bot_has_one_terminal_no_effect_path():
    agent = _prose(AGENT)
    strategy = _prose(STRATEGY)
    lifecycle = _prose(LIFECYCLE)
    combined = " ".join((agent, strategy, lifecycle)).casefold()

    for phrase in (
        "`controllers_config=[config_name]`",
        "copying both exact strings character-for-character",
        "never normalize underscores or hyphens",
        "exact namespaced slot bot, current or inherited",
        "raw topology has zero controller configs",
        "targeted `get_config` confirms none",
        "raw performance has no controller, `custom_info`, executor, or position evidence",
        "stopped status alone never suffices",
        "exact namespace/slot identity",
        "only `stop_bot` exception without controller `exited`",
        "keep its slot/budget reserved until later `archive_confirmed`",
        "never repair, restart, or redeploy the malformed bot",
    ):
        assert phrase in combined


def test_assembled_prompt_is_bounded_and_keeps_action_contract():
    prompt = _prompt("loop")
    assert len(AGENT.read_bytes()) + len(STRATEGY.read_bytes()) <= 32_000
    assert len(prompt.encode()) < 64_000
    assert "controller alone evaluates active-lp pnl grace" in prompt.casefold()
    assert "update_config" in prompt
    assert "at most one runtime/capital mutation per tick" in _prose(AGENT)
    assert "formatted bot status drops `custom_info`" in prompt


def test_bounded_deployment_phase_repairs_session_11_continuity():
    agent = _prose(AGENT)
    strategy = _prose(STRATEGY)
    lifecycle = _prose(LIFECYCLE)
    combined = " ".join((agent, strategy, lifecycle)).casefold()

    for phrase in (
        "`configure -> deploy` may fold only when no trend refresh occurred that tick",
        "full config including `controller_started_at`",
        "only exact `config created:`/`config updated:`",
        "a refresh tick may scan then upsert, but deploys next tick",
        'manage_controllers(action="describe", config_name=<exact>, include_code=false)',
        "missing/mismatch permits corrected upsert, not indefinite `hold`",
        "zero raw matches is non-retryable `deploy_pending`",
        "one exact match is current-session provenance",
        "a lost response alone never makes the exact match inherited",
    ):
        assert phrase in combined


def test_journal_and_skill_order_prevent_tick_4_regression():
    agent = _prose(AGENT).casefold()
    lifecycle_frontmatter = _frontmatter(LIFECYCLE)

    for phrase in (
        'exact `agent_id`, `entry_type="action"`, and positive current `tick`',
        "exact `read` only after the raw snapshot",
        "only for its concrete non-empty conflict",
    ):
        assert phrase in agent
    assert (
        "never read from injected ownership alone"
        in lifecycle_frontmatter["when_to_use"].casefold()
    )


def test_strategy_folder_contains_no_python_and_sources_do_not_reference_sessions():
    assert not list((ROOT / "strategies").rglob("*.py"))
    source_text = " ".join(
        path.read_text() for path in (ROOT / "routines").rglob("*.py")
    )
    assert "sessions/session_" not in source_text
    assert "search_history" not in _frontmatter(AGENT)["tools"]


def test_routine_models_reject_unknown_fields():
    models = (
        allocate_controller_capital.Config,
        register_gateway_token.Config,
        scan_orca_pools.Config,
        snapshot_trend_aware_lp_bots.Config,
    )
    assert all(model.model_config.get("extra") == "forbid" for model in models)


def test_all_routine_and_compact_row_contracts_use_safe_text_budget():
    routines = (
        allocate_controller_capital,
        register_gateway_token,
        scan_orca_pools,
        snapshot_trend_aware_lp_bots,
    )
    assert all(routine.MAX_RESULT_CHARS == 1_899 for routine in routines)

    prose = _prose(AGENT) + " " + _prose(STRATEGY)
    for phrase in (
        "valid JSON shorter than 1,900 characters",
        "when at least five eligible requested rows exist",
        "target is desired occupancy and never a minimum",
        "`format=compact_rows_v1`",
        "never guess an index",
        "`summary_rows_v1` is degraded evidence",
    ):
        assert phrase.casefold() in prose.casefold()

    assert scan_orca_pools.MIN_TRANSPORT_CANDIDATES == 5
    assert scan_orca_pools.CANDIDATE_FIELDS == (
        "rank",
        "pool_address",
        "base_symbol",
        "base_mint",
        "base_decimals",
        "tvl_usd",
        "score",
        "price_stability",
        "liquidity_depth",
        "execution_simplicity",
        "trend",
        "trend_signal_id",
        "range_side",
        "position_width_pct",
        "downside_offset_pct",
        "rebalance_threshold_pct",
    )

    assert snapshot_trend_aware_lp_bots.CONTROLLER_FIELDS == (
        "bot_name",
        "controller_id",
        "slot",
        "run_state",
        "lifecycle_state",
        "readiness_state",
        "pool_address",
        "base_token_mint",
        "assigned_quote",
        "schema_version",
        "telemetry_complete",
        "identity_matches_config",
        "domain_matches_strategy",
        "assigned_quote_matches_config",
        "config_available",
        "policy_matches_config",
        "lifecycle_coherent",
        "lp_executor",
        "order_executor",
        "trend",
        "failure",
        "exit",
        "inventory",
        "pnl",
    )
