"""Telegram confirmation transport for the agent permission flow.

The registry + formatter moved to ``condor.agents.confirmation``
(simplification plan §5.1); this module keeps only the Telegram rendering —
an inline-keyboard confirmation message whose Approve/Reject callback
resolves the shared registry.
"""

import logging
import uuid
from typing import Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from condor.agents.confirmation import (  # noqa: F401 — re-exported for callers
    CONFIRMATION_TIMEOUT,
    _format_tool_summary,
    _pending,
    discard_confirmation,
    register_confirmation,
    resolve_confirmation,
)
from condor.agents.gating import BLOCKED_TOOLS, is_dangerous_tool_call

log = logging.getLogger(__name__)


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
    import asyncio

    # Block tools that bypass Condor's RBAC
    tool_name = tool_call.get("tool", "") or tool_call.get("title", "")
    if tool_name in BLOCKED_TOOLS:
        log.warning("Blocked tool %s in chat %d", tool_name, chat_id)
        return {"outcome": {"outcome": "cancelled"}}

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
    future = register_confirmation(request_id)

    try:
        approved = await asyncio.wait_for(future, timeout=CONFIRMATION_TIMEOUT)
    except asyncio.TimeoutError:
        await bot.send_message(
            chat_id=chat_id,
            text="Confirmation timed out -- action rejected.",
        )
        return {"outcome": {"outcome": "cancelled"}}
    finally:
        discard_confirmation(request_id)

    if approved:
        # Find allow option
        for opt in options:
            if opt.get("kind") in ("allow_once", "allow_always"):
                return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
        if options:
            return {"outcome": {"outcome": "selected", "optionId": options[0]["optionId"]}}

    return {"outcome": {"outcome": "cancelled"}}
