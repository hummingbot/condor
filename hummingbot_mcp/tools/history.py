"""
Historical data search tools for Hummingbot MCP Server

Provides access to historical data:
- Orders (filled, cancelled, failed)
- Perpetual positions (open and closed)
- CLMM positions (open and closed)
"""
import logging
from typing import Any, Literal

from hummingbot_mcp.hummingbot_client import HummingbotClient
from . import trading as trading_tools
from . import gateway_clmm as gateway_clmm_tools

logger = logging.getLogger("hummingbot-mcp")


async def search_history(
    client: HummingbotClient,
    data_type: Literal["orders", "perp_positions", "clmm_positions"],
    # Common filters
    account_names: list[str] | None = None,
    connector_names: list[str] | None = None,
    trading_pairs: list[str] | None = None,
    status: str | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
    # Pagination
    limit: int = 50,
    offset: int = 0,
    # CLMM-specific filters
    network: str | None = None,
    wallet_address: str | None = None,
    position_addresses: list[str] | None = None,
) -> dict[str, Any]:
    """
    Search historical data from the backend database.

    This tool is for historical analysis, reporting, and tax purposes.
    For real-time current state, use get_portfolio_overview() instead.

    Data Types:
    - orders: Historical order data (filled, cancelled, failed)
    - perp_positions: Perpetual positions (both open and closed)
    - clmm_positions: CLMM LP positions (both open and closed)

    Args:
        client: Hummingbot client instance
        data_type: Type of historical data to search
        account_names: Filter by account names (optional)
        connector_names: Filter by connector names (optional)
        trading_pairs: Filter by trading pairs (optional)
        status: Filter by status (optional, e.g., 'OPEN', 'CLOSED', 'FILLED', 'CANCELED')
        start_time: Start timestamp in seconds (optional)
        end_time: End timestamp in seconds (optional)
        limit: Maximum number of results (default: 50, max: 1000)
        offset: Pagination offset (default: 0)
        network: Network filter for CLMM positions (optional)
        wallet_address: Wallet address filter for CLMM positions (optional)
        position_addresses: Specific position addresses for CLMM (optional)

    Returns:
        Dictionary containing search results with formatted output
    """
    try:
        # ============================================
        # ORDERS - Historical order data
        # ============================================
        if data_type == "orders":
            # Use existing trading_tools.search_orders function
            result = await trading_tools.search_orders(
                client=client,
                account_names=account_names,
                connector_names=connector_names,
                trading_pairs=trading_pairs,
                status=status,
                start_time=start_time,
                end_time=end_time,
                limit=min(limit, 1000),
                cursor=None,  # We use offset instead for pagination
            )

            formatted_output = f"Order History\n{'=' * 100}\n\n{result['orders_table']}"

            if result['pagination'].get('has_more'):
                formatted_output += f"\n\n... and more (use offset={offset + limit} to see more)"

            return {
                "data_type": "orders",
                "total_count": result['total_returned'],
                "results": result['orders'],
                "formatted_output": formatted_output
            }

        # ============================================
        # PERP POSITIONS - Perpetual positions
        # ============================================
        elif data_type == "perp_positions":
            # Use existing trading_tools.get_positions function
            result = await trading_tools.get_positions(
                client=client,
                account_names=account_names,
                connector_names=connector_names,
                limit=min(limit, 1000),
            )

            formatted_output = f"Perpetual Positions History\n{'=' * 100}\n\n{result['positions_table']}"

            return {
                "data_type": "perp_positions",
                "total_count": result['total_positions'],
                "results": result['positions'],
                "formatted_output": formatted_output
            }

        # ============================================
        # CLMM POSITIONS - LP positions
        # ============================================
        elif data_type == "clmm_positions":
            # Build search parameters for CLMM positions
            search_params = {
                "limit": min(limit, 1000),
                "offset": offset,
                "refresh": False,  # Don't refresh from blockchain for historical search
            }

            # Add CLMM-specific filters
            if network:
                search_params["network"] = network
            if wallet_address:
                search_params["wallet_address"] = wallet_address
            if connector_names:
                search_params["connector"] = connector_names[0] if len(connector_names) == 1 else None
            if trading_pairs:
                search_params["trading_pair"] = trading_pairs[0] if len(trading_pairs) == 1 else None
            if status:
                search_params["status"] = status
            if position_addresses:
                search_params["position_addresses"] = position_addresses

            # Search CLMM positions using gateway_clmm tools
            result = await client.gateway_clmm.search_positions(**search_params)

            if not result or not isinstance(result, dict):
                return {
                    "data_type": "clmm_positions",
                    "total_count": 0,
                    "results": [],
                    "formatted_output": "No CLMM positions found"
                }

            positions = result.get("data", [])
            total_count = len(positions)

            # Format CLMM positions as table
            if positions:
                table_lines = ["CLMM LP Positions History", "=" * 150, ""]
                table_lines.append(
                    f"{'Connector':<10} | {'Network':<20} | {'Pair':<15} | {'Lower':<10} | {'Upper':<10} | "
                    f"{'Status':<8} | {'Created':<20} | {'Closed':<20}"
                )
                table_lines.append("-" * 150)

                for pos in positions[:limit]:
                    connector = pos.get("connector", "N/A")[:10]
                    network = pos.get("network", "N/A")[:20]
                    pair = pos.get("trading_pair", "N/A")[:15]
                    lower = f"{float(pos.get('lower_price', 0)):.4f}"[:10]
                    upper = f"{float(pos.get('upper_price', 0)):.4f}"[:10]
                    status_val = pos.get("status", "N/A")[:8]
                    created = pos.get("created_at", "N/A")[:20]
                    closed = pos.get("closed_at", "N/A")[:20] if pos.get("closed_at") else "-"

                    table_lines.append(
                        f"{connector:<10} | {network:<20} | {pair:<15} | {lower:<10} | {upper:<10} | "
                        f"{status_val:<8} | {created:<20} | {closed:<20}"
                    )

                if total_count > limit:
                    table_lines.append(f"\n... and {total_count - limit} more positions (use offset={offset + limit} to see more)")

                formatted_output = "\n".join(table_lines)
            else:
                formatted_output = "No CLMM positions found"

            return {
                "data_type": "clmm_positions",
                "total_count": total_count,
                "results": positions,
                "formatted_output": formatted_output
            }

        else:
            return {
                "data_type": data_type,
                "total_count": 0,
                "results": [],
                "formatted_output": f"Unknown data type: {data_type}"
            }

    except Exception as e:
        logger.error(f"Error in search_history: {str(e)}", exc_info=True)
        raise Exception(f"Failed to search history: {str(e)}")
