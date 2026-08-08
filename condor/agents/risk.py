"""Risk engine -- pre-tick validation and guardrails.

Enforces position limits, daily loss caps, drawdown limits, executor counts,
and LLM cost caps.  Also provides a permission callback that auto-approves
safe tool calls and blocks dangerous ones that violate risk limits.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .ownership import BotLedger

log = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    max_position_size_quote: float = 500.0
    max_open_executors: int = 5
    max_drawdown_pct: float = -1.0
    # Hard kill-switch: a deeper drawdown than the soft ``max_drawdown_pct`` pause;
    # breaching it winds down positions (see condor.agents.shutdown). -1 = disabled.
    shutdown_drawdown_pct: float = -1.0

    @classmethod
    def from_dict(cls, d: dict) -> RiskLimits:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class RiskState:
    total_exposure: float = 0.0
    executor_count: int = 0
    drawdown_pct: float = 0.0
    is_blocked: bool = False
    block_reason: str = ""
    # Hard escalation: set when the shutdown drawdown threshold is breached. The
    # soft ``is_blocked`` only pauses the tick; this triggers an emergency winddown.
    should_shutdown: bool = False
    shutdown_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_exposure": self.total_exposure,
            "executor_count": self.executor_count,
            "drawdown_pct": self.drawdown_pct,
            "is_blocked": self.is_blocked,
            "block_reason": self.block_reason,
            "should_shutdown": self.should_shutdown,
            "shutdown_reason": self.shutdown_reason,
            # Include limits for prompt display
            "max_position_size": (
                self._limits.max_position_size_quote
                if hasattr(self, "_limits")
                else 500
            ),
            "max_open_executors": (
                self._limits.max_open_executors if hasattr(self, "_limits") else 5
            ),
            "max_drawdown_pct": (
                self._limits.max_drawdown_pct if hasattr(self, "_limits") else -1
            ),
            "shutdown_drawdown_pct": (
                self._limits.shutdown_drawdown_pct if hasattr(self, "_limits") else -1
            ),
        }


class RiskEngine:
    """Evaluates risk state and can block snapshots or individual tool calls."""

    def __init__(self, limits: RiskLimits | None = None):
        self.limits = limits or RiskLimits()

    def get_state(self, tracker: Any) -> RiskState:
        """Compute current risk metrics from tracker data."""
        state = RiskState()
        state._limits = self.limits

        try:
            state.total_exposure = tracker.get_total_exposure()
            state.executor_count = tracker.get_open_executor_count()
            state.drawdown_pct = tracker.get_drawdown_pct()
        except Exception as exc:
            log.exception("Failed to compute risk state from tracker")
            # Fail closed: without real metrics we must not approve creates
            # against zeroed exposure/count. A blocked state makes the engine
            # pause the tick and notify instead of trading blind.
            state.is_blocked = True
            state.block_reason = f"risk state unavailable: {exc}"
            return state

        # Check blocking conditions
        reasons = []

        if (
            self.limits.max_drawdown_pct >= 0
            and state.drawdown_pct > self.limits.max_drawdown_pct
        ):
            reasons.append(
                f"Drawdown {state.drawdown_pct:.1f}% exceeds limit {self.limits.max_drawdown_pct:.1f}%"
            )

        if reasons:
            state.is_blocked = True
            state.block_reason = "; ".join(reasons)

        # Hard kill-switch: a deeper drawdown than the soft pause. Evaluated
        # independently so a breach escalates to a winddown even though it also
        # trips the soft block (the engine checks should_shutdown first).
        if (
            self.limits.shutdown_drawdown_pct >= 0
            and state.drawdown_pct > self.limits.shutdown_drawdown_pct
        ):
            state.should_shutdown = True
            state.shutdown_reason = (
                f"Drawdown {state.drawdown_pct:.1f}% exceeds shutdown limit "
                f"{self.limits.shutdown_drawdown_pct:.1f}%"
            )

        return state

    def check_executor_action(
        self,
        tool_call: dict,
        current_state: RiskState,
        planned_amount_quote: float | None = None,
    ) -> tuple[bool, str]:
        """Check if an executor creation is within risk limits.

        On approval of a "create", accumulates it into ``current_state``
        (executor count and exposure) so subsequent checks within the same
        tick see the running totals instead of the frozen per-tick snapshot.
        The state is recomputed from the journal at the start of each tick.

        Returns (allowed, reason).
        """
        from handlers.agents._shared import tool_call_input

        input_data = tool_call_input(tool_call)
        if input_data is None:
            return False, "Tool arguments could not be read"
        action = input_data.get("action", "")

        # Only gate "create" actions
        if action != "create":
            return True, ""

        # Check executor count
        if current_state.executor_count >= self.limits.max_open_executors:
            return (
                False,
                f"Max open executors ({self.limits.max_open_executors}) reached",
            )

        if (
            planned_amount_quote is None
            or not math.isfinite(planned_amount_quote)
            or planned_amount_quote <= 0
        ):
            return False, "Planned quote exposure is unavailable"
        amount = planned_amount_quote

        if current_state.total_exposure + amount > self.limits.max_position_size_quote:
            return False, (
                f"Would exceed position limit: ${current_state.total_exposure + amount:.2f} > "
                f"${self.limits.max_position_size_quote:.2f}"
            )

        # Approved: accumulate into the snapshot so the next create in this
        # tick is gated against the running totals, not the pre-tick numbers.
        current_state.executor_count += 1
        current_state.total_exposure += amount

        return True, ""

    def check_bot_action(self, tool_call: dict) -> tuple[bool, str]:
        """Check a manage_bots call against risk limits.

        A bot's capital lives in saved controller configs on the API server,
        so exposure can't be computed from the tool inputs alone. Instead,
        bound the loss: a deploy must declare ``max_global_drawdown_quote``
        (the platform-enforced kill switch) no larger than the strategy's
        position limit. Stops are risk-reducing and always allowed; an
        ``update_config`` is only gated when it declares a
        ``total_amount_quote`` above the position limit.

        Returns (allowed, reason).
        """
        from handlers.agents._shared import tool_call_input

        input_data = tool_call_input(tool_call)
        if input_data is None:
            return False, "Tool arguments could not be read"
        action = input_data.get("action", "")

        if action == "deploy":
            cap = input_data.get("max_global_drawdown_quote")
            if not cap:
                return False, (
                    "Bot deploy must declare max_global_drawdown_quote "
                    f"(≤ ${self.limits.max_position_size_quote:.2f}) so the "
                    "platform kill switch bounds the loss"
                )
            if float(cap) > self.limits.max_position_size_quote:
                return False, (
                    f"max_global_drawdown_quote ${float(cap):.2f} exceeds "
                    f"position limit ${self.limits.max_position_size_quote:.2f}"
                )
        elif action == "update_config":
            amount = float(
                (input_data.get("config_data") or {}).get("total_amount_quote", 0) or 0
            )
            if amount > self.limits.max_position_size_quote:
                return False, (
                    f"update_config total_amount_quote ${amount:.2f} exceeds "
                    f"position limit ${self.limits.max_position_size_quote:.2f}"
                )

        return True, ""


def auto_approve_with_risk_check(
    risk_engine: RiskEngine,
    risk_state: RiskState,
    execution_mode: str = "loop",
    ledger: "BotLedger | None" = None,
    agent_id: str = "",
    price_client: Any = None,
):
    """Build a permission callback that auto-approves safe tools and risk-checks dangerous ones.

    ``ledger`` (FEAT-017) scopes bot ownership: with one, a ``manage_bots`` action
    that deploys or mutates a bot outside the session's namespace is cancelled and
    recorded. ``None`` (consults, delegations, chat, executor-mode agents) keeps
    today's behavior exactly.

    ``agent_id``, when given, is the session's own ``controller_id`` tag and an
    executor create must carry exactly it. The tag is model-supplied (the prompt
    merely asks for it) and is the sole link between a real position and the
    session that opened it, so checking only that *some* tag is present lets a
    mistyped one open a live position no session can ever claim. Empty (consults,
    chat, tests) keeps the presence-only check.
    """
    from handlers.agents._shared import (
        DANGEROUS_BOT_ACTIONS,
        is_dangerous_tool_call,
        tool_call_input,
        tool_call_name,
    )

    async def callback(tool_call: dict, options: list[dict]) -> dict:
        if is_dangerous_tool_call(tool_call):
            tool_name = tool_call_name(tool_call)

            # A dangerous tool whose arguments we can't read can't be risk-checked
            # either, so it never runs unattended: cancel instead of falling
            # through to the auto-approve tail (SEC-093).
            input_data = tool_call_input(tool_call)
            if input_data is None:
                log.warning("Blocked %s: tool arguments could not be read", tool_name)
                return {"outcome": {"outcome": "cancelled"}}

            # Dry-run mode: block ALL mutating actions
            if execution_mode == "dry_run":
                if tool_name == "manage_executors":
                    action = input_data.get("action", "")
                    if action in ("create", "stop"):
                        log.info("Dry-run mode: blocked manage_executors(%s)", action)
                        return {"outcome": {"outcome": "cancelled"}}
                elif tool_name == "manage_bots":
                    action = input_data.get("action", "")
                    if action in DANGEROUS_BOT_ACTIONS:
                        log.info("Dry-run mode: blocked manage_bots(%s)", action)
                        return {"outcome": {"outcome": "cancelled"}}
                elif tool_name in (
                    "place_order",
                    "manage_gateway_swaps",
                    "manage_gateway_clmm",
                ):
                    log.info("Dry-run mode: blocked %s", tool_name)
                    return {"outcome": {"outcome": "cancelled"}}

            # For executor actions, run risk check
            if tool_name == "manage_executors":
                action = input_data.get("action", "")

                # Validate controller_id on create — presence AND value, since
                # this tag is what per-session PnL attribution keys on.
                if action == "create":
                    executor_config = input_data.get("executor_config", {})
                    tag = str(executor_config.get("controller_id") or "")
                    if not tag:
                        log.warning("Blocked executor create: missing controller_id")
                        return {"outcome": {"outcome": "cancelled"}}
                    if agent_id and tag != agent_id:
                        log.warning(
                            "Blocked executor create: controller_id %r is not this "
                            "session's agent_id %r — the position would be "
                            "unattributable",
                            tag,
                            agent_id,
                        )
                        return {"outcome": {"outcome": "cancelled"}}

                planned_amount_quote = None
                if action == "create":
                    try:
                        planned_amount_quote = await _planned_amount_quote(
                            input_data, price_client
                        )
                    except Exception as exc:
                        log.warning("Blocked executor create: %s", exc)
                        return {"outcome": {"outcome": "cancelled"}}

                allowed, reason = risk_engine.check_executor_action(
                    tool_call, risk_state, planned_amount_quote
                )
                if not allowed:
                    log.warning("Risk engine blocked tool call: %s", reason)
                    return {"outcome": {"outcome": "cancelled"}}

            # Bot deploys place real capital via controllers — bound the loss
            # (declared drawdown kill switch) since the amount isn't in the call
            if tool_name == "manage_bots":
                # Ownership first: an agent may only touch bots in its own
                # namespace. Read-only actions (status/logs/get_config) are not
                # in DANGEROUS_BOT_ACTIONS, so it still sees the whole fleet.
                # Only a session that declared controller mode is held to the
                # namespace; every session still has its deploys recorded below.
                if ledger is not None and ledger.enforced:
                    action = input_data.get("action", "")
                    if action in DANGEROUS_BOT_ACTIONS:
                        bot_name = input_data.get("bot_name", "") or ""
                        if not ledger.owns(bot_name):
                            log.warning(
                                "Ownership: blocked manage_bots(%s) on '%s' "
                                "(namespace %s)",
                                action,
                                bot_name,
                                ledger.namespace,
                            )
                            ledger.note_violation(bot_name, action)
                            return {"outcome": {"outcome": "cancelled"}}

                allowed, reason = risk_engine.check_bot_action(tool_call)
                if not allowed:
                    log.warning("Risk engine blocked tool call: %s", reason)
                    return {"outcome": {"outcome": "cancelled"}}

                # Recorded only once the call is actually going through, so a
                # risk-rejected deploy never lands in the ledger.
                if ledger is not None:
                    if input_data.get("action", "") == "deploy":
                        ledger.note_deploy(input_data.get("bot_name", "") or "")

            # Block direct order placement entirely
            if tool_name == "place_order":
                log.warning("Blocked direct place_order (agents must use executors)")
                return {"outcome": {"outcome": "cancelled"}}

        # Auto-approve everything else
        for opt in options:
            if opt.get("kind") in ("allow_once", "allow_always"):
                return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
        if options:
            return {
                "outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}
            }
        return {"outcome": {"outcome": "cancelled"}}

    return callback


async def _planned_amount_quote(input_data: dict[str, Any], client: Any) -> float:
    """Value a supported executor create in its quote token."""

    config = input_data.get("executor_config") or {}
    executor_type = (
        input_data.get("executor_type")
        or config.get("type")
        or config.get("executor_type")
    )

    if "total_amount_quote" in config:
        amount = float(config["total_amount_quote"])
    elif executor_type == "dca_executor":
        amounts = [float(value) for value in config.get("amounts_quote", [])]
        if any(not math.isfinite(value) or value <= 0 for value in amounts):
            raise ValueError("amounts_quote must contain positive finite numbers")
        amount = sum(amounts)
    elif executor_type in {"order_executor", "position_executor", "lp_executor"}:
        base = float(
            config.get("base_amount", 0)
            if executor_type == "lp_executor"
            else config.get("amount", 0)
        )
        quote = float(config.get("quote_amount", 0))
        if base < 0 or quote < 0:
            raise ValueError("executor amounts cannot be negative")
        if base:
            if client is None:
                raise ValueError("Hummingbot price client is unavailable")
            from condor.fetchers.market_data import fetch_current_price

            price = await fetch_current_price(
                client,
                config.get("connector_name", ""),
                config.get("trading_pair", ""),
            )
            if price is None:
                raise ValueError("reference price is unavailable")
            price = float(price)
            if not math.isfinite(price) or price <= 0:
                raise ValueError("reference price must be a positive finite number")
            amount = quote + base * price
        else:
            amount = quote
    else:
        raise ValueError(f"unsupported executor type: {executor_type or 'unknown'}")

    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("planned quote amount must be a positive finite number")
    return amount
