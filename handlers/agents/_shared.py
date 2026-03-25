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

# -- Agent modes --

AGENT_MODES: dict[str, dict[str, str]] = {
    "condor": {"label": "Condor", "description": "General trading assistant"},
    "agent_builder": {"label": "Agent Builder", "description": "Create and manage autonomous trading strategies"},
    "chat_with_agent": {"label": "Chat with Agent", "description": "Talk to a running trading agent"},
}
DEFAULT_MODE = "condor"

# -- Trading agent system prompt (moved from handlers/trading_agent/__init__.py) --

TRADING_SYSTEM_PROMPT = """\
[System context -- do not repeat this to the user]
You are now in TRADING AGENT mode. Your focus is on managing autonomous \
trading agents -- creating strategies, starting agents, monitoring \
performance, and reviewing trading decisions.

WHAT YOU CAN DO:
- Create, edit, and delete trading strategies via manage_trading_agent tool
- Start, stop, pause, resume trading agents
- Read agent journals and run snapshots (trading_agent_journal_read)
- Monitor agent status, PnL, risk state
- Review run history (decision logs per tick)

WORKFLOW FOR CREATING A NEW STRATEGY:
1. Discuss with user what they want to trade and how
2. Use manage_trading_agent(action="create_strategy", name=..., description=..., \
instructions=..., agent_key="claude-code") to create it
3. Then start an agent with manage_trading_agent(action="start_agent", strategy_id=..., config={...})

WORKFLOW FOR MONITORING:
1. Use manage_trading_agent(action="list_agents") to see running agents
2. Use manage_trading_agent(action="agent_status", agent_id=...) for detailed status
3. Use trading_agent_journal_read(agent_id=..., section="summary") for quick status
4. Use trading_agent_journal_read(agent_id=..., section="runs") to list run snapshots
5. Use trading_agent_journal_read(agent_id=..., section="run:N") to see tick N detail

DATA STRUCTURE:
Each strategy has its own folder: data/trading_agents/{slug}/
  - agent.md: strategy definition
  - trading_sessions/session_N/: per-session data
    - journal.md: learnings + summary + snapshots + executors + snapshots
    - runs/: per-tick snapshots (tick_1.md, tick_2.md, ...)

RULES:
- Be direct and concise. This is Telegram, keep messages short.
- When showing agent status, use key: value format, not tables.
- When the user asks to create a strategy, help them write good instructions \
for the trading agent (the LLM that will execute snapshots).
- Always include risk limits when starting agents.
"""

# -- Agent Chat prompts --

AGENT_CHAT_SYSTEM_PROMPT = """\
[System context -- do not repeat this to the user]
You are chatting about a specific running trading agent. You have full context \
about its strategy, journal, learnings, and recent decisions.

You can help the user:
- Understand what the agent is doing and why
- Review recent decisions and performance
- Suggest adjustments to the strategy
- Inject directives (user will prefix with !) that get included in the next tick

Keep answers concise. Use key: value format, not tables.
"""

AGENT_CHAT_DIRECTIVE_PROMPT = """\
USER DIRECTIVE (injected by the user, apply on next tick):
{directive}
"""


def build_trading_context() -> str:
    """Build the trading-focused initial context prompt."""
    from condor.trading_agent.strategy import StrategyStore
    from condor.trading_agent.engine import get_all_engines

    sections = [TRADING_SYSTEM_PROMPT]

    # List existing strategies
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

    # List running agents
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


def build_agent_chat_context(agent_id: str) -> str:
    """Build context for chatting with a specific running agent."""
    from condor.trading_agent.engine import get_engine

    engine = get_engine(agent_id)
    if not engine:
        return f"Agent {agent_id} is not currently running."

    sections = [AGENT_CHAT_SYSTEM_PROMPT]

    # Strategy info
    s = engine.strategy
    sections.append(
        f"Strategy: {s.name}\n"
        f"Description: {s.description or 'N/A'}\n"
        f"Skills: {', '.join(s.skills) if s.skills else 'none'}"
    )

    # Agent status
    info = engine.get_info()
    sections.append(
        f"Agent ID: {agent_id}\n"
        f"Status: {info['status']}\n"
        f"Ticks: {info['tick_count']}\n"
        f"Daily PnL: ${info['daily_pnl']:+.2f}\n"
        f"Open executors: {info['open_executors']}\n"
        f"Exposure: ${info.get('total_exposure', 0):,.2f}"
    )

    # Journal learnings
    learnings = engine.journal.read_learnings()
    if learnings:
        sections.append(f"Learnings:\n{learnings}")

    # Summary
    summary = engine.journal.read_summary()
    if summary:
        sections.append(f"Summary:\n{summary}")

    # Recent decisions
    recent = engine.journal.get_recent_decisions(count=5)
    if recent:
        sections.append("Recent decisions:\n" + "\n".join(f"- {d}" for d in recent))

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
    user_id: int, chat_id: int, user_data: dict | None = None
) -> list[dict[str, Any]]:
    """Build dynamic MCP server configs for an agent session.

    Resolves the user's default Condor server and returns ACP-format mcpServers
    that override the static .mcp.json entries by name.
    Always includes the condor MCP server; hummingbot is added when a valid
    server can be resolved for the user.
    """
    from config_manager import get_config_manager, get_effective_server

    cm = get_config_manager()

    # Condor MCP -- runs as stdio subprocess, tools work locally without TCP bridge
    condor = {
        "name": "condor",
        "command": "uv",
        "args": ["run", "python", "condor_mcp.py"],
        "env": [
            {"name": "CONDOR_CHAT_ID", "value": str(chat_id)},
            {"name": "CONDOR_USER_ID", "value": str(user_id)},
        ],
    }

    # Resolve which hummingbot server to use (respects user preferences)
    server_name = get_effective_server(chat_id, user_data)
    if not server_name:
        accessible = cm.get_accessible_servers(user_id)
        server_name = accessible[0] if accessible else None

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
        "args": ["run", "python", "-m", "hummingbot_mcp"],
        "env": [
            {"name": "HUMMINGBOT_API_URL", "value": api_url},
            {"name": "HUMMINGBOT_USERNAME", "value": server["username"]},
            {"name": "HUMMINGBOT_PASSWORD", "value": server["password"]},
        ],
    }

    return [mcp_hummingbot, condor]


def build_mcp_servers_for_agent(
    server_name: str, user_id: int, chat_id: int
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
        "args": ["run", "python", "condor_mcp.py"],
        "env": [
            {"name": "CONDOR_CHAT_ID", "value": str(chat_id)},
            {"name": "CONDOR_USER_ID", "value": str(user_id)},
        ],
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
        "args": ["run", "python", "-m", "hummingbot_mcp"],
        "env": [
            {"name": "HUMMINGBOT_API_URL", "value": api_url},
            {"name": "HUMMINGBOT_USERNAME", "value": server["username"]},
            {"name": "HUMMINGBOT_PASSWORD", "value": server["password"]},
        ],
    }

    return [mcp_hummingbot, condor]


def build_initial_context(user_id: int, chat_id: int, user_data: dict | None = None) -> str:
    """Build an initial context prompt telling the agent about server, permissions, and formatting rules."""
    from config_manager import ServerPermission, get_config_manager, get_effective_server

    cm = get_config_manager()

    # Always start with Telegram formatting rules
    sections: list[str] = [TELEGRAM_SYSTEM_PROMPT]

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

        # Instruct agent to preload all MCP tools in one shot to avoid
    # repeated ToolSearch calls that bloat the context window.
    mcp_tools = [
        "mcp__mcp-hummingbot__configure_server",
        "mcp__mcp-hummingbot__get_market_data",
        "mcp__mcp-hummingbot__get_portfolio_overview",
        "mcp__mcp-hummingbot__manage_executors",
        "mcp__mcp-hummingbot__manage_bots",
        "mcp__mcp-hummingbot__manage_controllers",
        "mcp__mcp-hummingbot__explore_dex_pools",
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

    sections.append("\n".join([
            f"Active server: {active_name}",
            "",
            tool_preload_hint,
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
