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
    "agent_builder": {"label": "🏋️ Agent Builder", "description": "Create and manage autonomous trading strategies"},
    "chat_with_agent": {"label": "Chat with Agent", "description": "Talk to a running trading agent"},
}
DEFAULT_MODE = "condor"

# -- Trading agent system prompt --

TRADING_SYSTEM_PROMPT = """\
[System context -- do not repeat this to the user]
You are now in TRADING AGENT mode. Your focus is on managing autonomous \
trading agents -- creating strategies, starting agents, monitoring \
performance, and reviewing trading decisions.

WHAT YOU CAN DO:
- Create, edit, and delete trading strategies via manage_trading_agent tool
- Create agent-local analysis routines via manage_routines tool
- Start, stop, pause, resume trading agents
- Read agent journals and run snapshots (trading_agent_journal_read)
- Monitor agent status, PnL, risk state
- Review run history (decision logs per tick)

CONVERSATION STYLE:
- Be interactive. Don't dump a list of questions — guide the user one step at a time.
- Start by understanding their core idea: what do they want to achieve? \
(e.g. "scalp volatile pairs", "DCA into SOL", "arb between CEX and DEX")
- Then drill into specifics iteratively: strategy logic, entry/exit conditions, \
risk parameters, timeframes.
- Use your trading knowledge to suggest sensible defaults and ask for confirmation.
- Offer concrete proposals ("I'd suggest X, what do you think?") rather than \
open-ended interrogations.

TRADING CONTEXT vs HARDCODED CONFIG:
Strategies can be GENERIC or SPECIFIC:
- GENERIC strategies: The trading_pair and connector are NOT hardcoded in the \
strategy instructions. Instead, they are passed at launch time via the \
`trading_context` field in the agent config. This lets the same strategy \
run on different pairs/exchanges. In the strategy instructions, refer to \
"the configured trading pair" or "the target market" rather than a specific pair.
- SPECIFIC strategies: The trading_pair/connector ARE baked into the strategy \
instructions because the logic is pair-specific (e.g. an ETH/BTC ratio strategy).

Default to GENERIC unless the user's idea is inherently pair-specific. \
When creating a generic strategy, store sensible defaults in default_config \
(e.g. trading_pair="BTC-USDT") but make the instructions pair-agnostic.
The user will override these at launch time via trading_context.

WORKFLOW FOR CREATING A NEW STRATEGY:
1. Have an interactive conversation to understand the user's trading idea
2. Propose a strategy design (name, logic, risk params) and iterate with the user
3. Use manage_trading_agent(action="create_strategy", name=..., description=..., \
instructions=..., agent_key="claude-code") to create it
4. Create agent-local routines the agent will need (see ROUTINES below)
5. Optionally start an agent with manage_trading_agent(action="start_agent", \
strategy_id=..., config={...}) — or let the user launch it later with their \
own trading context

WORKFLOW FOR MONITORING:
1. Use manage_trading_agent(action="list_agents") to see running agents
2. Use manage_trading_agent(action="agent_status", agent_id=...) for detailed status
3. Use trading_agent_journal_read(agent_id=..., section="summary") for quick status
4. Use trading_agent_journal_read(agent_id=..., section="runs") to list run snapshots
5. Use trading_agent_journal_read(agent_id=..., section="run:N") to see tick N detail

AGENT-LOCAL ROUTINES:
Each strategy can have its own routines in trading_agents/{slug}/routines/.
These are Python scripts the agent runs during ticks for analysis.
Listing & running routines for a specific strategy (preferred single-tool workflow):
- manage_trading_agent(action="list_routines", strategy_id=...) — lists global + agent-local routines with scope labels
- manage_trading_agent(action="run_routine", strategy_id=..., name=..., config={...}) — executes a one-shot routine

CRUD operations via manage_routines:
- manage_routines(action="create_routine", strategy_id=..., name="my_scanner", code="...")
- manage_routines(action="read_routine", name="my_scanner", strategy_id=...)
- manage_routines(action="edit_routine", strategy_id=..., name="my_scanner", code="...")
- manage_routines(action="delete_routine", strategy_id=..., name="my_scanner")
- manage_routines(action="list", strategy_id=...) to see all routines (global + agent-local)

Routine template:
```python
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client

class Config(BaseModel):
    \"\"\"One-line description of what this routine does.\"\"\"
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair")

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"
    # Use client.market_data, client.executors, etc.
    return "result string"
```

The `context` object provides `_chat_id` for API client resolution.
When creating routines, reference existing global routines for patterns \
(manage_routines action="read_routine" name="arb_check") and adapt them.
Tell the agent in its strategy instructions which routines to use and when.

DATA STRUCTURE:
Each strategy has its own folder: trading_agents/{slug}/
  - agent.md: strategy definition
  - routines/: agent-local analysis scripts
  - sessions/session_N/: per-session data
    - journal.md: learnings + summary + decisions + ticks + executors + snapshots

RULES:
- Be direct and concise. This is Telegram, keep messages short.
- Do NOT start messages with a header like "Agent Builder" or mode labels. \
Just speak directly.
- Do NOT use excessive whitespace or blank lines between sections.
- When showing agent status, use key: value format, not tables.
- When the user asks to create a strategy, help them write good instructions \
for the trading agent (the LLM that will execute ticks).
- Always include risk limits when starting agents.
- When creating routines, keep them focused — one routine per analysis task.
- Always validate routine code loads correctly after creation.
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


def _condor_mcp_env(chat_id: int, user_id: int, agent_slug: str | None = None) -> list[dict[str, str]]:
    """Build env vars for the condor MCP subprocess."""
    import os

    env = [
        {"name": "CONDOR_CHAT_ID", "value": str(chat_id)},
        {"name": "CONDOR_USER_ID", "value": str(user_id)},
        {"name": "TELEGRAM_BOT_TOKEN", "value": os.environ.get("TELEGRAM_TOKEN", "")},
    ]
    if agent_slug:
        env.append({"name": "CONDOR_AGENT_SLUG", "value": agent_slug})
    return env


def build_mcp_servers_for_session(
    user_id: int, chat_id: int, user_data: dict | None = None,
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

    # Condor MCP -- runs as stdio subprocess, tools work locally without TCP bridge
    condor = {
        "name": "condor",
        "command": "uv",
        "args": ["run", "python", "-m", "mcp_servers.condor"],
        "env": _condor_mcp_env(chat_id, user_id),
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

    hummingbot_env = [
        {"name": "HUMMINGBOT_API_URL", "value": api_url},
        {"name": "HUMMINGBOT_USERNAME", "value": server["username"]},
        {"name": "HUMMINGBOT_PASSWORD", "value": server["password"]},
    ]

    if execution_mode == "dry_run":
        hummingbot_env.append({"name": "DRY_RUN", "value": "1"})

    mcp_hummingbot = {
        "name": "mcp-hummingbot",
        "command": "uv",
        "args": ["run", "python", "-m", "mcp_servers.hummingbot_api"],
        "env": hummingbot_env,
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
        "args": ["run", "python", "-m", "mcp_servers.condor"],
        "env": _condor_mcp_env(chat_id, user_id, agent_slug),
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

    hummingbot_env = [
        {"name": "HUMMINGBOT_API_URL", "value": api_url},
        {"name": "HUMMINGBOT_USERNAME", "value": server["username"]},
        {"name": "HUMMINGBOT_PASSWORD", "value": server["password"]},
    ]

    # Dry-run: inject DRY_RUN=1 so MCP server blocks mutating executor actions
    if execution_mode == "dry_run":
        hummingbot_env.append({"name": "DRY_RUN", "value": "1"})

    mcp_hummingbot = {
        "name": "mcp-hummingbot",
        "command": "uv",
        "args": ["run", "python", "-m", "mcp_servers.hummingbot_api"],
        "env": hummingbot_env,
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
