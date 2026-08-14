"""A routine started from a group belongs to the person, not to the room.

Instances are written through ``_get_instances(context)`` -> ``context.user_data``,
and PTB keys ``user_data`` by ``update.effective_user.id``. Every job path used to
read it back with ``application.user_data[job.data["chat_id"]]``. In a private chat
the two ids coincide, so the bug hides; in a group the chat id is negative, the
bucket is simply absent, and the interval callback fails closed — the routine never
runs once, forever, while the repeating job stays alive.
"""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

import handlers.routines as hr

GROUP_ID = -1001234
OWNER_ID = 7


class Config(BaseModel):
    pass


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=1)

    async def edit_message_text(self, **kwargs):
        self.sent.append((kwargs.get("chat_id"), kwargs.get("text")))
        return SimpleNamespace(message_id=1)


class FakeStore:
    def __init__(self):
        self.removed = []

    def remove_instance(self, instance_id):
        self.removed.append(instance_id)


def _routine():
    return SimpleNamespace(
        name="probe",
        description="Probe routine",
        is_continuous=False,
        get_fields=lambda: {},
        get_default_config=lambda: Config(),
    )


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setattr(hr, "get_routine_store", lambda: s)
    return s


@pytest.fixture
def synced(monkeypatch):
    calls = []
    monkeypatch.setattr(
        hr, "_sync_instance_to_store", lambda iid, meta: calls.append((iid, meta))
    )
    return calls


@pytest.fixture
def executed(monkeypatch):
    """Records every ``_execute_routine`` call instead of running a routine."""
    calls = []

    async def _execute(context, instance_id, routine_name, config_dict, chat_id, **kw):
        calls.append({"chat_id": chat_id, **kw})
        return "ok", 1.5, None

    monkeypatch.setattr(hr, "_execute_routine", _execute)
    monkeypatch.setattr(hr, "get_routine", lambda name: _routine())
    return calls


def _instances():
    return {
        "i1": {
            "routine_name": "probe",
            "status": "running",
            "user_id": OWNER_ID,
            "schedule": {"type": "interval", "interval_sec": 60},
        }
    }


def _ctx(instances, *, user_id=OWNER_ID, chat_id=GROUP_ID, extra=None):
    """A job context shaped like a group run: instance in the owner's bucket."""
    data = {
        "instance_id": "i1",
        "routine_name": "probe",
        "config_dict": {},
        "chat_id": chat_id,
        "background": True,
    }
    if user_id is not None:
        data["user_id"] = user_id
    data.update(extra or {})
    return SimpleNamespace(
        bot=FakeBot(),
        application=SimpleNamespace(
            user_data={OWNER_ID: {"routine_instances": instances}}
        ),
        job=SimpleNamespace(data=data),
    )


# ---------------------------------------------------------------------------
# Interval: the load-bearing case — today it returns before executing anything.
# ---------------------------------------------------------------------------


def test_interval_job_from_a_group_actually_runs(executed, synced):
    instances = _instances()
    ctx = _ctx(instances)

    asyncio.run(hr._interval_job_callback(ctx))

    assert executed, "the routine never executed for a group-started interval job"
    assert instances["i1"]["run_count"] == 1
    assert instances["i1"]["last_run_at"] is not None
    assert instances["i1"]["last_result"] == "ok"


def test_interval_job_delivers_to_the_group_not_the_owner(executed, synced):
    ctx = _ctx(_instances())

    asyncio.run(hr._interval_job_callback(ctx))

    assert [c for c, _ in ctx.bot.sent] == [GROUP_ID]


def test_execute_receives_the_owner_id(executed, synced):
    ctx = _ctx(_instances())

    asyncio.run(hr._interval_job_callback(ctx))

    assert executed[0]["owner_id"] == OWNER_ID
    assert executed[0]["chat_id"] == GROUP_ID


# ---------------------------------------------------------------------------
# One-shot and daily: they run, but their bookkeeping used to be skipped,
# leaving a ghost stuck at status "running" in the store forever.
# ---------------------------------------------------------------------------


def test_oneshot_job_from_a_group_clears_the_instance(executed, store):
    instances = _instances()
    ctx = _ctx(instances)

    asyncio.run(hr._oneshot_job_callback(ctx))

    assert "i1" not in instances
    assert store.removed == ["i1"]


def test_daily_job_from_a_group_updates_and_syncs(executed, synced):
    instances = _instances()
    ctx = _ctx(instances)

    asyncio.run(hr._daily_job_callback(ctx))

    assert instances["i1"]["run_count"] == 1
    assert [iid for iid, _ in synced] == ["i1"]


# ---------------------------------------------------------------------------
# Private chats and jobs persisted before the owner was carried must not move.
# ---------------------------------------------------------------------------


def test_legacy_job_without_user_id_still_resolves_by_chat_id(executed, synced):
    """Pre-change job data carries no ``user_id``; in the private chats it was
    created in, ``chat_id`` is the right bucket key and must keep working."""
    instances = _instances()
    ctx = _ctx(instances, user_id=None, chat_id=OWNER_ID)

    asyncio.run(hr._interval_job_callback(ctx))

    assert instances["i1"]["run_count"] == 1
    assert executed[0]["owner_id"] in (None, OWNER_ID)


def test_a_stopped_instance_is_still_skipped(executed, synced):
    ctx = _ctx({})

    asyncio.run(hr._interval_job_callback(ctx))

    assert not executed, "a removed instance must not be executed"


# ---------------------------------------------------------------------------
# Continuous routines: no junk bucket under the negative chat id, and the
# instance is cleaned out of the starter's real bucket when the task ends.
# ---------------------------------------------------------------------------


def test_continuous_routine_uses_the_owner_bucket(monkeypatch, store):
    seen = {}

    async def _run(config, context):
        seen["user_data"] = context.user_data
        return "done"

    monkeypatch.setattr(
        hr,
        "get_routine",
        lambda name: SimpleNamespace(
            name="probe",
            is_continuous=True,
            config_class=Config,
            run_fn=_run,
        ),
    )

    instances = _instances()
    owner_bucket = {"routine_instances": instances, "preferences": {}}
    application = SimpleNamespace(user_data={OWNER_ID: owner_bucket}, bot=FakeBot())

    asyncio.run(
        hr._run_continuous_routine(
            application,
            "i1",
            "probe",
            {},
            GROUP_ID,
            owner_id=OWNER_ID,
        )
    )

    assert seen["user_data"] is owner_bucket
    assert (
        GROUP_ID not in application.user_data
    ), "a junk bucket was created for the chat"
    assert "i1" not in instances, "the finished instance was left as a zombie"
    assert store.removed == ["i1"]


def test_continuous_routine_without_owner_falls_back_to_chat(monkeypatch, store):
    """Private-chat behaviour (and any caller that hasn't been threaded) is
    unchanged: the chat id is still the bucket key."""
    seen = {}

    async def _run(config, context):
        seen["user_data"] = context.user_data
        return "done"

    monkeypatch.setattr(
        hr,
        "get_routine",
        lambda name: SimpleNamespace(
            name="probe",
            is_continuous=True,
            config_class=Config,
            run_fn=_run,
        ),
    )

    instances = _instances()
    bucket = {"routine_instances": instances}
    application = SimpleNamespace(user_data={OWNER_ID: bucket}, bot=FakeBot())

    asyncio.run(hr._run_continuous_routine(application, "i1", "probe", {}, OWNER_ID))

    assert seen["user_data"] is bucket
    assert "i1" not in instances


# ---------------------------------------------------------------------------
# The detail message must read the starter's drafts, not the room's.
# ---------------------------------------------------------------------------


def test_detail_refresh_reads_the_owner_drafts(monkeypatch):
    monkeypatch.setattr(
        hr,
        "get_routine",
        lambda name: SimpleNamespace(
            name="probe",
            description="Probe routine",
            is_continuous=False,
            get_fields=lambda: {"pair": {"default": "BTC-USDT"}},
            get_default_config=lambda: Config(),
        ),
    )

    ctx = SimpleNamespace(
        bot=FakeBot(),
        application=SimpleNamespace(
            user_data={
                OWNER_ID: {
                    "routine_drafts": {"probe": {"pair": "SOL-USDC"}},
                    "routine_instances": _instances(),
                }
            }
        ),
    )

    asyncio.run(hr._refresh_detail_msg(ctx, GROUP_ID, 99, "probe", owner_id=OWNER_ID))

    _, text = ctx.bot.sent[0]
    assert "pair=SOL-USDC" in text
    assert "1 running" in text
