"""
Gateway configuration handlers - Server-aware gateway management
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from utils.telegram_formatters import escape_markdown_v2

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
        await deploy_gateway(query, context)
    elif query.data == "gateway_stop":
        await stop_gateway(query, context)
    elif query.data == "gateway_restart":
        await restart_gateway(query, context)
    elif query.data == "gateway_logs":
        await show_gateway_logs(query, context)
    elif query.data == "gateway_wallets":
        await show_wallets_menu(query, context)
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

        # Get default server
        default_server = server_manager.get_default_server()
        servers = server_manager.list_servers()

        if not servers:
            message_text = (
                "🌐 *Gateway Configuration*\n\n"
                "No API servers configured\\.\n\n"
                "_Add servers in API Servers first\\._"
            )
            keyboard = [[InlineKeyboardButton("« Back", callback_data="config_back")]]
        elif not default_server:
            message_text = (
                "🌐 *Gateway Configuration*\n\n"
                "No default server set\\.\n\n"
                "_Set a default server in API Servers first\\._"
            )
            keyboard = [[InlineKeyboardButton("« Back", callback_data="config_back")]]
        else:
            # Get client for default server
            client = await server_manager.get_default_client()

            # Check gateway status
            try:
                status_response = await client.gateway.get_status()
                gateway_status = status_response.get('status', 'unknown')
                is_running = gateway_status == 'running'

                if is_running:
                    status_icon = "🟢"
                    status_text = "Running"
                else:
                    status_icon = "🔴"
                    status_text = "Not Running"

            except Exception as e:
                logger.warning(f"Failed to get gateway status: {e}")
                status_icon = "⚪️"
                status_text = "Unknown"
                is_running = False

            server_escaped = escape_markdown_v2(default_server)
            status_escaped = escape_markdown_v2(status_text)

            message_text = (
                f"🌐 *Gateway Configuration*\n\n"
                f"*Server:* `{server_escaped}`\n"
                f"*Status:* {status_icon} {status_escaped}\n\n"
            )

            keyboard = []

            # Show appropriate action buttons based on status
            if is_running:
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

            # Add server selection and back buttons
            if len(servers) > 1:
                keyboard.append([
                    InlineKeyboardButton("🔄 Change Server", callback_data="gateway_select_server"),
                ])

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


async def deploy_gateway(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deploy Gateway container on the default server"""
    try:
        from servers import server_manager

        await query.answer("🚀 Deploying Gateway...")

        client = await server_manager.get_default_client()

        # Default gateway configuration
        # Users can customize this through environment variables or config
        config = {
            "image": "hummingbot/gateway:latest",
            "port": 15888
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

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing gateway logs: {e}", exc_info=True)
        error_text = f"❌ Error loading logs: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="config_gateway")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def show_wallets_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show wallets management menu - placeholder for future implementation"""
    message_text = (
        "🔑 *Wallet Management*\n\n"
        "_Wallet management coming soon\\._\n\n"
        "You can add wallets through the Hummingbot Gateway directly\\."
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
            # Build connector list
            connector_lines = []
            connector_buttons = []

            for connector in connectors:
                connector_name = connector.get('name', 'unknown')
                chain = connector.get('chain', 'N/A')
                trading_type = connector.get('trading_type', 'N/A')

                name_escaped = escape_markdown_v2(connector_name)
                chain_escaped = escape_markdown_v2(chain)
                type_escaped = escape_markdown_v2(str(trading_type))

                connector_lines.append(
                    f"• *{name_escaped}* \\({chain_escaped}\\)\n"
                    f"  _{type_escaped}_"
                )

                connector_buttons.append([
                    InlineKeyboardButton(connector_name, callback_data=f"gateway_connector_view_{connector_name}")
                ])

            message_text = (
                "🔌 *DEX Connectors*\n\n"
                + "\n\n".join(connector_lines) + "\n\n"
                "_Click on a connector to view and modify settings\\._"
            )

            keyboard = connector_buttons + [
                [InlineKeyboardButton("🔄 Refresh", callback_data="gateway_connectors")],
                [InlineKeyboardButton("« Back", callback_data="config_gateway")]
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

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
    else:
        await query.answer("Unknown action")


async def show_connector_details(query, context: ContextTypes.DEFAULT_TYPE, connector_name: str) -> None:
    """Show details and configuration for a specific connector"""
    try:
        from servers import server_manager

        client = await server_manager.get_default_client()
        response = await client.gateway.get_connector_config(connector_name)

        config = response.get('config', {})

        name_escaped = escape_markdown_v2(connector_name)

        # Build configuration display
        config_lines = []
        for key, value in config.items():
            key_escaped = escape_markdown_v2(str(key))
            value_escaped = escape_markdown_v2(str(value))
            config_lines.append(f"• *{key_escaped}:* `{value_escaped}`")

        if config_lines:
            config_text = "\n".join(config_lines)
        else:
            config_text = "_No configuration available_"

        message_text = (
            f"🔌 *Connector: {name_escaped}*\n\n"
            "*Configuration:*\n"
            f"{config_text}\n\n"
            "_Connector configuration editing coming soon\\._"
        )

        keyboard = [
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

            for network_id in networks[:20]:  # Limit to first 20 to avoid message size issues
                network_buttons.append([
                    InlineKeyboardButton(network_id, callback_data=f"gateway_network_view_{network_id}")
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
        network_id = action_data.replace("view_", "")
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


async def handle_gateway_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle text input during gateway configuration flows
    Placeholder for future interactive configuration
    """
    # For now, this is a placeholder
    # Future implementations could handle:
    # - Custom token address input
    # - Pool address input
    # - Network RPC URL input
    # - Connector configuration values
    pass
