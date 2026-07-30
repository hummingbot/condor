"""Agent selection and session status UI."""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from condor.runtime import SessionKey
from condor.runtime import client as runtime

from ._shared import AGENT_MODES, AGENT_OPTIONS, DEFAULT_AGENT, normalize_mode
from .custom_models import format_model_label

log = logging.getLogger(__name__)


def _active_session_keyboard(mode: str) -> InlineKeyboardMarkup:
    """Build keyboard for active session."""
    rows = [
        [
            InlineKeyboardButton("New", callback_data="agent:new"),
            InlineKeyboardButton("Compact", callback_data="agent:compact"),
        ],
        [
            InlineKeyboardButton("Change LLM", callback_data="agent:settings"),
            InlineKeyboardButton("Talk to", callback_data="agent:talk_to"),
        ],
        [
            InlineKeyboardButton("Stop", callback_data="agent:stop"),
            InlineKeyboardButton("Close", callback_data="agent:close"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


# "Talk to" picker pagination
TALK_PAGE_SIZE = 8


def _talk_to_keyboard(
    agents: list, page: int, current_slug: str
) -> InlineKeyboardMarkup:
    """Paginated picker of domain Agents to hold a conversation with.

    Agents are referenced by index so callback_data stays well under Telegram's
    64-byte cap regardless of slug length, matching the OpenRouter picker.
    """
    keyboard: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                ("• " if not current_slug else "") + "Condor (coordinator)",
                callback_data="agent:talk_pick:-1",
            )
        ]
    ]

    if not agents:
        keyboard.append([InlineKeyboardButton("Back", callback_data="agent:menu")])
        return InlineKeyboardMarkup(keyboard)

    total_pages = (len(agents) + TALK_PAGE_SIZE - 1) // TALK_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    start = page * TALK_PAGE_SIZE
    end = min(start + TALK_PAGE_SIZE, len(agents))

    for idx in range(start, end):
        agent = agents[idx]
        label = agent.name or agent.slug
        if agent.slug == current_slug:
            label = f"• {label}"
        keyboard.append(
            [InlineKeyboardButton(label, callback_data=f"agent:talk_pick:{idx}")]
        )

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    "‹ Prev", callback_data=f"agent:talk_page:{page - 1}"
                )
            )
        nav.append(
            InlineKeyboardButton(
                f"{page + 1}/{total_pages}", callback_data="agent:talk_noop"
            )
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    "Next ›", callback_data=f"agent:talk_page:{page + 1}"
                )
            )
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("Back", callback_data="agent:menu")])
    return InlineKeyboardMarkup(keyboard)


# Sentinel rows that open a model picker instead of setting agent_llm directly.
# Maps sentinel key → display name used when a picked model is the current LLM.
_PICKER_SENTINELS = {"openrouter:": "OpenRouter", "custom:": "Custom"}


def _picked_model_label(sentinel_key: str, current_llm: str) -> str | None:
    """Label for a sentinel row when the active LLM came from that picker.

    Returns None when `current_llm` isn't one of this sentinel's models. For
    custom endpoints the row is named after the saved endpoint rather than the
    word "Custom", so a user with two endpoints can tell which one is live.
    """
    from condor.acp.pydantic_ai_client import model_prefix
    from condor.preferences import parse_custom_agent_key

    sentinel_prefix = sentinel_key.rstrip(":")
    if model_prefix(current_llm) != sentinel_prefix or current_llm == sentinel_key:
        return None

    if sentinel_prefix == "custom":
        provider, model_id = parse_custom_agent_key(current_llm)
        if not model_id:
            return None
        return f"• {provider or 'Custom'} — {model_id}"

    return f"• {_PICKER_SENTINELS[sentinel_key]} — {current_llm.partition(':')[2]}"


def _settings_keyboard(current_llm: str) -> InlineKeyboardMarkup:
    """Build LLM picker keyboard.

    The current selection is marked with a bullet. If the user has previously
    picked a model through a sentinel row (agent_llm like "openrouter:<slug>"
    or "custom@venice:<model-id>"), that sentinel row matches and shows the pick.
    """
    keyboard = []
    for key, info in AGENT_OPTIONS.items():
        label = info["label"]
        picked = (
            _picked_model_label(key, current_llm) if key in _PICKER_SENTINELS else None
        )
        if picked:
            label = picked
        elif key == current_llm:
            label = f"• {label}"
        keyboard.append(
            [InlineKeyboardButton(label, callback_data=f"agent:set_llm:{key}")]
        )
    keyboard.append([InlineKeyboardButton("Back", callback_data="agent:menu")])
    return InlineKeyboardMarkup(keyboard)


# OpenRouter picker pagination
OPENROUTER_PAGE_SIZE = 8


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


def _openrouter_input_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown while waiting for a typed OpenRouter slug.

    The prompt arms a "next message is the slug" mode, so it needs a visible way
    out — otherwise a user who changed their mind has to know to type /cancel.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Back to model list", callback_data="agent:or_page:0"
                )
            ],
            [InlineKeyboardButton("Cancel", callback_data="agent:or_type_cancel")],
        ]
    )


# Custom provider picker pagination
CUSTOM_PAGE_SIZE = 8


def _custom_endpoints_keyboard(
    providers: list[dict], current_llm: str
) -> InlineKeyboardMarkup:
    """Landing screen for custom endpoints: pick one, add one, or manage them."""
    from condor.preferences import parse_custom_agent_key

    active_provider, active_model = parse_custom_agent_key(current_llm)

    keyboard: list[list[InlineKeyboardButton]] = []
    for idx, provider in enumerate(providers):
        name = provider.get("name", "?")
        if active_provider == name and active_model:
            label = f"• {name} — {format_model_label(active_model, limit=24)}"
        else:
            label = name
        keyboard.append(
            [InlineKeyboardButton(label, callback_data=f"agent:cu_use:{idx}")]
        )

    keyboard.append(
        [InlineKeyboardButton("+ Add endpoint", callback_data="agent:cu_add")]
    )
    if providers:
        keyboard.append(
            [InlineKeyboardButton("Manage endpoints", callback_data="agent:cu_manage")]
        )
    keyboard.append([InlineKeyboardButton("Back", callback_data="agent:settings")])
    return InlineKeyboardMarkup(keyboard)


def _custom_manage_keyboard(providers: list[dict]) -> InlineKeyboardMarkup:
    """Per-endpoint maintenance: replace the API key, or forget the endpoint."""
    keyboard: list[list[InlineKeyboardButton]] = []
    for idx, provider in enumerate(providers):
        name = provider.get("name", "?")
        keyboard.append([InlineKeyboardButton(name, callback_data="agent:cu_noop")])
        keyboard.append(
            [
                InlineKeyboardButton(
                    "Change API key", callback_data=f"agent:cu_key:{idx}"
                ),
                InlineKeyboardButton("Forget", callback_data=f"agent:cu_del:{idx}"),
            ]
        )
    keyboard.append([InlineKeyboardButton("Back", callback_data="agent:cu_list")])
    return InlineKeyboardMarkup(keyboard)


def _custom_delete_keyboard(idx: int, name: str) -> InlineKeyboardMarkup:
    """Confirmation for forgetting an endpoint."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Forget {name}", callback_data=f"agent:cu_delok:{idx}"
                )
            ],
            [InlineKeyboardButton("Keep it", callback_data="agent:cu_manage")],
        ]
    )


def _custom_input_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown while waiting for typed input (URL, key, search)."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Cancel", callback_data="agent:cu_cancel")]]
    )


def _custom_key_prompt_keyboard() -> InlineKeyboardMarkup:
    """API-key prompt: type one, or declare the endpoint doesn't need one."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("No API key needed", callback_data="agent:cu_nokey")],
            [InlineKeyboardButton("Cancel", callback_data="agent:cu_cancel")],
        ]
    )


def _custom_error_keyboard(
    retry_action: str, secondary: tuple[str, str] | None = None
) -> InlineKeyboardMarkup:
    """Recovery keyboard for a failed validation.

    Validation failures are exactly where a dead-end message hurts most — the
    user has just typed a URL and a key and has nothing to tap. `secondary` is
    the (label, callback) for the sideways fix, which differs by context:
    a new endpoint needs its URL corrected, a saved one usually needs its key.
    """
    label, action = secondary or ("Edit URL", "agent:cu_add")
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Try again", callback_data=retry_action),
                InlineKeyboardButton(label, callback_data=action),
            ],
            [InlineKeyboardButton("Back", callback_data="agent:cu_list")],
        ]
    )


def _order_models(
    model_ids: list[str], current_id: str | None, query: str
) -> list[str]:
    """Apply the search filter and float the active model to the top."""
    models = list(model_ids)
    if query:
        needle = query.lower()
        models = [m for m in models if needle in m.lower()]
    if current_id and current_id in models:
        models.remove(current_id)
        models.insert(0, current_id)
    return models


def _custom_picker_keyboard(
    model_ids: list[str],
    page: int,
    current_id: str | None,
    query: str = "",
) -> InlineKeyboardMarkup:
    """Paginated keyboard for picking a model from a custom endpoint.

    Buttons carry a short content hash of the model id rather than its index:
    ids are unbounded and callback_data is capped at 64 bytes, and a hash still
    resolves to the right model if the list was refetched between render and tap.
    """
    from .custom_models import model_token

    models = _order_models(model_ids, current_id, query)
    keyboard: list[list[InlineKeyboardButton]] = []

    if not models:
        keyboard.append(
            [InlineKeyboardButton("No models match", callback_data="agent:cu_noop")]
        )
        total_pages = 1
        page = 0
    else:
        total_pages = (len(models) + CUSTOM_PAGE_SIZE - 1) // CUSTOM_PAGE_SIZE
        page = max(0, min(page, total_pages - 1))
        start = page * CUSTOM_PAGE_SIZE
        for model_id in models[start : start + CUSTOM_PAGE_SIZE]:
            label = format_model_label(model_id)
            if current_id and model_id == current_id:
                label = f"• {label}"
            keyboard.append(
                [
                    InlineKeyboardButton(
                        label, callback_data=f"agent:cu_pick:{model_token(model_id)}"
                    )
                ]
            )

    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton(
                    "‹ Prev", callback_data=f"agent:cu_page:{page - 1}"
                )
            )
        nav_row.append(
            InlineKeyboardButton(
                f"{page + 1}/{total_pages}", callback_data="agent:cu_noop"
            )
        )
        if page < total_pages - 1:
            nav_row.append(
                InlineKeyboardButton(
                    "Next ›", callback_data=f"agent:cu_page:{page + 1}"
                )
            )
        keyboard.append(nav_row)

    # A search row only earns its space once the list is too long to scan
    if query:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"Clear filter '{query}'", callback_data="agent:cu_clear"
                )
            ]
        )
    elif len(model_ids) > CUSTOM_PAGE_SIZE:
        keyboard.append(
            [InlineKeyboardButton("Search models", callback_data="agent:cu_search")]
        )

    keyboard.append([InlineKeyboardButton("Back", callback_data="agent:cu_list")])
    return InlineKeyboardMarkup(keyboard)


def _no_session_keyboard(mode: str) -> InlineKeyboardMarkup:
    """Build keyboard when no session is active."""
    rows = [
        [
            InlineKeyboardButton("Start", callback_data=f"agent:mode:{mode}"),
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
    session = await runtime.get_info(SessionKey.telegram(chat_id))

    if session and session.alive:
        mode_label = AGENT_MODES.get(session.mode, {}).get("label", session.mode)
        agent_label = AGENT_OPTIONS.get(session.agent_key, {}).get(
            "label", session.agent_key
        )
        status = "busy" if session.is_busy else "ready"
        # A bound domain Agent replaces the assistant persona on the header:
        # it is a different brain, not a different mode.
        who = (
            f"Talking to: {session.label}"
            if session.agent_slug
            else (f"Mode: {mode_label}")
        )
        lines = [
            who,
            f"LLM: {agent_label}",
            f"Status: {status}",
            "\nSend a message to chat, or use the buttons below.",
        ]
        text = "\n".join(lines)
        keyboard = _active_session_keyboard(session.mode)
    else:
        # No session — show options to start or change settings
        agent_key = context.user_data.get("agent_llm", DEFAULT_AGENT)
        mode = normalize_mode(context.user_data.get("agent_mode"))
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
