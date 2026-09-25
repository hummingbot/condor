"""Core data provider: the book checked against the venue ([[FEAT-113]]).

I/O and nothing else. The comparison lives in :mod:`condor.venue_drift`, which
fetches nothing and is where the dashboard will read the same verdicts from —
so the tick loop and the browser cannot grow two copies of the rules and drift
apart.

Kept apart from :class:`~condor.agents.providers.positions.PositionsProvider`
on purpose: *what do I hold* and *is what I hold real* are different questions,
and ``run_core_providers`` isolates failures per provider — a venue that times
out must cost the agent its drift block, not its positions block.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from condor import venue_drift
from condor.fetchers.executors import describe_executor_error, fetch_all_executors
from condor.fetchers.positions import fetch_positions
from condor.fetchers.tracked_positions import fetch_tracked_positions

from . import register_provider
from .base import BaseProvider, ProviderResult

if TYPE_CHECKING:
    from condor.agents.ownership import OwnedBot

log = logging.getLogger(__name__)

#: Separators a controller tag may put between a session's ``agent_id`` and a
#: suffix. Matching on the bare prefix would let ``brigado.mm_1`` claim
#: ``brigado.mm_10``; requiring a separator (or an exact hit) cannot.
_TAG_SEPARATORS = ("_", "-", ".", ":", "/")


def owned_controller_ids(agent_id: str, tracked: list[dict]) -> list[str]:
    """The controller tags among ``tracked`` that belong to this session.

    ``agent_id`` is the tag an executor create must carry (the risk gate
    enforces it), so an exact hit is the common case; the separator-prefix arm
    catches tags that extend it. A session whose executors were tagged before
    that convention existed sees its own rows as unowned rather than as somebody
    else's — under-claiming, which is the safe direction.
    """
    if not agent_id:
        return []
    out: list[str] = []
    for row in tracked:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("controller_id") or "").strip()
        if not cid or cid in out:
            continue
        if cid == agent_id or any(
            cid.startswith(f"{agent_id}{sep}") for sep in _TAG_SEPARATORS
        ):
            out.append(cid)
    return out


class DriftProvider(BaseProvider):
    name = "drift"
    is_core = True

    async def execute(
        self,
        client: Any,
        config: dict,
        agent_id: str = "",
        bot_names: list[str] | None = None,
        owned: list[OwnedBot] | None = None,
    ) -> ProviderResult:
        # Unscoped on purpose: the venue answers for the whole account, so the
        # tracked side must too or every sibling controller's position would
        # read as an orphan. The agent's own involvement is an annotation on the
        # account's drift, never a filter of it.
        #
        # The tracked side is two reads: the held book (``position_holds``,
        # written only when an executor stops with ``keep_position=True``) and
        # the running executors' open inventory, which is on the venue and in no
        # hold — without it every live grid or position executor reads as an
        # orphan (CORR-708). "Active" is RUNNING plus SHUTTING_DOWN: a stopping
        # executor keeps its fills on the venue until its close order lands,
        # which a rejected-and-retried close can stretch indefinitely (CORR-710).
        # The API filters on one status, so that is two reads. All four reads
        # are independent, so they go out together. A failed read of either
        # tracked half fails the provider (the registry records it): half a book
        # scored against the venue would name the active executors' fills as
        # orphans. Only a failed venue read degrades to "unanswered".
        held, venue, running_rows, shutting_rows = await asyncio.gather(
            fetch_tracked_positions(client, strict=True),
            fetch_positions(client, strict=True),
            fetch_all_executors(client, status="RUNNING"),
            fetch_all_executors(client, status="SHUTTING_DOWN"),
            return_exceptions=True,
        )
        for read in (held, running_rows, shutting_rows):
            if isinstance(read, BaseException):
                raise read
        active, unmeasured = venue_drift.tracked_from_active(
            running_rows + shutting_rows
        )
        tracked = held + active

        if isinstance(venue, Exception):
            # An unreachable venue is not a flat venue. ``strict=True`` is how
            # the fetcher already draws that line; refusing to swallow it here
            # is what keeps "unanswered" out of "agreed". The reason reaches the
            # prompt and snapshot, so it is the sanitized message (the raw one
            # carries the backend URL) — clipped, as an API detail can be long.
            log.warning("drift provider venue fetch failed", exc_info=venue)
            _, message = describe_executor_error(venue)
            report = venue_drift.check(
                tracked, None, reason=message[:120], unmeasured=unmeasured
            )
        elif isinstance(venue, BaseException):
            raise venue
        else:
            report = venue_drift.check(tracked, venue, unmeasured=unmeasured)

        mine = owned_controller_ids(agent_id, tracked)
        worst = venue_drift.worst_quote(report, mine)

        return ProviderResult(
            name=self.name,
            data={
                "report": asdict(report),
                "mine": mine,
                # Denormalised for the risk engine, which reads a verdict and
                # not a report: it must not re-derive the comparison.
                "trusted": report.trusted,
                "reason": report.reason,
                "worst_quote": worst,
                "drifting": len(venue_drift.drifting(report)),
            },
            summary=venue_drift.summarize(report, mine),
        )


register_provider(DriftProvider())
