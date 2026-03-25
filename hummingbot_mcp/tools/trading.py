"""
Trading operations business logic.

This module provides the core business logic for trading operations including
managing positions and setting account configurations.

For order placement and cancellation, use `manage_executors` with `order_executor` type.
"""
from typing import Any, Literal

from hummingbot_mcp.formatters import format_orders_as_table, format_positions_as_table


async def set_position_mode_and_leverage(
    client: Any,
    account_name: str,
    connector_name: str,
    trading_pair: str | None = None,
    position_mode: str | None = None,
    leverage: int | None = None,
) -> dict[str, Any]:
    """
    Set position mode and leverage for an account.

    Args:
        client: Hummingbot API client
        account_name: Account name
        connector_name: Exchange connector name
        trading_pair: Trading pair (required for leverage)
        position_mode: Position mode ('HEDGE' or 'ONE-WAY')
        leverage: Leverage to set

    Returns:
        Dictionary containing results

    Raises:
        ValueError: If parameters are invalid
    """
    if position_mode is None and leverage is None:
        raise ValueError("At least one of position_mode or leverage must be specified")

    results = {}

    # Set position mode
    if position_mode:
        position_mode = position_mode.upper()
        if position_mode not in ["HEDGE", "ONE-WAY"]:
            raise ValueError("Invalid position mode. Must be 'HEDGE' or 'ONE-WAY'")

        position_mode_result = await client.trading.set_position_mode(
            account_name=account_name, connector_name=connector_name, position_mode=position_mode
        )
        results["position_mode"] = position_mode_result

    # Set leverage
    if leverage is not None:
        if not isinstance(leverage, int) or leverage <= 0:
            raise ValueError("Leverage must be a positive integer")
        if trading_pair is None:
            raise ValueError("Trading_pair must be specified when setting leverage")

        leverage_result = await client.trading.set_leverage(
            account_name=account_name,
            connector_name=connector_name,
            trading_pair=trading_pair,
            leverage=leverage,
        )
        results["leverage"] = leverage_result

    return results


async def search_orders(
    client: Any,
    account_names: list[str] | None = None,
    connector_names: list[str] | None = None,
    trading_pairs: list[str] | None = None,
    status: Literal["OPEN", "FILLED", "CANCELED", "FAILED"] | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
    limit: int | None = 500,
    cursor: str | None = None,
) -> dict[str, Any]:
    """
    Search orders with filters.

    Args:
        client: Hummingbot API client
        account_names: List of account names to filter by
        connector_names: List of connector names to filter by
        trading_pairs: List of trading pairs to filter by
        status: Order status to filter by
        start_time: Start time (in seconds) to filter by
        end_time: End time (in seconds) to filter by
        limit: Number of orders to return (max 1000)
        cursor: Cursor for pagination

    Returns:
        Dictionary containing orders data, pagination info, and formatted table
    """
    result = await client.trading.search_orders(
        account_names=account_names,
        connector_names=connector_names,
        trading_pairs=trading_pairs,
        status=status,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        cursor=cursor,
    )

    # Format orders as table for better readability
    orders = result.get("data", [])
    orders_table = format_orders_as_table(orders)

    pagination = result.get("pagination", {})

    return {
        "orders": orders,
        "orders_table": orders_table,
        "pagination": pagination,
        "total_returned": len(orders),
        "status_filter": status,
    }


async def get_positions(
    client: Any,
    account_names: list[str] | None = None,
    connector_names: list[str] | None = None,
    limit: int | None = 100,
) -> dict[str, Any]:
    """
    Get positions managed by connected accounts.

    Args:
        client: Hummingbot API client
        account_names: List of account names to filter by
        connector_names: List of connector names to filter by
        limit: Number of positions to return (max 1000)

    Returns:
        Dictionary containing positions data and formatted table
    """
    result = await client.trading.get_positions(
        account_names=account_names, connector_names=connector_names, limit=limit
    )

    # Format positions as table for better readability
    positions = result.get("data", [])
    positions_table = format_positions_as_table(positions)

    return {
        "positions": positions,
        "positions_table": positions_table,
        "total_positions": len(positions),
    }
