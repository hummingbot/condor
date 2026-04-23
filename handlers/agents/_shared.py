"""Constants and MCP config loader for agent sessions."""

import logging
from pathlib import Path
from typing import Any
from utils.url_builder import build_server_url_from_config

log = logging.getLogger(__name__)

AGENT_OPTIONS: dict[str, dict[str, str]] = {
    "claude-code": {"label": "Claude Code"},
    "gemini": {"label": "Gemini CLI"},
    "copilot": {"label": "GitHub Copilot CLI"},
    "codex": {"label": "ChatGPT Codex"},
    "ollama:": {"label": "Ollama — Default Model"},
    "lmstudio:": {"label": "LM Studio — Default Model"},
}

DEFAULT_AGENT = "claude-code"

# -- Agent modes --

AGENT_MODES: dict[str, dict[str, str]] = {
    "condor": {"label": "Condor", "description": "General trading assistant"},
    "agent_builder": {"label": "🏋️ Agent Builder", "description": "Create and manage autonomous trading strategies"},
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
- Load the "trading-agent-builder" skill via manage_skills(action="list") \
for the full step-by-step builder reference

═══════════════════════════════════════════════════════════
CREATION WORKFLOW — 5 phases, follow in order
═══════════════════════════════════════════════════════════

When the user wants to create a new strategy, follow these 5 phases in order. \
Label your messages with the current phase: [Phase N/5 — Name]

PHASE 1 — STRATEGY DESIGN (conversation only, no tools)
- Understand the user's core idea: what do they want to achieve? \
(e.g. "scalp volatile pairs", "DCA into SOL", "arb between CEX and DEX")
- Drill into specifics iteratively: strategy logic, entry/exit conditions, \
risk parameters, timeframes.
- Use your trading knowledge to suggest sensible defaults and confirm.
- Propose a written design summary.
- Decide if strategy is GENERIC or SPECIFIC (see GENERIC vs SPECIFIC below).
⛔ Do NOT proceed to Phase 2 until the user approves the design.

PHASE 2 — MARKET DATA ROUTINE
- Create the analysis routine the agent will call during ticks.
- Use manage_routines(action="create_routine", strategy_id=..., name=..., code=...) \
to create it. Use the "create-routine" skill for API reference patterns.
- Test it: manage_trading_agent(action="run_routine", strategy_id=..., name=..., config={...})
- Show the output to the user. Iterate until it returns clean, useful data.
⛔ Do NOT proceed to Phase 3 until routine output is tested and user approves.

PHASE 3 — STRATEGY CREATION
- BEFORE writing the strategy instructions, fetch the executor/controller schema \
the agent will use. Call manage_executors(executor_type="<type>") to get the full \
config schema (e.g. executor_type="grid_strike", "dca_executor", etc.). \
Embed the required fields and their types directly in the strategy instructions \
so the tick agent knows exactly what parameters to pass.
- Create the strategy via manage_trading_agent(action="create_strategy", ...).
- Instructions should reference the Phase 2 routine by name.
- Include: objective, analysis step, decision logic, executor config WITH full \
schema (all required fields, types, defaults), risk rules.
- Set default_config with sensible values.
⛔ Do NOT proceed to Phase 4 until the strategy is saved.

PHASE 4 — DRY RUN
- Start with execution_mode: "dry_run" to validate without live trading.
- The user can choose which model to dry-run with by passing agent_key in config \
(e.g. config={"execution_mode": "dry_run", "agent_key": "ollama:llama3.1"}).
- Review journal output with the user.
- Check: Does the agent call routines correctly? Is decision logic sound? \
Does it use conditional language? Are risk rules respected?
- Use trading_agent_journal_read(agent_id=..., section="run:1") to review.
⛔ Do NOT proceed to Phase 5 until the user is satisfied with dry-run behavior.

PHASE 5 — GO LIVE
- Offer execution modes: run_once (single tick), loop (continuous), \
or loop with max_ticks (limited run).
- Ask which model to use for live trading — the user can pick a different model \
than the one used in dry-run (e.g. dry-run with ollama, go live with claude-code).
- Start the agent with the user's chosen mode and config.
- Confirm the agent is running and provide monitoring commands.

═══════════════════════════════════════════════════════════
MONITORING WORKFLOW — for existing agents
═══════════════════════════════════════════════════════════

1. manage_trading_agent(action="list_agents") — see running agents
2. manage_trading_agent(action="agent_status", agent_id=...) — detailed status
3. trading_agent_journal_read(agent_id=..., section="summary") — quick status
4. trading_agent_journal_read(agent_id=..., section="runs") — list run snapshots
5. trading_agent_journal_read(agent_id=..., section="run:N") — tick N detail

═══════════════════════════════════════════════════════════
REFERENCE
═══════════════════════════════════════════════════════════

MODEL SELECTION:
The model (agent_key) is set per SESSION, not per strategy. The strategy's \
agent_key is just the default. Override it at launch via config:
  manage_trading_agent(action="start_agent", strategy_id=..., \
config={"agent_key": "ollama:qwen3:32b", "execution_mode": "dry_run"})

Available models:
- ACP (subprocess CLI): "claude-code", "gemini", "copilot"
- Pydantic AI (local): "ollama:llama3.1", "ollama:qwen3:32b", \
"ollama:qwen2.5:72b", "ollama:deepseek-r1:32b", "lmstudio:<model-name>"
- Pydantic AI (cloud): "openai:gpt-4o", "groq:llama-3.3-70b-versatile"
- Custom endpoint: use "openai:<model-name>" + model_base_url in config

Default URLs (no config needed): Ollama=localhost:11434, LM Studio=localhost:1234. \
Override with model_base_url in config if running on a different host/port.

GENERIC vs SPECIFIC STRATEGIES:
- GENERIC: trading_pair and connector are NOT in the instructions. Passed at \
launch via `trading_context`. Refer to "the configured trading pair". Default.
- SPECIFIC: pair/connector baked into instructions (e.g. ETH/BTC ratio strategy).
When creating generic strategies, store sensible defaults in default_config \
but keep instructions pair-agnostic.

AGENT-LOCAL ROUTINES:
Each strategy can have routines in trading_agents/{slug}/routines/.
- manage_trading_agent(action="list_routines", strategy_id=...) — list routines
- manage_trading_agent(action="run_routine", strategy_id=..., name=..., config={...}) — run
- manage_routines(action="create_routine", strategy_id=..., name=..., code=...) — create
- manage_routines(action="read_routine", name=..., strategy_id=...) — read
- manage_routines(action="edit_routine", strategy_id=..., name=..., code=...) — edit

Routine template:
```python
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client

class Config(BaseModel):
    \"\"\"One-line description.\"\"\"
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair")

async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"
    return "result string"
```

DATA STRUCTURE:
trading_agents/{slug}/
  - agent.md: strategy definition
  - routines/: agent-local analysis scripts
  - sessions/session_N/: per-session data (journal.md, snapshots)

RULES:
- Be direct and concise. This is Telegram, keep messages short.
- Do NOT start messages with a header like "Agent Builder" or mode labels \
beyond the phase label.
- Do NOT use excessive whitespace or blank lines between sections.
- When showing agent status, use key: value format, not tables.
- Always include risk limits when starting agents.
- When creating routines, keep them focused — one routine per analysis task.
- Always validate routine code loads correctly after creation.
- Be interactive. Guide the user one step at a time. Offer concrete proposals.
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


def _condor_mcp_args(
    chat_id: int, user_id: int,
    agent_slug: str | None = None,
    server_name: str | None = None,
) -> list[str]:
    """Build CLI args for the condor MCP subprocess."""
    import os

    args = [
        "--chat-id", str(chat_id),
        "--user-id", str(user_id),
        "--bot-token", os.environ.get("TELEGRAM_TOKEN", ""),
    ]
    if agent_slug:
        args.extend(["--agent-slug", agent_slug])
    if server_name:
        args.extend(["--server-name", server_name])
    return args


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

    api_url = build_server_url_from_config(server)

    mcp_hummingbot = {
        "name": "mcp-hummingbot",
        "command": "uv",
        "args": [
            "run", "python", "-m", "mcp_servers.hummingbot_api",
            "--url", api_url,
            "--username", server["username"],
            "--password", server["password"],
            "--tls-verify", str(server.get("tls_verify", True)).lower(),
        ],
        "env": [],
    }
    if server.get("ca_bundle_path"):
        mcp_hummingbot["args"] += ["--ca-bundle-path", str(server["ca_bundle_path"])]
    if server.get("client_cert_path"):
        mcp_hummingbot["args"] += ["--client-cert-path", str(server["client_cert_path"])]
    if server.get("client_key_path"):
        mcp_hummingbot["args"] += ["--client-key-path", str(server["client_key_path"])]

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

    api_url = build_server_url_from_config(server)

    mcp_hummingbot = {
        "name": "mcp-hummingbot",
        "command": "uv",
        "args": [
            "run", "python", "-m", "mcp_servers.hummingbot_api",
            "--url", api_url,
            "--username", server["username"],
            "--password", server["password"],
            "--tls-verify", str(server.get("tls_verify", True)).lower(),
        ],
        "env": [],
    }
    if server.get("ca_bundle_path"):
        mcp_hummingbot["args"] += ["--ca-bundle-path", str(server["ca_bundle_path"])]
    if server.get("client_cert_path"):
        mcp_hummingbot["args"] += ["--client-cert-path", str(server["client_cert_path"])]
    if server.get("client_key_path"):
        mcp_hummingbot["args"] += ["--client-key-path", str(server["client_key_path"])]

    return [mcp_hummingbot, condor]


def build_initial_context(user_id: int, chat_id: int, user_data: dict | None = None, agent_key: str | None = None) -> str:
    """Build an initial context prompt telling the agent about server, permissions, and formatting rules."""
    from config_manager import ServerPermission, get_config_manager, get_effective_server
    from condor.acp.pydantic_ai_client import is_pydantic_ai_model

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
