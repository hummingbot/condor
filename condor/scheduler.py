"""Condor's clock.

One scheduler for the whole process, in both modes. Everything that used to ask
``application.job_queue`` what time it is — scheduled routines, signal pipelines,
the periodic update check, the telemetry flush and heartbeat — asks this instead.

Why not PTB's ``JobQueue``: it only exists as an attribute of a PTB
``Application``, so a Condor that runs without Telegram had to build a Telegram
client from a placeholder token in order to own two timers. It is also not the
only clock — ``RoutineStore`` ran dashboard- and MCP-created schedules as bare
``while True: sleep(interval)`` tasks with no names, no removal and no daily
trigger. This module is the single clock both of those collapse into.

Why APScheduler rather than hand-rolled ``asyncio`` timers: ``JobQueue`` is
itself a thin wrapper over ``AsyncIOScheduler``, the dependency already ships
with ``python-telegram-bot[job-queue]``, and ``daily`` is the case a sleep loop
gets wrong (DST, clock steps, drift). The construction here mirrors PTB's
exactly — ``timezone=UTC`` and an ``AsyncIOExecutor``, everything else left at
APScheduler's own job defaults (``coalesce=True``, ``misfire_grace_time=1``,
``max_instances=1``).

**The UTC default is load-bearing.** Callers hand :meth:`Scheduler.run_daily` a
naive ``datetime.time``, which is resolved in the scheduler's timezone. Every
daily routine and signal that exists today fires at that time *UTC*; a scheduler
defaulting to the machine's zone would silently move all of them with nothing in
any UI changing.
"""

from __future__ import annotations

import asyncio
import datetime as dtm
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

from apscheduler.events import EVENT_JOB_REMOVED
from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

UTC = dtm.timezone.utc

# APScheduler's cron ``day_of_week`` names, indexed the way PTB indexes them:
# 0 is Sunday. Kept identical so a ``days=`` tuple written for ``run_daily``
# means the same thing before and after this module existed.
_CRON_DAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")

ALL_DAYS: tuple[int, ...] = tuple(range(7))

# The persisted per-user buckets a job callback reads through
# :meth:`JobContext.owner_data`. Bound once at boot from PTB's
# ``application.user_data``; this is the last thing a job still needs the
# Application for, deliberately concentrated in one place so the follow-on
# feature that moves routine state onto disk has one call site to change.
_user_data: Mapping[int, dict] = {}


def bind_user_data(mapping: Mapping[int, dict]) -> None:
    """Point job callbacks at the per-user buckets they read.

    Reads only: PTB hands out ``application.user_data`` as a ``mappingproxy``,
    so a bucket cannot be created through it — but the buckets themselves are
    ordinary dicts, which is where every job's writes land.
    """
    global _user_data
    _user_data = mapping


@dataclass
class Job:
    """A scheduled callback, addressable by the name its caller gave it.

    The attribute surface is PTB's ``telegram.ext.Job`` minus what nothing used:
    ``data`` is the opaque payload every callback opens with, ``chat_id`` and
    ``user_id`` are the identities carried alongside it.
    """

    id: str
    callback: Callable[["JobContext"], Any]
    name: str | None = None
    data: Any = None
    chat_id: int | None = None
    user_id: int | None = None
    _scheduler: "Scheduler | None" = field(default=None, repr=False, compare=False)

    def remove(self) -> None:
        """Drop this job. It will not run again.

        Named ``remove`` rather than PTB's ``schedule_removal`` because nothing
        is scheduled: the job is gone when this returns.
        """
        if self._scheduler is not None:
            self._scheduler.remove(self.id)


class JobContext:
    """What a job callback is handed, in place of PTB's ``CallbackContext``.

    Deliberately small: the callbacks that used to take a full
    ``CallbackContext`` used five attributes between them — ``job.data``, a bot
    to send with, the owner's ``user_data`` bucket, and the three
    ``_chat_id``/``_instance_id``/``_user_data`` that ``_execute_routine`` sets
    on the way into a routine. Arbitrary attributes are settable for that last
    reason, exactly as :class:`condor.routine_store.WebRoutineContext` allows
    them.
    """

    def __init__(self, job: Job) -> None:
        self.job = job

    @property
    def bot(self):
        """The best sender this process has: real bot, ``_HttpBot`` or the bell.

        The same ladder a delegation completion notice climbs, so a job and a
        delegation can never disagree about how to reach a user — and so a job
        in local mode records on the dashboard instead of handing its message to
        a Telegram client built from a placeholder token.
        """
        from condor.agents.delegate import resolve_bot

        return resolve_bot()

    @property
    def user_data(self) -> dict | None:
        """PTB's semantics: the owner's bucket when the job names a user.

        ``None`` when it does not — jobs created with only a ``chat_id`` got
        ``None`` from PTB too, and at least one caller (signals) reads it.

        Read-only lookup, never creating: what is bound here is PTB's
        ``application.user_data``, which is a ``mappingproxy``. Its *buckets*
        are ordinary dicts, so everything a job actually does — updating an
        instance record in place — writes through; only minting a new top-level
        bucket is impossible, and no job does that.
        """
        if self.job.user_id is None:
            return None
        return _user_data.get(self.job.user_id)

    def owner_data(self, owner_id: int | None, chat_id: int) -> dict:
        """The starter's ``user_data`` bucket, as seen from a job.

        Buckets are keyed by *user* id, which is what the routine instance dicts
        are written through — so a job must look itself up by the owner stamped
        at creation, never by ``chat_id``. In a private chat the two coincide; in
        a group the chat id is negative and its bucket does not exist at all.
        ``owner_id`` falls back to ``chat_id`` so jobs persisted before the owner
        was carried in the payload resolve exactly as they did before.
        """
        return _user_data.get(owner_id if owner_id is not None else chat_id, {})


class Scheduler:
    """The five verbs everything in Condor actually schedules with."""

    def __init__(self) -> None:
        self._executor = AsyncIOExecutor()
        self._scheduler = AsyncIOScheduler(
            timezone=UTC, executors={"default": self._executor}
        )
        self._jobs: dict[str, Job] = {}
        self._by_name: dict[str, set[str]] = {}
        # APScheduler is the source of truth for what is still scheduled, so the
        # name index is maintained from its removal event rather than by hand:
        # that covers explicit removal *and* a one-shot retiring itself after it
        # fires, which no call site would otherwise tell us about.
        self._scheduler.add_listener(self._on_job_removed, EVENT_JOB_REMOVED)

    # ── lifecycle ──

    def start(self) -> None:
        """Start ticking. Idempotent; needs a running event loop."""
        if not self._scheduler.running:
            self._scheduler.start()

    async def stop(self, wait: bool = True) -> None:
        """Stop ticking, optionally draining jobs already in flight. Idempotent.

        The ``wait`` dance is PTB's: ``AsyncIOExecutor`` cancels its pending
        futures on shutdown rather than awaiting them, and ``shutdown()`` returns
        before the loop has actually torn the scheduler down.
        """
        if wait:
            # ``_pending_futures`` only exists once the executor has been
            # started, and stopping something that never ran must be a no-op.
            await asyncio.gather(
                *getattr(self._executor, "_pending_futures", ()),
                return_exceptions=True,
            )
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)
            await asyncio.sleep(0.01)

    # ── the verbs ──

    def run_once(
        self,
        callback: Callable[[JobContext], Any],
        when: float | dtm.timedelta | dtm.datetime,
        *,
        data: Any = None,
        name: str | None = None,
        chat_id: int | None = None,
        user_id: int | None = None,
    ) -> Job:
        """Run ``callback`` once, ``when`` seconds from now."""
        return self._add(
            callback,
            data=data,
            name=name,
            chat_id=chat_id,
            user_id=user_id,
            trigger="date",
            run_date=self._at(when),
        )

    def run_repeating(
        self,
        callback: Callable[[JobContext], Any],
        interval: float | dtm.timedelta,
        *,
        first: float | dtm.timedelta | dtm.datetime | None = None,
        data: Any = None,
        name: str | None = None,
        chat_id: int | None = None,
        user_id: int | None = None,
    ) -> Job:
        """Run ``callback`` every ``interval`` seconds.

        ``first`` is when the first run happens, in seconds from now. Left
        unset it is one full ``interval`` from now — PTB's default, and the
        reason it is ``None`` here rather than ``0``: with an interval trigger a
        start date of *exactly now* also means "one interval from now", so a
        ``0`` default would read as "immediately" and not be.
        """
        if isinstance(interval, dtm.timedelta):
            interval = interval.total_seconds()
        return self._add(
            callback,
            data=data,
            name=name,
            chat_id=chat_id,
            user_id=user_id,
            trigger="interval",
            seconds=interval,
            start_date=self._at(first) if first is not None else None,
        )

    def run_daily(
        self,
        callback: Callable[[JobContext], Any],
        time: dtm.time,
        *,
        days: tuple[int, ...] = ALL_DAYS,
        data: Any = None,
        name: str | None = None,
        chat_id: int | None = None,
        user_id: int | None = None,
    ) -> Job:
        """Run ``callback`` at ``time`` every day in ``days``.

        A naive ``time`` is resolved in the scheduler's timezone, which is UTC —
        see the module docstring for why that is not an implementation detail.
        """
        return self._add(
            callback,
            data=data,
            name=name,
            chat_id=chat_id,
            user_id=user_id,
            trigger="cron",
            day_of_week=",".join(_CRON_DAYS[d] for d in days),
            hour=time.hour,
            minute=time.minute,
            second=time.second,
            timezone=time.tzinfo or self._scheduler.timezone,
        )

    # ── lookup and removal ──

    def jobs(self) -> tuple[Job, ...]:
        """Every job still scheduled, in no particular order."""
        return tuple(self._jobs.values())

    def jobs_by_name(self, name: str) -> list[Job]:
        """Every scheduled job registered under ``name``.

        A list, not a single job: names are the caller's and nothing enforces
        that they are unique, which is how PTB's ``get_jobs_by_name`` behaved.
        """
        return [
            self._jobs[jid] for jid in self._by_name.get(name, ()) if jid in self._jobs
        ]

    def remove_by_name(self, name: str) -> int:
        """Remove every job under ``name``. Returns how many were removed."""
        removed = 0
        for job in self.jobs_by_name(name):
            self.remove(job.id)
            removed += 1
        return removed

    def remove(self, job_id: str) -> None:
        """Remove one job by its APScheduler id. Unknown ids are ignored."""
        if job_id not in self._jobs:
            return
        try:
            self._scheduler.remove_job(job_id)
        except Exception:  # noqa: BLE001 - already gone is the outcome we wanted
            self._forget(job_id)

    # ── internals ──

    def _at(self, when: float | dtm.timedelta | dtm.datetime) -> dtm.datetime:
        """Resolve a relative offset into an absolute instant, PTB-style."""
        if isinstance(when, dtm.datetime):
            return when
        if isinstance(when, dtm.timedelta):
            return dtm.datetime.now(self._scheduler.timezone) + when
        return dtm.datetime.now(self._scheduler.timezone) + dtm.timedelta(seconds=when)

    def _add(
        self,
        callback: Callable[[JobContext], Any],
        *,
        data: Any,
        name: str | None,
        chat_id: int | None,
        user_id: int | None,
        **job_kwargs: Any,
    ) -> Job:
        name = name or getattr(callback, "__name__", None)
        # Our own id, minted before ``add_job`` so the dispatch closure can be a
        # plain ``(job_id,)`` argument: APScheduler holds no reference to the
        # payload, and the payload is looked up fresh on every tick.
        job_id = uuid4().hex
        job = Job(
            id=job_id,
            callback=callback,
            name=name,
            data=data,
            chat_id=chat_id,
            user_id=user_id,
            _scheduler=self,
        )
        self._jobs[job_id] = job
        if name is not None:
            self._by_name.setdefault(name, set()).add(job_id)
        try:
            self._scheduler.add_job(
                self._dispatch, id=job_id, name=name, args=(job,), **job_kwargs
            )
        except Exception:
            self._forget(job_id)
            raise
        return job

    async def _dispatch(self, job: Job) -> None:
        """Run one job's callback. A failure is logged, never propagated.

        APScheduler would otherwise only report it through an event listener,
        and a routine raising must not silence its own schedule.

        The job is passed by value rather than looked up by id: a one-shot is
        submitted to the executor and *then* retired, so by the time this runs
        the name index has already forgotten it.
        """
        try:
            await job.callback(JobContext(job))
        except Exception:  # noqa: BLE001
            logger.exception("Scheduled job %s failed", job.name or job.id)

    def _on_job_removed(self, event) -> None:
        self._forget(event.job_id)

    def _forget(self, job_id: str) -> None:
        job = self._jobs.pop(job_id, None)
        if job is None or job.name is None:
            return
        ids = self._by_name.get(job.name)
        if ids is None:
            return
        ids.discard(job_id)
        if not ids:
            del self._by_name[job.name]


_scheduler: Scheduler | None = None


def get_scheduler() -> Scheduler:
    """The process's one scheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler
