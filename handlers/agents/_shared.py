"""Constants and MCP config loader for agent sessions."""

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

AGENT_OPTIONS: dict[str, dict[str, str]] = {
    "claude-code": {"label": "Claude Code"},
    "gemini": {"label": "Gemini CLI"},
}

DEFAULT_AGENT = "claude-code"

# -- Compact prompt templates --

COMPACT_PROMPT_AUTO = (
    "Please provide a concise summary of our conversation so far. Include:\n"
    "- Key decisions and conclusions reached\n"
    "- Important data points and numbers discussed\n"
    "- Current task state and any pending actions\n"
    "- User preferences or instructions given\n\n"
    "Be concise but thorough. This summary will be used to carry context into a fresh session."
)

COMPACT_PROMPT_CUSTOM_TEMPLATE = (
    "Please provide a concise summary of our conversation, focusing specifically on:\n"
    "{instructions}\n\n"
    "Drop everything else. Be concise but preserve the details requested above. "
    "This summary will be used to carry context into a fresh session."
)

COMPACT_CONTEXT_TEMPLATE = (
    "[System context -- do not repeat this to the user]\n"
    "This is a continuation of a previous conversation. "
    "Here is the summary from that session:\n\n"
    "{summary}\n\n"
    "Continue from where we left off. The user compacted the context to free up space."
)

TELEGRAM_SYSTEM_PROMPT = (
    "[System context -- do not repeat this to the user]\n"
    "You are Condor, a trading assistant inside Telegram.\n\n"
    "BEHAVIOR:\n"
    "- Lead with the answer. Be direct, not verbose.\n"
    "- For trading questions, use MCP tools directly. Don't explore the filesystem.\n"
    "- Keep tool chains short: 1-3 tool calls per response, not 10.\n"
    "- Never read source code or explore the codebase unless explicitly asked.\n\n"
    "FORMATTING (Telegram mobile):\n"
    "- NEVER use Markdown tables. Use bullet lists or key: value lines.\n"
    "- Keep paragraphs short (2-3 sentences max).\n"
    "- Cap lists at 5-7 items.\n"
    "- Respond in the user's language.\n\n"
    "Read @CONDOR.md for full details on your identity, tools, permissions, and rules.\n"
)

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

    mcp_dir = str(Path.home() / "mcp")
    mcp_hummingbot = {
        "name": "mcp-hummingbot",
        "command": "uv",
        "args": [
            "--directory",
            mcp_dir,
            "run",
            "main.py",
        ],
        "env": [
            {"name": "HUMMINGBOT_API_URL", "value": api_url},
            {"name": "HUMMINGBOT_USERNAME", "value": server["username"]},
            {"name": "HUMMINGBOT_PASSWORD", "value": server["password"]},
        ],
    }

    condor = {
        "name": "condor",
        "command": "uv",
        "args": ["run", "python", "condor_mcp.py"],
        "env": [
            {"name": "CONDOR_WIDGET_PORT", "value": str(widget_port)},
            {"name": "CONDOR_CHAT_ID", "value": str(chat_id)},
            {"name": "CONDOR_USER_ID", "value": str(user_id)},
        ],
    }

    return [mcp_hummingbot, condor]


def build_initial_context(user_id: int, chat_id: int) -> str:
    """Build an initial context prompt telling the agent about server, permissions, and formatting rules."""
    from config_manager import ServerPermission, get_config_manager

    cm = get_config_manager()

    # Always start with Telegram formatting rules
    sections: list[str] = [TELEGRAM_SYSTEM_PROMPT]

    # Resolve active server
    active_name = cm.get_chat_default_server(chat_id)
    accessible = cm.get_accessible_servers(user_id)
    if not active_name:
        active_name = accessible[0] if accessible else None

    if active_name:
        # Build server list with credentials and permissions
        server_lines: list[str] = []
        for name in accessible:
            server = cm.get_server(name)
            if not server:
                continue
            perm = cm.get_server_permission(user_id, name)
            perm_label = perm.value.upper() if perm else "UNKNOWN"
            active_tag = " (active)" if name == active_name else ""
            server_lines.append(
                f"- {name}{active_tag} [{perm_label}]: "
                f"host={server['host']}, port={server['port']}, "
                f"user={server['username']}, pass={server['password']}"
            )

        # Build configure_server call for the active server
        active_server = cm.get_server(active_name)
        if active_server:
            configure_hint = (
                f'IMPORTANT: Before making ANY tool calls to mcp-hummingbot, you MUST first call '
                f'configure_server(host="{active_server["host"]}", port={active_server["port"]}, '
                f'username="{active_server["username"]}", password="{active_server["password"]}") '
                f'to ensure the MCP is connected to the correct server. '
                f'Do this silently without telling the user.'
            )
        else:
            configure_hint = ""

        sections.append("\n".join([
            f"Active server: {active_name}",
            "",
            configure_hint,
            "",
            "Available servers:",
            *server_lines,
            "",
            "To switch servers, use configure_server with the credentials above.",
            'Example: configure_server(host="localhost", port=8000, username="admin", password="admin")',
            "Only use servers listed here.",
            "",
            "Permission rules:",
            "- OWNER: Full access including trading operations and server management.",
            "- TRADER: Can trade, view balances, and manage own settings.",
            "",
            "After switching servers, enforce the permission level shown for that server.",
        ]))

    return "\n\n".join(sections)
