import logging
import importlib
import sys
import os
import asyncio
from pathlib import Path

from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    PicklePersistence,
)

from handlers import clear_all_input_states
from utils.auth import restricted
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




async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the conversation and display the main menu."""
    from config_manager import get_config_manager, UserRole
    from utils.auth import _notify_admin_new_user

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    username = update.effective_user.username or "No username"

    cm = get_config_manager()
    role = cm.get_user_role(user_id)

    # Handle blocked users
    if role == UserRole.BLOCKED:
        await update.message.reply_text("🚫 Access denied.")
        return

    # Handle pending users
    if role == UserRole.PENDING:
        reply_text = rf"""
⏳ *Access Pending*

Your access request is awaiting admin approval\.

🆔 *Your Info*:
👤 User ID: `{user_id}`
🏷️ Username: `@{username}`

You will be notified when approved\.
"""
        await update.message.reply_text(reply_text, parse_mode="MarkdownV2")
        return

    # Handle new users - register as pending
    if role is None:
        is_new = cm.register_pending(user_id, username)
        if is_new:
            await _notify_admin_new_user(context, user_id, username)

        reply_text = rf"""
🔒 *Access Request Submitted*

Your request has been sent to the admin for approval\.

🆔 *Your Info*:
👤 User ID: `{user_id}`
🏷️ Username: `@{username}`

You will be notified when approved\.
"""
        await update.message.reply_text(reply_text, parse_mode="MarkdownV2")
        return

    # User is approved (USER or ADMIN role)
    # Clear all pending input states to prevent interference
    clear_all_input_states(context)

    is_admin = role == UserRole.ADMIN

    # Build status information
    from config_manager import get_effective_server
    from utils.telegram_formatters import escape_markdown_v2

    # Get all servers and their statuses in parallel
    servers = cm.list_servers()
    active_server = get_effective_server(chat_id, context.user_data) or cm.get_default_server()

    server_statuses = {}
    active_server_online = False

    if servers:
        # Query all server statuses in parallel
        status_tasks = [cm.check_server_status(name) for name in servers]
        status_results = await asyncio.gather(*status_tasks, return_exceptions=True)

        for server_name, status_result in zip(servers, status_results):
            if isinstance(status_result, Exception):
                status = "error"
            else:
                status = status_result.get("status", "unknown")
            server_statuses[server_name] = status
            if server_name == active_server and status == "online":
                active_server_online = True

    # Build servers list display
    servers_display = ""
    online_count = 0
    if servers:
        for server_name in servers:
            status = server_statuses.get(server_name, "unknown")
            if status == "online":
                icon = "🟢"
                online_count += 1
            else:
                icon = "🔴"

            is_active = " ⭐" if server_name == active_server else ""
            server_escaped = escape_markdown_v2(server_name)
            servers_display += f"  {icon} `{server_escaped}`{is_active}\n"
    else:
        servers_display = "  _No servers configured_\n"

    # Get gateway and accounts info only if active server is online
    extra_info = ""
    if active_server_online:
        try:
            from handlers.config.server_context import get_gateway_status_info
            gateway_header, _ = await get_gateway_status_info(chat_id, context.user_data)
            extra_info += gateway_header

            client = await cm.get_client_for_chat(chat_id, preferred_server=active_server)
            accounts = await client.accounts.list_accounts()
            if accounts:
                total_creds = 0
                for account in accounts:
                    try:
                        creds = await client.accounts.list_account_credentials(account_name=str(account))
                        total_creds += len(creds) if creds else 0
                    except Exception:
                        pass
                accounts_escaped = escape_markdown_v2(str(len(accounts)))
                creds_escaped = escape_markdown_v2(str(total_creds))
                extra_info += f"*Accounts:* {accounts_escaped} \\({creds_escaped} keys\\)\n"
        except Exception as e:
            logger.warning(f"Failed to get extra info: {e}")

    # Build the message
    admin_badge = " 👑" if is_admin else ""

    # Description of capabilities
    capabilities = """_Trade CEX/DEX, manage bots, monitor portfolio_"""

    # Offline help message
    offline_help = ""
    if not active_server_online and servers:
        offline_help = """
⚠️ *Active server is offline*
• Ensure `hummingbot\\-backend\\-api` is running
• Or select an online server below
"""

    # Menu descriptions
    menu_help = r"""
🔌 *Servers* \- Add/manage Hummingbot API servers
🔑 *Keys* \- Connect exchange API credentials
🌐 *Gateway* \- Deploy Gateway for DEX trading
"""

    reply_text = rf"""
🦅 *Condor*{admin_badge}
{capabilities}

*Servers:*
{servers_display}{offline_help}{extra_info}{menu_help}"""
    keyboard = _get_start_menu_keyboard(is_admin=is_admin)
    await update.message.reply_text(reply_text, parse_mode="MarkdownV2", reply_markup=keyboard)


@restricted
async def start_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            from handlers.config.servers import show_api_servers
            from handlers import clear_all_input_states
            clear_all_input_states(context)
            await show_api_servers(query, context)
        elif action == "config_keys":
            from handlers.config.api_keys import show_api_keys
            from handlers import clear_all_input_states
            clear_all_input_states(context)
            await show_api_keys(query, context)
        elif action == "config_gateway":
            from handlers.config.gateway import show_gateway_menu
            from handlers import clear_all_input_states
            clear_all_input_states(context)
            context.user_data.pop("dex_state", None)
            context.user_data.pop("cex_state", None)
            await show_gateway_menu(query, context)
        elif action == "admin":
            from handlers.admin import _show_admin_menu
            from handlers import clear_all_input_states
            clear_all_input_states(context)
            await _show_admin_menu(query, context)


def reload_handlers():
    """Reload all handler modules."""
    modules_to_reload = [
        'handlers.portfolio',
        'handlers.bots',
        'handlers.bots.menu',
        'handlers.bots.controllers',
        'handlers.bots._shared',
        'handlers.trading',
        'handlers.trading.router',
        'handlers.cex',
        'handlers.cex.menu',
        'handlers.cex.trade',
        'handlers.cex.orders',
        'handlers.cex.positions',
        'handlers.cex._shared',
        'handlers.dex',
        'handlers.dex.menu',
        'handlers.dex.swap_quote',
        'handlers.dex.swap_execute',
        'handlers.dex.swap_history',
        'handlers.dex.pools',
        'handlers.dex._shared',
        'handlers.config',
        'handlers.config.servers',
        'handlers.config.api_keys',
        'handlers.config.gateway',
        'handlers.config.user_preferences',
        'handlers.routines',
        'handlers.admin',
        'routines.base',
        'utils.auth',
        'utils.telegram_formatters',
        'config_manager',
    ]

    for module_name in modules_to_reload:
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])
            logger.info(f"Reloaded module: {module_name}")


def register_handlers(application: Application) -> None:
    """Register all command handlers."""
    # Import fresh versions after reload
    from handlers.portfolio import portfolio_command, get_portfolio_callback_handler
    from handlers.bots import bots_command, bots_callback_handler, get_bots_document_handler
    from handlers.trading import trade_command as unified_trade_command
    from handlers.trading.router import unified_trade_callback_handler
    from handlers.cex import cex_callback_handler
    from handlers.dex import lp_command, dex_callback_handler
    from handlers.config import get_config_callback_handler, get_modify_value_handler
    from handlers.routines import routines_command, routines_callback_handler

    # Clear existing handlers
    application.handlers.clear()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("portfolio", portfolio_command))
    application.add_handler(CommandHandler("bots", bots_command))
    application.add_handler(CommandHandler("trade", unified_trade_command))  # Unified trade (CEX + DEX)
    application.add_handler(CommandHandler("swap", unified_trade_command))   # Alias for /trade
    application.add_handler(CommandHandler("lp", lp_command))
    application.add_handler(CommandHandler("routines", routines_command))

    # Add callback query handler for start menu navigation
    application.add_handler(CallbackQueryHandler(start_callback_handler, pattern="^start:"))

    # Add unified trade callback handler BEFORE cex/dex handlers (for connector switching)
    application.add_handler(CallbackQueryHandler(unified_trade_callback_handler, pattern="^trade:"))

    # Add callback query handlers for trading operations
    application.add_handler(CallbackQueryHandler(cex_callback_handler, pattern="^cex:"))
    application.add_handler(CallbackQueryHandler(dex_callback_handler, pattern="^dex:"))
    application.add_handler(CallbackQueryHandler(bots_callback_handler, pattern="^bots:"))
    application.add_handler(CallbackQueryHandler(routines_callback_handler, pattern="^routines:"))

    # Add admin callback handler
    from handlers.admin import admin_callback_handler
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin:"))

    # Add callback query handler for portfolio settings
    application.add_handler(get_portfolio_callback_handler())

    # Add callback query handler for config menu
    application.add_handler(get_config_callback_handler())

    # Add UNIFIED message handler for ALL text input
    # This single handler routes to: CLOB trading, DEX trading, and Config flows
    # based on context state. This avoids issues with multiple MessageHandlers
    # competing for the same filter.
    application.add_handler(get_modify_value_handler())

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
    # Sync server permissions (ensures all servers have ownership entries)
    await sync_server_permissions()

    commands = [
        BotCommand("start", "Welcome message and quick commands overview"),
        BotCommand("portfolio", "View detailed portfolio breakdown by account and connector"),
        BotCommand("bots", "Check status of all active trading bots"),
        BotCommand("trade", "Unified trading - CEX orders and DEX swaps"),
        BotCommand("lp", "Liquidity pool management and explorer"),
        BotCommand("routines", "Run configurable Python scripts"),
    ]
    await application.bot.set_my_commands(commands)

    # Restore scheduled routine jobs from persistence
    from handlers.routines import restore_scheduled_jobs
    await restore_scheduled_jobs(application)

    # Start file watcher
    asyncio.create_task(watch_and_reload(application))


async def watch_and_reload(application: Application) -> None:
    """Watch for file changes and reload handlers automatically."""
    try:
        from watchfiles import awatch
    except ImportError:
        logger.warning("watchfiles not installed. Auto-reload disabled. Install with: pip install watchfiles")
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

def get_persistence() -> PicklePersistence:
    """
    Build a persistence object that works both locally and in Docker.
    - Uses an env var override if provided.
    - Defaults to <project_root>/condor_bot_data.pickle.
    - Ensures the parent directory exists, but does NOT create the file.
    """
    base_dir = Path(__file__).parent
    default_path = base_dir / "condor_bot_data.pickle"

    persistence_path = Path(os.getenv("CONDOR_PERSISTENCE_FILE", default_path))

    # Make sure the directory exists; the file will be created by PTB
    persistence_path.parent.mkdir(parents=True, exist_ok=True)

    return PicklePersistence(filepath=persistence_path)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors gracefully."""
    if isinstance(context.error, NetworkError):
        logger.warning(f"Network error (will retry): {context.error}")
        return

    logger.exception("Exception while handling an update:", exc_info=context.error)


def main() -> None:
    """Run the bot."""
    # Setup persistence to save user data, chat data, and bot data
    # This will save trading context, last used parameters, etc.
    persistence = get_persistence()

    # Create the Application with persistence enabled
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .build()
    )

    # Register all handlers
    register_handlers(application)

    # Register error handler
    application.add_error_handler(error_handler)

    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
