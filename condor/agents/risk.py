"""Risk engine -- pre-tick validation and guardrails.

Enforces position limits, daily loss caps, drawdown limits, executor counts,
and LLM cost caps.  Also provides a permission callback that auto-approves
safe tool calls and blocks dangerous ones that violate risk limits.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


def _as_nonnegative_float(value: Any, field_name: str) -> float:
    """Parse a numeric risk input without allowing NaN, infinity, or negatives."""
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"invalid {field_name}: {value!r}")
    return parsed


def _executor_controller_id(input_data: dict[str, Any]) -> str:
    """Accept the controller ID in either supported manage_executors location."""
    executor_config = input_data.get("executor_config") or {}
    return str(
        input_data.get("controller_id") or executor_config.get("controller_id") or ""
    ).strip()


def _executor_quote_exposure(input_data: dict[str, Any]) -> float:
    """Estimate executor notional in quote-token units.

    Most executors expose ``total_amount_quote``. LP executors instead use
    ``base_amount`` and ``quote_amount`` (older prompts used the reversed-name
    aliases). For a two-sided LP, conservatively value base inventory at the
    upper bound of its configured range.
    """
    config = input_data.get("executor_config") or {}

    zero_explicit_amount = False
    for field_name in ("total_amount_quote", "amount"):
        if field_name in config and config[field_name] is not None:
            explicit_amount = _as_nonnegative_float(config[field_name], field_name)
            if explicit_amount > 0:
                return explicit_amount
            zero_explicit_amount = True

    quote_field = next(
        (
            name
            for name in ("quote_amount", "amount_quote")
            if name in config and config[name] is not None
        ),
        None,
    )
    base_field = next(
        (
            name
            for name in ("base_amount", "amount_base")
            if name in config and config[name] is not None
        ),
        None,
    )
    if quote_field is None and base_field is None:
        if zero_explicit_amount:
            raise ValueError("executor exposure must be positive")
        raise ValueError("executor exposure is missing")

    quote_amount = (
        _as_nonnegative_float(config[quote_field], quote_field) if quote_field else 0.0
    )
    base_amount = (
        _as_nonnegative_float(config[base_field], base_field) if base_field else 0.0
    )
    if base_amount == 0:
        if quote_amount == 0:
            raise ValueError("executor exposure must be positive")
        return quote_amount

    try:
        lower = _as_nonnegative_float(config.get("lower_price"), "lower_price")
        upper = _as_nonnegative_float(config.get("upper_price"), "upper_price")
    except ValueError as exc:
        raise ValueError(
            "cannot value LP base_amount without a valid price range"
        ) from exc
    if lower <= 0 or upper <= 0 or lower >= upper:
        raise ValueError("cannot value LP base_amount without a valid price range")

    return quote_amount + base_amount * upper


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
        self, tool_call: dict, current_state: RiskState
    ) -> tuple[bool, str]:
        """Check if an executor creation is within risk limits.

        On approval of a "create", accumulates it into ``current_state``
        (executor count and exposure) so subsequent checks within the same
        tick see the running totals instead of the frozen per-tick snapshot.
        The state is recomputed from the journal at the start of each tick.

        Returns (allowed, reason).
        """
        input_data = tool_call.get("input", {})
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

        # Check position size. Limits and exposure are denominated in the
        # executor pair's quote token (SOL for ANSEM-SOL), not necessarily USD.
        try:
            amount = _executor_quote_exposure(input_data)
        except ValueError as exc:
            return False, f"Cannot determine executor exposure: {exc}"

        if current_state.total_exposure + amount > self.limits.max_position_size_quote:
            return False, (
                "Would exceed position limit: "
                f"{current_state.total_exposure + amount:.6f} quote units > "
                f"{self.limits.max_position_size_quote:.6f} quote units"
            )

        # Approved: accumulate into the snapshot so the next create in this
        # tick is gated against the running totals, not the pre-tick numbers.
        current_state.executor_count += 1
        current_state.total_exposure += amount

        return True, ""


def auto_approve_with_risk_check(
    risk_engine: RiskEngine,
    risk_state: RiskState,
    execution_mode: str = "loop",
):
    """Build a permission callback that auto-approves safe tools and risk-checks dangerous ones."""
    from handlers.agents._shared import is_dangerous_tool_call

    async def callback(tool_call: dict, options: list[dict]) -> dict:
        if is_dangerous_tool_call(tool_call):
            raw_name = tool_call.get("tool", "") or tool_call.get("title", "")
            tool_name = raw_name.rsplit("__", 1)[-1] if "__" in raw_name else raw_name

            # Dry-run mode: block ALL mutating actions
            if execution_mode == "dry_run":
                if tool_name == "manage_executors":
                    input_data = tool_call.get("input", {})
                    action = input_data.get("action", "")
                    if action in ("create", "stop"):
                        log.info("Dry-run mode: blocked manage_executors(%s)", action)
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
                input_data = tool_call.get("input", {})
                action = input_data.get("action", "")

                # Validate controller_id on create
                if action == "create":
                    if not _executor_controller_id(input_data):
                        log.warning("Blocked executor create: missing controller_id")
                        return {"outcome": {"outcome": "cancelled"}}

                allowed, reason = risk_engine.check_executor_action(
                    tool_call, risk_state
                )
                if not allowed:
                    log.warning("Risk engine blocked tool call: %s", reason)
                    return {"outcome": {"outcome": "cancelled"}}

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
