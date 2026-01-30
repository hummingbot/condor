"""
Configuration management command handlers
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

from utils.auth import restricted

logger = logging.getLogger(__name__)


def clear_config_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Clear all config-related state from user context.
    Call this when starting other commands to prevent state pollution.

    Note: This is now a convenience wrapper around clear_all_input_states()
    for backwards compatibility.
    """
    from handlers import clear_all_input_states
    clear_all_input_states(context)


def _get_start_menu_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Build the start menu inline keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("🔌 Servers", callback_data="start:config_servers"),
            InlineKeyboardButton("🔑 Keys", callback_data="start:config_keys"),
            InlineKeyboardButton("🌐 Gateway", callback_data="start:config_gateway"),
        ],
    ]
    if is_admin:
        keyboard.append([InlineKeyboardButton("👑 Admin", callback_data="start:admin")])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="start:cancel")])
    return InlineKeyboardMarkup(keyboard)


async def _show_start_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show the /start menu (replaces config menu for Back navigation).
    Mirrors the logic from main.py start() but for callback query context.
    """
    import asyncio
    from config_manager import get_config_manager, get_effective_server
    from handlers.config.server_context import get_gateway_status_info
    from utils.telegram_formatters import escape_markdown_v2

    user_id = query.from_user.id
    chat_id = query.message.chat_id
    cm = get_config_manager()
    is_admin = cm.is_admin(user_id)

    # Get all servers and their statuses in parallel
    servers = cm.list_servers()
    active_server = get_effective_server(chat_id, context.user_data) or cm.get_default_server()

    server_statuses = {}
    active_server_online = False

    if servers:
        # Query all server statuses in parallel
        status_tasks = [cm.check_server_status(name) for name in servers]
        status_results = await asyncio.gather(*status_tasks, return_exceptions=True)

        for server_name, status_result in zip(servers, status_results):
            if isinstance(status_result, Exception):
                status = "error"
            else:
                status = status_result.get("status", "unknown")
            server_statuses[server_name] = status
            if server_name == active_server and status == "online":
                active_server_online = True

    # Build servers list display
    servers_display = ""
    if servers:
        for server_name in servers:
            status = server_statuses.get(server_name, "unknown")
            icon = "🟢" if status == "online" else "🔴"
            is_active = " ⭐" if server_name == active_server else ""
            server_escaped = escape_markdown_v2(server_name)
            servers_display += f"  {icon} `{server_escaped}`{is_active}\n"
    else:
        servers_display = "  _No servers configured_\n"

    # Get gateway and accounts info only if active server is online
    extra_info = ""
    if active_server_online:
        try:
            gateway_header, _ = await get_gateway_status_info(chat_id, context.user_data)
            extra_info += gateway_header

            client = await cm.get_client_for_chat(chat_id, preferred_server=active_server)
            accounts = await client.accounts.list_accounts()
            if accounts:
                total_creds = 0
                for account in accounts:
                    try:
                        creds = await client.accounts.list_account_credentials(account_name=str(account))
                        total_creds += len(creds) if creds else 0
                    except Exception:
                        pass
                accounts_escaped = escape_markdown_v2(str(len(accounts)))
                creds_escaped = escape_markdown_v2(str(total_creds))
                extra_info += f"*Accounts:* {accounts_escaped} \\({creds_escaped} keys\\)\n"
        except Exception as e:
            logger.warning(f"Failed to get extra info: {e}")

    # Build the message
    admin_badge = " 👑" if is_admin else ""
    capabilities = """_Trade CEX/DEX, manage bots, monitor portfolio_"""

    # Offline help message
    offline_help = ""
    if not active_server_online and servers:
        offline_help = """
⚠️ *Active server is offline*
• Ensure `hummingbot\\-backend\\-api` is running
• Or select an online server below
"""

    # Menu descriptions
    menu_help = r"""
🔌 *Servers* \- Add/manage Hummingbot API servers
🔑 *Keys* \- Connect exchange API credentials
🌐 *Gateway* \- Deploy Gateway for DEX trading
"""

    reply_text = rf"""
🦅 *Condor*{admin_badge}
{capabilities}

*Servers:*
{servers_display}{offline_help}{extra_info}{menu_help}"""
    keyboard = _get_start_menu_keyboard(is_admin=is_admin)
    await query.message.edit_text(reply_text, parse_mode="MarkdownV2", reply_markup=keyboard)


async def config_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle callback queries from config menu buttons - Routes to appropriate sub-module
    """
    from .servers import handle_servers_callback
    from .api_keys import handle_api_keys_callback
    from .gateway import handle_gateway_callback

    query = update.callback_query
    await query.answer()

    # Main menu actions
    if query.data == "config_close":
        await query.message.delete()
        return
    elif query.data == "config_back":
        await _show_start_menu(query, context)
        return

    # Route to appropriate sub-module based on callback data prefix
    if query.data == "config_api_servers" or query.data.startswith(("api_server_", "modify_field_", "add_server_")):
        await handle_servers_callback(update, context)
    elif query.data == "config_api_keys" or query.data.startswith("api_key_"):
        await handle_api_keys_callback(update, context)
    elif query.data == "config_gateway" or query.data.startswith("gateway_"):
        await handle_gateway_callback(update, context)
    elif query.data == "config_admin" or query.data.startswith("admin:"):
        from handlers.admin import admin_callback_handler
        await admin_callback_handler(update, context)


# Create callback handler instance for registration
def get_config_callback_handler():
    """Get the callback query handler for config menu"""
    return CallbackQueryHandler(
        config_callback_handler,
        pattern="^config_|^modify_field_|^add_server_|^api_server_|^api_key_|^gateway_|^admin:"
    )


def get_modify_value_handler():
    """
    Get the UNIFIED message handler for ALL text input flows.

    This handler routes text input to the appropriate sub-handler based on context state.
    Order of priority:
    1. Trading states (cex_state, dex_state) - highest priority
    2. Config states (server modification, API keys, gateway)
    """
    from .servers import handle_server_input
    from .api_keys import handle_api_key_input
    from .gateway import handle_gateway_input

    async def handle_all_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Route text input to appropriate handler based on context state"""
        logger.info(f"handle_all_text_input: user_data keys = {list(context.user_data.keys())}")

        # 1. Check CEX trading state (highest priority)
        if context.user_data.get('cex_state'):
            from handlers.cex import cex_message_handler
            await cex_message_handler(update, context)
            return

        # 2. Check DEX trading state
        if context.user_data.get('dex_state'):
            from handlers.dex import dex_message_handler
            await dex_message_handler(update, context)
            return

        # 3. Check Bots state
        if context.user_data.get('bots_state'):
            from handlers.bots import bots_message_handler
            await bots_message_handler(update, context)
            return

        # 3.5. Check Executors state
        if context.user_data.get('executors_state'):
            from handlers.executors import executors_message_handler
            handled = await executors_message_handler(update, context)
            if handled:
                return

        # 4. Check config flows - server modification
        if context.user_data.get('awaiting_add_server_input') or context.user_data.get('awaiting_modify_input'):
            await handle_server_input(update, context)
            return

        # 5. Check config flows - API keys
        if context.user_data.get('awaiting_api_key_input'):
            await handle_api_key_input(update, context)
            return

        # 6. Check config flows - gateway
        if (context.user_data.get('awaiting_gateway_input') or
              context.user_data.get('awaiting_wallet_input') or
              context.user_data.get('awaiting_connector_input') or
              context.user_data.get('awaiting_network_input') or
              context.user_data.get('awaiting_token_input') or
              context.user_data.get('awaiting_pool_input')):
            await handle_gateway_input(update, context)
            return

        # 7. Check routines state
        if context.user_data.get('routines_state'):
            from handlers.routines import routines_message_handler
            await routines_message_handler(update, context)
            return

        # 8. Check server share state
        if context.user_data.get('awaiting_share_user_id'):
            from handlers.config.servers import handle_share_user_id_input
            await handle_share_user_id_input(update, context)
            return

        # No active state - ignore the message
        logger.debug(f"No active input state for message: {update.message.text[:50] if update.message else 'N/A'}...")

    return MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_text_input)
