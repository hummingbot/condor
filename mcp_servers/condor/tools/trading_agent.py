"""Explicit agent tools (§8 — Phase 5).

The ``manage_trading_agent`` mega-dispatcher is retired for narrowly typed
tools: ``create_agent / update_agent / delete_agent`` (tombstone semantics,
§5.2), ``run_agent / get_run / get_agent / list_agents``, one run-scoped
``control_run(run_id, verb, close?)``, and ``shutdown_agent(slug)`` (the
agent-scoped emergency winddown — §6.2's hierarchy makes shutdown
slug-scoped, so it cannot hide inside a run-keyed tool).

Every mutation goes over the control socket: the main-process AgentService
owns the guards (reserved tombstoned slugs, running-engine/nonterminal-
executor checks on delete, risk_limits/denomination validation on save)
exactly once (§5.2).
"""

from mcp_servers.condor.condor_client import call_control
from mcp_servers.condor.settings import settings

# ---------------------------------------------------------------------------
# Definitions (the AGENT.md identities)
# ---------------------------------------------------------------------------


async def list_agents() -> dict:
    """Agent summaries only — the full editable spec is get_agent."""
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
                "account": a.get("account"),
                "schedule": a.get("schedule"),
            }
            for a in agents
        ]
    }


async def get_agent(agent_slug: str) -> dict:
    """The full editable spec of one agent."""
    result = await call_control("agent.get", {"slug": agent_slug})
    return result.get("agent", result) if isinstance(result, dict) else result


async def create_agent(
    name: str,
    description: str = "",
    instructions: str = "",
    agent_key: str = "",
    tools: list[str] | None = None,
    when_to_consult: str = "",
    goal: str = "",
    risk_limits: dict | None = None,
    denomination: str = "",
    account: str = "",
    account_label: str = "",
    default_config: dict | None = None,
    default_trading_context: str = "",
    schedule: dict | None = None,
) -> dict:
    if not name:
        return {"error": "name is required to create an agent"}
    result = await call_control(
        "agent.create",
        {
            "name": name,
            "description": description,
            "instructions": instructions,
            "agent_key": agent_key,
            "tools": tools or [],
            "when_to_consult": when_to_consult,
            "goal": goal,
            "risk_limits": risk_limits or {},
            "denomination": denomination,
            "account": account,
            "account_label": account_label,
            "default_config": default_config or {},
            "default_trading_context": default_trading_context,
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


async def update_agent(agent_slug: str, **fields) -> dict:
    patch = {k: v for k, v in fields.items() if v is not None}
    if not agent_slug:
        return {"error": "agent_slug is required"}
    if not patch:
        return {"error": "no fields to update"}
    result = await call_control("agent.update", {"slug": agent_slug, "patch": patch})
    agent = result.get("agent", {}) if isinstance(result, dict) else {}
    return {
        "updated": True,
        "agent_slug": agent.get("slug"),
        "consultable": agent.get("consultable"),
    }


async def delete_agent(agent_slug: str) -> dict:
    # Tombstone, not erase (§5.2): the service rejects while engines run or
    # nonterminal executors remain; history stays readable, slug reserved.
    if not agent_slug:
        return {"error": "agent_slug is required"}
    return await call_control("agent.delete", {"slug": agent_slug})


# ---------------------------------------------------------------------------
# Lifecycle (delegates to main process via the control socket)
# ---------------------------------------------------------------------------


# An experiment blocks for its one simulated tick (same budget as a live
# tick) — the control call must outlive it.
_EXPERIMENT_TIMEOUT = 330.0


async def run_agent(
    agent_slug: str,
    config: dict | None = None,
    dry_run: bool = False,
    trading_context: str = "",
) -> dict:
    """Launch a run of ``agent_slug``. ``dry_run`` = an EXPERIMENT: one
    simulated tick fully in memory — mutations cancelled, nothing recorded —
    that blocks and returns {"report": <markdown>} instead of a run id.
    Launch overrides are limited and risk overrides are stricter-only (§5.3)
    — the service enforces both."""
    if not agent_slug:
        return {"error": "agent_slug is required"}

    # Existence check. Launch defaults and risk merging are owned by the
    # main-process service (§5.3) — we only send the caller's overrides.
    await call_control("agent.get", {"slug": agent_slug})

    config_dict = dict(config or {})
    experiment = (
        dry_run
        or config_dict.pop("dry_run", False)
        or config_dict.get("execution_mode") == "experiment"
    )
    trading_context = trading_context or config_dict.pop("trading_context", "")

    if experiment:
        return await call_control(
            "agent.experiment",
            {
                "slug": agent_slug,
                "config": config_dict,
                "trading_context": trading_context,
            },
            timeout=_EXPERIMENT_TIMEOUT,
        )

    return await call_control(
        "agent.start",
        {
            "slug": agent_slug,
            "config": config_dict,
            "trading_context": trading_context,
        },
    )


async def get_run(run_id: str, include_events: bool = False) -> dict:
    """One run: live engine info while running, RunStore meta after."""
    if not run_id:
        return {"error": "run_id is required"}
    return await call_control(
        "run.get", {"run_id": run_id, "include_events": include_events}
    )


async def list_runs(agent_slug: str = "", kind: str = "", limit: int = 20) -> dict:
    """Run history (RunStore metas, newest first), optionally one agent/kind."""
    return await call_control(
        "run.list",
        {"slug": agent_slug or None, "kind": kind or None, "limit": limit},
    )


async def control_run(run_id: str, verb: str, close: bool = False) -> dict:
    """Run-scoped verbs: pause | resume | stop. ``close=True`` on stop also
    closes the run's remaining owned inventory instead of detaching it."""
    if not run_id:
        return {"error": "run_id is required"}
    if verb not in ("pause", "resume", "stop"):
        return {
            "error": f"unknown verb {verb!r} — control_run takes pause|resume|stop"
            " (agent-wide emergency winddown is shutdown_agent)"
        }
    return await call_control(
        "agent.verb",
        {
            "slug": settings.agent_slug or "",
            "verb": verb,
            "agent_id": run_id,
            "close": close,
        },
    )


async def shutdown_agent(agent_slug: str) -> dict:
    """Agent-scoped emergency winddown (§6.2): stops every live run of the
    slug, cancels the agent's persisted orders, and closes its remaining
    owned inventory per its shutdown policy."""
    if not agent_slug:
        return {"error": "agent_slug is required"}
    return await call_control("agent.verb", {"slug": agent_slug, "verb": "shutdown"})


async def complete_run(summary: str = "") -> dict:
    """Declare THIS run's task complete (agent tick sessions only).

    ``settings.agent_id`` scopes the signal to the calling run — a chat
    session (no run identity) cannot complete anything."""
    if not settings.agent_id:
        return {
            "error": "complete_run only works from inside an agent run "
            "session — chat/consult surfaces have no run to complete"
        }
    return await call_control(
        "run.complete", {"run_id": settings.agent_id, "summary": summary}
    )
