"""Condor MCP Server -- exposes Condor capabilities to AI agents.

Provides local-only tools for routines, servers, user context,
trading agents, skills, and notes via MCP.

Expects CONDOR_CHAT_ID and CONDOR_USER_ID environment variables.
"""

import json
import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("condor")

CHAT_ID = int(os.environ.get("CONDOR_CHAT_ID", "0"))
USER_ID = int(os.environ.get("CONDOR_USER_ID", "0"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CONDOR_AGENT_SLUG = os.environ.get("CONDOR_AGENT_SLUG", "")


# =============================================================================
# Notification Tool
# =============================================================================


@mcp.tool()
async def send_notification(
    text: str,
    parse_mode: str = "Markdown",
) -> dict:
    """Send a Telegram message to the user.

    Args:
        text: Message text to send.
        parse_mode: Telegram parse mode ("Markdown" or "HTML"). Default: "Markdown".

    Returns:
        {"sent": true} on success, {"error": "..."} on failure.
    """
    if not TELEGRAM_BOT_TOKEN:
        return {"error": "TELEGRAM_BOT_TOKEN not configured"}
    if not CHAT_ID:
        return {"error": "CONDOR_CHAT_ID not configured"}

    import httpx

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            data = resp.json()
            if data.get("ok"):
                return {"sent": True}
            # Retry without parse_mode if formatting fails
            if "can't parse" in data.get("description", "").lower():
                payload.pop("parse_mode")
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("ok"):
                    return {"sent": True}
            return {"error": data.get("description", "Unknown Telegram API error")}
    except Exception as e:
        return {"error": f"Failed to send: {e}"}


# =============================================================================
# Routines Tools (local: list and describe only)
# =============================================================================


def _local_manage_routines_list(strategy_id: str | None = None) -> dict:
    from routines.base import discover_routines, discover_routines_from_path

    routines = discover_routines(force_reload=True)
    result = []
    for name, routine in sorted(routines.items()):
        result.append({
            "name": name,
            "description": routine.description,
            "type": "continuous" if routine.is_continuous else "one-shot",
            "scope": "global",
        })

    # Merge agent-local routines: use strategy_id if provided, else CONDOR_AGENT_SLUG
    agent_routines_dir = _get_agent_routines_dir(strategy_id) if strategy_id else None
    if not agent_routines_dir and CONDOR_AGENT_SLUG:
        from pathlib import Path
        agent_routines_dir = Path("trading_agents") / CONDOR_AGENT_SLUG / "routines"

    if agent_routines_dir and agent_routines_dir.exists():
        agent_routines = discover_routines_from_path(agent_routines_dir)
        for name, routine in sorted(agent_routines.items()):
            result.append({
                "name": name,
                "description": routine.description,
                "type": "continuous" if routine.is_continuous else "one-shot",
                "scope": "agent",
            })

    return {"routines": result}


def _resolve_routine(name: str):
    """Look up a routine: agent-local first, then global."""
    if CONDOR_AGENT_SLUG:
        from routines.base import discover_routines_from_path
        from pathlib import Path

        agent_routines_dir = Path("trading_agents") / CONDOR_AGENT_SLUG / "routines"
        if agent_routines_dir.exists():
            agent_routines = discover_routines_from_path(agent_routines_dir)
            if name in agent_routines:
                return agent_routines[name]

    from routines.base import get_routine
    return get_routine(name)


def _local_manage_routines_describe(name: str) -> dict:
    routine = _resolve_routine(name)
    if not routine:
        return {"error": f"Routine '{name}' not found"}
    fields = routine.get_fields()
    return {
        "name": name,
        "description": routine.description,
        "type": "continuous" if routine.is_continuous else "one-shot",
        "fields": fields,
    }


async def _local_manage_routines_run(name: str, config: dict | None, strategy_id: str | None = None) -> dict:
    """Execute a one-shot routine and return its result."""
    import asyncio

    routine = None

    # If strategy_id provided, look in agent-local routines first
    if strategy_id:
        routines_dir = _get_agent_routines_dir(strategy_id)
        if routines_dir and routines_dir.exists():
            from routines.base import discover_routines_from_path
            agent_routines = discover_routines_from_path(routines_dir)
            routine = agent_routines.get(name)

    # Fall back to default resolution (CONDOR_AGENT_SLUG → global)
    if not routine:
        routine = _resolve_routine(name)

    if not routine:
        return {"error": f"Routine '{name}' not found"}

    if routine.is_continuous:
        return {
            "error": f"Routine '{name}' is continuous and cannot be run via MCP. "
            "Use the Telegram /routines command to start/stop continuous routines."
        }

    # Build config from defaults + overrides
    try:
        config_obj = routine.config_class(**(config or {}))
    except Exception as e:
        return {"error": f"Invalid config: {e}"}

    # Minimal mock context — provides _chat_id for API client resolution
    class MCPContext:
        def __init__(self):
            self._chat_id = CHAT_ID
            self._user_id = USER_ID
            self._user_data: dict = {}
            self.bot = None
            self.application = None

        @property
        def user_data(self):
            return self._user_data

    context = MCPContext()

    try:
        result = await asyncio.wait_for(
            routine.run_fn(config_obj, context), timeout=120
        )
        return {"name": name, "result": result}
    except asyncio.TimeoutError:
        return {"error": f"Routine '{name}' timed out after 120s"}
    except Exception as e:
        return {"error": f"Routine '{name}' failed: {e}"}


def _get_agent_routines_dir(strategy_id: str | None) -> "Path | None":
    """Resolve the routines directory for a strategy."""
    from pathlib import Path

    # If strategy_id given, resolve slug from StrategyStore
    if strategy_id:
        from condor.trading_agent.strategy import StrategyStore
        store = StrategyStore()
        s = store.get(strategy_id)
        if not s:
            return None
        return Path("trading_agents") / s.slug / "routines"

    # Fall back to CONDOR_AGENT_SLUG env var
    if CONDOR_AGENT_SLUG:
        return Path("trading_agents") / CONDOR_AGENT_SLUG / "routines"

    return None


def _local_manage_routines_create(name: str, code: str, strategy_id: str | None) -> dict:
    """Create a new agent-local routine file."""
    import re
    from pathlib import Path

    if not name or not re.match(r"^[a-z][a-z0-9_]*$", name):
        return {"error": "name must be lowercase alphanumeric with underscores (e.g. 'my_scanner')"}
    if not code:
        return {"error": "code is required"}

    routines_dir = _get_agent_routines_dir(strategy_id)
    if not routines_dir:
        return {"error": "strategy_id is required (or CONDOR_AGENT_SLUG must be set)"}

    file_path = routines_dir / f"{name}.py"
    if file_path.exists():
        return {"error": f"Routine '{name}' already exists. Use action='edit_routine' to update it."}

    # Validate the code has required components
    if "class Config" not in code:
        return {"error": "Routine code must define a 'class Config(BaseModel)' class"}
    if "async def run" not in code and "def run" not in code:
        return {"error": "Routine code must define a 'run(config, context)' function"}

    routines_dir.mkdir(parents=True, exist_ok=True)
    file_path.write_text(code)

    # Verify it loads
    from routines.base import discover_routines_from_path
    loaded = discover_routines_from_path(routines_dir)
    if name not in loaded:
        # Remove the broken file
        file_path.unlink()
        return {"error": "Routine file was created but failed to load. Check for syntax errors."}

    routine = loaded[name]
    return {
        "created": True,
        "name": name,
        "description": routine.description,
        "path": str(file_path),
    }


def _local_manage_routines_read(name: str, strategy_id: str | None) -> dict:
    """Read the source code of a routine."""
    from pathlib import Path

    # Check agent-local first
    routines_dir = _get_agent_routines_dir(strategy_id)
    if routines_dir:
        file_path = routines_dir / f"{name}.py"
        if file_path.exists():
            return {"name": name, "code": file_path.read_text(), "scope": "agent"}

    # Check global routines
    global_path = Path("routines") / f"{name}.py"
    if global_path.exists():
        return {"name": name, "code": global_path.read_text(), "scope": "global"}

    return {"error": f"Routine '{name}' not found"}


def _local_manage_routines_edit(name: str, code: str, strategy_id: str | None) -> dict:
    """Update the source code of an agent-local routine."""
    routines_dir = _get_agent_routines_dir(strategy_id)
    if not routines_dir:
        return {"error": "strategy_id is required (or CONDOR_AGENT_SLUG must be set)"}

    file_path = routines_dir / f"{name}.py"
    if not file_path.exists():
        return {"error": f"Agent routine '{name}' not found. Use action='create_routine' first."}

    if not code:
        return {"error": "code is required"}

    # Write new code
    old_code = file_path.read_text()
    file_path.write_text(code)

    # Verify it loads
    from routines.base import discover_routines_from_path
    loaded = discover_routines_from_path(routines_dir)
    if name not in loaded:
        # Restore old code
        file_path.write_text(old_code)
        return {"error": "Updated code failed to load (syntax error?). Reverted to previous version."}

    routine = loaded[name]
    return {
        "updated": True,
        "name": name,
        "description": routine.description,
    }


def _local_manage_routines_delete(name: str, strategy_id: str | None) -> dict:
    """Delete an agent-local routine."""
    routines_dir = _get_agent_routines_dir(strategy_id)
    if not routines_dir:
        return {"error": "strategy_id is required (or CONDOR_AGENT_SLUG must be set)"}

    file_path = routines_dir / f"{name}.py"
    if not file_path.exists():
        return {"error": f"Agent routine '{name}' not found"}

    file_path.unlink()
    return {"deleted": True, "name": name}


@mcp.tool()
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
        name: Routine name (required for all except list).
        config: Config overrides for run (optional, merged with defaults).
        strategy_id: Strategy ID for agent-local routine CRUD operations.
        code: Python source code for create_routine / edit_routine.

    Returns:
        Action-specific result dict.
    """
    if action == "list":
        return _local_manage_routines_list(strategy_id)

    if action == "describe":
        if not name:
            return {"error": "name is required"}
        return _local_manage_routines_describe(name)

    if action == "run":
        if not name:
            return {"error": "name is required"}
        return await _local_manage_routines_run(name, config, strategy_id)

    if action == "create_routine":
        if not name:
            return {"error": "name is required"}
        return _local_manage_routines_create(name, code or "", strategy_id)

    if action == "read_routine":
        if not name:
            return {"error": "name is required"}
        return _local_manage_routines_read(name, strategy_id)

    if action == "edit_routine":
        if not name:
            return {"error": "name is required"}
        return _local_manage_routines_edit(name, code or "", strategy_id)

    if action == "delete_routine":
        if not name:
            return {"error": "name is required"}
        return _local_manage_routines_delete(name, strategy_id)

    return {"error": f"Unknown action: {action}"}


# =============================================================================
# Servers Tools (local: list and status only)
# =============================================================================


def _local_manage_servers_list() -> dict:
    from config_manager import get_config_manager

    cm = get_config_manager()
    accessible = cm.get_accessible_servers(USER_ID)
    active_server = cm.get_chat_default_server(CHAT_ID)
    servers = []
    for name in accessible:
        server = cm.get_server(name)
        if not server:
            continue
        perm = cm.get_server_permission(USER_ID, name)
        servers.append({
            "name": name,
            "host": server["host"],
            "port": server["port"],
            "permission": perm.value if perm else "unknown",
            "is_active": name == active_server,
        })
    return {"servers": servers, "active_server": active_server}


async def _local_manage_servers_status(name: str | None) -> dict:
    from config_manager import get_config_manager

    cm = get_config_manager()
    if not name:
        name = cm.get_chat_default_server(CHAT_ID)
        if not name:
            return {"error": "No active server"}
    if not cm.has_server_access(USER_ID, name):
        return {"error": f"No access to server '{name}'"}
    status = await cm.check_server_status(name)
    return {"server": name, **status}


@mcp.tool()
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
    if action == "list":
        return _local_manage_servers_list()

    if action == "status":
        return await _local_manage_servers_status(name)

    return {"error": f"Unknown action: {action}"}


# =============================================================================
# User Context Tools
# =============================================================================


@mcp.tool()
async def get_user_context() -> dict:
    """Get the current user's context within Condor.

    Returns:
        A dict with:
        - active_server: Currently active Hummingbot server name
        - user_role: User's role (admin, user, pending, blocked)
        - is_admin: Whether the user is an admin
    """
    from config_manager import get_config_manager

    cm = get_config_manager()
    active_server = cm.get_chat_default_server(CHAT_ID)
    user_role = cm.get_user_role(USER_ID)
    is_admin = cm.is_admin(USER_ID)

    return {
        "active_server": active_server,
        "user_role": user_role.value if user_role else None,
        "is_admin": is_admin,
    }


# =============================================================================
# Trading Agent Tools (mostly local)
# =============================================================================


def _local_journal_read(params: dict) -> dict:
    from condor.trading_agent.journal import JournalManager
    from condor.trading_agent.engine import get_engine

    agent_id = params.get("agent_id", "")
    if not agent_id:
        return {"error": "agent_id is required"}

    engine = get_engine(agent_id)
    if engine:
        session_dir = engine.session_dir
        agent_dir = engine.strategy.agent_dir
    else:
        from condor.trading_agent.journal import resolve_agent_dirs
        session_dir, agent_dir = resolve_agent_dirs(agent_id)
    jm = JournalManager(agent_id, session_dir=session_dir, agent_dir=agent_dir)

    section = params.get("section", "recent")
    max_entries = params.get("max_entries", 30)

    if section == "full":
        return {"content": jm.read_full()}
    elif section == "learnings":
        return {"content": jm.read_learnings()}
    elif section in ("state", "summary"):
        return {"content": jm.read_state()}
    elif section == "runs":
        runs = jm.list_runs(limit=max_entries)
        return {"runs": runs}
    elif section.startswith("run:"):
        try:
            tick_num = int(section.split(":", 1)[1])
        except (ValueError, IndexError):
            return {"error": "Invalid run format. Use 'run:N' where N is the tick number."}
        content = jm.read_run_snapshot(tick_num)
        if not content:
            return {"error": f"No run snapshot found for tick #{tick_num}"}
        return {"content": content}
    else:
        return {"content": jm.read_recent(max_entries=max_entries)}


def _local_journal_write(params: dict) -> dict:
    from condor.trading_agent.journal import JournalManager
    from condor.trading_agent.engine import get_engine

    agent_id = params.get("agent_id", "")
    if not agent_id:
        return {"error": "agent_id is required"}

    engine = get_engine(agent_id)
    if engine:
        session_dir = engine.session_dir
        agent_dir = engine.strategy.agent_dir
    else:
        from condor.trading_agent.journal import resolve_agent_dirs
        session_dir, agent_dir = resolve_agent_dirs(agent_id)
    jm = JournalManager(agent_id, session_dir=session_dir, agent_dir=agent_dir)

    entry_type = params.get("entry_type", "action")
    text = params.get("text", "")
    if not text:
        return {"error": "text is required"}

    if entry_type == "learning":
        jm.append_learning(text)
    elif entry_type == "state":
        jm.write_state(text)
    else:
        tick = params.get("tick", 0)
        reasoning = params.get("reasoning", "")
        risk_note = params.get("risk_note", "")
        jm.append_action(tick, text, reasoning, risk_note)
    return {"written": True}


@mcp.tool()
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
    return _local_journal_read(
        {"agent_id": agent_id, "section": section, "max_entries": max_entries}
    )


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
    return _local_journal_write({
        "agent_id": agent_id,
        "entry_type": entry_type,
        "text": text,
        "reasoning": reasoning,
        "risk_note": risk_note,
        "tick": tick,
    })


@mcp.tool()
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
    params: dict = {"action": action}
    if key is not None:
        params["key"] = key
    if value is not None:
        params["value"] = value

    return _local_manage_notes(params)


def _local_manage_notes(params: dict) -> dict:
    """File-based notes storage."""
    import json
    from pathlib import Path

    notes_file = Path("data") / "notes" / f"chat_{CHAT_ID}.json"

    def _load() -> dict:
        if notes_file.exists():
            try:
                return json.loads(notes_file.read_text())
            except Exception:
                return {}
        return {}

    def _save(notes: dict) -> None:
        notes_file.parent.mkdir(parents=True, exist_ok=True)
        notes_file.write_text(json.dumps(notes, indent=2))

    action = params.get("action", "list")

    if action == "list":
        return {"notes": _load()}

    elif action == "get":
        key = params.get("key")
        if not key:
            return {"error": "key is required"}
        notes = _load()
        value = notes.get(key)
        if value is None:
            return {"error": f"Note '{key}' not found"}
        return {"key": key, "value": value}

    elif action == "set":
        key = params.get("key")
        value = params.get("value")
        if not key or value is None:
            return {"error": "key and value are required"}
        notes = _load()
        notes[key] = str(value)
        _save(notes)
        return {"saved": True, "key": key, "value": str(value)}

    elif action == "delete":
        key = params.get("key")
        if not key:
            return {"error": "key is required"}
        notes = _load()
        if key not in notes:
            return {"error": f"Note '{key}' not found"}
        del notes[key]
        _save(notes)
        return {"deleted": True, "key": key}

    return {"error": f"Unknown action: {action}"}


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

    Actions -- Lifecycle:
    - "list_agents": List all running agent instances with status
    - "start_agent": Start a new agent session (requires strategy_id, optional config overrides)
    - "stop_agent": Stop a running agent (requires agent_id)
    - "pause_agent": Pause a running agent (requires agent_id)
    - "resume_agent": Resume a paused agent (requires agent_id)

    Actions -- Routines (scoped to a strategy):
    - "list_routines": List global + agent-local routines for a strategy (requires strategy_id)
    - "run_routine": Execute a one-shot routine (requires strategy_id, name, optional config)

    Actions -- Monitoring:
    - "agent_tracker": Get the full tracker markdown (tick history, executor ledger, snapshots) (requires agent_id)
    - "agent_journal": Get recent journal entries and learnings (requires agent_id)

    Args:
        action: The action to perform.
        agent_id: Agent instance ID (for lifecycle/monitoring actions).
        strategy_id: Strategy ID (for strategy/routine/start actions).
        name: Strategy name (for create/update) or routine name (for run_routine).
        description: Strategy description (for create/update).
        instructions: Strategy instructions text (for create/update).
        agent_key: Agent type "claude-code" or "gemini" (for create, default "claude-code").
        skills: List of optional skill names to enable (for create/update).
        config: Agent config overrides (for create/update/start) or routine config (for run_routine).

    Returns:
        Action-specific result dict.
    """
    # Strategy operations work locally (file-based StrategyStore)
    local_strategy_actions = {
        "list_strategies", "get_strategy", "create_strategy",
        "update_strategy", "delete_strategy",
    }

    if action in local_strategy_actions:
        return _local_manage_strategy(action, strategy_id, name, description,
                                       instructions, agent_key, skills, config)

    # Routine actions scoped to a strategy
    if action == "list_routines":
        if not strategy_id:
            return {"error": "strategy_id is required"}
        return _local_strategy_list_routines(strategy_id)

    if action == "run_routine":
        if not strategy_id:
            return {"error": "strategy_id is required"}
        if not name:
            return {"error": "name is required"}
        return await _local_manage_routines_run(name, config, strategy_id)

    # Agent lifecycle actions
    lifecycle_actions = {"start_agent", "stop_agent", "pause_agent", "resume_agent", "list_agents"}
    if action in lifecycle_actions:
        return await _local_agent_lifecycle(action, strategy_id, agent_id, config)

    # Journal/monitoring that's file-based
    if action in ("agent_tracker", "agent_journal"):
        return _local_agent_monitoring(action, agent_id)

    return {"error": f"Unknown action: {action}"}


def _local_strategy_list_routines(strategy_id: str) -> dict:
    """List global + agent-local routines for a strategy, with scope labels."""
    from routines.base import discover_routines, discover_routines_from_path

    result = []

    # Global routines
    for name, routine in sorted(discover_routines(force_reload=True).items()):
        result.append({
            "name": name,
            "description": routine.description,
            "type": "continuous" if routine.is_continuous else "one-shot",
            "scope": "global",
        })

    # Agent-local routines
    routines_dir = _get_agent_routines_dir(strategy_id)
    if routines_dir and routines_dir.exists():
        for name, routine in sorted(discover_routines_from_path(routines_dir).items()):
            result.append({
                "name": name,
                "description": routine.description,
                "type": "continuous" if routine.is_continuous else "one-shot",
                "scope": "agent",
            })

    return {"routines": result}


def _local_manage_strategy(
    action: str, strategy_id: str | None, name: str | None,
    description: str | None, instructions: str | None,
    agent_key: str | None, skills: list[str] | None, config: dict | None,
) -> dict:
    from condor.trading_agent.strategy import StrategyStore

    store = StrategyStore()

    if action == "list_strategies":
        strategies = store.list_all()
        return {
            "strategies": [
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "agent_key": s.agent_key,
                    "skills": s.skills,
                    "default_config": s.default_config,
                }
                for s in strategies
            ]
        }

    elif action == "get_strategy":
        if not strategy_id:
            return {"error": "strategy_id is required"}
        s = store.get(strategy_id)
        if not s:
            return {"error": f"Strategy '{strategy_id}' not found"}
        return {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "agent_key": s.agent_key,
            "instructions": s.instructions,
            "skills": s.skills,
            "default_config": s.default_config,
            "created_by": s.created_by,
            "created_at": s.created_at,
        }

    elif action == "create_strategy":
        if not name or not instructions:
            return {"error": "name and instructions are required"}
        strategy = store.create(
            name=name,
            description=description or "",
            agent_key=agent_key or "claude-code",
            instructions=instructions,
            skills=skills,
            default_config=config,
            created_by=USER_ID,
        )
        return {"created": True, "strategy_id": strategy.id, "name": strategy.name}

    elif action == "update_strategy":
        if not strategy_id:
            return {"error": "strategy_id is required"}
        s = store.get(strategy_id)
        if not s:
            return {"error": f"Strategy '{strategy_id}' not found"}
        if name:
            s.name = name
        if description:
            s.description = description
        if instructions:
            s.instructions = instructions
        if agent_key:
            s.agent_key = agent_key
        if skills is not None:
            s.skills = skills
        if config:
            s.default_config = config
        store.update(s)
        return {"updated": True, "strategy_id": s.id, "name": s.name}

    elif action == "delete_strategy":
        if not strategy_id:
            return {"error": "strategy_id is required"}
        deleted = store.delete(strategy_id)
        return {"deleted": deleted}

    return {"error": f"Unknown strategy action: {action}"}


async def _local_agent_lifecycle(
    action: str, strategy_id: str | None, agent_id: str | None, config: dict | None,
) -> dict:
    from condor.trading_agent.engine import TickEngine, get_engine, get_all_engines

    if action == "list_agents":
        engines = get_all_engines()
        if not engines:
            return {"agents": [], "message": "No agents running"}
        return {
            "agents": [e.get_info() for e in engines.values()],
        }

    if action == "start_agent":
        if not strategy_id:
            return {"error": "strategy_id is required"}
        from condor.trading_agent.strategy import StrategyStore
        from condor.trading_agent.config import load_agent_config
        store = StrategyStore()
        strategy = store.get(strategy_id)
        if not strategy:
            return {"error": f"Strategy '{strategy_id}' not found"}
        agent_config = load_agent_config(strategy.agent_dir, strategy.default_config)
        config_dict = agent_config.model_dump()
        if config:
            # Translate dry_run shorthand → execution_mode
            if config.get("dry_run") and "execution_mode" not in config:
                config["execution_mode"] = "dry_run"
            config_dict.update(config)
        engine = TickEngine(
            strategy=strategy,
            config=config_dict,
            chat_id=CHAT_ID,
            user_id=USER_ID,
        )
        await engine.start()
        return {"started": True, "agent_id": engine.agent_id, "session_num": engine.session_num}

    if action == "stop_agent":
        if not agent_id:
            return {"error": "agent_id is required"}
        engine = get_engine(agent_id)
        if not engine:
            return {"error": f"Agent '{agent_id}' not found"}
        await engine.stop()
        return {"stopped": True, "agent_id": agent_id}

    if action == "pause_agent":
        if not agent_id:
            return {"error": "agent_id is required"}
        engine = get_engine(agent_id)
        if not engine or not engine.is_running:
            return {"error": f"Agent '{agent_id}' not found or not running"}
        engine.pause()
        return {"paused": True, "agent_id": agent_id}

    if action == "resume_agent":
        if not agent_id:
            return {"error": "agent_id is required"}
        engine = get_engine(agent_id)
        if not engine:
            return {"error": f"Agent '{agent_id}' not found"}
        engine.resume()
        return {"resumed": True, "agent_id": agent_id}

    return {"error": f"Unknown lifecycle action: {action}"}


def _local_agent_monitoring(action: str, agent_id: str | None) -> dict:
    if not agent_id:
        return {"error": "agent_id is required"}

    from condor.trading_agent.journal import JournalManager
    from condor.trading_agent.engine import get_engine

    engine = get_engine(agent_id)
    if engine:
        session_dir = engine.session_dir
        agent_dir = engine.strategy.agent_dir
    else:
        from condor.trading_agent.journal import resolve_agent_dirs
        session_dir, agent_dir = resolve_agent_dirs(agent_id)
    jm = JournalManager(agent_id, session_dir=session_dir, agent_dir=agent_dir)

    if action == "agent_tracker":
        content = jm.read_full()
        summary = jm.get_summary_dict()
        return {"tracker_md": content, "summary": summary}

    elif action == "agent_journal":
        return {
            "recent_actions": jm.read_recent(max_entries=30),
            "learnings": jm.read_learnings(),
            "entry_count": jm.entry_count(),
        }

    return {"error": f"Unknown monitoring action: {action}"}


@mcp.tool()
async def manage_skills(
    action: str,
    name: str | None = None,
    params: dict | None = None,
) -> dict:
    """(Deprecated) Use manage_routines instead.

    Skills are being replaced by routines. Use manage_routines(action="run", ...)
    to execute analysis scripts directly.

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
    if action == "list":
        return _local_manage_skills_list()

    if action == "test":
        if not name:
            return {"error": "name is required"}
        return _local_skill_test(name, params or {})

    return {"error": f"Unknown action: {action}"}


def _local_manage_skills_list() -> dict:
    from condor.trading_agent.providers import list_providers
    from condor.trading_agent.skill_loader import list_skills as list_skill_files

    items = []
    for p in list_providers():
        items.append({
            "name": p.name,
            "is_core": p.is_core,
            "type": "provider",
        })
    for s in list_skill_files():
        items.append({
            "name": s.name,
            "is_core": False,
            "type": "skill",
            "description": s.description,
        })
    return {"skills": items}


def _local_skill_test(name: str, config: dict) -> dict:
    from condor.trading_agent.skill_loader import load_skill, _render_placeholders
    from condor.trading_agent.providers import get_provider

    # Check if it's a data provider (needs API client -- can't test locally)
    provider = get_provider(name)
    if provider:
        return {"error": f"Provider '{name}' requires the Condor bot to be running for testing (needs API client)"}

    # Test SKILL.md file (can render locally)
    skill_info = load_skill(name)
    if skill_info:
        rendered = _render_placeholders(skill_info.body, config)
        return {
            "name": skill_info.name,
            "description": skill_info.description,
            "rendered_prompt": rendered,
        }

    return {"error": f"Skill or provider '{name}' not found"}


if __name__ == "__main__":
    mcp.run()
