"""Register one selected token; never submit an executor or transfer funds."""

from __future__ import annotations

import asyncio
import copy
import json
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlparse

import aiohttp
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from agents.lp_expert.core import orca, reporting
from agents.lp_expert.core.planner import (
    apply_existing_base_inventory,
    build_candidate_plan,
)
from agents.lp_expert.core.portfolio import (
    NATIVE_SWAP_FAILED,
    NATIVE_SWAP_PENDING,
    NATIVE_SWAP_SUCCESS,
    exact_close_evidence,
    fetch_all_executors,
    normalize_executor,
)
from agents.lp_expert.core.receipts import (
    OPERATION_ID_PATTERN,
    OperationIdentity,
    ReceiptStore,
    controller_mutation_lock,
    read_prior_tick_stop,
)
from agents.lp_expert.core.runtime import (
    QUOTE_MINT,
    RuntimeScope,
    bind_wallet,
    decimal_config,
    get_hummingbot_client,
    integer_config,
    pool_tvl_policy,
    refresh_balances,
    resolve_runtime,
)

CATEGORY = "Non-Submitting LP Order Request"
VERSION = "8"
_TRANSPORT_MAX_CHARS = 1_900

_CONFIRMED = {"CONFIRMED", "SUCCESS", "COMPLETED"}
_FAILED = {"FAILED", "ERROR", "REVERTED", "DROPPED"}


class Config(BaseModel):
    """Prepare a non-submitting order request or reconcile its exact returned ID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    controller_id: StrictStr
    operation_id: StrictStr = Field(
        min_length=8,
        max_length=128,
        pattern=OPERATION_ID_PATTERN,
    )
    reason: (
        Literal[
            "inventory_preparation",
            "inventory_restoration",
            "post_close_residual_cleanup",
        ]
        | None
    ) = None
    candidate: dict[str, Any] | None = None
    amount_quote: Decimal | None = Field(default=None, gt=0)
    range_half_width_pct: Decimal | None = Field(default=None, gt=0)
    pool_address: StrictStr | None = None
    base_symbol: StrictStr | None = None
    base_mint: StrictStr | None = None
    base_decimals: StrictInt | None = Field(default=None, ge=0, le=18)
    amount: Decimal | None = Field(default=None, gt=0)
    max_quote_input: Decimal | None = Field(default=None, gt=0)
    attributed_base_amount: Decimal | None = Field(default=None, gt=0)
    attribution_operation_id: StrictStr | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=OPERATION_ID_PATTERN,
    )
    executor_id: StrictStr | None = None
    position_id: StrictStr | None = None
    closed_tick: StrictInt | None = Field(default=None, ge=1)
    swap_executor_id: StrictStr | None = None

    @model_validator(mode="after")
    def contract(self) -> "Config":
        strings = (self.controller_id, self.operation_id)
        if not all(value and value == value.strip() for value in strings):
            raise ValueError("swap identity fields must be exact and non-empty")
        if self.swap_executor_id is not None and (
            not self.swap_executor_id
            or self.swap_executor_id != self.swap_executor_id.strip()
        ):
            raise ValueError("swap executor identity must be exact and non-empty")
        numeric = (
            self.amount,
            self.amount_quote,
            self.range_half_width_pct,
            self.max_quote_input,
            self.attributed_base_amount,
        )
        if any(value is not None and not value.is_finite() for value in numeric):
            raise ValueError("swap numeric values must be finite")
        recovery_values = (
            self.candidate,
            self.amount_quote,
            self.range_half_width_pct,
            self.pool_address,
            self.base_symbol,
            self.base_mint,
            self.base_decimals,
            self.amount,
            self.max_quote_input,
            self.attributed_base_amount,
            self.attribution_operation_id,
            self.executor_id,
            self.position_id,
            self.closed_tick,
        )
        if self.reason is None:
            if self.swap_executor_id is None or any(
                value is not None for value in recovery_values
            ):
                raise ValueError(
                    "recovery requires only controller_id, operation_id, and "
                    "swap_executor_id"
                )
            return self
        if self.reason == "inventory_preparation":
            if (
                not isinstance(self.candidate, dict)
                or self.amount_quote is None
                or self.range_half_width_pct is None
                or self.pool_address is not None
                or self.base_symbol is not None
                or self.base_mint is not None
                or self.base_decimals is not None
                or self.amount is not None
                or self.max_quote_input is not None
                or self.attributed_base_amount is not None
                or self.attribution_operation_id is not None
                or self.executor_id is not None
                or self.position_id is not None
                or self.closed_tick is not None
            ):
                raise ValueError("inventory preparation selection is incomplete")
        elif self.reason == "inventory_restoration":
            if (
                self.candidate is not None
                or self.amount_quote is not None
                or self.range_half_width_pct is not None
                or self.max_quote_input is not None
                or self.attributed_base_amount != self.amount
                or not self.attribution_operation_id
                or self.executor_id is not None
                or self.position_id is not None
                or self.closed_tick is not None
            ):
                raise ValueError("inventory restoration attribution is incomplete")
        elif (
            self.candidate is not None
            or self.amount_quote is not None
            or self.range_half_width_pct is not None
            or self.max_quote_input is not None
            or self.attributed_base_amount != self.amount
            or self.attribution_operation_id is not None
            or not self.executor_id
            or not self.position_id
            or self.closed_tick is None
        ):
            raise ValueError("post-close cleanup attribution is incomplete")
        if self.reason != "inventory_preparation":
            identity = (self.pool_address, self.base_symbol, self.base_mint)
            if (
                not all(
                    isinstance(value, str) and value and value == value.strip()
                    for value in identity
                )
                or self.base_decimals is None
                or self.amount is None
            ):
                raise ValueError("swap token identity is incomplete")
            if self.base_mint == QUOTE_MINT or self.base_symbol.upper() == "USDC":
                raise ValueError("swap base must not be canonical USDC")
        return self


# Agent-local routines are loaded from file without module registration.
Config.model_rebuild(
    _types_namespace={
        "StrictStr": StrictStr,
        "StrictInt": StrictInt,
        "Literal": Literal,
        "Decimal": Decimal,
        "Any": Any,
    }
)


class SwapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    controller_id: str
    operation_id: str
    reason: str
    pool_address: str
    base_symbol: str
    base_mint: str
    base_decimals: int
    amount: Decimal
    slippage_pct: Decimal
    max_quote_input: Decimal | None
    attributed_base_amount: Decimal | None
    attribution_operation_id: str | None
    executor_id: str | None
    position_id: str | None
    closed_tick: int | None
    amount_quote: Decimal | None = None
    range_half_width_pct: Decimal | None = None
    plan: dict[str, Any] | None = None
    candidate: dict[str, Any] | None = None
    recorded_plan_digest: str | None = None

    @property
    def side(self) -> Literal["BUY", "SELL"]:
        return "BUY" if self.reason == "inventory_preparation" else "SELL"

    @property
    def trading_pair(self) -> str:
        return f"{self.base_symbol}-USDC"

    @property
    def plan_digest(self) -> str | None:
        if isinstance(self.plan, dict):
            return self.plan.get("plan_digest")
        return self.recorded_plan_digest


# Agent-local routines are loaded from file without module registration.
SwapRequest.model_rebuild(
    _types_namespace={
        "Decimal": Decimal,
        "Any": Any,
    }
)


def _candidate_capsule(candidate: dict[str, Any]) -> dict[str, Any]:
    if "base" in candidate:
        return copy.deepcopy(candidate)
    return orca.compact_candidate(candidate)


def _resolve_request(
    config: Config,
    scope: RuntimeScope,
    *,
    attributed_base_amount: Decimal | None = None,
) -> SwapRequest:
    """Derive technical swap values from one selected candidate and frozen policy."""

    if config.reason is None:
        raise ValueError("recovery request must be resolved from its operation receipt")
    slippage = decimal_config(scope.config, "max_slippage_pct", positive=True)
    if config.reason == "inventory_preparation":
        plan = build_candidate_plan(
            config.candidate,
            amount_quote=config.amount_quote,
            range_half_width_pct=config.range_half_width_pct,
            strategy_config=scope.config,
        )
        if attributed_base_amount is not None:
            plan = apply_existing_base_inventory(
                plan,
                attributed_base_amount,
            )
        identity = plan["identity"]
        inventory = plan["inventory"]
        amount = _decimal(inventory["base_shortfall"], "planned base shortfall")
        quote_cap = _decimal(
            inventory["max_usdc_for_preparation_swap"],
            "planned preparation quote cap",
        )
        if amount < 0 or quote_cap < 0:
            raise ValueError("planned preparation values must be non-negative")
        return SwapRequest(
            controller_id=config.controller_id,
            operation_id=config.operation_id,
            reason=config.reason,
            pool_address=identity["pool_address"],
            base_symbol=identity["base_symbol"],
            base_mint=identity["base_mint"],
            base_decimals=identity["base_decimals"],
            amount=amount,
            slippage_pct=slippage,
            max_quote_input=quote_cap,
            attributed_base_amount=(
                _decimal(
                    plan["inputs"]["attributed_base_amount"],
                    "authorized existing base inventory",
                )
                if attributed_base_amount is not None
                else None
            ),
            attribution_operation_id=None,
            executor_id=None,
            position_id=None,
            closed_tick=None,
            amount_quote=config.amount_quote,
            range_half_width_pct=config.range_half_width_pct,
            plan=plan,
            candidate=_candidate_capsule(config.candidate),
        )
    return SwapRequest(
        controller_id=config.controller_id,
        operation_id=config.operation_id,
        reason=config.reason,
        pool_address=str(config.pool_address),
        base_symbol=str(config.base_symbol),
        base_mint=str(config.base_mint),
        base_decimals=int(config.base_decimals),
        amount=config.amount,
        slippage_pct=slippage,
        max_quote_input=None,
        attributed_base_amount=config.attributed_base_amount,
        attribution_operation_id=config.attribution_operation_id,
        executor_id=config.executor_id,
        position_id=config.position_id,
        closed_tick=config.closed_tick,
    )


def _recorded_request(
    record: dict[str, Any],
    scope: RuntimeScope,
) -> SwapRequest:
    """Rebuild one request only from its current-session immutable receipt."""

    if record.get("operation_kind") != "swap":
        raise _ManualReview("operation receipt is not a swap")
    intent = record.get("intent")
    if not isinstance(intent, dict):
        raise _ManualReview("swap operation receipt lacks immutable intent")
    reason = intent.get("reason")
    if reason not in {
        "inventory_preparation",
        "inventory_restoration",
        "post_close_residual_cleanup",
    }:
        raise _ManualReview("swap operation receipt reason is invalid")
    try:
        request = SwapRequest(
            controller_id=scope.controller_id,
            operation_id=str(record["operation_id"]),
            reason=reason,
            pool_address=str(intent["pool_address"]),
            base_symbol=str(intent["base_symbol"]),
            base_mint=str(intent["base_mint"]),
            base_decimals=int(intent["base_decimals"]),
            amount=_decimal(intent["amount"], "recorded swap amount"),
            slippage_pct=_decimal(
                intent["slippage_pct"], "recorded swap slippage", positive=True
            ),
            max_quote_input=(
                _decimal(
                    intent["max_quote_input"],
                    "recorded preparation quote cap",
                )
                if intent.get("max_quote_input") is not None
                else None
            ),
            attributed_base_amount=(
                _decimal(
                    intent["attributed_base_amount"],
                    "recorded attributed base",
                    positive=True,
                )
                if intent.get("attributed_base_amount") is not None
                else None
            ),
            attribution_operation_id=intent.get("attribution_operation_id"),
            executor_id=intent.get("executor_id"),
            position_id=intent.get("position_id"),
            closed_tick=intent.get("closed_tick"),
            amount_quote=(
                _decimal(
                    intent["amount_quote"],
                    "recorded allocation quote",
                    positive=True,
                )
                if intent.get("amount_quote") is not None
                else None
            ),
            range_half_width_pct=(
                _decimal(
                    intent["range_half_width_pct"],
                    "recorded range width",
                    positive=True,
                )
                if intent.get("range_half_width_pct") is not None
                else None
            ),
            candidate=(
                intent.get("candidate")
                if isinstance(intent.get("candidate"), dict)
                else None
            ),
            recorded_plan_digest=(
                str(intent["plan_digest"])
                if intent.get("plan_digest") is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _ManualReview("swap operation receipt intent is incomplete") from exc
    if (
        request.controller_id != record.get("controller_id")
        or request.amount < 0
        or (request.reason != "inventory_preparation" and request.amount <= 0)
        or (request.max_quote_input is not None and request.max_quote_input < 0)
        or request.side != str(intent.get("side") or "").upper()
        or request.trading_pair != intent.get("trading_pair")
        or (
            request.reason == "inventory_preparation"
            and (
                request.max_quote_input is None
                or request.amount_quote is None
                or request.range_half_width_pct is None
            )
        )
        or (
            request.reason != "inventory_preparation"
            and request.attributed_base_amount != request.amount
        )
    ):
        raise _ManualReview("swap operation receipt intent is inconsistent")
    return request


def _deployment_input(config: SwapRequest) -> dict[str, Any] | None:
    if (
        config.reason != "inventory_preparation"
        or not isinstance(config.candidate, dict)
        or config.amount_quote is None
        or config.range_half_width_pct is None
    ):
        return None
    return {
        "candidate": config.candidate,
        "amount_quote": format(config.amount_quote, "f"),
        "range_half_width_pct": format(config.range_half_width_pct, "f"),
        "preparation_operation_id": config.operation_id,
    }


def _lp_create_allowed(record: dict[str, Any], current_tick: int) -> bool:
    result = record.get("result")
    admitted_allowed = (
        bool(result.get("same_tick_lp_create_allowed"))
        if isinstance(result, dict)
        else False
    )
    return admitted_allowed or int(record["tick"]) < current_tick


class _ManualReview(Exception):
    pass


def _decimal(value: Any, label: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{label} must be a decimal") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f"{label} must be finite and valid")
    return result


def _find(value: Any, *keys: str) -> Any:
    pending = [value]
    visited: set[int] = set()
    wanted = {key.casefold() for key in keys}
    while pending:
        current = pending.pop(0)
        if not isinstance(current, dict) or id(current) in visited:
            continue
        visited.add(id(current))
        for key, item in current.items():
            if str(key).casefold() in wanted and item not in (None, ""):
                return item
        pending.extend(item for item in current.values() if isinstance(item, dict))
    return None


def _token_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        rows = value.get("tokens", value.get("data"))
    else:
        rows = None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Gateway token registry response is invalid")
    return rows


def _token_address(value: dict[str, Any]) -> str:
    return str(
        value.get("address") or value.get("token_address") or value.get("mint") or ""
    ).strip()


def _registered_token_or_none(
    rows: list[dict[str, Any]], *, address: str, symbol: str, decimals: int
) -> dict[str, Any] | None:
    exact = [row for row in rows if _token_address(row) == address]
    if len(exact) > 1:
        raise ValueError(f"registered token identity for {symbol} is not unique")
    collisions = [
        item
        for item in rows
        if str(item.get("symbol") or "").strip().casefold() == symbol.casefold()
        and _token_address(item) != address
    ]
    if collisions:
        raise ValueError(f"registered token symbol {symbol} is ambiguous")
    if not exact:
        return None
    row = exact[0]
    found_symbol = str(row.get("symbol") or "").strip()
    found_decimals = row.get("decimals")
    if (
        found_symbol.casefold() != symbol.casefold()
        or isinstance(found_decimals, bool)
        or int(found_decimals) != decimals
    ):
        raise ValueError(f"registered token metadata for {symbol} conflicts")
    return {"address": address, "symbol": found_symbol, "decimals": decimals}


def _require_registered_token(
    rows: list[dict[str, Any]], *, address: str, symbol: str, decimals: int
) -> dict[str, Any]:
    result = _registered_token_or_none(
        rows,
        address=address,
        symbol=symbol,
        decimals=decimals,
    )
    if result is None:
        raise ValueError(f"registered token identity for {symbol} is unavailable")
    return result


def _balance(
    rows: list[dict[str, Any]],
    *,
    symbol: str,
    mint: str | None = None,
    missing_exact_is_zero: bool = False,
) -> Decimal:
    exact: list[Decimal] = []
    by_symbol: list[tuple[str, Decimal]] = []
    any_mint = False
    for row in rows:
        row_symbol = str(row.get("token") or row.get("symbol") or "").strip()
        row_mint = _token_address(row)
        any_mint |= bool(row_mint)
        amount = row.get(
            "available_units",
            row.get("available", row.get("available_balance", row.get("units"))),
        )
        if amount is None:
            continue
        parsed = _decimal(amount, f"{symbol} available balance")
        if row_symbol.casefold() == symbol.casefold():
            by_symbol.append((row_mint, parsed))
        if mint and row_mint == mint:
            exact.append(parsed)

    if mint:
        conflicting = [
            amount for row_mint, amount in by_symbol if row_mint and row_mint != mint
        ]
        unscoped = [amount for row_mint, amount in by_symbol if not row_mint]
        if exact and (conflicting or unscoped):
            raise ValueError(f"{symbol} balance is not uniquely scoped")
        if conflicting:
            raise ValueError(f"{symbol} balance conflicts with the selected mint")
        if exact:
            found = exact
        elif by_symbol and not any_mint:
            found = [amount for _, amount in by_symbol]
        elif by_symbol:
            raise ValueError(f"{symbol} balance is not uniquely scoped")
        elif missing_exact_is_zero:
            return Decimal(0)
        else:
            found = []
    else:
        found = [amount for _, amount in by_symbol]

    if len(found) != 1 or found[0] < 0:
        raise ValueError(f"{symbol} balance is not uniquely scoped")
    return found[0]


def _authorized_existing_base(
    *,
    plan: dict[str, Any],
    balances: list[dict[str, Any]],
    scope: RuntimeScope,
) -> tuple[Decimal, dict[str, Decimal]]:
    """Return the configured, reserve-aware wallet allocation for one plan."""

    identity = plan["identity"]
    required = _decimal(
        plan["inventory"]["base_amount"],
        "planned base amount",
        positive=True,
    )
    available = _balance(
        balances,
        symbol=identity["base_symbol"],
        mint=identity["base_mint"],
        missing_exact_is_zero=True,
    )
    reserve = decimal_config(scope.config, "min_sol_reserve", positive=True)
    if not scope.config["use_existing_base_inventory"]:
        usable = Decimal(0)
    elif identity["base_symbol"].upper() == "SOL":
        usable = max(Decimal(0), available - reserve)
    else:
        usable = available
    attributed = min(required, usable)
    return attributed, {
        "required_base": required,
        "available_base": available,
        "minimum_sol_reserve": reserve,
        "usable_existing_base": usable,
        "attributed_existing_base": attributed,
    }


def _balance_error(
    field: str,
    *,
    required: Decimal,
    available: Decimal,
) -> ValueError:
    return ValueError(
        f"insufficient {field}: required={format(required, 'f')}, "
        f"available={format(available, 'f')}"
    )


def _quote_values(value: Any) -> tuple[Decimal, Decimal]:
    amount_in = _decimal(
        _find(value, "input_amount", "amount_in", "amountIn"),
        "quote input amount",
        positive=True,
    )
    amount_out = _decimal(
        _find(value, "output_amount", "amount_out", "amountOut"),
        "quote output amount",
        positive=True,
    )
    return amount_in, amount_out


async def _quote(
    client: Any, scope: RuntimeScope, config: SwapRequest
) -> tuple[Any, Decimal, Decimal]:
    value = await client.gateway_swap.get_swap_quote(
        connector=scope.swap_connector,
        network=scope.network,
        trading_pair=config.trading_pair,
        side=config.side,
        amount=config.amount,
        slippage_pct=config.slippage_pct,
    )
    amount_in, amount_out = _quote_values(value)
    return value, amount_in, amount_out


async def _fetch_solana_transaction(
    node_url: str,
    transaction_hash: str,
    *,
    timeout: aiohttp.ClientTimeout | None,
) -> dict[str, Any] | None:
    """Fetch one finalized transaction without forwarding HAPI credentials."""

    request = {
        "jsonrpc": "2.0",
        "id": transaction_hash,
        "method": "getTransaction",
        "params": [
            transaction_hash,
            {
                "encoding": "jsonParsed",
                "commitment": "finalized",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    }
    session_options = {"timeout": timeout} if timeout is not None else {}
    async with aiohttp.ClientSession(**session_options) as session:
        async with session.post(
            node_url,
            json=request,
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise ValueError(
                    f"Solana RPC returned unexpected HTTP status {response.status}"
                )
            value = await response.json(content_type=None)
    if not isinstance(value, dict):
        raise ValueError("Solana RPC response is invalid")
    if value.get("error") is not None:
        raise ValueError("Solana RPC rejected the transaction lookup")
    result = value.get("result")
    if result is not None and not isinstance(result, dict):
        raise ValueError("Solana RPC transaction result is invalid")
    return result


def _account_key(value: Any) -> tuple[str, bool, bool]:
    if isinstance(value, str):
        return value, False, False
    if not isinstance(value, dict):
        raise ValueError("Solana transaction account key is invalid")
    address = str(value.get("pubkey") or "").strip()
    if not address:
        raise ValueError("Solana transaction account address is unavailable")
    return address, value.get("signer") is True, value.get("writable") is True


def _raw_token_balances(
    rows: Any,
    *,
    wallet_address: str,
    mint: str,
    decimals: int,
) -> dict[int, int]:
    if not isinstance(rows, list):
        raise ValueError("Solana transaction token balances are invalid")
    found: dict[int, int] = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("owner") != wallet_address
            or row.get("mint") != mint
        ):
            continue
        index = row.get("accountIndex")
        ui_amount = row.get("uiTokenAmount")
        if (
            isinstance(index, bool)
            or not isinstance(index, int)
            or not isinstance(ui_amount, dict)
            or isinstance(ui_amount.get("decimals"), bool)
            or ui_amount.get("decimals") != decimals
        ):
            raise ValueError("Solana transaction token balance identity conflicts")
        raw = ui_amount.get("amount")
        if not isinstance(raw, str) or not raw.isdigit() or index in found:
            raise ValueError("Solana transaction token balance is ambiguous")
        found[index] = int(raw)
    return found


def _token_delta(
    meta: dict[str, Any],
    *,
    wallet_address: str,
    mint: str,
    decimals: int,
) -> Decimal:
    pre = _raw_token_balances(
        meta.get("preTokenBalances"),
        wallet_address=wallet_address,
        mint=mint,
        decimals=decimals,
    )
    post = _raw_token_balances(
        meta.get("postTokenBalances"),
        wallet_address=wallet_address,
        mint=mint,
        decimals=decimals,
    )
    indexes = set(pre) | set(post)
    if not indexes:
        raise ValueError("wallet token balance delta is unavailable")
    raw_delta = sum(post.get(index, 0) - pre.get(index, 0) for index in indexes)
    return Decimal(raw_delta) / (Decimal(10) ** decimals)


def _solana_receipt_from_transaction(
    value: dict[str, Any],
    transaction_hash: str,
    config: SwapRequest,
    scope: RuntimeScope,
) -> dict[str, Any]:
    transaction = value.get("transaction")
    meta = value.get("meta")
    message = transaction.get("message") if isinstance(transaction, dict) else None
    signatures = (
        transaction.get("signatures") if isinstance(transaction, dict) else None
    )
    account_values = message.get("accountKeys") if isinstance(message, dict) else None
    if (
        not isinstance(meta, dict)
        or not isinstance(signatures, list)
        or not signatures
        or signatures[0] != transaction_hash
        or not isinstance(account_values, list)
        or not account_values
    ):
        raise ValueError("finalized Solana transaction evidence is incomplete")
    account_keys = [_account_key(item) for item in account_values]
    wallet_matches = [
        index
        for index, (address, _, _) in enumerate(account_keys)
        if address == scope.wallet_address
    ]
    if len(wallet_matches) != 1:
        raise ValueError("wallet identity is not unique in the Solana transaction")
    wallet_index = wallet_matches[0]
    _, signer, writable = account_keys[wallet_index]
    if wallet_index != 0 or not signer or not writable:
        raise ValueError("bound wallet is not the transaction fee payer")
    pre_balances = meta.get("preBalances")
    post_balances = meta.get("postBalances")
    fee_lamports = meta.get("fee")
    if (
        not isinstance(pre_balances, list)
        or not isinstance(post_balances, list)
        or len(pre_balances) != len(account_keys)
        or len(post_balances) != len(account_keys)
        or isinstance(fee_lamports, bool)
        or not isinstance(fee_lamports, int)
        or fee_lamports < 0
    ):
        raise ValueError("native balance or fee evidence is invalid")
    native_delta = Decimal(
        post_balances[wallet_index] - pre_balances[wallet_index]
    ) / Decimal(1_000_000_000)
    native_fee = Decimal(fee_lamports) / Decimal(1_000_000_000)
    receipt = {
        "transaction_hash": transaction_hash,
        "status": "FAILED" if meta.get("err") is not None else "CONFIRMED",
        "network": scope.network,
        "wallet_address": scope.wallet_address,
        "trading_pair": config.trading_pair,
        "side": config.side,
        "block_time": value.get("blockTime"),
        "slot": value.get("slot"),
        "native_fee_amount": format(native_fee, "f"),
        "evidence_source": "solana_rpc_finalized_balance_deltas",
    }
    if receipt["status"] == "FAILED":
        return receipt
    quote_delta = _token_delta(
        meta,
        wallet_address=str(scope.wallet_address),
        mint=scope.quote_mint,
        decimals=scope.quote_decimals,
    )
    if config.base_symbol.upper() == "SOL":
        if config.side == "BUY":
            amount_in = -quote_delta
            amount_out = native_delta
            receipt["gross_output_amount"] = format(native_delta + native_fee, "f")
        else:
            amount_in = -native_delta - native_fee
            amount_out = quote_delta
    else:
        base_delta = _token_delta(
            meta,
            wallet_address=str(scope.wallet_address),
            mint=config.base_mint,
            decimals=config.base_decimals,
        )
        if config.side == "BUY":
            amount_in = -quote_delta
            amount_out = base_delta
        else:
            amount_in = -base_delta
            amount_out = quote_delta
    if amount_in <= 0 or amount_out <= 0:
        raise ValueError("finalized wallet balance deltas conflict with swap direction")
    receipt["input_amount"] = format(amount_in, "f")
    receipt["output_amount"] = format(amount_out, "f")
    return receipt


async def _solana_transaction_receipt(
    client: Any,
    scope: RuntimeScope,
    config: SwapRequest,
    transaction_hash: str,
) -> dict[str, Any]:
    network_config = await client.gateway.get_network_config(scope.network)
    node_url = _find(network_config, "node_url", "nodeUrl")
    if not isinstance(node_url, str) or not node_url.strip():
        raise ValueError("configured Solana RPC node URL is unavailable")
    parsed = urlparse(node_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("configured Solana RPC node URL is invalid")
    value = await _fetch_solana_transaction(
        node_url,
        transaction_hash,
        timeout=getattr(client, "timeout", None),
    )
    if value is None:
        raise ValueError("transaction is not yet available at finalized commitment")
    return _solana_receipt_from_transaction(value, transaction_hash, config, scope)


def _receipt_issue(receipt: dict[str, Any], config: SwapRequest) -> str | None:
    try:
        amount_in = _decimal(
            receipt.get("input_amount"), "receipt input", positive=True
        )
        amount_out = _decimal(
            receipt.get("output_amount"), "receipt output", positive=True
        )
    except ValueError:
        return "confirmed receipt lacks exact positive input and output amounts"
    if config.side == "BUY":
        floor = config.amount * (Decimal(1) - config.slippage_pct / Decimal(100))
        if amount_in > config.max_quote_input or amount_out < floor:
            return "confirmed preparation receipt violates its cap or slippage floor"
    elif amount_in != config.amount:
        return "confirmed restoration receipt violates exact attributed input"
    return None


def _record_receipt(record: dict[str, Any]) -> dict[str, Any] | None:
    result = record.get("result")
    receipt = result.get("receipt") if isinstance(result, dict) else None
    return receipt if isinstance(receipt, dict) else None


def _public_record(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    return {
        key: record.get(key)
        for key in (
            "created_at",
            "updated_at",
            "operation_id",
            "operation_kind",
            "tick",
            "phase",
            "mutation_possible",
            "result",
            "reason",
        )
        if record.get(key) is not None
    }


def _outcome_from_receipt(
    receipt: dict[str, Any],
    config: SwapRequest,
) -> tuple[str, str, bool, str | None]:
    status = str(receipt.get("status") or "UNKNOWN").upper()
    if status in _CONFIRMED:
        issue = _receipt_issue(receipt, config)
        if issue:
            return "manual_review", "manual_review", False, issue
        return "confirmed", "confirmed", False, None
    if status in _FAILED:
        return (
            "manual_review",
            "manual_review",
            False,
            f"finalized transaction has terminal swap status {status}",
        )
    return "pending", "submitted", False, None


async def _refresh_receipt(
    client: Any,
    scope: RuntimeScope,
    config: SwapRequest,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    observed = await _solana_transaction_receipt(
        client,
        scope,
        config,
        receipt["transaction_hash"],
    )
    if observed["transaction_hash"] != receipt["transaction_hash"]:
        raise ValueError("Solana transaction hash conflicts")
    return {
        **receipt,
        **{
            key: item
            for key, item in observed.items()
            if item not in (None, "", "UNKNOWN")
        },
    }


async def _cleanup_post_transaction_refresh(
    client: Any,
    scope: RuntimeScope,
    config: SwapRequest,
    receipt: dict[str, Any],
    trace: reporting.TraceRecorder,
) -> tuple[bool, dict[str, Any] | None, str | None]:
    """Refresh exact transaction status, then capture diagnostic wallet state."""

    if config.reason != "post_close_residual_cleanup":
        return False, None, None
    with trace.stage("cleanup_post_transaction_refresh") as stage:
        try:
            refreshed = await _refresh_receipt(client, scope, config, receipt)
            if (
                str(refreshed.get("status") or "").upper() not in _CONFIRMED
                or _receipt_issue(refreshed, config) is not None
            ):
                raise ValueError("cleanup transaction is not exactly confirmed")
            balances = await refresh_balances(scope, client)
            diagnostic = {
                "transaction_hash": refreshed["transaction_hash"],
                "transaction_status": refreshed["status"],
                "confirmed_input_amount": refreshed["input_amount"],
                "base_wallet_balance": format(
                    _balance(
                        balances,
                        symbol=config.base_symbol,
                        mint=config.base_mint,
                    ),
                    "f",
                ),
                "quote_wallet_balance": format(
                    _balance(
                        balances,
                        symbol=scope.quote_symbol,
                        mint=scope.quote_mint,
                    ),
                    "f",
                ),
            }
            stage.update(diagnostic)
            return True, diagnostic, None
        except Exception as exc:
            stage["_outcome"] = "unavailable"
            reason = reporting.safe_error(
                f"post-cleanup evidence refresh failed: {type(exc).__name__}: {exc}"
            )
            stage["reason"] = reason
            return False, None, reason


async def _cleanup_attribution(
    client: Any,
    scope: RuntimeScope,
    config: SwapRequest,
) -> dict[str, Any]:
    try:
        prior_stop = read_prior_tick_stop(
            scope, str(config.executor_id), int(config.closed_tick)
        )
    except ValueError as exc:
        raise _ManualReview(str(exc)) from exc
    value = await client.executors.get_executor(config.executor_id)
    row = value.get("executor", value) if isinstance(value, dict) else None
    if not isinstance(row, dict):
        raise _ManualReview("exact closed executor detail is unavailable")
    try:
        executor = normalize_executor(row)
        evidence = exact_close_evidence(executor)
    except ValueError as exc:
        raise _ManualReview(str(exc)) from exc
    if (
        executor["executor_id"] != config.executor_id
        or executor["controller_id"] != scope.controller_id
        or executor["active"]
        or executor["pool_address"] != config.pool_address
        or executor["trading_pair"] != config.trading_pair
    ):
        raise _ManualReview(
            "closed executor ownership or terminal identity conflicts; "
            "residual attribution conflicts"
        )
    if (
        evidence["position_address"] != config.position_id
        or evidence["pool_address"] != config.pool_address
        or evidence["base_symbol"] != config.base_symbol
        or evidence["base_mint"] != config.base_mint
        or evidence["base_decimals"] != config.base_decimals
        or evidence["residual_base_amount"] != config.amount
        or evidence["residual_base_amount"] != config.attributed_base_amount
    ):
        raise _ManualReview("closed executor residual attribution conflicts")
    native_close_status = evidence["native_swap_status"]
    if native_close_status in NATIVE_SWAP_PENDING:
        raise _ManualReview("native close-out remains pending or unknown")
    if native_close_status not in NATIVE_SWAP_SUCCESS | NATIVE_SWAP_FAILED:
        raise _ManualReview("native close-out status is not recognized")
    return {
        "prior_stop": prior_stop,
        "executor_id": config.executor_id,
        "position_id": evidence["position_address"],
        "pool_address": config.pool_address,
        "base_mint": evidence["base_mint"],
        "base_decimals": evidence["base_decimals"],
        "residual_base_amount": format(evidence["residual_base_amount"], "f"),
        "native_close_status": native_close_status,
        "close_transaction_hash": evidence["close_transaction_hash"],
    }


def _restoration_attribution(
    store: ReceiptStore, config: SwapRequest
) -> dict[str, Any]:
    preparation = store.read_confirmed_swap(str(config.attribution_operation_id))
    intent = preparation.get("intent")
    receipt = _record_receipt(preparation)
    if (
        not isinstance(intent, dict)
        or not isinstance(receipt, dict)
        or intent.get("reason") != "inventory_preparation"
        or intent.get("pool_address") != config.pool_address
        or intent.get("trading_pair") != config.trading_pair
        or intent.get("base_mint") != config.base_mint
        or intent.get("base_decimals") != config.base_decimals
        or _decimal(receipt.get("output_amount"), "preparation output", positive=True)
        != config.amount
    ):
        raise _ManualReview(
            "confirmed preparation receipt does not exactly attribute restoration"
        )
    consumers = [
        record
        for record in store.list_records("create")
        if isinstance(record.get("intent"), dict)
        and record["intent"].get("preparation_operation_id")
        == config.attribution_operation_id
        and record.get("phase")
        in {"submitting", "submitted", "confirmed", "uncertain", "manual_review"}
    ]
    if consumers:
        raise _ManualReview(
            "prepared inventory may have been consumed by an executor create"
        )
    return {
        "preparation_operation_id": config.attribution_operation_id,
        "preparation_transaction_hash": receipt["transaction_hash"],
        "attributed_base_amount": receipt["output_amount"],
    }


async def _ensure_no_owned_pool_executor(
    client: Any, scope: RuntimeScope, config: SwapRequest
) -> None:
    for row in await fetch_all_executors(client, scope.account_name):
        try:
            executor = normalize_executor(row)
        except ValueError as exc:
            raise _ManualReview(str(exc)) from exc
        if (
            executor["controller_id"] == scope.controller_id
            and executor["active"]
            and (
                executor["pool_address"] == config.pool_address
                or executor["trading_pair"] == config.trading_pair
            )
        ):
            raise _ManualReview(
                "active or reconciling executor may own prepared inventory"
            )


def _ensure_no_unconsumed_preparation(store: ReceiptStore, operation_id: str) -> None:
    """Do not prepare a second inventory lot until the prior lot was consumed."""

    for record in store.unconsumed_preparations():
        if record.get("operation_id") != operation_id:
            raise _ManualReview(
                "a confirmed preparation has not been consumed by one confirmed "
                "executor create or exact restoration"
            )


async def _ensure_preparation_capacity(
    client: Any, scope: RuntimeScope, config: SwapRequest
) -> dict[str, Any]:
    """Revalidate portfolio capacity before buying selected LP inventory."""

    normalized = [
        normalize_executor(row)
        for row in await fetch_all_executors(client, scope.account_name)
    ]
    active = [row for row in normalized if row["active"]]
    foreign = [
        row["executor_id"]
        for row in active
        if row["controller_id"] != scope.controller_id
    ]
    owned = [row for row in active if row["controller_id"] == scope.controller_id]
    if foreign:
        raise _ManualReview("foreign live executor overlaps the wallet scope")
    if len(owned) >= integer_config(scope.config, "max_open_executors"):
        raise ValueError("executor capacity is full")
    if any(
        row["pool_address"] == config.pool_address
        or row["trading_pair"] == config.trading_pair
        for row in owned
    ):
        raise ValueError("selected pool already has an active executor")
    active_exposure = sum((row["exposure_quote"] for row in owned), Decimal(0))
    selected = _decimal(config.amount_quote, "selected allocation", positive=True)
    total = decimal_config(scope.config, "total_amount_quote", positive=True)
    if active_exposure + selected > total:
        raise ValueError("aggregate LP capital would exceed its configured limit")
    return {
        "active_executors": len(owned),
        "active_exposure_quote": active_exposure,
        "selected_allocation_quote": selected,
        "total_allocation_limit_quote": total,
    }


def _intent(config: SwapRequest) -> dict[str, Any]:
    return {
        "reason": config.reason,
        "pool_address": config.pool_address,
        "trading_pair": config.trading_pair,
        "side": config.side,
        "amount": format(config.amount, "f"),
        "base_symbol": config.base_symbol,
        "base_mint": config.base_mint,
        "base_decimals": config.base_decimals,
        "max_quote_input": (
            format(config.max_quote_input, "f")
            if config.max_quote_input is not None
            else None
        ),
        "attributed_base_amount": (
            format(config.attributed_base_amount, "f")
            if config.attributed_base_amount is not None
            else None
        ),
        "slippage_pct": format(config.slippage_pct, "f"),
        "plan_digest": config.plan_digest,
        "amount_quote": (
            format(config.amount_quote, "f")
            if config.amount_quote is not None
            else None
        ),
        "range_half_width_pct": (
            format(config.range_half_width_pct, "f")
            if config.range_half_width_pct is not None
            else None
        ),
        "attribution_operation_id": config.attribution_operation_id,
        "executor_id": config.executor_id,
        "position_id": config.position_id,
        "closed_tick": config.closed_tick,
        "candidate": config.candidate,
    }


def _schema_fields(value: Any, label: str) -> set[str]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} schema is invalid")
    value = value.get("result", value)
    fields = value.get("fields") if isinstance(value, dict) else None
    if fields is not None:
        if not isinstance(fields, list) or not all(
            isinstance(row, dict) and isinstance(row.get("name"), str) for row in fields
        ):
            raise ValueError(f"{label} schema fields are invalid")
        return {row["name"] for row in fields}
    properties = value.get("properties") if isinstance(value, dict) else None
    nested = value.get("schema") if isinstance(value, dict) else None
    if not isinstance(properties, dict) and isinstance(nested, dict):
        properties = nested.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise ValueError(f"{label} schema has no properties")
    return set(properties)


def _order_executor_request(scope: RuntimeScope, config: SwapRequest) -> dict[str, Any]:
    return {
        "action": "create",
        "executor_type": "order_executor",
        "account_name": scope.account_name,
        "controller_id": scope.controller_id,
        "executor_config": {
            "type": "order_executor",
            # The checked-out Condor permission callback currently reads this
            # nested copy while manage_executors owns the top-level authority.
            # Both values are derived from the same exact runtime scope.
            "controller_id": scope.controller_id,
            "connector_name": scope.network,
            "trading_pair": config.trading_pair,
            "side": 1 if config.side == "BUY" else 2,
            "amount": format(config.amount, "f"),
            "execution_strategy": "MARKET",
            "level_id": config.operation_id,
        },
    }


async def _validate_order_executor_surface(
    client: Any,
    scope: RuntimeScope,
    config: SwapRequest,
    *,
    effective_executor_count: int,
) -> dict[str, Any]:
    request = _order_executor_request(scope, config)
    fields = _schema_fields(
        await client.executors.get_executor_config_schema("order_executor"),
        "order executor",
    )
    missing = set(request["executor_config"]) - fields - {"type"}
    if missing:
        raise ValueError(f"order executor schema lacks fields: {sorted(missing)}")

    connector = scope.swap_connector.split("/", 1)[0]
    network_config = await client.gateway.get_network_config(scope.network)
    swap_provider = _find(network_config, "swap_provider", "swapProvider")
    if (
        not isinstance(swap_provider, str)
        or swap_provider.split("/", 1)[0].strip().casefold() != connector.casefold()
    ):
        raise ValueError(
            "network default swap provider conflicts with the Strategy connector"
        )
    connector_config = await client.gateway.get_connector_config(connector)
    configured_slippage = _find(
        connector_config,
        "slippage_pct",
        "slippagePct",
    )
    if configured_slippage in (None, ""):
        raise ValueError("Jupiter executor slippage configuration is unavailable")
    executor_slippage = _decimal(
        configured_slippage,
        "Jupiter executor slippage",
        positive=True,
    )
    if executor_slippage > config.slippage_pct:
        raise ValueError(
            "Jupiter executor slippage exceeds the frozen Strategy maximum"
        )

    limits = scope.config.get("risk_limits")
    if not isinstance(limits, dict):
        raise ValueError("Condor executor risk limits are unavailable")
    risk_quote_limit = _decimal(
        limits.get("max_position_size_quote"),
        "Condor position risk limit",
        positive=True,
    )
    risk_executor_limit = integer_config(limits, "max_open_executors")
    if effective_executor_count >= risk_executor_limit:
        raise ValueError("native Condor executor capacity is full")
    return {
        "executor_request": request,
        "swap_provider": swap_provider,
        "executor_slippage_pct": executor_slippage,
        "risk_quote_limit": risk_quote_limit,
        "risk_executor_limit": risk_executor_limit,
    }


def _executor_detail(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("order executor detail is invalid")
    nested = value.get("executor")
    row = nested if isinstance(nested, dict) else value
    if not isinstance(row, dict):
        raise ValueError("order executor detail is invalid")
    return row


def _side_number(value: Any) -> int | None:
    text = str(getattr(value, "value", value)).strip().upper()
    if text in {"1", "BUY", "TRADETYPE.BUY"}:
        return 1
    if text in {"2", "SELL", "TRADETYPE.SELL"}:
        return 2
    return None


def _order_executor_matches(
    value: Any,
    executor_id: str,
    scope: RuntimeScope,
    config: SwapRequest,
) -> dict[str, Any]:
    row = _executor_detail(value)
    nested = row.get("config") if isinstance(row.get("config"), dict) else {}
    custom = row.get("custom_info") if isinstance(row.get("custom_info"), dict) else {}
    ids = {
        str(item).strip()
        for item in (row.get("executor_id"), row.get("id"), nested.get("id"))
        if item
    }
    owners = {
        str(item).strip()
        for item in (row.get("controller_id"), nested.get("controller_id"))
        if item
    }
    expected = _order_executor_request(scope, config)["executor_config"]
    strategy = str(nested.get("execution_strategy") or "").strip().upper()
    if strategy.startswith("EXECUTIONSTRATEGY."):
        strategy = strategy.rsplit(".", 1)[-1]
    if (
        ids != {executor_id}
        or owners != {scope.controller_id}
        or (row.get("type") or nested.get("type")) != "order_executor"
        or nested.get("connector_name") != expected["connector_name"]
        or nested.get("trading_pair") != expected["trading_pair"]
        or _side_number(nested.get("side")) != expected["side"]
        or _decimal(nested.get("amount"), "order executor amount", positive=True)
        != config.amount
        or strategy != "MARKET"
        or custom.get("level_id", nested.get("level_id")) != config.operation_id
    ):
        raise _ManualReview(
            "order executor detail conflicts with the immutable swap intent"
        )
    return {
        "row": row,
        "config": nested,
        "custom": custom,
        "status": str(row.get("status") or "").strip().upper(),
        "is_active": bool(row.get("is_active")),
        "close_type": str(row.get("close_type") or "").strip().upper() or None,
    }


def _transaction_hashes(value: Any) -> set[str]:
    hashes: set[str] = set()
    pending = [value]
    seen: set[int] = set()
    names = {
        "exchange_order_id",
        "transaction_hash",
        "tx_hash",
        "signature",
    }
    while pending:
        current = pending.pop(0)
        if isinstance(current, dict):
            if id(current) in seen:
                continue
            seen.add(id(current))
            for key, item in current.items():
                if str(key).casefold() in names and isinstance(item, str):
                    if item.strip():
                        hashes.add(item.strip())
                elif isinstance(item, (dict, list, tuple)):
                    pending.append(item)
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return hashes


async def _reconcile_order_executor(
    *,
    client: Any,
    scope: RuntimeScope,
    config: SwapRequest,
    store: ReceiptStore,
    identity: OperationIdentity,
    record: dict[str, Any],
    executor_id: str,
    trace: reporting.TraceRecorder,
) -> dict[str, Any]:
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    recorded_id = str(result.get("swap_executor_id") or "").strip() or None
    if recorded_id is not None and recorded_id != executor_id:
        raise _ManualReview(
            "native swap executor identity conflicts with the operation receipt"
        )
    if record["phase"] == "confirmed":
        recovered = await _recover(
            client, scope, config, store, identity, record, trace
        )
        if recovered is None:
            raise _ManualReview("confirmed swap receipt could not be recovered")
        return recovered
    if record["phase"] == "rejected_before_submit":
        raise _ManualReview(
            "native swap executor identity conflicts with a pre-submit rejection"
        )
    if record["phase"] in {"manual_review", "ambiguous"}:
        recovered = await _recover(
            client, scope, config, store, identity, record, trace
        )
        if recovered is None:
            raise _ManualReview("quarantined swap operation is unavailable")
        return recovered

    with trace.stage("native_order_executor_reconciliation") as stage:
        try:
            detail = await client.executors.get_executor(executor_id=executor_id)
        except Exception as exc:
            if record["phase"] == "admitted":
                record = store.write(
                    identity,
                    phase="submitting",
                    mutation_possible=True,
                    result={**result, "swap_executor_id": executor_id},
                )
            record = store.write(
                identity,
                phase="submitted",
                mutation_possible=True,
                result={
                    **(
                        record.get("result")
                        if isinstance(record.get("result"), dict)
                        else {}
                    ),
                    "swap_executor_id": executor_id,
                },
                reason=reporting.safe_error(
                    "exact native order executor detail is unavailable: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )
            stage["_outcome"] = "unavailable"
            return {
                "status": "submitted",
                "mutation": True,
                "mutation_classification": "submitted",
                "retry_allowed": False,
                "swap_executor_id": executor_id,
                "reason": record["reason"],
                "operation_state": _public_record(record),
            }

        matched = _order_executor_matches(
            detail,
            executor_id,
            scope,
            config,
        )
        if record["phase"] == "admitted":
            record = store.write(
                identity,
                phase="submitting",
                mutation_possible=True,
                result={**result, "swap_executor_id": executor_id},
            )
        record = store.write(
            identity,
            phase="submitted",
            mutation_possible=True,
            result={
                **(
                    record.get("result")
                    if isinstance(record.get("result"), dict)
                    else {}
                ),
                "swap_executor_id": executor_id,
            },
        )
        stage.update(
            {
                "executor_id": executor_id,
                "status": matched["status"],
                "close_type": matched["close_type"],
            }
        )

    hashes = _transaction_hashes(matched["custom"].get("held_position_orders", []))
    if len(hashes) > 1:
        reason = "order executor exposes multiple transaction identities"
        record = store.write(
            identity,
            phase="manual_review",
            mutation_possible=True,
            result={
                **record["result"],
                "swap_executor_id": executor_id,
                "transaction_hashes": sorted(hashes),
            },
            reason=reason,
        )
        return {
            "status": "manual_review",
            "mutation": True,
            "mutation_classification": "manual_review",
            "retry_allowed": False,
            "swap_executor_id": executor_id,
            "reason": reason,
            "operation_state": _public_record(record),
        }
    if not hashes:
        settled = matched["status"] in {
            "COMPLETE",
            "COMPLETED",
            "TERMINATED",
            "CANCELED",
            "CANCELLED",
            "CLOSED",
            "STOPPED",
        }
        if settled:
            reason = "terminal order executor lacks one Jupiter transaction identity"
            record = store.write(
                identity,
                phase="manual_review",
                mutation_possible=True,
                result={**record["result"], "swap_executor_id": executor_id},
                reason=reason,
            )
            return {
                "status": "manual_review",
                "mutation": True,
                "mutation_classification": "manual_review",
                "retry_allowed": False,
                "swap_executor_id": executor_id,
                "reason": reason,
                "operation_state": _public_record(record),
            }
        return {
            "status": "submitted",
            "mutation": True,
            "mutation_classification": "submitted",
            "retry_allowed": False,
            "swap_executor_id": executor_id,
            "reason": "native order executor has not exposed a transaction yet",
            "operation_state": _public_record(record),
        }

    transaction_hash = hashes.pop()
    with trace.stage("finalized_transaction_reconciliation") as stage:
        try:
            receipt = await _solana_transaction_receipt(
                client,
                scope,
                config,
                transaction_hash,
            )
        except Exception as exc:
            stage["_outcome"] = "unavailable"
            return {
                "status": "submitted",
                "mutation": True,
                "mutation_classification": "submitted",
                "retry_allowed": False,
                "swap_executor_id": executor_id,
                "reason": reporting.safe_error(
                    f"finalized Solana transaction evidence is unavailable: "
                    f"{type(exc).__name__}: {exc}"
                ),
                "operation_state": _public_record(record),
            }
        stage.update(
            {
                "transaction_hash": transaction_hash,
                "status": receipt["status"],
            }
        )

    status, phase, retry_allowed, reason = _outcome_from_receipt(receipt, config)
    deployment_input = _deployment_input(config)
    record = store.write(
        identity,
        phase=phase,
        mutation_possible=True,
        result={
            **record["result"],
            "swap_executor_id": executor_id,
            "receipt": receipt,
            **(
                {"deployment_input": deployment_input}
                if deployment_input is not None
                else {}
            ),
            **({"confirmed_tick": scope.current_tick} if status == "confirmed" else {}),
        },
        reason=reason,
    )
    release = False
    diagnostic = None
    refresh_error = None
    if status == "confirmed":
        release, diagnostic, refresh_error = await _cleanup_post_transaction_refresh(
            client, scope, config, receipt, trace
        )
    return {
        "status": status,
        "mutation": True,
        "mutation_classification": phase,
        "retry_allowed": retry_allowed,
        "swap_executor_id": executor_id,
        **({"reason": reason} if reason else {}),
        **(
            {"quote": record["result"]["quote"]}
            if isinstance(record.get("result"), dict)
            and isinstance(record["result"].get("quote"), dict)
            else {}
        ),
        **(
            {"attribution": record["result"]["attribution"]}
            if isinstance(record.get("result"), dict)
            and isinstance(record["result"].get("attribution"), dict)
            else {}
        ),
        "receipt": receipt,
        **(
            {"deployment_input": deployment_input}
            if status == "confirmed" and deployment_input is not None
            else {}
        ),
        **(
            {
                "same_tick_lp_create_allowed": _lp_create_allowed(
                    record,
                    scope.current_tick,
                )
            }
            if config.reason == "inventory_preparation"
            else {}
        ),
        "capacity_release_allowed": release,
        **({"post_transaction_refresh": diagnostic} if diagnostic is not None else {}),
        **(
            {"capacity_release_reason": refresh_error}
            if refresh_error is not None
            else {}
        ),
        "operation_state": _public_record(record),
    }


async def _recover(
    client: Any,
    scope: RuntimeScope,
    config: SwapRequest,
    store: ReceiptStore,
    identity: OperationIdentity,
    record: dict[str, Any],
    trace: reporting.TraceRecorder,
) -> dict[str, Any] | None:
    phase = str(record["phase"])
    if phase == "rejected_before_submit":
        return None
    if phase == "admitted":
        result = record.get("result") if isinstance(record.get("result"), dict) else {}
        return {
            "status": "admitted",
            "mutation": False,
            "mutation_classification": "admitted",
            "retry_allowed": False,
            "reason": (
                "native request was already emitted; provide its exact returned "
                "swap_executor_id or require manual reconciliation"
            ),
            **(
                {"executor_request": result["executor_request"]}
                if isinstance(result.get("executor_request"), dict)
                else {}
            ),
            "operation_state": _public_record(record),
        }
    if phase == "confirmed":
        receipt = _record_receipt(record)
        result = record.get("result") if isinstance(record.get("result"), dict) else {}
        allocation = result.get("inventory_allocation")
        if not isinstance(receipt, dict) and not (
            config.reason == "inventory_preparation"
            and isinstance(allocation, dict)
            and allocation.get("source") == "existing_wallet_balance"
        ):
            raise _ManualReview(
                "confirmed preparation or swap operation lacks exact evidence"
            )
        if isinstance(receipt, dict):
            release, diagnostic, refresh_error = (
                await _cleanup_post_transaction_refresh(
                    client, scope, config, receipt, trace
                )
            )
        else:
            release, diagnostic, refresh_error = False, None, None
        deployment_input = (
            result.get("deployment_input")
            if isinstance(result.get("deployment_input"), dict)
            else _deployment_input(config)
        )
        return {
            "status": "confirmed",
            "mutation": isinstance(receipt, dict),
            "mutation_classification": "confirmed",
            "retry_allowed": False,
            "operation_state": _public_record(record),
            **({"receipt": receipt} if isinstance(receipt, dict) else {}),
            **(
                {"inventory_allocation": allocation}
                if isinstance(allocation, dict)
                else {}
            ),
            **(
                {"deployment_input": deployment_input}
                if deployment_input is not None
                else {}
            ),
            **(
                {
                    "same_tick_lp_create_allowed": _lp_create_allowed(
                        record,
                        scope.current_tick,
                    )
                }
                if config.reason == "inventory_preparation"
                and isinstance(record.get("result"), dict)
                else {}
            ),
            "capacity_release_allowed": release,
            **(
                {"post_transaction_refresh": diagnostic}
                if diagnostic is not None
                else {}
            ),
            **(
                {"capacity_release_reason": refresh_error}
                if refresh_error is not None
                else {}
            ),
        }
    if phase in {"manual_review", "ambiguous"}:
        return {
            "status": "manual_review",
            "mutation": bool(record["mutation_possible"]),
            "mutation_classification": phase,
            "retry_allowed": False,
            "reason": record.get("reason") or "operation is quarantined",
            "operation_state": _public_record(record),
        }
    reason = (
        "operation may have mutated but lacks one exact native order executor "
        "identity; Gateway-history guessing is prohibited"
    )
    updated = store.write(
        identity,
        phase="manual_review",
        mutation_possible=True,
        reason=reason,
    )
    return {
        "status": "manual_review",
        "mutation": True,
        "mutation_classification": "manual_review",
        "retry_allowed": False,
        "reason": reason,
        "operation_state": _public_record(updated),
        "capacity_release_allowed": False,
    }


async def _workflow(
    config: Config,
    trace: reporting.TraceRecorder,
    state: dict[str, Any],
) -> tuple[dict[str, Any], RuntimeScope]:
    recovery_only = config.reason is None
    with trace.stage("runtime_resolution") as stage:
        scope = resolve_runtime(config.controller_id)
        state["scope"] = scope
        stage.update(
            {
                "controller_id": scope.controller_id,
                "strategy": scope.strategy_slug,
                "execution_mode": scope.execution_mode,
                "current_tick": scope.current_tick,
                "recovery_only": recovery_only,
            }
        )
        if not recovery_only and config.reason != "inventory_preparation":
            request = _resolve_request(config, scope)
            state["request"] = request
            stage.update(
                {
                    "reason": request.reason,
                    "pool_address": request.pool_address,
                    "plan_digest": request.plan_digest,
                }
            )

    with trace.stage("wallet_binding") as stage:
        client = await get_hummingbot_client(scope)
        scope = await bind_wallet(scope, client)
        state["scope"] = scope
        stage.update(
            {
                "server_name": scope.server_name,
                "network": scope.network,
                "wallet_bound": True,
            }
        )

    store = ReceiptStore(scope)
    prior = store.read_by_id(config.operation_id)
    if prior is not None:
        request = _recorded_request(prior, scope)
        state["request"] = request
        if not recovery_only:
            supplied = _resolve_request(
                config,
                scope,
                attributed_base_amount=request.attributed_base_amount,
            )
            if _intent(supplied) != prior.get("intent"):
                raise _ManualReview(
                    "existing swap operation identity conflicts with this request"
                )
    elif recovery_only:
        if prior is None:
            raise _ManualReview(
                "recovery requires one exact current-session operation receipt"
            )
    elif config.reason == "inventory_preparation":
        with trace.stage("existing_base_inventory_authorization") as stage:
            initial_plan = build_candidate_plan(
                config.candidate,
                amount_quote=config.amount_quote,
                range_half_width_pct=config.range_half_width_pct,
                strategy_config=scope.config,
            )
            planning_balances = await refresh_balances(scope, client)
            attributed, evidence = _authorized_existing_base(
                plan=initial_plan,
                balances=planning_balances,
                scope=scope,
            )
            request = _resolve_request(
                config,
                scope,
                attributed_base_amount=(attributed if attributed > 0 else None),
            )
            state["request"] = request
            stage.update(
                {
                    "authorization_enabled": scope.config[
                        "use_existing_base_inventory"
                    ],
                    **evidence,
                    "swap_required": request.amount > 0,
                }
            )
    if request.reason == "inventory_preparation" and request.max_quote_input > (
        decimal_config(scope.config, "max_quote_per_executor", positive=True)
    ):
        raise ValueError("preparation quote cap exceeds the frozen per-executor limit")
    if recovery_only:
        with trace.stage("recorded_intent_recovery") as stage:
            stage.update(
                {
                    "operation_id": request.operation_id,
                    "recorded_tick": prior["tick"],
                    "reason": request.reason,
                    "pool_address": request.pool_address,
                    "candidate_available": isinstance(request.candidate, dict),
                }
            )
    identity = store.identity(
        operation_id=request.operation_id,
        operation_kind="swap",
        intent=(
            prior["intent"]
            if recovery_only and isinstance(prior, dict)
            else _intent(request)
        ),
        tick=(
            int(prior["tick"])
            if isinstance(prior, dict) and isinstance(prior.get("tick"), int)
            else None
        ),
    )
    state.update({"store": store, "identity": identity})

    async with controller_mutation_lock(scope.controller_id):
        with trace.stage("prior_receipt_lookup") as stage:
            try:
                existing = store.read(identity)
            except ValueError as exc:
                if store.read_by_id(request.operation_id) is not None:
                    raise _ManualReview(
                        "existing swap operation identity conflicts with this request"
                    ) from exc
                raise
            stage["phase"] = existing.get("phase") if existing else "absent"
        if existing is not None:
            existing_result = (
                existing.get("result")
                if isinstance(existing.get("result"), dict)
                else {}
            )
            recorded_executor_id = str(
                existing_result.get("swap_executor_id") or ""
            ).strip()
            requested_executor_id = str(config.swap_executor_id or "").strip()
            if (
                recorded_executor_id
                and requested_executor_id
                and recorded_executor_id != requested_executor_id
            ):
                raise _ManualReview(
                    "native swap executor identity conflicts with the operation receipt"
                )
            executor_id = requested_executor_id or recorded_executor_id
            if executor_id:
                state["mutation_possible"] = True
                return (
                    await _reconcile_order_executor(
                        client=client,
                        scope=scope,
                        config=request,
                        store=store,
                        identity=identity,
                        record=existing,
                        executor_id=executor_id,
                        trace=trace,
                    ),
                    scope,
                )
            recovered = await _recover(
                client, scope, request, store, identity, existing, trace
            )
            if recovered is not None:
                return recovered, scope
        elif config.swap_executor_id is not None:
            raise _ManualReview(
                "native swap executor identity was supplied before operation admission"
            )

        conflicts = [
            record
            for record in store.unresolved_operations()
            if record.get("operation_id") != request.operation_id
        ]
        if conflicts:
            raise _ManualReview(
                "current-session operation "
                f"{conflicts[0]['operation_id']} remains unresolved"
            )

        if request.reason == "inventory_preparation":
            _ensure_no_unconsumed_preparation(store, request.operation_id)
            with trace.stage("selected_candidate_refresh") as stage:
                tvl_policy = pool_tvl_policy(scope.config)
                refreshed = await orca.refresh_candidate(
                    config.candidate,
                    tvl_policy["minimum_tvl_usd"],
                )
                stage.update(
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
                        "identity_unchanged": True,
                    }
                )
            with trace.stage("preparation_capacity_revalidation") as stage:
                capacity = await _ensure_preparation_capacity(client, scope, request)
                stage.update(capacity)
        else:
            capacity = {
                "active_executors": 0,
                "active_exposure_quote": Decimal(0),
            }

        with trace.stage("token_registry") as stage:
            tokens = _token_rows(await client.gateway.get_network_tokens(scope.network))
            base = _registered_token_or_none(
                tokens,
                address=request.base_mint,
                symbol=request.base_symbol,
                decimals=request.base_decimals,
            )
            quote_token = _require_registered_token(
                tokens,
                address=scope.quote_mint,
                symbol=scope.quote_symbol,
                decimals=scope.quote_decimals,
            )
            registration = None
            if base is None:
                registration = {
                    "status": "proposed",
                    "network": scope.network,
                    "token": {
                        "address": request.base_mint,
                        "symbol": request.base_symbol,
                        "decimals": request.base_decimals,
                    },
                    "mutation": False,
                }
                state["token_registration"] = registration
                if scope.execution_mode == "dry_run":
                    stage.update(
                        {
                            "_outcome": "proposed",
                            "registration_required": True,
                            "token": registration["token"],
                            "quote": quote_token,
                        }
                    )
                    return {
                        "status": "not_submitted",
                        "mutation": False,
                        "mutation_classification": "rejected_before_submit",
                        "retry_allowed": False,
                        "reason": (
                            "dry run cannot register the selected Gateway token"
                        ),
                        "proposed_token_registration": registration,
                        "capacity_release_allowed": False,
                    }, scope

                registration["status"] = "submitting"
                registration["mutation"] = True
                state["mutation_possible"] = True
                add_error = None
                try:
                    await client.gateway.add_token(
                        network_id=scope.network,
                        address=request.base_mint,
                        symbol=request.base_symbol,
                        decimals=request.base_decimals,
                        name=request.base_symbol,
                    )
                except Exception as exc:
                    add_error = reporting.safe_error(f"{type(exc).__name__}: {exc}")
                try:
                    tokens = _token_rows(
                        await client.gateway.get_network_tokens(scope.network)
                    )
                    base = _registered_token_or_none(
                        tokens,
                        address=request.base_mint,
                        symbol=request.base_symbol,
                        decimals=request.base_decimals,
                    )
                    quote_token = _require_registered_token(
                        tokens,
                        address=scope.quote_mint,
                        symbol=scope.quote_symbol,
                        decimals=scope.quote_decimals,
                    )
                except Exception as exc:
                    registration.update(
                        {
                            "status": "uncertain",
                            "verification_error": reporting.safe_error(
                                f"{type(exc).__name__}: {exc}"
                            ),
                        }
                    )
                    stage.update(
                        {
                            "_outcome": "uncertain",
                            "registration": registration,
                        }
                    )
                    raise _ManualReview(
                        "selected Gateway token registration could not be "
                        "verified exactly"
                    ) from exc
                if base is None:
                    registration.update(
                        {
                            "status": "uncertain",
                            **({"submission_error": add_error} if add_error else {}),
                        }
                    )
                    stage.update(
                        {
                            "_outcome": "uncertain",
                            "registration": registration,
                        }
                    )
                    raise _ManualReview(
                        "selected Gateway token registration outcome is uncertain"
                    )
                registration.update(
                    {
                        "status": "confirmed",
                        "mutation": True,
                        **({"submission_warning": add_error} if add_error else {}),
                    }
                )
                state["mutation_possible"] = False
            stage.update(
                {
                    "base": base,
                    "quote": quote_token,
                    "registration": registration,
                }
            )

        with trace.stage("attribution") as stage:
            if request.reason == "post_close_residual_cleanup":
                attribution = await _cleanup_attribution(client, scope, request)
            elif request.reason == "inventory_restoration":
                attribution = _restoration_attribution(store, request)
                await _ensure_no_owned_pool_executor(client, scope, request)
            else:
                attribution = {
                    "pool_address": request.pool_address,
                    "plan_digest": request.plan_digest,
                    "amount_quote": format(request.amount_quote, "f"),
                    "range_half_width_pct": format(request.range_half_width_pct, "f"),
                    "existing_base_amount": (
                        format(request.attributed_base_amount, "f")
                        if request.attributed_base_amount is not None
                        else "0"
                    ),
                }
            stage.update(attribution)

        with trace.stage("balance_snapshot") as stage:
            balances = await refresh_balances(scope, client)
            sol_available = _balance(balances, symbol="SOL")
            base_available = _balance(
                balances,
                symbol=request.base_symbol,
                mint=request.base_mint,
                missing_exact_is_zero=True,
            )
            quote_available = _balance(
                balances, symbol=scope.quote_symbol, mint=scope.quote_mint
            )
            stage.update(
                {
                    "sol_available": sol_available,
                    "base_available": base_available,
                    "quote_available": quote_available,
                }
            )

        minimum_reserve = decimal_config(scope.config, "min_sol_reserve", positive=True)
        existing_base = request.attributed_base_amount or Decimal(0)
        if request.base_symbol.upper() == "SOL":
            required_sol = minimum_reserve + existing_base
            if sol_available < required_sol:
                raise _balance_error(
                    "SOL balance for reserve and authorized LP base",
                    required=required_sol,
                    available=sol_available,
                )
        else:
            if sol_available < minimum_reserve:
                raise _balance_error(
                    "SOL reserve balance",
                    required=minimum_reserve,
                    available=sol_available,
                )
            if base_available < existing_base:
                raise _balance_error(
                    f"{request.base_symbol} balance for authorized LP base",
                    required=existing_base,
                    available=base_available,
                )

        if request.reason == "inventory_preparation" and request.amount == 0:
            required_quote = _decimal(
                request.plan["inventory"]["quote_amount"],
                "LP quote leg",
                positive=True,
            )
            if quote_available < required_quote:
                raise _balance_error(
                    f"{scope.quote_symbol} balance for LP quote leg",
                    required=required_quote,
                    available=quote_available,
                )
            allocation = {
                "source": "existing_wallet_balance",
                "attributed_base_amount": format(existing_base, "f"),
                "required_base_amount": str(request.plan["inventory"]["base_amount"]),
                "available_base_amount": format(base_available, "f"),
                "minimum_sol_reserve": format(minimum_reserve, "f"),
                "required_quote_amount": format(required_quote, "f"),
                "available_quote_amount": format(quote_available, "f"),
            }
            deployment_input = _deployment_input(request)
            if scope.execution_mode == "dry_run":
                return {
                    "status": "not_submitted",
                    "mutation": False,
                    "mutation_classification": "rejected_before_submit",
                    "retry_allowed": False,
                    "reason": (
                        "dry run observed sufficient authorized existing base; "
                        "no preparation swap would be submitted"
                    ),
                    "inventory_allocation": allocation,
                    "proposed_deployment_input": deployment_input,
                    "same_tick_lp_create_allowed": False,
                    "capacity_release_allowed": False,
                }, scope
            with trace.stage("existing_inventory_admission") as stage:
                store.admit_preparation(identity)
                record = store.write(
                    identity,
                    phase="confirmed",
                    mutation_possible=False,
                    result={
                        "attribution": attribution,
                        "inventory_allocation": allocation,
                        "deployment_input": deployment_input,
                        "confirmed_tick": scope.current_tick,
                        "same_tick_lp_create_allowed": True,
                    },
                    create_only=existing is None,
                )
                stage.update(
                    {
                        "phase": "confirmed",
                        "mutation": False,
                        "preparation_swap_skipped": True,
                    }
                )
            return {
                "status": "confirmed",
                "mutation": False,
                "mutation_classification": "confirmed",
                "retry_allowed": False,
                "inventory_allocation": allocation,
                "deployment_input": deployment_input,
                "same_tick_lp_create_allowed": True,
                "next_action": (
                    "invoke lp_executor_request with deployment_input and a new create "
                    "operation_id; no order executor is required"
                ),
                "capacity_release_allowed": False,
                "operation_state": _public_record(record),
            }, scope

        with trace.stage("quote") as stage:
            _, amount_in, amount_out = await _quote(client, scope, request)
            quote = {
                "input_amount": format(amount_in, "f"),
                "output_amount": format(amount_out, "f"),
            }
            stage.update(quote)

        with trace.stage("hard_guards") as stage:
            if request.side == "BUY":
                floor = request.amount * (
                    Decimal(1) - request.slippage_pct / Decimal(100)
                )
                if amount_in > request.max_quote_input:
                    raise ValueError(
                        "preparation quote input exceeds cap: "
                        f"required={format(amount_in, 'f')}, "
                        f"cap={format(request.max_quote_input, 'f')}"
                    )
                if amount_out < floor:
                    raise ValueError(
                        "preparation base output is below floor: "
                        f"required={format(floor, 'f')}, "
                        f"quoted={format(amount_out, 'f')}"
                    )
                future_quote = _decimal(
                    request.plan["inventory"]["quote_amount"],
                    "LP quote leg",
                    positive=True,
                )
                required_quote = amount_in + future_quote
                if quote_available < required_quote:
                    raise _balance_error(
                        (
                            f"{scope.quote_symbol} balance for preparation "
                            "input and LP quote leg"
                        ),
                        required=required_quote,
                        available=quote_available,
                    )
            else:
                if amount_in != request.amount:
                    raise ValueError(
                        "restoration input conflicts with exact attribution: "
                        f"required={format(request.amount, 'f')}, "
                        f"quoted={format(amount_in, 'f')}"
                    )
                if base_available < request.amount:
                    raise _balance_error(
                        f"{request.base_symbol} balance for restoration input",
                        required=request.amount,
                        available=base_available,
                    )
                if request.base_symbol.upper() == "SOL" and (
                    sol_available - request.amount < minimum_reserve
                ):
                    raise _balance_error(
                        "SOL balance for restoration input and reserve",
                        required=request.amount + minimum_reserve,
                        available=sol_available,
                    )
            dust = decimal_config(
                scope.config, "residual_base_dust_quote", non_negative=True
            )
            stage.update(
                {
                    "minimum_sol_reserve": minimum_reserve,
                    "economic_dust_quote": dust,
                    "quote_guard": "passed",
                }
            )
            if request.reason == "post_close_residual_cleanup" and amount_out <= dust:
                reason = (
                    "exact residual is below the configured economic-dust threshold"
                )
                record = None
                if scope.execution_mode != "dry_run":
                    record = store.write(
                        identity,
                        phase="rejected_before_submit",
                        mutation_possible=False,
                        result={"quote": quote, "attribution": attribution},
                        reason=reason,
                        create_only=existing is None,
                    )
                return {
                    "status": "not_submitted",
                    "mutation": False,
                    "mutation_classification": "rejected_before_submit",
                    "retry_allowed": False,
                    "reason": reason,
                    "quote": quote,
                    "attribution": attribution,
                    "capacity_release_allowed": True,
                    "operation_state": _public_record(record),
                }, scope

        with trace.stage("native_order_executor_preflight") as stage:
            prior_native_orders = [
                record
                for record in store.list_records("swap")
                if record.get("operation_id") != request.operation_id
                and record.get("tick") == scope.current_tick
                and record.get("mutation_possible") is True
                and isinstance(record.get("result"), dict)
                and record["result"].get("swap_executor_id")
            ]
            effective_executor_count = int(capacity["active_executors"]) + len(
                prior_native_orders
            )
            surface = await _validate_order_executor_surface(
                client,
                scope,
                request,
                effective_executor_count=effective_executor_count,
            )
            executor_request = surface["executor_request"]
            same_tick_lp_create_allowed = (
                request.reason != "inventory_preparation"
                or effective_executor_count + 2 <= surface["risk_executor_limit"]
            )
            if (
                request.reason == "inventory_preparation"
                and scope.execution_mode == "run_once"
                and not same_tick_lp_create_allowed
            ):
                raise ValueError(
                    "run-once preparation would require a later tick before LP create"
                )
            stage.update(
                {
                    "executor_type": "order_executor",
                    "swap_provider": surface["swap_provider"],
                    "executor_slippage_pct": surface["executor_slippage_pct"],
                    "max_slippage_pct": request.slippage_pct,
                    "native_risk_quote_limit": surface["risk_quote_limit"],
                    "effective_native_executor_count": effective_executor_count,
                    "same_tick_lp_create_allowed": same_tick_lp_create_allowed,
                }
            )

        if scope.execution_mode == "dry_run":
            return {
                "status": "not_submitted",
                "mutation": False,
                "mutation_classification": "rejected_before_submit",
                "retry_allowed": False,
                "reason": "dry run cannot submit a wallet mutation",
                "quote": quote,
                "attribution": attribution,
                "proposed_executor_request": executor_request,
                "same_tick_lp_create_allowed": same_tick_lp_create_allowed,
            }, scope

        with trace.stage("operation_admission") as stage:
            if request.reason == "inventory_preparation":
                store.admit_preparation(identity)
            record = store.write(
                identity,
                phase="admitted",
                mutation_possible=False,
                result={
                    "quote": quote,
                    "attribution": attribution,
                    "executor_request": executor_request,
                    "same_tick_lp_create_allowed": same_tick_lp_create_allowed,
                },
                create_only=existing is None,
            )
            stage["phase"] = record["phase"]
        return {
            "status": "ready",
            "mutation": False,
            "mutation_classification": "admitted",
            "retry_allowed": False,
            "quote": quote,
            "attribution": attribution,
            "executor_request": executor_request,
            "same_tick_lp_create_allowed": same_tick_lp_create_allowed,
            "next_action": (
                "call manage_executors once with executor_request, then invoke "
                "lp_order_request again with the returned swap_executor_id"
            ),
            "capacity_release_allowed": False,
            "operation_state": _public_record(record),
        }, scope


def _model_result(payload: dict[str, Any], config: Config) -> str:
    evidence_fields = (
        (
            "inventory_allocation",
            "deployment_input",
            "receipt",
            "executor_request",
            "proposed_executor_request",
            "proposed_deployment_input",
        )
        if config.reason in {None, "inventory_preparation"}
        else (
            "attribution",
            "receipt",
            "executor_request",
            "proposed_executor_request",
        )
    )
    compact = {
        "operation_id": config.operation_id,
        "controller_id": config.controller_id,
        **{
            key: payload[key]
            for key in (
                "status",
                "mutation",
                "mutation_classification",
                "retry_allowed",
                "swap_executor_id",
                *evidence_fields,
                "same_tick_lp_create_allowed",
                "capacity_release_allowed",
                "post_transaction_refresh",
                "capacity_release_reason",
                "token_registration",
                "proposed_token_registration",
                "configuration_mutation",
                "reason",
                "next_action",
                "report_id",
                "report_error",
            )
            if key in payload
        },
        "transport_complete": True,
    }
    if compact.get("reason"):
        compact["reason"] = reporting.safe_error(compact["reason"], limit=300)
    if compact.get("report_error"):
        compact["report_error"] = reporting.safe_error(
            compact["report_error"], limit=200
        )
    encoded = json.dumps(compact, default=str, separators=(",", ":"), sort_keys=True)
    if len(encoded) <= _TRANSPORT_MAX_CHARS:
        return encoded
    fallback = {
        "status": compact.get("status"),
        "operation_id": config.operation_id,
        "controller_id": config.controller_id,
        "mutation": bool(compact.get("mutation")),
        "mutation_classification": compact.get("mutation_classification"),
        "retry_allowed": False,
        "swap_executor_id": compact.get("swap_executor_id"),
        "capacity_release_allowed": False,
        "transport_complete": False,
        "reason": (
            "essential LP order result exceeded the safe model transport budget; "
            "HOLD and review the complete report"
        ),
        "report_id": compact.get("report_id"),
        "report_error": compact.get("report_error"),
    }
    return json.dumps(fallback, default=str, separators=(",", ":"), sort_keys=True)


async def run(config: Config, context: Any) -> str:
    """Register the selected token and prepare/reconcile without transferring."""

    trace = reporting.TraceRecorder()
    state: dict[str, Any] = {"mutation_possible": False}
    scope: RuntimeScope | None = None
    try:
        payload, scope = await _workflow(config, trace, state)
    except _ManualReview as exc:
        scope = state.get("scope")
        payload = {
            "status": "manual_review",
            "mutation": bool(state.get("mutation_possible")),
            "mutation_classification": "manual_review",
            "retry_allowed": False,
            "reason": reporting.safe_error(f"{type(exc).__name__}: {exc}"),
            "capacity_release_allowed": False,
        }
        store = state.get("store")
        identity = state.get("identity")
        if isinstance(store, ReceiptStore) and isinstance(identity, OperationIdentity):
            try:
                existing = store.read(identity)
                record = store.write(
                    identity,
                    phase="manual_review",
                    mutation_possible=bool(state.get("mutation_possible")),
                    reason=payload["reason"],
                    create_only=existing is None,
                )
                payload["operation_state"] = _public_record(record)
            except Exception as record_error:
                payload["operation_record_error"] = reporting.safe_error(
                    f"{type(record_error).__name__}: {record_error}"
                )
    except asyncio.CancelledError:
        scope = state.get("scope")
        mutation = bool(state.get("mutation_possible"))
        payload = {
            "status": "uncertain" if mutation else "rejected",
            "mutation": mutation,
            "mutation_classification": (
                "uncertain" if mutation else "rejected_before_submit"
            ),
            "retry_allowed": False,
            "reason": "CancelledError: LP order request handling was cancelled",
            "capacity_release_allowed": False,
        }
    except Exception as exc:
        scope = state.get("scope")
        mutation = bool(state.get("mutation_possible"))
        payload = {
            "status": "uncertain" if mutation else "rejected",
            "mutation": mutation,
            "mutation_classification": (
                "uncertain" if mutation else "rejected_before_submit"
            ),
            "retry_allowed": not mutation,
            "reason": reporting.safe_error(f"{type(exc).__name__}: {exc}"),
            "capacity_release_allowed": False,
        }

    request = state.get("request")
    registration = state.get("token_registration")
    if isinstance(registration, dict):
        payload.setdefault("token_registration", registration)
        payload.setdefault(
            "configuration_mutation",
            registration.get("status") == "confirmed"
            and registration.get("mutation") is True,
        )
    if (
        isinstance(request, SwapRequest)
        and request.reason == "post_close_residual_cleanup"
    ):
        payload.setdefault("capacity_release_allowed", False)

    if (
        isinstance(request, SwapRequest)
        and request.reason == "inventory_preparation"
        and request.plan is not None
    ):
        payload.setdefault("selection_plan", request.plan)

    routine_input = config.model_dump(mode="json")
    links = {
        "operation_id": config.operation_id,
        "attribution_operation_id": (
            request.attribution_operation_id
            if isinstance(request, SwapRequest)
            else config.attribution_operation_id
        ),
        "executor_id": (
            request.executor_id
            if isinstance(request, SwapRequest)
            else config.executor_id
        ),
        "swap_executor_id": (
            payload.get("swap_executor_id") or config.swap_executor_id
        ),
        "transaction_hash": (
            payload.get("receipt", {}).get("transaction_hash")
            if isinstance(payload.get("receipt"), dict)
            else None
        ),
    }
    payload = await reporting.attach_report(
        payload,
        title="LP Order Request Planning and Evidence",
        source="lp_order_request",
        version=VERSION,
        routine_input=routine_input,
        trace=trace,
        links=links,
        scope=scope,
    )
    return _model_result(payload, config)
