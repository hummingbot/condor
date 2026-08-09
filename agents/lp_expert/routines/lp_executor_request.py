"""Validate or reconcile one exact native Orca LP executor request; never submit."""

from __future__ import annotations

import asyncio
import copy
import json
import re
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from agents.lp_expert.core import orca
from agents.lp_expert.core.planner import (
    apply_existing_base_inventory,
    build_candidate_plan,
)
from agents.lp_expert.core.portfolio import (
    classify_executor_create_request,
    decimal_value,
    fetch_all_executors,
    normalize_executor,
    positive_integer,
)
from agents.lp_expert.core.receipts import (
    OPERATION_ID_PATTERN,
    ReceiptStore,
    controller_mutation_lock,
)
from agents.lp_expert.core.reporting import TraceRecorder, attach_report, safe_error
from agents.lp_expert.core.runtime import (
    bind_wallet,
    get_hummingbot_client,
    pool_tvl_policy,
    refresh_balances,
    resolve_runtime,
)

CATEGORY = "Non-Submitting LP Executor Request"
VERSION = "9"
_CONTROLLER = re.compile(r"^lp_expert\.orca_(?:e)?[1-9]\d*$")
_TRANSPORT_MAX_CHARS = 1_900


class Config(BaseModel):
    """Freeze a non-submitting LP executor request or reconcile its exact ID; never submit."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    controller_id: StrictStr
    tick: StrictInt | None = Field(default=None, gt=0)
    operation_id: StrictStr = Field(
        min_length=8,
        max_length=128,
        pattern=OPERATION_ID_PATTERN,
    )
    candidate: dict[str, Any] | None = None
    amount_quote: Decimal | None = Field(default=None, gt=0)
    range_half_width_pct: Decimal | None = Field(default=None, gt=0)
    preparation_operation_id: StrictStr | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=OPERATION_ID_PATTERN,
    )
    lp_executor_id: StrictStr | None = None

    @model_validator(mode="after")
    def identity(self) -> "Config":
        if not _CONTROLLER.fullmatch(self.controller_id):
            raise ValueError("controller_id must be an lp_expert.orca controller")
        if self.lp_executor_id is not None and (
            not self.lp_executor_id
            or self.lp_executor_id != self.lp_executor_id.strip()
        ):
            raise ValueError("LP executor identity must be exact and non-empty")
        plan_fields = (
            self.candidate,
            self.amount_quote,
            self.range_half_width_pct,
        )
        supplied_plan = [value is not None for value in plan_fields]
        if any(supplied_plan) and not all(supplied_plan):
            raise ValueError(
                "candidate, amount_quote, and range_half_width_pct must be "
                "supplied together"
            )
        if (self.tick is None) != (self.preparation_operation_id is None):
            raise ValueError(
                "tick and preparation_operation_id must be supplied together"
            )
        if any(supplied_plan) and self.tick is None:
            raise ValueError(
                "a complete executor request requires tick and "
                "preparation_operation_id"
            )
        if self.tick is not None and not any(supplied_plan) and self.lp_executor_id:
            raise ValueError(
                "compact preparation continuation cannot include lp_executor_id"
            )
        if self.tick is None and self.lp_executor_id is None:
            raise ValueError(
                "executor request inputs or an exact lp_executor_id are required"
            )
        if self.candidate is not None and not isinstance(self.candidate, dict):
            raise ValueError("candidate must be a snapshot object")
        if (
            self.amount_quote is not None
            and self.range_half_width_pct is not None
            and (
                not self.amount_quote.is_finite()
                or not self.range_half_width_pct.is_finite()
            )
        ):
            raise ValueError("selected plan values must be finite")
        return self

    @property
    def recovery_only(self) -> bool:
        return self.tick is None

    @property
    def continuation_only(self) -> bool:
        return self.tick is not None and self.candidate is None


# Agent-local routines are loaded from file without module registration.
Config.model_rebuild(
    _types_namespace={
        "StrictStr": StrictStr,
        "StrictInt": StrictInt,
        "Decimal": Decimal,
        "Any": Any,
    }
)


def _preparation(
    store: ReceiptStore,
    preparation_operation_id: str,
    create_operation_id: str,
    *,
    candidate: dict[str, Any],
    amount_quote: Decimal,
    range_half_width_pct: Decimal,
    strategy_config: Any,
) -> tuple[dict[str, Any], Decimal, Decimal, dict[str, Any]]:
    record = store.read_confirmed_preparation(preparation_operation_id)
    intent = record.get("intent")
    result = record.get("result")
    receipt = result.get("receipt") if isinstance(result, dict) else None
    allocation = (
        result.get("inventory_allocation") if isinstance(result, dict) else None
    )
    if not isinstance(intent, dict):
        raise ValueError("confirmed preparation receipt content is unavailable")
    existing_base = (
        decimal_value(
            intent["attributed_base_amount"],
            "authorized existing base",
        )
        if intent.get("attributed_base_amount") is not None
        else Decimal(0)
    )
    selection_plan = build_candidate_plan(
        candidate,
        amount_quote=amount_quote,
        range_half_width_pct=range_half_width_pct,
        strategy_config=strategy_config,
    )
    if existing_base > 0:
        selection_plan = apply_existing_base_inventory(
            selection_plan,
            existing_base,
        )
    expected = selection_plan["identity"]
    inputs = selection_plan["inputs"]
    if (
        intent.get("reason") != "inventory_preparation"
        or str(intent.get("side") or "").upper() != "BUY"
        or intent.get("pool_address") != expected["pool_address"]
        or intent.get("trading_pair") != expected["trading_pair"]
        or intent.get("base_mint") != expected["base_mint"]
        or intent.get("plan_digest") != selection_plan["plan_digest"]
        or intent.get("amount_quote") != str(inputs["amount_quote"])
        or intent.get("range_half_width_pct") != str(inputs["range_half_width_pct"])
    ):
        raise ValueError("preparation receipt is not attributed to this selection")
    consumers = [
        value
        for value in store.list_records("create")
        if value.get("operation_id") != create_operation_id
        and isinstance(value.get("intent"), dict)
        and value["intent"].get("preparation_operation_id") == preparation_operation_id
    ]
    if consumers:
        raise ValueError("preparation receipt is already assigned to another create")
    if isinstance(receipt, dict):
        input_amount = decimal_value(
            receipt.get("input_amount"), "preparation quote input", positive=True
        )
        swapped_base = decimal_value(
            receipt.get("output_amount"), "preparation base output", positive=True
        )
        if not str(receipt.get("transaction_hash") or "").strip():
            raise ValueError("preparation transaction identity is unavailable")
    else:
        if (
            not isinstance(allocation, dict)
            or allocation.get("source") != "existing_wallet_balance"
            or decimal_value(
                allocation.get("attributed_base_amount"),
                "existing inventory allocation",
                positive=True,
            )
            != existing_base
            or decimal_value(intent.get("amount"), "recorded swap amount") != 0
        ):
            raise ValueError("confirmed no-swap preparation allocation is unavailable")
        input_amount = Decimal(0)
        swapped_base = Decimal(0)
    return (
        record,
        input_amount,
        existing_base + swapped_base,
        selection_plan,
    )


def _continued_deployment_input(
    store: ReceiptStore,
    preparation_operation_id: str,
) -> tuple[dict[str, Any], Decimal, Decimal]:
    """Load the immutable deployment choice from one confirmed preparation."""

    record = store.read_confirmed_preparation(preparation_operation_id)
    result = record.get("result")
    deployment = result.get("deployment_input") if isinstance(result, dict) else None
    if (
        not isinstance(deployment, dict)
        or deployment.get("preparation_operation_id") != preparation_operation_id
        or not isinstance(deployment.get("candidate"), dict)
    ):
        raise ValueError(
            "confirmed preparation lacks exact deployment continuation input"
        )
    amount_quote = decimal_value(
        deployment.get("amount_quote"),
        "continued allocation",
        positive=True,
    )
    range_half_width_pct = decimal_value(
        deployment.get("range_half_width_pct"),
        "continued range half width",
        positive=True,
    )
    return copy.deepcopy(deployment["candidate"]), amount_quote, range_half_width_pct


def _schema_fields(value: Any) -> set[str]:
    if not isinstance(value, dict):
        raise ValueError("LP executor schema is invalid")
    value = value.get("result", value)
    fields = value.get("fields") if isinstance(value, dict) else None
    if fields is not None:
        if not isinstance(fields, list) or not all(
            isinstance(row, dict) and isinstance(row.get("name"), str) for row in fields
        ):
            raise ValueError("LP executor schema fields are invalid")
        return {row["name"] for row in fields}
    properties = value.get("properties") if isinstance(value, dict) else None
    nested = value.get("schema") if isinstance(value, dict) else None
    if not isinstance(properties, dict) and isinstance(nested, dict):
        properties = nested.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise ValueError("LP executor schema has no properties")
    return set(properties)


def _balance(
    rows: list[dict[str, Any]], symbol: str, mint: str | None = None
) -> Decimal:
    found = []
    mint_aware = False
    for row in rows:
        row_symbol = str(row.get("token") or row.get("symbol") or "").strip()
        row_mint = str(
            row.get("mint") or row.get("token_address") or row.get("address") or ""
        ).strip()
        mint_aware |= bool(row_mint)
        amount = row.get(
            "available_units",
            row.get("available", row.get("available_balance", row.get("units"))),
        )
        if amount is None:
            continue
        if (mint and row_mint == mint) or (
            not mint and row_symbol.casefold() == symbol.casefold()
        ):
            found.append(decimal_value(amount, f"{symbol} available balance"))
    if mint and not found and not mint_aware:
        return _balance(rows, symbol)
    if len(found) != 1 or found[0] < 0:
        raise ValueError(f"{symbol} balance is not uniquely available")
    return found[0]


def _record_result(record: dict[str, Any]) -> dict[str, Any]:
    result = record.get("result")
    return copy.deepcopy(result) if isinstance(result, dict) else {}


def _record_executor_request(
    record: dict[str, Any],
    scope: Any,
) -> dict[str, Any]:
    result = _record_result(record)
    request = result.get("executor_request")
    executor_config = (
        request.get("executor_config") if isinstance(request, dict) else None
    )
    intent = record.get("intent")
    if (
        not isinstance(request, dict)
        or request.get("action") != "create"
        or request.get("executor_type") != "lp_executor"
        or request.get("account_name") != scope.account_name
        or request.get("controller_id") != scope.controller_id
        or not isinstance(executor_config, dict)
        or executor_config.get("type") != "lp_executor"
        or executor_config.get("controller_id") != scope.controller_id
        or not isinstance(intent, dict)
        or executor_config.get("pool_address") != intent.get("pool_address")
        or executor_config.get("trading_pair") != intent.get("trading_pair")
    ):
        raise ValueError("frozen create executor request is invalid")
    return copy.deepcopy(request)


def _record_identity(
    store: ReceiptStore,
    record: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    if record.get("operation_kind") != "create":
        raise ValueError("operation ID belongs to a different operation kind")
    identity = store.identity(
        operation_id=record["operation_id"],
        operation_kind="create",
        intent=record["intent"],
        tick=record["tick"],
    )
    try:
        validated = store.read(identity)
    except ValueError as exc:
        raise ValueError(
            "existing create operation identity conflicts with its stored receipt"
        ) from exc
    if validated is None:
        raise ValueError("existing create receipt disappeared")
    return identity, validated


def _validate_replay_inputs(
    config: Config,
    scope: Any,
    record: dict[str, Any],
) -> dict[str, Any] | None:
    """Validate a full replay without refreshing or changing the admitted plan."""

    if config.recovery_only:
        return None
    if config.continuation_only:
        if (
            config.tick != record.get("tick")
            or not isinstance(record.get("intent"), dict)
            or config.preparation_operation_id
            != record["intent"].get("preparation_operation_id")
        ):
            raise ValueError(
                "existing create operation identity conflicts with the "
                "continued request"
            )
        return None
    if (
        config.tick != record.get("tick")
        or not isinstance(record.get("intent"), dict)
        or config.preparation_operation_id
        != record["intent"].get("preparation_operation_id")
        or format(config.amount_quote, "f") != record["intent"].get("amount_quote")
        or format(config.range_half_width_pct, "f")
        != record["intent"].get("range_half_width_pct")
    ):
        raise ValueError(
            "existing create operation identity conflicts with the current request"
        )
    selection_plan = build_candidate_plan(
        config.candidate,
        amount_quote=config.amount_quote,
        range_half_width_pct=config.range_half_width_pct,
        strategy_config=scope.config,
    )
    intent = record["intent"]
    if (
        selection_plan["plan_digest"] != intent.get("selection_plan_digest")
        or selection_plan["identity"]["pool_address"] != intent.get("pool_address")
        or selection_plan["identity"]["trading_pair"] != intent.get("trading_pair")
    ):
        raise ValueError(
            "existing create operation identity conflicts with the current request"
        )
    return selection_plan


async def _reconcile_existing_create(
    *,
    config: Config,
    scope: Any,
    client: Any,
    store: ReceiptStore,
    identity: Any,
    record: dict[str, Any],
    trace: TraceRecorder,
) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    executor_request = _record_executor_request(record, scope)
    frozen_result = _record_result(record)
    phase = str(record.get("phase"))
    recorded_id = str(frozen_result.get("executor_id") or "").strip()
    supplied_id = str(config.lp_executor_id or "").strip()
    if supplied_id and recorded_id and supplied_id != recorded_id:
        return (
            {
                "status": "manual_review",
                "executor_id": recorded_id,
                "mutation": True,
                "retry_allowed": False,
                "reason": (
                    "native LP executor identity conflicts with the operation receipt"
                ),
            },
            identity,
            record,
        )
    executor_id = supplied_id or recorded_id
    if phase in {"rejected_before_submit", "manual_review", "ambiguous"}:
        return (
            {
                "status": "manual_review",
                "executor_id": executor_id or None,
                "mutation": bool(record.get("mutation_possible")),
                "retry_allowed": False,
                "reason": (
                    record.get("reason")
                    or f"recorded {phase} create outcome requires manual review"
                ),
            },
            identity,
            record,
        )
    if phase == "confirmed":
        return (
            {
                "status": "confirmed",
                "executor_id": recorded_id or None,
                "mutation": True,
                "retry_allowed": False,
                "recovery_source": "confirmed_operation_receipt",
            },
            identity,
            record,
        )
    if not executor_id:
        status = "admitted" if phase == "admitted" else "uncertain"
        return (
            {
                "status": status,
                "mutation": bool(record.get("mutation_possible")),
                "mutation_classification": phase,
                "retry_allowed": False,
                "executor_request": executor_request if phase == "admitted" else None,
                "reason": (
                    "native request was already emitted; reconcile only an exact "
                    "returned or snapshot-matched lp_executor_id"
                ),
            },
            identity,
            record,
        )

    frozen_result["executor_id"] = executor_id
    if phase == "admitted":
        record = store.write(
            identity,
            phase="submitting",
            mutation_possible=True,
            result=frozen_result,
        )
        record = store.write(
            identity,
            phase="submitted",
            mutation_possible=True,
            result=frozen_result,
        )
    elif phase in {"submitting", "uncertain"}:
        record = store.write(
            identity,
            phase="submitted",
            mutation_possible=True,
            result=frozen_result,
        )
    elif phase == "submitted" and not recorded_id:
        record = store.write(
            identity,
            phase="submitted",
            mutation_possible=True,
            result=frozen_result,
        )

    with trace.stage("submitted_create_reconciliation") as facts:
        try:
            detail = await client.executors.get_executor(executor_id=executor_id)
        except Exception as exc:
            facts["_outcome"] = "pending"
            return (
                {
                    "status": "submitted",
                    "executor_id": executor_id,
                    "mutation": True,
                    "retry_allowed": False,
                    "reason": (
                        "exact post-create detail remains unavailable: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                },
                identity,
                record,
            )
        comparison = classify_executor_create_request(
            detail,
            executor_id,
            scope.controller_id,
            executor_request,
        )
        facts.update(
            {
                "executor_id": executor_id,
                "match_outcome": comparison["outcome"],
                "missing_fields": comparison["missing_fields"],
                "mismatch_fields": sorted(comparison["mismatches"]),
                "observed_status": comparison["observed_status"],
                "observed_lifecycle": comparison["observed_lifecycle"],
            }
        )
        frozen_result["executor_match"] = comparison
        if comparison["outcome"] == "pending":
            record = store.write(
                identity,
                phase="submitted",
                mutation_possible=True,
                result=frozen_result,
                reason=comparison["reason"],
            )
            facts["_outcome"] = "pending"
            return (
                {
                    "status": "submitted",
                    "executor_id": executor_id,
                    "mutation": True,
                    "retry_allowed": False,
                    "reason": comparison["reason"],
                    "executor_match": comparison,
                    "next_action": (
                        "reconcile this exact executor ID on the next tick; "
                        "never resubmit the create request"
                    ),
                },
                identity,
                record,
            )
        if comparison["outcome"] == "conflict":
            record = store.write(
                identity,
                phase="manual_review",
                mutation_possible=True,
                result=frozen_result,
                reason=comparison["reason"],
            )
            facts["_outcome"] = "manual_review"
            return (
                {
                    "status": "manual_review",
                    "executor_id": executor_id,
                    "mutation": True,
                    "retry_allowed": False,
                    "reason": record["reason"],
                    "executor_match": comparison,
                },
                identity,
                record,
            )
        record = store.write(
            identity,
            phase="confirmed",
            mutation_possible=True,
            result=frozen_result,
        )
        facts.update({"_outcome": "confirmed", "executor_id": executor_id})
        return (
            {
                "status": "confirmed",
                "executor_id": executor_id,
                "mutation": True,
                "retry_allowed": False,
                "recovery_source": "exact_frozen_executor_request",
                "executor_match": comparison,
            },
            identity,
            record,
        )


async def _locked_create_preflight(
    *,
    scope: Any,
    client: Any,
    executor_config: dict[str, Any],
    final_plan: dict[str, Any],
    amount_quote: Decimal,
    required_quote: Decimal,
) -> dict[str, Any]:
    """Revalidate every mutable create gate while holding the controller lock."""

    schema_fields = _schema_fields(
        await client.executors.get_executor_config_schema("lp_executor")
    )
    missing = set(executor_config) - schema_fields - {"type"}
    if missing:
        raise ValueError(f"LP executor schema lacks fields: {sorted(missing)}")
    raw_rows = await fetch_all_executors(client, scope.account_name)
    normalized = [normalize_executor(row) for row in raw_rows]
    active = [row for row in normalized if row["active"]]
    foreign = [
        row["executor_id"]
        for row in active
        if row["controller_id"] != scope.controller_id
    ]
    owned = [row for row in active if row["controller_id"] == scope.controller_id]
    if foreign:
        raise ValueError("foreign live executor overlaps the wallet scope")
    if len(owned) >= positive_integer(
        scope.config["max_open_executors"], "max open executors"
    ):
        raise ValueError("executor capacity is full")
    if any(row["pool_address"] == executor_config["pool_address"] for row in owned):
        raise ValueError("selected pool already has an active executor")
    active_exposure = sum((row["exposure_quote"] for row in owned), Decimal(0))
    if active_exposure + amount_quote > decimal_value(
        scope.config["total_amount_quote"], "total amount", positive=True
    ):
        raise ValueError("aggregate LP capital would exceed its limit")
    balances = await refresh_balances(scope, client)
    base_symbol = final_plan["identity"]["base_symbol"]
    base_mint = final_plan["identity"]["base_mint"]
    required_base = decimal_value(
        final_plan["inventory"]["maximum_base_debit"],
        "executor maximum base debit",
        positive=True,
    )
    available_base = _balance(balances, base_symbol, base_mint)
    available_quote = _balance(balances, scope.quote_symbol, scope.quote_mint)
    available_sol = _balance(balances, "SOL")
    reserve = decimal_value(
        scope.config["min_sol_reserve"], "SOL reserve", positive=True
    )
    errors = []
    if base_symbol.upper() == "SOL":
        sol_needed = reserve + required_base
        if available_sol < sol_needed:
            errors.append(
                "SOL balance for LP base and reserve: "
                f"required={format(sol_needed, 'f')}, "
                f"available={format(available_sol, 'f')}"
            )
    else:
        if available_base < required_base:
            errors.append(
                f"{base_symbol} balance for LP base: "
                f"required={format(required_base, 'f')}, "
                f"available={format(available_base, 'f')}"
            )
        if available_sol < reserve:
            errors.append(
                "SOL reserve balance: "
                f"required={format(reserve, 'f')}, "
                f"available={format(available_sol, 'f')}"
            )
    if available_quote < required_quote:
        errors.append(
            f"{scope.quote_symbol} balance for LP quote leg: "
            f"required={format(required_quote, 'f')}, "
            f"available={format(available_quote, 'f')}"
        )
    if errors:
        raise ValueError("insufficient scoped balances; " + "; ".join(errors))
    return {
        "active_executors": len(owned),
        "active_exposure_quote": active_exposure,
        "required_base": required_base,
        "required_quote": required_quote,
        "available_base": available_base,
        "available_quote": available_quote,
        "available_sol": available_sol,
    }


def _native_executor_request(
    scope: Any, executor_config: dict[str, Any]
) -> dict[str, Any]:
    return {
        "action": "create",
        "executor_type": "lp_executor",
        "account_name": scope.account_name,
        "controller_id": scope.controller_id,
        "executor_config": copy.deepcopy(executor_config),
    }


def _model_result(payload: dict[str, Any]) -> str:
    compact = {
        key: payload[key]
        for key in (
            "status",
            "operation_id",
            "controller_id",
            "pool_address",
            "mutation",
            "mutation_classification",
            "retry_allowed",
            "executor_id",
            "executor_request",
            "recovery_source",
            "executor_match",
            "reason",
            "next_action",
            "report_id",
            "report_error",
        )
        if key in payload
    }
    compact["transport_complete"] = True
    if compact.get("reason"):
        compact["reason"] = safe_error(compact["reason"], limit=300)
    if compact.get("report_error"):
        compact["report_error"] = safe_error(compact["report_error"], limit=200)
    encoded = json.dumps(compact, default=str, separators=(",", ":"), sort_keys=True)
    if len(encoded) <= _TRANSPORT_MAX_CHARS:
        return encoded
    fallback = {
        key: compact.get(key)
        for key in (
            "status",
            "operation_id",
            "controller_id",
            "pool_address",
            "mutation",
            "mutation_classification",
            "executor_id",
            "report_id",
            "report_error",
        )
    }
    fallback.update(
        {
            "retry_allowed": False,
            "transport_complete": False,
            "reason": (
                "essential LP executor result exceeded the safe model transport "
                "budget; HOLD and review the complete report"
            ),
        }
    )
    return json.dumps(fallback, default=str, separators=(",", ":"), sort_keys=True)


async def run(config: Config, context: Any) -> str:
    trace = TraceRecorder()
    scope = None
    store = None
    identity = None
    mutation_possible = False
    rejection_result: dict[str, Any] | None = None
    links: dict[str, Any] = {}
    payload: dict[str, Any]
    try:
        with trace.stage("runtime_authority") as facts:
            scope = resolve_runtime(config.controller_id)
            if config.tick is not None and config.tick > scope.current_tick:
                raise ValueError("requested tick is later than the current engine tick")
            if scope.execution_mode == "dry_run":
                raise ValueError("dry run cannot create an executor")
            facts.update(
                {
                    "execution_mode": scope.execution_mode,
                    "current_tick": scope.current_tick,
                    "requested_tick": config.tick,
                    "recovery_only": config.recovery_only,
                }
            )
        with trace.stage("client_wallet_and_receipt") as facts:
            client = await get_hummingbot_client(scope)
            scope = await bind_wallet(scope, client)
            store = ReceiptStore(scope)
            prior = store.read_by_id(config.operation_id)
            facts.update(
                {
                    "wallet_bound": True,
                    "existing_operation": prior is not None,
                }
            )
        if prior is not None:
            identity, prior = _record_identity(store, prior)
            mutation_possible = bool(
                prior.get("mutation_possible") or config.lp_executor_id is not None
            )
            selection_plan = _validate_replay_inputs(config, scope, prior)
            payload, identity, reconciled = await _reconcile_existing_create(
                config=config,
                scope=scope,
                client=client,
                store=store,
                identity=identity,
                record=prior,
                trace=trace,
            )
            mutation_possible = bool(reconciled.get("mutation_possible"))
            frozen = _record_result(reconciled)
            if selection_plan is None and isinstance(
                frozen.get("selection_plan"), dict
            ):
                selection_plan = frozen["selection_plan"]
            final_plan = (
                frozen.get("final_plan")
                if isinstance(frozen.get("final_plan"), dict)
                else None
            )
            intent = reconciled["intent"]
            links.update(
                {
                    "preparation_operation_id": intent.get("preparation_operation_id"),
                    "selection_plan_digest": intent.get("selection_plan_digest"),
                    "final_plan_digest": intent.get("final_plan_digest"),
                    "executor_id": payload.get("executor_id"),
                }
            )
            payload = {
                "operation_id": config.operation_id,
                "controller_id": config.controller_id,
                "pool_address": intent.get("pool_address"),
                **(
                    {"selection_plan": selection_plan}
                    if isinstance(selection_plan, dict)
                    else {}
                ),
                **({"final_plan": final_plan} if isinstance(final_plan, dict) else {}),
                **payload,
            }
        else:
            if config.recovery_only:
                raise ValueError(
                    "current-session create receipt is unavailable for recovery"
                )
            if config.tick != scope.current_tick:
                raise ValueError(
                    "new create admission must use the current engine tick"
                )
            candidate = config.candidate
            amount_quote = config.amount_quote
            range_half_width_pct = config.range_half_width_pct
            if config.continuation_only:
                with trace.stage("continued_deployment_input") as facts:
                    candidate, amount_quote, range_half_width_pct = (
                        _continued_deployment_input(
                            store,
                            config.preparation_operation_id,
                        )
                    )
                    facts.update(
                        {
                            "preparation_operation_id": (
                                config.preparation_operation_id
                            ),
                            "source": "confirmed_preparation_receipt",
                        }
                    )
            if (
                not isinstance(candidate, dict)
                or amount_quote is None
                or range_half_width_pct is None
            ):
                raise ValueError("complete executor request input is unavailable")
            with trace.stage("selection_and_preparation_authority") as facts:
                preparation, quote_spent, attributed_base, selection_plan = (
                    _preparation(
                        store,
                        config.preparation_operation_id,
                        config.operation_id,
                        candidate=candidate,
                        amount_quote=amount_quote,
                        range_half_width_pct=range_half_width_pct,
                        strategy_config=scope.config,
                    )
                )
                preparation_result = preparation.get("result")
                preparation_receipt = (
                    preparation_result.get("receipt")
                    if isinstance(preparation_result, dict)
                    else None
                )
                admission_tick = preparation.get("tick")
                confirmed_tick = (
                    preparation_result.get("confirmed_tick")
                    if isinstance(preparation_result, dict)
                    else None
                )
                if (
                    isinstance(admission_tick, bool)
                    or not isinstance(admission_tick, int)
                    or isinstance(confirmed_tick, bool)
                    or not isinstance(confirmed_tick, int)
                    or not 1 <= admission_tick <= confirmed_tick <= scope.current_tick
                ):
                    raise ValueError("preparation tick evidence is unavailable")
                if (
                    confirmed_tick == scope.current_tick
                    and admission_tick == scope.current_tick
                    and not bool(preparation_result.get("same_tick_lp_create_allowed"))
                ):
                    raise ValueError(
                        "native executor capacity requires LP create on a later tick"
                    )
                facts.update(
                    {
                        "selection_plan_digest": selection_plan["plan_digest"],
                        "preparation_source": (
                            "swap"
                            if isinstance(preparation_receipt, dict)
                            else "existing_wallet_balance"
                        ),
                        "preparation_transaction": (
                            preparation_receipt.get("transaction_hash")
                            if isinstance(preparation_receipt, dict)
                            else None
                        ),
                        "quote_spent": quote_spent,
                        "attributed_base": attributed_base,
                        "preparation_admission_tick": admission_tick,
                        "preparation_confirmed_tick": confirmed_tick,
                    }
                )
            with trace.stage("candidate_refresh_and_replan") as facts:
                tvl_policy = pool_tvl_policy(scope.config)
                refreshed = await orca.refresh_candidate(
                    candidate,
                    tvl_policy["minimum_tvl_usd"],
                )
                registry = await client.gateway.get_network_tokens(scope.network)
                accepted, rejected = orca.filter_registered_tokens(
                    [refreshed], registry
                )
                if len(accepted) != 1 or rejected:
                    raise ValueError(
                        "refreshed candidate token is not exactly registered"
                    )
                final_plan = build_candidate_plan(
                    refreshed,
                    amount_quote=amount_quote,
                    range_half_width_pct=range_half_width_pct,
                    strategy_config=scope.config,
                    attributed_base_amount=attributed_base,
                )
                executor_config = {
                    **final_plan["executor_config"],
                    "controller_id": scope.controller_id,
                }
                required_quote = decimal_value(
                    final_plan["inventory"]["maximum_quote_debit"],
                    "executor maximum quote debit",
                    positive=True,
                )
                amount_quote = decimal_value(
                    final_plan["inputs"]["amount_quote"],
                    "plan amount",
                    positive=True,
                )
                if not final_plan["inventory"]["inventory_ready"]:
                    raise ValueError(
                        "confirmed preparation output does not cover final plan"
                    )
                if quote_spent + required_quote > amount_quote:
                    raise ValueError(
                        "preparation and final quote exceed selected allocation"
                    )
                facts.update(
                    {
                        "pool_address": refreshed["pool_address"],
                        "refreshed_price": refreshed["price"],
                        "refreshed_tvl_usd": refreshed["tvl_usd"],
                        "minimum_tvl_usd": tvl_policy["minimum_tvl_usd"],
                        "default_risk_posture": tvl_policy["default_risk_posture"],
                        "profile_target_tvl_usd": tvl_policy["profile_target_tvl_usd"],
                        "meets_profile_target": (
                            Decimal(str(refreshed["tvl_usd"]))
                            >= tvl_policy["profile_target_tvl_usd"]
                        ),
                        "final_plan_digest": final_plan["plan_digest"],
                    }
                )
            rejection_result = {
                "selection_plan": selection_plan,
                "final_plan": final_plan,
            }
            intent = {
                "pool_address": executor_config["pool_address"],
                "trading_pair": executor_config["trading_pair"],
                "selection_plan_digest": selection_plan["plan_digest"],
                "final_plan_digest": final_plan["plan_digest"],
                "preparation_operation_id": config.preparation_operation_id,
                "amount_quote": format(amount_quote, "f"),
                "range_half_width_pct": format(
                    range_half_width_pct,
                    "f",
                ),
            }
            identity = store.identity(
                operation_id=config.operation_id,
                operation_kind="create",
                intent=intent,
                tick=config.tick,
            )
            async with controller_mutation_lock(scope.controller_id):
                unresolved = [
                    record
                    for record in store.unresolved_operations()
                    if record.get("operation_id") != config.operation_id
                ]
                if unresolved:
                    raise ValueError(
                        "an unresolved current-session operation blocks executor creation"
                    )
                with trace.stage("schema_capacity_and_balances") as facts:
                    current_facts = await _locked_create_preflight(
                        scope=scope,
                        client=client,
                        executor_config=executor_config,
                        final_plan=final_plan,
                        amount_quote=amount_quote,
                        required_quote=required_quote,
                    )
                    facts.update(current_facts)
                executor_request = _native_executor_request(
                    scope,
                    executor_config,
                )
                frozen_result = {
                    "executor_request": executor_request,
                    "selection_plan": selection_plan,
                    "final_plan": final_plan,
                }
                store.admit_create(identity)
                record = store.write(
                    identity,
                    phase="admitted",
                    mutation_possible=False,
                    result=frozen_result,
                )
                with trace.stage("native_lp_executor_admission") as facts:
                    facts.update(
                        {
                            "phase": record["phase"],
                            "executor_type": "lp_executor",
                            "pool_address": executor_config["pool_address"],
                        }
                    )
                payload = {
                    "status": "ready",
                    "mutation": False,
                    "retry_allowed": False,
                    "mutation_classification": "admitted",
                    "executor_request": executor_request,
                    "next_action": (
                        "call manage_executors once with executor_request, then "
                        "invoke lp_executor_request again with the returned "
                        "lp_executor_id"
                    ),
                }
            payload = {
                "operation_id": config.operation_id,
                "controller_id": config.controller_id,
                "pool_address": executor_config["pool_address"],
                "selection_plan": selection_plan,
                "final_plan": final_plan,
                **payload,
            }
            links.update(
                {
                    "preparation_operation_id": config.preparation_operation_id,
                    "selection_plan_digest": selection_plan["plan_digest"],
                    "final_plan_digest": final_plan["plan_digest"],
                    "executor_id": payload.get("executor_id"),
                }
            )
    except asyncio.CancelledError:
        if mutation_possible and store is not None and identity is not None:
            receipt_error = None
            try:
                current = store.read(identity)
                store.write(
                    identity,
                    phase="uncertain",
                    mutation_possible=True,
                    result=(
                        _record_result(current) if isinstance(current, dict) else None
                    ),
                    reason="create invocation was cancelled after submission became possible",
                )
            except Exception as exc:
                receipt_error = safe_error(
                    f"{type(exc).__name__}: failed to persist cancellation state: {exc}"
                )
            payload = {
                "status": "uncertain",
                "operation_id": config.operation_id,
                "controller_id": config.controller_id,
                "reason": "CancelledError: create outcome may be unavailable",
                "mutation": True,
                "retry_allowed": False,
                **(
                    {"operation_record_error": receipt_error}
                    if receipt_error is not None
                    else {}
                ),
            }
        else:
            payload = {
                "status": "cancelled",
                "operation_id": config.operation_id,
                "controller_id": config.controller_id,
                "reason": "CancelledError: create invocation was cancelled before submission",
                "mutation": False,
                "retry_allowed": False,
            }
    except Exception as exc:
        prior_operation = None
        if store is not None:
            try:
                prior_operation = store.read_by_id(config.operation_id)
            except Exception:
                prior_operation = None
        rejection_reason = f"{type(exc).__name__}: {exc}"
        if (
            prior_operation is None
            and store is not None
            and identity is not None
            and config.lp_executor_id is None
        ):
            try:
                prior_operation = store.write(
                    identity,
                    phase="rejected_before_submit",
                    mutation_possible=False,
                    result=rejection_result,
                    reason=rejection_reason,
                    create_only=True,
                )
            except Exception:
                prior_operation = None
        if prior_operation is not None:
            prior_result = (
                prior_operation.get("result")
                if isinstance(prior_operation.get("result"), dict)
                else {}
            )
            if (
                prior_operation.get("phase") == "rejected_before_submit"
                and prior_operation.get("mutation_possible") is False
            ):
                payload = {
                    "status": "rejected_before_submit",
                    "operation_id": config.operation_id,
                    "controller_id": config.controller_id,
                    "reason": rejection_reason,
                    "mutation": False,
                    "retry_allowed": False,
                    "next_action": (
                        "restore the exact preparation output returned by the "
                        "next lp_snapshot"
                    ),
                }
            else:
                payload = {
                    "status": "manual_review",
                    "operation_id": config.operation_id,
                    "controller_id": config.controller_id,
                    **(
                        {"executor_id": prior_result["executor_id"]}
                        if prior_result.get("executor_id")
                        else {}
                    ),
                    "reason": (
                        "an existing create operation could not be safely "
                        f"reconciled: {rejection_reason}"
                    ),
                    "mutation": bool(prior_operation.get("mutation_possible")),
                    "retry_allowed": False,
                }
        else:
            payload = {
                "status": (
                    "manual_review"
                    if config.lp_executor_id is not None
                    else "rejected_before_submit"
                ),
                "operation_id": config.operation_id,
                "controller_id": config.controller_id,
                "reason": rejection_reason,
                "mutation": config.lp_executor_id is not None,
                "retry_allowed": config.lp_executor_id is None,
            }
    payload = await attach_report(
        payload,
        title="Non-Submitting LP Executor Request",
        source="lp_executor_request",
        version=VERSION,
        routine_input=config.model_dump(mode="json", exclude_none=True),
        trace=trace,
        scope=scope,
        links=links,
    )
    return _model_result(payload)
