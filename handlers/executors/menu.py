"""
Executors menu - Main menu and executor list display

Provides:
- Main executors menu with running executors summary
- Paginated list of running executors
- Executor detail view with config and performance
"""

import logging
from typing import Dict, Any, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2, format_error_message
from ._shared import (
    get_executors_client,
    clear_executors_state,
    search_running_executors,
    stop_executor,
    format_executor_status_line,
    format_executor_pnl,
    SIDE_LONG,
    invalidate_cache,
)

logger = logging.getLogger(__name__)

# Pagination
EXECUTORS_PER_PAGE = 8


# ============================================
# MAIN MENU
# ============================================

async def show_executors_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display the main executors menu with running summary

    Args:
        update: Telegram update
        context: Telegram context
    """
    clear_executors_state(context)

    query = update.callback_query
    msg = update.message or (query.message if query else None)
    chat_id = update.effective_chat.id

    if not msg:
        logger.error("No message object available for show_executors_menu")
        return

    try:
        client, server_name = await get_executors_client(chat_id, context.user_data)

        # Fetch running executors
        executors = await search_running_executors(client, status="running", limit=50)

        # Calculate total PnL
        total_pnl = 0.0
        for ex in executors:
            pnl = ex.get("pnl_quote", 0) or ex.get("unrealized_pnl_quote", 0) or 0
            total_pnl += pnl

        # Store for later use
        context.user_data["running_executors"] = executors
        context.user_data["current_server_name"] = server_name

        # Build message
        lines = [
            f"*Executors* \\| _{escape_markdown_v2(server_name)}_",
            "",
        ]

        if executors:
            lines.append(f"*Running* \\({len(executors)}\\)")

            # Show first few executors
            for ex in executors[:5]:
                status_line = format_executor_status_line(ex)
                lines.append(f"  {escape_markdown_v2(status_line)}")

            if len(executors) > 5:
                lines.append(f"  _\\.\\.\\.and {len(executors) - 5} more_")

            lines.append("")
            pnl_str = format_executor_pnl(total_pnl)
            lines.append(f"Total PnL: {escape_markdown_v2(pnl_str)}")
        else:
            lines.append("_No running executors_")

        # Build keyboard
        keyboard = []

        if executors:
            keyboard.append([
                InlineKeyboardButton(f"View All ({len(executors)})", callback_data="executors:list"),
            ])

        keyboard.append([
            InlineKeyboardButton("+ Create", callback_data="executors:create"),
        ])

        keyboard.append([
            InlineKeyboardButton("Refresh", callback_data="executors:menu"),
            InlineKeyboardButton("Close", callback_data="executors:close"),
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = "\n".join(lines)

        if query:
            try:
                await query.message.edit_text(
                    message_text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
            except BadRequest as e:
                if "no text in the message" in str(e).lower():
                    await query.message.delete()
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=message_text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                elif "Message is not modified" not in str(e):
                    raise
        else:
            await msg.reply_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error showing executors menu: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to fetch executors: {str(e)}")

        keyboard = [[
            InlineKeyboardButton("Retry", callback_data="executors:menu"),
            InlineKeyboardButton("Close", callback_data="executors:close"),
        ]]

        if query:
            try:
                await query.message.edit_text(
                    error_message,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except BadRequest:
                pass
        else:
            await msg.reply_text(
                error_message,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )


# ============================================
# RUNNING EXECUTORS LIST
# ============================================

async def show_running_executors(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display paginated list of running executors

    Args:
        update: Telegram update
        context: Telegram context
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    # Get page from context or default to 0
    page = context.user_data.get("executor_list_page", 0)

    try:
        # Fetch fresh data
        client, server_name = await get_executors_client(chat_id, context.user_data)
        executors = await search_running_executors(client, status="running", limit=100)

        context.user_data["running_executors"] = executors

        if not executors:
            keyboard = [[InlineKeyboardButton("Back", callback_data="executors:menu")]]
            await query.message.edit_text(
                "*Running Executors*\n\n_No executors running_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        # Calculate pagination
        total_pages = (len(executors) + EXECUTORS_PER_PAGE - 1) // EXECUTORS_PER_PAGE
        page = min(page, total_pages - 1)
        page = max(page, 0)
        context.user_data["executor_list_page"] = page

        start_idx = page * EXECUTORS_PER_PAGE
        end_idx = min(start_idx + EXECUTORS_PER_PAGE, len(executors))
        page_executors = executors[start_idx:end_idx]

        # Build message
        lines = [
            f"*Running Executors* \\({len(executors)}\\)",
            f"_Page {page + 1}/{total_pages}_",
            "",
        ]

        # Store executor list for index-based selection
        context.user_data["page_executor_ids"] = [
            ex.get("id", ex.get("executor_id", "")) for ex in page_executors
        ]

        for idx, ex in enumerate(page_executors):
            executor_id = ex.get("id", ex.get("executor_id", "unknown"))
            status_line = format_executor_status_line(ex)
            short_id = executor_id[:8] if len(executor_id) > 8 else executor_id
            lines.append(f"`{short_id}` {escape_markdown_v2(status_line)}")

        # Build keyboard with executor buttons
        keyboard = []

        # Executor selection buttons (2 per row)
        row = []
        for idx, ex in enumerate(page_executors):
            executor_id = ex.get("id", ex.get("executor_id", ""))
            config = ex.get("config", ex)
            pair = config.get("trading_pair", "???")[:10]

            row.append(InlineKeyboardButton(
                f"{pair}",
                callback_data=f"executors:detail:{executor_id[:20]}"
            ))

            if len(row) == 2:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)

        # Pagination buttons
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("< Prev", callback_data="executors:list_prev"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next >", callback_data="executors:list_next"))
        if nav_row:
            keyboard.append(nav_row)

        keyboard.append([
            InlineKeyboardButton("Refresh", callback_data="executors:list"),
            InlineKeyboardButton("Back", callback_data="executors:menu"),
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = "\n".join(lines)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing executors list: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="executors:menu")]]
        await query.message.edit_text(
            format_error_message(f"Error: {str(e)[:100]}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# ============================================
# EXECUTOR DETAIL VIEW
# ============================================

async def show_executor_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, executor_id: str) -> None:
    """Show detailed view for a specific executor

    Args:
        update: Telegram update
        context: Telegram context
        executor_id: ID of the executor to show
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        # Find executor in cached list or fetch fresh
        executors = context.user_data.get("running_executors", [])
        executor = None

        for ex in executors:
            ex_id = ex.get("id", ex.get("executor_id", ""))
            if ex_id.startswith(executor_id) or executor_id.startswith(ex_id[:20]):
                executor = ex
                break

        if not executor:
            # Try to fetch from API
            client, _ = await get_executors_client(chat_id, context.user_data)
            executor = await client.executors.get_executor(executor_id=executor_id)

        if not executor:
            await query.answer("Executor not found", show_alert=True)
            await show_running_executors(update, context)
            return

        # Store current executor
        full_id = executor.get("id", executor.get("executor_id", executor_id))
        context.user_data["current_executor"] = executor
        context.user_data["current_executor_id"] = full_id

        # Extract config
        config = executor.get("config", executor)
        pair = config.get("trading_pair", "UNKNOWN")
        connector = config.get("connector_name", "unknown")
        side = config.get("side", SIDE_LONG)
        leverage = config.get("leverage", 1)
        amount = config.get("total_amount_quote", 0)

        side_str = "LONG" if side == SIDE_LONG else "SHORT"

        start_price = config.get("start_price", 0)
        end_price = config.get("end_price", 0)
        limit_price = config.get("limit_price", 0)
        max_orders = config.get("max_open_orders", 3)
        take_profit = config.get("take_profit", 0.0005)

        pnl = executor.get("pnl_quote", 0) or executor.get("unrealized_pnl_quote", 0) or 0
        status = executor.get("status", "unknown")

        # Build message
        side_emoji = "" if side == SIDE_LONG else ""
        pnl_emoji = "" if pnl >= 0 else ""

        lines = [
            f"*Executor Detail*",
            "",
            f"`{escape_markdown_v2(full_id[:30])}`",
            "",
            f"*{escape_markdown_v2(pair)}* \\| {escape_markdown_v2(side_str)} {leverage}x",
            f"Connector: `{escape_markdown_v2(connector)}`",
            f"Amount: `${escape_markdown_v2(f'{amount:,.2f}')}`",
            "",
            f"*Grid*",
            f"  Start: `{escape_markdown_v2(f'{start_price:.6g}')}`",
            f"  End: `{escape_markdown_v2(f'{end_price:.6g}')}`",
            f"  Limit: `{escape_markdown_v2(f'{limit_price:.6g}')}`",
            "",
            f"Max Orders: `{max_orders}` \\| TP: `{escape_markdown_v2(f'{take_profit:.4%}')}`",
            "",
            f"Status: `{escape_markdown_v2(status)}`",
            f"PnL: {pnl_emoji} `{escape_markdown_v2(f'${pnl:+,.2f}')}`",
        ]

        # Build keyboard
        keyboard = [
            [
                InlineKeyboardButton("Stop", callback_data=f"executors:stop:{full_id[:20]}"),
            ],
            [
                InlineKeyboardButton("Refresh", callback_data=f"executors:detail:{full_id[:20]}"),
                InlineKeyboardButton("Back", callback_data="executors:list"),
            ],
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = "\n".join(lines)

        # Handle photo messages
        if getattr(query.message, 'photo', None):
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.chat.send_message(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        else:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error showing executor detail: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="executors:list")]]
        try:
            await query.message.edit_text(
                format_error_message(f"Error: {str(e)[:100]}"),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception:
            pass


# ============================================
# STOP EXECUTOR
# ============================================

async def handle_stop_executor(update: Update, context: ContextTypes.DEFAULT_TYPE, executor_id: str) -> None:
    """Handle stop executor request - show confirmation

    Args:
        update: Telegram update
        context: Telegram context
        executor_id: ID of executor to stop
    """
    query = update.callback_query

    # Get executor info for display
    executor = context.user_data.get("current_executor", {})
    config = executor.get("config", executor)
    pair = config.get("trading_pair", "UNKNOWN")

    keyboard = [
        [
            InlineKeyboardButton("Yes, Stop", callback_data=f"executors:confirm_stop:{executor_id}"),
            InlineKeyboardButton("Cancel", callback_data=f"executors:detail:{executor_id}"),
        ],
    ]

    message_text = (
        f"*Stop Executor?*\n\n"
        f"`{escape_markdown_v2(executor_id[:30])}`\n"
        f"Pair: {escape_markdown_v2(pair)}\n\n"
        f"_This will close the executor and any open orders\\._"
    )

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_confirm_stop_executor(update: Update, context: ContextTypes.DEFAULT_TYPE, executor_id: str) -> None:
    """Actually stop the executor

    Args:
        update: Telegram update
        context: Telegram context
        executor_id: ID of executor to stop
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    await query.answer("Stopping...")

    try:
        # Get full executor ID if we have a partial match
        executor = context.user_data.get("current_executor", {})
        full_id = executor.get("id", executor.get("executor_id", executor_id))

        client, _ = await get_executors_client(chat_id, context.user_data)
        result = await stop_executor(client, full_id, keep_position=False)

        # Invalidate cache
        invalidate_cache(context.user_data, "all")
        context.user_data.pop("running_executors", None)
        context.user_data.pop("current_executor", None)

        if result.get("status") == "success" or "stopped" in str(result).lower():
            keyboard = [[InlineKeyboardButton("Back to List", callback_data="executors:list")]]
            await query.message.edit_text(
                f"*Executor Stopped*\n\n`{escape_markdown_v2(full_id[:30])}`",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            error_msg = result.get("message", str(result))
            keyboard = [[InlineKeyboardButton("Back", callback_data=f"executors:detail:{executor_id}")]]
            await query.message.edit_text(
                f"*Stop Failed*\n\n{escape_markdown_v2(error_msg[:200])}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    except Exception as e:
        logger.error(f"Error stopping executor: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="executors:list")]]
        await query.message.edit_text(
            f"*Error*\n\n{escape_markdown_v2(str(e)[:200])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# ============================================
# CREATE MENU
# ============================================

async def show_create_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show menu for creating new executors

    Args:
        update: Telegram update
        context: Telegram context
    """
    query = update.callback_query

    lines = [
        "*Create Executor*",
        "",
        "Select executor type:",
    ]

    keyboard = [
        [InlineKeyboardButton("Grid Executor", callback_data="executors:create_grid")],
        [InlineKeyboardButton("Back", callback_data="executors:menu")],
    ]

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ============================================
# CLOSE HANDLER
# ============================================

async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle close button - delete message and clear state

    Args:
        update: Telegram update
        context: Telegram context
    """
    query = update.callback_query
    clear_executors_state(context)

    try:
        await query.message.delete()
    except Exception as e:
        logger.warning(f"Could not delete message: {e}")
