"""Agent chat handler -- /agent command, callback router, message handler."""

import logging
import shutil
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from condor.acp import ACP_COMMANDS
from condor.acp.pydantic_ai_client import is_pydantic_ai_model
from condor.runtime import (
    EventType,
    PromptRequest,
    SessionInfo,
    SessionKey,
    SessionSpec,
)
from condor.runtime import client as runtime
from handlers import clear_all_input_states
from utils.auth import restricted

from ._shared import (
    AGENT_OPTIONS,
    COMPACT_CONTEXT_TEMPLATE,
    COMPACT_PROMPT_AUTO,
    COMPACT_PROMPT_CUSTOM_TEMPLATE,
    DEFAULT_AGENT,
    bind_chat_to_agent,
    get_project_dir,
    resolve_chat_binding,
    selectable_agent_options,
    stale_binding_notice,
)
from .confirmation import resolve_confirmation
from .menu import show_agent_menu
from .stream import TelegramStreamer

# Imported for its import-time registration: it is what lets a finished
# background task render its continuation in the chat that asked (FEAT-034).
from . import wake as _wake  # noqa: F401  isort:skip

log = logging.getLogger(__name__)


# --- Session runtime adapters (Telegram surface) ---
#
# Session state lives in condor.runtime, outside the hot-reload blast radius.
# These are the only place this module turns a Telegram chat_id into a
# SessionKey. Everything below works with SessionInfo — the serializable view —
# never the live session object, so this surface behaves identically once the
# runtime moves out of process.


def _tg_key(chat_id: int) -> SessionKey:
    return SessionKey.telegram(chat_id)


def _tg_permission_callback(bot, chat_id: int, user_id: int):
    """Confirmation gate for this chat, delivered as an inline keyboard.

    The pending approval is registered centrally, so the same request can also
    be answered from the dashboard or over HTTP — whichever answers first wins.
    """
    from condor.runtime.confirmations import build_permission_callback

    from .confirmation import TelegramChannel

    return build_permission_callback(
        session_key=str(_tg_key(chat_id)),
        user_id=user_id,
        channels=[TelegramChannel(bot, chat_id)],
    )


async def get_session(chat_id: int) -> SessionInfo | None:
    """Serializable view of a chat's session, or None."""
    return await runtime.get_info(_tg_key(chat_id))


async def destroy_session(chat_id: int) -> bool:
    """Destroy a chat's session. Returns True if one existed."""
    return await runtime.destroy(_tg_key(chat_id))


async def _create_tg_session(
    chat_id: int,
    agent_key: str,
    user_id: int,
    permission_callback,
    user_data: dict | None,
    extra_context: str = "",
    agent_slug: str = "",
    conversation_id: str = "",
) -> SessionInfo:
    """Provision the session for a Telegram chat.

    An empty ``agent_key`` with ``agent_slug`` set lets the bound Agent's own
    configured model win; pass both to override it deliberately.

    An empty ``conversation_id`` starts a fresh conversation — which is what
    every call site here wants, since ``/agent → New session`` means a new
    chat. Passing one resumes that transcript; the key ``tg:{chat_id}`` is
    unchanged either way, so Telegram carries the conversation on the session
    record rather than in the key.
    """
    return await runtime.create_session(
        SessionSpec(
            key=str(_tg_key(chat_id)),
            agent_key=agent_key,
            user_id=user_id,
            chat_id=chat_id,
            platform="telegram",
            extra_context=extra_context,
            agent_slug=agent_slug,
            conversation_id=conversation_id,
        ),
        permission_callback=permission_callback,
        user_data=user_data,
    )


# Cache CLI availability checks so we only hit the filesystem once per key
_cli_available_cache: dict[str, bool] = {}

# Flags that make the next plain message mean something other than "talk to the
# agent". Tapping any button is a fresh intent, so they are all disarmed before
# the callback router dispatches: otherwise walking away from a prompt (e.g.
# "Enter model manually", then picking a different LLM) leaves the flag set and
# the user's next task is parsed as a model slug / URL / compact instruction.
# Handlers that genuinely want an armed mode set their own flag after this runs.
_ARMED_TEXT_INPUT_KEYS = (
    "_openrouter_typing_slug",
    "_custom_typing_url",
    "_custom_typing_key",
    "_custom_typing_search",
    "agent_compact_custom",
)


def _disarm_text_input(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Drop every armed "next message is the answer" mode."""
    for key in _ARMED_TEXT_INPUT_KEYS:
        context.user_data.pop(key, None)


def _is_agent_available(agent_key: str) -> bool:
    """Check if the agent backend is available.

    For ACP agents (claude-code, gemini): checks if the CLI binary is in PATH.
    For pydantic-ai agents (ollama:*, openai:*, etc.): always available
    (pydantic-ai handles connection errors at runtime).
    """
    # Pydantic-ai models don't need a CLI binary
    if is_pydantic_ai_model(agent_key):
        return True

    if agent_key in _cli_available_cache:
        return _cli_available_cache[agent_key]

    cmd = ACP_COMMANDS.get(agent_key, ACP_COMMANDS.get("claude-code", ""))
    # The command may have flags (e.g. "gemini --experimental-acp"), check the binary
    binary = cmd.split()[0] if cmd else ""
    available = shutil.which(binary) is not None
    _cli_available_cache[agent_key] = available

    if not available:
        log.warning("Agent CLI %r not found in PATH (agent_key=%s)", binary, agent_key)

    return available


def set_active_llm(context: ContextTypes.DEFAULT_TYPE, agent_key: str) -> None:
    """Set the chat's model and mirror it into the shared preference store.

    ``user_data["agent_llm"]`` lives only in the PTB pickle, which nothing
    outside the bot process can read. Mirroring it to config.yml is what lets
    a newly created Agent inherit the model the user is actually running,
    instead of the coordinator guessing one.
    """
    from condor.preferences import set_active_agent_key

    context.user_data["agent_llm"] = agent_key
    try:
        set_active_agent_key(context.user_data, agent_key)
    except Exception:
        log.debug("Could not mirror agent_llm to preferences", exc_info=True)


def _reclaim_default_agent(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Resolve the effective agent_key, reclaiming DEFAULT_AGENT after an auto-switch.

    A previous auto-switch (no installed CLI for the configured default) parks the
    user on whatever backend was available and flags it ``agent_llm_auto``. That was
    never the user's choice, so once DEFAULT_AGENT's CLI is back, restore it. An
    explicit pick via Change LLM clears the flag, so deliberate choices are never
    reverted. Used by both the /agent command and the always-on message handler so
    healing happens no matter how the user re-enters.
    """
    if not context.user_data.get("agent_llm"):
        set_active_llm(context, DEFAULT_AGENT)
    agent_key = context.user_data.get("agent_llm", DEFAULT_AGENT)
    if (
        context.user_data.get("agent_llm_auto")
        and agent_key != DEFAULT_AGENT
        and _is_agent_available(DEFAULT_AGENT)
    ):
        set_active_llm(context, DEFAULT_AGENT)
        context.user_data.pop("agent_llm_auto", None)
        agent_key = DEFAULT_AGENT
    # Backfill the shared mirror for users who picked their model before it
    # existed — otherwise agent creation sees no active model until they
    # happen to re-pick one. No-ops once the value already matches.
    set_active_llm(context, agent_key)
    return agent_key


@restricted
async def agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /agent command — manage agent settings and session."""
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await update.message.reply_text("The agent is only available in private chats.")
        return

    clear_all_input_states(context)

    agent_key = _reclaim_default_agent(context)

    # Warn if no agent CLI is available
    if not _is_agent_available(agent_key):
        # Sentinels are excluded: they open a picker rather than naming a
        # model, so auto-switching onto one would park the user on a key that
        # can't start a session.
        available = [k for k in selectable_agent_options() if _is_agent_available(k)]
        if not available:
            await update.message.reply_text(
                "No agent CLI found.\n\n"
                "Install one of:\n"
                "• claude-agent-acp (Claude Agent)\n"
                "• gemini (Gemini CLI)\n"
                "• npx @agentclientprotocol/codex-acp (ChatGPT Codex ACP bridge)\n\n"
                "Then restart the bot."
            )
            return
        # Auto-switch to an available one. Flag it as non-user so the reclaim
        # logic above can restore DEFAULT_AGENT once its CLI is installed.
        set_active_llm(context, available[0])
        context.user_data["agent_llm_auto"] = True

    await show_agent_menu(update, context)


@restricted
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop — interrupt the answer in flight, keep the session.

    This is the dashboard's Stop button, as a command: it aborts the running
    turn at the agent (ACP ``session/cancel``) and leaves the session and its
    context intact. Deliberately *not* what the menu's "End session" button
    does, which kills the subprocess and the conversation with it.

    A message queued behind the aborted turn still runs — stopping the answer
    you are watching is not the same as discarding what you asked next.
    """
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await update.message.reply_text("The agent is only available in private chats.")
        return

    chat_id = update.effective_chat.id
    session = await get_session(chat_id)

    if not session or not session.alive:
        await update.message.reply_text("No active session.")
        return

    if not session.is_busy:
        await update.message.reply_text("Nothing to stop — the agent is idle.")
        return

    # Awaits the agent's own cancel (bounded by TIMEOUTS.prompt_cancel), so by
    # the time this replies the generation has actually stopped. The streamed
    # message finalizes itself on the DONE/cancelled event.
    await runtime.abort(_tg_key(chat_id))
    await update.message.reply_text("⏹ Stopped. The session and its context are kept.")


@restricted
async def agent_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Route agent:* callbacks."""
    query = update.callback_query
    await query.answer()

    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await query.message.edit_text("Agent mode is only available in private chats.")
        return

    # A tap supersedes any half-finished typing prompt. Cleared before dispatch
    # so handlers that want an armed mode can re-arm it below.
    _disarm_text_input(context)

    data = query.data
    action = data.split(":", 1)[1] if ":" in data else data

    # Start a session. The "mode:" prefix is what buttons rendered before the
    # persona axis was deleted (FEAT-033) send; the value is ignored.
    if action == "start" or action.startswith("mode:"):
        await _handle_start(update, context)

    # Settings
    elif action == "settings":
        await _handle_settings(update, context)
    elif action.startswith("set_llm:"):
        llm_key = action.split(":", 1)[1]
        # OpenRouter sentinel -> open the model picker instead of setting directly
        if llm_key == "openrouter:":
            await _handle_openrouter_picker(update, context, page=0)
        # Custom sentinel -> open the saved-endpoints screen
        elif llm_key == "custom:":
            await _handle_custom_list(update, context)
        else:
            await _handle_set_llm(update, context, llm_key)
    elif action.startswith("or_page:"):
        page = int(action.split(":", 1)[1])
        await _handle_openrouter_picker(update, context, page=page)
    elif action.startswith("or_pick:"):
        idx = int(action.split(":", 1)[1])
        await _handle_openrouter_pick(update, context, idx)
    elif action == "or_type":
        await _handle_openrouter_type_prompt(update, context)
    elif action == "or_type_confirm":
        await _handle_openrouter_type_confirm(update, context)
    elif action == "or_type_cancel":
        await _handle_openrouter_type_cancel(update, context)
    elif action == "or_noop":
        pass  # page indicator button — do nothing

    # Custom OpenAI-compatible endpoints
    elif action == "cu_list":
        await _handle_custom_list(update, context)
    elif action == "cu_add":
        await _handle_custom_url_prompt(update, context)
    elif action == "cu_nokey":
        await _handle_custom_no_key(update, context)
    elif action == "cu_retry":
        await _handle_custom_retry(update, context)
    elif action == "cu_cancel":
        await _handle_custom_cancel(update, context)
    elif action == "cu_manage":
        await _handle_custom_manage(update, context)
    elif action == "cu_search":
        await _handle_custom_search_prompt(update, context)
    elif action == "cu_clear":
        await _handle_custom_clear_filter(update, context)
    elif action.startswith("cu_use:"):
        await _handle_custom_use(update, context, action.split(":", 1)[1])
    elif action.startswith("cu_page:"):
        await _handle_custom_page(update, context, int(action.split(":", 1)[1]))
    elif action.startswith("cu_pick:"):
        await _handle_custom_pick(update, context, action.split(":", 1)[1])
    elif action.startswith("cu_key:"):
        await _handle_custom_rekey(update, context, action.split(":", 1)[1])
    elif action.startswith("cu_delok:"):
        await _handle_custom_delete(update, context, action.split(":", 1)[1])
    elif action.startswith("cu_del:"):
        await _handle_custom_delete_confirm(update, context, action.split(":", 1)[1])
    elif action == "cu_noop":
        pass  # page indicator / section header — do nothing

    # Session management
    elif action == "stop":
        await _handle_stop(update, context)
    elif action == "cancel":
        await _handle_cancel(update, context)
    elif action == "close":
        await _handle_close(update, context)
    elif action == "menu":
        await show_agent_menu(update, context)
    elif action == "compact":
        await _handle_compact_menu(update, context)
    elif action == "compact_auto":
        await _handle_compact(update, context, custom_instructions=None)
    elif action == "compact_custom":
        await _handle_compact_custom_prompt(update, context)
    elif action == "new":
        await _handle_new_session(update, context)

    # Agent binding — who is on the other end of the chat
    elif action == "talk_to":
        await _handle_talk_to(update, context, page=0)
    elif action.startswith("talk_page:"):
        await _handle_talk_to(update, context, page=int(action.split(":", 1)[1]))
    elif action.startswith("talk_pick:"):
        await _handle_talk_pick(update, context, int(action.split(":", 1)[1]))
    elif action == "talk_noop":
        pass  # page indicator — do nothing

    # Trade confirmations. "Request expired" also covers "already answered
    # elsewhere" — resolve() is idempotent, so a dashboard click that beat this
    # tap is not an error.
    elif action.startswith("confirm_trade:"):
        request_id = action.split(":", 1)[1]
        resolved = await resolve_confirmation(
            request_id, approved=True, by_user_id=update.effective_user.id
        )
        text = "Approved." if resolved else "Request expired."
        await query.message.edit_text(text)
    elif action.startswith("reject_trade:"):
        request_id = action.split(":", 1)[1]
        resolved = await resolve_confirmation(
            request_id, approved=False, by_user_id=update.effective_user.id
        )
        text = "Rejected." if resolved else "Request expired."
        await query.message.edit_text(text)


async def _handle_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Start a fresh session for this chat, under whoever it is bound to.

    "New session" means a new conversation, not a new interlocutor: a chat bound
    to a specialist restarts as that specialist. Use "Talk to → Condor" to go
    back to the coordinator.
    """
    query = update.callback_query
    message = query.message if query else update.message
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    agent_key = context.user_data.get("agent_llm", DEFAULT_AGENT)
    llm_label = AGENT_OPTIONS.get(agent_key, {}).get("label", agent_key)

    agent, stale_slug = resolve_chat_binding(context.user_data)
    label = (agent.name or agent.slug) if agent else "Condor"

    status_text = (
        f"Starting {label} session..."
        if agent
        else f"Starting Condor session ({llm_label})..."
    )
    if stale_slug:
        status_text = f"{stale_binding_notice(stale_slug)}\n\n{status_text}"
    if query:
        await message.edit_text(status_text)
    else:
        message = await message.reply_text(status_text)

    # Destroy existing session
    await destroy_session(chat_id)

    try:
        bot = context.bot

        _perm_cb = _tg_permission_callback(bot, chat_id, user_id)

        await _create_tg_session(
            chat_id=chat_id,
            # A bound Agent's own configured model wins, exactly as it does when
            # the binding is first made in the "Talk to" picker.
            agent_key="" if agent else agent_key,
            user_id=user_id,
            permission_callback=_perm_cb,
            user_data=context.user_data,
            agent_slug=agent.slug if agent else "",
        )

        await message.edit_text(
            f"{label} is ready. Send a message to start chatting.\n\n"
            "Use /agent to see options or any other command to exit."
        )
    except Exception as e:
        log.exception("Failed to start agent session")
        await message.edit_text(f"Failed to start agent: {e}")


async def _handle_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show settings sub-menu with LLM picker."""
    from .menu import _settings_keyboard

    query = update.callback_query
    current_llm = context.user_data.get("agent_llm", DEFAULT_AGENT)
    await query.message.edit_text(
        "Select the LLM for new sessions:",
        reply_markup=_settings_keyboard(current_llm),
    )


async def _handle_set_llm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, llm_key: str
) -> None:
    """Update the preferred LLM."""
    query = update.callback_query
    if llm_key not in AGENT_OPTIONS:
        await query.message.edit_text("Unknown LLM option.")
        return

    set_active_llm(context, llm_key)
    context.user_data.pop("agent_llm_auto", None)  # explicit choice — don't auto-revert

    # Destroy existing session so the next interaction uses the new LLM
    chat_id = update.effective_chat.id
    await destroy_session(chat_id)

    label = AGENT_OPTIONS[llm_key]["label"]
    await query.message.edit_text(
        f"LLM set to {label}. New sessions will use this model.\n\n"
        "Use /agent to continue."
    )


async def _handle_openrouter_picker(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int
) -> None:
    """Show paginated OpenRouter model picker.

    Fetches the model list (cached for 1h), filters to tool-calling models, and
    stores the resolved list in user_data so or_pick:N can resolve the index.
    """
    from .menu import _openrouter_picker_keyboard
    from .openrouter_models import fetch_models

    query = update.callback_query

    # The catalog is public, so browse it without a key — same as the web picker
    # (condor/web/routes/chat_ws.py). The key is only needed to RUN a model; that
    # is enforced with a clear error at session start (pydantic_ai_client.py).
    if page == 0:
        await query.message.edit_text("Loading OpenRouter models...")

    try:
        models = await fetch_models()
    except Exception as e:
        log.exception("Failed to fetch OpenRouter models")
        await query.message.edit_text(f"Failed to fetch OpenRouter models: {e}")
        return

    if not models:
        await query.message.edit_text(
            "No OpenRouter models available. Check your network or try again later."
        )
        return

    # Cache for or_pick:N to resolve. Models list is sorted deterministically and
    # cached for an hour, so the index is stable across paging within a session.
    context.user_data["_openrouter_models"] = models

    current = context.user_data.get("agent_llm", "")
    current_slug = (
        current.split(":", 1)[1]
        if current.startswith("openrouter:") and current != "openrouter:"
        else None
    )

    keyboard = _openrouter_picker_keyboard(models, page=page, current_slug=current_slug)
    await query.message.edit_text(
        f"Select an OpenRouter model ({len(models)} with tool-calling support):",
        reply_markup=keyboard,
    )


async def _handle_openrouter_pick(
    update: Update, context: ContextTypes.DEFAULT_TYPE, idx: int
) -> None:
    """Resolve picker index → set agent_llm to 'openrouter:<slug>'."""
    query = update.callback_query
    models = context.user_data.get("_openrouter_models") or []
    if not models or idx < 0 or idx >= len(models):
        await query.message.edit_text(
            "Selection expired. Reopen the picker via Change LLM."
        )
        return

    model = models[idx]
    agent_key = f"openrouter:{model.slug}"
    set_active_llm(context, agent_key)
    context.user_data.pop("agent_llm_auto", None)  # explicit choice — don't auto-revert

    # Destroy existing session so the next interaction uses the new LLM
    await destroy_session(update.effective_chat.id)

    pricing = ""
    if model.prompt_price or model.completion_price:
        pricing = (
            f"\nPricing: ${model.prompt_price:.2f}/M input, "
            f"${model.completion_price:.2f}/M output"
        )

    await query.message.edit_text(
        f"LLM set to OpenRouter — {model.name}.\n"
        f"Slug: {model.slug}{pricing}\n\n"
        "New sessions will use this model. Use /agent to continue."
    )


async def _handle_openrouter_type_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Arm slug-input mode: next text message is parsed as an OpenRouter slug."""
    from .menu import _openrouter_input_keyboard

    query = update.callback_query
    context.user_data["_openrouter_typing_slug"] = True
    await query.message.edit_text(
        "Send the OpenRouter model slug as a message.\n"
        "Example: anthropic/claude-sonnet-4.5\n\n"
        "Send /cancel, or tap Cancel, to abort.",
        reply_markup=_openrouter_input_keyboard(),
    )


async def _resolve_openrouter_typed_slug(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    """Validate a typed slug; on match, prompt confirmation."""
    from .menu import _openrouter_input_keyboard
    from .openrouter_models import fetch_models, find_model_by_slug

    slug = text.strip()
    if slug.lower() in ("/cancel", "cancel"):
        await update.message.reply_text("Cancelled. Use /agent to continue.")
        return

    try:
        models = await fetch_models()
    except Exception as e:
        log.exception("Failed to fetch OpenRouter models")
        await update.message.reply_text(f"Failed to fetch OpenRouter models: {e}")
        return

    model = find_model_by_slug(models, slug)
    if not model:
        # Re-arm so the user can retype without hunting for the button again —
        # with a Cancel button, so a mistyped slug can't trap them in a loop of
        # "that isn't a model either" with no visible way out.
        context.user_data["_openrouter_typing_slug"] = True
        await update.message.reply_text(
            f"No tool-calling OpenRouter model matches '{slug}'.\n"
            "The slug must be exact (e.g. anthropic/claude-sonnet-4.5).\n"
            "Try again, send /cancel, or tap Cancel.",
            reply_markup=_openrouter_input_keyboard(),
        )
        return

    context.user_data["_openrouter_typed_slug"] = model.slug

    pricing = ""
    if model.prompt_price or model.completion_price:
        pricing = (
            f"\nPricing: ${model.prompt_price:.2f}/M input, "
            f"${model.completion_price:.2f}/M output"
        )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Use this model", callback_data="agent:or_type_confirm"
                ),
                InlineKeyboardButton("Cancel", callback_data="agent:or_type_cancel"),
            ]
        ]
    )
    await update.message.reply_text(
        f"Use OpenRouter — {model.name}?\nSlug: {model.slug}{pricing}",
        reply_markup=keyboard,
    )


async def _handle_openrouter_type_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Apply the typed slug as the active agent_llm."""
    query = update.callback_query
    slug = context.user_data.pop("_openrouter_typed_slug", None)
    if not slug:
        await query.message.edit_text(
            "Selection expired. Reopen the picker via Change LLM."
        )
        return

    set_active_llm(context, f"openrouter:{slug}")
    context.user_data.pop("agent_llm_auto", None)  # explicit choice — don't auto-revert

    # Destroy existing session so the next interaction uses the new LLM
    await destroy_session(update.effective_chat.id)

    await query.message.edit_text(
        f"LLM set to OpenRouter — {slug}.\n\n"
        "New sessions will use this model. Use /agent to continue."
    )


async def _handle_openrouter_type_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Discard the typed slug without changing the active LLM."""
    query = update.callback_query
    context.user_data.pop("_openrouter_typed_slug", None)
    await query.message.edit_text("Cancelled. Use /agent to continue.")


# -- Custom OpenAI-compatible endpoints --
# Flow: "Custom" sentinel → endpoint list → (Add: base URL → API key →
# validate by fetching {base_url}/models) → paginated model picker →
# agent_llm = "custom@<endpoint>:<model-id>".
#
# Endpoints are stored through condor/preferences.py, which syncs them to
# config.yml — so the web dashboard reads and writes the same records rather
# than each surface keeping its own copy.

# How long a fetched model list stays usable before we re-hit /models.
CUSTOM_MODEL_CACHE_TTL = 600

_CUSTOM_INPUT_KEYS = (
    "_custom_typing_url",
    "_custom_typing_key",
    "_custom_typing_search",
    "_custom_pending_url",
    "_custom_pending_name",
    "_custom_rekey_provider",
)


def _current_custom_model(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Model id of the current agent_llm when it points at a custom endpoint."""
    from condor.preferences import parse_custom_agent_key

    _, model_id = parse_custom_agent_key(context.user_data.get("agent_llm", ""))
    return model_id or None


def _clear_custom_input(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Disarm any half-finished typing step in this flow."""
    for key in _CUSTOM_INPUT_KEYS:
        context.user_data.pop(key, None)


async def _show(
    update: Update,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
    placeholder=None,
):
    """Render a screen, whichever way the user got here.

    Callback taps edit the message in place; typed input has no message of
    ours to edit, so it gets a fresh one (or reuses an explicit placeholder).
    """
    if placeholder is not None:
        await placeholder.edit_text(text, reply_markup=keyboard)
        return placeholder
    query = update.callback_query
    if query is not None:
        await query.message.edit_text(text, reply_markup=keyboard)
        return query.message
    return await update.effective_chat.send_message(text, reply_markup=keyboard)


async def _handle_custom_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    notice: str = "",
    placeholder=None,
) -> None:
    """Landing screen — the user's saved endpoints."""
    from condor.preferences import get_custom_providers

    from .menu import _custom_endpoints_keyboard

    _clear_custom_input(context)
    providers = get_custom_providers(context.user_data)

    if providers:
        body = (
            "Custom OpenAI-compatible endpoints\n\n"
            "Pick an endpoint to choose a model from it."
        )
    else:
        body = (
            "Custom OpenAI-compatible endpoints\n\n"
            "Connect any OpenAI-compatible API — Venice AI, Together, "
            "Fireworks, or your own vLLM / LM Studio server."
        )

    text = f"{notice}\n\n{body}" if notice else body
    await _show(
        update,
        text,
        _custom_endpoints_keyboard(providers, context.user_data.get("agent_llm", "")),
        placeholder=placeholder,
    )


def _provider_named(context: ContextTypes.DEFAULT_TYPE, name: str) -> dict | None:
    """Resolve the endpoint a `cu_*` button was rendered for.

    Buttons carry the sanitized nickname rather than a list position: the saved
    list is shared state (the web dashboard writes it too), so an index taken at
    render time can point at a different endpoint by the time it's tapped —
    which for delete and re-key means destroying the wrong credential.
    """
    from condor.preferences import find_custom_provider

    return find_custom_provider(context.user_data, name)


# -- Adding an endpoint --


async def _handle_custom_url_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Arm URL-input mode: the next text message is the endpoint base URL."""
    from .menu import _custom_input_keyboard

    _clear_custom_input(context)
    context.user_data["_custom_typing_url"] = True
    await _show(
        update,
        "Send the base URL of the OpenAI-compatible API as a message.\n\n"
        "Examples:\n"
        "  https://api.venice.ai/api/v1\n"
        "  http://localhost:8000/v1",
        _custom_input_keyboard(),
    )


async def _resolve_custom_url(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    """Validate the typed base URL, then prompt for the API key."""
    from condor.preferences import suggest_provider_name, unique_provider_name

    from .custom_models import normalize_base_url
    from .menu import _custom_input_keyboard, _custom_key_prompt_keyboard

    raw = text.strip()
    if raw.lower() in ("/cancel", "cancel"):
        await _handle_custom_list(update, context, notice="Cancelled.")
        return

    try:
        base_url = normalize_base_url(raw)
    except ValueError as e:
        # Re-arm so the user can retype — with a Cancel button, so a failed
        # parse can't trap them in a loop of "that isn't a URL either".
        context.user_data["_custom_typing_url"] = True
        await _show(
            update, f"{e}\n\nSend another URL, or cancel.", _custom_input_keyboard()
        )
        return

    name = unique_provider_name(context.user_data, suggest_provider_name(base_url))
    context.user_data["_custom_pending_url"] = base_url
    context.user_data["_custom_pending_name"] = name
    context.user_data["_custom_typing_key"] = True

    await _show(
        update,
        f"Endpoint: {base_url}\nIt will be saved as '{name}'.\n\n"
        "Now send the API key as a message. Your message is deleted from the "
        "chat as soon as it's read; the key is stored on this bot's host so it "
        "can sign requests.",
        _custom_key_prompt_keyboard(),
    )


async def _handle_custom_no_key(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """'No API key needed' — validate the pending endpoint unauthenticated."""
    await _validate_and_save(update, context, api_key="")


async def _resolve_custom_key(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    """Consume the typed API key and validate the endpoint with it."""
    key = text.strip()
    if key.lower() in ("/cancel", "cancel"):
        await _handle_custom_list(update, context, notice="Cancelled.")
        return

    # Best effort: get the secret out of the visible chat history
    try:
        await update.message.delete()
    except Exception:
        log.debug(
            "Could not delete API key message in chat %s", update.effective_chat.id
        )

    await _validate_and_save(update, context, api_key=key)


async def _validate_and_save(
    update: Update, context: ContextTypes.DEFAULT_TYPE, api_key: str
) -> None:
    """Fetch /models to prove the endpoint works, then persist it."""
    from condor.preferences import save_custom_provider

    from .custom_models import CustomProviderError, fetch_models
    from .menu import _custom_error_keyboard, _custom_picker_keyboard

    base_url = context.user_data.get("_custom_pending_url")
    name = context.user_data.get("_custom_pending_name")
    if not base_url or not name:
        await _handle_custom_list(
            update, context, notice="That setup step expired — start again."
        )
        return

    placeholder = await _show(update, f"Checking {base_url}...")

    try:
        resolved_url, models = await fetch_models(base_url, api_key)
    except CustomProviderError as e:
        await placeholder.edit_text(
            f"Couldn't use that endpoint: {e}",
            reply_markup=_custom_error_keyboard("agent:cu_retry"),
        )
        return

    try:
        saved = save_custom_provider(context.user_data, name, resolved_url, api_key)
    except ValueError as e:
        await placeholder.edit_text(
            str(e), reply_markup=_custom_error_keyboard("agent:cu_retry")
        )
        return

    _clear_custom_input(context)
    _cache_models(context, saved["name"], models)

    await placeholder.edit_text(
        f"'{saved['name']}' saved — {len(models)} chat models available at "
        f"{resolved_url}.\n\nSelect the model for new sessions:",
        reply_markup=_custom_picker_keyboard(
            models, page=0, current_id=_current_custom_model(context)
        ),
    )


async def _handle_custom_retry(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Re-run validation for the endpoint the user was just adding."""
    if not context.user_data.get("_custom_pending_url"):
        await _handle_custom_list(
            update, context, notice="That setup step expired — start again."
        )
        return
    context.user_data["_custom_typing_key"] = True
    from .menu import _custom_key_prompt_keyboard

    await _show(
        update,
        f"Endpoint: {context.user_data['_custom_pending_url']}\n\n"
        "Send the API key again, or continue without one.",
        _custom_key_prompt_keyboard(),
    )


# -- Using a saved endpoint --


def _cache_models(
    context: ContextTypes.DEFAULT_TYPE, provider_name: str, models: list[str]
) -> None:
    context.user_data["_custom_models"] = {
        "provider": provider_name,
        "models": models,
        "ts": time.time(),
    }
    context.user_data.pop("_custom_filter", None)


def _cached_models(
    context: ContextTypes.DEFAULT_TYPE, provider_name: str
) -> list[str] | None:
    """Model list for `provider_name` if we fetched it recently enough."""
    cache = context.user_data.get("_custom_models") or {}
    if cache.get("provider") != provider_name:
        return None
    if time.time() - cache.get("ts", 0) > CUSTOM_MODEL_CACHE_TTL:
        return None
    return cache.get("models") or None


async def _handle_custom_use(
    update: Update, context: ContextTypes.DEFAULT_TYPE, provider_name: str
) -> None:
    """Open the model picker for a saved endpoint, refetching if stale."""
    from condor.preferences import sanitize_provider_name

    from .custom_models import CustomProviderError, fetch_models
    from .menu import _custom_error_keyboard, _custom_picker_keyboard

    provider = _provider_named(context, provider_name)
    if provider is None:
        await _handle_custom_list(update, context, notice="That endpoint is gone.")
        return

    name = provider["name"]
    key = sanitize_provider_name(name)
    models = _cached_models(context, name)

    if models is None:
        placeholder = await _show(update, f"Fetching models from {name}...")
        try:
            resolved_url, models = await fetch_models(
                provider["base_url"], provider.get("api_key", "")
            )
        except CustomProviderError as e:
            await placeholder.edit_text(
                f"'{name}' isn't responding: {e}",
                reply_markup=_custom_error_keyboard(
                    f"agent:cu_use:{key}",
                    secondary=("Change API key", f"agent:cu_key:{key}"),
                ),
            )
            return
        _cache_models(context, name, models)
        if resolved_url != provider["base_url"]:
            from condor.preferences import save_custom_provider

            save_custom_provider(
                context.user_data, name, resolved_url, provider.get("api_key", "")
            )
    else:
        placeholder = None

    await _show(
        update,
        f"{name} — {len(models)} chat models.\nSelect the model for new sessions:",
        _custom_picker_keyboard(
            models, page=0, current_id=_current_custom_model(context)
        ),
        placeholder=placeholder,
    )


async def _handle_custom_page(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int
) -> None:
    """Page through the cached model list."""
    from .menu import _custom_picker_keyboard

    cache = context.user_data.get("_custom_models") or {}
    models = cache.get("models") or []
    if not models:
        await _handle_custom_list(update, context, notice="That model list expired.")
        return

    query = context.user_data.get("_custom_filter", "")
    await _show(
        update,
        f"{cache.get('provider', 'Endpoint')} — {len(models)} chat models."
        + (f"\nFilter: '{query}'" if query else "")
        + "\nSelect the model for new sessions:",
        _custom_picker_keyboard(
            models, page=page, current_id=_current_custom_model(context), query=query
        ),
    )


async def _handle_custom_search_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Arm search-input mode for the current model list."""
    from .menu import _custom_input_keyboard

    if not (context.user_data.get("_custom_models") or {}).get("models"):
        await _handle_custom_list(update, context, notice="That model list expired.")
        return
    context.user_data["_custom_typing_search"] = True
    await _show(
        update,
        "Send a search term to filter the model list (e.g. 'llama' or '70b').",
        _custom_input_keyboard(),
    )


async def _resolve_custom_search(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    """Apply a typed search term and re-render page 1."""
    term = text.strip()
    if term.lower() in ("/cancel", "cancel"):
        term = ""
    context.user_data["_custom_filter"] = term
    await _handle_custom_page(update, context, page=0)


async def _handle_custom_clear_filter(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    context.user_data.pop("_custom_filter", None)
    await _handle_custom_page(update, context, page=0)


async def _handle_custom_pick(
    update: Update, context: ContextTypes.DEFAULT_TYPE, token: str
) -> None:
    """Resolve a picker token → set agent_llm to custom@<endpoint>:<model-id>."""
    from condor.preferences import build_custom_agent_key

    from .custom_models import find_by_token

    cache = context.user_data.get("_custom_models") or {}
    models = cache.get("models") or []
    provider_name = cache.get("provider")
    model_id = find_by_token(models, token) if models else None

    if not model_id or not provider_name:
        await _handle_custom_list(
            update, context, notice="That selection expired — pick again."
        )
        return

    set_active_llm(context, build_custom_agent_key(provider_name, model_id))
    context.user_data.pop("agent_llm_auto", None)  # explicit choice — don't auto-revert

    # Destroy existing session so the next interaction uses the new LLM
    await destroy_session(update.effective_chat.id)

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Change model", callback_data="agent:cu_page:0")],
            [InlineKeyboardButton("Back to agent", callback_data="agent:menu")],
        ]
    )
    await _show(
        update,
        f"LLM set to {model_id} via '{provider_name}'.\n\n"
        "New sessions will use this model.",
        keyboard,
    )


# -- Managing saved endpoints --


async def _handle_custom_manage(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """List endpoints with per-endpoint maintenance actions."""
    from condor.preferences import get_custom_providers

    from .menu import _custom_manage_keyboard

    _clear_custom_input(context)
    providers = get_custom_providers(context.user_data)
    if not providers:
        await _handle_custom_list(update, context, notice="No saved endpoints.")
        return

    lines = [
        f"{p['name']} — {p['base_url']} "
        f"({'API key saved' if p.get('api_key') else 'no API key'})"
        for p in providers
    ]
    await _show(
        update,
        "Manage endpoints\n\n" + "\n".join(lines),
        _custom_manage_keyboard(providers),
    )


async def _handle_custom_rekey(
    update: Update, context: ContextTypes.DEFAULT_TYPE, provider_name: str
) -> None:
    """Replace the API key on a saved endpoint without re-entering its URL."""
    from .menu import _custom_key_prompt_keyboard

    provider = _provider_named(context, provider_name)
    if provider is None:
        await _handle_custom_list(update, context, notice="That endpoint is gone.")
        return

    _clear_custom_input(context)
    context.user_data["_custom_pending_url"] = provider["base_url"]
    context.user_data["_custom_pending_name"] = provider["name"]
    context.user_data["_custom_rekey_provider"] = provider["name"]
    context.user_data["_custom_typing_key"] = True
    context.user_data.pop("_custom_models", None)  # force a refetch with the new key

    await _show(
        update,
        f"Send the new API key for '{provider['name']}' ({provider['base_url']}).\n\n"
        "Your message is deleted as soon as it's read.",
        _custom_key_prompt_keyboard(),
    )


async def _handle_custom_delete_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE, provider_name: str
) -> None:
    from .menu import _custom_delete_keyboard

    provider = _provider_named(context, provider_name)
    if provider is None:
        await _handle_custom_list(update, context, notice="That endpoint is gone.")
        return

    await _show(
        update,
        f"Forget '{provider['name']}' ({provider['base_url']})?\n\n"
        "The saved URL and API key are deleted from this bot.",
        _custom_delete_keyboard(provider["name"]),
    )


async def _handle_custom_delete(
    update: Update, context: ContextTypes.DEFAULT_TYPE, provider_name: str
) -> None:
    """Forget an endpoint, clearing agent_llm if it pointed at that endpoint."""
    from condor.preferences import parse_custom_agent_key, remove_custom_provider

    provider = _provider_named(context, provider_name)
    if provider is None:
        await _handle_custom_list(update, context, notice="That endpoint is gone.")
        return

    name = provider["name"]
    remove_custom_provider(context.user_data, name)
    context.user_data.pop("_custom_models", None)

    # Don't leave agent_llm pointing at an endpoint that no longer resolves
    active_provider, _ = parse_custom_agent_key(context.user_data.get("agent_llm", ""))
    notice = f"Forgot '{name}'."
    if active_provider == name:
        set_active_llm(context, DEFAULT_AGENT)
        context.user_data.pop("agent_llm_auto", None)
        await destroy_session(update.effective_chat.id)
        notice += f" Active LLM reset to {AGENT_OPTIONS[DEFAULT_AGENT]['label']}."

    await _handle_custom_list(update, context, notice=notice)


async def _handle_custom_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Cancel button on any armed input step."""
    await _handle_custom_list(update, context, notice="Cancelled.")


async def _handle_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """End the active session — subprocess and conversation both go.

    Not to be confused with /stop, which only interrupts the turn in flight.
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    destroyed = await destroy_session(chat_id)

    if destroyed:
        await query.message.edit_text("Agent session ended.")
    else:
        await query.message.edit_text("No active session.")


async def _handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop generating — the /stop command as a button on the answer itself.

    Aborts the turn in flight (ACP ``session/cancel``) and leaves the session
    standing, unlike "End session" above. Nothing is written back here: the
    button lives on the message the streamer is still editing, so the reply
    belongs to `TelegramStreamer.finalize()`, which marks the partial answer
    stopped and drops the button with it.
    """
    await runtime.abort(_tg_key(update.effective_chat.id))


async def _handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Close the agent menu (keep session alive if running)."""
    query = update.callback_query

    await query.message.delete()


async def _handle_compact_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show compact options sub-menu."""
    from .menu import _compact_menu_keyboard

    query = update.callback_query
    chat_id = update.effective_chat.id
    session = await get_session(chat_id)

    if not session or not session.alive:
        await query.message.edit_text("No active session to compact.")
        return

    await query.message.edit_text(
        "How would you like to compact context?\n\n"
        "Auto - summarize everything\n"
        "Custom - specify what to keep",
        reply_markup=_compact_menu_keyboard(),
    )


def _edit_render(message):
    """Render every step by editing one message in place (callback surface)."""

    async def render(text: str) -> None:
        await message.edit_text(text)

    return render


def _reply_render(message):
    """Reply once, then edit that reply in place (typed-message surface)."""
    placeholder = None

    async def render(text: str) -> None:
        nonlocal placeholder
        if placeholder is None:
            placeholder = await message.reply_text(text)
        else:
            await placeholder.edit_text(text)

    return render


async def _compact(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    instructions: str | None,
    render,
    keep_custom_flag_on_busy: bool = False,
) -> None:
    """Summarize context → destroy session → recreate with the summary reinjected.

    The single implementation behind both compact surfaces: the menu button
    (which edits its own callback message) and the typed custom instructions
    (which replies, then edits that reply). ``render`` is what abstracts the
    two apart — everything else about the sequence is identical, and keeping it
    identical is the point: a fix here can no longer land in only one copy.
    """
    chat_id = update.effective_chat.id
    session = await get_session(chat_id)

    if not session or not session.alive:
        await render("No active session to compact.")
        return

    if session.is_busy:
        await render("Agent is busy. Wait for it to finish first.")
        if keep_custom_flag_on_busy:
            # Stay armed so the user can resend the same instructions.
            context.user_data["agent_compact_custom"] = True
        return

    await render("Compacting context...")

    if instructions:
        prompt = COMPACT_PROMPT_CUSTOM_TEMPLATE.format(instructions=instructions)
    else:
        prompt = COMPACT_PROMPT_AUTO

    try:
        summary = await runtime.prompt_once(_tg_key(chat_id), prompt)
    except Exception as e:
        log.exception("Failed to get compact summary")
        await render(f"Compact failed: {e}")
        return

    if not summary or not summary.strip():
        await render("Agent returned empty summary. Compact aborted.")
        return

    agent_key = session.agent_key
    # Compact is a respawn like any other, so it has to carry the identity over:
    # keeping only the model would summarize the specialist's context and then
    # hand it to the coordinator.
    agent_slug = session.agent_slug
    await destroy_session(chat_id)

    try:
        user_id = update.effective_user.id
        bot = context.bot

        _perm_cb = _tg_permission_callback(bot, chat_id, user_id)

        await _create_tg_session(
            chat_id=chat_id,
            agent_key=agent_key,
            user_id=user_id,
            permission_callback=_perm_cb,
            user_data=context.user_data,
            agent_slug=agent_slug,
        )

        compact_context = COMPACT_CONTEXT_TEMPLATE.format(summary=summary)
        await runtime.prompt_once(_tg_key(chat_id), compact_context)

    except Exception as e:
        log.exception("Failed to recreate session after compact")
        await render(f"Compact failed during session reset: {e}")
        return

    word_count = len(summary.split())
    await render(
        f"Context compacted ({word_count} words carried over).\n\n"
        "Send a message to continue chatting."
    )


async def _handle_compact(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    custom_instructions: str | None = None,
) -> None:
    """Compact: summarize context → destroy session → recreate with summary."""
    await _compact(
        update,
        context,
        instructions=custom_instructions,
        render=_edit_render(update.callback_query.message),
    )


async def _handle_compact_custom_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Prompt user to type custom compact instructions."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = await get_session(chat_id)

    if not session or not session.alive:
        await query.message.edit_text("No active session to compact.")
        return

    context.user_data["agent_compact_custom"] = True
    await query.message.edit_text(
        "What should I keep in the summary?\n\n"
        'Type your instructions (e.g. "keep the portfolio analysis and SOL trade setup"):'
    )


async def _handle_new_session(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Destroy the current session and start a fresh one."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = await get_session(chat_id)

    if not session or not session.alive:
        await query.message.edit_text("No active session.")
        return

    await _handle_start(update, context)


async def _handle_talk_to(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
) -> None:
    """Show the picker of domain Agents this chat can talk to."""
    from condor.agents.agent import AgentStore

    from .menu import _talk_to_keyboard

    query = update.callback_query
    session = await get_session(update.effective_chat.id)
    # Between respawns there is no session, but the chat is still bound -- tick
    # what it will come back as, not what happens to be running.
    bound, _ = resolve_chat_binding(context.user_data, drop_stale=False)
    current_slug = session.agent_slug if session else (bound.slug if bound else "")
    # The coordinator is the keyboard's own first row, so listing it here too
    # would offer Condor twice (FEAT-033 put it in the registry).
    agents = AgentStore().list_specialists()

    if not agents:
        await query.message.edit_text(
            "No agents defined yet. Create one with /agent → Condor, or from the "
            "dashboard's Agents page.",
            reply_markup=_talk_to_keyboard([], 0, current_slug),
        )
        return

    # Indices are resolved against this snapshot, so the pick handler must
    # re-read the same list (AgentStore.list_specialists is directory order).
    await query.message.edit_text(
        "Who do you want to talk to?\n\n"
        "A domain Agent answers with its own identity, tools and memory. "
        "Condor is the general coordinator.",
        reply_markup=_talk_to_keyboard(agents, page, current_slug),
    )


async def _handle_talk_pick(
    update: Update, context: ContextTypes.DEFAULT_TYPE, index: int
) -> None:
    """Bind (or unbind) the chat's session to a domain Agent.

    ACP cannot hot-swap an identity, so this reaps the subprocess and spawns a
    new one under the same key. The conversation does not survive; the user is
    told so rather than silently losing it.
    """
    from condor.agents.agent import AgentStore

    query = update.callback_query
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    agent_slug = ""
    label = "Condor"
    if index >= 0:
        # Same list the keyboard was built from — the indices in callback_data
        # are positions in it, so the two must not diverge.
        agents = AgentStore().list_specialists()
        if index >= len(agents):
            await query.message.edit_text("That agent no longer exists.")
            return
        agent_slug = agents[index].slug
        label = agents[index].name or agent_slug

    agent_key = _reclaim_default_agent(context)
    bot = context.bot

    _perm_cb = _tg_permission_callback(bot, chat_id, user_id)

    await query.message.edit_text(f"Switching to {label}...")
    await destroy_session(chat_id)

    try:
        await _create_tg_session(
            chat_id=chat_id,
            agent_key="" if agent_slug else agent_key,
            user_id=user_id,
            permission_callback=_perm_cb,
            user_data=context.user_data,
            agent_slug=agent_slug,
        )
    except Exception as e:
        log.exception("Failed to bind session to agent %r", agent_slug)
        await query.message.edit_text(f"Could not switch to {label}: {e}")
        return

    # Only once the session actually stands up: a chat must never be left
    # pointing at an agent it could not spawn.
    bind_chat_to_agent(context.user_data, agent_slug)

    await query.message.edit_text(
        f"Now talking to {label}. Previous conversation was not carried over.\n\n"
        "Send a message to start."
    )


async def _do_compact_from_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, instructions: str
) -> None:
    """Execute custom compact from user's text input."""
    await _compact(
        update,
        context,
        instructions=instructions,
        render=_reply_render(update.message),
        keep_custom_flag_on_busy=True,
    )


async def agent_voice_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle voice messages — transcribe and forward as text to the always-on agent."""
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        return

    # Auth check — only approved users
    from config_manager import UserRole, get_config_manager

    user_id = update.effective_user.id
    cm = get_config_manager()
    role = cm.get_user_role(user_id)
    if role not in (UserRole.ADMIN, UserRole.USER):
        return

    # Skip if no agent CLI available
    agent_key = context.user_data.get("agent_llm", DEFAULT_AGENT)
    if not await get_session(update.effective_chat.id) and not _is_agent_available(
        agent_key
    ):
        return

    voice = update.message.voice
    if not voice:
        return

    # Download the voice file
    status_msg = await update.message.reply_text("🎙 Transcribing voice...")
    try:
        tg_file = await voice.get_file()
        file_bytes = await tg_file.download_as_bytearray()

        # Resolve user voice preferences (language, model)
        from condor.preferences import get_voice_prefs

        voice_prefs = get_voice_prefs(context.user_data)
        voice_lang = voice_prefs.get("language")  # None = auto-detect
        voice_model = voice_prefs.get("whisper_model", "base")

        from utils.transcribe import transcribe_voice

        text = await transcribe_voice(
            bytes(file_bytes), language=voice_lang, model_size=voice_model
        )
    except Exception as e:
        log.exception("Voice transcription failed")
        await status_msg.edit_text(f"Transcription failed: {e}")
        return

    if not text or not text.strip():
        await status_msg.edit_text(
            "Could not transcribe any speech from the voice message."
        )
        return

    # Show the transcribed text
    from utils.telegram_formatters import escape_markdown_v2

    escaped = escape_markdown_v2(text)
    await status_msg.edit_text(
        f"🎙 _{escaped}_\n\nThinking\\.\\.\\.", parse_mode="MarkdownV2"
    )

    # Store the status message so agent_message_handler reuses it as placeholder
    context.chat_data["_voice_placeholder"] = status_msg
    context.chat_data["_voice_transcription"] = text

    # Forward to agent handler — pass transcribed text via chat_data
    # (Message.text is read-only in python-telegram-bot)
    await agent_message_handler(update, context)


async def agent_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle text messages — always-on agent fallback.

    This is called for any text that doesn't match a specific handler state.
    Auto-creates an agent session with the user's preferred LLM if none exists.
    """
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        return

    # Auth check — only approved users can use the agent
    from config_manager import UserRole, get_config_manager

    user_id = update.effective_user.id
    cm = get_config_manager()
    role = cm.get_user_role(user_id)
    if role not in (UserRole.ADMIN, UserRole.USER):
        return

    chat_id = update.effective_chat.id

    # Consume the voice hand-off exactly once, before any early return below, so
    # the transcript survives to build the streamer prefix and neither key leaks
    # into the next message's turn.
    voice_transcription = context.chat_data.pop("_voice_transcription", None)
    voice_placeholder = context.chat_data.pop("_voice_placeholder", None)
    text = voice_transcription or update.message.text

    if not text:
        return

    # "-" resets the ACP session: destroy current and let the next block auto-create a new one
    if text.strip() == "-":
        session = await get_session(chat_id)
        if session:
            await destroy_session(chat_id)
        await update.message.reply_text("Session reset. Send a message to start fresh.")
        return

    # Handle custom compact input
    if context.user_data.pop("agent_compact_custom", None):
        await _do_compact_from_message(update, context, text)
        return

    # Handle typed OpenRouter slug input
    if context.user_data.pop("_openrouter_typing_slug", None):
        await _resolve_openrouter_typed_slug(update, context, text)
        return

    # Handle custom endpoint input (base URL, then API key) and model search
    if context.user_data.pop("_custom_typing_url", None):
        await _resolve_custom_url(update, context, text)
        return
    if context.user_data.pop("_custom_typing_key", None):
        await _resolve_custom_key(update, context, text)
        return
    if context.user_data.pop("_custom_typing_search", None):
        await _resolve_custom_search(update, context, text)
        return

    session = await get_session(chat_id)

    # Auto-create session if none exists (always-on agent)
    if not session or not session.alive:
        # Reclaim the configured default after an auto-switch — same healing the
        # /agent command does, so users who only ever type messages benefit too.
        agent_key = _reclaim_default_agent(context)

        # The subprocess is gone but the chat's choice is not: respawn under the
        # Agent it was bound to, or this silently answers as the coordinator --
        # other identity, other tools, other server, other memory (CORR-090).
        agent, stale_slug = resolve_chat_binding(context.user_data)
        if stale_slug:
            await update.message.reply_text(stale_binding_notice(stale_slug))

        # Check if the CLI binary is installed before attempting to spawn. A
        # bound Agent runs on its own configured model, so that is the one whose
        # CLI has to be there.
        probe_key = (agent.agent_key or agent_key) if agent else agent_key
        if not _is_agent_available(probe_key):
            log.debug("Agent CLI for %s not found, skipping auto-create", probe_key)
            return

        try:
            bot = context.bot

            _perm_cb = _tg_permission_callback(bot, chat_id, user_id)

            session = await _create_tg_session(
                chat_id=chat_id,
                agent_key="" if agent else agent_key,
                user_id=user_id,
                permission_callback=_perm_cb,
                user_data=context.user_data,
                agent_slug=agent.slug if agent else "",
            )

        except Exception as e:
            log.exception("Failed to create agent session")
            await update.message.reply_text(f"Failed to start agent: {e}")
            return

    # Busy is an acknowledgement, not a refusal. The message really is queued:
    # `on_busy="queue"` below lets it block on the session lock, which is FIFO,
    # so several messages sent mid-answer are answered in the order they were
    # sent. Telegram has no live composer, so silently killing a running answer
    # because someone sent a second thought would be hostile — the dashboard
    # steers, this waits, and /stop is how you interrupt on purpose.
    #
    # The acknowledgement is not sent from here any more: the QUEUED event the
    # runtime emits renders inside this turn's own placeholder, which costs one
    # message instead of two and cannot miss the race where `is_busy` was read
    # from a snapshot taken just before the turn ahead finished.

    # Keep the spoken question at the head of the streamed answer: the streamer
    # edits the very placeholder that was showing the transcript, so without the
    # prefix the user's words would be overwritten by the reply.
    prefix = f"🎙 {voice_transcription}" if voice_transcription else ""

    # Send or reuse placeholder message
    if voice_placeholder:
        placeholder = voice_placeholder
    else:
        placeholder = await update.message.reply_text("Thinking...")

    streamer = TelegramStreamer(
        bot=context.bot,
        chat_id=chat_id,
        message_id=placeholder.message_id,
        prefix=prefix,
    )
    edit_task = streamer.start_edit_loop()

    last_event = None
    try:
        async for event in runtime.prompt(
            _tg_key(chat_id), PromptRequest(text=text), on_busy="queue"
        ):
            await streamer.process_event(event)
            last_event = event
    except Exception as e:
        log.exception("Agent prompt error")
        await streamer.finalize()
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=placeholder.message_id,
            text=f"Agent error: {e}",
        )
        await destroy_session(chat_id)
        return

    await streamer.finalize()

    # Detect subprocess death mid-stream
    stop_reason = (
        last_event.stop_reason
        if last_event is not None and last_event.type == EventType.DONE
        else ""
    )
    if stop_reason == "disconnected":
        await destroy_session(chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text="Agent session disconnected. Send a message to start a new session.",
        )
    elif stop_reason == "timeout":
        await destroy_session(chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text="Agent timed out (took too long). Send a message to start a new session.",
        )
