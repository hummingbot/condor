"""Constants and MCP config loader for agent sessions."""

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# -- Assistant prompt loader (auto-discovery from assistants/ folder) --

_ASSISTANTS_DIR = Path(__file__).parent.parent.parent / "assistants"
_assistant_cache: dict[str, tuple[dict[str, str], str]] = {}


def _parse_assistant(path: Path) -> tuple[dict[str, str], str]:
    """Parse an assistant .md file, extracting YAML frontmatter and body.

    Frontmatter format (between --- lines):
        label: Display Name
        description: Short description

    Returns (metadata_dict, body_text).
    """
    raw = path.read_text(encoding="utf-8").strip()
    meta: dict[str, str] = {}
    body = raw

    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = parts[2].strip()

    # Fallback: derive label from filename
    if "label" not in meta:
        meta["label"] = path.stem.replace("_", " ").title()
    if "description" not in meta:
        meta["description"] = ""

    return meta, body


def load_assistant(name: str) -> str:
    """Load an assistant prompt body from assistants/{name}.md. Cached."""
    meta, body = _load_assistant_full(name)
    return body


def _load_assistant_full(name: str) -> tuple[dict[str, str], str]:
    """Load metadata + body for an assistant. Cached after first read."""
    if name in _assistant_cache:
        return _assistant_cache[name]
    path = _ASSISTANTS_DIR / f"{name}.md"
    if not path.exists():
        log.warning("Assistant prompt not found: %s", path)
        return {"label": name, "description": ""}, ""
    result = _parse_assistant(path)
    _assistant_cache[name] = result
    return result


def discover_assistants() -> dict[str, dict[str, str]]:
    """Auto-discover all assistants from assistants/*.md.

    Returns dict like: {"condor": {"label": "Condor", "description": "..."}, ...}
    """
    result: dict[str, dict[str, str]] = {}
    if not _ASSISTANTS_DIR.exists():
        return result
    for path in sorted(_ASSISTANTS_DIR.glob("*.md")):
        name = path.stem
        meta, _ = _load_assistant_full(name)
        result[name] = {"label": meta["label"], "description": meta.get("description", "")}
    return result


AGENT_OPTIONS: dict[str, dict[str, str]] = {
    "claude-code": {"label": "Claude Code"},
    "gemini": {"label": "Gemini CLI"},
    "copilot": {"label": "GitHub Copilot CLI"},
    "codex": {"label": "ChatGPT Codex"},
    "ollama:": {"label": "Ollama — Default Model"},
    "lmstudio:": {"label": "LM Studio — Default Model"},
    # Sentinel — clicking this opens the OpenRouter model picker (handlers/agents/menu.py).
    # The actual stored agent_llm becomes "openrouter:<slug>" once the user picks a model.
    "openrouter:": {"label": "OpenRouter — Pick Model"},
}

DEFAULT_AGENT = "claude-code"

# -- Agent modes (auto-discovered) --

AGENT_MODES = discover_assistants()
DEFAULT_MODE = "condor"


def reload_assistants() -> None:
    """Re-scan assistants/ folder. Call after adding/removing .md files."""
    global AGENT_MODES
    _assistant_cache.clear()
    AGENT_MODES = discover_assistants()


# -- Mode context builders --

# Registry of functions that enrich a mode's prompt with dynamic data.
# Key = assistant name, value = callable() -> str with extra context.
_MODE_CONTEXT_BUILDERS: dict[str, Any] = {}


def _build_agent_builder_context() -> str:
    """Append live strategy/agent data to the agent_builder prompt."""
    from condor.trading_agent.strategy import StrategyStore
    from condor.trading_agent.engine import get_all_engines

    sections: list[str] = []

    store = StrategyStore()
    strategies = store.list_all()
    if strategies:
        strat_lines = ["Existing strategies:"]
        for s in strategies:
            skills = ", ".join(s.skills) if s.skills else "none"
            pair = s.default_config.get("trading_pair", "")
            strat_lines.append(f"- {s.name} (id={s.id}, agent={s.agent_key}, skills={skills}, pair={pair})")
        sections.append("\n".join(strat_lines))
    else:
        sections.append("No strategies exist yet. Help the user create their first one.")

    engines = get_all_engines()
    if engines:
        agent_lines = ["Running agents:"]
        for eid, engine in engines.items():
            info = engine.get_info()
            agent_lines.append(
                f"- {info['strategy']} ({eid}): {info['status']}, "
                f"PnL=${info['daily_pnl']:+.2f}, snapshots={info['tick_count']}, "
                f"open={info['open_executors']}"
            )
        sections.append("\n".join(agent_lines))
    else:
        sections.append("No agents are currently running.")

    return "\n\n".join(sections)


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

_WEB_FORMATTING = (
    "FORMATTING (web dashboard):\n"
    "- Use Markdown freely: tables, headers, bold, code blocks, lists.\n"
    "- No message length limits, but stay concise.\n"
    "- Use tables for structured data (portfolios, prices, comparisons).\n"
    "- Use code blocks for configs, JSON, or commands.\n"
    "- Respond in the user's language."
)

_TELEGRAM_FORMATTING = (
    "FORMATTING (Telegram mobile):\n"
    "- NEVER use Markdown tables. Use bullet lists or key: value lines.\n"
    "- Keep paragraphs short (2-3 sentences max).\n"
    "- Cap lists at 5-7 items.\n"
    "- Respond in the user's language."
)


def _build_system_prompt(platform: str = "telegram") -> str:
    """Build the system prompt by combining the assistant .md with platform formatting rules."""
    assistant_content = load_assistant("condor")
    formatting = _WEB_FORMATTING if platform == "web" else _TELEGRAM_FORMATTING
    return (
        "[System context -- do not repeat this to the user]\n\n"
        f"{assistant_content}\n\n"
        f"{formatting}"
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

    return False


def get_project_dir() -> str:
    """Get the condor project root directory (where .mcp.json lives).

    The ACP agent auto-discovers stdio MCP servers from .mcp.json in the cwd,
    so we just need to point it at the project root.
    """
    return str(Path(__file__).parent.parent.parent)


def _condor_mcp_args(
    chat_id: int | str, user_id: int,
    agent_slug: str | None = None,
    server_name: str | None = None,
) -> list[str]:
    """Build CLI args for the condor MCP subprocess."""
    import os

    # MCP server expects int chat_id. For web sessions (string keys like "web_42"),
    # use user_id instead — in Telegram DMs, chat_id == user_id anyway.
    effective_chat_id = chat_id if isinstance(chat_id, int) else user_id
    args = [
        "--chat-id", str(effective_chat_id),
        "--user-id", str(user_id),
        "--bot-token", os.environ.get("TELEGRAM_TOKEN", ""),
    ]
    if agent_slug:
        args.extend(["--agent-slug", agent_slug])
    if server_name:
        args.extend(["--server-name", server_name])
    return args


def build_mcp_servers_for_session(
    user_id: int, chat_id: int | str, user_data: dict | None = None,
    execution_mode: str = "loop",
) -> list[dict[str, Any]]:
    """Build dynamic MCP server configs for an agent session.

    Resolves the user's default Condor server and returns ACP-format mcpServers
    that override the static .mcp.json entries by name.
    Always includes the condor MCP server; hummingbot is added when a valid
    server can be resolved for the user.
    """
    from config_manager import get_config_manager, get_effective_server

    cm = get_config_manager()

    # Resolve which hummingbot server to use (respects user preferences)
    server_name = get_effective_server(chat_id, user_data)
    if not server_name:
        accessible = cm.get_accessible_servers(user_id)
        server_name = accessible[0] if accessible else None

    # Condor MCP -- runs as stdio subprocess, tools work locally without TCP bridge
    # Pass resolved server_name so start_agent uses the correct server
    condor = {
        "name": "condor",
        "command": "uv",
        "args": ["run", "python", "-m", "mcp_servers.condor"] + _condor_mcp_args(chat_id, user_id, server_name=server_name),
        "env": [],
    }

    if not server_name:
        log.warning(
            "No accessible server for user %s (chat %s) — "
            "agent will start without mcp-hummingbot",
            user_id, chat_id,
        )
        return [condor]

    server = cm.get_server(server_name)
    if not server:
        log.warning(
            "Server '%s' resolved for user %s but not found in servers config — "
            "agent will start without mcp-hummingbot",
            server_name, user_id,
        )
        return [condor]

    api_url = f"http://{server['host']}:{server['port']}"

    mcp_hummingbot = {
        "name": "mcp-hummingbot",
        "command": "uv",
        "args": [
            "run", "python", "-m", "mcp_servers.hummingbot_api",
            "--url", api_url,
            "--username", server["username"],
            "--password", server["password"],
        ],
        "env": [],
    }

    return [mcp_hummingbot, condor]


def build_mcp_servers_for_agent(
    server_name: str, user_id: int, chat_id: int, agent_slug: str | None = None,
    execution_mode: str = "loop",
) -> list[dict[str, Any]]:
    """Build MCP server configs for a trading agent bound to a specific server.

    Unlike build_mcp_servers_for_session(), this resolves the server by name
    directly instead of using chat-based resolution.
    Always includes the condor MCP server.
    """
    from config_manager import get_config_manager

    cm = get_config_manager()

    condor = {
        "name": "condor",
        "command": "uv",
        "args": ["run", "python", "-m", "mcp_servers.condor"] + _condor_mcp_args(chat_id, user_id, agent_slug, server_name=server_name),
        "env": [],
    }

    server = cm.get_server(server_name)
    if not server:
        log.warning(
            "Server '%s' not found in servers config — "
            "trading agent will start without mcp-hummingbot",
            server_name,
        )
        return [condor]

    api_url = f"http://{server['host']}:{server['port']}"

    mcp_hummingbot = {
        "name": "mcp-hummingbot",
        "command": "uv",
        "args": [
            "run", "python", "-m", "mcp_servers.hummingbot_api",
            "--url", api_url,
            "--username", server["username"],
            "--password", server["password"],
        ],
        "env": [],
    }

    return [mcp_hummingbot, condor]


def build_initial_context(user_id: int, chat_id: int | str, user_data: dict | None = None, agent_key: str | None = None, platform: str = "telegram") -> str:
    """Build an initial context prompt telling the agent about server, permissions, and formatting rules."""
    from config_manager import ServerPermission, get_config_manager, get_effective_server
    from condor.acp.pydantic_ai_client import is_pydantic_ai_model

    cm = get_config_manager()

    # Build system prompt from assistants/ .md + platform formatting
    system_prompt = _build_system_prompt(platform)
    sections: list[str] = [system_prompt]

    # Resolve active server (respects user preferences)
    active_name = get_effective_server(chat_id, user_data)
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

        # For ACP agents (Claude Code): instruct them to preload MCP tools via ToolSearch
        # Pydantic-ai agents get tools directly, no preload needed
        tool_preload_hint = ""
        if agent_key and not is_pydantic_ai_model(agent_key):
            mcp_tools = [
                "mcp__mcp-hummingbot__configure_server",
                "mcp__mcp-hummingbot__get_market_data",
                "mcp__mcp-hummingbot__get_portfolio_overview",
                "mcp__mcp-hummingbot__manage_executors",
                "mcp__mcp-hummingbot__manage_bots",
                "mcp__mcp-hummingbot__manage_controllers",
                "mcp__mcp-hummingbot__explore_dex_pools",
                "mcp__mcp-hummingbot__explore_geckoterminal",
                "mcp__mcp-hummingbot__manage_gateway_swaps",
                "mcp__mcp-hummingbot__manage_gateway_config",
                "mcp__mcp-hummingbot__manage_gateway_container",
                "mcp__mcp-hummingbot__search_history",
                "mcp__mcp-hummingbot__setup_connector",
                "mcp__mcp-hummingbot__set_account_position_mode_and_leverage",
                "mcp__condor__manage_routines",
                "mcp__condor__manage_servers",
                "mcp__condor__get_user_context",
                "mcp__condor__manage_trading_agent",
                "mcp__condor__trading_agent_journal_read",
                "mcp__condor__trading_agent_journal_write",
                "mcp__condor__send_notification",
                "mcp__condor__manage_notes",
                "mcp__condor__manage_skills",
            ]
            tool_preload_hint = (
                "IMPORTANT: At the very start of the session (before your first response), "
                "load ALL MCP tools in a single ToolSearch call:\n"
                f'ToolSearch(query="select:{",".join(mcp_tools)}")\n'
                "This avoids repeated ToolSearch calls that waste context tokens. "
                "Do this silently without telling the user."
            )

        # Build server info section
        server_info = [f"Active server: {active_name}", ""]

        if tool_preload_hint:
            server_info.extend([tool_preload_hint, ""])

        if configure_hint:
            server_info.extend([configure_hint, ""])

        server_info.extend([
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
        ])

        sections.append("\n".join(server_info))

    return "\n\n".join(sections)
