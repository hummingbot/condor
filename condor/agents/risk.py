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

    def _drawdown_pct(self, tracker: Any) -> float:
        """Peak-to-current PnL drawdown as a percent of ALLOCATED CAPITAL.

        The tracker's own get_drawdown_pct measures the drop against peak *PnL*,
        which is meaningless for a strategy whose PnL oscillates near zero — a
        $0.04 dip from a $0.01 peak reads as 400% and permanently pauses a market
        maker. Bounding by max_position_size_quote makes it an actual fraction of
        capital at risk. Falls back to the tracker's metric when no capital base
        is declared or the series is unavailable (e.g. experiment NullTracker)."""
        capital = float(self.limits.max_position_size_quote or 0)
        if capital <= 0:
            return tracker.get_drawdown_pct()
        try:
            series = [float(s.get("pnl", 0)) for s in tracker.get_pnl_series()]
        except Exception:
            return tracker.get_drawdown_pct()
        if not series:
            return 0.0
        peak = max(series + [0.0])  # high-water mark, from break-even up
        return max(0.0, (peak - series[-1]) / capital * 100)

    def get_state(self, tracker: Any) -> RiskState:
        """Compute current risk metrics from tracker data."""
        state = RiskState()
        state._limits = self.limits

        try:
            state.total_exposure = tracker.get_total_exposure()
            state.executor_count = tracker.get_open_executor_count()
            state.drawdown_pct = self._drawdown_pct(tracker)
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
        from condor.agents.gating import tool_call_input

        input_data = tool_call_input(tool_call)
        if input_data is None:
            # Arguments unavailable: cannot compute risk — fail CLOSED.
            return False, "manage_executors arguments unavailable — failing closed"
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

        # Check position size: the executor type's own RiskDeclaration computes
        # the notional from the declared config; if it can't be computed, fail
        # CLOSED.
        try:
            amount = self._native_notional(input_data)
        except Exception as exc:
            return False, f"Cannot compute risk for native executor: {exc}"

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

    @staticmethod
    def _native_notional(input_data: dict) -> float:
        """Notional of a condor-native create, from the type's RiskDeclaration.

        Note: the declaration is in the pool's QUOTE units; the position
        limit is nominally USD. Exact for USD-quoted pools, approximate
        otherwise.
        """
        from condor.executors.runtime import _EXECUTOR_TYPES

        executor_type = input_data.get("executor_type", "")
        if executor_type not in _EXECUTOR_TYPES:
            raise ValueError(f"unknown native executor type: {executor_type!r}")
        config_cls, _ = _EXECUTOR_TYPES[executor_type]
        config = config_cls(**(input_data.get("config") or {}))
        from condor.executors.base import validate_risk_declaration

        declaration = validate_risk_declaration(config.risk_declaration())
        return float(declaration.max_notional_quote)

def risk_gate(
    risk_engine: RiskEngine,
    risk_state: RiskState,
    experiment: bool = False,
):
    """Build a permission callback that auto-approves safe tools and risk-checks dangerous ones.

    The ONE shared risk policy (refactor-02 §4.1) — the caller chooses the state
    seed: journal-derived for tick sessions (real exposure/count carried over),
    ``RiskState()`` at zero when the caps act as a fresh per-run budget.
    ``experiment=True`` additionally cancels every mutating action — the
    in-memory dry-run policy (:mod:`condor.agents.experiment`).
    """
    from condor.agents.gating import (
        is_dangerous_tool_call,
        tool_call_input,
        tool_call_name,
    )

    async def callback(tool_call: dict, options: list[dict]) -> dict:
        if is_dangerous_tool_call(tool_call):
            tool_name = tool_call_name(tool_call)

            if tool_name == "manage_executors":
                input_data = tool_call_input(tool_call)
                action = (input_data or {}).get("action", "")

                # Experiment mode: block ALL mutating actions — including
                # calls whose arguments are unavailable (fail closed).
                if experiment and (input_data is None or action in ("create", "stop")):
                    log.info("Experiment mode: blocked manage_executors(%s)", action)
                    return {"outcome": {"outcome": "cancelled"}}

                allowed, reason = risk_engine.check_executor_action(
                    tool_call, risk_state
                )
                if not allowed:
                    log.warning("Risk engine blocked tool call: %s", reason)
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
