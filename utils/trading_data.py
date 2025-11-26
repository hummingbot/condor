"""
Trading data utilities for fetching positions and orders
"""
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


async def get_network_tokens(client, network_id: str) -> Dict[str, str]:
    """
    Fetch tokens from Gateway for a specific network.

    Args:
        client: HummingbotAPIClient instance
        network_id: Network identifier (e.g., 'solana-mainnet-beta')

    Returns:
        Dictionary mapping token address to symbol {address: symbol}
    """
    try:
        if not hasattr(client, 'gateway'):
            return {}

        tokens = []

        # Try get_network_tokens first
        try:
            if hasattr(client.gateway, 'get_network_tokens'):
                response = await client.gateway.get_network_tokens(network_id)
                tokens = response.get('tokens', []) if response else []
        except Exception as e:
            logger.debug(f"get_network_tokens failed for {network_id}: {e}")

        # Fallback to get_network_config
        if not tokens:
            try:
                config = await client.gateway.get_network_config(network_id)
                tokens = config.get('tokens', []) if config else []
            except Exception as e:
                logger.debug(f"get_network_config failed for {network_id}: {e}")

        # Build address -> symbol mapping
        token_map = {}
        for token in tokens:
            address = token.get('address', '')
            symbol = token.get('symbol', '')
            if address and symbol:
                token_map[address] = symbol

        logger.debug(f"Loaded {len(token_map)} tokens for {network_id}")
        return token_map

    except Exception as e:
        logger.warning(f"Failed to load tokens for {network_id}: {e}")
        return {}


async def get_tokens_for_networks(client, networks: List[str]) -> Dict[str, str]:
    """
    Fetch tokens from Gateway for multiple networks in parallel.

    Args:
        client: HummingbotAPIClient instance
        networks: List of network identifiers

    Returns:
        Combined dictionary mapping token address to symbol
    """
    import asyncio

    if not networks:
        return {}

    # Fetch tokens for all networks in parallel
    unique_networks = list(set(networks))
    results = await asyncio.gather(
        *[get_network_tokens(client, net) for net in unique_networks],
        return_exceptions=True
    )

    # Combine all token maps
    combined = {}
    for result in results:
        if isinstance(result, dict):
            combined.update(result)

    return combined


async def get_perpetual_positions(client, account_names: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Get all perpetual positions across accounts

    Args:
        client: HummingbotAPIClient instance
        account_names: Optional list of account names to filter by

    Returns:
        Dictionary with positions data and count
    """
    try:
        # Use the trading API to get positions
        positions_data = await client.trading.get_positions()

        if not positions_data:
            return {"positions": [], "total": 0}

        # Handle both list and dict responses
        if isinstance(positions_data, list):
            positions = positions_data
        elif isinstance(positions_data, dict):
            # Try 'data' key first (CLOB API format), then 'positions' key
            positions = positions_data.get('data', positions_data.get('positions', []))
        else:
            positions = []

        # Filter by account names if specified
        if account_names and positions:
            positions = [
                pos for pos in positions
                if pos.get('account_name') in account_names
            ]

        return {"positions": positions, "total": len(positions)}

    except Exception as e:
        logger.error(f"Error getting perpetual positions: {e}", exc_info=True)
        return {"positions": [], "total": 0}


async def get_active_orders(client, account_names: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Get all active (open) orders across accounts

    Args:
        client: HummingbotAPIClient instance
        account_names: Optional list of account names to filter by

    Returns:
        Dictionary with orders data and count
    """
    try:
        # Use the trading API to search for active orders
        # First, try using get_active_orders() if available
        try:
            orders_data = await client.trading.get_active_orders()
        except AttributeError:
            # Fall back to search_orders with status filter
            # Create filter request for OPEN orders
            filter_request = {"status": "OPEN"}
            if account_names:
                filter_request["account_names"] = account_names
            orders_data = await client.trading.search_orders(filter_request)

        if not orders_data:
            return {"orders": [], "total": 0}

        # Filter by account names if specified and not already filtered
        if account_names and isinstance(orders_data, list):
            orders_data = [
                order for order in orders_data
                if order.get('account_name') in account_names
            ]

        # Handle both list and dict responses
        if isinstance(orders_data, list):
            return {"orders": orders_data, "total": len(orders_data)}
        elif isinstance(orders_data, dict):
            orders = orders_data.get('orders', [])
            return {"orders": orders, "total": len(orders)}

        return {"orders": [], "total": 0}

    except Exception as e:
        logger.error(f"Error getting active orders: {e}", exc_info=True)
        return {"orders": [], "total": 0}


def _is_position_active(pos: Dict[str, Any]) -> bool:
    """
    Check if a CLMM position is actually active (has liquidity).

    Positions that are closed on-chain may still appear in the database
    with status="OPEN". This function filters them out by checking liquidity.

    Args:
        pos: Position data dictionary

    Returns:
        True if position appears to be active (has liquidity)
    """
    # Check liquidity field (exact field name may vary)
    liquidity = pos.get('liquidity') or pos.get('current_liquidity') or pos.get('liq')
    if liquidity is not None:
        try:
            if float(liquidity) <= 0:
                return False
        except (ValueError, TypeError):
            pass

    # Check if position has any token amounts remaining
    base_amount = pos.get('base_amount') or pos.get('amount_base') or pos.get('token_a_amount')
    quote_amount = pos.get('quote_amount') or pos.get('amount_quote') or pos.get('token_b_amount')

    if base_amount is not None and quote_amount is not None:
        try:
            if float(base_amount) <= 0 and float(quote_amount) <= 0:
                return False
        except (ValueError, TypeError):
            pass

    # If we can't determine, assume it's active
    return True


async def get_lp_positions(client, account_names: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Get all LP (CLMM) positions from the database.

    Args:
        client: HummingbotAPIClient instance
        account_names: Optional list of account names to filter by

    Returns:
        Dictionary with LP positions data and count
    """
    try:
        # Check if client has gateway_clmm capability
        if not hasattr(client, 'gateway_clmm'):
            logger.debug("Client doesn't have gateway_clmm - LP positions not available")
            return {"positions": [], "total": 0}

        try:
            search_result = await client.gateway_clmm.search_positions(
                limit=100,
                offset=0,
                status="OPEN",
            )
        except Exception as e:
            logger.debug(f"CLMM search_positions not available: {e}")
            return {"positions": [], "total": 0}

        if not search_result or not isinstance(search_result, dict):
            return {"positions": [], "total": 0}

        positions = search_result.get("data", [])

        # Filter out positions that appear to be closed (0 liquidity)
        active_positions = [p for p in positions if _is_position_active(p)]

        if len(active_positions) < len(positions):
            logger.info(f"Filtered {len(positions) - len(active_positions)} closed positions (0 liquidity)")

        return {
            "positions": active_positions,
            "total": len(active_positions)
        }

    except Exception as e:
        logger.error(f"Error getting LP positions: {e}", exc_info=True)
        return {"positions": [], "total": 0}


async def get_portfolio_overview(
    client,
    account_names: Optional[List[str]] = None,
    include_balances: bool = True,
    include_perp_positions: bool = True,
    include_lp_positions: bool = True,
    include_active_orders: bool = True,
) -> Dict[str, Any]:
    """
    Get a unified portfolio overview with all position types

    Args:
        client: HummingbotAPIClient instance
        account_names: Optional list of account names to filter by
        include_balances: Include token balances (default: True)
        include_perp_positions: Include perpetual positions (default: True)
        include_lp_positions: Include LP (CLMM) positions (default: True)
        include_active_orders: Include active orders (default: True)

    Returns:
        Dictionary containing all portfolio data:
        {
            'balances': {...},  # Portfolio state from get_state()
            'perp_positions': {'positions': [...], 'total': N},
            'lp_positions': {'positions': [...], 'total': N},
            'active_orders': {'orders': [...], 'total': N}
        }
    """
    import asyncio

    try:
        # Prepare tasks for parallel execution
        tasks = {}

        if include_balances:
            tasks['balances'] = client.portfolio.get_state()

        if include_perp_positions:
            tasks['perp_positions'] = get_perpetual_positions(client, account_names)

        if include_lp_positions:
            tasks['lp_positions'] = get_lp_positions(client, account_names)

        if include_active_orders:
            tasks['active_orders'] = get_active_orders(client, account_names)

        # Execute all tasks in parallel
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        # Map results back to their keys
        data = {}
        for i, key in enumerate(tasks.keys()):
            result = results[i]
            if isinstance(result, Exception):
                logger.error(f"Error fetching {key}: {result}")
                # Set default empty values on error
                if key == 'balances':
                    data[key] = None
                else:
                    data[key] = {"positions": [], "total": 0} if "positions" in key else {"orders": [], "total": 0}
            else:
                data[key] = result

        return {
            'balances': data.get('balances'),
            'perp_positions': data.get('perp_positions', {"positions": [], "total": 0}),
            'lp_positions': data.get('lp_positions', {"positions": [], "total": 0}),
            'active_orders': data.get('active_orders', {"orders": [], "total": 0}),
        }

    except Exception as e:
        logger.error(f"Error in get_portfolio_overview: {e}", exc_info=True)
        raise
