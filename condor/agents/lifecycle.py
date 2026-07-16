"""Transport-agnostic agent-session lifecycle.

Shared by the web routes (dashboard) and the control socket (headless MCP):
start a session, list running instances, pause / resume / stop / shutdown.
Raises :class:`LifecycleError` (HTTP-ish status) which each transport maps to
its own error shape — mirrors ``condor.executors.ops``.
"""

from __future__ import annotations

from typing import Optional


class LifecycleError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def engines_for_slug(slug: str) -> list:
    from condor.agents.engine import get_all_engines

    return [e for e in get_all_engines().values() if e.agent.slug == slug]


def select_engines(
    slug: str, agent_id: Optional[str] = None, running_only: bool = False
) -> list:
    from condor.agents.engine import get_engine

    if agent_id:
        engine = get_engine(agent_id)
        if not engine or (running_only and not engine.is_running):
            raise LifecycleError(404, f"Agent '{agent_id}' not found")
        return [engine]
    engines = engines_for_slug(slug)
    if running_only:
        engines = [e for e in engines if e.is_running]
    if not engines:
        raise LifecycleError(404, "No running session found")
    return engines


def list_instances() -> list[dict]:
    from condor.agents.engine import get_all_engines

    return [e.get_info() for e in get_all_engines().values()]


async def start_session(
    agent_slug: str,
    config: Optional[dict] = None,
    trading_context: str = "",
    kind: str = "",
    scheduled_for: str = "",
) -> dict:
    from condor.agents.agent import AgentStore
    from condor.agents.config import merge_launch_config, normalize_config
    from condor.agents.engine import TickEngine
    from condor.agents.spec import SpecValidationError, merge_risk_stricter
    from condor.executors.service import runtime_reconciling

    if runtime_reconciling():
        raise LifecycleError(
            503,
            "executor runtime is still reconciling after startup — retry shortly",
        )

    agent = AgentStore().get(agent_slug)
    if agent is None:
        raise LifecycleError(404, f"Agent '{agent_slug}' not found")

    # The AGENT.md is the one spec (§5.3): launch defaults come from its
    # default_config, with the agent risk baseline seeded BEFORE
    # normalize_config fills schema defaults.
    defaults = dict(agent.default_config or {})
    if not defaults.get("risk_limits") and agent.risk_limits:
        defaults["risk_limits"] = dict(agent.risk_limits)
    config_dict = normalize_config(defaults)
    if config:
        allowed_overrides = {
            "trading_context",
            "max_ticks",  # bounded run duration
            "dry_run",  # normalized to execution_mode=experiment
            "execution_mode",  # only experiment or the authored default
            "risk_limits",  # stricter-only below
        }
        forbidden = set(config) - allowed_overrides
        if forbidden:
            raise LifecycleError(
                422,
                "launch overrides may only set trading_context, max_ticks, "
                f"dry_run/experiment, and stricter risk_limits; forbidden: {sorted(forbidden)}",
            )
        requested_mode = config.get("execution_mode")
        if requested_mode not in (
            None,
            "experiment",
            config_dict.get("execution_mode"),
        ):
            raise LifecycleError(
                422,
                "launch execution_mode may only select experiment (dry run) "
                "or retain the authored default",
            )
        if "dry_run" in config and config.get("dry_run") is not True:
            raise LifecycleError(422, "launch dry_run override may only be true")
        if config.get("dry_run") is True:
            config = dict(config)
            config.pop("dry_run", None)
            config["execution_mode"] = "experiment"
        # Launch risk overrides are STRICTER-ONLY (§5.3): widening any
        # baseline cap is rejected before the deep merge + validation.
        try:
            if config.get("risk_limits"):
                config = dict(config)
                config["risk_limits"] = merge_risk_stricter(
                    config_dict.get("risk_limits", {}), config["risk_limits"]
                )
            config_dict = merge_launch_config(config_dict, config)
        except SpecValidationError as e:
            raise LifecycleError(422, str(e))
        except Exception as e:
            raise LifecycleError(422, f"invalid launch config: {e}")
    if trading_context:
        config_dict["trading_context"] = trading_context
    elif not config_dict.get("trading_context") and agent.default_trading_context:
        config_dict["trading_context"] = agent.default_trading_context

    try:
        engine = TickEngine(
            agent=agent,
            config=config_dict,
            kind_override=kind,
            scheduled_for=scheduled_for,
        )
    except SpecValidationError as e:
        raise LifecycleError(422, str(e))
    await engine.start()
    return {
        "started": True,
        "agent_id": engine.agent_id,
        "session_num": engine.session_num,
    }


async def apply_verb(
    slug: str, agent_id: Optional[str], verb: str, close: bool = False
) -> dict:
    """stop (position-preserving; ``close=True`` liquidates the run scope) |
    shutdown (agent-scoped emergency winddown) | pause | resume."""
    if verb == "stop":
        engines = []
        if agent_id:
            from condor.agents.engine import get_engine

            engine = get_engine(agent_id)
            if engine is not None:
                engines = [engine]
        else:
            engines = engines_for_slug(slug)
        for engine in engines:
            await engine.stop(close=close)
        stopped_durable: list[str] = []
        from condor.executors.service import peek_executor_runtime

        runtime = peek_executor_runtime()
        if runtime is not None:
            stopped_durable = (
                runtime.stop_agent_executors(agent_id, keep_position=not close)
                if agent_id
                else runtime.stop_slug_executors(slug, keep_position=not close)
            )
        if not engines and not stopped_durable:
            raise LifecycleError(404, "No run or durable executor scope found")
        return {
            "stopped": True,
            "closed": close,
            "stopped_executors": stopped_durable,
        }
    if verb == "shutdown":
        # Agent-SCOPED (§6.2): every live run winds down, and executors
        # surviving from prior runs of the slug stop too — a dead run's
        # financial scope must not escape the emergency stop.
        engines = select_engines(slug, agent_id) if agent_id else engines_for_slug(slug)
        for engine in engines:
            await engine._run_shutdown(reason="manual emergency stop")
        stopped_leftovers: list[str] = []
        if slug:
            from condor.executors.service import peek_executor_runtime

            runtime = peek_executor_runtime()
            if runtime is not None:
                stopped_leftovers = runtime.stop_slug_executors(
                    slug, keep_position=False
                )
        if not engines and not stopped_leftovers and not agent_id:
            raise LifecycleError(404, f"nothing to shut down for '{slug}'")
        return {"shutdown": True, "stopped_executors": stopped_leftovers}
    if verb == "pause":
        select_engines(slug, agent_id, running_only=True)[0].pause()
        return {"paused": True}
    if verb == "resume":
        select_engines(slug, agent_id)[0].resume()
        return {"resumed": True}
    raise LifecycleError(400, f"unknown lifecycle verb: {verb}")
