"""Permission policies — the explicit lattice every agent run picks from.

The one axis that genuinely differs between run kinds is *who approves
mutations* (refactor-02 §4.1). Instead of three hardwired callbacks::

    human_gate(chat_id)      strictest — every dangerous call goes to the user
                             for Approve/Reject (consult)
    risk_gate(limits, state) ONE shared policy — auto-approve within caps, block
                             breaches. The caller picks the state seed:
                             journal-derived for ticks, zero when the caps
                             act as a fresh per-run budget
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
    from condor.agents.gating import is_dangerous_tool_call, tool_call_name

    async def callback(tool_call: dict, options: list[dict]) -> dict:
        if is_dangerous_tool_call(tool_call):
            log.warning(
                "deny_gate cancelled %s: %s",
                tool_call_name(tool_call) or "tool call",
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


def approval_gate(run_id: str, agent_slug: str, *, durable: bool = True):
    """Human gate through the approval queue (§4.2): dangerous tool calls
    become pending approvals + a ``kind=approval`` outbox entry, resolved via
    ``resolve_approval`` (any channel) or a dashboard button. Default deny
    on timeout; a one-use grant keyed by executor_id survives for a same-run
    retry of the same create. ``durable=False`` (run-less consults) keeps the
    whole flow in memory — no permission events are written anywhere."""
    from condor.agents.confirmation import _format_tool_summary
    from condor.agents.gating import is_dangerous_tool_call, tool_call_input

    async def callback(tool_call: dict, options: list[dict]) -> dict:
        if is_dangerous_tool_call(tool_call):
            from condor.agents.approvals import get_approval_manager

            input_data = tool_call_input(tool_call) or {}
            approved = await get_approval_manager().request(
                run_id=run_id,
                agent_slug=agent_slug,
                summary=_format_tool_summary(tool_call),
                tool_call_id=str(
                    tool_call.get("id") or tool_call.get("tool_call_id") or ""
                ),
                executor_id=str(input_data.get("executor_id") or ""),
                durable=durable,
            )
            if not approved:
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


def human_gate(
    chat_id: int, *, run_id: str = "", agent_slug: str = "", durable: bool = True
):
    """Route dangerous-tool confirmations to a human.

    With a ``run_id`` the gate is the approval queue (§4.2): outbox
    surfacing + ``resolve_approval``/dashboard resolution, default deny on
    timeout. ``durable=True`` (runs) additionally persists the permission
    events on the run stream; consults pass their ephemeral id with
    ``durable=False`` — same flow, memory only.

    Without one (plain chat sessions), it falls back to the interactive
    confirmation transport the UI layer registered at startup; with no
    transport and no usable ``chat_id`` it FAILS CLOSED via
    :func:`deny_gate` — never ``None`` (which would silently become full
    auto-approve).
    """
    if run_id:
        return approval_gate(run_id, agent_slug, durable=durable)
    if not chat_id:
        return deny_gate(
            "consult has no chat to confirm in (chat_id=0); "
            "mutations are denied — run the consult from a linked "
            "chat to approve actions"
        )
    try:
        from condor.agents.confirmation import get_confirmation_transport

        transport = get_confirmation_transport()
        if transport is not None:
            return functools.partial(transport, chat_id)
    except Exception:
        log.exception("Could not build human_gate callback")
    return deny_gate("no confirmation transport registered; mutations denied")


def scope_gate(policy, allowed_tools):
    """Enforce an agent's declared tool scope for system MUTATIONS, over any
    underlying policy.

    ACP agents receive the full Condor MCP surface — so agent/routine/skill *mutation* tools
    are reachable even when never declared, and neither ``risk_gate`` nor ``AUTO``
    classifies them (#8). This wrapper denies a system-mutation call whose tool is
    not in ``allowed_tools``; everything else defers to ``policy`` (or auto-approves
    when ``policy`` is AUTO/None). An EMPTY allowlist means UNRESTRICTED, so the
    policy is returned unwrapped.
    """
    if not allowed_tools:
        return policy

    from condor.agents.gating import system_mutation_tool

    allowed_short = {t.rsplit("__", 1)[-1] for t in allowed_tools}

    def _auto_select(options: list[dict]) -> dict:
        for opt in options:
            if opt.get("kind") in ("allow_once", "allow_always"):
                return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
        if options:
            return {
                "outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}
            }
        return {"outcome": {"outcome": "cancelled"}}

    async def callback(tool_call: dict, options: list[dict]) -> dict:
        mutated = system_mutation_tool(tool_call)
        if mutated is not None and mutated not in allowed_short:
            log.warning(
                "scope_gate denied undeclared system-mutation tool %s (allowed: %s)",
                mutated,
                sorted(allowed_short),
            )
            return {"outcome": {"outcome": "cancelled"}}
        if policy is None:  # AUTO underlying — approve non-mutations
            return _auto_select(options)
        return await policy(tool_call, options)

    return callback


def risk_gate(
    limits: RiskLimits | dict,
    state: RiskState | None = None,
    experiment: bool = False,
):
    """Auto-approve within risk caps; block breaches (the shared trading policy).

    ``limits`` may be a ``RiskLimits`` or a raw dict (per-call overrides, AGENT.md
    baselines). ``state`` defaults to a zero seed — a fresh per-run budget;
    ticks pass their journal-derived state.
    """
    if isinstance(limits, dict):
        limits = RiskLimits.from_dict(limits)
    return _risk_gate_callback(
        RiskEngine(limits), state or RiskState(), experiment=experiment
    )
