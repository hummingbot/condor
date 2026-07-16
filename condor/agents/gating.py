"""Tool-call classification: what needs confirmation, what is blocked, and
what counts as a system mutation.

Relocated from ``handlers/agents/_shared.py`` (simplification plan §5.1) —
transport-agnostic; consumed by the permission policies, the risk gate, and
the web chat.
"""

from __future__ import annotations

from typing import Any

# Tools that require user confirmation regardless of action. Trading flows
# entirely through manage_executors (action-scoped below), so this is empty —
# kept as the extension point the policies/gates already consume.
DANGEROUS_TOOLS: set[str] = set()

# Tools that are always blocked (RBAC bypass prevention)
BLOCKED_TOOLS: set[str] = set()

# Actions within manage_executors that require confirmation
DANGEROUS_EXECUTOR_ACTIONS = {"create", "stop"}


def is_dangerous_tool_call(tool_call: dict[str, Any]) -> bool:
    """Check if a tool call requires user confirmation."""
    raw_name = tool_call.get("tool", "") or tool_call.get("title", "")
    # Normalize MCP-prefixed names (e.g. mcp__condor__manage_executors → manage_executors)
    tool_name = raw_name.rsplit("__", 1)[-1] if "__" in raw_name else raw_name

    if tool_name in DANGEROUS_TOOLS:
        return True

    # manage_executors with create/stop actions — the one trading mutation path
    if tool_name == "manage_executors":
        input_data = tool_call.get("input", {})
        action = input_data.get("action", "")
        return action in DANGEROUS_EXECUTOR_ACTIONS

    return False


# System MUTATIONS — reshaping the agent/routine/skill system, distinct from
# placing trades or writing an agent's own memory. Read/run/list actions are
# deliberately excluded (they are safe). Used to gate ACP agents to their
# declared tool scope (#8): mutations they never declared are denied.
#
# Agent CRUD is explicit tools now (§8) — the whole tool is the mutation;
# routines/skills keep action-scoped dispatchers.
SYSTEM_MUTATION_TOOLS: set[str] = {"create_agent", "update_agent", "delete_agent"}
SYSTEM_MUTATION_ACTIONS: dict[str, set[str]] = {
    "manage_routines": {
        "create_routine", "edit_routine", "delete_routine",
        "schedule_routine", "unschedule_routine",
    },
    "manage_skill": {"create", "write_file", "patch", "edit", "delete"},
}


def system_mutation_tool(tool_call: dict[str, Any]) -> str | None:
    """If this call mutates the agent/routine/skill system, return the tool's
    short name; else ``None``. Trading, an agent's own memory, and every
    read/run/list action are NOT system mutations."""
    raw_name = tool_call.get("tool", "") or tool_call.get("title", "")
    tool_name = raw_name.rsplit("__", 1)[-1] if "__" in raw_name else raw_name
    if tool_name in SYSTEM_MUTATION_TOOLS:
        return tool_name
    actions = SYSTEM_MUTATION_ACTIONS.get(tool_name)
    if actions is None:
        return None
    action = (tool_call.get("input", {}) or {}).get("action", "")
    return tool_name if action in actions else None
