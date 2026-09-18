"""Agent CRUD, strategy CRUD, instance lifecycle, and the journal."""

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
        try:
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
        except ValueError as exc:  # taken or reserved name (CORR-635)
            return {"error": str(exc)}
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
    """List the Agent identities (``*/AGENT.md`` across both agent roots).

    An *agent* (e.g. ``executor_manager``, ``brigado``) is distinct from a
    *strategy* (a looping playbook it owns) and from a running *instance*. This
    surfaces agents that ``list_strategies`` / ``list_agents`` (instances) never
    show. No capability flags: every agent listed here can be delegated to,
    delegated to and looped.
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
                "when_to_consult": a.consult_hint,
                "strategies": owned,
                "tools": a.tools,
            }
        )
    return {"agents": agents}


# ---------------------------------------------------------------------------
# Agent CRUD (the AGENT.md identity itself — the primary artifact)
#
# An Agent is the brain/identity. It is created FIRST; routines and strategies
# are sub-resources that hang off an existing agent_slug. There are no capability
# flags: the moment an agent exists it can be delegated to and looped.
# ``when_to_consult`` is a routing hint; a bespoke strategy is an optimization
# over the default playbook every agent already loops.
# ---------------------------------------------------------------------------


def _creator_agent_key() -> str:
    """The model the calling user is currently running, or "" if unknown.

    Mirrored into config.yml by handlers.agents.set_active_llm, since the live
    value lives in the bot's PTB pickle which this subprocess can't read.
    """
    try:
        from condor.preferences import get_active_agent_key

        return get_active_agent_key(settings.user_id) or ""
    except Exception:
        return ""


def _publish_agent(agent_slug: str, path: str) -> dict:
    """Copy a local agent into the shipped library. Admin only.

    Every other write in the product lands in the local root, which means the
    maintainer editing an agent through the product has no way to author what
    the repo ships. This is that way — and the single sanctioned write into the
    tracked tree, so it is gated on the one role that owns the checkout.

    There is no matching "install": a shipped agent is present the moment the
    install pulls. Publishing is a copy plus a commit; anything more would be a
    distribution system nobody asked for.
    """
    from condor.layering import publish_to_stock

    try:
        from config_manager import get_config_manager

        is_admin = get_config_manager().is_admin(settings.user_id)
    except Exception:  # noqa: BLE001 - no config is not a licence to publish
        is_admin = False
    if not is_admin:
        return {
            "error": (
                "Publishing writes into the tracked library the repo ships, so "
                "it is admin-only. Your edit is already saved locally and is "
                "what this install reads."
            )
        }

    return publish_to_stock(agent_slug, path)


def _refuse_unreachable_pin(new: str | None, stored: str) -> dict | None:
    """The MCP mirror of the web layer's ``_gate_pin_change`` (SEC-698).

    An Agent's server pin decides which account its tools trade on, so every
    writer of that field answers the same way: ``POST /agents`` and ``PATCH
    /config`` gate it (SEC-594), a raw ``AGENT.md`` write gates it (SEC-693),
    and this tool — the third door onto the same field, and the one the web
    routes' own docstring names as performing "the same write" — did not.

    Credentials never actually leaked, because every site that turns a stored
    name into a client re-checks reach (``config_manager.may_use_stored_server``
    at each of its five call sites). What leaked was the *label*: an Agent
    pinned over MCP to a server the caller cannot reach reports that name
    verbatim in ``AgentSummary`` and in the chat header's
    ``SessionBinding.server_name``, so the UI names a foreign account as the one
    at risk — exactly the mislabel SEC-594 and SEC-693 were shipped to prevent.

    The predicate is ``may_use_stored_server`` rather than the web layer's
    ``check_server_access``: the two apply the same TRADER floor to the same
    ``has_server_access``, but the web one lives in ``condor/web/auth.py`` and
    signals by raising ``HTTPException``, which this layer cannot import (it
    sits below the web app) and could not answer with anyway — an MCP tool
    reports refusal as ``{"error": ...}``, the shape ``manage_servers`` already
    uses for this exact sentence. ``may_use_stored_server`` is the boolean
    sibling that ARCH-587 gave the callers below the web layer, and it also
    checks existence, so an admin — for whom ``has_server_access`` answers True
    on any string at all (SEC-164) — cannot pin an Agent to a name that
    resolves to no server.

    An empty pin needs no access, and re-sending the value already stored is
    the ordinary round-trip of a pin someone else legitimately set, so only a
    *change* is checked — the same two exemptions ``_gate_pin_change`` makes.
    """
    if not new or new == stored:
        return None

    from config_manager import get_config_manager, may_use_stored_server

    if not may_use_stored_server(get_config_manager(), settings.user_id, new):
        return {"error": f"No access to server '{new}'"}
    return None


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
    server_name: str | None,
    path: str | None = None,
) -> dict:
    from condor.agents.agent import AgentStore

    store = AgentStore()

    if action == "create_agent":
        if not name:
            return {"error": "name is required to create an agent"}
        # Creating an Agent already pinned to an unreachable server is the edit
        # that is refused below, so it is refused here too.
        refusal = _refuse_unreachable_pin(server_name, "")
        if refusal:
            return refusal
        # Default to the model the creator is actually running. Guessing here
        # produces agents pinned to a backend the user never configured — the
        # coordinator has no way to know which models are reachable, so an
        # invented agent_key is a coin flip that only surfaces on the first run.
        resolved_key = agent_key or _creator_agent_key()
        try:
            agent = store.create(
                name=name,
                description=description or "",
                instructions=instructions or "",
                agent_key=resolved_key,
                tools=tools,
                when_to_consult=when_to_consult or "",
                server_required=True if server_required is None else server_required,
                server_name=server_name or "",
                created_by=settings.user_id,
            )
        except ValueError as exc:  # taken or reserved name (CORR-635)
            return {"error": str(exc)}
        return {
            "created": True,
            "agent_slug": agent.slug,
            "name": agent.name,
            "agent_key": agent.agent_key,
            "agent_key_inherited": not agent_key and bool(resolved_key),
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
            "server_name": a.server_name,
            "created_by": a.created_by,
            "created_at": a.created_at,
        }

    if action == "update_agent":
        if not agent_slug:
            return {"error": "agent_slug is required"}
        a = store.get(agent_slug)
        if not a:
            return {"error": f"Agent '{agent_slug}' not found"}
        # Ahead of every assignment, so a refused pin leaves the record whole
        # rather than persisting the fields that happened to be listed first.
        refusal = _refuse_unreachable_pin(server_name, a.server_name)
        if refusal:
            return refusal
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
        if server_name is not None:
            a.server_name = server_name
        store.update(a)
        return {"updated": True, "agent_slug": a.slug}

    if action == "publish_agent":
        if not agent_slug:
            return {"error": "agent_slug is required"}
        return _publish_agent(agent_slug, path or "")

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
            # Accepts a composite key OR a bare agent slug: every agent is
            # loopable, so a slug resolves to its only strategy or to a default
            # playbook materialized from its identity on first start.
            strategy = store.resolve_for_loop(strategy_id)
            if not strategy:
                return {"error": f"No strategy or agent matches '{strategy_id}'"}

            from condor.agents.config import load_full_config
            from config_manager import get_config_manager, get_effective_server

            config_dict = load_full_config(strategy.home, strategy.default_config)
            if config:
                if config.get("dry_run") and "execution_mode" not in config:
                    config["execution_mode"] = "dry_run"
                config_dict.update(config)
            if not config or "server_name" not in config:
                # A server pinned on the owning Agent wins over the ambient chat
                # server, mirroring delegate's resolution.
                from condor.agents.agent import AgentStore

                owner = AgentStore().get(strategy.agent_slug)
                effective = (
                    (owner.server_name if owner else "")
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

            return await call_main_api(
                "POST",
                f"/agents/{strategy.agent_slug}/strategies/{strategy.slug}/start",
                {
                    "config": config_dict,
                    "trading_context": trading_context,
                    "chat_id": settings.chat_id,
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
        agent_dir = engine.strategy.home
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
    from condor.agents.sessions_index import (
        find_experiment_file,
        parse_agent_id,
        strategy_dir_for_run_key,
    )

    parsed = parse_agent_id(agent_id)
    if parsed is None or parsed[2] != "experiment":
        return None, None
    run_key, num, _ = parsed
    base_dir = strategy_dir_for_run_key(run_key)
    if base_dir is None or not base_dir.is_dir():
        return None, num
    return find_experiment_file(base_dir, num), num


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

    if section == "tracker":
        return {"tracker_md": jm.read_full(), "summary": jm.get_summary_dict()}
    elif section == "full":
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


def _may_feed_run(engine) -> bool:
    """Whether this seat may write what ``engine`` reads back each tick (SEC-638).

    The tick's own MCP subprocess runs as the engine's owner, so the loop's
    writes pass; a foreign chat session's agent is refused unless its user is
    an admin. An unowned restored loop (``user_id == 0``) is left as it was.
    """
    owner = getattr(engine, "user_id", 0) or 0
    if not owner or owner == settings.user_id:
        return True
    try:
        from config_manager import get_config_manager

        return bool(get_config_manager().is_admin(settings.user_id))
    except Exception:  # noqa: BLE001 - no config is not a licence to write
        return False


def journal_write(
    agent_id: str,
    entry_type: str,
    text: str,
    reasoning: str = "",
    risk_note: str = "",
    tick: int = 0,
    category: str = "",
    section: str = "",
) -> dict:
    if not agent_id:
        return {"error": "agent_id is required"}
    if not text:
        return {"error": "text is required"}
    # An action and a canvas revision are both *dated* by their tick, and the
    # default of 0 is not a date. Written through, it reached the journal as
    # "**#0**" -- which the Decisions panel renders as ERR, so a perfectly good
    # decision looked like a failed one and the visible tick sequence broke --
    # and it disabled the canvas's per-tick revision cap, which is written
    # `if tick and ...`. A retry that drops the argument is exactly how this
    # happens, so it is refused here where the caller can still pass the tick.
    if entry_type in ("action", "canvas") and tick < 1:
        return {
            "error": f"tick must be the current tick number (>= 1) for a "
            f"{entry_type} entry, got {tick}"
        }

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
        # Every entry type is read back by the loop: learnings, state and the
        # canvas directly, actions as the prompt's recent decisions.
        if not _may_feed_run(engine):
            return {"error": "Not your agent"}
        session_dir = engine.session_dir
        agent_dir = engine.strategy.home
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

    if entry_type == "canvas":
        # The canvas (FEAT-036) rides on this tool rather than its own so it
        # inherits the engine resolution and the experiment-mode skip above.
        from condor.agents import canvas

        return canvas.write_section(session_dir, section, text, tick=tick)

    jm = JournalManager(agent_id, session_dir=session_dir, agent_dir=agent_dir)

    if entry_type == "learning":
        jm.append_learning(text, category=category or "market")
    elif entry_type == "state":
        jm.write_state(text)
    else:
        jm.append_action(tick, text, reasoning, risk_note)
    return {"written": True}


async def _agent_state(
    action: str,
    agent_id: str | None,
    key: str | None,
    value,
    expires_in: int | None,
    clear: bool,
) -> dict:
    """Read or write this agent's scratch state through the main process.

    State is for cursors and counters the loop would otherwise re-derive every
    tick. Anything worth *remembering* belongs in memory (manage_memory).
    """
    if not agent_id:
        return {"error": "agent_id is required"}
    agent_slug, sslug = agent_strategy_from_agent_id(agent_id)
    path = f"/agents/{agent_slug}/strategies/{sslug}/state"

    if action == "get_state":
        result = await call_main_api("GET", path)
        state = result.get("state", {}) if isinstance(result, dict) else {}
        return {"state": state.get(key) if key else state}

    if not key:
        return {"error": "key (the state key) is required for set_state"}
    body = {"key": key, "value": value, "expires_in": expires_in, "clear": clear}
    return await call_main_api("POST", path, body)


# ---------------------------------------------------------------------------
# Tool entry points — one per family (FEAT-068)
#
# Three tools replace the old manage_trading_agent funnel, each fronting the
# family function that already existed underneath it. Actions are short and
# unprefixed ("create", "start"); the legacy prefixed names ("create_agent",
# "start_agent") still resolve inside their own family, and a name belonging to
# a SIBLING family answers with the call that would have worked instead of a
# bare "unknown action".
# ---------------------------------------------------------------------------

# action name -> (owning tool, the call that handles it)
_ACTION_OWNER: dict[str, tuple[str, str]] = {
    "list_agent_definitions": ("manage_agents", 'manage_agents(action="list")'),
    "create_agent": ("manage_agents", 'manage_agents(action="create", ...)'),
    "get_agent": ("manage_agents", 'manage_agents(action="get", agent_slug=...)'),
    "update_agent": ("manage_agents", 'manage_agents(action="update", agent_slug=...)'),
    "delete_agent": ("manage_agents", 'manage_agents(action="delete", agent_slug=...)'),
    "publish_agent": (
        "manage_agents",
        'manage_agents(action="publish", agent_slug=...)',
    ),
    "list_strategies": ("manage_strategies", 'manage_strategies(action="list")'),
    "get_strategy": (
        "manage_strategies",
        'manage_strategies(action="get", strategy_id=...)',
    ),
    "create_strategy": (
        "manage_strategies",
        'manage_strategies(action="create", agent_slug=..., name=..., instructions=...)',
    ),
    "update_strategy": (
        "manage_strategies",
        'manage_strategies(action="update", strategy_id=...)',
    ),
    "delete_strategy": (
        "manage_strategies",
        'manage_strategies(action="delete", strategy_id=...)',
    ),
    "list_agents": ("control_agent", 'control_agent(action="list")'),
    "start_agent": ("control_agent", 'control_agent(action="start", strategy_id=...)'),
    "stop_agent": ("control_agent", 'control_agent(action="stop", agent_id=...)'),
    "pause_agent": ("control_agent", 'control_agent(action="pause", agent_id=...)'),
    "resume_agent": ("control_agent", 'control_agent(action="resume", agent_id=...)'),
    "shutdown_agent": (
        "control_agent",
        'control_agent(action="shutdown", agent_id=...)',
    ),
    "get_state": ("control_agent", 'control_agent(action="get_state", agent_id=...)'),
    "set_state": (
        "control_agent",
        'control_agent(action="set_state", agent_id=..., key=...)',
    ),
    "agent_tracker": (
        "trading_agent_journal_read",
        'trading_agent_journal_read(agent_id=..., section="tracker")',
    ),
    "agent_journal": (
        "trading_agent_journal_read",
        'trading_agent_journal_read(agent_id=..., section="recent")',
    ),
}

_AGENT_ACTIONS = {
    "list": "list_agent_definitions",
    "create": "create_agent",
    "get": "get_agent",
    "update": "update_agent",
    "delete": "delete_agent",
    "publish": "publish_agent",
}

_STRATEGY_ACTIONS = {
    "list": "list_strategies",
    "get": "get_strategy",
    "create": "create_strategy",
    "update": "update_strategy",
    "delete": "delete_strategy",
}

_CONTROL_ACTIONS = {
    "list": "list_agents",
    "start": "start_agent",
    "stop": "stop_agent",
    "pause": "pause_agent",
    "resume": "resume_agent",
    "shutdown": "shutdown_agent",
    "get_state": "get_state",
    "set_state": "set_state",
}


def _resolve_action(
    tool: str, action: str, actions: dict[str, str]
) -> tuple[str | None, dict | None]:
    """Map a tool's short action to its internal name, or explain the misroute."""
    if action in actions:
        return actions[action], None
    owner, call = _ACTION_OWNER.get(action, ("", ""))
    if owner == tool:
        # Legacy prefixed name for an action this tool already owns.
        return action, None
    if owner:
        return None, {"error": f"'{action}' belongs to {owner} — call {call} instead."}
    return None, {
        "error": f"Unknown action '{action}'. {tool} actions: "
        + ", ".join(actions)
        + "."
    }


def manage_agents(
    action: str,
    agent_slug: str | None = None,
    name: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    agent_key: str | None = None,
    tools: list[str] | None = None,
    when_to_consult: str | None = None,
    server_required: bool | None = None,
    server_name: str | None = None,
    path: str | None = None,
) -> dict:
    resolved, err = _resolve_action("manage_agents", action, _AGENT_ACTIONS)
    if err:
        return err
    if resolved == "list_agent_definitions":
        return _list_agent_definitions()
    return _manage_agent(
        resolved,
        agent_slug,
        name,
        description,
        instructions,
        agent_key,
        tools,
        when_to_consult,
        server_required,
        server_name,
        path,
    )


def manage_strategies(
    action: str,
    strategy_id: str | None = None,
    agent_slug: str | None = None,
    name: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    agent_key: str | None = None,
    skills: list[str] | None = None,
    config: dict | None = None,
) -> dict:
    resolved, err = _resolve_action("manage_strategies", action, _STRATEGY_ACTIONS)
    if err:
        return err
    return _manage_strategy(
        resolved,
        strategy_id,
        agent_slug,
        name,
        description,
        instructions,
        agent_key,
        skills,
        config,
    )


async def control_agent(
    action: str,
    agent_id: str | None = None,
    strategy_id: str | None = None,
    config: dict | None = None,
    key: str | None = None,
    value=None,
    expires_in: int | None = None,
    clear: bool = False,
) -> dict:
    resolved, err = _resolve_action("control_agent", action, _CONTROL_ACTIONS)
    if err:
        return err
    if resolved in ("get_state", "set_state"):
        # The namespace is derived from agent_id, never taken from the caller,
        # so an agent cannot read another's cursors by guessing a key.
        return await _agent_state(resolved, agent_id, key, value, expires_in, clear)
    return await _agent_lifecycle(resolved, strategy_id, agent_id, config)
