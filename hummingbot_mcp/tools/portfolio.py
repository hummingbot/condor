"""
Portfolio management tools for Hummingbot MCP Server

Provides unified portfolio overview by aggregating:
- Token balances from CEX/DEX
- Perpetual positions from CEX
- LP positions (CLMM) from blockchain DEXs
"""
import asyncio
import logging
from typing import Any, Literal

from hummingbot_mcp.exceptions import ToolError
from hummingbot_mcp.hummingbot_client import HummingbotClient
from hummingbot_mcp.formatters import format_portfolio_as_table
from hummingbot_mcp.tools import trading as trading_tools

logger = logging.getLogger("hummingbot-mcp")


async def get_portfolio_overview(
    client: HummingbotClient,
    account_names: list[str] | None = None,
    connector_names: list[str] | None = None,
    include_balances: bool = True,
    include_perp_positions: bool = True,
    include_lp_positions: bool = True,
    include_active_orders: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    """
    Get a unified portfolio overview with real-time data for all active positions.

    Fetches data in parallel:
    1. Token Balances - Real-time holdings across CEX/DEX exchanges
    2. Perpetual Positions - Active perp futures positions from CEX
    3. LP Positions (CLMM) - Real-time concentrated liquidity positions from blockchain DEXs
       - Queries database to find all pools user has interacted with
       - Calls get_positions() for each pool to fetch real-time blockchain data
       - Includes real-time fees and token amounts
    4. Active Orders - Currently open orders across all exchanges

    NOTE: This only shows ACTIVE/OPEN positions. For historical positions and closed positions,
    use the search_history() tool instead.

    Args:
        client: Hummingbot client instance
        account_names: List of account names to filter by (optional)
        connector_names: List of connector names to filter by (optional)
        include_balances: Include token balances (default: True)
        include_perp_positions: Include perpetual positions (default: True)
        include_lp_positions: Include LP (CLMM) positions with real-time data (default: True)
        include_active_orders: Include active (open) orders (default: True)

    Returns:
        Dictionary containing formatted portfolio data with sections for each type
    """
    try:
        # Prepare tasks for parallel execution
        tasks = []
        task_names = []

        # Task 1: Get token balances
        if include_balances:
            async def get_balances():
                try:
                    return await client.portfolio.get_state(
                        account_names=account_names,
                        connector_names=connector_names,
                        refresh=refresh,
                    )
                except Exception as e:
                    logger.warning(f"Failed to get balances: {str(e)}")
                    return None

            tasks.append(get_balances())
            task_names.append("balances")

        # Task 2: Get perpetual positions
        if include_perp_positions:
            async def get_perp_positions():
                try:
                    return await trading_tools.get_positions(
                        client=client,
                        account_names=account_names,
                        connector_names=connector_names,
                        limit=1000,  # Get all positions
                    )
                except Exception as e:
                    logger.warning(f"Failed to get perpetual positions: {str(e)}")
                    return None

            tasks.append(get_perp_positions())
            task_names.append("perp_positions")

        # Task 3: Get LP positions (CLMM) - Real-time from blockchain
        if include_lp_positions:
            async def get_lp_positions():
                try:
                    # Step 1: Get all unique pools from database (to know which pools to query)
                    # This uses the backend database to find pools the user has interacted with
                    search_result = await client.gateway_clmm.search_positions(
                        limit=1000,
                        offset=0,
                        status="OPEN",  # Only get open positions
                    )

                    if not search_result or not isinstance(search_result, dict):
                        return []

                    db_positions = search_result.get("data", [])
                    if not db_positions:
                        return []

                    # Step 2: Get unique pool addresses and their networks/connectors
                    pools_map = {}  # {(connector, network, pool_address): True}
                    for pos in db_positions:
                        connector = pos.get("connector")
                        network = pos.get("network")
                        pool_address = pos.get("pool_address")
                        if connector and network and pool_address:
                            pools_map[(connector, network, pool_address)] = True

                    # Step 3: Fetch real-time data for each pool
                    real_time_positions = []
                    for (connector, network, pool_address) in pools_map.keys():
                        try:
                            positions = await client.gateway_clmm.get_positions_owned(
                                connector=connector,
                                network=network,
                                pool_address=pool_address,
                                wallet_address=None  # Uses default wallet
                            )

                            if positions and isinstance(positions, list):
                                # Add connector and network info to each position
                                for pos in positions:
                                    pos["connector"] = connector
                                    pos["network"] = network
                                real_time_positions.extend(positions)
                        except Exception as e:
                            logger.warning(f"Failed to get positions for pool {pool_address}: {str(e)}")
                            continue

                    return real_time_positions

                except Exception as e:
                    logger.warning(f"Failed to get LP positions: {str(e)}")
                    return None

            tasks.append(get_lp_positions())
            task_names.append("lp_positions")

        # Task 4: Get active orders
        if include_active_orders:
            async def get_active_orders():
                try:
                    return await trading_tools.search_orders(
                        client=client,
                        account_names=account_names,
                        connector_names=connector_names,
                        status="OPEN",  # Only get open orders
                        limit=1000,  # Get all open orders
                    )
                except Exception as e:
                    logger.warning(f"Failed to get active orders: {str(e)}")
                    return None

            tasks.append(get_active_orders())
            task_names.append("active_orders")

        # Execute all tasks in parallel
        results = await asyncio.gather(*tasks, return_exceptions=False)

        # Map results back to their names
        data = {}
        for i, task_name in enumerate(task_names):
            data[task_name] = results[i]

        # Process and format each section
        sections = []
        total_value = 0.0

        # ============================================
        # SECTION 1: Token Balances
        # ============================================
        if include_balances and data.get("balances"):
            balances_data = data["balances"]

            # Calculate total value from balances
            balance_value = 0.0
            if balances_data and isinstance(balances_data, dict):
                for account_name, connectors in balances_data.items():
                    if not isinstance(connectors, dict):
                        continue
                    for connector_name, balances in connectors.items():
                        if not isinstance(balances, list):
                            continue
                        for balance in balances:
                            value = balance.get("value", 0)
                            if value:
                                balance_value += float(value)

            total_value += balance_value

            # Format balances as table
            balances_table = format_portfolio_as_table(balances_data) if balances_data else "No balances found"

            sections.append({
                "title": "Token Balances",
                "content": balances_table,
                "total_value": balance_value,
                "emoji": "💰"
            })
        elif include_balances and not data.get("balances"):
            sections.append({
                "title": "Token Balances",
                "content": "Failed to fetch balances",
                "total_value": 0.0,
                "emoji": "⚠️"
            })

        # ============================================
        # SECTION 2: Perpetual Positions
        # ============================================
        if include_perp_positions and data.get("perp_positions"):
            perp_data = data["perp_positions"]

            if perp_data and isinstance(perp_data, dict):
                perp_table = perp_data.get("positions_table", "No positions found")
                total_positions = perp_data.get("total_positions", 0)

                # Calculate total PnL if available
                # Note: You'll need to parse the table or enhance trading_tools.get_positions
                # to return structured data with PnL values

                sections.append({
                    "title": "Perpetual Positions",
                    "content": perp_table,
                    "total_positions": total_positions,
                    "emoji": "📊"
                })
            else:
                sections.append({
                    "title": "Perpetual Positions",
                    "content": "No perpetual positions found",
                    "total_positions": 0,
                    "emoji": "📊"
                })
        elif include_perp_positions and not data.get("perp_positions"):
            sections.append({
                "title": "Perpetual Positions",
                "content": "Failed to fetch perpetual positions",
                "total_positions": 0,
                "emoji": "⚠️"
            })

        # ============================================
        # SECTION 3: LP Positions (CLMM) - Real-time data
        # ============================================
        if include_lp_positions and data.get("lp_positions") is not None:
            lp_positions = data["lp_positions"]

            if lp_positions and isinstance(lp_positions, list):
                total_lp_positions = len(lp_positions)

                # All positions from get_positions() are OPEN by default
                # (it only returns active positions from the blockchain)
                open_positions = lp_positions

                # Format LP positions - show all open positions with real-time data
                if open_positions:
                    lp_table_lines = ["Status: OPEN positions", ""]
                    lp_table_lines.append("connector | trading_pair | lower_price | upper_price | position_address")
                    lp_table_lines.append("-" * 100)

                    for pos in open_positions[:10]:  # Show up to 10 open positions
                        connector = pos.get("connector", "N/A")
                        trading_pair = pos.get("trading_pair", "N/A")
                        lower_price = pos.get("lower_price", "N/A")
                        upper_price = pos.get("upper_price", "N/A")
                        position_address = pos.get("position_address", "N/A")

                        # Format prices
                        if lower_price != "N/A" and isinstance(lower_price, (int, float, str)):
                            try:
                                lower_price = f"{float(lower_price):.4f}"
                            except:
                                pass

                        if upper_price != "N/A" and isinstance(upper_price, (int, float, str)):
                            try:
                                upper_price = f"{float(upper_price):.4f}"
                            except:
                                pass

                        # Truncate position address
                        if position_address != "N/A" and len(position_address) > 20:
                            position_address = f"{position_address[:8]}...{position_address[-6:]}"

                        lp_table_lines.append(
                            f"{connector[:10]:10} | {trading_pair[:15]:15} | {str(lower_price)[:11]:11} | {str(upper_price)[:11]:11} | {position_address}"
                        )

                    if len(open_positions) > 10:
                        lp_table_lines.append(f"... and {len(open_positions) - 10} more open positions")

                    lp_table = "\n".join(lp_table_lines)
                else:
                    lp_table = "No active LP positions found"

                sections.append({
                    "title": "LP Positions (CLMM)",
                    "content": lp_table,
                    "total_positions": total_lp_positions,
                    "open_positions": len(open_positions),
                    "emoji": "🏊"
                })
            else:
                sections.append({
                    "title": "LP Positions (CLMM)",
                    "content": "No LP positions found",
                    "total_positions": 0,
                    "emoji": "🏊"
                })
        elif include_lp_positions and not data.get("lp_positions"):
            sections.append({
                "title": "LP Positions (CLMM)",
                "content": "Failed to fetch LP positions",
                "total_positions": 0,
                "emoji": "⚠️"
            })

        # ============================================
        # SECTION 4: Active Orders
        # ============================================
        if include_active_orders and data.get("active_orders"):
            orders_data = data["active_orders"]

            if orders_data and isinstance(orders_data, dict):
                orders_table = orders_data.get("orders_table", "No active orders found")
                total_orders = orders_data.get("total_returned", 0)

                sections.append({
                    "title": "Active Orders",
                    "content": orders_table,
                    "total_orders": total_orders,
                    "emoji": "📋"
                })
            else:
                sections.append({
                    "title": "Active Orders",
                    "content": "No active orders found",
                    "total_orders": 0,
                    "emoji": "📋"
                })
        elif include_active_orders and not data.get("active_orders"):
            sections.append({
                "title": "Active Orders",
                "content": "Failed to fetch active orders",
                "total_orders": 0,
                "emoji": "⚠️"
            })

        # ============================================
        # Build final formatted output
        # ============================================
        output_lines = ["Portfolio Overview", "=" * 80, ""]

        for section in sections:
            output_lines.append(f"{section['emoji']} {section['title']}:")
            output_lines.append("-" * 80)
            output_lines.append(section["content"])
            output_lines.append("")

        # Summary section
        output_lines.append("📈 Summary:")
        output_lines.append("-" * 80)

        if include_balances:
            balance_section = next((s for s in sections if s["title"] == "Token Balances"), None)
            if balance_section and "total_value" in balance_section:
                output_lines.append(f"Total Balance Value: ${balance_section['total_value']:.2f}")

        if include_perp_positions:
            perp_section = next((s for s in sections if s["title"] == "Perpetual Positions"), None)
            if perp_section and "total_positions" in perp_section:
                output_lines.append(f"Active Perpetual Positions: {perp_section['total_positions']}")

        if include_lp_positions:
            lp_section = next((s for s in sections if s["title"] == "LP Positions (CLMM)"), None)
            if lp_section and "open_positions" in lp_section:
                open_count = lp_section.get("open_positions", 0)
                output_lines.append(f"Active LP Positions: {open_count}")

        if include_active_orders:
            orders_section = next((s for s in sections if s["title"] == "Active Orders"), None)
            if orders_section and "total_orders" in orders_section:
                output_lines.append(f"Active Orders: {orders_section['total_orders']}")

        formatted_output = "\n".join(output_lines)

        return {
            "formatted_output": formatted_output,
            "sections": sections,
            "total_balance_value": total_value,
            "filters": {
                "account_names": account_names,
                "connector_names": connector_names,
                "include_balances": include_balances,
                "include_perp_positions": include_perp_positions,
                "include_lp_positions": include_lp_positions,
                "include_active_orders": include_active_orders,
            }
        }

    except Exception as e:
        logger.error(f"Error in get_portfolio_overview: {str(e)}", exc_info=True)
        raise ToolError(f"Failed to get portfolio overview: {str(e)}")
