"""
Portfolio command handler using hummingbot_api_client
"""

import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from condor.fetchers.portfolio import dedupe_unified_accounts
from condor.preferences import get_all_enabled_networks
from handlers import is_gateway_network
from handlers.bots._shared import NO_ACCESSIBLE_SERVERS, resolve_accessible_server
from handlers.config import clear_config_state
from utils.auth import hummingbot_api_required, restricted
from utils.telegram_formatters import (
    escape_markdown_v2,
    format_connector_detail,
    format_error_message,
    format_portfolio_overview,
)

logger = logging.getLogger(__name__)


# ============================================
# PORTFOLIO STATE KEYS (context.user_data)
# ============================================
# Centralized definitions so a typo is a NameError instead of a silent
# miss, and renaming a key only requires editing one place.

KEY_TEXT_MESSAGE_ID = "portfolio_text_message_id"
KEY_CHAT_ID = "portfolio_chat_id"
KEY_SERVER_NAME = "portfolio_server_name"
KEY_SERVER_STATUS = "portfolio_server_status"
KEY_BALANCES = "portfolio_balances"
KEY_CONNECTOR_KEYS = "portfolio_connector_keys"
KEY_VIEW_MODE = "portfolio_view_mode"


def _filter_balances_by_networks(balances: dict, enabled_networks: set) -> dict:
    """
    Filter portfolio balances to only include enabled networks for Gateway connectors.

    CEX connectors (binance, hyperliquid, etc.) are never filtered.
    Only Gateway/DEX connectors (solana-mainnet-beta, ethereum-mainnet, etc.) are filtered.

    Args:
        balances: Portfolio state dict {account: {connector: [balances]}}
        enabled_networks: Set of enabled network IDs, or None for no filtering

    Returns:
        Filtered balances dict with same structure
    """
    if enabled_networks is None:
        return balances

    if not balances:
        return balances

    filtered = {}
    for account_name, account_data in balances.items():
        filtered_account = {}
        for connector_name, connector_balances in account_data.items():
            connector_lower = connector_name.lower()

            # CEX connectors are never filtered - always include them
            if not is_gateway_network(connector_name):
                filtered_account[connector_name] = connector_balances
            # Gateway connectors are filtered by enabled networks
            elif connector_lower in enabled_networks:
                filtered_account[connector_name] = connector_balances

        if filtered_account:
            filtered[account_name] = filtered_account

    return filtered


def _get_connector_keys(balances: dict) -> list:
    """
    Extract connector keys from balances for keyboard buttons.

    Args:
        balances: Portfolio state {account: {connector: [holdings]}}

    Returns:
        List of "account:connector" keys, sorted by total value descending
    """
    if not balances:
        return []

    connector_values = []
    for account_name, account_data in balances.items():
        for connector_name, connector_balances in account_data.items():
            if connector_balances:
                total = sum(
                    b.get("value", 0)
                    for b in connector_balances
                    if b.get("value", 0) > 0
                )
                if total > 0:
                    connector_values.append(
                        {"key": f"{account_name}:{connector_name}", "value": total}
                    )

    # Sort by value descending
    connector_values.sort(key=lambda x: x["value"], reverse=True)
    return [c["key"] for c in connector_values]


def build_portfolio_keyboard(connector_keys: list) -> InlineKeyboardMarkup:
    """
    Build keyboard with connector buttons and refresh.

    Args:
        connector_keys: List of "account:connector" keys

    Returns:
        InlineKeyboardMarkup with connector buttons
    """
    keyboard = []

    # Row(s) of connector buttons (max 2 per row for longer names)
    if connector_keys:
        connector_row = []
        for conn_key in connector_keys:
            # Extract connector name for display (after the colon)
            display_name = conn_key.split(":")[-1]

            connector_row.append(
                InlineKeyboardButton(
                    display_name, callback_data=f"portfolio:connector:{conn_key}"
                )
            )
            # Use max 2 per row to fit longer names like "solana-mainnet-beta"
            if len(connector_row) == 2:
                keyboard.append(connector_row)
                connector_row = []
        if connector_row:
            keyboard.append(connector_row)

    # Bottom row: Refresh only
    keyboard.append(
        [InlineKeyboardButton("🔄 Refresh", callback_data="portfolio:refresh")]
    )

    return InlineKeyboardMarkup(keyboard)


def build_connector_detail_keyboard() -> InlineKeyboardMarkup:
    """Build keyboard for connector detail view with Back button."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⬅️ Back to Overview", callback_data="portfolio:back_overview"
                )
            ]
        ]
    )


# ============================================
# SERVER RESOLUTION (access-checked)
# ============================================
# The resolution itself lives in handlers/bots/_shared.py so /portfolio, /bots
# and /trade cannot drift apart on access control. NO_ACCESSIBLE_SERVERS is
# re-exported here (imported above) because the portfolio error paths speak
# that wording.


def _resolve_server_for_user(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Pick the server this user's portfolio may be read from.

    Both /portfolio and its refresh callback used to choose from *every* enabled
    server on the install and fall back to the first one, which handed a user
    with no shared server (or a stale ``active_server`` preference) somebody
    else's balance sheet. The resolution now lives in one place shared with
    ``get_bots_client`` and the trade menu; this stays as the portfolio-shaped
    entry point (it takes a ``context``, not a ``user_data`` dict).

    Raises:
        ValueError: when the user has no enabled server they can access.
    """
    return resolve_accessible_server(context.user_data)


@restricted
@hummingbot_api_required
async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /portfolio command - Display comprehensive portfolio dashboard

    Usage:
        /portfolio - Show portfolio dashboard with all graphs and information

    Progressive loading: Fetches all data in parallel and updates UI as each piece arrives.
    """
    # Clear any config state to prevent interference
    clear_config_state(context)

    # Get the appropriate message object for replies
    message = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    chat_id = update.effective_chat.id
    if not message:
        logger.error("No message object available for portfolio_command")
        return

    try:
        from condor.server_data_service import ServerDataType, get_server_data_service
        from config_manager import get_config_manager

        # Only servers this user has access to are candidates
        try:
            server_name = _resolve_server_for_user(context)
        except ValueError as e:
            await message.reply_text(
                format_error_message(str(e)), parse_mode="MarkdownV2"
            )
            return

        # Send initial loading message immediately
        text_msg = await message.reply_text(
            f"💼 *Portfolio Details* \\| _Server: {escape_markdown_v2(server_name)} ⏳_\n\n"
            f"_Loading\\.\\.\\._",
            parse_mode="MarkdownV2",
        )

        t_start = time.time()

        client = await get_config_manager().get_client(server_name)
        logger.info(f"[TIMING] get_client: {time.time() - t_start:.2f}s")

        # Server is online if we got a client
        server_status = "online"

        # ========================================
        # FETCH BALANCES
        # ========================================
        # Read the cache the dashboard already reads instead of forcing an
        # exchange-wide re-fetch on every render. SDS polls PORTFOLIO every 10s
        # with a 60s TTL for each configured server, so the initial view is at
        # most one TTL old — in practice a few seconds — and usually free. That
        # staleness window is deliberate and bounded: mutations invalidate the
        # key (see handlers/cex/trade.py and handlers/dex/swap.py), and the
        # Refresh button below still forces a real get_state(refresh=True).
        # get_or_fetch falls back to a real fetch when the key is cold and
        # returns None rather than raising when the server is unreachable.
        try:
            balances = await get_server_data_service().get_or_fetch(
                server_name, ServerDataType.PORTFOLIO
            )
        except Exception as e:
            logger.error(f"Failed to fetch balances: {e}")
            balances = None
        logger.info(f"[TIMING] balances fetch: {time.time() - t_start:.2f}s")

        # Filter balances by enabled networks from wallet preferences
        if balances:
            enabled_networks = get_all_enabled_networks(context.user_data)
            if enabled_networks:
                balances = _filter_balances_by_networks(balances, enabled_networks)
            balances, _ = dedupe_unified_accounts(balances)

        # Build keyboard with connector buttons
        connector_keys = _get_connector_keys(balances)
        reply_markup = build_portfolio_keyboard(connector_keys)

        # Render final message
        overview_data = {
            "balances": balances,
        }
        formatted_message = format_portfolio_overview(
            overview_data,
            server_name=server_name,
            server_status=server_status,
        )
        try:
            await text_msg.edit_text(
                formatted_message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
        except Exception:
            pass

        logger.info(f"[TIMING] /portfolio total: {time.time() - t_start:.2f}s")

        # Store data for callbacks
        context.user_data[KEY_TEXT_MESSAGE_ID] = text_msg.message_id
        context.user_data[KEY_CHAT_ID] = message.chat_id
        context.user_data[KEY_SERVER_NAME] = server_name
        context.user_data[KEY_SERVER_STATUS] = server_status
        # Cache data for connector detail callbacks
        context.user_data[KEY_BALANCES] = balances
        context.user_data[KEY_CONNECTOR_KEYS] = connector_keys

    except Exception as e:
        logger.error(f"Error fetching portfolio: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to fetch portfolio: {str(e)}")
        await message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# PORTFOLIO CALLBACK HANDLERS
# ============================================


@restricted
async def portfolio_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle inline button callbacks for portfolio operations"""
    query = update.callback_query
    await query.answer()

    logger.info(f"Portfolio callback received: {query.data}")

    try:
        callback_parts = query.data.split(":", 1)
        action = callback_parts[1] if len(callback_parts) > 1 else query.data

        logger.info(f"Portfolio action: {action}")

        if action == "refresh":
            await handle_portfolio_refresh(update, context)
        elif action.startswith("connector:"):
            connector_key = action.replace("connector:", "")
            await handle_connector_detail(update, context, connector_key)
        elif action == "back_overview":
            await handle_back_to_overview(update, context)
        else:
            logger.warning(f"Unknown portfolio action: {action}")

    except Exception as e:
        logger.error(f"Error in portfolio callback handler: {e}", exc_info=True)
        error_message = format_error_message(f"Operation failed: {str(e)}")
        try:
            await query.message.reply_text(error_message, parse_mode="MarkdownV2")
        except Exception as e2:
            logger.error(f"Failed to send error message: {e2}")


async def handle_portfolio_refresh(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle refresh button - force refresh balances from exchanges"""
    query = update.callback_query
    await query.answer("Refreshing from exchanges...")
    await refresh_portfolio_dashboard(update, context, refresh=True)


async def handle_connector_detail(
    update: Update, context: ContextTypes.DEFAULT_TYPE, connector_key: str
) -> None:
    """
    Handle connector inspection - show tokens for specific connector.

    Args:
        connector_key: "account:connector" format (e.g., "main:binance")
    """
    query = update.callback_query
    await query.answer()

    # Get cached data
    balances = context.user_data.get(KEY_BALANCES)
    text_message_id = context.user_data.get(KEY_TEXT_MESSAGE_ID)
    chat_id = context.user_data.get(KEY_CHAT_ID)

    if not balances or not text_message_id or not chat_id:
        logger.warning("Missing cached data for connector detail view")
        return

    # Calculate total value from balances
    total_value = 0.0
    for account_data in balances.values():
        for connector_balances in account_data.values():
            if connector_balances:
                for b in connector_balances:
                    v = b.get("value", 0)
                    if v > 0:
                        total_value += v

    try:
        bot = query.get_bot()

        # Format connector detail message
        detail_message = format_connector_detail(
            balances=balances,
            connector_key=connector_key,
            total_value=total_value,
        )

        # Update text message with connector detail and Back button
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=text_message_id,
            text=detail_message,
            parse_mode="MarkdownV2",
            reply_markup=build_connector_detail_keyboard(),
        )

        # Store current view mode
        context.user_data[KEY_VIEW_MODE] = f"connector:{connector_key}"

    except Exception as e:
        logger.error(f"Error showing connector detail: {e}", exc_info=True)


async def handle_back_to_overview(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle 'Back' button - return to main portfolio overview."""
    query = update.callback_query
    await query.answer()

    # Get cached data
    balances = context.user_data.get(KEY_BALANCES)
    server_name = context.user_data.get(KEY_SERVER_NAME)
    server_status = context.user_data.get(KEY_SERVER_STATUS)
    connector_keys = context.user_data.get(KEY_CONNECTOR_KEYS, [])
    text_message_id = context.user_data.get(KEY_TEXT_MESSAGE_ID)
    chat_id = context.user_data.get(KEY_CHAT_ID)

    if not text_message_id or not chat_id:
        logger.warning("Missing message IDs for back to overview")
        return

    try:
        bot = query.get_bot()

        overview_data = {"balances": balances}
        overview_message = format_portfolio_overview(
            overview_data,
            server_name=server_name,
            server_status=server_status,
        )

        # Update text message with overview and connector buttons
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=text_message_id,
            text=overview_message,
            parse_mode="MarkdownV2",
            reply_markup=build_portfolio_keyboard(connector_keys),
        )

        # Clear view mode
        context.user_data[KEY_VIEW_MODE] = "overview"

    except Exception as e:
        logger.error(f"Error returning to overview: {e}", exc_info=True)


async def refresh_portfolio_dashboard(
    update: Update, context: ContextTypes.DEFAULT_TYPE, refresh: bool = True
) -> None:
    """Refresh both the text message and photo with new settings

    Args:
        refresh: If True, force refresh balances from exchanges (bypasses API cache). Defaults to True.
    """
    query = update.callback_query
    bot = query.get_bot()

    chat_id = context.user_data.get(KEY_CHAT_ID)
    text_message_id = context.user_data.get(KEY_TEXT_MESSAGE_ID)

    if not chat_id or not text_message_id:
        logger.warning("Missing message IDs for refresh")
        return

    try:
        from condor.server_data_service import ServerDataType, get_server_data_service
        from config_manager import get_config_manager

        # Same access-checked resolution as the command
        try:
            server_name = _resolve_server_for_user(context)
        except ValueError as e:
            logger.warning(f"Portfolio refresh without an accessible server: {e}")
            return

        client = await get_config_manager().get_client(server_name)
        server_status = "online"

        # Fetch balances only. This is the explicit-refresh path (the button
        # answers "Refreshing from exchanges..."), so it keeps re-querying every
        # exchange rather than reading the cache.
        try:
            balances = await client.portfolio.get_state(refresh=refresh)
        except Exception as e:
            logger.error(f"Failed to fetch balances: {e}")
            balances = None

        if balances is not None:
            # Push the fresh payload into the shared cache, as the web route's
            # refresh branch does. /portfolio now renders from SDS, so leaving
            # the older polled value in place would make the next view regress
            # to what the user just refreshed away from. Store the raw state:
            # every surface applies its own filtering on top.
            try:
                get_server_data_service().put(
                    server_name, ServerDataType.PORTFOLIO, balances
                )
            except Exception as e:
                logger.warning(f"Failed to update portfolio cache: {e}")

        # Filter balances by enabled networks
        if balances:
            enabled_networks = get_all_enabled_networks(context.user_data)
            if enabled_networks:
                balances = _filter_balances_by_networks(balances, enabled_networks)
            balances, _ = dedupe_unified_accounts(balances)

        connector_keys = _get_connector_keys(balances)
        reply_markup = build_portfolio_keyboard(connector_keys)

        overview_data = {"balances": balances}
        formatted_message = format_portfolio_overview(
            overview_data,
            server_name=server_name,
            server_status=server_status,
        )
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=text_message_id,
                text=formatted_message,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.warning(f"Failed to update text message: {e}")

        # Store data for callbacks
        context.user_data[KEY_SERVER_NAME] = server_name
        context.user_data[KEY_SERVER_STATUS] = server_status
        context.user_data[KEY_BALANCES] = balances
        context.user_data[KEY_CONNECTOR_KEYS] = connector_keys

    except Exception as e:
        logger.error(f"Failed to refresh portfolio dashboard: {e}", exc_info=True)


def get_portfolio_callback_handler():
    """Returns the callback query handler for portfolio operations"""
    return CallbackQueryHandler(portfolio_callback_handler, pattern="^portfolio:")
