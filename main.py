import asyncio
import importlib
import logging
import os
import sys
from pathlib import Path

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from condor.persistence import SafePicklePersistence
from handlers import clear_all_input_states
from utils.auth import restricted
from utils.config import WEB_PORT, WEB_URL
from utils.config import TELEGRAM_TOKEN

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


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

    if WEB_URL:
        base = WEB_URL.rstrip("/")
        url = f"{base}/login?token={token}"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🌐 Open Dashboard", url=url)]]
        )
        await update.message.reply_text(
            "🌐 *Web Dashboard*\n\n"
            "Tap the button below to open the dashboard\\.\n"
            "_Link valid for 5 minutes\\._",
            reply_markup=keyboard,
            parse_mode="MarkdownV2",
        )
    else:
        url = f"http://localhost:{WEB_PORT}/login?token={token}"
        await update.message.reply_text(
            f"🌐 *Web Dashboard*\n\n"
            f"Open this link in your browser:\n`{url}`\n\n"
            f"_Link valid for 5 minutes\\._",
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

See [this manual](https://hummingbot.org/condor/) if you're new to Condor\\.

You can control me by sending these commands:

/keys \\- add exchange API keys
/portfolio \\- view balances across exchanges
/bots \\- deploy and manage trading bots
/trade \\- place CEX and DEX orders"""

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


def reload_handlers():
    """Reload all handler modules."""
    modules_to_reload = [
        "handlers.portfolio",
        "handlers.bots",
        "handlers.bots.menu",
        "handlers.bots.controllers",
        "handlers.bots._shared",
        "handlers.executors",
        "handlers.executors.menu",
        "handlers.executors.grid",
        "handlers.executors.position",
        "handlers.executors._shared",
        "handlers.trading",
        "handlers.trading.router",
        "handlers.cex",
        "handlers.cex.menu",
        "handlers.cex.trade",
        "handlers.cex.orders",
        "handlers.cex.positions",
        "handlers.cex._shared",
        "handlers.dex",
        "handlers.dex.menu",
        "handlers.dex.swap_quote",
        "handlers.dex.swap_execute",
        "handlers.dex.swap_history",
        "handlers.dex.pools",
        "handlers.dex._shared",
        "handlers.config",
        "handlers.config.servers",
        "handlers.config.api_keys",
        "handlers.config.gateway",
        "handlers.config.user_preferences",
        "handlers.routines",
        "handlers.agents",
        "handlers.agents.menu",
        "handlers.agents.session",
        "handlers.agents.stream",
        "handlers.agents.confirmation",
        "handlers.agents._shared",
        "handlers.admin",
        "routines.base",
        "utils.auth",
        "utils.telegram_formatters",
        "config_manager",
        "condor.data_manager",
    ]

    for module_name in modules_to_reload:
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])
            logger.info(f"Reloaded module: {module_name}")

    # Re-register fetch functions after reload (preserves in-memory cache)
    try:
        from condor.server_data_service import register_default_fetches as sds_register

        sds_register()
    except Exception as e:
        logger.warning(f"Failed to re-register SDS fetches: {e}")

    try:
        from condor.data_manager import register_default_fetches

        register_default_fetches()
    except Exception as e:
        logger.warning(f"Failed to re-register DataManager fetches: {e}")


def register_handlers(application: Application) -> None:
    """Register all command handlers."""
    # Import fresh versions after reload
    from handlers.admin import admin_command
    from handlers.agents import agent_callback_handler, agent_command, agent_voice_handler
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
    from handlers.dex import dex_callback_handler, lp_command
    from handlers.executors import executors_callback_handler, executors_command
    from handlers.portfolio import get_portfolio_callback_handler, portfolio_command
    from handlers.routines import routines_callback_handler, routines_command
    from handlers.trading import trade_command as unified_trade_command
    from handlers.trading.router import unified_trade_callback_handler

    # Clear existing handlers
    application.handlers.clear()

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

    # Add configuration commands (direct access)
    application.add_handler(CommandHandler("servers", servers_command))
    application.add_handler(CommandHandler("keys", keys_command))
    application.add_handler(CommandHandler("gateway", gateway_command))
    application.add_handler(CommandHandler("admin", admin_command))
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

    # Add admin callback handler
    from handlers.admin import admin_callback_handler

    application.add_handler(
        CallbackQueryHandler(admin_callback_handler, pattern="^admin:")
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


async def post_init(application: Application) -> None:
    """Register bot commands after initialization."""
    from telegram import BotCommandScopeChat

    from utils.config import ADMIN_USER_ID

    # Sync server permissions (ensures all servers have ownership entries)
    await sync_server_permissions()

    # Clear any previously set commands for all scopes to avoid stale overrides
    from telegram import BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, BotCommandScopeDefault

    for scope in [BotCommandScopeDefault(), BotCommandScopeAllPrivateChats(), BotCommandScopeAllGroupChats()]:
        try:
            await application.bot.delete_my_commands(scope=scope)
        except Exception:
            pass

    if ADMIN_USER_ID:
        try:
            await application.bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=int(ADMIN_USER_ID)))
        except Exception:
            pass

    # Public commands (all users)
    commands = [
        BotCommand("start", "Welcome message and setup"),
        BotCommand("portfolio", "View balances across exchanges"),
        BotCommand("agent", "AI trading assistant"),
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
    ]
    await application.bot.set_my_commands(commands)

    # Admin-only commands (visible only to admin user in their command menu)
    if ADMIN_USER_ID:
        admin_commands = commands + [
            BotCommand("admin", "Admin panel - manage users and access"),
        ]
        try:
            await application.bot.set_my_commands(
                admin_commands, scope=BotCommandScopeChat(chat_id=int(ADMIN_USER_ID))
            )
        except Exception as e:
            logger.warning(f"Failed to set admin-specific commands: {e}")

    # Restore scheduled routine jobs from persistence
    from handlers.routines import restore_scheduled_jobs

    await restore_scheduled_jobs(application)

    # Start ServerDataService (unified server-centric cache)
    from condor.server_data_service import get_server_data_service
    from condor.server_data_service import register_default_fetches as sds_register

    sds_register()
    sds = get_server_data_service()
    sds.start()
    await sds.auto_subscribe_servers()

    # Start DataManager (legacy, delegates to SDS)
    from condor.data_manager import get_data_manager, register_default_fetches

    register_default_fetches()
    get_data_manager().start()

    # Start agent session health monitor
    from handlers.agents.session import start_health_monitor

    await start_health_monitor(application.bot)

    # Start file watcher
    asyncio.create_task(watch_and_reload(application))


async def watch_and_reload(application: Application) -> None:
    """Watch for file changes and reload handlers automatically."""
    try:
        from watchfiles import awatch
    except ImportError:
        logger.warning(
            "watchfiles not installed. Auto-reload disabled. Install with: uv add watchfiles"
        )
        return

    handlers_path = Path(__file__).parent / "handlers"
    routines_path = Path(__file__).parent / "routines"
    logger.info(f"👀 Watching for changes in: {handlers_path}, {routines_path}")

    async for changes in awatch(handlers_path, routines_path):
        logger.info(f"📝 Detected changes: {changes}")
        try:
            reload_handlers()
            register_handlers(application)
            logger.info("✅ Auto-reloaded handlers successfully")
        except Exception as e:
            logger.error(f"❌ Auto-reload failed: {e}", exc_info=True)


def get_persistence() -> SafePicklePersistence:
    """
    Build a persistence object that works both locally and in Docker.
    - Uses an env var override if provided.
    - Defaults to <project_root>/data/condor_bot_data.pickle.
    - Ensures the parent directory exists, but does NOT create the file.
    - Uses SafePicklePersistence for atomic writes, backup recovery,
      and ephemeral key filtering.
    """
    base_dir = Path(__file__).parent
    default_path = base_dir / "data" / "condor_bot_data.pickle"

    persistence_path = Path(os.getenv("CONDOR_PERSISTENCE_FILE", default_path))

    # Make sure the directory exists; the file will be created by PTB
    persistence_path.parent.mkdir(parents=True, exist_ok=True)

    return SafePicklePersistence(filepath=persistence_path, update_interval=10)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors gracefully."""
    if isinstance(context.error, NetworkError):
        logger.warning(f"Network error (will retry): {context.error}")
        return

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
    # Setup persistence to save user data, chat data, and bot data
    # This will save trading context, last used parameters, etc.
    persistence = get_persistence()

    async def post_shutdown(application: Application) -> None:
        """Clean up agent subprocesses on shutdown."""
        from handlers.agents.session import destroy_all_sessions, stop_health_monitor

        await stop_health_monitor()
        await destroy_all_sessions()

        # Stop all trading agents
        from condor.trading_agent.engine import get_all_engines
        for engine in list(get_all_engines().values()):
            try:
                await engine.stop()
            except Exception:
                pass

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

    # Create the Application with persistence enabled
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(True)
        .build()
    )

    # Register all handlers
    register_handlers(application)

    # Register error handler
    application.add_error_handler(error_handler)

    # Run TG bot + web server concurrently in a manual event loop
    asyncio.run(_run_dual(application))


async def _run_dual(application: Application) -> None:
    """Run the Telegram bot and FastAPI web server concurrently."""
    import signal

    import uvicorn

    from condor.web.app import create_app
    from condor.web.ws_manager import get_ws_manager

    # Initialize and start the Telegram application
    await application.initialize()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    await application.start()

    # Create and start the web server
    web_app = create_app()
    config = uvicorn.Config(
        web_app,
        host="0.0.0.0",
        port=WEB_PORT,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)

    # Start WebSocket manager
    get_ws_manager().start()

    logger.info("Starting Condor: Telegram bot + web dashboard on port %s", WEB_PORT)

    # Handle shutdown signals
    shutdown_event = asyncio.Event()

    def _signal_handler():
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # Run uvicorn as a task
    web_task = asyncio.create_task(server.serve())

    # Wait until shutdown signal
    await shutdown_event.wait()

    logger.info("Shutting down...")
    server.should_exit = True
    await web_task

    # Graceful Telegram shutdown
    await application.updater.stop()
    await application.stop()
    await application.shutdown()


if __name__ == "__main__":
    # Add custom methods to the application object
    Application.send_to_telegram = send_to_telegram
    Application.send_to_all = send_to_all
    main()
