"""ST-02: the dashboard's Stop must really stop a Telegram-started routine.

A Telegram instance is only mirrored into ``RoutineStore``; its task lives in
the handler module and its record in the owner's ``user_data``. ``stop`` used
to find no task of its own, drop the mirror and answer ``True`` — the routine
kept running, invisible, and a restart restored it from the pickle. The
handlers now register a stopper with the instance; where there is none the
route answers 409 instead of a false success.
"""

import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.routines as routes_routines
import handlers.routines as tg_routines
from condor.routine_store import RoutineStopUnavailable, RoutineStore
from condor.web.auth import get_current_user
from condor.web.models import WebUser

STARTER = WebUser(id=111, username="starter", first_name="S", role="user")
GROUP_CHAT = -1002222222222


class FakeJob:
    def __init__(self):
        self.removed = False

    def schedule_removal(self):
        self.removed = True


class FakeJobQueue:
    def __init__(self):
        self.jobs = {}

    def run_once(self, callback, **kwargs):
        self.jobs[kwargs["name"]] = FakeJob()

    run_repeating = run_daily = run_once

    def get_jobs_by_name(self, name):
        return [self.jobs[name]] if name in self.jobs else []


class FakeApplication:
    def __init__(self):
        self.user_data = {STARTER.id: {}}
        self.job_queue = FakeJobQueue()
        self.bot = None
        self.marked = []

    def mark_data_for_update_persistence(self, user_ids=None, chat_ids=None):
        self.marked.append(user_ids)


class FakeContext:
    def __init__(self, application):
        self.application = application
        self.user_data = application.user_data[STARTER.id]
        self.job_queue = application.job_queue


class FakeConfigManager:
    def is_admin(self, user_id):
        return False


@pytest.fixture
def store(monkeypatch):
    real = RoutineStore()
    monkeypatch.setattr(tg_routines, "get_routine_store", lambda: real)
    monkeypatch.setattr(routes_routines, "get_routine_store", lambda: real)
    monkeypatch.setattr(routes_routines, "get_config_manager", FakeConfigManager)
    monkeypatch.setattr(tg_routines, "get_routine", lambda name: None)
    return real


def _client():
    app = FastAPI()
    app.include_router(routes_routines.router)
    app.dependency_overrides[get_current_user] = lambda: STARTER
    return TestClient(app)


def test_web_stop_cancels_a_telegram_continuous_routine(store, monkeypatch):
    started = asyncio.Event()

    async def forever(*args, **kwargs):
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(tg_routines, "_run_continuous_routine", forever)

    async def scenario():
        application = FakeApplication()
        context = FakeContext(application)
        instance_id = tg_routines._create_continuous_instance(
            context, GROUP_CHAT, "watcher", {}, user_id=STARTER.id
        )
        task = tg_routines._continuous_tasks[instance_id]
        await started.wait()

        response = await asyncio.to_thread(
            _client().post, f"/routines/instances/{instance_id}/stop"
        )
        assert response.status_code == 200
        # The stopper ran on the test client's own loop; let the cancel land.
        for _ in range(50):
            if task.done():
                break
            await asyncio.sleep(0.01)
        assert task.cancelled()
        assert instance_id not in tg_routines._continuous_tasks
        assert instance_id not in context.user_data["routine_instances"]
        assert application.marked == [STARTER.id]
        assert store.get_instance(instance_id) is None

    asyncio.run(scenario())


def test_web_stop_removes_a_telegram_interval_job(store):
    application = FakeApplication()
    context = FakeContext(application)
    instance_id = tg_routines._create_scheduled_instance(
        context,
        STARTER.id,
        "probe",
        {},
        {"type": "interval", "interval_sec": 60},
        user_id=STARTER.id,
    )
    job = application.job_queue.jobs[tg_routines._job_name(STARTER.id, instance_id)]

    assert _client().post(f"/routines/instances/{instance_id}/stop").status_code == 200

    assert job.removed
    assert instance_id not in context.user_data["routine_instances"]
    assert store.get_instance(instance_id) is None


def test_web_stop_refuses_a_telegram_instance_it_cannot_reach(store):
    """No stopper registered: 409 and the record stays, never a false success."""
    store.add_instance(
        "orphan",
        {"routine_name": "probe", "source": "telegram", "user_id": STARTER.id},
    )

    with pytest.raises(RoutineStopUnavailable):
        store.stop("orphan")
    response = _client().post("/routines/instances/orphan/stop")

    assert response.status_code == 409
    assert store.get_instance("orphan") is not None


def test_a_failing_stopper_keeps_the_record(store):
    def boom():
        raise RuntimeError("job queue is gone")

    store.add_instance(
        "i1", {"routine_name": "probe", "source": "telegram", "user_id": 1}, boom
    )

    with pytest.raises(RoutineStopUnavailable):
        store.stop("i1")
    assert store.get_instance("i1") is not None


def test_telegram_side_stop_drops_the_stopper(store):
    application = FakeApplication()
    context = FakeContext(application)
    instance_id = tg_routines._create_scheduled_instance(
        context,
        STARTER.id,
        "probe",
        {},
        {"type": "interval", "interval_sec": 60},
        user_id=STARTER.id,
    )
    assert instance_id in store._stoppers

    assert tg_routines._stop_instance(context, STARTER.id, instance_id) is True

    assert instance_id not in store._stoppers
