"""
Portfolio command handler using hummingbot_api_client
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from utils.auth import restricted
from utils.telegram_formatters import (
    format_portfolio_summary,
    format_portfolio_state,
    format_portfolio_overview,
    format_error_message,
    escape_markdown_v2
)
from handlers.config import clear_config_state
from utils.portfolio_graphs import generate_distribution_graph, generate_evolution_graph
from utils.trading_data import get_portfolio_overview

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

        # Get active server from context or use first enabled
        if not context.user_data.get("active_portfolio_server"):
            context.user_data["active_portfolio_server"] = enabled_servers[0]

        server_name = context.user_data["active_portfolio_server"]

        # Validate server is still enabled
        if server_name not in enabled_servers:
            server_name = enabled_servers[0]
            context.user_data["active_portfolio_server"] = server_name

        client = await server_manager.get_client(server_name)

        # Check server status
        server_status_info = await server_manager.check_server_status(server_name)
        server_status = server_status_info.get("status", "online")

        # Get complete portfolio overview (balances + positions + orders)
        overview_data = await get_portfolio_overview(
            client,
            account_names=None,  # Get all accounts
            include_balances=True,
            include_perp_positions=True,
            include_lp_positions=True,
            include_active_orders=True
        )

        # Format the complete overview
        message = format_portfolio_overview(
            overview_data,
            server_name=server_name,
            server_status=server_status
        )

        # Create inline keyboard with portfolio options (graphs only)
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

        # Get active server from context or use first enabled
        if not context.user_data.get("active_portfolio_server"):
            context.user_data["active_portfolio_server"] = enabled_servers[0]

        server_name = context.user_data["active_portfolio_server"]

        # Validate server is still enabled
        if server_name not in enabled_servers:
            server_name = enabled_servers[0]
            context.user_data["active_portfolio_server"] = server_name

        client = await server_manager.get_client(server_name)
        # Extract action from callback data (handle multi-part actions like "select_server:local")
        callback_parts = query.data.split(":", 1)  # Split into ["portfolio", "rest"]
        action = callback_parts[1] if len(callback_parts) > 1 else query.data

        if action == "switch_server":
            # Show server selection menu
            keyboard = []
            for srv_name in enabled_servers:
                is_active = "✓ " if srv_name == server_name else ""
                keyboard.append([
                    InlineKeyboardButton(
                        f"{is_active}{srv_name}",
                        callback_data=f"portfolio:select_server:{srv_name}"
                    )
                ])
            keyboard.append([
                InlineKeyboardButton("« Back to Summary", callback_data="portfolio:refresh")
            ])
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.message.edit_text(
                f"🔄 *Select Server*\n\nCurrent: `{escape_markdown_v2(server_name)}`",
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            return

        elif action.startswith("select_server:"):
            # Extract server name from callback data
            selected_server = query.data.split("select_server:")[-1]
            if selected_server in enabled_servers:
                context.user_data["active_portfolio_server"] = selected_server
                server_name = selected_server
                client = await server_manager.get_client(server_name)

            # Refresh portfolio with new server - show complete overview
            server_status_info = await server_manager.check_server_status(server_name)
            server_status = server_status_info.get("status", "online")

            # Get complete portfolio overview
            overview_data = await get_portfolio_overview(
                client,
                account_names=None,
                include_balances=True,
                include_perp_positions=True,
                include_lp_positions=True,
                include_active_orders=True
            )

            message = format_portfolio_overview(
                overview_data,
                server_name=server_name,
                server_status=server_status
            )

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

            await query.message.edit_text(
                message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            return

        elif action == "distribution":
            # Generate and send distribution graph
            distribution = await client.portfolio.get_distribution()
            graph_bytes = generate_distribution_graph(distribution)

            # Remove buttons from current message
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception as e:
                logger.debug(f"Could not remove buttons: {e}")

            # Send new message with graph and buttons
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

            await query.message.reply_photo(
                photo=graph_bytes,
                caption="📊 Portfolio Distribution by Token",
                reply_markup=reply_markup
            )

        elif action == "evolution":
            # Generate and send evolution graph
            # Fetch historical data with 1-hour interval resampling for 7-day view
            # This gives us ~168 data points (7 days * 24 hours) which is perfect for visualization
            # Note: Not using start_time/end_time as the API doesn't filter correctly with those params
            history = await client.portfolio.get_history(
                limit=168,  # 7 days * 24 hours = 168 hours
                interval="1h"
            )

            logger.debug(f"Fetched {len(history.get('data', []))} history data points with 1h interval")
            if not history.get('data'):
                logger.warning("No data points in history response!")

            graph_bytes = generate_evolution_graph(history)

            # Remove buttons from current message
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception as e:
                logger.debug(f"Could not remove buttons: {e}")

            # Send new message with graph and buttons
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

            await query.message.reply_photo(
                photo=graph_bytes,
                caption="📈 Portfolio Evolution (Last 7 Days)",
                reply_markup=reply_markup
            )

        elif action == "accounts":
            # Show accounts distribution
            accounts_dist = await client.portfolio.get_accounts_distribution()
            graph_bytes = generate_distribution_graph(accounts_dist, by_account=True)

            # Remove buttons from current message
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception as e:
                logger.debug(f"Could not remove buttons: {e}")

            # Send new message with graph and buttons
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

            await query.message.reply_photo(
                photo=graph_bytes,
                caption="🏦 Portfolio Distribution by Account",
                reply_markup=reply_markup
            )

        elif action == "refresh":
            # Refresh complete portfolio overview
            server_status_info = await server_manager.check_server_status(server_name)
            server_status = server_status_info.get("status", "online")

            # Get complete portfolio overview
            overview_data = await get_portfolio_overview(
                client,
                account_names=None,
                include_balances=True,
                include_perp_positions=True,
                include_lp_positions=True,
                include_active_orders=True
            )

            message = format_portfolio_overview(
                overview_data,
                server_name=server_name,
                server_status=server_status
            )

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

            # Remove buttons from current message
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception as e:
                logger.debug(f"Could not remove buttons: {e}")

            # Send new text message with complete portfolio overview
            await query.message.reply_text(
                message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error handling portfolio callback: {e}", exc_info=True)
        await query.message.reply_text(f"Error: {str(e)}")
