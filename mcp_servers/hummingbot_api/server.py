"""
Main MCP server for Hummingbot API integration
"""

import asyncio
import logging
import os
import sys
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from mcp_servers.hummingbot_api.formatters import (
    format_active_bots_as_table,
    format_amm_result,
    format_bot_logs_as_table,
    format_clmm_result,
    format_gateway_clmm_pool_result,
    format_gateway_config_result,
    format_gateway_container_result,
    format_gateway_swap_result,
    format_portfolio_as_table,
)
from mcp_servers.hummingbot_api.hummingbot_client import hummingbot_client
from mcp_servers.hummingbot_api.middleware import GATEWAY_LOG_HINT, handle_errors
from mcp_servers.hummingbot_api.schemas import (
    AMMRequest,
    CLMMRequest,
    GatewayCLMMRequest,
    GatewayConfigRequest,
    GatewayContainerRequest,
    GatewaySwapRequest,
    ManageExecutorsRequest,
)
from mcp_servers.hummingbot_api.settings import settings
from mcp_servers.hummingbot_api.tools import bot_management as bot_management_tools
from mcp_servers.hummingbot_api.tools import controllers as controllers_tools
from mcp_servers.hummingbot_api.tools import history as history_tools
from mcp_servers.hummingbot_api.tools import market_data as market_data_tools
from mcp_servers.hummingbot_api.tools import portfolio as portfolio_tools
from mcp_servers.hummingbot_api.tools import trading as trading_tools
from mcp_servers.hummingbot_api.tools.executors import (
    manage_executors as manage_executors_impl,
)
from mcp_servers.hummingbot_api.tools.gateway import (
    manage_gateway_config as manage_gateway_config_impl,
)
from mcp_servers.hummingbot_api.tools.gateway import (
    manage_gateway_container as manage_gateway_container_impl,
)
from mcp_servers.hummingbot_api.tools.gateway_amm import manage_amm_impl
from mcp_servers.hummingbot_api.tools.gateway_clmm import (
    explore_gateway_clmm_pools as explore_gateway_clmm_pools_impl,
)
from mcp_servers.hummingbot_api.tools.gateway_clmm import (
    manage_clmm_impl,
)
from mcp_servers.hummingbot_api.tools.gateway_swap import (
    manage_gateway_swaps as manage_gateway_swaps_impl,
)
from mcp_servers.hummingbot_api.tools.geckoterminal import (
    explore_geckoterminal as explore_geckoterminal_impl,
)

# Configure root logger
logging.basicConfig(
    level="INFO",
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("hummingbot-mcp")

# Initialize FastMCP server
mcp = FastMCP("hummingbot-mcp")


# Server Management Tools
#
# Connecting/removing exchange API keys is intentionally NOT exposed here.
# Keys are managed exclusively through the Condor web dashboard (Settings → Keys).


@mcp.tool()
@handle_errors("configure server")
async def configure_server(
    name: str | None = None,
    host: str | None = None,
    port: int | None = None,
    username: str | None = None,
    password: str | None = None,
) -> str:
    """Configure the active Hummingbot API server connection.

    This tool manages a single API server connection:
    1. No parameters → Show the current server configuration
    2. Any parameters → Update the server config and reconnect

    Only the provided parameters are changed; omitted ones keep their current values.

    Args:
        name: Server label (e.g., 'macmini', 'production')
        host: API host (e.g., 'localhost', 'host.docker.internal', '72.212.424.42')
        port: API port (e.g., 8000)
        username: API username
        password: API password
    """
    from mcp_servers.hummingbot_api.settings import ServerConfig, save_server_config

    # No params → show active server (use in-memory settings which include CLI overrides)
    if (
        name is None
        and host is None
        and port is None
        and username is None
        and password is None
    ):
        return (
            f"Active Server:\n\n"
            f"  Name: {settings.server_name}\n"
            f"  URL: {settings.api_url}\n"
            f"  Username: {settings.api_username}\n"
        )

    # Build new config with partial updates (use in-memory settings, not disk)
    from urllib.parse import urlparse

    parsed = urlparse(settings.api_url)
    current_host = parsed.hostname or "localhost"
    current_port = parsed.port or 8000

    final_name = name if name is not None else settings.server_name
    final_host = host if host is not None else current_host
    final_port = port if port is not None else current_port
    final_username = username if username is not None else settings.api_username
    final_password = password if password is not None else settings.api_password

    new_config = ServerConfig(
        name=final_name,
        url=f"http://{final_host}:{final_port}",
        username=final_username,
        password=final_password,
    )

    # Persist and apply
    save_server_config(new_config)
    settings.reload_from_server_config(new_config)
    await hummingbot_client.close()

    try:
        await hummingbot_client.initialize(force=True)
        return (
            f"Server '{new_config.name}' configured and connected successfully.\n\n"
            f"  URL: {new_config.url}\n"
            f"  Username: {new_config.username}\n"
        )
    except Exception as e:
        return (
            f"Server '{new_config.name}' configured but could not connect.\n\n"
            f"  URL: {new_config.url}\n"
            f"  Username: {new_config.username}\n\n"
            f"Error: {str(e)}\n"
        )


@mcp.tool()
@handle_errors("get portfolio overview")
async def get_portfolio_overview(
    account_names: list[str] | None = None,
    connector_names: list[str] | None = None,
    include_balances: bool = True,
    include_perp_positions: bool = True,
    include_lp_positions: bool = True,
    include_active_orders: bool = True,
    as_distribution: bool = False,
    refresh: bool = True,
) -> str:
    """Get a unified portfolio overview with balances, perpetual positions, LP positions, and active orders.

    This tool provides a comprehensive view of your entire portfolio by fetching data from multiple sources
    in parallel. By default, it returns all four types of data, but you can filter to only include
    specific sections.

    Data Sources (fetched in parallel using asyncio.gather):
    1. Token Balances - Holdings across all connected CEX/DEX exchanges
    2. Perpetual Positions - Open perpetual futures positions from CEX
    3. LP Positions (CLMM) - Real-time concentrated liquidity positions from blockchain DEXs
       - Queries database to find all pools user has interacted with
       - Calls get_positions() for each pool to fetch real-time blockchain data
       - Includes real-time fees and token amounts
    4. Active Orders - Currently open orders across all exchanges

    NOTE: This only shows ACTIVE/OPEN positions. For historical data, use search_history() instead.

    Args:
        account_names: List of account names to filter by (optional). If empty, returns all accounts.
        connector_names: List of connector names to filter by (optional). If empty, returns all connectors.
        include_balances: Include token balances in the overview (default: True)
        include_perp_positions: Include perpetual positions in the overview (default: True)
        include_lp_positions: Include LP (CLMM) positions in the overview (default: True)
        include_active_orders: Include active (open) orders in the overview (default: True)
        as_distribution: Show token balances as distribution percentages (default: False)
        refresh: If True, refresh balances from exchanges before returning. If False, return cached state (default: True)
    """
    client = await hummingbot_client.get_client()

    # Handle distribution mode separately
    if as_distribution:
        result = await client.portfolio.get_distribution(
            account_names=account_names, connector_names=connector_names
        )
        return f"Portfolio Distribution:\n{result}"

    # Normal portfolio overview
    result = await portfolio_tools.get_portfolio_overview(
        client=client,
        account_names=account_names,
        connector_names=connector_names,
        include_balances=include_balances,
        include_perp_positions=include_perp_positions,
        include_lp_positions=include_lp_positions,
        include_active_orders=include_active_orders,
        refresh=refresh,
    )

    return result["formatted_output"]


# Trading Tools


@mcp.tool()
@handle_errors("set position mode and leverage")
async def set_account_position_mode_and_leverage(
    account_name: str,
    connector_name: str,
    trading_pair: str | None = None,
    position_mode: str | None = None,
    leverage: int | None = None,
) -> str:
    """Set position mode and leverage for an account on a specific exchange. If position mode is not specified, will only
    set the leverage. If leverage is not specified, will only set the position mode.

    Args:
        account_name: Account name (default: master_account)
        connector_name: Exchange connector name (e.g., 'binance_perpetual')
        trading_pair: Trading pair (e.g., ETH-USD) only required for setting leverage
        position_mode: Position mode ('HEDGE' or 'ONEWAY')
        leverage: Leverage to set (optional, required for HEDGE mode)
    """
    client = await hummingbot_client.get_client()
    results = await trading_tools.set_position_mode_and_leverage(
        client=client,
        account_name=account_name,
        connector_name=connector_name,
        trading_pair=trading_pair,
        position_mode=position_mode,
        leverage=leverage,
    )

    response = ""
    if "position_mode" in results:
        response += f"Position Mode Set: {results['position_mode']}\n"
    if "leverage" in results:
        response += f"Leverage Set: {results['leverage']}\n"

    return response.strip()


@mcp.tool()
@handle_errors("search history")
async def search_history(
    data_type: Literal["orders", "perp_positions", "clmm_positions"],
    account_names: list[str] | None = None,
    connector_names: list[str] | None = None,
    trading_pairs: list[str] | None = None,
    status: str | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
    limit: int = 50,
    offset: int = 0,
    network: str | None = None,
    wallet_address: str | None = None,
    position_addresses: list[str] | None = None,
) -> str:
    """Search historical data from the backend database.

    This tool is for historical analysis, reporting, and tax purposes.
    For real-time current state, use get_portfolio_overview() instead.

    Data Types:
    - orders: Historical order data (filled, cancelled, failed)
    - perp_positions: Perpetual positions (both open and closed)
    - clmm_positions: CLMM LP positions (both open and closed)

    Common Filters (apply to all data types):
        account_names: Filter by account names (optional)
        connector_names: Filter by connector names (optional)
        trading_pairs: Filter by trading pairs (optional)
        status: Filter by status (optional, e.g., 'OPEN', 'CLOSED', 'FILLED', 'CANCELED')
        start_time: Start timestamp in seconds (optional)
        end_time: End timestamp in seconds (optional)
        limit: Maximum number of results (default: 50, max: 1000)
        offset: Pagination offset (default: 0)

    CLMM-Specific Filters:
        network: Network filter for CLMM positions (optional)
        wallet_address: Wallet address filter for CLMM positions (optional)
        position_addresses: Specific position addresses for CLMM (optional)

    Examples:
    - Search filled orders: search_history("orders", status="FILLED", limit=100)
    - Search closed perp positions: search_history("perp_positions", status="CLOSED")
    - Search all CLMM positions: search_history("clmm_positions", limit=100)
    """
    client = await hummingbot_client.get_client()

    result = await history_tools.search_history(
        client=client,
        data_type=data_type,
        account_names=account_names,
        connector_names=connector_names,
        trading_pairs=trading_pairs,
        status=status,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        offset=offset,
        network=network,
        wallet_address=wallet_address,
        position_addresses=position_addresses,
    )

    return result.get("formatted_output", str(result))


# Market Data Tools


@mcp.tool()
@handle_errors("get market data")
async def get_market_data(
    data_type: Literal["prices", "candles", "funding_rate", "order_book"],
    connector_name: str,
    trading_pairs: list[str] | None = None,
    trading_pair: str | None = None,
    interval: str = "1h",
    days: int = 30,
    query_type: (
        Literal[
            "snapshot",
            "volume_for_price",
            "price_for_volume",
            "quote_volume_for_price",
            "price_for_quote_volume",
        ]
        | None
    ) = None,
    query_value: float | None = None,
    is_buy: bool = True,
) -> str:
    """Get market data: prices, candles, funding rates, or order book data.

    Data Types:
    - prices: Get latest prices for multiple trading pairs
    - candles: Get OHLCV candle data for a trading pair
    - funding_rate: Get perpetual funding rate (connector must have _perpetual)
    - order_book: Get order book snapshot or queries

    Args:
        data_type: Type of market data to retrieve ('prices', 'candles', 'funding_rate', 'order_book')
        connector_name: Exchange connector name (e.g., 'binance', 'binance_perpetual')
        trading_pairs: List of trading pairs (required for 'prices', e.g., ['BTC-USDT', 'ETH-USD'])
        trading_pair: Single trading pair (required for 'candles', 'funding_rate', 'order_book')
        interval: Candle interval for 'candles' (default: '1h'). Options: '1m', '5m', '15m', '30m', '1h', '4h', '1d'.
        days: Number of days of historical data for 'candles' (default: 30).
        query_type: Order book query type for 'order_book' (default: 'snapshot'). Options: 'snapshot',
            'volume_for_price', 'price_for_volume', 'quote_volume_for_price', 'price_for_quote_volume'.
        query_value: Value for order book queries (required if query_type is not 'snapshot').
        is_buy: Side for order book queries (default: True for buy side).
    """
    client = await hummingbot_client.get_client()

    if data_type == "prices":
        if not trading_pairs:
            return "Error: 'trading_pairs' is required for data_type='prices'"
        result = await market_data_tools.get_prices(
            client=client,
            connector_name=connector_name,
            trading_pairs=trading_pairs,
        )
        return (
            f"Latest Prices for {result['connector_name']}:\n"
            f"Timestamp: {result['timestamp']}\n\n"
            f"{result['prices_table']}"
        )

    elif data_type == "candles":
        if not trading_pair:
            return "Error: 'trading_pair' is required for data_type='candles'"
        result = await market_data_tools.get_candles(
            client=client,
            connector_name=connector_name,
            trading_pair=trading_pair,
            interval=interval,
            days=days,
        )
        return (
            f"Candles for {result['trading_pair']} on {result['connector_name']}:\n"
            f"Interval: {result['interval']}\n"
            f"Total Candles: {result['total_candles']}\n\n"
            f"{result['candles_table']}"
        )

    elif data_type == "funding_rate":
        if not trading_pair:
            return "Error: 'trading_pair' is required for data_type='funding_rate'"
        result = await market_data_tools.get_funding_rate(
            client=client,
            connector_name=connector_name,
            trading_pair=trading_pair,
        )
        return (
            f"Funding Rate for {result['trading_pair']} on {result['connector_name']}:\n\n"
            f"Funding Rate: {result['funding_rate_pct']:.4f}%\n"
            f"Mark Price: ${result['mark_price']:.2f}\n"
            f"Index Price: ${result['index_price']:.2f}\n"
            f"Next Funding Time: {result['next_funding_time']}"
        )

    elif data_type == "order_book":
        if not trading_pair:
            return "Error: 'trading_pair' is required for data_type='order_book'"
        result = await market_data_tools.get_order_book(
            client=client,
            connector_name=connector_name,
            trading_pair=trading_pair,
            query_type=query_type or "snapshot",
            query_value=query_value,
            is_buy=is_buy,
        )
        if result["query_type"] == "snapshot":
            return (
                f"Order Book Snapshot for {result['trading_pair']} on {result['connector_name']}:\n"
                f"Timestamp: {result['timestamp']}\n"
                f"Top 10 Levels:\n\n"
                f"{result['order_book_table']}"
            )
        else:
            return (
                f"Order Book Query for {result['trading_pair']} on {result['connector_name']}:\n\n"
                f"Query Type: {result['query_type']}\n"
                f"Query Value: {result['query_value']}\n"
                f"Side: {result['side']}\n"
                f"Result: {result['result']}"
            )

    else:
        return f"Error: Invalid data_type '{data_type}'. Use 'prices', 'candles', 'funding_rate', or 'order_book'"


@mcp.tool()
@handle_errors("manage controllers")
async def manage_controllers(
    action: Literal["list", "describe", "upsert", "delete"],
    target: Literal["controller", "config"] | None = None,
    controller_type: (
        Literal["directional_trading", "market_making", "generic"] | None
    ) = None,
    controller_name: str | None = None,
    controller_code: str | None = None,
    config_name: str | None = None,
    config_data: dict[str, Any] | None = None,
    confirm_override: bool = False,
    include_code: bool = False,
) -> str:
    """
    Manage controller templates and saved configurations (design-time).

    Works with reusable strategy definitions and parameter sets for future deployments.
    Does NOT affect running bots. To modify a live bot's config, use manage_bots with action='update_config'.

    ⚠️ NOTE: For most trading strategies (grid, DCA, position trading), use manage_executors() instead.
    Only use controllers when the user EXPLICITLY asks for "controllers", "bots", or needs advanced
    multi-strategy bot deployments with centralized risk management.

    Exploration flow:
    1. action="list" → List all controllers and their configs
    2. action="list" + controller_type → List controllers of that type with config counts
    3. action="describe" + controller_name → Show config parameters template + list existing configs
    4. action="describe" + config_name → Show specific config values + its controller's parameters
    5. action="describe" + include_code=True → Also include the full controller source code

    Modification flow:
    6. action="upsert" + target="controller" → Create/update a controller template
    7. action="upsert" + target="config" → Create/update a saved controller config
    8. action="delete" + target="controller" → Delete a controller template
    9. action="delete" + target="config" → Delete a controller config

    Common Enum Values for Controller Configs:

    Position Mode (position_mode):
    - "HEDGE" - Allows holding both long and short positions simultaneously
    - "ONEWAY" - Allows only one direction position at a time

    Trade Side (side):
    - 1 or "BUY" - For long/buy positions
    - 2 or "SELL" - For short/sell positions
    - Note: Numeric values are required for controller configs

    Order Type (order_type, open_order_type, take_profit_order_type, etc.):
    - 1 or "MARKET" - Market order
    - 2 or "LIMIT" - Limit order
    - 3 or "LIMIT_MAKER" - Limit maker order (post-only)
    - Note: Numeric values are required for controller configs

    Args:
        action: "list", "describe", "upsert" (create/update), or "delete"
        target: "controller" (template) or "config" (instance). Required for upsert/delete.
        controller_type: Type of controller (e.g., 'directional_trading', 'market_making', 'generic').
        controller_name: Name of the controller to describe or modify.
        controller_code: Code for controller (required for controller upsert).
        config_name: Name of the config to describe or modify.
        config_data: Configuration data (required for config upsert). Must include 'controller_type' and 'controller_name'.
        confirm_override: Required True if overwriting existing items.
        include_code: If True, include full controller source code in describe output. Default False.
    """
    client = await hummingbot_client.get_client()
    result = await controllers_tools.manage_controllers(
        client=client,
        action=action,
        target=target,
        controller_type=controller_type,
        controller_name=controller_name,
        controller_code=controller_code,
        config_name=config_name,
        config_data=config_data,
        confirm_override=confirm_override,
        include_code=include_code,
    )
    # list/describe return formatted_output, upsert/delete return message
    return result.get("formatted_output") or result.get("message", str(result))


@mcp.tool()
@handle_errors("manage bots")
async def manage_bots(
    action: Literal[
        "deploy",
        "status",
        "logs",
        "stop_bot",
        "stop_controllers",
        "start_controllers",
        "get_config",
        "update_config",
    ],
    bot_name: str | None = None,
    controllers_config: list[str] | None = None,
    account_name: str | None = "master_account",
    max_global_drawdown_quote: float | None = None,
    max_controller_drawdown_quote: float | None = None,
    image: str = "hummingbot/hummingbot:latest",
    log_type: Literal["error", "general", "all"] = "all",
    limit: int = 50,
    search_term: str | None = None,
    controller_names: list[str] | None = None,
    config_name: str | None = None,
    config_data: dict[str, Any] | None = None,
    confirm_override: bool = False,
) -> str:
    """Manage controller-based bots: deploy, monitor, get logs, control execution, and modify runtime configs.

    ⚠️ NOTE: For most trading strategies (grid, DCA, position trading), use manage_executors() instead.
    Only use bots when the user EXPLICITLY asks for "bot" deployment or needs advanced features like
    multi-strategy bots with centralized risk management.

    Actions:
    - deploy: Deploy a new bot with controller configurations (requires bot_name + controllers_config)
    - status: Get status of all active bots (no additional params needed)
    - logs: Get detailed logs for a specific bot (requires bot_name)
    - stop_bot: Stop and archive a bot forever (requires bot_name)
    - stop_controllers: Stop specific controllers in a bot (requires bot_name + controller_names).
      Flips manual_kill_switch in the controller config and verifies the write; the bot picks it
      up on its next config reload (~10s) and then closes that controller's executors. A stopped
      controller keeps publishing performance, so confirm with the 'state' column of
      action="status" (stopped/running), never by the controller still appearing in the table.
    - start_controllers: Start/resume specific controllers (requires bot_name + controller_names)
    - get_config: View current configs of a running bot (requires bot_name)
    - update_config: Modify config of a controller INSIDE a running bot in real-time (requires bot_name + config_name + config_data)

    Args:
        action: Action to perform on bots.
        bot_name: Name of the bot (required for deploy, logs, stop_bot, stop/start_controllers, get_config, update_config).
        controllers_config: List of controller config names (required for deploy).
        account_name: Account name for deployment (default: master_account).
        max_global_drawdown_quote: Maximum global drawdown in quote currency (deploy only).
        max_controller_drawdown_quote: Maximum per-controller drawdown in quote currency (deploy only).
        image: Docker image for deployment (default: "hummingbot/hummingbot:latest").
        log_type: Type of logs to retrieve for 'logs' action ('error', 'general', 'all').
        limit: Maximum log entries for 'logs' action (default: 50, max: 1000).
        search_term: Search term to filter logs by message content (logs only).
        controller_names: List of controller names (required for stop/start_controllers).
        config_name: Name of the config to update (required for update_config).
        config_data: New configuration data (required for update_config). Must include 'controller_type' and 'controller_name'.
        confirm_override: Required True if overwriting existing config in a running bot (update_config only).
    """
    client = await hummingbot_client.get_client()

    if action == "deploy":
        if not bot_name:
            return "Error: 'bot_name' is required for deploy action"
        if not controllers_config:
            return "Error: 'controllers_config' is required for deploy action"
        result = await controllers_tools.deploy_bot(
            client=client,
            bot_name=bot_name,
            controllers_config=controllers_config,
            account_name=account_name,
            max_global_drawdown_quote=max_global_drawdown_quote,
            max_controller_drawdown_quote=max_controller_drawdown_quote,
            image=image,
        )
        return result["message"]

    elif action == "status":
        result = await bot_management_tools.get_active_bots_status(client)
        return (
            f"Active Bots Status Summary:\n"
            f"Total Active Bots: {result['total_bots']}\n\n"
            f"{result['bots_table']}\n\n"
            f"controller state: running = active | stopped = kill switch on (no new entries) | "
            f"error = performance report failed | unknown = controller config unreadable"
        )

    elif action == "logs":
        if not bot_name:
            return "Error: 'bot_name' is required for logs action"
        result = await bot_management_tools.get_bot_logs(
            client=client,
            bot_name=bot_name,
            log_type=log_type,
            limit=limit,
            search_term=search_term,
        )
        if "error" in result:
            return result["message"]
        return (
            f"Bot Logs for: {result['bot_name']}\n"
            f"Log Type: {result['log_type']}\n"
            f"Search Term: {result['search_term'] if result['search_term'] else 'None'}\n"
            f"Total Logs Returned: {result['total_logs']}\n\n"
            f"{result['logs_table']}"
        )

    elif action in ("stop_bot", "stop_controllers", "start_controllers"):
        if not bot_name:
            return f"Error: 'bot_name' is required for {action} action"
        result = await bot_management_tools.manage_bot_execution(
            client=client,
            bot_name=bot_name,
            action=action,
            controller_names=controller_names,
        )
        return result["message"]

    elif action == "get_config":
        if not bot_name:
            return "Error: 'bot_name' is required for get_config action"
        result = await bot_management_tools.get_bot_controller_configs(
            client=client, bot_name=bot_name
        )
        return result["formatted_output"]

    elif action == "update_config":
        if not bot_name:
            return "Error: 'bot_name' is required for update_config action"
        if not config_name or not config_data:
            return "Error: 'config_name' and 'config_data' are required for update_config action"
        result = await bot_management_tools.update_bot_controller_config(
            client=client,
            bot_name=bot_name,
            config_name=config_name,
            config_data=config_data,
            confirm_override=confirm_override,
        )
        return result["message"]

    else:
        return f"Error: Invalid action '{action}'"


# Executor Management Tools


@mcp.tool()
@handle_errors("manage executors")
async def manage_executors(
    action: (
        Literal[
            "create",
            "search",
            "stop",
            "get_logs",
            "get_preferences",
            "save_preferences",
            "reset_preferences",
            "positions_summary",
            "clear_position",
            "performance_report",
            "orphaned",
            "resolve_orphan",
        ]
        | None
    ) = None,
    executor_type: str | None = None,
    executor_config: dict[str, Any] | None = None,
    executor_id: str | None = None,
    log_level: str | None = None,
    account_names: list[str] | None = None,
    connector_names: list[str] | None = None,
    trading_pairs: list[str] | None = None,
    executor_types: list[str] | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    keep_position: bool = False,
    save_as_default: bool = False,
    preferences_content: str | None = None,
    account_name: str | None = None,
    connector_name: str | None = None,
    trading_pair: str | None = None,
    controller_id: str | None = None,
    controller_ids: list[str] | None = None,
) -> str:
    """Manage trading executors: create, search, stop, and configure preferences.

    This is the DEFAULT tool for ALL trading operations. Use progressive disclosure to get
    the full guide and config schema for any executor type before creating.

    Executor Types (pass executor_type with no action to see full guide + schema):
    - order_executor: Buy/sell orders (MARKET, LIMIT, LIMIT_MAKER, LIMIT_CHASER)
    - position_executor: Directional positions with SL/TP management
    - grid_executor: Grid trading for range-bound markets
    - dca_executor: Dollar-cost averaging with scheduled levels
    - lp_executor: CLMM LP positions on Meteora/Raydium (use explore_dex_pools first)

    Actions:
    - (none) + executor_type → Show full guide, config schema, and saved defaults
    - create + executor_config → Create executor (merged with saved defaults)
    - search → List/filter executors (add executor_id for detail)
    - stop + executor_id → Stop executor (with keep_position option)
    - get_logs + executor_id → Get logs (active executors only)
    - get_preferences / save_preferences / reset_preferences → Manage saved defaults
    - positions_summary → View all positions (add connector_name + trading_pair to filter)
    - clear_position + connector_name + trading_pair → Clear externally-closed position
    - performance_report → Get executor performance report (optionally filter by controller_id)
    - orphaned → List terminated executors that may still own an on-chain LP position (recover before opening new ones).
      Reports the dex, pool and network needed to close each one with `manage_clmm(action="close")`
    - resolve_orphan + executor_id → Mark an orphaned position as recovered, after closing it with `manage_clmm`.
      Stopping a terminated executor does NOT close its position

    Args:
        action: Action to perform. Leave empty to see executor types or config schema.
        executor_type: Type of executor. Provide alone to see its full guide and config schema.
        executor_config: Configuration for creating an executor. Required for 'create' action.
        executor_id: Executor ID for 'search' (detail), 'stop', or 'get_logs' actions.
        log_level: Filter logs by level - 'ERROR', 'WARNING', 'INFO', 'DEBUG' (for get_logs).
        account_names: Filter by account names (for search).
        connector_names: Filter by connector names (for search).
        trading_pairs: Filter by trading pairs (for search).
        executor_types: Filter by executor types (for search).
        status: Filter by status - 'RUNNING', 'TERMINATED' (for search).
        cursor: Pagination cursor for search results.
        limit: Maximum results to return (default: 50, max: 1000).
        keep_position: When stopping, keep the position open instead of closing it (default: False).
        save_as_default: Save executor_config as default for this executor_type (default: False).
        preferences_content: Complete markdown content for the preferences file. Required for 'save_preferences'.
        account_name: Account name for creating executors (default: 'master_account').
        connector_name: Connector name for position filtering or clearing.
        trading_pair: Trading pair for position filtering or clearing.
        controller_id: Controller ID that owns the executor. Used for create, positions_summary, clear_position, and performance_report.
        controller_ids: Filter by controller IDs (for search).
    """
    # Create and validate request using Pydantic model
    request = ManageExecutorsRequest(
        action=action,
        executor_type=executor_type,
        executor_config=executor_config,
        executor_id=executor_id,
        log_level=log_level,
        account_names=account_names,
        connector_names=connector_names,
        trading_pairs=trading_pairs,
        executor_types=executor_types,
        status=status,
        cursor=cursor,
        limit=limit,
        keep_position=keep_position,
        save_as_default=save_as_default,
        preferences_content=preferences_content,
        account_name=account_name,
        connector_name=connector_name,
        trading_pair=trading_pair,
        controller_id=controller_id,
        controller_ids=controller_ids,
    )

    client = await hummingbot_client.get_client()
    result = await manage_executors_impl(client, request)

    return result.get("formatted_output", str(result))


@mcp.tool()
@handle_errors("explore DEX pools", GATEWAY_LOG_HINT)
async def explore_dex_pools(
    action: Literal["list_pools", "get_pool_info"],
    connector: str | None = None,
    network: str | None = None,
    pool_address: str | None = None,
    page: int = 0,
    limit: int = 50,
    search_term: str | None = None,
    sort_key: str | None = "tvl",
    order_by: str | None = "desc",
    include_unknown: bool = True,
    detailed: bool = False,
) -> str:
    """Explore DeFi CLMM pools — discover pools, compare yields, and get pool details.

    Supports CLMM DEX connectors (Meteora, Raydium, Uniswap V3) for concentrated liquidity.

    - list_pools: Browse available CLMM pools with filtering and sorting
    - get_pool_info: Get detailed information about a specific pool (requires network + pool_address)

    To manage LP positions, use `manage_executors` with `lp_executor` type.
    To check on-chain positions, use `get_portfolio_overview` with `include_lp_positions=True`.

    Args:
        action: Action to perform on CLMM pools.
        connector: CLMM connector name (e.g., 'meteora', 'raydium', 'orca', 'uniswap', 'pancakeswap'). Required.
        network: Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta'). Required for get_pool_info.
        pool_address: Pool contract address (required for get_pool_info).
        page: Page number for list_pools (default: 0).
        limit: Results per page for list_pools (default: 50, max: 100).
        search_term: Search term to filter pools by token symbols (e.g., 'SOL', 'USDC').
        sort_key: Sort by field for list_pools. Defaults to 'tvl', which is the LP
            question — how much depth is there. 'volume' ranks by how much OTHERS traded,
            and on a quiet pair every pool ties at zero, making the order arbitrary: on
            UMBRA-USDC that put a pool holding $1.07 above one holding $15.34K, and buried
            the deep one at row 68 of 73. meteora: tvl, volume, feetvlratio.
            orca: tvl, volume, fees, rewards, yieldovertvl. Anything else is rejected with
            the legal list rather than failing upstream.
        order_by: Sort order for list_pools ('asc' or 'desc').
        include_unknown: Include pools with unverified tokens (default: True).
        detailed: Return detailed table with more columns for list_pools (default: False).
    """
    request = GatewayCLMMRequest(
        action=action,
        connector=connector,
        network=network,
        pool_address=pool_address,
        page=page,
        limit=limit,
        search_term=search_term,
        sort_key=sort_key,
        order_by=order_by,
        include_unknown=include_unknown,
        detailed=detailed,
    )

    client = await hummingbot_client.get_client()
    result = await explore_gateway_clmm_pools_impl(client, request)
    return format_gateway_clmm_pool_result(action, result)


@mcp.tool()
@handle_errors("manage AMM", GATEWAY_LOG_HINT)
async def manage_amm(
    action: (
        Literal[
            "pool_info",
            "position_info",
            "positions_owned",
            "quote_liquidity",
            "add_liquidity",
            "remove_liquidity",
            "create_pool",
        ]
        | None
    ) = None,
    connector: str | None = None,
    network: str | None = None,
    wallet_address: str | None = None,
    pool_address: str | None = None,
    position_address: str | None = None,
    base_token: str | None = None,
    quote_token: str | None = None,
    slippage_pct: str | None = None,
    base_token_amount: str | None = None,
    quote_token_amount: str | None = None,
    percentage_to_remove: str | None = None,
    initial_price: str | None = None,
    extra_params: dict[str, Any] | None = None,
) -> str:
    """Direct AMM pool operations + pool creation, chain- & DEX-agnostic (Meteora / Raydium / Uniswap).

    Stateless — you hold position state in your journal. Progressive disclosure: call with NO
    `action` to load the AMM guide, action list, per-connector param matrix, and network list.

    Actions:
    - pool_info / position_info → read pool reserves/price/fee; read your position (aggregate + positions[])
    - positions_owned → list ALL your positions across pools (meteora only)
    - quote_liquidity / add_liquidity / remove_liquidity → two-sided LP in/out
    - create_pool → create + seed a new pool (market-price seeded by default; anti-snipe)

    Connectors: meteora (Solana DAMM v2), raydium (Solana CPMM), uniswap (EVM V2).

    Meteora DAMM v2 positions are NFTs, so this tool is position-addressed: remove_liquidity REQUIRES
    position_address, add_liquidity takes it optionally (omit = open a new position), position_info
    returns a positions[] breakdown, positions_owned lists all your positions. Fungible-LP AMMs
    (raydium, uniswap) ignore position_address and have no enumerable positions.

    create_pool extras ride extra_params under Gateway's own names: meteora→configAddress
    (required); raydium→ammConfigIndex (optional); uniswap/pancakeswap (EVM)→none
    (0.30% fixed fee). Unknown keys are rejected with a 400.

    Scope: AMM only. Router/one-shot swaps → manage_executors(order_executor); CLMM LP →
    manage_executors(lp_executor).

    Args:
        action: AMM action. Leave empty to load the AMM guide + param matrix.
        connector: AMM connector (required for any action): meteora | raydium | uniswap.
        network: Network ID in 'chain-network' format (e.g., 'solana-mainnet-beta', 'ethereum-mainnet').
        wallet_address: Wallet address (optional, uses default if not provided).
        pool_address: Pool contract address.
        position_address: Meteora NFT position — required for remove_liquidity, optional for add_liquidity (omit = new position).
        base_token: Base token symbol or address (create_pool).
        quote_token: Quote token symbol or address (pool quote, for create_pool).
        slippage_pct: Maximum slippage percentage (string).
        base_token_amount: Base token amount (add_liquidity / quote_liquidity / create_pool).
        quote_token_amount: Quote token amount (add_liquidity / quote_liquidity / create_pool).
        percentage_to_remove: Percentage of liquidity to remove, 0-100 (remove_liquidity).
        initial_price: Initial price as quote per base (create_pool; overrides quote_token_amount).
        extra_params: Connector-specific create_pool params under Gateway's own names:
            configAddress (meteora, required), ammConfigIndex (raydium). Unknown keys
            are rejected with a 400.
    """
    request = AMMRequest(
        action=action,
        connector=connector,
        network=network,
        wallet_address=wallet_address,
        pool_address=pool_address,
        position_address=position_address,
        base_token=base_token,
        quote_token=quote_token,
        slippage_pct=slippage_pct,
        base_token_amount=base_token_amount,
        quote_token_amount=quote_token_amount,
        percentage_to_remove=percentage_to_remove,
        initial_price=initial_price,
        extra_params=extra_params,
    )

    client = await hummingbot_client.get_client()
    result = await manage_amm_impl(client, request)
    return format_amm_result(action, result)


@mcp.tool()
@handle_errors("manage CLMM", GATEWAY_LOG_HINT)
async def manage_clmm(
    action: (
        Literal[
            "position_info",
            "open",
            "add_liquidity",
            "remove_liquidity",
            "close",
            "collect_fees",
            "create_pool",
        ]
        | None
    ) = None,
    connector: str | None = None,
    network: str | None = None,
    wallet_address: str | None = None,
    pool_address: str | None = None,
    position_address: str | None = None,
    lower_price: str | None = None,
    upper_price: str | None = None,
    base_token_amount: str | None = None,
    quote_token_amount: str | None = None,
    percentage_to_remove: str | None = None,
    slippage_pct: str | None = None,
    base_token: str | None = None,
    quote_token: str | None = None,
    initial_price: str | None = None,
    extra_params: dict | None = None,
) -> str:
    """Direct CLMM position operations, chain- & DEX-agnostic (Meteora / Raydium / Orca / Uniswap / PancakeSwap).

    Stateless — you hold position state in your journal. Progressive disclosure: call with NO
    `action` to load the CLMM guide.

    THE UNMANAGED PATH. For normal LP work use `manage_executors` with `lp_executor`, which owns
    range monitoring, rebalancing and bounded close retries. Use this tool when no executor can do
    that for you — above all to RECOVER AN ORPHANED POSITION: a terminated executor cannot be told
    to close anything (`manage_executors(action="stop")` on one is correctly a no-op), so the
    position must be closed by address here, then marked with
    `manage_executors(action="resolve_orphan")`.

    Actions:
    - position_info → your positions in a pool with amounts, range, and uncollected fees
    - open → create a position NO executor tracks (not range-monitored or auto-closed)
    - add_liquidity / remove_liquidity → resize an existing position, keeping its range
    - close → withdraw everything, collect fees, close the account
    - collect_fees → fees only, position untouched
    - create_pool → create a new (empty) CLMM pool; liquidity is added by opening positions

    remove_liquidity at 100% leaves an EMPTY POSITION OPEN; only `close` closes the account. To
    recover an orphan, use `close`.

    Positions opened by an lp_executor are not in the API database (the bot opens them straight
    against Gateway), and they close fine anyway: the API never reads `pool_address` on
    close/collect_fees.

    Pool discovery lives in `explore_dex_pools`.

    Args:
        action: CLMM action. Leave empty to load the CLMM guide.
        connector: CLMM connector: meteora | raydium | orca | pancakeswap-sol (Solana),
            uniswap | pancakeswap (EVM). A '<name>/clmm' form is accepted, so an orphan's
            lp_provider passes through unchanged.
        network: Network ID in 'chain-network' format (e.g. 'solana-mainnet-beta', 'ethereum-mainnet').
            For an orphan record this is the `connector_name` field.
        wallet_address: Wallet address (optional, uses default if not provided).
        pool_address: Pool contract address. Required for open; informational elsewhere —
            position_info takes no pool filter and close/collect_fees never read it.
        position_address: Position NFT address (add_liquidity, remove_liquidity, close, collect_fees).
        lower_price: Lower price bound of the range (open).
        upper_price: Upper price bound of the range (open).
        base_token_amount: Base token amount (open / add_liquidity).
        quote_token_amount: Quote token amount (open / add_liquidity).
        percentage_to_remove: Percentage of liquidity to remove, 0-100 (remove_liquidity).
        slippage_pct: Maximum slippage percentage.
        base_token: Base token symbol or address (create_pool).
        quote_token: Quote token symbol or address (create_pool).
        initial_price: Initial pool price as quote per base (create_pool, optional —
            the API fetches the market price when omitted).
        extra_params: Connector-specific params under Gateway's own names.
            open/add_liquidity: strategyType (meteora DLMM, e.g. {"strategyType": 0}).
            create_pool: binStep (meteora/orca), feeBps (meteora/uniswap/pancakeswap),
            ammConfigIndex (raydium/pancakeswap-sol). Unknown keys are rejected with a 400.
    """
    request = CLMMRequest(
        action=action,
        connector=connector,
        network=network,
        wallet_address=wallet_address,
        pool_address=pool_address,
        position_address=position_address,
        lower_price=lower_price,
        upper_price=upper_price,
        base_token_amount=base_token_amount,
        quote_token_amount=quote_token_amount,
        percentage_to_remove=percentage_to_remove,
        slippage_pct=slippage_pct,
        base_token=base_token,
        quote_token=quote_token,
        initial_price=initial_price,
        extra_params=extra_params,
    )

    client = await hummingbot_client.get_client()
    result = await manage_clmm_impl(client, request)
    return format_clmm_result(action, result)


@mcp.tool()
@handle_errors("manage Gateway swaps", GATEWAY_LOG_HINT)
async def manage_gateway_swaps(
    action: Literal["quote", "execute", "search", "get_status"],
    connector: str | None = None,
    network: str | None = None,
    trading_pair: str | None = None,
    side: Literal["BUY", "SELL"] | None = None,
    amount: str | None = None,
    slippage_pct: str | None = None,
    wallet_address: str | None = None,
    transaction_hash: str | None = None,
    extra_params: dict[str, Any] | None = None,
    search_connector: str | None = None,
    search_network: str | None = None,
    search_wallet_address: str | None = None,
    search_trading_pair: str | None = None,
    status: Literal["SUBMITTED", "CONFIRMED", "FAILED"] | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """One-shot DEX swaps through Gateway's unified swap route — quote, execute, and track.

    This is the ONLY swap surface. Router aggregators (jupiter, 0x) and pool-scoped AMM/CLMM
    swaps all go through it; the pool-scoped `/trading/amm/*-swap` routes no longer exist.

    Actions:
    - quote → price, expected output, and price impact BEFORE committing (free, always do this first)
    - execute → submit the swap on-chain; returns a transaction hash
    - get_status → resolve a submitted swap by transaction_hash
    - search → query swap history with filters

    CONNECTOR FORMAT. `connector` is "name/type":
    - "jupiter/router", "0x/router" — aggregator routing across every pool it knows
    - "meteora/amm", "raydium/amm", "uniswap/amm" — swap against an AMM pool
    - "meteora/clmm", "raydium/clmm", "uniswap/clmm" — swap against a CLMM pool

    POOL RESOLUTION (read this before trusting a failure). For the pool-scoped forms
    (`/amm`, `/clmm`) Gateway resolves the pool itself from ITS OWN CONFIGURED POOL LIST,
    matched BY TOKEN SYMBOL from `trading_pair` — you cannot pass a pool address here. A token
    Gateway does not know (a fresh launch mint, a pool created moments ago via
    `manage_amm(create_pool)`) will NOT resolve, and creating a pool does not register it. Such a
    call fails with "No pool found", which means UNKNOWN, not "bad token" — do not read it as a
    honeypot or a failed safety check. Route those through an aggregator ("jupiter/router"),
    which quotes off live on-chain routes rather than a config file.

    LP work is elsewhere: `manage_amm` (AMM pools), `manage_clmm` (CLMM positions),
    `manage_executors(lp_executor)` (managed LP).

    Args:
        action: Swap action to perform.
        connector: Connector in "name/type" form (required for quote/execute), e.g.
            'jupiter/router', 'meteora/amm', 'raydium/clmm'.
        network: Network ID in 'chain-network' format (required for quote/execute), e.g.
            'solana-mainnet-beta', 'ethereum-mainnet'.
        trading_pair: Trading pair as 'BASE-QUOTE' (required for quote/execute). Symbols or
            token addresses. Pool-scoped connectors match Gateway's pool list by SYMBOL.
        side: 'BUY' (buy base with quote) or 'SELL' (sell base for quote). Required for quote/execute.
        amount: Base token amount to buy or sell (required for quote/execute).
        slippage_pct: Maximum slippage percentage. OMIT to use the connector's configured
            slippage; '0' is a real value, not "use the default".
        wallet_address: Wallet for execute (optional, uses the default wallet).
        transaction_hash: Transaction hash (required for get_status).
        extra_params: Connector-specific params under Gateway's own names. Supported:
            'approximateIfNoExactOut' (bool, jupiter/dflow/okx/titan routers).
        search_connector: Filter history by connector (search).
        search_network: Filter history by network (search).
        search_wallet_address: Filter history by wallet address (search).
        search_trading_pair: Filter history by trading pair (search).
        status: Filter history by status: SUBMITTED | CONFIRMED | FAILED (search).
        start_time: Start timestamp in unix seconds (search).
        end_time: End timestamp in unix seconds (search).
        limit: Max results for search (default 50, max 1000).
        offset: Pagination offset for search (default 0).
    """
    request = GatewaySwapRequest(
        action=action,
        connector=connector,
        network=network,
        trading_pair=trading_pair,
        side=side,
        amount=amount,
        slippage_pct=slippage_pct,
        wallet_address=wallet_address,
        transaction_hash=transaction_hash,
        extra_params=extra_params,
        search_connector=search_connector,
        search_network=search_network,
        search_wallet_address=search_wallet_address,
        search_trading_pair=search_trading_pair,
        status=status,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        offset=offset,
    )

    client = await hummingbot_client.get_client()
    result = await manage_gateway_swaps_impl(client, request)
    return format_gateway_swap_result(action, result)


# GeckoTerminal Tools


@mcp.tool()
@handle_errors("explore GeckoTerminal")
async def explore_geckoterminal(
    action: Literal[
        "networks",
        "dexes",
        "trending_pools",
        "top_pools",
        "new_pools",
        "pool_detail",
        "multi_pools",
        "token_pools",
        "token_info",
        "ohlcv",
        "trades",
    ],
    network: str | None = None,
    dex_id: str | None = None,
    pool_address: str | None = None,
    pool_addresses: list[str] | None = None,
    token_address: str | None = None,
    timeframe: str = "1h",
    before_timestamp: int | None = None,
    currency: str = "usd",
    token: str = "base",
    limit: int = 1000,
    trade_volume_filter: float | None = None,
) -> str:
    """Explore DEX market data from GeckoTerminal (free, no API key needed).

    Progressive discovery flow:
    1. action="networks" → List all supported networks (solana, eth, bsc, ...)
    2. action="dexes" + network → List DEXes on a network
    3. action="trending_pools" (+ network) → Trending pools globally or per network
    4. action="top_pools" + network (+ dex_id) → Top pools by volume on a network/dex
    5. action="new_pools" (+ network) → Recently created pools
    6. action="pool_detail" + network + pool_address → Detailed info for one pool
    7. action="multi_pools" + network + pool_addresses → Compare multiple pools
    8. action="token_pools" + network + token_address → Top pools for a token
    9. action="token_info" + network + token_address → Token details (price, mcap, fdv)
    10. action="ohlcv" + network + pool_address → OHLCV candle data
    11. action="trades" + network + pool_address → Recent trades

    Args:
        action: The data to retrieve.
        network: Network ID (e.g., 'solana', 'eth', 'bsc'). Required for most actions.
        dex_id: DEX ID filter for top_pools (e.g., 'raydium', 'uniswap_v3').
        pool_address: Pool contract address (for pool_detail, ohlcv, trades).
        pool_addresses: List of pool addresses (for multi_pools).
        token_address: Token contract address (for token_pools, token_info).
        timeframe: OHLCV interval (default: '1h'). Options: 1m, 5m, 15m, 1h, 4h, 12h, 1d.
        before_timestamp: Fetch OHLCV candles before this unix timestamp (pagination).
        currency: OHLCV price currency, 'usd' or 'token' (default: 'usd').
        token: Which token's price for OHLCV, 'base' or 'quote' (default: 'base').
        limit: Max OHLCV candles to return (default: 1000).
        trade_volume_filter: Min trade volume in USD to filter trades (optional).
    """
    result = await explore_geckoterminal_impl(
        action=action,
        network=network,
        dex_id=dex_id,
        pool_address=pool_address,
        pool_addresses=pool_addresses,
        token_address=token_address,
        timeframe=timeframe,
        before_timestamp=before_timestamp,
        currency=currency,
        token=token,
        limit=limit,
        trade_volume_filter=trade_volume_filter,
    )
    return result.get("formatted_output", str(result))


def _apply_cli_args():
    """Override settings from the spawn: credentials via env, the rest via CLI.

    The API username/password used to arrive as ``--username``/``--password``,
    which put them in every local user's ``ps`` output (SEC-095). They now come
    in on the environment, and they are applied HERE rather than in
    ``_load_server_config`` on purpose: that loader prefers the cached
    ``~/.hummingbot_mcp/server.yml`` and never reaches its own env fallbacks
    once the file exists, so credentials injected for this spawn have to sit at
    the same precedence layer the CLI args did — above the file.
    """
    import argparse

    if username := os.getenv("HUMMINGBOT_API_USERNAME"):
        settings.api_username = username
    if password := os.getenv("HUMMINGBOT_API_PASSWORD"):
        settings.api_password = password

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--url")
    parser.add_argument("--server-name")
    args, _ = parser.parse_known_args()

    if args.url:
        settings.api_url = args.url
    if args.server_name:
        settings.server_name = args.server_name


async def _run():
    """Run the MCP server"""
    _apply_cli_args()

    # Setup logging once at application start
    logger.info("Starting Hummingbot MCP Server")
    logger.info(f"Configured API URL: {settings.api_url}")
    logger.info(f"Default Account: {settings.default_account}")
    logger.info("Server will connect to API on first use (lazy initialization)")
    logger.info(
        "💡 Use 'configure_server' tool to view or update the API server connection"
    )

    # Run the server with FastMCP
    # Connection to API will happen lazily on first tool use
    try:
        await mcp.run_stdio_async()
    finally:
        # Clean up client connection if it was initialized
        await hummingbot_client.close()


def main():
    """Entry point for uvx/pip console_scripts."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
