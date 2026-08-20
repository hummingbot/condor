"""
Gateway CLMM tools for Hummingbot MCP Server

Two tools live here:
- explore_gateway_clmm_pools: read-only pool discovery (list pools, get pool info)
- manage_clmm: direct position operations (open, add, remove, close, collect fees, create pool)

The managed path for normal LP work is `manage_executors` with `lp_executor`, which owns range
monitoring, rebalancing and close retries. manage_clmm is the direct path, and the only way to
recover an orphaned position: once an executor has terminated it cannot be told to close anything,
so the position has to be closed by address.
"""

import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

from mcp_servers.hummingbot_api.exceptions import ToolError
from mcp_servers.hummingbot_api.formatters.base import format_number, get_field
from mcp_servers.hummingbot_api.schemas import CLMMRequest, GatewayCLMMRequest

logger = logging.getLogger("hummingbot-mcp")

# Gateway routes CLMM calls on the bare connector name (see trading/clmm close/open routes).
SUPPORTED_CLMM_CONNECTORS = {
    "meteora",
    "raydium",
    "orca",
    "uniswap",
    "pancakeswap",
    "pancakeswap-sol",
}


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
    Format pool data as a detailed table string.

    Every column here is a field of hapi's CLMMPoolListItem. The old exploded
    volume / fee_tvl_ratio columns, plus max_fee_percentage and
    protocol_fee_percentage, were dropped: the API never sends them, so they
    rendered as a permanent wall of N/A.

    Columns: address | name | trading_pair | mint_x | mint_y | bin_step | current_price |
             liquidity | base_fee_percentage | apr | apy | volume_24h | fees_24h
    """
    if not pools:
        return "No pools found."

    # Header - detailed columns
    header = (
        "address | name | trading_pair | mint_x | mint_y | bin_step | current_price | "
        "liquidity | base_fee_percentage | apr | apy | volume_24h | fees_24h"
    )
    separator = "-" * 300

    # Format each pool as a row
    rows = []
    for pool in pools:
        row = (
            f"{get_field(pool, 'address', default='N/A')} | "
            f"{get_field(pool, 'name', default='N/A')} | "
            f"{get_field(pool, 'trading_pair', default='N/A')} | "
            f"{get_field(pool, 'mint_x', default='N/A')} | "
            f"{get_field(pool, 'mint_y', default='N/A')} | "
            f"{get_field(pool, 'bin_step', default='N/A')} | "
            f"{format_number(get_field(pool, 'current_price', default=None))} | "
            f"{format_number(get_field(pool, 'liquidity', default=None))} | "
            f"{format_number(get_field(pool, 'base_fee_percentage', default=None))} | "
            f"{format_number(get_field(pool, 'apr', default=None))} | "
            f"{format_number(get_field(pool, 'apy', default=None))} | "
            f"{format_number(get_field(pool, 'volume_24h', default=None))} | "
            f"{format_number(get_field(pool, 'fees_24h', default=None))}"
        )
        rows.append(row)

    return f"{header}\n{separator}\n" + "\n".join(rows)


async def explore_gateway_clmm_pools(
    client: Any, request: GatewayCLMMRequest
) -> dict[str, Any]:
    """
    Explore Gateway CLMM pools: list pools and get pool information.

    Actions:
    - list_pools: Get list of available CLMM pools with filtering and sorting
    - get_pool_info: Get detailed information about a specific pool

    Supported CLMM Connectors:
    - meteora (Solana): DLMM pools
    - raydium (Solana): CLMM pools
    - orca (Solana): Whirlpools
    - uniswap (Ethereum/EVM): V3 pools
    - pancakeswap (BSC/EVM): V3 pools
    - pancakeswap-sol (Solana): CLMM pools
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
            include_unknown=request.include_unknown,
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
                "include_unknown": request.include_unknown,
            },
            "pagination": {
                "page": request.page,
                "limit": request.limit,
                "total": result.get("total", 0),
            },
            "pools_table": formatted_table,
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
            pool_address=request.pool_address,
            bin_count=request.bin_count,
        )

        return {
            "action": "get_pool_info",
            "connector": request.connector,
            "network": request.network,
            "pool_address": request.pool_address,
            "result": result,
        }

    else:
        raise ToolError(f"Unknown action: {request.action}")


def _clmm_guide() -> str:
    guide_file = Path(__file__).parent.parent / "guides" / "gateway_clmm.md"
    if guide_file.exists():
        return guide_file.read_text().strip()
    raise ToolError("CLMM guide not found (guides/gateway_clmm.md)")


def _dec(value: str | None, name: str) -> Decimal:
    if value is None:
        raise ToolError(f"{name} is required for this action")
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ToolError(f"{name} must be a number, got {value!r}") from exc


def _opt_dec(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _require_clmm(request: CLMMRequest, *fields: str) -> None:
    for f in fields:
        if getattr(request, f) is None:
            raise ToolError(f"{f} is required for the '{request.action}' action")


def _normalize_connector(connector: str) -> str:
    """Accept both 'orca' and the 'orca/clmm' form an lp_executor config records as lp_provider."""
    name = connector.lower().strip()
    if name.endswith("/clmm"):
        name = name[: -len("/clmm")]
    if name not in SUPPORTED_CLMM_CONNECTORS:
        raise ToolError(
            f"Unsupported CLMM connector '{connector}'. Supported: {', '.join(sorted(SUPPORTED_CLMM_CONNECTORS))}"
        )
    return name


async def manage_clmm_impl(client: Any, request: CLMMRequest) -> dict[str, Any]:
    """Validate per-action and dispatch to client.gateway_clmm.*."""
    # Progressive disclosure: no action → load the guide.
    if request.action is None:
        return {"action": None, "formatted_output": _clmm_guide()}

    if not request.connector:
        raise ToolError(
            "connector is required (meteora | raydium | orca | pancakeswap-sol | uniswap | "
            "pancakeswap). "
            "Call manage_clmm with no action to load the guide."
        )
    connector = _normalize_connector(request.connector)
    if not request.network:
        raise ToolError(
            "network is required (e.g. 'solana-mainnet-beta', 'ethereum-mainnet')"
        )

    net = request.network
    action = request.action
    # Dereferenced only after per-action validation so guard errors never depend on the client.
    gc = None if client is None else client.gateway_clmm

    if action == "position_info":
        if request.position_address:
            # Asked about one position, so read that one. positions_owned would answer
            # with every position the wallet holds on this connector — not wrong data,
            # but not the question, and it silently grows into a list once a wallet
            # holds more than one.
            result = [await gc.get_position_info(
                connector=connector,
                network=net,
                position_address=request.position_address,
            )]
        else:
            # No pool filter: Gateway's positions-owned lists every CLMM position the
            # wallet owns on the connector, each row carrying its own pool_address.
            result = await gc.get_positions_owned(
                connector=connector,
                network=net,
                wallet_address=request.wallet_address,
            )

    elif action == "open":
        _require_clmm(request, "pool_address", "lower_price", "upper_price")
        if request.base_token_amount is None and request.quote_token_amount is None:
            raise ToolError(
                "open requires base_token_amount, quote_token_amount, or both"
            )
        result = await gc.open_position(
            connector=connector,
            network=net,
            pool_address=request.pool_address,
            lower_price=_dec(request.lower_price, "lower_price"),
            upper_price=_dec(request.upper_price, "upper_price"),
            base_token_amount=_opt_dec(request.base_token_amount),
            quote_token_amount=_opt_dec(request.quote_token_amount),
            slippage_pct=_opt_dec(request.slippage_pct),
            wallet_address=request.wallet_address,
            extra_params=request.extra_params,
        )

    elif action == "add_liquidity":
        _require_clmm(request, "position_address")
        if request.base_token_amount is None and request.quote_token_amount is None:
            raise ToolError(
                "add_liquidity requires base_token_amount, quote_token_amount, or both"
            )
        result = await gc.add_liquidity(
            connector=connector,
            network=net,
            position_address=request.position_address,
            base_token_amount=_opt_dec(request.base_token_amount),
            quote_token_amount=_opt_dec(request.quote_token_amount),
            slippage_pct=_opt_dec(request.slippage_pct),
            wallet_address=request.wallet_address,
            extra_params=request.extra_params,
        )

    elif action == "remove_liquidity":
        _require_clmm(request, "position_address", "percentage_to_remove")
        result = await gc.remove_liquidity(
            connector=connector,
            network=net,
            position_address=request.position_address,
            percentage_to_remove=_dec(
                request.percentage_to_remove, "percentage_to_remove"
            ),
            slippage_pct=_opt_dec(request.slippage_pct),
            wallet_address=request.wallet_address,
        )

    elif action == "close":
        _require_clmm(request, "position_address")
        result = await gc.close_position(
            connector=connector,
            network=net,
            position_address=request.position_address,
            pool_address=request.pool_address,
            wallet_address=request.wallet_address,
        )

    elif action == "collect_fees":
        _require_clmm(request, "position_address")
        result = await gc.collect_fees(
            connector=connector,
            network=net,
            position_address=request.position_address,
            pool_address=request.pool_address,
            wallet_address=request.wallet_address,
        )

    elif action == "create_pool":
        _require_clmm(request, "base_token", "quote_token")
        result = await gc.create_pool(
            connector=connector,
            network=net,
            base_token=request.base_token,
            quote_token=request.quote_token,
            # None -> hapi/gateway fetch the market price
            initial_price=_opt_dec(request.initial_price),
            wallet_address=request.wallet_address,
            extra_params=request.extra_params,
        )

    else:
        raise ToolError(f"Unknown action: {action}")

    return {
        "action": action,
        "connector": connector,
        "network": net,
        "pool_address": request.pool_address,
        "position_address": request.position_address,
        "result": result,
    }
