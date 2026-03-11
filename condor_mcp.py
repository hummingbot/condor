"""Condor MCP Server -- exposes Condor capabilities to AI agents.

Provides widget tools (buttons, notifications) and Condor internals
(routines, servers, user context) via MCP.

Communicates with the Widget Bridge inside the Condor bot via TCP.
Expects CONDOR_WIDGET_PORT, CONDOR_CHAT_ID, and CONDOR_USER_ID
environment variables.
"""

import asyncio
import json
import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("condor")

WIDGET_PORT = int(os.environ.get("CONDOR_WIDGET_PORT", "0"))
CHAT_ID = int(os.environ.get("CONDOR_CHAT_ID", "0"))
USER_ID = int(os.environ.get("CONDOR_USER_ID", "0"))

TCP_READ_LIMIT = 1_048_576  # 1 MB


async def _bridge_request(request: dict) -> dict:
    """Send a JSON request to the Widget Bridge and return the response."""
    reader, writer = await asyncio.open_connection("127.0.0.1", WIDGET_PORT)
    try:
        writer.write(json.dumps(request).encode())
        await writer.drain()
        writer.write_eof()
        data = await asyncio.wait_for(reader.read(TCP_READ_LIMIT), timeout=130)
        return json.loads(data.decode())
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


# =============================================================================
# Widget Tools (existing)
# =============================================================================


@mcp.tool()
async def send_buttons(message: str, buttons: list[list[dict]]) -> dict:
    """Send a message with an inline keyboard to the Telegram chat.

    Args:
        message: The message text to display above the buttons.
        buttons: A list of rows, where each row is a list of button objects
                 with "label" (display text) and "value" (returned on click).
                 Example: [[{"label": "Option A", "value": "a"}, {"label": "Option B", "value": "b"}]]

    Returns:
        {"selected": "<value>"} when the user clicks a button, or {"timeout": true} after 120s.
    """
    return await _bridge_request({
        "method": "send_buttons",
        "chat_id": CHAT_ID,
        "message": message,
        "buttons": buttons,
    })


@mcp.tool()
async def ask_user_choice(question: str, options: list[str]) -> dict:
    """Ask the user to choose from a list of options using inline buttons.

    This is a convenience wrapper around send_buttons that creates a single-column layout.

    Args:
        question: The question to display.
        options: A list of option strings. Each becomes a button.

    Returns:
        {"selected": "<option text>"} when the user clicks, or {"timeout": true} after 120s.
    """
    buttons = [[{"label": opt, "value": opt}] for opt in options]
    return await _bridge_request({
        "method": "send_buttons",
        "chat_id": CHAT_ID,
        "message": question,
        "buttons": buttons,
    })


@mcp.tool()
async def send_notification(message: str) -> dict:
    """Send a one-way notification message to the Telegram chat (no buttons, no waiting).

    Args:
        message: The notification text to send.

    Returns:
        {"sent": true} on success.
    """
    return await _bridge_request({
        "method": "send_notification",
        "chat_id": CHAT_ID,
        "message": message,
    })


@mcp.tool()
async def send_progress(message: str, percentage: int | None = None) -> dict:
    """Send a progress notification to the Telegram chat with an optional progress bar.

    Args:
        message: The progress message to display (e.g., "Analyzing 5/12 pairs...").
        percentage: Optional progress percentage (0-100). Shows a visual progress bar when provided.

    Returns:
        {"sent": true} on success.
    """
    text = message
    if percentage is not None:
        pct = max(0, min(100, percentage))
        filled = pct // 5  # 20-char bar
        bar = "\u2588" * filled + "\u2591" * (20 - filled)
        text = f"{message}\n{bar} {pct}%"

    return await _bridge_request({
        "method": "send_notification",
        "chat_id": CHAT_ID,
        "message": text,
    })


@mcp.tool()
async def get_session_info() -> dict:
    """Get information about the current Telegram session.

    Returns:
        A dict with chat_id, widget_port, and connected status.
    """
    connected = False
    if WIDGET_PORT:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", WIDGET_PORT)
            writer.close()
            await writer.wait_closed()
            connected = True
        except Exception:
            pass

    return {
        "chat_id": CHAT_ID,
        "widget_port": WIDGET_PORT,
        "connected": connected,
    }


# =============================================================================
# Routines Tools (new)
# =============================================================================


@mcp.tool()
async def manage_routines(
    action: str,
    name: str | None = None,
    config: dict | None = None,
    schedule: dict | None = None,
    instance_id: str | None = None,
) -> dict:
    """Manage Condor routines (auto-discoverable Python scripts).

    Actions:
    - "list": List all available routines with name, description, and type
    - "describe": Show config schema for a routine (requires name)
    - "run": Execute a one-shot routine once and return the result (requires name, optional config overrides)
    - "schedule": Start a scheduled/continuous routine (requires name, optional config and schedule)
    - "list_active": List all currently running routine instances
    - "stop": Stop a running routine instance (requires instance_id)

    Args:
        action: The action to perform (list, describe, run, schedule, list_active, stop)
        name: Routine name (required for describe, run, schedule)
        config: Config overrides as key-value pairs (optional for run, schedule)
        schedule: Schedule configuration (for schedule action). Examples:
                  {"type": "once"} - run once
                  {"type": "interval", "interval_sec": 60} - every 60 seconds
                  {"type": "daily", "daily_time": "09:00"} - daily at 9am
                  {"type": "continuous"} - for continuous routines
        instance_id: Instance ID to stop (required for stop action)

    Returns:
        Action-specific result dict.
    """
    params = {"action": action}
    if name is not None:
        params["name"] = name
    if config is not None:
        params["config"] = config
    if schedule is not None:
        params["schedule"] = schedule
    if instance_id is not None:
        params["instance_id"] = instance_id

    return await _bridge_request({
        "method": "manage_routines",
        "chat_id": CHAT_ID,
        "user_id": USER_ID,
        "params": params,
    })


# =============================================================================
# Servers Tools (new)
# =============================================================================


@mcp.tool()
async def manage_servers(
    action: str,
    name: str | None = None,
) -> dict:
    """Manage Hummingbot API servers (list, switch active, check status).

    Actions:
    - "list": List all accessible servers with permissions and active status
    - "switch": Switch the active server for this chat (requires name)
    - "status": Check if a server is online (optional name, defaults to active server)

    Args:
        action: The action to perform (list, switch, status)
        name: Server name (required for switch, optional for status)

    Returns:
        Action-specific result dict.
    """
    params = {"action": action}
    if name is not None:
        params["name"] = name

    return await _bridge_request({
        "method": "manage_servers",
        "chat_id": CHAT_ID,
        "user_id": USER_ID,
        "params": params,
    })


# =============================================================================
# User Context Tool (new)
# =============================================================================


@mcp.tool()
async def get_session_usage() -> dict:
    """Get current session token usage and context window stats.

    Returns:
        tokens_used: Number of tokens used so far.
        context_window: Total context window size.
        percent_used: Percentage of context consumed.
        cost_usd: Cumulative session cost in USD.
    """
    return await _bridge_request({
        "method": "get_session_usage",
        "chat_id": CHAT_ID,
    })


@mcp.tool()
async def get_user_context() -> dict:
    """Get the current user's context within Condor.

    Returns:
        A dict with:
        - active_server: Currently active Hummingbot server name
        - user_role: User's role (admin, user, pending, blocked)
        - is_admin: Whether the user is an admin
        - active_routine_count: Number of running routine instances
        - preferences: User's trading preferences (portfolio, CLOB, DEX, etc.)
    """
    return await _bridge_request({
        "method": "get_user_context",
        "chat_id": CHAT_ID,
        "user_id": USER_ID,
    })


# =============================================================================
# Trading Agent Tools
# =============================================================================


@mcp.tool()
async def trading_agent_journal_read(
    agent_id: str,
    section: str = "recent",
    max_entries: int = 30,
) -> dict:
    """Read the trading agent's journal.

    Args:
        agent_id: The trading agent instance ID.
        section: What to read: "recent" (last 10 actions),
                 "learnings" (all learnings, max 20),
                 "state" (current state snapshot),
                 or "full" (entire journal).
        max_entries: Unused (kept for compatibility).

    Returns:
        {"content": "<journal text>"}
    """
    return await _bridge_request({
        "method": "trading_agent_journal",
        "chat_id": CHAT_ID,
        "params": {"action": "read", "agent_id": agent_id, "section": section, "max_entries": max_entries},
    })


@mcp.tool()
async def trading_agent_journal_write(
    agent_id: str,
    entry_type: str,
    text: str,
    reasoning: str = "",
    risk_note: str = "",
    tick: int = 0,
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

    Returns:
        {"written": true}
    """
    return await _bridge_request({
        "method": "trading_agent_journal",
        "chat_id": CHAT_ID,
        "params": {
            "action": "write",
            "agent_id": agent_id,
            "entry_type": entry_type,
            "text": text,
            "reasoning": reasoning,
            "risk_note": risk_note,
            "tick": tick,
        },
    })


@mcp.tool()
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

    Actions -- Agent lifecycle:
    - "list_agents": List running agent instances with status and PnL
    - "start_agent": Start an agent from a strategy (requires strategy_id, optional config)
    - "stop_agent": Stop a running agent (requires agent_id)
    - "pause_agent": Pause a running agent (requires agent_id)
    - "resume_agent": Resume a paused agent (requires agent_id)

    Actions -- Monitoring:
    - "agent_status": Get status, PnL, tick count, risk state of an agent (requires agent_id)
    - "agent_tracker": Get the full tracker markdown (tick history, executor ledger, snapshots) (requires agent_id)
    - "agent_journal": Get recent journal entries and learnings (requires agent_id)
    - "agent_risk": Get current risk limits and state (requires agent_id)

    Args:
        action: The action to perform.
        agent_id: Agent instance ID (for agent actions).
        strategy_id: Strategy ID (for strategy actions).
        name: Strategy name (for create/update).
        description: Strategy description (for create/update).
        instructions: Strategy instructions text (for create/update).
        agent_key: Agent type "claude-code" or "gemini" (for create, default "claude-code").
        skills: List of optional skill names to enable (for create/update).
        config: Agent config overrides (for start, or risk_limits dict).

    Returns:
        Action-specific result dict.
    """
    params: dict = {"action": action}
    for key, val in [
        ("agent_id", agent_id), ("strategy_id", strategy_id),
        ("name", name), ("description", description),
        ("instructions", instructions), ("agent_key", agent_key),
        ("skills", skills), ("config", config),
    ]:
        if val is not None:
            params[key] = val

    return await _bridge_request({
        "method": "manage_trading_agent",
        "chat_id": CHAT_ID,
        "user_id": USER_ID,
        "params": params,
    })


@mcp.tool()
async def manage_skills(
    action: str,
    name: str | None = None,
    params: dict | None = None,
) -> dict:
    """Manage trading agent skills (market analysis tools).

    Actions:
    - "list": List all available skills with descriptions
    - "test": Test a skill with given params (requires name)

    Args:
        action: The action to perform (list, test).
        name: Skill name (for test).
        params: Skill parameters (for test, e.g. {"connector_name": "binance", "trading_pair": "BTC-USDT"}).

    Returns:
        Action-specific result dict.
    """
    req_params: dict = {"action": action}
    if name is not None:
        req_params["name"] = name
    if params is not None:
        req_params["params"] = params

    return await _bridge_request({
        "method": "manage_skills",
        "chat_id": CHAT_ID,
        "user_id": USER_ID,
        "params": req_params,
    })


if __name__ == "__main__":
    mcp.run()
