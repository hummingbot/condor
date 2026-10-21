"""Fleet diagnostics: the signals that say which bot is in trouble (FEAT-141).

The reader half of the fleet diagnostics view. Liveness
(:mod:`condor.agents.fleet_map`), throttling (:mod:`condor.agents.throttle`)
and the running-without-controllers flag (``condor.fetchers.bots``) already
reach the dashboard on payloads it polls; what was still missing is the two
groups nothing produced — **host capacity** and **executor hygiene** — and
those are what this module folds.

Everything here is free. ``host_load`` reads the kernel's own load average,
``api_lag`` reads counters the server-data service already keeps, and
``executor_hygiene`` folds rows a caller already has. No Hummingbot call, no
network call, no new poll: the view's whole constraint is that it adds no
polling cost to a dashboard that already polls per server, so a signal that
would need a fetch is reported **unavailable** instead of fetched. That is not
a degradation — an unknown signal rendered as unknown is the lesson of
[[CORR-725]], where a missing reading rendered as *running*.

Counting and reporting only; the thresholds that turn a reading into a flag
live with the view that draws them, because a band an operator has to judge
(0.01–0.4% of orders failing was normal at the race, 93% was the case nobody
saw) has to be stated next to the number it judges.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterable, Mapping

from .executor_adoption import lineage_session_num

log = logging.getLogger(__name__)

__all__ = [
    "LIVE_EXECUTOR_STATUSES",
    "api_lag",
    "executor_hygiene",
    "host_load",
    "live_sessions_by_strategy",
]

#: Executor statuses that still hold exposure, mirroring
#: :data:`condor.agents.closeout.LIVE_EXECUTOR_STATUSES`. Everything else is a
#: terminal row, and terminal rows are never pruned upstream — which is the
#: whole reason their count per host is a hygiene signal ([[PERF-733]]).
LIVE_EXECUTOR_STATUSES = frozenset({"RUNNING", "SHUTTING_DOWN"})


def host_load() -> dict[str, Any]:
    """Condor's own host: 1-minute load average against its vCPU count.

    The host that matters, and the only one readable for free. It is where the
    tick loops run and where every order is issued from, and the race's
    oversubscription case — a 2-vCPU box above 1.0x load running
    high-frequency makers — showed up as slow ticks and timeouts rather than as
    a crash, so it was read as a strategy bug.

    ``available`` is ``False`` where the platform has no load average (Windows)
    or will not say how many CPUs it has. The caller renders that as *unknown*
    and never as *ok*.
    """
    try:
        load_1m = float(os.getloadavg()[0])
    except (OSError, AttributeError, IndexError, ValueError):
        return {"available": False, "load_1m": 0.0, "vcpus": 0, "load_per_vcpu": 0.0}
    vcpus = os.cpu_count() or 0
    if vcpus <= 0:
        # A load average with nothing to divide it by is not a reading: 3.0 is
        # idle on 32 vCPUs and on fire on 2, and reporting the bare number
        # would invite exactly that misreading.
        return {
            "available": False,
            "load_1m": round(load_1m, 2),
            "vcpus": 0,
            "load_per_vcpu": 0.0,
        }
    return {
        "available": True,
        "load_1m": round(load_1m, 2),
        "vcpus": vcpus,
        "load_per_vcpu": round(load_1m / vcpus, 3),
    }


def api_lag() -> list[dict[str, Any]]:
    """Per-server round-trip latency, as the order-placement lag it bounds.

    Read off :meth:`condor.server_data_service.ServerDataService.get_stats`,
    which reports what the polls already measured — so this is the one
    order-placement lag reading available without issuing an order. It is a
    *floor*: an order cannot be placed faster than the API answers.

    Sorted by host so the rows do not reshuffle between polls.
    """
    try:
        from condor.server_data_service import get_server_data_service

        health = get_server_data_service().get_stats().get("health") or {}
    except Exception:  # pragma: no cover - stats is a plain dict walk
        log.debug("diagnostics: server health unreadable", exc_info=True)
        return []
    rows: list[dict[str, Any]] = []
    for host, info in health.items():
        if not isinstance(info, Mapping):
            continue
        rows.append(
            {
                "host": str(host),
                "status": str(info.get("status") or "unknown"),
                "last_latency_ms": float(info.get("last_latency_ms") or 0.0),
                "consecutive_failures": int(info.get("consecutive_failures") or 0),
            }
        )
    rows.sort(key=lambda row: row["host"])
    return rows


def live_sessions_by_strategy() -> dict[tuple[str, str], set[int]]:
    """``(agent_slug, strategy_slug)`` → the session numbers still in memory.

    The supervisor's registry is the authority on which sessions are alive, as
    :mod:`condor.agents.executor_adoption` says: a run is registered for
    exactly as long as its engine is in memory, and no status file can say that.

    A strategy absent from the result has **no** live session, which is what
    makes every running executor of its lineage an orphan.
    """
    from condor.runtime.loops import get_supervisor

    out: dict[tuple[str, str], set[int]] = {}
    for engine in get_supervisor().all().values():
        try:
            key = (engine.agent.slug, engine.strategy.slug)
            out.setdefault(key, set()).add(int(getattr(engine, "session_num", 0) or 0))
        except Exception:  # pragma: no cover - the registry is a plain dict
            log.debug("diagnostics: skipping an unreadable engine", exc_info=True)
    return out


def executor_hygiene(
    rows: Iterable[Mapping[str, Any]] | None,
    live_sessions: Mapping[tuple[str, str], Iterable[int]] | None = None,
    *,
    truncated: bool = False,
) -> dict[str, Any]:
    """Terminal-row count for a host, and the running rows with no live owner.

    Two facts from one walk of rows the caller already has:

    * **terminated** — how much history the host is carrying. Nothing prunes a
      terminal executor record upstream, and the race's hosts ended with 35 /
      173 / 0 / 2 / 16 of them, which is what crowds a full walk's row cap
      ([[PERF-733]]).
    * **orphaned_running** — a RUNNING executor whose owning *session* is gone
      ([[CORR-726]]). The single most expensive defect of the race: one such
      LP executor polled every second for 13.5h and ate a host's whole RPC
      budget, and nothing on any screen said it existed.

    ``rows`` of ``None`` means the caller had nothing cached and declined to
    fetch — reported as ``available: False``, never as a clean host. ``rows``
    that are only part of the host's history set ``truncated``, so the counts
    read as a floor rather than as a total.
    """
    if rows is None:
        return {
            "available": False,
            "counted": 0,
            "running": 0,
            "terminated": 0,
            "truncated": False,
            "orphaned_running": [],
        }

    live = {
        key: {int(num) for num in nums} for key, nums in (live_sessions or {}).items()
    }
    counted = running = terminated = 0
    orphans: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        counted += 1
        status = str(row.get("status") or "").strip().upper()
        if status not in LIVE_EXECUTOR_STATUSES:
            terminated += 1
            continue
        running += 1
        if status != "RUNNING":
            continue
        orphan = _orphan_of(row, live)
        if orphan is not None:
            orphans.append(orphan)
    orphans.sort(key=lambda row: row["executor_id"])
    return {
        "available": True,
        "counted": counted,
        "running": running,
        "terminated": terminated,
        "truncated": bool(truncated),
        "orphaned_running": orphans,
    }


def _orphan_of(
    row: Mapping[str, Any], live: Mapping[tuple[str, str], set[int]]
) -> dict[str, Any] | None:
    """This RUNNING row, if its session tag names a session nobody holds.

    The lineage is read *out of* the tag rather than matched against a list of
    strategies, because the set of strategies with no live session at all is
    exactly the set this has to catch — and a strategy absent from ``live`` is
    absent from any list built from it.
    """
    cfg = row.get("config")
    cfg = cfg if isinstance(cfg, Mapping) else {}
    # Same precedence as ``build_executor_row``: the creator's tag is on the
    # record, and what is left inside ``config`` is the backend default.
    controller = str(row.get("controller_id") or cfg.get("controller_id") or "")
    agent_slug, strategy_slug, num = _split_session_tag(controller)
    if num is None:
        return None
    if num in live.get((agent_slug, strategy_slug), set()):
        return None
    executor_id = str(row.get("id") or row.get("executor_id") or "")
    if not executor_id:
        return None
    return {
        "executor_id": executor_id,
        "controller_id": controller,
        "agent_slug": agent_slug,
        "strategy_slug": strategy_slug,
        "session_num": num,
        "pair": str(cfg.get("trading_pair") or row.get("trading_pair") or ""),
        "kind": str(cfg.get("type") or row.get("type") or ""),
    }


def _split_session_tag(controller_id: str) -> tuple[str, str, int | None]:
    """``"brigado.brl_mm_7"`` → ``("brigado", "brl_mm", 7)``.

    The inverse of :func:`condor.agents.executor_adoption.session_lineage`, and
    it defers to :func:`~condor.agents.executor_adoption.lineage_session_num`
    for the one judgement that matters — whether the remainder really is a
    session number — so the backend's default ``"main"`` tag, an experiment's
    ``_e3`` and anything else that is not a session are rejected here exactly as
    they are there.
    """
    agent, _, rest = controller_id.partition(".")
    if not agent or not rest:
        return "", "", None
    strategy, sep, _ = rest.rpartition("_")
    if not sep or not strategy:
        return "", "", None
    num = lineage_session_num(controller_id, agent, strategy)
    if num is None:
        return "", "", None
    return agent, strategy, num
