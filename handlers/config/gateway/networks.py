"""
Gateway network management functions
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from ._shared import logger, escape_markdown_v2, extract_network_id


async def show_networks_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show networks configuration menu"""
    try:
        from servers import server_manager

        await query.answer("Loading networks...")

        client = await server_manager.get_default_client()
        response = await client.gateway.list_networks()

        networks = response.get('networks', [])

        if not networks:
            message_text = (
                "🌍 *Networks*\n\n"
                "No networks available\\.\n\n"
                "_Ensure Gateway is running\\._"
            )
            keyboard = [[InlineKeyboardButton("« Back", callback_data="config_gateway")]]
        else:
            # Group networks by chain if possible
            network_buttons = []
            network_count = len(networks)

            # Store networks in context for retrieval by index
            context.user_data['network_list'] = networks[:20]

            # Create buttons in 2 columns
            row = []
            for idx, network_item in enumerate(networks[:20]):  # Limit to first 20 to avoid message size issues
                network_id = extract_network_id(network_item)
                # Use index-based callback to avoid exceeding 64-byte limit
                button = InlineKeyboardButton(network_id, callback_data=f"gateway_network_view_{idx}")
                row.append(button)

                # Add row when we have 2 buttons
                if len(row) == 2:
                    network_buttons.append(row)
                    row = []

            # Add any remaining button (odd number of networks)
            if row:
                network_buttons.append(row)

            count_escaped = escape_markdown_v2(str(network_count))
            message_text = (
                f"🌍 *Networks* \\({count_escaped} available\\)\n\n"
                "_Click on a network to view and configure settings\\._"
            )

            keyboard = network_buttons + [
                [InlineKeyboardButton("« Back", callback_data="config_gateway")]
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing networks: {e}", exc_info=True)
        error_text = f"❌ Error loading networks: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="config_gateway")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def handle_network_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle network-specific actions"""
    action_data = query.data.replace("gateway_network_", "")

    if action_data.startswith("view_"):
        # Get network by index from context
        network_idx_str = action_data.replace("view_", "")
        try:
            network_idx = int(network_idx_str)
            network_list = context.user_data.get('network_list', [])
            if 0 <= network_idx < len(network_list):
                network = network_list[network_idx]
                # Extract network_id from dict if needed
                if isinstance(network, dict):
                    network_id = network.get('network_id', str(network))
                else:
                    network_id = str(network)
                await show_network_details(query, context, network_id)
            else:
                await query.answer("❌ Network not found")
        except ValueError:
            # Fallback for old-style callback data
            network_id = network_idx_str
            await show_network_details(query, context, network_id)
    elif action_data == "edit_config":
        # Start network configuration editing
        network_id = context.user_data.get('current_network_id')
        if network_id:
            await start_network_config_edit(query, context, network_id)
        else:
            await query.answer("❌ Network not found")
    elif action_data == "config_keep":
        await handle_network_config_keep(query, context)
    elif action_data == "config_back":
        await handle_network_config_back(query, context)
    elif action_data == "config_cancel":
        await handle_network_config_cancel(query, context)
    else:
        await query.answer("Unknown action")


async def show_network_details(query, context: ContextTypes.DEFAULT_TYPE, network_id: str) -> None:
    """Show details and configuration for a specific network"""
    try:
        from servers import server_manager

        client = await server_manager.get_default_client()
        response = await client.gateway.get_network_config(network_id)

        # Try to extract config - it might be directly in response or nested under 'config'
        if isinstance(response, dict):
            # If response has a 'config' key, use that; otherwise use the whole response
            config = response.get('config', response) if 'config' in response else response
        else:
            config = {}

        network_escaped = escape_markdown_v2(network_id)

        # Build configuration display
        config_lines = []
        for key, value in config.items():
            key_escaped = escape_markdown_v2(str(key))
            # Truncate long values like URLs
            value_str = str(value)
            if len(value_str) > 50:
                value_str = value_str[:47] + "..."
            value_escaped = escape_markdown_v2(value_str)
            config_lines.append(f"• *{key_escaped}:* `{value_escaped}`")

        if config_lines:
            config_text = "\n".join(config_lines)
        else:
            config_text = "_No configuration available_"

        message_text = (
            f"🌍 *Network: {network_escaped}*\n\n"
            "*Configuration:*\n"
            f"{config_text}"
        )

        # Store network_id in context for edit action
        context.user_data['current_network_id'] = network_id

        keyboard = [
            [InlineKeyboardButton("✏️ Edit Configuration", callback_data=f"gateway_network_edit_config")],
            [InlineKeyboardButton("« Back to Networks", callback_data="gateway_networks")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing network details: {e}", exc_info=True)
        error_text = f"❌ Error loading network: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_networks")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def start_network_config_edit(query, context: ContextTypes.DEFAULT_TYPE, network_id: str) -> None:
    """Start progressive configuration editing flow for a network"""
    try:
        from servers import server_manager

        client = await server_manager.get_default_client()
        response = await client.gateway.get_network_config(network_id)

        # Try to extract config - it might be directly in response or nested under 'config'
        if isinstance(response, dict):
            # If response has a 'config' key, use that; otherwise use the whole response
            config = response.get('config', response) if 'config' in response else response
        else:
            config = {}

        # Filter out metadata fields
        config_fields = {k: v for k, v in config.items() if k not in ['status', 'message', 'error']}
        field_names = list(config_fields.keys())

        if not field_names:
            await query.answer("❌ No configurable fields found")
            return

        # Initialize context storage for network configuration
        context.user_data['configuring_network'] = True
        context.user_data['network_config_data'] = {
            'network_id': network_id,
            'fields': field_names,
            'current_values': config_fields.copy(),
            'new_values': {}
        }
        context.user_data['awaiting_network_input'] = field_names[0]
        context.user_data['network_message_id'] = query.message.message_id
        context.user_data['network_chat_id'] = query.message.chat_id

        # Show first field
        message_text, reply_markup = _build_network_config_message(
            context.user_data['network_config_data'],
            field_names[0],
            field_names
        )

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error starting network config edit: {e}", exc_info=True)
        error_text = f"❌ Error loading configuration: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_networks")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def handle_network_config_back(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle back button during network configuration"""
    config_data = context.user_data.get('network_config_data', {})
    all_fields = config_data.get('fields', [])
    current_field = context.user_data.get('awaiting_network_input')

    if current_field and current_field in all_fields:
        current_index = all_fields.index(current_field)
        if current_index > 0:
            # Go to previous field
            previous_field = all_fields[current_index - 1]

            # Remove the previous field's new value to re-enter it
            new_values = config_data.get('new_values', {})
            new_values.pop(previous_field, None)
            config_data['new_values'] = new_values
            context.user_data['network_config_data'] = config_data

            # Update awaiting field
            context.user_data['awaiting_network_input'] = previous_field
            await query.answer("« Going back")
            await _update_network_config_message(context, query.message.get_bot())
        else:
            await query.answer("Cannot go back")
    else:
        await query.answer("Cannot go back")


async def handle_network_config_keep(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle keep current value button during network configuration"""
    try:
        awaiting_field = context.user_data.get('awaiting_network_input')
        if not awaiting_field:
            await query.answer("No field to keep")
            return

        config_data = context.user_data.get('network_config_data', {})
        new_values = config_data.get('new_values', {})
        all_fields = config_data.get('fields', [])
        current_values = config_data.get('current_values', {})

        # Use the current value
        current_val = current_values.get(awaiting_field)
        new_values[awaiting_field] = current_val
        config_data['new_values'] = new_values
        context.user_data['network_config_data'] = config_data

        await query.answer("✓ Keeping current value")

        # Move to next field or show confirmation
        current_index = all_fields.index(awaiting_field)

        if current_index < len(all_fields) - 1:
            # Move to next field
            context.user_data['awaiting_network_input'] = all_fields[current_index + 1]
            await _update_network_config_message(context, query.message.get_bot())
        else:
            # All fields filled - submit configuration
            context.user_data['awaiting_network_input'] = None
            await submit_network_config(context, query.message.get_bot(), query.message.chat_id)

    except Exception as e:
        logger.error(f"Error handling keep current value: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def handle_network_config_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text input during network configuration flow"""
    awaiting_field = context.user_data.get('awaiting_network_input')
    if not awaiting_field:
        return

    # Delete user's input message for clean chat
    try:
        await update.message.delete()
    except:
        pass

    try:
        new_value = update.message.text.strip()
        config_data = context.user_data.get('network_config_data', {})
        new_values = config_data.get('new_values', {})
        all_fields = config_data.get('fields', [])
        current_values = config_data.get('current_values', {})

        # Convert value to appropriate type based on current value
        current_val = current_values.get(awaiting_field)
        try:
            if isinstance(current_val, bool):
                # Handle boolean conversion
                new_value = new_value.lower() in ['true', '1', 'yes', 'y', 'on']
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
        config_data['new_values'] = new_values
        context.user_data['network_config_data'] = config_data

        # Move to next field or show confirmation
        current_index = all_fields.index(awaiting_field)

        if current_index < len(all_fields) - 1:
            # Move to next field
            context.user_data['awaiting_network_input'] = all_fields[current_index + 1]
            await _update_network_config_message(context, update.get_bot())
        else:
            # All fields filled - submit configuration
            context.user_data['awaiting_network_input'] = None
            await submit_network_config(context, update.get_bot(), update.effective_chat.id)

    except Exception as e:
        logger.error(f"Error handling network config input: {e}", exc_info=True)
        context.user_data.pop('awaiting_network_input', None)
        context.user_data.pop('configuring_network', None)
        context.user_data.pop('network_config_data', None)


async def submit_network_config(context: ContextTypes.DEFAULT_TYPE, bot, chat_id: int) -> None:
    """Submit the network configuration to Gateway"""
    try:
        from servers import server_manager

        config_data = context.user_data.get('network_config_data', {})
        network_id = config_data.get('network_id')
        new_values = config_data.get('new_values', {})
        current_values = config_data.get('current_values', {})
        message_id = context.user_data.get('network_message_id')

        if not network_id or not new_values:
            await bot.send_message(chat_id=chat_id, text="❌ Missing configuration data")
            return

        # Merge current values with new values (only changed fields)
        final_config = current_values.copy()
        final_config.update(new_values)

        # Show "saving configuration" message
        saving_text = f"💾 Saving configuration for {escape_markdown_v2(network_id)}\\.\\.\\."
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=saving_text,
                parse_mode="MarkdownV2"
            )
        except:
            pass

        # Clear configuration state
        context.user_data.pop('configuring_network', None)
        context.user_data.pop('network_config_data', None)
        context.user_data.pop('awaiting_network_input', None)

        # Submit configuration to Gateway
        client = await server_manager.get_default_client()
        await client.gateway.update_network_config(network_id, final_config)

        success_text = f"✅ Configuration saved for {escape_markdown_v2(network_id)}\\!"

        # Create a mock query object to reuse the existing show_network_details function
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

        # Navigate back to network details view
        await show_network_details(mock_query, context, network_id)

    except Exception as e:
        logger.error(f"Error submitting network config: {e}", exc_info=True)
        error_text = f"❌ Error saving configuration: {escape_markdown_v2(str(e))}"
        await bot.send_message(chat_id=chat_id, text=error_text, parse_mode="MarkdownV2")


def _build_network_config_message(config_data: dict, current_field: str, all_fields: list) -> tuple:
    """
    Build the progressive network configuration message
    Returns (message_text, reply_markup)
    """
    network_id = config_data.get('network_id', '')
    current_values = config_data.get('current_values', {})
    new_values = config_data.get('new_values', {})

    network_escaped = escape_markdown_v2(network_id)

    # Build the message showing progress
    lines = [f"✏️ *Edit {network_escaped}*\n"]

    for field in all_fields:
        if field in new_values:
            # Field already filled with new value - show it
            value = new_values[field]
            # Mask sensitive values
            if 'key' in field.lower() or 'secret' in field.lower() or 'password' in field.lower():
                value = '***' if value else ''
            field_escaped = escape_markdown_v2(field)
            value_escaped = escape_markdown_v2(str(value))
            lines.append(f"*{field_escaped}:* `{value_escaped}` ✅")
        elif field == current_field:
            # Current field being filled - show current value as default
            current_val = current_values.get(field, '')
            # Mask sensitive values
            if 'key' in field.lower() or 'secret' in field.lower() or 'password' in field.lower():
                current_val = '***' if current_val else ''
            field_escaped = escape_markdown_v2(field)
            current_escaped = escape_markdown_v2(str(current_val))
            lines.append(f"*{field_escaped}:* _\\(current: `{current_escaped}`\\)_")
            lines.append("_Enter new value or keep current:_")
            break
        else:
            # Future field - show current value
            current_val = current_values.get(field, '')
            if 'key' in field.lower() or 'secret' in field.lower() or 'password' in field.lower():
                current_val = '***' if current_val else ''
            field_escaped = escape_markdown_v2(field)
            current_escaped = escape_markdown_v2(str(current_val))
            lines.append(f"*{field_escaped}:* `{current_escaped}`")

    message_text = "\n".join(lines)

    # Build keyboard with back and cancel buttons
    buttons = []

    # Get current value for "Keep current" button
    current_val = current_values.get(current_field, '')

    # Always add "Keep current" button (even for empty/None values)
    keep_buttons = []

    # Check if value is empty, None, or null-like
    is_empty = current_val is None or current_val == '' or str(current_val).lower() in ['none', 'null']

    if is_empty:
        # Show "Keep empty" for empty values
        button_text = "Keep empty"
    elif 'key' in current_field.lower() or 'secret' in current_field.lower() or 'password' in current_field.lower():
        # Don't show the actual value if it's sensitive
        button_text = "Keep current: ***"
    else:
        # Truncate long values
        display_val = str(current_val)
        if len(display_val) > 20:
            display_val = display_val[:17] + "..."
        button_text = f"Keep: {display_val}"

    keep_buttons.append(InlineKeyboardButton(button_text, callback_data="gateway_network_config_keep"))
    buttons.append(keep_buttons)

    # Back button (only if not on first field)
    current_index = all_fields.index(current_field)
    if current_index > 0:
        buttons.append([InlineKeyboardButton("« Back", callback_data="gateway_network_config_back")])

    # Cancel button
    buttons.append([InlineKeyboardButton("✖️ Cancel", callback_data="gateway_network_config_cancel")])

    reply_markup = InlineKeyboardMarkup(buttons)
    return (message_text, reply_markup)


async def _update_network_config_message(context: ContextTypes.DEFAULT_TYPE, bot) -> None:
    """Update the network config message with the current field"""
    try:
        config_data = context.user_data.get('network_config_data', {})
        all_fields = config_data.get('fields', [])
        current_field = context.user_data.get('awaiting_network_input')

        if not current_field or not all_fields:
            return

        message_text, reply_markup = _build_network_config_message(
            config_data,
            current_field,
            all_fields
        )

        message_id = context.user_data.get('network_message_id')
        chat_id = context.user_data.get('network_chat_id')

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error updating network config message: {e}", exc_info=True)


async def handle_network_config_cancel(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle cancel button during network configuration"""
    try:
        config_data = context.user_data.get('network_config_data', {})
        network_id = config_data.get('network_id', '')

        # Clear configuration state
        context.user_data.pop('configuring_network', None)
        context.user_data.pop('network_config_data', None)
        context.user_data.pop('awaiting_network_input', None)

        await query.answer("✖️ Configuration cancelled")

        # Return to network details
        if network_id:
            await show_network_details(query, context, network_id)
        else:
            await show_networks_menu(query, context)

    except Exception as e:
        logger.error(f"Error handling cancel: {e}", exc_info=True)
        await query.answer("Error cancelling configuration")
