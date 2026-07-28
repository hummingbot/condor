import fcntl
import hashlib
import json
import math
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from agents.lpmaxxing.routines import _orca_policy as orca_policy
from agents.lpmaxxing.routines._orca_evidence import redact

LIVE_PROFILES = orca_policy.LIVE_PROFILES
SESSION_SCHEMA_VERSION = 2


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


def active_position_records(session_state: dict[str, Any]) -> list[dict[str, Any]]:
    active_position = session_state.get("active_position")
    return [active_position] if isinstance(active_position, dict) else []


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
    value = redact({**state, "updated_at": _utc_now()}, datetime_iso=False)
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
    records = active_position_records(session_state)
    return records[0] if records else {}


def save_lifecycle_state(controller_id: str, state: dict[str, Any]) -> None:
    session_state = ensure_session_state(controller_id)
    status = _text(session_state.get("session_status"))
    if status not in {"running", "stopping"}:
        raise ValueError(f"Orca session is not writable while {status or 'unknown'}")
    records = active_position_records(session_state)
    current = records[0] if records else {}
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
    controller_id: str, trigger: dict[str, Any], *, active_position: bool
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
    if active_position_records(session_state):
        raise ValueError("completed Orca cycle still has an active position")
    resume_at = _parse_timestamp(session_state.get("cycle_resume_at"))
    current = now or datetime.now(timezone.utc)
    if resume_at is None or current < resume_at:
        return session_state
    next_state = {**session_state, "session_status": "running", "cycle_resume_at": None}
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
    if lifecycle.get("terminal_outcome") is not None:
        critical["terminal_outcome"] = lifecycle.get("terminal_outcome")
        critical["position_opened"] = lifecycle.get("position_opened")
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
    audit_payload = redact(audit_payload, datetime_iso=False)
    with lifecycle_lock(controller_id):
        session_state = ensure_session_state(controller_id)
        records = active_position_records(session_state)
        lifecycle = records[0] if records else {}
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
                        incoming_net_pnl, archived_net_pnl, rel_tol=1e-12, abs_tol=1e-12
                    )
                ):
                    raise ValueError("completed position PnL conflicts with audit")
                expected_fingerprint = _audit_fingerprint(
                    audit_payload, position_number, actual_executor_id, incoming_net_pnl
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
        next_state = {**session_state, "completed": completed, "active_position": None}
        terminal_state = _text(
            (audit_payload.get("identity") or {}).get("terminal_state")
        ).upper()
        failed_before_open = (audit_payload.get("lifecycle") or {}).get(
            "terminal_outcome"
        ) == "failed_before_open" and (audit_payload.get("lifecycle") or {}).get(
            "position_opened"
        ) is False
        if terminal_state == "FAILED" and not failed_before_open:
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
