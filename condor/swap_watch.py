"""How long a Gateway swap has been unverifiable, and the one call that resolves it.

A swap leaves ``SUBMITTED`` only after a *successful* status read. That is
upstream's deliberate rule (hummingbot-api
``services/gateway_transaction_poller.py``, GitHub #278): a swap must never be
aged out on a poll that could not check it, because a failing Gateway ping, a
``None`` status, an unparseable network string or an exception say nothing about
the chain. The cost of the rule is that any of those keeps the row ``SUBMITTED``
for ever, and at the Agent Builder Cup one DEX entry sat there until the agent
was redeployed — which cost race time and still left the operator unsure what
had landed on-chain (FEAT-140).

This module is the Condor half of that issue, and it is built around one
distinction:

- **unverifiable is not failed.** A swap nobody could check may well have
  landed. Every string here says *unverifiable*, with the duration attached, and
  the word "failed" appears nowhere in a condition or an alert — labelling an
  unchecked swap as lost is the same error as labelling it confirmed, in the
  other direction.
- **Condor sets no swap state.** It does not track swap confirmation and has no
  chain read, so asserting an outcome here would be Condor inventing exactly the
  fact the operator was missing. :func:`resolve` asks the *server* and returns
  the server's answer verbatim; nothing in this module writes a status.

So there are two halves, and only the first works against every server:

:class:`SwapWatch`
    Counting and describing. Pure arithmetic over the swap rows Condor already
    fetches, with no network call of its own, so any surface that has rows in
    hand can ask "how long has this been unverifiable" and alert past
    :func:`unverifiable_after_seconds`.

:func:`resolve`
    The exit. ``POST /gateway/swaps/{tx}/resolve`` is new upstream, so a server
    that predates it answers 404 — a capability answer and never an outage,
    which is why it comes back as :class:`SwapResolveUnsupported` through
    :mod:`condor.fetchers.raw_api` rather than as a failure. Three surfaces
    share this one function (the ``resolve_swap`` MCP tool, the dashboard route
    and the runbook's instruction to use them) so an agent and an operator
    cannot resolve a swap by different routes.

Where the clock starts, in order of preference: the swap row's own recorded
submission time, because it survives a Condor restart and is the honest age of
the swap; failing that, Condor's first observation of the row, which can only
*understate* the duration and never overstate it. The row says which one it
used in ``since``, so an operator reading "unverifiable for 4m" after a restart
knows it means "at least 4m".
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_UNVERIFIABLE_AFTER_SECONDS",
    "MAX_TRACKED_SWAPS",
    "SUBMITTED",
    "SwapResolveUnsupported",
    "SwapWatch",
    "describe_duration",
    "parse_timestamp",
    "reset_watch",
    "resolve",
    "unverifiable_after_seconds",
    "watch",
]

#: The status upstream holds an in-flight swap in. The one value this module
#: looks for; everything else is a swap whose outcome is known.
SUBMITTED = "SUBMITTED"

#: How long a swap stays ``SUBMITTED`` before it reads as *unverifiable* rather
#: than merely pending. Fifteen minutes, the same window
#: :data:`condor.agents.throttle.THROTTLED_FOR_SECONDS` uses and for the same
#: reason: long enough that no healthy confirmation is still in flight on any
#: chain Gateway routes to, short enough that an operator hears about it inside
#: the session that opened it.
DEFAULT_UNVERIFIABLE_AFTER_SECONDS = 900.0

#: Environment override, read per call so the suite and a second install
#: sharing a checkout can repoint it.
UNVERIFIABLE_AFTER_ENV = "CONDOR_SWAP_UNVERIFIABLE_AFTER"

#: First-observation times kept at once. Bounded because a long-lived process
#: searching swap history would otherwise accumulate one float per transaction
#: hash it has ever seen. Eviction drops the oldest first observation, whose
#: swap is either long resolved or already past the threshold on its own
#: recorded timestamp.
MAX_TRACKED_SWAPS = 500


class SwapResolveUnsupported(Exception):
    """This server does not serve the resolve route, or the client cannot reach it.

    A capability answer. The caller says "the connected server does not offer
    this" and changes nothing — never "the server is down", and never a local
    state change standing in for the one the server would have made.
    """


def unverifiable_after_seconds() -> float:
    """The threshold, honouring :data:`UNVERIFIABLE_AFTER_ENV`.

    A value that is not a positive number falls back to the default rather than
    disabling the alert: a malformed override must not be the reason nobody is
    told about a stuck swap.
    """
    raw = os.environ.get(UNVERIFIABLE_AFTER_ENV, "").strip()
    if not raw:
        return DEFAULT_UNVERIFIABLE_AFTER_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "%s=%r is not a number; using %ss",
            UNVERIFIABLE_AFTER_ENV,
            raw,
            DEFAULT_UNVERIFIABLE_AFTER_SECONDS,
        )
        return DEFAULT_UNVERIFIABLE_AFTER_SECONDS
    if value <= 0:
        return DEFAULT_UNVERIFIABLE_AFTER_SECONDS
    return value


def parse_timestamp(value: Any) -> Optional[float]:
    """A swap row's timestamp as epoch seconds, or ``None``.

    Upstream has written this field as an ISO string, as epoch seconds and as
    epoch milliseconds across versions, so every spelling is read here rather
    than in each caller. A naive ISO string is read as UTC, which is what the
    API serialises. A value in the future (clock skew between Condor and the
    API host) is returned as it is; the duration arithmetic floors at zero.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.replace(".", "", 1).isdigit():
            return _from_epoch(float(text))
        return _from_iso(text)
    except (TypeError, ValueError):
        return None


def _from_epoch(number: float) -> Optional[float]:
    if number <= 0:
        return None
    # Milliseconds, if the value is implausibly far in the future as seconds.
    # 1e11 seconds is the year 5138; 1e11 milliseconds is 1973.
    return number / 1000.0 if number > 1e11 else number


def _from_iso(text: str) -> Optional[float]:
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def describe_duration(seconds: float) -> str:
    """``"45s"``, ``"12m"``, ``"3h 12m"``, ``"2d 5h"``.

    Coarse on purpose: the operator decision this feeds ("has this been stuck
    long enough that I should resolve it") turns on the order of magnitude, and
    a second-precise duration on a four-hour swap only reads as false precision.
    """
    total = int(max(0.0, seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        hours, rest = divmod(total, 3600)
        minutes = rest // 60
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, rest = divmod(total, 86400)
    hours = rest // 3600
    return f"{days}d {hours}h" if hours else f"{days}d"


def is_submitted(swap: Any) -> bool:
    """Whether a swap row is in flight. Tolerant of legacy lower-case rows."""
    if not isinstance(swap, dict):
        return False
    return str(swap.get("status") or "").strip().upper() == SUBMITTED


@dataclass
class SwapWatch:
    """First-observation times for in-flight swaps, and the conditions over them.

    One instance per process (see :func:`watch`), because the first observation
    of a swap is per-process state: two instances would each hold the half of
    the history the other never saw, and the fallback duration would depend on
    which surface asked.

    Nothing here calls the network. Every method takes the rows the caller
    already fetched, which is what lets the Telegram history view, an MCP result
    and a dashboard poll all answer the same question without a second round
    trip.
    """

    _first_seen: dict[str, float] = field(default_factory=dict, init=False)

    # ── recording ──

    def observe(self, swaps: Any, now: Optional[float] = None) -> None:
        """Note the first time each in-flight swap was seen; forget settled ones.

        A hash missing from ``swaps`` is *not* treated as settled: the caller
        hands over one page of a filtered search, so absence says nothing. Only
        a row that arrives with a status other than ``SUBMITTED`` clears its
        entry — that is a real answer from the server.
        """
        at = time.time() if now is None else now
        for swap in swaps if isinstance(swaps, (list, tuple)) else []:
            if not isinstance(swap, dict):
                continue
            tx_hash = str(swap.get("transaction_hash") or "").strip()
            if not tx_hash:
                continue
            if not is_submitted(swap):
                self._first_seen.pop(tx_hash, None)
                continue
            self._first_seen.setdefault(tx_hash, at)
        self._evict()

    def _evict(self) -> None:
        excess = len(self._first_seen) - MAX_TRACKED_SWAPS
        if excess <= 0:
            return
        for tx_hash in sorted(self._first_seen, key=self._first_seen.__getitem__)[
            :excess
        ]:
            del self._first_seen[tx_hash]

    def forget(self, transaction_hash: str) -> None:
        """Drop one hash's first observation. For the suite, and after a resolve."""
        self._first_seen.pop(str(transaction_hash or "").strip(), None)

    # ── describing ──

    def since(self, swap: Any) -> tuple[Optional[float], str]:
        """``(epoch, source)`` the clock for this swap starts at.

        ``source`` is ``"submitted"`` for the row's own recorded time and
        ``"observed"`` for Condor's first sighting, which can only understate
        the duration. ``(None, "unknown")`` when there is neither — the row
        carries no timestamp and this process has never seen it.
        """
        if not isinstance(swap, dict):
            return None, "unknown"
        recorded = parse_timestamp(swap.get("timestamp"))
        if recorded is not None:
            return recorded, "submitted"
        tx_hash = str(swap.get("transaction_hash") or "").strip()
        observed = self._first_seen.get(tx_hash)
        if observed is not None:
            return observed, "observed"
        return None, "unknown"

    def unverifiable_for(
        self, swap: Any, now: Optional[float] = None
    ) -> Optional[float]:
        """Seconds this swap has been ``SUBMITTED``, or ``None``.

        ``None`` for a swap that is not in flight and for one whose clock cannot
        be started at all — no duration is not a duration of zero.
        """
        if not is_submitted(swap):
            return None
        started, _source = self.since(swap)
        if started is None:
            return None
        at = time.time() if now is None else now
        return max(0.0, at - started)

    def condition(self, swap: Any, now: Optional[float] = None) -> Optional[dict]:
        """One in-flight swap's row, or ``None`` when it is not in flight.

        ``condition`` is ``unverifiable`` past the threshold and ``pending``
        below it. Never ``failed``: this surface has not read the chain and
        cannot say the swap is lost.
        """
        if not is_submitted(swap):
            return None
        started, source = self.since(swap)
        elapsed = self.unverifiable_for(swap, now=now)
        threshold = unverifiable_after_seconds()
        unverifiable = elapsed is not None and elapsed >= threshold
        return {
            "transaction_hash": str(swap.get("transaction_hash") or ""),
            "connector": str(swap.get("connector") or ""),
            "network": str(swap.get("network") or ""),
            "trading_pair": str(swap.get("trading_pair") or ""),
            "side": str(swap.get("side") or "").upper(),
            "status": SUBMITTED,
            "condition": "unverifiable" if unverifiable else "pending",
            "since": source,
            "submitted_at": started,
            "unverifiable_for_seconds": elapsed,
            "unverifiable_for": (
                describe_duration(elapsed) if elapsed is not None else ""
            ),
            "threshold_seconds": threshold,
        }

    def conditions(self, swaps: Any, now: Optional[float] = None) -> list[dict]:
        """Every in-flight swap in ``swaps``, longest unverifiable first."""
        rows = []
        for swap in swaps if isinstance(swaps, (list, tuple)) else []:
            row = self.condition(swap, now=now)
            if row is not None:
                rows.append(row)
        rows.sort(key=lambda r: -(r["unverifiable_for_seconds"] or 0.0))
        return rows

    def alerts(self, swaps: Any, now: Optional[float] = None) -> list[dict]:
        """Only the swaps past the threshold, each with the line to show.

        The alert is the condition row plus a ``message`` naming the duration.
        An operator or an agent reading only the message still learns that the
        outcome is *unknown* rather than lost, and what to do about it.
        """
        alerting = [
            row
            for row in self.conditions(swaps, now=now)
            if row["condition"] == "unverifiable"
        ]
        for row in alerting:
            row["message"] = describe_alert(row)
        return alerting


def describe_alert(row: dict) -> str:
    """The alert line for one unverifiable swap.

    Deliberately free of the word *failed*. The swap may have landed on-chain
    and nobody has been able to check — the whole point of #278's rule that a
    swap is never aged out on a poll that could not check it.
    """
    tx_hash = str(row.get("transaction_hash") or "")
    short = f"{tx_hash[:10]}…" if len(tx_hash) > 10 else tx_hash or "(no hash)"
    venue = " ".join(
        part for part in (row.get("connector") or "", row.get("network") or "") if part
    )
    where = f" on {venue}" if venue else ""
    pair = f" {row['trading_pair']}" if row.get("trading_pair") else ""
    return (
        f"Swap {short}{pair}{where} has been SUBMITTED and unverifiable for "
        f"{row.get('unverifiable_for') or 'an unknown time'}. Its outcome is "
        f"unknown, not lost: it may have landed on-chain and no status read has "
        f"succeeded. Check the explorer, and resolve it against the server with "
        f"resolve_swap once a read does succeed."
    )


# ── the exit ──


async def resolve(
    client: Any,
    transaction_hash: str,
    *,
    network: Optional[str] = None,
) -> dict[str, Any]:
    """Ask the server to resolve one swap. Returns the server's answer verbatim.

    ``POST /gateway/swaps/{tx}/resolve`` is #278's upstream half: the server
    reads the chain, decides the swap's real outcome and records it. Condor
    carries the request and the answer and adds nothing — no local status, no
    inference from a timeout, nothing that could disagree with the poller.

    The route postdates the pinned ``hummingbot-api-client`` (there is no
    ``gateway_swap.resolve_swap`` on it), so the request goes through
    :mod:`condor.fetchers.raw_api`, the one place that idiom lives.

    Args:
        client: A ``HummingbotAPIClient`` for the target server.
        transaction_hash: The stuck swap's hash.
        network: Network id, passed through when the caller knows it. Omitted
            rather than guessed: the server identifies the swap by hash.

    Returns:
        The server's JSON, under ``result``, with ``transaction_hash`` echoed.

    Raises:
        SwapResolveUnsupported: The server does not serve the route (404), or
            this client cannot reach it.
        aiohttp.ClientResponseError: Any other non-2xx, carrying upstream's
            status and redacted detail.
    """
    from condor.fetchers.raw_api import request

    tx_hash = str(transaction_hash or "").strip()
    if not tx_hash:
        raise ValueError("transaction_hash is required to resolve a swap")

    result = await request(
        client,
        "post",
        f"/gateway/swaps/{tx_hash}/resolve",
        params={"network": network} if network else None,
        unsupported=SwapResolveUnsupported,
    )
    # The server has now answered for this hash, whatever it answered, so the
    # fallback clock for it is spent. Dropping it is bookkeeping about Condor's
    # own observations and is not a swap state.
    watch().forget(tx_hash)
    return {"transaction_hash": tx_hash, "result": result}


_WATCH: Optional[SwapWatch] = None


def watch() -> SwapWatch:
    """The process's watch. One instance — see :class:`SwapWatch`."""
    global _WATCH
    if _WATCH is None:
        _WATCH = SwapWatch()
    return _WATCH


def reset_watch() -> None:
    """Drop the process watch. For the suite, and for a runtime repoint."""
    global _WATCH
    _WATCH = None
