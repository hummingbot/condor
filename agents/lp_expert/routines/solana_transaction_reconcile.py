"""Read-only finalized Solana reconciliation for one uncertain LP operation."""

import asyncio
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit

import aiohttp
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from agents.lp_expert.routines import _routine_report as reports

CATEGORY = "Solana Transaction Reconciliation"
CONTROLLER_RE = re.compile(r"^lp_expert\.[a-z][a-z0-9_-]*_(?:e)?[1-9]\d*$")
BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,128}$")
NATIVE_ASSET = "native"


class AssetChange(BaseModel):
    """One bounded wallet-owned asset delta required for an exact match."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    mint: StrictStr
    decimals: StrictInt = Field(ge=0, le=18)
    direction: Literal["increase", "decrease"]
    minimum_amount: Decimal = Field(gt=0)
    maximum_amount: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def bounds(self) -> "AssetChange":
        scale = Decimal(10) ** self.decimals
        values = (self.minimum_amount, self.maximum_amount)
        if (
            (self.mint != NATIVE_ASSET and not BASE58_RE.fullmatch(self.mint))
            or (self.mint == NATIVE_ASSET and self.decimals != 9)
            or not all(value.is_finite() and value > 0 for value in values)
            or self.minimum_amount > self.maximum_amount
            or any(
                value * scale != (value * scale).to_integral_value() for value in values
            )
        ):
            raise ValueError("asset change bounds are invalid")
        return self


class Config(BaseModel):
    """Find one finalized transaction matching an exact uncertain operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    controller_id: StrictStr
    operation_id: StrictStr = Field(min_length=8, max_length=128)
    account_name: StrictStr
    network: StrictStr
    wallet_address: StrictStr
    operation_kind: Literal["swap", "lp_open", "lp_close"]
    window_start: datetime
    window_end: datetime
    transaction_hash: StrictStr | None = None
    required_accounts: tuple[StrictStr, ...] = Field(min_length=1, max_length=16)
    required_program_ids: tuple[StrictStr, ...] = Field(min_length=1, max_length=8)
    asset_changes: tuple[AssetChange, ...] = Field(min_length=1, max_length=8)
    signature_limit: StrictInt = Field(default=100, ge=1, le=1000)
    rpc_timeout_seconds: StrictInt = Field(default=15, ge=1, le=30)

    @model_validator(mode="after")
    def identity(self) -> "Config":
        if self.operation_kind == "swap" and len(self.asset_changes) < 2:
            raise ValueError("swap matching requires input and output asset changes")
        addresses = (
            self.wallet_address,
            *self.required_accounts,
            *self.required_program_ids,
            *(
                change.mint
                for change in self.asset_changes
                if change.mint != NATIVE_ASSET
            ),
        )
        if (
            not CONTROLLER_RE.fullmatch(self.controller_id)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", self.operation_id)
            or not re.fullmatch(r"solana-[a-z0-9-]+", self.network)
            or not all(BASE58_RE.fullmatch(value) for value in addresses)
            or (
                self.transaction_hash is not None
                and not BASE58_RE.fullmatch(self.transaction_hash)
            )
            or self.window_start.tzinfo is None
            or self.window_end.tzinfo is None
            or not 0 < (self.window_end - self.window_start).total_seconds() <= 900
            or len(set(self.required_accounts)) != len(self.required_accounts)
            or len(set(self.required_program_ids)) != len(self.required_program_ids)
            or len({change.mint for change in self.asset_changes})
            != len(self.asset_changes)
        ):
            raise ValueError("Solana operation match contract is invalid")
        return self


def _find_value(value: Any, key: str) -> Any:
    if not isinstance(value, dict):
        return None
    for current_key, current_value in value.items():
        if str(current_key).casefold() == key.casefold():
            return current_value
    for current_value in value.values():
        found = _find_value(current_value, key)
        if found is not None:
            return found
    return None


async def _scope(config: Config) -> tuple[Any, str]:
    from agents.lp_expert.routines import gateway_swap

    scope = gateway_swap.Config(
        action="scope",
        controller_id=config.controller_id,
        account_name=config.account_name,
        network=config.network,
        wallet_address=config.wallet_address,
    )
    client, binding, _ = await gateway_swap._scope(scope)
    if binding["wallet_address"] != config.wallet_address:
        raise ValueError("current network default wallet binding failed")
    network = await client.gateway.get_network_config(config.network)
    rpc_url = _find_value(network, "nodeURL") or _find_value(network, "node_url")
    parsed = urlsplit(str(rpc_url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("configured Solana RPC endpoint is unavailable")
    return client, str(rpc_url)


async def _rpc(
    session: aiohttp.ClientSession, rpc_url: str, method: str, params: list[Any]
) -> Any:
    async with session.post(
        rpc_url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    ) as response:
        response.raise_for_status()
        value = await response.json(content_type=None)
    if not isinstance(value, dict) or value.get("error") is not None:
        raise ValueError(f"Solana RPC {method} returned an error")
    return value.get("result")


def _accounts(value: dict[str, Any]) -> tuple[list[str], set[str]]:
    transaction = value.get("transaction")
    message = transaction.get("message") if isinstance(transaction, dict) else None
    rows = message.get("accountKeys") if isinstance(message, dict) else None
    if not isinstance(rows, list) or not all(
        isinstance(row, dict) and isinstance(row.get("pubkey"), str) for row in rows
    ):
        raise ValueError("jsonParsed transaction account keys are unavailable")
    keys = [row["pubkey"] for row in rows]
    signers = {row["pubkey"] for row in rows if row.get("signer") is True}
    return keys, signers


def _programs(value: dict[str, Any]) -> set[str]:
    transaction = value["transaction"]
    message = transaction["message"]
    groups = [message.get("instructions")]
    meta = value.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("innerInstructions"), list):
        groups.extend(
            row.get("instructions")
            for row in meta["innerInstructions"]
            if isinstance(row, dict)
        )
    return {
        row["programId"]
        for rows in groups
        if isinstance(rows, list)
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("programId"), str)
    }


def _token_balances(
    rows: Any, wallet: str, expected: dict[str, AssetChange]
) -> dict[str, int]:
    if not isinstance(rows, list):
        raise ValueError("transaction token balances are unavailable")
    balances = {mint: 0 for mint in expected}
    for row in rows:
        if not isinstance(row, dict) or row.get("owner") != wallet:
            continue
        mint = row.get("mint")
        if mint not in expected:
            continue
        amount = row.get("uiTokenAmount")
        if (
            not isinstance(amount, dict)
            or amount.get("decimals") != expected[mint].decimals
        ):
            raise ValueError("wallet token balance decimals are inconsistent")
        balances[mint] += int(amount["amount"])
    return balances


def _match(
    signature: str, value: Any, config: Config, start: int, end: int
) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("blockTime"), int):
        raise ValueError("finalized transaction detail is unavailable")
    meta = value.get("meta")
    if (
        not start <= value["blockTime"] <= end
        or not isinstance(meta, dict)
        or meta.get("err")
    ):
        return None
    keys, signers = _accounts(value)
    signatures = value["transaction"].get("signatures")
    if (
        config.wallet_address not in signers
        or not keys
        or keys[0] != config.wallet_address
        or not isinstance(signatures, list)
        or signature not in signatures
        or not set(config.required_accounts) <= set(keys)
        or not set(config.required_program_ids) <= _programs(value)
    ):
        return None
    expected = {
        change.mint: change
        for change in config.asset_changes
        if change.mint != NATIVE_ASSET
    }
    before = _token_balances(
        meta.get("preTokenBalances"), config.wallet_address, expected
    )
    after = _token_balances(
        meta.get("postTokenBalances"), config.wallet_address, expected
    )
    deltas = {mint: after[mint] - before[mint] for mint in expected}
    if NATIVE_ASSET in {change.mint for change in config.asset_changes}:
        indexes = [
            index for index, key in enumerate(keys) if key == config.wallet_address
        ]
        pre, post = meta.get("preBalances"), meta.get("postBalances")
        if (
            len(indexes) != 1
            or not isinstance(pre, list)
            or not isinstance(post, list)
            or indexes[0] >= len(pre)
            or indexes[0] >= len(post)
        ):
            raise ValueError("wallet native balance evidence is unavailable")
        deltas[NATIVE_ASSET] = int(post[indexes[0]]) - int(pre[indexes[0]])
    observed = []
    for change in config.asset_changes:
        delta = deltas.get(change.mint, 0)
        magnitude = delta if change.direction == "increase" else -delta
        scale = Decimal(10) ** change.decimals
        if (
            not int(change.minimum_amount * scale)
            <= magnitude
            <= int(change.maximum_amount * scale)
        ):
            return None
        observed.append(
            {
                "mint": change.mint,
                "direction": change.direction,
                "amount": format(Decimal(magnitude) / scale, "f"),
                "raw_delta": str(delta),
            }
        )
    return {
        "transaction_hash": signature,
        "slot": value.get("slot"),
        "block_time": value["blockTime"],
        "confirmation_status": "finalized",
        "matched_accounts": list(config.required_accounts),
        "matched_program_ids": list(config.required_program_ids),
        "asset_changes": observed,
    }


async def _response(
    status: str, config: Config, *, matches: list[dict[str, Any]], **details: Any
) -> str:
    payload = {
        "status": status,
        "operation_id": config.operation_id,
        "operation_kind": config.operation_kind,
        "controller_id": config.controller_id,
        "confirmation_source": "solana_rpc",
        "mutation": False,
        "retry_allowed": False,
        "matches": matches,
        **details,
    }
    evidence = [
        {
            "kind": "transaction",
            "transaction_hash": row["transaction_hash"],
            "slot": row["slot"],
            "block_time": row["block_time"],
            "confirmation_status": row["confirmation_status"],
        }
        for row in matches
    ]
    evidence.extend(
        {"kind": "asset_change", "transaction_hash": row["transaction_hash"], **asset}
        for row in matches
        for asset in row["asset_changes"]
    )
    if details.get("reason"):
        evidence.append({"kind": "error", "reason": details["reason"]})
    payload = await reports.attach_trace(
        payload,
        title="LP Expert Solana Transaction Reconciliation",
        source="solana_transaction_reconcile",
        status=status,
        summary={
            "operation_id": config.operation_id,
            "operation_kind": config.operation_kind,
            "controller_id": config.controller_id,
            "network": config.network,
            "match_count": len(matches),
            "mutation": False,
        },
        evidence=evidence,
    )
    return json.dumps(payload, default=str, separators=(",", ":"), sort_keys=True)


async def run(config: Config, context: Any) -> str:
    """Inspect finalized chain effects; never submit or retry a transaction."""
    try:
        _, rpc_url = await _scope(config)
        start = int(config.window_start.astimezone(timezone.utc).timestamp())
        end = int(config.window_end.astimezone(timezone.utc).timestamp())
        timeout = aiohttp.ClientTimeout(total=config.rpc_timeout_seconds)
        async with (
            asyncio.timeout(config.rpc_timeout_seconds),
            aiohttp.ClientSession(timeout=timeout) as session,
        ):
            if config.transaction_hash:
                signatures, examined, truncated = [config.transaction_hash], 1, False
            else:
                rows = await _rpc(
                    session,
                    rpc_url,
                    "getSignaturesForAddress",
                    [
                        config.wallet_address,
                        {"commitment": "finalized", "limit": config.signature_limit},
                    ],
                )
                if not isinstance(rows, list) or not all(
                    isinstance(row, dict)
                    and isinstance(row.get("signature"), str)
                    and isinstance(row.get("blockTime"), int)
                    for row in rows
                ):
                    raise ValueError("finalized signature history is invalid")
                examined = len(rows)
                truncated = (
                    len(rows) == config.signature_limit
                    and rows
                    and rows[-1]["blockTime"] >= start
                )
                if truncated:
                    return await _response(
                        "unavailable",
                        config,
                        matches=[],
                        signatures_examined=examined,
                        search_truncated=True,
                        reason="configured signature page does not cover the operation window",
                    )
                signatures = [
                    row["signature"]
                    for row in rows
                    if start <= row["blockTime"] <= end and row.get("err") is None
                ]
            matches = []
            for signature in signatures:
                value = await _rpc(
                    session,
                    rpc_url,
                    "getTransaction",
                    [
                        signature,
                        {
                            "commitment": "finalized",
                            "encoding": "jsonParsed",
                            "maxSupportedTransactionVersion": 0,
                        },
                    ],
                )
                matched = _match(signature, value, config, start, end)
                if matched:
                    matches.append(matched)
        status = (
            "confirmed"
            if len(matches) == 1
            else "not_found" if not matches else "ambiguous"
        )
        reason = {
            "not_found": "no finalized transaction matched every exact requirement",
            "ambiguous": "multiple finalized transactions matched the operation",
        }.get(status)
        return await _response(
            status,
            config,
            matches=matches,
            signatures_examined=examined,
            search_truncated=truncated,
            **({"reason": reason} if reason else {}),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return await _response(
            "unavailable",
            config,
            matches=[],
            reason=reports.safe_text(f"{type(exc).__name__}: {exc}"),
        )
