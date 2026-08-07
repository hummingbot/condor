"""Refresh, replan, guard, and submit one exact Orca LP executor creation."""

from __future__ import annotations

import asyncio
import json
import re
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from agents.lp_expert.core import orca
from agents.lp_expert.core.planner import build_candidate_plan
from agents.lp_expert.core.portfolio import (
    decimal_value,
    fetch_all_executors,
    normalize_executor,
    positive_integer,
)
from agents.lp_expert.core.receipts import ReceiptStore, controller_mutation_lock
from agents.lp_expert.core.reporting import TraceRecorder, attach_report, safe_error
from agents.lp_expert.core.runtime import (
    bind_wallet,
    get_hummingbot_client,
    refresh_balances,
    resolve_runtime,
)

CATEGORY = "Orca LP Creation"
VERSION = "2"
_CONTROLLER = re.compile(r"^lp_expert\.orca_(?:e)?[1-9]\d*$")
_OPERATION = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class Config(BaseModel):
    """Create one selected snapshot plan after exact receipt and live revalidation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    controller_id: StrictStr
    tick: StrictInt = Field(gt=0)
    operation_id: StrictStr = Field(min_length=8, max_length=128)
    candidate: dict[str, Any]
    amount_quote: Decimal = Field(gt=0)
    range_half_width_pct: Decimal = Field(gt=0)
    preparation_operation_id: StrictStr = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def identity(self) -> "Config":
        if not _CONTROLLER.fullmatch(self.controller_id):
            raise ValueError("controller_id must be an lp_expert.orca controller")
        if not _OPERATION.fullmatch(self.operation_id) or not _OPERATION.fullmatch(
            self.preparation_operation_id
        ):
            raise ValueError("operation identity contains unsupported characters")
        if not isinstance(self.candidate, dict):
            raise ValueError("candidate must be a snapshot object")
        if (
            not self.amount_quote.is_finite()
            or not self.range_half_width_pct.is_finite()
        ):
            raise ValueError("selected plan values must be finite")
        return self


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
    selection_plan: dict[str, Any],
) -> tuple[dict[str, Any], Decimal, Decimal]:
    record = store.read_confirmed_swap(preparation_operation_id)
    intent = record.get("intent")
    result = record.get("result")
    receipt = result.get("receipt") if isinstance(result, dict) else None
    if not isinstance(intent, dict) or not isinstance(receipt, dict):
        raise ValueError("confirmed preparation receipt content is unavailable")
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
    input_amount = decimal_value(
        receipt.get("input_amount"), "preparation quote input", positive=True
    )
    output_amount = decimal_value(
        receipt.get("output_amount"), "preparation base output", positive=True
    )
    if not str(receipt.get("transaction_hash") or "").strip():
        raise ValueError("preparation transaction identity is unavailable")
    return record, input_amount, output_amount


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


def _existing_result(record: dict[str, Any]) -> dict[str, Any] | None:
    phase = record.get("phase")
    if phase in {"admitted", "rejected_before_submit"}:
        return None
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    status = {
        "confirmed": "confirmed",
        "submitted": "submitted",
        "submitting": "uncertain",
        "uncertain": "uncertain",
        "ambiguous": "ambiguous",
        "manual_review": "manual_review",
    }.get(str(phase), "manual_review")
    return {
        "status": status,
        "mutation": bool(record.get("mutation_possible")),
        "retry_allowed": False,
        "operation_id": record.get("operation_id"),
        "executor_id": result.get("executor_id"),
        "reason": record.get("reason") or "existing create operation was reconciled",
    }


def _created_match(
    row: dict[str, Any], controller_id: str, executor_config: dict[str, Any]
) -> bool:
    try:
        normalized = normalize_executor(row)
    except ValueError:
        return False
    current = normalized.get("_config", {})
    keys = (
        "pool_address",
        "trading_pair",
        "base_amount",
        "quote_amount",
        "lower_price",
        "upper_price",
    )
    return normalized["controller_id"] == controller_id and all(
        str(current.get(key)) == str(executor_config.get(key)) for key in keys
    )


async def _locked_create_preflight(
    *,
    scope: Any,
    client: Any,
    executor_config: dict[str, Any],
    final_plan: dict[str, Any],
    amount_quote: Decimal,
    required_quote: Decimal,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
        executor_config["base_amount"], "executor base amount", positive=True
    )
    available_base = _balance(balances, base_symbol, base_mint)
    available_quote = _balance(balances, scope.quote_symbol, scope.quote_mint)
    available_sol = _balance(balances, "SOL")
    reserve = decimal_value(
        scope.config["min_sol_reserve"], "SOL reserve", positive=True
    )
    sol_needed = reserve + (
        required_base if base_symbol.upper() == "SOL" else Decimal(0)
    )
    if (
        available_base < required_base
        or available_quote < required_quote
        or available_sol < sol_needed
    ):
        raise ValueError("scoped balances do not cover plan and SOL reserve")
    return raw_rows, {
        "active_executors": len(owned),
        "active_exposure_quote": active_exposure,
        "available_base": available_base,
        "available_quote": available_quote,
        "available_sol": available_sol,
    }


async def run(config: Config, context: Any) -> str:
    trace = TraceRecorder()
    scope = None
    store = None
    identity = None
    mutation_possible = False
    links: dict[str, Any] = {
        "preparation_operation_id": config.preparation_operation_id,
    }
    payload: dict[str, Any]
    try:
        with trace.stage("runtime_authority") as facts:
            scope = resolve_runtime(config.controller_id)
            if config.tick != scope.current_tick:
                raise ValueError("requested tick is not the current engine tick")
            if scope.execution_mode == "dry_run":
                raise ValueError("dry run cannot create an executor")
            selection_plan = build_candidate_plan(
                config.candidate,
                amount_quote=config.amount_quote,
                range_half_width_pct=config.range_half_width_pct,
                strategy_config=scope.config,
            )
            links["selection_plan_digest"] = selection_plan["plan_digest"]
            facts.update(
                {
                    "execution_mode": scope.execution_mode,
                    "tick": scope.current_tick,
                    "selection_plan_digest": selection_plan["plan_digest"],
                }
            )
        with trace.stage("client_wallet_and_receipt") as facts:
            client = await get_hummingbot_client(scope)
            scope = await bind_wallet(scope, client)
            store = ReceiptStore(scope)
            preparation, quote_spent, attributed_base = _preparation(
                store,
                config.preparation_operation_id,
                config.operation_id,
                selection_plan,
            )
            facts.update(
                {
                    "preparation_transaction": preparation["result"]["receipt"][
                        "transaction_hash"
                    ],
                    "quote_spent": quote_spent,
                    "attributed_base": attributed_base,
                }
            )
        with trace.stage("candidate_refresh_and_replan") as facts:
            refreshed = await orca.refresh_candidate(config.candidate)
            registry = await client.gateway.get_network_tokens(scope.network)
            accepted, rejected = orca.filter_registered_tokens([refreshed], registry)
            if len(accepted) != 1 or rejected:
                raise ValueError("refreshed candidate token is not exactly registered")
            final_plan = build_candidate_plan(
                refreshed,
                amount_quote=config.amount_quote,
                range_half_width_pct=config.range_half_width_pct,
                strategy_config=scope.config,
                attributed_base_amount=attributed_base,
            )
            executor_config = {
                **final_plan["executor_config"],
                "controller_id": scope.controller_id,
            }
            required_quote = decimal_value(
                executor_config["quote_amount"], "executor quote amount", positive=True
            )
            amount_quote = decimal_value(
                final_plan["inputs"]["amount_quote"], "plan amount", positive=True
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
                    "final_plan_digest": final_plan["plan_digest"],
                }
            )
        intent = {
            "pool_address": executor_config["pool_address"],
            "trading_pair": executor_config["trading_pair"],
            "selection_plan_digest": selection_plan["plan_digest"],
            "final_plan_digest": final_plan["plan_digest"],
            "preparation_operation_id": config.preparation_operation_id,
            "amount_quote": format(config.amount_quote, "f"),
            "range_half_width_pct": format(config.range_half_width_pct, "f"),
        }
        identity = store.identity(
            operation_id=config.operation_id,
            operation_kind="create",
            intent=intent,
        )
        identity_conflict = None
        try:
            existing = store.read(identity)
        except ValueError as exc:
            conflicting = store.read_by_id(config.operation_id)
            if conflicting is None:
                raise
            existing = None
            identity_conflict = {
                "status": "manual_review",
                "mutation": bool(conflicting.get("mutation_possible")),
                "retry_allowed": False,
                "operation_id": config.operation_id,
                "executor_id": (
                    conflicting.get("result", {}).get("executor_id")
                    if isinstance(conflicting.get("result"), dict)
                    else None
                ),
                "reason": (
                    "existing create operation identity conflicts with the "
                    f"current request: {type(exc).__name__}: {exc}"
                ),
            }
        if identity_conflict is not None:
            payload = identity_conflict
        elif existing is not None and existing.get("phase") == "submitted":
            submitted_result = (
                existing.get("result")
                if isinstance(existing.get("result"), dict)
                else {}
            )
            executor_id = str(submitted_result.get("executor_id") or "").strip()
            if not executor_id:
                payload = {
                    "status": "manual_review",
                    "mutation": True,
                    "retry_allowed": False,
                    "reason": "submitted create receipt lacks an exact executor identity",
                }
            else:
                with trace.stage("submitted_create_reconciliation") as facts:
                    try:
                        detail = await client.executors.get_executor(
                            executor_id=executor_id
                        )
                    except Exception as exc:
                        payload = {
                            "status": "submitted",
                            "executor_id": executor_id,
                            "mutation": True,
                            "retry_allowed": False,
                            "reason": (
                                "exact post-create detail remains unavailable: "
                                f"{type(exc).__name__}: {exc}"
                            ),
                        }
                        facts["_outcome"] = "pending"
                    else:
                        if not _created_match(
                            detail, scope.controller_id, executor_config
                        ):
                            record = store.write(
                                identity,
                                phase="manual_review",
                                mutation_possible=True,
                                result={"executor_id": executor_id},
                                reason=(
                                    "submitted executor detail conflicts with "
                                    "the immutable create intent"
                                ),
                            )
                            payload = {
                                "status": "manual_review",
                                "executor_id": executor_id,
                                "mutation": True,
                                "retry_allowed": False,
                                "reason": record["reason"],
                            }
                            facts["_outcome"] = "manual_review"
                        else:
                            store.write(
                                identity,
                                phase="confirmed",
                                mutation_possible=True,
                                result={"executor_id": executor_id},
                            )
                            payload = {
                                "status": "confirmed",
                                "executor_id": executor_id,
                                "mutation": True,
                                "retry_allowed": False,
                                "recovery_source": "exact_executor_detail",
                            }
                            facts["_outcome"] = "confirmed"
                        facts["executor_id"] = executor_id
        elif (
            existing is not None and (result := _existing_result(existing)) is not None
        ):
            payload = result
        else:
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
                    raw_rows, current_facts = await _locked_create_preflight(
                        scope=scope,
                        client=client,
                        executor_config=executor_config,
                        final_plan=final_plan,
                        amount_quote=amount_quote,
                        required_quote=required_quote,
                    )
                    facts.update(current_facts)
                store.admit_create(identity)
                store.write(
                    identity,
                    phase="submitting",
                    mutation_possible=True,
                )
                mutation_possible = True
                baseline = {normalize_executor(row)["executor_id"] for row in raw_rows}
                with trace.stage("create_submission") as facts:
                    try:
                        task = asyncio.create_task(
                            client.executors.create_executor(
                                executor_config=executor_config,
                                account_name=scope.account_name,
                                controller_id=scope.controller_id,
                            )
                        )
                        response = await asyncio.shield(task)
                    except asyncio.CancelledError:
                        store.write(
                            identity,
                            phase="uncertain",
                            mutation_possible=True,
                            reason="create wait was cancelled",
                        )
                        facts["_outcome"] = "uncertain"
                        payload = {
                            "status": "uncertain",
                            "reason": "create wait was cancelled",
                            "mutation": True,
                            "retry_allowed": False,
                        }
                    except Exception as exc:
                        refreshed_rows = await fetch_all_executors(
                            client, scope.account_name
                        )
                        matches = [
                            normalize_executor(row)
                            for row in refreshed_rows
                            if _created_match(row, scope.controller_id, executor_config)
                            and normalize_executor(row)["executor_id"] not in baseline
                        ]
                        if len(matches) == 1:
                            executor_id = matches[0]["executor_id"]
                            store.write(
                                identity,
                                phase="confirmed",
                                mutation_possible=True,
                                result={"executor_id": executor_id},
                                reason=f"create response lost; exact executor recovered after {type(exc).__name__}",
                            )
                            payload = {
                                "status": "confirmed",
                                "executor_id": executor_id,
                                "recovery_source": "exact_executor_search",
                                "mutation": True,
                                "retry_allowed": False,
                            }
                        else:
                            phase = "ambiguous" if len(matches) > 1 else "uncertain"
                            reason = f"{type(exc).__name__}: {exc}"
                            store.write(
                                identity,
                                phase=phase,
                                mutation_possible=True,
                                reason=reason,
                            )
                            payload = {
                                "status": phase,
                                "reason": reason,
                                "mutation": True,
                                "retry_allowed": False,
                            }
                        facts["_outcome"] = payload["status"]
                    else:
                        ids = {
                            str(response[key]).strip()
                            for key in ("executor_id", "id")
                            if isinstance(response, dict) and response.get(key)
                        }
                        if len(ids) != 1:
                            store.write(
                                identity,
                                phase="uncertain",
                                mutation_possible=True,
                                reason="create response lacked one executor identity",
                            )
                            payload = {
                                "status": "uncertain",
                                "reason": "create response lacked one executor identity",
                                "mutation": True,
                                "retry_allowed": False,
                            }
                        else:
                            executor_id = ids.pop()
                            store.write(
                                identity,
                                phase="submitted",
                                mutation_possible=True,
                                result={"executor_id": executor_id},
                            )
                            try:
                                detail = await client.executors.get_executor(
                                    executor_id=executor_id
                                )
                                if not _created_match(
                                    detail, scope.controller_id, executor_config
                                ):
                                    raise ValueError(
                                        "created executor detail conflicts with intent"
                                    )
                            except Exception as exc:
                                payload = {
                                    "status": "submitted",
                                    "executor_id": executor_id,
                                    "reason": f"exact post-create detail is pending: {type(exc).__name__}: {exc}",
                                    "mutation": True,
                                    "retry_allowed": False,
                                }
                            else:
                                store.write(
                                    identity,
                                    phase="confirmed",
                                    mutation_possible=True,
                                    result={"executor_id": executor_id},
                                )
                                payload = {
                                    "status": "confirmed",
                                    "executor_id": executor_id,
                                    "mutation": True,
                                    "retry_allowed": False,
                                }
                        facts.update(
                            {
                                "_outcome": payload["status"],
                                "executor_id": payload.get("executor_id"),
                            }
                        )
        payload = {
            "operation_id": config.operation_id,
            "controller_id": config.controller_id,
            "pool_address": executor_config["pool_address"],
            "selection_plan": selection_plan,
            "final_plan": final_plan,
            **payload,
        }
        links["final_plan_digest"] = final_plan["plan_digest"]
        links["executor_id"] = payload.get("executor_id")
    except asyncio.CancelledError:
        if mutation_possible and store is not None and identity is not None:
            receipt_error = None
            try:
                store.write(
                    identity,
                    phase="uncertain",
                    mutation_possible=True,
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
        if prior_operation is not None:
            payload = {
                "status": "manual_review",
                "operation_id": config.operation_id,
                "controller_id": config.controller_id,
                "reason": (
                    "an existing create operation could not be safely reconciled: "
                    f"{type(exc).__name__}: {exc}"
                ),
                "mutation": bool(prior_operation.get("mutation_possible")),
                "retry_allowed": False,
            }
        else:
            payload = {
                "status": "rejected_before_submit",
                "operation_id": config.operation_id,
                "controller_id": config.controller_id,
                "reason": f"{type(exc).__name__}: {exc}",
                "mutation": False,
                "retry_allowed": True,
            }
    payload = await attach_report(
        payload,
        title="LP Executor Creation",
        source="lp_create",
        version=VERSION,
        routine_input=config.model_dump(mode="json"),
        trace=trace,
        scope=scope,
        links=links,
    )
    return json.dumps(payload, default=str, separators=(",", ":"), sort_keys=True)
