"""
Gateway configuration handlers - Server-aware gateway management
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2
from .server_context import build_config_message_header, format_server_selection_needed

logger = logging.getLogger(__name__)


async def handle_gateway_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main router for gateway-related callbacks"""
    query = update.callback_query

    if query.data == "config_gateway":
        await show_gateway_menu(query, context)
    elif query.data == "gateway_select_server":
        await show_server_selection(query, context)
    elif query.data.startswith("gateway_server_"):
        await handle_server_selection(query, context)
    elif query.data == "gateway_deploy":
        await start_deploy_gateway(query, context)
    elif query.data.startswith("gateway_deploy_image_"):
        await deploy_gateway_with_image(query, context)
    elif query.data == "gateway_deploy_custom":
        await prompt_custom_image(query, context)
    elif query.data == "gateway_stop":
        await stop_gateway(query, context)
    elif query.data == "gateway_restart":
        await restart_gateway(query, context)
    elif query.data == "gateway_logs":
        await show_gateway_logs(query, context)
    elif query.data == "gateway_wallets":
        await show_wallets_menu(query, context)
    elif query.data.startswith("gateway_wallet_"):
        await handle_wallet_action(query, context)
    elif query.data == "gateway_connectors":
        await show_connectors_menu(query, context)
    elif query.data.startswith("gateway_connector_"):
        await handle_connector_action(query, context)
    elif query.data == "gateway_networks":
        await show_networks_menu(query, context)
    elif query.data.startswith("gateway_network_"):
        await handle_network_action(query, context)
    elif query.data == "gateway_pools":
        await show_pools_menu(query, context)
    elif query.data.startswith("gateway_pool_"):
        await handle_pool_action(query, context)
    elif query.data == "gateway_tokens":
        await show_tokens_menu(query, context)
    elif query.data.startswith("gateway_token_"):
        await handle_token_action(query, context)


async def show_gateway_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show gateway configuration menu with status for default server
    """
    try:
        from servers import server_manager

        servers = server_manager.list_servers()

        if not servers:
            message_text = format_server_selection_needed()
            keyboard = [[InlineKeyboardButton("« Back", callback_data="config_back")]]
        else:
            # Build unified header with server and gateway info
            header, server_online, gateway_running = await build_config_message_header(
                "🌐 Gateway Configuration",
                include_gateway=True
            )

            message_text = header
            keyboard = []

            # Show appropriate action buttons based on server and gateway status
            if not server_online:
                message_text += "⚠️ _Server is offline\\. Cannot manage Gateway\\._"
            elif gateway_running:
                message_text += "_Gateway is running\\. Configure DEX settings or manage the container\\._"
                keyboard.extend([
                    [
                        InlineKeyboardButton("🔑 Wallets", callback_data="gateway_wallets"),
                        InlineKeyboardButton("🔌 Connectors", callback_data="gateway_connectors"),
                    ],
                    [
                        InlineKeyboardButton("🌍 Networks", callback_data="gateway_networks"),
                        InlineKeyboardButton("💧 Pools", callback_data="gateway_pools"),
                    ],
                    [
                        InlineKeyboardButton("🪙 Tokens", callback_data="gateway_tokens"),
                        InlineKeyboardButton("📋 Logs", callback_data="gateway_logs"),
                    ],
                    [
                        InlineKeyboardButton("🔄 Restart", callback_data="gateway_restart"),
                        InlineKeyboardButton("⏹ Stop", callback_data="gateway_stop"),
                    ],
                ])
            else:
                message_text += "_Gateway is not running\\. Deploy it to start configuring DEX operations\\._"
                keyboard.append([
                    InlineKeyboardButton("🚀 Deploy Gateway", callback_data="gateway_deploy"),
                ])

            # Add back button
            keyboard.append([InlineKeyboardButton("« Back", callback_data="config_back")])

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
        logger.error(f"Error showing gateway menu: {e}", exc_info=True)
        error_text = f"❌ Error loading gateway: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="config_back")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def show_server_selection(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show server selection menu for gateway configuration"""
    try:
        from servers import server_manager

        servers = server_manager.list_servers()
        default_server = server_manager.get_default_server()

        message_text = (
            "🔄 *Select Server*\n\n"
            "Choose which server's Gateway to configure:"
        )

        # Create server buttons
        server_buttons = []
        for server_name in servers.keys():
            button_text = server_name
            if server_name == default_server:
                button_text += " ⭐️"
            server_buttons.append([
                InlineKeyboardButton(button_text, callback_data=f"gateway_server_{server_name}")
            ])

        keyboard = server_buttons + [
            [InlineKeyboardButton("« Back", callback_data="config_gateway")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing server selection: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)}")


async def handle_server_selection(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle server selection for gateway configuration"""
    try:
        from servers import server_manager

        server_name = query.data.replace("gateway_server_", "")

        # Set as default server temporarily for this session
        # Or we could store it in context for this specific flow
        success = server_manager.set_default_server(server_name)

        if success:
            await query.answer(f"✅ Switched to {server_name}")
            await show_gateway_menu(query, context)
        else:
            await query.answer("❌ Failed to switch server")

    except Exception as e:
        logger.error(f"Error handling server selection: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)}")


async def start_deploy_gateway(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show Docker image selection for Gateway deployment"""
    try:
        header, server_online, _ = await build_config_message_header(
            "🚀 Deploy Gateway",
            include_gateway=False
        )

        if not server_online:
            message_text = (
                header +
                "⚠️ _Server is offline\\. Cannot deploy Gateway\\._"
            )
            keyboard = [[InlineKeyboardButton("« Back", callback_data="config_gateway")]]
        else:
            message_text = (
                header +
                "*Select Docker Image:*\n\n"
                "Choose which Gateway image to deploy\\.\n"
                "The latest stable version is recommended\\."
            )

            keyboard = [
                [InlineKeyboardButton("hummingbot/gateway:latest (recommended)", callback_data="gateway_deploy_image_latest")],
                [InlineKeyboardButton("hummingbot/gateway:development", callback_data="gateway_deploy_image_development")],
                [InlineKeyboardButton("hummingbot/gateway:stable", callback_data="gateway_deploy_image_stable")],
                [InlineKeyboardButton("✏️ Custom Image", callback_data="gateway_deploy_custom")],
                [InlineKeyboardButton("« Back", callback_data="config_gateway")],
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error showing deploy options: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def deploy_gateway_with_image(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deploy Gateway container with selected Docker image"""
    try:
        from servers import server_manager

        # Extract image tag from callback data
        image_tag = query.data.replace("gateway_deploy_image_", "")
        docker_image = f"hummingbot/gateway:{image_tag}"

        await query.answer("🚀 Deploying Gateway...")

        client = await server_manager.get_default_client()

        # Gateway configuration
        config = {
            "image": docker_image,
            "port": 15888,
            "passphrase": "a",
            "dev_mode": True,
        }

        response = await client.gateway.start(config)

        if response.get('status') == 'success' or response.get('status') == 'running':
            await query.answer("✅ Gateway deployed successfully")
        else:
            await query.answer("⚠️ Gateway deployment may need verification")

        # Refresh the gateway menu to show new status
        await show_gateway_menu(query, context)

    except Exception as e:
        logger.error(f"Error deploying gateway: {e}", exc_info=True)
        await query.answer(f"❌ Deployment failed: {str(e)[:100]}")
        # Still refresh menu to show current state
        await show_gateway_menu(query, context)


async def prompt_custom_image(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to enter custom Docker image"""
    try:
        header, server_online, _ = await build_config_message_header(
            "✏️ Custom Gateway Image",
            include_gateway=False
        )

        context.user_data['awaiting_gateway_input'] = 'custom_image'
        context.user_data['gateway_message_id'] = query.message.message_id
        context.user_data['gateway_chat_id'] = query.message.chat_id

        message_text = (
            header +
            "*Enter Custom Docker Image:*\n\n"
            "Please send the full Docker image name and tag\\.\n\n"
            "*Examples:*\n"
            "`hummingbot/gateway:1\\.0\\.0`\n"
            "`myregistry\\.io/gateway:custom`"
        )

        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_deploy")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error prompting custom image: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def stop_gateway(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop Gateway container on the default server"""
    try:
        from servers import server_manager

        await query.answer("⏹ Stopping Gateway...")

        client = await server_manager.get_default_client()
        response = await client.gateway.stop()

        if response.get('status') == 'success' or response.get('status') == 'stopped':
            await query.answer("✅ Gateway stopped successfully")
        else:
            await query.answer("⚠️ Gateway stop may need verification")

        # Refresh the gateway menu
        await show_gateway_menu(query, context)

    except Exception as e:
        logger.error(f"Error stopping gateway: {e}", exc_info=True)
        await query.answer(f"❌ Stop failed: {str(e)[:100]}")
        await show_gateway_menu(query, context)


async def restart_gateway(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restart Gateway container on the default server"""
    try:
        from servers import server_manager

        await query.answer("🔄 Restarting Gateway...")

        client = await server_manager.get_default_client()
        response = await client.gateway.restart()

        if response.get('status') == 'success' or response.get('status') == 'running':
            await query.answer("✅ Gateway restarted successfully")
        else:
            await query.answer("⚠️ Gateway restart may need verification")

        # Refresh the gateway menu
        await show_gateway_menu(query, context)

    except Exception as e:
        logger.error(f"Error restarting gateway: {e}", exc_info=True)
        await query.answer(f"❌ Restart failed: {str(e)[:100]}")
        await show_gateway_menu(query, context)


async def show_gateway_logs(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show Gateway container logs"""
    try:
        from servers import server_manager

        await query.answer("📋 Loading logs...")

        client = await server_manager.get_default_client()
        response = await client.gateway.get_logs(tail=50)

        logs = response.get('logs', 'No logs available')

        # Truncate logs if too long for Telegram
        if len(logs) > 3500:
            logs = logs[-3500:]
            logs = "...\\(truncated\\)\n" + logs

        logs_escaped = escape_markdown_v2(logs)

        message_text = (
            "📋 *Gateway Logs* \\(last 50 lines\\)\n\n"
            f"```\n{logs_escaped}\n```"
        )

        keyboard = [
            [InlineKeyboardButton("🔄 Refresh", callback_data="gateway_logs")],
            [InlineKeyboardButton("« Back", callback_data="config_gateway")]
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
        logger.error(f"Error showing gateway logs: {e}", exc_info=True)
        error_text = f"❌ Error loading logs: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="config_gateway")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def show_wallets_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show wallets management menu with list of connected wallets"""
    try:
        from servers import server_manager

        await query.answer("Loading wallets...")

        client = await server_manager.get_default_client()

        # Get list of gateway wallets
        try:
            response = await client.accounts.list_gateway_wallets()
            wallets = response if isinstance(response, list) else response.get('wallets', [])
        except Exception as e:
            logger.warning(f"Failed to list wallets: {e}")
            wallets = []

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
        elif not wallets:
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
            wallet_lines = []
            wallets_by_chain = {}

            for wallet in wallets:
                chain = wallet.get('chain', 'unknown')
                address = wallet.get('address', 'N/A')

                if chain not in wallets_by_chain:
                    wallets_by_chain[chain] = []
                wallets_by_chain[chain].append(address)

            # Format wallet display
            for chain, addresses in sorted(wallets_by_chain.items()):
                chain_escaped = escape_markdown_v2(chain.upper())
                wallet_lines.append(f"\n*{chain_escaped}*")
                for address in addresses:
                    # Truncate address for display
                    display_addr = address[:8] + "..." + address[-6:] if len(address) > 20 else address
                    addr_escaped = escape_markdown_v2(display_addr)
                    wallet_lines.append(f"  • `{addr_escaped}`")

            wallet_count = escape_markdown_v2(str(len(wallets)))
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
        # Format: confirm_remove_{chain}_{address}
        parts = action_data.replace("confirm_remove_", "").split("_", 1)
        if len(parts) == 2:
            chain, address = parts
            await remove_wallet(query, context, chain, address)
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
            wallets = response if isinstance(response, list) else response.get('wallets', [])
        except Exception as e:
            logger.warning(f"Failed to list wallets: {e}")
            wallets = []

        if not wallets:
            await query.answer("❌ No wallets to remove")
            await show_wallets_menu(query, context)
            return

        # Get unique chains
        chains = list(set(wallet.get('chain', 'unknown') for wallet in wallets))

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
            wallets = response if isinstance(response, list) else response.get('wallets', [])
            chain_wallets = [w for w in wallets if w.get('chain') == chain]
        except Exception as e:
            logger.warning(f"Failed to list wallets: {e}")
            chain_wallets = []

        if not chain_wallets:
            await query.answer(f"❌ No wallets found for {chain}")
            await show_wallets_menu(query, context)
            return

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

        # Create address buttons
        address_buttons = []
        for wallet in chain_wallets:
            address = wallet.get('address', 'N/A')
            # Truncate address for display
            display_addr = address[:10] + "..." + address[-8:] if len(address) > 20 else address
            address_buttons.append([
                InlineKeyboardButton(
                    display_addr,
                    callback_data=f"gateway_wallet_confirm_remove_{chain}_{address}"
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

        # Wait a moment then refresh wallets menu
        import asyncio
        await asyncio.sleep(1.5)

        # Create a proper async answer function for the query
        original_answer = query.answer
        async def safe_answer(text=""):
            try:
                await original_answer(text)
            except:
                pass
        query.answer = safe_answer

        await show_wallets_menu(query, context)

    except Exception as e:
        logger.error(f"Error removing wallet: {e}", exc_info=True)
        error_text = f"❌ Error removing wallet: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_wallets")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def show_connectors_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show DEX connectors configuration menu"""
    try:
        from servers import server_manager

        await query.answer("Loading connectors...")

        client = await server_manager.get_default_client()
        response = await client.gateway.list_connectors()

        connectors = response.get('connectors', [])

        if not connectors:
            message_text = (
                "🔌 *DEX Connectors*\n\n"
                "No connectors available\\.\n\n"
                "_Ensure Gateway is running\\._"
            )
            keyboard = [[InlineKeyboardButton("« Back", callback_data="config_gateway")]]
        else:
            message_text = (
                "🔌 *DEX Connectors*\n\n"
                "_Update the config of any of these connectors:_"
            )

            # Organize connector buttons in rows of 3
            connector_buttons = []
            current_row = []

            for connector in connectors:
                connector_name = connector.get('name', 'unknown')
                current_row.append(
                    InlineKeyboardButton(connector_name, callback_data=f"gateway_connector_view_{connector_name}")
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
                    InlineKeyboardButton("🔄 Refresh", callback_data="gateway_connectors"),
                    InlineKeyboardButton("« Back", callback_data="config_gateway")
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
        logger.error(f"Error showing connectors: {e}", exc_info=True)
        error_text = f"❌ Error loading connectors: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="config_gateway")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


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
    elif action_data.startswith("cancel_edit_"):
        connector_name = action_data.replace("cancel_edit_", "")
        # Clear config state and go back to connector details
        context.user_data.pop('configuring_connector', None)
        context.user_data.pop('awaiting_connector_input', None)
        context.user_data.pop('connector_config_data', None)
        await show_connector_details(query, context, connector_name)
    else:
        await query.answer("Unknown action")


async def show_connector_details(query, context: ContextTypes.DEFAULT_TYPE, connector_name: str) -> None:
    """Show details and configuration for a specific connector"""
    try:
        from servers import server_manager

        client = await server_manager.get_default_client()
        response = await client.gateway.get_connector_config(connector_name)

        # Try to extract config - it might be directly in response or nested under 'config'
        if isinstance(response, dict):
            # If response has a 'config' key, use that; otherwise use the whole response
            config = response.get('config', response) if 'config' in response else response
        else:
            config = {}

        # Filter out metadata fields
        config_fields = {k: v for k, v in config.items() if k not in ['status', 'message', 'error']}

        name_escaped = escape_markdown_v2(connector_name)

        # Build configuration display
        config_lines = []
        for key, value in config_fields.items():
            key_escaped = escape_markdown_v2(str(key))
            value_str = str(value)
            # Mask sensitive values like API keys
            if 'key' in key.lower() or 'secret' in key.lower() or 'password' in key.lower():
                if value_str and value_str.strip():
                    value_str = '***' if value_str else ''
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
            [InlineKeyboardButton("✏️ Edit Configuration", callback_data=f"gateway_connector_edit_{connector_name}")],
            [InlineKeyboardButton("« Back to Connectors", callback_data="gateway_connectors")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing connector details: {e}", exc_info=True)
        error_text = f"❌ Error loading connector: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_connectors")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


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

            for idx, network_id in enumerate(networks[:20]):  # Limit to first 20 to avoid message size issues
                # Ensure network_id is a string
                network_str = str(network_id) if not isinstance(network_id, str) else network_id
                # Use index-based callback to avoid exceeding 64-byte limit
                network_buttons.append([
                    InlineKeyboardButton(network_str, callback_data=f"gateway_network_view_{idx}")
                ])

            count_escaped = escape_markdown_v2(str(network_count))
            message_text = (
                f"🌍 *Networks* \\({count_escaped} available\\)\n\n"
                "_Click on a network to view and configure settings\\._"
            )

            keyboard = network_buttons + [
                [InlineKeyboardButton("🔄 Refresh", callback_data="gateway_networks")],
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
                network_id = network_list[network_idx]
                await show_network_details(query, context, network_id)
            else:
                await query.answer("❌ Network not found")
        except ValueError:
            # Fallback for old-style callback data
            network_id = network_idx_str
            await show_network_details(query, context, network_id)
    else:
        await query.answer("Unknown action")


async def show_network_details(query, context: ContextTypes.DEFAULT_TYPE, network_id: str) -> None:
    """Show details and configuration for a specific network"""
    try:
        from servers import server_manager

        client = await server_manager.get_default_client()
        response = await client.gateway.get_network_config(network_id)

        config = response.get('config', {})

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
            f"{config_text}\n\n"
            "_Network configuration editing coming soon\\._"
        )

        keyboard = [
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


async def show_pools_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show liquidity pools menu - requires connector and network selection"""
    message_text = (
        "💧 *Liquidity Pools*\n\n"
        "_Pool management coming soon\\._\n\n"
        "Pools are specific to each connector and network\\. "
        "You'll be able to view and add custom pools here\\."
    )

    keyboard = [
        [InlineKeyboardButton("« Back to Gateway", callback_data="config_gateway")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_pool_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle pool-specific actions - placeholder"""
    await query.answer("Pool management coming soon")


async def show_tokens_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show tokens menu - requires network selection"""
    message_text = (
        "🪙 *Token Management*\n\n"
        "_Token management coming soon\\._\n\n"
        "You'll be able to:\n"
        "• View tokens for each network\n"
        "• Add custom tokens\n"
        "• Remove custom tokens"
    )

    keyboard = [
        [InlineKeyboardButton("« Back to Gateway", callback_data="config_gateway")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        message_text,
        parse_mode="MarkdownV2",
        reply_markup=reply_markup
    )


async def handle_token_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle token-specific actions - placeholder"""
    await query.answer("Token management coming soon")


async def start_connector_config_edit(query, context: ContextTypes.DEFAULT_TYPE, connector_name: str) -> None:
    """Start progressive configuration editing flow for a connector"""
    try:
        from servers import server_manager

        client = await server_manager.get_default_client()
        response = await client.gateway.get_connector_config(connector_name)

        # Extract config
        if isinstance(response, dict):
            config = response.get('config', response) if 'config' in response else response
        else:
            config = {}

        # Filter out metadata fields
        config_fields = {k: v for k, v in config.items() if k not in ['status', 'message', 'error']}
        field_names = list(config_fields.keys())

        if not field_names:
            await query.answer("❌ No configurable fields found")
            return

        # Initialize context storage for connector configuration
        context.user_data['configuring_connector'] = True
        context.user_data['connector_config_data'] = {
            'connector_name': connector_name,
            'fields': field_names,
            'current_values': config_fields.copy(),
            'new_values': {}
        }
        context.user_data['awaiting_connector_input'] = field_names[0]
        context.user_data['connector_message_id'] = query.message.message_id
        context.user_data['connector_chat_id'] = query.message.chat_id

        # Show first field
        message_text, reply_markup = _build_connector_config_message(
            context.user_data['connector_config_data'],
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
        logger.error(f"Error starting connector config edit: {e}", exc_info=True)
        error_text = f"❌ Error loading configuration: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_connectors")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def handle_connector_config_back(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle back button during connector configuration"""
    config_data = context.user_data.get('connector_config_data', {})
    all_fields = config_data.get('fields', [])
    current_field = context.user_data.get('awaiting_connector_input')

    if current_field and current_field in all_fields:
        current_index = all_fields.index(current_field)
        if current_index > 0:
            # Go to previous field
            previous_field = all_fields[current_index - 1]

            # Remove the previous field's new value to re-enter it
            new_values = config_data.get('new_values', {})
            new_values.pop(previous_field, None)
            config_data['new_values'] = new_values
            context.user_data['connector_config_data'] = config_data

            # Update awaiting field
            context.user_data['awaiting_connector_input'] = previous_field
            await query.answer("« Going back")
            await _update_connector_config_message(context, query.message.get_bot())
        else:
            await query.answer("Cannot go back")
    else:
        await query.answer("Cannot go back")


async def handle_connector_config_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text input during connector configuration flow"""
    awaiting_field = context.user_data.get('awaiting_connector_input')
    if not awaiting_field:
        return

    # Delete user's input message for clean chat
    try:
        await update.message.delete()
    except:
        pass

    try:
        new_value = update.message.text.strip()
        config_data = context.user_data.get('connector_config_data', {})
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
        context.user_data['connector_config_data'] = config_data

        # Move to next field or show confirmation
        current_index = all_fields.index(awaiting_field)

        if current_index < len(all_fields) - 1:
            # Move to next field
            context.user_data['awaiting_connector_input'] = all_fields[current_index + 1]
            await _update_connector_config_message(context, update.get_bot())
        else:
            # All fields filled - submit configuration
            context.user_data['awaiting_connector_input'] = None
            await submit_connector_config(context, update.get_bot(), update.effective_chat.id)

    except Exception as e:
        logger.error(f"Error handling connector config input: {e}", exc_info=True)
        context.user_data.pop('awaiting_connector_input', None)
        context.user_data.pop('configuring_connector', None)
        context.user_data.pop('connector_config_data', None)


async def submit_connector_config(context: ContextTypes.DEFAULT_TYPE, bot, chat_id: int) -> None:
    """Submit the connector configuration to Gateway"""
    try:
        from servers import server_manager

        config_data = context.user_data.get('connector_config_data', {})
        connector_name = config_data.get('connector_name')
        new_values = config_data.get('new_values', {})
        current_values = config_data.get('current_values', {})
        message_id = context.user_data.get('connector_message_id')

        if not connector_name or not new_values:
            await bot.send_message(chat_id=chat_id, text="❌ Missing configuration data")
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
                parse_mode="MarkdownV2"
            )

        client = await server_manager.get_default_client()

        # Update configuration using the gateway API
        await client.gateway.update_connector_config(connector_name, final_config)

        # Clear context data
        context.user_data.pop('configuring_connector', None)
        context.user_data.pop('awaiting_connector_input', None)
        context.user_data.pop('connector_config_data', None)
        context.user_data.pop('connector_message_id', None)
        context.user_data.pop('connector_chat_id', None)

        # Show brief success message
        success_text = f"✅ *{connector_escaped}* updated successfully\\!"

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
                reply_markup=reply_markup
            ),
            chat_id=chat_id,
            message_id=message_id
        )
        mock_query = SimpleNamespace(message=mock_message)

        # Navigate back to connector details view
        await show_connector_details(mock_query, context, connector_name)

    except Exception as e:
        logger.error(f"Error submitting connector config: {e}", exc_info=True)
        error_text = f"❌ Error saving configuration: {escape_markdown_v2(str(e))}"
        await bot.send_message(chat_id=chat_id, text=error_text, parse_mode="MarkdownV2")


def _build_connector_config_message(config_data: dict, current_field: str, all_fields: list) -> tuple:
    """
    Build the progressive connector configuration message
    Returns (message_text, reply_markup)
    """
    connector_name = config_data.get('connector_name', '')
    current_values = config_data.get('current_values', {})
    new_values = config_data.get('new_values', {})

    connector_escaped = escape_markdown_v2(connector_name)

    # Build the message showing progress
    lines = [f"✏️ *Edit {connector_escaped}*\n"]

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
            lines.append("_Enter new value or same to keep:_")
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

    # Add back button if not on first field
    current_index = all_fields.index(current_field) if current_field in all_fields else 0
    if current_index > 0:
        buttons.append(InlineKeyboardButton("« Back", callback_data="gateway_connector_config_back"))

    # Always add cancel button
    buttons.append(InlineKeyboardButton("❌ Cancel", callback_data=f"gateway_connector_cancel_edit_{connector_name}"))

    keyboard = [buttons]
    reply_markup = InlineKeyboardMarkup(keyboard)

    return message_text, reply_markup


async def _update_connector_config_message(context: ContextTypes.DEFAULT_TYPE, bot) -> None:
    """Update the connector configuration message with current progress"""
    config_data = context.user_data.get('connector_config_data', {})
    current_field = context.user_data.get('awaiting_connector_input')
    message_id = context.user_data.get('connector_message_id')
    chat_id = context.user_data.get('connector_chat_id')

    if not message_id or not chat_id or not current_field:
        return

    all_fields = config_data.get('fields', [])
    message_text, reply_markup = _build_connector_config_message(config_data, current_field, all_fields)

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error updating connector config message: {e}")


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


async def handle_gateway_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle text input during gateway configuration flows
    """
    # Check if we're awaiting wallet input
    if context.user_data.get('awaiting_wallet_input'):
        await handle_wallet_input(update, context)
        return

    # Check if we're awaiting connector configuration input
    if context.user_data.get('awaiting_connector_input'):
        await handle_connector_config_input(update, context)
        return

    awaiting_field = context.user_data.get('awaiting_gateway_input')
    if not awaiting_field:
        return

    # Delete user's input message for clean chat
    try:
        await update.message.delete()
    except:
        pass

    try:
        if awaiting_field == 'custom_image':
            custom_image = update.message.text.strip()

            # Clear context
            context.user_data.pop('awaiting_gateway_input', None)
            message_id = context.user_data.pop('gateway_message_id', None)
            chat_id = context.user_data.pop('gateway_chat_id', None)

            # Deploy with custom image
            from servers import server_manager

            client = await server_manager.get_default_client()

            # Show deploying message
            if message_id and chat_id:
                await update.get_bot().edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"🚀 *Deploying Gateway*\n\n`{escape_markdown_v2(custom_image)}`\n\n_Please wait\\.\\.\\._",
                    parse_mode="MarkdownV2"
                )

            config = {
                "image": custom_image,
                "port": 15888,
                "passphrase": "a"
            }

            response = await client.gateway.start(config)

            # Create fake query object to reuse show_gateway_menu
            from types import SimpleNamespace

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

            if response.get('status') == 'success' or response.get('status') == 'running':
                # Show brief success, then refresh menu
                if message_id and chat_id:
                    await update.get_bot().edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text="✅ *Gateway Deployed Successfully\\!*",
                        parse_mode="MarkdownV2"
                    )
                    import asyncio
                    await asyncio.sleep(1.5)

            await show_gateway_menu(mock_query, context)

    except Exception as e:
        logger.error(f"Error handling gateway input: {e}", exc_info=True)
        context.user_data.pop('awaiting_gateway_input', None)
        await update.message.reply_text(f"❌ Error: {str(e)}")
