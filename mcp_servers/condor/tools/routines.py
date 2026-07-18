"""Routine discovery, execution, and CRUD operations."""

import asyncio
import logging
from pathlib import Path

from mcp_servers.condor.settings import settings

log = logging.getLogger(__name__)


def _authoring_target(agent_slug: str | None) -> tuple[str | None, dict | None]:
    """Resolve the IMMUTABLE authoring target for routine mutations (§7.2).

    An agent-scoped session (``settings.agent_slug`` set at spawn by the main
    process — consult, tick) may author ONLY its own routines: the
    target is the session's agent, and a caller-supplied ``agent_slug`` naming
    a DIFFERENT agent is denied and recorded. The chat coordinator (no session
    agent) acts for the human and may target any existing agent explicitly.

    Returns ``(effective_agent_slug, error_dict_or_None)``.
    """
    session_agent = settings.agent_slug or ""
    if session_agent:
        if agent_slug and agent_slug != session_agent:
            log.warning(
                "routine authoring denied: session agent %r attempted to "
                "target %r",
                session_agent,
                agent_slug,
            )
            return None, {
                "error": (
                    f"routine authoring is scoped to your own agent "
                    f"('{session_agent}') — you cannot create or edit "
                    f"another agent's routines ('{agent_slug}')"
                )
            }
        return session_agent, None
    return agent_slug, None


def _get_agent_routines_dir(agent_slug: str | None) -> Path | None:
    """Resolve the routines directory to write to.

    Routines live at the **Agent** level (``agents/<slug>/routines``), shared
    across all of that agent's strategies. An explicit ``agent_slug`` targets
    that agent (chat-side authoring); otherwise the current assistant's own dir
    — the general library (root ``routines/``) for the chat, or the launched
    Agent's (``agents/<slug>/routines``, ``settings.agent_slug``).
    """
    from routines.base import assistant_routines_dir

    if agent_slug:
        from condor.agents.agent import AgentStore

        if AgentStore().get(agent_slug):
            return assistant_routines_dir(agent_slug)
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


def list_routines(agent_slug: str | None = None) -> dict:
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

    if agent_slug:
        agent_routines_dir = _get_agent_routines_dir(agent_slug)
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
                        "agent": agent_slug,
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


async def run_routine(
    name: str, config: dict | None, agent_slug: str | None = None
) -> dict:
    """Execute a one-shot routine in a disposable worker subprocess (§7.2).

    The worker resolves the routine (agent-local dir first, then the global
    library), enforces the hard 120s timeout via SIGKILL, and returns the
    normalized result. A crash or hang takes down only the worker.
    """
    # Attribute the report to its producer: the explicitly-targeted agent,
    # else the run context (Agent consult → its slug; chat condor → "condor").
    agent = settings.agent_slug or "condor"
    if agent_slug:
        from condor.agents.agent import AgentStore

        if AgentStore().get(agent_slug):
            agent = agent_slug

    from condor.routines_worker import run_routine_in_worker

    out = await run_routine_in_worker(
        name,
        config or {},
        agent_slug=agent_slug or settings.agent_slug or "",
        attribute=agent,
    )
    if "result" in out:
        result = out["result"]
        if result.pop("chart_image_b64", None):
            result["chart_image"] = "(PNG bytes, view via dashboard)"
    return out


async def start_routine(name: str, config: dict | None) -> dict:
    """Removed: routines are strictly one-shot (§7.2)."""
    return {
        "error": "Continuous routines were removed — routines are one-shot "
        "with a hard timeout. Schedule the routine instead "
        "(action='schedule_routine' with a cron expression); the scheduler "
        "provides the repetition."
    }


def schedule_routine(
    name: str, config: dict | None, cron: str, tz: str = "UTC",
    agent_slug: str | None = None,
) -> dict:
    """Create a durable schedule (store/schedules.json) for a read-only
    routine — survives restarts; missed fires are skipped, never backfilled."""
    if not cron:
        return {"error": "cron is required (5-field cron expression)"}
    from condor.agents.scheduler import create_routine_schedule

    try:
        return create_routine_schedule(
            routine=name,
            agent_slug=agent_slug or settings.agent_slug or "",
            config=config or {},
            cron=cron,
            tz=tz,
        )
    except Exception as e:
        return {"error": f"could not schedule: {e}"}


def unschedule_routine(schedule_id: str) -> dict:
    from condor.agents.scheduler import RoutineScheduleStore

    if RoutineScheduleStore().remove(schedule_id):
        return {"removed": True, "schedule_id": schedule_id}
    return {"error": f"schedule '{schedule_id}' not found"}


def list_schedules() -> dict:
    from condor.agents.scheduler import RoutineScheduleStore

    data = RoutineScheduleStore().load()
    return {
        "schedules": [
            {"schedule_id": sid, **entry} for sid, entry in sorted(data.items())
        ]
    }


async def create_routine(name: str, code: str, agent_slug: str | None) -> dict:
    """Create a new agent-local routine file."""
    import re

    if not name or not re.match(r"^[a-z][a-z0-9_]*$", name):
        return {
            "error": "name must be lowercase alphanumeric with underscores (e.g. 'my_scanner')"
        }
    if not code:
        return {"error": "code is required"}

    agent_slug, denied = _authoring_target(agent_slug)
    if denied:
        return denied
    routines_dir = _get_agent_routines_dir(agent_slug)
    if not routines_dir:
        return {
            "error": "Pass agent_slug (or CONDOR_AGENT_SLUG must be set), and it "
            "must resolve to an existing agent."
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

    # Validate in a disposable worker (§7.2): a blocking top-level import in
    # the new module can no longer hang this process, and a syntax error is
    # caught before the routine is discoverable.
    from condor.routines_worker import validate_routine_in_worker

    check = await validate_routine_in_worker(str(file_path))
    if check.get("error"):
        file_path.unlink()
        return {"error": f"Routine failed validation: {check['error']}"}
    if check.get("continuous"):
        file_path.unlink()
        return {
            "error": "Continuous routines were removed (§7.2) — write a "
            "one-shot run() and schedule it (action='schedule_routine')."
        }
    return {"created": True, "name": name, "path": str(file_path)}


def read_routine(name: str, agent_slug: str | None) -> dict:
    """Read the source code of a routine."""
    routines_dir = _get_agent_routines_dir(agent_slug)
    if routines_dir:
        file_path = routines_dir / f"{name}.py"
        if file_path.exists():
            return {"name": name, "code": file_path.read_text(), "scope": "agent"}

    global_path = Path("routines") / f"{name}.py"
    if global_path.exists():
        return {"name": name, "code": global_path.read_text(), "scope": "global"}

    return {"error": f"Routine '{name}' not found"}


async def edit_routine(name: str, code: str, agent_slug: str | None) -> dict:
    """Update the source code of an agent-local routine."""
    agent_slug, denied = _authoring_target(agent_slug)
    if denied:
        return denied
    routines_dir = _get_agent_routines_dir(agent_slug)
    if not routines_dir:
        return {
            "error": "Pass agent_slug (or CONDOR_AGENT_SLUG must be set), and it "
            "must resolve to an existing agent."
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

    from condor.routines_worker import validate_routine_in_worker

    check = await validate_routine_in_worker(str(file_path))
    if check.get("error"):
        file_path.write_text(old_code)
        return {
            "error": f"Updated code failed validation ({check['error']}). "
            "Reverted to previous version."
        }
    return {"updated": True, "name": name}


def delete_routine(name: str, agent_slug: str | None) -> dict:
    """Delete an agent-local routine."""
    agent_slug, denied = _authoring_target(agent_slug)
    if denied:
        return denied
    routines_dir = _get_agent_routines_dir(agent_slug)
    if not routines_dir:
        return {
            "error": "Pass agent_slug (or CONDOR_AGENT_SLUG must be set), and it "
            "must resolve to an existing agent."
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
    agent_slug: str | None = None,
    code: str | None = None,
) -> dict:
    if action == "list":
        return list_routines(agent_slug)
    if action == "describe":
        if not name:
            return {"error": "name is required"}
        return describe_routine(name)
    if action == "run":
        if not name:
            return {"error": "name is required"}
        return await run_routine(name, config, agent_slug)
    if action == "create_routine":
        if not name:
            return {"error": "name is required"}
        return await create_routine(name, code or "", agent_slug)
    if action == "read_routine":
        if not name:
            return {"error": "name is required"}
        return read_routine(name, agent_slug)
    if action == "edit_routine":
        if not name:
            return {"error": "name is required"}
        return await edit_routine(name, code or "", agent_slug)
    if action == "delete_routine":
        if not name:
            return {"error": "name is required"}
        return delete_routine(name, agent_slug)
    if action == "start":
        if not name:
            return {"error": "name is required"}
        return await start_routine(name, config)
    if action == "schedule_routine":
        if not name:
            return {"error": "name is required"}
        cfg = dict(config or {})
        cron = cfg.pop("cron", "")
        tz = cfg.pop("tz", "UTC")
        return schedule_routine(name, cfg, cron=cron, tz=tz, agent_slug=agent_slug)
    if action == "unschedule_routine":
        if not name:
            return {"error": "schedule_id is required (pass as name)"}
        return unschedule_routine(name)
    if action in ("list_schedules", "list_instances"):
        return list_schedules()
    if action == "stop":
        return {
            "error": "Continuous routines were removed — use "
            "action='unschedule_routine' with the schedule_id to stop a "
            "scheduled routine."
        }
    return {"error": f"Unknown action: {action}"}
