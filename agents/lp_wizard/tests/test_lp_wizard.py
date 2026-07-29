from __future__ import annotations

import asyncio
import copy
import json
import multiprocessing
import os
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from agents.lp_wizard.routines import _pool_policy as policy
from agents.lp_wizard.routines import _reporting as reporting
from agents.lp_wizard.routines import _shared as shared
from agents.lp_wizard.routines import close, open, recover, scan, state
from routines.base import RoutineResult

AGENT = ROOT / "agents/lp_wizard"
USDC = shared.USDC_MINT
NOW = datetime(2026, 7, 29, 12, tzinfo=timezone.utc)
REAL_SAVE_REPORT = reporting._save_report


@pytest.fixture(autouse=True)
def disable_routine_report_writes(monkeypatch):
    async def no_report(*args, **kwargs):
        return None

    monkeypatch.setattr(reporting, "_save_report", no_report)


def config(**changes):
    values = {
        "account_name": "master_account",
        "server_name": "pinned",
        "risk_profile": "yield_focused",
        "total_amount_quote": 20,
        "max_slot_count": 2,
        "min_capital_per_slot_quote": 2,
        "max_capital_per_slot_quote": 10,
        "max_slots_per_pool": 1,
        "max_slippage_pct": 1,
        "min_sol_reserve": ".05",
        "inventory_dust_quote": ".01",
        "session_max_age_minutes": 1440,
        "session_take_profit_net_pnl_ratio": ".02",
        "session_stop_loss_net_pnl_ratio": ".02",
    }
    values.update(changes)
    return shared.Config.model_validate(values)


def make_session(tmp_path: Path, number=1, slots=2):
    strategy = tmp_path / "orca"
    session_dir = strategy / "sessions" / f"session_{number}"
    session_dir.mkdir(parents=True)
    values = config(max_slot_count=slots).model_dump(mode="json")
    (session_dir / "config.yml").write_text(
        "\n".join(f"{key}: {value}" for key, value in values.items()) + "\n"
    )
    (strategy / "config.yml").write_text(
        "\n".join(
            f"{key}: {value}" for key, value in values.items() if key != "server_name"
        )
        + "\n"
    )
    return shared.resolve_session(f"lp_wizard.orca_{number}", tmp_path), config(
        max_slot_count=slots
    )


def binding(session):
    return {
        "session_number": session.number,
        "server_name": "pinned",
        "account_name": "master_account",
        "network": shared.NETWORK,
        "observed_default_wallet": "wallet",
        "controller_id": session.controller_id,
    }


def position(
    session, pool="pool-1", executor="executor-1", address="position-1", amount="5"
):
    return {
        "controller_id": session.controller_id,
        "executor_id": executor,
        "position_address": address,
        "pool_address": pool,
        "base_mint": "base-mint",
        "quote_mint": USDC,
        "lower_price": "90",
        "upper_price": "110",
        "lower_limit_price": "89",
        "upper_limit_price": "111",
        "amount_quote": amount,
        "opened_at": NOW.isoformat(),
    }


def pending(
    operation="open", step="create", status="intent", pool="pool-1", amount="5"
):
    return {
        "operation_id": "operation-1",
        "type": operation,
        "step": step,
        "status": status,
        "request": {"pool_address": pool, "amount_quote": amount},
        "attempted_at": None if status == "intent" else NOW.isoformat(),
        "external_id": None,
        "confirmed": {},
    }


def local_stop_pending(position_record, status="submitted"):
    value = pending("close", "stop", status)
    value["request"] = {
        "executor_id": position_record["executor_id"],
        "controller_id": position_record["controller_id"],
        "pool_address": position_record["pool_address"],
        "position_address": position_record["position_address"],
        "keep_position": True,
        "reason": None,
    }
    return value


def returned_restore_pending(
    position_record, *, status="submitted", external_id="tx-1"
):
    value = pending("close", "restore", status)
    value["external_id"] = external_id
    value["request"] = {
        "pool_address": position_record["pool_address"],
        "position_address": position_record["position_address"],
        "executor_id": position_record["executor_id"],
        "restore_base_amount": ".021",
        "restore_swap": {
            "connector": "jupiter",
            "network": shared.NETWORK,
            "trading_pair": f"{position_record['base_mint']}-{position_record['quote_mint']}",
            "side": "SELL",
            "amount": ".021",
            "slippage_pct": "1",
            "wallet_address": "wallet",
            "base_mint": position_record["base_mint"],
            "quote_mint": position_record["quote_mint"],
        },
    }
    value["confirmed"] = {
        "returned_inventory": {
            "position_address": position_record["position_address"],
            "base_amount": ".02",
            "base_fee": ".001",
            "quote_amount": "5",
            "quote_fee": ".01",
            "base_total": ".021",
            "quote_total": "5.01",
            "source": "executor_close_receipt",
            "executor_status": "COMPLETED",
            "close_type": "POSITION_HOLD",
        }
    }
    return value


def raw_pool(address="pool-1", *, change=0.005, fee24=500, fee7=3500, updated=None):
    return {
        "address": address,
        "updatedAt": updated,
        "tokenA": {"symbol": "SOL", "mint": "base-mint", "decimals": 9},
        "tokenB": {"symbol": "USDC", "mint": USDC, "decimals": 6},
        "price": 100,
        "tvlUsdc": 1_000_000,
        "stats": {
            "1h": {"volume": 20_000, "fees": 30},
            "4h": {"volume": 80_000, "fees": 120},
            "24h": {"volume": 400_000, "fees": fee24, "priceDelta": change},
            "7d": {"volume": 2_000_000, "fees": fee7},
        },
        "feeRate": 400,
        "adaptiveFeeEnabled": False,
        "feeTierIndex": 1,
        "tickSpacing": 1,
        "hasWarning": False,
    }


def candidate(rank=1, profile="yield_focused", now=NOW, **pool_changes):
    raw = raw_pool(**pool_changes)
    normalized, error = policy.normalize_record(raw, "utility", "volume24h", 0)
    assert error is None
    row, error = policy.evaluate_pool(normalized, profile, now.isoformat(), 20)
    assert error is None
    return {"rank": rank, **row}


def executor(position_record, *, active=True, status="RUNNING", custom=None):
    return {
        "executor_id": position_record["executor_id"],
        "controller_id": position_record["controller_id"],
        "status": status,
        "is_active": active,
        "pool_address": position_record["pool_address"],
        "position_address": position_record["position_address"],
        "config": {
            "controller_id": position_record["controller_id"],
            "connector_name": shared.NETWORK,
            "lp_provider": "orca/clmm",
            "trading_pair": "SOL-USDC",
            "pool_address": position_record["pool_address"],
            "lower_price": "90",
            "upper_price": "110",
            "lower_limit_price": "89",
            "upper_limit_price": "111",
            "base_amount": "0.05",
            "quote_amount": "5",
            "side": 3,
            "keep_position": True,
        },
        "custom_info": custom or {},
    }


def terminal_executor(
    position_record,
    *,
    price="111",
    state="COMPLETE",
    close_type="POSITION_HOLD",
    base_amount=".02",
    base_fee=".001",
    quote_amount="5",
    quote_fee=".01",
):
    raw = executor(position_record, active=False, status="COMPLETED")
    raw["position_address"] = None
    raw["custom_info"] = {
        "state": state,
        "position_address": None,
        "close_type": close_type,
        "current_price": price,
        "base_amount": base_amount,
        "base_fee": base_fee,
        "quote_amount": quote_amount,
        "quote_fee": quote_fee,
    }
    return shared.normalize_executor_response(raw)


def run(coro):
    result = asyncio.run(coro)
    return result.text if isinstance(result, RoutineResult) else result


def lock_worker(path: str, ready, acquired):
    ready.set()
    with shared.file_lock(Path(path)):
        acquired.set()


def test_agent_and_strategy_metadata_and_local_policy():
    agent = (AGENT / "AGENT.md").read_text()
    strategy = (AGENT / "strategies/orca/strategy.md").read_text()
    assert re.search(r"(?m)^name: lp_wizard$", agent)
    assert re.search(r"(?m)^name: orca$", strategy)
    assert "agent_key: codex" in agent and "agent_key: null" in strategy
    assert "trading_agent_journal_write" in agent
    for name in ("scan", "state", "open", "close", "recover"):
        assert f"`{name}`" in agent and f"`{name}`" in strategy
    assert "only local" in agent.lower() and "observation only" in agent.lower()
    assert "must_take_action" in agent and "must_take_action" in strategy
    assert "GLOBAL_SESSION_STOP_LATCHED" in agent
    assert "GLOBAL_SESSION_STOP_LATCHED" in strategy


def test_public_routine_surface_is_generic_and_isolated():
    public = {
        path.stem
        for path in (AGENT / "routines").glob("*.py")
        if not path.stem.startswith("_")
    }
    assert public == {"scan", "state", "open", "close", "recover"}
    assert not any(name.startswith(("orca_", "lp_", "lpmaxxing")) for name in public)
    assert not list((AGENT / "strategies").rglob("*.py"))
    assert not any(
        "agents.lpmaxxing" in path.read_text()
        for path in (AGENT / "routines").glob("*.py")
    )


@pytest.mark.parametrize(
    "controller",
    [
        "lp_wizard.orca_0",
        "lp_wizard.orca_01",
        "lp_wizard.orca_e0",
        "lp_wizard.orca_e01",
        "orca_1",
        "lp_wizard.orca_1/x",
        " lp_wizard.orca_1",
    ],
)
def test_controller_form_is_strict(tmp_path, controller):
    with pytest.raises(ValueError, match="exactly"):
        shared.resolve_session(controller, tmp_path)


def test_session_resolution_missing_foreign_malformed_and_symlink_escape(tmp_path):
    (tmp_path / "orca/sessions/session_1").mkdir(parents=True)
    assert shared.resolve_session("lp_wizard.orca_1", tmp_path).number == 1
    assert not shared.resolve_session("lp_wizard.orca_e100", tmp_path).live
    with pytest.raises(ValueError, match="does not exist"):
        shared.resolve_session("lp_wizard.orca_2", tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "evil").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        shared._contained(tmp_path / "evil/file", tmp_path / "orca")


def test_experiment_mutations_rejected_and_dry_runs_are_stateless(
    tmp_path, monkeypatch
):
    strategy = tmp_path / "orca"
    strategy.mkdir()
    values = config(server_name=None).model_dump(mode="json", exclude={"server_name"})
    (strategy / "config.yml").write_text(
        "\n".join(f"{k}: {v}" for k, v in values.items())
    )
    dry = shared.resolve_session("lp_wizard.orca_e1", tmp_path)
    for routine in (open, close, recover):
        with pytest.raises(ValueError, match="live loop"):
            shared.require_live(dry)
    monkeypatch.setattr(shared, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(
        shared, "write_state", lambda *args: pytest.fail("dry read wrote state")
    )
    result = json.loads(
        run(state.run(state.Config(controller_id=dry.controller_id), None))
    )
    assert result["state_mode"] == "stateless_read_only" and result["slots"] == []
    assert not (strategy / "dry_runs").exists()


@pytest.mark.parametrize(
    ("routine", "missing"),
    [
        (state, ["controller_id"]),
        (
            open,
            [
                "controller_id",
                "slot_id",
                "candidate",
                "range_option",
                "amount_quote",
            ],
        ),
        (close, ["controller_id", "slot_id"]),
        (recover, ["controller_id", "slot_id"]),
    ],
)
def test_manual_runtime_routines_return_samples_without_runtime_access(
    routine, missing, monkeypatch
):
    monkeypatch.setattr(
        shared,
        "resolve_session",
        lambda *_: pytest.fail("sample resolved a controller session"),
    )

    result = json.loads(run(routine.run(routine.Config(), None)))

    assert result["status"] == "sample_only"
    assert result["missing_runtime_fields"] == missing
    assert result["mutations_allowed"] is False
    assert result["mutations_attempted"] is False
    assert result["runtime_state_inspected"] is False


def test_state_rejects_missing_live_controller_before_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(shared, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(
        reporting,
        "_save_report",
        lambda *args: pytest.fail("invalid state config attempted a report"),
    )

    with pytest.raises(ValidationError, match="live session directory does not exist"):
        state.Config(controller_id="lp_wizard.orca_999")


def test_state_external_read_timeout_returns_error(tmp_path, monkeypatch):
    session, _ = make_session(tmp_path)
    monkeypatch.setattr(shared, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(state, "STATE_TIMEOUT_SECONDS", 0.01)
    release = asyncio.Event()

    async def blocked(*args):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    monkeypatch.setattr(state, "_execute", blocked)

    async def scenario():
        result = await state.run(
            state.Config(controller_id=session.controller_id), None
        )
        release.set()
        await asyncio.sleep(0)
        return result

    result = json.loads(asyncio.run(scenario()).text)

    assert result["health"] == "error"
    assert result["mutations_allowed"] is False
    assert result["errors"] == ["TimeoutError: state inspection exceeded 0.01 seconds"]


def test_report_timeout_returns_completed_result(monkeypatch):
    monkeypatch.setattr(reporting, "REPORT_TIMEOUT_SECONDS", 0.01)
    release = asyncio.Event()

    async def blocked(*args):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    monkeypatch.setattr(reporting, "_save_report", blocked)

    async def scenario():
        result = await asyncio.wait_for(state.run(state.Config(), None), timeout=0.2)
        release.set()
        await asyncio.sleep(0)
        return result

    result = asyncio.run(scenario())
    payload = json.loads(result.text)

    assert payload["status"] == "sample_only"
    assert payload["report_id"] is None
    assert payload["report_error"] == (
        "TimeoutError: report save exceeded 0.01 seconds"
    )


def test_cancellation_report_timeout_is_bounded(monkeypatch):
    monkeypatch.setattr(reporting, "CANCELLATION_REPORT_TIMEOUT_SECONDS", 0.01)
    release = asyncio.Event()

    async def blocked(*args):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    monkeypatch.setattr(reporting, "_save_report", blocked)

    async def scenario():
        await asyncio.wait_for(
            reporting.finish_cancelled(
                "state",
                state.Config(),
                started_at=NOW.isoformat(),
                started_monotonic=0,
            ),
            timeout=0.2,
        )
        release.set()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_scan_has_safe_independent_experiment_default():
    assert scan.Config().controller_id == "lp_wizard.orca_e1"
    assert scan.Config().risk_profile == "yield_focused"


def test_scan_profile_parameter_overrides_live_session_config(tmp_path, monkeypatch):
    session, strategy_config = make_session(tmp_path)
    monkeypatch.setattr(shared, "STRATEGIES_DIR", tmp_path)

    resolved_session, resolved_config, profile, source = scan._resolve_policy(
        scan.Config(
            controller_id=session.controller_id,
            risk_profile="yield_high_risk",
        )
    )

    assert resolved_session == session
    assert resolved_config == strategy_config
    assert strategy_config.risk_profile == "yield_focused"
    assert profile == "yield_high_risk"
    assert source == "routine_parameter"


@pytest.mark.parametrize(
    "config_class",
    [scan.Config, state.Config, open.Config, close.Config, recover.Config],
)
def test_supplied_blank_runtime_ids_are_invalid_not_samples(config_class):
    with pytest.raises(ValidationError):
        config_class(controller_id=" ")


@pytest.mark.parametrize(
    "config_class",
    [scan.Config, state.Config, open.Config, close.Config, recover.Config],
)
def test_partial_samples_reject_invalid_supplied_controller(config_class):
    with pytest.raises(ValidationError):
        config_class(controller_id="lp_wizard.orca_invalid")


def test_report_save_failure_does_not_change_routine_result(monkeypatch):
    async def failed_report(*args, **kwargs):
        raise OSError("reports unavailable")

    monkeypatch.setattr(reporting, "_save_report", failed_report)
    result = asyncio.run(state.run(state.Config(), None))

    assert isinstance(result, RoutineResult)
    payload = json.loads(result.text)
    assert payload["status"] == "sample_only"
    assert payload["report_id"] is None
    assert payload["report_error"] == "OSError: reports unavailable"


def test_cancelled_mutation_still_persists_a_recovery_report(tmp_path, monkeypatch):
    import condor.reports as reports

    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(reports, "CHARTS_DIR", reports_dir)
    monkeypatch.setattr(reports, "INDEX_FILE", reports_dir / "reports_index.json")
    monkeypatch.setattr(reporting, "_save_report", REAL_SAVE_REPORT)

    async def cancelled(config, context, debug):
        debug.update(
            {
                "stage": "create_executor",
                "operation_id": "operation-1",
                "mutation_claimed": True,
            }
        )
        raise asyncio.CancelledError

    monkeypatch.setattr(open, "_execute", cancelled)
    cfg = open.Config(
        controller_id="lp_wizard.orca_1",
        slot_id="slot-01",
        candidate={"pool_address": "pool-1"},
        range_option="balanced",
        amount_quote="5",
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(open.run(cfg, None))

    entries, total = reports.list_reports(limit=10)
    assert total == 1 and entries[0]["source_name"] == "lp_wizard/open"
    document = (reports_dir / entries[0]["filename"]).read_text(encoding="utf-8")
    assert "cancelled" in document and "operation-1" in document
    assert "recover_required" in document


def test_cancellation_before_mutation_claim_does_not_require_recovery(monkeypatch):
    captured = {}

    async def capture_report(routine, config, payload, debug):
        captured.update(payload)
        return "report-1"

    monkeypatch.setattr(reporting, "_save_report", capture_report)
    asyncio.run(
        reporting.finish_cancelled(
            "open",
            open.Config(),
            started_at=NOW.isoformat(),
            started_monotonic=0,
            debug={"stage": "resolve_and_validate"},
        )
    )

    assert captured["status"] == "cancelled"
    assert captured["recover_required"] is False


def test_all_public_routines_persist_input_output_and_debug_reports(
    tmp_path, monkeypatch
):
    import condor.reports as reports

    reports_dir = tmp_path / "reports"
    monkeypatch.setattr(reports, "CHARTS_DIR", reports_dir)
    monkeypatch.setattr(reports, "INDEX_FILE", reports_dir / "reports_index.json")
    monkeypatch.setattr(reporting, "_save_report", REAL_SAVE_REPORT)

    async def scanned(config, context, debug):
        debug.update(
            {
                "stage": "normalize_and_rank",
                "transport_note": "authorization: Bearer top-secret-token",
                "alternate_auth": "Authorization=Token second-secret-token",
                "header_note": "X-API-Key: third-secret-token",
                "wallet_note": "wallet_address=private-wallet-in-error",
            }
        )
        return reporting.compact(
            {
                "scan_status": "success",
                "controller_id": config.controller_id,
                "controller_mode": "experiment",
                "ranked_candidates": [
                    {
                        "trading_pair": "SOL-USDC",
                        "pool_address": "pool-1",
                        "weighted_score": 1.5,
                        "tvl_usd": 1000000,
                        "volume_24h_usd": 500000,
                        "range_options": [{"name": "balanced"}],
                    }
                ],
                "errors": [],
            }
        )

    monkeypatch.setattr(scan, "_execute", scanned)
    configs = [
        (scan, scan.Config()),
        (state, state.Config()),
        (
            open,
            open.Config(
                candidate={
                    "password": "do-not-persist",
                    "wallet_address": "private-wallet",
                }
            ),
        ),
        (close, close.Config()),
        (recover, recover.Config()),
    ]

    results = [asyncio.run(routine.run(cfg, None)) for routine, cfg in configs]
    entries, total = reports.list_reports(limit=20)

    assert total == 5
    assert all(isinstance(result, RoutineResult) for result in results)
    assert all(json.loads(result.text).get("report_id") for result in results)
    assert {entry["source_name"] for entry in entries} == {
        "lp_wizard/scan",
        "lp_wizard/state",
        "lp_wizard/open",
        "lp_wizard/close",
        "lp_wizard/recover",
    }
    for entry in entries:
        document = (reports_dir / entry["filename"]).read_text(encoding="utf-8")
        assert "Input" in document and "Output" in document and "Debug" in document
        assert "do-not-persist" not in document
        assert "private-wallet" not in document
        assert "top-secret-token" not in document
        assert "second-secret-token" not in document
        assert "third-secret-token" not in document
        assert "private-wallet-in-error" not in document


def test_live_config_is_full_immutable_and_server_pinned(tmp_path):
    session, expected = make_session(tmp_path)
    loaded = shared.load_config(session)
    assert loaded == expected and loaded.server_name == "pinned"
    assert loaded.model_config["frozen"] is True
    with pytest.raises(ValidationError):
        loaded.total_amount_quote = Decimal("99")
    (session.path / "config.yml").write_text("account_name: master_account\n")
    with pytest.raises(ValidationError):
        shared.load_config(session)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("total_amount_quote", float("nan")),
        ("max_slippage_pct", float("inf")),
        ("max_slot_count", 1.5),
        ("max_slot_count", 0),
        ("session_take_profit_net_pnl_ratio", float("nan")),
    ],
)
def test_config_rejects_nonfinite_fractional_and_nonpositive_values(field, value):
    with pytest.raises(ValidationError):
        config(**{field: value})


@pytest.mark.parametrize(
    "changes",
    [
        {"min_capital_per_slot_quote": 11, "max_capital_per_slot_quote": 10},
        {"max_capital_per_slot_quote": 21},
        {"max_slippage_pct": 101},
        {"min_sol_reserve": -1},
        {"session_max_age_minutes": 0},
        {"session_take_profit_net_pnl_ratio": 0},
        {"session_stop_loss_net_pnl_ratio": -1},
    ],
)
def test_config_rejects_cross_bounds(changes):
    with pytest.raises(ValidationError):
        config(**changes)


def test_positive_slot_ids_are_stable_through_slot_100():
    slots = shared.initial_state(config(max_slot_count=100))["slots"]
    assert list(slots)[:2] == ["slot-01", "slot-02"]
    assert list(slots)[-1] == "slot-100" and len(slots) == 100


def test_state_is_session_local_atomic_and_validated(tmp_path, monkeypatch):
    session, cfg = make_session(tmp_path)
    value = shared.initial_state(cfg)
    shared.write_state(session, cfg, value)
    assert shared.read_state(session, cfg) == value
    assert not list(session.path.glob("*.tmp"))
    original_replace = os.replace
    observed = []
    monkeypatch.setattr(
        os,
        "replace",
        lambda source, target: (
            observed.append((source, target)),
            original_replace(source, target),
        )[1],
    )
    shared.write_state(session, cfg, value)
    assert observed and Path(observed[0][1]) == session.state_path
    assert not (session.strategy_dir / "state.json").exists()


def test_state_rejects_noncanonical_quotes_duplicate_pools_and_aggregate_capital(
    tmp_path,
):
    session, cfg = make_session(tmp_path)
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"]["position"] = position(session)
    shared.validate_state(value, session, cfg)
    bad = copy.deepcopy(value)
    bad["slots"]["slot-01"]["position"]["quote_mint"] = "fake-usdc"
    with pytest.raises(ValueError, match="canonical"):
        shared.validate_state(bad, session, cfg)
    bad = copy.deepcopy(value)
    bad["slots"]["slot-02"]["position"] = position(session, executor="e2", address="p2")
    with pytest.raises(ValueError, match="same pool"):
        shared.validate_state(bad, session, cfg)
    bad["slots"]["slot-02"]["position"]["pool_address"] = "pool-2"
    bad["slots"]["slot-01"]["position"]["amount_quote"] = "10"
    bad["slots"]["slot-02"]["position"]["amount_quote"] = "10.01"
    with pytest.raises(ValueError, match="outside configured bounds|exceeds"):
        shared.validate_state(bad, session, cfg)


def test_sessions_do_not_block_each_other(monkeypatch, tmp_path):
    one, _ = make_session(tmp_path, 1)
    two, cfg = make_session(tmp_path, 2)
    value = shared.initial_state(cfg)
    value["binding"] = binding(two)
    value["slots"]["slot-01"]["pending_mutation"] = pending()
    shared.write_state(two, cfg, value)
    monkeypatch.setattr(shared, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(
        shared,
        "bound_client",
        lambda *args: async_value(shared.BoundClient("pinned", object())),
    )
    monkeypatch.setattr(
        shared, "search_controller_executors", lambda *args: async_value([])
    )
    monkeypatch.setattr(
        shared, "default_solana_wallet", lambda *args: async_value("wallet")
    )
    monkeypatch.setattr(
        shared, "read_balances", lambda *args: pytest.fail("state read balances")
    )

    result = json.loads(
        run(state.run(state.Config(controller_id=one.controller_id), None))
    )

    assert result["health"] == "healthy"
    assert result["mutations_allowed"] is True
    assert "prior_session_blockers" not in result


def test_open_preflight_accepts_other_session_position_in_same_pool(
    monkeypatch, tmp_path
):
    session, cfg = make_session(tmp_path)
    monkeypatch.setattr(
        shared, "search_controller_executors", lambda *args: async_value([])
    )
    monkeypatch.setattr(
        shared,
        "get_owned_positions",
        lambda *args: async_value(
            [
                {
                    "position_address": "other-session-position",
                    "pool_address": "pool-1",
                    "raw": {},
                }
            ]
        ),
    )

    executor_ids, position_ids = run(
        open._ownership_preflight(object(), session, cfg, "wallet", "pool-1")
    )

    assert executor_ids == set()
    assert position_ids == {"other-session-position"}


def test_open_preflight_still_requires_current_session_position(monkeypatch, tmp_path):
    session, cfg = make_session(tmp_path)
    current_position = position(session)
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"]["position"] = current_position
    shared.write_state(session, cfg, value)
    monkeypatch.setattr(
        shared,
        "search_controller_executors",
        lambda *args: async_value(
            [shared.normalize_executor_response(executor(current_position))]
        ),
    )
    monkeypatch.setattr(shared, "get_owned_positions", lambda *args: async_value([]))

    with pytest.raises(ValueError, match="missing or conflicting session position"):
        run(open._ownership_preflight(object(), session, cfg, "wallet", "pool-2"))


def test_create_reconciliation_attributes_position_with_concurrent_same_pool_open(
    monkeypatch, tmp_path
):
    session, _ = make_session(tmp_path)
    created_position = position(session, executor="ours", address="ours-position")
    created_executor = shared.normalize_executor_response(executor(created_position))
    monkeypatch.setattr(
        shared,
        "search_controller_executors",
        lambda *args: async_value([created_executor]),
    )
    monkeypatch.setattr(
        shared,
        "get_owned_positions",
        lambda *args: async_value(
            [
                {"position_address": "existing", "pool_address": "pool-1"},
                {"position_address": "ours-position", "pool_address": "pool-1"},
                {
                    "position_address": "other-session-position",
                    "pool_address": "pool-1",
                },
            ]
        ),
    )

    reconciled, proof = run(
        open._reconcile_create(
            object(),
            session,
            "wallet",
            "pool-1",
            set(),
            {"existing"},
            created_executor["config"],
            None,
        )
    )

    assert reconciled["executor_id"] == "ours"
    assert proof == {
        "executor_ids_unchanged": False,
        "position_ids_unchanged": False,
    }


def test_file_lock_serializes_a_real_second_process(tmp_path):
    lock = tmp_path / "wallet.lock"
    ctx = multiprocessing.get_context("spawn")
    ready, acquired = ctx.Event(), ctx.Event()
    with shared.file_lock(lock):
        process = ctx.Process(target=lock_worker, args=(str(lock), ready, acquired))
        process.start()
        assert ready.wait(5) and not acquired.wait(0.2)
    assert acquired.wait(5)
    process.join(5)
    assert process.exitcode == 0


def test_profile_policy_is_monotonic_and_fee_windows_are_independent():
    focused, high, extreme, unlimited = (
        policy.get_profile(name) for name in policy.LIVE_PROFILES
    )
    assert (
        set(focused["categories"])
        <= set(high["categories"])
        == set(extreme["categories"])
        == set(unlimited["categories"])
    )
    for field in ("min_tvl_usd", "min_volume_24h_usd", "min_volume_7d_usd"):
        assert focused[field] >= high[field] == extreme[field] == unlimited[field]
    assert (
        focused["max_abs_net_price_change_24h"]
        < high["max_abs_net_price_change_24h"]
        < extreme["max_abs_net_price_change_24h"]
    )
    assert unlimited["max_abs_net_price_change_24h"] is None
    for fee24, fee7, reason in [(199, 3500, "24h"), (500, 1399, "7d")]:
        normalized, _ = policy.normalize_record(
            raw_pool(fee24=fee24, fee7=fee7), "utility", "volume24h", 0
        )
        assert policy.evaluate_pool(normalized, "yield_focused", NOW.isoformat(), 20)[
            1
        ].startswith(f"fee_productivity_{reason}")


def test_normalization_rejects_conflicting_aliases_before_dedup():
    raw = raw_pool()
    raw["pool_address"] = "other"
    assert (
        policy.normalize_record(raw, "utility", "volume24h", 0)[1]
        == "conflicting_pool_address_aliases"
    )
    raw = raw_pool()
    raw["stats"]["24h"]["feesUsd"] = 501
    assert (
        policy.normalize_record(raw, "utility", "volume24h", 0)[1]
        == "conflicting_fees_24h_usd_aliases"
    )


def test_dedup_ranking_tie_break_options_and_no_limit_cap_are_deterministic():
    older, _ = policy.normalize_record(
        raw_pool("pool-b", updated="2026-01-01T00:00:00Z"), "utility", "volume24h", 0
    )
    newer, _ = policy.normalize_record(
        raw_pool("pool-b", updated="2026-01-02T00:00:00Z"), "utility", "volume7d", 1
    )
    first, _ = policy.normalize_record(raw_pool("pool-a"), "utility", "volume24h", 0)
    deduped = policy.deduplicate_records([older, newer, first])
    ranked, errors = policy.rank_pools(deduped, "yield_focused", NOW.isoformat(), 20)
    assert errors == {} and [row["pool_address"] for row in ranked] == [
        "pool-a",
        "pool-b",
    ]
    assert len(ranked[1]["source_evidence"]) == 2
    names = {option["name"] for option in ranked[0]["range_options"]}
    assert {"concentrated", "balanced", "defensive"} <= names
    unlimited = candidate(profile="yield_no_limit", change=0.64)
    extreme = next(
        option for option in unlimited["range_options"] if option["name"] == "extreme"
    )
    assert extreme["width_capped"] is True and extreme[
        "provisional_half_width"
    ] == pytest.approx(0.95)


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (
            lambda row: row.__setitem__("fees_24h_usd", 501),
            "candidate_contract_mismatch",
        ),
        (
            lambda row: row.__setitem__("risk_profile", "yield_high_risk"),
            "candidate_profile_mismatch",
        ),
        (
            lambda row: row.__setitem__("selected", True),
            "candidate_contains_forbidden_field",
        ),
        (
            lambda row: row.__setitem__(
                "observed_at", (NOW - timedelta(minutes=11)).isoformat()
            ),
            "candidate_stale",
        ),
    ],
)
def test_candidate_tamper_profile_forbidden_and_stale_validation(mutation, error):
    row = candidate()
    mutation(row)
    assert policy.revalidate_candidate(row, "yield_focused", 20, now=NOW)[2] == error


def test_any_ranked_candidate_and_any_returned_option_revalidate():
    row = candidate(rank=7)
    for option in row["range_options"]:
        validated, chosen, error = policy.revalidate_candidate(
            row, "yield_focused", 20, option["name"], NOW
        )
        assert error is None and validated["rank"] == 7 and chosen == option


def test_scan_request_matrix_and_incomplete_fetch(monkeypatch, tmp_path):
    profile = policy.get_profile("yield_focused")
    specs = scan._request_specs(profile)
    assert len(specs) == len(profile["categories"]) * len(policy.DISCOVERY_LENSES)
    assert all(
        "size=100" in url and "stats=1h%2C4h%2C24h%2C7d" in url for *_, url in specs
    )
    session, cfg = make_session(tmp_path)
    monkeypatch.setattr(
        scan,
        "_resolve_policy",
        lambda _: (session, cfg, cfg.risk_profile, "immutable_session_config"),
    )

    async def failed(_):
        return (
            [],
            {
                "required_count": len(specs),
                "successful_count": 0,
                "failed_count": 1,
                "requests": [],
            },
            ["boom"],
        )

    monkeypatch.setattr(scan, "_fetch_all", failed)
    result = json.loads(
        run(scan.run(scan.Config(controller_id=session.controller_id), None))
    )
    assert (
        result["scan_status"] == "incomplete_fetch"
        and result["ranked_candidates"] == []
    )


def test_scan_normalizes_then_dedups_ranks_then_limits_and_never_writes(
    monkeypatch, tmp_path
):
    session, cfg = make_session(tmp_path)
    events = []
    monkeypatch.setattr(
        scan,
        "_resolve_policy",
        lambda _: (session, cfg, cfg.risk_profile, "immutable_session_config"),
    )

    async def fetched(_):
        return (
            [("utility", "volume24h", [raw_pool("b"), raw_pool("b"), raw_pool("a")])],
            {"requests": []},
            [],
        )

    monkeypatch.setattr(scan, "_fetch_all", fetched)
    real_dedup, real_rank = policy.deduplicate_records, policy.rank_pools
    monkeypatch.setattr(
        policy,
        "deduplicate_records",
        lambda rows: (events.append(("dedup", len(rows))), real_dedup(rows))[1],
    )
    monkeypatch.setattr(
        policy,
        "rank_pools",
        lambda rows, *args: (
            events.append(("rank", len(rows))),
            real_rank(rows, *args),
        )[1],
    )
    monkeypatch.setattr(
        shared, "write_state", lambda *args: pytest.fail("scan wrote state")
    )
    result = json.loads(
        run(scan.run(scan.Config(controller_id=session.controller_id, limit=1), None))
    )
    assert (
        events == [("dedup", 3), ("rank", 2)]
        and result["eligible_count"] == 2
        and result["returned_count"] == 1
    )
    forbidden = {
        "selected",
        "executable",
        "action",
        "next_action",
        "lower_price",
        "upper_price",
    }

    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    assert not forbidden.intersection(keys(result["ranked_candidates"]))
    assert not session.state_path.exists()


def test_state_missing_is_read_only_and_performance_aggregates(monkeypatch, tmp_path):
    session, cfg = make_session(tmp_path)
    monkeypatch.setattr(shared, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(
        shared, "write_state", lambda *args: pytest.fail("state routine wrote")
    )
    monkeypatch.setattr(
        shared,
        "bound_client",
        lambda *args: async_value(shared.BoundClient("pinned", object())),
    )
    monkeypatch.setattr(
        shared, "search_controller_executors", lambda *args: async_value([])
    )
    monkeypatch.setattr(
        shared, "default_solana_wallet", lambda *args: async_value("wallet")
    )
    result = json.loads(
        run(state.run(state.Config(controller_id=session.controller_id), None))
    )
    assert (
        result["state_mode"] == "in_memory_initial"
        and result["mutations_allowed"] is True
    )
    assert result["session_stop"]["triggered"] is False
    assert result["session_stop"]["performance"]["session_net_pnl_ratio"] == "0"
    assert not session.state_path.exists()
    rows = [
        {
            "telemetry": {
                "fees_earned_quote": "1",
                "net_pnl_quote": "2",
                "filled_amount_quote": "10",
            }
        },
        {
            "telemetry": {
                "fees_earned_quote": "3",
                "net_pnl_quote": "-1",
                "filled_amount_quote": "10",
            }
        },
    ]
    assert state._aggregate_performance(rows) == {
        "fees_earned_quote": Decimal(4),
        "net_pnl_quote": Decimal(1),
        "filled_amount_quote": Decimal(20),
        "net_pnl_ratio": Decimal(".05"),
    }


@pytest.mark.parametrize(
    ("pnl", "age_minutes", "reason"),
    [
        ("-.4", 1, "session_stop_loss_reached"),
        (".4", 1, "session_take_profit_reached"),
        ("0", 1440, "session_max_age_reached"),
    ],
)
def test_session_stop_advisory_commands_llm_for_each_global_limit(
    tmp_path, pnl, age_minutes, reason
):
    session, cfg = make_session(tmp_path)
    started_at = NOW - timedelta(minutes=age_minutes)
    os.utime(session.config_path, (started_at.timestamp(), started_at.timestamp()))
    advisory = state._session_stop_advisory(
        session,
        cfg,
        [{"executor_id": "executor-1", "net_pnl_quote": pnl}],
        NOW,
    )
    assert advisory["triggered"] is True
    assert reason in advisory["reasons"]
    assert advisory["must_take_action"] is True
    assert "do not scan or open" in advisory["command"]
    assert "GLOBAL_SESSION_STOP_LATCHED" in advisory["command"]
    assert advisory["performance"]["ratio_denominator_quote"] == Decimal("20")


def test_session_stop_advisory_uses_all_executors_and_requires_complete_pnl(
    tmp_path,
):
    session, cfg = make_session(tmp_path)
    started_at = NOW - timedelta(minutes=1)
    os.utime(session.config_path, (started_at.timestamp(), started_at.timestamp()))
    advisory = state._session_stop_advisory(
        session,
        cfg,
        [
            {"executor_id": "active", "net_pnl_quote": ".1"},
            {"executor_id": "terminal", "net_pnl_quote": ".3"},
        ],
        NOW,
    )
    assert advisory["performance"]["session_net_pnl_quote"] == Decimal(".4")
    assert advisory["performance"]["session_net_pnl_ratio"] == Decimal(".02")
    assert advisory["reasons"] == ["session_take_profit_reached"]

    incomplete = state._session_stop_advisory(
        session,
        cfg,
        [{"executor_id": "missing", "net_pnl_quote": None}],
        NOW,
    )
    assert incomplete["performance"]["pnl_complete"] is False
    assert incomplete["performance"]["session_net_pnl_ratio"] is None
    assert incomplete["reasons"] == []
    assert incomplete["must_take_action"] is True
    assert incomplete["attention_reasons"] == ["session_pnl_unavailable"]
    assert "do not scan or open" in incomplete["command"]

    duplicate = state._session_stop_advisory(
        session,
        cfg,
        [
            {"executor_id": "duplicate", "net_pnl_quote": ".2"},
            {"executor_id": "duplicate", "net_pnl_quote": ".2"},
        ],
        NOW,
    )
    assert duplicate["performance"]["pnl_complete"] is False
    assert duplicate["performance"]["duplicate_executor_ids"] == ["duplicate"]
    assert duplicate["reasons"] == []

    started_at = NOW - timedelta(minutes=1440)
    os.utime(session.config_path, (started_at.timestamp(), started_at.timestamp()))
    expired = state._session_stop_advisory(
        session,
        cfg,
        [{"executor_id": "missing", "net_pnl_quote": None}],
        NOW,
    )
    assert expired["reasons"] == ["session_max_age_reached"]

    # The stop is deliberately an LLM instruction, not a hard gate in open.
    open._assert_local_open(
        shared.initial_state(cfg), session, cfg, "slot-01", "pool-1", Decimal("5")
    )


def test_session_stop_advisory_flags_future_clock_and_survives_state_read_error(
    monkeypatch, tmp_path
):
    session, cfg = make_session(tmp_path)
    future = NOW + timedelta(minutes=5)
    os.utime(session.config_path, (future.timestamp(), future.timestamp()))
    advisory = state._session_stop_advisory(session, cfg, [], NOW)
    assert advisory["session_age_complete"] is False
    assert advisory["session_age_minutes"] is None
    assert advisory["attention_reasons"] == ["session_age_unavailable"]
    assert advisory["must_take_action"] is True

    original_stat = Path.stat

    def fail_config_stat(path, *args, **kwargs):
        if path == session.config_path:
            raise OSError("mtime unavailable")
        return original_stat(path, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "stat", fail_config_stat)
        unavailable = state._session_stop_advisory(session, cfg, [], NOW)
    assert unavailable["session_started_at"] is None
    assert unavailable["attention_reasons"] == ["session_age_unavailable"]
    assert unavailable["must_take_action"] is True

    old = datetime.now(timezone.utc) - timedelta(minutes=1441)
    os.utime(session.config_path, (old.timestamp(), old.timestamp()))
    monkeypatch.setattr(shared, "STRATEGIES_DIR", tmp_path)

    def fail_read(*args):
        raise ValueError("malformed persisted state")

    monkeypatch.setattr(shared, "read_state", fail_read)
    result = json.loads(
        run(state.run(state.Config(controller_id=session.controller_id), None))
    )
    assert result["health"] == "error"
    assert result["session_stop"]["reasons"] == ["session_max_age_reached"]
    assert result["session_stop"]["must_take_action"] is True


def test_state_separates_contradictions_history_and_virtual_hold_warning(
    monkeypatch, tmp_path
):
    session, cfg = make_session(tmp_path)
    pos = position(session)
    pos.update(
        {
            "lower_limit_price": "89.123456789012345",
            "lower_price": "90.123456789012345",
            "upper_price": "110.123456789012345",
            "upper_limit_price": "111.123456789012345",
        }
    )
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"]["position"] = pos
    shared.write_state(session, cfg, value)
    tracked_raw = executor(pos, custom={"state": "FAILED"})
    for field in (
        "lower_limit_price",
        "lower_price",
        "upper_price",
        "upper_limit_price",
    ):
        tracked_raw["config"][field] = float(pos[field])
    tracked_raw["config"]["keep_position"] = False
    tracked = shared.normalize_executor_response(tracked_raw)
    untracked_pos = position(session, pool="pool-x", executor="active-x", address="px")
    untracked = shared.normalize_executor_response(executor(untracked_pos))
    history_pos = position(session, pool="pool-y", executor="history-y", address="py")
    history = terminal_executor(history_pos)
    monkeypatch.setattr(shared, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(
        shared,
        "bound_client",
        lambda *args: async_value(shared.BoundClient("pinned", object())),
    )
    monkeypatch.setattr(
        shared,
        "search_controller_executors",
        lambda *args: async_value([tracked, untracked, history]),
    )
    monkeypatch.setattr(
        shared, "default_solana_wallet", lambda *args: async_value("wallet")
    )
    monkeypatch.setattr(
        shared,
        "get_gateway_pool",
        lambda *args: async_value(
            {
                "pool_address": "pool-1",
                "base_mint": "base-mint",
                "quote_mint": USDC,
                "current_price": Decimal(100),
            }
        ),
    )
    monkeypatch.setattr(
        shared,
        "get_owned_positions",
        lambda *args: async_value(
            [
                {
                    "position_address": "position-1",
                    "pool_address": "pool-1",
                    "raw": {},
                },
                {
                    "position_address": "other-session-position",
                    "pool_address": "pool-1",
                    "raw": {},
                },
            ]
        ),
    )
    result = json.loads(
        run(state.run(state.Config(controller_id=session.controller_id), None))
    )
    contradiction_types = {item["type"] for item in result["ownership_contradictions"]}
    assert {
        "executor_keep_position_conflict",
        "executor_custom_info_failed",
        "untracked_owned_executor",
    } <= contradiction_types
    assert result["historical_executors"][0]["executor_id"] == "history-y"
    assert {item["type"] for item in result["warnings"]} == {"virtual_position_hold"}
    assert "untracked_owned_position" not in contradiction_types
    assert not any(
        item.startswith("executor_") and item.endswith("_price_conflict")
        for item in contradiction_types
    )
    assert not any(
        item.get("executor_id") == "history-y"
        for item in result["ownership_contradictions"]
    )


def async_value(value):
    async def result(*args, **kwargs):
        return value

    return result()


def test_executor_unknown_terminal_and_exact_position_evidence_blockers(tmp_path):
    session, _ = make_session(tmp_path)
    pos = position(session)
    raw = executor(pos, active=False, status="MYSTERY")
    with pytest.raises(ValueError, match="unknown activity"):
        shared.normalize_executor_response(
            {key: value for key, value in raw.items() if key != "is_active"}
        )
    normalized = shared.normalize_executor_response(
        executor(pos, active=False, status="FAILED")
    )
    assert normalized["is_active"] is False
    pool = {"base_mint": "base-mint", "quote_mint": USDC}
    owned = [
        {
            "position_address": "position-1",
            "raw": {
                "lower_price": 90,
                "upper_price": 110,
                "lower_limit_price": 89,
                "upper_limit_price": 111,
            },
        }
    ]
    close._verify_position_evidence(
        pos, shared.normalize_executor_response(executor(pos)), pool, owned
    )
    for field in ("pool_address", "position_address"):
        bad = shared.normalize_executor_response(executor(pos))
        bad[field] = "wrong"
        with pytest.raises(ValueError):
            close._verify_position_evidence(pos, bad, pool, owned)


def test_open_budget_pool_bounds_price_and_sol_reserve_guards(tmp_path):
    session, cfg = make_session(tmp_path)
    value = shared.initial_state(cfg)
    open._assert_local_open(value, session, cfg, "slot-01", "pool-1", Decimal("10"))
    value["binding"] = binding(session)
    value["slots"]["slot-01"]["position"] = position(session, amount="10")
    for slot, pool, amount, message in [
        ("slot-02", "pool-2", "10.01", "free capital"),
        ("slot-02", "pool-1", "10", "already occupied"),
    ]:
        with pytest.raises(ValueError, match=message):
            open._assert_local_open(value, session, cfg, slot, pool, Decimal(amount))
    base = candidate()
    refreshed = copy.deepcopy(base)
    gateway = {
        "pool_address": "pool-1",
        "base_mint": "base-mint",
        "quote_mint": USDC,
        "current_price": Decimal("101"),
    }
    open._pool_checks(base, refreshed, gateway)
    gateway["current_price"] = Decimal("101.0000001")
    with pytest.raises(ValueError, match="exceeds 1%"):
        open._pool_checks(base, refreshed, gateway)
    balances = [
        {"symbol": "SOL", "mint": "base-mint", "available": Decimal(".06")},
        {"symbol": "USDC", "mint": USDC, "available": Decimal("100")},
    ]
    spendable, _, _ = open._validate_inventory(
        balances, base, Decimal(".01"), Decimal("1"), Decimal(".05")
    )
    assert spendable == Decimal(".01")


def test_open_derives_four_ordered_bounds_and_executor_payload_has_no_controller():
    assert shared.decimal_matches(0, 0)
    assert not shared.decimal_matches(0, "1e-30")
    bounds = open._bounds(Decimal("100"), Decimal(".03"))
    assert set(bounds) == {
        "lower_price",
        "upper_price",
        "lower_limit_price",
        "upper_limit_price",
    }
    assert (
        bounds["lower_limit_price"]
        < bounds["lower_price"]
        < 100
        < bounds["upper_price"]
        < bounds["upper_limit_price"]
    )
    plan = open._executor_config(
        candidate(), bounds, Decimal(".1"), Decimal("5"), Decimal("10"), True
    )
    assert (
        "controller_id" not in plan
        and plan["keep_position"] is True
        and plan["total_amount_quote"] == 10
    )
    pos = position(SimpleNamespace(controller_id="lp_wizard.orca_1"))
    raw = executor(pos)
    raw["config"].update(plan)
    expected = {"controller_id": pos["controller_id"], "executor_config": plan}
    assert shared.executor_plan_mismatches(raw, expected) == []
    for key in (
        "lower_price",
        "upper_price",
        "lower_limit_price",
        "upper_limit_price",
    ):
        raw["config"][key] = float(plan[key])
    raw["config"]["side"] = "RANGE"
    assert shared.executor_plan_mismatches(raw, expected) == []
    raw["config"]["lower_price"] += 0.001
    assert "lower_price" in shared.executor_plan_mismatches(raw, expected)
    raw["config"]["lower_price"] = float(plan["lower_price"])
    raw["config"]["total_amount_quote"] = "9"
    assert "total_amount_quote" in shared.executor_plan_mismatches(raw, expected)
    raw["config"]["total_amount_quote"] = plan["total_amount_quote"]
    raw["config"]["keep_position"] = False
    assert "keep_position" in shared.executor_plan_mismatches(raw, expected)


def test_claim_is_atomic_and_concurrent_slots_cannot_overspend(tmp_path, monkeypatch):
    session, cfg = make_session(tmp_path)
    barrier = threading.Barrier(2)
    outcomes = []

    def claim(slot, pool):
        barrier.wait()
        try:
            open._claim(
                session,
                cfg,
                slot,
                pool,
                Decimal("6"),
                binding(session),
                pending(pool=pool, amount="6"),
            )
            outcomes.append("ok")
        except ValueError as error:
            outcomes.append(str(error))

    threads = [
        threading.Thread(target=claim, args=("slot-01", "pool-1")),
        threading.Thread(target=claim, args=("slot-02", "pool-2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    persisted = shared.read_state(session, cfg)
    assert (
        outcomes.count("ok") == 1
        and sum(
            slot["pending_mutation"] is not None for slot in persisted["slots"].values()
        )
        == 1
    )


def test_open_persists_baselines_candidate_and_intent_before_create(
    monkeypatch, tmp_path
):
    session, cfg = make_session(tmp_path)
    other, other_cfg = make_session(tmp_path, 2)
    other_state = shared.initial_state(other_cfg)
    other_state["binding"] = binding(other)
    other_state["slots"]["slot-01"]["pending_mutation"] = pending()
    shared.write_state(other, other_cfg, other_state)
    row = candidate(now=datetime.now(timezone.utc))
    option = row["range_options"][0]
    events = []
    monkeypatch.setattr(shared, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(
        shared,
        "bound_client",
        lambda *args: async_value(
            shared.BoundClient("pinned", fake_open_client(events))
        ),
    )
    monkeypatch.setattr(
        shared, "default_solana_wallet", lambda *args: async_value("wallet")
    )
    monkeypatch.setattr(
        open, "_ownership_preflight", lambda *args: async_value(({"old-e"}, {"old-p"}))
    )
    live = {
        "bounds": open._bounds(
            Decimal(100), Decimal(str(option["provisional_half_width"]))
        ),
        "required_base": Decimal(".05"),
        "required_quote": Decimal("5"),
        "base_balance": Decimal("1"),
        "available_base": Decimal("1"),
        "available_quote": Decimal("100"),
        "available_sol": Decimal("1"),
        "gateway": {"current_price": Decimal(100)},
    }
    monkeypatch.setattr(open, "_live_preflight", lambda *args: async_value(live))
    monkeypatch.setattr(
        open,
        "_reconcile_create",
        lambda *args: async_value(
            (None, {"executor_ids_unchanged": True, "position_ids_unchanged": True})
        ),
    )
    result = json.loads(
        run(
            open.run(
                open.Config(
                    controller_id=session.controller_id,
                    slot_id="slot-01",
                    candidate=row,
                    range_option=option["name"],
                    amount_quote=5,
                ),
                None,
            )
        )
    )
    persisted = shared.read_state(session, cfg)["slots"]["slot-01"]["pending_mutation"]
    assert events == ["create"] and persisted["status"] == "uncertain"
    assert (
        persisted["request"]["baseline_executor_ids"] == ["old-e"]
        and persisted["request"]["candidate"] == row
    )
    assert result["recover_required"] is True


def fake_open_client(events):
    class Executors:
        async def get_executor_config_schema(self, *args):
            return {"properties": {}}

        async def create_executor(self, **kwargs):
            json.dumps(kwargs)
            events.append("create")
            return {"id": "new-e", "status": "accepted"}

    return SimpleNamespace(executors=Executors())


@pytest.mark.parametrize(
    ("second_required", "output", "second_balance", "reason"),
    [
        (
            Decimal(".10"),
            Decimal(".06"),
            Decimal(".11"),
            "does not match fresh base shortfall",
        ),
        (
            Decimal(".08"),
            Decimal(".05"),
            Decimal(".10"),
            "reduced the attributable base shortfall",
        ),
    ],
)
def test_rebalance_accounting_mismatch_restores_before_create(
    monkeypatch, tmp_path, second_required, output, second_balance, reason
):
    session, cfg = make_session(tmp_path)
    row = candidate(now=datetime.now(timezone.utc))
    option = row["range_options"][0]
    events = []
    initial = {
        "bounds": open._bounds(Decimal(100), Decimal(".01")),
        "required_base": Decimal(".10"),
        "required_quote": Decimal("5"),
        "base_balance": Decimal(".05"),
        "available_base": Decimal(".05"),
        "available_quote": Decimal("100"),
        "available_sol": Decimal("1"),
        "gateway": {"current_price": Decimal(100)},
    }
    refreshed = {
        **initial,
        "required_base": second_required,
        "base_balance": second_balance,
        "available_base": second_balance,
    }
    results = iter((initial, refreshed))

    class GatewaySwap:
        async def get_swap_quote(self, **kwargs):
            return {"input_amount": "5", "output_amount": ".05"}

        async def execute_swap(self, **kwargs):
            events.append("swap")
            return {"transaction_hash": "swap-1"}

    class Executors:
        async def get_executor_config_schema(self, _):
            return {"fields": []}

        async def create_executor(self, **kwargs):
            pytest.fail("create ran after rebalance accounting mismatch")

    client = SimpleNamespace(gateway_swap=GatewaySwap(), executors=Executors())
    monkeypatch.setattr(shared, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(
        shared,
        "bound_client",
        lambda *args: async_value(shared.BoundClient("pinned", client)),
    )
    monkeypatch.setattr(
        shared, "default_solana_wallet", lambda *args: async_value("wallet")
    )
    monkeypatch.setattr(
        open, "_ownership_preflight", lambda *args: async_value((set(), set()))
    )
    monkeypatch.setattr(
        open, "_live_preflight", lambda *args: async_value(next(results))
    )
    monkeypatch.setattr(
        open,
        "_confirmed_swap",
        lambda *args: async_value(
            {
                "transaction_hash": "swap-1",
                "status": "CONFIRMED",
                "input_amount": Decimal("5"),
                "output_amount": output,
                "side": "BUY",
                "trading_pair": "SOL-USDC",
            }
        ),
    )

    async def restore(*args):
        events.append("restore")
        assert args[7]["rebalance_accounting"]["fresh_shortfall"] == max(
            Decimal(0), second_required - Decimal(".05")
        )
        return {"transaction_hash": "restore-1"}

    monkeypatch.setattr(open, "_restore", restore)
    result = json.loads(
        run(
            open.run(
                open.Config(
                    controller_id=session.controller_id,
                    slot_id="slot-01",
                    candidate=row,
                    range_option=option["name"],
                    amount_quote=5,
                ),
                None,
            )
        )
    )
    assert (
        events == ["swap", "restore"]
        and result["status"] == "restored"
        and reason in result["reason"]
    )


def swap_request():
    return {
        "connector": "jupiter",
        "network": shared.NETWORK,
        "wallet_address": "wallet",
        "trading_pair": "SOL-USDC",
        "side": "BUY",
        "amount": ".05",
        "slippage_pct": "1",
        "base_mint": "base-mint",
        "quote_mint": USDC,
        "base_symbol": "SOL",
        "quote_symbol": "USDC",
    }


def swap_evidence(transaction_hash):
    return {
        "transaction_hash": transaction_hash,
        "status": "CONFIRMED",
        "timestamp": NOW.isoformat(),
        "connector": "jupiter",
        "network": shared.NETWORK,
        "wallet_address": "wallet",
        "trading_pair": "SOL-USDC",
        "side": "BUY",
        "input_amount": "5",
        "output_amount": ".05",
        "slippage_pct": "1",
        "base_mint": "base-mint",
        "quote_mint": USDC,
    }


@pytest.mark.parametrize("count", [0, 1, 2])
def test_find_submitted_swap_requires_unique_parameter_scoped_proof(count):
    calls = []
    rows = [swap_evidence(f"swap-{number}") for number in range(count)]

    class GatewaySwap:
        async def _post(self, path, params):
            calls.append((path, params))
            return {
                "data": rows,
                "pagination": {"offset": 0, "limit": 100, "total": count},
            }

        async def get_swap_status(self, transaction_hash):
            return next(
                row for row in rows if row["transaction_hash"] == transaction_hash
            )

    client = SimpleNamespace(gateway_swap=GatewaySwap())
    if count == 2:
        with pytest.raises(ValueError, match="ambiguous"):
            run(shared.find_submitted_swap(client, swap_request(), NOW.isoformat()))
    else:
        result = run(
            shared.find_submitted_swap(client, swap_request(), NOW.isoformat())
        )
        assert (
            (result is None) if count == 0 else result["transaction_hash"] == "swap-0"
        )
    path, params = calls[0]
    assert path == "/gateway/swaps/search"
    assert params == {
        "network": shared.NETWORK,
        "connector": "jupiter",
        "wallet_address": "wallet",
        "trading_pair": "SOL-USDC",
        "start_time": int((NOW - timedelta(seconds=60)).timestamp()),
        "end_time": int((NOW + timedelta(seconds=60)).timestamp()),
        "limit": 100,
        "offset": 0,
    }


def test_live_schema_fields_control_total_and_reject_malformed_names():
    assert open._schema_supports_total({"fields": [{"name": "pool_address"}]}) is False
    assert (
        open._schema_supports_total(
            {"result": {"fields": [{"name": "total_amount_quote"}]}}
        )
        is True
    )
    for schema in (
        {"fields": {}},
        {"fields": ["pool_address"]},
        {"fields": [{"name": ""}]},
        {"fields": [{"name": " pool_address"}]},
        {"fields": [{"name": "pool_address"}, {"name": "pool_address"}]},
    ):
        with pytest.raises(ValueError, match="field|duplicate"):
            open._schema_supports_total(schema)


def test_default_solana_wallet_reads_gateway_network_config():
    calls = []

    class Gateway:
        async def get_network_config(self, network):
            calls.append(network)
            return {"default_wallet": "solana-wallet"}

    assert (
        run(shared.default_solana_wallet(SimpleNamespace(gateway=Gateway())))
        == "solana-wallet"
    )
    assert calls == [shared.NETWORK]
    with pytest.raises(ValueError, match="default_wallet"):
        run(
            shared.default_solana_wallet(
                SimpleNamespace(
                    gateway=SimpleNamespace(
                        get_network_config=lambda network: async_value({})
                    )
                )
            )
        )


def test_full_pending_cas_rejects_request_only_plan_change(tmp_path):
    session, cfg = make_session(tmp_path)
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    expected = pending()
    expected["request"]["executor_config"] = {
        "pool_address": "pool-1",
        "base_amount": "1",
    }
    value["slots"]["slot-01"]["pending_mutation"] = copy.deepcopy(expected)
    shared.write_state(session, cfg, value)
    changed = shared.read_state(session, cfg)
    changed["slots"]["slot-01"]["pending_mutation"]["request"]["executor_config"][
        "base_amount"
    ] = "2"
    shared.write_state(session, cfg, changed)
    with pytest.raises(ValueError, match="changed concurrently"):
        close._checkpoint(session, cfg, "slot-01", expected, lambda state, slot: None)


def test_definitive_no_result_can_clear_but_timeout_is_not_definitive():
    class Response:
        status_code = 422

    error = RuntimeError("rejected")
    error.response = Response()
    assert open._definitive_create_error(error) is True
    assert open._definitive_create_error(TimeoutError()) is False


def test_definitive_create_error_accepts_direct_status_only_for_client_failures():
    class Error(Exception):
        def __init__(self, status):
            self.status = status

    assert open._definitive_create_error(Error(400)) is True
    assert open._definitive_create_error(Error(422)) is True
    assert open._definitive_create_error(Error(500)) is False
    assert open._definitive_create_error(TimeoutError()) is False


def test_definitive_create_failure_ignores_other_session_position_changes(
    monkeypatch, tmp_path
):
    session, cfg = make_session(tmp_path)
    row = candidate(now=datetime.now(timezone.utc))
    option = row["range_options"][0]

    class CreateRejected(Exception):
        status_code = 422

    class Executors:
        async def get_executor_config_schema(self, *args):
            return {"properties": {}}

        async def create_executor(self, **kwargs):
            raise CreateRejected("rejected")

    monkeypatch.setattr(shared, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(
        shared,
        "bound_client",
        lambda *args: async_value(
            shared.BoundClient("pinned", SimpleNamespace(executors=Executors()))
        ),
    )
    monkeypatch.setattr(
        shared, "default_solana_wallet", lambda *args: async_value("wallet")
    )
    monkeypatch.setattr(
        open,
        "_ownership_preflight",
        lambda *args: async_value((set(), {"existing-position"})),
    )
    live = {
        "bounds": open._bounds(
            Decimal(100), Decimal(str(option["provisional_half_width"]))
        ),
        "required_base": Decimal(".05"),
        "required_quote": Decimal("5"),
        "base_balance": Decimal("1"),
        "available_base": Decimal("1"),
        "available_quote": Decimal("100"),
        "available_sol": Decimal("1"),
        "gateway": {"current_price": Decimal(100)},
    }
    monkeypatch.setattr(open, "_live_preflight", lambda *args: async_value(live))
    monkeypatch.setattr(
        open,
        "_reconcile_create",
        lambda *args: async_value(
            (None, {"executor_ids_unchanged": True, "position_ids_unchanged": False})
        ),
    )

    result = json.loads(
        run(
            open.run(
                open.Config(
                    controller_id=session.controller_id,
                    slot_id="slot-01",
                    candidate=row,
                    range_option=option["name"],
                    amount_quote=5,
                ),
                None,
            )
        )
    )

    assert result["status"] == "failed"
    assert result["recover_required"] is False
    assert shared.read_state(session, cfg)["slots"]["slot-01"] == {
        "position": None,
        "pending_mutation": None,
    }


def test_cancelled_create_persists_uncertain_without_retry(monkeypatch, tmp_path):
    session, cfg = make_session(tmp_path)
    row = candidate(now=datetime.now(timezone.utc))
    option = row["range_options"][0]
    calls = []

    class Executors:
        async def get_executor_config_schema(self, _):
            return {"fields": []}

        async def create_executor(self, **kwargs):
            calls.append(kwargs)
            raise asyncio.CancelledError

    monkeypatch.setattr(shared, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(
        shared,
        "bound_client",
        lambda *args: async_value(
            shared.BoundClient("pinned", SimpleNamespace(executors=Executors()))
        ),
    )
    monkeypatch.setattr(
        shared, "default_solana_wallet", lambda *args: async_value("wallet")
    )
    monkeypatch.setattr(
        open, "_ownership_preflight", lambda *args: async_value((set(), set()))
    )
    live = {
        "bounds": open._bounds(Decimal(100), Decimal(".01")),
        "required_base": Decimal(".05"),
        "required_quote": Decimal("5"),
        "base_balance": Decimal("1"),
        "available_base": Decimal("1"),
        "available_quote": Decimal("100"),
        "available_sol": Decimal("1"),
        "gateway": {"current_price": Decimal(100)},
    }
    monkeypatch.setattr(open, "_live_preflight", lambda *args: async_value(live))
    with pytest.raises(asyncio.CancelledError):
        run(
            open.run(
                open.Config(
                    controller_id=session.controller_id,
                    slot_id="slot-01",
                    candidate=row,
                    range_option=option["name"],
                    amount_quote=5,
                ),
                None,
            )
        )
    persisted = shared.read_state(session, cfg)["slots"]["slot-01"]["pending_mutation"]
    assert (
        len(calls) == 1
        and persisted["step"] == "create"
        and persisted["status"] == "uncertain"
    )


@pytest.mark.parametrize("error", [TimeoutError("timeout"), asyncio.CancelledError()])
def test_close_persists_keep_position_true_before_one_stop_and_is_recoverable(
    monkeypatch, tmp_path, error
):
    session, cfg = make_session(tmp_path)
    other, other_cfg = make_session(tmp_path, 2)
    other_state = shared.initial_state(other_cfg)
    other_state["binding"] = binding(other)
    other_state["slots"]["slot-01"]["pending_mutation"] = pending()
    shared.write_state(other, other_cfg, other_state)
    pos = position(session)
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"]["position"] = pos
    shared.write_state(session, cfg, value)
    calls = []

    class Executors:
        async def stop_executor(self, **kwargs):
            current = shared.read_state(session, cfg)["slots"]["slot-01"][
                "pending_mutation"
            ]
            assert current["status"] == "submitted"
            assert current["request"]["keep_position"] is True
            calls.append(kwargs)
            raise error

    client = SimpleNamespace(executors=Executors())
    monkeypatch.setattr(shared, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(
        shared,
        "bound_client",
        lambda *args: async_value(shared.BoundClient("pinned", client)),
    )
    monkeypatch.setattr(
        shared, "default_solana_wallet", lambda *args: async_value("wallet")
    )
    monkeypatch.setattr(
        shared,
        "get_executor_evidence",
        lambda *args: async_value(shared.normalize_executor_response(executor(pos))),
    )
    monkeypatch.setattr(
        shared,
        "get_gateway_pool",
        lambda *args: async_value(
            {
                "pool_address": "pool-1",
                "base_mint": "base-mint",
                "quote_mint": USDC,
                "current_price": Decimal(100),
            }
        ),
    )
    monkeypatch.setattr(
        shared,
        "get_owned_positions",
        lambda *args: async_value([{"position_address": "position-1", "raw": {}}]),
    )
    call = close.run(
        close.Config(controller_id=session.controller_id, slot_id="slot-01"), None
    )
    if isinstance(error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            run(call)
    else:
        assert json.loads(run(call))["status"] == "recoverable"
    persisted = shared.read_state(session, cfg)["slots"]["slot-01"]["pending_mutation"]
    assert calls == [{"executor_id": "executor-1", "keep_position": True}]
    assert persisted["status"] == "uncertain"
    with pytest.raises(ValueError, match="eligible for a first submission"):
        run(
            recover._submit_intent(
                recover.Config(controller_id=session.controller_id, slot_id="slot-01"),
                session,
                cfg,
                client,
                "wallet",
                persisted["operation_id"],
            )
        )
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("available", "expected"), [(".5", "closed"), (".02", "manual_blocked")]
)
def test_returned_inventory_restores_exact_base_total_not_wallet_balance(
    monkeypatch, tmp_path, available, expected
):
    session, cfg = make_session(tmp_path)
    pos = position(session)
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"] = {
        "position": pos,
        "pending_mutation": local_stop_pending(pos),
    }
    shared.write_state(session, cfg, value)
    receipt = terminal_executor(pos)
    swaps, persisted_receipts = [], []

    class GatewaySwap:
        async def get_swap_quote(self, **kwargs):
            return {"input_amount": ".021", "output_amount": "2.1"}

        async def execute_swap(self, **kwargs):
            swaps.append(kwargs)
            current = shared.read_state(session, cfg)["slots"]["slot-01"][
                "pending_mutation"
            ]
            persisted_receipts.append(
                copy.deepcopy(current["confirmed"]["returned_inventory"])
            )
            return {"transaction_hash": "restore-1"}

    client = SimpleNamespace(gateway_swap=GatewaySwap())
    monkeypatch.setattr(
        shared, "get_executor_evidence", lambda *args: async_value(receipt)
    )
    monkeypatch.setattr(
        shared,
        "get_gateway_pool",
        lambda *args: async_value(
            {
                "pool_address": "pool-1",
                "base_mint": "base-mint",
                "quote_mint": USDC,
                "current_price": Decimal(100),
            }
        ),
    )
    monkeypatch.setattr(shared, "get_owned_positions", lambda *args: async_value([]))
    monkeypatch.setattr(
        shared,
        "read_balances",
        lambda *args: async_value(
            [{"symbol": "SOL", "mint": "base-mint", "available": Decimal(available)}]
        ),
    )
    monkeypatch.setattr(
        shared,
        "get_swap_evidence",
        lambda *args: async_value(
            {
                "transaction_hash": "restore-1",
                "status": "CONFIRMED",
                "input_amount": Decimal(".021"),
                "output_amount": Decimal("2.1"),
                "slippage_pct": Decimal(1),
                "connector": "jupiter",
                "network": shared.NETWORK,
                "wallet_address": "wallet",
                "trading_pair": f"base-mint-{USDC}",
                "side": "SELL",
                "base_mint": "base-mint",
                "quote_mint": USDC,
                "base_token": None,
                "quote_token": None,
            }
        ),
    )
    result = json.loads(
        run(
            close._reconcile_close(
                close.Config(controller_id=session.controller_id, slot_id="slot-01"),
                session,
                cfg,
                client,
                "wallet",
                "operation-1",
            )
        )
    )
    assert result["status"] == expected
    if expected == "closed":
        assert swaps[0]["amount"] == Decimal(".021")
        assert Decimal(persisted_receipts[0]["base_total"]) == Decimal(".021")
        assert Decimal(persisted_receipts[0]["quote_total"]) == Decimal("5.01")
        assert result["returned_inventory"] == persisted_receipts[0]
        assert shared.read_state(session, cfg) is not None
        assert shared.read_state(session, cfg)["slots"]["slot-01"] == {
            "position": None,
            "pending_mutation": None,
        }
    else:
        assert swaps == []


def test_dust_return_clears_only_selected_slot_without_swap(monkeypatch, tmp_path):
    session, cfg = make_session(tmp_path)
    first, second = position(session), position(
        session, pool="pool-2", executor="e2", address="p2"
    )
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"] = {
        "position": first,
        "pending_mutation": local_stop_pending(first),
    }
    value["slots"]["slot-02"]["position"] = second
    shared.write_state(session, cfg, value)
    monkeypatch.setattr(
        shared,
        "get_executor_evidence",
        lambda *args: async_value(
            terminal_executor(first, base_amount=".00001", base_fee="0")
        ),
    )
    monkeypatch.setattr(
        shared,
        "get_gateway_pool",
        lambda *args: async_value(
            {
                "pool_address": "pool-1",
                "base_mint": "base-mint",
                "quote_mint": USDC,
                "current_price": Decimal(100),
            }
        ),
    )
    monkeypatch.setattr(shared, "get_owned_positions", lambda *args: async_value([]))
    monkeypatch.setattr(
        close,
        "_submit_restore",
        lambda *args: pytest.fail("dust return submitted a swap"),
    )
    result = json.loads(
        run(
            close._reconcile_close(
                close.Config(controller_id=session.controller_id, slot_id="slot-01"),
                session,
                cfg,
                object(),
                "wallet",
                "operation-1",
            )
        )
    )
    persisted = shared.read_state(session, cfg)
    assert result["status"] == "closed" and result["restoration"] == "not_required"
    assert persisted["slots"]["slot-01"]["position"] is None
    assert persisted["slots"]["slot-02"]["position"] == second


def test_two_distinct_slots_and_one_slot_clear_preserves_the_other(tmp_path):
    session, cfg = make_session(tmp_path)
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"]["position"] = position(session)
    value["slots"]["slot-02"]["position"] = position(
        session, pool="pool-2", executor="e2", address="p2"
    )
    close._clear_selected(value, value["slots"]["slot-01"])
    assert value["slots"]["slot-01"]["position"] is None
    assert (
        value["slots"]["slot-02"]["position"]["executor_id"] == "e2"
        and value["binding"] is not None
    )


def test_active_and_terminal_position_evidence_have_distinct_ownership_contracts(
    tmp_path,
):
    session, _ = make_session(tmp_path)
    pos = position(session)
    pool = {"base_mint": "base-mint", "quote_mint": USDC}
    owned = [{"position_address": pos["position_address"], "raw": {}}]
    active = shared.normalize_executor_response(executor(pos))
    close._verify_position_evidence(pos, active, pool, owned)
    snapped = copy.deepcopy(owned)
    snapped[0]["raw"] = {"lower_price": "90.0001", "upper_price": "109.9998"}
    close._verify_position_evidence(pos, active, pool, snapped)
    snapped[0]["raw"] = {"lower_price": "95", "upper_price": "105"}
    with pytest.raises(ValueError, match="deviates"):
        close._verify_position_evidence(pos, active, pool, snapped)
    snapped[0]["raw"]["lower_price"] = "88"
    with pytest.raises(ValueError, match="safety limits"):
        close._verify_position_evidence(pos, active, pool, snapped)
    with pytest.raises(ValueError, match="uniquely owned"):
        close._verify_position_evidence(pos, active, pool, [])
    active["position_address"] = "wrong"
    with pytest.raises(ValueError, match="position address"):
        close._verify_position_evidence(pos, active, pool, owned)

    terminal = terminal_executor(pos)
    close._verify_position_evidence(pos, terminal, pool, [], terminal=True)
    with pytest.raises(ValueError, match="still owned"):
        close._verify_position_evidence(pos, terminal, pool, owned, terminal=True)
    terminal["position_address"] = pos["position_address"]
    with pytest.raises(ValueError, match="not cleared"):
        close._verify_position_evidence(pos, terminal, pool, [], terminal=True)

    omitted_raw = executor(pos, active=False, status="COMPLETED")
    omitted_raw.pop("position_address")
    omitted = shared.normalize_executor_response(omitted_raw)
    assert (
        omitted["position_address"] is None
        and omitted["position_address_present"] is False
    )
    with pytest.raises(ValueError, match="cleared explicitly"):
        close._verify_position_evidence(pos, omitted, pool, [], terminal=True)
    assert terminal["position_address"] == pos["position_address"]
    explicit = terminal_executor(pos)
    assert (
        explicit["position_address"] is None
        and explicit["position_address_present"] is True
    )
    close._verify_position_evidence(pos, explicit, pool, [], terminal=True)

    delayed_raw = executor(pos)
    delayed_raw["config"]["controller_id"] = "main"
    delayed_raw["position_address"] = None
    delayed_raw["cum_fees_quote"] = ".01"
    delayed_raw["filled_amount_quote"] = "5"
    for key in (
        "lower_price",
        "upper_price",
        "lower_limit_price",
        "upper_limit_price",
    ):
        delayed_raw["config"][key] = float(delayed_raw["config"][key])
    delayed_raw["config"]["side"] = "RANGE"
    delayed_raw["custom_info"] = {
        "position_address": pos["position_address"],
        "fees_earned_quote": ".02",
        "filled_amount_quote": "2.5",
    }
    delayed = shared.normalize_executor_response(
        delayed_raw, expected_controller_id=session.controller_id
    )
    assert delayed["controller_id"] == session.controller_id
    assert delayed["position_address"] == pos["position_address"]
    assert delayed["cum_fees_quote"] == Decimal(".01")
    assert delayed["fees_earned_quote"] == Decimal(".02")
    assert delayed["filled_amount_quote"] == Decimal("5")
    close._verify_position_evidence(pos, delayed, pool, owned)
    missing_aggregate = copy.deepcopy(delayed_raw)
    missing_aggregate.pop("filled_amount_quote")
    missing_aggregate["custom_info"].pop("fees_earned_quote")
    normalized_missing = shared.normalize_executor_response(missing_aggregate)
    assert normalized_missing["filled_amount_quote"] is None
    assert normalized_missing["fees_earned_quote"] is None


@pytest.mark.parametrize(
    ("price", "expected"), [("111", "closed"), ("100", "manual_blocked")]
)
def test_unattempted_stop_intent_requires_native_persisted_limit(
    monkeypatch, tmp_path, price, expected
):
    session, cfg = make_session(tmp_path)
    pos = position(session)
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"] = {
        "position": pos,
        "pending_mutation": local_stop_pending(pos, "intent"),
    }
    shared.write_state(session, cfg, value)
    monkeypatch.setattr(
        shared,
        "get_executor_evidence",
        lambda *args: async_value(
            terminal_executor(
                pos,
                price=price,
                base_amount="0",
                base_fee="0",
                quote_amount="5",
            )
        ),
    )
    monkeypatch.setattr(
        shared,
        "get_gateway_pool",
        lambda *args: async_value(
            {
                "pool_address": "pool-1",
                "base_mint": "base-mint",
                "quote_mint": USDC,
                "current_price": Decimal(100),
            }
        ),
    )
    monkeypatch.setattr(shared, "get_owned_positions", lambda *args: async_value([]))
    monkeypatch.setattr(
        close,
        "_submit_restore",
        lambda *args: pytest.fail("quote-only receipt swapped"),
    )
    result = json.loads(
        run(
            close._reconcile_close(
                close.Config(controller_id=session.controller_id, slot_id="slot-01"),
                session,
                cfg,
                object(),
                "wallet",
                "operation-1",
            )
        )
    )
    assert result["status"] == expected


@pytest.mark.parametrize(
    "mutate",
    [
        lambda request: request.pop("executor_id"),
        lambda request: request.__setitem__("position_address", "other"),
        lambda request: request.__setitem__("keep_position", False),
    ],
)
def test_malformed_or_mismatched_stop_provenance_blocks(tmp_path, mutate):
    session, _ = make_session(tmp_path)
    pos = position(session)
    mutation = local_stop_pending(pos)
    mutate(mutation["request"])
    with pytest.raises(ValueError, match="provenance contradicts"):
        close._local_stop_provenance(pos, mutation)


def test_position_hold_receipt_totals_principal_and_fees(tmp_path):
    session, _ = make_session(tmp_path)
    receipt = close._returned_inventory(terminal_executor(position(session)))
    assert receipt["base_total"] == Decimal(".021")
    assert receipt["quote_total"] == Decimal("5.01")
    assert receipt["close_type"] == "POSITION_HOLD"


def test_confirmed_restore_accepts_mint_tokens_but_rejects_unrelated_identity():
    request = {
        "amount": ".02",
        "slippage_pct": "1",
        "wallet_address": "wallet",
        "trading_pair": f"base-mint-{USDC}",
        "base_mint": "base-mint",
        "base_symbol": "SOL",
        "quote_mint": USDC,
        "quote_symbol": "USDC",
    }
    evidence = {
        "status": "CONFIRMED",
        "side": "SELL",
        "trading_pair": request["trading_pair"],
        "input_amount": Decimal(".02"),
        "output_amount": Decimal("2"),
        "connector": "jupiter",
        "network": shared.NETWORK,
        "wallet_address": "wallet",
        "slippage_pct": Decimal("1"),
        "base_mint": None,
        "quote_mint": None,
        "base_token": "base-mint",
        "quote_token": USDC,
    }
    assert close._transaction_matches(evidence, request) is True
    evidence["quote_token"] = "unrelated-mint"
    assert close._transaction_matches(evidence, request) is False


@pytest.mark.parametrize(
    "changes",
    [
        {"base_fee": None},
        {"base_amount": "nan"},
        {"quote_fee": "-1"},
        {"base_amount": "0", "base_fee": "0", "quote_amount": "0", "quote_fee": "0"},
        {"state": "FAILED"},
        {"close_type": "EARLY_STOP"},
    ],
)
def test_invalid_terminal_receipts_block(tmp_path, changes):
    session, _ = make_session(tmp_path)
    kwargs = {key: value for key, value in changes.items() if value is not None}
    receipt = terminal_executor(position(session), **kwargs)
    if any(value is None for value in changes.values()):
        receipt["custom_info"].pop("base_fee")
    with pytest.raises(ValueError):
        close._returned_inventory(receipt)


def test_conflicting_terminal_close_type_blocks_normalization(tmp_path):
    session, _ = make_session(tmp_path)
    raw = executor(position(session), active=False, status="COMPLETED")
    raw["close_type"] = "POSITION_HOLD"
    raw["custom_info"] = {"close_type": "EARLY_STOP"}
    with pytest.raises(ValueError, match="conflicting"):
        shared.normalize_executor_response(raw)


def test_recover_baselines_exact_match_counts_and_fill_proof():
    request = {
        "baseline_executor_ids": ["old"],
        "baseline_position_ids": ["position-old"],
    }
    assert recover._baseline_sets(request) == ({"old"}, {"position-old"})
    for key in ("baseline_executor_ids", "baseline_position_ids"):
        bad = copy.deepcopy(request)
        bad[key] = ["same", "same"]
        with pytest.raises(ValueError, match="duplicates"):
            recover._baseline_sets(bad)
    failed = {
        "is_active": False,
        "status": "FAILED",
        "position_address": None,
        "filled_amount_quote": Decimal(0),
        "is_trading": False,
        "custom_info": {"filled_amount_base": 0, "filled_amount_quote": 0},
    }
    assert recover._failed_before_open(failed) is True
    failed["custom_info"]["filled_amount_quote"] = Decimal(".01")
    assert recover._failed_before_open(failed) is False
    failed["custom_info"]["filled_amount_quote"] = 0
    failed["filled_amount_quote"] = Decimal(".01")
    assert recover._failed_before_open(failed) is False


def test_recover_create_rejects_position_from_baseline(monkeypatch, tmp_path):
    session, cfg = make_session(tmp_path)
    existing_position = position(session, executor="new-executor", address="existing")
    matching_executor = shared.normalize_executor_response(executor(existing_position))
    mutation = pending(status="uncertain")
    mutation["request"] = {
        "controller_id": session.controller_id,
        "amount_quote": "5",
        "pool_address": "pool-1",
        "baseline_executor_ids": [],
        "baseline_position_ids": ["existing"],
        "executor_config": matching_executor["config"],
    }
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"]["pending_mutation"] = mutation
    shared.write_state(session, cfg, value)
    monkeypatch.setattr(
        shared,
        "search_controller_executors",
        lambda *args: async_value([matching_executor]),
    )
    monkeypatch.setattr(
        shared,
        "get_owned_positions",
        lambda *args: async_value(
            [{"position_address": "existing", "pool_address": "pool-1"}]
        ),
    )

    result = json.loads(
        run(
            recover._reconcile_create(
                recover.Config(controller_id=session.controller_id, slot_id="slot-01"),
                session,
                cfg,
                object(),
                "wallet",
                "operation-1",
            )
        )
    )

    assert result["status"] == "manual_blocked"
    assert "predates this create" in result["error"]
    assert shared.read_state(session, cfg)["slots"]["slot-01"]["position"] is None


def test_recover_adopts_delayed_executor_with_backend_default_config_controller(
    monkeypatch, tmp_path
):
    session, cfg = make_session(tmp_path)
    position_address = "delayed-position"
    position_record = position(
        session, executor="delayed-executor", address=position_address
    )
    raw_executor = executor(position_record)
    persisted_executor_config = {
        key: value
        for key, value in raw_executor["config"].items()
        if key != "controller_id"
    }
    raw_executor["config"]["controller_id"] = "main"
    raw_executor["position_address"] = None
    raw_executor["cum_fees_quote"] = ".01"
    raw_executor["filled_amount_quote"] = "5"
    for key in (
        "lower_price",
        "upper_price",
        "lower_limit_price",
        "upper_limit_price",
    ):
        raw_executor["config"][key] = float(raw_executor["config"][key])
    raw_executor["config"]["side"] = "RANGE"
    raw_executor["custom_info"] = {
        "position_address": position_address,
        "fees_earned_quote": ".02",
        "filled_amount_quote": "2.5",
    }
    mutation = pending(status="uncertain")
    mutation["external_id"] = "delayed-executor"
    mutation["request"] = {
        "controller_id": session.controller_id,
        "amount_quote": "5",
        "pool_address": "pool-1",
        "base_mint": "base-mint",
        "quote_mint": USDC,
        "baseline_executor_ids": [],
        "baseline_position_ids": [],
        "executor_config": persisted_executor_config,
    }
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"]["pending_mutation"] = mutation
    shared.write_state(session, cfg, value)

    class Executors:
        async def search_executors(self, **kwargs):
            assert kwargs["controller_ids"] == [session.controller_id]
            return [raw_executor]

    monkeypatch.setattr(
        shared,
        "get_owned_positions",
        lambda *args: async_value(
            [
                {
                    "position_address": position_address,
                    "pool_address": "pool-1",
                    "raw": {},
                }
            ]
        ),
    )
    monkeypatch.setattr(
        shared,
        "get_gateway_pool",
        lambda *args: async_value(
            {
                "pool_address": "pool-1",
                "base_mint": "base-mint",
                "quote_mint": USDC,
                "current_price": Decimal("100"),
            }
        ),
    )

    result = json.loads(
        run(
            recover._reconcile_create(
                recover.Config(controller_id=session.controller_id, slot_id="slot-01"),
                session,
                cfg,
                SimpleNamespace(executors=Executors()),
                "wallet",
                "operation-1",
            )
        )
    )

    persisted = shared.read_state(session, cfg)["slots"]["slot-01"]
    assert result["status"] == "recovered"
    assert persisted["pending_mutation"] is None
    assert persisted["position"]["executor_id"] == "delayed-executor"
    assert persisted["position"]["position_address"] == position_address


@pytest.mark.parametrize("mutation_status", ["submitted", "uncertain"])
def test_recover_submitted_or_uncertain_never_resubmits(
    monkeypatch, tmp_path, mutation_status
):
    session, cfg = make_session(tmp_path)
    other, other_cfg = make_session(tmp_path, 2)
    other_state = shared.initial_state(other_cfg)
    other_state["binding"] = binding(other)
    other_state["slots"]["slot-01"]["pending_mutation"] = pending()
    shared.write_state(other, other_cfg, other_state)
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"]["pending_mutation"] = pending(status=mutation_status)
    shared.write_state(session, cfg, value)
    monkeypatch.setattr(shared, "STRATEGIES_DIR", tmp_path)
    monkeypatch.setattr(
        shared,
        "bound_client",
        lambda *args: async_value(shared.BoundClient("pinned", object())),
    )
    monkeypatch.setattr(
        shared, "default_solana_wallet", lambda *args: async_value("wallet")
    )
    monkeypatch.setattr(
        recover,
        "_submit_intent",
        lambda *args: pytest.fail("submitted mutation resubmitted"),
    )
    monkeypatch.setattr(
        recover, "_reconcile_create", lambda *args: async_value("reconciled")
    )
    assert (
        run(
            recover.run(
                recover.Config(controller_id=session.controller_id, slot_id="slot-01"),
                None,
            )
        )
        == "reconciled"
    )


@pytest.mark.parametrize("mode", ["no_hash", "discovered_hash", "known_hash"])
def test_restore_hash_recovery_never_resubmits(monkeypatch, tmp_path, mode):
    session, cfg = make_session(tmp_path)
    mutation = pending("close", "restore", "uncertain")
    mutation["external_id"] = "known" if mode == "known_hash" else None
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"]["position"] = position(session)
    value["slots"]["slot-01"]["pending_mutation"] = mutation
    shared.write_state(session, cfg, value)
    searches, submissions = [], []

    async def find(*args):
        searches.append(args)
        return {"transaction_hash": "found"} if mode == "discovered_hash" else None

    monkeypatch.setattr(shared, "find_submitted_swap", find)
    client = SimpleNamespace(
        gateway_swap=SimpleNamespace(
            execute_swap=lambda **kwargs: submissions.append(kwargs)
        )
    )
    updated, blocked = run(
        recover._recover_swap_hash(
            recover.Config(controller_id=session.controller_id, slot_id="slot-01"),
            session,
            cfg,
            client,
            mutation,
        )
    )
    assert submissions == []
    if mode == "no_hash":
        assert updated is None and json.loads(blocked)["status"] == "recoverable"
    else:
        assert updated["external_id"] == (
            "found" if mode == "discovered_hash" else "known"
        )
    assert len(searches) == (0 if mode == "known_hash" else 1)


def test_successful_recover_returns_exact_inventory_after_state_clear(
    monkeypatch, tmp_path
):
    session, cfg = make_session(tmp_path)
    pos = position(session)
    mutation = returned_restore_pending(pos)
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"] = {"position": pos, "pending_mutation": mutation}
    shared.write_state(session, cfg, value)
    evidence = {
        "transaction_hash": "tx-1",
        "status": "CONFIRMED",
        "input_amount": Decimal(".021"),
        "output_amount": Decimal("2.1"),
        "slippage_pct": Decimal(1),
        "connector": "jupiter",
        "network": shared.NETWORK,
        "wallet_address": "wallet",
        "trading_pair": f"base-mint-{USDC}",
        "side": "SELL",
        "base_mint": "base-mint",
        "quote_mint": USDC,
        "base_token": None,
        "quote_token": None,
    }
    monkeypatch.setattr(
        shared, "get_swap_evidence", lambda *args: async_value(evidence)
    )
    result = json.loads(
        run(
            recover._reconcile_restore(
                recover.Config(controller_id=session.controller_id, slot_id="slot-01"),
                session,
                cfg,
                object(),
                "wallet",
                "operation-1",
            )
        )
    )
    assert result["status"] == "recovered"
    assert result["returned_inventory"] == mutation["confirmed"]["returned_inventory"]
    assert shared.read_state(session, cfg)["slots"]["slot-01"] == {
        "position": None,
        "pending_mutation": None,
    }


def test_restore_timeout_persists_uncertain_and_cannot_resubmit(monkeypatch, tmp_path):
    session, cfg = make_session(tmp_path)
    pos = position(session)
    mutation = pending("close", "restore")
    mutation["request"] = {
        "pool_address": "pool-1",
        "position_address": "position-1",
        "executor_id": "executor-1",
        "restore_base_amount": ".021",
        "restore_swap": {
            "connector": "jupiter",
            "network": shared.NETWORK,
            "trading_pair": f"base-mint-{USDC}",
            "side": "SELL",
            "amount": ".021",
            "slippage_pct": "1",
            "wallet_address": "wallet",
            "base_mint": "base-mint",
            "quote_mint": USDC,
        },
    }
    mutation["confirmed"] = {
        "returned_inventory": {
            "position_address": "position-1",
            "base_amount": ".02",
            "base_fee": ".001",
            "quote_amount": "5",
            "quote_fee": ".01",
            "base_total": ".021",
            "quote_total": "5.01",
            "source": "executor_close_receipt",
            "executor_status": "COMPLETED",
            "close_type": "POSITION_HOLD",
        }
    }
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"] = {"position": pos, "pending_mutation": mutation}
    shared.write_state(session, cfg, value)
    calls = []

    class GatewaySwap:
        async def get_swap_quote(self, **kwargs):
            return {"input_amount": ".021", "output_amount": "2.1"}

        async def execute_swap(self, **kwargs):
            calls.append(kwargs)
            raise TimeoutError("timeout")

    client = SimpleNamespace(gateway_swap=GatewaySwap())
    monkeypatch.setattr(
        shared,
        "get_gateway_pool",
        lambda *args: async_value(
            {
                "pool_address": "pool-1",
                "base_mint": "base-mint",
                "quote_mint": USDC,
                "current_price": Decimal(100),
            }
        ),
    )
    monkeypatch.setattr(
        shared,
        "read_balances",
        lambda *args: async_value(
            [{"symbol": "SOL", "mint": "base-mint", "available": Decimal("1")}]
        ),
    )
    result = json.loads(
        run(
            close._submit_restore(
                close.Config(controller_id=session.controller_id, slot_id="slot-01"),
                session,
                cfg,
                client,
                "operation-1",
            )
        )
    )
    persisted = shared.read_state(session, cfg)["slots"]["slot-01"]["pending_mutation"]
    assert result["status"] == "recoverable" and len(calls) == 1
    assert persisted["status"] == "uncertain" and persisted["external_id"] is None
    with pytest.raises(ValueError, match="eligible for a first submission"):
        run(
            recover._submit_intent(
                recover.Config(controller_id=session.controller_id, slot_id="slot-01"),
                session,
                cfg,
                client,
                "wallet",
                "operation-1",
            )
        )
    assert len(calls) == 1


@pytest.mark.parametrize(("available", "expected_calls"), [(".071", 1), (".070", 0)])
def test_wrapped_sol_restore_preserves_exact_minimum_reserve(
    monkeypatch, tmp_path, available, expected_calls
):
    session, cfg = make_session(tmp_path)
    pos = position(session)
    pos["base_mint"] = shared.WRAPPED_SOL_MINT
    mutation = returned_restore_pending(pos, status="intent", external_id=None)
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"] = {"position": pos, "pending_mutation": mutation}
    shared.write_state(session, cfg, value)
    calls = []

    class GatewaySwap:
        async def get_swap_quote(self, **kwargs):
            return {"input_amount": ".021", "output_amount": "2.1"}

        async def execute_swap(self, **kwargs):
            calls.append(kwargs)
            raise TimeoutError("stop after exact amount assertion")

    client = SimpleNamespace(gateway_swap=GatewaySwap())
    monkeypatch.setattr(
        shared,
        "get_gateway_pool",
        lambda *args: async_value(
            {
                "pool_address": "pool-1",
                "base_mint": shared.WRAPPED_SOL_MINT,
                "quote_mint": USDC,
                "current_price": Decimal(100),
            }
        ),
    )
    monkeypatch.setattr(
        shared,
        "read_balances",
        lambda *args: async_value(
            [
                {
                    "symbol": "SOL",
                    "mint": shared.WRAPPED_SOL_MINT,
                    "available": Decimal(available),
                }
            ]
        ),
    )
    result = json.loads(
        run(
            close._submit_restore(
                close.Config(controller_id=session.controller_id, slot_id="slot-01"),
                session,
                cfg,
                client,
                "operation-1",
            )
        )
    )
    assert len(calls) == expected_calls
    if calls:
        assert calls[0]["amount"] == Decimal(".021")
        assert result["status"] == "recoverable"
    else:
        assert result["status"] == "manual_blocked"
        assert "minimum reserve" in result["error"]


def test_recover_intent_cas_allows_only_one_concurrent_submission(
    monkeypatch, tmp_path
):
    session, cfg = make_session(tmp_path)
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"]["position"] = position(session)
    mutation = pending("close", "stop")
    mutation["request"] = {
        "executor_id": "executor-1",
        "controller_id": session.controller_id,
        "pool_address": "pool-1",
        "position_address": "position-1",
        "keep_position": True,
    }
    value["slots"]["slot-01"]["pending_mutation"] = mutation
    shared.write_state(session, cfg, value)
    calls = []
    monkeypatch.setattr(
        shared,
        "get_executor_evidence",
        lambda *args: async_value(
            shared.normalize_executor_response(executor(position(session)))
        ),
    )
    monkeypatch.setattr(
        shared,
        "get_gateway_pool",
        lambda *args: async_value(
            {
                "pool_address": "pool-1",
                "base_mint": "base-mint",
                "quote_mint": USDC,
                "current_price": Decimal(100),
            }
        ),
    )
    monkeypatch.setattr(
        shared,
        "get_owned_positions",
        lambda *args: async_value(
            [
                {
                    "position_address": "position-1",
                    "raw": {
                        "lower_price": 90,
                        "upper_price": 110,
                        "lower_limit_price": 89,
                        "upper_limit_price": 111,
                    },
                }
            ]
        ),
    )

    class Executors:
        async def stop_executor(self, **kwargs):
            calls.append(kwargs)
            await asyncio.sleep(0.05)
            return {"ok": True}

    client = SimpleNamespace(executors=Executors())
    monkeypatch.setattr(close, "_reconcile_close", lambda *args: async_value("done"))
    routine_config = recover.Config(
        controller_id=session.controller_id, slot_id="slot-01"
    )

    async def concurrent():
        return await asyncio.gather(
            *(
                recover._submit_intent(
                    routine_config, session, cfg, client, "wallet", "operation-1"
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )

    results = run(concurrent())
    assert (
        len(calls) == 1
        and results.count("done") == 1
        and sum(isinstance(item, ValueError) for item in results) == 1
    )


@pytest.mark.parametrize(
    ("price", "close_type", "expected"),
    [
        ("111", "POSITION_HOLD", "recovered"),
        ("100", "POSITION_HOLD", "manual_blocked"),
        ("111", "EARLY_STOP", "manual_blocked"),
    ],
)
def test_native_terminal_requires_position_hold_at_persisted_limit(
    monkeypatch, tmp_path, price, close_type, expected
):
    session, cfg = make_session(tmp_path)
    pos = position(session)
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"]["position"] = pos
    shared.write_state(session, cfg, value)
    terminal = terminal_executor(
        pos,
        price=price,
        close_type=close_type,
        base_amount="0",
        base_fee="0",
        quote_amount="5",
        quote_fee=".01",
    )
    stops = []

    class Executors:
        async def stop_executor(self, **kwargs):
            stops.append(kwargs)
            pytest.fail("native terminal recovery stopped an executor again")

    client = SimpleNamespace(executors=Executors())
    monkeypatch.setattr(
        shared, "get_executor_evidence", lambda *args: async_value(terminal)
    )
    monkeypatch.setattr(
        shared,
        "get_gateway_pool",
        lambda *args: async_value(
            {
                "pool_address": "pool-1",
                "base_mint": "base-mint",
                "quote_mint": USDC,
                "current_price": Decimal(100),
            }
        ),
    )
    monkeypatch.setattr(shared, "get_owned_positions", lambda *args: async_value([]))
    result = json.loads(
        run(
            recover._native_terminal(
                recover.Config(controller_id=session.controller_id, slot_id="slot-01"),
                session,
                cfg,
                client,
                "wallet",
                pos,
            )
        )
    )
    persisted = shared.read_state(session, cfg)
    assert stops == [] and result["status"] == expected
    if expected == "recovered":
        assert persisted["slots"]["slot-01"] == {
            "position": None,
            "pending_mutation": None,
        }
    else:
        assert persisted["slots"]["slot-01"]["position"] == pos


def test_native_terminal_final_claim_blocks_new_pending_other_slot(
    monkeypatch, tmp_path
):
    session, cfg = make_session(tmp_path, slots=2)
    pos = position(session)
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"]["position"] = pos
    shared.write_state(session, cfg, value)
    swaps = []

    class GatewaySwap:
        async def execute_swap(self, **kwargs):
            swaps.append(kwargs)
            pytest.fail("blocked native terminal recovery submitted a swap")

    async def positions(*args):
        with shared.wallet_lock(session):
            with shared.state_lock(session):
                current = shared.read_state(session, cfg)
                current["slots"]["slot-02"]["pending_mutation"] = pending(
                    "open", "create", "intent", pool="pool-2"
                )
                shared.write_state(session, cfg, current)
        return []

    monkeypatch.setattr(
        shared,
        "get_executor_evidence",
        lambda *args: async_value(terminal_executor(pos)),
    )
    monkeypatch.setattr(
        shared,
        "get_gateway_pool",
        lambda *args: async_value(
            {
                "pool_address": "pool-1",
                "base_mint": "base-mint",
                "quote_mint": USDC,
                "current_price": Decimal(100),
            }
        ),
    )
    monkeypatch.setattr(shared, "get_owned_positions", positions)
    result = json.loads(
        run(
            recover._native_terminal(
                recover.Config(controller_id=session.controller_id, slot_id="slot-01"),
                session,
                cfg,
                SimpleNamespace(gateway_swap=GatewaySwap()),
                "wallet",
                pos,
            )
        )
    )
    persisted = shared.read_state(session, cfg)
    assert result["status"] == "blocked" and result["slot_blockers"] == ["slot-02"]
    assert persisted["slots"]["slot-01"] == {"position": pos, "pending_mutation": None}
    assert persisted["slots"]["slot-02"]["pending_mutation"] is not None and swaps == []


def test_fresh_recovered_create_builds_a_valid_executor_plan(monkeypatch, tmp_path):
    session, cfg = make_session(tmp_path)
    row = candidate(now=datetime.now(timezone.utc))
    request = {
        "candidate": row,
        "range_option": row["range_options"][0]["name"],
        "amount_quote": "5",
        "pool_address": "pool-1",
        "base_mint": "base-mint",
        "quote_mint": USDC,
        "trading_pair": "SOL-USDC",
    }
    live = {
        "required_base": Decimal(".05"),
        "available_base": Decimal(".05"),
        "required_quote": Decimal("5"),
        "bounds": open._bounds(Decimal(100), Decimal(".01")),
        "gateway": {"current_price": Decimal(100)},
    }
    monkeypatch.setattr(open, "_live_preflight", lambda *args: async_value(live))
    client = SimpleNamespace(
        executors=SimpleNamespace(
            get_executor_config_schema=lambda *args: async_value({"properties": {}})
        )
    )
    plan = run(recover._fresh_create_plan(client, session, cfg, request))
    assert plan["pool_address"] == "pool-1" and plan["keep_position"] is True


def test_recovered_create_persists_and_submits_only_the_fresh_plan(
    monkeypatch, tmp_path
):
    session, cfg = make_session(tmp_path)
    row = candidate(now=datetime.now(timezone.utc))
    bounds = open._bounds(Decimal(100), Decimal(".01"))
    stale_plan = open._executor_config(
        row, bounds, Decimal(".04"), Decimal("5"), Decimal("5"), False
    )
    fresh_plan = open._executor_config(
        row, bounds, Decimal(".05"), Decimal("5"), Decimal("5"), True
    )
    request = {
        "controller_id": session.controller_id,
        "amount_quote": "5",
        "pool_address": "pool-1",
        "trading_pair": "SOL-USDC",
        "base_mint": "base-mint",
        "quote_mint": USDC,
        "candidate": row,
        "range_option": row["range_options"][0]["name"],
        "baseline_executor_ids": [],
        "baseline_position_ids": [],
        "executor_config": stale_plan,
    }
    mutation = pending()
    mutation["request"] = request
    value = shared.initial_state(cfg)
    value["binding"] = binding(session)
    value["slots"]["slot-01"]["pending_mutation"] = mutation
    shared.write_state(session, cfg, value)
    submitted = []

    class Executors:
        async def create_executor(self, **kwargs):
            json.dumps(kwargs)
            submitted.append(copy.deepcopy(kwargs))
            return {"executor_id": "fresh-executor"}

    client = SimpleNamespace(executors=Executors())
    monkeypatch.setattr(
        recover, "_fresh_create_plan", lambda *args: async_value(fresh_plan)
    )
    monkeypatch.setattr(
        open,
        "_ownership_preflight",
        lambda *args: async_value(({"baseline-executor"}, {"baseline-position"})),
    )

    async def reconciled(*args):
        current = shared.read_state(session, cfg)["slots"]["slot-01"][
            "pending_mutation"
        ]
        assert (
            current["status"] == "submitted"
            and current["external_id"] == "fresh-executor"
        )
        assert current["request"]["executor_config"] == shared.json_value(
            fresh_plan, redact=False
        )
        assert current["request"]["baseline_executor_ids"] == ["baseline-executor"]
        assert current["request"]["baseline_position_ids"] == ["baseline-position"]
        assert current["confirmed"]["fresh_preflight"]["plan"] == shared.json_value(
            fresh_plan, redact=False
        )
        return "reconciled"

    monkeypatch.setattr(recover, "_reconcile_create", reconciled)
    result = run(
        recover._submit_intent(
            recover.Config(controller_id=session.controller_id, slot_id="slot-01"),
            session,
            cfg,
            client,
            "wallet",
            "operation-1",
        )
    )
    assert result == "reconciled" and len(submitted) == 1
    assert submitted[0]["executor_config"] == shared.json_value(
        fresh_plan, redact=False
    ) and submitted[0]["executor_config"] != shared.json_value(stale_plan, redact=False)
