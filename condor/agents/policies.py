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


def deny_gate(reason: str):
    """Fail-closed policy: safe tools auto-approve, dangerous tools cancel.

    The fallback when a human gate cannot reach a human. Returning ``None``
    here would be catastrophic — a ``None`` permission callback means the
    client AUTO-APPROVES everything (that is exactly how AUTO works), so a
    failed gate must deny, loudly, never downgrade.
    """
    from handlers.agents._shared import is_dangerous_tool_call

    async def callback(tool_call: dict, options: list[dict]) -> dict:
        if is_dangerous_tool_call(tool_call):
            log.warning(
                "deny_gate cancelled %s: %s",
                tool_call.get("tool") or tool_call.get("title") or "tool call",
                reason,
            )
            return {"outcome": {"outcome": "cancelled"}}
        for opt in options:
            if opt.get("kind") in ("allow_once", "allow_always"):
                return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
        if options:
            return {
                "outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}
            }
        return {"outcome": {"outcome": "cancelled"}}

    return callback


def human_gate(chat_id: int):
    """Route dangerous-tool confirmations to the user's Telegram chat.

    Reuses the live bot registered at startup (main.py: routine_store.set_bot).
    When there is no human to route to — no bot registered, or no usable
    ``chat_id`` (web consults default to 0) — the gate FAILS CLOSED via
    :func:`deny_gate`: safe reads proceed, mutations are cancelled with a
    logged reason. It never returns ``None`` (which would silently become
    full auto-approve).
    """
    if not chat_id:
        return deny_gate(
            "consult has no Telegram chat to confirm in (chat_id=0); "
            "mutations are denied — run the consult from a Telegram-linked "
            "chat to approve actions"
        )
    try:
        from condor.routine_store import get_routine_store
        from handlers.agents import confirmation

        bot = get_routine_store().get_bot()
        if bot is not None:
            return functools.partial(confirmation.permission_callback, bot, chat_id)
    except Exception:
        log.exception("Could not build human_gate callback")
    return deny_gate("no Telegram bot registered to confirm with; mutations denied")


def risk_gate(
    limits: RiskLimits | dict,
    state: RiskState | None = None,
    experiment: bool = False,
):
    """Auto-approve within risk caps; block breaches (the shared trading policy).

    ``limits`` may be a ``RiskLimits`` or a raw dict (per-call overrides, AGENT.md
    baselines). ``state`` defaults to a zero seed — the delegation case, where the
    caps act as a per-run budget; ticks pass their journal-derived state.
    """
    if isinstance(limits, dict):
        limits = RiskLimits.from_dict(limits)
    return _risk_gate_callback(RiskEngine(limits), state or RiskState(), experiment=experiment)
