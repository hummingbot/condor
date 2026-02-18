"""
Executors menu - Main menu and executor detail display

Provides:
- Main executors menu with running executors summary
- Executor detail view with config and performance
"""

import logging
import time
from typing import Any, Dict, List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2, format_error_message

from ._shared import (
    SIDE_LONG,
    clear_executors_state,
    format_executor_pnl,
    format_executor_status_line,
    get_executor_fees,
    get_executor_pnl,
    get_executor_type,
    get_executor_volume,
    get_executors_client,
    invalidate_cache,
    normalize_side,
    search_running_executors,
    stop_executor,
)

logger = logging.getLogger(__name__)


# ============================================
# MAIN MENU
# ============================================


async def show_executors_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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

        # Check if we're returning from detail view - preserve cached data if recent
        coming_from_detail = query and query.data and "detail:" in str(query.data)
        cached_executors = (
            context.user_data.get("running_executors") if coming_from_detail else None
        )

        if cached_executors and coming_from_detail:
            executors = cached_executors
            logger.debug(
                f"Using cached executors for menu return (count: {len(executors)})"
            )
        else:
            # Fetch running executors
            executors = await search_running_executors(
                client, status="RUNNING", limit=50
            )

        # Calculate totals
        total_pnl = 0.0
        total_volume = 0.0
        for ex in executors:
            total_pnl += get_executor_pnl(ex)
            total_volume += get_executor_volume(ex)

        # Store for later use
        context.user_data["running_executors"] = executors
        context.user_data["current_server_name"] = server_name
        context.user_data["executor_menu_last_refresh"] = time.time()

        # Build message
        lines = [
            f"⚡ *Executors* \\| _{escape_markdown_v2(server_name)}_",
        ]

        if executors:
            lines.append("")

            max_shown = 8
            displayed = []  # (executor, type) tuples for buttons

            # Build table in code block
            lines.append("```")
            lines.append("Pair         Type Side    PnL      Vol")
            lines.append("──────────── ──── ──── ──────── ───────")

            for ex in executors[:max_shown]:
                ex_type = get_executor_type(ex)
                config = ex.get("config", ex)
                pair = config.get("trading_pair", "???")
                side_val = normalize_side(config.get("side", SIDE_LONG))
                leverage = config.get("leverage", 1)
                side_display = f"{'L' if side_val == SIDE_LONG else 'S'} {leverage}x"
                type_col = {"grid": "Grid", "position": "Pos"}.get(ex_type, "Ord")

                pnl = get_executor_pnl(ex)
                vol = get_executor_volume(ex)

                pair_col = pair[:12].ljust(12)
                type_display = type_col.ljust(4)
                side_col = side_display.ljust(4)
                pnl_col = f"{pnl:+.2f}".rjust(8)
                vol_col = (
                    f"{vol/1000:.1f}k".rjust(7)
                    if vol >= 1000
                    else f"{vol:.0f}".rjust(7)
                )

                lines.append(
                    f"{pair_col} {type_display} {side_col} {pnl_col} {vol_col}"
                )
                displayed.append((ex, ex_type))

            if len(executors) > max_shown:
                lines.append(f"  ...and {len(executors) - max_shown} more")

            if len(executors) > 1:
                lines.append("──────────── ──── ──── ──────── ───────")
                total_label = "TOTAL".ljust(22)
                pnl_col = f"{total_pnl:+.2f}".rjust(8)
                vol_col = (
                    f"{total_volume/1000:.1f}k".rjust(7)
                    if total_volume >= 1000
                    else f"{total_volume:.0f}".rjust(7)
                )
                lines.append(f"{total_label} {pnl_col} {vol_col}")

            lines.append("```")

            # Summary line below table
            pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
            summary_parts = [
                f"{pnl_emoji} PnL: `{escape_markdown_v2(f'${total_pnl:+,.2f}')}`"
            ]
            if total_volume:
                summary_parts.append(
                    f"📊 Vol: `{escape_markdown_v2(f'${total_volume:,.0f}')}`"
                )
            lines.append(" \\| ".join(summary_parts))
        else:
            lines.append("")
            lines.append("_No running executors\\._")

        # Build keyboard
        keyboard = []

        # Executor selection buttons (2 per row) - direct to detail
        if executors:
            row = []
            for ex, ex_type in displayed:
                executor_id = ex.get("id", ex.get("executor_id", ""))
                config = ex.get("config", ex)
                pair = config.get("trading_pair", "???")[:10]
                side_val = normalize_side(config.get("side", SIDE_LONG))
                side_label = "L" if side_val == SIDE_LONG else "S"
                type_icon = "📐" if ex_type == "grid" else "🎯"

                row.append(
                    InlineKeyboardButton(
                        f"{type_icon} {pair} {side_label}",
                        callback_data=f"executors:detail:{executor_id[:20]}",
                    )
                )

                if len(row) == 2:
                    keyboard.append(row)
                    row = []

            if row:
                keyboard.append(row)

        keyboard.append(
            [
                InlineKeyboardButton(
                    "📐 Create Grid", callback_data="executors:create_grid"
                ),
                InlineKeyboardButton(
                    "🎯 Create Position", callback_data="executors:create_position"
                ),
            ]
        )

        keyboard.append(
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="executors:menu"),
                InlineKeyboardButton("❌ Close", callback_data="executors:close"),
            ]
        )

        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = "\n".join(lines)

        if query:
            try:
                await query.message.edit_text(
                    message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
            except BadRequest as e:
                if "no text in the message" in str(e).lower():
                    await query.message.delete()
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=message_text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup,
                    )
                elif "Message is not modified" not in str(e):
                    raise
        else:
            await msg.reply_text(
                message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error showing executors menu: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to fetch executors: {str(e)}")

        keyboard = [
            [
                InlineKeyboardButton("🔄 Retry", callback_data="executors:menu"),
                InlineKeyboardButton("❌ Close", callback_data="executors:close"),
            ]
        ]

        if query:
            try:
                await query.message.edit_text(
                    error_message,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            except BadRequest:
                pass
        else:
            await msg.reply_text(
                error_message,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )


# ============================================
# EXECUTOR DETAIL VIEW
# ============================================


async def show_executor_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, executor_id: str
) -> None:
    """Show detailed view for a specific executor

    Args:
        update: Telegram update
        context: Telegram context
        executor_id: ID of the executor to show
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        # Always fetch fresh data for refresh button or if not cached
        client, _ = await get_executors_client(chat_id, context.user_data)
        executor = None

        # First try to get specific executor details from API
        try:
            executor = await client.executors.get_executor(executor_id=executor_id)
        except Exception as e:
            logger.warning(f"Could not fetch specific executor {executor_id}: {e}")

        # If specific fetch failed, try to find in fresh list of running executors
        if not executor:
            executors = await search_running_executors(
                client, status="RUNNING", limit=100
            )
            # Update cache with fresh data
            context.user_data["running_executors"] = executors

            for ex in executors:
                ex_id = ex.get("id", ex.get("executor_id", ""))
                if ex_id.startswith(executor_id) or executor_id.startswith(ex_id[:20]):
                    executor = ex
                    break

        if not executor:
            await query.answer("Executor not found", show_alert=True)
            await show_executors_menu(update, context)
            return

        # Store current executor
        full_id = executor.get("id", executor.get("executor_id", executor_id))
        context.user_data["current_executor"] = executor
        context.user_data["current_executor_id"] = full_id

        # Extract config
        config = executor.get("config", executor)
        ex_type = get_executor_type(executor)
        pair = config.get("trading_pair", "UNKNOWN")
        connector = config.get("connector_name", "unknown")
        side = normalize_side(config.get("side", SIDE_LONG))
        leverage = config.get("leverage", 1)

        side_str = "LONG" if side == SIDE_LONG else "SHORT"

        pnl = get_executor_pnl(executor)
        volume = get_executor_volume(executor)
        fees = get_executor_fees(executor)
        status = executor.get("status", "unknown")

        # Additional performance fields from API
        net_pnl_pct = executor.get("net_pnl_pct", 0) or 0
        realized_pnl = executor.get("realized_pnl_quote", 0) or 0
        unrealized_pnl = executor.get("unrealized_pnl_quote", 0) or 0
        break_even = executor.get("break_even_price", 0) or 0
        position_size = executor.get("filled_amount_quote", 0) or 0
        created_at = executor.get("timestamp", 0) or 0

        # Build message
        side_emoji = "🟢" if side == SIDE_LONG else "🔴"
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"

        lines = [
            f"⚡ *Executor Detail*",
            "─────────────────────────",
            "",
            f"{side_emoji} *{escape_markdown_v2(pair)}* \\| {escape_markdown_v2(side_str)} {leverage}x",
            f"🏦 `{escape_markdown_v2(connector)}`",
        ]

        if ex_type == "position":
            amount = config.get("amount", 0)
            entry_price = config.get("entry_price", 0)

            # SL/TP may be nested under triple_barrier_config
            tbc = config.get("triple_barrier_config", {})
            stop_loss = config.get("stop_loss", 0) or tbc.get("stop_loss", 0)
            take_profit = config.get("take_profit", 0) or tbc.get("take_profit", 0)
            time_limit = config.get("time_limit", 0) or tbc.get("time_limit", 0)
            trailing_cfg = tbc.get("trailing_stop") or {}
            trailing_act = config.get(
                "trailing_stop_activation", 0
            ) or trailing_cfg.get("activation_price", 0)
            trailing_delta = config.get("trailing_stop_delta", 0) or trailing_cfg.get(
                "trailing_delta", 0
            )

            # Amount is in base units — compute quote value if entry_price available
            if amount and entry_price:
                quote_val = amount * entry_price
                base_token = pair.split("-")[0] if "-" in pair else ""
                lines.append(
                    f"💰 Amount: `{escape_markdown_v2(f'{amount:,.4g}')}` {escape_markdown_v2(base_token)} ≈ `${escape_markdown_v2(f'{quote_val:,.2f}')}`"
                )
            elif amount:
                base_token = pair.split("-")[0] if "-" in pair else ""
                lines.append(
                    f"💰 Amount: `{escape_markdown_v2(f'{amount:,.4g}')}` {escape_markdown_v2(base_token)}"
                )

            lines.append("")
            lines.append(f"🎯 *Position Config*")
            if entry_price:
                lines.append(f"  Entry: `{escape_markdown_v2(f'{entry_price:.6g}')}`")
            lines.append(
                f"  SL: `{escape_markdown_v2(f'{stop_loss:.4%}')}` \\| TP: `{escape_markdown_v2(f'{take_profit:.4%}')}`"
            )
            if trailing_act:
                lines.append(
                    f"  Trail: `{escape_markdown_v2(f'{trailing_act:.4%}')}` δ `{escape_markdown_v2(f'{trailing_delta:.4%}')}`"
                )
            if time_limit:
                lines.append(f"  Time Limit: `{time_limit}s`")
        else:
            amount = config.get("total_amount_quote", 0)
            start_price = config.get("start_price", 0)
            end_price = config.get("end_price", 0)
            limit_price = config.get("limit_price", 0)
            max_orders = config.get("max_open_orders", 3)
            max_batch = config.get("max_orders_per_batch", 2)
            order_freq = config.get("order_frequency", 1)
            min_spread = config.get("min_spread_between_orders", 0.0001)
            min_order_quote = config.get("min_order_amount_quote", 10)
            activation_bounds = config.get("activation_bounds", 0)
            coerce_tp = config.get("coerce_tp_to_step", False)
            keep_position = config.get("keep_position", True)
            # Take profit may be nested under triple_barrier_config for grid executors too
            tbc = config.get("triple_barrier_config", {})
            take_profit = (
                config.get("take_profit", 0) or tbc.get("take_profit", 0) or 0.0002
            )

            lines.append(f"💰 Amount: `${escape_markdown_v2(f'{amount:,.2f}')}`")
            lines.append("")
            lines.append(f"📐 *Grid Config*")
            lines.append(f"  Start: `{escape_markdown_v2(f'{start_price:.6g}')}`")
            lines.append(f"  End: `{escape_markdown_v2(f'{end_price:.6g}')}`")
            lines.append(f"  Limit: `{escape_markdown_v2(f'{limit_price:.6g}')}`")
            lines.append(
                f"  TP: `{escape_markdown_v2(f'{take_profit:.4%}')}` \\| Spread: `{escape_markdown_v2(f'{min_spread:.4%}')}`"
            )
            lines.append(
                f"  Orders: `{max_orders}` max \\| `{max_batch}`/batch \\| `{order_freq}s` freq"
            )
            if activation_bounds:
                lines.append(
                    f"  Bounds: `{escape_markdown_v2(f'{activation_bounds:.2%}')}` \\| Min Order: `${escape_markdown_v2(f'{min_order_quote:.0f}')}`"
                )
            keep_label = "Hold" if keep_position else "Close"
            lines.append(f"  On Stop: `{keep_label}` position")

            # Compute grid metrics from config (mirrors _generate_grid_levels logic)
            if start_price and end_price and amount:
                mid_price = (start_price + end_price) / 2
                grid_range = (end_price - start_price) / start_price
                min_step = max(min_spread, 0)
                max_levels_by_amount = int(amount / min_order_quote) if min_order_quote else 1
                max_levels_by_step = int(grid_range / min_step) if min_step > 0 else max_levels_by_amount
                n_levels = max(1, min(max_levels_by_amount, max_levels_by_step))
                amount_per_level = amount / n_levels
                step = grid_range / max(n_levels - 1, 1)
                eff_tp = max(step, take_profit) if coerce_tp else take_profit

                lines.append("")
                lines.append(f"📏 *Grid Metrics*")
                lines.append(f"  Levels: `{n_levels}` \\| Step: `{escape_markdown_v2(f'{step:.4%}')}`")
                lines.append(f"  Per Level: `${escape_markdown_v2(f'{amount_per_level:,.2f}')}`")
                if coerce_tp and eff_tp != take_profit:
                    lines.append(f"  Eff\\. TP: `{escape_markdown_v2(f'{eff_tp:.4%}')}` \\(coerced to step\\)")

        lines.append("")
        lines.append(f"📊 *Performance*")

        # PnL with percentage
        pnl_str = f"${pnl:+,.2f}"
        if net_pnl_pct:
            pnl_str += f" ({net_pnl_pct:+.2f}%)"
        lines.append(f"  {pnl_emoji} PnL: `{escape_markdown_v2(pnl_str)}`")

        # Realized / Unrealized breakdown (only if there's data)
        if realized_pnl or unrealized_pnl:
            lines.append(
                f"  Real: `{escape_markdown_v2(f'${realized_pnl:+,.2f}')}` \\| Unreal: `{escape_markdown_v2(f'${unrealized_pnl:+,.2f}')}`"
            )

        if break_even:
            lines.append(f"  BE: `{escape_markdown_v2(f'{break_even:,.2f}')}`")

        if position_size:
            lines.append(f"  📈 Size: `${escape_markdown_v2(f'{position_size:,.2f}')}`")
        if volume:
            lines.append(f"  📈 Volume: `${escape_markdown_v2(f'{volume:,.2f}')}`")
        if fees:
            lines.append(f"  💸 Fees: `${escape_markdown_v2(f'{fees:,.2f}')}`")

        lines.append("")
        status_emoji = "▶️" if status.upper() == "RUNNING" else "⏹"
        lines.append(f"{status_emoji} Status: `{escape_markdown_v2(status)}`")

        # Created timestamp
        if created_at:
            from datetime import datetime, timezone
            try:
                dt = datetime.fromtimestamp(created_at, tz=timezone.utc)
                created_str = dt.strftime("%m/%d %H:%M UTC")
                lines.append(f"🕐 Created: `{escape_markdown_v2(created_str)}`")
            except (ValueError, OSError):
                pass

        lines.append(f"🆔 `{escape_markdown_v2(full_id[:30])}`")

        # Build keyboard
        keyboard = [
            [
                InlineKeyboardButton(
                    "🛑 Stop", callback_data=f"executors:stop:{full_id[:20]}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔄 Refresh", callback_data=f"executors:detail:{full_id[:20]}"
                ),
                InlineKeyboardButton("⬅️ Back", callback_data="executors:menu"),
            ],
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = "\n".join(lines)

        # Handle photo messages
        if getattr(query.message, "photo", None):
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.chat.send_message(
                message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
        else:
            try:
                await query.message.edit_text(
                    message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
            except BadRequest as e:
                if "Message is not modified" in str(e):
                    await query.answer("✅ Already up to date")
                else:
                    raise

    except Exception as e:
        logger.error(f"Error showing executor detail: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="executors:menu")]]
        try:
            await query.message.edit_text(
                format_error_message(f"Error: {str(e)[:100]}"),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception:
            pass


# ============================================
# STOP EXECUTOR
# ============================================


async def handle_stop_executor(
    update: Update, context: ContextTypes.DEFAULT_TYPE, executor_id: str
) -> None:
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

    side = normalize_side(config.get("side", SIDE_LONG))
    side_emoji = "🟢" if side == SIDE_LONG else "🔴"

    keyboard = [
        [
            InlineKeyboardButton(
                "🛑 Yes, Stop", callback_data=f"executors:confirm_stop:{executor_id}"
            ),
            InlineKeyboardButton(
                "⬅️ Cancel", callback_data=f"executors:detail:{executor_id}"
            ),
        ],
    ]

    message_text = (
        f"⚠️ *Stop Executor?*\n"
        f"─────────────────────────\n\n"
        f"{side_emoji} *{escape_markdown_v2(pair)}*\n"
        f"🆔 `{escape_markdown_v2(executor_id[:30])}`\n\n"
        f"_This will close the executor and any open orders\\._"
    )

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_confirm_stop_executor(
    update: Update, context: ContextTypes.DEFAULT_TYPE, executor_id: str
) -> None:
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
        # Get client first
        client, _ = await get_executors_client(chat_id, context.user_data)

        # Get full executor ID if we have a partial match
        executor = context.user_data.get("current_executor", {})
        full_id = executor.get("id", executor.get("executor_id", executor_id))

        # If we still have a short ID, search in cached executors for the full ID
        if len(full_id) <= 20:
            executors = context.user_data.get("running_executors", [])
            for ex in executors:
                ex_id = ex.get("id", ex.get("executor_id", ""))
                if ex_id.startswith(executor_id) or executor_id.startswith(ex_id[:20]):
                    full_id = ex_id
                    break

            # If still not found, fetch fresh list to find the full ID
            if len(full_id) <= 20:
                fresh_executors = await search_running_executors(
                    client, status="RUNNING", limit=100
                )
                for ex in fresh_executors:
                    ex_id = ex.get("id", ex.get("executor_id", ""))
                    if ex_id.startswith(executor_id) or executor_id.startswith(
                        ex_id[:20]
                    ):
                        full_id = ex_id
                        break
        result = await stop_executor(client, full_id, keep_position=False)

        # Invalidate cache selectively - preserve menu cache for better UX
        invalidate_cache(context.user_data, "all")
        context.user_data.pop("current_executor", None)
        # Only clear running_executors if successful stop
        if (
            result.get("status") in ("success", "stopping", "stopped")
            or "stop" in str(result).lower()
        ):
            context.user_data.pop("running_executors", None)

        if (
            result.get("status") in ("success", "stopping", "stopped")
            or "stop" in str(result).lower()
        ):
            keyboard = [
                [
                    InlineKeyboardButton(
                        "📋 Back to List", callback_data="executors:menu"
                    )
                ]
            ]
            await query.message.edit_text(
                f"✅ *Executor Stopped*\n\n🆔 `{escape_markdown_v2(full_id[:30])}`",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            error_msg = result.get("message", str(result))

            # If executor not found, it might have already stopped - offer to refresh list
            if "not found" in error_msg.lower():
                keyboard = [
                    [
                        InlineKeyboardButton(
                            "🔄 Refresh List", callback_data="executors:menu"
                        ),
                        InlineKeyboardButton(
                            "⬅️ Back", callback_data=f"executors:detail:{executor_id}"
                        ),
                    ]
                ]
            else:
                keyboard = [
                    [
                        InlineKeyboardButton(
                            "⬅️ Back", callback_data=f"executors:detail:{executor_id}"
                        )
                    ]
                ]

            await query.message.edit_text(
                f"❌ *Stop Failed*\n\n{escape_markdown_v2(error_msg[:200])}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    except Exception as e:
        logger.error(f"Error stopping executor: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="executors:menu")]]
        await query.message.edit_text(
            f"*Error*\n\n{escape_markdown_v2(str(e)[:200])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
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
        "➕ *Create Executor*",
        "─────────────────────────",
        "",
        "Select executor type:",
    ]

    keyboard = [
        [
            InlineKeyboardButton(
                "📐 Grid Executor", callback_data="executors:create_grid"
            )
        ],
        [
            InlineKeyboardButton(
                "🎯 Position Executor", callback_data="executors:create_position"
            )
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="executors:menu")],
    ]

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
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
