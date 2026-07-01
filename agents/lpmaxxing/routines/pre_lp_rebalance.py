import asyncio
import json
import math
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CATEGORY = "Orca LP Agent"
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


class Config(BaseModel):
    """Quote or execute a quote-to-base rebalance before Orca LP open."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["quote", "execute"] = Field(
        default="quote", description="quote or execute rebalance"
    )
    connector: str = Field(default="jupiter", description="Gateway router connector")
    network: str = Field(default="solana-mainnet-beta", description="Gateway network")
    trading_pair: str = Field(default="SOL-USDC", description="BASE-QUOTE pair")
    fallback_trading_pair: str = Field(
        default="", description="Optional BASE_MINT-QUOTE_MINT fallback pair"
    )
    wallet_address: str | None = Field(default=None, description="Optional wallet address")

    current_price: float = Field(default=1, gt=0, description="Current base price in quote")
    lower_price: float = Field(default=0.999, gt=0, description="LP lower price")
    upper_price: float = Field(default=1.001, gt=0, description="LP upper price")
    total_amount_quote: float = Field(default=1, gt=0, description="Quote budget")
    available_base: float = Field(default=0, ge=0, description="Current base balance")
    available_quote: float = Field(default=2, ge=0, description="Current quote balance")
    available_sol: float = Field(default=0.05, ge=0, description="Current SOL balance")
    min_sol_fee_buffer: float = Field(default=0.05, ge=0, description="SOL fee buffer")
    slippage_pct: float = Field(default=1.0, gt=0, le=10, description="Swap slippage percent")
    max_quote_spend: float | None = Field(
        default=None, description="Optional quote spend cap; defaults to total_amount_quote"
    )
    quote_buffer: float = Field(
        default=0.01, ge=0, description="Extra quote to keep after planned LP quote amount"
    )
    settlement_wait_seconds: float = Field(
        default=45, ge=0, le=300, description="Seconds to wait for submitted swap confirmation"
    )
    settlement_poll_interval_seconds: float = Field(
        default=5, gt=0, le=60, description="Seconds between swap status checks"
    )
    post_confirm_delay_seconds: float = Field(
        default=3, ge=0, le=60, description="Extra delay after confirmation before portfolio refresh"
    )

    def model_post_init(self, __context: Any) -> None:
        if self.max_quote_spend is None:
            self.max_quote_spend = self.total_amount_quote

    @field_validator("trading_pair", "fallback_trading_pair", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return str(value or "").strip()


def _round(value: float, digits: int = 8) -> float:
    return round(float(value), digits)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plan(config: Config) -> dict[str, Any]:
    if not config.trading_pair:
        return {"status": "blocked", "reason": "missing_trading_pair"}
    if not config.lower_price < config.current_price < config.upper_price:
        return {"status": "blocked", "reason": "price_not_inside_centered_range"}
    if config.available_sol < config.min_sol_fee_buffer:
        return {"status": "blocked", "reason": "sol_fee_buffer_insufficient"}

    sqrt_p = math.sqrt(config.current_price)
    sqrt_a = math.sqrt(config.lower_price)
    sqrt_b = math.sqrt(config.upper_price)
    base_per_l = (sqrt_b - sqrt_p) / (sqrt_p * sqrt_b)
    quote_per_l = sqrt_p - sqrt_a
    denominator = (base_per_l * config.current_price) + quote_per_l
    if denominator <= 0:
        return {"status": "blocked", "reason": "invalid_range_liquidity_math"}

    budget = min(config.total_amount_quote, config.max_quote_spend or config.total_amount_quote)
    liquidity = budget / denominator
    target_base = base_per_l * liquidity
    target_quote = quote_per_l * liquidity
    swap_amount_base = max(0.0, target_base - config.available_base)
    estimated_quote_spend = swap_amount_base * config.current_price
    required_quote_balance = target_quote + estimated_quote_spend + config.quote_buffer

    status = "ready"
    reason = "rebalance_needed" if swap_amount_base > 0 else "already_balanced"
    if config.available_quote < required_quote_balance:
        status = "blocked"
        reason = "quote_balance_insufficient"

    return {
        "status": status,
        "reason": reason,
        "trading_pair": config.trading_pair,
        "fallback_trading_pair": config.fallback_trading_pair or None,
        "current_price": _round(config.current_price, 12),
        "lower_price": _round(config.lower_price, 12),
        "upper_price": _round(config.upper_price, 12),
        "budget_quote": _round(budget),
        "target_base": _round(target_base),
        "target_quote": _round(target_quote),
        "swap_amount_base": _round(swap_amount_base),
        "estimated_quote_spend": _round(estimated_quote_spend),
        "required_quote_balance": _round(required_quote_balance),
        "post_swap_base_amount": _round(target_base),
        "post_swap_quote_amount": _round(target_quote),
        "available_base": _round(config.available_base),
        "available_quote": _round(config.available_quote),
        "available_sol": _round(config.available_sol),
        "min_sol_fee_buffer": _round(config.min_sol_fee_buffer),
    }


def _find_number(value: Any, keys: set[str]) -> float | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in keys:
                parsed = _to_float(item)
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


def _to_float(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _estimate_quote_spend(quote: Any, swap_amount_base: float) -> float | None:
    amount_in = _find_number(
        quote,
        {"amount_in", "amountin", "input_amount", "inputamount", "in_amount", "inamount"},
    )
    if amount_in and amount_in > 0:
        return amount_in
    price = _find_number(quote, {"price", "rate", "executionprice", "estimatedprice"})
    if price and price > 0:
        return price * swap_amount_base
    return None


def _quote_pairs(config: Config) -> list[str]:
    pairs = [config.trading_pair]
    if (
        config.fallback_trading_pair
        and config.fallback_trading_pair.lower() != config.trading_pair.lower()
    ):
        pairs.append(config.fallback_trading_pair)
    return pairs


async def _client(context: Any) -> Any:
    from config_manager import get_client

    chat_id = getattr(context, "_chat_id", None) or 0
    return await get_client(chat_id, context=context)


async def _quote(client: Any, config: Config, pair: str, amount: float) -> Any:
    return await client.gateway_swap.get_swap_quote(
        connector=config.connector,
        network=config.network,
        trading_pair=pair,
        side="BUY",
        amount=Decimal(str(amount)),
        slippage_pct=Decimal(str(config.slippage_pct)),
    )


async def _execute(client: Any, config: Config, pair: str, amount: float) -> Any:
    return await client.gateway_swap.execute_swap(
        connector=config.connector,
        network=config.network,
        trading_pair=pair,
        side="BUY",
        amount=Decimal(str(amount)),
        slippage_pct=Decimal(str(config.slippage_pct)),
        wallet_address=config.wallet_address,
    )


async def _wait_for_settlement(client: Any, config: Config, tx_hash: str) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + config.settlement_wait_seconds
    attempts: list[dict[str, Any]] = []
    status = "SUBMITTED"

    while True:
        try:
            result = await client.gateway_swap.get_swap_status(tx_hash)
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}

        raw_status = _find_text(result, {"status"})
        status = (raw_status or status or "UNKNOWN").upper()
        attempts.append({"status": status, "result": result})

        if status in {"CONFIRMED", "FAILED", "REJECTED"}:
            if status == "CONFIRMED" and config.post_confirm_delay_seconds:
                await asyncio.sleep(config.post_confirm_delay_seconds)
            return {"status": status, "attempts": attempts}

        if asyncio.get_running_loop().time() >= deadline:
            return {"status": status, "timeout": True, "attempts": attempts}

        await asyncio.sleep(config.settlement_poll_interval_seconds)


def _format(payload: dict[str, Any]) -> str:
    lines = [f"pre_lp_rebalance: {payload.get('status', 'unknown')} ({payload.get('reason', 'n/a')})"]
    plan = payload.get("plan") or payload
    for key in (
        "trading_pair",
        "swap_amount_base",
        "target_base",
        "target_quote",
        "estimated_quote_spend",
        "required_quote_balance",
    ):
        if key in plan:
            lines.append(f"{key}: {plan[key]}")
    if payload.get("quote_pair"):
        lines.append(f"quote_pair: {payload['quote_pair']}")
    if payload.get("warnings"):
        lines.append("warnings: " + "; ".join(payload["warnings"]))
    return "\n".join(lines)


def _payload_text(payload: dict[str, Any]) -> str:
    return _format(payload) + "\n```json\n" + json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n```"


async def _save_rebalance_report(payload: dict[str, Any]) -> None:
    try:
        from condor.reports import ReportBuilder

        plan = payload.get("plan") or {}
        builder = ReportBuilder("Orca Pre-LP Rebalance")
        builder.source("routine", "pre_lp_rebalance").tags(
            ["orca", "lp", "rebalance", "agent"]
        ).manual_order()
        builder.kpi("Status", str(payload.get("status", "unknown")))
        builder.kpi("Action", str(payload.get("action", "n/a")))
        builder.kpi("Pair", str(plan.get("trading_pair") or payload.get("quote_pair") or "n/a"))
        builder.kpi("Swap Base", str(plan.get("swap_amount_base", "n/a")))
        builder.markdown("## Agent Summary\n" + payload.get("agent_prompt_summary", "No summary available."))
        if plan:
            builder.markdown("## Planned Rebalance")
            builder.table([{"Field": key, "Value": value} for key, value in plan.items()])
        builder.markdown(
            "## Routine Input Config\n```json\n"
            f"{json.dumps(payload.get('input_config', {}), indent=2, sort_keys=True, default=str)}\n"
            "```"
        )
        if payload.get("quote") is not None:
            builder.markdown(
                "## Swap Quote Debug\n```json\n"
                f"{json.dumps(payload.get('quote'), indent=2, sort_keys=True, default=str)}\n"
                "```"
            )
        if payload.get("execute_result") is not None:
            builder.markdown(
                "## Execute Result Debug\n```json\n"
                f"{json.dumps(payload.get('execute_result'), indent=2, sort_keys=True, default=str)}\n"
                "```"
            )
        if payload.get("settlement") is not None:
            builder.markdown(
                "## Settlement Debug\n```json\n"
                f"{json.dumps(payload.get('settlement'), indent=2, sort_keys=True, default=str)}\n"
                "```"
            )
        builder.markdown(
            "## Debug JSON Payload\n```json\n"
            f"{json.dumps(payload, indent=2, sort_keys=True, default=str)}\n"
            "```"
        )
        await builder.save()
    except Exception:
        return


async def _finish(payload: dict[str, Any]) -> str:
    payload["agent_prompt_summary"] = (
        f"Pre-LP rebalance {payload.get('action')} finished with "
        f"{payload.get('status')} ({payload.get('reason')})."
    )
    await _save_rebalance_report(payload)
    return _payload_text(payload)


async def run(config: Config, context: Any) -> str:
    plan = _plan(config)
    payload: dict[str, Any] = {
        "timestamp": _utc_now(),
        "status": plan["status"],
        "reason": plan["reason"],
        "action": config.action,
        "input_config": config.model_dump(),
        "plan": plan,
        "warnings": [],
    }
    if plan["status"] == "blocked":
        return await _finish(payload)

    if plan["swap_amount_base"] <= 0:
        payload.update(status="quote", reason="no_swap_needed")
        return await _finish(payload)

    try:
        client = await _client(context)
    except Exception as exc:
        payload.update(status="blocked", reason="client_unavailable")
        payload["warnings"].append(f"client error: {type(exc).__name__}: {exc}")
        return await _finish(payload)
    if not hasattr(client, "gateway_swap"):
        payload.update(status="blocked", reason="gateway_swap_unavailable")
        return await _finish(payload)

    quote_pair = config.trading_pair
    quote = None
    quote_spend = None
    quote_errors = []
    quote_pairs = _quote_pairs(config)
    for pair in quote_pairs:
        try:
            quote = await _quote(client, config, pair, plan["swap_amount_base"])
        except Exception as exc:
            quote_errors.append(f"{pair}: {type(exc).__name__}: {exc}")
            continue

        quote_pair = pair
        quote_spend = _estimate_quote_spend(quote, plan["swap_amount_base"])
        if quote_spend is not None:
            if pair != config.trading_pair:
                payload["warnings"].append("symbol quote unusable; used fallback_trading_pair")
            break
        if pair == config.trading_pair and len(quote_pairs) > 1:
            payload["warnings"].append("symbol quote returned unusable cost; trying fallback_trading_pair")

    if quote is None:
        payload.update(status="blocked", reason="quote_failed")
        payload["warnings"].extend(f"quote error: {error}" for error in quote_errors)
        return await _finish(payload)

    payload["quote_pair"] = quote_pair
    payload["quote"] = quote
    if quote_spend is None:
        payload.update(status="quote", reason="quote_ok_cost_unparsed")
        payload["warnings"].append("quote cost could not be parsed; execute blocked")
    else:
        plan["estimated_quote_spend"] = _round(quote_spend)
        if quote_spend > (config.max_quote_spend or config.total_amount_quote):
            payload.update(status="blocked", reason="quote_spend_above_cap")
        elif config.available_quote - quote_spend < plan["target_quote"] + config.quote_buffer:
            payload.update(status="blocked", reason="post_swap_quote_insufficient")
        else:
            payload.update(status="quote", reason="quote_ok")

    if config.action == "quote" or payload["status"] != "quote" or payload["reason"] != "quote_ok":
        return await _finish(payload)

    try:
        result = await _execute(client, config, quote_pair, plan["swap_amount_base"])
    except Exception as exc:
        payload.update(status="failed", reason="execute_failed")
        payload["warnings"].append(f"execute error: {type(exc).__name__}: {exc}")
        return await _finish(payload)

    payload["execute_result"] = result
    tx_hash = _find_text(result, {"transaction_hash", "transactionhash", "tx_hash", "txhash", "hash"})
    if not tx_hash:
        payload.update(status="executed", reason="swap_submitted_untracked")
        payload["warnings"].append("execute result did not include a transaction hash")
        return await _finish(payload)

    payload["transaction_hash"] = tx_hash
    settlement = await _wait_for_settlement(client, config, tx_hash)
    payload["settlement"] = settlement
    settlement_status = settlement.get("status")
    if settlement_status == "CONFIRMED":
        payload.update(status="executed", reason="swap_confirmed")
    elif settlement_status in {"FAILED", "REJECTED"}:
        payload.update(status="failed", reason="swap_failed")
    else:
        payload.update(status="executed", reason="swap_submitted_unconfirmed")
        payload["warnings"].append("swap was submitted but not confirmed before timeout")
    return await _finish(payload)


def _self_check() -> None:
    config = Config(
        trading_pair="USDT-USDC",
        current_price=1,
        lower_price=0.999,
        upper_price=1.001,
        total_amount_quote=1,
        available_quote=2,
        available_sol=0.3999,
    )
    plan = _plan(config)
    assert config.action == "quote", config
    assert config.max_quote_spend == 1, config
    assert plan["status"] == "ready", plan
    assert plan["reason"] == "rebalance_needed", plan
    assert plan["swap_amount_base"] > 0, plan
    assert plan["trading_pair"] == "USDT-USDC", plan
    assert plan["target_base"] > 0, plan
    assert plan["target_quote"] > 0, plan
    assert _estimate_quote_spend({"price": "0", "amount_in": None}, 10) is None
    assert _estimate_quote_spend({"price": "0", "amount_in": "4.5"}, 10) == 4.5
    assert _find_text({"result": {"transaction_hash": "abc"}}, {"transaction_hash"}) == "abc"
    assert _find_text({"result": {"status": "CONFIRMED"}}, {"status"}) == "CONFIRMED"
    assert _plan(config.model_copy(update={"available_base": 2}))["reason"] == "already_balanced"
    blocked = _plan(config.model_copy(update={"available_sol": 0.01}))
    assert blocked["reason"] == "sol_fee_buffer_insufficient", blocked


if __name__ == "__main__":
    import asyncio
    import sys

    if "--self-check" in sys.argv:
        _self_check()
        print("self-check passed")
    else:
        print(asyncio.run(run(Config(), None)))
