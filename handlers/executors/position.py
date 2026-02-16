"""
Position Executor Wizard - 2-step wizard for deploying position executors

Steps:
1. Connector & Pair - Select exchange, enter/pick trading pair
2. Configure & Deploy - Text config editor with key=value, deploy button

Deploys a single long/short position with exit conditions:
stop loss, take profit, time limit, trailing stop.
"""

import logging
from typing import Dict, Any, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2, format_error_message
from handlers.bots._shared import (
    get_available_cex_connectors,
    fetch_current_price,
)
from handlers.cex._shared import get_cex_balances, get_trading_rules, validate_trading_pair, get_correct_pair_format
from ._shared import (
    SIDE_LONG,
    SIDE_SHORT,
    get_executors_client,
    set_executor_config,
    get_executor_config,
    init_new_executor_config,
    clear_executors_state,
    create_executor,
    invalidate_cache,
)

logger = logging.getLogger(__name__)

# Editable parameters with their types
EDITABLE_PARAMS = {
    "side": int,
    "leverage": int,
    "amount": float,
    "entry_price": float,
    "stop_loss": float,
    "take_profit": float,
    "time_limit": int,
    "trailing_stop_activation": float,
    "trailing_stop_delta": float,
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

    entry_price = config.get("entry_price", 0.0)
    entry_display = "MARKET" if entry_price == 0 else f"{entry_price:.6g}"

    stop_loss = config.get("stop_loss", 0.0)
    take_profit = config.get("take_profit", 0.0)
    time_limit = config.get("time_limit", 0)
    trailing_activation = config.get("trailing_stop_activation", 0.0)
    trailing_delta = config.get("trailing_stop_delta", 0.0)

    # Format trailing stop as single display line
    if trailing_activation > 0 and trailing_delta > 0:
        trailing_display = f"{trailing_activation:.4%} / {trailing_delta:.4%}"
    else:
        trailing_display = "OFF"

    lines = [
        f"side={side_label}",
        f"leverage={config.get('leverage', 10)}",
        f"amount={config.get('amount', 0.0):.6g}",
        f"entry_price={entry_display}",
        f"stop_loss={stop_loss:.4%}" if stop_loss > 0 else "stop_loss=OFF",
        f"take_profit={take_profit:.4%}" if take_profit > 0 else "take_profit=OFF",
        f"time_limit={time_limit}s" if time_limit > 0 else "time_limit=OFF",
        f"trailing_stop={trailing_display}",
    ]
    return "\n".join(lines)


def _build_step_2_text(
    config: Dict[str, Any],
    current_price: Optional[float] = None,
    balances: Optional[Dict] = None,
    trading_rules: Optional[Dict] = None,
) -> str:
    """Build MarkdownV2 text for the step 2 config view."""
    connector = config.get("connector_name", "unknown")
    pair = config.get("trading_pair", "UNKNOWN")

    config_block = _format_config_block(config)

    lines = [
        "🎯 *Position Executor \\- Step 2/2*",
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
    lines.append("_Send `key\\=value` to edit \\(e\\.g\\. `amount\\=1\\.5`\\)_")

    return "\n".join(lines)


def _build_step_2_keyboard() -> InlineKeyboardMarkup:
    """Build the keyboard for step 2."""
    keyboard = [
        [InlineKeyboardButton("🚀 Deploy", callback_data="executors:pos_deploy")],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="executors:create_position"),
            InlineKeyboardButton("❌ Cancel", callback_data="executors:menu"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# ============================================
# WIZARD ENTRY
# ============================================

async def start_position_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the position executor wizard."""
    query = update.callback_query

    init_new_executor_config(context, "position")

    context.user_data["executors_state"] = "wizard"
    context.user_data["executor_wizard_step"] = 1
    context.user_data["executor_wizard_type"] = "position"
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
                "🎯 *Position Executor \\- Step 1/2*\n"
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
            row.append(InlineKeyboardButton(f"🏦 {display}", callback_data=f"executors:pos_conn:{conn}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="executors:menu")])

        lines = [
            "🎯 *Position Executor \\- Step 1/2*",
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
        logger.error(f"Error in position step 1: {e}", exc_info=True)
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
        "🎯 *Position Executor \\- Step 1/2*",
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
            row.append(InlineKeyboardButton(pair, callback_data=f"executors:pos_pair:{pair}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="executors:create_position"),
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

    config["trading_pair"] = pair
    set_executor_config(context, config)

    context.user_data["executor_wizard_step"] = 2
    context.user_data["executors_state"] = "wizard_config_input"

    await show_step_2_config(update, context)


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

    help_text = f"🎯 *Position Executor \\- Step 1/2*\n"
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
            callback_data=f"executors:pos_pair_select:{pair}"
        )])

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="executors:create_position"),
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
# STEP 2: CONFIG + DEPLOY (TEXT ONLY)
# ============================================

async def show_step_2_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show step 2 - text config editor + deploy button."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    config = get_executor_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    # Ensure state is config input
    context.user_data["executors_state"] = "wizard_config_input"

    try:
        client, _ = await get_executors_client(chat_id, context.user_data)

        # Fetch current price
        current_price = await fetch_current_price(client, connector, pair)

        if current_price:
            context.user_data.setdefault("executor_wizard_data", {})["current_price"] = current_price

        # Fetch balances and trading rules
        balances = None
        trading_rules = None
        try:
            balances = await get_cex_balances(context.user_data, client, "master_account")
            context.user_data.setdefault("executor_wizard_data", {})["balances"] = balances
        except Exception as e:
            logger.warning(f"Could not fetch balances: {e}")

        try:
            trading_rules = await get_trading_rules(context.user_data, client, connector)
            context.user_data.setdefault("executor_wizard_data", {})["trading_rules"] = trading_rules
        except Exception as e:
            logger.warning(f"Could not fetch trading rules: {e}")

        # Build message
        message_text = _build_step_2_text(config, current_price, balances, trading_rules)
        reply_markup = _build_step_2_keyboard()

        # Text-only: use edit_message_text
        if query:
            try:
                await query.message.edit_text(
                    message_text, parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
            except Exception:
                # If edit fails (e.g. photo message), delete and send new
                try:
                    await query.message.delete()
                except Exception:
                    pass
                sent = await context.bot.send_message(
                    chat_id=chat_id,
                    text=message_text, parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
                context.user_data["executor_wizard_msg_id"] = sent.message_id
        else:
            msg_id = context.user_data.get("executor_wizard_msg_id")
            if msg_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=msg_id,
                        text=message_text, parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                except Exception:
                    try:
                        await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                    except Exception:
                        pass
                    sent = await context.bot.send_message(
                        chat_id=chat_id,
                        text=message_text, parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                    context.user_data["executor_wizard_msg_id"] = sent.message_id

    except Exception as e:
        logger.error(f"Error in position step 2: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="executors:create_position")]]
        error_text = format_error_message(f"Error: {str(e)[:100]}")
        if query:
            try:
                await query.message.edit_text(
                    error_text, parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception:
                pass
        else:
            msg_id = context.user_data.get("executor_wizard_msg_id")
            if msg_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=msg_id,
                        text=error_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                except Exception:
                    pass


async def _refresh_step_2(context: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int) -> None:
    """Refresh step 2 after config text input."""
    config = get_executor_config(context)

    # Use stored data
    wizard_data = context.user_data.get("executor_wizard_data", {})
    current_price = wizard_data.get("current_price")
    balances = wizard_data.get("balances")
    trading_rules = wizard_data.get("trading_rules")

    message_text = _build_step_2_text(config, current_price, balances, trading_rules)
    reply_markup = _build_step_2_keyboard()

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=msg_id,
            text=message_text, parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error refreshing position step 2: {e}", exc_info=True)


# ============================================
# CONFIG INPUT HANDLER
# ============================================

async def handle_config_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Handle key=value config input from user."""
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

    for key, value in updates.items():
        config[key] = value

    set_executor_config(context, config)

    await _refresh_step_2(context, chat_id, msg_id)


# ============================================
# DEPLOY
# ============================================

async def handle_deploy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle deploy button - create the position executor."""
    query = update.callback_query
    chat_id = update.effective_chat.id

    config = get_executor_config(context)

    # Validate required fields
    if not config.get("connector_name") or not config.get("trading_pair"):
        await query.answer("Connector or trading pair not set. Start over.", show_alert=True)
        return

    # Validate amount > 0
    amount = config.get("amount", 0.0)
    if amount <= 0:
        await query.answer("Set amount first (e.g. amount=1.5)", show_alert=True)
        return

    # Validate at least one exit condition
    has_stop_loss = config.get("stop_loss", 0) > 0
    has_take_profit = config.get("take_profit", 0) > 0
    has_time_limit = config.get("time_limit", 0) > 0
    has_trailing = (
        config.get("trailing_stop_activation", 0) > 0 and
        config.get("trailing_stop_delta", 0) > 0
    )
    if not (has_stop_loss or has_take_profit or has_time_limit or has_trailing):
        await query.answer("Enable at least one exit condition (stop_loss, take_profit, time_limit, or trailing_stop)", show_alert=True)
        return

    logger.info(f"Deploying position executor: connector={config.get('connector_name')}, pair={config.get('trading_pair')}, side={config.get('side')}")

    # Build executor config for API
    executor_config = {
        "type": "position_executor",
        "connector_name": config.get("connector_name"),
        "trading_pair": config.get("trading_pair"),
        "side": config.get("side", SIDE_LONG),
        "leverage": config.get("leverage", 10),
        "amount": config.get("amount"),
    }

    # Entry price: omit if 0 (market order)
    entry_price = config.get("entry_price", 0.0)
    if entry_price > 0:
        executor_config["entry_price"] = entry_price

    # Build triple barrier config (only include non-zero values)
    triple_barrier = {}
    if has_stop_loss:
        triple_barrier["stop_loss"] = config["stop_loss"]
    if has_take_profit:
        triple_barrier["take_profit"] = config["take_profit"]
    if has_time_limit:
        triple_barrier["time_limit"] = config["time_limit"]
    if has_trailing:
        triple_barrier["trailing_stop"] = {
            "activation_price": config["trailing_stop_activation"],
            "trailing_delta": config["trailing_stop_delta"],
        }

    if triple_barrier:
        executor_config["triple_barrier_config"] = triple_barrier

    # Send loading message
    try:
        await query.message.edit_text(
            "🚀 _Deploying position executor\\.\\.\\._",
            parse_mode="MarkdownV2"
        )
    except Exception:
        pass

    loading_msg_id = query.message.message_id

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

            # Store pair in deployed pairs list
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
                message_id=loading_msg_id,
                text=f"✅ *Position Executor Deployed*\n"
                     f"─────────────────────────\n\n"
                     f"{side_emoji} *{escape_markdown_v2(pair_display)}* \\| {escape_markdown_v2(side_label)} {leverage}x\n"
                     f"💰 Amount: `{escape_markdown_v2(f'{amount:.6g}')}`\n"
                     f"🆔 `{escape_markdown_v2(str(executor_id)[:30])}`\n\n"
                     f"_The executor is now running\\._",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            clear_executors_state(context)

        else:
            error_msg = result.get("message", result.get("error", str(result)))

            keyboard = [[
                InlineKeyboardButton("🔄 Try Again", callback_data="executors:pos_step2"),
                InlineKeyboardButton("❌ Cancel", callback_data="executors:menu"),
            ]]

            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=loading_msg_id,
                text=f"❌ *Deploy Failed*\n"
                     f"─────────────────────────\n\n"
                     f"{escape_markdown_v2(str(error_msg)[:300])}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    except Exception as e:
        logger.error(f"Error deploying position executor: {e}", exc_info=True)

        keyboard = [[
            InlineKeyboardButton("🔄 Try Again", callback_data="executors:pos_step2"),
            InlineKeyboardButton("❌ Cancel", callback_data="executors:menu"),
        ]]

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=loading_msg_id,
                text=f"*❌ Error*\n\n{escape_markdown_v2(str(e)[:300])}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception:
            pass
