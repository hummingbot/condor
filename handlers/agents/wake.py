"""Rendering a server-initiated turn in Telegram (FEAT-034).

A woken turn has no message to reply to and no ``context.bot`` handed down from
an update — it starts in a background task when a delegation finishes. So this
sink does what the message handler does, from the other side: send a placeholder,
wrap it in :class:`~handlers.agents.stream.TelegramStreamer`, and finalize it when
the turn ends. The chat then shows the agent's continuation exactly the way it
shows an answer to something the user typed.

The sink is registered with :mod:`condor.runtime.wake` at import time rather than
imported by it: ``condor.runtime`` must not depend on handler code (see
``client._local()``).
"""

from __future__ import annotations

import logging

from condor.runtime import TELEGRAM
from condor.runtime.events import RuntimeEvent
from condor.runtime.keys import SessionKey
from condor.runtime.wake import register_sink_factory

from .stream import TelegramStreamer

log = logging.getLogger(__name__)

PLACEHOLDER = "Thinking..."


def _message_id_of(sent) -> int | None:
    """The id of a just-sent message, whichever sender produced it.

    ``Bot.send_message`` returns a ``Message``; the ``_HttpBot`` fallback returns
    the raw Telegram API envelope. The streamer needs the id either way.
    """
    message_id = getattr(sent, "message_id", None)
    if message_id is not None:
        return int(message_id)
    if isinstance(sent, dict):
        result = sent.get("result") or {}
        if isinstance(result, dict) and result.get("message_id") is not None:
            return int(result["message_id"])
    return None


class TelegramWakeSink:
    """Streams a server-initiated turn into a Telegram chat."""

    def __init__(self, chat_id: int):
        self._chat_id = chat_id
        self._streamer: TelegramStreamer | None = None

    async def open(self) -> None:
        """Send the placeholder the turn will be written into.

        A chat this process cannot reach (no live bot and no token) leaves the
        sink inert instead of failing the turn: the answer is still recorded,
        and the user has already had the completion notice.
        """
        from condor.agents.delegate import resolve_bot

        bot = resolve_bot()
        sent = await bot.send_message(chat_id=self._chat_id, text=PLACEHOLDER)
        message_id = _message_id_of(sent)
        if message_id is None:
            log.debug(
                "No Telegram placeholder for chat %s; wake runs unseen", self._chat_id
            )
            return
        self._streamer = TelegramStreamer(
            bot=bot, chat_id=self._chat_id, message_id=message_id
        )
        self._streamer.start_edit_loop()

    async def on_event(self, event: RuntimeEvent) -> None:
        if self._streamer is not None:
            await self._streamer.process_event(event)

    async def close(self) -> None:
        if self._streamer is not None:
            await self._streamer.finalize()


def _telegram_wake_sink(
    key: SessionKey, user_id: int | None
) -> TelegramWakeSink | None:
    chat_id = key.telegram_chat_id
    return TelegramWakeSink(chat_id) if chat_id is not None else None


register_sink_factory(TELEGRAM, _telegram_wake_sink)
