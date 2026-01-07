"""
Gateway token management functions
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from geckoterminal_py import GeckoTerminalAsyncClient

from ._shared import logger, escape_markdown_v2, extract_network_id


# Gateway network ID -> GeckoTerminal network ID mapping
NETWORK_TO_GECKO = {
    "solana": "solana",
    "solana-mainnet-beta": "solana",
    "ethereum": "eth",
    "ethereum-mainnet": "eth",
    "arbitrum": "arbitrum",
    "arbitrum-mainnet": "arbitrum",
    "base": "base",
    "base-mainnet": "base",
    "polygon": "polygon_pos",
    "polygon-mainnet": "polygon_pos",
    "avalanche": "avax",
    "avalanche-mainnet": "avax",
    "optimism": "optimism",
    "optimism-mainnet": "optimism",
}


async def show_tokens_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show tokens menu - select network to view tokens"""
    try:
        from config_manager import get_config_manager

        await query.answer("Loading networks...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(chat_id)
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
        # Ignore "message not modified" errors - they're harmless
        if "not modified" in str(e).lower():
            logger.debug(f"Message not modified (ignored): {e}")
            return
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
        # Show token list to manage (edit/remove)
        network_id = action_data.replace("remove_", "")
        await prompt_remove_token(query, context, network_id)
    elif action_data.startswith("select_"):
        # Show options for selected token
        try:
            token_idx = int(action_data.replace("select_", ""))
            await show_token_options(query, context, token_idx)
        except ValueError:
            await query.answer("❌ Invalid token")
    elif action_data.startswith("edit_"):
        # Edit selected token
        try:
            token_idx = int(action_data.replace("edit_", ""))
            await prompt_edit_token(query, context, token_idx)
        except ValueError:
            await query.answer("❌ Invalid token")
    elif action_data.startswith("del_"):
        # Delete selected token (show confirmation)
        try:
            token_idx = int(action_data.replace("del_", ""))
            tokens = context.user_data.get('token_manage_list', [])
            network_id = context.user_data.get('token_manage_network')
            if tokens and token_idx < len(tokens) and network_id:
                token = tokens[token_idx]
                token_address = token.get('address', '')
                await show_delete_token_confirmation(query, context, network_id, token_address)
            else:
                await query.answer("❌ Token not found")
        except ValueError:
            await query.answer("❌ Invalid token")
    elif action_data == "confirm_remove":
        # Get token info from user_data (stored to avoid 64-byte callback limit)
        pending_delete = context.user_data.get('pending_token_delete')
        if pending_delete:
            network_id = pending_delete['network_id']
            token_address = pending_delete['token_address']
            context.user_data.pop('pending_token_delete', None)  # Clean up
            await remove_token(query, context, network_id, token_address)
        else:
            await query.answer("❌ Token deletion expired. Please try again.")
    elif action_data.startswith("view_"):
        # Back to viewing tokens for network
        network_id = action_data.replace("view_", "")
        await show_network_tokens(query, context, network_id)
    elif action_data.startswith("page_"):
        # Handle pagination
        try:
            page = int(action_data.replace("page_", ""))
            network_id = context.user_data.get('token_view_network')
            if network_id:
                await show_network_tokens(query, context, network_id, page=page)
            else:
                await query.answer("❌ Network not found")
        except ValueError:
            await query.answer("❌ Invalid page")
    else:
        await query.answer("Unknown action")


async def show_network_tokens(query, context: ContextTypes.DEFAULT_TYPE, network_id: str, page: int = 0) -> None:
    """Show tokens for a specific network with button grid and pagination"""
    TOKENS_PER_PAGE = 16
    COLUMNS = 4

    try:
        from config_manager import get_config_manager

        await query.answer("Loading tokens...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(chat_id)

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
            # Store all tokens for selection
            context.user_data['token_manage_list'] = tokens
            context.user_data['token_manage_network'] = network_id
            context.user_data['token_view_page'] = page

            # Calculate pagination
            total_tokens = len(tokens)
            total_pages = (total_tokens + TOKENS_PER_PAGE - 1) // TOKENS_PER_PAGE
            page = max(0, min(page, total_pages - 1))  # Clamp page to valid range

            start_idx = page * TOKENS_PER_PAGE
            end_idx = min(start_idx + TOKENS_PER_PAGE, total_tokens)
            page_tokens = tokens[start_idx:end_idx]

            # Build page indicator
            if total_pages > 1:
                page_indicator = f" \\[{page + 1}/{total_pages}\\]"
            else:
                page_indicator = ""

            token_count = escape_markdown_v2(str(total_tokens))
            message_text = (
                f"🪙 *{network_escaped}*{page_indicator}\n\n"
                f"*Tokens* \\({token_count} total\\)\n"
                "_Select a token to edit or remove:_"
            )

            # Build token buttons in grid (4 columns)
            keyboard = []
            row = []
            for idx, token in enumerate(page_tokens):
                global_idx = start_idx + idx
                symbol = token.get('symbol', '?')[:6]  # Truncate long symbols
                row.append(InlineKeyboardButton(symbol, callback_data=f"gateway_token_select_{global_idx}"))

                if len(row) == COLUMNS:
                    keyboard.append(row)
                    row = []

            # Add remaining buttons if any
            if row:
                keyboard.append(row)

            # Add pagination buttons if needed
            if total_pages > 1:
                nav_buttons = []
                if page > 0:
                    nav_buttons.append(InlineKeyboardButton("« Prev", callback_data=f"gateway_token_page_{page - 1}"))
                if page < total_pages - 1:
                    nav_buttons.append(InlineKeyboardButton("Next »", callback_data=f"gateway_token_page_{page + 1}"))
                if nav_buttons:
                    keyboard.append(nav_buttons)

            # Action buttons
            keyboard.append([
                InlineKeyboardButton("➕ Add Token", callback_data=f"gateway_token_add_{network_id}"),
                InlineKeyboardButton("🔄 Refresh", callback_data=f"gateway_token_view_{network_id}")
            ])
            keyboard.append([InlineKeyboardButton("« Back", callback_data="gateway_tokens")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        # Ignore "message not modified" errors - they're harmless
        if "not modified" in str(e).lower():
            logger.debug(f"Message not modified (ignored): {e}")
            return
        logger.error(f"Error showing network tokens: {e}", exc_info=True)
        error_text = f"❌ Error loading tokens: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_tokens")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def prompt_add_token(query, context: ContextTypes.DEFAULT_TYPE, network_id: str) -> None:
    """Prompt user to enter token details"""
    try:
        network_escaped = escape_markdown_v2(network_id)

        # Clear any lingering states from previous operations
        context.user_data.pop('dex_state', None)
        context.user_data.pop('cex_state', None)

        context.user_data['awaiting_token_input'] = 'token_details'
        context.user_data['token_network'] = network_id
        context.user_data['token_message_id'] = query.message.message_id
        context.user_data['token_chat_id'] = query.message.chat_id

        message_text = (
            f"➕ *Add Token to {network_escaped}*\n\n"
            "*Option 1:* Just paste the token address\n"
            "_\\(details will be fetched automatically\\)_\n\n"
            "*Option 2:* Full format\n"
            "`address,symbol,decimals,name`\n\n"
            "*Example:*\n"
            "`9QFfgxdSqH5zT7j6rZb1y6SZhw2aFtcQu2r6BuYpump`\n\n"
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
    """Show list of tokens to select for editing or removal"""
    try:
        from config_manager import get_config_manager

        await query.answer("Loading tokens...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(chat_id)

        # Get tokens for the network
        try:
            if hasattr(client.gateway, 'get_network_tokens') and callable(client.gateway.get_network_tokens):
                response = await client.gateway.get_network_tokens(network_id)
                tokens = response.get('tokens', []) if response else []
            else:
                config_response = await client.gateway.get_network_config(network_id)
                tokens = config_response.get('tokens', []) if config_response else []
        except Exception as e:
            logger.warning(f"Failed to get tokens for {network_id}: {e}")
            tokens = []

        network_escaped = escape_markdown_v2(network_id)

        if not tokens:
            message_text = (
                f"🪙 *Manage Tokens \\- {network_escaped}*\n\n"
                "_No tokens found to manage\\._"
            )
            keyboard = [[InlineKeyboardButton("« Back", callback_data=f"gateway_token_view_{network_id}")]]
        else:
            # Store tokens in user_data for later retrieval
            context.user_data['token_manage_list'] = tokens[:20]
            context.user_data['token_manage_network'] = network_id

            # Create buttons for each token (limit to 20)
            token_buttons = []
            for idx, token in enumerate(tokens[:20]):
                symbol = token.get('symbol', 'Unknown')
                token_buttons.append([
                    InlineKeyboardButton(f"{symbol}", callback_data=f"gateway_token_select_{idx}")
                ])

            count_escaped = escape_markdown_v2(str(len(tokens)))
            message_text = (
                f"🪙 *Manage Tokens \\- {network_escaped}*\n\n"
                f"_Select a token to edit or remove \\({count_escaped} total\\):_"
            )

            keyboard = token_buttons + [
                [InlineKeyboardButton("« Back", callback_data=f"gateway_token_view_{network_id}")]
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing token list: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def show_token_options(query, context: ContextTypes.DEFAULT_TYPE, token_idx: int) -> None:
    """Show edit/remove options for a selected token"""
    try:
        tokens = context.user_data.get('token_manage_list', [])
        network_id = context.user_data.get('token_manage_network')

        if not tokens or token_idx >= len(tokens) or not network_id:
            await query.answer("❌ Token not found")
            return

        token = tokens[token_idx]
        symbol = token.get('symbol', 'Unknown')
        name = token.get('name', '')
        decimals = token.get('decimals', 'N/A')
        address = token.get('address', 'N/A')

        network_escaped = escape_markdown_v2(network_id)
        symbol_escaped = escape_markdown_v2(str(symbol))
        name_escaped = escape_markdown_v2(str(name)) if name else "_Not set_"
        decimals_escaped = escape_markdown_v2(str(decimals))

        message_text = (
            f"🪙 *{symbol_escaped}* on {network_escaped}\n\n"
            f"*Name:* {name_escaped}\n"
            f"*Decimals:* {decimals_escaped}\n"
            f"*Address:*\n`{escape_markdown_v2(address)}`\n\n"
            "_Choose an action:_"
        )

        # Store selected token info for edit/delete operations
        context.user_data['selected_token_idx'] = token_idx

        # Get current page to return to correct page
        current_page = context.user_data.get('token_view_page', 0)

        keyboard = [
            [
                InlineKeyboardButton("✏️ Edit", callback_data=f"gateway_token_edit_{token_idx}"),
                InlineKeyboardButton("🗑 Remove", callback_data=f"gateway_token_del_{token_idx}")
            ],
            [InlineKeyboardButton("« Back", callback_data=f"gateway_token_page_{current_page}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error showing token options: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def prompt_edit_token(query, context: ContextTypes.DEFAULT_TYPE, token_idx: int) -> None:
    """Prompt user to enter new values for the token"""
    try:
        tokens = context.user_data.get('token_manage_list', [])
        network_id = context.user_data.get('token_manage_network')

        if not tokens or token_idx >= len(tokens) or not network_id:
            await query.answer("❌ Token not found")
            return

        token = tokens[token_idx]
        symbol = token.get('symbol', '')
        name = token.get('name', '')
        decimals = token.get('decimals', '')
        address = token.get('address', '')

        network_escaped = escape_markdown_v2(network_id)
        symbol_escaped = escape_markdown_v2(str(symbol))

        # Store edit context
        context.user_data['awaiting_token_input'] = 'token_edit'
        context.user_data['token_network'] = network_id
        context.user_data['token_edit_address'] = address
        context.user_data['token_message_id'] = query.message.message_id
        context.user_data['token_chat_id'] = query.message.chat_id

        # Show current values
        current_values = f"{symbol},{decimals}"
        if name:
            current_values += f",{name}"

        message_text = (
            f"✏️ *Edit Token {symbol_escaped}*\n\n"
            f"*Current values:*\n"
            f"`{escape_markdown_v2(current_values)}`\n\n"
            "*Enter new values in this format:*\n"
            "`symbol,decimals,name`\n\n"
            "*Example:*\n"
            "`GOLD,9,Goldcoin`\n\n"
            "_Leave name empty if not needed \\(e\\.g\\. `GOLD,9`\\)_\n\n"
            "⚠️ _Restart Gateway after editing for changes to take effect\\._"
        )

        keyboard = [[InlineKeyboardButton("« Cancel", callback_data=f"gateway_token_select_{token_idx}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error prompting edit token: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def show_delete_token_confirmation(query, context: ContextTypes.DEFAULT_TYPE, network_id: str, token_address: str) -> None:
    """Show confirmation dialog before deleting a token"""
    try:
        from config_manager import get_config_manager

        chat_id = query.message.chat_id

        # Get token details to show in confirmation
        client = await get_config_manager().get_client_for_chat(chat_id)

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

        # Store token info in user_data to avoid exceeding Telegram's 64-byte callback limit
        context.user_data['pending_token_delete'] = {
            'network_id': network_id,
            'token_address': token_address
        }

        keyboard = [
            [InlineKeyboardButton("✅ Yes, Delete", callback_data="gateway_token_confirm_remove")],
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
        from config_manager import get_config_manager

        await query.answer("Removing token...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(chat_id)
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
    logger.info(f"handle_token_input called. awaiting_field={awaiting_field}, user_data keys={list(context.user_data.keys())}")

    if not awaiting_field:
        logger.info("No awaiting_token_input, returning")
        return

    # Delete user's input message
    try:
        await update.message.delete()
    except:
        pass

    try:
        from config_manager import get_config_manager
        from types import SimpleNamespace

        network_id = context.user_data.get('token_network')
        message_id = context.user_data.get('token_message_id')
        chat_id = context.user_data.get('token_chat_id')
        logger.info(f"Token input: network_id={network_id}, message_id={message_id}, chat_id={chat_id}")

        if awaiting_field == 'token_details':
            # Parse token details: address,symbol,decimals,name OR just address
            token_input = update.message.text.strip()
            parts = [p.strip() for p in token_input.split(',')]

            # Check if just an address (no commas) - try to fetch from GeckoTerminal
            if len(parts) == 1 and len(token_input) > 20:
                address = token_input
                gecko_network = NETWORK_TO_GECKO.get(network_id, network_id)

                try:
                    # Show fetching message
                    if message_id and chat_id:
                        network_escaped = escape_markdown_v2(network_id)
                        await update.get_bot().edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=f"🔍 *Fetching token details\\.\\.\\.*\n\nFrom GeckoTerminal for {network_escaped}",
                            parse_mode="MarkdownV2"
                        )

                    gecko_client = GeckoTerminalAsyncClient()
                    result = await gecko_client.get_specific_token_on_network(gecko_network, address)

                    # Extract token data
                    if isinstance(result, dict):
                        token_data = result.get('data', result) if 'data' in result else result
                        attrs = token_data.get('attributes', token_data)
                        symbol = attrs.get('symbol', '???')
                        decimals = attrs.get('decimals', 9)
                        name = attrs.get('name')
                        logger.info(f"Fetched token from GeckoTerminal: {symbol}, decimals={decimals}, name={name}")
                    else:
                        raise ValueError("Invalid response format from GeckoTerminal")

                except Exception as e:
                    logger.warning(f"Failed to fetch token from GeckoTerminal: {e}")
                    await update.get_bot().send_message(
                        chat_id=chat_id,
                        text=f"❌ Could not fetch token details. Please use full format:\naddress,symbol,decimals,name"
                    )
                    return

            elif len(parts) < 3:
                await update.get_bot().send_message(
                    chat_id=chat_id,
                    text="❌ Invalid format. Use: address,symbol,decimals,name\nOr just paste the token address."
                )
                return
            else:
                address = parts[0]
                symbol = parts[1]
                decimals = int(parts[2])
                name = parts[3] if len(parts) > 3 else None

            # Clear context (including any lingering dex_state from previous operations)
            context.user_data.pop('awaiting_token_input', None)
            context.user_data.pop('token_network', None)
            context.user_data.pop('token_message_id', None)
            context.user_data.pop('token_chat_id', None)
            context.user_data.pop('dex_state', None)

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
                client = await get_config_manager().get_client_for_chat(chat_id)
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

                async def mock_answer(text=""):
                    """Mock async answer method"""
                    pass

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
                    answer=mock_answer
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
            logger.info(f"Processing token_address_remove: token_address={token_address}, network_id={network_id}")

            # Clear context (including any lingering dex_state from previous operations)
            context.user_data.pop('awaiting_token_input', None)
            context.user_data.pop('token_network', None)
            context.user_data.pop('token_message_id', None)
            context.user_data.pop('token_chat_id', None)
            context.user_data.pop('dex_state', None)

            # Create mock query and call confirmation dialog
            async def mock_answer(text=""):
                """Mock async answer method"""
                pass

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
                answer=mock_answer
            )
            await show_delete_token_confirmation(mock_query, context, network_id, token_address)

        elif awaiting_field == 'token_edit':
            # Parse edit values: symbol,decimals,name
            token_input = update.message.text.strip()
            parts = [p.strip() for p in token_input.split(',')]

            if len(parts) < 2:
                await update.get_bot().send_message(
                    chat_id=chat_id,
                    text="❌ Invalid format. Use: symbol,decimals,name"
                )
                return

            new_symbol = parts[0]
            new_decimals = int(parts[1])
            new_name = parts[2] if len(parts) > 2 else None

            # Get the address we're editing
            token_address = context.user_data.get('token_edit_address')

            # Clear context
            context.user_data.pop('awaiting_token_input', None)
            context.user_data.pop('token_network', None)
            context.user_data.pop('token_message_id', None)
            context.user_data.pop('token_chat_id', None)
            context.user_data.pop('token_edit_address', None)
            context.user_data.pop('dex_state', None)

            # Show updating message
            network_escaped = escape_markdown_v2(network_id)
            symbol_escaped = escape_markdown_v2(new_symbol)

            if message_id and chat_id:
                await update.get_bot().edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"⏳ *Updating Token {symbol_escaped}*\n\nOn {network_escaped}\n\n_Please wait\\.\\.\\._",
                    parse_mode="MarkdownV2"
                )

            try:
                client = await get_config_manager().get_client_for_chat(chat_id)

                # Delete old token first, then add with new values
                await client.gateway.delete_token(network_id=network_id, token_address=token_address)
                await client.gateway.add_token(
                    network_id=network_id,
                    address=token_address,
                    symbol=new_symbol,
                    decimals=new_decimals,
                    name=new_name
                )

                success_text = (
                    f"✅ *Token Updated Successfully*\n\n"
                    f"*{symbol_escaped}* updated on {network_escaped}\n\n"
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

                async def mock_answer(text=""):
                    """Mock async answer method"""
                    pass

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
                    answer=mock_answer
                )
                await show_network_tokens(mock_query, context, network_id)

            except Exception as e:
                logger.error(f"Error updating token: {e}", exc_info=True)
                error_text = f"❌ Error updating token: {escape_markdown_v2(str(e))}"
                if message_id and chat_id:
                    await update.get_bot().edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=error_text,
                        parse_mode="MarkdownV2"
                    )

    except Exception as e:
        logger.error(f"Error handling token input: {e}", exc_info=True)
        context.user_data.pop('awaiting_token_input', None)
