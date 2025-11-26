"""
Trading data utilities for fetching positions and orders
"""
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


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


async def get_lp_positions(client, account_names: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Get all LP (CLMM) positions from blockchain DEXs

    This fetches real-time position data from the blockchain by:
    1. Finding all pools the user has interacted with (from database)
    2. Querying each pool's current state from the blockchain

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

        # Step 1: Get all unique pools from database (to know which pools to query)
        # This uses the backend database to find pools the user has interacted with
        try:
            search_result = await client.gateway_clmm.search_positions(
                limit=100,  # Reduced limit for portfolio overview
                offset=0,
                status="OPEN",  # Only get open positions
            )
        except Exception as e:
            logger.debug(f"CLMM search_positions not available: {e}")
            return {"positions": [], "total": 0}

        if not search_result or not isinstance(search_result, dict):
            return {"positions": [], "total": 0}

        db_positions = search_result.get("data", [])
        if not db_positions:
            return {"positions": [], "total": 0}

        # Step 2: Get unique pool addresses and their networks/connectors
        pools_map = {}  # {(connector, network, pool_address): True}
        for pos in db_positions:
            connector = pos.get("connector")
            network = pos.get("network")
            pool_address = pos.get("pool_address")

            # Validate all required fields are present and non-empty
            if not connector or not network or not pool_address:
                continue

            # Skip if pool_address looks invalid (too short)
            if len(pool_address) < 20:
                logger.debug(f"Skipping invalid pool_address: {pool_address}")
                continue

            pools_map[(connector, network, pool_address)] = True

        if not pools_map:
            return {"positions": [], "total": 0}

        logger.info(f"LP Positions: querying {len(pools_map)} pools in parallel")

        # Step 3: Fetch real-time data for all pools in parallel
        import asyncio

        async def fetch_pool_positions(connector: str, network: str, pool_address: str):
            """Fetch positions for a single pool"""
            try:
                # Ensure network has the chain prefix (e.g., "solana-mainnet-beta")
                if network and '-' not in network:
                    chain = "solana" if connector in ("meteora", "raydium", "orca") else "ethereum"
                    network = f"{chain}-{network}"

                positions = await client.gateway_clmm.get_positions_owned(
                    connector=connector,
                    network=network,
                    pool_address=pool_address,
                    wallet_address=None
                )

                if positions and isinstance(positions, list):
                    # Add connector and network info to each position
                    for pos in positions:
                        pos["connector"] = connector
                        pos["network"] = network
                    return positions
                return []
            except Exception as e:
                logger.debug(f"Failed to get LP positions for {connector}/{pool_address[:12]}...: {str(e)}")
                return []

        # Create tasks for all pools
        tasks = [
            fetch_pool_positions(connector, network, pool_address)
            for (connector, network, pool_address) in pools_map.keys()
        ]

        # Execute all in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect all positions
        real_time_positions = []
        for result in results:
            if isinstance(result, list):
                real_time_positions.extend(result)

        return {
            "positions": real_time_positions,
            "total": len(real_time_positions)
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
