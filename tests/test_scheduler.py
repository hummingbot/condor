"""Tests for FEAT-050: Condor owns its scheduler.

The five verbs everything in the process schedules with — ``run_once``,
``run_repeating``, ``run_daily``, lookup-by-name and removal — plus the two
properties that are load-bearing and invisible: a naive daily ``time`` is
resolved in **UTC**, and a job that raises does not take its schedule down.
"""

import asyncio
import datetime as dtm

import pytest

from condor.scheduler import ALL_DAYS, Scheduler, bind_user_data


@pytest.fixture
def scheduler():
    return Scheduler()


def run(scheduler, scenario):
    """Run one async scenario, always stopping the scheduler inside its loop.

    ``AsyncIOScheduler`` holds the loop it was started on, so it has to be shut
    down before ``asyncio.run`` closes that loop — including when the scenario
    fails.
    """

    async def wrapper():
        try:
            await scenario()
        finally:
            await scheduler.stop(wait=False)

    asyncio.run(wrapper())


async def _wait(event, timeout=5):
    await asyncio.wait_for(event.wait(), timeout=timeout)


# ── run_once ──


def test_run_once_fires_exactly_once(scheduler):
    async def scenario():
        runs = []
        done = asyncio.Event()

        async def cb(context):
            runs.append(context.job.data)
            done.set()

        scheduler.run_once(cb, 0.01, data={"hello": "world"}, name="probe")
        scheduler.start()
        await _wait(done)
        # Well past the single fire: nothing else may arrive.
        await asyncio.sleep(0.2)
        assert runs == [{"hello": "world"}]

    run(scheduler, scenario)


def test_a_fired_one_shot_stops_being_findable(scheduler):
    """A one-shot retires itself, so its name must not linger in the index."""

    async def scenario():
        done = asyncio.Event()

        async def cb(context):
            done.set()

        scheduler.run_once(cb, 0.01, name="retiring")
        scheduler.start()
        await _wait(done)
        await asyncio.sleep(0.1)
        assert scheduler.jobs_by_name("retiring") == []
        assert scheduler.jobs() == ()

    run(scheduler, scenario)


# ── run_repeating ──


def test_run_repeating_repeats_and_honours_first(scheduler):
    async def scenario():
        ticks = []
        third = asyncio.Event()

        async def cb(context):
            ticks.append(asyncio.get_running_loop().time())
            if len(ticks) == 3:
                third.set()

        started = asyncio.get_running_loop().time()
        scheduler.run_repeating(cb, interval=0.05, first=0.01, name="ticker")
        scheduler.start()
        await _wait(third)
        assert len(ticks) >= 3
        # `first` is honoured: the first tick is not one whole interval away.
        assert ticks[0] - started < 0.05

    run(scheduler, scenario)


def test_a_failing_job_keeps_its_schedule(scheduler):
    """A routine that raises must not silence its own timer."""

    async def scenario():
        calls = []
        twice = asyncio.Event()

        async def cb(context):
            calls.append(1)
            if len(calls) == 2:
                twice.set()
            raise RuntimeError("boom")

        scheduler.run_repeating(cb, interval=0.05, first=0.01, name="angry")
        scheduler.start()
        await _wait(twice)
        assert len(calls) >= 2

    run(scheduler, scenario)


# ── run_daily ──


def test_run_daily_resolves_a_naive_time_in_utc(scheduler):
    """Every daily routine and signal that exists fires at its time *UTC*.

    Pinned by assertion rather than inferred from the scheduler's timezone: a
    scheduler defaulting to the machine's zone would move every user's daily job
    with nothing in any UI changing.
    """

    async def cb(context):  # pragma: no cover - never fires in this test
        pass

    job = scheduler.run_daily(cb, dtm.time(hour=7, minute=30), name="dawn")
    aps_job = scheduler._scheduler.get_job(job.id)

    assert aps_job.trigger.timezone == dtm.timezone.utc

    # And the concrete instant it resolves to, computed from a moment whose UTC
    # answer is unambiguous: 06:00 UTC on a fixed date is followed by 07:30 UTC
    # the same day, whatever zone the machine running this is in.
    now = dtm.datetime(2026, 3, 4, 6, 0, tzinfo=dtm.timezone.utc)
    assert aps_job.trigger.get_next_fire_time(None, now) == dtm.datetime(
        2026, 3, 4, 7, 30, tzinfo=dtm.timezone.utc
    )


def test_run_daily_defaults_to_every_day(scheduler):
    async def cb(context):  # pragma: no cover - never fires in this test
        pass

    job = scheduler.run_daily(cb, dtm.time(hour=9), name="every-day")
    fields = {
        f.name: str(f) for f in scheduler._scheduler.get_job(job.id).trigger.fields
    }
    assert fields["day_of_week"] == "sun,mon,tue,wed,thu,fri,sat"
    assert ALL_DAYS == (0, 1, 2, 3, 4, 5, 6)


# ── names ──


def test_jobs_by_name_returns_every_job_under_that_name(scheduler):
    async def cb(context):  # pragma: no cover - never fires in this test
        pass

    a = scheduler.run_repeating(cb, interval=60, name="dupe")
    b = scheduler.run_repeating(cb, interval=60, name="dupe")

    assert {job.id for job in scheduler.jobs_by_name("dupe")} == {a.id, b.id}
    assert scheduler.jobs_by_name("nobody") == []


def test_remove_by_name_removes_all_of_them(scheduler):
    async def cb(context):  # pragma: no cover - never fires in this test
        pass

    scheduler.run_repeating(cb, interval=60, name="doomed")
    scheduler.run_repeating(cb, interval=60, name="doomed")
    scheduler.run_repeating(cb, interval=60, name="spared")

    assert scheduler.remove_by_name("doomed") == 2
    assert scheduler.jobs_by_name("doomed") == []
    assert len(scheduler.jobs_by_name("spared")) == 1
    assert scheduler.remove_by_name("doomed") == 0


def test_a_removed_job_never_runs(scheduler):
    async def scenario():
        runs = []

        async def cb(context):
            runs.append(1)

        job = scheduler.run_repeating(cb, interval=0.05, first=0.01, name="cancelled")
        scheduler.start()
        job.remove()
        await asyncio.sleep(0.2)
        assert runs == []
        assert scheduler.jobs_by_name("cancelled") == []

    run(scheduler, scenario)


def test_removing_a_job_twice_is_harmless(scheduler):
    async def cb(context):  # pragma: no cover - never fires in this test
        pass

    job = scheduler.run_repeating(cb, interval=60, name="twice")
    job.remove()
    job.remove()
    assert scheduler.jobs() == ()


# ── lifecycle ──


def test_start_and_stop_are_idempotent(scheduler):
    async def scenario():
        scheduler.start()
        scheduler.start()
        await scheduler.stop()
        await scheduler.stop()
        assert not scheduler._scheduler.running

    run(scheduler, scenario)


def test_stop_before_start_is_harmless(scheduler):
    asyncio.run(scheduler.stop())


# ── the context a callback is handed ──


def test_owner_data_reads_the_bucket_the_owner_is_keyed_by(scheduler):
    """A group chat has no bucket of its own; the starter's user id has one."""

    async def scenario():
        seen = {}
        done = asyncio.Event()

        async def cb(context):
            seen["owner"] = context.owner_data(111, -100222)
            seen["fallback"] = context.owner_data(None, 111)
            seen["missing"] = context.owner_data(None, -100222)
            done.set()

        bind_user_data({111: {"routine_instances": {"i1": {}}}})
        scheduler.run_once(cb, 0.01, name="ctx")
        scheduler.start()
        await _wait(done)

        assert seen["owner"] == {"routine_instances": {"i1": {}}}
        assert seen["fallback"] == {"routine_instances": {"i1": {}}}
        assert seen["missing"] == {}

    try:
        run(scheduler, scenario)
    finally:
        bind_user_data({})


def test_user_data_follows_ptbs_rule(scheduler):
    """``None`` unless the job names a user — signals read it and relied on it."""

    async def scenario():
        seen = {}
        done = asyncio.Event()

        async def cb(context):
            seen[context.job.name] = context.user_data
            if len(seen) == 2:
                done.set()

        bind_user_data({7: {"marker": True}})
        scheduler.run_once(cb, 0.01, name="chat-only", chat_id=-100222)
        scheduler.run_once(cb, 0.01, name="with-user", user_id=7)
        scheduler.start()
        await _wait(done)

        assert seen["chat-only"] is None
        assert seen["with-user"] == {"marker": True}

    try:
        run(scheduler, scenario)
    finally:
        bind_user_data({})
