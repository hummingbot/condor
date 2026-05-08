"""
Gateway RPC Providers management - API keys and RPC provider configuration
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..user_preferences import get_active_server
from ._shared import escape_markdown_v2, logger


# RPC Provider configuration
# Maps provider name to chain and default network
RPC_PROVIDERS = {
    "helius": {
        "name": "Helius",
        "chain": "solana",
        "default_network": "solana-mainnet-beta",
        "description": "Premium Solana RPC provider",
    },
    "infura": {
        "name": "Infura",
        "chain": "ethereum",
        "default_network": "ethereum-mainnet",
        "description": "Popular Ethereum RPC provider",
    },
}


async def show_rpc_providers_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show RPC Providers configuration menu"""
    try:
        from config_manager import get_config_manager

        await query.answer("Loading RPC providers...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )

        # Get current API keys
        api_keys = await client.gateway.get_api_keys()

        # Get current rpcProvider settings for each chain
        rpc_settings = {}
        for provider_key, provider_info in RPC_PROVIDERS.items():
            network_id = provider_info["default_network"]
            try:
                config = await client.gateway.get_network_config(network_id)
                rpc_settings[provider_info["chain"]] = config.get("rpc_provider", "url")
            except Exception as e:
                logger.debug(f"Could not fetch {network_id} config: {e}")
                rpc_settings[provider_info["chain"]] = "url"

        # Build provider buttons
        provider_buttons = []
        for provider_key, provider_info in RPC_PROVIDERS.items():
            chain = provider_info["chain"]
            current_rpc = rpc_settings.get(chain, "url")
            has_key = bool(api_keys.get(provider_key, ""))

            # Status indicator
            if current_rpc == provider_key:
                status = "✅"  # Active as RPC provider
            elif has_key:
                status = "🔑"  # Has API key but not active
            else:
                status = "⬜"  # No API key

            # Button text shows provider name and current status
            chain_label = chain.capitalize()
            button_text = f"{status} {provider_info['name']} ({chain_label})"

            provider_buttons.append([
                InlineKeyboardButton(
                    button_text,
                    callback_data=f"gateway_rpc_{provider_key}"
                )
            ])

        message_text = (
            "📡 *RPC Providers*\n\n"
            "_Configure API keys and RPC providers for blockchain access\\._\n\n"
            "✅ \\= Active RPC provider\n"
            "🔑 \\= API key configured\n"
            "⬜ \\= Not configured"
        )

        keyboard = provider_buttons + [
            [InlineKeyboardButton("🔗 Custom URL", callback_data="gateway_rpc_url_menu")],
            [InlineKeyboardButton("« Back", callback_data="config_gateway")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing RPC providers: {e}", exc_info=True)
        error_text = f"❌ Error loading RPC providers: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="config_gateway")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            error_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def handle_rpc_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle RPC provider actions"""
    action_data = query.data.replace("gateway_rpc_", "")

    if action_data in RPC_PROVIDERS:
        await show_provider_details(query, context, action_data)
    elif action_data.startswith("setkey_"):
        provider_key = action_data.replace("setkey_", "")
        await prompt_api_key_input(query, context, provider_key)
    elif action_data.startswith("activate_"):
        provider_key = action_data.replace("activate_", "")
        await activate_provider(query, context, provider_key)
    elif action_data.startswith("deactivate_"):
        provider_key = action_data.replace("deactivate_", "")
        await deactivate_provider(query, context, provider_key)
    elif action_data == "url_menu":
        await show_url_networks_menu(query, context)
    elif action_data == "url_all":
        await show_url_networks_menu(query, context, show_all=True)
    elif action_data.startswith("url_net_"):
        network_idx = int(action_data.replace("url_net_", ""))
        await show_network_rpc_config(query, context, network_idx)
    elif action_data.startswith("url_edit_"):
        network_id = action_data.replace("url_edit_", "")
        await prompt_node_url_input(query, context, network_id)
    elif action_data == "providers":
        await show_rpc_providers_menu(query, context)
    else:
        await query.answer("Unknown action")


async def show_provider_details(
    query, context: ContextTypes.DEFAULT_TYPE, provider_key: str
) -> None:
    """Show details for a specific RPC provider"""
    try:
        from config_manager import get_config_manager

        provider_info = RPC_PROVIDERS.get(provider_key)
        if not provider_info:
            await query.answer("❌ Unknown provider")
            return

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )

        # Get current API key status
        api_keys = await client.gateway.get_api_keys()
        current_key = api_keys.get(provider_key, "")
        has_key = bool(current_key)

        # Get current rpcProvider setting
        network_id = provider_info["default_network"]
        try:
            config = await client.gateway.get_network_config(network_id)
            current_rpc = config.get("rpc_provider", "url")
        except Exception:
            current_rpc = "url"

        is_active = current_rpc == provider_key

        # Build message
        provider_name = escape_markdown_v2(provider_info["name"])
        chain = escape_markdown_v2(provider_info["chain"].capitalize())
        network_escaped = escape_markdown_v2(network_id)

        # API key status
        if has_key:
            # Mask the API key for display
            masked_key = current_key[:8] + "..." + current_key[-4:] if len(current_key) > 12 else "***"
            key_status = f"🔑 API Key: `{escape_markdown_v2(masked_key)}`"
        else:
            key_status = "⬜ No API key configured"

        # RPC status
        if is_active:
            rpc_status = f"✅ *Active* as RPC provider for {chain}"
        else:
            current_rpc_escaped = escape_markdown_v2(current_rpc)
            rpc_status = f"⬜ Not active \\(current: `{current_rpc_escaped}`\\)"

        message_text = (
            f"🔌 *{provider_name}*\n\n"
            f"Chain: {chain}\n"
            f"Network: `{network_escaped}`\n\n"
            f"{key_status}\n"
            f"{rpc_status}"
        )

        # Build action buttons
        keyboard = []

        # API key button
        if has_key:
            keyboard.append([
                InlineKeyboardButton(
                    "🔑 Update API Key",
                    callback_data=f"gateway_rpc_setkey_{provider_key}"
                )
            ])
        else:
            keyboard.append([
                InlineKeyboardButton(
                    "➕ Add API Key",
                    callback_data=f"gateway_rpc_setkey_{provider_key}"
                )
            ])

        # Activate/Deactivate button (only if has key)
        if has_key:
            if is_active:
                keyboard.append([
                    InlineKeyboardButton(
                        "⬜ Deactivate (use custom URL)",
                        callback_data=f"gateway_rpc_deactivate_{provider_key}"
                    )
                ])
            else:
                keyboard.append([
                    InlineKeyboardButton(
                        "✅ Activate as RPC Provider",
                        callback_data=f"gateway_rpc_activate_{provider_key}"
                    )
                ])

        keyboard.append([
            InlineKeyboardButton("« Back", callback_data="gateway_rpc_providers")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing provider details: {e}", exc_info=True)
        error_text = f"❌ Error: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_rpc_providers")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            error_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def prompt_api_key_input(
    query, context: ContextTypes.DEFAULT_TYPE, provider_key: str
) -> None:
    """Prompt user to enter API key"""
    provider_info = RPC_PROVIDERS.get(provider_key)
    if not provider_info:
        await query.answer("❌ Unknown provider")
        return

    provider_name = escape_markdown_v2(provider_info["name"])

    message_text = (
        f"🔑 *Enter {provider_name} API Key*\n\n"
        f"_Send your API key to configure {provider_name}\\._\n\n"
        f"_The key will be saved and Gateway will be configured to use it\\._"
    )

    # Store state for input handling
    context.user_data["awaiting_rpc_input"] = provider_key
    context.user_data["rpc_message_id"] = query.message.message_id
    context.user_data["rpc_chat_id"] = query.message.chat_id

    keyboard = [
        [InlineKeyboardButton("✖ Cancel", callback_data=f"gateway_rpc_{provider_key}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )


async def handle_rpc_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text input for API key or node URL"""
    input_type = context.user_data.get("awaiting_rpc_input")
    if not input_type:
        return

    # Delete user's input message for security
    try:
        await update.message.delete()
    except:
        pass

    # Check if this is a URL input or API key input
    if input_type.startswith("url_"):
        await _handle_url_input(update, context, input_type)
    else:
        await _handle_api_key_input(update, context, input_type)


async def _handle_api_key_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE, provider_key: str
) -> None:
    """Handle API key input"""
    try:
        api_key = update.message.text.strip()
        provider_info = RPC_PROVIDERS.get(provider_key)

        if not provider_info:
            await update.get_bot().send_message(
                chat_id=update.effective_chat.id,
                text="❌ Unknown provider"
            )
            return

        if not api_key:
            await update.get_bot().send_message(
                chat_id=update.effective_chat.id,
                text="❌ API key cannot be empty"
            )
            return

        # Clear input state
        context.user_data.pop("awaiting_rpc_input", None)
        message_id = context.user_data.pop("rpc_message_id", None)
        chat_id = context.user_data.pop("rpc_chat_id", None)

        # Show saving message
        if message_id and chat_id:
            try:
                await update.get_bot().edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"💾 Saving {provider_info['name']} API key..."
                )
            except:
                pass

        # Save API key
        from config_manager import get_config_manager

        client = await get_config_manager().get_client_for_chat(
            update.effective_chat.id,
            preferred_server=get_active_server(context.user_data)
        )

        # Step 1: Update API key
        await client.gateway.update_api_keys({provider_key: api_key})

        # Step 2: Set rpcProvider on the default network
        network_id = provider_info["default_network"]
        await client.gateway.update_network_config(
            network_id,
            {"rpc_provider": provider_key}
        )

        # Show success and return to provider details
        success_text = (
            f"✅ {provider_info['name']} configured\\!\n\n"
            f"API key saved and set as RPC provider for {escape_markdown_v2(network_id)}\\.\n\n"
            f"_Restart Gateway for changes to take effect\\._"
        )

        keyboard = [
            [InlineKeyboardButton("🔄 Restart Gateway", callback_data="gateway_restart")],
            [InlineKeyboardButton("« Back", callback_data=f"gateway_rpc_{provider_key}")]
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
                chat_id=update.effective_chat.id,
                text=success_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error handling API key input: {e}", exc_info=True)
        context.user_data.pop("awaiting_rpc_input", None)
        await update.get_bot().send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ Error saving API key: {str(e)}"
        )


async def _handle_url_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE, input_type: str
) -> None:
    """Handle node URL input"""
    try:
        # Extract network_id from input_type (format: "url_{network_id}")
        network_id = input_type.replace("url_", "")
        node_url = update.message.text.strip()

        if not node_url:
            await update.get_bot().send_message(
                chat_id=update.effective_chat.id,
                text="❌ URL cannot be empty"
            )
            return

        # Basic URL validation
        if not node_url.startswith(("http://", "https://")):
            await update.get_bot().send_message(
                chat_id=update.effective_chat.id,
                text="❌ URL must start with http:// or https://"
            )
            return

        # Clear input state
        context.user_data.pop("awaiting_rpc_input", None)
        message_id = context.user_data.pop("rpc_message_id", None)
        chat_id = context.user_data.pop("rpc_chat_id", None)

        # Show saving message
        if message_id and chat_id:
            try:
                await update.get_bot().edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"💾 Saving node URL for {network_id}..."
                )
            except:
                pass

        # Save URL
        from config_manager import get_config_manager

        client = await get_config_manager().get_client_for_chat(
            update.effective_chat.id,
            preferred_server=get_active_server(context.user_data)
        )

        # Update node_url and set rpc_provider to "url"
        await client.gateway.update_network_config(
            network_id,
            {
                "node_url": node_url,
                "rpc_provider": "url"
            }
        )

        network_escaped = escape_markdown_v2(network_id)
        success_text = (
            f"✅ Node URL updated for {network_escaped}\\!\n\n"
            f"_Restart Gateway for changes to take effect\\._"
        )

        keyboard = [
            [InlineKeyboardButton("🔄 Restart Gateway", callback_data="gateway_restart")],
            [InlineKeyboardButton("« Back", callback_data="gateway_rpc_url_menu")]
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
                chat_id=update.effective_chat.id,
                text=success_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Error handling URL input: {e}", exc_info=True)
        context.user_data.pop("awaiting_rpc_input", None)
        await update.get_bot().send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ Error saving URL: {str(e)}"
        )


async def activate_provider(
    query, context: ContextTypes.DEFAULT_TYPE, provider_key: str
) -> None:
    """Activate a provider as the RPC provider for its chain"""
    try:
        from config_manager import get_config_manager

        provider_info = RPC_PROVIDERS.get(provider_key)
        if not provider_info:
            await query.answer("❌ Unknown provider")
            return

        await query.answer("Activating provider...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )

        # Update rpcProvider on the default network
        network_id = provider_info["default_network"]
        await client.gateway.update_network_config(
            network_id,
            {"rpc_provider": provider_key}
        )

        await query.answer(f"✅ {provider_info['name']} activated! Restart Gateway.")

        # Refresh provider details
        await show_provider_details(query, context, provider_key)

    except Exception as e:
        logger.error(f"Error activating provider: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def deactivate_provider(
    query, context: ContextTypes.DEFAULT_TYPE, provider_key: str
) -> None:
    """Deactivate a provider, reverting to custom URL"""
    try:
        from config_manager import get_config_manager

        provider_info = RPC_PROVIDERS.get(provider_key)
        if not provider_info:
            await query.answer("❌ Unknown provider")
            return

        await query.answer("Deactivating provider...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )

        # Set rpcProvider back to "url" (custom)
        network_id = provider_info["default_network"]
        await client.gateway.update_network_config(
            network_id,
            {"rpc_provider": "url"}
        )

        await query.answer(f"✅ {provider_info['name']} deactivated. Restart Gateway.")

        # Refresh provider details
        await show_provider_details(query, context, provider_key)

    except Exception as e:
        logger.error(f"Error deactivating provider: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


# ============================================
# Custom URL Configuration
# ============================================

async def show_url_networks_menu(
    query, context: ContextTypes.DEFAULT_TYPE, show_all: bool = False
) -> None:
    """Show networks menu for custom URL configuration"""
    try:
        from config_manager import get_config_manager

        from ._shared import extract_network_id, get_default_networks

        await query.answer("Loading networks...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )

        response = await client.gateway.list_networks()
        all_networks = response.get("networks", [])

        if not all_networks:
            message_text = (
                "🔗 *Custom RPC URLs*\n\n"
                "No networks available\\.\n\n"
                "_Ensure Gateway is running\\._"
            )
            keyboard = [
                [InlineKeyboardButton("« Back", callback_data="gateway_rpc_providers")]
            ]
        else:
            # Get default networks from config
            default_network_ids = await get_default_networks(client)

            # Decide which networks to show
            if show_all or not default_network_ids:
                networks_to_show = all_networks[:20]
                showing_defaults = False
            else:
                networks_to_show = [
                    n for n in all_networks
                    if extract_network_id(n) in default_network_ids
                ][:20]
                showing_defaults = True

            # Store networks in context
            context.user_data["rpc_url_network_list"] = networks_to_show

            # Create network buttons
            network_buttons = []
            for idx, network_item in enumerate(networks_to_show):
                network_id = extract_network_id(network_item)
                network_buttons.append([
                    InlineKeyboardButton(
                        network_id,
                        callback_data=f"gateway_rpc_url_net_{idx}"
                    )
                ])

            if showing_defaults:
                count_escaped = escape_markdown_v2(str(len(networks_to_show)))
                message_text = (
                    f"🔗 *Custom RPC URLs* \\({count_escaped} default\\)\n\n"
                    "_Select a network to view and edit RPC settings:_"
                )
                keyboard = network_buttons + [
                    [
                        InlineKeyboardButton(
                            f"🌐 All Networks ({len(all_networks)})",
                            callback_data="gateway_rpc_url_all"
                        )
                    ],
                    [InlineKeyboardButton("« Back", callback_data="gateway_rpc_providers")]
                ]
            else:
                count_escaped = escape_markdown_v2(str(len(all_networks)))
                message_text = (
                    f"🔗 *Custom RPC URLs* \\({count_escaped} networks\\)\n\n"
                    "_Select a network to view and edit RPC settings:_"
                )
                keyboard = network_buttons + [
                    [InlineKeyboardButton("« Back", callback_data="gateway_rpc_providers")]
                ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing URL networks menu: {e}", exc_info=True)
        error_text = f"❌ Error loading networks: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_rpc_providers")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            error_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def show_network_rpc_config(
    query, context: ContextTypes.DEFAULT_TYPE, network_idx: int
) -> None:
    """Show RPC configuration for a specific network"""
    try:
        from config_manager import get_config_manager

        from ._shared import extract_network_id

        network_list = context.user_data.get("rpc_url_network_list", [])
        if network_idx >= len(network_list):
            await query.answer("❌ Network not found")
            return

        network_item = network_list[network_idx]
        network_id = extract_network_id(network_item)

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )

        # Get network config
        config = await client.gateway.get_network_config(network_id)

        rpc_provider = config.get("rpc_provider", "url")
        node_url = config.get("node_url", "")

        network_escaped = escape_markdown_v2(network_id)
        rpc_provider_escaped = escape_markdown_v2(rpc_provider)

        # Truncate long URLs for display
        if len(node_url) > 60:
            url_display = node_url[:30] + "..." + node_url[-20:]
        else:
            url_display = node_url
        url_escaped = escape_markdown_v2(url_display)

        # Store for editing
        context.user_data["rpc_edit_network_id"] = network_id
        context.user_data["rpc_edit_network_idx"] = network_idx

        message_text = (
            f"🔗 *{network_escaped}*\n\n"
            f"*RPC Provider:* `{rpc_provider_escaped}`\n"
            f"*Node URL:*\n`{url_escaped}`\n\n"
            "_Click Edit URL to change the RPC endpoint\\._"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "✏️ Edit URL",
                    callback_data=f"gateway_rpc_url_edit_{network_id}"
                )
            ],
            [InlineKeyboardButton("« Back", callback_data="gateway_rpc_url_menu")]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing network RPC config: {e}", exc_info=True)
        error_text = f"❌ Error: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_rpc_url_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            error_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def prompt_node_url_input(
    query, context: ContextTypes.DEFAULT_TYPE, network_id: str
) -> None:
    """Prompt user to enter custom node URL"""
    network_escaped = escape_markdown_v2(network_id)

    message_text = (
        f"✏️ *Edit Node URL for {network_escaped}*\n\n"
        "_Send the new RPC endpoint URL\\._\n\n"
        "_Example:_\n"
        "`https://api\\.mainnet\\-beta\\.solana\\.com`"
    )

    # Store state for input handling
    context.user_data["awaiting_rpc_input"] = f"url_{network_id}"
    context.user_data["rpc_message_id"] = query.message.message_id
    context.user_data["rpc_chat_id"] = query.message.chat_id

    keyboard = [
        [InlineKeyboardButton("✖ Cancel", callback_data="gateway_rpc_url_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
    )
