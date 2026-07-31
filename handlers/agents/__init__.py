"""Agent chat handler -- /agent command, callback router, message handler."""

import logging
import shutil
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from condor.acp import ACP_COMMANDS, PromptDone
from condor.acp.pydantic_ai_client import is_pydantic_ai_model
from handlers import clear_all_input_states
from utils.auth import restricted

from ._shared import (
    AGENT_MODES,
    AGENT_OPTIONS,
    COMPACT_CONTEXT_TEMPLATE,
    COMPACT_PROMPT_AUTO,
    COMPACT_PROMPT_CUSTOM_TEMPLATE,
    DEFAULT_AGENT,
    DEFAULT_MODE,
    get_project_dir,
    load_assistant,
    normalize_mode,
    selectable_agent_options,
)
from .confirmation import resolve_confirmation
from .menu import show_agent_menu
from .session import destroy_session, get_or_create_session, get_session
from .stream import TelegramStreamer

log = logging.getLogger(__name__)

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
    """Handle /agent command — manage agent settings, mode, and session."""
    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        await update.message.reply_text(
            "Agent mode is only available in private chats."
        )
        return

    clear_all_input_states(context)

    # Ensure defaults are set (coercing any legacy/removed persisted mode)
    context.user_data["agent_mode"] = normalize_mode(
        context.user_data.get("agent_mode")
    )
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

    # Start a session (single condor agent)
    if action.startswith("mode:"):
        mode = action.split(":", 1)[1]
        await _handle_mode_start(update, context, mode)

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
        await _handle_custom_use(update, context, int(action.split(":", 1)[1]))
    elif action.startswith("cu_page:"):
        await _handle_custom_page(update, context, int(action.split(":", 1)[1]))
    elif action.startswith("cu_pick:"):
        await _handle_custom_pick(update, context, action.split(":", 1)[1])
    elif action.startswith("cu_key:"):
        await _handle_custom_rekey(update, context, int(action.split(":", 1)[1]))
    elif action.startswith("cu_delok:"):
        await _handle_custom_delete(update, context, int(action.split(":", 1)[1]))
    elif action.startswith("cu_del:"):
        await _handle_custom_delete_confirm(update, context, int(action.split(":", 1)[1]))
    elif action == "cu_noop":
        pass  # page indicator / section header — do nothing

    # Session management
    elif action == "stop":
        await _handle_stop(update, context)
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

    # Trade confirmations
    elif action.startswith("confirm_trade:"):
        request_id = action.split(":", 1)[1]
        resolved = resolve_confirmation(request_id, approved=True)
        text = "Approved." if resolved else "Request expired."
        await query.message.edit_text(text)
    elif action.startswith("reject_trade:"):
        request_id = action.split(":", 1)[1]
        resolved = resolve_confirmation(request_id, approved=False)
        text = "Rejected." if resolved else "Request expired."
        await query.message.edit_text(text)


async def _handle_mode_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    mode: str,
) -> None:
    """Start a session in the given mode."""
    query = update.callback_query
    message = query.message if query else update.message
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Never start a session in a removed/unknown mode (stale state, old button).
    mode = normalize_mode(mode)

    agent_key = context.user_data.get("agent_llm", DEFAULT_AGENT)
    mode_label = AGENT_MODES.get(mode, {}).get("label", mode)
    llm_label = AGENT_OPTIONS.get(agent_key, {}).get("label", agent_key)

    status_text = f"Starting {mode_label} session ({llm_label})..."
    if query:
        await message.edit_text(status_text)
    else:
        message = await message.reply_text(status_text)

    # Destroy existing session
    await destroy_session(chat_id)

    context.user_data["agent_mode"] = mode

    try:
        bot = context.bot

        async def _perm_cb(tool_call, options):
            from .confirmation import permission_callback

            return await permission_callback(bot, chat_id, tool_call, options)

        session = await get_or_create_session(
            chat_id=chat_id,
            agent_key=agent_key,
            permission_callback=_perm_cb,
            user_id=user_id,
            user_data=context.user_data,
            mode=mode,
        )

        # Inject mode-specific context (auto-loaded from assistants/*.md)
        extra_context = load_assistant(mode)

        if extra_context:
            try:
                await session.client.prompt(extra_context)
            except Exception:
                log.warning("Failed to inject %s context for chat %d", mode, chat_id)

        await message.edit_text(
            f"{mode_label} is ready. Send a message to start chatting.\n\n"
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


def _provider_at(context: ContextTypes.DEFAULT_TYPE, idx: int) -> dict | None:
    from condor.preferences import get_custom_providers

    providers = get_custom_providers(context.user_data)
    if 0 <= idx < len(providers):
        return providers[idx]
    return None


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
        await _show(update, f"{e}\n\nSend another URL, or cancel.", _custom_input_keyboard())
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
        await placeholder.edit_text(str(e), reply_markup=_custom_error_keyboard("agent:cu_retry"))
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
    update: Update, context: ContextTypes.DEFAULT_TYPE, idx: int
) -> None:
    """Open the model picker for a saved endpoint, refetching if stale."""
    from .custom_models import CustomProviderError, fetch_models
    from .menu import _custom_error_keyboard, _custom_picker_keyboard

    provider = _provider_at(context, idx)
    if provider is None:
        await _handle_custom_list(update, context, notice="That endpoint is gone.")
        return

    name = provider["name"]
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
                    f"agent:cu_use:{idx}",
                    secondary=("Change API key", f"agent:cu_key:{idx}"),
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
    update: Update, context: ContextTypes.DEFAULT_TYPE, idx: int
) -> None:
    """Replace the API key on a saved endpoint without re-entering its URL."""
    from .menu import _custom_key_prompt_keyboard

    provider = _provider_at(context, idx)
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
    update: Update, context: ContextTypes.DEFAULT_TYPE, idx: int
) -> None:
    from .menu import _custom_delete_keyboard

    provider = _provider_at(context, idx)
    if provider is None:
        await _handle_custom_list(update, context, notice="That endpoint is gone.")
        return

    await _show(
        update,
        f"Forget '{provider['name']}' ({provider['base_url']})?\n\n"
        "The saved URL and API key are deleted from this bot.",
        _custom_delete_keyboard(idx, provider["name"]),
    )


async def _handle_custom_delete(
    update: Update, context: ContextTypes.DEFAULT_TYPE, idx: int
) -> None:
    """Forget an endpoint, clearing agent_llm if it pointed at that endpoint."""
    from condor.preferences import parse_custom_agent_key, remove_custom_provider

    provider = _provider_at(context, idx)
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
    """Stop the active agent session."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    destroyed = await destroy_session(chat_id)

    if destroyed:
        await query.message.edit_text("Agent session stopped.")
    else:
        await query.message.edit_text("No active session.")


async def _handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Close the agent menu (keep session alive if running)."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    await query.message.delete()


async def _handle_compact_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show compact options sub-menu."""
    from .menu import _compact_menu_keyboard

    query = update.callback_query
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if not session or not session.client.alive:
        await query.message.edit_text("No active session to compact.")
        return

    await query.message.edit_text(
        "How would you like to compact context?\n\n"
        "Auto - summarize everything\n"
        "Custom - specify what to keep",
        reply_markup=_compact_menu_keyboard(),
    )


async def _handle_compact(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    custom_instructions: str | None = None,
) -> None:
    """Compact: summarize context → destroy session → recreate with summary."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if not session or not session.client.alive:
        await query.message.edit_text("No active session to compact.")
        return

    if session.is_busy:
        await query.message.edit_text("Agent is busy. Wait for it to finish first.")
        return

    await query.message.edit_text("Compacting context...")

    if custom_instructions:
        prompt = COMPACT_PROMPT_CUSTOM_TEMPLATE.format(instructions=custom_instructions)
    else:
        prompt = COMPACT_PROMPT_AUTO

    try:
        summary = await session.client.prompt(prompt)
    except Exception as e:
        log.exception("Failed to get compact summary")
        await query.message.edit_text(f"Compact failed: {e}")
        return

    if not summary or not summary.strip():
        await query.message.edit_text("Agent returned empty summary. Compact aborted.")
        return

    agent_key = session.agent_key
    mode = session.mode
    await destroy_session(chat_id)

    try:
        user_id = update.effective_user.id
        bot = context.bot

        async def _perm_cb(tool_call, options):
            from .confirmation import permission_callback

            return await permission_callback(bot, chat_id, tool_call, options)

        new_session = await get_or_create_session(
            chat_id=chat_id,
            agent_key=agent_key,
            permission_callback=_perm_cb,
            user_id=user_id,
            user_data=context.user_data,
            mode=mode,
        )

        compact_context = COMPACT_CONTEXT_TEMPLATE.format(summary=summary)
        await new_session.client.prompt(compact_context)

    except Exception as e:
        log.exception("Failed to recreate session after compact")
        await query.message.edit_text(f"Compact failed during session reset: {e}")
        return

    word_count = len(summary.split())
    await query.message.edit_text(
        f"Context compacted ({word_count} words carried over).\n\n"
        "Send a message to continue chatting."
    )


async def _handle_compact_custom_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Prompt user to type custom compact instructions."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if not session or not session.client.alive:
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
    """Destroy current session and start a fresh one in the same mode."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if not session or not session.client.alive:
        await query.message.edit_text("No active session.")
        return

    mode = session.mode
    await _handle_mode_start(update, context, mode)


async def _do_compact_from_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, instructions: str
) -> None:
    """Execute custom compact from user's text input."""
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if not session or not session.client.alive:
        await update.message.reply_text("No active session to compact.")
        return

    if session.is_busy:
        await update.message.reply_text("Agent is busy. Wait for it to finish first.")
        context.user_data["agent_compact_custom"] = True
        return

    placeholder = await update.message.reply_text("Compacting context...")

    prompt = COMPACT_PROMPT_CUSTOM_TEMPLATE.format(instructions=instructions)
    try:
        summary = await session.client.prompt(prompt)
    except Exception as e:
        log.exception("Failed to get compact summary")
        await placeholder.edit_text(f"Compact failed: {e}")
        return

    if not summary or not summary.strip():
        await placeholder.edit_text("Agent returned empty summary. Compact aborted.")
        return

    agent_key = session.agent_key
    mode = session.mode
    await destroy_session(chat_id)

    try:
        user_id = update.effective_user.id
        bot = context.bot

        async def _perm_cb(tool_call, options):
            from .confirmation import permission_callback

            return await permission_callback(bot, chat_id, tool_call, options)

        new_session = await get_or_create_session(
            chat_id=chat_id,
            agent_key=agent_key,
            permission_callback=_perm_cb,
            user_id=user_id,
            user_data=context.user_data,
            mode=mode,
        )
        compact_context = COMPACT_CONTEXT_TEMPLATE.format(summary=summary)
        await new_session.client.prompt(compact_context)
    except Exception as e:
        log.exception("Failed to recreate session after compact")
        await placeholder.edit_text(f"Compact failed during session reset: {e}")
        return

    word_count = len(summary.split())
    await placeholder.edit_text(
        f"Context compacted ({word_count} words carried over).\n\n"
        "Send a message to continue chatting."
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
    if not get_session(update.effective_chat.id) and not _is_agent_available(agent_key):
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
    text = context.chat_data.pop("_voice_transcription", None) or update.message.text

    if not text:
        return

    # "-" resets the ACP session: destroy current and let the next block auto-create a new one
    if text.strip() == "-":
        session = get_session(chat_id)
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

    mode = normalize_mode(context.user_data.get("agent_mode"))

    session = get_session(chat_id)

    # Auto-create session if none exists (always-on agent)
    if not session or not session.client.alive:
        # Reclaim the configured default after an auto-switch — same healing the
        # /agent command does, so users who only ever type messages benefit too.
        agent_key = _reclaim_default_agent(context)

        # Condor is the single interactive agent; its builder capabilities ship
        # as built-in skills, so there is only ever one mode.
        mode = DEFAULT_MODE
        context.user_data["agent_mode"] = mode

        # Check if the CLI binary is installed before attempting to spawn
        if not _is_agent_available(agent_key):
            log.debug("Agent CLI for %s not found, skipping auto-create", agent_key)
            return

        try:
            bot = context.bot

            async def _perm_cb(tool_call, options):
                from .confirmation import permission_callback

                return await permission_callback(bot, chat_id, tool_call, options)

            session = await get_or_create_session(
                chat_id=chat_id,
                agent_key=agent_key,
                permission_callback=_perm_cb,
                user_id=user_id,
                user_data=context.user_data,
                mode=mode,
            )

            # Inject mode-specific context (auto-loaded from assistants/*.md)
            extra_context = load_assistant(mode)

            if extra_context:
                try:
                    await session.client.prompt(extra_context)
                except Exception:
                    log.warning(
                        "Failed to inject %s context for chat %d", mode, chat_id
                    )

        except Exception as e:
            log.exception("Failed to create agent session")
            await update.message.reply_text(f"Failed to start agent: {e}")
            return

    # Check if busy
    if session.is_busy:
        await update.message.reply_text(
            r"⏳ Still working on the previous request\.\.\."
            "\n"
            r"Your message will be queued — or wait for it to finish\.",
            parse_mode="MarkdownV2",
        )
        return

    # Create streamer prefix
    prefix = ""
    mode_label = AGENT_MODES.get(mode, {}).get("label", "")
    if mode != DEFAULT_MODE and mode_label:
        prefix = f"{mode_label}\n\n"

    # Fetch voice data if this was a transcription
    voice_placeholder = context.chat_data.pop("_voice_placeholder", None)
    voice_transcription = context.chat_data.pop("_voice_transcription", None)

    if voice_transcription:
        voice_prefix = f"🎙 {voice_transcription}"
        prefix = f"{prefix}{voice_prefix}" if prefix else voice_prefix

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
        async for event in session.prompt_stream(text):
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
    if isinstance(last_event, PromptDone) and last_event.stop_reason == "disconnected":
        await destroy_session(chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text="Agent session disconnected. Send a message to start a new session.",
        )
    elif isinstance(last_event, PromptDone) and last_event.stop_reason == "timeout":
        await destroy_session(chat_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text="Agent timed out (took too long). Send a message to start a new session.",
        )
