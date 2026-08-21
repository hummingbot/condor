"""A schedule persisted before FEAT-050 comes back on the same minute.

The clock changed owner; nothing about a routine's schedule did. These pin the
half of that promise a user would notice: ``restore_scheduled_jobs`` still
replays instances out of the pickle, under the same job names, and a daily one
still resolves its ``"HH:MM"`` in **UTC**. A scheduler defaulting to the
machine's zone would move every restored daily job with nothing in the UI
changing, and nothing else in the process would notice.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv.
"""

import asyncio
import datetime as dtm

import pytest

import handlers.routines as hr
from condor.scheduler import Scheduler

CHAT_ID = 4242


class FakeApplication:
    def __init__(self, user_data):
        self.user_data = user_data


@pytest.fixture
def scheduler(monkeypatch):
    sched = Scheduler()
    monkeypatch.setattr(hr, "get_scheduler", lambda: sched)
    return sched


@pytest.fixture(autouse=True)
def routine_exists(monkeypatch):
    """The routine a persisted instance names is still installed."""
    monkeypatch.setattr(hr, "get_routine", lambda name: object())
    monkeypatch.setattr(hr, "_sync_instance_to_store", lambda *a, **kw: None)


def _restore(schedule):
    instances = {
        "i1": {
            "routine_name": "probe",
            "config": {},
            "schedule": schedule,
            "status": "running",
            "source": "telegram",
            "user_id": CHAT_ID,
        }
    }
    app = FakeApplication({CHAT_ID: {"routine_instances": instances}})
    return asyncio.run(hr.restore_scheduled_jobs(app))


def test_a_persisted_daily_routine_is_restored_on_the_same_utc_minute(scheduler):
    assert _restore({"type": "daily", "daily_time": "07:30"}) == 1

    jobs = scheduler.jobs_by_name(hr._job_name(CHAT_ID, "i1"))
    assert len(jobs) == 1

    trigger = scheduler._scheduler.get_job(jobs[0].id).trigger
    assert trigger.timezone == dtm.timezone.utc
    now = dtm.datetime(2026, 3, 4, 6, 0, tzinfo=dtm.timezone.utc)
    assert trigger.get_next_fire_time(None, now) == dtm.datetime(
        2026, 3, 4, 7, 30, tzinfo=dtm.timezone.utc
    )


def test_a_persisted_interval_routine_is_restored(scheduler):
    assert _restore({"type": "interval", "interval_sec": 900}) == 1

    jobs = scheduler.jobs_by_name(hr._job_name(CHAT_ID, "i1"))
    trigger = scheduler._scheduler.get_job(jobs[0].id).trigger
    assert trigger.interval == dtm.timedelta(seconds=900)


def test_the_restored_job_carries_the_payload_its_callback_reads(scheduler):
    """The tick has no update to derive chat or owner from, so it is in the data."""
    _restore({"type": "daily", "daily_time": "09:00"})

    job = scheduler.jobs_by_name(hr._job_name(CHAT_ID, "i1"))[0]
    assert job.data["instance_id"] == "i1"
    assert job.data["chat_id"] == CHAT_ID
    assert job.data["user_id"] == CHAT_ID
    assert job.chat_id == CHAT_ID


def test_a_one_shot_that_never_completed_is_dropped_not_restored(scheduler):
    assert _restore({"type": "once"}) == 0
    assert scheduler.jobs() == ()
