import asyncio
import importlib
import json
import re
import time
import traceback
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, StrictStr, field_validator

from agents.lp_wizard.routines import _pool_policy, _reporting, _shared
from agents.lp_wizard.routines import close as _close
from agents.lp_wizard.routines import open as _open

if getattr(_shared, "RUNTIME_VERSION", 0) < 1:
    _shared = importlib.reload(_shared)

CATEGORY = "LP Wizard"
_SLOT_RE = re.compile(r"^slot-[0-9]{2,}$")


class Config(BaseModel):
    """Reconcile one exact persisted LP slot without blind retries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    controller_id: StrictStr | None = None
    slot_id: StrictStr | None = None

    @field_validator("controller_id", "slot_id")
    @classmethod
    def _required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or value != value.strip():
            raise ValueError(
                "must be a non-empty string without surrounding whitespace"
            )
        return value

    @field_validator("slot_id")
    @classmethod
    def _slot(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _SLOT_RE.fullmatch(value) is None:
            raise ValueError("slot_id must be slot- followed by at least two digits")
        return value

    @field_validator("controller_id")
    @classmethod
    def _controller_id(cls, value: str | None) -> str | None:
        return _shared.validate_controller_id(value) if value is not None else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact(value: dict[str, Any]) -> str:
    return json.dumps(_shared.json_value(value), separators=(",", ":"), sort_keys=True)


def _error(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"


def _result(config: Config, status: str, **values: Any) -> str:
    return _compact(
        {
            "controller_id": config.controller_id,
            "slot_id": config.slot_id,
            "status": status,
            **values,
        }
    )


def _transaction_matches(evidence: dict[str, Any], request: dict[str, Any]) -> bool:
    expected_pair = _shared.text(request["trading_pair"])
    expected_side = _shared.text(request["side"]).upper()
    amount = _shared.decimal(request["amount"], "rebalance amount", positive=True)
    input_amount = evidence["input_amount"]
    output_amount = evidence["output_amount"]
    attributed = output_amount if expected_side == "BUY" else input_amount
    expected_slippage = _shared.decimal(
        request["slippage_pct"], "swap slippage", positive=True
    )
    base_proven = (
        evidence["base_mint"] in (None, request["base_mint"])
        and evidence["base_token"] in (None, request["base_symbol"])
        and (evidence["base_mint"] is not None or evidence["base_token"] is not None)
    )
    quote_proven = (
        evidence["quote_mint"] in (None, request["quote_mint"])
        and evidence["quote_token"] in (None, request["quote_symbol"])
        and (evidence["quote_mint"] is not None or evidence["quote_token"] is not None)
    )
    return (
        evidence["status"] in {"CONFIRMED", "SUCCESS", "COMPLETED"}
        and evidence["trading_pair"] == expected_pair
        and evidence["side"] == expected_side
        and input_amount is not None
        and output_amount is not None
        and input_amount > 0
        and output_amount > 0
        and attributed is not None
        and attributed == amount
        and evidence["connector"] == request["connector"] == "jupiter"
        and evidence["network"] == request["network"] == _shared.NETWORK
        and evidence["wallet_address"] == request["wallet_address"]
        and evidence["slippage_pct"] == expected_slippage
        and base_proven
        and quote_proven
    )


async def _recover_swap_hash(
    config: Config,
    session: _shared.Session,
    strategy_config: _shared.Config,
    client: Any,
    pending: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    if pending["external_id"]:
        return pending, None
    try:
        evidence = await _shared.find_submitted_swap(
            client, pending["request"], pending["attempted_at"]
        )
    except ValueError as error:
        return None, _result(
            config,
            "manual_blocked",
            operation_id=pending["operation_id"],
            step=pending["step"],
            error=f"submitted swap proof is ambiguous or invalid: {error}",
        )
    if evidence is None:
        return None, _result(
            config,
            "recoverable",
            operation_id=pending["operation_id"],
            step=pending["step"],
            error="no unique submitted swap proof was found; request was not repeated",
        )

    def adopt(state: dict[str, Any], slot: dict[str, Any]) -> None:
        current = slot["pending_mutation"]
        current["external_id"] = evidence["transaction_hash"]
        current["confirmed"]["discovered_submission"] = _shared.json_value(
            evidence, redact=False
        )

    adopted = _close._checkpoint(
        session, strategy_config, config.slot_id, pending, adopt
    )
    return deepcopy(adopted["slots"][config.slot_id]["pending_mutation"]), None


def _with_discovered_identity(
    pending: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    discovered = pending["confirmed"].get("discovered_submission")
    if not isinstance(discovered, dict):
        return evidence
    return {
        key: discovered.get(key) if value is None else value
        for key, value in evidence.items()
    }


def _baseline_sets(request: dict[str, Any]) -> tuple[set[str], set[str]]:
    raw_executors = request["baseline_executor_ids"]
    raw_positions = request["baseline_position_ids"]
    if not isinstance(raw_executors, list) or not all(
        isinstance(item, str) and item for item in raw_executors
    ):
        raise ValueError("create recovery requires baseline_executor_ids")
    if not isinstance(raw_positions, list) or not all(
        isinstance(item, str) and item for item in raw_positions
    ):
        raise ValueError("create recovery requires baseline_position_ids")
    if len(raw_executors) != len(set(raw_executors)):
        raise ValueError("baseline_executor_ids contains duplicates")
    if len(raw_positions) != len(set(raw_positions)):
        raise ValueError("baseline_position_ids contains duplicates")
    return set(raw_executors), set(raw_positions)


def _executor_expected(request: dict[str, Any], controller_id: str) -> dict[str, Any]:
    executor_config = request["executor_config"]
    if not isinstance(executor_config, dict):
        raise ValueError("persisted create request has no executor_config")
    if request["controller_id"] != controller_id:
        raise ValueError("persisted create controller differs from selected controller")
    return {
        **request,
        "controller_id": controller_id,
        "executor_config": executor_config,
    }


def _confirmed_rebalance(pending: dict[str, Any]) -> dict[str, Any]:
    value = pending["confirmed"].get("rebalance")
    if not isinstance(value, dict) or value.get("status") not in {
        "CONFIRMED",
        "SUCCESS",
        "COMPLETED",
    }:
        raise ValueError("confirmed rebalance attribution is unavailable")
    if value.get("output_amount") is None:
        raise ValueError("confirmed rebalance output amount is unavailable")
    return value


def _position_record(
    session: _shared.Session,
    pending: dict[str, Any],
    executor: dict[str, Any],
) -> dict[str, Any]:
    request = pending["request"]
    plan = _executor_expected(request, session.controller_id)
    config = plan["executor_config"]
    return {
        "controller_id": session.controller_id,
        "executor_id": executor["executor_id"],
        "position_address": executor["position_address"],
        "pool_address": request["pool_address"],
        "base_mint": request["base_mint"],
        "quote_mint": request["quote_mint"],
        "lower_price": config.get("lower_price"),
        "upper_price": config.get("upper_price"),
        "lower_limit_price": config.get("lower_limit_price"),
        "upper_limit_price": config.get("upper_limit_price"),
        "amount_quote": request["amount_quote"],
        "opened_at": pending["attempted_at"],
    }


def _restore_request(
    request: dict[str, Any],
    rebalance: dict[str, Any],
    wallet: str,
    strategy_config: _shared.Config,
) -> dict[str, Any]:
    base_mint = request["base_mint"]
    quote_mint = request["quote_mint"]
    if (
        not isinstance(base_mint, str)
        or not base_mint
        or not isinstance(quote_mint, str)
        or not quote_mint
    ):
        raise ValueError("restore token mint attribution is unavailable")
    amount = _shared.decimal(rebalance["output_amount"], "confirmed rebalance output")
    if amount < 0:
        raise ValueError("confirmed rebalance output is negative")
    result = deepcopy(request)
    result.pop("rebalance_swap", None)
    result["pool_address"] = request["pool_address"]
    result["restore_base_amount"] = str(amount)
    result["restore_swap"] = {
        "connector": "jupiter",
        "network": _shared.NETWORK,
        "trading_pair": f"{base_mint}-{quote_mint}",
        "side": "SELL",
        "amount": str(amount),
        "slippage_pct": str(strategy_config.max_slippage_pct),
        "wallet_address": wallet,
        "base_mint": base_mint,
        "base_symbol": request["base_symbol"],
        "quote_mint": quote_mint,
        "quote_symbol": request["quote_symbol"],
    }
    return result


def _normalized_restore_request(
    pending: dict[str, Any], strategy_config: _shared.Config
) -> dict[str, Any]:
    request = pending["request"]
    swap = request["restore_swap"]
    if not isinstance(swap, dict):
        raise ValueError("persisted restore request has no restore_swap")
    if (
        _shared.decimal(swap["slippage_pct"], "restore slippage", positive=True)
        != strategy_config.max_slippage_pct
    ):
        raise ValueError("persisted restore slippage differs from immutable config")
    result = deepcopy(request)
    result["restore_swap"] = {
        **swap,
        "amount": str(
            _shared.decimal(
                request["restore_base_amount"], "restore amount", positive=True
            )
        ),
        "slippage_pct": str(strategy_config.max_slippage_pct),
    }
    return result


async def _fresh_create_plan(
    client: Any,
    session: _shared.Session,
    strategy_config: _shared.Config,
    request: dict[str, Any],
) -> dict[str, Any]:
    candidate = request["candidate"]
    option_name = request["range_option"]
    if not isinstance(candidate, dict) or not isinstance(option_name, str):
        raise ValueError(
            "recovered create requires the full persisted candidate and option"
        )
    amount = _shared.decimal(request["amount_quote"], "amount_quote", positive=True)
    validated, option, rejection = _pool_policy.revalidate_candidate(
        candidate,
        strategy_config.risk_profile,
        float(strategy_config.total_amount_quote),
        option_name,
    )
    if rejection or validated is None or option is None:
        raise ValueError(f"candidate revalidation failed: {rejection}")
    if (
        validated["pool_address"] != request["pool_address"]
        or validated["token_a"]["mint"] != request["base_mint"]
        or validated["token_b"]["mint"] != request["quote_mint"]
    ):
        raise ValueError("persisted candidate identity changed")
    live = await _open._live_preflight(
        client, strategy_config, validated, option, option_name, amount
    )
    shortfall = max(live["required_base"] - live["available_base"], 0)
    if shortfall > 0:
        quote = await client.gateway_swap.get_swap_quote(
            connector="jupiter",
            network=_shared.NETWORK,
            trading_pair=request["trading_pair"],
            side="BUY",
            amount=shortfall,
            slippage_pct=strategy_config.max_slippage_pct,
        )
        _open._quote_result(
            quote,
            request["trading_pair"],
            "BUY",
            shortfall,
            live["gateway"]["current_price"],
            strategy_config.max_slippage_pct,
        )
        raise ValueError("fresh inventory still requires a rebalance")
    schema = await client.executors.get_executor_config_schema("lp_executor")
    return _open._executor_config(
        validated,
        live["bounds"],
        live["required_base"],
        live["required_quote"],
        amount,
        _open._schema_supports_total(schema),
    )


async def _reconcile_restore(
    config: Config,
    session: _shared.Session,
    strategy_config: _shared.Config,
    client: Any,
    wallet: str,
    operation_id: str,
) -> str:
    state = _close._locked_state(session, strategy_config)
    pending = deepcopy(state["slots"][config.slot_id]["pending_mutation"])
    pending, blocked = await _recover_swap_hash(
        config, session, strategy_config, client, pending
    )
    if blocked is not None:
        return blocked
    assert pending is not None
    transaction_hash = pending["external_id"]
    persisted_request = pending["request"]
    swap = persisted_request["restore_swap"]
    if not isinstance(swap, dict):
        raise ValueError("persisted restore request has no restore_swap")
    request = {
        **swap,
        "pool_address": persisted_request["pool_address"],
        "position_address": persisted_request.get("position_address"),
    }
    evidence = _with_discovered_identity(
        pending, await _shared.get_swap_evidence(client, transaction_hash)
    )
    if evidence["status"] in {"FAILED", "REJECTED"}:
        return _result(
            config,
            "manual_blocked",
            operation_id=operation_id,
            step="restore",
            error=f"restore transaction is {evidence['status']}",
        )
    if not _close._transaction_matches(evidence, request):
        return _result(
            config,
            "pending",
            operation_id=operation_id,
            step="restore",
            error="restore transaction is unconfirmed or attribution is contradictory",
        )
    _close._checkpoint(
        session,
        strategy_config,
        config.slot_id,
        pending,
        _close._clear_selected,
    )
    return _result(
        config,
        "recovered",
        operation_id=operation_id,
        step="restore",
        transaction_hash=transaction_hash,
        **(
            {
                "returned_inventory": _shared.json_value(
                    pending["confirmed"]["returned_inventory"], redact=False
                )
            }
            if isinstance(pending["confirmed"].get("returned_inventory"), dict)
            else {}
        ),
    )


async def _begin_restore_after_create_failure(
    config: Config,
    session: _shared.Session,
    strategy_config: _shared.Config,
    client: Any,
    wallet: str,
    operation_id: str,
) -> str:
    state = _close._locked_state(session, strategy_config)
    selected = state["slots"][config.slot_id]
    pending = deepcopy(selected["pending_mutation"])
    rebalance = _confirmed_rebalance(pending)
    request = _restore_request(pending["request"], rebalance, wallet, strategy_config)
    if _shared.decimal(request["restore_base_amount"], "restore amount") == 0:
        _close._checkpoint(
            session,
            strategy_config,
            config.slot_id,
            pending,
            _close._clear_selected,
        )
        return _result(
            config, "recovered", operation_id=operation_id, restoration="not_required"
        )

    def intent(state: dict[str, Any], slot: dict[str, Any]) -> None:
        prior = slot["pending_mutation"]
        slot["pending_mutation"] = {
            "operation_id": operation_id,
            "type": prior["type"],
            "step": "restore",
            "status": "intent",
            "request": request,
            "attempted_at": None,
            "external_id": None,
            "confirmed": prior["confirmed"],
        }

    _close._checkpoint(session, strategy_config, config.slot_id, pending, intent)
    return await _close._submit_restore(
        config, session, strategy_config, client, operation_id
    )


def _failed_before_open(executor: dict[str, Any]) -> bool:
    custom = executor["custom_info"]
    base_fill = _shared.read(custom, "filled_amount_base", "base_filled_amount")
    quote_fill = _shared.read(custom, "filled_amount_quote", "quote_filled_amount")
    return (
        not executor["is_active"]
        and executor["status"]
        in {"FAILED", "ERROR", "CANCELED", "CANCELLED", "TERMINATED", "STOPPED"}
        and executor["position_address"] is None
        and executor["filled_amount_quote"] is not None
        and executor["filled_amount_quote"] == 0
        and base_fill is not None
        and _shared.decimal(base_fill, "filled base amount") == 0
        and quote_fill is not None
        and _shared.decimal(quote_fill, "filled quote amount") == 0
        and executor["is_trading"] is False
    )


async def _reconcile_create(
    config: Config,
    session: _shared.Session,
    strategy_config: _shared.Config,
    client: Any,
    wallet: str,
    operation_id: str,
) -> str:
    state = _close._locked_state(session, strategy_config)
    pending = deepcopy(state["slots"][config.slot_id]["pending_mutation"])
    expected = _executor_expected(pending["request"], session.controller_id)
    external_id = pending["external_id"]
    baseline_executors, baseline_positions = _baseline_sets(pending["request"])
    executors = await _shared.search_controller_executors(client, session.controller_id)
    matches = [
        executor
        for executor in executors
        if executor["executor_id"] not in baseline_executors
        and not _shared.executor_plan_mismatches(executor, expected)
    ]
    if external_id:
        matches = [
            executor for executor in matches if executor["executor_id"] == external_id
        ]
    if not matches:
        return _result(
            config,
            "pending",
            operation_id=operation_id,
            step="create",
            error="no executor uniquely matches the strict persisted plan; create remains uncertain",
        )
    if len(matches) > 1:
        return _result(
            config,
            "manual_blocked",
            operation_id=operation_id,
            step="create",
            error="multiple executors match the strict persisted plan",
            executor_ids=[item["executor_id"] for item in matches],
        )
    executor = matches[0]
    pool_address = expected["executor_config"].get("pool_address")
    owned = await _shared.get_owned_positions(client, pool_address, wallet)
    owned_ids = _shared.owned_position_addresses(owned)
    if executor["position_address"] is None:
        if executor["is_active"]:
            return _result(
                config,
                "pending",
                operation_id=operation_id,
                step="create",
                error="matching executor has not exposed a position address",
            )
        if _failed_before_open(executor):
            if pending["confirmed"].get("rebalance") is not None:
                return await _begin_restore_after_create_failure(
                    config, session, strategy_config, client, wallet, operation_id
                )
            _close._checkpoint(
                session,
                strategy_config,
                config.slot_id,
                pending,
                _close._clear_selected,
            )
            return _result(
                config,
                "recovered",
                operation_id=operation_id,
                step="failed_before_open",
            )
        return _result(
            config,
            "manual_blocked",
            operation_id=operation_id,
            step="create",
            error="terminal matching executor has no attributable position",
        )
    if executor["position_address"] in baseline_positions:
        return _result(
            config,
            "manual_blocked",
            operation_id=operation_id,
            step="create",
            error="matching executor points to a position that predates this create",
        )
    position = _position_record(session, pending, executor)
    pool = await _shared.get_gateway_pool(client, pool_address)
    if executor["position_address"] not in owned_ids:
        if not executor["is_active"]:
            return _result(
                config,
                "manual_blocked",
                operation_id=operation_id,
                step="create",
                error="terminal create executor cleared its position address; exact position identity cannot be recovered",
            )
        return _result(
            config,
            "pending",
            operation_id=operation_id,
            step="create",
            error="matching executor position is not yet proven owned by the bound wallet",
        )
    _close._verify_position_evidence(position, executor, pool, owned)

    def adopt(state: dict[str, Any], slot: dict[str, Any]) -> None:
        if slot["position"] is not None:
            raise ValueError("selected slot already acquired a position")
        slot["position"] = position
        slot["pending_mutation"] = None

    _close._checkpoint(session, strategy_config, config.slot_id, pending, adopt)
    return _result(
        config,
        "recovered",
        operation_id=operation_id,
        step="create",
        executor_id=executor["executor_id"],
        position_address=executor["position_address"],
    )


async def _reconcile_rebalance(
    config: Config,
    session: _shared.Session,
    strategy_config: _shared.Config,
    client: Any,
    wallet: str,
    operation_id: str,
) -> str:
    state = _close._locked_state(session, strategy_config)
    pending = deepcopy(state["slots"][config.slot_id]["pending_mutation"])
    pending, blocked = await _recover_swap_hash(
        config, session, strategy_config, client, pending
    )
    if blocked is not None:
        return blocked
    assert pending is not None
    transaction_hash = pending["external_id"]
    request = pending["request"]["rebalance_swap"]
    if not isinstance(request, dict):
        raise ValueError("persisted rebalance request has no rebalance_swap")
    evidence = _with_discovered_identity(
        pending, await _shared.get_swap_evidence(client, transaction_hash)
    )
    if evidence["status"] in {"FAILED", "REJECTED"}:
        return _result(
            config,
            "manual_blocked",
            operation_id=operation_id,
            step="rebalance",
            error=f"rebalance transaction is {evidence['status']}",
        )
    if not _transaction_matches(evidence, request):
        return _result(
            config,
            "pending",
            operation_id=operation_id,
            step="rebalance",
            error="rebalance transaction is unconfirmed or attribution is contradictory",
        )
    _executor_expected(pending["request"], session.controller_id)
    _baseline_sets(pending["request"])

    def create_intent(state: dict[str, Any], slot: dict[str, Any]) -> None:
        current = slot["pending_mutation"]
        current["step"] = "create"
        current["status"] = "intent"
        current["attempted_at"] = None
        current["external_id"] = None
        current["confirmed"]["rebalance"] = _shared.json_value(evidence, redact=False)

    _close._checkpoint(session, strategy_config, config.slot_id, pending, create_intent)
    return await _submit_intent(
        config, session, strategy_config, client, wallet, operation_id
    )


async def _submit_intent(
    config: Config,
    session: _shared.Session,
    strategy_config: _shared.Config,
    client: Any,
    wallet: str,
    operation_id: str,
) -> str:
    state = _close._locked_state(session, strategy_config)
    pending = deepcopy(state["slots"][config.slot_id]["pending_mutation"])
    if (
        pending["status"] != "intent"
        or pending["attempted_at"] is not None
        or pending["external_id"] is not None
    ):
        raise ValueError("intent is not eligible for a first submission")
    step = pending["step"]
    if step == "restore":
        normalized = _normalized_restore_request(pending, strategy_config)

        def normalize_restore(state: dict[str, Any], slot: dict[str, Any]) -> None:
            slot["pending_mutation"]["request"] = normalized

        normalized_state = _close._checkpoint(
            session,
            strategy_config,
            config.slot_id,
            pending,
            normalize_restore,
        )
        pending = deepcopy(
            normalized_state["slots"][config.slot_id]["pending_mutation"]
        )
        return await _close._submit_restore(
            config, session, strategy_config, client, operation_id
        )

    call: dict[str, Any]
    if step == "stop":
        if _shared.load_config(session) != strategy_config:
            raise ValueError("immutable session config changed")
        position = state["slots"][config.slot_id]["position"]
        request = pending["request"]
        if (
            position is None
            or request.get("executor_id") != position["executor_id"]
            or request.get("controller_id") != position["controller_id"]
            or request.get("pool_address") != position["pool_address"]
            or request.get("position_address") != position["position_address"]
            or request.get("keep_position") is not True
        ):
            raise ValueError("persisted stop intent contradicts selected position")
        executor = await _shared.get_executor_evidence(
            client, position["executor_id"], session.controller_id
        )
        pool = await _shared.get_gateway_pool(client, position["pool_address"])
        owned = await _shared.get_owned_positions(
            client, position["pool_address"], wallet
        )
        if not executor["is_active"]:
            return await _close._reconcile_close(
                config, session, strategy_config, client, wallet, operation_id
            )
        _close._verify_position_evidence(position, executor, pool, owned)
        call = {"executor_id": position["executor_id"]}
    elif step == "rebalance":
        request = pending["request"]["rebalance_swap"]
        if not isinstance(request, dict):
            raise ValueError("persisted rebalance request has no rebalance_swap")
        if (
            _shared.decimal(
                request["slippage_pct"], "rebalance slippage", positive=True
            )
            != strategy_config.max_slippage_pct
        ):
            raise ValueError(
                "persisted rebalance slippage differs from immutable config"
            )
        normalized_parent = deepcopy(pending["request"])
        normalized_parent["rebalance_swap"] = {
            **request,
            "slippage_pct": str(strategy_config.max_slippage_pct),
        }

        def normalize_rebalance(state: dict[str, Any], slot: dict[str, Any]) -> None:
            slot["pending_mutation"]["request"] = normalized_parent

        normalized_state = _close._checkpoint(
            session,
            strategy_config,
            config.slot_id,
            pending,
            normalize_rebalance,
        )
        pending = deepcopy(
            normalized_state["slots"][config.slot_id]["pending_mutation"]
        )
        request = pending["request"]["rebalance_swap"]
        if not isinstance(request, dict):
            raise ValueError("persisted rebalance request has no rebalance_swap")
        amount = _shared.decimal(request["amount"], "rebalance amount", positive=True)
        if (
            request["connector"] != "jupiter"
            or request["network"] != _shared.NETWORK
            or request["wallet_address"] != wallet
            or request["side"] != "BUY"
            or request["trading_pair"]
            != f"{request['base_mint']}-{request['quote_mint']}"
        ):
            raise ValueError("persisted rebalance request identity is invalid")
        pool = await _shared.get_gateway_pool(
            client, pending["request"]["pool_address"]
        )
        quote = await client.gateway_swap.get_swap_quote(
            connector="jupiter",
            network=_shared.NETWORK,
            trading_pair=request["trading_pair"],
            side="BUY",
            amount=amount,
            slippage_pct=strategy_config.max_slippage_pct,
        )
        _open._quote_result(
            quote,
            request["trading_pair"],
            "BUY",
            amount,
            pool["current_price"],
            strategy_config.max_slippage_pct,
        )
        call = {"request": request, "amount": amount}
    elif step == "create":
        _baseline_sets(pending["request"])
        try:
            fresh_plan = await _fresh_create_plan(
                client, session, strategy_config, pending["request"]
            )
            baseline_executors, baseline_positions = await _open._ownership_preflight(
                client,
                session,
                strategy_config,
                wallet,
                pending["request"]["pool_address"],
            )
        except Exception as error:
            if pending["confirmed"].get("rebalance") is not None:
                return await _begin_restore_after_create_failure(
                    config, session, strategy_config, client, wallet, operation_id
                )
            _close._checkpoint(
                session,
                strategy_config,
                config.slot_id,
                pending,
                _close._clear_selected,
            )
            return _result(
                config,
                "recovered",
                operation_id=operation_id,
                step="create",
                error=f"fresh preflight rejected unsubmitted create: {_error(error)}",
            )

        def fresh_request(state: dict[str, Any], slot: dict[str, Any]) -> None:
            current = slot["pending_mutation"]
            current["request"] = {
                **current["request"],
                "executor_config": fresh_plan,
                "baseline_executor_ids": sorted(baseline_executors),
                "baseline_position_ids": sorted(baseline_positions),
            }
            current["confirmed"]["fresh_preflight"] = {
                "checked_at": _now(),
                "plan": _shared.json_value(fresh_plan, redact=False),
            }

        fresh_state = _close._checkpoint(
            session, strategy_config, config.slot_id, pending, fresh_request
        )
        pending = deepcopy(fresh_state["slots"][config.slot_id]["pending_mutation"])
        plan = _executor_expected(pending["request"], session.controller_id)
        if (
            plan.get("account_name", strategy_config.account_name)
            != strategy_config.account_name
        ):
            raise ValueError("persisted create account differs from immutable config")
        call = {"executor_config": plan["executor_config"]}
    else:
        raise ValueError(f"unsupported intent step '{step}'")

    def submitted(state: dict[str, Any], slot: dict[str, Any]) -> None:
        current = slot["pending_mutation"]
        if (
            current["status"] != "intent"
            or current["attempted_at"] is not None
            or current["external_id"] is not None
        ):
            raise ValueError("intent was already attempted")
        current["status"] = "submitted"
        current["attempted_at"] = _now()

    submitted_state = _close._checkpoint(
        session, strategy_config, config.slot_id, pending, submitted
    )
    submitted_pending = deepcopy(
        submitted_state["slots"][config.slot_id]["pending_mutation"]
    )
    try:
        if step == "stop":
            response = await client.executors.stop_executor(
                executor_id=call["executor_id"], keep_position=True
            )
            external_id = None
        elif step == "rebalance":
            request = call["request"]
            response = await client.gateway_swap.execute_swap(
                connector="jupiter",
                network=_shared.NETWORK,
                trading_pair=request["trading_pair"],
                side="BUY",
                amount=call["amount"],
                slippage_pct=strategy_config.max_slippage_pct,
                wallet_address=wallet,
            )
            external_id = _shared.normalize_transaction(response)["transaction_hash"]
        elif step == "create":
            response = await client.executors.create_executor(
                executor_config=_shared.json_value(
                    call["executor_config"], redact=False
                ),
                account_name=strategy_config.account_name,
                controller_id=session.controller_id,
            )
            external_id = _shared.executor_id(response)
    except BaseException as error:

        def uncertain(state: dict[str, Any], slot: dict[str, Any]) -> None:
            slot["pending_mutation"]["status"] = "uncertain"
            slot["pending_mutation"]["confirmed"]["submission_error"] = _error(error)

        _close._checkpoint(
            session, strategy_config, config.slot_id, submitted_pending, uncertain
        )
        if (
            isinstance(error, (KeyboardInterrupt, SystemExit))
            or type(error).__name__ == "CancelledError"
        ):
            raise
        return _result(
            config,
            "recoverable",
            operation_id=operation_id,
            step=step,
            error=_error(error),
        )

    def record(state: dict[str, Any], slot: dict[str, Any]) -> None:
        current = slot["pending_mutation"]
        current["external_id"] = external_id
        current["confirmed"]["submission"] = _shared.json_value(response, redact=False)

    _close._checkpoint(
        session, strategy_config, config.slot_id, submitted_pending, record
    )
    if step == "stop":
        return await _close._reconcile_close(
            config, session, strategy_config, client, wallet, operation_id
        )
    if step == "rebalance":
        return await _reconcile_rebalance(
            config, session, strategy_config, client, wallet, operation_id
        )
    return await _reconcile_create(
        config, session, strategy_config, client, wallet, operation_id
    )


async def _native_terminal(
    config: Config,
    session: _shared.Session,
    strategy_config: _shared.Config,
    client: Any,
    wallet: str,
    position: dict[str, Any],
) -> str:
    executor = await _shared.get_executor_evidence(
        client, position["executor_id"], session.controller_id
    )
    if executor["is_active"]:
        return _result(
            config,
            "nothing_to_recover",
            error="selected executor remains active and has no pending mutation",
        )
    if executor["status"] in {"ERROR", "FAILED"}:
        return _result(
            config,
            "manual_blocked",
            error=f"terminal executor ended with {executor['status']}",
        )
    pool = await _shared.get_gateway_pool(client, position["pool_address"])
    owned = await _shared.get_owned_positions(client, position["pool_address"], wallet)
    try:
        _close._verify_position_evidence(position, executor, pool, owned, terminal=True)
        inventory = _close._returned_inventory(executor)
        terminal_price = _close._native_limit_evidence(position, executor)
    except ValueError as error:
        return _result(
            config,
            "manual_blocked",
            error=str(error),
        )
    base_total = inventory["base_total"]
    operation_id = uuid4().hex
    request = {
        "pool_address": position["pool_address"],
        "position_address": position["position_address"],
        "executor_id": position["executor_id"],
        "restore_base_amount": str(base_total),
        "restore_swap": {
            "connector": "jupiter",
            "network": _shared.NETWORK,
            "trading_pair": f"{position['base_mint']}-{position['quote_mint']}",
            "side": "SELL",
            "amount": str(base_total),
            "slippage_pct": str(strategy_config.max_slippage_pct),
            "wallet_address": wallet,
            "base_mint": position["base_mint"],
            "quote_mint": position["quote_mint"],
        },
    }
    with _shared.wallet_lock(session):
        with _shared.state_lock(session):
            if _shared.load_config(session) != strategy_config:
                raise ValueError("immutable session config changed")
            state = _shared.read_state(session, strategy_config)
            slot = state["slots"][config.slot_id] if state is not None else None
            if (
                slot is None
                or slot["position"] != position
                or slot["pending_mutation"] is not None
                or state["binding"]["observed_default_wallet"] != wallet
            ):
                raise ValueError("selected terminal position changed concurrently")
            blockers = _close._pending_blockers(state, config.slot_id)
            if blockers:
                return _result(
                    config,
                    "blocked",
                    error="another slot in this session has an unresolved mutation",
                    slot_blockers=blockers,
                )
            slot["pending_mutation"] = {
                "operation_id": operation_id,
                "type": "close",
                "step": "restore",
                "status": "intent",
                "request": request,
                "attempted_at": None,
                "external_id": None,
                "confirmed": {
                    "native_terminal": {
                        "executor_status": executor["status"],
                        "position_absent": True,
                        "close_type": inventory["close_type"],
                        "current_price": str(terminal_price),
                    },
                    "returned_inventory": {
                        "position_address": position["position_address"],
                        **{
                            key: str(inventory[key])
                            for key in (
                                "base_amount",
                                "base_fee",
                                "quote_amount",
                                "quote_fee",
                                "base_total",
                                "quote_total",
                            )
                        },
                        "source": inventory["source"],
                        "executor_status": inventory["executor_status"],
                        "close_type": inventory["close_type"],
                    },
                },
            }
            _shared.write_state(session, strategy_config, state)
    if base_total * pool["current_price"] <= strategy_config.inventory_dust_quote:
        state = _close._locked_state(session, strategy_config)
        pending = deepcopy(state["slots"][config.slot_id]["pending_mutation"])
        _close._checkpoint(
            session,
            strategy_config,
            config.slot_id,
            pending,
            _close._clear_selected,
        )
        return _result(
            config,
            "recovered",
            operation_id=operation_id,
            returned_inventory=_shared.json_value(
                pending["confirmed"]["returned_inventory"], redact=False
            ),
            restoration="not_required",
        )
    return await _close._submit_restore(
        config, session, strategy_config, client, operation_id
    )


async def _execute(config: Config, context: Any, debug: dict[str, Any]) -> str:
    try:
        debug["stage"] = "resolve_and_validate"
        session = _shared.resolve_session(config.controller_id)
        _shared.require_live(session)
        strategy_config = _shared.load_config(session)
        state = _close._locked_state(session, strategy_config)
        if config.slot_id not in state["slots"]:
            raise ValueError("selected slot does not exist")
        slot = state["slots"][config.slot_id]
        if slot["position"] is None and slot["pending_mutation"] is None:
            return _result(config, "nothing_to_recover")
        debug["mutation_claimed"] = True
        blockers = _close._pending_blockers(state, config.slot_id)
        if blockers:
            return _result(
                config,
                "blocked",
                error="another slot in this session has an unresolved mutation",
                slot_blockers=blockers,
            )
        binding = state["binding"]
        if not isinstance(binding, dict):
            raise ValueError("persisted binding is required")
        debug["stage"] = "bind_server_and_wallet"
        bound = await _shared.bound_client(context, binding["server_name"])
        wallet = await _shared.default_solana_wallet(bound.client)
        _close._verify_binding(state, bound.server_name, wallet)
        pending = deepcopy(slot["pending_mutation"])
        if pending is None:
            debug["stage"] = "reconcile_native_terminal"
            return await _native_terminal(
                config,
                session,
                strategy_config,
                bound.client,
                wallet,
                deepcopy(slot["position"]),
            )
        operation_id = pending["operation_id"]
        debug["operation_id"] = operation_id
        debug["mutation_claimed"] = True
        if pending["status"] == "intent":
            debug["stage"] = "submit_unattempted_intent"
            return await _submit_intent(
                config,
                session,
                strategy_config,
                bound.client,
                wallet,
                operation_id,
            )
        if pending["step"] == "stop":
            debug["stage"] = "reconcile_stop"
            return await _close._reconcile_close(
                config,
                session,
                strategy_config,
                bound.client,
                wallet,
                operation_id,
            )
        if pending["step"] == "restore":
            debug["stage"] = "reconcile_restore"
            return await _reconcile_restore(
                config,
                session,
                strategy_config,
                bound.client,
                wallet,
                operation_id,
            )
        if pending["step"] == "rebalance":
            debug["stage"] = "reconcile_rebalance"
            return await _reconcile_rebalance(
                config,
                session,
                strategy_config,
                bound.client,
                wallet,
                operation_id,
            )
        if pending["step"] == "create":
            debug["stage"] = "reconcile_create"
            return await _reconcile_create(
                config,
                session,
                strategy_config,
                bound.client,
                wallet,
                operation_id,
            )
        raise ValueError("persisted mutation step is unsupported")
    except Exception as error:
        debug.update(
            {
                "exception_type": type(error).__name__,
                "exception": str(error),
                "traceback": traceback.format_exc(),
            }
        )
        return _result(config, "error", error=_error(error))


async def run(config: Config, context: Any):
    started_at = _now()
    started_monotonic = time.monotonic()
    debug: dict[str, Any] = {}
    try:
        required = ("controller_id", "slot_id")
        missing = [name for name in required if getattr(config, name) is None]
        if missing:
            output = _reporting.sample_payload("recover", config, missing)
        else:
            output = await _execute(config, context, debug)
    except asyncio.CancelledError:
        await _reporting.finish_cancelled(
            "recover",
            config,
            started_at=started_at,
            started_monotonic=started_monotonic,
            debug=debug,
        )
        raise
    return await _reporting.finish(
        "recover",
        config,
        output,
        started_at=started_at,
        started_monotonic=started_monotonic,
        debug=debug,
    )
