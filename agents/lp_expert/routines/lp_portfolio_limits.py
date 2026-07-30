"""Read-only per-executor and session limit evidence for LP Expert."""

import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, StrictStr, model_validator

from agents.lp_expert.routines import _routine_report as reports

CATEGORY = "LP Portfolio Safety"
CONTROLLER_RE = re.compile(
    r"^lp_expert\.(?P<strategy>[a-z][a-z0-9_-]*)_(?:e)?[1-9]\d*$"
)
STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "strategies"
REQUIRED_CONFIG = {
    "server_name",
    "account_name",
    "execution_mode",
    "total_amount_quote",
    "executor_max_age_minutes",
    "executor_take_profit_net_pnl_ratio",
    "executor_stop_loss_net_pnl_ratio",
    "session_max_age_minutes",
    "session_take_profit_net_pnl_ratio",
    "session_stop_loss_net_pnl_ratio",
}
SETTLED = {
    "COMPLETE",
    "COMPLETED",
    "TERMINATED",
    "CANCELED",
    "CANCELLED",
    "CLOSED",
    "STOPPED",
}
STOPPABLE_LP_STATES = {"NOT_ACTIVE", "OPENING", "IN_RANGE", "OUT_OF_RANGE"}
RECONCILING_LP_STATES = {"CLOSING", "SWAPPING", "FAILED"}


class Config(BaseModel):
    """Classify the exact current LP portfolio against frozen limits."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    controller_id: StrictStr

    @model_validator(mode="after")
    def identity(self) -> "Config":
        if not CONTROLLER_RE.fullmatch(self.controller_id):
            raise ValueError("invalid lp_expert controller_id")
        return self


def _decimal(value: Any, label: str, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{label} must be a decimal") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f"{label} must be finite and valid")
    return result


def _positive_integer(value: Any, label: str) -> int:
    number = _decimal(value, label, positive=True)
    if isinstance(value, bool) or number != number.to_integral_value():
        raise ValueError(f"{label} must be a positive integer")
    return int(number)


async def _authority(config: Config) -> tuple[dict[str, Any], Path | None]:
    from agents.lp_expert.routines.gateway_swap import _runtime

    runtime = await _runtime(config.controller_id)
    mode = runtime["execution_mode"]
    match = CONTROLLER_RE.fullmatch(config.controller_id)
    strategy = match.group("strategy") if match else ""
    if runtime.get("_strategy_slug") != strategy:
        raise ValueError("active strategy identity is inconsistent")
    if mode == "loop":
        number = config.controller_id.rsplit("_", 1)[1]
        path = (
            STRATEGIES_DIR / strategy / "sessions" / f"session_{number}" / "config.yml"
        )
        strategy_root = (STRATEGIES_DIR / strategy).resolve()
        resolved = path.resolve()
        if (
            not path.is_file()
            or path.is_symlink()
            or strategy_root not in resolved.parents
        ):
            raise ValueError("current session config path is unavailable or unsafe")
        before = path.stat()
        authority = yaml.safe_load(path.read_text())
        after = path.stat()
        if before.st_mtime_ns != after.st_mtime_ns or before.st_size != after.st_size:
            raise ValueError("current session config changed while being read")
    else:
        path = None
        authority = runtime
    if (
        not isinstance(authority, dict)
        or not REQUIRED_CONFIG <= set(authority)
        or authority.get("execution_mode") != mode
        or authority.get("server_name") != runtime.get("server_name")
        or not all(
            isinstance(authority.get(key), str) and authority[key].strip()
            for key in ("server_name", "account_name")
        )
    ):
        raise ValueError("current executor-limit config authority is unavailable")
    return authority, path


def _session_started_at(path: Path | None, observed_at: Decimal) -> Decimal:
    if path is None:
        return observed_at
    started_at = _decimal(path.stat().st_mtime, "session start", positive=True)
    if started_at > observed_at:
        raise ValueError("current session start is in the future")
    return started_at


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        rows, cursor = value, None
    elif isinstance(value, dict):
        rows, cursor = value.get("data"), value.get("next_cursor")
    else:
        raise ValueError("executor search response is invalid")
    if cursor is not None:
        raise ValueError("executor search exceeded the complete 1000-row scope")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("executor search rows are invalid")
    return rows


def _executor(
    row: dict[str, Any], controller_id: str, observed_at: Decimal
) -> dict[str, Any]:
    inner = row.get("config") if isinstance(row.get("config"), dict) else {}
    owners = {
        str(value).strip()
        for value in (row.get("controller_id"), inner.get("controller_id"))
        if value
    }
    status = str(row.get("status") or "").strip().upper()
    active = status not in SETTLED
    if (
        owners != {controller_id}
        or not status
        or status in {"FAILED", "ERROR"}
        or (row.get("is_active") is not None and row["is_active"] is not active)
    ):
        raise ValueError("executor ownership or lifecycle evidence is inconsistent")
    executor_type = row.get("type") or inner.get("type")
    if executor_type != "lp_executor":
        raise ValueError("current-controller executor is not an LP executor")
    ids = {
        str(value).strip() for value in (row.get("executor_id"), row.get("id")) if value
    }
    if len(ids) != 1:
        raise ValueError("executor identity is not unique")
    result = {
        "executor_id": ids.pop(),
        "active": active,
        "net_pnl_quote": _decimal(row.get("net_pnl_quote"), "executor net PnL quote"),
    }
    if not active:
        return result
    custom_info = (
        row.get("custom_info") if isinstance(row.get("custom_info"), dict) else {}
    )
    lifecycle_state = str(custom_info.get("state") or "").strip().upper()
    if lifecycle_state not in STOPPABLE_LP_STATES | RECONCILING_LP_STATES:
        raise ValueError("active LP executor lifecycle evidence is unavailable")
    timestamps = {
        _decimal(value, "executor timestamp", positive=True)
        for value in (row.get("timestamp"), inner.get("timestamp"))
        if value is not None
    }
    if len(timestamps) != 1:
        raise ValueError("executor timestamp is not unique")
    timestamp = timestamps.pop()
    if timestamp > observed_at:
        raise ValueError("executor timestamp is in the future")
    return {
        **result,
        "trading_pair": inner.get("trading_pair"),
        "pool_address": inner.get("pool_address"),
        "lifecycle_state": lifecycle_state,
        "age_minutes": (observed_at - timestamp) / Decimal(60),
        "net_pnl_ratio": _decimal(row.get("net_pnl_pct"), "executor net PnL"),
    }


async def _result(
    status: str,
    config: Config,
    *,
    limits: dict[str, Any] | None = None,
    session: dict[str, Any] | None = None,
    executors: list[dict[str, Any]] | None = None,
    reason: str | None = None,
) -> str:
    rows = executors or []
    payload = {
        "status": status,
        "controller_id": config.controller_id,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "limits": limits,
        "session": session,
        "executors": rows,
        "triggered_executor_ids": [
            row["executor_id"] for row in rows if row.get("triggered_by")
        ],
        "close_required_executor_ids": [
            row["executor_id"] for row in rows if row.get("close_required")
        ],
        "reconcile_required_executor_ids": [
            row["executor_id"] for row in rows if row.get("reconcile_required")
        ],
        "mutation": False,
    }
    if reason:
        payload["reason"] = reason
    evidence = []
    if session:
        evidence.append({"kind": "session_limit", **session})
    evidence.extend({"kind": "executor_limit", **row} for row in rows)
    if reason:
        evidence.append({"kind": "error", "reason": reason})
    payload = await reports.attach_trace(
        payload,
        title="LP Supervision Limit Check",
        source="lp_portfolio_limits",
        status=status,
        summary={
            "controller_id": config.controller_id,
            "active_executors": len(rows),
            "triggered_executors": len(payload["triggered_executor_ids"]),
            "executor_max_age_minutes": (limits or {}).get("executor_max_age_minutes"),
            "executor_take_profit_net_pnl_ratio": (limits or {}).get(
                "executor_take_profit_net_pnl_ratio"
            ),
            "executor_stop_loss_net_pnl_ratio": (limits or {}).get(
                "executor_stop_loss_net_pnl_ratio"
            ),
            "session_stop_latched": (session or {}).get("stop_latched"),
            "session_triggered_by": (session or {}).get("triggered_by"),
        },
        evidence=evidence,
    )
    return json.dumps(payload, default=str, separators=(",", ":"), sort_keys=True)


async def run(config: Config, context: Any) -> str:
    try:
        from config_manager import get_config_manager

        authority, session_path = await _authority(config)
        limits = {
            "executor_max_age_minutes": _positive_integer(
                authority["executor_max_age_minutes"], "executor max age"
            ),
            "executor_take_profit_net_pnl_ratio": _decimal(
                authority["executor_take_profit_net_pnl_ratio"],
                "executor take profit",
                positive=True,
            ),
            "executor_stop_loss_net_pnl_ratio": _decimal(
                authority["executor_stop_loss_net_pnl_ratio"],
                "executor stop loss",
                positive=True,
            ),
            "session_max_age_minutes": _positive_integer(
                authority["session_max_age_minutes"], "session max age"
            ),
            "session_take_profit_net_pnl_ratio": _decimal(
                authority["session_take_profit_net_pnl_ratio"],
                "session take profit",
                positive=True,
            ),
            "session_stop_loss_net_pnl_ratio": _decimal(
                authority["session_stop_loss_net_pnl_ratio"],
                "session stop loss",
                positive=True,
            ),
            "total_amount_quote": _decimal(
                authority["total_amount_quote"], "session total amount", positive=True
            ),
        }
        client = await get_config_manager().get_client(authority["server_name"])
        value = await client.executors.search_executors(
            account_names=[authority["account_name"]],
            executor_types=["lp_executor"],
            controller_ids=[config.controller_id],
            limit=1000,
            cursor=None,
        )
        observed_at = Decimal(str(time.time()))
        started_at = _session_started_at(session_path, observed_at)
        normalized = [
            _executor(row, config.controller_id, observed_at) for row in _rows(value)
        ]
        session_net_pnl_quote = sum(
            (row["net_pnl_quote"] for row in normalized), Decimal(0)
        )
        session_age_minutes = (observed_at - started_at) / Decimal(60)
        session_triggered_by = []
        if session_net_pnl_quote <= -(
            limits["total_amount_quote"] * limits["session_stop_loss_net_pnl_ratio"]
        ):
            session_triggered_by.append("stop_loss")
        if session_net_pnl_quote >= (
            limits["total_amount_quote"] * limits["session_take_profit_net_pnl_ratio"]
        ):
            session_triggered_by.append("take_profit")
        if session_age_minutes >= limits["session_max_age_minutes"]:
            session_triggered_by.append("max_age")
        session = {
            "started_at": datetime.fromtimestamp(
                float(started_at), timezone.utc
            ).isoformat(),
            "age_minutes": session_age_minutes,
            "net_pnl_quote": session_net_pnl_quote,
            "take_profit_quote": (
                limits["total_amount_quote"]
                * limits["session_take_profit_net_pnl_ratio"]
            ),
            "stop_loss_quote": -(
                limits["total_amount_quote"] * limits["session_stop_loss_net_pnl_ratio"]
            ),
            "triggered_by": session_triggered_by,
            "stop_latched": bool(session_triggered_by),
        }
        executors = []
        for executor in normalized:
            if not executor["active"]:
                continue
            triggered_by = []
            if executor["net_pnl_ratio"] <= -limits["executor_stop_loss_net_pnl_ratio"]:
                triggered_by.append("stop_loss")
            if (
                executor["net_pnl_ratio"]
                >= limits["executor_take_profit_net_pnl_ratio"]
            ):
                triggered_by.append("take_profit")
            if executor["age_minutes"] >= limits["executor_max_age_minutes"]:
                triggered_by.append("max_age")
            stoppable = executor["lifecycle_state"] in STOPPABLE_LP_STATES
            must_wind_down = bool(triggered_by) or session["stop_latched"]
            executors.append(
                {
                    **executor,
                    "triggered_by": triggered_by,
                    "close_required": must_wind_down and stoppable,
                    "reconcile_required": must_wind_down and not stoppable,
                }
            )
        return await _result(
            "complete",
            config,
            limits=limits,
            session=session,
            executors=executors,
        )
    except Exception as exc:
        return await _result(
            "rejected",
            config,
            reason=f"{type(exc).__name__}: {exc}",
        )
