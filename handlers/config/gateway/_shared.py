"""
Shared utilities and imports for gateway modules
"""

import logging
from typing import Any, Dict, List

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
