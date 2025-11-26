"""
CLOB Trading - Leverage & Position Mode configuration

Improved UX:
- Position Mode: Toggle buttons per perpetual connector showing current state
- Leverage: Simple text input (connector trading_pair leverage)
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import format_error_message, escape_markdown_v2
from handlers.config.user_preferences import get_clob_account

logger = logging.getLogger(__name__)

# Known perpetual connectors
PERPETUAL_CONNECTORS = [
    "binance_perpetual",
    "bybit_perpetual",
    "okx_perpetual",
    "kucoin_perpetual",
    "gate_io_perpetual",
]


async def _get_connector_position_modes(client, account: str) -> dict:
    """
    Fetch current position mode for each connected perpetual connector.

    Returns:
        Dict mapping connector_name to position_mode (HEDGE/ONE-WAY)
    """
    position_modes = {}

    try:
        # Get connected connectors from the account
        accounts_result = await client.accounts.get_accounts()
        accounts = accounts_result.get("accounts", {})

        if account not in accounts:
            logger.warning(f"Account {account} not found")
            return position_modes

        account_data = accounts[account]
        connected_connectors = list(account_data.get("connectors", {}).keys())

        # Filter for perpetual connectors
        perp_connectors = [c for c in connected_connectors if "perpetual" in c.lower()]

        # For each perpetual connector, try to get position mode
        for connector in perp_connectors:
            try:
                # Try to get position mode - this may vary by exchange API
                # Some exchanges expose this in account info
                result = await client.trading.get_position_mode(
                    account_name=account,
                    connector_name=connector
                )
                mode = result.get("position_mode", "HEDGE")
                position_modes[connector] = mode
            except Exception as e:
                # If get_position_mode doesn't exist or fails, default to HEDGE
                logger.debug(f"Could not get position mode for {connector}: {e}")
                position_modes[connector] = "HEDGE"

    except Exception as e:
        logger.error(f"Error fetching connector position modes: {e}", exc_info=True)

    return position_modes


async def handle_leverage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle leverage & position mode configuration screen"""
    account = get_clob_account(context.user_data)

    # Build header
    help_text = r"⚙️ *Leverage & Position Mode*" + "\n\n"
    help_text += f"📋 Account: `{escape_markdown_v2(account)}`\n\n"

    # Fetch position modes for connected perpetual connectors
    position_modes = {}
    perp_connectors = []

    try:
        from servers import server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if enabled_servers:
            server_name = enabled_servers[0]
            client = await server_manager.get_client(server_name)

            # Get connected connectors
            accounts_result = await client.accounts.get_accounts()
            accounts = accounts_result.get("accounts", {})

            if account in accounts:
                account_data = accounts[account]
                connected_connectors = list(account_data.get("connectors", {}).keys())
                perp_connectors = [c for c in connected_connectors if "perpetual" in c.lower()]

                # Store for later use
                context.user_data["perp_connectors"] = perp_connectors

                # Try to fetch position modes
                position_modes = await _get_connector_position_modes(client, account)
                context.user_data["position_modes"] = position_modes

    except Exception as e:
        logger.error(f"Error fetching account data: {e}", exc_info=True)
        help_text += "_Could not fetch account connectors_\n\n"

    # Position Mode Section
    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n"
    help_text += r"*🔄 Position Mode*" + "\n"
    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"

    if perp_connectors:
        help_text += r"Tap a button to toggle position mode:" + "\n"
        help_text += r"• `HEDGE` \- Can hold both long and short" + "\n"
        help_text += r"• `ONE\-WAY` \- Single position per symbol" + "\n\n"
    else:
        help_text += r"_No perpetual connectors found_" + "\n\n"

    # Leverage Section
    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n"
    help_text += r"*⚡ Set Leverage*" + "\n"
    help_text += r"━━━━━━━━━━━━━━━━━━━━" + "\n\n"
    help_text += r"Reply with:" + "\n"
    help_text += r"`connector trading_pair leverage`" + "\n\n"
    help_text += r"*Examples:*" + "\n"
    help_text += r"`binance_perpetual BTC\-USDT 10`" + "\n"
    help_text += r"`bybit_perpetual ETH\-USDT 20`" + "\n"

    # Build keyboard with position mode toggle buttons
    keyboard = []

    # Add button for each perpetual connector showing current mode
    for connector in perp_connectors:
        current_mode = position_modes.get(connector, "HEDGE")
        # Short name for button (remove _perpetual suffix)
        short_name = connector.replace("_perpetual", "").upper()
        button_text = f"{short_name}: {current_mode}"
        callback_data = f"clob:toggle_pos_mode:{connector}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

    # Back button
    keyboard.append([InlineKeyboardButton("« Back", callback_data="clob:main_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Set state for leverage text input
    context.user_data["clob_state"] = "leverage"

    await update.callback_query.message.edit_text(
        help_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_toggle_position_mode(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    connector_name: str
) -> None:
    """Toggle position mode for a specific connector"""
    try:
        account = get_clob_account(context.user_data)

        # Get current mode from cached data
        position_modes = context.user_data.get("position_modes", {})
        current_mode = position_modes.get(connector_name, "HEDGE")

        # Toggle mode
        new_mode = "ONE-WAY" if current_mode == "HEDGE" else "HEDGE"

        from servers import server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            raise ValueError("No enabled API servers available")

        server_name = enabled_servers[0]
        client = await server_manager.get_client(server_name)

        # Set new position mode
        result = await client.trading.set_position_mode(
            account_name=account,
            connector_name=connector_name,
            position_mode=new_mode
        )

        # Update cached position mode
        position_modes[connector_name] = new_mode
        context.user_data["position_modes"] = position_modes

        # Update cached position mode and refresh the leverage menu
        short_name = connector_name.replace("_perpetual", "").upper()

        # Refresh the leverage menu (will show updated mode)
        await handle_leverage(update, context)

    except Exception as e:
        logger.error(f"Error toggling position mode: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to change position mode: {str(e)}")
        await update.callback_query.message.edit_text(error_message, parse_mode="MarkdownV2")


async def process_leverage(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_input: str
) -> None:
    """Process set leverage user input

    Format: connector trading_pair leverage
    Example: binance_perpetual BTC-USDT 10
    """
    try:
        parts = user_input.split()

        if len(parts) < 3:
            raise ValueError("Missing parameters. Format: connector trading_pair leverage\nExample: binance_perpetual BTC-USDT 10")

        connector_name = parts[0]
        trading_pair = parts[1]
        leverage = int(parts[2])

        account = get_clob_account(context.user_data)

        if leverage <= 0:
            raise ValueError("Leverage must be a positive integer")

        from servers import server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            raise ValueError("No enabled API servers available")

        server_name = enabled_servers[0]
        client = await server_manager.get_client(server_name)

        # Set leverage
        result = await client.trading.set_leverage(
            account_name=account,
            connector_name=connector_name,
            trading_pair=trading_pair,
            leverage=leverage,
        )

        config_info = escape_markdown_v2(
            f"✅ Leverage updated!\n\n"
            f"Connector: {connector_name}\n"
            f"Pair: {trading_pair}\n"
            f"Leverage: {leverage}x\n"
            f"Account: {account}"
        )

        # Create keyboard with back button
        keyboard = [[InlineKeyboardButton("« Back to Leverage", callback_data="clob:leverage")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            config_info,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except ValueError as e:
        error_message = format_error_message(str(e))
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")
    except Exception as e:
        logger.error(f"Error setting leverage: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to set leverage: {str(e)}")
        await update.message.reply_text(error_message, parse_mode="MarkdownV2")
