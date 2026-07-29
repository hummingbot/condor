import asyncio
import json
import time
import traceback
from copy import deepcopy
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Any, Callable
from urllib.parse import urlencode
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, StrictStr, field_validator

from agents.lp_wizard.routines import _pool_policy, _reporting, _shared, scan

CATEGORY = "LP Wizard"
MAX_PRICE_DEVIATION = Decimal("0.01")
JUPITER = "jupiter"
LP_PROVIDER = "orca/clmm"


class Config(BaseModel):
    """Open one controller-bound LP slot from a complete fresh scan candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    controller_id: StrictStr | None = None
    slot_id: StrictStr | None = None
    candidate: dict | None = None
    range_option: StrictStr | None = None
    amount_quote: Decimal | None = None

    @field_validator("controller_id", "slot_id", "range_option")
    @classmethod
    def _nonempty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("controller_id")
    @classmethod
    def _controller_id(cls, value: str | None) -> str | None:
        return _shared.validate_controller_id(value) if value is not None else None

    @field_validator("amount_quote", mode="before")
    @classmethod
    def _amount_quote(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        return _shared.decimal(value, "amount_quote", positive=True)


def _compact(value: dict[str, Any]) -> str:
    return json.dumps(_shared.json_value(value), separators=(",", ":"), sort_keys=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _binding(
    session: _shared.Session,
    strategy_config: _shared.Config,
    server_name: str,
    wallet: str,
) -> dict[str, Any]:
    return {
        "session_number": session.number,
        "server_name": server_name,
        "account_name": strategy_config.account_name,
        "network": _shared.NETWORK,
        "observed_default_wallet": wallet,
        "controller_id": session.controller_id,
    }


def _assert_local_open(
    state: dict[str, Any],
    session: _shared.Session,
    strategy_config: _shared.Config,
    slot_id: str,
    pool_address: str,
    amount_quote: Decimal,
) -> None:
    if slot_id not in state["slots"]:
        raise ValueError("slot_id is not configured for this session")
    slot = state["slots"][slot_id]
    if slot["position"] is not None or slot["pending_mutation"] is not None:
        raise ValueError("selected slot is not empty")
    if any(
        other["pending_mutation"] is not None
        for other_id, other in state["slots"].items()
        if other_id != slot_id
    ):
        raise ValueError("another slot has an unresolved mutation")
    capital = _shared.capital(state, strategy_config)
    if amount_quote > capital["free_quote"]:
        raise ValueError("amount_quote exceeds aggregate free capital")
    if pool_address in capital["pool_usage"]:
        raise ValueError("pool is already occupied or pending in another slot")
    if not (
        strategy_config.min_capital_per_slot_quote
        <= amount_quote
        <= strategy_config.max_capital_per_slot_quote
    ):
        raise ValueError("amount_quote is outside configured per-slot bounds")
    if state["binding"] is not None:
        binding = state["binding"]
        if (
            binding["controller_id"] != session.controller_id
            or binding["account_name"] != strategy_config.account_name
            or binding["network"] != _shared.NETWORK
        ):
            raise ValueError("persisted session binding conflicts with this open")


def _locked_state(
    session: _shared.Session, strategy_config: _shared.Config
) -> dict[str, Any]:
    current_config = _shared.load_config(session)
    if current_config != strategy_config:
        raise ValueError("immutable session config changed during open")
    return _shared.ensure_state(session, strategy_config)


def _claim(
    session: _shared.Session,
    strategy_config: _shared.Config,
    slot_id: str,
    pool_address: str,
    amount_quote: Decimal,
    binding: dict[str, Any],
    pending: dict[str, Any],
) -> None:
    with _shared.wallet_lock(session):
        with _shared.state_lock(session):
            state = _locked_state(session, strategy_config)
            _assert_local_open(
                state, session, strategy_config, slot_id, pool_address, amount_quote
            )
            if state["binding"] is not None and state["binding"] != binding:
                raise ValueError(
                    "current server or default wallet conflicts with binding"
                )
            state["binding"] = binding
            state["slots"][slot_id]["pending_mutation"] = deepcopy(pending)
            _shared.write_state(session, strategy_config, state)


def _finish_position(
    session: _shared.Session,
    strategy_config: _shared.Config,
    slot_id: str,
    expected_pending: dict[str, Any],
    position: dict[str, Any],
) -> None:
    with _shared.wallet_lock(session):
        with _shared.state_lock(session):
            state = _locked_state(session, strategy_config)
            if state["slots"][slot_id]["pending_mutation"] != _shared.json_value(
                expected_pending, redact=False
            ):
                raise ValueError("create reconciliation lost mutation ownership")
            state["slots"][slot_id] = {
                "position": deepcopy(position),
                "pending_mutation": None,
            }
            _shared.write_state(session, strategy_config, state)


def _clear_restored(
    session: _shared.Session,
    strategy_config: _shared.Config,
    slot_id: str,
    expected_pending: dict[str, Any],
) -> None:
    with _shared.wallet_lock(session):
        with _shared.state_lock(session):
            state = _locked_state(session, strategy_config)
            if state["slots"][slot_id]["pending_mutation"] != _shared.json_value(
                expected_pending, redact=False
            ):
                raise ValueError("restoration lost mutation ownership")
            state["slots"][slot_id] = {"position": None, "pending_mutation": None}
            if not any(
                slot["position"] is not None or slot["pending_mutation"] is not None
                for slot in state["slots"].values()
            ):
                state["binding"] = None
            _shared.write_state(session, strategy_config, state)


def _clear_failed_open(
    session: _shared.Session,
    strategy_config: _shared.Config,
    slot_id: str,
    expected_pending: dict[str, Any],
) -> None:
    with _shared.wallet_lock(session):
        with _shared.state_lock(session):
            state = _locked_state(session, strategy_config)
            if state["slots"][slot_id]["pending_mutation"] != _shared.json_value(
                expected_pending, redact=False
            ):
                raise ValueError("failed create proof changed concurrently")
            state["slots"][slot_id] = {"position": None, "pending_mutation": None}
            if not any(
                slot["position"] is not None or slot["pending_mutation"] is not None
                for slot in state["slots"].values()
            ):
                state["binding"] = None
            _shared.write_state(session, strategy_config, state)


def _replace_pending_exact(
    session: _shared.Session,
    strategy_config: _shared.Config,
    slot_id: str,
    predecessor: dict[str, Any],
    replacement: dict[str, Any],
) -> None:
    with _shared.wallet_lock(session):
        with _shared.state_lock(session):
            state = _locked_state(session, strategy_config)
            if state["slots"][slot_id]["pending_mutation"] != _shared.json_value(
                predecessor, redact=False
            ):
                raise ValueError("open mutation predecessor changed concurrently")
            state["slots"][slot_id]["pending_mutation"] = deepcopy(replacement)
            _shared.write_state(session, strategy_config, state)


def _pending(
    operation_id: str,
    step: str,
    status: str,
    request: dict[str, Any],
    confirmed: dict[str, Any],
    *,
    attempted_at: str | None = None,
    external_id: str | None = None,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "type": "open",
        "step": step,
        "status": status,
        "request": deepcopy(request),
        "attempted_at": attempted_at,
        "external_id": external_id,
        "confirmed": deepcopy(confirmed),
    }


async def _refresh_orca(
    candidate: dict[str, Any], risk_profile: str, option_name: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    category = candidate["source_categories"][0]
    lens = candidate["source_lenses"][0]
    query = urlencode(
        {
            "addresses": candidate["pool_address"],
            "size": 1,
            "stats": ",".join(_pool_policy.LIVE_STATS),
        }
    )
    url = f"{scan.ORCA_POOL_URL}?{query}"
    rows, _ = await asyncio.to_thread(
        scan._fetch_json, "exact_pool_refresh", category, lens, url
    )
    matches = [
        row
        for row in rows
        if isinstance(row, dict)
        and _shared.text(
            _shared.read(row, "address", "id", "pubkey", "poolAddress", "pool_address")
        )
        == candidate["pool_address"]
    ]
    if len(rows) != 1 or len(matches) != 1:
        raise ValueError(
            "trusted Orca refresh did not return exactly the selected pool"
        )
    normalized, rejection = _pool_policy.normalize_record(matches[0], category, lens, 0)
    if rejection or normalized is None:
        raise ValueError(f"refreshed Orca pool is invalid: {rejection}")
    refreshed, rejection = _pool_policy.evaluate_pool(
        normalized, risk_profile, _now(), candidate["total_amount_quote"]
    )
    if rejection or refreshed is None:
        raise ValueError(f"refreshed Orca pool is ineligible: {rejection}")
    option = next(
        (item for item in refreshed["range_options"] if item["name"] == option_name),
        None,
    )
    if option is None:
        raise ValueError("chosen range option is no longer feasible")
    return refreshed, option


def _validate_width(
    original: dict[str, Any], refreshed: dict[str, Any], risk_profile: str
) -> Decimal:
    width = _shared.decimal(
        original.get("provisional_half_width"), "provisional half-width", positive=True
    )
    required = _shared.decimal(
        refreshed.get("uncapped_required_half_width"),
        "refreshed required half-width",
        positive=True,
    )
    capped_exception = (
        risk_profile == "yield_no_limit"
        and original.get("name") == "extreme"
        and refreshed.get("width_capped") is True
        and width == Decimal("0.95")
    )
    if width < required and not capped_exception:
        raise ValueError(
            "original provisional width no longer covers refreshed requirement"
        )
    return width


def _bounds(price: Decimal, width: Decimal) -> dict[str, Decimal]:
    lower = price * (Decimal(1) - width)
    upper = price * (Decimal(1) + width)
    buffer = min(Decimal("0.015"), max(Decimal("0.003"), width * Decimal("0.5")))
    result = {
        "lower_price": lower,
        "upper_price": upper,
        "lower_limit_price": lower * (Decimal(1) - buffer),
        "upper_limit_price": upper * (Decimal(1) + buffer),
    }
    if not (
        result["lower_limit_price"]
        < result["lower_price"]
        < price
        < result["upper_price"]
        < result["upper_limit_price"]
    ):
        raise ValueError("derived LP bounds are invalid")
    return result


def _floor(value: Decimal, decimals: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN)


def _inventory(
    amount_quote: Decimal,
    price: Decimal,
    bounds: dict[str, Decimal],
    base_decimals: int,
    quote_decimals: int,
) -> tuple[Decimal, Decimal]:
    root_price = price.sqrt()
    root_lower = bounds["lower_price"].sqrt()
    root_upper = bounds["upper_price"].sqrt()
    base_per_liquidity = (root_upper - root_price) / (root_price * root_upper)
    quote_per_liquidity = root_price - root_lower
    value_per_liquidity = base_per_liquidity * price + quote_per_liquidity
    if min(base_per_liquidity, quote_per_liquidity, value_per_liquidity) <= 0:
        raise ValueError("CLMM inventory calculation is not double-sided")
    liquidity = amount_quote / value_per_liquidity
    base = _floor(liquidity * base_per_liquidity, base_decimals)
    quote = _floor(liquidity * quote_per_liquidity, quote_decimals)
    if base <= 0 or quote <= 0:
        raise ValueError("rounded CLMM inventory is not double-sided")
    return base, quote


def _sol_balance(balances: list[dict[str, Any]]) -> Decimal:
    matches = [
        item for item in balances if _shared.text(item.get("symbol")).upper() == "SOL"
    ]
    if len(matches) != 1:
        raise ValueError("scoped SOL balance is not unique")
    return _shared.decimal(matches[0].get("available"), "SOL balance")


def _validate_inventory(
    balances: list[dict[str, Any]],
    candidate: dict[str, Any],
    required_base: Decimal,
    required_quote: Decimal,
    min_sol_reserve: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    base = candidate["token_a"]
    quote = candidate["token_b"]
    available_base = _shared.balance_for_token(balances, base["mint"], base["symbol"])
    available_quote = _shared.balance_for_token(
        balances, quote["mint"], quote["symbol"]
    )
    available_sol = _sol_balance(balances)
    if available_sol < min_sol_reserve:
        raise ValueError("current SOL balance is below configured reserve")
    if available_quote < required_quote:
        raise ValueError("quote inventory is insufficient")
    spendable_base = available_base
    if base["symbol"].upper() == "SOL":
        spendable_base = max(Decimal(0), available_base - min_sol_reserve)
    return spendable_base, available_quote, available_sol


def _pool_checks(
    candidate: dict[str, Any], refreshed: dict[str, Any], gateway: dict[str, Any]
) -> None:
    if candidate.get("price_orientation") != "token_b_per_token_a":
        raise ValueError("candidate price orientation is invalid")
    if (
        gateway["pool_address"] != candidate["pool_address"]
        or gateway["base_mint"] != candidate["token_a"]["mint"]
        or gateway["quote_mint"] != candidate["token_b"]["mint"]
    ):
        raise ValueError("Gateway pool identity or orientation conflicts with Orca")
    orca_price = _shared.decimal(
        refreshed.get("scanner_price"), "refreshed Orca price", positive=True
    )
    deviation = abs(gateway["current_price"] - orca_price) / orca_price
    if deviation > MAX_PRICE_DEVIATION:
        raise ValueError("Orca/Gateway price deviation exceeds 1%")


def _schema_supports_total(schema: Any) -> bool:
    if not isinstance(schema, dict):
        raise ValueError("LP executor schema response is invalid")
    if isinstance(schema.get("result"), dict):
        schema = schema["result"]
    if "fields" in schema:
        fields = schema["fields"]
        if not isinstance(fields, list):
            raise ValueError("LP executor schema fields must be a list")
        names = []
        for field in fields:
            if not isinstance(field, dict):
                raise ValueError("LP executor schema field must be an object")
            name = field.get("name")
            if not isinstance(name, str) or not name.strip() or name != name.strip():
                raise ValueError("LP executor schema field name is invalid")
            names.append(name)
        if len(names) != len(set(names)):
            raise ValueError("LP executor schema contains duplicate field names")
        return "total_amount_quote" in names
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        nested = schema.get("schema")
        properties = nested.get("properties") if isinstance(nested, dict) else None
    if not isinstance(properties, dict):
        raise ValueError("LP executor schema has no properties")
    return "total_amount_quote" in properties


def _executor_config(
    candidate: dict[str, Any],
    bounds: dict[str, Decimal],
    base_amount: Decimal,
    quote_amount: Decimal,
    amount_quote: Decimal,
    include_total: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "lp_executor",
        "connector_name": _shared.NETWORK,
        "lp_provider": LP_PROVIDER,
        "trading_pair": candidate["trading_pair"],
        "pool_address": candidate["pool_address"],
        **bounds,
        "side": 3,
        "base_amount": base_amount,
        "quote_amount": quote_amount,
        "keep_position": True,
    }
    if include_total:
        result["total_amount_quote"] = amount_quote
    return result


def _mapped_ownership(
    state: dict[str, Any], executors: list[dict[str, Any]]
) -> tuple[set[str], dict[str, set[str]]]:
    persisted = [
        slot["position"]
        for slot in state["slots"].values()
        if slot["position"] is not None
    ]
    persisted_by_id = {position["executor_id"]: position for position in persisted}
    if len(persisted_by_id) != len(persisted):
        raise ValueError("persisted executor ownership is duplicated")
    observed_by_id: dict[str, list[dict[str, Any]]] = {}
    for executor in executors:
        observed_by_id.setdefault(executor["executor_id"], []).append(executor)
    if any(len(items) != 1 for items in observed_by_id.values()):
        raise ValueError("executor ownership response contains conflicting identities")
    current_by_id = {
        executor_id: items[0]
        for executor_id, items in observed_by_id.items()
        if items[0]["is_active"] or executor_id in persisted_by_id
    }
    if set(current_by_id) != set(persisted_by_id):
        raise ValueError("unknown or missing controller executor blocks open")
    positions: dict[str, set[str]] = {}
    for executor_id, position in persisted_by_id.items():
        executor = current_by_id[executor_id]
        if (
            executor["controller_id"] != position["controller_id"]
            or executor["pool_address"] != position["pool_address"]
            or executor["position_address"] != position["position_address"]
        ):
            raise ValueError("executor conflicts with persisted ownership")
        positions.setdefault(position["pool_address"], set()).add(
            position["position_address"]
        )
    return set(observed_by_id), positions


async def _ownership_preflight(
    client: Any,
    session: _shared.Session,
    strategy_config: _shared.Config,
    wallet: str,
    selected_pool: str,
) -> tuple[set[str], set[str]]:
    with _shared.wallet_lock(session):
        with _shared.state_lock(session):
            state = _locked_state(session, strategy_config)
            state_snapshot = deepcopy(state)
    executors = await _shared.search_controller_executors(client, session.controller_id)
    baseline_ids, tracked_positions = _mapped_ownership(state_snapshot, executors)
    pools = sorted(set(tracked_positions) | {selected_pool})
    owned_results = await asyncio.gather(
        *(_shared.get_owned_positions(client, pool, wallet) for pool in pools)
    )
    selected_baseline: set[str] = set()
    for pool, owned in zip(pools, owned_results, strict=True):
        addresses = _shared.owned_position_addresses(owned)
        expected = tracked_positions.get(pool, set())
        if not expected <= addresses:
            raise ValueError("missing or conflicting session position blocks open")
        if pool == selected_pool:
            selected_baseline = addresses
    return baseline_ids, selected_baseline


def _quote_result(
    value: Any,
    pair: str,
    side: str,
    amount: Decimal,
    gateway_price: Decimal,
    slippage_pct: Decimal,
) -> dict[str, Decimal]:
    if not isinstance(value, dict):
        raise ValueError("Jupiter quote must be an object")
    result = value.get("result", value)
    if not isinstance(result, dict):
        raise ValueError("Jupiter quote result must be an object")
    observed_pair = _shared.text(result.get("trading_pair"))
    observed_side = _shared.text(result.get("side")).upper()
    if observed_pair and observed_pair != pair:
        raise ValueError("Jupiter quote trading pair mismatch")
    if observed_side and observed_side != side:
        raise ValueError("Jupiter quote side mismatch")
    input_amount = _shared.decimal(
        _shared.read(result, "input_amount", "amount_in"),
        "quoted input amount",
        positive=True,
    )
    output_amount = _shared.decimal(
        _shared.read(result, "output_amount", "amount_out"),
        "quoted output amount",
        positive=True,
    )
    allowed = slippage_pct / Decimal(100)
    if side == "BUY":
        if output_amount < amount or input_amount > amount * gateway_price * (
            1 + allowed
        ):
            raise ValueError("Jupiter BUY quote exceeds configured slippage")
    elif input_amount < amount or output_amount < amount * gateway_price * (
        1 - allowed
    ):
        raise ValueError("Jupiter SELL quote exceeds configured slippage")
    return {"input_amount": input_amount, "output_amount": output_amount}


async def _confirmed_swap(
    client: Any,
    response: Any,
    request: dict[str, Any],
    persist_external_id: Callable[[str], None],
) -> dict[str, Any]:
    evidence = response
    transaction = _shared.normalize_transaction(response)
    if transaction["status"] not in {"CONFIRMED", "SUCCESS", "COMPLETED"}:
        persist_external_id(transaction["transaction_hash"])
        evidence = await client.gateway_swap.get_swap_status(
            transaction["transaction_hash"]
        )
        transaction = _shared.normalize_transaction(
            evidence, transaction["transaction_hash"]
        )
    if transaction["status"] not in {"CONFIRMED", "SUCCESS", "COMPLETED"}:
        raise ValueError("Jupiter transaction is not confirmed")
    for key in ("connector", "network", "wallet_address", "trading_pair", "side"):
        if transaction[key] != request[key]:
            raise ValueError(f"confirmed Jupiter {key} attribution mismatch")
    if transaction["slippage_pct"] != _shared.decimal(
        request["slippage_pct"], "requested Jupiter slippage"
    ):
        raise ValueError("confirmed Jupiter slippage attribution mismatch")
    base_proof = (transaction["base_mint"], transaction["base_token"])
    quote_proof = (transaction["quote_mint"], transaction["quote_token"])
    if (
        not any(base_proof)
        or any(
            value is not None and value != expected
            for value, expected in zip(
                base_proof,
                (request["base_mint"], request["base_symbol"]),
                strict=True,
            )
        )
        or not any(quote_proof)
        or any(
            value is not None and value != expected
            for value, expected in zip(
                quote_proof,
                (request["quote_mint"], request["quote_symbol"]),
                strict=True,
            )
        )
        or transaction["input_amount"] is None
        or transaction["output_amount"] is None
        or transaction["input_amount"] <= 0
        or transaction["output_amount"] <= 0
    ):
        raise ValueError(
            "confirmed Jupiter transaction lacks exact token/pair/side attribution"
        )
    attributed = (
        transaction["output_amount"]
        if request["side"] == "BUY"
        else transaction["input_amount"]
    )
    requested_amount = _shared.decimal(request["amount"], "swap request amount")
    quantum = Decimal(1).scaleb(-request["base_token_decimals"])
    if request["side"] == "SELL" and abs(attributed - requested_amount) > quantum:
        raise ValueError("confirmed Jupiter SELL amount differs from exact request")
    return transaction


def _ack_executor_id(value: Any) -> tuple[str | None, bool]:
    if not isinstance(value, dict):
        return None, False
    result = value.get("result") if isinstance(value.get("result"), dict) else {}
    ids = {
        _shared.text(item)
        for item in (
            value.get("executor_id"),
            value.get("id"),
            result.get("executor_id"),
            result.get("id"),
        )
        if _shared.text(item)
    }
    statuses = {
        _shared.text(item).upper()
        for item in (value.get("status"), result.get("status"))
        if _shared.text(item)
    }
    failed = bool(statuses & {"ERROR", "FAILED", "REJECTED"})
    return (next(iter(ids)) if len(ids) == 1 else None), failed


async def _reconcile_create(
    client: Any,
    session: _shared.Session,
    wallet: str,
    pool_address: str,
    baseline_executor_ids: set[str],
    baseline_position_ids: set[str],
    expected: dict[str, Any],
    acknowledged_id: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    executors, owned = await asyncio.gather(
        _shared.search_controller_executors(client, session.controller_id),
        _shared.get_owned_positions(client, pool_address, wallet),
    )
    all_new_executors = [
        executor
        for executor in executors
        if executor["executor_id"] not in baseline_executor_ids
    ]
    new_executors = [
        executor
        for executor in all_new_executors
        if not _shared.executor_plan_mismatches(executor, expected)
    ]
    new_position_ids = {
        position["position_address"]
        for position in owned
        if position["position_address"] not in baseline_position_ids
    }
    if acknowledged_id is not None:
        new_executors = [
            executor
            for executor in new_executors
            if executor["executor_id"] == acknowledged_id
        ]
    if len(all_new_executors) == 1 and len(new_executors) == 1:
        executor = new_executors[0]
        if executor["position_address"] in new_position_ids:
            return executor, {
                "executor_ids_unchanged": False,
                "position_ids_unchanged": False,
            }
    observed_executor_ids = {executor["executor_id"] for executor in executors}
    observed_position_ids = {position["position_address"] for position in owned}
    proof = {
        "baseline_executor_ids": sorted(baseline_executor_ids),
        "observed_executor_ids": sorted(observed_executor_ids),
        "executor_ids_unchanged": observed_executor_ids == baseline_executor_ids,
        "baseline_position_ids": sorted(baseline_position_ids),
        "observed_position_ids": sorted(observed_position_ids),
        "position_ids_unchanged": observed_position_ids == baseline_position_ids,
        "observed_at": _now(),
    }
    return None, proof


def _definitive_create_error(error: BaseException | None) -> bool:
    if error is None:
        return False
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is None:
        status_code = getattr(error, "status_code", None)
    if status_code is None:
        status_code = getattr(error, "status", None)
    return status_code in {400, 401, 403, 404, 422}


async def _live_preflight(
    client: Any,
    strategy_config: _shared.Config,
    candidate: dict[str, Any],
    original_option: dict[str, Any],
    option_name: str,
    amount_quote: Decimal,
) -> dict[str, Any]:
    validated, _, rejection = _pool_policy.revalidate_candidate(
        candidate,
        strategy_config.risk_profile,
        float(strategy_config.total_amount_quote),
        option_name,
    )
    if rejection or validated is None:
        raise ValueError(f"candidate revalidation failed: {rejection}")
    refreshed, refreshed_option = await _refresh_orca(
        validated, strategy_config.risk_profile, option_name
    )
    width = _validate_width(
        original_option, refreshed_option, strategy_config.risk_profile
    )
    gateway = await _shared.get_gateway_pool(client, validated["pool_address"])
    _pool_checks(validated, refreshed, gateway)
    await _shared.read_token_metadata(
        client,
        [
            {
                "mint": validated["token_a"]["mint"],
                "symbol": validated["token_a"]["symbol"],
                "decimals": validated["token_a"]["decimals"],
            },
            {
                "mint": validated["token_b"]["mint"],
                "symbol": validated["token_b"]["symbol"],
                "decimals": validated["token_b"]["decimals"],
            },
        ],
    )
    bounds = _bounds(gateway["current_price"], width)
    required_base, required_quote = _inventory(
        amount_quote,
        gateway["current_price"],
        bounds,
        validated["token_a"]["decimals"],
        validated["token_b"]["decimals"],
    )
    balances = await _shared.read_balances(client, strategy_config.account_name)
    base_balance = _shared.balance_for_token(
        balances,
        validated["token_a"]["mint"],
        validated["token_a"]["symbol"],
    )
    available_base, available_quote, available_sol = _validate_inventory(
        balances,
        validated,
        required_base,
        required_quote,
        strategy_config.min_sol_reserve,
    )
    return {
        "candidate": validated,
        "refreshed": refreshed,
        "gateway": gateway,
        "bounds": bounds,
        "required_base": required_base,
        "required_quote": required_quote,
        "base_balance": base_balance,
        "available_base": available_base,
        "available_quote": available_quote,
        "available_sol": available_sol,
    }


async def _restore(
    client: Any,
    session: _shared.Session,
    strategy_config: _shared.Config,
    slot_id: str,
    wallet: str,
    operation_id: str,
    request: dict[str, Any],
    confirmed: dict[str, Any],
    reason: str,
    predecessor: dict[str, Any],
) -> dict[str, Any]:
    rebalance = confirmed.get("rebalance")
    if not isinstance(rebalance, dict):
        raise ValueError("restoration requires confirmed rebalance attribution")
    amount = _shared.decimal(
        rebalance.get("output_amount"),
        "attributed rebalance base output",
        positive=True,
    )
    pair = request["trading_pair"]
    restore_request = {
        **request,
        "restore_base_amount": amount,
        "restore_reason": reason,
    }
    swap_request = {
        "connector": JUPITER,
        "network": _shared.NETWORK,
        "wallet_address": wallet,
        "trading_pair": pair,
        "side": "SELL",
        "amount": amount,
        "slippage_pct": strategy_config.max_slippage_pct,
        "base_mint": request["base_mint"],
        "base_symbol": request["base_symbol"],
        "quote_mint": request["quote_mint"],
        "quote_symbol": request["quote_symbol"],
        "base_token_decimals": request["base_token_decimals"],
        "quote_token_decimals": request["quote_token_decimals"],
    }
    restore_request["restore_swap"] = swap_request
    intent = _pending(operation_id, "restore", "intent", restore_request, confirmed)
    _replace_pending_exact(session, strategy_config, slot_id, predecessor, intent)
    gateway = await _shared.get_gateway_pool(client, request["pool_address"])
    balances = await _shared.read_balances(client, strategy_config.account_name)
    available = _shared.balance_for_token(
        balances, request["base_mint"], request["base_symbol"]
    )
    if available < amount:
        raise ValueError("attributed rebalance output is not available for restoration")
    quote = await client.gateway_swap.get_swap_quote(
        connector=JUPITER,
        network=_shared.NETWORK,
        trading_pair=pair,
        side="SELL",
        amount=amount,
        slippage_pct=strategy_config.max_slippage_pct,
    )
    _quote_result(
        quote,
        pair,
        "SELL",
        amount,
        gateway["current_price"],
        strategy_config.max_slippage_pct,
    )
    submitted = _pending(
        operation_id,
        "restore",
        "submitted",
        restore_request,
        confirmed,
        attempted_at=_now(),
    )
    _replace_pending_exact(session, strategy_config, slot_id, intent, submitted)
    swap_predecessor = submitted

    def persist_external_id(transaction_hash: str) -> None:
        nonlocal swap_predecessor
        updated = {**swap_predecessor, "external_id": transaction_hash}
        _replace_pending_exact(
            session,
            strategy_config,
            slot_id,
            swap_predecessor,
            updated,
        )
        swap_predecessor = updated

    try:
        response = await client.gateway_swap.execute_swap(
            connector=JUPITER,
            network=_shared.NETWORK,
            trading_pair=pair,
            side="SELL",
            amount=amount,
            slippage_pct=strategy_config.max_slippage_pct,
            wallet_address=wallet,
        )
        transaction = await _confirmed_swap(
            client, response, swap_request, persist_external_id
        )
    except BaseException:
        uncertain = {**swap_predecessor, "status": "uncertain"}
        _replace_pending_exact(
            session,
            strategy_config,
            slot_id,
            swap_predecessor,
            uncertain,
        )
        raise
    confirmed = {**confirmed, "restore": _shared.json_value(transaction, redact=False)}
    restored = {
        **swap_predecessor,
        "confirmed": confirmed,
        "external_id": transaction["transaction_hash"],
    }
    _replace_pending_exact(
        session,
        strategy_config,
        slot_id,
        swap_predecessor,
        restored,
    )
    _clear_restored(session, strategy_config, slot_id, restored)
    return transaction


async def _execute(config: Config, context: Any, debug: dict[str, Any]) -> str:
    observed_at = _now()
    session: _shared.Session | None = None
    operation_id: str | None = None
    claimed = False
    try:
        debug["stage"] = "resolve_and_validate"
        session = _shared.resolve_session(config.controller_id)
        _shared.require_live(session)
        strategy_config = _shared.load_config(session)
        candidate, original_option, rejection = _pool_policy.revalidate_candidate(
            config.candidate,
            strategy_config.risk_profile,
            float(strategy_config.total_amount_quote),
            config.range_option,
        )
        if rejection or candidate is None or original_option is None:
            raise ValueError(f"candidate revalidation failed: {rejection}")
        if not (
            strategy_config.min_capital_per_slot_quote
            <= config.amount_quote
            <= strategy_config.max_capital_per_slot_quote
        ):
            raise ValueError("amount_quote is outside configured per-slot bounds")

        debug["stage"] = "validate_local_ownership"
        # Lazy state creation and all local ownership checks happen under both locks.
        with _shared.wallet_lock(session):
            with _shared.state_lock(session):
                state = _locked_state(session, strategy_config)
                _assert_local_open(
                    state,
                    session,
                    strategy_config,
                    config.slot_id,
                    candidate["pool_address"],
                    config.amount_quote,
                )
                binding_snapshot = deepcopy(state["binding"])

        debug["stage"] = "bind_server_and_wallet"
        bound = await _shared.bound_client(
            context,
            (
                binding_snapshot["server_name"]
                if binding_snapshot
                else strategy_config.server_name
            ),
        )
        wallet = await _shared.default_solana_wallet(bound.client)
        expected_binding = _binding(session, strategy_config, bound.server_name, wallet)
        if binding_snapshot is not None and binding_snapshot != expected_binding:
            raise ValueError("current server or default wallet conflicts with binding")
        debug["stage"] = "ownership_preflight"
        baseline_executor_ids, baseline_position_ids = await _ownership_preflight(
            bound.client,
            session,
            strategy_config,
            wallet,
            candidate["pool_address"],
        )
        debug["stage"] = "live_preflight"
        live = await _live_preflight(
            bound.client,
            strategy_config,
            candidate,
            original_option,
            config.range_option,
            config.amount_quote,
        )
        schema = await bound.client.executors.get_executor_config_schema("lp_executor")
        include_total = _schema_supports_total(schema)
        plan = _executor_config(
            candidate,
            live["bounds"],
            live["required_base"],
            live["required_quote"],
            config.amount_quote,
            include_total,
        )
        request = {
            "controller_id": session.controller_id,
            "amount_quote": config.amount_quote,
            "pool_address": candidate["pool_address"],
            "trading_pair": candidate["trading_pair"],
            "base_mint": candidate["token_a"]["mint"],
            "base_symbol": candidate["token_a"]["symbol"],
            "quote_mint": candidate["token_b"]["mint"],
            "quote_symbol": candidate["token_b"]["symbol"],
            "candidate": deepcopy(candidate),
            "range_option": config.range_option,
            "provisional_half_width": original_option["provisional_half_width"],
            "baseline_executor_ids": sorted(baseline_executor_ids),
            "baseline_position_ids": sorted(baseline_position_ids),
            "pre_rebalance_base_available": live["base_balance"],
            "pre_rebalance_spendable_base": live["available_base"],
            "base_token_decimals": candidate["token_a"]["decimals"],
            "quote_token_decimals": candidate["token_b"]["decimals"],
            "executor_config": plan,
        }
        shortfall = max(Decimal(0), live["required_base"] - live["available_base"])
        confirmed: dict[str, Any] = {}
        operation_id = uuid4().hex
        debug["operation_id"] = operation_id

        if shortfall > 0:
            debug["stage"] = "rebalance"
            pair = candidate["trading_pair"]
            swap_request = {
                "connector": JUPITER,
                "network": _shared.NETWORK,
                "wallet_address": wallet,
                "trading_pair": pair,
                "side": "BUY",
                "amount": shortfall,
                "slippage_pct": strategy_config.max_slippage_pct,
                "base_mint": candidate["token_a"]["mint"],
                "base_symbol": candidate["token_a"]["symbol"],
                "quote_mint": candidate["token_b"]["mint"],
                "quote_symbol": candidate["token_b"]["symbol"],
                "base_token_decimals": candidate["token_a"]["decimals"],
                "quote_token_decimals": candidate["token_b"]["decimals"],
            }
            request["rebalance_base_shortfall"] = shortfall
            request["rebalance_swap"] = swap_request
            quote = await bound.client.gateway_swap.get_swap_quote(
                connector=JUPITER,
                network=_shared.NETWORK,
                trading_pair=pair,
                side="BUY",
                amount=shortfall,
                slippage_pct=strategy_config.max_slippage_pct,
            )
            quote_evidence = _quote_result(
                quote,
                pair,
                "BUY",
                shortfall,
                live["gateway"]["current_price"],
                strategy_config.max_slippage_pct,
            )
            if (
                live["available_quote"]
                < live["required_quote"] + quote_evidence["input_amount"]
            ):
                raise ValueError("quote inventory cannot fund rebalance and LP deposit")
            rebalance_request = deepcopy(request)
            intent = _pending(
                operation_id, "rebalance", "intent", rebalance_request, {}
            )
            _claim(
                session,
                strategy_config,
                config.slot_id,
                candidate["pool_address"],
                config.amount_quote,
                expected_binding,
                intent,
            )
            claimed = True
            debug["mutation_claimed"] = True
            submitted = _pending(
                operation_id,
                "rebalance",
                "submitted",
                rebalance_request,
                {},
                attempted_at=_now(),
            )
            _replace_pending_exact(
                session, strategy_config, config.slot_id, intent, submitted
            )
            swap_predecessor = submitted

            def persist_external_id(transaction_hash: str) -> None:
                nonlocal swap_predecessor
                updated = {**swap_predecessor, "external_id": transaction_hash}
                _replace_pending_exact(
                    session,
                    strategy_config,
                    config.slot_id,
                    swap_predecessor,
                    updated,
                )
                swap_predecessor = updated

            try:
                response = await bound.client.gateway_swap.execute_swap(
                    connector=JUPITER,
                    network=_shared.NETWORK,
                    trading_pair=pair,
                    side="BUY",
                    amount=shortfall,
                    slippage_pct=strategy_config.max_slippage_pct,
                    wallet_address=wallet,
                )
                transaction = await _confirmed_swap(
                    bound.client,
                    response,
                    swap_request,
                    persist_external_id,
                )
            except BaseException:
                uncertain = {**swap_predecessor, "status": "uncertain"}
                _replace_pending_exact(
                    session,
                    strategy_config,
                    config.slot_id,
                    swap_predecessor,
                    uncertain,
                )
                raise
            confirmed = {
                "rebalance": _shared.json_value(transaction, redact=False),
                "rebalance_quote": _shared.json_value(quote_evidence, redact=False),
            }
            rebalance_confirmed = {
                **swap_predecessor,
                "external_id": transaction["transaction_hash"],
                "confirmed": confirmed,
            }
            _replace_pending_exact(
                session,
                strategy_config,
                config.slot_id,
                swap_predecessor,
                rebalance_confirmed,
            )
            rebalance_predecessor = rebalance_confirmed
            try:
                live = await _live_preflight(
                    bound.client,
                    strategy_config,
                    candidate,
                    original_option,
                    config.range_option,
                    config.amount_quote,
                )
                pre_base = _shared.decimal(
                    request["pre_rebalance_base_available"],
                    "pre-rebalance base availability",
                )
                pre_spendable = _shared.decimal(
                    request["pre_rebalance_spendable_base"],
                    "pre-rebalance spendable base",
                )
                current_base = _shared.decimal(
                    live["base_balance"], "post-rebalance base availability"
                )
                output = _shared.decimal(
                    transaction["output_amount"],
                    "confirmed rebalance output",
                    positive=True,
                )
                quantum = Decimal(1).scaleb(-request["base_token_decimals"])
                fresh_shortfall = max(Decimal(0), live["required_base"] - pre_spendable)
                accounting = {
                    "initial_shortfall": shortfall,
                    "fresh_shortfall": fresh_shortfall,
                    "confirmed_output": output,
                    "base_quantum": quantum,
                    "pre_base_available": pre_base,
                    "post_base_available": current_base,
                    "observed_base_delta": current_base - pre_base,
                }
                confirmed = {**confirmed, "rebalance_accounting": accounting}
                accounted = {**rebalance_predecessor, "confirmed": confirmed}
                _replace_pending_exact(
                    session,
                    strategy_config,
                    config.slot_id,
                    rebalance_predecessor,
                    accounted,
                )
                rebalance_predecessor = accounted
                if fresh_shortfall < shortfall:
                    raise ValueError(
                        "fresh LP bounds reduced the attributable base shortfall"
                    )
                if abs(output - fresh_shortfall) > quantum:
                    raise ValueError(
                        "confirmed rebalance output does not match fresh base shortfall"
                    )
                if abs((current_base - pre_base) - output) > quantum:
                    raise ValueError(
                        "current base balance does not prove confirmed rebalance output"
                    )
                if live["available_base"] < live["required_base"]:
                    raise ValueError(
                        "confirmed rebalance is not visible in scoped inventory"
                    )
                if (
                    candidate["token_a"]["symbol"].upper() == "SOL"
                    and live["available_sol"] - live["required_base"]
                    < strategy_config.min_sol_reserve
                ):
                    raise ValueError(
                        "post-rebalance SOL inventory cannot retain configured reserve"
                    )
                plan = _executor_config(
                    candidate,
                    live["bounds"],
                    live["required_base"],
                    live["required_quote"],
                    config.amount_quote,
                    include_total,
                )
                request["executor_config"] = plan
                baseline_executor_ids, baseline_position_ids = (
                    await _ownership_preflight(
                        bound.client,
                        session,
                        strategy_config,
                        wallet,
                        candidate["pool_address"],
                    )
                )
                request["baseline_executor_ids"] = sorted(baseline_executor_ids)
                request["baseline_position_ids"] = sorted(baseline_position_ids)
            except Exception as error:
                transaction = await _restore(
                    bound.client,
                    session,
                    strategy_config,
                    config.slot_id,
                    wallet,
                    operation_id,
                    request,
                    confirmed,
                    f"post_rebalance_preflight_failed: {type(error).__name__}: {error}",
                    rebalance_predecessor,
                )
                return _compact(
                    {
                        "status": "restored",
                        "controller_id": session.controller_id,
                        "slot_id": config.slot_id,
                        "operation_id": operation_id,
                        "reason": str(error),
                        "restoration": transaction,
                    }
                )
            create_intent = _pending(
                operation_id, "create", "intent", request, confirmed
            )
            _replace_pending_exact(
                session,
                strategy_config,
                config.slot_id,
                rebalance_predecessor,
                create_intent,
            )
        else:
            baseline_executor_ids, baseline_position_ids = await _ownership_preflight(
                bound.client,
                session,
                strategy_config,
                wallet,
                candidate["pool_address"],
            )
            request["baseline_executor_ids"] = sorted(baseline_executor_ids)
            request["baseline_position_ids"] = sorted(baseline_position_ids)
            create_intent = _pending(operation_id, "create", "intent", request, {})
            _claim(
                session,
                strategy_config,
                config.slot_id,
                candidate["pool_address"],
                config.amount_quote,
                expected_binding,
                create_intent,
            )
            claimed = True
            debug["mutation_claimed"] = True

        debug["stage"] = "create_executor"
        create_submitted = _pending(
            operation_id,
            "create",
            "submitted",
            request,
            confirmed,
            attempted_at=_now(),
        )
        _replace_pending_exact(
            session,
            strategy_config,
            config.slot_id,
            create_intent,
            create_submitted,
        )
        create_error: BaseException | None = None
        response: Any = None
        try:
            response = await bound.client.executors.create_executor(
                executor_config=_shared.json_value(plan, redact=False),
                account_name=strategy_config.account_name,
                controller_id=session.controller_id,
            )
        except asyncio.CancelledError:
            uncertain = {**create_submitted, "status": "uncertain"}
            _replace_pending_exact(
                session,
                strategy_config,
                config.slot_id,
                create_submitted,
                uncertain,
            )
            raise
        except BaseException as error:
            create_error = error
            debug.update(
                {
                    "submission_exception_type": type(error).__name__,
                    "submission_exception": str(error),
                    "submission_traceback": traceback.format_exc(),
                }
            )

        acknowledged_id, acknowledged_failure = _ack_executor_id(response)
        if (
            create_error is None
            and acknowledged_id is None
            and not acknowledged_failure
        ):
            uncertain = {**create_submitted, "status": "uncertain"}
            _replace_pending_exact(
                session,
                strategy_config,
                config.slot_id,
                create_submitted,
                uncertain,
            )
            raise ValueError(
                "executor create acknowledgement is malformed or ambiguous"
            )
        try:
            debug["stage"] = "reconcile_create"
            executor, proven_absent = await _reconcile_create(
                bound.client,
                session,
                wallet,
                candidate["pool_address"],
                baseline_executor_ids,
                baseline_position_ids,
                {
                    "controller_id": session.controller_id,
                    "executor_config": plan,
                },
                acknowledged_id,
            )
        except BaseException:
            uncertain = {
                **create_submitted,
                "status": "uncertain",
                "external_id": acknowledged_id,
            }
            _replace_pending_exact(
                session,
                strategy_config,
                config.slot_id,
                create_submitted,
                uncertain,
            )
            raise
        if executor is None:
            definitive = (
                acknowledged_failure or _definitive_create_error(create_error)
            ) and proven_absent["executor_ids_unchanged"]
            if not definitive:
                uncertain = {
                    **create_submitted,
                    "status": "uncertain",
                    "external_id": acknowledged_id,
                }
                _replace_pending_exact(
                    session,
                    strategy_config,
                    config.slot_id,
                    create_submitted,
                    uncertain,
                )
                if create_error is not None:
                    raise create_error
                raise ValueError("executor creation remains uncertain")
            failure_proof = {
                **proven_absent,
                "acknowledgement": _shared.json_value(response, redact=False),
                "submission_error": (
                    f"{type(create_error).__name__}: {create_error}"
                    if create_error is not None
                    else None
                ),
            }
            failed_pending = {
                **create_submitted,
                "external_id": acknowledged_id,
                "confirmed": {
                    **confirmed,
                    "create_failure": failure_proof,
                },
            }
            _replace_pending_exact(
                session,
                strategy_config,
                config.slot_id,
                create_submitted,
                failed_pending,
            )
            if confirmed.get("rebalance") is not None:
                transaction = await _restore(
                    bound.client,
                    session,
                    strategy_config,
                    config.slot_id,
                    wallet,
                    operation_id,
                    request,
                    failed_pending["confirmed"],
                    "executor_create_definitively_failed",
                    failed_pending,
                )
                return _compact(
                    {
                        "status": "restored",
                        "controller_id": session.controller_id,
                        "slot_id": config.slot_id,
                        "operation_id": operation_id,
                        "reason": "executor_create_definitively_failed",
                        "restoration": transaction,
                    }
                )
            _clear_failed_open(
                session,
                strategy_config,
                config.slot_id,
                failed_pending,
            )
            return _compact(
                {
                    "status": "failed",
                    "controller_id": session.controller_id,
                    "slot_id": config.slot_id,
                    "operation_id": operation_id,
                    "reason": "executor_create_definitively_failed",
                    "recover_required": False,
                    "failure_proof": failure_proof,
                }
            )

        position = {
            "controller_id": session.controller_id,
            "executor_id": executor["executor_id"],
            "position_address": executor["position_address"],
            "pool_address": candidate["pool_address"],
            "base_mint": candidate["token_a"]["mint"],
            "quote_mint": candidate["token_b"]["mint"],
            "lower_price": str(plan["lower_price"]),
            "upper_price": str(plan["upper_price"]),
            "lower_limit_price": str(plan["lower_limit_price"]),
            "upper_limit_price": str(plan["upper_limit_price"]),
            "amount_quote": str(config.amount_quote),
            "opened_at": _now(),
        }
        reconciled = {
            **create_submitted,
            "external_id": executor["executor_id"],
            "confirmed": {**confirmed, "create": position},
        }
        _replace_pending_exact(
            session,
            strategy_config,
            config.slot_id,
            create_submitted,
            reconciled,
        )
        _finish_position(session, strategy_config, config.slot_id, reconciled, position)
        return _compact(
            {
                "status": "opened",
                "controller_id": session.controller_id,
                "slot_id": config.slot_id,
                "operation_id": operation_id,
                "position": position,
                "rebalance": confirmed.get("rebalance"),
            }
        )
    except asyncio.CancelledError:
        raise
    except BaseException as error:
        debug.update(
            {
                "exception_type": type(error).__name__,
                "exception": str(error),
                "traceback": traceback.format_exc(),
            }
        )
        return _compact(
            {
                "status": "failed",
                "controller_id": config.controller_id,
                "slot_id": config.slot_id,
                "operation_id": operation_id,
                "observed_at": observed_at,
                "error": f"{type(error).__name__}: {error}",
                "recover_required": claimed,
            }
        )


async def run(config: Config, context: Any):
    started_at = _now()
    started_monotonic = time.monotonic()
    debug: dict[str, Any] = {}
    try:
        required = (
            "controller_id",
            "slot_id",
            "candidate",
            "range_option",
            "amount_quote",
        )
        missing = [name for name in required if getattr(config, name) is None]
        if missing:
            output = _reporting.sample_payload("open", config, missing)
        else:
            output = await _execute(config, context, debug)
    except asyncio.CancelledError:
        await _reporting.finish_cancelled(
            "open",
            config,
            started_at=started_at,
            started_monotonic=started_monotonic,
            debug=debug,
        )
        raise
    return await _reporting.finish(
        "open",
        config,
        output,
        started_at=started_at,
        started_monotonic=started_monotonic,
        debug=debug,
    )
