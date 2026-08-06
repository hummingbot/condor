"""Exact LP executor normalization, capacity, and close-cleanup evidence."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from agents.lp_expert.core.planner import NETWORK

SETTLED = {
    "COMPLETE",
    "COMPLETED",
    "TERMINATED",
    "CANCELED",
    "CANCELLED",
    "CLOSED",
    "STOPPED",
}
STOPPABLE = {"NOT_ACTIVE", "OPENING", "IN_RANGE", "OUT_OF_RANGE"}
RECONCILING = {"CLOSING", "SWAPPING", "FAILED"}
NATIVE_SWAP_SUCCESS = {"CONFIRMED", "SUCCESS", "COMPLETED"}
NATIVE_SWAP_PENDING = {"PENDING", "SUBMITTED", "SWAPPING", "UNKNOWN"}
NATIVE_SWAP_FAILED = {"FAILED", "FAILURE", "ERROR", "REJECTED", "NOT_SUBMITTED"}


def decimal_value(value: Any, label: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{label} must be a decimal") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f"{label} must be finite and valid")
    return result


def positive_integer(value: Any, label: str) -> int:
    number = decimal_value(value, label, positive=True)
    if isinstance(value, bool) or number != number.to_integral_value():
        raise ValueError(f"{label} must be a positive integer")
    return int(number)


def _nested(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("config")
    return value if isinstance(value, dict) else {}


def _custom(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("custom_info")
    return value if isinstance(value, dict) else {}


def normalize_executor(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("executor row is invalid")
    config = _nested(row)
    owners = {
        str(value).strip()
        for value in (row.get("controller_id"), config.get("controller_id"))
        if value
    }
    ids = {
        str(value).strip() for value in (row.get("executor_id"), row.get("id")) if value
    }
    status = str(row.get("status") or "").strip().upper()
    if len(owners) != 1 or len(ids) != 1 or not status:
        raise ValueError("executor identity or ownership is not unique")
    if (row.get("type") or config.get("type")) != "lp_executor":
        raise ValueError("executor is not an LP executor")
    active = status not in SETTLED
    if row.get("is_active") is not None and bool(row["is_active"]) is not active:
        raise ValueError("executor status and activity conflict")
    custom = _custom(row)
    lifecycle = str(custom.get("state") or "").strip().upper() or None
    if active and lifecycle not in STOPPABLE | RECONCILING:
        raise ValueError("active LP lifecycle evidence is unavailable")
    timestamp_values = {
        decimal_value(value, "executor timestamp", positive=True)
        for value in (row.get("timestamp"), config.get("timestamp"))
        if value is not None
    }
    timestamp = timestamp_values.pop() if len(timestamp_values) == 1 else None
    if active and timestamp is None:
        raise ValueError("active executor timestamp is not unique")
    pool = str(config.get("pool_address") or custom.get("pool_address") or "").strip()
    pair = str(config.get("trading_pair") or custom.get("trading_pair") or "").strip()
    normalized = {
        "executor_id": ids.pop(),
        "controller_id": owners.pop(),
        "status": status,
        "active": active,
        "lifecycle_state": lifecycle,
        "timestamp": timestamp,
        "pool_address": pool or None,
        "trading_pair": pair or None,
        "net_pnl_quote": decimal_value(row.get("net_pnl_quote", 0), "executor net PnL"),
        "net_pnl_ratio": decimal_value(
            row.get("net_pnl_pct", 0), "executor net PnL ratio"
        ),
        "_config": config,
        "_custom_info": custom,
    }
    if active:
        required = {
            "base_amount",
            "quote_amount",
            "lower_price",
            "upper_price",
            "pool_address",
        }
        if not required <= set(config):
            raise ValueError("active executor capital evidence is incomplete")
        valuation = (
            decimal_value(config["lower_price"], "executor lower price", positive=True)
            * decimal_value(
                config["upper_price"], "executor upper price", positive=True
            )
        ).sqrt()
        normalized["exposure_quote"] = decimal_value(
            config["base_amount"], "executor base amount", positive=True
        ) * valuation + decimal_value(
            config["quote_amount"], "executor quote amount", positive=True
        )
    else:
        normalized["exposure_quote"] = Decimal(0)
    return normalized


def _page(value: Any) -> tuple[list[dict[str, Any]], str | None]:
    if isinstance(value, list):
        rows, cursor = value, None
    elif isinstance(value, dict):
        rows, cursor = value.get("data"), value.get("next_cursor")
    else:
        raise ValueError("executor search response is invalid")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("executor search rows are invalid")
    if cursor is not None and (not isinstance(cursor, str) or not cursor.strip()):
        raise ValueError("executor search cursor is invalid")
    return rows, cursor


async def fetch_all_executors(client: Any, account_name: str) -> list[dict[str, Any]]:
    """Follow bounded native pagination and reject repeated/truncated pages."""
    cursor: str | None = None
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for _ in range(20):
        response = await client.executors.search_executors(
            account_names=[account_name],
            connector_names=[NETWORK],
            executor_types=["lp_executor"],
            limit=1000,
            cursor=cursor,
        )
        page, cursor = _page(response)
        rows.extend(page)
        if cursor is None:
            return rows
        if cursor in seen:
            raise ValueError("executor search repeated a cursor")
        seen.add(cursor)
    raise ValueError("executor search exceeded the bounded pagination limit")


def _pick(value: dict[str, Any], *names: str) -> Any:
    pending = [value]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        if not isinstance(current, dict) or id(current) in seen:
            continue
        seen.add(id(current))
        for name in names:
            if current.get(name) not in (None, ""):
                return current[name]
        pending.extend(item for item in current.values() if isinstance(item, dict))
    return None


def exact_close_evidence(executor: dict[str, Any]) -> dict[str, Any]:
    """Extract executor-bound close evidence; never infer from wallet balances."""
    source = {
        "config": executor.get("_config", {}),
        "custom_info": executor.get("_custom_info", {}),
    }
    fields = {
        "position_address": _pick(source, "position_address", "position_id"),
        "pool_address": _pick(source, "pool_address"),
        "base_symbol": _pick(source, "base_symbol", "base_token"),
        "base_mint": _pick(source, "base_mint", "token_a_mint"),
        "base_decimals": _pick(source, "base_decimals", "token_a_decimals"),
        "residual_base_amount": _pick(
            source, "residual_base_amount", "base_residual", "base_amount_remaining"
        ),
        "native_swap_status": _pick(
            source, "native_swap_status", "close_swap_status", "swap_status"
        ),
        "close_transaction_hash": _pick(
            source, "close_transaction_hash", "close_tx_hash"
        ),
    }
    missing = [key for key, value in fields.items() if value in (None, "")]
    if missing:
        raise ValueError(
            "terminal executor lacks exact close evidence: " + ", ".join(missing)
        )
    residual = decimal_value(fields["residual_base_amount"], "close residual")
    if residual < 0:
        raise ValueError("close residual must be non-negative")
    try:
        decimals = int(fields["base_decimals"])
    except (TypeError, ValueError) as exc:
        raise ValueError("close base decimals are invalid") from exc
    if isinstance(fields["base_decimals"], bool) or not 0 <= decimals <= 18:
        raise ValueError("close base decimals are invalid")
    return {
        **fields,
        "base_decimals": decimals,
        "residual_base_amount": residual,
        "native_swap_status": str(fields["native_swap_status"]).strip().upper(),
    }


def classify_cleanup(
    *,
    executor: dict[str, Any],
    prior_close: dict[str, Any],
    controller_id: str,
    current_tick: int,
    dust_quote: Decimal,
) -> dict[str, Any]:
    if prior_close.get("executor_id") != executor["executor_id"]:
        raise ValueError("prior close executor identity is inconsistent")
    closed_tick = positive_integer(prior_close.get("closed_tick"), "prior close tick")
    if closed_tick >= current_tick:
        raise ValueError("post-close verification requires a later tick")
    stop_proof = prior_close.get("stop_proof")
    expected_stop = {
        "tick": closed_tick,
        "controller_id": controller_id,
        "executor_id": executor["executor_id"],
        "keep_position": False,
    }
    if not isinstance(stop_proof, dict) or any(
        stop_proof.get(key) != value for key, value in expected_stop.items()
    ):
        reason = str(
            prior_close.get("stop_proof_error")
            or "exact platform stop evidence is unavailable or conflicting"
        )
        return {
            "status": "manual_review",
            "executor_id": executor["executor_id"],
            "closed_tick": closed_tick,
            "capacity_quarantined": True,
            "reason": reason,
            "stop_proof": stop_proof,
        }
    if executor["active"]:
        return {
            "status": "pending",
            "executor_id": executor["executor_id"],
            "closed_tick": closed_tick,
            "capacity_quarantined": True,
            "reason": "executor close is not terminal",
            "stop_proof": stop_proof,
        }
    try:
        evidence = exact_close_evidence(executor)
    except ValueError as exc:
        return {
            "status": "manual_review",
            "executor_id": executor["executor_id"],
            "closed_tick": closed_tick,
            "capacity_quarantined": True,
            "reason": str(exc),
            "stop_proof": stop_proof,
        }
    expected = {
        key: prior_close.get(key)
        for key in (
            "position_address",
            "pool_address",
            "base_symbol",
            "base_mint",
            "base_decimals",
        )
    }
    observed = {key: evidence[key] for key in expected}
    if observed != expected:
        return {
            "status": "manual_review",
            "executor_id": executor["executor_id"],
            "closed_tick": closed_tick,
            "capacity_quarantined": True,
            "reason": "prior close and terminal executor evidence conflict",
            "stop_proof": stop_proof,
        }
    status = evidence["native_swap_status"]
    residual_quote = evidence["residual_base_amount"] * decimal_value(
        prior_close.get("valuation_price"), "cleanup valuation price", positive=True
    )
    if status not in NATIVE_SWAP_PENDING | NATIVE_SWAP_SUCCESS | NATIVE_SWAP_FAILED:
        return {
            "status": "manual_review",
            "executor_id": executor["executor_id"],
            "closed_tick": closed_tick,
            "capacity_quarantined": True,
            "reason": "native close-out status is not recognized",
            "stop_proof": stop_proof,
            "evidence": evidence,
        }
    if status in NATIVE_SWAP_PENDING:
        result = "pending"
    elif status in NATIVE_SWAP_SUCCESS and evidence["residual_base_amount"] == 0:
        result = "complete"
    elif residual_quote <= dust_quote:
        result = "dust"
    else:
        result = "cleanup_required"
    return {
        "status": result,
        "executor_id": executor["executor_id"],
        "closed_tick": closed_tick,
        "capacity_quarantined": result not in {"complete", "dust"},
        "residual_quote": residual_quote,
        "stop_proof": stop_proof,
        "evidence": evidence,
    }


def build_portfolio(
    *,
    rows: list[dict[str, Any]],
    controller_id: str,
    strategy_config: dict[str, Any],
    session_started_at: float,
    current_tick: int,
    prior_closes: list[dict[str, Any]],
) -> dict[str, Any]:
    observed_at = Decimal(str(time.time()))
    normalized = [normalize_executor(row) for row in rows]
    owned = [row for row in normalized if row["controller_id"] == controller_id]
    foreign_active = [
        row["executor_id"]
        for row in normalized
        if row["controller_id"] != controller_id and row["active"]
    ]
    active = [row for row in owned if row["active"]]
    max_open = positive_integer(
        strategy_config["max_open_executors"], "max open executors"
    )
    total = decimal_value(
        strategy_config["total_amount_quote"], "total amount", positive=True
    )
    executor_max_age = positive_integer(
        strategy_config["executor_max_age_minutes"], "executor max age"
    )
    executor_tp = decimal_value(
        strategy_config["executor_take_profit_net_pnl_ratio"],
        "executor take profit",
        positive=True,
    )
    executor_sl = decimal_value(
        strategy_config["executor_stop_loss_net_pnl_ratio"],
        "executor stop loss",
        positive=True,
    )
    session_age = (observed_at - Decimal(str(session_started_at))) / Decimal(60)
    session_pnl = sum((row["net_pnl_quote"] for row in owned), Decimal(0))
    session_triggers = []
    if session_age >= positive_integer(
        strategy_config["session_max_age_minutes"], "session max age"
    ):
        session_triggers.append("max_age")
    if session_pnl >= total * decimal_value(
        strategy_config["session_take_profit_net_pnl_ratio"],
        "session take profit",
        positive=True,
    ):
        session_triggers.append("take_profit")
    if session_pnl <= -(
        total
        * decimal_value(
            strategy_config["session_stop_loss_net_pnl_ratio"],
            "session stop loss",
            positive=True,
        )
    ):
        session_triggers.append("stop_loss")
    executor_results = []
    for executor in active:
        age_minutes = (observed_at - executor["timestamp"]) / Decimal(60)
        triggers = []
        if age_minutes >= executor_max_age:
            triggers.append("max_age")
        if executor["net_pnl_ratio"] >= executor_tp:
            triggers.append("take_profit")
        if executor["net_pnl_ratio"] <= -executor_sl:
            triggers.append("stop_loss")
        must_close = bool(triggers or session_triggers)
        executor_results.append(
            {
                **{
                    key: value
                    for key, value in executor.items()
                    if not key.startswith("_")
                },
                "age_minutes": age_minutes,
                "triggered_by": triggers,
                "close_required": must_close
                and executor["lifecycle_state"] in STOPPABLE,
                "reconcile_required": must_close
                and executor["lifecycle_state"] not in STOPPABLE,
            }
        )
    cleanups = []
    for prior_close in prior_closes:
        matches = [
            row for row in owned if row["executor_id"] == prior_close.get("executor_id")
        ]
        if len(matches) != 1:
            cleanup = {
                "status": "manual_review",
                "executor_id": prior_close.get("executor_id"),
                "closed_tick": prior_close.get("closed_tick"),
                "capacity_quarantined": True,
                "reason": "prior close did not match one current-session executor",
                "stop_proof": prior_close.get("stop_proof"),
            }
        else:
            cleanup = classify_cleanup(
                executor=matches[0],
                prior_close=prior_close,
                controller_id=controller_id,
                current_tick=current_tick,
                dust_quote=decimal_value(
                    strategy_config["residual_base_dust_quote"], "residual dust"
                ),
            )
        cleanups.append(cleanup)
    active_ids = {row["executor_id"] for row in active}
    quarantined_terminal_ids = {
        str(cleanup["executor_id"])
        for cleanup in cleanups
        if cleanup["capacity_quarantined"]
        and cleanup.get("executor_id") not in active_ids
    }
    active_exposure = sum((row["exposure_quote"] for row in active), Decimal(0))
    reconciling_ids = [
        row["executor_id"] for row in active if row["lifecycle_state"] in RECONCILING
    ]
    available_slots = max(0, max_open - len(active) - len(quarantined_terminal_ids))
    return {
        "session": {
            "age_minutes": session_age,
            "net_pnl_quote": session_pnl,
            "triggered_by": session_triggers,
            "stop_latched": bool(session_triggers),
        },
        "executors": executor_results,
        "terminal_executor_ids": [
            row["executor_id"] for row in owned if not row["active"]
        ],
        "foreign_active_executor_ids": foreign_active,
        "active_count": len(active),
        "active_exposure_quote": active_exposure,
        "occupied_pools": sorted(
            row["pool_address"] for row in active if row["pool_address"]
        ),
        "available_slots": available_slots,
        "remaining_quote_budget": max(Decimal(0), total - active_exposure),
        "deployment_blocked": bool(
            foreign_active
            or session_triggers
            or reconciling_ids
            or any(
                cleanup["status"] not in {"complete", "dust"} for cleanup in cleanups
            )
        ),
        "cleanups": cleanups,
        "quarantined_cleanup_executor_ids": sorted(quarantined_terminal_ids),
        "reconciling_executor_ids": reconciling_ids,
        "close_required_executor_ids": [
            row["executor_id"] for row in executor_results if row["close_required"]
        ],
        "reconcile_required_executor_ids": [
            row["executor_id"] for row in executor_results if row["reconcile_required"]
        ],
    }
