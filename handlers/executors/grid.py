"""
Grid Executor Wizard - 4-step wizard for deploying grid executors

Steps:
1. Connector & Pair - Select exchange, enter/pick trading pair
2. Configuration - Side (LONG/SHORT), leverage, amount
3. Prices - Auto-calculated with chart visualization and edit option
4. Review & Deploy - Final confirmation

Uses the existing chart generation from grid_strike controller.
"""

import asyncio
import logging
from typing import Dict, Any, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2, format_error_message
from handlers.bots._shared import (
    get_available_cex_connectors,
    fetch_current_price,
    fetch_candles,
)
from handlers.bots.controllers.grid_strike.config import (
    SIDE_LONG,
    SIDE_SHORT,
    calculate_auto_prices,
)
from handlers.bots.controllers.grid_strike.chart import generate_chart
from ._shared import (
    get_executors_client,
    set_executor_config,
    get_executor_config,
    init_new_executor_config,
    clear_executors_state,
    create_executor,
    invalidate_cache,
    GRID_EXECUTOR_DEFAULTS,
)

logger = logging.getLogger(__name__)

# Common leverage options
LEVERAGE_OPTIONS = [5, 10, 20, 50]

# Common amount options (USDT)
AMOUNT_OPTIONS = [100, 500, 1000, 2000]

# Chart intervals
CHART_INTERVALS = ["1m", "5m", "15m", "1h"]


# ============================================
# WIZARD ENTRY
# ============================================

async def start_grid_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the grid executor wizard

    Args:
        update: Telegram update
        context: Telegram context
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    # Initialize config
    config = init_new_executor_config(context, "grid")

    # Set wizard state
    context.user_data["executors_state"] = "wizard"
    context.user_data["executor_wizard_step"] = 1
    context.user_data["executor_wizard_data"] = {}

    # Store message info for updates
    context.user_data["executor_wizard_chat_id"] = query.message.chat_id
    context.user_data["executor_wizard_msg_id"] = query.message.message_id

    await show_step_1_connector(update, context)


# ============================================
# STEP 1: CONNECTOR & PAIR
# ============================================

async def show_step_1_connector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show step 1 - connector and pair selection

    Args:
        update: Telegram update
        context: Telegram context
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, server_name = await get_executors_client(chat_id, context.user_data)

        # Get available connectors
        connectors = await get_available_cex_connectors(
            context.user_data,
            client,
            server_name=server_name
        )

        if not connectors:
            keyboard = [[InlineKeyboardButton("Back", callback_data="executors:menu")]]
            await query.message.edit_text(
                "*Grid Executor \\(1/4\\)*\n\n"
                "_No CEX connectors configured\\._\n\n"
                "Add API keys via /keys first\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        # Build connector buttons (2 per row)
        keyboard = []
        row = []
        for conn in connectors[:8]:  # Max 8 connectors
            # Shorten connector name for button
            display = conn.replace("_perpetual", "").replace("_spot", "")[:15]
            row.append(InlineKeyboardButton(display, callback_data=f"executors:grid_conn:{conn}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton("Cancel", callback_data="executors:menu")])

        lines = [
            "*Grid Executor \\(1/4\\)*",
            "",
            "Select exchange:",
        ]

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error in step 1: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="executors:menu")]]
        await query.message.edit_text(
            format_error_message(f"Error: {str(e)[:100]}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_connector_select(update: Update, context: ContextTypes.DEFAULT_TYPE, connector: str) -> None:
    """Handle connector selection, show pair input

    Args:
        update: Telegram update
        context: Telegram context
        connector: Selected connector name
    """
    query = update.callback_query

    # Update config
    config = get_executor_config(context)
    config["connector_name"] = connector
    set_executor_config(context, config)

    # Set state to expect pair input
    context.user_data["executors_state"] = "wizard_pair_input"

    lines = [
        "*Grid Executor \\(1/4\\)*",
        "",
        f"Exchange: `{escape_markdown_v2(connector)}`",
        "",
        "Enter trading pair \\(e\\.g\\. SOL\\-USDT\\):",
    ]

    # Add recent pairs if available
    recent_pairs = context.user_data.get("recent_trading_pairs", [])
    keyboard = []

    if recent_pairs:
        row = []
        for pair in recent_pairs[:4]:
            row.append(InlineKeyboardButton(pair, callback_data=f"executors:grid_pair:{pair}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton("Back", callback_data="executors:create_grid"),
        InlineKeyboardButton("Cancel", callback_data="executors:menu"),
    ])

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_pair_input(update: Update, context: ContextTypes.DEFAULT_TYPE, pair: str) -> None:
    """Handle trading pair input (from button or text)

    Args:
        update: Telegram update
        context: Telegram context
        pair: Trading pair entered
    """
    chat_id = update.effective_chat.id

    # Normalize pair format
    pair = pair.upper().strip()
    if "/" in pair:
        pair = pair.replace("/", "-")

    # Update config
    config = get_executor_config(context)
    config["trading_pair"] = pair
    set_executor_config(context, config)

    # Store in recent pairs
    recent = context.user_data.get("recent_trading_pairs", [])
    if pair not in recent:
        recent.insert(0, pair)
        context.user_data["recent_trading_pairs"] = recent[:5]

    # Move to step 2
    context.user_data["executor_wizard_step"] = 2
    context.user_data["executors_state"] = "wizard"

    # Delete user message if text input
    if update.message:
        try:
            await update.message.delete()
        except Exception:
            pass

    await show_step_2_config(update, context)


# ============================================
# STEP 2: CONFIGURATION
# ============================================

async def show_step_2_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show step 2 - side, leverage, amount configuration

    Args:
        update: Telegram update
        context: Telegram context
    """
    query = update.callback_query
    chat_id = update.effective_chat.id
    msg_id = context.user_data.get("executor_wizard_msg_id")

    config = get_executor_config(context)
    connector = config.get("connector_name", "unknown")
    pair = config.get("trading_pair", "UNKNOWN")
    side = config.get("side", SIDE_LONG)
    leverage = config.get("leverage", 10)
    amount = config.get("total_amount_quote", 1000)

    side_str = "LONG" if side == SIDE_LONG else "SHORT"

    lines = [
        "*Grid Executor \\(2/4\\)*",
        "",
        f"`{escape_markdown_v2(connector)}` \\| `{escape_markdown_v2(pair)}`",
        "",
        "*Side*",
    ]

    # Build keyboard
    keyboard = []

    # Side buttons
    side_row = [
        InlineKeyboardButton(
            f"{'[LONG]' if side == SIDE_LONG else 'LONG'}",
            callback_data="executors:grid_side:long"
        ),
        InlineKeyboardButton(
            f"{'[SHORT]' if side == SIDE_SHORT else 'SHORT'}",
            callback_data="executors:grid_side:short"
        ),
    ]
    keyboard.append(side_row)

    # Leverage label
    lines.append("")
    lines.append(f"*Leverage* \\({leverage}x\\)")

    # Leverage buttons
    lev_row = []
    for lev in LEVERAGE_OPTIONS:
        label = f"[{lev}x]" if leverage == lev else f"{lev}x"
        lev_row.append(InlineKeyboardButton(label, callback_data=f"executors:grid_lev:{lev}"))
    keyboard.append(lev_row)

    # Amount label
    lines.append("")
    lines.append(f"*Amount* \\(${amount:,}\\)")

    # Amount buttons
    amt_row = []
    for amt in AMOUNT_OPTIONS:
        label = f"[${amt}]" if amount == amt else f"${amt}"
        amt_row.append(InlineKeyboardButton(label, callback_data=f"executors:grid_amt:{amt}"))
    keyboard.append(amt_row)

    # Custom amount button
    keyboard.append([InlineKeyboardButton("Custom Amount...", callback_data="executors:grid_amt_custom")])

    # Navigation
    keyboard.append([
        InlineKeyboardButton("Back", callback_data="executors:create_grid"),
        InlineKeyboardButton("Next", callback_data="executors:grid_step3"),
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = "\n".join(lines)

    # Edit message based on how we got here
    if query:
        try:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.warning(f"Error editing message: {e}")
    else:
        # Coming from text input - need to edit stored message
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=message_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.warning(f"Error editing stored message: {e}")


async def handle_side_select(update: Update, context: ContextTypes.DEFAULT_TYPE, side_str: str) -> None:
    """Handle side selection

    Args:
        update: Telegram update
        context: Telegram context
        side_str: 'long' or 'short'
    """
    config = get_executor_config(context)
    config["side"] = SIDE_LONG if side_str == "long" else SIDE_SHORT
    set_executor_config(context, config)

    await update.callback_query.answer()
    await show_step_2_config(update, context)


async def handle_leverage_select(update: Update, context: ContextTypes.DEFAULT_TYPE, leverage: int) -> None:
    """Handle leverage selection

    Args:
        update: Telegram update
        context: Telegram context
        leverage: Selected leverage value
    """
    config = get_executor_config(context)
    config["leverage"] = leverage
    set_executor_config(context, config)

    await update.callback_query.answer()
    await show_step_2_config(update, context)


async def handle_amount_select(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: int) -> None:
    """Handle amount selection

    Args:
        update: Telegram update
        context: Telegram context
        amount: Selected amount value
    """
    config = get_executor_config(context)
    config["total_amount_quote"] = amount
    set_executor_config(context, config)

    await update.callback_query.answer()
    await show_step_2_config(update, context)


async def handle_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle custom amount input request

    Args:
        update: Telegram update
        context: Telegram context
    """
    query = update.callback_query

    context.user_data["executors_state"] = "wizard_amount_input"

    config = get_executor_config(context)
    current_amount = config.get("total_amount_quote", 1000)

    keyboard = [[InlineKeyboardButton("Cancel", callback_data="executors:grid_step2")]]

    await query.message.edit_text(
        f"*Grid Executor \\(2/4\\)*\n\n"
        f"Current amount: `${current_amount:,}`\n\n"
        f"Enter custom amount \\(USDT\\):",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE, amount_str: str) -> None:
    """Handle custom amount text input

    Args:
        update: Telegram update
        context: Telegram context
        amount_str: Amount string entered
    """
    try:
        # Parse amount
        amount_str = amount_str.replace("$", "").replace(",", "").strip()
        amount = float(amount_str)

        if amount <= 0:
            raise ValueError("Amount must be positive")

        config = get_executor_config(context)
        config["total_amount_quote"] = amount
        set_executor_config(context, config)

        context.user_data["executors_state"] = "wizard"

        # Delete user message
        try:
            await update.message.delete()
        except Exception:
            pass

        await show_step_2_config(update, context)

    except ValueError as e:
        await update.message.reply_text(f"Invalid amount. Please enter a number.")


# ============================================
# STEP 3: PRICES
# ============================================

async def show_step_3_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show step 3 - price configuration with chart

    Args:
        update: Telegram update
        context: Telegram context
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    config = get_executor_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = config.get("side", SIDE_LONG)
    leverage = config.get("leverage", 10)
    amount = config.get("total_amount_quote", 1000)

    side_str = "LONG" if side == SIDE_LONG else "SHORT"

    # Get current interval or default
    interval = context.user_data.get("executor_chart_interval", "1h")

    await query.answer("Fetching price data...")

    try:
        client, _ = await get_executors_client(chat_id, context.user_data)

        # Fetch current price
        current_price = await fetch_current_price(client, connector, pair)

        if not current_price:
            keyboard = [[InlineKeyboardButton("Back", callback_data="executors:grid_step2")]]
            await query.message.edit_text(
                f"*Grid Executor \\(3/4\\)*\n\n"
                f"Could not fetch price for {escape_markdown_v2(pair)}\\.\n"
                f"Check if the pair exists on {escape_markdown_v2(connector)}\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        # Calculate auto prices if not set
        if config.get("start_price", 0) == 0:
            start, end, limit = calculate_auto_prices(current_price, side)
            config["start_price"] = start
            config["end_price"] = end
            config["limit_price"] = limit
            set_executor_config(context, config)

        start_price = config["start_price"]
        end_price = config["end_price"]
        limit_price = config["limit_price"]

        # Store wizard data
        context.user_data["executor_wizard_data"]["current_price"] = current_price

        # Fetch candles for chart
        candles = await fetch_candles(client, connector, pair, interval=interval, max_records=420)

        # Generate chart
        chart_bytes = None
        if candles:
            try:
                chart_bytes = generate_chart(config, candles, current_price)
            except Exception as e:
                logger.warning(f"Error generating chart: {e}")

        # Calculate percentages
        start_pct = ((start_price / current_price) - 1) * 100
        end_pct = ((end_price / current_price) - 1) * 100
        limit_pct = ((limit_price / current_price) - 1) * 100

        # Build message
        lines = [
            f"*Grid Executor \\(3/4\\)*",
            "",
            f"`{escape_markdown_v2(connector)}` \\| `{escape_markdown_v2(pair)}`",
            f"{escape_markdown_v2(side_str)} {leverage}x \\| ${escape_markdown_v2(f'{amount:,}')}",
            "",
            f"Current: `{escape_markdown_v2(f'{current_price:,.6g}')}`",
            "",
            "*Grid Zone*",
            f"  Start: `{escape_markdown_v2(f'{start_price:.6g}')}` \\({escape_markdown_v2(f'{start_pct:+.1f}')}%\\)",
            f"  End: `{escape_markdown_v2(f'{end_price:.6g}')}` \\({escape_markdown_v2(f'{end_pct:+.1f}')}%\\)",
            "",
            f"Limit: `{escape_markdown_v2(f'{limit_price:.6g}')}` \\({escape_markdown_v2(f'{limit_pct:+.1f}')}%\\)",
        ]

        # Build keyboard
        keyboard = []

        # Interval selector
        interval_row = []
        for intv in CHART_INTERVALS:
            label = f"[{intv}]" if interval == intv else intv
            interval_row.append(InlineKeyboardButton(label, callback_data=f"executors:grid_interval:{intv}"))
        keyboard.append(interval_row)

        keyboard.append([
            InlineKeyboardButton("Accept", callback_data="executors:grid_step4"),
            InlineKeyboardButton("Edit Prices", callback_data="executors:grid_edit_prices"),
        ])

        keyboard.append([
            InlineKeyboardButton("Back", callback_data="executors:grid_step2"),
            InlineKeyboardButton("Cancel", callback_data="executors:menu"),
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = "\n".join(lines)

        # Send with or without chart
        if chart_bytes:
            # Delete old message and send photo
            try:
                await query.message.delete()
            except Exception:
                pass

            sent = await query.message.chat.send_photo(
                photo=chart_bytes,
                caption=message_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            context.user_data["executor_wizard_msg_id"] = sent.message_id
        else:
            # Text only
            if getattr(query.message, 'photo', None):
                try:
                    await query.message.delete()
                except Exception:
                    pass
                sent = await query.message.chat.send_message(
                    message_text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
                context.user_data["executor_wizard_msg_id"] = sent.message_id
            else:
                await query.message.edit_text(
                    message_text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )

    except Exception as e:
        logger.error(f"Error in step 3: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="executors:grid_step2")]]
        try:
            if getattr(query.message, 'photo', None):
                await query.message.delete()
                await query.message.chat.send_message(
                    format_error_message(f"Error: {str(e)[:100]}"),
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await query.message.edit_text(
                    format_error_message(f"Error: {str(e)[:100]}"),
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        except Exception:
            pass


async def handle_interval_select(update: Update, context: ContextTypes.DEFAULT_TYPE, interval: str) -> None:
    """Handle chart interval selection

    Args:
        update: Telegram update
        context: Telegram context
        interval: Selected interval
    """
    context.user_data["executor_chart_interval"] = interval
    await show_step_3_prices(update, context)


async def show_edit_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show price editing interface

    Args:
        update: Telegram update
        context: Telegram context
    """
    query = update.callback_query

    config = get_executor_config(context)
    start_price = config.get("start_price", 0)
    end_price = config.get("end_price", 0)
    limit_price = config.get("limit_price", 0)

    context.user_data["executors_state"] = "wizard_prices_input"

    lines = [
        "*Edit Prices*",
        "",
        f"Current values:",
        f"```",
        f"start_price={start_price:.6g}",
        f"end_price={end_price:.6g}",
        f"limit_price={limit_price:.6g}",
        f"```",
        "",
        "_Send one or more values to update\\._",
        "_Format: `key=value` \\(one per line\\)_",
    ]

    keyboard = [[InlineKeyboardButton("Cancel", callback_data="executors:grid_step3")]]

    # Handle photo message
    if getattr(query.message, 'photo', None):
        try:
            await query.message.delete()
        except Exception:
            pass
        sent = await query.message.chat.send_message(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data["executor_wizard_msg_id"] = sent.message_id
    else:
        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_prices_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Handle price input from user

    Args:
        update: Telegram update
        context: Telegram context
        text: User input text
    """
    chat_id = update.effective_chat.id
    msg_id = context.user_data.get("executor_wizard_msg_id")

    config = get_executor_config(context)
    updates = {}
    errors = []

    valid_keys = {"start_price", "end_price", "limit_price"}

    for line in text.split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()

        if key not in valid_keys:
            errors.append(f"Unknown: {key}")
            continue

        try:
            updates[key] = float(value)
        except ValueError:
            errors.append(f"Invalid: {key}")

    # Delete user message
    try:
        await update.message.delete()
    except Exception:
        pass

    if errors:
        await update.message.reply_text(f"Errors: {', '.join(errors)}")
        return

    if not updates:
        await update.message.reply_text("No valid updates. Use: key=value")
        return

    # Apply updates
    for key, value in updates.items():
        config[key] = value
    set_executor_config(context, config)

    context.user_data["executors_state"] = "wizard"

    # Show step 3 again with updated values
    # Create a mock update with the stored message
    class MockQuery:
        def __init__(self):
            self.message = None

        async def answer(self, text=None):
            pass

    class MockUpdate:
        def __init__(self):
            self.callback_query = MockQuery()
            self.effective_chat = update.effective_chat

    mock = MockUpdate()
    mock.callback_query.message = await context.bot.get_chat(chat_id)

    # Edit stored message to show loading
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text="Updating chart...",
        )
    except Exception:
        pass

    # Need to actually show step 3 - create proper callback
    await _refresh_step_3(context, chat_id, msg_id)


async def _refresh_step_3(context: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int) -> None:
    """Refresh step 3 after price edit

    Args:
        context: Telegram context
        chat_id: Chat ID
        msg_id: Message ID to edit
    """
    config = get_executor_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = config.get("side", SIDE_LONG)
    leverage = config.get("leverage", 10)
    amount = config.get("total_amount_quote", 1000)

    side_str = "LONG" if side == SIDE_LONG else "SHORT"
    interval = context.user_data.get("executor_chart_interval", "1h")

    try:
        client, _ = await get_executors_client(chat_id, context.user_data)
        current_price = context.user_data.get("executor_wizard_data", {}).get("current_price")

        if not current_price:
            current_price = await fetch_current_price(client, connector, pair)

        start_price = config["start_price"]
        end_price = config["end_price"]
        limit_price = config["limit_price"]

        # Fetch candles for chart
        candles = await fetch_candles(client, connector, pair, interval=interval, max_records=420)

        # Generate chart
        chart_bytes = None
        if candles:
            try:
                chart_bytes = generate_chart(config, candles, current_price)
            except Exception as e:
                logger.warning(f"Error generating chart: {e}")

        # Calculate percentages
        start_pct = ((start_price / current_price) - 1) * 100 if current_price else 0
        end_pct = ((end_price / current_price) - 1) * 100 if current_price else 0
        limit_pct = ((limit_price / current_price) - 1) * 100 if current_price else 0

        # Build message
        lines = [
            f"*Grid Executor \\(3/4\\)*",
            "",
            f"`{escape_markdown_v2(connector)}` \\| `{escape_markdown_v2(pair)}`",
            f"{escape_markdown_v2(side_str)} {leverage}x \\| ${escape_markdown_v2(f'{amount:,}')}",
            "",
            f"Current: `{escape_markdown_v2(f'{current_price:,.6g}')}`" if current_price else "",
            "",
            "*Grid Zone*",
            f"  Start: `{escape_markdown_v2(f'{start_price:.6g}')}` \\({escape_markdown_v2(f'{start_pct:+.1f}')}%\\)",
            f"  End: `{escape_markdown_v2(f'{end_price:.6g}')}` \\({escape_markdown_v2(f'{end_pct:+.1f}')}%\\)",
            "",
            f"Limit: `{escape_markdown_v2(f'{limit_price:.6g}')}` \\({escape_markdown_v2(f'{limit_pct:+.1f}')}%\\)",
        ]

        # Build keyboard
        keyboard = []

        interval_row = []
        for intv in CHART_INTERVALS:
            label = f"[{intv}]" if interval == intv else intv
            interval_row.append(InlineKeyboardButton(label, callback_data=f"executors:grid_interval:{intv}"))
        keyboard.append(interval_row)

        keyboard.append([
            InlineKeyboardButton("Accept", callback_data="executors:grid_step4"),
            InlineKeyboardButton("Edit Prices", callback_data="executors:grid_edit_prices"),
        ])

        keyboard.append([
            InlineKeyboardButton("Back", callback_data="executors:grid_step2"),
            InlineKeyboardButton("Cancel", callback_data="executors:menu"),
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = "\n".join([l for l in lines if l])  # Filter empty lines

        # Delete old message and send new with chart
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass

        if chart_bytes:
            sent = await context.bot.send_photo(
                chat_id=chat_id,
                photo=chart_bytes,
                caption=message_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        else:
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text=message_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

        context.user_data["executor_wizard_msg_id"] = sent.message_id

    except Exception as e:
        logger.error(f"Error refreshing step 3: {e}", exc_info=True)


# ============================================
# STEP 4: REVIEW & DEPLOY
# ============================================

async def show_step_4_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show step 4 - final review and deploy

    Args:
        update: Telegram update
        context: Telegram context
    """
    query = update.callback_query

    config = get_executor_config(context)

    connector = config.get("connector_name", "unknown")
    pair = config.get("trading_pair", "UNKNOWN")
    side = config.get("side", SIDE_LONG)
    leverage = config.get("leverage", 10)
    amount = config.get("total_amount_quote", 1000)
    start_price = config.get("start_price", 0)
    end_price = config.get("end_price", 0)
    limit_price = config.get("limit_price", 0)
    max_orders = config.get("max_open_orders", 3)
    take_profit = config.get("take_profit", 0.0005)

    side_str = "LONG" if side == SIDE_LONG else "SHORT"

    lines = [
        "*Deploy Grid Executor?*",
        "",
        f"Exchange: `{escape_markdown_v2(connector)}`",
        f"Pair: `{escape_markdown_v2(pair)}`",
        f"Side: {escape_markdown_v2(side_str)} {leverage}x",
        f"Amount: `${escape_markdown_v2(f'{amount:,}')}`",
        "",
        f"Grid: `{escape_markdown_v2(f'{start_price:.6g}')}` \\- `{escape_markdown_v2(f'{end_price:.6g}')}`",
        f"Limit: `{escape_markdown_v2(f'{limit_price:.6g}')}`",
        "",
        f"Max Orders: `{max_orders}`",
        f"Take Profit: `{escape_markdown_v2(f'{take_profit:.4%}')}`",
    ]

    keyboard = [
        [InlineKeyboardButton("Deploy", callback_data="executors:grid_deploy")],
        [
            InlineKeyboardButton("Edit", callback_data="executors:grid_step2"),
            InlineKeyboardButton("Cancel", callback_data="executors:menu"),
        ],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = "\n".join(lines)

    # Handle photo message
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


async def handle_deploy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle deploy button - create the executor

    Args:
        update: Telegram update
        context: Telegram context
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    await query.answer("Deploying...")

    config = get_executor_config(context)

    # Build executor config for API
    executor_config = {
        "type": "grid_executor",
        "connector_name": config.get("connector_name"),
        "trading_pair": config.get("trading_pair"),
        "side": config.get("side", SIDE_LONG),
        "leverage": config.get("leverage", 10),
        "total_amount_quote": config.get("total_amount_quote", 1000),
        "start_price": config.get("start_price"),
        "end_price": config.get("end_price"),
        "limit_price": config.get("limit_price"),
        "max_open_orders": config.get("max_open_orders", 3),
        "triple_barrier_config": {
            "take_profit": config.get("take_profit", 0.0005),
        },
    }

    try:
        await query.message.edit_text(
            "Deploying executor\\.\\.\\.",
            parse_mode="MarkdownV2"
        )

        client, _ = await get_executors_client(chat_id, context.user_data)
        result = await create_executor(client, executor_config)

        # Invalidate cache
        invalidate_cache(context.user_data, "all")
        context.user_data.pop("running_executors", None)

        # Check result
        is_success = (
            result.get("status") == "success" or
            "created" in str(result).lower() or
            result.get("executor_id") is not None or
            result.get("id") is not None
        )

        if is_success:
            executor_id = result.get("executor_id", result.get("id", "unknown"))

            keyboard = [[
                InlineKeyboardButton("View Executors", callback_data="executors:list"),
                InlineKeyboardButton("Close", callback_data="executors:close"),
            ]]

            await query.message.edit_text(
                f"*Executor Deployed*\n\n"
                f"ID: `{escape_markdown_v2(str(executor_id)[:30])}`\n\n"
                f"The executor is now running\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            # Clear wizard state
            clear_executors_state(context)

        else:
            error_msg = result.get("message", result.get("error", str(result)))

            keyboard = [[
                InlineKeyboardButton("Try Again", callback_data="executors:grid_step4"),
                InlineKeyboardButton("Cancel", callback_data="executors:menu"),
            ]]

            await query.message.edit_text(
                f"*Deploy Failed*\n\n{escape_markdown_v2(str(error_msg)[:300])}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    except Exception as e:
        logger.error(f"Error deploying executor: {e}", exc_info=True)

        keyboard = [[
            InlineKeyboardButton("Try Again", callback_data="executors:grid_step4"),
            InlineKeyboardButton("Cancel", callback_data="executors:menu"),
        ]]

        await query.message.edit_text(
            f"*Error*\n\n{escape_markdown_v2(str(e)[:300])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
