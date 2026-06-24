import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class Config(BaseModel):
    """Fetch an LP executor from the API, summarize state, and recommend supervision action."""

    model_config = ConfigDict(extra="forbid")

    controller_id: str = Field(
        default="orca_lp_agent",
        description="Executor controller id used when executor_id is not supplied",
    )
    executor_id: str | None = Field(
        default=None,
        description="Executor id or unique id prefix to fetch executor details",
    )
    now_timestamp: Any = Field(default=None, description="Current timestamp override")

    max_position_age_minutes: float = Field(
        default=480, ge=0, description="Time-limit exit threshold"
    )
    take_profit_pct_after_costs: float | None = Field(
        default=0.005,
        description="Close when net_pnl_pct reaches this ratio after costs",
    )
    stop_loss_pct_after_costs: float | None = Field(
        default=0.01,
        description="Close when net_pnl_pct reaches this negative ratio after costs",
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
        if value is None:
            return "orca_lp_agent"
        text = str(value).strip()
        return text or "orca_lp_agent"

    @field_validator("executor_id", mode="before")
    @classmethod
    def _coerce_executor_id(cls, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


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


def _executor_id(executor: dict[str, Any]) -> str | None:
    config = executor.get("config") if isinstance(executor.get("config"), dict) else {}
    return executor.get("executor_id") or executor.get("id") or config.get("id")


def _looks_like_executor(value: dict[str, Any]) -> bool:
    if not isinstance(value, dict):
        return False
    config = value.get("config") if isinstance(value.get("config"), dict) else {}
    return bool(
        value.get("executor_id")
        or value.get("id")
        or value.get("status")
        or value.get("trading_pair")
        or config.get("id")
        or config.get("trading_pair")
    )


def _as_executor_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    for key in ("executor", "data", "result", "item"):
        nested = value.get(key)
        if isinstance(nested, dict) and _looks_like_executor(nested):
            return nested
    return value if _looks_like_executor(value) else {}


def _as_executor_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [
            item
            for item in value
            if isinstance(item, dict) and _looks_like_executor(item)
        ]
    if isinstance(value, dict):
        for key in ("executors", "data", "results", "items"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [
                    item
                    for item in nested
                    if isinstance(item, dict) and _looks_like_executor(item)
                ]
        executor = _as_executor_dict(value)
        return [executor] if executor else []
    return []


def _executor_matches_id(executor: dict[str, Any], executor_id: str) -> bool:
    actual = _executor_id(executor)
    if actual is None:
        return False
    actual_text = str(actual).strip()
    wanted = executor_id.strip()
    return actual_text == wanted or actual_text.startswith(wanted)


def _is_active_executor(executor: dict[str, Any], controller_id: str) -> bool:
    if not isinstance(executor, dict):
        return False
    owner = executor.get("controller_id")
    if owner and str(owner) != controller_id:
        return False
    if executor.get("is_active") is False:
        return False
    state = _as_state(executor.get("status"))
    return state not in INACTIVE_STATES


async def _fetch_executor_from_api(
    config: Config, context: Any
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    debug: dict[str, Any] = {
        "controller_id": config.controller_id,
        "executor_id": config.executor_id,
        "lookup_mode": "executor_id" if config.executor_id else "controller_id",
        "requests": [],
    }
    warnings: list[str] = []

    try:
        from config_manager import get_client
    except Exception as exc:
        debug["error"] = f"executor lookup unavailable: {type(exc).__name__}: {exc}"
        return None, [debug["error"]], debug

    client = await get_client(_context_chat_id(context), context=context)
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
        client, config.controller_id, debug
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
        request_debug["response"] = _json_safe(raw_response)
        executor = _as_executor_dict(raw_response)
        if executor:
            request_debug["selected"] = True
            debug["selected_source"] = "get_executor"
            debug["requests"].append(request_debug)
            return executor, warnings
        request_debug["selected"] = False
        warnings.append(f"executor_id '{executor_id}' lookup returned no executor")
    except Exception as exc:
        error = f"executor_id '{executor_id}' direct lookup failed: {type(exc).__name__}: {exc}"
        request_debug["error"] = error
        warnings.append(error)
    debug["requests"].append(request_debug)

    for scope, params in (
        (
            "controller_executor_id_fallback",
            {"controller_ids": [controller_id], "limit": 100},
        ),
        ("unfiltered_executor_id_fallback", {"limit": 100}),
    ):
        request_debug = {
            "method": "search_executors",
            "scope": scope,
            "params": _json_safe(params),
        }
        try:
            raw_response = await client.executors.search_executors(**params)
            request_debug["response"] = _json_safe(raw_response)
            executors = _as_executor_list(raw_response)
            matches = [
                executor
                for executor in executors
                if _executor_matches_id(executor, executor_id)
            ]
            request_debug["candidate_count"] = len(executors)
            request_debug["match_count"] = len(matches)
            if matches:
                if len(matches) > 1:
                    warning = f"executor_id prefix '{executor_id}' matched {len(matches)} executors in {scope}; using first match"
                    warnings.append(warning)
                    request_debug["selected_warning"] = warning
                request_debug["selected"] = True
                debug["selected_source"] = scope
                debug["requests"].append(request_debug)
                return matches[0], warnings
            request_debug["selected"] = False
            warnings.append(f"executor_id '{executor_id}' was not found in {scope}")
        except Exception as exc:
            error = f"executor_id '{executor_id}' {scope} failed: {type(exc).__name__}: {exc}"
            request_debug["error"] = error
            warnings.append(error)
        debug["requests"].append(request_debug)

    return None, warnings


async def _fetch_active_executor_by_controller(
    client: Any, controller_id: str, debug: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    params = {"controller_ids": [controller_id], "limit": 100}
    request_debug: dict[str, Any] = {
        "method": "search_executors",
        "params": _json_safe(params),
    }
    try:
        raw_response = await client.executors.search_executors(**params)
        request_debug["response"] = _json_safe(raw_response)
        executors = _as_executor_list(raw_response)
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
                warning = f"controller_id '{controller_id}' has {len(active_executors)} active executors; using first match"
                warnings.append(warning)
                request_debug["selected_warning"] = warning
            request_debug["selected"] = True
            debug["selected_source"] = "search_executors"
            debug["requests"].append(request_debug)
            return active_executors[0], warnings
        request_debug["selected"] = False
    except Exception as exc:
        error = f"active executor lookup failed for controller_id '{controller_id}': {type(exc).__name__}: {exc}"
        request_debug["error"] = error
        warnings.append(error)
    debug["requests"].append(request_debug)
    return None, warnings or [
        f"no active executor found for controller_id '{controller_id}'"
    ]


def _extract_api_executor(executor: dict[str, Any]) -> dict[str, Any]:
    config = executor.get("config") if isinstance(executor.get("config"), dict) else {}
    custom_info = (
        executor.get("custom_info")
        if isinstance(executor.get("custom_info"), dict)
        else {}
    )
    out_of_range_seconds = _to_float(custom_info.get("out_of_range_seconds"))
    return {
        "executor_id": _executor_id(executor),
        "executor_state": _as_state(executor.get("status")),
        "lp_state": _as_state(custom_info.get("state")),
        "pool_address": config.get("pool_address"),
        "trading_pair": executor.get("trading_pair") or config.get("trading_pair"),
        "position_address": custom_info.get("position_address"),
        "current_price": _to_float(custom_info.get("current_price")),
        "lower_price": _to_float(config.get("lower_price")),
        "upper_price": _to_float(config.get("upper_price")),
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
        "estimated_net_pnl_quote": _to_float(executor.get("net_pnl_quote")),
        "estimated_net_pnl_pct": _to_float(executor.get("net_pnl_pct")),
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
            "manual-review",
            "executor_failed",
            ["executor state is FAILED; stop new opens"],
        )
    if state in COMPLETE_STATES:
        return "write-audit", "executor_complete", warnings

    active_count = fields.get("active_executor_count")
    if isinstance(active_count, (int, float)) and active_count > 1:
        warnings.append(
            f"controller has {int(active_count)} active executors; V1 expects one"
        )
        return "manual-review", "multiple_active_executors", warnings

    if not fields.get("position_address"):
        warnings.append("position address missing from executor API response")
        missing_ticks = max(1, config.missing_position_ticks)
        if missing_ticks >= config.missing_position_grace_ticks:
            return "manual-review", "missing_position_grace_exceeded", warnings
        return "continue", "missing_position_within_grace", warnings

    price = fields.get("current_price")
    lower = fields.get("lower_price")
    upper = fields.get("upper_price")
    lower_limit = fields.get("lower_limit_price")
    upper_limit = fields.get("upper_limit_price")
    if price is not None and lower_limit is not None and price <= lower_limit:
        return "close", "lower_limit_crossed", warnings
    if price is not None and upper_limit is not None and price >= upper_limit:
        return "close", "upper_limit_crossed", warnings

    pnl_pct = fields.get("estimated_net_pnl_pct")
    if config.take_profit_pct_after_costs is not None and pnl_pct is not None:
        if pnl_pct >= config.take_profit_pct_after_costs:
            return "close", "take_profit_reached", warnings
    if config.stop_loss_pct_after_costs is not None and pnl_pct is not None:
        if pnl_pct <= -abs(config.stop_loss_pct_after_costs):
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
            return "continue", "out_of_range_duration_unknown", warnings
        if out_of_range_minutes >= config.soft_out_of_range_grace_minutes:
            return "close", "soft_out_of_range_grace_exceeded", warnings
        return "continue", "out_of_range_within_grace", warnings

    age_minutes = _age_minutes(fields, now)
    if age_minutes is not None and age_minutes >= config.max_position_age_minutes:
        return "close", "time_limit_reached", warnings

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
            "opened_at": now,
            "time_out_of_range_minutes": 0.0,
        }
        data.update(overrides)
        return data

    assert _recommend(fields(estimated_net_pnl_pct=0.005), Config(), now)[:2] == (
        "close",
        "take_profit_reached",
    )
    assert _recommend(fields(estimated_net_pnl_pct=-0.01), Config(), now)[:2] == (
        "close",
        "stop_loss_reached",
    )
    assert _recommend(fields(opened_at=now - timedelta(minutes=481)), Config(), now)[
        :2
    ] == ("close", "time_limit_reached")
    assert _recommend(fields(position_address=None), Config(), now)[:2] == (
        "continue",
        "missing_position_within_grace",
    )
    assert _recommend(
        fields(position_address=None), Config(missing_position_ticks=2), now
    )[:2] == ("manual-review", "missing_position_grace_exceeded")
    assert _recommend(
        fields(lp_state="OUT_OF_RANGE", time_out_of_range_minutes=11), Config(), now
    )[:2] == ("close", "soft_out_of_range_grace_exceeded")
    assert _recommend(fields(active_executor_count=2), Config(), now)[:2] == (
        "manual-review",
        "multiple_active_executors",
    )


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
        "estimated_net_pnl_pct",
        "position_age_minutes",
        "time_out_of_range_minutes",
    ]
    rows = []
    for key in keys:
        value = position.get(key)
        if value is None:
            continue
        if key.endswith("_pct"):
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
        builder.kpi(
            "Active Position", "yes" if payload.get("has_active_position") else "no"
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
        if warnings:
            builder.markdown(
                "## Warnings\n" + "\n".join(f"- {warning}" for warning in warnings)
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
        builder.markdown(
            "## Debug JSON Payload\n"
            "```json\n"
            f"{json.dumps(payload, indent=2, sort_keys=True, default=str)}\n"
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
        await builder.save()
    except Exception:
        return


async def run(config: Config, context: Any) -> str:
    input_config = _json_safe(config.model_dump())
    try:
        now = _to_datetime(config.now_timestamp) or _utc_now()
        executor, lookup_warnings, executor_api_debug = await _fetch_executor_from_api(
            config, context
        )

        if not executor:
            payload = {
                "report_status": "success",
                "timestamp": now.isoformat(),
                "has_active_position": False,
                "recommended_supervision_action": "no-active-position",
                "reason": "no executor found in API",
                "warnings": lookup_warnings,
                "input_config": input_config,
                "executor_api_debug": executor_api_debug,
                "position": None,
                "distances": {},
                "agent_prompt_summary": "No active Orca LP executor was found in the executor API; agent may run pool scan if risk limits allow.",
            }
            await _save_position_report(payload)
            return _format_position_text(payload)

        executor_api_debug["executor_source_used"] = "api"
        fields = _extract_api_executor(executor)
        fields["active_executor_count"] = executor_api_debug.get(
            "active_executor_count"
        )
        age_minutes = _age_minutes(fields, now)
        if age_minutes is not None:
            fields["position_age_minutes"] = round(age_minutes, 4)
        distances = _distances(fields)
        action, reason, warnings = _recommend(fields, config, now)
        warnings = lookup_warnings + warnings
        has_active = (
            action not in {"no-active-position", "write-audit"}
            and fields["executor_state"] not in COMPLETE_STATES | FAILED_STATES
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
            "warnings": warnings,
            "agent_prompt_summary": summary,
        }
        await _save_position_report(payload)
        return _format_position_text(payload)
    except Exception as exc:
        now = _utc_now().isoformat()
        payload = {
            "report_status": "failed-closed",
            "timestamp": now,
            "has_active_position": False,
            "recommended_supervision_action": "manual-review",
            "reason": "routine_exception",
            "warnings": [f"unexpected report failure: {type(exc).__name__}: {exc}"],
            "input_config": input_config,
            "position": None,
            "distances": {},
            "agent_prompt_summary": "Manual review: LP position report failed closed.",
        }
        await _save_position_report(payload)
        return _format_position_text(payload)


if __name__ == "__main__":
    import asyncio
    import sys

    if "--self-check" in sys.argv:
        _self_check()
        print("self-check passed")
    else:
        print(asyncio.run(run(Config(), None)))
