"""Routine discovery, execution, and CRUD operations."""

import asyncio
from pathlib import Path

from mcp_servers.condor.settings import settings


def _get_agent_routines_dir(strategy_id: str | None) -> Path | None:
    """Resolve the routines directory to write to.

    Routines live at the **Agent** level (``agents/<slug>/routines``),
    shared across all of that agent's strategies. A strategy_id (composite key
    "agent_slug.strategy_slug") resolves to its owning agent's routines dir; a
    bare agent slug ("agent_slug", no dot) resolves to that agent directly —
    used in the expert-first flow where routines are created before any strategy
    exists. Otherwise the current assistant's own dir — the general library
    (root ``routines/``) for the chat, or the launched Agent's
    (``agents/<slug>/routines``, ``settings.agent_slug``).
    """
    from routines.base import assistant_routines_dir

    if strategy_id:
        from condor.agents.strategy import StrategyStore

        s = StrategyStore().get_by_key(strategy_id)
        if s:
            return assistant_routines_dir(s.agent_slug)
        # Fall back: treat strategy_id as a bare agent slug so routines can be
        # created/run for a consult-only expert that owns no strategy yet.
        from condor.agents.agent import AgentStore

        if AgentStore().get(strategy_id):
            return assistant_routines_dir(strategy_id)
        return None

    return assistant_routines_dir(settings.agent_slug or None)


def _resolve_routine(name: str):
    """Look up a routine in the current assistant's scope.

    A domain expert/trading agent (``settings.agent_slug`` set) resolves ONLY its
    own routines — it never sees the chat's general library. The chat ``condor``
    resolves the general library (root ``routines/``).
    """
    from routines.base import (
        assistant_routines_dir,
        discover_routines,
        discover_routines_from_path,
    )

    if settings.agent_slug:
        own_dir = assistant_routines_dir(settings.agent_slug)
        if own_dir.exists():
            own = discover_routines_from_path(own_dir, agent_slug=settings.agent_slug)
            return own.get(name)
        return None

    return discover_routines(force_reload=True).get(name)


def list_routines(strategy_id: str | None = None) -> dict:
    from routines.base import discover_routines, discover_routines_from_path

    result = []

    # Agent/expert MCP: ONLY its own routines, isolated from the general library.
    if settings.agent_slug:
        own_dir = _get_agent_routines_dir(None)
        if own_dir and own_dir.exists():
            for name, routine in sorted(discover_routines_from_path(own_dir).items()):
                result.append(
                    {
                        "name": name,
                        "description": routine.description,
                        "type": "continuous" if routine.is_continuous else "one-shot",
                        "scope": "agent",
                        "agent": settings.agent_slug,
                    }
                )
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

    if strategy_id:
        agent_routines_dir = _get_agent_routines_dir(strategy_id)
        if agent_routines_dir and agent_routines_dir.exists():
            for name, routine in sorted(
                discover_routines_from_path(agent_routines_dir).items()
            ):
                result.append(
                    {
                        "name": name,
                        "description": routine.description,
                        "type": "continuous" if routine.is_continuous else "one-shot",
                        "scope": "agent",
                        "agent": strategy_id,
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


class MCPContext:
    """Minimal mock context for routine execution."""

    def __init__(self):
        self._chat_id = settings.chat_id
        self._user_id = settings.user_id
        self._user_data: dict = {}
        # Use the HTTP fallback bot from routine_store so messages are delivered
        from condor.routine_store import _http_bot

        self.bot = _http_bot
        self.application = None

    @property
    def user_data(self):
        return self._user_data


async def run_routine(
    name: str, config: dict | None, strategy_id: str | None = None
) -> dict:
    """Execute a one-shot routine and return its result."""
    routine = None

    if strategy_id:
        routines_dir = _get_agent_routines_dir(strategy_id)
        if routines_dir and routines_dir.exists():
            from routines.base import discover_routines_from_path

            agent_routines = discover_routines_from_path(routines_dir)
            routine = agent_routines.get(name)

    if not routine:
        routine = _resolve_routine(name)

    if not routine:
        return {"error": f"Routine '{name}' not found"}

    if routine.is_continuous:
        return {
            "error": f"Routine '{name}' is continuous and cannot be run via MCP. "
            "Use the Telegram /routines command to start/stop continuous routines."
        }

    try:
        config_obj = routine.config_class(**(config or {}))
    except Exception as e:
        return {"error": f"Invalid config: {e}"}

    context = MCPContext()

    # Attribute the report to its producer: an explicit strategy's owning agent
    # (the bare agent slug, the canonical attribution unit shared with the
    # web/Telegram runner's _agent_of and the agent index), else the run context
    # (Agent consult -> its slug; chat condor -> "condor").
    agent = settings.agent_slug or "condor"
    if strategy_id:
        from condor.agents.strategy import StrategyStore

        s = StrategyStore().get_by_key(strategy_id)
        if s:
            agent = s.agent_slug
        else:
            from condor.agents.agent import AgentStore

            if AgentStore().get(strategy_id):
                # bare agent slug (expert-first flow): attribute to the agent
                agent = strategy_id

    try:
        from condor.reports import attribute_to

        with attribute_to(agent):
            result = await asyncio.wait_for(
                routine.run_fn(config_obj, context), timeout=120
            )
        from routines.base import normalize_result

        nr = normalize_result(result)
        return {
            "name": name,
            "result": {
                "text": nr.text,
                "table_data": nr.table_data,
                "table_columns": nr.table_columns,
                "chart_image": (
                    "(PNG bytes, view via dashboard)" if nr.chart_image else None
                ),
                "sections": nr.sections,
            },
        }
    except asyncio.TimeoutError:
        return {"error": f"Routine '{name}' timed out after 120s"}
    except Exception as e:
        return {"error": f"Routine '{name}' failed: {e}"}


async def start_routine(name: str, config: dict | None) -> dict:
    """Start a continuous routine as a background task."""
    routine = _resolve_routine(name)
    if not routine:
        return {"error": f"Routine '{name}' not found"}
    if not routine.is_continuous:
        return {
            "error": f"Routine '{name}' is not continuous — use action='run' instead"
        }

    from condor.routine_store import get_routine_store

    store = get_routine_store()
    try:
        instance_id = await store.start_continuous(
            routine_name=name,
            config=config or {},
            server_name=settings.active_server,
            user_id=settings.chat_id,
        )
        return {"started": True, "instance_id": instance_id, "routine": name}
    except Exception as e:
        return {"error": f"Failed to start: {e}"}


def stop_routine(instance_id: str) -> dict:
    """Stop a running routine instance."""
    from condor.routine_store import get_routine_store

    store = get_routine_store()
    stopped = store.stop(instance_id)
    if stopped:
        return {"stopped": True, "instance_id": instance_id}
    return {"error": f"Instance '{instance_id}' not found or already stopped"}


def list_instances() -> dict:
    """List all running/scheduled routine instances."""
    from condor.routine_store import get_routine_store

    store = get_routine_store()
    instances = store.list_instances()
    return {"instances": instances}


def create_routine(name: str, code: str, strategy_id: str | None) -> dict:
    """Create a new agent-local routine file."""
    import re

    if not name or not re.match(r"^[a-z][a-z0-9_]*$", name):
        return {
            "error": "name must be lowercase alphanumeric with underscores (e.g. 'my_scanner')"
        }
    if not code:
        return {"error": "code is required"}

    routines_dir = _get_agent_routines_dir(strategy_id)
    if not routines_dir:
        return {
            "error": "Pass strategy_id — a strategy key 'agent_slug.strategy_slug' "
            "or a bare agent slug — (or CONDOR_AGENT_SLUG must be set), and it must "
            "resolve to an existing agent."
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
    return {
        "created": True,
        "name": name,
        "description": routine.description,
        "path": str(file_path),
    }


def read_routine(name: str, strategy_id: str | None) -> dict:
    """Read the source code of a routine."""
    routines_dir = _get_agent_routines_dir(strategy_id)
    if routines_dir:
        file_path = routines_dir / f"{name}.py"
        if file_path.exists():
            return {"name": name, "code": file_path.read_text(), "scope": "agent"}

    global_path = Path("routines") / f"{name}.py"
    if global_path.exists():
        return {"name": name, "code": global_path.read_text(), "scope": "global"}

    return {"error": f"Routine '{name}' not found"}


def edit_routine(name: str, code: str, strategy_id: str | None) -> dict:
    """Update the source code of an agent-local routine."""
    routines_dir = _get_agent_routines_dir(strategy_id)
    if not routines_dir:
        return {
            "error": "Pass strategy_id — a strategy key 'agent_slug.strategy_slug' "
            "or a bare agent slug — (or CONDOR_AGENT_SLUG must be set), and it must "
            "resolve to an existing agent."
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


def delete_routine(name: str, strategy_id: str | None) -> dict:
    """Delete an agent-local routine."""
    routines_dir = _get_agent_routines_dir(strategy_id)
    if not routines_dir:
        return {
            "error": "Pass strategy_id — a strategy key 'agent_slug.strategy_slug' "
            "or a bare agent slug — (or CONDOR_AGENT_SLUG must be set), and it must "
            "resolve to an existing agent."
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
    strategy_id: str | None = None,
    code: str | None = None,
) -> dict:
    if action == "list":
        return list_routines(strategy_id)
    if action == "describe":
        if not name:
            return {"error": "name is required"}
        return describe_routine(name)
    if action == "run":
        if not name:
            return {"error": "name is required"}
        return await run_routine(name, config, strategy_id)
    if action == "create_routine":
        if not name:
            return {"error": "name is required"}
        return create_routine(name, code or "", strategy_id)
    if action == "read_routine":
        if not name:
            return {"error": "name is required"}
        return read_routine(name, strategy_id)
    if action == "edit_routine":
        if not name:
            return {"error": "name is required"}
        return edit_routine(name, code or "", strategy_id)
    if action == "delete_routine":
        if not name:
            return {"error": "name is required"}
        return delete_routine(name, strategy_id)
    if action == "start":
        if not name:
            return {"error": "name is required"}
        return await start_routine(name, config)
    if action == "stop":
        if not name:
            return {"error": "instance_id is required (pass as name)"}
        return stop_routine(name)
    if action == "list_instances":
        return list_instances()
    return {"error": f"Unknown action: {action}"}
