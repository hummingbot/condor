"""
API Servers configuration handlers
"""

import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from utils.telegram_formatters import escape_markdown_v2
from utils.auth import restricted

logger = logging.getLogger(__name__)


@restricted
async def servers_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /servers command - show API servers configuration directly."""
    from handlers import clear_all_input_states
    from utils.telegram_helpers import create_mock_query_from_message

    clear_all_input_states(context)
    mock_query = await create_mock_query_from_message(update, "Loading servers...")
    await show_api_servers(mock_query, context)


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
    Show API servers configuration with status and actions.
    Only shows servers the user has access to.
    """
    try:
        from config_manager import get_config_manager, ServerPermission

        # Reload configuration to pick up any manual changes
        get_config_manager().reload()

        user_id = query.from_user.id
        cm = get_config_manager()

        # Get only accessible servers
        servers = cm.list_accessible_servers(user_id)
        # User's preferred server (checks both user_data and config.yml)
        from config_manager import get_effective_server
        default_server = get_effective_server(query.message.chat_id, context.user_data)

        if not servers:
            message_text = (
                "🔌 *API Servers*\n\n"
                "No API servers configured\\.\n\n"
                "_Use the buttons below to add a server\\._"
            )
            keyboard = [
                [InlineKeyboardButton("➕ Add Server", callback_data="api_server_add")],
                [InlineKeyboardButton("« Close", callback_data="config_close")]
            ]
        else:
            # Build server list with status
            server_lines = []
            server_buttons = []

            # Check all server statuses in parallel
            server_names = list(servers.keys())
            status_tasks = [
                get_config_manager().check_server_status(name) for name in server_names
            ]
            status_results = await asyncio.gather(*status_tasks)
            server_statuses = dict(zip(server_names, status_results))

            for server_name, server_config in servers.items():
                status_result = server_statuses[server_name]

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
                    status_icon = "🔴"
                    error_msg = escape_markdown_v2(status_result.get("message", "Error"))
                    status_detail = f" \\[{error_msg}\\]"

                # Default server indicator
                default_indicator = " ⭐️" if server_name == default_server else ""

                # Permission badge
                perm = cm.get_server_permission(user_id, server_name)
                perm_badges = {
                    ServerPermission.OWNER: "👑",
                    ServerPermission.TRADER: "💱",
                    ServerPermission.VIEWER: "👁",
                }
                perm_badge = perm_badges.get(perm, "") + " " if perm else ""

                url = f"{server_config['host']}:{server_config['port']}"
                url_escaped = escape_markdown_v2(url)
                name_escaped = escape_markdown_v2(server_name)

                server_lines.append(
                    f"{status_icon} {perm_badge}*{name_escaped}*{default_indicator}{status_detail}\n"
                    f"   `{url_escaped}`"
                )

                # Add button for each server
                button_text = f"{perm_badge}{server_name}"
                if server_name == default_server:
                    button_text += " ⭐️"
                server_buttons.append(
                    InlineKeyboardButton(button_text, callback_data=f"api_server_view_{server_name}")
                )

            server_count = escape_markdown_v2(str(len(servers)))
            message_text = (
                f"🔌 *API Servers* \\({server_count} configured\\)\n\n"
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
                    InlineKeyboardButton("« Close", callback_data="config_close")
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
        keyboard = [[InlineKeyboardButton("« Close", callback_data="config_close")]]
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
    # Server sharing actions
    elif action_data.startswith("share_user_"):
        # Format: share_user_{uid}_{server_name}
        parts = action_data.replace("share_user_", "").split("_", 1)
        if len(parts) == 2:
            target_user_id = int(parts[0])
            server_name = parts[1]
            await select_share_user(query, context, server_name, target_user_id)
        else:
            await query.answer("Invalid share action")
    elif action_data.startswith("share_manual_"):
        server_name = action_data.replace("share_manual_", "")
        await start_manual_share_flow(query, context, server_name)
    elif action_data.startswith("share_start_"):
        server_name = action_data.replace("share_start_", "")
        await start_share_flow(query, context, server_name)
    elif action_data.startswith("share_cancel_"):
        server_name = action_data.replace("share_cancel_", "")
        # Clear sharing state
        context.user_data.pop('sharing_server', None)
        context.user_data.pop('awaiting_share_user_id', None)
        context.user_data.pop('share_target_user_id', None)
        context.user_data.pop('share_message_id', None)
        context.user_data.pop('share_chat_id', None)
        await show_server_sharing(query, context, server_name)
    elif action_data.startswith("share_"):
        server_name = action_data.replace("share_", "")
        await show_server_sharing(query, context, server_name)
    elif action_data.startswith("perm_trader_"):
        server_name = action_data.replace("perm_trader_", "")
        await set_share_permission(query, context, server_name, "trader")
    elif action_data.startswith("perm_viewer_"):
        server_name = action_data.replace("perm_viewer_", "")
        await set_share_permission(query, context, server_name, "viewer")
    elif action_data.startswith("revoke_"):
        # Format: revoke_{user_id}_{server_name}
        parts = action_data.replace("revoke_", "").split("_", 1)
        if len(parts) == 2:
            target_user_id = int(parts[0])
            server_name = parts[1]
            await revoke_access(query, context, server_name, target_user_id)
        else:
            await query.answer("Invalid revoke action")
    else:
        await query.answer("Unknown action")


async def show_server_details(query, context: ContextTypes.DEFAULT_TYPE, server_name: str) -> None:
    """Show details and actions for a specific server (callback query wrapper)."""
    try:
        user_id = query.from_user.id
        chat_id = query.message.chat_id

        async def edit_message(text, parse_mode=None, reply_markup=None):
            await query.message.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)

        async def send_error(text):
            await query.answer(text)

        await _show_server_details(context, server_name, user_id, chat_id, edit_message, send_error)
    except Exception as e:
        logger.error(f"Error showing server details: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)}")


async def _show_server_details(
    context: ContextTypes.DEFAULT_TYPE,
    server_name: str,
    user_id: int,
    chat_id: int,
    edit_message,
    send_error,
) -> None:
    """Show details and actions for a specific server.
    Actions are restricted based on user's permission level.
    """
    from config_manager import get_config_manager, ServerPermission

    # Clear any modify state when showing server details
    context.user_data.pop('modifying_server', None)
    context.user_data.pop('modifying_field', None)
    context.user_data.pop('awaiting_modify_input', None)

    server = get_config_manager().get_server(server_name)
    if not server:
        await send_error("❌ Server not found")
        return

    cm = get_config_manager()

    # Check user's permission level
    perm = cm.get_server_permission(user_id, server_name)
    if not perm:
        await send_error("❌ No access to this server")
        return

    is_owner = perm == ServerPermission.OWNER
    can_trade = perm in (ServerPermission.OWNER, ServerPermission.TRADER)

    from config_manager import get_effective_server
    is_user_default = server_name == get_effective_server(chat_id, context.user_data)

    # Check status
    status_result = await get_config_manager().check_server_status(server_name)
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

    # Permission badge
    perm_labels = {
        ServerPermission.OWNER: "👑 Owner",
        ServerPermission.TRADER: "💱 Trader",
        ServerPermission.VIEWER: "👁 Viewer",
    }
    perm_label = perm_labels.get(perm, "Unknown")

    message_text = (
        f"🔌 *Server: {name_escaped}*\n\n"
        f"*Status:* {status_text}\n"
        f"*Host:* `{host_escaped}`\n"
        f"*Port:* `{port_escaped}`\n"
        f"*Access:* {escape_markdown_v2(perm_label)}\n"
    )

    # Only show username to owners
    if is_owner:
        username_escaped = escape_markdown_v2(server['username'])
        message_text += f"*Username:* `{username_escaped}`\n"

    # Show if this is the user's default
    if is_user_default:
        message_text += "\n⭐️ _Your default server_"

    # Different help text based on permission
    if is_owner:
        message_text += "\n\n_You can modify, share, or delete this server\\._"
    elif can_trade:
        message_text += "\n\n_You can use this server for trading\\._"
    else:
        message_text += "\n\n_You have view\\-only access to this server\\._"

    keyboard = []

    # Show Set as Default button for traders and owners
    if can_trade and not is_user_default:
        keyboard.append([InlineKeyboardButton("⭐️ Set as Default", callback_data=f"api_server_set_default_{server_name}")])

    # Only owners can modify server settings
    if is_owner:
        keyboard.append([
            InlineKeyboardButton("🌐 Host", callback_data=f"modify_field_host_{server_name}"),
            InlineKeyboardButton("🔌 Port", callback_data=f"modify_field_port_{server_name}"),
            InlineKeyboardButton("👤 User", callback_data=f"modify_field_username_{server_name}"),
            InlineKeyboardButton("🔑 Pass", callback_data=f"modify_field_password_{server_name}"),
        ])
        keyboard.append([
            InlineKeyboardButton("📤 Share", callback_data=f"api_server_share_{server_name}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"api_server_delete_{server_name}"),
        ])

    keyboard.append([InlineKeyboardButton("« Back to Servers", callback_data="config_api_servers")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await edit_message(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def set_default_server(query, context: ContextTypes.DEFAULT_TYPE, server_name: str) -> None:
    """Set server as default for this user/chat"""
    try:
        from handlers.dex._shared import invalidate_cache
        from handlers.config.user_preferences import set_active_server
        from config_manager import get_config_manager

        # Save to user_data (in-memory, pickle persistence)
        set_active_server(context.user_data, server_name)

        # Also save to config.yml for immediate persistence (survives hard kills)
        chat_id = query.message.chat_id
        get_config_manager().set_chat_default_server(chat_id, server_name)

        # Invalidate ALL cached data since we're switching to a different server
        invalidate_cache(context.user_data, "all")
        context.user_data["_current_server"] = server_name

        await query.answer(f"✅ Set {server_name} as your default server")
        await show_server_details(query, context, server_name)

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
    """Delete a server from configuration.
    Only owners can delete servers.
    """
    try:
        from config_manager import get_config_manager
        from handlers.dex._shared import invalidate_cache
        from config_manager import get_config_manager, ServerPermission

        user_id = query.from_user.id
        cm = get_config_manager()

        # Check if user has owner permission
        perm = cm.get_server_permission(user_id, server_name)
        if perm != ServerPermission.OWNER:
            await query.answer("❌ Only the owner can delete this server", show_alert=True)
            return

        # Check if this is the user's current default server
        from handlers.config.user_preferences import get_active_server
        was_current = (get_active_server(context.user_data) == server_name)

        # Delete server and clean up permissions
        success = get_config_manager().delete_server(server_name, actor_id=user_id)

        if success:
            # Invalidate cache if we deleted the server that was in use
            if was_current:
                invalidate_cache(context.user_data, "all")
                logger.info(f"Cache invalidated after deleting current server '{server_name}'")

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
            from config_manager import get_config_manager
            if new_value in get_config_manager().list_servers() and new_value != server_data.get('name'):
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
        from config_manager import get_config_manager

        server_data = context.user_data.get('adding_server', {})
        user_id = query.from_user.id

        required_fields = ['name', 'host', 'port', 'username', 'password']
        for field in required_fields:
            if field not in server_data:
                await query.answer(f"❌ Missing field: {field}")
                return

        # Add server with ownership registration
        success = get_config_manager().add_server(
            name=server_data['name'],
            host=server_data['host'],
            port=server_data['port'],
            username=server_data['username'],
            password=server_data['password'],
            owner_id=user_id
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
        from config_manager import get_config_manager

        server = get_config_manager().get_server(server_name)
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

        from config_manager import get_config_manager
        server = get_config_manager().get_server(server_name)
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

    logger.info(f"Handling modify input: server={server_name}, field={field}, msg_id={modify_message_id}, chat_id={modify_chat_id}")

    try:
        await update.message.delete()
    except:
        pass

    try:
        from config_manager import get_config_manager

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
        success = get_config_manager().modify_server(server_name, **kwargs)

        # Clear modification state
        context.user_data.pop('modifying_server', None)
        context.user_data.pop('modifying_field', None)
        context.user_data.pop('awaiting_modify_input', None)

        if success:
            logger.info(f"Successfully modified {field} for server {server_name}")

            # Invalidate cache if this is the user's current default server
            from handlers.config.user_preferences import get_active_server
            if get_active_server(context.user_data) == server_name:
                from handlers.dex._shared import invalidate_cache
                invalidate_cache(context.user_data, "all")
                logger.info(f"Cache invalidated after modifying current server '{server_name}'")
            if modify_message_id and modify_chat_id:
                logger.info(f"Attempting to show server details for message {modify_message_id}")

                context.user_data.pop('modify_message_id', None)
                context.user_data.pop('modify_chat_id', None)

                async def edit_message(text, parse_mode=None, reply_markup=None):
                    await context.bot.edit_message_text(
                        chat_id=modify_chat_id,
                        message_id=modify_message_id,
                        text=text,
                        parse_mode=parse_mode,
                        reply_markup=reply_markup,
                    )

                async def send_error(text):
                    await context.bot.send_message(chat_id=chat_id, text=text)

                try:
                    await _show_server_details(
                        context, server_name, update.message.from_user.id,
                        modify_chat_id, edit_message, send_error,
                    )
                    logger.info("Successfully showed server details")
                except Exception as e:
                    logger.error(f"Error showing server details: {e}", exc_info=True)
            else:
                context.user_data.pop('modify_message_id', None)
                context.user_data.pop('modify_chat_id', None)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ Successfully updated {field} for server '{server_name}'"
                )
        else:
            # Clean up state even on failure
            context.user_data.pop('modify_message_id', None)
            context.user_data.pop('modify_chat_id', None)

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
        # Clean up state on exception
        context.user_data.pop('awaiting_modify_input', None)
        context.user_data.pop('modify_message_id', None)
        context.user_data.pop('modify_chat_id', None)
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Error: {str(e)}"
            )
        except:
            pass


# Entry point for text input routing
async def handle_server_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route server-related text input to appropriate handler"""
    if context.user_data.get('awaiting_add_server_input'):
        await handle_add_server_input(update, context)
    elif context.user_data.get('awaiting_modify_input'):
        await handle_modify_value_input(update, context)
    elif context.user_data.get('awaiting_share_user_id'):
        await handle_share_user_id_input(update, context)


# ==================== Server Sharing ====================

async def show_server_sharing(query, context: ContextTypes.DEFAULT_TYPE, server_name: str) -> None:
    """Show sharing details and management for a server."""
    from config_manager import get_config_manager, ServerPermission

    user_id = query.from_user.id
    cm = get_config_manager()

    # Check ownership
    perm = cm.get_server_permission(user_id, server_name)
    if perm != ServerPermission.OWNER:
        await query.answer("Only the owner can manage sharing", show_alert=True)
        return

    shared_users = cm.get_server_shared_users(server_name)
    name_escaped = escape_markdown_v2(server_name)

    message = f"📤 *Share Server: {name_escaped}*\n\n"

    keyboard = []

    if shared_users:
        message += "*Shared with:*\n"

        perm_badges = {
            ServerPermission.TRADER: "💱",
            ServerPermission.VIEWER: "👁",
        }

        for target_user_id, perm in shared_users:
            target_user = cm.get_user(target_user_id)
            username = target_user.get('username') if target_user else None

            badge = perm_badges.get(perm, "?")
            if username:
                message += f"  {badge} `{target_user_id}` \\(@{escape_markdown_v2(username)}\\)\n"
            else:
                message += f"  {badge} `{target_user_id}`\n"

            keyboard.append([
                InlineKeyboardButton(
                    f"🗑 Revoke {target_user_id}",
                    callback_data=f"api_server_revoke_{target_user_id}_{server_name}"
                )
            ])

        message += "\n"
    else:
        message += "_Not shared with anyone yet\\._\n\n"

    message += "_Enter a User ID below to share this server\\._"

    keyboard.append([
        InlineKeyboardButton("➕ Share with User ID", callback_data=f"api_server_share_start_{server_name}")
    ])
    keyboard.append([InlineKeyboardButton("« Back", callback_data=f"api_server_view_{server_name}")])

    await query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def start_share_flow(query, context: ContextTypes.DEFAULT_TYPE, server_name: str) -> None:
    """Start the share flow.
    - Admin sees a list of approved users to pick from
    - Regular users enter the user ID manually
    """
    from config_manager import get_config_manager, ServerPermission, UserRole

    user_id = query.from_user.id
    cm = get_config_manager()

    perm = cm.get_server_permission(user_id, server_name)
    if perm != ServerPermission.OWNER:
        await query.answer("Only the owner can share", show_alert=True)
        return

    context.user_data['sharing_server'] = server_name
    context.user_data['share_message_id'] = query.message.message_id
    context.user_data['share_chat_id'] = query.message.chat_id

    name_escaped = escape_markdown_v2(server_name)
    owner_id = cm.get_server_owner(server_name)

    # Get already shared users to exclude them
    shared_users = cm.get_server_shared_users(server_name)
    shared_user_ids = {uid for uid, _ in shared_users}

    # For admin: show list of approved users
    if cm.is_admin(user_id):
        approved_users = [
            u for u in cm.get_all_users()
            if u.get('role') in (UserRole.USER.value, UserRole.ADMIN.value)
            and u['user_id'] != owner_id
            and u['user_id'] not in shared_user_ids
        ]

        if not approved_users:
            message = (
                f"📤 *Share Server: {name_escaped}*\n\n"
                "_No approved users available to share with\\._\n\n"
                "All approved users either already have access or are the owner\\."
            )
            keyboard = [[InlineKeyboardButton("« Back", callback_data=f"api_server_share_{server_name}")]]
        else:
            message = (
                f"📤 *Share Server: {name_escaped}*\n\n"
                "Select a user to share with:"
            )
            keyboard = []
            for u in approved_users[:10]:  # Limit to 10 users
                uid = u['user_id']
                username = u.get('username') or 'N/A'
                btn_text = f"@{username}" if username != 'N/A' else str(uid)
                keyboard.append([
                    InlineKeyboardButton(btn_text, callback_data=f"api_server_share_user_{uid}_{server_name}")
                ])

            if len(approved_users) > 10:
                message += f"\n\n_Showing first 10 of {len(approved_users)} users_"

            keyboard.append([InlineKeyboardButton("✏️ Enter ID manually", callback_data=f"api_server_share_manual_{server_name}")])
            keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data=f"api_server_share_cancel_{server_name}")])

        context.user_data['awaiting_share_user_id'] = False
    else:
        # Regular users: manual entry
        message = (
            f"📤 *Share Server: {name_escaped}*\n\n"
            "Enter the *User ID* of the user you want to share with:\n\n"
            "_The user must be approved to receive access\\._"
        )
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data=f"api_server_share_cancel_{server_name}")]]
        context.user_data['awaiting_share_user_id'] = True

    await query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def select_share_user(query, context: ContextTypes.DEFAULT_TYPE, server_name: str, target_user_id: int) -> None:
    """Handle user selection from the list (admin flow)."""
    from config_manager import get_config_manager, ServerPermission

    cm = get_config_manager()
    user_id = query.from_user.id

    # Verify ownership
    perm = cm.get_server_permission(user_id, server_name)
    if perm != ServerPermission.OWNER:
        await query.answer("Only the owner can share", show_alert=True)
        return

    # Store target and ask for permission level
    context.user_data['sharing_server'] = server_name
    context.user_data['share_target_user_id'] = target_user_id

    target_user = cm.get_user(target_user_id)
    username = target_user.get('username') if target_user else None
    name_escaped = escape_markdown_v2(server_name)

    if username:
        user_display = f"`{target_user_id}` \\(@{escape_markdown_v2(username)}\\)"
    else:
        user_display = f"`{target_user_id}`"

    message = (
        f"📤 *Share Server: {name_escaped}*\n\n"
        f"Sharing with: {user_display}\n\n"
        "Select the permission level:"
    )

    keyboard = [
        [InlineKeyboardButton("💱 Trader (can trade)", callback_data=f"api_server_perm_trader_{server_name}")],
        [InlineKeyboardButton("👁 Viewer (read-only)", callback_data=f"api_server_perm_viewer_{server_name}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"api_server_share_cancel_{server_name}")],
    ]

    await query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def start_manual_share_flow(query, context: ContextTypes.DEFAULT_TYPE, server_name: str) -> None:
    """Start manual entry flow for sharing (used when admin clicks 'Enter ID manually')."""
    from config_manager import get_config_manager, ServerPermission

    user_id = query.from_user.id
    cm = get_config_manager()

    perm = cm.get_server_permission(user_id, server_name)
    if perm != ServerPermission.OWNER:
        await query.answer("Only the owner can share", show_alert=True)
        return

    context.user_data['sharing_server'] = server_name
    context.user_data['awaiting_share_user_id'] = True
    context.user_data['share_message_id'] = query.message.message_id
    context.user_data['share_chat_id'] = query.message.chat_id

    name_escaped = escape_markdown_v2(server_name)
    message = (
        f"📤 *Share Server: {name_escaped}*\n\n"
        "Enter the *User ID* of the user you want to share with:\n\n"
        "_The user must be approved to receive access\\._"
    )

    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data=f"api_server_share_cancel_{server_name}")]]

    await query.message.edit_text(
        message,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_share_user_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle user ID input for sharing."""
    from config_manager import get_config_manager

    if not context.user_data.get('awaiting_share_user_id'):
        return

    server_name = context.user_data.get('sharing_server')
    if not server_name:
        return

    try:
        await update.message.delete()
    except:
        pass

    cm = get_config_manager()
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    message_id = context.user_data.get('share_message_id')

    # Parse target user ID
    try:
        target_user_id = int(update.message.text.strip())
    except ValueError:
        if message_id:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="❌ Invalid User ID\\. Please enter a valid number\\.\n\nEnter the User ID:",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data=f"api_server_share_cancel_{server_name}")
                ]])
            )
        return

    # Check if target user is approved
    if not cm.is_approved(target_user_id):
        if message_id:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"❌ User `{target_user_id}` is not an approved user\\.\n\n"
                     "Only approved users can receive server access\\.\n\n"
                     "Enter a different User ID:",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data=f"api_server_share_cancel_{server_name}")
                ]])
            )
        return

    # Check if trying to share with self
    owner_id = cm.get_server_owner(server_name)
    if target_user_id == owner_id:
        if message_id:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="❌ You can't share with the owner\\.\n\nEnter a different User ID:",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data=f"api_server_share_cancel_{server_name}")
                ]])
            )
        return

    # Store target and ask for permission level
    context.user_data['share_target_user_id'] = target_user_id
    context.user_data['awaiting_share_user_id'] = False

    target_user = cm.get_user(target_user_id)
    username = target_user.get('username') if target_user else None
    name_escaped = escape_markdown_v2(server_name)

    if username:
        user_display = f"`{target_user_id}` \\(@{escape_markdown_v2(username)}\\)"
    else:
        user_display = f"`{target_user_id}`"

    message = (
        f"📤 *Share Server: {name_escaped}*\n\n"
        f"Sharing with: {user_display}\n\n"
        "Select the permission level:"
    )

    keyboard = [
        [InlineKeyboardButton("💱 Trader (can trade)", callback_data=f"api_server_perm_trader_{server_name}")],
        [InlineKeyboardButton("👁 Viewer (read-only)", callback_data=f"api_server_perm_viewer_{server_name}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"api_server_share_cancel_{server_name}")],
    ]

    if message_id:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def set_share_permission(query, context: ContextTypes.DEFAULT_TYPE, server_name: str, permission: str) -> None:
    """Set the permission level and complete sharing."""
    from config_manager import get_config_manager, ServerPermission

    user_id = query.from_user.id
    target_user_id = context.user_data.get('share_target_user_id')

    if not target_user_id:
        await query.answer("Session expired. Please try again.", show_alert=True)
        await show_api_servers(query, context)
        return

    cm = get_config_manager()

    perm_map = {
        'trader': ServerPermission.TRADER,
        'viewer': ServerPermission.VIEWER,
    }
    perm = perm_map.get(permission)

    if not perm:
        await query.answer("Invalid permission", show_alert=True)
        return

    success = cm.share_server(server_name, user_id, target_user_id, perm)

    # Clean up state
    context.user_data.pop('sharing_server', None)
    context.user_data.pop('share_target_user_id', None)
    context.user_data.pop('share_message_id', None)
    context.user_data.pop('share_chat_id', None)

    if success:
        # Auto-set as default if this is the user's only accessible server
        accessible = cm.get_accessible_servers(target_user_id)
        if accessible and len(accessible) == 1:
            cm.set_chat_default_server(target_user_id, server_name)
            auto_default = True
        else:
            auto_default = False

        # Notify target user
        try:
            perm_label = "Trader" if perm == ServerPermission.TRADER else "Viewer"
            default_note = "\n\n_This server has been set as your default\\._" if auto_default else ""
            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    f"📥 *Server Shared With You*\n\n"
                    f"You now have *{escape_markdown_v2(perm_label)}* access to server:\n"
                    f"`{escape_markdown_v2(server_name)}`{default_note}\n\n"
                    f"Use /config \\> API Servers to access it\\."
                ),
                parse_mode="MarkdownV2"
            )
        except Exception as e:
            logger.warning(f"Failed to notify user {target_user_id} of share: {e}")

        await query.answer(f"Shared with {target_user_id}", show_alert=True)
        await show_server_sharing(query, context, server_name)
    else:
        await query.answer("Failed to share server", show_alert=True)
        await show_server_sharing(query, context, server_name)


async def revoke_access(query, context: ContextTypes.DEFAULT_TYPE, server_name: str, target_user_id: int) -> None:
    """Revoke a user's access to a server."""
    from config_manager import get_config_manager

    user_id = query.from_user.id
    cm = get_config_manager()

    success = cm.revoke_server_access(server_name, user_id, target_user_id)

    if success:
        # Notify target user
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    f"🚫 *Access Revoked*\n\n"
                    f"Your access to server `{escape_markdown_v2(server_name)}` has been revoked\\."
                ),
                parse_mode="MarkdownV2"
            )
        except Exception as e:
            logger.warning(f"Failed to notify user {target_user_id} of revocation: {e}")

        await query.answer(f"Revoked access for {target_user_id}", show_alert=True)
    else:
        await query.answer("Failed to revoke access", show_alert=True)

    await show_server_sharing(query, context, server_name)
