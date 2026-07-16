"""Trading agent CRUD, lifecycle, monitoring, and journal.

Since the Agent + Strategy collapse (simplification plan §5.3) the AGENT.md is
the ONE spec — identity + strategy body + ``default_config`` + ``denomination``
+ optional ``schedule``. Agent CRUD and lifecycle go through the main process's
control socket so ``AgentService`` owns every guard (tombstone semantics,
running-engine checks, spec validation) exactly once (§5.2). Journal and
monitoring tools read the shared filesystem directly.
"""

from mcp_servers.condor.condor_client import call_control
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
            # Run ids are opaque (§7.1): the handler selects the engine by
            # agent_id; the slug only matters for slug-wide verbs without one.
            return await call_control(
                "agent.verb",
                {"slug": settings.agent_slug or "", "verb": verb, "agent_id": agent_id},
            )

        return {"error": f"Unknown lifecycle action: {action}"}
    except APIError as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Journal read/write — projections over the RunStore stream (§7.1)
#
# Reads fold the run's event stream directly (read-only file access); writes
# go over the control socket ("run.emit") — the main process owns the one
# serialized writer per run. Learnings are agent-level curated memory
# (agents/{slug}/learnings.md), written via the flock'd learnings module.
# ---------------------------------------------------------------------------


def _run_events(agent_id: str):
    """(slug, events) for a run id, or (None, None) when unknown."""
    from condor.agents.runstore import get_run_store

    store = get_run_store()
    path = store.find_run_path(agent_id)
    if path is None:
        return None, None
    slug = path.parent.parent.name
    return slug, store.read_events(slug, agent_id)


def _agent_dir_for(agent_ref: str):
    """Agent dir for a slug (or a run id, resolved to its slug)."""
    from condor.agents.agent import AgentStore

    agent = AgentStore().get(agent_ref)
    if agent is not None:
        return agent.agent_dir
    slug, _ = _run_events(agent_ref)
    if slug is None:
        return None
    agent = AgentStore().get(slug)
    return agent.agent_dir if agent is not None else None


def journal_read(agent_id: str, section: str = "recent", max_entries: int = 30) -> dict:
    if not agent_id:
        return {"error": "agent_id is required"}

    slug, events = _run_events(agent_id)
    if events is None:
        return {"error": f"unknown run '{agent_id}'"}

    from condor.agents.learnings import read_learnings
    from condor.agents.projections import run_projection

    if section == "learnings":
        agent_dir = _agent_dir_for(slug)
        return {"content": read_learnings(agent_dir) if agent_dir else ""}

    proj = run_projection(events)
    if section in ("state", "summary"):
        return {"content": proj["state"] or "(no state recorded yet)"}
    if section == "full":
        from condor.agents.exports import render_run_markdown
        from condor.agents.runstore import get_run_store

        meta = get_run_store().run_meta(slug, agent_id)
        return {"content": render_run_markdown(meta, events)}
    if section == "runs":
        from condor.agents.runstore import get_run_store

        return {"runs": get_run_store().list_runs(slug, limit=max_entries)}
    # default: recent decisions + last state
    parts = []
    if proj["recent_decisions"]:
        parts.append(proj["recent_decisions"])
    if proj["state"]:
        parts.append(f"State: {proj['state']}")
    return {"content": "\n".join(parts) or "(no entries yet)"}


async def journal_write(
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

    if entry_type == "learning":
        # Agent-level curated memory — direct flock'd file append (the
        # single-writer rule covers RUN streams; learnings are agent memory,
        # same trust boundary as the memory tools).
        from condor.agents.learnings import append_learning

        agent_dir = _agent_dir_for(settings.agent_slug or agent_id)
        if agent_dir is None:
            return {"error": f"unknown agent for '{agent_id}'"}
        append_learning(agent_dir, text)
        return {"written": True}

    if entry_type == "promote_learning":
        return {
            "error": "promote_learning was removed (§7.1) — learnings are a "
            "flat curated list now; just append the distilled form"
        }

    # state / decision entries → run events over the control socket (the
    # main process owns the one serialized writer per run).
    payload = {"state": text} if entry_type == "state" else {
        "decision": text if not reasoning else f"{text} — {reasoning}"
    }
    if risk_note:
        payload["risk_note"] = risk_note
    try:
        await call_control(
            "run.emit",
            {
                "run_id": agent_id,
                "type": "state_snapshot",
                "payload": payload,
                "tick": tick or None,
            },
        )
    except APIError as e:
        return {"error": str(e)}
    return {"written": True}


# ---------------------------------------------------------------------------
# Agent monitoring (projections over the same stream)
# ---------------------------------------------------------------------------


def _agent_monitoring(action: str, agent_id: str | None) -> dict:
    if not agent_id:
        return {"error": "agent_id is required"}

    slug, events = _run_events(agent_id)
    if events is None:
        return {"error": f"unknown run '{agent_id}'"}

    from condor.agents.learnings import read_learnings
    from condor.agents.projections import run_projection

    proj = run_projection(events)

    if action == "agent_tracker":
        from condor.agents.exports import render_run_markdown
        from condor.agents.runstore import get_run_store

        meta = get_run_store().run_meta(slug, agent_id)
        summary = {
            "total_ticks": proj["tick_count"],
            "tool_calls": proj["tool_calls"],
            "status": meta.get("status"),
            "kind": meta.get("kind"),
        }
        if proj["metrics_series"]:
            summary.update(proj["metrics_series"][-1])
        return {"tracker_md": render_run_markdown(meta, events), "summary": summary}

    if action == "agent_journal":
        agent_dir = _agent_dir_for(slug)
        return {
            "recent_actions": proj["recent_decisions"],
            "learnings": read_learnings(agent_dir) if agent_dir else "",
            "entry_count": proj["tick_count"],
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
