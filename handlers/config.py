"""
Configuration management command handlers
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, ConversationHandler, MessageHandler, filters

from utils.auth import restricted
from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)

# Conversation states for adding/modifying servers
(ADD_SERVER_NAME, ADD_SERVER_HOST, ADD_SERVER_PORT,
 ADD_SERVER_USERNAME, ADD_SERVER_PASSWORD,
 MODIFY_SERVER_CHOICE, MODIFY_SERVER_VALUE) = range(7)


def _get_config_menu_markup_and_text():
    """
    Build the main config menu keyboard and message text
    """
    keyboard = [
        [
            InlineKeyboardButton("🔌 API Servers", callback_data="config_api_servers"),
            InlineKeyboardButton("🔑 Connect Keys", callback_data="config_connect_keys"),
        ],
        [
            InlineKeyboardButton("❌ Close", callback_data="config_close"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    message_text = (
        "⚙️ *Configuration Menu*\n\n"
        "Select a configuration category:\n\n"
        "🔌 *API Servers* \\- Manage Hummingbot API instances\n"
        "🔑 *Connect Keys* \\- Manage exchange credentials"
    )

    return reply_markup, message_text


async def show_config_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show the main config menu
    """
    reply_markup, message_text = _get_config_menu_markup_and_text()

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


@restricted
async def config_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /config command - Show configuration options

    Displays a menu with configuration categories:
    - API Servers (Hummingbot instances)
    - Connect Keys (Exchange API credentials)
    """
    reply_markup, message_text = _get_config_menu_markup_and_text()

    await update.message.reply_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def config_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle callback queries from config menu buttons
    """
    query = update.callback_query
    await query.answer()

    if query.data == "config_api_servers":
        await show_api_servers(query, context)
    elif query.data == "config_connect_keys":
        await show_connect_keys(query, context)
    elif query.data == "config_close":
        await query.message.delete()
    elif query.data == "config_back":
        await show_config_menu(query, context)
    elif query.data.startswith("api_server_"):
        await handle_api_server_action(query, context)
    elif query.data.startswith("connect_key_"):
        await handle_connect_key_action(query, context)


async def show_api_servers(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show API servers configuration with status and actions
    """
    try:
        from servers import server_manager

        servers = server_manager.list_servers()
        default_server = server_manager.get_default_server()

        if not servers:
            message_text = (
                "🔌 *API Servers*\n\n"
                "No API servers configured\\.\n\n"
                "_Use the buttons below to add a server\\._"
            )
            keyboard = [
                [InlineKeyboardButton("➕ Add Server", callback_data="api_server_add")],
                [InlineKeyboardButton("« Back", callback_data="config_back")]
            ]
        else:
            # Build server list with status
            server_lines = []
            server_buttons = []

            for server_name, server_config in servers.items():
                # Check server status
                status_result = await server_manager.check_server_status(server_name)
                if status_result["status"] == "online":
                    status_label = "[Online]"
                elif status_result["status"] == "auth_error":
                    status_label = "[Auth Error]"
                elif status_result["status"] == "offline":
                    status_label = "[Offline]"
                else:
                    status_label = "[Error]"

                # Default server indicator
                default_indicator = " ⭐️" if server_name == default_server else ""

                url = f"{server_config['host']}:{server_config['port']}"
                url_escaped = escape_markdown_v2(url)
                username_escaped = escape_markdown_v2(server_config['username'])
                name_escaped = escape_markdown_v2(server_name)
                status_label_escaped = escape_markdown_v2(status_label)

                server_lines.append(
                    f"*{name_escaped}*{default_indicator} {status_label_escaped}\n"
                    f"   `{url_escaped}` \\- {username_escaped}"
                )

                # Add button for each server
                button_text = f"{server_name}"
                if server_name == default_server:
                    button_text += " ⭐️"
                server_buttons.append([
                    InlineKeyboardButton(button_text, callback_data=f"api_server_view_{server_name}")
                ])

            message_text = (
                "🔌 *API Servers*\n\n"
                + "\n\n".join(server_lines)
            )

            keyboard = server_buttons + [
                [InlineKeyboardButton("➕ Add Server", callback_data="api_server_add")],
                [InlineKeyboardButton("🔄 Refresh", callback_data="config_api_servers")],
                [InlineKeyboardButton("« Back", callback_data="config_back")],
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Only edit if content changed to avoid Telegram error
        try:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except Exception as e:
            if "Message is not modified" in str(e):
                # Message content hasn't changed, just answer the callback
                await query.answer("✅ Already up to date")
            else:
                raise

    except Exception as e:
        logger.error(f"Error showing API servers: {e}", exc_info=True)
        error_text = f"❌ Error loading API servers: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="config_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def show_connect_keys(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show connect keys configuration
    """
    try:
        from servers import server_manager

        # Get first server to check accounts
        servers = server_manager.list_servers()

        if not servers:
            message_text = (
                "🔑 *Connect Keys*\n\n"
                "No API servers configured\\.\n\n"
                "_Add servers in `servers.yml` first\\._"
            )
            keyboard = [[InlineKeyboardButton("« Back", callback_data="config_back")]]
        else:
            # Get client from first server
            server_name = list(servers.keys())[0]
            client = await server_manager.get_client(server_name)
            accounts = await client.accounts.list_accounts()

            if not accounts:
                message_text = (
                    "🔑 *Connect Keys*\n\n"
                    "No accounts configured\\.\n\n"
                    "_Configure exchange credentials in Hummingbot\\._"
                )
                keyboard = [[InlineKeyboardButton("« Back", callback_data="config_back")]]
            else:
                # Build accounts list
                account_lines = []
                for account in accounts:
                    account_name = escape_markdown_v2(account.get('name', 'Unknown'))
                    account_lines.append(f"• *{account_name}*")

                message_text = (
                    "🔑 *Connect Keys*\n\n"
                    + "\n".join(account_lines) + "\n\n"
                    f"_Connected to: {escape_markdown_v2(server_name)}_"
                )

                keyboard = [
                    [InlineKeyboardButton("🔄 Refresh", callback_data="config_connect_keys")],
                    [InlineKeyboardButton("« Back", callback_data="config_back")],
                ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Only edit if content changed to avoid Telegram error
        try:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except Exception as e:
            if "Message is not modified" in str(e):
                # Message content hasn't changed, just answer the callback
                await query.answer("✅ Already up to date")
            else:
                raise

    except Exception as e:
        logger.error(f"Error showing connect keys: {e}", exc_info=True)
        error_text = f"❌ Error loading connect keys: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="config_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def handle_api_server_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle API server specific actions
    """
    action_data = query.data.replace("api_server_", "")

    if action_data == "add":
        await start_add_server(query, context)
    elif action_data.startswith("view_"):
        server_name = action_data.replace("view_", "")
        await show_server_details(query, context, server_name)
    elif action_data.startswith("set_default_"):
        server_name = action_data.replace("set_default_", "")
        await set_default_server(query, context, server_name)
    elif action_data.startswith("modify_"):
        server_name = action_data.replace("modify_", "")
        await start_modify_server(query, context, server_name)
    elif action_data.startswith("delete_"):
        server_name = action_data.replace("delete_", "")
        await confirm_delete_server(query, context, server_name)
    elif action_data.startswith("delete_confirm_"):
        server_name = action_data.replace("delete_confirm_", "")
        await delete_server(query, context, server_name)
    elif action_data == "cancel_delete":
        await show_api_servers(query, context)
    else:
        await query.answer("Unknown action")


async def show_server_details(query, context: ContextTypes.DEFAULT_TYPE, server_name: str) -> None:
    """
    Show details and actions for a specific server
    """
    try:
        from servers import server_manager

        server = server_manager.get_server(server_name)
        if not server:
            await query.answer("❌ Server not found")
            return

        default_server = server_manager.get_default_server()
        is_default = server_name == default_server

        # Check status
        status_result = await server_manager.check_server_status(server_name)
        status = status_result["status"]
        message = status_result.get("message", "")

        if status == "online":
            status_text = "*\\[Online\\]*"
        elif status == "auth_error":
            status_text = f"*\\[Auth Error\\]*\n_{escape_markdown_v2(message)}_"
        elif status == "offline":
            status_text = f"*\\[Offline\\]*\n_{escape_markdown_v2(message)}_"
        else:
            status_text = f"*\\[Error\\]*\n_{escape_markdown_v2(message)}_"

        name_escaped = escape_markdown_v2(server_name)
        host_escaped = escape_markdown_v2(server['host'])
        port_escaped = escape_markdown_v2(str(server['port']))
        username_escaped = escape_markdown_v2(server['username'])

        message_text = (
            f"🔌 *Server: {name_escaped}*\n\n"
            f"*Status:* {status_text}\n"
            f"*Host:* `{host_escaped}`\n"
            f"*Port:* `{port_escaped}`\n"
            f"*Username:* `{username_escaped}`\n"
        )

        if is_default:
            message_text += "\n⭐️ _This is the default server_"

        keyboard = []

        if not is_default:
            keyboard.append([InlineKeyboardButton("⭐️ Set as Default", callback_data=f"api_server_set_default_{server_name}")])

        keyboard.extend([
            [InlineKeyboardButton("✏️ Modify", callback_data=f"api_server_modify_{server_name}")],
            [InlineKeyboardButton("🗑 Delete", callback_data=f"api_server_delete_{server_name}")],
            [InlineKeyboardButton("« Back to Servers", callback_data="config_api_servers")],
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing server details: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)}")


async def set_default_server(query, context: ContextTypes.DEFAULT_TYPE, server_name: str) -> None:
    """
    Set a server as the default
    """
    try:
        from servers import server_manager

        success = server_manager.set_default_server(server_name)

        if success:
            await query.answer(f"✅ Set {server_name} as default")
            await show_server_details(query, context, server_name)
        else:
            await query.answer("❌ Failed to set default server")

    except Exception as e:
        logger.error(f"Error setting default server: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)}")


async def confirm_delete_server(query, context: ContextTypes.DEFAULT_TYPE, server_name: str) -> None:
    """
    Ask for confirmation before deleting a server
    """
    name_escaped = escape_markdown_v2(server_name)
    message_text = (
        f"⚠️ *Delete Server*\n\n"
        f"Are you sure you want to delete *{name_escaped}*?\n\n"
        f"_This action cannot be undone\\._"
    )

    keyboard = [
        [InlineKeyboardButton("✅ Yes, Delete", callback_data=f"api_server_delete_confirm_{server_name}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="api_server_cancel_delete")],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def delete_server(query, context: ContextTypes.DEFAULT_TYPE, server_name: str) -> None:
    """
    Delete a server from configuration
    """
    try:
        from servers import server_manager

        success = server_manager.delete_server(server_name)

        if success:
            await query.answer(f"✅ Deleted {server_name}")
            await show_api_servers(query, context)
        else:
            await query.answer("❌ Failed to delete server")

    except Exception as e:
        logger.error(f"Error deleting server: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)}")


async def start_add_server(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Start the add server conversation
    """
    await query.answer("Feature coming soon - please add servers manually to servers.yml for now")


async def start_modify_server(query, context: ContextTypes.DEFAULT_TYPE, server_name: str) -> None:
    """
    Start the modify server conversation
    """
    await query.answer("Feature coming soon - please modify servers manually in servers.yml for now")


async def handle_connect_key_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle connect key specific actions
    """
    # Future: Add actions like remove credential, etc.
    await query.answer("Not implemented yet")


# Create callback handler instance for registration
def get_config_callback_handler():
    """Get the callback query handler for config menu"""
    return CallbackQueryHandler(config_callback_handler, pattern="^config_")
