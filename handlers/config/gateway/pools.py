"""
Gateway liquidity pool management functions
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from ._shared import logger, escape_markdown_v2, filter_pool_connectors, extract_network_id
from utils.telegram_formatters import resolve_token_address


async def show_pools_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show liquidity pools menu - select connector first"""
    try:
        from config_manager import get_config_manager

        await query.answer("Loading connectors...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(chat_id)
        response = await client.gateway.list_connectors()
        connectors = response.get('connectors', [])

        # Filter connectors that support liquidity pools (AMM or CLMM trading types)
        pool_connectors = filter_pool_connectors(connectors)

        # Store full connector data in context for later use
        context.user_data['pool_connectors_data'] = {
            c.get('name'): c for c in pool_connectors
        }

        if not pool_connectors:
            message_text = (
                "💧 *Liquidity Pools*\n\n"
                "No pool\\-enabled connectors available\\.\n\n"
                "_Ensure Gateway is running with DEX connectors\\._"
            )
            keyboard = [[InlineKeyboardButton("« Back to Gateway", callback_data="config_gateway")]]
        else:
            message_text = (
                "💧 *Liquidity Pools*\n\n"
                "_Select a connector to view and manage pools:_"
            )

            # Create connector buttons
            connector_buttons = []
            for connector in pool_connectors[:15]:  # Limit to 15 to avoid message size issues
                connector_name = connector.get('name', 'unknown')
                trading_types = ', '.join(connector.get('trading_types', []))
                connector_buttons.append([
                    InlineKeyboardButton(
                        f"{connector_name} ({trading_types})",
                        callback_data=f"gateway_pool_connector_{connector_name}"
                    )
                ])

            keyboard = connector_buttons + [
                [InlineKeyboardButton("« Back to Gateway", callback_data="config_gateway")]
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing pools menu: {e}", exc_info=True)
        error_text = f"❌ Error loading connectors: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back to Gateway", callback_data="config_gateway")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def handle_pool_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle pool-specific actions"""
    action_data = query.data.replace("gateway_pool_", "")

    if action_data.startswith("connector_"):
        # Show networks for selected connector
        connector_name = action_data.replace("connector_", "")
        await show_pool_networks(query, context, connector_name)
    elif action_data.startswith("network_"):
        # Show pools for selected network using index
        # Format: network_{idx}
        network_idx_str = action_data.replace("network_", "")
        try:
            network_idx = int(network_idx_str)
            network_list = context.user_data.get('pool_network_list', [])
            connector_name = context.user_data.get('pool_connector_name')

            if connector_name and 0 <= network_idx < len(network_list):
                network_item = network_list[network_idx]
                network_id = extract_network_id(network_item)

                # Store current network for later operations
                context.user_data['pool_current_network'] = network_id
                await show_connector_pools(query, context, connector_name, network_id)
            else:
                await query.answer("❌ Network not found")
        except ValueError:
            await query.answer("❌ Invalid network")
    elif action_data == "add":
        # Add pool to connector/network
        # Using stored context data
        connector_name = context.user_data.get('pool_connector_name')
        network_id = context.user_data.get('pool_current_network')
        if connector_name and network_id:
            await prompt_add_pool(query, context, connector_name, network_id)
        else:
            await query.answer("❌ Session expired, please start over")
    elif action_data == "remove":
        # Remove pool from connector/network
        # Using stored context data
        connector_name = context.user_data.get('pool_connector_name')
        network_id = context.user_data.get('pool_current_network')
        if connector_name and network_id:
            await prompt_remove_pool(query, context, connector_name, network_id)
        else:
            await query.answer("❌ Session expired, please start over")
    elif action_data.startswith("select_remove_"):
        # User selected a pool to remove from the list
        pool_idx_str = action_data.replace("select_remove_", "")
        try:
            pool_idx = int(pool_idx_str)
            pool_list = context.user_data.get('pool_list', [])
            connector_name = context.user_data.get('pool_connector_name')
            network_id = context.user_data.get('pool_current_network')

            if connector_name and network_id and 0 <= pool_idx < len(pool_list):
                pool = pool_list[pool_idx]
                pool_address = pool.get('address', pool.get('pool_id', ''))
                pool_type = pool.get('type', '')
                # Store for confirmation
                context.user_data['pool_remove_address'] = pool_address
                context.user_data['pool_remove_type'] = pool_type
                await show_delete_pool_confirmation(query, context, connector_name, network_id, pool_address, pool_type)
            else:
                await query.answer("❌ Pool not found")
        except ValueError:
            await query.answer("❌ Invalid pool selection")
    elif action_data == "confirm_remove":
        # Full pool address and type stored in context
        pool_address = context.user_data.get('pool_remove_address')
        pool_type = context.user_data.get('pool_remove_type')
        connector_name = context.user_data.get('pool_connector_name')
        network_id = context.user_data.get('pool_current_network')
        if pool_address and pool_type and connector_name and network_id:
            await remove_pool(query, context, connector_name, network_id, pool_address, pool_type)
        else:
            await query.answer("❌ Session expired, please start over")
    elif action_data == "view":
        # Back to viewing pools
        connector_name = context.user_data.get('pool_connector_name')
        network_id = context.user_data.get('pool_current_network')
        if connector_name and network_id:
            await show_connector_pools(query, context, connector_name, network_id)
        else:
            await query.answer("❌ Session expired, please start over")
    else:
        await query.answer("Unknown action")


async def show_pool_networks(query, context: ContextTypes.DEFAULT_TYPE, connector_name: str) -> None:
    """Show network selection for viewing pools - only connector-specific networks"""
    try:
        from config_manager import get_config_manager

        await query.answer("Loading networks...")

        # Get connector data from context
        connectors_data = context.user_data.get('pool_connectors_data', {})
        connector_info = connectors_data.get(connector_name)

        if not connector_info:
            # Fallback: fetch connector info again if not in context
            chat_id = query.message.chat_id
            client = await get_config_manager().get_client_for_chat(chat_id)
            response = await client.gateway.list_connectors()
            connectors = response.get('connectors', [])
            connector_info = next((c for c in connectors if c.get('name') == connector_name), None)

        if not connector_info:
            message_text = (
                "💧 *Liquidity Pools*\n\n"
                "Connector not found\\.\n\n"
                "_Please go back and try again\\._"
            )
            keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_pools")]]
        else:
            # Get networks specific to this connector
            connector_networks = connector_info.get('networks', [])

            if not connector_networks:
                connector_escaped = escape_markdown_v2(connector_name)
                message_text = (
                    f"💧 *{connector_escaped} Pools*\n\n"
                    "No networks available for this connector\\.\n\n"
                    "_Ensure Gateway is properly configured\\._"
                )
                keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_pools")]]
            else:
                connector_escaped = escape_markdown_v2(connector_name)
                chain = connector_info.get('chain', 'unknown')
                chain_escaped = escape_markdown_v2(chain)

                message_text = (
                    f"💧 *{connector_escaped} Pools*\n"
                    f"Chain: `{chain_escaped}`\n\n"
                    "_Select a network to view pools:_"
                )

                # Store network list in user_data to avoid long callback_data
                context.user_data['pool_network_list'] = connector_networks[:15]
                context.user_data['pool_connector_name'] = connector_name

                # Create network buttons using indices to avoid Button_data_invalid
                network_buttons = []
                for idx, network_item in enumerate(connector_networks[:15]):
                    network_str = extract_network_id(network_item)
                    network_buttons.append([
                        InlineKeyboardButton(network_str, callback_data=f"gateway_pool_network_{idx}")
                    ])

                keyboard = network_buttons + [
                    [InlineKeyboardButton("« Back", callback_data="gateway_pools")]
                ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing pool networks: {e}", exc_info=True)
        error_text = f"❌ Error loading networks: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_pools")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def show_connector_pools(query, context: ContextTypes.DEFAULT_TYPE, connector_name: str, network: str) -> None:
    """Show pools for a specific connector and network"""
    try:
        from config_manager import get_config_manager

        await query.answer("Loading pools...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(chat_id)
        pools = await client.gateway.list_pools(connector_name=connector_name, network=network)

        connector_escaped = escape_markdown_v2(connector_name)
        network_escaped = escape_markdown_v2(network)

        if not pools:
            message_text = (
                f"💧 *{connector_escaped}*\n"
                f"Network: `{network_escaped}`\n\n"
                "_No pools found\\._\n\n"
                "Add a custom pool to get started\\."
            )
            keyboard = [
                [InlineKeyboardButton("➕ Add Pool", callback_data="gateway_pool_add")],
                [InlineKeyboardButton("« Back", callback_data=f"gateway_pool_connector_{connector_name}")]
            ]
        else:
            # Display first 10 pools
            pool_lines = []
            for idx, pool in enumerate(pools[:10], 1):
                trading_pair = pool.get('trading_pair', pool.get('tradingPair', 'N/A'))
                pool_type = pool.get('type', 'N/A')
                trading_pair_escaped = escape_markdown_v2(str(trading_pair))
                pool_type_escaped = escape_markdown_v2(str(pool_type))
                pool_lines.append(f"{idx}\\. `{trading_pair_escaped}` \\({pool_type_escaped}\\)")

            pools_text = "\n".join(pool_lines)
            pool_count = escape_markdown_v2(str(len(pools)))

            message_text = (
                f"💧 *{connector_escaped}*\n"
                f"Network: `{network_escaped}`\n\n"
                f"*Pools* \\({pool_count} total\\):\n"
                f"{pools_text}\n\n"
                "_Add or remove custom pools as needed\\._"
            )

            keyboard = [
                [
                    InlineKeyboardButton("➕ Add Pool", callback_data="gateway_pool_add"),
                    InlineKeyboardButton("➖ Remove Pool", callback_data="gateway_pool_remove")
                ],
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="gateway_pool_view"),
                    InlineKeyboardButton("« Back", callback_data=f"gateway_pool_connector_{connector_name}")
                ]
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

    except BadRequest as e:
        if "Message is not modified" in str(e):
            # Ignore - message content is the same (e.g., on refresh with no changes)
            pass
        else:
            logger.error(f"Error showing connector pools: {e}", exc_info=True)
            error_text = f"❌ Error loading pools: {escape_markdown_v2(str(e))}"
            keyboard = [[InlineKeyboardButton("« Back", callback_data=f"gateway_pool_connector_{connector_name}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error showing connector pools: {e}", exc_info=True)
        error_text = f"❌ Error loading pools: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data=f"gateway_pool_connector_{connector_name}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def prompt_add_pool(query, context: ContextTypes.DEFAULT_TYPE, connector_name: str, network: str) -> None:
    """Prompt user to enter pool details"""
    try:
        connector_escaped = escape_markdown_v2(connector_name)
        network_escaped = escape_markdown_v2(network)

        context.user_data['awaiting_pool_input'] = 'pool_details'
        context.user_data['pool_connector'] = connector_name
        context.user_data['pool_network'] = network
        context.user_data['pool_message_id'] = query.message.message_id
        context.user_data['pool_chat_id'] = query.message.chat_id

        message_text = (
            f"➕ *Add Pool to {connector_escaped}*\n"
            f"Network: `{network_escaped}`\n\n"
            "*Enter pool details in this format:*\n"
            "`pool_type,base,quote,address`\n\n"
            "*Example:*\n"
            "`CLMM,SOL,USDC,8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj`"
        )

        keyboard = [[InlineKeyboardButton("« Cancel", callback_data="gateway_pool_view")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error prompting add pool: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def prompt_remove_pool(query, context: ContextTypes.DEFAULT_TYPE, connector_name: str, network: str) -> None:
    """Show list of pools to remove with numbered buttons"""
    try:
        from config_manager import get_config_manager

        connector_escaped = escape_markdown_v2(connector_name)
        network_escaped = escape_markdown_v2(network)

        chat_id = query.message.chat_id

        # Fetch pools to display as options
        client = await get_config_manager().get_client_for_chat(chat_id)
        pools = await client.gateway.list_pools(connector_name=connector_name, network=network)

        if not pools:
            message_text = (
                f"➖ *Remove Pool from {connector_escaped}*\n"
                f"Network: `{network_escaped}`\n\n"
                "_No pools found to remove\\._"
            )
            keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_pool_view")]]
        else:
            # Store pools in context for later retrieval
            context.user_data['pool_list'] = pools

            pool_lines = []
            keyboard = []
            for idx, pool in enumerate(pools[:10], 1):
                trading_pair = pool.get('trading_pair', pool.get('tradingPair', 'N/A'))
                pool_type = pool.get('type', 'N/A')
                trading_pair_escaped = escape_markdown_v2(str(trading_pair))
                pool_type_escaped = escape_markdown_v2(str(pool_type))
                pool_lines.append(f"{idx}\\. `{trading_pair_escaped}` \\({pool_type_escaped}\\)")
                # Add button for each pool
                keyboard.append([InlineKeyboardButton(f"{idx}. {trading_pair} ({pool_type})", callback_data=f"gateway_pool_select_remove_{idx-1}")])

            pools_text = "\n".join(pool_lines)

            message_text = (
                f"➖ *Remove Pool from {connector_escaped}*\n"
                f"Network: `{network_escaped}`\n\n"
                "*Select a pool to remove:*\n\n"
                f"{pools_text}\n\n"
                "⚠️ _Restart Gateway after removing for changes to take effect\\._"
            )
            keyboard.append([InlineKeyboardButton("« Cancel", callback_data="gateway_pool_view")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error prompting remove pool: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def show_delete_pool_confirmation(query, context: ContextTypes.DEFAULT_TYPE, connector_name: str, network: str, pool_address: str, pool_type: str) -> None:
    """Show confirmation dialog before deleting a pool"""
    try:
        connector_escaped = escape_markdown_v2(connector_name)
        network_escaped = escape_markdown_v2(network)
        pool_type_escaped = escape_markdown_v2(pool_type)
        addr_display = pool_address[:10] + "..." + pool_address[-8:] if len(pool_address) > 20 else pool_address
        addr_escaped = escape_markdown_v2(addr_display)

        message_text = (
            f"🗑 *Delete Pool*\n\n"
            f"Connector: *{connector_escaped}*\n"
            f"Network: *{network_escaped}*\n"
            f"Type: *{pool_type_escaped}*\n"
            f"Address: `{addr_escaped}`\n\n"
            f"⚠️ This will remove the pool from *{connector_escaped}* on *{network_escaped}*\\.\n"
            "You will need to restart the Gateway for changes to take effect\\.\n\n"
            "Are you sure you want to delete this pool?"
        )

        # Store pool address and type in context
        context.user_data['pool_remove_address'] = pool_address
        context.user_data['pool_remove_type'] = pool_type

        keyboard = [
            [InlineKeyboardButton("✅ Yes, Delete", callback_data="gateway_pool_confirm_remove")],
            [InlineKeyboardButton("❌ Cancel", callback_data="gateway_pool_view")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        try:
            await query.answer()
        except TypeError:
            pass  # Mock query doesn't support answer

    except Exception as e:
        logger.error(f"Error showing delete pool confirmation: {e}", exc_info=True)
        try:
            await query.answer(f"❌ Error: {str(e)[:100]}")
        except TypeError:
            pass  # Mock query doesn't support answer


async def remove_pool(query, context: ContextTypes.DEFAULT_TYPE, connector_name: str, network: str, pool_address: str, pool_type: str) -> None:
    """Remove a pool from Gateway"""
    try:
        from config_manager import get_config_manager

        try:
            await query.answer("Removing pool...")
        except TypeError:
            pass  # Mock query doesn't support answer

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(chat_id)
        await client.gateway.delete_pool(connector=connector_name, network=network, pool_type=pool_type, address=pool_address)

        connector_escaped = escape_markdown_v2(connector_name)
        network_escaped = escape_markdown_v2(network)
        addr_display = pool_address[:10] + "..." + pool_address[-8:] if len(pool_address) > 20 else pool_address
        addr_escaped = escape_markdown_v2(addr_display)

        success_text = (
            f"✅ *Pool Removed*\n\n"
            f"`{addr_escaped}`\n\n"
            f"Removed from {connector_escaped} on {network_escaped}\n\n"
            "⚠️ _Restart Gateway for changes to take effect\\._"
        )

        keyboard = [[InlineKeyboardButton("« Back to Pools", callback_data="gateway_pool_view")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            success_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

        import asyncio
        await asyncio.sleep(2)
        await show_connector_pools(query, context, connector_name, network)

    except Exception as e:
        logger.error(f"Error removing pool: {e}", exc_info=True)
        error_text = f"❌ Error removing pool: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_pool_view")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(error_text, parse_mode="MarkdownV2", reply_markup=reply_markup)


async def handle_pool_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text input during pool addition/removal flow"""
    awaiting_field = context.user_data.get('awaiting_pool_input')
    if not awaiting_field:
        return

    # Delete user's input message
    try:
        await update.message.delete()
    except:
        pass

    try:
        from config_manager import get_config_manager
        from types import SimpleNamespace

        connector_name = context.user_data.get('pool_connector')
        network = context.user_data.get('pool_network')
        message_id = context.user_data.get('pool_message_id')
        chat_id = context.user_data.get('pool_chat_id')

        if awaiting_field == 'pool_details':
            # Parse pool details: pool_type,base,quote,address
            pool_input = update.message.text.strip()
            parts = [p.strip() for p in pool_input.split(',')]

            if len(parts) != 4:
                await update.message.reply_text("❌ Invalid format. Use: pool_type,base,quote,address")
                return

            pool_type, base, quote, address = parts

            # Resolve token addresses from symbols
            base_address = resolve_token_address(base)
            quote_address = resolve_token_address(quote)

            if not base_address:
                await update.message.reply_text(f"❌ Unknown token symbol: {base}\nPlease use known tokens (SOL, USDC, USDT, etc.)")
                return
            if not quote_address:
                await update.message.reply_text(f"❌ Unknown token symbol: {quote}\nPlease use known tokens (SOL, USDC, USDT, etc.)")
                return

            # Clear context
            context.user_data.pop('awaiting_pool_input', None)
            context.user_data.pop('pool_connector', None)
            context.user_data.pop('pool_network', None)
            context.user_data.pop('pool_message_id', None)
            context.user_data.pop('pool_chat_id', None)

            # Show adding message
            connector_escaped = escape_markdown_v2(connector_name)
            pair_escaped = escape_markdown_v2(f"{base}/{quote}")

            if message_id and chat_id:
                await update.get_bot().edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"⏳ *Adding Pool {pair_escaped}*\n\nTo {connector_escaped}\n\n_Please wait\\.\\.\\._",
                    parse_mode="MarkdownV2"
                )

            try:
                client = await get_config_manager().get_client_for_chat(chat_id)

                logger.info(f"Adding pool: connector={connector_name}, network={network}, "
                           f"pool_type={pool_type}, base={base}, quote={quote}, address={address}, "
                           f"base_address={base_address}, quote_address={quote_address}")

                await client.gateway.add_pool(
                    connector_name=connector_name,
                    pool_type=pool_type,
                    network=network,
                    base=base,
                    quote=quote,
                    address=address,
                    base_address=base_address,
                    quote_address=quote_address
                )

                success_text = (
                    f"✅ *Pool Added Successfully*\n\n"
                    f"{pair_escaped} added to {connector_escaped}"
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
                async def noop_answer(text=""):
                    pass
                mock_query = SimpleNamespace(
                    message=mock_message,
                    answer=noop_answer
                )
                await show_connector_pools(mock_query, context, connector_name, network)

            except Exception as e:
                logger.error(f"Error adding pool: {e}", exc_info=True)
                error_text = f"❌ Error adding pool: {escape_markdown_v2(str(e))}"
                if message_id and chat_id:
                    await update.get_bot().edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=error_text,
                        parse_mode="MarkdownV2"
                    )

    except Exception as e:
        logger.error(f"Error handling pool input: {e}", exc_info=True)
        context.user_data.pop('awaiting_pool_input', None)
