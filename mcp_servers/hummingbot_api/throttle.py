"""Counting what the venue refused, at the one place every agent call passes.

Nothing in Condor used to count a 429. The risk gate's ``RefusalLog`` recorded
only its own refusals, the single per-server limiter was the dashboard poller,
and per-request budgets existed for GeckoTerminal alone — so a throttled venue
reached an operator as a slow tick and read as a mysterious strategy bug. One
host at the Agent Builder Cup was sampled from the logs as "intermittent, may
recover"; counting it gave 8 attempts, 8 failures, 0 successes and 27 refusals,
which is a different fact (GitHub #276).

This module is the writer half, and it lives here because this process is the
only one that sees every agent call *and* its status. It is a stdlib-only leaf
on purpose: the MCP server must not import ``condor`` to count a refusal, and
it is handed its log path rather than deriving one (``$CONDOR_THROTTLE_LOG``,
set by ``condor.runtime.toolsets``), the same way the condor MCP server is
handed its agent roots. With no path set — a standalone ``uvx`` launch, an
external MCP host, pytest — recording is simply off.

The reader half is :mod:`condor.agents.throttle`, which folds this log into
per-agent and per-venue counters. **A counter never changes whether a call is
made:** everything here swallows, and no function in this module can refuse
anything. The deliberate absence of a limiter is the point — guessing a venue's
budget wrong starves a healthy agent, so this run counts first.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

#: The log to append to, handed over by the spawner. Unset = recording off.
THROTTLE_LOG_ENV = "CONDOR_THROTTLE_LOG"

#: What one call did. ``refused`` is the venue saying no (a 429 or its prose),
#: ``error`` is every other failure, and the two are kept apart because the
#: whole finding of #276 is that they had been read as one thing.
OUTCOME_OK = "ok"
OUTCOME_REFUSED = "refused"
OUTCOME_ERROR = "error"

#: A call whose arguments name no connector. Reported as itself rather than
#: folded into some venue it might have meant.
VENUE_UNKNOWN = "unknown"

#: Appending is unbounded in principle, so the log is reset once it passes this.
#: The reader notices the file shrank and restarts from zero (counters it has
#: already folded stay folded), which costs at most the events of one window.
MAX_LOG_BYTES = 2_000_000

#: Tool arguments that name a venue, in the order the tools prefer them.
_VENUE_KEYS = (
    "connector_name",
    "connector",
    "connector_names",
    "exchange",
    "venue",
)

#: The venue's prose for "you are asking too often". Matched case-insensitively
#: against the exception text, which is all an HTTP client hands up for most of
#: these.
_THROTTLE_PHRASES = (
    "too many requests",
    "rate limit",
    "rate-limit",
    "ratelimit",
    "throttl",
    "quota exceeded",
    "slow down",
    "request limit",
)

#: A bare ``429`` that reads like a status code rather than like a price. A
#: digit or a decimal point on either side disqualifies it, so "1429.50" and
#: "4290" do not count while "429, message='Too Many Requests'" does.
_HTTP_429 = re.compile(r"(?:^|[^\d.])429(?:[^\d.]|$)")


def is_throttle_refusal(error: Any) -> str:
    """Why this failure is the venue refusing, or ``""`` when it is not.

    The HTTP status is believed first: ``aiohttp`` carries it as ``.status`` and
    the venue's own wording varies by exchange. The prose match is the fallback,
    because a client that wrapped the response has usually kept the message and
    thrown the status away.

    A false positive here miscounts a refusal and nothing else — it cannot
    refuse a call, change a result, or gate a retry — which is why the prose
    list is allowed to be generous.
    """
    try:
        status = getattr(error, "status", None) or getattr(error, "status_code", None)
        if status == 429:
            return "venue returned HTTP 429 (too many requests)"

        text = str(error or "")
        if not text:
            return ""
        lowered = text.lower()
        for phrase in _THROTTLE_PHRASES:
            if phrase in lowered:
                return f"venue refused: {text[:200]}"
        if _HTTP_429.search(text):
            return f"venue returned 429: {text[:200]}"
    except Exception:  # noqa: BLE001 - a classifier may never raise at a call site
        return ""
    return ""


def venue_of(kwargs: dict[str, Any]) -> str:
    """The connector a call's arguments name, or :data:`VENUE_UNKNOWN`.

    A list-valued key (``connector_names``) with exactly one entry names that
    venue; with several it names none in particular, because the refusal cannot
    be attributed to one of them from here.
    """
    try:
        for key in _VENUE_KEYS:
            value = kwargs.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, (list, tuple)) and len(value) == 1:
                first = value[0]
                if isinstance(first, str) and first.strip():
                    return first.strip()
    except Exception:  # noqa: BLE001
        return VENUE_UNKNOWN
    return VENUE_UNKNOWN


def log_path() -> Path | None:
    """Where to append, or ``None`` when this process was not given a log.

    Read on every call rather than cached at import, for the same reason
    ``condor.paths`` re-reads its own overrides: the suite repoints it per test.
    """
    raw = os.environ.get(THROTTLE_LOG_ENV, "").strip()
    return Path(raw).expanduser() if raw else None


def record_call(
    tool: str,
    outcome: str,
    *,
    venue: str = VENUE_UNKNOWN,
    host: str = "",
    agent: str = "",
    agent_id: str = "",
    reason: str = "",
    now: float | None = None,
) -> None:
    """Append one call's outcome to the shared log. Never raises, ever.

    One line per call, written in a single ``write`` so two subprocesses
    appending at once interleave whole lines rather than halves of two.

    ``agent``/``agent_id`` are the labels the spawner stamped on this process —
    the slug for "which agent spends this host's budget" and the session id so a
    refusal reaches the right engine's ``RefusalLog``. Both empty for a
    standalone launch, which is honest: nobody can be named.
    """
    try:
        path = log_path()
        if path is None:
            return
        event = {
            "at": time.time() if now is None else float(now),
            "agent": str(agent or ""),
            "agent_id": str(agent_id or ""),
            "host": str(host or ""),
            "venue": str(venue or VENUE_UNKNOWN),
            "tool": str(tool or ""),
            "outcome": str(outcome or OUTCOME_ERROR),
            "reason": str(reason or "")[:300],
        }
        line = json.dumps(event, separators=(",", ":"), default=str) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        _reset_if_oversized(path)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:  # noqa: BLE001 - see the module docstring
        return


def _reset_if_oversized(path: Path) -> None:
    """Truncate a log that has outgrown :data:`MAX_LOG_BYTES`."""
    try:
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            path.write_text("", encoding="utf-8")
    except OSError:
        return
