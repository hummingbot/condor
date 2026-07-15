"""Trading agent CRUD, lifecycle, monitoring, and journal.

Since the Agent + Strategy collapse (simplification plan §5.3) the AGENT.md is
the ONE spec — identity + strategy body + ``default_config`` + ``denomination``
+ optional ``schedule``. Agent CRUD and lifecycle go through the main process's
control socket so ``AgentService`` owns every guard (tombstone semantics,
running-engine checks, spec validation) exactly once (§5.2). Journal and
monitoring tools read the shared filesystem directly.
"""

from mcp_servers.condor.condor_client import call_control, slug_from_agent_id
from mcp_servers.condor.exceptions import APIError
from mcp_servers.condor.settings import settings

# ---------------------------------------------------------------------------
# Agent definitions (the AGENT.md identities — distinct from running instances)
# ---------------------------------------------------------------------------


async def _list_agent_definitions() -> dict:
    """List the Agent identities (agents/*/AGENT.md), with capabilities.

    An *agent* (e.g. ``executor_manager``, ``brigado``) is distinct from a
    running *instance*. This surfaces consult-only agents that ``list_agents``
    (live instances) never shows.
    """
    result = await call_control("agent.definitions")
    agents = result.get("agents", []) if isinstance(result, dict) else []
    return {
        "agents": [
            {
                "slug": a.get("slug"),
                "name": a.get("name"),
                "description": a.get("description"),
                "agent_key": a.get("agent_key"),
                "consultable": a.get("consultable"),
                "when_to_consult": a.get("when_to_consult"),
                "can_trade": a.get("can_trade"),
                "denomination": a.get("denomination"),
                "schedule": a.get("schedule"),
                "tools": a.get("tools"),
            }
            for a in agents
        ]
    }


# ---------------------------------------------------------------------------
# Agent CRUD (the AGENT.md identity itself — the ONE spec, §5.3)
#
# Every mutation goes over the control socket: the main-process AgentService
# owns the guards (reserved tombstoned slugs, running-engine/nonterminal-
# executor checks on delete, risk_limits/denomination validation on save).
# ---------------------------------------------------------------------------


async def _manage_agent(
    action: str,
    agent_slug: str | None,
    name: str | None,
    description: str | None,
    instructions: str | None,
    agent_key: str | None,
    tools: list[str] | None,
    when_to_consult: str | None,
    server_required: bool | None,
    server_name: str | None,
    risk_limits: dict | None,
    denomination: str | None,
    default_config: dict | None,
    default_trading_context: str | None,
    schedule: dict | None,
) -> dict:
    if action == "create_agent":
        if not name:
            return {"error": "name is required to create an agent"}
        result = await call_control(
            "agent.create",
            {
                "name": name,
                "description": description or "",
                "instructions": instructions or "",
                "agent_key": agent_key or "",
                "tools": tools or [],
                "when_to_consult": when_to_consult or "",
                "server_required": True if server_required is None else server_required,
                "server_name": server_name or "",
                "risk_limits": risk_limits or {},
                "denomination": denomination or "",
                "default_config": default_config or {},
                "default_trading_context": default_trading_context or "",
                "schedule": schedule or {},
            },
        )
        agent = result.get("agent", {}) if isinstance(result, dict) else {}
        return {
            "created": True,
            "agent_slug": agent.get("slug"),
            "name": agent.get("name"),
            "consultable": agent.get("consultable"),
        }

    if not agent_slug:
        return {"error": "agent_slug is required"}

    if action == "get_agent":
        result = await call_control("agent.get", {"slug": agent_slug})
        return result.get("agent", result) if isinstance(result, dict) else result

    if action == "update_agent":
        patch = {
            k: v
            for k, v in {
                "name": name,
                "description": description,
                "instructions": instructions,
                "agent_key": agent_key,
                "tools": tools,
                "when_to_consult": when_to_consult,
                "server_required": server_required,
                "server_name": server_name,
                "risk_limits": risk_limits,
                "denomination": denomination,
                "default_config": default_config,
                "default_trading_context": default_trading_context,
                "schedule": schedule,
            }.items()
            if v is not None
        }
        if not patch:
            return {"error": "no fields to update"}
        result = await call_control(
            "agent.update", {"slug": agent_slug, "patch": patch}
        )
        agent = result.get("agent", {}) if isinstance(result, dict) else {}
        return {
            "updated": True,
            "agent_slug": agent.get("slug"),
            "consultable": agent.get("consultable"),
        }

    if action == "delete_agent":
        # Tombstone, not erase (§5.2): the service rejects while engines run or
        # nonterminal executors remain; history stays readable, slug reserved.
        return await call_control("agent.delete", {"slug": agent_slug})

    return {"error": f"Unknown agent action: {action}"}


# ---------------------------------------------------------------------------
# Agent-scoped routine listing
# ---------------------------------------------------------------------------


def _agent_list_routines(agent_slug: str) -> dict:
    """List global + agent-local routines for an agent, with scope labels."""
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

    routines_dir = _get_agent_routines_dir(agent_slug)
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
# Agent lifecycle (delegates to main process via the control socket)
# ---------------------------------------------------------------------------


async def _agent_lifecycle(
    action: str,
    agent_slug: str | None,
    agent_id: str | None,
    config: dict | None,
) -> dict:
    try:
        if action == "list_agents":
            result = await call_control("agent.list")
            agents = result.get("agents", []) if isinstance(result, dict) else []
            if not agents:
                return {"agents": [], "message": "No agents running"}
            return {"agents": agents}

        if action in ("start_session", "start_experiment"):
            if not agent_slug:
                return {"error": "agent_slug is required"}

            # Existence check + the agent's pinned server. Launch defaults and
            # risk merging are owned by the main-process service (§5.3) — we
            # only send the caller's overrides.
            result = await call_control("agent.get", {"slug": agent_slug})
            owner = result.get("agent", {}) if isinstance(result, dict) else {}

            config_dict = dict(config or {})
            if config_dict.get("dry_run") and "execution_mode" not in config_dict:
                config_dict["execution_mode"] = "experiment"
            if action == "start_experiment":
                # The experiment verb IS the mode — no config knob to get wrong.
                config_dict["execution_mode"] = "experiment"
            if "server_name" not in config_dict:
                from config_manager import get_config_manager, get_effective_server

                # A server pinned on the Agent wins over the ambient chat
                # server, mirroring consult/delegate resolution.
                effective = (
                    owner.get("server_name")
                    or settings.active_server
                    or get_effective_server(settings.chat_id)
                )
                if not effective:
                    cm = get_config_manager()
                    accessible = cm.get_accessible_servers(settings.user_id)
                    effective = accessible[0] if accessible else None
                if effective:
                    config_dict["server_name"] = effective

            trading_context = config_dict.pop("trading_context", "")

            return await call_control(
                "agent.start",
                {
                    "slug": agent_slug,
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
            slug = slug_from_agent_id(agent_id)
            return await call_control(
                "agent.verb",
                {"slug": slug, "verb": verb, "agent_id": agent_id},
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
                "content": "(experiment mode — no journal, results saved to experiments/)"
            }
        session_dir = engine.session_dir
        agent_dir = engine.agent.agent_dir
    else:
        from condor.agents.journal import resolve_agent_dirs

        session_dir, agent_dir = resolve_agent_dirs(agent_id)
    if not session_dir:
        return None, {"content": "(no journal available for this agent)"}
    return JournalManager(agent_id, session_dir=session_dir, agent_dir=agent_dir), None


def _resolve_experiment_file(agent_id: str):
    """For an experiment agent_id ("{slug}_eN"), locate its saved snapshot.

    Experiments keep no journal — the tick is saved as a flat
    ``experiments/{date}-eN.md``. Returns (path | None, num | None); num is
    set even when the file isn't on disk yet so callers can distinguish
    "experiment in progress" from "not an experiment".
    """
    from condor.agents.journal import resolve_agent_dirs, split_agent_id
    from condor.agents.sessions_index import find_experiment_file

    parsed = split_agent_id(agent_id)
    if parsed is None or not parsed[2]:
        return None, None
    num = parsed[1]

    _, agent_dir = resolve_agent_dirs(agent_id)
    if agent_dir is None:
        return None, num
    return find_experiment_file(agent_dir, num), num


def journal_read(agent_id: str, section: str = "recent", max_entries: int = 30) -> dict:
    if not agent_id:
        return {"error": "agent_id is required"}

    # Experiments have no journal — surface the saved experiment
    # snapshot instead of the misleading "no journal available" error.
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
        runs = jm.list_snapshots(limit=max_entries)
        return {"runs": runs}
    elif section.startswith("run:"):
        try:
            tick_num = int(section.split(":", 1)[1])
        except (ValueError, IndexError):
            return {
                "error": "Invalid run format. Use 'run:N' where N is the tick number."
            }
        content = jm.read_snapshot(tick_num)
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

    if entry_type == "promote_learning":
        # Learnings live at the AGENT level, so promotion takes the bare agent
        # slug — no session handle required (a session id also resolves, for
        # convenience). Moves the matched line to the Promoted section after
        # it was folded into a skill (frees the active-pool cap, keeps the
        # record).
        from condor.agents.agent import AgentStore
        from condor.agents.journal import promote_agent_learning, split_agent_id

        parsed = split_agent_id(agent_id)
        slug = parsed[0] if parsed else agent_id
        target = AgentStore().get(slug)
        if target is None:
            return {"error": f"unknown agent '{slug}'"}
        ok = promote_agent_learning(target.agent_dir, text)
        return {"promoted": ok} if ok else {
            "error": "no active learning matched that text — copy it exactly "
            "from the [LEARNINGS] block"
        }

    from condor.agents.engine import get_engine
    from condor.agents.journal import JournalManager

    engine = get_engine(agent_id)
    if engine:
        if engine.is_experiment:
            # Experiments keep no journal — the whole tick is captured in
            # the experiment snapshot. Treat a stray write as a benign
            # skip so it never derails the (possibly live) run_once tick.
            return {
                "skipped": "experiment mode — no journal; the tick is saved as an experiment snapshot"
            }
        session_dir = engine.session_dir
        agent_dir = engine.agent.agent_dir
    else:
        from condor.agents.journal import resolve_agent_dirs

        session_dir, agent_dir = resolve_agent_dirs(agent_id)
        # resolve_agent_dirs returns (None, agent_dir) for an experiment id
        # ("{slug}_eN") but (None, None) for a genuinely unknown agent. Skip
        # benignly for the former, error for the latter.
        if session_dir is None and agent_dir is not None:
            return {
                "skipped": "experiment mode — no journal; the tick is saved as an experiment snapshot"
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
                "error": "experiments don't have a journal — use experiments/ for results"
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
    agent_slug: str | None = None,
    name: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    agent_key: str | None = None,
    config: dict | None = None,
    # Agent-definition params (for create_agent/update_agent actions)
    tools: list[str] | None = None,
    when_to_consult: str | None = None,
    server_required: bool | None = None,
    server_name: str | None = None,
    risk_limits: dict | None = None,
    denomination: str | None = None,
    default_config: dict | None = None,
    default_trading_context: str | None = None,
    schedule: dict | None = None,
) -> dict:
    # Agent definitions (identities) — distinct from running instances
    if action == "list_agent_definitions":
        try:
            return await _list_agent_definitions()
        except APIError as e:
            return {"error": str(e)}

    # Agent CRUD — the AGENT.md identity, the ONE spec (§5.3)
    agent_def_actions = {
        "create_agent",
        "get_agent",
        "update_agent",
        "delete_agent",
    }
    if action in agent_def_actions:
        try:
            return await _manage_agent(
                action,
                agent_slug,
                name,
                description,
                instructions,
                agent_key,
                tools,
                when_to_consult,
                server_required,
                server_name,
                risk_limits,
                denomination,
                default_config,
                default_trading_context,
                schedule,
            )
        except APIError as e:
            return {"error": str(e)}

    # Routine actions scoped to an agent
    if action == "list_routines":
        if not agent_slug:
            return {"error": "agent_slug is required"}
        return _agent_list_routines(agent_slug)

    if action == "run_routine":
        if not agent_slug:
            return {"error": "agent_slug is required"}
        if not name:
            return {"error": "name is required"}
        from mcp_servers.condor.tools.routines import run_routine

        return await run_routine(name, config, agent_slug)

    # Agent lifecycle actions
    lifecycle_actions = {
        "start_session",
        "start_experiment",
        "stop_agent",
        "pause_agent",
        "resume_agent",
        "shutdown_agent",
        "list_agents",
    }
    if action in lifecycle_actions:
        return await _agent_lifecycle(action, agent_slug, agent_id, config)

    # Journal reads/writes are the standalone trading_agent_journal_read /
    # trading_agent_journal_write tools — the canonical interface used by live
    # tick prompts. They are intentionally NOT duplicated as actions here.

    # Journal/monitoring that's file-based
    if action in ("agent_tracker", "agent_journal"):
        return _agent_monitoring(action, agent_id)

    return {"error": f"Unknown action: {action}"}
