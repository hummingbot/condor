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
)
from mcp_servers.hummingbot_api.settings import settings
from mcp_servers.hummingbot_api.tools import bot_management as bot_management_tools
from mcp_servers.hummingbot_api.tools import controllers as controllers_tools
from mcp_servers.hummingbot_api.tools import executor_create
from mcp_servers.hummingbot_api.tools import executors as executors_tools
from mcp_servers.hummingbot_api.tools import history as history_tools
from mcp_servers.hummingbot_api.tools import market_data as market_data_tools
from mcp_servers.hummingbot_api.tools import portfolio as portfolio_tools
from mcp_servers.hummingbot_api.tools import trading as trading_tools
from mcp_servers.hummingbot_api.tools.gateway import (
    manage_gateway_config as manage_gateway_config_impl,
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
# No secret is a parameter of any tool on this server. Connecting/removing exchange
# API keys and adding/removing Gateway wallets are intentionally NOT exposed here:
# a key typed at an agent is persisted by the chat transport, by the bot's state and
# by every transcript that session writes, and no confirmation gate un-leaks it.
# Both are managed exclusively through the Condor web dashboard (Settings → Keys,
# Settings → Gateway).


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
@handle_errors("get prices")
async def get_prices(connector_name: str, trading_pairs: list[str]) -> str:
    """Get the latest price for one or more trading pairs on a connector.

    Args:
        connector_name: Exchange connector name (e.g., 'binance', 'binance_perpetual')
        trading_pairs: Trading pairs to price (e.g., ['BTC-USDT', 'ETH-USDT'])

    Example:
    - get_prices("binance", ["BTC-USDT", "ETH-USDT"])
    """
    client = await hummingbot_client.get_client()

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


@mcp.tool()
@handle_errors("get candles")
async def get_candles(
    connector_name: str,
    trading_pair: str,
    interval: str = "1h",
    days: int = 30,
) -> str:
    """Get OHLCV candles for a trading pair.

    Args:
        connector_name: Exchange connector name (e.g., 'binance', 'binance_perpetual')
        trading_pair: Trading pair (e.g., 'BTC-USDT')
        interval: Candle interval (default: '1h'). Options: '1m', '5m', '15m', '30m', '1h', '4h', '1d'.
        days: Number of days of historical data (default: 30).

    Example:
    - get_candles("binance", "BTC-USDT", interval="1h", days=7)
    """
    client = await hummingbot_client.get_client()

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


@mcp.tool()
@handle_errors("get funding rate")
async def get_funding_rate(connector_name: str, trading_pair: str) -> str:
    """Get the current perpetual funding rate for a trading pair.

    Args:
        connector_name: Perpetual connector name, must contain '_perpetual'
            (e.g., 'binance_perpetual')
        trading_pair: Trading pair (e.g., 'BTC-USDT')

    Example:
    - get_funding_rate("binance_perpetual", "BTC-USDT")
    """
    client = await hummingbot_client.get_client()

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


@mcp.tool()
@handle_errors("get order book")
async def get_order_book(
    connector_name: str,
    trading_pair: str,
    query_type: Literal[
        "snapshot",
        "volume_for_price",
        "price_for_volume",
        "quote_volume_for_price",
        "price_for_quote_volume",
    ] = "snapshot",
    query_value: float | None = None,
    is_buy: bool = True,
) -> str:
    """Get an order book snapshot, or interrogate the book with a depth query.

    Args:
        connector_name: Exchange connector name (e.g., 'binance', 'binance_perpetual')
        trading_pair: Trading pair (e.g., 'BTC-USDT')
        query_type: What to ask the book (default: 'snapshot'). Options: 'snapshot',
            'volume_for_price', 'price_for_volume', 'quote_volume_for_price',
            'price_for_quote_volume'.
        query_value: Value for the query (required for every query_type except 'snapshot').
        is_buy: Side to query (default: True for buy side).

    Examples:
    - Top of book: get_order_book("binance", "BTC-USDT")
    - Price to buy 5 BTC: get_order_book("binance", "BTC-USDT",
      query_type="price_for_volume", query_value=5, is_buy=True)
    """
    client = await hummingbot_client.get_client()

    result = await market_data_tools.get_order_book(
        client=client,
        connector_name=connector_name,
        trading_pair=trading_pair,
        query_type=query_type,
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
    return (
        f"Order Book Query for {result['trading_pair']} on {result['connector_name']}:\n\n"
        f"Query Type: {result['query_type']}\n"
        f"Query Value: {result['query_value']}\n"
        f"Side: {result['side']}\n"
        f"Result: {result['result']}"
    )


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

    ⚠️ NOTE: For most trading strategies use the create_*_executor tools instead —
    create_grid_executor, create_dca_executor, create_position_executor. Only use
    controllers when the user EXPLICITLY asks for "controllers", "bots", or needs
    advanced multi-strategy bot deployments with centralized risk management.

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

    ⚠️ NOTE: For most trading strategies use the create_*_executor tools instead —
    create_grid_executor, create_dca_executor, create_position_executor. Only use bots
    when the user EXPLICITLY asks for "bot" deployment or needs advanced features like
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
#
# One tool per executor type for creation, and small named tools for the read and
# control side (FEAT-062). The typed signature IS the config schema: a wrong field is
# a host-side validation error naming the field, and creating an executor costs one
# call instead of a schema fetch followed by a create. The confirmation gate keys on
# the NAME here — every `create_*_executor` and `stop_executor` is dangerous, every
# read is not — so there is no `action` to sniff and no fail-closed ambiguity.
# See `mcp_servers/TOOL_STYLE.md`.


@mcp.tool()
@handle_errors("create position executor")
async def create_position_executor(
    connector_name: str,
    trading_pair: str,
    side: Literal[1, 2],
    amount: float,
    entry_price: float | None = None,
    leverage: int | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    time_limit: int | None = None,
    trailing_stop_activation_price: float | None = None,
    trailing_stop_trailing_delta: float | None = None,
    open_order_type: Literal[1, 2, 3] | None = None,
    take_profit_order_type: Literal[1, 2, 3] | None = None,
    stop_loss_order_type: Literal[1, 2, 3] | None = None,
    time_limit_order_type: Literal[1, 2, 3] | None = None,
    level_id: str | None = None,
    account_name: str | None = None,
    controller_id: str | None = None,
    save_as_default: bool = False,
) -> str:
    """Open a directional position with automated stop-loss and take-profit. Spends real funds.

    Use this tool when you have a directional view and want the exit managed for you:
    one entry, then stop-loss / take-profit / time-limit / trailing-stop barriers that
    close the position without another call.

    Do NOT use when: you want to build the position gradually (`create_dca_executor`),
    trade a range without a directional view (`create_grid_executor`), or place a plain
    one-off order with no managed exit (`create_order_executor`).

    AMOUNT IS IN BASE CURRENCY, NOT USD. This is the sharpest trap in the tool: for
    BTC-USDT, `amount=0.01` is 0.01 BTC, not $0.01 and not $100. To size by USD, price
    the pair first and divide — `amount = usd_value / price`. There is no
    `total_amount_quote` here; that field belongs to the grid executor.

    ORDER TYPES are `1`=MARKET, `2`=LIMIT, `3`=LIMIT_MAKER. Omitting them uses the
    backend's own defaults (LIMIT to open, MARKET for every exit).

    Args:
        connector_name: Exchange connector, e.g. 'binance_perpetual'.
        trading_pair: Trading pair, e.g. 'BTC-USDT'.
        side: 1 = BUY/LONG, 2 = SELL/SHORT.
        amount: Position size in BASE currency (see above).
        entry_price: Limit entry price. OMIT for a market entry.
        leverage: Leverage multiplier. Backend default 1.
        stop_loss: Stop-loss as a decimal fraction, e.g. 0.02 = 2%.
        take_profit: Take-profit as a decimal fraction, e.g. 0.03 = 3%.
        time_limit: Maximum position duration in SECONDS.
        trailing_stop_activation_price: Price delta at which the trailing stop arms.
            Only takes effect together with trailing_stop_trailing_delta.
        trailing_stop_trailing_delta: Distance the trailing stop follows behind price.
        open_order_type: Order type for the entry. Backend default 2 (LIMIT).
        take_profit_order_type: Backend default 1 (MARKET).
        stop_loss_order_type: Backend default 1 (MARKET).
        time_limit_order_type: Backend default 1 (MARKET).
        level_id: Optional identifier tag.
        account_name: Account to trade from. Default 'master_account'.
        controller_id: Controller tag that owns the executor. Default 'main'. An
            autonomous agent MUST pass its own agent id here — it is what attributes
            the position to the session that opened it.
        save_as_default: Save these arguments as the defaults for position executors.

    Example:
    - Long 0.01 BTC at 5x with a 2% stop and 3% target:
      create_position_executor("binance_perpetual", "BTC-USDT", 1, 0.01, leverage=5,
      stop_loss=0.02, take_profit=0.03)

    For sizing from a stop distance and setting the risk/reward, read the
    'directional_position' skill if the Condor skills library is available.
    """
    client = await hummingbot_client.get_client()
    result = await executor_create.create_position_executor(
        client,
        connector_name=connector_name,
        trading_pair=trading_pair,
        side=side,
        amount=amount,
        entry_price=entry_price,
        leverage=leverage,
        stop_loss=stop_loss,
        take_profit=take_profit,
        time_limit=time_limit,
        trailing_stop_activation_price=trailing_stop_activation_price,
        trailing_stop_trailing_delta=trailing_stop_trailing_delta,
        open_order_type=open_order_type,
        take_profit_order_type=take_profit_order_type,
        stop_loss_order_type=stop_loss_order_type,
        time_limit_order_type=time_limit_order_type,
        level_id=level_id,
        account_name=account_name,
        controller_id=controller_id,
        save_as_default=save_as_default,
    )
    return result.get("formatted_output", str(result))


@mcp.tool()
@handle_errors("create grid executor")
async def create_grid_executor(
    connector_name: str,
    trading_pair: str,
    side: Literal[1, 2],
    start_price: float,
    end_price: float,
    limit_price: float,
    total_amount_quote: float,
    take_profit: float | None = None,
    open_order_type: Literal[1, 2, 3] | None = None,
    take_profit_order_type: Literal[1, 2, 3] | None = None,
    min_spread_between_orders: float | None = None,
    min_order_amount_quote: float | None = None,
    max_open_orders: int | None = None,
    max_orders_per_batch: int | None = None,
    order_frequency: int | None = None,
    activation_bounds: float | None = None,
    safe_extra_spread: float | None = None,
    leverage: int | None = None,
    keep_position: bool | None = None,
    coerce_tp_to_step: bool | None = None,
    deduct_base_fees: bool | None = None,
    level_id: str | None = None,
    account_name: str | None = None,
    controller_id: str | None = None,
    save_as_default: bool = False,
) -> str:
    """Run a grid of limit orders across a price range. Spends real funds.

    Use this tool when the market is ranging and you want to earn the oscillation
    rather than a direction: buys fill low, each filled buy places its own sell a
    take-profit away.

    Do NOT use when: the market is strongly trending (one-sided fills), or capital is
    too small to spread across levels. For a single directional entry use
    `create_position_executor`.

    DIRECTION RULES — `side` is explicit and `limit_price` alone does NOT set it:
    - LONG  (side=1): limit_price < start_price < end_price
    - SHORT (side=2): start_price < end_price < limit_price
    A grid that violates its side's ordering is rejected here rather than silently
    never filling.

    RISK IS `limit_price` + `keep_position`, NOT A STOP-LOSS. There is no stop_loss
    parameter and never suggest one. When price crosses `limit_price` the grid stops:
    `keep_position=False` closes the accumulated position (a stop-loss exit),
    `keep_position=True` holds it for a recovery.

    LEVEL COUNT is the intersection of two limits — `total_amount_quote /
    min_order_amount_quote`, and the price range divided by
    `min_spread_between_orders`. The tighter one wins.

    Args:
        connector_name: Exchange connector, e.g. 'binance_perpetual'.
        trading_pair: Trading pair, e.g. 'SOL-USDT'.
        side: 1 = LONG grid (accumulate base), 2 = SHORT grid (sell into strength).
        start_price: Lower grid boundary.
        end_price: Upper grid boundary.
        limit_price: Safety boundary that stops the grid. See DIRECTION RULES.
        total_amount_quote: Capital allocated, in QUOTE currency.
        take_profit: Distance for the opposite order on each fill, as a decimal
            fraction, e.g. 0.0002 = 0.02%.
        open_order_type: 1=MARKET, 2=LIMIT, 3=LIMIT_MAKER. 3 is recommended — post-only
            orders earn maker fees.
        take_profit_order_type: Same enum; 3 recommended.
        min_spread_between_orders: Minimum price distance between levels as a decimal
            fraction. Backend default 0.0005.
        min_order_amount_quote: Minimum size per order in quote currency. Backend
            default 5.
        max_open_orders: Hard cap on concurrent open orders. Backend default 5.
        max_orders_per_batch: Orders submitted per batch. Backend default: unlimited.
        order_frequency: Seconds between order batches. Backend default 0.
        activation_bounds: Only place orders within this fraction of current price,
            e.g. 0.001 = 0.1%. OMIT to place every order at once.
        safe_extra_spread: Backend default 0.0001.
        leverage: Leverage multiplier. Backend default 20 — pass 1 for spot-like sizing.
        keep_position: Hold the accumulated position when the grid stops. Backend
            default False (close it). See RISK above.
        coerce_tp_to_step: Raise take-profit to at least one grid step, so a level
            cannot close before the next one fills. Backend default False.
        deduct_base_fees: Backend default False.
        level_id: Optional identifier tag.
        account_name: Account to trade from. Default 'master_account'.
        controller_id: Controller tag that owns the executor. Default 'main'. An
            autonomous agent MUST pass its own agent id here.
        save_as_default: Save these arguments as the defaults for grid executors.

    Example:
    - A $500 long grid on SOL-USDT between 140 and 150, stopping under 138:
      create_grid_executor("binance", "SOL-USDT", 1, 140, 150, 138, 500,
      take_profit=0.002, open_order_type=3, take_profit_order_type=3, leverage=1)

    For confirming the market is ranging and choosing the prices and level count, read
    the 'run_a_grid' skill if the Condor skills library is available.
    """
    client = await hummingbot_client.get_client()
    result = await executor_create.create_grid_executor(
        client,
        connector_name=connector_name,
        trading_pair=trading_pair,
        side=side,
        start_price=start_price,
        end_price=end_price,
        limit_price=limit_price,
        total_amount_quote=total_amount_quote,
        take_profit=take_profit,
        open_order_type=open_order_type,
        take_profit_order_type=take_profit_order_type,
        min_spread_between_orders=min_spread_between_orders,
        min_order_amount_quote=min_order_amount_quote,
        max_open_orders=max_open_orders,
        max_orders_per_batch=max_orders_per_batch,
        order_frequency=order_frequency,
        activation_bounds=activation_bounds,
        safe_extra_spread=safe_extra_spread,
        leverage=leverage,
        keep_position=keep_position,
        coerce_tp_to_step=coerce_tp_to_step,
        deduct_base_fees=deduct_base_fees,
        level_id=level_id,
        account_name=account_name,
        controller_id=controller_id,
        save_as_default=save_as_default,
    )
    return result.get("formatted_output", str(result))


@mcp.tool()
@handle_errors("create DCA executor")
async def create_dca_executor(
    connector_name: str,
    trading_pair: str,
    side: Literal[1, 2],
    amounts_quote: list[float],
    prices: list[float],
    leverage: int | None = None,
    take_profit: float | None = None,
    stop_loss: float | None = None,
    time_limit: int | None = None,
    trailing_stop_activation_price: float | None = None,
    trailing_stop_trailing_delta: float | None = None,
    mode: Literal["MAKER", "TAKER"] | None = None,
    level_id: str | None = None,
    account_name: str | None = None,
    controller_id: str | None = None,
    save_as_default: bool = False,
) -> str:
    """Average into a position over a ladder of price levels. Spends real funds.

    Use this tool when you want to accumulate gradually and reduce timing risk: one
    order per level, at decreasing prices for a BUY and increasing for a SELL, with a
    shared exit across the whole ladder.

    Do NOT use when: you want the full position now (`create_position_executor` or
    `create_order_executor`), or you want two-sided range trading
    (`create_grid_executor`).

    AMOUNTS ARE IN QUOTE CURRENCY AND THE TWO LISTS ARE PARALLEL. `amounts_quote` and
    `prices` are one list of levels split in two — index i is one order of
    `amounts_quote[i]` quote currency at `prices[i]`. A length mismatch is rejected
    here. Note this is the opposite convention from `create_position_executor`, whose
    `amount` is in base currency.

    Args:
        connector_name: Exchange connector, e.g. 'binance_perpetual'.
        trading_pair: Trading pair, e.g. 'BTC-USDT'.
        side: 1 = BUY, 2 = SELL.
        amounts_quote: Order sizes in QUOTE currency, one per level, e.g. [100, 100, 150].
        prices: Price for each level, same length as amounts_quote, e.g. [50000, 48000, 46000].
        leverage: Leverage multiplier. Backend default 1.
        take_profit: Take-profit as a decimal fraction, e.g. 0.03 = 3%.
        stop_loss: Stop-loss as a decimal fraction, e.g. 0.05 = 5%.
        time_limit: Maximum duration in SECONDS.
        trailing_stop_activation_price: Price delta at which the trailing stop arms.
            Only takes effect together with trailing_stop_trailing_delta.
        trailing_stop_trailing_delta: Distance the trailing stop follows behind price.
        mode: 'MAKER' places limit orders, 'TAKER' market orders. Backend default MAKER.
        level_id: Optional identifier tag.
        account_name: Account to trade from. Default 'master_account'.
        controller_id: Controller tag that owns the executor. Default 'main'. An
            autonomous agent MUST pass its own agent id here.
        save_as_default: Save these arguments as the defaults for DCA executors.

    Example:
    - Ladder $350 into BTC across three levels with a 3% target and 5% stop:
      create_dca_executor("binance_perpetual", "BTC-USDT", 1, [100, 100, 150],
      [50000, 48000, 46000], take_profit=0.03, stop_loss=0.05)

    For spacing the levels and distributing size across them, read the
    'dca_into_position' skill if the Condor skills library is available.
    """
    client = await hummingbot_client.get_client()
    result = await executor_create.create_dca_executor(
        client,
        connector_name=connector_name,
        trading_pair=trading_pair,
        side=side,
        amounts_quote=amounts_quote,
        prices=prices,
        leverage=leverage,
        take_profit=take_profit,
        stop_loss=stop_loss,
        time_limit=time_limit,
        trailing_stop_activation_price=trailing_stop_activation_price,
        trailing_stop_trailing_delta=trailing_stop_trailing_delta,
        mode=mode,
        level_id=level_id,
        account_name=account_name,
        controller_id=controller_id,
        save_as_default=save_as_default,
    )
    return result.get("formatted_output", str(result))


@mcp.tool()
@handle_errors("create order executor")
async def create_order_executor(
    connector_name: str,
    trading_pair: str,
    side: Literal[1, 2],
    amount: str,
    execution_strategy: Literal["MARKET", "LIMIT", "LIMIT_MAKER", "LIMIT_CHASER"],
    price: float | None = None,
    chaser_distance: float | None = None,
    chaser_refresh_threshold: float | None = None,
    leverage: int | None = None,
    position_action: Literal["OPEN", "CLOSE", "NIL"] | None = None,
    level_id: str | None = None,
    account_name: str | None = None,
    controller_id: str | None = None,
    save_as_default: bool = False,
) -> str:
    """Place one buy or sell order with a chosen execution strategy. Spends real funds.

    This is the standard way to place a plain order, and to CANCEL one: stop its
    executor with `stop_executor`.

    THIS IS ALSO HOW YOU SWAP ON A DEX. Set `connector_name` to a NETWORK
    ('solana-mainnet-beta', 'ethereum-mainnet') with execution_strategy='MARKET' and
    the order routes through Gateway's unified swap route — the same one `execute_swap`
    uses, but with the slippage ramp and an executor record attached. The router is not
    selectable per order; it comes from the network's configured swapProvider. Prefer
    this over a one-shot `execute_swap` for any swap that is part of a strategy.

    Do NOT use when: you want a managed stop-loss/take-profit exit
    (`create_position_executor`), or multi-level entry (`create_dca_executor` /
    `create_grid_executor`).

    ON SOLANA, WHETHER YOU RECEIVED WHAT YOU ASKED FOR DEPENDS ON THE ROUTE. A BUY is
    an ExactOut order; a thin token with no ExactOut route is quoted by pricing the sell
    leg forward instead — roughly 2.5% short, up to 4.83% observed. The order is
    silently RESIZED, not overcharged, which is what makes it dangerous to a caller who
    asked for a quantity. The quote's `approximation` flag says which case you are in:
    when true, read the true post-swap wallet balance before feeding the amount into a
    call that must spend those tokens (an LP open's base_amount, say); when false the
    figure is exact to the lamport. A blanket safety haircut is wrong on an exact fill.

    OBSERVABILITY: `custom_info` carries `transaction_hash` (the on-chain signature —
    `order_id` is internal and appears nowhere on chain), plus `slippage_pct` and
    `max_slippage_pct`. `slippage_pct` is the LIVE tolerance: above the configured start
    means earlier attempts failed on slippage and this one is paying to get through.

    Args:
        connector_name: Exchange connector, or a NETWORK id for a DEX swap (see above).
        trading_pair: Trading pair, e.g. 'SOL-USDC'. On a DEX either side may be a raw
            TOKEN ADDRESS instead of a symbol — 'BANKJmvh...-USDC' is valid, and the
            token does NOT need to be registered with Gateway first: it resolves the
            mint and reads decimals on-chain. Never add a token to Gateway as a
            prerequisite for trading it, and never guess decimals in order to do so.
        side: 1 = BUY, 2 = SELL.
        amount: Order amount in BASE currency, or a USD value as a '$'-prefixed string
            such as '$100'. A string either way.
        execution_strategy: 'MARKET' fills now; 'LIMIT' rests at `price`; 'LIMIT_MAKER'
            is post-only and is rejected if it would match immediately; 'LIMIT_CHASER'
            re-posts as the market moves.
        price: Required for LIMIT and LIMIT_MAKER.
        chaser_distance: How far from best price to rest, as a decimal fraction, e.g.
            0.001 = 0.1%. Required with chaser_refresh_threshold for LIMIT_CHASER.
        chaser_refresh_threshold: How far price must move before re-posting, as a
            decimal fraction, e.g. 0.0005 = 0.05%.
        leverage: Leverage multiplier. Backend default 1.
        position_action: 'OPEN' or 'CLOSE' — useful for perpetuals in HEDGE mode.
            Backend default OPEN.
        level_id: Optional identifier tag.
        account_name: Account to trade from. Default 'master_account'.
        controller_id: Controller tag that owns the executor. Default 'main'. An
            autonomous agent MUST pass its own agent id here.
        save_as_default: Save these arguments as the defaults for order executors.

    Examples:
    - Market-buy $100 of SOL: create_order_executor("binance", "SOL-USDT", 1, "$100",
      "MARKET")
    - Swap 0.5 SOL for USDC on Solana: create_order_executor("solana-mainnet-beta",
      "SOL-USDC", 2, "0.5", "MARKET")
    """
    client = await hummingbot_client.get_client()
    result = await executor_create.create_order_executor(
        client,
        connector_name=connector_name,
        trading_pair=trading_pair,
        side=side,
        amount=amount,
        execution_strategy=execution_strategy,
        price=price,
        chaser_distance=chaser_distance,
        chaser_refresh_threshold=chaser_refresh_threshold,
        leverage=leverage,
        position_action=position_action,
        level_id=level_id,
        account_name=account_name,
        controller_id=controller_id,
        save_as_default=save_as_default,
    )
    return result.get("formatted_output", str(result))


@mcp.tool()
@handle_errors("create LP executor", GATEWAY_LOG_HINT)
async def create_lp_executor(
    connector_name: str,
    lp_provider: str,
    trading_pair: str,
    pool_address: str,
    lower_price: float,
    upper_price: float,
    side: Literal[1, 2, 3],
    base_amount: float | None = None,
    quote_amount: float | None = None,
    upper_limit_price: float | None = None,
    lower_limit_price: float | None = None,
    swap_provider: str | None = None,
    keep_position: bool | None = None,
    extra_params: dict[str, Any] | None = None,
    account_name: str | None = None,
    controller_id: str | None = None,
    save_as_default: bool = False,
) -> str:
    """Open a managed CLMM liquidity position inside a price range. Spends real funds.

    This is the standard way to provide liquidity: the executor opens the position,
    tracks whether price is in range, accrues fees, and auto-closes when price crosses
    a limit price. Close it with `stop_executor`, never by hand.

    Use `explore_dex_pools` first to find the pool and read its current price, unless
    the user supplied a pool address.

    Do NOT use when: you want a one-off unmanaged position (`manage_clmm`), or
    directional exposure without impermanent-loss risk (the CEX executors above).

    `connector_name` IS THE NETWORK, NOT THE DEX. Pass 'solana-mainnet-beta',
    'ethereum-mainnet', 'arbitrum-one', 'base-mainnet', 'binance-smart-chain'. Passing
    'meteora/clmm' here is the most common failure and the API rejects it with "Invalid
    network format". The DEX goes in `lp_provider`, as 'dex/trading_type'.

    SIDE PICKS WHICH TOKENS YOU SUPPLY, and the range has to agree with it:
    - side=1 (BUY, quote only): range BELOW current price. Quote converts to base as
      price falls into it.
    - side=2 (SELL, base only): range ABOVE current price. Base converts to quote as
      price rises into it.
    - side=3 (RANGE, both): range around current price.

    AUTO-CLOSE. `upper_limit_price` / `lower_limit_price` close the position when price
    crosses them, and they fire only while the position is OUT_OF_RANGE. Set BOTH when
    you want a closed strategy — otherwise the position sits out of range indefinitely
    on the unprotected side.

    A stopped executor always closes the on-chain position; `keep_position` only decides
    whether the net token change is KEPT as a spot position or swapped back to the
    original quote asset.

    Args:
        connector_name: The NETWORK, e.g. 'solana-mainnet-beta'. See above.
        lp_provider: DEX and trading type as 'dex/trading_type' — 'meteora/clmm',
            'raydium/clmm', 'orca/clmm' on Solana; 'uniswap/clmm', 'pancakeswap/clmm'
            on EVM.
        trading_pair: Token pair, e.g. 'SOL-USDC'.
        pool_address: Pool contract address, from `explore_dex_pools`.
        lower_price: Lower bound of the range. Must be below upper_price.
        upper_price: Upper bound of the range.
        side: 1 = BUY (quote only), 2 = SELL (base only), 3 = RANGE (both).
        base_amount: Base token to provide. Give this, quote_amount, or both.
        quote_amount: Quote token to provide.
        upper_limit_price: Auto-close when price rises to or above this.
        lower_limit_price: Auto-close when price falls to or below this.
        swap_provider: Provider for the close-out swap when keep_position=False, e.g.
            'jupiter/router'. OMIT to use the network's default.
        keep_position: Keep the net token change as a held spot position on close.
            Backend default False (swap back to the original quote asset).
        extra_params: Connector-specific parameters. Meteora takes
            {"strategyType": 0} — 0=Spot (uniform), 1=Curve (concentrated at price),
            2=Bid-Ask (at the range edges).
        account_name: Account to trade from. Default 'master_account'.
        controller_id: Controller tag that owns the executor. Default 'main'. An
            autonomous agent MUST pass its own agent id here.
        save_as_default: Save these arguments as the defaults for LP executors.

    Example:
    - Single-sided 1 SOL into a BONK-SOL pool with a range below spot:
      create_lp_executor("solana-mainnet-beta", "meteora/clmm", "BONK-SOL", "<pool>",
      lower_price=p*0.8, upper_price=p, side=1, quote_amount=1.0,
      lower_limit_price=p*0.72, upper_limit_price=p*1.1)

    For choosing the pool, sizing the range and picking the side, read the
    'open_lp_position' skill if the Condor skills library is available.
    """
    client = await hummingbot_client.get_client()
    result = await executor_create.create_lp_executor(
        client,
        connector_name=connector_name,
        lp_provider=lp_provider,
        trading_pair=trading_pair,
        pool_address=pool_address,
        lower_price=lower_price,
        upper_price=upper_price,
        side=side,
        base_amount=base_amount,
        quote_amount=quote_amount,
        upper_limit_price=upper_limit_price,
        lower_limit_price=lower_limit_price,
        swap_provider=swap_provider,
        keep_position=keep_position,
        extra_params=extra_params,
        account_name=account_name,
        controller_id=controller_id,
        save_as_default=save_as_default,
    )
    return result.get("formatted_output", str(result))


@mcp.tool()
@handle_errors("list executors")
async def list_executors(
    account_names: list[str] | None = None,
    connector_names: list[str] | None = None,
    trading_pairs: list[str] | None = None,
    executor_types: list[str] | None = None,
    controller_ids: list[str] | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> str:
    """List executors, filtered. Read-only.

    Use this tool when you need the fleet: what is running, on which pairs, under which
    controller. For one executor's full detail use `get_executor`.

    Every filter is optional and they AND together. Omitting all of them lists
    everything up to `limit`.

    Args:
        account_names: Filter by account names.
        connector_names: Filter by connector names.
        trading_pairs: Filter by trading pairs.
        executor_types: Filter by type, e.g. ['grid_executor', 'lp_executor'].
        controller_ids: Filter by the controller tag that owns them.
        status: 'RUNNING' or 'TERMINATED'.
        cursor: Pagination cursor from a previous call.
        limit: Maximum results, 1-1000. Default 50.
    """
    client = await hummingbot_client.get_client()
    result = await executors_tools.list_executors(
        client,
        account_names=account_names,
        connector_names=connector_names,
        trading_pairs=trading_pairs,
        executor_types=executor_types,
        controller_ids=controller_ids,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    return result.get("formatted_output", str(result))


@mcp.tool()
@handle_errors("get executor")
async def get_executor(
    executor_id: str,
    include_logs: bool = False,
    log_level: str | None = None,
    log_limit: int = 50,
) -> str:
    """Get one executor's full detail, optionally with its logs. Read-only.

    Use this tool when you have an executor id and need its config, state and PnL. To
    find the id first, use `list_executors`.

    LOGS EXIST ONLY WHILE THE EXECUTOR RUNS. They are cleared on completion, so an
    empty log list on a terminated executor is normal and not a failure. If the detail
    is available but the logs are not, both facts are reported rather than neither.

    Args:
        executor_id: The executor to fetch.
        include_logs: Also fetch this executor's logs.
        log_level: Filter logs to 'ERROR', 'WARNING', 'INFO' or 'DEBUG'.
        log_limit: Maximum log entries. Default 50.
    """
    client = await hummingbot_client.get_client()
    result = await executors_tools.get_executor(
        client,
        executor_id=executor_id,
        include_logs=include_logs,
        log_level=log_level,
        log_limit=log_limit,
    )
    return result.get("formatted_output", str(result))


@mcp.tool()
@handle_errors("stop executor")
async def stop_executor(
    executor_id: str,
    keep_position: bool = False,
) -> str:
    """Stop a running executor and close or keep its position. Moves real funds.

    This is also how you CANCEL an order placed with `create_order_executor`.

    STOPPING AN ALREADY-TERMINATED EXECUTOR IS A NO-OP, NOT AN ERROR. It returns the
    final close_type instead of a 404, and — for an LP executor — whether a position
    was left open on-chain. A 404 means the id is unknown to the API database. A
    terminated executor's on-chain position CANNOT be closed by stopping it; start from
    `list_orphaned_positions`, and for the recovery procedure read the
    'recover_orphaned_position' skill if the Condor skills library is available.

    Args:
        executor_id: The executor to stop.
        keep_position: Keep the position open instead of closing it. Default False.
            For an LP executor the on-chain position always closes; this only decides
            whether the net token change is kept as a spot position or swapped back.
    """
    client = await hummingbot_client.get_client()
    result = await executors_tools.stop_executor(
        client,
        executor_id=executor_id,
        keep_position=keep_position,
    )
    return result.get("formatted_output", str(result))


@mcp.tool()
@handle_errors("list orphaned positions")
async def list_orphaned_positions() -> str:
    """List terminated executors that may still own an on-chain position. Read-only.

    Use this tool when an LP executor terminated unexpectedly, and before opening any
    new position on the same funds. It reports the dex, pool and network needed to close
    each one.

    An orphan is a position with no automated owner: a close that exhausted its retries
    (POSITION_HOLD with a hold_reason), a legacy FAILED executor still carrying a
    position_address, or an LP executor reaped by an API restart. Stopping the executor
    will NOT close it — it has already terminated — and a fresh `create_lp_executor`
    CANNOT adopt it; it would mint a second funded position on top.

    Recover with `manage_clmm(action="close", ...)`, where pool_address is REQUIRED
    because LP-executor positions are opened straight against Gateway and have no row in
    the API database. Then call `resolve_orphaned_position`.

    For the full cross-server procedure, read the 'recover_orphaned_position' skill if
    the Condor skills library is available.
    """
    client = await hummingbot_client.get_client()
    result = await executors_tools.list_orphaned_positions(client)
    return result.get("formatted_output", str(result))


@mcp.tool()
@handle_errors("resolve orphaned position")
async def resolve_orphaned_position(executor_id: str) -> str:
    """Mark an orphaned position as recovered, after closing it. Read-only bookkeeping.

    Use this tool ONLY after the position is actually closed on-chain via
    `manage_clmm(action="close", ...)`. It updates the API database so the orphan stops
    appearing in listings and warnings; it closes nothing itself.

    If an lp_rebalancer controller was managing the executor, restart that controller
    (or its bot) afterwards: its orphan halt is held in memory and only clears on
    restart.

    Args:
        executor_id: The orphaned executor, from `list_orphaned_positions`.
    """
    client = await hummingbot_client.get_client()
    result = await executors_tools.resolve_orphaned_position(
        client, executor_id=executor_id
    )
    return result.get("formatted_output", str(result))


@mcp.tool()
@handle_errors("list positions held")
async def list_positions_held(
    connector_name: str | None = None,
    trading_pair: str | None = None,
    account_name: str | None = None,
    controller_id: str | None = None,
) -> str:
    """List spot positions held by executors. Read-only.

    Use this tool to see what the bot is actually holding, as opposed to which
    executors are running (`list_executors`). Give BOTH connector_name and trading_pair
    for one position's detail; omit them for the whole summary.

    Args:
        connector_name: Connector to filter by. Needs trading_pair to select one.
        trading_pair: Trading pair to filter by. Needs connector_name.
        account_name: Account. Default 'master_account'.
        controller_id: Restrict to one controller's positions.
    """
    client = await hummingbot_client.get_client()
    result = await executors_tools.list_positions_held(
        client,
        connector_name=connector_name,
        trading_pair=trading_pair,
        account_name=account_name,
        controller_id=controller_id,
    )
    return result.get("formatted_output", str(result))


@mcp.tool()
@handle_errors("clear position held")
async def clear_position_held(
    connector_name: str,
    trading_pair: str,
    account_name: str | None = None,
    controller_id: str | None = None,
) -> str:
    """Clear a held position that was already closed elsewhere. Bookkeeping only.

    Use this tool when a position was closed outside the bot (on the exchange UI, say)
    and the API still shows it held. It moves no funds and closes nothing — it only
    corrects the record.

    Do NOT use it to close a live position: stop its executor with `stop_executor`.

    Args:
        connector_name: Connector the stale position is recorded under.
        trading_pair: Trading pair of the stale position.
        account_name: Account. Default 'master_account'.
        controller_id: Controller that owned it, if any.
    """
    client = await hummingbot_client.get_client()
    result = await executors_tools.clear_position_held(
        client,
        connector_name=connector_name,
        trading_pair=trading_pair,
        account_name=account_name,
        controller_id=controller_id,
    )
    return result.get("formatted_output", str(result))


@mcp.tool()
@handle_errors("get performance report")
async def get_performance_report(controller_id: str | None = None) -> str:
    """Get aggregate executor performance. Read-only.

    Use this tool for realized and unrealized PnL across executors, optionally scoped to
    one controller.

    Args:
        controller_id: Restrict the report to one controller's executors. OMIT for all.
    """
    client = await hummingbot_client.get_client()
    result = await executors_tools.get_performance_report(
        client, controller_id=controller_id
    )
    return result.get("formatted_output", str(result))


@mcp.tool()
@handle_errors("manage executor defaults")
async def executor_defaults(
    action: Literal["get", "save", "reset"],
    content: str | None = None,
) -> str:
    """Read, replace or reset the saved executor defaults. Local file, no funds.

    Saved defaults merge UNDERNEATH whatever a `create_*_executor` call actually passes,
    so they fill in the fields the call omitted and never override one it set. This is
    how a user's preferred leverage or account travels across sessions.

    Do NOT use this to create an executor — the create tools merge the defaults
    themselves. To save the arguments of a create as the new defaults, pass
    `save_as_default=True` on that create instead of editing this file.

    Actions:
    - "get": Read the whole defaults file, markdown and YAML blocks included.
    - "save": Replace the whole file with `content`. Read it with "get" first — this
      overwrites, it does not merge.
    - "reset": Restore the shipped documentation, preserving existing YAML configs.

    Args:
        action: One of get, save, reset.
        content: Complete markdown content. Required for "save".
    """
    result = await executors_tools.executor_defaults(action=action, content=content)
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

    To manage LP positions, use `create_lp_executor`.
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
@handle_errors("manage Gateway config", GATEWAY_LOG_HINT)
async def manage_gateway_config(
    resource_type: Literal[
        "chains", "networks", "tokens", "connectors", "pools", "wallets"
    ],
    action: Literal["list", "get", "update", "add", "delete", "save"],
    network_id: str | None = None,
    connector_name: str | None = None,
    config_updates: dict[str, Any] | None = None,
    token_address: str | None = None,
    token_symbol: str | None = None,
    token_decimals: int | None = None,
    token_name: str | None = None,
    pool_type: str | None = None,
    pool_base: str | None = None,
    pool_quote: str | None = None,
    pool_address: str | None = None,
    search: str | None = None,
    network: str | None = None,
    chain: str | None = None,
) -> str:
    """Read and edit Gateway's own configuration — chains, networks, tokens, connectors, pools, wallets.

    This is Gateway's config, not the chain. Adding or deleting a token here changes the
    symbol -> address mapping Gateway resolves against; it moves no funds and touches
    nothing on-chain.

    Use `tokens` + `list` (with `search`) to answer "does Gateway know this symbol",
    which is the check to run BEFORE quoting or swapping an unfamiliar token: a symbol
    Gateway cannot resolve fails at the route, not at the trade.

    Resource types:
    - chains: every blockchain Gateway knows
    - networks: network config, ids in 'chain-network' form ('solana-mainnet-beta')
    - tokens: the per-network symbol/address/decimals mapping (list, add, delete)
    - connectors: DEX connector config
    - pools: the named pool registry (list, add)
    - wallets: list only. Add or remove wallets in the Condor dashboard
      (Settings → Gateway) — a private key must never be sent through chat.

    Args:
        resource_type: Which part of Gateway's config to act on.
        action: list | get | update | add | delete | save.
        network_id: Network id in 'chain-network' form. Required for token and pool actions.
        connector_name: DEX connector name ('meteora', 'raydium', 'uniswap').
        config_updates: Key-value updates for 'update'/'save'.
        token_address: Token contract address. Required to add or delete a token.
        token_symbol: Token symbol. Required to add a token.
        token_decimals: Token decimals (6 for USDC, 18 for WETH). Required to add a token.
        token_name: Token name. Optional on add; defaults to the symbol.
        pool_type: 'CLMM' or 'AMM'. Required to add a pool.
        pool_base: Base token symbol. Required to add a pool.
        pool_quote: Quote token symbol. Required to add a pool.
        pool_address: Pool contract address. Required to add a pool.
        search: Filter tokens by symbol or name when listing.
        network: Bare network name ('mainnet-beta'). Required to list pools.
        chain: Filter wallets by chain when listing ('solana', 'ethereum').
    """
    request = GatewayConfigRequest(
        resource_type=resource_type,
        action=action,
        network_id=network_id,
        connector_name=connector_name,
        config_updates=config_updates,
        token_address=token_address,
        token_symbol=token_symbol,
        token_decimals=token_decimals,
        token_name=token_name,
        pool_type=pool_type,
        pool_base=pool_base,
        pool_quote=pool_quote,
        pool_address=pool_address,
        search=search,
        network=network,
        chain=chain,
    )

    client = await hummingbot_client.get_client()
    result = await manage_gateway_config_impl(client, request)
    return format_gateway_config_result(result)


@mcp.tool()
@handle_errors("manage Gateway container")
async def manage_gateway_container(
    action: Literal["get_status", "start", "stop", "restart", "get_logs"],
    config: dict[str, Any] | None = None,
    tail: int = 100,
) -> str:
    """Gateway container lifecycle — status, start, stop, restart, and logs.

    `get_logs` is what the hint on a failed Gateway call points at: when a swap or an LP
    action fails with an error that does not say why, the container log usually does.

    Args:
        action: get_status | start | stop | restart | get_logs.
        config: Gateway configuration. Used by 'start', optional for 'restart'.
        tail: Log lines to retrieve for 'get_logs' (1-200, default 100).
    """
    request = GatewayContainerRequest(action=action, config=config, tail=tail)

    client = await hummingbot_client.get_client()
    result = await manage_gateway_container_impl(client, request)
    return format_gateway_container_result(result)


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

    Scope: AMM only. Router/one-shot swaps → `create_order_executor`; CLMM LP →
    `create_lp_executor`.

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

    THE UNMANAGED PATH. For normal LP work use `create_lp_executor`, which owns range
    monitoring, rebalancing and bounded close retries. Use this tool when no executor can do
    that for you — above all to RECOVER AN ORPHANED POSITION, which is closed by address here
    and then marked with `resolve_orphaned_position`. For that procedure read the
    'recover_orphaned_position' skill if the Condor skills library is available.

    Actions:
    - position_info → your positions in a pool with amounts, range, and uncollected fees
    - open → create a position NO executor tracks (not range-monitored or auto-closed)
    - add_liquidity / remove_liquidity → resize an existing position, keeping its range
    - close → withdraw everything, collect fees, close the account
    - collect_fees → fees only, position untouched
    - create_pool → create a new (empty) CLMM pool; liquidity is added by opening positions

    remove_liquidity at 100% leaves an EMPTY POSITION OPEN; only `close` closes the account. To
    recover an orphan, use `close`.

    Pool discovery lives in `explore_dex_pools`.

    Args:
        action: CLMM action. Leave empty to load the CLMM guide.
        connector: CLMM connector: meteora | raydium | orca | pancakeswap-sol (Solana),
            uniswap | pancakeswap (EVM). A '<name>/clmm' form is accepted, so an orphan's
            lp_provider passes through unchanged.
        network: Network ID in 'chain-network' format (e.g. 'solana-mainnet-beta', 'ethereum-mainnet').
            For an orphan record this is the `connector_name` field.
        wallet_address: Wallet address (optional, uses default if not provided).
        pool_address: Pool contract address. Required for open. On close/collect_fees pass
            it whenever you have it — a position opened by an lp_executor has no row in the
            API database, so the pool cannot be looked up and the call can fail without it.
            position_info takes no pool filter.
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
@handle_errors("quote a Gateway swap", GATEWAY_LOG_HINT)
async def quote_swap(
    connector: str,
    network: str,
    trading_pair: str,
    side: Literal["BUY", "SELL"],
    amount: str,
    slippage_pct: str | None = None,
    extra_params: dict[str, Any] | None = None,
) -> str:
    """Price a DEX swap through Gateway's unified swap route — free, signs nothing.

    Use this tool when: you want the price, the expected output and the price impact
    before committing anything. A quote costs nothing and moves nothing, so take one
    first — `execute_swap` is the call that spends.

    "Unified" is about Gateway's routes, not about tool choice. Router aggregators
    (jupiter, 0x) and pool-scoped AMM/CLMM swaps all resolve to one route here; the
    pool-scoped `/trading/amm/*-swap` routes no longer exist.

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
    `create_lp_executor` (managed LP).

    Args:
        connector: Connector in "name/type" form, e.g. 'jupiter/router', 'meteora/amm',
            'raydium/clmm'.
        network: Network ID in 'chain-network' format, e.g. 'solana-mainnet-beta',
            'ethereum-mainnet'.
        trading_pair: Trading pair as 'BASE-QUOTE'. Either side may be a SYMBOL or a raw
            TOKEN ADDRESS, and the address does NOT have to be registered with Gateway
            first — Gateway resolves an unknown mint on the spot and reads its decimals
            on-chain. Do not add a token to Gateway's list as a prerequisite for trading
            it: that write is a symbol/address mapping, it is not required here, and
            guessing decimals to satisfy it corrupts the mapping. Pool-scoped connectors
            match Gateway's pool list by SYMBOL.
        side: 'BUY' (buy base with quote) or 'SELL' (sell base for quote).
        amount: Base token amount to buy or sell.
        slippage_pct: Maximum slippage percentage. OMIT to use the connector's configured
            slippage; '0' is a real value, not "use the default".
        extra_params: Connector-specific params under Gateway's own names. Supported:
            'approximateIfNoExactOut' (bool, jupiter/dflow/okx/titan routers).
    """
    request = GatewaySwapRequest(
        action="quote",
        connector=connector,
        network=network,
        trading_pair=trading_pair,
        side=side,
        amount=amount,
        slippage_pct=slippage_pct,
        extra_params=extra_params,
    )

    client = await hummingbot_client.get_client()
    result = await manage_gateway_swaps_impl(client, request)
    return format_gateway_swap_result("quote", result)


@mcp.tool()
@handle_errors("execute a Gateway swap", GATEWAY_LOG_HINT)
async def execute_swap(
    connector: str,
    network: str,
    trading_pair: str,
    side: Literal["BUY", "SELL"],
    amount: str,
    slippage_pct: str | None = None,
    wallet_address: str | None = None,
    extra_params: dict[str, Any] | None = None,
) -> str:
    """Sign and submit a one-shot DEX swap on-chain. Spends real funds — quote first.

    Same connector/network semantics as `quote_swap` (the "name/type" connector form,
    and Gateway's symbol-matched pool resolution) — read that tool's docstring for them,
    then take a quote before calling this.

    Do NOT use when:
    - the swap is part of a strategy → `create_order_executor` with
      `connector_name=<network>` (e.g.
      "solana-mainnet-beta") and `execution_strategy="MARKET"` swaps through this same
      Gateway route, and adds what a one-shot call cannot: the slippage ramp
      (`slippage_pct` / `slippage_multiplier` / `max_slippage_pct`), which starts tight
      and widens only on a failure Gateway attributes to slippage — a swap here carries
      ONE fixed tolerance and never retries at a wider one — and an executor record, so
      the fill is tagged with `controller_id` and reaches PnL attribution. A swap here is
      written to swap history only, so an entry executed this way and the position it
      funds land in different ledgers.
    - you only want a price → `quote_swap` (free).

    Use this tool when order_executor cannot express what you need: a pool-scoped
    connector the executor does not route to, or a one-off swap outside any strategy.

    Returns a transaction hash; resolve it with `get_swap_status`.

    Args:
        connector: Connector in "name/type" form, e.g. 'jupiter/router', 'meteora/amm',
            'raydium/clmm'.
        network: Network ID in 'chain-network' format, e.g. 'solana-mainnet-beta',
            'ethereum-mainnet'.
        trading_pair: Trading pair as 'BASE-QUOTE'; either side may be a SYMBOL or a raw
            TOKEN ADDRESS (see `quote_swap`).
        side: 'BUY' (buy base with quote) or 'SELL' (sell base for quote).
        amount: Base token amount to buy or sell.
        slippage_pct: Maximum slippage percentage. OMIT to use the connector's configured
            slippage; '0' is a real value, not "use the default".
        wallet_address: Wallet to sign with (optional, uses the default wallet).
        extra_params: Connector-specific params under Gateway's own names. Supported:
            'approximateIfNoExactOut' (bool, jupiter/dflow/okx/titan routers).
    """
    request = GatewaySwapRequest(
        action="execute",
        connector=connector,
        network=network,
        trading_pair=trading_pair,
        side=side,
        amount=amount,
        slippage_pct=slippage_pct,
        wallet_address=wallet_address,
        extra_params=extra_params,
    )

    client = await hummingbot_client.get_client()
    result = await manage_gateway_swaps_impl(client, request)
    return format_gateway_swap_result("execute", result)


@mcp.tool()
@handle_errors("get Gateway swap status", GATEWAY_LOG_HINT)
async def get_swap_status(transaction_hash: str) -> str:
    """Resolve a submitted swap by its transaction hash.

    Use this tool when: `execute_swap` returned a hash and you need to know whether the
    swap confirmed, is still pending, or failed.

    Args:
        transaction_hash: Transaction hash returned by `execute_swap`.
    """
    request = GatewaySwapRequest(action="get_status", transaction_hash=transaction_hash)

    client = await hummingbot_client.get_client()
    result = await manage_gateway_swaps_impl(client, request)
    return format_gateway_swap_result("get_status", result)


@mcp.tool()
@handle_errors("search Gateway swaps", GATEWAY_LOG_HINT)
async def search_swaps(
    connector: str | None = None,
    network: str | None = None,
    wallet_address: str | None = None,
    trading_pair: str | None = None,
    status: Literal["SUBMITTED", "CONFIRMED", "FAILED"] | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """Query swap history with filters. Reads only — every argument is a filter.

    Use this tool when: you want to see what was swapped, by whom and when. Omitting a
    filter leaves that dimension unrestricted.

    Args:
        connector: Filter by connector.
        network: Filter by network.
        wallet_address: Filter by wallet address.
        trading_pair: Filter by trading pair.
        status: Filter by status: SUBMITTED | CONFIRMED | FAILED.
        start_time: Start timestamp in unix seconds.
        end_time: End timestamp in unix seconds.
        limit: Max results (default 50, max 1000).
        offset: Pagination offset (default 0).
    """
    request = GatewaySwapRequest(
        action="search",
        connector=connector,
        network=network,
        wallet_address=wallet_address,
        trading_pair=trading_pair,
        status=status,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        offset=offset,
    )

    client = await hummingbot_client.get_client()
    result = await manage_gateway_swaps_impl(client, request)
    return format_gateway_swap_result("search", result)


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
