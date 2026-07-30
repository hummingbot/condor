"""Telegram delivery of trade confirmations.

The pending approval itself lives in :mod:`condor.runtime.confirmations`; this
module only renders it as an inline keyboard and posts the answer back. That
split is what lets the same request be answered from the dashboard or over
HTTP instead.
"""

import logging
from typing import Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from condor.runtime.confirmations import (
    CONFIRMATION_TIMEOUT,
    PendingConfirmation,
    get_registry,
)

log = logging.getLogger(__name__)

__all__ = [
    "CONFIRMATION_TIMEOUT",
    "TelegramChannel",
    "format_tool_summary",
    "permission_callback",
    "resolve_confirmation",
]


def format_tool_summary(tool_call: dict[str, Any]) -> str:
    """Format a tool call into a human-readable summary for the confirmation message."""
    tool_name = tool_call.get("tool", "") or tool_call.get("title", "Unknown")
    input_data = tool_call.get("input", {})

    if tool_name == "place_order":
        side = input_data.get("trade_type", "?")
        pair = input_data.get("trading_pair", "?")
        amount = input_data.get("amount", "?")
        order_type = input_data.get("order_type", "MARKET")
        price = input_data.get("price", "")
        connector = input_data.get("connector_name", "?")
        summary = f"{side} {amount} {pair} ({order_type})"
        if price:
            summary += f" @ {price}"
        summary += f" on {connector}"
        return summary

    if tool_name == "manage_executors":
        action = input_data.get("action", "?")
        exec_type = input_data.get("executor_type", "")
        exec_id = input_data.get("executor_id", "")
        if action == "create" and exec_type:
            config = input_data.get("executor_config", {})
            pair = config.get("trading_pair", "?")
            return f"Create {exec_type} on {pair}"
        if action == "stop" and exec_id:
            return f"Stop executor {exec_id[:12]}..."
        return f"Executor: {action}"

    if tool_name == "manage_bots":
        action = input_data.get("action", "?")
        bot_name = input_data.get("bot_name", "?")
        if action == "deploy":
            controllers = input_data.get("controllers_config", [])
            return f"Deploy bot '{bot_name}' with controllers {controllers}"
        if action == "update_config":
            config_name = input_data.get("config_name", "?")
            return f"Update config '{config_name}' on bot '{bot_name}'"
        return f"Bot '{bot_name}': {action}"

    if tool_name == "manage_gateway_swaps":
        action = input_data.get("action", "?")
        pair = input_data.get("trading_pair", "?")
        side = input_data.get("side", "?")
        amount = input_data.get("amount", "?")
        return f"Swap {side} {amount} {pair}"

    if tool_name == "manage_gateway_clmm":
        action = input_data.get("action", "?")
        if action == "open_position":
            return "Open LP position"
        if action == "close_position":
            return "Close LP position"
        return f"CLMM: {action}"

    # Generic fallback
    return tool_name


# Backwards-compatible alias for the pre-registry name.
_format_tool_summary = format_tool_summary


class TelegramChannel:
    """Renders a pending confirmation as an inline keyboard in a chat."""

    def __init__(self, bot: Bot, chat_id: int):
        self._bot = bot
        self._chat_id = chat_id

    async def deliver(self, pending: PendingConfirmation) -> None:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Approve", callback_data=f"agent:confirm_trade:{pending.id}"
                    ),
                    InlineKeyboardButton(
                        "Reject", callback_data=f"agent:reject_trade:{pending.id}"
                    ),
                ]
            ]
        )
        await self._bot.send_message(
            chat_id=self._chat_id,
            text=f"Trade Confirmation\n\n{pending.summary}\n\nApprove this action?",
            reply_markup=keyboard,
        )


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
