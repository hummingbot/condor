"""
Gateway Trading tools for Hummingbot MCP Server

Handles DEX trading operations via Hummingbot Gateway:
- Swap quote/execute (Router: Jupiter, 0x)
- Swap search and status tracking
"""

import logging
from decimal import Decimal
from typing import Any

from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.schemas import GatewaySwapRequest

logger = logging.getLogger("hummingbot-mcp")


async def manage_gateway_swaps(
    client: Any, request: GatewaySwapRequest
) -> dict[str, Any]:
    """
    Manage Gateway swap operations: quote, execute, search, and status tracking.

    Actions:
    - quote: Get price quote for a swap before executing
    - execute: Execute a swap transaction on DEX, priced at execution
    - execute_quote: Execute a quote already taken, by its quote_id (routers only)
    - search: Search swap history with various filters
    - get_status: Get status of a specific swap by transaction hash

    Supported DEX Connectors:
    - jupiter (Solana): Router for Solana swaps
    - 0x (Ethereum): Aggregator for EVM chains

    A quote carrying `approximation: true` reports an ESTIMATED amount_out rather than
    the exact-out amount asked for. A BUY is an ExactOut order, and a thin token with no
    ExactOut route is quoted by pricing the sell leg and quoting that input forward,
    which costs roughly 2.5%. Nobody is overcharged — the order is silently resized — so
    say so whenever the quantity is what the user cares about, and pass
    extra_params={'approximateIfNoExactOut': False} to require an exact route instead.
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
            raise ToolError(
                f"Invalid trading_pair format. Expected 'BASE-QUOTE', got '{request.trading_pair}'"
            )

        result = await client.gateway_swap.get_swap_quote(
            connector=request.connector,
            network=request.network,
            trading_pair=request.trading_pair,
            side=request.side,
            amount=Decimal(request.amount),
            # None -> SDK omits it and the connector's configured slippage applies
            slippage_pct=(
                Decimal(request.slippage_pct)
                if request.slippage_pct is not None
                else None
            ),
            extra_params=request.extra_params,
        )

        return {
            "action": "quote",
            "trading_pair": request.trading_pair,
            "side": request.side,
            "amount": request.amount,
            "result": result,
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
            raise ToolError(
                f"Invalid trading_pair format. Expected 'BASE-QUOTE', got '{request.trading_pair}'"
            )

        result = await client.gateway_swap.execute_swap(
            connector=request.connector,
            network=request.network,
            trading_pair=request.trading_pair,
            side=request.side,
            amount=Decimal(request.amount),
            # None -> SDK omits it and the connector's configured slippage applies
            slippage_pct=(
                Decimal(request.slippage_pct)
                if request.slippage_pct is not None
                else None
            ),
            wallet_address=request.wallet_address,
            extra_params=request.extra_params,
        )

        return {
            "action": "execute",
            "trading_pair": request.trading_pair,
            "side": request.side,
            "amount": request.amount,
            "wallet_address": request.wallet_address or "(default)",
            "result": result,
        }

    # ============================================
    # EXECUTE_QUOTE - Commit to a quote already taken
    # ============================================
    elif request.action == "execute_quote":
        if not request.connector:
            raise ToolError("connector is required for execute_quote action")
        if not request.network:
            raise ToolError("network is required for execute_quote action")
        if not request.quote_id:
            raise ToolError(
                "quote_id is required for execute_quote action: take a quote first "
                "(action='quote') and pass the quote_id it returns. Only router connectors "
                "return one."
            )
        if not request.trading_pair or not request.side or not request.amount:
            raise ToolError(
                "trading_pair, side and amount are required for execute_quote: Gateway "
                "identifies the swap by quote_id alone, but the recorded trade has to be "
                "filed under the pair and size it was for."
            )

        result = await client.gateway_swap.execute_quote(
            connector=request.connector,
            network=request.network,
            quote_id=request.quote_id,
            trading_pair=request.trading_pair,
            side=request.side,
            amount=Decimal(request.amount),
            wallet_address=request.wallet_address,
        )

        return {
            "action": "execute_quote",
            "trading_pair": request.trading_pair,
            "side": request.side,
            "amount": request.amount,
            "quote_id": request.quote_id,
            "wallet_address": request.wallet_address or "(default)",
            "result": result,
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
            "result": result,
        }

    # ============================================
    # SEARCH - Search swap history
    # ============================================
    elif request.action == "search":
        # Build search filters
        search_params = {"limit": request.limit or 50, "offset": request.offset or 0}

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
            "filters": {
                k: v for k, v in search_params.items() if k not in ["limit", "offset"]
            },
            "pagination": {
                "limit": search_params["limit"],
                "offset": search_params["offset"],
            },
            "result": result,
        }

    else:
        raise ToolError(f"Unknown action: {request.action}")
