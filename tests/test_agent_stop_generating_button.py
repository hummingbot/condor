"""The Stop button that rides on a streamed answer (ARCH-096).

Telegram's only stop affordance used to be the menu button, which destroys the
subprocess and the conversation with it — "interrupt this answer" and "throw
away this chat" were the same tap. `/stop` gave the runtime's `abort()` a
Telegram caller; this covers the other half: the same abort reachable *from the
answer being written*, and gone the moment that answer finalizes.
"""

import asyncio
from types import SimpleNamespace

import pytest

import utils.auth as auth_module
from condor.runtime import SessionKey
from condor.runtime import client as runtime
from condor.runtime.events import EventType, RuntimeEvent
from handlers.agents import agent_callback_handler
from handlers.agents.stream import MAX_MESSAGE_LEN, TelegramStreamer

CHAT_ID = 4243
PLACEHOLDER_ID = 11


class _RecordingBot:
    """Captures what each edit put on screen, markup included."""

    def __init__(self):
        self.edits: list[tuple[int, str, object]] = []
        self._next_id = 100

    async def edit_message_text(self, **kw):
        self.edits.append((kw["message_id"], kw["text"], kw.get("reply_markup")))
        return None

    async def send_message(self, **kw):
        self._next_id += 1
        return SimpleNamespace(message_id=self._next_id)

    def markup_on(self, message_id: int):
        """The markup of the last edit to `message_id` (None if never edited)."""
        for msg_id, _text, markup in reversed(self.edits):
            if msg_id == message_id:
                return markup
        return None


def _streamer():
    bot = _RecordingBot()
    return bot, TelegramStreamer(bot=bot, chat_id=CHAT_ID, message_id=PLACEHOLDER_ID)


def _callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


# --- The button on the placeholder ---


def test_a_streaming_answer_carries_a_stop_button():
    bot, streamer = _streamer()

    async def drive():
        await streamer.process_event(
            RuntimeEvent(type=EventType.TEXT, data={"text": "half an answ"})
        )
        await streamer._flush(final=False)

    asyncio.run(drive())

    markup = bot.markup_on(PLACEHOLDER_ID)
    assert markup is not None, "nothing to tap while the answer is being written"
    # The whole point: it aborts the turn, it does not end the session.
    assert _callbacks(markup) == ["agent:cancel"]


def test_the_button_is_gone_once_the_answer_finalizes():
    bot, streamer = _streamer()

    async def drive():
        await streamer.process_event(
            RuntimeEvent(type=EventType.TEXT, data={"text": "the whole answer"})
        )
        await streamer._flush(final=False)
        await streamer.process_event(RuntimeEvent.done("end_turn"))
        await streamer.finalize()

    asyncio.run(drive())

    # None is how editMessageText drops an inline keyboard.
    assert bot.markup_on(PLACEHOLDER_ID) is None


def test_a_stopped_answer_also_loses_its_button():
    """The stopped turn is the one that was most likely to keep a live button."""
    bot, streamer = _streamer()

    async def drive():
        await streamer.process_event(
            RuntimeEvent(type=EventType.TEXT, data={"text": "half an answ"})
        )
        await streamer._flush(final=False)
        await streamer.process_event(RuntimeEvent.done("cancelled"))
        await streamer.finalize()

    asyncio.run(drive())

    assert bot.markup_on(PLACEHOLDER_ID) is None
    assert "half an answ" in bot.edits[-1][1]


def test_dropping_the_button_survives_the_not_modified_dedupe():
    """A long answer's first chunk is byte-identical on the final flush.

    The dedupe cache added by PERF-092 keys on what is already on screen; if it
    ignored the markup, the final flush would be skipped and the placeholder
    would keep a Stop button wired to a turn that already ended.
    """
    bot, streamer = _streamer()
    # Two chunks, so chunk 0 stops growing and stays the same text throughout.
    long_answer = ("paragraph\n\n" * 40) + "x" * MAX_MESSAGE_LEN

    async def drive():
        await streamer.process_event(
            RuntimeEvent(type=EventType.TEXT, data={"text": long_answer})
        )
        await streamer._flush(final=False)
        first_chunk = bot.edits[0][1]
        await streamer.process_event(RuntimeEvent.done("end_turn"))
        await streamer.finalize()
        return first_chunk

    first_chunk = asyncio.run(drive())

    assert len(bot.edits) > 2, "the answer did not actually split into chunks"
    assert bot.markup_on(PLACEHOLDER_ID) is None
    # It really was the unchanged-text case that had to be re-edited.
    placeholder_texts = [t for mid, t, _ in bot.edits if mid == PLACEHOLDER_ID]
    assert placeholder_texts[-1] == first_chunk


def test_continuation_messages_never_carry_the_button():
    """One Stop per turn: overflow chunks are the same turn, not new ones."""
    bot, streamer = _streamer()

    async def drive():
        await streamer.process_event(
            RuntimeEvent(
                type=EventType.TEXT,
                data={"text": ("paragraph\n\n" * 40) + "y" * MAX_MESSAGE_LEN},
            )
        )
        await streamer._flush(final=False)

    asyncio.run(drive())

    for message_id, _text, markup in bot.edits:
        if message_id != PLACEHOLDER_ID:
            assert markup is None


# --- The button's callback ---


@pytest.fixture
def approved_user(monkeypatch):
    """Make @restricted see an admin so the handler body actually runs."""
    from config_manager import UserRole

    monkeypatch.setattr(
        auth_module,
        "get_config_manager",
        lambda: SimpleNamespace(get_user_role=lambda _uid: UserRole.ADMIN),
    )


def test_tapping_stop_aborts_the_turn_and_keeps_the_session(approved_user, monkeypatch):
    calls = {"abort": [], "destroy": [], "edited": []}

    async def fake_abort(key):
        calls["abort"].append(str(key))
        return True

    async def fake_destroy(key):
        calls["destroy"].append(str(key))
        return True

    monkeypatch.setattr(runtime, "abort", fake_abort)
    monkeypatch.setattr(runtime, "destroy", fake_destroy)

    async def answer(*_a, **_kw):
        return None

    async def edit_text(text, **_kw):
        calls["edited"].append(text)
        return None

    update = SimpleNamespace(
        callback_query=SimpleNamespace(
            data="agent:cancel",
            answer=answer,
            message=SimpleNamespace(edit_text=edit_text),
        ),
        effective_chat=SimpleNamespace(id=CHAT_ID, type="private"),
        effective_user=SimpleNamespace(id=7, username="tester"),
        message=None,
    )

    asyncio.run(
        agent_callback_handler(update, SimpleNamespace(user_data={}, chat_data={}))
    )

    assert calls["abort"] == [str(SessionKey.telegram(CHAT_ID))]
    # The conversation survives being interrupted — that is the whole item.
    assert calls["destroy"] == []
    # The streamer owns that message; the handler must not overwrite the
    # partial answer with a notice of its own.
    assert calls["edited"] == []
