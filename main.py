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

from handlers.portfolio import portfolio_command, portfolio_callback_handler
from handlers.bots import bots_command
from handlers.trade_ai import trade_command
from handlers.clob_trading import clob_trading_command, clob_callback_handler, get_clob_message_handler
from handlers.dex_trading import dex_trading_command, dex_callback_handler, get_dex_message_handler
from handlers.config import config_command, get_config_callback_handler, get_modify_value_handler, clear_config_state
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
    # Clear any config state to prevent interference
    clear_config_state(context)

    reply_text = r"""
🚀 *Welcome to Condor\!* 🦅

Manage your trading bots efficiently and monitor their performance\.

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
        'handlers.clob_trading',
        'handlers.dex_trading',
        'handlers.config',
        'handlers.config.servers',
        'handlers.config.api_keys',
        'handlers.config.gateway',
        'handlers.config.trading_context',
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
    from handlers.portfolio import portfolio_command, portfolio_callback_handler
    from handlers.bots import bots_command
    from handlers.trade_ai import trade_command
    from handlers.clob_trading import clob_trading_command, clob_callback_handler, get_clob_message_handler
    from handlers.dex_trading import dex_trading_command, dex_callback_handler, get_dex_message_handler
    from handlers.config import config_command, get_config_callback_handler, get_modify_value_handler, clear_config_state

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

    # Add callback query handler for portfolio views
    application.add_handler(CallbackQueryHandler(portfolio_callback_handler, pattern="^portfolio:"))

    # Add callback query handlers for trading operations
    application.add_handler(CallbackQueryHandler(clob_callback_handler, pattern="^clob:"))
    application.add_handler(CallbackQueryHandler(dex_callback_handler, pattern="^dex:"))

    # Add callback query handler for config menu
    application.add_handler(get_config_callback_handler())

    # Add message handlers for trading text input (MUST come before config handler)
    # Trading handlers check specific states and should have priority
    application.add_handler(get_clob_message_handler())
    application.add_handler(get_dex_message_handler())

    # Add message handler for server modification text input
    # This comes last as a catch-all for config operations
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
