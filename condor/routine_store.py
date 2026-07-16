"""Routine discovery for the web dashboard + scheduler.

Post-simplification this is DISCOVERY ONLY: execution happens in the
disposable worker subprocess (§7.2, ``condor.routines_worker``) and
repetition comes from the durable cron schedules (§5.4,
``condor.agents.scheduler``) — the old in-memory instance/interval
machinery is gone.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path

from routines.base import (
    RoutineInfo,
    discover_routines,
    discover_routines_from_path,
)

log = logging.getLogger(__name__)


class RoutineStore:
    """Discovery over the general library + every agent's routines."""

    def _discover_all(self) -> dict[str, RoutineInfo]:
        """Discover routines: the general library (root ``routines/``) + each
        agent's, prefixed ``{slug}/{name}``.

        Discovery is mtime-cached in routines.base: only new/changed files
        are (re)imported, so this stays cheap on every list call while edits
        are still picked up without a restart.
        """
        all_routines = dict(discover_routines())

        agents_dir = Path(__file__).resolve().parent.parent / "agents"
        if agents_dir.exists():
            for agent_dir in sorted(agents_dir.iterdir()):
                routines_path = agent_dir / "routines"
                if not routines_path.is_dir():
                    continue
                slug = agent_dir.name
                agent_routines = discover_routines_from_path(
                    routines_path, agent_slug=slug
                )
                for rname, rinfo in agent_routines.items():
                    # Shallow-copy before prefixing: the RoutineInfo is shared
                    # with the discovery cache and must keep its bare name.
                    prefixed_info = copy.copy(rinfo)
                    prefixed_info.name = f"{slug}/{rname}"
                    all_routines[prefixed_info.name] = prefixed_info

        return all_routines

    def get_routine(
        self, name: str, agent_slug: str | None = None
    ) -> RoutineInfo | None:
        """Resolve one routine: the agent's own dir when ``agent_slug`` is
        given, else the general library (including the ``{slug}/{name}``
        prefixed form)."""
        if agent_slug:
            routines_path = (
                Path(__file__).resolve().parent.parent
                / "agents"
                / agent_slug
                / "routines"
            )
            if routines_path.is_dir():
                found = discover_routines_from_path(
                    routines_path, agent_slug=agent_slug
                ).get(name)
                if found is not None:
                    return found
            return discover_routines().get(name)
        return self._discover_all().get(name)

    def _get_report_counts(self) -> dict[str, int]:
        """Get report count per routine source_name."""
        try:
            from condor.reports import list_reports

            reports, _ = list_reports(limit=1000)
            counts: dict[str, int] = {}
            for r in reports:
                sn = r.get("source_name", "")
                if sn:
                    counts[sn] = counts.get(sn, 0) + 1
            return counts
        except Exception:
            return {}

    def list_routines(self) -> list[dict]:
        all_routines = self._discover_all()
        report_counts = self._get_report_counts()
        out = []
        for name, info in all_routines.items():
            out.append(
                {
                    "name": name,
                    "description": info.description,
                    "is_continuous": info.is_continuous,
                    "category": info.category,
                    "source": info.source,
                    "fields": info.get_fields(),
                    "last_modified": info.last_modified,
                    "report_count": report_counts.get(name, 0)
                    or report_counts.get(name.split("/")[-1], 0),
                }
            )
        return out


_store: RoutineStore | None = None


def get_routine_store() -> RoutineStore:
    global _store
    if _store is None:
        _store = RoutineStore()
    return _store
