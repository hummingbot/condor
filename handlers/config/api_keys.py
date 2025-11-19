"""
API Keys configuration management handlers
"""

import logging
import base64
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)


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


async def handle_api_key_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle API key specific actions
    """
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


async def show_account_credentials(query, context: ContextTypes.DEFAULT_TYPE, account_name: str) -> None:
    """
    Show connected credentials for a specific account
    """
    try:
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
        error_text = f"❌ Error loading connector config: {escape_markdown_v2(str(e))}"
        encoded_account = base64.b64encode(account_name.encode()).decode()
        keyboard = [[InlineKeyboardButton("« Back", callback_data=f"api_key_back_account:{encoded_account}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


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

        config_data = context.user_data.get('api_key_config_data', {})
        account_name = config_data.get('account_name')
        connector_name = config_data.get('connector_name')
        values = config_data.get('values', {})
        message_id = context.user_data.get('api_key_message_id')

        if not account_name or not connector_name or not values:
            await bot.send_message(chat_id=chat_id, text="❌ Missing configuration data")
            return

        # Show "waiting for connection" message
        account_escaped = escape_markdown_v2(account_name)
        connector_escaped = escape_markdown_v2(connector_name)
        waiting_message_text = (
            f"⏳ *Connecting to {connector_escaped}*\n\n"
            f"Account: *{account_escaped}*\n\n"
            "Please wait while we verify your credentials\\.\\.\\."
        )

        if message_id and chat_id:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=waiting_message_text,
                parse_mode="MarkdownV2"
            )

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

        # Show brief success message
        connector_escaped = escape_markdown_v2(connector_name)
        success_text = f"✅ *{connector_escaped}* connected successfully\\!"

        if message_id and chat_id:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=success_text,
                parse_mode="MarkdownV2"
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=success_text,
                parse_mode="MarkdownV2"
            )

        # Create a mock query object to reuse the existing show_account_credentials function
        # This automatically refreshes the account credentials view
        import asyncio
        from types import SimpleNamespace

        # Wait a moment to let the user see the success message
        await asyncio.sleep(1.5)

        # Create a mock query object with the necessary attributes
        mock_message = SimpleNamespace(
            edit_text=lambda text, parse_mode=None, reply_markup=None: bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            ),
            chat_id=chat_id,
            message_id=message_id
        )
        mock_query = SimpleNamespace(message=mock_message)

        # Navigate back to account credentials view
        await show_account_credentials(mock_query, context, account_name)

    except Exception as e:
        logger.error(f"Error submitting API key config: {e}", exc_info=True)
        error_text = f"❌ Error saving configuration: {escape_markdown_v2(str(e))}"
        await bot.send_message(chat_id=chat_id, text=error_text, parse_mode="MarkdownV2")


async def delete_credential(query, context: ContextTypes.DEFAULT_TYPE, account_name: str, connector_name: str) -> None:
    """
    Delete a credential for a specific account and connector
    """
    try:
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
    encoded_account = base64.b64encode(account_name.encode()).decode()
    buttons.append(InlineKeyboardButton("❌ Cancel", callback_data=f"api_key_back_account:{encoded_account}"))

    keyboard = [buttons]
    reply_markup = InlineKeyboardMarkup(keyboard)

    return message_text, reply_markup


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


# Entry point functions for routing

async def handle_api_keys_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Entry point function that routes API key callback queries to appropriate handlers
    """
    query = update.callback_query

    if query.data == "config_api_keys":
        await show_api_keys(query, context)
    elif query.data.startswith("api_key_"):
        await handle_api_key_action(query, context)


async def handle_api_key_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Entry point function that handles text input for API key configuration
    """
    # Only process if we're awaiting API key input
    if context.user_data.get('awaiting_api_key_input'):
        await handle_api_key_config_input(update, context)
