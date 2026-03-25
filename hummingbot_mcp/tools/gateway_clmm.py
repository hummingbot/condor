"""
Gateway CLMM tools for Hummingbot MCP Server

Handles DEX CLMM read-only operations via Hummingbot Gateway:
- Pool exploration (list pools, get pool info)
- Position queries (get positions)

For opening/closing LP positions, use `manage_executors` with `lp_executor` type.
"""
import logging
from typing import Any

from hummingbot_mcp.exceptions import ToolError
from hummingbot_mcp.formatters.base import format_number, get_field
from hummingbot_mcp.schemas import GatewayCLMMRequest

logger = logging.getLogger("hummingbot-mcp")


def format_pools_as_table(pools: list[dict[str, Any]]) -> str:
    """
    Format pool data as a simplified table string.

    Columns: address | trading_pair | bin_step | current_price | liquidity | base_fee_percentage | apy | volume_24h | fees_24h
    """
    if not pools:
        return "No pools found."

    # Header - simplified columns
    header = "address | trading_pair | bin_step | current_price | liquidity | base_fee_percentage | apy | volume_24h | fees_24h"
    separator = "-" * 200

    # Format each pool as a row
    rows = []
    for pool in pools:
        row = (
            f"{get_field(pool, 'address', default='N/A')} | "
            f"{get_field(pool, 'trading_pair', default='N/A')} | "
            f"{get_field(pool, 'bin_step', default='N/A')} | "
            f"{format_number(get_field(pool, 'current_price', default=None))} | "
            f"{format_number(get_field(pool, 'liquidity', default=None))} | "
            f"{format_number(get_field(pool, 'base_fee_percentage', default=None))} | "
            f"{format_number(get_field(pool, 'apy', default=None))} | "
            f"{format_number(get_field(pool, 'volume_24h', default=None))} | "
            f"{format_number(get_field(pool, 'fees_24h', default=None))}"
        )
        rows.append(row)

    return f"{header}\n{separator}\n" + "\n".join(rows)


def format_pools_as_detailed_table(pools: list[dict[str, Any]]) -> str:
    """
    Format pool data as a detailed table string with exploded volume and fee_tvl_ratio fields.

    Columns: address | trading_pair | mint_x | mint_y | bin_step | current_price | liquidity |
             base_fee_percentage | max_fee_percentage | protocol_fee_percentage | apr | apy |
             volume_hour_1 | volume_hour_12 | volume_hour_24 |
             fee_tvl_ratio_hour_1 | fee_tvl_ratio_hour_12 | fee_tvl_ratio_hour_24
    """
    if not pools:
        return "No pools found."

    # Header - detailed columns
    header = (
        "address | trading_pair | mint_x | mint_y | bin_step | current_price | liquidity | "
        "base_fee_percentage | max_fee_percentage | protocol_fee_percentage | apr | apy | "
        "volume_hour_1 | volume_hour_12 | volume_hour_24 | "
        "fee_tvl_ratio_hour_1 | fee_tvl_ratio_hour_12 | fee_tvl_ratio_hour_24"
    )
    separator = "-" * 300

    # Format each pool as a row
    rows = []
    for pool in pools:
        # Extract nested volume fields
        volume = pool.get('volume', {})
        volume_hour_1 = volume.get('hour_1', 'N/A')
        volume_hour_12 = volume.get('hour_12', 'N/A')
        volume_hour_24 = volume.get('hour_24', 'N/A')

        # Extract nested fee_tvl_ratio fields
        fee_tvl_ratio = pool.get('fee_tvl_ratio', {})
        fee_tvl_ratio_hour_1 = fee_tvl_ratio.get('hour_1', 'N/A')
        fee_tvl_ratio_hour_12 = fee_tvl_ratio.get('hour_12', 'N/A')
        fee_tvl_ratio_hour_24 = fee_tvl_ratio.get('hour_24', 'N/A')

        row = (
            f"{get_field(pool, 'address', default='N/A')} | "
            f"{get_field(pool, 'trading_pair', default='N/A')} | "
            f"{get_field(pool, 'mint_x', default='N/A')} | "
            f"{get_field(pool, 'mint_y', default='N/A')} | "
            f"{get_field(pool, 'bin_step', default='N/A')} | "
            f"{format_number(get_field(pool, 'current_price', default=None))} | "
            f"{format_number(get_field(pool, 'liquidity', default=None))} | "
            f"{format_number(get_field(pool, 'base_fee_percentage', default=None))} | "
            f"{format_number(get_field(pool, 'max_fee_percentage', default=None))} | "
            f"{format_number(get_field(pool, 'protocol_fee_percentage', default=None))} | "
            f"{format_number(get_field(pool, 'apr', default=None))} | "
            f"{format_number(get_field(pool, 'apy', default=None))} | "
            f"{format_number(volume_hour_1)} | "
            f"{format_number(volume_hour_12)} | "
            f"{format_number(volume_hour_24)} | "
            f"{format_number(fee_tvl_ratio_hour_1)} | "
            f"{format_number(fee_tvl_ratio_hour_12)} | "
            f"{format_number(fee_tvl_ratio_hour_24)}"
        )
        rows.append(row)

    return f"{header}\n{separator}\n" + "\n".join(rows)


async def explore_gateway_clmm_pools(client: Any, request: GatewayCLMMRequest) -> dict[str, Any]:
    """
    Explore Gateway CLMM pools: list pools and get pool information.

    Actions:
    - list_pools: Get list of available CLMM pools with filtering and sorting
    - get_pool_info: Get detailed information about a specific pool

    Supported CLMM Connectors:
    - meteora (Solana): DLMM pools
    - raydium (Solana): CLMM pools
    - uniswap (Ethereum/EVM): V3 pools
    """
    # ============================================
    # LIST POOLS - Browse available pools
    # ============================================
    if request.action == "list_pools":
        result = await client.gateway_clmm.get_pools(
            connector=request.connector,
            page=request.page,
            limit=request.limit,
            search_term=request.search_term,
            sort_key=request.sort_key,
            order_by=request.order_by,
            include_unknown=request.include_unknown
        )

        pools = result.get("pools", [])

        # Format as detailed table if detailed mode is enabled
        if request.detailed:
            formatted_table = format_pools_as_detailed_table(pools)
        else:
            # Otherwise format as simplified table
            formatted_table = format_pools_as_table(pools)

        return {
            "action": "list_pools",
            "connector": request.connector,
            "filters": {
                "search_term": request.search_term,
                "sort_key": request.sort_key,
                "order_by": request.order_by,
                "include_unknown": request.include_unknown
            },
            "pagination": {
                "page": request.page,
                "limit": request.limit,
                "total": result.get("total", 0)
            },
            "pools_table": formatted_table
        }

    # ============================================
    # GET POOL INFO - Get detailed pool information
    # ============================================
    elif request.action == "get_pool_info":
        # Validate required parameters
        if not request.network:
            raise ToolError("network is required for get_pool_info action")
        if not request.pool_address:
            raise ToolError("pool_address is required for get_pool_info action")

        result = await client.gateway_clmm.get_pool_info(
            connector=request.connector,
            network=request.network,
            pool_address=request.pool_address
        )

        return {
            "action": "get_pool_info",
            "connector": request.connector,
            "network": request.network,
            "pool_address": request.pool_address,
            "result": result
        }

    else:
        raise ToolError(f"Unknown action: {request.action}")


