import asyncio
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from agents.lpmaxxing.routines import _orca_evidence as evidence
from agents.lpmaxxing.routines._orca_contracts import NextAction, attach_outcome
from agents.lpmaxxing.routines._orca_lifecycle import (
    lifecycle_lock,
    load_lifecycle_state,
    load_session_state,
    mark_session_stop,
    resume_session_cycle,
    save_lifecycle_state,
    session_execution_mode,
    session_exit_policy,
    session_stop_trigger,
)

try:
    from routines.base import RoutineResult
except ModuleNotFoundError:
    from routines.base import RoutineResult

CATEGORY = "Orca LP Agent"

INACTIVE_STATES = {
    "COMPLETE",
    "COMPLETED",
    "TERMINATED",
    "CANCELED",
    "CANCELLED",
    "FAILED",
    "STOPPED",
}
FAILED_STATES = {"FAILED"}
COMPLETE_STATES = {"COMPLETE", "COMPLETED", "TERMINATED"}
TERMINAL_STATES = INACTIVE_STATES
ACTIVE_STATES = {"RUNNING", "OPENING", "CLOSING", "SHUTTING_DOWN"}


class Config(BaseModel):
    """Fetch an LP executor from the API, summarize state, and recommend supervision action."""

    model_config = ConfigDict(extra="forbid")

    controller_id: str = Field(
        default="",
        description="Dynamic executor controller id from the current Condor tick",
    )
    execution_mode: Literal["dry_run", "loop"] = Field(
        description="Explicit dry_run or loop lifecycle mode"
    )
    executor_id: str | None = Field(
        default=None,
        description="Exact executor id to fetch executor details",
    )
    now_timestamp: Any = Field(default=None, description="Current timestamp override")

    position_max_age_minutes: float = Field(
        default=480, ge=0, description="Time-limit exit threshold"
    )
    position_take_profit_net_pnl_ratio: float | None = Field(
        default=0.005,
        description="Close when reconciled net PnL ratio reaches this threshold",
    )
    position_stop_loss_net_pnl_ratio: float | None = Field(
        default=0.01,
        description="Close when reconciled net PnL ratio reaches this negative threshold",
    )
    archived_executor_ids: list[str] = Field(
        default_factory=list,
        description="Terminal executor ids already committed to position archives",
    )
    soft_out_of_range_grace_minutes: float = Field(
        default=10, ge=0, description="Minutes tolerated out of LP range"
    )
    missing_position_grace_ticks: int = Field(
        default=2,
        ge=1,
        description="Missing-position ticks tolerated before manual review",
    )
    missing_position_ticks: int = Field(
        default=0, ge=0, description="Observed consecutive missing-position ticks"
    )

    @field_validator("controller_id", mode="before")
    @classmethod
    def _coerce_controller_id(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("executor_id", mode="before")
    @classmethod
    def _coerce_executor_id(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_float(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("$", "")
        if text.endswith("%"):
            text = text[:-1]
            try:
                return float(text) / 100.0
            except ValueError:
                return None
        try:
            parsed = float(text)
        except ValueError:
            return None
        return parsed if math.isfinite(parsed) else None
    return None


def _to_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp, timezone.utc)
        except (OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _as_state(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    return str(value).strip().upper() or "UNKNOWN"


def _context_chat_id(context: Any) -> Any:
    return getattr(context, "_chat_id", None) if context is not None else None


def _executor_matches_id(executor: dict[str, Any], executor_id: str) -> bool:
    actual = evidence.executor_id(executor)
    if actual is None:
        return False
    actual_text = str(actual).strip()
    wanted = executor_id.strip()
    return actual_text == wanted


def _is_active_executor(executor: dict[str, Any], controller_id: str) -> bool:
    if not isinstance(executor, dict):
        return False
    owner = evidence.executor_controller_id(executor)
    if owner != controller_id:
        return False
    if executor.get("is_active") is False:
        return False
    state = _as_state(executor.get("status"))
    return state in ACTIVE_STATES


def _lp_side_value(value: Any) -> float | None:
    parsed = _to_float(value)
    if parsed is not None:
        return parsed
    return 3.0 if _as_state(value) == "RANGE" else None


def _executor_plan_mismatches(
    executor: dict[str, Any], lifecycle_state: dict[str, Any]
) -> list[str]:
    actual = executor.get("config") if isinstance(executor.get("config"), dict) else {}
    plan = lifecycle_state.get("executor_plan") or {}
    expected = (
        plan.get("executor_config")
        if isinstance(plan.get("executor_config"), dict)
        else plan
    )
    if not isinstance(expected, dict) or not expected:
        return ["executor_plan"]

    mismatches: list[str] = []
    identity_values = {
        "controller_id": evidence.executor_controller_id(executor),
        "connector_name": executor.get("connector_name")
        or actual.get("connector_name"),
        "lp_provider": executor.get("lp_provider") or actual.get("lp_provider"),
        "pool_address": executor.get("pool_address") or actual.get("pool_address"),
        "trading_pair": executor.get("trading_pair") or actual.get("trading_pair"),
    }
    for key, actual_value in identity_values.items():
        if str(actual_value or "").strip() != str(expected.get(key) or "").strip():
            mismatches.append(key)
    for key in (
        "lower_price",
        "upper_price",
        "lower_limit_price",
        "upper_limit_price",
        "base_amount",
        "quote_amount",
    ):
        actual_value = _to_float(actual.get(key))
        expected_value = _to_float(expected.get(key))
        if (
            actual_value is None
            or expected_value is None
            or not math.isclose(
                actual_value, expected_value, rel_tol=1e-9, abs_tol=1e-12
            )
        ):
            mismatches.append(key)

    actual_side = _lp_side_value(actual.get("side"))
    expected_side = _lp_side_value(expected.get("side"))
    if (
        actual_side is None
        or expected_side is None
        or not math.isclose(actual_side, expected_side, rel_tol=0.0, abs_tol=0.0)
    ):
        mismatches.append("side")

    actual_total_raw = actual.get("total_amount_quote")
    if actual_total_raw not in (None, ""):
        actual_total = _to_float(actual_total_raw)
        expected_total = _to_float(expected.get("total_amount_quote"))
        if (
            actual_total is None
            or expected_total is None
            or not math.isclose(
                actual_total, expected_total, rel_tol=1e-9, abs_tol=1e-12
            )
        ):
            mismatches.append("total_amount_quote")

    if not (
        actual.get("keep_position") is False and expected.get("keep_position") is False
    ):
        mismatches.append("keep_position")
    return mismatches


def _executor_lifecycle_mismatches(
    executor: dict[str, Any], lifecycle_state: dict[str, Any]
) -> list[str]:
    mismatches = _executor_plan_mismatches(executor, lifecycle_state)
    expected_id = str(lifecycle_state.get("executor_id") or "").strip()
    if expected_id and evidence.executor_id(executor) != expected_id:
        mismatches.insert(0, "executor_id")
    return mismatches


async def _fetch_executor_from_api(
    config: Config, context: Any
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    debug: dict[str, Any] = {
        "controller_id": config.controller_id,
        "executor_id": config.executor_id,
        "lookup_mode": "executor_id" if config.executor_id else "controller_id",
        "requests": [],
        "outcome": "failed",
    }
    warnings: list[str] = []

    try:
        from config_manager import get_client
    except Exception as exc:
        debug["error"] = f"executor lookup unavailable: {type(exc).__name__}: {exc}"
        return None, [debug["error"]], debug

    try:
        client = await get_client(_context_chat_id(context), context=context)
    except Exception as exc:
        debug["error"] = f"executor client unavailable: {type(exc).__name__}: {exc}"
        return None, [debug["error"]], debug
    if not client:
        debug["client_available"] = False
        return (
            None,
            ["executor lookup skipped: no Hummingbot API server available"],
            debug,
        )
    debug["client_available"] = True

    if config.executor_id:
        executor, lookup_warnings = await _fetch_executor_by_id(
            client, config.executor_id, config.controller_id, debug
        )
        return executor, warnings + lookup_warnings, debug

    executor, lookup_warnings = await _fetch_active_executor_by_controller(
        client, config.controller_id, set(config.archived_executor_ids), debug
    )
    return executor, warnings + lookup_warnings, debug


async def _fetch_executor_by_id(
    client: Any, executor_id: str, controller_id: str, debug: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    request_debug: dict[str, Any] = {
        "method": "get_executor",
        "executor_id": executor_id,
    }
    try:
        raw_response = await client.executors.get_executor(executor_id)
        request_debug["response"] = evidence.redact(raw_response)
        executor = evidence.executor_dict(raw_response)
        owner = evidence.executor_controller_id(executor) if executor else None
        if (
            executor
            and _executor_matches_id(executor, executor_id)
            and owner == controller_id
        ):
            request_debug["selected"] = True
            debug["selected_source"] = "get_executor"
            debug["outcome"] = "found"
            debug["requests"].append(request_debug)
            return executor, warnings
        if executor and owner and owner != controller_id:
            warnings.append(
                f"executor_id '{executor_id}' belongs to controller_id '{owner}', not '{controller_id}'"
            )
        request_debug["selected"] = False
        warnings.append(
            f"executor_id '{executor_id}' direct lookup did not prove controller ownership"
        )
    except Exception as exc:
        error = f"executor_id '{executor_id}' direct lookup failed: {type(exc).__name__}: {exc}"
        request_debug["error"] = error
        warnings.append(error)
    debug["requests"].append(request_debug)

    for scope in ("controller_executor_id_fallback",):
        request_debug = {
            "method": "search_executors",
            "scope": scope,
            "params": {"controller_ids": [controller_id], "paginated": True},
        }
        try:
            executors, pages = await evidence.search_controller_executors(
                client, controller_id, capture_pages=True, strict=True
            )
            request_debug["responses"] = pages
            matches = [
                executor
                for executor in executors
                if _executor_matches_id(executor, executor_id)
                and evidence.executor_controller_id(executor) == controller_id
            ]
            request_debug["candidate_count"] = len(executors)
            request_debug["match_count"] = len(matches)
            if matches:
                if len(matches) > 1:
                    warning = f"executor_id '{executor_id}' matched {len(matches)} executors in {scope}"
                    warnings.append(warning)
                    request_debug["error"] = warning
                    debug["outcome"] = "ambiguous"
                    debug["requests"].append(request_debug)
                    return None, warnings
                request_debug["selected"] = True
                debug["selected_source"] = scope
                debug["outcome"] = "found"
                debug["requests"].append(request_debug)
                return matches[0], warnings
            request_debug["selected"] = False
            debug["outcome"] = "failed"
            warnings.append(f"executor_id '{executor_id}' was not found in {scope}")
        except Exception as exc:
            error = f"executor_id '{executor_id}' {scope} failed: {type(exc).__name__}: {exc}"
            request_debug["error"] = error
            warnings.append(error)
        debug["requests"].append(request_debug)

    return None, warnings


async def _fetch_active_executor_by_controller(
    client: Any,
    controller_id: str,
    archived_executor_ids: set[str],
    debug: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    request_debug: dict[str, Any] = {
        "method": "search_executors",
        "params": {"controller_ids": [controller_id], "paginated": True},
    }
    try:
        executors, pages = await evidence.search_controller_executors(
            client, controller_id, capture_pages=True, strict=True
        )
        request_debug["responses"] = pages
        unknown_states = [
            _as_state(executor.get("status"))
            for executor in executors
            if _as_state(executor.get("status")) not in ACTIVE_STATES | TERMINAL_STATES
        ]
        if unknown_states:
            warning = f"controller executor search returned unknown states: {sorted(set(unknown_states))}"
            request_debug["error"] = warning
            debug["outcome"] = "failed"
            warnings.append(warning)
            debug["requests"].append(request_debug)
            return None, warnings
        unowned = [
            evidence.executor_id(executor)
            for executor in executors
            if evidence.executor_controller_id(executor) != controller_id
        ]
        if unowned:
            warning = f"controller executor search returned records without proven ownership: {unowned}"
            request_debug["error"] = warning
            debug["outcome"] = "failed"
            warnings.append(warning)
            debug["requests"].append(request_debug)
            return None, warnings
        active_executors = [
            executor
            for executor in executors
            if _is_active_executor(executor, controller_id)
        ]
        debug["active_executor_count"] = len(active_executors)
        request_debug["candidate_count"] = len(executors)
        request_debug["active_count"] = len(active_executors)
        if active_executors:
            if len(active_executors) > 1:
                warning = f"controller_id '{controller_id}' has {len(active_executors)} active executors"
                warnings.append(warning)
                request_debug["error"] = warning
                debug["outcome"] = "ambiguous"
                debug["requests"].append(request_debug)
                return None, warnings
            request_debug["selected"] = True
            debug["selected_source"] = "search_executors"
            debug["outcome"] = "found"
            debug["requests"].append(request_debug)
            return active_executors[0], warnings
        terminal_executors = [
            executor
            for executor in executors
            if _as_state(executor.get("status")) in TERMINAL_STATES
            and evidence.executor_controller_id(executor) in {None, controller_id}
        ]
        request_debug["terminal_count"] = len(terminal_executors)
        unarchived = [
            executor
            for executor in terminal_executors
            if str(evidence.executor_id(executor) or "").strip()
            not in archived_executor_ids
        ]
        request_debug["archived_terminal_count"] = len(terminal_executors) - len(
            unarchived
        )
        request_debug["unarchived_terminal_count"] = len(unarchived)
        if len(unarchived) > 1:
            warning = (
                f"controller_id '{controller_id}' has {len(unarchived)} "
                "terminal executor(s) without a position archive"
            )
            warnings.append(warning)
            request_debug["error"] = warning
            debug["outcome"] = "unarchived_terminal"
            debug["requests"].append(request_debug)
            return None, warnings
        if unarchived:
            request_debug["selected"] = True
            debug["selected_source"] = "search_executors_unarchived_terminal"
            debug["outcome"] = "found"
            debug["requests"].append(request_debug)
            return unarchived[0], warnings
        request_debug["selected"] = False
        debug["outcome"] = "empty"
    except Exception as exc:
        error = f"active executor lookup failed for controller_id '{controller_id}': {type(exc).__name__}: {exc}"
        request_debug["error"] = error
        warnings.append(error)
    debug["requests"].append(request_debug)
    return None, warnings or [
        f"no active executor found for controller_id '{controller_id}'"
    ]


def _reconcile_pnl(
    net_pnl_quote: Any, filled_amount_quote: Any, raw_net_pnl_pct: Any
) -> dict[str, Any]:
    net_quote = _to_float(net_pnl_quote)
    filled_quote = _to_float(filled_amount_quote)
    raw_numeric = _to_float(raw_net_pnl_pct)
    derived = (
        net_quote / filled_quote
        if net_quote is not None and filled_quote is not None and filled_quote > 0
        else None
    )
    result = {
        "estimated_net_pnl_quote": net_quote,
        "filled_amount_quote": filled_quote,
        "raw_net_pnl_pct": raw_net_pnl_pct,
        "pnl_ratio_from_quote": derived,
        "estimated_net_pnl_pct": None,
        "pnl_pct_interpretation": None,
        "pnl_reconciliation_status": "unavailable",
    }
    if derived is None:
        return result
    if raw_numeric is None:
        result.update(
            estimated_net_pnl_pct=derived,
            pnl_pct_interpretation="derived-from-quote",
            pnl_reconciliation_status="derived-only",
        )
        return result

    explicit_percent = isinstance(
        raw_net_pnl_pct, str
    ) and raw_net_pnl_pct.strip().endswith("%")
    candidates = [(raw_numeric, "ratio")]
    if not explicit_percent:
        candidates.append((raw_numeric / 100.0, "percentage-points"))
    else:
        candidates = [(raw_numeric, "percentage-points-string")]
    ratio, interpretation = min(candidates, key=lambda item: abs(item[0] - derived))
    tolerance = max(1e-6, abs(derived) * 0.05)
    if abs(ratio - derived) <= tolerance:
        result.update(
            estimated_net_pnl_pct=derived,
            pnl_pct_interpretation=interpretation,
            pnl_reconciliation_status="reconciled",
        )
    else:
        result.update(
            pnl_pct_interpretation="mismatch",
            pnl_reconciliation_status="mismatch",
        )
    return result


def _find_number(value: Any, keys: set[str]) -> float | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in keys:
                parsed = _to_float(item)
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


def _rebalance_cost_quote(lifecycle_state: dict[str, Any]) -> float | None:
    rebalance = lifecycle_state.get("rebalance") or {}
    status = _as_state(rebalance.get("status") or "NOT_REQUIRED")
    if status == "NOT_REQUIRED" or not rebalance:
        return 0.0
    if status != "CONFIRMED":
        return None
    settlement = rebalance.get("settlement") or {}
    plan = lifecycle_state.get("rebalance_plan") or {}
    inventory = rebalance.get("post_swap_inventory") or {}
    amount_in = _find_number(
        settlement,
        {
            "amount_in",
            "amountin",
            "input_amount",
            "inputamount",
            "in_amount",
            "inamount",
        },
    )
    amount_out = _find_number(
        settlement,
        {
            "amount_out",
            "amountout",
            "output_amount",
            "outputamount",
            "out_amount",
            "outamount",
        },
    )
    if amount_in is None:
        before = _to_float(plan.get("available_quote"))
        after = _to_float(inventory.get("quote"))
        if before is not None and after is not None:
            amount_in = max(0.0, before - after)
    if amount_out is None:
        before = _to_float(plan.get("available_base"))
        after = _to_float(inventory.get("base"))
        if before is not None and after is not None:
            amount_out = max(0.0, after - before)
    price = _to_float(plan.get("current_price"))
    if amount_in is None or amount_out is None or price is None:
        return None
    gas = _find_number(
        settlement,
        {
            "gas_fee_quote",
            "gasfeequote",
            "transaction_cost_quote",
            "transactioncostquote",
        },
    )
    return max(0.0, amount_in - amount_out * price) + (gas or 0.0)


def _extract_api_executor(executor: dict[str, Any]) -> dict[str, Any]:
    config = executor.get("config") if isinstance(executor.get("config"), dict) else {}
    custom_info = (
        executor.get("custom_info")
        if isinstance(executor.get("custom_info"), dict)
        else {}
    )
    out_of_range_seconds = _to_float(custom_info.get("out_of_range_seconds"))
    pnl = _reconcile_pnl(
        executor.get("net_pnl_quote"),
        executor.get("filled_amount_quote"),
        executor.get("net_pnl_pct"),
    )
    position_address = custom_info.get("position_address")
    realized_lower = _to_float(custom_info.get("lower_price"))
    realized_upper = _to_float(custom_info.get("upper_price"))
    requested_lower = _to_float(config.get("lower_price"))
    requested_upper = _to_float(config.get("upper_price"))
    return {
        "executor_id": evidence.executor_id(executor),
        "executor_state": _as_state(executor.get("status")),
        "lp_state": _as_state(custom_info.get("state")),
        "pool_address": config.get("pool_address"),
        "trading_pair": executor.get("trading_pair") or config.get("trading_pair"),
        "position_address": position_address,
        "current_price": _to_float(custom_info.get("current_price")),
        "lower_price": (
            realized_lower
            if realized_lower is not None
            else requested_lower if not position_address else None
        ),
        "upper_price": (
            realized_upper
            if realized_upper is not None
            else requested_upper if not position_address else None
        ),
        "requested_lower_price": requested_lower,
        "requested_upper_price": requested_upper,
        "range_bound_source": (
            "custom_info_on_chain"
            if realized_lower is not None and realized_upper is not None
            else (
                "requested_config_before_position"
                if not position_address
                else "on_chain_bounds_unavailable"
            )
        ),
        "lower_limit_price": _to_float(config.get("lower_limit_price")),
        "upper_limit_price": _to_float(config.get("upper_limit_price")),
        "base_amount_current": _to_float(custom_info.get("base_amount")),
        "quote_amount_current": _to_float(custom_info.get("quote_amount")),
        "base_fees_accrued": _to_float(custom_info.get("base_fee")),
        "quote_fees_accrued": _to_float(custom_info.get("quote_fee")),
        "fees_earned_quote": _to_float(custom_info.get("fees_earned_quote")),
        "tx_fees_paid": _to_float(custom_info.get("tx_fee")),
        "position_rent": _to_float(custom_info.get("position_rent")),
        "rent_refunded": _to_float(custom_info.get("position_rent_refunded")),
        **pnl,
        "opened_at": executor.get("created_at") or executor.get("timestamp"),
        "position_age_minutes": None,
        "time_out_of_range_minutes": (
            None if out_of_range_seconds is None else out_of_range_seconds / 60.0
        ),
    }


def _age_minutes(fields: dict[str, Any], now: datetime) -> float | None:
    opened_at = _to_datetime(fields.get("opened_at"))
    if opened_at is None:
        return None
    return max(0.0, (now - opened_at).total_seconds() / 60.0)


def _distances(fields: dict[str, Any]) -> dict[str, Any]:
    price = fields.get("current_price")
    lower = fields.get("lower_price")
    upper = fields.get("upper_price")
    lower_limit = fields.get("lower_limit_price")
    upper_limit = fields.get("upper_limit_price")
    distances: dict[str, Any] = {}
    if price is not None and lower is not None:
        distances["distance_to_lower_range_pct"] = round((price - lower) / price, 6)
    if price is not None and upper is not None:
        distances["distance_to_upper_range_pct"] = round((upper - price) / price, 6)
    if price is not None and lower_limit is not None:
        distances["distance_to_lower_limit_pct"] = round(
            (price - lower_limit) / price, 6
        )
    if price is not None and upper_limit is not None:
        distances["distance_to_upper_limit_pct"] = round(
            (upper_limit - price) / price, 6
        )
    return distances


def _recommend(
    fields: dict[str, Any], config: Config, now: datetime
) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    state = fields["executor_state"]
    if state in FAILED_STATES:
        return (
            "write-audit",
            "executor_failed",
            ["executor state is FAILED; audit before any new open"],
        )
    if state in TERMINAL_STATES:
        return "write-audit", "executor_complete", warnings
    if state == "CLOSING":
        return "continue", "executor_closing", warnings
    if state == "SHUTTING_DOWN":
        return "continue", "executor_closing", warnings
    if state == "OPENING":
        return "continue", "executor_opening", warnings
    if state not in ACTIVE_STATES:
        return (
            "manual-review",
            "unknown_executor_state",
            [f"unknown executor state: {state}"],
        )

    lp_state = fields.get("lp_state")
    if lp_state in {"CLOSING", "SWAPPING"}:
        return "continue", f"lp_{lp_state.lower()}", warnings
    if lp_state in {"NOT_ACTIVE", "OPENING"}:
        return "continue", f"lp_{lp_state.lower()}", warnings
    if lp_state not in {"IN_RANGE", "OUT_OF_RANGE"}:
        return "manual-review", "unknown_lp_state", [f"unknown LP state: {lp_state}"]

    active_count = fields.get("active_executor_count")
    if isinstance(active_count, (int, float)) and active_count > 1:
        warnings.append(
            f"controller has {int(active_count)} active executors; Orca expects one"
        )
        return "manual-review", "multiple_active_executors", warnings

    if not fields.get("position_address"):
        warnings.append("position address missing from executor API response")
        if config.missing_position_ticks + 1 < config.missing_position_grace_ticks:
            return "continue", "missing_position_within_grace", warnings
        return "manual-review", "missing_position", warnings

    price = fields.get("current_price")
    lower = fields.get("lower_price")
    upper = fields.get("upper_price")
    lower_limit = fields.get("lower_limit_price")
    upper_limit = fields.get("upper_limit_price")
    if price is not None and lower_limit is not None and price <= lower_limit:
        return "close", "lower_limit_crossed", warnings
    if price is not None and upper_limit is not None and price >= upper_limit:
        return "close", "upper_limit_crossed", warnings

    age_minutes = _age_minutes(fields, now)
    if age_minutes is None:
        warnings.append("position age could not be established from executor evidence")
        return "manual-review", "position_age_unknown", warnings
    if age_minutes >= config.position_max_age_minutes:
        return "close", "time_limit_reached", warnings

    pnl_pct = fields.get("estimated_net_pnl_pct")
    pnl_status = fields.get("pnl_reconciliation_status")
    if pnl_status not in {"reconciled", "derived-only"}:
        warnings.append("PnL percentage could not be reconciled against quote PnL")
        return "manual-review", "pnl_unreconciled", warnings
    if config.position_take_profit_net_pnl_ratio is not None and pnl_pct is not None:
        if pnl_pct >= config.position_take_profit_net_pnl_ratio:
            return "close", "take_profit_reached", warnings
    if config.position_stop_loss_net_pnl_ratio is not None and pnl_pct is not None:
        if pnl_pct <= -abs(config.position_stop_loss_net_pnl_ratio):
            return "close", "stop_loss_reached", warnings

    out_of_range = fields.get("lp_state") == "OUT_OF_RANGE"
    out_of_range = out_of_range or (
        price is not None and lower is not None and price < lower
    )
    out_of_range = out_of_range or (
        price is not None and upper is not None and price > upper
    )
    if out_of_range:
        warnings.append("position is out of LP range")
        out_of_range_minutes = fields.get("time_out_of_range_minutes")
        if config.soft_out_of_range_grace_minutes <= 0:
            return "close", "soft_out_of_range_grace_exceeded", warnings
        if out_of_range_minutes is None:
            warnings.append("out-of-range duration missing from executor API response")
            return "manual-review", "out_of_range_duration_unknown", warnings
        if out_of_range_minutes >= config.soft_out_of_range_grace_minutes:
            return "close", "soft_out_of_range_grace_exceeded", warnings
        return "continue", "out_of_range_within_grace", warnings

    return "continue", "normal_supervision", warnings


def _self_check() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def fields(**overrides: Any) -> dict[str, Any]:
        data = {
            "executor_state": "RUNNING",
            "lp_state": "IN_RANGE",
            "position_address": "position-1",
            "current_price": 100.0,
            "lower_price": 90.0,
            "upper_price": 110.0,
            "lower_limit_price": 80.0,
            "upper_limit_price": 120.0,
            "estimated_net_pnl_pct": 0.0,
            "pnl_reconciliation_status": "reconciled",
            "opened_at": now,
            "time_out_of_range_minutes": 0.0,
        }
        data.update(overrides)
        return data

    config = Config(controller_id="lpmaxxing.orca_1", execution_mode="dry_run")
    assert _recommend(fields(estimated_net_pnl_pct=0.005), config, now)[:2] == (
        "close",
        "take_profit_reached",
    )
    assert _recommend(fields(estimated_net_pnl_pct=-0.01), config, now)[:2] == (
        "close",
        "stop_loss_reached",
    )
    assert _recommend(fields(opened_at=now - timedelta(minutes=481)), config, now)[
        :2
    ] == ("close", "time_limit_reached")
    assert _recommend(fields(position_address=None), config, now)[:2] == (
        "continue",
        "missing_position_within_grace",
    )
    assert _recommend(
        fields(position_address=None),
        config.model_copy(update={"missing_position_ticks": 1}),
        now,
    )[:2] == (
        "manual-review",
        "missing_position",
    )
    assert _recommend(
        fields(lp_state="OUT_OF_RANGE", time_out_of_range_minutes=11), config, now
    )[:2] == ("close", "soft_out_of_range_grace_exceeded")
    assert _recommend(fields(active_executor_count=2), config, now)[:2] == (
        "manual-review",
        "multiple_active_executors",
    )
    assert _recommend(fields(lp_state="CLOSING"), config, now)[:2] == (
        "continue",
        "lp_closing",
    )
    assert _recommend(fields(lp_state="SWAPPING"), config, now)[:2] == (
        "continue",
        "lp_swapping",
    )
    assert _recommend(fields(executor_state="SHUTTING_DOWN"), config, now)[:2] == (
        "continue",
        "executor_closing",
    )
    pnl = _reconcile_pnl(0.0264518, 9.9395858, 0.2661259)
    assert pnl["pnl_reconciliation_status"] == "reconciled", pnl
    assert pnl["pnl_pct_interpretation"] == "percentage-points", pnl
    assert abs(pnl["estimated_net_pnl_pct"] - 0.002661259) < 0.000001, pnl
    extracted = _extract_api_executor(
        {
            "id": "executor-1",
            "status": "RUNNING",
            "config": {"lower_price": 90, "upper_price": 110},
            "custom_info": {
                "position_address": "position-1",
                "lower_price": 90.5,
                "upper_price": 109.5,
            },
        }
    )
    assert extracted["lower_price"] == 90.5, extracted
    assert extracted["upper_price"] == 109.5, extracted
    assert extracted["range_bound_source"] == "custom_info_on_chain", extracted

    class Executors:
        async def search_executors(self, **kwargs: Any) -> list[dict[str, Any]]:
            return [
                {
                    "id": "executor-1",
                    "controller_id": "lpmaxxing.orca_1",
                    "status": "COMPLETE",
                }
            ]

    debug = {"requests": []}
    executor, _ = asyncio.run(
        _fetch_active_executor_by_controller(
            type("Client", (), {"executors": Executors()})(),
            "lpmaxxing.orca_1",
            {"executor-1"},
            debug,
        )
    )
    assert executor is None, debug
    assert debug["outcome"] == "empty", debug
    assert evidence.recognized_executor_list([]) == ([], True)
    assert evidence.recognized_executor_list([{"unexpected": "record"}]) == ([], False)


def _format_money(value: Any) -> str:
    parsed = _to_float(value)
    return "n/a" if parsed is None else f"${parsed:,.4f}"


def _format_number(value: Any, digits: int = 4) -> str:
    parsed = _to_float(value)
    return "n/a" if parsed is None else f"{parsed:,.{digits}f}"


def _format_pct(value: Any) -> str:
    parsed = _to_float(value)
    return "n/a" if parsed is None else f"{parsed * 100:+.2f}%"


def _range_status(position: dict[str, Any]) -> str:
    price = _to_float(position.get("current_price"))
    lower = _to_float(position.get("lower_price"))
    upper = _to_float(position.get("upper_price"))
    if price is None or lower is None or upper is None:
        return "unknown"
    if lower <= price <= upper:
        return "in-range"
    return "out-of-range"


def _format_position_text(payload: dict[str, Any]) -> str:
    position = payload.get("position") or {}
    warnings = payload.get("warnings") or []
    lines = [f"Orca LP Position Report: {payload.get('report_status', 'unknown')}"]
    lines.extend(
        [
            f"Executor: {payload.get('executor_id') or position.get('executor_id') or 'none'}",
            f"State: {payload.get('executor_state') or position.get('executor_state') or 'n/a'}",
            f"Action: {payload.get('recommended_supervision_action', 'n/a')}",
            f"Reason: {payload.get('reason', 'n/a')}",
            f"Active position: {payload.get('has_active_position')}",
        ]
    )
    if payload.get("trading_pair") or position.get("trading_pair"):
        lines.append(
            f"Pair: {payload.get('trading_pair') or position.get('trading_pair')}"
        )
    if position:
        lines.append(f"PnL: {_format_pct(position.get('estimated_net_pnl_pct'))}")
        lines.append(f"Range status: {_range_status(position)}")
    if warnings:
        lines.append(f"Warnings: {len(warnings)}")
    summary = payload.get("agent_prompt_summary")
    if summary:
        lines.extend(["", summary])
    return "\n".join(lines)


def _position_rows(position: dict[str, Any]) -> list[dict[str, Any]]:
    if not position:
        return []
    keys = [
        "executor_id",
        "executor_state",
        "lp_state",
        "pool_address",
        "trading_pair",
        "position_address",
        "current_price",
        "lower_price",
        "upper_price",
        "lower_limit_price",
        "upper_limit_price",
        "base_amount_current",
        "quote_amount_current",
        "base_fees_accrued",
        "quote_fees_accrued",
        "fees_earned_quote",
        "tx_fees_paid",
        "position_rent",
        "rent_refunded",
        "estimated_net_pnl_quote",
        "filled_amount_quote",
        "raw_net_pnl_pct",
        "pnl_ratio_from_quote",
        "pnl_pct_interpretation",
        "pnl_reconciliation_status",
        "estimated_net_pnl_pct",
        "position_age_minutes",
        "time_out_of_range_minutes",
    ]
    rows = []
    for key in keys:
        value = position.get(key)
        if value is None:
            continue
        if key == "raw_net_pnl_pct":
            display = value
        elif key.endswith("_pct"):
            display = _format_pct(value)
        elif "quote" in key and isinstance(value, (int, float)):
            display = _format_money(value)
        else:
            display = value
        rows.append({"Field": key, "Value": display})
    return rows


def _distance_rows(distances: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"Metric": key, "Value": _format_pct(value)} for key, value in distances.items()
    ]


async def _save_position_report(payload: dict[str, Any]) -> None:
    """Create a standard Condor dashboard report; report failures must not fail the routine."""
    try:
        from condor.reports import ReportBuilder

        position = payload.get("position") or {}
        distances = payload.get("distances") or {}
        warnings = payload.get("warnings") or []
        status = payload.get("report_status", "unknown")
        action = payload.get("recommended_supervision_action", "n/a")

        builder = ReportBuilder("Orca LP Position Report")
        builder.source("routine", "lp_position_report").tags(
            ["orca", "lp", "position", "agent"]
        ).manual_order()
        builder.kpi(
            "Status", str(status), trend="up" if status == "success" else "down"
        )
        builder.kpi(
            "Action",
            str(action),
            trend="down" if action in {"close", "manual-review"} else "neutral",
        )
        builder.kpi(
            "State",
            str(
                payload.get("executor_state") or position.get("executor_state") or "n/a"
            ),
        )
        active = payload.get("has_active_position")
        builder.kpi(
            "Active Position",
            "unknown" if active is None else ("yes" if active else "no"),
        )
        builder.kpi("PnL", _format_pct(position.get("estimated_net_pnl_pct")))
        builder.kpi("Age", _format_number(position.get("position_age_minutes"), 2))
        builder.markdown(
            "## Agent Summary\n"
            f"{payload.get('agent_prompt_summary', 'No summary available.')}\n\n"
            f"Reason: `{payload.get('reason', 'n/a')}`\n\n"
            f"Range status: `{_range_status(position)}`"
        )
        position_rows = _position_rows(position)
        if position_rows:
            builder.markdown("## Extracted Position Fields")
            builder.table(position_rows)
        distance_rows = _distance_rows(distances)
        if distance_rows:
            builder.markdown("## Distance Metrics")
            builder.table(distance_rows)
        builder.markdown(
            "## Warnings\n"
            + (
                "\n".join(f"- {warning}" for warning in warnings)
                if warnings
                else "- None."
            )
        )
        input_config = payload.get("input_config")
        if input_config:
            builder.markdown(
                "## Routine Input Config\n"
                "Input parameters supplied by the agent when it ran this report.\n\n"
                "```json\n"
                f"{json.dumps(input_config, indent=2, sort_keys=True, default=str)}\n"
                "```"
            )
        executor_api_debug = payload.get("executor_api_debug")
        if executor_api_debug:
            builder.markdown(
                "## Executor API Debug\n"
                "Raw response(s) returned by the executor API during this routine run.\n\n"
                "```json\n"
                f"{json.dumps(executor_api_debug, indent=2, sort_keys=True, default=str)}\n"
                "```"
            )
        builder.markdown(
            "## Debug JSON Payload\n"
            "```json\n"
            f"{json.dumps(payload, indent=2, sort_keys=True, default=str)}\n"
            "```"
        )
        await builder.save()
    except Exception:
        return


def _outcome(payload: dict[str, Any]) -> dict[str, Any]:
    recommended = str(payload.get("recommended_supervision_action") or "manual-review")
    if recommended == "no-active-position":
        action = NextAction.RUN_POOL_SCAN
    elif recommended == "session-stop-recorded":
        action = NextAction.NO_ACTION
    else:
        try:
            action = NextAction(recommended)
        except ValueError:
            action = NextAction.MANUAL_REVIEW
    lifecycle = (
        payload.get("lifecycle_state_update")
        or payload.get("lifecycle_resume")
        or (payload.get("session_state") or {}).get("active_position")
        or {}
    )
    executor_id = (
        payload.get("executor_id")
        or (payload.get("position") or {}).get("executor_id")
        or lifecycle.get("executor_id")
    )
    update = payload.get("lifecycle_state_update") or lifecycle
    input_config = payload.get("input_config") or {}
    session_state = payload.get("session_state") or {}
    terms = session_state.get("terms") or {}
    controller_id = input_config.get("controller_id")
    execution_mode = input_config.get("execution_mode")
    outcome_reason = str(payload.get("reason") or "unknown")
    if action == NextAction.CLOSE:
        if executor_id:
            arguments = {
                "action": "stop",
                "executor_id": executor_id,
                "keep_position": False,
            }
        else:
            action = NextAction.MANUAL_REVIEW
            outcome_reason = "close-dispatch-missing-executor-id"
            arguments = {"controller_id": controller_id}
    elif action == NextAction.STOP_AGENT:
        if controller_id:
            arguments = {"action": "stop_agent", "agent_id": controller_id}
        else:
            action = NextAction.MANUAL_REVIEW
            outcome_reason = "stop-dispatch-missing-controller-id"
            arguments = {}
    elif action == NextAction.WRITE_AUDIT:
        arguments = payload.get("audit_input") or {}
        required = {
            "controller_id": arguments.get("controller_id"),
            "execution_mode": arguments.get("execution_mode"),
            "final_executor": arguments.get("final_executor"),
        }
        missing = [key for key, value in required.items() if value in (None, "", {})]
        if missing:
            action = NextAction.MANUAL_REVIEW
            outcome_reason = "audit-dispatch-incomplete: " + ", ".join(missing)
            arguments = {"controller_id": controller_id}
    elif action == NextAction.RUN_POOL_SCAN:
        arguments = {
            "execution_mode": execution_mode,
            "controller_id": controller_id,
            "risk_profile": terms.get("risk_profile") or "yield_focused",
            "total_amount_quote": terms.get("total_amount_quote") or 10,
        }
        if (
            not execution_mode
            or not controller_id
            or (
                execution_mode == "loop"
                and (
                    not terms.get("risk_profile")
                    or terms.get("total_amount_quote") is None
                )
            )
        ):
            action = NextAction.MANUAL_REVIEW
            outcome_reason = "scan-dispatch-incomplete"
            arguments = {"controller_id": controller_id}
    elif action == NextAction.RESUME_REBALANCE:
        arguments = {
            "execution_mode": execution_mode,
            "controller_id": controller_id,
            "selected_candidate": lifecycle.get("selected_candidate") or {},
            "gateway_pool_info": lifecycle.get("gateway_pool_info") or {},
            "rebalance_plan": lifecycle.get("rebalance_plan") or {},
            "total_amount_quote": lifecycle.get("total_amount_quote")
            or terms.get("total_amount_quote"),
            "wallet_account_name": lifecycle.get("wallet_account_name"),
            "wallet_connector_name": lifecycle.get("wallet_connector_name"),
        }
        missing = [key for key, value in arguments.items() if value in (None, "", {})]
        if missing:
            action = NextAction.MANUAL_REVIEW
            outcome_reason = "rebalance-resume-incomplete: " + ", ".join(missing)
            arguments = {"controller_id": controller_id}
    elif action == NextAction.RESUME_PREFLIGHT:
        arguments = {
            "execution_mode": execution_mode,
            "controller_id": controller_id,
            "selected_candidate": lifecycle.get("selected_candidate") or {},
            "gateway_pool_info": lifecycle.get("gateway_pool_info") or {},
            "total_amount_quote": lifecycle.get("total_amount_quote")
            or terms.get("total_amount_quote"),
            "fetch_wallet_balances": True,
            "wallet_account_name": lifecycle.get("wallet_account_name"),
            "wallet_connector_name": lifecycle.get("wallet_connector_name"),
        }
        missing = [
            key
            for key, value in arguments.items()
            if key != "fetch_wallet_balances" and value in (None, "", {})
        ]
        if missing:
            action = NextAction.MANUAL_REVIEW
            outcome_reason = "preflight-resume-incomplete: " + ", ".join(missing)
            arguments = {"controller_id": controller_id}
    elif action == NextAction.MANUAL_REVIEW:
        arguments = {"controller_id": controller_id}
    else:
        arguments = {}
    return attach_outcome(
        payload,
        routine="lp_position_report",
        next_action=action,
        reason=outcome_reason,
        arguments=arguments,
        mutation={
            "recommended_supervision_action": recommended,
            "phase": update.get("phase"),
            "session_status": (payload.get("session_state") or {}).get(
                "session_status"
            ),
        },
        position_number=update.get("position_number"),
        executor_id=str(executor_id) if executor_id else None,
    )


async def _finish(payload: dict[str, Any], suffix: str = "") -> RoutineResult:
    _outcome(payload)
    payload = evidence.redact(payload, datetime_iso=False)
    await _save_position_report(payload)
    outcome_text = (
        "\n\nOutcome:\n```json\n"
        + json.dumps(payload["outcome"], indent=2, sort_keys=True, default=str)
        + "\n```"
    )
    return RoutineResult(
        text=_format_position_text(payload) + outcome_text + suffix,
        table_data=_position_rows(payload.get("position") or {}),
        table_columns=["Field", "Value"],
    )


async def run(config: Config, context: Any) -> RoutineResult:
    input_config = evidence.redact(config.model_dump())
    executor_api_debug: dict[str, Any] = {}
    lifecycle_state: dict[str, Any] = {}
    session_state: dict[str, Any] = {}
    try:
        now = (
            _utc_now()
            if config.execution_mode == "loop"
            else (_to_datetime(config.now_timestamp) or _utc_now())
        )
        if not config.controller_id:
            raise ValueError(
                "controller_id is required and must come from the current Condor tick"
            )
        if session_execution_mode(config.controller_id) != config.execution_mode:
            raise ValueError("execution_mode does not match the current SESSION_MODE")
        if config.execution_mode == "loop":
            session_state = load_session_state(config.controller_id)
            lifecycle_state = session_state.get("active_position") or {}
            exit_policy = session_exit_policy(config.controller_id)
            config = config.model_copy(
                update={
                    "executor_id": lifecycle_state.get("executor_id"),
                    "archived_executor_ids": list(
                        (session_state.get("completed") or {}).get("executor_ids") or []
                    ),
                    **exit_policy,
                }
            )
            input_config = evidence.redact(config.model_dump())
        session_status = str(session_state.get("session_status") or "running")
        if session_status == "cycle_complete":
            with lifecycle_lock(config.controller_id):
                session_state = resume_session_cycle(config.controller_id, now)
            session_status = str(session_state.get("session_status") or "")
            if session_status == "cycle_complete":
                payload = {
                    "report_status": "success",
                    "timestamp": now.isoformat(),
                    "has_active_position": False,
                    "recommended_supervision_action": "wait-next-cycle",
                    "reason": "cycle_resume_not_due",
                    "warnings": [],
                    "input_config": input_config,
                    "executor_api_debug": {},
                    "position": None,
                    "distances": {},
                    "session_state": evidence.redact(session_state),
                    "agent_prompt_summary": "The completed position is archived; wait until the next scheduled cycle before scanning.",
                }
                return await _finish(payload)
        if session_status == "stop_pending":
            if lifecycle_state:
                raise ValueError(
                    "stop-pending Orca session still has an active-position record"
                )
            payload = {
                "report_status": "success",
                "timestamp": now.isoformat(),
                "has_active_position": False,
                "recommended_supervision_action": "stop-agent",
                "reason": "session_stop_pending",
                "warnings": [],
                "input_config": input_config,
                "executor_api_debug": {},
                "position": None,
                "distances": {},
                "session_state": evidence.redact(session_state),
                "agent_prompt_summary": "The Orca session is fully audited and stop-pending. Call manage_trading_agent(stop_agent) for this controller and do nothing else.",
            }
            return await _finish(payload)
        if session_status == "manual_review":
            payload = {
                "report_status": "failed-closed",
                "timestamp": now.isoformat(),
                "has_active_position": None,
                "recommended_supervision_action": "manual-review",
                "reason": "session_manual_review",
                "warnings": [str(session_state.get("manual_review") or "")],
                "input_config": input_config,
                "executor_api_debug": {},
                "position": None,
                "distances": {},
                "session_state": evidence.redact(session_state),
                "agent_prompt_summary": "The Orca session is blocked for manual review; do not scan or re-enter.",
            }
            return await _finish(payload)
        executor, lookup_warnings, executor_api_debug = await _fetch_executor_from_api(
            config, context
        )

        if not executor:
            outcome = executor_api_debug.get("outcome")
            if outcome != "empty":
                payload = {
                    "report_status": "failed-closed",
                    "timestamp": now.isoformat(),
                    "has_active_position": None,
                    "recommended_supervision_action": "manual-review",
                    "reason": f"executor_lookup_{outcome or 'failed'}",
                    "warnings": lookup_warnings,
                    "input_config": input_config,
                    "executor_api_debug": executor_api_debug,
                    "position": None,
                    "distances": {},
                    "agent_prompt_summary": "Manual review: executor state could not be established; do not scan or open.",
                }
                return await _finish(payload)
            phase = str(lifecycle_state.get("phase") or "")
            if phase in {
                "rebalance_required",
                "rebalance_submitted",
                "rebalance_confirmed_waiting_balance",
            }:
                payload = {
                    "report_status": "success",
                    "timestamp": now.isoformat(),
                    "has_active_position": False,
                    "recommended_supervision_action": "resume-rebalance",
                    "reason": phase,
                    "warnings": lookup_warnings,
                    "input_config": input_config,
                    "executor_api_debug": executor_api_debug,
                    "position": None,
                    "distances": {},
                    "lifecycle_resume": evidence.redact(lifecycle_state),
                    "agent_prompt_summary": "Resume the persisted rebalance; do not rescan or submit a second swap.",
                }
                return await _finish(
                    payload,
                    "\n\nLifecycle resume:\n```json\n"
                    + json.dumps(
                        evidence.redact(lifecycle_state),
                        indent=2,
                        sort_keys=True,
                        default=str,
                    )
                    + "\n```",
                )
            if phase == "rebalance_confirmed":
                payload = {
                    "report_status": "success",
                    "timestamp": now.isoformat(),
                    "has_active_position": False,
                    "recommended_supervision_action": "resume-preflight",
                    "reason": phase,
                    "warnings": lookup_warnings,
                    "input_config": input_config,
                    "executor_api_debug": executor_api_debug,
                    "position": None,
                    "distances": {},
                    "lifecycle_resume": evidence.redact(lifecycle_state),
                    "agent_prompt_summary": "The rebalance is confirmed; refresh Gateway evidence and rerun preflight for the persisted candidate without rescanning.",
                }
                return await _finish(
                    payload,
                    "\n\nLifecycle resume:\n```json\n"
                    + json.dumps(
                        evidence.redact(lifecycle_state),
                        indent=2,
                        sort_keys=True,
                        default=str,
                    )
                    + "\n```",
                )
            unfinished = lifecycle_state.get("phase") in {
                "rebalance_submission_intent",
                "rebalance_submission_uncertain",
                "rebalance_failed",
                "rebalance_blocked",
                "preflight_ready",
                "opened",
                "supervising",
                "closing",
                "terminal",
            }
            session_trigger = None
            if not unfinished:
                session_trigger = session_stop_trigger(session_state, now)
                if session_trigger:
                    with lifecycle_lock(config.controller_id):
                        session_state = mark_session_stop(
                            config.controller_id,
                            session_trigger,
                            active_position=False,
                        )
            payload = {
                "report_status": "success",
                "timestamp": now.isoformat(),
                "has_active_position": False,
                "recommended_supervision_action": (
                    "manual-review"
                    if unfinished
                    else (
                        "session-stop-recorded"
                        if session_trigger
                        else "no-active-position"
                    )
                ),
                "reason": (
                    "unfinished_lifecycle_without_executor"
                    if unfinished
                    else (
                        session_trigger["reason"]
                        if session_trigger
                        else "no executor found in API"
                    )
                ),
                "warnings": lookup_warnings,
                "input_config": input_config,
                "executor_api_debug": executor_api_debug,
                "position": None,
                "distances": {},
                "session_state": evidence.redact(session_state),
                "agent_prompt_summary": (
                    "Manual review: lifecycle state exists but no executor was found; do not scan or re-enter."
                    if unfinished
                    else (
                        "A session stop was durably recorded while flat. Do not scan; the next tick must stop the agent."
                        if session_trigger
                        else "No active Orca LP executor was found in the executor API; agent may run pool scan if risk limits allow."
                    )
                ),
            }
            return await _finish(payload)

        if not lifecycle_state:
            payload = {
                "report_status": "failed-closed",
                "timestamp": now.isoformat(),
                "has_active_position": True,
                "recommended_supervision_action": "manual-review",
                "reason": "active_executor_without_lifecycle",
                "warnings": lookup_warnings
                + ["an active executor exists without an active Orca position record"],
                "input_config": input_config,
                "executor_api_debug": executor_api_debug,
                "position": None,
                "distances": {},
                "agent_prompt_summary": "Manual review: an untracked active executor blocks scanning and re-entry.",
            }
            return await _finish(payload)
        expected_executor_id = str(lifecycle_state.get("executor_id") or "").strip()
        if expected_executor_id:
            plan_mismatches = _executor_lifecycle_mismatches(executor, lifecycle_state)
            mismatch_reason = "persisted_executor_mismatch"
        else:
            plan_mismatches = (
                ["lifecycle_phase"]
                if lifecycle_state.get("phase") != "preflight_ready"
                else _executor_plan_mismatches(executor, lifecycle_state)
            )
            mismatch_reason = "unmatched_executor_after_create"
        if plan_mismatches:
            payload = {
                "report_status": "failed-closed",
                "timestamp": now.isoformat(),
                "has_active_position": True,
                "recommended_supervision_action": "manual-review",
                "reason": mismatch_reason,
                "warnings": lookup_warnings
                + [
                    "active executor does not match the persisted lifecycle plan: "
                    + ", ".join(plan_mismatches)
                ],
                "executor_plan_mismatches": plan_mismatches,
                "input_config": input_config,
                "executor_api_debug": executor_api_debug,
                "position": None,
                "distances": {},
                "agent_prompt_summary": "Manual review: executor identity could not be reconciled with the persisted plan.",
            }
            return await _finish(payload)

        executor_api_debug["executor_source_used"] = "api"
        fields = _extract_api_executor(executor)
        fields["active_executor_count"] = executor_api_debug.get(
            "active_executor_count"
        )
        age_minutes = _age_minutes(fields, now)
        if age_minutes is not None:
            fields["position_age_minutes"] = round(age_minutes, 4)
        distances = _distances(fields)
        persisted_missing_ticks = int(
            lifecycle_state.get("missing_position_ticks") or 0
        )
        missing_position_observed = (
            not fields.get("position_address")
            and fields.get("executor_state") == "RUNNING"
            and fields.get("lp_state") in {"IN_RANGE", "OUT_OF_RANGE"}
        )
        config = config.model_copy(
            update={
                "missing_position_ticks": (
                    max(config.missing_position_ticks, persisted_missing_ticks)
                    if missing_position_observed
                    else 0
                )
            }
        )
        action, reason, warnings = _recommend(fields, config, now)
        warnings = lookup_warnings + warnings
        rebalance_cost = _rebalance_cost_quote(lifecycle_state)
        active_net_pnl = fields.get("estimated_net_pnl_quote")
        if (
            active_net_pnl is None
            or rebalance_cost is None
            or fields.get("pnl_reconciliation_status")
            not in {"reconciled", "derived-only"}
        ):
            active_session_pnl = None
        else:
            active_session_pnl = active_net_pnl - rebalance_cost
        session_trigger = session_state.get("global_stop") or session_stop_trigger(
            session_state, now, active_session_pnl
        )
        if session_trigger and action != "write-audit":
            if config.execution_mode == "loop":
                with lifecycle_lock(config.controller_id):
                    session_state = mark_session_stop(
                        config.controller_id,
                        session_trigger,
                        active_position=True,
                    )
            action = "close"
            reason = str(session_trigger.get("reason") or "session_stop_reached")
            warnings.append(
                "session-level stop reached; close and audit before stopping the agent"
            )
        if action == "close" and config.execution_mode != "loop":
            action = "manual-review"
            reason = "dry_run_close_blocked"
            warnings.append(
                "dry-run supervision may recommend review but cannot stop an executor"
            )
        if lifecycle_state.get("phase") == "closing" and action in {
            "continue",
            "close",
        }:
            if fields["executor_state"] == "RUNNING":
                action = "manual-review"
                reason = "close_not_confirmed"
                warnings.append(
                    "close was requested but the executor is still RUNNING; verify the stop result before retrying"
                )
            else:
                action = "continue"
                reason = "close_in_progress"
                warnings.append(
                    "persisted lifecycle state is closing; wait for terminal executor evidence"
                )
        state_controller = str(lifecycle_state.get("controller_id") or "")
        if state_controller and state_controller != config.controller_id:
            action = "manual-review"
            reason = "lifecycle_state_controller_mismatch"
            warnings.append(
                f"persisted lifecycle controller '{state_controller}' does not match '{config.controller_id}'"
            )
        has_active = fields["executor_state"] in ACTIVE_STATES
        lifecycle_state_update = {
            **lifecycle_state,
            "controller_id": config.controller_id,
            "executor_id": fields.get("executor_id"),
            "missing_position_ticks": (
                config.missing_position_ticks + 1 if missing_position_observed else 0
            ),
        }
        if action == "close":
            lifecycle_state_update["phase"] = "closing"
            lifecycle_state_update["close_reason"] = reason
        elif action == "write-audit":
            lifecycle_state_update["phase"] = "terminal"
            lifecycle_state_update["terminal_executor"] = evidence.redact(executor)
        elif action == "continue":
            lifecycle_state_update["phase"] = (
                "closing"
                if reason
                in {
                    "close_in_progress",
                    "executor_closing",
                    "lp_closing",
                    "lp_swapping",
                }
                else "supervising"
            )
        if config.execution_mode == "loop" and action != "manual-review":
            try:
                with lifecycle_lock(config.controller_id):
                    current = load_lifecycle_state(config.controller_id)
                    current_executor_id = current.get("executor_id")
                    next_executor_id = lifecycle_state_update.get("executor_id")
                    if current_executor_id and current_executor_id != next_executor_id:
                        raise ValueError("active executor changed during supervision")
                    if action == "close" and current.get("phase") == "closing":
                        action = "continue"
                        reason = "close_in_progress"
                        warnings.append(
                            "another report already claimed the close transition; do not issue a duplicate stop"
                        )
                        lifecycle_state_update = current
                    else:
                        if action == "close" and current.get("phase") not in {
                            "preflight_ready",
                            "opened",
                            "supervising",
                        }:
                            raise ValueError(
                                "active position is not eligible for a close transition"
                            )
                        save_lifecycle_state(
                            config.controller_id,
                            {**current, **lifecycle_state_update},
                        )
            except Exception as exc:
                action = "manual-review"
                reason = "lifecycle_state_persistence_failed"
                warnings.append(
                    f"lifecycle state persistence failed: {type(exc).__name__}: {exc}"
                )
        summary = f"LP executor {fields.get('executor_id') or 'unknown'} state {fields['executor_state']} recommends {action} ({reason})."
        payload = {
            "report_status": "success",
            "timestamp": now.isoformat(),
            "has_active_position": has_active,
            "input_config": input_config,
            "executor_api_debug": executor_api_debug,
            "executor_id": fields.get("executor_id"),
            "executor_state": fields["executor_state"],
            "pool_address": fields.get("pool_address"),
            "trading_pair": fields.get("trading_pair"),
            "position_address": fields.get("position_address"),
            "position": fields,
            "distances": distances,
            "recommended_supervision_action": action,
            "reason": reason,
            "lifecycle_state_update": lifecycle_state_update,
            "session_state": evidence.redact(session_state),
            "session_stop_trigger": evidence.redact(session_trigger),
            "active_session_net_pnl_quote": active_session_pnl,
            "rebalance_cost_quote": rebalance_cost,
            "warnings": warnings,
            "agent_prompt_summary": summary,
        }
        suffix = (
            "\n\nLifecycle state update:\n```json\n"
            + json.dumps(lifecycle_state_update, indent=2, sort_keys=True, default=str)
            + "\n```"
        )
        if action == "write-audit":
            audit_input = {
                "controller_id": config.controller_id,
                "execution_mode": config.execution_mode,
                "executor_plan": lifecycle_state.get("executor_plan") or {},
                "preset": lifecycle_state.get("preset") or "",
                "final_executor": evidence.redact(executor),
                "close_reason": lifecycle_state.get("close_reason")
                or executor.get("close_type")
                or (
                    executor.get("custom_info", {}).get("close_type")
                    if isinstance(executor.get("custom_info"), dict)
                    else None
                )
                or "",
            }
            payload["audit_input"] = audit_input
            suffix += (
                "\n\nClose audit input:\n```json\n"
                + json.dumps(audit_input, indent=2, sort_keys=True, default=str)
                + "\n```"
            )
        return await _finish(payload, suffix)
    except Exception as exc:
        now = _utc_now().isoformat()
        payload = {
            "report_status": "failed-closed",
            "timestamp": now,
            "has_active_position": None,
            "executor_api_debug": executor_api_debug,
            "recommended_supervision_action": "manual-review",
            "reason": "routine_exception",
            "warnings": [f"unexpected report failure: {type(exc).__name__}: {exc}"],
            "input_config": input_config,
            "position": None,
            "distances": {},
            "agent_prompt_summary": "Manual review: LP position report failed closed.",
        }
        return await _finish(payload)


if __name__ == "__main__":
    import sys

    if "--self-check" in sys.argv:
        _self_check()
        print("self-check passed")
    else:
        print(asyncio.run(run(Config(execution_mode="dry_run"), None)))
