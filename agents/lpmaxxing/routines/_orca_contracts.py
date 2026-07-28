from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SessionStatus(StrEnum):
    RUNNING = "running"
    STOPPING = "stopping"
    STOP_PENDING = "stop_pending"
    CYCLE_COMPLETE = "cycle_complete"
    MANUAL_REVIEW = "manual_review"


class PositionPhase(StrEnum):
    PREFLIGHT_READY = "preflight_ready"
    REBALANCE_REQUIRED = "rebalance_required"
    REBALANCE_SUBMISSION_INTENT = "rebalance_submission_intent"
    REBALANCE_SUBMITTED = "rebalance_submitted"
    REBALANCE_SUBMISSION_UNCERTAIN = "rebalance_submission_uncertain"
    REBALANCE_CONFIRMED = "rebalance_confirmed"
    REBALANCE_CONFIRMED_WAITING_BALANCE = "rebalance_confirmed_waiting_balance"
    REBALANCE_FAILED = "rebalance_failed"
    REBALANCE_BLOCKED = "rebalance_blocked"
    OPENED = "opened"
    SUPERVISING = "supervising"
    CLOSING = "closing"
    TERMINAL = "terminal"


class NextAction(StrEnum):
    RUN_POOL_SCAN = "run-pool-scan"
    RUN_PREFLIGHT = "run-preflight"
    RUN_REBALANCE = "run-rebalance"
    CREATE_EXECUTOR = "create-executor"
    RESCAN = "rescan"
    RERUN_PREFLIGHT = "rerun-preflight"
    MANUAL_REVIEW = "manual-review"
    CONTINUE = "continue"
    CLOSE = "close"
    WRITE_AUDIT = "write-audit"
    RESUME_REBALANCE = "resume-rebalance"
    RESUME_PREFLIGHT = "resume-preflight"
    WAIT_NEXT_CYCLE = "wait-next-cycle"
    STOP_AGENT = "stop-agent"
    NO_ACTION = "no-action"
    SESSION_STOP_RECORDED = "session-stop-recorded"
    NO_ACTIVE_POSITION = "no-active-position"


class RoutineOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: int = Field(default=1, frozen=True)
    routine: str
    next_action: NextAction = NextAction.NO_ACTION
    reason: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    mutation: dict[str, Any] = Field(default_factory=dict)
    position_number: int | None = None
    executor_id: str | None = None


def attach_outcome(
    payload: dict[str, Any],
    *,
    routine: str,
    next_action: NextAction,
    reason: str,
    arguments: dict[str, Any] | None = None,
    mutation: dict[str, Any] | None = None,
    position_number: int | None = None,
    executor_id: str | None = None,
) -> dict[str, Any]:
    """Attach the versioned, single-command routine contract to a payload."""
    payload["outcome"] = RoutineOutcome(
        routine=routine,
        next_action=next_action,
        reason=reason,
        arguments=arguments or {},
        mutation=mutation or {},
        position_number=position_number,
        executor_id=executor_id,
    ).model_dump(mode="json")
    return payload
