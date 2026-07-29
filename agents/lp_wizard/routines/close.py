import asyncio
import json
import re
import time
import traceback
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from agents.lp_wizard.routines import _reporting, _shared
from agents.lp_wizard.routines import open as _open

CATEGORY = "LP Wizard"
_SLOT_RE = re.compile(r"^slot-[0-9]{2,}$")


class Config(BaseModel):
    """Close the exact position persisted in one live LP slot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    controller_id: StrictStr | None = None
    slot_id: StrictStr | None = None
    reason: StrictStr | None = Field(default=None, max_length=160)

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

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str | None) -> str | None:
        if value is not None and (not value or value != value.strip()):
            raise ValueError(
                "reason must be concise and have no surrounding whitespace"
            )
        return value


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


def _locked_state(
    session: _shared.Session, strategy_config: _shared.Config
) -> dict[str, Any]:
    with _shared.wallet_lock(session):
        with _shared.state_lock(session):
            if _shared.load_config(session) != strategy_config:
                raise ValueError("immutable session config changed")
            state = _shared.read_state(session, strategy_config)
            if state is None:
                raise ValueError("state.json does not exist")
            return state


def _checkpoint(
    session: _shared.Session,
    strategy_config: _shared.Config,
    slot_id: str,
    expected: dict[str, Any],
    update: Callable[[dict[str, Any], dict[str, Any]], None],
) -> dict[str, Any]:
    with _shared.wallet_lock(session):
        with _shared.state_lock(session):
            if _shared.load_config(session) != strategy_config:
                raise ValueError("immutable session config changed")
            state = _shared.read_state(session, strategy_config)
            if state is None:
                raise ValueError("state.json disappeared")
            slot = state["slots"].get(slot_id)
            if slot is None:
                raise ValueError("selected slot does not exist")
            pending = slot["pending_mutation"]
            if not isinstance(pending, dict) or _shared.json_value(
                pending, redact=False
            ) != _shared.json_value(expected, redact=False):
                raise ValueError("selected mutation changed concurrently")
            predecessor = deepcopy(pending)
            update(state, slot)
            successor = slot["pending_mutation"]
            if successor is not None:
                if (
                    successor.get("operation_id") != predecessor["operation_id"]
                    or successor.get("type") != predecessor["type"]
                ):
                    raise ValueError("mutation transition changed operation identity")
                allowed_steps = {
                    ("rebalance", "create"),
                    ("rebalance", "restore"),
                    ("create", "restore"),
                    ("stop", "restore"),
                }
                if (
                    successor.get("step") != predecessor["step"]
                    and (predecessor["step"], successor.get("step"))
                    not in allowed_steps
                ):
                    raise ValueError("mutation transition changed step illegally")
                if (
                    successor.get("step") == predecessor["step"]
                    and predecessor["status"] in {"submitted", "uncertain"}
                    and successor.get("status") == "intent"
                ):
                    raise ValueError("attempted mutation cannot regress to intent")
            _shared.write_state(session, strategy_config, state)
            return state


def _pending_blockers(state: dict[str, Any], slot_id: str) -> list[str]:
    return [
        current_id
        for current_id, slot in state["slots"].items()
        if current_id != slot_id and slot["pending_mutation"] is not None
    ]


def _verify_binding(
    state: dict[str, Any], server_name: str, wallet_address: str
) -> dict[str, Any]:
    binding = state["binding"]
    if not isinstance(binding, dict):
        raise ValueError("persisted binding is required")
    if binding["server_name"] != server_name:
        raise ValueError("current server differs from persisted binding")
    if binding["observed_default_wallet"] != wallet_address:
        raise ValueError("current default Solana wallet differs from persisted binding")
    return binding


def _evidence_bound(raw: dict[str, Any], name: str) -> Decimal | None:
    camel = "".join(
        word if index == 0 else word.title()
        for index, word in enumerate(name.split("_"))
    )
    values = [
        _shared.read(raw, path)
        for path in (name, camel, f"config.{name}", f"custom_info.{name}")
        if _shared.read(raw, path) not in (None, "")
    ]
    if not values:
        return None
    parsed = {_shared.decimal(value, name, positive=True) for value in values}
    if len(parsed) != 1:
        raise ValueError(f"conflicting {name} evidence")
    return next(iter(parsed))


def _verify_position_evidence(
    position: dict[str, Any],
    executor: dict[str, Any],
    pool: dict[str, Any],
    owned: list[dict[str, Any]],
    *,
    terminal: bool = False,
) -> None:
    if executor["executor_id"] != position["executor_id"]:
        raise ValueError("executor identity contradicts persisted position")
    if executor["controller_id"] != position["controller_id"]:
        raise ValueError("executor controller contradicts persisted position")
    if executor["pool_address"] != position["pool_address"]:
        raise ValueError("executor pool contradicts persisted position")
    if terminal:
        if executor["is_active"]:
            raise ValueError("executor is not terminal")
        if (
            not executor["position_address_present"]
            or executor["position_address"] is not None
        ):
            raise ValueError(
                "terminal executor position address is not cleared explicitly"
            )
    else:
        if not executor["is_active"]:
            raise ValueError("executor is not active")
        if executor["position_address"] != position["position_address"]:
            raise ValueError("executor position address contradicts persisted position")
    if executor["config"].get("keep_position") is not True:
        raise ValueError("executor config does not prove keep_position true")
    if (
        pool["base_mint"] != position["base_mint"]
        or pool["quote_mint"] != position["quote_mint"]
    ):
        raise ValueError("Gateway pool token identity contradicts persisted position")
    matches = [
        item
        for item in owned
        if item["position_address"] == position["position_address"]
    ]
    if not terminal and len(matches) != 1:
        raise ValueError("exact persisted Gateway position is not uniquely owned")
    if terminal and matches:
        raise ValueError("exact persisted Gateway position is still owned")
    for name in (
        "lower_price",
        "upper_price",
        "lower_limit_price",
        "upper_limit_price",
    ):
        expected = _shared.decimal(position[name], name, positive=True)
        actual = _evidence_bound(executor["config"], name)
        if actual is None or not _shared.decimal_matches(actual, expected, name):
            raise ValueError(f"executor {name} contradicts persisted position")
    if matches:
        gateway_lower = _evidence_bound(matches[0]["raw"], "lower_price")
        gateway_upper = _evidence_bound(matches[0]["raw"], "upper_price")
        if (gateway_lower is None) != (gateway_upper is None):
            raise ValueError("Gateway position range evidence is incomplete")
        if gateway_lower is not None and not (
            _shared.decimal(position["lower_limit_price"], positive=True)
            < gateway_lower
            < gateway_upper
            < _shared.decimal(position["upper_limit_price"], positive=True)
        ):
            raise ValueError("Gateway position range exceeds persisted safety limits")
        if gateway_lower is not None:
            requested_lower = _shared.decimal(position["lower_price"], positive=True)
            requested_upper = _shared.decimal(position["upper_price"], positive=True)
            snap_tolerance = (requested_upper - requested_lower) / 10
            if (
                abs(gateway_lower - requested_lower) > snap_tolerance
                or abs(gateway_upper - requested_upper) > snap_tolerance
            ):
                raise ValueError("Gateway position range deviates from requested range")


def _returned_inventory(executor: dict[str, Any]) -> dict[str, Any]:
    if executor["is_active"]:
        raise ValueError("terminal close receipt requires an inactive executor")
    if _shared.text(executor["custom_state"]) != "COMPLETE":
        raise ValueError("terminal close receipt custom state is not COMPLETE")
    if executor["close_type"] != "POSITION_HOLD":
        raise ValueError("terminal close receipt close_type is not POSITION_HOLD")
    custom = executor["custom_info"]
    values = {}
    for name in ("base_amount", "base_fee", "quote_amount", "quote_fee"):
        if name not in custom:
            raise ValueError(f"terminal close receipt is missing {name}")
        value = _shared.decimal(custom[name], f"terminal close receipt {name}")
        if value < 0:
            raise ValueError(f"terminal close receipt {name} is negative")
        values[name] = value
    values["base_total"] = values["base_amount"] + values["base_fee"]
    values["quote_total"] = values["quote_amount"] + values["quote_fee"]
    if values["base_total"] <= 0 and values["quote_total"] <= 0:
        raise ValueError("terminal close receipt returned no inventory")
    return {
        **values,
        "source": "executor_close_receipt",
        "executor_status": executor["status"],
        "close_type": executor["close_type"],
    }


def _native_limit_evidence(
    position: dict[str, Any], executor: dict[str, Any]
) -> Decimal:
    price = _shared.decimal(
        executor.get("current_price"), "terminal current price", positive=True
    )
    lower = _shared.decimal(
        position["lower_limit_price"], "lower_limit_price", positive=True
    )
    upper = _shared.decimal(
        position["upper_limit_price"], "upper_limit_price", positive=True
    )
    if lower < price < upper:
        raise ValueError(
            "terminal current price is not at or beyond a persisted native limit"
        )
    return price


def _local_stop_provenance(position: dict[str, Any], pending: dict[str, Any]) -> bool:
    request = pending.get("request")
    if (
        pending.get("type") != "close"
        or pending.get("step") != "stop"
        or not isinstance(request, dict)
        or request.get("executor_id") != position["executor_id"]
        or request.get("controller_id") != position["controller_id"]
        or request.get("pool_address") != position["pool_address"]
        or request.get("position_address") != position["position_address"]
        or request.get("keep_position") is not True
    ):
        raise ValueError("persisted stop provenance contradicts selected position")
    if pending.get("status") in {"submitted", "uncertain"}:
        if pending.get("attempted_at") is None:
            raise ValueError("persisted submitted stop has no attempted_at")
        return True
    if pending.get("status") == "intent" and pending.get("attempted_at") is None:
        return False
    raise ValueError("persisted stop provenance has an invalid submission state")


def _transaction_matches(evidence: dict[str, Any], request: dict[str, Any]) -> bool:
    amount = _shared.decimal(
        request.get("amount", request.get("restore_base_amount")),
        "restore amount",
        positive=True,
    )
    slippage = _shared.decimal(
        request.get("slippage_pct"), "restore slippage", positive=True
    )
    base_proven = (
        evidence["base_mint"] in (None, request["base_mint"])
        and evidence["base_token"]
        in (None, request["base_mint"], request.get("base_symbol"))
        and (evidence["base_mint"] is not None or evidence["base_token"] is not None)
    )
    quote_proven = (
        evidence["quote_mint"] in (None, request["quote_mint"])
        and evidence["quote_token"]
        in (None, request["quote_mint"], request.get("quote_symbol"))
        and (evidence["quote_mint"] is not None or evidence["quote_token"] is not None)
    )

    return (
        evidence["status"] in {"CONFIRMED", "SUCCESS", "COMPLETED"}
        and evidence["side"] == "SELL"
        and evidence["trading_pair"] == request["trading_pair"]
        and evidence["input_amount"] == amount
        and evidence["output_amount"] is not None
        and evidence["input_amount"] > 0
        and evidence["output_amount"] > 0
        and evidence["connector"] == "jupiter"
        and evidence["network"] == _shared.NETWORK
        and evidence["wallet_address"] == request["wallet_address"]
        and evidence["slippage_pct"] == slippage
        and base_proven
        and quote_proven
    )


def _clear_selected(state: dict[str, Any], slot: dict[str, Any]) -> None:
    slot["position"] = None
    slot["pending_mutation"] = None
    if not any(
        item["position"] is not None or item["pending_mutation"] is not None
        for item in state["slots"].values()
    ):
        state["binding"] = None


async def _reconcile_close(
    config: Config,
    session: _shared.Session,
    strategy_config: _shared.Config,
    client: Any,
    wallet: str,
    operation_id: str,
) -> str:
    state = _locked_state(session, strategy_config)
    slot = state["slots"][config.slot_id]
    position = deepcopy(slot["position"])
    pending = deepcopy(slot["pending_mutation"])
    if position is None or pending is None:
        raise ValueError("close ownership checkpoint is missing")
    try:
        local_stop = _local_stop_provenance(position, pending)
    except ValueError as error:
        return _result(
            config,
            "manual_blocked",
            operation_id=operation_id,
            step="stop",
            error=str(error),
        )
    executor = await _shared.get_executor_evidence(
        client, position["executor_id"], session.controller_id
    )
    pool = await _shared.get_gateway_pool(client, position["pool_address"])
    owned = await _shared.get_owned_positions(client, position["pool_address"], wallet)
    if executor["is_active"]:
        return _result(
            config,
            "pending",
            operation_id=operation_id,
            step="stop",
            error="executor is still active or closing",
        )
    if executor["status"] in {"ERROR", "FAILED"}:
        return _result(
            config,
            "manual_blocked",
            operation_id=operation_id,
            step="stop",
            error=f"executor terminated with {executor['status']}",
        )
    try:
        _verify_position_evidence(position, executor, pool, owned, terminal=True)
        inventory = _returned_inventory(executor)
        terminal_price = (
            None if local_stop else _native_limit_evidence(position, executor)
        )
    except ValueError as error:
        return _result(
            config,
            "manual_blocked",
            operation_id=operation_id,
            step="stop",
            error=str(error),
        )
    base_total = inventory["base_total"]
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

    def restoration_intent(state: dict[str, Any], selected: dict[str, Any]) -> None:
        selected["pending_mutation"] = {
            "operation_id": operation_id,
            "type": "close",
            "step": "restore",
            "status": "intent",
            "request": request,
            "attempted_at": None,
            "external_id": None,
            "confirmed": {
                "stop": {
                    "executor_status": executor["status"],
                    "position_absent": True,
                    **(
                        {}
                        if terminal_price is None
                        else {
                            "close_type": inventory["close_type"],
                            "current_price": str(terminal_price),
                        }
                    ),
                },
                "returned_inventory": {
                    "position_address": position["position_address"],
                    **{
                        key: str(value)
                        for key, value in inventory.items()
                        if isinstance(value, Decimal)
                    },
                    "source": inventory["source"],
                    "executor_status": inventory["executor_status"],
                    "close_type": inventory["close_type"],
                },
            },
        }

    restored = _checkpoint(
        session, strategy_config, config.slot_id, pending, restoration_intent
    )
    restore_pending = deepcopy(restored["slots"][config.slot_id]["pending_mutation"])
    if base_total * pool["current_price"] <= strategy_config.inventory_dust_quote:
        _checkpoint(
            session,
            strategy_config,
            config.slot_id,
            restore_pending,
            _clear_selected,
        )
        return _result(
            config,
            "closed",
            operation_id=operation_id,
            returned_inventory=_shared.json_value(
                restore_pending["confirmed"]["returned_inventory"], redact=False
            ),
            restoration="not_required",
        )
    return await _submit_restore(config, session, strategy_config, client, operation_id)


async def _submit_restore(
    config: Config,
    session: _shared.Session,
    strategy_config: _shared.Config,
    client: Any,
    operation_id: str,
) -> str:
    state = _locked_state(session, strategy_config)
    pending = deepcopy(state["slots"][config.slot_id]["pending_mutation"])
    if pending["step"] != "restore" or pending["status"] != "intent":
        raise ValueError("restore is not an unattempted intent")
    persisted_request = pending["request"]
    nested = persisted_request.get("restore_swap")
    request = (
        {
            **nested,
            "pool_address": persisted_request.get("pool_address"),
            "position_address": persisted_request.get("position_address"),
            "base_symbol": nested.get("base_symbol"),
            "quote_symbol": nested.get("quote_symbol"),
        }
        if isinstance(nested, dict)
        else persisted_request
    )
    binding = state["binding"]
    if not isinstance(binding, dict):
        raise ValueError("restore requires the persisted binding")
    amount = _shared.decimal(
        request.get("amount", request.get("restore_base_amount")),
        "restore amount",
        positive=True,
    )
    slippage = _shared.decimal(
        request.get("slippage_pct"), "restore slippage", positive=True
    )
    expected_pairs = {f"{request.get('base_mint')}-{request.get('quote_mint')}"}
    if _shared.text(request.get("base_symbol")) and _shared.text(
        request.get("quote_symbol")
    ):
        expected_pairs.add(f"{request['base_symbol']}-{request['quote_symbol']}")
    if (
        request.get("connector") != "jupiter"
        or request.get("network") != _shared.NETWORK
        or request.get("side") != "SELL"
        or request.get("wallet_address") != binding["observed_default_wallet"]
        or slippage != strategy_config.max_slippage_pct
        or request.get("trading_pair") not in expected_pairs
        or not _shared.text(request.get("base_mint"))
        or not _shared.text(request.get("quote_mint"))
    ):
        raise ValueError("persisted restore request identity is invalid")
    returned = pending["confirmed"].get("returned_inventory")
    rebalance = pending["confirmed"].get("rebalance")
    if isinstance(returned, dict):
        components = {
            name: _shared.decimal(returned.get(name), f"returned {name}")
            for name in ("base_amount", "base_fee", "quote_amount", "quote_fee")
        }
        if any(value < 0 for value in components.values()):
            raise ValueError("returned inventory contains a negative amount")
        attributed = components["base_amount"] + components["base_fee"]
        quote_total = components["quote_amount"] + components["quote_fee"]
        if (
            _shared.decimal(returned.get("base_total"), "returned base total")
            != attributed
            or _shared.decimal(returned.get("quote_total"), "returned quote total")
            != quote_total
            or (attributed <= 0 and quote_total <= 0)
            or returned.get("position_address") != request.get("position_address")
            or returned.get("source") != "executor_close_receipt"
            or returned.get("close_type") != "POSITION_HOLD"
            or not _shared.text(returned.get("executor_status"))
        ):
            raise ValueError("returned inventory is not position-linked")
        if amount != attributed:
            raise ValueError("restore amount must equal returned base total")
    elif isinstance(rebalance, dict):
        attributed = _shared.decimal(
            rebalance.get("output_amount"), "attributed rebalance output", positive=True
        )
        if (
            rebalance.get("status") not in {"CONFIRMED", "SUCCESS", "COMPLETED"}
            or not _shared.text(rebalance.get("transaction_hash"))
            or rebalance.get("side") != "BUY"
            or rebalance.get("trading_pair") != request["trading_pair"]
            or rebalance.get("base_token")
            not in {request["base_mint"], request.get("base_symbol")}
            or rebalance.get("quote_token")
            not in {request["quote_mint"], request.get("quote_symbol")}
        ):
            raise ValueError("restore rebalance attribution is incomplete")
    else:
        raise ValueError(
            "restore has no explicit position- or transaction-linked attribution"
        )
    if not isinstance(returned, dict) and amount > attributed:
        raise ValueError("restore amount exceeds attributed inventory")

    pool_address = request.get("pool_address")
    if not _shared.text(pool_address):
        raise ValueError("restore request has no attributed pool address")
    pool = await _shared.get_gateway_pool(client, pool_address)
    if (
        pool["base_mint"] != request["base_mint"]
        or pool["quote_mint"] != request["quote_mint"]
    ):
        raise ValueError("restore pool mint identity changed")
    balances = await _shared.read_balances(client, strategy_config.account_name)
    available = _shared.balance_for_token(
        balances,
        request["base_mint"],
        request.get("base_symbol") or request["base_mint"],
    )
    reserve_shortfall = (
        request["base_mint"] == _shared.WRAPPED_SOL_MINT
        and available - amount < strategy_config.min_sol_reserve
    )
    if available < amount or reserve_shortfall:
        return _result(
            config,
            "manual_blocked",
            operation_id=operation_id,
            step="restore",
            error=(
                "scoped wallet SOL balance would fall below the minimum reserve"
                if reserve_shortfall
                else "scoped wallet base balance is below exact restoration amount"
            ),
            required_base_amount=amount,
            available_base_amount=available,
            **(
                {"min_sol_reserve": strategy_config.min_sol_reserve}
                if reserve_shortfall
                else {}
            ),
        )
    quote = await client.gateway_swap.get_swap_quote(
        connector="jupiter",
        network=_shared.NETWORK,
        trading_pair=request["trading_pair"],
        side="SELL",
        amount=amount,
        slippage_pct=slippage,
    )
    _open._quote_result(
        quote,
        request["trading_pair"],
        "SELL",
        amount,
        pool["current_price"],
        slippage,
    )

    def submitted(state: dict[str, Any], slot: dict[str, Any]) -> None:
        current = slot["pending_mutation"]
        if (
            current["status"] != "intent"
            or current["attempted_at"] is not None
            or current["external_id"] is not None
        ):
            raise ValueError("restore intent was already attempted")
        current["status"] = "submitted"
        current["attempted_at"] = _now()

    submitted_state = _checkpoint(
        session, strategy_config, config.slot_id, pending, submitted
    )
    submitted_pending = deepcopy(
        submitted_state["slots"][config.slot_id]["pending_mutation"]
    )
    try:
        response = await client.gateway_swap.execute_swap(
            connector="jupiter",
            network=_shared.NETWORK,
            trading_pair=request["trading_pair"],
            side="SELL",
            amount=amount,
            slippage_pct=slippage,
            wallet_address=request["wallet_address"],
        )
        transaction = _shared.normalize_transaction(response)
    except BaseException as error:

        def uncertain(state: dict[str, Any], slot: dict[str, Any]) -> None:
            slot["pending_mutation"]["status"] = "uncertain"
            slot["pending_mutation"]["confirmed"]["submission_error"] = _error(error)

        _checkpoint(
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
            step="restore",
            error=_error(error),
        )

    def submitted_result(state: dict[str, Any], slot: dict[str, Any]) -> None:
        current = slot["pending_mutation"]
        current["external_id"] = transaction["transaction_hash"]
        current["confirmed"]["submission"] = _shared.json_value(response, redact=False)

    result_state = _checkpoint(
        session, strategy_config, config.slot_id, submitted_pending, submitted_result
    )
    result_pending = deepcopy(result_state["slots"][config.slot_id]["pending_mutation"])
    evidence = await _shared.get_swap_evidence(client, transaction["transaction_hash"])
    if not _transaction_matches(evidence, request):
        return _result(
            config,
            (
                "pending"
                if evidence["status"] not in {"FAILED", "REJECTED"}
                else "manual_blocked"
            ),
            operation_id=operation_id,
            step="restore",
            error=f"restore transaction status/evidence is {evidence['status'] or 'ambiguous'}",
        )
    _checkpoint(
        session, strategy_config, config.slot_id, result_pending, _clear_selected
    )
    return _result(
        config,
        "closed",
        operation_id=operation_id,
        restoration_transaction_hash=transaction["transaction_hash"],
        **(
            {"returned_inventory": _shared.json_value(returned, redact=False)}
            if isinstance(returned, dict)
            else {}
        ),
    )


async def _execute(config: Config, context: Any, debug: dict[str, Any]) -> str:
    try:
        debug["stage"] = "resolve_and_validate"
        session = _shared.resolve_session(config.controller_id)
        _shared.require_live(session)
        strategy_config = _shared.load_config(session)
        state = _locked_state(session, strategy_config)
        if config.slot_id not in state["slots"]:
            raise ValueError("selected slot does not exist")
        slot = state["slots"][config.slot_id]
        if slot["position"] is None:
            raise ValueError("selected slot has no persisted position")
        if slot["pending_mutation"] is not None:
            return _result(
                config,
                "recoverable",
                error="selected slot already has an unresolved mutation; use recover",
            )
        blockers = _pending_blockers(state, config.slot_id)
        if blockers:
            return _result(
                config,
                "blocked",
                error="another slot in this session has an unresolved mutation",
                slot_blockers=blockers,
            )
        position = deepcopy(slot["position"])
        binding = deepcopy(state["binding"])
        if not isinstance(binding, dict):
            raise ValueError("persisted binding is required")

        debug["stage"] = "read_external_position"
        bound = await _shared.bound_client(context, binding["server_name"])
        wallet = await _shared.default_solana_wallet(bound.client)
        _verify_binding(state, bound.server_name, wallet)
        executor = await _shared.get_executor_evidence(
            bound.client, position["executor_id"], session.controller_id
        )
        pool = await _shared.get_gateway_pool(bound.client, position["pool_address"])
        owned = await _shared.get_owned_positions(
            bound.client, position["pool_address"], wallet
        )
        if not executor["is_active"]:
            raise ValueError(
                "executor is already terminal; use recover for evidence-only settlement"
            )
        _verify_position_evidence(position, executor, pool, owned)

        debug["stage"] = "persist_close_intent"
        operation_id = uuid4().hex
        debug["operation_id"] = operation_id
        request = {
            "executor_id": position["executor_id"],
            "controller_id": position["controller_id"],
            "pool_address": position["pool_address"],
            "position_address": position["position_address"],
            "keep_position": True,
            "reason": config.reason,
        }
        with _shared.wallet_lock(session):
            with _shared.state_lock(session):
                if _shared.load_config(session) != strategy_config:
                    raise ValueError("immutable session config changed")
                current = _shared.read_state(session, strategy_config)
                if (
                    current is None
                    or current["slots"][config.slot_id]["position"] != position
                ):
                    raise ValueError("selected position changed concurrently")
                if current["slots"][config.slot_id]["pending_mutation"] is not None:
                    raise ValueError("selected slot acquired a concurrent mutation")
                if current["binding"] != binding:
                    raise ValueError("persisted binding changed concurrently")
                if _pending_blockers(current, config.slot_id):
                    raise ValueError(
                        "another slot in this session acquired an unresolved mutation"
                    )
                intent = {
                    "operation_id": operation_id,
                    "type": "close",
                    "step": "stop",
                    "status": "intent",
                    "request": request,
                    "attempted_at": None,
                    "external_id": None,
                    "confirmed": {},
                }
                current["slots"][config.slot_id]["pending_mutation"] = intent
                _shared.write_state(session, strategy_config, current)
                debug["mutation_claimed"] = True

        def submitted(state: dict[str, Any], selected: dict[str, Any]) -> None:
            pending = selected["pending_mutation"]
            if (
                pending["status"] != "intent"
                or pending["attempted_at"] is not None
                or pending["external_id"] is not None
            ):
                raise ValueError("stop intent was already attempted")
            pending["status"] = "submitted"
            pending["attempted_at"] = _now()

        submitted_state = _checkpoint(
            session, strategy_config, config.slot_id, intent, submitted
        )
        submitted_pending = deepcopy(
            submitted_state["slots"][config.slot_id]["pending_mutation"]
        )
        debug["stage"] = "stop_executor"
        try:
            acknowledgement = await bound.client.executors.stop_executor(
                executor_id=position["executor_id"], keep_position=True
            )
        except BaseException as error:
            debug.update(
                {
                    "submission_exception_type": type(error).__name__,
                    "submission_exception": str(error),
                    "submission_traceback": traceback.format_exc(),
                }
            )

            def uncertain(state: dict[str, Any], selected: dict[str, Any]) -> None:
                selected["pending_mutation"]["status"] = "uncertain"
                selected["pending_mutation"]["confirmed"]["submission_error"] = _error(
                    error
                )

            _checkpoint(
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
                step="stop",
                error=_error(error),
            )

        def acknowledged(state: dict[str, Any], selected: dict[str, Any]) -> None:
            selected["pending_mutation"]["confirmed"]["stop_acknowledgement"] = (
                _shared.json_value(acknowledgement, redact=False)
            )

        _checkpoint(
            session, strategy_config, config.slot_id, submitted_pending, acknowledged
        )
        debug["stage"] = "reconcile_close"
        return await _reconcile_close(
            config, session, strategy_config, bound.client, wallet, operation_id
        )
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
            output = _reporting.sample_payload("close", config, missing)
        else:
            output = await _execute(config, context, debug)
    except asyncio.CancelledError:
        await _reporting.finish_cancelled(
            "close",
            config,
            started_at=started_at,
            started_monotonic=started_monotonic,
            debug=debug,
        )
        raise
    return await _reporting.finish(
        "close",
        config,
        output,
        started_at=started_at,
        started_monotonic=started_monotonic,
        debug=debug,
    )
