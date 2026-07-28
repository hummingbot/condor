from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from agents.lpmaxxing.routines import _orca_contracts as contracts
from agents.lpmaxxing.routines import _orca_evidence as evidence
from agents.lpmaxxing.routines import _orca_lifecycle as lifecycle
from agents.lpmaxxing.routines import _orca_policy as policy
from agents.lpmaxxing.routines import lp_close_audit, lp_position_report
from agents.lpmaxxing.routines import orca_live_preflight as preflight
from agents.lpmaxxing.routines import orca_pool_scan as scanner
from agents.lpmaxxing.routines import pre_lp_rebalance
from routines.base import RoutineResult

UTC = timezone.utc
CONTROLLER_ID = "test.orca.synthetic"


def _scan_candidate() -> dict:
    raw = {
        "address": "synthetic-pool",
        "tokenA": {"symbol": "SYN", "mint": "synthetic-base-mint", "decimals": 9},
        "tokenB": {
            "symbol": "USDC",
            "mint": scanner.CANONICAL_USDC_MINT,
            "decimals": 6,
        },
        "price": 100.0,
        "tvlUsdc": 1_000_000.0,
        "stats": {
            "1h": {"volume": 10_000.0, "fees": 25.0},
            "4h": {"volume": 40_000.0, "fees": 100.0},
            "24h": {"volume": 300_000.0, "fees": 500.0, "priceDelta": 0.005},
            "7d": {"volume": 2_000_000.0, "fees": 3_500.0},
        },
        "feeRate": 400.0,
        "adaptiveFeeEnabled": False,
        "feeTierIndex": 1,
        "tickSpacing": 1,
        "hasWarning": False,
        "_scan_categories": ["utility"],
        "_scan_lenses": ["volume24h"],
    }
    candidate = policy.normalize_candidate(raw)
    policy.derive(candidate, 10.0)
    assert (
        policy.gate(candidate, policy.PROFILE_POLICY["yield_focused"], True, set())
        is None
    )
    policy.score(candidate, policy.PROFILE_POLICY["yield_focused"])
    candidate["range_plan"] = policy.range_plan(candidate, "yield_focused")[0]
    return scanner._candidate_row(
        candidate, "2026-01-01T00:00:00+00:00", "yield_focused", 10.0
    )


def _write_session(tmp_path, *, frequency_sec: int = 60) -> None:
    (tmp_path / "config.yml").write_text(
        "\n".join(
            [
                "execution_mode: loop",
                "risk_profile: yield_focused",
                "total_amount_quote: 10",
                f"frequency_sec: {frequency_sec}",
                "trading_context: |",
                "  SESSION_MODE: loop",
                "  POSITION_MAX_AGE_MINUTES: 60",
                "  POSITION_TAKE_PROFIT_NET_PNL_RATIO: 0.1",
                "  POSITION_STOP_LOSS_NET_PNL_RATIO: 0.1",
                "  SESSION_MAX_AGE_MINUTES: 999",
                "  SESSION_TAKE_PROFIT_NET_PNL_RATIO: 0.9",
                "  SESSION_STOP_LOSS_NET_PNL_RATIO: 0.9",
                "",
            ]
        )
    )


def _session_state(*, started_at: datetime, completed_pnl: float = 0.0) -> dict:
    return {
        "session_started_at": started_at.isoformat(),
        "terms": {
            "total_amount_quote": 10.0,
            "session_max_age_minutes": 60.0,
            "session_take_profit_net_pnl_ratio": 0.1,
            "session_stop_loss_net_pnl_ratio": 0.1,
        },
        "completed": {"net_pnl_quote": completed_pnl},
    }


def _failed_before_open_executor(*, status: str = "TERMINATED") -> dict:
    return {
        "executor_id": "failed-open-executor",
        "controller_id": CONTROLLER_ID,
        "status": status,
        "close_type": "FAILED",
        "created_at": "2026-01-01T00:00:00+00:00",
        "closed_at": "2026-01-01T00:00:02+00:00",
        "filled_amount_quote": 0.0,
        "net_pnl_quote": 0.0,
        "net_pnl_pct": 0.0,
        "cum_fees_quote": 0.0,
        "is_active": False,
        "is_trading": False,
        "last_error": "Position creation failed: Gateway error [SIMULATION_FAILED]",
        "config": {
            "controller_id": CONTROLLER_ID,
            "pool_address": "synthetic-pool",
            "trading_pair": "SYN-USDC",
        },
        "custom_info": {
            "state": "FAILED",
            "position_address": None,
            "filled_amount_base": 0.0,
            "filled_amount_quote": 0.0,
            "initial_base_amount": 0.05,
            "initial_quote_amount": 5.0,
            "base_amount": 0.0,
            "quote_amount": 0.0,
            "total_value_quote": 0.0,
            "fees_earned_quote": 0.0,
            "tx_fee": 0.0,
            "position_rent": 0.0,
            "position_rent_refunded": 0.0,
            "held_position_orders": [],
        },
    }


def _failed_before_open_audit_config(*, status: str = "TERMINATED"):
    return lp_close_audit.Config(
        controller_id=CONTROLLER_ID,
        execution_mode="loop",
        preset="balanced",
        executor_plan={
            "executor_config": {
                "controller_id": CONTROLLER_ID,
                "pool_address": "synthetic-pool",
                "trading_pair": "SYN-USDC",
                "base_amount": 0.05,
                "quote_amount": 5.0,
            }
        },
        final_executor=_failed_before_open_executor(status=status),
    )


def test_live_profile_policy_is_monotonic() -> None:
    focused = scanner.PROFILE_POLICY["yield_focused"]
    high = scanner.PROFILE_POLICY["yield_high_risk"]
    extreme = scanner.PROFILE_POLICY["yield_extreme_risk"]
    no_limit = scanner.PROFILE_POLICY["yield_no_limit"]

    assert set(focused["categories"]).issubset(high["categories"])
    assert high["categories"] == extreme["categories"] == no_limit["categories"]
    for field in ("min_tvl_usd", "min_volume_24h_usd", "min_volume_7d_usd"):
        assert focused[field] >= high[field] == extreme[field] == no_limit[field]
    assert (
        focused["max_abs_net_price_change_24h"] < high["max_abs_net_price_change_24h"]
    )
    assert (
        high["max_abs_net_price_change_24h"] < extreme["max_abs_net_price_change_24h"]
    )
    assert no_limit["max_abs_net_price_change_24h"] is None


@pytest.mark.parametrize(
    ("change", "profile", "preset"),
    [
        (0.01, "yield_focused", "concentrated"),
        (0.02, "yield_focused", "balanced"),
        (0.04, "yield_high_risk", "defensive"),
    ],
)
def test_range_plan_has_deterministic_boundary_presets(
    change: float, profile: str, preset: str
) -> None:
    candidate = {
        "net_price_change_24h": change,
        "tick_spacing": 1,
        "sustained_fee_productivity": 0.001,
    }
    first, first_reason = scanner._range_plan(candidate, profile)
    second, second_reason = scanner._range_plan(candidate, profile)

    assert first_reason is second_reason is None
    assert first == second
    assert first["preset"] == preset
    assert first["uncapped_required_half_width"] == pytest.approx(change * 1.5)


def test_no_limit_range_caps_an_oversized_required_width() -> None:
    plan, reason = scanner._range_plan(
        {
            "net_price_change_24h": 0.64,
            "tick_spacing": 1,
            "sustained_fee_productivity": 0.001,
        },
        "yield_no_limit",
    )

    assert reason is None
    assert plan["preset"] == "extreme"
    assert plan["width_capped"] is True
    assert plan["maximum_executable_half_width"] == 0.95
    assert plan["provisional_half_width"] == 0.95


def test_scanner_candidate_passes_preflight_revalidation_unchanged() -> None:
    candidate = _scan_candidate()

    passed, evidence, normalized = preflight._candidate_revalidation(
        candidate, 10.0, "yield_focused"
    )

    assert passed is True, evidence
    assert evidence["errors"] == []
    assert normalized is not None
    assert normalized["range_plan"] == candidate["range_plan"]


def test_preflight_revalidation_rejects_tampered_scanner_fields() -> None:
    candidate = _scan_candidate()
    candidate["fees_24h_usd"] += 1

    passed, evidence, normalized = preflight._candidate_revalidation(
        candidate, 10.0, "yield_focused"
    )

    assert passed is False
    assert normalized is None
    assert (
        "candidate field 'fee_tvl_24h' does not match recomputed policy"
        in evidence["errors"]
    )


def test_schema_v2_state_initialization_and_pinned_term_mismatch(
    tmp_path, monkeypatch
) -> None:
    _write_session(tmp_path)
    monkeypatch.setattr(lifecycle, "_session_dir", lambda controller_id: tmp_path)

    state = lifecycle.ensure_session_state(CONTROLLER_ID)

    assert state["schema_version"] == 2
    assert state["controller_id"] == CONTROLLER_ID
    assert state["session_status"] == "running"
    assert state["active_position"] is None
    assert state["terms"]["frequency_sec"] == 60.0

    _write_session(tmp_path, frequency_sec=61)
    with pytest.raises(ValueError, match="terms differ"):
        lifecycle.ensure_session_state(CONTROLLER_ID)


def test_lifecycle_persistence_redacts_sensitive_fields(tmp_path, monkeypatch) -> None:
    _write_session(tmp_path)
    monkeypatch.setattr(lifecycle, "_session_dir", lambda controller_id: tmp_path)
    lifecycle.ensure_session_state(CONTROLLER_ID)

    lifecycle.save_lifecycle_state(
        CONTROLLER_ID,
        {
            "controller_id": CONTROLLER_ID,
            "phase": "preflight_ready",
            "gateway": {
                "walletAddress": "synthetic-wallet",
                "exchange_api_secret": "synthetic-secret",
                "pool_address": "synthetic-pool",
            },
        },
    )

    persisted = lifecycle.load_lifecycle_state(CONTROLLER_ID)
    assert persisted["gateway"]["walletAddress"] == "[redacted]"
    assert persisted["gateway"]["exchange_api_secret"] == "[redacted]"
    assert persisted["gateway"]["pool_address"] == "synthetic-pool"


@pytest.mark.parametrize(
    ("now_offset", "completed_pnl", "active_pnl", "reason"),
    [
        (timedelta(minutes=60), 0.0, 0.0, "session_max_age_reached"),
        (timedelta(minutes=1), 0.2, 0.8, "session_take_profit_reached"),
        (timedelta(minutes=1), -0.2, -0.8, "session_stop_loss_reached"),
    ],
)
def test_session_stop_trigger_covers_age_take_profit_and_stop_loss(
    now_offset: timedelta, completed_pnl: float, active_pnl: float, reason: str
) -> None:
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    trigger = lifecycle.session_stop_trigger(
        _session_state(started_at=started_at, completed_pnl=completed_pnl),
        started_at + now_offset,
        active_pnl,
    )

    assert trigger is not None
    assert trigger["reason"] == reason


def test_audit_commit_is_idempotent_and_completes_cycle(tmp_path, monkeypatch) -> None:
    _write_session(tmp_path)
    monkeypatch.setattr(lifecycle, "_session_dir", lambda controller_id: tmp_path)
    lifecycle.ensure_session_state(CONTROLLER_ID)
    lifecycle.save_lifecycle_state(
        CONTROLLER_ID,
        {
            "controller_id": CONTROLLER_ID,
            "phase": "terminal",
            "executor_id": "synthetic-executor",
        },
    )
    audit = {
        "identity": {
            "controller_id": CONTROLLER_ID,
            "executor_id": "synthetic-executor",
            "pool_address": "synthetic-pool",
            "trading_pair": "SYN-USDC",
            "preset": "balanced",
            "terminal_state": "COMPLETE",
        },
        "lifecycle": {"close_reason": "synthetic_close"},
        "deposits": {"planned_base": 1.0, "planned_quote": 5.0},
        "performance": {
            "filled_amount_quote": 10.0,
            "reconciled_pnl_ratio": 0.025,
            "net_pnl_after_rebalance_quote": 0.25,
        },
        "rebalance": {"status": "NOT_REQUIRED"},
        "source_evidence": {"authorization": "Bearer synthetic-secret"},
    }
    assert (
        lifecycle._audit_fingerprint(audit, 1, "synthetic-executor", 0.25)
        == "49128b9494b603f3eea7f89a9df1dc151d2cf1059af4b41ae20d665cde048817"
    )

    committed = lifecycle.commit_position_audit(CONTROLLER_ID, audit)
    repeated = lifecycle.commit_position_audit(CONTROLLER_ID, audit)

    assert committed["session_status"] == "cycle_complete"
    assert repeated["already_committed"] is True
    assert repeated["session_status"] == "cycle_complete"
    assert (tmp_path / "orca_positions" / "position_000001.json").is_file()
    archive = lifecycle.load_position_archive(CONTROLLER_ID, "synthetic-executor")
    assert archive["audit"]["source_evidence"]["authorization"] == "[redacted]"


def test_close_audit_classifies_failed_before_open_without_ratio() -> None:
    audit = lp_close_audit._evaluate(_failed_before_open_audit_config())

    assert audit["audit_status"] == "complete"
    assert audit["lifecycle"]["terminal_outcome"] == "failed_before_open"
    assert audit["lifecycle"]["position_opened"] is False
    assert audit["performance"]["reconciled_pnl_ratio"] is None
    assert audit["performance"]["pnl_reconciliation_status"] == "not_applicable_no_fill"
    assert audit["deposits"]["actual_base"] == 0.0
    assert audit["deposits"]["actual_quote"] == 0.0
    assert "reconciled_pnl_ratio" not in audit["missing_fields"]


def test_failed_before_open_does_not_require_diagnostic_error_fields() -> None:
    config = _failed_before_open_audit_config()
    config.final_executor.pop("last_error")
    config.final_executor["custom_info"].pop("held_position_orders")

    audit = lp_close_audit._evaluate(config)

    assert audit["audit_status"] == "complete"
    assert audit["lifecycle"]["terminal_outcome"] == "failed_before_open"


@pytest.mark.parametrize(
    ("status", "location", "field", "value"),
    [
        ("TERMINATED", "custom_info", "position_address", "position-exists"),
        ("FAILED", "executor", "position_address", "position-exists"),
        ("TERMINATED", "custom_info", "filled_amount_base", 0.01),
        ("TERMINATED", "custom_info", "filled_amount_quote", 1.0),
        ("TERMINATED", "custom_info", "base_amount", 0.01),
        ("TERMINATED", "executor", "base_amount", 0.01),
        ("TERMINATED", "custom_info", "quote_amount", 1.0),
        ("TERMINATED", "custom_info", "total_value_quote", 1.0),
        ("TERMINATED", "custom_info", "held_position_orders", ["order"]),
        ("TERMINATED", "executor", "held_position_orders", ["order"]),
        ("TERMINATED", "executor", "filled_amount_base", "unknown"),
        ("TERMINATED", "custom_info", "total_value_quote", float("nan")),
    ],
)
def test_close_audit_does_not_recover_contradictory_open_evidence(
    status: str, location: str, field: str, value
) -> None:
    config = _failed_before_open_audit_config(status=status)
    target = (
        config.final_executor["custom_info"]
        if location == "custom_info"
        else config.final_executor
    )
    target[field] = value

    audit = lp_close_audit._evaluate(config)

    assert audit["lifecycle"]["terminal_outcome"] == "failed_after_open_or_unknown"
    assert audit["audit_status"] == "partial"
    assert "failed_executor_not_proven_pre_open" in audit["missing_fields"]
    assert "reconciled_pnl_ratio" in audit["missing_fields"]


def test_close_audit_requires_explicit_null_position_evidence() -> None:
    config = _failed_before_open_audit_config()
    config.final_executor["custom_info"].pop("position_address")

    audit = lp_close_audit._evaluate(config)

    assert audit["lifecycle"]["terminal_outcome"] == "failed_after_open_or_unknown"
    assert "failed_executor_not_proven_pre_open" in audit["missing_fields"]


@pytest.mark.parametrize("status", ["TERMINATED", "FAILED"])
def test_failed_before_open_commit_advances_failed_executor_cycle(
    tmp_path, monkeypatch, status: str
) -> None:
    _write_session(tmp_path)
    monkeypatch.setattr(lifecycle, "_session_dir", lambda controller_id: tmp_path)

    async def skip_report_write(payload) -> None:
        return None

    monkeypatch.setattr(lp_close_audit, "_save_report", skip_report_write)
    lifecycle.ensure_session_state(CONTROLLER_ID)
    config = _failed_before_open_audit_config(status=status)
    lifecycle.save_lifecycle_state(
        CONTROLLER_ID,
        {
            "controller_id": CONTROLLER_ID,
            "phase": "terminal",
            "executor_id": "failed-open-executor",
            "executor_plan": config.executor_plan,
            "preset": "balanced",
        },
    )

    result = asyncio.run(lp_close_audit.run(config, None))
    state = lifecycle.load_session_state(CONTROLLER_ID)

    assert state["active_position"] is None
    assert state["session_status"] == "cycle_complete"
    assert state["manual_review"] is None
    assert state["completed"]["executor_ids"] == ["failed-open-executor"]
    assert (tmp_path / "orca_positions" / "position_000001.json").is_file()
    assert '"next_action": "wait-next-cycle"' in result.text


def test_position_report_requires_explicit_execution_mode() -> None:
    with pytest.raises(ValueError, match="execution_mode"):
        lp_position_report.Config(controller_id=CONTROLLER_ID)


def test_recursive_secret_redaction_is_preserved() -> None:
    value = {
        "walletAddress": "synthetic-wallet",
        "nested": [
            {
                "api_key": "synthetic-api-key",
                "secret": "synthetic-secret",
                "exchange_client_secret": "synthetic-client-secret",
                "private_key_hex": "synthetic-private-key",
                "secret_value": "synthetic-secret-value",
            }
        ],
        "message": (
            "request failed: authorization=Bearer synthetic-token "
            "url=https://user:password@example.com/path Basic Account"
        ),
        "serialized": '{"api_key":"serialized-secret"} '
        "Authorization: ApiKey id:authorization-secret",
        "safe": "visible",
    }
    sanitized = evidence.redact(value)
    assert sanitized["walletAddress"] == "[redacted]"
    assert sanitized["nested"][0]["api_key"] == "[redacted]"
    assert sanitized["nested"][0]["secret"] == "[redacted]"
    assert sanitized["nested"][0]["exchange_client_secret"] == "[redacted]"
    assert sanitized["nested"][0]["private_key_hex"] == "[redacted]"
    assert sanitized["nested"][0]["secret_value"] == "[redacted]"
    assert "synthetic-token" not in sanitized["message"]
    assert "user:password" not in sanitized["message"]
    assert "Basic Account" in sanitized["message"]
    assert "serialized-secret" not in sanitized["serialized"]
    assert "authorization-secret" not in sanitized["serialized"]
    assert "id:" not in sanitized["serialized"]
    assert sanitized["safe"] == "visible"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("orca_api_base_url", "http://api.orca.so/v2/solana"),
        ("orca_api_base_url", "https://api.orca.so.evil/v2/solana"),
        ("pool_endpoint", "/pools/../private"),
    ],
)
def test_scanner_rejects_untrusted_api_egress(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        scanner.Config(**{field: value})

    with pytest.raises(ValueError, match="trusted HTTPS pool endpoint"):
        scanner._validate_orca_request_url(
            "https://api.orca.so.evil/v2/solana/pools?size=1"
        )


def test_recursive_redaction_serializes_datetimes() -> None:
    observed_at = datetime(2026, 1, 1, tzinfo=UTC)

    assert evidence.redact({"nested": [observed_at]}) == {
        "nested": ["2026-01-01T00:00:00+00:00"]
    }


def test_controller_executor_evidence_returns_all_pages() -> None:
    class Executors:
        def __init__(self) -> None:
            self.requests = []

        async def search_executors(self, **params):
            self.requests.append(params)
            if len(self.requests) == 1:
                return {
                    "executors": [{"id": "one", "controller_id": CONTROLLER_ID}],
                    "next_cursor": "next",
                }
            return {"items": [{"id": "two", "controller_id": CONTROLLER_ID}]}

    executors = Executors()
    rows, pages = asyncio.run(
        evidence.search_controller_executors(
            type("Client", (), {"executors": executors})(),
            CONTROLLER_ID,
            capture_pages=True,
            strict=True,
        )
    )

    assert [evidence.executor_id(row) for row in rows] == ["one", "two"]
    assert len(pages) == 2
    assert executors.requests[1]["cursor"] == "next"


def test_controller_executor_evidence_rejects_repeated_cursor() -> None:
    class Executors:
        async def search_executors(self, **params):
            return {"executors": [], "next_cursor": "repeated"}

    with pytest.raises(ValueError, match="cursor repeated"):
        asyncio.run(
            evidence.search_controller_executors(
                type("Client", (), {"executors": Executors()})(),
                CONTROLLER_ID,
                strict=True,
            )
        )


def test_wallet_balance_is_mint_first_and_symbol_fallback_is_unambiguous() -> None:
    balances = [
        {"symbol": "SYN", "mint": "wrong-mint", "available": 50},
        {"symbol": "SYN", "mint": "right-mint", "available": 2},
    ]

    assert (
        evidence.balance_for_token(balances, {"symbol": "SYN", "mint": "right-mint"})
        == 2
    )
    assert (
        evidence.balance_for_token(balances, {"symbol": "SYN", "mint": "missing-mint"})
        is None
    )
    assert (
        evidence.balance_for_token(
            [{"symbol": "SYN", "available": 3}],
            {"symbol": "SYN", "mint": "missing-mint"},
        )
        == 3
    )


def test_gateway_token_symbol_collision_is_rejected() -> None:
    class Gateway:
        async def get_network_tokens(self, network_id: str):
            return {
                "tokens": [{"symbol": "SYN", "address": "other-mint", "decimals": 9}]
            }

        async def add_token(self, **kwargs):
            raise AssertionError("colliding token must not be registered")

    candidate = {
        "pool_address": "pool",
        "token_a": {"symbol": "SYN", "mint": "wanted-mint", "decimals": 9},
        "token_b": {"symbol": "USDC", "mint": "usdc-mint", "decimals": 6},
    }
    gateway_pool = {
        "pool_address": "pool",
        "base_mint": "wanted-mint",
        "quote_mint": "usdc-mint",
    }

    with pytest.raises(ValueError, match="symbol collision"):
        asyncio.run(
            evidence.ensure_gateway_tokens(
                type("Client", (), {"gateway": Gateway()})(),
                "solana-mainnet-beta",
                candidate,
                gateway_pool,
            )
        )


def test_executor_plan_adoption_accepts_range_alias_and_derived_total() -> None:
    expected = {
        "controller_id": CONTROLLER_ID,
        "connector_name": "solana-mainnet-beta",
        "lp_provider": "orca/clmm",
        "pool_address": "synthetic-pool",
        "trading_pair": "SYN-USDC",
        "lower_price": 99.0,
        "upper_price": 101.0,
        "lower_limit_price": 98.5,
        "upper_limit_price": 101.5,
        "base_amount": 0.05,
        "quote_amount": 5.0,
        "side": 3,
        "total_amount_quote": 10.0,
        "keep_position": False,
    }
    executor = {
        "id": "synthetic-executor",
        "controller_id": CONTROLLER_ID,
        "connector_name": "solana-mainnet-beta",
        "lp_provider": "orca/clmm",
        "pool_address": "synthetic-pool",
        "trading_pair": "SYN-USDC",
        "config": {**expected, "side": "RANGE"},
    }
    executor["config"].pop("total_amount_quote")

    mismatches = lp_position_report._executor_plan_mismatches(
        executor, {"executor_plan": {"executor_config": expected}}
    )

    assert mismatches == []


def test_persisted_executor_requires_exact_id_and_plan_identity() -> None:
    expected = {
        "controller_id": CONTROLLER_ID,
        "connector_name": "solana-mainnet-beta",
        "lp_provider": "orca/clmm",
        "pool_address": "synthetic-pool",
        "trading_pair": "SYN-USDC",
        "lower_price": 99.0,
        "upper_price": 101.0,
        "lower_limit_price": 98.5,
        "upper_limit_price": 101.5,
        "base_amount": 0.05,
        "quote_amount": 5.0,
        "side": 3,
        "total_amount_quote": 10.0,
        "keep_position": False,
    }
    executor = {
        "executor_id": "executor-123",
        "controller_id": CONTROLLER_ID,
        "config": expected,
    }
    state = {
        "executor_id": "executor-123",
        "executor_plan": {"executor_config": expected},
    }

    assert lp_position_report._executor_matches_id(executor, "executor-123") is True
    assert lp_position_report._executor_matches_id(executor, "executor") is False
    assert lp_position_report._executor_lifecycle_mismatches(executor, state) == []

    wrong_executor = {**executor, "executor_id": "executor-1234"}
    assert "executor_id" in lp_position_report._executor_lifecycle_mismatches(
        wrong_executor, state
    )
    wrong_pool = {**executor, "config": {**expected, "pool_address": "other-pool"}}
    assert "pool_address" in lp_position_report._executor_lifecycle_mismatches(
        wrong_pool, state
    )


def test_active_position_records_adapts_schema_v2_single_position() -> None:
    position = {"controller_id": CONTROLLER_ID, "phase": "preflight_ready"}

    assert lifecycle.active_position_records({"active_position": None}) == []
    assert lifecycle.active_position_records({"active_position": position}) == [
        position
    ]


def test_contract_enums_and_routine_outcome_serialize() -> None:
    outcome = contracts.RoutineOutcome(
        routine="orca_live_preflight",
        next_action=contracts.NextAction.RERUN_PREFLIGHT,
        reason="ready",
        arguments={"controller_id": CONTROLLER_ID},
        mutation={"phase": contracts.PositionPhase.PREFLIGHT_READY},
        position_number=1,
        executor_id="executor-1",
    )

    assert contracts.SessionStatus.RUNNING == "running"
    assert outcome.model_dump(mode="json") == {
        "contract_version": 1,
        "routine": "orca_live_preflight",
        "next_action": "rerun-preflight",
        "reason": "ready",
        "arguments": {"controller_id": CONTROLLER_ID},
        "mutation": {"phase": "preflight_ready"},
        "position_number": 1,
        "executor_id": "executor-1",
    }


def test_routine_outcome_mappers_emit_one_v2_staggered_command() -> None:
    scan_payload = {
        "scan_status": "success",
        "decision": "analysis-only",
        "config_summary": {
            "execution_mode": "loop",
            "controller_id": CONTROLLER_ID,
            "total_amount_quote": 10,
        },
        "selected_candidate": {"pool_address": "pool-1"},
    }
    scanner._outcome(scan_payload)

    executor_plan = {
        "action": "create",
        "executor_config": {"controller_id": CONTROLLER_ID, "pool_address": "pool-1"},
    }
    preflight_payload = {
        "decision": "ready",
        "executor_plan": executor_plan,
        "rebalance_plan": None,
        "lifecycle_state": {"position_number": 2},
    }
    preflight._outcome(
        preflight_payload,
        preflight.Config(execution_mode="loop", controller_id=CONTROLLER_ID),
    )

    rebalance_payload = {
        "controller_id": CONTROLLER_ID,
        "execution_mode": "loop",
        "status": "ready",
        "reason": "rebalance_already_consumed",
        "next_action": "rerun-preflight",
        "rebalance_plan": {"total_amount_quote": 10},
        "selected_candidate": {"pool_address": "pool-1"},
        "gateway_pool_info": {"pool_address": "pool-1"},
        "total_amount_quote": 10,
        "wallet_account_name": "master_account",
        "wallet_connector_name": "solana-mainnet-beta",
        "position_number": 2,
    }
    pre_lp_rebalance._outcome(rebalance_payload)

    report_payload = {
        "recommended_supervision_action": "continue",
        "reason": "healthy",
        "input_config": {"controller_id": CONTROLLER_ID},
        "lifecycle_state_update": {"position_number": 2, "phase": "supervising"},
    }
    lp_position_report._outcome(report_payload)

    audit_payload = {
        "audit_status": "complete",
        "identity": {"controller_id": CONTROLLER_ID, "executor_id": "executor-2"},
        "session_commit": {"session_status": "cycle_complete"},
    }
    lp_close_audit._outcome(audit_payload, {"position_number": 2})

    outcomes = [
        scan_payload["outcome"],
        preflight_payload["outcome"],
        rebalance_payload["outcome"],
        report_payload["outcome"],
        audit_payload["outcome"],
    ]
    assert [outcome["next_action"] for outcome in outcomes] == [
        "run-preflight",
        "create-executor",
        "run-preflight",
        "continue",
        "wait-next-cycle",
    ]
    assert preflight_payload["outcome"]["arguments"] == executor_plan
    assert scan_payload["outcome"]["arguments"]["execution_mode"] == "loop"
    assert scan_payload["outcome"]["arguments"]["total_amount_quote"] == 10
    assert rebalance_payload["outcome"]["arguments"]["selected_candidate"] == {
        "pool_address": "pool-1"
    }
    for outcome in outcomes:
        assert outcome["contract_version"] == 1
        assert "next_actions" not in outcome
        assert set(outcome) == {
            "contract_version",
            "routine",
            "next_action",
            "reason",
            "arguments",
            "mutation",
            "position_number",
            "executor_id",
        }


def test_outcome_mappers_cover_blocked_and_close_semantics() -> None:
    scanner_payload = {"scan_status": "no-trade", "decision": "no-trade"}
    scanner._outcome(scanner_payload)
    assert scanner_payload["outcome"]["next_action"] == "no-action"

    preflight_payload = {
        "decision": "blocked",
        "executor_plan": None,
        "rebalance_plan": None,
        "lifecycle_state": {"phase": "rebalance_blocked", "position_number": 3},
    }
    preflight._outcome(
        preflight_payload,
        preflight.Config(execution_mode="loop", controller_id=CONTROLLER_ID),
    )
    assert preflight_payload["outcome"]["next_action"] == "manual-review"

    rebalance_payload = {
        "controller_id": CONTROLLER_ID,
        "status": "blocked",
        "reason": "rebalance_submission_uncertain",
        "next_action": None,
    }
    pre_lp_rebalance._outcome(rebalance_payload)
    assert rebalance_payload["outcome"]["next_action"] == "manual-review"

    report_payload = {
        "recommended_supervision_action": "close",
        "reason": "take_profit_reached",
        "executor_id": "exact-executor-id",
        "input_config": {"controller_id": CONTROLLER_ID},
        "lifecycle_state_update": {"position_number": 3, "phase": "closing"},
    }
    lp_position_report._outcome(report_payload)
    assert report_payload["outcome"]["arguments"] == {
        "action": "stop",
        "executor_id": "exact-executor-id",
        "keep_position": False,
    }

    audit_payload = {
        "audit_status": "complete",
        "identity": {
            "controller_id": CONTROLLER_ID,
            "executor_id": "exact-executor-id",
        },
        "session_commit": {"session_status": "stop_pending"},
    }
    lp_close_audit._outcome(audit_payload, {"position_number": 3})
    assert audit_payload["outcome"]["next_action"] == "no-action"

    flat_payload = {
        "recommended_supervision_action": "no-active-position",
        "reason": "no executor found in API",
        "input_config": {
            "controller_id": CONTROLLER_ID,
            "execution_mode": "loop",
        },
        "session_state": {
            "terms": {
                "risk_profile": "yield_high_risk",
                "total_amount_quote": 25,
            }
        },
    }
    lp_position_report._outcome(flat_payload)
    assert flat_payload["outcome"]["next_action"] == "run-pool-scan"
    assert flat_payload["outcome"]["arguments"] == {
        "execution_mode": "loop",
        "controller_id": CONTROLLER_ID,
        "risk_profile": "yield_high_risk",
        "total_amount_quote": 25,
    }

    audit_input = {
        "controller_id": CONTROLLER_ID,
        "execution_mode": "loop",
        "final_executor": {"id": "exact-executor-id"},
    }
    write_audit_payload = {
        "recommended_supervision_action": "write-audit",
        "reason": "executor_terminal",
        "input_config": {"controller_id": CONTROLLER_ID},
        "audit_input": audit_input,
    }
    lp_position_report._outcome(write_audit_payload)
    assert write_audit_payload["outcome"]["arguments"] == audit_input

    incomplete_resume_payload = {
        "recommended_supervision_action": "resume-rebalance",
        "reason": "rebalance_required",
        "input_config": {
            "controller_id": CONTROLLER_ID,
            "execution_mode": "loop",
        },
        "lifecycle_resume": {
            "selected_candidate": {"pool_address": "pool-1"},
            "gateway_pool_info": {"pool_address": "pool-1"},
            "rebalance_plan": {"total_amount_quote": 10},
            "total_amount_quote": 10,
        },
    }
    lp_position_report._outcome(incomplete_resume_payload)
    assert incomplete_resume_payload["outcome"]["next_action"] == "manual-review"
    assert "wallet_account_name" in incomplete_resume_payload["outcome"]["reason"]


def test_pre_rebalance_and_position_report_return_routine_results(monkeypatch) -> None:
    async def skip_report_write(payload) -> None:
        return None

    monkeypatch.setattr(pre_lp_rebalance, "_save_report", skip_report_write)
    monkeypatch.setattr(lp_position_report, "_save_position_report", skip_report_write)

    rebalance_result = asyncio.run(
        pre_lp_rebalance.run(pre_lp_rebalance.Config(), None)
    )
    report_result = asyncio.run(
        lp_position_report.run(
            lp_position_report.Config(execution_mode="dry_run"), None
        )
    )

    assert isinstance(rebalance_result, RoutineResult)
    assert isinstance(report_result, RoutineResult)
    assert '"outcome"' in rebalance_result.text
    assert "Outcome:" in report_result.text
