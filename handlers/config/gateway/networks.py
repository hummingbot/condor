"""
Gateway network management functions
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from ._shared import logger, escape_markdown_v2, extract_network_id
from ..user_preferences import get_active_server


async def show_networks_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show networks configuration menu"""
    try:
        from config_manager import get_config_manager

        await query.answer("Loading networks...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(chat_id, preferred_server=get_active_server(context.user_data))
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
    elif action_data == "config_cancel":
        await handle_network_config_cancel(query, context)
    else:
        await query.answer("Unknown action")


async def show_network_details(query, context: ContextTypes.DEFAULT_TYPE, network_id: str) -> None:
    """Show network config in edit mode - user can copy/paste to change values"""
    try:
        from config_manager import get_config_manager

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(chat_id, preferred_server=get_active_server(context.user_data))
        response = await client.gateway.get_network_config(network_id)

        # Try to extract config - it might be directly in response or nested under 'config'
        if isinstance(response, dict):
            config = response.get('config', response) if 'config' in response else response
        else:
            config = {}

        # Filter out metadata fields
        config_fields = {k: v for k, v in config.items() if k not in ['status', 'message', 'error']}

        network_escaped = escape_markdown_v2(network_id)

        if not config_fields:
            message_text = (
                f"🌍 *Network: {network_escaped}*\n\n"
                "_No configuration available_"
            )
            keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_networks")]]
        else:
            # Build copyable config for editing
            config_lines = []
            for key, value in config_fields.items():
                config_lines.append(f"{key}={value}")

            config_text = "\n".join(config_lines)

            message_text = (
                f"🌍 *{network_escaped}*\n\n"
                f"```\n{config_text}\n```\n\n"
                f"✏️ _Send `key=value` to update_"
            )

            # Set up editing state
            context.user_data['configuring_network'] = True
            context.user_data['network_config_data'] = {
                'network_id': network_id,
                'current_values': config_fields.copy(),
            }
            context.user_data['awaiting_network_input'] = 'bulk_edit'
            context.user_data['network_message_id'] = query.message.message_id
            context.user_data['network_chat_id'] = query.message.chat_id

            keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_networks")]]

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


async def handle_network_config_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text input during network configuration - parses key=value lines"""
    awaiting_field = context.user_data.get('awaiting_network_input')
    if awaiting_field != 'bulk_edit':
        return

    # Delete user's input message for clean chat
    try:
        await update.message.delete()
    except:
        pass

    try:
        input_text = update.message.text.strip()
        config_data = context.user_data.get('network_config_data', {})
        current_values = config_data.get('current_values', {})

        # Parse key=value lines
        updates = {}
        errors = []

        for line in input_text.split('\n'):
            line = line.strip()
            if not line or '=' not in line:
                continue

            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip()

            # Validate key exists in config
            if key not in current_values:
                errors.append(f"Unknown key: {key}")
                continue

            # Convert value to appropriate type based on current value
            current_val = current_values.get(key)
            try:
                if isinstance(current_val, bool):
                    value = value.lower() in ['true', '1', 'yes', 'y', 'on']
                elif isinstance(current_val, int):
                    value = int(value)
                elif isinstance(current_val, float):
                    value = float(value)
            except ValueError:
                pass  # Keep as string

            updates[key] = value

        if errors:
            # Show errors but don't cancel
            error_msg = "⚠️ " + ", ".join(errors)
            await update.get_bot().send_message(
                chat_id=update.effective_chat.id,
                text=error_msg
            )

        if not updates:
            await update.get_bot().send_message(
                chat_id=update.effective_chat.id,
                text="❌ No valid updates found. Use format: key=value"
            )
            return

        # Store updates and submit
        config_data['new_values'] = updates
        context.user_data['network_config_data'] = config_data
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
        from config_manager import get_config_manager

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
        client = await get_config_manager().get_client_for_chat(chat_id, preferred_server=get_active_server(context.user_data))
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
