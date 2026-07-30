"""Expose one strategy-agnostic Gateway swap to LP Expert strategies."""

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from agents.lp_expert.routines import _gateway_operation_receipt as operations
from agents.lp_expert.routines import _routine_report as reports

CATEGORY = "Gateway Swap Safety"
RUN_ID = re.compile(r"^lp_expert\.[a-z][a-z0-9_-]*_(?:e)?[1-9]\d*$")
CONFIRMED = {"CONFIRMED", "SUCCESS", "COMPLETED"}
FAILED = {"FAILED", "ERROR", "REVERTED", "DROPPED"}
STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "strategies"


class Config(BaseModel):
    """Read scope/quote/recovery/status or submit one chosen Gateway swap."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["scope", "ensure_token", "quote", "recover", "execute", "status"]
    controller_id: StrictStr
    account_name: StrictStr
    network: StrictStr
    token_address: StrictStr | None = Field(default=None, min_length=1, max_length=128)
    token_symbol: StrictStr | None = Field(default=None, min_length=1, max_length=64)
    token_decimals: StrictInt | None = Field(default=None, ge=0, le=255)
    token_name: StrictStr | None = Field(default=None, min_length=1, max_length=128)
    connector: StrictStr | None = None
    operation_id: StrictStr | None = Field(default=None, min_length=8, max_length=128)
    wallet_address: StrictStr | None = None
    trading_pair: StrictStr | None = None
    side: Literal["BUY", "SELL"] | None = None
    amount: Decimal | None = Field(default=None, gt=0)
    slippage_pct: Decimal | None = Field(default=None, gt=0, le=100)
    max_quote_input: Decimal | None = Field(default=None, gt=0)
    attributed_base_amount: Decimal | None = Field(default=None, gt=0)
    reserve_token: StrictStr | None = None
    minimum_reserve_amount: Decimal | None = Field(default=None, ge=0)
    transaction_hash: StrictStr | None = None

    @model_validator(mode="after")
    def contract(self) -> "Config":
        if not RUN_ID.fullmatch(self.controller_id):
            raise ValueError("invalid lp_expert strategy controller_id")
        if not all(
            re.fullmatch(r"[A-Za-z0-9._-]+", value)
            for value in (self.account_name, self.network)
        ):
            raise ValueError("account and network identifiers are invalid")
        if self.action == "ensure_token":
            if (
                not self.token_address
                or not self.token_symbol
                or self.token_decimals is None
                or self.token_address != self.token_address.strip()
                or self.token_symbol != self.token_symbol.strip()
                or (
                    self.token_name is not None
                    and self.token_name != self.token_name.strip()
                )
            ):
                raise ValueError(
                    "ensure_token requires exact address, symbol, and decimals"
                )
        if self.action in {"quote", "recover", "execute", "status"}:
            if (
                not self.connector
                or not re.fullmatch(r"[A-Za-z0-9._-]+", self.connector)
                or not self.trading_pair
                or not self.side
                or self.amount is None
                or self.slippage_pct is None
            ):
                raise ValueError(
                    "quote/recover/execute/status requires connector, pair, side, amount, and slippage"
                )
            pair = self.trading_pair.split("-")
            if len(pair) != 2 or not all(pair):
                raise ValueError("pair must be BASE-QUOTE")
        if self.action in {"execute", "recover", "status"}:
            if (
                not self.operation_id
                or not re.fullmatch(r"[A-Za-z0-9_-]+", self.operation_id)
                or not self.wallet_address
            ):
                raise ValueError(
                    "execute/recover/status requires exact operation and wallet"
                )
            if self.side == "BUY":
                if self.max_quote_input is None:
                    raise ValueError("BUY requires an explicit maximum quote input")
            elif self.attributed_base_amount != self.amount:
                raise ValueError("SELL must equal exact attributed base")
        if self.action == "execute":
            if (self.reserve_token is None) != (self.minimum_reserve_amount is None):
                raise ValueError("reserve token and amount must be supplied together")
        if self.action == "status" and not self.transaction_hash:
            raise ValueError("status requires an exact transaction hash")
        return self


async def _response(status: str, config: Config, **details: Any) -> str:
    payload = {
        "status": status,
        "action": config.action,
        "controller_id": config.controller_id,
        "operation_id": config.operation_id,
        **details,
    }
    evidence = []
    binding = details.get("binding")
    if isinstance(binding, dict):
        evidence.append(
            {
                "kind": "binding",
                "execution_mode": binding.get("execution_mode"),
                "server_name": binding.get("server_name"),
                "network": binding.get("network"),
            }
        )
    balances = details.get("balances")
    if isinstance(balances, list):
        evidence.extend(
            {
                "kind": "balance",
                "asset": row.get("symbol") or row.get("token") or "unknown",
                "available": row.get(
                    "available_units",
                    row.get(
                        "available",
                        row.get("available_balance", row.get("units")),
                    ),
                ),
            }
            for row in balances
            if isinstance(row, dict)
        )
    quote = details.get("quote")
    if isinstance(quote, dict):
        evidence.append({"kind": "quote", **quote})
    receipt = details.get("receipt")
    if isinstance(receipt, dict):
        evidence.append({"kind": "receipt", **receipt})
    operation_state = details.get("operation_state")
    if isinstance(operation_state, dict):
        evidence.append({"kind": "operation_state", **operation_state})
    token = details.get("token")
    if isinstance(token, dict):
        evidence.append({"kind": "token_registry", **token})
    if details.get("recovery_source"):
        evidence.append({"kind": "recovery", "source": details["recovery_source"]})
    if details.get("reason"):
        evidence.append({"kind": "error", "reason": details["reason"]})
    payload = await reports.attach_trace(
        payload,
        title="LP Expert Gateway Operation",
        source="gateway_swap",
        status=status,
        summary={
            "action": config.action,
            "controller_id": config.controller_id,
            "operation_id": config.operation_id,
            "connector": config.connector,
            "network": config.network,
            "trading_pair": config.trading_pair,
            "side": config.side,
            "amount": config.amount,
            "token_address": config.token_address,
            "token_symbol": config.token_symbol,
            "token_decimals": config.token_decimals,
            "mutation": details.get("mutation", False),
        },
        evidence=evidence,
    )
    return json.dumps(payload, default=str, separators=(",", ":"), sort_keys=True)


def _result_value(value: Any, *names: str) -> Any:
    pending = [value]
    visited: set[int] = set()
    while pending:
        current = pending.pop(0)
        if not isinstance(current, dict) or id(current) in visited:
            continue
        visited.add(id(current))
        for name in names:
            if current.get(name) not in (None, ""):
                return current[name]
        pending.extend(current.get(key) for key in ("result", "data"))
    return None


def _positive_amount(value: Any, *names: str) -> Decimal | None:
    raw = _result_value(value, *names)
    if raw in (None, ""):
        return None
    try:
        amount = Decimal(str(raw))
    except Exception:
        return None
    return amount if amount.is_finite() and amount > 0 else None


def _token_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        rows = value.get("tokens")
        if not isinstance(rows, list):
            rows = value.get("data")
    else:
        rows = None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Gateway token registry response is invalid")
    return rows


def _token_address(value: dict[str, Any]) -> str:
    return str(
        value.get("address") or value.get("token_address") or value.get("mint") or ""
    ).strip()


def _registered_token(
    rows: list[dict[str, Any]], config: Config
) -> dict[str, Any] | None:
    exact = [row for row in rows if _token_address(row) == config.token_address]
    if len(exact) > 1:
        raise ValueError("Gateway token registry has duplicate address metadata")
    if exact:
        row = exact[0]
        symbol = str(row.get("symbol") or "").strip()
        decimals = row.get("decimals")
        if symbol.casefold() != config.token_symbol.casefold() or isinstance(
            decimals, bool
        ):
            raise ValueError(
                f"Gateway token metadata mismatch for {config.token_symbol}"
            )
        try:
            decimals = int(decimals)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Gateway token metadata mismatch for {config.token_symbol}"
            ) from exc
        if decimals != config.token_decimals:
            raise ValueError(
                f"Gateway token metadata mismatch for {config.token_symbol}"
            )
        return {
            "address": config.token_address,
            "symbol": symbol,
            "decimals": decimals,
        }
    collision = [
        row
        for row in rows
        if str(row.get("symbol") or "").strip().casefold()
        == config.token_symbol.casefold()
        and _token_address(row) != config.token_address
    ]
    if collision:
        raise ValueError(f"Gateway token symbol collision for {config.token_symbol}")
    return None


async def _runtime(controller_id: str) -> dict[str, Any]:
    from mcp_servers.condor.condor_client import call_main_api

    agent = await call_main_api("GET", "/agents/lp_expert")
    matches = [
        (strategy.get("slug"), row)
        for strategy in agent.get("strategies", [])
        for row in strategy.get("instances", [])
        if row.get("agent_id") == controller_id
    ]
    if len(matches) != 1:
        raise ValueError("active Condor instance was not uniquely found")
    strategy_slug, row = matches[0]
    mode = row.get("execution_mode")
    suffix = r"[1-9]\d*" if mode == "loop" else r"e[1-9]\d*"
    expected_id = re.compile(rf"^lp_expert\.{re.escape(str(strategy_slug))}_{suffix}$")
    if (
        row.get("status") != "running"
        or mode not in {"dry_run", "run_once", "loop"}
        or not expected_id.fullmatch(controller_id)
        or not str(row.get("server_name") or "").strip()
    ):
        raise ValueError("active Condor mode or server scope is invalid")
    return {**row, "_strategy_slug": str(strategy_slug)}


def _loop_account(controller_id: str, strategy_slug: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", strategy_slug):
        raise ValueError("active strategy slug is invalid")
    session_number = controller_id.rsplit("_", 1)[1]
    path = (
        STRATEGIES_DIR
        / strategy_slug
        / "sessions"
        / f"session_{session_number}"
        / "config.yml"
    )
    value = yaml.safe_load(path.read_text()) if path.is_file() else None
    account = value.get("account_name") if isinstance(value, dict) else None
    if not isinstance(account, str) or not account.strip():
        raise ValueError("current loop session account binding is unavailable")
    return account


def _authoritative_account(
    config: Config, runtime: dict[str, Any], require: bool
) -> str | None:
    account = runtime.get("account_name")
    if not isinstance(account, str) or not account.strip():
        account = None
    if account is None and runtime.get("execution_mode") == "loop":
        account = _loop_account(
            config.controller_id, str(runtime.get("_strategy_slug") or "")
        )
    if account is None:
        if require:
            raise ValueError(
                "active account config authority is unavailable for this mutation"
            )
        return None
    if config.account_name != account:
        raise ValueError("account conflicts with active Condor instance")
    return account


def _operation_identity(config: Config, binding: dict[str, Any]) -> dict[str, Any]:
    strategy_slug = config.controller_id.split(".", 1)[1].rsplit("_", 1)[0]
    return {
        "controller_id": config.controller_id,
        "strategy_slug": strategy_slug,
        "operation_id": config.operation_id,
        "account_name": config.account_name,
        "network": config.network,
        "wallet_address": binding["wallet_address"],
        "connector": config.connector,
        "trading_pair": config.trading_pair,
        "side": config.side,
        "amount": str(config.amount),
        "slippage_pct": str(config.slippage_pct),
        "max_quote_input": (
            str(config.max_quote_input) if config.max_quote_input is not None else None
        ),
        "attributed_base_amount": (
            str(config.attributed_base_amount)
            if config.attributed_base_amount is not None
            else None
        ),
    }


def _write_operation(
    config: Config,
    binding: dict[str, Any],
    *,
    phase: str,
    mutation_possible: bool,
    receipt: dict[str, Any] | None = None,
    reason: str | None = None,
    create_only: bool = False,
    upgrade_legacy: bool = False,
) -> str | None:
    if binding["execution_mode"] != "loop":
        return None
    try:
        operations.write(
            STRATEGIES_DIR,
            _operation_identity(config, binding),
            phase=phase,
            mutation_possible=mutation_possible,
            receipt=receipt,
            reason=reason,
            create_only=create_only,
            upgrade_legacy=upgrade_legacy,
        )
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _read_operation(config: Config, binding: dict[str, Any]) -> dict[str, Any] | None:
    if binding["execution_mode"] != "loop":
        raise ValueError("operation recovery is available only in live loop mode")
    return operations.read(
        STRATEGIES_DIR,
        _operation_identity(config, binding),
        allow_legacy=config.action == "recover",
    )


async def _scope(config: Config) -> tuple[Any, dict[str, Any], list[dict]]:
    from config_manager import get_config_manager
    from mcp_servers.condor.settings import settings

    runtime = await _runtime(config.controller_id)
    server = str(runtime["server_name"])
    if settings.active_server and settings.active_server != server:
        raise ValueError("MCP server conflicts with active Condor instance")
    _authoritative_account(
        config,
        runtime,
        require=(
            config.action in {"ensure_token", "execute"}
            and runtime["execution_mode"] != "dry_run"
        ),
    )
    client = await get_config_manager().get_client(server)
    network = await client.gateway.get_network_config(config.network)
    wallet = str(network.get("default_wallet") or "").strip()
    if not wallet or (config.wallet_address and config.wallet_address != wallet):
        raise ValueError("current network default wallet binding failed")
    state = await client.portfolio.get_state(
        account_names=[config.account_name],
        connector_names=[config.network],
        refresh=True,
    )
    balances = (
        state.get(config.account_name, {}).get(config.network)
        if isinstance(state, dict) and isinstance(state.get(config.account_name), dict)
        else None
    )
    if not isinstance(balances, list):
        raise ValueError("scoped balances were not returned")
    binding = {
        "execution_mode": runtime["execution_mode"],
        "server_name": server,
        "account_name": config.account_name,
        "network": config.network,
        "wallet_address": wallet,
    }
    return client, binding, balances


async def _ensure_token(client: Any, binding: dict[str, Any], config: Config) -> str:
    rows = _token_rows(await client.gateway.get_network_tokens(config.network))
    token = _registered_token(rows, config)
    if token is not None:
        return await _response(
            "ready",
            config,
            binding=binding,
            token={**token, "registered_now": False},
            mutation=False,
            retry_allowed=False,
        )
    if binding["execution_mode"] == "dry_run":
        return await _response(
            "missing",
            config,
            binding=binding,
            token={
                "address": config.token_address,
                "symbol": config.token_symbol,
                "decimals": config.token_decimals,
                "registered_now": False,
            },
            reason="dry run cannot mutate the Gateway token registry",
            mutation=False,
            retry_allowed=False,
        )
    try:
        await client.gateway.add_token(
            network_id=config.network,
            address=config.token_address,
            symbol=config.token_symbol,
            decimals=config.token_decimals,
            name=config.token_name or config.token_symbol,
        )
    except asyncio.CancelledError:
        return await _response(
            "uncertain",
            config,
            binding=binding,
            token={
                "address": config.token_address,
                "symbol": config.token_symbol,
                "decimals": config.token_decimals,
                "registered_now": None,
            },
            reason="Gateway token registration wait was cancelled",
            mutation=True,
            retry_allowed=False,
        )
    except Exception as exc:
        try:
            refreshed = _registered_token(
                _token_rows(await client.gateway.get_network_tokens(config.network)),
                config,
            )
        except Exception:
            refreshed = None
        if refreshed is not None:
            return await _response(
                "registered",
                config,
                binding=binding,
                token={**refreshed, "registered_now": True, "reconciled": True},
                mutation=True,
                retry_allowed=False,
            )
        return await _response(
            "uncertain",
            config,
            binding=binding,
            token={
                "address": config.token_address,
                "symbol": config.token_symbol,
                "decimals": config.token_decimals,
                "registered_now": None,
            },
            reason=f"Gateway token registration was not confirmed: {type(exc).__name__}: {exc}",
            mutation=True,
            retry_allowed=False,
        )
    try:
        token = _registered_token(
            _token_rows(await client.gateway.get_network_tokens(config.network)),
            config,
        )
    except Exception as exc:
        return await _response(
            "uncertain",
            config,
            binding=binding,
            token={
                "address": config.token_address,
                "symbol": config.token_symbol,
                "decimals": config.token_decimals,
                "registered_now": None,
            },
            reason=f"Gateway token registration verification failed: {type(exc).__name__}: {exc}",
            mutation=True,
            retry_allowed=False,
        )
    if token is None:
        return await _response(
            "uncertain",
            config,
            binding=binding,
            token={
                "address": config.token_address,
                "symbol": config.token_symbol,
                "decimals": config.token_decimals,
                "registered_now": None,
            },
            reason="Gateway token registration was not visible after submission",
            mutation=True,
            retry_allowed=False,
        )
    return await _response(
        "registered",
        config,
        binding=binding,
        token={**token, "registered_now": True},
        mutation=True,
        retry_allowed=False,
    )


def _available(rows: list[dict], token: str) -> Decimal:
    found = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("portfolio balance row is invalid")
        symbol = str(row.get("token") or row.get("symbol") or "")
        mint = str(
            row.get("mint") or row.get("token_address") or row.get("address") or ""
        )
        if mint == token or symbol.upper() == token.upper():
            value = row.get(
                "available_units",
                row.get("available", row.get("available_balance", row.get("units"))),
            )
            if value is not None:
                found.append(Decimal(str(value)))
    if len(found) != 1 or not found[0].is_finite() or found[0] < 0:
        raise ValueError(f"{token} balance is not unique")
    return found[0]


async def _gateway(client: Any, **values: Any) -> dict[str, Any]:
    from mcp_servers.hummingbot_api.schemas import GatewaySwapRequest
    from mcp_servers.hummingbot_api.tools.gateway_swap import manage_gateway_swaps

    return await manage_gateway_swaps(client, GatewaySwapRequest(**values))


async def _quote(client: Any, config: Config) -> tuple[dict, Decimal, Decimal]:
    value = await _gateway(
        client,
        action="quote",
        connector=config.connector,
        network=config.network,
        trading_pair=config.trading_pair,
        side=config.side,
        amount=str(config.amount),
        slippage_pct=str(config.slippage_pct),
    )
    if (
        value.get("action") != "quote"
        or value.get("trading_pair") != config.trading_pair
        or value.get("side") != config.side
    ):
        raise ValueError("Gateway quote identity is inconsistent")
    amount_in = Decimal(str(_result_value(value, "input_amount", "amount_in")))
    amount_out = Decimal(str(_result_value(value, "output_amount", "amount_out")))
    if not all(item.is_finite() and item > 0 for item in (amount_in, amount_out)):
        raise ValueError("Gateway quote amounts are invalid")
    return value, amount_in, amount_out


def _receipt(value: Any, config: Config) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("swap receipt is not an object")
    if value.get("action") == "execute" and (
        value.get("trading_pair") != config.trading_pair
        or value.get("side") != config.side
        or value.get("wallet_address") != config.wallet_address
    ):
        raise ValueError("swap receipt identity is inconsistent")
    transaction_hash = _result_value(
        value, "transaction_hash", "tx_hash", "signature", "hash"
    )
    if not transaction_hash or (
        config.transaction_hash and transaction_hash != config.transaction_hash
    ):
        raise ValueError("transaction hash is missing or mismatched")
    observed = {
        "connector": _result_value(value, "connector"),
        "network": _result_value(value, "network"),
        "wallet_address": _result_value(value, "wallet_address", "walletAddress"),
        "trading_pair": _result_value(value, "trading_pair", "tradingPair"),
        "side": _result_value(value, "side"),
    }
    expected = {
        "connector": config.connector,
        "network": config.network,
        "wallet_address": config.wallet_address,
        "trading_pair": config.trading_pair,
        "side": config.side,
    }
    for key, found in observed.items():
        if found not in (None, "") and str(found).upper() != str(expected[key]).upper():
            raise ValueError(f"swap receipt {key} is inconsistent")
    return {
        "transaction_hash": str(transaction_hash),
        "transaction_status": str(_result_value(value, "status") or "UNKNOWN").upper(),
        "input_amount": _positive_amount(
            value, "input_amount", "amount_in", "amountIn"
        ),
        "output_amount": _positive_amount(
            value, "output_amount", "amount_out", "amountOut"
        ),
        "timestamp": _result_value(value, "timestamp", "created_at", "submitted_at"),
        **{key: found for key, found in observed.items() if found not in (None, "")},
    }


def _merge_receipts(
    submitted: dict[str, Any], observed: dict[str, Any]
) -> dict[str, Any]:
    if submitted["transaction_hash"] != observed["transaction_hash"]:
        raise ValueError("swap status transaction hash is inconsistent")
    return {
        **submitted,
        **{
            key: value
            for key, value in observed.items()
            if value not in (None, "", "UNKNOWN")
        },
    }


def _receipt_issue(receipt: dict[str, Any], config: Config) -> str | None:
    actual_in = receipt.get("input_amount")
    actual_out = receipt.get("output_amount")
    if not isinstance(actual_in, Decimal) or not isinstance(actual_out, Decimal):
        return "confirmed swap receipt is missing positive numeric amounts"
    if config.side == "BUY":
        minimum_output = config.amount * (
            Decimal("1") - config.slippage_pct / Decimal("100")
        )
        if actual_in > config.max_quote_input or actual_out < minimum_output:
            return "confirmed BUY receipt violates its input cap or slippage floor"
    elif actual_in != config.amount:
        return "confirmed SELL receipt violates its exact attributed input"
    return None


def _operation_time(operation: dict[str, Any]) -> datetime:
    raw = operation.get("created_at") or operation.get("updated_at")
    if not isinstance(raw, str):
        raise ValueError("operation receipt timestamp is missing")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("operation receipt timestamp is invalid") from exc
    if value.tzinfo is None:
        raise ValueError("operation receipt timestamp is not timezone-aware")
    return value.astimezone(timezone.utc)


def _history_rows(value: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or not isinstance(value.get("data"), list):
        raise ValueError("Gateway swap history response is invalid")
    rows = value["data"]
    if len(rows) >= limit:
        raise ValueError("Gateway swap history result may be truncated")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Gateway swap history contains an invalid row")
    return rows


def _history_match(
    row: dict[str, Any], config: Config, attempted_at: datetime
) -> dict[str, Any] | None:
    try:
        receipt = _receipt(row, config)
        timestamp = receipt.get("timestamp")
        observed_at = (
            datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            if timestamp is not None
            else None
        )
    except (TypeError, ValueError):
        return None
    if (
        observed_at is None
        or observed_at.tzinfo is None
        or not attempted_at - timedelta(seconds=15)
        <= observed_at.astimezone(timezone.utc)
        <= attempted_at + timedelta(seconds=120)
        or _receipt_issue(receipt, config) is not None
    ):
        return None
    return receipt


async def _find_submitted_swap(
    client: Any,
    config: Config,
    operation: dict[str, Any],
) -> list[dict[str, Any]]:
    attempted_at = _operation_time(operation)
    limit = 1000
    params = {
        "network": config.network,
        "connector": config.connector,
        "wallet_address": config.wallet_address,
        "trading_pair": config.trading_pair,
        "start_time": int((attempted_at - timedelta(seconds=15)).timestamp()),
        "end_time": int((attempted_at + timedelta(seconds=120)).timestamp()),
        "limit": limit,
        "offset": 0,
    }
    response = await client.gateway_swap._post("/gateway/swaps/search", params=params)
    matches = [
        receipt
        for row in _history_rows(response, limit)
        if (receipt := _history_match(row, config, attempted_at)) is not None
    ]
    hashes = {receipt["transaction_hash"] for receipt in matches}
    if len(hashes) != len(matches):
        raise ValueError("Gateway swap history repeated a transaction")
    return matches


async def _refresh_receipt(
    client: Any, config: Config, receipt: dict[str, Any]
) -> dict[str, Any]:
    observed = _receipt(
        await _gateway(
            client,
            action="get_status",
            transaction_hash=receipt["transaction_hash"],
        ),
        config,
    )
    return _merge_receipts(receipt, observed)


async def _receipt_response(
    config: Config,
    binding: dict[str, Any],
    receipt: dict[str, Any],
    *,
    quote: dict[str, Decimal] | None = None,
    reason: str | None = None,
    recovery_source: str | None = None,
) -> str:
    transaction_status = receipt["transaction_status"]
    if transaction_status in CONFIRMED:
        issue = _receipt_issue(receipt, config)
        status = "confirmed" if issue is None else "manual_review"
        phase = status
        reason = issue or reason
    elif transaction_status in FAILED:
        status = "manual_review"
        phase = "manual_review"
        reason = reason or f"Gateway reported terminal status {transaction_status}"
    else:
        status = "submitted"
        phase = "submitted"
    record_error = _write_operation(
        config,
        binding,
        phase=phase,
        mutation_possible=True,
        receipt=receipt,
        reason=reason,
    )
    details = {
        "binding": binding,
        "receipt": receipt,
        "mutation": True,
        "retry_allowed": False,
    }
    if quote is not None:
        details["quote"] = quote
    if reason is not None:
        details["reason"] = reason
    if recovery_source is not None:
        details["recovery_source"] = recovery_source
    if record_error:
        details["reason"] = f"operation receipt failed: {record_error}"
        return await _response("manual_review", config, **details)
    return await _response(status, config, **details)


async def run(config: Config, context: Any) -> str:
    binding = None
    quote_evidence = None
    try:
        client, binding, balances = await _scope(config)
        if config.action == "scope":
            return await _response("ready", config, binding=binding, balances=balances)
        if config.action == "ensure_token":
            return await _ensure_token(client, binding, config)
        if config.action == "quote":
            value, amount_in, amount_out = await _quote(client, config)
            return await _response(
                "quoted",
                config,
                binding=binding,
                quote={"input_amount": amount_in, "output_amount": amount_out},
                gateway=value,
            )
        if config.action == "recover":
            try:
                operation = _read_operation(config, binding)
            except Exception as exc:
                return await _response(
                    "manual_review",
                    config,
                    binding=binding,
                    reason=f"operation identity reconciliation failed: {type(exc).__name__}: {exc}",
                    mutation=True,
                    retry_allowed=False,
                )
            if operation is None:
                return await _response(
                    "not_submitted",
                    config,
                    binding=binding,
                    operation_state={"phase": "absent", "mutation_possible": False},
                    mutation=False,
                    retry_allowed=True,
                )
            operation_state = operations.public(operation)
            phase = str(operation["phase"])
            saved_receipt = (
                operation["receipt"]
                if isinstance(operation.get("receipt"), dict)
                else None
            )
            if phase in {"submitting", "uncertain"} and not (
                saved_receipt and saved_receipt.get("transaction_hash")
            ):
                try:
                    matches = await _find_submitted_swap(client, config, operation)
                except Exception as exc:
                    reason = f"Gateway history reconciliation failed: {type(exc).__name__}: {exc}"
                    record_error = _write_operation(
                        config,
                        binding,
                        phase="uncertain",
                        mutation_possible=True,
                        reason=reason,
                    )
                    return await _response(
                        "uncertain",
                        config,
                        binding=binding,
                        operation_state=operation_state,
                        reason=reason,
                        mutation=True,
                        retry_allowed=False,
                        **(
                            {"operation_record_error": record_error}
                            if record_error is not None
                            else {}
                        ),
                    )
                if not matches:
                    reason = "no exact current-operation swap was found; submission remains uncertain"
                    record_error = _write_operation(
                        config,
                        binding,
                        phase="uncertain",
                        mutation_possible=True,
                        reason=reason,
                    )
                    return await _response(
                        "uncertain",
                        config,
                        binding=binding,
                        operation_state=operation_state,
                        reason=reason,
                        mutation=True,
                        retry_allowed=False,
                        **(
                            {"operation_record_error": record_error}
                            if record_error is not None
                            else {}
                        ),
                    )
                if len(matches) != 1:
                    reason = "multiple current-operation swaps matched Gateway history"
                    record_error = _write_operation(
                        config,
                        binding,
                        phase="manual_review",
                        mutation_possible=True,
                        reason=reason,
                    )
                    return await _response(
                        "manual_review",
                        config,
                        binding=binding,
                        operation_state=operation_state,
                        reason=reason,
                        mutation=True,
                        retry_allowed=False,
                        **(
                            {"operation_record_error": record_error}
                            if record_error is not None
                            else {}
                        ),
                    )
                saved_receipt = matches[0]
                record_error = _write_operation(
                    config,
                    binding,
                    phase="submitted",
                    mutation_possible=True,
                    receipt=saved_receipt,
                    upgrade_legacy=operation.get("_legacy_identity") is True,
                )
                if record_error:
                    return await _response(
                        "manual_review",
                        config,
                        binding=binding,
                        receipt=saved_receipt,
                        reason=f"operation receipt failed: {record_error}",
                        mutation=True,
                        retry_allowed=False,
                    )
                try:
                    saved_receipt = await _refresh_receipt(
                        client, config, saved_receipt
                    )
                except Exception as exc:
                    return await _receipt_response(
                        config,
                        binding,
                        saved_receipt,
                        reason=f"status reconciliation failed: {type(exc).__name__}: {exc}",
                        recovery_source="gateway_swap_history",
                    )
                return await _receipt_response(
                    config,
                    binding,
                    saved_receipt,
                    recovery_source="gateway_swap_history",
                )
            status = {
                "rejected": "rejected",
                "confirmed": "confirmed",
                "submitted": "pending",
                "manual_review": "manual_review",
            }.get(phase, "uncertain")
            if phase in {"submitting", "uncertain"} and saved_receipt:
                status = "pending"
            return await _response(
                status,
                config,
                binding=binding,
                operation_state=operation_state,
                **({"receipt": saved_receipt} if saved_receipt else {}),
                mutation=bool(operation["mutation_possible"]),
                retry_allowed=phase in {"rejected"},
            )
        if config.action == "status":
            receipt = {
                "transaction_hash": config.transaction_hash,
                "transaction_status": "UNKNOWN",
                "input_amount": None,
                "output_amount": None,
                "timestamp": None,
            }
            try:
                receipt = await _refresh_receipt(client, config, receipt)
            except Exception as exc:
                return await _receipt_response(
                    config,
                    binding,
                    receipt,
                    reason=f"status reconciliation failed: {type(exc).__name__}: {exc}",
                )
            return await _receipt_response(config, binding, receipt)
        if binding["execution_mode"] == "loop":
            existing = _read_operation(config, binding)
            if existing is not None:
                return await _response(
                    "rejected",
                    config,
                    binding=binding,
                    operation_state=operations.public(existing),
                    reason="operation id already has a current-session receipt; recover it",
                    mutation=False,
                    retry_allowed=False,
                )
        if binding["execution_mode"] == "dry_run":
            raise ValueError("dry run cannot execute a Gateway mutation")
        _, amount_in, amount_out = await _quote(client, config)
        quote_evidence = {"input_amount": amount_in, "output_amount": amount_out}
        amount = Decimal(config.amount)
        base, quote = config.trading_pair.split("-", 1)
        if config.side == "BUY":
            if (
                amount_in > config.max_quote_input
                or amount_out < amount
                or _available(balances, quote) < amount_in
            ):
                raise ValueError("BUY quote or inventory bound failed")
        else:
            if amount_in != amount or _available(balances, base) < amount:
                raise ValueError("SELL attribution or residual bound failed")
        if config.reserve_token is not None:
            reserve_after = _available(balances, config.reserve_token)
            spent_token, spent_amount = (
                (quote, amount_in) if config.side == "BUY" else (base, amount)
            )
            if spent_token.upper() == config.reserve_token.upper():
                reserve_after -= spent_amount
            if reserve_after < config.minimum_reserve_amount:
                raise ValueError("swap cannot retain the requested reserve")
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        record_error = (
            _write_operation(
                config,
                binding,
                phase="rejected",
                mutation_possible=False,
                reason=reason,
                create_only=True,
            )
            if config.action == "execute" and binding is not None
            else None
        )
        return await _response(
            "rejected",
            config,
            **({"binding": binding} if binding is not None else {}),
            **({"quote": quote_evidence} if quote_evidence is not None else {}),
            reason=reason,
            mutation=False,
            retry_allowed=record_error is None,
            **(
                {"operation_record_error": record_error}
                if record_error is not None
                else {}
            ),
        )

    record_error = _write_operation(
        config,
        binding,
        phase="submitting",
        mutation_possible=True,
        create_only=True,
    )
    if record_error:
        return await _response(
            "rejected",
            config,
            binding=binding,
            quote=quote_evidence,
            reason=f"operation receipt admission failed: {record_error}",
            mutation=False,
            retry_allowed=False,
        )
    task = asyncio.create_task(
        _gateway(
            client,
            action="execute",
            connector=config.connector,
            network=config.network,
            trading_pair=config.trading_pair,
            side=config.side,
            amount=str(config.amount),
            slippage_pct=str(config.slippage_pct),
            wallet_address=binding["wallet_address"],
        )
    )
    raw_receipt = None
    try:
        raw_receipt = await asyncio.shield(task)
    except asyncio.CancelledError:
        reason = "swap wait was cancelled"
        record_error = _write_operation(
            config,
            binding,
            phase="uncertain",
            mutation_possible=True,
            reason=reason,
        )
        return await _response(
            "uncertain",
            config,
            binding=binding,
            quote=quote_evidence,
            reason=reason,
            mutation=True,
            retry_allowed=False,
            **(
                {"operation_record_error": record_error}
                if record_error is not None
                else {}
            ),
        )
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        record_error = _write_operation(
            config,
            binding,
            phase="uncertain",
            mutation_possible=True,
            reason=reason,
        )
        return await _response(
            "uncertain",
            config,
            binding=binding,
            quote=quote_evidence,
            reason=reason,
            mutation=True,
            retry_allowed=False,
            **(
                {"operation_record_error": record_error}
                if record_error is not None
                else {}
            ),
        )
    try:
        receipt = _receipt(raw_receipt, config)
    except Exception as exc:
        transaction_hash = _result_value(
            raw_receipt, "transaction_hash", "tx_hash", "signature", "hash"
        )
        receipt = (
            {
                "transaction_hash": str(transaction_hash),
                "transaction_status": str(
                    _result_value(raw_receipt, "status") or "UNKNOWN"
                ).upper(),
                "input_amount": None,
                "output_amount": None,
                "timestamp": _result_value(
                    raw_receipt, "timestamp", "created_at", "submitted_at"
                ),
            }
            if transaction_hash
            else None
        )
        reason = f"{type(exc).__name__}: {exc}"
        record_error = _write_operation(
            config,
            binding,
            phase="manual_review" if receipt else "uncertain",
            mutation_possible=True,
            receipt=receipt,
            reason=reason,
        )
        return await _response(
            "manual_review" if receipt else "uncertain",
            config,
            binding=binding,
            quote=quote_evidence,
            **({"receipt": receipt} if receipt else {}),
            reason=reason,
            mutation=True,
            retry_allowed=False,
            **(
                {"operation_record_error": record_error}
                if record_error is not None
                else {}
            ),
        )
    record_error = _write_operation(
        config,
        binding,
        phase="submitted",
        mutation_possible=True,
        receipt=receipt,
    )
    if record_error:
        return await _response(
            "manual_review",
            config,
            binding=binding,
            quote=quote_evidence,
            receipt=receipt,
            reason=f"operation receipt failed: {record_error}",
            mutation=True,
            retry_allowed=False,
        )
    if (
        receipt["transaction_status"] not in CONFIRMED
        or _receipt_issue(receipt, config)
        == "confirmed swap receipt is missing positive numeric amounts"
    ):
        try:
            receipt = await _refresh_receipt(client, config, receipt)
        except Exception as exc:
            return await _receipt_response(
                config,
                binding,
                receipt,
                quote=quote_evidence,
                reason=f"status reconciliation failed: {type(exc).__name__}: {exc}",
            )
    return await _receipt_response(
        config,
        binding,
        receipt,
        quote=quote_evidence,
    )
