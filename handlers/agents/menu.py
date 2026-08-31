"""Agent selection and session status UI."""

import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from condor.runtime import SessionKey
from condor.runtime import client as runtime

from ._shared import AGENT_OPTIONS, DEFAULT_AGENT, resolve_chat_binding
from .custom_models import format_model_label

log = logging.getLogger(__name__)


def _active_session_keyboard() -> InlineKeyboardMarkup:
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
        [InlineKeyboardButton("Conversations", callback_data="agent:conv_list")],
        [
            # Kills the subprocess and the conversation with it. Labelled
            # "End session" so it is not read as "stop generating" — that is
            # /stop, which aborts the turn and keeps the context.
            InlineKeyboardButton("End session", callback_data="agent:stop"),
            InlineKeyboardButton("Close", callback_data="agent:close"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def stop_generating_keyboard() -> InlineKeyboardMarkup:
    """The button that rides on a streamed answer while it is being written.

    Same semantics as /stop — abort the turn at the agent, keep the session and
    its context — but reachable without leaving the message you are watching,
    which is where someone actually decides an answer has gone wrong. Dropped by
    `TelegramStreamer.finalize()`, so it never outlives the turn it can stop.
    """
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⏹ Stop generating", callback_data="agent:cancel")]]
    )


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


# Conversations picker pagination. Smaller than the other pickers on purpose:
# each row here is a conversation *plus* its delete button, and the labels carry
# three facts, so eight of them is a wall of text on a phone.
CONVERSATIONS_PAGE_SIZE = 6

# How much of a title survives into a button label. The rest of the row is the
# age and the agent, which is what tells two same-titled chats apart.
_CONV_TITLE_CHARS = 28


def _ago(when: datetime) -> str:
    """Compact age of a conversation: ``now``, ``12m``, ``5h``, ``3d``.

    Rendered rather than the timestamp itself because the only question a picker
    answers is "which of these is the one I was in" — and the answer is almost
    always the recency, not the date.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - when).total_seconds()
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _conversation_label(meta, current_id: str) -> str:
    """One conversation as a button: how old, who with, what about.

    The agent is on every row because that is what makes a flat log navigable as
    multi-agent history — "the one with the funding desk" is how someone
    remembers a chat. An empty ``agent_slug`` is the coordinator.
    """
    title = meta.title or meta.last_snippet or "empty"
    if len(title) > _CONV_TITLE_CHARS:
        title = title[: _CONV_TITLE_CHARS - 1].rstrip() + "…"
    marker = "• " if current_id and meta.id == current_id else ""
    return f"{marker}{_ago(meta.updated_at)} · {meta.agent_slug or 'Condor'} · {title}"


def _conversations_keyboard(
    metas: list, page: int, current_id: str
) -> InlineKeyboardMarkup:
    """Paginated picker over the caller's conversations, newest first.

    Rows are addressed by a digest of the conversation id rather than by their
    position: this list is shared with the dashboard and is sorted by last write,
    so it reorders under the user's thumb, and a position tapped after a reorder
    would resume — or delete — the wrong chat.
    """
    from ._shared import conversation_token

    keyboard: list[list[InlineKeyboardButton]] = []

    if not metas:
        keyboard.append([InlineKeyboardButton("Back", callback_data="agent:menu")])
        return InlineKeyboardMarkup(keyboard)

    total_pages = (len(metas) + CONVERSATIONS_PAGE_SIZE - 1) // CONVERSATIONS_PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    start = page * CONVERSATIONS_PAGE_SIZE

    for meta in metas[start : start + CONVERSATIONS_PAGE_SIZE]:
        token = conversation_token(meta.id)
        keyboard.append(
            [
                InlineKeyboardButton(
                    _conversation_label(meta, current_id),
                    callback_data=f"agent:conv_pick:{token}",
                ),
                InlineKeyboardButton("🗑", callback_data=f"agent:conv_del:{token}"),
            ]
        )

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    "‹ Prev", callback_data=f"agent:conv_page:{page - 1}"
                )
            )
        nav.append(
            InlineKeyboardButton(
                f"{page + 1}/{total_pages}", callback_data="agent:conv_noop"
            )
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    "Next ›", callback_data=f"agent:conv_page:{page + 1}"
                )
            )
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("Back", callback_data="agent:menu")])
    return InlineKeyboardMarkup(keyboard)


def _conversation_delete_keyboard(meta) -> InlineKeyboardMarkup:
    """Confirmation for forgetting a conversation.

    Deleting is the only verb in the store that loses something, and it loses it
    for both surfaces at once, so it gets the same explicit second tap the custom
    endpoints do. The button carries the id's digest, not the row's position, so
    a list that reordered while the confirmation was open cannot redirect it.
    """
    from ._shared import conversation_token

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Delete for good",
                    callback_data=f"agent:conv_delok:{conversation_token(meta.id)}",
                )
            ],
            [InlineKeyboardButton("Keep it", callback_data="agent:conv_list")],
        ]
    )


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


def _settings_keyboard(
    current_llm: str, secret_notices: bool = True
) -> InlineKeyboardMarkup:
    """Build LLM picker keyboard.

    The current selection is marked with a bullet. If the user has previously
    picked a model through a sentinel row (agent_llm like "openrouter:<slug>"
    or "custom@venice:<model-id>"), that sentinel row matches and shows the pick.

    The last row is the key-shape warning switch (FEAT-056), which is here
    because a preference with no way back on is not a preference.
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
    keyboard.append(
        [
            InlineKeyboardButton(
                f"Key-shape warnings: {'on' if secret_notices else 'off'}",
                callback_data="agent:secret_notices_toggle",
            )
        ]
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
    from condor.preferences import parse_custom_agent_key, sanitize_provider_name

    active_provider, active_model = parse_custom_agent_key(current_llm)

    keyboard: list[list[InlineKeyboardButton]] = []
    for provider in providers:
        name = provider.get("name", "?")
        if active_provider == name and active_model:
            label = f"• {name} — {format_model_label(active_model, limit=24)}"
        else:
            label = name
        key = sanitize_provider_name(name)
        keyboard.append(
            [InlineKeyboardButton(label, callback_data=f"agent:cu_use:{key}")]
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
    from condor.preferences import sanitize_provider_name

    keyboard: list[list[InlineKeyboardButton]] = []
    for provider in providers:
        name = provider.get("name", "?")
        key = sanitize_provider_name(name)
        keyboard.append([InlineKeyboardButton(name, callback_data="agent:cu_noop")])
        keyboard.append(
            [
                InlineKeyboardButton(
                    "Change API key", callback_data=f"agent:cu_key:{key}"
                ),
                InlineKeyboardButton("Forget", callback_data=f"agent:cu_del:{key}"),
            ]
        )
    keyboard.append([InlineKeyboardButton("Back", callback_data="agent:cu_list")])
    return InlineKeyboardMarkup(keyboard)


def _custom_delete_keyboard(name: str) -> InlineKeyboardMarkup:
    """Confirmation for forgetting an endpoint.

    The button carries the endpoint's name, not its position: the saved list is
    shared with the web dashboard, so an index could resolve to a different
    endpoint by the time the confirmation is tapped.
    """
    from condor.preferences import sanitize_provider_name

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Forget {name}",
                    callback_data=f"agent:cu_delok:{sanitize_provider_name(name)}",
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


def _no_session_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard when no session is active."""
    rows = [
        [
            InlineKeyboardButton("Start", callback_data="agent:start"),
            InlineKeyboardButton("Change LLM", callback_data="agent:settings"),
        ],
        # Here too, not only beside a live session: picking the specialist is
        # how you say which one to *raise*, and hanging it off a running
        # subprocess made the coordinator the only thing a chat could start as.
        [InlineKeyboardButton("Talk to", callback_data="agent:talk_to")],
        # Reachable with no session precisely because this is when it is wanted:
        # the chat whose subprocess is gone is the one whose owner is looking for
        # what they were saying yesterday.
        [InlineKeyboardButton("Conversations", callback_data="agent:conv_list")],
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
        agent_label = AGENT_OPTIONS.get(session.agent_key, {}).get(
            "label", session.agent_key
        )
        status = "busy" if session.is_busy else "ready"
        lines = [
            f"Talking to: {session.label}",
            f"LLM: {agent_label}",
            f"Status: {status}",
            "\nSend a message to chat, or use the buttons below.",
        ]
        # Advertised where the user can see it is answering — a menu opened
        # mid-answer is exactly when someone wants to interrupt.
        if session.is_busy:
            lines.append("/stop interrupts the answer without losing the session.")
        text = "\n".join(lines)
        keyboard = _active_session_keyboard()
    else:
        # No session — show options to start or change settings
        agent_key = context.user_data.get("agent_llm", DEFAULT_AGENT)
        llm_label = AGENT_OPTIONS.get(agent_key, {}).get("label", agent_key)
        # The binding outlives the subprocess, so between respawns this is the
        # only place the chat's real interlocutor shows: reporting the LLM alone
        # would read as "you are back on Condor" when the next message is not.
        bound, _ = resolve_chat_binding(context.user_data)
        lines = ["No active session"]
        if bound:
            lines.append(f"Talking to: {bound.name or bound.slug}")
        lines.append(f"LLM: {llm_label}")
        lines.append("\nStart a session or adjust settings below.")
        # Said where the choice is made: "Start" boots whoever the chat is bound
        # to, so the way to start it as somebody else has to be visible here.
        lines.append('"Talk to" starts it as a specialist — or /agent <name>.')
        text = "\n".join(lines)
        keyboard = _no_session_keyboard()

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
