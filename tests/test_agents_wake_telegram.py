"""A woken turn renders in Telegram like any other answer (FEAT-034, slice 5).

Telegram has no socket to push into: an answer is a placeholder message that
gets edited as the turn streams. A turn nobody typed has no message to reply to
either, so the sink sends its own placeholder through the same bot ladder the
completion notice uses and hands it to the same streamer the message handler
uses.
"""

import asyncio

import pytest

from condor.agents import delegate as delegate_module
from condor.runtime import SessionKey
from condor.runtime.events import EventType, RuntimeEvent
from handlers.agents import wake as tg_wake


class _Message:
    def __init__(self, message_id: int):
        self.message_id = message_id


class _FakeBot:
    """A live ``Bot``: returns Message objects."""

    def __init__(self):
        self.sent: list[dict] = []
        self.edits: list[dict] = []

    async def send_message(self, **kw):
        self.sent.append(kw)
        return _Message(100 + len(self.sent))

    async def edit_message_text(self, **kw):
        self.edits.append(kw)
        return None


class _HttpishBot(_FakeBot):
    """The ``_HttpBot`` fallback: returns the raw Telegram API envelope."""

    async def send_message(self, **kw):
        self.sent.append(kw)
        return {"ok": True, "result": {"message_id": 777}}


class _MuteBot(_FakeBot):
    """No token: the HTTP fallback returns None rather than a message."""

    async def send_message(self, **kw):
        self.sent.append(kw)
        return None


@pytest.fixture
def bot(monkeypatch):
    fake = _FakeBot()
    monkeypatch.setattr(delegate_module, "resolve_bot", lambda b=None: fake)
    return fake


def _events():
    return [
        RuntimeEvent(
            type=EventType.TEXT,
            data={"text": "the pools I found are thin; not worth it"},
        ),
        RuntimeEvent.done("end_turn"),
    ]


async def _drive(sink):
    await sink.open()
    for event in _events():
        await sink.on_event(event)
    await sink.close()


def test_the_turn_is_streamed_into_a_placeholder_message(bot):
    asyncio.run(_drive(tg_wake.TelegramWakeSink(42)))

    assert bot.sent == [{"chat_id": 42, "text": tg_wake.PLACEHOLDER}]
    # The answer replaced the placeholder rather than arriving as a new message.
    assert bot.edits
    final = bot.edits[-1]
    assert final["chat_id"] == 42
    assert final["message_id"] == 101
    assert "not worth it" in final["text"]


def test_the_http_fallback_envelope_is_understood(monkeypatch):
    """A process with no live bot still delivers -- same ladder as _notify_done."""
    fake = _HttpishBot()
    monkeypatch.setattr(delegate_module, "resolve_bot", lambda b=None: fake)

    asyncio.run(_drive(tg_wake.TelegramWakeSink(42)))

    assert fake.edits[-1]["message_id"] == 777


def test_a_chat_this_process_cannot_reach_leaves_the_sink_inert(monkeypatch):
    """No placeholder is not a failed turn: the answer is still recorded."""
    fake = _MuteBot()
    monkeypatch.setattr(delegate_module, "resolve_bot", lambda b=None: fake)

    asyncio.run(_drive(tg_wake.TelegramWakeSink(42)))

    assert fake.edits == []


def test_only_a_telegram_key_resolves_to_a_chat_sink():
    assert tg_wake._telegram_wake_sink(SessionKey.telegram(42), None) is not None
    # A web key has no chat behind it; the web sink owns that surface.
    assert tg_wake._telegram_wake_sink(SessionKey.web(1, "conv-1"), 1) is None
