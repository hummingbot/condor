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
            InlineKeyboardButton("Context", callback_data="agent:context"),
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
    buttons = []
    for key, info in AGENT_OPTIONS.items():
        label = info["label"]
        if key == current_llm:
            label = f"• {label}"
        buttons.append(
            InlineKeyboardButton(label, callback_data=f"agent:set_llm:{key}")
        )
    keyboard = [buttons]
    keyboard.append([InlineKeyboardButton("Back", callback_data="agent:menu")])
    return InlineKeyboardMarkup(keyboard)


def _running_agents_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard listing running TickEngine instances for Chat with Agent."""
    from condor.trading_agent.engine import get_all_engines

    engines = get_all_engines()
    rows = []
    for eid, engine in engines.items():
        info = engine.get_info()
        label = f"{info['strategy']} ({eid})"
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"agent:chat_target:{eid}")]
        )
    if not rows:
        rows.append(
            [InlineKeyboardButton("No running agents", callback_data="agent:menu")]
        )
    rows.append([InlineKeyboardButton("Back", callback_data="agent:switch_mode")])
    return InlineKeyboardMarkup(rows)


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
        # Context usage
        if session.context_window > 0 and session.tokens_used > 0:
            pct = round(session.tokens_used / session.context_window * 100)
            used_k = round(session.tokens_used / 1000)
            total_k = round(session.context_window / 1000)
            usage_line = f"Context: {used_k}k / {total_k}k ({pct}%)"
        else:
            usage_line = "Context: no usage data yet"
        cost_line = f"Cost: ${session.cost_usd:.4f}" if session.cost_usd > 0 else ""
        lines = [
            f"Mode: {mode_label}",
            f"LLM: {agent_label}",
            f"Status: {status}",
            usage_line,
        ]
        if cost_line:
            lines.append(cost_line)
        lines.append("\nSend a message to chat, or use the buttons below.")
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
