"""Read raw namespaced bot status while preserving controller lifecycle telemetry."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

CATEGORY = "Trend-Aware LP Bot Snapshot"
SCHEMA = "multi_lp_rebalancer_manager.bot_snapshot.v2"
NETWORK = "solana-mainnet-beta"
LP_PROVIDER = "orca/clmm"
SWAP_PROVIDER = "jupiter/router"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
_CONTROLLER_STATES = {
    "RECOVERING",
    "PREPARING",
    "OPENING",
    "ACTIVE",
    "CLOSING",
    "CLEANING",
    "WAITING_FOR_TREND_REFRESH",
    "EXITED",
    "FAULTED",
    "BLOCKED",
}
_RUN_STATES = {"running", "stopped", "idle", "error", "unknown"}
_TERMINAL_EXECUTOR_STATES = {
    "TERMINATED",
    "COMPLETED",
    "CLOSED",
    "FAILED",
    "CANCELED",
    "CANCELLED",
}
MAX_RESULT_CHARS = 1_899
CONTROLLER_FIELDS = (
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
LP_EXECUTOR_FIELDS = ("id", "status", "close_type", "position_address")
ORDER_EXECUTOR_FIELDS = ("id", "status", "close_type", "role")
TREND_FIELDS = (
    "market_trend",
    "observed_at",
    "signal_id",
    "used_signal_id",
    "breach_at",
    "cleanup_completed_at",
    "cooldown_until",
    "rearm_admissible",
)
FAILURE_FIELDS = (
    "consecutive_count",
    "last_reason",
    "retry_after",
    "fault_reason",
    "ownership_error",
    "orphan_position_addresses",
)
EXIT_FIELDS = ("requested", "reason", "completed")
INVENTORY_FIELDS = ("attributed_base", "attributed_quote")
PNL_FIELDS = (
    "global_quote",
    "ratio",
    "lifetime_seconds",
    "grace_remaining_seconds",
)


class Config(BaseModel):
    """Return one current namespaced snapshot from the raw bot status API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: StrictStr = Field(min_length=3, max_length=128)
    controller_type: Literal["generic"]
    controller_name: Literal["trend_aware_lp_rebalancer"]
    expected_bots: list[StrictStr] = Field(default_factory=list, max_length=3)
    archive_check_bots: list[StrictStr] = Field(default_factory=list, max_length=3)
    timeout_seconds: StrictInt = Field(default=15, ge=1, le=30)

    @model_validator(mode="after")
    def identities(self) -> "Config":
        if not re.fullmatch(
            r"[a-z0-9_-]+", self.namespace
        ) or self.namespace.startswith("-"):
            raise ValueError("namespace must be a lowercase Condor bot namespace")
        for field, names in (
            ("expected_bots", self.expected_bots),
            ("archive_check_bots", self.archive_check_bots),
        ):
            if len(names) != len(set(names)):
                raise ValueError(f"{field} must not contain duplicates")
            if any(_slot_number(self.namespace, name) is None for name in names):
                raise ValueError(
                    f"{field} must contain exact timestamped slot bot names"
                )
        if set(self.expected_bots) & set(self.archive_check_bots):
            raise ValueError("a bot cannot be both live-expected and archive-pending")
        return self


Config.model_rebuild(
    _types_namespace={
        "Literal": Literal,
        "StrictInt": StrictInt,
        "StrictStr": StrictStr,
    }
)


def _safe_error(error: BaseException) -> str:
    text = " ".join(str(error).split()) or type(error).__name__
    text = re.sub(
        r"(?i)\b(api[_-]?key|token|password|secret|authorization)=([^\s,&]+)",
        r"\1=***",
        text,
    )
    text = re.sub(r"(?i)(https?://[^?\s]+)\?[^\s]+", r"\1?[redacted]", text)
    return text[:180]


async def _get_client(context: Any) -> Any:
    from config_manager import get_client

    return await get_client(getattr(context, "_chat_id", 0) or 0, context=context)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, (float, Decimal)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def _controller_config(
    configs: list[dict[str, Any]], controller_id: str
) -> dict[str, Any] | None:
    matches = [
        item
        for item in configs
        if str(item.get("id") or "") == controller_id
        or str(item.get("_config_name") or "") == controller_id
    ]
    return matches[0] if len(matches) == 1 else None


def _positive_number(value: Any) -> bool:
    try:
        number = Decimal(str(value))
    except Exception:
        return False
    return number.is_finite() and number > 0


def _same_number(left: Any, right: Any) -> bool:
    try:
        first = Decimal(str(left))
        second = Decimal(str(right))
    except Exception:
        return False
    return first.is_finite() and second.is_finite() and first == second


def _slot_number(namespace: str, bot_name: str) -> int | None:
    match = re.fullmatch(
        rf"{re.escape(namespace)}-slot-([1-3])-\d{{8}}-\d{{6}}", bot_name
    )
    return int(match.group(1)) if match else None


def _slot_identity_matches(
    namespace: str, bot_name: str, controller_id: str
) -> tuple[int | None, bool]:
    slot = _slot_number(namespace, bot_name)
    return slot, slot is not None and controller_id == f"{namespace}-slot-{slot}"


def _identity_matches(
    controller_id: str,
    identity: dict[str, Any],
    current_config: dict[str, Any] | None,
) -> bool:
    if current_config is None:
        return False
    exact = {
        "controller_id": controller_id,
        "connector_name": current_config.get("connector_name"),
        "pool_address": current_config.get("pool_address"),
        "base_token_mint": current_config.get("base_token_mint"),
        "quote_token_mint": current_config.get("quote_token_mint"),
        "trading_pair": current_config.get("trading_pair"),
        "lp_provider": current_config.get("lp_provider"),
        "swap_provider": current_config.get("swap_provider"),
    }
    if any(identity.get(key) != value or value is None for key, value in exact.items()):
        return False
    return _same_number(
        identity.get("controller_started_at"),
        current_config.get("controller_started_at"),
    )


def _domain_matches(current_config: dict[str, Any] | None) -> bool:
    if current_config is None:
        return False
    return (
        current_config.get("controller_type") == "generic"
        and current_config.get("controller_name") == "trend_aware_lp_rebalancer"
        and current_config.get("connector_name") == NETWORK
        and current_config.get("lp_provider") == LP_PROVIDER
        and current_config.get("swap_provider") == SWAP_PROVIDER
        and current_config.get("quote_token_mint") == USDC_MINT
        and isinstance(current_config.get("pool_address"), str)
        and bool(current_config.get("pool_address"))
        and isinstance(current_config.get("base_token_mint"), str)
        and bool(current_config.get("base_token_mint"))
    )


def _finite_number(value: Any) -> bool:
    try:
        number = Decimal(str(value))
    except Exception:
        return False
    return number.is_finite()


def _policy_matches(
    custom: dict[str, Any], current_config: dict[str, Any] | None
) -> bool:
    if current_config is None:
        return False
    policy = custom.get("policy")
    trend = custom.get("trend")
    if not isinstance(policy, dict) or not isinstance(trend, dict):
        return False
    numeric_pairs = (
        ("position_width_pct", "position_width_pct"),
        ("downside_offset_pct", "downside_offset_pct"),
        ("rebalance_threshold_pct", "rebalance_threshold_pct"),
        ("trend_signal_max_age_seconds", "trend_signal_max_age_seconds"),
        ("defensive_rearm_cooldown_minutes", "defensive_rearm_cooldown_minutes"),
        ("take_profit_ratio", "controller_take_profit_ratio"),
        ("stop_loss_ratio", "controller_stop_loss_ratio"),
        ("time_limit_minutes", "controller_time_limit_minutes"),
        ("pnl_grace_period_minutes", "controller_pnl_grace_period_minutes"),
    )
    return (
        all(
            _same_number(policy.get(info_key), current_config.get(config_key))
            for info_key, config_key in numeric_pairs
        )
        and str(trend.get("market_trend") or "").upper()
        == str(current_config.get("market_trend") or "").upper()
        and _same_number(
            trend.get("observed_at"), current_config.get("trend_observed_at")
        )
        and trend.get("signal_id") == current_config.get("trend_signal_id")
    )


def _active_executor(data: Any) -> bool:
    return isinstance(data, dict) and str(data.get("status") or "").upper() not in (
        _TERMINAL_EXECUTOR_STATES | {""}
    )


def _structurally_complete(custom: dict[str, Any]) -> bool:
    required_dicts = (
        "identity",
        "policy",
        "trend",
        "failure",
        "exit",
        "inventory",
        "pnl",
    )
    if any(not isinstance(custom.get(key), dict) for key in required_dicts):
        return False
    failure = custom["failure"]
    exit_info = custom["exit"]
    inventory = custom["inventory"]
    pnl = custom["pnl"]
    trend = custom["trend"]
    return (
        type(failure.get("consecutive_count")) is int
        and failure["consecutive_count"] >= 0
        and type(exit_info.get("requested")) is bool
        and isinstance(exit_info.get("reason"), str)
        and type(exit_info.get("completed")) is bool
        and all(
            _finite_number(inventory.get(key))
            for key in ("assigned_quote", "attributed_base", "attributed_quote")
        )
        and all(_finite_number(pnl.get(key)) for key in PNL_FIELDS)
        and isinstance(trend.get("market_trend"), str)
        and isinstance(custom.get("readiness_state"), str)
        and (
            custom.get("lp_executor") is None
            or isinstance(custom.get("lp_executor"), dict)
        )
        and (
            custom.get("order_executor") is None
            or isinstance(custom.get("order_executor"), dict)
        )
    )


def _lifecycle_coherent(custom: dict[str, Any], lifecycle: str) -> bool:
    lp = custom.get("lp_executor")
    order = custom.get("order_executor")
    exit_info = custom.get("exit") if isinstance(custom.get("exit"), dict) else {}
    if lifecycle == "ACTIVE":
        return (
            _active_executor(lp)
            and bool(lp.get("id"))
            and bool(lp.get("position_address"))
        )
    if lifecycle == "EXITED":
        return (
            exit_info.get("requested") is True
            and exit_info.get("completed") is True
            and str(exit_info.get("reason") or "none") != "none"
            and not _active_executor(lp)
            and not _active_executor(order)
        )
    return True


def _brief_text(value: Any, limit: int = 120) -> Any:
    if not isinstance(value, str):
        return value
    text = " ".join(value.split())
    return text if len(text) <= limit else f"{text[: limit - 3]}..."


def _group(data: Any, fields: tuple[str, ...]) -> list[Any] | None:
    if not isinstance(data, dict):
        return None
    values = []
    for field in fields:
        value = data.get(field)
        if field in {"last_reason", "fault_reason", "ownership_error"}:
            value = _brief_text(value)
        elif field == "orphan_position_addresses" and isinstance(value, list):
            value = value[:3]
        values.append(_json_value(value))
    return values


def _compact(
    namespace: str,
    bot_name: str,
    bot_data: dict[str, Any],
    configs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    performance = bot_data.get("performance")
    if not isinstance(performance, dict):
        return []
    rows: list[dict[str, Any]] = []
    bot_status = str(bot_data.get("status") or "").lower()
    bot_live = bot_status == "running"
    for controller_id, raw in performance.items():
        raw = raw if isinstance(raw, dict) else {}
        custom = (
            raw.get("custom_info") if isinstance(raw.get("custom_info"), dict) else {}
        )
        metrics = (
            raw.get("performance") if isinstance(raw.get("performance"), dict) else {}
        )
        controller_id = str(controller_id)
        slot, slot_matches = _slot_identity_matches(namespace, bot_name, controller_id)
        current_config = _controller_config(configs, controller_id)
        kill_switch = (
            current_config.get("manual_kill_switch") if current_config else None
        )
        run_state = (
            "stopped"
            if kill_switch is True
            else (
                "error"
                if raw.get("status") == "error" or bot_status == "error"
                else (
                    "idle"
                    if bot_status == "idle"
                    else "running" if kill_switch is False and bot_live else "unknown"
                )
            )
        )
        lifecycle = str(custom.get("lifecycle_state") or "").upper()
        valid_lifecycle = lifecycle in _CONTROLLER_STATES
        custom_identity = (
            custom.get("identity") if isinstance(custom.get("identity"), dict) else {}
        )
        inventory = (
            custom.get("inventory") if isinstance(custom.get("inventory"), dict) else {}
        )
        identity_matches = _identity_matches(
            controller_id, custom_identity, current_config
        )
        domain_matches = _domain_matches(current_config)
        policy_matches = _policy_matches(custom, current_config)
        lifecycle_coherent = _lifecycle_coherent(custom, lifecycle)
        structural_complete = _structurally_complete(custom)
        assigned_quote_matches = _positive_number(
            inventory.get("assigned_quote")
        ) and _same_number(
            inventory.get("assigned_quote"),
            (current_config or {}).get("total_amount_quote"),
        )
        telemetry_complete = (
            type(custom.get("schema_version")) is int
            and custom.get("schema_version") == 1
            and valid_lifecycle
            and identity_matches
            and domain_matches
            and slot_matches
            and policy_matches
            and assigned_quote_matches
            and structural_complete
            and lifecycle_coherent
            and bot_live
        )
        identity = {
            "controller_type": (current_config or {}).get("controller_type"),
            "controller_name": (current_config or {}).get("controller_name"),
            **custom_identity,
        }
        rows.append(
            {
                "bot_name": bot_name,
                "controller_id": controller_id,
                "slot": slot,
                "run_state": run_state if run_state in _RUN_STATES else "unknown",
                "lifecycle_state": lifecycle if valid_lifecycle else "UNKNOWN",
                "schema_version": custom.get("schema_version"),
                "identity": _json_value(identity),
                "readiness_state": custom.get("readiness_state"),
                "policy": _json_value(custom.get("policy")),
                "lp_executor": _json_value(custom.get("lp_executor")),
                "order_executor": _json_value(custom.get("order_executor")),
                "trend": _json_value(custom.get("trend")),
                "failure": _json_value(custom.get("failure")),
                "exit": _json_value(custom.get("exit")),
                "inventory": _json_value(inventory),
                "pnl": _json_value(custom.get("pnl")),
                "performance": _json_value(metrics),
                "telemetry_complete": telemetry_complete,
                "identity_matches_config": identity_matches,
                "domain_matches_strategy": domain_matches,
                "assigned_quote_matches_config": assigned_quote_matches,
                "config_available": current_config is not None,
                "policy_matches_config": policy_matches,
                "lifecycle_coherent": lifecycle_coherent,
            }
        )
    return rows


def _transport_row(row: dict[str, Any]) -> list[Any]:
    identity = row.get("identity") or {}
    inventory = row.get("inventory") or {}
    failure = row.get("failure") or {}
    lifecycle = row.get("lifecycle_state")
    include_failure = any(
        failure.get(field)
        for field in (
            "consecutive_count",
            "last_reason",
            "retry_after",
            "fault_reason",
            "ownership_error",
            "orphan_position_addresses",
        )
    )
    trend = row.get("trend") or {}
    if lifecycle not in {"WAITING_FOR_TREND_REFRESH", "BLOCKED"}:
        trend = {
            "market_trend": trend.get("market_trend"),
            "observed_at": trend.get("observed_at"),
            "signal_id": trend.get("signal_id"),
        }
    order = row.get("order_executor")
    if (
        lifecycle == "ACTIVE"
        and isinstance(order, dict)
        and str(order.get("status") or "").upper() in _TERMINAL_EXECUTOR_STATES
    ):
        order = None
    values = {
        "bot_name": row.get("bot_name"),
        "controller_id": row.get("controller_id"),
        "slot": row.get("slot"),
        "run_state": row.get("run_state"),
        "lifecycle_state": lifecycle,
        "readiness_state": row.get("readiness_state"),
        "pool_address": identity.get("pool_address"),
        "base_token_mint": identity.get("base_token_mint"),
        "assigned_quote": inventory.get("assigned_quote"),
        "schema_version": row.get("schema_version"),
        "telemetry_complete": row.get("telemetry_complete"),
        "identity_matches_config": row.get("identity_matches_config"),
        "domain_matches_strategy": row.get("domain_matches_strategy"),
        "assigned_quote_matches_config": row.get("assigned_quote_matches_config"),
        "config_available": row.get("config_available"),
        "policy_matches_config": row.get("policy_matches_config"),
        "lifecycle_coherent": row.get("lifecycle_coherent"),
        "lp_executor": _group(row.get("lp_executor"), LP_EXECUTOR_FIELDS),
        "order_executor": _group(order, ORDER_EXECUTOR_FIELDS),
        "trend": _group(trend, TREND_FIELDS),
        "failure": _group(failure, FAILURE_FIELDS) if include_failure else None,
        "exit": _group(row.get("exit"), EXIT_FIELDS),
        "inventory": _group(inventory, INVENTORY_FIELDS),
        "pnl": _group(row.get("pnl"), PNL_FIELDS),
    }
    return [_json_value(values[field]) for field in CONTROLLER_FIELDS]


def _bounded(payload: dict[str, Any], full_rows: list[dict[str, Any]]) -> str:
    payload["format"] = "compact_rows_v1"
    payload["controllers"] = [_transport_row(row) for row in full_rows]
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    if len(encoded) <= MAX_RESULT_CHARS:
        return encoded
    summaries = [
        [
            row.get("bot_name"),
            row.get("controller_id"),
            row.get("slot"),
            row.get("lifecycle_state"),
            (row.get("identity") or {}).get("pool_address"),
            (row.get("identity") or {}).get("base_token_mint"),
            row.get("telemetry_complete"),
            row.get("lifecycle_coherent"),
        ]
        for row in full_rows
    ]
    fallback = {
        "schema": SCHEMA,
        "status": "degraded",
        "reason": "response_limit",
        "format": "summary_rows_v1",
        "summary_fields": [
            "bot_name",
            "controller_id",
            "slot",
            "lifecycle_state",
            "pool_address",
            "base_token_mint",
            "telemetry_complete",
            "lifecycle_coherent",
        ],
        "controllers": summaries,
        "mutation": False,
    }
    for key in (
        "archive_confirmed",
        "archive_pending",
        "archive_errors",
        "invalid_slot_bots",
    ):
        if key in payload:
            fallback[key] = payload[key]
    encoded = json.dumps(fallback, separators=(",", ":"), ensure_ascii=True)
    if len(encoded) <= MAX_RESULT_CHARS:
        return encoded
    return json.dumps(
        {
            "schema": SCHEMA,
            "status": "unavailable",
            "reason": "response_limit",
            "mutation": False,
        },
        separators=(",", ":"),
    )


async def run(config: Config, context: Any) -> str:
    """Normalize exact raw bot lifecycle evidence; never mutate bots or configs."""

    try:
        client = await asyncio.wait_for(
            _get_client(context), timeout=config.timeout_seconds
        )
        raw = await asyncio.wait_for(
            client.bot_orchestration.get_active_bots_status(),
            timeout=config.timeout_seconds,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return json.dumps(
            {
                "schema": SCHEMA,
                "status": "unavailable",
                "reason": _safe_error(exc),
                "mutation": False,
            },
            separators=(",", ":"),
        )
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        return json.dumps(
            {
                "schema": SCHEMA,
                "status": "unavailable",
                "reason": "raw bot status has no data map",
                "mutation": False,
            },
            separators=(",", ":"),
        )

    owned_names = sorted(
        name
        for name in data
        if isinstance(name, str)
        and (name == config.namespace or name.startswith(f"{config.namespace}-"))
    )
    configs_per_bot = await asyncio.gather(
        *(
            asyncio.wait_for(
                client.controllers.get_bot_controller_configs(name),
                timeout=config.timeout_seconds,
            )
            for name in owned_names
        ),
        return_exceptions=True,
    )
    controllers: list[dict[str, Any]] = []
    config_errors: dict[str, str] = {}
    config_counts: dict[str, int] = {}
    for bot_name, configs in zip(owned_names, configs_per_bot, strict=True):
        if isinstance(configs, BaseException) or not isinstance(configs, list):
            config_errors[bot_name] = _safe_error(
                configs
                if isinstance(configs, BaseException)
                else ValueError("invalid config list")
            )
            configs = []
        config_counts[bot_name] = len(configs)
        bot_data = data.get(bot_name)
        if isinstance(bot_data, dict):
            controllers.extend(_compact(config.namespace, bot_name, bot_data, configs))

    archive_results = await asyncio.gather(
        *(
            asyncio.wait_for(
                client.bot_orchestration.get_bot_runs(
                    bot_name=name,
                    deployment_status="ARCHIVED",
                    limit=1,
                ),
                timeout=config.timeout_seconds,
            )
            for name in config.archive_check_bots
        ),
        return_exceptions=True,
    )
    archive_confirmed: list[str] = []
    archive_pending: list[str] = []
    archive_errors: dict[str, str] = {}
    for name, result in zip(config.archive_check_bots, archive_results, strict=True):
        if isinstance(result, BaseException):
            archive_errors[name] = _safe_error(result)
            continue
        if not isinstance(result, dict) or result.get("status") != "success":
            archive_errors[name] = "archive status response unavailable"
            continue
        runs = result.get("data")
        if not isinstance(runs, list):
            archive_errors[name] = "archive status response has no data list"
            continue
        if any(
            isinstance(run, dict)
            and run.get("bot_name") == name
            and run.get("deployment_status") == "ARCHIVED"
            for run in runs
        ):
            archive_confirmed.append(name)
        else:
            archive_pending.append(name)

    unexpected = (
        sorted(set(owned_names) - set(config.expected_bots))
        if config.expected_bots
        else []
    )
    missing = sorted(set(config.expected_bots) - set(owned_names))
    invalid = [
        row
        for row in controllers
        if row["identity"].get("controller_type") != config.controller_type
        or row["identity"].get("controller_name") != config.controller_name
        or not row["telemetry_complete"]
    ]
    controller_counts: dict[str, int] = {
        bot_name: sum(row["bot_name"] == bot_name for row in controllers)
        for bot_name in owned_names
    }
    invalid_topology = sorted(
        bot_name
        for bot_name in owned_names
        if controller_counts.get(bot_name, 0) != 1
        or config_counts.get(bot_name, 0) != 1
    )
    namespace_capacity_exceeded = len(owned_names) > 3
    invalid_slot_bots = sorted(
        {name for name in owned_names if _slot_number(config.namespace, name) is None}
        | {
            row["bot_name"]
            for row in controllers
            if row["slot"] is None
            or row["controller_id"] != f"{config.namespace}-slot-{row['slot']}"
        }
    )
    status = (
        "complete"
        if not config_errors
        and not missing
        and not unexpected
        and not invalid
        and not invalid_topology
        and not namespace_capacity_exceeded
        and not invalid_slot_bots
        and not archive_pending
        and not archive_errors
        else "degraded"
    )
    payload = {
        "schema": SCHEMA,
        "status": status,
        "observed_at": round(time.time(), 3),
        "namespace": config.namespace,
        "owned_bot_count": len(owned_names),
        "mutation": False,
    }
    if missing:
        payload["expected_missing"] = missing
    if unexpected:
        payload["unexpected_namespaced"] = unexpected
    if config_errors:
        payload["config_errors"] = config_errors
    if any(count != 1 for count in config_counts.values()):
        payload["controller_config_counts"] = config_counts
    if invalid_topology:
        payload["invalid_topology_bots"] = invalid_topology
    if namespace_capacity_exceeded:
        payload["namespace_capacity_exceeded"] = True
    if invalid_slot_bots:
        payload["invalid_slot_bots"] = invalid_slot_bots
    if invalid:
        payload["invalid_controller_count"] = len(invalid)
    if config.archive_check_bots:
        payload["archive_confirmed"] = archive_confirmed
    if archive_pending:
        payload["archive_pending"] = archive_pending
    if archive_errors:
        payload["archive_errors"] = archive_errors
    return _bounded(payload, controllers)
