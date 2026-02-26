"""
Command handlers for Condor Telegram bot
"""

from telegram.ext import ContextTypes


def is_gateway_network(connector_name: str) -> bool:
    """
    Check if a connector name is a Gateway network (DEX) vs a CEX connector.

    Gateway networks: solana-mainnet-beta, ethereum-mainnet, base, arbitrum, etc.
    CEX connectors: binance, binance_perpetual, hyperliquid, kucoin, etc.
    """
    if not connector_name:
        return False

    connector_lower = connector_name.lower()

    # Known Gateway network patterns
    gateway_patterns = [
        "solana",
        "ethereum",
        "base",
        "arbitrum",
        "polygon",
        "optimism",
        "avalanche",
        "mainnet",
        "devnet",
        "testnet",
    ]

    for pattern in gateway_patterns:
        if pattern in connector_lower:
            return True

    return False


def clear_all_input_states(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Clear ALL input-related states from user context.

    Call this at the start of any new command or major flow transition
    to prevent state pollution between different features.

    This is the MASTER state cleaner - it clears:
    - CLOB trading states
    - DEX trading states
    - Config states (servers, API keys, gateway)
    - Gateway wallet/pool/connector states
    """
    # CEX trading states
    context.user_data.pop("cex_state", None)
    context.user_data.pop("place_order_params", None)
    context.user_data.pop("current_positions", None)
    context.user_data.pop("cex_previous_state", None)
    context.user_data.pop("trade_params", None)
    context.user_data.pop("current_orders", None)
    context.user_data.pop("trade_menu_message_id", None)
    context.user_data.pop("trade_menu_chat_id", None)
    context.user_data.pop("current_market_price", None)
    context.user_data.pop("swap_params", None)

    # DEX trading states
    context.user_data.pop("dex_state", None)
    context.user_data.pop("swap_quote_params", None)
    context.user_data.pop("swap_execute_params", None)
    context.user_data.pop("add_position_params", None)
    context.user_data.pop("selected_pool", None)
    context.user_data.pop("pool_list_data", None)
    context.user_data.pop("position_list_data", None)
    context.user_data.pop("add_position_menu_msg_id", None)
    context.user_data.pop("add_position_menu_chat_id", None)
    context.user_data.pop("quote_swap_params", None)
    context.user_data.pop("execute_swap_params", None)
    context.user_data.pop("dex_previous_state", None)

    # Config - server modification states
    context.user_data.pop("modifying_server", None)
    context.user_data.pop("modifying_field", None)
    context.user_data.pop("awaiting_modify_input", None)
    context.user_data.pop("adding_server", None)
    context.user_data.pop("awaiting_add_server_input", None)

    # Config - API keys states
    context.user_data.pop("configuring_api_key", None)
    context.user_data.pop("awaiting_api_key_input", None)
    context.user_data.pop("api_key_config_data", None)

    # Config - gateway states
    context.user_data.pop("gateway_state", None)
    context.user_data.pop("awaiting_gateway_input", None)

    # Gateway - wallet states
    context.user_data.pop("awaiting_wallet_input", None)
    context.user_data.pop("wallet_chain", None)
    context.user_data.pop("wallet_address", None)

    # Gateway - pool states
    context.user_data.pop("awaiting_pool_input", None)
    context.user_data.pop("pool_connector", None)

    # Gateway - connector states
    context.user_data.pop("awaiting_connector_input", None)
    context.user_data.pop("connector_name", None)

    # Gateway - network states
    context.user_data.pop("awaiting_network_input", None)
    context.user_data.pop("network_list", None)
    context.user_data.pop("configuring_network", None)
    context.user_data.pop("network_config_data", None)
    context.user_data.pop("network_message_id", None)
    context.user_data.pop("network_chat_id", None)

    # Gateway - token states
    context.user_data.pop("awaiting_token_input", None)
    context.user_data.pop("token_network", None)

    # Bots - controller config states
    context.user_data.pop("bots_state", None)
    context.user_data.pop("controller_config_params", None)
    context.user_data.pop("controller_configs_list", None)
    context.user_data.pop("selected_controllers", None)
    context.user_data.pop("editing_controller_field", None)
    context.user_data.pop("deploy_params", None)
    context.user_data.pop("editing_deploy_field", None)
    context.user_data.pop("selected_configs", None)
    context.user_data.pop("configs_controller_type", None)
    context.user_data.pop("configs_type_filtered", None)
    context.user_data.pop("configs_page", None)

    # Bots - archived states
    context.user_data.pop("archived_databases", None)
    context.user_data.pop("archived_current_db", None)
    context.user_data.pop("archived_page", None)
    context.user_data.pop("archived_summaries", None)
    context.user_data.pop("archived_total_count", None)

    # Agent states
    context.user_data.pop("agent_state", None)
    context.user_data.pop("agent_selected", None)

    # Routines states
    context.user_data.pop("routines_state", None)
    context.user_data.pop("routines_editing", None)

    # Signals states
    context.user_data.pop("signals_state", None)
    context.user_data.pop("signals_editing", None)

    # Access share states
    context.user_data.pop("sharing_server", None)
    context.user_data.pop("awaiting_share_user_id", None)
    context.user_data.pop("share_target_user_id", None)
    context.user_data.pop("share_message_id", None)
    context.user_data.pop("share_chat_id", None)

    # Executors states
    context.user_data.pop("executors_state", None)
    context.user_data.pop("executor_config_params", None)
    context.user_data.pop("executor_wizard_step", None)
    context.user_data.pop("executor_wizard_data", None)
    context.user_data.pop("executor_list_page", None)
    context.user_data.pop("executor_chart_interval", None)
    context.user_data.pop("executor_wizard_type", None)

    # Clear DataManager context (resets active TTLs to idle)
    user_id = context.user_data.get("_user_id")
    if user_id:
        try:
            from condor.data_manager import dm_clear_context
            dm_clear_context(user_id)
        except Exception:
            pass

    # Portfolio snapshot states (large transient data)
    context.user_data.pop("portfolio_text_message_id", None)
    context.user_data.pop("portfolio_photo_message_id", None)
    context.user_data.pop("portfolio_chat_id", None)
    context.user_data.pop("portfolio_graph_interval", None)
    context.user_data.pop("portfolio_server_name", None)
    context.user_data.pop("portfolio_server_status", None)
    context.user_data.pop("portfolio_current_value", None)
    context.user_data.pop("portfolio_balances", None)
    context.user_data.pop("portfolio_accounts_distribution", None)
    context.user_data.pop("portfolio_changes_24h", None)
    context.user_data.pop("portfolio_pnl_indicators", None)
    context.user_data.pop("portfolio_connector_keys", None)
    context.user_data.pop("portfolio_view_mode", None)
    context.user_data.pop("_portfolio_refresh", None)
