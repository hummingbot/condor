"""Trading agent strategy CRUD, lifecycle, monitoring, and journal."""

from pathlib import Path

from mcp_servers.condor.condor_client import agent_strategy_from_agent_id, call_main_api
from mcp_servers.condor.exceptions import APIError
from mcp_servers.condor.settings import settings

# ---------------------------------------------------------------------------
# Strategy CRUD (sub-resource of an Agent)
#
# ``strategy_id`` is the opaque composite key ``"{agent_slug}.{strategy_slug}"``
# returned by list_strategies/create_strategy — the LLM just passes it back.
# ``agent_slug`` (the owning Agent) is required to create a strategy.
# ---------------------------------------------------------------------------


def _manage_strategy(
    action: str,
    strategy_id: str | None,
    agent_slug: str | None,
    name: str | None,
    description: str | None,
    instructions: str | None,
    agent_key: str | None,
    skills: list[str] | None,
    config: dict | None,
) -> dict:
    from condor.trading_agent.strategy import StrategyStore, split_key

    store = StrategyStore()

    if action == "list_strategies":
        strategies = store.list_all()
        return {
            "strategies": [
                {
                    "id": s.key,
                    "agent_slug": s.agent_slug,
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
        s = store.get_by_key(strategy_id)
        if not s:
            return {"error": f"Strategy '{strategy_id}' not found"}
        return {
            "id": s.key,
            "agent_slug": s.agent_slug,
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
        if not agent_slug:
            return {
                "error": "agent_slug (the owning Agent) is required to create a strategy"
            }
        from condor.trading_agent.agent import AgentStore

        if AgentStore().get(agent_slug) is None:
            return {"error": f"Agent '{agent_slug}' not found"}
        strategy = store.create(
            agent_slug=agent_slug,
            name=name,
            description=description or "",
            agent_key=agent_key,
            instructions=instructions,
            skills=skills,
            default_config=config,
            created_by=settings.user_id,
        )
        return {"created": True, "strategy_id": strategy.key, "name": strategy.name}

    elif action == "update_strategy":
        if not strategy_id:
            return {"error": "strategy_id is required"}
        s = store.get_by_key(strategy_id)
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
        return {"updated": True, "strategy_id": s.key, "name": s.name}

    elif action == "delete_strategy":
        if not strategy_id:
            return {"error": "strategy_id is required"}
        parts = split_key(strategy_id)
        if not parts:
            return {"error": f"Invalid strategy_id '{strategy_id}'"}
        deleted = store.delete(parts[0], parts[1])
        return {"deleted": deleted}

    return {"error": f"Unknown strategy action: {action}"}


# ---------------------------------------------------------------------------
# Strategy-scoped routine listing
# ---------------------------------------------------------------------------


def _strategy_list_routines(strategy_id: str) -> dict:
    """List global + agent-local routines for a strategy, with scope labels."""
    from routines.base import discover_routines, discover_routines_from_path

    result = []

    for name, routine in sorted(discover_routines(force_reload=True).items()):
        result.append(
            {
                "name": name,
                "description": routine.description,
                "type": "continuous" if routine.is_continuous else "one-shot",
                "scope": "global",
            }
        )

    from mcp_servers.condor.tools.routines import _get_agent_routines_dir

    routines_dir = _get_agent_routines_dir(strategy_id)
    if routines_dir and routines_dir.exists():
        for name, routine in sorted(discover_routines_from_path(routines_dir).items()):
            result.append(
                {
                    "name": name,
                    "description": routine.description,
                    "type": "continuous" if routine.is_continuous else "one-shot",
                    "scope": "agent",
                }
            )

    return {"routines": result}


# ---------------------------------------------------------------------------
# Agent lifecycle (delegates to main process via web API)
# ---------------------------------------------------------------------------


async def _agent_lifecycle(
    action: str,
    strategy_id: str | None,
    agent_id: str | None,
    config: dict | None,
) -> dict:
    try:
        if action == "list_agents":
            result = await call_main_api("GET", "/agents")
            agents = []
            if isinstance(result, list):
                for agent_summary in result:
                    for strat in agent_summary.get("strategies", []):
                        for inst in strat.get("instances", []):
                            agents.append(inst)
            if not agents:
                return {"agents": [], "message": "No agents running"}
            return {"agents": agents}

        if action == "start_agent":
            if not strategy_id:
                return {"error": "strategy_id is required"}

            from condor.trading_agent.strategy import StrategyStore

            store = StrategyStore()
            strategy = store.get_by_key(strategy_id)
            if not strategy:
                return {"error": f"Strategy '{strategy_id}' not found"}

            from condor.trading_agent.config import load_full_config
            from config_manager import get_config_manager, get_effective_server

            config_dict = load_full_config(strategy.dir, strategy.default_config)
            if config:
                if config.get("dry_run") and "execution_mode" not in config:
                    config["execution_mode"] = "dry_run"
                config_dict.update(config)
            if not config or "server_name" not in config:
                effective = settings.active_server or get_effective_server(
                    settings.chat_id
                )
                if not effective:
                    cm = get_config_manager()
                    accessible = cm.get_accessible_servers(settings.user_id)
                    effective = accessible[0] if accessible else None
                if effective:
                    config_dict["server_name"] = effective

            trading_context = config_dict.pop("trading_context", "")

            return await call_main_api(
                "POST",
                f"/agents/{strategy.agent_slug}/strategies/{strategy.slug}/start",
                {
                    "config": config_dict,
                    "trading_context": trading_context,
                    "chat_id": settings.chat_id,
                    "user_id": settings.user_id,
                },
            )

        if action in ("stop_agent", "pause_agent", "resume_agent"):
            if not agent_id:
                return {"error": "agent_id is required"}
            verb = {
                "stop_agent": "stop",
                "pause_agent": "pause",
                "resume_agent": "resume",
            }[action]
            aslug, sslug = agent_strategy_from_agent_id(agent_id)
            return await call_main_api(
                "POST",
                f"/agents/{aslug}/strategies/{sslug}/{verb}?agent_id={agent_id}",
            )

        return {"error": f"Unknown lifecycle action: {action}"}
    except APIError as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Journal read/write
# ---------------------------------------------------------------------------


def _resolve_journal_manager(agent_id: str):
    """Get JournalManager for an agent, returns (jm, error_dict)."""
    from condor.trading_agent.engine import get_engine
    from condor.trading_agent.journal import JournalManager

    engine = get_engine(agent_id)
    if engine:
        if engine.is_experiment:
            return None, {
                "content": "(experiment mode — no journal, results saved to dry_runs/)"
            }
        session_dir = engine.session_dir
        agent_dir = engine.strategy.dir
    else:
        from condor.trading_agent.journal import resolve_agent_dirs

        session_dir, agent_dir = resolve_agent_dirs(agent_id)
    if not session_dir:
        return None, {"content": "(no journal available for this agent)"}
    return JournalManager(agent_id, session_dir=session_dir, agent_dir=agent_dir), None


def journal_read(agent_id: str, section: str = "recent", max_entries: int = 30) -> dict:
    if not agent_id:
        return {"error": "agent_id is required"}

    jm, err = _resolve_journal_manager(agent_id)
    if err:
        return err

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
            return {
                "error": "Invalid run format. Use 'run:N' where N is the tick number."
            }
        content = jm.read_run_snapshot(tick_num)
        if not content:
            return {"error": f"No run snapshot found for tick #{tick_num}"}
        return {"content": content}
    else:
        return {"content": jm.read_recent(max_entries=max_entries)}


def journal_write(
    agent_id: str,
    entry_type: str,
    text: str,
    reasoning: str = "",
    risk_note: str = "",
    tick: int = 0,
    category: str = "",
) -> dict:
    if not agent_id:
        return {"error": "agent_id is required"}
    if not text:
        return {"error": "text is required"}

    from condor.trading_agent.engine import get_engine
    from condor.trading_agent.journal import JournalManager

    engine = get_engine(agent_id)
    if engine:
        if engine.is_experiment:
            return {
                "error": "experiments don't have a journal — use dry_runs/ for results"
            }
        session_dir = engine.session_dir
        agent_dir = engine.strategy.dir
    else:
        from condor.trading_agent.journal import resolve_agent_dirs

        session_dir, agent_dir = resolve_agent_dirs(agent_id)
    if not session_dir:
        return {"error": "no journal available for this agent"}
    jm = JournalManager(agent_id, session_dir=session_dir, agent_dir=agent_dir)

    if entry_type == "learning":
        jm.append_learning(text, category=category or "market")
    elif entry_type == "state":
        jm.write_state(text)
    else:
        jm.append_action(tick, text, reasoning, risk_note)
    return {"written": True}


# ---------------------------------------------------------------------------
# Agent monitoring (file-based)
# ---------------------------------------------------------------------------


def _agent_monitoring(action: str, agent_id: str | None) -> dict:
    if not agent_id:
        return {"error": "agent_id is required"}

    jm, err = _resolve_journal_manager(agent_id)
    if err:
        # For monitoring, convert experiment/missing journal to error
        if "experiment" in str(err.get("content", "")):
            return {
                "error": "experiments don't have a journal — use dry_runs/ for results"
            }
        return {"error": "no journal available for this agent"}

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


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def manage_trading_agent(
    action: str,
    agent_id: str | None = None,
    strategy_id: str | None = None,
    agent_slug: str | None = None,
    name: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    agent_key: str | None = None,
    skills: list[str] | None = None,
    config: dict | None = None,
    # Journal params (for journal_read/journal_write actions)
    section: str = "recent",
    max_entries: int = 30,
    entry_type: str = "action",
    text: str = "",
    reasoning: str = "",
    risk_note: str = "",
    tick: int = 0,
    category: str = "",
) -> dict:
    # Strategy operations
    local_strategy_actions = {
        "list_strategies",
        "get_strategy",
        "create_strategy",
        "update_strategy",
        "delete_strategy",
    }
    if action in local_strategy_actions:
        return _manage_strategy(
            action,
            strategy_id,
            agent_slug,
            name,
            description,
            instructions,
            agent_key,
            skills,
            config,
        )

    # Routine actions scoped to a strategy
    if action == "list_routines":
        if not strategy_id:
            return {"error": "strategy_id is required"}
        return _strategy_list_routines(strategy_id)

    if action == "run_routine":
        if not strategy_id:
            return {"error": "strategy_id is required"}
        if not name:
            return {"error": "name is required"}
        from mcp_servers.condor.tools.routines import run_routine

        return await run_routine(name, config, strategy_id)

    # Agent lifecycle actions
    lifecycle_actions = {
        "start_agent",
        "stop_agent",
        "pause_agent",
        "resume_agent",
        "list_agents",
    }
    if action in lifecycle_actions:
        return await _agent_lifecycle(action, strategy_id, agent_id, config)

    # Journal actions (absorbed from standalone tools)
    if action == "journal_read":
        return journal_read(agent_id or "", section, max_entries)

    if action == "journal_write":
        return journal_write(
            agent_id or "", entry_type, text, reasoning, risk_note, tick, category
        )

    # Journal/monitoring that's file-based
    if action in ("agent_tracker", "agent_journal"):
        return _agent_monitoring(action, agent_id)

    return {"error": f"Unknown action: {action}"}
