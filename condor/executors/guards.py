"""Spec-declared entry guards — per-agent hard rules on the executor create path.

An agent's prompt can ASK for discipline; a guard ENFORCES it in code. The
first guard exists because prompt text failed in production: the memecoin
scalper's instructions said "blacklist any token that hit the hard stop", but
the model repeatedly re-bought tokens it had just been stopped out of
(falling-knife re-entries booking compounding losses).

This is THE pattern for per-agent custom trading rules (agent-builder skill,
docs/insight-flow-simplification.md): the rule is **declared in the agent's
spec** — so it is hashed/versioned with the spec and applies only to agents
that opt in — and **enforced by a vetted core primitive** here, on the one
create path. Never agent-authored code on the money path (routines are
strictly read-only, §7.2), and never prompt text alone.

Declaration, in AGENT.md ``default_config``::

    entry_guards:
      stop_loss_cooldown:
        hours: 4

Adding a new kind of rule = one function in ``_GUARDS`` + a spec entry on the
agents that want it.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger(__name__)


def entry_guard_reason(
    agent_slug: str,
    executor_type: str,
    config: dict,
    store=None,
) -> Optional[str]:
    """Reason to BLOCK this create per the agent's declared entry guards.

    ``None`` allows the trade. Agents with no spec or no ``entry_guards``
    declaration are unguarded (chat/manual creates have no spec at all). An
    unknown guard name in the spec blocks loudly — a typo must not silently
    disable a financial protection.
    """
    if not agent_slug:
        return None
    from condor.agents.agent import AgentStore

    agent = AgentStore().get(agent_slug)
    if agent is None:
        return None
    guards = (agent.default_config or {}).get("entry_guards") or {}
    for name, params in guards.items():
        guard = _GUARDS.get(name)
        if guard is None:
            return (
                f"unknown entry guard {name!r} declared in {agent_slug}'s spec "
                f"(known: {sorted(_GUARDS)}) — fix the spec"
            )
        reason = guard(agent_slug, executor_type, config or {}, params or {}, store)
        if reason:
            return reason
    return None


def _stop_loss_cooldown(
    agent_slug: str, executor_type: str, config: dict, params: dict, store
) -> Optional[str]:
    """No re-entry into a spot token this agent was stop-loss-closed out of
    within the cooldown window. Derived from the executor log's own history —
    no writer, no extra state; ``opened_at`` stands in for close time (spot
    positions live minutes). An unknown timestamp fails safe (treated recent);
    an unreadable store fails open (a log hiccup must not halt the agent)."""
    if executor_type != "position_spot":
        return None
    base_token = config.get("base_token", "")
    if not base_token:
        return None
    hours = float(params.get("hours", 4))
    cutoff = time.time() - hours * 3600
    try:
        if store is None:
            from condor.executors.log import ExecutorLog

            store = ExecutorLog()
        records = store.load_by_slug(agent_slug)
    except Exception:
        log.exception("stop_loss_cooldown: executor log read failed (fail-open)")
        return None
    for r in records:
        if r.type != "position_spot":
            continue
        if r.config.get("base_token") != base_token:
            continue
        if r.state.get("close_type") != "stop_loss":
            continue
        opened = r.state.get("opened_at")
        if opened is None or float(opened) >= cutoff:
            return (
                f"{base_token[:12]} was stopped out within {hours:g}h — "
                "cooldown blacklist, no re-entry"
            )
    return None


_GUARDS = {
    "stop_loss_cooldown": _stop_loss_cooldown,
}
