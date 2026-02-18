"""
Gateway configuration module - Modular gateway management

This module provides a modular structure for gateway configuration:
- menu.py: Gateway main menu and server selection
- deployment.py: Gateway deployment, lifecycle, and logs
- wallets.py: Wallet management
- connectors.py: DEX connector configuration
- networks.py: Network configuration
- pools.py: Liquidity pool management
- tokens.py: Token management
"""

from telegram import Update
from telegram.ext import ContextTypes

from utils.auth import restricted


@restricted
async def gateway_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /gateway command - show Gateway configuration directly."""
    from handlers import clear_all_input_states
    from utils.telegram_helpers import create_mock_query_from_message

    from .menu import show_gateway_menu

    clear_all_input_states(context)
    # Clear trading states that might interfere
    context.user_data.pop("dex_state", None)
    context.user_data.pop("cex_state", None)
    mock_query = await create_mock_query_from_message(update, "Loading Gateway...")
    await show_gateway_menu(mock_query, context)


from .connectors import (
    handle_connector_action,
    handle_connector_config_input,
    show_connectors_menu,
)

# Import all submodule handlers
from .deployment import (
    deploy_gateway_with_image,
    handle_deployment_input,
    prompt_custom_image,
    restart_gateway,
    show_gateway_logs,
    start_deploy_gateway,
    stop_gateway,
)
from .menu import handle_server_selection, show_gateway_menu, show_server_selection
from .networks import (
    handle_network_action,
    handle_network_config_input,
    show_networks_menu,
)
from .pools import handle_pool_action, handle_pool_input, show_pools_menu
from .tokens import handle_token_action, handle_token_input, show_tokens_menu
from .wallets import handle_wallet_action, handle_wallet_input, show_wallets_menu


async def handle_gateway_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Main router for gateway-related callbacks"""
    query = update.callback_query

    # When entering gateway config, clear any trading states that might interfere
    # This prevents issues like DEX pool_info state capturing gateway wallet input
    if query.data == "config_gateway":
        context.user_data.pop("dex_state", None)
        context.user_data.pop("cex_state", None)
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


async def handle_gateway_input(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Route text input to the appropriate gateway module"""
    # Check which type of input we're awaiting
    if context.user_data.get("awaiting_gateway_input"):
        await handle_deployment_input(update, context)
    elif context.user_data.get("awaiting_wallet_input"):
        await handle_wallet_input(update, context)
    elif context.user_data.get("awaiting_token_input"):
        await handle_token_input(update, context)
    elif context.user_data.get("awaiting_pool_input"):
        await handle_pool_input(update, context)
    elif context.user_data.get("awaiting_network_input"):
        await handle_network_config_input(update, context)
    elif context.user_data.get("awaiting_connector_config"):
        await handle_connector_config_input(update, context)


__all__ = [
    "gateway_command",
    "handle_gateway_callback",
    "handle_gateway_input",
]
