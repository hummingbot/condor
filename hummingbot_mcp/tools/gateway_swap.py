"""
Gateway Trading tools for Hummingbot MCP Server

Handles DEX trading operations via Hummingbot Gateway:
- Swap quote/execute (Router: Jupiter, 0x)
- Swap search and status tracking
"""
import logging
from decimal import Decimal
from typing import Any

from hummingbot_mcp.exceptions import ToolError
from hummingbot_mcp.schemas import GatewaySwapRequest

logger = logging.getLogger("hummingbot-mcp")


async def manage_gateway_swaps(client: Any, request: GatewaySwapRequest) -> dict[str, Any]:
    """
    Manage Gateway swap operations: quote, execute, search, and status tracking.

    Actions:
    - quote: Get price quote for a swap before executing
    - execute: Execute a swap transaction on DEX
    - search: Search swap history with various filters
    - get_status: Get status of a specific swap by transaction hash

    Supported DEX Connectors:
    - jupiter (Solana): Router for Solana swaps
    - 0x (Ethereum): Aggregator for EVM chains
    """
    # ============================================
    # QUOTE - Get swap price quote
    # ============================================
    if request.action == "quote":
        # Validate required parameters
        if not request.connector:
            raise ToolError("connector is required for quote action")
        if not request.network:
            raise ToolError("network is required for quote action")
        if not request.trading_pair:
            raise ToolError("trading_pair is required for quote action")
        if not request.side:
            raise ToolError("side is required for quote action (BUY or SELL)")
        if not request.amount:
            raise ToolError("amount is required for quote action")

        # Parse trading pair
        if "-" not in request.trading_pair:
            raise ToolError(f"Invalid trading_pair format. Expected 'BASE-QUOTE', got '{request.trading_pair}'")

        result = await client.gateway_swap.get_swap_quote(
            connector=request.connector,
            network=request.network,
            trading_pair=request.trading_pair,
            side=request.side,
            amount=Decimal(request.amount),
            slippage_pct=Decimal(request.slippage_pct or "1.0")
        )

        return {
            "action": "quote",
            "trading_pair": request.trading_pair,
            "side": request.side,
            "amount": request.amount,
            "result": result
        }

    # ============================================
    # EXECUTE - Execute swap transaction
    # ============================================
    elif request.action == "execute":
        # Validate required parameters
        if not request.connector:
            raise ToolError("connector is required for execute action")
        if not request.network:
            raise ToolError("network is required for execute action")
        if not request.trading_pair:
            raise ToolError("trading_pair is required for execute action")
        if not request.side:
            raise ToolError("side is required for execute action (BUY or SELL)")
        if not request.amount:
            raise ToolError("amount is required for execute action")

        # Parse trading pair
        if "-" not in request.trading_pair:
            raise ToolError(f"Invalid trading_pair format. Expected 'BASE-QUOTE', got '{request.trading_pair}'")

        result = await client.gateway_swap.execute_swap(
            connector=request.connector,
            network=request.network,
            trading_pair=request.trading_pair,
            side=request.side,
            amount=Decimal(request.amount),
            slippage_pct=Decimal(request.slippage_pct or "1.0"),
            wallet_address=request.wallet_address
        )

        return {
            "action": "execute",
            "trading_pair": request.trading_pair,
            "side": request.side,
            "amount": request.amount,
            "wallet_address": request.wallet_address or "(default)",
            "result": result
        }

    # ============================================
    # GET STATUS - Get swap status by tx hash
    # ============================================
    elif request.action == "get_status":
        if not request.transaction_hash:
            raise ToolError("transaction_hash is required for get_status action")

        result = await client.gateway_swap.get_swap_status(request.transaction_hash)

        return {
            "action": "get_status",
            "transaction_hash": request.transaction_hash,
            "result": result
        }

    # ============================================
    # SEARCH - Search swap history
    # ============================================
    elif request.action == "search":
        # Build search filters
        search_params = {
            "limit": request.limit or 50,
            "offset": request.offset or 0
        }

        # Add optional filters
        if request.search_network:
            search_params["network"] = request.search_network
        if request.search_connector:
            search_params["connector"] = request.search_connector
        if request.search_wallet_address:
            search_params["wallet_address"] = request.search_wallet_address
        if request.search_trading_pair:
            search_params["trading_pair"] = request.search_trading_pair
        if request.status:
            search_params["status"] = request.status
        if request.start_time:
            search_params["start_time"] = request.start_time
        if request.end_time:
            search_params["end_time"] = request.end_time

        result = await client.gateway_swap.search_swaps(**search_params)

        return {
            "action": "search",
            "filters": {k: v for k, v in search_params.items() if k not in ["limit", "offset"]},
            "pagination": {
                "limit": search_params["limit"],
                "offset": search_params["offset"]
            },
            "result": result
        }

    else:
        raise ToolError(f"Unknown action: {request.action}")
