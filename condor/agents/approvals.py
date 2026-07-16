"""Durable approval queue (§4.2).

Pending approvals are RunStore ``permission`` events (``status: pending``)
plus this in-process open-approvals index. Surfacing: a ``kind=approval``
outbox entry (reaches every §4.1 channel). Resolution: the
``resolve_approval`` MCP tool or a dashboard button — the resolving CHANNEL
is recorded.

Semantics (defined in the plan, tested in §11):

- **Default deny on timeout** (10 min, ``CONDOR_APPROVAL_TIMEOUT_S``),
  recorded as ``permission {decision: timeout_deny}``.
- **Durable continuation:** persisting the pending approval survives
  restart, but the suspended tool coroutine does not. Approval therefore
  does NOT resume a coroutine; it **grants a one-use token** keyed by
  ``tool_call_id``/``executor_id``, consumed by the blocked call (if still
  live) or by a retry of the same create (same ``executor_id``) within the
  SAME run. Resolution is idempotent (double resolve is a no-op;
  resolve-after-consume is a no-op).
- **Interrupted runs:** the startup sweep voids pending approvals
  (``interrupted_void`` — see ``RunStore.sweep_interrupted``); a relaunched
  run must re-request approval under its own create.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = int(os.environ.get("CONDOR_APPROVAL_TIMEOUT_S", "600"))


@dataclass
class PendingApproval:
    approval_id: str
    run_id: str
    agent_slug: str
    summary: str
    tool_call_id: str = ""
    executor_id: str = ""
    created_at: float = 0.0
    # resolution
    # approved | denied | timeout_deny | interrupted_void |
    # notification_failed_deny
    decision: str = ""
    channel: str = ""
    note: str = ""
    consumed: bool = False
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def to_dict(self) -> dict:
        return {
            "approval_id": self.approval_id,
            "run_id": self.run_id,
            "agent_slug": self.agent_slug,
            "summary": self.summary,
            "tool_call_id": self.tool_call_id,
            "executor_id": self.executor_id,
            "created_at": self.created_at,
            "decision": self.decision,
            "channel": self.channel,
            "note": self.note,
        }


class ApprovalManager:
    """In-process open-approvals index over the durable permission events."""

    def __init__(self):
        self._approvals: dict[str, PendingApproval] = {}

    # -- request side (the blocked tool call) ---------------------------

    async def request(
        self,
        *,
        run_id: str,
        agent_slug: str,
        summary: str,
        tool_call_id: str = "",
        executor_id: str = "",
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> bool:
        """Register a pending approval, surface it, and wait for resolution.

        Returns True iff approved. Emits the durable permission events and
        the ``kind=approval`` outbox entry. On timeout records
        ``timeout_deny`` and returns False (§4.2 default-deny).
        """
        import time

        from condor.agents.runstore import get_run_store

        # A one-use grant from a PRIOR resolution of the same create (same
        # executor_id, same run) short-circuits the wait: the blocked call
        # died but its approval was granted — the retry consumes it.
        if executor_id and self.consume_grant(run_id, executor_id=executor_id):
            return True

        ap = PendingApproval(
            approval_id=uuid.uuid4().hex[:12],
            run_id=run_id,
            agent_slug=agent_slug,
            summary=summary,
            tool_call_id=tool_call_id,
            executor_id=executor_id,
            created_at=time.time(),
        )
        self._approvals[ap.approval_id] = ap

        store = get_run_store()
        try:
            store.emit(
                run_id,
                "permission",
                {
                    "approval_id": ap.approval_id,
                    "status": "pending",
                    "summary": summary,
                },
                tool_call_id=tool_call_id or None,
                executor_id=executor_id or None,
            )
        except Exception:
            # No durable record → no approval flow; fail closed.
            log.exception("approval %s: pending event emit failed", ap.approval_id)
            self._approvals.pop(ap.approval_id, None)
            return False

        # Surface on every notification channel (§4.1).
        try:
            from condor.notifications import notify

            await notify(
                f"🔐 Approval needed [{ap.approval_id}] for {agent_slug}: "
                f"{summary}\nResolve with resolve_approval(approval_id="
                f"'{ap.approval_id}', decision='approve'|'deny') within "
                f"{timeout_s // 60} min (default: deny).",
                agent_id=run_id,
                kind="approval",
            )
        except Exception:
            log.exception("approval %s: outbox surfacing failed", ap.approval_id)
            # An approval the operator could not see must never authorize a
            # mutation or sit waiting as if it had been surfaced. Persist the
            # explicit deny when possible; regardless, return False.
            try:
                self._record_decision(
                    ap, "notification_failed_deny", channel="outbox_failure"
                )
            except Exception:
                log.exception(
                    "approval %s: failed to persist outbox-failure deny",
                    ap.approval_id,
                )
                self._approvals.pop(ap.approval_id, None)
            return False

        try:
            await asyncio.wait_for(ap._event.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            self._record_decision(ap, "timeout_deny", channel="timeout")
            return False

        if ap.decision == "approved":
            # The blocked call is live — it consumes the grant right here.
            ap.consumed = True
            return True
        return False

    # -- resolution side (human via tool/dashboard) ----------------------

    def resolve(
        self, approval_id: str, decision: str, *, channel: str, note: str = ""
    ) -> dict:
        """Resolve a pending approval. Idempotent: double-resolve and
        resolve-after-consume are no-ops reporting the recorded state."""
        if decision not in ("approve", "deny"):
            raise ValueError("decision must be 'approve' or 'deny'")
        ap = self._approvals.get(approval_id)
        if ap is None:
            return {"approval_id": approval_id, "error": "unknown or expired approval"}
        if ap.decision:
            return {**ap.to_dict(), "already_resolved": True}
        self._record_decision(
            ap,
            "approved" if decision == "approve" else "denied",
            channel=channel,
            note=note,
        )
        ap._event.set()
        return ap.to_dict()

    def _record_decision(
        self, ap: PendingApproval, decision: str, *, channel: str, note: str = ""
    ) -> None:
        from condor.agents.runstore import get_run_store

        # Permission is financial authority: durable event first, in-memory
        # grant second. If append/fsync fails the exception propagates, the
        # waiter stays blocked/default-deny, and no mutation is authorized.
        get_run_store().emit(
            ap.run_id,
            "permission",
            {
                "approval_id": ap.approval_id,
                "decision": decision,
                "channel": channel,
                **({"note": note} if note else {}),
            },
            tool_call_id=ap.tool_call_id or None,
            executor_id=ap.executor_id or None,
        )
        ap.decision = decision
        ap.channel = channel
        ap.note = note

    # -- one-use grants ---------------------------------------------------

    def consume_grant(
        self, run_id: str, *, executor_id: str = "", tool_call_id: str = ""
    ) -> bool:
        """Consume an unconsumed APPROVED grant for the same run keyed by
        executor_id/tool_call_id. One use only."""
        for ap in self._approvals.values():
            if ap.run_id != run_id or ap.decision != "approved" or ap.consumed:
                continue
            if (executor_id and ap.executor_id == executor_id) or (
                tool_call_id and ap.tool_call_id == tool_call_id
            ):
                ap.consumed = True
                return True
        return False

    # -- queries / lifecycle ----------------------------------------------

    def open_approvals(self) -> list[dict]:
        return [a.to_dict() for a in self._approvals.values() if not a.decision]

    def void_run(self, run_id: str) -> int:
        """Void every pending approval of one run (run ended without
        consuming them). The RunStore sweep handles the restart case; this
        handles in-process run end."""
        n = 0
        for ap in self._approvals.values():
            if ap.run_id == run_id and not ap.decision:
                self._record_decision(ap, "interrupted_void", channel="run_end")
                ap._event.set()
                n += 1
        return n

    def drop_resolved(self, max_age_s: float = 3600.0) -> None:
        """Prune long-resolved entries (the durable record is the stream)."""
        import time

        cutoff = time.time() - max_age_s
        for aid in [
            a.approval_id
            for a in self._approvals.values()
            if a.decision and a.created_at < cutoff
        ]:
            self._approvals.pop(aid, None)


_manager: Optional[ApprovalManager] = None


def get_approval_manager() -> ApprovalManager:
    global _manager
    if _manager is None:
        _manager = ApprovalManager()
    return _manager


def set_approval_manager(manager: Optional[ApprovalManager]) -> None:
    """Test seam."""
    global _manager
    _manager = manager
