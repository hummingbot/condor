"""One read-only LP portfolio, cleanup, capacity, and range-bound snapshot."""

from __future__ import annotations

import asyncio
import json
import re
import time
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from agents.lp_expert.core.portfolio import (
    build_portfolio,
    classify_executor_create_request,
    fetch_all_executors,
)
from agents.lp_expert.core.receipts import ReceiptStore, read_prior_tick_stop
from agents.lp_expert.core.reporting import TraceRecorder, attach_report, safe_error
from agents.lp_expert.core.runtime import (
    bind_wallet,
    decimal_config,
    get_hummingbot_client,
    integer_config,
    resolve_runtime,
)

CATEGORY = "Orca LP Decision Evidence"
VERSION = "8"
_CONTROLLER = re.compile(r"^lp_expert\.orca_(?:e)?[1-9]\d*$")
_TRANSPORT_MAX_CHARS = 1_900


class PriorCloseEvidence(BaseModel):
    """Journal-carried identity that fresh terminal executor evidence must prove."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    executor_id: StrictStr = Field(min_length=1, max_length=256)
    closed_tick: StrictInt = Field(gt=0)
    position_address: StrictStr = Field(min_length=1, max_length=256)
    pool_address: StrictStr = Field(min_length=1, max_length=256)
    base_symbol: StrictStr = Field(min_length=1, max_length=64)
    base_mint: StrictStr = Field(min_length=1, max_length=256)
    base_decimals: StrictInt = Field(ge=0, le=18)
    valuation_price: Decimal = Field(gt=0)


class Config(BaseModel):
    """Return one complete read-only decision snapshot for the exact current tick."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    controller_id: StrictStr
    tick: StrictInt = Field(gt=0)
    prior_closes: list[PriorCloseEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def identity(self) -> "Config":
        if not _CONTROLLER.fullmatch(self.controller_id):
            raise ValueError("controller_id must be an lp_expert.orca controller")
        executor_ids = [item.executor_id for item in self.prior_closes]
        if len(executor_ids) != len(set(executor_ids)):
            raise ValueError("prior close executor identities must be unique")
        return self


# Agent-local routines are loaded from file without module registration.
PriorCloseEvidence.model_rebuild(
    _types_namespace={
        "StrictStr": StrictStr,
        "StrictInt": StrictInt,
        "Decimal": Decimal,
    }
)
Config.model_rebuild(
    _types_namespace={
        "StrictStr": StrictStr,
        "StrictInt": StrictInt,
        "Decimal": Decimal,
        "PriorCloseEvidence": PriorCloseEvidence,
    }
)


def _compact_executor(executor: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: executor.get(key)
        for key in (
            "executor_id",
            "status",
            "lifecycle_state",
            "trading_pair",
            "net_pnl_ratio",
        )
    }
    if executor.get("triggered_by"):
        result["triggered_by"] = executor["triggered_by"]
    if executor.get("close_required"):
        result["close_required"] = True
    if executor.get("reconcile_required"):
        result["reconcile_required"] = True
    return result


def _compact_cleanup(cleanup: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: cleanup.get(key)
        for key in (
            "status",
            "executor_id",
            "closed_tick",
            "capacity_quarantined",
            "residual_quote",
        )
        if key in cleanup
    }
    if cleanup.get("reason"):
        result["reason"] = safe_error(cleanup["reason"], limit=160)
    evidence = cleanup.get("evidence")
    if isinstance(evidence, dict):
        result["evidence"] = {
            key: evidence.get(key)
            for key in (
                "position_address",
                "pool_address",
                "base_symbol",
                "base_mint",
                "base_decimals",
                "residual_base_amount",
                "native_swap_status",
                "close_transaction_hash",
            )
        }
    return result


def _compact_unresolved_operation(
    record: dict[str, Any],
    controller_id: str,
    executor_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    item = {
        key: record.get(key)
        for key in ("operation_id", "operation_kind", "tick", "phase")
    }
    if record.get("reason"):
        item["reason"] = safe_error(record["reason"], limit=120)
    result = record.get("result")
    swap_executor_id = (
        str(result.get("swap_executor_id") or "").strip()
        if isinstance(result, dict)
        else ""
    )
    if record.get("operation_kind") == "swap" and swap_executor_id:
        item["reconcile"] = {
            "routine": "lp_order_request",
            "config": {
                "controller_id": controller_id,
                "operation_id": record.get("operation_id"),
                "swap_executor_id": swap_executor_id,
            },
        }
    if (
        record.get("operation_kind") == "create"
        and record.get("phase") in {"admitted", "submitting", "submitted", "uncertain"}
        and isinstance(result, dict)
    ):
        lp_executor_id = str(result.get("executor_id") or "").strip()
        if not lp_executor_id and isinstance(result.get("executor_request"), dict):
            matches = []
            pending = []
            for row in executor_rows:
                candidate_id = str(
                    row.get("executor_id") or row.get("id") or ""
                ).strip()
                if not candidate_id:
                    continue
                comparison = classify_executor_create_request(
                    row,
                    candidate_id,
                    controller_id,
                    result["executor_request"],
                )
                if comparison["outcome"] == "match":
                    matches.append(candidate_id)
                elif comparison["outcome"] == "pending":
                    pending.append(candidate_id)
            matches = sorted(set(matches))
            if len(matches) == 1:
                lp_executor_id = matches[0]
            elif len(matches) > 1:
                item["reason"] = (
                    "multiple live executors match the frozen create request"
                )
            elif pending:
                item["reason"] = (
                    "candidate executor detail is incomplete for exact create recovery"
                )
        if lp_executor_id:
            item["reconcile"] = {
                "routine": "lp_executor_request",
                "config": {
                    "controller_id": controller_id,
                    "operation_id": record.get("operation_id"),
                    "lp_executor_id": lp_executor_id,
                },
            }
    return item


def _transition_operation_id(
    controller_id: str,
    current_tick: int,
    pool_address: str,
    transition: str,
) -> str:
    safe_controller = controller_id.replace(".", "_")
    safe_target = re.sub(r"[^A-Za-z0-9_-]", "_", pool_address)
    operation_id = f"{safe_controller}-t{current_tick}-{safe_target}-{transition}"
    if (
        not pool_address
        or safe_target != pool_address
        or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", operation_id)
    ):
        raise ValueError(f"exact {transition} operation ID cannot be derived safely")
    return operation_id


def _unconsumed_preparation_capsule(
    record: dict[str, Any],
    *,
    controller_id: str,
    current_tick: int,
    create_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the next exact create or restoration preparation instruction."""

    intent = record.get("intent")
    result = record.get("result")
    receipt = result.get("receipt") if isinstance(result, dict) else None
    if not isinstance(intent, dict) or not isinstance(receipt, dict):
        raise ValueError("unconsumed preparation evidence is incomplete")
    pool_address = str(intent.get("pool_address") or "").strip()
    amount = str(receipt.get("output_amount") or "").strip()
    transaction_hash = str(receipt.get("transaction_hash") or "").strip()
    if not amount or not transaction_hash:
        raise ValueError("unconsumed preparation amount or transaction is missing")
    preparation_operation_id = record.get("operation_id")
    consumers = [
        candidate
        for candidate in create_records
        if isinstance(candidate.get("intent"), dict)
        and candidate["intent"].get("preparation_operation_id")
        == preparation_operation_id
    ]
    if any(
        candidate.get("phase") != "rejected_before_submit"
        or candidate.get("mutation_possible") is not False
        for candidate in consumers
    ):
        raise ValueError("unconsumed preparation has conflicting create evidence")
    capsule = {
        "operation_id": record.get("operation_id"),
        "operation_kind": "swap",
        "tick": record.get("tick"),
        "prepared_inventory": {
            "pool_address": pool_address,
            "base_symbol": intent.get("base_symbol"),
            "base_mint": intent.get("base_mint"),
            "base_decimals": intent.get("base_decimals"),
            "amount": amount,
            "preparation_transaction_hash": transaction_hash,
        },
    }
    if not consumers:
        deployment = result.get("deployment_input")
        if (
            not isinstance(deployment, dict)
            or deployment.get("preparation_operation_id") != preparation_operation_id
            or not isinstance(deployment.get("candidate"), dict)
            or deployment.get("amount_quote") in (None, "")
            or deployment.get("range_half_width_pct") in (None, "")
        ):
            capsule.update(
                {
                    "phase": "manual_review",
                    "reason": (
                        "confirmed preparation lacks exact deployment "
                        "continuation input"
                    ),
                }
            )
            return capsule
        confirmed_tick = result.get("confirmed_tick")
        if (
            result.get("same_tick_lp_create_allowed") is False
            and confirmed_tick == current_tick
        ):
            capsule.update(
                {
                    "phase": "confirmed_pending_create",
                    "reason": "LP create continuation is due on the following tick",
                }
            )
            return capsule
        capsule.update(
            {
                "phase": "confirmed_pending_create",
                "reason": (
                    "confirmed preparation has not yet entered LP create admission"
                ),
                "continue_create": {
                    "routine": "lp_executor_request",
                    "config": {
                        "controller_id": controller_id,
                        "tick": current_tick,
                        "operation_id": _transition_operation_id(
                            controller_id,
                            current_tick,
                            pool_address,
                            "create",
                        ),
                        "preparation_operation_id": preparation_operation_id,
                    },
                },
            }
        )
        return capsule
    rejected_tick = max(int(candidate["tick"]) for candidate in consumers)
    capsule.update(
        {
            "phase": "confirmed_pending_restore",
            "reason": (
                "LP create was rejected before submission; restore exact "
                "preparation output"
            ),
            "restore": {
                "routine": "lp_order_request",
                "config": {
                    "controller_id": controller_id,
                    "operation_id": _transition_operation_id(
                        controller_id,
                        current_tick,
                        pool_address,
                        "restore",
                    ),
                    "reason": "inventory_restoration",
                    "pool_address": pool_address,
                    "base_symbol": intent.get("base_symbol"),
                    "base_mint": intent.get("base_mint"),
                    "base_decimals": intent.get("base_decimals"),
                    "amount": amount,
                    "attributed_base_amount": amount,
                    "attribution_operation_id": record.get("operation_id"),
                },
            },
        }
    )
    if rejected_tick >= current_tick:
        capsule.pop("restore")
        capsule["reason"] = "exact restoration is due on the following tick"
    return capsule


def _compact_portfolio(portfolio: dict[str, Any]) -> dict[str, Any]:
    unresolved = []
    for record in portfolio.get("unresolved_operations", []):
        item = {
            key: record.get(key)
            for key in ("operation_id", "operation_kind", "tick", "phase")
        }
        if record.get("reason"):
            item["reason"] = safe_error(record["reason"], limit=120)
        if isinstance(record.get("reconcile"), dict):
            item["reconcile"] = record["reconcile"]
        if isinstance(record.get("restore"), dict):
            item["restore"] = record["restore"]
        if isinstance(record.get("continue_create"), dict):
            item["continue_create"] = record["continue_create"]
        if isinstance(record.get("prepared_inventory"), dict):
            item["prepared_inventory"] = record["prepared_inventory"]
        unresolved.append(item)
    result = {
        "session": portfolio.get("session"),
        "foreign_active_executor_ids": portfolio.get("foreign_active_executor_ids", []),
        "active_count": portfolio.get("active_count"),
        "active_exposure_quote": portfolio.get("active_exposure_quote"),
        "available_slots": portfolio.get("available_slots"),
        "remaining_quote_budget": portfolio.get("remaining_quote_budget"),
        "deployment_blocked": portfolio.get("deployment_blocked"),
        "cleanups": [_compact_cleanup(item) for item in portfolio.get("cleanups", [])],
        "close_required_executor_ids": portfolio.get("close_required_executor_ids", []),
        "reconcile_required_executor_ids": portfolio.get(
            "reconcile_required_executor_ids", []
        ),
        "unresolved_operations": unresolved,
    }
    if not unresolved:
        result["executors"] = [
            _compact_executor(item) for item in portfolio.get("executors", [])
        ]
    for key in (
        "occupied_pools",
        "quarantined_cleanup_executor_ids",
        "reconciling_executor_ids",
    ):
        if portfolio.get(key):
            result[key] = portfolio[key]
    return result


def _compact_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    result = {
        "schema": "lp_snapshot.v1",
        "status": payload.get("status"),
        "controller_id": payload.get("controller_id"),
        "tick": payload.get("tick"),
        "mutation": False,
        "retry_allowed": False,
        "scan_allowed": bool(payload.get("scan_allowed")),
        "cleanup_tick": bool(payload.get("cleanup_tick")),
    }
    if payload.get("reason"):
        result["reason"] = safe_error(payload["reason"])
    portfolio = payload.get("portfolio")
    if isinstance(portfolio, dict):
        result["portfolio"] = _compact_portfolio(portfolio)
    constraints = payload.get("selection_constraints")
    portfolio_unresolved = (
        portfolio.get("unresolved_operations") if isinstance(portfolio, dict) else None
    )
    if isinstance(constraints, dict) and not portfolio_unresolved:
        allocation = constraints["allocation_quote"]
        range_width = constraints["range_half_width_pct"]
        result["selection_constraints"] = {
            "allocation_quote": [
                allocation["minimum"],
                allocation["maximum"],
                allocation["remaining_portfolio_budget"],
            ],
            "range_half_width_pct": [
                range_width["minimum"],
                range_width["maximum"],
            ],
            "deployments": [
                constraints["configured_deployments_per_tick"],
                constraints["available_deployments_this_tick"],
            ],
        }
    result["report_id"] = payload.get("report_id")
    result["report_error"] = payload.get("report_error")
    return result


def _model_result(payload: dict[str, Any]) -> str:
    compact = _compact_snapshot(payload)
    encoded = json.dumps(compact, default=str, separators=(",", ":"))
    if len(encoded) <= _TRANSPORT_MAX_CHARS:
        return encoded
    portfolio = compact.get("portfolio")
    unresolved = (
        portfolio.get("unresolved_operations") if isinstance(portfolio, dict) else None
    )
    if isinstance(unresolved, list) and len(unresolved) == 1:
        operation = unresolved[0]
        action = next(
            (
                {key: operation[key]}
                for key in ("reconcile", "continue_create", "restore")
                if isinstance(operation.get(key), dict)
            ),
            {},
        )
        recovery = {
            "schema": "lp_snapshot.v1",
            "status": compact.get("status"),
            "controller_id": compact.get("controller_id"),
            "tick": compact.get("tick"),
            "mutation": False,
            "retry_allowed": False,
            "scan_allowed": False,
            "cleanup_tick": compact.get("cleanup_tick"),
            "portfolio": {
                "active_count": portfolio.get("active_count"),
                "available_slots": portfolio.get("available_slots"),
                "deployment_blocked": True,
                "unresolved_operations": [
                    {
                        key: operation.get(key)
                        for key in (
                            "operation_id",
                            "operation_kind",
                            "tick",
                            "phase",
                        )
                    }
                    | action
                ],
            },
            "report_id": compact.get("report_id"),
            "report_error": compact.get("report_error"),
        }
        encoded = json.dumps(recovery, default=str, separators=(",", ":"))
        if len(encoded) <= _TRANSPORT_MAX_CHARS:
            return encoded
    fallback = {
        "schema": "lp_snapshot.v1",
        "status": "incomplete",
        "controller_id": payload.get("controller_id"),
        "tick": payload.get("tick"),
        "mutation": False,
        "retry_allowed": False,
        "scan_allowed": False,
        "cleanup_tick": bool(payload.get("cleanup_tick")),
        "reason": (
            "compact snapshot exceeded the safe model transport budget; "
            "HOLD and review the complete report"
        ),
        "report_id": payload.get("report_id"),
        "report_error": (
            safe_error(payload["report_error"], limit=200)
            if payload.get("report_error")
            else None
        ),
    }
    return json.dumps(fallback, default=str, separators=(",", ":"))


async def run(config: Config, context: Any) -> str:
    trace = TraceRecorder()
    scope = None
    payload: dict[str, Any]
    try:
        with trace.stage("runtime_authority") as facts:
            scope = resolve_runtime(config.controller_id)
            if config.tick != scope.current_tick:
                raise ValueError("requested tick is not the current engine tick")
            max_open = integer_config(scope.config, "max_open_executors")
            if len(config.prior_closes) > max_open:
                raise ValueError(
                    "prior close evidence exceeds current configured executor capacity"
                )
            facts.update(
                {
                    "controller_id": scope.controller_id,
                    "execution_mode": scope.execution_mode,
                    "tick": scope.current_tick,
                    "max_open_executors": max_open,
                }
            )
        with trace.stage("client_and_wallet_binding") as facts:
            client = await get_hummingbot_client(scope)
            scope = await bind_wallet(scope, client)
            facts.update({"network": scope.network, "wallet_bound": True})
        with trace.stage("current_session_operation_reconciliation") as facts:
            receipt_store = ReceiptStore(scope, read_only=True)
            unresolved_records = receipt_store.unresolved_operations()
            unconsumed_preparations = receipt_store.unconsumed_preparations()
            create_records = receipt_store.list_records("create")
            facts.update(
                {
                    "unresolved_count": len(unresolved_records),
                    "unconsumed_preparation_count": len(unconsumed_preparations),
                    "operation_ids": [
                        record["operation_id"] for record in unresolved_records
                    ],
                }
            )
        with trace.stage("executor_fetch_and_pagination") as facts:
            rows = await fetch_all_executors(client, scope.account_name)
            facts["executor_rows"] = len(rows)
        with trace.stage("operation_recovery_capsules") as facts:
            unresolved_operations = [
                _compact_unresolved_operation(
                    record,
                    scope.controller_id,
                    rows,
                )
                for record in unresolved_records
            ]
            unresolved_operations.extend(
                _unconsumed_preparation_capsule(
                    record,
                    controller_id=scope.controller_id,
                    current_tick=scope.current_tick,
                    create_records=create_records,
                )
                for record in unconsumed_preparations
            )
            facts.update(
                {
                    "capsule_count": sum(
                        isinstance(record.get("reconcile"), dict)
                        for record in unresolved_operations
                    ),
                    "create_capsule_count": sum(
                        record.get("operation_kind") == "create"
                        and isinstance(record.get("reconcile"), dict)
                        for record in unresolved_operations
                    ),
                    "continuation_capsule_count": sum(
                        isinstance(record.get("continue_create"), dict)
                        for record in unresolved_operations
                    ),
                    "restoration_capsule_count": sum(
                        isinstance(record.get("restore"), dict)
                        for record in unresolved_operations
                    ),
                }
            )
        with trace.stage("prior_close_stop_lookup") as facts:
            prior_closes = []
            proof_errors = 0
            for evidence in config.prior_closes:
                prior_close = evidence.model_dump(mode="python")
                try:
                    prior_close["stop_proof"] = read_prior_tick_stop(
                        scope,
                        evidence.executor_id,
                        evidence.closed_tick,
                    )
                except Exception as exc:
                    proof_errors += 1
                    prior_close["stop_proof_error"] = f"{type(exc).__name__}: {exc}"
                prior_closes.append(prior_close)
            facts.update(
                {
                    "requested": len(prior_closes),
                    "proven": len(prior_closes) - proof_errors,
                    "unproven": proof_errors,
                    "_outcome": "skipped" if not prior_closes else "complete",
                }
            )
        with trace.stage("portfolio_and_cleanup_classification") as facts:
            if scope.session_dir is not None:
                session_config = scope.session_dir / "config.yml"
                if not session_config.is_file() or session_config.is_symlink():
                    raise ValueError(
                        "stable current-session start evidence is unavailable"
                    )
                session_started_at = session_config.stat().st_mtime
            else:
                session_started_at = time.time()
            portfolio = build_portfolio(
                rows=rows,
                controller_id=scope.controller_id,
                strategy_config=dict(scope.config),
                session_started_at=session_started_at,
                current_tick=scope.current_tick,
                prior_closes=prior_closes,
            )
            facts.update(
                {
                    "active_count": portfolio["active_count"],
                    "available_slots": portfolio["available_slots"],
                    "close_required": len(portfolio["close_required_executor_ids"]),
                    "cleanup_statuses": [
                        cleanup["status"] for cleanup in portfolio["cleanups"]
                    ],
                }
            )
            portfolio["unresolved_operations"] = unresolved_operations
            if unresolved_operations:
                portfolio["deployment_blocked"] = True
        cleanup_tick = bool(config.prior_closes)
        scan_allowed = (
            not cleanup_tick
            and not unresolved_operations
            and not portfolio["deployment_blocked"]
            and portfolio["available_slots"] > 0
            and not portfolio["close_required_executor_ids"]
            and not portfolio["reconcile_required_executor_ids"]
        )
        minimum_allocation = decimal_config(
            scope.config, "min_quote_per_executor", positive=True
        )
        capital_slots = int(portfolio["remaining_quote_budget"] // minimum_allocation)
        configured_deployments = integer_config(
            scope.config, "max_slot_deployments_per_tick"
        )
        available_deployments = (
            min(
                configured_deployments,
                portfolio["available_slots"],
                capital_slots,
            )
            if scan_allowed
            else 0
        )
        selection_constraints = {
            "allocation_quote": {
                "minimum": minimum_allocation,
                "maximum": decimal_config(
                    scope.config, "max_quote_per_executor", positive=True
                ),
                "remaining_portfolio_budget": portfolio["remaining_quote_budget"],
            },
            "range_half_width_pct": {
                "minimum": decimal_config(
                    scope.config,
                    "minimum_range_half_width_pct",
                    positive=True,
                ),
                "maximum": decimal_config(
                    scope.config,
                    "maximum_range_half_width_pct",
                    positive=True,
                ),
            },
            "maximum_slippage_pct": decimal_config(
                scope.config, "max_slippage_pct", positive=True
            ),
            "configured_deployments_per_tick": configured_deployments,
            "available_deployments_this_tick": available_deployments,
        }
        with trace.stage("deployment_selection_constraints") as facts:
            facts.update(
                {
                    "capital_slots": capital_slots,
                    "available_deployments": available_deployments,
                    "scan_allowed": scan_allowed,
                }
            )
        payload = {
            "status": "complete",
            "controller_id": scope.controller_id,
            "tick": scope.current_tick,
            "observed_at": trace.finished_at.isoformat(),
            "mutation": False,
            "retry_allowed": False,
            "portfolio": portfolio,
            "selection_constraints": selection_constraints,
            "scan_allowed": scan_allowed and available_deployments > 0,
            "cleanup_tick": cleanup_tick,
        }
    except asyncio.CancelledError:
        payload = {
            "status": "cancelled",
            "controller_id": config.controller_id,
            "tick": config.tick,
            "observed_at": trace.finished_at.isoformat(),
            "reason": "CancelledError: snapshot invocation was cancelled",
            "mutation": False,
            "retry_allowed": False,
            "portfolio": None,
            "scan_allowed": False,
        }
    except Exception as exc:
        payload = {
            "status": "rejected",
            "controller_id": config.controller_id,
            "tick": config.tick,
            "observed_at": trace.finished_at.isoformat(),
            "reason": f"{type(exc).__name__}: {exc}",
            "mutation": False,
            "retry_allowed": False,
            "portfolio": None,
            "scan_allowed": False,
        }
    reported_payload = await attach_report(
        payload,
        title="LP Portfolio Snapshot",
        source="lp_snapshot",
        version=VERSION,
        routine_input=config.model_dump(mode="json"),
        trace=trace,
        scope=scope,
        links={
            "prior_close_executor_ids": [
                evidence.executor_id for evidence in config.prior_closes
            ]
        },
    )
    return _model_result(reported_payload)
