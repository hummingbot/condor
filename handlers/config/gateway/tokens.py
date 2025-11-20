"""
Gateway token management functions
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from ._shared import logger, escape_markdown_v2, extract_network_id


async def show_tokens_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show tokens menu - select network to view tokens"""
    try:
        from servers import server_manager

        await query.answer("Loading networks...")

        client = await server_manager.get_default_client()
        response = await client.gateway.list_networks()

        networks = response.get('networks', [])

        if not networks:
            message_text = (
                "🪙 *Token Management*\n\n"
                "No networks available\\.\n\n"
                "_Ensure Gateway is running\\._"
            )
            keyboard = [[InlineKeyboardButton("« Back to Gateway", callback_data="config_gateway")]]
        else:
            # Limit to first 20 networks
            network_buttons = []
            context.user_data['token_network_list'] = networks[:20]

            for idx, network_item in enumerate(networks[:20]):
                network_id = extract_network_id(network_item)
                network_buttons.append([
                    InlineKeyboardButton(network_id, callback_data=f"gateway_token_network_{idx}")
                ])

            count_escaped = escape_markdown_v2(str(len(networks)))
            message_text = (
                f"🪙 *Token Management* \\({count_escaped} networks\\)\n\n"
                "_Select a network to view and manage tokens:_"
            )

            keyboard = network_buttons + [
                [InlineKeyboardButton("« Back to Gateway", callback_data="config_gateway")]
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing tokens menu: {e}", exc_info=True)
        error_text = f"❌ Error loading networks: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back to Gateway", callback_data="config_gateway")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def handle_token_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle token-specific actions"""
    action_data = query.data.replace("gateway_token_", "")

    if action_data.startswith("network_"):
        # Show tokens for selected network
        network_idx_str = action_data.replace("network_", "")
        try:
            network_idx = int(network_idx_str)
            network_list = context.user_data.get('token_network_list', [])
            if 0 <= network_idx < len(network_list):
                network_item = network_list[network_idx]
                network_id = extract_network_id(network_item)
                await show_network_tokens(query, context, network_id)
            else:
                await query.answer("❌ Network not found")
        except ValueError:
            await query.answer("❌ Invalid network")
    elif action_data.startswith("add_"):
        # Add token to network
        network_id = action_data.replace("add_", "")
        await prompt_add_token(query, context, network_id)
    elif action_data.startswith("remove_"):
        # Show tokens to remove from network
        network_id = action_data.replace("remove_", "")
        await prompt_remove_token(query, context, network_id)
    elif action_data.startswith("confirm_remove_"):
        # Format: confirm_remove_{network_id}_{token_address}
        parts = action_data.replace("confirm_remove_", "").split("_", 1)
        if len(parts) == 2:
            network_id, token_address = parts
            await remove_token(query, context, network_id, token_address)
    elif action_data.startswith("view_"):
        # Back to viewing tokens for network
        network_id = action_data.replace("view_", "")
        await show_network_tokens(query, context, network_id)
    else:
        await query.answer("Unknown action")


async def show_network_tokens(query, context: ContextTypes.DEFAULT_TYPE, network_id: str) -> None:
    """Show tokens for a specific network"""
    try:
        from servers import server_manager

        await query.answer("Loading tokens...")

        client = await server_manager.get_default_client()

        # Try to get tokens - the method might not exist in older versions
        try:
            if hasattr(client.gateway, 'get_network_tokens') and callable(client.gateway.get_network_tokens):
                response = await client.gateway.get_network_tokens(network_id)
                tokens = response.get('tokens', []) if response else []
            else:
                # Fallback: get tokens from network config
                config_response = await client.gateway.get_network_config(network_id)
                tokens = config_response.get('tokens', []) if config_response else []
        except Exception as e:
            logger.warning(f"Failed to get tokens for {network_id}: {e}")
            tokens = []

        network_escaped = escape_markdown_v2(network_id)

        if not tokens:
            message_text = (
                f"🪙 *{network_escaped}*\n\n"
                "_No tokens found\\._\n\n"
                "Add custom tokens to get started\\."
            )
            keyboard = [
                [InlineKeyboardButton("➕ Add Token", callback_data=f"gateway_token_add_{network_id}")],
                [InlineKeyboardButton("« Back", callback_data="gateway_tokens")]
            ]
        else:
            # Display first 15 tokens
            token_lines = []
            for idx, token in enumerate(tokens[:15], 1):
                symbol = token.get('symbol', 'N/A')
                name = token.get('name', '')
                decimals = token.get('decimals', 'N/A')
                symbol_escaped = escape_markdown_v2(str(symbol))
                name_escaped = escape_markdown_v2(str(name)) if name else ""
                decimals_escaped = escape_markdown_v2(str(decimals))

                if name:
                    token_lines.append(f"{idx}\\. *{symbol_escaped}* \\({name_escaped}\\) \\- {decimals_escaped} decimals")
                else:
                    token_lines.append(f"{idx}\\. *{symbol_escaped}* \\- {decimals_escaped} decimals")

            tokens_text = "\n".join(token_lines)
            token_count = escape_markdown_v2(str(len(tokens)))

            message_text = (
                f"🪙 *{network_escaped}*\n\n"
                f"*Tokens* \\({token_count} total\\):\n"
                f"{tokens_text}\n\n"
                "_Manage custom tokens:_"
            )

            keyboard = [
                [
                    InlineKeyboardButton("➕ Add Token", callback_data=f"gateway_token_add_{network_id}"),
                    InlineKeyboardButton("➖ Remove Token", callback_data=f"gateway_token_remove_{network_id}")
                ],
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data=f"gateway_token_view_{network_id}"),
                    InlineKeyboardButton("« Back", callback_data="gateway_tokens")
                ]
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing network tokens: {e}", exc_info=True)
        error_text = f"❌ Error loading tokens: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_tokens")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def prompt_add_token(query, context: ContextTypes.DEFAULT_TYPE, network_id: str) -> None:
    """Prompt user to enter token details"""
    try:
        network_escaped = escape_markdown_v2(network_id)

        context.user_data['awaiting_token_input'] = 'token_details'
        context.user_data['token_network'] = network_id
        context.user_data['token_message_id'] = query.message.message_id
        context.user_data['token_chat_id'] = query.message.chat_id

        message_text = (
            f"➕ *Add Token to {network_escaped}*\n\n"
            "*Enter token details in this format:*\n"
            "`address,symbol,decimals,name`\n\n"
            "*Example:*\n"
            "`9QFfgxdSqH5zT7j6rZb1y6SZhw2aFtcQu2r6BuYpump,GOLD,9,Goldcoin`\n\n"
            "⚠️ _Restart Gateway after adding for changes to take effect\\._"
        )

        keyboard = [[InlineKeyboardButton("« Cancel", callback_data=f"gateway_token_view_{network_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error prompting add token: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def prompt_remove_token(query, context: ContextTypes.DEFAULT_TYPE, network_id: str) -> None:
    """Prompt user to enter token address to remove"""
    try:
        network_escaped = escape_markdown_v2(network_id)

        context.user_data['awaiting_token_input'] = 'token_address_remove'
        context.user_data['token_network'] = network_id
        context.user_data['token_message_id'] = query.message.message_id
        context.user_data['token_chat_id'] = query.message.chat_id

        message_text = (
            f"➖ *Remove Token from {network_escaped}*\n\n"
            "*Enter the token address to remove:*\n\n"
            "*Example:*\n"
            "`9QFfgxdSqH5zT7j6rZb1y6SZhw2aFtcQu2r6BuYpump`\n\n"
            "⚠️ _Restart Gateway after removing for changes to take effect\\._"
        )

        keyboard = [[InlineKeyboardButton("« Cancel", callback_data=f"gateway_token_view_{network_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error prompting remove token: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def show_delete_token_confirmation(query, context: ContextTypes.DEFAULT_TYPE, network_id: str, token_address: str) -> None:
    """Show confirmation dialog before deleting a token"""
    try:
        from servers import server_manager

        # Get token details to show in confirmation
        client = await server_manager.get_default_client()

        # Try to get tokens - the method might not exist in older versions
        try:
            if hasattr(client.gateway, 'get_network_tokens') and callable(client.gateway.get_network_tokens):
                response = await client.gateway.get_network_tokens(network_id)
                tokens = response.get('tokens', []) if response else []
            else:
                # Fallback: get tokens from network config
                config_response = await client.gateway.get_network_config(network_id)
                tokens = config_response.get('tokens', []) if config_response else []
        except Exception as e:
            logger.warning(f"Failed to get tokens for {network_id}: {e}")
            tokens = []

        # Find the token to get its details
        token_info = next((t for t in tokens if t.get('address') == token_address), None)

        network_escaped = escape_markdown_v2(network_id)
        addr_display = token_address[:10] + "..." + token_address[-8:] if len(token_address) > 20 else token_address
        addr_escaped = escape_markdown_v2(addr_display)

        message_text = (
            f"🗑 *Delete Token*\n\n"
            f"Network: *{network_escaped}*\n"
        )

        if token_info:
            symbol = token_info.get('symbol', 'Unknown')
            symbol_escaped = escape_markdown_v2(symbol)
            message_text += f"Token: *{symbol_escaped}*\n"

        message_text += (
            f"Address: `{addr_escaped}`\n\n"
            f"⚠️ This will remove the token from *{network_escaped}*\\.\n"
            "You will need to restart the Gateway for changes to take effect\\.\n\n"
            "Are you sure you want to delete this token?"
        )

        keyboard = [
            [InlineKeyboardButton("✅ Yes, Delete", callback_data=f"gateway_token_confirm_remove_{network_id}_{token_address}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"gateway_token_view_{network_id}")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error showing delete confirmation: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def remove_token(query, context: ContextTypes.DEFAULT_TYPE, network_id: str, token_address: str) -> None:
    """Remove a token from Gateway"""
    try:
        from servers import server_manager

        await query.answer("Removing token...")

        client = await server_manager.get_default_client()
        await client.gateway.delete_token(network_id=network_id, token_address=token_address)

        network_escaped = escape_markdown_v2(network_id)
        addr_display = token_address[:10] + "..." + token_address[-8:] if len(token_address) > 20 else token_address
        addr_escaped = escape_markdown_v2(addr_display)

        success_text = (
            f"✅ *Token Removed*\n\n"
            f"`{addr_escaped}`\n\n"
            f"Removed from {network_escaped}\n\n"
            "⚠️ _Restart Gateway for changes to take effect\\._"
        )

        keyboard = [[InlineKeyboardButton("« Back to Tokens", callback_data=f"gateway_token_view_{network_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            success_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

        import asyncio
        await asyncio.sleep(2)
        await show_network_tokens(query, context, network_id)

    except Exception as e:
        logger.error(f"Error removing token: {e}", exc_info=True)
        error_text = f"❌ Error removing token: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data=f"gateway_token_view_{network_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def handle_token_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text input during token addition/removal flow"""
    awaiting_field = context.user_data.get('awaiting_token_input')
    if not awaiting_field:
        return

    # Delete user's input message
    try:
        await update.message.delete()
    except:
        pass

    try:
        from servers import server_manager
        from types import SimpleNamespace

        network_id = context.user_data.get('token_network')
        message_id = context.user_data.get('token_message_id')
        chat_id = context.user_data.get('token_chat_id')

        if awaiting_field == 'token_details':
            # Parse token details: address,symbol,decimals,name
            token_input = update.message.text.strip()
            parts = [p.strip() for p in token_input.split(',')]

            if len(parts) < 3:
                await update.get_bot().send_message(
                    chat_id=chat_id,
                    text="❌ Invalid format. Use: address,symbol,decimals,name"
                )
                return

            address = parts[0]
            symbol = parts[1]
            decimals = int(parts[2])
            name = parts[3] if len(parts) > 3 else None

            # Clear context
            context.user_data.pop('awaiting_token_input', None)
            context.user_data.pop('token_network', None)
            context.user_data.pop('token_message_id', None)
            context.user_data.pop('token_chat_id', None)

            # Show adding message
            network_escaped = escape_markdown_v2(network_id)
            symbol_escaped = escape_markdown_v2(symbol)

            if message_id and chat_id:
                await update.get_bot().edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"⏳ *Adding Token {symbol_escaped}*\n\nTo {network_escaped}\n\n_Please wait\\.\\.\\._",
                    parse_mode="MarkdownV2"
                )

            try:
                client = await server_manager.get_default_client()
                await client.gateway.add_token(
                    network_id=network_id,
                    address=address,
                    symbol=symbol,
                    decimals=decimals,
                    name=name
                )

                success_text = (
                    f"✅ *Token Added Successfully*\n\n"
                    f"*{symbol_escaped}* added to {network_escaped}\n\n"
                    "⚠️ _Restart Gateway for changes to take effect\\._"
                )

                if message_id and chat_id:
                    await update.get_bot().edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=success_text,
                        parse_mode="MarkdownV2"
                    )

                # Wait then refresh
                import asyncio
                await asyncio.sleep(2)

                mock_message = SimpleNamespace(
                    edit_text=lambda text, parse_mode=None, reply_markup=None: update.get_bot().edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text,
                        parse_mode=parse_mode,
                        reply_markup=reply_markup
                    ),
                    chat_id=chat_id,
                    message_id=message_id
                )
                mock_query = SimpleNamespace(
                    message=mock_message,
                    answer=lambda text="": None
                )
                await show_network_tokens(mock_query, context, network_id)

            except Exception as e:
                logger.error(f"Error adding token: {e}", exc_info=True)
                error_text = f"❌ Error adding token: {escape_markdown_v2(str(e))}"
                if message_id and chat_id:
                    await update.get_bot().edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=error_text,
                        parse_mode="MarkdownV2"
                    )

        elif awaiting_field == 'token_address_remove':
            # Parse token address to remove
            token_address = update.message.text.strip()

            # Clear context
            context.user_data.pop('awaiting_token_input', None)
            context.user_data.pop('token_network', None)
            context.user_data.pop('token_message_id', None)
            context.user_data.pop('token_chat_id', None)

            # Create mock query and call confirmation dialog
            mock_message = SimpleNamespace(
                edit_text=lambda text, parse_mode=None, reply_markup=None: update.get_bot().edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                ),
                chat_id=chat_id,
                message_id=message_id
            )
            mock_query = SimpleNamespace(
                message=mock_message,
                answer=lambda text="": None
            )
            await show_delete_token_confirmation(mock_query, context, network_id, token_address)

    except Exception as e:
        logger.error(f"Error handling token input: {e}", exc_info=True)
        context.user_data.pop('awaiting_token_input', None)
