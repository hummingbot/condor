"""Telegram-side wrapper of the chat session manager.

The session core moved to ``condor.agents.chat_session`` (simplification
plan §5.1). This module keeps the Telegram handlers' import paths working
and adapts the health monitor to Telegram (a bot handle instead of the
injected notifier the core takes).
"""

import logging

from telegram import Bot

from condor.agents.chat_session import (  # noqa: F401 — re-exported for handlers
    PROMPT_LOCK_TIMEOUT,
    PROMPT_OVERALL_TIMEOUT,
    AgentSession,
    _destroy_session_internal,
    _sessions,
    destroy_all_sessions,
    destroy_session,
    get_or_create_session,
    get_session,
)
from condor.agents.chat_session import (
    start_health_monitor as _start_health_monitor,
)
from condor.agents.chat_session import (
    stop_health_monitor,  # noqa: F401 — re-exported for main.py
)

log = logging.getLogger(__name__)


async def start_health_monitor(bot: Bot) -> None:
    """Start the session health monitor with a Telegram notifier.

    Only integer session keys are Telegram chats; web sessions (string keys)
    get no Telegram notification.
    """

    async def _notify(chat_id) -> None:
        if isinstance(chat_id, int):
            await bot.send_message(
                chat_id=chat_id,
                text="Agent session ended unexpectedly. Send a message to start a new session.",
            )

    await _start_health_monitor(_notify)
