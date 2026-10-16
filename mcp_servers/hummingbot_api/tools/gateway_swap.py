"""
Gateway Trading tools for Hummingbot MCP Server

Handles DEX trading operations via Hummingbot Gateway:
- Swap quote/execute (Router: Jupiter, 0x)
- Swap search and status tracking
- Resolving a swap that has been SUBMITTED and unverifiable (FEAT-140)
"""

import logging
from decimal import Decimal
from typing import Any

from condor.swap_watch import SwapResolveUnsupported
from condor.swap_watch import resolve as resolve_stuck_swap
from condor.swap_watch import watch
from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.schemas import GatewaySwapRequest

logger = logging.getLogger("hummingbot-mcp")


def _rows(result: Any) -> list[dict[str, Any]]:
    """The swap rows out of a search payload, whatever it is wrapped in."""
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
    if isinstance(result, list):
        return [row for row in result if isinstance(row, dict)]
    return []


async def manage_gateway_swaps(
    client: Any, request: GatewaySwapRequest
) -> dict[str, Any]:
    """
    The shared implementation behind the registered swap tools.

    ``server.py`` registers ``quote_swap``, ``execute_swap``, ``get_swap_status`` and
    ``search_swaps`` (FEAT-064), plus ``resolve_swap`` (FEAT-140); each builds a request
    with its own ``action`` and lands on the matching branch below. There is no
    ``manage_gateway_swaps`` tool on the wire any more — a swap that spends is reachable
    only under a name the danger gate knows is dangerous.

    Actions:
    - quote: Get price quote for a swap before executing
    - execute: Execute a swap transaction on DEX, priced at execution
    - execute_quote: Execute a quote already taken, by its quote_id (routers only)
    - search: Search swap history with various filters
    - get_status: Get status of a specific swap by transaction hash
    - resolve: Ask the server to resolve a swap stuck SUBMITTED (FEAT-140)

    ``search`` and ``get_status`` also report how long anything still ``SUBMITTED``
    has been *unverifiable*, and alert past the threshold — a swap leaves SUBMITTED
    only on a successful status read, so a Gateway that cannot be reached keeps one
    there indefinitely (GitHub #278). Unverifiable is not failed: the swap may have
    landed. ``resolve`` is the exit, and it is the server's decision, not Condor's.

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

        # How long it has been unverifiable, when it is still SUBMITTED
        # (FEAT-140). The status read is the one place a caller is already
        # asking "did this land", so the duration belongs in its answer rather
        # than in a second call the caller has to know to make.
        row = dict(result) if isinstance(result, dict) else {}
        row.setdefault("transaction_hash", request.transaction_hash)
        book = watch()
        book.observe([row])
        payload: dict[str, Any] = {
            "action": "get_status",
            "transaction_hash": request.transaction_hash,
            "result": result,
        }
        condition = book.condition(row)
        if condition is not None:
            payload["condition"] = condition
            payload["alerts"] = book.alerts([row])
        return payload

    # ============================================
    # SEARCH - Search swap history
    # ============================================
    elif request.action == "search":
        # Build search filters
        search_params = {"limit": request.limit or 50, "offset": request.offset or 0}

        # Add optional filters
        if request.network:
            search_params["network"] = request.network
        if request.connector:
            search_params["connector"] = request.connector
        if request.wallet_address:
            search_params["wallet_address"] = request.wallet_address
        if request.trading_pair:
            search_params["trading_pair"] = request.trading_pair
        if request.status:
            search_params["status"] = request.status
        if request.start_time:
            search_params["start_time"] = request.start_time
        if request.end_time:
            search_params["end_time"] = request.end_time

        result = await client.gateway_swap.search_swaps(**search_params)

        # Every search is also an observation: the rows it returns start (or
        # extend) the unverifiable clock for anything still SUBMITTED, and a
        # swap past the threshold is alerted on here rather than waiting for
        # somebody to ask about that hash specifically (FEAT-140).
        book = watch()
        rows = _rows(result)
        book.observe(rows)
        alerts = book.alerts(rows)

        payload: dict[str, Any] = {
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
        if alerts:
            payload["alerts"] = alerts
        return payload

    # ============================================
    # RESOLVE - Ask the server to resolve a stuck swap
    # ============================================
    elif request.action == "resolve":
        if not request.transaction_hash:
            raise ToolError("transaction_hash is required for resolve action")

        try:
            resolved = await resolve_stuck_swap(
                client,
                request.transaction_hash,
                network=request.network,
            )
        except SwapResolveUnsupported as exc:
            # A capability answer, not an outage and not a failure of the swap.
            # Condor records nothing: the only thing that can resolve a swap is
            # the server that polls the chain (FEAT-140).
            return {
                "action": "resolve",
                "transaction_hash": request.transaction_hash,
                "supported": False,
                "detail": (
                    f"this Hummingbot API server does not offer a resolve route "
                    f"for swaps ({exc}). Nothing was changed. The swap is still "
                    f"SUBMITTED and unverifiable; upgrade the API server, or read "
                    f"the explorer and act on what the chain shows."
                ),
            }

        return {
            "action": "resolve",
            "transaction_hash": request.transaction_hash,
            "supported": True,
            "result": resolved["result"],
        }

    else:
        raise ToolError(f"Unknown action: {request.action}")
