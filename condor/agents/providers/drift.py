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

from dataclasses import asdict
from typing import Any

from condor import venue_drift
from condor.fetchers.positions import fetch_positions
from condor.fetchers.tracked_positions import fetch_tracked_positions

from . import register_provider
from .base import BaseProvider, ProviderResult

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
        since: float = 0.0,
    ) -> ProviderResult:
        # Unscoped on purpose: the venue answers for the whole account, so the
        # tracked side must too or every sibling controller's position would
        # read as an orphan. The agent's own involvement is an annotation on the
        # account's drift, never a filter of it.
        tracked = await fetch_tracked_positions(client, strict=True)

        try:
            venue = await fetch_positions(client, strict=True)
        except Exception as exc:
            # An unreachable venue is not a flat venue. ``strict=True`` is how
            # the fetcher already draws that line; refusing to swallow it here
            # is what keeps "unanswered" out of "agreed".
            report = venue_drift.check(tracked, None, reason=str(exc)[:120])
        else:
            report = venue_drift.check(tracked, venue)

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
