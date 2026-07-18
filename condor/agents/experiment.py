"""EXPERIMENT — an in-memory dry run: one simulated tick, no disk record.

An experiment answers "what WOULD this agent do right now?". It assembles the
same prompt a live tick would see (frozen prefix + per-tick suffix, real
provider data), drives :func:`condor.agents.run.run_agent` once under
``risk_gate(..., experiment=True)`` — every mutating tool call is cancelled at
the gate — and returns a human-readable markdown report of what the agent
would have done.

Nothing touches the RunStore: no run id, no event stream, no folder. The only
durable side effects an experiment can have are the ones the agent explicitly
chooses — ``record_learning`` (agent memory) and ``send_notification``.
The ephemeral ULID minted here exists only to scope the run capability and
executor attribution for the tick's read-only tool calls; it never lands on
disk.
"""

from __future__ import annotations

import json
import logging
import time

log = logging.getLogger(__name__)

# One simulated tick gets the same wall-clock budget as a live tick.
EXPERIMENT_TIMEOUT_S = 300


async def run_experiment(
    agent_slug: str,
    config: dict | None = None,
    trading_context: str = "",
) -> str:
    """Simulate one tick of ``agent_slug`` and return the markdown report.

    Raises :class:`condor.agents.lifecycle.LifecycleError` on unknown agent
    or invalid launch overrides (same contract as starting a session).
    """
    from condor.agents.agent import AgentStore
    from condor.agents.learnings import read_learnings
    from condor.agents.lifecycle import LifecycleError, resolve_launch_config
    from condor.agents.projections import RunMetricsTracker
    from condor.agents.prompts import (
        build_run_prompt_prefix,
        build_tick_prompt_suffix,
    )
    from condor.agents.providers import ProviderRegistry
    from condor.agents.risk import RiskEngine, RiskLimits, risk_gate
    from condor.agents.run import run_agent
    from condor.agents.runstore import new_ulid

    agent = AgentStore().get(agent_slug)
    if agent is None:
        raise LifecycleError(404, f"Agent '{agent_slug}' not found")

    # ``dry_run``/``execution_mode`` are how callers *request* an experiment —
    # here they are already satisfied, so strip the markers before the
    # override check.
    overrides = dict(config or {})
    overrides.pop("dry_run", None)
    overrides.pop("execution_mode", None)
    config_dict = resolve_launch_config(agent, overrides, trading_context)
    # Prompt selection only (BASE_PROMPT_EXPERIMENT + read-only tool preload);
    # not a storage mode — experiments have none.
    config_dict["execution_mode"] = "experiment"

    account_ref = None
    if agent.can_trade and agent.risk_limits:
        from condor.agents.spec import resolve_agent_account

        account_ref = resolve_agent_account(agent)

    # Ephemeral identity: scopes the run capability + attribution for the
    # tick's read-only tool calls. Never persisted anywhere.
    exp_id = new_ulid()

    # The same pre-flight a live tick runs: provider data, learnings, risk
    # baseline. No run history exists, so state/decisions are empty.
    skill_results = await ProviderRegistry().run_core_providers(
        config_dict, agent_id=exp_id, agent_slug=agent.slug
    )
    core_data = {name: r.summary for name, r in skill_results.items()}
    learnings = read_learnings(agent.agent_dir)
    risk_limits = dict(config_dict.get("risk_limits") or {})
    risk_engine = RiskEngine(RiskLimits.from_dict(risk_limits))
    risk_state = risk_engine.get_state(RunMetricsTracker())

    user_memory = ""
    skills_index = ""
    try:
        from condor.memory import MemoryStore, SkillStore

        user_memory = MemoryStore(None).list_index()
        skills_index = SkillStore(agent.slug).list_index()
    except Exception:
        pass

    prefix = build_run_prompt_prefix(agent, config_dict)
    suffix = build_tick_prompt_suffix(
        core_data=core_data,
        learnings=learnings,
        summary="",
        recent_decisions="",
        risk_state=risk_state.to_dict(),
        tick_number=1,
        agent_id=exp_id,
        user_memory=user_memory,
        skills_index=skills_index,
    )
    prompt = f"{prefix}\n\n{suffix}"

    tool_calls: list[dict] = []

    def _capture(tc: dict) -> None:
        from condor.agents.gating import is_dangerous_tool_call

        tool_calls.append(
            {
                "name": tc.get("name") or tc.get("title", ""),
                "status": tc.get("status", ""),
                "input": tc.get("input"),
                "mutating": is_dangerous_tool_call(tc),
            }
        )

    started = time.time()
    result = await run_agent(
        agent,
        prompt,
        permission_policy=risk_gate(risk_engine, risk_state, experiment=True),
        execution_mode="experiment",
        model=config_dict.get("agent_key") or agent.agent_key,
        risk_limits=risk_limits or None,
        account_ref=account_ref,
        timeout_s=EXPERIMENT_TIMEOUT_S,
        agent_id=exp_id,
        on_tool_call=_capture,
    )
    return _render_report(
        agent.slug, config_dict, result, tool_calls, time.time() - started
    )


def _render_report(
    slug: str, config: dict, result, tool_calls: list[dict], duration: float
) -> str:
    """The experiment's ONLY output: a human-readable markdown report."""
    would = [tc for tc in tool_calls if tc.get("mutating")]
    reads = [tc for tc in tool_calls if not tc.get("mutating")]

    header = [f"# 🧪 Experiment — {slug}", ""]
    facts = ["one simulated tick", "nothing recorded", "no orders placed"]
    if config.get("venue"):
        facts.append(f"venue {config['venue']}")
    if result.model:
        facts.append(f"model {result.model}")
    header.append("_" + " · ".join(facts) + "_")

    body = ["", "## What the agent would do", ""]
    if result.timed_out:
        body.append(f"⚠️ The simulation timed out: {result.error}")
        if result.text:
            body += ["", "Partial response:", "", result.text]
    else:
        body.append(result.text or "(the agent returned no response)")

    actions = ["", "## Trading actions it attempted (cancelled, not executed)", ""]
    if would:
        for tc in would:
            try:
                detail = json.dumps(tc.get("input"), default=str)
            except Exception:
                detail = str(tc.get("input"))
            if len(detail) > 400:
                detail = detail[:400] + "…"
            actions.append(f"- `{tc['name']}` — {detail}")
    else:
        actions.append("- none — the agent took no trading action this tick")

    footer = []
    if reads:
        names: dict[str, int] = {}
        for tc in reads:
            names[tc["name"]] = names.get(tc["name"], 0) + 1
        listing = ", ".join(
            f"{n}×{c}" if c > 1 else n for n, c in sorted(names.items())
        )
        footer.append("")
        footer.append(f"_Read-only tool calls: {listing}._")
    footer.append("")
    footer.append(f"_Simulated in {duration:.0f}s._")

    return "\n".join(header + body + actions + footer)
