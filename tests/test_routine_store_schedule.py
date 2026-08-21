"""Dashboard and MCP schedules run on the same clock as Telegram's (FEAT-050).

``RoutineStore.schedule`` used to mint a bare ``asyncio`` task looping over
``sleep(interval)``: no name, no removal by name, and — the visible half — no
daily trigger, so a routine scheduled from the dashboard could only ever repeat
on an interval while the same routine scheduled from Telegram could run once a
day. Both surfaces now register jobs on ``condor.scheduler``.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv.
"""

import asyncio
import datetime as dtm

import pytest

import condor.routine_store as rs
from condor.scheduler import Scheduler


@pytest.fixture
def scheduler(monkeypatch):
    """A scheduler per test, never started — nothing here needs a tick."""
    sched = Scheduler()
    monkeypatch.setattr(rs, "get_scheduler", lambda: sched)
    return sched


@pytest.fixture
def store(monkeypatch):
    s = rs.RoutineStore()
    monkeypatch.setattr(s, "_resolve_routine", lambda name: object())
    return s


def _job(scheduler, instance_id):
    jobs = scheduler.jobs_by_name(f"web:{instance_id}")
    assert len(jobs) == 1, f"expected exactly one job for {instance_id}"
    return scheduler._scheduler.get_job(jobs[0].id)


# ── the trigger the dashboard never had ──


def test_a_daily_web_schedule_fires_once_a_day_in_utc(store, scheduler):
    instance_id = asyncio.run(
        store.schedule("probe", {}, "srv", user_id=7, daily_time="07:30")
    )

    meta = store.get_instance(instance_id)
    assert meta["schedule"] == {"type": "daily", "daily_time": "07:30"}

    trigger = _job(scheduler, instance_id).trigger
    assert trigger.timezone == dtm.timezone.utc
    now = dtm.datetime(2026, 3, 4, 6, 0, tzinfo=dtm.timezone.utc)
    assert trigger.get_next_fire_time(None, now) == dtm.datetime(
        2026, 3, 4, 7, 30, tzinfo=dtm.timezone.utc
    )


def test_daily_time_wins_over_interval(store, scheduler):
    """The route sends both fields; only one of them can be the schedule."""
    instance_id = asyncio.run(
        store.schedule("probe", {}, "srv", interval_sec=60, daily_time="09:00")
    )
    assert store.get_instance(instance_id)["schedule"]["type"] == "daily"


# ── the interval case, unchanged behaviour on a new clock ──


def test_an_interval_web_schedule_repeats(store, scheduler):
    instance_id = asyncio.run(store.schedule("probe", {}, "srv", interval_sec=120))

    meta = store.get_instance(instance_id)
    assert meta["schedule"] == {"type": "interval", "interval_sec": 120}
    assert meta["status"] == "scheduled"

    trigger = _job(scheduler, instance_id).trigger
    assert trigger.interval == dtm.timedelta(seconds=120)


def test_a_scheduled_instance_no_longer_holds_a_task(store, scheduler):
    """The sleep loop is gone; the schedule is a job, not a coroutine."""
    instance_id = asyncio.run(store.schedule("probe", {}, "srv", interval_sec=60))
    assert instance_id not in store._tasks


# ── stopping ──


def test_stop_removes_the_job_as_well_as_the_instance(store, scheduler):
    instance_id = asyncio.run(store.schedule("probe", {}, "srv", interval_sec=60))

    assert store.stop(instance_id) is True
    assert scheduler.jobs_by_name(f"web:{instance_id}") == []
    assert store.get_instance(instance_id) is None


def test_two_schedules_are_stopped_independently(store, scheduler):
    """Names are per-instance, so stopping one must not touch the other."""
    first = asyncio.run(store.schedule("probe", {}, "srv", interval_sec=60))
    second = asyncio.run(store.schedule("probe", {}, "srv", interval_sec=60))

    store.stop(first)

    assert scheduler.jobs_by_name(f"web:{first}") == []
    assert len(scheduler.jobs_by_name(f"web:{second}")) == 1


def test_an_unknown_routine_is_still_rejected(store, scheduler, monkeypatch):
    monkeypatch.setattr(store, "_resolve_routine", lambda name: None)
    with pytest.raises(ValueError):
        asyncio.run(store.schedule("nope", {}, "srv", interval_sec=60))
    assert scheduler.jobs() == ()
