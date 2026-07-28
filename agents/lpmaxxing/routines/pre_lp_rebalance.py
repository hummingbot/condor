import asyncio
import json
import logging
import math
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from agents.lpmaxxing.routines import _orca_evidence as evidence
from agents.lpmaxxing.routines import _orca_policy as policy
from agents.lpmaxxing.routines._orca_contracts import NextAction, attach_outcome
from agents.lpmaxxing.routines._orca_lifecycle import (
    clear_lifecycle_state,
    lifecycle_lock,
    load_lifecycle_state,
    load_session_state,
    save_lifecycle_state,
    session_execution_mode,
    session_risk_profile,
    session_total_amount_quote,
)

try:
    from routines.base import RoutineResult
except ModuleNotFoundError:
    from routines.base import RoutineResult

CATEGORY = "Orca LP Agent"
logger = logging.getLogger(__name__)


class Config(BaseModel):
    """Quote or execute the base-token preparation required by Orca preflight."""

    model_config = ConfigDict(extra="forbid")

    execution_mode: Literal["dry_run", "loop"] = "dry_run"
    controller_id: str = Field(
        default="", description="Dynamic controller id from the current tick"
    )
    selected_candidate: dict[str, Any] = Field(default_factory=dict)
    gateway_pool_info: dict[str, Any] = Field(default_factory=dict)
    rebalance_plan: dict[str, Any] = Field(default_factory=dict)
    total_amount_quote: float = Field(
        default=10, gt=0, description="Active session quote budget"
    )
    wallet_account_name: str = "master_account"
    wallet_connector_name: str = "solana-mainnet-beta"
    connector: str = "jupiter"
    network: str = "solana-mainnet-beta"
    slippage_pct: float = Field(default=1.0, gt=0, le=10)
    settlement_wait_seconds: float = Field(default=45, ge=0, le=300)
    settlement_poll_interval_seconds: float = Field(default=5, gt=0, le=60)
    post_confirm_delay_seconds: float = Field(default=3, ge=0, le=60)
    balance_refresh_wait_seconds: float = Field(default=20, ge=0, le=120)
    balance_refresh_poll_interval_seconds: float = Field(default=2, gt=0, le=30)

    @field_validator("controller_id", mode="before")
    @classmethod
    def _controller(cls, value: Any) -> str:
        return str(value or "").strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _find_number(value: Any, keys: set[str]) -> float | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in keys:
                parsed = _number(item)
                if parsed is not None:
                    return parsed
        for item in value.values():
            found = _find_number(item, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_number(item, keys)
            if found is not None:
                return found
    return None


def _find_text(value: Any, keys: set[str]) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in keys and item not in (None, ""):
                return str(item)
        for item in value.values():
            found = _find_text(item, keys)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_text(item, keys)
            if found:
                return found
    return None


async def _client(context: Any) -> Any:
    from config_manager import get_client

    chat_id = getattr(context, "_chat_id", None) if context is not None else None
    client = await get_client(chat_id or 0, context=context)
    if not client:
        raise RuntimeError("no Hummingbot API server available")
    return client


def _quote_pairs(plan: dict[str, Any]) -> list[str]:
    pairs = []
    base_mint = _text(plan.get("base_mint"))
    quote_mint = _text(plan.get("quote_mint"))
    if base_mint and quote_mint:
        pairs.append(f"{base_mint}-{quote_mint}")
    symbol_pair = _text(plan.get("trading_pair"))
    if symbol_pair and symbol_pair not in pairs:
        pairs.append(symbol_pair)
    return [pair for pair in pairs if pair]


def _quote_matches_mints(quote: Any, plan: dict[str, Any]) -> bool:
    base = _find_text(quote, {"base", "base_mint", "basemint"})
    quote_token = _find_text(quote, {"quote", "quote_mint", "quotemint"})
    return base == _text(plan.get("base_mint")) and quote_token == _text(
        plan.get("quote_mint")
    )


async def _quote(client: Any, config: Config, pair: str, base_amount: float) -> Any:
    return await client.gateway_swap.get_swap_quote(
        connector=config.connector,
        network=config.network,
        trading_pair=pair,
        side="BUY",
        amount=Decimal(str(base_amount)),
        slippage_pct=Decimal(str(config.slippage_pct)),
    )


async def _execute(client: Any, config: Config, pair: str, base_amount: float) -> Any:
    return await client.gateway_swap.execute_swap(
        connector=config.connector,
        network=config.network,
        trading_pair=pair,
        side="BUY",
        amount=Decimal(str(base_amount)),
        slippage_pct=Decimal(str(config.slippage_pct)),
        wallet_address=None,
    )


async def _swap_status(client: Any, transaction_hash: str) -> tuple[str, Any]:
    result = await client.gateway_swap.get_swap_status(transaction_hash)
    status = (_find_text(result, {"status"}) or "UNKNOWN").upper()
    return status, result


async def _wait_for_settlement(
    client: Any, config: Config, transaction_hash: str
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + config.settlement_wait_seconds
    attempts = []
    while True:
        try:
            status, result = await _swap_status(client, transaction_hash)
        except Exception as exc:
            status = "UNKNOWN"
            result = {"error": f"{type(exc).__name__}: {exc}"}
        attempts.append({"status": status, "result": result})
        if status in {"CONFIRMED", "FAILED", "REJECTED"}:
            if status == "CONFIRMED" and config.post_confirm_delay_seconds:
                await asyncio.sleep(config.post_confirm_delay_seconds)
            return {"status": status, "attempts": attempts}
        if asyncio.get_running_loop().time() >= deadline:
            return {"status": status, "timeout": True, "attempts": attempts}
        await asyncio.sleep(config.settlement_poll_interval_seconds)


def _state_inputs(
    config: Config, state: dict[str, Any]
) -> tuple[dict, dict, dict, float]:
    if config.execution_mode == "loop":
        if state.get("controller_id") != config.controller_id:
            raise ValueError("rebalance lifecycle controller mismatch")
        budget = _number(state.get("total_amount_quote"))
        if budget is None or budget != config.total_amount_quote:
            raise ValueError("rebalance lifecycle budget mismatch")
        return (
            state.get("selected_candidate") or {},
            state.get("gateway_pool_info") or {},
            state.get("rebalance_plan") or {},
            budget,
        )
    return (
        config.selected_candidate,
        config.gateway_pool_info,
        config.rebalance_plan,
        config.total_amount_quote,
    )


def _validate_identity(candidate: dict, gateway: dict, plan: dict) -> None:
    candidate_pool = _text(candidate.get("pool_address"))
    if not candidate_pool or candidate_pool != _text(gateway.get("pool_address")):
        raise ValueError("candidate and Gateway pool identity do not match")
    if candidate_pool != _text(plan.get("pool_address")):
        raise ValueError("rebalance plan pool identity does not match")
    if _text(plan.get("base_mint")) != _text(gateway.get("base_mint")):
        raise ValueError("rebalance base mint does not match Gateway")
    if _text(plan.get("quote_mint")) != _text(gateway.get("quote_mint")):
        raise ValueError("rebalance quote mint does not match Gateway")


def _inventory_evidence(
    balances: list[dict[str, Any]], plan: dict[str, Any]
) -> dict[str, Any]:
    return {
        "base": evidence.balance_for_symbol(
            balances,
            _text(plan.get("base_symbol")),
            _text(plan.get("base_mint")),
        ),
        "quote": evidence.balance_for_symbol(
            balances,
            _text(plan.get("quote_symbol")),
            _text(plan.get("quote_mint")),
        ),
        "sol": evidence.balance_for_symbol(balances, "SOL"),
    }


def _inventory_ready(inventory: dict[str, Any], plan: dict[str, Any]) -> bool:
    base = _number(inventory.get("base"))
    quote = _number(inventory.get("quote"))
    sol = _number(inventory.get("sol"))
    required_base = _number(plan.get("required_base"))
    required_quote = _number(plan.get("required_quote"))
    sol_buffer = _number(plan.get("min_sol_fee_buffer")) or 0.0
    sol_spend = 0.0
    if _text(plan.get("base_symbol")).upper() == "SOL" and required_base is not None:
        sol_spend += required_base
    if _text(plan.get("quote_symbol")).upper() == "SOL" and required_quote is not None:
        sol_spend += required_quote
    return bool(
        base is not None
        and required_base is not None
        and base >= required_base
        and quote is not None
        and required_quote is not None
        and quote >= required_quote
        and sol is not None
        and sol - sol_spend >= sol_buffer
    )


def _rebalance_visible(inventory: dict[str, Any], plan: dict[str, Any]) -> bool:
    base = _number(inventory.get("base"))
    previous_base = _number(plan.get("available_base")) or 0.0
    return base is not None and base > previous_base + 1e-12


async def _wait_for_inventory(
    client: Any, config: Config, plan: dict[str, Any]
) -> tuple[dict[str, Any], bool, int]:
    deadline = asyncio.get_running_loop().time() + config.balance_refresh_wait_seconds
    attempts = 0
    inventory: dict[str, Any] = {}
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if attempts and remaining <= 0:
            return inventory, False, attempts
        attempts += 1
        try:
            balances = await asyncio.wait_for(
                evidence.fetch_wallet_balances(
                    client, config.wallet_account_name, config.wallet_connector_name
                ),
                timeout=max(0.1, remaining),
            )
        except asyncio.TimeoutError:
            return inventory, False, attempts
        inventory = _inventory_evidence(balances, plan)
        if _rebalance_visible(inventory, plan):
            return inventory, True, attempts
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return inventory, False, attempts
        await asyncio.sleep(
            min(config.balance_refresh_poll_interval_seconds, remaining)
        )


def _format(payload: dict[str, Any]) -> str:
    lines = [
        f"pre_lp_rebalance: {payload.get('status', 'unknown')} ({payload.get('reason', 'n/a')})"
    ]
    plan = payload.get("rebalance_plan") or {}
    for key in ("trading_pair", "base_shortfall", "required_quote"):
        if key in plan:
            lines.append(f"{key}: {plan[key]}")
    if payload.get("transaction_hash"):
        lines.append(f"transaction_hash: {payload['transaction_hash']}")
    if payload.get("next_action"):
        lines.append(f"next_action: {payload['next_action']}")
    return "\n".join(lines)


async def _save_report(payload: dict[str, Any]) -> None:
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("Orca Pre-LP Rebalance")
        builder.source("routine", "pre_lp_rebalance").tags(
            ["orca", "lp", "rebalance", "agent"]
        ).manual_order()
        builder.kpi("Status", str(payload.get("status", "unknown")))
        builder.kpi("Mode", str(payload.get("execution_mode", "unknown")))
        builder.kpi(
            "Pair",
            str((payload.get("rebalance_plan") or {}).get("trading_pair", "n/a")),
        )
        builder.markdown("## Agent Summary\n" + payload["agent_prompt_summary"])
        builder.markdown(
            "## Rebalance Plan\n```json\n"
            f"{json.dumps(payload.get('rebalance_plan'), indent=2, sort_keys=True, default=str)}\n```"
        )
        builder.markdown(
            "## Quote Evidence\n```json\n"
            f"{json.dumps(payload.get('quote'), indent=2, sort_keys=True, default=str)}\n```"
        )
        builder.markdown(
            "## Settlement Evidence\n```json\n"
            f"{json.dumps(payload.get('settlement'), indent=2, sort_keys=True, default=str)}\n```"
        )
        builder.markdown(
            "## Wallet Evidence\n```json\n"
            f"{json.dumps(payload.get('wallet_evidence'), indent=2, sort_keys=True, default=str)}\n```"
        )
        builder.markdown(
            "## Warnings\n"
            + (
                "\n".join(f"- {warning}" for warning in payload.get("warnings", []))
                if payload.get("warnings")
                else "- None."
            )
        )
        builder.markdown(
            "## Debug JSON Payload\n```json\n"
            f"{json.dumps(payload, indent=2, sort_keys=True, default=str)}\n```"
        )
        await builder.save()
    except Exception:
        logger.exception("Failed to save pre-LP rebalance report")


def _outcome(payload: dict[str, Any]) -> dict[str, Any]:
    next_action = str(payload.get("next_action") or "")
    if next_action == "rescan":
        action = NextAction.RUN_POOL_SCAN
    elif next_action == "rerun-preflight":
        action = NextAction.RUN_PREFLIGHT
    elif next_action == "manual-review":
        action = NextAction.MANUAL_REVIEW
    elif payload.get("reason") in {
        "rebalance_submission_uncertain",
        "rebalance_not_submittable",
        "rebalance_failed",
        "swap_failed",
    }:
        action = NextAction.MANUAL_REVIEW
    else:
        action = NextAction.NO_ACTION
    reason = str(payload.get("reason") or "unknown")
    controller_id = str(payload.get("controller_id") or "")
    if action == NextAction.RUN_POOL_SCAN:
        arguments = {
            "execution_mode": payload.get("execution_mode"),
            "controller_id": controller_id,
            "risk_profile": payload.get("risk_profile") or "yield_focused",
            "total_amount_quote": payload.get("total_amount_quote"),
        }
    elif action == NextAction.RUN_PREFLIGHT:
        arguments = {
            "execution_mode": payload.get("execution_mode"),
            "controller_id": controller_id,
            "selected_candidate": payload.get("selected_candidate") or {},
            "gateway_pool_info": payload.get("gateway_pool_info") or {},
            "total_amount_quote": payload.get("total_amount_quote"),
            "fetch_wallet_balances": True,
            "wallet_account_name": payload.get("wallet_account_name"),
            "wallet_connector_name": payload.get("wallet_connector_name"),
        }
    elif action == NextAction.MANUAL_REVIEW:
        arguments = {"controller_id": controller_id}
    else:
        arguments = {}
    if action in {NextAction.RUN_POOL_SCAN, NextAction.RUN_PREFLIGHT}:
        missing = [
            key
            for key, value in arguments.items()
            if key != "fetch_wallet_balances" and value in (None, "", {})
        ]
        if missing:
            action = NextAction.MANUAL_REVIEW
            reason = "incomplete-follow-up-dispatch: " + ", ".join(missing)
            arguments = {"controller_id": controller_id}
    return attach_outcome(
        payload,
        routine="pre_lp_rebalance",
        next_action=action,
        reason=reason,
        arguments=arguments,
        mutation={
            "status": payload.get("status"),
            "next_action": payload.get("next_action"),
            "transaction_hash": payload.get("transaction_hash"),
        },
        position_number=payload.get("position_number"),
    )


async def _finish(payload: dict[str, Any]) -> RoutineResult:
    payload["timestamp"] = _utc_now()
    payload["agent_prompt_summary"] = (
        f"Pre-LP rebalance finished with {payload.get('status')} "
        f"({payload.get('reason')})."
    )
    payload = evidence.redact(payload, datetime_iso=False)
    _outcome(payload)
    await _save_report(payload)
    return RoutineResult(
        text=(
            _format(payload)
            + "\n```json\n"
            + json.dumps(payload, indent=2, sort_keys=True)
            + "\n```"
        )
    )


async def run(config: Config, context: Any) -> RoutineResult:
    payload: dict[str, Any] = {
        "execution_mode": config.execution_mode,
        "controller_id": config.controller_id,
        "total_amount_quote": config.total_amount_quote,
        "wallet_account_name": config.wallet_account_name,
        "wallet_connector_name": config.wallet_connector_name,
        "status": "blocked",
        "reason": "not_evaluated",
        "rebalance_plan": {},
        "quote": None,
        "settlement": None,
        "wallet_evidence": None,
        "transaction_hash": None,
        "next_action": None,
        "warnings": [],
    }
    try:
        if not config.controller_id:
            raise ValueError("controller_id is required")
        if session_execution_mode(config.controller_id) != config.execution_mode:
            raise ValueError("execution_mode does not match the current SESSION_MODE")
        if (
            config.execution_mode == "loop"
            and load_session_state(config.controller_id).get("session_status")
            != "running"
        ):
            raise ValueError("current Orca session is not accepting a rebalance")
        active_session_budget = session_total_amount_quote(config.controller_id)
        if (
            active_session_budget is not None
            and active_session_budget != config.total_amount_quote
        ):
            raise ValueError("total_amount_quote does not match the active session")
        state = (
            load_lifecycle_state(config.controller_id)
            if config.execution_mode == "loop"
            else {}
        )
        payload["position_number"] = state.get("position_number")
        phase = _text(state.get("phase"))
        if config.execution_mode == "loop" and phase in {
            "rebalance_submission_uncertain",
            "rebalance_failed",
            "rebalance_blocked",
        }:
            payload.update(status="blocked", reason=phase)
            return await _finish(payload)

        candidate, gateway, plan, budget = _state_inputs(config, state)
        payload["selected_candidate"] = candidate
        payload["gateway_pool_info"] = gateway
        payload["risk_profile"] = (
            session_risk_profile(config.controller_id)
            if config.execution_mode == "loop"
            else _text(candidate.get("risk_profile"))
        )
        payload["rebalance_plan"] = plan
        _validate_identity(candidate, gateway, plan)
        if _number(plan.get("total_amount_quote")) != budget:
            raise ValueError("rebalance plan does not match active session budget")
        if config.execution_mode == "loop":
            rebalance = state.get("rebalance") or {}
            rebalance_status = _text(rebalance.get("status")).upper()
            submission_attempt = int(rebalance.get("submission_attempt") or 0)
            transaction_hash = _text(rebalance.get("transaction_hash"))
            profile_matches = _text(
                candidate.get("risk_profile")
            ) == session_risk_profile(config.controller_id)
            fresh = policy.candidate_is_fresh(candidate)
            pristine = (
                phase == "rebalance_required"
                and rebalance_status.lower() == "required"
                and submission_attempt == 0
                and not transaction_hash
            )
            if not profile_matches or (not fresh and not transaction_hash):
                with lifecycle_lock(config.controller_id):
                    current = load_lifecycle_state(config.controller_id)
                    if pristine and current == state:
                        clear_lifecycle_state(config.controller_id)
                    else:
                        save_lifecycle_state(
                            config.controller_id,
                            {
                                **current,
                                "phase": "rebalance_blocked",
                                "rebalance_block": {
                                    "blocked_at": _utc_now(),
                                    "reason": (
                                        "session_risk_profile_mismatch"
                                        if not profile_matches
                                        else "scanner_evidence_stale"
                                    ),
                                },
                            },
                        )
                payload.update(
                    status="blocked",
                    reason=(
                        "session_risk_profile_mismatch"
                        if not profile_matches
                        else "scanner_evidence_stale"
                    ),
                    next_action="rescan" if pristine else "manual-review",
                )
                return await _finish(payload)
            if phase == "rebalance_confirmed" or rebalance_status == "NOT_REQUIRED":
                payload.update(
                    status="ready",
                    reason="rebalance_already_consumed",
                    next_action="rerun-preflight",
                )
                return await _finish(payload)
            if not transaction_hash and not pristine:
                payload.update(
                    status="blocked",
                    reason=(
                        "rebalance_submission_uncertain"
                        if phase == "rebalance_submission_intent"
                        else "rebalance_not_submittable"
                    ),
                )
                return await _finish(payload)

        client = await _client(context)
        if not hasattr(client, "gateway_swap"):
            raise RuntimeError("Gateway swap API is unavailable")
        if config.execution_mode == "loop":
            payload["token_registry"] = await evidence.ensure_gateway_tokens(
                client,
                config.network,
                candidate,
                gateway,
            )

        transaction_hash = _text((state.get("rebalance") or {}).get("transaction_hash"))
        if (
            config.execution_mode == "loop"
            and phase == "rebalance_submission_intent"
            and not transaction_hash
        ):
            payload.update(status="blocked", reason="rebalance_submission_uncertain")
            return await _finish(payload)

        if config.execution_mode == "loop" and transaction_hash:
            payload["transaction_hash"] = transaction_hash
            rebalance = state.get("rebalance") or {}
            persisted_confirmed = _text(rebalance.get("status")).upper() == "CONFIRMED"
            settlement = (
                rebalance.get("settlement") or {"status": "CONFIRMED", "attempts": []}
                if persisted_confirmed
                else await _wait_for_settlement(client, config, transaction_hash)
            )
            payload["settlement"] = settlement
            status = "CONFIRMED" if persisted_confirmed else settlement.get("status")
            if status in {"FAILED", "REJECTED"}:
                with lifecycle_lock(config.controller_id):
                    current = load_lifecycle_state(config.controller_id)
                    save_lifecycle_state(
                        config.controller_id,
                        {
                            **current,
                            "phase": "rebalance_failed",
                            "rebalance": {
                                **(current.get("rebalance") or {}),
                                "status": status,
                                "settlement": settlement,
                            },
                        },
                    )
                payload.update(status="failed", reason="swap_failed")
                return await _finish(payload)
            if status != "CONFIRMED":
                payload.update(status="pending", reason="swap_submitted_unconfirmed")
                return await _finish(payload)

            inventory, inventory_visible, refresh_attempts = await _wait_for_inventory(
                client, config, plan
            )
            payload["wallet_evidence"] = inventory
            payload["balance_refresh_attempts"] = refresh_attempts
            next_phase = (
                "rebalance_confirmed"
                if inventory_visible
                else "rebalance_confirmed_waiting_balance"
            )
            with lifecycle_lock(config.controller_id):
                current = load_lifecycle_state(config.controller_id)
                save_lifecycle_state(
                    config.controller_id,
                    {
                        **current,
                        "phase": next_phase,
                        "rebalance": {
                            **(current.get("rebalance") or {}),
                            "status": "CONFIRMED",
                            "confirmed_at": (current.get("rebalance") or {}).get(
                                "confirmed_at"
                            )
                            or _utc_now(),
                            "transaction_hash": transaction_hash,
                            "settlement": settlement,
                            "post_swap_inventory": inventory,
                            "inventory_visible": inventory_visible,
                        },
                    },
                )
            payload.update(
                status="confirmed" if inventory_visible else "pending",
                reason=(
                    "swap_confirmed_balance_visible"
                    if inventory_visible
                    else "swap_confirmed_waiting_balance"
                ),
                next_action="rerun-preflight" if inventory_visible else None,
            )
            return await _finish(payload)

        balances = await evidence.fetch_wallet_balances(
            client, config.wallet_account_name, config.wallet_connector_name
        )
        inventory = _inventory_evidence(balances, plan)
        payload["wallet_evidence"] = inventory
        if _inventory_ready(inventory, plan):
            if config.execution_mode == "loop":
                with lifecycle_lock(config.controller_id):
                    current = load_lifecycle_state(config.controller_id)
                    save_lifecycle_state(
                        config.controller_id,
                        {
                            **current,
                            "phase": "rebalance_confirmed",
                            "rebalance": {
                                **(current.get("rebalance") or {}),
                                "status": "NOT_REQUIRED",
                                "inventory_visible": True,
                                "post_swap_inventory": inventory,
                            },
                        },
                    )
            payload.update(status="ready", reason="existing_inventory_sufficient")
            if config.execution_mode == "loop":
                payload["next_action"] = "rerun-preflight"
            return await _finish(payload)

        required_base = _number(plan.get("required_base"))
        current_base = _number(inventory.get("base")) or 0.0
        required_quote = _number(plan.get("required_quote"))
        current_quote = _number(inventory.get("quote"))
        current_sol = _number(inventory.get("sol"))
        sol_buffer = _number(plan.get("min_sol_fee_buffer")) or 0.0
        quote_buffer = _number(plan.get("quote_buffer")) or 0.0
        if required_base is None or required_quote is None:
            raise ValueError("rebalance plan is missing required inventory")
        base_shortfall = max(0.0, required_base - current_base)
        if base_shortfall <= 1e-12:
            raise ValueError("base shortfall is not positive")
        if current_quote is None or current_sol is None or current_sol < sol_buffer:
            raise ValueError("current quote or SOL balance is insufficient")

        quote_result = None
        quote_pair = None
        quote_spend = None
        quote_errors = []
        for pair in _quote_pairs(plan):
            try:
                result = await _quote(client, config, pair, base_shortfall)
            except Exception as exc:
                quote_errors.append(f"{pair}: {type(exc).__name__}: {exc}")
                continue
            if not _quote_matches_mints(result, plan):
                quote_errors.append(f"{pair}: quote token mints do not match the pool")
                continue
            spend = _find_number(
                result,
                {
                    "amount_in",
                    "amountin",
                    "input_amount",
                    "inputamount",
                    "in_amount",
                    "inamount",
                },
            )
            if spend is not None and spend > 0:
                quote_result = result
                quote_pair = pair
                quote_spend = spend
                break
        payload["quote"] = quote_result
        if quote_result is None or quote_pair is None or quote_spend is None:
            payload["warnings"].extend(quote_errors)
            payload.update(status="blocked", reason="quote_failed_or_cost_unparsed")
            return await _finish(payload)
        reference_price = _number(plan.get("current_price"))
        if reference_price is None:
            raise ValueError("rebalance plan is missing current_price")
        max_quote_spend = (
            base_shortfall * reference_price * (1 + config.slippage_pct / 100)
        )
        if quote_spend > max_quote_spend + 1e-6:
            payload.update(status="blocked", reason="quote_exceeds_session_budget")
            return await _finish(payload)
        if current_quote - quote_spend < required_quote + quote_buffer:
            payload.update(status="blocked", reason="post_swap_quote_insufficient")
            return await _finish(payload)
        if _text(plan.get("quote_symbol")).upper() == "SOL":
            if current_sol - quote_spend - required_quote < sol_buffer:
                payload.update(
                    status="blocked", reason="post_swap_sol_buffer_insufficient"
                )
                return await _finish(payload)

        payload["quote_pair"] = quote_pair
        payload["quote_spend"] = quote_spend
        payload["base_shortfall"] = base_shortfall
        if config.execution_mode == "dry_run":
            payload.update(status="quote", reason="quote_ok_dry_run")
            return await _finish(payload)

        with lifecycle_lock(config.controller_id):
            current = load_lifecycle_state(config.controller_id)
            rebalance = current.get("rebalance") or {}
            if current.get("phase") != "rebalance_required":
                raise ValueError("rebalance lifecycle is not ready for submission")
            if int(rebalance.get("submission_attempt") or 0) != 0:
                raise ValueError("rebalance submission has already been attempted")
            save_lifecycle_state(
                config.controller_id,
                {
                    **current,
                    "phase": "rebalance_submission_intent",
                    "rebalance": {
                        **rebalance,
                        "status": "SUBMISSION_INTENT",
                        "submission_attempt": 1,
                        "quote_pair": quote_pair,
                        "quote_spend": quote_spend,
                        "base_amount": base_shortfall,
                        "quote": evidence.redact(quote_result, datetime_iso=False),
                    },
                },
            )

        try:
            execute_result = await _execute(client, config, quote_pair, base_shortfall)
        except Exception as exc:
            with lifecycle_lock(config.controller_id):
                current = load_lifecycle_state(config.controller_id)
                save_lifecycle_state(
                    config.controller_id,
                    {
                        **current,
                        "phase": "rebalance_submission_uncertain",
                        "rebalance": {
                            **(current.get("rebalance") or {}),
                            "status": "SUBMISSION_UNCERTAIN",
                        },
                    },
                )
            payload["warnings"].append(f"execute error: {type(exc).__name__}: {exc}")
            payload.update(status="blocked", reason="rebalance_submission_uncertain")
            return await _finish(payload)
        payload["execute_result"] = execute_result
        transaction_hash = _find_text(
            execute_result,
            {"transaction_hash", "transactionhash", "tx_hash", "txhash", "hash"},
        )
        if not transaction_hash:
            with lifecycle_lock(config.controller_id):
                current = load_lifecycle_state(config.controller_id)
                save_lifecycle_state(
                    config.controller_id,
                    {
                        **current,
                        "phase": "rebalance_submission_uncertain",
                        "rebalance": {
                            **(current.get("rebalance") or {}),
                            "status": "SUBMISSION_UNCERTAIN",
                            "execute_result": evidence.redact(
                                execute_result, datetime_iso=False
                            ),
                        },
                    },
                )
            payload.update(status="blocked", reason="rebalance_submission_uncertain")
            return await _finish(payload)
        payload["transaction_hash"] = transaction_hash
        with lifecycle_lock(config.controller_id):
            current = load_lifecycle_state(config.controller_id)
            save_lifecycle_state(
                config.controller_id,
                {
                    **current,
                    "phase": "rebalance_submitted",
                    "rebalance": {
                        **(current.get("rebalance") or {}),
                        "status": "SUBMITTED",
                        "transaction_hash": transaction_hash,
                        "execute_result": evidence.redact(
                            execute_result, datetime_iso=False
                        ),
                    },
                },
            )
        settlement = await _wait_for_settlement(client, config, transaction_hash)
        payload["settlement"] = settlement
        status = settlement.get("status")
        if status == "CONFIRMED":
            inventory, inventory_visible, refresh_attempts = await _wait_for_inventory(
                client, config, plan
            )
            payload["wallet_evidence"] = inventory
            payload["balance_refresh_attempts"] = refresh_attempts
            next_phase = (
                "rebalance_confirmed"
                if inventory_visible
                else "rebalance_confirmed_waiting_balance"
            )
            payload.update(
                status="confirmed" if inventory_visible else "pending",
                reason=(
                    "swap_confirmed_balance_visible"
                    if inventory_visible
                    else "swap_confirmed_waiting_balance"
                ),
                next_action="rerun-preflight" if inventory_visible else None,
            )
        elif status in {"FAILED", "REJECTED"}:
            next_phase = "rebalance_failed"
            payload.update(status="failed", reason="swap_failed")
        else:
            next_phase = "rebalance_submitted"
            payload.update(status="pending", reason="swap_submitted_unconfirmed")
            inventory = None
            inventory_visible = False
        with lifecycle_lock(config.controller_id):
            current = load_lifecycle_state(config.controller_id)
            save_lifecycle_state(
                config.controller_id,
                {
                    **current,
                    "phase": next_phase,
                    "rebalance": {
                        **(current.get("rebalance") or {}),
                        "status": status,
                        "confirmed_at": (
                            (current.get("rebalance") or {}).get("confirmed_at")
                            or _utc_now()
                            if status == "CONFIRMED"
                            else (current.get("rebalance") or {}).get("confirmed_at")
                        ),
                        "transaction_hash": transaction_hash,
                        "settlement": settlement,
                        "post_swap_inventory": inventory,
                        "inventory_visible": inventory_visible,
                    },
                },
            )
        return await _finish(payload)
    except Exception as exc:
        payload["warnings"].append(f"{type(exc).__name__}: {exc}")
        payload.update(status="blocked", reason="rebalance_validation_failed")
        return await _finish(payload)


def _self_check() -> None:
    plan = {
        "trading_pair": "TOKEN-USDC",
        "pool_address": "pool-1",
        "base_mint": "base-mint",
        "quote_mint": "quote-mint",
        "base_symbol": "TOKEN",
        "quote_symbol": "USDC",
        "required_base": 5,
        "required_quote": 5,
        "available_base": 0,
        "total_amount_quote": 10,
        "min_sol_fee_buffer": 0.05,
    }
    _validate_identity(
        {"pool_address": "pool-1"},
        {
            "pool_address": "pool-1",
            "base_mint": "base-mint",
            "quote_mint": "quote-mint",
        },
        plan,
    )
    balances = [
        {"symbol": "TOKEN", "mint": "base-mint", "available": 5},
        {"symbol": "USDC", "mint": "quote-mint", "available": 5},
        {"symbol": "SOL", "available": 0.5},
    ]
    evidence = _inventory_evidence(balances, plan)
    assert _inventory_ready(evidence, plan), evidence
    assert _rebalance_visible(evidence, plan), evidence
    assert not _rebalance_visible({"base": 0}, plan)
    assert _quote_pairs(plan) == ["base-mint-quote-mint", "TOKEN-USDC"]
    assert _quote_matches_mints({"base": "base-mint", "quote": "quote-mint"}, plan)
    assert _find_number({"result": {"amount_in": "4.5"}}, {"amount_in"}) == 4.5
    assert Config().execution_mode == "dry_run"


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        _self_check()
        print("self-check passed")
    else:
        print(asyncio.run(run(Config(), None)))
