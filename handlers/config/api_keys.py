"""
API Keys configuration management handlers
"""

import asyncio
import base64
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from utils.auth import restricted
from utils.telegram_formatters import escape_markdown_v2

from .server_context import build_config_message_header, format_server_selection_needed
from .user_preferences import get_active_server

logger = logging.getLogger(__name__)

# Default account name used for all API key operations
DEFAULT_ACCOUNT = "master_account"


@restricted
async def keys_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /keys command - show API keys configuration directly."""
    from handlers import clear_all_input_states
    from utils.telegram_helpers import create_mock_query_from_message

    clear_all_input_states(context)
    mock_query = await create_mock_query_from_message(update, "Loading API keys...")
    await show_api_keys(mock_query, context)


async def get_default_account(client) -> str:
    """
    Get the default account to use for API key operations.
    Returns the first available account from the backend, or DEFAULT_ACCOUNT if none exist.
    """
    try:
        accounts = await client.accounts.list_accounts()
        if accounts:
            return str(accounts[0])
    except Exception:
        pass
    return DEFAULT_ACCOUNT


async def show_api_keys(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show API keys configuration with Perpetual/Spot connector selection
    """
    # Clear bots state to prevent bots handler from intercepting API key input
    # This is needed when navigating here from Grid Strike or PMM wizards
    context.user_data.pop("bots_state", None)

    try:
        from config_manager import get_config_manager

        servers = get_config_manager().list_servers()

        if not servers:
            message_text = format_server_selection_needed()
            keyboard = [[InlineKeyboardButton("« Close", callback_data="config_close")]]
        else:
            # Build header with server context
            chat_id = query.message.chat_id
            header, server_online, _ = await build_config_message_header(
                "🔑 API Keys",
                include_gateway=False,
                chat_id=chat_id,
                user_data=context.user_data,
            )

            if not server_online:
                message_text = (
                    header + "⚠️ _Server is offline\\. Cannot manage API keys\\._"
                )
                keyboard = [
                    [InlineKeyboardButton("« Close", callback_data="config_close")]
                ]
            else:
                # Get client from per-chat server
                client = await get_config_manager().get_client_for_chat(
                    chat_id, preferred_server=get_active_server(context.user_data)
                )

                # Get the default account to use
                account_name = await get_default_account(client)

                # Get credentials for the account
                try:
                    credentials = await client.accounts.list_account_credentials(
                        account_name=account_name
                    )
                    cred_list = credentials if credentials else []
                except Exception as e:
                    logger.warning(f"Failed to get credentials for {account_name}: {e}")
                    cred_list = []

                # Separate credentials into perpetual and spot
                perp_creds = [c for c in cred_list if c.endswith("_perpetual")]
                spot_creds = [c for c in cred_list if not c.endswith("_perpetual")]

                # Store credentials in context for callback handling
                context.user_data["api_key_current_account"] = account_name
                context.user_data["api_key_credentials"] = cred_list

                # Build keyboard with credential buttons for deletion
                keyboard = []

                # Add perpetual credential buttons
                if perp_creds:
                    for i, cred in enumerate(perp_creds):
                        # Store index for lookup
                        keyboard.append(
                            [
                                InlineKeyboardButton(
                                    f"📈 {cred}", callback_data=f"api_key_manage:{i}"
                                )
                            ]
                        )

                # Add spot credential buttons
                if spot_creds:
                    for i, cred in enumerate(spot_creds):
                        # Offset index by perp count
                        idx = len(perp_creds) + i
                        keyboard.append(
                            [
                                InlineKeyboardButton(
                                    f"💱 {cred}", callback_data=f"api_key_manage:{idx}"
                                )
                            ]
                        )

                # Build message text
                if cred_list:
                    creds_display = "_Tap a key to manage it\\._\n\n"
                else:
                    creds_display = "_No exchanges connected yet\\._\n\n"

                message_text = (
                    header + creds_display + "_Select exchange type to add a new key:_"
                )

                # Add type selection buttons
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            "➕ Perpetual", callback_data="api_key_type:perpetual"
                        ),
                        InlineKeyboardButton(
                            "➕ Spot", callback_data="api_key_type:spot"
                        ),
                    ]
                )
                keyboard.append(
                    [InlineKeyboardButton("« Close", callback_data="config_close")]
                )

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Only edit if content changed to avoid Telegram error
        try:
            await query.message.edit_text(
                message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
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
        keyboard = [[InlineKeyboardButton("« Close", callback_data="config_close")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            error_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def show_connectors_by_type(
    query, context: ContextTypes.DEFAULT_TYPE, connector_type: str
) -> None:
    """
    Show connectors filtered by type (perpetual or spot)
    """
    try:
        from config_manager import get_config_manager

        chat_id = query.message.chat_id
        is_perpetual = connector_type == "perpetual"
        type_label = "Perpetual" if is_perpetual else "Spot"
        type_emoji = "📈" if is_perpetual else "💱"

        # Build header with server context
        header, server_online, _ = await build_config_message_header(
            f"🔑 {type_emoji} {type_label} Exchanges",
            include_gateway=False,
            chat_id=chat_id,
            user_data=context.user_data,
        )

        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )

        # Get the default account to use
        account_name = await get_default_account(client)
        context.user_data["api_key_current_account"] = account_name

        # Get credentials for the account
        try:
            credentials = await client.accounts.list_account_credentials(
                account_name=account_name
            )
            cred_list = credentials if credentials else []
        except Exception as e:
            logger.warning(f"Failed to get credentials for {account_name}: {e}")
            cred_list = []

        # Filter credentials by type
        if is_perpetual:
            type_creds = [c for c in cred_list if c.endswith("_perpetual")]
        else:
            type_creds = [c for c in cred_list if not c.endswith("_perpetual")]

        # Store credentials in context for delete functionality
        context.user_data["api_key_credentials"] = type_creds
        context.user_data["api_key_connector_type"] = connector_type

        keyboard = []

        if type_creds:
            # Build list of connected credentials with delete buttons
            cred_lines = ["*Connected:*"]
            for i, cred in enumerate(type_creds):
                cred_escaped = escape_markdown_v2(str(cred))
                cred_lines.append(f"  ✅ {cred_escaped}")
                # Add delete button for each credential
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            f"🗑 Delete {cred}", callback_data=f"api_key_delete_cred:{i}"
                        )
                    ]
                )
            creds_display = "\n".join(cred_lines) + "\n\n"
        else:
            creds_display = "_No exchanges connected yet\\._\n\n"

        message_text = header + creds_display + "_Select an exchange to configure:_\n"

        # Get list of available connectors
        all_connectors = await client.connectors.list_connectors()

        # Filter out testnet connectors and gateway connectors (those with '/' like "uniswap/ethereum")
        connectors = [
            c for c in all_connectors if "testnet" not in c.lower() and "/" not in c
        ]

        # Filter by type
        if is_perpetual:
            connectors = [c for c in connectors if c.endswith("_perpetual")]
        else:
            connectors = [c for c in connectors if not c.endswith("_perpetual")]

        # Store connector list in context
        context.user_data["api_key_connectors"] = connectors

        # Create connector buttons
        connector_buttons = []
        for i, connector in enumerate(connectors):
            # Use index instead of full names to keep callback_data short
            connector_buttons.append(
                InlineKeyboardButton(connector, callback_data=f"api_key_connector:{i}")
            )

        # Organize into rows of 2 columns for better readability
        connector_button_rows = []
        for i in range(0, len(connector_buttons), 2):
            connector_button_rows.append(connector_buttons[i : i + 2])

        keyboard = (
            keyboard
            + connector_button_rows
            + [
                [
                    InlineKeyboardButton(
                        "« Back", callback_data="api_key_back_to_accounts"
                    )
                ]
            ]
        )

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing connectors by type: {e}", exc_info=True)
        error_text = f"❌ Error loading connectors: {escape_markdown_v2(str(e))}"
        keyboard = [
            [InlineKeyboardButton("« Back", callback_data="api_key_back_to_accounts")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            error_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def handle_api_key_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle API key specific actions
    """
    action_data = query.data.replace("api_key_", "")

    if action_data.startswith("type:"):
        # Handle perpetual/spot selection
        connector_type = action_data.replace("type:", "")
        await show_connectors_by_type(query, context, connector_type)
    elif action_data.startswith("account:"):
        # Legacy handler - redirect to main API keys view
        await show_api_keys(query, context)
    elif action_data.startswith("connector:"):
        # Format: connector:{index}
        # Retrieve connector from context (use account from context)
        try:
            connector_index = int(action_data.replace("connector:", ""))
            connectors = context.user_data.get("api_key_connectors", [])
            account_name = context.user_data.get(
                "api_key_current_account", DEFAULT_ACCOUNT
            )

            if 0 <= connector_index < len(connectors):
                connector_name = connectors[connector_index]
                await show_connector_config(
                    query, context, account_name, connector_name
                )
            else:
                logger.error(f"Invalid connector index or missing context data")
                await query.answer("❌ Session expired, please try again")
        except (ValueError, IndexError) as e:
            logger.error(f"Failed to parse connector index: {e}")
            await query.answer("❌ Invalid connector data")
    elif action_data == "config_back":
        # Handle back button during API key configuration
        config_data = context.user_data.get("api_key_config_data", {})
        all_fields = config_data.get("fields", [])
        current_field = context.user_data.get("awaiting_api_key_input")

        if current_field and current_field in all_fields:
            current_index = all_fields.index(current_field)
            if current_index > 0:
                # Go to previous field
                previous_field = all_fields[current_index - 1]

                # Remove the previous field's value to re-enter it
                values = config_data.get("values", {})
                values.pop(previous_field, None)
                config_data["values"] = values
                context.user_data["api_key_config_data"] = config_data

                # Update awaiting field
                context.user_data["awaiting_api_key_input"] = previous_field
                await query.answer("« Going back")
                await _update_api_key_config_message(context, query.message.get_bot())
        else:
            await query.answer("Cannot go back")
    elif action_data.startswith("back_account:"):
        # Legacy handler - clear state and redirect to main API keys view
        context.user_data.pop("configuring_api_key", None)
        context.user_data.pop("awaiting_api_key_input", None)
        context.user_data.pop("api_key_config_data", None)
        await show_api_keys(query, context)
    elif action_data == "back_to_accounts":
        await show_api_keys(query, context)
    elif action_data.startswith("manage:"):
        # Show manage options for a credential (delete)
        try:
            cred_index = int(action_data.replace("manage:", ""))
            credentials = context.user_data.get("api_key_credentials", [])
            account_name = context.user_data.get(
                "api_key_current_account", DEFAULT_ACCOUNT
            )

            if 0 <= cred_index < len(credentials):
                connector_name = credentials[cred_index]
                await show_credential_manage_menu(
                    query, context, cred_index, connector_name
                )
            else:
                await query.answer("❌ Session expired, please try again")
        except (ValueError, IndexError) as e:
            logger.error(f"Failed to parse credential index: {e}")
            await query.answer("❌ Invalid credential data")
    elif action_data.startswith("delete_cred:"):
        # Handle credential deletion (use account from context)
        try:
            cred_index = int(action_data.replace("delete_cred:", ""))
            credentials = context.user_data.get("api_key_credentials", [])
            account_name = context.user_data.get(
                "api_key_current_account", DEFAULT_ACCOUNT
            )

            if 0 <= cred_index < len(credentials):
                connector_name = credentials[cred_index]
                # Show confirmation dialog
                await show_delete_credential_confirmation(
                    query, context, account_name, connector_name
                )
            else:
                logger.error(f"Invalid credential index or missing context data")
                await query.answer("❌ Session expired, please try again")
        except (ValueError, IndexError) as e:
            logger.error(f"Failed to parse credential index: {e}")
            await query.answer("❌ Invalid credential data")
    elif action_data.startswith("delete_cred_confirm:"):
        # Confirm credential deletion (use account from context)
        try:
            cred_index = int(action_data.replace("delete_cred_confirm:", ""))
            credentials = context.user_data.get("api_key_credentials", [])
            account_name = context.user_data.get(
                "api_key_current_account", DEFAULT_ACCOUNT
            )

            if 0 <= cred_index < len(credentials):
                connector_name = credentials[cred_index]
                await delete_credential(query, context, account_name, connector_name)
            else:
                await query.answer("❌ Session expired, please try again")
        except Exception as e:
            logger.error(f"Failed to delete credential: {e}")
            await query.answer("❌ Failed to delete credential")
    elif action_data == "delete_cred_cancel":
        # Cancel credential deletion - go back to keys menu
        await show_api_keys(query, context)
    elif action_data.startswith("select:"):
        # Handle Literal type option selection
        selected_value = action_data.replace("select:", "")
        await _handle_field_value_selection(query, context, selected_value)
    elif action_data == "skip":
        # Skip optional field
        await _handle_skip_optional_field(query, context)
    else:
        await query.answer("Unknown action")


async def show_connector_config(
    query, context: ContextTypes.DEFAULT_TYPE, account_name: str, connector_name: str
) -> None:
    """
    Start progressive configuration flow for a specific connector
    """
    try:
        from config_manager import get_config_manager

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )

        # Get config map for this connector
        try:
            config_map = await client.connectors.get_config_map(connector_name)
        except Exception as config_err:
            # Fallback for older backends that don't support config-map endpoint
            logger.warning(
                f"Failed to get config map for {connector_name}, using defaults: {config_err}"
            )
            config_map = None

        # Support both old format (list) and new format (dict with metadata)
        if config_map is None:
            # Fallback: use default fields for most exchange connectors
            config_fields = [
                f"{connector_name}_api_key",
                f"{connector_name}_api_secret",
            ]
            field_metadata = {
                f"{connector_name}_api_key": {"type": "SecretStr", "required": True},
                f"{connector_name}_api_secret": {"type": "SecretStr", "required": True},
            }
        elif isinstance(config_map, dict):
            # New format: dict with field metadata
            config_fields = list(config_map.keys())
            field_metadata = config_map
        else:
            # Old format: list of field names
            config_fields = config_map
            field_metadata = {}

        # Filter out fields that should be handled automatically
        if connector_name == "xrpl":
            # custom_markets expects a dict, we'll default to empty
            config_fields = [f for f in config_fields if f != "custom_markets"]
            field_metadata.pop("custom_markets", None)

        # Determine connector type for back navigation
        connector_type = (
            "perpetual" if connector_name.endswith("_perpetual") else "spot"
        )

        # Initialize context storage for API key configuration
        context.user_data["configuring_api_key"] = True
        context.user_data["api_key_config_data"] = {
            "account_name": account_name,
            "connector_name": connector_name,
            "connector_type": connector_type,
            "fields": config_fields,
            "field_metadata": field_metadata,
            "values": {},
        }
        context.user_data["awaiting_api_key_input"] = (
            config_fields[0] if config_fields else None
        )
        context.user_data["api_key_message_id"] = query.message.message_id
        context.user_data["api_key_chat_id"] = query.message.chat_id

        if not config_fields:
            # No configuration needed
            connector_escaped = escape_markdown_v2(connector_name)
            message_text = (
                f"🔑 *Configure {connector_escaped}*\n\n"
                "✅ No configuration required for this connector\\."
            )
            keyboard = [
                [
                    InlineKeyboardButton(
                        "« Back", callback_data=f"api_key_type:{connector_type}"
                    )
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text(
                message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
            return

        # Show first field
        message_text, reply_markup = _build_api_key_config_message(
            context.user_data["api_key_config_data"], config_fields[0], config_fields
        )

        await query.message.edit_text(
            message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error showing connector config: {e}", exc_info=True)
        error_text = f"❌ Error loading connector config: {escape_markdown_v2(str(e))}"
        connector_type = (
            "perpetual" if connector_name.endswith("_perpetual") else "spot"
        )
        keyboard = [
            [
                InlineKeyboardButton(
                    "« Back", callback_data=f"api_key_type:{connector_type}"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            error_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def handle_api_key_config_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle text input during API key configuration flow
    """
    # Only process if we're awaiting API key input
    awaiting_field = context.user_data.get("awaiting_api_key_input")
    if not awaiting_field:
        return

    # Store chat_id if not already stored
    if "api_key_chat_id" not in context.user_data:
        context.user_data["api_key_chat_id"] = update.effective_chat.id

    # Delete the user's input message to keep chat clean (especially for secrets)
    try:
        await update.message.delete()
    except:
        pass

    try:
        new_value = update.message.text.strip()
        config_data = context.user_data.get("api_key_config_data", {})
        values = config_data.get("values", {})
        all_fields = config_data.get("fields", [])
        field_metadata = config_data.get("field_metadata", {})

        # Get field metadata for validation
        field_meta = field_metadata.get(awaiting_field, {})
        field_type = field_meta.get("type", "")

        # Validate and convert based on field type
        if field_type == "Literal":
            allowed_values = field_meta.get("allowed_values", [])
            if allowed_values and new_value not in allowed_values:
                # Send error message and don't advance
                error_msg = await update.effective_chat.send_message(
                    f"❌ Invalid value. Please select one of: {', '.join(allowed_values)}"
                )
                # Auto-delete error message after 3 seconds
                await asyncio.sleep(3)
                try:
                    await error_msg.delete()
                except:
                    pass
                return
        elif field_type == "bool":
            # Convert string to boolean
            lower_val = new_value.lower()
            if lower_val in ("true", "yes", "1"):
                new_value = True
            elif lower_val in ("false", "no", "0"):
                new_value = False
            else:
                error_msg = await update.effective_chat.send_message(
                    "❌ Invalid value. Please enter 'true' or 'false'"
                )
                await asyncio.sleep(3)
                try:
                    await error_msg.delete()
                except:
                    pass
                return
        elif field_type == "int":
            try:
                new_value = int(new_value)
            except ValueError:
                error_msg = await update.effective_chat.send_message(
                    "❌ Invalid value. Please enter an integer number"
                )
                await asyncio.sleep(3)
                try:
                    await error_msg.delete()
                except:
                    pass
                return
        elif field_type == "float":
            try:
                new_value = float(new_value)
            except ValueError:
                error_msg = await update.effective_chat.send_message(
                    "❌ Invalid value. Please enter a number"
                )
                await asyncio.sleep(3)
                try:
                    await error_msg.delete()
                except:
                    pass
                return

        # Store the value
        values[awaiting_field] = new_value
        config_data["values"] = values
        context.user_data["api_key_config_data"] = config_data

        # Move to next field or show confirmation
        current_index = all_fields.index(awaiting_field)

        if current_index < len(all_fields) - 1:
            # Move to next field
            context.user_data["awaiting_api_key_input"] = all_fields[current_index + 1]
            await _update_api_key_config_message(context, update.get_bot())
        else:
            # All fields filled - submit configuration
            context.user_data["awaiting_api_key_input"] = None
            await submit_api_key_config(
                context, update.get_bot(), update.effective_chat.id
            )

    except Exception as e:
        logger.error(f"Error handling API key config input: {e}", exc_info=True)
        context.user_data.pop("awaiting_api_key_input", None)
        context.user_data.pop("configuring_api_key", None)
        context.user_data.pop("api_key_config_data", None)


async def submit_api_key_config(
    context: ContextTypes.DEFAULT_TYPE, bot, chat_id: int
) -> None:
    """
    Submit the API key configuration to Hummingbot
    """
    try:
        from config_manager import get_config_manager

        config_data = context.user_data.get("api_key_config_data", {})
        account_name = config_data.get("account_name")
        connector_name = config_data.get("connector_name")
        connector_type = config_data.get("connector_type", "spot")
        values = config_data.get("values", {})
        message_id = context.user_data.get("api_key_message_id")

        if not account_name or not connector_name or not values:
            await bot.send_message(
                chat_id=chat_id, text="❌ Missing configuration data"
            )
            return

        # Show "waiting for connection" message
        connector_escaped = escape_markdown_v2(connector_name)
        waiting_message_text = (
            f"⏳ *Connecting to {connector_escaped}*\n\n"
            "Please wait while we verify your credentials\\.\\.\\."
        )

        if message_id and chat_id:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=waiting_message_text,
                parse_mode="MarkdownV2",
            )

        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )

        # Handle special cases for certain connectors
        if connector_name == "xrpl":
            # XRPL connector expects custom_markets as a dict, default to empty
            if "custom_markets" not in values or values.get("custom_markets") is None:
                values["custom_markets"] = {}

        # Add credentials using the accounts API
        await client.accounts.add_credential(
            account_name=account_name, connector_name=connector_name, credentials=values
        )

        # Store connector type before clearing context
        saved_connector_type = connector_type

        # Clear context data
        context.user_data.pop("configuring_api_key", None)
        context.user_data.pop("awaiting_api_key_input", None)
        context.user_data.pop("api_key_config_data", None)
        context.user_data.pop("api_key_message_id", None)
        context.user_data.pop("api_key_chat_id", None)

        # Show brief success message
        connector_escaped = escape_markdown_v2(connector_name)
        success_text = f"✅ *{connector_escaped}* connected successfully\\!"

        if message_id and chat_id:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=success_text,
                parse_mode="MarkdownV2",
            )
        else:
            await bot.send_message(
                chat_id=chat_id, text=success_text, parse_mode="MarkdownV2"
            )

        # Create a mock query object to navigate back to connector type view
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
                reply_markup=reply_markup,
            ),
            chat_id=chat_id,
            message_id=message_id,
        )
        mock_query = SimpleNamespace(message=mock_message)

        # Navigate back to connector type view
        await show_connectors_by_type(mock_query, context, saved_connector_type)

    except Exception as e:
        logger.error(f"Error submitting API key config: {e}", exc_info=True)

        # Get connector info for back button before clearing state
        config_data = context.user_data.get("api_key_config_data", {})
        connector_name = config_data.get("connector_name", "")
        connector_type = config_data.get("connector_type", "spot")
        message_id = context.user_data.get("api_key_message_id")

        # Clear context data so user can retry
        context.user_data.pop("configuring_api_key", None)
        context.user_data.pop("awaiting_api_key_input", None)
        context.user_data.pop("api_key_config_data", None)
        context.user_data.pop("api_key_message_id", None)
        context.user_data.pop("api_key_chat_id", None)

        # Build error message with more helpful text for timeout
        error_str = str(e)
        if "TimeoutError" in error_str or "timeout" in error_str.lower():
            connector_escaped = escape_markdown_v2(connector_name)
            error_text = (
                f"❌ *Connection Timeout*\n\n"
                f"Failed to verify credentials for *{connector_escaped}*\\.\n\n"
                "The exchange took too long to respond\\. "
                "Please check your API keys and try again\\."
            )
        else:
            error_text = (
                f"❌ Error saving configuration: {escape_markdown_v2(error_str)}"
            )

        # Add back button to navigate back to connector type view
        keyboard = [
            [
                InlineKeyboardButton(
                    "« Back", callback_data=f"api_key_type:{connector_type}"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Try to edit existing message, fall back to sending new message
        try:
            if message_id and chat_id:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=error_text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup,
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=error_text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup,
                )
        except Exception as msg_error:
            logger.error(f"Failed to send error message: {msg_error}")
            # Last resort: send simple message
            await bot.send_message(
                chat_id=chat_id,
                text=error_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )


async def delete_credential(
    query, context: ContextTypes.DEFAULT_TYPE, account_name: str, connector_name: str
) -> None:
    """
    Delete a credential for a specific account and connector
    """
    try:
        from config_manager import get_config_manager

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )

        # Determine connector type for back navigation
        connector_type = context.user_data.get(
            "api_key_connector_type",
            "perpetual" if connector_name.endswith("_perpetual") else "spot",
        )

        # Delete the credential
        await client.accounts.delete_credential(
            account_name=account_name, connector_name=connector_name
        )

        # Show success message
        connector_escaped = escape_markdown_v2(connector_name)
        message_text = (
            f"✅ *Credential Deleted*\n\n"
            f"The *{connector_escaped}* credentials have been removed\\."
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "« Back", callback_data=f"api_key_type:{connector_type}"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )
        await query.answer("✅ Credential deleted")

    except Exception as e:
        logger.error(f"Error deleting credential: {e}", exc_info=True)
        error_text = f"❌ Error deleting credential: {escape_markdown_v2(str(e))}"

        connector_type = context.user_data.get(
            "api_key_connector_type",
            "perpetual" if connector_name.endswith("_perpetual") else "spot",
        )
        keyboard = [
            [
                InlineKeyboardButton(
                    "« Back", callback_data=f"api_key_type:{connector_type}"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            error_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )
        await query.answer("❌ Failed to delete credential")


async def show_credential_manage_menu(
    query, context: ContextTypes.DEFAULT_TYPE, cred_index: int, connector_name: str
) -> None:
    """
    Show management options for a credential (currently just delete)
    """
    from .server_context import build_config_message_header

    chat_id = query.message.chat_id
    header, _, _ = await build_config_message_header(
        "🔑 Manage API Key",
        include_gateway=False,
        chat_id=chat_id,
        user_data=context.user_data,
    )

    # Determine type emoji
    is_perpetual = connector_name.endswith("_perpetual")
    type_emoji = "📈" if is_perpetual else "💱"
    connector_escaped = escape_markdown_v2(connector_name)

    message_text = (
        header + f"*Exchange:* {type_emoji} {connector_escaped}\n\n"
        "_What would you like to do?_"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🗑 Delete Key", callback_data=f"api_key_delete_cred:{cred_index}"
            )
        ],
        [InlineKeyboardButton("« Back", callback_data="api_key_back_to_accounts")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def show_delete_credential_confirmation(
    query, context: ContextTypes.DEFAULT_TYPE, account_name: str, connector_name: str
) -> None:
    """
    Show confirmation dialog before deleting a credential
    """
    connector_escaped = escape_markdown_v2(connector_name)

    message_text = (
        f"🗑 *Delete Credential*\n\n"
        f"Exchange: *{connector_escaped}*\n\n"
        f"⚠️ This will remove the API credentials for *{connector_escaped}*\\.\n\n"
        "Are you sure you want to delete this credential?"
    )

    # Find the index of the connector in the credentials list
    credentials = context.user_data.get("api_key_credentials", [])
    cred_index = (
        credentials.index(connector_name) if connector_name in credentials else -1
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Yes, Delete",
                callback_data=f"api_key_delete_cred_confirm:{cred_index}",
            )
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="api_key_delete_cred_cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def _handle_field_value_selection(query, context, selected_value: str) -> None:
    """
    Handle selection of a value for a Literal or bool type field via inline button
    """
    try:
        awaiting_field = context.user_data.get("awaiting_api_key_input")
        if not awaiting_field:
            await query.answer("❌ No field awaiting input")
            return

        config_data = context.user_data.get("api_key_config_data", {})
        values = config_data.get("values", {})
        all_fields = config_data.get("fields", [])
        field_metadata = config_data.get("field_metadata", {})

        # Convert value based on field type
        field_meta = field_metadata.get(awaiting_field, {})
        if field_meta.get("type") == "bool":
            selected_value = selected_value.lower() == "true"

        # Store the selected value
        values[awaiting_field] = selected_value
        config_data["values"] = values
        context.user_data["api_key_config_data"] = config_data

        # Move to next field or submit
        current_index = all_fields.index(awaiting_field)

        if current_index < len(all_fields) - 1:
            # Move to next field
            context.user_data["awaiting_api_key_input"] = all_fields[current_index + 1]
            await query.answer(f"✅ {awaiting_field} = {selected_value}")
            await _update_api_key_config_message(context, query.message.get_bot())
        else:
            # All fields filled - submit configuration
            context.user_data["awaiting_api_key_input"] = None
            await query.answer("✅ Submitting configuration...")
            chat_id = context.user_data.get("api_key_chat_id", query.message.chat_id)
            await submit_api_key_config(context, query.message.get_bot(), chat_id)

    except Exception as e:
        logger.error(f"Error handling field value selection: {e}", exc_info=True)
        await query.answer("❌ Error processing selection")


async def _handle_skip_optional_field(query, context) -> None:
    """
    Handle skipping an optional field
    """
    try:
        awaiting_field = context.user_data.get("awaiting_api_key_input")
        if not awaiting_field:
            await query.answer("❌ No field awaiting input")
            return

        config_data = context.user_data.get("api_key_config_data", {})
        all_fields = config_data.get("fields", [])
        field_metadata = config_data.get("field_metadata", {})

        # Verify field is optional
        field_meta = field_metadata.get(awaiting_field, {})
        if field_meta.get("required", True):
            await query.answer("❌ This field is required")
            return

        # Don't store a value - just move to next field
        current_index = all_fields.index(awaiting_field)

        if current_index < len(all_fields) - 1:
            # Move to next field
            context.user_data["awaiting_api_key_input"] = all_fields[current_index + 1]
            await query.answer(f"⏭ Skipped {awaiting_field}")
            await _update_api_key_config_message(context, query.message.get_bot())
        else:
            # All fields filled - submit configuration
            context.user_data["awaiting_api_key_input"] = None
            await query.answer("✅ Submitting configuration...")
            chat_id = context.user_data.get("api_key_chat_id", query.message.chat_id)
            await submit_api_key_config(context, query.message.get_bot(), chat_id)

    except Exception as e:
        logger.error(f"Error handling skip field: {e}", exc_info=True)
        await query.answer("❌ Error skipping field")


def _format_field_type_hint(field_meta: dict) -> str:
    """
    Format a human-readable type hint from field metadata
    """
    if not field_meta:
        return ""

    field_type = field_meta.get("type", "")
    required = field_meta.get("required", False)
    allowed_values = field_meta.get("allowed_values", [])

    hints = []

    # Type hint
    if field_type == "Literal" and allowed_values:
        values_str = " | ".join(allowed_values)
        hints.append(f"Options: {values_str}")
    elif field_type == "bool":
        hints.append("true/false")
    elif field_type == "SecretStr":
        hints.append("secret")
    elif field_type == "int":
        hints.append("integer")
    elif field_type == "float":
        hints.append("number")
    elif field_type:
        hints.append(field_type.lower())

    # Required hint
    if not required:
        hints.append("optional")

    return ", ".join(hints)


def _build_api_key_config_message(
    config_data: dict, current_field: str, all_fields: list
) -> tuple:
    """
    Build the progressive API key configuration message showing filled fields and current prompt
    Returns (message_text, reply_markup)
    """
    connector_name = config_data.get("connector_name", "")
    connector_type = config_data.get("connector_type", "spot")
    values = config_data.get("values", {})
    field_metadata = config_data.get("field_metadata", {})

    connector_escaped = escape_markdown_v2(connector_name)

    # Build the message showing progress
    lines = [f"🔑 *Configure {connector_escaped}*\n"]

    for field in all_fields:
        field_meta = field_metadata.get(field, {})
        field_escaped = escape_markdown_v2(field)

        if field in values:
            # Field already filled - show value (mask if contains 'secret', 'key', or 'password')
            value = values[field]
            is_secret = any(
                keyword in field.lower()
                for keyword in ["secret", "key", "password", "passphrase"]
            )
            if is_secret or field_meta.get("type") == "SecretStr":
                value = "****"
            value_escaped = escape_markdown_v2(str(value))
            lines.append(f"*{field_escaped}:* `{value_escaped}` ✅")
        elif field == current_field:
            # Current field being filled - show with type hint
            type_hint = _format_field_type_hint(field_meta)
            if type_hint:
                type_hint_escaped = escape_markdown_v2(type_hint)
                lines.append(f"*{field_escaped}:* _\\(awaiting input\\)_")
                lines.append(f"  ↳ _{type_hint_escaped}_")
            else:
                lines.append(f"*{field_escaped}:* _\\(awaiting input\\)_")
            break
        else:
            # Future field - show placeholder with optional indicator
            is_optional = field_meta and not field_meta.get("required", True)
            optional_marker = " \\(optional\\)" if is_optional else ""
            lines.append(f"*{field_escaped}:*{optional_marker} \\_\\_\\_")

    message_text = "\n".join(lines)

    # Build keyboard
    keyboard = []

    # Add option buttons for Literal and bool types
    current_field_meta = field_metadata.get(current_field, {})
    if current_field_meta.get("type") == "Literal" and current_field_meta.get(
        "allowed_values"
    ):
        allowed_values = current_field_meta["allowed_values"]
        # Create buttons for each allowed value (max 2 per row)
        option_buttons = []
        for value in allowed_values:
            option_buttons.append(
                InlineKeyboardButton(value, callback_data=f"api_key_select:{value}")
            )
        # Arrange in rows of 2
        for i in range(0, len(option_buttons), 2):
            keyboard.append(option_buttons[i : i + 2])
    elif current_field_meta.get("type") == "bool":
        # Add true/false buttons for boolean fields
        keyboard.append(
            [
                InlineKeyboardButton("✓ true", callback_data="api_key_select:true"),
                InlineKeyboardButton("✗ false", callback_data="api_key_select:false"),
            ]
        )

    # Add skip button for optional fields
    if current_field_meta and not current_field_meta.get("required", True):
        keyboard.append(
            [InlineKeyboardButton("⏭ Skip (use default)", callback_data="api_key_skip")]
        )

    # Build navigation buttons
    buttons = []

    # Add back button if not on first field
    current_index = (
        all_fields.index(current_field) if current_field in all_fields else 0
    )
    if current_index > 0:
        buttons.append(
            InlineKeyboardButton("« Back", callback_data="api_key_config_back")
        )

    # Always add cancel button - navigate back to connector type view
    buttons.append(
        InlineKeyboardButton(
            "❌ Cancel", callback_data=f"api_key_type:{connector_type}"
        )
    )

    keyboard.append(buttons)
    reply_markup = InlineKeyboardMarkup(keyboard)

    return message_text, reply_markup


async def _update_api_key_config_message(
    context: ContextTypes.DEFAULT_TYPE, bot
) -> None:
    """
    Update the API key configuration message with current progress
    """
    config_data = context.user_data.get("api_key_config_data", {})
    current_field = context.user_data.get("awaiting_api_key_input")
    message_id = context.user_data.get("api_key_message_id")
    chat_id = context.user_data.get("api_key_chat_id")

    if not message_id or not chat_id or not current_field:
        return

    all_fields = config_data.get("fields", [])
    message_text, reply_markup = _build_api_key_config_message(
        config_data, current_field, all_fields
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
        logger.error(f"Error updating API key config message: {e}")


# Entry point functions for routing


async def handle_api_keys_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Entry point function that routes API key callback queries to appropriate handlers
    """
    query = update.callback_query

    if query.data == "config_api_keys":
        await show_api_keys(query, context)
    elif query.data.startswith("api_key_"):
        await handle_api_key_action(query, context)


async def handle_api_key_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Entry point function that handles text input for API key configuration
    """
    # Only process if we're awaiting API key input
    if context.user_data.get("awaiting_api_key_input"):
        await handle_api_key_config_input(update, context)
