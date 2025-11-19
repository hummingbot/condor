"""
Configuration management command handlers
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, ConversationHandler, MessageHandler, filters

from utils.auth import restricted
from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)

# Conversation states for adding/modifying servers and API keys
(ADD_SERVER_NAME, ADD_SERVER_HOST, ADD_SERVER_PORT,
 ADD_SERVER_USERNAME, ADD_SERVER_PASSWORD, ADD_SERVER_CONFIRM,
 MODIFY_SERVER_FIELD_CHOICE, MODIFY_SERVER_VALUE,
 CONFIG_API_KEY_FIELD) = range(9)


def clear_config_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Clear all config-related state from user context.
    Call this when starting other commands to prevent state pollution.
    """
    context.user_data.pop('modifying_server', None)
    context.user_data.pop('modifying_field', None)
    context.user_data.pop('awaiting_modify_input', None)
    context.user_data.pop('adding_server', None)
    context.user_data.pop('awaiting_add_server_input', None)
    context.user_data.pop('configuring_api_key', None)
    context.user_data.pop('awaiting_api_key_input', None)
    context.user_data.pop('api_key_config_data', None)


def _get_config_menu_markup_and_text():
    """
    Build the main config menu keyboard and message text
    """
    keyboard = [
        [
            InlineKeyboardButton("🔌 API Servers", callback_data="config_api_servers"),
            InlineKeyboardButton("🔑 API Keys", callback_data="config_api_keys"),
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
        "🔑 *API Keys* \\- Manage exchange credentials"
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
    elif query.data == "config_api_keys":
        await show_api_keys(query, context)
    elif query.data == "config_close":
        await query.message.delete()
    elif query.data == "config_back":
        await show_config_menu(query, context)
    elif query.data.startswith("modify_field_"):
        await handle_modify_field_selection(query, context)
    elif query.data.startswith("add_server_"):
        await handle_add_server_callbacks(query, context)
    elif query.data.startswith("api_server_"):
        await handle_api_server_action(query, context)
    elif query.data.startswith("api_key_"):
        await handle_api_key_action(query, context)


async def show_api_servers(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show API servers configuration with status and actions
    """
    try:
        from servers import server_manager

        # Reload configuration from servers.yml to pick up any manual changes
        await server_manager.reload_config()

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

                # Choose status icon and detail message
                if status_result["status"] == "online":
                    status_icon = "🟢"
                    status_detail = ""  # No status text when online
                elif status_result["status"] == "auth_error":
                    status_icon = "🔴"
                    error_msg = escape_markdown_v2(status_result.get("message", "Auth Error"))
                    status_detail = f" \\[{error_msg}\\]"  # Error message on same line as URL
                elif status_result["status"] == "offline":
                    status_icon = "🔴"
                    error_msg = escape_markdown_v2(status_result.get("message", "Offline"))
                    status_detail = f" \\[{error_msg}\\]"  # Error message on same line as URL
                else:
                    status_icon = "🟡"
                    error_msg = escape_markdown_v2(status_result.get("message", "Error"))
                    status_detail = f" \\[{error_msg}\\]"  # Error message on same line as URL

                # Default server indicator
                default_indicator = " ⭐️" if server_name == default_server else ""

                url = f"{server_config['host']}:{server_config['port']}"
                url_escaped = escape_markdown_v2(url)
                name_escaped = escape_markdown_v2(server_name)

                # Format: 🟢 remote ⭐️
                #         212.85.15.60:8000
                # Or if offline:
                # 🔴 local [Cannot reach server]
                #    localhost:8000
                server_lines.append(
                    f"{status_icon} *{name_escaped}*{default_indicator}{status_detail}\n"
                    f"   `{url_escaped}`"
                )

                # Add button for each server
                button_text = f"{server_name}"
                if server_name == default_server:
                    button_text += " ⭐️"
                server_buttons.append(
                    InlineKeyboardButton(button_text, callback_data=f"api_server_view_{server_name}")
                )

            message_text = (
                "🔌 *API Servers*\n\n"
                + "\n\n".join(server_lines) + "\n\n"
                "_Click on a server name to view details and modify settings\\._"
            )

            # Organize server buttons in rows of max 4 columns
            server_button_rows = []
            for i in range(0, len(server_buttons), 4):
                server_button_rows.append(server_buttons[i:i+4])

            keyboard = server_button_rows + [
                [
                    InlineKeyboardButton("➕ Add Server", callback_data="api_server_add"),
                    InlineKeyboardButton("🔄 Refresh", callback_data="config_api_servers"),
                    InlineKeyboardButton("« Back", callback_data="config_back")
                ]
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


async def show_api_keys(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show API keys configuration with account selection
    """
    try:
        from servers import server_manager

        # Get default server
        servers = server_manager.list_servers()

        if not servers:
            message_text = (
                "🔑 *API Keys*\n\n"
                "No API servers configured\\.\n\n"
                "_Add servers in API Servers first\\._"
            )
            keyboard = [[InlineKeyboardButton("« Back", callback_data="config_back")]]
        else:
            # Get client from default server
            client = await server_manager.get_default_client()
            accounts = await client.accounts.list_accounts()

            if not accounts:
                message_text = (
                    "🔑 *API Keys*\n\n"
                    "No accounts configured\\.\n\n"
                    "_Create accounts in Hummingbot first\\._"
                )
                keyboard = [[InlineKeyboardButton("« Back", callback_data="config_back")]]
            else:
                # Build account list with credentials info
                account_lines = []
                for account in accounts:
                    account_name = str(account)
                    # Get credentials for this account
                    try:
                        credentials = await client.accounts.list_account_credentials(account_name=account_name)
                        cred_count = len(credentials) if credentials else 0

                        account_escaped = escape_markdown_v2(account_name)
                        if cred_count > 0:
                            creds_text = escape_markdown_v2(", ".join(credentials))
                            account_lines.append(f"• *{account_escaped}* \\({cred_count} connected\\)\n  _{creds_text}_")
                        else:
                            account_lines.append(f"• *{account_escaped}* \\(no credentials\\)")
                    except Exception as e:
                        logger.warning(f"Failed to get credentials for {account_name}: {e}")
                        account_escaped = escape_markdown_v2(account_name)
                        account_lines.append(f"• *{account_escaped}*")

                message_text = (
                    "🔑 *API Keys*\n\n"
                    + "\n".join(account_lines) + "\n\n"
                    "_Select an account to manage exchange credentials:_"
                )

                # Create account buttons in grid of 4 per row
                # Use base64 encoding to avoid issues with special characters in account names
                import base64
                account_buttons = []
                for account in accounts:
                    account_name = str(account)
                    # Encode account name to avoid issues with underscores and special chars
                    encoded_name = base64.b64encode(account_name.encode()).decode()
                    account_buttons.append(
                        InlineKeyboardButton(account_name, callback_data=f"api_key_account:{encoded_name}")
                    )

                # Organize into rows of max 4 columns
                account_button_rows = []
                for i in range(0, len(account_buttons), 4):
                    account_button_rows.append(account_buttons[i:i+4])

                keyboard = account_button_rows + [
                    [InlineKeyboardButton("« Back", callback_data="config_back")]
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
        logger.error(f"Error showing API keys: {e}", exc_info=True)
        error_text = f"❌ Error loading API keys: {escape_markdown_v2(str(e))}"
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
    elif action_data.startswith("delete_confirm_"):
        # Handle confirmation first (before the generic delete_)
        server_name = action_data.replace("delete_confirm_", "")
        await delete_server(query, context, server_name)
    elif action_data.startswith("delete_"):
        server_name = action_data.replace("delete_", "")
        await confirm_delete_server(query, context, server_name)
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

        # Clear any modify state when showing server details
        context.user_data.pop('modifying_server', None)
        context.user_data.pop('modifying_field', None)
        context.user_data.pop('awaiting_modify_input', None)

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

        message_text += "\n\n_You can modify or delete this server using the buttons below\\._"

        keyboard = []

        if not is_default:
            keyboard.append([InlineKeyboardButton("⭐️ Set as Default", callback_data=f"api_server_set_default_{server_name}")])

        # Add modification buttons in a row with 4 columns
        keyboard.append([
            InlineKeyboardButton("🌐 Host", callback_data=f"modify_field_host_{server_name}"),
            InlineKeyboardButton("🔌 Port", callback_data=f"modify_field_port_{server_name}"),
            InlineKeyboardButton("👤 User", callback_data=f"modify_field_username_{server_name}"),
            InlineKeyboardButton("🔑 Pass", callback_data=f"modify_field_password_{server_name}"),
        ])

        keyboard.extend([
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


def _build_add_server_message(server_data: dict, current_field: str) -> tuple:
    """
    Build the progressive add server message showing filled fields and current prompt
    Returns (message_text, reply_markup)
    """
    # Define field order and defaults
    fields = {
        'name': {'label': 'Name', 'default': None},
        'host': {'label': 'Host', 'default': 'localhost'},
        'port': {'label': 'Port', 'default': '8000'},
        'username': {'label': 'Username', 'default': 'admin'},
        'password': {'label': 'Password', 'default': 'admin'}
    }

    field_order = ['name', 'host', 'port', 'username', 'password']

    # Build the message showing progress
    lines = ["➕ *Add New Server*\n"]

    for field_key, field_info in fields.items():
        if field_key in server_data:
            # Field already filled - show value
            value = server_data[field_key]
            if field_key == 'password':
                value = '****'
            label = escape_markdown_v2(field_info['label'])
            value_escaped = escape_markdown_v2(str(value))
            lines.append(f"*{label}:* `{value_escaped}` ✅")
        elif field_key == current_field:
            # Current field being filled
            label = escape_markdown_v2(field_info['label'])
            lines.append(f"*{label}:* _\\(awaiting input\\)_")
            break
        else:
            # Future field - show placeholder
            label = escape_markdown_v2(field_info['label'])
            lines.append(f"*{label}:* \\_\\_\\_")

    message_text = "\n".join(lines)

    # Build keyboard with default, back, and cancel buttons in same row
    buttons = []

    # Add default button if available
    if current_field in fields and fields[current_field]['default']:
        default_value = fields[current_field]['default']
        buttons.append(InlineKeyboardButton(
            f"Default: {default_value}",
            callback_data=f"add_server_default_{current_field}"
        ))

    # Add back button if not on first field
    current_index = field_order.index(current_field)
    if current_index > 0:
        buttons.append(InlineKeyboardButton("« Back", callback_data="add_server_back"))

    # Always add cancel button
    buttons.append(InlineKeyboardButton("❌ Cancel", callback_data="config_api_servers"))

    keyboard = [buttons]
    reply_markup = InlineKeyboardMarkup(keyboard)

    return message_text, reply_markup


async def start_add_server(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Start the add server conversation - prompt for server name
    """
    # Initialize context storage for new server data
    context.user_data['adding_server'] = {}
    context.user_data['awaiting_add_server_input'] = 'name'
    context.user_data['add_server_message_id'] = query.message.message_id
    context.user_data['add_server_chat_id'] = query.message.chat_id

    message_text, reply_markup = _build_add_server_message({}, 'name')

    # Edit the message to start the flow
    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )
    await query.answer()


async def _update_add_server_message(context: ContextTypes.DEFAULT_TYPE, bot) -> None:
    """
    Update the add server message with current progress
    """
    server_data = context.user_data.get('adding_server', {})
    current_field = context.user_data.get('awaiting_add_server_input')
    message_id = context.user_data.get('add_server_message_id')
    chat_id = context.user_data.get('add_server_chat_id')

    if not message_id or not chat_id or not current_field:
        return

    message_text, reply_markup = _build_add_server_message(server_data, current_field)

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error updating add server message: {e}")


async def show_add_server_confirmation(context: ContextTypes.DEFAULT_TYPE, bot, chat_id: int) -> None:
    """
    Show confirmation screen with all values and option to modify
    Uses the same message, just updates it to show confirmation
    """
    server_data = context.user_data.get('adding_server', {})
    message_id = context.user_data.get('add_server_message_id')

    name_escaped = escape_markdown_v2(server_data.get('name', 'N/A'))
    host_escaped = escape_markdown_v2(server_data.get('host', 'N/A'))
    port_escaped = escape_markdown_v2(str(server_data.get('port', 'N/A')))
    username_escaped = escape_markdown_v2(server_data.get('username', 'N/A'))

    message_text = (
        "✅ *Confirm New Server*\n\n"
        f"*Name:* `{name_escaped}`\n"
        f"*Host:* `{host_escaped}`\n"
        f"*Port:* `{port_escaped}`\n"
        f"*Username:* `{username_escaped}`\n"
        f"*Password:* `****`\n\n"
        "Click a field to modify it or confirm to add the server:"
    )

    # Modification buttons in same row (5 fields)
    keyboard = [
        [
            InlineKeyboardButton("📝 Name", callback_data="add_server_modify_name"),
            InlineKeyboardButton("🌐 Host", callback_data="add_server_modify_host"),
            InlineKeyboardButton("🔌 Port", callback_data="add_server_modify_port"),
            InlineKeyboardButton("👤 User", callback_data="add_server_modify_username"),
            InlineKeyboardButton("🔑 Pass", callback_data="add_server_modify_password"),
        ],
        [
            InlineKeyboardButton("✅ Confirm & Add", callback_data="add_server_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="config_api_servers")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error showing confirmation: {e}")


async def handle_add_server_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle text input during add server flow
    """
    # Only process if we're awaiting add server input
    awaiting_field = context.user_data.get('awaiting_add_server_input')
    if not awaiting_field:
        return

    # Store chat_id if not already stored
    if 'add_server_chat_id' not in context.user_data:
        context.user_data['add_server_chat_id'] = update.effective_chat.id

    # Delete the user's input message to keep chat clean
    try:
        await update.message.delete()
    except:
        pass

    try:
        new_value = update.message.text.strip()
        server_data = context.user_data.get('adding_server', {})

        # Check if this is initial flow or modification from confirmation screen
        is_initial_flow = awaiting_field in ['name', 'host', 'port', 'username', 'password'] and \
                          awaiting_field not in server_data

        # Validate and store the value
        if awaiting_field == 'name':
            # Check if server already exists (skip check if modifying from confirmation)
            from servers import server_manager
            if new_value in server_manager.list_servers() and new_value != server_data.get('name'):
                # Show error in the same message temporarily
                message_id = context.user_data.get('add_server_message_id')
                chat_id = context.user_data.get('add_server_chat_id')
                if message_id and chat_id:
                    error_text = f"❌ A server named `{escape_markdown_v2(new_value)}` already exists\\. Please choose a different name\\."
                    message_text, reply_markup = _build_add_server_message(server_data, awaiting_field)
                    message_text = error_text + "\n\n" + message_text
                    await update.get_bot().edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=message_text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                return  # Don't advance, wait for valid input

            server_data['name'] = new_value
            context.user_data['adding_server'] = server_data

            # Only proceed to next field if this is initial flow
            if is_initial_flow:
                context.user_data['awaiting_add_server_input'] = 'host'
                await _update_add_server_message(context, update.get_bot())
            else:
                # Return to confirmation screen
                context.user_data['awaiting_add_server_input'] = None
                await show_add_server_confirmation(context, update.get_bot(), update.effective_chat.id)

        elif awaiting_field == 'host':
            server_data['host'] = new_value
            context.user_data['adding_server'] = server_data

            if is_initial_flow:
                context.user_data['awaiting_add_server_input'] = 'port'
                await _update_add_server_message(context, update.get_bot())
            else:
                context.user_data['awaiting_add_server_input'] = None
                await show_add_server_confirmation(context, update.get_bot(), update.effective_chat.id)

        elif awaiting_field == 'port':
            try:
                port_value = int(new_value)
                if port_value < 1 or port_value > 65535:
                    # Show error in the same message
                    message_id = context.user_data.get('add_server_message_id')
                    chat_id = context.user_data.get('add_server_chat_id')
                    if message_id and chat_id:
                        error_text = "❌ Port must be between 1 and 65535\\. Please try again\\."
                        message_text, reply_markup = _build_add_server_message(server_data, awaiting_field)
                        message_text = error_text + "\n\n" + message_text
                        await update.get_bot().edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=message_text,
                            parse_mode="MarkdownV2",
                            reply_markup=reply_markup
                        )
                    return
                server_data['port'] = port_value
                context.user_data['adding_server'] = server_data

                if is_initial_flow:
                    context.user_data['awaiting_add_server_input'] = 'username'
                    await _update_add_server_message(context, update.get_bot())
                else:
                    context.user_data['awaiting_add_server_input'] = None
                    await show_add_server_confirmation(context, update.get_bot(), update.effective_chat.id)
            except ValueError:
                # Show error in the same message
                message_id = context.user_data.get('add_server_message_id')
                chat_id = context.user_data.get('add_server_chat_id')
                if message_id and chat_id:
                    error_text = "❌ Port must be a number\\. Please try again\\."
                    message_text, reply_markup = _build_add_server_message(server_data, awaiting_field)
                    message_text = error_text + "\n\n" + message_text
                    await update.get_bot().edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=message_text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                return

        elif awaiting_field == 'username':
            server_data['username'] = new_value
            context.user_data['adding_server'] = server_data

            if is_initial_flow:
                context.user_data['awaiting_add_server_input'] = 'password'
                await _update_add_server_message(context, update.get_bot())
            else:
                context.user_data['awaiting_add_server_input'] = None
                await show_add_server_confirmation(context, update.get_bot(), update.effective_chat.id)

        elif awaiting_field == 'password':
            server_data['password'] = new_value
            context.user_data['adding_server'] = server_data
            context.user_data['awaiting_add_server_input'] = None
            await show_add_server_confirmation(context, update.get_bot(), update.effective_chat.id)

    except Exception as e:
        logger.error(f"Error handling add server input: {e}", exc_info=True)
        context.user_data.pop('awaiting_add_server_input', None)
        context.user_data.pop('adding_server', None)


async def handle_add_server_callbacks(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle callback queries during add server flow (defaults and modifications)
    """
    try:
        # Handle back button
        if query.data == "add_server_back":
            field_order = ['name', 'host', 'port', 'username', 'password']
            current_field = context.user_data.get('awaiting_add_server_input')

            if current_field and current_field in field_order:
                current_index = field_order.index(current_field)
                if current_index > 0:
                    # Go to previous field
                    previous_field = field_order[current_index - 1]

                    # Remove the current field's value from server_data
                    server_data = context.user_data.get('adding_server', {})
                    server_data.pop(previous_field, None)  # Remove previous field to re-enter it
                    context.user_data['adding_server'] = server_data

                    # Update awaiting field
                    context.user_data['awaiting_add_server_input'] = previous_field
                    await query.answer("« Going back")
                    await _update_add_server_message(context, query.message.get_bot())
            return

        # Handle default value buttons
        if query.data.startswith("add_server_default_"):
            field = query.data.replace("add_server_default_", "")
            server_data = context.user_data.get('adding_server', {})

            # Define defaults
            defaults = {
                'host': 'localhost',
                'port': 8000,
                'username': 'admin',
                'password': 'admin'
            }

            # Set the default value
            server_data[field] = defaults[field]
            context.user_data['adding_server'] = server_data

            # Move to next field
            field_order = ['name', 'host', 'port', 'username', 'password']
            current_index = field_order.index(field)

            if current_index < len(field_order) - 1:
                # Move to next field
                context.user_data['awaiting_add_server_input'] = field_order[current_index + 1]
                await query.answer(f"✅ Using default: {defaults[field]}")
                await _update_add_server_message(context, query.message.get_bot())
            else:
                # Last field - show confirmation
                context.user_data['awaiting_add_server_input'] = None
                await query.answer(f"✅ Using default: {defaults[field]}")
                await show_add_server_confirmation(context, query.message.get_bot(), query.message.chat_id)

        elif query.data.startswith("add_server_modify_"):
            # Modification from confirmation screen
            field = query.data.replace("add_server_modify_", "")
            context.user_data['awaiting_add_server_input'] = field

            field_names = {
                "name": "Name",
                "host": "Host",
                "port": "Port",
                "username": "Username",
                "password": "Password"
            }

            server_data = context.user_data.get('adding_server', {})
            current_value = server_data.get(field, 'N/A')
            if field == 'password':
                current_value = '****'

            field_name = field_names.get(field, field)
            field_escaped = escape_markdown_v2(field_name)
            current_escaped = escape_markdown_v2(str(current_value))

            message_text = (
                f"✏️ *Modify {field_escaped}*\n\n"
                f"Current value: `{current_escaped}`\n\n"
                f"Please send the new value for *{field_escaped}*:"
            )

            # Add back button to return to confirmation screen
            keyboard = [[InlineKeyboardButton("« Back", callback_data="add_server_back_to_confirm")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            await query.answer()

        elif query.data == "add_server_confirm":
            await confirm_add_server(query, context)

        elif query.data == "add_server_back_to_confirm":
            # Clear awaiting input state and return to confirmation screen
            context.user_data['awaiting_add_server_input'] = None
            await query.answer()
            await show_add_server_confirmation(context, query.message.get_bot(), query.message.chat_id)

    except Exception as e:
        logger.error(f"Error handling add server callback: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)}")


async def confirm_add_server(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Actually add the server to configuration
    """
    try:
        from servers import server_manager

        server_data = context.user_data.get('adding_server', {})

        # Validate we have all required fields
        required_fields = ['name', 'host', 'port', 'username', 'password']
        for field in required_fields:
            if field not in server_data:
                await query.answer(f"❌ Missing field: {field}")
                return

        # Add the server
        success = server_manager.add_server(
            name=server_data['name'],
            host=server_data['host'],
            port=server_data['port'],
            username=server_data['username'],
            password=server_data['password']
        )

        if success:
            await query.answer(f"✅ Added server '{server_data['name']}'")
            # Clear context data
            context.user_data.pop('adding_server', None)
            context.user_data.pop('awaiting_add_server_input', None)
            # Show updated server list
            await show_api_servers(query, context)
        else:
            await query.answer("❌ Failed to add server (may already exist)")

    except Exception as e:
        logger.error(f"Error confirming add server: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)}")


async def start_modify_server(query, context: ContextTypes.DEFAULT_TYPE, server_name: str) -> int:
    """
    Start the modify server conversation - show field selection menu
    """
    try:
        from servers import server_manager

        server = server_manager.get_server(server_name)
        if not server:
            await query.answer("❌ Server not found")
            return ConversationHandler.END

        # Store server name in context for later use
        context.user_data['modifying_server'] = server_name

        name_escaped = escape_markdown_v2(server_name)
        message_text = (
            f"✏️ *Modify Server: {name_escaped}*\n\n"
            f"Select which field you want to modify:"
        )

        keyboard = [
            [InlineKeyboardButton("🌐 Host", callback_data=f"modify_field_host_{server_name}")],
            [InlineKeyboardButton("🔌 Port", callback_data=f"modify_field_port_{server_name}")],
            [InlineKeyboardButton("👤 Username", callback_data=f"modify_field_username_{server_name}")],
            [InlineKeyboardButton("🔑 Password", callback_data=f"modify_field_password_{server_name}")],
            [InlineKeyboardButton("« Back", callback_data=f"api_server_view_{server_name}")],
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

        return MODIFY_SERVER_FIELD_CHOICE

    except Exception as e:
        logger.error(f"Error starting server modification: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)}")
        return ConversationHandler.END


async def handle_modify_field_selection(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle field selection for server modification
    """
    try:
        # Parse the callback data: modify_field_{field}_{server_name}
        parts = query.data.replace("modify_field_", "").split("_", 1)
        if len(parts) != 2:
            await query.answer("❌ Invalid field selection")
            return

        field, server_name = parts

        # Store the field and server name in context for the message handler
        context.user_data['modifying_server'] = server_name
        context.user_data['modifying_field'] = field
        context.user_data['awaiting_modify_input'] = True
        context.user_data['modify_message_id'] = query.message.message_id
        context.user_data['modify_chat_id'] = query.message.chat_id

        # Get current value
        from servers import server_manager
        server = server_manager.get_server(server_name)
        if not server:
            await query.answer("❌ Server not found")
            return

        current_value = server.get(field, "N/A")
        if field == "password":
            current_value = "****"  # Don't show password

        field_names = {
            "host": "Host",
            "port": "Port",
            "username": "Username",
            "password": "Password"
        }

        field_name = field_names.get(field, field)
        name_escaped = escape_markdown_v2(server_name)
        field_escaped = escape_markdown_v2(field_name)
        current_escaped = escape_markdown_v2(str(current_value))

        message_text = (
            f"✏️ *Modify {field_escaped}*\n\n"
            f"Server: *{name_escaped}*\n"
            f"Current {field_escaped}: `{current_escaped}`\n\n"
            f"Please send the new value for *{field_escaped}*:"
        )

        # Add back button to allow cancellation
        keyboard = [[InlineKeyboardButton("« Back", callback_data=f"api_server_view_{server_name}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error handling field selection: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)}")


async def _update_api_key_config_message(context: ContextTypes.DEFAULT_TYPE, bot) -> None:
    """
    Update the API key configuration message with current progress
    """
    config_data = context.user_data.get('api_key_config_data', {})
    current_field = context.user_data.get('awaiting_api_key_input')
    message_id = context.user_data.get('api_key_message_id')
    chat_id = context.user_data.get('api_key_chat_id')

    if not message_id or not chat_id or not current_field:
        return

    all_fields = config_data.get('fields', [])
    message_text, reply_markup = _build_api_key_config_message(config_data, current_field, all_fields)

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error updating API key config message: {e}")


async def handle_api_key_config_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle text input during API key configuration flow
    """
    # Only process if we're awaiting API key input
    awaiting_field = context.user_data.get('awaiting_api_key_input')
    if not awaiting_field:
        return

    # Store chat_id if not already stored
    if 'api_key_chat_id' not in context.user_data:
        context.user_data['api_key_chat_id'] = update.effective_chat.id

    # Delete the user's input message to keep chat clean (especially for secrets)
    try:
        await update.message.delete()
    except:
        pass

    try:
        new_value = update.message.text.strip()
        config_data = context.user_data.get('api_key_config_data', {})
        values = config_data.get('values', {})
        all_fields = config_data.get('fields', [])

        # Store the value
        values[awaiting_field] = new_value
        config_data['values'] = values
        context.user_data['api_key_config_data'] = config_data

        # Move to next field or show confirmation
        current_index = all_fields.index(awaiting_field)

        if current_index < len(all_fields) - 1:
            # Move to next field
            context.user_data['awaiting_api_key_input'] = all_fields[current_index + 1]
            await _update_api_key_config_message(context, update.get_bot())
        else:
            # All fields filled - submit configuration
            context.user_data['awaiting_api_key_input'] = None
            await submit_api_key_config(context, update.get_bot(), update.effective_chat.id)

    except Exception as e:
        logger.error(f"Error handling API key config input: {e}", exc_info=True)
        context.user_data.pop('awaiting_api_key_input', None)
        context.user_data.pop('configuring_api_key', None)
        context.user_data.pop('api_key_config_data', None)


async def submit_api_key_config(context: ContextTypes.DEFAULT_TYPE, bot, chat_id: int) -> None:
    """
    Submit the API key configuration to Hummingbot
    """
    try:
        from servers import server_manager
        import base64

        config_data = context.user_data.get('api_key_config_data', {})
        account_name = config_data.get('account_name')
        connector_name = config_data.get('connector_name')
        values = config_data.get('values', {})
        message_id = context.user_data.get('api_key_message_id')

        if not account_name or not connector_name or not values:
            await bot.send_message(chat_id=chat_id, text="❌ Missing configuration data")
            return

        client = await server_manager.get_default_client()

        # Add credentials using the accounts API
        await client.accounts.add_credential(
            account_name=account_name,
            connector_name=connector_name,
            credentials=values
        )

        # Clear context data
        context.user_data.pop('configuring_api_key', None)
        context.user_data.pop('awaiting_api_key_input', None)
        context.user_data.pop('api_key_config_data', None)
        context.user_data.pop('api_key_message_id', None)
        context.user_data.pop('api_key_chat_id', None)

        # Show success message and return to account view
        account_escaped = escape_markdown_v2(account_name)
        connector_escaped = escape_markdown_v2(connector_name)
        message_text = (
            f"✅ *Configuration Saved*\n\n"
            f"*{connector_escaped}* has been configured for account *{account_escaped}*\\."
        )

        encoded_account = base64.b64encode(account_name.encode()).decode()
        keyboard = [[InlineKeyboardButton("« Back to Account", callback_data=f"api_key_back_account:{encoded_account}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if message_id and chat_id:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=message_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=message_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error submitting API key config: {e}", exc_info=True)
        error_text = f"❌ Error saving configuration: {escape_markdown_v2(str(e))}"
        await bot.send_message(chat_id=chat_id, text=error_text, parse_mode="MarkdownV2")


async def handle_modify_value_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle text input for server field modification, add server flow, and API key configuration
    """
    # IMMEDIATELY return if no config state exists - this prevents interference with other commands
    if not context.user_data.get('awaiting_add_server_input') and \
       not context.user_data.get('awaiting_modify_input') and \
       not context.user_data.get('awaiting_api_key_input'):
        return

    # Check if we're in API key configuration flow
    if context.user_data.get('awaiting_api_key_input'):
        await handle_api_key_config_input(update, context)
        return

    # Check if we're in add server flow
    if context.user_data.get('awaiting_add_server_input'):
        await handle_add_server_input(update, context)
        return

    # Only process if we're awaiting modify input
    if not context.user_data.get('awaiting_modify_input'):
        return

    # Store chat_id and message info BEFORE deleting the message
    chat_id = update.message.chat_id
    server_name = context.user_data.get('modifying_server')
    field = context.user_data.get('modifying_field')
    new_value = update.message.text.strip()

    # Get stored message info for editing the modification page
    modify_message_id = context.user_data.get('modify_message_id')
    modify_chat_id = context.user_data.get('modify_chat_id')

    # Delete the user's input message immediately to keep chat clean (especially for passwords)
    try:
        await update.message.delete()
    except:
        pass

    try:
        from servers import server_manager

        if not server_name or not field:
            # Send error to chat since we can't reply to deleted message
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Session expired. Please start over with /config"
            )
            context.user_data.pop('awaiting_modify_input', None)
            return

        # Convert port to integer if needed
        if field == "port":
            try:
                new_value = int(new_value)
            except ValueError:
                # Edit the modification page to show error instead of sending new message
                if modify_message_id and modify_chat_id:
                    await context.bot.edit_message_text(
                        chat_id=modify_chat_id,
                        message_id=modify_message_id,
                        text=f"❌ Port must be a number\\. Please send a valid port number:",
                        parse_mode="MarkdownV2"
                    )
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text="❌ Port must be a number. Please try again:"
                    )
                return  # Don't clear the flag, wait for valid input

        # Update the server
        kwargs = {field: new_value}
        success = server_manager.modify_server(server_name, **kwargs)

        # Clear context data
        context.user_data.pop('modifying_server', None)
        context.user_data.pop('modifying_field', None)
        context.user_data.pop('awaiting_modify_input', None)
        context.user_data.pop('modify_message_id', None)
        context.user_data.pop('modify_chat_id', None)

        if success:
            # Edit the existing message to show server details
            # We need to create a fake query object to reuse show_server_details
            class FakeQuery:
                def __init__(self, msg_id, ch_id, bot):
                    async def edit_text_wrapper(text, parse_mode=None, reply_markup=None):
                        return await bot.edit_message_text(
                            chat_id=ch_id,
                            message_id=msg_id,
                            text=text,
                            parse_mode=parse_mode,
                            reply_markup=reply_markup
                        )

                    self.message = type('obj', (object,), {
                        'message_id': msg_id,
                        'chat_id': ch_id,
                        'edit_text': edit_text_wrapper
                    })()

                async def answer(self, text=""):
                    pass

            if modify_message_id and modify_chat_id:
                fake_query = FakeQuery(modify_message_id, modify_chat_id, context.bot)
                await show_server_details(fake_query, context, server_name)
            else:
                # Fallback: create new message if we don't have the message ID
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ Successfully updated {field} for server '{server_name}'"
                )
        else:
            if modify_message_id and modify_chat_id:
                # Edit the existing message to show error
                await context.bot.edit_message_text(
                    chat_id=modify_chat_id,
                    message_id=modify_message_id,
                    text=f"❌ Failed to update server '{escape_markdown_v2(server_name)}'\\.",
                    parse_mode="MarkdownV2"
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="❌ Failed to update server."
                )

    except Exception as e:
        logger.error(f"Error handling value input: {e}", exc_info=True)
        # Use bot.send_message since the original message was deleted
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Error: {str(e)}"
            )
        except:
            pass
        context.user_data.pop('awaiting_modify_input', None)


async def handle_api_key_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle API key specific actions
    """
    import base64

    action_data = query.data.replace("api_key_", "")

    if action_data.startswith("account:"):
        # Decode base64 encoded account name
        encoded_name = action_data.replace("account:", "")
        try:
            account_name = base64.b64decode(encoded_name.encode()).decode()
            await show_account_credentials(query, context, account_name)
        except Exception as e:
            logger.error(f"Failed to decode account name: {e}")
            await query.answer("❌ Invalid account name")
    elif action_data.startswith("connector:"):
        # Format: connector:{index}
        # Retrieve account and connector from context
        try:
            connector_index = int(action_data.replace("connector:", ""))
            account_name = context.user_data.get('api_key_current_account')
            connectors = context.user_data.get('api_key_connectors', [])

            if account_name and 0 <= connector_index < len(connectors):
                connector_name = connectors[connector_index]
                await show_connector_config(query, context, account_name, connector_name)
            else:
                logger.error(f"Invalid connector index or missing context data")
                await query.answer("❌ Session expired, please try again")
        except (ValueError, IndexError) as e:
            logger.error(f"Failed to parse connector index: {e}")
            await query.answer("❌ Invalid connector data")
    elif action_data == "config_back":
        # Handle back button during API key configuration
        config_data = context.user_data.get('api_key_config_data', {})
        all_fields = config_data.get('fields', [])
        current_field = context.user_data.get('awaiting_api_key_input')

        if current_field and current_field in all_fields:
            current_index = all_fields.index(current_field)
            if current_index > 0:
                # Go to previous field
                previous_field = all_fields[current_index - 1]

                # Remove the previous field's value to re-enter it
                values = config_data.get('values', {})
                values.pop(previous_field, None)
                config_data['values'] = values
                context.user_data['api_key_config_data'] = config_data

                # Update awaiting field
                context.user_data['awaiting_api_key_input'] = previous_field
                await query.answer("« Going back")
                await _update_api_key_config_message(context, query.message.get_bot())
        else:
            await query.answer("Cannot go back")
    elif action_data.startswith("back_account:"):
        # Back to specific account view - also clear API key config state
        context.user_data.pop('configuring_api_key', None)
        context.user_data.pop('awaiting_api_key_input', None)
        context.user_data.pop('api_key_config_data', None)

        encoded_name = action_data.replace("back_account:", "")
        try:
            account_name = base64.b64decode(encoded_name.encode()).decode()
            await show_account_credentials(query, context, account_name)
        except Exception as e:
            logger.error(f"Failed to decode account name: {e}")
            await query.answer("❌ Invalid account name")
    elif action_data == "back_to_accounts":
        await show_api_keys(query, context)
    elif action_data.startswith("delete_cred:"):
        # Handle credential deletion
        try:
            cred_index = int(action_data.replace("delete_cred:", ""))
            account_name = context.user_data.get('api_key_current_account')
            credentials = context.user_data.get('api_key_credentials', [])

            if account_name and 0 <= cred_index < len(credentials):
                connector_name = credentials[cred_index]
                # Show confirmation dialog
                await show_delete_credential_confirmation(query, context, account_name, connector_name)
            else:
                logger.error(f"Invalid credential index or missing context data")
                await query.answer("❌ Session expired, please try again")
        except (ValueError, IndexError) as e:
            logger.error(f"Failed to parse credential index: {e}")
            await query.answer("❌ Invalid credential data")
    elif action_data.startswith("delete_cred_confirm:"):
        # Confirm credential deletion
        try:
            cred_index = int(action_data.replace("delete_cred_confirm:", ""))
            account_name = context.user_data.get('api_key_current_account')
            credentials = context.user_data.get('api_key_credentials', [])

            if account_name and 0 <= cred_index < len(credentials):
                connector_name = credentials[cred_index]
                await delete_credential(query, context, account_name, connector_name)
            else:
                await query.answer("❌ Session expired, please try again")
        except Exception as e:
            logger.error(f"Failed to delete credential: {e}")
            await query.answer("❌ Failed to delete credential")
    elif action_data == "delete_cred_cancel":
        # Cancel credential deletion - go back to account view
        account_name = context.user_data.get('api_key_current_account')
        if account_name:
            await show_account_credentials(query, context, account_name)
        else:
            await show_api_keys(query, context)
    else:
        await query.answer("Unknown action")


async def show_delete_credential_confirmation(query, context: ContextTypes.DEFAULT_TYPE, account_name: str, connector_name: str) -> None:
    """
    Show confirmation dialog before deleting a credential
    """
    account_escaped = escape_markdown_v2(account_name)
    connector_escaped = escape_markdown_v2(connector_name)

    message_text = (
        f"🗑 *Delete Credential*\n\n"
        f"Account: *{account_escaped}*\n"
        f"Exchange: *{connector_escaped}*\n\n"
        f"⚠️ This will remove the API credentials for *{connector_escaped}* from account *{account_escaped}*\\.\n\n"
        "Are you sure you want to delete this credential?"
    )

    # Find the index of the connector in the credentials list
    credentials = context.user_data.get('api_key_credentials', [])
    cred_index = credentials.index(connector_name) if connector_name in credentials else -1

    keyboard = [
        [InlineKeyboardButton("✅ Yes, Delete", callback_data=f"api_key_delete_cred_confirm:{cred_index}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="api_key_delete_cred_cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def delete_credential(query, context: ContextTypes.DEFAULT_TYPE, account_name: str, connector_name: str) -> None:
    """
    Delete a credential for a specific account and connector
    """
    try:
        import base64
        from servers import server_manager

        client = await server_manager.get_default_client()

        # Delete the credential
        await client.accounts.delete_credential(
            account_name=account_name,
            connector_name=connector_name
        )

        # Show success message
        account_escaped = escape_markdown_v2(account_name)
        connector_escaped = escape_markdown_v2(connector_name)
        message_text = (
            f"✅ *Credential Deleted*\n\n"
            f"The *{connector_escaped}* credentials have been removed from account *{account_escaped}*\\."
        )

        keyboard = [[InlineKeyboardButton("« Back to Account", callback_data=f"api_key_account:{base64.b64encode(account_name.encode()).decode()}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer("✅ Credential deleted")

    except Exception as e:
        logger.error(f"Error deleting credential: {e}", exc_info=True)
        error_text = f"❌ Error deleting credential: {escape_markdown_v2(str(e))}"

        keyboard = [[InlineKeyboardButton("« Back to Account", callback_data=f"api_key_account:{base64.b64encode(account_name.encode()).decode()}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)
        await query.answer("❌ Failed to delete credential")


async def show_account_credentials(query, context: ContextTypes.DEFAULT_TYPE, account_name: str) -> None:
    """
    Show connected credentials for a specific account
    """
    try:
        import base64
        from servers import server_manager

        client = await server_manager.get_default_client()

        # Get list of connected credentials for this account
        credentials = await client.accounts.list_account_credentials(account_name=account_name)

        account_escaped = escape_markdown_v2(account_name)

        # Store credentials in context for delete functionality
        context.user_data['api_key_credentials'] = credentials if credentials else []

        if not credentials:
            message_text = (
                f"🔑 *API Keys \\- {account_escaped}*\n\n\n\n"
                "No exchange credentials connected\\.\n\n\n\n"
                "Select an exchange below to add credentials:\n\n"
            )
            keyboard = []
        else:
            # Build list of connected credentials with delete buttons
            cred_lines = []
            credential_buttons = []
            for i, cred in enumerate(credentials):
                cred_escaped = escape_markdown_v2(str(cred))
                cred_lines.append(f"  ✅ {cred_escaped}")
                # Add delete button for each credential
                credential_buttons.append([
                    InlineKeyboardButton(f"🗑 Delete {cred}", callback_data=f"api_key_delete_cred:{i}")
                ])

            message_text = (
                f"🔑 *API Keys \\- {account_escaped}*\n\n\n"
                "*Connected Exchanges:*\n"
                + "\n".join(cred_lines) + "\n\n\n\n"
                "Select an exchange below to configure or delete:\n\n"
            )
            keyboard = credential_buttons

        # Get list of available connectors
        all_connectors = await client.connectors.list_connectors()

        # Filter out testnet connectors
        connectors = [c for c in all_connectors if 'testnet' not in c.lower()]

        # Create connector buttons in grid of 3 per row (for better readability of long names)
        # Store account name and connector list in context to avoid exceeding 64-byte callback_data limit
        context.user_data['api_key_current_account'] = account_name
        context.user_data['api_key_connectors'] = connectors

        connector_buttons = []
        for i, connector in enumerate(connectors):
            # Use index instead of full names to keep callback_data short
            connector_buttons.append(
                InlineKeyboardButton(connector, callback_data=f"api_key_connector:{i}")
            )

        # Organize into rows of 2 columns for better readability
        connector_button_rows = []
        for i in range(0, len(connector_buttons), 2):
            connector_button_rows.append(connector_buttons[i:i+2])

        keyboard = keyboard + connector_button_rows + [
            [InlineKeyboardButton("« Back to Accounts", callback_data="api_key_back_to_accounts")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing account credentials: {e}", exc_info=True)
        error_text = f"❌ Error loading account credentials: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="api_key_back_to_accounts")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


def _build_api_key_config_message(config_data: dict, current_field: str, all_fields: list) -> tuple:
    """
    Build the progressive API key configuration message showing filled fields and current prompt
    Returns (message_text, reply_markup)
    """
    account_name = config_data.get('account_name', '')
    connector_name = config_data.get('connector_name', '')
    values = config_data.get('values', {})

    account_escaped = escape_markdown_v2(account_name)
    connector_escaped = escape_markdown_v2(connector_name)

    # Build the message showing progress
    lines = [f"🔑 *Configure {connector_escaped}*\n"]
    lines.append(f"Account: *{account_escaped}*\n")

    for field in all_fields:
        if field in values:
            # Field already filled - show value (mask if contains 'secret', 'key', or 'password')
            value = values[field]
            if any(keyword in field.lower() for keyword in ['secret', 'key', 'password', 'passphrase']):
                value = '****'
            field_escaped = escape_markdown_v2(field)
            value_escaped = escape_markdown_v2(str(value))
            lines.append(f"*{field_escaped}:* `{value_escaped}` ✅")
        elif field == current_field:
            # Current field being filled
            field_escaped = escape_markdown_v2(field)
            lines.append(f"*{field_escaped}:* _\\(awaiting input\\)_")
            break
        else:
            # Future field - show placeholder
            field_escaped = escape_markdown_v2(field)
            lines.append(f"*{field_escaped}:* \\_\\_\\_")

    message_text = "\n".join(lines)

    # Build keyboard with back and cancel buttons
    buttons = []

    # Add back button if not on first field
    current_index = all_fields.index(current_field) if current_field in all_fields else 0
    if current_index > 0:
        buttons.append(InlineKeyboardButton("« Back", callback_data="api_key_config_back"))

    # Always add cancel button
    import base64
    encoded_account = base64.b64encode(account_name.encode()).decode()
    buttons.append(InlineKeyboardButton("❌ Cancel", callback_data=f"api_key_back_account:{encoded_account}"))

    keyboard = [buttons]
    reply_markup = InlineKeyboardMarkup(keyboard)

    return message_text, reply_markup


async def show_connector_config(query, context: ContextTypes.DEFAULT_TYPE, account_name: str, connector_name: str) -> None:
    """
    Start progressive configuration flow for a specific connector
    """
    try:
        from servers import server_manager

        client = await server_manager.get_default_client()

        # Get config map for this connector
        config_fields = await client.connectors.get_config_map(connector_name)

        # Initialize context storage for API key configuration
        context.user_data['configuring_api_key'] = True
        context.user_data['api_key_config_data'] = {
            'account_name': account_name,
            'connector_name': connector_name,
            'fields': config_fields,
            'values': {}
        }
        context.user_data['awaiting_api_key_input'] = config_fields[0] if config_fields else None
        context.user_data['api_key_message_id'] = query.message.message_id
        context.user_data['api_key_chat_id'] = query.message.chat_id

        if not config_fields:
            # No configuration needed
            import base64
            account_escaped = escape_markdown_v2(account_name)
            connector_escaped = escape_markdown_v2(connector_name)
            message_text = (
                f"🔑 *Configure {connector_escaped}*\n\n"
                f"Account: *{account_escaped}*\n\n"
                "✅ No configuration required for this connector\\."
            )
            encoded_account = base64.b64encode(account_name.encode()).decode()
            keyboard = [[InlineKeyboardButton("« Back", callback_data=f"api_key_back_account:{encoded_account}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text(message_text, parse_mode="MarkdownV2", reply_markup=reply_markup)
            return

        # Show first field
        message_text, reply_markup = _build_api_key_config_message(
            context.user_data['api_key_config_data'],
            config_fields[0],
            config_fields
        )

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error showing connector config: {e}", exc_info=True)
        import base64
        error_text = f"❌ Error loading connector config: {escape_markdown_v2(str(e))}"
        encoded_account = base64.b64encode(account_name.encode()).decode()
        keyboard = [[InlineKeyboardButton("« Back", callback_data=f"api_key_back_account:{encoded_account}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


# Create callback handler instance for registration
def get_config_callback_handler():
    """Get the callback query handler for config menu"""
    return CallbackQueryHandler(config_callback_handler, pattern="^config_|^modify_field_|^add_server_|^api_server_|^api_key_")


def get_modify_value_handler():
    """
    Get the message handler for server modification text input
    """
    return MessageHandler(filters.TEXT & ~filters.COMMAND, handle_modify_value_input)
