"""Constants and MCP config loader for agent sessions."""

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

AGENT_OPTIONS: dict[str, dict[str, str]] = {
    "claude-code": {"label": "Claude Code", "protocol": "claude"},
    "gemini": {"label": "Gemini CLI", "protocol": "gemini"},
}

DEFAULT_AGENT = "claude-code"

# Tools that require user confirmation before execution
DANGEROUS_TOOLS = {
    "place_order",
    "manage_gateway_swaps",  # execute action
    "manage_gateway_clmm",  # open/close position
}

# Tools that are always blocked (RBAC bypass prevention)
BLOCKED_TOOLS = {"configure_api_servers"}

# Actions within manage_executors that require confirmation
DANGEROUS_EXECUTOR_ACTIONS = {"create", "stop"}

# Actions within manage_gateway_swaps that require confirmation
DANGEROUS_SWAP_ACTIONS = {"execute"}

# Actions within manage_gateway_clmm that require confirmation
DANGEROUS_CLMM_ACTIONS = {"open_position", "close_position"}


def is_dangerous_tool_call(tool_call: dict[str, Any]) -> bool:
    """Check if a tool call requires user confirmation."""
    tool_name = tool_call.get("tool", "") or tool_call.get("title", "")

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

    return False


def get_project_dir() -> str:
    """Get the condor project root directory (where .mcp.json lives).

    The ACP agent auto-discovers stdio MCP servers from .mcp.json in the cwd,
    so we just need to point it at the project root.
    """
    return str(Path(__file__).parent.parent.parent)


def build_mcp_servers_for_session(
    user_id: int, chat_id: int, widget_port: int
) -> list[dict[str, Any]]:
    """Build dynamic MCP server configs for an agent session.

    Resolves the user's default Condor server and returns ACP-format mcpServers
    that override the static .mcp.json entries by name.
    """
    from config_manager import get_config_manager

    cm = get_config_manager()

    # Resolve which server to use
    server_name = cm.get_chat_default_server(chat_id)
    if not server_name:
        accessible = cm.get_accessible_servers(user_id)
        server_name = accessible[0] if accessible else None

    if not server_name:
        return []  # Fall back to .mcp.json auto-discovery

    server = cm.get_server(server_name)
    if not server:
        return []

    api_url = f"http://{server['host']}:{server['port']}"

    mcp_hummingbot = {
        "name": "mcp-hummingbot",
        "command": "uv",
        "args": [
            "--directory",
            "/Users/dman/Documents/mcp",
            "run",
            "main.py",
        ],
        "env": [
            {"name": "HUMMINGBOT_API_URL", "value": api_url},
            {"name": "HUMMINGBOT_USERNAME", "value": server["username"]},
            {"name": "HUMMINGBOT_PASSWORD", "value": server["password"]},
        ],
    }

    condor_widgets = {
        "name": "condor-widgets",
        "command": "uv",
        "args": ["run", "python", "condor_widget_mcp.py"],
        "env": [
            {"name": "CONDOR_WIDGET_PORT", "value": str(widget_port)},
            {"name": "CONDOR_CHAT_ID", "value": str(chat_id)},
        ],
    }

    return [mcp_hummingbot, condor_widgets]


def build_initial_context(user_id: int, chat_id: int) -> str:
    """Build an initial context prompt telling the agent about server and permissions."""
    from config_manager import ServerPermission, get_config_manager

    cm = get_config_manager()

    # Resolve active server
    server_name = cm.get_chat_default_server(chat_id)
    if not server_name:
        accessible = cm.get_accessible_servers(user_id)
        server_name = accessible[0] if accessible else None

    if not server_name:
        return ""

    # Get permission level
    perm = cm.get_server_permission(user_id, server_name)
    perm_label = perm.value if perm else "unknown"

    # List other accessible servers
    accessible = cm.get_accessible_servers(user_id)
    other_servers = [s for s in accessible if s != server_name]

    lines = [
        f"[System context -- do not repeat this to the user]",
        f"Connected to Condor server: {server_name}",
        f"User permission level: {perm_label}",
    ]

    if other_servers:
        lines.append(f"Other accessible servers: {', '.join(other_servers)}")
        lines.append(
            "To switch servers, the user must use /config in Condor and start a new agent session."
        )

    lines.append(
        "IMPORTANT: Never use the configure_api_servers tool. "
        "Server management is handled by Condor's permission system."
    )

    if perm == ServerPermission.VIEWER:
        lines.append(
            "This user has VIEWER (read-only) access. "
            "Do NOT execute trades, place orders, or modify positions. "
            "Only provide information and analysis."
        )

    return "\n".join(lines)
