"""Stopping and queueing on the Telegram surface.

The dashboard has had a real Stop since FEAT-029 — a WS ``abort_prompt`` that
lands on the agent as ``session/cancel`` and leaves the session standing.
Telegram had none: the only kill switch was the menu button, which destroys the
subprocess and the conversation with it. ``/stop`` is the missing half.

The queue half is the same gap seen from the other side: a message sent
mid-answer was already queued (FEAT-030), but ``EventType.QUEUED`` fell through
``TelegramStreamer`` unhandled, so waiting its turn looked exactly like being
answered — and each waiting placeholder re-edited its message twice a second.
"""

import asyncio
from types import SimpleNamespace

import pytest

import utils.auth as auth_module
from condor.runtime import SessionKey
from condor.runtime import client as runtime
from condor.runtime.events import EventType, RuntimeEvent
from handlers.agents import stop_command
from handlers.agents.stream import QUEUED_LABEL, STOPPED_LABEL, TelegramStreamer

CHAT_ID = 4242


@pytest.fixture
def approved_user(monkeypatch):
    """Make @restricted see an admin so the handler body actually runs."""
    from config_manager import UserRole

    monkeypatch.setattr(
        auth_module,
        "get_config_manager",
        lambda: SimpleNamespace(get_user_role=lambda _uid: UserRole.ADMIN),
    )


def _message_update(replies: list):
    async def reply_text(text, **kwargs):
        replies.append(text)
        return SimpleNamespace(message_id=1)

    return SimpleNamespace(
        message=SimpleNamespace(text="/stop", reply_text=reply_text),
        effective_chat=SimpleNamespace(id=CHAT_ID, type="private"),
        effective_user=SimpleNamespace(id=7, username="tester"),
        callback_query=None,
    )


def _run_stop(monkeypatch, info):
    """Drive /stop against a session snapshot; report what the runtime saw."""
    calls = {"abort": [], "destroy": []}
    replies: list[str] = []

    async def fake_get_info(key):
        return info

    async def fake_abort(key):
        calls["abort"].append(str(key))
        return True

    async def fake_destroy(key):
        calls["destroy"].append(str(key))
        return True

    monkeypatch.setattr(runtime, "get_info", fake_get_info)
    monkeypatch.setattr(runtime, "abort", fake_abort)
    monkeypatch.setattr(runtime, "destroy", fake_destroy)

    asyncio.run(stop_command(_message_update(replies), SimpleNamespace(user_data={})))
    return calls, replies


def _session(*, alive=True, is_busy=True):
    return SimpleNamespace(alive=alive, is_busy=is_busy)


# --- /stop ---


def test_stop_aborts_the_turn_and_keeps_the_session(approved_user, monkeypatch):
    calls, replies = _run_stop(monkeypatch, _session())

    assert calls["abort"] == [str(SessionKey.telegram(CHAT_ID))]
    # The whole point: the conversation survives being interrupted.
    assert calls["destroy"] == []
    assert replies and "Stopped" in replies[0]


def test_stop_on_an_idle_session_aborts_nothing(approved_user, monkeypatch):
    """Nothing is generating: a stray /stop must not disturb the session."""
    calls, replies = _run_stop(monkeypatch, _session(is_busy=False))

    assert calls["abort"] == []
    assert replies and "idle" in replies[0]


def test_stop_without_a_session_says_so(approved_user, monkeypatch):
    calls, replies = _run_stop(monkeypatch, None)

    assert calls["abort"] == []
    assert replies == ["No active session."]


def test_stop_is_private_chat_only(approved_user, monkeypatch):
    replies: list[str] = []
    update = _message_update(replies)
    update.effective_chat = SimpleNamespace(id=CHAT_ID, type="supergroup")

    aborted = []
    monkeypatch.setattr(runtime, "abort", lambda key: aborted.append(key))

    asyncio.run(stop_command(update, SimpleNamespace(user_data={})))

    assert aborted == []
    assert replies and "private chats" in replies[0]


# --- Streaming a queued / stopped turn ---


class _RecordingBot:
    def __init__(self):
        self.edits: list[str] = []

    async def edit_message_text(self, **kw):
        self.edits.append(kw["text"])
        return None

    async def send_message(self, **kw):
        return SimpleNamespace(message_id=99)


def _streamer():
    bot = _RecordingBot()
    return bot, TelegramStreamer(bot=bot, chat_id=CHAT_ID, message_id=1)


def test_a_queued_turn_says_it_is_waiting(monkeypatch):
    bot, streamer = _streamer()

    async def drive():
        await streamer.process_event(RuntimeEvent.queued())
        await streamer._flush(final=False)

    asyncio.run(drive())

    # Not the Thinking pulse: nothing has reached the agent yet.
    assert bot.edits == [QUEUED_LABEL]
    assert "Thinking" not in bot.edits[-1]


def test_the_queued_notice_gives_way_to_the_answer():
    bot, streamer = _streamer()

    async def drive():
        await streamer.process_event(RuntimeEvent.queued())
        await streamer._flush(final=False)
        await streamer.process_event(
            RuntimeEvent(type=EventType.TEXT, data={"text": "hello"})
        )
        await streamer._flush(final=False)

    asyncio.run(drive())

    assert streamer._queued is False
    assert bot.edits[-1] == "hello"


def test_waiting_its_turn_costs_one_edit_not_two_per_second():
    """Several queued placeholders used to hammer Telegram while idle."""
    bot, streamer = _streamer()

    async def drive():
        await streamer.process_event(RuntimeEvent.queued())
        task = streamer.start_edit_loop()
        await asyncio.sleep(0.15)
        streamer._done = True
        await task

    asyncio.run(drive())

    assert bot.edits == [QUEUED_LABEL]


def test_a_stopped_turn_keeps_its_partial_answer_and_says_it_was_stopped():
    bot, streamer = _streamer()

    async def drive():
        await streamer.process_event(
            RuntimeEvent(type=EventType.TEXT, data={"text": "half an answ"})
        )
        await streamer.process_event(RuntimeEvent.done("cancelled"))
        await streamer.finalize()

    asyncio.run(drive())

    final = bot.edits[-1]
    # What the agent managed to say is kept — abort is not a rollback.
    assert "half an answ" in final
    assert STOPPED_LABEL in final


def test_a_turn_stopped_before_it_spoke_is_not_reported_as_no_response():
    bot, streamer = _streamer()

    async def drive():
        await streamer.process_event(RuntimeEvent.done("cancelled"))
        await streamer.finalize()

    asyncio.run(drive())

    assert "stopped" in bot.edits[-1]
    assert "no response" not in bot.edits[-1]


def test_a_normal_turn_is_unmarked():
    bot, streamer = _streamer()

    async def drive():
        await streamer.process_event(
            RuntimeEvent(type=EventType.TEXT, data={"text": "done thinking"})
        )
        await streamer.process_event(RuntimeEvent.done("end_turn"))
        await streamer.finalize()

    asyncio.run(drive())

    assert STOPPED_LABEL not in bot.edits[-1]
