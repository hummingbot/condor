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
import copy
import logging
from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputMediaPhoto
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from handlers.cex._shared import (
    get_cex_balances,
    get_correct_pair_format,
    get_trading_rules,
    validate_trading_pair,
)
from utils.telegram_formatters import escape_markdown_v2, format_error_message

from .controllers import get_controller_info, get_supported_controller_types

from ._shared import (
    GRID_STRIKE_DEFAULTS,
    GRID_STRIKE_FIELD_ORDER,
    GRID_STRIKE_FIELDS,
    GS_EDITABLE_FIELDS,
    ORDER_TYPE_LABELS,
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_LIMIT_MAKER,
    ORDER_TYPE_MARKET,
    SIDE_LONG,
    SIDE_SHORT,
    calculate_auto_prices,
    clear_bots_state,
    fetch_candles,
    fetch_current_price,
    format_config_field_value,
    generate_candles_chart,
    generate_config_id,
    get_available_cex_connectors,
    get_bots_client,
    get_controller_config,
    init_new_controller_config,
    set_controller_config,
)
from .controllers.grid_strike.grid_analysis import (
    calculate_natr,
    generate_theoretical_grid,
    suggest_grid_params,
)
from .controllers.pmm_mister import FIELD_ORDER as PMM_FIELD_ORDER
from .controllers.pmm_mister import FIELDS as PMM_FIELDS

logger = logging.getLogger(__name__)


# ============================================
# CONTROLLER CONFIGS MENU
# ============================================

# Pagination settings for configs
CONFIGS_PER_PAGE = 8  # Reduced to leave space for action buttons


def _get_controller_type_display(controller_name: str) -> tuple[str, str]:
    """Get display name and emoji for controller type"""
    type_map = {
        "grid_strike": ("Grid Strike", "📊"),
        "pmm_mister": ("PMM Mister", "📈"),
        "pmm_v1": ("PMM V1", "📉"),
        "multi_grid_strike": ("Multi Grid Strike", "🔲"),
        "dman_v3": ("DMan V3", "🤖"),
        "macd_bb_v1": ("MACD BB V1", "📉"),
        "arbitrage_controller": ("Arbitrage", "⚡"),
        "xemm_multiple_levels": ("XEMM Multi Levels", "🔄"),
        "anti_folla_v1": ("Anti-Folla V1", "🦅"),
    }
    controller_lower = controller_name.lower() if controller_name else ""
    for key, (name, emoji) in type_map.items():
        if key == controller_lower:
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


def _get_config_seq_num(cfg: dict) -> int:
    """Extract sequence number from config ID for sorting"""
    config_id = cfg.get("id", "")
    parts = config_id.split("_", 1)
    if parts and parts[0].isdigit():
        return int(parts[0])
    return -1  # No number goes to end


def _get_available_controller_types(configs: list) -> dict[str, int]:
    """Get available controller types with counts"""
    type_counts: dict[str, int] = {}
    for cfg in configs:
        ctrl_type = cfg.get("controller_name", "unknown")
        type_counts[ctrl_type] = type_counts.get(ctrl_type, 0) + 1
    return type_counts


def _get_selected_config_ids(context, type_configs: list) -> list[str]:
    """Get list of selected config IDs from selection state"""
    selected = context.user_data.get("selected_configs", {})  # {config_id: True}
    result = []
    for cfg in type_configs:
        cfg_id = cfg.get("id", "")
        if cfg_id and selected.get(cfg_id):
            result.append(cfg_id)
    return result


async def show_controller_configs_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0
) -> None:
    """
    Unified configs menu - shows configs directly with type selector, multi-select,
    and actions (Deploy, Edit, Delete).

    Selection persists across type/page changes using config IDs (not indices).
    """
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()

        # Store all configs
        context.user_data["controller_configs_list"] = configs

        # Get available types from registry (always shows all supported types)
        all_types = get_supported_controller_types()
        type_counts = _get_available_controller_types(configs)

        # Determine current type (default to first registered or grid_strike)
        current_type = context.user_data.get("configs_controller_type")
        if not current_type or current_type not in all_types:
            current_type = all_types[0] if all_types else "grid_strike"
        context.user_data["configs_controller_type"] = current_type

        # Filter and sort configs by current type
        type_configs = [c for c in configs if c.get("controller_name") == current_type]
        type_configs.sort(key=_get_config_seq_num, reverse=True)
        context.user_data["configs_type_filtered"] = type_configs
        context.user_data["configs_page"] = page

        # Get selection state (uses config IDs for persistence)
        # Sync with available configs - remove any IDs that no longer exist
        selected = context.user_data.get("selected_configs", {})  # {config_id: True}
        available_ids = {c.get("id") for c in configs if c.get("id")}
        selected = {
            cfg_id: is_sel
            for cfg_id, is_sel in selected.items()
            if cfg_id in available_ids
        }
        context.user_data["selected_configs"] = selected
        selected_ids = [cfg_id for cfg_id, is_sel in selected.items() if is_sel]

        # Calculate pagination
        total_pages = max(
            1, (len(type_configs) + CONFIGS_PER_PAGE - 1) // CONFIGS_PER_PAGE
        )
        start_idx = page * CONFIGS_PER_PAGE
        end_idx = min(start_idx + CONFIGS_PER_PAGE, len(type_configs))
        page_configs = type_configs[start_idx:end_idx]

        # Build message
        type_name, emoji = _get_controller_type_display(current_type)
        lines = [r"*Controller Configs*", ""]

        # Add separator to maintain consistent width
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        # Show selected summary (always visible)
        if selected_ids:
            lines.append(f"✅ *Selected \\({len(selected_ids)}\\):*")
            for cfg_id in selected_ids[:5]:  # Show max 5
                lines.append(f"  • `{escape_markdown_v2(cfg_id)}`")
            if len(selected_ids) > 5:
                lines.append(f"  _\\.\\.\\.and {len(selected_ids) - 5} more_")
            lines.append("")

        # Current type info
        if type_configs:
            if total_pages > 1:
                lines.append(
                    f"_{len(type_configs)} {escape_markdown_v2(type_name)} configs \\(page {page + 1}/{total_pages}\\)_"
                )
            else:
                lines.append(
                    f"_{len(type_configs)} {escape_markdown_v2(type_name)} config{'s' if len(type_configs) != 1 else ''}_"
                )
        else:
            lines.append(f"_No {escape_markdown_v2(type_name)} configs yet_")

        # Build keyboard
        keyboard = []

        # Row 1: Type selector + Create buttons
        type_row = []
        # Type selector button (shows current type, click to change)
        if len(all_types) > 1:
            type_row.append(
                InlineKeyboardButton(
                    f"{emoji} {type_name} ▼", callback_data="bots:cfg_select_type"
                )
            )
        else:
            type_row.append(
                InlineKeyboardButton(f"{emoji} {type_name}", callback_data="bots:noop")
            )

        # Create button - dynamically routes to the current type's wizard
        type_row.append(
            InlineKeyboardButton(
                "➕ New", callback_data=f"bots:new_{current_type}"
            )
        )

        keyboard.append(type_row)

        # Config checkboxes - show just the controller name/ID
        for i, cfg in enumerate(page_configs):
            config_id = cfg.get("id", f"config_{start_idx + i}")
            is_selected = selected.get(config_id, False)
            checkbox = "✅" if is_selected else "⬜"

            # Show just the config ID (truncated if needed)
            display = f"{checkbox} {config_id[:28]}"

            keyboard.append(
                [
                    InlineKeyboardButton(
                        display, callback_data=f"bots:cfg_toggle:{config_id}"
                    )
                ]
            )

        # Pagination row
        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(
                    InlineKeyboardButton(
                        "◀️", callback_data=f"bots:cfg_page:{page - 1}"
                    )
                )
            nav.append(
                InlineKeyboardButton(
                    f"📄 {page + 1}/{total_pages}", callback_data="bots:noop"
                )
            )
            if page < total_pages - 1:
                nav.append(
                    InlineKeyboardButton(
                        "▶️", callback_data=f"bots:cfg_page:{page + 1}"
                    )
                )
            keyboard.append(nav)

        # Action buttons (only if something selected)
        if selected_ids:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"🚀 Deploy ({len(selected_ids)})",
                        callback_data="bots:cfg_deploy",
                    ),
                    InlineKeyboardButton(
                        f"✏️ Edit ({len(selected_ids)})",
                        callback_data="bots:cfg_edit_loop",
                    ),
                ]
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"🗑️ Delete ({len(selected_ids)})",
                        callback_data="bots:cfg_delete_confirm",
                    ),
                    InlineKeyboardButton(
                        "⬜ Clear", callback_data="bots:cfg_clear_selection"
                    ),
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton("📤 Upload", callback_data="bots:upload_config"),
                InlineKeyboardButton("⬅️ Back", callback_data="bots:main_menu"),
            ]
        )

        reply_markup = InlineKeyboardMarkup(keyboard)
        text_content = "\n".join(lines)

        # Handle command vs callback
        if query and query.message:
            # Called from callback - edit the message
            if getattr(query.message, "photo", None):
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await query.message.chat.send_message(
                    text_content, parse_mode="MarkdownV2", reply_markup=reply_markup
                )
            else:
                try:
                    await query.message.edit_text(
                        text_content, parse_mode="MarkdownV2", reply_markup=reply_markup
                    )
                except BadRequest as e:
                    if "Message is not modified" not in str(e):
                        raise
        else:
            # Called from command - send new message
            msg = update.message
            if msg:
                await msg.reply_text(
                    text_content, parse_mode="MarkdownV2", reply_markup=reply_markup
                )

    except Exception as e:
        logger.error(f"Error loading controller configs: {e}", exc_info=True)
        keyboard = []
        for ctrl_type in get_supported_controller_types():
            type_name, emoji = _get_controller_type_display(ctrl_type)
            keyboard.append([
                InlineKeyboardButton(
                    f"➕ {type_name}", callback_data=f"bots:new_{ctrl_type}"
                )
            ])
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="bots:main_menu")])
        error_msg = format_error_message(f"Failed to load configs: {str(e)}")
        try:
            if query and query.message:
                if getattr(query.message, "photo", None):
                    try:
                        await query.message.delete()
                    except Exception:
                        pass
                    await query.message.chat.send_message(
                        error_msg,
                        parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                else:
                    await query.message.edit_text(
                        error_msg,
                        parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
            elif update.message:
                await update.message.reply_text(
                    error_msg,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
        except Exception:
            pass


async def show_type_selector(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show type selector popup to switch between controller types"""
    query = update.callback_query
    configs = context.user_data.get("controller_configs_list", [])

    type_counts = _get_available_controller_types(configs)
    current_type = context.user_data.get("configs_controller_type", "grid_strike")

    lines = [r"*Select Controller Type*", ""]

    # Show all registered types from registry (not just existing configs)
    keyboard = []
    for ctrl_type, info in get_controller_info().items():
        type_name, emoji = _get_controller_type_display(ctrl_type)
        count = type_counts.get(ctrl_type, 0)
        is_current = "• " if ctrl_type == current_type else ""
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"{is_current}{emoji} {type_name} ({count})",
                    callback_data=f"bots:cfg_type:{ctrl_type}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton("❌ Cancel", callback_data="bots:controller_configs"),
        ]
    )

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_configs_by_type(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    controller_type: str,
    page: int = 0,
) -> None:
    """Switch to a specific controller type and show configs"""
    context.user_data["configs_controller_type"] = controller_type
    context.user_data["configs_page"] = page
    await show_controller_configs_menu(update, context, page)


async def handle_cfg_toggle(
    update: Update, context: ContextTypes.DEFAULT_TYPE, config_id: str
) -> None:
    """Toggle config selection by config ID"""
    selected = context.user_data.get("selected_configs", {})

    if selected.get(config_id):
        selected.pop(config_id, None)
    else:
        selected[config_id] = True

    context.user_data["selected_configs"] = selected

    page = context.user_data.get("configs_page", 0)
    await show_controller_configs_menu(update, context, page)


async def handle_cfg_page(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int
) -> None:
    """Handle pagination for configs"""
    await show_controller_configs_menu(update, context, page)


async def handle_cfg_clear_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Clear all selected configs"""
    context.user_data["selected_configs"] = {}
    page = context.user_data.get("configs_page", 0)
    await show_controller_configs_menu(update, context, page)


async def handle_cfg_delete_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show delete confirmation dialog"""
    query = update.callback_query
    selected = context.user_data.get("selected_configs", {})
    selected_ids = [cfg_id for cfg_id, is_sel in selected.items() if is_sel]

    if not selected_ids:
        await query.answer("No configs selected", show_alert=True)
        return

    # Build confirmation message
    lines = [r"*Delete Configs\?*", ""]
    lines.append(
        f"You are about to delete {len(selected_ids)} config{'s' if len(selected_ids) != 1 else ''}:"
    )
    lines.append("")

    for cfg_id in selected_ids:
        lines.append(f"• `{escape_markdown_v2(cfg_id)}`")

    lines.append("")
    lines.append(r"⚠️ _This action cannot be undone\._")

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Yes, Delete", callback_data="bots:cfg_delete_execute"
            ),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:controller_configs"),
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    text_content = "\n".join(lines)

    await query.message.edit_text(
        text_content, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_cfg_delete_execute(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Execute deletion of selected configs"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    selected = context.user_data.get("selected_configs", {})
    selected_ids = [cfg_id for cfg_id, is_sel in selected.items() if is_sel]

    if not selected_ids:
        await query.answer("No configs selected", show_alert=True)
        return

    # Show progress
    await query.message.edit_text(
        f"🗑️ Deleting {len(selected_ids)} config{'s' if len(selected_ids) != 1 else ''}\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    # Delete each config
    client, _ = await get_bots_client(chat_id, context.user_data)
    deleted = []
    failed = []

    for config_id in selected_ids:
        try:
            await client.controllers.delete_controller_config(config_id)
            deleted.append(config_id)
        except Exception as e:
            logger.error(f"Failed to delete config {config_id}: {e}")
            failed.append((config_id, str(e)))

    # Clear selection
    context.user_data["selected_configs"] = {}

    # Build result message
    lines = []
    if deleted:
        lines.append(
            f"✅ *Deleted {len(deleted)} config{'s' if len(deleted) != 1 else ''}*"
        )
        for cfg_id in deleted:
            lines.append(f"  • `{escape_markdown_v2(cfg_id)}`")

    if failed:
        lines.append("")
        lines.append(f"❌ *Failed to delete {len(failed)}:*")
        for cfg_id, error in failed:
            lines.append(f"  • `{escape_markdown_v2(cfg_id)}`")
            lines.append(f"    _{escape_markdown_v2(error[:40])}_")

    keyboard = [
        [InlineKeyboardButton("⬅️ Back", callback_data="bots:controller_configs")]
    ]

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_cfg_deploy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deploy selected configs - bridges to existing deploy flow"""
    selected = context.user_data.get("selected_configs", {})
    selected_ids = [cfg_id for cfg_id, is_sel in selected.items() if is_sel]
    all_configs = context.user_data.get("controller_configs_list", [])

    if not selected_ids:
        query = update.callback_query
        await query.answer("No configs selected", show_alert=True)
        return

    # Map config IDs to all_configs indices for existing deploy flow
    deploy_indices = set()
    for cfg_id in selected_ids:
        for all_idx, all_cfg in enumerate(all_configs):
            if all_cfg.get("id") == cfg_id:
                deploy_indices.add(all_idx)
                break

    # Set up for existing deploy flow
    context.user_data["selected_controllers"] = deploy_indices

    # Don't clear selection - keep it for when user comes back

    # Use existing deploy configure flow
    await show_deploy_configure(update, context)


# ============================================
# EDIT LOOP - Edit multiple configs in sequence
# ============================================


async def handle_cfg_edit_loop(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Start editing selected configs in a loop"""
    query = update.callback_query
    selected = context.user_data.get("selected_configs", {})
    selected_ids = [cfg_id for cfg_id, is_sel in selected.items() if is_sel]
    all_configs = context.user_data.get("controller_configs_list", [])

    if not selected_ids:
        await query.answer("No configs selected", show_alert=True)
        return

    # Build list of configs to edit
    configs_to_edit = []
    for cfg_id in selected_ids:
        for cfg in all_configs:
            if cfg.get("id") == cfg_id:
                configs_to_edit.append(cfg.copy())
                break

    if not configs_to_edit:
        await query.answer("Configs not found", show_alert=True)
        return

    # Store edit loop state
    context.user_data["cfg_edit_loop"] = configs_to_edit
    context.user_data["cfg_edit_index"] = 0
    context.user_data["cfg_edit_modified"] = {}  # {config_id: modified_config}

    await show_cfg_edit_form(update, context)

def _get_editable_config_fields(config: dict) -> dict:
    """Extract editable fields from a controller config using centralized field definitions"""
    controller_type = config.get("controller_name", "grid_strike")
    tp_cfg = config.get("triple_barrier_config", {})
    take_profit = (
        tp_cfg.get("take_profit", 0.0001) if isinstance(tp_cfg, dict) else 0.0001
    )

    if "grid_strike" in controller_type:
        result = {}
        for field_name in GS_EDITABLE_FIELDS:
            if field_name == "take_profit":
                result[field_name] = take_profit
            else:
                default_val = GRID_STRIKE_DEFAULTS.get(field_name, "")
                result[field_name] = config.get(field_name, default_val)
        return result

    elif "pmm" in controller_type:
        from .controllers.pmm_mister import DEFAULTS as PMM_DEFAULTS
        result = {}
        for field_name in PMM_FIELD_ORDER:
            if field_name == "id":
                continue
            if field_name in PMM_FIELDS:
                default_val = PMM_DEFAULTS.get(field_name, "")
                result[field_name] = config.get(field_name, default_val)
        return result

    elif controller_type == "multi_grid_strike":
        tp_cfg = config.get("triple_barrier_config", {})
        take_profit = tp_cfg.get("take_profit", 0.001)

        grids = config.get("grids", [])

        # ========== DEBUG ==========
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"MGS EDIT DEBUG: config_id={config.get('id')}, grids count={len(grids)}")
        if grids:
            logger.info(f"MGS EDIT DEBUG: first grid = {grids[0]}")
        # ===========================

        # Prepara i campi per TUTTI i grid (fino a un massimo di 5)
        grid_fields = {}
        for i, grid in enumerate(grids[:5]):
            prefix = f"grid{i+1}_"
            grid_fields[f"{prefix}id"] = grid.get("grid_id", f"grid_{i+1}")
            grid_fields[f"{prefix}start"] = grid.get("start_price", 0.0)
            grid_fields[f"{prefix}end"] = grid.get("end_price", 0.0)
            grid_fields[f"{prefix}limit"] = grid.get("limit_price", 0.0)
            grid_fields[f"{prefix}side"] = "LONG" if grid.get("side") == 1 else "SHORT"
            grid_fields[f"{prefix}pct"] = grid.get("amount_quote_pct", 1.0)
            grid_fields[f"{prefix}enabled"] = grid.get("enabled", True)

        return {
            "connector_name": config.get("connector_name", ""),
            "trading_pair": config.get("trading_pair", ""),
            "total_amount_quote": config.get("total_amount_quote", 1000),
            "leverage": config.get("leverage", 1),
            "position_mode": config.get("position_mode", "HEDGE"),
            "take_profit": take_profit,
            "min_spread_between_orders": config.get("min_spread_between_orders", 0.001),
            "min_order_amount_quote": config.get("min_order_amount_quote", 5),
            "max_open_orders": config.get("max_open_orders", 2),
            "max_orders_per_batch": config.get("max_orders_per_batch", 1),
            "order_frequency": config.get("order_frequency", 3),
            "keep_position": config.get("keep_position", False),
            "activation_bounds": config.get("activation_bounds", None),
            "manual_kill_switch": config.get("manual_kill_switch", False),
            **grid_fields,
        }

    elif controller_type == "dman_v3":
        ts = config.get("trailing_stop", {}) or {}
        if isinstance(ts, str):
            parts = ts.split(",")
            ts_activation = float(parts[0]) if len(parts) > 0 else 0.015
            ts_delta = float(parts[1]) if len(parts) > 1 else 0.005
        else:
            ts_activation = ts.get("activation_price", 0.015)
            ts_delta = ts.get("trailing_delta", 0.005)
        return {
            "connector_name": config.get("connector_name", ""),
            "trading_pair": config.get("trading_pair", ""),
            "total_amount_quote": config.get("total_amount_quote", 1000),
            "leverage": config.get("leverage", 1),
            "position_mode": config.get("position_mode", "HEDGE"),
            "max_executors_per_side": config.get("max_executors_per_side", 1),
            "cooldown_time": config.get("cooldown_time", 60),
            "stop_loss": config.get("stop_loss", 0.05),
            "take_profit": config.get("take_profit", 0.03),
            "take_profit_order_type": config.get("take_profit_order_type", 2),
            "time_limit": config.get("time_limit", None),
            "trailing_stop_activation": ts_activation,
            "trailing_stop_delta": ts_delta,
            "candles_connector": config.get("candles_connector", ""),
            "candles_trading_pair": config.get("candles_trading_pair", ""),
            "interval": config.get("interval", "3m"),
            "bb_length": config.get("bb_length", 100),
            "bb_std": config.get("bb_std", 2.0),
            "bb_long_threshold": config.get("bb_long_threshold", 0.0),
            "bb_short_threshold": config.get("bb_short_threshold", 1.0),
            "dca_spreads": config.get("dca_spreads", "0.001,0.018,0.15,0.25"),
            "dca_amounts_pct": config.get("dca_amounts_pct", ""),
            "dynamic_order_spread": config.get("dynamic_order_spread", False),
            "dynamic_target": config.get("dynamic_target", False),
            "activation_bounds": config.get("activation_bounds", None),
        }

    elif controller_type == "arbitrage_controller":
        from .controllers.arbitrage_controller.config import get_flat_fields
        return get_flat_fields(config)

    elif controller_type == "xemm_multiple_levels":
            return {
                "maker_connector": config.get("maker_connector", ""),
                "maker_trading_pair": config.get("maker_trading_pair", ""),
                "taker_connector": config.get("taker_connector", ""),
                "taker_trading_pair": config.get("taker_trading_pair", ""),
                "total_amount_quote": config.get("total_amount_quote", 0),
                "min_profitability": config.get("min_profitability", 0.003),
                "max_profitability": config.get("max_profitability", 0.009),
                "max_executors_imbalance": config.get("max_executors_imbalance", 1),
                "buy_levels_targets_amount": config.get("buy_levels_targets_amount", ""),
                "sell_levels_targets_amount": config.get("sell_levels_targets_amount", ""),
            }
    elif controller_type == "macd_bb_v1":
        ts = config.get("trailing_stop", {}) or {}
        ts_activation = ts.get("activation_price", 0.015) if isinstance(ts, dict) else 0.015
        ts_delta = ts.get("trailing_delta", 0.005) if isinstance(ts, dict) else 0.005
        return {
            "connector_name": config.get("connector_name", ""),
            "trading_pair": config.get("trading_pair", ""),
            "total_amount_quote": config.get("total_amount_quote", 1000),
            "leverage": config.get("leverage", 1),
            "position_mode": config.get("position_mode", "HEDGE"),
            "max_executors_per_side": config.get("max_executors_per_side", 1),
            "cooldown_time": config.get("cooldown_time", 60),
            "stop_loss": config.get("stop_loss", 0.05),
            "take_profit": config.get("take_profit", 0.03),
            "take_profit_order_type": config.get("take_profit_order_type", 2),
            "trailing_stop_activation": ts_activation,
            "trailing_stop_delta": ts_delta,
            "candles_connector": config.get("candles_connector", ""),
            "candles_trading_pair": config.get("candles_trading_pair", ""),
            "interval": config.get("interval", "3m"),
            "bb_length": config.get("bb_length", 100),
            "bb_std": config.get("bb_std", 2.0),
            "bb_long_threshold": config.get("bb_long_threshold", 0.0),
            "bb_short_threshold": config.get("bb_short_threshold", 1.0),
            "macd_fast": config.get("macd_fast", 21),
            "macd_slow": config.get("macd_slow", 42),
            "macd_signal": config.get("macd_signal", 9),
        }

    elif controller_type == "supertrend_v1":
        ts = config.get("trailing_stop", {}) or {}
        ts_activation = ts.get("activation_price", 0.015) if isinstance(ts, dict) else 0.015
        ts_delta = ts.get("trailing_delta", 0.005) if isinstance(ts, dict) else 0.005
        return {
            "connector_name": config.get("connector_name", ""),
            "trading_pair": config.get("trading_pair", ""),
            "total_amount_quote": config.get("total_amount_quote", 1000),
            "leverage": config.get("leverage", 1),
            "position_mode": config.get("position_mode", "HEDGE"),
            "max_executors_per_side": config.get("max_executors_per_side", 1),
            "cooldown_time": config.get("cooldown_time", 60),
            "stop_loss": config.get("stop_loss", 0.05),
            "take_profit": config.get("take_profit", 0.03),
            "take_profit_order_type": config.get("take_profit_order_type", 2),
            "trailing_stop_activation": ts_activation,
            "trailing_stop_delta": ts_delta,
            "interval": config.get("interval", "3m"),
            "length": config.get("length", 20),
            "multiplier": config.get("multiplier", 4.0),
            "percentage_threshold": config.get("percentage_threshold", 0.01),
        }

    elif controller_type == "anti_folla_v1":
        ts = config.get("trailing_stop", {}) or {}
        ts_activation = ts.get("activation_price", 0.015) if isinstance(ts, dict) else 0.015
        ts_delta = ts.get("trailing_delta", 0.005) if isinstance(ts, dict) else 0.005
        return {
            "connector_name": config.get("connector_name", ""),
            "trading_pair": config.get("trading_pair", ""),
            "total_amount_quote": config.get("total_amount_quote", 1000),
            "leverage": config.get("leverage", 1),
            "position_mode": config.get("position_mode", "HEDGE"),
            "max_executors_per_side": config.get("max_executors_per_side", 1),
            "cooldown_time": config.get("cooldown_time", 60),
            "stop_loss": config.get("stop_loss", 0.05),
            "take_profit": config.get("take_profit", 0.03),
            "take_profit_order_type": config.get("take_profit_order_type", 2),
            "trailing_stop_activation": ts_activation,
            "trailing_stop_delta": ts_delta,
            "candles_connector": config.get("candles_connector", ""),
            "candles_trading_pair": config.get("candles_trading_pair", ""),
            "interval": config.get("interval", "3m"),
            "is_perpetual": config.get("is_perpetual", False),
            "vwap_period": config.get("vwap_period", 20),
            "donchian_period": config.get("donchian_period", 20),
            "atr_period": config.get("atr_period", 14),
            "obv_divergence_lookback": config.get("obv_divergence_lookback", 10),
            "volume_spike_threshold": config.get("volume_spike_threshold", 2.5),
            "enable_order_book_imbalance": config.get("enable_order_book_imbalance", True),
            "obi_depth_percentage": config.get("obi_depth_percentage", 0.02),
            "obi_buy_threshold": config.get("obi_buy_threshold", 1.5),
            "obi_sell_threshold": config.get("obi_sell_threshold", 0.67),
            "score_buy_threshold": config.get("score_buy_threshold", 50.0),
            "score_sell_threshold": config.get("score_sell_threshold", -50.0),
            "weight_vwap": config.get("weight_vwap", 15),
            "weight_donchian": config.get("weight_donchian", 10),
            "weight_obv": config.get("weight_obv", 15),
            "weight_obi": config.get("weight_obi", 20),
            "weight_volume_spike": config.get("weight_volume_spike", 10),
            "weight_trade_flow": config.get("weight_trade_flow", 15),
            "weight_funding": config.get("weight_funding", 15),
        }




    # Default fallback per controller types non riconosciuti
    return {
        "total_amount_quote": config.get("total_amount_quote", 0),
        "take_profit": take_profit,
    }


async def show_cfg_edit_form(
    update: Update, context: ContextTypes.DEFAULT_TYPE, status_msg: str = None
) -> None:
    """Show edit form for current config in bulk edit format (key=value)"""
    query = update.callback_query

    configs_to_edit = context.user_data.get("cfg_edit_loop", [])
    current_idx = context.user_data.get("cfg_edit_index", 0)
    modified = context.user_data.get("cfg_edit_modified", {})

    if not configs_to_edit or current_idx >= len(configs_to_edit):
        await show_controller_configs_menu(update, context)
        return

    total = len(configs_to_edit)
    config = configs_to_edit[current_idx]
    config_id = config.get("id", "unknown")

    # Check if we have modifications for this config
    if config_id in modified:
        config = modified[config_id]

    # Store current config for editing
    set_controller_config(context, config)

    # Get editable fields
    editable_fields = _get_editable_config_fields(config)

    # Store editable fields and set state for bulk edit
    context.user_data["cfg_editable_fields"] = editable_fields
    context.user_data["bots_state"] = "cfg_bulk_edit"
    context.user_data["cfg_edit_message_id"] = (
        query.message.message_id if not query.message.photo else None
    )
    context.user_data["cfg_edit_chat_id"] = query.message.chat_id

    # Build message with key=value format
    header = f"*Edit Config* \\({current_idx + 1}/{total}\\)"
    if status_msg:
        header += f" — {escape_markdown_v2(status_msg)}"
    lines = [header, ""]
    lines.append(f"`{escape_markdown_v2(config_id)}`")
    lines.append("")

    # Add context info for Grid Strike (connector, trading pair, side)
    controller_type = config.get("controller_name", "")
    if "grid_strike" in controller_type:
        connector = config.get("connector_name", "")
        pair = config.get("trading_pair", "")
        side = config.get("side", SIDE_LONG)
        side_str = "LONG" if side == SIDE_LONG else "SHORT"
        lines.append(
            f"*{escape_markdown_v2(pair)}* {side_str} on {escape_markdown_v2(connector)}"
        )
        lines.append("")

    # Build config text for display (each line copyable)
    for key, value in editable_fields.items():
        lines.append(f"`{key}={value}`")
    lines.append("")
    lines.append("✏️ _Send `key=value` to update_")

    # Build keyboard - simplified, no field buttons
    keyboard = []

    # Navigation row
    nav_row = []
    if current_idx > 0:
        nav_row.append(
            InlineKeyboardButton("◀️ Prev", callback_data="bots:cfg_edit_prev")
        )
    nav_row.append(InlineKeyboardButton(f"💾 Save", callback_data="bots:cfg_edit_save"))
    if current_idx < total - 1:
        nav_row.append(
            InlineKeyboardButton("Next ▶️", callback_data="bots:cfg_edit_next")
        )
    keyboard.append(nav_row)

    # Branch button row
    keyboard.append(
        [
            InlineKeyboardButton("🔀 Branch", callback_data="bots:cfg_branch"),
        ]
    )

    # Final row
    keyboard.append(
        [
            InlineKeyboardButton(
                "💾 Save All & Exit", callback_data="bots:cfg_edit_save_all"
            ),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:cfg_edit_cancel"),
        ]
    )

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        "\n".join(lines), parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_cfg_edit_field(
    update: Update, context: ContextTypes.DEFAULT_TYPE, field_name: str
) -> None:
    """Prompt to edit a field in the current config"""
    query = update.callback_query
    config = get_controller_config(context)

    if not config:
        await query.answer("Config not found", show_alert=True)
        return

    # Get current value
    if field_name == "take_profit":
        current_value = config.get("triple_barrier_config", {}).get(
            "take_profit", 0.0001
        )
    elif field_name == "side":
        # Toggle side directly
        current_side = config.get("side", 1)
        new_side = 2 if current_side == 1 else 1
        config["side"] = new_side

        # Store modified config
        config_id = config.get("id")
        modified = context.user_data.get("cfg_edit_modified", {})
        modified[config_id] = config
        context.user_data["cfg_edit_modified"] = modified

        # Update in edit loop
        configs_to_edit = context.user_data.get("cfg_edit_loop", [])
        current_idx = context.user_data.get("cfg_edit_index", 0)
        if current_idx < len(configs_to_edit):
            configs_to_edit[current_idx] = config

        await show_cfg_edit_form(update, context)
        return
    else:
        current_value = config.get(field_name, "")

    # Get field info
    field_labels = {
        "leverage": ("Leverage", "Enter leverage (1-20)"),
        "total_amount_quote": ("Amount (USDT)", "Enter total amount in quote currency"),
        "start_price": ("Start Price", "Enter start price"),
        "end_price": ("End Price", "Enter end price"),
        "limit_price": ("Limit Price", "Enter limit/stop price"),
        "take_profit": ("Take Profit", "Enter take profit (e.g., 0.01 = 1%)"),
        "max_open_orders": ("Max Open Orders", "Enter max open orders (1-10)"),
    }

    label, hint = field_labels.get(field_name, (field_name, "Enter value"))

    # Store state for input processing
    context.user_data["bots_state"] = f"cfg_edit_input:{field_name}"
    context.user_data["cfg_edit_field"] = field_name

    lines = [
        f"*Edit {escape_markdown_v2(label)}*",
        "",
        f"Current: `{escape_markdown_v2(str(current_value))}`",
        "",
        f"_{escape_markdown_v2(hint)}_",
    ]

    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="bots:cfg_edit_form")]]

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def process_cfg_edit_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str
) -> None:
    """Process user input for config bulk edit - parses key=value lines"""
    chat_id = update.effective_chat.id
    config = get_controller_config(context)
    editable_fields = context.user_data.get("cfg_editable_fields", {})

    if not config:
        await update.message.reply_text("Context lost. Please start over.")
        return

    # Delete user's input message for clean chat
    try:
        await update.message.delete()
    except Exception:
        pass

    # Parse key=value lines
    updates = {}
    errors = []

    for line in user_input.split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # Validate key exists in editable fields
        if key not in editable_fields:
            errors.append(f"Unknown: {key}")
            continue

        # Convert value to appropriate type
        current_val = editable_fields.get(key)
        try:
            if isinstance(current_val, bool):
                parsed_value = value.lower() in ["true", "1", "yes", "y", "on"]
            elif isinstance(current_val, int):
                parsed_value = int(value)
            elif isinstance(current_val, float):
                parsed_value = float(value)
            else:
                parsed_value = value
            updates[key] = parsed_value
        except ValueError:
            errors.append(f"Invalid: {key}={value}")

    if errors:
        error_msg = "⚠️ " + ", ".join(errors)
        await update.get_bot().send_message(chat_id=chat_id, text=error_msg)

    if not updates:
        await update.get_bot().send_message(
            chat_id=chat_id, text="❌ No valid updates found. Use format: key=value"
        )
        return

    # Apply updates to config
    old_config_id = config.get("id", "")
    for key, value in updates.items():
        if key == "take_profit":
            if "triple_barrier_config" not in config:
                config["triple_barrier_config"] = {}
            config["triple_barrier_config"]["take_profit"] = value
        elif key in ("stop_loss", "time_limit", "trailing_stop") and config.get("controller_name") == "multi_grid_strike":
            if "triple_barrier_config" not in config:
                config["triple_barrier_config"] = {}
            config["triple_barrier_config"][key] = value if value not in ("null", "None", "") else None
        elif key == "trailing_stop_activation" and config.get("controller_name") == "dman_v3":
            if not isinstance(config.get("trailing_stop"), dict):
                config["trailing_stop"] = {"activation_price": 0.015, "trailing_delta": 0.005}
            config["trailing_stop"]["activation_price"] = value
        elif key == "trailing_stop_delta" and config.get("controller_name") == "dman_v3":
            if not isinstance(config.get("trailing_stop"), dict):
                config["trailing_stop"] = {"activation_price": 0.015, "trailing_delta": 0.005}
            config["trailing_stop"]["trailing_delta"] = value

        elif key.startswith("exchange_pair_"):
            from .controllers.arbitrage_controller.config import apply_flat_fields
            apply_flat_fields(config, {key: value})

        elif key.startswith("exchange_pair_"):
            from .controllers.arbitrage_controller.config import apply_flat_fields
            apply_flat_fields(config, {key: value})

        elif key.startswith("grid") and "_" in key and not key.startswith("grid_analysis"):
            # Supporta grid1_, grid2_, grid3_, ... per Multi Grid Strike
            # Formato: gridX_field (es. grid1_start, grid2_side, grid3_enabled)
            parts = key.split("_", 1)
            if len(parts) == 2:
                grid_num_str = parts[0].replace("grid", "")
                field_name = parts[1]
                try:
                    grid_idx = int(grid_num_str) - 1  # 0-based index
                    grids = config.get("grids", [])

                    # Assicurati che la lista grids abbia abbastanza elementi
                    while len(grids) <= grid_idx:
                        grids.append({
                            "grid_id": f"grid_{len(grids)+1}",
                            "side": 1,
                            "amount_quote_pct": 1.0,
                            "enabled": True,
                            "start_price": 0.0,
                            "end_price": 0.0,
                            "limit_price": 0.0
                        })

                    # Mappa i nomi dei campi
                    field_map = {
                        "id": "grid_id",
                        "start": "start_price",
                        "end": "end_price",
                        "limit": "limit_price",
                        "side": "side",
                        "pct": "amount_quote_pct",
                        "enabled": "enabled",
                    }
                    mapped_field = field_map.get(field_name, field_name)

                    # Converti il valore in base al tipo di campo
                    if mapped_field == "side":
                        if isinstance(value, str):
                            if value.upper() in ("LONG", "1"):
                                val = 1
                            elif value.upper() in ("SHORT", "2"):
                                val = 2
                            else:
                                val = int(value)
                        else:
                            val = int(value)
                    elif mapped_field == "enabled":
                        if isinstance(value, str):
                            val = value.lower() in ("true", "1", "yes", "on")
                        else:
                            val = bool(value)
                    elif mapped_field in ("start_price", "end_price", "limit_price", "amount_quote_pct"):
                        val = float(value)
                    else:
                        val = value

                    grids[grid_idx][mapped_field] = val
                    config["grids"] = grids

                except (ValueError, IndexError) as e:
                    errors.append(f"Grid error: {key}={value} ({e})")
        else:
            config[key] = value

    # Auto-update ID if connector_name or trading_pair changed
    if "connector_name" in updates or "trading_pair" in updates:
        # Extract sequence number from old ID
        parts = old_config_id.split("_", 1)
        seq_num = parts[0] if parts and parts[0].isdigit() else "001"

        # Determine controller type abbreviation
        controller_name = config.get("controller_name", "")
        if controller_name == "grid_strike":
            type_abbrev = "gs"
        elif controller_name == "pmm_mister":
            type_abbrev = "pmm"
        else:
            type_abbrev = (
                parts[1].split("_")[0] if len(parts) > 1 and "_" in parts[1] else "cfg"
            )

        # Build new ID with current values
        connector = config.get("connector_name", "unknown")
        conn_clean = connector.replace("_perpetual", "").replace("_spot", "")
        pair = config.get("trading_pair", "UNKNOWN").upper()
        new_config_id = f"{seq_num}_{type_abbrev}_{conn_clean}_{pair}"

        config["id"] = new_config_id
    else:
        new_config_id = old_config_id

    # Store modified config (remove old key if ID changed)
    modified = context.user_data.get("cfg_edit_modified", {})
    if old_config_id != new_config_id and old_config_id in modified:
        del modified[old_config_id]
    modified[new_config_id] = config
    context.user_data["cfg_edit_modified"] = modified

    # Update in edit loop
    configs_to_edit = context.user_data.get("cfg_edit_loop", [])
    current_idx = context.user_data.get("cfg_edit_index", 0)
    if current_idx < len(configs_to_edit):
        configs_to_edit[current_idx] = config

    # Update editable fields for display
    editable_fields = _get_editable_config_fields(config)
    context.user_data["cfg_editable_fields"] = editable_fields

    # Try to delete the user's input message
    try:
        await update.message.delete()
    except Exception:
        pass

    # Rebuild the edit form with updated values
    total = len(configs_to_edit)
    config_id = config.get("id", "unknown")

    lines = [f"*Edit Config* \\({current_idx + 1}/{total}\\)", ""]
    lines.append(f"`{escape_markdown_v2(config_id)}`")
    lines.append("")

    # Add context info for Grid Strike (connector, trading pair, side)
    controller_type = config.get("controller_name", "")
    if "grid_strike" in controller_type:
        connector = config.get("connector_name", "")
        pair = config.get("trading_pair", "")
        side = config.get("side", SIDE_LONG)
        side_str = "LONG" if side == SIDE_LONG else "SHORT"
        lines.append(
            f"*{escape_markdown_v2(pair)}* {side_str} on {escape_markdown_v2(connector)}"
        )
        lines.append("")

    for key, value in editable_fields.items():
        lines.append(f"`{key}={value}`")
    lines.append("")
    lines.append("✏️ _Send `key=value` to update_")

    # Build keyboard
    keyboard = []
    nav_row = []
    if current_idx > 0:
        nav_row.append(
            InlineKeyboardButton("◀️ Prev", callback_data="bots:cfg_edit_prev")
        )
    nav_row.append(InlineKeyboardButton(f"💾 Save", callback_data="bots:cfg_edit_save"))
    if current_idx < total - 1:
        nav_row.append(
            InlineKeyboardButton("Next ▶️", callback_data="bots:cfg_edit_next")
        )
    keyboard.append(nav_row)
    keyboard.append(
        [InlineKeyboardButton("🔀 Branch", callback_data="bots:cfg_branch")]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                "💾 Save All & Exit", callback_data="bots:cfg_edit_save_all"
            ),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:cfg_edit_cancel"),
        ]
    )

    # Edit the original message
    message_id = context.user_data.get("cfg_edit_message_id")
    if message_id:
        try:
            await update.get_bot().edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="\n".join(lines),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception:
            # If edit fails, send a new message
            msg = await update.get_bot().send_message(
                chat_id=chat_id,
                text="\n".join(lines),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            context.user_data["cfg_edit_message_id"] = msg.message_id


async def handle_cfg_edit_prev(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Go to previous config in edit loop"""
    current_idx = context.user_data.get("cfg_edit_index", 0)
    if current_idx > 0:
        context.user_data["cfg_edit_index"] = current_idx - 1
    await show_cfg_edit_form(update, context)


async def handle_cfg_edit_next(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Go to next config in edit loop"""
    configs_to_edit = context.user_data.get("cfg_edit_loop", [])
    current_idx = context.user_data.get("cfg_edit_index", 0)
    if current_idx < len(configs_to_edit) - 1:
        context.user_data["cfg_edit_index"] = current_idx + 1
    await show_cfg_edit_form(update, context)


async def handle_cfg_edit_save(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Save current config and stay in edit loop"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    config = get_controller_config(context)
    if not config:
        await query.answer("Config not found", show_alert=True)
        return

    config_id = config.get("id")

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        await client.controllers.create_or_update_controller_config(config_id, config)
        await query.answer()

        # Remove from modified since it's now saved
        modified = context.user_data.get("cfg_edit_modified", {})
        modified.pop(config_id, None)
        context.user_data["cfg_edit_modified"] = modified

        # Refresh form with saved status
        await show_cfg_edit_form(update, context, status_msg="✅ Saved!")

    except Exception as e:
        logger.error(f"Failed to save config {config_id}: {e}")
        await query.answer(f"❌ Save failed: {str(e)[:30]}", show_alert=True)


async def handle_cfg_edit_save_all(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Save all modified configs and exit edit loop"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    modified = context.user_data.get("cfg_edit_modified", {})

    if not modified:
        await query.answer("No changes to save")
        # Clean up edit loop state
        context.user_data.pop("cfg_edit_loop", None)
        context.user_data.pop("cfg_edit_index", None)
        context.user_data.pop("cfg_edit_modified", None)
        await show_controller_configs_menu(update, context)
        return

    # Show progress
    await query.message.edit_text(
        f"💾 Saving {len(modified)} config{'s' if len(modified) != 1 else ''}\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    client, _ = await get_bots_client(chat_id, context.user_data)
    saved = []
    failed = []

    for config_id, config in modified.items():
        try:
            await client.controllers.create_or_update_controller_config(
                config_id, config
            )
            saved.append(config_id)
        except Exception as e:
            logger.error(f"Failed to save config {config_id}: {e}")
            failed.append((config_id, str(e)))

    # Clean up edit loop state
    context.user_data.pop("cfg_edit_loop", None)
    context.user_data.pop("cfg_edit_index", None)
    context.user_data.pop("cfg_edit_modified", None)

    # Build result message
    lines = []
    if saved:
        lines.append(f"✅ *Saved {len(saved)} config{'s' if len(saved) != 1 else ''}*")
        for cfg_id in saved[:5]:
            lines.append(f"  • `{escape_markdown_v2(cfg_id)}`")
        if len(saved) > 5:
            lines.append(f"  _\\.\\.\\.and {len(saved) - 5} more_")

    if failed:
        lines.append("")
        lines.append(f"❌ *Failed to save {len(failed)}:*")
        for cfg_id, error in failed[:3]:
            lines.append(f"  • `{escape_markdown_v2(cfg_id)}`")

    keyboard = [
        [InlineKeyboardButton("⬅️ Back", callback_data="bots:controller_configs")]
    ]

    await query.message.edit_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_cfg_edit_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Cancel edit loop without saving"""
    # Clean up edit loop state
    context.user_data.pop("cfg_edit_loop", None)
    context.user_data.pop("cfg_edit_index", None)
    context.user_data.pop("cfg_edit_modified", None)
    context.user_data.pop("bots_state", None)

    await show_controller_configs_menu(update, context)


async def handle_cfg_branch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Branch (duplicate) the current config with a new ID"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    configs_to_edit = context.user_data.get("cfg_edit_loop", [])
    current_idx = context.user_data.get("cfg_edit_index", 0)
    modified = context.user_data.get("cfg_edit_modified", {})

    if not configs_to_edit or current_idx >= len(configs_to_edit):
        await query.answer("No config to branch")
        return

    # Get current config (use modified version if exists)
    config = configs_to_edit[current_idx]
    config_id = config.get("id", "unknown")
    if config_id in modified:
        config = modified[config_id]

    # Generate new ID by incrementing the sequence number
    # Format: NNN_type_connector_pair -> increment NNN
    old_id = config.get("id", "")
    parts = old_id.split("_", 1)

    # Find highest sequence number across all configs from multiple sources
    client, _ = await get_bots_client(chat_id, context.user_data)

    # Source 1: Fresh list from API
    try:
        api_configs = await client.controllers.list_controller_configs()
    except Exception:
        api_configs = []

    # Source 2: Cached list in user_data (may have configs not yet saved)
    cached_configs = context.user_data.get("controller_configs_list", [])

    max_num = 0

    # Check all sources for highest sequence number
    all_config_sources = [api_configs, cached_configs, configs_to_edit]
    for config_list in all_config_sources:
        for cfg in config_list:
            cfg_id = cfg.get("id", "") if isinstance(cfg, dict) else ""
            cfg_parts = cfg_id.split("_", 1)
            if cfg_parts and cfg_parts[0].isdigit():
                max_num = max(max_num, int(cfg_parts[0]))

    # Also check modified config IDs (keys)
    for cfg_id in modified.keys():
        cfg_parts = cfg_id.split("_", 1)
        if cfg_parts and cfg_parts[0].isdigit():
            max_num = max(max_num, int(cfg_parts[0]))

    # Create new ID based on current config values
    new_num = str(max_num + 1).zfill(3)

    # Determine controller type abbreviation
    controller_name = config.get("controller_name", "")
    if controller_name == "grid_strike":
        type_abbrev = "gs"
    elif controller_name == "pmm_mister":
        type_abbrev = "pmm"
    else:
        # Fallback: try to extract from old ID
        if len(parts) > 1:
            type_abbrev = parts[1].split("_")[0] if "_" in parts[1] else parts[1]
        else:
            type_abbrev = "cfg"

    # Get connector and trading pair from current config values
    connector = config.get("connector_name", "unknown")
    conn_clean = connector.replace("_perpetual", "").replace("_spot", "")
    pair = config.get("trading_pair", "UNKNOWN").upper()

    new_id = f"{new_num}_{type_abbrev}_{conn_clean}_{pair}"

    # Deep copy the config with new ID
    new_config = copy.deepcopy(config)
    new_config["id"] = new_id

    # Add to edit loop right after current config
    configs_to_edit.insert(current_idx + 1, new_config)
    context.user_data["cfg_edit_loop"] = configs_to_edit

    # Mark as modified so it gets saved
    modified[new_id] = new_config
    context.user_data["cfg_edit_modified"] = modified

    # Navigate to the new config
    context.user_data["cfg_edit_index"] = current_idx + 1

    await query.answer(f"Branched to {new_id}")
    await show_cfg_edit_form(update, context)


async def handle_configs_page(
    update: Update, context: ContextTypes.DEFAULT_TYPE, page: int
) -> None:
    """Handle pagination for controller configs menu (legacy, redirects to cfg_page)"""
    controller_type = context.user_data.get("configs_controller_type")
    if controller_type:
        await show_configs_by_type(update, context, controller_type, page)
    else:
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


async def show_new_grid_strike_form(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Start the progressive Grid Strike wizard - Step 1: Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    # Clear any cached market data from previous wizard runs
    # This prevents showing stale data when starting a new grid for a different pair
    gs_keys_to_clear = [
        "gs_current_price",
        "gs_candles",
        "gs_candles_interval",
        "gs_chart_interval",
        "gs_natr",
        "gs_trading_rules",
        "gs_theoretical_grid",
        "gs_market_data_ready",
        "gs_market_data_error",
    ]
    for key in gs_keys_to_clear:
        context.user_data.pop(key, None)

    # Fetch existing configs for sequence numbering
    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
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


async def _show_wizard_connector_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Wizard Step 1: Select Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    try:
        client, server_name = await get_bots_client(chat_id, context.user_data)
        cex_connectors = await get_available_cex_connectors(
            context.user_data, client, server_name=server_name
        )

        if not cex_connectors:
            keyboard = [
                [
                    InlineKeyboardButton(
                        "🔑 Configure API Keys", callback_data="config_api_keys"
                    )
                ],
                [InlineKeyboardButton("« Back", callback_data="bots:main_menu")],
            ]
            await query.message.edit_text(
                r"*Grid Strike \- New Config*" + "\n\n"
                r"⚠️ No CEX connectors available\." + "\n\n"
                r"You need to connect API keys for an exchange to deploy strategies\."
                + "\n"
                r"Click below to configure your API keys\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        # Build connector buttons (2 per row)
        keyboard = []
        row = []
        for connector in cex_connectors:
            row.append(
                InlineKeyboardButton(
                    f"🏦 {connector}", callback_data=f"bots:gs_connector:{connector}"
                )
            )
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        keyboard.append(
            [InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")]
        )

        await query.message.edit_text(
            r"*📈 Grid Strike*" + "\n\n"
            r"A market making strategy that places a series of buy and sell "
            r"limit orders at predetermined price intervals above and below "
            r"a reference price, forming a \"grid\.\"" + "\n\n"
            r"[📖 Read the strategy guide](https://hummingbot.org/blog/strategy-guide-grid-strike/)"
            + "\n\n"
            r"─────────────────────────" + "\n\n"
            r"*Step 1: Select Exchange*" + "\n\n"
            r"Grid Strike works on both spot and perpetual exchanges\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True,
        )

    except Exception as e:
        logger.error(f"Error in connector step: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        await query.message.edit_text(
            format_error_message(f"Error: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_gs_wizard_connector(
    update: Update, context: ContextTypes.DEFAULT_TYPE, connector: str
) -> None:
    """Handle connector selection in wizard"""
    query = update.callback_query
    config = get_controller_config(context)

    config["connector_name"] = connector
    set_controller_config(context, config)

    # Move to trading pair step
    context.user_data["gs_wizard_step"] = "trading_pair"
    await _show_wizard_pair_step(update, context)


async def handle_gs_wizard_pair(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pair: str
) -> None:
    """Handle trading pair selection from button in wizard"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    # Clear old market data if pair changed (prevents stale data)
    old_pair = config.get("trading_pair", "")
    if old_pair and old_pair.upper() != pair.upper():
        for key in [
            "gs_current_price",
            "gs_candles",
            "gs_candles_interval",
            "gs_natr",
            "gs_trading_rules",
            "gs_theoretical_grid",
            "gs_market_data_ready",
            "gs_market_data_error",
        ]:
            context.user_data.pop(key, None)

    config["trading_pair"] = pair.upper()
    set_controller_config(context, config)

    # Start background fetch of market data
    asyncio.create_task(_background_fetch_market_data(context, config, chat_id))

    # Move to side step
    context.user_data["gs_wizard_step"] = "side"
    await _show_wizard_side_step(update, context)


async def _show_wizard_pair_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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

    keyboard.append(
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:gs_back_to_connector"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ]
    )

    recent_hint = ""
    if recent_pairs:
        recent_hint = "\n\nOr type a custom pair below:"

    # Determine total steps based on connector type
    is_perp = connector.endswith("_perpetual")
    total_steps = 6 if is_perp else 5

    await query.message.edit_text(
        rf"*📈 Grid Strike \- Step 2/{total_steps}*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}`" + "\n\n"
        r"🔗 *Trading Pair*" + "\n\n"
        r"Select a recent pair or enter a new one:" + escape_markdown_v2(recent_hint),
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _show_wizard_side_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:gs_back_to_pair"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    # Determine total steps based on connector type
    is_perp = connector.endswith("_perpetual")
    total_steps = 6 if is_perp else 5

    await query.message.edit_text(
        rf"*📈 Grid Strike \- Step 3/{total_steps}*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n\n"
        r"🎯 *Select Side*" + "\n\n"
        r"📈 *Long Grid* \- Opens long positions on dips and closes them "
        r"on bounces\. Bullish bias—profits from oscillations while "
        r"expecting price to trend upward\." + "\n\n"
        r"📉 *Short Grid* \- Opens short positions on rallies and closes "
        r"them on pullbacks\. Bearish bias—profits from oscillations while "
        r"expecting price to trend downward\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_gs_wizard_side(
    update: Update, context: ContextTypes.DEFAULT_TYPE, side_str: str
) -> None:
    """Handle side selection in wizard"""
    query = update.callback_query
    config = get_controller_config(context)

    config["side"] = SIDE_LONG if side_str == "long" else SIDE_SHORT
    set_controller_config(context, config)

    connector = config.get("connector_name", "")

    # Only ask for leverage on perpetual exchanges
    if connector.endswith("_perpetual"):
        context.user_data["gs_wizard_step"] = "leverage"
        await _show_wizard_leverage_step(update, context)
    else:
        # Spot exchange - set leverage to 1 and skip to amount
        config["leverage"] = 1
        set_controller_config(context, config)
        context.user_data["gs_wizard_step"] = "total_amount_quote"
        await _show_wizard_amount_step(update, context)


async def _show_wizard_leverage_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Wizard Step 4: Select Leverage"""
    query = update.callback_query
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = "📈 LONG" if config.get("side") == SIDE_LONG else "📉 SHORT"

    # Enable text input for leverage
    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "leverage"

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
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:gs_back_to_side"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    # Leverage step is only shown for perps (always 6 steps)
    await query.message.edit_text(
        r"*📈 Grid Strike \- Step 4/6*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}` \\| {side}"
        + "\n\n"
        r"⚡ *Select Leverage*" + "\n"
        r"_Or type a value \(e\.g\. 2, 3x\)_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_gs_wizard_leverage(
    update: Update, context: ContextTypes.DEFAULT_TYPE, leverage: int
) -> None:
    """Handle leverage selection in wizard"""
    query = update.callback_query
    config = get_controller_config(context)

    config["leverage"] = leverage
    set_controller_config(context, config)

    # Move to amount step
    context.user_data["gs_wizard_step"] = "total_amount_quote"
    await _show_wizard_amount_step(update, context)


async def _show_wizard_amount_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Wizard Step 5: Enter Amount with available balances"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = "📈 LONG" if config.get("side") == SIDE_LONG else "📉 SHORT"
    leverage = config.get("leverage", 1)

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "total_amount_quote"

    # Extract base and quote tokens from pair
    base_token, quote_token = "", ""
    if "-" in pair:
        base_token, quote_token = pair.split("-", 1)

    # Fetch balances for the connector
    balance_text = ""
    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        balances = await get_cex_balances(
            context.user_data, client, "master_account", ttl=30
        )

        # Try to find connector balances with flexible matching
        # (binance_perpetual should match binance_perpetual, binance, etc.)
        connector_balances = []
        connector_lower = connector.lower()
        connector_base = connector_lower.replace("_perpetual", "").replace("_spot", "")

        for bal_connector, bal_list in balances.items():
            bal_lower = bal_connector.lower()
            bal_base = bal_lower.replace("_perpetual", "").replace("_spot", "")
            # Match exact, base name, or if one contains the other
            if bal_lower == connector_lower or bal_base == connector_base:
                connector_balances = bal_list
                logger.debug(
                    f"Found balances for {connector} under key {bal_connector}"
                )
                break

        if connector_balances:
            relevant_balances = []
            for bal in connector_balances:
                token = bal.get("token", bal.get("asset", ""))
                # Portfolio API returns 'units' for available balance
                available = bal.get(
                    "units", bal.get("available_balance", bal.get("free", 0))
                )
                value_usd = bal.get("value", 0)  # USD value if available
                if token and available:
                    try:
                        available_float = float(available)
                        if available_float > 0:
                            # Show quote token and base token balances
                            if token.upper() in [
                                quote_token.upper(),
                                base_token.upper(),
                            ]:
                                relevant_balances.append(
                                    (
                                        token,
                                        available_float,
                                        float(value_usd) if value_usd else None,
                                    )
                                )
                    except (ValueError, TypeError):
                        continue

            if relevant_balances:
                bal_lines = []
                for token, available, value_usd in relevant_balances:
                    # Format amount based on size
                    if available >= 1000:
                        amt_str = f"{available:,.0f}"
                    elif available >= 1:
                        amt_str = f"{available:,.2f}"
                    else:
                        amt_str = f"{available:,.6f}"

                    # Add USD value if available
                    if value_usd and value_usd >= 1:
                        bal_lines.append(f"{token}: {amt_str} (${value_usd:,.0f})")
                    else:
                        bal_lines.append(f"{token}: {amt_str}")
                balance_text = (
                    "💼 *Available:* "
                    + " \\| ".join(escape_markdown_v2(b) for b in bal_lines)
                    + "\n\n"
                )
            else:
                # Connector has balances but not the specific tokens for this pair
                logger.debug(
                    f"Connector {connector} has balances but not {base_token} or {quote_token}"
                )
                balance_text = f"_No {escape_markdown_v2(quote_token)} balance on {escape_markdown_v2(connector)}_\n\n"
        elif balances:
            # Balances exist but not for this connector/pair
            logger.debug(
                f"No balances found for connector {connector} with tokens {base_token}/{quote_token}. Available connectors: {list(balances.keys())}"
            )
            balance_text = f"_No {escape_markdown_v2(quote_token)} balance found_\n\n"
        else:
            logger.debug(f"No balances returned from API for connector {connector}")
    except Exception as e:
        logger.warning(f"Could not fetch balances for amount step: {e}", exc_info=True)

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
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:gs_back_to_leverage"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    # Determine step number based on connector type
    # Perps: Step 5/6 (has leverage step), Spot: Step 4/5 (no leverage step)
    is_perp = connector.endswith("_perpetual")
    step_num = 5 if is_perp else 4
    total_steps = 6 if is_perp else 5

    # Calculate margin example for leverage explanation
    margin_example = 100 / leverage if leverage > 1 else 100

    message_text = (
        rf"*📈 Grid Strike \- Step {step_num}/{total_steps}*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n"
        f"🎯 {side} \\| ⚡ `{leverage}x`"
        + "\n\n"
        + balance_text
        + r"💰 *Total Amount \(Quote\)*"
        + "\n\n"
        rf"This is the total position size in {escape_markdown_v2(quote_token or 'quote asset')}, "
        r"including leverage\." + "\n\n"
        rf"_Example: 100 {escape_markdown_v2(quote_token or 'USDT')} at {leverage}x \= "
        rf"{escape_markdown_v2(f'{margin_example:.1f}')} margin used_" + "\n\n"
        r"Select a preset or type a custom amount:"
    )

    # Handle both text and photo messages (when going back from chart step)
    try:
        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        # Message is likely a photo - delete it and send new text message
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_gs_wizard_amount(
    update: Update, context: ContextTypes.DEFAULT_TYPE, amount: float
) -> None:
    """Handle amount selection in wizard"""
    query = update.callback_query
    config = get_controller_config(context)

    config["total_amount_quote"] = amount
    set_controller_config(context, config)

    pair = config.get("trading_pair", "")

    # Always show loading indicator immediately since chart generation takes time
    await query.message.edit_text(
        r"*📈 Grid Strike \- New Config*" + "\n\n"
        f"⏳ *Loading chart for* `{escape_markdown_v2(pair)}`\\.\\.\\." + "\n\n"
        r"_Fetching market data and generating chart\.\.\._",
        parse_mode="MarkdownV2",
    )

    # Move to prices step - this will fetch OHLC and show chart
    context.user_data["gs_wizard_step"] = "prices"
    await _show_wizard_prices_step(update, context)


def _calculate_min_order_amount(
    current_price: float, trading_rules: dict, default: float = 6.0
) -> float:
    """
    Calculate minimum order amount based on trading rules.

    The minimum is the greater of:
    - min_notional_size from trading rules
    - current_price * min_order_size (min base amount)
    - the provided default

    Returns the calculated minimum order amount in quote currency.
    """
    min_notional = trading_rules.get("min_notional_size", 0) or 0
    min_order_size = trading_rules.get("min_order_size", 0) or 0

    # Calculate min from base amount requirement
    min_from_base = current_price * min_order_size if min_order_size > 0 else 0

    # Take the maximum of all constraints
    calculated_min = max(default, min_notional, min_from_base)

    return calculated_min


async def _show_wizard_prices_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE, interval: str = None
) -> None:
    """Wizard Step 6: Grid Configuration with prices, TP, spread, and grid analysis"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = config.get("side", SIDE_LONG)
    total_amount = config.get("total_amount_quote", 1000)

    # Get current interval (default 5m for better NATR calculation)
    if interval is None:
        interval = context.user_data.get("gs_chart_interval", "5m")
    context.user_data["gs_chart_interval"] = interval

    # Check if we have pre-cached data from background fetch
    current_price = context.user_data.get("gs_current_price")
    candles = context.user_data.get("gs_candles")

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
                    parse_mode="MarkdownV2",
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
                    parse_mode="MarkdownV2",
                )
                context.user_data["gs_wizard_message_id"] = loading_msg.message_id

            client, _ = await get_bots_client(chat_id, context.user_data)
            current_price = await fetch_current_price(client, connector, pair)

            if current_price:
                context.user_data["gs_current_price"] = current_price
                # Fetch candles for NATR calculation and chart visualization
                candles = await fetch_candles(
                    client, connector, pair, interval=interval, max_records=420
                )
                context.user_data["gs_candles"] = candles
                context.user_data["gs_candles_interval"] = interval

                # Fetch trading rules for validation
                try:
                    rules = await get_trading_rules(
                        context.user_data, client, connector
                    )
                    context.user_data["gs_trading_rules"] = rules.get(pair, {})
                except Exception as e:
                    logger.warning(f"Could not fetch trading rules: {e}")
                    context.user_data["gs_trading_rules"] = {}

        if not current_price:
            keyboard = [
                [InlineKeyboardButton("⬅️ Back", callback_data="bots:main_menu")]
            ]
            try:
                await query.message.edit_text(
                    r"*❌ Error*" + "\n\n"
                    f"Could not fetch price for `{escape_markdown_v2(pair)}`\\.\n"
                    r"Please check the trading pair and try again\.",
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=(
                        r"*❌ Error*" + "\n\n"
                        f"Could not fetch price for `{escape_markdown_v2(pair)}`\\.\n"
                        r"Please check the trading pair and try again\."
                    ),
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            return

        # Calculate NATR from candles
        natr = None
        candles_list = candles.get("data", []) if isinstance(candles, dict) else candles
        logger.info(
            f"Candles for {pair} ({interval}): {len(candles_list) if candles_list else 0} records"
        )
        if candles_list:
            natr = calculate_natr(candles_list, period=14)
            context.user_data["gs_natr"] = natr
            # Use the last candle's close price for better chart alignment
            last_candle = candles_list[-1] if candles_list else None
            if last_candle:
                last_close = last_candle.get("close") or last_candle.get("c")
                if last_close:
                    current_price = float(last_close)
                    context.user_data["gs_current_price"] = current_price

        # Get trading rules
        trading_rules = context.user_data.get("gs_trading_rules", {})
        min_notional = trading_rules.get("min_notional_size", 5.0)
        min_order_size = trading_rules.get("min_order_size", 0)

        # Calculate smart defaults based on NATR if not already set
        if not config.get("start_price") or not config.get("end_price"):
            if natr and natr > 0:
                # Use NATR-based suggestions
                suggestions = suggest_grid_params(
                    current_price, natr, side, total_amount, min_notional
                )
                config["start_price"] = suggestions["start_price"]
                config["end_price"] = suggestions["end_price"]
                config["limit_price"] = suggestions["limit_price"]
                # Note: min_spread_between_orders and take_profit use fixed defaults from config.py
                # NATR-based suggestions are not applied - user prefers consistent defaults
            else:
                # Fallback to default percentages
                start, end, limit = calculate_auto_prices(current_price, side)
                config["start_price"] = start
                config["end_price"] = end
                config["limit_price"] = limit

        start = config.get("start_price")
        end = config.get("end_price")
        limit = config.get("limit_price")
        min_spread = config.get("min_spread_between_orders", 0.0001)
        take_profit = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)

        # Calculate minimum order amount from trading rules
        required_min_order = _calculate_min_order_amount(
            current_price, trading_rules, default=6.0
        )
        min_order_amount = config.get("min_order_amount_quote", required_min_order)

        # Ensure min_order_amount respects exchange rules
        if min_order_amount < required_min_order:
            config["min_order_amount_quote"] = required_min_order
            min_order_amount = required_min_order

        # Generate config ID with sequence number (if not already set)
        if not config.get("id"):
            existing_configs = context.user_data.get("controller_configs_list", [])
            config["id"] = generate_config_id(
                connector, pair, existing_configs=existing_configs
            )

        set_controller_config(context, config)

        # Generate theoretical grid
        grid = generate_theoretical_grid(
            start_price=start,
            end_price=end,
            min_spread=min_spread,
            total_amount=total_amount,
            min_order_amount=min_order_amount,
            current_price=current_price,
            side=side,
            trading_rules=trading_rules,
        )
        context.user_data["gs_theoretical_grid"] = grid

        # Show price edit options
        side_str = "📈 LONG" if side == SIDE_LONG else "📉 SHORT"

        context.user_data["bots_state"] = "gs_wizard_input"
        context.user_data["gs_wizard_step"] = "prices"

        # Build interval buttons with current one highlighted
        interval_options = ["1m", "5m", "15m", "1h", "4h"]
        interval_row = []
        for opt in interval_options:
            label = f"✓ {opt}" if opt == interval else opt
            interval_row.append(
                InlineKeyboardButton(label, callback_data=f"bots:gs_interval:{opt}")
            )

        keyboard = [
            interval_row,
            [
                InlineKeyboardButton("💾 Save Config", callback_data="bots:gs_save"),
            ],
            [
                InlineKeyboardButton("⬅️ Back", callback_data="bots:gs_back_to_amount"),
                InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
            ],
        ]

        # Get config values
        max_open_orders = config.get("max_open_orders", 3)
        order_frequency = config.get("order_frequency", 3)
        leverage = config.get("leverage", 1)
        position_mode = config.get("position_mode", "ONEWAY")
        coerce_tp_to_step = config.get("coerce_tp_to_step", False)
        activation_bounds = config.get("activation_bounds", 0.01)
        side_value = config.get("side", SIDE_LONG)
        side_str_label = "LONG" if side_value == SIDE_LONG else "SHORT"

        # Grid analysis info
        grid_valid = "✓" if grid.get("valid") else "⚠️"
        natr_pct = f"{natr*100:.2f}%" if natr else "N/A"
        range_pct = f"{grid.get('grid_range_pct', 0):.2f}%"

        # Determine final step number based on connector type
        is_perp = connector.endswith("_perpetual")
        final_step = 6 if is_perp else 5

        # Build config text with individually copyable key=value params
        config_text = (
            rf"*📈 Grid Strike \- Step {final_step}/{final_step} \(Final\)*" + "\n\n"
            f"*{escape_markdown_v2(pair)}* {side_str_label}\n"
            f"Price: `{current_price:,.6g}` \\| Range: `{range_pct}` \\| NATR: `{natr_pct}`\n\n"
            f"`connector_name={connector}`\n"
            f"`trading_pair={pair}`\n"
            f"`total_amount_quote={total_amount:.0f}`\n"
            f"`start_price={start:.6g}`\n"
            f"`end_price={end:.6g}`\n"
            f"`limit_price={limit:.6g}`\n"
            f"`leverage={leverage}`\n"
            f"`position_mode={position_mode}`\n"
            f"`take_profit={take_profit}`\n"
            f"`coerce_tp_to_step={str(coerce_tp_to_step).lower()}`\n"
            f"`min_spread_between_orders={min_spread}`\n"
            f"`min_order_amount_quote={min_order_amount:.0f}`\n"
            f"`max_open_orders={max_open_orders}`\n"
            f"`activation_bounds={activation_bounds}`\n\n"
            f"{grid_valid} Grid: `{grid['num_levels']}` levels "
            f"\\(↓{grid.get('levels_below_current', 0)} ↑{grid.get('levels_above_current', 0)}\\) "
            f"@ `${grid['amount_per_level']:.2f}`/lvl \\| step: `{grid.get('spread_pct', 0):.3f}%`"
        )

        # Add warnings if any
        if grid.get("warnings"):
            warnings_text = "\n".join(
                f"⚠️ {escape_markdown_v2(w)}" for w in grid["warnings"]
            )
            config_text += f"\n{warnings_text}"

        config_text += "\n\n_Edit: `field=value`_"

        # Generate chart and send as photo with caption
        if candles_list:
            chart_bytes = generate_candles_chart(
                candles_list,
                pair,
                start_price=start,
                end_price=end,
                limit_price=limit,
                current_price=current_price,
                side=side,
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
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

            context.user_data["gs_wizard_message_id"] = msg.message_id
            context.user_data["gs_wizard_chat_id"] = query.message.chat_id
        else:
            # No chart - handle photo messages
            if getattr(query.message, "photo", None):
                try:
                    await query.message.delete()
                except Exception:
                    pass
                msg = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=config_text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["gs_wizard_message_id"] = msg.message_id
            else:
                await query.message.edit_text(
                    text=config_text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["gs_wizard_message_id"] = query.message.message_id

    except Exception as e:
        logger.error(f"Error in prices step: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        error_msg = format_error_message(f"Error fetching market data: {str(e)}")
        try:
            if getattr(query.message, "photo", None):
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await query.message.chat.send_message(
                    error_msg,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            else:
                await query.message.edit_text(
                    error_msg,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
        except Exception:
            pass


async def handle_gs_accept_prices(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Accept grid configuration and save - legacy handler, redirects to gs_save"""
    # Redirect to save handler since prices step is now the final step
    await handle_gs_save(update, context)


async def handle_gs_back_to_prices(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Go back to prices step from validation error"""
    context.user_data["gs_wizard_step"] = "prices"
    await _show_wizard_prices_step(update, context)


async def handle_gs_back_to_connector(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Go back to connector selection step"""
    context.user_data["gs_wizard_step"] = "connector_name"
    await _show_wizard_connector_step(update, context)


async def handle_gs_back_to_pair(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Go back to trading pair step"""
    context.user_data["gs_wizard_step"] = "trading_pair"
    await _show_wizard_pair_step(update, context)


async def handle_gs_back_to_side(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Go back to side selection step"""
    context.user_data["gs_wizard_step"] = "side"
    await _show_wizard_side_step(update, context)


async def handle_gs_back_to_leverage(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Go back to leverage step (or side step for spot exchanges)"""
    config = get_controller_config(context)
    connector = config.get("connector_name", "")

    # If spot exchange, go back to side step instead
    if not connector.endswith("_perpetual"):
        context.user_data["gs_wizard_step"] = "side"
        await _show_wizard_side_step(update, context)
    else:
        context.user_data["gs_wizard_step"] = "leverage"
        await _show_wizard_leverage_step(update, context)


async def handle_gs_back_to_amount(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Go back to amount step"""
    context.user_data["gs_wizard_step"] = "total_amount_quote"
    # Clear cached market data to avoid showing stale chart
    context.user_data.pop("gs_current_price", None)
    context.user_data.pop("gs_candles", None)
    await _show_wizard_amount_step(update, context)


async def handle_gs_interval_change(
    update: Update, context: ContextTypes.DEFAULT_TYPE, interval: str
) -> None:
    """Handle interval change for chart - refetch candles with new interval"""
    query = update.callback_query

    # Clear cached candles to force refetch
    context.user_data.pop("gs_candles", None)
    context.user_data["gs_chart_interval"] = interval

    # Redisplay prices step with new interval
    await _show_wizard_prices_step(update, context, interval=interval)


async def _show_wizard_take_profit_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
        [InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")],
    ]

    message_text = (
        r"*📈 Grid Strike \- New Config*" + "\n\n"
        f"🏦 *Connector:* `{escape_markdown_v2(connector)}`" + "\n"
        f"🔗 *Pair:* `{escape_markdown_v2(pair)}`" + "\n"
        f"🎯 *Side:* `{side}` \\| ⚡ *Leverage:* `{config.get('leverage', 1)}x`" + "\n"
        f"💰 *Amount:* `{config.get('total_amount_quote', 0):,.0f}`" + "\n"
        f"📊 *Grid:* `{config.get('start_price', 0):,.6g}` \\- `{config.get('end_price', 0):,.6g}`"
        + "\n\n"
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
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    context.user_data["gs_wizard_message_id"] = msg.message_id
    context.user_data["gs_wizard_chat_id"] = query.message.chat_id


async def handle_gs_wizard_take_profit(
    update: Update, context: ContextTypes.DEFAULT_TYPE, tp: float
) -> None:
    """Handle take profit selection and show final review"""
    query = update.callback_query
    config = get_controller_config(context)

    if "triple_barrier_config" not in config:
        config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
            "triple_barrier_config"
        ].copy()
    config["triple_barrier_config"]["take_profit"] = tp
    set_controller_config(context, config)

    # Move to review step
    context.user_data["gs_wizard_step"] = "review"
    await _show_wizard_review_step(update, context)


async def _show_wizard_review_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Final Review Step with copyable config format"""
    query = update.callback_query
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = "LONG" if config.get("side") == SIDE_LONG else "SHORT"
    leverage = config.get("leverage", 1)
    position_mode = config.get("position_mode", "ONEWAY")
    amount = config.get("total_amount_quote", 0)
    start_price = config.get("start_price", 0)
    end_price = config.get("end_price", 0)
    limit_price = config.get("limit_price", 0)
    tp = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)
    open_order_type = config.get("triple_barrier_config", {}).get(
        "open_order_type", ORDER_TYPE_LIMIT_MAKER
    )
    tp_order_type = config.get("triple_barrier_config", {}).get(
        "take_profit_order_type", ORDER_TYPE_LIMIT_MAKER
    )
    keep_position = config.get("keep_position", True)
    activation_bounds = config.get("activation_bounds", 0.01)
    config_id = config.get("id", "")
    max_open_orders = config.get("max_open_orders", 3)
    max_orders_per_batch = config.get("max_orders_per_batch", 1)
    order_frequency = config.get("order_frequency", 3)
    min_order_amount = config.get("min_order_amount_quote", 6)
    min_spread = config.get("min_spread_between_orders", 0.0001)
    coerce_tp_to_step = config.get("coerce_tp_to_step", False)

    # Delete previous chart if exists
    chart_msg_id = context.user_data.pop("gs_chart_message_id", None)
    if chart_msg_id:
        try:
            await context.bot.delete_message(
                chat_id=query.message.chat_id, message_id=chart_msg_id
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
        f"position_mode: {position_mode}\n"
        f"total_amount_quote: {amount:.0f}\n"
        f"start_price: {start_price:.6g}\n"
        f"end_price: {end_price:.6g}\n"
        f"limit_price: {limit_price:.6g}\n"
        f"take_profit: {tp}\n"
        f"open_order_type: {open_order_type}\n"
        f"take_profit_order_type: {tp_order_type}\n"
        f"coerce_tp_to_step: {str(coerce_tp_to_step).lower()}\n"
        f"keep_position: {str(keep_position).lower()}\n"
        f"activation_bounds: {activation_bounds}\n"
        f"max_open_orders: {max_open_orders}\n"
        f"max_orders_per_batch: {max_orders_per_batch}\n"
        f"order_frequency: {order_frequency}\n"
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
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    # Handle photo messages - can't edit_text on photos, need to delete and send new
    try:
        if getattr(query.message, "photo", None):
            await query.message.delete()
            msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            context.user_data["gs_wizard_message_id"] = msg.message_id
        else:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
    except BadRequest as e:
        # Fallback: delete and send new message
        try:
            await query.message.delete()
        except Exception:
            pass
        msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        context.user_data["gs_wizard_message_id"] = msg.message_id


async def _update_wizard_message_for_review(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
    position_mode = config.get("position_mode", "ONEWAY")
    amount = config.get("total_amount_quote", 0)
    start_price = config.get("start_price", 0)
    end_price = config.get("end_price", 0)
    limit_price = config.get("limit_price", 0)
    tp = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)
    open_order_type = config.get("triple_barrier_config", {}).get(
        "open_order_type", ORDER_TYPE_LIMIT_MAKER
    )
    tp_order_type = config.get("triple_barrier_config", {}).get(
        "take_profit_order_type", ORDER_TYPE_LIMIT_MAKER
    )
    keep_position = config.get("keep_position", True)
    activation_bounds = config.get("activation_bounds", 0.01)
    config_id = config.get("id", "")
    max_open_orders = config.get("max_open_orders", 3)
    max_orders_per_batch = config.get("max_orders_per_batch", 1)
    order_frequency = config.get("order_frequency", 3)
    min_order_amount = config.get("min_order_amount_quote", 6)
    min_spread = config.get("min_spread_between_orders", 0.0001)
    coerce_tp_to_step = config.get("coerce_tp_to_step", False)

    # Build copyable config block with real YAML field names
    side_value = config.get("side", SIDE_LONG)
    config_block = (
        f"id: {config_id}\n"
        f"connector_name: {connector}\n"
        f"trading_pair: {pair}\n"
        f"side: {side_value}\n"
        f"leverage: {leverage}\n"
        f"position_mode: {position_mode}\n"
        f"total_amount_quote: {amount:.0f}\n"
        f"start_price: {start_price:.6g}\n"
        f"end_price: {end_price:.6g}\n"
        f"limit_price: {limit_price:.6g}\n"
        f"take_profit: {tp}\n"
        f"open_order_type: {open_order_type}\n"
        f"take_profit_order_type: {tp_order_type}\n"
        f"coerce_tp_to_step: {str(coerce_tp_to_step).lower()}\n"
        f"keep_position: {str(keep_position).lower()}\n"
        f"activation_bounds: {activation_bounds}\n"
        f"max_open_orders: {max_open_orders}\n"
        f"max_orders_per_batch: {max_orders_per_batch}\n"
        f"order_frequency: {order_frequency}\n"
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
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        logger.error(f"Error updating review message: {e}", exc_info=True)
        logger.debug(f"Message text was: {message_text[:500]}")


async def handle_gs_edit_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allow user to edit config ID before saving"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat_id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_id"

    current_id = config.get("id", "")

    keyboard = [
        [
            InlineKeyboardButton(
                f"Keep: {current_id[:25]}", callback_data="bots:gs_save"
            )
        ],
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
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_keep(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_act(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_max_orders(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_batch(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_min_amt(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Edit min order amount"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat_id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_min_amt"

    current = config.get("min_order_amount_quote", 6)

    # Calculate minimum required from trading rules
    current_price = context.user_data.get("gs_current_price", 0)
    trading_rules = context.user_data.get("gs_trading_rules", {})
    required_min = _calculate_min_order_amount(
        current_price, trading_rules, default=6.0
    )

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
        f"Current: `{current}`\n"
        f"Minimum: `{required_min:.2f}` \\(from trading rules\\)" + "\n\n"
        r"Enter new value:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_edit_spread(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Edit min spread between orders"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = query.message.chat_id

    context.user_data["bots_state"] = "gs_wizard_input"
    context.user_data["gs_wizard_step"] = "edit_spread"

    current = config.get("min_spread_between_orders", 0.0001)

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
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    context.user_data["gs_wizard_message_id"] = msg.message_id


async def handle_gs_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save the Grid Strike configuration"""
    query = update.callback_query
    config = get_controller_config(context)

    # Validate price ordering before saving
    side = config.get("side", SIDE_LONG)
    start_price = config.get("start_price", 0)
    end_price = config.get("end_price", 0)
    limit_price = config.get("limit_price", 0)

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
        keyboard = [
            [
                InlineKeyboardButton(
                    "Edit Prices", callback_data="bots:gs_back_to_prices"
                )
            ],
            [InlineKeyboardButton("Cancel", callback_data="bots:main_menu")],
        ]
        try:
            await query.message.delete()
        except:
            pass
        msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"⚠️ *Price Validation Error*\n\n{validation_error}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        context.user_data["gs_wizard_message_id"] = msg.message_id
        context.user_data["gs_wizard_chat_id"] = query.message.chat_id
        return

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
        parse_mode="MarkdownV2",
    )

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        result = await client.controllers.create_or_update_controller_config(
            config_id, config
        )

        # Clean up wizard state
        _cleanup_wizard_state(context)

        keyboard = [
            [
                InlineKeyboardButton(
                    "Create Another", callback_data="bots:new_grid_strike"
                )
            ],
            [
                InlineKeyboardButton(
                    "Back to Configs", callback_data="bots:controller_configs"
                )
            ],
        ]

        await status_msg.edit_text(
            r"*Config Saved\!*" + "\n\n"
            f"Controller `{escape_markdown_v2(config_id)}` saved successfully\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
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
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_gs_review_back(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Go back to prices step (main configuration screen)"""
    context.user_data["gs_wizard_step"] = "prices"
    await _show_wizard_prices_step(update, context)


def _cleanup_wizard_state(context) -> None:
    """Clean up wizard-related state"""
    keys_to_remove = [
        "gs_wizard_step",
        "gs_wizard_message_id",
        "gs_wizard_chat_id",
        "gs_current_price",
        "gs_candles",
        "gs_chart_message_id",
        "gs_market_data_ready",
        "gs_market_data_error",
        "gs_chart_interval",
        "gs_candles_interval",
    ]
    for key in keys_to_remove:
        context.user_data.pop(key, None)
    clear_bots_state(context)


async def _background_fetch_market_data(
    context, config: dict, chat_id: int = None
) -> None:
    """Background task to fetch market data while user continues with wizard"""
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    if not connector or not pair:
        return

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        # Fetch current price
        current_price = await fetch_current_price(client, connector, pair)

        if current_price:
            context.user_data["gs_current_price"] = current_price

            # Fetch candles (5m, 420 records) - consistent with default interval
            candles = await fetch_candles(
                client, connector, pair, interval="5m", max_records=420
            )
            context.user_data["gs_candles"] = candles
            context.user_data["gs_candles_interval"] = "5m"
            context.user_data["gs_market_data_ready"] = True

            logger.info(f"Background fetch complete for {pair}: price={current_price}")
        else:
            context.user_data["gs_market_data_error"] = (
                f"Could not fetch price for {pair}"
            )

    except Exception as e:
        logger.error(f"Background fetch error for {pair}: {e}")
        context.user_data["gs_market_data_error"] = str(e)


async def process_gs_wizard_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str
) -> None:
    """Process text input during wizard flow"""
    step = context.user_data.get("gs_wizard_step")
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    logger.debug(f"GS wizard input: step={step}, input={user_input[:50]}")

    if not step:
        logger.warning("GS wizard input called but no step set")
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

            connector = config.get("connector_name", "")

            # Validate trading pair exists on the connector
            client, _ = await get_bots_client(chat_id, context.user_data)
            is_valid, error_msg, suggestions, correct_pair = (
                await validate_trading_pair(context.user_data, client, connector, pair)
            )

            if not is_valid:
                # Show error with suggestions
                await _show_gs_pair_suggestions(
                    update, context, pair, error_msg, suggestions, connector
                )
                return

            # Use the correct pair format returned by validation
            if correct_pair:
                pair = correct_pair
            else:
                # Fallback: Get correctly formatted pair from trading rules
                trading_rules = await get_trading_rules(
                    context.user_data, client, connector
                )
                fallback_pair = get_correct_pair_format(trading_rules, pair)
                if fallback_pair:
                    pair = fallback_pair

            # Clear old market data if pair changed (prevents stale data)
            old_pair = config.get("trading_pair", "")
            if old_pair and old_pair.upper() != pair.upper():
                for key in [
                    "gs_current_price",
                    "gs_candles",
                    "gs_candles_interval",
                    "gs_natr",
                    "gs_trading_rules",
                    "gs_theoretical_grid",
                    "gs_market_data_ready",
                    "gs_market_data_error",
                ]:
                    context.user_data.pop(key, None)

            config["trading_pair"] = pair
            set_controller_config(context, config)

            # Start background fetch of market data
            asyncio.create_task(_background_fetch_market_data(context, config, chat_id))

            # Move to side step
            context.user_data["gs_wizard_step"] = "side"

            # Update the wizard message
            await _update_wizard_message_for_side(update, context)

        elif step == "prices":
            # Handle multiple input formats:
            # 1. field=value - set any field (e.g., start_price=130, order_frequency=5)
            # 2. start,end,limit - price values (legacy)
            # 3. tp:0.1 - take profit percentage (legacy)
            # 4. spread:0.05 - min spread percentage (legacy)
            # 5. min:10 - min order amount (legacy)
            input_stripped = user_input.strip()
            input_lower = input_stripped.lower()

            # Check for field=value format first
            if "=" in input_stripped:
                # Parse field=value format
                changes_made = False
                chart_affecting_change = False  # Track if chart needs regeneration
                warning_msg = None
                # Fields that affect the chart visualization
                chart_fields = {
                    "start_price",
                    "start",
                    "end_price",
                    "end",
                    "limit_price",
                    "limit",
                    "connector_name",
                    "trading_pair",
                }

                for line in input_stripped.split("\n"):
                    line = line.strip()
                    if not line or "=" not in line:
                        continue

                    field, value = line.split("=", 1)
                    field = field.strip().lower()
                    value = value.strip()

                    # Check if this field affects the chart
                    if field in chart_fields:
                        chart_affecting_change = True

                    # Map field names and set values
                    if field in ("start_price", "start"):
                        config["start_price"] = float(value)
                        changes_made = True
                    elif field in ("end_price", "end"):
                        config["end_price"] = float(value)
                        changes_made = True
                    elif field in ("limit_price", "limit"):
                        config["limit_price"] = float(value)
                        changes_made = True
                    elif field in ("take_profit", "tp"):
                        # Support both decimal (0.001) and percentage (0.1%)
                        val = float(value.replace("%", ""))
                        if val > 1:  # Likely percentage like 0.1
                            val = val / 100
                        config.setdefault(
                            "triple_barrier_config",
                            GRID_STRIKE_DEFAULTS["triple_barrier_config"].copy(),
                        )
                        config["triple_barrier_config"]["take_profit"] = val
                        changes_made = True
                    elif field in ("min_spread_between_orders", "min_spread", "spread"):
                        val = float(value.replace("%", ""))
                        if val > 1:  # Likely percentage
                            val = val / 100
                        config["min_spread_between_orders"] = val
                        changes_made = True
                    elif field in (
                        "min_order_amount_quote",
                        "min_order_amount",
                        "min_order",
                        "min",
                    ):
                        new_min_amt = float(value.replace("$", ""))
                        # Validate against trading rules
                        current_price = context.user_data.get("gs_current_price", 0)
                        trading_rules = context.user_data.get("gs_trading_rules", {})
                        required_min = _calculate_min_order_amount(
                            current_price, trading_rules, default=6.0
                        )
                        if new_min_amt < required_min:
                            config["min_order_amount_quote"] = required_min
                            warning_msg = f"Min order must be >= ${required_min:.2f}"
                        else:
                            config["min_order_amount_quote"] = new_min_amt
                        changes_made = True
                    elif field in ("total_amount_quote", "total_amount", "amount"):
                        config["total_amount_quote"] = float(value)
                        changes_made = True
                    elif field == "leverage":
                        config["leverage"] = int(float(value))
                        changes_made = True
                    elif field == "side":
                        config["side"] = int(float(value))
                        changes_made = True
                    elif field in ("max_open_orders", "max_orders"):
                        config["max_open_orders"] = int(float(value))
                        changes_made = True
                    elif field == "order_frequency":
                        config["order_frequency"] = int(float(value))
                        changes_made = True
                    elif field == "max_orders_per_batch":
                        config["max_orders_per_batch"] = int(float(value))
                        changes_made = True
                    elif field == "activation_bounds":
                        val = float(value.replace("%", ""))
                        if val > 1:  # Likely percentage
                            val = val / 100
                        config["activation_bounds"] = val
                        changes_made = True
                    elif field in ("coerce_tp_to_step", "coerce_tp", "coerce"):
                        # Boolean field - accept true/false/1/0/yes/no
                        val_lower = value.lower()
                        config["coerce_tp_to_step"] = val_lower in (
                            "true",
                            "1",
                            "yes",
                            "on",
                        )
                        changes_made = True
                    elif field == "position_mode":
                        config["position_mode"] = value.upper()
                        changes_made = True

                if changes_made:
                    set_controller_config(context, config)
                    # Only regenerate chart if price/pair fields changed
                    if chart_affecting_change:
                        await _update_wizard_message_for_prices_after_edit(
                            update, context
                        )
                    else:
                        await _update_wizard_caption_only(
                            update, context, warning_msg=warning_msg
                        )
                else:
                    raise ValueError(f"Unknown field: {field}")

            elif input_lower.startswith("tp:"):
                # Take profit in percentage (e.g., tp:0.1 = 0.1% = 0.001)
                tp_pct = float(input_lower.replace("tp:", "").replace("%", "").strip())
                tp_decimal = tp_pct / 100
                if "triple_barrier_config" not in config:
                    config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
                        "triple_barrier_config"
                    ].copy()
                config["triple_barrier_config"]["take_profit"] = tp_decimal
                set_controller_config(context, config)
                await _update_wizard_caption_only(update, context)

            elif input_lower.startswith("spread:"):
                # Min spread in percentage (e.g., spread:0.05 = 0.05% = 0.0005)
                spread_pct = float(
                    input_lower.replace("spread:", "").replace("%", "").strip()
                )
                spread_decimal = spread_pct / 100
                config["min_spread_between_orders"] = spread_decimal
                set_controller_config(context, config)
                await _update_wizard_caption_only(update, context)

            elif input_lower.startswith("min:"):
                # Min order amount in quote (e.g., min:10 = $10)
                min_amt = float(
                    input_lower.replace("min:", "").replace("$", "").strip()
                )
                # Validate against trading rules
                current_price = context.user_data.get("gs_current_price", 0)
                trading_rules = context.user_data.get("gs_trading_rules", {})
                required_min = _calculate_min_order_amount(
                    current_price, trading_rules, default=6.0
                )
                warning_msg = None
                if min_amt < required_min:
                    config["min_order_amount_quote"] = required_min
                    warning_msg = f"Min order must be >= ${required_min:.2f}"
                else:
                    config["min_order_amount_quote"] = min_amt
                set_controller_config(context, config)
                await _update_wizard_caption_only(
                    update, context, warning_msg=warning_msg
                )

            else:
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
                    raise ValueError("Use format: field=value (e.g., start_price=130)")
                else:
                    raise ValueError("Invalid format")

        elif step == "take_profit":
            # Parse take profit - interpret as percentage (0.4 = 0.4% = 0.004)
            tp_input = user_input.replace("%", "").strip()
            tp_pct = float(tp_input)
            tp_decimal = tp_pct / 100  # Convert 0.4 -> 0.004

            config = get_controller_config(context)
            if "triple_barrier_config" not in config:
                config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
                    "triple_barrier_config"
                ].copy()
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

        elif step == "leverage":
            # Parse leverage - handle formats like "2", "2x", "2X", "10x"
            lev_input = user_input.strip().lower().replace("x", "")
            leverage = int(float(lev_input))

            if leverage < 1:
                raise ValueError("Leverage must be at least 1")

            config["leverage"] = leverage
            set_controller_config(context, config)

            # Move to amount step
            context.user_data["gs_wizard_step"] = "total_amount_quote"
            await _update_wizard_message_for_amount(update, context)

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
                config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
                    "triple_barrier_config"
                ].copy()
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
            new_min_amt = float(user_input)
            # Validate against trading rules
            current_price = context.user_data.get("gs_current_price", 0)
            trading_rules = context.user_data.get("gs_trading_rules", {})
            required_min = _calculate_min_order_amount(
                current_price, trading_rules, default=6.0
            )
            if new_min_amt < required_min:
                config["min_order_amount_quote"] = required_min
                # Send warning message
                warn_msg = await update.message.reply_text(
                    f"Min order must be >= ${required_min:.2f}. Set to ${required_min:.2f}."
                )
                await asyncio.sleep(3)
                try:
                    await warn_msg.delete()
                except:
                    pass
            else:
                config["min_order_amount_quote"] = new_min_amt
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
                "position_mode": "position_mode",
                "total_amount_quote": "total_amount_quote",
                "start_price": "start_price",
                "end_price": "end_price",
                "limit_price": "limit_price",
                "take_profit": "triple_barrier_config.take_profit",
                "open_order_type": "triple_barrier_config.open_order_type",
                "take_profit_order_type": "triple_barrier_config.take_profit_order_type",
                "coerce_tp_to_step": "coerce_tp_to_step",
                "keep_position": "keep_position",
                "activation_bounds": "activation_bounds",
                "max_open_orders": "max_open_orders",
                "max_orders_per_batch": "max_orders_per_batch",
                "order_frequency": "order_frequency",
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
                elif key == "position_mode":
                    # Accept HEDGE or ONEWAY (case insensitive)
                    config["position_mode"] = (
                        "ONEWAY" if value.upper() == "ONEWAY" else "HEDGE"
                    )
                elif key == "keep_position":
                    config["keep_position"] = value.lower() in ("true", "yes", "y", "1")
                elif key == "coerce_tp_to_step":
                    config["coerce_tp_to_step"] = value.lower() in (
                        "true",
                        "yes",
                        "y",
                        "1",
                    )
                elif key == "take_profit":
                    if "triple_barrier_config" not in config:
                        config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
                            "triple_barrier_config"
                        ].copy()
                    config["triple_barrier_config"]["take_profit"] = float(value)
                elif key == "open_order_type":
                    if "triple_barrier_config" not in config:
                        config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
                            "triple_barrier_config"
                        ].copy()
                    config["triple_barrier_config"]["open_order_type"] = int(value)
                elif key == "take_profit_order_type":
                    if "triple_barrier_config" not in config:
                        config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
                            "triple_barrier_config"
                        ].copy()
                    config["triple_barrier_config"]["take_profit_order_type"] = int(
                        value
                    )
                elif field in [
                    "leverage",
                    "max_open_orders",
                    "max_orders_per_batch",
                    "order_frequency",
                ]:
                    config[field] = int(value)
                elif field in [
                    "total_amount_quote",
                    "start_price",
                    "end_price",
                    "limit_price",
                    "activation_bounds",
                    "min_order_amount_quote",
                    "min_spread_between_orders",
                ]:
                    config[field] = float(value)
                else:
                    config[field] = value

                updated_fields.append(key)

            if updated_fields:
                set_controller_config(context, config)
                await _update_wizard_message_for_review(update, context)
            else:
                raise ValueError("No valid fields found")

    except ValueError as e:
        # Send error and let user try again
        logger.warning(f"GS wizard input ValueError: {e}")
        error_msg = await update.message.reply_text(
            f"Invalid input. Please enter a valid value."
        )
        # Auto-delete error after 3 seconds
        await asyncio.sleep(3)
        try:
            await error_msg.delete()
        except:
            pass
    except Exception as e:
        # Catch any other exceptions and log them
        logger.error(f"GS wizard input error: {e}", exc_info=True)
        try:
            error_msg = await update.message.reply_text(
                f"Error processing input: {str(e)[:100]}"
            )
            await asyncio.sleep(3)
            await error_msg.delete()
        except:
            pass


async def _show_gs_pair_suggestions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    input_pair: str,
    error_msg: str,
    suggestions: list,
    connector: str,
) -> None:
    """Show trading pair suggestions when validation fails in grid strike wizard"""
    config = get_controller_config(context)
    message_id = context.user_data.get("gs_wizard_message_id")
    chat_id = context.user_data.get("gs_wizard_chat_id")

    # Build suggestion message
    help_text = f"❌ *{escape_markdown_v2(error_msg)}*\n\n"

    if suggestions:
        help_text += "💡 *Did you mean:*\n"
    else:
        help_text += "_No similar pairs found\\._\n"

    # Build keyboard with suggestions
    keyboard = []
    for pair in suggestions:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📈 {pair}", callback_data=f"bots:gs_pair_select:{pair}"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:gs_back_to_connector"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ]
    )
    reply_markup = InlineKeyboardMarkup(keyboard)

    if message_id and chat_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.debug(f"Could not update wizard message: {e}")
    else:
        await update.effective_chat.send_message(
            help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def handle_gs_pair_select(
    update: Update, context: ContextTypes.DEFAULT_TYPE, trading_pair: str
) -> None:
    """Handle selection of a suggested trading pair in grid strike wizard"""
    config = get_controller_config(context)
    chat_id = update.effective_chat.id

    # Clear old market data
    for key in [
        "gs_current_price",
        "gs_candles",
        "gs_candles_interval",
        "gs_natr",
        "gs_trading_rules",
        "gs_theoretical_grid",
        "gs_market_data_ready",
        "gs_market_data_error",
    ]:
        context.user_data.pop(key, None)

    config["trading_pair"] = trading_pair
    set_controller_config(context, config)

    # Start background fetch of market data
    asyncio.create_task(_background_fetch_market_data(context, config, chat_id))

    # Move to side step
    context.user_data["gs_wizard_step"] = "side"

    # Update the wizard message
    await _update_wizard_message_for_side(update, context)


async def _update_wizard_message_for_side(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
        [InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")],
    ]

    # Determine total steps based on connector type
    is_perp = connector.endswith("_perpetual")
    total_steps = 6 if is_perp else 5

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=(
                rf"*📈 Grid Strike \- Step 3/{total_steps}*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
                + "\n\n"
                r"🎯 *Select Side*"
            ),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        logger.error(f"Error updating wizard message: {e}")


async def _update_wizard_message_for_prices(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Trigger prices step after amount input"""
    message_id = context.user_data.get("gs_wizard_message_id")
    chat_id = context.user_data.get("gs_wizard_chat_id")

    if not message_id or not chat_id:
        return

    # Create a fake query object to reuse _show_wizard_prices_step
    class FakeChat:
        def __init__(self, chat_id):
            self.id = chat_id

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
                chat_id=self.chat_id, message_id=self.message_id, text=text, **kwargs
            )

        async def delete(self):
            await self._bot.delete_message(
                chat_id=self.chat_id, message_id=self.message_id
            )

    fake_update = type(
        "FakeUpdate",
        (),
        {
            "callback_query": FakeQuery(context.bot, chat_id, message_id),
            "effective_chat": FakeChat(chat_id),
        },
    )()
    await _show_wizard_prices_step(fake_update, context)


async def _update_wizard_message_for_amount(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Trigger amount step after leverage input"""
    message_id = context.user_data.get("gs_wizard_message_id")
    chat_id = context.user_data.get("gs_wizard_chat_id")

    if not message_id or not chat_id:
        return

    # Create a fake query object to reuse _show_wizard_amount_step
    class FakeChat:
        def __init__(self, chat_id):
            self.id = chat_id

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
                chat_id=self.chat_id, message_id=self.message_id, text=text, **kwargs
            )

        async def delete(self):
            await self._bot.delete_message(
                chat_id=self.chat_id, message_id=self.message_id
            )

    fake_update = type(
        "FakeUpdate",
        (),
        {
            "callback_query": FakeQuery(context.bot, chat_id, message_id),
            "effective_chat": FakeChat(chat_id),
        },
    )()
    await _show_wizard_amount_step(fake_update, context)


async def _update_wizard_message_for_prices_after_edit(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Update prices display after editing prices - regenerate chart with new prices and grid analysis"""
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
    total_amount = config.get("total_amount_quote", 1000)
    min_spread = config.get("min_spread_between_orders", 0.0001)
    take_profit = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)
    min_order_amount = config.get("min_order_amount_quote", 6)
    natr = context.user_data.get("gs_natr")
    trading_rules = context.user_data.get("gs_trading_rules", {})

    # Regenerate theoretical grid with updated parameters
    grid = generate_theoretical_grid(
        start_price=start,
        end_price=end,
        min_spread=min_spread,
        total_amount=total_amount,
        min_order_amount=min_order_amount,
        current_price=current_price,
        side=side,
        trading_rules=trading_rules,
    )
    context.user_data["gs_theoretical_grid"] = grid

    # Build interval buttons with current one highlighted
    interval_options = ["1m", "5m", "15m", "1h", "4h"]
    interval_row = []
    for opt in interval_options:
        label = f"✓ {opt}" if opt == interval else opt
        interval_row.append(
            InlineKeyboardButton(label, callback_data=f"bots:gs_interval:{opt}")
        )

    keyboard = [
        interval_row,
        [
            InlineKeyboardButton("💾 Save Config", callback_data="bots:gs_save"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")],
    ]

    # Get config values
    max_open_orders = config.get("max_open_orders", 3)
    order_frequency = config.get("order_frequency", 3)
    leverage = config.get("leverage", 1)
    position_mode = config.get("position_mode", "ONEWAY")
    coerce_tp_to_step = config.get("coerce_tp_to_step", False)
    activation_bounds = config.get("activation_bounds", 0.01)
    side_value = config.get("side", SIDE_LONG)
    side_str = "LONG" if side_value == SIDE_LONG else "SHORT"

    # Grid analysis info
    grid_valid = "✓" if grid.get("valid") else "⚠️"
    natr_pct = f"{natr*100:.2f}%" if natr else "N/A"
    range_pct = f"{grid.get('grid_range_pct', 0):.2f}%"

    # Build config text with individually copyable key=value params
    config_text = (
        f"*{escape_markdown_v2(pair)}* {side_str}\n"
        f"Price: `{current_price:,.6g}` \\| Range: `{range_pct}` \\| NATR: `{natr_pct}`\n\n"
        f"`connector_name={connector}`\n"
        f"`trading_pair={pair}`\n"
        f"`total_amount_quote={total_amount:.0f}`\n"
        f"`start_price={start:.6g}`\n"
        f"`end_price={end:.6g}`\n"
        f"`limit_price={limit:.6g}`\n"
        f"`leverage={leverage}`\n"
        f"`position_mode={position_mode}`\n"
        f"`take_profit={take_profit}`\n"
        f"`coerce_tp_to_step={str(coerce_tp_to_step).lower()}`\n"
        f"`min_spread_between_orders={min_spread}`\n"
        f"`min_order_amount_quote={min_order_amount:.0f}`\n"
        f"`max_open_orders={max_open_orders}`\n"
        f"`activation_bounds={activation_bounds}`\n\n"
        f"{grid_valid} Grid: `{grid['num_levels']}` levels "
        f"\\(↓{grid.get('levels_below_current', 0)} ↑{grid.get('levels_above_current', 0)}\\) "
        f"@ `${grid['amount_per_level']:.2f}`/lvl \\| step: `{grid.get('spread_pct', 0):.3f}%`"
    )

    # Add warnings if any
    if grid.get("warnings"):
        warnings_text = "\n".join(
            f"⚠️ {escape_markdown_v2(w)}" for w in grid["warnings"]
        )
        config_text += f"\n{warnings_text}"

    config_text += "\n\n_Edit: `field=value`_"

    try:
        # Delete old message (which is a photo)
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

        # Get candles list
        candles_list = candles.get("data", []) if isinstance(candles, dict) else candles

        # Generate new chart with updated prices
        if candles_list:
            chart_bytes = generate_candles_chart(
                candles_list,
                pair,
                start_price=start,
                end_price=end,
                limit_price=limit,
                current_price=current_price,
                side=side,
            )

            # Send new photo with updated caption
            msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=chart_bytes,
                caption=config_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

            # Update stored message ID
            context.user_data["gs_wizard_message_id"] = msg.message_id
        else:
            # No chart - send text message
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=config_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            context.user_data["gs_wizard_message_id"] = msg.message_id

    except Exception as e:
        logger.error(f"Error updating prices message: {e}", exc_info=True)


async def _update_wizard_caption_only(
    update: Update, context: ContextTypes.DEFAULT_TYPE, warning_msg: str = None
) -> None:
    """
    Update only the caption of the chart message without regenerating the chart.

    Use this when changing fields that don't affect the visual representation
    (e.g., min_order_amount, take_profit, activation_bounds, etc.)
    """
    config = get_controller_config(context)
    message_id = context.user_data.get("gs_wizard_message_id")
    chat_id = context.user_data.get("gs_wizard_chat_id")

    if not message_id or not chat_id:
        return

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    side = config.get("side", SIDE_LONG)
    side_value = config.get("side", SIDE_LONG)
    side_str = "LONG" if side_value == SIDE_LONG else "SHORT"
    start = config.get("start_price", 0)
    end = config.get("end_price", 0)
    limit = config.get("limit_price", 0)
    current_price = context.user_data.get("gs_current_price", 0)
    interval = context.user_data.get("gs_chart_interval", "5m")
    total_amount = config.get("total_amount_quote", 1000)
    min_spread = config.get("min_spread_between_orders", 0.0001)
    take_profit = config.get("triple_barrier_config", {}).get("take_profit", 0.0001)
    min_order_amount = config.get("min_order_amount_quote", 6)
    natr = context.user_data.get("gs_natr")
    trading_rules = context.user_data.get("gs_trading_rules", {})

    # Get config values
    max_open_orders = config.get("max_open_orders", 3)
    leverage = config.get("leverage", 1)
    position_mode = config.get("position_mode", "ONEWAY")
    coerce_tp_to_step = config.get("coerce_tp_to_step", False)
    activation_bounds = config.get("activation_bounds", 0.01)

    # Regenerate theoretical grid with updated parameters
    grid = generate_theoretical_grid(
        start_price=start,
        end_price=end,
        min_spread=min_spread,
        total_amount=total_amount,
        min_order_amount=min_order_amount,
        current_price=current_price,
        side=side,
        trading_rules=trading_rules,
    )
    context.user_data["gs_theoretical_grid"] = grid

    # Build interval buttons with current one highlighted
    interval_options = ["1m", "5m", "15m", "1h", "4h"]
    interval_row = []
    for opt in interval_options:
        label = f"✓ {opt}" if opt == interval else opt
        interval_row.append(
            InlineKeyboardButton(label, callback_data=f"bots:gs_interval:{opt}")
        )

    keyboard = [
        interval_row,
        [
            InlineKeyboardButton("💾 Save Config", callback_data="bots:gs_save"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")],
    ]

    # Grid analysis info
    grid_valid = "✓" if grid.get("valid") else "⚠️"
    natr_pct = f"{natr*100:.2f}%" if natr else "N/A"
    range_pct = f"{grid.get('grid_range_pct', 0):.2f}%"

    # Build config text with individually copyable key=value params
    config_text = (
        f"*{escape_markdown_v2(pair)}* {side_str}\n"
        f"Price: `{current_price:,.6g}` \\| Range: `{range_pct}` \\| NATR: `{natr_pct}`\n\n"
        f"`connector_name={connector}`\n"
        f"`trading_pair={pair}`\n"
        f"`total_amount_quote={total_amount:.0f}`\n"
        f"`start_price={start:.6g}`\n"
        f"`end_price={end:.6g}`\n"
        f"`limit_price={limit:.6g}`\n"
        f"`leverage={leverage}`\n"
        f"`position_mode={position_mode}`\n"
        f"`take_profit={take_profit}`\n"
        f"`coerce_tp_to_step={str(coerce_tp_to_step).lower()}`\n"
        f"`min_spread_between_orders={min_spread}`\n"
        f"`min_order_amount_quote={min_order_amount:.0f}`\n"
        f"`max_open_orders={max_open_orders}`\n"
        f"`activation_bounds={activation_bounds}`\n\n"
        f"{grid_valid} Grid: `{grid['num_levels']}` levels "
        f"\\(↓{grid.get('levels_below_current', 0)} ↑{grid.get('levels_above_current', 0)}\\) "
        f"@ `${grid['amount_per_level']:.2f}`/lvl \\| step: `{grid.get('spread_pct', 0):.3f}%`"
    )

    # Add warnings if any
    if grid.get("warnings"):
        warnings_text = "\n".join(
            f"⚠️ {escape_markdown_v2(w)}" for w in grid["warnings"]
        )
        config_text += f"\n{warnings_text}"

    # Add user warning message if provided
    if warning_msg:
        config_text += f"\n\n⚠️ {escape_markdown_v2(warning_msg)}"

    config_text += "\n\n_Edit: `field=value`_"

    try:
        # Try to edit the caption of the existing photo message
        await context.bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=config_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        # If editing caption fails (e.g., message is text not photo), fall back to full update
        logger.warning(f"Caption edit failed, falling back to full update: {e}")
        await _update_wizard_message_for_prices_after_edit(update, context)


async def handle_gs_edit_price(
    update: Update, context: ContextTypes.DEFAULT_TYPE, price_type: str
) -> None:
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

    keyboard = [
        [InlineKeyboardButton("Cancel", callback_data="bots:gs_back_to_prices")]
    ]

    await query.message.edit_text(
        f"*Edit {escape_markdown_v2(label)}*" + "\n\n"
        f"Current: `{current:,.6g}`" + "\n\n"
        r"Enter new price:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
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
                chat_id=self.chat_id, message_id=self.message_id, text=text, **kwargs
            )

    fake_update = type(
        "FakeUpdate",
        (),
        {"callback_query": FakeQuery(context.bot, chat_id, message_id)},
    )()
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
            value = config.get("triple_barrier_config", {}).get(
                "open_order_type", ORDER_TYPE_LIMIT_MAKER
            )
        elif field_name == "take_profit_order_type":
            value = config.get("triple_barrier_config", {}).get(
                "take_profit_order_type", ORDER_TYPE_LIMIT_MAKER
            )
        else:
            value = config.get(field_name, "")

        formatted_value = format_config_field_value(field_name, value)
        required = "\\*" if field_info.get("required") else ""

        lines.append(
            f"*{escape_markdown_v2(label)}*{required}: `{escape_markdown_v2(formatted_value)}`"
        )

    lines.append("")
    lines.append(r"_Tap a button to edit a field\. \* \= required_")

    # Build keyboard with field buttons
    keyboard = []

    # Row 1: ID and Connector
    keyboard.append(
        [
            InlineKeyboardButton("ID", callback_data="bots:set_field:id"),
            InlineKeyboardButton(
                "Connector", callback_data="bots:set_field:connector_name"
            ),
            InlineKeyboardButton("Pair", callback_data="bots:set_field:trading_pair"),
        ]
    )

    # Row 2: Side, Leverage, Position Mode
    keyboard.append(
        [
            InlineKeyboardButton("Side", callback_data="bots:toggle_side"),
            InlineKeyboardButton("Leverage", callback_data="bots:set_field:leverage"),
            InlineKeyboardButton("Pos Mode", callback_data="bots:toggle_position_mode"),
        ]
    )

    # Row 3: Amount and Prices
    keyboard.append(
        [
            InlineKeyboardButton(
                "Amount", callback_data="bots:set_field:total_amount_quote"
            ),
            InlineKeyboardButton("Start", callback_data="bots:set_field:start_price"),
            InlineKeyboardButton("End", callback_data="bots:set_field:end_price"),
        ]
    )

    # Row 4: Limit Price and Order Settings
    keyboard.append(
        [
            InlineKeyboardButton("Limit", callback_data="bots:set_field:limit_price"),
            InlineKeyboardButton(
                "Max Orders", callback_data="bots:set_field:max_open_orders"
            ),
            InlineKeyboardButton(
                "Min Spread", callback_data="bots:set_field:min_spread_between_orders"
            ),
        ]
    )

    # Row 5: Take Profit and Order Types
    keyboard.append(
        [
            InlineKeyboardButton(
                "Take Profit", callback_data="bots:set_field:take_profit"
            ),
            InlineKeyboardButton(
                "Open Type", callback_data="bots:cycle_order_type:open"
            ),
            InlineKeyboardButton("TP Type", callback_data="bots:cycle_order_type:tp"),
        ]
    )

    # Row 6: Actions
    keyboard.append(
        [
            InlineKeyboardButton("Save Config", callback_data="bots:save_config"),
            InlineKeyboardButton("Cancel", callback_data="bots:controller_configs"),
        ]
    )

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        "\n".join(lines), parse_mode="MarkdownV2", reply_markup=reply_markup
    )


# ============================================
# FIELD EDITING
# ============================================


async def handle_set_field(
    update: Update, context: ContextTypes.DEFAULT_TYPE, field_name: str
) -> None:
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
        message, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


# ============================================
# CONNECTOR SELECTOR
# ============================================


async def show_connector_selector(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show connector selection keyboard with available CEX connectors"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, server_name = await get_bots_client(chat_id, context.user_data)

        # Get available CEX connectors (with cache)
        cex_connectors = await get_available_cex_connectors(
            context.user_data, client, server_name=server_name
        )

        if not cex_connectors:
            await query.answer("No CEX connectors configured", show_alert=True)
            return

        # Build connector buttons (2 per row)
        keyboard = []
        row = []

        for connector in cex_connectors:
            row.append(
                InlineKeyboardButton(
                    connector, callback_data=f"bots:select_connector:{connector}"
                )
            )
            if len(row) == 2:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)

        keyboard.append(
            [InlineKeyboardButton("Cancel", callback_data="bots:edit_config_back")]
        )
        reply_markup = InlineKeyboardMarkup(keyboard)

        config = get_controller_config(context)
        current = config.get("connector_name", "") or "Not set"

        await query.message.edit_text(
            r"*Select Connector*" + "\n\n"
            f"Current: `{escape_markdown_v2(current)}`\n\n"
            r"Choose an exchange from your configured connectors:",
            parse_mode="MarkdownV2",
            reply_markup=reply_markup,
        )

    except Exception as e:
        logger.error(f"Error showing connector selector: {e}", exc_info=True)
        await query.answer(f"Error: {str(e)[:50]}", show_alert=True)


async def handle_select_connector(
    update: Update, context: ContextTypes.DEFAULT_TYPE, connector_name: str
) -> None:
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


async def fetch_and_apply_market_data(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Fetch current price and candles, apply auto-pricing, show chart"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    connector = config.get("connector_name")
    pair = config.get("trading_pair")
    side = config.get("side", SIDE_LONG)

    if not connector or not pair:
        await show_config_form(update, context)
        return

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        # Show loading message
        await query.message.edit_text(
            f"Fetching market data for *{escape_markdown_v2(pair)}*\\.\\.\\.",
            parse_mode="MarkdownV2",
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
            config["id"] = generate_config_id(
                connector, pair, existing_configs=existing_configs
            )

            set_controller_config(context, config)

            # Fetch candles for chart
            candles = await fetch_candles(
                client, connector, pair, interval="5m", max_records=420
            )

            if candles:
                # Generate and send chart
                chart_bytes = generate_candles_chart(
                    candles,
                    pair,
                    start_price=start,
                    end_price=end,
                    limit_price=limit,
                    current_price=current_price,
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
                    parse_mode="MarkdownV2",
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
                    parse_mode="MarkdownV2",
                )
        else:
            await query.message.reply_text(
                f"Could not fetch price for {pair}. Please set prices manually.",
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Error fetching market data: {e}", exc_info=True)
        await query.message.reply_text(
            f"Error fetching market data: {str(e)[:100]}", parse_mode="HTML"
        )

    # Show the config form
    keyboard = [
        [
            InlineKeyboardButton(
                "Continue Editing", callback_data="bots:edit_config_back"
            )
        ]
    ]
    await query.message.reply_text(
        "Tap to continue editing configuration\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_toggle_side(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
                existing_configs=existing_configs,
            )

    set_controller_config(context, config)

    # Refresh the form
    await show_config_form(update, context)


async def handle_toggle_position_mode(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Toggle the position mode between HEDGE and ONEWAY"""
    query = update.callback_query
    config = get_controller_config(context)

    current_mode = config.get("position_mode", "ONEWAY")
    new_mode = "ONEWAY" if current_mode == "HEDGE" else "HEDGE"
    config["position_mode"] = new_mode

    set_controller_config(context, config)

    # Refresh the form
    await show_config_form(update, context)


async def handle_cycle_order_type(
    update: Update, context: ContextTypes.DEFAULT_TYPE, order_type_key: str
) -> None:
    """Cycle the order type between Market, Limit, and Limit Maker

    Args:
        update: Telegram update
        context: Telegram context
        order_type_key: 'open' for open_order_type, 'tp' for take_profit_order_type
    """
    query = update.callback_query
    config = get_controller_config(context)

    # Determine which field to update
    field_name = (
        "open_order_type" if order_type_key == "open" else "take_profit_order_type"
    )

    # Get current value
    if "triple_barrier_config" not in config:
        config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
            "triple_barrier_config"
        ].copy()

    current_type = config["triple_barrier_config"].get(
        field_name, ORDER_TYPE_LIMIT_MAKER
    )

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


async def process_field_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str
) -> None:
    """Process user input for a field

    Args:
        update: Telegram update
        context: Telegram context
        user_input: The text the user entered
    """
    chat_id = update.effective_chat.id
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
                config["triple_barrier_config"] = GRID_STRIKE_DEFAULTS[
                    "triple_barrier_config"
                ].copy()
            config["triple_barrier_config"]["take_profit"] = value
        else:
            config[field_name] = value

        set_controller_config(context, config)

        # Clear field editing state
        context.user_data.pop("editing_controller_field", None)
        context.user_data["bots_state"] = "editing_config"

        # Show success
        await update.message.reply_text(f"{label} set to: {value}", parse_mode="HTML")

        # If trading_pair was set and we have a connector, fetch market data
        if field_name == "trading_pair" and config.get("connector_name"):
            # Create a fake callback query context for fetch_and_apply_market_data
            keyboard = [
                [
                    InlineKeyboardButton(
                        "Fetching market data...", callback_data="bots:noop"
                    )
                ]
            ]
            msg = await update.message.reply_text(
                "Fetching market data\\.\\.\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

            try:
                client, _ = await get_bots_client(chat_id, context.user_data)
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
                    existing_configs = context.user_data.get(
                        "controller_configs_list", []
                    )
                    config["id"] = generate_config_id(
                        connector, pair, existing_configs=existing_configs
                    )
                    set_controller_config(context, config)

                    # Fetch candles
                    candles = await fetch_candles(
                        client, connector, pair, interval="5m", max_records=420
                    )

                    if candles:
                        chart_bytes = generate_candles_chart(
                            candles,
                            pair,
                            start_price=start,
                            end_price=end,
                            limit_price=limit,
                            current_price=current_price,
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
                            parse_mode="MarkdownV2",
                        )
                    else:
                        await update.message.reply_text(
                            f"*{escape_markdown_v2(pair)}* prices auto\\-calculated\\.\n\n"
                            f"Current: `{current_price:,.4f}`",
                            parse_mode="MarkdownV2",
                        )
                else:
                    await update.message.reply_text(
                        f"Could not fetch price for {pair}. Set prices manually."
                    )

            except Exception as e:
                logger.error(f"Error fetching market data: {e}", exc_info=True)
                await update.message.reply_text(
                    f"Error fetching market data: {str(e)[:50]}"
                )

        # Show the form again
        keyboard = [
            [
                InlineKeyboardButton(
                    "Continue Editing", callback_data="bots:edit_config_back"
                )
            ]
        ]
        await update.message.reply_text(
            "Tap to continue editing configuration\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except ValueError as e:
        await update.message.reply_text(
            f"Invalid value for {label}. Expected {field_type}. Please try again."
        )


# ============================================
# SAVE CONFIG
# ============================================


async def handle_save_config(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Save the current config to the backend"""
    query = update.callback_query
    chat_id = update.effective_chat.id
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
        client, _ = await get_bots_client(chat_id, context.user_data)

        # Save to backend using config id as the config_name
        config_name = config.get("id", "")
        result = await client.controllers.create_or_update_controller_config(
            config_name, config
        )

        # Clear state
        clear_bots_state(context)

        keyboard = [
            [
                InlineKeyboardButton(
                    "Create Another", callback_data="bots:new_grid_strike"
                )
            ],
            [
                InlineKeyboardButton(
                    "Back to Configs", callback_data="bots:controller_configs"
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        config_id = config.get("id", "unknown")
        await query.message.edit_text(
            f"*Config Saved\\!*\n\n"
            f"Controller `{escape_markdown_v2(config_id)}` has been saved successfully\\.",
            parse_mode="MarkdownV2",
            reply_markup=reply_markup,
        )

    except Exception as e:
        logger.error(f"Error saving config: {e}", exc_info=True)
        await query.answer(f"Failed to save: {str(e)[:100]}", show_alert=True)


# ============================================
# EDIT EXISTING CONFIG
# ============================================


async def handle_edit_config(
    update: Update, context: ContextTypes.DEFAULT_TYPE, config_index: int
) -> None:
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
    chat_id = update.effective_chat.id

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()

        if not configs:
            keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
            await query.message.edit_text(
                r"*Deploy Controllers*" + "\n\n"
                r"No configurations available to deploy\." + "\n"
                r"Create a controller config first\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
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

            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"{checkbox} {config_id[:25]}",
                        callback_data=f"bots:toggle_deploy:{i}",
                    )
                ]
            )

        # Action buttons
        keyboard.append(
            [
                InlineKeyboardButton("Select All", callback_data="bots:select_all"),
                InlineKeyboardButton("Clear All", callback_data="bots:clear_all"),
            ]
        )

        if selected:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"Next: Configure ({len(selected)})",
                        callback_data="bots:deploy_configure",
                    ),
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton("Back", callback_data="bots:main_menu"),
            ]
        )

        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await query.message.edit_text(
                "\n".join(lines), parse_mode="MarkdownV2", reply_markup=reply_markup
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
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_toggle_deploy_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE, index: int
) -> None:
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


async def show_deploy_configure(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
    lines.append(
        f"*Max Controller DD*: `{max_controller if max_controller else 'Not set'}`"
    )
    lines.append(f"*Image*: `{escape_markdown_v2(image)}`")
    lines.append("")
    lines.append(r"_\* \= required_")

    # Build keyboard
    keyboard = [
        [
            InlineKeyboardButton(
                "Instance Name", callback_data="bots:deploy_set:instance_name"
            ),
            InlineKeyboardButton(
                "Credentials", callback_data="bots:deploy_set:credentials_profile"
            ),
        ],
        [
            InlineKeyboardButton(
                "Max Global DD",
                callback_data="bots:deploy_set:max_global_drawdown_quote",
            ),
            InlineKeyboardButton(
                "Max Controller DD",
                callback_data="bots:deploy_set:max_controller_drawdown_quote",
            ),
        ],
        [
            InlineKeyboardButton("Image", callback_data="bots:deploy_set:image"),
        ],
    ]

    # Check if ready to deploy
    can_deploy = bool(
        deploy_params.get("instance_name") and deploy_params.get("credentials_profile")
    )

    if can_deploy:
        keyboard.append(
            [
                InlineKeyboardButton("Deploy Now", callback_data="bots:execute_deploy"),
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton("Back to Selection", callback_data="bots:deploy_menu"),
        ]
    )

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        "\n".join(lines), parse_mode="MarkdownV2", reply_markup=reply_markup
    )


# ============================================
# PROGRESSIVE DEPLOY CONFIGURATION FLOW
# ============================================


async def show_deploy_progressive_form(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show the progressive deployment configuration form"""
    query = update.callback_query

    deploy_params = context.user_data.get("deploy_params", DEPLOY_DEFAULTS.copy())
    current_field = context.user_data.get("deploy_current_field", DEPLOY_FIELD_ORDER[0])

    message_text, reply_markup = _build_deploy_progressive_message(
        deploy_params, current_field, context
    )

    await query.message.edit_text(
        message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )
    await query.answer()


def _build_deploy_progressive_message(
    deploy_params: dict, current_field: str, context
) -> tuple:
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
            lines.append(
                f"➡️ *{escape_markdown_v2(label)}*{required}: _awaiting input_"
            )
        elif DEPLOY_FIELD_ORDER.index(field_name) < DEPLOY_FIELD_ORDER.index(
            current_field
        ):
            lines.append(
                f"✅ *{escape_markdown_v2(label)}*{required}: `{escape_markdown_v2(value_display)}`"
            )
        else:
            lines.append(
                f"⬜ *{escape_markdown_v2(label)}*{required}: `{escape_markdown_v2(value_display)}`"
            )

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
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"Use Default: {default_value[:20]}",
                    callback_data=f"bots:deploy_use_default:{current_field}",
                )
            ]
        )

    if not DEPLOY_FIELDS.get(current_field, {}).get("required"):
        keyboard.append(
            [
                InlineKeyboardButton(
                    "Skip (keep default)", callback_data="bots:deploy_skip_field"
                )
            ]
        )

    nav_buttons = []
    current_index = DEPLOY_FIELD_ORDER.index(current_field)
    if current_index > 0:
        nav_buttons.append(
            InlineKeyboardButton("« Back", callback_data="bots:deploy_prev_field")
        )
    nav_buttons.append(
        InlineKeyboardButton("❌ Cancel", callback_data="bots:deploy_menu")
    )
    keyboard.append(nav_buttons)

    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


async def handle_deploy_progressive_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
        error_msg = await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Invalid value. Please enter a valid {field_type}.",
        )
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

    message_text, reply_markup = _build_deploy_progressive_message(
        deploy_params, current_field, context
    )

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup,
        )
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

        lines.append(
            f"✅ *{escape_markdown_v2(label)}*{required}: `{escape_markdown_v2(value_display)}`"
        )

    context.user_data["deploy_params"] = deploy_params

    lines.append("")
    lines.append(r"_Ready to deploy\? Tap Deploy Now or edit any field\._")

    keyboard = []
    field_buttons = []
    for field_name in DEPLOY_FIELD_ORDER:
        label = DEPLOY_FIELDS[field_name]["label"]
        field_buttons.append(
            InlineKeyboardButton(
                f"✏️ {label[:15]}", callback_data=f"bots:deploy_edit:{field_name}"
            )
        )

    for i in range(0, len(field_buttons), 2):
        keyboard.append(field_buttons[i : i + 2])

    keyboard.append(
        [InlineKeyboardButton("🚀 Deploy Now", callback_data="bots:execute_deploy")]
    )
    keyboard.append(
        [InlineKeyboardButton("« Back to Selection", callback_data="bots:deploy_menu")]
    )

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text="\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        logger.error(f"Error showing deploy summary: {e}")


async def handle_deploy_use_default(
    update: Update, context: ContextTypes.DEFAULT_TYPE, field_name: str
) -> None:
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


async def handle_deploy_skip_field(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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


async def handle_deploy_prev_field(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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


async def handle_deploy_edit_field(
    update: Update, context: ContextTypes.DEFAULT_TYPE, field_name: str
) -> None:
    """Edit a specific field from the summary view"""
    query = update.callback_query
    context.user_data["deploy_current_field"] = field_name
    context.user_data["bots_state"] = "deploy_progressive"
    await show_deploy_progressive_form(update, context)


async def handle_deploy_set_field(
    update: Update, context: ContextTypes.DEFAULT_TYPE, field_name: str
) -> None:
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
        message, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def process_deploy_field_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str
) -> None:
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
        keyboard = [
            [InlineKeyboardButton("Continue", callback_data="bots:deploy_form_back")]
        ]
        await update.message.reply_text(
            "Value updated\\. Tap to continue\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except ValueError as e:
        await update.message.reply_text(f"Invalid value. Please enter a valid number.")


async def handle_execute_deploy(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Execute the deployment of selected controllers"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    deploy_params = context.user_data.get("deploy_params", {})

    instance_name = deploy_params.get("instance_name")
    credentials_profile = deploy_params.get("credentials_profile")
    controllers_config = deploy_params.get("controllers_config", [])

    if not instance_name or not credentials_profile:
        await query.answer(
            "Instance name and credentials are required", show_alert=True
        )
        return

    if not controllers_config:
        await query.answer("No controllers selected", show_alert=True)
        return

    # Show deploying message FIRST (before the long operation)
    controllers_str = ", ".join(
        [f"`{escape_markdown_v2(c)}`" for c in controllers_config]
    )
    await query.message.edit_text(
        f"*Deploying\\.\\.\\.*\n\n"
        f"*Instance:* `{escape_markdown_v2(instance_name)}`\n"
        f"*Controllers:*\n{controllers_str}\n\n"
        f"Please wait, this may take a moment\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        # Deploy using deploy_v2_controllers (this can take time)
        result = await client.bot_orchestration.deploy_v2_controllers(
            instance_name=instance_name,
            credentials_profile=credentials_profile,
            controllers_config=controllers_config,
            max_global_drawdown_quote=deploy_params.get("max_global_drawdown_quote"),
            max_controller_drawdown_quote=deploy_params.get(
                "max_controller_drawdown_quote"
            ),
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
            status == "success"
            or "successfully" in message.lower()
            or "created" in message.lower()
        )

        if is_success:
            await query.message.edit_text(
                f"*Deployment Started\\!*\n\n"
                f"*Instance:* `{escape_markdown_v2(instance_name)}`\n"
                f"*Controllers:*\n{controllers_str}\n\n"
                f"The bot is being deployed\\. Check status in Bots menu\\.",
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
        else:
            error_msg = message or "Unknown error"
            await query.message.edit_text(
                f"*Deployment Failed*\n\n" f"Error: {escape_markdown_v2(error_msg)}",
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )

    except Exception as e:
        logger.error(f"Error deploying controllers: {e}", exc_info=True)
        # Use message edit instead of query.answer (which may have expired)
        keyboard = [
            [InlineKeyboardButton("Try Again", callback_data="bots:execute_deploy")],
            [InlineKeyboardButton("Back", callback_data="bots:deploy_form_back")],
        ]
        await query.message.edit_text(
            f"*Deployment Failed*\n\n" f"Error: {escape_markdown_v2(str(e)[:200])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
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


async def show_deploy_config_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
        for i in selected
        if i < len(configs)
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
        r"*Configuration*",
        "",
        f"  📝  *Name:*      `{escape_markdown_v2(instance_name)}`",
        f"  👤  *Account:*   `{escape_markdown_v2(creds)}`",
        f"  🐳  *Image:*     `{escape_markdown_v2(image_short)}`",
        "",
        r"_Tap buttons below to change settings_",
    ]

    # Build keyboard - one button per row for better readability
    keyboard = [
        [
            InlineKeyboardButton(
                f"📝 Name: {instance_name[:25]}", callback_data="bots:select_name:_show"
            )
        ],
        [
            InlineKeyboardButton(
                f"👤 Account: {creds}", callback_data="bots:select_creds:_show"
            )
        ],
        [
            InlineKeyboardButton(
                f"🐳 Image: {image_short}", callback_data="bots:select_image:_show"
            )
        ],
        [InlineKeyboardButton("✅ Deploy Now", callback_data="bots:execute_deploy")],
        [InlineKeyboardButton("« Back", callback_data="bots:deploy_menu")],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Set drawdowns to None (skip them)
    deploy_params["max_global_drawdown_quote"] = None
    deploy_params["max_controller_drawdown_quote"] = None
    context.user_data["deploy_params"] = deploy_params

    await query.message.edit_text(
        "\n".join(lines), parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_select_credentials(
    update: Update, context: ContextTypes.DEFAULT_TYPE, creds: str
) -> None:
    """Handle credentials profile selection"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    if creds == "_show":
        # Show available credentials profiles
        try:
            client, _ = await get_bots_client(chat_id, context.user_data)
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
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"{marker}{acc}", callback_data=f"bots:select_creds:{acc}"
                    )
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton("« Back", callback_data="bots:deploy_config"),
            ]
        )

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        # Set the selected credential profile
        deploy_params = context.user_data.get("deploy_params", {})
        deploy_params["credentials_profile"] = creds
        context.user_data["deploy_params"] = deploy_params

        await query.answer(f"Account set to {creds}")
        await show_deploy_config_step(update, context)


async def handle_select_image(
    update: Update, context: ContextTypes.DEFAULT_TYPE, image: str
) -> None:
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
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"{marker}{img_short}", callback_data=f"bots:select_image:{img}"
                    )
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton("« Back", callback_data="bots:deploy_config"),
            ]
        )

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        # Set the selected image
        deploy_params = context.user_data.get("deploy_params", {})
        deploy_params["image"] = image
        context.user_data["deploy_params"] = deploy_params

        img_short = image.split("/")[-1] if "/" in image else image
        await query.answer(f"Image set to {img_short}")
        await show_deploy_config_step(update, context)


async def handle_select_instance_name(
    update: Update, context: ContextTypes.DEFAULT_TYPE, name: str
) -> None:
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
            [
                InlineKeyboardButton(
                    f"✓ Use: {creds}", callback_data=f"bots:select_name:{creds}"
                )
            ],
            [InlineKeyboardButton("« Back", callback_data="bots:deploy_config")],
        ]

        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
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


async def process_instance_name_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str
) -> None:
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
            [
                InlineKeyboardButton(
                    f"📝 Name: {custom_name[:25]}",
                    callback_data="bots:select_name:_show",
                )
            ],
            [
                InlineKeyboardButton(
                    f"👤 Account: {creds}", callback_data="bots:select_creds:_show"
                )
            ],
            [
                InlineKeyboardButton(
                    f"🐳 Image: {image_short}", callback_data="bots:select_image:_show"
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ Deploy Now", callback_data="bots:execute_deploy"
                )
            ],
            [InlineKeyboardButton("« Back", callback_data="bots:deploy_menu")],
        ]

        try:
            await update.get_bot().edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="\n".join(lines),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception as e:
            logger.error(f"Error updating deploy config message: {e}")


async def handle_deploy_confirm(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
            InlineKeyboardButton(
                f"✅ Deploy as {generated_name[:25]}",
                callback_data="bots:execute_deploy",
            ),
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
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_deploy_custom_name(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle custom instance name input (called from message handler)"""
    # This is triggered via message handler when in deploy_custom_name state
    pass  # The actual processing happens in process_deploy_custom_name_input


async def process_deploy_custom_name_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str
) -> None:
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
            parse_mode="MarkdownV2",
        )
    except Exception as e:
        logger.error(f"Error updating deploy message: {e}")

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

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
            status == "success"
            or "successfully" in message.lower()
            or "created" in message.lower()
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
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            error_msg = message or "Unknown error"
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=(
                    f"*Deployment Failed*\n\n" f"Error: {escape_markdown_v2(error_msg)}"
                ),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
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
                f"*Deployment Failed*\n\n" f"Error: {escape_markdown_v2(str(e)[:200])}"
            ),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


# ============================================
# PMM MISTER WIZARD
# ============================================

from .controllers.pmm_mister import generate_id as pmm_generate_id
from .controllers.pmm_mister import validate_config as pmm_validate_config


async def show_new_pmm_mister_form(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Start the progressive PMM Mister wizard - Step 1: Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()
        context.user_data["controller_configs_list"] = configs
    except Exception as e:
        logger.warning(f"Could not fetch existing configs: {e}")

    config = init_new_controller_config(context, "pmm_mister")
    context.user_data["bots_state"] = "pmm_wizard"
    context.user_data["pmm_wizard_step"] = "connector_name"
    context.user_data["pmm_wizard_message_id"] = query.message.message_id
    context.user_data["pmm_wizard_chat_id"] = query.message.chat_id

    await _show_pmm_wizard_connector_step(update, context)


async def _show_pmm_wizard_connector_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """PMM Wizard Step 1: Select Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, server_name = await get_bots_client(chat_id, context.user_data)
        cex_connectors = await get_available_cex_connectors(
            context.user_data, client, server_name=server_name
        )

        if not cex_connectors:
            keyboard = [
                [
                    InlineKeyboardButton(
                        "🔑 Configure API Keys", callback_data="config_api_keys"
                    )
                ],
                [InlineKeyboardButton("« Back", callback_data="bots:main_menu")],
            ]
            await query.message.edit_text(
                r"*PMM Mister \- New Config*" + "\n\n"
                r"⚠️ No CEX connectors available\." + "\n\n"
                r"You need to connect API keys for an exchange to deploy strategies\."
                + "\n"
                r"Click below to configure your API keys\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        keyboard = []
        row = []
        for connector in cex_connectors:
            row.append(
                InlineKeyboardButton(
                    f"🏦 {connector}", callback_data=f"bots:pmm_connector:{connector}"
                )
            )
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append(
            [InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")]
        )

        await query.message.edit_text(
            r"*📈 PMM Mister \- New Config*" + "\n\n"
            r"*Step 1/8:* 🏦 Select Connector",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"Error in PMM connector step: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        await query.message.edit_text(
            format_error_message(f"Error: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_pmm_wizard_connector(
    update: Update, context: ContextTypes.DEFAULT_TYPE, connector: str
) -> None:
    """Handle connector selection"""
    config = get_controller_config(context)
    config["connector_name"] = connector
    set_controller_config(context, config)
    context.user_data["pmm_wizard_step"] = "trading_pair"
    await _show_pmm_wizard_pair_step(update, context)


async def _show_pmm_wizard_pair_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """PMM Wizard Step 2: Trading Pair"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = "trading_pair"

    existing_configs = context.user_data.get("controller_configs_list", [])
    recent_pairs = []
    seen = set()
    for cfg in reversed(existing_configs):
        pair = cfg.get("trading_pair", "")
        if pair and pair not in seen:
            seen.add(pair)
            recent_pairs.append(pair)
            if len(recent_pairs) >= 6:
                break

    keyboard = []
    if recent_pairs:
        row = []
        for pair in recent_pairs:
            row.append(
                InlineKeyboardButton(pair, callback_data=f"bots:pmm_pair:{pair}")
            )
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
    keyboard.append(
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_back:connector"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ]
    )

    await query.message.edit_text(
        r"*📈 PMM Mister \- New Config*" + "\n\n"
        f"*Connector:* `{escape_markdown_v2(connector)}`" + "\n\n"
        r"*Step 2/8:* 🔗 Trading Pair" + "\n\n"
        r"Select or type a pair:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_pmm_wizard_pair(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pair: str
) -> None:
    """Handle pair selection"""
    config = get_controller_config(context)
    config["trading_pair"] = pair.upper()
    set_controller_config(context, config)

    connector = config.get("connector_name", "")

    # Only ask for leverage on perpetual exchanges
    if connector.endswith("_perpetual"):
        context.user_data["pmm_wizard_step"] = "leverage"
        await _show_pmm_wizard_leverage_step(update, context)
    else:
        # Spot exchange - set leverage to 1 and skip to allocation
        config["leverage"] = 1
        set_controller_config(context, config)
        context.user_data["pmm_wizard_step"] = "portfolio_allocation"
        await _show_pmm_wizard_allocation_step(update, context)


async def _show_pmm_wizard_leverage_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """PMM Wizard Step 3: Leverage"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    keyboard = [
        [
            InlineKeyboardButton("1x", callback_data="bots:pmm_leverage:1"),
            InlineKeyboardButton("5x", callback_data="bots:pmm_leverage:5"),
            InlineKeyboardButton("10x", callback_data="bots:pmm_leverage:10"),
        ],
        [
            InlineKeyboardButton("20x", callback_data="bots:pmm_leverage:20"),
            InlineKeyboardButton("50x", callback_data="bots:pmm_leverage:50"),
            InlineKeyboardButton("75x", callback_data="bots:pmm_leverage:75"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_back:pair"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await query.message.edit_text(
        r"*📈 PMM Mister \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n\n"
        r"*Step 3/8:* ⚡ Leverage",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_pmm_wizard_leverage(
    update: Update, context: ContextTypes.DEFAULT_TYPE, leverage: int
) -> None:
    """Handle leverage selection"""
    config = get_controller_config(context)
    config["leverage"] = leverage
    set_controller_config(context, config)
    context.user_data["pmm_wizard_step"] = "portfolio_allocation"
    await _show_pmm_wizard_allocation_step(update, context)


async def _show_pmm_wizard_allocation_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """PMM Wizard Step 4: Portfolio Allocation"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 20)

    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = "portfolio_allocation"

    # Back goes to leverage for perpetual, or pair for spot
    back_target = "leverage" if connector.endswith("_perpetual") else "pair"

    keyboard = [
        [
            InlineKeyboardButton("1%", callback_data="bots:pmm_alloc:0.01"),
            InlineKeyboardButton("2%", callback_data="bots:pmm_alloc:0.02"),
            InlineKeyboardButton("3%", callback_data="bots:pmm_alloc:0.03"),
        ],
        [
            InlineKeyboardButton("5%", callback_data="bots:pmm_alloc:0.05"),
            InlineKeyboardButton("10%", callback_data="bots:pmm_alloc:0.1"),
            InlineKeyboardButton("20%", callback_data="bots:pmm_alloc:0.2"),
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back", callback_data=f"bots:pmm_back:{back_target}"
            ),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await query.message.edit_text(
        r"*📈 PMM Mister \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n"
        f"⚡ `{leverage}x`" + "\n\n"
        r"*Step 4/8:* 💰 Portfolio Allocation" + "\n\n"
        r"_Or type a custom value \(e\.g\. 3% or 0\.03\)_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_pmm_wizard_allocation(
    update: Update, context: ContextTypes.DEFAULT_TYPE, allocation: float
) -> None:
    """Handle allocation selection"""
    config = get_controller_config(context)
    config["portfolio_allocation"] = allocation
    set_controller_config(context, config)
    context.user_data["pmm_wizard_step"] = "total_amount_quote"
    await _show_pmm_wizard_amount_step(update, context)


async def _show_pmm_wizard_amount_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """PMM Wizard Step 5: Total Amount Quote"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 20)
    allocation = config.get("portfolio_allocation", 0.05)

    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = "total_amount_quote"

    # Extract base and quote tokens from pair
    base_token, quote_token = "", ""
    if "-" in pair:
        base_token, quote_token = pair.split("-", 1)

    # Fetch balances for the connector
    balance_text = ""
    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        balances = await get_cex_balances(
            context.user_data, client, "master_account", ttl=30
        )

        # Try to find connector balances with flexible matching
        connector_balances = []
        connector_lower = connector.lower()
        connector_base = connector_lower.replace("_perpetual", "").replace("_spot", "")

        for bal_connector, bal_list in balances.items():
            bal_lower = bal_connector.lower()
            bal_base = bal_lower.replace("_perpetual", "").replace("_spot", "")
            if bal_lower == connector_lower or bal_base == connector_base:
                connector_balances = bal_list
                break

        if connector_balances:
            relevant_balances = []
            for bal in connector_balances:
                token = bal.get("token", bal.get("asset", ""))
                available = bal.get(
                    "units", bal.get("available_balance", bal.get("free", 0))
                )
                value_usd = bal.get("value", 0)
                if token and available:
                    try:
                        available_float = float(available)
                        if available_float > 0:
                            if token.upper() in [
                                quote_token.upper(),
                                base_token.upper(),
                            ]:
                                relevant_balances.append(
                                    (
                                        token,
                                        available_float,
                                        float(value_usd) if value_usd else None,
                                    )
                                )
                    except (ValueError, TypeError):
                        continue

            if relevant_balances:
                bal_lines = []
                for token, available, value_usd in relevant_balances:
                    if available >= 1000:
                        amt_str = f"{available:,.0f}"
                    elif available >= 1:
                        amt_str = f"{available:,.2f}"
                    else:
                        amt_str = f"{available:,.6f}"

                    if value_usd and value_usd >= 1:
                        bal_lines.append(f"{token}: {amt_str} (${value_usd:,.0f})")
                    else:
                        bal_lines.append(f"{token}: {amt_str}")
                balance_text = (
                    "💼 *Available:* "
                    + " \\| ".join(escape_markdown_v2(b) for b in bal_lines)
                    + "\n\n"
                )
            else:
                balance_text = f"_No {escape_markdown_v2(quote_token)} balance on {escape_markdown_v2(connector)}_\n\n"
        elif balances:
            balance_text = f"_No {escape_markdown_v2(quote_token)} balance found_\n\n"
    except Exception as e:
        logger.warning(f"Could not fetch balances for PMM amount step: {e}")

    keyboard = [
        [
            InlineKeyboardButton("💵 100", callback_data="bots:pmm_amount:100"),
            InlineKeyboardButton("💵 500", callback_data="bots:pmm_amount:500"),
            InlineKeyboardButton("💵 1000", callback_data="bots:pmm_amount:1000"),
        ],
        [
            InlineKeyboardButton("💰 2000", callback_data="bots:pmm_amount:2000"),
            InlineKeyboardButton("💰 5000", callback_data="bots:pmm_amount:5000"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_back:allocation"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    message_text = (
        r"*📈 PMM Mister \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n"
        f"⚡ `{leverage}x` \\| 💰 `{allocation*100:.1f}%`"
        + "\n\n"
        + balance_text
        + r"*Step 5/8:* 💵 Total Amount \(Quote\)"
        + "\n\n"
        r"Select or type amount:"
    )

    try:
        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        pass


async def handle_pmm_wizard_amount(
    update: Update, context: ContextTypes.DEFAULT_TYPE, amount: float
) -> None:
    """Handle amount selection in PMM wizard"""
    config = get_controller_config(context)
    config["total_amount_quote"] = amount
    set_controller_config(context, config)
    context.user_data["pmm_wizard_step"] = "spreads"
    await _show_pmm_wizard_spreads_step(update, context)


async def _show_pmm_wizard_spreads_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """PMM Wizard Step 6: Spreads"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 20)
    allocation = config.get("portfolio_allocation", 0.05)

    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = "spreads"

    amount = config.get("total_amount_quote", 100)

    keyboard = [
        [
            InlineKeyboardButton(
                "Tight: 0.02%, 0.1%", callback_data="bots:pmm_spreads:0.0002,0.001"
            )
        ],
        [
            InlineKeyboardButton(
                "Normal: 0.5%, 1%", callback_data="bots:pmm_spreads:0.005,0.01"
            )
        ],
        [
            InlineKeyboardButton(
                "Wide: 1%, 2%", callback_data="bots:pmm_spreads:0.01,0.02"
            )
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_back:amount"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await query.message.edit_text(
        r"*📈 PMM Mister \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n"
        f"⚡ `{leverage}x` \\| 💰 `{allocation*100:.0f}%` \\| 💵 `{amount:,.0f}`"
        + "\n\n"
        r"*Step 6/8:* 📊 Spreads" + "\n\n"
        r"_Or type custom: `0\.01,0\.02`_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _show_pmm_wizard_spreads_step_msg(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, config: dict
) -> None:
    """Show spreads step via direct message edit (for text input flow)"""
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 20)
    allocation = config.get("portfolio_allocation", 0.05)
    amount = config.get("total_amount_quote", 100)

    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = "spreads"

    keyboard = [
        [
            InlineKeyboardButton(
                "Tight: 0.02%, 0.1%", callback_data="bots:pmm_spreads:0.0002,0.001"
            )
        ],
        [
            InlineKeyboardButton(
                "Normal: 0.5%, 1%", callback_data="bots:pmm_spreads:0.005,0.01"
            )
        ],
        [
            InlineKeyboardButton(
                "Wide: 1%, 2%", callback_data="bots:pmm_spreads:0.01,0.02"
            )
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_back:amount"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=r"*📈 PMM Mister \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n"
        f"⚡ `{leverage}x` \\| 💰 `{allocation*100:.0f}%` \\| 💵 `{amount:,.0f}`"
        + "\n\n"
        r"*Step 6/8:* 📊 Spreads" + "\n\n"
        r"_Or type custom: `0\.01,0\.02`_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _show_pmm_wizard_amount_step_msg(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, config: dict
) -> None:
    """Show amount step via direct message edit (for text input flow)"""
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 20)
    allocation = config.get("portfolio_allocation", 0.05)

    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = "total_amount_quote"

    keyboard = [
        [
            InlineKeyboardButton("💵 100", callback_data="bots:pmm_amount:100"),
            InlineKeyboardButton("💵 500", callback_data="bots:pmm_amount:500"),
            InlineKeyboardButton("💵 1000", callback_data="bots:pmm_amount:1000"),
        ],
        [
            InlineKeyboardButton("💰 2000", callback_data="bots:pmm_amount:2000"),
            InlineKeyboardButton("💰 5000", callback_data="bots:pmm_amount:5000"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_back:allocation"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=r"*📈 PMM Mister \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n"
        f"⚡ `{leverage}x` \\| 💰 `{allocation*100:.1f}%`" + "\n\n"
        r"*Step 5/8:* 💵 Total Amount \(Quote\)" + "\n\n"
        r"Select or type amount:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_pmm_wizard_spreads(
    update: Update, context: ContextTypes.DEFAULT_TYPE, spreads: str
) -> None:
    """Handle spreads selection"""
    config = get_controller_config(context)
    config["buy_spreads"] = spreads
    config["sell_spreads"] = spreads
    set_controller_config(context, config)
    context.user_data["pmm_wizard_step"] = "take_profit"
    await _show_pmm_wizard_tp_step(update, context)


async def _show_pmm_wizard_tp_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """PMM Wizard Step 7: Take Profit"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 20)
    allocation = config.get("portfolio_allocation", 0.05)
    spreads = config.get("buy_spreads", "0.0002,0.001")

    keyboard = [
        [
            InlineKeyboardButton("0.01%", callback_data="bots:pmm_tp:0.0001"),
            InlineKeyboardButton("0.02%", callback_data="bots:pmm_tp:0.0002"),
            InlineKeyboardButton("0.05%", callback_data="bots:pmm_tp:0.0005"),
        ],
        [
            InlineKeyboardButton("0.1%", callback_data="bots:pmm_tp:0.001"),
            InlineKeyboardButton("0.2%", callback_data="bots:pmm_tp:0.002"),
            InlineKeyboardButton("0.5%", callback_data="bots:pmm_tp:0.005"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_back:spreads"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await query.message.edit_text(
        r"*📈 PMM Mister \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n"
        f"⚡ `{leverage}x` \\| 💰 `{allocation*100:.0f}%`" + "\n"
        f"📊 Spreads: `{escape_markdown_v2(spreads)}`" + "\n\n"
        r"*Step 7/8:* 🎯 Take Profit",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_pmm_wizard_tp(
    update: Update, context: ContextTypes.DEFAULT_TYPE, tp: float
) -> None:
    """Handle take profit selection"""
    config = get_controller_config(context)
    config["take_profit"] = tp
    set_controller_config(context, config)
    context.user_data["pmm_wizard_step"] = "review"
    await _show_pmm_wizard_review_step(update, context)


async def _show_pmm_wizard_review_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """PMM Wizard Step 7: Review with copyable config format"""
    query = update.callback_query
    config = get_controller_config(context)

    # Generate ID if not set
    if not config.get("id"):
        existing = context.user_data.get("controller_configs_list", [])
        config["id"] = pmm_generate_id(config, existing)
        set_controller_config(context, config)

    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = "review"

    # Format order types as string
    tp_order_type = config.get("take_profit_order_type", "LIMIT_MAKER")
    if isinstance(tp_order_type, int):
        tp_order_type_str = ORDER_TYPE_LABELS.get(tp_order_type, "LIMIT_MAKER")
    else:
        tp_order_type_str = str(tp_order_type)

    open_order_type = config.get("open_order_type", "LIMIT")
    if isinstance(open_order_type, int):
        open_order_type_str = ORDER_TYPE_LABELS.get(open_order_type, "LIMIT")
    else:
        open_order_type_str = str(open_order_type)

    # Get amounts
    buy_spreads = config.get("buy_spreads", "0.0002,0.0006")
    sell_spreads = config.get("sell_spreads", "0.0002,0.0006")
    buy_amounts = config.get("buy_amounts_pct", "1,1")
    sell_amounts = config.get("sell_amounts_pct", "1,1")

    if not buy_amounts:
        num_buy_spreads = len(buy_spreads.split(",")) if isinstance(buy_spreads, str) and buy_spreads else 1
        buy_amounts = ",".join(["1"] * num_buy_spreads)
    if not sell_amounts:
        num_sell_spreads = len(sell_spreads.split(",")) if isinstance(sell_spreads, str) and sell_spreads else 1
        sell_amounts = ",".join(["1"] * num_sell_spreads)

    # Build copyable config block
    config_block = (
        f"id: {config.get('id', '')}\n"
        f"connector_name: {config.get('connector_name', '')}\n"
        f"trading_pair: {config.get('trading_pair', '')}\n"
        f"leverage: {config.get('leverage', 1)}\n"
        f"position_mode: {config.get('position_mode', 'ONEWAY')}\n"
        f"total_amount_quote: {config.get('total_amount_quote', 1000)}\n"
        f"portfolio_allocation: {config.get('portfolio_allocation', 0.03)}\n"
        f"target_base_pct: {config.get('target_base_pct', 0.5)}\n"
        f"min_base_pct: {config.get('min_base_pct', 0.3)}\n"
        f"max_base_pct: {config.get('max_base_pct', 0.7)}\n"
        f"buy_spreads: {buy_spreads}\n"
        f"sell_spreads: {sell_spreads}\n"
        f"buy_amounts_pct: {buy_amounts}\n"
        f"sell_amounts_pct: {sell_amounts}\n"
        f"take_profit: {config.get('take_profit', 0.0003)}\n"
        f"take_profit_order_type: {tp_order_type_str}\n"
        f"open_order_type: {open_order_type_str}\n"
        f"executor_refresh_time: {config.get('executor_refresh_time', 10)}\n"
        f"buy_cooldown_time: {config.get('buy_cooldown_time', 10)}\n"
        f"sell_cooldown_time: {config.get('sell_cooldown_time', 10)}\n"
        f"buy_position_effectivization_time: {config.get('buy_position_effectivization_time', 300)}\n"
        f"sell_position_effectivization_time: {config.get('sell_position_effectivization_time', 300)}\n"
        f"price_distance_tolerance: {config.get('price_distance_tolerance', '0.0005')}\n"
        f"refresh_tolerance: {config.get('refresh_tolerance', '0.0005')}\n"
        f"tolerance_scaling: {config.get('tolerance_scaling', '1.2')}\n"
        f"max_active_executors_by_level: {config.get('max_active_executors_by_level', 10)}\n"
        f"position_profit_protection: {config.get('position_profit_protection', True)}\n"
        f"min_skew: {config.get('min_skew', '1.0')}\n"
        f"global_take_profit: {config.get('global_take_profit', '0.03')}\n"
        f"global_stop_loss: {config.get('global_stop_loss', '0.05')}"
    )

    pair = config.get("trading_pair", "")
    message_text = (
        f"*{escape_markdown_v2(pair)}* \\- Review Config\n\n"
        f"```\n{config_block}\n```\n\n"
        f"_To edit, send `field: value` lines_"
    )

    keyboard = [
        [InlineKeyboardButton("✅ Save Config", callback_data="bots:pmm_save")],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_back:tp"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_pmm_back(
    update: Update, context: ContextTypes.DEFAULT_TYPE, target: str
) -> None:
    """Handle back navigation in PMM wizard"""
    query = update.callback_query

    if target == "connector":
        await _show_pmm_wizard_connector_step(update, context)
    elif target == "pair":
        await _show_pmm_wizard_pair_step(update, context)
    elif target == "leverage":
        await _show_pmm_wizard_leverage_step(update, context)
    elif target == "allocation":
        await _show_pmm_wizard_allocation_step(update, context)
    elif target == "amount":
        await _show_pmm_wizard_amount_step(update, context)
    elif target == "spreads":
        await _show_pmm_wizard_spreads_step(update, context)
    elif target == "tp":
        await _show_pmm_wizard_tp_step(update, context)


async def handle_pmm_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save PMM config"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    is_valid, error = pmm_validate_config(config)
    if not is_valid:
        keyboard = [
            [InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_review_back")]
        ]
        await query.message.edit_text(
            f"*Validation Error*\n\n{escape_markdown_v2(error or 'Unknown error')}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        config_id = config.get("id", "")
        result = await client.controllers.create_or_update_controller_config(
            config_id, config
        )

        if result.get("status") == "success" or "success" in str(result).lower():
            keyboard = [
                [
                    InlineKeyboardButton(
                        "Create Another", callback_data="bots:new_pmm_mister"
                    )
                ],
                [InlineKeyboardButton("Deploy Now", callback_data="bots:deploy_menu")],
                [
                    InlineKeyboardButton(
                        "Back to Menu", callback_data="bots:controller_configs"
                    )
                ],
            ]
            await query.message.edit_text(
                r"*✅ Config Saved\!*" + "\n\n"
                f"*ID:* `{escape_markdown_v2(config.get('id', ''))}`",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            clear_bots_state(context)
        else:
            error_msg = result.get("message", str(result))
            keyboard = [
                [InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_review_back")]
            ]
            await query.message.edit_text(
                f"*Save Failed*\n\n{escape_markdown_v2(error_msg[:200])}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    except Exception as e:
        logger.error(f"Error saving PMM config: {e}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_review_back")]
        ]
        await query.message.edit_text(
            f"*Error*\n\n{escape_markdown_v2(str(e)[:200])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_pmm_review_back(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Back to review"""
    await _show_pmm_wizard_review_step(update, context)


async def handle_pmm_edit_id(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Edit config ID"""
    query = update.callback_query
    config = get_controller_config(context)
    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = "edit_id"

    keyboard = [
        [InlineKeyboardButton("❌ Cancel", callback_data="bots:pmm_review_back")]
    ]
    await query.message.edit_text(
        r"*Edit Config ID*" + "\n\n"
        f"Current: `{escape_markdown_v2(config.get('id', ''))}`" + "\n\n"
        r"Enter new ID:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_pmm_edit_field(
    update: Update, context: ContextTypes.DEFAULT_TYPE, field: str
) -> None:
    """Handle editing a specific field from review"""
    query = update.callback_query
    config = get_controller_config(context)
    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = f"edit_{field}"

    keyboard = [
        [InlineKeyboardButton("❌ Cancel", callback_data="bots:pmm_review_back")]
    ]

    if field == "leverage":
        # Show leverage buttons instead of text input
        keyboard = [
            [
                InlineKeyboardButton("1x", callback_data="bots:pmm_set:leverage:1"),
                InlineKeyboardButton("5x", callback_data="bots:pmm_set:leverage:5"),
                InlineKeyboardButton("10x", callback_data="bots:pmm_set:leverage:10"),
            ],
            [
                InlineKeyboardButton("20x", callback_data="bots:pmm_set:leverage:20"),
                InlineKeyboardButton("50x", callback_data="bots:pmm_set:leverage:50"),
                InlineKeyboardButton("75x", callback_data="bots:pmm_set:leverage:75"),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="bots:pmm_review_back")],
        ]
        await query.message.edit_text(
            r"*Edit Leverage*" + "\n\n" f"Current: `{config.get('leverage', 20)}x`",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif field == "allocation":
        keyboard = [
            [
                InlineKeyboardButton(
                    "1%", callback_data="bots:pmm_set:allocation:0.01"
                ),
                InlineKeyboardButton(
                    "2%", callback_data="bots:pmm_set:allocation:0.02"
                ),
                InlineKeyboardButton(
                    "3%", callback_data="bots:pmm_set:allocation:0.03"
                ),
            ],
            [
                InlineKeyboardButton(
                    "5%", callback_data="bots:pmm_set:allocation:0.05"
                ),
                InlineKeyboardButton(
                    "10%", callback_data="bots:pmm_set:allocation:0.1"
                ),
                InlineKeyboardButton(
                    "20%", callback_data="bots:pmm_set:allocation:0.2"
                ),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="bots:pmm_review_back")],
        ]
        await query.message.edit_text(
            r"*Edit Portfolio Allocation*" + "\n\n"
            f"Current: `{config.get('portfolio_allocation', 0.05)*100:.0f}%`" + "\n\n"
            r"_Or type a custom value \(e\.g\. 3% or 0\.03\)_",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif field == "spreads":
        keyboard = [
            [
                InlineKeyboardButton(
                    "Tight: 0.02%, 0.1%",
                    callback_data="bots:pmm_set:spreads:0.0002,0.001",
                )
            ],
            [
                InlineKeyboardButton(
                    "Normal: 0.5%, 1%", callback_data="bots:pmm_set:spreads:0.005,0.01"
                )
            ],
            [
                InlineKeyboardButton(
                    "Wide: 1%, 2%", callback_data="bots:pmm_set:spreads:0.01,0.02"
                )
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="bots:pmm_review_back")],
        ]
        await query.message.edit_text(
            r"*Edit Spreads*" + "\n\n"
            f"Buy: `{escape_markdown_v2(config.get('buy_spreads', ''))}`" + "\n"
            f"Sell: `{escape_markdown_v2(config.get('sell_spreads', ''))}`" + "\n\n"
            r"_Or type custom spreads \(e\.g\. 0\.001,0\.002\)_",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif field == "take_profit":
        keyboard = [
            [
                InlineKeyboardButton(
                    "0.01%", callback_data="bots:pmm_set:take_profit:0.0001"
                ),
                InlineKeyboardButton(
                    "0.02%", callback_data="bots:pmm_set:take_profit:0.0002"
                ),
                InlineKeyboardButton(
                    "0.05%", callback_data="bots:pmm_set:take_profit:0.0005"
                ),
            ],
            [
                InlineKeyboardButton(
                    "0.1%", callback_data="bots:pmm_set:take_profit:0.001"
                ),
                InlineKeyboardButton(
                    "0.2%", callback_data="bots:pmm_set:take_profit:0.002"
                ),
                InlineKeyboardButton(
                    "0.5%", callback_data="bots:pmm_set:take_profit:0.005"
                ),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="bots:pmm_review_back")],
        ]
        await query.message.edit_text(
            r"*Edit Take Profit*" + "\n\n"
            f"Current: `{config.get('take_profit', 0.0001)*100:.2f}%`" + "\n\n"
            r"_Or type a custom value \(e\.g\. 0\.001 for 0\.1%\)_",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif field == "base":
        await query.message.edit_text(
            r"*Edit Base Percentages*" + "\n\n"
            f"Min: `{config.get('min_base_pct', 0.1)*100:.0f}%`" + "\n"
            f"Target: `{config.get('target_base_pct', 0.2)*100:.0f}%`" + "\n"
            f"Max: `{config.get('max_base_pct', 0.4)*100:.0f}%`" + "\n\n"
            r"Enter new values \(min,target,max\):" + "\n"
            r"_Example: 0\.1,0\.2,0\.4_",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_pmm_set_field(
    update: Update, context: ContextTypes.DEFAULT_TYPE, field: str, value: str
) -> None:
    """Handle setting a field value from button click"""
    config = get_controller_config(context)

    if field == "leverage":
        config["leverage"] = int(value)
    elif field == "allocation":
        config["portfolio_allocation"] = float(value)
    elif field == "spreads":
        config["buy_spreads"] = value
        config["sell_spreads"] = value
    elif field == "take_profit":
        config["take_profit"] = float(value)

    set_controller_config(context, config)
    await _show_pmm_wizard_review_step(update, context)


async def handle_pmm_edit_advanced(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show advanced settings"""
    query = update.callback_query
    config = get_controller_config(context)

    keyboard = [
        [
            InlineKeyboardButton("Base %", callback_data="bots:pmm_adv:base"),
            InlineKeyboardButton("Cooldowns", callback_data="bots:pmm_adv:cooldown"),
        ],
        [
            InlineKeyboardButton("Refresh Time", callback_data="bots:pmm_adv:refresh"),
            InlineKeyboardButton(
                "Max Executors", callback_data="bots:pmm_adv:max_exec"
            ),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_review_back")],
    ]

    await query.message.edit_text(
        r"*Advanced Settings*" + "\n\n"
        f"📈 *Base %:* min=`{config.get('min_base_pct', 0.3)*100:.0f}%` "
        f"target=`{config.get('target_base_pct', 0.5)*100:.0f}%` "
        f"max=`{config.get('max_base_pct', 0.7)*100:.0f}%`" + "\n"
        f"⏱️ *Refresh:* `{config.get('executor_refresh_time', 10)}s`" + "\n"
        f"⏸️ *Cooldowns:* buy=`{config.get('buy_cooldown_time', 10)}s` "
        f"sell=`{config.get('sell_cooldown_time', 10)}s`" + "\n"
        f"🔢 *Max Executors:* `{config.get('max_active_executors_by_level', 10)}`" + "\n"
        f"🛡️ *Profit Protection:* `{config.get('position_profit_protection', True)}`" + "\n"
        f"📊 *Global TP/SL:* `{config.get('global_take_profit', '0.03')}`/`{config.get('global_stop_loss', '0.05')}`",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_pmm_adv_setting(
    update: Update, context: ContextTypes.DEFAULT_TYPE, setting: str
) -> None:
    """Handle advanced setting edit"""
    query = update.callback_query
    config = get_controller_config(context)

    context.user_data["bots_state"] = "pmm_wizard_input"
    context.user_data["pmm_wizard_step"] = f"adv_{setting}"

    hints = {
        "base": (
            "Base Percentages",
            f"min={config.get('min_base_pct', 0.1)}, target={config.get('target_base_pct', 0.2)}, max={config.get('max_base_pct', 0.4)}",
            "min,target,max as decimals",
        ),
        "cooldown": (
            "Cooldown Times",
            f"buy={config.get('buy_cooldown_time', 15)}s, sell={config.get('sell_cooldown_time', 15)}s",
            "buy,sell in seconds",
        ),
        "refresh": (
            "Refresh Time",
            f"{config.get('executor_refresh_time', 30)}s",
            "seconds",
        ),
        "max_exec": (
            "Max Executors",
            str(config.get("max_active_executors_by_level", 4)),
            "number",
        ),
    }
    label, current, hint = hints.get(setting, (setting, "", ""))

    keyboard = [
        [InlineKeyboardButton("❌ Cancel", callback_data="bots:pmm_edit_advanced")]
    ]
    await query.message.edit_text(
        f"*Edit {escape_markdown_v2(label)}*" + "\n\n"
        f"Current: `{escape_markdown_v2(current)}`" + "\n\n"
        f"Enter new value \\({escape_markdown_v2(hint)}\\):",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _show_pmm_pair_suggestions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    input_pair: str,
    error_msg: str,
    suggestions: list,
    connector: str,
) -> None:
    """Show trading pair suggestions when validation fails in PMM wizard"""
    message_id = context.user_data.get("pmm_wizard_message_id")
    chat_id = context.user_data.get("pmm_wizard_chat_id")

    # Build suggestion message
    help_text = f"❌ *{escape_markdown_v2(error_msg)}*\n\n"

    if suggestions:
        help_text += "💡 *Did you mean:*\n"
    else:
        help_text += "_No similar pairs found\\._\n"

    # Build keyboard with suggestions
    keyboard = []
    for pair in suggestions:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📈 {pair}", callback_data=f"bots:pmm_pair_select:{pair}"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_back:connector"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ]
    )
    reply_markup = InlineKeyboardMarkup(keyboard)

    if message_id and chat_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.debug(f"Could not update PMM wizard message: {e}")
    else:
        await update.effective_chat.send_message(
            help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def handle_pmm_pair_select(
    update: Update, context: ContextTypes.DEFAULT_TYPE, trading_pair: str
) -> None:
    """Handle selection of a suggested trading pair in PMM wizard"""
    config = get_controller_config(context)
    message_id = context.user_data.get("pmm_wizard_message_id")
    chat_id = context.user_data.get("pmm_wizard_chat_id")

    config["trading_pair"] = trading_pair
    set_controller_config(context, config)
    connector = config.get("connector_name", "")

    # Only ask for leverage on perpetual exchanges
    if connector.endswith("_perpetual"):
        context.user_data["pmm_wizard_step"] = "leverage"
        keyboard = [
            [
                InlineKeyboardButton("1x", callback_data="bots:pmm_leverage:1"),
                InlineKeyboardButton("5x", callback_data="bots:pmm_leverage:5"),
                InlineKeyboardButton("10x", callback_data="bots:pmm_leverage:10"),
            ],
            [
                InlineKeyboardButton("20x", callback_data="bots:pmm_leverage:20"),
                InlineKeyboardButton("50x", callback_data="bots:pmm_leverage:50"),
                InlineKeyboardButton("75x", callback_data="bots:pmm_leverage:75"),
            ],
            [
                InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_back:pair"),
                InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
            ],
        ]
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=r"*📈 PMM Mister \- New Config*" + "\n\n"
            f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(trading_pair)}`"
            + "\n\n"
            r"*Step 3/8:* ⚡ Leverage",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        # Spot exchange - set leverage to 1 and skip to allocation
        config["leverage"] = 1
        set_controller_config(context, config)
        context.user_data["bots_state"] = "pmm_wizard_input"
        context.user_data["pmm_wizard_step"] = "portfolio_allocation"
        keyboard = [
            [
                InlineKeyboardButton("1%", callback_data="bots:pmm_alloc:0.01"),
                InlineKeyboardButton("2%", callback_data="bots:pmm_alloc:0.02"),
                InlineKeyboardButton("3%", callback_data="bots:pmm_alloc:0.03"),
            ],
            [
                InlineKeyboardButton("5%", callback_data="bots:pmm_alloc:0.05"),
                InlineKeyboardButton("10%", callback_data="bots:pmm_alloc:0.1"),
                InlineKeyboardButton("20%", callback_data="bots:pmm_alloc:0.2"),
            ],
            [
                InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_back:pair"),
                InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
            ],
        ]
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=r"*📈 PMM Mister \- New Config*" + "\n\n"
            f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(trading_pair)}`"
            + "\n\n"
            r"*Step 4/8:* 💰 Portfolio Allocation" + "\n\n"
            r"_Or type a custom value \(e\.g\. 3% or 0\.03\)_",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def process_pmm_wizard_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str
) -> None:
    """Process text input during PMM wizard"""
    step = context.user_data.get("pmm_wizard_step", "")
    config = get_controller_config(context)
    message_id = context.user_data.get("pmm_wizard_message_id")
    chat_id = context.user_data.get("pmm_wizard_chat_id")

    try:
        await update.message.delete()
    except Exception:
        pass

    if step == "trading_pair":
        pair = user_input.upper().strip()
        if "-" not in pair:
            pair = pair.replace("/", "-").replace("_", "-")

        connector = config.get("connector_name", "")

        # Validate trading pair exists on the connector
        client, _ = await get_bots_client(chat_id, context.user_data)
        is_valid, error_msg, suggestions, correct_pair = await validate_trading_pair(
            context.user_data, client, connector, pair
        )

        if not is_valid:
            # Show error with suggestions
            await _show_pmm_pair_suggestions(
                update, context, pair, error_msg, suggestions, connector
            )
            return

        # Use the correct pair format returned by validation
        if correct_pair:
            pair = correct_pair
        else:
            # Fallback: Get correctly formatted pair from trading rules
            trading_rules = await get_trading_rules(
                context.user_data, client, connector
            )
            fallback_pair = get_correct_pair_format(trading_rules, pair)
            if fallback_pair:
                pair = fallback_pair

        config["trading_pair"] = pair
        set_controller_config(context, config)

        # Only ask for leverage on perpetual exchanges
        if connector.endswith("_perpetual"):
            context.user_data["pmm_wizard_step"] = "leverage"
            keyboard = [
                [
                    InlineKeyboardButton("1x", callback_data="bots:pmm_leverage:1"),
                    InlineKeyboardButton("5x", callback_data="bots:pmm_leverage:5"),
                    InlineKeyboardButton("10x", callback_data="bots:pmm_leverage:10"),
                ],
                [
                    InlineKeyboardButton("20x", callback_data="bots:pmm_leverage:20"),
                    InlineKeyboardButton("50x", callback_data="bots:pmm_leverage:50"),
                    InlineKeyboardButton("75x", callback_data="bots:pmm_leverage:75"),
                ],
                [
                    InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_back:pair"),
                    InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                ],
            ]
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=r"*📈 PMM Mister \- New Config*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(config['trading_pair'])}`"
                + "\n\n"
                r"*Step 3/8:* ⚡ Leverage",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            # Spot exchange - set leverage to 1 and skip to allocation
            config["leverage"] = 1
            set_controller_config(context, config)
            context.user_data["bots_state"] = "pmm_wizard_input"
            context.user_data["pmm_wizard_step"] = "portfolio_allocation"
            keyboard = [
                [
                    InlineKeyboardButton("1%", callback_data="bots:pmm_alloc:0.01"),
                    InlineKeyboardButton("2%", callback_data="bots:pmm_alloc:0.02"),
                    InlineKeyboardButton("3%", callback_data="bots:pmm_alloc:0.03"),
                ],
                [
                    InlineKeyboardButton("5%", callback_data="bots:pmm_alloc:0.05"),
                    InlineKeyboardButton("10%", callback_data="bots:pmm_alloc:0.1"),
                    InlineKeyboardButton("20%", callback_data="bots:pmm_alloc:0.2"),
                ],
                [
                    InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_back:pair"),
                    InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                ],
            ]
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=r"*📈 PMM Mister \- New Config*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(config['trading_pair'])}`"
                + "\n\n"
                r"*Step 4/8:* 💰 Portfolio Allocation" + "\n\n"
                r"_Or type a custom value \(e\.g\. 3% or 0\.03\)_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    elif step == "portfolio_allocation":
        # Parse allocation value (handle "3%" or "0.03" formats)
        try:
            val_str = user_input.strip().replace("%", "")
            val = float(val_str)
            if val > 1:  # User entered percentage like "3" or "3%"
                val = val / 100
            config["portfolio_allocation"] = val
            set_controller_config(context, config)
            context.user_data["pmm_wizard_step"] = "total_amount_quote"
            await _show_pmm_wizard_amount_step_msg(context, chat_id, message_id, config)
        except ValueError:
            # Invalid input - show error and keep at same step
            connector = config.get("connector_name", "")
            pair = config.get("trading_pair", "")
            leverage = config.get("leverage", 20)
            back_target = "leverage" if connector.endswith("_perpetual") else "pair"
            keyboard = [
                [
                    InlineKeyboardButton("1%", callback_data="bots:pmm_alloc:0.01"),
                    InlineKeyboardButton("2%", callback_data="bots:pmm_alloc:0.02"),
                    InlineKeyboardButton("3%", callback_data="bots:pmm_alloc:0.03"),
                ],
                [
                    InlineKeyboardButton("5%", callback_data="bots:pmm_alloc:0.05"),
                    InlineKeyboardButton("10%", callback_data="bots:pmm_alloc:0.1"),
                    InlineKeyboardButton("20%", callback_data="bots:pmm_alloc:0.2"),
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back", callback_data=f"bots:pmm_back:{back_target}"
                    ),
                    InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                ],
            ]
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=r"*📈 PMM Mister \- New Config*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
                + "\n"
                f"⚡ `{leverage}x`" + "\n\n"
                r"*Step 4/8:* 💰 Portfolio Allocation" + "\n\n"
                r"⚠️ _Invalid value\. Enter a percentage \(e\.g\. 3% or 0\.03\)_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    elif step == "total_amount_quote":
        # Parse amount value
        try:
            amount = float(user_input.strip().replace(",", ""))
            config["total_amount_quote"] = amount
            set_controller_config(context, config)
            context.user_data["pmm_wizard_step"] = "spreads"
            await _show_pmm_wizard_spreads_step_msg(
                context, chat_id, message_id, config
            )
        except ValueError:
            # Invalid input - show error and keep at same step
            connector = config.get("connector_name", "")
            pair = config.get("trading_pair", "")
            leverage = config.get("leverage", 20)
            allocation = config.get("portfolio_allocation", 0.05)
            keyboard = [
                [
                    InlineKeyboardButton("💵 100", callback_data="bots:pmm_amount:100"),
                    InlineKeyboardButton("💵 500", callback_data="bots:pmm_amount:500"),
                    InlineKeyboardButton(
                        "💵 1000", callback_data="bots:pmm_amount:1000"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "💰 2000", callback_data="bots:pmm_amount:2000"
                    ),
                    InlineKeyboardButton(
                        "💰 5000", callback_data="bots:pmm_amount:5000"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back", callback_data="bots:pmm_back:allocation"
                    ),
                    InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                ],
            ]
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=r"*📈 PMM Mister \- New Config*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
                + "\n"
                f"⚡ `{leverage}x` \\| 💰 `{allocation*100:.1f}%`" + "\n\n"
                r"*Step 5/8:* 💵 Total Amount \(Quote\)" + "\n\n"
                r"⚠️ _Invalid value\. Enter a number \(e\.g\. 500\)_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    elif step == "spreads":
        config["buy_spreads"] = user_input.strip()
        config["sell_spreads"] = user_input.strip()
        set_controller_config(context, config)
        context.user_data["pmm_wizard_step"] = "take_profit"
        connector = config.get("connector_name", "")
        pair = config.get("trading_pair", "")
        leverage = config.get("leverage", 20)
        allocation = config.get("portfolio_allocation", 0.05)
        keyboard = [
            [
                InlineKeyboardButton("0.01%", callback_data="bots:pmm_tp:0.0001"),
                InlineKeyboardButton("0.02%", callback_data="bots:pmm_tp:0.0002"),
                InlineKeyboardButton("0.05%", callback_data="bots:pmm_tp:0.0005"),
            ],
            [
                InlineKeyboardButton("0.1%", callback_data="bots:pmm_tp:0.001"),
                InlineKeyboardButton("0.2%", callback_data="bots:pmm_tp:0.002"),
                InlineKeyboardButton("0.5%", callback_data="bots:pmm_tp:0.005"),
            ],
            [
                InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_back:spreads"),
                InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
            ],
        ]
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=r"*📈 PMM Mister \- New Config*" + "\n\n"
            f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
            + "\n"
            f"⚡ `{leverage}x` \\| 💰 `{allocation*100:.0f}%`" + "\n"
            f"📊 Spreads: `{escape_markdown_v2(user_input.strip())}`" + "\n\n"
            r"*Step 7/8:* 🎯 Take Profit",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif step == "edit_id":
        config["id"] = user_input.strip()
        set_controller_config(context, config)
        await _pmm_show_review(context, chat_id, message_id, config)

    elif step == "edit_allocation":
        try:
            val = float(user_input.strip())
            if val > 1:  # User entered percentage
                val = val / 100
            config["portfolio_allocation"] = val
            set_controller_config(context, config)
        except ValueError:
            pass
        await _pmm_show_review(context, chat_id, message_id, config)

    elif step == "edit_spreads":
        config["buy_spreads"] = user_input.strip()
        config["sell_spreads"] = user_input.strip()
        set_controller_config(context, config)
        await _pmm_show_review(context, chat_id, message_id, config)

    elif step == "edit_take_profit":
        try:
            val = float(user_input.strip())
            config["take_profit"] = val
            set_controller_config(context, config)
        except ValueError:
            pass
        await _pmm_show_review(context, chat_id, message_id, config)

    elif step == "edit_base":
        try:
            parts = [float(x.strip()) for x in user_input.split(",")]
            if len(parts) == 3:
                (
                    config["min_base_pct"],
                    config["target_base_pct"],
                    config["max_base_pct"],
                ) = parts
                set_controller_config(context, config)
        except ValueError:
            pass
        await _pmm_show_review(context, chat_id, message_id, config)

    elif step == "adv_base":
        try:
            parts = [float(x.strip()) for x in user_input.split(",")]
            if len(parts) == 3:
                (
                    config["min_base_pct"],
                    config["target_base_pct"],
                    config["max_base_pct"],
                ) = parts
                set_controller_config(context, config)
        except ValueError:
            pass
        await _pmm_show_advanced(context, chat_id, message_id, config)

    elif step == "adv_cooldown":
        try:
            parts = [int(x.strip()) for x in user_input.split(",")]
            if len(parts) == 2:
                config["buy_cooldown_time"], config["sell_cooldown_time"] = parts
                set_controller_config(context, config)
        except ValueError:
            pass
        await _pmm_show_advanced(context, chat_id, message_id, config)

    elif step == "adv_refresh":
        try:
            config["executor_refresh_time"] = int(user_input)
            set_controller_config(context, config)
        except ValueError:
            pass
        await _pmm_show_advanced(context, chat_id, message_id, config)

    elif step == "adv_max_exec":
        try:
            config["max_active_executors_by_level"] = int(user_input)
            set_controller_config(context, config)
        except ValueError:
            pass
        await _pmm_show_advanced(context, chat_id, message_id, config)

    elif step == "review":
        # Parse field: value or field=value pairs
        field_map = {
            "id": ("id", str),
            "connector_name": ("connector_name", str),
            "trading_pair": ("trading_pair", str),
            "leverage": ("leverage", int),
            "position_mode": ("position_mode", str),
            "total_amount_quote": ("total_amount_quote", float),
            "portfolio_allocation": ("portfolio_allocation", float),
            "target_base_pct": ("target_base_pct", float),
            "min_base_pct": ("min_base_pct", float),
            "max_base_pct": ("max_base_pct", float),
            "buy_spreads": ("buy_spreads", str),
            "sell_spreads": ("sell_spreads", str),
            "buy_amounts_pct": ("buy_amounts_pct", str),
            "sell_amounts_pct": ("sell_amounts_pct", str),
            "take_profit": ("take_profit", float),
            "take_profit_order_type": ("take_profit_order_type", str),
            "open_order_type": ("open_order_type", str),
            "executor_refresh_time": ("executor_refresh_time", int),
            "buy_cooldown_time": ("buy_cooldown_time", int),
            "sell_cooldown_time": ("sell_cooldown_time", int),
            "buy_position_effectivization_time": (
                "buy_position_effectivization_time",
                int,
            ),
            "sell_position_effectivization_time": (
                "sell_position_effectivization_time",
                int,
            ),
            "price_distance_tolerance": ("price_distance_tolerance", str),
            "refresh_tolerance": ("refresh_tolerance", str),
            "tolerance_scaling": ("tolerance_scaling", str),
            "max_active_executors_by_level": ("max_active_executors_by_level", int),
            "position_profit_protection": ("position_profit_protection", str),
            "min_skew": ("min_skew", str),
            "global_take_profit": ("global_take_profit", str),
            "global_stop_loss": ("global_stop_loss", str),
            "manual_kill_switch": ("manual_kill_switch", str),
        }

        updated_fields = []
        lines = user_input.strip().split("\n")

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Parse field: value or field=value
            if ":" in line:
                parts = line.split(":", 1)
            elif "=" in line:
                parts = line.split("=", 1)
            else:
                continue

            if len(parts) != 2:
                continue

            field_name = parts[0].strip().lower()
            value_str = parts[1].strip()

            if field_name in field_map:
                config_key, type_fn = field_map[field_name]
                try:
                    # Special handling for order type fields
                    if config_key in ("take_profit_order_type", "open_order_type"):
                        # Normalize input to uppercase with underscores
                        normalized = value_str.upper().replace(" ", "_")
                        if normalized in ("LIMIT_MAKER", "LIMIT", "MARKET"):
                            config[config_key] = normalized
                            updated_fields.append(field_name)
                    elif type_fn == str:
                        config[config_key] = value_str
                        updated_fields.append(field_name)
                    else:
                        config[config_key] = type_fn(value_str)
                        updated_fields.append(field_name)
                except (ValueError, TypeError):
                    pass

        if updated_fields:
            set_controller_config(context, config)
            await _pmm_show_review(context, chat_id, message_id, config)


async def _pmm_show_review(context, chat_id, message_id, config):
    """Helper to show review step with copyable config format"""
    # Format order types as string
    tp_order_type = config.get("take_profit_order_type", "LIMIT_MAKER")
    if isinstance(tp_order_type, int):
        tp_order_type_str = ORDER_TYPE_LABELS.get(tp_order_type, "LIMIT_MAKER")
    else:
        tp_order_type_str = str(tp_order_type)

    open_order_type = config.get("open_order_type", "LIMIT_MAKER")
    if isinstance(open_order_type, int):
        open_order_type_str = ORDER_TYPE_LABELS.get(open_order_type, "LIMIT_MAKER")
    else:
        open_order_type_str = str(open_order_type)

    # Get amounts
    buy_spreads = config.get("buy_spreads", "0.0002,0.0006")
    sell_spreads = config.get("sell_spreads", "0.0002,0.0006")
    buy_amounts = config.get("buy_amounts_pct", "1,1")
    sell_amounts = config.get("sell_amounts_pct", "1,1")

    if not buy_amounts:
        num_buy_spreads = len(buy_spreads.split(",")) if isinstance(buy_spreads, str) and buy_spreads else 1
        buy_amounts = ",".join(["1"] * num_buy_spreads)
    if not sell_amounts:
        num_sell_spreads = len(sell_spreads.split(",")) if isinstance(sell_spreads, str) and sell_spreads else 1
        sell_amounts = ",".join(["1"] * num_sell_spreads)

    # Build copyable config block
    config_block = (
        f"id: {config.get('id', '')}\n"
        f"connector_name: {config.get('connector_name', '')}\n"
        f"trading_pair: {config.get('trading_pair', '')}\n"
        f"leverage: {config.get('leverage', 1)}\n"
        f"position_mode: {config.get('position_mode', 'ONEWAY')}\n"
        f"total_amount_quote: {config.get('total_amount_quote', 1000)}\n"
        f"portfolio_allocation: {config.get('portfolio_allocation', 0.03)}\n"
        f"target_base_pct: {config.get('target_base_pct', 0.5)}\n"
        f"min_base_pct: {config.get('min_base_pct', 0.3)}\n"
        f"max_base_pct: {config.get('max_base_pct', 0.7)}\n"
        f"buy_spreads: {buy_spreads}\n"
        f"sell_spreads: {sell_spreads}\n"
        f"buy_amounts_pct: {buy_amounts}\n"
        f"sell_amounts_pct: {sell_amounts}\n"
        f"take_profit: {config.get('take_profit', 0.0003)}\n"
        f"take_profit_order_type: {tp_order_type_str}\n"
        f"open_order_type: {open_order_type_str}\n"
        f"executor_refresh_time: {config.get('executor_refresh_time', 10)}\n"
        f"buy_cooldown_time: {config.get('buy_cooldown_time', 10)}\n"
        f"sell_cooldown_time: {config.get('sell_cooldown_time', 10)}\n"
        f"buy_position_effectivization_time: {config.get('buy_position_effectivization_time', 300)}\n"
        f"sell_position_effectivization_time: {config.get('sell_position_effectivization_time', 300)}\n"
        f"price_distance_tolerance: {config.get('price_distance_tolerance', '0.0005')}\n"
        f"refresh_tolerance: {config.get('refresh_tolerance', '0.0005')}\n"
        f"tolerance_scaling: {config.get('tolerance_scaling', '1.2')}\n"
        f"max_active_executors_by_level: {config.get('max_active_executors_by_level', 10)}\n"
        f"position_profit_protection: {config.get('position_profit_protection', True)}\n"
        f"min_skew: {config.get('min_skew', '1.0')}\n"
        f"global_take_profit: {config.get('global_take_profit', '0.03')}\n"
        f"global_stop_loss: {config.get('global_stop_loss', '0.05')}"
    )

    pair = config.get("trading_pair", "")
    message_text = (
        f"*{escape_markdown_v2(pair)}* \\- Review Config\n\n"
        f"```\n{config_block}\n```\n\n"
        f"_To edit, send `field: value` lines_"
    )

    keyboard = [
        [InlineKeyboardButton("✅ Save Config", callback_data="bots:pmm_save")],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_back:tp"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        # Ignore "Message is not modified" error
        if "Message is not modified" not in str(e):
            raise


async def _pmm_show_advanced(context, chat_id, message_id, config):
    """Helper to show advanced settings"""
    keyboard = [
        [
            InlineKeyboardButton("Base %", callback_data="bots:pmm_adv:base"),
            InlineKeyboardButton("Cooldowns", callback_data="bots:pmm_adv:cooldown"),
        ],
        [
            InlineKeyboardButton("Refresh Time", callback_data="bots:pmm_adv:refresh"),
            InlineKeyboardButton(
                "Max Executors", callback_data="bots:pmm_adv:max_exec"
            ),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="bots:pmm_review_back")],
    ]
    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=r"*Advanced Settings*" + "\n\n"
        f"📈 *Base %:* min=`{config.get('min_base_pct', 0.3)*100:.0f}%` "
        f"target=`{config.get('target_base_pct', 0.5)*100:.0f}%` "
        f"max=`{config.get('max_base_pct', 0.7)*100:.0f}%`" + "\n"
        f"⏱️ *Refresh:* `{config.get('executor_refresh_time', 10)}s`" + "\n"
        f"⏸️ *Cooldowns:* buy=`{config.get('buy_cooldown_time', 10)}s` "
        f"sell=`{config.get('sell_cooldown_time', 10)}s`" + "\n"
        f"🔢 *Max Executors:* `{config.get('max_active_executors_by_level', 10)}`" + "\n"
        f"🛡️ *Profit Protection:* `{config.get('position_profit_protection', True)}`" + "\n"
        f"📊 *Global TP/SL:* `{config.get('global_take_profit', '0.03')}`/`{config.get('global_stop_loss', '0.05')}`",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ============================================
# PMM V1 WIZARD
# ============================================

from .controllers.pmm_v1 import generate_id as pv1_generate_id
from .controllers.pmm_v1 import validate_config as pv1_validate_config
from .controllers.pmm_v1 import parse_spreads as pv1_parse_spreads
from .controllers.pmm_v1 import FIELD_ORDER as PV1_FIELD_ORDER
from .controllers.pmm_v1 import FIELDS as PV1_FIELDS


async def show_new_pmm_v1_form(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Start the progressive PMM V1 wizard - Step 1: Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()
        context.user_data["controller_configs_list"] = configs
    except Exception as e:
        logger.warning(f"Could not fetch existing configs: {e}")

    config = init_new_controller_config(context, "pmm_v1")
    context.user_data["bots_state"] = "pv1_wizard"
    context.user_data["pv1_wizard_step"] = "connector_name"
    context.user_data["pv1_wizard_message_id"] = query.message.message_id
    context.user_data["pv1_wizard_chat_id"] = query.message.chat_id

    await _show_pv1_wizard_connector_step(update, context)


async def _show_pv1_wizard_connector_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """PMM V1 Wizard Step 1: Select Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, server_name = await get_bots_client(chat_id, context.user_data)
        cex_connectors = await get_available_cex_connectors(
            context.user_data, client, server_name=server_name
        )

        if not cex_connectors:
            keyboard = [
                [
                    InlineKeyboardButton(
                        "🔑 Configure API Keys", callback_data="config_api_keys"
                    )
                ],
                [InlineKeyboardButton("« Back", callback_data="bots:main_menu")],
            ]
            await query.message.edit_text(
                r"*PMM V1 \- New Config*" + "\n\n"
                r"⚠️ No CEX connectors available\." + "\n\n"
                r"You need to connect API keys for an exchange to deploy strategies\."
                + "\n"
                r"Click below to configure your API keys\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        keyboard = []
        row = []
        for connector in cex_connectors:
            row.append(
                InlineKeyboardButton(
                    f"🏦 {connector}", callback_data=f"bots:pv1_connector:{connector}"
                )
            )
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append(
            [InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")]
        )

        await query.message.edit_text(
            r"*📈 PMM V1 \- New Config*" + "\n\n"
            r"*Step 1/5:* 🏦 Select Connector",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"Error in PMM V1 connector step: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        await query.message.edit_text(
            format_error_message(f"Error: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_pv1_wizard_connector(
    update: Update, context: ContextTypes.DEFAULT_TYPE, connector: str
) -> None:
    """Handle connector selection"""
    config = get_controller_config(context)
    config["connector_name"] = connector
    set_controller_config(context, config)
    context.user_data["pv1_wizard_step"] = "trading_pair"
    await _show_pv1_wizard_pair_step(update, context)


async def _show_pv1_wizard_pair_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """PMM V1 Wizard Step 2: Trading Pair"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    context.user_data["bots_state"] = "pv1_wizard_input"
    context.user_data["pv1_wizard_step"] = "trading_pair"

    existing_configs = context.user_data.get("controller_configs_list", [])
    recent_pairs = []
    seen = set()
    for cfg in reversed(existing_configs):
        pair = cfg.get("trading_pair", "")
        if pair and pair not in seen:
            seen.add(pair)
            recent_pairs.append(pair)
            if len(recent_pairs) >= 6:
                break

    keyboard = []
    if recent_pairs:
        row = []
        for pair in recent_pairs:
            row.append(
                InlineKeyboardButton(pair, callback_data=f"bots:pv1_pair:{pair}")
            )
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
    keyboard.append(
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pv1_back:connector"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ]
    )

    await query.message.edit_text(
        r"*📈 PMM V1 \- New Config*" + "\n\n"
        f"*Connector:* `{escape_markdown_v2(connector)}`" + "\n\n"
        r"*Step 2/5:* 🔗 Trading Pair" + "\n\n"
        r"Select or type a pair:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_pv1_wizard_pair(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pair: str
) -> None:
    """Handle pair selection"""
    config = get_controller_config(context)
    config["trading_pair"] = pair.upper()
    set_controller_config(context, config)
    context.user_data["pv1_wizard_step"] = "order_amount"
    await _show_pv1_wizard_amount_step(update, context)


async def _show_pv1_wizard_amount_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """PMM V1 Wizard Step 3: Order Amount (Base)"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    context.user_data["bots_state"] = "pv1_wizard_input"
    context.user_data["pv1_wizard_step"] = "order_amount"

    keyboard = [
        [
            InlineKeyboardButton("0.001", callback_data="bots:pv1_amount:0.001"),
            InlineKeyboardButton("0.01", callback_data="bots:pv1_amount:0.01"),
            InlineKeyboardButton("0.1", callback_data="bots:pv1_amount:0.1"),
        ],
        [
            InlineKeyboardButton("1", callback_data="bots:pv1_amount:1"),
            InlineKeyboardButton("10", callback_data="bots:pv1_amount:10"),
            InlineKeyboardButton("100", callback_data="bots:pv1_amount:100"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pv1_back:pair"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    # Extract base token for label
    base_token = pair.split("-")[0] if "-" in pair else "base"

    await query.message.edit_text(
        r"*📈 PMM V1 \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n\n"
        f"*Step 3/5:* 💰 Order Amount \\({escape_markdown_v2(base_token)}\\)" + "\n\n"
        r"Select or type amount:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_pv1_wizard_amount(
    update: Update, context: ContextTypes.DEFAULT_TYPE, amount: str
) -> None:
    """Handle amount selection"""
    config = get_controller_config(context)
    config["order_amount"] = amount
    set_controller_config(context, config)
    context.user_data["pv1_wizard_step"] = "spreads"
    await _show_pv1_wizard_spreads_step(update, context)


async def _show_pv1_wizard_spreads_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """PMM V1 Wizard Step 4: Spread"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    amount = config.get("order_amount", "0.001")

    context.user_data["bots_state"] = "pv1_wizard_input"
    context.user_data["pv1_wizard_step"] = "spreads"

    keyboard = [
        [
            InlineKeyboardButton("0.01%", callback_data="bots:pv1_spreads:0.0001"),
            InlineKeyboardButton("0.02%", callback_data="bots:pv1_spreads:0.0002"),
            InlineKeyboardButton("0.05%", callback_data="bots:pv1_spreads:0.0005"),
        ],
        [
            InlineKeyboardButton("0.1%", callback_data="bots:pv1_spreads:0.001"),
            InlineKeyboardButton("0.2%", callback_data="bots:pv1_spreads:0.002"),
            InlineKeyboardButton("0.5%", callback_data="bots:pv1_spreads:0.005"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pv1_back:amount"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await query.message.edit_text(
        r"*📈 PMM V1 \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
        + "\n"
        f"💰 Amount: `{escape_markdown_v2(str(amount))}`"
        + "\n\n"
        r"*Step 4/5:* 📊 Spread" + "\n\n"
        r"_Applied to both buy and sell\. Or type a custom value \(e\.g\. 0\.001\)_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_pv1_wizard_spreads(
    update: Update, context: ContextTypes.DEFAULT_TYPE, spread: str
) -> None:
    """Handle spread selection - applies single spread to both buy and sell"""
    config = get_controller_config(context)
    spread_val = float(spread)
    config["buy_spreads"] = [spread_val]
    config["sell_spreads"] = [spread_val]
    set_controller_config(context, config)
    context.user_data["pv1_wizard_step"] = "review"
    await _show_pv1_wizard_review_step(update, context)


async def _show_pv1_wizard_review_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """PMM V1 Wizard Step 5: Review with copyable config format"""
    query = update.callback_query
    config = get_controller_config(context)

    # Generate ID if not set
    if not config.get("id"):
        existing = context.user_data.get("controller_configs_list", [])
        config["id"] = pv1_generate_id(config, existing)
        set_controller_config(context, config)

    context.user_data["bots_state"] = "pv1_wizard_input"
    context.user_data["pv1_wizard_step"] = "review"

    # Build copyable config block
    config_block = (
        f"id: {config.get('id', '')}\n"
        f"connector_name: {config.get('connector_name', '')}\n"
        f"trading_pair: {config.get('trading_pair', '')}\n"
        f"order_amount: {config.get('order_amount', '0.001')}\n"
        f"buy_spreads: {config.get('buy_spreads', [0.0002])}\n"
        f"sell_spreads: {config.get('sell_spreads', [0.0002])}\n"
        f"order_refresh_time: {config.get('order_refresh_time', 30)}\n"
        f"order_refresh_tolerance_pct: {config.get('order_refresh_tolerance_pct', '-1')}\n"
        f"filled_order_delay: {config.get('filled_order_delay', 60)}\n"
        f"inventory_skew_enabled: {config.get('inventory_skew_enabled', False)}\n"
        f"inventory_range_multiplier: {config.get('inventory_range_multiplier', '1.0')}\n"
        f"price_ceiling: {config.get('price_ceiling', '-1')}\n"
        f"price_floor: {config.get('price_floor', '-1')}\n"
        f"manual_kill_switch: {config.get('manual_kill_switch', False)}"
    )

    pair = config.get("trading_pair", "")
    message_text = (
        f"*{escape_markdown_v2(pair)}* \\- Review Config\n\n"
        f"```\n{config_block}\n```\n\n"
        f"_To edit, send `field: value` lines_"
    )

    keyboard = [
        [InlineKeyboardButton("✅ Save Config", callback_data="bots:pv1_save")],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pv1_back:spreads"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_pv1_back(
    update: Update, context: ContextTypes.DEFAULT_TYPE, target: str
) -> None:
    """Handle back navigation in PMM V1 wizard"""
    if target == "connector":
        await _show_pv1_wizard_connector_step(update, context)
    elif target == "pair":
        await _show_pv1_wizard_pair_step(update, context)
    elif target == "amount":
        await _show_pv1_wizard_amount_step(update, context)
    elif target == "spreads":
        await _show_pv1_wizard_spreads_step(update, context)


async def handle_pv1_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save PMM V1 config"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    is_valid, error = pv1_validate_config(config)
    if not is_valid:
        keyboard = [
            [InlineKeyboardButton("⬅️ Back", callback_data="bots:pv1_review_back")]
        ]
        await query.message.edit_text(
            f"*Validation Error*\n\n{escape_markdown_v2(error or 'Unknown error')}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        config_id = config.get("id", "")
        result = await client.controllers.create_or_update_controller_config(
            config_id, config
        )

        if result.get("status") == "success" or "success" in str(result).lower():
            keyboard = [
                [
                    InlineKeyboardButton(
                        "Create Another", callback_data="bots:new_pmm_v1"
                    )
                ],
                [InlineKeyboardButton("Deploy Now", callback_data="bots:deploy_menu")],
                [
                    InlineKeyboardButton(
                        "Back to Menu", callback_data="bots:controller_configs"
                    )
                ],
            ]
            await query.message.edit_text(
                r"*✅ Config Saved\!*" + "\n\n"
                f"*ID:* `{escape_markdown_v2(config.get('id', ''))}`",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            clear_bots_state(context)
        else:
            error_msg = result.get("message", str(result))
            keyboard = [
                [InlineKeyboardButton("⬅️ Back", callback_data="bots:pv1_review_back")]
            ]
            await query.message.edit_text(
                f"*Save Failed*\n\n{escape_markdown_v2(error_msg[:200])}",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    except Exception as e:
        logger.error(f"Error saving PMM V1 config: {e}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton("⬅️ Back", callback_data="bots:pv1_review_back")]
        ]
        await query.message.edit_text(
            f"*Error*\n\n{escape_markdown_v2(str(e)[:200])}",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_pv1_review_back(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Back to review"""
    await _show_pv1_wizard_review_step(update, context)


async def handle_pv1_pair_select(
    update: Update, context: ContextTypes.DEFAULT_TYPE, trading_pair: str
) -> None:
    """Handle selection of a suggested trading pair in PMM V1 wizard"""
    config = get_controller_config(context)
    message_id = context.user_data.get("pv1_wizard_message_id")
    chat_id = context.user_data.get("pv1_wizard_chat_id")

    config["trading_pair"] = trading_pair
    set_controller_config(context, config)
    connector = config.get("connector_name", "")

    # Go to amount step
    context.user_data["pv1_wizard_step"] = "order_amount"
    context.user_data["bots_state"] = "pv1_wizard_input"

    base_token = trading_pair.split("-")[0] if "-" in trading_pair else "base"

    keyboard = [
        [
            InlineKeyboardButton("0.001", callback_data="bots:pv1_amount:0.001"),
            InlineKeyboardButton("0.01", callback_data="bots:pv1_amount:0.01"),
            InlineKeyboardButton("0.1", callback_data="bots:pv1_amount:0.1"),
        ],
        [
            InlineKeyboardButton("1", callback_data="bots:pv1_amount:1"),
            InlineKeyboardButton("10", callback_data="bots:pv1_amount:10"),
            InlineKeyboardButton("100", callback_data="bots:pv1_amount:100"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pv1_back:pair"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=r"*📈 PMM V1 \- New Config*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(trading_pair)}`"
        + "\n\n"
        f"*Step 3/5:* 💰 Order Amount \\({escape_markdown_v2(base_token)}\\)" + "\n\n"
        r"Select or type amount:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def _show_pv1_pair_suggestions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    input_pair: str,
    error_msg: str,
    suggestions: list,
    connector: str,
) -> None:
    """Show trading pair suggestions when validation fails in PMM V1 wizard"""
    message_id = context.user_data.get("pv1_wizard_message_id")
    chat_id = context.user_data.get("pv1_wizard_chat_id")

    help_text = f"❌ *{escape_markdown_v2(error_msg)}*\n\n"

    if suggestions:
        help_text += "💡 *Did you mean:*\n"
    else:
        help_text += "_No similar pairs found\\._\n"

    keyboard = []
    for pair in suggestions:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📈 {pair}", callback_data=f"bots:pv1_pair_select:{pair}"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pv1_back:connector"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ]
    )
    reply_markup = InlineKeyboardMarkup(keyboard)

    if message_id and chat_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.debug(f"Could not update PMM V1 wizard message: {e}")
    else:
        await update.effective_chat.send_message(
            help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def process_pv1_wizard_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str
) -> None:
    """Process text input during PMM V1 wizard"""
    step = context.user_data.get("pv1_wizard_step", "")
    config = get_controller_config(context)
    message_id = context.user_data.get("pv1_wizard_message_id")
    chat_id = context.user_data.get("pv1_wizard_chat_id")

    try:
        await update.message.delete()
    except Exception:
        pass

    if step == "trading_pair":
        pair = user_input.upper().strip()
        if "-" not in pair:
            pair = pair.replace("/", "-").replace("_", "-")

        connector = config.get("connector_name", "")

        # Validate trading pair exists on the connector
        client, _ = await get_bots_client(chat_id, context.user_data)
        is_valid, error_msg, suggestions, correct_pair = await validate_trading_pair(
            context.user_data, client, connector, pair
        )

        if not is_valid:
            await _show_pv1_pair_suggestions(
                update, context, pair, error_msg, suggestions, connector
            )
            return

        # Use the correct pair format
        if correct_pair:
            pair = correct_pair
        else:
            trading_rules = await get_trading_rules(
                context.user_data, client, connector
            )
            fallback_pair = get_correct_pair_format(trading_rules, pair)
            if fallback_pair:
                pair = fallback_pair

        config["trading_pair"] = pair
        set_controller_config(context, config)

        # Go to amount step
        context.user_data["pv1_wizard_step"] = "order_amount"
        base_token = pair.split("-")[0] if "-" in pair else "base"

        keyboard = [
            [
                InlineKeyboardButton("0.001", callback_data="bots:pv1_amount:0.001"),
                InlineKeyboardButton("0.01", callback_data="bots:pv1_amount:0.01"),
                InlineKeyboardButton("0.1", callback_data="bots:pv1_amount:0.1"),
            ],
            [
                InlineKeyboardButton("1", callback_data="bots:pv1_amount:1"),
                InlineKeyboardButton("10", callback_data="bots:pv1_amount:10"),
                InlineKeyboardButton("100", callback_data="bots:pv1_amount:100"),
            ],
            [
                InlineKeyboardButton("⬅️ Back", callback_data="bots:pv1_back:pair"),
                InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
            ],
        ]
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=r"*📈 PMM V1 \- New Config*" + "\n\n"
            f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
            + "\n\n"
            f"*Step 3/5:* 💰 Order Amount \\({escape_markdown_v2(base_token)}\\)"
            + "\n\n"
            r"Select or type amount:",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif step == "order_amount":
        try:
            amount_str = user_input.strip().replace(",", "")
            amount = float(amount_str)
            if amount <= 0:
                raise ValueError("Amount must be positive")
            config["order_amount"] = amount_str
            set_controller_config(context, config)
            context.user_data["pv1_wizard_step"] = "spreads"

            connector = config.get("connector_name", "")
            pair = config.get("trading_pair", "")

            keyboard = [
                [
                    InlineKeyboardButton(
                        "0.01%", callback_data="bots:pv1_spreads:0.0001"
                    ),
                    InlineKeyboardButton(
                        "0.02%", callback_data="bots:pv1_spreads:0.0002"
                    ),
                    InlineKeyboardButton(
                        "0.05%", callback_data="bots:pv1_spreads:0.0005"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "0.1%", callback_data="bots:pv1_spreads:0.001"
                    ),
                    InlineKeyboardButton(
                        "0.2%", callback_data="bots:pv1_spreads:0.002"
                    ),
                    InlineKeyboardButton(
                        "0.5%", callback_data="bots:pv1_spreads:0.005"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back", callback_data="bots:pv1_back:amount"
                    ),
                    InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                ],
            ]
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=r"*📈 PMM V1 \- New Config*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
                + "\n"
                f"💰 Amount: `{escape_markdown_v2(amount_str)}`"
                + "\n\n"
                r"*Step 4/5:* 📊 Spread" + "\n\n"
                r"_Applied to both buy and sell\. Or type a custom value \(e\.g\. 0\.001\)_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except ValueError:
            connector = config.get("connector_name", "")
            pair = config.get("trading_pair", "")
            base_token = pair.split("-")[0] if "-" in pair else "base"
            keyboard = [
                [
                    InlineKeyboardButton(
                        "0.001", callback_data="bots:pv1_amount:0.001"
                    ),
                    InlineKeyboardButton("0.01", callback_data="bots:pv1_amount:0.01"),
                    InlineKeyboardButton("0.1", callback_data="bots:pv1_amount:0.1"),
                ],
                [
                    InlineKeyboardButton("1", callback_data="bots:pv1_amount:1"),
                    InlineKeyboardButton("10", callback_data="bots:pv1_amount:10"),
                    InlineKeyboardButton("100", callback_data="bots:pv1_amount:100"),
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back", callback_data="bots:pv1_back:pair"
                    ),
                    InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                ],
            ]
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=r"*📈 PMM V1 \- New Config*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
                + "\n\n"
                f"*Step 3/5:* 💰 Order Amount \\({escape_markdown_v2(base_token)}\\)"
                + "\n\n"
                r"⚠️ _Invalid value\. Enter a positive number \(e\.g\. 0\.01\)_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    elif step == "spreads":
        try:
            spread_val = float(user_input.strip().replace("%", ""))
            # If user entered percentage like "0.02" meaning 0.02%, convert
            # But if they enter something like 0.0002, treat as raw fraction
            if spread_val <= 0:
                raise ValueError("Spread must be positive")
            config["buy_spreads"] = [spread_val]
            config["sell_spreads"] = [spread_val]
            set_controller_config(context, config)
            context.user_data["pv1_wizard_step"] = "review"

            # Show review via message edit
            await _pv1_show_review(context, chat_id, message_id, config)
        except ValueError:
            connector = config.get("connector_name", "")
            pair = config.get("trading_pair", "")
            amount = config.get("order_amount", "0.001")
            keyboard = [
                [
                    InlineKeyboardButton(
                        "0.01%", callback_data="bots:pv1_spreads:0.0001"
                    ),
                    InlineKeyboardButton(
                        "0.02%", callback_data="bots:pv1_spreads:0.0002"
                    ),
                    InlineKeyboardButton(
                        "0.05%", callback_data="bots:pv1_spreads:0.0005"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "0.1%", callback_data="bots:pv1_spreads:0.001"
                    ),
                    InlineKeyboardButton(
                        "0.2%", callback_data="bots:pv1_spreads:0.002"
                    ),
                    InlineKeyboardButton(
                        "0.5%", callback_data="bots:pv1_spreads:0.005"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back", callback_data="bots:pv1_back:amount"
                    ),
                    InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                ],
            ]
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=r"*📈 PMM V1 \- New Config*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
                + "\n"
                f"💰 Amount: `{escape_markdown_v2(str(amount))}`"
                + "\n\n"
                r"*Step 4/5:* 📊 Spread" + "\n\n"
                r"⚠️ _Invalid value\. Enter a positive number \(e\.g\. 0\.001\)_",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    elif step == "review":
        # Handle field: value editing in review
        if ":" in user_input:
            field, _, value = user_input.partition(":")
            field = field.strip().lower()
            value = value.strip()

            if field in config:
                # Parse booleans
                if value.lower() in ("true", "false"):
                    config[field] = value.lower() == "true"
                # Parse lists
                elif value.startswith("["):
                    try:
                        import ast
                        config[field] = ast.literal_eval(value)
                    except Exception:
                        config[field] = value
                # Try numeric
                else:
                    try:
                        if "." in value:
                            config[field] = float(value)
                        else:
                            config[field] = int(value)
                    except ValueError:
                        config[field] = value

                set_controller_config(context, config)

        await _pv1_show_review(context, chat_id, message_id, config)


async def _pv1_show_review(context, chat_id, message_id, config):
    """Helper to show review step with copyable config format"""
    # Generate ID if not set
    if not config.get("id"):
        existing = context.user_data.get("controller_configs_list", [])
        config["id"] = pv1_generate_id(config, existing)
        set_controller_config(context, config)

    config_block = (
        f"id: {config.get('id', '')}\n"
        f"connector_name: {config.get('connector_name', '')}\n"
        f"trading_pair: {config.get('trading_pair', '')}\n"
        f"order_amount: {config.get('order_amount', '0.001')}\n"
        f"buy_spreads: {config.get('buy_spreads', [0.0002])}\n"
        f"sell_spreads: {config.get('sell_spreads', [0.0002])}\n"
        f"order_refresh_time: {config.get('order_refresh_time', 30)}\n"
        f"order_refresh_tolerance_pct: {config.get('order_refresh_tolerance_pct', '-1')}\n"
        f"filled_order_delay: {config.get('filled_order_delay', 60)}\n"
        f"inventory_skew_enabled: {config.get('inventory_skew_enabled', False)}\n"
        f"inventory_range_multiplier: {config.get('inventory_range_multiplier', '1.0')}\n"
        f"price_ceiling: {config.get('price_ceiling', '-1')}\n"
        f"price_floor: {config.get('price_floor', '-1')}\n"
        f"manual_kill_switch: {config.get('manual_kill_switch', False)}"
    )

    pair = config.get("trading_pair", "")
    message_text = (
        f"*{escape_markdown_v2(pair)}* \\- Review Config\n\n"
        f"```\n{config_block}\n```\n\n"
        f"_To edit, send `field: value` lines_"
    )

    keyboard = [
        [InlineKeyboardButton("✅ Save Config", callback_data="bots:pv1_save")],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:pv1_back:spreads"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        if "Message is not modified" not in str(e):
            raise


# ============================================
# CUSTOM CONFIG UPLOAD
# ============================================


async def show_upload_config_prompt(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show message prompting user to upload a YAML config file"""
    query = update.callback_query

    # Set state to expect file upload
    context.user_data["bots_state"] = "awaiting_config_upload"

    message_text = (
        r"*Upload Custom Config*" + "\n\n"
        r"Upload a YAML file \(`.yml` or `.yaml`\) with your controller configuration\."
        + "\n\n"
        r"The file should contain a valid controller config with at least an `id` field\."
    )

    keyboard = [
        [InlineKeyboardButton("❌ Cancel", callback_data="bots:upload_cancel")],
    ]

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_upload_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Cancel the upload and return to configs menu"""
    clear_bots_state(context)
    await show_controller_configs_menu(update, context)


async def handle_config_file_upload(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle uploaded YAML config file"""
    import yaml

    # Only process if we're expecting a config upload
    if context.user_data.get("bots_state") != "awaiting_config_upload":
        return

    chat_id = update.effective_chat.id
    document = update.message.document

    # Check file extension
    file_name = document.file_name or ""
    if not file_name.lower().endswith((".yml", ".yaml")):
        await update.message.reply_text(
            format_error_message("Please upload a YAML file (.yml or .yaml)"),
            parse_mode="MarkdownV2",
        )
        return

    try:
        # Download the file
        file = await context.bot.get_file(document.file_id)
        file_bytes = await file.download_as_bytearray()
        content = file_bytes.decode("utf-8")

        # Parse YAML
        try:
            config = yaml.safe_load(content)
        except yaml.YAMLError as e:
            await update.message.reply_text(
                format_error_message(f"Invalid YAML file: {str(e)}"),
                parse_mode="MarkdownV2",
            )
            return

        if not isinstance(config, dict):
            await update.message.reply_text(
                format_error_message("YAML file must contain a dictionary/object"),
                parse_mode="MarkdownV2",
            )
            return

        # Validate minimum required field
        config_id = config.get("id")
        if not config_id:
            await update.message.reply_text(
                format_error_message("Config must have an 'id' field"),
                parse_mode="MarkdownV2",
            )
            return

        # Save to backend
        client, _ = await get_bots_client(chat_id, context.user_data)
        result = await client.controllers.create_or_update_controller_config(
            config_id, config
        )

        # Clear state
        clear_bots_state(context)

        # Check result
        if result.get("status") == "success" or "success" in str(result).lower():
            controller_name = config.get("controller_name", "unknown")
            success_msg = (
                f"✅ *Config uploaded successfully\\!*\n\n"
                f"ID: `{escape_markdown_v2(config_id)}`\n"
                f"Type: `{escape_markdown_v2(controller_name)}`"
            )
            keyboard = [
                [
                    InlineKeyboardButton(
                        "📁 View Configs", callback_data="bots:controller_configs"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back to Menu", callback_data="bots:main_menu"
                    )
                ],
            ]
            await update.message.reply_text(
                success_msg,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            error_detail = result.get("message", result.get("error", str(result)))
            await update.message.reply_text(
                format_error_message(f"Failed to save config: {error_detail}"),
                parse_mode="MarkdownV2",
            )

    except Exception as e:
        logger.error(f"Error uploading config file: {e}", exc_info=True)
        clear_bots_state(context)
        await update.message.reply_text(
            format_error_message(f"Failed to upload config: {str(e)}"),
            parse_mode="MarkdownV2",
        )

# ============================================
# MULTI GRID STRIKE WIZARD
# ============================================
# Steps: connector → pair → grid_type → num_grids → leverage (perp) → amount → review+save
# Prefisso handler: mgs_

async def show_new_multi_grid_strike_form(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Start the Multi Grid Strike wizard - Step 1: Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    # Clear cached market data
    for key in ["mgs_current_price", "mgs_candles", "mgs_candles_interval",
                "mgs_chart_interval", "mgs_natr", "mgs_trading_rules"]:
        context.user_data.pop(key, None)

    # Fetch existing configs for sequence numbering
    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()
        context.user_data["controller_configs_list"] = configs
    except Exception as e:
        logger.warning(f"Could not fetch existing configs for sequencing: {e}")

    # Initialize new config with defaults
    init_new_controller_config(context, "multi_grid_strike")
    context.user_data["bots_state"] = "mgs_wizard"
    context.user_data["mgs_wizard_step"] = "connector_name"
    context.user_data["mgs_wizard_message_id"] = query.message.message_id
    context.user_data["mgs_wizard_chat_id"] = query.message.chat_id

    await _mgs_show_connector_step(update, context)


async def _mgs_show_connector_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """MGS Wizard Step 1: Select Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, server_name = await get_bots_client(chat_id, context.user_data)
        cex_connectors = await get_available_cex_connectors(
            context.user_data, client, server_name=server_name
        )

        if not cex_connectors:
            keyboard = [
                [InlineKeyboardButton("🔑 Configure API Keys", callback_data="config_api_keys")],
                [InlineKeyboardButton("« Back", callback_data="bots:main_menu")],
            ]
            await query.message.edit_text(
                r"*🔲 Multi Grid Strike \- New Config*" + "\n\n"
                r"⚠️ No CEX connectors available\." + "\n\n"
                r"You need to connect API keys for an exchange to deploy strategies\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        keyboard = []
        row = []
        for connector in cex_connectors:
            row.append(InlineKeyboardButton(
                f"🏦 {connector}", callback_data=f"bots:mgs_connector:{connector}"
            ))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")])

        # Escapa TUTTO il testo fisso
        message_text = (
            r"\*🔲 Multi Grid Strike\*" + "\n\n"
            r"Multiple independent grids on the same trading pair, each covering "
            r"a different price range\. Ideal for layered market making strategies\." + "\n\n"
            r"─────────────────────────" + "\n\n"
            r"\*Step 1: Select Exchange\*"
        )

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True,
        )

    except Exception as e:
        logger.error(f"MGS connector step error: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        await query.message.edit_text(
            format_error_message(f"Error: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

async def handle_mgs_wizard_connector(
    update: Update, context: ContextTypes.DEFAULT_TYPE, connector: str
) -> None:
    """Handle connector selection"""
    config = get_controller_config(context)
    config["connector_name"] = connector
    set_controller_config(context, config)
    context.user_data["mgs_wizard_step"] = "trading_pair"
    await _mgs_show_pair_step(update, context)

async def _mgs_show_pair_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """MGS Wizard Step 2: Enter Trading Pair"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")

    context.user_data["bots_state"] = "mgs_wizard_input"
    context.user_data["mgs_wizard_step"] = "trading_pair"

    # Recent pairs from existing configs
    existing_configs = context.user_data.get("controller_configs_list", [])
    recent_pairs = []
    seen = set()
    for cfg in reversed(existing_configs):
        pair = cfg.get("trading_pair", "")
        if pair and pair not in seen:
            seen.add(pair)
            recent_pairs.append(pair)
            if len(recent_pairs) >= 6:
                break

    keyboard = []
    if recent_pairs:
        row = []
        for pair in recent_pairs:
            row.append(InlineKeyboardButton(pair, callback_data=f"bots:mgs_pair:{pair}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="bots:mgs_back_to_connector"),
        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
    ])

    is_perp = connector.endswith("_perpetual")
    total_steps = 7 if is_perp else 6

    # Escapa il connector
    escaped_connector = escape_markdown_v2(connector)

    await query.message.edit_text(
        rf"*🔲 Multi Grid Strike \- Step 2/{total_steps}*" + "\n\n"
        f"🏦 `{escaped_connector}`" + "\n\n"
        r"🔗 *Trading Pair*" + "\n\n"
        r"Select a recent pair or type a new one:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
async def handle_mgs_wizard_pair(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pair: str
) -> None:
    """Handle pair selection via button"""
    config = get_controller_config(context)
    config["trading_pair"] = pair
    set_controller_config(context, config)
    context.user_data["mgs_wizard_step"] = "grid_type"
    await _mgs_show_grid_type_step(update, context)

async def _mgs_show_grid_type_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """MGS Wizard Step 3: Select Strategy Type"""
    # Usa i dati salvati
    chat_id = context.user_data.get("mgs_wizard_chat_id")
    message_id = context.user_data.get("mgs_wizard_message_id")

    if not chat_id or not message_id:
        logger.error("MGS: No chat_id or message_id saved for grid_type step")
        return

    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    context.user_data["bots_state"] = "mgs_wizard_input"
    context.user_data["mgs_wizard_step"] = "grid_type"

    is_perp = connector.endswith("_perpetual")
    total_steps = 7 if is_perp else 6
    current_step = 3

    escaped_connector = escape_markdown_v2(connector)
    escaped_pair = escape_markdown_v2(pair)

    from .controllers.multi_grid_strike.config import GRID_TYPES

    keyboard = []
    for grid_type_key, grid_type_info in GRID_TYPES.items():
        # Crea un unico bottone per strategia con nome e descrizione su due righe
        button_text = f"{grid_type_info['label']}\n   {grid_type_info['description']}"
        keyboard.append([
            InlineKeyboardButton(
                button_text,
                callback_data=f"bots:mgs_grid_type:{grid_type_key}"
            )
        ])
    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="bots:mgs_back_to_pair"),
        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
    ])

    # ========== DEFINISCI message_text ==========
    message_text = (
        rf"*🔲 Multi Grid Strike \- Step {current_step}/{total_steps}*" + "\n\n"
        f"🏦 `{escaped_connector}` \\| 🔗 `{escaped_pair}`" + "\n\n"
        r"🎯 *Select Strategy Type*" + "\n\n"
        r"Choose how your grids will be structured:"
    )
    # ===========================================

    await context.bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def _mgs_show_num_grids_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """MGS Wizard Step 4: Select Number of Grids"""
    query = update.callback_query

    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    grid_type = config.get("grid_strategy_type", "accumulation_distribution")

    from .controllers.multi_grid_strike.config import GRID_TYPES
    grid_info = GRID_TYPES.get(grid_type, GRID_TYPES["accumulation_distribution"])
    min_grids = grid_info["min_grids"]
    max_grids = grid_info["max_grids"]
    default_grids = grid_info["default_grids"]

    context.user_data["bots_state"] = "mgs_wizard_input"
    context.user_data["mgs_wizard_step"] = "num_grids"

    # 🔧 FIX: Salva i limiti nel contesto per validazione successiva
    context.user_data["mgs_min_grids"] = min_grids
    context.user_data["mgs_max_grids"] = max_grids

    is_perp = connector.endswith("_perpetual")
    total_steps = 7 if is_perp else 6
    current_step = 4

    escaped_connector = escape_markdown_v2(connector)
    escaped_pair = escape_markdown_v2(pair)
    escaped_label = escape_markdown_v2(grid_info['label'])

    keyboard = []

    # Suggest 3 values
    suggested = [min_grids, default_grids, max_grids]
    if len(set(suggested)) < 3:
        suggested = [min_grids, (min_grids + max_grids) // 2, max_grids]

    row = []
    for num in suggested:
        row.append(InlineKeyboardButton(
            f"{num} grids", callback_data=f"bots:mgs_num_grids:{num}"
        ))
    keyboard.append(row)

    # Custom option
    keyboard.append([
        InlineKeyboardButton("✏️ Custom", callback_data="bots:mgs_num_grids:custom")
    ])

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="bots:mgs_back_to_grid_type"),
        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
    ])

    message_text = (
        rf"*🔲 Multi Grid Strike \- Step {current_step}/{total_steps}*" + "\n\n"
        f"🏦 `{escaped_connector}` \\| 🔗 `{escaped_pair}`" + "\n\n"
        f"📊 *Strategy:* {escaped_label}" + "\n\n"
        f"🔢 *Number of Grids*" + "\n"
        f"_Min: {min_grids} \\| Max: {max_grids} \\| Default: {default_grids}_" + "\n\n"
        r"Select or type a number:"
    )

    # Cancella il messaggio corrente
    if query and query.message:
        try:
            await query.message.delete()
        except Exception:
            pass

    # Invia nuovo messaggio
    new_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    context.user_data["mgs_wizard_message_id"] = new_msg.message_id
    context.user_data["mgs_wizard_chat_id"] = update.effective_chat.id

async def handle_mgs_grid_type(
    update: Update, context: ContextTypes.DEFAULT_TYPE, grid_type: str
) -> None:
    """Handle grid type selection"""
    config = get_controller_config(context)
    config["grid_strategy_type"] = grid_type
    set_controller_config(context, config)

    from .controllers.multi_grid_strike.config import GRID_TYPES
    grid_info = GRID_TYPES.get(grid_type, GRID_TYPES["accumulation_distribution"])
    default_grids = grid_info["default_grids"]

    context.user_data["mgs_wizard_step"] = "num_grids"
    context.user_data["mgs_default_grids"] = default_grids

    await _mgs_show_num_grids_step(update, context)


async def handle_mgs_num_grids(update, context, num_grids_str: str) -> None:
    """Handle number of grids selection"""
    config = get_controller_config(context)

    # 🔧 FIX: Recupera i limiti dal contesto
    min_grids = context.user_data.get("mgs_min_grids", 2)
    max_grids = context.user_data.get("mgs_max_grids", 20)

    if num_grids_str == "custom":
        context.user_data["bots_state"] = "mgs_wizard_input"
        context.user_data["mgs_wizard_step"] = "num_grids_custom"
        context.user_data["mgs_waiting_for_num_grids"] = True

        query = update.callback_query
        connector = config.get("connector_name", "")
        pair = config.get("trading_pair", "")

        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="bots:mgs_back_to_num_grids")]]
        await query.message.edit_text(
            rf"*🔲 Multi Grid Strike*" + "\n\n"
            f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`" + "\n\n"
            r"*Enter number of grids:*" + "\n"
            rf"_Type a number between {min_grids} and {max_grids}_",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    num_grids = int(num_grids_str)

    # 🔧 FIX: Valida il numero di griglie
    if num_grids < min_grids or num_grids > max_grids:
        query = update.callback_query
        await query.answer(f"Number must be between {min_grids} and {max_grids}", show_alert=True)
        return

    config["num_grids"] = num_grids
    set_controller_config(context, config)

    connector = config.get("connector_name", "")
    if connector.endswith("_perpetual"):
        context.user_data["mgs_wizard_step"] = "leverage"
        await _mgs_show_leverage_step(update, context)
    else:
        # Spot: salta leverage
        config["leverage"] = 1
        config["position_mode"] = "ONEWAY"
        set_controller_config(context, config)
        context.user_data["mgs_wizard_step"] = "total_amount_quote"
        await _mgs_show_amount_step(update, context)

async def _mgs_show_leverage_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """MGS Wizard Step 5 (perp only): Select Leverage"""
    query = update.callback_query

    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    context.user_data["bots_state"] = "mgs_wizard_input"
    context.user_data["mgs_wizard_step"] = "leverage"

    keyboard = [
        [
            InlineKeyboardButton("1x", callback_data="bots:mgs_leverage:1"),
            InlineKeyboardButton("5x", callback_data="bots:mgs_leverage:5"),
            InlineKeyboardButton("10x", callback_data="bots:mgs_leverage:10"),
        ],
        [
            InlineKeyboardButton("20x", callback_data="bots:mgs_leverage:20"),
            InlineKeyboardButton("50x", callback_data="bots:mgs_leverage:50"),
            InlineKeyboardButton("75x", callback_data="bots:mgs_leverage:75"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:mgs_back_to_num_grids"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    escaped_connector = escape_markdown_v2(connector)
    escaped_pair = escape_markdown_v2(pair)

    message_text = (
        rf"*🔲 Multi Grid Strike \- Step 5/7*" + "\n\n"
        f"🏦 `{escaped_connector}` \\| 🔗 `{escaped_pair}`" + "\n\n"
        r"⚡ *Select Leverage*" + "\n"
        r"_Or type a value \(e\.g\. 20\)_"
    )

    # ========== INVIA NUOVO MESSAGGIO ==========
    # Cancella il messaggio corrente
    if query and query.message:
        try:
            await query.message.delete()
        except Exception:
            pass

    # Invia nuovo messaggio
    new_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    # Salva il nuovo message_id
    context.user_data["mgs_wizard_message_id"] = new_msg.message_id
    context.user_data["mgs_wizard_chat_id"] = update.effective_chat.id


async def handle_mgs_wizard_leverage(update, context, leverage: int) -> None:
    """Handle leverage selection"""
    config = get_controller_config(context)
    config["leverage"] = leverage
    set_controller_config(context, config)

    # Step 6: Position Mode
    context.user_data["mgs_wizard_step"] = "position_mode"
    await _mgs_show_position_mode_step(update, context)

async def _mgs_show_position_mode_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """MGS Wizard Step: Select Position Mode (only for perpetual)"""
    query = update.callback_query

    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 1)

    context.user_data["bots_state"] = "mgs_wizard_input"
    context.user_data["mgs_wizard_step"] = "position_mode"

    is_perp = connector.endswith("_perpetual")
    total_steps = 7 if is_perp else 6
    current_step = 6 if is_perp else 5

    escaped_connector = escape_markdown_v2(connector)
    escaped_pair = escape_markdown_v2(pair)

    keyboard = [
        [
            InlineKeyboardButton("🔒 ONEWAY", callback_data="bots:mgs_position_mode:ONEWAY"),
            InlineKeyboardButton("🔄 HEDGE", callback_data="bots:mgs_position_mode:HEDGE"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:mgs_back_to_leverage"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    message_text = (
        rf"*🔲 Multi Grid Strike \- Step {current_step}/{total_steps}*" + "\n\n"
        f"🏦 `{escaped_connector}` \\| 🔗 `{escaped_pair}` \\| ⚡ `{leverage}x`" + "\n\n"
        r"🎯 *Position Mode*" + "\n\n"
        r"• *ONEWAY*: Can only hold positions in one direction \(long OR short\)" + "\n"
        r"• *HEDGE*: Can hold both long and short positions simultaneously" + "\n\n"
        r"_Select your position mode:_"
    )

    # ========== INVIA NUOVO MESSAGGIO ==========
    # Cancella il messaggio corrente
    if query and query.message:
        try:
            await query.message.delete()
        except Exception:
            pass

    # Invia nuovo messaggio
    new_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    # Salva il nuovo message_id
    context.user_data["mgs_wizard_message_id"] = new_msg.message_id
    context.user_data["mgs_wizard_chat_id"] = update.effective_chat.id
async def handle_mgs_back_to_position_mode(update, context) -> None:
    """Go back to position mode step"""
    context.user_data["mgs_wizard_step"] = "position_mode"
    await _mgs_show_position_mode_step(update, context)

async def handle_mgs_position_mode(update, context, mode: str) -> None:
    """Handle position mode selection"""
    config = get_controller_config(context)
    config["position_mode"] = mode
    set_controller_config(context, config)

    # Step 7: Total Amount
    context.user_data["mgs_wizard_step"] = "total_amount_quote"
    await _mgs_show_amount_step(update, context)

async def _mgs_show_amount_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """MGS Wizard Step: Enter Total Amount"""
    # Determina chat_id e message_id
    if update.callback_query:
        chat_id = update.callback_query.message.chat_id
        # Non abbiamo un message_id esistente perché abbiamo cancellato il vecchio
        message_id = None
    else:
        chat_id = update.effective_chat.id
        message_id = None

    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 1)
    grid_type = config.get("grid_strategy_type", "accumulation_distribution")
    num_grids = config.get("num_grids", 2)

    context.user_data["bots_state"] = "mgs_wizard_input"
    context.user_data["mgs_wizard_step"] = "total_amount_quote"

    is_perp = connector.endswith("_perpetual")
    total_steps = 7 if is_perp else 6
    current_step = 7 if is_perp else 5

    escaped_connector = escape_markdown_v2(connector)
    escaped_pair = escape_markdown_v2(pair)
    escaped_grid_type = escape_markdown_v2(grid_type)

    # Fetch balance
    balance_text = ""
    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        balances = await get_cex_balances(context.user_data, client, connector)
        quote = pair.split("-")[1] if "-" in pair else "USDT"
        available = balances.get(quote, {}).get("available", 0)
        if available:
            balance_text = f"\n\n💰 Available `{escape_markdown_v2(quote)}`: `{available:,.2f}`"
    except Exception:
        pass

    keyboard = [
        [
            InlineKeyboardButton("$100", callback_data="bots:mgs_amount:100"),
            InlineKeyboardButton("$500", callback_data="bots:mgs_amount:500"),
            InlineKeyboardButton("$1000", callback_data="bots:mgs_amount:1000"),
        ],
        [
            InlineKeyboardButton("$2000", callback_data="bots:mgs_amount:2000"),
            InlineKeyboardButton("$5000", callback_data="bots:mgs_amount:5000"),
            InlineKeyboardButton("$10000", callback_data="bots:mgs_amount:10000"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:mgs_back_to_leverage" if is_perp else "bots:mgs_back_to_num_grids"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    message_text = (
        rf"*🔲 Multi Grid Strike \- Step {current_step}/{total_steps}*" + "\n\n"
        f"🏦 `{escaped_connector}` \\| 🔗 `{escaped_pair}` \\| ⚡ `{leverage}x`" + "\n"
        f"📊 `{escaped_grid_type}` \\| 🔢 `{num_grids}` grids" + balance_text + "\n\n"
        r"💰 *Total Amount \(USDT\)*" + "\n"
        r"_Select or type an amount:_"
    )

    # ========== INVIA NUOVO MESSAGGIO (non edit) ==========
    try:
        # Cancella eventuale messaggio precedente
        old_msg_id = context.user_data.get("mgs_wizard_message_id")
        if old_msg_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=old_msg_id)
            except Exception:
                pass
    except Exception:
        pass

    # Invia nuovo messaggio
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    context.user_data["mgs_wizard_message_id"] = msg.message_id
    context.user_data["mgs_wizard_chat_id"] = chat_id

    # =============================================
async def handle_mgs_wizard_amount(
    update: Update, context: ContextTypes.DEFAULT_TYPE, amount: float
) -> None:
    """Handle amount selection"""
    query = update.callback_query
    config = get_controller_config(context)
    config["total_amount_quote"] = amount
    set_controller_config(context, config)

    pair = config.get("trading_pair", "")
    await query.message.edit_text(
        r"*🔲 Multi Grid Strike \- New Config*" + "\n\n"
        f"⏳ *Loading market data for* `{escape_markdown_v2(pair)}`\\.\\.\\.  " + "\n\n"
        r"_Fetching price and generating grids\.\.\._",
        parse_mode="MarkdownV2",
    )

    context.user_data["mgs_wizard_step"] = "final"
    await _mgs_show_final_step(update, context)

async def _mgs_show_final_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE, interval: str = None
) -> None:
    """MGS Final Step: Generate grids, show chart + config summary"""
    import html

    if update.callback_query:
        query = update.callback_query
        msg = query.message
    else:
        msg = update.message

    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    total_amount = config.get("total_amount_quote", 1000)
    leverage = config.get("leverage", 1)
    position_mode = config.get("position_mode", "HEDGE")
    grid_type = config.get("grid_strategy_type", "accumulation_distribution")
    num_grids = config.get("num_grids", 2)

    logger.info(f"MGS FINAL STEP: grid_type={grid_type}, num_grids={num_grids}, total_amount={total_amount}")

    config["num_grids"] = num_grids
    set_controller_config(context, config)

    if interval is None:
        interval = context.user_data.get("mgs_chart_interval", "5m")
    context.user_data["mgs_chart_interval"] = interval

    current_price = context.user_data.get("mgs_current_price")
    candles = context.user_data.get("mgs_candles")
    natr = context.user_data.get("mgs_natr")

    try:
        cached_interval = context.user_data.get("mgs_candles_interval", "5m")
        if not current_price or interval != cached_interval:
            try:
                await msg.edit_text(
                    "<b>🔲 Multi Grid Strike - New Config</b>\n\n"
                    f"⏳ Fetching market data for <code>{html.escape(pair)}</code>...",
                    parse_mode="HTML",
                )
            except Exception:
                pass

            client, _ = await get_bots_client(chat_id, context.user_data)
            current_price = await fetch_current_price(client, connector, pair)

            if current_price:
                context.user_data["mgs_current_price"] = current_price
                candles = await fetch_candles(
                    client, connector, pair, interval=interval, max_records=420
                )
                context.user_data["mgs_candles"] = candles
                context.user_data["mgs_candles_interval"] = interval

                candles_list = candles.get("data", []) if isinstance(candles, dict) else (candles or [])
                if candles_list:
                    natr = calculate_natr(candles_list, period=14)
                    context.user_data["mgs_natr"] = natr

                try:
                    rules = await get_trading_rules(context.user_data, client, connector)
                    context.user_data["mgs_trading_rules"] = rules.get(pair, {})
                except Exception:
                    context.user_data["mgs_trading_rules"] = {}

        if not current_price:
            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="bots:main_menu")]]
            await msg.edit_text(
                f"<b>❌ Error</b>\n\nCould not fetch price for <code>{html.escape(pair)}</code>.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        candles_list = candles.get("data", []) if isinstance(candles, dict) else (candles or [])
        if candles_list:
            last_close = candles_list[-1].get("close") or candles_list[-1].get("c")
            if last_close:
                current_price = float(last_close)
                context.user_data["mgs_current_price"] = current_price

        min_order_amount = config.get("min_order_amount_quote", 5)
        trading_rules = context.user_data.get("mgs_trading_rules", {})

        from .controllers.multi_grid_strike.grid_analysis import suggest_multi_grid_params
        suggestion = suggest_multi_grid_params(
            current_price=current_price,
            natr=natr or 0.02,
            total_amount=total_amount,
            min_order_amount=min_order_amount,
            num_grids=num_grids,
            grid_type=grid_type
        )

        config["grids"] = suggestion["grids"]
        set_controller_config(context, config)

        if not config.get("id"):
            existing_configs = context.user_data.get("controller_configs_list", [])
            from .controllers.multi_grid_strike import generate_id as mgs_generate_id
            config["id"] = mgs_generate_id(config, existing_configs)
            set_controller_config(context, config)

        min_spread = config.get("min_spread_between_orders", 0.001)
        take_profit = config.get("triple_barrier_config", {}).get("take_profit", 0.001)
        natr_pct = f"{natr*100:.2f}%" if natr else "N/A"

        # ========== BUILD GRID DISPLAY CON LIMITE DI GRIGLIE VISUALIZZATE ==========
        # Per evitare caption troppo lunghe, mostriamo massimo 5 griglie nel messaggio
        max_grids_to_show = 5
        total_grids = len(suggestion["grids"])

        grid_lines = []
        for i, grid in enumerate(suggestion["grids"][:max_grids_to_show]):
            side_str = "LONG" if grid["side"] == SIDE_LONG else "SHORT"
            grid_lines.append(
                f"<b>Grid {i+1}:</b> <code>{html.escape(grid['grid_id'])}</code> ({side_str}, {grid['amount_quote_pct']*100:.0f}%)"
            )
            grid_lines.append(f"  <code>start={grid['start_price']:.6g}</code>")
            grid_lines.append(f"  <code>end={grid['end_price']:.6g}</code>")
            grid_lines.append(f"  <code>limit={grid['limit_price']:.6g}</code>")

        if total_grids > max_grids_to_show:
            grid_lines.append(f"<i>...and {total_grids - max_grids_to_show} more grids (edit via Configs menu)</i>")
        # ========================================================================

        context.user_data["bots_state"] = "mgs_wizard_input"
        context.user_data["mgs_wizard_step"] = "final"

        interval_options = ["1m", "5m", "15m", "1h", "4h"]
        interval_row = [
            InlineKeyboardButton(
                f"✓ {opt}" if opt == interval else opt,
                callback_data=f"bots:mgs_interval:{opt}"
            )
            for opt in interval_options
        ]

        keyboard = [
            interval_row,
            [InlineKeyboardButton("💾 Save Config", callback_data="bots:mgs_save")],
            [
                InlineKeyboardButton("⬅️ Back", callback_data="bots:mgs_back_to_amount"),
                InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
            ],
        ]

        is_perp = connector.endswith("_perpetual")
        final_step = 7 if is_perp else 6

        # ========== COSTRUISCI CONFIG_TEXT CON TAG BILANCIATI ==========
        # Usa una lista e poi join per evitare problemi di concatenazione
        config_text_parts = [
            f"<b>🔲 Multi Grid Strike - Step {final_step}/{final_step} (Final)</b>",
            "",
            f"<b>{html.escape(pair)}</b>",
            f"Price: <code>{current_price:,.6g}</code> | NATR: <code>{html.escape(natr_pct)}</code>",
            "",
            f"<code>connector_name={html.escape(connector)}</code>",
            f"<code>trading_pair={html.escape(pair)}</code>",
            f"<code>total_amount_quote={total_amount:.0f}</code>",
            f"<code>leverage={leverage}</code>",
            f"<code>position_mode={html.escape(position_mode)}</code>",
            f"<code>min_spread_between_orders={min_spread}</code>",
            f"<code>min_order_amount_quote={min_order_amount}</code>",
            f"<code>max_open_orders={config.get('max_open_orders', 2)}</code>",
            f"<code>take_profit={take_profit}</code>",
            f"<code>keep_position={str(config.get('keep_position', False)).lower()}</code>",
            "",
        ]
        config_text_parts.extend(grid_lines)
        config_text_parts.extend(["", "<i>Edit individual grids via Configs menu after saving</i>"])

        config_text = "\n".join(config_text_parts)
        # ================================================================

        # ========== TRONCAMENTO PIÙ SICURO ==========
        MAX_CAPTION_LEN = 950
        if len(config_text) > MAX_CAPTION_LEN:
            truncation_note = "\n\n<i>...truncated due to length limit. Edit via Configs menu.</i>"
            max_allowed = MAX_CAPTION_LEN - len(truncation_note)
            # Cerca l'ultimo newline prima del limite per non troncare a metà riga
            last_newline = config_text.rfind('\n', 0, max_allowed)
            if last_newline > 0:
                config_text = config_text[:last_newline] + truncation_note
            else:
                config_text = config_text[:max_allowed] + truncation_note
        # ===========================================

        # Invia la foto
        if candles_list and suggestion["grids"]:
            first_grid = suggestion["grids"][0]
            chart_bytes = generate_candles_chart(
                candles_list,
                pair,
                start_price=first_grid["start_price"],
                end_price=first_grid["end_price"],
                limit_price=first_grid["limit_price"],
                current_price=current_price,
                side=first_grid["side"],
            )
            try:
                await context.bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=msg.message_id,
                    media=InputMediaPhoto(media=chart_bytes, caption=config_text, parse_mode="HTML"),
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["mgs_wizard_message_id"] = msg.message_id
            except Exception as e:
                logger.warning(f"edit_message_media fallito: {e}, fallback a delete+send")
                try:
                    await msg.delete()
                except Exception:
                    pass
                new_msg = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=chart_bytes,
                    caption=config_text,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["mgs_wizard_message_id"] = new_msg.message_id
                context.user_data["mgs_wizard_chat_id"] = chat_id
        else:
            try:
                await msg.edit_text(
                    text=config_text,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["mgs_wizard_message_id"] = msg.message_id
            except Exception as e:
                try:
                    await msg.delete()
                except Exception:
                    pass
                new_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=config_text,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["mgs_wizard_message_id"] = new_msg.message_id
                context.user_data["mgs_wizard_chat_id"] = chat_id

    except Exception as e:
        logger.error(f"MGS final step error: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        try:
            await msg.edit_text(
                f"<b>Error</b>\n\n{html.escape(str(e))}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception:
            pass
async def handle_mgs_interval_change(
    update: Update, context: ContextTypes.DEFAULT_TYPE, interval: str
) -> None:
    """Change chart interval"""
    query = update.callback_query
    context.user_data["mgs_candles"] = None
    context.user_data["mgs_candles_interval"] = None

    # Ricrea il final step con un nuovo messaggio
    await _mgs_show_final_step(update, context, interval=interval)


async def handle_mgs_save(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Save the Multi Grid Strike configuration"""
    query = update.callback_query
    config = get_controller_config(context)

    # ========== PULISCI I CAMPI NON NECESSARI ==========
    # Rimuovi campi interni del wizard
    config.pop("grid_strategy_type", None)
    config.pop("num_grids", None)
    config.pop("candles_config", None)
    config.pop("initial_positions", None)

    # Rimuovi 'description' da ogni grid
    if "grids" in config:
        for grid in config["grids"]:
            grid.pop("description", None)
    # ========== NUOVO: PULISCI triple_barrier_config (SOLO CAMPI VALIDI) ==========
    # Multi Grid Strike supporta SOLO: open_order_type, take_profit, take_profit_order_type
    valid_tp_fields = {"open_order_type", "take_profit", "take_profit_order_type"}

    if "triple_barrier_config" in config:
        tp_config = config["triple_barrier_config"]
        if isinstance(tp_config, dict):
            # Filtra solo i campi validi
            cleaned_tp = {
                k: v for k, v in tp_config.items()
                if k in valid_tp_fields and v is not None
            }
            if cleaned_tp:
                config["triple_barrier_config"] = cleaned_tp
            else:
                config.pop("triple_barrier_config", None)
        else:
            config.pop("triple_barrier_config", None)

    # ========== NUOVO: RIMUOVI CAMPI NON STANDARD (ereditati da altri wizard) ==========
    invalid_fields = [
        "candles_connector",
        "candles_trading_pair",
        "interval",
        "bb_length",
        "bb_std",
        "bb_long_threshold",
        "bb_short_threshold",
        "macd_fast",
        "macd_slow",
        "macd_signal",
        "dca_spreads",
        "dca_amounts_pct",
        "dynamic_order_spread",
        "dynamic_target",
        "cooldown_time",
        "max_executors_per_side",
        "stop_loss",
        "time_limit",
        "trailing_stop",
        "trailing_stop_activation",
        "trailing_stop_delta",
    ]
    for field in invalid_fields:
        config.pop(field, None)

    # ========== GARANTISCI CHE I CAMPI OBBLIGATORI SIANO PRESENTI ==========
    # Se manca triple_barrier_config ma c'è take_profit da qualche parte
    if "take_profit" in config and "triple_barrier_config" not in config:
        config["triple_barrier_config"] = {
            "take_profit": config.pop("take_profit")
        }

    # Assicurati che open_order_type e take_profit_order_type abbiano valori validi
    if "triple_barrier_config" in config:
        if "open_order_type" not in config["triple_barrier_config"]:
            config["triple_barrier_config"]["open_order_type"] = ORDER_TYPE_LIMIT_MAKER
        if "take_profit_order_type" not in config["triple_barrier_config"]:
            config["triple_barrier_config"]["take_profit_order_type"] = ORDER_TYPE_LIMIT_MAKER

    # ===================================================

    config_id = config.get("id", "")
    chat_id = query.message.chat_id

    try:
        await query.message.delete()
    except Exception:
        pass

    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="Saving configuration `" + escape_markdown_v2(config_id) + "`\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        await client.controllers.create_or_update_controller_config(config_id, config)

        # Cleanup wizard state
        for key in ["mgs_wizard_step", "mgs_wizard_message_id", "mgs_wizard_chat_id",
                    "mgs_current_price", "mgs_candles", "mgs_candles_interval",
                    "mgs_chart_interval", "mgs_natr", "mgs_trading_rules",
                    "mgs_default_grids", "mgs_waiting_for_num_grids"]:
            context.user_data.pop(key, None)
        context.user_data.pop("bots_state", None)

        keyboard = [
            [InlineKeyboardButton("Create Another", callback_data="bots:new_multi_grid_strike")],
            [InlineKeyboardButton("Back to Configs", callback_data="bots:controller_configs")],
        ]
        saved_msg = (
            "*Config Saved\\!*\n\n"
            "Controller `" + escape_markdown_v2(config_id) + "` saved successfully\\.\n"
            "Use 📋 Configs menu to edit individual grids or parameters\\."
        )
        await status_msg.edit_text(
            saved_msg,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"MGS save error: {e}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton("Try Again", callback_data="bots:mgs_save")],
            [InlineKeyboardButton("Back", callback_data="bots:controller_configs")],
        ]
        await status_msg.edit_text(
            format_error_message(f"Failed to save: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

# ============================================
# MGS BACK HANDLERS
# ============================================

async def handle_mgs_back_to_connector(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    context.user_data["mgs_wizard_step"] = "connector_name"
    await _mgs_show_connector_step(update, context)


async def handle_mgs_back_to_pair(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    context.user_data["mgs_wizard_step"] = "trading_pair"
    await _mgs_show_pair_step(update, context)


async def handle_mgs_back_to_grid_type(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    context.user_data["mgs_wizard_step"] = "grid_type"
    await _mgs_show_grid_type_step(update, context)

async def handle_mgs_back_to_leverage(update, context) -> None:
    config = get_controller_config(context)
    if config.get("connector_name", "").endswith("_perpetual"):
        context.user_data["mgs_wizard_step"] = "leverage"

        # Cancella messaggio corrente
        query = update.callback_query
        try:
            await query.message.delete()
        except Exception:
            pass

        await _mgs_show_leverage_step(update, context)
    else:
        await handle_mgs_back_to_num_grids(update, context)

async def handle_mgs_back_to_num_grids(update, context) -> None:
    """Go back to num_grids step"""
    query = update.callback_query

    # Cancella il messaggio corrente
    if query and query.message:
        try:
            await query.message.delete()
        except Exception:
            pass

    context.user_data["mgs_wizard_step"] = "num_grids"
    await _mgs_show_num_grids_step(update, context)

async def handle_mgs_back_to_amount(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Go back to amount step"""
    query = update.callback_query

    # Cancella il messaggio corrente
    if query and query.message:
        try:
            await query.message.delete()
        except Exception:
            pass

    context.user_data["mgs_wizard_step"] = "total_amount_quote"
    context.user_data.pop("mgs_current_price", None)
    context.user_data.pop("mgs_candles", None)

    await _mgs_show_amount_step(update, context)


# ============================================
# MGS PAIR SELECTION HANDLER
# ============================================

async def handle_mgs_pair_select(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pair: str
) -> None:
    """Handle pair selection via button"""
    await handle_mgs_wizard_pair(update, context, pair)


# ============================================
# MGS TEXT INPUT PROCESSOR
# ============================================
async def process_mgs_wizard_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str
) -> None:
    """Process text input during MGS wizard"""
    step = context.user_data.get("mgs_wizard_step")
    logger.info(f"🔍 MGS DEBUG: step={step}, input={user_input}, bots_state={context.user_data.get('bots_state')}")
    chat_id = update.effective_chat.id
    config = get_controller_config(context)
    message_id = context.user_data.get("mgs_wizard_message_id")
    wizard_chat_id = context.user_data.get("mgs_wizard_chat_id", chat_id)

    try:
        try:
            await update.message.delete()
        except Exception:
            pass

        # ========== GESTISCI INPUT MANUALE PER TRADING PAIR ==========
        if step == "trading_pair":
            pair = user_input.upper().strip().replace("/", "-").replace("_", "-")
            connector = config.get("connector_name", "")

            # Validazione base: deve contenere un trattino
            if "-" not in pair:
                keyboard = [
                    [InlineKeyboardButton("⬅️ Back", callback_data="bots:mgs_back_to_connector")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")],
                ]
                error_text = (
                    r"*🔲 Multi Grid Strike \- Step 2*" + "\n\n"
                    f"🏦 `{escape_markdown_v2(connector)}`\n\n"
                    r"⚠️ *Invalid format\. Use BASE\-QUOTE \(e\.g\. BTC\-USDT\)*" + "\n\n"
                    r"Type the pair again \(e\.g\. `BTC\-USDT`\):"
                )
                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id,
                        message_id=message_id,
                        text=error_text,
                        parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                else:
                    msg = await context.bot.send_message(
                        chat_id=chat_id,
                        text=error_text,
                        parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    context.user_data["mgs_wizard_message_id"] = msg.message_id
                return

            # Valida il trading pair sull'exchange
            client, _ = await get_bots_client(chat_id, context.user_data)
            is_valid, error_msg, suggestions, correct_pair = (
                await validate_trading_pair(context.user_data, client, connector, pair)
            )

            if not is_valid:
                # Mostra suggerimenti se disponibili
                keyboard = []
                for sugg in suggestions[:4]:
                    keyboard.append([InlineKeyboardButton(sugg, callback_data=f"bots:mgs_pair:{sugg}")])
                keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="bots:mgs_back_to_connector")])
                keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")])

                error_text = (
                    r"*🔲 Multi Grid Strike \- Step 2*" + "\n\n"
                    f"❌ `{escape_markdown_v2(pair)}` not found on `{escape_markdown_v2(connector)}`\\.\n\n"
                    r"*Did you mean?*"
                )
                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id,
                        message_id=message_id,
                        text=error_text,
                        parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                else:
                    msg = await context.bot.send_message(
                        chat_id=chat_id,
                        text=error_text,
                        parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    context.user_data["mgs_wizard_message_id"] = msg.message_id
                return

            if correct_pair:
                pair = correct_pair

            config["trading_pair"] = pair
            for key in ["mgs_current_price", "mgs_candles", "mgs_candles_interval", "mgs_natr"]:
                context.user_data.pop(key, None)
            set_controller_config(context, config)
            # ========== DEBUG ==========
            logger.info(f"🔍 MGS: pair set to {pair}, now calling _mgs_show_grid_type_step")
            # ===========================
            # Vai allo step successivo (grid_type)
            context.user_data["mgs_wizard_step"] = "grid_type"
            await _mgs_show_grid_type_step(update, context)

        # ========== GESTISCI INPUT PER NUM_GRIDS_CUSTOM ==========
        elif step == "num_grids_custom":
            try:
                num_grids = int(user_input.strip())

                # 🔧 FIX: Recupera i limiti dal contesto
                min_grids = context.user_data.get("mgs_min_grids", 2)
                max_grids = context.user_data.get("mgs_max_grids", 20)

                if num_grids < min_grids or num_grids > max_grids:
                    raise ValueError(f"Number must be between {min_grids} and {max_grids}")

                config["num_grids"] = num_grids
                set_controller_config(context, config)
                context.user_data.pop("mgs_waiting_for_num_grids", None)

                connector = config.get("connector_name", "")
                if connector.endswith("_perpetual"):
                    context.user_data["mgs_wizard_step"] = "leverage"
                    await _mgs_show_leverage_step(update, context)
                else:
                    config["leverage"] = 1
                    config["position_mode"] = "ONEWAY"
                    set_controller_config(context, config)
                    context.user_data["mgs_wizard_step"] = "total_amount_quote"
                    await _mgs_show_amount_step(update, context)
            except ValueError as e:
                keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="bots:mgs_back_to_num_grids")]]
                await context.bot.edit_message_text(
                    chat_id=wizard_chat_id,
                    message_id=message_id,
                    text=rf"*🔲 Multi Grid Strike*" + "\n\n"
                    f"❌ *Invalid number:* {escape_markdown_v2(str(e))}" + "\n\n"
                    rf"*Enter number of grids ({min_grids}-{max_grids}):*",
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
        elif step == "leverage":
            try:
                val = int(float(user_input.strip().lower().replace("x", "")))
                config["leverage"] = val
                set_controller_config(context, config)
                context.user_data["mgs_wizard_step"] = "total_amount_quote"
                await _mgs_show_amount_step(update, context)
            except ValueError:
                keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="bots:mgs_back_to_leverage")]]
                await context.bot.edit_message_text(
                    chat_id=wizard_chat_id,
                    message_id=message_id,
                    text=r"*🔲 Multi Grid Strike*" + "\n\n"
                    r"❌ *Invalid leverage*" + "\n\n"
                    r"Enter a positive number \(e\.g\. 20\):",
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )

        elif step == "total_amount_quote":
            try:
                amount = float(user_input.strip().replace("$", "").replace(",", ""))
                config["total_amount_quote"] = amount
                set_controller_config(context, config)
                context.user_data["mgs_wizard_step"] = "final"
                # Show loading
                tmp = await context.bot.send_message(
                    chat_id=wizard_chat_id,
                    text=r"*🔲 Multi Grid Strike*" + "\n\n"
                         f"⏳ Loading market data for `{escape_markdown_v2(config.get('trading_pair', ''))}`\\.\\.\\.",
                    parse_mode="MarkdownV2",
                )
                context.user_data["mgs_wizard_message_id"] = tmp.message_id
                await _mgs_show_final_step(update, context)
            except ValueError:
                keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="bots:mgs_back_to_amount")]]
                await context.bot.edit_message_text(
                    chat_id=wizard_chat_id,
                    message_id=message_id,
                    text=r"*🔲 Multi Grid Strike*" + "\n\n"
                    r"❌ *Invalid amount*" + "\n\n"
                    r"Enter a positive number \(e\.g\. 500\):",
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )

        elif step == "final":
            # Handle field=value edits
            if "=" in user_input:
                for line in user_input.strip().split("\n"):
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    field, value = line.split("=", 1)
                    field = field.strip().lower()
                    value = value.strip()
                    try:
                        if field in ("min_spread_between_orders", "min_spread"):
                            val = float(value.replace("%", ""))
                            config["min_spread_between_orders"] = val / 100 if val > 1 else val
                        elif field in ("take_profit", "tp"):
                            val = float(value.replace("%", ""))
                            if "triple_barrier_config" not in config:
                                config["triple_barrier_config"] = {}
                            config["triple_barrier_config"]["take_profit"] = val / 100 if val > 1 else val
                        elif field in ("total_amount_quote", "amount"):
                            config["total_amount_quote"] = float(value)
                        elif field == "leverage":
                            config["leverage"] = int(float(value))
                        elif field == "max_open_orders":
                            config["max_open_orders"] = int(float(value))
                        elif field == "min_order_amount_quote":
                            config["min_order_amount_quote"] = float(value)
                        elif field == "keep_position":
                            config["keep_position"] = value.lower() in ("true", "yes", "1")
                        elif field == "position_mode":
                            config["position_mode"] = value.upper()
                    except Exception:
                        pass
                set_controller_config(context, config)
                # Refresh the final step
                await _mgs_show_final_step(update, context)

    except Exception as e:
        logger.error(f"MGS wizard input error: {e}", exc_info=True)

# ============================================
# GENERIC SAVE HANDLER (for wizard fallback)
# ============================================
async def _show_new_generic_form(
    update, context, controller_type: str
) -> None:
    """Generic handler for new bot configs - saves with defaults."""
    from .controllers import get_controller
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()
        context.user_data["controller_configs_list"] = configs
    except Exception as e:
        logger.warning(f"Could not fetch existing configs for sequencing: {e}")
        configs = []

    ctrl_cls = get_controller(controller_type)
    config = ctrl_cls.get_defaults() if ctrl_cls else {}
    config_id = ctrl_cls.generate_id(config, configs) if ctrl_cls else f"001_{controller_type}"
    config["id"] = config_id

    lines = [
        f"*🆕 New {escape_markdown_v2(ctrl_cls.display_name if ctrl_cls else controller_type)}*",
        "",
        f"`{escape_markdown_v2(config_id)}`",
        "",
        "_Config created with default values\\._",
        "_Use 📋 Configs menu to edit fields\\._",
        "",
    ]
    for key, value in config.items():
        if key in ("controller_name", "controller_type"):
            continue
        lines.append(f"`{escape_markdown_v2(str(key))}={escape_markdown_v2(str(value))}`")

    keyboard = [
        [InlineKeyboardButton("💾 Save with defaults", callback_data=f"bots:generic_save:{controller_type}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="bots:controller_configs")],
    ]

    set_controller_config(context, config)
    context.user_data["generic_pending_id"] = config_id

    try:
        await query.message.edit_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        logger.error(f"Error showing generic form: {e}", exc_info=True)


async def handle_generic_save(
    update, context, controller_type: str
) -> None:
    """Save a generic bot config with default values."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)
    config_id = config.get("id", context.user_data.get("generic_pending_id", f"001_{controller_type}"))

    try:
        await query.message.delete()
    except Exception:
        pass

    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="Saving `" + escape_markdown_v2(config_id) + "`\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        await client.controllers.create_or_update_controller_config(config_id, config)

        keyboard = [
            [InlineKeyboardButton("📋 Back to Configs", callback_data="bots:controller_configs")],
        ]
        saved_text = (
            "*✅ Config Saved\\!*\n\n"
            + "`" + escape_markdown_v2(config_id) + "` saved with default values\\.\n"
            + "Open 📋 Configs to edit the fields\\."
        )
        await status_msg.edit_text(
            saved_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        logger.error(f"Error saving generic config: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:controller_configs")]]
        await status_msg.edit_text(
            format_error_message(f"Failed to save: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

# ============================================
# DMAN V3 WIZARD
# ============================================
# Steps: connector → pair → (leverage) → amount → interval+chart → save
# Prefisso handler: dman_

async def show_new_dman_v3_form(
    update, context
) -> None:
    """Start the DMan V3 wizard - Step 1: Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    # ========== PULISCI STATI DI ALTRI WIZARD ==========
    # Rimuovi tutti i message_id e chat_id di altri wizard
    for key in list(context.user_data.keys()):
        if key.endswith("_wizard_message_id") or key.endswith("_wizard_chat_id"):
            context.user_data.pop(key, None)
        if key.endswith("_wizard_step"):
            context.user_data.pop(key, None)
    # ===================================================

    # Clear cached data
    for key in ["dman_current_price", "dman_candles", "dman_candles_interval",
                "dman_chart_interval", "dman_trading_rules"]:
        context.user_data.pop(key, None)

    # Fetch existing configs for sequence numbering
    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()
        context.user_data["controller_configs_list"] = configs
    except Exception as e:
        logger.warning(f"Could not fetch existing configs: {e}")

    init_new_controller_config(context, "dman_v3")
    context.user_data["bots_state"] = "dman_wizard"
    context.user_data["dman_wizard_step"] = "connector_name"
    context.user_data["dman_wizard_message_id"] = query.message.message_id
    context.user_data["dman_wizard_chat_id"] = query.message.chat_id

    await _dman_show_connector_step(update, context)


async def _dman_show_connector_step(update, context) -> None:
    """DMan Step 1: Select Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, server_name = await get_bots_client(chat_id, context.user_data)
        cex_connectors = await get_available_cex_connectors(
            context.user_data, client, server_name=server_name
        )

        if not cex_connectors:
            keyboard = [
                [InlineKeyboardButton("🔑 Configure API Keys", callback_data="config_api_keys")],
                [InlineKeyboardButton("« Back", callback_data="bots:main_menu")],
            ]
            await query.message.edit_text(
                r"*📉 DMan V3 \- New Config*" + "\n\n"
                r"⚠️ No CEX connectors available\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        keyboard = []
        row = []
        for connector in cex_connectors:
            row.append(InlineKeyboardButton(
                f"🏦 {connector}", callback_data=f"bots:dman_connector:{connector}"
            ))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")])

        await query.message.edit_text(
            r"*📉 DMan V3*" + "\n\n"
            r"Mean reversion strategy using Bollinger Bands to detect overbought/oversold "
            r"conditions, then enters with DCA orders at multiple levels\." + "\n\n"
            r"─────────────────────────" + "\n\n"
            r"*Step 1: Select Exchange*",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True,
        )

    except Exception as e:
        logger.error(f"DMan connector step error: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        await query.message.edit_text(
            format_error_message(f"Error: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_dman_wizard_connector(update, context, connector: str) -> None:
    """Handle connector selection"""
    config = get_controller_config(context)
    config["connector_name"] = connector
    # Auto-set candles connector = same exchange
    config["candles_connector"] = connector
    set_controller_config(context, config)
    context.user_data["dman_wizard_step"] = "trading_pair"
    await _dman_show_pair_step(update, context)

async def _dman_show_pair_step(update, context) -> None:
    """DMan Step 2: Select Trading Pair"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")

    context.user_data["bots_state"] = "dman_wizard_input"
    context.user_data["dman_wizard_step"] = "trading_pair"

    # Recent pairs from existing configs
    existing_configs = context.user_data.get("controller_configs_list", [])
    recent_pairs = []
    seen = set()
    for cfg in reversed(existing_configs):
        pair = cfg.get("trading_pair", "")
        if pair and pair not in seen:
            seen.add(pair)
            recent_pairs.append(pair)
            if len(recent_pairs) >= 6:
                break

    keyboard = []
    if recent_pairs:
        row = []
        for pair in recent_pairs:
            row.append(InlineKeyboardButton(pair, callback_data=f"bots:dman_pair:{pair}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="bots:dman_back_to_connector"),
        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
    ])

    is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()
    total_steps = 6 if is_perp else 4
    current_step = 2
    message_text = (
        rf"*📉 DMan V3 \- Step {current_step}/{total_steps}*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}`" + "\n\n"
        r"🔗 *Trading Pair*" + "\n\n"
        r"Select a recent pair or type a new one:"
    )

    # ========== SALVA MESSAGE_ID ==========
    try:
        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        context.user_data["dman_wizard_message_id"] = query.message.message_id
    except Exception:
        try:
            await query.message.delete()
        except Exception:
            pass
        new_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        context.user_data["dman_wizard_message_id"] = new_msg.message_id
        context.user_data["dman_wizard_chat_id"] = query.message.chat_id

async def handle_dman_wizard_pair(update, context, pair: str) -> None:
    """Handle pair selection"""
    config = get_controller_config(context)
    config["trading_pair"] = pair
    config["candles_trading_pair"] = pair
    set_controller_config(context, config)

    connector = config.get("connector_name", "").lower()
    is_perp = "_perpetual" in connector or "_margin" in connector

    if is_perp:
        # Step: Leverage
        context.user_data["dman_wizard_step"] = "leverage"
        await _dman_show_leverage_step(update, context)
    else:
        config["leverage"] = 1
        config["position_mode"] = "ONEWAY"  # spot → ONEWAY obbligatorio
        set_controller_config(context, config)
        context.user_data["dman_wizard_step"] = "total_amount_quote"
        await _dman_show_amount_step(update, context)

async def _dman_show_leverage_step(update, context) -> None:
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    context.user_data["bots_state"] = "dman_wizard_input"
    context.user_data["dman_wizard_step"] = "leverage"

    keyboard = [
        [
            InlineKeyboardButton("1x", callback_data="bots:dman_leverage:1"),
            InlineKeyboardButton("5x", callback_data="bots:dman_leverage:5"),
            InlineKeyboardButton("10x", callback_data="bots:dman_leverage:10"),
        ],
        [
            InlineKeyboardButton("20x", callback_data="bots:dman_leverage:20"),
            InlineKeyboardButton("50x", callback_data="bots:dman_leverage:50"),
            InlineKeyboardButton("75x", callback_data="bots:dman_leverage:75"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:dman_back_to_pair"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    # ========== SALVA message_id PER ERROR HANDLING ==========
    context.user_data["dman_wizard_message_id"] = query.message.message_id
    context.user_data["dman_wizard_chat_id"] = query.message.chat_id
    # ========================================================

    await query.message.edit_text(
        r"*📉 DMan V3 \- Step 3/6*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`" + "\n\n"
        r"⚡ *Select Leverage*" + "\n"
        r"_Or type a value \(e\.g\. 20\)_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_dman_wizard_leverage(update, context, leverage: int) -> None:
    config = get_controller_config(context)
    config["leverage"] = leverage
    set_controller_config(context, config)
    context.user_data["dman_wizard_step"] = "position_mode"
    await _dman_show_position_mode_step(update, context)

async def _dman_show_position_mode_step(update, context) -> None:
    """DMan Step 4 (derivati only): HEDGE vs ONEWAY"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 1)

    context.user_data["bots_state"] = "dman_wizard_input"
    context.user_data["dman_wizard_step"] = "position_mode"

    keyboard = [
        [
            InlineKeyboardButton("🔀 HEDGE  ✅ recommended", callback_data="bots:dman_position_mode:HEDGE"),
            InlineKeyboardButton("➡️ ONEWAY", callback_data="bots:dman_position_mode:ONEWAY"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:dman_back_to_leverage"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    # CORREZIONE: escape di tutti i caratteri speciali MarkdownV2
    # I caratteri speciali sono: _ * [ ] ( ) ~ ` > # + - = | { } . !
    escaped_connector = escape_markdown_v2(connector)
    escaped_pair = escape_markdown_v2(pair)

    await query.message.edit_text(
        f"*📉 DMan V3 \\- Step 4/6*\n\n"
        f"🏦 `{escaped_connector}` \\| 🔗 `{escaped_pair}` \\| ⚡ `{leverage}x`\n\n"
        r"📐 *Position Mode*" + "\n\n"
        r"• *HEDGE*: Can hold both long and short positions simultaneously" + "\n"
        r"• *ONEWAY*: Can only hold positions in one direction \(long OR short\)",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
async def handle_dman_position_mode(update, context, position_mode: str) -> None:
    config = get_controller_config(context)
    config["position_mode"] = position_mode
    set_controller_config(context, config)
    context.user_data["dman_wizard_step"] = "total_amount_quote"
    await _dman_show_amount_step(update, context)

async def _dman_show_amount_step(update, context) -> None:
    """DMan Step: Enter Total Amount"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 1)
    pos_mode = config.get("position_mode", "HEDGE")

    context.user_data["bots_state"] = "dman_wizard_input"
    context.user_data["dman_wizard_step"] = "total_amount_quote"
    connector = config.get("connector_name", "").lower()
    is_perp = "_perpetual" in connector or "_margin" in connector
    total_steps = 6 if is_perp else 4
    current_step = 5 if is_perp else 3

    # Fetch balance
    balance_text = ""
    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        balances = await get_cex_balances(context.user_data, client, connector)
        quote = pair.split("-")[1] if "-" in pair else "USDT"
        available = balances.get(quote, {}).get("available", 0)
        if available:
            balance_text = f"\n\n💰 Available `{escape_markdown_v2(quote)}`: `{available:,.2f}`"
    except Exception:
        pass

    # Gestione dinamica del tasto Back
    back_callback = "bots:dman_back_to_position_mode" if is_perp else "bots:dman_back_to_pair"

    keyboard = [
        [
            InlineKeyboardButton("$100", callback_data="bots:dman_amount:100"),
            InlineKeyboardButton("$500", callback_data="bots:dman_amount:500"),
            InlineKeyboardButton("$1000", callback_data="bots:dman_amount:1000"),
        ],
        [
            InlineKeyboardButton("$2000", callback_data="bots:dman_amount:2000"),
            InlineKeyboardButton("$5000", callback_data="bots:dman_amount:5000"),
            InlineKeyboardButton("$10000", callback_data="bots:dman_amount:10000"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data=back_callback),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    # Testo del messaggio con riepilogo parametri scelti finora
    header_info = f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
    if is_perp:
        header_info += f" \\| ⚡ `{leverage}x` \\| 🎯 `{pos_mode}`"

    message_text = (
        rf"*📉 DMan V3 \- Step {current_step}/{total_steps}*" + "\n\n"
        + header_info + balance_text + "\n\n"
        r"💰 *Total Amount \(USDT\)*" + "\n"
        r"_Select or type an amount:_"
    )

    # ========== SALVA MESSAGE_ID ==========
    target_chat_id = chat_id
    if query and query.message:
        target_chat_id = query.message.chat_id
        try:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            context.user_data["dman_wizard_message_id"] = query.message.message_id
            return
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass

    new_msg = await context.bot.send_message(
        chat_id=target_chat_id,
        text=message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    context.user_data["dman_wizard_message_id"] = new_msg.message_id
    context.user_data["dman_wizard_chat_id"] = target_chat_id

async def handle_dman_wizard_amount(update, context, amount: float) -> None:
    """Handle amount selection"""
    query = update.callback_query
    config = get_controller_config(context)
    config["total_amount_quote"] = amount
    set_controller_config(context, config)

    pair = config.get("trading_pair", "")
    await query.message.edit_text(
        r"*📉 DMan V3 \- New Config*" + "\n\n"
        f"⏳ *Loading chart for* `{escape_markdown_v2(pair)}`\\.\\.\\.  " + "\n\n"
        r"_Fetching market data\.\.\._",
        parse_mode="MarkdownV2",
    )

    context.user_data["dman_wizard_step"] = "final"
    await _dman_show_final_step(update, context)


async def _dman_show_final_step(update, context, interval: str = None) -> None:
    """DMan Final Step: Chart + Config Summary"""
    query = update.callback_query
    msg = query.message if query else update.message
    chat_id = update.effective_chat.id
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    total_amount = config.get("total_amount_quote", 1000)
    leverage = config.get("leverage", 1)
    bb_length = config.get("bb_length", 100)

    if interval is None:
        interval = context.user_data.get("dman_chart_interval", config.get("interval", "3m"))
    context.user_data["dman_chart_interval"] = interval
    config["interval"] = interval
    set_controller_config(context, config)

    current_price = context.user_data.get("dman_current_price")
    candles = context.user_data.get("dman_candles")

    try:
        cached_interval = context.user_data.get("dman_candles_interval", interval)
        if not current_price or interval != cached_interval:
            try:
                await msg.edit_text(
                    r"*📉 DMan V3 \- New Config*" + "\n\n"
                    f"⏳ Fetching market data for `{escape_markdown_v2(pair)}`\\.\\.\\.",
                    parse_mode="MarkdownV2",
                )
            except Exception:
                pass
            client, _ = await get_bots_client(chat_id, context.user_data)

            # --- INIZIO MODIFICA FIX GRAFICO ---
            # Puliamo il nome del connettore per le candele (es: kucoin_perpetual -> kucoin)
            # Questo evita l'errore 500 sui futures
            candles_connector = connector.replace("_perpetual", "").replace("_margin", "").replace("_spot", "")
            # -----------------------------------

            current_price = await fetch_current_price(client, connector, pair)

            if current_price:
                context.user_data["dman_current_price"] = current_price
                candles = await fetch_candles(
                    client, candles_connector, pair, interval=interval, max_records=420 # <--- Usiamo candles_connector
                )
                context.user_data["dman_candles"] = candles
                context.user_data["dman_candles_interval"] = interval

        if not current_price:
            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="bots:main_menu")]]
            await msg.edit_text(
                r"*❌ Error*" + "\n\n"
                f"Could not fetch price for `{escape_markdown_v2(pair)}`\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        candles_list = candles.get("data", []) if isinstance(candles, dict) else (candles or [])

        if candles_list:
            last_close = candles_list[-1].get("close") or candles_list[-1].get("c")
            if last_close:
                current_price = float(last_close)
                context.user_data["dman_current_price"] = current_price

        if not config.get("id"):
            existing_configs = context.user_data.get("controller_configs_list", [])
            from .controllers.dman_v3 import generate_id as dman_generate_id
            config["id"] = dman_generate_id(config, existing_configs)

        from .controllers.dman_v3.analysis import analyze_candles_for_dman, format_dman_analysis, get_dca_strategy_suggestions
        bb_length = config.get("bb_length", 20)
        bb_std_val = config.get("bb_std", 2.0)
        analysis = analyze_candles_for_dman(candles_list, bb_length=bb_length, bb_std=bb_std_val)

        config["bb_long_threshold"] = analysis["suggested_long_threshold"]
        config["bb_short_threshold"] = analysis["suggested_short_threshold"]
        if analysis["suggested_dca_spreads"]:
            config["dca_spreads"] = ",".join(str(s) for s in analysis["suggested_dca_spreads"])
        if "dca_amounts_pct" not in config or config["dca_amounts_pct"] is None:
            config["dca_amounts_pct"] = ""
        set_controller_config(context, config)

        config_id = config.get("id", "")
        position_mode = config.get("position_mode", "HEDGE")
        stop_loss = config.get("stop_loss", 0.05)
        take_profit = config.get("take_profit", 0.03)
        max_exec = config.get("max_executors_per_side", 1)
        cooldown = config.get("cooldown_time", 60)
        dca_spreads = config.get("dca_spreads", "0.001,0.018,0.15,0.25")
        bb_std = config.get("bb_std", 2.0)
        bb_long = config.get("bb_long_threshold", 0.0)
        bb_short = config.get("bb_short_threshold", 1.0)
        ts = config.get("trailing_stop", {}) or {}
        ts_act = ts.get("activation_price", 0.015) if isinstance(ts, dict) else 0.015
        ts_delta = ts.get("trailing_delta", 0.005) if isinstance(ts, dict) else 0.005

        context.user_data["bots_state"] = "dman_wizard_input"
        context.user_data["dman_wizard_step"] = "final"

        interval_options = ["1m", "3m", "5m", "15m", "1h"]
        interval_row = [
            InlineKeyboardButton(
                f"✓ {opt}" if opt == interval else opt,
                callback_data=f"bots:dman_interval:{opt}"
            )
            for opt in interval_options
        ]
# Recuperiamo il NATR dall'analisi fatta precedentemente (punto dove hai chiamato analyze_candles_for_dman)
        natr_val = analysis.get("natr", 0.01)
        # Salviamo l'analisi in user_data per recuperarla quando l'utente clicca i bottoni
        context.user_data["dman_analysis"] = analysis

        # Generiamo i bottoni usando le chiavi che abbiamo definito in analysis.py
        strategy_row = [
            InlineKeyboardButton("🎯 Scalp", callback_data="bots:dman_set_strat:scalping"),
            InlineKeyboardButton("🎲 Marti", callback_data="bots:dman_set_strat:martingale"),
            InlineKeyboardButton("⚖️ Def", callback_data="bots:dman_set_strat:standard"),
            InlineKeyboardButton("🛡️ Cons", callback_data="bots:dman_set_strat:conservative"),
            InlineKeyboardButton("🤖 Auto", callback_data="bots:dman_set_strat:auto"),
        ]
        is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()
        final_step = 6 if is_perp else 4

        keyboard = [
                    interval_row,
                    strategy_row,  # <--- AGGIUNGI QUESTA RIGA QUI
                    [InlineKeyboardButton("💾 Save Config", callback_data="bots:dman_save")],
                    [
                        InlineKeyboardButton("⬅️ Back", callback_data="bots:dman_back_to_amount"),
                        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                    ],
                ]

        config_text = (
            rf"*📉 DMan V3 \- Step {final_step}/{final_step} \(Final\)*" + "\n\n"
            f"*{escape_markdown_v2(pair)}*\n"
            f"Price: `{current_price:,.6g}` \\| BB: `{bb_length}` \\| Interval: `{interval}`\n\n"
            f"`connector_name={connector}`\n"
            f"`trading_pair={pair}`\n"
            f"`total_amount_quote={total_amount:.0f}`\n"
            f"`leverage={leverage}`\n"
            f"`position_mode={position_mode}`\n"
            f"`max_executors_per_side={max_exec}`\n"
            f"`cooldown_time={cooldown}`\n"
            f"`stop_loss={stop_loss}`\n"
            f"`take_profit={take_profit}`\n"
            f"`trailing_stop_activation={ts_act}`\n"
            f"`trailing_stop_delta={ts_delta}`\n"
            f"`interval={interval}`\n"
            f"`bb_length={bb_length}`\n"
            f"`bb_std={bb_std}`\n"
            f"`bb_long_threshold={bb_long}`\n"
            f"`bb_short_threshold={bb_short}`\n"
            f"`dca_spreads={escape_markdown_v2(str(dca_spreads))}`\n"
            f"`dca_amounts_pct={config.get('dca_amounts_pct', '')}`\n"
            r"_Edit: `field=value`_"
        )

        analysis_text = format_dman_analysis(analysis)
        config_text += "\n\n```\n" + analysis_text + "\n```"

        if candles_list:
            from .controllers.dman_v3.chart import generate_chart as dman_chart
            chart_bytes = dman_chart(config, candles_list, current_price)

            try:
                await msg.delete()
            except Exception:
                pass

            new_msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=chart_bytes,
                caption=config_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            context.user_data["dman_wizard_message_id"] = new_msg.message_id
            context.user_data["dman_wizard_chat_id"] = chat_id
        else:
            try:
                await msg.edit_text(
                    text=config_text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            except Exception:
                new_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=config_text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["dman_wizard_message_id"] = new_msg.message_id

    except Exception as e:
        logger.error(f"DMan final step error: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        try:
            await msg.edit_text(
                format_error_message(f"Error: {str(e)}"),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception:
            pass

async def handle_dman_interval_change(update, context, interval: str) -> None:
    """Change chart interval"""
    context.user_data["dman_candles"] = None
    context.user_data["dman_candles_interval"] = None
    await _dman_show_final_step(update, context, interval=interval)

async def handle_dman_set_strategy(update, context, strat_key: str) -> None:
    """Handle DCA strategy selection from the final step buttons"""
    query = update.callback_query
    config = get_controller_config(context)

    # Recuperiamo l'analisi NATR salvata in precedenza in _dman_show_final_step
    analysis = context.user_data.get("dman_analysis", {})
    natr = analysis.get("natr", 0.01)

    from .controllers.dman_v3.analysis import get_dca_strategy_suggestions
    strats = get_dca_strategy_suggestions(natr)

    if strat_key in strats:
        selected = strats[strat_key]
        # Aggiorniamo la configurazione con i valori della strategia scelta
        config["dca_spreads"] = ",".join(str(s) for s in selected["dca_spreads"])
        config["dca_amounts_pct"] = ",".join(str(a) for a in selected["dca_amounts_pct"])

        # Applichiamo anche i threshold suggeriti dall'analisi BB
        config["bb_long_threshold"] = analysis.get("suggested_long_threshold", 0.0)
        config["bb_short_threshold"] = analysis.get("suggested_short_threshold", 1.0)

        set_controller_config(context, config)

        # Feedback visivo all'utente
        await query.answer(f"✅ Strategia {selected['label']} applicata")

        # Ricarichiamo la schermata finale per mostrare i nuovi valori nel testo e nel grafico
        return await _dman_show_final_step(update, context)

async def handle_dman_save(update, context) -> None:
    """Save DMan V3 configuration"""
    query = update.callback_query
    config = get_controller_config(context)

    # 1. Riconoscimento del tipo di connettore
    connector = config.get("connector_name", "").lower()
    is_perp = "_perpetual" in connector or "_margin" in connector

    # 2. ========== LOGICA DI PULIZIA E VALIDAZIONE ==========
    # Se è SPOT, forziamo i parametri corretti a prescindere dall'input utente
    if not is_perp:
        config["leverage"] = 1
        config["position_mode"] = "ONEWAY"
    else:
        # Se è Perpetual/Margin e non è stato impostato un position_mode, mettiamo HEDGE
        if not config.get("position_mode"):
            config["position_mode"] = "HEDGE"

    # ========== PULISCI I CAMPI NON NECESSARI ==========
    config.pop("candles_config", None)
    config.pop("manual_kill_switch", None)
    # dca_amounts_pct vuoto → rimuovilo, hummingbot usa distribuzione uguale di default
    if not config.get("dca_amounts_pct"):
        config.pop("dca_amounts_pct", None)
    # ===================================================

    config_id = config.get("id", "")
    chat_id = query.message.chat_id

    try:
        await query.message.delete()
    except Exception:
        pass

    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="Saving `" + escape_markdown_v2(config_id) + "`\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        await client.controllers.create_or_update_controller_config(config_id, config)

        # Cleanup dati temporanei del wizard
        for key in ["dman_wizard_step", "dman_wizard_message_id", "dman_wizard_chat_id",
                    "dman_current_price", "dman_candles", "dman_candles_interval",
                    "dman_chart_interval", "controller_config"]: # Aggiunto controller_config per sicurezza
            context.user_data.pop(key, None)
        context.user_data.pop("bots_state", None)

        keyboard = [
            [InlineKeyboardButton("Create Another", callback_data="bots:new_dman_v3")],
            [InlineKeyboardButton("Back to Configs", callback_data="bots:controller_configs")],
        ]
        saved_msg = (
            "*Config Saved\\!*\n\n"
            "Controller `" + escape_markdown_v2(config_id) + "` saved successfully\\."
        )
        await status_msg.edit_text(
            saved_msg,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"DMan save error: {e}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton("Try Again", callback_data="bots:dman_save")],
            [InlineKeyboardButton("Back", callback_data="bots:controller_configs")],
        ]
        await status_msg.edit_text(
            format_error_message(f"Failed to save: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
async def handle_dman_back_to_connector(update, context) -> None:
    context.user_data["dman_wizard_step"] = "connector_name"
    await _dman_show_connector_step(update, context)


async def handle_dman_back_to_pair(update, context) -> None:
    context.user_data["dman_wizard_step"] = "trading_pair"
    await _dman_show_pair_step(update, context)

async def handle_dman_back_to_leverage(update, context) -> None:
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    # Usa la stessa logica degli altri wizard
    is_perp = "_perpetual" in connector or "_margin" in connector
    if is_perp:
        context.user_data["dman_wizard_step"] = "leverage"
        await _dman_show_leverage_step(update, context)
    else:
        await handle_dman_back_to_pair(update, context)


async def handle_dman_back_to_amount(update, context) -> None:
    context.user_data["dman_wizard_step"] = "total_amount_quote"
    context.user_data.pop("dman_current_price", None)
    context.user_data.pop("dman_candles", None)
    context.user_data.pop("dman_candles_interval", None)
    await _dman_show_amount_step(update, context)

async def handle_dman_back_to_position_mode(update, context) -> None:
    context.user_data["dman_wizard_step"] = "position_mode"
    await _dman_show_position_mode_step(update, context)

async def handle_dman_pair_select(
    update: Update, context: ContextTypes.DEFAULT_TYPE, trading_pair: str
) -> None:
    """Handle selection of a suggested trading pair in DMan wizard"""
    config = get_controller_config(context)
    chat_id = update.effective_chat.id

    # Clear old market data
    for key in ["dman_current_price", "dman_candles", "dman_candles_interval", "dman_chart_interval"]:
        context.user_data.pop(key, None)

    config["trading_pair"] = trading_pair
    config["candles_trading_pair"] = trading_pair
    set_controller_config(context, config)

    # Move to next step based on connector type
    connector = config.get("connector_name", "")
    is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()
    if is_perp:
        context.user_data["dman_wizard_step"] = "leverage"
        await _dman_show_leverage_step(update, context)
    else:
        config["leverage"] = 1
        config["position_mode"] = "ONEWAY"
        set_controller_config(context, config)
        context.user_data["dman_wizard_step"] = "total_amount_quote"
        await _dman_show_amount_step(update, context)

async def process_dman_wizard_input(update, context, user_input: str) -> None:
    """Process text input during DMan V3 wizard"""
    step = context.user_data.get("dman_wizard_step")
    chat_id = update.effective_chat.id
    config = get_controller_config(context)
    message_id = context.user_data.get("dman_wizard_message_id")
    wizard_chat_id = context.user_data.get("dman_wizard_chat_id", chat_id)
    try:
        try:
            await update.message.delete()
        except Exception:
            pass

        if step == "trading_pair":
            pair = user_input.upper().strip().replace("/", "-").replace("_", "-")
            connector = config.get("connector_name", "")

            # ========== VALIDAZIONE BASE: DEVE CONTENERE IL TRATTINO ==========
            if "-" not in pair:
                message_id = context.user_data.get("dman_wizard_message_id")
                wizard_chat_id = context.user_data.get("dman_wizard_chat_id", chat_id)

                # CORRETTO: keyboard come lista di liste
                keyboard = [
                    [InlineKeyboardButton("⬅️ Back", callback_data="bots:dman_back_to_connector")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")],
                ]

                # Mostra il contesto con exchange selezionato
                context_text = f"🏦 `{escape_markdown_v2(connector)}`\n\n"

                err_text = (
                    r"*📉 DMan V3 \- Step 2*" + "\n\n"
                    + context_text
                    + r"⚠️ *Invalid format\. Use BASE\-QUOTE \(e\.g\. BTC\-USDT\)*" + "\n\n"
                    r"Type the pair again:"
                )

                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id, message_id=message_id,
                        text=err_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                else:
                    msg = await context.bot.send_message(
                        chat_id=chat_id, text=err_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    context.user_data["dman_wizard_message_id"] = msg.message_id
                return

            # ========== 2. VALIDAZIONE SULL'EXCHANGE (CON SUGGERIMENTI) ==========
            client, _ = await get_bots_client(chat_id, context.user_data)
            is_valid, error_msg, suggestions, correct_pair = (
                await validate_trading_pair(context.user_data, client, connector, pair)
            )

            if not is_valid:
                message_id = context.user_data.get("dman_wizard_message_id")
                wizard_chat_id = context.user_data.get("dman_wizard_chat_id", chat_id)

                # CORRETTO: costruisci la keyboard come lista di liste
                keyboard = []
                for sugg in suggestions[:4]:
                    keyboard.append([InlineKeyboardButton(sugg, callback_data=f"bots:dman_pair_select:{sugg}")])
                keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="bots:dman_back_to_connector")])
                keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")])

                # Mostra il contesto con exchange selezionato
                context_text = f"🏦 `{escape_markdown_v2(connector)}`\n\n"

                err_text = (
                    r"*📉 DMan V3 \- Step 2*" + "\n\n"
                    + context_text
                    + f"❌ `{escape_markdown_v2(pair)}` not found on `{escape_markdown_v2(connector)}`\\.\n\n"
                    r"*Did you mean?*"
                )

                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id, message_id=message_id,
                        text=err_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                else:
                    msg = await context.bot.send_message(
                        chat_id=chat_id, text=err_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    context.user_data["dman_wizard_message_id"] = msg.message_id
                return

            if correct_pair:
                pair = correct_pair

            config["trading_pair"] = pair
            config["candles_trading_pair"] = pair
            for key in ["dman_current_price", "dman_candles", "dman_candles_interval"]:
                context.user_data.pop(key, None)
            set_controller_config(context, config)            # Advance to next step
            connector = config.get("connector_name", "")
            is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()
            message_id = context.user_data.get("dman_wizard_message_id")
            wizard_chat_id = context.user_data.get("dman_wizard_chat_id", chat_id)

            if is_perp:
                context.user_data["dman_wizard_step"] = "leverage"
                leverage = config.get("leverage", 1)
                keyboard = [
                    [
                        InlineKeyboardButton("1x", callback_data="bots:dman_leverage:1"),
                        InlineKeyboardButton("5x", callback_data="bots:dman_leverage:5"),
                        InlineKeyboardButton("10x", callback_data="bots:dman_leverage:10"),
                    ],
                    [
                        InlineKeyboardButton("20x", callback_data="bots:dman_leverage:20"),
                        InlineKeyboardButton("50x", callback_data="bots:dman_leverage:50"),
                        InlineKeyboardButton("75x", callback_data="bots:dman_leverage:75"),
                    ],
                    [
                        InlineKeyboardButton("⬅️ Back", callback_data="bots:dman_back_to_pair"),
                        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                    ],
                ]
                text = (
                    r"*📉 DMan V3 \- Step 3/6*" + "\n\n"
                    f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`" + "\n\n"
                    r"⚡ *Select Leverage*" + "\n"
                    r"_Or type a value \(e\.g\. 20\)_"
                )
            else:
                config["leverage"] = 1
                set_controller_config(context, config)
                context.user_data["dman_wizard_step"] = "total_amount_quote"
                context.user_data["bots_state"] = "dman_wizard_input"
                keyboard = [
                    [
                        InlineKeyboardButton("$100", callback_data="bots:dman_amount:100"),
                        InlineKeyboardButton("$500", callback_data="bots:dman_amount:500"),
                        InlineKeyboardButton("$1000", callback_data="bots:dman_amount:1000"),
                    ],
                    [
                        InlineKeyboardButton("$2000", callback_data="bots:dman_amount:2000"),
                        InlineKeyboardButton("$5000", callback_data="bots:dman_amount:5000"),
                        InlineKeyboardButton("$10000", callback_data="bots:dman_amount:10000"),
                    ],
                    [
                        InlineKeyboardButton("⬅️ Back", callback_data="bots:dman_back_to_pair"),
                        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                    ],
                ]
                text = (
                    r"*📉 DMan V3 \- Step 3/4*" + "\n\n"
                    f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`" + "\n\n"
                    r"💰 *Total Amount \(USDT\)*" + "\n"
                    r"_Select or type an amount:_"
                )

            try:
                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id, message_id=message_id,
                        text=text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    context.user_data["dman_wizard_message_id"] = message_id
            except Exception:
                pass
            return
        elif step == "leverage":
            val = int(float(user_input.strip().lower().replace("x", "")))
            config["leverage"] = val
            set_controller_config(context, config)
            context.user_data["dman_wizard_step"] = "total_amount_quote"
            message_id = context.user_data.get("dman_wizard_message_id")
            wizard_chat_id = context.user_data.get("dman_wizard_chat_id", chat_id)
            connector = config.get("connector_name", "")
            pair = config.get("trading_pair", "")
            keyboard = [
                [
                    InlineKeyboardButton("$100", callback_data="bots:dman_amount:100"),
                    InlineKeyboardButton("$500", callback_data="bots:dman_amount:500"),
                    InlineKeyboardButton("$1000", callback_data="bots:dman_amount:1000"),
                ],
                [
                    InlineKeyboardButton("$2000", callback_data="bots:dman_amount:2000"),
                    InlineKeyboardButton("$5000", callback_data="bots:dman_amount:5000"),
                    InlineKeyboardButton("$10000", callback_data="bots:dman_amount:10000"),
                ],
                [
                    InlineKeyboardButton("⬅️ Back", callback_data="bots:dman_back_to_leverage"),
                    InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                ],
            ]
            text = (
                r"*📉 DMan V3 \- Step 4/6*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}` \\| ⚡ `{val}x`" + "\n\n"
                r"💰 *Total Amount \(USDT\)*" + "\n"
                r"_Select or type an amount:_"
            )
            context.user_data["bots_state"] = "dman_wizard_input"
            if message_id:
                await context.bot.edit_message_text(
                    chat_id=wizard_chat_id, message_id=message_id,
                    text=text, parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
        elif step == "total_amount_quote":
                    amount = float(user_input.strip().replace("$", "").replace(",", ""))
                    config["total_amount_quote"] = amount
                    set_controller_config(context, config)
                    context.user_data["dman_wizard_step"] = "final"
                    wizard_chat_id = context.user_data.get("dman_wizard_chat_id", chat_id)
                    pair = config.get("trading_pair", "")
                    tmp = await context.bot.send_message(
                        chat_id=wizard_chat_id,
                        text=r"*📉 DMan V3*" + "\n\n"
                             f"⏳ Loading chart for `{escape_markdown_v2(pair)}`\\.\\.\\.  ",
                        parse_mode="MarkdownV2",
                    )
                    context.user_data["dman_wizard_message_id"] = tmp.message_id
                    await _dman_show_final_step(update, context)

        elif step == "final":
            # Handle field=value edits
            if "=" in user_input:
                for line in user_input.strip().split("\n"):
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    field, value = line.split("=", 1)
                    field = field.strip().lower()
                    value = value.strip()
                    try:
                        if field in ("total_amount_quote", "stop_loss", "take_profit",
                                     "bb_std", "bb_long_threshold", "bb_short_threshold",
                                     "trailing_stop_activation", "trailing_stop_delta"):
                            val = float(value)
                            if field == "trailing_stop_activation":
                                if not isinstance(config.get("trailing_stop"), dict):
                                    config["trailing_stop"] = {"activation_price": 0.015, "trailing_delta": 0.005}
                                config["trailing_stop"]["activation_price"] = val
                            elif field == "trailing_stop_delta":
                                if not isinstance(config.get("trailing_stop"), dict):
                                    config["trailing_stop"] = {"activation_price": 0.015, "trailing_delta": 0.005}
                                config["trailing_stop"]["trailing_delta"] = val
                            else:
                                config[field] = val
                        elif field in ("leverage", "bb_length", "max_executors_per_side",
                                       "cooldown_time", "take_profit_order_type"):
                            config[field] = int(float(value))
                        elif field in ("dynamic_order_spread", "dynamic_target"):
                            config[field] = value.lower() in ("true", "yes", "1")
                        elif field == "interval":
                            config["interval"] = value
                            # Clear candles to refresh chart
                            context.user_data.pop("dman_candles", None)
                            context.user_data["dman_chart_interval"] = value
                        else:
                            config[field] = value
                    except Exception:
                        pass
                set_controller_config(context, config)

    except Exception as e:
        logger.error(f"DMan wizard input error: {e}", exc_info=True)


# ============================================
# ARBITRAGE CONTROLLER WIZARD
# ============================================
# Steps: connector1 → pair1 → connector2 → pair2 → amount → final+save
# Prefisso handler: arb_

async def show_new_arbitrage_controller_form(update, context) -> None:
    """Start the Arbitrage Controller wizard - Step 1: Exchange 1"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    for key in ["arb_price_1", "arb_price_2"]:
        context.user_data.pop(key, None)

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()
        context.user_data["controller_configs_list"] = configs
    except Exception as e:
        logger.warning(f"Could not fetch existing configs: {e}")

    init_new_controller_config(context, "arbitrage_controller")
    context.user_data["bots_state"] = "arb_wizard"
    context.user_data["arb_wizard_step"] = "connector_1"  # Step 1: primo exchange
    context.user_data["arb_wizard_message_id"] = query.message.message_id
    context.user_data["arb_wizard_chat_id"] = query.message.chat_id
    await _arb_show_connector_step(update, context, exchange_num=1)

async def _arb_show_connector_step(update, context, exchange_num: int, target_message_id: int = None) -> None:
    """Show connector selection for exchange 1 or 2"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    # Usa target_message_id se fornito
    use_message_id = target_message_id
    if not query and not use_message_id:
        logger.error("_arb_show_connector_step called without callback_query and without target_message_id")
        return

    if not query and use_message_id:
        wizard_chat_id = context.user_data.get("arb_wizard_chat_id", chat_id)

    try:
        client, server_name = await get_bots_client(chat_id, context.user_data)
        cex_connectors = await get_available_cex_connectors(
            context.user_data, client, server_name=server_name
        )

        # DEX connectors via Gateway
        dex_connectors = ["jupiter/router", "uniswap/ethereum", "uniswap/base",
                          "uniswap/arbitrum", "uniswap/bsc", "pancakeswap/bsc",
                          "raydium/solana"]

        # ========== CORREZIONE STEP NUMBER ==========
        # exchange_num=1 -> step 1, exchange_num=2 -> step 3
        if exchange_num == 1:
            step = 1
        else:
            step = 3
        # ===========================================

        total_steps = 6
        emoji = "1️⃣" if exchange_num == 1 else "2️⃣"
        role = "Buy" if exchange_num == 1 else "Sell"

        # Show context for step 3 (second exchange)
        header = ""
        if exchange_num == 2:
            ep1 = config.get("exchange_pair_1", {})
            c1 = ep1.get("connector_name", "")
            p1 = ep1.get("trading_pair", "")
            header = f"1️⃣ `{escape_markdown_v2(c1)}` \\| `{escape_markdown_v2(p1)}`\n\n"

        keyboard = []
        if cex_connectors:
            keyboard.append([InlineKeyboardButton("— CEX —", callback_data="bots:noop")])
            row = []
            for c in cex_connectors:
                row.append(InlineKeyboardButton(c, callback_data=f"bots:arb_connector_{exchange_num}:{c}"))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)

        if dex_connectors:
            keyboard.append([InlineKeyboardButton("— DEX (Gateway) —", callback_data="bots:noop")])
            row = []
            for d in dex_connectors:
                row.append(InlineKeyboardButton(d, callback_data=f"bots:arb_connector_{exchange_num}:{d}"))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)

        back_cb = "bots:main_menu" if exchange_num == 1 else "bots:arb_back_to_pair_1"
        keyboard.append([
            InlineKeyboardButton("⬅️ Back", callback_data=back_cb),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ])

        message_text = (
            rf"*⚡ Arbitrage \- Step {step}/{total_steps}*" + "\n\n"
            r"Buy on one exchange, sell on another when spread exceeds min profitability\." + "\n\n"
            r"─────────────────────────" + "\n\n"
            + header
            + rf"*{emoji} Select Exchange {exchange_num} \({role}\):*"
        )

        # Invia/edita il messaggio
        if query and query.message:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        elif use_message_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=wizard_chat_id,
                    message_id=use_message_id,
                    text=message_text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            except Exception as e:
                logger.error(f"Error editing message: {e}")
                new_msg = await context.bot.send_message(
                    chat_id=wizard_chat_id,
                    text=message_text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["arb_wizard_message_id"] = new_msg.message_id

    except Exception as e:
        logger.error(f"Arb connector step error: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        error_text = format_error_message(f"Error: {str(e)}")

        if query and query.message:
            await query.message.edit_text(
                error_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        elif use_message_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=wizard_chat_id,
                    message_id=use_message_id,
                    text=error_text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            except Exception:
                pass



async def handle_arb_wizard_connector_1(update, context, connector: str) -> None:
    """Handle Exchange 1 connector selection"""
    config = get_controller_config(context)
    if "exchange_pair_1" not in config:
        config["exchange_pair_1"] = {}
    config["exchange_pair_1"]["connector_name"] = connector
    # Auto-set rate_connector to first CEX (not DEX)
    if "/" not in connector:
        config["rate_connector"] = connector.replace("_perpetual", "").replace("_spot", "")
    set_controller_config(context, config)

    # VAI AL PAIR 1 (step 2)
    context.user_data["arb_wizard_step"] = "pair_1"
    await _arb_show_pair_step(update, context, exchange_num=1)

async def handle_arb_wizard_connector_2(update, context, connector: str) -> None:
    """Handle Exchange 2 connector selection"""
    config = get_controller_config(context)
    if "exchange_pair_2" not in config:
        config["exchange_pair_2"] = {}
    config["exchange_pair_2"]["connector_name"] = connector
    set_controller_config(context, config)

    # VAI AL PAIR 2 (step 4)
    context.user_data["arb_wizard_step"] = "pair_2"
    await _arb_show_pair_step(update, context, exchange_num=2)

async def _arb_show_pair_step(update, context, exchange_num: int) -> None:
    """Show trading pair input for exchange 1 or 2"""
    query = update.callback_query
    config = get_controller_config(context)

    ep_key = f"exchange_pair_{exchange_num}"
    connector = config.get(ep_key, {}).get("connector_name", "")

    context.user_data["bots_state"] = "arb_wizard_input"
    context.user_data["arb_wizard_step"] = f"pair_{exchange_num}"

    # Calcola lo step corretto
    if exchange_num == 1:
        step = 2
        emoji = "1️⃣"
    else:
        step = 4
        emoji = "2️⃣"

    total_steps = 6

    # Show context per exchange 2
    header = ""
    if exchange_num == 2:
        ep1 = config.get("exchange_pair_1", {})
        c1 = ep1.get("connector_name", "")
        p1 = ep1.get("trading_pair", "")
        c2 = config.get("exchange_pair_2", {}).get("connector_name", "")
        header = (
            f"1️⃣ `{escape_markdown_v2(c1)}` \\| `{escape_markdown_v2(p1)}`\n"
            f"2️⃣ `{escape_markdown_v2(c2)}`\n\n"
        )
    else:
        c1 = config.get("exchange_pair_1", {}).get("connector_name", "")
        header = f"1️⃣ `{escape_markdown_v2(c1)}`\n\n"

    # ... resto del codice invariato ...

    # Recent pairs from existing configs
    existing_configs = context.user_data.get("controller_configs_list", [])
    recent_pairs = []
    seen = set()
    for cfg in reversed(existing_configs):
        ep = cfg.get(ep_key, {})
        pair = ep.get("trading_pair", "") if isinstance(ep, dict) else ""
        if pair and pair not in seen:
            seen.add(pair)
            recent_pairs.append(pair)
            if len(recent_pairs) >= 6:
                break

    # Suggest same pair as exchange 1 for exchange 2
    if exchange_num == 2:
        p1 = config.get("exchange_pair_1", {}).get("trading_pair", "")
        if p1 and p1 not in seen:
            recent_pairs.insert(0, p1)

    keyboard = []
    if recent_pairs:
        row = []
        for pair in recent_pairs[:6]:
            row.append(InlineKeyboardButton(pair, callback_data=f"bots:arb_pair_{exchange_num}:{pair}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    back_cb = f"bots:arb_back_to_connector_{exchange_num}"
    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data=back_cb),
        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
    ])

    is_dex = "/" in connector
    hint = r"_e\.g\. SOL\-USDC_" if is_dex else r"_e\.g\. SOL\-USDT_"

    await query.message.edit_text(
        rf"*⚡ Arbitrage \- Step {step}/6*" + "\n\n"
        + header
        + rf"*{emoji} Trading Pair on* `{escape_markdown_v2(connector)}`:" + "\n\n"
        + hint + "\n\n"
        r"Select or type a pair:",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_arb_wizard_pair_1(update: Update, context: ContextTypes.DEFAULT_TYPE, pair: str) -> None:
    """Handle pair selection for Exchange 1"""
    config = get_controller_config(context)

    if "exchange_pair_1" not in config:
        config["exchange_pair_1"] = {}
    config["exchange_pair_1"]["trading_pair"] = pair.upper()
    set_controller_config(context, config)

    # VAI AL CONNECTOR 2 (step 3) - NON al pair 2
    context.user_data["arb_wizard_step"] = "connector_2"

    # Mostra la selezione del secondo exchange
    await _arb_show_connector_step(update, context, exchange_num=2)

async def handle_arb_wizard_pair_2(update, context, pair: str) -> None:
    """Handle pair selection for Exchange 2"""
    config = get_controller_config(context)
    if "exchange_pair_2" not in config:
        config["exchange_pair_2"] = {}
    config["exchange_pair_2"]["trading_pair"] = pair.upper()

    # Auto-set quote_conversion_asset from first pair (or second)
    ep1 = config.get("exchange_pair_1", {})
    p1 = ep1.get("trading_pair", "")
    if p1:
        quote = p1.split("-")[1] if "-" in p1 else "USDT"
    else:
        quote = pair.split("-")[1] if "-" in pair else "USDT"
    config["quote_conversion_asset"] = quote

    set_controller_config(context, config)

    # VAI ALL'AMOUNT (step 5)
    context.user_data["arb_wizard_step"] = "total_amount_quote"
    await _arb_show_amount_step(update, context)


async def _arb_show_amount_step(update, context) -> None:
    """Arb Step 5: Enter Total Amount"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    ep1 = config.get("exchange_pair_1", {})
    ep2 = config.get("exchange_pair_2", {})
    c1 = ep1.get("connector_name", "")
    p1 = ep1.get("trading_pair", "")
    c2 = ep2.get("connector_name", "")
    p2 = ep2.get("trading_pair", "")

    context.user_data["bots_state"] = "arb_wizard_input"
    context.user_data["arb_wizard_step"] = "total_amount_quote"

    keyboard = [
        [
            InlineKeyboardButton("$100", callback_data="bots:arb_amount:100"),
            InlineKeyboardButton("$500", callback_data="bots:arb_amount:500"),
            InlineKeyboardButton("$1000", callback_data="bots:arb_amount:1000"),
        ],
        [
            InlineKeyboardButton("$2000", callback_data="bots:arb_amount:2000"),
            InlineKeyboardButton("$5000", callback_data="bots:arb_amount:5000"),
            InlineKeyboardButton("$10000", callback_data="bots:arb_amount:10000"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:arb_back_to_pair_2"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    message_text = (
        r"*⚡ Arbitrage \- Step 5/6*" + "\n\n"
        f"1️⃣ `{escape_markdown_v2(c1)}` \\| `{escape_markdown_v2(p1)}`\n"
        f"2️⃣ `{escape_markdown_v2(c2)}` \\| `{escape_markdown_v2(p2)}`\n\n"
        r"💰 *Total Amount per side \(Quote\)*" + "\n"
        r"_Select or type an amount:_"
    )

    try:
        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_arb_wizard_amount(update, context, amount: float) -> None:
    """Handle amount selection from button click"""
    query = update.callback_query
    config = get_controller_config(context)
    config["total_amount_quote"] = amount
    set_controller_config(context, config)

    ep1 = config.get("exchange_pair_1", {})
    pair = ep1.get("trading_pair", "")
    await query.message.edit_text(
        r"*⚡ Arbitrage \- New Config*" + "\n\n"
        f"⏳ Fetching prices for `{escape_markdown_v2(pair)}`\\.\\.\\.",
        parse_mode="MarkdownV2",
    )
    context.user_data["arb_wizard_step"] = "final"
    await _arb_show_final_step(update, context)

async def _arb_show_final_step(update, context) -> None:
    """Arb Final Step: Show config with ONLY fields supported by the controller"""
    query = update.callback_query
    msg = query.message if query else update.message
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    ep1 = config.get("exchange_pair_1", {})
    ep2 = config.get("exchange_pair_2", {})
    c1 = ep1.get("connector_name", "")
    p1 = ep1.get("trading_pair", "")
    c2 = ep2.get("connector_name", "")
    p2 = ep2.get("trading_pair", "")
    total_amount = config.get("total_amount_quote", 1000)
    min_prof = config.get("min_profitability", 0.01)
    delay = config.get("delay_between_executors", 10)
    max_imbalance = config.get("max_executors_imbalance", 1)
    rate_connector = config.get("rate_connector", "binance")
    quote_asset = config.get("quote_conversion_asset", "USDT")

    if "controller_type" not in config:
        config["controller_type"] = "generic"
        set_controller_config(context, config)
    # Generate ID if not set
    if not config.get("id"):
        existing_configs = context.user_data.get("controller_configs_list", [])
        from .controllers.arbitrage_controller.config import generate_id as arb_generate_id
        config["id"] = arb_generate_id(config, existing_configs)
        set_controller_config(context, config)

    # ========== FETCH LIVE PRICES FOR SPREAD (opzionale, solo visualizzazione) ==========
    spread_text = ""
    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        price_1 = None
        price_2 = None

        if "/" not in c1:
            price_1 = await fetch_current_price(client, c1, p1)
            context.user_data["arb_price_1"] = price_1

        if "/" not in c2:
            price_2 = await fetch_current_price(client, c2, p2)
            context.user_data["arb_price_2"] = price_2

        if price_1 and price_2:
            spread_pct = abs(price_1 - price_2) / min(price_1, price_2) * 100
            spread_text = (
                "\n\n📊 *Live Spread:*\n"
                f"  `{c1}`: `{price_1:,.6g}`\n"
                f"  `{c2}`: `{price_2:,.6g}`\n"
                f"  Spread: `{spread_pct:.3f}%`\n"
            )
            if spread_pct > min_prof * 100:
                spread_text += r"  ✅ _Current spread \> min profitability_"
            else:
                spread_text += r"  ⚠️ _Current spread \< min profitability_"
        elif price_1:
            spread_text = (
                "\n\n📊 *Live Price:*\n"
                f"  `{c1}`: `{price_1:,.6g}`\n"
                r"  _DEX price not available via API_"
            )
    except Exception as e:
        logger.warning(f"Could not fetch arb prices: {e}")

    context.user_data["bots_state"] = "arb_wizard_input"
    context.user_data["arb_wizard_step"] = "final"

    keyboard = [
        [InlineKeyboardButton("💾 Save Config", callback_data="bots:arb_save")],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:arb_back_to_amount"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    config_id = config.get("id", "")

    # ========== MOSTRA SOLO I CAMPI SUPPORTATI DAL CONTROLLER ==========
    config_text = (
        r"*⚡ Arbitrage \- Final Review*" + "\n\n"
        f"1️⃣ `{escape_markdown_v2(c1)}` \\| `{escape_markdown_v2(p1)}`\n"
        f"2️⃣ `{escape_markdown_v2(c2)}` \\| `{escape_markdown_v2(p2)}`"
        + spread_text
        + "\n\n"
        f"`id={escape_markdown_v2(config_id)}`\n"
        f"`total_amount_quote={total_amount:.0f}`\n"
        f"`min_profitability={min_prof}`\n"
        f"`delay_between_executors={delay}`\n"
        f"`max_executors_imbalance={max_imbalance}`\n"
        f"`rate_connector={escape_markdown_v2(rate_connector)}`\n"
        f"`quote_conversion_asset={escape_markdown_v2(quote_asset)}`\n\n"
        r"_Edit: `field=value`_"
    )

    try:
        await msg.edit_text(
            config_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        try:
            await msg.delete()
        except Exception:
            pass
        new_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=config_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        context.user_data["arb_wizard_message_id"] = new_msg.message_id
        context.user_data["arb_wizard_chat_id"] = chat_id

async def handle_arb_save(update, context) -> None:
    """Save Arbitrage config - cleans unsupported fields before saving"""
    query = update.callback_query
    config = get_controller_config(context)

    # ========== PULISCI I CAMPI NON SUPPORTATI ==========
    # Campi supportati dal controller originale
    supported_fields = [
        "id",
        "controller_name",
        "controller_type",
        "exchange_pair_1",
        "exchange_pair_2",
        "total_amount_quote",
        "min_profitability",
        "delay_between_executors",
        "max_executors_imbalance",
        "rate_connector",
        "quote_conversion_asset",
    ]
    if "controller_type" not in config:
        config["controller_type"] = "generic"
    # Rimuovi tutti i campi non supportati
    keys_to_remove = [k for k in config.keys() if k not in supported_fields]
    for key in keys_to_remove:
        config.pop(key, None)

    # Assicurati che exchange_pair_1 e exchange_pair_2 abbiano solo i campi necessari
    for ep_key in ["exchange_pair_1", "exchange_pair_2"]:
        if ep_key in config:
            # ConnectorPair supporta solo connector_name e trading_pair
            ep = config[ep_key]
            config[ep_key] = {
                "connector_name": ep.get("connector_name", ""),
                "trading_pair": ep.get("trading_pair", "")
            }

    # ===================================================

    config_id = config.get("id", "")
    chat_id = query.message.chat_id

    try:
        await query.message.delete()
    except Exception:
        pass

    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="Saving `" + escape_markdown_v2(config_id) + "`\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        await client.controllers.create_or_update_controller_config(config_id, config)

        for key in ["arb_wizard_step", "arb_wizard_message_id", "arb_wizard_chat_id",
                    "arb_price_1", "arb_price_2"]:
            context.user_data.pop(key, None)
        context.user_data.pop("bots_state", None)

        keyboard = [
            [InlineKeyboardButton("Create Another", callback_data="bots:new_arbitrage_controller")],
            [InlineKeyboardButton("Back to Configs", callback_data="bots:controller_configs")],
        ]
        await status_msg.edit_text(
            "*Config Saved\\!*\n\n"
            "Controller `" + escape_markdown_v2(config_id) + "` saved successfully\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        logger.error(f"Arb save error: {e}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton("Try Again", callback_data="bots:arb_save")],
            [InlineKeyboardButton("Back", callback_data="bots:controller_configs")],
        ]
        await status_msg.edit_text(
            format_error_message(f"Failed to save: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

# Back handlers
async def handle_arb_back_to_connector_1(update, context) -> None:
    context.user_data["arb_wizard_step"] = "connector_1"
    await _arb_show_connector_step(update, context, exchange_num=1)

async def handle_arb_back_to_connector_2(update, context) -> None:
    context.user_data["arb_wizard_step"] = "connector_2"
    await _arb_show_connector_step(update, context, exchange_num=2)

async def handle_arb_back_to_pair_1(update, context) -> None:
    context.user_data["arb_wizard_step"] = "pair_1"
    await _arb_show_pair_step(update, context, exchange_num=1)

async def handle_arb_back_to_pair_2(update, context) -> None:
    context.user_data["arb_wizard_step"] = "pair_2"
    await _arb_show_pair_step(update, context, exchange_num=2)

async def handle_arb_back_to_amount(update, context) -> None:
    context.user_data["arb_wizard_step"] = "total_amount_quote"
    context.user_data.pop("arb_price_1", None)
    context.user_data.pop("arb_price_2", None)
    await _arb_show_amount_step(update, context)

async def _arb_show_amount_step(update, context, target_message_id: int = None) -> None:
    """Arb Step 5: Enter Total Amount"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    # Usa target_message_id se fornito
    use_message_id = target_message_id
    if not query and not use_message_id:
        logger.error("_arb_show_amount_step called without callback_query and without target_message_id")
        return

    if not query and use_message_id:
        wizard_chat_id = context.user_data.get("arb_wizard_chat_id", chat_id)

    ep1 = config.get("exchange_pair_1", {})
    ep2 = config.get("exchange_pair_2", {})
    c1 = ep1.get("connector_name", "")
    p1 = ep1.get("trading_pair", "")
    c2 = ep2.get("connector_name", "")
    p2 = ep2.get("trading_pair", "")

    context.user_data["bots_state"] = "arb_wizard_input"
    context.user_data["arb_wizard_step"] = "total_amount_quote"

    keyboard = [
        [
            InlineKeyboardButton("$100", callback_data="bots:arb_amount:100"),
            InlineKeyboardButton("$500", callback_data="bots:arb_amount:500"),
            InlineKeyboardButton("$1000", callback_data="bots:arb_amount:1000"),
        ],
        [
            InlineKeyboardButton("$2000", callback_data="bots:arb_amount:2000"),
            InlineKeyboardButton("$5000", callback_data="bots:arb_amount:5000"),
            InlineKeyboardButton("$10000", callback_data="bots:arb_amount:10000"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:arb_back_to_pair_2"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    message_text = (
        r"*⚡ Arbitrage \- Step 5/6*" + "\n\n"
        f"1️⃣ `{escape_markdown_v2(c1)}` \\| `{escape_markdown_v2(p1)}`\n"
        f"2️⃣ `{escape_markdown_v2(c2)}` \\| `{escape_markdown_v2(p2)}`\n\n"
        r"💰 *Total Amount per side \(Quote\)*" + "\n"
        r"_Select or type an amount:_"
    )

    # Invia/edita il messaggio
    if query and query.message:
        try:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
    elif use_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=wizard_chat_id,
                message_id=use_message_id,
                text=message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception as e:
            logger.error(f"Error editing message in _arb_show_amount_step: {e}")
            # Fallback: invia un nuovo messaggio
            new_msg = await context.bot.send_message(
                chat_id=wizard_chat_id,
                text=message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            context.user_data["arb_wizard_message_id"] = new_msg.message_id
    # ========== RIMUOVI IL BLOCCO elif step == "total_amount_quote" DA QUI ==========


async def process_arb_wizard_input(update, context, user_input: str) -> None:
    """Process text input during Arbitrage wizard"""
    step = context.user_data.get("arb_wizard_step")
    chat_id = update.effective_chat.id
    config = get_controller_config(context)
    message_id = context.user_data.get("arb_wizard_message_id")
    wizard_chat_id = context.user_data.get("arb_wizard_chat_id", chat_id)

    try:
        try:
            await update.message.delete()
        except Exception:
            pass

        # ========== GESTISCI INPUT MANUALE PER PAIR 1 ==========
        if step == "pair_1":
            pair = user_input.upper().strip().replace("/", "-").replace("_", "-")
            connector = config.get("exchange_pair_1", {}).get("connector_name", "")

            # Validazione base
            if "-" not in pair:
                keyboard = [
                    [InlineKeyboardButton("⬅️ Back", callback_data="bots:arb_back_to_connector_1")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")],
                ]
                error_text = (
                    r"*⚡ Arbitrage \- Step 2/6*" + "\n\n"
                    f"1️⃣ `{escape_markdown_v2(connector)}`\n\n"
                    r"⚠️ *Invalid format\. Use BASE\-QUOTE \(e\.g\. SOL\-USDT\)*" + "\n\n"
                    r"Type the pair again:"
                )
                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id, message_id=message_id,
                        text=error_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                return

            # Validazione sul primo exchange
            client, _ = await get_bots_client(chat_id, context.user_data)
            is_valid, error_msg, suggestions, correct_pair = (
                await validate_trading_pair(context.user_data, client, connector, pair)
            )

            if not is_valid:
                keyboard = []
                for sugg in suggestions[:4]:
                    keyboard.append([InlineKeyboardButton(sugg, callback_data=f"bots:arb_pair_select:{sugg}")])
                keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="bots:arb_back_to_connector_1")])
                keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")])

                error_text = (
                    r"*⚡ Arbitrage \- Step 2/6*" + "\n\n"
                    f"1️⃣ `{escape_markdown_v2(connector)}`\n\n"
                    f"❌ `{escape_markdown_v2(pair)}` not found on `{escape_markdown_v2(connector)}`\\.\n\n"
                    r"*Did you mean?*"
                )
                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id, message_id=message_id,
                        text=error_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                return

            if correct_pair:
                pair = correct_pair

            # Imposta la pair per il primo exchange
            if "exchange_pair_1" not in config:
                config["exchange_pair_1"] = {}
            config["exchange_pair_1"]["trading_pair"] = pair
            set_controller_config(context, config)

            # VAI AL CONNECTOR 2 (STEP 3)
            context.user_data["arb_wizard_step"] = "connector_2"

            # Mostra la selezione del secondo exchange
            await _arb_show_connector_step(update, context, exchange_num=2, target_message_id=message_id)

        # ========== GESTISCI INPUT MANUALE PER PAIR 2 ==========
        elif step == "pair_2":
            # message_id già definito all'inizio della funzione
            pair = user_input.upper().strip().replace("/", "-").replace("_", "-")
            connector = config.get("exchange_pair_2", {}).get("connector_name", "")

            # Validazione base
            if "-" not in pair:
                ep1 = config.get("exchange_pair_1", {})
                c1 = ep1.get("connector_name", "")
                p1 = ep1.get("trading_pair", "")

                keyboard = [
                    [InlineKeyboardButton("⬅️ Back", callback_data="bots:arb_back_to_connector_2")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")],
                ]
                error_text = (
                    r"*⚡ Arbitrage \- Step 4/6*" + "\n\n"
                    f"1️⃣ `{escape_markdown_v2(c1)}` \\| `{escape_markdown_v2(p1)}`\n"
                    f"2️⃣ `{escape_markdown_v2(connector)}`\n\n"
                    r"⚠️ *Invalid format\. Use BASE\-QUOTE \(e\.g\. SOL\-USDT\)*" + "\n\n"
                    r"Type the pair again:"
                )
                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id, message_id=message_id,
                        text=error_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                return

            # Validazione sul secondo exchange (solo se CEX)
            is_cex = "/" not in connector
            if is_cex:
                client, _ = await get_bots_client(chat_id, context.user_data)
                is_valid, error_msg, suggestions, correct_pair = (
                    await validate_trading_pair(context.user_data, client, connector, pair)
                )

                if not is_valid:
                    keyboard = []
                    for sugg in suggestions[:4]:
                        keyboard.append([InlineKeyboardButton(sugg, callback_data=f"bots:arb_pair_select:{sugg}")])
                    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="bots:arb_back_to_connector_2")])
                    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")])

                    ep1 = config.get("exchange_pair_1", {})
                    c1 = ep1.get("connector_name", "")
                    p1 = ep1.get("trading_pair", "")

                    error_text = (
                        r"*⚡ Arbitrage \- Step 4/6*" + "\n\n"
                        f"1️⃣ `{escape_markdown_v2(c1)}` \\| `{escape_markdown_v2(p1)}`\n"
                        f"2️⃣ `{escape_markdown_v2(connector)}`\n\n"
                        f"❌ `{escape_markdown_v2(pair)}` not found on `{escape_markdown_v2(connector)}`\\.\n\n"
                        r"*Did you mean?*"
                    )
                    if message_id:
                        await context.bot.edit_message_text(
                            chat_id=wizard_chat_id, message_id=message_id,
                            text=error_text, parse_mode="MarkdownV2",
                            reply_markup=InlineKeyboardMarkup(keyboard),
                        )
                    return

                if correct_pair:
                    pair = correct_pair

            # Imposta la pair per il secondo exchange
            if "exchange_pair_2" not in config:
                config["exchange_pair_2"] = {}
            config["exchange_pair_2"]["trading_pair"] = pair

            # Auto-set quote_conversion_asset
            ep1 = config.get("exchange_pair_1", {})
            p1 = ep1.get("trading_pair", "")
            if p1:
                quote = p1.split("-")[1] if "-" in p1 else "USDT"
            else:
                quote = pair.split("-")[1] if "-" in pair else "USDT"
            config["quote_conversion_asset"] = quote

            set_controller_config(context, config)

            # VAI ALL'AMOUNT (STEP 5)
            context.user_data["arb_wizard_step"] = "total_amount_quote"

            # Mostra lo step dell'amount
            await _arb_show_amount_step(update, context, target_message_id=message_id)

        # ========== GESTISCI INPUT PER TOTAL_AMOUNT_QUOTE ==========
        elif step == "total_amount_quote":
            message_id = context.user_data.get("arb_wizard_message_id")
            wizard_chat_id = context.user_data.get("arb_wizard_chat_id", chat_id)
            try:
                # Rimuovi simboli di valuta e virgole
                cleaned_input = user_input.strip().replace("$", "").replace(",", "")

                # Controlla se è un numero valido
                amount = float(cleaned_input)

                if amount <= 0:
                    raise ValueError("Amount must be positive")

                config["total_amount_quote"] = amount
                set_controller_config(context, config)
                context.user_data["arb_wizard_step"] = "final"

                # Mostra messaggio di caricamento
                pair = config.get("exchange_pair_1", {}).get("trading_pair", "")

                if message_id:
                    try:
                        await context.bot.edit_message_text(
                            chat_id=wizard_chat_id,
                            message_id=message_id,
                            text=r"*⚡ Arbitrage \- New Config*" + "\n\n"
                                 f"⏳ Fetching prices for `{escape_markdown_v2(pair)}`\\.\\.\\.",
                            parse_mode="MarkdownV2",
                        )
                    except Exception:
                        pass
                else:
                    tmp_msg = await context.bot.send_message(
                        chat_id=wizard_chat_id,
                        text=r"*⚡ Arbitrage \- New Config*" + "\n\n"
                             f"⏳ Fetching prices for `{escape_markdown_v2(pair)}`\\.\\.\\.",
                        parse_mode="MarkdownV2",
                    )
                    context.user_data["arb_wizard_message_id"] = tmp_msg.message_id

                await _arb_show_final_step(update, context)

            except ValueError:
                # Input non valido - mostra errore
                ep1 = config.get("exchange_pair_1", {})
                ep2 = config.get("exchange_pair_2", {})
                c1 = ep1.get("connector_name", "")
                p1 = ep1.get("trading_pair", "")
                c2 = ep2.get("connector_name", "")
                p2 = ep2.get("trading_pair", "")

                keyboard = [
                    [
                        InlineKeyboardButton("$100", callback_data="bots:arb_amount:100"),
                        InlineKeyboardButton("$500", callback_data="bots:arb_amount:500"),
                        InlineKeyboardButton("$1000", callback_data="bots:arb_amount:1000"),
                    ],
                    [
                        InlineKeyboardButton("$2000", callback_data="bots:arb_amount:2000"),
                        InlineKeyboardButton("$5000", callback_data="bots:arb_amount:5000"),
                        InlineKeyboardButton("$10000", callback_data="bots:arb_amount:10000"),
                    ],
                    [
                        InlineKeyboardButton("⬅️ Back", callback_data="bots:arb_back_to_pair_2"),
                        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                    ],
                ]

                error_text = (
                    r"*⚡ Arbitrage \- Step 5/6*" + "\n\n"
                    f"1️⃣ `{escape_markdown_v2(c1)}` \\| `{escape_markdown_v2(p1)}`\n"
                    f"2️⃣ `{escape_markdown_v2(c2)}` \\| `{escape_markdown_v2(p2)}`\n\n"
                    r"💰 *Total Amount \(Quote\)*" + "\n"
                    r"⚠️ *Invalid amount\. Enter a positive number \(e\.g\. 500\)*" + "\n\n"
                    r"_Select or type an amount:_"
                )

                if message_id:
                    try:
                        await context.bot.edit_message_text(
                            chat_id=wizard_chat_id,
                            message_id=message_id,
                            text=error_text,
                            parse_mode="MarkdownV2",
                            reply_markup=InlineKeyboardMarkup(keyboard),
                        )
                    except Exception:
                        pass
                else:
                    new_msg = await context.bot.send_message(
                        chat_id=wizard_chat_id,
                        text=error_text,
                        parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    context.user_data["arb_wizard_message_id"] = new_msg.message_id

        # ========== GESTISCI INPUT PER FINAL (EDIT CAMPI) ==========
        elif step == "final":
            if "=" in user_input:
                supported_fields = [
                    "id", "total_amount_quote", "min_profitability", "delay_between_executors",
                    "max_executors_imbalance", "rate_connector", "quote_conversion_asset",
                    "exchange_pair_1_connector", "exchange_pair_1_pair",
                    "exchange_pair_2_connector", "exchange_pair_2_pair",
                ]

                for line in user_input.strip().split("\n"):
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    field, value = line.split("=", 1)
                    field = field.strip().lower()
                    value = value.strip()

                    if field not in supported_fields:
                        continue

                    try:
                        if field in ("min_profitability", "total_amount_quote"):
                            config[field] = float(value)
                        elif field in ("delay_between_executors", "max_executors_imbalance"):
                            config[field] = int(float(value))
                        elif field.startswith("exchange_pair_"):
                            from .controllers.arbitrage_controller.config import apply_flat_fields
                            apply_flat_fields(config, {field: value})
                        else:
                            config[field] = value
                    except Exception:
                        pass

                set_controller_config(context, config)
                await _arb_show_final_step(update, context)

    except Exception as e:
        logger.error(f"Arb wizard input error: {e}", exc_info=True)

async def handle_arb_pair_select(update: Update, context: ContextTypes.DEFAULT_TYPE, trading_pair: str) -> None:
    """Handle selection of a suggested trading pair in Arbitrage wizard"""
    step = context.user_data.get("arb_wizard_step")
    if step == "pair_1":
        await handle_arb_wizard_pair_1(update, context, trading_pair)
    elif step == "pair_2":
        await handle_arb_wizard_pair_2(update, context, trading_pair)

async def handle_arb_proceed_anyway(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle proceeding with DEX pair validation warning"""
    pair = context.user_data.get("arb_pending_pair")
    step = context.user_data.get("arb_wizard_step")
    if step == "pair_1":
        await handle_arb_wizard_pair_1(update, context, pair)
    elif step == "pair_2":
        await handle_arb_wizard_pair_2(update, context, pair)
# ============================================
# XEMM MULTIPLE LEVELS WIZARD
# ============================================
# Steps: maker_connector → maker_pair → taker_connector → taker_pair → amount → final+save
# Prefisso handler: xemm_

async def show_new_xemm_multiple_levels_form(update, context) -> None:
    """Start the XEMM wizard - Step 1: Maker Exchange (come arbitrage)"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    for key in ["xemm_price_maker", "xemm_price_taker"]:
        context.user_data.pop(key, None)

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()
        context.user_data["controller_configs_list"] = configs
    except Exception as e:
        logger.warning(f"Could not fetch existing configs: {e}")

    init_new_controller_config(context, "xemm_multiple_levels")
    context.user_data["bots_state"] = "xemm_wizard"
    context.user_data["xemm_wizard_step"] = "maker_connector"  # Step 1
    context.user_data["xemm_wizard_message_id"] = query.message.message_id
    context.user_data["xemm_wizard_chat_id"] = query.message.chat_id

    await _xemm_show_connector_step(update, context, role="maker")

async def _xemm_show_connector_step(update, context, role: str, target_message_id: int = None) -> None:
    """Show connector selection for maker or taker"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    # Usa target_message_id se fornito
    use_message_id = target_message_id
    if not query and not use_message_id:
        logger.error("_xemm_show_connector_step called without callback_query and without target_message_id")
        return

    if not query and use_message_id:
        wizard_chat_id = context.user_data.get("xemm_wizard_chat_id", chat_id)

    is_maker = role == "maker"
    step = 1 if is_maker else 3
    emoji = "🏭" if is_maker else "⚡"
    role_label = "Maker (less liquid, limit orders)" if is_maker else "Taker (more liquid, hedge)"
    # Show context for taker step
    header = ""
    if not is_maker:
        mc = config.get("maker_connector", "")
        mp = config.get("maker_trading_pair", "")
        header = f"🏭 Maker: `{escape_markdown_v2(mc)}` \\| `{escape_markdown_v2(mp)}`\n\n"

    try:
        client, server_name = await get_bots_client(chat_id, context.user_data)
        cex_connectors = await get_available_cex_connectors(
            context.user_data, client, server_name=server_name
        )

        # Taker can also be a DEX
        dex_connectors = [] if is_maker else [
            "jupiter/router", "uniswap/ethereum", "uniswap/base",
            "uniswap/arbitrum", "uniswap/bsc", "pancakeswap/bsc",
        ]

        keyboard = []
        if cex_connectors:
            if dex_connectors:
                keyboard.append([InlineKeyboardButton("— CEX —", callback_data="bots:noop")])
            row = []
            for c in cex_connectors:
                row.append(InlineKeyboardButton(
                    c, callback_data=f"bots:xemm_{role}_connector:{c}"
                ))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)

        if dex_connectors:
            keyboard.append([InlineKeyboardButton("— DEX (Gateway) —", callback_data="bots:noop")])
            row = []
            for d in dex_connectors:
                row.append(InlineKeyboardButton(
                    d, callback_data=f"bots:xemm_{role}_connector:{d}"
                ))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)

        back_cb = "bots:main_menu" if is_maker else "bots:xemm_back_to_pair"
        keyboard.append([
            InlineKeyboardButton("⬅️ Back", callback_data=back_cb),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ])

        message_text = (
            rf"*🔄 XEMM Multi Levels \- Step {step}/6*" + "\n\n"
            r"Places limit orders on maker exchange, hedges instantly on taker\." + "\n\n"
            r"─────────────────────────" + "\n\n"
            + header
            + rf"*{emoji} Select {escape_markdown_v2(role_label)}:*"
        )

        # Invia/edita il messaggio
        if query and query.message:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        elif use_message_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=wizard_chat_id,
                    message_id=use_message_id,
                    text=message_text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            except Exception as e:
                logger.error(f"Error editing message in _xemm_show_connector_step: {e}")
                new_msg = await context.bot.send_message(
                    chat_id=wizard_chat_id,
                    text=message_text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["xemm_wizard_message_id"] = new_msg.message_id

    except Exception as e:
        logger.error(f"XEMM connector step error: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        error_text = format_error_message(f"Error: {str(e)}")

        if query and query.message:
            await query.message.edit_text(
                error_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        elif use_message_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=wizard_chat_id,
                    message_id=use_message_id,
                    text=error_text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            except Exception:
                pass

async def handle_xemm_maker_connector(update, context, connector: str) -> None:
    """Handle Maker connector selection"""
    config = get_controller_config(context)
    config["maker_connector"] = connector
    set_controller_config(context, config)

    # VAI AL PAIR DEL MAKER (Step 2)
    context.user_data["xemm_wizard_step"] = "maker_pair"
    await _xemm_show_pair_step(update, context, role="maker")

async def handle_xemm_taker_connector(update, context, connector: str) -> None:
    """Handle Taker connector selection"""
    config = get_controller_config(context)
    config["taker_connector"] = connector
    set_controller_config(context, config)

    # VAI AL PAIR DEL TAKER (Step 4)
    context.user_data["xemm_wizard_step"] = "taker_pair"
    await _xemm_show_pair_step(update, context, role="taker")

async def handle_xemm_wizard_pair(update, context, pair: str) -> None:
    """Handle pair selection - applies to BOTH exchanges (come arbitrage)"""
    query = update.callback_query
    config = get_controller_config(context)

    # Applica lo stesso pair a entrambi (come arbitrage)
    config["maker_trading_pair"] = pair
    config["taker_trading_pair"] = pair
    set_controller_config(context, config)

    # Vai al taker connector (Step 3)
    context.user_data["xemm_wizard_step"] = "taker_connector"
    await _xemm_show_connector_step(update, context, role="taker")

async def _xemm_show_pair_step(update, context, role: str) -> None:
    """Mostra l'input per la trading pair (Maker o Taker)"""
    query = update.callback_query
    config = get_controller_config(context)

    is_maker = (role == "maker")
    connector = config.get("maker_connector" if is_maker else "taker_connector", "")

    context.user_data["bots_state"] = "xemm_wizard_input"
    context.user_data["xemm_wizard_step"] = f"{role}_pair"

    step = 2 if is_maker else 4
    emoji = "🏭 Maker" if is_maker else "⚡ Taker"

    # Header con riepilogo passi precedenti
    if not is_maker:
        mc = config.get("maker_connector", "")
        mp = config.get("maker_trading_pair", "")
        header = f"🏭 `{escape_markdown_v2(mc)}` \\| `{escape_markdown_v2(mp)}`\n" \
                 f"⚡ `{escape_markdown_v2(connector)}`\n\n"
    else:
        header = f"🏭 `{escape_markdown_v2(connector)}`\n\n"

    # Suggerimenti coppie recenti
    existing_configs = context.user_data.get("controller_configs_list", [])
    recent_pairs = []
    seen = set()
    for cfg in reversed(existing_configs):
        p = cfg.get(f"{role}_trading_pair", "")
        if p and p not in seen:
            seen.add(p); recent_pairs.append(p)
            if len(recent_pairs) >= 6: break

    # Se Taker, suggerisci la stessa pair del Maker come prima opzione
    if not is_maker:
        mp = config.get("maker_trading_pair", "")
        if mp and mp not in seen: recent_pairs.insert(0, mp)

    keyboard = []
    if recent_pairs:
        row = []
        for p in recent_pairs[:6]:
            row.append(InlineKeyboardButton(p, callback_data=f"bots:xemm_{role}_pair:{p}"))
            if len(row) == 2: keyboard.append(row); row = []
        if row: keyboard.append(row)

    back_cb = "bots:xemm_back_to_maker_connector" if is_maker else "bots:xemm_back_to_taker_connector"
    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data=back_cb),
        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
    ])

    await query.message.edit_text(
        rf"*🔄 XEMM \- Step {step}/6*" + "\n\n" + header +
        rf"*{emoji} Trading Pair:* " + "\n\n" +
        r"_Esempio: BTC\-USDT_" + "\n\n" +
        r"Seleziona o scrivi la coppia:",
        parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_xemm_maker_pair(update, context, pair: str) -> None:
    """Handle maker pair selection"""
    config = get_controller_config(context)
    config["maker_trading_pair"] = pair.upper()
    set_controller_config(context, config)
    context.user_data["xemm_wizard_step"] = "taker_connector"
    # Non serve target_message_id qui perché viene da callback
    await _xemm_show_connector_step(update, context, role="taker")


async def handle_xemm_taker_pair(update, context, pair: str) -> None:
    """Handle taker pair selection"""
    config = get_controller_config(context)
    config["taker_trading_pair"] = pair.upper()
    set_controller_config(context, config)
    context.user_data["xemm_wizard_step"] = "total_amount_quote"
    # Non serve target_message_id perché viene da callback
    await _xemm_show_amount_step(update, context)

async def _show_xemm_pair_suggestions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    input_pair: str,
    error_msg: str,
    suggestions: list,
    connector: str,
    role: str,  # "maker" o "taker"
) -> None:
    """Show trading pair suggestions when validation fails in XEMM wizard"""
    message_id = context.user_data.get("xemm_wizard_message_id")
    chat_id = context.user_data.get("xemm_wizard_chat_id")
    wizard_chat_id = context.user_data.get("xemm_wizard_chat_id", update.effective_chat.id)

    # Memorizza la pair in sospeso per proceed_anyway
    context.user_data["xemm_pending_pair"] = input_pair
    # Memorizza anche lo step corrente
    context.user_data["xemm_wizard_step"] = role + "_pair"

    # Build suggestion message
    help_text = f"❌ *{escape_markdown_v2(error_msg)}*\n\n"

    if suggestions:
        help_text += "💡 *Did you mean:*\n"
    else:
        help_text += "_No similar pairs found\\._\n"

    # Build keyboard with suggestions
    keyboard = []
    for pair in suggestions[:4]:
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"📈 {pair}", callback_data=f"bots:xemm_{role}_pair:{pair}"
                )
            ]
        )

    # Back button based on role
    back_cb = "bots:xemm_back_to_maker_connector" if role == "maker" else "bots:xemm_back_to_taker_connector"
    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data=back_cb),
        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if message_id and chat_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=help_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.debug(f"Could not update XEMM wizard message: {e}")
    else:
        await update.effective_chat.send_message(
            help_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )



async def _xemm_show_amount_step(update, context, target_message_id: int = None) -> None:
    """XEMM Step 5: Enter Total Amount"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    # Usa target_message_id se fornito
    use_message_id = target_message_id
    if not query and not use_message_id:
        logger.error("_xemm_show_amount_step called without callback_query and without target_message_id")
        return

    if not query and use_message_id:
        wizard_chat_id = context.user_data.get("xemm_wizard_chat_id", chat_id)

    mc = config.get("maker_connector", "")
    mp = config.get("maker_trading_pair", "")
    tc = config.get("taker_connector", "")
    tp = config.get("taker_trading_pair", "")

    context.user_data["bots_state"] = "xemm_wizard_input"
    context.user_data["xemm_wizard_step"] = "total_amount_quote"

    keyboard = [
        [
            InlineKeyboardButton("$100", callback_data="bots:xemm_amount:100"),
            InlineKeyboardButton("$500", callback_data="bots:xemm_amount:500"),
            InlineKeyboardButton("$1000", callback_data="bots:xemm_amount:1000"),
        ],
        [
            InlineKeyboardButton("$2000", callback_data="bots:xemm_amount:2000"),
            InlineKeyboardButton("$5000", callback_data="bots:xemm_amount:5000"),
            InlineKeyboardButton("$10000", callback_data="bots:xemm_amount:10000"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:xemm_back_to_taker_connector"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    message_text = (
        r"*🔄 XEMM Multi Levels \- Step 5/6*" + "\n\n"
        f"🏭 `{escape_markdown_v2(mc)}` \\| `{escape_markdown_v2(mp)}`\n"
        f"⚡ `{escape_markdown_v2(tc)}` \\| `{escape_markdown_v2(tp)}`\n\n"
        r"💰 *Total Amount \(Quote\)*" + "\n"
        r"_50% allocated to buy side, 50% to sell side_\n"
        r"_Select or type an amount:_"
    )

    # Invia/edita il messaggio
    if query and query.message:
        try:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
    elif use_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=wizard_chat_id,
                message_id=use_message_id,
                text=message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception as e:
            logger.error(f"Error editing message in _xemm_show_amount_step: {e}")
            # Fallback: invia un nuovo messaggio
            new_msg = await context.bot.send_message(
                chat_id=wizard_chat_id,
                text=message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            context.user_data["xemm_wizard_message_id"] = new_msg.message_id


async def handle_xemm_wizard_amount(update, context, amount: float) -> None:
    """Handle amount selection"""
    query = update.callback_query
    config = get_controller_config(context)
    config["total_amount_quote"] = amount
    set_controller_config(context, config)

    mp = config.get("maker_trading_pair", "")
    await query.message.edit_text(
        r"*🔄 XEMM Multi Levels \- New Config*" + "\n\n"
        f"⏳ Fetching prices for `{escape_markdown_v2(mp)}`\\.\\.\\.",
        parse_mode="MarkdownV2",
    )
    context.user_data["xemm_wizard_step"] = "final"
    await _xemm_show_final_step(update, context)


async def _xemm_show_final_step(update, context) -> None:
    """XEMM Final Step: Show config + live spread + suggested levels"""
    query = update.callback_query
    if query:
        msg = query.message
    else:
        msg = update.message
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    mc = config.get("maker_connector", "")
    mp = config.get("maker_trading_pair", "")
    tc = config.get("taker_connector", "")
    tp = config.get("taker_trading_pair", "")
    total_amount = config.get("total_amount_quote", 1000)
    min_prof = config.get("min_profitability", 0.003)
    max_prof = config.get("max_profitability", 0.01)
    buy_levels = config.get("buy_levels_targets_amount", "0.003,10-0.006,20-0.009,30")
    sell_levels = config.get("sell_levels_targets_amount", "0.003,10-0.006,20-0.009,30")

    # Generate ID if not set
    if not config.get("id"):
        existing_configs = context.user_data.get("controller_configs_list", [])
        from .controllers.xemm_multiple_levels.config import generate_id as xemm_generate_id
        config["id"] = xemm_generate_id(config, existing_configs)
        set_controller_config(context, config)

    # Fetch live prices and calculate spread
    spread_line = ""
    try:
        client, _ = await get_bots_client(chat_id, context.user_data)

        price_maker = None
        price_taker = None

        if "/" not in mc:
            price_maker = await fetch_current_price(client, mc, mp)
            if price_maker:
                context.user_data["xemm_price_maker"] = price_maker

        if "/" not in tc:
            price_taker = await fetch_current_price(client, tc, tp)
            if price_taker:
                context.user_data["xemm_price_taker"] = price_taker

        if price_maker and price_taker:
            spread_pct = abs(price_maker - price_taker) / min(price_maker, price_taker)
            spread_pct_display = spread_pct * 100

            # Auto-suggest levels based on observed spread
            from .controllers.xemm_multiple_levels.config import suggest_levels_from_spread
            suggested_levels = suggest_levels_from_spread(spread_pct, total_amount)
            config["buy_levels_targets_amount"] = suggested_levels
            config["sell_levels_targets_amount"] = suggested_levels
            buy_levels = suggested_levels
            sell_levels = suggested_levels
            set_controller_config(context, config)

            spread_line = (
                "\n\n" +
                r"📊 *Live Spread:*" + "\n"
                f"🏭 `{escape_markdown_v2(mc)}`: `{price_maker:,.6g}`\n"
                f"⚡ `{escape_markdown_v2(tc)}`: `{price_taker:,.6g}`\n"
                f"Spread: `{spread_pct_display:.3f}%`\n"
                r"_✅ Levels auto\-suggested from spread_"
            )
        elif price_maker:
            spread_line = (
                "\n\n" +
                r"📊 *Live Price \(maker\):*" + "\n"
                f"🏭 `{escape_markdown_v2(mc)}`: `{price_maker:,.6g}`\n"
                r"_⚠️ Taker price not available — using default levels_"
            )

    except Exception as e:
        logger.warning(f"Could not fetch XEMM prices: {e}")

    context.user_data["bots_state"] = "xemm_wizard_input"
    context.user_data["xemm_wizard_step"] = "final"

    keyboard = [
        [InlineKeyboardButton("💾 Save Config", callback_data="bots:xemm_save")],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:xemm_back_to_amount"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    config_text = (
        r"*🔄 XEMM Multi Levels \- Step 6/6 \(Final\)*" + "\n\n"
        f"🏭 `{escape_markdown_v2(mc)}` \\| `{escape_markdown_v2(mp)}`\n"
        f"⚡ `{escape_markdown_v2(tc)}` \\| `{escape_markdown_v2(tp)}`"
        + spread_line + "\n\n"
        f"`total_amount_quote={total_amount:.0f}`\n"
        f"`buy_levels_targets_amount={escape_markdown_v2(str(buy_levels))}`\n"
        f"`sell_levels_targets_amount={escape_markdown_v2(str(sell_levels))}`\n"
        f"`min_profitability={min_prof}`\n"
        f"`max_profitability={max_prof}`\n"
        f"`max_executors_imbalance={config.get('max_executors_imbalance', 1)}`\n\n"
        r"_Edit: `field=value`_" + "\n"
        r"_Levels format: `profit,weight\-profit,weight`_"
    )
    try:
        await msg.edit_text(
            config_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        try:
            await msg.delete()
        except Exception:
            pass
        new_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=config_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        context.user_data["xemm_wizard_message_id"] = new_msg.message_id
        context.user_data["xemm_wizard_chat_id"] = chat_id


async def handle_xemm_save(update, context) -> None:
    """Save XEMM config"""
    query = update.callback_query
    config = get_controller_config(context)
    config_id = config.get("id", "").replace("/", "-")
    config["id"] = config_id  # aggiorna anche nel config
    chat_id = query.message.chat_id

    try:
        await query.message.delete()
    except Exception:
        pass

    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="Saving `" + escape_markdown_v2(config_id) + "`\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        await client.controllers.create_or_update_controller_config(config_id, config)

        for key in ["xemm_wizard_step", "xemm_wizard_message_id", "xemm_wizard_chat_id",
                    "xemm_price_maker", "xemm_price_taker"]:
            context.user_data.pop(key, None)
        context.user_data.pop("bots_state", None)

        keyboard = [
            [InlineKeyboardButton("Create Another", callback_data="bots:new_xemm_multiple_levels")],
            [InlineKeyboardButton("Back to Configs", callback_data="bots:controller_configs")],
        ]
        await status_msg.edit_text(
            "*Config Saved\\!*\n\n"
            "Controller `" + escape_markdown_v2(config_id) + "` saved successfully\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        logger.error(f"XEMM save error: {e}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton("Try Again", callback_data="bots:xemm_save")],
            [InlineKeyboardButton("Back", callback_data="bots:controller_configs")],
        ]
        await status_msg.edit_text(
            format_error_message(f"Failed to save: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


# Back handlers
async def handle_xemm_back_to_maker_connector(update, context) -> None:
    context.user_data["xemm_wizard_step"] = "maker_connector"
    await _xemm_show_connector_step(update, context, role="maker")

async def handle_xemm_back_to_maker_pair(update, context) -> None:
    context.user_data["xemm_wizard_step"] = "maker_trading_pair"
    await _xemm_show_pair_step(update, context, role="maker")

async def handle_xemm_back_to_taker_connector(update, context) -> None:
    context.user_data["xemm_wizard_step"] = "taker_connector"
    await _xemm_show_connector_step(update, context, role="taker")

async def handle_xemm_back_to_taker_pair(update, context) -> None:
    context.user_data["xemm_wizard_step"] = "taker_trading_pair"
    await _xemm_show_pair_step(update, context, role="taker")

async def handle_xemm_back_to_amount(update, context) -> None:
    context.user_data["xemm_wizard_step"] = "total_amount_quote"
    context.user_data.pop("xemm_price_maker", None)
    context.user_data.pop("xemm_price_taker", None)
    await _xemm_show_amount_step(update, context)

async def process_xemm_wizard_input(update, context, user_input: str) -> None:
    """Process text input during XEMM wizard"""
    step = context.user_data.get("xemm_wizard_step")
    chat_id = update.effective_chat.id
    config = get_controller_config(context)
    message_id = context.user_data.get("xemm_wizard_message_id")
    wizard_chat_id = context.user_data.get("xemm_wizard_chat_id", chat_id)

    try:
        try:
            await update.message.delete()
        except Exception:
            pass

        # ========== GESTISCI INPUT MANUALE PER MAKER PAIR ==========
        if step == "maker_pair":
            pair = user_input.upper().strip().replace("/", "-").replace("_", "-")
            connector = config.get("maker_connector", "")

            # Validazione base
            if "-" not in pair:
                keyboard = [
                    [InlineKeyboardButton("⬅️ Back", callback_data="bots:xemm_back_to_maker_connector")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")],
                ]
                error_text = (
                    r"*🔄 XEMM Multi Levels \- Step 2*" + "\n\n"
                    f"🏭 `{escape_markdown_v2(connector)}`\n\n"
                    r"⚠️ *Invalid format\. Use BASE\-QUOTE \(e\.g\. BTC\-USDT\)*" + "\n\n"
                    r"Type the pair again:"
                )
                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id, message_id=message_id,
                        text=error_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                return

            # Validazione sul maker exchange (solo se CEX)
            is_cex = "/" not in connector
            if is_cex:
                client, _ = await get_bots_client(chat_id, context.user_data)
                is_valid, error_msg, suggestions, correct_pair = (
                    await validate_trading_pair(context.user_data, client, connector, pair)
                )

                if not is_valid:
                    await _show_xemm_pair_suggestions(
                        update, context, pair, error_msg, suggestions, connector, "maker"
                    )
                    return

                if correct_pair:
                    pair = correct_pair
            else:
                # DEX - avvertimento ma permetti di continuare
                context.user_data["xemm_pending_pair"] = pair
                warning_text = (
                    r"*🔄 XEMM Multi Levels \- Step 2*" + "\n\n"
                    f"🏭 `{escape_markdown_v2(connector)}` \\| `{escape_markdown_v2(pair)}`\n\n"
                    r"⚠️ *DEX pair validation not available*" + "\n\n"
                    r"_The pair may not exist on the DEX\. Proceed with caution\._"
                )
                keyboard = [[InlineKeyboardButton("✅ Proceed Anyway", callback_data="bots:xemm_proceed_anyway")]]
                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id, message_id=message_id,
                        text=warning_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                return

            # Imposta la pair per il maker exchange
            config["maker_trading_pair"] = pair
            set_controller_config(context, config)

            # VAI AL TAKER CONNECTOR (Step 3)
            context.user_data["xemm_wizard_step"] = "taker_connector"

            # Mostra la selezione del taker exchange
            await _xemm_show_connector_step(update, context, role="taker", target_message_id=message_id)

        # ========== GESTISCI INPUT MANUALE PER TAKER PAIR ==========
        elif step == "taker_pair":
            pair = user_input.upper().strip().replace("/", "-").replace("_", "-")
            connector = config.get("taker_connector", "")

            # Validazione base
            if "-" not in pair:
                mc = config.get("maker_connector", "")
                mp = config.get("maker_trading_pair", "")

                keyboard = [
                    [InlineKeyboardButton("⬅️ Back", callback_data="bots:xemm_back_to_taker_connector")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")],
                ]
                error_text = (
                    r"*🔄 XEMM Multi Levels \- Step 4*" + "\n\n"
                    f"🏭 `{escape_markdown_v2(mc)}` \\| `{escape_markdown_v2(mp)}`\n"
                    f"⚡ `{escape_markdown_v2(connector)}`\n\n"
                    r"⚠️ *Invalid format\. Use BASE\-QUOTE \(e\.g\. BTC\-USDT\)*" + "\n\n"
                    r"Type the pair again:"
                )
                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id, message_id=message_id,
                        text=error_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                return

            # Validazione sul taker exchange (solo se CEX)
            is_cex = "/" not in connector
            if is_cex:
                client, _ = await get_bots_client(chat_id, context.user_data)
                is_valid, error_msg, suggestions, correct_pair = (
                    await validate_trading_pair(context.user_data, client, connector, pair)
                )

                if not is_valid:
                    await _show_xemm_pair_suggestions(
                        update, context, pair, error_msg, suggestions, connector, "taker"
                    )
                    return

                if correct_pair:
                    pair = correct_pair
            else:
                # DEX - avvertimento ma permetti di continuare
                context.user_data["xemm_pending_pair"] = pair
                mc = config.get("maker_connector", "")
                mp = config.get("maker_trading_pair", "")
                warning_text = (
                    r"*🔄 XEMM Multi Levels \- Step 4*" + "\n\n"
                    f"🏭 `{escape_markdown_v2(mc)}` \\| `{escape_markdown_v2(mp)}`\n"
                    f"⚡ `{escape_markdown_v2(connector)}` \\| `{escape_markdown_v2(pair)}`\n\n"
                    r"⚠️ *DEX pair validation not available*" + "\n\n"
                    r"_The pair may not exist on the DEX\. Proceed with caution\._"
                )
                keyboard = [[InlineKeyboardButton("✅ Proceed Anyway", callback_data="bots:xemm_proceed_anyway")]]
                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id, message_id=message_id,
                        text=warning_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                return

            # Imposta la pair per il taker exchange
            config["taker_trading_pair"] = pair
            set_controller_config(context, config)

            # VAI ALL'AMOUNT (Step 5)
            context.user_data["xemm_wizard_step"] = "total_amount_quote"

            # Mostra lo step dell'amount
            await _xemm_show_amount_step(update, context, target_message_id=message_id)
        elif step == "taker_connector":
            # Questo step è gestito esclusivamente dai callback button
            pass
        # ========== TOTAL AMOUNT QUOTE (ispirato a arbitrage) ==========
        elif step == "total_amount_quote":
            try:
                # Rimuovi simboli di valuta e virgole
                cleaned_input = user_input.strip().replace("$", "").replace(",", "")

                # Controlla se è un numero valido
                amount = float(cleaned_input)

                if amount <= 0:
                    raise ValueError("Amount must be positive")

                config["total_amount_quote"] = amount
                set_controller_config(context, config)
                context.user_data["xemm_wizard_step"] = "final"

                # Mostra messaggio di caricamento (come arbitrage)
                wizard_chat_id = context.user_data.get("xemm_wizard_chat_id", chat_id)
                mp = config.get("maker_trading_pair", "")

                # Crea un messaggio temporaneo per il caricamento
                tmp_msg = await context.bot.send_message(
                    chat_id=wizard_chat_id,
                    text=r"*🔄 XEMM Multi Levels \- New Config*" + "\n\n"
                         f"⏳ Fetching prices for `{escape_markdown_v2(mp)}`\\.\\.\\.",
                    parse_mode="MarkdownV2",
                )
                context.user_data["xemm_wizard_message_id"] = tmp_msg.message_id
                await _xemm_show_final_step(update, context)

            except ValueError:
                # Input non valido - mostra errore (come arbitrage)
                mc = config.get("maker_connector", "")
                mp = config.get("maker_trading_pair", "")
                tc = config.get("taker_connector", "")
                tp = config.get("taker_trading_pair", "")

                keyboard = [
                    [
                        InlineKeyboardButton("$100", callback_data="bots:xemm_amount:100"),
                        InlineKeyboardButton("$500", callback_data="bots:xemm_amount:500"),
                        InlineKeyboardButton("$1000", callback_data="bots:xemm_amount:1000"),
                    ],
                    [
                        InlineKeyboardButton("$2000", callback_data="bots:xemm_amount:2000"),
                        InlineKeyboardButton("$5000", callback_data="bots:xemm_amount:5000"),
                        InlineKeyboardButton("$10000", callback_data="bots:xemm_amount:10000"),
                    ],
                    [
                        InlineKeyboardButton("⬅️ Back", callback_data="bots:xemm_back_to_taker_connector"),
                        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                    ],
                ]

                error_text = (
                    r"*🔄 XEMM Multi Levels \- Step 5/6*" + "\n\n"
                    f"🏭 `{escape_markdown_v2(mc)}` \\| `{escape_markdown_v2(mp)}`\n"
                    f"⚡ `{escape_markdown_v2(tc)}` \\| `{escape_markdown_v2(tp)}`\n\n"
                    r"💰 *Total Amount \(Quote\)*" + "\n"
                    r"⚠️ *Invalid amount\. Enter a positive number \(e\.g\. 500\)*" + "\n\n"
                    r"_Select or type an amount:_"
                )

                if message_id:
                    try:
                        await context.bot.edit_message_text(
                            chat_id=wizard_chat_id,
                            message_id=message_id,
                            text=error_text,
                            parse_mode="MarkdownV2",
                            reply_markup=InlineKeyboardMarkup(keyboard),
                        )
                    except Exception:
                        pass
                else:
                    # Se non c'è message_id, invia un nuovo messaggio
                    new_msg = await context.bot.send_message(
                        chat_id=wizard_chat_id,
                        text=error_text,
                        parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    context.user_data["xemm_wizard_message_id"] = new_msg.message_id

        # ========== FINAL (edit campi) ==========
        elif step == "final":
            if "=" in user_input:
                for line in user_input.strip().split("\n"):
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    field, value = line.split("=", 1)
                    field = field.strip().lower()
                    value = value.strip()
                    try:
                        if field in ("total_amount_quote", "min_profitability", "max_profitability"):
                            config[field] = float(value)
                        elif field == "max_executors_imbalance":
                            config[field] = int(float(value))
                        elif field in ("buy_levels_targets_amount", "sell_levels_targets_amount"):
                            # Validazione formato (es. "0.003,10-0.006,20")
                            parts = value.split("-")
                            valid = True
                            for part in parts:
                                vals = part.split(",")
                                if len(vals) != 2:
                                    valid = False
                                    break
                                try:
                                    float(vals[0])
                                    float(vals[1])
                                except ValueError:
                                    valid = False
                                    break
                            if valid:
                                config[field] = value
                        else:
                            config[field] = value
                    except Exception:
                        pass
                set_controller_config(context, config)
                await _xemm_show_final_step(update, context)

    except Exception as e:
        logger.error(f"XEMM wizard input error: {e}", exc_info=True)

async def handle_xemm_back_to_pair(update, context) -> None:
    """Go back to trading pair selection"""
    context.user_data["xemm_wizard_step"] = "trading_pair"
    await _xemm_show_pair_step(update, context)


async def handle_xemm_proceed_anyway(update, context) -> None:
    """Handle proceeding with DEX pair validation warning"""
    query = update.callback_query
    pair = context.user_data.get("xemm_pending_pair")
    step = context.user_data.get("xemm_wizard_step")
    config = get_controller_config(context)

    if not pair:
        await query.answer("No pending pair found", show_alert=True)
        return

    if step == "maker_pair":
        # Imposta la pair per il maker exchange
        config["maker_trading_pair"] = pair
        set_controller_config(context, config)
        # Vai al taker connector (Step 3)
        context.user_data["xemm_wizard_step"] = "taker_connector"
        await _xemm_show_connector_step(update, context, role="taker")

    elif step == "taker_pair":
        # Imposta la pair per il taker exchange
        config["taker_trading_pair"] = pair
        set_controller_config(context, config)
        # Vai all'amount (Step 5)
        context.user_data["xemm_wizard_step"] = "total_amount_quote"
        await _xemm_show_amount_step(update, context)

    else:
        # Fallback: vai all'amount (comportamento originale)
        context.user_data["xemm_wizard_step"] = "total_amount_quote"

        mc = config.get("maker_connector", "")
        mp = config.get("maker_trading_pair", "")
        tc = config.get("taker_connector", "")
        tp = config.get("taker_trading_pair", "")

        # Se non c'è maker_pair, usa la pair in sospeso
        if not mp and pair:
            mp = pair
            config["maker_trading_pair"] = pair
            set_controller_config(context, config)

        keyboard = [
            [
                InlineKeyboardButton("$100", callback_data="bots:xemm_amount:100"),
                InlineKeyboardButton("$500", callback_data="bots:xemm_amount:500"),
                InlineKeyboardButton("$1000", callback_data="bots:xemm_amount:1000"),
            ],
            [
                InlineKeyboardButton("$2000", callback_data="bots:xemm_amount:2000"),
                InlineKeyboardButton("$5000", callback_data="bots:xemm_amount:5000"),
                InlineKeyboardButton("$10000", callback_data="bots:xemm_amount:10000"),
            ],
            [
                InlineKeyboardButton("⬅️ Back", callback_data="bots:xemm_back_to_taker_pair"),
                InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
            ],
        ]

        amount_text = (
            r"*🔄 XEMM Multi Levels \- Step 5/6*" + "\n\n"
            f"🏭 `{escape_markdown_v2(mc)}` \\| `{escape_markdown_v2(mp)}`\n"
            f"⚡ `{escape_markdown_v2(tc)}` \\| `{escape_markdown_v2(tp)}`\n\n"
            r"💰 *Total Amount \(USDT\)*" + "\n"
            r"_Select or type an amount:_"
        )

        await query.message.edit_text(
            amount_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
# ============================================
# MACD BB V1 WIZARD
# ============================================
# Steps: connector → pair → (leverage) → amount → interval+chart → final
# Prefisso handler: macdbb_

async def show_new_macd_bb_v1_form(update, context) -> None:
    """Start the MACD BB V1 wizard - Step 1: Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    # Clear cached data
    for key in ["macdbb_current_price", "macdbb_candles", "macdbb_candles_interval",
                "macdbb_chart_interval", "macdbb_trading_rules"]:
        context.user_data.pop(key, None)

    # Fetch existing configs for sequence numbering
    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()
        context.user_data["controller_configs_list"] = configs
    except Exception as e:
        logger.warning(f"Could not fetch existing configs: {e}")

    init_new_controller_config(context, "macd_bb_v1")
    context.user_data["bots_state"] = "macdbb_wizard"
    context.user_data["macdbb_wizard_step"] = "connector_name"
    context.user_data["macdbb_wizard_message_id"] = query.message.message_id
    context.user_data["macdbb_wizard_chat_id"] = query.message.chat_id

    await _macdbb_show_connector_step(update, context)


async def _macdbb_show_connector_step(update, context) -> None:
    """MACD BB Step 1: Select Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, server_name = await get_bots_client(chat_id, context.user_data)
        cex_connectors = await get_available_cex_connectors(
            context.user_data, client, server_name=server_name
        )

        if not cex_connectors:
            keyboard = [
                [InlineKeyboardButton("🔑 Configure API Keys", callback_data="config_api_keys")],
                [InlineKeyboardButton("« Back", callback_data="bots:main_menu")],
            ]
            await query.message.edit_text(
                r"*📊 MACD BB V1 \- New Config*" + "\n\n"
                r"⚠️ No CEX connectors available\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        keyboard = []
        row = []
        for connector in cex_connectors:
            row.append(InlineKeyboardButton(
                f"🏦 {connector}", callback_data=f"bots:macdbb_connector:{connector}"
            ))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")])

        await query.message.edit_text(
            r"*📊 MACD BB V1*" + "\n\n"
            r"Combines Bollinger Bands with MACD confirmation\. "
            r"Enters LONG when BBP is low AND MACD histogram is rising\. "
            r"Enters SHORT when BBP is high AND MACD histogram is falling\." + "\n\n"
            r"─────────────────────────" + "\n\n"
            r"*Step 1: Select Exchange*",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True,
        )

    except Exception as e:
        logger.error(f"MACD BB connector step error: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        await query.message.edit_text(
            format_error_message(f"Error: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_macdbb_wizard_connector(update, context, connector: str) -> None:
    """Handle connector selection"""
    config = get_controller_config(context)
    config["connector_name"] = connector
    config["candles_connector"] = connector  # Auto-set candles connector = same exchange
    set_controller_config(context, config)
    context.user_data["macdbb_wizard_step"] = "trading_pair"
    await _macdbb_show_pair_step(update, context)

async def _macdbb_show_pair_step(update, context) -> None:
    """MACD BB Step 2: Select Trading Pair"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")

    context.user_data["bots_state"] = "macdbb_wizard_input"
    context.user_data["macdbb_wizard_step"] = "trading_pair"

    # Recent pairs from existing configs
    existing_configs = context.user_data.get("controller_configs_list", [])
    recent_pairs = []
    seen = set()
    for cfg in reversed(existing_configs):
        pair = cfg.get("trading_pair", "")
        if pair and pair not in seen:
            seen.add(pair)
            recent_pairs.append(pair)
            if len(recent_pairs) >= 6:
                break

    keyboard = []
    if recent_pairs:
        row = []
        for pair in recent_pairs:
            row.append(InlineKeyboardButton(pair, callback_data=f"bots:macdbb_pair:{pair}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="bots:macdbb_back_to_connector"),
        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
    ])

    is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()
    total_steps = 6 if is_perp else 4
    current_step = 2

    message_text = (
        rf"*📊 MACD BB V1 \- Step {current_step}/{total_steps}*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}`" + "\n\n"
        r"🔗 *Trading Pair*" + "\n\n"
        r"Select a recent pair or type a new one:"
    )

    # ========== SALVA MESSAGE_ID ==========
    try:
        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        context.user_data["macdbb_wizard_message_id"] = query.message.message_id
    except Exception:
        try:
            await query.message.delete()
        except Exception:
            pass
        new_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        context.user_data["macdbb_wizard_message_id"] = new_msg.message_id
        context.user_data["macdbb_wizard_chat_id"] = query.message.chat_id

async def handle_macdbb_wizard_pair(update, context, pair: str) -> None:
    """Handle pair selection via button"""
    config = get_controller_config(context)
    config["trading_pair"] = pair
    config["candles_trading_pair"] = pair
    set_controller_config(context, config)

    connector = config.get("connector_name", "").lower()
    is_perp = "_perpetual" in connector or "_margin" in connector

    if is_perp:
        context.user_data["macdbb_wizard_step"] = "leverage"
        await _macdbb_show_leverage_step(update, context)
    else:
        config["leverage"] = 1
        config["position_mode"] = "ONEWAY"
        set_controller_config(context, config)
        context.user_data["macdbb_wizard_step"] = "total_amount_quote"
        await _macdbb_show_amount_step(update, context)

async def _macdbb_show_leverage_step(update, context) -> None:
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    context.user_data["bots_state"] = "macdbb_wizard_input"
    context.user_data["macdbb_wizard_step"] = "leverage"

    keyboard = [
        [
            InlineKeyboardButton("1x", callback_data="bots:macdbb_leverage:1"),
            InlineKeyboardButton("5x", callback_data="bots:macdbb_leverage:5"),
            InlineKeyboardButton("10x", callback_data="bots:macdbb_leverage:10"),
        ],
        [
            InlineKeyboardButton("20x", callback_data="bots:macdbb_leverage:20"),
            InlineKeyboardButton("50x", callback_data="bots:macdbb_leverage:50"),
            InlineKeyboardButton("75x", callback_data="bots:macdbb_leverage:75"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:macdbb_back_to_pair"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    # ========== SALVA message_id PER ERROR HANDLING ==========
    context.user_data["macdbb_wizard_message_id"] = query.message.message_id
    context.user_data["macdbb_wizard_chat_id"] = query.message.chat_id
    # ========================================================

    await query.message.edit_text(
        r"*📊 MACD BB V1 \- Step 3/6*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`" + "\n\n"
        r"⚡ *Select Leverage*" + "\n"
        r"_Or type a value \(e\.g\. 20\)_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_macdbb_wizard_leverage(update, context, leverage: int) -> None:
    config = get_controller_config(context)
    config["leverage"] = leverage
    set_controller_config(context, config)
    context.user_data["macdbb_wizard_step"] = "position_mode"
    await _macdbb_show_position_mode_step(update, context)

async def _macdbb_show_position_mode_step(update, context) -> None:
    """MACD BB Wizard Step: Select Position Mode (Perpetual/Margin only)"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = escape_markdown_v2(config.get("connector_name", ""))
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 1)

    context.user_data["macdbb_wizard_message_id"] = query.message.message_id
    context.user_data["macdbb_wizard_chat_id"] = query.message.chat_id

    keyboard = [
        [
            InlineKeyboardButton("🔒 ONEWAY", callback_data="bots:macdbb_position_mode:ONEWAY"),
            InlineKeyboardButton("🔄 HEDGE", callback_data="bots:macdbb_position_mode:HEDGE"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:macdbb_back_to_leverage"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]


    # CORREZIONE: escape di tutti i caratteri speciali MarkdownV2
    # I caratteri speciali sono: _ * [ ] ( ) ~ ` > # + - = | { } . !
    escaped_connector = escape_markdown_v2(connector)
    escaped_pair = escape_markdown_v2(pair)

    await query.message.edit_text(
        f"*📊 MACD BB V1 \\- Step 4/6*\n\n"
        f"🏦 `{escaped_connector}` \\| 🔗 `{escaped_pair}` \\| ⚡ `{leverage}x`\n\n"
        r"🎯 *Position Mode*" + "\n\n"
        r"• *HEDGE*: Can hold both long and short positions simultaneously" + "\n"
        r"• *ONEWAY*: Can only hold positions in one direction \(long OR short\)",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_macdbb_position_mode(update, context, mode: str) -> None:
    config = get_controller_config(context)
    config["position_mode"] = mode
    set_controller_config(context, config)
    context.user_data["macdbb_wizard_step"] = "total_amount_quote"
    await _macdbb_show_amount_step(update, context)

async def _macdbb_show_amount_step(update, context) -> None:
    """MACD BB Step: Enter Total Amount"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 1)
    pos_mode = config.get("position_mode", "HEDGE")

    context.user_data["bots_state"] = "macdbb_wizard_input"
    context.user_data["macdbb_wizard_step"] = "total_amount_quote"
    is_perp = "_perpetual" in connector.lower() or "_margin" in connector.lower()
    total_steps = 6 if is_perp else 4
    current_step = 5 if is_perp else 3

    # Fetch balance
    balance_text = ""
    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        balances = await get_cex_balances(context.user_data, client, connector)
        quote = pair.split("-")[1] if "-" in pair else "USDT"
        available = balances.get(quote, {}).get("available", 0)
        if available:
            balance_text = f"\n\n💰 Available `{escape_markdown_v2(quote)}`: `{available:,.2f}`"
    except Exception:
        pass

    # Gestione dinamica del tasto Back
    back_callback = "bots:macdbb_back_to_position_mode" if is_perp else "bots:macdbb_back_to_pair"

    keyboard = [
        [
            InlineKeyboardButton("$100", callback_data="bots:macdbb_amount:100"),
            InlineKeyboardButton("$500", callback_data="bots:macdbb_amount:500"),
            InlineKeyboardButton("$1000", callback_data="bots:macdbb_amount:1000"),
        ],
        [
            InlineKeyboardButton("$2000", callback_data="bots:macdbb_amount:2000"),
            InlineKeyboardButton("$5000", callback_data="bots:macdbb_amount:5000"),
            InlineKeyboardButton("$10000", callback_data="bots:macdbb_amount:10000"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data=back_callback),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    header_info = f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
    if is_perp:
        header_info += f" \\| ⚡ `{leverage}x` \\| 🎯 `{pos_mode}`"

    message_text = (
        rf"*📊 MACD BB V1 \- Step {current_step}/{total_steps}*" + "\n\n"
        + header_info + balance_text + "\n\n"
        r"💰 *Total Amount \(USDT\)*" + "\n"
        r"_Select or type an amount:_"
    )

    # ========== SALVA MESSAGE_ID ==========
    target_chat_id = chat_id
    if query and query.message:
        target_chat_id = query.message.chat_id
        try:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            context.user_data["macdbb_wizard_message_id"] = query.message.message_id
            return
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass

    new_msg = await context.bot.send_message(
        chat_id=target_chat_id,
        text=message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    context.user_data["macdbb_wizard_message_id"] = new_msg.message_id
    context.user_data["macdbb_wizard_chat_id"] = target_chat_id

async def handle_macdbb_wizard_amount(update, context, amount: float) -> None:
    """Handle amount selection"""
    query = update.callback_query
    config = get_controller_config(context)
    config["total_amount_quote"] = amount
    set_controller_config(context, config)

    pair = config.get("trading_pair", "")
    await query.message.edit_text(
        r"*📊 MACD BB V1 \- New Config*" + "\n\n"
        f"⏳ *Loading chart for* `{escape_markdown_v2(pair)}`\\.\\.\\.  " + "\n\n"
        r"_Fetching market data\.\.\._",
        parse_mode="MarkdownV2",
    )

    context.user_data["macdbb_wizard_step"] = "final"
    await _macdbb_show_final_step(update, context)

async def _macdbb_show_final_step(update, context, interval: str = None) -> None:
    """MACD BB Final Step: Chart + Config Summary + Analysis"""
    query = update.callback_query
    msg = query.message if query else update.message
    chat_id = update.effective_chat.id
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    total_amount = config.get("total_amount_quote", 1000)
    leverage = config.get("leverage", 1)
    bb_length = config.get("bb_length", 100)
    macd_fast = config.get("macd_fast", 21)
    macd_slow = config.get("macd_slow", 42)
    macd_signal_period = config.get("macd_signal", 9)

    if interval is None:
        interval = context.user_data.get("macdbb_chart_interval", config.get("interval", "3m"))
    context.user_data["macdbb_chart_interval"] = interval
    config["interval"] = interval
    set_controller_config(context, config)

    current_price = context.user_data.get("macdbb_current_price")
    candles = context.user_data.get("macdbb_candles")

    try:
        cached_interval = context.user_data.get("macdbb_candles_interval", interval)
        if not current_price or interval != cached_interval:
            try:
                await msg.edit_text(
                    r"*📊 MACD BB V1 \- New Config*" + "\n\n"
                    f"⏳ Fetching market data for `{escape_markdown_v2(pair)}`\\.\\.\\.",
                    parse_mode="MarkdownV2",
                )
            except Exception:
                pass
            client, _ = await get_bots_client(chat_id, context.user_data)
            candles_connector = connector.replace("_perpetual", "").replace("_margin", "").replace("_spot", "")
            current_price = await fetch_current_price(client, connector, pair)

            if current_price:
                # CORREZIONE QUI - usa macdbb_current_price
                context.user_data["macdbb_current_price"] = current_price
                candles = await fetch_candles(
                    client, candles_connector, pair, interval=interval, max_records=420
                )
                context.user_data["macdbb_candles"] = candles
                context.user_data["macdbb_candles_interval"] = interval

        if not current_price:
            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="bots:main_menu")]]
            await msg.edit_text(
                r"*❌ Error*" + "\n\n"
                f"Could not fetch price for `{escape_markdown_v2(pair)}`\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        candles_list = candles.get("data", []) if isinstance(candles, dict) else (candles or [])

        if candles_list:
            last_close = candles_list[-1].get("close") or candles_list[-1].get("c")
            if last_close:
                current_price = float(last_close)
                context.user_data["macdbb_current_price"] = current_price

        # Generate config ID if not set
        if not config.get("id"):
            existing_configs = context.user_data.get("controller_configs_list", [])
            from .controllers.macd_bb_v1.config import generate_id as macdbb_generate_id
            config["id"] = macdbb_generate_id(config, existing_configs)

        from .controllers.macd_bb_v1.analysis import analyze_candles_for_macd_bb, format_macd_bb_analysis
        analysis = analyze_candles_for_macd_bb(candles_list, bb_length=config.get("bb_length", 100), bb_std=config.get("bb_std", 2.0), macd_fast=macd_fast, macd_slow=macd_slow, macd_signal=macd_signal_period)

        # Auto-save suggested thresholds
        if config.get("bb_long_threshold", 0.0) == 0.0:
            config["bb_long_threshold"] = analysis["suggested_long_threshold"]
        if config.get("bb_short_threshold", 1.0) == 1.0:
            config["bb_short_threshold"] = analysis["suggested_short_threshold"]
        set_controller_config(context, config)

        position_mode = config.get("position_mode", "HEDGE")
        stop_loss = config.get("stop_loss", 0.05)
        take_profit = config.get("take_profit", 0.03)
        max_exec = config.get("max_executors_per_side", 1)
        cooldown = config.get("cooldown_time", 60)
        bb_std = config.get("bb_std", 2.0)
        bb_long = config.get("bb_long_threshold", 0.0)
        bb_short = config.get("bb_short_threshold", 1.0)
        ts = config.get("trailing_stop", {}) or {}
        ts_act = ts.get("activation_price", 0.015) if isinstance(ts, dict) else 0.015
        ts_delta = ts.get("trailing_delta", 0.005) if isinstance(ts, dict) else 0.005

        context.user_data["bots_state"] = "macdbb_wizard_input"
        context.user_data["macdbb_wizard_step"] = "final"

        interval_options = ["1m", "3m", "5m", "15m", "1h"]
        interval_row = [
            InlineKeyboardButton(
                f"✓ {opt}" if opt == interval else opt,
                callback_data=f"bots:macdbb_interval:{opt}"
            )
            for opt in interval_options
        ]

        # ========== SALVA ANALISI PER I BOTTONI STRATEGIA ==========
        context.user_data["macdbb_analysis"] = analysis
        strategy_row = [
            InlineKeyboardButton("🎯 Scalp", callback_data="bots:macdbb_set_strat:scalping"),
            InlineKeyboardButton("⚖️ Swing", callback_data="bots:macdbb_set_strat:swing"),
            InlineKeyboardButton("🤖 Auto", callback_data="bots:macdbb_set_strat:auto"),
        ]


        is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()
        final_step = 6 if is_perp else 4

        keyboard = [
            interval_row,
            strategy_row,  # <--- AGGIUNGI QUESTA RIGA
            [InlineKeyboardButton("💾 Save Config", callback_data="bots:macdbb_save")],
            [
                InlineKeyboardButton("⬅️ Back", callback_data="bots:macdbb_back_to_amount"),
                InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
            ],
        ]

        config_text = (
            rf"*📊 MACD BB V1 \- Step {final_step}/{final_step} \(Final\)*" + "\n\n"
            f"*{escape_markdown_v2(pair)}*\n"
            f"Price: `{current_price:,.6g}` \\| BB: `{bb_length}` \\| MACD: `{macd_fast}/{macd_slow}/{macd_signal_period}` \\| Interval: `{interval}`\n\n"
            f"`connector_name={escape_markdown_v2(connector)}`\n"
            f"`trading_pair={escape_markdown_v2(pair)}`\n"
            f"`total_amount_quote={total_amount:.0f}`\n"
            f"`leverage={leverage}`\n"
            f"`position_mode={escape_markdown_v2(position_mode)}`\n"
            f"`max_executors_per_side={max_exec}`\n"
            f"`cooldown_time={cooldown}`\n"
            f"`stop_loss={stop_loss}`\n"
            f"`take_profit={take_profit}`\n"
            f"`trailing_stop_activation={ts_act}`\n"
            f"`trailing_stop_delta={ts_delta}`\n"
            f"`interval={interval}`\n"
            f"`bb_length={bb_length}`\n"
            f"`bb_std={bb_std}`\n"
            f"`bb_long_threshold={bb_long}`\n"
            f"`bb_short_threshold={bb_short}`\n"
            f"`macd_fast={macd_fast}`\n"
            f"`macd_slow={macd_slow}`\n"
            f"`macd_signal={macd_signal_period}`\n\n"
            r"_Edit: `field=value`_"
        )

        # ========== ANALYSIS TEXT - USARE TRIPLI BACKTICK ==========
        analysis_text = format_macd_bb_analysis(analysis)
        config_text += "\n\n```\n" + analysis_text + "\n```"
        # ==========================================================

        # ========== FIX CAPTION TOO LONG ==========
        MAX_CAPTION_LEN = 950
        if len(config_text) > MAX_CAPTION_LEN:
            truncation_note = "\n\n_...truncated due to length limit. Edit via Configs menu._"
            max_allowed = MAX_CAPTION_LEN - len(truncation_note)
            config_text = config_text[:max_allowed] + truncation_note
        # ==========================================

        # ========== COME DMan V3 ==========
        if candles_list:
            from .controllers.macd_bb_v1.chart import generate_chart as macdbb_chart
            chart_bytes = macdbb_chart(config, candles_list, current_price)

            try:
                await msg.delete()
            except Exception:
                pass

            new_msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=chart_bytes,
                caption=config_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            context.user_data["macdbb_wizard_message_id"] = new_msg.message_id
            context.user_data["macdbb_wizard_chat_id"] = chat_id
        else:
            try:
                await msg.edit_text(
                    text=config_text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            except Exception:
                new_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=config_text,
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["macdbb_wizard_message_id"] = new_msg.message_id

    except Exception as e:
        logger.error(f"MACD BB final step error: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        try:
            await msg.edit_text(
                format_error_message(f"Error: {str(e)}"),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception:
            pass


async def handle_macdbb_interval_change(update, context, interval: str) -> None:
    """Change chart interval"""
    context.user_data["macdbb_candles"] = None
    context.user_data["macdbb_candles_interval"] = None
    await _macdbb_show_final_step(update, context, interval=interval)

async def handle_macdbb_set_strategy(update, context, strat_key: str) -> None:
    """Handle MACD BB strategy selection from final step buttons."""
    query = update.callback_query
    config = get_controller_config(context)

    analysis = context.user_data.get("macdbb_analysis", {})
    natr = analysis.get("natr", 0.01)

    from .controllers.macd_bb_v1.analysis import get_macd_bb_strategy_suggestions
    strats = get_macd_bb_strategy_suggestions(natr, analysis)

    if strat_key in strats:
        selected = strats[strat_key]

        # Aggiorna i parametri
        config["bb_length"] = selected.get("bb_length", 100)
        config["bb_std"] = selected.get("bb_std", 2.0)
        config["macd_fast"] = selected.get("macd_fast", 21)
        config["macd_slow"] = selected.get("macd_slow", 42)
        config["macd_signal"] = selected.get("macd_signal", 9)
        config["bb_long_threshold"] = selected.get("bb_long_threshold", 0.0)
        config["bb_short_threshold"] = selected.get("bb_short_threshold", 1.0)
        config["take_profit"] = selected.get("take_profit", 0.03)
        config["stop_loss"] = selected.get("stop_loss", 0.05)

        # ========== AGGIUNGI TRAILING STOP ==========
        if "trailing_stop_activation" in selected:
            if not isinstance(config.get("trailing_stop"), dict):
                config["trailing_stop"] = {"activation_price": 0.015, "trailing_delta": 0.005}
            config["trailing_stop"]["activation_price"] = selected["trailing_stop_activation"]
            config["trailing_stop"]["trailing_delta"] = selected["trailing_stop_delta"]
        # ===========================================

        set_controller_config(context, config)

        vol_regime = selected.get("volatility_regime", "moderate")
        await query.answer(f"✅ {selected['label']} applicata (vol: {vol_regime})")

        return await _macdbb_show_final_step(update, context)

async def handle_macdbb_save(update, context) -> None:
    """Save MACD BB V1 configuration"""
    query = update.callback_query
    config = get_controller_config(context)

    # ========== PULISCI I CAMPI NON NECESSARI ==========
    config.pop("candles_config", None)
    config.pop("manual_kill_switch", None)
    # ===================================================

    config_id = config.get("id", "")
    chat_id = query.message.chat_id

    try:
        await query.message.delete()
    except Exception:
        pass

    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="Saving `" + escape_markdown_v2(config_id) + "`\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        await client.controllers.create_or_update_controller_config(config_id, config)

        for key in ["macdbb_wizard_step", "macdbb_wizard_message_id", "macdbb_wizard_chat_id",
                    "macdbb_current_price", "macdbb_candles", "macdbb_candles_interval",
                    "macdbb_chart_interval"]:
            context.user_data.pop(key, None)
        context.user_data.pop("bots_state", None)

        keyboard = [
            [InlineKeyboardButton("Create Another", callback_data="bots:new_macd_bb_v1")],
            [InlineKeyboardButton("Back to Configs", callback_data="bots:controller_configs")],
        ]
        await status_msg.edit_text(
            "*Config Saved\\!*\n\n"
            "Controller `" + escape_markdown_v2(config_id) + "` saved successfully\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"MACD BB save error: {e}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton("Try Again", callback_data="bots:macdbb_save")],
            [InlineKeyboardButton("Back", callback_data="bots:controller_configs")],
        ]
        await status_msg.edit_text(
            format_error_message(f"Failed to save: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

# Back handlers
async def handle_macdbb_back_to_connector(update, context) -> None:
    context.user_data["macdbb_wizard_step"] = "connector_name"
    await _macdbb_show_connector_step(update, context)


async def handle_macdbb_back_to_pair(update, context) -> None:
    context.user_data["macdbb_wizard_step"] = "trading_pair"
    await _macdbb_show_pair_step(update, context)


async def handle_macdbb_back_to_leverage(update, context) -> None:
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    # Usa la stessa logica degli altri wizard
    is_perp = "_perpetual" in connector or "_margin" in connector
    if is_perp:
        context.user_data["macdbb_wizard_step"] = "leverage"
        await _macdbb_show_leverage_step(update, context)
    else:
        await handle_macdbb_back_to_pair(update, context)

async def handle_macdbb_back_to_amount(update, context) -> None:
    """Go back to amount step"""
    # Non cancellare il messaggio!
    # Lascia che _macdbb_show_amount_step lo editi

    context.user_data["macdbb_wizard_step"] = "total_amount_quote"
    context.user_data.pop("macdbb_current_price", None)
    context.user_data.pop("macdbb_candles", None)
    context.user_data.pop("macdbb_candles_interval", None)

    await _macdbb_show_amount_step(update, context)

async def handle_macdbb_back_to_position_mode(update, context) -> None:
    """Go back to position mode step"""
    context.user_data["macdbb_wizard_step"] = "position_mode"
    await _macdbb_show_position_mode_step(update, context)

async def handle_macdbb_pair_select(
    update: Update, context: ContextTypes.DEFAULT_TYPE, trading_pair: str
) -> None:
    """Handle selection of a suggested trading pair in MACD BB V1 wizard"""
    query = update.callback_query
    config = get_controller_config(context)
    chat_id = update.effective_chat.id

    # Clear old market data
    for key in ["macdbb_current_price", "macdbb_candles", "macdbb_candles_interval"]:
        context.user_data.pop(key, None)

    config["trading_pair"] = trading_pair
    config["candles_trading_pair"] = trading_pair
    set_controller_config(context, config)

    # Move to next step based on connector type
    connector = config.get("connector_name", "")
    is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()
    if is_perp:
        context.user_data["macdbb_wizard_step"] = "leverage"
        await _macdbb_show_leverage_step(update, context)
    else:
        config["leverage"] = 1
        config["position_mode"] = "ONEWAY"
        set_controller_config(context, config)
        context.user_data["macdbb_wizard_step"] = "total_amount_quote"
        await _macdbb_show_amount_step(update, context)

async def process_macdbb_wizard_input(update, context, user_input: str) -> None:
    """Process text input during MACD BB V1 wizard"""
    step = context.user_data.get("macdbb_wizard_step")
    chat_id = update.effective_chat.id
    config = get_controller_config(context)
    message_id = context.user_data.get("macdbb_wizard_message_id")
    wizard_chat_id = context.user_data.get("macdbb_wizard_chat_id", chat_id)

    try:
        try:
            await update.message.delete()
        except Exception:
            pass

        # ========== GESTISCI INPUT MANUALE PER TRADING PAIR ==========
        if step == "trading_pair":
            pair = user_input.upper().strip().replace("/", "-").replace("_", "-")
            connector = config.get("connector_name", "")

            # ========== VALIDAZIONE BASE: DEVE CONTENERE IL TRATTINO (come DMan/Arb) ==========
            if "-" not in pair:
                message_id = context.user_data.get("macdbb_wizard_message_id")
                wizard_chat_id = context.user_data.get("macdbb_wizard_chat_id", chat_id)

                keyboard = [
                    [InlineKeyboardButton("⬅️ Back", callback_data="bots:macdbb_back_to_connector")]
                ]

                # Mostra il contesto con exchange selezionato (come DMan/Arb)
                context_text = f"🏦 `{escape_markdown_v2(connector)}`\n\n"

                err_text = (
                    r"*📊 MACD BB V1 \- Step 2*" + "\n\n"
                    + context_text
                    + r"⚠️ *Invalid format\. Use BASE\-QUOTE \(e\.g\. BTC\-USDT\)*" + "\n\n"
                    + r"Type the pair again:"
                )

                if message_id:
                    try:
                        await context.bot.edit_message_text(
                            chat_id=wizard_chat_id, message_id=message_id,
                            text=err_text, parse_mode="MarkdownV2",
                            reply_markup=InlineKeyboardMarkup(keyboard),
                        )
                    except Exception as e:
                        logger.error(f"Error editing message: {e}")
                        # Fallback: invia nuovo messaggio
                        msg = await context.bot.send_message(
                            chat_id=chat_id, text=err_text, parse_mode="MarkdownV2",
                            reply_markup=InlineKeyboardMarkup(keyboard),
                        )
                        context.user_data["macdbb_wizard_message_id"] = msg.message_id
                else:
                    msg = await context.bot.send_message(
                        chat_id=chat_id, text=err_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    context.user_data["macdbb_wizard_message_id"] = msg.message_id
                return

            # ========== 2. VALIDAZIONE SULL'EXCHANGE (CON SUGGERIMENTI) come DMan/Arb ==========
            client, _ = await get_bots_client(chat_id, context.user_data)
            is_valid, error_msg, suggestions, correct_pair = (
                await validate_trading_pair(context.user_data, client, connector, pair)
            )

            if not is_valid:
                message_id = context.user_data.get("macdbb_wizard_message_id")
                wizard_chat_id = context.user_data.get("macdbb_wizard_chat_id", chat_id)

                keyboard = []
                for sugg in suggestions[:4]:
                    keyboard.append([InlineKeyboardButton(sugg, callback_data=f"bots:macdbb_pair_select:{sugg}")])
                keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="bots:macdbb_back_to_connector")])
                keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")])

                # Mostra il contesto con exchange selezionato (come DMan/Arb)
                context_text = f"🏦 `{escape_markdown_v2(connector)}`\n\n"

                err_text = (
                    r"*📊 MACD BB V1 \- Step 2*" + "\n\n"
                    + context_text
                    + f"❌ `{escape_markdown_v2(pair)}` not found on `{escape_markdown_v2(connector)}`\\.\n\n"
                    + r"*Did you mean?*"
                )

                if message_id:
                    try:
                        await context.bot.edit_message_text(
                            chat_id=wizard_chat_id, message_id=message_id,
                            text=err_text, parse_mode="MarkdownV2",
                            reply_markup=InlineKeyboardMarkup(keyboard),
                        )
                    except Exception as e:
                        logger.error(f"Error editing message: {e}")
                        msg = await context.bot.send_message(
                            chat_id=chat_id, text=err_text, parse_mode="MarkdownV2",
                            reply_markup=InlineKeyboardMarkup(keyboard),
                        )
                        context.user_data["macdbb_wizard_message_id"] = msg.message_id
                else:
                    msg = await context.bot.send_message(
                        chat_id=chat_id, text=err_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    context.user_data["macdbb_wizard_message_id"] = msg.message_id
                return

            if correct_pair:
                pair = correct_pair

            config["trading_pair"] = pair
            config["candles_trading_pair"] = pair
            for key in ["macdbb_current_price", "macdbb_candles", "macdbb_candles_interval"]:
                context.user_data.pop(key, None)
            set_controller_config(context, config)
            # Vai al passo successivo (leverage o amount)
            connector = config.get("connector_name", "")
            is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()

            if is_perp:
                context.user_data["macdbb_wizard_step"] = "leverage"
                # ========== RICOSTRUISCI IL MESSAGGIO QUI (come DMan) ==========
                message_id = context.user_data.get("macdbb_wizard_message_id")
                wizard_chat_id = context.user_data.get("macdbb_wizard_chat_id", chat_id)

                keyboard = [
                    [
                        InlineKeyboardButton("1x", callback_data="bots:macdbb_leverage:1"),
                        InlineKeyboardButton("5x", callback_data="bots:macdbb_leverage:5"),
                        InlineKeyboardButton("10x", callback_data="bots:macdbb_leverage:10"),
                    ],
                    [
                        InlineKeyboardButton("20x", callback_data="bots:macdbb_leverage:20"),
                        InlineKeyboardButton("50x", callback_data="bots:macdbb_leverage:50"),
                        InlineKeyboardButton("75x", callback_data="bots:macdbb_leverage:75"),
                    ],
                    [
                        InlineKeyboardButton("⬅️ Back", callback_data="bots:macdbb_back_to_pair"),
                        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                    ],
                ]

                text = (
                    r"*📊 MACD BB V1 \- Step 3/6*" + "\n\n"
                    f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`" + "\n\n"
                    r"⚡ *Select Leverage*" + "\n"
                    r"_Or type a value \(e\.g\. 20\)_"
                )

                try:
                    if message_id:
                        await context.bot.edit_message_text(
                            chat_id=wizard_chat_id,
                            message_id=message_id,
                            text=text,
                            parse_mode="MarkdownV2",
                            reply_markup=InlineKeyboardMarkup(keyboard),
                        )
                        context.user_data["macdbb_wizard_message_id"] = message_id
                except Exception as e:
                    logger.error(f"Error showing leverage step: {e}")
                    # Fallback: invia nuovo messaggio
                    new_msg = await context.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    context.user_data["macdbb_wizard_message_id"] = new_msg.message_id
                    context.user_data["macdbb_wizard_chat_id"] = chat_id
                return  # <--- IMPORTANTE: esci qui

            else:
                # Spot: vai direttamente all'amount
                config["leverage"] = 1
                config["position_mode"] = "ONEWAY"
                set_controller_config(context, config)
                context.user_data["macdbb_wizard_step"] = "total_amount_quote"
                context.user_data["bots_state"] = "macdbb_wizard_input"
                keyboard = [
                    [
                        InlineKeyboardButton("$100", callback_data="bots:macdbb_amount:100"),
                        InlineKeyboardButton("$500", callback_data="bots:macdbb_amount:500"),
                        InlineKeyboardButton("$1000", callback_data="bots:macdbb_amount:1000"),
                    ],
                    [
                        InlineKeyboardButton("$2000", callback_data="bots:macdbb_amount:2000"),
                        InlineKeyboardButton("$5000", callback_data="bots:macdbb_amount:5000"),
                        InlineKeyboardButton("$10000", callback_data="bots:macdbb_amount:10000"),
                    ],
                    [
                        InlineKeyboardButton("⬅️ Back", callback_data="bots:macdbb_back_to_pair"),
                        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                    ],
                ]
                text = (
                    r"*📊 MACD BB V1 \- Step 3/4*" + "\n\n"  # <--- CAMBIA DA 3/5 A 3/4
                    f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`" + "\n\n"
                    r"💰 *Total Amount \(USDT\)*" + "\n"
                    r"_Select or type an amount:_"
                )

                try:
                    if message_id:
                        await context.bot.edit_message_text(
                            chat_id=wizard_chat_id, message_id=message_id,
                            text=text, parse_mode="MarkdownV2",
                            reply_markup=InlineKeyboardMarkup(keyboard),
                        )
                        context.user_data["macdbb_wizard_message_id"] = message_id
                except Exception:
                    pass
                return  # <--- AGGIUNGI QUESTO return

        # ========== GESTISCI INPUT PER LEVERAGE ==========
        elif step == "leverage":
            try:
                val = int(float(user_input.strip().lower().replace("x", "")))
                config["leverage"] = val
                set_controller_config(context, config)
                context.user_data["macdbb_wizard_step"] = "total_amount_quote"
                await _macdbb_show_amount_step(update, context)
            except ValueError:
                keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="bots:macdbb_back_to_leverage")]]
                await context.bot.edit_message_text(
                    chat_id=wizard_chat_id,
                    message_id=message_id,
                    text=r"*📊 MACD BB V1*" + "\n\n"
                    r"❌ *Invalid leverage*" + "\n\n"
                    r"Enter a positive number \(e\.g\. 20\):",
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )

        # ========== GESTISCI INPUT PER TOTAL_AMOUNT_QUOTE ==========
        elif step == "total_amount_quote":
            try:
                amount = float(user_input.strip().replace("$", "").replace(",", ""))
                config["total_amount_quote"] = amount
                set_controller_config(context, config)
                context.user_data["macdbb_wizard_step"] = "final"

                # Mostra loading
                pair = config.get("trading_pair", "")
                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id,
                        message_id=message_id,
                        text=r"*📊 MACD BB V1*" + "\n\n"
                             f"⏳ Loading chart for `{escape_markdown_v2(pair)}`\\.\\.\\.  ",
                        parse_mode="MarkdownV2",
                    )
                else:
                    tmp = await context.bot.send_message(
                        chat_id=wizard_chat_id,
                        text=r"*📊 MACD BB V1*" + "\n\n"
                             f"⏳ Loading chart for `{escape_markdown_v2(pair)}`\\.\\.\\.  ",
                        parse_mode="MarkdownV2",
                    )
                    context.user_data["macdbb_wizard_message_id"] = tmp.message_id
                await _macdbb_show_final_step(update, context)

            except ValueError:
                keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="bots:macdbb_back_to_amount")]]
                await context.bot.edit_message_text(
                    chat_id=wizard_chat_id,
                    message_id=message_id,
                    text=r"*📊 MACD BB V1*" + "\n\n"
                    r"❌ *Invalid amount*" + "\n\n"
                    r"Enter a positive number \(e\.g\. 500\):",
                    parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )

        # ========== GESTISCI INPUT PER FINAL (EDIT CAMPI) ==========
        elif step == "final":
            if "=" in user_input:
                supported_fields = [
                    "id", "connector_name", "trading_pair", "leverage", "position_mode",
                    "total_amount_quote", "max_executors_per_side", "cooldown_time",
                    "stop_loss", "take_profit", "take_profit_order_type", "time_limit",
                    "trailing_stop_activation", "trailing_stop_delta",
                    "candles_connector", "candles_trading_pair", "interval",
                    "bb_length", "bb_std", "bb_long_threshold", "bb_short_threshold",
                    "macd_fast", "macd_slow", "macd_signal",
                ]

                for line in user_input.strip().split("\n"):
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    field, value = line.split("=", 1)
                    field = field.strip().lower()
                    value = value.strip()

                    if field not in supported_fields:
                        continue

                    try:
                        if field in ("total_amount_quote", "stop_loss", "take_profit",
                                     "bb_std", "bb_long_threshold", "bb_short_threshold",
                                     "trailing_stop_activation", "trailing_stop_delta"):
                            config[field] = float(value)
                        elif field in ("leverage", "max_executors_per_side", "cooldown_time",
                                       "take_profit_order_type", "bb_length",
                                       "macd_fast", "macd_slow", "macd_signal"):
                            config[field] = int(float(value))
                        elif field in ("position_mode", "interval"):
                            config[field] = value.upper()
                        else:
                            config[field] = value
                    except Exception:
                        pass

                set_controller_config(context, config)
                await _macdbb_show_final_step(update, context)

    except Exception as e:
        logger.error(f"MACD BB wizard input error: {e}", exc_info=True)

# ============================================
# SUPERTREND V1 WIZARD
# ============================================
# Steps: connector → pair → (leverage) → amount → interval+chart → save
# Prefisso handler: st_

async def show_new_supertrend_v1_form(update, context) -> None:
    """Start the SuperTrend V1 wizard - Step 1: Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    for key in ["st_current_price", "st_candles", "st_candles_interval", "st_chart_interval"]:
        context.user_data.pop(key, None)

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()
        context.user_data["controller_configs_list"] = configs
    except Exception as e:
        logger.warning(f"Could not fetch existing configs: {e}")

    init_new_controller_config(context, "supertrend_v1")
    context.user_data["bots_state"] = "st_wizard"
    context.user_data["st_wizard_step"] = "connector_name"
    context.user_data["st_wizard_message_id"] = query.message.message_id
    context.user_data["st_wizard_chat_id"] = query.message.chat_id

    await _st_show_connector_step(update, context)


async def _st_show_connector_step(update, context) -> None:
    """ST Step 1: Select Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    try:
        client, server_name = await get_bots_client(chat_id, context.user_data)
        cex_connectors = await get_available_cex_connectors(
            context.user_data, client, server_name=server_name
        )

        if not cex_connectors:
            keyboard = [
                [InlineKeyboardButton("🔑 Configure API Keys", callback_data="config_api_keys")],
                [InlineKeyboardButton("« Back", callback_data="bots:main_menu")],
            ]
            await query.message.edit_text(
                r"*📈 SuperTrend V1 \- New Config*" + "\n\n"
                r"⚠️ No CEX connectors available\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        keyboard = []
        row = []
        for connector in cex_connectors:
            row.append(InlineKeyboardButton(
                f"🏦 {connector}", callback_data=f"bots:st_connector:{connector}"
            ))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")])

        await query.message.edit_text(
            r"*📈 SuperTrend V1*" + "\n\n"
            r"Trend\-following strategy using the SuperTrend indicator\. "
            r"Enters LONG when price is above the ST line and within threshold\. "
            r"Enters SHORT when price is below the ST line and within threshold\." + "\n\n"
            r"─────────────────────────" + "\n\n"
            r"*Step 1: Select Exchange*",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"ST connector step error: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        await query.message.edit_text(
            format_error_message(f"Error: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


async def handle_st_wizard_connector(update, context, connector: str) -> None:
    config = get_controller_config(context)
    config["connector_name"] = connector
    config["candles_connector"] = connector
    set_controller_config(context, config)
    context.user_data["st_wizard_step"] = "trading_pair"
    await _st_show_pair_step(update, context)

async def _st_show_pair_step(update, context) -> None:
    """ST Step 2: Select Trading Pair"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")

    context.user_data["bots_state"] = "st_wizard_input"
    context.user_data["st_wizard_step"] = "trading_pair"

    existing_configs = context.user_data.get("controller_configs_list", [])
    recent_pairs = []
    seen = set()
    for cfg in reversed(existing_configs):
        pair = cfg.get("trading_pair", "")
        if pair and pair not in seen:
            seen.add(pair)
            recent_pairs.append(pair)
            if len(recent_pairs) >= 6:
                break

    keyboard = []
    if recent_pairs:
        row = []
        for pair in recent_pairs:
            row.append(InlineKeyboardButton(pair, callback_data=f"bots:st_pair:{pair}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="bots:st_back_to_connector"),
        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
    ])

    is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()
    total_steps = 6 if is_perp else 4
    current_step = 2

    message_text = (
        rf"*📈 SuperTrend V1 \- Step {current_step}/{total_steps}*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}`" + "\n\n"
        r"🔗 *Trading Pair*" + "\n\n"
        r"Select a recent pair or type a new one:"
    )

    # ========== SALVA MESSAGE_ID ==========
    try:
        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        context.user_data["st_wizard_message_id"] = query.message.message_id
    except Exception:
        try:
            await query.message.delete()
        except Exception:
            pass
        new_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        context.user_data["st_wizard_message_id"] = new_msg.message_id
        context.user_data["st_wizard_chat_id"] = query.message.chat_id

async def handle_st_wizard_pair(update, context, pair: str) -> None:
    """Handle pair selection"""
    config = get_controller_config(context)
    config["trading_pair"] = pair
    config["candles_trading_pair"] = pair
    set_controller_config(context, config)
    connector = config.get("connector_name", "")
    is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()

    if is_perp:
        context.user_data["st_wizard_step"] = "leverage"
        await _st_show_leverage_step(update, context)
    else:
        config["leverage"] = 1
        config["position_mode"] = "ONEWAY"
        set_controller_config(context, config)
        context.user_data["st_wizard_step"] = "total_amount_quote"
        await _st_show_amount_step(update, context)

async def _st_show_leverage_step(update, context) -> None:
    """ST Step 3 (perp only): Select Leverage"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    context.user_data["bots_state"] = "st_wizard_input"
    context.user_data["st_wizard_step"] = "leverage"
    context.user_data["st_wizard_message_id"] = query.message.message_id
    context.user_data["st_wizard_chat_id"] = query.message.chat_id

    keyboard = [
        [
            InlineKeyboardButton("1x", callback_data="bots:st_leverage:1"),
            InlineKeyboardButton("5x", callback_data="bots:st_leverage:5"),
            InlineKeyboardButton("10x", callback_data="bots:st_leverage:10"),
        ],
        [
            InlineKeyboardButton("20x", callback_data="bots:st_leverage:20"),
            InlineKeyboardButton("50x", callback_data="bots:st_leverage:50"),
            InlineKeyboardButton("75x", callback_data="bots:st_leverage:75"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:st_back_to_pair"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    await query.message.edit_text(
        r"*📈 SuperTrend V1 \- Step 3/6*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`" + "\n\n"
        r"⚡ *Select Leverage*" + "\n"
        r"_Or type a value \(e\.g\. 20\)_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_st_wizard_leverage(update, context, leverage: int) -> None:
    """Handle leverage selection"""
    config = get_controller_config(context)
    config["leverage"] = leverage
    set_controller_config(context, config)
    # Vai a position_mode (come DMan V3)
    context.user_data["st_wizard_step"] = "position_mode"
    await _st_show_position_mode_step(update, context)

async def _st_show_position_mode_step(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """ST Wizard Step: Select Position Mode (Perpetual/Margin only)"""
    query = update.callback_query
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 1)
    context.user_data["bots_state"] = "st_wizard_input"
    context.user_data["st_wizard_step"] = "position_mode"
    escaped_connector = escape_markdown_v2(connector)
    escaped_pair = escape_markdown_v2(pair)

    keyboard = [
        [
            InlineKeyboardButton("🔒 ONEWAY", callback_data="bots:st_position_mode:ONEWAY"),
            InlineKeyboardButton("🔄 HEDGE", callback_data="bots:st_position_mode:HEDGE"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:st_back_to_leverage"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    total_steps = 6
    current_step = 4

    await query.message.edit_text(
        f"*📈 SuperTrend V1 \\- Step {current_step}/{total_steps}*\n\n"
        f"🏦 `{escaped_connector}` \\| 🔗 `{escaped_pair}` \\| ⚡ `{leverage}x`\n\n"
        r"🎯 *Position Mode*\n\n"
        r"• *ONEWAY*: Can only hold positions in one direction\n"
        r"• *HEDGE*: Can hold both long and short simultaneously",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_st_back_to_position_mode(update, context) -> None:
    """Go back to position mode step"""
    context.user_data["st_wizard_step"] = "position_mode"
    await _st_show_position_mode_step(update, context)

async def _st_show_amount_step(update, context) -> None:
    """ST Step: Enter Total Amount"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 1)
    pos_mode = config.get("position_mode", "HEDGE")

    context.user_data["bots_state"] = "st_wizard_input"
    context.user_data["st_wizard_step"] = "total_amount_quote"

    is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()
    total_steps = 6 if is_perp else 4
    current_step = 5 if is_perp else 3

    # Back callback
    if is_perp:
        back_callback = "bots:st_back_to_position_mode"
    else:
        back_callback = "bots:st_back_to_pair"

    keyboard = [
        [
            InlineKeyboardButton("$100", callback_data="bots:st_amount:100"),
            InlineKeyboardButton("$500", callback_data="bots:st_amount:500"),
            InlineKeyboardButton("$1000", callback_data="bots:st_amount:1000"),
        ],
        [
            InlineKeyboardButton("$2000", callback_data="bots:st_amount:2000"),
            InlineKeyboardButton("$5000", callback_data="bots:st_amount:5000"),
            InlineKeyboardButton("$10000", callback_data="bots:st_amount:10000"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data=back_callback),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    header_info = f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
    if is_perp:
        header_info += f" \\| ⚡ `{leverage}x` \\| 🎯 `{escape_markdown_v2(pos_mode)}`"

    message_text = (
        rf"*📈 SuperTrend V1 \- Step {current_step}/{total_steps}*" + "\n\n"
        + header_info + "\n\n"
        r"💰 *Total Amount \(USDT\)*" + "\n"
        r"_Select or type an amount:_"
    )

    # ========== SALVA MESSAGE_ID ==========
    target_chat_id = chat_id
    if query and query.message:
        target_chat_id = query.message.chat_id
        try:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            context.user_data["st_wizard_message_id"] = query.message.message_id
            return
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass

    new_msg = await context.bot.send_message(
        chat_id=target_chat_id,
        text=message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    context.user_data["st_wizard_message_id"] = new_msg.message_id
    context.user_data["st_wizard_chat_id"] = target_chat_id

async def handle_st_wizard_amount(update, context, amount: float) -> None:
    query = update.callback_query
    config = get_controller_config(context)
    config["total_amount_quote"] = amount
    set_controller_config(context, config)

    pair = config.get("trading_pair", "")
    await query.message.edit_text(
        r"*📈 SuperTrend V1 \- New Config*" + "\n\n"
        f"⏳ *Loading chart for* `{escape_markdown_v2(pair)}`\\.\\.\\.  " + "\n\n"
        r"_Fetching market data\.\.\._",
        parse_mode="MarkdownV2",
    )

    context.user_data["st_wizard_step"] = "final"
    await _st_show_final_step(update, context)

async def _st_show_final_step(update, context, interval: str = None) -> None:
    """ST Final Step: Chart + Config Summary + Analysis"""
    import asyncio

    query = update.callback_query
    msg = query.message if query else update.message
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    total_amount = config.get("total_amount_quote", 1000)
    leverage = config.get("leverage", 1)
    length = config.get("length", 20)
    multiplier = config.get("multiplier", 4.0)
    pct_threshold = config.get("percentage_threshold", 0.01)

    if interval is None:
        interval = context.user_data.get("st_chart_interval", config.get("interval", "3m"))
    context.user_data["st_chart_interval"] = interval
    config["interval"] = interval
    set_controller_config(context, config)

    current_price = context.user_data.get("st_current_price")
    candles = context.user_data.get("st_candles")

    try:
        cached_interval = context.user_data.get("st_candles_interval", interval)
        if not current_price or interval != cached_interval:
            # Usa MarkdownV2 invece di HTML
            try:
                await msg.edit_text(
                    r"*📈 SuperTrend V1 \- New Config*" + "\n\n"
                    f"⏳ Fetching market data for `{escape_markdown_v2(pair)}`\\.\\.\\.",
                    parse_mode="MarkdownV2",
                )
            except Exception:
                pass

            client, _ = await get_bots_client(chat_id, context.user_data)

            # ========== FIX: USA SEMPRE IL CONNETTORE SPOT PER LE CANDELE ==========
            candles_connector = connector.replace("_perpetual", "").replace("_margin", "").replace("_spot", "")
            if not candles_connector or len(candles_connector) < 3:
                candles_connector = "kucoin"

            logger.info(f"ST: Trading on {connector}, using candles from {candles_connector}")

            # Fetch current price
            try:
                current_price = await asyncio.wait_for(
                    fetch_current_price(client, connector, pair),
                    timeout=10.0
                )
                if current_price:
                    context.user_data["st_current_price"] = current_price
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"Could not fetch price for {pair}: {e}")
                current_price = None

            # Fetch candles
            if current_price:
                pair_variants = [pair, pair.replace("-", "/")]
                candles = None

                for try_pair in pair_variants:
                    try:
                        logger.info(f"ST: Trying candles from {candles_connector} for {try_pair}")
                        candles = await asyncio.wait_for(
                            fetch_candles(client, candles_connector, try_pair, interval=interval, max_records=420),
                            timeout=15.0
                        )
                        if candles:
                            candles_data = candles.get("data", []) if isinstance(candles, dict) else (candles or [])
                            if candles_data and len(candles_data) > 0:
                                logger.info(f"ST: Got {len(candles_data)} candles from {candles_connector} for {try_pair}")
                                break
                            else:
                                candles = None
                        else:
                            candles = None
                    except asyncio.TimeoutError:
                        logger.warning(f"ST: Timeout fetching candles from {candles_connector} for {try_pair}")
                    except Exception as e:
                        logger.warning(f"ST: Error fetching candles from {candles_connector} for {try_pair}: {e}")

                if candles:
                    context.user_data["st_candles"] = candles
                    context.user_data["st_candles_interval"] = interval
                else:
                    logger.warning(f"ST: Could not fetch candles for {pair}")

        if not current_price:
            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="bots:main_menu")]]
            await msg.edit_text(
                r"*❌ Error*" + "\n\n"
                f"Could not fetch price for `{escape_markdown_v2(pair)}`\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        candles_list = []
        if candles:
            candles_list = candles.get("data", []) if isinstance(candles, dict) else (candles or [])
            if candles_list:
                last_close = candles_list[-1].get("close") or candles_list[-1].get("c")
                if last_close:
                    current_price = float(last_close)
                    context.user_data["st_current_price"] = current_price

        # Generate config ID if not set
        if not config.get("id"):
            existing_configs = context.user_data.get("controller_configs_list", [])
            from .controllers.supertrend_v1.config import generate_id as st_generate_id
            config["id"] = st_generate_id(config, existing_configs)

        # Run analysis
        from .controllers.supertrend_v1.analysis import analyze_candles_for_supertrend, format_supertrend_analysis
        analysis = analyze_candles_for_supertrend(
            candles_list,
            length=length,
            multiplier=multiplier,
            percentage_threshold=pct_threshold,
        )

        # Auto-save suggested threshold
        config["percentage_threshold"] = analysis["suggested_percentage_threshold"]
        pct_threshold = config["percentage_threshold"]
        set_controller_config(context, config)

        position_mode = config.get("position_mode", "HEDGE")
        stop_loss = config.get("stop_loss", 0.05)
        take_profit = config.get("take_profit", 0.03)
        max_exec = config.get("max_executors_per_side", 1)
        cooldown = config.get("cooldown_time", 60)
        ts = config.get("trailing_stop", {}) or {}
        ts_act = ts.get("activation_price", 0.015) if isinstance(ts, dict) else 0.015
        ts_delta = ts.get("trailing_delta", 0.005) if isinstance(ts, dict) else 0.005

        context.user_data["bots_state"] = "st_wizard_input"
        context.user_data["st_wizard_step"] = "final"

        interval_options = ["1m", "3m", "5m", "15m", "1h"]
        interval_row = [
            InlineKeyboardButton(
                f"✓ {opt}" if opt == interval else opt,
                callback_data=f"bots:st_interval:{opt}"
            )
            for opt in interval_options
        ]

        context.user_data["st_analysis"] = analysis

        strategy_row = [
            InlineKeyboardButton("🎯 Scalp", callback_data="bots:st_set_strat:scalping"),
            InlineKeyboardButton("⚖️ Swing", callback_data="bots:st_set_strat:swing"),
            InlineKeyboardButton("🤖 Auto", callback_data="bots:st_set_strat:auto"),
        ]

        is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()
        final_step = 6 if is_perp else 4

        keyboard = [
            interval_row,
            strategy_row,
            [InlineKeyboardButton("💾 Save Config", callback_data="bots:st_save")],
            [
                InlineKeyboardButton("⬅️ Back", callback_data="bots:st_back_to_amount"),
                InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
            ],
        ]

        # ========== COSTRUISCI CONFIG_TEXT IN MARKDOWNV2 (COME MACD BB) ==========
        escaped_pair = escape_markdown_v2(pair)
        escaped_connector = escape_markdown_v2(connector)
        escaped_position_mode = escape_markdown_v2(position_mode)
        escaped_interval = escape_markdown_v2(interval)
        escaped_title = escape_markdown_v2(f"📈 SuperTrend V1 - Step {final_step}/{final_step} (Final)")

        # IMPORTANTE: escapa anche il pipe e il punto
        price_line = f"Price: `{current_price:,.6g}` | ST length: `{length}` | Multiplier: `{multiplier}` | Interval: `{escaped_interval}`"
        escaped_price_line = escape_markdown_v2(price_line)

        config_text = (
            f"*{escaped_title}*\n\n"
            f"*{escaped_pair}*\n"
            f"{escaped_price_line}\n\n"
            f"`connector_name={escaped_connector}`\n"
            f"`trading_pair={escaped_pair}`\n"
            f"`total_amount_quote={total_amount:.0f}`\n"
            f"`leverage={leverage}`\n"
            f"`position_mode={escaped_position_mode}`\n"
            f"`max_executors_per_side={max_exec}`\n"
            f"`cooldown_time={cooldown}`\n"
            f"`stop_loss={stop_loss}`\n"
            f"`take_profit={take_profit}`\n"
            f"`trailing_stop_activation={ts_act}`\n"
            f"`trailing_stop_delta={ts_delta}`\n"
            f"`interval={escaped_interval}`\n"
            f"`length={length}`\n"
            f"`multiplier={multiplier}`\n"
            f"`percentage_threshold={pct_threshold}`\n\n"
            r"_Edit: `field=value`_"
        )

        # Analysis con tripli backtick (non serve escape qui)
        analysis_text = format_supertrend_analysis(analysis)
        config_text += "\n\n```\n" + analysis_text + "\n```"

        # ========== INVIA CON MARKDOWNV2 (NON HTML) ==========
        if not candles_list:
            try:
                await msg.edit_text(
                    text=config_text,
                    parse_mode="MarkdownV2",  # <-- CAMBIATO
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["st_wizard_message_id"] = msg.message_id
            except Exception:
                new_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=config_text,
                    parse_mode="MarkdownV2",  # <-- CAMBIATO
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["st_wizard_message_id"] = new_msg.message_id
            return

        # ========== TENTA DI GENERARE IL GRAFICO ==========
        try:
            from .controllers.supertrend_v1.chart import generate_chart as st_chart
            chart_bytes = st_chart(config, candles_list, current_price)
            chart_bytes.seek(0)

            stored_msg_id = context.user_data.get("st_wizard_message_id")
            stored_chat_id = context.user_data.get("st_wizard_chat_id", chat_id)
            if stored_msg_id:
                try:
                    await context.bot.delete_message(chat_id=stored_chat_id, message_id=stored_msg_id)
                except Exception:
                    pass
            try:
                await msg.delete()
            except Exception:
                pass

            new_msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=chart_bytes,
                caption=config_text,
                parse_mode="MarkdownV2",  # <-- CAMBIATO
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            context.user_data["st_wizard_message_id"] = new_msg.message_id
            context.user_data["st_wizard_chat_id"] = chat_id
        except Exception as chart_err:
            logger.warning(f"Chart generation failed: {chart_err}, sending text-only")
            try:
                await msg.edit_text(
                    text=config_text,
                    parse_mode="MarkdownV2",  # <-- CAMBIATO
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["st_wizard_message_id"] = msg.message_id
            except Exception:
                new_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=config_text,
                    parse_mode="MarkdownV2",  # <-- CAMBIATO
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["st_wizard_message_id"] = new_msg.message_id

    except Exception as e:
        logger.error(f"ST final step error: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        try:
            await msg.edit_text(
                format_error_message(f"Error: {str(e)}"),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception:
            pass

async def handle_st_interval_change(update, context, interval: str) -> None:
    context.user_data["st_candles"] = None
    context.user_data["st_candles_interval"] = None
    await _st_show_final_step(update, context, interval=interval)

async def handle_st_set_strategy(update, context, strat_key: str) -> None:
    """Handle SuperTrend strategy selection from final step buttons."""
    query = update.callback_query
    config = get_controller_config(context)

    analysis = context.user_data.get("st_analysis", {})
    natr = analysis.get("natr", 0.01)

    from .controllers.supertrend_v1.analysis import get_st_strategy_suggestions
    strats = get_st_strategy_suggestions(natr, analysis)

    if strat_key in strats:
        selected = strats[strat_key]

        # Aggiorna i parametri
        config["length"] = selected.get("length", 20)
        config["multiplier"] = selected.get("multiplier", 4.0)
        config["percentage_threshold"] = selected.get("percentage_threshold", 0.01)
        config["take_profit"] = selected.get("take_profit", 0.03)
        config["stop_loss"] = selected.get("stop_loss", 0.05)

        # Trailing stop
        if "trailing_stop_activation" in selected:
            if not isinstance(config.get("trailing_stop"), dict):
                config["trailing_stop"] = {"activation_price": 0.015, "trailing_delta": 0.005}
            config["trailing_stop"]["activation_price"] = selected["trailing_stop_activation"]
            config["trailing_stop"]["trailing_delta"] = selected["trailing_stop_delta"]

        set_controller_config(context, config)

        vol_regime = selected.get("volatility_regime", "moderate")
        await query.answer(f"✅ {selected['label']} applicata (vol: {vol_regime})")

        return await _st_show_final_step(update, context)

async def handle_st_save(update, context) -> None:
    """Save SuperTrend V1 configuration"""
    query = update.callback_query
    config = get_controller_config(context)
    config.pop("candles_config", None)
    config_id = config.get("id", "")
    chat_id = query.message.chat_id

    try:
        await query.message.delete()
    except Exception:
        pass

    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="Saving `" + escape_markdown_v2(config_id) + "`\\.\\.\\.",
        parse_mode="MarkdownV2",
    )

    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        await client.controllers.create_or_update_controller_config(config_id, config)

        for key in ["st_wizard_step", "st_wizard_message_id", "st_wizard_chat_id",
                    "st_current_price", "st_candles", "st_candles_interval", "st_chart_interval"]:
            context.user_data.pop(key, None)
        context.user_data.pop("bots_state", None)

        keyboard = [
            [InlineKeyboardButton("Create Another", callback_data="bots:new_supertrend_v1")],
            [InlineKeyboardButton("Back to Configs", callback_data="bots:controller_configs")],
        ]
        await status_msg.edit_text(
            "*Config Saved\\!*\n\n"
            "Controller `" + escape_markdown_v2(config_id) + "` saved successfully\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"ST save error: {e}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton("Try Again", callback_data="bots:st_save")],
            [InlineKeyboardButton("Back", callback_data="bots:controller_configs")],
        ]
        await status_msg.edit_text(
            format_error_message(f"Failed to save: {str(e)}"),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

async def handle_st_back_to_connector(update, context) -> None:
    context.user_data["st_wizard_step"] = "connector_name"
    await _st_show_connector_step(update, context)

async def handle_st_back_to_pair(update, context) -> None:
    context.user_data["st_wizard_step"] = "trading_pair"
    await _st_show_pair_step(update, context)

async def handle_st_back_to_leverage(update, context) -> None:
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    # CORREZIONE: supporta _margin
    is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()
    if is_perp:
        context.user_data["st_wizard_step"] = "leverage"
        await _st_show_leverage_step(update, context)
    else:
        await handle_st_back_to_pair(update, context)


async def handle_st_back_to_amount(update, context) -> None:
    context.user_data["st_wizard_step"] = "total_amount_quote"
    context.user_data.pop("st_current_price", None)
    context.user_data.pop("st_candles", None)
    await _st_show_amount_step(update, context)

async def handle_st_position_mode(update, context, mode: str) -> None:
    """Handle position mode selection"""
    config = get_controller_config(context)
    config["position_mode"] = mode
    set_controller_config(context, config)
    context.user_data["st_wizard_step"] = "total_amount_quote"
    await _st_show_amount_step(update, context)

async def handle_st_pair_select(
    update: Update, context: ContextTypes.DEFAULT_TYPE, trading_pair: str
) -> None:
    """Handle selection of a suggested trading pair in SuperTrend wizard"""
    config = get_controller_config(context)
    chat_id = update.effective_chat.id

    # Clear old market data
    for key in ["st_current_price", "st_candles", "st_candles_interval"]:
        context.user_data.pop(key, None)

    config["trading_pair"] = trading_pair
    config["candles_trading_pair"] = trading_pair
    set_controller_config(context, config)

    connector = config.get("connector_name", "")
    is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()

    if is_perp:
        context.user_data["st_wizard_step"] = "leverage"
        await _st_show_leverage_step(update, context)
    else:
        config["leverage"] = 1
        config["position_mode"] = "ONEWAY"
        set_controller_config(context, config)
        context.user_data["st_wizard_step"] = "total_amount_quote"
        await _st_show_amount_step(update, context)

async def process_st_wizard_input(update, context, user_input: str) -> None:
    """Process text input during Supertrend V1 wizard"""
    step = context.user_data.get("st_wizard_step")
    chat_id = update.effective_chat.id
    config = get_controller_config(context)
    message_id = context.user_data.get("st_wizard_message_id")
    wizard_chat_id = context.user_data.get("st_wizard_chat_id", chat_id)
    try:
        try:
            await update.message.delete()
        except Exception:
            pass

        if step == "trading_pair":
            pair = user_input.upper().strip().replace("/", "-").replace("_", "-")
            connector = config.get("connector_name", "")

            # ========== VALIDAZIONE BASE: DEVE CONTENERE IL TRATTINO ==========
            if "-" not in pair:
                message_id = context.user_data.get("st_wizard_message_id")
                wizard_chat_id = context.user_data.get("st_wizard_chat_id", chat_id)

                keyboard = [
                    [InlineKeyboardButton("⬅️ Back", callback_data="bots:st_back_to_connector")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")],
                ]

                context_text = f"🏦 `{escape_markdown_v2(connector)}`\n\n"

                err_text = (
                    r"*📈 Supertrend V1 \- Step 2*" + "\n\n"
                    + context_text
                    + r"⚠️ *Invalid format\. Use BASE\-QUOTE \(e\.g\. BTC\-USDT\)*" + "\n\n"
                    r"Type the pair again:"
                )

                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id, message_id=message_id,
                        text=err_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                else:
                    msg = await context.bot.send_message(
                        chat_id=chat_id, text=err_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    context.user_data["st_wizard_message_id"] = msg.message_id
                return

            # ========== 2. VALIDAZIONE SULL'EXCHANGE (CON SUGGERIMENTI) ==========
            client, _ = await get_bots_client(chat_id, context.user_data)
            is_valid, error_msg, suggestions, correct_pair = (
                await validate_trading_pair(context.user_data, client, connector, pair)
            )

            if not is_valid:
                message_id = context.user_data.get("st_wizard_message_id")
                wizard_chat_id = context.user_data.get("st_wizard_chat_id", chat_id)

                keyboard = []
                for sugg in suggestions[:4]:
                    keyboard.append([InlineKeyboardButton(sugg, callback_data=f"bots:st_pair_select:{sugg}")])
                keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="bots:st_back_to_connector")])
                keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")])

                context_text = f"🏦 `{escape_markdown_v2(connector)}`\n\n"

                err_text = (
                    r"*📈 Supertrend V1 \- Step 2*" + "\n\n"
                    + context_text
                    + f"❌ `{escape_markdown_v2(pair)}` not found on `{escape_markdown_v2(connector)}`\\.\n\n"
                    r"*Did you mean?*"
                )

                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id, message_id=message_id,
                        text=err_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                else:
                    msg = await context.bot.send_message(
                        chat_id=chat_id, text=err_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    context.user_data["st_wizard_message_id"] = msg.message_id
                return

            if correct_pair:
                pair = correct_pair

            config["trading_pair"] = pair
            config["candles_trading_pair"] = pair
            for key in ["st_current_price", "st_candles", "st_candles_interval"]:
                context.user_data.pop(key, None)
            set_controller_config(context, config)

            connector = config.get("connector_name", "")
            is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()
            message_id = context.user_data.get("st_wizard_message_id")
            wizard_chat_id = context.user_data.get("st_wizard_chat_id", chat_id)

            if is_perp:
                context.user_data["st_wizard_step"] = "leverage"
                keyboard = [
                    [
                        InlineKeyboardButton("1x", callback_data="bots:st_leverage:1"),
                        InlineKeyboardButton("5x", callback_data="bots:st_leverage:5"),
                        InlineKeyboardButton("10x", callback_data="bots:st_leverage:10"),
                    ],
                    [
                        InlineKeyboardButton("20x", callback_data="bots:st_leverage:20"),
                        InlineKeyboardButton("50x", callback_data="bots:st_leverage:50"),
                        InlineKeyboardButton("75x", callback_data="bots:st_leverage:75"),
                    ],
                    [
                        InlineKeyboardButton("⬅️ Back", callback_data="bots:st_back_to_pair"),
                        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                    ],
                ]
                text = (
                    r"*📈 Supertrend V1 \- Step 3/6*" + "\n\n"
                    f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`" + "\n\n"
                    r"⚡ *Select Leverage*" + "\n"
                    r"_Or type a value \(e\.g\. 20\)_"
                )
            else:
                config["leverage"] = 1
                set_controller_config(context, config)
                context.user_data["st_wizard_step"] = "total_amount_quote"
                context.user_data["bots_state"] = "st_wizard_input"
                keyboard = [
                    [
                        InlineKeyboardButton("$100", callback_data="bots:st_amount:100"),
                        InlineKeyboardButton("$500", callback_data="bots:st_amount:500"),
                        InlineKeyboardButton("$1000", callback_data="bots:st_amount:1000"),
                    ],
                    [
                        InlineKeyboardButton("$2000", callback_data="bots:st_amount:2000"),
                        InlineKeyboardButton("$5000", callback_data="bots:st_amount:5000"),
                        InlineKeyboardButton("$10000", callback_data="bots:st_amount:10000"),
                    ],
                    [
                        InlineKeyboardButton("⬅️ Back", callback_data="bots:st_back_to_pair"),
                        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                    ],
                ]
                text = (
                    r"*📈 Supertrend V1 \- Step 3/4*" + "\n\n"
                    f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`" + "\n\n"
                    r"💰 *Total Amount \(USDT\)*" + "\n"
                    r"_Select or type an amount:_"
                )

            try:
                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id, message_id=message_id,
                        text=text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    context.user_data["st_wizard_message_id"] = message_id
            except Exception:
                pass
            return

        elif step == "leverage":
            val = int(float(user_input.strip().lower().replace("x", "")))
            config["leverage"] = val
            set_controller_config(context, config)
            context.user_data["st_wizard_step"] = "total_amount_quote"
            message_id = context.user_data.get("st_wizard_message_id")
            wizard_chat_id = context.user_data.get("st_wizard_chat_id", chat_id)
            connector = config.get("connector_name", "")
            pair = config.get("trading_pair", "")
            keyboard = [
                [
                    InlineKeyboardButton("$100", callback_data="bots:st_amount:100"),
                    InlineKeyboardButton("$500", callback_data="bots:st_amount:500"),
                    InlineKeyboardButton("$1000", callback_data="bots:st_amount:1000"),
                ],
                [
                    InlineKeyboardButton("$2000", callback_data="bots:st_amount:2000"),
                    InlineKeyboardButton("$5000", callback_data="bots:st_amount:5000"),
                    InlineKeyboardButton("$10000", callback_data="bots:st_amount:10000"),
                ],
                [
                    InlineKeyboardButton("⬅️ Back", callback_data="bots:st_back_to_leverage"),
                    InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                ],
            ]
            text = (
                r"*📈 Supertrend V1 \- Step 4/6*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}` \\| ⚡ `{val}x`" + "\n\n"
                r"💰 *Total Amount \(USDT\)*" + "\n"
                r"_Select or type an amount:_"
            )
            context.user_data["bots_state"] = "st_wizard_input"
            if message_id:
                await context.bot.edit_message_text(
                    chat_id=wizard_chat_id, message_id=message_id,
                    text=text, parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )

        elif step == "total_amount_quote":
            amount = float(user_input.strip().replace("$", "").replace(",", ""))
            config["total_amount_quote"] = amount
            set_controller_config(context, config)
            context.user_data["st_wizard_step"] = "final"
            wizard_chat_id = context.user_data.get("st_wizard_chat_id", chat_id)
            pair = config.get("trading_pair", "")
            tmp = await context.bot.send_message(
                chat_id=wizard_chat_id,
                text=r"*📈 Supertrend V1*" + "\n\n"
                     f"⏳ Loading chart for `{escape_markdown_v2(pair)}`\\.\\.\\.  ",
                parse_mode="MarkdownV2",
            )
            context.user_data["st_wizard_message_id"] = tmp.message_id
            await _st_show_final_step(update, context)

        elif step == "final":
            # Handle field=value edits
            if "=" in user_input:
                for line in user_input.strip().split("\n"):
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    field, value = line.split("=", 1)
                    field = field.strip().lower()
                    value = value.strip()
                    try:
                        if field in ("total_amount_quote", "stop_loss", "take_profit",
                                     "trailing_stop_activation", "trailing_stop_delta",
                                     "supertrend_multiplier", "supertrend_period"):
                            val = float(value)
                            if field == "trailing_stop_activation":
                                if not isinstance(config.get("trailing_stop"), dict):
                                    config["trailing_stop"] = {"activation_price": 0.015, "trailing_delta": 0.005}
                                config["trailing_stop"]["activation_price"] = val
                            elif field == "trailing_stop_delta":
                                if not isinstance(config.get("trailing_stop"), dict):
                                    config["trailing_stop"] = {"activation_price": 0.015, "trailing_delta": 0.005}
                                config["trailing_stop"]["trailing_delta"] = val
                            else:
                                config[field] = val
                        elif field in ("leverage", "max_executors_per_side", "cooldown_time",
                                       "take_profit_order_type"):
                            config[field] = int(float(value))
                        elif field == "interval":
                            config["interval"] = value
                            context.user_data.pop("st_candles", None)
                            context.user_data["st_chart_interval"] = value
                        else:
                            config[field] = value
                    except Exception:
                        pass
                set_controller_config(context, config)

    except Exception as e:
        logger.error(f"ST wizard input error: {e}", exc_info=True)
# =============================================================================
# ANTIFOLLA
# =============================================================================

async def show_new_anti_folla_v1_form(update, context) -> None:
    """Start the Anti-Folla V1 wizard - Step 1: Connector"""
    query = update.callback_query
    chat_id = update.effective_chat.id

    for key in list(context.user_data.keys()):
        if key.endswith("_wizard_message_id") or key.endswith("_wizard_chat_id"):
            context.user_data.pop(key, None)
        if key.endswith("_wizard_step"):
            context.user_data.pop(key, None)
    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        configs = await client.controllers.list_controller_configs()
        context.user_data["controller_configs_list"] = configs
    except Exception as e:
        logger.warning(f"Could not fetch existing configs: {e}")

    init_new_controller_config(context, "anti_folla_v1")
    context.user_data["bots_state"] = "af_wizard"
    context.user_data["af_wizard_step"] = "connector_name"
    context.user_data["af_wizard_message_id"] = query.message.message_id
    context.user_data["af_wizard_chat_id"] = query.message.chat_id

    await _af_show_connector_step(update, context)


async def _af_show_connector_step(update, context) -> None:
    query = update.callback_query
    chat_id = update.effective_chat.id
    try:
        client, server_name = await get_bots_client(chat_id, context.user_data)
        cex_connectors = await get_available_cex_connectors(
            context.user_data, client, server_name=server_name
        )
        if not cex_connectors:
            keyboard = [
                [InlineKeyboardButton("🔑 Configure API Keys", callback_data="config_api_keys")],
                [InlineKeyboardButton("« Back", callback_data="bots:main_menu")],
            ]
            await query.message.edit_text(
                r"*🦅 Anti\-Folla V1 \- New Config*" + "\n\n" r"⚠️ No CEX connectors available\.",
                parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return
        keyboard = []
        row = []
        for connector in cex_connectors:
            row.append(InlineKeyboardButton(f"🏦 {connector}", callback_data=f"bots:af_connector:{connector}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")])
        await query.message.edit_text(
            r"*🦅 Anti\-Folla V1*" + "\n\n"
            r"Crowd\-contrarian: VWAP, Donchian, OBV divergence, OBI, Volume Spike, Whale, Funding Rate\." + "\n\n"
            r"─────────────────────────" + "\n\n" r"*Step 1: Select Exchange*",
            parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        logger.error(f"AF connector step error: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        await query.message.edit_text(format_error_message(f"Error: {str(e)}"),
                                      parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_af_wizard_connector(update, context, connector: str) -> None:
    config = get_controller_config(context)
    config["connector_name"] = connector
    config["candles_connector"] = connector
    # Auto-set is_perpetual
    config["is_perpetual"] = connector.endswith("_perpetual")
    set_controller_config(context, config)
    context.user_data["af_wizard_step"] = "trading_pair"
    await _af_show_pair_step(update, context)

async def _af_show_pair_step(update, context) -> None:
    """AF Step 2: Select Trading Pair"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")

    context.user_data["bots_state"] = "af_wizard_input"
    context.user_data["af_wizard_step"] = "trading_pair"

    # Recent pairs from existing configs
    existing_configs = context.user_data.get("controller_configs_list", [])
    recent_pairs = []
    seen = set()
    for cfg in reversed(existing_configs):
        pair = cfg.get("trading_pair", "")
        if pair and pair not in seen:
            seen.add(pair)
            recent_pairs.append(pair)
            if len(recent_pairs) >= 6:
                break

    keyboard = []
    if recent_pairs:
        row = []
        for pair in recent_pairs:
            row.append(InlineKeyboardButton(pair, callback_data=f"bots:af_pair:{pair}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data="bots:af_back_to_connector"),
        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
    ])

    is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()
    total_steps = 6 if is_perp else 4
    current_step = 2

    # NOTA: Il nome "Anti-Folla" ha un trattino che deve essere escapato con \-
    # Oppure usa r"*🦅 Anti\-Folla V1 ..." come in DMan
    message_text = (
        rf"*🦅 Anti\-Folla V1 \- Step {current_step}/{total_steps}*" + "\n\n"
        f"🏦 `{escape_markdown_v2(connector)}`" + "\n\n"
        r"🔗 *Trading Pair*" + "\n\n"
        r"Select a recent pair or type a new one:"
    )

    # ========== SALVA MESSAGE_ID ==========
    try:
        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        context.user_data["af_wizard_message_id"] = query.message.message_id
    except Exception:
        try:
            await query.message.delete()
        except Exception:
            pass
        new_msg = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        context.user_data["af_wizard_message_id"] = new_msg.message_id
        context.user_data["af_wizard_chat_id"] = query.message.chat_id

async def handle_af_wizard_pair(update, context, pair: str) -> None:
    config = get_controller_config(context)
    config["trading_pair"] = pair
    config["candles_trading_pair"] = pair
    for key in list(context.user_data.keys()):
        if key.endswith("_wizard_message_id") or key.endswith("_wizard_chat_id"):
            context.user_data.pop(key, None)
        if key.endswith("_wizard_step"):
            context.user_data.pop(key, None)
    set_controller_config(context, config)

    connector = config.get("connector_name", "")
    # CORREZIONE: supporta _margin
    is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()

    if is_perp:
        context.user_data["af_wizard_step"] = "leverage"
        await _af_show_leverage_step(update, context)
    else:
        config["leverage"] = 1
        config["position_mode"] = "ONEWAY"
        set_controller_config(context, config)
        context.user_data["af_wizard_step"] = "total_amount_quote"
        await _af_show_amount_step(update, context)

async def _af_show_leverage_step(update, context) -> None:
    """AF Step 3 (perp only): Select Leverage"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")

    context.user_data["bots_state"] = "af_wizard_input"
    context.user_data["af_wizard_step"] = "leverage"

    keyboard = [
        [
            InlineKeyboardButton("1x", callback_data="bots:af_leverage:1"),
            InlineKeyboardButton("5x", callback_data="bots:af_leverage:5"),
            InlineKeyboardButton("10x", callback_data="bots:af_leverage:10"),
        ],
        [
            InlineKeyboardButton("20x", callback_data="bots:af_leverage:20"),
            InlineKeyboardButton("50x", callback_data="bots:af_leverage:50"),
            InlineKeyboardButton("75x", callback_data="bots:af_leverage:75"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:af_back_to_pair"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    # ========== SALVA message_id ==========
    context.user_data["af_wizard_message_id"] = query.message.message_id
    context.user_data["af_wizard_chat_id"] = query.message.chat_id
    # ======================================

    await query.message.edit_text(
        r"*🦅 Anti\-Folla V1 \- Step 3/6*" + "\n\n"  # <--- Anti\-Folla con backslash
        f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`" + "\n\n"
        r"⚡ *Select Leverage*" + "\n"
        r"_Or type a value \(e\.g\. 20\)_",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_af_wizard_leverage(update, context, leverage: int) -> None:
    """Handle leverage selection"""
    config = get_controller_config(context)
    config["leverage"] = leverage
    set_controller_config(context, config)
    # Vai a position_mode
    context.user_data["af_wizard_step"] = "position_mode"
    await _af_show_position_mode_step(update, context)

async def _af_show_position_mode_step(update, context) -> None:
    """AF Step 4 (derivati only): HEDGE vs ONEWAY"""
    query = update.callback_query
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 1)

    context.user_data["bots_state"] = "af_wizard_input"
    context.user_data["af_wizard_step"] = "position_mode"

    keyboard = [
        [
            InlineKeyboardButton("🔀 HEDGE  ✅ recommended", callback_data="bots:af_position_mode:HEDGE"),
            InlineKeyboardButton("➡️ ONEWAY", callback_data="bots:af_position_mode:ONEWAY"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data="bots:af_back_to_leverage"),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    # CORREZIONE: escape di tutti i caratteri speciali MarkdownV2
    escaped_connector = escape_markdown_v2(connector)
    escaped_pair = escape_markdown_v2(pair)

    # NOTA: "Anti-Folla" ha un trattino che deve essere escapato come "Anti\-Folla"
    await query.message.edit_text(
        f"*🦅 Anti\\-Folla V1 \\- Step 4/6*\n\n"
        f"🏦 `{escaped_connector}` \\| 🔗 `{escaped_pair}` \\| ⚡ `{leverage}x`\n\n"
        r"📐 *Position Mode*" + "\n\n"
        r"• *HEDGE*: Can hold both long and short positions simultaneously" + "\n"
        r"• *ONEWAY*: Can only hold positions in one direction \(long OR short\)",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def handle_af_position_mode(update, context, mode: str) -> None:
    """Handle position mode selection for Anti-Folla V1"""
    config = get_controller_config(context)
    config["position_mode"] = mode
    set_controller_config(context, config)
    context.user_data["af_wizard_step"] = "total_amount_quote"
    await _af_show_amount_step(update, context)

async def _af_show_amount_step(update, context) -> None:
    """AF Step: Enter Total Amount"""
    query = update.callback_query
    chat_id = update.effective_chat.id
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    leverage = config.get("leverage", 1)
    pos_mode = config.get("position_mode", "HEDGE")

    context.user_data["bots_state"] = "af_wizard_input"
    context.user_data["af_wizard_step"] = "total_amount_quote"
    connector = config.get("connector_name", "").lower()
    is_perp = "_perpetual" in connector or "_margin" in connector
    total_steps = 6 if is_perp else 4
    current_step = 5 if is_perp else 3

    back_callback = "bots:af_back_to_position_mode" if is_perp else "bots:af_back_to_pair"

    # Fetch balance
    balance_text = ""
    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        balances = await get_cex_balances(context.user_data, client, connector)
        quote = pair.split("-")[1] if "-" in pair else "USDT"
        available = balances.get(quote, {}).get("available", 0)
        if available:
            balance_text = f"\n\n💰 Available `{escape_markdown_v2(quote)}`: `{available:,.2f}`"
    except Exception:
        pass

    keyboard = [
        [
            InlineKeyboardButton("$100", callback_data="bots:af_amount:100"),
            InlineKeyboardButton("$500", callback_data="bots:af_amount:500"),
            InlineKeyboardButton("$1000", callback_data="bots:af_amount:1000"),
        ],
        [
            InlineKeyboardButton("$2000", callback_data="bots:af_amount:2000"),
            InlineKeyboardButton("$5000", callback_data="bots:af_amount:5000"),
            InlineKeyboardButton("$10000", callback_data="bots:af_amount:10000"),
        ],
        [
            InlineKeyboardButton("⬅️ Back", callback_data=back_callback),
            InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
        ],
    ]

    header_info = f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`"
    if is_perp:
        header_info += f" \\| ⚡ `{leverage}x` \\| 🎯 `{escape_markdown_v2(pos_mode)}`"

    message_text = (
        rf"*🦅 Anti\-Folla V1 \- Step {current_step}/{total_steps}*" + "\n\n"
        + header_info + balance_text + "\n\n"
        r"💰 *Total Amount \(USDT\)*" + "\n"
        r"_Select or type an amount:_"
    )

    # ========== SALVA MESSAGE_ID ==========
    target_chat_id = chat_id
    if query and query.message:
        target_chat_id = query.message.chat_id
        try:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            context.user_data["af_wizard_message_id"] = query.message.message_id
            return
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass

    new_msg = await context.bot.send_message(
        chat_id=target_chat_id,
        text=message_text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    context.user_data["af_wizard_message_id"] = new_msg.message_id
    context.user_data["af_wizard_chat_id"] = target_chat_id

async def handle_af_wizard_amount(update, context, amount: float) -> None:
    query = update.callback_query
    config = get_controller_config(context)
    config["total_amount_quote"] = amount
    set_controller_config(context, config)
    pair = config.get("trading_pair", "")
    await query.message.edit_text(
        r"*🦅 Anti\-Folla V1 \- New Config*" + "\n\n"
        f"⏳ *Loading chart for* `{escape_markdown_v2(pair)}`\\.\\.\\.  " + "\n\n"
        r"_Fetching market data\.\.\._",
        parse_mode="MarkdownV2",
    )
    context.user_data["af_wizard_step"] = "final"
    await _af_show_final_step(update, context)

async def _af_show_final_step(update, context, interval: str = None) -> None:
    """Anti-Folla Final Step: Chart + Config Summary + Analysis"""
    import asyncio

    query = update.callback_query
    msg = query.message if query else update.message
    chat_id = update.effective_chat.id
    config = get_controller_config(context)

    connector = config.get("connector_name", "")
    pair = config.get("trading_pair", "")
    total_amount = config.get("total_amount_quote", 1000)
    leverage = config.get("leverage", 1)
    vwap_period = config.get("vwap_period", 20)
    donchian_period = config.get("donchian_period", 20)
    score_buy = config.get("score_buy_threshold", 50.0)
    score_sell = config.get("score_sell_threshold", -50.0)

    if interval is None:
        interval = context.user_data.get("af_chart_interval", config.get("interval", "3m"))
    context.user_data["af_chart_interval"] = interval
    config["interval"] = interval
    set_controller_config(context, config)

    current_price = context.user_data.get("af_current_price")
    candles = context.user_data.get("af_candles")

    try:
        cached_interval = context.user_data.get("af_candles_interval", interval)
        if not current_price or interval != cached_interval:
            # Usa MarkdownV2 invece di HTML
            try:
                await msg.edit_text(
                    r"*🦅 Anti\-Folla V1 \- New Config*" + "\n\n"
                    f"⏳ Fetching market data for `{escape_markdown_v2(pair)}`\\.\\.\\.",
                    parse_mode="MarkdownV2",
                )
            except Exception:
                pass

            client, _ = await get_bots_client(chat_id, context.user_data)

            # ========== PULISCI IL CONNETTORE PER LE CANDELE ==========
            candles_connector = connector.replace("_perpetual", "").replace("_margin", "").replace("_spot", "")
            if not candles_connector or len(candles_connector) < 3:
                candles_connector = "kucoin"
            logger.info(f"AF: Trading on {connector}, using candles from {candles_connector}")

            # Fetch current price
            try:
                current_price = await asyncio.wait_for(
                    fetch_current_price(client, connector, pair),
                    timeout=10.0
                )
                if current_price:
                    context.user_data["af_current_price"] = current_price
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"Could not fetch price for {pair}: {e}")
                current_price = None

            # Fetch candles
            if current_price:
                pair_variants = [pair, pair.replace("-", "/")]
                candles = None

                for try_pair in pair_variants:
                    try:
                        logger.info(f"AF: Trying candles from {candles_connector} for {try_pair}")
                        candles = await asyncio.wait_for(
                            fetch_candles(client, candles_connector, try_pair, interval=interval, max_records=420),
                            timeout=15.0
                        )
                        if candles:
                            candles_data = candles.get("data", []) if isinstance(candles, dict) else (candles or [])
                            if candles_data and len(candles_data) > 0:
                                logger.info(f"AF: Got {len(candles_data)} candles from {candles_connector} for {try_pair}")
                                context.user_data["af_candles"] = {"data": candles_data}
                                context.user_data["af_candles_interval"] = interval
                                break
                            else:
                                candles = None
                        else:
                            candles = None
                    except asyncio.TimeoutError:
                        logger.warning(f"AF: Timeout fetching candles from {candles_connector} for {try_pair}")
                    except Exception as e:
                        logger.warning(f"AF: Error fetching candles from {candles_connector} for {try_pair}: {e}")

        if not current_price:
            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="bots:main_menu")]]
            await msg.edit_text(
                r"*❌ Error*" + "\n\n"
                f"Could not fetch price for `{escape_markdown_v2(pair)}`\\.",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return

        candles_list = []
        if candles:
            candles_list = candles.get("data", []) if isinstance(candles, dict) else (candles or [])
            if candles_list:
                last_close = candles_list[-1].get("close") or candles_list[-1].get("c")
                if last_close:
                    current_price = float(last_close)
                    context.user_data["af_current_price"] = current_price

        if not config.get("id"):
            existing_configs = context.user_data.get("controller_configs_list", [])
            from .controllers.anti_folla_v1.config import generate_id as af_generate_id
            config["id"] = af_generate_id(config, existing_configs)

        from .controllers.anti_folla_v1.analysis import analyze_candles_for_anti_folla, format_anti_folla_analysis
        analysis = analyze_candles_for_anti_folla(
            candles_list, vwap_period=vwap_period, donchian_period=donchian_period,
            score_buy_threshold=score_buy, score_sell_threshold=score_sell,
        )
        set_controller_config(context, config)

        position_mode = config.get("position_mode", "HEDGE")
        stop_loss = config.get("stop_loss", 0.05)
        take_profit = config.get("take_profit", 0.03)
        max_exec = config.get("max_executors_per_side", 1)
        cooldown = config.get("cooldown_time", 60)
        ts = config.get("trailing_stop", {}) or {}
        ts_act = ts.get("activation_price", 0.015) if isinstance(ts, dict) else 0.015
        ts_delta = ts.get("trailing_delta", 0.005) if isinstance(ts, dict) else 0.005
        is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()
        final_step = 6 if is_perp else 4

        context.user_data["bots_state"] = "af_wizard_input"
        context.user_data["af_wizard_step"] = "final"

        interval_options = ["1m", "3m", "5m", "15m", "1h"]
        interval_row = [
            InlineKeyboardButton(
                f"✓ {opt}" if opt == interval else opt,
                callback_data=f"bots:af_interval:{opt}"
            )
            for opt in interval_options
        ]

        context.user_data["af_analysis"] = analysis

        strategy_row = [
            InlineKeyboardButton("🔥 Aggr", callback_data="bots:af_set_strat:aggressive"),
            InlineKeyboardButton("⚖️ Balanced", callback_data="bots:af_set_strat:balanced"),
            InlineKeyboardButton("🛡️ Cons", callback_data="bots:af_set_strat:conservative"),
        ]

        keyboard = [
            interval_row,
            strategy_row,
            [InlineKeyboardButton("💾 Save Config", callback_data="bots:af_save")],
            [
                InlineKeyboardButton("⬅️ Back", callback_data="bots:af_back_to_amount"),
                InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
            ],
        ]

        # ========== COSTRUISCI CONFIG_TEXT IN MARKDOWNV2 (COME MACD BB) ==========
        escaped_pair = escape_markdown_v2(pair)
        escaped_connector = escape_markdown_v2(connector)
        escaped_position_mode = escape_markdown_v2(position_mode)
        escaped_interval = escape_markdown_v2(interval)
        escaped_title = escape_markdown_v2(f"🦅 Anti-Folla V1 - Step {final_step}/{final_step} (Final)")

        # Escapa anche il pipe
        price_line = f"Price: `{current_price:,.6g}` | VWAP: `{vwap_period}` | DC: `{donchian_period}` | Interval: `{escaped_interval}`"
        escaped_price_line = escape_markdown_v2(price_line)

        config_text = (
            f"*{escaped_title}*\n\n"
            f"*{escaped_pair}*\n"
            f"{escaped_price_line}\n\n"
            f"`connector_name={escaped_connector}`\n"
            f"`trading_pair={escaped_pair}`\n"
            f"`total_amount_quote={total_amount:.0f}`\n"
            f"`leverage={leverage}`\n"
            f"`position_mode={escaped_position_mode}`\n"
            f"`max_executors_per_side={max_exec}`\n"
            f"`cooldown_time={cooldown}`\n"
            f"`stop_loss={stop_loss}`\n"
            f"`take_profit={take_profit}`\n"
            f"`trailing_stop_activation={ts_act}`\n"
            f"`trailing_stop_delta={ts_delta}`\n"
            f"`interval={escaped_interval}`\n"
            f"`is_perpetual={str(is_perp).lower()}`\n"
            f"`vwap_period={vwap_period}`\n"
            f"`donchian_period={donchian_period}`\n"
            f"`score_buy_threshold={score_buy}`\n"
            f"`score_sell_threshold={score_sell}`\n\n"
            r"_Edit: `field=value`_"
        )

        # Analysis con tripli backtick (non serve escape qui)
        analysis_text = format_anti_folla_analysis(analysis)
        config_text += "\n\n```\n" + analysis_text + "\n```"

        # ========== SE NON CI SONO CANDELE, MOSTRA SOLO TESTO ==========
        if not candles_list:
            try:
                await msg.edit_text(
                    text=config_text,
                    parse_mode="MarkdownV2",  # <-- CAMBIATO
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["af_wizard_message_id"] = msg.message_id
            except Exception:
                new_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=config_text,
                    parse_mode="MarkdownV2",  # <-- CAMBIATO
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["af_wizard_message_id"] = new_msg.message_id
            return

        # ========== TENTA DI GENERARE IL GRAFICO ==========
        chart_bytes = None
        if candles_list:
            try:
                from .controllers.anti_folla_v1.chart import generate_chart as af_chart
                chart_bytes = af_chart(config, candles_list, current_price)
            except Exception as chart_err:
                logger.warning(f"AF chart generation failed: {chart_err}")
                chart_bytes = None

        stored_msg_id = context.user_data.get("af_wizard_message_id")
        stored_chat_id = context.user_data.get("af_wizard_chat_id", chat_id)

        if chart_bytes is not None:
            chart_bytes.seek(0)
            if stored_msg_id:
                try:
                    await context.bot.delete_message(chat_id=stored_chat_id, message_id=stored_msg_id)
                except Exception:
                    pass
            try:
                await msg.delete()
            except Exception:
                pass
            new_msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=chart_bytes,
                caption=config_text,
                parse_mode="MarkdownV2",  # <-- CAMBIATO
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            context.user_data["af_wizard_message_id"] = new_msg.message_id
            context.user_data["af_wizard_chat_id"] = chat_id
        else:
            # No chart: edit/send as plain text message
            try:
                if stored_msg_id:
                    await context.bot.edit_message_text(
                        chat_id=stored_chat_id,
                        message_id=stored_msg_id,
                        text=config_text,
                        parse_mode="MarkdownV2",  # <-- CAMBIATO
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                else:
                    raise Exception("no stored_msg_id")
            except Exception:
                new_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=config_text,
                    parse_mode="MarkdownV2",  # <-- CAMBIATO
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                context.user_data["af_wizard_message_id"] = new_msg.message_id
                context.user_data["af_wizard_chat_id"] = chat_id

    except Exception as e:
        logger.error(f"AF final step error: {e}", exc_info=True)
        keyboard = [[InlineKeyboardButton("Back", callback_data="bots:main_menu")]]
        try:
            await msg.edit_text(
                format_error_message(f"Error: {str(e)}"),
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        except Exception:
            pass

async def handle_af_interval_change(update, context, interval: str) -> None:
    context.user_data["af_candles"] = None
    context.user_data["af_candles_interval"] = None
    await _af_show_final_step(update, context, interval=interval)

async def handle_af_set_strategy(update, context, strat_key: str) -> None:
    """Handle Anti-Folla strategy selection from final step buttons."""
    query = update.callback_query
    config = get_controller_config(context)

    analysis = context.user_data.get("af_analysis", {})

    from .controllers.anti_folla_v1.analysis import get_af_strategy_suggestions
    strats = get_af_strategy_suggestions(analysis)  # ← senza NATR!

    if strat_key in strats:
        selected = strats[strat_key]

        # Aggiorna i parametri specifici di Anti-Folla
        config["score_buy_threshold"] = selected.get("score_buy_threshold", 50.0)
        config["score_sell_threshold"] = selected.get("score_sell_threshold", -50.0)

        # Aggiorna i pesi
        config["weight_vwap"] = selected.get("weight_vwap", 15)
        config["weight_donchian"] = selected.get("weight_donchian", 10)
        config["weight_obv"] = selected.get("weight_obv", 15)
        config["weight_obi"] = selected.get("weight_obi", 20)
        config["weight_volume_spike"] = selected.get("weight_volume_spike", 10)
        config["weight_trade_flow"] = selected.get("weight_trade_flow", 15)
        config["weight_funding"] = selected.get("weight_funding", 15)

        # TP/SL
        config["take_profit"] = selected.get("take_profit", 0.03)
        config["stop_loss"] = selected.get("stop_loss", 0.05)

        # Trailing stop
        if "trailing_stop_activation" in selected:
            if not isinstance(config.get("trailing_stop"), dict):
                config["trailing_stop"] = {"activation_price": 0.015, "trailing_delta": 0.005}
            config["trailing_stop"]["activation_price"] = selected["trailing_stop_activation"]
            config["trailing_stop"]["trailing_delta"] = selected["trailing_stop_delta"]

        set_controller_config(context, config)

        await query.answer(f"✅ {selected['label']} applicata")

        return await _af_show_final_step(update, context)

async def handle_af_save(update, context) -> None:
    """Save Anti-Folla V1 configuration"""
    query = update.callback_query
    config = get_controller_config(context)
    config.pop("candles_config", None)
    config.pop("manual_kill_switch", None)
    # ========== FORZA IL CONNETTORE SPOT PER LE CANDELE ==========
    connector = config.get("connector_name", "")
    candles_connector = connector.replace("_perpetual", "").replace("_margin", "").replace("_spot", "")
    if not candles_connector or len(candles_connector) < 3:
        candles_connector = "kucoin"

    # Sovrascrivi candles_connector e candles_trading_pair
    config["candles_connector"] = candles_connector
    config["candles_trading_pair"] = config.get("trading_pair", "")
    # =============================================================

    config_id = config.get("id", "")
    chat_id = query.message.chat_id
    try:
        await query.message.delete()
    except Exception:
        pass
    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="Saving `" + escape_markdown_v2(config_id) + "`\\.\\.\\.  ",
        parse_mode="MarkdownV2",
    )
    try:
        client, _ = await get_bots_client(chat_id, context.user_data)
        await client.controllers.create_or_update_controller_config(config_id, config)
        for key in list(context.user_data.keys()):
            if key.endswith("_wizard_message_id") or key.endswith("_wizard_chat_id"):
                context.user_data.pop(key, None)
            if key.endswith("_wizard_step"):
                context.user_data.pop(key, None)
        context.user_data.pop("bots_state", None)
        keyboard = [
            [InlineKeyboardButton("Create Another", callback_data="bots:new_anti_folla_v1")],
            [InlineKeyboardButton("Back to Configs", callback_data="bots:controller_configs")],
        ]
        await status_msg.edit_text(
            "*Config Saved\\!*\n\nController `" + escape_markdown_v2(config_id) + "` saved successfully\\.",
            parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        logger.error(f"AF save error: {e}", exc_info=True)
        keyboard = [
            [InlineKeyboardButton("Try Again", callback_data="bots:af_save")],
            [InlineKeyboardButton("Back", callback_data="bots:controller_configs")],
        ]
        await status_msg.edit_text(format_error_message(f"Failed to save: {str(e)}"),
                                   parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))

# Back handlers
async def handle_af_back_to_connector(update, context) -> None:
    context.user_data["af_wizard_step"] = "connector_name"
    await _af_show_connector_step(update, context)

async def handle_af_back_to_pair(update, context) -> None:
    context.user_data["af_wizard_step"] = "trading_pair"
    await _af_show_pair_step(update, context)

async def handle_af_back_to_leverage(update, context) -> None:
    config = get_controller_config(context)
    connector = config.get("connector_name", "")
    is_perp = "_perpetual" in connector or "_margin" in connector
    if is_perp:
        context.user_data["af_wizard_step"] = "leverage"
        await _af_show_leverage_step(update, context)
    else:
        await handle_af_back_to_pair(update, context)

async def handle_af_back_to_amount(update, context) -> None:
    context.user_data["af_wizard_step"] = "total_amount_quote"
    context.user_data.pop("af_current_price", None)
    context.user_data.pop("af_candles", None)
    await _af_show_amount_step(update, context)

async def handle_af_back_to_position_mode(update, context) -> None:
    """Go back to position mode step"""
    context.user_data["af_wizard_step"] = "position_mode"
    await _af_show_position_mode_step(update, context)

async def handle_af_pair_select(
    update: Update, context: ContextTypes.DEFAULT_TYPE, trading_pair: str
) -> None:
    """Handle selection of a suggested trading pair in Anti-Folla wizard"""
    config = get_controller_config(context)
    chat_id = update.effective_chat.id

    for key in list(context.user_data.keys()):
        if key.endswith("_wizard_message_id") or key.endswith("_wizard_chat_id"):
            context.user_data.pop(key, None)
        if key.endswith("_wizard_step"):
            context.user_data.pop(key, None)

    config["trading_pair"] = trading_pair
    config["candles_trading_pair"] = trading_pair
    set_controller_config(context, config)

    connector = config.get("connector_name", "")
    is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()

    if is_perp:
        context.user_data["af_wizard_step"] = "leverage"
        await _af_show_leverage_step(update, context)
    else:
        config["leverage"] = 1
        config["position_mode"] = "ONEWAY"
        set_controller_config(context, config)
        context.user_data["af_wizard_step"] = "total_amount_quote"
        await _af_show_amount_step(update, context)

async def process_af_wizard_input(update, context, user_input: str) -> None:
    """Process text input during Anti-Folla V1 wizard"""
    step = context.user_data.get("af_wizard_step")
    chat_id = update.effective_chat.id
    config = get_controller_config(context)
    message_id = context.user_data.get("af_wizard_message_id")
    wizard_chat_id = context.user_data.get("af_wizard_chat_id", chat_id)
    try:
        try:
            await update.message.delete()
        except Exception:
            pass

        if step == "trading_pair":
            pair = user_input.upper().strip().replace("/", "-").replace("_", "-")
            connector = config.get("connector_name", "")

            # ========== VALIDAZIONE BASE: DEVE CONTENERE IL TRATTINO ==========
            if "-" not in pair:
                message_id = context.user_data.get("af_wizard_message_id")
                wizard_chat_id = context.user_data.get("af_wizard_chat_id", chat_id)

                keyboard = [
                    [InlineKeyboardButton("⬅️ Back", callback_data="bots:af_back_to_connector")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")],
                ]

                context_text = f"🏦 `{escape_markdown_v2(connector)}`\n\n"

                err_text = (
                    r"*🦅 Anti\-Folla V1 \- Step 2*" + "\n\n"
                    + context_text
                    + r"⚠️ *Invalid format\. Use BASE\-QUOTE \(e\.g\. BTC\-USDT\)*" + "\n\n"
                    r"Type the pair again:"
                )

                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id, message_id=message_id,
                        text=err_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                else:
                    msg = await context.bot.send_message(
                        chat_id=chat_id, text=err_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    context.user_data["af_wizard_message_id"] = msg.message_id
                return

            # ========== 2. VALIDAZIONE SULL'EXCHANGE (CON SUGGERIMENTI) ==========
            client, _ = await get_bots_client(chat_id, context.user_data)
            is_valid, error_msg, suggestions, correct_pair = (
                await validate_trading_pair(context.user_data, client, connector, pair)
            )

            if not is_valid:
                message_id = context.user_data.get("af_wizard_message_id")
                wizard_chat_id = context.user_data.get("af_wizard_chat_id", chat_id)

                keyboard = []
                for sugg in suggestions[:4]:
                    keyboard.append([InlineKeyboardButton(sugg, callback_data=f"bots:af_pair_select:{sugg}")])
                keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="bots:af_back_to_connector")])
                keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu")])

                context_text = f"🏦 `{escape_markdown_v2(connector)}`\n\n"

                err_text = (
                    r"*🦅 Anti\-Folla V1 \- Step 2*" + "\n\n"
                    + context_text
                    + f"❌ `{escape_markdown_v2(pair)}` not found on `{escape_markdown_v2(connector)}`\\.\n\n"
                    r"*Did you mean?*"
                )

                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id, message_id=message_id,
                        text=err_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                else:
                    msg = await context.bot.send_message(
                        chat_id=chat_id, text=err_text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    context.user_data["af_wizard_message_id"] = msg.message_id
                return

            if correct_pair:
                pair = correct_pair

            config["trading_pair"] = pair
            config["candles_trading_pair"] = pair
            for key in ["af_current_price", "af_candles", "af_candles_interval"]:
                context.user_data.pop(key, None)
            set_controller_config(context, config)

            connector = config.get("connector_name", "")
            is_perp = connector.endswith("_perpetual") or "_margin" in connector.lower()
            message_id = context.user_data.get("af_wizard_message_id")
            wizard_chat_id = context.user_data.get("af_wizard_chat_id", chat_id)

            if is_perp:
                context.user_data["af_wizard_step"] = "leverage"
                keyboard = [
                    [
                        InlineKeyboardButton("1x", callback_data="bots:af_leverage:1"),
                        InlineKeyboardButton("5x", callback_data="bots:af_leverage:5"),
                        InlineKeyboardButton("10x", callback_data="bots:af_leverage:10"),
                    ],
                    [
                        InlineKeyboardButton("20x", callback_data="bots:af_leverage:20"),
                        InlineKeyboardButton("50x", callback_data="bots:af_leverage:50"),
                        InlineKeyboardButton("75x", callback_data="bots:af_leverage:75"),
                    ],
                    [
                        InlineKeyboardButton("⬅️ Back", callback_data="bots:af_back_to_pair"),
                        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                    ],
                ]
                text = (
                    r"*🦅 Anti\-Folla V1 \- Step 3/6*" + "\n\n"
                    f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`" + "\n\n"
                    r"⚡ *Select Leverage*" + "\n"
                    r"_Or type a value \(e\.g\. 20\)_"
                )
            else:
                config["leverage"] = 1
                set_controller_config(context, config)
                context.user_data["af_wizard_step"] = "total_amount_quote"
                context.user_data["bots_state"] = "af_wizard_input"
                keyboard = [
                    [
                        InlineKeyboardButton("$100", callback_data="bots:af_amount:100"),
                        InlineKeyboardButton("$500", callback_data="bots:af_amount:500"),
                        InlineKeyboardButton("$1000", callback_data="bots:af_amount:1000"),
                    ],
                    [
                        InlineKeyboardButton("$2000", callback_data="bots:af_amount:2000"),
                        InlineKeyboardButton("$5000", callback_data="bots:af_amount:5000"),
                        InlineKeyboardButton("$10000", callback_data="bots:af_amount:10000"),
                    ],
                    [
                        InlineKeyboardButton("⬅️ Back", callback_data="bots:af_back_to_pair"),
                        InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                    ],
                ]
                text = (
                    r"*🦅 Anti\-Folla V1 \- Step 3/4*" + "\n\n"
                    f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}`" + "\n\n"
                    r"💰 *Total Amount \(USDT\)*" + "\n"
                    r"_Select or type an amount:_"
                )

            try:
                if message_id:
                    await context.bot.edit_message_text(
                        chat_id=wizard_chat_id, message_id=message_id,
                        text=text, parse_mode="MarkdownV2",
                        reply_markup=InlineKeyboardMarkup(keyboard),
                    )
                    context.user_data["af_wizard_message_id"] = message_id
            except Exception:
                pass
            return

        elif step == "leverage":
            val = int(float(user_input.strip().lower().replace("x", "")))
            config["leverage"] = val
            set_controller_config(context, config)
            context.user_data["af_wizard_step"] = "total_amount_quote"
            message_id = context.user_data.get("af_wizard_message_id")
            wizard_chat_id = context.user_data.get("af_wizard_chat_id", chat_id)
            connector = config.get("connector_name", "")
            pair = config.get("trading_pair", "")
            keyboard = [
                [
                    InlineKeyboardButton("$100", callback_data="bots:af_amount:100"),
                    InlineKeyboardButton("$500", callback_data="bots:af_amount:500"),
                    InlineKeyboardButton("$1000", callback_data="bots:af_amount:1000"),
                ],
                [
                    InlineKeyboardButton("$2000", callback_data="bots:af_amount:2000"),
                    InlineKeyboardButton("$5000", callback_data="bots:af_amount:5000"),
                    InlineKeyboardButton("$10000", callback_data="bots:af_amount:10000"),
                ],
                [
                    InlineKeyboardButton("⬅️ Back", callback_data="bots:af_back_to_leverage"),
                    InlineKeyboardButton("❌ Cancel", callback_data="bots:main_menu"),
                ],
            ]
            text = (
                r"*🦅 Anti\-Folla V1 \- Step 4/6*" + "\n\n"
                f"🏦 `{escape_markdown_v2(connector)}` \\| 🔗 `{escape_markdown_v2(pair)}` \\| ⚡ `{val}x`" + "\n\n"
                r"💰 *Total Amount \(USDT\)*" + "\n"
                r"_Select or type an amount:_"
            )
            context.user_data["bots_state"] = "af_wizard_input"
            if message_id:
                await context.bot.edit_message_text(
                    chat_id=wizard_chat_id, message_id=message_id,
                    text=text, parse_mode="MarkdownV2",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )

        elif step == "total_amount_quote":
            amount = float(user_input.strip().replace("$", "").replace(",", ""))
            config["total_amount_quote"] = amount
            set_controller_config(context, config)
            context.user_data["af_wizard_step"] = "final"
            wizard_chat_id = context.user_data.get("af_wizard_chat_id", chat_id)
            pair = config.get("trading_pair", "")
            tmp = await context.bot.send_message(
                chat_id=wizard_chat_id,
                text=r"*🦅 Anti\-Folla V1*" + "\n\n"
                     f"⏳ Loading chart for `{escape_markdown_v2(pair)}`\\.\\.\\.  ",
                parse_mode="MarkdownV2",
            )
            context.user_data["af_wizard_message_id"] = tmp.message_id
            await _af_show_final_step(update, context)

        elif step == "final":
            # Handle field=value edits
            if "=" in user_input:
                for line in user_input.strip().split("\n"):
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    field, value = line.split("=", 1)
                    field = field.strip().lower()
                    value = value.strip()
                    try:
                        if field in ("total_amount_quote", "stop_loss", "take_profit",
                                     "volume_spike_threshold", "obi_depth_percentage",
                                     "obi_buy_threshold", "obi_sell_threshold",
                                     "score_buy_threshold", "score_sell_threshold",
                                     "weight_vwap", "weight_donchian", "weight_obv",
                                     "weight_obi", "weight_volume_spike", "weight_trade_flow",
                                     "weight_funding", "trailing_stop_activation", "trailing_stop_delta"):
                            val = float(value)
                            if field == "trailing_stop_activation":
                                if not isinstance(config.get("trailing_stop"), dict):
                                    config["trailing_stop"] = {"activation_price": 0.015, "trailing_delta": 0.005}
                                config["trailing_stop"]["activation_price"] = val
                            elif field == "trailing_stop_delta":
                                if not isinstance(config.get("trailing_stop"), dict):
                                    config["trailing_stop"] = {"activation_price": 0.015, "trailing_delta": 0.005}
                                config["trailing_stop"]["trailing_delta"] = val
                            else:
                                config[field] = val
                        elif field in ("leverage", "max_executors_per_side", "cooldown_time",
                                       "take_profit_order_type", "vwap_period", "donchian_period",
                                       "atr_period", "obv_divergence_lookback"):
                            config[field] = int(float(value))
                        elif field in ("is_perpetual", "enable_order_book_imbalance"):
                            config[field] = value.lower() in ("true", "yes", "1")
                        elif field == "interval":
                            config["interval"] = value
                            context.user_data.pop("af_candles", None)
                            context.user_data["af_chart_interval"] = value
                        else:
                            config[field] = value
                    except Exception:
                        pass
                set_controller_config(context, config)

    except Exception as e:
        logger.error(f"AF wizard input error: {e}", exc_info=True)
