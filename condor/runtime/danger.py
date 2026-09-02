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
import math
from typing import Any

#: Every executor-creating tool, by name. The typed split (FEAT-062) gave each
#: executor type its own tool, so creating one is no longer an ``action`` inside a
#: mega-tool — reaching any of these names at all is the create.
CREATE_EXECUTOR_TOOLS = frozenset(
    {
        "create_position_executor",
        "create_grid_executor",
        "create_dca_executor",
        "create_order_executor",
        "create_lp_executor",
    }
)

# Tools that require user confirmation before execution
DANGEROUS_TOOLS = {
    "place_order",
    "execute_swap",  # every call signs; quote/status/search are separate tools
    "manage_clmm",  # every action that moves liquidity
    "manage_amm",  # every action that moves liquidity
    "manage_gateway_config",  # no resource of it is gated today; see below
    "control_agent",  # only `start`, which launches an unattended trading loop
    # The executor family is gated by NAME (FEAT-062), the same way the swap family
    # is: a create and a stop each have their own tool, so there is no `action` to
    # read out of the arguments and no fail-closed ambiguity. Every other executor
    # tool (list_executors, get_executor, list_positions_held, get_performance_report,
    # list_orphaned_positions, resolve_orphaned_position, clear_position_held,
    # executor_defaults) is safe by name and never reaches a human.
    *CREATE_EXECUTOR_TOOLS,
    "stop_executor",
}

# Tools that are always blocked (RBAC bypass prevention)
BLOCKED_TOOLS: set[str] = set()

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

# There is no DANGEROUS_SWAP_ACTIONS: the swap family is gated by NAME. `execute_swap`
# is its own tool (FEAT-064), so the gate no longer has to read an `action` out of the
# arguments to tell a free quote from a signature — reaching `execute_swap` at all is
# the signature, and `quote_swap` / `get_swap_status` / `search_swaps` are never gated.

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

# Actions within control_agent that require confirmation (SEC-275). `start` is
# the third capital path and the widest of them: it launches a TickEngine that
# trades unattended every N seconds until stopped, spawning executors and
# deploying controller-mode bots on its own — and the caller picks the
# execution_mode, total_amount_quote and risk_limits it runs with. Creating one
# executor already needs a human, so starting the loop that creates hundreds
# does too.
#
# Everything else stays on the fast path. `list`, `get_state` and `set_state`
# read or scribble on an instance's own scratch namespace. `stop`, `pause` and
# `resume` are the brakes, and a confirmation in front of a brake is a
# confirmation in front of the user stopping their own loop.
#
# `shutdown` is deliberately ungated too, though it does wind positions down: it
# is the emergency exit, and the failure mode of prompting for it (a human is
# away, the wind-down waits) is worse than the failure mode of not prompting (an
# agent exits the market early). Exposure-reducing calls are let through
# elsewhere for the same reason — see check_dex_action's remove_liquidity note.
#
# The legacy `*_agent` spellings are accepted by the tool's own _resolve_action,
# so the gate has to know both or `start_agent` walks straight past it.
DANGEROUS_CONTROL_ACTIONS = {"start", "start_agent"}

# Resource types within manage_gateway_config that require confirmation. This tool
# is gated on `resource_type`, not `action`, because what it edits matters and how
# it edits does not. The set is empty and the gate stays: `wallets` used to be in it
# because `add` took a PRIVATE KEY, but that path no longer exists over MCP (wallets
# are read-only there, added and removed in the dashboard), so nothing this tool can
# reach is worth a human. Everything it still touches — tokens, pools, connectors,
# networks — is Gateway's own symbol/address mapping. Deleting a token there moves no
# funds and changes nothing on-chain, so gating it would put a human in front of a
# config edit while the trades that edit enables stay where they are. An unreadable
# `resource_type` still fails closed.
DANGEROUS_CONFIG_RESOURCES: set[str] = set()


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


def _short_address(value: Any) -> str:
    """An on-chain address abbreviated for a confirmation line.

    The human approving a signature needs to recognize the pool, not read 44
    base58 characters, and a missing address has to render as "?" rather than
    crash the summary of a call that is about to move funds.
    """
    if not isinstance(value, str) or not value:
        return "?"
    return f"{value[:8]}..." if len(value) > 8 else value


def _quote_ladder_total(values: list[Any]) -> str | None:
    """The sum of a DCA ladder's quote amounts, or ``None`` if a rung is unreadable.

    The summary reads *wire* arguments: ``normalize_tool_call`` hands the raw
    ``rawInput`` through and the MCP server's pydantic coercion of
    ``amounts_quote: list[float]`` happens later, so a perfectly valid call can
    arrive as ``["100", "100"]``. Summing that raw raises inside the permission
    callback, and an exception there is turned into a *cancellation* by the ACP
    client — the human never sees the prompt and a legitimate create is denied
    (CORR-294). So each rung is parsed the tolerant way ``risk._quote_amount``
    already parses one, and anything that will not parse returns ``None`` so the
    caller can drop the total rather than invent or misstate one.
    """
    total = 0.0
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return None
        if isinstance(value, str):
            value = value.strip().removeprefix("$")
        try:
            total += float(value)
        except (TypeError, ValueError):
            return None
    if not math.isfinite(total):
        return None
    # Integral ladders keep their bare form ("100", not "100.0"): the figure a
    # human approves should read the way the model wrote it.
    return str(int(total)) if total.is_integer() else str(total)


def _executor_amount(tool_name: str, input_data: dict[str, Any]) -> str:
    """The size of a pending executor create, for the confirmation line.

    Each executor type denominates its size in a different field and a different
    currency -- base for a position, quote for a grid, a ladder of quote amounts for a
    DCA, one or both legs for an LP position. A human approving a create is approving
    that number, so it is spelled out per type rather than omitted.
    """
    if tool_name == "create_grid_executor":
        return f" for {input_data.get('total_amount_quote', '?')} quote"
    if tool_name == "create_dca_executor":
        amounts = input_data.get("amounts_quote")
        if isinstance(amounts, list) and amounts:
            total = _quote_ladder_total(amounts)
            if total is None:
                # A rung we cannot read means we cannot state the size. Say what
                # is certain — the ladder's depth — instead of a partial total
                # that would understate what the human is approving.
                return f" over {len(amounts)} levels"
            return f" for {total} quote over {len(amounts)} levels"
        return ""
    if tool_name == "create_lp_executor":
        base = input_data.get("base_amount") or 0
        quote = input_data.get("quote_amount") or 0
        return f" with {base} base / {quote} quote"
    amount = input_data.get("amount")
    return f" of {amount}" if amount is not None else ""


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
        # For the LP tools, only the actions that move liquidity are dangerous
        if tool_name == "manage_clmm":
            return _has_dangerous_action(tool_call, DANGEROUS_CLMM_ACTIONS)

        if tool_name == "manage_amm":
            return _has_dangerous_action(tool_call, DANGEROUS_AMM_ACTIONS)

        if tool_name == "manage_gateway_config":
            return _has_dangerous_resource(tool_call, DANGEROUS_CONFIG_RESOURCES)

        if tool_name == "control_agent":
            return _has_dangerous_action(tool_call, DANGEROUS_CONTROL_ACTIONS)

        return True

    # manage_bots with deploy/stop/update actions (a bot deploy places real
    # capital via a controller, a different path than the executor tools)
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

    if tool_name in CREATE_EXECUTOR_TOOLS:
        # The typed tools put the numbers the human is approving at the top level,
        # so the summary reads them straight instead of digging through a config
        # blob. Each type sizes itself differently, hence the per-type amount.
        pair = input_data.get("trading_pair", "?")
        kind = tool_name.removeprefix("create_").removesuffix("_executor")
        amount = _executor_amount(tool_name, input_data)
        return f"Create {kind} executor on {pair}{amount}"

    if tool_name == "stop_executor":
        exec_id = str(input_data.get("executor_id", "") or "")
        keep = input_data.get("keep_position", False)
        if not exec_id:
            return "Stop executor (id could not be read)"
        suffix = ", keeping the position" if keep else ""
        return f"Stop executor {exec_id[:12]}...{suffix}"

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

    if tool_name == "control_agent":
        # The human is approving an unattended loop, so the line has to name the
        # strategy it will run and — when the caller overrode them — the two
        # numbers that decide how much it can lose. Without this the prompt says
        # "control_agent" and a config dict.
        action = input_data.get("action", "?")
        if action in DANGEROUS_CONTROL_ACTIONS:
            strategy = input_data.get("strategy_id") or "?"
            overrides = input_data.get("config")
            overrides = overrides if isinstance(overrides, dict) else {}
            summary = f"Start a live agent loop on '{strategy}'"
            mode = overrides.get("execution_mode")
            if mode:
                summary += f" in {mode} mode"
            amount = overrides.get("total_amount_quote")
            if amount is not None:
                summary += f", sized {amount} quote"
            return summary
        agent_id = str(input_data.get("agent_id") or "?")
        return f"Agent instance {agent_id}: {action}"

    if tool_name == "execute_swap":
        pair = input_data.get("trading_pair", "?")
        side = input_data.get("side", "?")
        amount = input_data.get("amount", "?")
        return f"Swap {side} {amount} {pair}"

    if tool_name == "manage_gateway_config":
        # The wallet import/remove summaries lived here until the tool stopped
        # accepting a private key at all (FEAT-065); wallets are read-only now.
        resource = input_data.get("resource_type", "?")
        action = input_data.get("action", "?")
        return f"Gateway config: {action} {resource}"

    if tool_name in ("manage_clmm", "manage_amm"):
        action = input_data.get("action", "?")
        kind = "CLMM" if tool_name == "manage_clmm" else "AMM"
        connector = input_data.get("connector", "?")
        pool = _short_address(
            input_data.get("pool_address") or input_data.get("position_address")
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
