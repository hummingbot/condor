"""Condor MCP Server -- exposes Condor capabilities to AI agents.

Thin wrapper layer: tool registration + docstrings only.
All business logic lives in mcp_servers.condor.tools.*
"""

from mcp.server.fastmcp import FastMCP

from mcp_servers.condor.middleware import handle_errors
from mcp_servers.condor.tools import context, notes, notification, routines, servers, trading_agent

mcp = FastMCP("condor")


@mcp.tool()
@handle_errors("send notification")
async def send_notification(text: str) -> dict:
    """Send a Telegram message to the user.

    Messages are sent as plain text (no Telegram markup) so they read cleanly on mobile.
    Do not use MarkdownV2 backslash escapes; they appear as stray backslashes.

    Args:
        text: Message body. Long text is softly truncated.

    Returns:
        {"sent": true} on success, {"error": "..."} on failure.
    """
    return await notification.send_notification(text)


@mcp.tool()
@handle_errors("manage routines")
async def manage_routines(
    action: str,
    name: str | None = None,
    config: dict | None = None,
    strategy_id: str | None = None,
    code: str | None = None,
) -> dict:
    """Manage and run Condor routines (auto-discoverable Python scripts).

    Actions -- Discovery & Execution:
    - "list": List all available routines with name, description, type, and scope
    - "describe": Show config schema for a routine (requires name)
    - "run": Execute a one-shot routine and return its result (requires name, optional config)
    - "start": Start a continuous routine as a background task (requires name, optional config)
    - "stop": Stop a running routine instance (requires name=instance_id)
    - "list_instances": List all running/scheduled routine instances

    Actions -- Agent-Local Routine CRUD (requires strategy_id or CONDOR_AGENT_SLUG):
    - "create_routine": Create a new agent-local routine (requires name, code)
    - "read_routine": Read source code of a routine (requires name)
    - "edit_routine": Update an agent-local routine (requires name, code)
    - "delete_routine": Delete an agent-local routine (requires name)

    Agent-local routines live in trading_agents/{slug}/routines/ and are only
    visible to that strategy's agent. They follow the same pattern as global
    routines: a Config(BaseModel) class and an async run(config, context) function.

    Args:
        action: The action to perform.
        name: Routine name (required for all except list/list_instances). For "stop", pass the instance_id as name.
        config: Config overrides for run/start (optional, merged with defaults).
        strategy_id: Strategy ID for agent-local routine CRUD operations.
        code: Python source code for create_routine / edit_routine.

    Returns:
        Action-specific result dict.
    """
    return await routines.manage_routines(action, name, config, strategy_id, code)


@mcp.tool()
@handle_errors("manage servers")
async def manage_servers(
    action: str,
    name: str | None = None,
) -> dict:
    """Manage Hummingbot API servers (list, check status).

    Actions:
    - "list": List all accessible servers with permissions and active status
    - "status": Check if a server is online (optional name, defaults to active server)

    Args:
        action: The action to perform (list, status)
        name: Server name (optional for status)

    Returns:
        Action-specific result dict.
    """
    return await servers.manage_servers(action, name)


@mcp.tool()
@handle_errors("get user context")
async def get_user_context() -> dict:
    """Get the current user's context within Condor.

    Returns:
        A dict with:
        - active_server: Currently active Hummingbot server name
        - user_role: User's role (admin, user, pending, blocked)
        - is_admin: Whether the user is an admin
    """
    return await context.get_user_context()


@mcp.tool()
@handle_errors("manage trading agent")
async def manage_trading_agent(
    action: str,
    agent_id: str | None = None,
    strategy_id: str | None = None,
    name: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    agent_key: str | None = None,
    skills: list[str] | None = None,
    config: dict | None = None,
) -> dict:
    """Manage trading agents and strategies.

    Actions -- Strategies:
    - "list_strategies": List all strategies for the current user
    - "get_strategy": Get full strategy details including instructions (requires strategy_id)
    - "create_strategy": Create a new strategy (requires name, description, instructions)
    - "update_strategy": Update an existing strategy (requires strategy_id, plus fields to update)
    - "delete_strategy": Delete a strategy (requires strategy_id)

    Actions -- Lifecycle:
    - "list_agents": List all running agent instances with status
    - "start_agent": Start a new agent session (requires strategy_id, optional config overrides)
    - "stop_agent": Stop a running agent (requires agent_id)
    - "pause_agent": Pause a running agent (requires agent_id)
    - "resume_agent": Resume a paused agent (requires agent_id)

    Actions -- Routines (scoped to a strategy):
    - "list_routines": List global + agent-local routines for a strategy (requires strategy_id)
    - "run_routine": Execute a one-shot routine (requires strategy_id, name, optional config)

    Actions -- Journal:
    - "journal_read": Read journal entries (requires agent_id, optional section/max_entries)
    - "journal_write": Write a journal entry (requires agent_id, entry_type, text)

    Actions -- Monitoring:
    - "agent_tracker": Get the full tracker markdown (tick history, executor ledger, snapshots) (requires agent_id)
    - "agent_journal": Get recent journal entries and learnings (requires agent_id)

    Args:
        action: The action to perform.
        agent_id: Agent instance ID (for lifecycle/monitoring/journal actions).
        strategy_id: Strategy ID (for strategy/routine/start actions).
        name: Strategy name (for create/update) or routine name (for run_routine).
        description: Strategy description (for create/update).
        instructions: Strategy instructions text (for create/update).
        agent_key: Default LLM for the strategy (for create/update). Examples: "claude-code", "gemini", "copilot", "ollama:llama3.1", "ollama:qwen3:32b", "groq:llama-3.3-70b-versatile". Default "claude-code".
        skills: List of optional skill names to enable (for create/update).
        config: Agent config overrides (for create/update/start) or routine config (for run_routine).
            For start_agent, supports: agent_key (override strategy default), model_base_url (for LM Studio/vLLM),
            execution_mode, frequency_sec, total_amount_quote, trading_context, risk_limits, server_name, max_ticks,
            digest_interval_ticks (0 = no periodic digest).

    Returns:
        Action-specific result dict.
    """
    return await trading_agent.manage_trading_agent(
        action, agent_id, strategy_id, name, description,
        instructions, agent_key, skills, config,
    )


@mcp.tool()
@handle_errors("manage notes")
async def manage_notes(
    action: str,
    key: str | None = None,
    value: str | None = None,
) -> dict:
    """Manage persistent key-value notes for Condor's memory.

    Use this to remember facts across sessions: client chat IDs, server aliases,
    trading preferences, or any context the user asks you to remember.

    Actions:
    - "list": List all saved notes
    - "get": Get a specific note (requires key)
    - "set": Save a note (requires key and value)
    - "delete": Delete a note (requires key)

    Naming convention for keys:
    - Use dot-separated namespaces: "server.brigado_2.group_chat_id"
    - Common prefixes: "server.", "client.", "routine.", "trading."

    Args:
        action: The action to perform (list, get, set, delete)
        key: The note key (required for get, set, delete)
        value: The note value (required for set)

    Returns:
        Action-specific result dict.
    """
    return await notes.manage_notes(action, key, value)


@mcp.tool()
@handle_errors("manage skills")
async def manage_skills(
    action: str | None = None,
    name: str | None = None,
    params: dict | None = None,
    pattern: str | None = None,
) -> dict:
    """(Deprecated) Use manage_routines instead.

    Skills are being replaced by routines. Use manage_routines(action="run", ...)
    to execute analysis scripts directly.

    Actions:
    - "list": List all available skills with descriptions
    - "test": Test a skill with given params (requires name)

    Args:
        action: The action to perform (list, test). Defaults to list if omitted.
        name: Skill name (for test).
        params: Skill parameters (for test, e.g. {"connector_name": "binance", "trading_pair": "BTC-USDT"}).
        pattern: Optional regex filter on names when listing (e.g. executor|position).

    Returns:
        Action-specific result dict.
    """
    import re

    act_raw = (action or "").strip().lower()
    if not act_raw:
        act_raw = "list"
    if act_raw not in ("list", "test") and pattern and str(pattern).strip():
        act_raw = "list"

    if act_raw == "list":
        result = _local_manage_skills_list()
        pat = (pattern or "").strip()
        if pat:
            try:
                rx = re.compile(pat, re.I | re.MULTILINE)
            except re.error:
                result["pattern_error"] = f"invalid regex: {pat!r}"
                return result
            items = result.get("skills") or []
            result["skills"] = [s for s in items if rx.search(str(s.get("name", "")))]
            result["pattern"] = pat
        return result

    if act_raw == "test":
        if not name:
            return {"error": "name is required"}
        return _local_skill_test(name, params or {})

    return {"error": f"Unknown action: {act_raw}"}


def _local_manage_skills_list() -> dict:
    from condor.trading_agent.providers import list_providers

    items = []
    for p in list_providers():
        items.append({
            "name": p.name,
            "is_core": p.is_core,
            "type": "provider",
        })
    return {"skills": items}


def _local_skill_test(name: str, config: dict) -> dict:
    from condor.trading_agent.providers import get_provider

    provider = get_provider(name)
    if provider:
        return {"error": f"Provider '{name}' requires the Condor bot to be running for testing (needs API client)"}

    return {"error": f"Skills have been removed; use manage_routines instead. Provider '{name}' not found."}


# ---------------------------------------------------------------------------
# Backward-compatibility aliases for journal tools
# ---------------------------------------------------------------------------


@mcp.tool()
@handle_errors("journal read")
async def trading_agent_journal_read(
    agent_id: str,
    section: str = "recent",
    max_entries: int = 30,
) -> dict:
    """Read the trading agent's journal.

    Args:
        agent_id: The trading agent instance ID.
        section: What to read:
                 "recent" (last 10 decisions from run snapshots),
                 "learnings" (all learnings, max 20),
                 "summary" (current status one-liner),
                 "state" (alias for summary),
                 "full" (entire journal),
                 "runs" (list recent run snapshots),
                 "run:N" (read specific run snapshot, e.g. "run:3").
        max_entries: Max entries for recent/runs (default 30).

    Returns:
        {"content": "<journal text>"} or {"runs": [...]} for runs listing.
    """
    return trading_agent.journal_read(agent_id, section, max_entries)


@mcp.tool()
@handle_errors("journal write")
async def trading_agent_journal_write(
    agent_id: str,
    entry_type: str,
    text: str,
    reasoning: str = "",
    risk_note: str = "",
    tick: int = 0,
    category: str = "",
) -> dict:
    """Write to the trading agent's journal. Keep entries SHORT (one line).

    Args:
        agent_id: The trading agent instance ID.
        entry_type: "action", "learning", or "state".
            - "action": What you did this tick (auto-trimmed to last 10).
            - "learning": A new insight. Duplicates are auto-filtered. Only write
              if this is genuinely new and not already in learnings (max 20).
            - "state": Overwrite the current state snapshot (e.g. price, position, grids).
        text: The entry content. Keep it to ONE short line.
        reasoning: One-sentence reasoning (for actions only).
        risk_note: Optional risk note (for actions only).
        tick: Current tick number (for actions only).
        category: Learning category: "market" (observations, patterns, volatility)
            or "execution" (errors, fills, timing). Only used when entry_type="learning".
            Defaults to "market".

    Returns:
        {"written": true}
    """
    return trading_agent.journal_write(
        agent_id, entry_type, text, reasoning, risk_note, tick, category,
    )


if __name__ == "__main__":
    mcp.run()
