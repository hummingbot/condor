"""
Command handlers for Condor Telegram bot
"""

from telegram.ext import ContextTypes


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
    # CLOB trading states
    context.user_data.pop("clob_state", None)
    context.user_data.pop("place_order_params", None)
    context.user_data.pop("current_positions", None)
    context.user_data.pop("clob_previous_state", None)

    # DEX trading states
    context.user_data.pop("dex_state", None)
    context.user_data.pop("swap_quote_params", None)
    context.user_data.pop("swap_execute_params", None)
    context.user_data.pop("add_position_params", None)
    context.user_data.pop("selected_pool", None)
    context.user_data.pop("pool_list_data", None)
    context.user_data.pop("position_list_data", None)

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

    # Gateway - token states
    context.user_data.pop("awaiting_token_input", None)
    context.user_data.pop("token_network", None)
