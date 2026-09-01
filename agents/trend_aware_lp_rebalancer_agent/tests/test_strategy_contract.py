import json
import re
from decimal import Decimal
from pathlib import Path

import yaml

from condor.agents.agent import AgentStore
from condor.agents.config import load_full_config
from condor.agents.journal import JournalManager
from condor.agents.prompts import _build_routines_section, build_tick_prompt
from condor.agents.strategy import StrategyStore
from condor.memory.skills import SkillStore
from routines.base import assistant_routines, discover_routines_from_path

ROOT = Path(__file__).resolve().parents[1]
AGENT_PATH = ROOT / "AGENT.md"
STRATEGY_PATH = ROOT / "strategies" / "orca" / "strategy.md"
EXAMPLE_PATH = ROOT / "strategies" / "orca" / "config.example.yml"
CONFIG_PATH = ROOT / "strategies" / "orca" / "config.yml"
LEARNINGS_PATH = ROOT / "strategies" / "orca" / "learnings.md"
AGENT_SLUG = "trend_aware_lp_rebalancer_agent"
STRATEGY_KEY = f"{AGENT_SLUG}.orca"
BOT_NAMESPACE = f"{AGENT_SLUG}-orca-v2"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
STRATEGY_CONFIG_KEYS = {
    "server_name",
    "execution_mode",
    "frequency_sec",
    "max_ticks",
    "canvas_enabled",
    "bot_mode",
    "bot_name",
    "account_name",
    "trading_context",
    "total_amount_quote",
    "min_positions",
    "max_positions",
    "min_position_amount_quote",
    "min_sol_reserve",
    "risk_profile",
    "min_pool_tvl_usd",
    "min_fee_productivity_bps_per_day",
    "candidate_scan_limit",
    "excluded_base_mints",
    "excluded_pool_addresses",
    "take_profit_ratio",
    "stop_loss_ratio",
    "time_limit_minutes",
    "risk_limits",
}
CONTROLLER_TOP_LEVEL_KEYS = {
    "id",
    "controller_type",
    "controller_name",
    "candles_config",
    "manual_kill_switch",
    "initial_positions",
    "connector_name",
    "lp_provider",
    "swap_provider",
    "quote_token_mint",
    "total_amount_quote",
    "lp_positions",
    "lp_sizing_buffer_pct",
    "min_sol_reserve",
    "cleanup_min_quote_value",
    "rebalance_cooldown_minutes",
    "max_consecutive_controller_failures",
    "failure_retry_backoff_seconds",
    "controller_take_profit_ratio",
    "controller_stop_loss_ratio",
    "controller_time_limit_minutes",
    "controller_pnl_grace_period_minutes",
    "exit_requested",
    "exit_reason",
}
PINNED_CONTROLLER_DEFAULTS = {
    "controller_type": "generic",
    "controller_name": "trend_aware_lp_rebalancer",
    "candles_config": [],
    "manual_kill_switch": False,
    "initial_positions": [],
    "connector_name": "solana-mainnet-beta",
    "lp_provider": "orca/clmm",
    "swap_provider": "jupiter/router",
    "lp_sizing_buffer_pct": "2",
    "cleanup_min_quote_value": "0.01",
    "rebalance_cooldown_minutes": 5,
    "max_consecutive_controller_failures": 3,
    "failure_retry_backoff_seconds": 30,
    "controller_pnl_grace_period_minutes": 5,
}
FORMATION_FIELDS = {
    "market_trend",
    "position_width_pct",
    "downside_offset_pct",
    "rebalance_threshold_pct",
}


def _frontmatter(path: Path) -> dict:
    return yaml.safe_load(path.read_text().split("---", 2)[1])


def _loaded_strategy_configs() -> dict[str, dict]:
    configs = {"example": yaml.safe_load(EXAMPLE_PATH.read_text())}
    if CONFIG_PATH.exists():
        configs["runtime"] = yaml.safe_load(CONFIG_PATH.read_text())
    return configs


def _prose(path: Path) -> str:
    return " ".join(path.read_text().split())


def _pending_record() -> dict:
    generation = f"{BOT_NAMESPACE}_s12_20260830T120000Z"
    return {
        "state": "CONFIG_PENDING",
        "decision": "CONFIG_CREATE",
        "reason": "exact config readback temporarily unavailable after successful upsert",
        "generation": generation,
        "config_name": generation,
        "runtime_instance": None,
        "anomaly_code": None,
        "released_session": None,
        "terminal_pnl_status": None,
        "terminal_pnl": None,
        "pending_operation": {
            "intent_id": "intent-config-12-0001",
            "kind": "config_create",
            "target": generation,
            "committed_values": {
                "config": {
                    "id": generation,
                    "controller_type": "generic",
                    "controller_name": "trend_aware_lp_rebalancer",
                    "candles_config": [],
                    "manual_kill_switch": False,
                    "initial_positions": [],
                    "connector_name": "solana-mainnet-beta",
                    "lp_provider": "orca/clmm",
                    "swap_provider": "jupiter/router",
                    "quote_token_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                    "total_amount_quote": "9",
                    "lp_positions": [
                        {
                            "position_id": f"pool_{pool_address}",
                            "trading_pair": f"{symbol}-USDC",
                            "pool_address": pool_address,
                            "base_token_mint": base_token_mint,
                            "allocation_pct": allocation,
                            "market_trend": trend,
                            "position_width_pct": "5",
                            "downside_offset_pct": offset,
                            "rebalance_threshold_pct": "0.5",
                        }
                        for symbol, pool_address, base_token_mint, allocation, trend, offset in (
                            (
                                "SOL",
                                "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE",
                                "So11111111111111111111111111111111111111112",
                                "33.333333333333333333",
                                "DOWN",
                                "0.5",
                            ),
                            (
                                "CBBTC",
                                "HxA6SKW5qA4o12fjVgTpXdq2YnZ5Zv1s7SB4FFomsyLM",
                                "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij",
                                "33.333333333333333333",
                                "SIDEWAYS",
                                "0",
                            ),
                            (
                                "ZEC",
                                "GTHKH8s82ZR8GTSFZ1dUu6wfdxhy59wpMShxzG5zjiPm",
                                "A7bdiYdS5GjqGFtxf17ppRHtDKPkkRqbKtR27dxvQXaS",
                                "33.333333333333333334",
                                "SIDEWAYS",
                                "0",
                            ),
                        )
                    ],
                    "lp_sizing_buffer_pct": "2",
                    "min_sol_reserve": "0.1",
                    "cleanup_min_quote_value": "0.01",
                    "rebalance_cooldown_minutes": 5,
                    "max_consecutive_controller_failures": 3,
                    "failure_retry_backoff_seconds": 30,
                    "controller_take_profit_ratio": "0.05",
                    "controller_stop_loss_ratio": "0.05",
                    "controller_time_limit_minutes": 720,
                    "controller_pnl_grace_period_minutes": 5,
                    "exit_requested": False,
                    "exit_reason": "none",
                }
            },
            "external_receipt": None,
            "outcome": "submitted",
        },
    }


def _five_position_pending_record() -> dict:
    record = json.loads(json.dumps(_pending_record()))
    positions = record["pending_operation"]["committed_values"]["config"][
        "lp_positions"
    ]
    positions.extend(
        [
            {
                "position_id": f"pool_{pool_address}",
                "trading_pair": f"{symbol}-USDC",
                "pool_address": pool_address,
                "base_token_mint": base_token_mint,
                "allocation_pct": "20",
                "market_trend": "SIDEWAYS",
                "position_width_pct": "5",
                "downside_offset_pct": "0",
                "rebalance_threshold_pct": "0.5",
            }
            for symbol, pool_address, base_token_mint in (
                (
                    "JUP",
                    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
                    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
                ),
                (
                    "BONK",
                    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6D5BCbpMxiTCEDB",
                    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6D5BCbpMxiTCEDB",
                ),
            )
        ]
    )
    for position in positions:
        position["allocation_pct"] = "20"
    return record


def _operation_record(
    *,
    state: str,
    decision: str,
    kind: str,
    target: str,
    committed_values: dict,
) -> dict:
    generation = f"{BOT_NAMESPACE}_s12_20260830T120000Z"
    runtime = f"{BOT_NAMESPACE}-20260830-120001"
    return {
        "state": state,
        "decision": decision,
        "reason": f"{kind} intent committed",
        "generation": generation,
        "config_name": generation,
        "runtime_instance": runtime,
        "anomaly_code": None,
        "released_session": None,
        "terminal_pnl_status": (
            committed_values.get("terminal_pnl_status") if kind == "archive" else None
        ),
        "terminal_pnl": (
            committed_values.get("terminal_pnl") if kind == "archive" else None
        ),
        "pending_operation": {
            "intent_id": f"intent-{kind}-12-0001",
            "kind": kind,
            "target": target,
            "committed_values": committed_values,
            "external_receipt": None,
            "outcome": None,
        },
    }


def _journal_example() -> dict:
    section = STRATEGY_PATH.read_text().split("## Journal continuity", 1)[1]
    block = section.split("```json", 1)[1].split("```", 1)[0]
    return json.loads(block)


def _pending_shape_table() -> dict[str, tuple[str, str]]:
    section = STRATEGY_PATH.read_text().split(
        "Use these exact inner shapes; do not invent alternate keys:", 1
    )[1]
    table = section.split("`external_receipt` preserves", 1)[0]
    rows = {}
    for line in table.splitlines():
        if not line.startswith("| `"):
            continue
        kind, target, committed = [cell.strip() for cell in line.strip("|").split("|")]
        kind = kind.strip("`")
        if kind != "kind":
            rows[kind] = (target, committed)
    return rows


def _decision_mapping_table() -> dict[str, str]:
    section = (
        STRATEGY_PATH.read_text()
        .split("Use one decision for each condition:", 1)[1]
        .split("`anomaly_code` is null", 1)[0]
    )
    rows = {}
    for line in section.splitlines():
        if not line.startswith("| ") or line.startswith("| ---"):
            continue
        condition, decision = [cell.strip() for cell in line.strip("|").split("|")]
        if condition != "Condition":
            rows[condition] = decision.strip("`")
    return rows


def _prompt(
    mode: str,
    *,
    recent_decisions: str = "",
    summary: str = "",
    learnings: str = "",
) -> str:
    agent = AgentStore().get(AGENT_SLUG)
    strategy = StrategyStore().get_by_key(STRATEGY_KEY)
    assert agent is not None and strategy is not None
    config = load_full_config(strategy.dir, strategy.default_config)
    config["execution_mode"] = mode
    suffix = "e1" if mode in {"dry_run", "run_once"} else "1"
    limits = config["risk_limits"]
    return build_tick_prompt(
        agent=agent,
        strategy=strategy,
        config=config,
        core_data={},
        learnings=learnings,
        summary=summary,
        recent_decisions=recent_decisions,
        risk_state={
            "total_exposure": 0,
            "max_position_size": limits["max_position_size_quote"],
            "executor_count": 0,
            "max_open_executors": limits["max_open_executors"],
            "max_drawdown_pct": limits["max_drawdown_pct"],
            "is_blocked": False,
        },
        tick_number=1,
        agent_id=f"{STRATEGY_KEY}_{suffix}",
        cached_routines_section=_build_routines_section(strategy),
        skills_index=SkillStore(AGENT_SLUG).list_index(),
    )


def test_agent_and_strategy_load_without_runtime_skills():
    agent = AgentStore().get(AGENT_SLUG)
    strategy = StrategyStore().get_by_key(STRATEGY_KEY)

    assert agent is not None and strategy is not None
    assert agent.slug == AGENT_SLUG
    assert strategy.agent_slug == AGENT_SLUG
    assert strategy.slug == "orca"
    assert strategy.skills == []
    assert not (ROOT / "skills").exists()
    assert not (ROOT / "RUNBOOK.md").exists()


def test_tool_allowlist_is_the_minimum_bot_operator_surface():
    assert _frontmatter(AGENT_PATH)["tools"] == [
        "get_portfolio_overview",
        "manage_controllers",
        "manage_bots",
        "manage_routines",
        "trading_agent_journal_write",
    ]

    prose = _prose(AGENT_PATH).casefold()
    for forbidden in (
        "standalone executor creation",
        "gateway swap or clmm mutations",
        "token registration",
        "native pool exploration",
        "memory",
        "history",
        "consultation",
        "delegation",
        "notification",
        "`manage_skill`",
    ):
        assert forbidden in prose

    assert "external tool/action gate" in prose
    assert "availability is not authority" in prose
    assert "an uncertain mutation is never retried" in prose
    assert "never choose or pin any of them" in prose


def test_agent_records_standing_user_authorization_for_full_lifecycle():
    agent = _prose(AGENT_PATH).casefold()

    for phrase in (
        "standing user authorization",
        "every live action expressly allowed",
        "terminal bot archive",
        "no separate approval is required at each lifecycle transition",
        "exact ownership",
    ):
        assert phrase in agent


def test_default_example_and_optional_runtime_files_match_the_compact_shape():
    defaults = _frontmatter(STRATEGY_PATH)["default_config"]
    configs = _loaded_strategy_configs()
    example = configs["example"]

    assert set(defaults) == STRATEGY_CONFIG_KEYS - {"trading_context"}
    assert set(example) == STRATEGY_CONFIG_KEYS
    assert {key: example[key] for key in defaults} == defaults
    assert set(example) - set(defaults) == {"trading_context"}

    # ponytail: runtime records are optional and may override session values.
    for config_name, config in configs.items():
        assert set(config) == set(example), config_name
        assert config["execution_mode"] == "loop"
        assert isinstance(config["server_name"], str) and config["server_name"]
        assert isinstance(config["account_name"], str) and config["account_name"]
        assert isinstance(config["trading_context"], str)
        assert config["bot_name"] == BOT_NAMESPACE
        assert config["bot_mode"] == "bot"
        assert config["canvas_enabled"] is False
        assert isinstance(config["max_ticks"], int) and not isinstance(
            config["max_ticks"], bool
        )
        assert config["max_ticks"] == 0

        for field in (
            "frequency_sec",
            "min_positions",
            "max_positions",
            "candidate_scan_limit",
            "time_limit_minutes",
        ):
            assert isinstance(config[field], int) and not isinstance(
                config[field], bool
            ), (config_name, field)

        assert config["frequency_sec"] > 0
        assert 1 <= config["min_positions"] <= config["max_positions"]
        assert config["max_positions"] <= config["candidate_scan_limit"]

        for field in (
            "total_amount_quote",
            "min_position_amount_quote",
            "min_sol_reserve",
            "min_pool_tvl_usd",
            "min_fee_productivity_bps_per_day",
        ):
            value = config[field]
            assert isinstance(value, (int, float)) and not isinstance(value, bool), (
                config_name,
                field,
            )
            assert Decimal(str(value)).is_finite() and Decimal(str(value)) > 0

        assert Decimal(str(config["min_position_amount_quote"])) * config[
            "min_positions"
        ] <= Decimal(str(config["total_amount_quote"]))
        assert config["risk_profile"] in {"conservative", "balanced", "high_yield"}

        for field in ("take_profit_ratio", "stop_loss_ratio"):
            value = config[field]
            assert isinstance(value, (int, float)) and not isinstance(value, bool), (
                config_name,
                field,
            )
            ratio = Decimal(str(value))
            assert ratio.is_finite() and Decimal("0") < ratio <= Decimal("1")
        assert 5 < config["time_limit_minutes"] <= 525600

        for field in ("excluded_base_mints", "excluded_pool_addresses"):
            values = config[field]
            assert isinstance(values, list)
            assert all(isinstance(value, str) and value for value in values)
            assert len(values) == len(set(values))

        limits = config["risk_limits"]
        assert set(limits) == {
            "max_position_size_quote",
            "max_open_executors",
            "max_drawdown_pct",
            "shutdown_drawdown_pct",
        }
        assert isinstance(
            limits["max_position_size_quote"], (int, float)
        ) and not isinstance(limits["max_position_size_quote"], bool)
        max_position_size = Decimal(str(limits["max_position_size_quote"]))
        assert max_position_size.is_finite()
        assert max_position_size >= Decimal(str(config["total_amount_quote"]))
        assert isinstance(limits["max_open_executors"], int) and not isinstance(
            limits["max_open_executors"], bool
        )
        assert limits["max_open_executors"] >= 1
        assert limits["max_drawdown_pct"] == -1
        assert limits["shutdown_drawdown_pct"] == -1

    assert "[RISK STATE]` to be `ACTIVE`" in _prose(STRATEGY_PATH)
    if LEARNINGS_PATH.exists():
        assert LEARNINGS_PATH.read_text().strip() == ""

    obsolete = {
        "target_active_controllers",
        "max_active_controllers",
        "min_controller_amount_quote",
        "trend_signal_max_age_seconds",
        "allocation_safety_weights",
        "allocation_profile_exponents",
        "range_profiles",
        "mcda_weights",
        "controller_started_at",
    }
    assert obsolete.isdisjoint(defaults)


def test_config_validation_and_funding_gates_are_explicit():
    strategy = _prose(STRATEGY_PATH)
    required = (
        "reject an unknown prompt-visible Strategy-policy field",
        "no boolean where a number is expected",
        "non-finite number",
        "duplicate exclusion",
        "1 <= min_positions <= max_positions <= candidate_scan_limit",
        "min_position_amount_quote * min_positions <= total_amount_quote",
        "risk_profile` exactly `conservative`, `balanced`, or `high_yield",
        "take_profit_ratio` and `stop_loss_ratio` in `(0, 1]",
        "integer `time_limit_minutes` with `5 < time_limit_minutes <= 525600`",
        "five-minute terminal-PnL grace remains below the session lifetime",
        "position-size limit to be at least `total_amount_quote`",
        "available canonical USDC at least `total_amount_quote",
        "available SOL at least `min_sol_reserve",
        "fresh session owns no prior runtime",
        "global namespace/account matches and Condor-injected Executors from older "
        "sessions are outside its authority",
        "must report the configured account before mutation",
        "Foreign-bot account or wallet metadata is diagnostic only and never blocks",
        "The Agent validates the exact values exposed in `[CURRENT CONFIG]`",
    )
    for phrase in required:
        assert phrase.casefold() in strategy.casefold()


def test_prompt_modes_preserve_the_strategy_action_boundary():
    dry = _prompt("dry_run")
    once = _prompt("run_once")
    loop = _prompt("loop")

    assert dry.startswith(
        "You are an autonomous trading agent running inside Condor in 🧪 DRY RUN mode"
    )
    assert "This is OBSERVATION ONLY" in dry
    assert "This is an experiment (dry-run / run-once): there is NO journal" in dry
    run_once_marker = (
        "\n[EXECUTION MODE — RUN ONCE]\n"
        "Single-tick session with LIVE execution. The engine will stop"
    )
    assert run_once_marker not in dry

    assert "steering the controllers" in once
    assert "This is an experiment (dry-run / run-once): there is NO journal" in once
    assert run_once_marker in once
    assert "Dry run and run once remain observation/proposal only" in " ".join(
        once.split()
    )

    assert "steering the controllers" in loop
    assert "Write ONE action entry per tick" in loop
    assert "This is an experiment (dry-run / run-once)" not in loop
    assert run_once_marker not in loop
    assert f"Agent ID: {STRATEGY_KEY}_1" in loop

    for prompt in (dry, once, loop):
        current = prompt[prompt.rindex("[CURRENT CONFIG]") :].split(
            "[CONTROLLER MODE]", 1
        )[0]
        assert "execution_mode:" not in current
        assert "frequency_sec:" not in current
        assert "server_name:" not in current
        assert "risk_limits:" not in current
        assert BOT_NAMESPACE in current

    assert "Do not journal or mutate" in once
    assert "Loop mode may mutate after the live admission checks pass" in loop


def test_exact_routines_and_no_runtime_authoring_policy_are_explicit():
    prose = (_prose(AGENT_PATH) + " " + _prose(STRATEGY_PATH)).casefold()
    local = discover_routines_from_path(
        ROOT / "routines", agent_slug=AGENT_SLUG, force_reload=True
    )
    merged = assistant_routines(AGENT_SLUG, force_reload=True)
    local_names = {
        name
        for name, routine in merged.items()
        if routine.source == f"agent:{AGENT_SLUG}"
    }
    shared_names = set(merged) - local_names

    assert set(local) == {"scan_orca_pools", "read_trend_aware_lp_session"}
    assert local_names == set(local)
    assert shared_names
    assert shared_names.isdisjoint(local_names)
    assert "`scan_orca_pools`" in prose
    assert "`read_trend_aware_lp_session`" in prose
    assert "`required_pool_addresses` alias" in prose
    assert "run only `scan_orca_pools` and `read_trend_aware_lp_session`" in prose
    assert "never list, describe, create, update, delete, or schedule routines" in prose
    assert "visibility never authorizes them" in prose
    assert "external tool/action gate" in prose
    for historical in (
        "allocate_controller_capital",
        "register_gateway_token",
        "snapshot_trend_aware_lp_bots",
    ):
        assert historical not in prose


def test_each_new_admission_config_readback_and_mutation_limit_are_exact():
    strategy = _prose(STRATEGY_PATH)
    tick = strategy.split("## Canonical loop tick", 1)[1].split(
        "## Journal continuity", 1
    )[0]

    upsert = tick.index("submit one create-only config upsert")
    readback = tick.index("`Config Details` block")
    journal = tick.index("deploy-intent journal entry")
    deploy = tick.index("same-tick bot deployment")
    assert upsert < readback < journal < deploy

    for phrase in (
        "sole two-write exception",
        "Do not write a journal entry before the config upsert",
        "Missing, duplicate, truncated, unparseable, or mismatched config serialization",
        "exactly one action entry",
        "normally ending `DEPLOY_PENDING`",
        "Tick two reconciles only the runtime created from this session's deploy intent",
        "at most one external mutation",
    ):
        assert phrase.casefold() in strategy.casefold()

    raw = STRATEGY_PATH.read_text()
    for row in (
        "| Proven name collision | No | `VACANT` | `config_create` / `rejected_before_submit` |",
        "| Timeout, cancellation, or transport failure after the upsert call may have left Condor | No | `CONFIG_PENDING` | `config_create` / `uncertain` |",
        "| Upsert explicitly returned success, but one known generation's readback is temporarily unavailable, missing, truncated, or unparseable | No | `CONFIG_PENDING` | `config_create` / `submitted` |",
        "| Duplicate `Config Details` blocks, contradictory identity, or any full-field mismatch | No | `QUARANTINED` | `config_create` / `ambiguous` |",
        "| Exactly one complete block and every normalized field matches | Yes, once after this entry | `DEPLOY_PENDING` | `deploy` / `null` |",
    ):
        assert row in raw
    assert "name collision or other authoritative pre-submit rejection" not in raw

    assert "Its `outcome` is literal JSON `null`" in raw
    assert "Do not write a second action entry with the deploy response" in strategy
    assert (
        "Each newly selected `VACANT` generation receives the sole two-write exception"
        in strategy
    )
    assert "not limited to the Agent process's lifetime first tick" in strategy
    assert "Do not write a journal entry before the config upsert" in strategy

    collision = _pending_record()
    collision.update(
        {
            "state": "VACANT",
            "generation": None,
            "config_name": None,
            "runtime_instance": None,
            "reason": "authoritative pre-submit name collision",
        }
    )
    collision["pending_operation"]["outcome"] = "rejected_before_submit"
    assert collision["pending_operation"]["target"].startswith(BOT_NAMESPACE)
    assert all(
        collision[field] is None
        for field in ("generation", "config_name", "runtime_instance", "anomaly_code")
    )
    following_tick = {
        **collision,
        "decision": "HOLD",
        "pending_operation": {
            "intent_id": None,
            "kind": "none",
            "target": None,
            "committed_values": None,
            "external_receipt": None,
            "outcome": None,
        },
    }
    assert following_tick["pending_operation"]["kind"] == "none"
    assert (
        "the following tick clears that operation and may generate a different name"
        in strategy
    )


def test_canonical_journal_schema_decisions_and_pending_shapes_are_exact():
    example = _journal_example()
    assert list(example) == [
        "state",
        "decision",
        "reason",
        "generation",
        "config_name",
        "runtime_instance",
        "anomaly_code",
        "released_session",
        "terminal_pnl_status",
        "terminal_pnl",
        "pending_operation",
    ]
    assert list(example["pending_operation"]) == [
        "intent_id",
        "kind",
        "target",
        "committed_values",
        "external_receipt",
        "outcome",
    ]

    raw = STRATEGY_PATH.read_text()
    decision_clause = raw.split("`decision` is exactly", 1)[1].split(
        "It describes this tick's decision", 1
    )[0]
    assert set(re.findall(r"`([A-Z_]+)`", decision_clause)) == {
        "HOLD",
        "CONFIG_CREATE",
        "DEPLOY",
        "FORMATION_UPDATE",
        "AGENT_EXIT",
        "SUPERVISE_EXIT",
        "ARCHIVE",
        "ARCHIVE_CONFIRMED",
        "QUARANTINE",
    }

    assert _decision_mapping_table() == {
        "no justified mutation or unresolved read-only reconciliation": "HOLD",
        "config-create attempt and its readback branch": "CONFIG_CREATE",
        "verified config followed by deploy intent": "DEPLOY",
        "formation-update intent": "FORMATION_UPDATE",
        "Agent-exit intent; journal `state: EXITING` before the call": "AGENT_EXIT",
        "controller close/cleanup before complete terminal proof": "SUPERVISE_EXIT",
        "first complete terminal observation": "SUPERVISE_EXIT",
        "later fresh terminal reconfirmation and archive intent": "ARCHIVE",
        "exact archive record plus active-runtime absence with no same-tick admission action": "ARCHIVE_CONFIRMED",
        "unsafe evidence/manual handoff": "QUARANTINE",
    }

    anomaly_clause = re.search(
        r"use exactly one\s+stable code:(.*?)Keep the\s+same code",
        raw,
        re.DOTALL,
    ).group(1)
    assert set(re.findall(r"`([A-Z_]+)`", anomaly_clause)) == {
        "AUTHORITY_CONFLICT",
        "IDENTITY_CONFLICT",
        "MULTIPLE_MATCHES",
        "TELEMETRY_STALE",
        "TELEMETRY_SCHEMA_INVALID",
        "CONTROLLER_FAULTED",
        "UNEXPLAINED_DISAPPEARANCE",
        "MUTATION_AMBIGUOUS",
        "CONFIG_MISMATCH",
        "REQUIRED_EVIDENCE_UNAVAILABLE",
        "ARCHIVE_ERROR",
    }
    assert "`anomaly_code` is null outside `QUARANTINED`" in raw
    assert (
        '`{"generation":<old-generation>,"config_name":<old-config-name>,'
        '"runtime_instance":<old-runtime-instance>}`'
    ) in raw
    assert (
        '`{"global":{"pnl_quote":<literal-or-null>,"pnl_ratio":<literal-or-null>},'
        '"positions":{<every-position-id>:{"pnl_quote":<literal-or-null>,'
        '"pnl_ratio":<literal-or-null>}}}`'
    ) in raw

    assert _pending_shape_table() == {
        "none": ("null", "null"),
        "config_create": (
            "exact generation/config name",
            '`{"config":<complete-controller-config>}`',
        ),
        "deploy": (
            "exact bot namespace",
            '`{"bot_name":<namespace>,"config_name":<generation>,"account_name":<account>,"max_global_drawdown_quote":<total-amount-quote>}`',
        ),
        "formation_update": (
            "exact runtime instance",
            '`{"config_name":<generation>,"previous_position_formations":{<changed-position-id>:{"market_trend":<old-value>,"position_width_pct":<old-value>,"downside_offset_pct":<old-value>,"rebalance_threshold_pct":<old-value>}},"intended_position_formations":{<changed-position-id>:{"market_trend":<new-value>,"position_width_pct":<new-value>,"downside_offset_pct":<new-value>,"rebalance_threshold_pct":<new-value>}}}`',
        ),
        "agent_exit": (
            "exact runtime instance",
            '`{"config_name":<generation>,"exit_requested":true,"exit_reason":"operator","original_exit_reasoning":<intent-tick-reason>,"adverse_evidence":[<evidence-row>,...]}`',
        ),
        "archive": (
            "exact runtime instance",
            '`{"terminal_pnl_status":<available-or-unavailable>,"terminal_pnl":{"global":{"pnl_quote":<literal-or-null>,"pnl_ratio":<literal-or-null>},"positions":{<every-position-id>:{"pnl_quote":<literal-or-null>,"pnl_ratio":<literal-or-null>}}}}`',
        ),
    }


def test_exact_native_read_and_mutation_calls_are_documented():
    strategy = _prose(STRATEGY_PATH)
    for phrase in (
        "tool: get_portfolio_overview",
        "account_names: [<configured-account>]",
        "connector_names: [solana-mainnet-beta]",
        "include_balances: true",
        "include_perp_positions: false",
        "include_lp_positions: false",
        "include_active_orders: false",
        "as_distribution: false",
        "refresh: true",
        "controller_type: generic",
        "controller_name: trend_aware_lp_rebalancer",
        "action: upsert",
        "target: config",
        "confirm_override: false",
        "action: describe",
        "include_code: false",
        "use it to confirm the expected V2 controller identity",
        "action: deploy",
        f"bot_name: {BOT_NAMESPACE}",
        "controllers_config: [<generation>]",
        "max_global_drawdown_quote: <total_amount_quote>",
        "the configured Hummingbot API environment owns its deployment image",
        "Do not send `image`",
        "Do not send `max_controller_drawdown_quote`",
        "action: update_config",
        "confirm_override: true",
        "action: stop_bot",
    ):
        assert phrase.casefold() in strategy.casefold()

    assert "hummingbot/hummingbot" not in strategy.casefold()
    assert "@sha256:" not in strategy.casefold()


def test_compact_json_pending_tuple_survives_more_than_three_hold_ticks(tmp_path):
    journal = JournalManager(
        f"{STRATEGY_KEY}_42",
        session_dir=tmp_path / "session",
        agent_dir=tmp_path / "agent",
    )
    record = _pending_record()
    assert set(record) == {
        "state",
        "decision",
        "reason",
        "generation",
        "config_name",
        "runtime_instance",
        "anomaly_code",
        "released_session",
        "terminal_pnl_status",
        "terminal_pnl",
        "pending_operation",
    }
    assert record["decision"] == "CONFIG_CREATE"
    assert record["anomaly_code"] is None
    assert record["released_session"] is None
    assert record["terminal_pnl_status"] is None
    assert record["terminal_pnl"] is None
    committed_wrapper = record["pending_operation"]["committed_values"]
    assert set(committed_wrapper) == {"config"}
    committed = committed_wrapper["config"]
    assert set(committed) == {
        "id",
        "controller_type",
        "controller_name",
        "candles_config",
        "manual_kill_switch",
        "initial_positions",
        "connector_name",
        "lp_provider",
        "swap_provider",
        "quote_token_mint",
        "total_amount_quote",
        "lp_positions",
        "lp_sizing_buffer_pct",
        "min_sol_reserve",
        "cleanup_min_quote_value",
        "rebalance_cooldown_minutes",
        "max_consecutive_controller_failures",
        "failure_retry_backoff_seconds",
        "controller_take_profit_ratio",
        "controller_stop_loss_ratio",
        "controller_time_limit_minutes",
        "controller_pnl_grace_period_minutes",
        "exit_requested",
        "exit_reason",
    }
    assert all(
        position["position_id"] == f"pool_{position['pool_address']}"
        for position in committed["lp_positions"]
    )
    assert all(
        set(position)
        == {
            "position_id",
            "trading_pair",
            "pool_address",
            "base_token_mint",
            "allocation_pct",
            "market_trend",
            "position_width_pct",
            "downside_offset_pct",
            "rebalance_threshold_pct",
        }
        for position in committed["lp_positions"]
    )
    payload = json.dumps(record, separators=(",", ":"), sort_keys=True)

    assert "\n" not in payload
    journal.append_action(1, payload, "", "")
    hold = json.loads(payload)
    hold["decision"] = "HOLD"
    hold["reason"] = "exact config readback remains unavailable"
    hold_payload = json.dumps(hold, separators=(",", ":"), sort_keys=True)
    for tick in range(2, 7):
        journal.append_action(tick, hold_payload, "", "")

    recent = journal.get_recent_decisions(count=3).splitlines()
    assert len(recent) == 3
    assert all(f"**#{tick}**" in line for tick, line in zip(range(4, 7), recent))
    recovered = [json.loads(line[line.index("{") :]) for line in recent]
    assert recovered == [hold, hold, hold]
    assert all(
        item["pending_operation"] == record["pending_operation"] for item in recovered
    )


def test_complete_controller_config_and_defaults_are_runtime_instructions():
    raw = STRATEGY_PATH.read_text()
    section = raw.split("## Controller config and naming", 1)[1].split(
        "## Exact mutation calls", 1
    )[0]
    key_clause = section.split("The complete top-level key set is exactly", 1)[1].split(
        "For the\nexpected controller model", 1
    )[0]
    documented_keys = set(re.findall(r"`([a-z_]+)`", key_clause))
    committed = _pending_record()["pending_operation"]["committed_values"]["config"]
    configs = _loaded_strategy_configs()

    assert documented_keys == CONTROLLER_TOP_LEVEL_KEYS
    assert set(committed) == CONTROLLER_TOP_LEVEL_KEYS
    assert {
        key: committed[key] for key in PINNED_CONTROLLER_DEFAULTS
    } == PINNED_CONTROLLER_DEFAULTS
    barrier_mapping = {
        "take_profit_ratio": "controller_take_profit_ratio",
        "stop_loss_ratio": "controller_stop_loss_ratio",
        "time_limit_minutes": "controller_time_limit_minutes",
    }
    example = configs["example"]
    assert example["min_positions"] <= len(committed["lp_positions"])
    assert len(committed["lp_positions"]) <= example["max_positions"]
    assert len(committed["lp_positions"]) <= example["candidate_scan_limit"]
    for strategy_field, controller_field in barrier_mapping.items():
        assert Decimal(str(example[strategy_field])) == Decimal(
            str(committed[controller_field])
        )
    assert Decimal(str(example["min_sol_reserve"])) == Decimal(
        committed["min_sol_reserve"]
    )
    assert (
        PINNED_CONTROLLER_DEFAULTS["controller_pnl_grace_period_minutes"]
        < example["time_limit_minutes"]
    )
    assert Decimal(str(example["total_amount_quote"])) == Decimal(
        committed["total_amount_quote"]
    )

    normalized_section = " ".join(section.split())
    pinned_clause = normalized_section.split("For the expected controller model", 1)[
        1
    ].split("Current Strategy/scanner evidence", 1)[0]
    for instruction in (
        "`controller_type: generic`",
        "`controller_name: trend_aware_lp_rebalancer`",
        "`candles_config: []`",
        "`manual_kill_switch: false`",
        "`initial_positions: []`",
        "`connector_name: solana-mainnet-beta`",
        "`lp_provider: orca/clmm`",
        "`swap_provider: jupiter/router`",
        "`lp_sizing_buffer_pct: 2`",
        "`cleanup_min_quote_value: 0.01`",
        "`rebalance_cooldown_minutes: 5`",
        "`max_consecutive_controller_failures: 3`",
        "`failure_retry_backoff_seconds: 30`",
        "`controller_pnl_grace_period_minutes: 5`",
    ):
        assert instruction in pinned_clause

    loop_prompt = " ".join(_prompt("loop").split())
    assert "The complete top-level key set is exactly" in loop_prompt
    assert pinned_clause.strip() in loop_prompt


def test_controller_schema_is_binary_and_never_a_second_position_count_limit():
    strategy = _prose(STRATEGY_PATH)
    assert (
        "These configured bounds, funding, eligible candidates are the only count "
        "limits. Controller-schema compatibility is a binary admission prerequisite, "
        "never another position-count ceiling."
    ) in strategy
    assert "accepts any non-empty `lp_positions` length" in strategy
    assert "never another position-count ceiling" in strategy


def test_five_position_config_and_journal_round_trip_has_no_three_position_cap(
    tmp_path,
):
    journal = JournalManager(
        f"{STRATEGY_KEY}_43",
        session_dir=tmp_path / "session",
        agent_dir=tmp_path / "agent",
    )
    record = _five_position_pending_record()
    config = record["pending_operation"]["committed_values"]["config"]
    positions = config["lp_positions"]

    assert len(positions) == 5
    assert len({position["pool_address"] for position in positions}) == 5
    assert len({position["position_id"] for position in positions}) == 5
    assert sum(
        Decimal(position["allocation_pct"]) for position in positions
    ) == Decimal("100")
    assert set(config) == CONTROLLER_TOP_LEVEL_KEYS

    payload = json.dumps(record, separators=(",", ":"), sort_keys=True)
    journal.append_action(1, payload, "", "")
    line = journal.get_recent_decisions(count=1)
    recovered = json.loads(line[line.index("{") :])

    assert recovered == record
    assert (
        len(
            recovered["pending_operation"]["committed_values"]["config"]["lp_positions"]
        )
        == 5
    )


def test_real_journal_round_trips_all_mutation_pending_shapes(tmp_path):
    generation = f"{BOT_NAMESPACE}_s12_20260830T120000Z"
    runtime = f"{BOT_NAMESPACE}-20260830-120001"
    position_id = "pool_Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"
    previous = {
        position_id: {
            "market_trend": "SIDEWAYS",
            "position_width_pct": "5",
            "downside_offset_pct": "0",
            "rebalance_threshold_pct": "0.5",
        }
    }
    intended = {
        position_id: {
            "market_trend": "DOWN",
            "position_width_pct": "6",
            "downside_offset_pct": "0.5",
            "rebalance_threshold_pct": "0.75",
        }
    }
    terminal_pnl = {
        "global": {"pnl_quote": "0.17", "pnl_ratio": "0.0189"},
        "positions": {position_id: {"pnl_quote": "0.17", "pnl_ratio": "0.0189"}},
    }
    records = [
        _operation_record(
            state="DEPLOY_PENDING",
            decision="DEPLOY",
            kind="deploy",
            target=BOT_NAMESPACE,
            committed_values={
                "bot_name": BOT_NAMESPACE,
                "config_name": generation,
                "account_name": "primary",
                "max_global_drawdown_quote": "9",
            },
        ),
        _operation_record(
            state="FORMATION_UPDATE_PENDING",
            decision="FORMATION_UPDATE",
            kind="formation_update",
            target=runtime,
            committed_values={
                "config_name": generation,
                "previous_position_formations": previous,
                "intended_position_formations": intended,
            },
        ),
        _operation_record(
            state="EXITING",
            decision="AGENT_EXIT",
            kind="agent_exit",
            target=runtime,
            committed_values={
                "config_name": generation,
                "exit_requested": True,
                "exit_reason": "operator",
                "original_exit_reasoning": "explicit live human exit",
                "adverse_evidence": [],
            },
        ),
        _operation_record(
            state="ARCHIVE_PENDING",
            decision="ARCHIVE",
            kind="archive",
            target=runtime,
            committed_values={
                "terminal_pnl_status": "available",
                "terminal_pnl": terminal_pnl,
            },
        ),
    ]
    recovered_records = []
    for index, record in enumerate(records, start=1):
        journal = JournalManager(
            f"{STRATEGY_KEY}_{44 + index}",
            session_dir=tmp_path / f"session_{index}",
            agent_dir=tmp_path / f"agent_{index}",
        )
        payload = json.dumps(record, separators=(",", ":"), sort_keys=True)
        assert "\n" not in payload
        journal.append_action(1, payload, "", "")
        hold = json.loads(payload)
        hold["decision"] = "HOLD"
        hold["reason"] = f"{record['pending_operation']['kind']} remains unresolved"
        hold_payload = json.dumps(hold, separators=(",", ":"), sort_keys=True)
        for tick in range(2, 7):
            journal.append_action(tick, hold_payload, "", "")
        lines = journal.get_recent_decisions(count=3).splitlines()
        recovered = [json.loads(line[line.index("{") :]) for line in lines]
        assert recovered == [hold, hold, hold]
        assert all(
            item["pending_operation"] == record["pending_operation"]
            for item in recovered
        )
        recovered_records.append(recovered[-1])

    assert [record["pending_operation"]["kind"] for record in recovered_records] == [
        "deploy",
        "formation_update",
        "agent_exit",
        "archive",
    ]
    assert set(recovered_records[0]["pending_operation"]["committed_values"]) == {
        "bot_name",
        "config_name",
        "account_name",
        "max_global_drawdown_quote",
    }
    assert set(recovered_records[1]["pending_operation"]["committed_values"]) == {
        "config_name",
        "previous_position_formations",
        "intended_position_formations",
    }
    assert all(
        set(formation) == FORMATION_FIELDS
        for formation_map in (previous, intended)
        for formation in formation_map.values()
    )
    assert set(recovered_records[2]["pending_operation"]["committed_values"]) == {
        "config_name",
        "exit_requested",
        "exit_reason",
        "original_exit_reasoning",
        "adverse_evidence",
    }
    assert set(recovered_records[3]["pending_operation"]["committed_values"]) == {
        "terminal_pnl_status",
        "terminal_pnl",
    }
    assert recovered_records[3]["terminal_pnl"] == terminal_pnl


def test_journal_pending_outcomes_and_session_isolation_are_explicit():
    prose = (_prose(AGENT_PATH) + " " + _prose(STRATEGY_PATH)).casefold()
    for state in (
        "VACANT",
        "CONFIG_PENDING",
        "DEPLOY_PENDING",
        "RUNNING",
        "FORMATION_UPDATE_PENDING",
        "EXITING",
        "EXITED_PENDING_ARCHIVE",
        "ARCHIVE_PENDING",
        "QUARANTINED",
    ):
        assert f"`{state.casefold()}`" in prose

    strategy = STRATEGY_PATH.read_text()
    assert "On the first terminal-proof tick" in strategy
    assert "EXITED_PENDING_ARCHIVE" in strategy
    assert "On a later tick, freshly repeat steps 3–4" in strategy
    assert "Both may occur in the same tick." in strategy
    assert "new direct\n   tool call containing only" in strategy
    assert "In that same terminal-observation tick" not in strategy
    assert "do not delay `stop_bot`" not in strategy

    for outcome in (
        "rejected_before_submit",
        "submitted",
        "confirmed",
        "confirmed_terminal_no_effect",
        "uncertain",
        "ambiguous",
        "unavailable",
    ):
        assert f"`{outcome}`" in prose
    for field in (
        '"decision":',
        '"reason":',
        '"generation":',
        '"config_name":',
        '"runtime_instance":',
        '"anomaly_code":',
        '"released_session":',
        '"terminal_pnl_status":',
        '"terminal_pnl":',
        '"pending_operation":',
        '"intent_id":',
        '"committed_values":',
        '"external_receipt":',
        '"outcome":',
    ):
        assert field in STRATEGY_PATH.read_text()

    for phrase in (
        "one valid compact JSON object",
        "one physical line with no literal newline",
        "JSON strings escape any embedded line break",
    ):
        assert phrase.casefold() in prose

    for phrase in (
        "temporary read failure with one known pending identity",
        "starts with no owned bot and derives `VACANT`",
        "older namespace/account resources remain outside this session",
        "BotLedger` attribution and injected Executor ownership do not grant Strategy "
        "supervision authority",
        "never pass an injected identity to the session reader",
        "returns only resources matching the expected current-session generation or runtime",
        "with all three expected identities null, it returns no owned active match",
        "unexplained disappearance is `QUARANTINED`, never `VACANT`",
        "Only the last three decisions are injected",
        "an uncertain mutation is never retried",
        "next tick reconciles the exact runtime identity",
    ):
        assert phrase.casefold() in prose

    terminal = _pending_record()
    terminal["pending_operation"]["outcome"] = "confirmed"
    assert terminal["pending_operation"]["kind"] == "config_create"
    cleared = {
        **terminal,
        "pending_operation": {
            "intent_id": None,
            "kind": "none",
            "target": None,
            "committed_values": None,
            "external_receipt": None,
            "outcome": None,
        },
    }
    assert cleared["pending_operation"]["kind"] == "none"
    assert (
        "retain the completed operation and terminal outcome in that decision line"
        in prose
    )
    assert "on the next tick replace it with `kind: none`" in prose


def test_archive_stop_uses_a_separate_tool_boundary_after_journal_success():
    agent = AGENT_PATH.read_text()
    strategy = STRATEGY_PATH.read_text()
    exit_flow = strategy.split("## Controller exit and bot archive", 1)[1]

    assert "Terminal `stop_bot` follows the Strategy's separate-call shutdown rule." in agent
    assert "Call the journal tool alone; its success must return" in exit_flow
    assert "Both may occur in the same tick." in exit_flow
    assert "new direct\n   tool call containing only" in exit_flow
    assert "Never batch,\n   parallelize, or compose these calls." in exit_flow
    assert "Journal failure\n   blocks the stop." in exit_flow
    assert exit_flow.index("Call the journal tool alone") < exit_flow.index(
        "tool call containing only"
    )


def test_archive_retry_requires_proven_pre_submit_rejection_and_correction():
    states = STRATEGY_PATH.read_text().split("State behavior:", 1)[1].split(
        "## Schema 3 supervision", 1
    )[0]

    assert "never repeat a submitted,\n  uncertain, or ambiguous request" in states
    assert "pre-dispatch rejection is\n  `rejected_before_submit`" in states
    assert "such as batched to standalone" in states
    assert "An unchanged standalone rejection requires\n  explicit operator approval." in states


def test_pool_selection_and_equal_allocation_policy_are_complete():
    strategy = _prose(STRATEGY_PATH)
    for phrase in (
        "fundable_positions = floor(total_amount_quote / min_position_amount_quote)",
        "available_capacity = min(max_positions, fundable_positions, eligible_candidates_returned)",
        "mandatory, preferred, or unspecified",
        "requested LP position count is exact",
        "Scanner rank orders attention; it never commands the portfolio",
        "natural comparable-quality group",
        "add the next-best eligible candidates until the minimum",
        "best complete portfolio of exactly `max_positions`",
        "pool quality, diversification, equal-budget impact, and reward contribution",
        "Different pool addresses alone do not prove diversification",
        "first meaningful excluded candidate",
        "Assign the decimal remainder to the final position",
        "Do not weight capital by MCDA score",
    ):
        assert phrase.casefold() in strategy.casefold()


def test_controller_mapping_and_generation_reuse_rules_are_exact():
    strategy = _prose(STRATEGY_PATH)
    assert (
        "Use Strategy-fixed bot, controller, network, provider, and mint identities"
        in _prose(AGENT_PATH)
    )
    assert USDC_MINT in strategy
    for phrase in (
        "<bot_name>_s<current-session-number>_<UTC YYYYMMDDTHHMMSSZ>",
        "Use that exact string for controller `id` and saved `config_name`",
        "Never reuse an ID after any Executor history",
        'position_id = "pool_" + <full-pool-address>',
        'trading_pair = <scanner-base-symbol-with-exact-casing> + "-USDC"',
        "Preserve the scanner's exact `base_symbol` casing",
        "do not uppercase or otherwise rewrite it",
        "Reject one symbol resolving to different mints",
        "Base symbols cannot contain `-` or equal `USDC`",
        "invalid or ambiguous, including `USDC-USDC`",
        "allocations total exactly `100`",
        "membership and allocation are immutable",
        "exit_requested: false",
        "exit_reason: none",
        "Each `lp_positions` row has exactly",
        "`base_decimals` is scanner-only evidence; never serialize it",
        "controller_started_at",
    ):
        assert phrase.casefold() in strategy.casefold()
    for phrase in (
        "`take_profit_ratio` -> `controller_take_profit_ratio`",
        "`stop_loss_ratio` -> `controller_stop_loss_ratio`",
        "`time_limit_minutes` -> `controller_time_limit_minutes`",
        "The Agent only copies those three values into the controller config",
        "It never evaluates the triple barriers",
        "the controller owns their evaluation and automatic exit",
    ):
        assert phrase.casefold() in strategy.casefold()


def test_session_reader_contract_uses_account_not_wallet_as_authority():
    session_reader = " ".join(
        STRATEGY_PATH.read_text()
        .split("### Session reader", 1)[1]
        .split("### Orca scanner", 1)[0]
        .split()
    ).casefold()
    assert "exact account observability and conflicts" in session_reader
    assert "wallet metadata is not queried across bots" in session_reader
    assert "never trading authority" in session_reader


def test_reused_controller_id_is_controller_fault_evidence_not_anomaly_code():
    raw = STRATEGY_PATH.read_text()
    anomaly_clause = re.search(
        r"use exactly one\s+stable code:(.*?)Keep the\s+same code",
        raw,
        re.DOTALL,
    ).group(1)
    anomaly_codes = set(re.findall(r"`([A-Z_]+)`", anomaly_clause))
    supervision = " ".join(
        raw.split("## Schema 3 supervision", 1)[1]
        .split("## Formation retuning", 1)[0]
        .split()
    )

    assert "REUSED_CONTROLLER_ID" not in anomaly_codes
    assert "CONTROLLER_FAULTED" in anomaly_codes
    assert (
        "A position error `REUSED_CONTROLLER_ID` is controller evidence, not an Agent "
        "anomaly-code value. Its required top-level `FAULTED` state derives `QUARANTINED` "
        "with the existing `CONTROLLER_FAULTED` journal code and never permits ID reuse "
        "or automatic recovery."
    ) in supervision


def test_running_formation_refresh_preserves_active_position():
    strategy = _prose(STRATEGY_PATH)
    refresh = strategy.split("### Orca scanner", 1)[1].split(
        "## Canonical loop tick", 1
    )[0]
    formation = strategy.split("## Formation retuning", 1)[1].split(
        "## Controller exit and bot archive", 1
    )[0]

    assert set(
        re.findall(r"`(ok|invalid_evidence|missing|source_error)`", refresh)
    ) == {
        "ok",
        "invalid_evidence",
        "missing",
        "source_error",
    }
    assert (
        "Apply valid `ok` rows from a degraded refresh and preserve an explicitly returned "
        "non-`ok` position's existing `formation.next`."
    ) in refresh
    assert (
        "An omitted requested identity or transport-overflow result makes the entire "
        "refresh `unavailable` and blocks every sibling update that tick."
    ) in refresh
    assert (
        "An explicitly returned non-`ok` row for one pool preserves that pool's next "
        "formation without blocking a valid sibling update."
    ) in formation
    assert (
        "An omitted requested pool or unavailable whole refresh blocks every update that tick."
        in formation
    )
    assert "change only those four fields" in formation
    assert "The update does not change the active LP" in formation


def test_formation_propagation_lag_and_exit_priority_are_exact():
    strategy = _prose(STRATEGY_PATH)
    tick = strategy.split("## Canonical loop tick", 1)[1].split(
        "## Journal continuity", 1
    )[0]
    states = strategy.split("State behavior:", 1)[1].split(
        "## Schema 3 supervision", 1
    )[0]
    exit_flow = strategy.split("## Controller exit and bot archive", 1)[1]

    assert (
        "an exact live config that contains the intended update while telemetry still "
        "shows the previous `formation.next` is expected propagation lag: remain "
        "`FORMATION_UPDATE_PENDING`, do not resubmit, and do not quarantine when that is "
        "the sole schema mismatch."
    ) in tick
    assert (
        "An exact live config containing the intended update while telemetry still shows "
        "the previous `formation.next` is propagation lag and remains pending when it "
        "exactly matches `previous_position_formations` and is the sole mismatch."
    ) in states
    assert (
        "Identity-consistent controller `EXITING` or `EXITED` overrides deploy/formation pending work."
        in strategy
    )
    assert "During close supervision, preserve an unresolved formation update." in exit_flow
    assert "Before terminal proof, record an applied update as `confirmed`." in exit_flow
    assert (
        "At first terminal proof, retain `confirmed` or `confirmed_terminal_no_effect` "
        "in the `EXITED_PENDING_ARCHIVE` decision. Replace it with the archive intent on "
        "the later archive tick."
        in exit_flow
    )
    assert "A formation mismatch never relaxes archive evidence requirements." in exit_flow

    fields = {
        "market_trend",
        "position_width_pct",
        "downside_offset_pct",
        "rebalance_threshold_pct",
    }
    previous = {
        "pool_exact": {
            "market_trend": "SIDEWAYS",
            "position_width_pct": "5",
            "downside_offset_pct": "0",
            "rebalance_threshold_pct": "0.5",
        }
    }
    intended = {
        "pool_exact": {
            "market_trend": "DOWN",
            "position_width_pct": "6",
            "downside_offset_pct": "0.5",
            "rebalance_threshold_pct": "0.75",
        }
    }
    assert set(previous) == set(intended)
    assert set(previous["pool_exact"]) == fields
    assert set(intended["pool_exact"]) == fields
    assert previous != intended
    assert "Only a durable framework receipt proving `rejected_before_submit`" in states
    assert "Replace it with the archive intent" in exit_flow


def test_agent_exit_intent_preserves_original_reasoning_and_journals_exiting():
    strategy = _prose(STRATEGY_PATH)
    original = (
        "corroborated price dislocation and venue warning affect three of four pools"
    )
    evidence = [
        {
            "category": "liquidity_impairment",
            "position_ids": ["pool_a", "pool_b", "pool_c"],
            "shared_dependency": None,
            "observed_at": "2026-08-30T12:00:00Z",
            "measurements": [
                {
                    "source": "scan_orca_pools",
                    "field": "table_data[0].tvl_usd",
                    "value": 9000,
                }
            ],
        },
        {
            "category": "price_dislocation",
            "position_ids": ["pool_a", "pool_b", "pool_c"],
            "shared_dependency": None,
            "observed_at": "2026-08-30T12:00:00Z",
            "measurements": [
                {
                    "source": "read_trend_aware_lp_session",
                    "field": "custom_info.positions[0].upper_price",
                    "value": 151.25,
                },
                {
                    "source": "scan_orca_pools",
                    "field": "table_data[0].price_quote",
                    "value": 149.75,
                },
            ],
        },
    ]
    record = {
        "state": "EXITING",
        "decision": "AGENT_EXIT",
        "reason": original,
        "pending_operation": {
            "kind": "agent_exit",
            "committed_values": {
                "config_name": "generation",
                "exit_requested": True,
                "exit_reason": "operator",
                "original_exit_reasoning": original,
                "adverse_evidence": evidence,
            },
        },
    }
    later = {**record, "reason": "controller acknowledged exit and is closing"}
    assert (
        later["pending_operation"]["committed_values"]["original_exit_reasoning"]
        == original
    )
    assert later["reason"] != original
    committed = later["pending_operation"]["committed_values"]
    assert list(committed) == [
        "config_name",
        "exit_requested",
        "exit_reason",
        "original_exit_reasoning",
        "adverse_evidence",
    ]
    assert committed["adverse_evidence"]
    assert [row["category"] for row in evidence] == sorted(
        row["category"] for row in evidence
    )
    for row in evidence:
        assert list(row) == [
            "category",
            "position_ids",
            "shared_dependency",
            "observed_at",
            "measurements",
        ]
        assert row["category"] in {
            "price_dislocation",
            "liquidity_impairment",
            "execution_impairment",
            "venue_integrity",
        }
        assert row["position_ids"] == sorted(set(row["position_ids"]))
        assert row["measurements"] == sorted(
            row["measurements"], key=lambda item: (item["source"], item["field"])
        )
        assert all(
            list(measurement) == ["source", "field", "value"]
            and measurement["source"]
            in {"scan_orca_pools", "read_trend_aware_lp_session"}
            for measurement in row["measurements"]
        )
    assert json.loads(json.dumps(evidence, separators=(",", ":"))) == evidence
    assert "`original_exit_reasoning` is copied once" in strategy
    assert "remains immutable" in strategy
    assert "journal `state: EXITING`, `decision: AGENT_EXIT`" in strategy
    assert "non-empty for an adverse-event exit" in strategy
    assert "empty only for a proven explicit live human exit" in strategy
    assert "never prose, reports, logs, or secrets" in strategy


def test_archive_confirmation_releases_identity_without_losing_terminal_pnl():
    fields = list(_journal_example())
    generation = f"{BOT_NAMESPACE}_s12_20260830T120000Z"
    runtime = f"{BOT_NAMESPACE}-20260830-120001"
    record = dict.fromkeys(fields)
    record.update(
        {
            "state": "VACANT",
            "decision": "ARCHIVE_CONFIRMED",
            "reason": "exact archived run and active-bot absence confirmed",
            "released_session": {
                "generation": generation,
                "config_name": generation,
                "runtime_instance": runtime,
            },
            "terminal_pnl_status": "unavailable",
            "terminal_pnl": {
                "global": {"pnl_quote": None, "pnl_ratio": None},
                "positions": {"pool_exact": {"pnl_quote": None, "pnl_ratio": None}},
            },
            "pending_operation": {
                "intent_id": None,
                "kind": "none",
                "target": None,
                "committed_values": None,
                "external_receipt": None,
                "outcome": None,
            },
        }
    )

    assert record["generation"] is None
    assert record["config_name"] is None
    assert record["runtime_instance"] is None
    assert record["released_session"] == {
        "generation": generation,
        "config_name": generation,
        "runtime_instance": runtime,
    }
    assert record["terminal_pnl"] == {
        "global": {"pnl_quote": None, "pnl_ratio": None},
        "positions": {"pool_exact": {"pnl_quote": None, "pnl_ratio": None}},
    }

    exit_flow = _prose(STRATEGY_PATH).split("## Controller exit and bot archive", 1)[1]
    assert (
        "If no same-tick admission action is produced, journal `state: VACANT`, "
        "`decision: ARCHIVE_CONFIRMED`, null active "
        "identity and pending fields, and the exact old identity in `released_session`."
    ) in exit_flow
    assert (
        "immediately continue through complete `VACANT` admission." in exit_flow
    )
    assert "Do not start admission in this reconciliation tick." not in exit_flow


def test_adverse_exit_requires_corroboration_and_session_wide_effect():
    exit_flow = _prose(STRATEGY_PATH).split("## Controller exit and bot archive", 1)[1]
    assert (
        "Require either one explicit venue-integrity warning or at least two independent "
        "current adverse observations, and require the evidence to affect a majority of "
        "configured positions or a shared venue/session dependency."
    ) in exit_flow
    assert (
        "One uncorroborated metric, routine trend change, one pool failing a new-session "
        "gate, ordinary formation change, or vague concern is insufficient."
    ) in exit_flow

    categories_section = exit_flow.split(
        "Independent observations mean different evidence categories", 1
    )[1].split("An explicit human exit", 1)[0]
    categories = set(re.findall(r"- ([a-z ]+):", categories_section))
    assert categories == {
        "price dislocation",
        "current liquidity impairment",
        "execution impairment",
        "venue integrity",
    }
    assert "do not claim historical TVL deterioration" in categories_section
    assert "Do not count two price windows as two observations." in exit_flow
    assert "one pool warning is not automatically venue-wide" in exit_flow

    def qualifies(*, evidence_categories, affected, total, shared_dependency=False):
        corroborated = (
            "venue integrity" in evidence_categories or len(evidence_categories) >= 2
        )
        session_wide = affected > total / 2 or shared_dependency
        return corroborated and session_wide

    assert not qualifies(evidence_categories={"price dislocation"}, affected=3, total=4)
    assert not qualifies(
        evidence_categories={"execution impairment", "price dislocation"},
        affected=1,
        total=4,
    )
    assert qualifies(
        evidence_categories={"venue integrity"},
        affected=0,
        total=4,
        shared_dependency=True,
    )
    assert qualifies(
        evidence_categories={"price dislocation", "current liquidity impairment"},
        affected=3,
        total=4,
    )
    # Multiple time windows are one price-dislocation category, not corroboration.
    assert not qualifies(evidence_categories={"price dislocation"}, affected=4, total=4)


def test_schema_three_exit_and_archive_evidence_are_exact():
    strategy = _prose(STRATEGY_PATH)
    for phrase in (
        "0 <= observed_at - reported_at <= 30 seconds",
        "schema_version == 3",
        "`CONFLICT` ownership always quarantines",
        "exit_requested: true",
        "exit_reason: operator",
        "top-level lifecycle `EXITED`",
        "every configured position lifecycle `EXITED`",
        "every position ownership state `ABSENT`",
        "terminal_pnl_status: available",
        "terminal_pnl_status: unavailable",
        "deployment_status: ARCHIVED",
        "plus absence of the exact active runtime bot",
        "immediately continue through complete `VACANT` admission",
    ):
        assert phrase.casefold() in strategy.casefold()


def test_live_exit_paths_do_not_reinterpret_startup_context():
    prose = (_prose(AGENT_PATH) + " " + _prose(STRATEGY_PATH)).casefold()
    for phrase in (
        "startup `trading_context` is frozen preference input, not a live instruction channel",
        "never reread or reinterpret it as a post-start command",
        "the controller's triple barriers are the primary exit path",
        "`exit_requested: true` and `exit_reason: operator`",
    ):
        assert phrase.casefold() in prose


def test_live_loop_admission_checks_are_explicit():
    prose = (_prose(AGENT_PATH) + " " + _prose(STRATEGY_PATH)).casefold()
    for phrase in (
        "dry run and run once are observation-and-proposal only",
        "valid prompt-visible config and an `active` risk state",
        "no namespace or account conflict",
        "refreshed canonical-usdc and sol funding",
        "pinned v2 controller is available",
        "at least `min_positions` eligible pools",
        "missing evidence causes `hold`",
        "contradictory ownership or identity causes `quarantined`",
        "neither condition authorizes a blind retry",
    ):
        assert phrase in prose


def test_controller_identity_and_exact_readback_gate_live_deployment():
    strategy = STRATEGY_PATH.read_text()
    discovery = " ".join(
        strategy.split("## Controller config and naming", 1)[1]
        .split("## Exact mutation calls", 1)[0]
        .split()
    )

    assert "confirm the expected V2 controller identity" in discovery
    assert "Build the exact pinned key set and defaults documented below" in discovery
    assert "require an exact saved-config readback before deployment" in discovery
    assert "readback mismatch blocks deployment" in discovery


def test_config_name_and_saved_readback_follow_the_specified_exact_rules():
    strategy = STRATEGY_PATH.read_text()
    config_section = " ".join(
        strategy.split("## Controller config and naming", 1)[1]
        .split("## Lifecycle derivation", 1)[0]
        .split()
    )

    assert (
        "checked-out HAPI safe-name rule `^[A-Za-z0-9_-]+$` and keep the generated "
        "filename, including `.yml`, within 255 UTF-8 bytes"
    ) in config_section
    assert (
        "Require exactly one complete `Config '<generation>' Details:` block"
        in config_section
    )
    assert "compare every serialized field" in config_section
    assert (
        "missing, duplicated, truncated, unparseable, or mismatched block"
        in config_section
    )


def test_authoritative_recent_decisions_require_one_canonical_action_line():
    strategy = " ".join(STRATEGY_PATH.read_text().split())

    assert (
        "Every loop action entry, including `HOLD`, passes one valid compact JSON object"
        in strategy
    )
    assert "It must occupy one physical line with no literal newline" in strategy


def test_generic_learning_and_canvas_prompt_conflict_remains_explicitly_blocked():
    prompt = _prompt("loop")
    generic_learning = 'trading_agent_journal_write(entry_type="learning"'
    generic_canvas = 'trading_agent_journal_write(entry_type="canvas"'
    strategy_denial = "Never write learning or canvas entries."

    assert generic_learning in prompt
    assert generic_canvas in prompt
    assert strategy_denial in prompt
    assert prompt.index(generic_learning) < prompt.rindex(strategy_denial)
    assert prompt.index(generic_canvas) < prompt.rindex(strategy_denial)
    assert prompt.rindex(strategy_denial) < prompt.rindex("[SESSION CANVAS")
    assert "Loop mode may mutate after the live admission checks pass" in prompt


def test_worst_case_prompt_includes_routines_and_three_complete_pending_entries():
    payload = json.dumps(_pending_record(), separators=(",", ":"), sort_keys=True)
    recent = "\n".join(f"- **#{tick}** (12:00) {payload}" for tick in range(8, 11))
    prompt = _prompt(
        "loop",
        recent_decisions=recent,
        summary="CONFIG_PENDING: exact config readback is temporarily unavailable",
    )

    assert prompt.count(payload) == 3
    assert "ROUTINES — executable analysis scripts:" in prompt
    assert "scan_orca_pools" in prompt
    assert "read_trend_aware_lp_session" in prompt
    # Keep CI independent of the tokenizer's separately downloaded encoding table by
    # enforcing a conservative character ceiling.
    assert len(prompt) < 72_000


def test_new_agent_sources_do_not_reference_the_historical_agent():
    for path in (
        AGENT_PATH,
        STRATEGY_PATH,
        EXAMPLE_PATH,
        CONFIG_PATH,
        LEARNINGS_PATH,
    ):
        assert "multi_lp_rebalancer_manager" not in path.read_text()
