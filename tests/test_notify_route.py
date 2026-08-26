"""``send_notification`` crosses back into the main process (ARCH-088).

What is pinned here is that an announcement leaves two traces, not one: the
Telegram push it always had, plus a ``system`` turn on the conversation that
produced it — and that neither can cost the other. A session with nothing behind
it still gets its push, and a main process that cannot be reached still gets the
message out through the tool's own direct Telegram path.

Sync tests driving coroutines with ``asyncio.run``: ``pytest-asyncio`` is a dev
dependency but is not installed in this venv.
"""

import asyncio

import pytest
from fastapi import HTTPException

from condor.web.models import WebUser
from condor.web.routes import agents as agents_routes
from condor.web.routes.agents import NotifyRequest, notify_user
from mcp_servers.condor.tools import notification as notification_tool

# The pushes below target chat 1 — the caller's own private chat. A foreign
# chat id is refused since SEC-198 (see test_agents_chat_id_ownership.py).
CALLER = WebUser(id=1, role="user")


class _FakeBot:
    """Stands in for the resolved outbound bot.

    ``ok`` mirrors what ``_HttpBot`` returns; ``raises`` mirrors a live
    python-telegram-bot rejecting bad Markdown.
    """

    def __init__(self, ok=True, raises_with_parse_mode=False):
        self.ok = ok
        self.raises_with_parse_mode = raises_with_parse_mode
        self.calls: list[dict] = []

    async def send_message(self, **kw):
        self.calls.append(kw)
        if kw.get("parse_mode") and self.raises_with_parse_mode:
            raise RuntimeError("can't parse entities")
        return {"ok": self.ok}


@pytest.fixture
def bot(monkeypatch):
    """Resolve the outbound ladder to a fake, and capture transcript writes."""
    fake = _FakeBot()
    from condor.agents import delegate as delegate_module

    monkeypatch.setattr(delegate_module, "resolve_bot", lambda b=None: fake)
    return fake


@pytest.fixture
def notes(monkeypatch):
    written: list[tuple] = []
    from condor.runtime import conversations

    monkeypatch.setattr(
        conversations,
        "record_system",
        lambda user_id, conv_id, text, kind="": written.append(
            (user_id, conv_id, text, kind)
        ),
    )
    return written


def _resolves_to(monkeypatch, conversation_id: str):
    async def fake(session_key: str) -> str:
        return conversation_id if session_key else ""

    monkeypatch.setattr(agents_routes, "_conversation_for_session", fake)


# ── The route: one push, one note ──


def test_a_live_session_gets_both_a_transcript_note_and_the_telegram_push(
    monkeypatch, bot, notes
):
    _resolves_to(monkeypatch, "conv-1")

    result = asyncio.run(
        notify_user(
            NotifyRequest(
                text="LP position closed",
                chat_id=1,
                session_key="web:1:slot-1",
            ),
            user=CALLER,
        )
    )

    assert result == {"sent": True, "recorded": True}
    assert notes == [(1, "conv-1", "LP position closed", "notification")]
    assert len(bot.calls) == 1
    assert bot.calls[0]["chat_id"] == 1


def test_the_note_is_scoped_to_the_jwt_caller_not_the_posted_user_id(
    monkeypatch, bot, notes
):
    """Mirror consult/delegate: a caller cannot write into another transcript."""
    _resolves_to(monkeypatch, "conv-1")

    asyncio.run(
        notify_user(
            NotifyRequest(text="hi", chat_id=1, user_id=999, session_key="web:1:s"),
            user=CALLER,
        )
    )

    assert notes[0][0] == 1


def test_a_dead_session_key_still_pushes_and_notes_nothing(monkeypatch, bot, notes):
    _resolves_to(monkeypatch, "")

    result = asyncio.run(
        notify_user(NotifyRequest(text="ping", chat_id=1, session_key=""), user=CALLER)
    )

    # No conversation to write into, so no transcript note -- but the dashboard
    # bell is addressed to the user, not to a conversation, so it still records
    # and the call still counts as delivered (FEAT-048).
    assert result == {"sent": True, "recorded": True}
    assert notes == []
    assert len(bot.calls) == 1


def test_an_unwritable_transcript_does_not_cost_the_user_the_push(
    monkeypatch, bot, notes
):
    _resolves_to(monkeypatch, "conv-1")
    from condor.runtime import conversations

    def explode(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(conversations, "record_system", explode)

    result = asyncio.run(
        notify_user(
            NotifyRequest(text="ping", chat_id=1, session_key="web:1:s"), user=CALLER
        )
    )

    # ``recorded`` is now true on the bell alone (FEAT-048): the transcript
    # write failed, and that must not cost the user either delivery.
    assert result == {"sent": True, "recorded": True}
    assert len(bot.calls) == 1


def test_bad_markdown_is_retried_as_plain_text(monkeypatch, notes):
    _resolves_to(monkeypatch, "")
    fake = _FakeBot(raises_with_parse_mode=True)
    from condor.agents import delegate as delegate_module

    monkeypatch.setattr(delegate_module, "resolve_bot", lambda b=None: fake)

    result = asyncio.run(
        notify_user(NotifyRequest(text="a_b_c", chat_id=1), user=CALLER)
    )

    assert result["sent"] is True
    assert [c.get("parse_mode") for c in fake.calls] == ["Markdown", None]


def test_a_web_session_with_no_chat_records_without_pushing(monkeypatch, bot, notes):
    """``chat_id=0`` means there is no Telegram chat behind this session."""
    _resolves_to(monkeypatch, "conv-1")

    result = asyncio.run(
        notify_user(
            NotifyRequest(text="done", chat_id=0, session_key="web:1:s"), user=CALLER
        )
    )

    assert result == {"sent": False, "recorded": True}
    assert bot.calls == []


def test_empty_text_is_rejected(monkeypatch, bot, notes):
    _resolves_to(monkeypatch, "conv-1")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(notify_user(NotifyRequest(text="", chat_id=1), user=CALLER))

    assert exc.value.status_code == 400


# ── The tool: prefers the main process, survives without it ──


def test_the_tool_goes_through_the_main_process_and_never_touches_telegram(monkeypatch):
    calls: list[tuple] = []

    async def fake_call(method, path, body=None, timeout=None):
        calls.append((method, path, body))
        return {"sent": True, "recorded": True}

    monkeypatch.setattr(notification_tool, "call_main_api", fake_call)
    monkeypatch.setattr(
        notification_tool.settings, "session_key", "web:1:slot-1", raising=False
    )
    monkeypatch.setattr(notification_tool.httpx, "AsyncClient", _no_direct_http)

    assert asyncio.run(notification_tool.send_notification("hello")) == {"sent": True}
    method, path, body = calls[0]
    assert (method, path) == ("POST", "/agents/notify")
    assert body["session_key"] == "web:1:slot-1"
    assert body["text"] == "hello"


def test_a_recorded_only_notification_is_reported_as_sent(monkeypatch):
    """A dashboard session has no chat to push to; the note is the delivery."""

    async def fake_call(*a, **kw):
        return {"sent": False, "recorded": True}

    monkeypatch.setattr(notification_tool, "call_main_api", fake_call)
    monkeypatch.setattr(notification_tool.httpx, "AsyncClient", _no_direct_http)

    assert asyncio.run(notification_tool.send_notification("hello")) == {"sent": True}


def test_the_tool_falls_back_to_direct_telegram_when_the_main_api_is_down(monkeypatch):
    async def fake_call(*a, **kw):
        raise RuntimeError("connection refused")

    posted: list[dict] = []
    monkeypatch.setattr(notification_tool, "call_main_api", fake_call)
    monkeypatch.setattr(notification_tool.settings, "bot_token", "T", raising=False)
    monkeypatch.setattr(notification_tool.settings, "chat_id", 42, raising=False)
    monkeypatch.setattr(
        notification_tool.httpx, "AsyncClient", _direct_http(posted, {"ok": True})
    )

    assert asyncio.run(notification_tool.send_notification("hello")) == {"sent": True}
    assert posted[0]["chat_id"] == 42


def test_a_route_that_could_not_deliver_falls_back_too(monkeypatch):
    async def fake_call(*a, **kw):
        return {"sent": False, "recorded": False}

    posted: list[dict] = []
    monkeypatch.setattr(notification_tool, "call_main_api", fake_call)
    monkeypatch.setattr(notification_tool.settings, "bot_token", "T", raising=False)
    monkeypatch.setattr(notification_tool.settings, "chat_id", 42, raising=False)
    monkeypatch.setattr(
        notification_tool.httpx, "AsyncClient", _direct_http(posted, {"ok": True})
    )

    assert asyncio.run(notification_tool.send_notification("hello")) == {"sent": True}
    assert len(posted) == 1


# ── httpx stand-ins for the direct path ──


class _no_direct_http:
    def __init__(self, *a, **kw):
        raise AssertionError("the tool must not talk to Telegram directly here")


def _direct_http(posted: list, reply: dict):
    class _Resp:
        def json(self):
            return reply

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            posted.append(json)
            return _Resp()

    return _Client


# ── The rung every other test here stubs away ──


@pytest.fixture
def store(monkeypatch):
    """A private bell store and an empty sink registry (see test_notifications).

    conftest's ``$CONDOR_DATA_DIR`` already isolates the store's file.
    """
    from condor import notifications

    monkeypatch.setattr(notifications, "_push_sinks", [])


@pytest.fixture
def no_telegram(monkeypatch):
    """The real ladder with nothing above its bottom rung: no bot, no token."""

    class _NoBotStore:
        def get_bot(self):
            return None

    class _CM:
        def get_user(self, user_id):
            return {"id": user_id} if user_id == CALLER.id else None

    monkeypatch.setattr("condor.routine_store.get_routine_store", lambda: _NoBotStore())
    monkeypatch.setattr("config_manager.get_config_manager", lambda: _CM())
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)


def test_a_telegram_less_install_files_the_notification_once(
    monkeypatch, notes, store, no_telegram
):
    """The bell rung records the push; recording again filed it twice (ARCH-212).

    Deliberately does *not* stub ``resolve_bot``, unlike every other test in
    this file — the duplicate only existed below the two Telegram rungs, so a
    fake bot could never see it.
    """
    _resolves_to(monkeypatch, "")
    from condor.agents.delegate import resolve_bot
    from condor.notifications import NotifyBot, list_for

    assert isinstance(resolve_bot(), NotifyBot)

    result = asyncio.run(
        notify_user(
            NotifyRequest(text="the thing happened", chat_id=CALLER.id), user=CALLER
        )
    )

    # Nothing reached Telegram, and the caller is told so honestly; the tool
    # counts ``recorded`` as delivery, so it still succeeds.
    assert result == {"sent": False, "recorded": True}
    items = list_for(CALLER.id)
    assert [(n.text, n.kind) for n in items] == [("the thing happened", "agent")]
