"""
Controller configuration management

Provides:
- List existing controller configs
- Create new controller configs (grid_strike)
- Interactive form for configuration with:
  - Connector selection via buttons
  - Auto-pricing based on current market price
  - Candle chart visualization
  - Auto-generated config IDs
- Deploy selected controllers
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2, format_error_message
from ._shared import (
    get_bots_client,
    clear_bots_state,
    get_controller_config,
    set_controller_config,
    init_new_controller_config,
    format_controller_config_summary,
    format_config_field_value,
    get_available_cex_connectors,
    fetch_current_price,
    fetch_candles,
    calculate_auto_prices,
    generate_config_id,
    generate_candles_chart,
    SUPPORTED_CONTROLLERS,
    GRID_STRIKE_DEFAULTS,
    GRID_STRIKE_FIELDS,
    GRID_STRIKE_FIELD_ORDER,
    GS_WIZARD_STEPS,
    SIDE_LONG,
    SIDE_SHORT,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_LIMIT_MAKER,
    ORDER_TYPE_LABELS,
)

logger = logging.getLogger(__name__)


# ============================================
# CONTROLLER CONFIGS MENU
# ============================================

# Pagination settings for configs
CONFIGS_PER_PAGE = 16


def _get_controller_type_display(controller_name: str) -> tuple[str, str]:
    """Get display name and emoji for controller type"""
    type_map = {
        "grid_strike": ("Grid Strike", "📊"),
        "dman_v3": ("DMan V3", "🤖"),
        "xemm": ("XEMM", "🔄"),
        "pmm": ("PMM", "📈"),
    }
    controller_lower = controller_name.lower() if controller_name else ""
    for key, (name, emoji) in type_map.items():
        if key in controller_lower:
            return name, emoji
    return controller_name or "Unknown", "⚙️"


def _format_config_line(cfg: dict, index: int) -> str:
    """Format a single config line with relevant info"""
    connector = cfg.get("connector_name", "")
    pair = cfg.get("trading_pair", "")
    side_val = cfg.get("side", 1)
    side = "L" if side_val == 1 else "S"
    start_price = cfg.get("start_price", 0)
    end_price = cfg.get("end_price", 0)

    # Build display: connector PAIR side [start-end]
    if connector and pair:
        # Format prices compactly
        if start_price and end_price:
            price_range = f"[{start_price:g}-{end_price:g}]"
        else:
            price_range = ""
        display = f"{connector} {pair} {side} {price_range}".strip()
    else:
        # Fallback to config ID
        config_id = cfg.get("id", "unnamed")
        display = config_id

    return f"{index}. {display}"


async def show_controller_configs_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0) -> None:
    """Show the controller configs management menu grouped by type"""
    query = update.callback_query

    try:
        client = await get_bots_client()
        configs = await client.controllers.list_controller_configs()

        # Store configs for later use
        context.user_data["controller_configs_list"] = configs
        context.user_data["configs_page"] = page

        total_configs = len(configs)
        total_pages = (total_configs + CONFIGS_PER_PAGE - 1) // CONFIGS_PER_PAGE if total_configs > 0 else 1

        # Calculate page slice
        start_idx = page * CONFIGS_PER_PAGE
        end_idx = min(start_idx + CONFIGS_PER_PAGE, total_configs)
        page_configs = configs[start_idx:end_idx]

        # Build message header
        lines = [r"*Controller Configs*", ""]

        if not configs:
            lines.append(r"_No configurations found\._")
            lines.append(r"Create a new one to get started\!")
        else:
            if total_pages > 1:
                lines.append(f"_{total_configs} configs \\(page {page + 1}/{total_pages}\\)_")
            else:
                lines.append(f"_{total_configs} config{'s' if total_configs != 1 else ''}_")
            lines.append("")

            # Group page configs by controller type
            grouped: dict[str, list[tuple[int, dict]]] = {}
            for i, cfg in enumerate(page_configs):
                global_idx = start_idx + i
                ctrl_type = cfg.get("controller_name", "unknown")
                if ctrl_type not in grouped:
                    grouped[ctrl_type] = []
                grouped[ctrl_type].append((global_idx, cfg))

            # Display each group
            for ctrl_type, type_configs in grouped.items():
                type_name, emoji = _get_controller_type_display(ctrl_type)
                lines.append(f"{emoji} *{escape_markdown_v2(type_name)}*")
                lines.append("```")
                for global_idx, cfg in type_configs:
                    line = _format_config_line(cfg, global_idx + 1)
                    lines.append(line)
                lines.append("```")

        # Build keyboard - numbered buttons (4 per row)
        keyboard = []

        # Config edit buttons for current page
        if page_configs:
            edit_buttons = []
            for i, cfg in enumerate(page_configs):
                global_idx = start_idx + i
                edit_buttons.append(
                    InlineKeyboardButton(f"✏️{global_idx + 1}", callback_data=f"bots:edit_config:{global_idx}")
                )
            # Add in rows of 4
            for i in range(0, len(edit_buttons), 4):
                keyboard.append(edit_buttons[i:i+4])

            # Pagination buttons if needed
            if total_pages > 1:
                nav_buttons = []
                if page > 0:
                    nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"bots:configs_page:{page - 1}"))
                # Always show Next (loops to first page)
                next_page = (page + 1) % total_pages
                nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"bots:configs_page:{next_page}"))
                keyboard.append(nav_buttons)

        keyboard.append([
            InlineKeyboardButton("+ New Grid Strike", callback_data="bots:new_grid_strike"),
        ])

        keyboard.append([
            InlineKeyboardButton("Back", callback_data="bots:main_menu"),
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error loading controller configs: {e}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton("+ New Grid Strike", callback_data="bots:new_grid_strike")],
            [InlineKeyboardButton("Back", callback_data="bots:main_menu")],
        ]
        error_msg = format_error_message(f"Failed to load configs: {str(e)}")
        await query.message.edit_text(
            error_msg,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_configs_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    """Handle pagination for controller configs menu"""
    await show_controller_configs_menu(update, context, page=page)


# ============================================
# LIST EXISTING CONFIGS (DEPRECATED - merged into show_controller_configs_menu)
# ============================================

async def show_configs_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Redirect to controller configs menu (backward compatibility)"""
    await show_controller_configs_menu(update, context)


# ============================================
# PROGRESSIVE GRID STRIKE WIZARD
# ============================================

async def show_new_grid_strike_form(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the progressive Grid Strike wizard - Step 1: Connector"""
    query = update.callback_query

    # Fetch existing configs for sequence numbering
    try:
        client = await get_bots_client()
        configs = await client.controllers.list_controller_configs()
        context.user_data["controller_configs_list"] = configs
    except Exception as e:
        logger.warning(f"Could not fetch existing configs for sequencing: {e}")

    # Initialize new config with defaults
    config = init_new_controller_config(context, "grid_strike")
    context.user_data["bots_state"] = "gs_wizard"
    context.user_data["gs_wizard_step"] = "connector_name"
    context.user_data["gs_wizard_message_id"] = query.message.message_id
    context.user_data["gs_wizard_chat_id"] = query.message.chat_id

    # Show connector selector directly
    await _show_wizard_connector_step(update, context)


async def _show_wizard_connector_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wizard Step 1: Select Connector"""
    query = update.callback_query
    config = get_controller_config(context)

    try:
        client = await get_bots_client()
        cex_connectors = await get_available_cex_connectors(context.user_data, client)

        if not cex_connectors:
            keyboard = [[InlineKeyboardButton("Back", callback_data="bots:controller_configs")]]
            await query.message.edit_text(
                r"*Grid Strike \- New Config*" + "\n\n"
                r"No CEX connectors configured\." + "\n"
                r"Please configure exchange credentials first\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        # Build connector buttons (2 per row)
        keyboard = []
        row = []
        for connector in cex_connectors:
            row.append(InlineKeyboardButton(f"🏦 {connector}", callback_data=f"bots:gs_connector:{connector}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="bots:controller_configs")])

        await query.message.edit_text(
            r"*📈 Grid Strike \- New Config*" + "\n\n"
            r"*Step 1/7:* 🏦 Select Connector" + "\n\n"
            r"Choose the exchange for this grid, can be a spot or perpetual exchange:",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error in connector step: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:controller_configs")]]
        await query.message.edit_text(
            format_error_message(f"Error: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_gs_wizard_connector(update: Update, context: ContextTypes.DEFAULT_TYPE, connector: str) -> None:
    """Handle connector selection in wizard"""
    query = update.callback_query
    config = get_controller_config(context)

    config["connector_name"] = connector
    set_controller_config(context, config)

    # Move to trading pair step
    context.user_data["gs_wizard_step"] = "trading_pair"
    await _show_wizard_pair_step(update, context)


async def handle_gs_wizard_pair(update: Update, context: ContextTypes.DEFAULT_TYPE, pair: str) -> None:
    """Handle trading pair selection from button in wizard"""
    query = update.callback_query
    config = get_controller_config(context)

    config["trading_pair"] = pair.upper()
    set_controller_config(context, config)

    # Start background fetch of market data
    asyncio.create_task(_background_fetch_market_data(context, config))

    # Move to side step
    context.user_data["gs_wizard_step"] = "side"
    await _show_wizard_side_step(update, context)


async def _show_wizard_pair_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wizard Step 2: Enter Trading Pair"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "trading_pair"

    # Get recent pairs from existing configs (max 6)
    existing_configs = context.user_data.get("controller_configs_list", [])
    recent_pairs = []
    seen_pairs = set()
    for cfg in reversed(existing_configs):  # Most recent first
        pair = cfg.get("trading_pair", "")
        if pair and pair not in seen_pairs:
            seen_pairs.add(pair)
            recent_pairs.append(pair)
            if len(recent_pairs) >= 6:
                break

    # Build keyboard with recent pairs (2 per row) + cancel
    keyboard = []
    if recent_pairs:
        row = []
        for pair in recent_pairs:
            row.append(InlineKeyboardButton(pair, callback_data=f"bots:gs_pair:{pair}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="bots:controller_configs")])

    recent_hint = ""
    if recent_pairs:
        recent_hint = "\n\nOr type a custom pair below:"

    await query.message.edit_text(
        r"*📈 Grid Strike \- New Config*" + "\n\n"
        f"*Connector:* `{escape_markdown_v2(connector)}`" + "\n\n"
        r"*Step 2/7:* 🔗 Trading Pair" + "\n\n"
        r"Select a recent pair or enter a new one:" + escape_markdown_v2(recent_hint),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _show_wizard_side_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wizard Step 3: Select Side"""
    query = update.callback_query
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    keyboard = [
        [
            InlineKeyboardButton("📈 LONG", callback_data="bots:gs_side:long"),
            InlineKeyboardButton("📉 SHORT", callback_data="bots:gs_side:short"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="bots:controller_configs")],
    ]

    await query.message.edit_text(
        r"*📈 Grid Strike \- New Config*" + "\n\n"
        f"🏦 *Connector:* `{escape_markdown_v2(connector)}`" + "\n"
        f"🔗 *Pair:* `{escape_markdown_v2(pair)}`" + "\n\n"
        r"*Step 3/7:* 🎯 Side" + "\n\n"
        r"Select trading side:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_gs_wizard_side(update: Update, context: ContextTypes.DEFAULT_TYPE, side_str: str) -> None:
    """Handle side selection in wizard"""
    query = update.callback_query
    config = get_controller_config(context)

    config["side"] = SIDE_LONG if side_str == "long" else SIDE_SHORT
    set_controller_config(context, config)

    # Move to leverage step
    context.user_data["gs_wizard_step"] = "leverage"
    await _show_wizard_leverage_step(update, context)


async def _show_wizard_leverage_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wizard Step 4: Select Leverage"""
    query = update.callback_query
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = "📈 LONG" if config.get("side") == SIDE_LONG else "📉 SHORT"

    keyboard = [
        [
            InlineKeyboardButton("1x", callback_data="bots:gs_leverage:1"),
            InlineKeyboardButton("5x", callback_data="bots:gs_leverage:5"),
            InlineKeyboardButton("10x", callback_data="bots:gs_leverage:10"),
        ],
        [
            InlineKeyboardButton("20x", callback_data="bots:gs_leverage:20"),
            InlineKeyboardButton("50x", callback_data="bots:gs_leverage:50"),
            InlineKeyboardButton("75x", callback_data="bots:gs_leverage:75"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="bots:controller_configs")],
    ]

    await query.message.edit_text(
        r"*📈 Grid Strike \- New Config*" + "\n\n"
        f"🏦 *Connector:* `{escape_markdown_v2(connector)}`" + "\n"
        f"🔗 *Pair:* `{escape_markdown_v2(pair)}`" + "\n"
        f"🎯 *Side:* `{side}`" + "\n\n"
        r"*Step 4/7:* ⚡ Leverage" + "\n\n"
        r"Select leverage:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_gs_wizard_leverage(update: Update, context: ContextTypes.DEFAULT_TYPE, leverage: int) -> None:
    """Handle leverage selection in wizard"""
    query = update.callback_query
    config = get_controller_config(context)

    config["leverage"] = leverage
    set_controller_config(context, config)

    # Move to amount step
    context.user_data["gs_wizard_step"] = "total_amount_quote"
    await _show_wizard_amount_step(update, context)


async def _show_wizard_amount_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wizard Step 5: Enter Amount"""
    query = update.callback_query
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = "📈 LONG" if config.get("side") == SIDE_LONG else "📉 SHORT"
    leverage = config.get("leverage", 1)

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "total_amount_quote"

    keyboard = [
        [
            InlineKeyboardButton("💵 100", callback_data="bots:gs_amount:100"),
            InlineKeyboardButton("💵 500", callback_data="bots:gs_amount:500"),
            InlineKeyboardButton("💵 1000", callback_data="bots:gs_amount:1000"),
        ],
        [
            InlineKeyboardButton("💰 2000", callback_data="bots:gs_amount:2000"),
            InlineKeyboardButton("💰 5000", callback_data="bots:gs_amount:5000"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="bots:controller_configs")],
    ]

    await query.message.edit_text(
        r"*📈 Grid Strike \- New Config*" + "\n\n"
        f"🏦 *Connector:* `{escape_markdown_v2(connector)}`" + "\n"
        f"🔗 *Pair:* `{escape_markdown_v2(pair)}`" + "\n"
        f"🎯 *Side:* `{side}` \\| ⚡ *Leverage:* `{leverage}x`" + "\n\n"
        r"*Step 5/7:* 💰 Total Amount \(Quote\)" + "\n\n"
        r"Select or type amount in quote currency:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_gs_wizard_amount(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: float) -> None:
    """Handle amount selection in wizard"""
    query = update.callback_query
    config = get_controller_config(context)

    config["total_amount_quote"] = amount
    set_controller_config(context, config)

    # Check if market data is ready (pre-fetched in background)
    market_data_ready = context.user_data.get("gs_market_data_ready", False)
    pair = config.get("trading_pair", "")

    # Show loading indicator if market data is not ready yet
    if not market_data_ready:
        await query.message.edit_text(
            r"*📈 Grid Strike \- New Config*" + "\n\n"
            f"⏳ *Loading chart for* `{escape_markdown_v2(pair)}`\\.\\.\\." + "\n\n"
            r"_Fetching market data and generating chart\\._",
            parse_mode="MarkdownV2"
        )

    # Move to prices step - this will fetch OHLC and show chart
    context.user_data["gs_wizard_step"] = "prices"
    await _show_wizard_prices_step(update, context)


async def _show_wizard_prices_step(update: Update, context: ContextTypes.DEFAULT_TYPE, interval: str = None) -> None:
    """Wizard Step 6: Price Configuration with OHLC chart"""
    query = update.callback_query
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = config.get("side", SIDE_LONG)

    # Get current interval (default 5m)
    if interval is None:
        interval = context.user_data.get("gs_chart_interval", "5m")
    context.user_data["gs_chart_interval"] = interval

    # Check if we have pre-cached data from background fetch
    current_price = context.user_data.get("gs_current_price")
    candles = context.user_data.get("gs_candles")
    market_data_ready = context.user_data.get("gs_market_data_ready", False)
    market_data_error = context.user_data.get("gs_market_data_error")

    try:
        # If no cached data or interval changed, fetch now
        cached_interval = context.user_data.get("gs_candles_interval", "5m")
        need_refetch = interval != cached_interval

        if not current_price or need_refetch:
            # Show loading message - handle both text and photo messages
            try:
                await query.message.edit_text(
                    r"*📈 Grid Strike \- New Config*" + "\n\n"
                    f"⏳ Fetching market data for `{escape_markdown_v2(pair)}`\\.\\.\\.",
                    parse_mode="MarkdownV2"
                )
            except Exception:
                # Message is likely a photo - delete it and send new text message
                try:
                    await query.message.delete()
                except Exception:
                    pass
                loading_msg = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=(
                        r"*📈 Grid Strike \- New Config*" + "\n\n"
                        f"⏳ Fetching market data for `{escape_markdown_v2(pair)}`\\.\\.\\."
                    ),
                    parse_mode="MarkdownV2"
                )
                # Update the wizard message ID to the new loading message
                context.user_data["gs_wizard_message_id"] = loading_msg.message_id

            client = await get_bots_client()
            current_price = await fetch_current_price(client, connector, pair)

            if current_price:
                context.user_data["gs_current_price"] = current_price
                candles = await fetch_candles(client, connector, pair, interval=interval, max_records=500)
                context.user_data["gs_candles"] = candles
                context.user_data["gs_candles_interval"] = interval

        if not current_price:
            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="bots:controller_configs")]]
            try:
                await query.message.edit_text(
                    r"*❌ Error*" + "\n\n"
                    f"Could not fetch price for `{escape_markdown_v2(pair)}`\\.\n"
                    r"Please check the trading pair and try again\\.",
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception:
                # Message might be a photo or already deleted
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=(
                        r"*❌ Error*" + "\n\n"
                        f"Could not fetch price for `{escape_markdown_v2(pair)}`\\.\n"
                        r"Please check the trading pair and try again\\."
                    ),
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            return

        # Calculate auto prices only if not already set (preserve user edits)
        if not config.get("start_price") or not config.get("end_price"):
            start, end, limit = calculate_auto_prices(current_price, side)
            config["start_price"] = start
            config["end_price"] = end
            config["limit_price"] = limit
        else:
            start = config.get("start_price")
            end = config.get("end_price")
            limit = config.get("limit_price")

        # Generate config ID with sequence number (if not already set)
        if not config.get("id"):
            existing_configs = context.user_data.get("controller_configs_list", [])
            config["id"] = generate_config_id(connector, pair, existing_configs=existing_configs)

        set_controller_config(context, config)

        # Show price edit options
        side_str = "📈 LONG" if side == SIDE_LONG else "📉 SHORT"

        context.user_data["bots_state"] = "gs_wizard_input"
        context.user_data["gs_wizard_step"] = "prices"

        # Build interval buttons with current one highlighted
        interval_options = ["1m", "5m", "15m", "1h", "4h"]
        interval_row = []
        for opt in interval_options:
            label = f"✓ {opt}" if opt == interval else opt
            interval_row.append(InlineKeyboardButton(label, callback_data=f"bots:gs_interval:{opt}"))

        keyboard = [
            interval_row,
            [
                InlineKeyboardButton("✅ Accept Prices", callback_data="bots:gs_accept_prices"),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="bots:controller_configs")],
        ]

        # Format example with current values
        example_prices = f"{start:,.6g},{end:,.6g},{limit:,.6g}"

        # Build the caption
        config_text = (
            f"*📊 {escape_markdown_v2(pair)}* \\- Grid Zone Preview\n\n"
            f"🏦 *Connector:* `{escape_markdown_v2(connector)}`\n"
            f"🎯 *Side:* `{side_str}` \\| ⚡ *Leverage:* `{config.get('leverage', 1)}x`\n"
            f"💰 *Amount:* `{config.get('total_amount_quote', 0):,.0f}`\n\n"
            f"📍 Current: `{current_price:,.6g}`\n"
            f"🟢 Start: `{start:,.6g}`\n"
            f"🔵 End: `{end:,.6g}`\n"
            f"🔴 Limit: `{limit:,.6g}`\n\n"
            f"_Type `start,end,limit` to edit_\n"
            f"_e\\.g\\. `{escape_markdown_v2(example_prices)}`_"
        )

        # Generate chart and send as photo with caption
        if candles:
            chart_bytes = generate_candles_chart(
                candles, pair,
                start_price=start,
                end_price=end,
                limit_price=limit,
                current_price=current_price
            )

            # Delete old message and send photo with caption + buttons
            try:
                await query.message.delete()
            except:
                pass

            msg = await context.bot.send_photo(
                chat_id=query.message.chat_id,
                photo=chart_bytes,
                caption=config_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            # Store as wizard message (photo with buttons)
            context.user_data["gs_wizard_message_id"] = msg.message_id
            context.user_data["gs_wizard_chat_id"] = query.message.chat_id
        else:
            # No chart - just edit text message
            await query.message.edit_text(
                text=config_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            context.user_data["gs_wizard_message_id"] = query.message.message_id

    except Exception as e:
        logger.error(f"Error in prices step: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:controller_configs")]]
        await query.message.edit_text(
            format_error_message(f"Error fetching market data: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_gs_accept_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Accept prices and move to take profit step"""
    query = update.callback_query
    config = get_controller_config(context)

    side = config.get("side", SIDE_LONG)
    start_price = config.get("start_price", 0)
    end_price = config.get("end_price", 0)
    limit_price = config.get("limit_price", 0)

    # Validate price ordering based on side
    # LONG: limit_price < start_price < end_price
    # SHORT: start_price < end_price < limit_price
    validation_error = None
    if side == SIDE_LONG:
        if not (limit_price < start_price < end_price):
            validation_error = (
                "Invalid prices for LONG position\\.\n\n"
                "Required: `limit < start < end`\n"
                f"Current: `{limit_price:,.6g}` < `{start_price:,.6g}` < `{end_price:,.6g}`"
            )
    else:  # SHORT
        if not (start_price < end_price < limit_price):
            validation_error = (
                "Invalid prices for SHORT position\\.\n\n"
                "Required: `start < end < limit`\n"
                f"Current: `{start_price:,.6g}` < `{end_price:,.6g}` < `{limit_price:,.6g}`"
            )

    if validation_error:
        await query.answer("Invalid price configuration", show_alert=True)
        # Clean up the chart photo if it exists
        # Show error - delete photo and send text message
        keyboard = [
            [InlineKeyboardButton("Edit Prices", callback_data="bots:gs_back_to_prices")],
            [InlineKeyboardButton("Cancel", callback_data="bots:controller_configs")],
        ]
        try:
            await query.message.delete()
        except:
            pass
        msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"⚠️ *Price Validation Error*\n\n{validation_error}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data["gs_wizard_message_id"] = msg.message_id
        context.user_data["gs_wizard_chat_id"] = query.message.chat_id
        return

    context.user_data["gs_wizard_step"] = "take_profit"
    await _show_wizard_take_profit_step(update, context)


async def handle_gs_back_to_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Go back to prices step from validation error"""
    context.user_data["gs_wizard_step"] = "prices"
    await _show_wizard_prices_step(update, context)


async def handle_gs_interval_change(update: Update, context: ContextTypes.DEFAULT_TYPE, interval: str) -> None:
    """Handle interval change for chart - refetch candles with new interval"""
    query = update.callback_query

    # Clear cached candles to force refetch
    context.user_data.pop("gs_candles", None)
    context.user_data["gs_chart_interval"] = interval

    # Redisplay prices step with new interval
    await _show_wizard_prices_step(update, context, interval=interval)


async def _show_wizard_take_profit_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Wizard Step 7: Take Profit Configuration"""
    query = update.callback_query
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = "📈 LONG" if config.get("side") == SIDE_LONG else "📉 SHORT"

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "take_profit"

    keyboard = [
        [
            InlineKeyboardButton("0.01%", callback_data="bots:gs_tp:0.0001"),
            InlineKeyboardButton("0.02%", callback_data="bots:gs_tp:0.0002"),
            InlineKeyboardButton("0.05%", callback_data="bots:gs_tp:0.0005"),
        ],
        [
            InlineKeyboardButton("0.1%", callback_data="bots:gs_tp:0.001"),
            InlineKeyboardButton("0.2%", callback_data="bots:gs_tp:0.002"),
            InlineKeyboardButton("0.5%", callback_data="bots:gs_tp:0.005"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="bots:controller_configs")],
    ]

    message_text = (
        r"*📈 Grid Strike \- New Config*" + "\n\n"
        f"🏦 *Connector:* `{escape_markdown_v2(connector)}`" + "\n"
        f"🔗 *Pair:* `{escape_markdown_v2(pair)}`" + "\n"
        f"🎯 *Side:* `{side}` \\| ⚡ *Leverage:* `{config.get('leverage', 1)}x`" + "\n"
        f"💰 *Amount:* `{config.get('total_amount_quote', 0):,.0f}`" + "\n"
        f"📊 *Grid:* `{config.get('start_price', 0):,.6g}` \\- `{config.get('end_price', 0):,.6g}`" + "\n\n"
        r"*Step 7/7:* 🎯 Take Profit" + "\n\n"
        r"Select or type take profit % \(e\.g\. `0\.4` for 0\.4%\):"
    )

    # Delete photo message and send text message
    try:
        await query.message.delete()
    except:
        pass

    msg = await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    context.user_data["gs_wizard_message_id"] = msg.message_id
    context.user_data["gs_wizard_chat_id"] = query.message.chat_id


async def handle_gs_wizard_take_profit(update: Update, context: ContextTypes.DEFAULT_TYPE, tp: float) -> None:
    """Handle take profit selection and show final review"""
    query = update.callback_query
    config = get_controller_config(context)

    if "triple_barrier_config" not in config:
        config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS["triple_barrier_config"].copy()
    config["triple_barrier_config"]["take_profit"] = tp
    set_controller_config(context, config)

    # Move to review step
    context.user_data["gs_wizard_step"] = "review"
    await _show_wizard_review_step(update, context)


async def _show_wizard_review_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Final Review Step with copyable config format"""
    query = update.callback_query
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = "LONG" if config.get("side") == SIDE_LONG else "SHORT"
    leverage = config.get("leverage", 1)
    amount = config.get("total_amount_quote", 0)
    start_price = config.get("start_price", 0)
    end_price = config.get("end_price", 0)
    limit_price = config.get("limit_price", 0)
    tp = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)
    keep_position = config.get("keep_position", True)
    activation_bounds = config.get("activation_bounds", 0.01)
    config_id = config.get("id", "")
    max_open_orders = config.get("max_open_orders", 3)
    max_orders_per_batch = config.get("max_orders_per_batch", 1)
    min_order_amount = config.get("min_order_amount_quote", 6)
    min_spread = config.get("min_spread_between_orders", 0.0002)

    # Delete previous chart if exists
    chart_msg_id = context.user_data.pop("gs_chart_message_id", None)
    if chart_msg_id:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id,
                message_id=chart_msg_id
            )
        except:
            pass

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "review"

    # Build copyable config block with real YAML field names
    side_value = config.get("side", SIDE_LONG)
    config_block = (
        f"id: {config_id}\n"
        f"connector_name: {connector}\n"
        f"trading_pair: {pair}\n"
        f"side: {side_value}\n"
        f"leverage: {leverage}\n"
        f"total_amount_quote: {amount:.0f}\n"
        f"start_price: {start_price:.6g}\n"
        f"end_price: {end_price:.6g}\n"
        f"limit_price: {limit_price:.6g}\n"
        f"take_profit: {tp}\n"
        f"keep_position: {str(keep_position).lower()}\n"
        f"activation_bounds: {activation_bounds}\n"
        f"max_open_orders: {max_open_orders}\n"
        f"max_orders_per_batch: {max_orders_per_batch}\n"
        f"min_order_amount_quote: {min_order_amount}\n"
        f"min_spread_between_orders: {min_spread}"
    )

    message_text = (
        f"*{escape_markdown_v2(pair)}* \\- Review Config\n\n"
        f"```\n{config_block}\n```\n\n"
        f"_To edit, send `field: value` lines:_\n"
        f"`leverage: 75`\n"
        f"`total_amount_quote: 1000`"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ Save Config", callback_data="bots:gs_save"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="bots:controller_configs"),
        ],
    ]

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _update_wizard_message_for_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update wizard to show review step with copyable config format"""
    message_id = context.user_data.get("gs_wizard_message_id")
    chat_id = context.user_data.get("gs_wizard_chat_id")

    if not message_id or not chat_id:
        return

    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = "LONG" if config.get("side") == SIDE_LONG else "SHORT"
    leverage = config.get("leverage", 1)
    amount = config.get("total_amount_quote", 0)
    start_price = config.get("start_price", 0)
    end_price = config.get("end_price", 0)
    limit_price = config.get("limit_price", 0)
    tp = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)
    keep_position = config.get("keep_position", True)
    activation_bounds = config.get("activation_bounds", 0.01)
    config_id = config.get("id", "")
    max_open_orders = config.get("max_open_orders", 3)
    max_orders_per_batch = config.get("max_orders_per_batch", 1)
    min_order_amount = config.get("min_order_amount_quote", 6)
    min_spread = config.get("min_spread_between_orders", 0.0002)

    # Build copyable config block with real YAML field names
    side_value = config.get("side", SIDE_LONG)
    config_block = (
        f"id: {config_id}\n"
        f"connector_name: {connector}\n"
        f"trading_pair: {pair}\n"
        f"side: {side_value}\n"
        f"leverage: {leverage}\n"
        f"total_amount_quote: {amount:.0f}\n"
        f"start_price: {start_price:.6g}\n"
        f"end_price: {end_price:.6g}\n"
        f"limit_price: {limit_price:.6g}\n"
        f"take_profit: {tp}\n"
        f"keep_position: {str(keep_position).lower()}\n"
        f"activation_bounds: {activation_bounds}\n"
        f"max_open_orders: {max_open_orders}\n"
        f"max_orders_per_batch: {max_orders_per_batch}\n"
        f"min_order_amount_quote: {min_order_amount}\n"
        f"min_spread_between_orders: {min_spread}"
    )

    message_text = (
        f"*{escape_markdown_v2(pair)}* \\- Review Config\n\n"
        f"```\n{config_block}\n```\n\n"
        f"_To edit, send `field: value` lines:_\n"
        f"`leverage: 75`\n"
        f"`total_amount_quote: 1000`"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ Save Config", callback_data="bots:gs_save"),
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="bots:controller_configs"),
        ],
    ]

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error updating review message: {e}")


async def handle_gs_edit_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allow user to edit config ID before saving"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat_id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_id"

    current_id = config.get("id", "")

    keyboard = [
        [InlineKeyboardButton(f"Keep: {current_id[:25]}", callback_data="bots:gs_save")],
        [InlineKeyboardButton("Cancel", callback_data="bots:gs_review_back")],
    ]

    # Delete current message (could be photo)
    try:
        await query.message.delete()
    except:
        pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=r"*Edit Config ID*" + "\n\n"
        f"Current: `{escape_markdown_v2(current_id)}`" + "\n\n"
        r"Type a new ID or tap Keep to use current:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_keep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle keep_position setting"""
    query = update.callback_query
    config = get_controller_config(context)

    # Toggle the value
    current = config.get("keep_position", True)
    config["keep_position"] = not current
    context.user_data["controller_config"] = config

    # Go back to review
    await _show_wizard_review_step(update, context)


async def handle_gs_edit_tp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit take profit"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat_id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_tp"

    current_tp = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)

    keyboard = [
        [InlineKeyboardButton("Cancel", callback_data="bots:gs_review_back")],
    ]

    try:
        await query.message.delete()
    except:
        pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=r"*Edit Take Profit*" + "\n\n"
        f"Current: `{current_tp*100:.4f}%`" + "\n\n"
        r"Enter new TP \(e\.g\. 0\.03 for 0\.03%\):",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_act(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit activation bounds"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat_id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_act"

    current_act = config.get("activation_bounds", 0.01)

    keyboard = [
        [InlineKeyboardButton("Cancel", callback_data="bots:gs_review_back")],
    ]

    try:
        await query.message.delete()
    except:
        pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=r"*Edit Activation Bounds*" + "\n\n"
        f"Current: `{current_act*100:.1f}%`" + "\n\n"
        r"Enter new value \(e\.g\. 1 for 1%\):",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_max_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit max open orders"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat_id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_max_orders"

    current = config.get("max_open_orders", 3)

    keyboard = [
        [InlineKeyboardButton("Cancel", callback_data="bots:gs_review_back")],
    ]

    try:
        await query.message.delete()
    except:
        pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=r"*Edit Max Open Orders*" + "\n\n"
        f"Current: `{current}`" + "\n\n"
        r"Enter new value \(integer\):",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_batch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit max orders per batch"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat_id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_batch"

    current = config.get("max_orders_per_batch", 1)

    keyboard = [
        [InlineKeyboardButton("Cancel", callback_data="bots:gs_review_back")],
    ]

    try:
        await query.message.delete()
    except:
        pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=r"*Edit Max Orders Per Batch*" + "\n\n"
        f"Current: `{current}`" + "\n\n"
        r"Enter new value \(integer\):",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_min_amt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit min order amount"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat_id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_min_amt"

    current = config.get("min_order_amount_quote", 6)

    keyboard = [
        [InlineKeyboardButton("Cancel", callback_data="bots:gs_review_back")],
    ]

    try:
        await query.message.delete()
    except:
        pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=r"*Edit Min Order Amount*" + "\n\n"
        f"Current: `{current}`" + "\n\n"
        r"Enter new value \(e\.g\. 6\):",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_spread(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit min spread between orders"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat_id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_spread"

    current = config.get("min_spread_between_orders", 0.0002)

    keyboard = [
        [InlineKeyboardButton("Cancel", callback_data="bots:gs_review_back")],
    ]

    try:
        await query.message.delete()
    except:
        pass

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=r"*Edit Min Spread Between Orders*" + "\n\n"
        f"Current: `{current}`" + "\n\n"
        r"Enter new value \(e\.g\. 0\.0002\):",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save the Grid Strike configuration"""
    query = update.callback_query
    config = get_controller_config(context)

    config_id = config.get("id", "")
    chat_id = query.message.chat_id

    # Delete the current message (could be photo or text)
    try:
        await query.message.delete()
    except:
        pass

    # Send saving status
    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"Saving configuration `{escape_markdown_v2(config_id)}`\\.\\.\\.",
        parse_mode="MarkdownV2"
    )

    try:
        client = await get_bots_client()
        result = await client.controllers.create_or_update_controller_config(config_id, config)

        # Clean up wizard state
        _cleanup_wizard_state(context)

        keyboard = [
            [InlineKeyboardButton("Create Another", callback_data="bots:new_grid_strike")],
            [InlineKeyboardButton("Back to Configs", callback_data="bots:controller_configs")],
        ]

        await status_msg.edit_text(
            r"*Config Saved\!*" + "\n\n"
            f"Controller `{escape_markdown_v2(config_id)}` saved successfully\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error saving config: {e}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton("Try Again", callback_data="bots:gs_save")],
            [InlineKeyboardButton("Back", callback_data="bots:gs_review_back")],
        ]
        await status_msg.edit_text(
            format_error_message(f"Failed to save: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_gs_review_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Go back to review step"""
    await _show_wizard_review_step(update, context)


def _cleanup_wizard_state(context) -> None:
    """Clean up wizard-related state"""
    keys_to_remove = [
        "gs_wizard_step", "gs_wizard_message_id", "gs_wizard_chat_id",
        "gs_current_price", "gs_candles", "gs_chart_message_id",
        "gs_market_data_ready", "gs_market_data_error",
        "gs_chart_interval", "gs_candles_interval"
    ]
    for key in keys_to_remove:
        context.user_data.pop(key, None)
    clear_bots_state(context)


async def _background_fetch_market_data(context, config: dict) -> None:
    """Background task to fetch market data while user continues with wizard"""
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    if not connector or not pair:
        return

    try:
        client = await get_bots_client()

        # Fetch current price
        current_price = await fetch_current_price(client, connector, pair)

        if current_price:
            context.user_data["gs_current_price"] = current_price

            # Fetch candles (5m, 2000 records)
            candles = await fetch_candles(client, connector, pair, interval="5m", max_records=2000)
            context.user_data["gs_candles"] = candles
            context.user_data["gs_market_data_ready"] = True

            logger.info(f"Background fetch complete for {pair}: price={current_price}")
        else:
            context.user_data["gs_market_data_error"] = f"Could not fetch price for {pair}"

    except Exception as e:
        logger.error(f"Background fetch error for {pair}: {e}")
        context.user_data["gs_market_data_error"] = str(e)


async def process_gs_wizard_input(update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str) -> None:
    """Process text input during wizard flow"""
    step = context.user_data.get("gs_wizard_step")
    config = get_controller_config(context)

    if not step:
        return

    try:
        # Delete user's message
        try:
            await update.message.delete()
        except:
            pass

        if step == "trading_pair":
            # Validate and set trading pair
            pair = user_input.upper().strip()
            if "-" not in pair:
                pair = pair.replace("/", "-").replace("_", "-")

            config["trading_pair"] = pair
            set_controller_config(context, config)

            # Start background fetch of market data
            asyncio.create_task(_background_fetch_market_data(context, config))

            # Move to side step
            context.user_data["gs_wizard_step"] = "side"

            # Update the wizard message
            await _update_wizard_message_for_side(update, context)

        elif step == "prices":
            # Parse comma-separated prices: start,end,limit
            parts = user_input.replace(" ", "").split(",")
            if len(parts) == 3:
                config["start_price"] = float(parts[0])
                config["end_price"] = float(parts[1])
                config["limit_price"] = float(parts[2])
                set_controller_config(context, config)
                # Stay in prices step to show updated values
                await _update_wizard_message_for_prices_after_edit(update, context)
            elif len(parts) == 1:
                # Single price - ask which one to update
                raise ValueError("Use format: start,end,limit")
            else:
                raise ValueError("Invalid format")

        elif step == "take_profit":
            # Parse take profit - interpret as percentage (0.4 = 0.4% = 0.004)
            tp_input = user_input.replace("%", "").strip()
            tp_pct = float(tp_input)
            tp_decimal = tp_pct / 100  # Convert 0.4 -> 0.004

            config = get_controller_config(context)
            if "triple_barrier_config" not in config:
                config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS["triple_barrier_config"].copy()
            config["triple_barrier_config"]["take_profit"] = tp_decimal
            set_controller_config(context, config)

            # Move to review step
            context.user_data["gs_wizard_step"] = "review"
            await _update_wizard_message_for_review(update, context)

        elif step == "total_amount_quote":
            amount = float(user_input)
            config["total_amount_quote"] = amount
            set_controller_config(context, config)

            # Move to prices step
            context.user_data["gs_wizard_step"] = "prices"
            await _update_wizard_message_for_prices(update, context)

        elif step == "edit_id":
            new_id = user_input.strip()
            config["id"] = new_id
            set_controller_config(context, config)

            # Save immediately
            context.user_data["gs_wizard_step"] = "review"
            await _trigger_gs_save(update, context)

        elif step in ["start_price", "end_price", "limit_price"]:
            price = float(user_input)
            price_field = step.replace("_price", "_price")
            config[step] = price
            set_controller_config(context, config)

            # Go back to prices step
            context.user_data["gs_wizard_step"] = "prices"
            await _update_wizard_message_for_prices_after_edit(update, context)

        elif step == "edit_tp":
            tp_input = user_input.replace("%", "").strip()
            tp_pct = float(tp_input)
            tp_decimal = tp_pct / 100  # Convert 0.03 -> 0.0003
            if "triple_barrier_config" not in config:
                config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS["triple_barrier_config"].copy()
            config["triple_barrier_config"]["take_profit"] = tp_decimal
            set_controller_config(context, config)
            context.user_data["gs_wizard_step"] = "review"
            await _update_wizard_message_for_review(update, context)

        elif step == "edit_act":
            act_input = user_input.replace("%", "").strip()
            act_pct = float(act_input)
            act_decimal = act_pct / 100  # Convert 1 -> 0.01
            config["activation_bounds"] = act_decimal
            set_controller_config(context, config)
            context.user_data["gs_wizard_step"] = "review"
            await _update_wizard_message_for_review(update, context)

        elif step == "edit_max_orders":
            config["max_open_orders"] = int(user_input)
            set_controller_config(context, config)
            context.user_data["gs_wizard_step"] = "review"
            await _update_wizard_message_for_review(update, context)

        elif step == "edit_batch":
            config["max_orders_per_batch"] = int(user_input)
            set_controller_config(context, config)
            context.user_data["gs_wizard_step"] = "review"
            await _update_wizard_message_for_review(update, context)

        elif step == "edit_min_amt":
            config["min_order_amount_quote"] = float(user_input)
            set_controller_config(context, config)
            context.user_data["gs_wizard_step"] = "review"
            await _update_wizard_message_for_review(update, context)

        elif step == "edit_spread":
            config["min_spread_between_orders"] = float(user_input)
            set_controller_config(context, config)
            context.user_data["gs_wizard_step"] = "review"
            await _update_wizard_message_for_review(update, context)

        elif step == "review":
            # Parse field: value or field=value pairs (YAML-style)
            field_map = {
                # Real YAML field names
                "id": "id",
                "connector_name": "connector_name",
                "trading_pair": "trading_pair",
                "side": "side",
                "leverage": "leverage",
                "total_amount_quote": "total_amount_quote",
                "start_price": "start_price",
                "end_price": "end_price",
                "limit_price": "limit_price",
                "take_profit": "triple_barrier_config.take_profit",
                "keep_position": "keep_position",
                "activation_bounds": "activation_bounds",
                "max_open_orders": "max_open_orders",
                "max_orders_per_batch": "max_orders_per_batch",
                "min_order_amount_quote": "min_order_amount_quote",
                "min_spread_between_orders": "min_spread_between_orders",
            }

            updated_fields = []
            lines = user_input.strip().split("\n")
            for line in lines:
                line = line.strip()
                # Support both YAML style (field: value) and equals style (field=value)
                if ":" in line:
                    key, value = line.split(":", 1)
                elif "=" in line:
                    key, value = line.split("=", 1)
                else:
                    continue
                key = key.strip().lower()
                value = value.strip()

                if key not in field_map:
                    continue

                field = field_map[key]

                # Handle special cases
                if key == "side":
                    # Accept both numeric (1, 2) and text (LONG, SHORT)
                    if value in ("1", "LONG", "long"):
                        config["side"] = SIDE_LONG
                    else:
                        config["side"] = SIDE_SHORT
                elif key == "keep_position":
                    config["keep_position"] = value.lower() in ("true", "yes", "y", "1")
                elif key == "take_profit":
                    if "triple_barrier_config" not in config:
                        config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS["triple_barrier_config"].copy()
                    config["triple_barrier_config"]["take_profit"] = float(value)
                elif field in ["leverage", "max_open_orders", "max_orders_per_batch"]:
                    config[field] = int(value)
                elif field in ["total_amount_quote", "start_price", "end_price", "limit_price",
                              "activation_bounds", "min_order_amount_quote", "min_spread_between_orders"]:
                    config[field] = float(value)
                else:
                    config[field] = value

                updated_fields.append(key)

            if updated_fields:
                set_controller_config(context, config)
                await _update_wizard_message_for_review(update, context)
            else:
                raise ValueError("No valid fields found")

    except ValueError:
        # Send error and let user try again
        error_msg = await update.message.reply_text(
            f"Invalid input. Please enter a valid value."
        )
        # Auto-delete error after 3 seconds
        await asyncio.sleep(3)
        try:
            await error_msg.delete()
        except:
            pass


async def _update_wizard_message_for_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update wizard message to show side step after pair input"""
    config = get_controller_config(context)
    message_id = context.user_data.get("gs_wizard_message_id")
    chat_id = context.user_data.get("gs_wizard_chat_id")

    if not message_id or not chat_id:
        return

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    keyboard = [
        [
            InlineKeyboardButton("📈 LONG", callback_data="bots:gs_side:long"),
            InlineKeyboardButton("📉 SHORT", callback_data="bots:gs_side:short"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="bots:controller_configs")],
    ]

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                r"*📈 Grid Strike \- New Config*" + "\n\n"
                f"🏦 *Connector:* `{escape_markdown_v2(connector)}`" + "\n"
                f"🔗 *Pair:* `{escape_markdown_v2(pair)}`" + "\n\n"
                r"*Step 3/7:* 🎯 Side" + "\n\n"
                r"Select trading side:"
            ),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error updating wizard message: {e}")


async def _update_wizard_message_for_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger prices step after amount input"""
    message_id = context.user_data.get("gs_wizard_message_id")
    chat_id = context.user_data.get("gs_wizard_chat_id")

    if not message_id or not chat_id:
        return

    # Create a fake query object to reuse _show_wizard_prices_step
    class FakeQuery:
        def __init__(self, bot, chat_id, message_id):
            self.message = FakeMessage(bot, chat_id, message_id)

    class FakeMessage:
        def __init__(self, bot, chat_id, message_id):
            self.chat_id = chat_id
            self.message_id = message_id
            self._bot = bot

        async def edit_text(self, text, **kwargs):
            await self._bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=text,
                **kwargs
            )

        async def delete(self):
            await self._bot.delete_message(chat_id=self.chat_id, message_id=self.message_id)

    fake_update = type('FakeUpdate', (), {'callback_query': FakeQuery(context.bot, chat_id, message_id)})()
    await _show_wizard_prices_step(fake_update, context)


async def _update_wizard_message_for_prices_after_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update prices display after editing prices - regenerate chart with new prices"""
    config = get_controller_config(context)
    message_id = context.user_data.get("gs_wizard_message_id")
    chat_id = context.user_data.get("gs_wizard_chat_id")

    if not message_id or not chat_id:
        return

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = config.get("side", SIDE_LONG)
    side_str = "📈 LONG" if side == SIDE_LONG else "📉 SHORT"
    start = config.get("start_price", 0)
    end = config.get("end_price", 0)
    limit = config.get("limit_price", 0)
    current_price = context.user_data.get("gs_current_price", 0)
    candles = context.user_data.get("gs_candles")
    interval = context.user_data.get("gs_chart_interval", "5m")

    # Build interval buttons with current one highlighted
    interval_options = ["1m", "5m", "15m", "1h", "4h"]
    interval_row = []
    for opt in interval_options:
        label = f"✓ {opt}" if opt == interval else opt
        interval_row.append(InlineKeyboardButton(label, callback_data=f"bots:gs_interval:{opt}"))

    keyboard = [
        interval_row,
        [
            InlineKeyboardButton("✅ Accept Prices", callback_data="bots:gs_accept_prices"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="bots:controller_configs")],
    ]

    # Format example with current values
    example_prices = f"{start:,.6g},{end:,.6g},{limit:,.6g}"

    # Build the caption
    config_text = (
        f"*📊 {escape_markdown_v2(pair)}* \\- Grid Zone Preview\n\n"
        f"🏦 *Connector:* `{escape_markdown_v2(connector)}`\n"
        f"🎯 *Side:* `{side_str}` \\| ⚡ *Leverage:* `{config.get('leverage', 1)}x`\n"
        f"💰 *Amount:* `{config.get('total_amount_quote', 0):,.0f}`\n\n"
        f"📍 Current: `{current_price:,.6g}`\n"
        f"🟢 Start: `{start:,.6g}`\n"
        f"🔵 End: `{end:,.6g}`\n"
        f"🔴 Limit: `{limit:,.6g}`\n\n"
        f"_Type `start,end,limit` to edit_\n"
        f"_e\\.g\\. `{escape_markdown_v2(example_prices)}`_"
    )

    try:
        # Delete old message (which is a photo)
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

        # Generate new chart with updated prices
        if candles:
            chart_bytes = generate_candles_chart(
                candles, pair,
                start_price=start,
                end_price=end,
                limit_price=limit,
                current_price=current_price
            )

            # Send new photo with updated caption
            msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=chart_bytes,
                caption=config_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            # Update stored message ID
            context.user_data["gs_wizard_message_id"] = msg.message_id
        else:
            # No chart - send text message
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=config_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            context.user_data["gs_wizard_message_id"] = msg.message_id

    except Exception as e:
        logger.error(f"Error updating prices message: {e}", exc_info=True)


async def handle_gs_edit_price(update: Update, context: ContextTypes.DEFAULT_TYPE, price_type: str) -> None:
    """Handle price editing request"""
    query = update.callback_query
    config = get_controller_config(context)

    price_map = {
        "start": ("start_price", "Start Price"),
        "end": ("end_price", "End Price"),
        "limit": ("limit_price", "Limit Price"),
    }

    field, label = price_map.get(price_type, ("start_price", "Start Price"))
    current = config.get(field, 0)

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = field

    keyboard = [[InlineKeyboardButton("Cancel", callback_data="bots:gs_accept_prices")]]

    await query.message.edit_text(
        f"*Edit {escape_markdown_v2(label)}*" + "\n\n"
        f"Current: `{current:,.6g}`" + "\n\n"
        r"Enter new price:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _trigger_gs_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger save after ID edit"""
    message_id = context.user_data.get("gs_wizard_message_id")
    chat_id = context.user_data.get("gs_wizard_chat_id")

    if not message_id or not chat_id:
        return

    class FakeQuery:
        def __init__(self, bot, chat_id, message_id):
            self.message = FakeMessage(bot, chat_id, message_id)

    class FakeMessage:
        def __init__(self, bot, chat_id, message_id):
            self.chat_id = chat_id
            self.message_id = message_id
            self._bot = bot

        async def edit_text(self, text, **kwargs):
            await self._bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=text,
                **kwargs
            )

    fake_update = type('FakeUpdate', (), {'callback_query': FakeQuery(context.bot, chat_id, message_id)})()
    await handle_gs_save(fake_update, context)


# ============================================
# LEGACY FORM (for edit mode)
# ============================================

async def show_config_form(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the configuration form with current values (legacy/edit mode)"""
    query = update.callback_query
    config = get_controller_config(context)

    if not config:
        config = init_new_controller_config(context, "grid_strike")

    # Build the form display
    lines = [r"*Grid Strike Configuration*", ""]

    # Show current values
    for field_name in GRID_STRIKE_FIELD_ORDER:
        field_info = GRID_STRIKE_FIELDS[field_name]
        label = field_info["label"]

        # Get value, handling nested triple_barrier_config
        if field_name == "take_profit":
            value = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)
        elif field_name == "open_order_type":
            value = config.get("triple_barrier_config", {}).get("open_order_type", ORDER_TYPE_LIMIT_MAKER)
        elif field_name == "take_profit_order_type":
            value = config.get("triple_barrier_config", {}).get("take_profit_order_type", ORDER_TYPE_LIMIT_MAKER)
        else:
            value = config.get(field_name, "")

        formatted_value = format_config_field_value(field_name, value)
        required = "\\*" if field_info.get("required") else ""

        lines.append(f"*{escape_markdown_v2(label)}*{required}: `{escape_markdown_v2(formatted_value)}`")

    lines.append("")
    lines.append(r"_Tap a button to edit a field\. \* \= required_")

    # Build keyboard with field buttons
    keyboard = []

    # Row 1: ID and Connector
    keyboard.append([
        InlineKeyboardButton("ID", callback_data="bots:set_field:id"),
        InlineKeyboardButton("Connector", callback_data="bots:set_field:connector_name"),
        InlineKeyboardButton("Pair", callback_data="bots:set_field:trading_pair"),
    ])

    # Row 2: Side and Leverage
    keyboard.append([
        InlineKeyboardButton("Side", callback_data="bots:toggle_side"),
        InlineKeyboardButton("Leverage", callback_data="bots:set_field:leverage"),
        InlineKeyboardButton("Amount", callback_data="bots:set_field:total_amount_quote"),
    ])

    # Row 3: Prices
    keyboard.append([
        InlineKeyboardButton("Start Price", callback_data="bots:set_field:start_price"),
        InlineKeyboardButton("End Price", callback_data="bots:set_field:end_price"),
        InlineKeyboardButton("Limit Price", callback_data="bots:set_field:limit_price"),
    ])

    # Row 4: Advanced
    keyboard.append([
        InlineKeyboardButton("Max Orders", callback_data="bots:set_field:max_open_orders"),
        InlineKeyboardButton("Min Spread", callback_data="bots:set_field:min_spread_between_orders"),
        InlineKeyboardButton("Take Profit", callback_data="bots:set_field:take_profit"),
    ])

    # Row 5: Order Types
    keyboard.append([
        InlineKeyboardButton("Open Order Type", callback_data="bots:cycle_order_type:open"),
        InlineKeyboardButton("TP Order Type", callback_data="bots:cycle_order_type:tp"),
    ])

    # Row 6: Actions
    keyboard.append([
        InlineKeyboardButton("Save Config", callback_data="bots:save_config"),
        InlineKeyboardButton("Cancel", callback_data="bots:controller_configs"),
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


# ============================================
# FIELD EDITING
# ============================================

async def handle_set_field(update: Update, context: ContextTypes.DEFAULT_TYPE, field_name: str) -> None:
    """Prompt user to enter a value for a field

    Args:
        update: Telegram update
        context: Telegram context
        field_name: Name of the field to edit
    """
    query = update.callback_query

    # Special handling for connector_name - show button selector
    if field_name == "connector_name":
        await show_connector_selector(update, context)
        return

    field_info = GRID_STRIKE_FIELDS.get(field_name, {})
    label = field_info.get("label", field_name)
    hint = field_info.get("hint", "")
    field_type = field_info.get("type", "str")

    # Set state for text input
    context.user_data["bots_state"] = f"set_field:{field_name}"
    context.user_data["editing_controller_field"] = field_name

    # Get current value
    config = get_controller_config(context)
    if field_name == "take_profit":
        current = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)
    else:
        current = config.get(field_name, "")

    current_str = format_config_field_value(field_name, current)

    message = (
        f"*Set {escape_markdown_v2(label)}*\n\n"
        f"Current: `{escape_markdown_v2(current_str)}`\n\n"
    )

    if hint:
        message += f"_Hint: {escape_markdown_v2(hint)}_\n\n"

    message += r"Type the new value or tap Cancel\."

    keyboard = [[InlineKeyboardButton("Cancel", callback_data="bots:edit_config_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


# ============================================
# CONNECTOR SELECTOR
# ============================================

async def show_connector_selector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show connector selection keyboard with available CEX connectors"""
    query = update.callback_query

    try:
        client = await get_bots_client()

        # Get available CEX connectors (with cache)
        cex_connectors = await get_available_cex_connectors(context.user_data, client)

        if not cex_connectors:
            await query.answer("No CEX connectors configured", show_alert=True)
            return

        # Build connector buttons (2 per row)
        keyboard = []
        row = []

        for connector in cex_connectors:
            row.append(InlineKeyboardButton(
                connector,
                callback_data=f"bots:select_connector:{connector}"
            ))
            if len(row) == 2:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton("Cancel", callback_data="bots:edit_config_back")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        config = get_controller_config(context)
        current = config.get("connector_name", "") or "Not set"

        await query.message.edit_text(
            r"*Select Connector*" + "\n\n"
            f"Current: `{escape_markdown_v2(current)}`\n\n"
            r"Choose an exchange from your configured connectors:",
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing connector selector: {e}", exc_info=True)
        await query.answer(f"Error: {str(e)[:50]}", show_alert=True)


async def handle_select_connector(update: Update, context: ContextTypes.DEFAULT_TYPE, connector_name: str) -> None:
    """Handle connector selection from keyboard"""
    query = update.callback_query

    config = get_controller_config(context)
    config["connector_name"] = connector_name
    set_controller_config(context, config)

    await query.answer(f"Connector set to {connector_name}")

    # If we have both connector and trading pair, fetch market data
    if config.get("trading_pair"):
        await fetch_and_apply_market_data(update, context)
    else:
        await show_config_form(update, context)


# ============================================
# MARKET DATA & AUTO-PRICING
# ============================================

async def fetch_and_apply_market_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch current price and candles, apply auto-pricing, show chart"""
    query = update.callback_query
    config = get_controller_config(context)

    connector = config.get("connector_name")
    pair = config.get("trading_pair")
    side = config.get("side", SIDE_LONG)

    if not connector or not pair:
        await show_config_form(update, context)
        return

    try:
        client = await get_bots_client()

        # Show loading message
        await query.message.edit_text(
            f"Fetching market data for *{escape_markdown_v2(pair)}*\\.\\.\\.",
            parse_mode="MarkdownV2"
        )

        # Fetch current price
        current_price = await fetch_current_price(client, connector, pair)

        if current_price:
            # Cache the current price
            context.user_data["grid_strike_current_price"] = current_price

            # Calculate auto prices
            start, end, limit = calculate_auto_prices(current_price, side)
            config["start_price"] = start
            config["end_price"] = end
            config["limit_price"] = limit

            # Generate auto ID with sequence number
            existing_configs = context.user_data.get("controller_configs_list", [])
            config["id"] = generate_config_id(connector, pair, existing_configs=existing_configs)

            set_controller_config(context, config)

            # Fetch candles for chart
            candles = await fetch_candles(client, connector, pair, interval="5m", max_records=50)

            if candles:
                # Generate and send chart
                chart_bytes = generate_candles_chart(
                    candles,
                    pair,
                    start_price=start,
                    end_price=end,
                    limit_price=limit,
                    current_price=current_price
                )

                # Send chart as photo
                await query.message.reply_photo(
                    photo=chart_bytes,
                    caption=(
                        f"*{escape_markdown_v2(pair)}* Grid Zone\n\n"
                        f"Current: `{current_price:,.4f}`\n"
                        f"Start: `{start:,.4f}` \\(\\-2%\\)\n"
                        f"End: `{end:,.4f}` \\(\\+2%\\)\n"
                        f"Limit: `{limit:,.4f}`"
                    ),
                    parse_mode="MarkdownV2"
                )
            else:
                # No candles, just show price info
                await query.message.reply_text(
                    f"*{escape_markdown_v2(pair)}* Market Data\n\n"
                    f"Current Price: `{current_price:,.4f}`\n"
                    f"Auto\\-calculated grid:\n"
                    f"  Start: `{start:,.4f}`\n"
                    f"  End: `{end:,.4f}`\n"
                    f"  Limit: `{limit:,.4f}`",
                    parse_mode="MarkdownV2"
                )
        else:
            await query.message.reply_text(
                f"Could not fetch price for {pair}. Please set prices manually.",
                parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"Error fetching market data: {e}", exc_info=True)
        await query.message.reply_text(
            f"Error fetching market data: {str(e)[:100]}",
            parse_mode="HTML"
        )

    # Show the config form
    keyboard = [[InlineKeyboardButton("Continue Editing", callback_data="bots:edit_config_back")]]
    await query.message.reply_text(
        "Tap to continue editing configuration\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_toggle_side(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle the side between LONG and SHORT"""
    query = update.callback_query
    config = get_controller_config(context)

    current_side = config.get("side", SIDE_LONG)
    new_side = SIDE_SHORT if current_side == SIDE_LONG else SIDE_LONG
    config["side"] = new_side

    # Recalculate prices if we have a current price cached
    current_price = context.user_data.get("grid_strike_current_price")
    if current_price:
        start, end, limit = calculate_auto_prices(current_price, new_side)
        config["start_price"] = start
        config["end_price"] = end
        config["limit_price"] = limit

        # Regenerate ID with sequence number
        if config.get("connector_name") and config.get("trading_pair"):
            existing_configs = context.user_data.get("controller_configs_list", [])
            config["id"] = generate_config_id(
                config["connector_name"],
                config["trading_pair"],
                existing_configs=existing_configs
            )

    set_controller_config(context, config)

    # Refresh the form
    await show_config_form(update, context)


async def handle_cycle_order_type(update: Update, context: ContextTypes.DEFAULT_TYPE, order_type_key: str) -> None:
    """Cycle the order type between Market, Limit, and Limit Maker

    Args:
        update: Telegram update
        context: Telegram context
        order_type_key: 'open' for open_order_type, 'tp' for take_profit_order_type
    """
    query = update.callback_query
    config = get_controller_config(context)

    # Determine which field to update
    field_name = "open_order_type" if order_type_key == "open" else "take_profit_order_type"

    # Get current value
    if "triple_barrier_config" not in config:
        config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS["triple_barrier_config"].copy()

    current_type = config["triple_barrier_config"].get(field_name, ORDER_TYPE_LIMIT_MAKER)

    # Cycle: Limit Maker -> Market -> Limit -> Limit Maker
    order_cycle = [ORDER_TYPE_LIMIT_MAKER, ORDER_TYPE_MARKET, ORDER_TYPE_LIMIT]
    try:
        current_index = order_cycle.index(current_type)
        next_index = (current_index + 1) % len(order_cycle)
    except ValueError:
        next_index = 0

    new_type = order_cycle[next_index]
    config["triple_barrier_config"][field_name] = new_type

    set_controller_config(context, config)

    # Refresh the form
    await show_config_form(update, context)


async def process_field_input(update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str) -> None:
    """Process user input for a field

    Args:
        update: Telegram update
        context: Telegram context
        user_input: The text the user entered
    """
    field_name = context.user_data.get("editing_controller_field")

    if not field_name:
        await update.message.reply_text("No field selected. Please try again.")
        return

    field_info = GRID_STRIKE_FIELDS.get(field_name, {})
    field_type = field_info.get("type", "str")
    label = field_info.get("label", field_name)

    config = get_controller_config(context)

    try:
        # Parse the value based on type
        if field_type == "int":
            value = int(user_input)
        elif field_type == "float":
            value = float(user_input)
        else:
            value = user_input.strip()

        # Set the value
        if field_name == "take_profit":
            if "triple_barrier_config" not in config:
                config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS["triple_barrier_config"].copy()
            config["triple_barrier_config"]["take_profit"] = value
        else:
            config[field_name] = value

        set_controller_config(context, config)

        # Clear field editing state
        context.user_data.pop("editing_controller_field", None)
        context.user_data["bots_state"] = "editing_config"

        # Show success
        await update.message.reply_text(
            f"{label} set to: {value}",
            parse_mode="HTML"
        )

        # If trading_pair was set and we have a connector, fetch market data
        if field_name == "trading_pair" and config.get("connector_name"):
            # Create a fake callback query context for fetch_and_apply_market_data
            keyboard = [[InlineKeyboardButton("Fetching market data...", callback_data="bots:noop")]]
            msg = await update.message.reply_text(
                "Fetching market data\\.\\.\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            try:
                client = await get_bots_client()
                connector = config.get("connector_name")
                pair = config.get("trading_pair")
                side = config.get("side", SIDE_LONG)

                # Fetch current price
                current_price = await fetch_current_price(client, connector, pair)

                if current_price:
                    # Cache and calculate
                    context.user_data["grid_strike_current_price"] = current_price
                    start, end, limit = calculate_auto_prices(current_price, side)
                    config["start_price"] = start
                    config["end_price"] = end
                    config["limit_price"] = limit
                    existing_configs = context.user_data.get("controller_configs_list", [])
                    config["id"] = generate_config_id(connector, pair, existing_configs=existing_configs)
                    set_controller_config(context, config)

                    # Fetch candles
                    candles = await fetch_candles(client, connector, pair, interval="5m", max_records=50)

                    if candles:
                        chart_bytes = generate_candles_chart(
                            candles, pair,
                            start_price=start,
                            end_price=end,
                            limit_price=limit,
                            current_price=current_price
                        )
                        await update.message.reply_photo(
                            photo=chart_bytes,
                            caption=(
                                f"*{escape_markdown_v2(pair)}* Grid Zone\n\n"
                                f"Current: `{current_price:,.4f}`\n"
                                f"Start: `{start:,.4f}` \\(\\-2%\\)\n"
                                f"End: `{end:,.4f}` \\(\\+2%\\)\n"
                                f"Limit: `{limit:,.4f}`"
                            ),
                            parse_mode="MarkdownV2"
                        )
                    else:
                        await update.message.reply_text(
                            f"*{escape_markdown_v2(pair)}* prices auto\\-calculated\\.\n\n"
                            f"Current: `{current_price:,.4f}`",
                            parse_mode="MarkdownV2"
                        )
                else:
                    await update.message.reply_text(
                        f"Could not fetch price for {pair}. Set prices manually."
                    )

            except Exception as e:
                logger.error(f"Error fetching market data: {e}", exc_info=True)
                await update.message.reply_text(f"Error fetching market data: {str(e)[:50]}")

        # Show the form again
        keyboard = [[InlineKeyboardButton("Continue Editing", callback_data="bots:edit_config_back")]]
        await update.message.reply_text(
            "Tap to continue editing configuration\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except ValueError as e:
        await update.message.reply_text(
            f"Invalid value for {label}. Expected {field_type}. Please try again."
        )


# ============================================
# SAVE CONFIG
# ============================================

async def handle_save_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save the current config to the backend"""
    query = update.callback_query
    config = get_controller_config(context)

    # Validate required fields
    missing = []
    for field_name in GRID_STRIKE_FIELD_ORDER:
        field_info = GRID_STRIKE_FIELDS[field_name]
        if field_info.get("required"):
            if field_name == "take_profit":
                value = config.get("triple_barrier_config", {}).get("take_profit")
            else:
                value = config.get(field_name)

            if value is None or value == "" or value == 0:
                missing.append(field_info["label"])

    if missing:
        missing_str = ", ".join(missing)
        await query.answer(f"Missing required fields: {missing_str}", show_alert=True)
        return

    try:
        client = await get_bots_client()

        # Save to backend using config id as the config_name
        config_name = config.get("id", "")
        result = await client.controllers.create_or_update_controller_config(config_name, config)

        # Clear state
        clear_bots_state(context)

        keyboard = [
            [InlineKeyboardButton("Create Another", callback_data="bots:new_grid_strike")],
            [InlineKeyboardButton("Back to Configs", callback_data="bots:controller_configs")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        config_id = config.get("id", "unknown")
        await query.message.edit_text(
            f"*Config Saved\\!*\n\n"
            f"Controller `{escape_markdown_v2(config_id)}` has been saved successfully\\.",
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error saving config: {e}", exc_info=True)
        await query.answer(f"Failed to save: {str(e)[:100]}", show_alert=True)


# ============================================
# EDIT EXISTING CONFIG
# ============================================

async def handle_edit_config(update: Update, context: ContextTypes.DEFAULT_TYPE, config_index: int) -> None:
    """Load an existing config for editing

    Args:
        update: Telegram update
        context: Telegram context
        config_index: Index in the configs list
    """
    query = update.callback_query
    configs_list = context.user_data.get("controller_configs_list", [])

    if config_index >= len(configs_list):
        await query.answer("Config not found", show_alert=True)
        return

    config = configs_list[config_index].copy()
    set_controller_config(context, config)
    context.user_data["bots_state"] = "editing_config"

    await show_config_form(update, context)


# ============================================
# DEPLOY CONTROLLERS
# ============================================

# Default deploy settings
DEPLOY_DEFAULTS = {
    "instance_name": "",
    "credentials_profile": "master_account",
    "controllers_config": [],
    "max_global_drawdown_quote": None,
    "max_controller_drawdown_quote": None,
    "image": "hummingbot/hummingbot:latest",
}

# Deploy field configuration for progressive flow
DEPLOY_FIELDS = {
    "instance_name": {
        "label": "Instance Name",
        "required": True,
        "hint": "Name for your bot instance (e.g. my_grid_bot)",
        "type": "str",
        "default": None,
    },
    "credentials_profile": {
        "label": "Credentials Profile",
        "required": True,
        "hint": "Account profile with exchange credentials",
        "type": "str",
        "default": "master_account",
    },
    "max_global_drawdown_quote": {
        "label": "Max Global Drawdown",
        "required": False,
        "hint": "Maximum total loss in quote currency (e.g. 1000 USDT)",
        "type": "float",
        "default": None,
    },
    "max_controller_drawdown_quote": {
        "label": "Max Controller Drawdown",
        "required": False,
        "hint": "Maximum loss per controller in quote currency",
        "type": "float",
        "default": None,
    },
    "image": {
        "label": "Docker Image",
        "required": False,
        "hint": "Hummingbot image to use",
        "type": "str",
        "default": "hummingbot/hummingbot:latest",
    },
}

# Field order for progressive flow
DEPLOY_FIELD_ORDER = [
    "instance_name",
    "credentials_profile",
    "max_global_drawdown_quote",
    "max_controller_drawdown_quote",
    "image",
]


async def show_deploy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the deploy controllers menu"""
    query = update.callback_query

    try:
        client = await get_bots_client()
        configs = await client.controllers.list_controller_configs()

        if not configs:
            keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
            await query.message.edit_text(
                r"*Deploy Controllers*" + "\n\n"
                r"No configurations available to deploy\." + "\n"
                r"Create a controller config first\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        # Store configs and initialize selection
        context.user_data["controller_configs_list"] = configs
        selected = context.user_data.get("selected_controllers", set())

        # Build message
        lines = [r"*Deploy Controllers*", ""]
        lines.append(r"Select controllers to deploy:")
        lines.append("")

        # Build keyboard with checkboxes
        keyboard = []

        for i, config in enumerate(configs):
            config_id = config.get("id", config.get("config_name", f"config_{i}"))
            is_selected = i in selected
            checkbox = "[x]" if is_selected else "[ ]"

            keyboard.append([
                InlineKeyboardButton(
                    f"{checkbox} {config_id[:25]}",
                    callback_data=f"bots:toggle_deploy:{i}"
                )
            ])

        # Action buttons
        keyboard.append([
            InlineKeyboardButton("Select All", callback_data="bots:select_all"),
            InlineKeyboardButton("Clear All", callback_data="bots:clear_all"),
        ])

        if selected:
            keyboard.append([
                InlineKeyboardButton(f"Next: Configure ({len(selected)})", callback_data="bots:deploy_configure"),
            ])

        keyboard.append([
            InlineKeyboardButton("Back", callback_data="bots:main_menu"),
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await query.message.edit_text(
                "\n".join(lines),
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise

    except Exception as e:
        logger.error(f"Error loading deploy menu: {e}", exc_info=True)
        error_msg = format_error_message(f"Failed to load configs: {str(e)}")
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        await query.message.edit_text(
            error_msg,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_toggle_deploy_selection(update: Update, context: ContextTypes.DEFAULT_TYPE, index: int) -> None:
    """Toggle selection of a controller for deployment"""
    selected = context.user_data.get("selected_controllers", set())

    if index in selected:
        selected.discard(index)
    else:
        selected.add(index)

    context.user_data["selected_controllers"] = selected
    await show_deploy_menu(update, context)


async def handle_select_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Select all controllers for deployment"""
    configs = context.user_data.get("controller_configs_list", [])
    context.user_data["selected_controllers"] = set(range(len(configs)))
    await show_deploy_menu(update, context)


async def handle_clear_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear all selections"""
    context.user_data["selected_controllers"] = set()
    await show_deploy_menu(update, context)


async def show_deploy_configure(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the streamlined deployment configuration flow"""
    # Use the new streamlined deploy flow
    await show_deploy_config_step(update, context)


async def show_deploy_form(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the deployment configuration form with current values"""
    query = update.callback_query
    deploy_params = context.user_data.get("deploy_params", DEPLOY_DEFAULTS.copy())

    # Build display
    lines = [r"*Deploy Configuration*", ""]

    instance = deploy_params.get("instance_name", "") or "Not set"
    creds = deploy_params.get("credentials_profile", "") or "Not set"
    controllers = deploy_params.get("controllers_config", [])
    controllers_str = ", ".join(controllers) if controllers else "None"
    max_global = deploy_params.get("max_global_drawdown_quote")
    max_controller = deploy_params.get("max_controller_drawdown_quote")
    image = deploy_params.get("image", "hummingbot/hummingbot:latest")

    lines.append(f"*Instance Name*\\*: `{escape_markdown_v2(instance)}`")
    lines.append(f"*Credentials Profile*\\*: `{escape_markdown_v2(creds)}`")
    lines.append(f"*Controllers*: `{escape_markdown_v2(controllers_str[:50])}`")
    lines.append(f"*Max Global DD*: `{max_global if max_global else 'Not set'}`")
    lines.append(f"*Max Controller DD*: `{max_controller if max_controller else 'Not set'}`")
    lines.append(f"*Image*: `{escape_markdown_v2(image)}`")
    lines.append("")
    lines.append(r"_\* \= required_")

    # Build keyboard
    keyboard = [
        [
            InlineKeyboardButton("Instance Name", callback_data="bots:deploy_set:instance_name"),
            InlineKeyboardButton("Credentials", callback_data="bots:deploy_set:credentials_profile"),
        ],
        [
            InlineKeyboardButton("Max Global DD", callback_data="bots:deploy_set:max_global_drawdown_quote"),
            InlineKeyboardButton("Max Controller DD", callback_data="bots:deploy_set:max_controller_drawdown_quote"),
        ],
        [
            InlineKeyboardButton("Image", callback_data="bots:deploy_set:image"),
        ],
    ]

    # Check if ready to deploy
    can_deploy = bool(deploy_params.get("instance_name") and deploy_params.get("credentials_profile"))

    if can_deploy:
        keyboard.append([
            InlineKeyboardButton("Deploy Now", callback_data="bots:execute_deploy"),
        ])

    keyboard.append([
        InlineKeyboardButton("Back to Selection", callback_data="bots:deploy_menu"),
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


# ============================================
# PROGRESSIVE DEPLOY CONFIGURATION FLOW
# ============================================

async def show_deploy_progressive_form(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the progressive deployment configuration form"""
    query = update.callback_query

    deploy_params = context.user_data.get("deploy_params", DEPLOY_DEFAULTS.copy())
    current_field = context.user_data.get("deploy_current_field", DEPLOY_FIELD_ORDER[0])

    message_text, reply_markup = _build_deploy_progressive_message(
        deploy_params, current_field, context
    )

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )
    await query.answer()


def _build_deploy_progressive_message(deploy_params: dict, current_field: str, context) -> tuple:
    """Build the progressive deploy configuration message."""
    controllers = deploy_params.get("controllers_config", [])
    controllers_str = ", ".join(controllers) if controllers else "None"

    lines = [r"*Deploy Configuration*", ""]
    lines.append(f"*Controllers:* `{escape_markdown_v2(controllers_str[:40])}`")
    lines.append("")

    for field_name in DEPLOY_FIELD_ORDER:
        field_info = DEPLOY_FIELDS[field_name]
        label = field_info["label"]
        required = "\\*" if field_info.get("required") else ""
        value = deploy_params.get(field_name)

        if value is not None and value != "":
            value_display = str(value)
            if field_name == "credentials_profile" and value == "master_account":
                value_display = "master_account (default)"
        else:
            default = field_info.get("default")
            value_display = f"{default} (default)" if default else "Not set"

        if field_name == current_field:
            lines.append(f"➡️ *{escape_markdown_v2(label)}*{required}: _awaiting input_")
        elif DEPLOY_FIELD_ORDER.index(field_name) < DEPLOY_FIELD_ORDER.index(current_field):
            lines.append(f"✅ *{escape_markdown_v2(label)}*{required}: `{escape_markdown_v2(value_display)}`")
        else:
            lines.append(f"⬜ *{escape_markdown_v2(label)}*{required}: `{escape_markdown_v2(value_display)}`")

    field_info = DEPLOY_FIELDS.get(current_field, {})
    hint = field_info.get("hint", "")
    if hint:
        lines.append("")
        lines.append(f"_Hint: {escape_markdown_v2(hint)}_")

    lines.append("")
    lines.append(r"_Type a value or use the buttons below\._")

    keyboard = []
    default_value = DEPLOY_FIELDS.get(current_field, {}).get("default")
    if default_value:
        keyboard.append([
            InlineKeyboardButton(f"Use Default: {default_value[:20]}", callback_data=f"bots:deploy_use_default:{current_field}")
        ])

    if not DEPLOY_FIELDS.get(current_field, {}).get("required"):
        keyboard.append([InlineKeyboardButton("Skip (keep default)", callback_data="bots:deploy_skip_field")])

    nav_buttons = []
    current_index = DEPLOY_FIELD_ORDER.index(current_field)
    if current_index > 0:
        nav_buttons.append(InlineKeyboardButton("« Back", callback_data="bots:deploy_prev_field"))
    nav_buttons.append(InlineKeyboardButton("❌ Cancel", callback_data="bots:deploy_menu"))
    keyboard.append(nav_buttons)

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


async def handle_deploy_progressive_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text input during progressive deploy configuration"""
    current_field = context.user_data.get("deploy_current_field")
    bots_state = context.user_data.get("bots_state")

    if bots_state != "deploy_progressive" or not current_field:
        return

    try:
        await update.message.delete()
    except:
        pass

    user_input = update.message.text.strip()
    field_info = DEPLOY_FIELDS.get(current_field, {})
    field_type = field_info.get("type", "str")

    try:
        if field_type == "float":
            value = float(user_input) if user_input else None
        elif field_type == "int":
            value = int(user_input) if user_input else None
        else:
            value = user_input

        deploy_params = context.user_data.get("deploy_params", DEPLOY_DEFAULTS.copy())
        deploy_params[current_field] = value
        context.user_data["deploy_params"] = deploy_params

        await _advance_deploy_field(update, context)

    except ValueError:
        import asyncio
        bot = update.get_bot()
        chat_id = context.user_data.get("deploy_chat_id", update.effective_chat.id)
        error_msg = await bot.send_message(chat_id=chat_id, text=f"❌ Invalid value. Please enter a valid {field_type}.")
        await asyncio.sleep(3)
        try:
            await error_msg.delete()
        except:
            pass


async def _advance_deploy_field(update: Update, context) -> None:
    """Advance to the next deploy field or show summary"""
    current_field = context.user_data.get("deploy_current_field")
    current_index = DEPLOY_FIELD_ORDER.index(current_field)

    if current_index < len(DEPLOY_FIELD_ORDER) - 1:
        next_field = DEPLOY_FIELD_ORDER[current_index + 1]
        context.user_data["deploy_current_field"] = next_field
        await _update_deploy_progressive_message(context, update.get_bot())
    else:
        context.user_data["bots_state"] = "deploy_review"
        context.user_data.pop("deploy_current_field", None)
        await _show_deploy_summary(context, update.get_bot())


async def _update_deploy_progressive_message(context, bot) -> None:
    """Update the deploy progressive message with current progress"""
    message_id = context.user_data.get("deploy_message_id")
    chat_id = context.user_data.get("deploy_chat_id")
    current_field = context.user_data.get("deploy_current_field")
    deploy_params = context.user_data.get("deploy_params", {})

    if not message_id or not chat_id:
        return

    message_text, reply_markup = _build_deploy_progressive_message(deploy_params, current_field, context)

    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=message_text, parse_mode="MarkdownV2", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error updating deploy message: {e}")


async def _show_deploy_summary(context, bot) -> None:
    """Show deployment summary before executing"""
    message_id = context.user_data.get("deploy_message_id")
    chat_id = context.user_data.get("deploy_chat_id")
    deploy_params = context.user_data.get("deploy_params", {})

    if not message_id or not chat_id:
        return

    controllers = deploy_params.get("controllers_config", [])
    controllers_str = ", ".join(controllers) if controllers else "None"

    lines = [r"*Deploy Configuration \- Review*", ""]
    lines.append(f"*Controllers:* `{escape_markdown_v2(controllers_str)}`")
    lines.append("")

    for field_name in DEPLOY_FIELD_ORDER:
        field_info = DEPLOY_FIELDS[field_name]
        label = field_info["label"]
        required = "\\*" if field_info.get("required") else ""
        value = deploy_params.get(field_name)

        if value is not None and value != "":
            value_display = str(value)
        else:
            default = field_info.get("default")
            if default:
                deploy_params[field_name] = default
                value_display = str(default)
            else:
                value_display = "Not set"

        lines.append(f"✅ *{escape_markdown_v2(label)}*{required}: `{escape_markdown_v2(value_display)}`")

    context.user_data["deploy_params"] = deploy_params

    lines.append("")
    lines.append(r"_Ready to deploy\? Tap Deploy Now or edit any field\._")

    keyboard = []
    field_buttons = []
    for field_name in DEPLOY_FIELD_ORDER:
        label = DEPLOY_FIELDS[field_name]["label"]
        field_buttons.append(InlineKeyboardButton(f"✏️ {label[:15]}", callback_data=f"bots:deploy_edit:{field_name}"))

    for i in range(0, len(field_buttons), 2):
        keyboard.append(field_buttons[i:i+2])

    keyboard.append([InlineKeyboardButton("🚀 Deploy Now", callback_data="bots:execute_deploy")])
    keyboard.append([InlineKeyboardButton("« Back to Selection", callback_data="bots:deploy_menu")])

    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="\n".join(lines), parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Error showing deploy summary: {e}")


async def handle_deploy_use_default(update: Update, context: ContextTypes.DEFAULT_TYPE, field_name: str) -> None:
    """Use default value for a deploy field"""
    query = update.callback_query
    field_info = DEPLOY_FIELDS.get(field_name, {})
    default = field_info.get("default")

    if default:
        deploy_params = context.user_data.get("deploy_params", DEPLOY_DEFAULTS.copy())
        deploy_params[field_name] = default
        context.user_data["deploy_params"] = deploy_params

    await _advance_deploy_field(update, context)
    await query.answer()


async def handle_deploy_skip_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Skip the current optional deploy field"""
    query = update.callback_query
    current_field = context.user_data.get("deploy_current_field")
    field_info = DEPLOY_FIELDS.get(current_field, {})
    default = field_info.get("default")

    deploy_params = context.user_data.get("deploy_params", DEPLOY_DEFAULTS.copy())
    deploy_params[current_field] = default
    context.user_data["deploy_params"] = deploy_params

    await _advance_deploy_field(update, context)
    await query.answer()


async def handle_deploy_prev_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Go back to the previous deploy field"""
    query = update.callback_query
    current_field = context.user_data.get("deploy_current_field")
    current_index = DEPLOY_FIELD_ORDER.index(current_field)

    if current_index > 0:
        prev_field = DEPLOY_FIELD_ORDER[current_index - 1]
        context.user_data["deploy_current_field"] = prev_field
        await show_deploy_progressive_form(update, context)
    else:
        await query.answer("Already at first field")


async def handle_deploy_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE, field_name: str) -> None:
    """Edit a specific field from the summary view"""
    query = update.callback_query
    context.user_data["deploy_current_field"] = field_name
    context.user_data["bots_state"] = "deploy_progressive"
    await show_deploy_progressive_form(update, context)


async def handle_deploy_set_field(update: Update, context: ContextTypes.DEFAULT_TYPE, field_name: str) -> None:
    """Prompt user to enter a value for a deploy field"""
    query = update.callback_query

    labels = {
        "instance_name": "Instance Name",
        "credentials_profile": "Credentials Profile",
        "max_global_drawdown_quote": "Max Global Drawdown (Quote)",
        "max_controller_drawdown_quote": "Max Controller Drawdown (Quote)",
        "image": "Docker Image",
    }

    hints = {
        "instance_name": "e.g. my_grid_bot",
        "credentials_profile": "e.g. binance_main",
        "max_global_drawdown_quote": "e.g. 1000 (in USDT)",
        "max_controller_drawdown_quote": "e.g. 500 (in USDT)",
        "image": "e.g. hummingbot/hummingbot:latest",
    }

    label = labels.get(field_name, field_name)
    hint = hints.get(field_name, "")

    # Set state for text input
    context.user_data["bots_state"] = f"deploy_set:{field_name}"
    context.user_data["editing_deploy_field"] = field_name

    # Get current value
    deploy_params = context.user_data.get("deploy_params", {})
    current = deploy_params.get(field_name, "")
    current_str = str(current) if current else "Not set"

    message = (
        f"*Set {escape_markdown_v2(label)}*\n\n"
        f"Current: `{escape_markdown_v2(current_str)}`\n\n"
    )

    if hint:
        message += f"_Hint: {escape_markdown_v2(hint)}_\n\n"

    message += r"Type the new value or tap Cancel\."

    keyboard = [[InlineKeyboardButton("Cancel", callback_data="bots:deploy_form_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def process_deploy_field_input(update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str) -> None:
    """Process user input for a deploy field"""
    field_name = context.user_data.get("editing_deploy_field")

    if not field_name:
        await update.message.reply_text("No field selected. Please try again.")
        return

    deploy_params = context.user_data.get("deploy_params", DEPLOY_DEFAULTS.copy())

    try:
        # Parse the value based on field type
        if field_name in ["max_global_drawdown_quote", "max_controller_drawdown_quote"]:
            value = float(user_input) if user_input.strip() else None
        else:
            value = user_input.strip()

        # Set the value
        deploy_params[field_name] = value
        context.user_data["deploy_params"] = deploy_params

        # Clear field editing state
        context.user_data.pop("editing_deploy_field", None)
        context.user_data["bots_state"] = "deploy_configure"

        # Show confirmation
        label = field_name.replace("_", " ").title()
        await update.message.reply_text(f"{label} set to: {value}")

        # Show button to return to form
        keyboard = [[InlineKeyboardButton("Continue", callback_data="bots:deploy_form_back")]]
        await update.message.reply_text(
            "Value updated\\. Tap to continue\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except ValueError as e:
        await update.message.reply_text(f"Invalid value. Please enter a valid number.")


async def handle_execute_deploy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute the deployment of selected controllers"""
    query = update.callback_query

    deploy_params = context.user_data.get("deploy_params", {})

    instance_name = deploy_params.get("instance_name")
    credentials_profile = deploy_params.get("credentials_profile")
    controllers_config = deploy_params.get("controllers_config", [])

    if not instance_name or not credentials_profile:
        await query.answer("Instance name and credentials are required", show_alert=True)
        return

    if not controllers_config:
        await query.answer("No controllers selected", show_alert=True)
        return

    # Show deploying message FIRST (before the long operation)
    controllers_str = ", ".join([f"`{escape_markdown_v2(c)}`" for c in controllers_config])
    await query.message.edit_text(
        f"*Deploying\\.\\.\\.*\n\n"
        f"*Instance:* `{escape_markdown_v2(instance_name)}`\n"
        f"*Controllers:*\n{controllers_str}\n\n"
        f"Please wait, this may take a moment\\.\\.\\.",
        parse_mode="MarkdownV2"
    )

    try:
        client = await get_bots_client()

        # Deploy using deploy_v2_controllers (this can take time)
        result = await client.bot_orchestration.deploy_v2_controllers(
            instance_name=instance_name,
            credentials_profile=credentials_profile,
            controllers_config=controllers_config,
            max_global_drawdown_quote=deploy_params.get("max_global_drawdown_quote"),
            max_controller_drawdown_quote=deploy_params.get("max_controller_drawdown_quote"),
            image=deploy_params.get("image", "hummingbot/hummingbot:latest"),
        )

        # Clear deploy state
        context.user_data.pop("selected_controllers", None)
        context.user_data.pop("deploy_params", None)
        context.user_data.pop("bots_state", None)

        keyboard = [
            [InlineKeyboardButton("View Bots", callback_data="bots:main_menu")],
            [InlineKeyboardButton("Deploy More", callback_data="bots:deploy_menu")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        status = result.get("status", "unknown")
        message = result.get("message", "")

        # Check for success - either status is "success" or message indicates success
        is_success = (
            status == "success" or
            "successfully" in message.lower() or
            "created" in message.lower()
        )

        if is_success:
            await query.message.edit_text(
                f"*Deployment Started\\!*\n\n"
                f"*Instance:* `{escape_markdown_v2(instance_name)}`\n"
                f"*Controllers:*\n{controllers_str}\n\n"
                f"The bot is being deployed\\. Check status in Bots menu\\.",
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        else:
            error_msg = message or "Unknown error"
            await query.message.edit_text(
                f"*Deployment Failed*\n\n"
                f"Error: {escape_markdown_v2(error_msg)}",
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error deploying controllers: {e}", exc_info=True)
        # Use message edit instead of query.answer (which may have expired)
        keyboard = [
            [InlineKeyboardButton("Try Again", callback_data="bots:execute_deploy")],
            [InlineKeyboardButton("Back", callback_data="bots:deploy_form_back")],
        ]
        await query.message.edit_text(
            f"*Deployment Failed*\n\n"
            f"Error: {escape_markdown_v2(str(e)[:200])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# ============================================
# STREAMLINED DEPLOY FLOW
# ============================================

# Available docker images
AVAILABLE_IMAGES = [
    "hummingbot/hummingbot:latest",
    "hummingbot/hummingbot:development",
]


async def _get_available_credentials(client) -> List[str]:
    """Fetch list of available credential profiles from the backend"""
    try:
        accounts = await client.accounts.list_accounts()
        return accounts if accounts else ["master_account"]
    except Exception as e:
        logger.warning(f"Could not fetch accounts, using default: {e}")
        return ["master_account"]


async def show_deploy_config_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show streamlined deploy configuration with clickable buttons for name, credentials, and image"""
    query = update.callback_query

    selected = context.user_data.get("selected_controllers", set())
    configs = context.user_data.get("controller_configs_list", [])

    if not selected:
        await query.answer("No controllers selected", show_alert=True)
        return

    # Get selected config names
    controller_names = [
        configs[i].get("id", configs[i].get("config_name", f"config_{i}"))
        for i in selected if i < len(configs)
    ]

    # Initialize or get deploy params
    deploy_params = context.user_data.get("deploy_params", {})
    if not deploy_params.get("controllers_config"):
        creds_default = "master_account"
        deploy_params = {
            "controllers_config": controller_names,
            "credentials_profile": creds_default,
            "image": "hummingbot/hummingbot:latest",
            "instance_name": creds_default,  # Default name = credentials profile
        }
    context.user_data["deploy_params"] = deploy_params
    context.user_data["deploy_message_id"] = query.message.message_id
    context.user_data["deploy_chat_id"] = query.message.chat_id

    # Build message
    creds = deploy_params.get("credentials_profile", "master_account")
    image = deploy_params.get("image", "hummingbot/hummingbot:latest")
    instance_name = deploy_params.get("instance_name", creds)

    # Build controllers list in code block for readability
    controllers_block = "\n".join(controller_names)
    image_short = image.split("/")[-1] if "/" in image else image

    lines = [
        r"*🚀 Deploy Controllers*",
        "",
        "```",
        controllers_block,
        "```",
        "",
        f"*Name:*     `{escape_markdown_v2(instance_name)}`",
        f"*Account:*  `{escape_markdown_v2(creds)}`",
        f"*Image:*    `{escape_markdown_v2(image_short)}`",
        "",
        r"_Tap buttons below to change settings_",
    ]

    # Build keyboard - one button per row for better readability
    keyboard = [
        [InlineKeyboardButton(f"📝 Name: {instance_name[:25]}", callback_data="bots:select_name:_show")],
        [InlineKeyboardButton(f"👤 Account: {creds}", callback_data="bots:select_creds:_show")],
        [InlineKeyboardButton(f"🐳 Image: {image_short}", callback_data="bots:select_image:_show")],
        [InlineKeyboardButton("✅ Deploy Now", callback_data="bots:execute_deploy")],
        [InlineKeyboardButton("« Back", callback_data="bots:deploy_menu")],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Set drawdowns to None (skip them)
    deploy_params["max_global_drawdown_quote"] = None
    deploy_params["max_controller_drawdown_quote"] = None
    context.user_data["deploy_params"] = deploy_params

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_select_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE, creds: str) -> None:
    """Handle credentials profile selection"""
    query = update.callback_query

    if creds == "_show":
        # Show available credentials profiles
        try:
            client = await get_bots_client()
            available_creds = await _get_available_credentials(client)
        except Exception:
            available_creds = ["master_account"]

        deploy_params = context.user_data.get("deploy_params", {})
        current = deploy_params.get("credentials_profile", "master_account")

        lines = [
            r"*Select Credentials Profile*",
            "",
            f"Current: `{escape_markdown_v2(current)}`",
            "",
            r"_Choose an account to deploy with:_",
        ]

        # Build buttons for each credential profile
        keyboard = []
        for acc in available_creds:
            marker = "✓ " if acc == current else ""
            keyboard.append([
                InlineKeyboardButton(f"{marker}{acc}", callback_data=f"bots:select_creds:{acc}")
            ])

        keyboard.append([
            InlineKeyboardButton("« Back", callback_data="bots:deploy_config"),
        ])

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        # Set the selected credential profile
        deploy_params = context.user_data.get("deploy_params", {})
        deploy_params["credentials_profile"] = creds
        context.user_data["deploy_params"] = deploy_params

        await query.answer(f"Account set to {creds}")
        await show_deploy_config_step(update, context)


async def handle_select_image(update: Update, context: ContextTypes.DEFAULT_TYPE, image: str) -> None:
    """Handle docker image selection"""
    query = update.callback_query

    if image == "_show":
        # Show available images
        deploy_params = context.user_data.get("deploy_params", {})
        current = deploy_params.get("image", "hummingbot/hummingbot:latest")

        lines = [
            r"*Select Docker Image*",
            "",
            f"Current: `{escape_markdown_v2(current)}`",
            "",
            r"_Choose an image to deploy with:_",
        ]

        # Build buttons for each image
        keyboard = []
        for img in AVAILABLE_IMAGES:
            marker = "✓ " if img == current else ""
            img_short = img.split("/")[-1] if "/" in img else img
            keyboard.append([
                InlineKeyboardButton(f"{marker}{img_short}", callback_data=f"bots:select_image:{img}")
            ])

        keyboard.append([
            InlineKeyboardButton("« Back", callback_data="bots:deploy_config"),
        ])

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        # Set the selected image
        deploy_params = context.user_data.get("deploy_params", {})
        deploy_params["image"] = image
        context.user_data["deploy_params"] = deploy_params

        img_short = image.split("/")[-1] if "/" in image else image
        await query.answer(f"Image set to {img_short}")
        await show_deploy_config_step(update, context)


async def handle_select_instance_name(update: Update, context: ContextTypes.DEFAULT_TYPE, name: str) -> None:
    """Handle instance name selection/editing"""
    query = update.callback_query

    if name == "_show":
        # Show name editing prompt
        deploy_params = context.user_data.get("deploy_params", {})
        creds = deploy_params.get("credentials_profile", "master_account")
        current = deploy_params.get("instance_name", creds)

        lines = [
            r"*Edit Instance Name*",
            "",
            f"Current: `{escape_markdown_v2(current)}`",
            "",
            r"_Send a new name or choose an option:_",
        ]

        keyboard = [
            [InlineKeyboardButton(f"✓ Use: {creds}", callback_data=f"bots:select_name:{creds}")],
            [InlineKeyboardButton("« Back", callback_data="bots:deploy_config")],
        ]

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        # Set state to allow custom name input
        context.user_data["bots_state"] = "deploy_edit_name"
    else:
        # Set the selected name
        deploy_params = context.user_data.get("deploy_params", {})
        deploy_params["instance_name"] = name
        context.user_data["deploy_params"] = deploy_params
        context.user_data["bots_state"] = None

        await query.answer(f"Name set to {name[:25]}")
        await show_deploy_config_step(update, context)


async def process_instance_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str) -> None:
    """Process custom instance name input from user text message"""
    try:
        await update.message.delete()
    except:
        pass

    custom_name = user_input.strip()
    if not custom_name:
        return

    # Set the custom name
    deploy_params = context.user_data.get("deploy_params", {})
    deploy_params["instance_name"] = custom_name
    context.user_data["deploy_params"] = deploy_params
    context.user_data["bots_state"] = None

    # Update the config step message
    message_id = context.user_data.get("deploy_message_id")
    chat_id = context.user_data.get("deploy_chat_id")

    if message_id and chat_id:
        # Create a fake update/query to reuse show_deploy_config_step logic
        # We need to update the existing message, so we'll do it manually
        creds = deploy_params.get("credentials_profile", "master_account")
        image = deploy_params.get("image", "hummingbot/hummingbot:latest")
        controllers = deploy_params.get("controllers_config", [])

        controllers_block = "\n".join(controllers)
        image_short = image.split("/")[-1] if "/" in image else image

        lines = [
            r"*🚀 Deploy Controllers*",
            "",
            "```",
            controllers_block,
            "```",
            "",
            f"*Name:*     `{escape_markdown_v2(custom_name)}`",
            f"*Account:*  `{escape_markdown_v2(creds)}`",
            f"*Image:*    `{escape_markdown_v2(image_short)}`",
            "",
            r"_Tap buttons below to change settings_",
        ]

        keyboard = [
            [InlineKeyboardButton(f"📝 Name: {custom_name[:25]}", callback_data="bots:select_name:_show")],
            [InlineKeyboardButton(f"👤 Account: {creds}", callback_data="bots:select_creds:_show")],
            [InlineKeyboardButton(f"🐳 Image: {image_short}", callback_data="bots:select_image:_show")],
            [InlineKeyboardButton("✅ Deploy Now", callback_data="bots:execute_deploy")],
            [InlineKeyboardButton("« Back", callback_data="bots:deploy_menu")],
        ]

        try:
            await update.get_bot().edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="\n".join(lines),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"Error updating deploy config message: {e}")


async def handle_deploy_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show deployment confirmation with auto-generated instance name"""
    query = update.callback_query

    deploy_params = context.user_data.get("deploy_params", {})
    controllers = deploy_params.get("controllers_config", [])
    creds = deploy_params.get("credentials_profile", "master_account")
    image = deploy_params.get("image", "hummingbot/hummingbot:latest")

    if not controllers:
        await query.answer("No controllers selected", show_alert=True)
        return

    # Instance name is just the credentials profile - API adds timestamp
    generated_name = creds

    # Store for later use
    context.user_data["deploy_generated_name"] = generated_name

    controllers_str = "\n".join([f"• `{escape_markdown_v2(c)}`" for c in controllers])
    image_short = image.split("/")[-1] if "/" in image else image

    lines = [
        r"*Confirm Deployment*",
        "",
        r"*Controllers:*",
        controllers_str,
        "",
        f"*Account:* `{escape_markdown_v2(creds)}`",
        f"*Image:* `{escape_markdown_v2(image_short)}`",
        "",
        r"*Instance Name:*",
        f"`{escape_markdown_v2(generated_name)}`",
        "",
        r"_Click the name to deploy, or send a custom name_",
    ]

    keyboard = [
        [
            InlineKeyboardButton(f"✅ Deploy as {generated_name[:25]}", callback_data="bots:execute_deploy"),
        ],
        [
            InlineKeyboardButton("« Back", callback_data="bots:deploy_config"),
        ],
    ]

    # Set state to allow custom name input
    context.user_data["bots_state"] = "deploy_custom_name"

    # Store the generated name in deploy_params
    deploy_params["instance_name"] = generated_name
    # Set drawdowns to None (skip them)
    deploy_params["max_global_drawdown_quote"] = None
    deploy_params["max_controller_drawdown_quote"] = None
    context.user_data["deploy_params"] = deploy_params

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_deploy_custom_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle custom instance name input (called from message handler)"""
    # This is triggered via message handler when in deploy_custom_name state
    pass  # The actual processing happens in process_deploy_custom_name_input


async def process_deploy_custom_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str) -> None:
    """Process custom instance name input and execute deployment"""
    try:
        await update.message.delete()
    except:
        pass

    custom_name = user_input.strip()
    if not custom_name:
        return

    deploy_params = context.user_data.get("deploy_params", {})
    deploy_params["instance_name"] = custom_name
    context.user_data["deploy_params"] = deploy_params

    # Execute deployment with custom name
    message_id = context.user_data.get("deploy_message_id")
    chat_id = context.user_data.get("deploy_chat_id")

    if not message_id or not chat_id:
        return

    controllers = deploy_params.get("controllers_config", [])
    creds = deploy_params.get("credentials_profile", "master_account")
    image = deploy_params.get("image", "hummingbot/hummingbot:latest")

    controllers_str = ", ".join([f"`{escape_markdown_v2(c)}`" for c in controllers])

    # Update message to show deploying
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                f"*Deploying\\.\\.\\.*\n\n"
                f"*Instance:* `{escape_markdown_v2(custom_name)}`\n"
                f"*Controllers:* {controllers_str}\n\n"
                f"Please wait, this may take a moment\\.\\.\\."
            ),
            parse_mode="MarkdownV2"
        )
    except Exception as e:
        logger.error(f"Error updating deploy message: {e}")

    try:
        client = await get_bots_client()

        result = await client.bot_orchestration.deploy_v2_controllers(
            instance_name=custom_name,
            credentials_profile=creds,
            controllers_config=controllers,
            max_global_drawdown_quote=None,
            max_controller_drawdown_quote=None,
            image=image,
        )

        # Clear deploy state
        context.user_data.pop("selected_controllers", None)
        context.user_data.pop("deploy_params", None)
        context.user_data.pop("bots_state", None)
        context.user_data.pop("deploy_generated_name", None)

        keyboard = [
            [InlineKeyboardButton("View Bots", callback_data="bots:main_menu")],
            [InlineKeyboardButton("Deploy More", callback_data="bots:deploy_menu")],
        ]

        status = result.get("status", "unknown")
        message = result.get("message", "")
        is_success = (
            status == "success" or
            "successfully" in message.lower() or
            "created" in message.lower()
        )

        if is_success:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"*Deployment Started\\!*\n\n"
                    f"*Instance:* `{escape_markdown_v2(custom_name)}`\n"
                    f"*Controllers:* {controllers_str}\n\n"
                    f"The bot is being deployed\\. Check status in Bots menu\\."
                ),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            error_msg = message or "Unknown error"
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"*Deployment Failed*\n\n"
                    f"Error: {escape_markdown_v2(error_msg)}"
                ),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    except Exception as e:
        logger.error(f"Error deploying with custom name: {e}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton("Try Again", callback_data="bots:deploy_confirm")],
            [InlineKeyboardButton("Back", callback_data="bots:deploy_config")],
        ]
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                f"*Deployment Failed*\n\n"
                f"Error: {escape_markdown_v2(str(e)[:200])}"
            ),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
