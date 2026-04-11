"""Agent selection and session status UI."""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ._shared import AGENT_MODES, AGENT_OPTIONS, DEFAULT_AGENT, DEFAULT_MODE
from .session import get_session

log = logging.getLogger(__name__)


def _active_session_keyboard(mode: str) -> InlineKeyboardMarkup:
    """Build keyboard for active session."""
    rows = [
        [
            InlineKeyboardButton("Switch Agent Mode", callback_data="agent:switch_mode"),
            InlineKeyboardButton("New", callback_data="agent:new"),
        ],
        [
            InlineKeyboardButton("Compact", callback_data="agent:compact"),
        ],
        [
            InlineKeyboardButton("Change LLM", callback_data="agent:settings"),
            InlineKeyboardButton("Stop", callback_data="agent:stop"),
        ],
        [InlineKeyboardButton("Close", callback_data="agent:close")],
    ]
    return InlineKeyboardMarkup(rows)


def _mode_selection_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard for mode selection."""
    buttons = []
    for key, info in AGENT_MODES.items():
        buttons.append(
            InlineKeyboardButton(info["label"], callback_data=f"agent:mode:{key}")
        )
    keyboard = [buttons]
    keyboard.append([InlineKeyboardButton("Back", callback_data="agent:menu")])
    return InlineKeyboardMarkup(keyboard)


def _settings_keyboard(current_llm: str) -> InlineKeyboardMarkup:
    """Build LLM picker keyboard."""
    keyboard = []
    for key, info in AGENT_OPTIONS.items():
        label = info["label"]
        if key == current_llm:
            label = f"• {label}"
        keyboard.append(
            [InlineKeyboardButton(label, callback_data=f"agent:set_llm:{key}")]
        )
    keyboard.append([InlineKeyboardButton("Back", callback_data="agent:menu")])
    return InlineKeyboardMarkup(keyboard)


def _no_session_keyboard(mode: str) -> InlineKeyboardMarkup:
    """Build keyboard when no session is active."""
    rows = [
        [InlineKeyboardButton("Start", callback_data=f"agent:mode:{mode}")],
        [
            InlineKeyboardButton("Switch Agent Mode", callback_data="agent:switch_mode"),
            InlineKeyboardButton("Change LLM", callback_data="agent:settings"),
        ],
        [InlineKeyboardButton("Close", callback_data="agent:close")],
    ]
    return InlineKeyboardMarkup(rows)


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
    """Show agent menu: active session info or auto-start."""
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if session and session.client.alive:
        mode_label = AGENT_MODES.get(session.mode, {}).get("label", session.mode)
        agent_label = AGENT_OPTIONS.get(session.agent_key, {}).get(
            "label", session.agent_key
        )
        status = "busy" if session.is_busy else "ready"
        lines = [
            f"Mode: {mode_label}",
            f"LLM: {agent_label}",
            f"Status: {status}",
            "\nSend a message to chat, or use the buttons below.",
        ]
        text = "\n".join(lines)
        keyboard = _active_session_keyboard(session.mode)
    else:
        # No session — show options to start, switch mode, or change settings
        agent_key = context.user_data.get("agent_llm", DEFAULT_AGENT)
        mode = context.user_data.get("agent_mode", DEFAULT_MODE)
        mode_label = AGENT_MODES.get(mode, {}).get("label", mode)
        llm_label = AGENT_OPTIONS.get(agent_key, {}).get("label", agent_key)
        text = (
            f"No active session\n"
            f"Mode: {mode_label}\n"
            f"LLM: {llm_label}\n\n"
            "Start a session or adjust settings below."
        )
        keyboard = _no_session_keyboard(mode)

    message = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if message:
        if update.callback_query:
            if keyboard:
                await message.edit_text(text, reply_markup=keyboard)
            else:
                await message.edit_text(text)
        else:
            if keyboard:
                await message.reply_text(text, reply_markup=keyboard)
            else:
                await message.reply_text(text)
