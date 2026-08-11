from pathlib import Path

import yaml

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
    assert "run `Select` every tick" in strategy
    assert "A healthy existing LP is not by itself a reason to `HOLD`" in strategy_prose
    assert "sole hard current-session executor ceiling" in agent_prose


def test_same_pool_position_policy_is_configurable_and_preserves_retry_safety():
    strategy = STRATEGY_PATH.read_text()
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    defaults = _strategy_frontmatter()["default_config"]
    example = yaml.safe_load(EXAMPLE_CONFIG_PATH.read_text())

    assert defaults["allow_multiple_lp_positions_per_pool"] is True
    assert (
        example["allow_multiple_lp_positions_per_pool"]
        == defaults["allow_multiple_lp_positions_per_pool"]
    )
    assert "boolean `allow_multiple_lp_positions_per_pool`" in strategy
    assert "When `false`, an exact active, submitted, uncertain" in strategy_prose
    assert "When `true`, a healthy exact active LP" in strategy_prose
    assert "Same-pool permission never authorizes a retry" in agent_prose


def test_metrics_contract_maps_native_state_without_schema_retry():
    strategy = STRATEGY_PATH.read_text()
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)

    assert '`RUNNING` plus `IN_RANGE`, to compact `state="active"`' in strategy_prose
    assert "do not make a second metrics attempt" in strategy_prose
    assert "Native fields such as `status`, `range_state`" in strategy_prose
    assert '`RUNNING`/`IN_RANGE` is `state="active"`' in agent_prose
    assert "the routine has no `position_mint` input" in agent_prose
    assert (
        "never suppresses an independently verified native risk-reducing exit"
        in strategy_prose
    )
    assert "Pass `residuals` as a list of exact objects" in strategy_prose
    assert "exact Solana-address `mint`" in strategy_prose
    assert "Every residual `mint` must be an exact Solana address" in agent_prose
    assert "status` in `prepared|clean|cleanup|unattributed`" in strategy_prose
    assert "Pass optional `last` as exactly `kind`, `identity`" in strategy_prose


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
    assert "prevents a dust top-up" in agent_prose


def test_failed_open_retries_from_unchanged_balances_without_orca_inspection():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)

    for prose in (strategy_prose, agent_prose, operations_prose):
        assert "terminal `FAILED`" in prose
        assert "no position address" in prose
        assert "zero native actual LP base/quote amounts" in prose
        assert "one exact executor-detail read and one fresh wallet read" in prose
        assert "`rejected_before_submit`" in prose
        for field in (
            "`base_mint`",
            "`pre_base_balance`",
            "`quote_mint`",
            "`pre_quote_balance`",
        ):
            assert field in prose
        assert "same reconciliation tick" in prose
        assert "separate `HOLD` tick" in prose
        assert "at least one" in prose.lower()
        assert "no arbitrary percentage reduction is required" in prose
        assert "initial-amount fallback fields" in prose
        assert "four-field" in prose or "four journal fields" in prose
    assert "Do not call `inspect_orca_positions` for an LP open" in strategy_prose
    assert "Do not call `inspect_orca_positions` for an LP open" in operations_prose
    assert "Do not call `inspect_orca_positions` for an LP open" in agent_prose
    assert "planned LP deposit amounts are not baseline substitutes" in strategy_prose
    assert "planned LP deposit amounts are not baseline substitutes" in operations_prose
    assert "Metrics never supply or repair this proof" in strategy_prose
    assert "metrics are never a baseline substitute" in operations_prose
    assert "do not call orca stats" in strategy_prose.lower()
    assert "do not call orca stats" in operations_prose.lower()
    assert "Do not promise exact-position recovery" in agent_prose
    for prose in (strategy_prose, agent_prose, operations_prose):
        assert "all six exact journal fields" not in prose
        assert "journaled `base_decimals`" not in prose
        assert "those six journal fields" not in prose


def test_failed_close_uses_exact_position_inspection_before_retry():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)
    operations_prose = _prose(OPERATIONS_SKILL_PATH)

    for prose in (strategy_prose, agent_prose, operations_prose):
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
    assert '`expected_action_type="close_position"`' not in strategy_prose
    assert '`expected_action_type="close_position"`' not in agent_prose
    assert '`expected_action_type="close_position"`' not in operations_prose


def test_journal_contract_requires_exact_agent_and_tick_before_mutation():
    strategy_prose = _prose(STRATEGY_PATH)
    agent_prose = _prose(AGENT_PATH)

    for prose in (strategy_prose, agent_prose):
        assert "agent_id=<exact injected Agent ID>" in prose
        assert 'entry_type="action"' in prose
        assert "tick=<exact injected tick>" in prose
        assert "Require `written=true` before" in prose
    assert "never create a second successful action entry" in strategy_prose


def test_prepare_uses_order_executor_exclusively_and_realized_received_balance():
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
    assert "never call `manage_gateway_swaps` for any action" in operations_prose
    assert "order_executor" in strategy
    assert "exclusive preparation swap path" in strategy_prose
    assert "exclusive cleanup swap path" in strategy_prose
    assert "`order_executor` exclusively" in operations_prose
    assert "max_slippage_pct" not in defaults
    assert "max_slippage_pct" not in example
    assert "max_slippage_pct" not in local
    assert (
        "realized received BASE derived from settled token changes" in operations_prose
    )
    assert '`explore_dex_pools(action="get_pool_info")`' in operations_prose
    assert "finite positive current price" in strategy_prose
    assert "downsize to a meaningful feasible LP" in operations_prose
    assert "never submit a dust top-up swap" in strategy_prose


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
    assert "abs(executed_amount_base - requested_base_amount)" in strategy_prose
    assert "Strictly above it" in operations_prose
    assert "Equality never blacklists" in operations_prose
    assert (
        "requested BASE shortfall, and configured receive threshold" in operations_prose
    )
    assert "BLACKLIST_POOL=<pool> BLACKLIST_TOKEN=<base_mint>" in agent_prose
    assert "Exclude the exact pool and every candidate" in selection_prose
    assert "## Execution Notes" in learnings
