"""Validate an automatic lending request against an operator-owned API grant."""

import math
import re
from decimal import Decimal

from condor.fetchers.market_data import fetch_current_price


async def lending_grant(
    input_data: dict, client, agent_id: str
) -> tuple[str, float, float]:
    cfg = input_data.get("executor_config") or {}
    if (
        not agent_id
        or cfg.get("mode") != "lending"
        or cfg.get("require_lending_policy") is not True
    ):
        raise ValueError(
            "Automatic lending requires a named agent and an enforced operator policy"
        )
    if (
        cfg.get("controller_id") != agent_id
        or (input_data.get("controller_id") or agent_id) != agent_id
    ):
        raise ValueError("Lending controller must match the current agent")
    policy = await client.executors._get("/executors/lending/policy")
    if (
        not isinstance(policy, dict)
        or policy.get("enabled") is not True
        or policy.get("automatic_admission") != "database_serialized"
    ):
        raise ValueError("The API has no active durable lending grant")
    account = input_data.get("account_name") or "master_account"
    if account != policy.get("account_name") or agent_id not in policy.get(
        "controller_limits_raw", {}
    ):
        raise ValueError("Agent or account has no lending grant")
    plan = cfg.get("lending")
    if not isinstance(plan, dict) or plan.get("action") not in {"supply", "withdraw"}:
        raise ValueError("A supply or withdrawal plan is required")
    if (
        cfg.get("chain", "evm") != "evm"
        or cfg.get("chain_id") != policy.get("chain_id")
        or policy.get("decimals") != 6
    ):
        raise ValueError("Lending chain or asset precision does not match policy")
    for field in ("chain_id", "wallet", "pool", "asset"):
        actual, expected = plan.get(field), policy.get(field)
        if isinstance(actual, str) and isinstance(expected, str):
            actual, expected = actual.lower(), expected.lower()
        if actual is None or actual != expected:
            raise ValueError("Lending authority differs from the operator grant")

    def units(value):
        if type(value) is int:
            value = str(value)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
            raise ValueError("Lending amounts must be exact raw units")
        result = int(value)
        if not 0 <= result < 2**256 - 1:
            raise ValueError("Lending amount is out of range")
        return result

    amount = units(plan.get("amount"))
    gas, gas_limit = Decimal(str(cfg.get("max_gas_quote"))), Decimal(
        str(policy.get("max_gas_quote"))
    )
    if (
        amount <= 0
        or amount > units(policy.get("max_action_raw"))
        or not gas.is_finite()
        or not gas_limit.is_finite()
        or not 0 < gas <= gas_limit
    ):
        raise ValueError("Lending action exceeds its grant")
    history = await client.executors._get("/executors/lending/positions")
    if not isinstance(history, dict) or not isinstance(history.get("positions"), list):
        raise ValueError("Durable lending history is unavailable")
    own = total = withdrawable = 0
    for row in history["positions"]:
        if (
            not isinstance(row, dict)
            or not row.get("controller_id")
            or not row.get("account_name")
        ):
            raise ValueError("Lending history has missing attribution")
        market_matches = all(
            row.get(k) == policy.get(k) for k in ("chain_id", "wallet", "pool", "asset")
        )
        ours = row["account_name"] == account and row["controller_id"] == agent_id
        if ours and not market_matches:
            raise ValueError(
                "Other lending markets require separate exposure reconciliation"
            )
        if not market_matches:
            continue
        net = units(row.get("net_contributed_raw"))
        reserved = net + units(row.get("pending_supply_raw"))
        total += reserved
        if ours:
            own += reserved
            withdrawable += max(0, net - units(row.get("pending_withdraw_raw")))
    action = plan["action"]
    if action == "supply":
        if own + amount > units(
            policy["controller_limits_raw"][agent_id]
        ) or total + amount > units(policy.get("max_total_supply_raw")):
            raise ValueError("Lending allocation exceeds remaining operator grant")
    elif amount > withdrawable:
        raise ValueError(
            "Withdrawal exceeds this controller's unreserved contributions"
        )
    # The exact asset is selected by the API's fixed Base USDC policy, never by a display pair.
    if (
        policy.get("chain_id") != 8453
        or str(policy.get("asset", "")).lower()
        != "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    ):
        raise ValueError("No trusted quote mapping for this policy asset")
    price = await fetch_current_price(client, "binance", "USDC-USDT")
    rate = Decimal(str(price))
    if not rate.is_finite() or rate <= 0:
        raise ValueError("USDC/USDT valuation is unavailable")
    exposure = float(Decimal(own) / Decimal(1_000_000) * rate)
    planned = (
        float(Decimal(amount) / Decimal(1_000_000) * rate)
        if action == "supply"
        else 0.0
    )
    if not math.isfinite(exposure) or not math.isfinite(planned):
        raise ValueError("Lending quote exposure is out of range")
    return action, planned, exposure


async def lending_exposure(client, positions: list[dict]) -> float:
    """Value controller contributions and pending supply, independently of tx status."""
    if not positions:
        return 0.0
    raw = 0
    for row in positions:
        if (
            row.get("chain_id") != 8453
            or row.get("asset") != "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
        ):
            raise ValueError("Lending asset cannot be valued")
        for key in ("net_contributed_raw", "pending_supply_raw"):
            value = row.get(key)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
                raise ValueError("Lending amount cannot be valued")
            raw += int(value)
    price = await fetch_current_price(client, "binance", "USDC-USDT")
    rate = Decimal(str(price))
    result = float(Decimal(raw) / Decimal(1_000_000) * rate)
    if not rate.is_finite() or rate <= 0 or not math.isfinite(result):
        raise ValueError("Lending valuation is unavailable")
    return result
