"""ST-10: the web/MCP scheduler keeps its cadence, bounds its interval, re-resolves
the routine each tick and backs off a schedule that keeps failing.

Before: the period was interval + run time; ``interval_sec <= 0`` became a
back-to-back loop (``asyncio.sleep`` returns at once for both); the
``RoutineInfo`` resolved at schedule time ran for ever, edits and revoked
consent notwithstanding; and a routine failing every tick retried at full rate,
ringing the owner's bell each time.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from pydantic import BaseModel
from starlette.testclient import TestClient

import condor.routine_store as rs
import condor.web.routes.routines as routes_routines
from condor.routine_store import (
    MAX_SCHEDULE_INTERVAL_SEC,
    MIN_SCHEDULE_INTERVAL_SEC,
    RoutineStore,
    ScheduleIntervalError,
    _failure_backoff,
    _next_slot_delay,
)
from condor.web.auth import get_current_user
from condor.web.models import WebUser


@pytest.fixture(autouse=True)
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(tmp_path))


class Config(BaseModel):
    pass


def _routine(run_fn):
    return SimpleNamespace(
        name="probe", source="global", config_class=Config, run_fn=run_fn
    )


# ── Cadence ──


def test_the_next_slot_is_anchored_not_relative_to_the_end_of_the_run():
    # Started at t=1000, every 300s. A run that took 40s waits 260, not 300.
    assert _next_slot_delay(1000.0, 300, 1040.0) == pytest.approx(260.0)
    # An instant run waits the whole interval.
    assert _next_slot_delay(1000.0, 300, 1000.0) == pytest.approx(300.0)


def test_a_run_that_overran_skips_the_missed_slots():
    # 700s in: slots at 1300 and 1600 are gone, the next one is 1900.
    assert _next_slot_delay(1000.0, 300, 1700.0) == pytest.approx(200.0)
    # Landing exactly on a slot never yields a zero-delay burst.
    assert _next_slot_delay(1000.0, 300, 1600.0) == pytest.approx(300.0)


def test_backoff_starts_after_the_threshold_and_is_capped():
    n = rs._SCHEDULE_FAILURES_BEFORE_BACKOFF
    assert _failure_backoff(60, n - 1) == 0.0
    assert _failure_backoff(60, n) == 120
    assert _failure_backoff(60, n + 1) == 240
    assert _failure_backoff(60, n + 50) == rs._SCHEDULE_MAX_BACKOFF_SEC
    # A schedule already slower than the cap is never made faster by it.
    assert _failure_backoff(7200, n + 3) == 7200


# ── Bounds ──


@pytest.mark.parametrize(
    "interval",
    [0, -5, MIN_SCHEDULE_INTERVAL_SEC - 1, MAX_SCHEDULE_INTERVAL_SEC + 1, 12.5, "60"],
)
def test_schedule_refuses_an_out_of_bounds_interval(interval):
    store = RoutineStore()

    async def scenario():
        with pytest.raises(ScheduleIntervalError):
            await store.schedule("probe", {}, "srv", interval_sec=interval)

    asyncio.run(scenario())
    assert store.list_instances() == []


@pytest.mark.parametrize("interval", [0, -1, 9, 86401])
def test_the_route_answers_422_for_an_out_of_bounds_interval(monkeypatch, interval):
    monkeypatch.setattr(routes_routines, "check_server_access", lambda *a, **kw: None)
    app = FastAPI()
    app.include_router(routes_routines.router)
    app.dependency_overrides[get_current_user] = lambda: WebUser(
        id=1, username="u", first_name="U", role="user"
    )

    response = TestClient(app).post(
        "/routines/schedule",
        json={"routine_name": "probe", "server_name": "srv", "interval_sec": interval},
    )

    assert response.status_code == 422


# ── Ticks ──


def _drive(monkeypatch, store, resolver, *, ticks, interval=60):
    """Run the scheduler loop for ``ticks`` ticks with sleeping stubbed out."""
    delays = []
    real_sleep = asyncio.sleep
    calls = {"n": 0}

    def _resolve(name):
        calls["n"] += 1
        if calls["n"] > ticks:
            store._instances.pop("i1", None)
            return None
        return resolver(calls["n"])

    async def _sleep(delay, *a, **kw):
        delays.append(delay)
        await real_sleep(0)

    async def _quiet(*a, **kw):
        return None

    monkeypatch.setattr(store, "_resolve_routine", _resolve)
    monkeypatch.setattr(store, "_report_run", _quiet)
    monkeypatch.setattr(store, "_fire_hooks", _quiet)
    monkeypatch.setattr(rs.asyncio, "sleep", _sleep)
    store._instances["i1"] = store._new_instance_meta(
        "probe", {}, "srv", 0, source="web", status="scheduled"
    )
    snapshots = []

    async def scenario():
        task = asyncio.create_task(
            store._run_scheduled("i1", "probe", {}, "srv", interval)
        )
        while not task.done():
            if "i1" in store._instances:
                snapshots.append(dict(store._instances["i1"]))
            await real_sleep(0)
        await task

    asyncio.run(scenario())
    return delays, snapshots


def test_an_edited_routine_is_picked_up_on_the_next_tick(monkeypatch):
    ran = []

    async def old(config, ctx):
        ran.append("old")
        return "old"

    async def new(config, ctx):
        ran.append("new")
        return "new"

    store = RoutineStore()
    _drive(
        monkeypatch,
        store,
        lambda tick: _routine(old if tick == 1 else new),
        ticks=3,
    )

    assert ran == ["old", "new", "new"]


def test_a_routine_that_stops_resolving_is_not_run_and_the_schedule_survives(
    monkeypatch,
):
    ran = []

    async def run_fn(config, ctx):
        ran.append(1)
        return "ok"

    def resolver(tick):
        if tick == 2:
            raise RuntimeError("awaiting consent")
        return _routine(run_fn)

    store = RoutineStore()
    _, snapshots = _drive(monkeypatch, store, resolver, ticks=3)

    assert len(ran) == 2  # ticks 1 and 3; tick 2 ran nothing
    assert any(s.get("error") == "awaiting consent" for s in snapshots)
    assert all(s["status"] != "failed" for s in snapshots)


def test_a_schedule_failing_every_tick_backs_off(monkeypatch):
    async def boom(config, ctx):
        raise RuntimeError("exchange down")

    store = RoutineStore()
    n = rs._SCHEDULE_FAILURES_BEFORE_BACKOFF
    delays, _ = _drive(
        monkeypatch, store, lambda tick: _routine(boom), ticks=n + 2, interval=60
    )

    assert all(d <= 60 for d in delays[: n - 1])
    assert delays[n - 1 : n + 2] == [120, 240, 480]


def test_one_success_resets_the_backoff(monkeypatch):
    n = rs._SCHEDULE_FAILURES_BEFORE_BACKOFF

    async def boom(config, ctx):
        raise RuntimeError("exchange down")

    async def fine(config, ctx):
        return "ok"

    store = RoutineStore()
    delays, snapshots = _drive(
        monkeypatch,
        store,
        lambda tick: _routine(fine if tick == n + 1 else boom),
        ticks=n + 2,
        interval=60,
    )

    assert delays[n - 1] == 120
    assert delays[n] <= 60 and delays[n + 1] <= 60
    assert snapshots[-1]["consecutive_failures"] == 1
