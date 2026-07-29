import asyncio
import importlib
import json
import time
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictStr, field_validator

from agents.lp_wizard.routines import _reporting, _shared

if getattr(_shared, "RUNTIME_VERSION", 0) < 1:
    _shared = importlib.reload(_shared)

CATEGORY = "LP Wizard"
STATE_TIMEOUT_SECONDS = 45
TERMINAL_EXECUTOR_STATES = {
    "COMPLETE",
    "COMPLETED",
    "TERMINATED",
    "CANCELED",
    "CANCELLED",
    "CLOSED",
    "ERROR",
    "FAILED",
    "STOPPED",
}
_STOP_MARKER = "GLOBAL_SESSION_STOP_LATCHED"
_STOP_COMMAND = (
    f"GLOBAL SESSION STOP: write or refresh an action journal entry prefixed "
    f"{_STOP_MARKER}, then do not scan or open. Treat that journaled trigger as "
    "latched for the rest of the controller session. Resolve any pending mutation "
    "with recover, then close every occupied slot one at a time with close and call "
    "state after each action. Once flat, remain flat and refresh the journal marker "
    "on later ticks."
)
_INCOMPLETE_COMMAND = (
    "SESSION STOP EVALUATION INCOMPLETE: do not scan or open another slot. Keep "
    "existing positions under supervision, resolve reported evidence problems, and "
    "call state again. Do not infer a PnL or age threshold result from partial data."
)


class Config(BaseModel):
    """Inspect controller-bound LP slots and external ownership without mutating."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    controller_id: StrictStr | None = None

    @field_validator("controller_id")
    @classmethod
    def _controller_id(cls, value: str | None) -> str | None:
        if value is not None and (not value or value != value.strip()):
            raise ValueError(
                "controller_id must be non-empty without surrounding whitespace"
            )
        if value is None:
            return None
        return _shared.resolve_session(value).controller_id


def _compact(value: dict[str, Any]) -> str:
    return json.dumps(_shared.json_value(value), separators=(",", ":"), sort_keys=True)


def _error(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _range_state(position: dict[str, Any], executor: dict[str, Any]) -> str | None:
    reported = _shared.text(executor.get("range_state")).upper()
    if reported:
        return reported
    custom = executor["custom_info"]
    try:
        price = _shared.decimal(
            executor.get("current_price"), "current price", positive=True
        )
        lower = _shared.decimal(position["lower_price"], positive=True)
        upper = _shared.decimal(position["upper_price"], positive=True)
    except ValueError:
        return None
    return "IN_RANGE" if lower <= price <= upper else "OUT_OF_RANGE"


def _telemetry(position: dict[str, Any], executor: dict[str, Any]) -> dict[str, Any]:
    custom = executor["custom_info"]
    fields = {
        "current_price": executor.get("current_price"),
        "base_amount": _shared.read(custom, "base_amount", "base_token_amount"),
        "quote_amount": _shared.read(custom, "quote_amount", "quote_token_amount"),
        "base_fees": _shared.read(custom, "base_fee", "base_fees"),
        "quote_fees": _shared.read(custom, "quote_fee", "quote_fees"),
        "fees_earned_quote": executor.get("fees_earned_quote"),
        "net_pnl_quote": executor.get("net_pnl_quote"),
        "net_pnl_pct": executor.get("net_pnl_pct"),
        "filled_amount_quote": executor.get("filled_amount_quote"),
        "is_trading": executor.get("is_trading"),
        "range_state": _range_state(position, executor),
    }
    return {key: value for key, value in fields.items() if value is not None}


def _aggregate_performance(slots: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in ("fees_earned_quote", "net_pnl_quote", "filled_amount_quote"):
        values = [
            row["telemetry"][field]
            for row in slots
            if row["telemetry"].get(field) is not None
        ]
        if values:
            result[field] = sum(
                (_shared.decimal(value, field) for value in values), Decimal(0)
            )
    filled = result.get("filled_amount_quote")
    pnl = result.get("net_pnl_quote")
    if filled is not None and pnl is not None and filled > 0:
        result["net_pnl_ratio"] = pnl / filled
    return result


def _stop_limits(config: _shared.Config) -> dict[str, Decimal]:
    return {
        "session_max_age_minutes": config.session_max_age_minutes,
        "session_take_profit_net_pnl_ratio": config.session_take_profit_net_pnl_ratio,
        "session_stop_loss_net_pnl_ratio": config.session_stop_loss_net_pnl_ratio,
    }


def _session_stop_advisory(
    session: _shared.Session,
    config: _shared.Config,
    executors: list[dict[str, Any]] | None,
    now: datetime,
) -> dict[str, Any]:
    try:
        started_at = datetime.fromtimestamp(
            session.config_path.stat().st_mtime, tz=timezone.utc
        )
    except (OSError, OverflowError, ValueError):
        started_at = None
    age_seconds = (now - started_at).total_seconds() if started_at else None
    age_complete = age_seconds is not None and age_seconds >= 0
    age_minutes = (
        Decimal(str(max(0.0, age_seconds / 60))).quantize(Decimal("0.000001"))
        if age_complete
        else None
    )

    duplicate_ids: set[str] = set()
    missing_pnl_ids: list[str] = []
    seen_ids: set[str] = set()
    pnl_complete = executors is not None
    session_net_pnl_quote: Decimal | None = Decimal(0) if pnl_complete else None
    if executors is not None:
        for executor in executors:
            executor_id = _shared.text(executor.get("executor_id"))
            if executor_id in seen_ids:
                duplicate_ids.add(executor_id)
            seen_ids.add(executor_id)
            value = executor.get("net_pnl_quote")
            if value is None:
                missing_pnl_ids.append(executor_id)
            else:
                session_net_pnl_quote += _shared.decimal(
                    value, f"executor {executor_id} net_pnl_quote"
                )
        if duplicate_ids or missing_pnl_ids:
            pnl_complete = False
            session_net_pnl_quote = None

    session_net_pnl_ratio = (
        session_net_pnl_quote / config.total_amount_quote
        if session_net_pnl_quote is not None
        else None
    )
    reasons: list[str] = []
    if (
        session_net_pnl_ratio is not None
        and session_net_pnl_ratio <= -config.session_stop_loss_net_pnl_ratio
    ):
        reasons.append("session_stop_loss_reached")
    if (
        session_net_pnl_ratio is not None
        and session_net_pnl_ratio >= config.session_take_profit_net_pnl_ratio
    ):
        reasons.append("session_take_profit_reached")
    if age_minutes is not None and age_minutes >= config.session_max_age_minutes:
        reasons.append("session_max_age_reached")

    triggered = bool(reasons)
    attention_reasons: list[str] = []
    if not pnl_complete:
        attention_reasons.append("session_pnl_unavailable")
    if not age_complete:
        attention_reasons.append("session_age_unavailable")
    must_take_action = triggered or bool(attention_reasons)
    return {
        "policy": "llm_mandatory_advisory_not_code_enforced",
        "session_started_at": started_at.isoformat() if started_at else None,
        "session_age_source": "immutable_session_config_mtime",
        "session_age_minutes": age_minutes,
        "session_age_complete": age_complete,
        "limits": _stop_limits(config),
        "performance": {
            "source": "sum_controller_executor_net_pnl_quote",
            "executor_count": len(executors) if executors is not None else None,
            "pnl_complete": pnl_complete,
            "missing_net_pnl_executor_ids": sorted(missing_pnl_ids),
            "duplicate_executor_ids": sorted(duplicate_ids),
            "session_net_pnl_quote": session_net_pnl_quote,
            "session_net_pnl_ratio": session_net_pnl_ratio,
            "ratio_denominator_quote": config.total_amount_quote,
        },
        "triggered": triggered,
        "reasons": reasons,
        "attention_reasons": attention_reasons,
        "must_take_action": must_take_action,
        "command": (
            _STOP_COMMAND
            if triggered
            else _INCOMPLETE_COMMAND if attention_reasons else None
        ),
    }


def _slot_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "slot_id": slot_id,
            "pool_address": _shared.pool_address(slot),
            "position": slot["position"],
            "pending_mutation": slot["pending_mutation"],
            "executor_evidence": None,
            "gateway_pool_evidence": None,
            "gateway_position_evidence": None,
            "telemetry": {},
            "ownership_contradictions": [],
        }
        for slot_id, slot in state["slots"].items()
    ]


async def _execute(config: Config, context: Any, debug: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    observed_at = now.isoformat()
    fallback_session_stop = None
    try:
        debug["stage"] = "resolve_session"
        session = _shared.resolve_session(config.controller_id)
        debug["stage"] = "load_config"
        try:
            strategy_config = _shared.load_config(session)
        except ValueError as error:
            if session.live and "server_name" in str(error):
                return _compact(
                    {
                        "controller_id": session.controller_id,
                        "controller_mode": "live",
                        "observed_at": observed_at,
                        "slots": [],
                        "balances": [],
                        "ownership_contradictions": [],
                        "errors": [_error(error)],
                        "health": "blocked",
                        "mutations_allowed": False,
                    }
                )
            raise
        if not session.live:
            return _compact(
                {
                    "controller_id": session.controller_id,
                    "controller_mode": "experiment",
                    "state_mode": "stateless_read_only",
                    "observed_at": observed_at,
                    "config": {
                        "risk_profile": strategy_config.risk_profile,
                        "total_amount_quote": strategy_config.total_amount_quote,
                        "max_slot_count": strategy_config.max_slot_count,
                    },
                    "session_stop": {
                        "status": "not_evaluated_for_experiment",
                        "limits": _stop_limits(strategy_config),
                    },
                    "slots": [],
                    "capital": {
                        "committed_quote": Decimal(0),
                        "free_quote": strategy_config.total_amount_quote,
                        "occupied_slot_count": 0,
                        "free_slot_count": strategy_config.max_slot_count,
                    },
                    "pool_usage": {},
                    "balances": [],
                    "ownership_contradictions": [],
                    "errors": [],
                    "health": "healthy",
                    "mutations_allowed": False,
                }
            )

        debug["stage"] = "read_local_state"
        stop_without_pnl = _session_stop_advisory(session, strategy_config, None, now)
        fallback_session_stop = stop_without_pnl
        # State writes use atomic replacement, so a lock-free read is a stable snapshot.
        try:
            persisted_state = _shared.read_state(session, strategy_config)
        except ValueError as error:
            if "binding server_name mismatch" in str(error):
                return _compact(
                    {
                        "controller_id": session.controller_id,
                        "controller_mode": "live",
                        "observed_at": observed_at,
                        "slots": [],
                        "balances": [],
                        "ownership_contradictions": [],
                        "session_stop": stop_without_pnl,
                        "errors": [_error(error)],
                        "health": "blocked",
                        "mutations_allowed": False,
                    }
                )
            raise
        state = persisted_state or _shared.initial_state(strategy_config)
        state_source = (
            "persisted" if persisted_state is not None else "in_memory_initial"
        )
        capital = _shared.capital(state, strategy_config)
        slots = _slot_rows(state)
        binding = state["binding"]
        immutable_server = _shared.text(getattr(strategy_config, "server_name", None))
        server_error = None
        if not immutable_server:
            server_error = "immutable live config lacks server_name"
        elif binding is not None and binding["server_name"] != immutable_server:
            server_error = "persisted binding server differs from immutable live config"

        if server_error:
            return _compact(
                {
                    "controller_id": session.controller_id,
                    "controller_mode": "live",
                    "state_mode": state_source,
                    "observed_at": observed_at,
                    "binding": binding,
                    "slots": slots,
                    "capital": {
                        key: value
                        for key, value in capital.items()
                        if key != "pool_usage"
                    },
                    "pool_usage": capital["pool_usage"],
                    "aggregate_performance": {},
                    "session_stop": stop_without_pnl,
                    "balances": [],
                    "ownership_contradictions": [],
                    "errors": [server_error],
                    "health": "blocked",
                    "mutations_allowed": False,
                }
            )

        debug["stage"] = "bind_server"
        try:
            bound = await _shared.bound_client(context, immutable_server)
        except Exception as error:
            return _compact(
                {
                    "controller_id": session.controller_id,
                    "controller_mode": "live",
                    "state_mode": state_source,
                    "observed_at": observed_at,
                    "binding": binding,
                    "slots": slots,
                    "capital": {
                        key: value
                        for key, value in capital.items()
                        if key != "pool_usage"
                    },
                    "pool_usage": capital["pool_usage"],
                    "aggregate_performance": {},
                    "session_stop": stop_without_pnl,
                    "balances": [],
                    "ownership_contradictions": [],
                    "errors": [f"server binding/read failed: {_error(error)}"],
                    "health": "blocked",
                    "mutations_allowed": False,
                }
            )

        debug["stage"] = "read_external_state"
        executor_result, wallet_result = await asyncio.gather(
            _shared.search_controller_executors(bound.client, session.controller_id),
            _shared.default_solana_wallet(bound.client),
            return_exceptions=True,
        )
        errors: list[str] = []
        if isinstance(executor_result, BaseException):
            errors.append(f"executor read failed: {_error(executor_result)}")
            executors: list[dict[str, Any]] = []
            stop_executors = None
        else:
            executors = executor_result
            stop_executors = executors
        session_stop = _session_stop_advisory(
            session, strategy_config, stop_executors, now
        )
        fallback_session_stop = session_stop
        if isinstance(wallet_result, BaseException):
            errors.append(f"default wallet read failed: {_error(wallet_result)}")
            wallet = None
        else:
            wallet = wallet_result
        balances: list[dict[str, Any]] = []

        pools = sorted(capital["pool_usage"])
        pool_results, position_results = await asyncio.gather(
            asyncio.gather(
                *(_shared.get_gateway_pool(bound.client, pool) for pool in pools),
                return_exceptions=True,
            ),
            asyncio.gather(
                *(
                    _shared.get_owned_positions(bound.client, pool, wallet)
                    for pool in pools
                    if wallet is not None
                ),
                return_exceptions=True,
            ),
        )
        pool_info_by_pool: dict[str, dict[str, Any]] = {}
        for pool, result in zip(pools, pool_results, strict=True):
            if isinstance(result, BaseException):
                errors.append(f"Gateway pool read failed for {pool}: {_error(result)}")
            else:
                pool_info_by_pool[pool] = result
        positions_by_pool: dict[str, list[dict[str, Any]]] = {}
        if wallet is not None:
            for pool, result in zip(pools, position_results, strict=True):
                if isinstance(result, BaseException):
                    errors.append(
                        f"Gateway positions read failed for {pool}: {_error(result)}"
                    )
                else:
                    positions_by_pool[pool] = result

        contradictions: list[dict[str, Any]] = []
        historical_executors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        if (
            binding is not None
            and wallet is not None
            and wallet != binding["observed_default_wallet"]
        ):
            contradictions.append(
                {
                    "type": "default_wallet_conflict",
                    "persisted": binding["observed_default_wallet"],
                    "observed": wallet,
                }
            )

        executors_by_id: dict[str, list[dict[str, Any]]] = {}
        for executor in executors:
            executors_by_id.setdefault(executor["executor_id"], []).append(executor)
        tracked_executor_ids: set[str] = set()
        for row in slots:
            position = row["position"]
            if position is None:
                continue
            executor_id = position["executor_id"]
            pool = position["pool_address"]
            position_address = position["position_address"]
            tracked_executor_ids.add(executor_id)
            executor_matches = executors_by_id.get(executor_id, [])
            if not executor_matches:
                row["ownership_contradictions"].append("missing_executor")
            elif len(executor_matches) != 1:
                row["ownership_contradictions"].append("conflicting_executor_identity")
            else:
                executor = executor_matches[0]
                row["executor_evidence"] = {
                    "executor_id": executor["executor_id"],
                    "controller_id": executor["controller_id"],
                    "status": executor["status"],
                    "is_active": executor["is_active"],
                    "pool_address": executor["pool_address"],
                    "position_address": executor["position_address"],
                    "position_address_present": executor["position_address_present"],
                    "close_type": executor["close_type"],
                    "custom_state": executor["custom_state"],
                }
                if (
                    not executor["is_active"]
                    or executor["status"] in TERMINAL_EXECUTOR_STATES
                ):
                    row["ownership_contradictions"].append(
                        "executor_terminal_or_inactive"
                    )
                if executor["controller_id"] != position["controller_id"]:
                    row["ownership_contradictions"].append(
                        "executor_controller_conflict"
                    )
                if executor["pool_address"] != pool:
                    row["ownership_contradictions"].append("executor_pool_conflict")
                if executor["position_address"] != position_address:
                    row["ownership_contradictions"].append("executor_position_conflict")
                executor_config = executor["config"]
                if executor_config.get("keep_position") is not True:
                    row["ownership_contradictions"].append(
                        "executor_keep_position_conflict"
                    )
                if _shared.text(executor["custom_state"]).upper() == "FAILED":
                    row["ownership_contradictions"].append(
                        "executor_custom_info_failed"
                    )
                for field in (
                    "lower_price",
                    "upper_price",
                    "lower_limit_price",
                    "upper_limit_price",
                ):
                    observed = executor_config.get(field)
                    if observed not in (None, ""):
                        try:
                            matches = _shared.decimal_matches(
                                observed, position[field], field
                            )
                        except ValueError:
                            matches = False
                        if not matches:
                            row["ownership_contradictions"].append(
                                f"executor_{field}_conflict"
                            )
                row["telemetry"] = _telemetry(position, executor)

            pool_info = pool_info_by_pool.get(pool)
            if pool_info is not None:
                row["gateway_pool_evidence"] = pool_info
                if pool_info["base_mint"] != position["base_mint"]:
                    row["ownership_contradictions"].append(
                        "gateway_pool_base_mint_conflict"
                    )
                if pool_info["quote_mint"] != position["quote_mint"]:
                    row["ownership_contradictions"].append(
                        "gateway_pool_quote_mint_conflict"
                    )

            owned = positions_by_pool.get(pool)
            if owned is not None:
                matches = [
                    item
                    for item in owned
                    if item["position_address"] == position_address
                ]
                if len(matches) != 1:
                    row["ownership_contradictions"].append("missing_gateway_position")
                else:
                    row["gateway_position_evidence"] = matches[0]
            for contradiction in row["ownership_contradictions"]:
                contradictions.append(
                    {
                        "type": contradiction,
                        "slot_id": row["slot_id"],
                        "executor_id": executor_id,
                    }
                )

        for executor in executors:
            if executor["executor_id"] not in tracked_executor_ids:
                evidence = {
                    "executor_id": executor["executor_id"],
                    "status": executor["status"],
                    "pool_address": executor["pool_address"],
                    "position_address": executor["position_address"],
                    "position_address_present": executor["position_address_present"],
                    "close_type": executor["close_type"],
                    "custom_state": executor["custom_state"],
                }
                if executor["is_active"]:
                    contradictions.append(
                        {"type": "untracked_owned_executor", **evidence}
                    )
                elif executor["status"] in TERMINAL_EXECUTOR_STATES:
                    historical_executors.append(evidence)
                if _shared.text(executor["custom_state"]).upper() == "FAILED":
                    contradictions.append(
                        {"type": "executor_custom_info_failed", **evidence}
                    )
            if executor["close_type"] == "POSITION_HOLD":
                warnings.append(
                    {
                        "type": "virtual_position_hold",
                        "executor_id": executor["executor_id"],
                        "position_address": executor["position_address"],
                    }
                )
        debug["stage"] = "assemble_result"
        current_pending = any(row["pending_mutation"] is not None for row in slots)
        blocked = bool(errors or contradictions or current_pending)
        return _compact(
            {
                "controller_id": session.controller_id,
                "controller_mode": "live",
                "state_mode": state_source,
                "observed_at": observed_at,
                "server_name": bound.server_name,
                "binding": binding,
                "default_solana_wallet": wallet,
                "slots": slots,
                "capital": {
                    key: value for key, value in capital.items() if key != "pool_usage"
                },
                "pool_usage": capital["pool_usage"],
                "aggregate_performance": _aggregate_performance(slots),
                "session_stop": session_stop,
                "balances": balances,
                "ownership_contradictions": contradictions,
                "historical_executors": historical_executors,
                "warnings": warnings,
                "errors": errors,
                "health": "blocked" if blocked else "healthy",
                "mutations_allowed": not blocked,
            }
        )
    except Exception as error:
        debug.update(
            {
                "exception_type": type(error).__name__,
                "exception": str(error),
                "traceback": traceback.format_exc(),
            }
        )
        result = {
            "controller_id": config.controller_id,
            "observed_at": observed_at,
            "slots": [],
            "balances": [],
            "ownership_contradictions": [],
            "errors": [_error(error)],
            "health": "error",
            "mutations_allowed": False,
        }
        if fallback_session_stop is not None:
            result["session_stop"] = fallback_session_stop
        return _compact(result)


async def run(config: Config, context: Any):
    started_at = datetime.now(timezone.utc).isoformat()
    started_monotonic = time.monotonic()
    debug: dict[str, Any] = {}
    try:
        if config.controller_id is None:
            output = _reporting.sample_payload("state", config, ["controller_id"])
        else:
            output = await _shared.wait_bounded(
                _execute(config, context, debug), STATE_TIMEOUT_SECONDS
            )
    except asyncio.TimeoutError:
        debug.update(
            {
                "exception_type": "TimeoutError",
                "exception": (
                    f"state inspection exceeded {STATE_TIMEOUT_SECONDS} seconds"
                ),
            }
        )
        output = _compact(
            {
                "controller_id": config.controller_id,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "slots": [],
                "balances": [],
                "ownership_contradictions": [],
                "errors": [
                    f"TimeoutError: state inspection exceeded "
                    f"{STATE_TIMEOUT_SECONDS} seconds"
                ],
                "health": "error",
                "mutations_allowed": False,
            }
        )
    except asyncio.CancelledError:
        await _reporting.finish_cancelled(
            "state",
            config,
            started_at=started_at,
            started_monotonic=started_monotonic,
            debug=debug,
        )
        raise
    return await _reporting.finish(
        "state",
        config,
        output,
        started_at=started_at,
        started_monotonic=started_monotonic,
        debug=debug,
    )
