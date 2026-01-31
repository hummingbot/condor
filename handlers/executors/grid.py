"""
Grid Executor Wizard - 2-step wizard for deploying grid executors

Steps:
1. Connector & Pair - Select exchange, enter/pick trading pair
2. Configure & Deploy - Chart + key=value config editor, deploy button

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
from handlers.cex._shared import get_cex_balances, get_trading_rules, validate_trading_pair, get_correct_pair_format
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

# Chart intervals
CHART_INTERVALS = ["1m", "5m", "15m", "1h"]

# Editable parameters with their types
EDITABLE_PARAMS = {
    "side": int,
    "leverage": int,
    "total_amount_quote": float,
    "start_price": float,
    "end_price": float,
    "limit_price": float,
    "min_spread_between_orders": float,
    "take_profit": float,
    "max_open_orders": int,
    "max_orders_per_batch": int,
    "order_frequency": int,
    "min_order_amount_quote": float,
    "activation_bounds": float,
}


# ============================================
# HELPERS
# ============================================

def _is_perpetual(connector: str) -> bool:
    """Check if connector is a perpetual/futures market."""
    return "_perpetual" in connector.lower()


def _format_config_block(config: Dict[str, Any]) -> str:
    """Format config as key=value block for display inside a code block."""
    side = config.get("side", SIDE_LONG)
    side_label = "LONG" if side == SIDE_LONG else "SHORT"

    lines = [
        f"side={side_label}",
        f"leverage={config.get('leverage', 10)}",
        f"total_amount_quote={config.get('total_amount_quote', 300)}",
        f"start_price={config.get('start_price', 0):.6g}",
        f"end_price={config.get('end_price', 0):.6g}",
        f"limit_price={config.get('limit_price', 0):.6g}",
        f"min_spread_between_orders={config.get('min_spread_between_orders', 0.0001)}",
        f"take_profit={config.get('take_profit', 0.0002)}",
        f"max_open_orders={config.get('max_open_orders', 5)}",
        f"max_orders_per_batch={config.get('max_orders_per_batch', 2)}",
        f"order_frequency={config.get('order_frequency', 1)}",
        f"min_order_amount_quote={config.get('min_order_amount_quote', 6)}",
        f"activation_bounds={config.get('activation_bounds', 0.001)}",
    ]
    return "\n".join(lines)


def _build_step_2_caption(
    config: Dict[str, Any],
    current_price: Optional[float] = None,
    balances: Optional[Dict] = None,
    trading_rules: Optional[Dict] = None,
) -> str:
    """Build MarkdownV2 caption for the combined step 2 view."""
    connector = config.get("connector_name", "unknown")
    pair = config.get("trading_pair", "UNKNOWN")

    config_block = _format_config_block(config)

    lines = [
        "📐 *Grid Executor \\- Step 2/2*",
        "─────────────────────────",
        "",
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`",
    ]

    if current_price:
        lines.append(f"📊 Current: `{escape_markdown_v2(f'{current_price:,.6g}')}`")

    # Show balances for base/quote tokens
    if balances and "-" in pair:
        base, quote = pair.split("-", 1)
        connector_bals = balances.get(connector, [])
        base_bal = next((b for b in connector_bals if b.get("token", "").upper() == base.upper()), None)
        quote_bal = next((b for b in connector_bals if b.get("token", "").upper() == quote.upper()), None)
        base_units = base_bal.get("units", 0) if base_bal else 0
        quote_units = quote_bal.get("units", 0) if quote_bal else 0
        lines.append(
            f"💰 {escape_markdown_v2(base)}: "
            f"`{escape_markdown_v2(f'{base_units:,.4g}')}`"
            f" \\| {escape_markdown_v2(quote)}: "
            f"`{escape_markdown_v2(f'{quote_units:,.4g}')}`"
        )

    # Show trading rules
    if trading_rules and pair in trading_rules:
        rules = trading_rules[pair]
        min_notional = rules.get("min_notional_size", 0)
        min_order = rules.get("min_order_size", 0)
        min_price_inc = rules.get("min_price_increment", 0)
        parts = []
        if min_notional:
            parts.append(f"min\\=${escape_markdown_v2(f'{min_notional:g}')}")
        if min_order:
            parts.append(f"lot\\={escape_markdown_v2(f'{min_order:g}')}")
        if min_price_inc:
            parts.append(f"tick\\={escape_markdown_v2(f'{min_price_inc:g}')}")
        if parts:
            lines.append(f"📏 {' \\| '.join(parts)}")

    lines.append("")
    lines.append(f"```\n{config_block}\n```")
    lines.append("")
    lines.append("_Send `key\\=value` to edit_")

    return "\n".join(lines)


def _build_step_2_keyboard(interval: str = "1h") -> InlineKeyboardMarkup:
    """Build the minimal keyboard for step 2."""
    keyboard = []

    # Chart interval
    interval_row = []
    for intv in CHART_INTERVALS:
        label = f"[{intv}]" if interval == intv else intv
        interval_row.append(InlineKeyboardButton(label, callback_data=f"executors:grid_interval:{intv}"))
    keyboard.append(interval_row)

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
    context.user_data["executor_wizard_type"] = "grid"
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
                "📐 *Grid Executor \\- Step 1/2*\n"
                "─────────────────────────\n\n"
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
            "📐 *Grid Executor \\- Step 1/2*",
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
        "📐 *Grid Executor \\- Step 1/2*",
        "─────────────────────────",
        "",
        f"🏦 `{escape_markdown_v2(connector)}`",
        "",
        "🔗 *Trading Pair*",
        "_Enter pair \\(e\\.g\\. SOL\\-USDT\\):_",
    ]

    executor_pairs = context.user_data.get("executor_deployed_pairs", [])
    keyboard = []

    if executor_pairs:
        row = []
        for pair in executor_pairs[:4]:
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

    chat_id = update.effective_chat.id

    # Delete user message if text input
    if update.message:
        try:
            await update.message.delete()
        except Exception:
            pass

    # Validate trading pair exists on the connector
    config = get_executor_config(context)
    connector = config.get("connector_name", "")

    try:
        client, _ = await get_executors_client(chat_id, context.user_data)
        is_valid, error_msg, suggestions = await validate_trading_pair(
            context.user_data, client, connector, pair
        )

        if not is_valid:
            await _show_pair_suggestions(update, context, pair, error_msg, suggestions, connector)
            return

        # Get correctly formatted pair from trading rules
        trading_rules = await get_trading_rules(context.user_data, client, connector)
        correct_pair = get_correct_pair_format(trading_rules, pair)
        pair = correct_pair if correct_pair else pair

    except Exception as e:
        logger.warning(f"Could not validate trading pair: {e}")
        # Allow through if validation fails (e.g. no trading rules)

    config["trading_pair"] = pair
    set_executor_config(context, config)

    context.user_data["executor_wizard_step"] = 2
    context.user_data["executors_state"] = "wizard_config_input"

    # Show loading message while chart loads
    loading_text = "⏳ Loading chart\\.\\.\\."
    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(loading_text, parse_mode="MarkdownV2")
        except Exception:
            pass
    else:
        msg_id = context.user_data.get("executor_wizard_msg_id")
        if msg_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id,
                    text=loading_text, parse_mode="MarkdownV2"
                )
            except Exception:
                pass

    await show_step_2_combined(update, context)


async def _show_pair_suggestions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    input_pair: str,
    error_msg: str,
    suggestions: list,
    connector: str,
) -> None:
    """Show trading pair suggestions when validation fails."""
    chat_id = update.effective_chat.id
    msg_id = context.user_data.get("executor_wizard_msg_id")

    help_text = f"📐 *Grid Executor \\- Step 1/2*\n"
    help_text += f"─────────────────────────\n\n"
    help_text += f"❌ *{escape_markdown_v2(error_msg)}*\n\n"

    if suggestions:
        help_text += "💡 *Did you mean:*\n"
    else:
        help_text += "_No similar pairs found\\._\n"

    keyboard = []
    for pair in suggestions:
        keyboard.append([InlineKeyboardButton(
            f"📈 {pair}",
            callback_data=f"executors:grid_pair_select:{pair}"
        )])

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="executors:create_grid"),
        InlineKeyboardButton("❌ Cancel", callback_data="executors:menu"),
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        try:
            await update.callback_query.message.edit_text(
                help_text, parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.debug(f"Could not update wizard message: {e}")
    elif msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=help_text, parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.debug(f"Could not update wizard message: {e}")


# ============================================
# STEP 2: COMBINED CONFIG + CHART + DEPLOY
# ============================================

async def show_step_2_combined(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show step 2 - chart + key=value config editor + deploy."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    config = get_executor_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = config.get("side", SIDE_LONG)
    interval = context.user_data.get("executor_chart_interval", "1h")

    # Ensure state is config input
    context.user_data["executors_state"] = "wizard_config_input"

    try:
        client, _ = await get_executors_client(chat_id, context.user_data)

        # Fetch current price
        current_price = await fetch_current_price(client, connector, pair)

        if not current_price:
            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="executors:create_grid")]]
            msg_text = (
                f"📐 *Grid Executor \\- Step 2/2*\n"
                f"─────────────────────────\n\n"
                f"❌ Could not fetch price for `{escape_markdown_v2(pair)}`\\.\n"
                f"_Check if the pair exists on {escape_markdown_v2(connector)}\\._"
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

        # Fetch balances and trading rules
        balances = None
        trading_rules = None
        try:
            balances = await get_cex_balances(context.user_data, client, "master_account")
            context.user_data["executor_wizard_data"]["balances"] = balances
        except Exception as e:
            logger.warning(f"Could not fetch balances: {e}")

        try:
            trading_rules = await get_trading_rules(context.user_data, client, connector)
            context.user_data["executor_wizard_data"]["trading_rules"] = trading_rules
        except Exception as e:
            logger.warning(f"Could not fetch trading rules: {e}")

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
        caption = _build_step_2_caption(config, current_price, balances, trading_rules)
        reply_markup = _build_step_2_keyboard(interval)

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
    """Send or replace the step 2 message."""
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
    """Refresh step 2 after config text input."""
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

        # Use stored balances and trading rules
        wizard_data = context.user_data.get("executor_wizard_data", {})
        balances = wizard_data.get("balances")
        trading_rules = wizard_data.get("trading_rules")

        caption = _build_step_2_caption(config, current_price, balances, trading_rules)
        reply_markup = _build_step_2_keyboard(interval)

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
# CONFIG INPUT HANDLER
# ============================================

async def handle_config_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Handle key=value config input from user.

    Accepts all editable params. For side, accepts:
    side=1 or side=long (LONG), side=2 or side=short (SHORT).
    """
    chat_id = update.effective_chat.id
    msg_id = context.user_data.get("executor_wizard_msg_id")

    config = get_executor_config(context)
    updates = {}
    errors = []

    for line in text.split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()

        if key not in EDITABLE_PARAMS:
            errors.append(f"Unknown: {key}")
            continue

        # Handle side: accept long/short strings
        if key == "side":
            if value.lower() in ("long", "1"):
                updates["side"] = SIDE_LONG
            elif value.lower() in ("short", "2"):
                updates["side"] = SIDE_SHORT
            else:
                errors.append("side: use long/short or 1/2")
            continue

        try:
            updates[key] = EDITABLE_PARAMS[key](value)
        except ValueError:
            errors.append(f"Invalid: {key}")

    # Delete user message
    try:
        await update.message.delete()
    except Exception:
        pass

    if errors:
        await context.bot.send_message(chat_id=chat_id, text=f"Errors: {', '.join(errors)}")
        return

    if not updates:
        await context.bot.send_message(chat_id=chat_id, text="No valid updates. Send key=value")
        return

    # Check if side changed for auto price recalculation
    old_side = config.get("side", SIDE_LONG)

    for key, value in updates.items():
        config[key] = value

    new_side = config.get("side", SIDE_LONG)
    if "side" in updates and new_side != old_side:
        current_price = context.user_data.get("executor_wizard_data", {}).get("current_price")
        if current_price:
            start, end, limit = calculate_auto_prices(current_price, new_side)
            config["start_price"] = start
            config["end_price"] = end
            config["limit_price"] = limit

    set_executor_config(context, config)

    await _refresh_step_2(context, chat_id, msg_id)


# ============================================
# INTERVAL
# ============================================

async def handle_interval_select(update: Update, context: ContextTypes.DEFAULT_TYPE, interval: str) -> None:
    """Handle chart interval selection."""
    context.user_data["executor_chart_interval"] = interval
    await show_step_2_combined(update, context)


# ============================================
# DEPLOY
# ============================================

async def handle_deploy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle deploy button - create the executor."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    config = get_executor_config(context)

    # Validate required fields before deploying
    if not config.get("connector_name") or not config.get("trading_pair"):
        keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="executors:create_grid")]]
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=chat_id,
            text="*❌ Missing Config*\n\nConnector or trading pair not set\\. Please start over\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    logger.info(f"Deploying executor: connector={config.get('connector_name')}, pair={config.get('trading_pair')}, side={config.get('side')}")

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
        "activation_bounds": config.get("activation_bounds", 0.001),
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
        text="🚀 _Deploying executor\\.\\.\\._",
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

            # Store pair in executor-specific deployed pairs list
            deployed_pair = config.get("trading_pair", "")
            if deployed_pair:
                deployed = context.user_data.get("executor_deployed_pairs", [])
                if deployed_pair in deployed:
                    deployed.remove(deployed_pair)
                deployed.insert(0, deployed_pair)
                context.user_data["executor_deployed_pairs"] = deployed[:8]

            keyboard = [[
                InlineKeyboardButton("📋 View Executors", callback_data="executors:list"),
                InlineKeyboardButton("❌ Close", callback_data="executors:close"),
            ]]

            pair_display = config.get("trading_pair", "")
            side_val = config.get("side", 1)
            side_emoji = "🟢" if side_val == 1 else "🔴"
            side_label = "LONG" if side_val == 1 else "SHORT"
            leverage = config.get("leverage", 1)

            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=loading_msg.message_id,
                text=f"✅ *Executor Deployed*\n"
                     f"─────────────────────────\n\n"
                     f"{side_emoji} *{escape_markdown_v2(pair_display)}* \\| {escape_markdown_v2(side_label)} {leverage}x\n"
                     f"🆔 `{escape_markdown_v2(str(executor_id)[:30])}`\n\n"
                     f"_The executor is now running\\._",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            clear_executors_state(context)

        else:
            error_msg = result.get("message", result.get("error", str(result)))

            keyboard = [[
                InlineKeyboardButton("🔄 Try Again", callback_data="executors:grid_step2"),
                InlineKeyboardButton("❌ Cancel", callback_data="executors:menu"),
            ]]

            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=loading_msg.message_id,
                text=f"❌ *Deploy Failed*\n"
                     f"─────────────────────────\n\n"
                     f"{escape_markdown_v2(str(error_msg)[:300])}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    except Exception as e:
        logger.error(f"Error deploying executor: {e}", exc_info=True)

        keyboard = [[
            InlineKeyboardButton("🔄 Try Again", callback_data="executors:grid_step2"),
            InlineKeyboardButton("❌ Cancel", callback_data="executors:menu"),
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
