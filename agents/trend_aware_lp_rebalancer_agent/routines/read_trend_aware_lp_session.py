"""Read exact HAPI evidence for one trend-aware LP controller session.

This routine is deliberately a read-only normalizer.  It preserves raw identity,
configuration, schema-3 telemetry, and bot-run records; the Strategy, not this
module, derives lifecycle state and chooses any action.
"""

import asyncio
import copy
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from condor.reports import ReportBuilder
from routines.base import RoutineResult

CATEGORY = "Trend-Aware LP Session"
MAX_AGENT_RESULT_BYTES = 1_000_000

_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,254}$")
_RUNTIME_SUFFIX = re.compile(r"^\d{8}-\d{6}$")
_CONTROLLER_LIFECYCLE_STATES = {"RUNNING", "EXITING", "EXITED", "FAULTED"}
_POSITION_LIFECYCLE_STATES = {
    "PENDING",
    "PREPARING",
    "OPENING",
    "ACTIVE",
    "CLOSING",
    "CLEANING",
    "COOLDOWN",
    "RECOVERING",
    "BLOCKED",
    "EXITED",
    "FAULTED",
}
_RUNNING_POSITION_STATES = {
    "PENDING",
    "PREPARING",
    "OPENING",
    "ACTIVE",
    "CLOSING",
    "CLEANING",
    "COOLDOWN",
    "RECOVERING",
    "BLOCKED",
}
_OWNERSHIP_STATES = {"PRESENT", "ABSENT", "UNKNOWN", "CONFLICT"}
_EXIT_REASONS = {
    "none",
    "take_profit",
    "stop_loss",
    "time_limit",
    "operator",
    "fault",
}
_FORMATION_FIELDS = {
    "market_trend",
    "position_width_pct",
    "downside_offset_pct",
    "rebalance_threshold_pct",
}
_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "node_url",
    "password",
    "private_key",
    "proxy_authorization",
    "rpc_url",
    "secret",
    "set_cookie",
    "token_url",
}
_WALLET_KEYS = {
    "default_address",
    "default_wallet",
    "solana_default_address",
    "wallet",
    "wallet_address",
    "wallet_addresses",
    "wallets",
}
_SECRET_SUFFIXES = (
    "_api_key",
    "_api_token",
    "_auth_token",
    "_access_token",
    "_private_key",
    "_rpc_token",
    "_credential",
    "_credentials",
    "_password",
    "_secret",
)
_WALLET_SUFFIXES = ("_wallet", "_wallet_address", "_wallet_id", "_default_address")
_SENSITIVE_QUERY_VALUE = re.compile(
    r"(?i)([?&](?:access[_-]?token|api[_-]?(?:key|token)|auth[_-]?token|"
    r"authorization|credential|jwt|password|private[_-]?key|rpc[_-]?token|"
    r"secret|seed|token|wallet(?:[_-]?(?:address|id))?)=)[^&#\s]*"
)
_SENSITIVE_INLINE_VALUE = re.compile(
    r"(?i)\b(access[_-]?token|api[_-]?(?:key|token)|auth[_-]?token|"
    r"authorization|credential|jwt|password|private[_-]?key|rpc[_-]?token|"
    r"secret|seed|token|(?:default|owner|solana[_-]?default)?[_-]?wallet"
    r"(?:[_-]?(?:address|id))?)\b(\s*[:=]\s*)"
    r"(?:\[[^\]]*\]|\{[^}]*\}|\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_SENSITIVE_HEADER_VALUE = re.compile(
    r"(?i)\b(authorization|proxy[-_]?authorization|cookie|set[-_]?cookie|"
    r"x[-_]?api[-_]?key|x[-_]?auth[-_]?token)\b(\s*[:=]\s*)[^\r\n]+"
)
_BEARER_TOKEN = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+")


class Config(BaseModel):
    """Strict identity tuple for one current controller session read."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: StrictStr = Field(min_length=1, max_length=255)
    account_name: StrictStr = Field(min_length=1, max_length=255)
    expected_generation: StrictStr | None = Field(
        default=None, min_length=1, max_length=255
    )
    expected_config_name: StrictStr | None = Field(
        default=None, min_length=1, max_length=255
    )
    expected_runtime_instance: StrictStr | None = Field(
        default=None, min_length=1, max_length=255
    )
    include_archive_record: StrictBool = False
    timeout_seconds: StrictInt = Field(default=15, ge=1, le=30)

    @field_validator(
        "namespace",
        "account_name",
        "expected_generation",
        "expected_config_name",
        "expected_runtime_instance",
    )
    @classmethod
    def validate_identity(cls, value: str | None) -> str | None:
        if value is not None and not _IDENTITY.fullmatch(value):
            raise ValueError(
                "identities may contain only letters, numbers, '.', '_', and '-'"
            )
        return value

    @model_validator(mode="after")
    def validate_identity_tuple(self):
        generation_set = self.expected_generation is not None
        config_set = self.expected_config_name is not None
        if generation_set != config_set:
            raise ValueError(
                "expected_generation and expected_config_name must both be set or both be null"
            )
        if generation_set and self.expected_generation != self.expected_config_name:
            raise ValueError(
                "expected_generation and expected_config_name must be identical"
            )
        if self.expected_runtime_instance is not None and not generation_set:
            raise ValueError(
                "expected_runtime_instance requires expected_generation and expected_config_name"
            )
        if self.expected_runtime_instance is not None:
            if not _valid_runtime_name(self.expected_runtime_instance, self.namespace):
                raise ValueError(
                    "expected_runtime_instance must be namespace-YYYYMMDD-HHMMSS"
                )
        if self.include_archive_record and self.expected_runtime_instance is None:
            raise ValueError(
                "include_archive_record requires expected_runtime_instance"
            )
        return self


def _in_namespace(name: str, namespace: str) -> bool:
    return name == namespace or name.startswith(f"{namespace}-")


def _valid_runtime_name(name: str, namespace: str) -> bool:
    prefix = f"{namespace}-"
    if not name.startswith(prefix):
        return False
    suffix = name[len(prefix) :]
    if not _RUNTIME_SUFFIX.fullmatch(suffix):
        return False
    try:
        datetime.strptime(suffix, "%Y%m%d-%H%M%S")
    except ValueError:
        return False
    return True


def _first_string(mapping: Any, names: tuple[str, ...]) -> str | None:
    if not isinstance(mapping, dict):
        return None
    for name in names:
        value = mapping.get(name)
        if isinstance(value, str) and value:
            return value
    return None


def _performance(bot: Any) -> dict[str, Any]:
    value = bot.get("performance") if isinstance(bot, dict) else None
    return value if isinstance(value, dict) else {}


def _controller_custom_info(bot: dict[str, Any], generation: str | None) -> Any:
    if generation is None:
        return None
    controller = _performance(bot).get(generation)
    return controller.get("custom_info") if isinstance(controller, dict) else None


def _active_row(
    runtime_instance: str,
    bot: dict[str, Any],
    namespace: str,
    generation: str | None,
) -> dict[str, Any]:
    custom_info = _controller_custom_info(bot, generation)
    reported_at = (
        custom_info.get("reported_at") if isinstance(custom_info, dict) else None
    )
    return {
        "runtime_instance": runtime_instance,
        "namespace": namespace,
        "account_name": _first_string(
            bot, ("account_name", "credentials_profile", "account")
        ),
        "wallet_address": _first_string(bot, ("wallet_address", "wallet")),
        "deployment_status": bot.get("deployment_status"),
        "raw_status": bot.get("status"),
        "recently_active": bot.get("recently_active"),
        "reported_at": reported_at,
        "controller_ids": list(_performance(bot).keys()),
    }


def _response_data(response: Any, source: str) -> Any:
    if not isinstance(response, dict) or "data" not in response:
        raise ValueError(f"{source} response must be an object containing 'data'")
    return response["data"]


def _solana_wallet_observation(wallets: Any) -> dict[str, Any]:
    if not isinstance(wallets, list) or any(
        not isinstance(row, dict) for row in wallets
    ):
        raise ValueError("gateway wallet response must be a list of objects")
    solana = [copy.deepcopy(row) for row in wallets if row.get("chain") == "solana"]
    defaults = [
        row.get("default_address")
        for row in solana
        if isinstance(row.get("default_address"), str) and row.get("default_address")
    ]
    return {
        "solana_default_address": defaults[0] if len(defaults) == 1 else None,
        "solana_wallets": solana,
    }


def _normalized_key(key: str) -> str:
    with_word_boundaries = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    return re.sub(r"[^a-z0-9]+", "_", with_word_boundaries.lower()).strip("_")


def _sensitive_report_key(key: str) -> bool:
    normalized = _normalized_key(key)
    return (
        normalized in _SECRET_KEYS
        or normalized in _WALLET_KEYS
        or normalized == "headers"
        or normalized.endswith(_SECRET_SUFFIXES)
        or normalized.endswith(_WALLET_SUFFIXES)
    )


def _sanitize_report_string(value: str) -> str:
    value = _PRIVATE_KEY_BLOCK.sub("[redacted]", value)
    value = _SENSITIVE_QUERY_VALUE.sub(r"\1[redacted]", value)
    value = _SENSITIVE_HEADER_VALUE.sub(r"\1\2[redacted]", value)
    value = _BEARER_TOKEN.sub(r"\1[redacted]", value)
    return _SENSITIVE_INLINE_VALUE.sub(r"\1\2[redacted]", value)


def _redact_report_value(value: Any, key: str = "") -> Any:
    """Redact only report copies; canonical routine evidence stays unchanged."""

    if _sensitive_report_key(key):
        return "[redacted]"
    if isinstance(value, dict):
        field = value.get("field")
        wallet_conflict = isinstance(field, str) and "wallet" in _normalized_key(field)
        return {
            k: (
                "[redacted]"
                if wallet_conflict and k in {"expected", "observed"} and v is not None
                else _redact_report_value(v, str(k))
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_report_value(item) for item in value]
    if isinstance(value, str):
        return _sanitize_report_string(value)
    return value


def _finite_number(value: Any, *, nullable: bool = True) -> bool:
    if value is None:
        return nullable
    if type(value) not in {int, float}:
        return False
    try:
        return Decimal(str(value)).is_finite()
    except (InvalidOperation, TypeError, ValueError):
        return False


def _formation_errors(value: Any, label: str, *, required: bool) -> list[str]:
    if value is None:
        return [f"{label} is required"] if required else []
    if not isinstance(value, dict):
        return [f"{label} must be an object or null"]

    errors = []
    missing = sorted(_FORMATION_FIELDS - value.keys())
    if missing:
        errors.append(f"{label} is missing fields: {', '.join(missing)}")
    if value.get("market_trend") not in {"UP", "SIDEWAYS", "DOWN"}:
        errors.append(f"{label}.market_trend is invalid")
    for field in _FORMATION_FIELDS - {"market_trend"}:
        if not _finite_number(value.get(field), nullable=False):
            errors.append(f"{label}.{field} must be a finite number")
    return errors


def _schema_three_errors(
    custom_info: dict[str, Any], live_config: dict[str, Any] | None
) -> list[str]:
    """Return controller-output contradictions without deriving Agent state."""

    errors: list[str] = []
    required_top = {
        "schema_version",
        "reported_at",
        "controller_id",
        "controller_started_at",
        "lifecycle_state",
        "pnl_quote",
        "pnl_ratio",
        "positions",
        "exit_reason",
        "fault_reason",
    }
    missing_top = sorted(required_top - custom_info.keys())
    if missing_top:
        errors.append(
            "matching controller custom_info is missing fields: "
            + ", ".join(missing_top)
        )
    if custom_info.get("schema_version") != 3:
        errors.append("matching controller custom_info must use schema_version 3")
    if not _finite_number(custom_info.get("reported_at"), nullable=False):
        errors.append(
            "matching controller custom_info.reported_at must be a finite number"
        )
    if not _finite_number(custom_info.get("controller_started_at"), nullable=False):
        errors.append(
            "matching controller custom_info.controller_started_at must be a finite number"
        )
    if custom_info.get("lifecycle_state") not in _CONTROLLER_LIFECYCLE_STATES:
        errors.append("matching controller custom_info has an invalid lifecycle_state")
    if custom_info.get("exit_reason") not in _EXIT_REASONS:
        errors.append("matching controller custom_info has an invalid exit_reason")
    fault_reason = custom_info.get("fault_reason")
    if fault_reason is not None and not isinstance(fault_reason, str):
        errors.append(
            "matching controller custom_info.fault_reason must be a string or null"
        )
    lifecycle_state = custom_info.get("lifecycle_state")
    exit_reason = custom_info.get("exit_reason")
    valid_lifecycle = lifecycle_state in _CONTROLLER_LIFECYCLE_STATES
    valid_exit_reason = exit_reason in _EXIT_REASONS
    if valid_lifecycle and valid_exit_reason:
        if lifecycle_state == "RUNNING" and exit_reason != "none":
            errors.append(
                "matching controller custom_info RUNNING requires exit_reason none"
            )
        if lifecycle_state != "RUNNING" and exit_reason == "none":
            errors.append(
                f"matching controller custom_info {lifecycle_state} requires a non-none exit_reason"
            )
        if lifecycle_state == "EXITED" and exit_reason == "fault":
            errors.append(
                "matching controller custom_info EXITED cannot use exit_reason fault"
            )
    if lifecycle_state in {"RUNNING", "EXITED"} and fault_reason is not None:
        errors.append(
            f"matching controller custom_info {lifecycle_state} requires fault_reason null"
        )
    if lifecycle_state == "FAULTED" and not (
        isinstance(fault_reason, str) and fault_reason
    ):
        errors.append(
            "matching controller custom_info FAULTED requires a non-empty fault_reason"
        )
    if exit_reason == "fault" and not (isinstance(fault_reason, str) and fault_reason):
        errors.append(
            "matching controller custom_info exit_reason fault requires a non-empty fault_reason"
        )
    for field in ("pnl_quote", "pnl_ratio"):
        if not _finite_number(custom_info.get(field)):
            errors.append(
                f"matching controller custom_info.{field} must be finite or null"
            )
    if (custom_info.get("pnl_quote") is None) != (custom_info.get("pnl_ratio") is None):
        errors.append(
            "matching controller custom_info pnl_quote and pnl_ratio must both be numeric or both be null"
        )

    positions = custom_info.get("positions")
    if not isinstance(positions, list) or any(
        not isinstance(position, dict) for position in positions
    ):
        errors.append(
            "matching controller custom_info.positions must be a list of objects"
        )
        return errors

    required_position = {
        "position_id",
        "pool_address",
        "lifecycle_state",
        "executor_id",
        "position_address",
        "ownership_state",
        "lower_price",
        "upper_price",
        "lower_limit_price",
        "upper_limit_price",
        "pnl_quote",
        "pnl_ratio",
        "formation",
        "error",
    }
    telemetry_by_id: dict[str, dict[str, Any]] = {}
    for index, position in enumerate(positions):
        label = f"custom_info.positions[{index}]"
        missing = sorted(required_position - position.keys())
        if missing:
            errors.append(f"{label} is missing fields: {', '.join(missing)}")

        position_id = position.get("position_id")
        if not isinstance(position_id, str) or not position_id:
            errors.append(f"{label}.position_id must be a non-empty string")
        elif position_id in telemetry_by_id:
            errors.append(f"custom_info contains duplicate position_id {position_id}")
        else:
            telemetry_by_id[position_id] = position

        if not isinstance(position.get("pool_address"), str) or not position.get(
            "pool_address"
        ):
            errors.append(f"{label}.pool_address must be a non-empty string")
        lifecycle = position.get("lifecycle_state")
        if lifecycle not in _POSITION_LIFECYCLE_STATES:
            errors.append(f"{label}.lifecycle_state is invalid")
        elif lifecycle_state == "RUNNING" and lifecycle not in _RUNNING_POSITION_STATES:
            errors.append(
                f"{label}.{lifecycle} is impossible while controller is RUNNING"
            )
        elif lifecycle_state == "EXITED" and lifecycle != "EXITED":
            errors.append(f"{label} must be EXITED while controller is EXITED")
        if position.get("ownership_state") not in _OWNERSHIP_STATES:
            errors.append(f"{label}.ownership_state is invalid")

        executor_id = position.get("executor_id")
        if executor_id is not None and (
            not isinstance(executor_id, str) or not executor_id
        ):
            errors.append(f"{label}.executor_id must be a non-empty string or null")
        if (
            lifecycle in {"PENDING", "COOLDOWN", "BLOCKED", "EXITED"}
            and executor_id is not None
        ):
            errors.append(f"{label}.executor_id must be null for {lifecycle}")
        if lifecycle in {"PREPARING", "OPENING", "ACTIVE", "CLOSING"} and not (
            isinstance(executor_id, str) and executor_id
        ):
            errors.append(f"{label}.executor_id is required for {lifecycle}")
        if lifecycle == "EXITED" and position.get("ownership_state") != "ABSENT":
            errors.append(f"{label}.ownership_state must be ABSENT for EXITED")

        position_address = position.get("position_address")
        if position_address is not None and (
            not isinstance(position_address, str) or not position_address
        ):
            errors.append(
                f"{label}.position_address must be a non-empty string or null"
            )
        error = position.get("error")
        if error is not None and not isinstance(error, str):
            errors.append(f"{label}.error must be a string or null")
        for field in (
            "lower_price",
            "upper_price",
            "lower_limit_price",
            "upper_limit_price",
            "pnl_quote",
            "pnl_ratio",
        ):
            if not _finite_number(position.get(field)):
                errors.append(f"{label}.{field} must be finite or null")
        if (position.get("pnl_quote") is None) != (position.get("pnl_ratio") is None):
            errors.append(
                f"{label} pnl_quote and pnl_ratio must both be numeric or both be null"
            )
        if lifecycle == "PREPARING" and (
            position.get("pnl_quote") is not None
            or position.get("pnl_ratio") is not None
        ):
            errors.append(f"{label} PnL must be null for PREPARING")

        formation = position.get("formation")
        if not isinstance(formation, dict):
            errors.append(f"{label}.formation must be an object")
            continue
        missing_formation = {"current", "next"} - formation.keys()
        if missing_formation:
            errors.append(
                f"{label}.formation is missing fields: "
                + ", ".join(sorted(missing_formation))
            )
        current_required = lifecycle in {"PREPARING", "OPENING", "ACTIVE", "CLOSING"}
        errors.extend(
            _formation_errors(
                formation.get("current"),
                f"{label}.formation.current",
                required=current_required,
            )
        )
        errors.extend(
            _formation_errors(
                formation.get("next"), f"{label}.formation.next", required=True
            )
        )

    if live_config is None:
        return errors
    configured_positions = live_config.get("lp_positions")
    if not isinstance(configured_positions, list) or any(
        not isinstance(position, dict) for position in configured_positions
    ):
        errors.append("live controller config.lp_positions must be a list of objects")
        return errors
    config_ids = [position.get("position_id") for position in configured_positions]
    if (
        any(
            not isinstance(position_id, str) or not position_id
            for position_id in config_ids
        )
        or len(config_ids) != len(set(config_ids))
        or set(config_ids) != set(telemetry_by_id)
    ):
        errors.append(
            "custom_info positions must exactly match live config position_id values"
        )
        return errors

    for configured in configured_positions:
        position_id = configured["position_id"]
        telemetry = telemetry_by_id[position_id]
        if telemetry.get("pool_address") != configured.get("pool_address"):
            errors.append(
                f"custom_info position {position_id} pool_address does not match live config"
            )
    return errors


async def _save_report(payload: dict[str, Any]) -> None:
    generation = payload["expected"].get("generation") or "vacant"
    safe_payload = _redact_report_value(payload)
    builder = ReportBuilder(f"Trend-Aware LP Session — {generation}")
    builder.markdown(
        "```json\n"
        + json.dumps(safe_payload, indent=2, sort_keys=True, default=str)
        + "\n```"
    )
    await builder.save()


def _safe_report_error(exc: BaseException) -> str:
    return f"Report generation failed: {type(exc).__name__}"


async def _get_client(context: Any) -> Any:
    from config_manager import get_client

    return await get_client(context._chat_id, context=context)


async def _await_source_calls(
    calls: dict[str, Any], *, deadline: float, loop: asyncio.AbstractEventLoop
) -> dict[str, Any]:
    tasks = {name: asyncio.create_task(call) for name, call in calls.items()}
    try:
        done, pending = await asyncio.wait(
            tasks.values(), timeout=max(0.0, deadline - loop.time())
        )
    except asyncio.CancelledError:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        raise

    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    results: dict[str, Any] = {}
    for name, task in tasks.items():
        if task not in done:
            results[name] = TimeoutError(
                f"{name} did not complete within the read deadline"
            )
            continue
        try:
            results[name] = task.result()
        except Exception as exc:
            results[name] = exc
    return results


def _discover_runtime(config: Config, active_status: Any) -> str | None:
    if (
        config.expected_runtime_instance is not None
        or config.expected_generation is None
    ):
        return None
    try:
        active_data = _response_data(active_status, "active_status")
    except ValueError:
        return None
    if not isinstance(active_data, dict):
        return None
    matches = [
        name
        for name, bot in active_data.items()
        if isinstance(name, str)
        and isinstance(bot, dict)
        and _valid_runtime_name(name, config.namespace)
        and config.expected_generation in _performance(bot)
    ]
    return matches[0] if len(matches) == 1 else None


async def _read_sources(config: Config, context: Any) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + float(config.timeout_seconds)
    client = await asyncio.wait_for(
        _get_client(context), timeout=max(0.0, deadline - loop.time())
    )
    calls: dict[str, Any] = {
        "active_status": client.bot_orchestration.get_active_bots_status(),
        "gateway_wallets": client.accounts.list_gateway_wallets(),
    }
    if config.expected_runtime_instance is None:
        if config.expected_config_name is not None:
            calls["saved_config"] = client.controllers.get_controller_config(
                config.expected_config_name
            )
    else:
        calls["live_configs"] = client.controllers.get_bot_controller_configs(
            config.expected_runtime_instance
        )
        calls["bot_runs"] = client.bot_orchestration.get_bot_runs(
            bot_name=config.expected_runtime_instance,
            limit=2,
            offset=0,
            include_final_status=False,
        )

    results = await _await_source_calls(calls, deadline=deadline, loop=loop)
    discovered_runtime = _discover_runtime(config, results.get("active_status"))
    if discovered_runtime is not None:
        results["discovered_runtime_instance"] = discovered_runtime
        follow_up = {
            "live_configs": client.controllers.get_bot_controller_configs(
                discovered_runtime
            ),
            "bot_runs": client.bot_orchestration.get_bot_runs(
                bot_name=discovered_runtime,
                limit=2,
                offset=0,
                include_final_status=False,
            ),
        }
        results.update(
            await _await_source_calls(follow_up, deadline=deadline, loop=loop)
        )
    return results


def _source_error(source: str, value: Any) -> str | None:
    if isinstance(value, Exception):
        return f"{source} unavailable: {type(value).__name__}"
    return None


def _canonical_payload(config: Config, results: dict[str, Any]) -> dict[str, Any]:
    observed_at = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "status": "complete",
        "observed_at": observed_at.isoformat(),
        "mutation": False,
        "report_error": None,
        "expected": {
            "namespace": config.namespace,
            "account_name": config.account_name,
            "generation": config.expected_generation,
            "config_name": config.expected_config_name,
            "runtime_instance": config.expected_runtime_instance,
        },
        "authority_observability": {
            "account": "not_observable",
            "wallet": "not_observable",
        },
        "gateway_wallet_observation": None,
        "active_matches": [],
        "namespace_conflicts": [],
        "account_conflicts": [],
        "wallet_conflicts": [],
        "config": None,
        "custom_info": None,
        "bot_run_matches": [],
        "archive_record": None,
        "errors": [],
        "warnings": [],
    }
    required_failure = False

    # Gateway wallet metadata is retained only for human diagnostics. The configured
    # HAPI account is the Strategy's authority boundary, so missing or malformed wallet
    # metadata must not affect lifecycle status or mutation eligibility.
    if _source_error("gateway_wallets", results.get("gateway_wallets")) is None:
        try:
            payload["gateway_wallet_observation"] = _solana_wallet_observation(
                results.get("gateway_wallets")
            )
        except ValueError:
            pass

    active_error = _source_error("active_status", results.get("active_status"))
    active_data: dict[str, Any] = {}
    active_status_readable = False
    if active_error:
        payload["errors"].append(active_error)
        required_failure = True
    else:
        try:
            value = _response_data(results.get("active_status"), "active_status")
            if not isinstance(value, dict):
                raise ValueError(
                    "active_status data must be an object keyed by bot name"
                )
            if any(
                not isinstance(name, str) or not isinstance(bot, dict)
                for name, bot in value.items()
            ):
                raise ValueError("active_status entries must be bot-name/object pairs")
            active_data = value
            active_status_readable = True
        except ValueError as exc:
            payload["errors"].append(str(exc))
            required_failure = True

    discovered_runtime = results.get("discovered_runtime_instance")
    effective_runtime = (
        config.expected_runtime_instance
        if config.expected_runtime_instance is not None
        else discovered_runtime if isinstance(discovered_runtime, str) else None
    )

    discovered_namespace_bots = {
        name: bot
        for name, bot in active_data.items()
        if _in_namespace(name, config.namespace)
    }
    namespace_bots = {
        name: bot
        for name, bot in discovered_namespace_bots.items()
        if _valid_runtime_name(name, config.namespace)
    }

    expected_runtime = effective_runtime
    if expected_runtime is None and config.expected_generation is None:
        # A fresh Condor session owns no external runtime until its own deploy
        # intent records a generation. Condor's injected executor attribution can
        # include older sessions, so namespace membership alone is never adoption.
        matching_bots = {}
    elif expected_runtime is None:
        matching_bots = {
            name: bot
            for name, bot in namespace_bots.items()
            if config.expected_generation in _performance(bot)
        }
    else:
        matching_bots = {
            name: bot
            for name, bot in namespace_bots.items()
            if name == expected_runtime
        }

    for name, bot in matching_bots.items():
        payload["active_matches"].append(
            _active_row(name, bot, config.namespace, config.expected_generation)
        )

    if len(payload["active_matches"]) > 1 or payload["namespace_conflicts"]:
        required_failure = True
    if (
        config.expected_runtime_instance is not None
        and active_status_readable
        and not payload["active_matches"]
        and not config.include_archive_record
    ):
        payload["errors"].append(
            "expected runtime instance is absent from active bot status"
        )
        required_failure = True

    observed_accounts: list[tuple[str, str]] = []
    owned_run_accounts: list[tuple[str, str]] = []
    for row in payload["active_matches"]:
        if row["account_name"] is not None:
            observed_accounts.append((row["runtime_instance"], row["account_name"]))

    if expected_runtime is None and config.expected_config_name is not None:
        saved_error = _source_error("saved_config", results.get("saved_config"))
        if saved_error:
            payload["errors"].append(saved_error)
            required_failure = True
        else:
            saved = results.get("saved_config")
            if not isinstance(saved, dict):
                payload["errors"].append("saved_config response must be an object")
                required_failure = True
            else:
                payload["config"] = copy.deepcopy(saved)
                if saved.get("id") != config.expected_generation:
                    payload["namespace_conflicts"].append(
                        {
                            "runtime_instance": None,
                            "field": "config.id",
                            "expected": config.expected_generation,
                            "observed": saved.get("id"),
                        }
                    )
                    required_failure = True
                config_file = saved.get("_config_name")
                if (
                    config_file is not None
                    and config_file != config.expected_config_name
                ):
                    payload["namespace_conflicts"].append(
                        {
                            "runtime_instance": None,
                            "field": "config._config_name",
                            "expected": config.expected_config_name,
                            "observed": config_file,
                        }
                    )
                    required_failure = True

    if expected_runtime is not None:
        live_error = _source_error("live_configs", results.get("live_configs"))
        live_configs = results.get("live_configs")
        if live_error:
            target = (
                payload["warnings"]
                if config.include_archive_record
                else payload["errors"]
            )
            target.append(live_error)
            required_failure = required_failure or not config.include_archive_record
        elif not isinstance(live_configs, list) or any(
            not isinstance(item, dict) for item in live_configs
        ):
            message = "live_configs response must be a list of objects"
            target = (
                payload["warnings"]
                if config.include_archive_record
                else payload["errors"]
            )
            target.append(message)
            required_failure = required_failure or not config.include_archive_record
        else:
            exact_configs = [
                item
                for item in live_configs
                if item.get("id") == config.expected_generation
                and item.get("_config_name") == config.expected_config_name
            ]
            if len(exact_configs) == 1:
                payload["config"] = copy.deepcopy(exact_configs[0])
            elif (
                len(exact_configs) == 0
                and config.include_archive_record
                and not live_configs
            ):
                payload["warnings"].append(
                    "archived runtime has no readable live controller config"
                )
            else:
                payload["errors"].append(
                    "live controller config identity matched "
                    f"{len(exact_configs)} records; exactly one is required"
                )
                for item in live_configs:
                    payload["namespace_conflicts"].append(
                        {
                            "runtime_instance": expected_runtime,
                            "field": "controller_config_identity",
                            "expected": {
                                "id": config.expected_generation,
                                "_config_name": config.expected_config_name,
                            },
                            "observed": {
                                "id": item.get("id"),
                                "_config_name": item.get("_config_name"),
                            },
                        }
                    )
                required_failure = True

        runs_error = _source_error("bot_runs", results.get("bot_runs"))
        if runs_error:
            payload["errors"].append(runs_error)
            required_failure = True
        else:
            try:
                runs = _response_data(results.get("bot_runs"), "bot_runs")
                if not isinstance(runs, list) or any(
                    not isinstance(row, dict) for row in runs
                ):
                    raise ValueError("bot_runs data must be a list of objects")
                exact_runs = [
                    copy.deepcopy(row)
                    for row in runs
                    if row.get("bot_name") == expected_runtime
                ]
                payload["bot_run_matches"] = exact_runs
                if len(exact_runs) > 1:
                    payload["errors"].append(
                        "multiple exact bot-run records matched the runtime instance"
                    )
                    required_failure = True
                if config.include_archive_record:
                    if len(exact_runs) != 1:
                        payload["errors"].append(
                            "archive confirmation requires exactly one exact bot-run record"
                        )
                        required_failure = True
                    elif payload["active_matches"]:
                        payload["errors"].append(
                            "archive confirmation requires the exact runtime to be absent from active status"
                        )
                        required_failure = True
                    elif exact_runs[0].get("deployment_status") != "ARCHIVED":
                        payload["errors"].append(
                            "archive confirmation requires deployment_status ARCHIVED"
                        )
                        required_failure = True
                    else:
                        payload["archive_record"] = copy.deepcopy(exact_runs[0])
                for row in exact_runs:
                    account = _first_string(
                        row, ("account_name", "credentials_profile", "account")
                    )
                    if account is not None:
                        observed_accounts.append((expected_runtime, account))
                        owned_run_accounts.append((expected_runtime, account))
            except ValueError as exc:
                payload["errors"].append(str(exc))
                required_failure = True

    for runtime, account in observed_accounts:
        if account != config.account_name:
            payload["account_conflicts"].append(
                {
                    "runtime_instance": runtime,
                    "field": "account_name",
                    "expected": config.account_name,
                    "observed": account,
                }
            )
            required_failure = True
    if owned_run_accounts:
        payload["authority_observability"]["account"] = "observed"

    if len(payload["active_matches"]) == 1 and config.expected_generation is not None:
        runtime = payload["active_matches"][0]["runtime_instance"]
        bot = matching_bots[runtime]
        performance = _performance(bot)
        controller_ids = list(performance)
        if len(controller_ids) != 1 or controller_ids[0] != config.expected_generation:
            payload["namespace_conflicts"].append(
                {
                    "runtime_instance": runtime,
                    "field": "controller_ids",
                    "expected": [config.expected_generation],
                    "observed": controller_ids,
                }
            )
            required_failure = True

        custom_info = _controller_custom_info(bot, config.expected_generation)
        if config.expected_generation not in performance:
            payload["errors"].append(
                "active bot does not contain the expected controller generation"
            )
            required_failure = True
        elif custom_info is None:
            payload["errors"].append("matching controller does not contain custom_info")
            required_failure = True
        else:
            payload["custom_info"] = copy.deepcopy(custom_info)
            if not isinstance(custom_info, dict):
                payload["errors"].append(
                    "matching controller custom_info must be a schema_version 3 object"
                )
                required_failure = True
            else:
                controller_id = custom_info.get("controller_id")
                if controller_id != config.expected_generation:
                    payload["namespace_conflicts"].append(
                        {
                            "runtime_instance": runtime,
                            "field": "custom_info.controller_id",
                            "expected": config.expected_generation,
                            "observed": controller_id,
                        }
                    )
                    required_failure = True
                schema_errors = _schema_three_errors(
                    custom_info,
                    payload["config"] if isinstance(payload["config"], dict) else None,
                )
                if bot.get("status") != "running":
                    schema_errors.append(
                        "matching active bot raw status must be running"
                    )
                if bot.get("recently_active") is not True:
                    schema_errors.append(
                        "matching active bot recently_active must be true"
                    )
                reported_at = custom_info.get("reported_at")
                if _finite_number(reported_at, nullable=False):
                    age_seconds = Decimal(str(observed_at.timestamp())) - Decimal(
                        str(reported_at)
                    )
                    if age_seconds < 0:
                        schema_errors.append(
                            "matching controller custom_info.reported_at is in the future"
                        )
                    elif age_seconds > 30:
                        schema_errors.append(
                            "matching controller custom_info.reported_at is older than 30 seconds"
                        )
                if schema_errors:
                    payload["errors"].extend(schema_errors)
                    required_failure = True

    if required_failure:
        payload["status"] = "unavailable"
    elif (
        payload["warnings"]
        or payload["authority_observability"]["account"] == "not_observable"
    ):
        payload["status"] = "degraded"
    return payload


def _routine_result(payload: dict[str, Any]) -> RoutineResult:
    summary = (
        f"Trend-aware LP session read: {payload['status']}; "
        f"active={len(payload['active_matches'])}, "
        f"namespace_conflicts={len(payload['namespace_conflicts'])}, "
        f"account_conflicts={len(payload['account_conflicts'])}, "
        f"wallet_conflicts={len(payload['wallet_conflicts'])}."
    )
    rows = copy.deepcopy(payload["active_matches"])
    columns = (
        list(rows[0].keys())
        if rows
        else [
            "runtime_instance",
            "namespace",
            "account_name",
            "wallet_address",
            "deployment_status",
            "raw_status",
            "recently_active",
            "reported_at",
            "controller_ids",
        ]
    )
    return RoutineResult(
        text=summary,
        table_columns=columns,
        table_data=rows,
        sections=[{"type": "canonical_payload", "data": copy.deepcopy(payload)}],
    )


def _unavailable_payload(config: Config, error: str) -> dict[str, Any]:
    """Return the full result shape without carrying partial authority evidence."""

    return {
        "status": "unavailable",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "mutation": False,
        "report_error": None,
        "expected": {
            "namespace": config.namespace,
            "account_name": config.account_name,
            "generation": config.expected_generation,
            "config_name": config.expected_config_name,
            "runtime_instance": config.expected_runtime_instance,
        },
        "authority_observability": {
            "account": "not_observable",
            "wallet": "not_observable",
        },
        "gateway_wallet_observation": None,
        "active_matches": [],
        "namespace_conflicts": [],
        "account_conflicts": [],
        "wallet_conflicts": [],
        "config": None,
        "custom_info": None,
        "bot_run_matches": [],
        "archive_record": None,
        "errors": [error],
        "warnings": [],
    }


def _result_projection(result: RoutineResult) -> dict[str, Any]:
    return {
        "text": result.text,
        "table_data": result.table_data,
        "table_columns": result.table_columns,
        "chart_image": result.chart_image,
        "sections": result.sections,
    }


def _projection_error(result: RoutineResult) -> str | None:
    try:
        encoded = json.dumps(
            _result_projection(result),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (OverflowError, TypeError, ValueError):
        return "session read unavailable: Agent-facing result is not strict-JSON serializable"
    if len(encoded) > MAX_AGENT_RESULT_BYTES:
        return (
            "session read unavailable: Agent-facing result exceeds "
            f"{MAX_AGENT_RESULT_BYTES}-byte strict-JSON limit"
        )
    return None


def _bounded_result(
    config: Config, payload: dict[str, Any]
) -> tuple[dict[str, Any], RoutineResult]:
    result = _routine_result(payload)
    error = _projection_error(result)
    if error is None:
        return payload, result
    unavailable = _unavailable_payload(config, error)
    fallback = _routine_result(unavailable)
    fallback_error = _projection_error(fallback)
    if fallback_error is not None:  # pragma: no cover - fixed, bounded constants only
        raise RuntimeError(fallback_error)
    return unavailable, fallback


async def run(config: Config, context: Any) -> RoutineResult:
    """Read, normalize, report, and return current-session evidence without mutation."""

    try:
        results = await _read_sources(config, context)
        payload = _canonical_payload(config, results)
    except Exception as exc:
        payload = _unavailable_payload(
            config, f"session read unavailable: {type(exc).__name__}"
        )

    payload, result = _bounded_result(config, payload)
    try:
        await _save_report(payload)
    except asyncio.CancelledError as exc:
        payload["report_error"] = _safe_report_error(exc)
    except Exception as exc:
        payload["report_error"] = _safe_report_error(exc)
    payload, result = _bounded_result(config, payload)
    return result
