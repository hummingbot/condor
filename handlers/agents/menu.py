"""Agent selection and session status UI."""

import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ._shared import AGENT_OPTIONS, DEFAULT_AGENT
from .session import get_session

log = logging.getLogger(__name__)


def _agent_selection_keyboard() -> InlineKeyboardMarkup:
    """Build agent selection inline keyboard."""
    buttons = []
    for key, info in AGENT_OPTIONS.items():
        buttons.append(
            InlineKeyboardButton(info["label"], callback_data=f"agent:select:{key}")
        )
    keyboard = [buttons]
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="agent:close")])
    return InlineKeyboardMarkup(keyboard)


def _active_session_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard for active session."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Compact", callback_data="agent:compact"),
                InlineKeyboardButton("New", callback_data="agent:new"),
            ],
            [
                InlineKeyboardButton("Context", callback_data="agent:context"),
                InlineKeyboardButton("Stop", callback_data="agent:stop"),
            ],
            [InlineKeyboardButton("Close", callback_data="agent:close")],
        ]
    )


def _compact_menu_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard for compact options."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Auto", callback_data="agent:compact_auto"),
                InlineKeyboardButton("Custom", callback_data="agent:compact_custom"),
            ],
            [InlineKeyboardButton("Back", callback_data="agent:menu")],
        ]
    )


async def show_agent_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Show agent menu: selection if no session, status if active."""
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if session and session.client.alive:
        agent_label = AGENT_OPTIONS.get(session.agent_key, {}).get(
            "label", session.agent_key
        )
        status = "busy" if session.is_busy else "ready"
        # Context usage
        if session.context_window > 0 and session.tokens_used > 0:
            pct = round(session.tokens_used / session.context_window * 100)
            used_k = round(session.tokens_used / 1000)
            total_k = round(session.context_window / 1000)
            usage_line = f"Context: {used_k}k / {total_k}k ({pct}%)"
        else:
            usage_line = "Context: no usage data yet"
        cost_line = f"Cost: ${session.cost_usd:.4f}" if session.cost_usd > 0 else ""
        lines = [f"Agent: {agent_label}", f"Status: {status}", usage_line]
        if cost_line:
            lines.append(cost_line)
        lines.append("\nSend a message to chat, or use the buttons below.")
        text = "\n".join(lines)
        keyboard = _active_session_keyboard()
    else:
        text = "Select an AI agent to start a trading chat session.\n\nThe agent has access to all Hummingbot trading tools."
        keyboard = _agent_selection_keyboard()

    message = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if message:
        if update.callback_query:
            await message.edit_text(text, reply_markup=keyboard)
        else:
            await message.reply_text(text, reply_markup=keyboard)
