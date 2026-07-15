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


def select_engines(slug: str, agent_id: Optional[str] = None, running_only: bool = False) -> list:
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
    strategy: Optional[str] = None,
    config: Optional[dict] = None,
    trading_context: str = "",
    chat_id: int = 0,
    user_id: int = 0,
) -> dict:
    from condor.agents.agent import AgentStore
    from condor.agents.config import merge_launch_config, normalize_config
    from condor.agents.engine import TickEngine
    from condor.agents.strategy import StrategyStore
    from condor.executors.service import runtime_reconciling

    if runtime_reconciling():
        raise LifecycleError(
            503,
            "executor runtime is still reconciling after startup — retry shortly",
        )

    agent = AgentStore().get(agent_slug)
    if agent is None:
        raise LifecycleError(404, f"Agent '{agent_slug}' not found")

    strategies = StrategyStore().list(agent_slug)
    if strategy:
        selected = next((s for s in strategies if s.slug == strategy), None)
        if selected is None:
            raise LifecycleError(
                404,
                f"Strategy '{strategy}' not found under agent '{agent_slug}'. "
                f"Available: {[s.slug for s in strategies] or 'none'}",
            )
    elif len(strategies) == 1:
        selected = strategies[0]
    elif not strategies:
        raise LifecycleError(400, f"Agent '{agent_slug}' has no strategies — create one first.")
    else:
        raise LifecycleError(
            400,
            f"Agent '{agent_slug}' has several strategies — pass strategy=<slug>. "
            f"Available: {[s.slug for s in strategies]}",
        )

    # Risk resolution: request config > strategy default_config > agent baseline.
    # Seed the baseline BEFORE normalize_config fills schema defaults.
    defaults = dict(selected.default_config or {})
    if not defaults.get("risk_limits") and agent.risk_limits:
        defaults["risk_limits"] = dict(agent.risk_limits)
    config_dict = normalize_config(defaults)
    if config:
        # Deep-merge + validate: a partial risk_limits override must merge into
        # the seeded baseline (a shallow update replaced it, silently restoring
        # 500/5 schema defaults), and the final object must validate.
        try:
            config_dict = merge_launch_config(config_dict, config)
        except Exception as e:
            raise LifecycleError(422, f"invalid launch config: {e}")
    if trading_context:
        config_dict["trading_context"] = trading_context
    elif not config_dict.get("trading_context") and selected.default_trading_context:
        config_dict["trading_context"] = selected.default_trading_context

    engine = TickEngine(
        agent=agent, strategy=selected, config=config_dict, chat_id=chat_id, user_id=user_id
    )
    await engine.start()
    return {
        "started": True,
        "agent_id": engine.agent_id,
        "session_num": engine.session_num,
        "strategy": selected.slug,
    }


async def apply_verb(slug: str, agent_id: Optional[str], verb: str) -> dict:
    """stop (position-preserving) | shutdown (winddown) | pause | resume."""
    if verb == "stop":
        for engine in select_engines(slug, agent_id):
            await engine.stop()
        return {"stopped": True}
    if verb == "shutdown":
        for engine in select_engines(slug, agent_id):
            await engine._run_shutdown(reason="manual emergency stop")
        return {"shutdown": True}
    if verb == "pause":
        select_engines(slug, agent_id, running_only=True)[0].pause()
        return {"paused": True}
    if verb == "resume":
        select_engines(slug, agent_id)[0].resume()
        return {"resumed": True}
    raise LifecycleError(400, f"unknown lifecycle verb: {verb}")
