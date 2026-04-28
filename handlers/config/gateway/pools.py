"""
Gateway liquidity pool management functions
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from ..user_preferences import get_active_server
from ._shared import (
    escape_markdown_v2,
    extract_network_id,
    logger,
)


async def show_pools_menu(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show pools menu - select network to view pools (like tokens)"""
    try:
        from config_manager import get_config_manager

        await query.answer("Loading networks...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )
        response = await client.gateway.list_networks()

        networks = response.get("networks", [])

        if not networks:
            message_text = (
                "💧 *Liquidity Pools*\n\n"
                "No networks available\\.\n\n"
                "_Ensure Gateway is running\\._"
            )
            keyboard = [
                [
                    InlineKeyboardButton(
                        "« Back to Gateway", callback_data="config_gateway"
                    )
                ]
            ]
        else:
            # Limit to first 20 networks
            network_buttons = []
            context.user_data["pool_network_list"] = networks[:20]

            for idx, network_item in enumerate(networks[:20]):
                network_id = extract_network_id(network_item)
                network_buttons.append(
                    [
                        InlineKeyboardButton(
                            network_id, callback_data=f"gateway_pool_network_{idx}"
                        )
                    ]
                )

            count_escaped = escape_markdown_v2(str(len(networks)))
            message_text = (
                f"💧 *Liquidity Pools* \\({count_escaped} networks\\)\n\n"
                "_Select a network to view and manage pools:_"
            )

            keyboard = network_buttons + [
                [
                    InlineKeyboardButton(
                        "« Back to Gateway", callback_data="config_gateway"
                    )
                ]
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )

    except Exception as e:
        # Ignore "message not modified" errors - they're harmless
        if "not modified" in str(e).lower():
            logger.debug(f"Message not modified (ignored): {e}")
            return
        logger.error(f"Error showing pools menu: {e}", exc_info=True)
        error_text = f"❌ Error loading networks: {escape_markdown_v2(str(e))}"
        keyboard = [
            [InlineKeyboardButton("« Back to Gateway", callback_data="config_gateway")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            error_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def handle_pool_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle pool-specific actions"""
    action_data = query.data.replace("gateway_pool_", "")

    if action_data.startswith("network_"):
        # Show pools for selected network
        network_idx_str = action_data.replace("network_", "")
        try:
            network_idx = int(network_idx_str)
            network_list = context.user_data.get("pool_network_list", [])
            if 0 <= network_idx < len(network_list):
                network_item = network_list[network_idx]
                network_id = extract_network_id(network_item)
                await show_network_pools(query, context, network_id)
            else:
                await query.answer("❌ Network not found")
        except ValueError:
            await query.answer("❌ Invalid network")
    elif action_data.startswith("add_"):
        # Add pool to network
        network_id = action_data.replace("add_", "")
        await prompt_add_pool(query, context, network_id)
    elif action_data.startswith("remove_"):
        # Show pool list to manage (remove)
        network_id = action_data.replace("remove_", "")
        await prompt_remove_pool(query, context, network_id)
    elif action_data.startswith("select_"):
        # Show options for selected pool
        try:
            pool_idx = int(action_data.replace("select_", ""))
            await show_pool_options(query, context, pool_idx)
        except ValueError:
            await query.answer("❌ Invalid pool")
    elif action_data.startswith("del_"):
        # Delete selected pool (show confirmation)
        try:
            pool_idx = int(action_data.replace("del_", ""))
            pools = context.user_data.get("pool_manage_list", [])
            network_id = context.user_data.get("pool_manage_network")
            if pools and pool_idx < len(pools) and network_id:
                pool = pools[pool_idx]
                pool_address = pool.get("address", pool.get("pool_id", ""))
                pool_type = pool.get("type", "")
                await show_delete_pool_confirmation(
                    query, context, network_id, pool_address, pool_type, pool_idx
                )
            else:
                await query.answer("❌ Pool not found")
        except ValueError:
            await query.answer("❌ Invalid pool")
    elif action_data == "confirm_remove":
        # Get pool info from user_data (stored to avoid 64-byte callback limit)
        pending_delete = context.user_data.get("pending_pool_delete")
        if pending_delete:
            network_id = pending_delete["network_id"]
            pool_address = pending_delete["pool_address"]
            pool_type = pending_delete.get("pool_type")
            context.user_data.pop("pending_pool_delete", None)  # Clean up
            await remove_pool(query, context, network_id, pool_address, pool_type)
        else:
            await query.answer("❌ Pool deletion expired. Please try again.")
    elif action_data.startswith("view_"):
        # Back to viewing pools for network
        network_id = action_data.replace("view_", "")
        await show_network_pools(query, context, network_id)
    elif action_data.startswith("page_"):
        # Handle pagination
        try:
            page = int(action_data.replace("page_", ""))
            network_id = context.user_data.get("pool_view_network")
            if network_id:
                await show_network_pools(query, context, network_id, page=page)
            else:
                await query.answer("❌ Network not found")
        except ValueError:
            await query.answer("❌ Invalid page")
    else:
        await query.answer("Unknown action")


async def show_network_pools(
    query, context: ContextTypes.DEFAULT_TYPE, network_id: str, page: int = 0
) -> None:
    """Show pools for a specific network with button grid and pagination"""
    POOLS_PER_PAGE = 16
    COLUMNS = 4

    try:
        from config_manager import get_config_manager

        await query.answer("Loading pools...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )

        # Get pools for the network
        try:
            result = await client.gateway.get_network_pools(network_id)
            pools = result.get("pools", []) if isinstance(result, dict) else result
        except Exception as e:
            logger.warning(f"Failed to get pools for {network_id}: {e}")
            pools = []

        network_escaped = escape_markdown_v2(network_id)

        if not pools:
            message_text = (
                f"💧 *{network_escaped}*\n\n"
                "_No pools found\\._\n\n"
                "Add custom pools to get started\\."
            )
            keyboard = [
                [
                    InlineKeyboardButton(
                        "➕ Add Pool", callback_data=f"gateway_pool_add_{network_id}"
                    )
                ],
                [InlineKeyboardButton("« Back", callback_data="gateway_pools")],
            ]
        else:
            # Store all pools for selection
            context.user_data["pool_manage_list"] = pools
            context.user_data["pool_manage_network"] = network_id
            context.user_data["pool_view_network"] = network_id
            context.user_data["pool_view_page"] = page

            # Calculate pagination
            total_pools = len(pools)
            total_pages = (total_pools + POOLS_PER_PAGE - 1) // POOLS_PER_PAGE
            page = max(0, min(page, total_pages - 1))  # Clamp page to valid range

            start_idx = page * POOLS_PER_PAGE
            end_idx = min(start_idx + POOLS_PER_PAGE, total_pools)
            page_pools = pools[start_idx:end_idx]

            # Build page indicator
            if total_pages > 1:
                page_indicator = f" \\[{page + 1}/{total_pages}\\]"
            else:
                page_indicator = ""

            pool_count = escape_markdown_v2(str(total_pools))
            message_text = (
                f"💧 *{network_escaped}*{page_indicator}\n\n"
                f"*Pools* \\({pool_count} total\\)\n"
                "_Select a pool to view or remove:_"
            )

            # Build pool buttons in grid (4 columns)
            keyboard = []
            row = []
            for idx, pool in enumerate(page_pools):
                global_idx = start_idx + idx
                # Use trading pair as button text, truncate if needed
                trading_pair = pool.get("trading_pair", pool.get("tradingPair", "?/?"))
                label = trading_pair[:10]  # Truncate long pairs
                row.append(
                    InlineKeyboardButton(
                        label, callback_data=f"gateway_pool_select_{global_idx}"
                    )
                )

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
                    nav_buttons.append(
                        InlineKeyboardButton(
                            "« Prev", callback_data=f"gateway_pool_page_{page - 1}"
                        )
                    )
                if page < total_pages - 1:
                    nav_buttons.append(
                        InlineKeyboardButton(
                            "Next »", callback_data=f"gateway_pool_page_{page + 1}"
                        )
                    )
                if nav_buttons:
                    keyboard.append(nav_buttons)

            # Action buttons
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "➕ Add Pool", callback_data=f"gateway_pool_add_{network_id}"
                    ),
                    InlineKeyboardButton(
                        "🔄 Refresh", callback_data=f"gateway_pool_view_{network_id}"
                    ),
                ]
            )
            keyboard.append(
                [InlineKeyboardButton("« Back", callback_data="gateway_pools")]
            )

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )

    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass  # Ignore - message content is the same
        else:
            raise
    except Exception as e:
        # Ignore "message not modified" errors - they're harmless
        if "not modified" in str(e).lower():
            logger.debug(f"Message not modified (ignored): {e}")
            return
        logger.error(f"Error showing network pools: {e}", exc_info=True)
        error_text = f"❌ Error loading pools: {escape_markdown_v2(str(e))}"
        keyboard = [[InlineKeyboardButton("« Back", callback_data="gateway_pools")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            error_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def show_pool_options(
    query, context: ContextTypes.DEFAULT_TYPE, pool_idx: int
) -> None:
    """Show details and options for a selected pool"""
    try:
        pools = context.user_data.get("pool_manage_list", [])
        network_id = context.user_data.get("pool_manage_network")

        if not pools or pool_idx >= len(pools) or not network_id:
            await query.answer("❌ Pool not found")
            return

        pool = pools[pool_idx]
        trading_pair = pool.get("trading_pair", pool.get("tradingPair", "N/A"))
        pool_type = pool.get("type", "N/A")
        connector = pool.get("connector_name", pool.get("connector", "N/A"))
        address = pool.get("address", pool.get("pool_id", "N/A"))
        fee_pct = pool.get("fee_pct", pool.get("feePct"))

        network_escaped = escape_markdown_v2(network_id)
        pair_escaped = escape_markdown_v2(str(trading_pair))
        type_escaped = escape_markdown_v2(str(pool_type))
        connector_escaped = escape_markdown_v2(str(connector))

        message_text = (
            f"💧 *{pair_escaped}* on {network_escaped}\n\n"
            f"*Type:* {type_escaped}\n"
            f"*Connector:* {connector_escaped}\n"
        )
        if fee_pct is not None:
            fee_escaped = escape_markdown_v2(f"{fee_pct}%")
            message_text += f"*Fee:* {fee_escaped}\n"
        message_text += f"*Address:*\n`{escape_markdown_v2(address)}`\n\n"
        message_text += "_Choose an action:_"

        # Store selected pool info
        context.user_data["selected_pool_idx"] = pool_idx

        # Get current page to return to correct page
        current_page = context.user_data.get("pool_view_page", 0)

        keyboard = [
            [
                InlineKeyboardButton(
                    "🗑 Remove", callback_data=f"gateway_pool_del_{pool_idx}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "« Back", callback_data=f"gateway_pool_page_{current_page}"
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error showing pool options: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def prompt_add_pool(
    query, context: ContextTypes.DEFAULT_TYPE, network_id: str
) -> None:
    """Prompt user to enter pool details"""
    try:
        network_escaped = escape_markdown_v2(network_id)

        # Clear any lingering states from previous operations
        context.user_data.pop("dex_state", None)
        context.user_data.pop("cex_state", None)

        context.user_data["awaiting_pool_input"] = "pool_details"
        context.user_data["pool_network"] = network_id
        context.user_data["pool_message_id"] = query.message.message_id
        context.user_data["pool_chat_id"] = query.message.chat_id

        message_text = (
            f"➕ *Add Pool to {network_escaped}*\n\n"
            "*Enter pool details in this format:*\n"
            "`connector,pool_type,address`\n\n"
            "*Example:*\n"
            "`raydium,clmm,8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj`\n\n"
            "_Pool info \\(tokens, fees\\) will be fetched automatically\\._\n\n"
            "⚠️ _Restart Gateway after adding for changes to take effect\\._"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "« Cancel", callback_data=f"gateway_pool_view_{network_id}"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error prompting add pool: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def prompt_remove_pool(
    query, context: ContextTypes.DEFAULT_TYPE, network_id: str
) -> None:
    """Show list of pools to select for removal"""
    try:
        from config_manager import get_config_manager

        await query.answer("Loading pools...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )

        # Get pools for the network
        try:
            result = await client.gateway.get_network_pools(network_id)
            pools = result.get("pools", []) if isinstance(result, dict) else result
        except Exception as e:
            logger.warning(f"Failed to get pools for {network_id}: {e}")
            pools = []

        network_escaped = escape_markdown_v2(network_id)

        if not pools:
            message_text = (
                f"💧 *Manage Pools \\- {network_escaped}*\n\n"
                "_No pools found to manage\\._"
            )
            keyboard = [
                [
                    InlineKeyboardButton(
                        "« Back", callback_data=f"gateway_pool_view_{network_id}"
                    )
                ]
            ]
        else:
            # Store pools in user_data for later retrieval
            context.user_data["pool_manage_list"] = pools[:20]
            context.user_data["pool_manage_network"] = network_id

            # Create buttons for each pool (limit to 20)
            pool_buttons = []
            for idx, pool in enumerate(pools[:20]):
                trading_pair = pool.get("trading_pair", pool.get("tradingPair", "?/?"))
                pool_type = pool.get("type", "")
                label = f"{trading_pair} ({pool_type})"
                pool_buttons.append(
                    [
                        InlineKeyboardButton(
                            label, callback_data=f"gateway_pool_select_{idx}"
                        )
                    ]
                )

            count_escaped = escape_markdown_v2(str(len(pools)))
            message_text = (
                f"💧 *Manage Pools \\- {network_escaped}*\n\n"
                f"_Select a pool to view or remove \\({count_escaped} total\\):_"
            )

            keyboard = pool_buttons + [
                [
                    InlineKeyboardButton(
                        "« Back", callback_data=f"gateway_pool_view_{network_id}"
                    )
                ]
            ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error showing pool list: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def show_delete_pool_confirmation(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    network_id: str,
    pool_address: str,
    pool_type: str,
    pool_idx: int,
) -> None:
    """Show confirmation dialog before deleting a pool"""
    try:
        from config_manager import get_config_manager

        chat_id = query.message.chat_id

        # Get pool details from stored list
        pools = context.user_data.get("pool_manage_list", [])
        pool_info = pools[pool_idx] if pool_idx < len(pools) else None

        network_escaped = escape_markdown_v2(network_id)
        addr_display = (
            pool_address[:10] + "..." + pool_address[-8:]
            if len(pool_address) > 20
            else pool_address
        )
        addr_escaped = escape_markdown_v2(addr_display)

        message_text = f"🗑 *Delete Pool*\n\nNetwork: *{network_escaped}*\n"

        if pool_info:
            trading_pair = pool_info.get("trading_pair", pool_info.get("tradingPair"))
            if trading_pair:
                pair_escaped = escape_markdown_v2(trading_pair)
                message_text += f"Pool: *{pair_escaped}*\n"

        if pool_type:
            type_escaped = escape_markdown_v2(pool_type)
            message_text += f"Type: *{type_escaped}*\n"

        message_text += (
            f"Address: `{addr_escaped}`\n\n"
            f"⚠️ This will remove the pool from *{network_escaped}*\\.\n"
            "You will need to restart the Gateway for changes to take effect\\.\n\n"
            "Are you sure you want to delete this pool?"
        )

        # Store pool info in user_data to avoid exceeding Telegram's 64-byte callback limit
        context.user_data["pending_pool_delete"] = {
            "network_id": network_id,
            "pool_address": pool_address,
            "pool_type": pool_type,
        }

        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ Yes, Delete", callback_data="gateway_pool_confirm_remove"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel", callback_data=f"gateway_pool_view_{network_id}"
                )
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            message_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )
        await query.answer()

    except Exception as e:
        logger.error(f"Error showing delete confirmation: {e}", exc_info=True)
        await query.answer(f"❌ Error: {str(e)[:100]}")


async def remove_pool(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    network_id: str,
    pool_address: str,
    pool_type: str,
) -> None:
    """Remove a pool from Gateway"""
    try:
        from config_manager import get_config_manager

        await query.answer("Removing pool...")

        chat_id = query.message.chat_id
        client = await get_config_manager().get_client_for_chat(
            chat_id, preferred_server=get_active_server(context.user_data)
        )
        await client.gateway.delete_network_pool(
            network_id=network_id,
            address=pool_address,
            pool_type=pool_type
        )

        network_escaped = escape_markdown_v2(network_id)
        addr_display = (
            pool_address[:10] + "..." + pool_address[-8:]
            if len(pool_address) > 20
            else pool_address
        )
        addr_escaped = escape_markdown_v2(addr_display)

        success_text = (
            f"✅ *Pool Removed*\n\n"
            f"`{addr_escaped}`\n\n"
            f"Removed from {network_escaped}\n\n"
            "⚠️ _Restart Gateway for changes to take effect\\._"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "« Back to Pools", callback_data=f"gateway_pool_view_{network_id}"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.edit_text(
            success_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )

        import asyncio

        await asyncio.sleep(2)
        await show_network_pools(query, context, network_id)

    except Exception as e:
        logger.error(f"Error removing pool: {e}", exc_info=True)
        error_text = f"❌ Error removing pool: {escape_markdown_v2(str(e))}"
        keyboard = [
            [
                InlineKeyboardButton(
                    "« Back", callback_data=f"gateway_pool_view_{network_id}"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            error_text, parse_mode="MarkdownV2", reply_markup=reply_markup
        )


async def handle_pool_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text input during pool addition flow"""
    awaiting_field = context.user_data.get("awaiting_pool_input")
    logger.info(
        f"handle_pool_input called. awaiting_field={awaiting_field}, user_data keys={list(context.user_data.keys())}"
    )

    if not awaiting_field:
        logger.info("No awaiting_pool_input, returning")
        return

    # Delete user's input message
    try:
        await update.message.delete()
    except:
        pass

    try:
        from types import SimpleNamespace

        from config_manager import get_config_manager

        network_id = context.user_data.get("pool_network")
        message_id = context.user_data.get("pool_message_id")
        chat_id = context.user_data.get("pool_chat_id")
        logger.info(
            f"Pool input: network_id={network_id}, message_id={message_id}, chat_id={chat_id}"
        )

        if awaiting_field == "pool_details":
            # Parse pool details: connector,pool_type,address
            pool_input = update.message.text.strip()
            parts = [p.strip() for p in pool_input.split(",")]

            if len(parts) != 3:
                await update.get_bot().send_message(
                    chat_id=chat_id,
                    text="❌ Invalid format. Use: connector,pool_type,address\n\nExample: raydium,clmm,8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj",
                )
                return

            connector_name, pool_type, address = parts

            # Clear context (including any lingering states from previous operations)
            context.user_data.pop("awaiting_pool_input", None)
            context.user_data.pop("pool_network", None)
            context.user_data.pop("pool_message_id", None)
            context.user_data.pop("pool_chat_id", None)
            context.user_data.pop("dex_state", None)

            # Show adding message
            network_escaped = escape_markdown_v2(network_id)
            connector_escaped = escape_markdown_v2(connector_name)
            type_escaped = escape_markdown_v2(pool_type)

            if message_id and chat_id:
                await update.get_bot().edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=f"⏳ *Adding Pool*\n\nConnector: {connector_escaped}\nType: {type_escaped}\nNetwork: {network_escaped}\n\n_Fetching pool info\\.\\.\\._",
                    parse_mode="MarkdownV2",
                )

            try:
                client = await get_config_manager().get_client_for_chat(
                    chat_id, preferred_server=get_active_server(context.user_data)
                )

                logger.info(
                    f"Adding pool: network_id={network_id}, connector={connector_name}, "
                    f"pool_type={pool_type}, address={address}"
                )

                # Use new network-based endpoint
                await client.gateway.add_network_pool(
                    network_id=network_id,
                    connector_name=connector_name,
                    pool_type=pool_type,
                    address=address,
                )

                success_text = (
                    f"✅ *Pool Added Successfully*\n\n"
                    f"Added to {connector_escaped} on {network_escaped}\n\n"
                    "⚠️ _Restart Gateway for changes to take effect\\._"
                )

                if message_id and chat_id:
                    await update.get_bot().edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=success_text,
                        parse_mode="MarkdownV2",
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
                        reply_markup=reply_markup,
                    ),
                    chat_id=chat_id,
                    message_id=message_id,
                )
                mock_query = SimpleNamespace(message=mock_message, answer=mock_answer)
                await show_network_pools(mock_query, context, network_id)

            except Exception as e:
                logger.error(f"Error adding pool: {e}", exc_info=True)
                error_text = f"❌ Error adding pool: {escape_markdown_v2(str(e))}"
                if message_id and chat_id:
                    await update.get_bot().edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=error_text,
                        parse_mode="MarkdownV2",
                    )

    except Exception as e:
        logger.error(f"Error handling pool input: {e}", exc_info=True)
        context.user_data.pop("awaiting_pool_input", None)
