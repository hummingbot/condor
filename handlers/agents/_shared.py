"""Constants and MCP config loader for agent sessions."""

import logging
from pathlib import Path
from typing import Any

from condor.memory.paths import CHAT_SLUG

log = logging.getLogger(__name__)


def _chat_agent():
    """The default agent's record — Condor's ``agents/condor/AGENT.md``.

    There is one loader and one frontmatter schema (FEAT-033): the chat is read
    by ``AgentStore`` exactly like a specialist is. Returns ``None`` only if the
    file is missing or unreadable, which callers treat as "no instructions"
    rather than as a startup failure.
    """
    from condor.agents.agent import AgentStore

    return AgentStore().get(CHAT_SLUG)


AGENT_OPTIONS: dict[str, dict[str, Any]] = {
    "claude-code": {"label": "Claude Code"},
    "claude-acp:opus": {"label": "Claude (ACP) — Opus"},
    "claude-acp:sonnet": {"label": "Claude (ACP) — Sonnet"},
    "gemini": {"label": "Gemini CLI"},
    "copilot": {"label": "GitHub Copilot CLI"},
    "codex": {"label": "ChatGPT Codex"},
    "ollama:": {"label": "Ollama — Default Model"},
    "lmstudio:": {"label": "LM Studio — Default Model"},
    # Sentinels — these open a picker instead of being a selectable model, so
    # they are NOT valid agent keys. `picker` marks them for surfaces that
    # render AGENT_OPTIONS as a flat list of choices (the web dashboard's model
    # dropdown), which would otherwise offer a key that fails at session start.
    #
    # OpenRouter: stored agent_llm becomes "openrouter:<slug>".
    "openrouter:": {"label": "OpenRouter — Pick Model", "picker": True},
    # Custom endpoints: the user's saved OpenAI-compatible endpoints live in
    # preferences (condor/preferences.py, shared with the web dashboard) and
    # the stored agent_llm becomes "custom@<endpoint>:<model-id>".
    "custom:": {"label": "Custom — OpenAI-compatible API", "picker": True},
}


def selectable_agent_options() -> dict[str, dict[str, Any]]:
    """AGENT_OPTIONS minus the picker sentinels — every key here is startable."""
    return {k: v for k, v in AGENT_OPTIONS.items() if not v.get("picker")}


def _default_agent() -> str:
    """Resolve the fallback agent_key for users who haven't picked a model.

    Precedence: ``CONDOR_DEFAULT_AGENT`` env > Condor's ``AGENT.md`` frontmatter
    ``agent_key`` > ``"claude-code"``. A user's own /agent → Change LLM choice
    (``agent_llm``) still overrides this at runtime; this is only the default.
    Examples for the frontmatter / env: ``claude-code``, ``claude-acp:opus``,
    ``ollama:qwen3:32b``.
    """
    import os

    agent = _chat_agent()
    return (
        os.environ.get("CONDOR_DEFAULT_AGENT")
        or (agent.agent_key if agent else "")
        or "claude-code"
    )


DEFAULT_AGENT = _default_agent()


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
    """Condor's own AGENT.md plus the platform's formatting rules."""
    agent = _chat_agent()
    return (
        "[System context -- do not repeat this to the user]\n\n"
        f"{agent.instructions if agent else ''}\n\n"
        f"{_WEB_FORMATTING if platform == 'web' else _TELEGRAM_FORMATTING}"
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


def get_project_dir() -> str:
    """Get the condor project root directory (where .mcp.json lives).

    The ACP agent auto-discovers stdio MCP servers from .mcp.json in the cwd,
    so we just need to point it at the project root.
    """
    return str(Path(__file__).parent.parent.parent)


def _condor_mcp_args(
    chat_id: int | str,
    user_id: int,
    agent_slug: str | None = None,
    server_name: str | None = None,
    delegate_worker: bool = False,
) -> list[str]:
    """Build CLI args for the condor MCP subprocess.

    ``delegate_worker`` marks a *background Condor worker* — the detached session
    ``delegate`` starts (FEAT-032). The chat and that worker share one agent
    record, so the flag is what tells the subprocess which seat it is sitting in.
    """
    import os

    # MCP server expects int chat_id. For web sessions (string keys like "web_42"),
    # use user_id instead — in Telegram DMs, chat_id == user_id anyway.
    effective_chat_id = chat_id if isinstance(chat_id, int) else user_id
    args = [
        "--chat-id",
        str(effective_chat_id),
        "--user-id",
        str(user_id),
        "--bot-token",
        os.environ.get("TELEGRAM_TOKEN", ""),
    ]
    if agent_slug:
        args.extend(["--agent-slug", agent_slug])
    if server_name:
        args.extend(["--server-name", server_name])
    if delegate_worker:
        args.append("--delegate-worker")
    return args


def build_mcp_servers_for_session(
    user_id: int,
    chat_id: int | str,
    user_data: dict | None = None,
    server_name: str | None = None,
    agent_slug: str | None = None,
    delegate_worker: bool = False,
) -> list[dict[str, Any]]:
    """Build dynamic MCP server configs for an agent session.

    Returns ACP-format mcpServers that override the static .mcp.json entries by
    name. Always includes the condor MCP server; hummingbot is added when a
    valid server can be resolved for the user.

    ``server_name`` pins the run to that Condor server (an Agent with
    ``server_required``); when omitted, the chat's ambient server is resolved
    from the user's preferences, then from the first accessible server.

    ``agent_slug`` scopes the condor MCP tools' memory/skills to that Agent's
    own stores (``agents/{slug}/``). Without it the tools target the chat
    condor's stores — correct for chat sessions, wrong for an Agent run: a
    serverless consult/tick would silently read and write the CHAT's memory
    and skills instead of the Agent's own (e.g. an agent unable to find its
    inherited ``routine_cookbook`` skill).

    ``delegate_worker`` marks a background Condor delegation (FEAT-032) so the
    subprocess picks up the worker framing and refuses to delegate again.
    """
    from config_manager import get_config_manager, get_effective_server

    cm = get_config_manager()

    # Resolve which hummingbot server to use (explicit override > user preferences)
    if not server_name:
        server_name = get_effective_server(chat_id, user_data)
    if not server_name:
        accessible = cm.get_accessible_servers(user_id)
        server_name = accessible[0] if accessible else None

    # Condor MCP -- runs as stdio subprocess, tools work locally without TCP bridge
    # Pass resolved server_name so start_agent uses the correct server
    condor = {
        "name": "condor",
        "command": "uv",
        "args": ["run", "python", "-m", "mcp_servers.condor"]
        + _condor_mcp_args(
            chat_id,
            user_id,
            agent_slug,
            server_name=server_name,
            delegate_worker=delegate_worker,
        ),
        "env": [],
    }

    if not server_name:
        log.warning(
            "No accessible server for user %s (chat %s) — "
            "agent will start without mcp-hummingbot",
            user_id,
            chat_id,
        )
        return [condor]

    server = cm.get_server(server_name)
    if not server:
        log.warning(
            "Server '%s' resolved for user %s but not found in servers config — "
            "agent will start without mcp-hummingbot",
            server_name,
            user_id,
        )
        return [condor]

    api_url = f"http://{server['host']}:{server['port']}"

    mcp_hummingbot = {
        "name": "mcp-hummingbot",
        "command": "uv",
        "args": [
            "run",
            "python",
            "-m",
            "mcp_servers.hummingbot_api",
            "--url",
            api_url,
            "--username",
            server["username"],
            "--password",
            server["password"],
            "--server-name",
            server_name,
        ],
        "env": [],
    }

    return [mcp_hummingbot, condor]


def build_initial_context(
    user_id: int,
    chat_id: int | str,
    user_data: dict | None = None,
    agent_key: str | None = None,
    platform: str = "telegram",
    server_name: str | None = None,
) -> str:
    """Build an initial context prompt telling the agent about server, permissions, and formatting rules."""
    from condor.acp.pydantic_ai_client import is_pydantic_ai_model
    from config_manager import (
        ServerPermission,
        get_config_manager,
        get_effective_server,
    )

    cm = get_config_manager()

    # System prompt: Condor's own AGENT.md + platform formatting
    system_prompt = _build_system_prompt(platform)
    sections: list[str] = [system_prompt]

    # Resolve active server (explicit override > user preferences)
    active_name = server_name or get_effective_server(chat_id, user_data)
    accessible = cm.get_accessible_servers(user_id)
    if not active_name:
        active_name = accessible[0] if accessible else None

    if active_name:
        # Build server list with permissions (no credentials needed — MCP is pre-configured)
        server_lines: list[str] = []
        for name in accessible:
            server = cm.get_server(name)
            if not server:
                continue
            perm = cm.get_server_permission(user_id, name)
            perm_label = perm.value.upper() if perm else "UNKNOWN"
            active_tag = " (active)" if name == active_name else ""
            server_lines.append(f"- {name}{active_tag} [{perm_label}]")

        # Get active server permission for enforcement
        active_perm = cm.get_server_permission(user_id, active_name)
        active_perm_label = active_perm.value.upper() if active_perm else "UNKNOWN"

        # For ACP agents (Claude Code): instruct them to preload MCP tools via ToolSearch
        # Pydantic-ai agents get tools directly, no preload needed
        tool_preload_hint = ""
        if agent_key and not is_pydantic_ai_model(agent_key):
            mcp_tools = [
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
                "mcp__mcp-hummingbot__set_account_position_mode_and_leverage",
                "mcp__condor__manage_routines",
                "mcp__condor__manage_servers",
                "mcp__condor__get_user_context",
                "mcp__condor__manage_trading_agent",
                "mcp__condor__trading_agent_journal_read",
                "mcp__condor__trading_agent_journal_write",
                "mcp__condor__send_notification",
                "mcp__condor__manage_memory",
                "mcp__condor__manage_skill",
            ]
            tool_preload_hint = (
                "IMPORTANT: At the very start of the session (before your first response), "
                "load ALL MCP tools in a single ToolSearch call:\n"
                f'ToolSearch(query="select:{",".join(mcp_tools)}")\n'
                "This avoids repeated ToolSearch calls that waste context tokens. "
                "Do this silently without telling the user."
            )

        # Build server info section
        server_info = [
            f"Active server: {active_name} (permission: {active_perm_label})",
            "The MCP server is already connected to this server. Do NOT call configure_server — it is pre-configured.",
            "",
        ]

        if tool_preload_hint:
            server_info.extend([tool_preload_hint, ""])

        server_info.extend(
            [
                "Available servers:",
                *server_lines,
                "",
                "Server switching is not supported mid-session. If the user wants a different server, "
                "tell them to start a new session with that server selected.",
                "",
                "Permission rules:",
                f"- Your current permission on '{active_name}' is {active_perm_label}.",
                "- OWNER: Full access including trading operations and server management.",
                "- TRADER: Can trade, view balances, and manage own settings.",
                "- Enforce the permission level for this server.",
            ]
        )

        sections.append("\n".join(server_info))

    # User memory index — what the chat assistant remembers about this user. This
    # store is the chat's own (FEAT-003), not shared with the user's trading
    # agents. Inject only the index; bodies are read on demand via
    # manage_memory(action="read"). Nothing injected for new users.
    try:
        from condor.memory import MemoryStore

        memory_index = MemoryStore(user_id).list_index()
        if memory_index:
            sections.append(
                "[USER MEMORY — what you remember about this user]\n"
                "Consider this before responding. Read a full memory with "
                'manage_memory(action="read", name="..."). When you learn something '
                'new and stable about the user, save it with manage_memory(action="write", ...).\n\n'
                f"{memory_index}"
            )
    except Exception:
        pass  # Memory is advisory — never block session start on it.

    # Skills index — read-only playbooks the chat assistant ships (know-how +
    # steps). Skills are general to the assistant, not learned per user; inject
    # only the index, bodies are read on demand via manage_skill(action="read").
    # Nothing injected when the assistant ships none.
    try:
        from condor.memory import SkillStore

        skills_index = SkillStore().list_index()
        if skills_index:
            sections.append(
                "[SKILLS — check here BEFORE handling a known flow with raw tools]\n"
                "Read-only playbooks shipped with the assistant. Before using raw "
                "tools for a flow below, read its playbook with "
                'manage_skill(action="read", name="...") and follow the steps — '
                "don't re-derive or hand-roll what a playbook already covers.\n\n"
                f"{skills_index}"
            )
    except Exception:
        pass  # Skills are advisory — never block session start on them.

    # Agents index — domain Agents condor can consult (FEAT: coordinator model).
    # condor delegates domain work via consult(...) instead of holding the domain's
    # tools/context itself. EVERY agent is listed: one missing from this index can
    # never be routed to. Nothing injected only when none exist.
    try:
        from condor.agents.agent import AgentStore

        # The coordinator is not among its own consultees (FEAT-033).
        agents_index = AgentStore().list_index(exclude={CHAT_SLUG})
        if agents_index:
            sections.append(
                "[AGENTS — consult these BEFORE doing domain work with raw tools]\n"
                "You are a coordinator. If a request falls in an agent's domain "
                "below, delegate it with "
                'consult(agent="<slug>", task="...", context="...") instead of '
                "driving the domain's raw tools yourself — the agent has the focused "
                "tools and domain memory. Relay a concise summary of its answer.\n\n"
                f"{agents_index}"
            )
    except Exception:
        pass  # The agents index is advisory — never block session start on it.

    return "\n\n".join(sections)


def build_agent_context(
    agent: "Agent",  # noqa: F821 — condor.agents.agent.Agent
    user_id: int,
    task: str,
    context: str = "",
) -> str:
    """Assemble the prompt for an Agent consult.

    Mirrors :func:`build_initial_context` but for a worker run: the Agent's own
    instructions become the system prompt, its domain-scoped memory/skills indexes
    are injected (keyed by the Agent slug, FEAT-003 — the shared brain), and the
    consult task is appended last. Read on demand via manage_memory/manage_skill
    inside the run.

    Those indexes come from :func:`~condor.memory.domain_context`, the same
    builder ``binding.agent_identity_context`` composes, so this Agent carries
    identical instructions about its own memory whether it is consulted or
    chatted with (ARCH-099).
    """
    from condor.memory import domain_context

    sections: list[str] = [agent.instructions]
    sections.extend(domain_context(agent.slug, user_id))

    consult = f"[CONSULT REQUEST]\n{task}"
    if context:
        consult += f"\n\n[CONTEXT FROM CONDOR]\n{context}"
    sections.append(consult)

    return "\n\n".join(sections)
