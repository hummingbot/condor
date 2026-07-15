"""Tool-call classification: what needs confirmation, what is blocked, and
what counts as a system mutation.

Relocated from ``handlers/agents/_shared.py`` (simplification plan §5.1) —
transport-agnostic; consumed by the permission policies, the risk gate, and
the web chat.
"""

from __future__ import annotations

from typing import Any

# Tools that require user confirmation before execution
DANGEROUS_TOOLS = {
    "place_order",
    "manage_gateway_swaps",  # execute action
    "manage_gateway_clmm",  # open/close position
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

# Actions within manage_gateway_clmm that require confirmation
DANGEROUS_CLMM_ACTIONS = {"open_position", "close_position"}


def is_dangerous_tool_call(tool_call: dict[str, Any]) -> bool:
    """Check if a tool call requires user confirmation."""
    raw_name = tool_call.get("tool", "") or tool_call.get("title", "")
    # Normalize MCP-prefixed names (e.g. mcp__mcp-hummingbot__manage_executors → manage_executors)
    tool_name = raw_name.rsplit("__", 1)[-1] if "__" in raw_name else raw_name

    # Direct dangerous tools
    if tool_name in DANGEROUS_TOOLS:
        # For manage_gateway_swaps, only "execute" action is dangerous
        if tool_name == "manage_gateway_swaps":
            input_data = tool_call.get("input", {})
            action = input_data.get("action", "")
            return action in DANGEROUS_SWAP_ACTIONS

        # For manage_gateway_clmm, only open/close are dangerous
        if tool_name == "manage_gateway_clmm":
            input_data = tool_call.get("input", {})
            action = input_data.get("action", "")
            return action in DANGEROUS_CLMM_ACTIONS

        return True

    # manage_executors with create/stop actions
    if tool_name == "manage_executors":
        input_data = tool_call.get("input", {})
        action = input_data.get("action", "")
        return action in DANGEROUS_EXECUTOR_ACTIONS

    # manage_bots with deploy/stop/update actions (a bot deploy places real
    # capital via a controller, a different path than manage_executors)
    if tool_name == "manage_bots":
        input_data = tool_call.get("input", {})
        action = input_data.get("action", "")
        return action in DANGEROUS_BOT_ACTIONS

    return False


# System-MUTATION actions per tool — reshaping the agent/routine/skill system,
# distinct from placing trades or writing an agent's own memory. Read/run/list
# actions are deliberately excluded (they are safe). Used to gate ACP agents to
# their declared tool scope (#8): mutations they never declared are denied.
SYSTEM_MUTATION_ACTIONS: dict[str, set[str]] = {
    "manage_trading_agent": {
        "create_agent", "update_agent", "delete_agent",
        "create_strategy", "update_strategy", "delete_strategy",
    },
    "manage_routines": {"create_routine", "edit_routine", "delete_routine"},
    "manage_skill": {"create", "write_file", "patch", "edit", "delete"},
}


def system_mutation_tool(tool_call: dict[str, Any]) -> str | None:
    """If this call mutates the agent/routine/skill system, return the tool's
    short name; else ``None``. Trading, an agent's own memory, and every
    read/run/list action are NOT system mutations."""
    raw_name = tool_call.get("tool", "") or tool_call.get("title", "")
    tool_name = raw_name.rsplit("__", 1)[-1] if "__" in raw_name else raw_name
    actions = SYSTEM_MUTATION_ACTIONS.get(tool_name)
    if actions is None:
        return None
    action = (tool_call.get("input", {}) or {}).get("action", "")
    return tool_name if action in actions else None
