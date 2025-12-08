"""
Gateway wallet management functions
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from ..server_context import build_config_message_header
from ._shared import logger, escape_markdown_v2


async def show_wallets_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show wallets management menu with list of connected wallets"""
    try:
        from servers import server_manager

        await query.answer("Loading wallets...")

        client = await server_manager.get_default_client()

        # Get list of gateway wallets
        try:
            response = await client.accounts.list_gateway_wallets()
            wallets_data = response if isinstance(response, list) else response.get('wallets', [])
        except Exception as e:
            logger.warning(f"Failed to list wallets: {e}")
            wallets_data = []

        header, server_online, gateway_running = await build_config_message_header(
            "🔑 Wallet Management",
            include_gateway=True
        )

        if not server_online:
            message_text = (
                header +
                "⚠️ _Server is offline\\. Cannot manage wallets\\._"
            )
            keyboard = [[InlineKeyboardButton("« Back to Gateway", callback_data="config_gateway")]]
        elif not gateway_running:
            message_text = (
                header +
                "⚠️ _Gateway is not running\\. Cannot manage wallets\\._"
            )
            keyboard = [[InlineKeyboardButton("« Back to Gateway", callback_data="config_gateway")]]
        elif not wallets_data:
            message_text = (
                header +
                "_No wallets connected\\._\n\n"
                "Add a wallet to get started\\."
            )
            keyboard = [
                [InlineKeyboardButton("➕ Add Wallet", callback_data="gateway_wallet_add")],
                [InlineKeyboardButton("« Back to Gateway", callback_data="config_gateway")]
            ]
        else:
            # Display wallets grouped by chain
            # API returns: [{"chain": "solana", "walletAddresses": ["addr1", "addr2"]}]
            wallet_lines = []
            total_wallets = 0

            for wallet_group in wallets_data:
                chain = wallet_group.get('chain', 'unknown')
                addresses = wallet_group.get('walletAddresses', [])
                total_wallets += len(addresses)

                chain_escaped = escape_markdown_v2(chain.upper())
                wallet_lines.append(f"\n*{chain_escaped}*")
                for address in addresses:
                    # Truncate address for display
                    display_addr = address[:8] + "..." + address[-6:] if len(address) > 20 else address
                    addr_escaped = escape_markdown_v2(display_addr)
                    wallet_lines.append(f"  • `{addr_escaped}`")

            wallet_count = escape_markdown_v2(str(total_wallets))
            message_text = (
                header +
                f"*Connected Wallets:* {wallet_count}\n" +
                "\n".join(wallet_lines) + "\n\n"
                "_Select an action:_"
            )

            keyboard = [
                [
                    InlineKeyboardButton("➕ Add Wallet", callback_data="gateway_wallet_add"),
                    InlineKeyboardButton("➖ Remove Wallet", callback_data="gateway_wallet_remove")
                ],
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="gateway_wallets"),
                    InlineKeyboardButton("« Back to Gateway", callback_data="config_gateway")
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
        logger.error(f"Error showing wallets menu: {e}", exc_info=True)
        error_text = f"❌ Error loading wallets: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back to Gateway", callback_data="config_gateway")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def handle_wallet_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle wallet-specific actions"""
    action_data = query.data.replace("gateway_wallet_", "")

    if action_data == "add":
        await prompt_add_wallet_chain(query, context)
    elif action_data == "remove":
        await prompt_remove_wallet_chain(query, context)
    elif action_data.startswith("add_chain_"):
        chain = action_data.replace("add_chain_", "")
        await prompt_add_wallet_private_key(query, context, chain)
    elif action_data.startswith("remove_chain_"):
        chain = action_data.replace("remove_chain_", "")
        await prompt_remove_wallet_address(query, context, chain)
    elif action_data.startswith("confirm_remove_"):
        # Format: confirm_remove_{chain}_{index}
        parts = action_data.replace("confirm_remove_", "").split("_", 1)
        if len(parts) == 2:
            chain, idx_str = parts
            try:
                idx = int(idx_str)
                # Retrieve address from context
                addresses = context.user_data.get(f'wallet_addresses_{chain}', [])
                if 0 <= idx < len(addresses):
                    address = addresses[idx]
                    await remove_wallet(query, context, chain, address)
                else:
                    await query.answer("❌ Invalid wallet selection")
            except ValueError:
                await query.answer("❌ Invalid wallet index")
    elif action_data == "cancel_add" or action_data == "cancel_remove":
        await show_wallets_menu(query, context)
    else:
        await query.answer("Unknown action")


async def prompt_add_wallet_chain(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to select chain for adding wallet"""
    try:
        header, server_online, gateway_running = await build_config_message_header(
            "➕ Add Wallet",
            include_gateway=True
        )

        # Common chains
        supported_chains = ["ethereum", "polygon", "solana", "avalanche", "binance-smart-chain"]

        message_text = (
            header +
            "*Select Chain:*\n\n"
            "_Choose which blockchain network to add a wallet for\\._"
        )

        # Create chain buttons
        chain_buttons = []
        for chain in supported_chains:
            chain_display = chain.replace("-", " ").title()
            chain_buttons.append([
                InlineKeyboardButton(chain_display, callback_data=f"gateway_wallet_add_chain_{chain}")
            ])

        keyboard = chain_buttons + [
            [InlineKeyboardButton("« Back", callback_data="gateway_wallets")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error prompting add wallet chain: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def prompt_add_wallet_private_key(query, context: ContextTypes.DEFAULT_TYPE, chain: str) -> None:
    """Prompt user to enter private key for adding wallet"""
    try:
        header, server_online, gateway_running = await build_config_message_header(
            f"➕ Add {chain.replace('-', ' ').title()} Wallet",
            include_gateway=True
        )

        context.user_data['awaiting_wallet_input'] = 'add_wallet'
        context.user_data['wallet_chain'] = chain
        context.user_data['wallet_message_id'] = query.message.message_id
        context.user_data['wallet_chat_id'] = query.message.chat_id

        chain_escaped = escape_markdown_v2(chain.replace("-", " ").title())
        message_text = (
            header +
            f"*Enter Private Key for {chain_escaped}:*\n\n"
            "⚠️ *Security Warning:*\n"
            "• Your private key will be sent securely to the Gateway\n"
            "• The message will be deleted immediately\n"
            "• Never share your private key with untrusted sources\n\n"
            "_Please send your wallet private key as a message\\._"
        )

        keyboard = [[InlineKeyboardButton("« Cancel", callback_data="gateway_wallet_cancel_add")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error prompting private key: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def prompt_remove_wallet_chain(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to select chain for removing wallet"""
    try:
        from servers import server_manager

        client = await server_manager.get_default_client()

        # Get list of gateway wallets
        try:
            response = await client.accounts.list_gateway_wallets()
            wallets_data = response if isinstance(response, list) else response.get('wallets', [])
        except Exception as e:
            logger.warning(f"Failed to list wallets: {e}")
            wallets_data = []

        if not wallets_data:
            await query.answer("❌ No wallets to remove")
            await show_wallets_menu(query, context)
            return

        # Get unique chains from wallet groups
        chains = [wallet_group.get('chain') for wallet_group in wallets_data if wallet_group.get('walletAddresses')]

        header, server_online, gateway_running = await build_config_message_header(
            "➖ Remove Wallet",
            include_gateway=True
        )

        message_text = (
            header +
            "*Select Chain:*\n\n"
            "_Choose which blockchain network to remove a wallet from\\._"
        )

        # Create chain buttons
        chain_buttons = []
        for chain in sorted(chains):
            chain_display = chain.replace("-", " ").title()
            chain_buttons.append([
                InlineKeyboardButton(chain_display, callback_data=f"gateway_wallet_remove_chain_{chain}")
            ])

        keyboard = chain_buttons + [
            [InlineKeyboardButton("« Back", callback_data="gateway_wallets")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error prompting remove wallet chain: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def prompt_remove_wallet_address(query, context: ContextTypes.DEFAULT_TYPE, chain: str) -> None:
    """Prompt user to select wallet address to remove"""
    try:
        from servers import server_manager

        client = await server_manager.get_default_client()

        # Get wallets for this chain
        try:
            response = await client.accounts.list_gateway_wallets()
            wallets_data = response if isinstance(response, list) else response.get('wallets', [])

            # Find the wallet group for this chain and extract addresses
            chain_addresses = []
            for wallet_group in wallets_data:
                if wallet_group.get('chain') == chain:
                    chain_addresses = wallet_group.get('walletAddresses', [])
                    break
        except Exception as e:
            logger.warning(f"Failed to list wallets: {e}")
            chain_addresses = []

        if not chain_addresses:
            await query.answer(f"❌ No wallets found for {chain}")
            await show_wallets_menu(query, context)
            return

        # Store addresses in context for later retrieval
        context.user_data[f'wallet_addresses_{chain}'] = chain_addresses

        header, server_online, gateway_running = await build_config_message_header(
            f"➖ Remove {chain.replace('-', ' ').title()} Wallet",
            include_gateway=True
        )

        chain_escaped = escape_markdown_v2(chain.replace("-", " ").title())
        message_text = (
            header +
            f"*Select {chain_escaped} Wallet to Remove:*\n\n"
            "_Click on a wallet address to confirm removal\\._"
        )

        # Create address buttons using indices to avoid 64-byte callback_data limit
        address_buttons = []
        for idx, address in enumerate(chain_addresses):
            # Truncate address for display
            display_addr = address[:10] + "..." + address[-8:] if len(address) > 20 else address
            address_buttons.append([
                InlineKeyboardButton(
                    display_addr,
                    callback_data=f"gateway_wallet_confirm_remove_{chain}_{idx}"
                )
            ])

        keyboard = address_buttons + [
            [InlineKeyboardButton("« Back", callback_data="gateway_wallet_remove")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error prompting wallet address: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def remove_wallet(query, context: ContextTypes.DEFAULT_TYPE, chain: str, address: str) -> None:
    """Remove a wallet from Gateway"""
    try:
        from servers import server_manager

        await query.answer("Removing wallet...")

        client = await server_manager.get_default_client()

        # Remove the wallet
        await client.accounts.remove_gateway_wallet(chain=chain, address=address)

        # Show success message
        chain_escaped = escape_markdown_v2(chain.replace("-", " ").title())
        display_addr = address[:10] + "..." + address[-8:] if len(address) > 20 else address
        addr_escaped = escape_markdown_v2(display_addr)

        success_text = f"✅ *Wallet Removed*\n\n`{addr_escaped}`\n\nRemoved from {chain_escaped}"

        keyboard = [[InlineKeyboardButton("« Back to Wallets", callback_data="gateway_wallets")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            success_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error removing wallet: {e}", exc_info=True)
        error_text = f"❌ Error removing wallet: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_wallets")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def handle_wallet_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text input during wallet addition flow"""
    awaiting_field = context.user_data.get('awaiting_wallet_input')
    if not awaiting_field:
        return

    # Delete user's input message for security
    try:
        await update.message.delete()
    except:
        pass

    try:
        if awaiting_field == 'add_wallet':
            private_key = update.message.text.strip()
            chain = context.user_data.get('wallet_chain')
            message_id = context.user_data.get('wallet_message_id')
            chat_id = context.user_data.get('wallet_chat_id')

            # Clear context
            context.user_data.pop('awaiting_wallet_input', None)
            context.user_data.pop('wallet_chain', None)
            context.user_data.pop('wallet_message_id', None)
            context.user_data.pop('wallet_chat_id', None)

            if not chain or not private_key:
                await update.get_bot().send_message(
                    chat_id=chat_id,
                    text="❌ Missing chain or private key"
                )
                return

            # Show adding message
            from servers import server_manager
            from types import SimpleNamespace

            chain_escaped = escape_markdown_v2(chain.replace("-", " ").title())
            if message_id and chat_id:
                await update.get_bot().edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"⏳ *Adding {chain_escaped} Wallet*\n\n_Please wait\\.\\.\\._",
                    parse_mode="MarkdownV2"
                )

            try:
                client = await server_manager.get_default_client()

                # Add the wallet
                response = await client.accounts.add_gateway_wallet(chain=chain, private_key=private_key)

                # Extract address from response
                address = response.get('address', 'Added') if isinstance(response, dict) else 'Added'

                # Show success message
                display_addr = address[:10] + "..." + address[-8:] if len(address) > 20 else address
                addr_escaped = escape_markdown_v2(display_addr)

                success_text = f"✅ *Wallet Added Successfully*\n\n`{addr_escaped}`\n\nAdded to {chain_escaped}"

                if message_id and chat_id:
                    await update.get_bot().edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=success_text,
                        parse_mode="MarkdownV2"
                    )
                else:
                    await update.get_bot().send_message(
                        chat_id=chat_id,
                        text=success_text,
                        parse_mode="MarkdownV2"
                    )

                # Wait a moment then refresh wallets menu
                import asyncio
                await asyncio.sleep(1.5)

                # Create mock query object to reuse show_wallets_menu
                async def mock_answer(text=""):
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

                await show_wallets_menu(mock_query, context)

            except Exception as e:
                logger.error(f"Error adding wallet: {e}", exc_info=True)
                error_text = f"❌ Error adding wallet: {escape_markdown_v2(str(e))}"

                if message_id and chat_id:
                    await update.get_bot().edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=error_text,
                        parse_mode="MarkdownV2"
                    )
                else:
                    await update.get_bot().send_message(
                        chat_id=chat_id,
                        text=error_text,
                        parse_mode="MarkdownV2"
                    )

    except Exception as e:
        logger.error(f"Error handling wallet input: {e}", exc_info=True)
        context.user_data.pop('awaiting_wallet_input', None)
