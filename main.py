import asyncio
import importlib
import logging
import os
import sys
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

from telegram import BotCommand, CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from condor import paths
from condor.persistence import SafePicklePersistence
from condor.telemetry import taps as telemetry_taps
from handlers import cancel_command, clear_all_input_states
from utils.auth import restricted
from utils.config import (
    LOCAL_MODE,
    TELEGRAM_TOKEN,
    USE_TAILSCALE,
    WEB_HOST,
    WEB_PORT,
    WEB_URL,
    ConfigError,
    check_local_user,
    check_startup_config,
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Suppress httpx INFO-level request logging — python-telegram-bot embeds the
# bot token in the request path (api.telegram.org/bot<TOKEN>/getUpdates), and
# httpx's default INFO logs the full URL on every call. With long-poll firing
# every ~10s, the token ends up in every log handler (journald, files, etc.),
# which makes safe log sharing impossible. Suppressing to WARNING preserves
# real HTTP errors while removing the token leak.
logging.getLogger("httpx").setLevel(logging.WARNING)


def _get_start_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Build the start menu inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🔌 Servers", callback_data="start:config_servers"),
            InlineKeyboardButton("🔑 Keys", callback_data="start:config_keys"),
            InlineKeyboardButton("🌐 Gateway", callback_data="start:config_gateway"),
        ],
    ]
    if is_admin:
        keyboard.append([InlineKeyboardButton("👑 Admin", callback_data="start:admin")])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="start:cancel")])
    return InlineKeyboardMarkup(keyboard)


@restricted
async def web_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate a one-time login link for the web dashboard."""
    from condor.web.auth import create_login_token

    user = update.effective_user
    token = create_login_token(user.id, user.username or "", user.first_name or "")

    url = f"{WEB_URL}/login?token={token}"

    # Telegram rejects URL buttons pointing at localhost/loopback hosts, so the
    # Open Dashboard button is only emitted for a reachable host. The Copy Link
    # button (plain text copy) and the monospace URL work everywhere.
    _hostname = urlparse(WEB_URL).hostname or ""
    is_local = (
        "localhost" in WEB_URL or "127.0.0.1" in WEB_URL or "." not in _hostname
    )

    buttons = [InlineKeyboardButton("📋 Copy Link", copy_text=CopyTextButton(text=url))]
    if not is_local:
        buttons.insert(0, InlineKeyboardButton("🌐 Open Dashboard", url=url))
    keyboard = InlineKeyboardMarkup([buttons])
    await update.message.reply_text(
        "🌐 *Web Dashboard*\n\n"
        "Tap the button below, to open the dashboard or copy the link:\n"
        f"`{url}`\n\n"
        "_Link valid for 5 minutes\\._",
        reply_markup=keyboard,
        parse_mode="MarkdownV2",
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the conversation and display available commands (BotFather style)."""
    from config_manager import UserRole, get_config_manager
    from utils.auth import _notify_admin_new_user

    user_id = update.effective_user.id
    username = update.effective_user.username or "No username"

    cm = get_config_manager()
    role = cm.get_user_role(user_id)

    # Handle blocked users
    if role == UserRole.BLOCKED:
        await update.message.reply_text("Access denied.")
        return

    # Handle pending users
    if role == UserRole.PENDING:
        reply_text = f"""Access Pending

Your access request is awaiting admin approval.

Your Info:
User ID: {user_id}
Username: @{username}

You will be notified when approved."""
        await update.message.reply_text(reply_text)
        return

    # Handle new users - register as pending
    if role is None:
        is_new = cm.register_pending(user_id, username)
        if is_new:
            await _notify_admin_new_user(context, user_id, username)

        reply_text = f"""Access Request Submitted

Your request has been sent to the admin for approval.

Your Info:
User ID: {user_id}
Username: @{username}

You will be notified when approved."""
        await update.message.reply_text(reply_text)
        return

    # User is approved (USER or ADMIN role)
    clear_all_input_states(context)

    reply_text = """I can help you create and manage trading bots on any CEX or DEX using Hummingbot API servers\\.

See [this manual](https://condor.hummingbot.org/introduction) if you're new to Condor\\.

You can control me by sending these commands:

/keys \\- add exchange API keys
/portfolio \\- view balances across exchanges
/bots \\- deploy and manage trading bots
/trade \\- place CEX and DEX orders
/agent \\- AI trading assistant
/web \\- open the web dashboard"""

    await update.message.reply_text(
        reply_text, parse_mode="MarkdownV2", disable_web_page_preview=True
    )


@restricted
async def start_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle callbacks from the start menu."""
    query = update.callback_query
    await query.answer()

    data = query.data
    action = data.split(":")[1] if ":" in data else data

    # Handle cancel - delete the message
    if action == "cancel":
        await query.message.delete()
        return

    # Handle navigation to config options
    if data.startswith("start:"):
        if action == "config_servers":
            from handlers import clear_all_input_states
            from handlers.config.servers import show_api_servers

            clear_all_input_states(context)
            await show_api_servers(query, context)
        elif action == "config_keys":
            from handlers import clear_all_input_states
            from handlers.config.api_keys import show_api_keys

            clear_all_input_states(context)
            await show_api_keys(query, context)
        elif action == "config_gateway":
            from handlers import clear_all_input_states
            from handlers.config.gateway import show_gateway_menu

            clear_all_input_states(context)
            context.user_data.pop("dex_state", None)
            context.user_data.pop("cex_state", None)
            await show_gateway_menu(query, context)
        elif action == "admin":
            from handlers import clear_all_input_states
            from handlers.admin import _show_admin_menu

            clear_all_input_states(context)
            await _show_admin_menu(query, context)


# Modules the handlers import from that do not live under ``handlers/``, and
# that are safe to re-execute. Everything under ``handlers/`` itself is
# discovered by :func:`_discover_handler_modules`.
#
# ``routines/`` is deliberately absent: routines have their own mtime-aware
# discovery in ``routines.base.discover_routines(force_reload=True)``, which
# owns reimporting individual routine modules. Only the base module is listed,
# so that machinery itself stays fresh.
_EXTRA_RELOAD_MODULES = (
    "config_manager",
    "utils.auth",
    "utils.telegram_formatters",
    "routines.base",
)


def _discover_handler_modules() -> list[str]:
    """Every module under ``handlers/``, children before parents.

    Derived rather than hand-listed. A hand-maintained list drifts, and it
    drifts *silently*: ``reload_handlers`` skips any name not in
    ``sys.modules``, so a stale entry is a no-op and a missing entry means the
    watcher logs a successful reload while the running bot keeps the old code.
    That is worse than no hot-reload, because it reports success. The list this
    replaced named three ``handlers.dex.swap_*`` modules that no longer exist
    and omitted six that do, including ``handlers.dex.router``.

    Naming every module matters because ``importlib.reload`` is NOT recursive:
    reloading ``handlers.dex`` re-executes its ``__init__``, but its
    ``from .router import ...`` re-binds the *cached* ``handlers.dex.router``,
    so an edit there is missed unless that module is reloaded in its own right.

    For the same reason children sort before parents: by the time a package's
    ``__init__`` re-executes, the submodules it imports from have already been
    refreshed.
    """
    root = Path(__file__).parent / "handlers"
    modules: set[str] = set()
    for path in root.rglob("*.py"):
        parts = path.relative_to(root.parent).with_suffix("").parts
        # Per-agent runtime stores can sit under a watched tree (FEAT-003) and
        # are data, not code — the file watcher skips them for the same reason.
        if "__pycache__" in parts or "store" in parts:
            continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            modules.add(".".join(parts))
    return sorted(modules, key=lambda name: (-name.count("."), name))


def reload_handlers():
    """Reload all handler modules."""
    # NOTE: no "condor.runtime.*" module belongs here. The runtime holds live
    # subprocess handles (agent sessions); re-executing those modules resets
    # the registry and silently orphans every running agent. The discovery
    # above only walks handlers/, so it cannot pull them in.
    modules_to_reload = [*_EXTRA_RELOAD_MODULES, *_discover_handler_modules()]

    reloaded = 0
    for module_name in modules_to_reload:
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])
            logger.debug(f"Reloaded module: {module_name}")
            reloaded += 1
    logger.info(f"Reloaded {reloaded} modules")

    # Re-register fetch functions after reload (preserves in-memory cache)
    try:
        from condor.server_data_service import register_default_fetches as sds_register

        sds_register()
    except Exception as e:
        logger.warning(f"Failed to re-register SDS fetches: {e}")


def register_handlers(application: Application) -> None:
    """Register all command handlers."""
    # Import fresh versions after reload
    from handlers.admin import admin_command
    from handlers.admin.update import update_command
    from handlers.agents import (
        agent_callback_handler,
        agent_command,
        agent_voice_handler,
        stop_command,
    )
    from handlers.bots import (
        bots_callback_handler,
        bots_command,
        get_bots_document_handler,
        new_bot_command,
    )
    from handlers.cex import cex_callback_handler
    from handlers.config import get_config_callback_handler, get_modify_value_handler
    from handlers.config.api_keys import keys_command
    from handlers.config.gateway import gateway_command
    from handlers.config.servers import servers_command
    from handlers.delegations import delegations_callback_handler, delegations_command
    from handlers.dex import dex_callback_handler, lp_command
    from handlers.executors import executors_callback_handler, executors_command
    from handlers.memory import memory_callback_handler, memory_command
    from handlers.portfolio import get_portfolio_callback_handler, portfolio_command
    from handlers.routines import routines_callback_handler, routines_command
    from handlers.trading import trade_command as unified_trade_command
    from handlers.trading.router import unified_trade_callback_handler

    # Clear existing handlers
    application.handlers.clear()

    # Usage telemetry observer (FEAT-023). PTB dispatches every update to every
    # group, so one handler in group -1 sees every command and every callback
    # without touching a single handler below. It only reads — it must never
    # call into @restricted, or observing would become an authorization side
    # effect — and it is a no-op unless the admin opted in.
    application.add_handler(TypeHandler(Update, telemetry_taps.telegram_tap), group=-1)

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("portfolio", portfolio_command))
    application.add_handler(CommandHandler("bots", bots_command))
    application.add_handler(CommandHandler("new_bot", new_bot_command))
    application.add_handler(
        CommandHandler("trade", unified_trade_command)
    )  # Unified trade (CEX + DEX)
    application.add_handler(
        CommandHandler("swap", unified_trade_command)
    )  # Alias for /trade
    application.add_handler(CommandHandler("lp", lp_command))
    application.add_handler(CommandHandler("routines", routines_command))
    application.add_handler(CommandHandler("executors", executors_command))
    application.add_handler(CommandHandler("agent", agent_command))
    # Interrupts the answer in flight without touching the session — the
    # dashboard's Stop button, for a surface that has no buttons while streaming.
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("delegations", delegations_command))
    application.add_handler(CommandHandler("memory", memory_command))
    # Universal escape hatch for flows that arm a "next message is the answer"
    # input mode. Every such prompt advertises /cancel.
    application.add_handler(CommandHandler("cancel", cancel_command))

    # Add configuration commands (direct access)
    application.add_handler(CommandHandler("servers", servers_command))
    application.add_handler(CommandHandler("keys", keys_command))
    application.add_handler(CommandHandler("gateway", gateway_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("update", update_command))
    application.add_handler(CommandHandler("web", web_command))

    # Add callback query handler for start menu navigation
    application.add_handler(
        CallbackQueryHandler(start_callback_handler, pattern="^start:")
    )

    # Add unified trade callback handler BEFORE cex/dex handlers (for connector switching)
    application.add_handler(
        CallbackQueryHandler(unified_trade_callback_handler, pattern="^trade:")
    )

    # Add callback query handlers for trading operations
    application.add_handler(CallbackQueryHandler(cex_callback_handler, pattern="^cex:"))
    application.add_handler(CallbackQueryHandler(dex_callback_handler, pattern="^dex:"))
    application.add_handler(
        CallbackQueryHandler(bots_callback_handler, pattern="^bots:")
    )
    application.add_handler(
        CallbackQueryHandler(routines_callback_handler, pattern="^routines:")
    )
    application.add_handler(
        CallbackQueryHandler(executors_callback_handler, pattern="^executors:")
    )

    # Add agent callback handler
    application.add_handler(
        CallbackQueryHandler(agent_callback_handler, pattern="^agent:")
    )

    # Add delegations callback handler (/delegations list + view + stop)
    application.add_handler(
        CallbackQueryHandler(delegations_callback_handler, pattern="^deleg:")
    )

    # Add memory callback handler (/memory review + delete)
    application.add_handler(
        CallbackQueryHandler(memory_callback_handler, pattern="^memory:")
    )

    # Add admin callback handler
    from handlers.admin import admin_callback_handler

    application.add_handler(
        CallbackQueryHandler(admin_callback_handler, pattern="^admin:")
    )

    # Telemetry consent prompt (FEAT-023): three buttons, admin only
    from condor.telemetry import prompt as telemetry_prompt

    application.add_handler(
        CallbackQueryHandler(
            telemetry_prompt.callback_handler,
            pattern=f"^{telemetry_prompt.CALLBACK_PREFIX}:",
        )
    )

    # Add callback query handler for portfolio settings
    application.add_handler(get_portfolio_callback_handler())

    # Add callback query handler for config menu
    application.add_handler(get_config_callback_handler())

    # Add UNIFIED message handler for ALL text input
    # This single handler routes to: CLOB trading, DEX trading, and Config flows
    # based on context state. This avoids issues with multiple MessageHandlers
    # competing for the same filter.
    application.add_handler(get_modify_value_handler())

    # Add voice message handler for agent transcription
    application.add_handler(MessageHandler(filters.VOICE, agent_voice_handler))

    # Add document handler for file uploads (e.g., config files in /bots)
    application.add_handler(get_bots_document_handler())

    logger.info("Handlers registered successfully")


async def sync_server_permissions() -> None:
    """
    Ensure all servers in config have permission entries.
    Registers any unregistered servers with admin as owner.
    """
    from config_manager import get_config_manager

    cm = get_config_manager()
    for server_name in cm.list_servers():
        cm.ensure_server_registered(server_name)

    logger.info("Synced server permissions")


async def register_bot_commands(application: Application) -> None:
    """Register the Telegram command menus (public for everyone, admin overlay).

    Extracted from ``startup`` so it can also run on hot-reload — otherwise a
    newly added command (e.g. /delegations) gets its dispatch handler reloaded
    but never shows up in the menu until a full process restart.
    """
    from telegram import (
        BotCommandScopeAllGroupChats,
        BotCommandScopeAllPrivateChats,
        BotCommandScopeChat,
        BotCommandScopeDefault,
    )

    from utils.config import ADMIN_USER_ID

    # Clear any previously set commands for all scopes to avoid stale overrides
    for scope in [
        BotCommandScopeDefault(),
        BotCommandScopeAllPrivateChats(),
        BotCommandScopeAllGroupChats(),
    ]:
        try:
            await application.bot.delete_my_commands(scope=scope)
        except Exception:
            pass

    if ADMIN_USER_ID:
        try:
            await application.bot.delete_my_commands(
                scope=BotCommandScopeChat(chat_id=int(ADMIN_USER_ID))
            )
        except Exception:
            pass

    # 1) Public commands — registered by default for ALL users (default scope is
    #    the universal fallback every user resolves to unless a more specific
    #    scope overrides it). Wrapped independently so a transient failure here
    #    never blocks the admin step (or the rest of startup) from running.
    commands = [
        BotCommand("start", "Welcome message and setup"),
        BotCommand("portfolio", "View balances across exchanges"),
        BotCommand("agent", "AI trading assistant"),
        BotCommand("stop", "Stop the answer being generated"),
        BotCommand("delegations", "Monitor background agent tasks"),
        BotCommand("memory", "Review what the assistant remembers about you"),
        BotCommand("executors", "Deploy and manage trading executors"),
        BotCommand("bots", "Deploy and manage trading bots"),
        BotCommand("new_bot", "Create bot configurations"),
        BotCommand("routines", "Run configurable Python scripts"),
        BotCommand("trade", "Place CEX and DEX orders"),
        BotCommand("lp", "Liquidity pool management"),
        BotCommand("servers", "Manage Hummingbot API servers"),
        BotCommand("keys", "Configure exchange API credentials"),
        BotCommand("gateway", "Gateway for DEX trading"),
        BotCommand("web", "Open the web dashboard"),
        BotCommand("cancel", "Abort the current input flow"),
    ]
    try:
        await application.bot.set_my_commands(commands)
    except Exception as e:
        logger.warning(f"Failed to set public commands: {e}", exc_info=True)

    # 2) Admin-only commands — layered on top of the public ones, visible only in
    #    the admin user's own command menu (chat scope overrides the default).
    if ADMIN_USER_ID:
        admin_commands = commands + [
            BotCommand("admin", "Admin panel - manage users and access"),
            BotCommand("update", "Check for updates and restart"),
        ]
        try:
            await application.bot.set_my_commands(
                admin_commands, scope=BotCommandScopeChat(chat_id=int(ADMIN_USER_ID))
            )
        except Exception as e:
            logger.warning(f"Failed to set admin-specific commands: {e}", exc_info=True)


async def _notify_interrupted_runs(bot, report) -> None:
    """Tell each owner, once, what the restart found.

    One summary per chat rather than a message per run: a crash with several
    live loops would otherwise spam the user at the worst possible moment.
    """
    from condor.notifications import announce, user_for_chat

    by_chat: dict[int, list] = {}
    for run in report.interrupted:
        status = None
        try:
            from condor.runtime.registry_file import read_status

            status = read_status(run.session_dir)
        except Exception:
            pass
        chat_id = (status or {}).get("chat_id")
        if chat_id:
            by_chat.setdefault(int(chat_id), []).append(run)

    for chat_id, runs in by_chat.items():
        lines = [f"Found {len(runs)} interrupted run(s) after restart:"]
        for run in runs:
            suffix = " — restarted" if run.restarted else ""
            lines.append(f"• {run.label} (last tick {run.last_tick}){suffix}")
        text = "\n".join(lines)
        # Telegram and the bell (FEAT-048) in one call: ``announce`` resolves
        # the sender once and files the notice exactly once, including when the
        # sender *is* the bell (local mode, FEAT-049). A private chat id is the
        # owner's user id; a group has no dashboard owner, so a group summary is
        # simply not filed anywhere.
        try:
            await announce(
                user_for_chat(chat_id), chat_id, text, kind="system", bot=bot
            )
        except Exception:
            logger.warning("Could not notify chat %s about interrupted runs", chat_id)


def _outbound_bot(application: Application):
    """The object this process sends user-facing messages through.

    Telegram mode: the real PTB bot, exactly as before. Local mode: there is no
    Telegram behind the placeholder token, so outbound messages go to the
    dashboard bell instead (:class:`condor.notifications.NotifyBot`, FEAT-048).
    Everything downstream — the routine store, the session health monitor, the
    interrupted-run summaries — keeps calling ``send_message`` and neither knows
    nor cares which surface it reached.
    """
    if not LOCAL_MODE:
        return application.bot
    from condor.notifications import NotifyBot

    return NotifyBot()


async def startup(application: Application) -> None:
    """Bring the process up: commands, caches, supervisors, boot reconciliation.

    Deliberately *not* wired as PTB's ``post_init``. That hook only fires from
    ``run_polling``/``run_webhook``, and :func:`_run_dual` drives the lifecycle
    by hand (``initialize`` → ``start_polling`` → ``start``) so it can run
    uvicorn alongside the bot. Registering it on the builder would look correct
    and never run — which is exactly how boot reconciliation silently died.
    Called explicitly from :func:`_run_dual`, before the first update is served.
    """
    # First, before anything reads a conversation or reconciles a delegation:
    # settle where the runtime store lives (FEAT-051). Idempotent, so this is a
    # no-op on every boot after the first; it is a named public function rather
    # than inline code because a second entry point (a CLI, a worker) would have
    # to call it too.
    from condor.migrations import ensure_migrated

    ensure_migrated()

    # Sync server permissions (ensures all servers have ownership entries)
    await sync_server_permissions()

    # Preload Whisper model in background so first voice message is fast
    import asyncio

    from utils.transcribe import DEFAULT_MODEL, _get_model

    asyncio.get_event_loop().run_in_executor(None, _get_model, DEFAULT_MODEL)

    # Whatever this process pushes at users from here on. In local mode there is
    # no Telegram to push to, so it is the dashboard bell (FEAT-048) instead —
    # which is what keeps the documented "context.bot is never None" contract
    # true for routines with no bot behind them.
    outbound_bot = _outbound_bot(application)

    # Register command menus (public + admin overlay). Pure Telegram: there is
    # no command menu to publish when nothing polls.
    if not LOCAL_MODE:
        await register_bot_commands(application)

    # Restore scheduled routine jobs from persistence
    from handlers.routines import restore_scheduled_jobs

    await restore_scheduled_jobs(application)

    # Inject Telegram bot into routine store so web-triggered routines can send messages
    from condor.routine_store import get_routine_store

    get_routine_store().set_bot(outbound_bot)

    # Start ServerDataService (unified server-centric cache)
    from condor.server_data_service import get_server_data_service
    from condor.server_data_service import register_default_fetches as sds_register

    sds_register()
    sds = get_server_data_service()
    sds.start()
    await sds.auto_subscribe_servers()

    # Ride the ticker-pool poll the SDS just started: an hourly price snapshot
    # per server is the only source of 24h change on the CLOB side, and it costs
    # no upstream request (FEAT-053).
    from condor import ticker_history

    ticker_history.install_listener()

    # Start agent session health monitor. The health monitor is process
    # lifecycle, not a session operation, so it is driven off the module
    # directly rather than through the client facade.
    from condor.runtime import sessions as runtime_sessions
    from condor.runtime.confirmations import get_registry

    await runtime_sessions.start_health_monitor(outbound_bot)
    # Sweeps expired approvals so a request nobody answers is denied, not leaked.
    await get_registry().start()

    # Settle whatever the previous process left running: mark orphaned loops
    # interrupted, restart only the ones that opted in, and tell the owner once.
    from condor.runtime.loops import get_supervisor

    try:
        report = await get_supervisor().reconcile_boot()
        if report.total:
            logger.warning(
                "Boot reconciliation: %d interrupted, %d restarted",
                report.total,
                len(report.restarted),
            )
            await _notify_interrupted_runs(outbound_bot, report)
    except Exception:
        logger.exception("Boot reconciliation failed; continuing startup")

    # Schedule periodic update checks (notifies admin)
    from handlers.admin.update import schedule_update_checks

    schedule_update_checks(application)

    # Usage telemetry (FEAT-023). init() resolves the consent level once so the
    # taps never read the disk on a hot path, and only materializes the
    # install's random ids when telemetry is actually on. The jobs are
    # registered either way and return immediately while consent is absent, so
    # opting in mid-run works without a restart.
    from condor import telemetry

    try:
        level = telemetry.init(hosted=True)
        telemetry_taps.register_jobs(application)
        logger.info("Telemetry level: %s", level)
    except Exception:
        logger.exception("Telemetry init failed (continuing without it)")

    # Conversation sharing (FEAT-054, FEAT-055). Two jobs, deliberately not part
    # of the telemetry block above: they share no consent record, no queue and
    # no endpoint with it. Both are free on an install where nobody has opted
    # in — the delivery job finds an empty queue and the sweep finds no user at
    # ``always``, and neither touches the network.
    try:
        from condor.sharing import share as sharing
        from condor.sharing import sweep as sharing_sweep

        sharing.register_jobs(application)
        sharing_sweep.register_jobs(application)
    except Exception:
        logger.exception("Sharing job registration failed (continuing without it)")

    # Start file watcher
    asyncio.create_task(watch_and_reload(application))


async def teardown(application: Application) -> None:
    """Wind the process down: stop supervisors, flush state, close clients.

    The counterpart to :func:`startup`, and not PTB's ``post_shutdown`` for the
    same reason: ``Application.shutdown()`` does not call that hook either.
    Called explicitly from :func:`_run_dual`.
    """
    from condor.runtime import client as runtime
    from condor.runtime import sessions as runtime_sessions
    from condor.runtime.confirmations import get_registry

    await runtime_sessions.stop_health_monitor()
    await get_registry().stop()
    await runtime.destroy_all()

    # Stop all trading agents. Graceful stop, deliberately NOT the shutdown
    # sequence — winding down positions is an emergency action, not what a
    # restart should do. Each engine records its final state on the way out.
    from condor.runtime.conversations import flush_all as flush_conversations
    from condor.runtime.loops import get_supervisor
    from condor.runtime.state import flush_all

    await get_supervisor().stop_all()
    # Writes are debounced, so force the last one out on a clean shutdown.
    flush_all()
    # A prompt still streaming when the bot went down holds its turn in
    # memory; write it out rather than losing the last thing that was said.
    flush_conversations()

    # Stop WebSocket manager
    from condor.web.ws_manager import get_ws_manager

    get_ws_manager().stop()

    # Stop ServerDataService
    from condor.server_data_service import get_server_data_service

    get_server_data_service().stop()

    # Close cached Hummingbot API clients (ConfigManager)
    from config_manager import get_config_manager

    await get_config_manager().close_all_clients()

    # Close MCP hummingbot client
    from mcp_servers.hummingbot_api.hummingbot_client import hummingbot_client

    await hummingbot_client.close()

    # Record the clean exit and give the outbox one last chance. Both are no-ops
    # unless the admin opted in, and neither can fail the shutdown.
    try:
        from condor import telemetry

        telemetry.shutdown("signal")
        await telemetry.flush("teardown")
    except Exception:
        logger.debug("Telemetry teardown failed", exc_info=True)


async def watch_and_reload(application: Application) -> None:
    """Watch for file changes and reload handlers automatically."""
    try:
        from watchfiles import DefaultFilter, awatch
    except ImportError:
        logger.warning(
            "watchfiles not installed. Auto-reload disabled. Install with: uv add watchfiles"
        )
        return

    handlers_path = Path(__file__).parent / "handlers"
    routines_path = Path(__file__).parent / "routines"
    # ``agents/`` is deliberately NOT watched: AgentStore reads AGENT.md from
    # disk on every call, so there is no cache to bust (the assistant cache that
    # justified watching it is gone with FEAT-033), while agents write journals
    # and strategy state under the same tree — watching it would thrash reloads.
    watch_paths = [handlers_path, routines_path]
    logger.info(f"👀 Watching for changes in: {', '.join(str(p) for p in watch_paths)}")

    class _ReloadFilter(DefaultFilter):
        """Ignore per-agent runtime stores (FEAT-003).

        A store can sit under a watched tree (an agent-owned ``routines/`` dir
        next to its ``store/``), so without this every memory/skill write would
        thrash a full handler reload.
        """

        def __call__(self, change, path: str) -> bool:
            if f"{os.sep}store{os.sep}" in path:
                return False
            return super().__call__(change, path)

    async for changes in awatch(*watch_paths, watch_filter=_ReloadFilter()):
        logger.info(f"📝 Detected changes: {changes}")
        try:
            reload_handlers()
            register_handlers(application)
            # Refresh the Telegram command menus too, so a newly added/removed
            # command shows up without requiring a full process restart.
            await register_bot_commands(application)
            logger.info("✅ Auto-reloaded handlers successfully")
        except Exception as e:
            logger.error(f"❌ Auto-reload failed: {e}", exc_info=True)


def get_persistence() -> SafePicklePersistence:
    """
    Build a persistence object that works both locally and in Docker.
    - Uses an env var override if provided.
    - Defaults to <project_root>/data/condor_bot_data.pickle, resolved through
      condor.paths.data_dir() so $CONDOR_DATA_DIR repoints the whole
      operational store at once rather than this one file.
    - Ensures the parent directory exists, but does NOT create the file.
    - Uses SafePicklePersistence for atomic writes, backup recovery,
      and ephemeral key filtering.
    """
    default_path = paths.data_dir() / "condor_bot_data.pickle"

    persistence_path = Path(os.getenv("CONDOR_PERSISTENCE_FILE", default_path))

    # Make sure the directory exists; the file will be created by PTB
    persistence_path.parent.mkdir(parents=True, exist_ok=True)

    return SafePicklePersistence(filepath=persistence_path, update_interval=10)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors gracefully."""
    if isinstance(context.error, NetworkError):
        # Expected and self-healing; reported as an upstream blip, not a bug.
        telemetry_taps.on_upstream_error("telegram", "poll", "network")
        logger.warning(f"Network error (will retry): {context.error}")
        return

    # Type, a hash of the message, and our own stack frames. Never the message
    # itself — that is where balances, hostnames and keys leak.
    telemetry_taps.on_error(context.error, where="telegram", surface="telegram")
    logger.exception("Exception while handling an update:", exc_info=context.error)


async def send_to_telegram(
    self, chat_id: int, message: str, parse_mode: str = "Markdown"
):
    """Sends a message to a specific Telegram chat."""
    await self.bot.send_message(chat_id=chat_id, text=message, parse_mode=parse_mode)


async def send_to_all(self, message: str, parse_mode: str = "Markdown"):
    """Sends a message to all users who have started the bot."""
    for chat_id in self.user_data:
        try:
            await self.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=parse_mode
            )
        except Exception as e:
            logger.warning(f"Failed to send message to chat {chat_id}: {e}")


def main() -> None:
    """Run the bot."""
    # Refuse to start on a configuration that cannot mean what it says: telegram
    # mode (the default) with no token used to surface as an InvalidToken
    # traceback from inside PTB, and must never be quietly read as "local mode".
    # check_local_user() is the same idea one layer in — local mode logs in with
    # no password, so who it logs in as is settled here, at boot, and not as a
    # 500 from /auth/local-login with the browser already open.
    try:
        check_startup_config()
        check_local_user()
    except ConfigError as exc:
        logger.error("%s", exc)
        raise SystemExit(1)

    # Reap any ACP/MCP subprocess trees orphaned by a prior hard kill (kill -9,
    # OOM, power loss) before we spawn our own — those bypass teardown().
    try:
        from condor.acp.client import reap_stale_acp_trees

        reaped = reap_stale_acp_trees(TELEGRAM_TOKEN)
        if reaped:
            logger.info("Reaped %d stale ACP/MCP process(es) from a prior run", reaped)
    except Exception:
        logger.exception("Startup ACP reaper failed (continuing)")

    # Setup persistence to save user data, chat data, and bot data
    # This will save trading context, last used parameters, etc.
    persistence = get_persistence()

    # Create the Application with persistence enabled. No post_init/post_shutdown
    # hooks: they only fire from run_polling/run_webhook, and _run_dual owns the
    # lifecycle — startup() and teardown() are called there, explicitly.
    # In local mode there is no token and nothing polls; the placeholder exists
    # only so the Application (and with it job_queue, CallbackContext and the
    # handler registry) can be built at all. Nothing ever calls Telegram with it.
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN or "0:local")
        .persistence(persistence)
        .concurrent_updates(True)
        .build()
    )

    # Register all handlers
    register_handlers(application)

    # Register error handler
    application.add_error_handler(error_handler)

    # Run TG bot + web server concurrently in a manual event loop
    try:
        asyncio.run(_run_dual(application))
    finally:
        # /update asks for a restart by signalling a normal shutdown, so the
        # re-exec happens here: after the loop is gone and teardown has flushed
        # state and reaped subprocesses. In a `finally` because a restart is
        # still the right outcome even if a shutdown step blew up on the way
        # out. Replacing the image in place keeps the tmux pane alive.
        from utils.updater import exec_restart, restart_pending

        if restart_pending():
            exec_restart()  # never returns


def _web_server_config(web_app):
    """uvicorn's config for the dashboard, isolated so the bind address is testable.

    ``WEB_HOST`` is ``0.0.0.0`` in telegram mode (unchanged) and loopback in
    local mode, where the dashboard has no login at all, or when Tailscale is
    enabled, where `tailscale serve` is what actually exposes it — see
    :func:`utils.config.resolve_web_host` for why that is not negotiable by
    accident.
    """
    import uvicorn

    return uvicorn.Config(
        web_app,
        host=WEB_HOST,
        port=WEB_PORT,
        log_level="info",
        access_log=False,
    )


async def _run_dual(application: Application) -> None:
    """Run the Telegram bot and FastAPI web server concurrently."""
    import signal

    import uvicorn

    from condor.web.app import create_app
    from condor.web.ws_manager import get_ws_manager

    # Initialize and start the Telegram application. startup() runs between
    # initialize() and start_polling() — the same slot PTB gives post_init — so
    # commands, caches and boot reconciliation are settled before the first
    # update is dispatched.
    #
    # Local mode skips that lifecycle rather than faking it (FEAT-049): the
    # Application is built but never initialized, so nothing polls and no handler
    # can dispatch — the Telegram surface is inert, not mocked. The job queue is
    # started directly, which is all scheduled routines, update checks and
    # signals actually need.
    if LOCAL_MODE:
        await startup(application)
        await application.job_queue.start()
    else:
        await application.initialize()
        await startup(application)
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await application.start()

    # Create and start the web server. WEB_HOST (utils.config.resolve_web_host)
    # already resolves to loopback for Tailscale same as it does for local mode
    # -- here we only have to make sure `tailscale serve` is actually proxying
    # the tailnet to that loopback bind, or it would be reachable nowhere at
    # all.
    web_app = create_app()
    if USE_TAILSCALE:
        from utils.tailscale import ensure_serve

        if not await ensure_serve(WEB_PORT):
            logger.error(
                "Dashboard bound to 127.0.0.1 only; tailnet forwarding could "
                "not be confirmed, so it will NOT be reachable remotely "
                "until `tailscale serve` is fixed (see error above)."
            )
    server = uvicorn.Server(_web_server_config(web_app))

    # Start WebSocket manager
    get_ws_manager().start()

    # Notify admin that Condor has started
    from utils.config import ADMIN_USER_ID

    if ADMIN_USER_ID:
        try:
            # Report the version we came up on: after a /update restart this is
            # the confirmation that the new commit is the one actually running.
            from utils.updater import get_current_branch, get_local_commit

            branch, commit = await asyncio.gather(
                get_current_branch(), get_local_commit()
            )
            version = f" ({branch} @ {commit})" if commit else ""
            boot_text = f"Condor is online and ready.{version}"
            if not LOCAL_MODE:
                await application.bot.send_message(
                    chat_id=int(ADMIN_USER_ID),
                    text=boot_text,
                )
            # The same notice on the dashboard bell (FEAT-048), so an admin who
            # only has the browser open still sees which commit came up.
            from condor.notifications import record

            await record(int(ADMIN_USER_ID), boot_text, kind="system")
        except Exception as e:
            logger.warning(f"Failed to send startup notification to admin: {e}")

        # Ask, once, whether this install wants to be counted (FEAT-023). Sent
        # next to the boot notification because that is the one moment the admin
        # is already looking. Until it is answered, nothing is collected. The
        # prompt is a Telegram message with inline buttons, so local mode is
        # skipped here and asked in the dashboard instead (the consent card in
        # Settings → Privacy). Either way the install is already counted:
        # `telemetry.init()` does that at the ping floor, without an answer.
        if not LOCAL_MODE:
            from condor.telemetry.prompt import maybe_prompt_admin

            await maybe_prompt_admin(application.bot)

    if LOCAL_MODE:
        logger.info(
            "Starting Condor in local mode (no Telegram): dashboard on http://%s:%s",
            WEB_HOST,
            WEB_PORT,
        )
    else:
        logger.info(
            "Starting Condor: Telegram bot + web dashboard on port %s", WEB_PORT
        )

    # Handle shutdown signals
    shutdown_event = asyncio.Event()

    def _signal_handler():
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Run uvicorn as a task
    web_task = asyncio.create_task(server.serve())
    stop_task = asyncio.create_task(shutdown_event.wait())

    try:
        # Exit on a shutdown signal OR if the web server stops/crashes on its own.
        await asyncio.wait({web_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # Always run teardown — even if the run raised — so it (runtime.destroy_all
        # + engine.stop) reaps every ACP subprocess tree.
        logger.info("Shutting down...")
        stop_task.cancel()
        server.should_exit = True
        try:
            await web_task
        except Exception:
            logger.exception("Web server crashed during shutdown")
        # Run each step independently so one failure can't skip the rest.
        # teardown() runs last, mirroring where post_shutdown would have sat.
        # Local mode never initialized or started the Application, so the PTB
        # stop steps would only raise "not running" — it has a job queue to stop
        # and nothing else.
        if LOCAL_MODE:
            steps = (
                ("job_queue.stop", application.job_queue.stop),
                ("teardown", partial(teardown, application)),
            )
        else:
            steps = (
                ("updater.stop", application.updater.stop),
                ("application.stop", application.stop),
                ("application.shutdown", application.shutdown),
                ("teardown", partial(teardown, application)),
            )
        for name, step in steps:
            try:
                await step()
            except Exception:
                logger.exception("Shutdown step %s failed", name)


if __name__ == "__main__":
    # Add custom methods to the application object
    Application.send_to_telegram = send_to_telegram
    Application.send_to_all = send_to_all
    main()
