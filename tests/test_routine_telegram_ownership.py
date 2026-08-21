"""Tests for CORR-165: a routine started from Telegram belongs to its starter.

SEC-150 gates the dashboard's instance endpoints on the ``user_id`` recorded in
the instance meta, but the Telegram bridge only ever threaded ``chat_id``, so
every Telegram-started routine reached the store unowned and the gate — which
fails closed — hid it from the very person who started it, leaving it
admin-only.

The fix stamps the owner at creation (the *user*, not the chat, so a group run
attributes to the member who pressed the button), never downgrades it on a
later sync, and adopts instances persisted by an older build at restore time
from the ``user_data`` bucket holding them (PTB keys that by user id). The
ownership gate itself is untouched: whatever is still ownerless stays
admin-only, as SEC-167 chose for ownerless sessions.
"""

import asyncio

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.web.routes.routines as routes_routines
import handlers.routines as tg_routines
from condor.web.auth import get_current_user
from condor.web.models import WebUser

STARTER = WebUser(id=111, username="starter", first_name="S", role="user")
STRANGER = WebUser(id=222, username="stranger", first_name="X", role="user")
ADMIN = WebUser(id=999, username="admin", first_name="A", role="admin")

GROUP_CHAT = -1002222222222


# ── Doubles ──


class FakeStore:
    """Just the slice of RoutineStore the Telegram bridge and the routes use."""

    def __init__(self):
        self._instances = {}

    def add_instance(self, instance_id, metadata):
        self._instances[instance_id] = metadata

    def get_instance(self, instance_id):
        return self._instances.get(instance_id)

    def list_instances(self):
        return [dict(meta, instance_id=iid) for iid, meta in self._instances.items()]

    def get_result(self, instance_id):
        return None

    def stop(self, instance_id):
        return self._instances.pop(instance_id, None) is not None


class FakeContext:
    def __init__(self):
        self.user_data = {}
        self.application = None


class FakeUpdate:
    """Only ``effective_chat`` / ``effective_user`` matter to the creators."""

    def __init__(self, chat_id, user_id):
        self.effective_chat = type("Chat", (), {"id": chat_id})()
        self.effective_user = type("User", (), {"id": user_id})()


class FakeConfigManager:
    def is_admin(self, user_id):
        return user_id == ADMIN.id


@pytest.fixture(autouse=True)
def scheduler(monkeypatch):
    """A scheduler per test, so jobs don't pile up on the process-wide one.

    Never started: these tests are about what gets *recorded* at creation, not
    about anything firing.
    """
    from condor.scheduler import Scheduler

    sched = Scheduler()
    monkeypatch.setattr(tg_routines, "get_scheduler", lambda: sched)
    return sched


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    monkeypatch.setattr(tg_routines, "get_routine_store", lambda: fake)
    monkeypatch.setattr(routes_routines, "get_routine_store", lambda: fake)
    monkeypatch.setattr(routes_routines, "get_config_manager", FakeConfigManager)
    return fake


@pytest.fixture
def make_client():
    def _make(user: WebUser):
        app = FastAPI()
        app.include_router(routes_routines.router)
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    return _make


def start_from_telegram(store, chat_id=STARTER.id, user_id=STARTER.id, **kwargs):
    """Start a scheduled routine the way the Telegram handlers do."""
    context = FakeContext()
    update = FakeUpdate(chat_id, user_id)
    return tg_routines._create_scheduled_instance(
        context,
        update.effective_chat.id,
        "probe",
        {"pair": "SOL-USDC"},
        {"type": "interval", "interval_sec": 60},
        user_id=tg_routines._starter_id(update),
        **kwargs,
    )


# ── Ownership is stamped at creation ──


def test_scheduled_instance_records_the_starting_user(store):
    instance_id = start_from_telegram(store)
    assert store.get_instance(instance_id)["user_id"] == STARTER.id


def test_group_run_attributes_to_the_member_not_the_group(store):
    instance_id = start_from_telegram(store, chat_id=GROUP_CHAT, user_id=STARTER.id)
    meta = store.get_instance(instance_id)
    assert meta["user_id"] == STARTER.id
    assert meta["user_id"] != GROUP_CHAT


def test_continuous_instance_records_the_starting_user(store, monkeypatch):
    async def fake_runner(*args, **kwargs):
        return None

    monkeypatch.setattr(tg_routines, "_run_continuous_routine", fake_runner)

    async def scenario():
        context = FakeContext()
        update = FakeUpdate(GROUP_CHAT, STARTER.id)
        instance_id = tg_routines._create_continuous_instance(
            context,
            update.effective_chat.id,
            "watcher",
            {},
            user_id=tg_routines._starter_id(update),
        )
        await asyncio.gather(*tg_routines._continuous_tasks.values())
        tg_routines._continuous_tasks.clear()
        return instance_id

    instance_id = asyncio.run(scenario())
    assert store.get_instance(instance_id)["user_id"] == STARTER.id


def test_starter_id_survives_an_update_with_no_user():
    update = FakeUpdate(GROUP_CHAT, STARTER.id)
    update.effective_user = None
    assert tg_routines._starter_id(update) is None


# ── The dashboard now shows it to its starter, and to nobody else ──


def test_telegram_run_is_visible_and_stoppable_by_its_starter(store, make_client):
    instance_id = start_from_telegram(store)
    client = make_client(STARTER)

    listed = client.get("/routines/instances")
    assert [i["instance_id"] for i in listed.json()] == [instance_id]

    detail = client.get(f"/routines/instances/{instance_id}")
    assert detail.status_code == 200
    assert detail.json()["routine_name"] == "probe"

    assert client.post(f"/routines/instances/{instance_id}/stop").status_code == 200
    assert store.get_instance(instance_id) is None


def test_telegram_run_stays_hidden_from_an_unrelated_user(store, make_client):
    instance_id = start_from_telegram(store)
    client = make_client(STRANGER)

    assert client.get("/routines/instances").json() == []
    assert client.get(f"/routines/instances/{instance_id}").status_code == 403
    assert client.post(f"/routines/instances/{instance_id}/stop").status_code == 403
    assert store.get_instance(instance_id) is not None


def test_admin_still_sees_every_telegram_run(store, make_client):
    instance_id = start_from_telegram(store)
    assert (
        make_client(ADMIN).get(f"/routines/instances/{instance_id}").status_code == 200
    )


# ── The owner is never downgraded by a later sync ──


def test_sync_keeps_the_owner_when_the_tick_meta_lost_it(store):
    instance_id = start_from_telegram(store)
    tg_routines._sync_instance_to_store(
        instance_id, {"run_count": 3, "last_result": "ok"}
    )
    meta = store.get_instance(instance_id)
    assert meta["user_id"] == STARTER.id
    assert meta["run_count"] == 3


def test_sync_can_still_adopt_an_instance_that_had_no_owner(store):
    store.add_instance("legacy", {"routine_name": "probe", "status": "running"})
    tg_routines._sync_instance_to_store("legacy", {"user_id": STARTER.id})
    assert store.get_instance("legacy")["user_id"] == STARTER.id


# ── Instances persisted before the fix ──


class FakeApplication:
    def __init__(self, user_data):
        self.user_data = user_data


def test_restore_adopts_ownerless_instances_from_their_user_data_bucket(
    store, monkeypatch
):
    """PTB keys ``user_data`` by user id, so the bucket names the real starter."""
    monkeypatch.setattr(tg_routines, "get_routine", lambda name: object())
    legacy = {
        "routine_name": "probe",
        "config": {},
        "schedule": {"type": "interval", "interval_sec": 60},
        "status": "running",
        "source": "telegram",
    }
    app = FakeApplication({STARTER.id: {"routine_instances": {"legacy": legacy}}})

    assert asyncio.run(tg_routines.restore_scheduled_jobs(app)) == 1

    assert legacy["user_id"] == STARTER.id
    assert store.get_instance("legacy")["user_id"] == STARTER.id


def test_an_instance_with_no_owner_at_all_stays_admin_only(store, make_client):
    """Anything still ownerless (MCP runs, older meta) keeps failing closed."""
    store.add_instance("orphan", {"routine_name": "probe", "status": "running"})
    assert make_client(STARTER).get("/routines/instances/orphan").status_code == 403
    assert make_client(STARTER).get("/routines/instances").json() == []
    assert make_client(ADMIN).get("/routines/instances/orphan").status_code == 200
