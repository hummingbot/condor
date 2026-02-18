"""
Archived Bots Module - View historical bot data and generate reports

Provides:
- List of archived bot databases with pagination
- Detailed view for individual archived bots
- Timeline chart showing all bots with PnL
- Performance chart for individual bots
- Full report generation (JSON + PNG) saved locally
"""

import logging
from typing import Any, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from utils.telegram_formatters import (
    escape_markdown_v2,
    format_error_message,
    format_number,
)

from ._shared import get_bots_client, get_cached, set_cached

logger = logging.getLogger(__name__)

# Cache TTL for archived data (longer since it's static)
ARCHIVED_CACHE_TTL = 300  # 5 minutes

# Pagination settings
BOTS_PER_PAGE = 5


# ============================================
# STATE MANAGEMENT
# ============================================


def clear_archived_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear archived-related state from context."""
    context.user_data.pop("archived_databases", None)
    context.user_data.pop("archived_current_db", None)
    context.user_data.pop("archived_page", None)
    context.user_data.pop("archived_summaries", None)
    context.user_data.pop("archived_total_count", None)


def _get_db_path_by_index(
    context: ContextTypes.DEFAULT_TYPE, index: int
) -> Optional[str]:
    """Get db_path from stored databases list by index."""
    databases = context.user_data.get("archived_databases", [])
    if 0 <= index < len(databases):
        return databases[index]
    return None


# ============================================
# DATA FETCHING
# ============================================


async def fetch_archived_databases(client) -> List[str]:
    """Fetch list of archived bot database paths."""
    try:
        result = await client.archived_bots.list_databases()
        # Result is ArchivedBotListResponse with 'bots' list
        if isinstance(result, dict):
            return result.get("bots", [])
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.error(f"Error fetching archived databases: {e}", exc_info=True)
        return []


async def fetch_database_status(client, db_path: str) -> Optional[Dict[str, Any]]:
    """Fetch health status for a database."""
    try:
        return await client.archived_bots.get_database_status(db_path)
    except Exception as e:
        logger.debug(f"Error fetching status for {db_path}: {e}")
        return None


def is_database_healthy(status: Optional[Dict[str, Any]]) -> bool:
    """
    Check if a database has valid trade data for analysis.

    We only need trade_fill to be correct for PnL analysis,
    even if executors/positions have errors.
    """
    if not status:
        return False

    # Check nested status object for trade_fill
    nested_status = status.get("status", {})
    if isinstance(nested_status, dict):
        # Trade fill data is sufficient for analysis
        if nested_status.get("trade_fill") == "Correct":
            return True
        # Also accept if orders are correct (backup)
        if nested_status.get("orders") == "Correct":
            return True

    # Fallback to overall healthy flag
    if status.get("healthy") == True:
        return True

    return False


async def get_healthy_databases(client, databases: List[str]) -> List[str]:
    """Filter databases to only include healthy ones."""
    healthy = []
    for db_path in databases:
        status = await fetch_database_status(client, db_path)
        if is_database_healthy(status):
            healthy.append(db_path)
        else:
            logger.debug(f"Skipping unhealthy database: {db_path}")
    return healthy


async def fetch_database_summary(client, db_path: str) -> Optional[Dict[str, Any]]:
    """Fetch summary for a specific archived database."""
    try:
        return await client.archived_bots.get_database_summary(db_path)
    except Exception as e:
        logger.error(f"Error fetching summary for {db_path}: {e}", exc_info=True)
        return None


async def fetch_database_performance(client, db_path: str) -> Optional[Dict[str, Any]]:
    """Fetch performance metrics for a specific archived database."""
    try:
        return await client.archived_bots.get_database_performance(db_path)
    except Exception as e:
        logger.error(f"Error fetching performance for {db_path}: {e}", exc_info=True)
        return None


async def fetch_database_trades(
    client, db_path: str, limit: int = 500, offset: int = 0
) -> Optional[Dict[str, Any]]:
    """Fetch trades for a specific archived database (single page)."""
    try:
        return await client.archived_bots.get_database_trades(
            db_path, limit=limit, offset=offset
        )
    except Exception as e:
        logger.error(f"Error fetching trades for {db_path}: {e}", exc_info=True)
        return None


async def fetch_all_trades(client, db_path: str) -> List[Dict[str, Any]]:
    """Fetch ALL trades with pagination."""
    all_trades = []
    offset = 0
    limit = 500

    while True:
        response = await fetch_database_trades(
            client, db_path, limit=limit, offset=offset
        )
        if not response:
            break

        trades = response.get("trades", [])
        if not trades:
            break

        all_trades.extend(trades)

        # Check pagination
        pagination = response.get("pagination", {})
        if not pagination.get("has_more", False):
            break

        offset += limit

        # Safety limit to avoid infinite loops
        if len(all_trades) > 50000:
            logger.warning(
                f"Trade limit reached for {db_path}, stopping at {len(all_trades)}"
            )
            break

    logger.debug(f"Fetched {len(all_trades)} total trades for {db_path}")
    return all_trades


async def fetch_database_orders(
    client, db_path: str, limit: int = 1000, offset: int = 0
) -> Optional[Dict[str, Any]]:
    """Fetch orders for a specific archived database."""
    try:
        return await client.archived_bots.get_database_orders(
            db_path, limit=limit, offset=offset
        )
    except Exception as e:
        logger.error(f"Error fetching orders for {db_path}: {e}", exc_info=True)
        return None


async def fetch_database_executors(client, db_path: str) -> Optional[Dict[str, Any]]:
    """Fetch executors for a specific archived database."""
    try:
        return await client.archived_bots.get_database_executors(db_path)
    except Exception as e:
        logger.error(f"Error fetching executors for {db_path}: {e}", exc_info=True)
        return None


# ============================================
# FORMATTING HELPERS
# ============================================


def _format_pnl(pnl: float) -> str:
    """Format PnL with color indicator."""
    if pnl >= 0:
        return f"+${format_number(pnl)}"
    else:
        return f"-${format_number(abs(pnl))}"


def _format_datetime(dt) -> str:
    """Format datetime for display."""
    if dt is None:
        return "N/A"
    if isinstance(dt, str):
        # Parse ISO format and format nicely
        try:
            from datetime import datetime

            if "T" in dt:
                parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            else:
                parsed = datetime.fromisoformat(dt)
            return parsed.strftime("%b %d %H:%M")
        except Exception:
            return dt[:16] if len(dt) > 16 else dt
    return str(dt)


def _format_duration(start_time, end_time) -> str:
    """Calculate and format duration between two times."""
    if not start_time or not end_time:
        return "N/A"

    try:
        from datetime import datetime

        def parse_dt(dt):
            if isinstance(dt, datetime):
                return dt
            if isinstance(dt, str):
                if "T" in dt:
                    return datetime.fromisoformat(dt.replace("Z", "+00:00"))
                return datetime.fromisoformat(dt)
            return None

        start = parse_dt(start_time)
        end = parse_dt(end_time)

        if not start or not end:
            return "N/A"

        # Remove timezone info for calculation if present
        if start.tzinfo:
            start = start.replace(tzinfo=None)
        if end.tzinfo:
            end = end.replace(tzinfo=None)

        delta = end - start
        days = delta.days
        hours = delta.seconds // 3600

        if days > 0:
            return f"{days}d {hours}h"
        elif hours > 0:
            mins = (delta.seconds % 3600) // 60
            return f"{hours}h {mins}m"
        else:
            mins = delta.seconds // 60
            return f"{mins}m"
    except Exception as e:
        logger.debug(f"Error calculating duration: {e}")
        return "N/A"


def _extract_bot_name(db_path: str) -> str:
    """Extract readable bot name from database path."""
    # db_path might be like "/path/to/bot_name.db" or just "bot_name.db"
    import os

    name = os.path.basename(db_path)
    if name.endswith(".db"):
        name = name[:-3]
    return name


# ============================================
# MENU DISPLAY
# ============================================


async def show_archived_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
) -> None:
    """Display the archived bots menu with pagination."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        # Fetch databases (with caching) - only healthy databases
        cache_key = "archived_databases"
        databases = get_cached(context.user_data, cache_key, ARCHIVED_CACHE_TTL)
        if databases is None:
            all_databases = await fetch_archived_databases(client)
            # Filter to only healthy databases to avoid 500 errors
            databases = await get_healthy_databases(client, all_databases)
            set_cached(context.user_data, cache_key, databases)
            logger.info(
                f"Found {len(databases)} healthy databases out of {len(all_databases)} total"
            )
            # Store total count for message
            context.user_data["archived_total_count"] = len(all_databases)

        # Store in context for later use
        context.user_data["archived_databases"] = databases
        context.user_data["archived_page"] = page

        if not databases:
            total_count = context.user_data.get("archived_total_count", 0)
            if total_count > 0:
                message = escape_markdown_v2(
                    f"📜 No readable archived bots found.\n\n{total_count} databases exist but may be corrupted or incomplete."
                )
            else:
                message = escape_markdown_v2(
                    "📜 No archived bots found.\n\nArchived bots will appear here after you stop a running bot."
                )
            keyboard = [
                [InlineKeyboardButton("🔙 Back", callback_data="bots:main_menu")]
            ]

            if query:
                await query.message.edit_text(
                    message,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            return

        # Fetch summaries for current page (with caching)
        summaries_cache_key = "archived_summaries"
        all_summaries = get_cached(
            context.user_data, summaries_cache_key, ARCHIVED_CACHE_TTL
        )
        if all_summaries is None:
            all_summaries = {}
            for db_path in databases:
                summary = await fetch_database_summary(client, db_path)
                if summary:
                    all_summaries[db_path] = summary
            set_cached(context.user_data, summaries_cache_key, all_summaries)

        context.user_data["archived_summaries"] = all_summaries

        # Pagination
        total_pages = (len(databases) + BOTS_PER_PAGE - 1) // BOTS_PER_PAGE
        start_idx = page * BOTS_PER_PAGE
        end_idx = min(start_idx + BOTS_PER_PAGE, len(databases))
        page_databases = databases[start_idx:end_idx]

        # Build message
        lines = [r"*📜 Archived Bots*", ""]

        for i, db_path in enumerate(page_databases, start=start_idx + 1):
            summary = all_summaries.get(db_path, {})
            bot_name = summary.get("bot_name") or _extract_bot_name(db_path)
            total_trades = summary.get("total_trades", 0)
            start_time = summary.get("start_time")
            end_time = summary.get("end_time")

            # Format time range
            if start_time and end_time:
                time_range = (
                    f"{_format_datetime(start_time)} → {_format_datetime(end_time)}"
                )
            else:
                time_range = "Time unknown"

            lines.append(f"`{i}.` *{escape_markdown_v2(bot_name)}*")
            lines.append(f"   {escape_markdown_v2(time_range)} • {total_trades} trades")
            lines.append("")

        message = "\n".join(lines)

        # Build keyboard
        keyboard = []

        # Bot selection buttons - use index into databases list
        for idx, db_path in enumerate(page_databases):
            global_idx = start_idx + idx  # Global index in full databases list
            summary = all_summaries.get(db_path, {})
            bot_name = summary.get("bot_name") or _extract_bot_name(db_path)
            display_name = bot_name[:25] + "..." if len(bot_name) > 25 else bot_name
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"📊 {display_name}",
                        callback_data=f"bots:archived_select:{global_idx}",
                    )
                ]
            )

        # Pagination row
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(
                    InlineKeyboardButton(
                        "◀️", callback_data=f"bots:archived_page:{page-1}"
                    )
                )
            nav_row.append(
                InlineKeyboardButton(
                    f"{page+1}/{total_pages}", callback_data="bots:noop"
                )
            )
            if page < total_pages - 1:
                nav_row.append(
                    InlineKeyboardButton(
                        "▶️", callback_data=f"bots:archived_page:{page+1}"
                    )
                )
            keyboard.append(nav_row)

        # Action buttons
        keyboard.append(
            [
                InlineKeyboardButton(
                    "📊 Timeline", callback_data="bots:archived_timeline"
                ),
                InlineKeyboardButton(
                    "🔄 Refresh", callback_data="bots:archived_refresh"
                ),
            ]
        )
        keyboard.append(
            [
                InlineKeyboardButton("🔙 Back", callback_data="bots:main_menu"),
            ]
        )

        reply_markup = InlineKeyboardMarkup(keyboard)

        if query:
            try:
                await query.message.edit_text(
                    message, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
            except BadRequest as e:
                if "no text in the message" in str(e).lower():
                    await query.message.delete()
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=message,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup,
                    )
                elif "Message is not modified" not in str(e):
                    raise
        else:
            await update.message.reply_text(
                message, parse_mode="MarkdownV2", reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error showing archived menu: {e}", exc_info=True)
        error_msg = format_error_message(f"Failed to fetch archived bots: {str(e)}")
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="bots:main_menu")]]

        if query:
            await query.message.edit_text(
                error_msg,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )


async def show_archived_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db_index: int
) -> None:
    """Show detailed view for a specific archived bot."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        db_path = _get_db_path_by_index(context, db_index)
        if not db_path:
            await query.message.reply_text("Bot not found. Please refresh the list.")
            return

        context.user_data["archived_current_db"] = db_path
        context.user_data["archived_current_idx"] = db_index

        client, _ = await get_bots_client(chat_id, context.user_data)

        # Fetch summary
        summary = await fetch_database_summary(client, db_path)

        if not summary:
            error_msg = format_error_message("Could not fetch bot data")
            keyboard = [
                [InlineKeyboardButton("🔙 Back", callback_data="bots:archived")]
            ]
            await query.message.edit_text(
                error_msg,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        # Extract data from summary (actual API structure)
        bot_name = _extract_bot_name(db_path)
        total_trades = summary.get("total_trades", 0)
        total_orders = summary.get("total_orders", 0)
        trading_pairs = summary.get("trading_pairs", [])
        exchanges = summary.get("exchanges", [])

        # Fetch trades to calculate PnL and time range
        # Only fetch first page initially for quick display
        trades_response = await fetch_database_trades(
            client, db_path, limit=500, offset=0
        )
        trades = trades_response.get("trades", []) if trades_response else []

        # Calculate PnL and time range from trades
        from .archived_chart import (
            calculate_pnl_from_trades,
            get_time_range_from_trades,
        )

        # For detail view, use first page of trades for quick PnL estimate
        pnl_data = calculate_pnl_from_trades(trades)
        total_pnl = pnl_data.get("total_pnl", 0)
        total_fees = pnl_data.get("total_fees", 0)
        total_volume = pnl_data.get("total_volume", 0)

        # Get time range from trades
        start_time, end_time = get_time_range_from_trades(trades)

        # Check if there are more trades (for accurate PnL)
        pagination = trades_response.get("pagination", {}) if trades_response else {}
        has_more = pagination.get("has_more", False)
        pnl_note = " (partial)" if has_more else ""

        # Build message
        lines = [
            f"*📊 {escape_markdown_v2(bot_name)}*",
            "",
        ]

        # Time info
        if start_time and end_time:
            duration = _format_duration(start_time, end_time)
            lines.append(
                f"⏱ {escape_markdown_v2(_format_datetime(start_time))} → {escape_markdown_v2(_format_datetime(end_time))}"
            )
            lines.append(f"   Duration: {escape_markdown_v2(duration)}")
            lines.append("")

        # PnL - highlight color
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        lines.append(
            f"{pnl_emoji} *PnL:* `{escape_markdown_v2(_format_pnl(total_pnl))}`{escape_markdown_v2(pnl_note)}"
        )

        # Other metrics
        if total_volume:
            lines.append(
                f"📊 *Volume:* ${escape_markdown_v2(format_number(total_volume))}"
            )
        if total_fees:
            lines.append(f"💰 *Fees:* ${escape_markdown_v2(format_number(total_fees))}")

        lines.append("")
        lines.append(f"📝 *Trades:* {total_trades} • *Orders:* {total_orders}")

        if trading_pairs:
            lines.append(
                f"📈 *Pairs:* {escape_markdown_v2(', '.join(trading_pairs[:5]))}"
            )
        if exchanges:
            lines.append(
                f"🏦 *Exchanges:* {escape_markdown_v2(', '.join(exchanges[:3]))}"
            )

        message = "\n".join(lines)

        # Build keyboard - use index for callbacks
        keyboard = [
            [
                InlineKeyboardButton(
                    "📈 Chart", callback_data=f"bots:archived_chart:{db_index}"
                ),
                InlineKeyboardButton(
                    "💾 Report", callback_data=f"bots:archived_report:{db_index}"
                ),
            ],
            [
                InlineKeyboardButton("🔙 Back", callback_data="bots:archived"),
            ],
        ]

        await query.message.edit_text(
            message,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"Error showing archived detail: {e}", exc_info=True)
        error_msg = format_error_message(f"Failed to fetch bot details: {str(e)}")
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="bots:archived")]]
        await query.message.edit_text(
            error_msg,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


# ============================================
# CHART HANDLERS
# ============================================


async def show_timeline_chart(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Generate and show timeline chart for all archived bots."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        # Show loading message
        loading_msg = await query.message.reply_text(
            escape_markdown_v2(
                "⏳ Generating timeline... Fetching trade data for all bots."
            ),
            parse_mode="MarkdownV2",
        )

        # Get cached data or fetch
        databases = context.user_data.get("archived_databases", [])
        summaries = context.user_data.get("archived_summaries", {})

        if not databases:
            client, _ = await get_bots_client(chat_id, context.user_data)
            all_databases = await fetch_archived_databases(client)
            # Filter to only healthy databases
            databases = await get_healthy_databases(client, all_databases)
            if not databases:
                await loading_msg.edit_text(
                    escape_markdown_v2("No healthy archived bots to display."),
                    parse_mode="MarkdownV2",
                )
                return

        client, _ = await get_bots_client(chat_id, context.user_data)
        bots_data = []

        # Import chart functions
        from .archived_chart import calculate_pnl_from_trades

        for db_path in databases:
            summary = summaries.get(db_path) or await fetch_database_summary(
                client, db_path
            )

            if summary:
                # Fetch all trades for this bot to calculate accurate PnL
                trades = await fetch_all_trades(client, db_path)

                # Calculate PnL from trades
                pnl_data = calculate_pnl_from_trades(trades)

                bots_data.append(
                    {
                        "db_path": db_path,
                        "summary": summary,
                        "trades": trades,
                        "pnl_data": pnl_data,
                    }
                )

        if not bots_data:
            await loading_msg.edit_text(
                escape_markdown_v2("No data available for timeline chart."),
                parse_mode="MarkdownV2",
            )
            return

        # Import chart generation
        from .archived_chart import generate_timeline_chart

        chart_bytes = generate_timeline_chart(bots_data)

        if chart_bytes:
            # Calculate total PnL from all bots
            total_pnl = sum(
                b.get("pnl_data", {}).get("total_pnl", 0) for b in bots_data
            )

            caption = (
                f"📊 *Archived Bots Timeline*\n"
                f"Total: {len(bots_data)} bots • PnL: `{escape_markdown_v2(_format_pnl(total_pnl))}`"
            )

            keyboard = [
                [InlineKeyboardButton("🔙 Back", callback_data="bots:archived")]
            ]

            # Delete loading message and send chart
            await loading_msg.delete()

            await context.bot.send_photo(
                chat_id=chat_id,
                photo=chart_bytes,
                caption=caption,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            await loading_msg.edit_text(
                escape_markdown_v2("Failed to generate timeline chart."),
                parse_mode="MarkdownV2",
            )

    except Exception as e:
        logger.error(f"Error generating timeline chart: {e}", exc_info=True)
        error_msg = format_error_message(f"Failed to generate chart: {str(e)}")
        await query.message.reply_text(error_msg, parse_mode="MarkdownV2")


async def show_bot_chart(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db_index: int
) -> None:
    """Generate and show performance chart for a specific bot."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        db_path = _get_db_path_by_index(context, db_index)
        if not db_path:
            await query.message.reply_text("Bot not found. Please refresh the list.")
            return

        # Show loading message
        loading_msg = await query.message.reply_text(
            escape_markdown_v2("⏳ Generating chart... Fetching all trade data."),
            parse_mode="MarkdownV2",
        )

        client, _ = await get_bots_client(chat_id, context.user_data)

        # Fetch summary and ALL trades
        summary = await fetch_database_summary(client, db_path)
        trades = await fetch_all_trades(client, db_path)

        if not summary:
            await loading_msg.edit_text(
                escape_markdown_v2("Could not fetch bot data for chart."),
                parse_mode="MarkdownV2",
            )
            return

        # Import chart generation
        from .archived_chart import (
            calculate_pnl_from_trades,
            generate_performance_chart,
        )

        # Calculate PnL from trades
        pnl_data = calculate_pnl_from_trades(trades)
        total_pnl = pnl_data.get("total_pnl", 0)

        # Generate chart (pass db_path for bot name extraction)
        chart_bytes = generate_performance_chart(summary, None, trades, db_path=db_path)

        if chart_bytes:
            bot_name = _extract_bot_name(db_path)

            caption = (
                f"📊 *{escape_markdown_v2(bot_name)}*\n"
                f"PnL: `{escape_markdown_v2(_format_pnl(total_pnl))}` • "
                f"Trades: {len(trades)}"
            )

            keyboard = [
                [
                    InlineKeyboardButton(
                        "💾 Report", callback_data=f"bots:archived_report:{db_index}"
                    ),
                    InlineKeyboardButton(
                        "🔙 Back", callback_data=f"bots:archived_select:{db_index}"
                    ),
                ]
            ]

            # Delete loading message and send chart
            await loading_msg.delete()

            await context.bot.send_photo(
                chat_id=chat_id,
                photo=chart_bytes,
                caption=caption,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            await loading_msg.edit_text(
                escape_markdown_v2("Failed to generate performance chart."),
                parse_mode="MarkdownV2",
            )

    except Exception as e:
        logger.error(f"Error generating bot chart: {e}", exc_info=True)
        error_msg = format_error_message(f"Failed to generate chart: {str(e)}")
        await query.message.reply_text(error_msg, parse_mode="MarkdownV2")


# ============================================
# REPORT GENERATION
# ============================================


async def handle_generate_report(
    update: Update, context: ContextTypes.DEFAULT_TYPE, db_index: int
) -> None:
    """Generate and save a full report for a specific bot."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        db_path = _get_db_path_by_index(context, db_index)
        if not db_path:
            await query.message.reply_text("Bot not found. Please refresh the list.")
            return

        # Show progress message
        progress_msg = await query.message.reply_text(
            escape_markdown_v2("⏳ Generating report... This may take a moment."),
            parse_mode="MarkdownV2",
        )

        # Import report generation
        from .archived_report import save_full_report

        client, _ = await get_bots_client(chat_id, context.user_data)
        json_path, png_path = await save_full_report(client, db_path)

        # Update message with success
        if json_path:
            lines = [
                "✅ *Report Generated\\!*",
                "",
                f"📄 JSON: `{escape_markdown_v2(json_path)}`",
            ]
            if png_path:
                lines.append(f"📊 Chart: `{escape_markdown_v2(png_path)}`")

            message = "\n".join(lines)

            keyboard = [
                [
                    InlineKeyboardButton(
                        "🔙 Back", callback_data=f"bots:archived_select:{db_index}"
                    )
                ]
            ]

            await progress_msg.edit_text(
                message,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            await progress_msg.edit_text(
                escape_markdown_v2("❌ Failed to generate report."),
                parse_mode="MarkdownV2",
            )

    except Exception as e:
        logger.error(f"Error generating report: {e}", exc_info=True)
        error_msg = format_error_message(f"Failed to generate report: {str(e)}")
        await query.message.reply_text(error_msg, parse_mode="MarkdownV2")


# ============================================
# REFRESH HANDLER
# ============================================


async def handle_archived_refresh(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Refresh archived bots data."""
    # Clear cache
    context.user_data.pop("_bots_cache", None)
    clear_archived_state(context)

    # Re-show menu
    await show_archived_menu(update, context, page=0)
