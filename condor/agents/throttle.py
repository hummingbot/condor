"""Throttle accounting: who spent a host's budget, and who the venue refused.

The reader half of :mod:`mcp_servers.hummingbot_api.throttle`. That module
appends one line per agent call — its outcome, its venue, its host and the
agent and session that made it — and this one folds the log into counters three
surfaces ask different questions of (FEAT-138, GitHub #276):

- **per venue**, so "this host is refusing us" is a *venue condition* and not an
  agent error. The agent did nothing wrong, and labelling it as an agent
  failure is what made throttling read as a mysterious strategy bug.
- **per (agent, host)**, so the agent consuming a host's budget can be named.
  At the Agent Builder Cup nothing could show that one 1.0s poller was the sole
  consumer starving a host ([[CORR-726]]).
- **per session**, so a tick's refusals reach that engine's ``RefusalLog`` and
  its journal, and the agent is told next tick that the venue refused rather
  than being left to read a slow call as a vendor outage.

Counting only. There is no limiter here and this module cannot refuse anything:
a Condor-side limiter has to guess the venue's budget, and guessing it wrong
starves a healthy agent. That is a separate decision; this run counts first.

The log is a shared append-only file read from a byte offset, so folding is
incremental and one read serves every caller. A file that shrank is a reset
(the writer truncates an oversized log) and restarts the offset: counters
already folded stay folded, so the cost is at most the events of one window.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from condor.paths import data_dir
from mcp_servers.hummingbot_api.throttle import (
    OUTCOME_ERROR,
    OUTCOME_OK,
    OUTCOME_REFUSED,
    THROTTLE_LOG_ENV,
    VENUE_UNKNOWN,
    is_throttle_refusal,
)

__all__ = [
    "CallCounts",
    "ThrottleLedger",
    "is_throttle_refusal",
    "ledger",
    "throttle_log_path",
    "OUTCOME_ERROR",
    "OUTCOME_OK",
    "OUTCOME_REFUSED",
    "VENUE_UNKNOWN",
]

#: How long a refusal keeps a venue reading as throttled. A venue that refused
#: an hour ago is reported with its counts and condition ``ok``: the counts are
#: the record, the condition is the thing an operator can act on now.
THROTTLED_FOR_SECONDS = 900.0

#: Refusals held per session for an engine that has not drained them yet. Same
#: bound as ``RefusalLog``'s, and for the same reason: the prompt shows a
#: handful, the rest exist so a burst is still visible. Bounded per session so a
#: session nobody drains (a chat seat, an engine that died) cannot grow.
MAX_PENDING_REFUSALS = 20


def throttle_log_path() -> Path:
    """The shared call log. ``$CONDOR_THROTTLE_LOG`` overrides it.

    Under :func:`condor.paths.data_dir` because that is exactly what it is for:
    what the bot accumulated while it ran, written and read across the main
    process and every MCP subprocess. The override is read on every call so the
    suite (and a second install sharing a checkout) can repoint it.
    """
    override = os.environ.get(THROTTLE_LOG_ENV, "").strip()
    return Path(override).expanduser() if override else data_dir() / "throttle.jsonl"


@dataclass
class CallCounts:
    """What one (caller, host, venue) did.

    ``attempts`` is every call, so ``attempts == successes + refusals + errors``
    always holds — which is what makes "8 attempts, 0 successes, 8 refusals"
    reproducible from the counters rather than from a log sample.
    """

    attempts: int = 0
    successes: int = 0
    refusals: int = 0
    errors: int = 0
    last_refusal_at: float = 0.0
    last_reason: str = ""

    def note(self, outcome: str, at: float, reason: str) -> None:
        self.attempts += 1
        if outcome == OUTCOME_OK:
            self.successes += 1
            return
        if outcome == OUTCOME_REFUSED:
            self.refusals += 1
            if at >= self.last_refusal_at:
                self.last_refusal_at = at
                self.last_reason = reason
            return
        self.errors += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "successes": self.successes,
            "refusals": self.refusals,
            "errors": self.errors,
            "last_refusal_at": self.last_refusal_at,
            "last_reason": self.last_reason,
        }


#: A counter key: the agent slug, its session id, the API host, the venue.
_Key = tuple[str, str, str, str]


@dataclass
class ThrottleLedger:
    """The shared log, folded. One instance per process (see :func:`ledger`).

    Counters are keyed per ``(agent, session, host, venue)`` and live for the
    life of the process, so the key count grows with the sessions that have
    ever run rather than with the calls they made — a handful of integers per
    session and venue, which is what makes "attempts since this install came
    up" answerable at all.
    """

    path: Path | None = None
    _counts: dict[_Key, CallCounts] = field(default_factory=dict, init=False)
    _offset: int = field(default=0, init=False)
    _pending: dict[str, list[dict[str, Any]]] = field(default_factory=dict, init=False)

    # ── reading ──

    def ingest(self) -> list[dict[str, Any]]:
        """Fold every line written since the last read. Returns those events.

        Never raises: a missing, truncated or half-written log leaves the
        counters as they were. Called by whoever wants fresh numbers — the
        engine before it drains its tick's refusals, the route before it
        answers — and the fold is shared, while each session's refusals are
        queued for that session alone so two callers cannot consume each
        other's.
        """
        events: list[dict[str, Any]] = []
        try:
            path = self.path or throttle_log_path()
            if not path.exists():
                return events
            size = path.stat().st_size
            if size < self._offset:
                # The writer reset an oversized log; start again from its head.
                self._offset = 0
            if size == self._offset:
                return events
            with open(path, "r", encoding="utf-8") as fh:
                fh.seek(self._offset)
                raw = fh.read()
                self._offset = fh.tell()
        except OSError:
            return events

        # A trailing partial line can only happen if a writer was interrupted
        # mid-write; re-reading it is impossible once the offset has moved, so
        # an unparseable line is dropped rather than retried.
        for line in raw.splitlines():
            event = _parse(line)
            if event is None:
                continue
            events.append(event)
            self._fold(event)
        return events

    def _fold(self, event: dict[str, Any]) -> None:
        key: _Key = (
            event["agent"],
            event["agent_id"],
            event["host"],
            event["venue"],
        )
        counts = self._counts.get(key)
        if counts is None:
            counts = self._counts[key] = CallCounts()
        counts.note(event["outcome"], event["at"], event["reason"])
        if event["outcome"] == OUTCOME_REFUSED:
            queue = self._pending.setdefault(event["agent_id"], [])
            queue.append(event)
            del queue[:-MAX_PENDING_REFUSALS]

    # ── the three questions ──

    def drain_refusals(self, agent_id: str) -> list[dict[str, Any]]:
        """This session's venue refusals, clearing them. Oldest first."""
        return self._pending.pop(agent_id or "", [])

    def venue_conditions(self, now: float | None = None) -> list[dict[str, Any]]:
        """Every (host, venue) with its counts and its condition.

        ``condition`` is ``throttled`` while a refusal is inside
        :data:`THROTTLED_FOR_SECONDS`, else ``ok``. Folded across agents on
        purpose: a venue refusing is a fact about the venue, and splitting it
        per agent is what let it read as one agent's bug.
        """
        at = time.time() if now is None else now
        folded: dict[tuple[str, str], CallCounts] = {}
        for (_agent, _agent_id, host, venue), counts in self._counts.items():
            _merge(folded.setdefault((host, venue), CallCounts()), counts)
        rows = [
            {
                "host": host,
                "venue": venue,
                "condition": (
                    "throttled"
                    if counts.refusals
                    and at - counts.last_refusal_at <= THROTTLED_FOR_SECONDS
                    else "ok"
                ),
                **counts.as_dict(),
            }
            for (host, venue), counts in folded.items()
        ]
        rows.sort(key=lambda r: (-r["refusals"], r["host"], r["venue"]))
        return rows

    def caller_volume(self) -> list[dict[str, Any]]:
        """Call volume per (agent, session, host) — who spent whose budget.

        The question [[CORR-726]]'s incident could not answer. Kept per session
        as well as per agent because two sessions of one agent are two
        consumers, and an operator throttling one of them needs to know which.
        """
        folded: dict[tuple[str, str, str], CallCounts] = {}
        for (agent, agent_id, host, _venue), counts in self._counts.items():
            _merge(folded.setdefault((agent, agent_id, host), CallCounts()), counts)
        rows = [
            {"agent": agent, "agent_id": agent_id, "host": host, **counts.as_dict()}
            for (agent, agent_id, host), counts in folded.items()
        ]
        rows.sort(key=lambda r: (-r["attempts"], r["agent"], r["agent_id"]))
        return rows

    def session_venues(
        self, agent_id: str, now: float | None = None
    ) -> list[dict[str, Any]]:
        """The venues this session has been refused by, newest refusal first.

        What a running instance reports (``RunningInstance.throttled_venues``):
        only venues that actually refused *this* session, each with the
        attempts/successes/refusals triple behind the claim.
        """
        at = time.time() if now is None else now
        rows = []
        for (_agent, sid, host, venue), counts in self._counts.items():
            if sid != (agent_id or "") or not counts.refusals:
                continue
            rows.append(
                {
                    "host": host,
                    "venue": venue,
                    "condition": (
                        "throttled"
                        if at - counts.last_refusal_at <= THROTTLED_FOR_SECONDS
                        else "ok"
                    ),
                    **counts.as_dict(),
                }
            )
        rows.sort(key=lambda r: (-r["last_refusal_at"], r["venue"]))
        return rows


def _merge(into: CallCounts, other: CallCounts) -> None:
    into.attempts += other.attempts
    into.successes += other.successes
    into.refusals += other.refusals
    into.errors += other.errors
    if other.last_refusal_at >= into.last_refusal_at:
        into.last_refusal_at = other.last_refusal_at
        into.last_reason = other.last_reason


def _parse(line: str) -> dict[str, Any] | None:
    """One log line as an event, or ``None`` when it is not one.

    Every field is coerced here so no reader downstream has to defend itself
    against a log written by a different version of the writer.
    """
    line = line.strip()
    if not line:
        return None
    try:
        raw = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    outcome = str(raw.get("outcome") or OUTCOME_ERROR)
    if outcome not in (OUTCOME_OK, OUTCOME_REFUSED, OUTCOME_ERROR):
        outcome = OUTCOME_ERROR
    try:
        at = float(raw.get("at") or 0.0)
    except (TypeError, ValueError):
        at = 0.0
    return {
        "at": at,
        "agent": str(raw.get("agent") or ""),
        "agent_id": str(raw.get("agent_id") or ""),
        "host": str(raw.get("host") or ""),
        "venue": str(raw.get("venue") or VENUE_UNKNOWN),
        "tool": str(raw.get("tool") or ""),
        "outcome": outcome,
        "reason": str(raw.get("reason") or ""),
    }


_LEDGER: ThrottleLedger | None = None


def ledger() -> ThrottleLedger:
    """The process's ledger.

    One instance, because the offset into the shared log is per-process state
    and two instances would each fold only the lines the other had not yet
    reached. Every engine and every route answers from this one.
    """
    global _LEDGER
    if _LEDGER is None:
        _LEDGER = ThrottleLedger()
    return _LEDGER


def reset_ledger() -> None:
    """Drop the process ledger. For the suite, and for a runtime repoint."""
    global _LEDGER
    _LEDGER = None
