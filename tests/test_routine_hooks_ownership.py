"""Tests for SEC-152: routine hooks are owned, and only their owner's runs fire them.

Hooks used to be one global config per routine name: any approved user could
PUT one, read anyone else's, and — because dispatch keyed only on the routine
name — receive the full interactive report (portfolio value, PnL, positions)
of every run of that routine by anyone, forever. These tests pin the three
halves of the fix: the REST endpoints are per-owner, dispatch fires only the
run owner's hooks, and no hook can deliver to a chat its owner does not
control (checked again at dispatch, so a file written by an older build is
defused too).
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

import condor.routine_hooks as hooks
import condor.web.routes.routines as routines_module
from condor import paths
from condor.web.auth import get_current_user
from condor.web.models import WebUser

OWNER = WebUser(id=111, username="owner", first_name="O", role="user")
STRANGER = WebUser(id=222, username="stranger", first_name="S", role="user")
ADMIN = WebUser(id=999, username="admin", first_name="A", role="admin")

ROUTINE = "brigado/bot_report"
GROUP_CHAT = "-1003602724903"


class FakeConfigManager:
    def is_admin(self, user_id):
        return user_id == ADMIN.id


@pytest.fixture(autouse=True)
def hooks_file(monkeypatch):
    """Point the hook store at a throwaway file and stub the role lookup."""
    import config_manager

    # No monkeypatch of the store's path: conftest's $CONDOR_DATA_DIR already
    # points the whole operational store at this test's tmp_path, so what runs
    # here is the resolver production uses.
    path = paths.routine_hooks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_manager, "get_config_manager", FakeConfigManager)
    # The "dropped a dead field" log fires once per routine per process.
    monkeypatch.setattr(hooks, "_dead_field_warned", set())
    return path


def write_raw(path, data):
    path.write_text(json.dumps(data))


def read_raw(path):
    return json.loads(path.read_text())


@pytest.fixture
def make_client():
    def _make(user: WebUser):
        app = FastAPI()
        app.include_router(routines_module.router)
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    return _make


def hook_body(chat_ids, trigger="success"):
    return {"telegram": {"enabled": True, "chat_ids": chat_ids}, "trigger": trigger}


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_document(self, chat_id, document):
        self.sent.append(chat_id)


class FakeResult:
    text = "portfolio: $1,234,567"


def fire(routine_name, owner_id, bot, failed=False):
    """Run one routine's post-execution hooks as ``owner_id``."""
    asyncio.run(
        hooks.dispatch(
            routine_name, FakeResult(), None, failed=failed, bot=bot, owner_id=owner_id
        )
    )


# ── REST: reading someone else's hooks ──


def test_stranger_cannot_read_another_users_hooks(make_client, hooks_file):
    hooks.save_hooks(ROUTINE, hook_body([str(OWNER.id)]), OWNER.id)

    resp = make_client(STRANGER).get(f"/routines/{ROUTINE}/hooks")

    assert resp.status_code == 200
    # The stranger sees their own (empty) config, never the owner's chat ids.
    assert resp.json() == hooks._default_config()
    assert str(OWNER.id) not in json.dumps(resp.json())


def test_owner_reads_back_their_own_hooks(make_client):
    hooks.save_hooks(ROUTINE, hook_body([str(OWNER.id)]), OWNER.id)

    resp = make_client(OWNER).get(f"/routines/{ROUTINE}/hooks")

    assert resp.json()["telegram"]["chat_ids"] == [str(OWNER.id)]


# ── REST: writing over someone else's hooks ──


def test_stranger_put_does_not_overwrite_the_owners_hooks(make_client, hooks_file):
    hooks.save_hooks(ROUTINE, hook_body([str(OWNER.id)]), OWNER.id)

    resp = make_client(STRANGER).put(
        f"/routines/{ROUTINE}/hooks", json=hook_body([str(STRANGER.id)])
    )

    assert resp.status_code == 200
    assert hooks.load_hooks(ROUTINE, OWNER.id)["telegram"]["chat_ids"] == [
        str(OWNER.id)
    ]
    assert hooks.load_hooks(ROUTINE, STRANGER.id)["telegram"]["chat_ids"] == [
        str(STRANGER.id)
    ]


def test_put_rejects_a_chat_the_caller_does_not_control(make_client, hooks_file):
    resp = make_client(STRANGER).put(
        f"/routines/{ROUTINE}/hooks", json=hook_body([str(OWNER.id), GROUP_CHAT])
    )

    assert resp.status_code == 403
    assert not hooks_file.exists() or read_raw(hooks_file) == {}


def test_admin_may_target_a_group(make_client, hooks_file):
    resp = make_client(ADMIN).put(
        f"/routines/{ROUTINE}/hooks", json=hook_body([GROUP_CHAT])
    )

    assert resp.status_code == 200
    assert hooks.load_hooks(ROUTINE, ADMIN.id)["telegram"]["chat_ids"] == [GROUP_CHAT]


# ── Dispatch: only the run owner's hooks fire ──


def test_dispatch_fires_for_the_owners_own_run():
    hooks.save_hooks(ROUTINE, hook_body([str(OWNER.id)]), OWNER.id)
    bot = FakeBot()

    fire(ROUTINE, OWNER.id, bot)

    assert bot.sent == [OWNER.id]


def test_a_strangers_hook_never_sees_another_users_run():
    """The exfiltration channel itself: hook the routine, wait for someone else."""
    hooks.save_hooks(ROUTINE, hook_body([str(STRANGER.id)]), STRANGER.id)
    bot = FakeBot()

    fire(ROUTINE, OWNER.id, bot)
    fire(ROUTINE, ADMIN.id, bot)

    assert bot.sent == []


def test_dispatch_sends_nothing_for_an_unowned_run():
    hooks.save_hooks(ROUTINE, hook_body([str(OWNER.id)]), OWNER.id)
    bot = FakeBot()

    fire(ROUTINE, None, bot)
    fire(ROUTINE, 0, bot)

    assert bot.sent == []


def test_dispatch_drops_a_chat_the_owner_does_not_control(hooks_file):
    """A hooks file from a pre-SEC-152 build must not deliver anywhere new."""
    write_raw(
        hooks_file,
        {ROUTINE: {str(OWNER.id): hook_body([str(OWNER.id), GROUP_CHAT])}},
    )
    bot = FakeBot()

    fire(ROUTINE, OWNER.id, bot)

    assert bot.sent == [OWNER.id]


# ── Migration: hooks written before ownership existed ──


def test_legacy_flat_entry_is_preserved_on_disk(hooks_file):
    write_raw(hooks_file, {ROUTINE: hook_body([GROUP_CHAT])})

    hooks.save_hooks("other_routine", hook_body([str(OWNER.id)]), OWNER.id)

    stored = read_raw(hooks_file)
    assert stored[ROUTINE][""]["telegram"]["chat_ids"] == [GROUP_CHAT]


def test_legacy_entry_no_longer_fires_for_everyone(hooks_file):
    write_raw(hooks_file, {ROUTINE: hook_body([GROUP_CHAT])})
    bot = FakeBot()

    fire(ROUTINE, STRANGER.id, bot)
    assert bot.sent == []

    # The admin's runs keep delivering exactly as before the change.
    fire(ROUTINE, ADMIN.id, bot)
    assert bot.sent == [int(GROUP_CHAT)]


def test_legacy_entry_is_hidden_from_non_admins(hooks_file, make_client):
    write_raw(hooks_file, {ROUTINE: hook_body([GROUP_CHAT])})

    assert make_client(STRANGER).get(f"/routines/{ROUTINE}/hooks").json() == (
        hooks._default_config()
    )
    assert make_client(ADMIN).get(f"/routines/{ROUTINE}/hooks").json()["telegram"][
        "chat_ids"
    ] == [GROUP_CHAT]


def test_admin_save_adopts_the_legacy_entry(hooks_file):
    write_raw(hooks_file, {ROUTINE: hook_body([GROUP_CHAT])})

    hooks.save_hooks(ROUTINE, hook_body([GROUP_CHAT, str(ADMIN.id)]), ADMIN.id)

    stored = read_raw(hooks_file)
    assert "" not in stored[ROUTINE]
    assert stored[ROUTINE][str(ADMIN.id)]["telegram"]["chat_ids"] == [
        GROUP_CHAT,
        str(ADMIN.id),
    ]


# ── The store hands dispatch the run's owner ──


def test_fire_hooks_passes_the_instance_owner(monkeypatch):
    from condor.routine_store import RoutineStore

    store = RoutineStore()
    store._instances["i1"] = {"routine_name": ROUTINE, "user_id": OWNER.id}

    seen = {}

    async def fake_dispatch(name, result, report_id, *, failed, bot, owner_id):
        seen["name"] = name
        seen["owner_id"] = owner_id

    monkeypatch.setattr(hooks, "dispatch", fake_dispatch)
    asyncio.run(store._fire_hooks("i1", FakeResult(), None, False))

    assert seen == {"name": ROUTINE, "owner_id": OWNER.id}


# ── READ-172: the dead `email` block on old on-disk configs ──


def legacy_email_body(chat_ids, recipients=("someone@example.com",)):
    """A hook config as written before the email destination was deleted."""
    body = hook_body(chat_ids)
    body["email"] = {"enabled": True, "recipients": list(recipients)}
    return body


def test_owned_config_with_a_legacy_email_block_still_loads(hooks_file):
    """A config a user already has on disk must survive the field's removal."""
    write_raw(
        hooks_file, {ROUTINE: {str(OWNER.id): legacy_email_body([str(OWNER.id)])}}
    )

    cfg = hooks.load_hooks(ROUTINE, OWNER.id)

    assert cfg is not None, "an old config must not fail to load"
    assert "email" not in cfg
    assert cfg["telegram"]["chat_ids"] == [str(OWNER.id)]
    assert cfg["trigger"] == "success"


def test_unowned_legacy_entry_with_an_email_block_still_loads(hooks_file):
    write_raw(hooks_file, {ROUTINE: legacy_email_body([GROUP_CHAT])})

    cfg = hooks.load_hooks(ROUTINE, ADMIN.id)

    assert cfg is not None
    assert "email" not in cfg
    assert cfg["telegram"]["chat_ids"] == [GROUP_CHAT]


def test_a_legacy_email_block_does_not_stop_telegram_delivery(hooks_file):
    write_raw(
        hooks_file, {ROUTINE: {str(OWNER.id): legacy_email_body([str(OWNER.id)])}}
    )
    bot = FakeBot()

    fire(ROUTINE, OWNER.id, bot)

    assert bot.sent == [OWNER.id]


def test_the_dashboard_is_never_shown_the_dead_email_block(make_client, hooks_file):
    write_raw(
        hooks_file, {ROUTINE: {str(OWNER.id): legacy_email_body([str(OWNER.id)])}}
    )

    assert "email" not in make_client(OWNER).get(f"/routines/{ROUTINE}/hooks").json()


def test_the_email_block_is_dropped_on_load_not_silently_on_save(hooks_file):
    """The next write cleans the file because the *loader* already dropped it."""
    write_raw(hooks_file, {ROUTINE: legacy_email_body([GROUP_CHAT])})

    # Saving an unrelated routine rewrites the whole file.
    hooks.save_hooks("other_routine", hook_body([str(OWNER.id)]), OWNER.id)

    stored = read_raw(hooks_file)
    assert "email" not in stored[ROUTINE][""]
    assert stored[ROUTINE][""]["telegram"]["chat_ids"] == [GROUP_CHAT]
