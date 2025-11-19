import logging
import importlib
import sys
import asyncio
from pathlib import Path

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from handlers.portfolio import portfolio_command
from handlers.bots import bots_command
from handlers.trade_ai import trade_command
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

    reply_text = """
🚀 *Welcome to Condor\!* 🦅

Manage your trading bots efficiently and monitor their performance\.

🎛️ *Quick Commands*:

📊 `/portfolio` \\- View your portfolio summary and holdings
🤖 `/bots` \\- Check status of all active trading bots
💹 `/trade` \\- AI\\-powered trading assistant
⚙️ `/config` \\- Configure API servers and credentials


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
        'handlers.config',
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
    from handlers.portfolio import portfolio_command
    from handlers.bots import bots_command
    from handlers.trade_ai import trade_command
    from handlers.config import config_command, get_config_callback_handler, get_modify_value_handler, clear_config_state

    # Clear existing handlers
    application.handlers.clear()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("portfolio", portfolio_command))
    application.add_handler(CommandHandler("bots", bots_command))
    application.add_handler(CommandHandler("trade", trade_command))
    application.add_handler(CommandHandler("config", config_command))
    application.add_handler(CommandHandler("reload", reload_command))

    # Add callback query handler for config menu
    application.add_handler(get_config_callback_handler())

    # Add message handler for server modification text input
    application.add_handler(get_modify_value_handler())

    logger.info("Handlers registered successfully")


@restricted
async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reload all handlers without restarting the bot."""
    try:
        await update.message.reply_text("🔄 Reloading handlers...")
        reload_handlers()
        register_handlers(context.application)
        await update.message.reply_text("✅ Handlers reloaded successfully!")
        logger.info("Handlers reloaded via /reload command")
    except Exception as e:
        error_msg = f"❌ Reload failed: {str(e)}"
        await update.message.reply_text(error_msg)
        logger.error(f"Reload failed: {e}", exc_info=True)


async def post_init(application: Application) -> None:
    """Register bot commands after initialization."""
    commands = [
        BotCommand("start", "Welcome message and quick commands overview"),
        BotCommand("portfolio", "View detailed portfolio breakdown by account and connector"),
        BotCommand("bots", "Check status of all active trading bots"),
        # BotCommand("trade", "AI-powered trading assistant"),
        BotCommand("config", "Configure API servers and credentials"),
        BotCommand("reload", "Reload handlers without restarting"),
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
    # Create the Application and pass it your bot's token
    application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    # Register all handlers
    register_handlers(application)

    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
