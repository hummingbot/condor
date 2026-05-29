"""Agent selection and session status UI."""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ._shared import AGENT_MODES, AGENT_OPTIONS, DEFAULT_AGENT, DEFAULT_MODE, format_agent_llm_label
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
    """Build LLM picker keyboard.

    The current selection is marked with a bullet. If the user has previously
    picked an OpenRouter model (agent_llm starts with "openrouter:<slug>") or a
    Cursor model (agent_llm starts with "cursor:<id>"), the sentinel row matches
    and shows the slug/id they picked.
    """
    keyboard = []
    for key, info in AGENT_OPTIONS.items():
        label = info["label"]
        is_openrouter_current = key == "openrouter:" and current_llm.startswith(
            "openrouter:"
        ) and current_llm != "openrouter:"
        is_cursor_current = key == "cursor:" and current_llm.startswith(
            "cursor:"
        ) and current_llm != "cursor:"
        is_current = key == current_llm or is_openrouter_current or is_cursor_current
        if is_openrouter_current:
            slug = current_llm.split(":", 1)[1]
            label = f"• OpenRouter — {slug}"
        elif is_cursor_current:
            model_id = current_llm.split(":", 1)[1]
            label = f"• Cursor — {model_id}"
        elif is_current:
            label = f"• {label}"
        keyboard.append(
            [InlineKeyboardButton(label, callback_data=f"agent:set_llm:{key}")]
        )
    keyboard.append([InlineKeyboardButton("Back", callback_data="agent:menu")])
    return InlineKeyboardMarkup(keyboard)


# Model picker pagination (OpenRouter + Cursor)
MODEL_PICKER_PAGE_SIZE = 8
OPENROUTER_PAGE_SIZE = MODEL_PICKER_PAGE_SIZE


def _openrouter_picker_keyboard(
    models: list, page: int, current_slug: str | None
) -> InlineKeyboardMarkup:
    """Paginated keyboard for picking an OpenRouter model.

    `models` is a list of OpenRouterModel; we reference each by its index in this
    list so callback_data stays well under Telegram's 64-byte cap regardless of
    slug length.
    """
    from .openrouter_models import format_button_label

    if not models:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("Back", callback_data="agent:settings")]]
        )

    total_pages = (len(models) + OPENROUTER_PAGE_SIZE - 1) // OPENROUTER_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    start = page * OPENROUTER_PAGE_SIZE
    end = min(start + OPENROUTER_PAGE_SIZE, len(models))

    keyboard: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("Enter model manually", callback_data="agent:or_type")],
    ]
    for idx in range(start, end):
        m = models[idx]
        label = format_button_label(m)
        if current_slug and m.slug == current_slug:
            label = f"• {label}"
        keyboard.append(
            [InlineKeyboardButton(label, callback_data=f"agent:or_pick:{idx}")]
        )

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton("‹ Prev", callback_data=f"agent:or_page:{page - 1}")
        )
    nav_row.append(
        InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="agent:or_noop")
    )
    if page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton("Next ›", callback_data=f"agent:or_page:{page + 1}")
        )
    keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("Back", callback_data="agent:settings")])
    return InlineKeyboardMarkup(keyboard)


def _cursor_picker_keyboard(
    models: list, page: int, current_id: str | None
) -> InlineKeyboardMarkup:
    """Paginated keyboard for picking a Cursor model."""
    from .cursor_models import format_button_label

    if not models:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("Back", callback_data="agent:settings")]]
        )

    total_pages = (len(models) + MODEL_PICKER_PAGE_SIZE - 1) // MODEL_PICKER_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    start = page * MODEL_PICKER_PAGE_SIZE
    end = min(start + MODEL_PICKER_PAGE_SIZE, len(models))

    keyboard: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("Enter model manually", callback_data="agent:cur_type")],
    ]
    for idx in range(start, end):
        m = models[idx]
        label = format_button_label(m)
        if current_id and m.id == current_id:
            label = f"• {label}"
        keyboard.append(
            [InlineKeyboardButton(label, callback_data=f"agent:cur_pick:{idx}")]
        )

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton("‹ Prev", callback_data=f"agent:cur_page:{page - 1}")
        )
    nav_row.append(
        InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="agent:cur_noop")
    )
    if page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton("Next ›", callback_data=f"agent:cur_page:{page + 1}")
        )
    keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("Back", callback_data="agent:settings")])
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
        agent_label = format_agent_llm_label(session.agent_key)
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
        llm_label = format_agent_llm_label(agent_key)
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