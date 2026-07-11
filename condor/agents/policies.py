"""Permission policies — the explicit lattice every agent run picks from.

The one axis that genuinely differs between run kinds is *who approves
mutations* (refactor-02 §4.1). Instead of three hardwired callbacks::

    human_gate(chat_id)      strictest — every dangerous call goes to the user's
                             Telegram chat for Approve/Reject (consult)
    risk_gate(limits, state) ONE shared policy — auto-approve within caps, block
                             breaches. The caller picks the state seed:
                             journal-derived for ticks, zero for delegations
                             (caps become a per-run budget)
    AUTO                     loosest — permission_callback=None, the client
                             auto-approves everything (serverless specialists)

A policy is either ``None`` (AUTO) or an async ``callback(tool_call, options)``
compatible with ACPClient/PydanticAIClient ``permission_callback``.
"""

from __future__ import annotations

import functools
import logging

from .risk import RiskEngine, RiskLimits, RiskState
from .risk import risk_gate as _risk_gate_callback

log = logging.getLogger(__name__)

# The loosest policy: no callback — the client auto-approves every tool call.
AUTO = None


def human_gate(chat_id: int):
    """Route dangerous-tool confirmations to the user's Telegram chat.

    Reuses the live bot registered at startup (main.py: routine_store.set_bot).
    Returns ``None`` when no bot is available — mutations will then error
    instead of silently auto-approving.
    """
    try:
        from condor.routine_store import get_routine_store
        from handlers.agents import confirmation

        bot = get_routine_store().get_bot()
        if bot is not None:
            return functools.partial(confirmation.permission_callback, bot, chat_id)
    except Exception:
        log.exception("Could not build human_gate callback; mutations will error")
    return None


def risk_gate(
    limits: RiskLimits | dict,
    state: RiskState | None = None,
    dry_run: bool = False,
):
    """Auto-approve within risk caps; block breaches (the shared trading policy).

    ``limits`` may be a ``RiskLimits`` or a raw dict (per-call overrides, AGENT.md
    baselines). ``state`` defaults to a zero seed — the delegation case, where the
    caps act as a per-run budget; ticks pass their journal-derived state.
    """
    if isinstance(limits, dict):
        limits = RiskLimits.from_dict(limits)
    return _risk_gate_callback(RiskEngine(limits), state or RiskState(), dry_run=dry_run)
