"""Pending approvals as addressable entries, not closures.

"May I run this dangerous tool?" used to live inside a ``Future`` captured by a
per-connection callback (the web path) or a ``functools.partial`` bound to a live
``Bot`` (the Telegram path). Neither can cross a process boundary, and the web
one died with the WebSocket that created it — reloading the dashboard mid-approval
stranded the request until it timed out.

Here an approval is a registry entry with an id. Rendering it is a *delivery
channel*; answering it is ``resolve(id, ...)`` from any surface. The agent awaits
``wait_for(id)`` and neither knows nor cares who answers.

Deliberately in memory and not persisted: a confirmation is only meaningful while
the requesting agent is still awaiting it. After a restart that agent is gone, so
a persisted pending entry would be a lie.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from condor.runtime.timeouts import TIMEOUTS

log = logging.getLogger(__name__)

# How long a human has to answer before the request is denied. Sourced from the
# shared timeout policy so every deadline lives in one file.
CONFIRMATION_TIMEOUT = TIMEOUTS.confirmation

# How often expired entries are swept out of the registry.
CLEANUP_INTERVAL = 30  # seconds


class ConfirmationStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    TIMEOUT = "timeout"


@dataclass
class PendingConfirmation:
    """One approval request, awaiting a human."""

    id: str
    session_key: str
    user_id: int
    summary: str
    tool_call: dict = field(default_factory=dict)
    options: list[dict] = field(default_factory=list)
    status: ConfirmationStatus = ConfirmationStatus.PENDING
    selected_option_id: str = ""
    created_at: float = field(default_factory=time.time)
    timeout_seconds: int = CONFIRMATION_TIMEOUT
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def approved(self) -> bool:
        return self.status is ConfirmationStatus.APPROVED

    @property
    def expires_at(self) -> float:
        return self.created_at + self.timeout_seconds

    def to_wire(self) -> dict:
        """JSON-safe view for the API and the delivery channels."""
        return {
            "id": self.id,
            "session_key": self.session_key,
            "user_id": self.user_id,
            "summary": self.summary,
            "tool_call": self.tool_call,
            "options": self.options,
            "status": self.status.value,
            "selected_option_id": self.selected_option_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


class Channel(Protocol):
    """Renders a pending confirmation on one surface."""

    async def deliver(self, pending: PendingConfirmation) -> None: ...


class ConfirmationRegistry:
    """Process-global registry of pending approvals."""

    def __init__(self):
        self._entries: dict[str, PendingConfirmation] = {}
        self._cleanup_task: asyncio.Task | None = None

    # ── Lifecycle ──

    def register(
        self,
        session_key: str,
        user_id: int,
        summary: str,
        tool_call: dict,
        options: list[dict],
        timeout_seconds: int = CONFIRMATION_TIMEOUT,
    ) -> PendingConfirmation:
        """Create a pending entry. Synchronous and I/O-free by design.

        The entry must exist before any channel is told about it; otherwise a
        fast surface could resolve an id the registry has not stored yet.
        """
        pending = PendingConfirmation(
            id=str(uuid.uuid4())[:8],
            session_key=session_key,
            user_id=user_id,
            summary=summary,
            tool_call=tool_call,
            options=options,
            timeout_seconds=timeout_seconds,
        )
        self._entries[pending.id] = pending
        return pending

    async def wait_for(self, confirmation_id: str) -> PendingConfirmation | None:
        """Block until the entry is resolved or its TTL expires.

        A TTL expiry becomes ``TIMEOUT``, never an approval: every outcome that
        is not an explicit approval denies.
        """
        pending = self._entries.get(confirmation_id)
        if pending is None:
            return None

        remaining = pending.expires_at - time.time()
        try:
            await asyncio.wait_for(pending._event.wait(), timeout=max(remaining, 0))
        except asyncio.TimeoutError:
            if pending.status is ConfirmationStatus.PENDING:
                pending.status = ConfirmationStatus.TIMEOUT
                log.info("Confirmation %s timed out; denying", confirmation_id)
        return pending

    async def resolve(
        self,
        confirmation_id: str,
        approved: bool,
        by_user_id: int | None = None,
        option_id: str = "",
    ) -> bool:
        """Answer a pending confirmation.

        Idempotent: the first answer wins, so a Telegram tap and a dashboard
        click racing each other cannot double-resolve. Returns False when the
        id is unknown, already answered, or answered by the wrong user.
        """
        pending = self._entries.get(confirmation_id)
        if pending is None:
            return False
        if pending.status is not ConfirmationStatus.PENDING:
            return False
        if by_user_id is not None and pending.user_id != by_user_id:
            log.warning(
                "User %s tried to resolve confirmation %s owned by %s",
                by_user_id,
                confirmation_id,
                pending.user_id,
            )
            return False

        pending.status = (
            ConfirmationStatus.APPROVED if approved else ConfirmationStatus.DENIED
        )
        pending.selected_option_id = option_id
        pending._event.set()
        return True

    def get(self, confirmation_id: str) -> PendingConfirmation | None:
        return self._entries.get(confirmation_id)

    def list_pending(self, user_id: int | None = None) -> list[PendingConfirmation]:
        now = time.time()
        return [
            p
            for p in self._entries.values()
            if p.status is ConfirmationStatus.PENDING
            and p.expires_at > now
            and (user_id is None or p.user_id == user_id)
        ]

    def deny_pending_for_session(self, session_key: str) -> int:
        """Deny every request this session is still waiting on. Returns how many.

        Used when a turn is interrupted mid-tool-call: the agent is holding a
        dialog for work the user just abandoned, and nobody is going to answer
        it. Without this the interrupted turn keeps its confirmation alive until
        the TTL denies it, which also stops it noticing the cancel that
        interrupted it.
        """
        denied = 0
        for pending in list(self._entries.values()):
            if pending.session_key != session_key:
                continue
            if pending.status is not ConfirmationStatus.PENDING:
                continue
            pending.status = ConfirmationStatus.DENIED
            pending._event.set()
            denied += 1
        return denied

    def discard(self, confirmation_id: str) -> None:
        """Drop a finished entry once its waiter has consumed the outcome."""
        self._entries.pop(confirmation_id, None)

    # ── Background sweep ──

    def sweep(self) -> int:
        """Expire overdue entries and drop finished ones. Returns entries removed."""
        now = time.time()
        removed = 0
        for cid, pending in list(self._entries.items()):
            if (
                pending.status is ConfirmationStatus.PENDING
                and pending.expires_at <= now
            ):
                pending.status = ConfirmationStatus.TIMEOUT
                pending._event.set()  # wake any waiter immediately
            elif pending.status is not ConfirmationStatus.PENDING:
                # Give the waiter a grace period to read the outcome first.
                if now - pending.expires_at > CLEANUP_INTERVAL:
                    self._entries.pop(cid, None)
                    removed += 1
        return removed

    async def _cleanup_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(CLEANUP_INTERVAL)
                try:
                    self.sweep()
                except Exception:
                    log.exception("Confirmation sweep failed")
        except asyncio.CancelledError:
            pass

    async def start(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            log.info("Confirmation registry started")

    async def stop(self) -> None:
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        self._cleanup_task = None


# Process-global singleton, same pattern as the engine registry. A user's tap
# must resolve the pending entry even while the asking session is busy.
_registry = ConfirmationRegistry()


def get_registry() -> ConfirmationRegistry:
    return _registry


# ── The one permission callback ──


def _select_allow(options: list[dict]) -> dict:
    """ACP outcome approving the call, preferring an explicit allow option."""
    for opt in options:
        if opt.get("kind") in ("allow_once", "allow_always"):
            return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
    if options:
        return {"outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}}
    return {"outcome": {"outcome": "cancelled"}}


CANCELLED: dict[str, Any] = {"outcome": {"outcome": "cancelled"}}


def build_permission_callback(
    session_key: str,
    user_id: int,
    channels: list[Channel] | None = None,
    timeout_seconds: int = CONFIRMATION_TIMEOUT,
):
    """Build the permission callback every surface shares.

    Semantics preserved exactly from the previous per-surface closures:

    * a tool in ``BLOCKED_TOOLS`` is refused outright (it would bypass RBAC),
    * a tool that is not dangerous is auto-approved without touching the
      registry — this is the fast path DELEGATE relies on,
    * anything else waits for a human, and **every** non-approval outcome
      (denied, timed out, unknown id) cancels.
    """
    channels = channels or []

    async def permission_callback(
        tool_call: dict[str, Any], options: list[dict[str, Any]]
    ) -> dict[str, Any]:
        from handlers.agents._shared import BLOCKED_TOOLS, is_dangerous_tool_call
        from handlers.agents.confirmation import format_tool_summary

        tool_name = tool_call.get("tool", "") or tool_call.get("title", "")
        if tool_name in BLOCKED_TOOLS:
            log.warning("Blocked tool %s for session %s", tool_name, session_key)
            return CANCELLED

        if not is_dangerous_tool_call(tool_call):
            return _select_allow(options)

        pending = _registry.register(
            session_key=session_key,
            user_id=user_id,
            summary=format_tool_summary(tool_call),
            tool_call=tool_call,
            options=options,
            timeout_seconds=timeout_seconds,
        )

        # A channel that blows up must not swallow the request: the others
        # still render it, and the TTL denies if nobody can answer.
        results = await asyncio.gather(
            *(ch.deliver(pending) for ch in channels), return_exceptions=True
        )
        for result in results:
            if isinstance(result, Exception):
                log.warning("Confirmation channel failed to deliver", exc_info=result)

        if not channels:
            # Nothing can render it, so nobody can approve it. Deny now rather
            # than stalling the agent for the full TTL.
            log.warning(
                "No confirmation channel for session %s; denying %s",
                session_key,
                tool_name,
            )
            _registry.discard(pending.id)
            return CANCELLED

        resolved = await _registry.wait_for(pending.id)
        _registry.discard(pending.id)

        if resolved is not None and resolved.approved:
            if resolved.selected_option_id:
                return {
                    "outcome": {
                        "outcome": "selected",
                        "optionId": resolved.selected_option_id,
                    }
                }
            return _select_allow(options)
        return CANCELLED

    return permission_callback
