"""
Gateway menu and server selection
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from ..server_context import build_config_message_header, format_server_selection_needed
from ._shared import logger, escape_markdown_v2


async def show_gateway_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show gateway configuration menu with status for default server
    """
    try:
        from config_manager import get_config_manager

        servers = get_config_manager().list_servers()

        if not servers:
            message_text = format_server_selection_needed()
            keyboard = [[InlineKeyboardButton("« Back", callback_data="config_back")]]
        else:
            # Build unified header with server and gateway info
            chat_id = query.message.chat_id
            header, server_online, gateway_running = await build_config_message_header(
                "🌐 Gateway Configuration",
                include_gateway=True,
                chat_id=chat_id,
                user_data=context.user_data
            )

            message_text = header
            keyboard = []

            # Show appropriate action buttons based on server and gateway status
            if not server_online:
                message_text += "⚠️ _Server is offline\\. Cannot manage Gateway\\._"
            elif gateway_running:
                message_text += "_Gateway is running\\. Configure DEX settings or manage the container\\._"
                keyboard.extend([
                    [
                        InlineKeyboardButton("🔑 Wallets", callback_data="gateway_wallets"),
                        InlineKeyboardButton("🔌 Connectors", callback_data="gateway_connectors"),
                    ],
                    [
                        InlineKeyboardButton("🌍 Networks", callback_data="gateway_networks"),
                        InlineKeyboardButton("💧 Pools", callback_data="gateway_pools"),
                    ],
                    [
                        InlineKeyboardButton("🪙 Tokens", callback_data="gateway_tokens"),
                        InlineKeyboardButton("📋 Logs", callback_data="gateway_logs"),
                    ],
                    [
                        InlineKeyboardButton("🔄 Restart", callback_data="gateway_restart"),
                        InlineKeyboardButton("⏹ Stop", callback_data="gateway_stop"),
                    ],
                ])
            else:
                message_text += "_Gateway is not running\\. Deploy it to start configuring DEX operations\\._"
                keyboard.append([
                    InlineKeyboardButton("🚀 Deploy Gateway", callback_data="gateway_deploy"),
                ])

            # Add back button
            keyboard.append([InlineKeyboardButton("« Back", callback_data="config_back")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except Exception as e:
            error_str = str(e)
            if "Message is not modified" in error_str:
                # Try to answer the query, but don't raise if it fails
                try:
                    await query.answer("✅ Already up to date")
                except:
                    pass  # Query expired, ignore
            elif "Query is too old" in error_str or "query id is invalid" in error_str:
                # Query expired, message was likely already updated - ignore
                pass
            else:
                raise

    except Exception as e:
        logger.error(f"Error showing gateway menu: {e}", exc_info=True)
        error_text = f"❌ Error loading gateway: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="config_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def show_server_selection(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show server selection menu for gateway configuration"""
    try:
        from config_manager import get_config_manager

        servers = get_config_manager().list_servers()
        default_server = get_config_manager().get_default_server()

        message_text = (
            "🔄 *Select Server*\n\n"
            "Choose which server's Gateway to configure:"
        )

        # Create server buttons
        server_buttons = []
        for server_name in servers.keys():
            button_text = server_name
            if server_name == default_server:
                button_text += " ⭐️"
            server_buttons.append([
                InlineKeyboardButton(button_text, callback_data=f"gateway_server_{server_name}")
            ])

        keyboard = server_buttons + [
            [InlineKeyboardButton("« Back", callback_data="config_gateway")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing server selection: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)}")


async def handle_server_selection(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle server selection for gateway configuration"""
    try:
        from config_manager import get_config_manager

        server_name = query.data.replace("gateway_server_", "")

        # Set as default server temporarily for this session
        # Or we could store it in context for this specific flow
        success = get_config_manager().set_default_server(server_name)

        if success:
            await query.answer(f"✅ Switched to {server_name}")
            await show_gateway_menu(query, context)
        else:
            await query.answer("❌ Failed to switch server")

    except Exception as e:
        logger.error(f"Error handling server selection: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)}")
