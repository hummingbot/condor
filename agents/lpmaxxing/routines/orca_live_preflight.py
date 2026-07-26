import asyncio
import fcntl
import hashlib
import json
import math
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from agents.lpmaxxing.routines import orca_pool_scan

try:
    from routines.base import RoutineResult
except ModuleNotFoundError:
    sys.path.append(str(PROJECT_ROOT))
    from routines.base import RoutineResult


CATEGORY = "Orca LP Agent"
CANONICAL_USDC_MINT = orca_pool_scan.CANONICAL_USDC_MINT
LIVE_PROFILES = orca_pool_scan.LIVE_PROFILES
ACTIVE_EXECUTOR_STATES = {
    "RUNNING",
    "ACTIVE",
    "OPEN",
    "OPENING",
    "CLOSING",
    "SHUTTING_DOWN",
    "SWAPPING",
}
TERMINAL_EXECUTOR_STATES = {
    "COMPLETE",
    "COMPLETED",
    "TERMINATED",
    "STOPPED",
    "CANCELED",
    "CANCELLED",
    "FAILED",
}
SESSION_SCHEMA_VERSION = 2


def _session_dir(controller_id: str) -> Path | None:
    from condor.agents.journal import resolve_agent_dirs

    session_dir, _ = resolve_agent_dirs(controller_id)
    return session_dir if session_dir is not None and session_dir.is_dir() else None


def _state_path(controller_id: str) -> Path:
    session_dir = _session_dir(controller_id)
    if session_dir is None:
        raise ValueError(f"no loop session found for controller_id '{controller_id}'")
    return session_dir / "orca_lifecycle.json"


def _session_config(controller_id: str) -> dict[str, Any]:
    session_dir = _session_dir(controller_id)
    if session_dir is None:
        return {}
    value = yaml.safe_load((session_dir / "config.yml").read_text()) or {}
    if not isinstance(value, dict):
        raise ValueError("current session config must be a YAML object")
    return value


def session_execution_mode(controller_id: str) -> str:
    session_config = _session_config(controller_id)
    if not session_config:
        return "dry_run"
    runtime_mode = _text(session_config.get("execution_mode")).lower()
    match = re.search(
        r"^\s*SESSION_MODE:\s*(dry_run|loop|run_once)\s*$",
        str(session_config.get("trading_context") or ""),
        re.MULTILINE | re.IGNORECASE,
    )
    if not match:
        raise ValueError("SESSION_MODE is missing from the current session context")
    requested_mode = match.group(1).lower()
    if "run_once" in {runtime_mode, requested_mode}:
        return "run_once"
    return "loop" if runtime_mode == requested_mode == "loop" else "dry_run"


def session_total_amount_quote(controller_id: str) -> float | None:
    session_config = _session_config(controller_id)
    return _number(session_config.get("total_amount_quote")) if session_config else None


def session_risk_profile(controller_id: str) -> str:
    session_config = _session_config(controller_id)
    profile = (
        _text(session_config.get("risk_profile"))
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )
    if profile not in LIVE_PROFILES:
        raise ValueError(
            "current session risk_profile must be one of "
            + ", ".join(sorted(LIVE_PROFILES))
        )
    return profile


def session_policies(controller_id: str) -> dict[str, float]:
    session_config = _session_config(controller_id)
    if not session_config:
        return {}
    context = str(session_config.get("trading_context") or "")
    fields = {
        "position_max_age_minutes": "POSITION_MAX_AGE_MINUTES",
        "position_take_profit_net_pnl_ratio": "POSITION_TAKE_PROFIT_NET_PNL_RATIO",
        "position_stop_loss_net_pnl_ratio": "POSITION_STOP_LOSS_NET_PNL_RATIO",
        "session_max_age_minutes": "SESSION_MAX_AGE_MINUTES",
        "session_take_profit_net_pnl_ratio": "SESSION_TAKE_PROFIT_NET_PNL_RATIO",
        "session_stop_loss_net_pnl_ratio": "SESSION_STOP_LOSS_NET_PNL_RATIO",
    }
    policy = {}
    for field, label in fields.items():
        match = re.search(
            rf"^\s*{label}:\s*([0-9]+(?:\.[0-9]+)?)\s*$",
            context,
            re.MULTILINE | re.IGNORECASE,
        )
        if not match:
            raise ValueError(f"{label} is missing from the current session context")
        value = _number(match.group(1))
        if value is None or value < 0:
            raise ValueError(f"{label} must be a non-negative number")
        policy[field] = value
    return policy


def session_exit_policy(controller_id: str) -> dict[str, float]:
    policy = session_policies(controller_id)
    return {key: value for key, value in policy.items() if key.startswith("position_")}


def _load_session_state(controller_id: str) -> dict[str, Any]:
    path = _state_path(controller_id)
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Orca lifecycle state must be a JSON object")
    if value.get("schema_version") != SESSION_SCHEMA_VERSION:
        raise ValueError("legacy Orca lifecycle state cannot be resumed")
    if value.get("controller_id") != controller_id:
        raise ValueError("Orca session-state controller mismatch")
    if not isinstance(value.get("terms"), dict):
        raise ValueError("Orca session-state terms are missing")
    if value.get("active_position") is not None and not isinstance(
        value.get("active_position"), dict
    ):
        raise ValueError("Orca active-position state must be an object or null")
    return value


def _save_session_state(controller_id: str, state: dict[str, Any]) -> None:
    path = _state_path(controller_id)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    value = {**state, "updated_at": _utc_now()}
    try:
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def ensure_session_state(controller_id: str) -> dict[str, Any]:
    existing = _load_session_state(controller_id)
    policy = session_policies(controller_id)
    expected_terms = {
        "total_amount_quote": session_total_amount_quote(controller_id),
        "risk_profile": session_risk_profile(controller_id),
        "frequency_sec": _number(_session_config(controller_id).get("frequency_sec")),
        **policy,
    }
    if expected_terms["total_amount_quote"] is None:
        raise ValueError("current session total_amount_quote is missing")
    if expected_terms["frequency_sec"] is None or expected_terms["frequency_sec"] <= 0:
        raise ValueError("current session frequency_sec must be positive")
    if existing:
        if existing.get("terms") != expected_terms:
            raise ValueError("current Orca session terms differ from persisted terms")
        return existing
    now = _utc_now()
    state = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "controller_id": controller_id,
        "session_status": "running",
        "session_started_at": now,
        "created_at": now,
        "updated_at": now,
        "terms": expected_terms,
        "completed": {
            "position_count": 0,
            "net_pnl_quote": 0.0,
            "executor_ids": [],
            "archive_files": [],
        },
        "active_position": None,
        "global_stop": None,
        "manual_review": None,
    }
    _save_session_state(controller_id, state)
    return state


def load_session_state(controller_id: str) -> dict[str, Any]:
    return ensure_session_state(controller_id)


def load_lifecycle_state(controller_id: str) -> dict[str, Any]:
    session_state = _load_session_state(controller_id)
    if not session_state:
        return {}
    return session_state.get("active_position") or {}


def save_lifecycle_state(controller_id: str, state: dict[str, Any]) -> None:
    session_state = ensure_session_state(controller_id)
    status = _text(session_state.get("session_status"))
    if status not in {"running", "stopping"}:
        raise ValueError(f"Orca session is not writable while {status or 'unknown'}")
    current = session_state.get("active_position") or {}
    next_state = dict(state)
    if next_state:
        if next_state.get("controller_id") != controller_id:
            raise ValueError("active-position controller mismatch")
        next_state.setdefault(
            "position_number",
            current.get("position_number")
            or int((session_state.get("completed") or {}).get("position_count") or 0)
            + 1,
        )
        next_state.setdefault(
            "position_started_at", current.get("position_started_at") or _utc_now()
        )
    _save_session_state(
        controller_id, {**session_state, "active_position": next_state or None}
    )


def clear_lifecycle_state(controller_id: str) -> None:
    session_state = ensure_session_state(controller_id)
    _save_session_state(controller_id, {**session_state, "active_position": None})


def session_stop_trigger(
    session_state: dict[str, Any],
    now: datetime,
    active_net_pnl_quote: float | None = 0.0,
) -> dict[str, Any] | None:
    terms = session_state.get("terms") or {}
    completed = session_state.get("completed") or {}
    started_at = _parse_timestamp(session_state.get("session_started_at"))
    budget = _number(terms.get("total_amount_quote"))
    completed_pnl = _number(completed.get("net_pnl_quote"))
    if started_at is None or budget is None or budget <= 0 or completed_pnl is None:
        raise ValueError("Orca session stop metrics are incomplete")
    age_minutes = max(0.0, (now - started_at).total_seconds() / 60.0)
    net_pnl_quote = (
        completed_pnl + active_net_pnl_quote
        if active_net_pnl_quote is not None
        else None
    )
    net_pnl_ratio = net_pnl_quote / budget if net_pnl_quote is not None else None
    observed = {
        "session_age_minutes": round(age_minutes, 6),
        "session_net_pnl_quote": (
            round(net_pnl_quote, 12) if net_pnl_quote is not None else None
        ),
        "session_net_pnl_ratio": (
            round(net_pnl_ratio, 12) if net_pnl_ratio is not None else None
        ),
        "completed_net_pnl_quote": completed_pnl,
        "active_net_pnl_quote": active_net_pnl_quote,
        "total_amount_quote": budget,
    }
    max_age = _number(terms.get("session_max_age_minutes"))
    take_profit = _number(terms.get("session_take_profit_net_pnl_ratio"))
    stop_loss = _number(terms.get("session_stop_loss_net_pnl_ratio"))
    if max_age is not None and age_minutes >= max_age:
        return {"reason": "session_max_age_reached", "observed": observed}
    if (
        take_profit is not None
        and net_pnl_ratio is not None
        and net_pnl_ratio >= take_profit
    ):
        return {"reason": "session_take_profit_reached", "observed": observed}
    if (
        stop_loss is not None
        and net_pnl_ratio is not None
        and net_pnl_ratio <= -abs(stop_loss)
    ):
        return {"reason": "session_stop_loss_reached", "observed": observed}
    return None


def mark_session_stop(
    controller_id: str,
    trigger: dict[str, Any],
    *,
    active_position: bool,
) -> dict[str, Any]:
    session_state = ensure_session_state(controller_id)
    existing = session_state.get("global_stop")
    global_stop = existing or {**trigger, "triggered_at": _utc_now()}
    next_state = {
        **session_state,
        "session_status": "stopping" if active_position else "stop_pending",
        "global_stop": global_stop,
    }
    _save_session_state(controller_id, next_state)
    return next_state


def resume_session_cycle(
    controller_id: str, now: datetime | None = None
) -> dict[str, Any]:
    session_state = ensure_session_state(controller_id)
    if session_state.get("session_status") != "cycle_complete":
        return session_state
    if session_state.get("active_position"):
        raise ValueError("completed Orca cycle still has an active position")
    resume_at = _parse_timestamp(session_state.get("cycle_resume_at"))
    current = now or datetime.now(timezone.utc)
    if resume_at is None or current < resume_at:
        return session_state
    next_state = {
        **session_state,
        "session_status": "running",
        "cycle_resume_at": None,
    }
    _save_session_state(controller_id, next_state)
    return next_state


def _archive_dir(controller_id: str) -> Path:
    session_dir = _session_dir(controller_id)
    if session_dir is None:
        raise ValueError(f"no loop session found for controller_id '{controller_id}'")
    return session_dir / "orca_positions"


def _audit_fingerprint(
    audit_payload: dict[str, Any],
    position_number: int,
    executor_id: str,
    net_pnl_quote: float | None,
) -> str:
    identity = audit_payload.get("identity") or {}
    lifecycle = audit_payload.get("lifecycle") or {}
    deposits = audit_payload.get("deposits") or {}
    performance = audit_payload.get("performance") or {}
    rebalance = audit_payload.get("rebalance") or {}
    critical = {
        "controller_id": identity.get("controller_id"),
        "executor_id": executor_id,
        "position_number": position_number,
        "pool_address": identity.get("pool_address"),
        "trading_pair": identity.get("trading_pair"),
        "preset": identity.get("preset"),
        "terminal_state": identity.get("terminal_state"),
        "close_reason": lifecycle.get("close_reason"),
        "planned_base": deposits.get("planned_base"),
        "planned_quote": deposits.get("planned_quote"),
        "net_pnl_quote": net_pnl_quote,
        "filled_amount_quote": performance.get("filled_amount_quote"),
        "reconciled_pnl_ratio": performance.get("reconciled_pnl_ratio"),
        "net_pnl_after_rebalance_quote": performance.get(
            "net_pnl_after_rebalance_quote"
        ),
        "rebalance_status": rebalance.get("status"),
        "rebalance_transaction_hash": rebalance.get("transaction_hash"),
        "rebalance_actual_quote_input": rebalance.get("actual_quote_input"),
        "rebalance_actual_base_output": rebalance.get("actual_base_output"),
    }
    encoded = json.dumps(critical, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_position_archive(
    controller_id: str, executor_id: str
) -> dict[str, Any] | None:
    wanted = _text(executor_id)
    if not wanted:
        return None
    for path in sorted(_archive_dir(controller_id).glob("position_*.json")):
        value = json.loads(path.read_text())
        if value.get("executor_id") != wanted:
            continue
        expected_fingerprint = _audit_fingerprint(
            value.get("audit") or {},
            int(value.get("position_number") or 0),
            wanted,
            _number(value.get("net_pnl_quote")),
        )
        if (
            value.get("schema_version") != SESSION_SCHEMA_VERSION
            or value.get("controller_id") != controller_id
            or value.get("commit_fingerprint") != expected_fingerprint
        ):
            raise ValueError("completed position archive is invalid")
        return value
    return None


def commit_position_audit(
    controller_id: str, audit_payload: dict[str, Any]
) -> dict[str, Any]:
    with lifecycle_lock(controller_id):
        session_state = ensure_session_state(controller_id)
        lifecycle = session_state.get("active_position") or {}
        actual_executor_id = _text(
            (audit_payload.get("identity") or {}).get("executor_id")
        )
        if not lifecycle:
            for path in sorted(_archive_dir(controller_id).glob("position_*.json")):
                existing_archive = json.loads(path.read_text())
                if existing_archive.get("executor_id") != actual_executor_id:
                    continue
                position_number = int(existing_archive.get("position_number") or 0)
                incoming_net_pnl = _number(
                    (audit_payload.get("performance") or {}).get(
                        "net_pnl_after_rebalance_quote"
                    )
                )
                archived_net_pnl = _number(existing_archive.get("net_pnl_quote"))
                if (
                    incoming_net_pnl is None
                    or archived_net_pnl is None
                    or not math.isclose(
                        incoming_net_pnl,
                        archived_net_pnl,
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    )
                ):
                    raise ValueError("completed position PnL conflicts with audit")
                expected_fingerprint = _audit_fingerprint(
                    audit_payload,
                    position_number,
                    actual_executor_id,
                    incoming_net_pnl,
                )
                if existing_archive.get("commit_fingerprint") != expected_fingerprint:
                    raise ValueError("completed position archive conflicts with audit")
                return {
                    "archive_path": str(path),
                    "session_status": session_state.get("session_status"),
                    "completed": session_state.get("completed") or {},
                    "global_stop": session_state.get("global_stop"),
                    "already_committed": True,
                }
            raise ValueError("active position is not terminal")
        if lifecycle.get("phase") != "terminal":
            raise ValueError("active position is not terminal")
        executor_id = _text(lifecycle.get("executor_id"))
        if not executor_id or executor_id != actual_executor_id:
            raise ValueError("terminal executor does not match active position")
        position_number = int(lifecycle.get("position_number") or 0)
        if position_number <= 0:
            raise ValueError("active position number is missing")
        net_pnl_quote = _number(
            (audit_payload.get("performance") or {}).get(
                "net_pnl_after_rebalance_quote"
            )
        )
        if net_pnl_quote is None:
            raise ValueError("terminal net PnL after rebalance is unavailable")
        commit_fingerprint = _audit_fingerprint(
            audit_payload, position_number, executor_id, net_pnl_quote
        )

        archive_dir = _archive_dir(controller_id)
        archive_dir.mkdir(exist_ok=True)
        archive_path = archive_dir / f"position_{position_number:06d}.json"
        archive = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "record_type": "orca_lp_position",
            "position_number": position_number,
            "controller_id": controller_id,
            "executor_id": executor_id,
            "archived_at": _utc_now(),
            "net_pnl_quote": net_pnl_quote,
            "commit_fingerprint": commit_fingerprint,
            "lifecycle": lifecycle,
            "audit": audit_payload,
        }
        if archive_path.exists():
            existing_archive = json.loads(archive_path.read_text())
            if (
                existing_archive.get("controller_id") != controller_id
                or existing_archive.get("executor_id") != executor_id
                or int(existing_archive.get("position_number") or 0) != position_number
                or existing_archive.get("commit_fingerprint") != commit_fingerprint
            ):
                raise ValueError("position archive conflicts with terminal audit")
        else:
            temporary = archive_path.with_name(
                f".{archive_path.name}.{uuid4().hex}.tmp"
            )
            try:
                temporary.write_text(
                    json.dumps(archive, indent=2, sort_keys=True) + "\n"
                )
                temporary.replace(archive_path)
            finally:
                if temporary.exists():
                    temporary.unlink()

        archives = []
        for path in sorted(archive_dir.glob("position_*.json")):
            value = json.loads(path.read_text())
            expected_fingerprint = _audit_fingerprint(
                value.get("audit") or {},
                int(value.get("position_number") or 0),
                _text(value.get("executor_id")),
                _number(value.get("net_pnl_quote")),
            )
            if (
                value.get("schema_version") != SESSION_SCHEMA_VERSION
                or value.get("controller_id") != controller_id
                or not _text(value.get("executor_id"))
                or _number(value.get("net_pnl_quote")) is None
                or value.get("commit_fingerprint") != expected_fingerprint
            ):
                raise ValueError(f"invalid Orca position archive: {path.name}")
            archives.append((path, value))
        completed_pnl = sum(float(value["net_pnl_quote"]) for _, value in archives)
        completed = {
            "position_count": len(archives),
            "net_pnl_quote": round(completed_pnl, 12),
            "executor_ids": [value["executor_id"] for _, value in archives],
            "archive_files": [path.name for path, _ in archives],
        }
        next_state = {
            **session_state,
            "completed": completed,
            "active_position": None,
        }
        terminal_state = _text(
            (audit_payload.get("identity") or {}).get("terminal_state")
        ).upper()
        if terminal_state == "FAILED":
            next_state["session_status"] = "manual_review"
            next_state["manual_review"] = {
                "reason": "executor_failed",
                "recorded_at": _utc_now(),
                "executor_id": executor_id,
            }
        else:
            trigger = next_state.get("global_stop") or session_stop_trigger(
                next_state, datetime.now(timezone.utc)
            )
            if trigger:
                next_state["session_status"] = "stop_pending"
                next_state["global_stop"] = (
                    trigger
                    if trigger.get("triggered_at")
                    else {**trigger, "triggered_at": _utc_now()}
                )
            else:
                next_state["session_status"] = "cycle_complete"
                frequency_sec = _number(
                    (next_state.get("terms") or {}).get("frequency_sec")
                )
                if frequency_sec is None or frequency_sec <= 0:
                    raise ValueError("Orca session frequency is invalid")
                next_state["cycle_resume_at"] = (
                    datetime.now(timezone.utc) + timedelta(seconds=frequency_sec)
                ).isoformat()
        _save_session_state(controller_id, next_state)
        return {
            "archive_path": str(archive_path),
            "session_status": next_state["session_status"],
            "completed": completed,
            "global_stop": next_state.get("global_stop"),
        }


@contextmanager
def lifecycle_lock(controller_id: str):
    path = _state_path(controller_id).with_suffix(".lock")
    with path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class Config(BaseModel):
    """Validate one Orca candidate and produce a read-only LP executor plan."""

    model_config = ConfigDict(extra="forbid")

    execution_mode: str = Field(default="dry_run", description="dry_run or loop")
    controller_id: str = Field(
        default="", description="Dynamic controller id from the current tick"
    )
    selected_candidate: dict[str, Any] = Field(
        default_factory=dict, description="Selected orca_pool_scan candidate"
    )
    gateway_pool_info: dict[str, Any] = Field(
        default_factory=dict,
        description="Normalized Gateway evidence: pool_address, base_mint, quote_mint, current_price",
    )
    wallet_balances: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Exact scoped balances with symbol and available fields",
    )
    active_executors: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Current executors for the dynamic controller",
    )
    executor_lookup_succeeded: bool = Field(
        default=False,
        description="Whether controller executor lookup completed successfully",
    )
    wallet_lookup_succeeded: bool = Field(
        default=False,
        description="Whether scoped wallet balance lookup completed successfully",
    )
    fetch_wallet_balances: bool = Field(
        default=True,
        description="Fetch exact scoped balances directly from the Hummingbot API",
    )
    wallet_account_name: str = Field(
        default="master_account", description="Account used for the balance lookup"
    )
    wallet_connector_name: str = Field(
        default="solana-mainnet-beta",
        description="Network connector used for the balance lookup",
    )
    api_errors: list[str] = Field(
        default_factory=list, description="Sanitized executor or wallet lookup errors"
    )
    total_amount_quote: float = Field(
        default=10, gt=0, description="Requested LP budget"
    )
    min_sol_fee_buffer: float = Field(
        default=0.05, ge=0, description="SOL retained for fees and rent"
    )
    max_price_deviation_ratio: float = Field(
        default=0.01, ge=0, le=1, description="Maximum Gateway/scanner price deviation"
    )
    connector_name: str = Field(
        default="solana-mainnet-beta", description="LP executor connector"
    )
    lp_provider: str = Field(default="orca/clmm", description="LP executor provider")

    @field_validator("execution_mode", mode="before")
    @classmethod
    def _mode(cls, value: Any) -> str:
        return str(value or "").strip().lower().replace("-", "_")

    @field_validator("controller_id", mode="before")
    @classmethod
    def _controller(cls, value: Any) -> str:
        return str(value or "").strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _strict_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _strict_integer(value: Any) -> int | None:
    number = _strict_number(value)
    return int(number) if number is not None and number.is_integer() else None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def candidate_age_seconds(
    candidate: dict[str, Any], now: datetime | None = None
) -> float | None:
    observed_at = _parse_timestamp(candidate.get("observed_at"))
    if observed_at is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("current time must be timezone-aware")
    return (current.astimezone(timezone.utc) - observed_at).total_seconds()


def candidate_is_fresh(candidate: dict[str, Any], now: datetime | None = None) -> bool:
    age = candidate_age_seconds(candidate, now)
    return age is not None and -30.0 <= age <= 600.0


def _numbers_match(actual: Any, expected: Any) -> bool:
    if expected is None:
        return actual is None
    actual_number = _strict_number(actual)
    expected_number = _strict_number(expected)
    return (
        actual_number is not None
        and expected_number is not None
        and math.isclose(actual_number, expected_number, rel_tol=1e-9, abs_tol=1e-12)
    )


def _values_match(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _values_match(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and actual == expected
    if isinstance(expected, bool) or expected is None or isinstance(expected, str):
        return actual == expected
    return _numbers_match(actual, expected)


def _contains_executable_bounds(value: Any) -> bool:
    forbidden = {
        "lower_price",
        "upper_price",
        "lower_limit_price",
        "upper_limit_price",
    }
    if isinstance(value, dict):
        return bool(forbidden.intersection(value)) or any(
            _contains_executable_bounds(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_executable_bounds(item) for item in value)
    return False


def _candidate_revalidation(
    candidate: dict[str, Any], budget: float, risk_profile: str
) -> tuple[bool, dict[str, Any], dict[str, Any] | None]:
    errors: list[str] = []
    if risk_profile not in LIVE_PROFILES:
        return False, {"errors": ["invalid live risk profile"]}, None
    if _contains_executable_bounds(candidate):
        errors.append("scanner candidate contains executable bounds")

    token_a = (
        candidate.get("token_a") if isinstance(candidate.get("token_a"), dict) else {}
    )
    token_b = (
        candidate.get("token_b") if isinstance(candidate.get("token_b"), dict) else {}
    )
    normalized = {
        "malformed": False,
        "address": _text(candidate.get("pool_address")),
        "token_a": {
            "symbol": _text(token_a.get("symbol")),
            "mint": _text(token_a.get("mint")),
            "decimals": _strict_integer(token_a.get("decimals")),
        },
        "token_b": {
            "symbol": _text(token_b.get("symbol")),
            "mint": _text(token_b.get("mint")),
            "decimals": _strict_integer(token_b.get("decimals")),
        },
        "scanner_price": _strict_number(candidate.get("scanner_price")),
        "tvl_usd": _strict_number(candidate.get("tvl_usd")),
        "net_price_change_24h": orca_pool_scan._decimal_ratio(
            candidate.get("net_price_change_24h")
        ),
        "price_delta_24h_raw": candidate.get("net_price_change_24h"),
        "fee_rate_raw": _strict_number(
            candidate.get("fee_rate_raw", candidate.get("fee_rate"))
        ),
        "adaptive_fee_enabled": (
            candidate.get("adaptive_fee_enabled")
            if isinstance(candidate.get("adaptive_fee_enabled"), bool)
            else None
        ),
        "fee_tier_index": _strict_integer(candidate.get("fee_tier_index")),
        "tick_spacing": _strict_integer(candidate.get("tick_spacing")),
        "has_warning": (
            candidate.get("has_warning")
            if isinstance(candidate.get("has_warning"), bool)
            else None
        ),
        "source_categories": candidate.get("source_categories"),
        "source_lenses": candidate.get("source_lenses"),
    }
    for window in orca_pool_scan.LIVE_STATS:
        normalized[f"volume_{window}_usd"] = _strict_number(
            candidate.get(f"volume_{window}_usd")
        )
        normalized[f"fees_{window}_usd"] = _strict_number(
            candidate.get(f"fees_{window}_usd")
        )

    policy = orca_pool_scan.PROFILE_POLICY[risk_profile]
    orca_pool_scan._derive(normalized, budget)
    gate_reason = orca_pool_scan._gate(normalized, policy, True, set())
    if gate_reason:
        errors.append(f"candidate hard gate failed: {gate_reason}")

    categories = candidate.get("source_categories")
    lenses = candidate.get("source_lenses")
    if (
        not isinstance(categories, list)
        or not categories
        or categories != sorted(set(categories))
        or not set(categories).issubset(policy["categories"])
    ):
        errors.append("candidate lacks allowed Orca category evidence")
    if (
        not isinstance(lenses, list)
        or not lenses
        or lenses != sorted(set(lenses))
        or not set(lenses).issubset(orca_pool_scan.DISCOVERY_LENSES)
    ):
        errors.append("candidate lacks Orca discovery-lens evidence")

    expected_range = None
    if not gate_reason:
        orca_pool_scan._score(normalized, policy)
        expected_range, range_reason = orca_pool_scan._range_plan(
            normalized, risk_profile
        )
        if range_reason:
            errors.append(f"candidate range is infeasible: {range_reason}")
        normalized["range_plan"] = expected_range

    maximum = policy["max_abs_net_price_change_24h"]
    expected_fields = {
        "risk_profile": risk_profile,
        "trading_pair": normalized.get("trading_pair"),
        "price_orientation": "token_b_per_token_a",
        "fee_tvl_24h": normalized.get("fee_tvl_24h"),
        "fee_tvl_7d_daily": normalized.get("fee_tvl_7d_daily"),
        "fee_productivity_1h_dailyized_bps": (
            normalized.get("fee_productivity_1h") * 10_000.0
            if normalized.get("fee_productivity_1h") is not None
            else None
        ),
        "fee_productivity_4h_dailyized_bps": (
            normalized.get("fee_productivity_4h") * 10_000.0
            if normalized.get("fee_productivity_4h") is not None
            else None
        ),
        "fee_productivity_24h_bps_per_day": (
            normalized.get("fee_productivity_24h") * 10_000.0
            if normalized.get("fee_productivity_24h") is not None
            else None
        ),
        "fee_productivity_7d_daily_bps": (
            normalized.get("fee_productivity_7d") * 10_000.0
            if normalized.get("fee_productivity_7d") is not None
            else None
        ),
        "sustained_fee_productivity": normalized.get("sustained_fee_productivity"),
        "sustained_fee_productivity_bps_per_day": (
            normalized.get("sustained_fee_productivity") * 10_000.0
            if normalized.get("sustained_fee_productivity") is not None
            else None
        ),
        "fee_momentum_1h_x": normalized.get("fee_momentum_1h_x"),
        "fee_momentum_4h_x": normalized.get("fee_momentum_4h_x"),
        "volume_tvl_24h": normalized.get("volume_tvl_24h"),
        "volume_tvl_7d_daily": normalized.get("volume_tvl_7d_daily"),
        "net_price_change_24h_pct": (
            normalized.get("net_price_change_24h") * 100.0
            if normalized.get("net_price_change_24h") is not None
            else None
        ),
        "profile_net_change_limit_pct": (
            maximum * 100.0 if maximum is not None else None
        ),
        "profile_net_change_limit_enabled": maximum is not None,
        "fee_rate_fraction": (
            normalized.get("fee_rate_raw") / 1_000_000.0
            if normalized.get("fee_rate_raw") is not None
            else None
        ),
        "fee_rate_bps": (
            normalized.get("fee_rate_raw") / 100.0
            if normalized.get("fee_rate_raw") is not None
            else None
        ),
        "gross_fee_estimate_quote_per_day": normalized.get(
            "gross_fee_estimate_quote_per_day"
        ),
        "session_budget_quote": budget,
        "total_amount_quote": budget,
    }
    if not gate_reason:
        expected_fields.update(
            {
                "criteria_raw": normalized.get("criteria_raw"),
                "criteria_scores": normalized.get("criteria_scores"),
                "mcda_weights": orca_pool_scan.MCDA_WEIGHTS,
                "weighted_score": normalized.get("weighted_score"),
                "score": normalized.get("weighted_score"),
                "range_plan": expected_range,
                "preset_suggestion": (expected_range or {}).get("preset"),
            }
        )
    for field, expected in expected_fields.items():
        if field not in candidate or not _values_match(candidate.get(field), expected):
            errors.append(f"candidate field '{field}' does not match recomputed policy")

    return (
        not errors,
        {
            "passed": not errors,
            "errors": errors,
            "recomputed": expected_fields,
        },
        normalized if not errors else None,
    )


def _executable_range(
    center_price: float, half_width: float
) -> dict[str, float] | None:
    if center_price <= 0 or not 0 < half_width < 1:
        return None
    lower = center_price * (1.0 - half_width)
    upper = center_price * (1.0 + half_width)
    limit_buffer = min(0.015, max(0.003, half_width * 0.5))
    lower_limit = lower * (1.0 - limit_buffer)
    upper_limit = upper * (1.0 + limit_buffer)
    values = (lower_limit, lower, center_price, upper, upper_limit)
    if not all(math.isfinite(value) and value > 0 for value in values):
        return None
    if not lower_limit < lower < center_price < upper < upper_limit:
        return None
    return {
        "center_price": center_price,
        "provisional_half_width": half_width,
        "limit_buffer": limit_buffer,
        "lower_price": lower,
        "upper_price": upper,
        "lower_limit_price": lower_limit,
        "upper_limit_price": upper_limit,
    }


def _nested_value(value: Any, paths: list[str]) -> Any:
    for path in paths:
        current = value
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                break
            current = current[part]
        else:
            if current is not None:
                return current
    return None


def _normalize_gateway_pool_info(
    value: Any, expected_pool_address: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Gateway pool info must be an object")
    if isinstance(value.get("result"), dict):
        value = value["result"]
    pool_address = _text(
        _nested_value(value, ["pool_address", "poolAddress", "address"])
    )
    base_mint = _text(
        _nested_value(
            value,
            [
                "base_mint",
                "baseMint",
                "base_token_address",
                "token_a.mint",
                "token_a.address",
                "tokenA.mint",
                "tokenA.address",
                "tokenMintA.mint",
                "tokenMintA.address",
                "tokenMintA",
            ],
        )
    )
    quote_mint = _text(
        _nested_value(
            value,
            [
                "quote_mint",
                "quoteMint",
                "quote_token_address",
                "token_b.mint",
                "token_b.address",
                "tokenB.mint",
                "tokenB.address",
                "tokenMintB.mint",
                "tokenMintB.address",
                "tokenMintB",
            ],
        )
    )
    current_price = _number(
        _nested_value(value, ["current_price", "currentPrice", "price"])
    )
    if (
        not pool_address
        or pool_address != expected_pool_address
        or not base_mint
        or not quote_mint
        or current_price is None
    ):
        raise ValueError("Gateway pool info is missing identity or current price")
    return {
        "pool_address": pool_address,
        "base_mint": base_mint,
        "quote_mint": quote_mint,
        "current_price": current_price,
        "observed_at": _utc_now(),
    }


def _executor_rows(value: Any) -> list[dict[str, Any]]:
    rows = value
    if isinstance(value, dict):
        rows = next(
            (
                value[key]
                for key in ("executors", "data", "results", "items")
                if isinstance(value.get(key), list)
            ),
            None,
        )
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("executor search response is not a recognized list")
    return rows


async def _search_controller_executors(
    client: Any, controller_id: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for _ in range(200):
        params: dict[str, Any] = {
            "controller_ids": [controller_id],
            "limit": 100,
        }
        if cursor:
            params["cursor"] = cursor
        result = await client.executors.search_executors(**params)
        page = _executor_rows(result)
        rows.extend(page)
        next_cursor = None
        if isinstance(result, dict):
            next_cursor = result.get("next_cursor") or result.get("cursor")
            pagination = result.get("pagination")
            if not next_cursor and isinstance(pagination, dict):
                next_cursor = pagination.get("next_cursor") or pagination.get("cursor")
        if not next_cursor:
            return rows
        next_cursor = str(next_cursor)
        if next_cursor in seen_cursors:
            raise ValueError("executor pagination cursor repeated")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise ValueError("executor pagination exceeded safety limit")


def _executor_controller_id(executor: dict[str, Any]) -> str:
    executor_config = (
        executor.get("config") if isinstance(executor.get("config"), dict) else {}
    )
    return _text(executor.get("controller_id") or executor_config.get("controller_id"))


def _executor_id(executor: dict[str, Any]) -> str:
    executor_config = (
        executor.get("config") if isinstance(executor.get("config"), dict) else {}
    )
    return _text(
        executor.get("executor_id") or executor.get("id") or executor_config.get("id")
    )


def _candidate_tokens(
    candidate: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    token_a = (
        candidate.get("token_a") if isinstance(candidate.get("token_a"), dict) else {}
    )
    token_b = (
        candidate.get("token_b") if isinstance(candidate.get("token_b"), dict) else {}
    )
    return token_a, token_b


def _candidate_identity(candidate: dict[str, Any]) -> dict[str, Any]:
    token_a, token_b = _candidate_tokens(candidate)
    range_plan = (
        candidate.get("range_plan")
        if isinstance(candidate.get("range_plan"), dict)
        else {}
    )
    return {
        "observed_at": _text(candidate.get("observed_at")),
        "risk_profile": _text(candidate.get("risk_profile")),
        "pool_address": _text(candidate.get("pool_address")),
        "price_orientation": _text(candidate.get("price_orientation")),
        "token_a_mint": _text(token_a.get("mint")),
        "token_b_mint": _text(token_b.get("mint")),
        "preset": _text(range_plan.get("preset")),
        "provisional_half_width": _number(range_plan.get("provisional_half_width")),
    }


def _token_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        rows = value.get("tokens")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _token_address(token: dict[str, Any]) -> str:
    return _text(
        token.get("address") or token.get("token_address") or token.get("mint")
    )


async def ensure_gateway_tokens(
    client: Any,
    network_id: str,
    candidate: dict[str, Any],
    gateway_pool_info: dict[str, Any],
) -> list[dict[str, Any]]:
    identity = _candidate_identity(candidate)
    if (
        not identity["pool_address"]
        or identity["pool_address"] != _text(gateway_pool_info.get("pool_address"))
        or identity["token_a_mint"] != _text(gateway_pool_info.get("base_mint"))
        or identity["token_b_mint"] != _text(gateway_pool_info.get("quote_mint"))
    ):
        raise ValueError("candidate and Gateway token identities do not match")
    if not hasattr(client, "gateway"):
        raise RuntimeError("Gateway configuration API is unavailable")

    metadata = []
    for key in ("token_a", "token_b"):
        token = candidate.get(key) if isinstance(candidate.get(key), dict) else {}
        address = _text(token.get("mint"))
        symbol = _text(token.get("symbol"))
        decimals_value = token.get("decimals")
        if not address or not symbol or isinstance(decimals_value, bool):
            raise ValueError(f"{key} metadata is incomplete")
        try:
            numeric_decimals = float(decimals_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} decimals are invalid") from exc
        if not numeric_decimals.is_integer() or not 0 <= numeric_decimals <= 18:
            raise ValueError(f"{key} decimals are invalid")
        metadata.append((address, symbol, int(numeric_decimals)))

    rows = _token_rows(await client.gateway.get_network_tokens(network_id))
    missing = []
    for address, symbol, decimals in metadata:
        exact = next((row for row in rows if _token_address(row) == address), None)
        if exact is None:
            collision = next(
                (
                    row
                    for row in rows
                    if _text(row.get("symbol")).upper() == symbol.upper()
                    and _token_address(row) != address
                ),
                None,
            )
            if collision is not None:
                raise ValueError(f"Gateway token symbol collision for {symbol}")
            missing.append((address, symbol, decimals))
            continue
        observed_symbol = _text(exact.get("symbol"))
        observed_decimals = exact.get("decimals")
        try:
            observed_decimals = int(observed_decimals)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Gateway token metadata mismatch for {symbol}") from exc
        if observed_symbol.upper() != symbol.upper() or observed_decimals != decimals:
            raise ValueError(f"Gateway token metadata mismatch for {symbol}")

    for address, symbol, decimals in missing:
        await client.gateway.add_token(
            network_id=network_id,
            address=address,
            symbol=symbol,
            decimals=decimals,
            name=symbol,
        )

    if missing:
        rows = _token_rows(await client.gateway.get_network_tokens(network_id))

    evidence = []
    for address, symbol, decimals in metadata:
        exact = next((row for row in rows if _token_address(row) == address), None)
        if exact is None:
            raise ValueError(f"Gateway token registration did not expose {symbol}")
        observed_symbol = _text(exact.get("symbol"))
        observed_decimals = exact.get("decimals")
        try:
            observed_decimals = int(observed_decimals)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Gateway token metadata mismatch for {symbol}") from exc
        if observed_symbol.upper() != symbol.upper() or observed_decimals != decimals:
            raise ValueError(f"Gateway token metadata mismatch for {symbol}")
        evidence.append(
            {
                "symbol": symbol,
                "mint": address,
                "decimals": decimals,
                "registered": True,
            }
        )
    return evidence


def _balance(balances: list[dict[str, Any]], token: dict[str, Any]) -> float | None:
    wanted_symbol = _text(token.get("symbol")).upper()
    wanted_mint = _text(token.get("mint"))
    mint_metadata_present = any(_text(balance.get("mint")) for balance in balances)
    symbol_matches = []
    for balance in balances:
        value = (
            balance.get("available")
            if "available" in balance
            else balance.get("available_units")
        )
        available = _number(value)
        if available is None:
            continue
        balance_mint = _text(balance.get("mint") or balance.get("address"))
        if wanted_mint and balance_mint == wanted_mint:
            return available
        if _text(balance.get("symbol")).upper() == wanted_symbol:
            symbol_matches.append(available)
    if wanted_mint and mint_metadata_present:
        return None
    return symbol_matches[0] if len(symbol_matches) == 1 else None


def _inventory_plan(
    budget: float,
    price: float | None,
    lower: float | None,
    upper: float | None,
    base_decimals: int | None,
    quote_decimals: int | None,
) -> dict[str, float] | None:
    if (
        price is None
        or lower is None
        or upper is None
        or base_decimals is None
        or quote_decimals is None
        or not 0 <= base_decimals <= 18
        or not 0 <= quote_decimals <= 18
        or not lower < price < upper
    ):
        return None
    sqrt_p = math.sqrt(price)
    sqrt_a = math.sqrt(lower)
    sqrt_b = math.sqrt(upper)
    base_per_l = (sqrt_b - sqrt_p) / (sqrt_p * sqrt_b)
    quote_per_l = sqrt_p - sqrt_a
    denominator = base_per_l * price + quote_per_l
    if denominator <= 0:
        return None
    liquidity = budget / denominator
    base_amount = Decimal(str(base_per_l * liquidity)).quantize(
        Decimal(1).scaleb(-base_decimals), rounding=ROUND_DOWN
    )
    quote_amount = Decimal(str(quote_per_l * liquidity)).quantize(
        Decimal(1).scaleb(-quote_decimals), rounding=ROUND_DOWN
    )
    if base_amount <= 0 or quote_amount <= 0:
        return None
    return {
        "base_amount": float(base_amount),
        "quote_amount": float(quote_amount),
    }


def _check(name: str, passed: bool, observed: Any, required: Any) -> dict[str, Any]:
    return {"check": name, "passed": passed, "observed": observed, "required": required}


def _evaluate(
    config: Config,
    require_session: bool = False,
    now: datetime | None = None,
    existing_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate = config.selected_candidate
    gateway = config.gateway_pool_info
    base_token, quote_token = _candidate_tokens(candidate)
    checks: list[dict[str, Any]] = []
    now = now or datetime.now(timezone.utc)
    existing_state = existing_state or {}

    mode_ok = config.execution_mode in {"dry_run", "loop"}
    expected_mode = None
    try:
        expected_mode = session_execution_mode(config.controller_id)
    except Exception:
        expected_mode = None if require_session else config.execution_mode
    mode_ok = mode_ok and config.execution_mode == expected_mode
    controller_ok = (
        bool(config.controller_id) and config.controller_id != "lpmaxxing_orca"
    )
    checks.append(
        _check(
            "runtime_identity",
            mode_ok and controller_ok,
            {
                "mode": config.execution_mode,
                "session_mode": expected_mode,
                "controller_id": config.controller_id,
            },
            "mode matching SESSION_MODE (dry_run or loop) and dynamic controller id",
        )
    )

    has_session = _session_dir(config.controller_id) is not None
    active_session_profile = None
    try:
        active_session_profile = session_risk_profile(config.controller_id)
    except Exception:
        if require_session and config.execution_mode == "loop":
            active_session_profile = None
    candidate_profile = _text(candidate.get("risk_profile"))
    expected_profile = active_session_profile or candidate_profile
    profile_ok = (
        candidate_profile in LIVE_PROFILES
        and expected_profile == candidate_profile
        and (config.execution_mode != "loop" or active_session_profile is not None)
    )
    checks.append(
        _check(
            "session_risk_profile",
            profile_ok,
            {
                "candidate": candidate_profile,
                "active_session": active_session_profile,
            },
            "candidate profile matching the current structured live risk_profile",
        )
    )

    scan_age = candidate_age_seconds(candidate, now)
    freshness_ok = scan_age is not None and -30.0 <= scan_age <= 600.0
    checks.append(
        _check(
            "scan_freshness",
            freshness_ok,
            scan_age,
            "-30 <= age_seconds <= 600",
        )
    )

    candidate_valid, candidate_revalidation, recomputed_candidate = (
        _candidate_revalidation(candidate, config.total_amount_quote, candidate_profile)
        if candidate_profile in LIVE_PROFILES
        else (False, {"errors": ["invalid live risk profile"]}, None)
    )
    checks.append(
        _check(
            "scanner_candidate_contract",
            candidate_valid,
            candidate_revalidation,
            "complete untampered scanner evidence and deterministic policy arithmetic",
        )
    )

    token_a_decimals = _strict_integer(base_token.get("decimals"))
    token_b_decimals = _strict_integer(quote_token.get("decimals"))
    canonical_quote_ok = (
        _text(quote_token.get("mint")) == CANONICAL_USDC_MINT
        and _text(quote_token.get("symbol")).upper() == "USDC"
        and token_b_decimals == 6
        and bool(_text(base_token.get("mint")))
        and _text(base_token.get("mint")) != _text(quote_token.get("mint"))
        and token_a_decimals is not None
        and candidate.get("price_orientation") == "token_b_per_token_a"
    )
    checks.append(
        _check(
            "canonical_quote_orientation",
            canonical_quote_ok,
            {
                "token_a_mint": _text(base_token.get("mint")),
                "token_b_mint": _text(quote_token.get("mint")),
                "token_b_symbol": _text(quote_token.get("symbol")),
                "token_b_decimals": token_b_decimals,
                "price_orientation": candidate.get("price_orientation"),
            },
            "canonical USDC token B with token_b_per_token_a orientation",
        )
    )

    controller_executors = [
        executor
        for executor in config.active_executors
        if _text(executor.get("controller_id")) == config.controller_id
    ]
    unresolved_executor_records = sum(
        1
        for executor in config.active_executors
        if _text(executor.get("controller_id")) != config.controller_id
        or not _text(executor.get("status"))
    )
    executor_states = [
        _text(item.get("status")).upper() for item in controller_executors
    ]
    executor_evidence_ok = (
        config.executor_lookup_succeeded
        and not config.active_executors
        and unresolved_executor_records == 0
        and all(executor_states)
    )
    checks.append(
        _check(
            "single_executor",
            executor_evidence_ok,
            {
                "lookup_succeeded": config.executor_lookup_succeeded,
                "matching_executors": len(controller_executors),
                "unresolved_records": unresolved_executor_records,
                "states": executor_states,
            },
            "successful controller lookup with zero executors",
        )
    )

    candidate_pool = _text(candidate.get("pool_address"))
    gateway_pool = _text(gateway.get("pool_address"))
    candidate_base_mint = _text(base_token.get("mint"))
    candidate_quote_mint = _text(quote_token.get("mint"))
    gateway_base_mint = _text(gateway.get("base_mint"))
    gateway_quote_mint = _text(gateway.get("quote_mint"))
    scan_price = _number(candidate.get("scanner_price"))
    gateway_price = _number(gateway.get("current_price"))
    price_deviation = (
        abs(gateway_price - scan_price) / scan_price
        if gateway_price is not None and scan_price is not None and scan_price > 0
        else None
    )
    pool_ok = bool(candidate_pool) and candidate_pool == gateway_pool
    mints_ok = (
        bool(candidate_base_mint)
        and candidate_base_mint == gateway_base_mint
        and bool(candidate_quote_mint)
        and candidate_quote_mint == gateway_quote_mint
    )
    price_ok = (
        price_deviation is not None
        and price_deviation <= config.max_price_deviation_ratio
    )
    checks.append(
        _check(
            "gateway_pool",
            pool_ok and mints_ok and price_ok,
            {
                "pool_address": gateway_pool,
                "base_mint": gateway_base_mint,
                "quote_mint": gateway_quote_mint,
                "current_price": gateway_price,
                "price_deviation_ratio": price_deviation,
            },
            {
                "pool_address": candidate_pool,
                "base_mint": candidate_base_mint,
                "quote_mint": candidate_quote_mint,
                "max_price_deviation_ratio": config.max_price_deviation_ratio,
            },
        )
    )

    range_plan = (
        recomputed_candidate.get("range_plan")
        if isinstance(recomputed_candidate, dict)
        and isinstance(recomputed_candidate.get("range_plan"), dict)
        else {}
    )
    provisional_half_width = _number(range_plan.get("provisional_half_width"))
    executable_range = (
        _executable_range(gateway_price, provisional_half_width)
        if gateway_price is not None and provisional_half_width is not None
        else None
    )
    checks.append(
        _check(
            "gateway_centered_range",
            executable_range is not None,
            executable_range,
            "positive finite requested bounds centered on the fresh Gateway price",
        )
    )

    try:
        active_session_budget = session_total_amount_quote(config.controller_id)
    except Exception:
        active_session_budget = None
    budget_ok = config.total_amount_quote > 0 and (
        (not has_session and active_session_budget is None)
        or config.total_amount_quote == active_session_budget
    )
    checks.append(
        _check(
            "session_budget",
            budget_ok,
            {
                "requested": config.total_amount_quote,
                "active_session": active_session_budget,
            },
            "positive total_amount_quote matching the active session",
        )
    )

    inventory = _inventory_plan(
        config.total_amount_quote,
        gateway_price,
        executable_range.get("lower_price") if executable_range else None,
        executable_range.get("upper_price") if executable_range else None,
        token_a_decimals,
        token_b_decimals,
    )
    available_base = _balance(config.wallet_balances, base_token)
    available_quote = _balance(config.wallet_balances, quote_token)
    available_sol = _balance(config.wallet_balances, {"symbol": "SOL"})
    required_base = inventory.get("base_amount") if inventory else None
    required_quote = inventory.get("quote_amount") if inventory else None
    sol_spend = 0.0
    if _text(base_token.get("symbol")).upper() == "SOL" and required_base is not None:
        sol_spend += required_base
    if _text(quote_token.get("symbol")).upper() == "SOL" and required_quote is not None:
        sol_spend += required_quote
    inventory_ok = (
        config.wallet_lookup_succeeded
        and bool(config.wallet_account_name)
        and config.wallet_connector_name == config.connector_name
        and inventory is not None
        and available_base is not None
        and required_base is not None
        and available_base >= required_base
        and available_quote is not None
        and required_quote is not None
        and available_quote >= required_quote
        and available_sol is not None
        and available_sol - sol_spend >= config.min_sol_fee_buffer
    )
    checks.append(
        _check(
            "wallet_inventory",
            inventory_ok,
            {
                "lookup_succeeded": config.wallet_lookup_succeeded,
                "account": config.wallet_account_name,
                "connector": config.wallet_connector_name,
                "base": available_base,
                "quote": available_quote,
                "sol": available_sol,
            },
            {
                "base": required_base,
                "quote": required_quote,
                "sol_remaining": config.min_sol_fee_buffer,
            },
        )
    )

    failed_checks = [item["check"] for item in checks if not item["passed"]]
    ready = not failed_checks
    executor_plan = None
    if ready and inventory and executable_range:
        executor_plan = {
            "action": "create",
            "executor_type": "lp_executor",
            "executor_config": {
                "controller_id": config.controller_id,
                "connector_name": config.connector_name,
                "lp_provider": config.lp_provider,
                "trading_pair": candidate.get("trading_pair"),
                "pool_address": candidate_pool,
                "lower_price": executable_range["lower_price"],
                "upper_price": executable_range["upper_price"],
                "lower_limit_price": executable_range["lower_limit_price"],
                "upper_limit_price": executable_range["upper_limit_price"],
                "side": 3,
                "base_amount": inventory["base_amount"],
                "quote_amount": inventory["quote_amount"],
                "total_amount_quote": config.total_amount_quote,
                "keep_position": False,
            },
        }

    available_base_for_plan = available_base or 0.0
    base_shortfall = (
        max(0.0, required_base - available_base_for_plan)
        if required_base is not None
        else None
    )
    estimated_quote_spend = (
        base_shortfall * gateway_price
        if base_shortfall is not None and gateway_price is not None
        else None
    )
    quote_symbol = _text(quote_token.get("symbol")).upper()
    quote_buffer = 0.01
    quote_required_after_swap = (
        required_quote + quote_buffer if required_quote is not None else None
    )
    quote_funds_ok = (
        available_quote is not None
        and estimated_quote_spend is not None
        and quote_required_after_swap is not None
        and available_quote >= estimated_quote_spend + quote_required_after_swap
    )
    sol_funds_ok = (
        available_sol is not None and available_sol >= config.min_sol_fee_buffer
    )
    if quote_symbol == "SOL" and quote_funds_ok:
        sol_funds_ok = (
            available_sol is not None
            and estimated_quote_spend is not None
            and required_quote is not None
            and available_sol - estimated_quote_spend - required_quote
            >= config.min_sol_fee_buffer
        )
    rebalance_status = _text(
        (existing_state.get("rebalance") or {}).get("status")
    ).upper()
    rebalance_consumed = rebalance_status in {"CONFIRMED", "NOT_REQUIRED"} or _text(
        existing_state.get("phase")
    ) in {"rebalance_confirmed", "rebalance_confirmed_waiting_balance"}
    rebalance_eligible = (
        set(failed_checks) == {"wallet_inventory"}
        and not rebalance_consumed
        and not config.api_errors
        and config.wallet_lookup_succeeded
        and inventory is not None
        and base_shortfall is not None
        and base_shortfall > 1e-12
        and quote_funds_ok
        and sol_funds_ok
    )
    rebalance_plan = None
    if rebalance_eligible:
        rebalance_plan = {
            "trading_pair": candidate.get("trading_pair"),
            "pool_address": candidate_pool,
            "base_mint": candidate_base_mint,
            "quote_mint": candidate_quote_mint,
            "base_symbol": base_token.get("symbol"),
            "quote_symbol": quote_token.get("symbol"),
            "base_decimals": base_token.get("decimals"),
            "quote_decimals": quote_token.get("decimals"),
            "base_shortfall": round(base_shortfall, 8),
            "required_base": required_base,
            "required_quote": required_quote,
            "estimated_quote_spend": round(estimated_quote_spend or 0.0, 8),
            "available_base": available_base_for_plan,
            "available_quote": available_quote,
            "available_sol": available_sol,
            "quote_buffer": quote_buffer,
            "min_sol_fee_buffer": config.min_sol_fee_buffer,
            "current_price": gateway_price,
            "lower_price": (
                executable_range.get("lower_price") if executable_range else None
            ),
            "upper_price": (
                executable_range.get("upper_price") if executable_range else None
            ),
            "total_amount_quote": config.total_amount_quote,
            "candidate_identity": _candidate_identity(candidate),
            "scan_age_seconds": scan_age,
        }

    lifecycle_state = None
    if executor_plan:
        lifecycle_state = {
            "controller_id": config.controller_id,
            "phase": "preflight_ready",
            "preset": range_plan.get("preset"),
            "selected_candidate": candidate,
            "gateway_pool_info": gateway,
            "executable_range": executable_range,
            "total_amount_quote": config.total_amount_quote,
            "executor_plan": executor_plan,
            "executor_id": None,
            "close_reason": None,
        }
    elif rebalance_plan:
        lifecycle_state = {
            "controller_id": config.controller_id,
            "phase": "rebalance_required",
            "preset": range_plan.get("preset"),
            "selected_candidate": candidate,
            "gateway_pool_info": gateway,
            "executable_range": executable_range,
            "total_amount_quote": config.total_amount_quote,
            "rebalance_plan": rebalance_plan,
            "rebalance": {"status": "required", "submission_attempt": 0},
            "executor_plan": None,
            "executor_id": None,
            "close_reason": None,
        }

    return {
        "decision": "ready" if ready else "blocked",
        "failed_checks": failed_checks,
        "checks": checks,
        "executor_plan": executor_plan,
        "rebalance_plan": rebalance_plan,
        "lifecycle_state": lifecycle_state,
        "evidence": {
            "candidate": candidate,
            "candidate_revalidation": candidate_revalidation,
            "scan_age_seconds": scan_age,
            "session_risk_profile": active_session_profile,
            "fresh_gateway_pool_info": gateway,
            "executable_range": executable_range,
            "inventory_plan": inventory,
            "rebalance_consumed": rebalance_consumed,
            "matched_wallet_balances": {
                "base": available_base,
                "quote": available_quote,
                "sol": available_sol,
            },
            "executor_lookup": {
                "succeeded": config.executor_lookup_succeeded,
                "matching_count": len(controller_executors),
                "states": executor_states,
            },
            "wallet_lookup": {
                "succeeded": config.wallet_lookup_succeeded,
                "account": config.wallet_account_name,
                "connector": config.wallet_connector_name,
            },
        },
        "warnings": [],
        "errors": config.api_errors,
    }


def _sanitized_input(config: Config, payload: dict[str, Any]) -> dict[str, Any]:
    value = config.model_dump(
        mode="json", exclude={"wallet_balances", "active_executors"}
    )
    value["matched_wallet_balances"] = payload["evidence"]["matched_wallet_balances"]
    value["executor_lookup"] = payload["evidence"]["executor_lookup"]
    return value


async def _with_live_evidence(config: Config, context: Any) -> Config:
    if not config.fetch_wallet_balances and config.execution_mode != "loop":
        return config

    errors = list(config.api_errors)
    try:
        if config.execution_mode == "loop":
            if session_execution_mode(config.controller_id) != "loop":
                raise ValueError(
                    "execution_mode does not match the current SESSION_MODE"
                )
            if (
                load_session_state(config.controller_id).get("session_status")
                != "running"
            ):
                raise ValueError("current Orca session is not accepting a new position")
            profile = session_risk_profile(config.controller_id)
            if profile != _text(config.selected_candidate.get("risk_profile")):
                raise ValueError(
                    "candidate risk_profile does not match the current session"
                )
            if not candidate_is_fresh(config.selected_candidate):
                raise ValueError("scanner candidate is stale or future-dated")
            candidate_valid, _, _ = _candidate_revalidation(
                config.selected_candidate, config.total_amount_quote, profile
            )
            if not candidate_valid:
                raise ValueError("scanner candidate contract revalidation failed")
            if (
                session_total_amount_quote(config.controller_id)
                != config.total_amount_quote
            ):
                raise ValueError("total_amount_quote does not match the active session")
        from config_manager import get_client

        chat_id = getattr(context, "_chat_id", None) if context is not None else None
        client = await get_client(chat_id or 0, context=context)
        if not client:
            raise RuntimeError("no Hummingbot API server available")
        if config.execution_mode == "loop":
            if not hasattr(client, "executors"):
                raise RuntimeError("executor API is unavailable")
            executor_rows = await _search_controller_executors(
                client, config.controller_id
            )
            states = [_text(row.get("status")).upper() for row in executor_rows]
            if any(
                state not in ACTIVE_EXECUTOR_STATES | TERMINAL_EXECUTOR_STATES
                for state in states
            ):
                raise ValueError("executor search returned an unknown state")
            if any(
                _executor_controller_id(row) != config.controller_id
                for row in executor_rows
            ):
                raise ValueError("executor search returned unowned records")
            archived_executor_ids = set(
                (load_session_state(config.controller_id).get("completed") or {}).get(
                    "executor_ids"
                )
                or []
            )
            unarchived_terminal_ids = [
                _executor_id(row)
                for row, state in zip(executor_rows, states, strict=True)
                if state in TERMINAL_EXECUTOR_STATES
                and _executor_id(row) not in archived_executor_ids
            ]
            if unarchived_terminal_ids:
                raise ValueError(
                    "executor search returned terminal records without position archives"
                )
            active_executors = [
                row
                for row, state in zip(executor_rows, states, strict=True)
                if state in ACTIVE_EXECUTOR_STATES and row.get("is_active") is not False
            ]
            config = config.model_copy(
                update={
                    "executor_lookup_succeeded": True,
                    "active_executors": active_executors,
                }
            )
            if active_executors:
                return config.model_copy(
                    update={
                        "wallet_lookup_succeeded": False,
                        "wallet_balances": [],
                        "api_errors": errors,
                    }
                )
            if not hasattr(client, "gateway_clmm"):
                raise RuntimeError("Gateway CLMM API is unavailable")
            gateway_result = await client.gateway_clmm.get_pool_info(
                connector="orca",
                network=config.connector_name,
                pool_address=_text(config.selected_candidate.get("pool_address")),
            )
            gateway_pool_info = _normalize_gateway_pool_info(
                gateway_result, _text(config.selected_candidate.get("pool_address"))
            )
            config = config.model_copy(update={"gateway_pool_info": gateway_pool_info})
            await ensure_gateway_tokens(
                client,
                config.wallet_connector_name,
                config.selected_candidate,
                gateway_pool_info,
            )
        if not config.fetch_wallet_balances:
            return config.model_copy(update={"api_errors": errors})
        balances = await client.portfolio.get_state(
            account_names=[config.wallet_account_name],
            connector_names=[config.wallet_connector_name],
            refresh=True,
        )
        account = balances.get(config.wallet_account_name)
        if not isinstance(account, dict):
            raise ValueError("scoped account balances were not returned")
        rows = account.get(config.wallet_connector_name)
        if not isinstance(rows, list):
            raise ValueError("scoped connector balances were not returned")
        normalized = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            symbol = _text(row.get("token") or row.get("symbol"))
            available = None
            for key in (
                "available_units",
                "available",
                "available_balance",
                "units",
            ):
                if key in row:
                    available = _number(row.get(key))
                    if available is not None:
                        break
            if symbol and available is not None:
                normalized.append(
                    {
                        "symbol": symbol,
                        "mint": _text(
                            row.get("mint")
                            or row.get("token_address")
                            or row.get("address")
                        ),
                        "available": available,
                    }
                )
        return config.model_copy(
            update={
                "wallet_lookup_succeeded": True,
                "wallet_balances": normalized,
                "api_errors": errors,
            }
        )
    except Exception as exc:
        errors.append(f"live evidence lookup failed: {type(exc).__name__}: {exc}")
        return config.model_copy(
            update={
                "executor_lookup_succeeded": (
                    False
                    if config.execution_mode == "loop"
                    else config.executor_lookup_succeeded
                ),
                "active_executors": (
                    [] if config.execution_mode == "loop" else config.active_executors
                ),
                "wallet_lookup_succeeded": False,
                "wallet_balances": [],
                "api_errors": errors,
            }
        )


def _format(payload: dict[str, Any]) -> str:
    lines = [f"Orca Live Preflight: {payload['decision']}"]
    if payload.get("rebalance_plan"):
        lines.append(
            f"Rebalance required: {payload['rebalance_plan'].get('base_shortfall')} "
            f"{payload['rebalance_plan'].get('base_symbol')}"
        )
    elif payload["failed_checks"]:
        lines.append("Failed checks: " + ", ".join(payload["failed_checks"]))
    elif payload.get("executor_plan"):
        plan = payload["executor_plan"]["executor_config"]
        lines.append(f"Plan: {plan.get('trading_pair')} on {plan.get('pool_address')}")
    return "\n".join(lines)


async def _save_report(payload: dict[str, Any]) -> None:
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("Orca Live Preflight")
        builder.source("routine", "orca_live_preflight").tags(
            ["orca", "lp", "preflight", "agent"]
        ).manual_order()
        builder.kpi(
            "Decision",
            payload["decision"],
            trend="up" if payload["decision"] == "ready" else "down",
        )
        builder.kpi("Failed Checks", str(len(payload["failed_checks"])))
        builder.markdown("## Agent Summary\n" + payload["agent_prompt_summary"])
        builder.markdown("## Preflight Checks")
        builder.table(payload["checks"])
        builder.markdown(
            "## Source Evidence\n```json\n"
            f"{json.dumps(payload['evidence'], indent=2, sort_keys=True, default=str)}\n```"
        )
        builder.markdown(
            "## Executor Plan\n```json\n"
            f"{json.dumps(payload.get('executor_plan'), indent=2, sort_keys=True, default=str)}\n```"
        )
        builder.markdown(
            "## Rebalance Plan\n```json\n"
            f"{json.dumps(payload.get('rebalance_plan'), indent=2, sort_keys=True, default=str)}\n```"
        )
        builder.markdown(
            "## Sanitized Routine Input\n```json\n"
            f"{json.dumps(payload['input_config'], indent=2, sort_keys=True, default=str)}\n```"
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
            "## Errors\n"
            + (
                "\n".join(f"- {error}" for error in payload["errors"])
                if payload["errors"]
                else "- None."
            )
        )
        builder.markdown(
            "## Debug JSON Payload\n```json\n"
            f"{json.dumps(payload, indent=2, sort_keys=True, default=str)}\n```"
        )
        await builder.save()
    except Exception:
        return


async def run(config: Config, context: Any) -> RoutineResult:
    existing_state: dict[str, Any] = {}
    if config.execution_mode == "loop" and config.controller_id:
        try:
            existing_state = load_lifecycle_state(config.controller_id)
        except Exception:
            existing_state = {}
    config = await _with_live_evidence(config, context)
    payload = _evaluate(config, require_session=True, existing_state=existing_state)
    if config.execution_mode == "loop":
        try:
            with lifecycle_lock(config.controller_id):
                existing = load_lifecycle_state(config.controller_id)
                rebalance_status = _text(
                    (existing.get("rebalance") or {}).get("status")
                ).upper()
                confirmed_rebalance = bool(existing) and (
                    rebalance_status in {"CONFIRMED", "NOT_REQUIRED"}
                    or existing.get("phase")
                    in {"rebalance_confirmed", "rebalance_confirmed_waiting_balance"}
                )
                if confirmed_rebalance:
                    persisted_candidate = existing.get("selected_candidate") or {}
                    if (
                        existing.get("controller_id") != config.controller_id
                        or _candidate_identity(persisted_candidate)
                        != _candidate_identity(config.selected_candidate)
                        or _number(existing.get("total_amount_quote"))
                        != config.total_amount_quote
                    ):
                        raise ValueError(
                            "confirmed rebalance does not match current preflight"
                        )
                    if payload["decision"] == "ready":
                        next_state = {
                            **payload["lifecycle_state"],
                            "selected_candidate": persisted_candidate,
                            "gateway_pool_info": config.gateway_pool_info,
                            "total_amount_quote": existing.get("total_amount_quote"),
                            "rebalance_plan": existing.get("rebalance_plan"),
                            "rebalance": existing.get("rebalance"),
                        }
                        payload["lifecycle_state"] = next_state
                        save_lifecycle_state(config.controller_id, next_state)
                    else:
                        blocked_state = {
                            **existing,
                            "phase": "rebalance_blocked",
                            "gateway_pool_info": config.gateway_pool_info,
                            "post_rebalance_block": {
                                "blocked_at": _utc_now(),
                                "failed_checks": payload["failed_checks"],
                                "gateway_pool_info": config.gateway_pool_info,
                                "wallet_evidence": payload["evidence"].get(
                                    "matched_wallet_balances"
                                ),
                            },
                        }
                        save_lifecycle_state(config.controller_id, blocked_state)
                        payload["lifecycle_state"] = blocked_state
                        payload["rebalance_plan"] = None
                        payload["executor_plan"] = None
                elif payload.get("lifecycle_state") is not None:
                    if existing:
                        raise ValueError("an unfinished Orca lifecycle already exists")
                    save_lifecycle_state(
                        config.controller_id, payload["lifecycle_state"]
                    )
        except Exception as exc:
            payload["decision"] = "blocked"
            if "lifecycle_state_persistence" not in payload["failed_checks"]:
                payload["failed_checks"].append("lifecycle_state_persistence")
            payload["executor_plan"] = None
            payload["rebalance_plan"] = None
            payload["lifecycle_state"] = None
            prior_status = _text(
                (existing_state.get("rebalance") or {}).get("status")
            ).upper()
            prior_confirmed = bool(existing_state) and (
                prior_status in {"CONFIRMED", "NOT_REQUIRED"}
                or existing_state.get("phase")
                in {"rebalance_confirmed", "rebalance_confirmed_waiting_balance"}
            )
            if prior_confirmed:
                try:
                    with lifecycle_lock(config.controller_id):
                        current = load_lifecycle_state(config.controller_id)
                        blocked_state = {
                            **current,
                            "phase": "rebalance_blocked",
                            "post_rebalance_block": {
                                "blocked_at": _utc_now(),
                                "failed_checks": payload["failed_checks"],
                                "reason": f"{type(exc).__name__}: {exc}",
                                "gateway_pool_info": config.gateway_pool_info,
                                "wallet_evidence": payload["evidence"].get(
                                    "matched_wallet_balances"
                                ),
                            },
                        }
                        save_lifecycle_state(config.controller_id, blocked_state)
                        payload["lifecycle_state"] = blocked_state
                except Exception as block_exc:
                    payload["errors"].append(
                        "post-rebalance manual-review persistence failed: "
                        f"{type(block_exc).__name__}: {block_exc}"
                    )
            payload["errors"].append(
                f"lifecycle state persistence failed: {type(exc).__name__}: {exc}"
            )
    payload["timestamp"] = _utc_now()
    payload["input_config"] = _sanitized_input(config, payload)
    payload["agent_prompt_summary"] = (
        "Preflight passed; use the exact executor plan for dry-run review or explicit live creation."
        if payload["decision"] == "ready"
        else (
            "Preflight requires a quote-to-base rebalance before it can pass."
            if payload.get("rebalance_plan")
            else f"Preflight blocked: {', '.join(payload['failed_checks'])}. Do not create an executor."
        )
    )
    await _save_report(payload)
    return RoutineResult(
        text=_format(payload)
        + "\n```json\n"
        + json.dumps(payload, indent=2, sort_keys=True, default=str)
        + "\n```",
        table_data=payload["checks"],
        table_columns=["check", "passed", "observed", "required"],
    )


def _self_check() -> None:
    observed_at = datetime(2026, 7, 24, tzinfo=timezone.utc)

    def candidate_for(symbol: str = "SOL", mint: str = "sol") -> dict[str, Any]:
        raw = {
            "address": "pool-1",
            "token_a": {"symbol": symbol, "mint": mint, "decimals": 9},
            "token_b": {
                "symbol": "USDC",
                "mint": CANONICAL_USDC_MINT,
                "decimals": 6,
            },
            "scanner_price": 100.0,
            "tvl_usd": 1_000_000.0,
            "volume_1h_usd": 10_000.0,
            "volume_4h_usd": 40_000.0,
            "volume_24h_usd": 300_000.0,
            "volume_7d_usd": 2_000_000.0,
            "fees_1h_usd": 25.0,
            "fees_4h_usd": 100.0,
            "fees_24h_usd": 500.0,
            "fees_7d_usd": 3_500.0,
            "net_price_change_24h": 0.005,
            "fee_rate_raw": 400.0,
            "adaptive_fee_enabled": False,
            "fee_tier_index": 1,
            "tick_spacing": 1,
            "has_warning": False,
            "source_categories": ["utility"],
            "source_lenses": ["volume24h"],
        }
        orca_pool_scan._derive(raw, 10)
        assert (
            orca_pool_scan._gate(
                raw,
                orca_pool_scan.PROFILE_POLICY["yield_focused"],
                True,
                set(),
            )
            is None
        )
        orca_pool_scan._score(raw, orca_pool_scan.PROFILE_POLICY["yield_focused"])
        raw["range_plan"] = orca_pool_scan._range_plan(raw, "yield_focused")[0]
        return orca_pool_scan._candidate_row(
            raw, observed_at.isoformat(), "yield_focused", 10
        )

    candidate = candidate_for()
    normalized_gateway = _normalize_gateway_pool_info(
        {
            "address": "pool-1",
            "base_token_address": "sol",
            "quote_token_address": CANONICAL_USDC_MINT,
            "price": "100.5",
        },
        "pool-1",
    )
    assert normalized_gateway["base_mint"] == "sol"
    assert normalized_gateway["quote_mint"] == CANONICAL_USDC_MINT

    class GatewayRegistry:
        async def get_network_tokens(self, network_id: str) -> dict[str, Any]:
            return {
                "tokens": [
                    {"symbol": "SOL", "address": "sol", "decimals": 9},
                    {
                        "symbol": "USDC",
                        "address": CANONICAL_USDC_MINT,
                        "decimals": 6,
                    },
                ]
            }

        async def add_token(self, **kwargs: Any) -> None:
            raise AssertionError("fixture tokens should already be registered")

    registry_evidence = asyncio.run(
        ensure_gateway_tokens(
            type("Client", (), {"gateway": GatewayRegistry()})(),
            "solana-mainnet-beta",
            candidate,
            normalized_gateway,
        )
    )
    assert len(registry_evidence) == 2
    assert _executor_rows({"data": []}) == []
    config = Config(
        controller_id="lpmaxxing.orca_self_check",
        executor_lookup_succeeded=True,
        wallet_lookup_succeeded=True,
        selected_candidate=candidate,
        gateway_pool_info={
            "pool_address": "pool-1",
            "base_mint": "sol",
            "quote_mint": CANONICAL_USDC_MINT,
            "current_price": 100.5,
        },
        wallet_balances=[
            {"symbol": "SOL", "mint": "sol", "available": 1},
            {"symbol": "USDC", "mint": CANONICAL_USDC_MINT, "available": 20},
        ],
    )
    ready = _evaluate(config, now=observed_at)
    assert ready["decision"] == "ready", ready
    assert ready["executor_plan"]["action"] == "create", ready
    assert (
        ready["executor_plan"]["executor_config"]["controller_id"]
        == "lpmaxxing.orca_self_check"
    ), ready
    assert ready["executor_plan"]["executor_config"]["total_amount_quote"] == 10, ready
    assert ready["lifecycle_state"]["preset"] == "concentrated", ready
    bounds = ready["evidence"]["executable_range"]
    inventory = ready["evidence"]["inventory_plan"]
    assert Decimal(str(inventory["quote_amount"])).as_tuple().exponent >= -6
    assert bounds["center_price"] == 100.5
    assert math.isclose(
        bounds["limit_buffer"],
        bounds["provisional_half_width"] * 0.5,
    )
    assert candidate_is_fresh(candidate, observed_at)
    assert candidate_is_fresh(candidate, observed_at - timedelta(seconds=30))
    assert not candidate_is_fresh(
        candidate, observed_at - timedelta(seconds=30, microseconds=1)
    )
    assert candidate_is_fresh(candidate, observed_at.replace(second=0))
    assert candidate_is_fresh(candidate, observed_at.replace(minute=10))
    assert not candidate_is_fresh(
        candidate, observed_at.replace(minute=10, microsecond=1)
    )
    assert _executable_range(100, 0.005)["limit_buffer"] == 0.003
    assert _executable_range(100, 0.01)["limit_buffer"] == 0.005
    assert _executable_range(100, 0.02)["limit_buffer"] == 0.01
    assert _executable_range(100, 0.03)["limit_buffer"] == 0.015
    assert _executable_range(100, 0.95)["limit_buffer"] == 0.015

    token_candidate = candidate_for("TOKEN", "token")
    needs_rebalance = _evaluate(
        config.model_copy(
            update={
                "selected_candidate": token_candidate,
                "gateway_pool_info": {
                    "pool_address": "pool-1",
                    "base_mint": "token",
                    "quote_mint": CANONICAL_USDC_MINT,
                    "current_price": 100.5,
                },
                "wallet_balances": [
                    {
                        "symbol": "USDC",
                        "mint": CANONICAL_USDC_MINT,
                        "available": 20,
                    },
                    {"symbol": "SOL", "mint": "sol", "available": 1},
                ],
            }
        ),
        now=observed_at,
    )
    assert needs_rebalance["failed_checks"] == ["wallet_inventory"], needs_rebalance
    assert needs_rebalance["rebalance_plan"]["base_shortfall"] > 0, needs_rebalance
    consumed = _evaluate(
        config.model_copy(
            update={
                "selected_candidate": token_candidate,
                "gateway_pool_info": {
                    "pool_address": "pool-1",
                    "base_mint": "token",
                    "quote_mint": CANONICAL_USDC_MINT,
                    "current_price": 100.5,
                },
                "wallet_balances": [
                    {"symbol": "USDC", "mint": CANONICAL_USDC_MINT, "available": 20},
                    {"symbol": "SOL", "mint": "sol", "available": 1},
                ],
            }
        ),
        now=observed_at,
        existing_state={
            "phase": "rebalance_confirmed",
            "rebalance": {"status": "CONFIRMED"},
        },
    )
    assert consumed["rebalance_plan"] is None, consumed
    assert consumed["evidence"]["rebalance_consumed"] is True, consumed

    stale = _evaluate(config, now=observed_at.replace(minute=10, microsecond=1))
    assert "scan_freshness" in stale["failed_checks"], stale
    tampered_candidate = {**candidate, "fees_24h_usd": 501.0}
    tampered = _evaluate(
        config.model_copy(update={"selected_candidate": tampered_candidate}),
        now=observed_at,
    )
    assert "scanner_candidate_contract" in tampered["failed_checks"], tampered
    planning = _evaluate(
        config.model_copy(update={"execution_mode": "run_once"}), now=observed_at
    )
    assert planning["decision"] == "blocked", planning
    blocked = _evaluate(
        config.model_copy(
            update={
                "active_executors": [
                    {"controller_id": "lpmaxxing.orca_self_check", "status": "RUNNING"}
                ]
            }
        ),
        now=observed_at,
    )
    assert blocked["decision"] == "blocked", blocked
    assert "single_executor" in blocked["failed_checks"], blocked
    malformed_executor = _evaluate(
        config.model_copy(
            update={"active_executors": [{"controller_id": "other", "unexpected": 1}]}
        ),
        now=observed_at,
    )
    assert "single_executor" in malformed_executor["failed_checks"], malformed_executor
    lookup_failed = _evaluate(
        config.model_copy(update={"executor_lookup_succeeded": False}),
        now=observed_at,
    )
    assert lookup_failed["decision"] == "blocked", lookup_failed


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        _self_check()
        print("self-check passed")
        raise SystemExit(0)
    print(asyncio.run(run(Config(), None)).text)
