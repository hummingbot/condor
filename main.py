import logging
import importlib
import sys
import asyncio
from pathlib import Path

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    PicklePersistence,
)

from handlers.portfolio import portfolio_command, get_portfolio_callback_handler
from handlers.bots import bots_command
from handlers.trade_ai import trade_command
from handlers.clob import clob_trading_command, clob_callback_handler
from handlers.dex import dex_trading_command, dex_callback_handler
from handlers.config import config_command, get_config_callback_handler, get_modify_value_handler
from handlers import clear_all_input_states
from utils.auth import restricted
from utils.config import TELEGRAM_TOKEN

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the conversation and display the main menu."""
    # Clear all pending input states to prevent interference
    clear_all_input_states(context)

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    username = update.effective_user.username or "No username"

    reply_text = rf"""
🚀 *Welcome to Condor\!* 🦅

Manage your trading bots efficiently and monitor their performance\.

🆔 *Your Chat Info*:
📱 Chat ID: `{chat_id}`
👤 User ID: `{user_id}`
🏷️ Username: @{username}

🎛️ *Quick Commands*:

📊 `/portfolio` \- View your portfolio summary and holdings
🤖 `/bots` \- Check status of all active trading bots
🏦 `/clob_trading` \- CLOB trading \(Spot & Perpetual\)
🔄 `/dex_trading` \- DEX trading \(Swaps & CLMM\)
⚙️ `/config` \- Configure API servers and credentials

🔍 *Need help?* Type `/help` for detailed command information\.

Get started on your automated trading journey with ease and precision\!
"""
    await update.message.reply_text(reply_text, parse_mode="MarkdownV2")


def reload_handlers():
    """Reload all handler modules."""
    modules_to_reload = [
        'handlers.portfolio',
        'handlers.bots',
        'handlers.trade_ai',
        'handlers.clob',
        'handlers.clob.menu',
        'handlers.clob.place_order',
        'handlers.clob.leverage',
        'handlers.clob.orders',
        'handlers.clob.positions',
        'handlers.clob.account',
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
        'utils.auth',
        'utils.telegram_formatters',
    ]

    for module_name in modules_to_reload:
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])
            logger.info(f"Reloaded module: {module_name}")


def register_handlers(application: Application) -> None:
    """Register all command handlers."""
    # Import fresh versions after reload
    from handlers.portfolio import portfolio_command, get_portfolio_callback_handler
    from handlers.bots import bots_command
    from handlers.trade_ai import trade_command
    from handlers.clob import clob_trading_command, clob_callback_handler
    from handlers.dex import dex_trading_command, dex_callback_handler
    from handlers.config import config_command, get_config_callback_handler, get_modify_value_handler

    # Clear existing handlers
    application.handlers.clear()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("portfolio", portfolio_command))
    application.add_handler(CommandHandler("bots", bots_command))
    application.add_handler(CommandHandler("trade", trade_command))
    application.add_handler(CommandHandler("clob_trading", clob_trading_command))
    application.add_handler(CommandHandler("dex_trading", dex_trading_command))
    application.add_handler(CommandHandler("config", config_command))

    # Add callback query handlers for trading operations
    application.add_handler(CallbackQueryHandler(clob_callback_handler, pattern="^clob:"))
    application.add_handler(CallbackQueryHandler(dex_callback_handler, pattern="^dex:"))

    # Add callback query handler for portfolio settings
    application.add_handler(get_portfolio_callback_handler())

    # Add callback query handler for config menu
    application.add_handler(get_config_callback_handler())

    # Add UNIFIED message handler for ALL text input
    # This single handler routes to: CLOB trading, DEX trading, and Config flows
    # based on context state. This avoids issues with multiple MessageHandlers
    # competing for the same filter.
    application.add_handler(get_modify_value_handler())

    logger.info("Handlers registered successfully")


async def post_init(application: Application) -> None:
    """Register bot commands after initialization."""
    commands = [
        BotCommand("start", "Welcome message and quick commands overview"),
        BotCommand("portfolio", "View detailed portfolio breakdown by account and connector"),
        BotCommand("bots", "Check status of all active trading bots"),
        BotCommand("clob_trading", "CLOB trading (Spot & Perpetual) with quick actions"),
        BotCommand("dex_trading", "DEX trading (Swaps & CLMM) via Gateway"),
        # BotCommand("trade", "AI-powered trading assistant"),
        BotCommand("config", "Configure API servers and credentials"),
    ]
    await application.bot.set_my_commands(commands)

    # Start file watcher
    asyncio.create_task(watch_and_reload(application))


async def watch_and_reload(application: Application) -> None:
    """Watch for file changes and reload handlers automatically."""
    try:
        from watchfiles import awatch
    except ImportError:
        logger.warning("watchfiles not installed. Auto-reload disabled. Install with: pip install watchfiles")
        return

    watch_path = Path(__file__).parent / "handlers"
    logger.info(f"👀 Watching for changes in: {watch_path}")

    async for changes in awatch(watch_path):
        logger.info(f"📝 Detected changes: {changes}")
        try:
            reload_handlers()
            register_handlers(application)
            logger.info("✅ Auto-reloaded handlers successfully")
        except Exception as e:
            logger.error(f"❌ Auto-reload failed: {e}", exc_info=True)


def main() -> None:
    """Run the bot."""
    # Setup persistence to save user data, chat data, and bot data
    # This will save trading context, last used parameters, etc.
    persistence = PicklePersistence(filepath="condor_bot_data.pickle")

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

    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
