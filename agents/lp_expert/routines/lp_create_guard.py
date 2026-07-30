"""Fail-closed, single-submit guard for one Orca LP executor create."""

import asyncio
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from agents.lp_expert.routines import _routine_report as reports
from agents.lp_expert.routines._orca_pool_metrics import USDC_MINT
from agents.lp_expert.routines.clmm_position_plan import (
    LP_PROVIDER,
    NETWORK,
    SWAP_PROVIDER,
    plan_digest,
)

CATEGORY = "Orca LP Safety"
CONTROLLER_RE = re.compile(r"^lp_expert\.orca_(?:e)?[1-9]\d*$")
RUN_ONCE_RE = re.compile(r"^lp_expert\.orca_e[1-9]\d*$")
STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "strategies"
SETTLED = {
    "COMPLETE",
    "COMPLETED",
    "TERMINATED",
    "CANCELED",
    "CANCELLED",
    "CLOSED",
    "STOPPED",
}
SESSION_KEYS = {
    "server_name",
    "agent_key",
    "model_base_url",
    "frequency_sec",
    "trading_context",
    "execution_mode",
    "max_ticks",
    "bot_name",
    "account_name",
    "default_risk_posture",
    "total_amount_quote",
    "max_open_executors",
    "max_slot_deployments_per_tick",
    "min_quote_per_executor",
    "max_quote_per_executor",
    "max_slippage_pct",
    "min_sol_reserve",
    "residual_base_dust_quote",
    "minimum_range_half_width_pct",
    "maximum_range_half_width_pct",
    "rebalance_threshold_pct",
    "executor_max_age_minutes",
    "executor_take_profit_net_pnl_ratio",
    "executor_stop_loss_net_pnl_ratio",
    "session_max_age_minutes",
    "session_take_profit_net_pnl_ratio",
    "session_stop_loss_net_pnl_ratio",
    "onchain_reconcile_before_seconds",
    "onchain_reconcile_after_seconds",
    "onchain_reconcile_signature_limit",
    "onchain_reconcile_rpc_timeout_seconds",
    "risk_limits",
}


def _decimal(value: Any, label: str, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{label} must be a decimal") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f"{label} must be finite and valid")
    return result


def _positive_integer(value: Any, label: str) -> int:
    number = _decimal(value, label, positive=True)
    if isinstance(value, bool) or number != number.to_integral_value():
        raise ValueError(f"{label} must be a positive integer")
    return int(number)


class Config(BaseModel):
    """Validate configured live scope and submit exactly one planned LP create."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    controller_id: StrictStr
    operation_id: StrictStr = Field(min_length=8, max_length=128)
    wallet_address: StrictStr
    attributed_base_amount: Decimal
    preparation_quote_spent: Decimal
    plan: dict[str, Any]
    plan_digest: StrictStr = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def identifiers(self) -> "Config":
        if not CONTROLLER_RE.fullmatch(self.controller_id):
            raise ValueError("controller_id must be a live lp_expert.orca id")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.operation_id):
            raise ValueError("operation_id contains unsupported characters")
        if (
            not self.wallet_address.strip()
            or _decimal(self.attributed_base_amount, "attributed base") < 0
            or _decimal(self.preparation_quote_spent, "preparation quote spent") < 0
        ):
            raise ValueError("wallet and non-negative swap attribution are required")
        return self


def _validate_session(value: dict[str, Any], execution_mode: str) -> dict[str, Any]:
    limits = value.get("risk_limits")
    required_risk_keys = {
        "max_position_size_quote",
        "max_open_executors",
        "max_drawdown_pct",
    }
    allowed_risk_keys = required_risk_keys | {"shutdown_drawdown_pct"}
    if (
        not isinstance(limits, dict)
        or not required_risk_keys <= set(limits)
        or not set(limits) <= allowed_risk_keys
    ):
        raise ValueError("current risk_limits are malformed")
    total = _decimal(value.get("total_amount_quote"), "total amount", True)
    max_open = _positive_integer(value.get("max_open_executors"), "max open executors")
    _positive_integer(
        value.get("max_slot_deployments_per_tick"),
        "max slot deployments per tick",
    )
    minimum = _decimal(
        value.get("min_quote_per_executor"), "minimum executor amount", True
    )
    maximum = _decimal(
        value.get("max_quote_per_executor"), "maximum executor amount", True
    )
    range_minimum = _decimal(
        value.get("minimum_range_half_width_pct"), "minimum range", True
    )
    range_maximum = _decimal(
        value.get("maximum_range_half_width_pct"), "maximum range", True
    )
    rebalance_threshold = _decimal(
        value.get("rebalance_threshold_pct"), "rebalance threshold", True
    )
    risk_maximum = _decimal(
        limits["max_position_size_quote"], "risk position size", True
    )
    risk_open = _positive_integer(
        limits["max_open_executors"], "risk max open executors"
    )
    soft_drawdown = _decimal(limits["max_drawdown_pct"], "max drawdown")
    shutdown_drawdown = _decimal(
        limits.get("shutdown_drawdown_pct", -1), "shutdown drawdown"
    )
    dust = _decimal(value.get("residual_base_dust_quote"), "capital dust")
    onchain_before = _positive_integer(
        value.get("onchain_reconcile_before_seconds"), "on-chain lookback"
    )
    onchain_after = _positive_integer(
        value.get("onchain_reconcile_after_seconds"), "on-chain lookahead"
    )
    onchain_limit = _positive_integer(
        value.get("onchain_reconcile_signature_limit"), "on-chain signature limit"
    )
    onchain_timeout = _positive_integer(
        value.get("onchain_reconcile_rpc_timeout_seconds"), "on-chain RPC timeout"
    )
    if (
        value.get("execution_mode") != execution_mode
        or value.get("default_risk_posture")
        not in {"steady", "balanced", "opportunistic", "exploratory"}
        or minimum > maximum
        or maximum > total
        or minimum * max_open > total
        or total > risk_maximum
        or max_open > risk_open
        or (soft_drawdown < 0 and soft_drawdown != Decimal("-1"))
        or (shutdown_drawdown < 0 and shutdown_drawdown != Decimal("-1"))
        or range_minimum > range_maximum
        or range_maximum >= Decimal("100")
        or rebalance_threshold >= Decimal("100")
        or _decimal(value["max_slippage_pct"], "max slippage", True) > 100
        or _decimal(value["min_sol_reserve"], "SOL reserve", True) <= 0
        or dust < 0
        or dust >= minimum
        or onchain_before + onchain_after > 900
        or onchain_limit > 1000
        or onchain_timeout > 30
        or _positive_integer(value["executor_max_age_minutes"], "executor max age") <= 0
        or _decimal(
            value["executor_take_profit_net_pnl_ratio"],
            "executor take-profit ratio",
            True,
        )
        <= 0
        or _decimal(
            value["executor_stop_loss_net_pnl_ratio"],
            "executor stop-loss ratio",
            True,
        )
        <= 0
        or _positive_integer(value["session_max_age_minutes"], "session max age") <= 0
        or _decimal(
            value["session_take_profit_net_pnl_ratio"], "take-profit ratio", True
        )
        <= 0
        or _decimal(value["session_stop_loss_net_pnl_ratio"], "stop-loss ratio", True)
        <= 0
        or not all(
            isinstance(value[key], str) and value[key].strip()
            for key in ("server_name", "account_name")
        )
    ):
        raise ValueError("configured session safety settings are invalid")
    return value


def _session(config: Config) -> dict[str, Any]:
    number = config.controller_id.rsplit("_", 1)[1]
    path = STRATEGIES_DIR / "orca" / "sessions" / f"session_{number}" / "config.yml"
    value = yaml.safe_load(path.read_text()) if path.is_file() else None
    if not isinstance(value, dict) or set(value) != SESSION_KEYS:
        raise ValueError(
            "current live session config is missing, malformed, or has unknown keys"
        )
    return _validate_session(value, "loop")


async def _live_session(config: Config) -> dict[str, Any]:
    if not RUN_ONCE_RE.fullmatch(config.controller_id):
        return _session(config)
    from agents.lp_expert.routines.gateway_swap import _runtime

    runtime = await _runtime(config.controller_id)
    if not isinstance(runtime, dict):
        raise ValueError("live run-once instance is malformed")
    if runtime.get("execution_mode") != "run_once":
        raise ValueError("live run-once instance is not in run-once mode")
    missing = SESSION_KEYS - set(runtime)
    if missing:
        raise ValueError(
            "live run-once config authority is unavailable; active runtime omits "
            + ", ".join(sorted(missing))
        )
    return _validate_session(runtime, "run_once")


def _schema_fields(value: Any) -> set[str]:
    if not isinstance(value, dict):
        raise ValueError("LP executor schema is invalid")
    value = value.get("result", value)
    fields = value.get("fields") if isinstance(value, dict) else None
    if fields is not None:
        if not isinstance(fields, list) or not all(
            isinstance(item, dict) and isinstance(item.get("name"), str)
            for item in fields
        ):
            raise ValueError("LP executor schema fields are invalid")
        return {item["name"] for item in fields}
    properties = value.get("properties") if isinstance(value, dict) else None
    nested = value.get("schema") if isinstance(value, dict) else None
    if not isinstance(properties, dict) and isinstance(nested, dict):
        properties = nested.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("LP executor schema has no properties")
    return set(properties)


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        rows, cursor = value, None
    elif isinstance(value, dict):
        rows, cursor = value.get("data"), value.get("next_cursor")
    else:
        raise ValueError("executor search response is invalid")
    if cursor is not None:
        raise ValueError("executor search exceeded the complete 1000-row scope")
    if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
        raise ValueError("executor search rows are invalid")
    return rows


def _ownership(row: dict[str, Any]) -> tuple[str, bool, dict[str, Any]]:
    inner = row.get("config") if isinstance(row.get("config"), dict) else {}
    owners = {
        str(value).strip()
        for value in (row.get("controller_id"), inner.get("controller_id"))
        if value
    }
    status = str(row.get("status") or "").strip().upper()
    if len(owners) != 1 or not status or status in {"FAILED", "ERROR"}:
        raise ValueError("executor ownership or terminal state is unaccountable")
    active = status not in SETTLED
    if row.get("is_active") is not None and row["is_active"] is not active:
        raise ValueError("executor status and activity conflict")
    return owners.pop(), active, inner


def _balance(
    rows: list[dict[str, Any]], symbol: str, mint: str | None = None
) -> Decimal:
    found, mint_aware = [], False
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("portfolio balance row is invalid")
        row_symbol = str(row.get("token") or row.get("symbol") or "").strip()
        row_mint = str(
            row.get("mint") or row.get("token_address") or row.get("address") or ""
        ).strip()
        amount = row.get(
            "available_units",
            row.get("available", row.get("available_balance", row.get("units"))),
        )
        mint_aware |= bool(row_mint)
        if amount is not None and (
            (mint and row_mint == mint)
            or (not mint and row_symbol.upper() == symbol.upper())
        ):
            found.append(_decimal(amount, "available balance"))
    if mint and not found and not mint_aware:
        return _balance(rows, symbol)
    if len(found) != 1 or found[0] < 0:
        raise ValueError(f"portfolio balance for {symbol} is not unique")
    return found[0]


def _plan(
    config: Config, session: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = dict(config.plan)
    status = plan.pop("status", None)
    plan.pop("report_id", None)
    plan.pop("report_error", None)
    embedded = plan.pop("plan_digest", None)
    if (
        status != "planned"
        or (embedded is not None and embedded != config.plan_digest)
        or plan_digest(plan) != config.plan_digest
    ):
        raise ValueError("plan digest mismatch")
    identity, inputs, range_data, inventory, executor = (
        plan.get("identity"),
        plan.get("inputs"),
        plan.get("range"),
        plan.get("inventory"),
        plan.get("executor_config"),
    )
    if not all(
        isinstance(item, dict)
        for item in (identity, inputs, range_data, inventory, executor)
    ):
        raise ValueError("planner output structure is invalid")
    prices = [
        _decimal(range_data.get(key), key, True)
        for key in (
            "lower_limit_price",
            "lower_price",
            "upper_price",
            "upper_limit_price",
        )
    ]
    valuation_price = (prices[1] * prices[2]).sqrt()
    exposure = _decimal(
        executor.get("base_amount"), "base amount", True
    ) * valuation_price + _decimal(executor.get("quote_amount"), "quote amount", True)
    current_price = _decimal(inputs.get("current_price"), "current price", True)
    width = _decimal(inputs.get("range_half_width_pct"), "range half-width", True)
    amount = _decimal(inputs.get("amount_quote"), "plan budget", True)
    minimum = _decimal(
        session["min_quote_per_executor"], "minimum executor amount", True
    )
    maximum = _decimal(
        session["max_quote_per_executor"], "maximum executor amount", True
    )
    dust = _decimal(session["residual_base_dust_quote"], "capital dust")
    slippage = _decimal(session["max_slippage_pct"], "session slippage", True) / 100
    range_minimum = _decimal(
        session["minimum_range_half_width_pct"], "minimum range", True
    )
    range_maximum = _decimal(
        session["maximum_range_half_width_pct"], "maximum range", True
    )
    rebalance_threshold = _decimal(
        inputs.get("rebalance_threshold_pct"), "plan rebalance threshold", True
    )
    configured_rebalance_threshold = _decimal(
        session["rebalance_threshold_pct"], "configured rebalance threshold", True
    )
    rebalance_ratio = configured_rebalance_threshold / 100
    checks = (
        identity.get("quote_mint") == USDC_MINT,
        str(identity.get("quote_symbol") or "").upper() == "USDC",
        identity.get("quote_decimals") == 6,
        identity.get("price_orientation") == "token_b_per_token_a",
        identity.get("pool_address") == executor.get("pool_address"),
        identity.get("trading_pair") == executor.get("trading_pair"),
        all(
            _decimal(executor.get(key), f"executor {key}", True) == prices[index]
            for index, key in enumerate(
                (
                    "lower_limit_price",
                    "lower_price",
                    "upper_price",
                    "upper_limit_price",
                )
            )
        ),
        executor.get("type") == "lp_executor",
        executor.get("connector_name") == NETWORK,
        executor.get("lp_provider") == LP_PROVIDER,
        executor.get("swap_provider") == SWAP_PROVIDER,
        executor.get("side") == 3,
        executor.get("keep_position") is False,
        minimum <= amount <= maximum,
        all(prices[index] < prices[index + 1] for index in range(3)),
        rebalance_threshold == configured_rebalance_threshold,
        prices[0] == prices[1] * (1 - rebalance_ratio),
        prices[3] == prices[2] * (1 + rebalance_ratio),
        prices[1] < current_price < prices[2],
        valuation_price >= current_price,
        range_minimum
        <= (current_price - prices[1]) / current_price * 100
        <= range_maximum,
        range_minimum
        <= (prices[2] - current_price) / current_price * 100
        <= range_maximum,
        range_minimum <= width <= range_maximum,
        amount - amount * slippage - dust <= exposure <= amount,
        _decimal(inputs.get("max_slippage_pct"), "plan slippage", True)
        <= _decimal(session["max_slippage_pct"], "session slippage"),
        inventory.get("inventory_ready") is True,
        _decimal(
            inventory.get("max_usdc_for_preparation_swap"),
            "preparation quote cap",
        )
        == 0,
        _decimal(
            inventory.get("preparation_slippage_headroom_quote"),
            "preparation headroom",
        )
        == 0,
        _decimal(inputs.get("attributed_base_amount"), "plan attribution")
        == config.attributed_base_amount,
        _decimal(inventory.get("base_amount"), "base amount", True)
        == _decimal(executor.get("base_amount"), "executor base", True),
        _decimal(inventory.get("quote_amount"), "quote amount", True)
        == _decimal(executor.get("quote_amount"), "executor quote", True),
        _decimal(executor.get("base_amount"), "base amount")
        <= config.attributed_base_amount,
        config.preparation_quote_spent
        + _decimal(executor.get("quote_amount"), "executor quote", True)
        <= amount,
    )
    if not all(checks):
        raise ValueError("plan violates the configured lp_expert contract")
    return plan, executor


def _executor_id(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    ids = {str(value[key]).strip() for key in ("executor_id", "id") if value.get(key)}
    return ids.pop() if len(ids) == 1 else None


async def _result(
    status: str,
    config: Config,
    *,
    trace: list[dict[str, Any]] | None = None,
    **details: Any,
) -> str:
    payload = {"status": status, "operation_id": config.operation_id, **details}
    plan = config.plan if isinstance(config.plan, dict) else {}
    identity = plan.get("identity") if isinstance(plan.get("identity"), dict) else {}
    inputs = plan.get("inputs") if isinstance(plan.get("inputs"), dict) else {}
    range_data = plan.get("range") if isinstance(plan.get("range"), dict) else {}
    evidence = [
        {
            "kind": "plan",
            "current_price": inputs.get("current_price"),
            "amount_quote": inputs.get("amount_quote"),
            "lower_price": range_data.get("lower_price"),
            "upper_price": range_data.get("upper_price"),
            "attributed_base_amount": config.attributed_base_amount,
            "preparation_quote_spent": config.preparation_quote_spent,
        }
    ]
    evidence.extend(trace or [])
    if details.get("executor_id"):
        evidence.append(
            {
                "kind": "executor",
                "executor_id": details["executor_id"],
                "pool_address": details.get("pool_address"),
                "retry_allowed": details.get("retry_allowed"),
            }
        )
    if details.get("reason"):
        evidence.append({"kind": "error", "reason": details["reason"]})
    payload = await reports.attach_trace(
        payload,
        title="Orca LP Create Guard",
        source="lp_create_guard",
        status=status,
        summary={
            "controller_id": config.controller_id,
            "operation_id": config.operation_id,
            "pool_address": details.get("pool_address") or identity.get("pool_address"),
            "plan_digest": config.plan_digest,
        },
        evidence=evidence,
    )
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


async def run(config: Config, context: Any) -> str:
    trace: list[dict[str, Any]] = []
    try:
        from config_manager import get_config_manager

        session = await _live_session(config)
        trace.append(
            {
                "kind": "session_limits",
                "execution_mode": session["execution_mode"],
                "total_amount_quote": session["total_amount_quote"],
                "max_open_executors": session["max_open_executors"],
                "max_slot_deployments_per_tick": session[
                    "max_slot_deployments_per_tick"
                ],
                "min_quote_per_executor": session["min_quote_per_executor"],
                "max_quote_per_executor": session["max_quote_per_executor"],
                "min_sol_reserve": session["min_sol_reserve"],
                "executor_max_age_minutes": session["executor_max_age_minutes"],
                "executor_take_profit_net_pnl_ratio": session[
                    "executor_take_profit_net_pnl_ratio"
                ],
                "executor_stop_loss_net_pnl_ratio": session[
                    "executor_stop_loss_net_pnl_ratio"
                ],
            }
        )
        plan, executor = _plan(config, session)
        executor = {**executor, "controller_id": config.controller_id}
        client = await get_config_manager().get_client(session["server_name"])
        network = await client.gateway.get_network_config(NETWORK)
        if (
            not isinstance(network, dict)
            or network.get("default_wallet") != config.wallet_address
        ):
            raise ValueError("supplied wallet is not the current Solana default wallet")
        schema_fields = _schema_fields(
            await client.executors.get_executor_config_schema("lp_executor")
        )
        missing = set(executor) - schema_fields - {"type"}
        trace.append(
            {
                "kind": "schema",
                "available_fields": len(schema_fields),
                "missing_planned_fields": ", ".join(sorted(missing)) or "-",
            }
        )
        if missing:
            raise ValueError(
                f"LP executor schema lacks planned fields: {sorted(missing)}"
            )
        # ponytail: one maximum-size page keeps the guard stateless; any cursor fails closed.
        search = await client.executors.search_executors(
            account_names=[session["account_name"]],
            connector_names=[NETWORK],
            executor_types=["lp_executor"],
            limit=1000,
            cursor=None,
        )
        active = []
        active_exposure = Decimal(0)
        minimum = _decimal(
            session["min_quote_per_executor"], "minimum executor amount", True
        )
        maximum = _decimal(
            session["max_quote_per_executor"], "maximum executor amount", True
        )
        dust = _decimal(session["residual_base_dust_quote"], "capital dust")
        slippage = _decimal(session["max_slippage_pct"], "session slippage", True) / 100
        for row in _rows(search):
            owner, is_active, current = _ownership(row)
            if not is_active:
                continue
            if owner != config.controller_id:
                raise ValueError(
                    "another live LP executor overlaps this wallet/account scope"
                )
            if (
                current.get("type") != "lp_executor"
                or current.get("connector_name") != NETWORK
                or current.get("lp_provider") != LP_PROVIDER
                or current.get("keep_position") is not False
                or not str(current.get("pool_address") or "").strip()
            ):
                raise ValueError(
                    "active executor does not prove the lp_expert contract"
                )
            current_exposure = _decimal(
                current.get("base_amount"), "executor base", True
            ) * (
                _decimal(current.get("lower_price"), "executor lower", True)
                * _decimal(current.get("upper_price"), "executor upper", True)
            ).sqrt() + _decimal(
                current.get("quote_amount"), "executor quote", True
            )
            if not (minimum - minimum * slippage - dust <= current_exposure <= maximum):
                raise ValueError(
                    "active executor exposure is outside configured per-slot bounds"
                )
            active.append(current)
            active_exposure += current_exposure
        trace.append(
            {
                "kind": "capacity",
                "active_executors": len(active),
                "active_exposure_quote": active_exposure,
                "active_pools": ", ".join(str(item["pool_address"]) for item in active)
                or "-",
            }
        )
        if len(active) >= _positive_integer(
            session["max_open_executors"], "max open executors"
        ) or any(item["pool_address"] == executor["pool_address"] for item in active):
            raise ValueError("executor capacity or one-position-per-pool rule failed")
        if active_exposure + _decimal(
            plan["inputs"]["amount_quote"], "plan budget", True
        ) > _decimal(session["total_amount_quote"], "total amount", True):
            raise ValueError("configured total capital would be exceeded")
        state = await client.portfolio.get_state(
            account_names=[session["account_name"]],
            connector_names=[NETWORK],
            refresh=True,
        )
        rows = (
            state.get(session["account_name"], {}).get(NETWORK)
            if isinstance(state, dict)
            else None
        )
        if not isinstance(rows, list):
            raise ValueError("scoped portfolio balances were not returned")
        base, base_mint = str(plan["identity"]["base_symbol"]), str(
            plan["identity"]["base_mint"]
        )
        required_base = _decimal(executor["base_amount"], "base amount", True)
        sol_needed = _decimal(session["min_sol_reserve"], "SOL reserve", True) + (
            required_base if base.upper() == "SOL" else 0
        )
        available_sol = _balance(rows, "SOL")
        available_base = _balance(rows, base, base_mint)
        available_quote = _balance(rows, "USDC", USDC_MINT)
        required_quote = _decimal(executor["quote_amount"], "quote amount", True)
        trace.append(
            {
                "kind": "balances",
                "base_symbol": base,
                "available_sol": available_sol,
                "required_sol": sol_needed,
                "available_base": available_base,
                "required_base": required_base,
                "available_usdc": available_quote,
                "required_usdc": required_quote,
            }
        )
        if (
            available_sol < sol_needed
            or available_base < required_base
            or available_quote < required_quote
        ):
            raise ValueError(
                "scoped balances do not cover plan inventory and SOL reserve"
            )
    except Exception as exc:
        return await _result(
            "rejected",
            config,
            trace=trace,
            reason=f"{type(exc).__name__}: {exc}",
        )

    task = asyncio.create_task(
        client.executors.create_executor(
            executor_config=executor,
            account_name=session["account_name"],
            controller_id=config.controller_id,
        )
    )
    try:
        response = await asyncio.shield(task)
    except asyncio.CancelledError:
        return await _result(
            "uncertain",
            config,
            trace=trace,
            reason="create wait was cancelled",
            retry_allowed=False,
        )
    except Exception as exc:
        return await _result(
            "uncertain",
            config,
            trace=trace,
            reason=f"{type(exc).__name__}: {exc}",
            retry_allowed=False,
        )
    executor_id = _executor_id(response)
    if executor_id is None:
        return await _result(
            "uncertain",
            config,
            trace=trace,
            reason="create response lacked one executor id",
            retry_allowed=False,
        )
    return await _result(
        "submitted",
        config,
        trace=trace,
        controller_id=config.controller_id,
        executor_id=executor_id,
        pool_address=executor["pool_address"],
        retry_allowed=False,
    )
