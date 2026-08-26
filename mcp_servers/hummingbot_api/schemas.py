"""
Centralized request schemas for Hummingbot MCP tools.

This module contains all Pydantic request models used by the MCP tools.
Centralizing these models makes it easier to maintain consistent validation
and documentation across the codebase.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

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
    12. action="orphaned" -> List terminated executors that may still own an on-chain position
    13. action="resolve_orphan" + executor_id -> Mark an orphaned position as recovered
    """

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
    ) = Field(
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
        elif (
            self.action == "clear_position"
            and self.connector_name
            and self.trading_pair
        ):
            return "clear_position"
        elif self.action == "performance_report":
            return "performance_report"
        elif self.action == "orphaned":
            return "orphaned"
        elif self.action == "resolve_orphan":
            return "resolve_orphan"
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
        description="Gateway configuration (used for 'start', optional for 'restart'). "
        "The Hummingbot API runs Gateway secured (TLS + mTLS) and manages the "
        "certificates/passphrase itself using its own CONFIG_PASSWORD (hummingbot-api "
        "SEC-048), so no passphrase is needed here. "
        "Fields: image (Docker image, default: hummingbot/gateway:development), "
        "port (exposed port, default: 15888).",
        examples=[
            {
                "image": "hummingbot/gateway:development",
                "port": 15888,
            }
        ],
    )

    tail: int | None = Field(
        default=100,
        ge=1,
        le=200,
        description="Number of log lines to retrieve (only for 'get_logs' action, default: 100, max: 200)",
    )


class GatewayConfigRequest(BaseModel):
    """Request model for Gateway configuration management.

    This model handles all Gateway configuration operations:

    Resource Types:
    - chains: Blockchain chains (get all chains)
    - networks: Network configurations (list, get, update) - format: 'chain-network'
    - tokens: Token configurations (list, add, delete, save) per network
    - connectors: DEX connector configurations (list, get, update)
    - pools: Liquidity pools (list, add, delete, save) per connector/network
    - wallets: Configured wallets per chain (list only — a wallet is added or
      removed in the Condor dashboard, never through an agent)

    Actions:
    - list: List available resources
    - get: Get specific resource configuration
    - update: Update resource configuration
    - add: Add new resource (tokens, pools) - requires full details
    - delete: Delete resource (tokens, pools)
    - save: Save resource by address only (tokens, pools) - auto-fetches details
    """

    resource_type: Literal[
        "chains", "networks", "tokens", "connectors", "pools", "wallets"
    ] = Field(description="Type of resource to manage")

    action: Literal["list", "get", "update", "add", "delete", "save"] = Field(
        description="Action to perform on the resource"
    )

    # Resource identifiers
    network_id: str | None = Field(
        default=None,
        description="Network ID in format 'chain-network' (e.g., 'solana-mainnet-beta', 'ethereum-mainnet'). "
        "Required for network operations and token operations",
        examples=["solana-mainnet-beta", "ethereum-mainnet", "polygon-mainnet"],
    )

    connector_name: str | None = Field(
        default=None,
        description="DEX connector name (e.g., 'meteora', 'raydium', 'orca', 'uniswap'). "
        "Required for connector operations and pool list operations",
        examples=["meteora", "raydium", "orca", "uniswap", "pancakeswap"],
    )

    # Configuration data
    config_updates: dict[str, Any] | None = Field(
        default=None,
        description="Configuration updates as key-value pairs. "
        "Keys can be in snake_case or camelCase. "
        "Required for 'update' action",
        examples=[
            {"slippage_pct": 0.5, "timeout": 30000},
            {"node_url": "https://api.mainnet-beta.solana.com"},
        ],
    )

    # Token-specific fields
    token_address: str | None = Field(
        default=None,
        description="Token contract address. Required for 'add' and 'delete' token actions",
        examples=[
            "9QFfgxdSqH5zT7j6rZb1y6SZhw2aFtcQu2r6BuYpump",  # Solana
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # Ethereum
        ],
    )

    token_symbol: str | None = Field(
        default=None,
        description="Token symbol (e.g., 'USDC', 'GOLD'). Required for 'add' token action",
        examples=["USDC", "WETH", "GOLD"],
    )

    token_decimals: int | None = Field(
        default=None,
        ge=0,
        le=18,
        description="Token decimals (e.g., 6 for USDC, 18 for WETH). Required for 'add' token action",
    )

    token_name: str | None = Field(
        default=None,
        description="Token name (optional for 'add' token action, defaults to symbol if not provided)",
    )

    # Pool-specific fields
    pool_type: str | None = Field(
        default=None,
        description="Pool type. Required for 'add' pool action",
        examples=["CLMM", "AMM"],
    )

    pool_base: str | None = Field(
        default=None,
        description="Base token symbol for pool. Required for 'add' pool action",
    )

    pool_quote: str | None = Field(
        default=None,
        description="Quote token symbol for pool. Required for 'add' pool action",
    )

    pool_address: str | None = Field(
        default=None,
        description="Pool contract address. Required for 'add' pool action",
    )

    # Search/filter fields
    search: str | None = Field(
        default=None,
        description="Search term to filter tokens by symbol or name (only for 'list' tokens action)",
    )

    network: str | None = Field(
        default=None,
        description="Network name (e.g., 'mainnet-beta'). Required for 'list' pools action",
    )

    # Wallet-specific fields
    chain: str | None = Field(
        default=None,
        description="Blockchain chain to filter wallets by (e.g., 'solana', 'ethereum')",
        examples=["solana", "ethereum", "avalanche", "polygon"],
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

    action: Literal["quote", "execute", "execute_quote", "search", "get_status"] = (
        Field(
            description="Action to perform: 'quote' (get price), 'execute' (perform swap at the "
            "price found at execution), 'execute_quote' (execute a quote you already have, by its "
            "quote_id — the price you were shown is the price you get), 'search' (query history), "
            "'get_status' (check tx status)"
        )
    )

    # Common swap parameters (required for quote/execute)
    connector: str | None = Field(
        default=None,
        description="DEX router connector (required for quote/execute). "
        "Examples: 'jupiter' (Solana), '0x' (Ethereum)",
    )

    network: str | None = Field(
        default=None,
        description="Network ID in 'chain-network' format (required for quote/execute). "
        "Examples: 'solana-mainnet-beta', 'ethereum-mainnet', 'ethereum-base'",
    )

    trading_pair: str | None = Field(
        default=None,
        description="Trading pair in BASE-QUOTE format (required for quote/execute). "
        "Supports both token symbols and token addresses. "
        "Examples: 'SOL-USDC', 'ETH-USDT', 'TOKEN_ADDRESS_1-TOKEN_ADDRESS_2', 'TOKEN_ADDRESS_1-USDC'",
    )

    side: Literal["BUY", "SELL"] | None = Field(
        default=None,
        description="Trade side (required for quote/execute): "
        "'BUY' (buy base with quote) or 'SELL' (sell base for quote)",
    )

    amount: str | None = Field(
        default=None,
        description="Amount to swap (required for quote/execute). "
        "For BUY: base token amount to receive. For SELL: base token amount to sell. "
        "Example: '1.5' to buy/sell 1.5 tokens",
    )

    slippage_pct: str | None = Field(
        default=None,
        description="Maximum slippage percentage (optional). "
        "Omit to use the connector's configured slippage. "
        "Example: '1.5' for 1.5% slippage tolerance ('0' is a real value, not 'use default')",
    )

    extra_params: dict[str, Any] | None = Field(
        default=None,
        description="Connector-specific parameters for quote/execute (optional). "
        "Supported key: 'approximateIfNoExactOut' (bool, jupiter/dflow/okx/titan routers only) - "
        "allow an approximate quote when no exact-out route exists",
    )

    # Execute-specific parameter
    wallet_address: str | None = Field(
        default=None,
        description="Wallet address for execute action (optional, uses default wallet if not provided)",
    )

    # Execute-quote parameter
    quote_id: str | None = Field(
        default=None,
        description="quote_id from a prior quote action (required for execute_quote). Only the "
        "router connectors return one — jupiter, dflow, okx, titan, 0x — because only they hold "
        "a price; a pool-scoped connector prices against the pool at execution and has none.",
    )

    # Get status parameter
    transaction_hash: str | None = Field(
        default=None, description="Transaction hash (required for get_status action)"
    )

    # Search parameters (all optional)
    search_connector: str | None = Field(
        default=None,
        description="Filter by connector for search action (e.g., 'jupiter')",
    )

    search_network: str | None = Field(
        default=None,
        description="Filter by network for search action (e.g., 'solana-mainnet-beta')",
    )

    search_wallet_address: str | None = Field(
        default=None, description="Filter by wallet address for search action"
    )

    search_trading_pair: str | None = Field(
        default=None,
        description="Filter by trading pair for search action. Supports symbols and addresses "
        "(e.g., 'SOL-USDC', 'TOKEN_ADDRESS_1-TOKEN_ADDRESS_2')",
    )

    status: Literal["SUBMITTED", "CONFIRMED", "FAILED"] | None = Field(
        default=None, description="Filter by transaction status for search action"
    )

    start_time: int | None = Field(
        default=None, description="Start timestamp in unix seconds for search action"
    )

    end_time: int | None = Field(
        default=None, description="End timestamp in unix seconds for search action"
    )

    limit: int | None = Field(
        default=50,
        ge=1,
        le=1000,
        description="Maximum number of results for search action (default: 50, max: 1000)",
    )

    offset: int | None = Field(
        default=0, ge=0, description="Pagination offset for search action (default: 0)"
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
        description="CLMM connector name (required). Examples: 'meteora', 'raydium', 'orca', 'uniswap', 'pancakeswap'",
    )

    network: str | None = Field(
        default=None,
        description="Network ID in 'chain-network' format (required for get_pool_info). Examples: 'solana-mainnet-beta', 'ethereum-mainnet'",
    )

    pool_address: str | None = Field(
        default=None, description="Pool contract address (required for get_pool_info)"
    )

    bin_count: int = Field(
        default=0,
        ge=0,
        le=401,
        description=(
            "For get_pool_info: if > 0, include the per-tick liquidity distribution "
            "(`bins`) around the active price. Meteora always returns its bins; "
            "orca, raydium, uniswap and pancakeswap compute them on request. "
            "Default 0 skips the extra on-chain reads."
        ),
    )

    # Pool listing parameters
    page: int = Field(
        default=0, ge=0, description="Page number for list_pools (default: 0)"
    )

    limit: int = Field(
        default=50,
        ge=1,
        le=100,
        description="Results per page for list_pools (default: 50, max: 100)",
    )

    search_term: str | None = Field(
        default=None,
        description="Search term to filter pools by token symbols (e.g., 'SOL', 'USDC')",
    )

    sort_key: str | None = Field(
        default="tvl",
        description="Sort by field. Defaults to tvl — depth is the LP question, and "
        "volume ties at zero across every pool on a quiet pair. "
        "meteora: tvl, volume, feetvlratio. "
        "orca: tvl, volume, fees, rewards, yieldovertvl.",
    )

    order_by: str | None = Field(
        default="desc", description="Sort order: 'asc' or 'desc'"
    )

    include_unknown: bool = Field(
        default=True, description="Include pools with unverified tokens (default: True)"
    )

    detailed: bool = Field(
        default=False,
        description="Return detailed table with more columns (default: False)",
    )


# ==============================================================================
# Gateway AMM Schemas (manage_amm)
# ==============================================================================


class AMMRequest(BaseModel):
    """Request model for the direct, chain- & DEX-agnostic AMM tool (manage_amm).

    Drives AMM operations and pool creation across Meteora DAMM v2 (Solana), Raydium CPMM (Solana),
    and Uniswap V2 (EVM). Stateless — the caller/agent holds position state.

    Progressive disclosure: action=None returns the AMM guide + per-connector matrix.

    Meteora DAMM v2 positions are NFTs (a wallet may hold several per pool), so this tool is
    position-addressed: remove_liquidity requires position_address, add_liquidity takes it
    optionally (omit = open a new position), position_info returns a positions[] breakdown, and
    positions_owned lists all of a wallet's positions. Fungible-LP AMMs ignore position_address.
    """

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
    ) = Field(
        default=None,
        description="AMM action. Leave empty to load the AMM guide + param matrix.",
    )

    connector: str | None = Field(
        default=None,
        description="AMM connector (required for any action): 'meteora' (Solana DAMM v2), 'raydium' (Solana CPMM), 'uniswap' (EVM V2)",
    )
    network: str | None = Field(
        default=None,
        description="Network ID in 'chain-network' format, built from Gateway's chain/network "
        "config names. Examples: 'solana-mainnet-beta', 'ethereum-mainnet', 'ethereum-base'",
    )
    wallet_address: str | None = Field(
        default=None,
        description="Wallet address (optional, uses default if not provided)",
    )
    pool_address: str | None = Field(default=None, description="Pool contract address")
    position_address: str | None = Field(
        default=None,
        description="Meteora NFT position: REQUIRED for remove_liquidity, optional for add_liquidity (omit = open a new position). Ignored by fungible-LP AMMs.",
    )

    # Token params
    base_token: str | None = Field(
        default=None, description="Base token symbol or address (create_pool)"
    )
    quote_token: str | None = Field(
        default=None,
        description="Quote token symbol or address (pool quote, for create_pool)",
    )
    slippage_pct: str | None = Field(
        default=None, description="Maximum slippage percentage (as string)"
    )

    # Liquidity params
    base_token_amount: str | None = Field(
        default=None,
        description="Base token amount (add_liquidity / quote_liquidity / create_pool)",
    )
    quote_token_amount: str | None = Field(
        default=None,
        description="Quote token amount (add_liquidity / quote_liquidity / create_pool)",
    )
    percentage_to_remove: str | None = Field(
        default=None,
        description="Percentage of liquidity to remove, 0-100 (remove_liquidity)",
    )

    # create_pool params
    initial_price: str | None = Field(
        default=None,
        description="Initial price as quote per base (create_pool; overrides quote_token_amount)",
    )
    extra_params: dict[str, Any] | None = Field(
        default=None,
        description="Connector-specific create_pool params under Gateway's own names: "
        "configAddress (meteora DAMM v2, required there), ammConfigIndex (raydium CPMM). "
        "uniswap/pancakeswap (EVM) AMM take no extra params. Unknown keys are "
        "rejected by the API with a 400.",
    )


class CLMMRequest(BaseModel):
    """Request model for the direct, chain- & DEX-agnostic CLMM tool (manage_clmm).

    Drives concentrated-liquidity position operations across Meteora DLMM, Raydium CLMM, Orca
    Whirlpools (Solana), and Uniswap V3 / PancakeSwap V3 (EVM).

    Progressive disclosure: action=None returns the CLMM guide.

    This is the direct, unmanaged path. The managed path for normal LP work is
    manage_executors(lp_executor), which owns range monitoring, rebalancing and close retries.
    Use manage_clmm to inspect positions and to recover orphaned ones that no executor owns
    any more — a terminated executor cannot be told to close its position.
    """

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
    ) = Field(
        default=None, description="CLMM action. Leave empty to load the CLMM guide."
    )

    connector: str | None = Field(
        default=None,
        description="CLMM connector (required for any action): 'meteora', 'raydium', 'orca', "
        "'pancakeswap-sol' (Solana), 'uniswap', 'pancakeswap' (EVM). A '<name>/clmm' form is "
        "accepted, so an orphan record's lp_provider (e.g. 'orca/clmm') can be passed through "
        "unchanged.",
    )
    network: str | None = Field(
        default=None,
        description="Network ID in 'chain-network' format, built from Gateway's chain/network "
        "config names. Examples: 'solana-mainnet-beta', 'ethereum-mainnet', 'ethereum-base', "
        "'ethereum-bsc'",
    )
    wallet_address: str | None = Field(
        default=None,
        description="Wallet address (optional, uses default if not provided)",
    )
    pool_address: str | None = Field(
        default=None,
        description="Pool contract address. Required for open. Optional and informational "
        "everywhere else: the API never reads it on close or collect_fees (Gateway needs only "
        "position_address, and the pre-close fee snapshot does not use it), and position_info "
        "lists every position the wallet owns without a pool filter.",
    )
    position_address: str | None = Field(
        default=None,
        description="Position NFT address. Required for add_liquidity, remove_liquidity, close, collect_fees.",
    )

    # Range params (open)
    lower_price: str | None = Field(
        default=None, description="Lower price bound of the position range (open)"
    )
    upper_price: str | None = Field(
        default=None, description="Upper price bound of the position range (open)"
    )

    # Liquidity params
    base_token_amount: str | None = Field(
        default=None, description="Base token amount (open / add_liquidity)"
    )
    quote_token_amount: str | None = Field(
        default=None, description="Quote token amount (open / add_liquidity)"
    )
    percentage_to_remove: str | None = Field(
        default=None,
        description="Percentage of liquidity to remove, 0-100 (remove_liquidity). 100 empties the position but "
        "leaves the account open — use action='close' to withdraw and close it.",
    )
    slippage_pct: str | None = Field(
        default=None, description="Maximum slippage percentage (as string)"
    )

    # create_pool params
    base_token: str | None = Field(
        default=None, description="Base token symbol or address (create_pool)"
    )
    quote_token: str | None = Field(
        default=None, description="Quote token symbol or address (create_pool)"
    )
    initial_price: str | None = Field(
        default=None,
        description="Initial pool price as quote per base (create_pool, optional — "
        "the API fetches the market price when omitted)",
    )
    extra_params: dict[str, Any] | None = Field(
        default=None,
        description="Connector-specific parameters under Gateway's own names. "
        "open/add_liquidity: strategyType (meteora DLMM, e.g. {'strategyType': 0}). "
        "create_pool: binStep (meteora/orca), feeBps (meteora/uniswap/pancakeswap), "
        "ammConfigIndex (raydium/pancakeswap-sol). Unknown keys are rejected by the API with a 400.",
    )
