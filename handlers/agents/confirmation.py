"""Telegram delivery of trade confirmations.

The pending approval itself lives in :mod:`condor.runtime.confirmations`; this
module only renders it as an inline keyboard and posts the answer back. That
split is what lets the same request be answered from the dashboard or over
HTTP instead.
"""

import logging
from typing import Any

from telegram import Bot

from condor.runtime.channels import TelegramChannel  # noqa: F401  (re-export)
from condor.runtime.confirmations import CONFIRMATION_TIMEOUT, get_registry
from condor.runtime.danger import format_tool_summary  # noqa: F401  (re-export)

log = logging.getLogger(__name__)

__all__ = [
    "CONFIRMATION_TIMEOUT",
    "TelegramChannel",
    "format_tool_summary",
    "permission_callback",
    "resolve_confirmation",
]


# Backwards-compatible alias for the pre-registry name; the implementation
# moved to condor.runtime.danger (ARCH-190).
_format_tool_summary = format_tool_summary


async def permission_callback(
    bot: Bot,
    chat_id: int,
    tool_call: dict[str, Any],
    options: list[dict[str, Any]],
    user_id: int | None = None,
) -> dict[str, Any]:
    """Permission callback delivering to one Telegram chat.

    Thin wrapper over the shared builder, kept because several call sites bind
    it with ``functools.partial(bot, chat_id)``. In a private chat the chat id
    IS the user id, which is why it is the authorization fallback.
    """
    from condor.runtime.confirmations import build_permission_callback

    callback = build_permission_callback(
        session_key=f"tg:{chat_id}",
        user_id=user_id if user_id is not None else chat_id,
        channels=[TelegramChannel(bot, chat_id)],
    )
    return await callback(tool_call, options)


async def resolve_confirmation(
    request_id: str, approved: bool, by_user_id: int | None = None
) -> bool:
    """Called from the Telegram callback when the user taps Approve/Reject.

    Returns True if the request was found and this user was allowed to answer
    it. False also covers "someone already answered", which is why the caller
    reports it as expired rather than as an error.
    """
    return await get_registry().resolve(
        request_id, approved=approved, by_user_id=by_user_id
    )
