import asyncio
import importlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from agents.lpmaxxing.routines import orca_live_preflight as _orca_live_preflight

_orca_live_preflight = importlib.reload(_orca_live_preflight)
load_lifecycle_state = _orca_live_preflight.load_lifecycle_state
load_position_archive = _orca_live_preflight.load_position_archive
commit_position_audit = _orca_live_preflight.commit_position_audit
session_execution_mode = _orca_live_preflight.session_execution_mode

try:
    from routines.base import RoutineResult
except ModuleNotFoundError:
    sys.path.append(str(PROJECT_ROOT))
    from routines.base import RoutineResult


CATEGORY = "Orca LP Agent"
TERMINAL_STATES = {
    "COMPLETE",
    "COMPLETED",
    "TERMINATED",
    "STOPPED",
    "CANCELED",
    "CANCELLED",
    "FAILED",
}
REDACTED_KEYS = {
    "privatekey",
    "secret",
    "password",
    "apikey",
    "accesstoken",
    "walletaddress",
    "owneraddress",
}


class Config(BaseModel):
    """Compare one LP executor plan with its terminal result and summarize the demo."""

    model_config = ConfigDict(extra="forbid")

    controller_id: str = Field(
        default="", description="Dynamic controller id from the completed session"
    )
    execution_mode: Literal["dry_run", "loop"] = Field(
        default="dry_run", description="dry_run or loop lifecycle mode"
    )
    executor_plan: dict[str, Any] = Field(
        default_factory=dict, description="Executor plan returned by preflight"
    )
    preset: str = Field(default="", description="Preset selected by the pool scan")
    final_executor: dict[str, Any] = Field(
        default_factory=dict, description="Final executor API response"
    )
    close_reason: str = Field(
        default="", description="Reason the agent requested or observed the close"
    )
    rebalance: dict[str, Any] = Field(
        default_factory=dict, description="Persisted pre-LP rebalance evidence"
    )
    rebalance_plan: dict[str, Any] = Field(
        default_factory=dict, description="Persisted inventory and budget plan"
    )

    @field_validator("controller_id", "preset", "close_reason", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return str(value or "").strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _first_number(*values: Any) -> float | None:
    for value in values:
        parsed = _number(value)
        if parsed is not None:
            return parsed
    return None


def _find_number(value: Any, keys: set[str]) -> float | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in keys:
                parsed = _number(item)
                if parsed is not None:
                    return parsed
        for item in value.values():
            found = _find_number(item, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_number(item, keys)
            if found is not None:
                return found
    return None


def _datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, timezone.utc)
        except (OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[redacted]"
                if "".join(
                    character for character in str(key).lower() if character.isalnum()
                )
                in REDACTED_KEYS
                else _sanitize(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _executor(value: dict[str, Any]) -> dict[str, Any]:
    for key in ("executor", "data", "result", "item"):
        nested = value.get(key)
        if isinstance(nested, dict):
            return nested
    return value


def _reconciled_pnl(executor: dict[str, Any]) -> dict[str, Any]:
    net_quote = _number(executor.get("net_pnl_quote"))
    filled_quote = _number(executor.get("filled_amount_quote"))
    raw_pct = executor.get("net_pnl_pct")
    ratio = (
        net_quote / filled_quote
        if net_quote is not None and filled_quote and filled_quote > 0
        else None
    )
    return {
        "net_pnl_quote": net_quote,
        "filled_amount_quote": filled_quote,
        "raw_net_pnl_pct": raw_pct,
        "reconciled_pnl_ratio": ratio,
    }


def _evaluate(config: Config) -> dict[str, Any]:
    executor = _executor(config.final_executor)
    deployed = (
        executor.get("config") if isinstance(executor.get("config"), dict) else {}
    )
    custom = (
        executor.get("custom_info")
        if isinstance(executor.get("custom_info"), dict)
        else {}
    )
    plan = (
        config.executor_plan.get("executor_config")
        if isinstance(config.executor_plan.get("executor_config"), dict)
        else config.executor_plan
    )

    executor_id = (
        executor.get("executor_id") or executor.get("id") or deployed.get("id")
    )
    executor_controller = executor.get("controller_id") or deployed.get("controller_id")
    planned_pool = plan.get("pool_address")
    planned_controller = plan.get("controller_id")
    actual_pool = executor.get("pool_address") or deployed.get("pool_address")
    state = str(executor.get("status") or custom.get("state") or "UNKNOWN").upper()
    opened_at = _datetime(
        executor.get("created_at")
        or executor.get("timestamp")
        or deployed.get("created_at")
        or deployed.get("timestamp")
    )
    closed_at = _datetime(
        executor.get("closed_at")
        or executor.get("close_timestamp")
        or executor.get("updated_at")
        or custom.get("closed_at")
        or custom.get("close_timestamp")
    )
    duration_minutes = (
        max(0.0, (closed_at - opened_at).total_seconds() / 60)
        if opened_at and closed_at
        else None
    )

    expected_base = _number(plan.get("base_amount"))
    expected_quote = _number(plan.get("quote_amount"))
    actual_base = _first_number(
        custom.get("initial_base_amount"), custom.get("deposited_base_amount")
    )
    actual_quote = _first_number(
        custom.get("initial_quote_amount"), custom.get("deposited_quote_amount")
    )
    pnl = _reconciled_pnl(executor)
    fees = _first_number(
        executor.get("cum_fees_quote"),
        executor.get("fees_quote"),
        custom.get("fees_earned_quote"),
        custom.get("cum_fees_quote"),
        custom.get("quote_fee"),
    )
    tx_cost = _first_number(
        executor.get("transaction_cost_quote"),
        executor.get("tx_fee"),
        custom.get("tx_fee"),
        custom.get("transaction_fees_quote"),
    )
    rent = _first_number(executor.get("position_rent"), custom.get("position_rent"))
    rent_refunded = _first_number(
        executor.get("position_rent_refunded"),
        custom.get("position_rent_refunded"),
    )
    close_reason = (
        config.close_reason or executor.get("close_type") or custom.get("close_type")
    )
    rebalance_status = str(config.rebalance.get("status") or "NOT_REQUIRED").upper()
    rebalance_tx = config.rebalance.get("transaction_hash")
    rebalance_quote = (
        config.rebalance.get("quote")
        if isinstance(config.rebalance.get("quote"), dict)
        else {}
    )
    rebalance_settlement = (
        config.rebalance.get("settlement")
        if isinstance(config.rebalance.get("settlement"), dict)
        else {}
    )
    settled_input = _find_number(
        rebalance_settlement,
        {
            "amount_in",
            "amountin",
            "input_amount",
            "inputamount",
            "in_amount",
            "inamount",
        },
    )
    settled_output = _find_number(
        rebalance_settlement,
        {
            "amount_out",
            "amountout",
            "output_amount",
            "outputamount",
            "out_amount",
            "outamount",
        },
    )
    post_swap_inventory = (
        config.rebalance.get("post_swap_inventory")
        if isinstance(config.rebalance.get("post_swap_inventory"), dict)
        else {}
    )
    before_base = _number(config.rebalance_plan.get("available_base"))
    before_quote = _number(config.rebalance_plan.get("available_quote"))
    after_base = _number(post_swap_inventory.get("base"))
    after_quote = _number(post_swap_inventory.get("quote"))
    balance_input = (
        max(0.0, before_quote - after_quote)
        if before_quote is not None and after_quote is not None
        else None
    )
    balance_output = (
        max(0.0, after_base - before_base)
        if before_base is not None and after_base is not None
        else None
    )
    rebalance_input = _first_number(settled_input, balance_input)
    rebalance_output = _first_number(settled_output, balance_output)
    amount_source = (
        "settlement"
        if settled_input is not None and settled_output is not None
        else (
            "wallet_balance_delta"
            if balance_input is not None and balance_output is not None
            else "unavailable"
        )
    )
    quoted_input = _first_number(
        config.rebalance.get("quote_spend"),
        _find_number(
            rebalance_quote,
            {
                "amount_in",
                "amountin",
                "input_amount",
                "inputamount",
                "in_amount",
                "inamount",
            },
        ),
    )
    quoted_output = _find_number(
        rebalance_quote,
        {
            "amount_out",
            "amountout",
            "output_amount",
            "outputamount",
            "out_amount",
            "outamount",
        },
    )
    rebalance_gas = _find_number(
        rebalance_settlement,
        {
            "gas_fee_quote",
            "gasfeequote",
            "transaction_cost_quote",
            "transactioncostquote",
        },
    )
    rebalance_price = _number(config.rebalance_plan.get("current_price"))
    rebalance_slippage = (
        rebalance_input - rebalance_output * rebalance_price
        if rebalance_input is not None
        and rebalance_output is not None
        and rebalance_price is not None
        else None
    )
    rebalance_cost = (
        max(0.0, rebalance_slippage or 0.0) + (rebalance_gas or 0.0)
        if rebalance_slippage is not None or rebalance_gas is not None
        else None
    )

    missing: list[str] = []
    required = {
        "controller_id": config.controller_id,
        "executor_controller_id": executor_controller,
        "executor_id": executor_id,
        "planned_controller_id": planned_controller,
        "planned_pool_address": planned_pool,
        "pool_address": actual_pool,
        "preset": config.preset,
        "terminal_state": state if state in TERMINAL_STATES else None,
        "opened_at": opened_at,
        "closed_at": closed_at,
        "planned_base_amount": expected_base,
        "planned_quote_amount": expected_quote,
        "actual_base_amount": actual_base,
        "actual_quote_amount": actual_quote,
        "net_pnl_quote": pnl["net_pnl_quote"],
        "filled_amount_quote": pnl["filled_amount_quote"],
        "close_reason": close_reason,
        "fees_earned_quote": fees,
        "transaction_cost_quote": tx_cost,
        "position_rent_quote": rent,
        "rent_refunded_quote": rent_refunded,
        "reconciled_pnl_ratio": pnl["reconciled_pnl_ratio"],
    }
    for name, value in required.items():
        if value in (None, ""):
            missing.append(name)
    if rebalance_status == "CONFIRMED":
        for name, value in {
            "rebalance_transaction_hash": rebalance_tx,
            "rebalance_actual_quote_input": rebalance_input,
            "rebalance_actual_base_output": rebalance_output,
        }.items():
            if value in (None, ""):
                missing.append(name)
    elif rebalance_status != "NOT_REQUIRED":
        missing.append("rebalance_confirmed")

    warnings: list[str] = []
    if executor_controller and executor_controller != config.controller_id:
        warnings.append(
            f"executor controller '{executor_controller}' does not match '{config.controller_id}'"
        )
        missing.append("controller_match")
    if planned_controller and planned_controller != config.controller_id:
        warnings.append(
            f"planned controller '{planned_controller}' does not match '{config.controller_id}'"
        )
        missing.append("planned_controller_match")
    if planned_pool and actual_pool != planned_pool:
        warnings.append(
            f"executor pool '{actual_pool}' does not match planned pool '{planned_pool}'"
        )
        missing.append("pool_match")
    if state == "FAILED":
        warnings.append("executor finished in FAILED state")

    return {
        "audit_status": "complete" if not missing else "partial",
        "identity": {
            "controller_id": config.controller_id,
            "executor_id": executor_id,
            "pool_address": actual_pool or planned_pool,
            "trading_pair": executor.get("trading_pair")
            or deployed.get("trading_pair")
            or plan.get("trading_pair"),
            "preset": config.preset,
            "terminal_state": state,
        },
        "lifecycle": {
            "opened_at": opened_at.isoformat() if opened_at else None,
            "closed_at": closed_at.isoformat() if closed_at else None,
            "duration_minutes": (
                round(duration_minutes, 2) if duration_minutes is not None else None
            ),
            "close_reason": close_reason,
        },
        "deposits": {
            "planned_base": expected_base,
            "planned_quote": expected_quote,
            "actual_base": actual_base,
            "actual_quote": actual_quote,
        },
        "performance": {
            "fees_earned_quote": fees,
            "transaction_cost_quote": tx_cost,
            "position_rent_quote": rent,
            "rent_refunded_quote": rent_refunded,
            **pnl,
            "net_pnl_after_rebalance_quote": (
                pnl["net_pnl_quote"] - rebalance_cost
                if pnl["net_pnl_quote"] is not None and rebalance_cost is not None
                else pnl["net_pnl_quote"]
            ),
        },
        "rebalance": {
            "status": rebalance_status,
            "transaction_hash": rebalance_tx,
            "actual_amount_source": amount_source,
            "actual_quote_input": rebalance_input,
            "actual_base_output": rebalance_output,
            "quoted_quote_input": quoted_input,
            "quoted_base_output": quoted_output,
            "estimated_slippage_quote": rebalance_slippage,
            "gas_cost_quote": rebalance_gas,
            "total_cost_quote": rebalance_cost,
        },
        "summary": {
            "pool_address": actual_pool or planned_pool,
            "trading_pair": executor.get("trading_pair")
            or deployed.get("trading_pair")
            or plan.get("trading_pair"),
            "preset": config.preset,
            "terminal_state": state,
            "close_reason": close_reason,
            "net_pnl_quote": pnl["net_pnl_quote"],
            "reconciled_pnl_ratio": pnl["reconciled_pnl_ratio"],
            "rebalance_status": rebalance_status,
        },
        "missing_fields": sorted(set(missing)),
        "warnings": warnings,
        "source_evidence": _sanitize(executor),
    }


def _format(payload: dict[str, Any]) -> str:
    identity = payload["identity"]
    performance = payload["performance"]
    lines = [
        f"Orca LP Close Audit: {payload['audit_status']}",
        f"Executor: {identity.get('executor_id') or 'n/a'}",
        f"State: {identity.get('terminal_state')}",
        f"PnL: {performance.get('net_pnl_quote')}",
    ]
    if payload["missing_fields"]:
        lines.append("Missing: " + ", ".join(payload["missing_fields"]))
    return "\n".join(lines)


def _rows(section: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"Field": key, "Value": value} for key, value in section.items()]


async def _save_report(payload: dict[str, Any]) -> None:
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("Orca LP Close Audit")
        builder.source("routine", "lp_close_audit").tags(
            ["orca", "lp", "close", "audit", "agent"]
        ).manual_order()
        builder.kpi(
            "Audit",
            payload["audit_status"],
            trend="up" if payload["audit_status"] == "complete" else "neutral",
        )
        builder.kpi("State", str(payload["identity"].get("terminal_state")))
        builder.kpi("Pair", str(payload["identity"].get("trading_pair") or "n/a"))
        pnl_quote = payload["performance"].get("net_pnl_quote")
        builder.kpi("PnL Quote", "n/a" if pnl_quote is None else str(pnl_quote))
        builder.markdown("## Agent Summary\n" + payload["agent_prompt_summary"])
        builder.markdown("## Result Summary")
        builder.table(_rows(payload["summary"]))
        for title, key in (
            ("Position Identity", "identity"),
            ("Lifecycle", "lifecycle"),
            ("Pre-LP Rebalance", "rebalance"),
            ("Planned vs Actual Deposits", "deposits"),
            ("Fees, Costs, and PnL", "performance"),
        ):
            builder.markdown(f"## {title}")
            builder.table(_rows(payload[key]))
        builder.markdown(
            "## Missing Fields\n"
            + (
                "\n".join(f"- {field}" for field in payload["missing_fields"])
                if payload["missing_fields"]
                else "- None."
            )
        )
        builder.markdown(
            "## Warnings\n"
            + (
                "\n".join(f"- {warning}" for warning in payload["warnings"])
                if payload["warnings"]
                else "- None."
            )
        )
        builder.markdown(
            "## Final Executor Source\n```json\n"
            f"{json.dumps(payload['source_evidence'], indent=2, sort_keys=True, default=str)}\n```"
        )
        builder.markdown(
            "## Sanitized Routine Input\n```json\n"
            f"{json.dumps(payload['input_config'], indent=2, sort_keys=True, default=str)}\n```"
        )
        builder.markdown(
            "## Debug JSON Payload\n```json\n"
            f"{json.dumps(payload, indent=2, sort_keys=True, default=str)}\n```"
        )
        await builder.save()
    except Exception:
        return


async def run(config: Config, context: Any) -> RoutineResult:
    lifecycle_state: dict[str, Any] = {}
    state_error: str | None = None
    try:
        if session_execution_mode(config.controller_id) != config.execution_mode:
            raise ValueError("execution_mode does not match the current SESSION_MODE")
    except Exception as exc:
        state_error = f"runtime mode validation failed: {type(exc).__name__}: {exc}"
    if config.execution_mode == "loop":
        try:
            lifecycle_state = load_lifecycle_state(config.controller_id)
            if not lifecycle_state:
                final_executor = _executor(config.final_executor)
                executor_id = (
                    final_executor.get("executor_id")
                    or final_executor.get("id")
                    or (
                        final_executor.get("config", {}).get("id")
                        if isinstance(final_executor.get("config"), dict)
                        else None
                    )
                )
                archive = load_position_archive(
                    config.controller_id, str(executor_id or "")
                )
                if archive:
                    lifecycle_state = archive.get("lifecycle") or {}
            config = config.model_copy(
                update={
                    "executor_plan": lifecycle_state.get("executor_plan") or {},
                    "preset": lifecycle_state.get("preset") or "",
                    "close_reason": lifecycle_state.get("close_reason") or "",
                    "rebalance": lifecycle_state.get("rebalance") or {},
                    "rebalance_plan": lifecycle_state.get("rebalance_plan") or {},
                }
            )
        except Exception as exc:
            state_error = f"lifecycle state load failed: {type(exc).__name__}: {exc}"
    payload = _evaluate(config)
    payload["timestamp"] = _utc_now()
    if state_error:
        payload["audit_status"] = "partial"
        payload["missing_fields"] = sorted(
            {*payload["missing_fields"], "lifecycle_state_load"}
        )
        payload["warnings"].append(state_error)
    elif config.execution_mode == "loop":
        expected_executor_id = lifecycle_state.get("executor_id")
        actual_executor_id = payload["identity"].get("executor_id")
        lifecycle_controller = lifecycle_state.get("controller_id")
        if lifecycle_controller != config.controller_id:
            payload["missing_fields"].append("lifecycle_controller_match")
            payload["warnings"].append(
                f"persisted controller '{lifecycle_controller}' does not match '{config.controller_id}'"
            )
        if lifecycle_state.get("phase") != "terminal":
            payload["missing_fields"].append("lifecycle_terminal_phase")
            payload["warnings"].append(
                f"persisted lifecycle phase '{lifecycle_state.get('phase')}' is not terminal"
            )
        if not expected_executor_id:
            payload["missing_fields"].append("persisted_executor_id")
        elif actual_executor_id != expected_executor_id:
            payload["missing_fields"].append("executor_match")
            payload["warnings"].append(
                f"executor '{actual_executor_id}' does not match persisted executor '{expected_executor_id}'"
            )
        critical_fields = {
            "controller_id",
            "executor_controller_id",
            "executor_id",
            "executor_match",
            "persisted_executor_id",
            "lifecycle_controller_match",
            "lifecycle_terminal_phase",
            "planned_controller_id",
            "planned_controller_match",
            "planned_pool_address",
            "pool_address",
            "pool_match",
            "controller_match",
            "terminal_state",
            "net_pnl_quote",
            "filled_amount_quote",
            "reconciled_pnl_ratio",
            "close_reason",
            "preset",
            "planned_base_amount",
            "planned_quote_amount",
            "rebalance_confirmed",
            "rebalance_transaction_hash",
            "rebalance_actual_quote_input",
            "rebalance_actual_base_output",
        }
        if critical_fields.intersection(payload["missing_fields"]):
            payload["audit_status"] = "partial"
            payload["missing_fields"] = sorted(
                {*payload["missing_fields"], "lifecycle_not_finalized"}
            )
            payload["warnings"].append(
                "terminal identity or persisted plan is incomplete; lifecycle remains open for manual review"
            )
        else:
            if payload["missing_fields"]:
                payload["warnings"].append(
                    "non-critical audit details are unavailable; identity and terminal PnL are complete"
                )
            payload["audit_status"] = "complete"
            try:
                payload["session_commit"] = commit_position_audit(
                    config.controller_id, _sanitize(payload)
                )
            except Exception as exc:
                payload["audit_status"] = "partial"
                payload["missing_fields"] = sorted(
                    {*payload["missing_fields"], "lifecycle_state_persistence"}
                )
                payload["warnings"].append(
                    f"lifecycle state persistence failed: {type(exc).__name__}: {exc}"
                )
    payload["input_config"] = _sanitize(config.model_dump(mode="json"))
    if "lifecycle_not_finalized" in payload["missing_fields"]:
        payload["agent_prompt_summary"] = (
            "Close audit could not prove terminal lifecycle identity; manual review is required and the state remains open."
        )
    elif payload["audit_status"] == "complete":
        session_status = (payload.get("session_commit") or {}).get("session_status")
        if session_status == "stop_pending":
            payload["agent_prompt_summary"] = (
                "Close audit is complete and the session stop is durable; the next tick must stop the agent."
            )
        elif session_status == "manual_review":
            payload["agent_prompt_summary"] = (
                "Close audit is recorded, but the session is blocked for manual review and must not re-enter."
            )
        else:
            payload["agent_prompt_summary"] = (
                "Close audit is complete; the position was archived and the next scheduled tick may begin a new lifecycle."
            )
    else:
        payload["agent_prompt_summary"] = (
            f"Close audit is partial; missing: {', '.join(payload['missing_fields'])}. The session remains blocked."
        )
    await _save_report(payload)
    return RoutineResult(
        text=_format(payload)
        + "\n```json\n"
        + json.dumps(payload, indent=2, sort_keys=True, default=str)
        + "\n```",
        table_data=_rows(payload["performance"]),
        table_columns=["Field", "Value"],
    )


def _self_check() -> None:
    config = Config(
        controller_id="lpmaxxing.orca_1",
        preset="balanced",
        close_reason="take_profit_reached",
        executor_plan={
            "preset": "balanced",
            "executor_config": {
                "controller_id": "lpmaxxing.orca_1",
                "pool_address": "pool-1",
                "trading_pair": "SOL-USDC",
                "base_amount": 0.05,
                "quote_amount": 5,
            },
        },
        final_executor={
            "id": "executor-1",
            "controller_id": "lpmaxxing.orca_1",
            "status": "COMPLETE",
            "created_at": "2026-01-01T00:00:00+00:00",
            "closed_at": "2026-01-01T01:00:00+00:00",
            "filled_amount_quote": 10,
            "net_pnl_quote": 0.1,
            "net_pnl_pct": 1,
            "config": {"pool_address": "pool-1", "trading_pair": "SOL-USDC"},
            "custom_info": {
                "initial_base_amount": 0.05,
                "initial_quote_amount": 5,
                "fees_earned_quote": 0.12,
                "tx_fee": 0.01,
                "position_rent": 0.02,
                "position_rent_refunded": 0.02,
            },
        },
    )
    audit = _evaluate(config)
    assert audit["audit_status"] == "complete", audit
    assert audit["lifecycle"]["duration_minutes"] == 60, audit
    assert audit["performance"]["reconciled_pnl_ratio"] == 0.01, audit
    assert audit["summary"]["preset"] == "balanced", audit
    partial = _evaluate(config.model_copy(update={"final_executor": {}}))
    assert partial["audit_status"] == "partial", partial
    assert partial["missing_fields"], partial
    no_reason = _evaluate(config.model_copy(update={"close_reason": ""}))
    assert no_reason["audit_status"] == "partial", no_reason
    assert "close_reason" in no_reason["missing_fields"], no_reason
    quote_only_rebalance = _evaluate(
        config.model_copy(
            update={
                "rebalance": {
                    "status": "CONFIRMED",
                    "transaction_hash": "tx-1",
                    "quote_spend": 4.5,
                    "quote": {"amount_in": 4.5, "amount_out": 5},
                },
                "rebalance_plan": {
                    "available_base": 0,
                    "available_quote": 10,
                    "current_price": 0.9,
                },
            }
        )
    )
    assert "rebalance_actual_quote_input" in quote_only_rebalance["missing_fields"]
    assert "rebalance_actual_base_output" in quote_only_rebalance["missing_fields"]
    assert _sanitize({"walletAddress": "secret"})["walletAddress"] == "[redacted]"


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        _self_check()
        print("self-check passed")
        raise SystemExit(0)
    print(asyncio.run(run(Config(), None)).text)
