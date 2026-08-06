"""One exact, serialized Jupiter inventory transition for LP Expert."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from agents.lp_expert.core import reporting
from agents.lp_expert.core.portfolio import (
    NATIVE_SWAP_FAILED,
    NATIVE_SWAP_PENDING,
    NATIVE_SWAP_SUCCESS,
    exact_close_evidence,
    fetch_all_executors,
    normalize_executor,
)
from agents.lp_expert.core.receipts import (
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
    refresh_balances,
    resolve_runtime,
)

CATEGORY = "LP Inventory Transition"
VERSION = "1"

_CONFIRMED = {"CONFIRMED", "SUCCESS", "COMPLETED"}
_FAILED = {"FAILED", "ERROR", "REVERTED", "DROPPED"}


class Config(BaseModel):
    """Request one exact bounded inventory transition or recover the same ID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    controller_id: StrictStr
    operation_id: StrictStr = Field(min_length=8, max_length=128)
    reason: Literal[
        "inventory_preparation",
        "inventory_restoration",
        "post_close_residual_cleanup",
    ]
    pool_address: StrictStr
    base_symbol: StrictStr
    base_mint: StrictStr
    base_decimals: StrictInt = Field(ge=0, le=18)
    amount: Decimal = Field(gt=0)
    slippage_pct: Decimal = Field(gt=0, le=100)
    max_quote_input: Decimal | None = Field(default=None, gt=0)
    attributed_base_amount: Decimal | None = Field(default=None, gt=0)
    plan_digest: StrictStr | None = None
    attribution_operation_id: StrictStr | None = None
    executor_id: StrictStr | None = None
    position_id: StrictStr | None = None
    closed_tick: StrictInt | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def contract(self) -> "Config":
        strings = (
            self.controller_id,
            self.operation_id,
            self.pool_address,
            self.base_symbol,
            self.base_mint,
        )
        if not all(value and value == value.strip() for value in strings):
            raise ValueError("swap identity fields must be exact and non-empty")
        if not self.amount.is_finite() or not self.slippage_pct.is_finite():
            raise ValueError("swap amount and slippage must be finite")
        if self.base_mint == QUOTE_MINT or self.base_symbol.upper() == "USDC":
            raise ValueError("swap base must not be canonical USDC")
        if self.reason == "inventory_preparation":
            if (
                self.max_quote_input is None
                or self.attributed_base_amount is not None
                or self.attribution_operation_id is not None
                or self.executor_id is not None
                or self.position_id is not None
                or self.closed_tick is not None
                or not self.plan_digest
                or len(self.plan_digest) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in self.plan_digest
                )
            ):
                raise ValueError("inventory preparation identity is incomplete")
        elif self.reason == "inventory_restoration":
            if (
                self.max_quote_input is not None
                or self.attributed_base_amount != self.amount
                or not self.attribution_operation_id
                or self.executor_id is not None
                or self.position_id is not None
                or self.closed_tick is not None
                or self.plan_digest is not None
            ):
                raise ValueError("inventory restoration attribution is incomplete")
        elif (
            self.max_quote_input is not None
            or self.attributed_base_amount != self.amount
            or self.attribution_operation_id is not None
            or not self.executor_id
            or not self.position_id
            or self.closed_tick is None
            or self.plan_digest is not None
        ):
            raise ValueError("post-close cleanup attribution is incomplete")
        return self

    @property
    def side(self) -> Literal["BUY", "SELL"]:
        return "BUY" if self.reason == "inventory_preparation" else "SELL"

    @property
    def trading_pair(self) -> str:
        return f"{self.base_symbol}-USDC"


# Agent-local routines are loaded from file without module registration.
Config.model_rebuild(
    _types_namespace={
        "StrictStr": StrictStr,
        "StrictInt": StrictInt,
        "Literal": Literal,
        "Decimal": Decimal,
    }
)


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


def _require_registered_token(
    rows: list[dict[str, Any]], *, address: str, symbol: str, decimals: int
) -> dict[str, Any]:
    exact = [row for row in rows if _token_address(row) == address]
    if len(exact) != 1:
        raise ValueError(f"registered token identity for {symbol} is not unique")
    row = exact[0]
    found_symbol = str(row.get("symbol") or "").strip()
    found_decimals = row.get("decimals")
    if (
        found_symbol.casefold() != symbol.casefold()
        or isinstance(found_decimals, bool)
        or int(found_decimals) != decimals
    ):
        raise ValueError(f"registered token metadata for {symbol} conflicts")
    collisions = [
        item
        for item in rows
        if str(item.get("symbol") or "").strip().casefold() == symbol.casefold()
        and _token_address(item) != address
    ]
    if collisions:
        raise ValueError(f"registered token symbol {symbol} is ambiguous")
    return {"address": address, "symbol": found_symbol, "decimals": decimals}


def _balance(
    rows: list[dict[str, Any]], *, symbol: str, mint: str | None = None
) -> Decimal:
    found: list[Decimal] = []
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
        if (mint and row_mint == mint) or (
            not mint and row_symbol.casefold() == symbol.casefold()
        ):
            found.append(_decimal(amount, f"{symbol} available balance"))
    if mint and not found and not any_mint:
        return _balance(rows, symbol=symbol)
    if len(found) != 1 or found[0] < 0:
        raise ValueError(f"{symbol} balance is not uniquely scoped")
    return found[0]


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
    client: Any, scope: RuntimeScope, config: Config
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


def _receipt(
    value: Any,
    config: Config,
    scope: RuntimeScope,
    *,
    require_identity: bool,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Gateway swap receipt is invalid")
    transaction_hash = _find(value, "transaction_hash", "tx_hash", "signature", "hash")
    if not isinstance(transaction_hash, str) or not transaction_hash.strip():
        raise ValueError("Gateway swap transaction hash is unavailable")
    observed = {
        "connector": _find(value, "connector"),
        "network": _find(value, "network"),
        "wallet_address": _find(value, "wallet_address", "walletAddress"),
        "trading_pair": _find(value, "trading_pair", "tradingPair"),
        "side": _find(value, "side"),
    }
    expected = {
        "connector": scope.swap_connector,
        "network": scope.network,
        "wallet_address": scope.wallet_address,
        "trading_pair": config.trading_pair,
        "side": config.side,
    }
    for key, expected_value in expected.items():
        found = observed[key]
        if require_identity and found in (None, ""):
            raise ValueError(f"Gateway history lacks exact {key} identity")
        if (
            found not in (None, "")
            and str(found).casefold() != str(expected_value).casefold()
        ):
            raise ValueError(f"Gateway swap receipt {key} conflicts")
    input_amount = _find(value, "input_amount", "amount_in", "amountIn")
    output_amount = _find(value, "output_amount", "amount_out", "amountOut")
    timestamp = _find(value, "timestamp", "created_at", "submitted_at")
    return {
        "transaction_hash": transaction_hash.strip(),
        "status": str(_find(value, "status") or "UNKNOWN").upper(),
        "input_amount": (
            format(_decimal(input_amount, "receipt input", positive=True), "f")
            if input_amount not in (None, "")
            else None
        ),
        "output_amount": (
            format(_decimal(output_amount, "receipt output", positive=True), "f")
            if output_amount not in (None, "")
            else None
        ),
        "timestamp": timestamp,
        **{
            key: str(found)
            for key, found in observed.items()
            if found not in (None, "")
        },
    }


def _receipt_issue(receipt: dict[str, Any], config: Config) -> str | None:
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
    config: Config,
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
            f"Gateway reported terminal swap status {status}",
        )
    return "pending", "submitted", False, None


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("operation timestamp is unavailable")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("operation timestamp is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def _history_rows(value: Any) -> list[dict[str, Any]]:
    rows = (
        value
        if isinstance(value, list)
        else (
            value.get("data", value.get("results")) if isinstance(value, dict) else None
        )
    )
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Gateway swap history is unavailable")
    if len(rows) >= 1000:
        raise ValueError("Gateway swap history may be truncated")
    return rows


async def _search_history(
    client: Any,
    scope: RuntimeScope,
    config: Config,
    record: dict[str, Any],
) -> list[dict[str, Any]]:
    attempted_at = _parse_time(record.get("created_at"))
    value = await client.gateway_swap.search_swaps(
        network=scope.network,
        connector=scope.swap_connector,
        wallet_address=scope.wallet_address,
        trading_pair=config.trading_pair,
        start_time=int((attempted_at - timedelta(seconds=15)).timestamp()),
        end_time=int((attempted_at + timedelta(seconds=120)).timestamp()),
        limit=1000,
        offset=0,
    )
    matches = []
    for row in _history_rows(value):
        try:
            receipt = _receipt(row, config, scope, require_identity=True)
            timestamp = _parse_time(receipt.get("timestamp"))
        except (TypeError, ValueError):
            continue
        if not (
            attempted_at - timedelta(seconds=15)
            <= timestamp
            <= attempted_at + timedelta(seconds=120)
        ):
            continue
        if _receipt_issue(receipt, config) is None:
            matches.append(receipt)
    hashes = {item["transaction_hash"] for item in matches}
    if len(hashes) != len(matches):
        raise ValueError("Gateway history repeated a transaction identity")
    return matches


async def _refresh_receipt(
    client: Any,
    scope: RuntimeScope,
    config: Config,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    value = await client.gateway_swap.get_swap_status(receipt["transaction_hash"])
    observed = _receipt(value, config, scope, require_identity=False)
    if observed["transaction_hash"] != receipt["transaction_hash"]:
        raise ValueError("Gateway status transaction hash conflicts")
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
    config: Config,
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
    config: Config,
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


def _restoration_attribution(store: ReceiptStore, config: Config) -> dict[str, Any]:
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
    client: Any, scope: RuntimeScope, config: Config
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


def _intent(config: Config) -> dict[str, Any]:
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
        "attribution_operation_id": config.attribution_operation_id,
        "executor_id": config.executor_id,
        "position_id": config.position_id,
        "closed_tick": config.closed_tick,
    }


async def _recover(
    client: Any,
    scope: RuntimeScope,
    config: Config,
    store: ReceiptStore,
    identity: OperationIdentity,
    record: dict[str, Any],
    trace: reporting.TraceRecorder,
) -> dict[str, Any] | None:
    phase = str(record["phase"])
    if phase == "rejected_before_submit":
        return None
    if phase == "admitted":
        return None
    if phase == "confirmed":
        receipt = _record_receipt(record)
        release, diagnostic, refresh_error = await _cleanup_post_transaction_refresh(
            client, scope, config, receipt or {}, trace
        )
        return {
            "status": "confirmed",
            "mutation": True,
            "mutation_classification": "confirmed",
            "retry_allowed": False,
            "operation_state": _public_record(record),
            "receipt": receipt,
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
    receipt = _record_receipt(record)
    recovery_source = None
    if receipt and receipt.get("transaction_hash"):
        with trace.stage("transaction_status_refresh") as stage:
            try:
                receipt = await _refresh_receipt(client, scope, config, receipt)
                recovery_source = "transaction_hash"
                stage["transaction_hash"] = receipt["transaction_hash"]
                stage["status"] = receipt["status"]
            except Exception as exc:
                stage["last_confirmed_transaction_hash"] = receipt["transaction_hash"]
                stage["_outcome"] = "unavailable"
                return {
                    "status": "pending",
                    "mutation": True,
                    "mutation_classification": "submitted",
                    "retry_allowed": False,
                    "reason": reporting.safe_error(
                        f"status refresh unavailable: {type(exc).__name__}: {exc}"
                    ),
                    "operation_state": _public_record(record),
                    "receipt": receipt,
                }
    else:
        with trace.stage("exact_history_recovery") as stage:
            try:
                matches = await _search_history(client, scope, config, record)
            except Exception as exc:
                reason = reporting.safe_error(
                    f"Gateway history reconciliation failed: {type(exc).__name__}: {exc}"
                )
                updated = store.write(
                    identity,
                    phase="uncertain",
                    mutation_possible=True,
                    reason=reason,
                )
                stage["_outcome"] = "unavailable"
                stage["matches"] = None
                return {
                    "status": "uncertain",
                    "mutation": True,
                    "mutation_classification": "uncertain",
                    "retry_allowed": False,
                    "reason": reason,
                    "operation_state": _public_record(updated),
                }
            stage["matches"] = len(matches)
            if len(matches) != 1:
                phase = "uncertain" if not matches else "manual_review"
                reason = (
                    "no exact operation swap was found; submission remains uncertain"
                    if not matches
                    else "multiple exact operation swaps matched Gateway history"
                )
                updated = store.write(
                    identity,
                    phase=phase,
                    mutation_possible=True,
                    reason=reason,
                )
                return {
                    "status": "uncertain" if not matches else "manual_review",
                    "mutation": True,
                    "mutation_classification": phase,
                    "retry_allowed": False,
                    "reason": reason,
                    "operation_state": _public_record(updated),
                }
            receipt = matches[0]
            recovery_source = "gateway_history"

    status, next_phase, retry_allowed, reason = _outcome_from_receipt(receipt, config)
    updated = store.write(
        identity,
        phase=next_phase,
        mutation_possible=True,
        result={"receipt": receipt, "recovery_source": recovery_source},
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
        "mutation_classification": next_phase,
        "retry_allowed": retry_allowed,
        **({"reason": reason} if reason else {}),
        "operation_state": _public_record(updated),
        "receipt": receipt,
        "recovery_source": recovery_source,
        "capacity_release_allowed": release,
        **({"post_transaction_refresh": diagnostic} if diagnostic is not None else {}),
        **(
            {"capacity_release_reason": refresh_error}
            if refresh_error is not None
            else {}
        ),
    }


async def _workflow(
    config: Config,
    trace: reporting.TraceRecorder,
    state: dict[str, Any],
) -> tuple[dict[str, Any], RuntimeScope]:
    with trace.stage("runtime_resolution") as stage:
        scope = resolve_runtime(config.controller_id)
        state["scope"] = scope
        stage.update(
            {
                "controller_id": scope.controller_id,
                "strategy": scope.strategy_slug,
                "execution_mode": scope.execution_mode,
                "current_tick": scope.current_tick,
            }
        )
        if config.slippage_pct > decimal_config(
            scope.config, "max_slippage_pct", positive=True
        ):
            raise ValueError("requested slippage exceeds frozen Strategy limit")
        if (
            config.reason == "inventory_preparation"
            and config.max_quote_input
            > decimal_config(scope.config, "max_quote_per_executor", positive=True)
        ):
            raise ValueError(
                "preparation quote cap exceeds the frozen per-executor limit"
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
    identity = store.identity(
        operation_id=config.operation_id,
        operation_kind="swap",
        intent=_intent(config),
    )
    state.update({"store": store, "identity": identity})

    async with controller_mutation_lock(scope.controller_id):
        with trace.stage("prior_receipt_lookup") as stage:
            try:
                existing = store.read(identity)
            except ValueError as exc:
                if store.read_by_id(config.operation_id) is not None:
                    raise _ManualReview(
                        "existing swap operation identity conflicts with this request"
                    ) from exc
                raise
            stage["phase"] = existing.get("phase") if existing else "absent"
        if existing is not None:
            recovered = await _recover(
                client, scope, config, store, identity, existing, trace
            )
            if recovered is not None:
                return recovered, scope

        conflicts = [
            record
            for record in store.unresolved_operations()
            if record.get("operation_id") != config.operation_id
        ]
        if conflicts:
            raise _ManualReview(
                "current-session operation "
                f"{conflicts[0]['operation_id']} remains unresolved"
            )

        with trace.stage("token_registry") as stage:
            tokens = _token_rows(await client.gateway.get_network_tokens(scope.network))
            base = _require_registered_token(
                tokens,
                address=config.base_mint,
                symbol=config.base_symbol,
                decimals=config.base_decimals,
            )
            quote_token = _require_registered_token(
                tokens,
                address=scope.quote_mint,
                symbol=scope.quote_symbol,
                decimals=scope.quote_decimals,
            )
            stage.update({"base": base, "quote": quote_token})

        with trace.stage("attribution") as stage:
            if config.reason == "post_close_residual_cleanup":
                attribution = await _cleanup_attribution(client, scope, config)
            elif config.reason == "inventory_restoration":
                attribution = _restoration_attribution(store, config)
                await _ensure_no_owned_pool_executor(client, scope, config)
            else:
                attribution = {
                    "pool_address": config.pool_address,
                    "plan_digest": config.plan_digest,
                }
            stage.update(attribution)

        with trace.stage("balance_snapshot") as stage:
            balances = await refresh_balances(scope, client)
            sol_available = _balance(balances, symbol="SOL")
            base_available = _balance(
                balances, symbol=config.base_symbol, mint=config.base_mint
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

        with trace.stage("quote") as stage:
            _, amount_in, amount_out = await _quote(client, scope, config)
            quote = {
                "input_amount": format(amount_in, "f"),
                "output_amount": format(amount_out, "f"),
            }
            stage.update(quote)

        with trace.stage("hard_guards") as stage:
            minimum_reserve = decimal_config(
                scope.config, "min_sol_reserve", positive=True
            )
            if sol_available < minimum_reserve:
                raise ValueError("current SOL balance is below the required reserve")
            if config.side == "BUY":
                floor = config.amount * (
                    Decimal(1) - config.slippage_pct / Decimal(100)
                )
                if (
                    amount_in > config.max_quote_input
                    or amount_out < floor
                    or quote_available < amount_in
                ):
                    raise ValueError("preparation quote, cap, or balance guard failed")
            else:
                if amount_in != config.amount or base_available < config.amount:
                    raise ValueError("restoration attribution or balance guard failed")
                if config.base_symbol.upper() == "SOL" and (
                    sol_available - config.amount < minimum_reserve
                ):
                    raise ValueError("SELL would violate the required SOL reserve")
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
            if config.reason == "post_close_residual_cleanup" and amount_out <= dust:
                record = store.write(
                    identity,
                    phase="rejected_before_submit",
                    mutation_possible=False,
                    result={"quote": quote, "attribution": attribution},
                    reason="exact residual is below the configured economic-dust threshold",
                    create_only=existing is None,
                )
                return {
                    "status": "not_submitted",
                    "mutation": False,
                    "mutation_classification": "rejected_before_submit",
                    "retry_allowed": False,
                    "reason": record["reason"],
                    "quote": quote,
                    "attribution": attribution,
                    "capacity_release_allowed": True,
                    "operation_state": _public_record(record),
                }, scope

        if scope.execution_mode == "dry_run":
            record = store.write(
                identity,
                phase="rejected_before_submit",
                mutation_possible=False,
                result={"quote": quote, "attribution": attribution},
                reason="dry run cannot submit a wallet mutation",
                create_only=existing is None,
            )
            return {
                "status": "not_submitted",
                "mutation": False,
                "mutation_classification": "rejected_before_submit",
                "retry_allowed": False,
                "reason": record["reason"],
                "quote": quote,
                "attribution": attribution,
                "operation_state": _public_record(record),
            }, scope

        with trace.stage("operation_admission") as stage:
            if config.reason == "inventory_preparation":
                store.admit_preparation(identity)
            record = store.write(
                identity,
                phase="admitted",
                mutation_possible=False,
                result={"quote": quote, "attribution": attribution},
                create_only=existing is None,
            )
            stage["phase"] = record["phase"]

        with trace.stage("submission") as stage:
            record = store.write(
                identity,
                phase="submitting",
                mutation_possible=True,
                result={"quote": quote, "attribution": attribution},
            )
            state["mutation_possible"] = True
            task = asyncio.create_task(
                client.gateway_swap.execute_swap(
                    connector=scope.swap_connector,
                    network=scope.network,
                    trading_pair=config.trading_pair,
                    side=config.side,
                    amount=config.amount,
                    slippage_pct=config.slippage_pct,
                    wallet_address=scope.wallet_address,
                )
            )
            try:
                raw = await asyncio.shield(task)
            except asyncio.CancelledError:
                reason = "swap submission wait was cancelled"
                updated = store.write(
                    identity,
                    phase="uncertain",
                    mutation_possible=True,
                    reason=reason,
                )
                stage["_outcome"] = "uncertain"
                return {
                    "status": "uncertain",
                    "mutation": True,
                    "mutation_classification": "uncertain",
                    "retry_allowed": False,
                    "reason": reason,
                    "quote": quote,
                    "operation_state": _public_record(updated),
                }, scope
            except Exception as exc:
                reason = reporting.safe_error(f"{type(exc).__name__}: {exc}")
                updated = store.write(
                    identity,
                    phase="uncertain",
                    mutation_possible=True,
                    reason=reason,
                )
                stage["_outcome"] = "uncertain"
                return {
                    "status": "uncertain",
                    "mutation": True,
                    "mutation_classification": "uncertain",
                    "retry_allowed": False,
                    "reason": reason,
                    "quote": quote,
                    "operation_state": _public_record(updated),
                }, scope
            try:
                receipt = _receipt(raw, config, scope, require_identity=False)
            except Exception as exc:
                reason = reporting.safe_error(
                    f"submitted swap receipt is invalid: {type(exc).__name__}: {exc}"
                )
                updated = store.write(
                    identity,
                    phase="uncertain",
                    mutation_possible=True,
                    reason=reason,
                )
                stage["_outcome"] = "uncertain"
                return {
                    "status": "uncertain",
                    "mutation": True,
                    "mutation_classification": "uncertain",
                    "retry_allowed": False,
                    "reason": reason,
                    "quote": quote,
                    "operation_state": _public_record(updated),
                }, scope
            record = store.write(
                identity,
                phase="submitted",
                mutation_possible=True,
                result={
                    "quote": quote,
                    "attribution": attribution,
                    "receipt": receipt,
                },
            )
            stage.update(
                {
                    "transaction_hash": receipt["transaction_hash"],
                    "status": receipt["status"],
                }
            )

        if (
            receipt["status"] not in _CONFIRMED | _FAILED
            or receipt.get("input_amount") is None
            or receipt.get("output_amount") is None
        ):
            with trace.stage("transaction_status_refresh") as stage:
                try:
                    receipt = await _refresh_receipt(client, scope, config, receipt)
                    stage.update(
                        {
                            "transaction_hash": receipt["transaction_hash"],
                            "status": receipt["status"],
                        }
                    )
                except Exception as exc:
                    stage["_outcome"] = "unavailable"
                    return {
                        "status": "pending",
                        "mutation": True,
                        "mutation_classification": "submitted",
                        "retry_allowed": False,
                        "reason": reporting.safe_error(
                            f"status refresh unavailable: {type(exc).__name__}: {exc}"
                        ),
                        "quote": quote,
                        "receipt": receipt,
                        "operation_state": _public_record(record),
                    }, scope

        status, phase, retry_allowed, reason = _outcome_from_receipt(receipt, config)
        record = store.write(
            identity,
            phase=phase,
            mutation_possible=True,
            result={
                "quote": quote,
                "attribution": attribution,
                "receipt": receipt,
            },
            reason=reason,
        )
        release = False
        diagnostic = None
        refresh_error = None
        if status == "confirmed":
            release, diagnostic, refresh_error = (
                await _cleanup_post_transaction_refresh(
                    client, scope, config, receipt, trace
                )
            )
        with trace.stage("final_classification") as stage:
            stage.update(
                {
                    "status": status,
                    "phase": phase,
                    "retry_allowed": retry_allowed,
                    "transaction_hash": receipt["transaction_hash"],
                }
            )
        return {
            "status": status,
            "mutation": True,
            "mutation_classification": phase,
            "retry_allowed": retry_allowed,
            **({"reason": reason} if reason else {}),
            "quote": quote,
            "attribution": attribution,
            "receipt": receipt,
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
            "operation_state": _public_record(record),
        }, scope


async def run(config: Config, context: Any) -> str:
    """Execute or reconcile one operation and emit exactly one review report."""

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
            "reason": "CancelledError: LP inventory transition was cancelled",
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

    if config.reason == "post_close_residual_cleanup":
        payload.setdefault("capacity_release_allowed", False)

    routine_input = config.model_dump(mode="json")
    links = {
        "operation_id": config.operation_id,
        "attribution_operation_id": config.attribution_operation_id,
        "executor_id": config.executor_id,
        "transaction_hash": (
            payload.get("receipt", {}).get("transaction_hash")
            if isinstance(payload.get("receipt"), dict)
            else None
        ),
    }
    payload = await reporting.attach_report(
        payload,
        title="LP Inventory Transition",
        source="lp_swap",
        version=VERSION,
        routine_input=routine_input,
        trace=trace,
        links=links,
        scope=scope,
    )
    return json.dumps(payload, default=str, separators=(",", ":"), sort_keys=True)
