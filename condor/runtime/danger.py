"""Security-critical tool-call classification (ARCH-190).

Which tool calls need a human, which are refused outright, and how a pending
call is summarized for the confirmation prompt. This used to live in the
Telegram package (``handlers/agents/_shared.py`` / ``confirmation.py``), which
``main.py`` hot-reloads via watchfiles — so the runtime's trade gate depended
on reload timing. It is platform-neutral and belongs to the runtime; the
handlers modules re-export from here for their Telegram callers.

Stdlib-only on purpose: everything that dispatches on a tool name (the danger
list, the risk gate, the confirmation summary) imports this module, so it must
never grow an import edge back into the runtime or the handlers.
"""

from __future__ import annotations

import json
from typing import Any

# Tools that require user confirmation before execution
DANGEROUS_TOOLS = {
    "place_order",
    "manage_gateway_swaps",  # execute action
    "manage_clmm",  # every action that moves liquidity
    "manage_amm",  # every action that moves liquidity
    "manage_gateway_config",  # only the wallets resource; see below
}

# Tools that are always blocked (RBAC bypass prevention)
BLOCKED_TOOLS: set[str] = set()

# Actions within manage_executors that require confirmation
DANGEROUS_EXECUTOR_ACTIONS = {"create", "stop"}

# Actions within manage_bots that deploy/mutate a live bot (status/logs/get_config
# are read-only and excluded). manage_controllers itself is excluded entirely — it
# only writes controller templates/saved configs, never a running bot (see its own
# tool docstring: "Does NOT affect running bots").
DANGEROUS_BOT_ACTIONS = {
    "deploy",
    "stop_bot",
    "stop_controllers",
    "start_controllers",
    "update_config",
}

# Actions within manage_gateway_swaps that require confirmation
DANGEROUS_SWAP_ACTIONS = {"execute"}

# Actions within manage_clmm that require confirmation. These are the tool's
# own action literals — a name that does not match one lets the call through
# ungated, so they are asserted against the registered tool in the tests.
DANGEROUS_CLMM_ACTIONS = {
    "open",
    "close",
    "add_liquidity",
    "remove_liquidity",
    "collect_fees",
    "create_pool",
}

# Actions within manage_amm that require confirmation
DANGEROUS_AMM_ACTIONS = {"add_liquidity", "remove_liquidity", "create_pool"}

# Resource types within manage_gateway_config that require confirmation. This tool
# is gated on `resource_type`, not `action`, because what it edits matters and how
# it edits does not: `wallets` + `add` takes a PRIVATE KEY, and `delete` removes a
# signing wallet. Everything else it touches — tokens, pools, connectors, networks —
# is Gateway's own symbol/address mapping. Deleting a token there moves no funds and
# changes nothing on-chain, so gating it would put a human in front of a config edit
# while the trades that edit enables stay where they are.
DANGEROUS_CONFIG_RESOURCES = {"wallets"}


def tool_call_name(tool_call: dict[str, Any]) -> str:
    """The bare tool name, with any MCP prefix stripped.

    ACP names a tool by its wire name (``mcp__mcp-hummingbot__manage_bots``)
    where the local surfaces use the bare one, so everything that dispatches on
    a tool name — the danger list, the risk gate, the confirmation summary —
    has to normalize identically or they disagree about the same call.
    """
    raw_name = tool_call.get("tool", "") or tool_call.get("title", "")
    return raw_name.rsplit("__", 1)[-1] if "__" in raw_name else raw_name


def tool_call_input(tool_call: dict[str, Any]) -> dict[str, Any] | None:
    """A tool call's arguments as a mapping, or ``None`` when unreadable.

    ``None`` means "I cannot tell what this call does", and every caller has to
    fail closed on it: an action-gated tool whose action can't be read is
    treated as dangerous rather than waved through (SEC-093). Callers must not
    reach for other spellings of the arguments — the ACP wire's ``rawInput`` is
    translated once, at the boundary in :func:`condor.acp.client.normalize_tool_call`.

    A JSON string is parsed: OpenAI-compatible providers deliver tool arguments
    that way.
    """
    args = tool_call.get("input")
    if isinstance(args, dict):
        return args
    if isinstance(args, str) and args.strip():
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _has_dangerous_action(
    tool_call: dict[str, Any], dangerous_actions: set[str]
) -> bool:
    """Whether an action-gated tool call selects one of its dangerous actions.

    Fails closed (SEC-093): unreadable arguments, or a missing/non-string
    ``action``, count as dangerous. ``action`` is required by every one of
    these tools, so an unreadable one is never a benign read — it is a call we
    failed to understand, and those belong in front of a human.
    """
    input_data = tool_call_input(tool_call)
    if input_data is None:
        return True
    action = input_data.get("action")
    if not isinstance(action, str) or not action:
        return True
    return action in dangerous_actions


def _has_dangerous_resource(
    tool_call: dict[str, Any], dangerous_resources: set[str]
) -> bool:
    """Whether a resource-gated tool call selects one of its dangerous resources.

    The resource-typed twin of :func:`_has_dangerous_action`, and it fails closed the
    same way (SEC-093): unreadable arguments, or a missing/non-string ``resource_type``,
    count as dangerous.
    """
    input_data = tool_call_input(tool_call)
    if input_data is None:
        return True
    resource = input_data.get("resource_type")
    if not isinstance(resource, str) or not resource:
        return True
    return resource in dangerous_resources


def is_dangerous_tool_call(tool_call: dict[str, Any]) -> bool:
    """Check if a tool call requires user confirmation."""
    tool_name = tool_call_name(tool_call)

    # Direct dangerous tools
    if tool_name in DANGEROUS_TOOLS:
        # For manage_gateway_swaps, only "execute" action is dangerous
        if tool_name == "manage_gateway_swaps":
            return _has_dangerous_action(tool_call, DANGEROUS_SWAP_ACTIONS)

        # For the LP tools, only the actions that move liquidity are dangerous
        if tool_name == "manage_clmm":
            return _has_dangerous_action(tool_call, DANGEROUS_CLMM_ACTIONS)

        if tool_name == "manage_amm":
            return _has_dangerous_action(tool_call, DANGEROUS_AMM_ACTIONS)

        if tool_name == "manage_gateway_config":
            return _has_dangerous_resource(tool_call, DANGEROUS_CONFIG_RESOURCES)

        return True

    # manage_executors with create/stop actions
    if tool_name == "manage_executors":
        return _has_dangerous_action(tool_call, DANGEROUS_EXECUTOR_ACTIONS)

    # manage_bots with deploy/stop/update actions (a bot deploy places real
    # capital via a controller, a different path than manage_executors)
    if tool_name == "manage_bots":
        return _has_dangerous_action(tool_call, DANGEROUS_BOT_ACTIONS)

    return False


def format_tool_summary(tool_call: dict[str, Any]) -> str:
    """Format a tool call into a human-readable summary for the confirmation message."""
    # The bare name, or an ACP call (``mcp__mcp-hummingbot__manage_bots``) would
    # match none of the branches below and be approved as an opaque string.
    tool_name = tool_call_name(tool_call) or "Unknown"
    input_data = tool_call_input(tool_call)
    if input_data is None:
        # The gate sends unreadable calls here on purpose (SEC-093). Say so,
        # rather than rendering a row of "?" that looks like a parsed call.
        return f"{tool_name} (arguments could not be read)"

    if tool_name == "place_order":
        side = input_data.get("trade_type", "?")
        pair = input_data.get("trading_pair", "?")
        amount = input_data.get("amount", "?")
        order_type = input_data.get("order_type", "MARKET")
        price = input_data.get("price", "")
        connector = input_data.get("connector_name", "?")
        summary = f"{side} {amount} {pair} ({order_type})"
        if price:
            summary += f" @ {price}"
        summary += f" on {connector}"
        return summary

    if tool_name == "manage_executors":
        action = input_data.get("action", "?")
        exec_type = input_data.get("executor_type", "")
        exec_id = input_data.get("executor_id", "")
        if action == "create" and exec_type:
            config = input_data.get("executor_config", {})
            pair = config.get("trading_pair", "?")
            return f"Create {exec_type} on {pair}"
        if action == "stop" and exec_id:
            return f"Stop executor {exec_id[:12]}..."
        return f"Executor: {action}"

    if tool_name == "manage_bots":
        action = input_data.get("action", "?")
        bot_name = input_data.get("bot_name", "?")
        if action == "deploy":
            controllers = input_data.get("controllers_config", [])
            return f"Deploy bot '{bot_name}' with controllers {controllers}"
        if action == "update_config":
            config_name = input_data.get("config_name", "?")
            return f"Update config '{config_name}' on bot '{bot_name}'"
        return f"Bot '{bot_name}': {action}"

    if tool_name == "manage_gateway_swaps":
        action = input_data.get("action", "?")
        pair = input_data.get("trading_pair", "?")
        side = input_data.get("side", "?")
        amount = input_data.get("amount", "?")
        return f"Swap {side} {amount} {pair}"

    if tool_name == "manage_gateway_config":
        resource = input_data.get("resource_type", "?")
        action = input_data.get("action", "?")
        if resource == "wallets":
            if action == "add":
                chain = input_data.get("chain", "?")
                return f"Import a {chain} wallet into Gateway (private key)"
            if action == "delete":
                addr = str(input_data.get("wallet_address") or "?")
                return f"Remove wallet {addr[:12]}... from Gateway"
        return f"Gateway config: {action} {resource}"

    if tool_name in ("manage_clmm", "manage_amm"):
        action = input_data.get("action", "?")
        kind = "CLMM" if tool_name == "manage_clmm" else "AMM"
        connector = input_data.get("connector", "?")
        pool = (
            input_data.get("pool_address") or input_data.get("position_address") or "?"
        )
        if action == "open":
            lower = input_data.get("lower_price", "?")
            upper = input_data.get("upper_price", "?")
            return (
                f"Open {kind} position on {connector} pool {pool} over {lower}-{upper}"
            )
        if action == "close":
            return f"Close {kind} position {pool} on {connector}"
        if action == "add_liquidity":
            base = input_data.get("base_token_amount", "?")
            quote = input_data.get("quote_token_amount", "?")
            return f"Add {base} base / {quote} quote to {kind} {pool} on {connector}"
        if action == "remove_liquidity":
            pct = input_data.get("percentage_to_remove", "?")
            return f"Remove {pct}% from {kind} position {pool} on {connector}"
        if action == "collect_fees":
            return f"Collect fees from {kind} position {pool} on {connector}"
        if action == "create_pool":
            base = input_data.get("base_token", "?")
            quote = input_data.get("quote_token", "?")
            return f"Create {kind} pool {base}-{quote} on {connector}"
        return f"{kind}: {action}"

    # Generic fallback
    return tool_name
