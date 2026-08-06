"""One read-only LP portfolio, cleanup, candidate, and range-plan snapshot."""

from __future__ import annotations

import asyncio
import json
import re
import time
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from agents.lp_expert.core import orca
from agents.lp_expert.core.planner import PlanRequest, build_plan
from agents.lp_expert.core.portfolio import build_portfolio, fetch_all_executors
from agents.lp_expert.core.receipts import ReceiptStore, read_prior_tick_stop
from agents.lp_expert.core.reporting import TraceRecorder, attach_report
from agents.lp_expert.core.runtime import (
    bind_wallet,
    get_hummingbot_client,
    resolve_runtime,
)

CATEGORY = "Orca LP Decision Evidence"
VERSION = "2"
_CONTROLLER = re.compile(r"^lp_expert\.orca_(?:e)?[1-9]\d*$")


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
    candidate_limit: StrictInt = Field(default=3, ge=1, le=5)
    amount_quote: Decimal = Field(gt=0)
    range_half_width_pct: Decimal = Field(gt=0)
    prior_closes: list[PriorCloseEvidence] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def identity(self) -> "Config":
        if not _CONTROLLER.fullmatch(self.controller_id):
            raise ValueError("controller_id must be an lp_expert.orca controller")
        for field in ("amount_quote", "range_half_width_pct"):
            value = Decimal(getattr(self, field))
            if not value.is_finite():
                raise ValueError(f"{field} must be finite")
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


def _plan(
    candidate: dict[str, Any], config: Config, limits: dict[str, Any]
) -> dict[str, Any]:
    token = candidate["token_a"]
    return build_plan(
        PlanRequest(
            pool_address=candidate["pool_address"],
            base_symbol=token["symbol"],
            base_mint=token["mint"],
            base_decimals=token["decimals"],
            current_price=candidate["price"],
            tick_spacing=candidate["tick_spacing"],
            amount_quote=config.amount_quote,
            range_half_width_pct=config.range_half_width_pct,
            minimum_range_half_width_pct=limits["minimum_range_half_width_pct"],
            maximum_range_half_width_pct=limits["maximum_range_half_width_pct"],
            rebalance_threshold_pct=limits["rebalance_threshold_pct"],
            max_slippage_pct=limits["max_slippage_pct"],
        )
    )


async def run(config: Config, context: Any) -> str:
    trace = TraceRecorder()
    scope = None
    payload: dict[str, Any]
    try:
        with trace.stage("runtime_authority") as facts:
            scope = resolve_runtime(config.controller_id)
            if config.tick != scope.current_tick:
                raise ValueError("requested tick is not the current engine tick")
            facts.update(
                {
                    "controller_id": scope.controller_id,
                    "execution_mode": scope.execution_mode,
                    "tick": scope.current_tick,
                }
            )
        with trace.stage("client_and_wallet_binding") as facts:
            client = await get_hummingbot_client(scope)
            scope = await bind_wallet(scope, client)
            facts.update({"network": scope.network, "wallet_bound": True})
        with trace.stage("current_session_operation_reconciliation") as facts:
            receipt_store = ReceiptStore(scope, read_only=True)
            unresolved_records = receipt_store.unresolved_operations()
            unresolved_operations = [
                {
                    key: record.get(key)
                    for key in (
                        "operation_id",
                        "operation_kind",
                        "tick",
                        "phase",
                        "reason",
                    )
                }
                for record in unresolved_records
            ]
            facts.update(
                {
                    "unresolved_count": len(unresolved_operations),
                    "operation_ids": [
                        record["operation_id"] for record in unresolved_operations
                    ],
                }
            )
        with trace.stage("executor_fetch_and_pagination") as facts:
            rows = await fetch_all_executors(client, scope.account_name)
            facts["executor_rows"] = len(rows)
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
        candidate_allowed = (
            not cleanup_tick
            and not unresolved_operations
            and not portfolio["deployment_blocked"]
            and portfolio["available_slots"] > 0
            and not portfolio["close_required_executor_ids"]
            and not portfolio["reconcile_required_executor_ids"]
        )
        scan = {
            "status": "skipped",
            "deployable": False,
            "source_coverage": None,
            "universe": None,
            "technical_rejections": {},
            "candidates": [],
        }
        token_rejections: list[dict[str, Any]] = []
        plans: list[dict[str, Any]] = []
        with trace.stage("orca_candidate_scan") as facts:
            if not candidate_allowed:
                facts.update(
                    {
                        "_outcome": "skipped",
                        "reason": (
                            "following_tick_cleanup_priority"
                            if cleanup_tick
                            else "portfolio_not_deployable"
                        ),
                    }
                )
            else:
                minimum = Decimal(str(scope.config["min_quote_per_executor"]))
                maximum = Decimal(str(scope.config["max_quote_per_executor"]))
                if not minimum <= config.amount_quote <= maximum:
                    raise ValueError(
                        "snapshot allocation is outside per-executor limits"
                    )
                if config.amount_quote > portfolio["remaining_quote_budget"]:
                    raise ValueError(
                        "snapshot allocation exceeds remaining quote budget"
                    )
                scan = await orca.scan_pools(config.candidate_limit)
                facts.update(scan["source_coverage"])
        with trace.stage("registered_token_filtering") as facts:
            if scan["status"] == "skipped":
                facts.update({"_outcome": "skipped", "reason": "scan_not_run"})
            elif scan["status"] != "complete":
                facts.update(
                    {"_outcome": "incomplete", "reason": "Orca coverage incomplete"}
                )
            else:
                registry = await client.gateway.get_network_tokens(scope.network)
                accepted, token_rejections = orca.filter_registered_tokens(
                    scan["candidates"], registry
                )
                scan["candidates"] = accepted
                scan["deployable"] = bool(accepted)
                facts.update(
                    {
                        "accepted": len(accepted),
                        "rejected": len(token_rejections),
                    }
                )
        with trace.stage("candidate_plan_calculation") as facts:
            if not scan["candidates"]:
                facts.update({"_outcome": "skipped", "reason": "no valid candidates"})
            else:
                for candidate in scan["candidates"]:
                    plans.append(
                        {
                            **candidate,
                            "plan": _plan(candidate, config, dict(scope.config)),
                        }
                    )
                facts["planned_candidates"] = len(plans)
        status = (
            "complete" if scan["status"] in {"complete", "skipped"} else "incomplete"
        )
        payload = {
            "status": status,
            "controller_id": scope.controller_id,
            "tick": scope.current_tick,
            "observed_at": trace.finished_at.isoformat(),
            "mutation": False,
            "retry_allowed": False,
            "portfolio": portfolio,
            "candidate_scan": {
                key: value
                for key, value in scan.items()
                if key not in {"candidates", "deployable"}
            },
            "token_rejections": token_rejections,
            "candidates": plans,
            "deployable": status == "complete"
            and candidate_allowed
            and bool(plans)
            and scan["deployable"],
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
            "candidates": [],
            "deployable": False,
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
            "candidates": [],
            "deployable": False,
        }
    payload = await attach_report(
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
    return json.dumps(payload, default=str, separators=(",", ":"), sort_keys=True)
