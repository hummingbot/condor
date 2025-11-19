"""
Portfolio command handler using hummingbot_api_client
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from utils.auth import restricted
from utils.telegram_formatters import format_portfolio_summary, format_portfolio_state, format_error_message
from handlers.config import clear_config_state
from utils.portfolio_graphs import generate_distribution_graph, generate_evolution_graph

logger = logging.getLogger(__name__)


@restricted
async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /portfolio command - Display detailed portfolio breakdown by account and connector

    Usage:
        /portfolio - Show detailed breakdown by account and connector
    """
    # Clear any config state to prevent interference
    clear_config_state(context)

    # Send "typing" status
    await update.message.reply_chat_action("typing")

    try:
        from servers import server_manager

        # Get first enabled server
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            error_message = format_error_message("No enabled API servers. Edit servers.yml to enable a server.")
            await update.message.reply_text(error_message, parse_mode="MarkdownV2")
            return

        # Get client from first enabled server
        client = await server_manager.get_client(enabled_servers[0])

        # Get detailed portfolio state
        state = await client.portfolio.get_state()
        message = format_portfolio_state(state)

        # Create inline keyboard with portfolio options
        keyboard = [
            [
                InlineKeyboardButton("📊 Distribution", callback_data="portfolio:distribution"),
                InlineKeyboardButton("📈 Evolution", callback_data="portfolio:evolution")
            ],
            [
                InlineKeyboardButton("🏦 By Account", callback_data="portfolio:accounts"),
                InlineKeyboardButton("🔄 Refresh", callback_data="portfolio:refresh")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error fetching portfolio: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to fetch portfolio: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")


@restricted
async def portfolio_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle inline button callbacks for portfolio views
    """
    query = update.callback_query
    await query.answer()

    # Send typing action
    await query.message.reply_chat_action("typing")

    try:
        from servers import server_manager

        # Get first enabled server
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            await query.message.reply_text("No enabled API servers available.")
            return

        client = await server_manager.get_client(enabled_servers[0])
        action = query.data.split(":")[-1]

        if action == "distribution":
            # Generate and send distribution graph
            distribution = await client.portfolio.get_distribution()
            graph_bytes = generate_distribution_graph(distribution)

            await query.message.reply_photo(
                photo=graph_bytes,
                caption="📊 Portfolio Distribution by Token"
            )

        elif action == "evolution":
            # Generate and send evolution graph
            import time
            week_ago = int(time.time()) - (7 * 24 * 60 * 60)
            history = await client.portfolio.get_history(start_time=week_ago, limit=100)
            graph_bytes = generate_evolution_graph(history)

            await query.message.reply_photo(
                photo=graph_bytes,
                caption="📈 Portfolio Evolution (Last 7 Days)"
            )

        elif action == "accounts":
            # Show accounts distribution
            accounts_dist = await client.portfolio.get_accounts_distribution()
            graph_bytes = generate_distribution_graph(accounts_dist, by_account=True)

            await query.message.reply_photo(
                photo=graph_bytes,
                caption="🏦 Portfolio Distribution by Account"
            )

        elif action == "refresh":
            # Refresh portfolio state
            state = await client.portfolio.get_state()
            message = format_portfolio_state(state)

            keyboard = [
                [
                    InlineKeyboardButton("📊 Distribution", callback_data="portfolio:distribution"),
                    InlineKeyboardButton("📈 Evolution", callback_data="portfolio:evolution")
                ],
                [
                    InlineKeyboardButton("🏦 By Account", callback_data="portfolio:accounts"),
                    InlineKeyboardButton("🔄 Refresh", callback_data="portfolio:refresh")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error handling portfolio callback: {e}", exc_info=True)
        await query.message.reply_text(f"Error: {str(e)}")
