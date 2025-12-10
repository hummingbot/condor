import logging
import importlib
import sys
import asyncio
from pathlib import Path

from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    PicklePersistence,
)

from handlers.portfolio import portfolio_command, get_portfolio_callback_handler
from handlers.bots import bots_command, bots_callback_handler
from handlers.cex import trade_command, cex_callback_handler
from handlers.dex import swap_command, lp_command, dex_callback_handler
from handlers.config import config_command, get_config_callback_handler, get_modify_value_handler
from handlers import clear_all_input_states
from utils.auth import restricted
from utils.config import TELEGRAM_TOKEN

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


def _get_start_menu_keyboard() -> InlineKeyboardMarkup:
    """Build the start menu inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("📊 Portfolio", callback_data="start:portfolio"),
            InlineKeyboardButton("🤖 Bots", callback_data="start:bots"),
        ],
        [
            InlineKeyboardButton("💱 Swap", callback_data="start:swap"),
            InlineKeyboardButton("📊 Trade", callback_data="start:trade"),
            InlineKeyboardButton("💧 LP", callback_data="start:lp"),
        ],
        [
            InlineKeyboardButton("🔌 Servers", callback_data="start:config_servers"),
            InlineKeyboardButton("🔑 Keys", callback_data="start:config_keys"),
            InlineKeyboardButton("🌐 Gateway", callback_data="start:config_gateway"),
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="start:help"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def _get_help_keyboard() -> InlineKeyboardMarkup:
    """Build the help menu inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("📊 Portfolio", callback_data="help:portfolio"),
            InlineKeyboardButton("🤖 Bots", callback_data="help:bots"),
        ],
        [
            InlineKeyboardButton("💱 Swap", callback_data="help:swap"),
            InlineKeyboardButton("📊 Trade", callback_data="help:trade"),
            InlineKeyboardButton("💧 LP", callback_data="help:lp"),
        ],
        [
            InlineKeyboardButton("⚙️ Config", callback_data="help:config"),
        ],
        [
            InlineKeyboardButton("🔙 Back to Menu", callback_data="help:back"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


HELP_TEXTS = {
    "main": r"""
❓ *Help \- Command Guide*

Select a command below to learn more about its features and usage:

📊 *Portfolio* \- View holdings and performance
🤖 *Bots* \- Monitor trading bot status
💱 *Swap* \- Quick token swaps via DEX
📊 *Trade* \- Order book trading \(CEX/CLOB\)
💧 *LP* \- Liquidity pool management
⚙️ *Config* \- System configuration
""",
    "portfolio": r"""
📊 *Portfolio Command*

View your complete portfolio summary across all connected accounts\.

*Features:*
• Real\-time balance overview by account
• PnL tracking with historical charts
• Holdings breakdown by asset
• Multi\-connector aggregation

*Usage:*
• Tap the button or type `/portfolio`
• Use ⚙️ Settings to adjust the time period \(1d, 3d, 7d, 30d\)
• View performance graphs and detailed breakdowns

*Tips:*
• Connect multiple accounts via Config to see aggregated portfolio
• PnL is calculated based on your configured time window
""",
    "bots": r"""
🤖 *Bots Command*

Monitor the status of all your active trading bots\.

*Features:*
• View all running bot instances
• Check bot health and uptime
• See active strategies per bot
• Monitor trading activity

*Usage:*
• Tap the button or type `/bots`
• View the status of each connected bot
• Check which strategies are currently active

*Tips:*
• Ensure your API servers are properly configured in Config
• Bots must be running on connected Hummingbot instances
""",
    "trade": r"""
📊 *Trade Command*

Trade on Central Limit Order Book exchanges \(Spot \& Perpetual\)\.

*Features:*
• Place market and limit orders
• Set leverage for perpetual trading
• View and manage open orders
• Monitor positions with PnL
• Quick account switching

*Usage:*
• Tap the button or type `/trade`
• Select an account and connector
• Use the menu to place orders or view positions

*Order Types:*
• 📝 *Place Order* \- Submit new orders
• ⚙️ *Set Leverage* \- Adjust perpetual leverage
• 🔍 *Orders Details* \- View/cancel open orders
• 📊 *Positions Details* \- Monitor active positions

*Tips:*
• Always verify the selected account before trading
• Use limit orders for better price control
""",
    "swap": r"""
💱 *Swap Command*

Quick token swaps on Decentralized Exchanges via Gateway\.

*Features:*
• Token swaps with real\-time quotes
• Multiple DEX router support
• Slippage configuration
• Swap history with status tracking

*Usage:*
• Tap the button or type `/swap`
• Select network and token pair
• Get quote and execute

*Operations:*
• 💰 *Quote* \- Get swap price estimates
• ✅ *Execute* \- Execute token swaps
• 🔍 *History* \- View past swaps

*Tips:*
• Always check quotes before executing swaps
• Gateway must be running for DEX operations
""",
    "lp": r"""
💧 *LP Command*

Manage liquidity positions on CLMM pools\.

*Features:*
• View LP positions with PnL
• Collect fees from positions
• Add/close positions
• Pool explorer with GeckoTerminal
• OHLCV charts and pool analytics

*Usage:*
• Tap the button or type `/lp`
• View your positions or explore pools
• Manage fees and positions

*Operations:*
• 📍 *Positions* \- View and manage LP positions
• 📋 *Pools* \- Browse available pools
• 🦎 *Explorer* \- GeckoTerminal pool discovery
• 📊 *Charts* \- View pool OHLCV data

*Tips:*
• Monitor V/TVL ratio for pool activity
• Check APR and fee tiers before adding liquidity
""",
    "config": r"""
⚙️ *Config Command*

Configure your trading infrastructure and credentials\.

*Sections:*

🔌 *API Servers*
• Add/remove Hummingbot instances
• Configure connection endpoints
• Test server connectivity

🔑 *API Keys*
• Manage exchange credentials
• Add new exchange API keys
• Securely store credentials

🌐 *Gateway*
• Configure Gateway container
• Set up DEX chain connections
• Manage wallet credentials

*Usage:*
• Tap the button or type `/config`
• Select the section you want to configure
• Follow the prompts to add or modify settings

*Tips:*
• Keep your API keys secure
• Test connections after adding new servers
• Gateway is required for DEX trading
""",
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the conversation and display the main menu."""
    from utils.config import AUTHORIZED_USERS

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    username = update.effective_user.username or "No username"

    # Check if user is authorized
    if user_id not in AUTHORIZED_USERS:
        reply_text = rf"""
🔒 *Access Restricted*

You are not authorized to use this bot\.

🆔 *Your Chat Info*:
📱 Chat ID: `{chat_id}`
👤 User ID: `{user_id}`

Share this information with the bot administrator to request access\.
"""
        await update.message.reply_text(reply_text, parse_mode="MarkdownV2")
        return

    # Clear all pending input states to prevent interference
    clear_all_input_states(context)

    reply_text = rf"""
🚀 *Welcome to Condor\!* 🦅

Manage your trading bots efficiently and monitor their performance\.

🆔 *Your Chat Info*:
📱 Chat ID: `{chat_id}`
👤 User ID: `{user_id}`
🏷️ Username: @{username}

Select a command below to get started:
"""
    keyboard = _get_start_menu_keyboard()
    await update.message.reply_text(reply_text, parse_mode="MarkdownV2", reply_markup=keyboard)


@restricted
async def start_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callbacks from the start menu."""
    query = update.callback_query
    await query.answer()

    data = query.data
    action = data.split(":")[1] if ":" in data else data

    # Handle navigation to commands
    if data.startswith("start:"):
        if action == "portfolio":
            await portfolio_command(update, context)
        elif action == "bots":
            await bots_command(update, context)
        elif action == "trade":
            await trade_command(update, context)
        elif action == "swap":
            await swap_command(update, context)
        elif action == "lp":
            await lp_command(update, context)
        elif action == "config_servers":
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
        elif action == "help":
            await query.edit_message_text(
                HELP_TEXTS["main"],
                parse_mode="MarkdownV2",
                reply_markup=_get_help_keyboard()
            )

    # Handle help submenu
    elif data.startswith("help:"):
        if action == "back":
            # Go back to main start menu
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

Select a command below to get started:
"""
            await query.edit_message_text(
                reply_text,
                parse_mode="MarkdownV2",
                reply_markup=_get_start_menu_keyboard()
            )
        elif action in HELP_TEXTS:
            # Show specific help with back button
            keyboard = [[InlineKeyboardButton("🔙 Back to Help", callback_data="start:help")]]
            await query.edit_message_text(
                HELP_TEXTS[action],
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )


def reload_handlers():
    """Reload all handler modules."""
    modules_to_reload = [
        'handlers.portfolio',
        'handlers.bots',
        'handlers.bots.menu',
        'handlers.bots.controllers',
        'handlers.bots._shared',
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
    from handlers.bots import bots_command, bots_callback_handler
    from handlers.cex import trade_command, cex_callback_handler
    from handlers.dex import swap_command, lp_command, dex_callback_handler
    from handlers.config import config_command, get_config_callback_handler, get_modify_value_handler

    # Clear existing handlers
    application.handlers.clear()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("portfolio", portfolio_command))
    application.add_handler(CommandHandler("bots", bots_command))
    application.add_handler(CommandHandler("swap", swap_command))
    application.add_handler(CommandHandler("trade", trade_command))
    application.add_handler(CommandHandler("lp", lp_command))
    application.add_handler(CommandHandler("config", config_command))

    # Add callback query handler for start menu navigation
    application.add_handler(CallbackQueryHandler(start_callback_handler, pattern="^(start:|help:)"))

    # Add callback query handlers for trading operations
    application.add_handler(CallbackQueryHandler(cex_callback_handler, pattern="^cex:"))
    application.add_handler(CallbackQueryHandler(dex_callback_handler, pattern="^dex:"))
    application.add_handler(CallbackQueryHandler(bots_callback_handler, pattern="^bots:"))

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
        BotCommand("swap", "Quick token swaps via DEX routers"),
        BotCommand("trade", "Order book trading (CEX/CLOB) with limit orders"),
        BotCommand("lp", "Liquidity pool management and explorer"),
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
