"""Trade confirmation flow using async Futures."""

import asyncio
import logging
import uuid
from typing import Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from ._shared import is_dangerous_tool_call

log = logging.getLogger(__name__)

# Pending confirmation futures: request_id -> Future
_pending: dict[str, asyncio.Future] = {}

CONFIRMATION_TIMEOUT = 120  # seconds


def _format_tool_summary(tool_call: dict[str, Any]) -> str:
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


async def permission_callback(
    bot: Bot,
    chat_id: int,
    tool_call: dict[str, Any],
    options: list[dict[str, Any]],
) -> dict[str, Any]:
    """Called by ACPClient when agent requests permission.

    For dangerous tools, sends a confirmation message and waits for user response.
    For safe tools, auto-approves immediately.
    """
    # Auto-approve safe tools
    if not is_dangerous_tool_call(tool_call):
        for opt in options:
            if opt.get("kind") in ("allow_once", "allow_always"):
                return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
        if options:
            return {"outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}}
        return {"outcome": {"outcome": "cancelled"}}

    # Dangerous tool -- ask user for confirmation
    request_id = str(uuid.uuid4())[:8]
    summary = _format_tool_summary(tool_call)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Approve", callback_data=f"agent:confirm_trade:{request_id}"),
            InlineKeyboardButton("Reject", callback_data=f"agent:reject_trade:{request_id}"),
        ]
    ])

    await bot.send_message(
        chat_id=chat_id,
        text=f"Trade Confirmation\n\n{summary}\n\nApprove this action?",
        reply_markup=keyboard,
    )

    # Create future and wait for user response
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending[request_id] = future

    try:
        approved = await asyncio.wait_for(future, timeout=CONFIRMATION_TIMEOUT)
    except asyncio.TimeoutError:
        _pending.pop(request_id, None)
        await bot.send_message(
            chat_id=chat_id,
            text="Confirmation timed out -- action rejected.",
        )
        return {"outcome": {"outcome": "cancelled"}}
    finally:
        _pending.pop(request_id, None)

    if approved:
        # Find allow option
        for opt in options:
            if opt.get("kind") in ("allow_once", "allow_always"):
                return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
        if options:
            return {"outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}}

    return {"outcome": {"outcome": "cancelled"}}


def resolve_confirmation(request_id: str, approved: bool) -> bool:
    """Called from Telegram callback when user clicks Approve/Reject.

    Returns True if the request was found and resolved.
    """
    future = _pending.get(request_id)
    if future and not future.done():
        future.set_result(approved)
        return True
    return False
