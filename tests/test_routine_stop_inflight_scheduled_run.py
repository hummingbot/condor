"""Stop must also stop a scheduled firing that is already running (ST-02 follow-up).

The stopper removed the JobQueue job, which only stops FUTURE firings. An
interval, daily or one-time firing already inside ``routine.run_fn`` runs in the
JobQueue's own task, with no entry in ``_continuous_tasks``, so it kept running
while ``RoutineStore.stop`` dropped the instance and answered "stopped". Each
firing's task is now tracked and cancelled by both the dashboard's Stop and the
Telegram Stop; a cancellation that did not come from a Stop still propagates.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.routines as routes_routines
import handlers.routines as tg_routines
from condor.routine_store import RoutineStore
from condor.web.auth import get_current_user
from condor.web.models import WebUser

STARTER = WebUser(id=111, username="starter", first_name="S", role="user")

SCHEDULES = {
    "interval": (
        {"type": "interval", "interval_sec": 60},
        "_interval_job_callback",
    ),
    "daily": ({"type": "daily", "daily_time": "09:00"}, "_daily_job_callback"),
    "once": ({"type": "once"}, "_oneshot_job_callback"),
}


class FakeJob:
    def __init__(self, data):
        self.data = data
        self.removed = False

    def schedule_removal(self):
        self.removed = True


class FakeJobQueue:
    def __init__(self):
        self.jobs = {}

    def run_once(self, callback, **kwargs):
        self.jobs[kwargs["name"]] = FakeJob(kwargs["data"])

    run_repeating = run_daily = run_once

    def get_jobs_by_name(self, name):
        return [self.jobs[name]] if name in self.jobs else []


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(text)


class FakeApplication:
    def __init__(self):
        self.user_data = {STARTER.id: {}}
        self.job_queue = FakeJobQueue()
        self.bot = FakeBot()

    def mark_data_for_update_persistence(self, user_ids=None, chat_ids=None):
        pass


class FakeContext:
    def __init__(self, application):
        self.application = application
        self.user_data = application.user_data[STARTER.id]
        self.job_queue = application.job_queue
        self.bot = application.bot


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
    yield real
    tg_routines._scheduled_runs.clear()


@pytest.fixture
def slow_run(monkeypatch):
    """A routine run that blocks until cancelled, recording how it ended."""
    state = SimpleNamespace(started=None, cancelled=False)

    async def _execute(context, instance_id, *args, **kwargs):
        state.started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            state.cancelled = True
            raise
        return "done", 0.0, None

    monkeypatch.setattr(tg_routines, "_execute_routine", _execute)
    return state


def _start(application, schedule):
    context = FakeContext(application)
    instance_id = tg_routines._create_scheduled_instance(
        context, STARTER.id, "probe", {}, schedule, user_id=STARTER.id
    )
    job = application.job_queue.jobs[tg_routines._job_name(STARTER.id, instance_id)]
    return context, instance_id, job


def _job_context(application, job):
    return SimpleNamespace(application=application, bot=application.bot, job=job)


@pytest.mark.parametrize("kind", sorted(SCHEDULES))
def test_store_stop_cancels_a_firing_already_inside_the_routine(store, slow_run, kind):
    schedule, callback_name = SCHEDULES[kind]
    callback = getattr(tg_routines, callback_name)

    async def scenario():
        slow_run.started = asyncio.Event()
        application = FakeApplication()
        context, instance_id, job = _start(application, schedule)
        firing = asyncio.create_task(callback(_job_context(application, job)))
        await slow_run.started.wait()
        assert firing in tg_routines._scheduled_runs[instance_id]

        assert store.stop(instance_id) is True

        # The Stop's cancellation ends the firing quietly, not as an error.
        await asyncio.wait_for(firing, timeout=2)
        assert slow_run.cancelled
        assert not firing.cancelled()
        assert job.removed
        assert application.bot.sent == []
        assert instance_id not in tg_routines._scheduled_runs
        assert instance_id not in context.user_data["routine_instances"]
        assert store.get_instance(instance_id) is None

    asyncio.run(scenario())


def test_web_stop_from_another_thread_cancels_the_running_firing(store, slow_run):
    schedule, _ = SCHEDULES["interval"]

    async def scenario():
        slow_run.started = asyncio.Event()
        application = FakeApplication()
        _, instance_id, job = _start(application, schedule)
        firing = asyncio.create_task(
            tg_routines._interval_job_callback(_job_context(application, job))
        )
        await slow_run.started.wait()

        app = FastAPI()
        app.include_router(routes_routines.router)
        app.dependency_overrides[get_current_user] = lambda: STARTER
        response = await asyncio.to_thread(
            TestClient(app).post, f"/routines/instances/{instance_id}/stop"
        )

        assert response.status_code == 200
        await asyncio.wait_for(firing, timeout=2)
        assert slow_run.cancelled
        assert application.bot.sent == []

    asyncio.run(scenario())


def test_telegram_stop_cancels_the_running_firing(store, slow_run):
    schedule, _ = SCHEDULES["interval"]

    async def scenario():
        slow_run.started = asyncio.Event()
        application = FakeApplication()
        context, instance_id, job = _start(application, schedule)
        firing = asyncio.create_task(
            tg_routines._interval_job_callback(_job_context(application, job))
        )
        await slow_run.started.wait()

        assert tg_routines._stop_instance(context, STARTER.id, instance_id) is True

        await asyncio.wait_for(firing, timeout=2)
        assert slow_run.cancelled
        assert application.bot.sent == []

    asyncio.run(scenario())


def test_a_cancellation_that_is_not_a_stop_still_propagates(store, slow_run):
    schedule, _ = SCHEDULES["interval"]

    async def scenario():
        slow_run.started = asyncio.Event()
        application = FakeApplication()
        _, instance_id, job = _start(application, schedule)
        firing = asyncio.create_task(
            tg_routines._interval_job_callback(_job_context(application, job))
        )
        await slow_run.started.wait()

        firing.cancel()  # e.g. shutdown, not a Stop

        with pytest.raises(asyncio.CancelledError):
            await firing
        assert instance_id not in tg_routines._scheduled_runs
        # The instance itself was not stopped.
        assert store.get_instance(instance_id) is not None

    asyncio.run(scenario())


def test_a_finished_firing_leaves_nothing_tracked(store, monkeypatch):
    async def _execute(*args, **kwargs):
        return "ok", 0.0, None

    monkeypatch.setattr(tg_routines, "_execute_routine", _execute)
    monkeypatch.setattr(tg_routines, "_sync_instance_to_store", lambda *a: None)

    async def scenario():
        application = FakeApplication()
        _, instance_id, job = _start(application, SCHEDULES["interval"][0])
        await tg_routines._interval_job_callback(_job_context(application, job))
        assert instance_id not in tg_routines._scheduled_runs
        assert len(application.bot.sent) == 1

    asyncio.run(scenario())
