"""
Grid Executor Wizard - 2-step wizard for deploying grid executors

Steps:
1. Connector & Pair - Select exchange, enter/pick trading pair
2. Configure & Deploy - Chart visualization, all parameters, deploy

Uses the existing chart generation from grid_strike controller.
"""

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
)

logger = logging.getLogger(__name__)

# Common leverage options
LEVERAGE_OPTIONS = [5, 10, 20, 50]

# Common amount options (USDT)
AMOUNT_OPTIONS = [100, 300, 500, 1000]

# Chart intervals
CHART_INTERVALS = ["1m", "5m", "15m", "1h"]


# ============================================
# HELPERS
# ============================================

def _is_perpetual(connector: str) -> bool:
    """Check if connector is a perpetual/futures market."""
    return "_perpetual" in connector.lower()


def _fmt_amount(amount) -> str:
    """Format amount for display (not escaped)."""
    if amount == int(amount):
        return f"${int(amount):,}"
    return f"${amount:,.2f}"


def _build_step_2_caption(config: Dict[str, Any], current_price: Optional[float] = None) -> str:
    """Build the MarkdownV2 caption for the combined step 2 view."""
    connector = config.get("connector_name", "unknown")
    pair = config.get("trading_pair", "UNKNOWN")
    side = config.get("side", SIDE_LONG)
    leverage = config.get("leverage", 10)
    amount = config.get("total_amount_quote", 300)
    start_price = config.get("start_price", 0)
    end_price = config.get("end_price", 0)
    limit_price = config.get("limit_price", 0)

    # Settings
    spread = config.get("min_spread_between_orders", 0.0001)
    tp = config.get("take_profit", 0.0002)
    max_orders = config.get("max_open_orders", 5)
    batch = config.get("max_orders_per_batch", 2)
    min_order = config.get("min_order_amount_quote", 6)
    freq = config.get("order_frequency", 1)

    side_str = "LONG" if side == SIDE_LONG else "SHORT"
    is_perp = _is_perpetual(connector)

    lines = [
        "*Grid Executor \\- Step 2/2*",
        "─────────────────────────",
        "",
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`",
    ]

    # Side + leverage (only show leverage for perpetual)
    amt_esc = escape_markdown_v2(_fmt_amount(amount))
    if is_perp:
        lines.append(f"🎯 {escape_markdown_v2(side_str)} {leverage}x \\| 💰 {amt_esc}")
    else:
        lines.append(f"🎯 {escape_markdown_v2(side_str)} \\| 💰 {amt_esc}")

    if current_price:
        lines.append("")
        lines.append(f"📊 Current: `{escape_markdown_v2(f'{current_price:,.6g}')}`")

    if start_price > 0:
        lines.append("")
        lines.append("*Grid Zone*")

        if current_price and current_price > 0:
            start_pct = ((start_price / current_price) - 1) * 100
            end_pct = ((end_price / current_price) - 1) * 100
            limit_pct = ((limit_price / current_price) - 1) * 100
            lines.append(f"  Start: `{escape_markdown_v2(f'{start_price:.6g}')}` \\({escape_markdown_v2(f'{start_pct:+.1f}')}%\\)")
            lines.append(f"  End: `{escape_markdown_v2(f'{end_price:.6g}')}` \\({escape_markdown_v2(f'{end_pct:+.1f}')}%\\)")
            lines.append(f"  Limit: `{escape_markdown_v2(f'{limit_price:.6g}')}` \\({escape_markdown_v2(f'{limit_pct:+.1f}')}%\\)")
        else:
            lines.append(f"  Start: `{escape_markdown_v2(f'{start_price:.6g}')}`")
            lines.append(f"  End: `{escape_markdown_v2(f'{end_price:.6g}')}`")
            lines.append(f"  Limit: `{escape_markdown_v2(f'{limit_price:.6g}')}`")

    # Settings summary
    spread_pct = escape_markdown_v2(f"{spread * 100:.4g}%")
    tp_pct = escape_markdown_v2(f"{tp * 100:.4g}%")
    min_order_esc = escape_markdown_v2(f"${min_order:g}")

    lines.append("")
    lines.append("*Settings*")
    lines.append(f"  Spread: `{spread_pct}` \\| TP: `{tp_pct}`")
    lines.append(f"  Orders: `{max_orders}` max, `{batch}`/batch \\| Freq: `{freq}s`")
    lines.append(f"  Min: `{min_order_esc}`")

    return "\n".join(lines)


def _build_step_2_keyboard(config: Dict[str, Any], interval: str = "1h") -> InlineKeyboardMarkup:
    """Build the keyboard for the combined step 2 view."""
    connector = config.get("connector_name", "")
    side = config.get("side", SIDE_LONG)
    leverage = config.get("leverage", 10)
    amount = config.get("total_amount_quote", 300)
    is_perp = _is_perpetual(connector)

    keyboard = []

    # Side buttons
    keyboard.append([
        InlineKeyboardButton(
            f"{'[📈 LONG]' if side == SIDE_LONG else '📈 LONG'}",
            callback_data="executors:grid_side:long"
        ),
        InlineKeyboardButton(
            f"{'[📉 SHORT]' if side == SIDE_SHORT else '📉 SHORT'}",
            callback_data="executors:grid_side:short"
        ),
    ])

    # Leverage buttons (only for perpetual)
    if is_perp:
        lev_row = []
        for lev in LEVERAGE_OPTIONS:
            label = f"[{lev}x]" if leverage == lev else f"{lev}x"
            lev_row.append(InlineKeyboardButton(label, callback_data=f"executors:grid_lev:{lev}"))
        keyboard.append(lev_row)

    # Amount buttons
    amt_row = []
    for amt in AMOUNT_OPTIONS:
        label = f"[${amt}]" if amount == amt else f"${amt}"
        amt_row.append(InlineKeyboardButton(label, callback_data=f"executors:grid_amt:{amt}"))
    keyboard.append(amt_row)

    # Custom amount
    keyboard.append([InlineKeyboardButton("💰 Custom Amount...", callback_data="executors:grid_amt_custom")])

    # Chart interval
    interval_row = []
    for intv in CHART_INTERVALS:
        label = f"[{intv}]" if interval == intv else intv
        interval_row.append(InlineKeyboardButton(label, callback_data=f"executors:grid_interval:{intv}"))
    keyboard.append(interval_row)

    # Edit buttons
    keyboard.append([
        InlineKeyboardButton("✏️ Prices", callback_data="executors:grid_edit_prices"),
        InlineKeyboardButton("⚙️ Settings", callback_data="executors:grid_edit_settings"),
    ])

    # Deploy
    keyboard.append([InlineKeyboardButton("🚀 Deploy", callback_data="executors:grid_deploy")])

    # Navigation
    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="executors:create_grid"),
        InlineKeyboardButton("❌ Cancel", callback_data="executors:menu"),
    ])

    return InlineKeyboardMarkup(keyboard)


# ============================================
# WIZARD ENTRY
# ============================================

async def start_grid_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the grid executor wizard."""
    query = update.callback_query

    init_new_executor_config(context, "grid")

    context.user_data["executors_state"] = "wizard"
    context.user_data["executor_wizard_step"] = 1
    context.user_data["executor_wizard_data"] = {}
    context.user_data["executor_wizard_chat_id"] = query.message.chat_id
    context.user_data["executor_wizard_msg_id"] = query.message.message_id

    await show_step_1_connector(update, context)


# ============================================
# STEP 1: CONNECTOR & PAIR
# ============================================

async def show_step_1_connector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show step 1 - connector selection."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, server_name = await get_executors_client(chat_id, context.user_data)

        connectors = await get_available_cex_connectors(
            context.user_data, client, server_name=server_name
        )

        if not connectors:
            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="executors:menu")]]
            await query.message.edit_text(
                "*Grid Executor \\- Step 1/2*\n\n"
                "_No CEX connectors configured\\._\n\n"
                "Add API keys via /keys first\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        # Build connector buttons (2 per row)
        keyboard = []
        row = []
        for conn in connectors[:8]:
            display = conn[:20]
            row.append(InlineKeyboardButton(f"🏦 {display}", callback_data=f"executors:grid_conn:{conn}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="executors:menu")])

        lines = [
            "*Grid Executor \\- Step 1/2*",
            "─────────────────────────",
            "",
            "🏦 *Select Exchange*",
        ]

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error in step 1: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="executors:menu")]]
        await query.message.edit_text(
            format_error_message(f"Error: {str(e)[:100]}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_connector_select(update: Update, context: ContextTypes.DEFAULT_TYPE, connector: str) -> None:
    """Handle connector selection, show pair input."""
    query = update.callback_query

    config = get_executor_config(context)
    config["connector_name"] = connector

    # Spot markets default to 1x leverage
    if not _is_perpetual(connector):
        config["leverage"] = 1

    set_executor_config(context, config)

    context.user_data["executors_state"] = "wizard_pair_input"

    lines = [
        "*Grid Executor \\- Step 1/2*",
        "─────────────────────────",
        "",
        f"🏦 `{escape_markdown_v2(connector)}`",
        "",
        "🔗 *Trading Pair*",
        "Enter trading pair \\(e\\.g\\. SOL\\-USDT\\):",
    ]

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
        InlineKeyboardButton("⬅️ Back", callback_data="executors:create_grid"),
        InlineKeyboardButton("❌ Cancel", callback_data="executors:menu"),
    ])

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_pair_input(update: Update, context: ContextTypes.DEFAULT_TYPE, pair: str) -> None:
    """Handle trading pair input (from button or text)."""
    pair = pair.upper().strip()
    if "/" in pair:
        pair = pair.replace("/", "-")

    config = get_executor_config(context)
    config["trading_pair"] = pair
    set_executor_config(context, config)

    # Store in recent pairs
    recent = context.user_data.get("recent_trading_pairs", [])
    if pair not in recent:
        recent.insert(0, pair)
        context.user_data["recent_trading_pairs"] = recent[:5]

    context.user_data["executor_wizard_step"] = 2
    context.user_data["executors_state"] = "wizard"

    # Delete user message if text input
    if update.message:
        try:
            await update.message.delete()
        except Exception:
            pass

    await show_step_2_combined(update, context)


# ============================================
# STEP 2: COMBINED CONFIG + CHART + DEPLOY
# ============================================

async def show_step_2_combined(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show step 2 - combined config, chart, and deploy view."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    config = get_executor_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = config.get("side", SIDE_LONG)
    interval = context.user_data.get("executor_chart_interval", "1h")

    try:
        client, _ = await get_executors_client(chat_id, context.user_data)

        # Fetch current price
        current_price = await fetch_current_price(client, connector, pair)

        if not current_price:
            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="executors:create_grid")]]
            msg_text = (
                f"*Grid Executor \\- Step 2/2*\n\n"
                f"Could not fetch price for {escape_markdown_v2(pair)}\\.\n"
                f"Check if the pair exists on {escape_markdown_v2(connector)}\\."
            )
            if query:
                await query.message.edit_text(
                    msg_text, parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                msg_id = context.user_data.get("executor_wizard_msg_id")
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id,
                    text=msg_text, parse_mode="MarkdownV2",
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

        context.user_data.setdefault("executor_wizard_data", {})["current_price"] = current_price

        # Fetch candles for chart
        candles = await fetch_candles(client, connector, pair, interval=interval, max_records=420)

        # Generate chart
        chart_bytes = None
        if candles:
            try:
                chart_bytes = generate_chart(config, candles, current_price)
            except Exception as e:
                logger.warning(f"Error generating chart: {e}")

        # Build message
        caption = _build_step_2_caption(config, current_price)
        reply_markup = _build_step_2_keyboard(config, interval)

        # Send with chart
        await _send_step_2_message(update, context, caption, reply_markup, chart_bytes)

    except Exception as e:
        logger.error(f"Error in step 2: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="executors:create_grid")]]
        error_text = format_error_message(f"Error: {str(e)[:100]}")
        try:
            if query:
                if getattr(query.message, 'photo', None):
                    await query.message.delete()
                    await query.message.chat.send_message(
                        error_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                else:
                    await query.message.edit_text(
                        error_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
            else:
                msg_id = context.user_data.get("executor_wizard_msg_id")
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                except Exception:
                    pass
                await context.bot.send_message(
                    chat_id=chat_id, text=error_text, parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        except Exception:
            pass


async def _send_step_2_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    caption: str, reply_markup: InlineKeyboardMarkup,
    chart_bytes=None
) -> None:
    """Send or replace the step 2 message with chart."""
    query = update.callback_query if update.callback_query else None
    chat_id = update.effective_chat.id

    # Delete old message
    if query and query.message:
        try:
            await query.message.delete()
        except Exception:
            pass
    else:
        msg_id = context.user_data.get("executor_wizard_msg_id")
        if msg_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass

    # Send new message
    if chart_bytes:
        sent = await context.bot.send_photo(
            chat_id=chat_id,
            photo=chart_bytes,
            caption=caption,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    else:
        sent = await context.bot.send_message(
            chat_id=chat_id,
            text=caption,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    context.user_data["executor_wizard_msg_id"] = sent.message_id


async def _refresh_step_2(context: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int) -> None:
    """Refresh step 2 after text input (prices/settings/amount)."""
    config = get_executor_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    interval = context.user_data.get("executor_chart_interval", "1h")

    try:
        client, _ = await get_executors_client(chat_id, context.user_data)
        current_price = context.user_data.get("executor_wizard_data", {}).get("current_price")

        if not current_price:
            current_price = await fetch_current_price(client, connector, pair)
            if current_price:
                context.user_data.setdefault("executor_wizard_data", {})["current_price"] = current_price

        # Fetch candles for chart
        candles = await fetch_candles(client, connector, pair, interval=interval, max_records=420)

        chart_bytes = None
        if candles:
            try:
                chart_bytes = generate_chart(config, candles, current_price)
            except Exception as e:
                logger.warning(f"Error generating chart: {e}")

        caption = _build_step_2_caption(config, current_price)
        reply_markup = _build_step_2_keyboard(config, interval)

        # Delete old message
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass

        if chart_bytes:
            sent = await context.bot.send_photo(
                chat_id=chat_id, photo=chart_bytes,
                caption=caption, parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        else:
            sent = await context.bot.send_message(
                chat_id=chat_id, text=caption,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

        context.user_data["executor_wizard_msg_id"] = sent.message_id

    except Exception as e:
        logger.error(f"Error refreshing step 2: {e}", exc_info=True)


# ============================================
# BUTTON HANDLERS
# ============================================

async def handle_side_select(update: Update, context: ContextTypes.DEFAULT_TYPE, side_str: str) -> None:
    """Handle side selection - recalculates auto prices on change."""
    config = get_executor_config(context)
    new_side = SIDE_LONG if side_str == "long" else SIDE_SHORT
    old_side = config.get("side", SIDE_LONG)
    config["side"] = new_side

    # Recalculate prices when side changes
    if new_side != old_side:
        current_price = context.user_data.get("executor_wizard_data", {}).get("current_price")
        if current_price:
            start, end, limit = calculate_auto_prices(current_price, new_side)
            config["start_price"] = start
            config["end_price"] = end
            config["limit_price"] = limit

    set_executor_config(context, config)
    await show_step_2_combined(update, context)


async def handle_leverage_select(update: Update, context: ContextTypes.DEFAULT_TYPE, leverage: int) -> None:
    """Handle leverage selection."""
    config = get_executor_config(context)
    config["leverage"] = leverage
    set_executor_config(context, config)
    await show_step_2_combined(update, context)


async def handle_amount_select(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: int) -> None:
    """Handle amount selection."""
    config = get_executor_config(context)
    config["total_amount_quote"] = amount
    set_executor_config(context, config)
    await show_step_2_combined(update, context)


async def handle_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle custom amount input request."""
    query = update.callback_query

    context.user_data["executors_state"] = "wizard_amount_input"

    config = get_executor_config(context)
    current_amount = config.get("total_amount_quote", 300)

    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="executors:grid_step2")]]

    amt_esc = escape_markdown_v2(_fmt_amount(current_amount))

    # Handle photo message
    if getattr(query.message, 'photo', None):
        try:
            await query.message.delete()
        except Exception:
            pass
        sent = await query.message.chat.send_message(
            f"*Grid Executor \\- Step 2/2*\n\n"
            f"Current amount: `{amt_esc}`\n\n"
            f"Enter custom amount \\(USDT\\):",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data["executor_wizard_msg_id"] = sent.message_id
    else:
        await query.message.edit_text(
            f"*Grid Executor \\- Step 2/2*\n\n"
            f"Current amount: `{amt_esc}`\n\n"
            f"Enter custom amount \\(USDT\\):",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE, amount_str: str) -> None:
    """Handle custom amount text input."""
    try:
        amount_str = amount_str.replace("$", "").replace(",", "").strip()
        amount = float(amount_str)

        if amount <= 0:
            raise ValueError("Amount must be positive")

        config = get_executor_config(context)
        config["total_amount_quote"] = amount
        set_executor_config(context, config)

        context.user_data["executors_state"] = "wizard"

        try:
            await update.message.delete()
        except Exception:
            pass

        chat_id = update.effective_chat.id
        msg_id = context.user_data.get("executor_wizard_msg_id")

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text="Updating chart\\.\\.\\.",
                parse_mode="MarkdownV2"
            )
        except Exception:
            pass

        await _refresh_step_2(context, chat_id, msg_id)

    except ValueError:
        await update.message.reply_text("Invalid amount. Please enter a number.")


async def handle_interval_select(update: Update, context: ContextTypes.DEFAULT_TYPE, interval: str) -> None:
    """Handle chart interval selection."""
    context.user_data["executor_chart_interval"] = interval
    await show_step_2_combined(update, context)


# ============================================
# EDIT PRICES
# ============================================

async def show_edit_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show price editing interface."""
    query = update.callback_query

    config = get_executor_config(context)
    start_price = config.get("start_price", 0)
    end_price = config.get("end_price", 0)
    limit_price = config.get("limit_price", 0)

    context.user_data["executors_state"] = "wizard_prices_input"

    lines = [
        "*Edit Prices*",
        "",
        "Current values:",
        "```",
        f"start_price={start_price:.6g}",
        f"end_price={end_price:.6g}",
        f"limit_price={limit_price:.6g}",
        "```",
        "",
        "_Send one or more values to update\\._",
        "_Format: `key=value` \\(one per line\\)_",
    ]

    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="executors:grid_step2")]]

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
    """Handle price input from user."""
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

    for key, value in updates.items():
        config[key] = value
    set_executor_config(context, config)

    context.user_data["executors_state"] = "wizard"

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text="Updating chart\\.\\.\\.",
            parse_mode="MarkdownV2"
        )
    except Exception:
        pass

    await _refresh_step_2(context, chat_id, msg_id)


# ============================================
# EDIT SETTINGS
# ============================================

async def show_edit_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show settings editing interface."""
    query = update.callback_query

    config = get_executor_config(context)
    spread = config.get("min_spread_between_orders", 0.0001)
    tp = config.get("take_profit", 0.0002)
    min_order = config.get("min_order_amount_quote", 6)
    max_orders = config.get("max_open_orders", 5)
    batch = config.get("max_orders_per_batch", 2)
    freq = config.get("order_frequency", 1)

    context.user_data["executors_state"] = "wizard_settings_input"

    lines = [
        "*Edit Settings*",
        "",
        "Current values:",
        "```",
        f"min_spread_between_orders={spread}",
        f"take_profit={tp}",
        f"min_order_amount_quote={min_order}",
        f"max_open_orders={max_orders}",
        f"max_orders_per_batch={batch}",
        f"order_frequency={freq}",
        "```",
        "",
        "_Send one or more values to update\\._",
        "_Format: `key=value` \\(one per line\\)_",
    ]

    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="executors:grid_step2")]]

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


async def handle_settings_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Handle settings input from user."""
    chat_id = update.effective_chat.id
    msg_id = context.user_data.get("executor_wizard_msg_id")

    config = get_executor_config(context)
    updates = {}
    errors = []

    valid_keys = {
        "min_spread_between_orders": float,
        "take_profit": float,
        "min_order_amount_quote": float,
        "max_open_orders": int,
        "max_orders_per_batch": int,
        "order_frequency": int,
    }

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
            updates[key] = valid_keys[key](value)
        except ValueError:
            errors.append(f"Invalid: {key}")

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

    for key, value in updates.items():
        config[key] = value
    set_executor_config(context, config)

    context.user_data["executors_state"] = "wizard"

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text="Updating chart\\.\\.\\.",
            parse_mode="MarkdownV2"
        )
    except Exception:
        pass

    await _refresh_step_2(context, chat_id, msg_id)


# ============================================
# DEPLOY
# ============================================

async def handle_deploy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle deploy button - create the executor."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    config = get_executor_config(context)

    # Build executor config for API
    executor_config = {
        "type": "grid_executor",
        "connector_name": config.get("connector_name"),
        "trading_pair": config.get("trading_pair"),
        "side": config.get("side", SIDE_LONG),
        "leverage": config.get("leverage", 10),
        "total_amount_quote": config.get("total_amount_quote", 300),
        "start_price": config.get("start_price"),
        "end_price": config.get("end_price"),
        "limit_price": config.get("limit_price"),
        "min_spread_between_orders": config.get("min_spread_between_orders", 0.0001),
        "min_order_amount_quote": config.get("min_order_amount_quote", 6),
        "max_open_orders": config.get("max_open_orders", 5),
        "max_orders_per_batch": config.get("max_orders_per_batch", 2),
        "order_frequency": config.get("order_frequency", 1),
        "triple_barrier_config": {
            "take_profit": config.get("take_profit", 0.0002),
        },
    }

    # Delete current message (likely a photo) and send text loading
    try:
        await query.message.delete()
    except Exception:
        pass

    loading_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="⏳ Deploying executor\\.\\.\\.",
        parse_mode="MarkdownV2"
    )

    try:
        client, _ = await get_executors_client(chat_id, context.user_data)
        result = await create_executor(client, executor_config)

        # Invalidate cache
        invalidate_cache(context.user_data, "all")
        context.user_data.pop("running_executors", None)

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

            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=loading_msg.message_id,
                text=f"*✅ Executor Deployed*\n\n"
                     f"ID: `{escape_markdown_v2(str(executor_id)[:30])}`\n\n"
                     f"The executor is now running\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            clear_executors_state(context)

        else:
            error_msg = result.get("message", result.get("error", str(result)))

            keyboard = [[
                InlineKeyboardButton("Try Again", callback_data="executors:grid_step2"),
                InlineKeyboardButton("Cancel", callback_data="executors:menu"),
            ]]

            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=loading_msg.message_id,
                text=f"*❌ Deploy Failed*\n\n{escape_markdown_v2(str(error_msg)[:300])}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    except Exception as e:
        logger.error(f"Error deploying executor: {e}", exc_info=True)

        keyboard = [[
            InlineKeyboardButton("Try Again", callback_data="executors:grid_step2"),
            InlineKeyboardButton("Cancel", callback_data="executors:menu"),
        ]]

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=loading_msg.message_id,
                text=f"*❌ Error*\n\n{escape_markdown_v2(str(e)[:300])}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception:
            pass
