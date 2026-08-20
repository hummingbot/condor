"""Task notifications reach the dashboard (FEAT-048).

What is pinned here is the promise the feature makes: a finished background task
is *addressed to a user*, survives nobody watching, never crosses to another
user, and never costs its producer anything when the surface is down.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv (same convention as
``test_notify_route.py``).
"""

import asyncio
import json

import pytest

from condor import notifications
from condor.notifications import (
    NotifyBot,
    list_for,
    mark_read,
    record,
    unread_count,
    user_for_chat,
)
from condor.web.models import WebUser

USER_A = 111
USER_B = 222


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A private store file and an empty sink registry for each test."""
    path = tmp_path / "notifications.json"
    monkeypatch.setattr(notifications, "_FILE", path)
    monkeypatch.setattr(notifications, "_push_sinks", [])
    return path


@pytest.fixture
def known_users(monkeypatch):
    """Both test users exist as far as ``config_manager`` is concerned."""

    class _CM:
        def get_user(self, user_id):
            return {"id": user_id} if user_id in (USER_A, USER_B) else None

    monkeypatch.setattr("config_manager.get_config_manager", lambda: _CM())


# ── The store ──


def test_record_persists_and_reloads(store):
    """A notification outlives the process that produced it."""
    n = asyncio.run(record(USER_A, "task done", kind="delegation"))

    assert n is not None
    assert store.exists()
    on_disk = json.loads(store.read_text())
    assert list(on_disk) == [str(USER_A)]

    items = list_for(USER_A)
    assert [i.text for i in items] == ["task done"]
    assert items[0].kind == "delegation"
    assert items[0].read is False
    assert unread_count(USER_A) == 1


def test_newest_first_and_capped(store, monkeypatch):
    """The bell keeps the recent past, not all of it."""
    monkeypatch.setattr(notifications, "_MAX_PER_USER", 5)

    async def fill():
        for i in range(8):
            await record(USER_A, f"note {i}")

    asyncio.run(fill())

    texts = [n.text for n in list_for(USER_A, limit=100)]
    assert texts == ["note 7", "note 6", "note 5", "note 4", "note 3"]


def test_users_never_see_each_other(store):
    """The store is per user, and reading is scoped to the reader."""

    async def both():
        await record(USER_A, "A's task")
        await record(USER_B, "B's task")

    asyncio.run(both())

    assert [n.text for n in list_for(USER_A)] == ["A's task"]
    assert [n.text for n in list_for(USER_B)] == ["B's task"]


def test_mark_read_cannot_reach_across_users(store):
    """An id from someone else's bucket is simply not found."""
    b = asyncio.run(record(USER_B, "B's task"))
    assert b is not None

    left = mark_read(USER_A, [b.id])

    assert left == 0  # A has nothing of their own
    assert unread_count(USER_B) == 1
    assert list_for(USER_B)[0].read is False


def test_mark_read_all_and_by_id(store):
    async def three():
        return [await record(USER_A, f"note {i}") for i in range(3)]

    made = asyncio.run(three())

    assert mark_read(USER_A, [made[0].id]) == 2
    assert mark_read(USER_A, None) == 0
    assert unread_count(USER_A) == 0


def test_no_user_records_nothing(store):
    """A producer with nobody behind it is not an error."""
    assert asyncio.run(record(0, "orphan")) is None
    assert asyncio.run(record(None, "orphan")) is None
    assert asyncio.run(record(USER_A, "")) is None
    assert not store.exists()


def test_concurrent_records_do_not_lose_each_other(store):
    """The write lock is what keeps a simultaneous finish from vanishing."""

    async def both():
        await asyncio.gather(
            *[record(USER_A, f"note {i}") for i in range(10)],
        )

    asyncio.run(both())
    assert len(list_for(USER_A, limit=100)) == 10


# ── The push bus ──


def test_push_reaches_registered_sink(store):
    seen: list = []

    async def sink(n):
        seen.append(n)

    notifications.register_push_sink(sink)
    asyncio.run(record(USER_A, "live"))

    assert [n.text for n in seen] == ["live"]


def test_dead_sink_never_reaches_the_producer(store):
    """A socket that blew up must not fail the task that was announcing itself."""

    async def broken(n):
        raise RuntimeError("socket is gone")

    notifications.register_push_sink(broken)

    n = asyncio.run(record(USER_A, "still recorded"))

    assert n is not None
    assert [i.text for i in list_for(USER_A)] == ["still recorded"]


# ── The WS surface ──


class _FakeWS:
    def __init__(self, explode=False):
        self.sent: list[dict] = []
        self.explode = explode

    async def send_text(self, raw: str) -> None:
        if self.explode:
            raise RuntimeError("closed")
        self.sent.append(json.loads(raw))


def test_ws_push_reaches_only_the_addressed_user(store, monkeypatch):
    from condor.web.routes import chat_ws

    monkeypatch.setattr(notifications, "_push_sinks", [chat_ws._push_notification])
    a_ws, b_ws = _FakeWS(), _FakeWS()
    monkeypatch.setattr(chat_ws, "_attached_sockets", {USER_A: {a_ws}, USER_B: {b_ws}})

    asyncio.run(record(USER_A, "A's task", kind="delegation", link="/routines"))

    assert b_ws.sent == []
    assert len(a_ws.sent) == 1
    frame = a_ws.sent[0]
    assert frame["event"] == "notification"
    assert frame["kind"] == "delegation"
    assert frame["text"] == "A's task"
    assert frame["link"] == "/routines"
    assert frame["id"] and frame["ts"]


def test_ws_push_on_a_dead_socket_still_stores(store, monkeypatch):
    from condor.web.routes import chat_ws

    monkeypatch.setattr(notifications, "_push_sinks", [chat_ws._push_notification])
    monkeypatch.setattr(chat_ws, "_attached_sockets", {USER_A: {_FakeWS(explode=True)}})

    asyncio.run(record(USER_A, "unwatched"))

    assert [n.text for n in list_for(USER_A)] == ["unwatched"]


# ── The REST surface ──


def test_rest_is_scoped_to_the_jwt(store):
    from condor.web.routes.notifications import (
        MarkReadRequest,
        list_notifications,
        mark_notifications_read,
    )

    async def both():
        await record(USER_A, "A's task")
        b = await record(USER_B, "B's task")
        return b

    b = asyncio.run(both())

    listing = asyncio.run(
        list_notifications(limit=50, user=WebUser(id=USER_A, role="user"))
    )
    assert [i["text"] for i in listing["items"]] == ["A's task"]
    assert listing["unread"] == 1

    # A's session asking to read B's id changes nothing about B.
    asyncio.run(
        mark_notifications_read(
            MarkReadRequest(ids=[b.id]), user=WebUser(id=USER_A, role="user")
        )
    )
    assert unread_count(USER_B) == 1


# ── The adapter and the outbound ladder ──


def test_notify_bot_records_a_send(store, known_users):
    bot = NotifyBot()

    asyncio.run(bot.send_message(chat_id=USER_A, text="hello"))

    assert [n.text for n in list_for(USER_A)] == ["hello"]


def test_notify_bot_ignores_a_chat_with_no_owner(store, known_users):
    bot = NotifyBot()

    asyncio.run(bot.send_message(chat_id=-100123, text="group chatter"))
    asyncio.run(bot.send_message(chat_id=999999, text="stranger"))

    assert user_for_chat(-100123) is None
    assert list_for(999999) == []
    assert not store.exists()


def test_notify_bot_documents_are_linked_not_rehosted(store, known_users):
    bot = NotifyBot()

    asyncio.run(bot.send_document(chat_id=USER_A, document=b"x", filename="Daily.html"))

    item = list_for(USER_A)[0]
    assert item.text == "Daily.html"
    assert item.link == "/routines?tab=reports"


def test_resolve_bot_falls_through_to_the_bell_without_a_token(
    store, known_users, monkeypatch
):
    """With no Telegram at all, an outbound message becomes a bell item."""
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    class _NoBotStore:
        def get_bot(self):
            return None

    monkeypatch.setattr("condor.routine_store.get_routine_store", lambda: _NoBotStore())

    from condor.agents.delegate import resolve_bot

    bot = resolve_bot()
    assert isinstance(bot, NotifyBot)

    asyncio.run(bot.send_message(chat_id=USER_A, text="dropped no more"))
    assert [n.text for n in list_for(USER_A)] == ["dropped no more"]


def test_resolve_bot_prefers_telegram_when_there_is_a_token(monkeypatch):
    """A configured install is byte-for-byte unchanged."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:abc")

    class _NoBotStore:
        def get_bot(self):
            return None

    monkeypatch.setattr("condor.routine_store.get_routine_store", lambda: _NoBotStore())

    from condor.agents.delegate import resolve_bot
    from condor.routine_store import _HttpBot

    assert isinstance(resolve_bot(), _HttpBot)


# ── Producers ──


def test_delegation_completion_lights_the_bell_once(store, known_users):
    """One item, with exactly the text Telegram received."""
    from condor.agents.delegate import DelegateTask, _completion_text, _notify_done

    dt = DelegateTask(
        task_id="t1",
        agent_slug="condor",
        user_id=USER_A,
        chat_id=USER_A,
        server_name=None,
        task="do the thing",
        status="done",
        result="all done",
    )

    sent: list[dict] = []

    class _Bot:
        async def send_message(self, **kw):
            sent.append(kw)
            return {"ok": True}

    asyncio.run(_notify_done(dt, _Bot()))

    assert len(sent) == 1
    items = list_for(USER_A)
    assert len(items) == 1
    assert items[0].text == _completion_text(dt) == sent[0]["text"]
    assert items[0].kind == "delegation"


def test_delegation_over_the_bell_ladder_is_not_recorded_twice(
    store, known_users, monkeypatch
):
    """When the bell *is* the sender, ``_notify_done`` must not file it again."""
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    class _NoBotStore:
        def get_bot(self):
            return None

    monkeypatch.setattr("condor.routine_store.get_routine_store", lambda: _NoBotStore())

    from condor.agents.delegate import DelegateTask, _notify_done

    dt = DelegateTask(
        task_id="t2",
        agent_slug="condor",
        user_id=USER_A,
        chat_id=USER_A,
        server_name=None,
        task="do the thing",
        status="done",
        result="all done",
    )

    asyncio.run(_notify_done(dt, None))

    assert len(list_for(USER_A)) == 1


def test_finished_routine_links_to_the_reports_tab(store):
    """Even with no conversation behind the run, the owner is told."""
    from condor.routine_store import RoutineStore

    rs = RoutineStore()
    rs._instances["i1"] = {"routine_name": "daily_pnl", "user_id": USER_A}

    asyncio.run(rs._report_run("i1", "12 pools checked", None))

    item = list_for(USER_A)[0]
    assert item.kind == "routine"
    assert item.link == "/routines?tab=reports"
    assert "daily_pnl" in item.text


def test_notify_route_counts_a_bell_only_delivery_as_recorded(store, monkeypatch):
    """MCP ``send_notification`` succeeds on an install with no Telegram."""
    from condor.web.routes import agents as agents_routes
    from condor.web.routes.agents import NotifyRequest, notify_user

    async def _no_conversation(session_key: str) -> str:
        return ""

    monkeypatch.setattr(agents_routes, "_conversation_for_session", _no_conversation)

    class _DeadBot:
        async def send_message(self, **kw):
            return None

    monkeypatch.setattr("condor.agents.delegate.resolve_bot", lambda b=None: _DeadBot())

    result = asyncio.run(
        notify_user(
            NotifyRequest(text="the thing happened", chat_id=USER_A),
            user=WebUser(id=USER_A, role="user"),
        )
    )

    assert result["sent"] is False
    assert result["recorded"] is True
    assert [n.text for n in list_for(USER_A)] == ["the thing happened"]
