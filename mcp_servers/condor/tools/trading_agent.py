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
    from condor.agents.strategy import StrategyStore, split_key

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
        from condor.agents.agent import AgentStore

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
# Agent definitions (the AGENT.md identities — distinct from strategies/instances)
# ---------------------------------------------------------------------------


def _list_agent_definitions() -> dict:
    """List the Agent identities (agents/*/AGENT.md), with capabilities.

    An *agent* (e.g. ``executor_manager``, ``brigado``) is distinct from a
    *strategy* (a looping playbook it owns) and from a running *instance*. This
    surfaces consult-only agents and loopable agents that ``list_strategies`` /
    ``list_agents`` (instances) never show.
    """
    from condor.agents.agent import AgentStore
    from condor.agents.strategy import StrategyStore

    strat_names: dict[str, list[str]] = {}
    for s in StrategyStore().list_all():
        strat_names.setdefault(s.agent_slug, []).append(s.name)

    agents = []
    for a in AgentStore().list_all():
        owned = strat_names.get(a.slug, [])
        agents.append(
            {
                "slug": a.slug,
                "name": a.name,
                "description": a.description,
                "agent_key": a.agent_key,
                "consultable": a.consultable,
                "when_to_consult": a.when_to_consult,
                "loopable": bool(owned),
                "strategies": owned,
                "tools": a.tools,
            }
        )
    return {"agents": agents}


# ---------------------------------------------------------------------------
# Agent CRUD (the AGENT.md identity itself — the primary artifact)
#
# An Agent is the brain/identity. It is created FIRST; routines and strategies
# are sub-resources that hang off an existing agent_slug. Capability is derived:
# ``when_to_consult`` => consultable (on any model); ≥1 strategy => loopeable.
# A bare agent (no trigger, no strategy) is a stub.
# ---------------------------------------------------------------------------


def _manage_agent(
    action: str,
    agent_slug: str | None,
    name: str | None,
    description: str | None,
    instructions: str | None,
    agent_key: str | None,
    tools: list[str] | None,
    when_to_consult: str | None,
    server_required: bool | None,
) -> dict:
    from condor.agents.agent import AgentStore

    store = AgentStore()

    if action == "create_agent":
        if not name:
            return {"error": "name is required to create an agent"}
        agent = store.create(
            name=name,
            description=description or "",
            instructions=instructions or "",
            agent_key=agent_key or "",
            tools=tools,
            when_to_consult=when_to_consult or "",
            server_required=True if server_required is None else server_required,
            created_by=settings.user_id,
        )
        return {
            "created": True,
            "agent_slug": agent.slug,
            "name": agent.name,
            "consultable": agent.consultable,
        }

    if action == "get_agent":
        if not agent_slug:
            return {"error": "agent_slug is required"}
        a = store.get(agent_slug)
        if not a:
            return {"error": f"Agent '{agent_slug}' not found"}
        return {
            "slug": a.slug,
            "name": a.name,
            "description": a.description,
            "instructions": a.instructions,
            "agent_key": a.agent_key,
            "tools": a.tools,
            "when_to_consult": a.when_to_consult,
            "server_required": a.server_required,
            "consultable": a.consultable,
            "created_by": a.created_by,
            "created_at": a.created_at,
        }

    if action == "update_agent":
        if not agent_slug:
            return {"error": "agent_slug is required"}
        a = store.get(agent_slug)
        if not a:
            return {"error": f"Agent '{agent_slug}' not found"}
        if name:
            a.name = name
        if description is not None:
            a.description = description
        if instructions is not None:
            a.instructions = instructions
        if agent_key is not None:
            a.agent_key = agent_key
        if tools is not None:
            a.tools = tools
        if when_to_consult is not None:
            a.when_to_consult = when_to_consult
        if server_required is not None:
            a.server_required = server_required
        store.update(a)
        return {"updated": True, "agent_slug": a.slug, "consultable": a.consultable}

    if action == "delete_agent":
        if not agent_slug:
            return {"error": "agent_slug is required"}
        from condor.agents.strategy import StrategyStore

        owned = StrategyStore().list(agent_slug)
        if owned:
            return {
                "error": (
                    f"Agent '{agent_slug}' still owns {len(owned)} strategy(ies). "
                    "Delete its strategies first."
                )
            }
        return {"deleted": store.delete(agent_slug)}

    return {"error": f"Unknown agent action: {action}"}


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

            from condor.agents.strategy import StrategyStore

            store = StrategyStore()
            strategy = store.get_by_key(strategy_id)
            if not strategy:
                return {"error": f"Strategy '{strategy_id}' not found"}

            from condor.agents.config import load_full_config
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

        if action in ("stop_agent", "pause_agent", "resume_agent", "shutdown_agent"):
            if not agent_id:
                return {"error": "agent_id is required"}
            # shutdown_agent escalates beyond the position-preserving stop: it winds
            # down this session's positions/executors per its shutdown.md policy.
            verb = {
                "stop_agent": "stop",
                "pause_agent": "pause",
                "resume_agent": "resume",
                "shutdown_agent": "shutdown",
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
    from condor.agents.engine import get_engine
    from condor.agents.journal import JournalManager

    engine = get_engine(agent_id)
    if engine:
        if engine.is_experiment:
            return None, {
                "content": "(experiment mode — no journal, results saved to dry_runs/)"
            }
        session_dir = engine.session_dir
        agent_dir = engine.strategy.dir
    else:
        from condor.agents.journal import resolve_agent_dirs

        session_dir, agent_dir = resolve_agent_dirs(agent_id)
    if not session_dir:
        return None, {"content": "(no journal available for this agent)"}
    return JournalManager(agent_id, session_dir=session_dir, agent_dir=agent_dir), None


def _resolve_experiment_file(agent_id: str):
    """For an experiment agent_id ("..._eN"), locate its saved snapshot.

    Experiments (dry_run / run_once) keep no journal — the tick is saved as a
    flat ``dry_runs/experiment_N.md`` (legacy: ``experiments/``). Returns
    (path | None, num | None); num is set even when the file isn't on disk yet
    so callers can distinguish "experiment in progress" from "not an experiment".
    """
    from condor.agents.journal import resolve_agent_dirs

    last_sep = agent_id.rfind("_")
    if last_sep == -1:
        return None, None
    num_part = agent_id[last_sep + 1 :]
    if not num_part.startswith("e"):
        return None, None
    try:
        num = int(num_part[1:])
    except ValueError:
        return None, None

    _, base_dir = resolve_agent_dirs(agent_id)
    if base_dir is None:
        return None, num
    for dirname in ("dry_runs", "experiments"):
        path = base_dir / dirname / f"experiment_{num}.md"
        if path.exists():
            return path, num
    return None, num


def journal_read(agent_id: str, section: str = "recent", max_entries: int = 30) -> dict:
    if not agent_id:
        return {"error": "agent_id is required"}

    # Experiments (dry_run / run_once) have no journal — surface the saved
    # dry-run snapshot instead of the misleading "no journal available" error.
    exp_path, exp_num = _resolve_experiment_file(agent_id)
    if exp_num is not None:
        if exp_path is None:
            return {
                "content": f"(experiment #{exp_num} — no saved snapshot yet; "
                "the run may still be in progress)"
            }
        content = exp_path.read_text()
        if section == "runs":
            return {"runs": [{"experiment": exp_num, "file": exp_path.name}]}
        return {"content": content}

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

    from condor.agents.engine import get_engine
    from condor.agents.journal import JournalManager

    engine = get_engine(agent_id)
    if engine:
        if engine.is_experiment:
            # Experiments (dry_run / run_once) keep no journal — the whole tick is
            # captured in the dry-run snapshot. Treat a stray write as a benign
            # skip so it never derails the (possibly live) run_once tick.
            return {
                "skipped": "experiment mode — no journal; the tick is saved as a dry-run snapshot"
            }
        session_dir = engine.session_dir
        agent_dir = engine.strategy.dir
    else:
        from condor.agents.journal import resolve_agent_dirs

        session_dir, agent_dir = resolve_agent_dirs(agent_id)
        # resolve_agent_dirs returns (None, base_dir) for an experiment id ("..._eN")
        # but (None, None) for a genuinely unknown agent. Skip benignly for the
        # former, error for the latter.
        if session_dir is None and agent_dir is not None:
            return {
                "skipped": "experiment mode — no journal; the tick is saved as a dry-run snapshot"
            }
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
    # Agent-definition params (for create_agent/update_agent actions)
    tools: list[str] | None = None,
    when_to_consult: str | None = None,
    server_required: bool | None = None,
) -> dict:
    # Agent definitions (identities) — distinct from strategies and instances
    if action == "list_agent_definitions":
        return _list_agent_definitions()

    # Agent CRUD — the AGENT.md identity itself (created before routines/strategies)
    agent_def_actions = {
        "create_agent",
        "get_agent",
        "update_agent",
        "delete_agent",
    }
    if action in agent_def_actions:
        return _manage_agent(
            action,
            agent_slug,
            name,
            description,
            instructions,
            agent_key,
            tools,
            when_to_consult,
            server_required,
        )

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
        "shutdown_agent",
        "list_agents",
    }
    if action in lifecycle_actions:
        return await _agent_lifecycle(action, strategy_id, agent_id, config)

    # Journal reads/writes are the standalone trading_agent_journal_read /
    # trading_agent_journal_write tools — the canonical interface used by live
    # tick prompts. They are intentionally NOT duplicated as actions here.

    # Journal/monitoring that's file-based
    if action in ("agent_tracker", "agent_journal"):
        return _agent_monitoring(action, agent_id)

    return {"error": f"Unknown action: {action}"}
