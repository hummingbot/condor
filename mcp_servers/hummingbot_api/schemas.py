"""
Centralized request schemas for Hummingbot MCP tools.

This module contains all Pydantic request models used by the MCP tools.
Centralizing these models makes it easier to maintain consistent validation
and documentation across the codebase.
"""
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from mcp_servers.hummingbot_api.settings import settings


# ==============================================================================
# Account Management Schemas
# ==============================================================================


class SetupConnectorRequest(BaseModel):
    """Request model for setting up exchange connectors with progressive disclosure.

    This model supports setup and delete flows:

    Setup flow (action=None or action="setup"):
    1. No parameters -> List available exchanges
    2. Connector only -> Show required credential fields
    3. Connector + credentials, no account -> Select account from available accounts
    4. All parameters -> Connect the exchange (with override confirmation if needed)

    Delete flow (action="delete"):
    1. action="delete" only -> List accounts and their configured connectors
    2. action="delete" + connector -> Show which accounts have this connector
    3. action="delete" + connector + account -> Delete the credential
    """

    action: Literal["setup", "delete"] | None = Field(
        default=None,
        description="Action to perform. 'setup' (default) to add/update credentials, 'delete' to remove credentials.",
    )

    account: str | None = Field(
        default=None, description="Account name to add credentials to. If not provided, uses the default account."
    )

    connector: str | None = Field(
        default=None,
        description="Exchange connector name (e.g., 'binance', 'coinbase_pro'). Leave empty to list available connectors.",
        examples=["binance", "coinbase_pro", "kraken", "gate_io"],
    )

    credentials: dict[str, Any] | None = Field(
        default=None,
        description="Credentials object with required fields for the connector. Leave empty to see required fields first.",
        examples=[
            {"binance_api_key": "your_api_key", "binance_secret_key": "your_secret"},
            {
                "coinbase_pro_api_key": "your_key",
                "coinbase_pro_secret_key": "your_secret",
                "coinbase_pro_passphrase": "your_passphrase",
            },
        ],
    )

    confirm_override: bool | None = Field(
        default=None,
        description="Explicit confirmation to override existing connector. Required when connector already exists.",
    )

    @field_validator("connector")
    @classmethod
    def validate_connector_name(cls, v: str | None) -> str | None:
        """Validate connector name format if provided"""
        if v is not None:
            # Convert to lowercase and replace spaces/hyphens with underscores
            v = v.lower().replace(" ", "_").replace("-", "_")

            # Basic validation - should be alphanumeric with underscores
            if not v.replace("_", "").isalnum():
                raise ValueError("Connector name should contain only letters, numbers, and underscores")

        return v

    @field_validator("credentials")
    @classmethod
    def validate_credentials(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        """Validate credentials format if provided"""
        if v is not None:
            if not isinstance(v, dict):
                raise ValueError("Credentials must be a dictionary/object")

            if not v:  # Empty dict
                raise ValueError("Credentials cannot be empty. Omit the field to see required fields.")

            # Check that all values are strings (typical for API credentials)
            # except for force_override which can be boolean
            for key, value in v.items():
                if key == "force_override":
                    if not isinstance(value, bool):
                        raise ValueError("'force_override' must be a boolean (true/false)")
                else:
                    if not isinstance(value, str):
                        raise ValueError(f"Credential '{key}' must be a string")
                    if not value.strip():  # Empty or whitespace-only
                        raise ValueError(f"Credential '{key}' cannot be empty")

        return v

    def get_account_name(self) -> str:
        """Get account name with fallback to default"""
        return self.account or settings.default_account

    def get_flow_stage(self) -> str:
        """Determine which stage of the setup/delete flow we're in"""

        if self.action == "delete":
            if self.connector is None:
                return "delete_list"
            elif self.account is None:
                return "delete_select_account"
            else:
                return "delete"

        if self.connector is None:
            return "list_exchanges"
        elif self.credentials is None:
            return "show_config"
        elif self.account is None:
            return "select_account"
        else:
            return "connect"

    def requires_override_confirmation(self) -> bool:
        """Check if this request needs override confirmation"""
        return self.credentials is not None and self.confirm_override is None


# ==============================================================================
# Executor Management Schemas
# ==============================================================================


class ManageExecutorsRequest(BaseModel):
    """Request model for managing executors with progressive disclosure.

    Progressive Flow Stages:
    1. No params -> List available executor types with descriptions
    2. executor_type only -> Show config schema with user defaults applied
    3. action="create" + executor_config -> Create executor (merged with defaults)
    4. action="search" -> Search/list executors (or get detail if executor_id provided)
    5. action="stop" + executor_id -> Stop executor
    6. action="get_logs" + executor_id -> Get executor logs
    7. action="get_preferences" -> View saved preferences
    8. action="save_preferences" + preferences_content -> Save preferences file
    9. action="reset_preferences" -> Reset preferences to defaults
    10. action="positions_summary" -> Get positions (or specific if connector_name+trading_pair given)
    11. action="clear_position" + connector_name + trading_pair -> Clear position
    """

    action: Literal["create", "search", "stop", "get_logs", "get_preferences", "save_preferences", "reset_preferences", "positions_summary", "clear_position", "performance_report"] | None = Field(
        default=None,
        description="Action to perform. Leave empty to see executor types or show schema.",
    )

    executor_type: str | None = Field(
        default=None,
        description="Type of executor (e.g., 'position_executor', 'dca_executor'). Leave empty to list types.",
    )

    executor_config: dict[str, Any] | None = Field(
        default=None,
        description="Configuration for creating an executor. Required for 'create' action.",
    )

    executor_id: str | None = Field(
        default=None,
        description="Executor ID for 'get' or 'stop' actions.",
    )

    # Log options
    log_level: str | None = Field(
        default=None,
        description="Filter logs by level (ERROR, WARNING, INFO, DEBUG). Only for 'get_logs' action.",
    )

    # Search filters
    account_names: list[str] | None = Field(
        default=None,
        description="Filter by account names.",
    )

    connector_names: list[str] | None = Field(
        default=None,
        description="Filter by connector names.",
    )

    trading_pairs: list[str] | None = Field(
        default=None,
        description="Filter by trading pairs.",
    )

    executor_types: list[str] | None = Field(
        default=None,
        description="Filter by executor types for search.",
    )

    status: str | None = Field(
        default=None,
        description="Filter by status (e.g., 'RUNNING', 'TERMINATED').",
    )

    # Pagination
    cursor: str | None = Field(
        default=None,
        description="Pagination cursor for search results.",
    )

    limit: int = Field(
        default=50,
        description="Maximum number of results to return.",
        ge=1,
        le=1000,
    )

    # Stop options
    keep_position: bool = Field(
        default=False,
        description="When stopping, keep the position open instead of closing it.",
    )

    # Preferences
    save_as_default: bool = Field(
        default=False,
        description="Save the executor_config as default for this executor_type.",
    )

    preferences_content: str | None = Field(
        default=None,
        description="Complete markdown content for the preferences file. Used with 'save_preferences' action.",
    )

    account_name: str | None = Field(
        default=None,
        description="Account name for creating executors. Defaults to 'master_account'.",
    )

    # Position management fields (for positions_summary, get_position, clear_position)
    connector_name: str | None = Field(
        default=None,
        description="Connector name for position filtering or clearing.",
    )

    trading_pair: str | None = Field(
        default=None,
        description="Trading pair for position filtering or clearing.",
    )

    controller_id: str | None = Field(
        default=None,
        description="Controller ID that owns this executor. Used for create, positions_summary, get_position, clear_position, and performance_report.",
    )

    controller_ids: list[str] | None = Field(
        default=None,
        description="Filter by controller IDs (for search).",
    )

    @field_validator("executor_type")
    @classmethod
    def validate_executor_type(cls, v: str | None) -> str | None:
        """Validate executor type format if provided."""
        if v is not None:
            v = v.lower().replace(" ", "_").replace("-", "_")
        return v

    def get_flow_stage(self) -> str:
        """Determine which stage of the flow we're in."""
        if self.action == "get_preferences":
            return "get_preferences"
        elif self.action == "save_preferences" and self.preferences_content:
            return "save_preferences"
        elif self.action == "reset_preferences":
            return "reset_preferences"
        elif self.action == "search":
            return "search"
        elif self.action == "stop" and self.executor_id:
            return "stop"
        elif self.action == "get_logs" and self.executor_id:
            return "get_logs"
        elif self.action == "create" and self.executor_config:
            return "create"
        elif self.action == "positions_summary":
            return "positions_summary"
        elif self.action == "clear_position" and self.connector_name and self.trading_pair:
            return "clear_position"
        elif self.action == "performance_report":
            return "performance_report"
        elif self.executor_type is not None:
            return "show_schema"
        else:
            return "list_types"


# ==============================================================================
# Gateway Management Schemas
# ==============================================================================


class GatewayContainerRequest(BaseModel):
    """Request model for Gateway container management with progressive disclosure.

    This model supports container lifecycle management:
    - get_status: Check if Gateway is running and get container details
    - start: Start Gateway container with configuration
    - stop: Stop Gateway container
    - restart: Restart Gateway (optionally with new configuration)
    - get_logs: Retrieve Gateway container logs
    """

    action: Literal["get_status", "start", "stop", "restart", "get_logs"] = Field(
        description="Action to perform on Gateway container"
    )

    config: dict[str, Any] | None = Field(
        default=None,
        description="Gateway configuration (required for 'start', optional for 'restart'). "
                    "Required fields: passphrase (Gateway passphrase), image (Docker image). "
                    "Optional fields: port (exposed port, default: 15888), environment (env vars)",
        examples=[
            {
                "passphrase": "your_secure_passphrase",
                "image": "hummingbot/gateway:latest",
                "port": 15888,
                "environment": {
                    "GATEWAY_PASSPHRASE": "your_secure_passphrase"
                }
            }
        ]
    )

    tail: int | None = Field(
        default=100,
        ge=1,
        le=200,
        description="Number of log lines to retrieve (only for 'get_logs' action, default: 100, max: 200)"
    )


class GatewayConfigRequest(BaseModel):
    """Request model for Gateway configuration management.

    This model handles all Gateway configuration operations:

    Resource Types:
    - chains: Blockchain chains (get all chains)
    - networks: Network configurations (list, get, update) - format: 'chain-network'
    - tokens: Token configurations (list, add, delete) per network
    - connectors: DEX connector configurations (list, get, update)
    - pools: Liquidity pools (list, add) per connector/network
    - wallets: Wallet management (add, delete) for blockchain chains

    Actions:
    - list: List available resources
    - get: Get specific resource configuration
    - update: Update resource configuration
    - add: Add new resource (tokens, pools, wallets)
    - delete: Delete resource (tokens, wallets)
    """

    resource_type: Literal["chains", "networks", "tokens", "connectors", "pools", "wallets"] = Field(
        description="Type of resource to manage"
    )

    action: Literal["list", "get", "update", "add", "delete"] = Field(
        description="Action to perform on the resource"
    )

    # Resource identifiers
    network_id: str | None = Field(
        default=None,
        description="Network ID in format 'chain-network' (e.g., 'solana-mainnet-beta', 'ethereum-mainnet'). "
                    "Required for network operations and token operations",
        examples=["solana-mainnet-beta", "ethereum-mainnet", "polygon-mainnet"]
    )

    connector_name: str | None = Field(
        default=None,
        description="DEX connector name (e.g., 'meteora', 'raydium', 'uniswap'). "
                    "Required for connector operations and pool list operations",
        examples=["meteora", "raydium", "uniswap", "pancakeswap"]
    )

    # Configuration data
    config_updates: dict[str, Any] | None = Field(
        default=None,
        description="Configuration updates as key-value pairs. "
                    "Keys can be in snake_case or camelCase. "
                    "Required for 'update' action",
        examples=[
            {"slippage_pct": 0.5, "timeout": 30000},
            {"node_url": "https://api.mainnet-beta.solana.com"}
        ]
    )

    # Token-specific fields
    token_address: str | None = Field(
        default=None,
        description="Token contract address. Required for 'add' and 'delete' token actions",
        examples=[
            "9QFfgxdSqH5zT7j6rZb1y6SZhw2aFtcQu2r6BuYpump",  # Solana
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"    # Ethereum
        ]
    )

    token_symbol: str | None = Field(
        default=None,
        description="Token symbol (e.g., 'USDC', 'GOLD'). Required for 'add' token action",
        examples=["USDC", "WETH", "GOLD"]
    )

    token_decimals: int | None = Field(
        default=None,
        ge=0,
        le=18,
        description="Token decimals (e.g., 6 for USDC, 18 for WETH). Required for 'add' token action"
    )

    token_name: str | None = Field(
        default=None,
        description="Token name (optional for 'add' token action, defaults to symbol if not provided)"
    )

    # Pool-specific fields
    pool_type: str | None = Field(
        default=None,
        description="Pool type. Required for 'add' pool action",
        examples=["CLMM", "AMM"]
    )

    pool_base: str | None = Field(
        default=None,
        description="Base token symbol for pool. Required for 'add' pool action"
    )

    pool_quote: str | None = Field(
        default=None,
        description="Quote token symbol for pool. Required for 'add' pool action"
    )

    pool_address: str | None = Field(
        default=None,
        description="Pool contract address. Required for 'add' pool action"
    )

    # Search/filter fields
    search: str | None = Field(
        default=None,
        description="Search term to filter tokens by symbol or name (only for 'list' tokens action)"
    )

    network: str | None = Field(
        default=None,
        description="Network name (e.g., 'mainnet-beta'). Required for 'list' pools action"
    )

    # Wallet-specific fields
    chain: str | None = Field(
        default=None,
        description="Blockchain chain for wallet (e.g., 'solana', 'ethereum'). Required for wallet operations",
        examples=["solana", "ethereum", "avalanche", "polygon"]
    )

    private_key: str | None = Field(
        default=None,
        description="Private key for wallet. Required for 'add' wallet action"
    )

    wallet_address: str | None = Field(
        default=None,
        description="Wallet address. Required for 'delete' wallet action"
    )


# ==============================================================================
# Gateway Swap Schemas
# ==============================================================================


class GatewaySwapRequest(BaseModel):
    """Request model for Gateway swap operations with progressive disclosure.

    This model supports swap operations:
    - quote: Get price quote for a swap
    - execute: Execute a swap transaction
    - search: Search swap history with filters
    - get_status: Get status of a specific swap by transaction hash

    Progressive Flow:
    1. action="quote" -> Get price quote before executing
    2. action="execute" -> Execute the swap
    3. action="get_status" + tx_hash -> Check transaction status
    4. action="search" + filters -> Query swap history
    """

    action: Literal["quote", "execute", "search", "get_status"] = Field(
        description="Action to perform: 'quote' (get price), 'execute' (perform swap), "
                    "'search' (query history), 'get_status' (check tx status)"
    )

    # Common swap parameters (required for quote/execute)
    connector: str | None = Field(
        default=None,
        description="DEX router connector (required for quote/execute). "
                    "Examples: 'jupiter' (Solana), '0x' (Ethereum)"
    )

    network: str | None = Field(
        default=None,
        description="Network ID in 'chain-network' format (required for quote/execute). "
                    "Examples: 'solana-mainnet-beta', 'ethereum-mainnet', 'ethereum-base'"
    )

    trading_pair: str | None = Field(
        default=None,
        description="Trading pair in BASE-QUOTE format (required for quote/execute). "
                    "Supports both token symbols and token addresses. "
                    "Examples: 'SOL-USDC', 'ETH-USDT', 'TOKEN_ADDRESS_1-TOKEN_ADDRESS_2', 'TOKEN_ADDRESS_1-USDC'"
    )

    side: Literal["BUY", "SELL"] | None = Field(
        default=None,
        description="Trade side (required for quote/execute): "
                    "'BUY' (buy base with quote) or 'SELL' (sell base for quote)"
    )

    amount: str | None = Field(
        default=None,
        description="Amount to swap (required for quote/execute). "
                    "For BUY: base token amount to receive. For SELL: base token amount to sell. "
                    "Example: '1.5' to buy/sell 1.5 tokens"
    )

    slippage_pct: str | None = Field(
        default="1.0",
        description="Maximum slippage percentage (optional, default: 1.0). "
                    "Example: '1.5' for 1.5% slippage tolerance"
    )

    # Execute-specific parameter
    wallet_address: str | None = Field(
        default=None,
        description="Wallet address for execute action (optional, uses default wallet if not provided)"
    )

    # Get status parameter
    transaction_hash: str | None = Field(
        default=None,
        description="Transaction hash (required for get_status action)"
    )

    # Search parameters (all optional)
    search_connector: str | None = Field(
        default=None,
        description="Filter by connector for search action (e.g., 'jupiter')"
    )

    search_network: str | None = Field(
        default=None,
        description="Filter by network for search action (e.g., 'solana-mainnet-beta')"
    )

    search_wallet_address: str | None = Field(
        default=None,
        description="Filter by wallet address for search action"
    )

    search_trading_pair: str | None = Field(
        default=None,
        description="Filter by trading pair for search action. Supports symbols and addresses "
                    "(e.g., 'SOL-USDC', 'TOKEN_ADDRESS_1-TOKEN_ADDRESS_2')"
    )

    status: Literal["SUBMITTED", "CONFIRMED", "FAILED"] | None = Field(
        default=None,
        description="Filter by transaction status for search action"
    )

    start_time: int | None = Field(
        default=None,
        description="Start timestamp in unix seconds for search action"
    )

    end_time: int | None = Field(
        default=None,
        description="End timestamp in unix seconds for search action"
    )

    limit: int | None = Field(
        default=50,
        ge=1,
        le=1000,
        description="Maximum number of results for search action (default: 50, max: 1000)"
    )

    offset: int | None = Field(
        default=0,
        ge=0,
        description="Pagination offset for search action (default: 0)"
    )


# ==============================================================================
# Gateway CLMM Schemas
# ==============================================================================


class GatewayCLMMRequest(BaseModel):
    """Request model for Gateway CLMM pool discovery.

    This model supports DeFi data exploration:

    Pool Exploration:
    - list_pools: Get list of available CLMM pools with filtering/sorting
    - get_pool_info: Get detailed information about a specific pool

    To manage LP positions, use `manage_executors` with `lp_executor` type.
    To check on-chain positions, use `get_portfolio_overview` with `include_lp_positions=True`.
    """

    action: Literal["list_pools", "get_pool_info"] = Field(
        description="Action to perform on CLMM pools"
    )

    # Common parameters
    connector: str | None = Field(
        default=None,
        description="CLMM connector name (required). Examples: 'meteora', 'raydium', 'uniswap'"
    )

    network: str | None = Field(
        default=None,
        description="Network ID in 'chain-network' format (required for get_pool_info). Examples: 'solana-mainnet-beta', 'ethereum-mainnet'"
    )

    pool_address: str | None = Field(
        default=None,
        description="Pool contract address (required for get_pool_info)"
    )

    # Pool listing parameters
    page: int = Field(
        default=0,
        ge=0,
        description="Page number for list_pools (default: 0)"
    )

    limit: int = Field(
        default=50,
        ge=1,
        le=100,
        description="Results per page for list_pools (default: 50, max: 100)"
    )

    search_term: str | None = Field(
        default=None,
        description="Search term to filter pools by token symbols (e.g., 'SOL', 'USDC')"
    )

    sort_key: str | None = Field(
        default="volume",
        description="Sort by field (volume, tvl, feetvlratio, etc.)"
    )

    order_by: str | None = Field(
        default="desc",
        description="Sort order: 'asc' or 'desc'"
    )

    include_unknown: bool = Field(
        default=True,
        description="Include pools with unverified tokens (default: True)"
    )

    detailed: bool = Field(
        default=False,
        description="Return detailed table with more columns (default: False)"
    )
