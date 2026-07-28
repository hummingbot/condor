import asyncio
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from agents.lpmaxxing.routines import _orca_evidence as evidence
from agents.lpmaxxing.routines import _orca_policy as orca_policy
from agents.lpmaxxing.routines import orca_pool_scan
from agents.lpmaxxing.routines._orca_contracts import NextAction, attach_outcome

try:
    from routines.base import RoutineResult
except ModuleNotFoundError:
    sys.path.append(str(PROJECT_ROOT))
    from routines.base import RoutineResult


CATEGORY = "Orca LP Agent"
CANONICAL_USDC_MINT = orca_policy.CANONICAL_USDC_MINT
LIVE_PROFILES = orca_policy.LIVE_PROFILES
ACTIVE_EXECUTOR_STATES = {
    "RUNNING",
    "ACTIVE",
    "OPEN",
    "OPENING",
    "CLOSING",
    "SHUTTING_DOWN",
    "SWAPPING",
}
TERMINAL_EXECUTOR_STATES = {
    "COMPLETE",
    "COMPLETED",
    "TERMINATED",
    "STOPPED",
    "CANCELED",
    "CANCELLED",
    "FAILED",
}
SESSION_SCHEMA_VERSION = 2


from agents.lpmaxxing.routines._orca_lifecycle import (
    _number,
    _parse_timestamp,
    _session_dir,
    _text,
    _utc_now,
    ensure_session_state,
    lifecycle_lock,
    load_lifecycle_state,
    load_session_state,
    save_lifecycle_state,
    session_execution_mode,
    session_risk_profile,
    session_total_amount_quote,
)


class Config(BaseModel):
    """Validate one Orca candidate and produce a read-only LP executor plan."""

    model_config = ConfigDict(extra="forbid")

    execution_mode: str = Field(default="dry_run", description="dry_run or loop")
    controller_id: str = Field(
        default="", description="Dynamic controller id from the current tick"
    )
    selected_candidate: dict[str, Any] = Field(
        default_factory=dict, description="Selected orca_pool_scan candidate"
    )
    gateway_pool_info: dict[str, Any] = Field(
        default_factory=dict,
        description="Normalized Gateway evidence: pool_address, base_mint, quote_mint, current_price",
    )
    wallet_balances: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Exact scoped balances with symbol and available fields",
    )
    active_executors: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Current executors for the dynamic controller",
    )
    executor_lookup_succeeded: bool = Field(
        default=False,
        description="Whether controller executor lookup completed successfully",
    )
    wallet_lookup_succeeded: bool = Field(
        default=False,
        description="Whether scoped wallet balance lookup completed successfully",
    )
    fetch_wallet_balances: bool = Field(
        default=True,
        description="Fetch exact scoped balances directly from the Hummingbot API",
    )
    wallet_account_name: str = Field(
        default="master_account", description="Account used for the balance lookup"
    )
    wallet_connector_name: str = Field(
        default="solana-mainnet-beta",
        description="Network connector used for the balance lookup",
    )
    api_errors: list[str] = Field(
        default_factory=list, description="Sanitized executor or wallet lookup errors"
    )
    total_amount_quote: float = Field(
        default=10, gt=0, description="Requested LP budget"
    )
    min_sol_fee_buffer: float = Field(
        default=0.05, ge=0, description="SOL retained for fees and rent"
    )
    max_price_deviation_ratio: float = Field(
        default=0.01, ge=0, le=1, description="Maximum Gateway/scanner price deviation"
    )
    connector_name: str = Field(
        default="solana-mainnet-beta", description="LP executor connector"
    )
    lp_provider: str = Field(default="orca/clmm", description="LP executor provider")

    @field_validator("execution_mode", mode="before")
    @classmethod
    def _mode(cls, value: Any) -> str:
        return str(value or "").strip().lower().replace("-", "_")

    @field_validator("controller_id", mode="before")
    @classmethod
    def _controller(cls, value: Any) -> str:
        return str(value or "").strip()


def _strict_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _strict_integer(value: Any) -> int | None:
    number = _strict_number(value)
    return int(number) if number is not None and number.is_integer() else None


candidate_age_seconds = orca_policy.candidate_age_seconds
candidate_is_fresh = orca_policy.candidate_is_fresh


def _numbers_match(actual: Any, expected: Any) -> bool:
    if expected is None:
        return actual is None
    actual_number = _strict_number(actual)
    expected_number = _strict_number(expected)
    return (
        actual_number is not None
        and expected_number is not None
        and math.isclose(actual_number, expected_number, rel_tol=1e-9, abs_tol=1e-12)
    )


def _values_match(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _values_match(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and actual == expected
    if isinstance(expected, bool) or expected is None or isinstance(expected, str):
        return actual == expected
    return _numbers_match(actual, expected)


def _contains_executable_bounds(value: Any) -> bool:
    forbidden = {
        "lower_price",
        "upper_price",
        "lower_limit_price",
        "upper_limit_price",
    }
    if isinstance(value, dict):
        return bool(forbidden.intersection(value)) or any(
            _contains_executable_bounds(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_executable_bounds(item) for item in value)
    return False


def _candidate_revalidation(
    candidate: dict[str, Any], budget: float, risk_profile: str
) -> tuple[bool, dict[str, Any], dict[str, Any] | None]:
    token_a = (
        candidate.get("token_a") if isinstance(candidate.get("token_a"), dict) else {}
    )
    token_b = (
        candidate.get("token_b") if isinstance(candidate.get("token_b"), dict) else {}
    )
    normalized = {
        "malformed": False,
        "address": _text(candidate.get("pool_address")),
        "token_a": {
            "symbol": _text(token_a.get("symbol")),
            "mint": _text(token_a.get("mint")),
            "decimals": _strict_integer(token_a.get("decimals")),
        },
        "token_b": {
            "symbol": _text(token_b.get("symbol")),
            "mint": _text(token_b.get("mint")),
            "decimals": _strict_integer(token_b.get("decimals")),
        },
        "scanner_price": _strict_number(candidate.get("scanner_price")),
        "tvl_usd": _strict_number(candidate.get("tvl_usd")),
        "net_price_change_24h": orca_policy.decimal_ratio(
            candidate.get("net_price_change_24h")
        ),
        "price_delta_24h_raw": candidate.get("net_price_change_24h"),
        "fee_rate_raw": _strict_number(
            candidate.get("fee_rate_raw", candidate.get("fee_rate"))
        ),
        "adaptive_fee_enabled": (
            candidate.get("adaptive_fee_enabled")
            if isinstance(candidate.get("adaptive_fee_enabled"), bool)
            else None
        ),
        "fee_tier_index": _strict_integer(candidate.get("fee_tier_index")),
        "tick_spacing": _strict_integer(candidate.get("tick_spacing")),
        "has_warning": (
            candidate.get("has_warning")
            if isinstance(candidate.get("has_warning"), bool)
            else None
        ),
        "source_categories": candidate.get("source_categories"),
        "source_lenses": candidate.get("source_lenses"),
    }
    for window in orca_policy.LIVE_STATS:
        normalized[f"volume_{window}_usd"] = _strict_number(
            candidate.get(f"volume_{window}_usd")
        )
        normalized[f"fees_{window}_usd"] = _strict_number(
            candidate.get(f"fees_{window}_usd")
        )

    return orca_policy.revalidate_candidate_contract(
        candidate,
        normalized,
        budget,
        risk_profile,
        _contains_executable_bounds,
        _values_match,
    )


def _executable_range(
    center_price: float, half_width: float
) -> dict[str, float] | None:
    if center_price <= 0 or not 0 < half_width < 1:
        return None
    lower = center_price * (1.0 - half_width)
    upper = center_price * (1.0 + half_width)
    limit_buffer = min(0.015, max(0.003, half_width * 0.5))
    lower_limit = lower * (1.0 - limit_buffer)
    upper_limit = upper * (1.0 + limit_buffer)
    values = (lower_limit, lower, center_price, upper, upper_limit)
    if not all(math.isfinite(value) and value > 0 for value in values):
        return None
    if not lower_limit < lower < center_price < upper < upper_limit:
        return None
    return {
        "center_price": center_price,
        "provisional_half_width": half_width,
        "limit_buffer": limit_buffer,
        "lower_price": lower,
        "upper_price": upper,
        "lower_limit_price": lower_limit,
        "upper_limit_price": upper_limit,
    }


def _candidate_tokens(
    candidate: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    token_a = (
        candidate.get("token_a") if isinstance(candidate.get("token_a"), dict) else {}
    )
    token_b = (
        candidate.get("token_b") if isinstance(candidate.get("token_b"), dict) else {}
    )
    return token_a, token_b


def _candidate_identity(candidate: dict[str, Any]) -> dict[str, Any]:
    token_a, token_b = _candidate_tokens(candidate)
    range_plan = (
        candidate.get("range_plan")
        if isinstance(candidate.get("range_plan"), dict)
        else {}
    )
    return {
        "observed_at": _text(candidate.get("observed_at")),
        "risk_profile": _text(candidate.get("risk_profile")),
        "pool_address": _text(candidate.get("pool_address")),
        "price_orientation": _text(candidate.get("price_orientation")),
        "token_a_mint": _text(token_a.get("mint")),
        "token_b_mint": _text(token_b.get("mint")),
        "preset": _text(range_plan.get("preset")),
        "provisional_half_width": _number(range_plan.get("provisional_half_width")),
    }


def _inventory_plan(
    budget: float,
    price: float | None,
    lower: float | None,
    upper: float | None,
    base_decimals: int | None,
    quote_decimals: int | None,
) -> dict[str, float] | None:
    if (
        price is None
        or lower is None
        or upper is None
        or base_decimals is None
        or quote_decimals is None
        or not 0 <= base_decimals <= 18
        or not 0 <= quote_decimals <= 18
        or not lower < price < upper
    ):
        return None
    sqrt_p = math.sqrt(price)
    sqrt_a = math.sqrt(lower)
    sqrt_b = math.sqrt(upper)
    base_per_l = (sqrt_b - sqrt_p) / (sqrt_p * sqrt_b)
    quote_per_l = sqrt_p - sqrt_a
    denominator = base_per_l * price + quote_per_l
    if denominator <= 0:
        return None
    liquidity = budget / denominator
    base_amount = Decimal(str(base_per_l * liquidity)).quantize(
        Decimal(1).scaleb(-base_decimals), rounding=ROUND_DOWN
    )
    quote_amount = Decimal(str(quote_per_l * liquidity)).quantize(
        Decimal(1).scaleb(-quote_decimals), rounding=ROUND_DOWN
    )
    if base_amount <= 0 or quote_amount <= 0:
        return None
    return {
        "base_amount": float(base_amount),
        "quote_amount": float(quote_amount),
    }


def _check(name: str, passed: bool, observed: Any, required: Any) -> dict[str, Any]:
    return {"check": name, "passed": passed, "observed": observed, "required": required}


def _evaluate(
    config: Config,
    require_session: bool = False,
    now: datetime | None = None,
    existing_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate = config.selected_candidate
    gateway = config.gateway_pool_info
    base_token, quote_token = _candidate_tokens(candidate)
    checks: list[dict[str, Any]] = []
    now = now or datetime.now(timezone.utc)
    existing_state = existing_state or {}

    mode_ok = config.execution_mode in {"dry_run", "loop"}
    expected_mode = None
    try:
        expected_mode = session_execution_mode(config.controller_id)
    except Exception:
        expected_mode = None if require_session else config.execution_mode
    mode_ok = mode_ok and config.execution_mode == expected_mode
    controller_ok = (
        bool(config.controller_id) and config.controller_id != "lpmaxxing_orca"
    )
    checks.append(
        _check(
            "runtime_identity",
            mode_ok and controller_ok,
            {
                "mode": config.execution_mode,
                "session_mode": expected_mode,
                "controller_id": config.controller_id,
            },
            "mode matching SESSION_MODE (dry_run or loop) and dynamic controller id",
        )
    )

    has_session = _session_dir(config.controller_id) is not None
    active_session_profile = None
    try:
        active_session_profile = session_risk_profile(config.controller_id)
    except Exception:
        if require_session and config.execution_mode == "loop":
            active_session_profile = None
    candidate_profile = _text(candidate.get("risk_profile"))
    expected_profile = active_session_profile or candidate_profile
    profile_ok = (
        candidate_profile in LIVE_PROFILES
        and expected_profile == candidate_profile
        and (config.execution_mode != "loop" or active_session_profile is not None)
    )
    checks.append(
        _check(
            "session_risk_profile",
            profile_ok,
            {
                "candidate": candidate_profile,
                "active_session": active_session_profile,
            },
            "candidate profile matching the current structured live risk_profile",
        )
    )

    scan_age = candidate_age_seconds(candidate, now)
    freshness_ok = scan_age is not None and -30.0 <= scan_age <= 600.0
    checks.append(
        _check(
            "scan_freshness",
            freshness_ok,
            scan_age,
            "-30 <= age_seconds <= 600",
        )
    )

    candidate_valid, candidate_revalidation, recomputed_candidate = (
        _candidate_revalidation(candidate, config.total_amount_quote, candidate_profile)
        if candidate_profile in LIVE_PROFILES
        else (False, {"errors": ["invalid live risk profile"]}, None)
    )
    checks.append(
        _check(
            "scanner_candidate_contract",
            candidate_valid,
            candidate_revalidation,
            "complete untampered scanner evidence and deterministic policy arithmetic",
        )
    )

    token_a_decimals = _strict_integer(base_token.get("decimals"))
    token_b_decimals = _strict_integer(quote_token.get("decimals"))
    canonical_quote_ok = (
        _text(quote_token.get("mint")) == CANONICAL_USDC_MINT
        and _text(quote_token.get("symbol")).upper() == "USDC"
        and token_b_decimals == 6
        and bool(_text(base_token.get("mint")))
        and _text(base_token.get("mint")) != _text(quote_token.get("mint"))
        and token_a_decimals is not None
        and candidate.get("price_orientation") == "token_b_per_token_a"
    )
    checks.append(
        _check(
            "canonical_quote_orientation",
            canonical_quote_ok,
            {
                "token_a_mint": _text(base_token.get("mint")),
                "token_b_mint": _text(quote_token.get("mint")),
                "token_b_symbol": _text(quote_token.get("symbol")),
                "token_b_decimals": token_b_decimals,
                "price_orientation": candidate.get("price_orientation"),
            },
            "canonical USDC token B with token_b_per_token_a orientation",
        )
    )

    controller_executors = [
        executor
        for executor in config.active_executors
        if _text(executor.get("controller_id")) == config.controller_id
    ]
    unresolved_executor_records = sum(
        1
        for executor in config.active_executors
        if _text(executor.get("controller_id")) != config.controller_id
        or not _text(executor.get("status"))
    )
    executor_states = [
        _text(item.get("status")).upper() for item in controller_executors
    ]
    executor_evidence_ok = (
        config.executor_lookup_succeeded
        and not config.active_executors
        and unresolved_executor_records == 0
        and all(executor_states)
    )
    checks.append(
        _check(
            "single_executor",
            executor_evidence_ok,
            {
                "lookup_succeeded": config.executor_lookup_succeeded,
                "matching_executors": len(controller_executors),
                "unresolved_records": unresolved_executor_records,
                "states": executor_states,
            },
            "successful controller lookup with zero executors",
        )
    )

    candidate_pool = _text(candidate.get("pool_address"))
    gateway_pool = _text(gateway.get("pool_address"))
    candidate_base_mint = _text(base_token.get("mint"))
    candidate_quote_mint = _text(quote_token.get("mint"))
    gateway_base_mint = _text(gateway.get("base_mint"))
    gateway_quote_mint = _text(gateway.get("quote_mint"))
    scan_price = _number(candidate.get("scanner_price"))
    gateway_price = _number(gateway.get("current_price"))
    price_deviation = (
        abs(gateway_price - scan_price) / scan_price
        if gateway_price is not None and scan_price is not None and scan_price > 0
        else None
    )
    pool_ok = bool(candidate_pool) and candidate_pool == gateway_pool
    mints_ok = (
        bool(candidate_base_mint)
        and candidate_base_mint == gateway_base_mint
        and bool(candidate_quote_mint)
        and candidate_quote_mint == gateway_quote_mint
    )
    price_ok = (
        price_deviation is not None
        and price_deviation <= config.max_price_deviation_ratio
    )
    checks.append(
        _check(
            "gateway_pool",
            pool_ok and mints_ok and price_ok,
            {
                "pool_address": gateway_pool,
                "base_mint": gateway_base_mint,
                "quote_mint": gateway_quote_mint,
                "current_price": gateway_price,
                "price_deviation_ratio": price_deviation,
            },
            {
                "pool_address": candidate_pool,
                "base_mint": candidate_base_mint,
                "quote_mint": candidate_quote_mint,
                "max_price_deviation_ratio": config.max_price_deviation_ratio,
            },
        )
    )

    range_plan = (
        recomputed_candidate.get("range_plan")
        if isinstance(recomputed_candidate, dict)
        and isinstance(recomputed_candidate.get("range_plan"), dict)
        else {}
    )
    provisional_half_width = _number(range_plan.get("provisional_half_width"))
    executable_range = (
        _executable_range(gateway_price, provisional_half_width)
        if gateway_price is not None and provisional_half_width is not None
        else None
    )
    checks.append(
        _check(
            "gateway_centered_range",
            executable_range is not None,
            executable_range,
            "positive finite requested bounds centered on the fresh Gateway price",
        )
    )

    try:
        active_session_budget = session_total_amount_quote(config.controller_id)
    except Exception:
        active_session_budget = None
    budget_ok = config.total_amount_quote > 0 and (
        (not has_session and active_session_budget is None)
        or config.total_amount_quote == active_session_budget
    )
    checks.append(
        _check(
            "session_budget",
            budget_ok,
            {
                "requested": config.total_amount_quote,
                "active_session": active_session_budget,
            },
            "positive total_amount_quote matching the active session",
        )
    )

    inventory = _inventory_plan(
        config.total_amount_quote,
        gateway_price,
        executable_range.get("lower_price") if executable_range else None,
        executable_range.get("upper_price") if executable_range else None,
        token_a_decimals,
        token_b_decimals,
    )
    available_base = evidence.balance_for_token(config.wallet_balances, base_token)
    available_quote = evidence.balance_for_token(config.wallet_balances, quote_token)
    available_sol = evidence.balance_for_token(
        config.wallet_balances, {"symbol": "SOL"}
    )
    required_base = inventory.get("base_amount") if inventory else None
    required_quote = inventory.get("quote_amount") if inventory else None
    sol_spend = 0.0
    if _text(base_token.get("symbol")).upper() == "SOL" and required_base is not None:
        sol_spend += required_base
    if _text(quote_token.get("symbol")).upper() == "SOL" and required_quote is not None:
        sol_spend += required_quote
    inventory_ok = (
        config.wallet_lookup_succeeded
        and bool(config.wallet_account_name)
        and config.wallet_connector_name == config.connector_name
        and inventory is not None
        and available_base is not None
        and required_base is not None
        and available_base >= required_base
        and available_quote is not None
        and required_quote is not None
        and available_quote >= required_quote
        and available_sol is not None
        and available_sol - sol_spend >= config.min_sol_fee_buffer
    )
    checks.append(
        _check(
            "wallet_inventory",
            inventory_ok,
            {
                "lookup_succeeded": config.wallet_lookup_succeeded,
                "account": config.wallet_account_name,
                "connector": config.wallet_connector_name,
                "base": available_base,
                "quote": available_quote,
                "sol": available_sol,
            },
            {
                "base": required_base,
                "quote": required_quote,
                "sol_remaining": config.min_sol_fee_buffer,
            },
        )
    )

    failed_checks = [item["check"] for item in checks if not item["passed"]]
    ready = not failed_checks
    executor_plan = None
    if ready and inventory and executable_range:
        executor_plan = {
            "action": "create",
            "executor_type": "lp_executor",
            "executor_config": {
                "controller_id": config.controller_id,
                "connector_name": config.connector_name,
                "lp_provider": config.lp_provider,
                "trading_pair": candidate.get("trading_pair"),
                "pool_address": candidate_pool,
                "lower_price": executable_range["lower_price"],
                "upper_price": executable_range["upper_price"],
                "lower_limit_price": executable_range["lower_limit_price"],
                "upper_limit_price": executable_range["upper_limit_price"],
                "side": 3,
                "base_amount": inventory["base_amount"],
                "quote_amount": inventory["quote_amount"],
                "total_amount_quote": config.total_amount_quote,
                "keep_position": False,
            },
        }

    available_base_for_plan = available_base or 0.0
    base_shortfall = (
        max(0.0, required_base - available_base_for_plan)
        if required_base is not None
        else None
    )
    estimated_quote_spend = (
        base_shortfall * gateway_price
        if base_shortfall is not None and gateway_price is not None
        else None
    )
    quote_symbol = _text(quote_token.get("symbol")).upper()
    quote_buffer = 0.01
    quote_required_after_swap = (
        required_quote + quote_buffer if required_quote is not None else None
    )
    quote_funds_ok = (
        available_quote is not None
        and estimated_quote_spend is not None
        and quote_required_after_swap is not None
        and available_quote >= estimated_quote_spend + quote_required_after_swap
    )
    sol_funds_ok = (
        available_sol is not None and available_sol >= config.min_sol_fee_buffer
    )
    if quote_symbol == "SOL" and quote_funds_ok:
        sol_funds_ok = (
            available_sol is not None
            and estimated_quote_spend is not None
            and required_quote is not None
            and available_sol - estimated_quote_spend - required_quote
            >= config.min_sol_fee_buffer
        )
    rebalance_status = _text(
        (existing_state.get("rebalance") or {}).get("status")
    ).upper()
    rebalance_consumed = rebalance_status in {"CONFIRMED", "NOT_REQUIRED"} or _text(
        existing_state.get("phase")
    ) in {"rebalance_confirmed", "rebalance_confirmed_waiting_balance"}
    rebalance_eligible = (
        set(failed_checks) == {"wallet_inventory"}
        and not rebalance_consumed
        and not config.api_errors
        and config.wallet_lookup_succeeded
        and inventory is not None
        and base_shortfall is not None
        and base_shortfall > 1e-12
        and quote_funds_ok
        and sol_funds_ok
    )
    rebalance_plan = None
    if rebalance_eligible:
        rebalance_plan = {
            "trading_pair": candidate.get("trading_pair"),
            "pool_address": candidate_pool,
            "base_mint": candidate_base_mint,
            "quote_mint": candidate_quote_mint,
            "base_symbol": base_token.get("symbol"),
            "quote_symbol": quote_token.get("symbol"),
            "base_decimals": base_token.get("decimals"),
            "quote_decimals": quote_token.get("decimals"),
            "base_shortfall": round(base_shortfall, 8),
            "required_base": required_base,
            "required_quote": required_quote,
            "estimated_quote_spend": round(estimated_quote_spend or 0.0, 8),
            "available_base": available_base_for_plan,
            "available_quote": available_quote,
            "available_sol": available_sol,
            "quote_buffer": quote_buffer,
            "min_sol_fee_buffer": config.min_sol_fee_buffer,
            "current_price": gateway_price,
            "lower_price": (
                executable_range.get("lower_price") if executable_range else None
            ),
            "upper_price": (
                executable_range.get("upper_price") if executable_range else None
            ),
            "total_amount_quote": config.total_amount_quote,
            "candidate_identity": _candidate_identity(candidate),
            "scan_age_seconds": scan_age,
        }

    lifecycle_state = None
    if executor_plan:
        lifecycle_state = {
            "controller_id": config.controller_id,
            "phase": "preflight_ready",
            "preset": range_plan.get("preset"),
            "selected_candidate": candidate,
            "gateway_pool_info": gateway,
            "executable_range": executable_range,
            "total_amount_quote": config.total_amount_quote,
            "wallet_account_name": config.wallet_account_name,
            "wallet_connector_name": config.wallet_connector_name,
            "executor_plan": executor_plan,
            "executor_id": None,
            "close_reason": None,
        }
    elif rebalance_plan:
        lifecycle_state = {
            "controller_id": config.controller_id,
            "phase": "rebalance_required",
            "preset": range_plan.get("preset"),
            "selected_candidate": candidate,
            "gateway_pool_info": gateway,
            "executable_range": executable_range,
            "total_amount_quote": config.total_amount_quote,
            "wallet_account_name": config.wallet_account_name,
            "wallet_connector_name": config.wallet_connector_name,
            "rebalance_plan": rebalance_plan,
            "rebalance": {"status": "required", "submission_attempt": 0},
            "executor_plan": None,
            "executor_id": None,
            "close_reason": None,
        }

    return {
        "decision": "ready" if ready else "blocked",
        "failed_checks": failed_checks,
        "checks": checks,
        "executor_plan": executor_plan,
        "rebalance_plan": rebalance_plan,
        "lifecycle_state": lifecycle_state,
        "evidence": {
            "candidate": candidate,
            "candidate_revalidation": candidate_revalidation,
            "scan_age_seconds": scan_age,
            "session_risk_profile": active_session_profile,
            "fresh_gateway_pool_info": gateway,
            "executable_range": executable_range,
            "inventory_plan": inventory,
            "rebalance_consumed": rebalance_consumed,
            "matched_wallet_balances": {
                "base": available_base,
                "quote": available_quote,
                "sol": available_sol,
            },
            "executor_lookup": {
                "succeeded": config.executor_lookup_succeeded,
                "matching_count": len(controller_executors),
                "states": executor_states,
            },
            "wallet_lookup": {
                "succeeded": config.wallet_lookup_succeeded,
                "account": config.wallet_account_name,
                "connector": config.wallet_connector_name,
            },
        },
        "warnings": [],
        "errors": config.api_errors,
    }


def _sanitized_input(config: Config, payload: dict[str, Any]) -> dict[str, Any]:
    value = config.model_dump(
        mode="json", exclude={"wallet_balances", "active_executors"}
    )
    value["matched_wallet_balances"] = payload["evidence"]["matched_wallet_balances"]
    value["executor_lookup"] = payload["evidence"]["executor_lookup"]
    return value


async def _with_live_evidence(config: Config, context: Any) -> Config:
    if not config.fetch_wallet_balances and config.execution_mode != "loop":
        return config

    errors = list(config.api_errors)
    try:
        if config.execution_mode == "loop":
            if session_execution_mode(config.controller_id) != "loop":
                raise ValueError(
                    "execution_mode does not match the current SESSION_MODE"
                )
            if (
                load_session_state(config.controller_id).get("session_status")
                != "running"
            ):
                raise ValueError("current Orca session is not accepting a new position")
            profile = session_risk_profile(config.controller_id)
            if profile != _text(config.selected_candidate.get("risk_profile")):
                raise ValueError(
                    "candidate risk_profile does not match the current session"
                )
            if not candidate_is_fresh(config.selected_candidate):
                raise ValueError("scanner candidate is stale or future-dated")
            candidate_valid, _, _ = _candidate_revalidation(
                config.selected_candidate, config.total_amount_quote, profile
            )
            if not candidate_valid:
                raise ValueError("scanner candidate contract revalidation failed")
            if (
                session_total_amount_quote(config.controller_id)
                != config.total_amount_quote
            ):
                raise ValueError("total_amount_quote does not match the active session")
        from config_manager import get_client

        chat_id = getattr(context, "_chat_id", None) if context is not None else None
        client = await get_client(chat_id or 0, context=context)
        if not client:
            raise RuntimeError("no Hummingbot API server available")
        if config.execution_mode == "loop":
            if not hasattr(client, "executors"):
                raise RuntimeError("executor API is unavailable")
            executor_rows = await evidence.search_controller_executors(
                client, config.controller_id
            )
            states = [_text(row.get("status")).upper() for row in executor_rows]
            if any(
                state not in ACTIVE_EXECUTOR_STATES | TERMINAL_EXECUTOR_STATES
                for state in states
            ):
                raise ValueError("executor search returned an unknown state")
            if any(
                evidence.executor_controller_id(row) != config.controller_id
                for row in executor_rows
            ):
                raise ValueError("executor search returned unowned records")
            archived_executor_ids = set(
                (load_session_state(config.controller_id).get("completed") or {}).get(
                    "executor_ids"
                )
                or []
            )
            unarchived_terminal_ids = [
                evidence.executor_id(row)
                for row, state in zip(executor_rows, states, strict=True)
                if state in TERMINAL_EXECUTOR_STATES
                and evidence.executor_id(row) not in archived_executor_ids
            ]
            if unarchived_terminal_ids:
                raise ValueError(
                    "executor search returned terminal records without position archives"
                )
            active_executors = [
                row
                for row, state in zip(executor_rows, states, strict=True)
                if state in ACTIVE_EXECUTOR_STATES and row.get("is_active") is not False
            ]
            config = config.model_copy(
                update={
                    "executor_lookup_succeeded": True,
                    "active_executors": active_executors,
                }
            )
            if active_executors:
                return config.model_copy(
                    update={
                        "wallet_lookup_succeeded": False,
                        "wallet_balances": [],
                        "api_errors": errors,
                    }
                )
            if not hasattr(client, "gateway_clmm"):
                raise RuntimeError("Gateway CLMM API is unavailable")
            gateway_result = await client.gateway_clmm.get_pool_info(
                connector="orca",
                network=config.connector_name,
                pool_address=_text(config.selected_candidate.get("pool_address")),
            )
            gateway_pool_info = evidence.normalize_gateway_pool_info(
                gateway_result,
                _text(config.selected_candidate.get("pool_address")),
                _utc_now(),
            )
            config = config.model_copy(update={"gateway_pool_info": gateway_pool_info})
            await evidence.ensure_gateway_tokens(
                client,
                config.wallet_connector_name,
                config.selected_candidate,
                gateway_pool_info,
            )
        if not config.fetch_wallet_balances:
            return config.model_copy(update={"api_errors": errors})
        normalized = await evidence.fetch_wallet_balances(
            client, config.wallet_account_name, config.wallet_connector_name
        )
        return config.model_copy(
            update={
                "wallet_lookup_succeeded": True,
                "wallet_balances": normalized,
                "api_errors": errors,
            }
        )
    except Exception as exc:
        errors.append(f"live evidence lookup failed: {type(exc).__name__}: {exc}")
        return config.model_copy(
            update={
                "executor_lookup_succeeded": (
                    False
                    if config.execution_mode == "loop"
                    else config.executor_lookup_succeeded
                ),
                "active_executors": (
                    [] if config.execution_mode == "loop" else config.active_executors
                ),
                "wallet_lookup_succeeded": False,
                "wallet_balances": [],
                "api_errors": errors,
            }
        )


def _format(payload: dict[str, Any]) -> str:
    lines = [f"Orca Live Preflight: {payload['decision']}"]
    if payload.get("rebalance_plan"):
        lines.append(
            f"Rebalance required: {payload['rebalance_plan'].get('base_shortfall')} "
            f"{payload['rebalance_plan'].get('base_symbol')}"
        )
    elif payload["failed_checks"]:
        lines.append("Failed checks: " + ", ".join(payload["failed_checks"]))
    elif payload.get("executor_plan"):
        plan = payload["executor_plan"]["executor_config"]
        lines.append(f"Plan: {plan.get('trading_pair')} on {plan.get('pool_address')}")
    return "\n".join(lines)


async def _save_report(payload: dict[str, Any]) -> None:
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("Orca Live Preflight")
        builder.source("routine", "orca_live_preflight").tags(
            ["orca", "lp", "preflight", "agent"]
        ).manual_order()
        builder.kpi(
            "Decision",
            payload["decision"],
            trend="up" if payload["decision"] == "ready" else "down",
        )
        builder.kpi("Failed Checks", str(len(payload["failed_checks"])))
        builder.markdown("## Agent Summary\n" + payload["agent_prompt_summary"])
        builder.markdown("## Preflight Checks")
        builder.table(payload["checks"])
        builder.markdown(
            "## Source Evidence\n```json\n"
            f"{json.dumps(payload['evidence'], indent=2, sort_keys=True, default=str)}\n```"
        )
        builder.markdown(
            "## Executor Plan\n```json\n"
            f"{json.dumps(payload.get('executor_plan'), indent=2, sort_keys=True, default=str)}\n```"
        )
        builder.markdown(
            "## Rebalance Plan\n```json\n"
            f"{json.dumps(payload.get('rebalance_plan'), indent=2, sort_keys=True, default=str)}\n```"
        )
        builder.markdown(
            "## Sanitized Routine Input\n```json\n"
            f"{json.dumps(payload['input_config'], indent=2, sort_keys=True, default=str)}\n```"
        )
        builder.markdown(
            "## Warnings\n"
            + (
                "\n".join(f"- {warning}" for warning in payload["warnings"])
                if payload["warnings"]
                else "- None."
            )
        )
        builder.markdown(
            "## Errors\n"
            + (
                "\n".join(f"- {error}" for error in payload["errors"])
                if payload["errors"]
                else "- None."
            )
        )
        builder.markdown(
            "## Debug JSON Payload\n```json\n"
            f"{json.dumps(payload, indent=2, sort_keys=True, default=str)}\n```"
        )
        await builder.save()
    except Exception:
        return


def _outcome(payload: dict[str, Any], config: Config) -> dict[str, Any]:
    state = payload.get("lifecycle_state") or {}
    executor_plan = payload.get("executor_plan") or {}
    rebalance_plan = payload.get("rebalance_plan") or {}
    if rebalance_plan:
        action = NextAction.RUN_REBALANCE
        arguments = {
            "controller_id": config.controller_id,
            "selected_candidate": config.selected_candidate,
            "gateway_pool_info": config.gateway_pool_info,
            "rebalance_plan": rebalance_plan,
            "total_amount_quote": config.total_amount_quote,
            "execution_mode": config.execution_mode,
            "wallet_account_name": config.wallet_account_name,
            "wallet_connector_name": config.wallet_connector_name,
        }
    elif payload.get("decision") == "ready" and config.execution_mode == "loop":
        action = NextAction.CREATE_EXECUTOR
        arguments = executor_plan
    elif (
        payload.get("decision") == "blocked"
        and config.execution_mode == "loop"
        and (
            state.get("phase") == "rebalance_blocked"
            or state.get("post_rebalance_block")
        )
    ):
        action = NextAction.MANUAL_REVIEW
        arguments = {"controller_id": config.controller_id}
    else:
        action = NextAction.NO_ACTION
        arguments = {}
    return attach_outcome(
        payload,
        routine="orca_live_preflight",
        next_action=action,
        reason=str(payload.get("decision") or "unknown"),
        arguments=arguments,
        mutation={"phase": state.get("phase"), "decision": payload.get("decision")},
        position_number=state.get("position_number"),
        executor_id=state.get("executor_id"),
    )


async def _finish(payload: dict[str, Any], config: Config) -> RoutineResult:
    _outcome(payload, config)
    payload = evidence.redact(payload, datetime_iso=False)
    await _save_report(payload)
    return RoutineResult(
        text=_format(payload)
        + "\n```json\n"
        + json.dumps(payload, indent=2, sort_keys=True, default=str)
        + "\n```",
        table_data=payload["checks"],
        table_columns=["check", "passed", "observed", "required"],
    )


async def run(config: Config, context: Any) -> RoutineResult:
    existing_state: dict[str, Any] = {}
    if config.execution_mode == "loop" and config.controller_id:
        try:
            existing_state = load_lifecycle_state(config.controller_id)
        except Exception:
            existing_state = {}
    config = await _with_live_evidence(config, context)
    payload = _evaluate(config, require_session=True, existing_state=existing_state)
    if config.execution_mode == "loop":
        try:
            with lifecycle_lock(config.controller_id):
                existing = load_lifecycle_state(config.controller_id)
                rebalance_status = _text(
                    (existing.get("rebalance") or {}).get("status")
                ).upper()
                confirmed_rebalance = bool(existing) and (
                    rebalance_status in {"CONFIRMED", "NOT_REQUIRED"}
                    or existing.get("phase")
                    in {"rebalance_confirmed", "rebalance_confirmed_waiting_balance"}
                )
                if confirmed_rebalance:
                    persisted_candidate = existing.get("selected_candidate") or {}
                    if (
                        existing.get("controller_id") != config.controller_id
                        or _candidate_identity(persisted_candidate)
                        != _candidate_identity(config.selected_candidate)
                        or _number(existing.get("total_amount_quote"))
                        != config.total_amount_quote
                    ):
                        raise ValueError(
                            "confirmed rebalance does not match current preflight"
                        )
                    if payload["decision"] == "ready":
                        next_state = {
                            **payload["lifecycle_state"],
                            "selected_candidate": persisted_candidate,
                            "gateway_pool_info": config.gateway_pool_info,
                            "total_amount_quote": existing.get("total_amount_quote"),
                            "rebalance_plan": existing.get("rebalance_plan"),
                            "rebalance": existing.get("rebalance"),
                        }
                        payload["lifecycle_state"] = next_state
                        save_lifecycle_state(config.controller_id, next_state)
                    else:
                        blocked_state = {
                            **existing,
                            "phase": "rebalance_blocked",
                            "gateway_pool_info": config.gateway_pool_info,
                            "post_rebalance_block": {
                                "blocked_at": _utc_now(),
                                "failed_checks": payload["failed_checks"],
                                "gateway_pool_info": config.gateway_pool_info,
                                "wallet_evidence": payload["evidence"].get(
                                    "matched_wallet_balances"
                                ),
                            },
                        }
                        save_lifecycle_state(config.controller_id, blocked_state)
                        payload["lifecycle_state"] = blocked_state
                        payload["rebalance_plan"] = None
                        payload["executor_plan"] = None
                elif payload.get("lifecycle_state") is not None:
                    if existing:
                        raise ValueError("an unfinished Orca lifecycle already exists")
                    save_lifecycle_state(
                        config.controller_id, payload["lifecycle_state"]
                    )
        except Exception as exc:
            payload["decision"] = "blocked"
            if "lifecycle_state_persistence" not in payload["failed_checks"]:
                payload["failed_checks"].append("lifecycle_state_persistence")
            payload["executor_plan"] = None
            payload["rebalance_plan"] = None
            payload["lifecycle_state"] = None
            prior_status = _text(
                (existing_state.get("rebalance") or {}).get("status")
            ).upper()
            prior_confirmed = bool(existing_state) and (
                prior_status in {"CONFIRMED", "NOT_REQUIRED"}
                or existing_state.get("phase")
                in {"rebalance_confirmed", "rebalance_confirmed_waiting_balance"}
            )
            if prior_confirmed:
                try:
                    with lifecycle_lock(config.controller_id):
                        current = load_lifecycle_state(config.controller_id)
                        blocked_state = {
                            **current,
                            "phase": "rebalance_blocked",
                            "post_rebalance_block": {
                                "blocked_at": _utc_now(),
                                "failed_checks": payload["failed_checks"],
                                "reason": f"{type(exc).__name__}: {exc}",
                                "gateway_pool_info": config.gateway_pool_info,
                                "wallet_evidence": payload["evidence"].get(
                                    "matched_wallet_balances"
                                ),
                            },
                        }
                        save_lifecycle_state(config.controller_id, blocked_state)
                        payload["lifecycle_state"] = blocked_state
                except Exception as block_exc:
                    payload["errors"].append(
                        "post-rebalance manual-review persistence failed: "
                        f"{type(block_exc).__name__}: {block_exc}"
                    )
            payload["errors"].append(
                f"lifecycle state persistence failed: {type(exc).__name__}: {exc}"
            )
    payload["timestamp"] = _utc_now()
    payload["input_config"] = _sanitized_input(config, payload)
    payload["agent_prompt_summary"] = (
        "Preflight passed; use the exact executor plan for dry-run review or explicit live creation."
        if payload["decision"] == "ready"
        else (
            "Preflight requires a quote-to-base rebalance before it can pass."
            if payload.get("rebalance_plan")
            else f"Preflight blocked: {', '.join(payload['failed_checks'])}. Do not create an executor."
        )
    )
    return await _finish(payload, config)


def _self_check() -> None:
    observed_at = datetime(2026, 7, 24, tzinfo=timezone.utc)

    def candidate_for(symbol: str = "SOL", mint: str = "sol") -> dict[str, Any]:
        raw = {
            "address": "pool-1",
            "token_a": {"symbol": symbol, "mint": mint, "decimals": 9},
            "token_b": {
                "symbol": "USDC",
                "mint": CANONICAL_USDC_MINT,
                "decimals": 6,
            },
            "scanner_price": 100.0,
            "tvl_usd": 1_000_000.0,
            "volume_1h_usd": 10_000.0,
            "volume_4h_usd": 40_000.0,
            "volume_24h_usd": 300_000.0,
            "volume_7d_usd": 2_000_000.0,
            "fees_1h_usd": 25.0,
            "fees_4h_usd": 100.0,
            "fees_24h_usd": 500.0,
            "fees_7d_usd": 3_500.0,
            "net_price_change_24h": 0.005,
            "fee_rate_raw": 400.0,
            "adaptive_fee_enabled": False,
            "fee_tier_index": 1,
            "tick_spacing": 1,
            "has_warning": False,
            "source_categories": ["utility"],
            "source_lenses": ["volume24h"],
        }
        orca_policy.derive(raw, 10)
        assert (
            orca_policy.gate(
                raw,
                orca_policy.PROFILE_POLICY["yield_focused"],
                True,
                set(),
            )
            is None
        )
        orca_policy.score(raw, orca_policy.PROFILE_POLICY["yield_focused"])
        raw["range_plan"] = orca_policy.range_plan(raw, "yield_focused")[0]
        return orca_pool_scan._candidate_row(
            raw, observed_at.isoformat(), "yield_focused", 10
        )

    candidate = candidate_for()
    normalized_gateway = evidence.normalize_gateway_pool_info(
        {
            "address": "pool-1",
            "base_token_address": "sol",
            "quote_token_address": CANONICAL_USDC_MINT,
            "price": "100.5",
        },
        "pool-1",
        _utc_now(),
    )
    assert normalized_gateway["base_mint"] == "sol"
    assert normalized_gateway["quote_mint"] == CANONICAL_USDC_MINT

    class GatewayRegistry:
        async def get_network_tokens(self, network_id: str) -> dict[str, Any]:
            return {
                "tokens": [
                    {"symbol": "SOL", "address": "sol", "decimals": 9},
                    {
                        "symbol": "USDC",
                        "address": CANONICAL_USDC_MINT,
                        "decimals": 6,
                    },
                ]
            }

        async def add_token(self, **kwargs: Any) -> None:
            raise AssertionError("fixture tokens should already be registered")

    registry_evidence = asyncio.run(
        evidence.ensure_gateway_tokens(
            type("Client", (), {"gateway": GatewayRegistry()})(),
            "solana-mainnet-beta",
            candidate,
            normalized_gateway,
        )
    )
    assert len(registry_evidence) == 2
    assert evidence.executor_rows({"data": []}) == []
    config = Config(
        controller_id="lpmaxxing.orca_self_check",
        executor_lookup_succeeded=True,
        wallet_lookup_succeeded=True,
        selected_candidate=candidate,
        gateway_pool_info={
            "pool_address": "pool-1",
            "base_mint": "sol",
            "quote_mint": CANONICAL_USDC_MINT,
            "current_price": 100.5,
        },
        wallet_balances=[
            {"symbol": "SOL", "mint": "sol", "available": 1},
            {"symbol": "USDC", "mint": CANONICAL_USDC_MINT, "available": 20},
        ],
    )
    ready = _evaluate(config, now=observed_at)
    assert ready["decision"] == "ready", ready
    assert ready["executor_plan"]["action"] == "create", ready
    assert (
        ready["executor_plan"]["executor_config"]["controller_id"]
        == "lpmaxxing.orca_self_check"
    ), ready
    assert ready["executor_plan"]["executor_config"]["total_amount_quote"] == 10, ready
    assert ready["lifecycle_state"]["preset"] == "concentrated", ready
    bounds = ready["evidence"]["executable_range"]
    inventory = ready["evidence"]["inventory_plan"]
    assert Decimal(str(inventory["quote_amount"])).as_tuple().exponent >= -6
    assert bounds["center_price"] == 100.5
    assert math.isclose(
        bounds["limit_buffer"],
        bounds["provisional_half_width"] * 0.5,
    )
    assert candidate_is_fresh(candidate, observed_at)
    assert candidate_is_fresh(candidate, observed_at - timedelta(seconds=30))
    assert not candidate_is_fresh(
        candidate, observed_at - timedelta(seconds=30, microseconds=1)
    )
    assert candidate_is_fresh(candidate, observed_at.replace(second=0))
    assert candidate_is_fresh(candidate, observed_at.replace(minute=10))
    assert not candidate_is_fresh(
        candidate, observed_at.replace(minute=10, microsecond=1)
    )
    assert _executable_range(100, 0.005)["limit_buffer"] == 0.003
    assert _executable_range(100, 0.01)["limit_buffer"] == 0.005
    assert _executable_range(100, 0.02)["limit_buffer"] == 0.01
    assert _executable_range(100, 0.03)["limit_buffer"] == 0.015
    assert _executable_range(100, 0.95)["limit_buffer"] == 0.015

    token_candidate = candidate_for("TOKEN", "token")
    needs_rebalance = _evaluate(
        config.model_copy(
            update={
                "selected_candidate": token_candidate,
                "gateway_pool_info": {
                    "pool_address": "pool-1",
                    "base_mint": "token",
                    "quote_mint": CANONICAL_USDC_MINT,
                    "current_price": 100.5,
                },
                "wallet_balances": [
                    {
                        "symbol": "USDC",
                        "mint": CANONICAL_USDC_MINT,
                        "available": 20,
                    },
                    {"symbol": "SOL", "mint": "sol", "available": 1},
                ],
            }
        ),
        now=observed_at,
    )
    assert needs_rebalance["failed_checks"] == ["wallet_inventory"], needs_rebalance
    assert needs_rebalance["rebalance_plan"]["base_shortfall"] > 0, needs_rebalance
    consumed = _evaluate(
        config.model_copy(
            update={
                "selected_candidate": token_candidate,
                "gateway_pool_info": {
                    "pool_address": "pool-1",
                    "base_mint": "token",
                    "quote_mint": CANONICAL_USDC_MINT,
                    "current_price": 100.5,
                },
                "wallet_balances": [
                    {"symbol": "USDC", "mint": CANONICAL_USDC_MINT, "available": 20},
                    {"symbol": "SOL", "mint": "sol", "available": 1},
                ],
            }
        ),
        now=observed_at,
        existing_state={
            "phase": "rebalance_confirmed",
            "rebalance": {"status": "CONFIRMED"},
        },
    )
    assert consumed["rebalance_plan"] is None, consumed
    assert consumed["evidence"]["rebalance_consumed"] is True, consumed

    stale = _evaluate(config, now=observed_at.replace(minute=10, microsecond=1))
    assert "scan_freshness" in stale["failed_checks"], stale
    tampered_candidate = {**candidate, "fees_24h_usd": 501.0}
    tampered = _evaluate(
        config.model_copy(update={"selected_candidate": tampered_candidate}),
        now=observed_at,
    )
    assert "scanner_candidate_contract" in tampered["failed_checks"], tampered
    planning = _evaluate(
        config.model_copy(update={"execution_mode": "run_once"}), now=observed_at
    )
    assert planning["decision"] == "blocked", planning
    blocked = _evaluate(
        config.model_copy(
            update={
                "active_executors": [
                    {"controller_id": "lpmaxxing.orca_self_check", "status": "RUNNING"}
                ]
            }
        ),
        now=observed_at,
    )
    assert blocked["decision"] == "blocked", blocked
    assert "single_executor" in blocked["failed_checks"], blocked
    malformed_executor = _evaluate(
        config.model_copy(
            update={"active_executors": [{"controller_id": "other", "unexpected": 1}]}
        ),
        now=observed_at,
    )
    assert "single_executor" in malformed_executor["failed_checks"], malformed_executor
    lookup_failed = _evaluate(
        config.model_copy(update={"executor_lookup_succeeded": False}),
        now=observed_at,
    )
    assert lookup_failed["decision"] == "blocked", lookup_failed


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        _self_check()
        print("self-check passed")
        raise SystemExit(0)
    print(asyncio.run(run(Config(), None)).text)
