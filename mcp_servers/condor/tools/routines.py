"""Routine discovery, execution, and CRUD operations.

Discovery and CRUD are local — this subprocess can read the same files the main
process does. Execution is not: a routine *run* is a live object (an instance in
``RoutineStore``, an asyncio task, post-run hooks) that must outlive the MCP
subprocess to show up in the dashboard and be stoppable from it. So run/start/
stop/list_instances delegate to the main process over ``call_main_api``, the
same crossing ``consult`` and ``delegate`` already make.

That crossing is also what makes a run survive the caller: the instance belongs
to the main process, not to the turn that asked for it. ``run`` waits on it for
convenience; ``run_async`` + ``get_instance`` hand the same run back as a token
to redeem later, which is the only workable shape for a routine that takes
longer than a conversation turn.
"""

import asyncio
import time
from pathlib import Path

from mcp_servers.condor.condor_client import call_main_api
from mcp_servers.condor.exceptions import APIError
from mcp_servers.condor.settings import settings

# Wall-clock budget for a delegated one-shot run, unchanged from when the tool
# executed the routine itself, and the granularity we poll the run with.
_RUN_BUDGET = 120.0
_POLL_INTERVAL = 1.0

_NO_SERVER = (
    "No active server is selected for this session, so the run cannot be "
    "registered. Ask the user to select a server (/servers in Telegram, or the "
    "server selector in the dashboard) and try again."
)


def _shared_root() -> Path:
    """``agents/_shared/routines`` — the library every assistant reads."""
    from condor.memory.paths import shared_routines_root

    return shared_routines_root()


def _get_agent_routines_dir(target: str | None, shared: bool = False) -> Path | None:
    """Resolve the routines directory to write to.

    Routines live at the **Agent** level (``agents/<slug>/routines``), shared
    across all of that agent's strategies — so ``target`` is an *agent* selector:
    a bare agent slug ("agent_slug") is the canonical form. A composite strategy
    key ("agent_slug.strategy_slug") is still accepted and resolves to its
    *owning agent's* dir (the strategy half is discarded — there is no
    per-strategy routines dir). Without a ``target``, the current assistant's own
    dir — the general library (root ``routines/``) for the chat, or the launched
    Agent's (``agents/<slug>/routines``, ``settings.agent_slug``).

    ``shared=True`` targets the published library every assistant reads
    (:func:`condor.memory.paths.shared_routines_root`), and is honored **only**
    for the chat authoring its own library — same rule and same one-line guard
    as ``manage_skill``'s ``shared``. For an agent it is ignored, so an agent's
    ``create_routine``/``edit_routine`` physically cannot reach the shared root.
    """
    from routines.base import assistant_routines_dir

    if shared and not settings.specialist_slug and not target:
        return _shared_root()

    if target:
        from condor.agents.agent import AgentStore

        if AgentStore().get(target):
            return assistant_routines_dir(target)

        # Legacy/convenience form: a composite strategy key. Routines live one
        # level up, so this only serves to name the owning agent.
        from condor.agents.strategy import StrategyStore

        s = StrategyStore().get_by_key(target)
        if s:
            return assistant_routines_dir(s.agent_slug)
        return None

    return assistant_routines_dir(settings.specialist_slug or None)


def _own_plus_shared(slug: str | None) -> dict:
    """An assistant's runnable routines: own library over the shared one.

    One call for both seats (FEAT-038): a domain expert/trading agent gets
    ``agents/<slug>/routines`` over ``agents/_shared/routines``, the chat gets
    the general library — which ``discover_routines`` already merges the shared
    root into. The chat re-scans on every call because it is the library's
    author and its edits must be visible immediately; an agent rides the mtime
    cache.
    """
    from routines.base import assistant_routines

    return assistant_routines(slug, force_reload=not slug)


def _resolve_routine(name: str):
    """Look up a routine in the current assistant's scope.

    A domain expert/trading agent (``settings.agent_slug`` set) resolves its own
    routines plus the shared library, its own shadowing a shared name. The chat
    ``condor`` resolves the general library (root ``routines/`` + shared).
    """
    return _own_plus_shared(settings.specialist_slug).get(name)


def list_routines(target: str | None = None) -> dict:
    from routines.base import discover_routines, discover_routines_from_path

    result = []

    # Agent/expert MCP: its own routines plus the shared library. Shared entries
    # are flagged so the agent knows which ones it may not edit — a local
    # routine of the same name shadows one instead.
    if settings.specialist_slug:
        for name, routine in sorted(_own_plus_shared(settings.specialist_slug).items()):
            entry = {
                "name": name,
                "description": routine.description,
                "type": "continuous" if routine.is_continuous else "one-shot",
            }
            if (routine.source or "").startswith("agent:"):
                entry["scope"] = "agent"
                entry["agent"] = settings.specialist_slug
            else:
                entry["scope"] = "shared"
            result.append(entry)
        return {"routines": result}

    # Chat condor: the general library (root routines/).
    for name, routine in sorted(discover_routines(force_reload=True).items()):
        result.append(
            {
                "name": name,
                "description": routine.description,
                "type": "continuous" if routine.is_continuous else "one-shot",
                "scope": "global",
            }
        )

    if target:
        agent_routines_dir = _get_agent_routines_dir(target)
        if agent_routines_dir and agent_routines_dir.exists():
            # Report the owning agent, not the caller's spelling: `target` may be
            # a composite strategy key, and the routines are the agent's.
            owner = _owner_of(target) or target
            for name, routine in sorted(
                discover_routines_from_path(agent_routines_dir).items()
            ):
                result.append(
                    {
                        "name": name,
                        "description": routine.description,
                        "type": "continuous" if routine.is_continuous else "one-shot",
                        "scope": "agent",
                        "agent": owner,
                    }
                )
    else:
        # Overview of every agent's routines.
        from condor.agents.agent import AgentStore

        for a in AgentStore().list_all():
            if not a.routines_dir.exists():
                continue
            for name, routine in sorted(
                discover_routines_from_path(a.routines_dir).items()
            ):
                result.append(
                    {
                        "name": name,
                        "description": routine.description,
                        "type": "continuous" if routine.is_continuous else "one-shot",
                        "scope": "agent",
                        "agent": a.slug,
                    }
                )

    return {"routines": result}


def describe_routine(name: str) -> dict:
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


def _owner_of(target: str) -> str | None:
    """The agent slug ``target`` names — itself, or the owner of a strategy key."""
    from condor.agents.agent import AgentStore
    from condor.agents.strategy import StrategyStore

    if AgentStore().get(target):
        return target
    # Legacy/convenience form: a composite strategy key names its owning agent.
    s = StrategyStore().get_by_key(target)
    if s:
        return s.agent_slug
    return None


def _resolve_with_owner(name: str, target: str | None):
    """Resolve a routine plus the assistant its reports belong to.

    Attribution follows the run context, not the routine file: an explicit
    target resolves to its owning agent (the bare slug, the canonical unit
    shared with the web/Telegram runner's ``_agent_of``), else the running
    assistant, else the chat ``condor``. It therefore differs from
    ``_agent_of`` exactly when an agent runs a routine from the general
    library — which is why it is sent to the store rather than re-derived there.
    """
    owner = _owner_of(target) if target else None
    if owner:
        from routines.base import assistant_routines_dir, discover_routines_from_path

        routines_dir = assistant_routines_dir(owner)
        if routines_dir.exists():
            found = discover_routines_from_path(routines_dir, agent_slug=owner).get(
                name
            )
            if found:
                return found, owner

    return _resolve_routine(name), owner or settings.specialist_slug or "condor"


def _store_name(routine, fallback: str) -> str:
    """The name the main-process store — and the chat dock — knows a routine by.

    Agent-local routines are registered as ``{slug}/{name}``: that is what
    ``RoutineStore._resolve_routine`` splits on and what ``DockRoutines``
    filters the agent's panel by. The general library keeps its bare name.
    """
    rname = getattr(routine, "name", "") or fallback
    src = getattr(routine, "source", "global") or "global"
    if src.startswith("agent:"):
        return f"{src.split(':', 1)[1]}/{rname}"
    return rname


def _with_provenance(payload: dict) -> dict:
    """Stamp a run payload with the session that asked for it.

    The main process turns the key into the conversation the finished run reports
    back to (ARCH-089) — the same provenance ``delegate`` and ``send_notification``
    carry, so all three announce their outcome where the user asked for it. A
    runner with no session behind it (a tick loop, a delegate worker) sends
    nothing: there is no conversation to report to, and the route already reads a
    missing key as exactly that.
    """
    if settings.session_key:
        payload["session_key"] = settings.session_key
    return payload


async def _poll_until_done(instance_id: str) -> dict | None:
    """Follow a delegated run until it leaves ``running``. None on timeout."""
    deadline = time.monotonic() + _RUN_BUDGET
    while time.monotonic() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)
        inst = await call_main_api("GET", f"/routines/instances/{instance_id}")
        if isinstance(inst, dict) and inst.get("status") != "running":
            return inst
    return None


async def _submit_oneshot(
    name: str, config: dict | None, target: str | None
) -> tuple[str | None, dict | None]:
    """Resolve, validate and start a one-shot run. Returns (instance_id, error).

    Resolution and config validation stay here — this is the code that knows
    about the agent ``target`` and agent-local dirs, and a bad config field must fail
    fast with a useful message instead of arriving as a 404 from the API. The
    execution itself is delegated so the run is a real instance: it shows up in
    the dock while it runs, fires its post-run hooks, and keeps its result.

    Shared by the blocking ``run`` and the fire-and-forget ``run_async`` — the
    two differ only in whether they then wait for the instance to finish.
    """
    routine, agent = _resolve_with_owner(name, target)
    if not routine:
        return None, {"error": f"Routine '{name}' not found"}

    if routine.is_continuous:
        return None, {
            "error": f"Routine '{name}' is continuous — use action='start' to run "
            "it in the background, and action='stop' with its instance_id to end it."
        }

    try:
        routine.config_class(**(config or {}))
    except Exception as e:
        return None, {"error": f"Invalid config: {e}"}

    if not settings.active_server:
        return None, {"error": _NO_SERVER}

    try:
        started = await call_main_api(
            "POST",
            "/routines/run",
            _with_provenance(
                {
                    "routine_name": _store_name(routine, name),
                    "server_name": settings.active_server,
                    "config": config or {},
                    "attribute_to": agent,
                }
            ),
        )
    except APIError as e:
        return None, {"error": f"Routine '{name}' could not be run: {e}"}

    instance_id = started.get("instance_id") if isinstance(started, dict) else None
    if not instance_id:
        return None, {"error": f"Routine '{name}' did not start: {started}"}
    return instance_id, None


def _result_payload(name: str, instance_id: str, inst: dict) -> dict:
    """Shape a finished instance record into the tool's result dict."""
    return {
        "name": name,
        "instance_id": instance_id,
        "status": inst.get("status"),
        "report_id": inst.get("report_id"),
        "result": {
            "text": inst.get("result_text") or inst.get("last_result") or "",
            "table_data": inst.get("table_data"),
            "table_columns": inst.get("table_columns"),
            "chart_image": (
                "(PNG bytes, view via dashboard)" if inst.get("has_chart") else None
            ),
            "sections": inst.get("sections"),
        },
    }


async def run_routine(
    name: str, config: dict | None, target: str | None = None
) -> dict:
    """Run a one-shot routine in the main process and wait for its result."""
    instance_id, error = await _submit_oneshot(name, config, target)
    if error:
        return error

    try:
        inst = await _poll_until_done(instance_id)
    except APIError as e:
        return {"error": f"Routine '{name}' could not be run: {e}"}

    if inst is None:
        return {
            "error": f"Routine '{name}' timed out after {int(_RUN_BUDGET)}s. It is "
            "still running — read its result later with "
            f"action='get_instance', name='{instance_id}'. For a routine you "
            "know is slow, submit it with action='run_async' instead of waiting.",
            "instance_id": instance_id,
        }
    if inst.get("error"):
        return {
            "error": f"Routine '{name}' failed: {inst['error']}",
            "instance_id": instance_id,
        }

    return _result_payload(name, instance_id, inst)


async def run_async_routine(
    name: str, config: dict | None, target: str | None = None
) -> dict:
    """Submit a one-shot routine and return immediately — fire and forget.

    The counterpart of ``run`` for work that outlives a turn (a backtest, a long
    scan): the run is a normal instance in the main process, so it keeps going,
    fires its post-run hooks and stores its result whether or not anyone is
    waiting. Read it back later with ``get_instance``.
    """
    instance_id, error = await _submit_oneshot(name, config, target)
    if error:
        return error

    return {
        "started": True,
        "instance_id": instance_id,
        "routine": name,
        "note": (
            "Running in the background — do NOT wait for it. Read the result "
            f"later with action='get_instance', name='{instance_id}'."
        ),
    }


async def get_instance(instance_id: str) -> dict:
    """Read a run back: its status, and its result once it has finished."""
    try:
        inst = await call_main_api("GET", f"/routines/instances/{instance_id}")
    except APIError as e:
        return {
            "error": f"Could not read instance '{instance_id}': {e}. Instances live "
            "in the running process, so a bot restart drops them — the run's "
            "report survives in the dashboard. Use action='list_instances' to "
            "see what is still known."
        }

    if not isinstance(inst, dict):
        return {"error": f"Instance '{instance_id}' returned no record: {inst}"}

    name = inst.get("routine_name") or instance_id
    if inst.get("status") == "running":
        return {
            "name": name,
            "instance_id": instance_id,
            "status": "running",
            "note": "Still running — check again later.",
        }
    if inst.get("error"):
        return {
            "error": f"Routine '{name}' failed: {inst['error']}",
            "instance_id": instance_id,
            "status": inst.get("status"),
        }

    return _result_payload(name, instance_id, inst)


async def start_routine(name: str, config: dict | None) -> dict:
    """Start a continuous routine in the main process, so it can be stopped there."""
    routine = _resolve_routine(name)
    if not routine:
        return {"error": f"Routine '{name}' not found"}
    if not routine.is_continuous:
        return {
            "error": f"Routine '{name}' is not continuous — use action='run' instead"
        }

    try:
        routine.config_class(**(config or {}))
    except Exception as e:
        return {"error": f"Invalid config: {e}"}

    if not settings.active_server:
        return {"error": _NO_SERVER}

    try:
        data = await call_main_api(
            "POST",
            "/routines/start",
            _with_provenance(
                {
                    "routine_name": _store_name(routine, name),
                    "server_name": settings.active_server,
                    "config": config or {},
                    "attribute_to": settings.specialist_slug or "condor",
                }
            ),
        )
    except APIError as e:
        return {"error": f"Failed to start '{name}': {e}"}

    instance_id = data.get("instance_id") if isinstance(data, dict) else None
    return {"started": True, "instance_id": instance_id, "routine": name}


async def stop_routine(instance_id: str) -> dict:
    """Stop a running routine instance."""
    try:
        await call_main_api("POST", f"/routines/instances/{instance_id}/stop")
    except APIError as e:
        return {"error": f"Could not stop instance '{instance_id}': {e}"}
    return {"stopped": True, "instance_id": instance_id}


async def list_instances() -> dict:
    """List all running/scheduled routine instances."""
    try:
        instances = await call_main_api("GET", "/routines/instances")
    except APIError as e:
        return {"error": f"Could not list instances: {e}"}
    return {"instances": instances}


def create_routine(
    name: str, code: str, target: str | None, shared: bool = False
) -> dict:
    """Create a new routine file in the caller's writable library.

    ``shared=True`` publishes it to every assistant — chat only, see
    :func:`_get_agent_routines_dir`.
    """
    import re

    if not name or not re.match(r"^[a-z][a-z0-9_]*$", name):
        return {
            "error": "name must be lowercase alphanumeric with underscores (e.g. 'my_scanner')"
        }
    if not code:
        return {"error": "code is required"}

    routines_dir = _get_agent_routines_dir(target, shared)
    if not routines_dir:
        return {
            "error": "Pass agent — the slug of an existing agent (a legacy strategy "
            "key 'agent_slug.strategy_slug' is also accepted) — or CONDOR_AGENT_SLUG "
            "must be set."
        }

    file_path = routines_dir / f"{name}.py"
    if file_path.exists():
        return {
            "error": f"Routine '{name}' already exists. Use action='edit_routine' to update it."
        }

    if "class Config" not in code:
        return {"error": "Routine code must define a 'class Config(BaseModel)' class"}
    if "async def run" not in code and "def run" not in code:
        return {"error": "Routine code must define a 'run(config, context)' function"}

    routines_dir.mkdir(parents=True, exist_ok=True)
    file_path.write_text(code)

    from routines.base import discover_routines_from_path

    loaded = discover_routines_from_path(routines_dir)
    if name not in loaded:
        file_path.unlink()
        return {
            "error": "Routine file was created but failed to load. Check for syntax errors."
        }

    routine = loaded[name]
    result = {
        "created": True,
        "name": name,
        "description": routine.description,
        "path": str(file_path),
    }
    if routines_dir == _shared_root():
        result["shared"] = True
    return result


def read_routine(name: str, target: str | None, shared: bool = False) -> dict:
    """Read the source code of a routine."""
    routines_dir = _get_agent_routines_dir(target, shared)
    if routines_dir:
        file_path = routines_dir / f"{name}.py"
        if file_path.exists():
            return {"name": name, "code": file_path.read_text(), "scope": "agent"}

    # An assistant can read the source of anything it can run, so the shared
    # library is on the fallback path too — read-only for an agent, which the
    # `scope` says (writes go through _get_agent_routines_dir and never land here).
    shared_path = _shared_root() / f"{name}.py"
    if shared_path.exists():
        return {"name": name, "code": shared_path.read_text(), "scope": "shared"}

    global_path = Path("routines") / f"{name}.py"
    if global_path.exists():
        return {"name": name, "code": global_path.read_text(), "scope": "global"}

    return {"error": f"Routine '{name}' not found"}


def edit_routine(
    name: str, code: str, target: str | None, shared: bool = False
) -> dict:
    """Update the source code of a routine in the caller's writable library."""
    routines_dir = _get_agent_routines_dir(target, shared)
    if not routines_dir:
        return {
            "error": "Pass agent — the slug of an existing agent (a legacy strategy "
            "key 'agent_slug.strategy_slug' is also accepted) — or CONDOR_AGENT_SLUG "
            "must be set."
        }

    file_path = routines_dir / f"{name}.py"
    if not file_path.exists():
        return {
            "error": f"Agent routine '{name}' not found. Use action='create_routine' first."
        }

    if not code:
        return {"error": "code is required"}

    old_code = file_path.read_text()
    file_path.write_text(code)

    from routines.base import discover_routines_from_path

    loaded = discover_routines_from_path(routines_dir)
    if name not in loaded:
        file_path.write_text(old_code)
        return {
            "error": "Updated code failed to load (syntax error?). Reverted to previous version."
        }

    routine = loaded[name]
    return {
        "updated": True,
        "name": name,
        "description": routine.description,
    }


def delete_routine(name: str, target: str | None, shared: bool = False) -> dict:
    """Delete a routine from the caller's writable library."""
    routines_dir = _get_agent_routines_dir(target, shared)
    if not routines_dir:
        return {
            "error": "Pass agent — the slug of an existing agent (a legacy strategy "
            "key 'agent_slug.strategy_slug' is also accepted) — or CONDOR_AGENT_SLUG "
            "must be set."
        }

    file_path = routines_dir / f"{name}.py"
    if not file_path.exists():
        return {"error": f"Agent routine '{name}' not found"}

    file_path.unlink()
    return {"deleted": True, "name": name}


async def manage_routines(
    action: str,
    name: str | None = None,
    config: dict | None = None,
    agent: str | None = None,
    code: str | None = None,
    strategy_id: str | None = None,
    shared: bool | None = None,
) -> dict:
    # ``strategy_id`` is the deprecated spelling of ``agent`` — routines live at
    # the agent level, never per strategy. Kept so existing MCP hosts and the
    # playbooks that name it keep working.
    target = agent or strategy_id
    # Publish/target the shared library. Chat-only; ignored for an agent, whose
    # writes therefore stay in its own dir (see _get_agent_routines_dir).
    publish = bool(shared)

    if action == "list":
        return list_routines(target)
    if action == "describe":
        if not name:
            return {"error": "name is required"}
        return describe_routine(name)
    if action == "run":
        if not name:
            return {"error": "name is required"}
        return await run_routine(name, config, target)
    if action == "run_async":
        if not name:
            return {"error": "name is required"}
        return await run_async_routine(name, config, target)
    if action == "get_instance":
        if not name:
            return {"error": "instance_id is required (pass as name)"}
        return await get_instance(name)
    if action == "create_routine":
        if not name:
            return {"error": "name is required"}
        return create_routine(name, code or "", target, publish)
    if action == "read_routine":
        if not name:
            return {"error": "name is required"}
        return read_routine(name, target, publish)
    if action == "edit_routine":
        if not name:
            return {"error": "name is required"}
        return edit_routine(name, code or "", target, publish)
    if action == "delete_routine":
        if not name:
            return {"error": "name is required"}
        return delete_routine(name, target, publish)
    if action == "start":
        if not name:
            return {"error": "name is required"}
        return await start_routine(name, config)
    if action == "stop":
        if not name:
            return {"error": "instance_id is required (pass as name)"}
        return await stop_routine(name)
    if action == "list_instances":
        return await list_instances()
    return {"error": f"Unknown action: {action}"}
