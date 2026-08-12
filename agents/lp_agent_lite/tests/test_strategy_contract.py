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

AGENT_ROOT = Path(__file__).resolve().parents[1]
STRATEGY_PATH = AGENT_ROOT / "strategies" / "orca" / "strategy.md"
EXAMPLE_CONFIG_PATH = AGENT_ROOT / "strategies" / "orca" / "config.example.yml"
LOCAL_CONFIG_PATH = AGENT_ROOT / "strategies" / "orca" / "config.yml"
LEARNINGS_PATH = AGENT_ROOT / "strategies" / "orca" / "learnings.md"
AGENT_PATH = AGENT_ROOT / "AGENT.md"
OPERATIONS_SKILL_PATH = AGENT_ROOT / "skills" / "orca_lp_operations" / "SKILL.md"
SELECTION_SKILL_PATH = AGENT_ROOT / "skills" / "orca_pool_selection" / "SKILL.md"


def _prose(path: Path) -> str:
    return " ".join(path.read_text().split())


def _strategy_frontmatter() -> dict:
    text = STRATEGY_PATH.read_text()
    _, raw, _ = text.split("---", 2)
    return yaml.safe_load(raw)


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
        "scan_orca_pools": (
            SELECTION_SKILL_PATH,
            "### Top-Level Config Parameters",
        ),
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
        before_anchor = path.read_text().split(anchor, 1)[0].rstrip()
        assert before_anchor.endswith(title), marker


def test_skill_routine_guides_define_call_and_result_handling():
    selection = _prose(SELECTION_SKILL_PATH)
    operations = _prose(OPERATIONS_SKILL_PATH)

    assert 'name="scan_orca_pools"' in selection
    assert 'agent="lp_agent_lite"' in selection
    for routine_name in (
        "calculate_lp_requirements",
        "snapshot_lp_metrics",
        "inspect_orca_positions",
        "register_gateway_token",
    ):
        assert f"### `{routine_name}`" in OPERATIONS_SKILL_PATH.read_text()
    for prose in (selection, operations):
        assert "Config" in prose and "exact" in prose
        assert "inner" in prose
        assert "`Invalid config:`" in prose


def test_skill_routine_guides_list_every_exact_enum_literal():
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
        literals = get_args(model.model_fields[field_name].annotation)
        assert literals
        for literal in literals:
            assert f"`{literal}`" in description, (marker, field_name, literal)


def test_target_position_objective_uses_executor_risk_ceiling():
    strategy = STRATEGY_PATH.read_text()
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)
    defaults = _strategy_frontmatter()["default_config"]
    example = yaml.safe_load(EXAMPLE_CONFIG_PATH.read_text())

    assert (
        0
        < defaults["target_active_lp_positions"]
        <= defaults["risk_limits"]["max_open_executors"]
    )
    assert (
        example["target_active_lp_positions"] == defaults["target_active_lp_positions"]
    )
    assert (
        example["risk_limits"]["max_open_executors"]
        == defaults["risk_limits"]["max_open_executors"]
    )
    assert "max_active_lp_positions" not in defaults
    assert "max_active_lp_positions" not in example
    assert "max_active_lp_positions" not in strategy
    assert "max_active_lp_positions" not in agent_prose
    assert "max_active_lp_positions" not in operations_prose
    assert (
        "run `Select` only when no committed deployment chain exists" in strategy_prose
    )
    assert "A healthy existing LP is not by itself a reason to `HOLD`" in strategy_prose
    assert "sole hard current-session executor ceiling" in agent_prose


def test_same_pool_policy_blocks_only_active_or_possibly_landed_lp():
    strategy = STRATEGY_PATH.read_text()
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    selection_prose = _prose(SELECTION_SKILL_PATH)
    defaults = _strategy_frontmatter()["default_config"]
    example = yaml.safe_load(EXAMPLE_CONFIG_PATH.read_text())
    local = yaml.safe_load(LOCAL_CONFIG_PATH.read_text())

    assert "allow_multiple_lp_positions_per_pool" not in defaults
    assert "allow_multiple_lp_positions_per_pool" not in example
    assert "allow_multiple_lp_positions_per_pool" not in local
    assert "allow_multiple_lp_positions_per_pool" not in strategy
    for prose in (strategy_prose, agent_prose, selection_prose):
        assert "At most one active or possibly landed LP per exact pool" in prose
        assert "no config waiver" in prose
        assert "becomes eligible again" in prose
        assert "terminal close" in prose
        assert "cleanup are resolved" in prose
        assert "`rejected_before_submit`" in prose
    assert "Pool exclusion never authorizes retry" in agent_prose


def test_post_close_cleanup_uses_full_non_sol_balance_and_bounded_sol_excess():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)

    for prose in (strategy_prose, agent_prose):
        assert "operator-authorized exception to inventory attribution" in prose
        assert "pre-existing same-mint wallet inventory" in prose
        assert "min_sol_reserve * (1 + capital_headroom_pct / 100)" in prose
        assert "total_amount_quote" in prose
        assert "QUOTE-per-SOL" in prose
    assert "entire refreshed available balance" in strategy_prose
    assert "sell the entire refreshed balance" in operations_prose
    assert "pre-existing same-mint wallet inventory" in operations_prose
    assert "Never apply full-balance normalization" in strategy_prose
    assert "normally retain SOL" in operations_prose


def test_metrics_contract_maps_native_state_without_schema_retry():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)

    for alias in (
        "`RUNNING`",
        "`IN_RANGE`",
        "`BELOW_RANGE`",
        "`ABOVE_RANGE`",
        "`SHUTTING_DOWN`",
        "`TERMINATED`",
    ):
        assert alias in operations_prose
    assert "case-insensitive and ignores spaces, hyphens" in operations_prose
    assert "Unknown states reject" in operations_prose
    assert "Use `position_address`, never `position_mint`" in operations_prose
    assert "Residual `mint` is an address, not a symbol" in operations_prose
    assert "latest lifecycle mutation" in operations_prose
    assert '`kind="prepare"`, never `"preparation"`' in operations_prose
    assert "not valid `last.kind` values" in operations_prose
    assert "Omit `last` when no exact mutation is known" in operations_prose
    assert "classified mutation outcome" in operations_prose
    assert "not a raw executor lifecycle status" in operations_prose
    assert "one compact pre-decision snapshot" in agent_prose
    assert "never an independently proven native risk-reducing exit" in operations_prose
    for prose in (strategy_prose, agent_prose):
        assert "`Invalid config:`" in prose
        assert "routine instance" in prose
    for prose in (strategy_prose, agent_prose):
        assert "exact declared field names and enum literals" in prose
        assert "never paraphrase, pluralize, change case" in prose
        assert "any completed" in prose.lower()


def test_lp_open_balance_buffer_is_configurable_and_used_in_two_phases():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)
    defaults = _strategy_frontmatter()["default_config"]
    example = yaml.safe_load(EXAMPLE_CONFIG_PATH.read_text())
    local = yaml.safe_load(LOCAL_CONFIG_PATH.read_text())

    key = "lp_open_balance_buffer_pct"
    assert defaults[key] == 2
    assert example[key] == defaults[key]
    assert local[key] == defaults[key]
    assert f"0 <= {key} <= capital_headroom_pct" in strategy_prose
    assert "reserves each LP leg against the provider's maximum debit" in strategy_prose
    assert "`allow_base_preparation=true`" in operations_prose
    assert "`allow_base_preparation=false`" in operations_prose
    for field in ("`available_base_display`", "`available_quote_display`"):
        assert field in strategy_prose
        assert field in operations_prose
    assert "safe wallet bounds" in operations_prose
    assert 'Exact `"0"` when BASE is absent' in operations_prose
    assert "native four-decimal string" in operations_prose
    assert "uppercase `K` or `M`" in operations_prose
    assert "never reconstruct them in prose" in operations_prose
    for prose in (strategy_prose, agent_prose, operations_prose):
        assert "quantum `0`" not in prose
        assert "exact attributable current-controller executor receipt" not in prose


def test_failed_open_retries_from_unchanged_balances_without_orca_inspection():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)

    for text in (
        "terminal `FAILED`",
        "no position address",
        "zero native actual LP base/quote amounts",
        "one exact executor-detail read and one fresh wallet read",
        "same reconciliation tick",
        "separate `HOLD` tick",
        "no arbitrary percentage reduction is required",
        "initial-amount fallback fields",
        "reported decimal scale",
        "exactly as reported",
        "do not normalize or demand mint precision",
        "least observable unit",
        "Planned amounts prove only observability",
        "this observability rule never blocks an initial OPEN",
    ):
        assert text in strategy_prose
    for field in (
        "`base_mint`",
        "`pre_base_balance`",
        "`quote_mint`",
        "`pre_quote_balance`",
    ):
        assert field in strategy_prose
    assert "Strategy's full `rejected_before_submit` evidence" in agent_prose
    assert "Only the Strategy's full `rejected_before_submit` proof" in operations_prose
    assert "Do not call `inspect_orca_positions` for an LP open" in strategy_prose
    assert "Do not call `inspect_orca_positions` for an LP open" in operations_prose
    assert "planned LP deposit amounts are not baseline substitutes" in strategy_prose
    assert "Metrics never supply or repair this proof" in strategy_prose
    assert "do not call orca stats" in strategy_prose.lower()
    for prose in (strategy_prose, agent_prose, operations_prose):
        assert "all six exact journal fields" not in prose
        assert "those six journal fields" not in prose
        assert "current native token precision" not in prose
        assert "freshly verified native precision" not in prose
        assert "normalizing both sides to each mint" not in prose
    assert "deposit-balance change at token precision" not in agent_prose
    assert "BASE/QUOTE balance change at token precision" not in agent_prose


def test_full_executor_id_is_identity_and_nested_controller_is_metadata():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)

    assert "full executor ID is the sole executor identity" in agent_prose
    assert "full `id` returned" in operations_prose
    for prose in (strategy_prose, agent_prose, operations_prose):
        assert "detail `id`" in prose
        assert "current-controller filtered search" in prose.lower()
        assert "earlier create response" in prose.lower()
        assert "`main`" in prose
        assert "non-authoritative" in prose
        assert "eight-character prefix" in prose.lower()
        assert "display-only" in prose
    assert "top-level mutation authority" in agent_prose
    assert "not the executor identifier" in strategy_prose
    assert "embedded raw/config `controller_id=main`" in strategy_prose
    assert "not a conflict" in strategy_prose
    assert "executor_id=<one full current-session LP ID>" in strategy_prose
    assert "executor_id=<one full executor ID>" in operations_prose
    assert "create receipt plus" not in strategy_prose.lower()
    assert "create receipt plus" not in agent_prose.lower()
    assert "discovery/capacity evidence only" not in operations_prose.lower()


def test_tick_evidence_reuses_broad_reads_and_targets_exact_detail():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)

    for prose in (strategy_prose, agent_prose):
        assert "one wallet portfolio read" in prose
        assert "one current-controller executor search" in prose
        assert "one exact current-session" in prose
        assert "one metrics snapshot" in prose
        assert "one journal write" in prose
        assert "raw `executors` rows" in prose
        assert "fan out exact-detail reads" in prose
        assert "lacks or contradicts" in prose
        assert "indicates an exit or lifecycle transition" in prose
        assert "proposed mutation target" in prose
        assert "affected by a mutation" in prose
        assert "visible injected CORE DATA" in prose
        assert "rounded" in prose
        assert "Never call it for a read-only `HOLD`" in prose
        assert "unresolved mutation or genuine shared-inventory conflict" in prose
        assert "quiet" in prose and "skill" in prose

    assert "canonical path" in strategy_prose
    assert "immediately before submit" in strategy_prose
    assert "refresh the affected executor and wallet surfaces" in agent_prose


def test_deployment_evidence_is_routine_first_and_never_uses_geckoterminal():
    agent = AGENT_PATH.read_text()
    agent_frontmatter = yaml.safe_load(agent.split("---", 2)[1])
    agent_prose = _prose(AGENT_PATH)
    strategy_prose = _prose(STRATEGY_PATH)
    selection_prose = _prose(SELECTION_SKILL_PATH)

    assert "explore_geckoterminal" not in agent_frontmatter["tools"]
    for prose in (agent_prose, strategy_prose, selection_prose):
        assert "Never call" in prose and "GeckoTerminal" in prose
        assert "external market-data fallback" in prose
    assert "routines are the first evidence surface" in agent_prose
    assert "Use the declared routines first" in strategy_prose
    assert "scan routine is the primary comparison surface" in selection_prose
    assert "native Orca pool detail is the verification surface" in selection_prose


def test_normal_routines_run_directly_from_always_loaded_contracts():
    agent_prose = _prose(AGENT_PATH)
    strategy = STRATEGY_PATH.read_text()
    strategy_prose = _prose(STRATEGY_PATH)

    assert "Never call routine `list` or `describe`" in agent_prose
    assert "Never use `manage_routines` `list` or `describe`" in strategy_prose
    assert "never read a skill merely to discover a Config" in strategy_prose

    models = (
        scan_orca_pools.Config,
        scan_orca_pools.McdaWeights,
        calculate_lp_requirements.Config,
        snapshot_lp_metrics.Config,
        snapshot_lp_metrics.PositionMetric,
        snapshot_lp_metrics.ResidualMetric,
        snapshot_lp_metrics.LastMutation,
        register_gateway_token.Config,
    )
    for model in models:
        for field_name in model.model_fields:
            assert f"`{field_name}`" in strategy, (model.__name__, field_name)


def test_registration_is_direct_once_per_new_committed_chain():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)

    for prose in (strategy_prose, agent_prose, operations_prose):
        assert "`preview=false`" in prose
        assert "uncertain" in prose
        assert "blindly repeated" in prose
    for prose in (strategy_prose, agent_prose):
        assert "`base_symbol`" in prose
        assert "`base_mint`" in prose
        assert "`base_decimals`" in prose
        assert "scanner" in prose
    assert "Do not preview or check whether it is already registered" in strategy_prose
    assert "repeated registration is allowed" in strategy_prose
    assert "registry-presence tool" in strategy_prose
    assert "A registration-only tick does not refresh pool detail" in strategy_prose
    assert "So11111111111111111111111111111111111111112" in strategy_prose
    assert "configured `quote_token_mint`" in strategy_prose
    assert "`next_phase=PREPARE`" in strategy_prose
    assert "`next_phase=REGISTER`" in strategy_prose
    assert "`preview=true`" in strategy_prose
    assert "read-only reconciliation" in strategy_prose
    assert (
        "add plus exact registry read-back is the registration reconciliation"
        in strategy_prose
    )
    assert "without `preview=true` or another registration call" in strategy_prose
    assert "final registration reconciliation" in agent_prose
    assert "without previewing or repeating registration" in operations_prose
    assert "no registration mutation is permitted" in strategy_prose
    for prose in (strategy_prose, agent_prose):
        assert "`registry=confirmed`" not in prose
        assert "`canonical_symbol`" in prose


def test_committed_open_uses_latency_bounded_fast_path():
    strategy = STRATEGY_PATH.read_text()
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations = OPERATIONS_SKILL_PATH.read_text()
    operations_prose = _prose(OPERATIONS_SKILL_PATH)

    for text in (
        "latency-bounded fast path",
        "at most one exact predecessor detail",
        "one canonical wallet read",
        "one intent write",
        "one liveness check",
        "one create",
        "Pure reads and calculations do not invalidate",
        "do not spend a separate tool call requesting the schema",
        "end the tick without more tool calls",
        "defers that read through the latency-bounded path",
        "refreshes those surfaces through the next tick's canonical path",
    ):
        assert text in strategy_prose

    assert "Read the same exact executor at most once per tick" in agent_prose
    assert "do not make a second precautionary wallet read" in strategy_prose
    assert "canonical current-tick wallet read used for sizing" in agent_prose
    assert "does not read a skill" in agent_prose
    assert "performs its own schema validation" in agent_prose
    assert "Do not read `orca_lp_operations`" in strategy_prose
    assert "make an intermediate wallet/executor refresh" in strategy_prose
    assert "successful LP create receipt ends the tick" in operations_prose
    assert "without reading this skill" in operations.split("---", 2)[1]

    for stale_instruction in (
        "immediately refresh native\n   wallet, executor",
        "read the current `lp_executor` schema",
        "Fetch with that full `executor_id`",
    ):
        assert stale_instruction not in strategy


def test_committed_deployment_chain_limits_strategic_phase_folding():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)
    selection_prose = _prose(SELECTION_SKILL_PATH)

    for prose in (strategy_prose, agent_prose):
        assert "committed deployment chain" in prose.casefold()
        assert "at most two adjacent lifecycle phases" in prose
        assert "at most one external mutation phase" in prose
        assert "Never fold three phases" in prose
        assert "`PREPARE` into `OPEN`" in prose
        assert "required exact reconciliation and end the tick" in prose
    assert "Fresh selection commits and ends without mutation" in operations_prose
    assert "Never fold `PREPARE` into `OPEN`" in operations_prose

    for prose in (strategy_prose, agent_prose, selection_prose):
        assert "exact pool" in prose
        assert "BASE mint" in prose
        assert "allocation" in prose
        assert "range thesis" in prose
        assert "next phase" in prose
        assert "without" in prose and "scan" in prose

    assert (
        "run `Select` only when no committed deployment chain exists" in strategy_prose
    )
    for prose in (strategy_prose, agent_prose, selection_prose):
        assert "Fresh selection" in prose
        assert "mutation" in prose
    assert "A registration mutation always ends its tick" in strategy_prose
    assert "preserve the committed pool with `next_phase=OPEN`" in strategy_prose
    assert "Never fold `PREPARE` into `OPEN`" in strategy_prose
    assert "fold its direct predecessor reconciliation into `OPEN`" in strategy_prose
    assert "do not run `scan_orca_pools`" in strategy_prose
    assert "repeat a confirmed registration" in strategy_prose
    for field in (
        "deployment_chain=committed",
        "allocation_quote",
        "range_thesis",
        "next_phase",
    ):
        assert field in strategy_prose
        assert field in agent_prose
    assert "defer another candidate" in strategy_prose
    assert "defer alternatives" in strategy_prose


def test_failed_close_uses_exact_position_inspection_before_retry():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)

    for prose in (strategy_prose, operations_prose):
        assert "failed or uncertain close" in prose
        assert "action selector" in prose
        assert '`close_outcome="closed"`' in prose or "`closed` forbids" in prose
        assert (
            '`close_outcome="still_active"`' in prose
            or "`still_active` permits" in prose
        )
        assert "`pending_index`" in prose
        assert "`uncertain`" in prose
        assert "`unavailable`" in prose
    assert "failed or uncertain close" in agent_prose
    assert "close-only `inspect_orca_positions`" in agent_prose
    assert '`expected_action_type="close_position"`' not in strategy_prose
    assert '`expected_action_type="close_position"`' not in agent_prose
    assert '`expected_action_type="close_position"`' not in operations_prose


def test_failed_close_quarantine_is_scoped_and_does_not_block_siblings():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)
    selection_prose = _prose(SELECTION_SKILL_PATH)

    for text in (
        "at most one corrected",
        "`404` / `executor not found`",
        "exact executor, position, pool, and attributable capital",
        "never retry that close again",
        "one full executor ID",
        "never concatenate",
        "independently proven sibling",
        "executable close batches",
        "occupies one `target_active_lp_positions` unit",
        "does not consume `risk_limits.max_open_executors`",
        "exact on-chain position is closed",
        "fresh wallet can fund a new LP",
        "Inventory attribution, QUOTE restoration, and cleanup completion",
        "ordinary balance-feasibility `HOLD`",
        "learning blacklist",
    ):
        assert text.casefold() in strategy_prose.casefold()

    assert "continue independently proven sibling positions" in agent_prose
    assert "quarantines only that executor, position" in operations_prose
    assert "healthy siblings and other available capacity continue" in operations_prose
    assert "wallet able to pass normal new-LP sizing" in operations_prose

    assert "does not block unrelated eligible work" in strategy_prose
    assert "quarantined pool remains excluded" in selection_prose
    assert "fresh wallet feasibility" in selection_prose


def test_journal_contract_requires_exact_agent_and_tick_before_mutation():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)

    for prose in (strategy_prose, agent_prose):
        assert "agent_id=<exact injected Agent ID>" in prose
        assert 'entry_type="action"' in prose
        assert "tick=<exact injected tick>" in prose
        assert "Require `written=true` before" in prose
    assert "never create a second successful action entry" in strategy_prose


def test_prepare_uses_order_executor_and_post_swap_wallet_exclusively():
    strategy = STRATEGY_PATH.read_text()
    agent = AGENT_PATH.read_text()
    operations = OPERATIONS_SKILL_PATH.read_text()
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)
    defaults = _strategy_frontmatter()["default_config"]
    example = yaml.safe_load(EXAMPLE_CONFIG_PATH.read_text())
    local = yaml.safe_load(LOCAL_CONFIG_PATH.read_text())

    assert "- manage_gateway_swaps" not in agent
    assert "Never call `manage_gateway_swaps` for any action" in agent_prose
    assert "never call `manage_gateway_swaps`" in strategy_prose
    assert "exclusive swap path" in operations_prose
    assert "call `manage_gateway_swaps`" in operations_prose
    assert "order_executor" in strategy
    assert "exclusive preparation swap path" in strategy_prose
    assert "exclusive cleanup swap path" in strategy_prose
    assert "Preparation and cleanup Order Executor" in operations_prose
    assert "max_slippage_pct" not in defaults
    assert "max_slippage_pct" not in example
    assert "max_slippage_pct" not in local
    assert "repeats the requested BASE amount" in operations_prose
    assert "not received-inventory evidence" in operations_prose
    assert "never enters receipt-difference or blacklist math" in strategy_prose
    assert (
        "Refresh the wallet and preserve the exact post-swap BASE display"
        in strategy_prose
    )
    assert "requested-amount meaning of `executed_amount_base`" in strategy_prose
    assert (
        '`explore_dex_pools(action="get_pool_info", connector="orca"' in strategy_prose
    )
    assert "finite positive current price" in strategy_prose
    assert "Small receive differences downsize the LP" in operations_prose
    assert "never submit a dust top-up swap" in strategy_prose


def test_committed_prepare_uses_latency_bounded_fast_path():
    strategy = STRATEGY_PATH.read_text()
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations = OPERATIONS_SKILL_PATH.read_text()
    operations_prose = _prose(OPERATIONS_SKILL_PATH)

    for text in (
        "latency-bounded fast path",
        "one selected-pool refresh already used for sizing",
        "one calculation",
        "one intent write",
        "one liveness check",
        "one create",
        "Do not read `orca_lp_operations`",
        "separate `order_executor` schema-only call",
        "perform post-create reconciliation in this tick",
        "native create path performs its own live schema validation",
        "end the tick without more tool calls",
        "successful create receipt establishes `submitted`",
    ):
        assert text in strategy_prose

    assert "Ordinary committed `PREPARE` and LP `OPEN`" in agent_prose
    assert "Ordinary PREPARE, OPEN" in operations.split("---", 2)[1]
    assert "Do not load this skill" in operations_prose
    assert "the canonical controller search and wallet read" in operations_prose
    assert "read the current `order_executor` schema" not in strategy


def test_lp_pnl_grace_is_configurable_and_only_suppresses_pnl_exits():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)
    defaults = _strategy_frontmatter()["default_config"]
    example = yaml.safe_load(EXAMPLE_CONFIG_PATH.read_text())
    local = yaml.safe_load(LOCAL_CONFIG_PATH.read_text())

    key = "lp_pnl_grace_period_minutes"
    assert defaults[key] == 5
    assert example[key] == defaults[key]
    assert local[key] == defaults[key]
    assert f"{key} >= 0" in strategy_prose
    for prose in (strategy_prose, agent_prose, operations_prose):
        assert key in prose
        assert "provisional" in prose
        assert "Zero disables" in prose or "zero disables" in prose
        assert "fresh PnL evidence" in prose
    assert "authoritative executor creation timestamp" in strategy_prose
    assert "otherwise eligible deployment remain active" in strategy_prose
    assert "whole-session aggregate PnL" in strategy_prose


def test_cleanup_submission_defers_receipt_reconciliation_to_next_tick():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)

    assert (
        "successful `PREPARE`, `OPEN`, and `CLEANUP` create receipts" in strategy_prose
    )
    assert "end the tick without an exact search or wallet refresh" in strategy_prose
    assert "successful create receipt is `submitted`" in strategy_prose
    assert "successful `PREPARE`, `OPEN`, and `CLEANUP` create receipts" in agent_prose
    assert "post-create search or wallet refresh" in operations_prose
    assert "next tick's canonical reads" in operations_prose


def test_gateway_case_alias_uses_canonical_executor_symbol():
    strategy_prose = _prose(STRATEGY_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)

    for prose in (strategy_prose, operations_prose):
        assert "canonical_symbol" in prose
    assert "differs only by case" in strategy_prose
    assert "case-only symbol alias" in operations_prose
    assert "advances this unchanged chain to sizing on the next tick" in strategy_prose


def test_receive_difference_blacklist_is_configurable_and_persistent():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)
    selection_prose = _prose(SELECTION_SKILL_PATH)
    defaults = _strategy_frontmatter()["default_config"]
    example = yaml.safe_load(EXAMPLE_CONFIG_PATH.read_text())
    local = yaml.safe_load(LOCAL_CONFIG_PATH.read_text())
    learnings = LEARNINGS_PATH.read_text()

    key = "preparation_receive_difference_blacklist_pct"
    assert defaults[key] == 5
    assert example[key] == defaults[key]
    assert local[key] == defaults[key]
    assert f"0 < {key} <= 100" in strategy_prose
    assert "observed_delta = post_value - pre_value" in strategy_prose
    assert "error_bound = pre_quantum + post_quantum" in strategy_prose
    assert "minimum_difference_pct" in strategy_prose
    assert "executed_amount_base - requested_base_amount" not in strategy_prose
    assert (
        "strictly above `preparation_receive_difference_blacklist_pct`"
        in operations_prose
    )
    assert (
        "Equality, ambiguity, or insufficient precision never blacklists"
        in operations_prose
    )
    assert "exact native `pre_base_balance_display`" in strategy_prose
    assert (
        "If the interval touches or crosses the configured threshold" in strategy_prose
    )
    assert "BLACKLIST_POOL=<pool> BLACKLIST_TOKEN=<base_mint>" in agent_prose
    assert "Exclude the exact pool and every candidate" in selection_prose
    assert "## Execution Notes" in learnings
