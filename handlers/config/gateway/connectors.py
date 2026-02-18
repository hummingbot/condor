"""
Gateway connector management functions
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..user_preferences import get_active_server
from ._shared import escape_markdown_v2, logger


async def show_connectors_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show DEX connectors configuration menu"""
    try:
        from config_manager import get_config_manager

        await query.answer("Loading connectors...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )
        response = await client.gateway.list_connectors()

        connectors = response.get("connectors", [])

        if not connectors:
            message_text = (
                "🔌 *DEX Connectors*\n\n"
                "No connectors available\\.\n\n"
                "_Ensure Gateway is running\\._"
            )
            keyboard = [
                [InlineKeyboardButton("« Back", callback_data="config_gateway")]
            ]
        else:
            message_text = (
                "🔌 *DEX Connectors*\n\n"
                "_Update the config of any of these connectors:_"
            )

            # Organize connector buttons in rows of 3
            connector_buttons = []
            current_row = []

            for connector in connectors:
                connector_name = connector.get("name", "unknown")
                current_row.append(
                    InlineKeyboardButton(
                        connector_name,
                        callback_data=f"gateway_connector_view_{connector_name}",
                    )
                )

                # Add row when we have 3 buttons
                if len(current_row) == 3:
                    connector_buttons.append(current_row)
                    current_row = []

            # Add remaining buttons if any
            if current_row:
                connector_buttons.append(current_row)

            # Add refresh and back buttons in the last row
            keyboard = connector_buttons + [
                [
                    InlineKeyboardButton(
                        "🔄 Refresh", callback_data="gateway_connectors"
                    ),
                    InlineKeyboardButton("« Back", callback_data="config_gateway"),
                ]
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await query.message.edit_text(
                message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
            )
        except Exception as e:
            if "Message is not modified" in str(e):
                await query.answer("✅ Already up to date")
            else:
                raise

    except Exception as e:
        logger.error(f"Error showing connectors: {e}", exc_info=True)
        error_text = f"❌ Error loading connectors: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="config_gateway")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            error_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def handle_connector_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle connector-specific actions"""
    action_data = query.data.replace("gateway_connector_", "")

    if action_data.startswith("view_"):
        connector_name = action_data.replace("view_", "")
        await show_connector_details(query, context, connector_name)
    elif action_data.startswith("edit_"):
        connector_name = action_data.replace("edit_", "")
        await start_connector_config_edit(query, context, connector_name)
    elif action_data == "config_back":
        # Handle back button during connector configuration
        await handle_connector_config_back(query, context)
    elif action_data == "config_keep":
        # Handle keep current value button during connector configuration
        await handle_connector_config_keep(query, context)
    elif action_data.startswith("cancel_edit_"):
        connector_name = action_data.replace("cancel_edit_", "")
        # Clear config state and go back to connector details
        context.user_data.pop("configuring_connector", None)
        context.user_data.pop("awaiting_connector_input", None)
        context.user_data.pop("connector_config_data", None)
        await show_connector_details(query, context, connector_name)
    else:
        await query.answer("Unknown action")


async def show_connector_details(
    query, context: ContextTypes.DEFAULT_TYPE, connector_name: str
) -> None:
    """Show details and configuration for a specific connector"""
    try:
        from config_manager import get_config_manager

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )
        response = await client.gateway.get_connector_config(connector_name)

        # Try to extract config - it might be directly in response or nested under 'config'
        if isinstance(response, dict):
            # If response has a 'config' key, use that; otherwise use the whole response
            config = (
                response.get("config", response) if "config" in response else response
            )
        else:
            config = {}

        # Filter out metadata fields
        config_fields = {
            k: v for k, v in config.items() if k not in ["status", "message", "error"]
        }

        name_escaped = escape_markdown_v2(connector_name)

        # Build configuration display
        config_lines = []
        for key, value in config_fields.items():
            key_escaped = escape_markdown_v2(str(key))
            value_str = str(value)
            # Mask sensitive values like API keys
            if (
                "key" in key.lower()
                or "secret" in key.lower()
                or "password" in key.lower()
            ):
                if value_str and value_str.strip():
                    value_str = "***" if value_str else ""
            value_escaped = escape_markdown_v2(value_str)
            config_lines.append(f"• *{key_escaped}:* `{value_escaped}`")

        if config_lines:
            config_text = "\n".join(config_lines)
        else:
            config_text = "_No configuration available_"

        message_text = (
            f"🔌 *Connector: {name_escaped}*\n\n"
            "*Configuration:*\n"
            f"{config_text}\n\n"
            "_Click Edit to modify these settings\\._"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "✏️ Edit Configuration",
                    callback_data=f"gateway_connector_edit_{connector_name}",
                )
            ],
            [
                InlineKeyboardButton(
                    "« Back to Connectors", callback_data="gateway_connectors"
                )
            ],
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing connector details: {e}", exc_info=True)
        error_text = f"❌ Error loading connector: {escape_markdown_v2(str(e))}"
        keyboard = [
            [InlineKeyboardButton("« Back", callback_data="gateway_connectors")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            error_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def start_connector_config_edit(
    query, context: ContextTypes.DEFAULT_TYPE, connector_name: str
) -> None:
    """Start progressive configuration editing flow for a connector"""
    try:
        from config_manager import get_config_manager

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )
        response = await client.gateway.get_connector_config(connector_name)

        # Extract config
        if isinstance(response, dict):
            config = (
                response.get("config", response) if "config" in response else response
            )
        else:
            config = {}

        # Filter out metadata fields
        config_fields = {
            k: v for k, v in config.items() if k not in ["status", "message", "error"]
        }
        field_names = list(config_fields.keys())

        if not field_names:
            await query.answer("❌ No configurable fields found")
            return

        # Initialize context storage for connector configuration
        context.user_data["configuring_connector"] = True
        context.user_data["connector_config_data"] = {
            "connector_name": connector_name,
            "fields": field_names,
            "current_values": config_fields.copy(),
            "new_values": {},
        }
        context.user_data["awaiting_connector_input"] = field_names[0]
        context.user_data["connector_message_id"] = query.message.message_id
        context.user_data["connector_chat_id"] = query.message.chat_id

        # Show first field
        message_text, reply_markup = _build_connector_config_message(
            context.user_data["connector_config_data"], field_names[0], field_names
        )

        await query.message.edit_text(
            message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error starting connector config edit: {e}", exc_info=True)
        error_text = f"❌ Error loading configuration: {escape_markdown_v2(str(e))}"
        keyboard = [
            [InlineKeyboardButton("« Back", callback_data="gateway_connectors")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            error_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def handle_connector_config_back(
    query, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle back button during connector configuration"""
    config_data = context.user_data.get("connector_config_data", {})
    all_fields = config_data.get("fields", [])
    current_field = context.user_data.get("awaiting_connector_input")

    if current_field and current_field in all_fields:
        current_index = all_fields.index(current_field)
        if current_index > 0:
            # Go to previous field
            previous_field = all_fields[current_index - 1]

            # Remove the previous field's new value to re-enter it
            new_values = config_data.get("new_values", {})
            new_values.pop(previous_field, None)
            config_data["new_values"] = new_values
            context.user_data["connector_config_data"] = config_data

            # Update awaiting field
            context.user_data["awaiting_connector_input"] = previous_field
            await query.answer("« Going back")
            await _update_connector_config_message(context, query.message.get_bot())
        else:
            await query.answer("Cannot go back")
    else:
        await query.answer("Cannot go back")


async def handle_connector_config_keep(
    query, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle keep current value button during connector configuration"""
    try:
        awaiting_field = context.user_data.get("awaiting_connector_input")
        if not awaiting_field:
            await query.answer("No field to keep")
            return

        config_data = context.user_data.get("connector_config_data", {})
        new_values = config_data.get("new_values", {})
        all_fields = config_data.get("fields", [])
        current_values = config_data.get("current_values", {})

        # Use the current value
        current_val = current_values.get(awaiting_field)
        new_values[awaiting_field] = current_val
        config_data["new_values"] = new_values
        context.user_data["connector_config_data"] = config_data

        await query.answer("✓ Keeping current value")

        # Move to next field or show confirmation
        current_index = all_fields.index(awaiting_field)

        if current_index < len(all_fields) - 1:
            # Move to next field
            context.user_data["awaiting_connector_input"] = all_fields[
                current_index + 1
            ]
            await _update_connector_config_message(context, query.message.get_bot())
        else:
            # All fields filled - submit configuration
            context.user_data["awaiting_connector_input"] = None
            await submit_connector_config(
                context, query.message.get_bot(), query.message.chat_id
            )

    except Exception as e:
        logger.error(f"Error handling keep current value: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def handle_connector_config_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle text input during connector configuration flow"""
    awaiting_field = context.user_data.get("awaiting_connector_input")
    if not awaiting_field:
        return

    # Delete user's input message for clean chat
    try:
        await update.message.delete()
    except:
        pass

    try:
        new_value = update.message.text.strip()
        config_data = context.user_data.get("connector_config_data", {})
        new_values = config_data.get("new_values", {})
        all_fields = config_data.get("fields", [])
        current_values = config_data.get("current_values", {})

        # Check for special keywords to keep empty/None values
        if new_value.lower() in ["none", "null", "empty", ""]:
            # Keep the current value (which might be empty/None)
            new_value = current_values.get(awaiting_field)
        else:
            # Convert value to appropriate type based on current value
            current_val = current_values.get(awaiting_field)
            try:
                if isinstance(current_val, bool):
                    # Handle boolean conversion
                    new_value = new_value.lower() in ["true", "1", "yes", "y", "on"]
                elif isinstance(current_val, int):
                    new_value = int(new_value)
                elif isinstance(current_val, float):
                    new_value = float(new_value)
                # else keep as string
            except ValueError:
                # If conversion fails, keep as string
                pass

        # Store the new value
        new_values[awaiting_field] = new_value
        config_data["new_values"] = new_values
        context.user_data["connector_config_data"] = config_data

        # Move to next field or show confirmation
        current_index = all_fields.index(awaiting_field)

        if current_index < len(all_fields) - 1:
            # Move to next field
            context.user_data["awaiting_connector_input"] = all_fields[
                current_index + 1
            ]
            await _update_connector_config_message(context, update.get_bot())
        else:
            # All fields filled - submit configuration
            context.user_data["awaiting_connector_input"] = None
            await submit_connector_config(
                context, update.get_bot(), update.effective_chat.id
            )

    except Exception as e:
        logger.error(f"Error handling connector config input: {e}", exc_info=True)
        context.user_data.pop("awaiting_connector_input", None)
        context.user_data.pop("configuring_connector", None)
        context.user_data.pop("connector_config_data", None)


async def submit_connector_config(
    context: ContextTypes.DEFAULT_TYPE, bot, chat_id: int
) -> None:
    """Submit the connector configuration to Gateway"""
    try:
        from config_manager import get_config_manager

        config_data = context.user_data.get("connector_config_data", {})
        connector_name = config_data.get("connector_name")
        new_values = config_data.get("new_values", {})
        current_values = config_data.get("current_values", {})
        message_id = context.user_data.get("connector_message_id")

        if not connector_name or not new_values:
            await bot.send_message(
                chat_id=chat_id, text="❌ Missing configuration data"
            )
            return

        # Merge current values with new values (only changed fields)
        final_config = current_values.copy()
        final_config.update(new_values)

        # Show "saving configuration" message
        connector_escaped = escape_markdown_v2(connector_name)
        waiting_message_text = (
            f"⏳ *Updating {connector_escaped}*\n\n"
            "Please wait while we save your configuration\\.\\.\\."
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

        # Update configuration using the gateway API
        await client.gateway.update_connector_config(connector_name, final_config)

        # Clear context data
        context.user_data.pop("configuring_connector", None)
        context.user_data.pop("awaiting_connector_input", None)
        context.user_data.pop("connector_config_data", None)
        context.user_data.pop("connector_message_id", None)
        context.user_data.pop("connector_chat_id", None)

        # Show brief success message
        success_text = f"✅ *{connector_escaped}* updated successfully\\!"

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

        # Create a mock query object to reuse the existing show_connector_details function
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
                reply_markup=reply_markup,
            ),
            chat_id=chat_id,
            message_id=message_id,
        )
        mock_query = SimpleNamespace(message=mock_message)

        # Navigate back to connector details view
        await show_connector_details(mock_query, context, connector_name)

    except Exception as e:
        logger.error(f"Error submitting connector config: {e}", exc_info=True)
        error_text = f"❌ Error saving configuration: {escape_markdown_v2(str(e))}"
        await bot.send_message(
            chat_id=chat_id, text=error_text, parse_mode="MarkdownV2"
        )


def _build_connector_config_message(
    config_data: dict, current_field: str, all_fields: list
) -> tuple:
    """
    Build the progressive connector configuration message
    Returns (message_text, reply_markup)
    """
    connector_name = config_data.get("connector_name", "")
    current_values = config_data.get("current_values", {})
    new_values = config_data.get("new_values", {})

    connector_escaped = escape_markdown_v2(connector_name)

    # Build the message showing progress
    lines = [f"✏️ *Edit {connector_escaped}*\n"]

    for field in all_fields:
        if field in new_values:
            # Field already filled with new value - show it
            value = new_values[field]
            # Mask sensitive values
            if (
                "key" in field.lower()
                or "secret" in field.lower()
                or "password" in field.lower()
            ):
                value = "***" if value else ""
            field_escaped = escape_markdown_v2(field)
            value_escaped = escape_markdown_v2(str(value))
            lines.append(f"*{field_escaped}:* `{value_escaped}` ✅")
        elif field == current_field:
            # Current field being filled - show current value as default
            current_val = current_values.get(field, "")
            # Mask sensitive values
            if (
                "key" in field.lower()
                or "secret" in field.lower()
                or "password" in field.lower()
            ):
                current_val = "***" if current_val else ""
            field_escaped = escape_markdown_v2(field)
            current_escaped = escape_markdown_v2(str(current_val))
            lines.append(f"*{field_escaped}:* _\\(current: `{current_escaped}`\\)_")
            lines.append("_Enter new value or same to keep:_")
            break
        else:
            # Future field - show current value
            current_val = current_values.get(field, "")
            if (
                "key" in field.lower()
                or "secret" in field.lower()
                or "password" in field.lower()
            ):
                current_val = "***" if current_val else ""
            field_escaped = escape_markdown_v2(field)
            current_escaped = escape_markdown_v2(str(current_val))
            lines.append(f"*{field_escaped}:* `{current_escaped}`")

    message_text = "\n".join(lines)

    # Build keyboard with back and cancel buttons
    buttons = []

    # Get current value for "Keep current" button
    current_val = current_values.get(current_field, "")

    # Always add "Keep current" button (even for empty/None values)
    keep_buttons = []

    # Check if value is empty, None, or null-like
    is_empty = (
        current_val is None
        or current_val == ""
        or str(current_val).lower() in ["none", "null"]
    )

    if is_empty:
        # Show "Keep empty" for empty values
        button_text = "Keep empty"
    elif (
        "key" in current_field.lower()
        or "secret" in current_field.lower()
        or "password" in current_field.lower()
    ):
        # Don't show the actual value if it's sensitive
        button_text = "Keep current: ***"
    else:
        # Truncate long values
        display_val = str(current_val)
        if len(display_val) > 20:
            display_val = display_val[:17] + "..."
        button_text = f"Keep: {display_val}"

    keep_buttons.append(
        InlineKeyboardButton(button_text, callback_data="gateway_connector_config_keep")
    )
    buttons = [keep_buttons]

    # Add back button if not on first field
    current_index = (
        all_fields.index(current_field) if current_field in all_fields else 0
    )
    nav_buttons = []
    if current_index > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                "« Back", callback_data="gateway_connector_config_back"
            )
        )

    # Always add cancel button
    nav_buttons.append(
        InlineKeyboardButton(
            "❌ Cancel", callback_data=f"gateway_connector_cancel_edit_{connector_name}"
        )
    )

    if nav_buttons:
        buttons.append(nav_buttons)

    keyboard = buttons
    reply_markup = InlineKeyboardMarkup(keyboard)

    return message_text, reply_markup


async def _update_connector_config_message(
    context: ContextTypes.DEFAULT_TYPE, bot
) -> None:
    """Update the connector configuration message with current progress"""
    config_data = context.user_data.get("connector_config_data", {})
    current_field = context.user_data.get("awaiting_connector_input")
    message_id = context.user_data.get("connector_message_id")
    chat_id = context.user_data.get("connector_chat_id")

    if not message_id or not chat_id or not current_field:
        return

    all_fields = config_data.get("fields", [])
    message_text, reply_markup = _build_connector_config_message(
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
        logger.error(f"Error updating connector config message: {e}")
