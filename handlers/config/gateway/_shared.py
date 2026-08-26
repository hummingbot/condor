"""
Shared utilities and imports for gateway modules
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)


def extract_network_id(network_item: Any) -> str:
    """
    Extract network_id string from network data.
    Handles both dict and string formats.

    Args:
        network_item: Network data (can be dict or string)

    Returns:
        Network ID as string
    """
    if isinstance(network_item, dict):
        return network_item.get(
            "network_id", network_item.get("name", str(network_item))
        )
    return str(network_item)


def filter_pool_connectors(connectors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Filter connectors to only those that support liquidity pools.

    Args:
        connectors: List of connector dicts from API

    Returns:
        List of connectors with 'amm' or 'clmm' trading types
    """
    return [
        c
        for c in connectors
        if any(
            trading_type in ["amm", "clmm"]
            for trading_type in c.get("trading_types", [])
        )
    ]


def get_connector_networks(
    connector_name: str, connectors_data: Dict[str, Dict[str, Any]]
) -> List[str]:
    """
    Get list of networks supported by a specific connector.

    Args:
        connector_name: Name of the connector
        connectors_data: Dict mapping connector names to their full data

    Returns:
        List of network IDs supported by the connector
    """
    connector_info = connectors_data.get(connector_name, {})
    return connector_info.get("networks", [])


async def get_default_networks(client) -> List[str]:
    """
    Get combined default networks from solana and ethereum configs.

    Fetches default_networks from solana-mainnet-beta and ethereum-mainnet
    and returns the combined list of network IDs.

    Args:
        client: HummingbotAPIClient instance

    Returns:
        List of default network IDs (e.g., ['solana-mainnet-beta', 'ethereum-mainnet'])
    """
    default_networks = []

    # Check solana defaults
    try:
        solana_config = await client.gateway.get_network_config("solana-mainnet-beta")
        solana_defaults = solana_config.get("default_networks", [])
        for network in solana_defaults:
            network_id = f"solana-{network}"
            if network_id not in default_networks:
                default_networks.append(network_id)
    except Exception as e:
        logger.debug(f"Could not fetch solana defaults: {e}")

    # Check ethereum defaults
    try:
        eth_config = await client.gateway.get_network_config("ethereum-mainnet")
        eth_defaults = eth_config.get("default_networks", [])
        for network in eth_defaults:
            network_id = f"ethereum-{network}"
            if network_id not in default_networks:
                default_networks.append(network_id)
    except Exception as e:
        logger.debug(f"Could not fetch ethereum defaults: {e}")

    return default_networks


# Refusal rendered when a non-owner reaches a Gateway mutation from Telegram.
OWNER_REQUIRED_MESSAGE = "Only the server owner can change the Gateway token list"


def require_gateway_owner(
    user_id: int, chat_id: int, preferred_server: Optional[str] = None
) -> Tuple[Optional[str], bool]:
    """Resolve the target server and whether ``user_id`` may mutate its Gateway.

    The OWNER line this enforces is the one ``condor/web/auth.py::require_owner``
    spells out (SEC-153, extended to the gateway by SEC-166 and to its token list
    by SEC-207): *reading* a server's Gateway state is TRADER, but its token list,
    keystore and RPC endpoints are the owner's machine, not a trading action. An
    entry deleted from the token list makes every balance for that mint read 0 for
    everyone on the server, not just the person who pressed the button.

    This is the Telegram counterpart of that function rather than a call into it:
    ``require_owner`` raises ``HTTPException``, which is a web concern. Callers
    here render a refusal and return.

    Admins keep the bypass they hold everywhere else — ``get_server_permission``
    already answers OWNER for them.

    Returns:
        ``(server_name, is_owner)``. ``server_name`` is the server the mutation
        would land on, resolved the same way ``get_client_for_chat`` resolves it
        for a caller that passes no ``user_id``: the explicit choice wins over the
        chat default. Pass it back as ``preferred_server`` so the permission and
        the mutation cannot disagree about which server they mean.
    """
    from config_manager import ServerPermission, get_config_manager

    cm = get_config_manager()

    server_name = (
        preferred_server
        if preferred_server and cm.get_server(preferred_server)
        else cm.get_chat_default_server(chat_id)
    )

    if not server_name:
        return None, False

    return server_name, cm.get_server_permission(user_id, server_name) == (
        ServerPermission.OWNER
    )
