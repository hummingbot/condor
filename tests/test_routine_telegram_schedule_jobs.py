"""ST-05 / ST-06: how Telegram routine jobs are handed to the JobQueue.

ST-05 — APScheduler's default ``misfire_grace_time`` is one second: a tick the
event loop reached later than that was dropped, not delayed, so a daily routine
silently lost its day. Every job now carries an explicit grace and ``coalesce``.

ST-06 — the daily menu promises the server's timezone, but the ``time`` handed
to ``run_daily`` was naive, which PTB schedules in UTC. New schedules attach the
local zone and store its name so restore agrees; a schedule persisted before
that has no zone and stays on UTC, where it has been firing all along.
"""

import asyncio
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import handlers.routines as hr

OWNER_ID = 7


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def run_once(self, callback, **kwargs):
        self.jobs.append({"kind": "once", **kwargs})

    def run_repeating(self, callback, **kwargs):
        self.jobs.append({"kind": "repeating", **kwargs})

    def run_daily(self, callback, **kwargs):
        self.jobs.append({"kind": "daily", **kwargs})

    def get_jobs_by_name(self, name):
        return []


class FakeContext:
    def __init__(self):
        self.user_data = {}
        self.job_queue = FakeJobQueue()
        self.application = None


class FakeApplication:
    def __init__(self, user_data):
        self.user_data = user_data
        self.job_queue = FakeJobQueue()


class FakeStore:
    def __init__(self):
        self._instances = {}

    def add_instance(self, instance_id, metadata):
        self._instances[instance_id] = metadata

    def register_stopper(self, instance_id, stopper):
        pass


@pytest.fixture(autouse=True)
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(hr, "get_routine_store", lambda: fake)
    monkeypatch.setattr(hr, "_local_tz_name", lambda: "Asia/Tokyo")
    return fake


def _create(schedule):
    context = FakeContext()
    instance_id = hr._create_scheduled_instance(
        context, OWNER_ID, "probe", {}, schedule, user_id=OWNER_ID
    )
    return context, instance_id, context.job_queue.jobs[0]


# ── ST-05 ──


@pytest.mark.parametrize(
    "schedule, grace",
    [
        ({"type": "once"}, hr._ONESHOT_GRACE_SEC),
        ({"type": "interval", "interval_sec": 300}, 300),
        ({"type": "daily", "daily_time": "09:00"}, hr._DAILY_GRACE_SEC),
    ],
)
def test_created_jobs_run_late_instead_of_being_skipped(schedule, grace):
    _, _, job = _create(schedule)
    assert job["job_kwargs"] == {"misfire_grace_time": grace, "coalesce": True}


def test_restored_jobs_carry_the_same_grace(monkeypatch):
    monkeypatch.setattr(hr, "get_routine", lambda name: object())
    instances = {
        "a": {
            "routine_name": "probe",
            "status": "running",
            "schedule": {"type": "interval", "interval_sec": 1800},
        },
        "b": {
            "routine_name": "probe",
            "status": "running",
            "schedule": {"type": "daily", "daily_time": "06:30"},
        },
    }
    app = FakeApplication({OWNER_ID: {"routine_instances": instances}})

    assert asyncio.run(hr.restore_scheduled_jobs(app)) == 2

    by_kind = {job["kind"]: job for job in app.job_queue.jobs}
    assert by_kind["repeating"]["job_kwargs"] == {
        "misfire_grace_time": 1800,
        "coalesce": True,
    }
    assert by_kind["daily"]["job_kwargs"] == {
        "misfire_grace_time": hr._DAILY_GRACE_SEC,
        "coalesce": True,
    }


# ── ST-06 ──


def test_a_new_daily_schedule_fires_in_the_server_zone_and_records_it(store):
    context, instance_id, job = _create({"type": "daily", "daily_time": "09:15"})

    assert (job["time"].hour, job["time"].minute) == (9, 15)
    assert job["time"].tzinfo == ZoneInfo("Asia/Tokyo")
    persisted = context.user_data["routine_instances"][instance_id]["schedule"]
    assert persisted["tz"] == "Asia/Tokyo"
    assert store._instances[instance_id]["schedule"]["tz"] == "Asia/Tokyo"
    assert hr._format_schedule(persisted) == "Daily @ 09:15 Asia/Tokyo"


def test_restore_uses_the_zone_the_schedule_was_created_in(monkeypatch):
    monkeypatch.setattr(hr, "get_routine", lambda name: object())
    # The server's zone changed since: the stored one still wins.
    monkeypatch.setattr(hr, "_local_tz_name", lambda: "America/New_York")
    instances = {
        "a": {
            "routine_name": "probe",
            "status": "running",
            "schedule": {"type": "daily", "daily_time": "09:15", "tz": "Asia/Tokyo"},
        }
    }
    app = FakeApplication({OWNER_ID: {"routine_instances": instances}})

    asyncio.run(hr.restore_scheduled_jobs(app))

    assert app.job_queue.jobs[0]["time"].tzinfo == ZoneInfo("Asia/Tokyo")


def test_a_daily_schedule_persisted_without_a_zone_stays_on_utc(monkeypatch):
    monkeypatch.setattr(hr, "get_routine", lambda name: object())
    legacy = {"type": "daily", "daily_time": "09:15"}
    instances = {
        "a": {"routine_name": "probe", "status": "running", "schedule": legacy}
    }
    app = FakeApplication({OWNER_ID: {"routine_instances": instances}})

    asyncio.run(hr.restore_scheduled_jobs(app))

    assert app.job_queue.jobs[0]["time"].tzinfo is timezone.utc
    assert hr._format_schedule(legacy) == "Daily @ 09:15 UTC"


def test_a_fixed_offset_zone_round_trips():
    assert hr._schedule_tz({"tz": "-03:30"}) == timezone(
        -timedelta(hours=3, minutes=30)
    )
    assert hr._schedule_tz({"tz": "not/a-zone"}) is timezone.utc
