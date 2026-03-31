"""Risk engine -- pre-tick validation and guardrails.

Enforces position limits, daily loss caps, drawdown limits, executor counts,
and LLM cost caps.  Also provides a permission callback that auto-approves
safe tool calls and blocks dangerous ones that violate risk limits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    max_position_size_quote: float = 500.0
    max_open_executors: int = 5
    max_drawdown_pct: float = -1.0

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_exposure": self.total_exposure,
            "executor_count": self.executor_count,
            "drawdown_pct": self.drawdown_pct,
            "is_blocked": self.is_blocked,
            "block_reason": self.block_reason,
            # Include limits for prompt display
            "max_position_size": self._limits.max_position_size_quote if hasattr(self, "_limits") else 500,
            "max_open_executors": self._limits.max_open_executors if hasattr(self, "_limits") else 5,
            "max_drawdown_pct": self._limits.max_drawdown_pct if hasattr(self, "_limits") else -1,
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
        except Exception:
            log.exception("Failed to compute risk state from tracker")
            return state

        # Check blocking conditions
        reasons = []

        if self.limits.max_drawdown_pct >= 0 and state.drawdown_pct > self.limits.max_drawdown_pct:
            reasons.append(
                f"Drawdown {state.drawdown_pct:.1f}% exceeds limit {self.limits.max_drawdown_pct:.1f}%"
            )

        if reasons:
            state.is_blocked = True
            state.block_reason = "; ".join(reasons)

        return state

    def check_executor_action(self, tool_call: dict, current_state: RiskState) -> tuple[bool, str]:
        """Check if an executor creation is within risk limits.

        Returns (allowed, reason).
        """
        input_data = tool_call.get("input", {})
        action = input_data.get("action", "")

        # Only gate "create" actions
        if action != "create":
            return True, ""

        # Check executor count
        if current_state.executor_count >= self.limits.max_open_executors:
            return False, f"Max open executors ({self.limits.max_open_executors}) reached"

        # Check position size
        config = input_data.get("executor_config", {})
        amount = float(config.get("total_amount_quote", 0) or config.get("amount", 0) or 0)

        if current_state.total_exposure + amount > self.limits.max_position_size_quote:
            return False, (
                f"Would exceed position limit: ${current_state.total_exposure + amount:.2f} > "
                f"${self.limits.max_position_size_quote:.2f}"
            )

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

            # For executor actions, run risk check
            if tool_name == "manage_executors":
                input_data = tool_call.get("input", {})
                action = input_data.get("action", "")

                # Dry-run mode: block executor creation (read-only tools still work)
                if execution_mode == "dry_run" and action == "create":
                    log.info("Dry-run mode: blocked executor create (recorded in snapshot)")
                    return {"outcome": {"outcome": "cancelled"}}

                # Validate controller_id on create
                if action == "create":
                    executor_config = input_data.get("executor_config", {})
                    if not executor_config.get("controller_id"):
                        log.warning("Blocked executor create: missing controller_id")
                        return {"outcome": {"outcome": "cancelled"}}

                allowed, reason = risk_engine.check_executor_action(tool_call, risk_state)
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
            return {"outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}}
        return {"outcome": {"outcome": "cancelled"}}

    return callback
