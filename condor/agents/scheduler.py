"""Scheduler execution (§5.4) — unattended operation.

AgentService-owned. Two schedule sources:

- **Agent schedules**: ``schedule:`` in AGENT.md frontmatter (cron + IANA tz,
  bounded duration enforced at spec validation). State derives from specs +
  RunStore at boot — no separate agent-schedule store. A due fire calls
  ``AgentService.run`` normally (``kind: scheduled``) and ``run_started``
  persists ``scheduled_for`` (the fire time); duplicate prevention
  deduplicates on that fire key.
- **Routine schedules (durable)**: read-only routine schedules persisted in
  ``store/schedules.json`` — serialized writer, atomic tmp+rename with
  fsync, 0700/0600; ``last_fired`` advances WRITE-AHEAD, before spawning the
  worker (a crash between advance and spawn loses that fire — exactly the
  skip/no-backfill semantics). Deleting/tombstoning a referenced routine or
  its owning agent disables the schedule explicitly (reason + notification).

Rules: **missed fires are skipped, never backfilled** (a trading decision
computed for a stale moment is wrong, not late); **overlap is skipped with a
warning** while the previous scheduled run is active or its leases have not
released. Scheduled fires enable LAST in the startup ordering — a fire that
came due during a slow startup is skipped, not launched against
half-reconciled state.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from condor.agents.cron import fires_between

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 20.0
# Never launch a fire older than this — covers slow polls without turning
# skip-no-backfill into backfill.
MAX_FIRE_AGE_S = 120.0


def schedules_path() -> Path:
    from condor.agents.context import get_project_dir

    return Path(get_project_dir()) / "store" / "schedules.json"


class RoutineScheduleStore:
    """Durable read-only-routine schedules: serialized writer, atomic
    tmp+rename with fsync, 0700 dir / 0600 file."""

    def __init__(self, path: Path | None = None):
        self._path = path or schedules_path()
        self._lock = threading.Lock()

    def load(self) -> dict[str, dict]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text()) or {}
        except json.JSONDecodeError:
            log.exception("schedules.json unreadable — treating as empty")
            return {}

    def _write(self, data: dict[str, dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self._path.parent, 0o700)
        tmp = self._path.with_name(f".schedules.tmp{os.getpid()}")
        with open(tmp, "w") as f:
            f.write(json.dumps(data, indent=2, sort_keys=True))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        tmp.replace(self._path)

    def upsert(self, schedule_id: str, entry: dict) -> dict:
        with self._lock:
            data = self.load()
            data[schedule_id] = entry
            self._write(data)
        return entry

    def remove(self, schedule_id: str) -> bool:
        with self._lock:
            data = self.load()
            if schedule_id not in data:
                return False
            del data[schedule_id]
            self._write(data)
        return True

    def mark_fired(self, schedule_id: str, fired_at: str) -> bool:
        """WRITE-AHEAD: advance last_fired BEFORE the worker spawns."""
        with self._lock:
            data = self.load()
            entry = data.get(schedule_id)
            if entry is None:
                return False
            entry["last_fired"] = fired_at
            self._write(data)
        return True

    def disable(self, schedule_id: str, reason: str) -> bool:
        with self._lock:
            data = self.load()
            entry = data.get(schedule_id)
            if entry is None:
                return False
            entry["disabled"] = True
            entry["disabled_reason"] = reason
            self._write(data)
        return True


def create_routine_schedule(
    *,
    routine: str,
    agent_slug: str = "",
    config: dict | None = None,
    cron: str,
    tz: str = "UTC",
    store: RoutineScheduleStore | None = None,
) -> dict:
    """Register a durable schedule for a read-only routine."""
    from condor.agents.spec import validate_cron

    validate_cron(cron)
    from zoneinfo import ZoneInfo

    ZoneInfo(tz)  # raises on non-IANA
    store = store or RoutineScheduleStore()
    schedule_id = uuid.uuid4().hex[:12]
    entry = {
        "routine": routine,
        "agent_slug": agent_slug,
        "config": config or {},
        "cron": cron,
        "tz": tz,
        "last_fired": "",
        "disabled": False,
    }
    store.upsert(schedule_id, entry)
    return {"schedule_id": schedule_id, **entry}


class Scheduler:
    """One poll loop over agent schedules + routine schedules."""

    def __init__(
        self,
        *,
        routine_store: RoutineScheduleStore | None = None,
        routine_runner: Optional[Callable[[dict], Awaitable[Any]]] = None,
        poll_interval_s: float = POLL_INTERVAL_S,
    ):
        self._routine_store = routine_store or RoutineScheduleStore()
        self._routine_runner = routine_runner
        self._poll_interval_s = poll_interval_s
        self._task: asyncio.Task | None = None
        self._enabled_at: datetime | None = None
        self._last_poll: datetime | None = None

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        """Enable scheduled fires — called LAST in startup ordering (§12).
        Fires due before this instant are skipped (no backfill)."""
        if self._task is not None:
            return
        self._enabled_at = datetime.now(timezone.utc)
        self._last_poll = self._enabled_at
        self._task = asyncio.create_task(self._loop())
        log.info("scheduler enabled (poll %.0fs)", self._poll_interval_s)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler poll failed")
            await asyncio.sleep(self._poll_interval_s)

    # -- one poll ----------------------------------------------------------

    async def poll_once(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        last = self._last_poll or now
        self._last_poll = now
        fired: dict[str, list[str]] = {"agents": [], "routines": []}
        await self._poll_agents(last, now, fired)
        await self._poll_routines(last, now, fired)
        return fired

    async def _poll_agents(
        self, last: datetime, now: datetime, fired: dict
    ) -> None:
        from condor.agents.service import AgentService

        svc = AgentService()
        for agent in svc.list():
            schedule = agent.schedule or {}
            cron = schedule.get("cron")
            if not cron:
                continue
            tz = str(schedule.get("tz", "UTC"))
            try:
                due = fires_between(str(cron), tz, last, now)
            except Exception:
                log.exception("agent %s: bad schedule %r", agent.slug, schedule)
                continue
            if not due:
                continue
            # Skip-no-backfill: only the MOST RECENT due fire is considered,
            # and only when it is still fresh.
            fire = due[-1]
            fire_iso = fire.astimezone(timezone.utc).isoformat()
            if (now - fire.astimezone(timezone.utc)).total_seconds() > MAX_FIRE_AGE_S:
                log.warning(
                    "agent %s: fire %s too old — skipped (no backfill)",
                    agent.slug, fire_iso,
                )
                continue
            if self._already_fired(agent.slug, fire_iso):
                continue
            if self._overlaps(agent.slug):
                log.warning(
                    "agent %s: fire %s skipped — previous run still active or "
                    "holding leases (overlap rule)", agent.slug, fire_iso,
                )
                await self._notify(
                    f"⏭️ Scheduled fire for {agent.slug} at {fire_iso} skipped: "
                    "the previous run is still active or holds leases.",
                    agent.slug,
                )
                continue
            try:
                result = await svc.run(
                    agent.slug,
                    config=None,
                    kind="scheduled",
                    scheduled_for=fire_iso,
                )
                fired["agents"].append(f"{agent.slug}@{fire_iso}")
                log.info(
                    "agent %s: scheduled fire %s launched (%s)",
                    agent.slug, fire_iso, result.get("agent_id"),
                )
            except Exception as e:
                log.exception("agent %s: scheduled launch failed", agent.slug)
                await self._notify(
                    f"🚨 Scheduled launch of {agent.slug} at {fire_iso} "
                    f"failed: {e}",
                    agent.slug,
                )

    def _already_fired(self, slug: str, fire_iso: str) -> bool:
        """Fire-key dedup (§5.4): a run_started with this scheduled_for —
        never 'last run_started per agent', which manual runs would confuse."""
        from condor.agents.runstore import get_run_store

        for meta in get_run_store().list_runs(slug, kind="scheduled", limit=20):
            if meta.get("scheduled_for") == fire_iso:
                return True
        return False

    def _overlaps(self, slug: str) -> bool:
        """Previous run still active, or leases held by any of this agent's
        runs, block a new fire (skip-with-warning, no queuing)."""
        from condor.agents.engine import get_all_engines

        for engine in get_all_engines().values():
            if engine.agent.slug == slug and engine.is_running:
                return True
        try:
            from condor.agents.runstore import get_run_store
            from condor.executors.service import peek_executor_runtime

            runtime = peek_executor_runtime()
            if runtime is None:
                return False
            holders = set(runtime.leases.snapshot().values())
            if not holders:
                return False
            store = get_run_store()
            for run_id in holders:
                path = store.find_run_path(run_id)
                if path is not None and store.slug_from_path(path) == slug:
                    return True
        except Exception:
            log.exception("overlap lease check failed for %s", slug)
            return True  # fail closed: don't stack runs on uncertain state
        return False

    # -- routines -----------------------------------------------------------

    async def _poll_routines(
        self, last: datetime, now: datetime, fired: dict
    ) -> None:
        data = self._routine_store.load()
        for schedule_id, entry in data.items():
            if entry.get("disabled"):
                continue
            try:
                due = fires_between(
                    str(entry["cron"]), str(entry.get("tz", "UTC")), last, now
                )
            except Exception:
                log.exception("schedule %s: bad cron", schedule_id)
                continue
            if not due:
                continue
            fire = due[-1]
            fire_iso = fire.astimezone(timezone.utc).isoformat()
            if entry.get("last_fired") == fire_iso:
                continue
            reason = self._routine_disabled_reason(entry)
            if reason:
                self._routine_store.disable(schedule_id, reason)
                await self._notify(
                    f"⏸️ Routine schedule {schedule_id} ({entry['routine']}) "
                    f"disabled: {reason}",
                    entry.get("agent_slug", ""),
                )
                continue
            # WRITE-AHEAD: advance last_fired BEFORE spawning — a crash in
            # between loses this fire (skip semantics), never duplicates it.
            self._routine_store.mark_fired(schedule_id, fire_iso)
            fired["routines"].append(f"{entry['routine']}@{fire_iso}")
            try:
                await self._run_routine(entry)
            except Exception as e:
                log.exception("scheduled routine %s failed", entry["routine"])
                await self._notify(
                    f"🚨 Scheduled routine {entry['routine']} failed: {e}",
                    entry.get("agent_slug", ""),
                )

    def _routine_disabled_reason(self, entry: dict) -> str:
        """A dangling target disables the schedule explicitly — never a
        dangling fire, never a deletion blocker."""
        slug = entry.get("agent_slug", "")
        if slug:
            from condor.agents.service import AgentService

            svc = AgentService()
            if svc.store.get(slug) is None:
                return f"owning agent '{slug}' no longer exists"
            if svc.is_tombstoned(slug):
                return f"owning agent '{slug}' is tombstoned"
        try:
            from condor.routine_store import get_routine_store

            if get_routine_store().get_routine(entry["routine"], slug or None) is None:
                return f"routine '{entry['routine']}' no longer exists"
        except Exception:
            log.exception("routine existence check failed")
        return ""

    async def _run_routine(self, entry: dict) -> Any:
        if self._routine_runner is not None:
            return await self._routine_runner(entry)
        from condor.routines_worker import run_routine_in_worker

        return await run_routine_in_worker(
            entry["routine"],
            entry.get("config") or {},
            agent_slug=entry.get("agent_slug", ""),
        )

    async def _notify(self, text: str, agent_slug: str) -> None:
        try:
            from condor.notifications import notify

            await notify(text, kind="scheduler", agent_id=agent_slug)
        except Exception:
            log.exception("scheduler notify failed")


_scheduler: Scheduler | None = None


def get_scheduler() -> Scheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler


def set_scheduler(s: Scheduler | None) -> None:
    """Test seam."""
    global _scheduler
    _scheduler = s
