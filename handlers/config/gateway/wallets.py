"""
Gateway wallet management functions
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from ..server_context import build_config_message_header
from ..user_preferences import (
    get_wallet_networks,
    set_wallet_networks,
    remove_wallet_networks,
    get_default_networks_for_chain,
    get_all_networks_for_chain,
    get_active_server,
)
from ._shared import logger, escape_markdown_v2


async def show_wallets_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show wallets management menu with list of connected wallets as clickable buttons"""
    try:
        from config_manager import get_config_manager

        await query.answer("Loading wallets...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(chat_id, preferred_server=get_active_server(context.user_data))

        # Get list of gateway wallets
        try:
            response = await client.accounts.list_gateway_wallets()
            wallets_data = response if isinstance(response, list) else response.get('wallets', [])
        except Exception as e:
            logger.warning(f"Failed to list wallets: {e}")
            wallets_data = []

        header, server_online, gateway_running = await build_config_message_header(
            "🔑 Wallet Management",
            include_gateway=True,
            chat_id=chat_id,
            user_data=context.user_data
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
                "Add an existing wallet or create a new one\\."
            )
            keyboard = [
                [
                    InlineKeyboardButton("➕ Add Wallet", callback_data="gateway_wallet_add"),
                    InlineKeyboardButton("🆕 Create Wallet", callback_data="gateway_wallet_create"),
                ],
                [InlineKeyboardButton("« Back to Gateway", callback_data="config_gateway")]
            ]
        else:
            # Build a flat list of wallets with chain info for indexing
            # Store in context for retrieval by index
            wallet_list = []
            for wallet_group in wallets_data:
                chain = wallet_group.get('chain', 'unknown')
                addresses = wallet_group.get('walletAddresses', [])
                for address in addresses:
                    wallet_list.append({'chain': chain, 'address': address})

            context.user_data['wallet_list'] = wallet_list
            total_wallets = len(wallet_list)

            wallet_count = escape_markdown_v2(str(total_wallets))
            message_text = (
                header +
                f"*Connected Wallets:* {wallet_count}\n\n"
                "_Click a wallet to view details and configure networks\\._"
            )

            # Create wallet buttons - one per row with chain prefix
            wallet_buttons = []
            for idx, wallet in enumerate(wallet_list):
                chain = wallet['chain']
                address = wallet['address']
                # Truncate address for display
                display_addr = address[:6] + "..." + address[-4:] if len(address) > 14 else address
                chain_icon = "🟣" if chain == "solana" else "🔵"  # Solana purple, Ethereum blue
                button_text = f"{chain_icon} {chain.title()}: {display_addr}"
                wallet_buttons.append([
                    InlineKeyboardButton(button_text, callback_data=f"gateway_wallet_view_{idx}")
                ])

            keyboard = wallet_buttons + [
                [
                    InlineKeyboardButton("➕ Add Wallet", callback_data="gateway_wallet_add"),
                    InlineKeyboardButton("🆕 Create Wallet", callback_data="gateway_wallet_create"),
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
    elif action_data == "create":
        await prompt_create_wallet_chain(query, context)
    elif action_data.startswith("create_chain_"):
        chain = action_data.replace("create_chain_", "")
        await create_wallet(query, context, chain)
    elif action_data == "remove":
        await prompt_remove_wallet_chain(query, context)
    elif action_data.startswith("view_"):
        # View wallet details by index
        idx_str = action_data.replace("view_", "")
        try:
            idx = int(idx_str)
            wallet_list = context.user_data.get('wallet_list', [])
            if 0 <= idx < len(wallet_list):
                wallet = wallet_list[idx]
                await show_wallet_details(query, context, wallet['chain'], wallet['address'])
            else:
                await query.answer("❌ Wallet not found")
        except ValueError:
            await query.answer("❌ Invalid wallet index")
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
    elif action_data.startswith("delete_"):
        # Direct delete from wallet detail view: delete_{idx}
        idx_str = action_data.replace("delete_", "")
        try:
            idx = int(idx_str)
            wallet_list = context.user_data.get('wallet_list', [])
            if 0 <= idx < len(wallet_list):
                wallet = wallet_list[idx]
                await remove_wallet(query, context, wallet['chain'], wallet['address'])
            else:
                await query.answer("❌ Wallet not found")
        except ValueError:
            await query.answer("❌ Invalid wallet index")
    elif action_data.startswith("networks_"):
        # Edit networks for wallet: networks_{idx}
        idx_str = action_data.replace("networks_", "")
        try:
            idx = int(idx_str)
            wallet_list = context.user_data.get('wallet_list', [])
            if 0 <= idx < len(wallet_list):
                wallet = wallet_list[idx]
                await show_wallet_network_edit(query, context, wallet['chain'], wallet['address'], idx)
            else:
                await query.answer("❌ Wallet not found")
        except ValueError:
            await query.answer("❌ Invalid wallet index")
    elif action_data.startswith("toggle_net_"):
        # Toggle network: toggle_net_{wallet_idx}_{network_id}
        parts = action_data.replace("toggle_net_", "").split("_", 1)
        if len(parts) == 2:
            wallet_idx_str, network_id = parts
            try:
                wallet_idx = int(wallet_idx_str)
                await toggle_wallet_network(query, context, wallet_idx, network_id)
            except ValueError:
                await query.answer("❌ Invalid index")
    elif action_data.startswith("net_done_"):
        # Done editing networks: net_done_{wallet_idx}
        idx_str = action_data.replace("net_done_", "")
        try:
            idx = int(idx_str)
            wallet_list = context.user_data.get('wallet_list', [])
            if 0 <= idx < len(wallet_list):
                wallet = wallet_list[idx]
                await show_wallet_details(query, context, wallet['chain'], wallet['address'])
            else:
                await show_wallets_menu(query, context)
        except ValueError:
            await show_wallets_menu(query, context)
    elif action_data == "cancel_add" or action_data == "cancel_remove":
        await show_wallets_menu(query, context)
    elif action_data.startswith("select_networks_"):
        # After adding wallet, select networks: select_networks_{chain}_{address_truncated}
        # We use the full address stored in context
        await show_new_wallet_network_selection(query, context)
    elif action_data.startswith("new_toggle_"):
        # Toggle network for newly added wallet: new_toggle_{network_id}
        network_id = action_data.replace("new_toggle_", "")
        await toggle_new_wallet_network(query, context, network_id)
    elif action_data == "new_net_done":
        # Finish network selection for new wallet
        await finish_new_wallet_network_selection(query, context)
    else:
        await query.answer("Unknown action")


async def prompt_add_wallet_chain(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to select chain for adding wallet"""
    try:
        chat_id = query.message.chat_id
        header, server_online, gateway_running = await build_config_message_header(
            "➕ Add Wallet",
            include_gateway=True,
            chat_id=chat_id,
            user_data=context.user_data
        )

        # Base blockchain chains (wallets are at blockchain level, not network level)
        supported_chains = ["ethereum", "solana"]

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


async def prompt_create_wallet_chain(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to select chain for creating a new wallet"""
    try:
        chat_id = query.message.chat_id
        header, server_online, gateway_running = await build_config_message_header(
            "🆕 Create Wallet",
            include_gateway=True,
            chat_id=chat_id,
            user_data=context.user_data
        )

        # Base blockchain chains (wallets are at blockchain level, not network level)
        supported_chains = ["ethereum", "solana"]

        message_text = (
            header +
            "*Select Chain:*\n\n"
            "_Choose which blockchain to create a new wallet for\\._\n\n"
            "⚠️ *Note:* A new wallet with a fresh keypair will be generated\\. "
            "Make sure to back up the private key from Gateway\\."
        )

        # Create chain buttons
        chain_buttons = []
        for chain in supported_chains:
            chain_display = chain.replace("-", " ").title()
            chain_icon = "🟣" if chain == "solana" else "🔵"
            chain_buttons.append([
                InlineKeyboardButton(f"{chain_icon} {chain_display}", callback_data=f"gateway_wallet_create_chain_{chain}")
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
        logger.error(f"Error prompting create wallet chain: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def create_wallet(query, context: ContextTypes.DEFAULT_TYPE, chain: str) -> None:
    """Create a new wallet on the specified chain via Gateway"""
    try:
        from config_manager import get_config_manager

        await query.answer("Creating wallet...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(chat_id, preferred_server=get_active_server(context.user_data))

        chain_escaped = escape_markdown_v2(chain.replace("-", " ").title())

        # Show creating message
        await query.message.edit_text(
            f"⏳ *Creating {chain_escaped} Wallet*\n\n_Please wait\\.\\.\\._",
            parse_mode="MarkdownV2"
        )

        # Create the wallet via Gateway API
        response = await client.gateway.create_wallet(chain=chain, set_default=False)

        # Extract address from response
        address = response.get('address', '') if isinstance(response, dict) else ''

        if not address:
            raise ValueError("No address returned from wallet creation")

        # Set default networks for the new wallet
        default_networks = get_default_networks_for_chain(chain)
        set_wallet_networks(context.user_data, address, default_networks)

        # Store info for network selection flow
        context.user_data['new_wallet_chain'] = chain
        context.user_data['new_wallet_address'] = address
        context.user_data['new_wallet_networks'] = list(default_networks)
        context.user_data['new_wallet_message_id'] = query.message.message_id
        context.user_data['new_wallet_chat_id'] = chat_id

        # Show success message with network selection prompt
        display_addr = address[:10] + "..." + address[-8:] if len(address) > 20 else address
        addr_escaped = escape_markdown_v2(display_addr)

        # Build network selection message
        all_networks = get_all_networks_for_chain(chain)
        network_buttons = []
        for net in all_networks:
            is_enabled = net in default_networks
            status = "✅" if is_enabled else "⬜"
            net_display = net.replace("-", " ").title()
            button_text = f"{status} {net_display}"
            network_buttons.append([
                InlineKeyboardButton(button_text, callback_data=f"gateway_wallet_new_toggle_{net}")
            ])

        success_text = (
            f"✅ *Wallet Created Successfully*\n\n"
            f"`{addr_escaped}`\n\n"
            f"*Select Networks:*\n"
            f"_Choose which networks to enable for balance queries\\._"
        )

        keyboard = network_buttons + [
            [InlineKeyboardButton("✓ Done", callback_data="gateway_wallet_new_net_done")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            success_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error creating wallet: {e}", exc_info=True)
        error_text = f"❌ Error creating wallet: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_wallets")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def show_wallet_details(query, context: ContextTypes.DEFAULT_TYPE, chain: str, address: str) -> None:
    """Show details for a specific wallet with edit options"""
    try:
        chat_id = query.message.chat_id
        header, server_online, gateway_running = await build_config_message_header(
            "🔑 Wallet Details",
            include_gateway=True,
            chat_id=chat_id,
            user_data=context.user_data
        )

        chain_escaped = escape_markdown_v2(chain.title())
        chain_icon = "🟣" if chain == "solana" else "🔵"

        # Get configured networks for this wallet
        enabled_networks = get_wallet_networks(context.user_data, address)
        if enabled_networks is None:
            # Not configured yet - use defaults
            enabled_networks = get_default_networks_for_chain(chain)

        # Format address display
        addr_escaped = escape_markdown_v2(address)

        # Build networks list
        all_networks = get_all_networks_for_chain(chain)
        networks_display = []
        for net in all_networks:
            is_enabled = net in enabled_networks
            status = "✅" if is_enabled else "❌"
            net_escaped = escape_markdown_v2(net)
            networks_display.append(f"  {status} `{net_escaped}`")

        networks_text = "\n".join(networks_display) if networks_display else "_No networks available_"

        message_text = (
            header +
            f"{chain_icon} *Chain:* {chain_escaped}\n\n"
            f"*Address:*\n`{addr_escaped}`\n\n"
            f"*Enabled Networks:*\n{networks_text}\n\n"
            "_Only enabled networks will be queried for balances\\._"
        )

        # Find wallet index in the list
        wallet_list = context.user_data.get('wallet_list', [])
        wallet_idx = None
        for idx, w in enumerate(wallet_list):
            if w['address'] == address and w['chain'] == chain:
                wallet_idx = idx
                break

        if wallet_idx is not None:
            keyboard = [
                [InlineKeyboardButton("🌐 Edit Networks", callback_data=f"gateway_wallet_networks_{wallet_idx}")],
                [InlineKeyboardButton("🗑️ Delete Wallet", callback_data=f"gateway_wallet_delete_{wallet_idx}")],
                [InlineKeyboardButton("« Back to Wallets", callback_data="gateway_wallets")]
            ]
        else:
            keyboard = [[InlineKeyboardButton("« Back to Wallets", callback_data="gateway_wallets")]]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error showing wallet details: {e}", exc_info=True)
        error_text = f"❌ Error: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_wallets")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def show_wallet_network_edit(query, context: ContextTypes.DEFAULT_TYPE, chain: str, address: str, wallet_idx: int) -> None:
    """Show network toggle interface for a wallet"""
    try:
        chat_id = query.message.chat_id
        header, server_online, gateway_running = await build_config_message_header(
            "🌐 Edit Networks",
            include_gateway=True,
            chat_id=chat_id,
            user_data=context.user_data
        )

        chain_escaped = escape_markdown_v2(chain.title())

        # Get currently enabled networks
        enabled_networks = get_wallet_networks(context.user_data, address)
        if enabled_networks is None:
            enabled_networks = get_default_networks_for_chain(chain)

        # Store current selection in temp context for toggling
        context.user_data['editing_wallet_networks'] = {
            'chain': chain,
            'address': address,
            'wallet_idx': wallet_idx,
            'enabled': list(enabled_networks)  # Make a copy
        }

        display_addr = address[:8] + "..." + address[-6:] if len(address) > 18 else address
        addr_escaped = escape_markdown_v2(display_addr)

        message_text = (
            header +
            f"*Editing Networks for {chain_escaped}*\n"
            f"`{addr_escaped}`\n\n"
            "_Toggle networks on/off\\. Only enabled networks will be queried for balances\\._"
        )

        # Create toggle buttons for each network
        all_networks = get_all_networks_for_chain(chain)
        network_buttons = []
        for net in all_networks:
            is_enabled = net in enabled_networks
            status = "✅" if is_enabled else "⬜"
            # Format network name nicely
            net_display = net.replace("-", " ").title()
            button_text = f"{status} {net_display}"
            network_buttons.append([
                InlineKeyboardButton(button_text, callback_data=f"gateway_wallet_toggle_net_{wallet_idx}_{net}")
            ])

        keyboard = network_buttons + [
            [InlineKeyboardButton("✓ Done", callback_data=f"gateway_wallet_net_done_{wallet_idx}")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error showing network edit: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def toggle_wallet_network(query, context: ContextTypes.DEFAULT_TYPE, wallet_idx: int, network_id: str) -> None:
    """Toggle a network on/off for a wallet"""
    try:
        editing = context.user_data.get('editing_wallet_networks')
        if not editing:
            await query.answer("❌ No wallet being edited")
            return

        enabled = editing.get('enabled', [])
        chain = editing['chain']
        address = editing['address']

        # Toggle the network
        if network_id in enabled:
            enabled.remove(network_id)
            await query.answer(f"❌ {network_id} disabled")
        else:
            enabled.append(network_id)
            await query.answer(f"✅ {network_id} enabled")

        # Update context
        editing['enabled'] = enabled
        context.user_data['editing_wallet_networks'] = editing

        # Save to preferences immediately
        set_wallet_networks(context.user_data, address, enabled)

        # Refresh the edit view
        await show_wallet_network_edit(query, context, chain, address, wallet_idx)

    except Exception as e:
        logger.error(f"Error toggling network: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def prompt_add_wallet_private_key(query, context: ContextTypes.DEFAULT_TYPE, chain: str) -> None:
    """Prompt user to enter private key for adding wallet"""
    try:
        chat_id = query.message.chat_id
        header, server_online, gateway_running = await build_config_message_header(
            f"➕ Add {chain.replace('-', ' ').title()} Wallet",
            include_gateway=True,
            chat_id=chat_id,
            user_data=context.user_data
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
        from config_manager import get_config_manager

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(chat_id, preferred_server=get_active_server(context.user_data))

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
            include_gateway=True,
            chat_id=chat_id,
            user_data=context.user_data
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
        from config_manager import get_config_manager

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(chat_id, preferred_server=get_active_server(context.user_data))

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
            include_gateway=True,
            chat_id=chat_id,
            user_data=context.user_data
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
        from config_manager import get_config_manager

        await query.answer("Removing wallet...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(chat_id, preferred_server=get_active_server(context.user_data))

        # Remove the wallet from Gateway
        await client.accounts.remove_gateway_wallet(chain=chain, address=address)

        # Also remove network preferences for this wallet
        remove_wallet_networks(context.user_data, address)

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
            from config_manager import get_config_manager

            chain_escaped = escape_markdown_v2(chain.replace("-", " ").title())
            if message_id and chat_id:
                await update.get_bot().edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"⏳ *Adding {chain_escaped} Wallet*\n\n_Please wait\\.\\.\\._",
                    parse_mode="MarkdownV2"
                )

            try:
                client = await get_config_manager().get_client_for_chat(chat_id, preferred_server=get_active_server(context.user_data))

                # Add the wallet
                response = await client.accounts.add_gateway_wallet(chain=chain, private_key=private_key)

                # Extract address from response
                address = response.get('address', 'Added') if isinstance(response, dict) else 'Added'

                # Set default networks for the new wallet
                default_networks = get_default_networks_for_chain(chain)
                set_wallet_networks(context.user_data, address, default_networks)

                # Store info for network selection flow
                context.user_data['new_wallet_chain'] = chain
                context.user_data['new_wallet_address'] = address
                context.user_data['new_wallet_networks'] = list(default_networks)
                context.user_data['new_wallet_message_id'] = message_id
                context.user_data['new_wallet_chat_id'] = chat_id

                # Show success message with network selection prompt
                display_addr = address[:10] + "..." + address[-8:] if len(address) > 20 else address
                addr_escaped = escape_markdown_v2(display_addr)

                # Build network selection message
                all_networks = get_all_networks_for_chain(chain)
                network_buttons = []
                for net in all_networks:
                    is_enabled = net in default_networks
                    status = "✅" if is_enabled else "⬜"
                    net_display = net.replace("-", " ").title()
                    button_text = f"{status} {net_display}"
                    network_buttons.append([
                        InlineKeyboardButton(button_text, callback_data=f"gateway_wallet_new_toggle_{net}")
                    ])

                success_text = (
                    f"✅ *Wallet Added Successfully*\n\n"
                    f"`{addr_escaped}`\n\n"
                    f"*Select Networks:*\n"
                    f"_Choose which networks to enable for balance queries\\._"
                )

                keyboard = network_buttons + [
                    [InlineKeyboardButton("✓ Done", callback_data="gateway_wallet_new_net_done")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                if message_id and chat_id:
                    await update.get_bot().edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=success_text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                else:
                    await update.get_bot().send_message(
                        chat_id=chat_id,
                        text=success_text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )

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


async def show_new_wallet_network_selection(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show network selection for newly added wallet"""
    try:
        chain = context.user_data.get('new_wallet_chain')
        address = context.user_data.get('new_wallet_address')
        enabled_networks = context.user_data.get('new_wallet_networks', [])

        if not chain or not address:
            await query.answer("❌ No new wallet found")
            await show_wallets_menu(query, context)
            return

        display_addr = address[:10] + "..." + address[-8:] if len(address) > 20 else address
        addr_escaped = escape_markdown_v2(display_addr)

        # Build network selection message
        all_networks = get_all_networks_for_chain(chain)
        network_buttons = []
        for net in all_networks:
            is_enabled = net in enabled_networks
            status = "✅" if is_enabled else "⬜"
            net_display = net.replace("-", " ").title()
            button_text = f"{status} {net_display}"
            network_buttons.append([
                InlineKeyboardButton(button_text, callback_data=f"gateway_wallet_new_toggle_{net}")
            ])

        message_text = (
            f"✅ *Wallet Added Successfully*\n\n"
            f"`{addr_escaped}`\n\n"
            f"*Select Networks:*\n"
            f"_Choose which networks to enable for balance queries\\._"
        )

        keyboard = network_buttons + [
            [InlineKeyboardButton("✓ Done", callback_data="gateway_wallet_new_net_done")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error showing new wallet network selection: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def toggle_new_wallet_network(query, context: ContextTypes.DEFAULT_TYPE, network_id: str) -> None:
    """Toggle a network for newly added wallet"""
    try:
        chain = context.user_data.get('new_wallet_chain')
        address = context.user_data.get('new_wallet_address')
        enabled_networks = context.user_data.get('new_wallet_networks', [])

        if not chain or not address:
            await query.answer("❌ No new wallet found")
            return

        # Toggle the network
        if network_id in enabled_networks:
            enabled_networks.remove(network_id)
            await query.answer(f"❌ {network_id} disabled")
        else:
            enabled_networks.append(network_id)
            await query.answer(f"✅ {network_id} enabled")

        # Update context and preferences
        context.user_data['new_wallet_networks'] = enabled_networks
        set_wallet_networks(context.user_data, address, enabled_networks)

        # Refresh the selection view
        await show_new_wallet_network_selection(query, context)

    except Exception as e:
        logger.error(f"Error toggling new wallet network: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def finish_new_wallet_network_selection(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Finish network selection for newly added wallet and go to wallets menu"""
    try:
        address = context.user_data.get('new_wallet_address')
        enabled_networks = context.user_data.get('new_wallet_networks', [])

        # Save final network selection
        if address and enabled_networks:
            set_wallet_networks(context.user_data, address, enabled_networks)

        # Clear temp context
        context.user_data.pop('new_wallet_chain', None)
        context.user_data.pop('new_wallet_address', None)
        context.user_data.pop('new_wallet_networks', None)
        context.user_data.pop('new_wallet_message_id', None)
        context.user_data.pop('new_wallet_chat_id', None)

        await query.answer("✅ Network configuration saved")
        await show_wallets_menu(query, context)

    except Exception as e:
        logger.error(f"Error finishing network selection: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")
        await show_wallets_menu(query, context)
