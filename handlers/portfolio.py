"""
Portfolio command handler using hummingbot_api_client
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from utils.auth import restricted
from utils.telegram_formatters import format_portfolio_summary, format_portfolio_state, format_error_message, escape_markdown_v2
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

        # Get detailed portfolio state
        state = await client.portfolio.get_state()
        message = format_portfolio_state(
            state,
            server_name=server_name,
            server_status=server_status
        )

        # Create inline keyboard with portfolio options
        keyboard = [
            [
                InlineKeyboardButton("📊 Distribution", callback_data="portfolio:distribution"),
                InlineKeyboardButton("📈 Evolution", callback_data="portfolio:evolution")
            ],
            [
                InlineKeyboardButton("🏦 By Account", callback_data="portfolio:accounts"),
                InlineKeyboardButton("📋 Summary", callback_data="portfolio:refresh")
            ]
        ]

        # Add server switch button if multiple servers available
        if len(enabled_servers) > 1:
            keyboard.append([
                InlineKeyboardButton("🔄 Switch Server", callback_data="portfolio:switch_server")
            ])

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

            # Refresh portfolio with new server
            server_status_info = await server_manager.check_server_status(server_name)
            server_status = server_status_info.get("status", "online")

            state = await client.portfolio.get_state()
            message = format_portfolio_state(
                state,
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
                    InlineKeyboardButton("📋 Summary", callback_data="portfolio:refresh")
                ]
            ]

            if len(enabled_servers) > 1:
                keyboard.append([
                    InlineKeyboardButton("🔄 Switch Server", callback_data="portfolio:switch_server")
                ])

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
            logger.info(f"Distribution data: {distribution}")
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
                    InlineKeyboardButton("📋 Summary", callback_data="portfolio:refresh")
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
            import time
            from datetime import datetime, timezone

            now = int(time.time())
            week_ago = now - (7 * 24 * 60 * 60)

            # Fetch all historical data with pagination
            all_data = []
            cursor = None
            max_pages = 50  # Safety limit to prevent infinite loops
            pages_fetched = 0

            while pages_fetched < max_pages:
                if cursor:
                    history = await client.portfolio.get_history(
                        start_time=week_ago,
                        end_time=now,
                        limit=100,
                        cursor=cursor
                    )
                else:
                    history = await client.portfolio.get_history(
                        start_time=week_ago,
                        end_time=now,
                        limit=100
                    )

                data_points = history.get("data", [])
                all_data.extend(data_points)
                pages_fetched += 1

                # Check if there's more data
                pagination = history.get("pagination", {})
                has_more = pagination.get("has_more", False)
                cursor = pagination.get("next_cursor")

                if not has_more or not cursor:
                    break

            logger.info(f"Fetched {len(all_data)} history data points across {pages_fetched} pages (before filtering)")

            # Filter data to ensure we only have data within the last 7 days
            # Some APIs might return data outside the range
            filtered_data = []
            week_ago_dt = datetime.fromtimestamp(week_ago, tz=timezone.utc)
            now_dt = datetime.fromtimestamp(now, tz=timezone.utc)

            for point in all_data:
                timestamp_str = point.get("timestamp", "")
                if isinstance(timestamp_str, str):
                    try:
                        timestamp_dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                        # Only include if within the 7-day window
                        if week_ago_dt <= timestamp_dt <= now_dt:
                            filtered_data.append(point)
                        else:
                            logger.debug(f"Filtering out timestamp {timestamp_str} outside 7-day window")
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Invalid timestamp format during filtering: {timestamp_str} - {e}")

            logger.info(f"After filtering: {len(filtered_data)} data points within last 7 days")

            # Create combined history object
            combined_history = {"data": filtered_data}
            graph_bytes = generate_evolution_graph(combined_history)

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
                    InlineKeyboardButton("📋 Summary", callback_data="portfolio:refresh")
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
            logger.info(f"Accounts distribution data: {accounts_dist}")
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
                    InlineKeyboardButton("📋 Summary", callback_data="portfolio:refresh")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.message.reply_photo(
                photo=graph_bytes,
                caption="🏦 Portfolio Distribution by Account",
                reply_markup=reply_markup
            )

        elif action == "refresh":
            # Refresh portfolio state
            server_status_info = await server_manager.check_server_status(server_name)
            server_status = server_status_info.get("status", "online")

            state = await client.portfolio.get_state()
            message = format_portfolio_state(
                state,
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
                    InlineKeyboardButton("📋 Summary", callback_data="portfolio:refresh")
                ]
            ]

            if len(enabled_servers) > 1:
                keyboard.append([
                    InlineKeyboardButton("🔄 Switch Server", callback_data="portfolio:switch_server")
                ])

            reply_markup = InlineKeyboardMarkup(keyboard)

            # Remove buttons from current message
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception as e:
                logger.debug(f"Could not remove buttons: {e}")

            # Send new text message with portfolio state
            await query.message.reply_text(
                message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error handling portfolio callback: {e}", exc_info=True)
        await query.message.reply_text(f"Error: {str(e)}")
