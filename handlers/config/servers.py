"""
API Servers configuration handlers
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)

# Conversation states
(ADD_SERVER_NAME, ADD_SERVER_HOST, ADD_SERVER_PORT,
 ADD_SERVER_USERNAME, ADD_SERVER_PASSWORD, ADD_SERVER_CONFIRM,
 MODIFY_SERVER_FIELD_CHOICE, MODIFY_SERVER_VALUE) = range(8)


async def handle_servers_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main router for servers-related callbacks"""
    query = update.callback_query

    if query.data == "config_api_servers":
        await show_api_servers(query, context)
    elif query.data.startswith("modify_field_"):
        await handle_modify_field_selection(query, context)
    elif query.data.startswith("add_server_"):
        await handle_add_server_callbacks(query, context)
    elif query.data.startswith("api_server_"):
        await handle_api_server_action(query, context)


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
                    status_detail = ""
                elif status_result["status"] == "auth_error":
                    status_icon = "🔴"
                    error_msg = escape_markdown_v2(status_result.get("message", "Auth Error"))
                    status_detail = f" \\[{error_msg}\\]"
                elif status_result["status"] == "offline":
                    status_icon = "🔴"
                    error_msg = escape_markdown_v2(status_result.get("message", "Offline"))
                    status_detail = f" \\[{error_msg}\\]"
                else:
                    status_icon = "🟡"
                    error_msg = escape_markdown_v2(status_result.get("message", "Error"))
                    status_detail = f" \\[{error_msg}\\]"

                # Default server indicator
                default_indicator = " ⭐️" if server_name == default_server else ""

                url = f"{server_config['host']}:{server_config['port']}"
                url_escaped = escape_markdown_v2(url)
                name_escaped = escape_markdown_v2(server_name)

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

        try:
            await query.message.edit_text(
                message_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except Exception as e:
            if "Message is not modified" in str(e):
                await query.answer("✅ Already up to date")
            else:
                raise

    except Exception as e:
        logger.error(f"Error showing API servers: {e}", exc_info=True)
        error_text = f"❌ Error loading API servers: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="config_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def handle_api_server_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle API server specific actions"""
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
    """Show details and actions for a specific server"""
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
    """Set a server as the default"""
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
    """Ask for confirmation before deleting a server"""
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
    """Delete a server from configuration"""
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


# Add Server Flow
def _build_add_server_message(server_data: dict, current_field: str) -> tuple:
    """Build the progressive add server message"""
    fields = {
        'name': {'label': 'Name', 'default': None},
        'host': {'label': 'Host', 'default': 'localhost'},
        'port': {'label': 'Port', 'default': '8000'},
        'username': {'label': 'Username', 'default': 'admin'},
        'password': {'label': 'Password', 'default': 'admin'}
    }

    field_order = ['name', 'host', 'port', 'username', 'password']
    lines = ["➕ *Add New Server*\n"]

    for field_key, field_info in fields.items():
        if field_key in server_data:
            value = server_data[field_key]
            if field_key == 'password':
                value = '****'
            label = escape_markdown_v2(field_info['label'])
            value_escaped = escape_markdown_v2(str(value))
            lines.append(f"*{label}:* `{value_escaped}` ✅")
        elif field_key == current_field:
            label = escape_markdown_v2(field_info['label'])
            lines.append(f"*{label}:* _\\(awaiting input\\)_")
            break
        else:
            label = escape_markdown_v2(field_info['label'])
            lines.append(f"*{label}:* \\_\\_\\_")

    message_text = "\n".join(lines)
    buttons = []

    if current_field in fields and fields[current_field]['default']:
        default_value = fields[current_field]['default']
        buttons.append(InlineKeyboardButton(
            f"Default: {default_value}",
            callback_data=f"add_server_default_{current_field}"
        ))

    current_index = field_order.index(current_field)
    if current_index > 0:
        buttons.append(InlineKeyboardButton("« Back", callback_data="add_server_back"))

    buttons.append(InlineKeyboardButton("❌ Cancel", callback_data="config_api_servers"))

    keyboard = [buttons]
    reply_markup = InlineKeyboardMarkup(keyboard)

    return message_text, reply_markup


async def start_add_server(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the add server conversation"""
    context.user_data['adding_server'] = {}
    context.user_data['awaiting_add_server_input'] = 'name'
    context.user_data['add_server_message_id'] = query.message.message_id
    context.user_data['add_server_chat_id'] = query.message.chat_id

    message_text, reply_markup = _build_add_server_message({}, 'name')

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )
    await query.answer()


async def _update_add_server_message(context: ContextTypes.DEFAULT_TYPE, bot) -> None:
    """Update the add server message with current progress"""
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
    """Show confirmation screen with all values"""
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
    """Handle text input during add server flow"""
    awaiting_field = context.user_data.get('awaiting_add_server_input')
    if not awaiting_field:
        return

    if 'add_server_chat_id' not in context.user_data:
        context.user_data['add_server_chat_id'] = update.effective_chat.id

    try:
        await update.message.delete()
    except:
        pass

    try:
        new_value = update.message.text.strip()
        server_data = context.user_data.get('adding_server', {})

        is_initial_flow = awaiting_field in ['name', 'host', 'port', 'username', 'password'] and \
                          awaiting_field not in server_data

        if awaiting_field == 'name':
            from servers import server_manager
            if new_value in server_manager.list_servers() and new_value != server_data.get('name'):
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
                return

            server_data['name'] = new_value
            context.user_data['adding_server'] = server_data

            if is_initial_flow:
                context.user_data['awaiting_add_server_input'] = 'host'
                await _update_add_server_message(context, update.get_bot())
            else:
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
    """Handle callback queries during add server flow"""
    try:
        if query.data == "add_server_back":
            field_order = ['name', 'host', 'port', 'username', 'password']
            current_field = context.user_data.get('awaiting_add_server_input')

            if current_field and current_field in field_order:
                current_index = field_order.index(current_field)
                if current_index > 0:
                    previous_field = field_order[current_index - 1]
                    server_data = context.user_data.get('adding_server', {})
                    server_data.pop(previous_field, None)
                    context.user_data['adding_server'] = server_data
                    context.user_data['awaiting_add_server_input'] = previous_field
                    await query.answer("« Going back")
                    await _update_add_server_message(context, query.message.get_bot())
            return

        if query.data.startswith("add_server_default_"):
            field = query.data.replace("add_server_default_", "")
            server_data = context.user_data.get('adding_server', {})

            defaults = {
                'host': 'localhost',
                'port': 8000,
                'username': 'admin',
                'password': 'admin'
            }

            server_data[field] = defaults[field]
            context.user_data['adding_server'] = server_data

            field_order = ['name', 'host', 'port', 'username', 'password']
            current_index = field_order.index(field)

            if current_index < len(field_order) - 1:
                context.user_data['awaiting_add_server_input'] = field_order[current_index + 1]
                await query.answer(f"✅ Using default: {defaults[field]}")
                await _update_add_server_message(context, query.message.get_bot())
            else:
                context.user_data['awaiting_add_server_input'] = None
                await query.answer(f"✅ Using default: {defaults[field]}")
                await show_add_server_confirmation(context, query.message.get_bot(), query.message.chat_id)

        elif query.data.startswith("add_server_modify_"):
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
            context.user_data['awaiting_add_server_input'] = None
            await query.answer()
            await show_add_server_confirmation(context, query.message.get_bot(), query.message.chat_id)

    except Exception as e:
        logger.error(f"Error handling add server callback: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)}")


async def confirm_add_server(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Actually add the server to configuration"""
    try:
        from servers import server_manager

        server_data = context.user_data.get('adding_server', {})

        required_fields = ['name', 'host', 'port', 'username', 'password']
        for field in required_fields:
            if field not in server_data:
                await query.answer(f"❌ Missing field: {field}")
                return

        success = server_manager.add_server(
            name=server_data['name'],
            host=server_data['host'],
            port=server_data['port'],
            username=server_data['username'],
            password=server_data['password']
        )

        if success:
            await query.answer(f"✅ Added server '{server_data['name']}'")
            context.user_data.pop('adding_server', None)
            context.user_data.pop('awaiting_add_server_input', None)
            await show_api_servers(query, context)
        else:
            await query.answer("❌ Failed to add server (may already exist)")

    except Exception as e:
        logger.error(f"Error confirming add server: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)}")


# Modify Server Flow
async def start_modify_server(query, context: ContextTypes.DEFAULT_TYPE, server_name: str) -> int:
    """Start the modify server conversation"""
    try:
        from servers import server_manager

        server = server_manager.get_server(server_name)
        if not server:
            await query.answer("❌ Server not found")
            return ConversationHandler.END

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
    """Handle field selection for server modification"""
    try:
        parts = query.data.replace("modify_field_", "").split("_", 1)
        if len(parts) != 2:
            await query.answer("❌ Invalid field selection")
            return

        field, server_name = parts

        context.user_data['modifying_server'] = server_name
        context.user_data['modifying_field'] = field
        context.user_data['awaiting_modify_input'] = True
        context.user_data['modify_message_id'] = query.message.message_id
        context.user_data['modify_chat_id'] = query.message.chat_id

        from servers import server_manager
        server = server_manager.get_server(server_name)
        if not server:
            await query.answer("❌ Server not found")
            return

        current_value = server.get(field, "N/A")
        if field == "password":
            current_value = "****"

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


async def handle_modify_value_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text input for server field modification"""
    if not context.user_data.get('awaiting_modify_input'):
        return

    chat_id = update.message.chat_id
    server_name = context.user_data.get('modifying_server')
    field = context.user_data.get('modifying_field')
    new_value = update.message.text.strip()

    modify_message_id = context.user_data.get('modify_message_id')
    modify_chat_id = context.user_data.get('modify_chat_id')

    try:
        await update.message.delete()
    except:
        pass

    try:
        from servers import server_manager

        if not server_name or not field:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Session expired. Please start over with /config"
            )
            context.user_data.pop('awaiting_modify_input', None)
            return

        if field == "port":
            try:
                new_value = int(new_value)
            except ValueError:
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
                return

        kwargs = {field: new_value}
        success = server_manager.modify_server(server_name, **kwargs)

        context.user_data.pop('modifying_server', None)
        context.user_data.pop('modifying_field', None)
        context.user_data.pop('awaiting_modify_input', None)
        context.user_data.pop('modify_message_id', None)
        context.user_data.pop('modify_chat_id', None)

        if success:
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
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ Successfully updated {field} for server '{server_name}'"
                )
        else:
            if modify_message_id and modify_chat_id:
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
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Error: {str(e)}"
            )
        except:
            pass
        context.user_data.pop('awaiting_modify_input', None)


# Entry point for text input routing
async def handle_server_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route server-related text input to appropriate handler"""
    if context.user_data.get('awaiting_add_server_input'):
        await handle_add_server_input(update, context)
    elif context.user_data.get('awaiting_modify_input'):
        await handle_modify_value_input(update, context)
