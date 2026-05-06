from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


# ── Auth ──


class LoginRequest(BaseModel):
    id: int
    first_name: str
    last_name: str = ""
    username: str = ""
    photo_url: str = ""
    auth_date: int
    hash: str


class LoginResponse(BaseModel):
    token: str
    user: WebUser


class WebUser(BaseModel):
    id: int
    username: str = ""
    first_name: str = ""
    role: str  # "admin" | "user"


# ── Servers ──


class ServerInfo(BaseModel):
    name: str
    host: str
    port: int
    online: bool = False
    permission: str = "trader"


# ── Portfolio ──


class BalanceItem(BaseModel):
    token: str
    total: float
    available: float
    usd_value: float = 0.0


class ConnectorBalance(BaseModel):
    connector: str
    balances: list[BalanceItem]
    total_usd: float = 0.0


class PortfolioResponse(BaseModel):
    server: str
    connectors: list[ConnectorBalance]
    total_usd: float = 0.0


class PortfolioHistoryPoint(BaseModel):
    timestamp: float
    total_usd: float = 0.0
    tokens: dict[str, float] = {}


class PortfolioHistoryResponse(BaseModel):
    server: str
    points: list[PortfolioHistoryPoint]
    interval: str
    top_tokens: list[str] = []


# ── Bots ──


class BotInfo(BaseModel):
    id: str
    name: str
    status: str
    connector: str = ""
    trading_pair: str = ""
    pnl: float = 0.0
    uptime: float = 0.0
    controller_type: str = ""


class BotDetailResponse(BaseModel):
    bot: BotInfo
    config: dict[str, Any] = {}
    performance: dict[str, Any] = {}


class ControllerInfo(BaseModel):
    controller_name: str
    controller_id: str = ""
    bot_name: str
    status: str = "unknown"
    connector: str = ""
    trading_pair: str = ""
    realized_pnl_quote: float = 0.0
    unrealized_pnl_quote: float = 0.0
    global_pnl_quote: float = 0.0
    global_pnl_pct: float = 0.0
    volume_traded: float = 0.0
    close_type_counts: dict[str, int] = {}
    positions_summary: list[dict[str, Any]] = []
    deployed_at: Optional[str] = None
    config: dict[str, Any] = {}


class BotSummary(BaseModel):
    bot_name: str
    status: str = "unknown"
    num_controllers: int = 0
    error_count: int = 0
    deployed_at: Optional[str] = None
    error_logs: list[dict[str, Any]] = []
    general_logs: list[dict[str, Any]] = []


class BotsPageResponse(BaseModel):
    controllers: list[ControllerInfo] = []
    bots: list[BotSummary] = []
    total_pnl: float = 0.0
    total_volume: float = 0.0
    server_online: bool = True
    error_hint: Optional[str] = None


# ── Executors ──


class CreateExecutorRequest(BaseModel):
    executor_type: str
    config: dict[str, Any]
    account_name: str = "master_account"
    controller_id: str = "main"


class ExecutorInfo(BaseModel):
    id: str
    type: str
    connector: str
    trading_pair: str
    side: str = ""
    status: str = ""
    close_type: str = ""
    pnl: float = 0.0
    volume: float = 0.0
    timestamp: float = 0.0
    controller_id: str = ""
    cum_fees_quote: float = 0.0
    net_pnl_pct: float = 0.0
    entry_price: float = 0.0
    current_price: float = 0.0
    close_timestamp: float = 0.0
    custom_info: dict[str, Any] = {}
    config: dict[str, Any] = {}


# ── Market Data ──


class CandleData(BaseModel):
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketPriceResponse(BaseModel):
    connector: str
    trading_pair: str
    mid_price: float
    best_bid: float = 0.0
    best_ask: float = 0.0


class OrderBookLevel(BaseModel):
    price: float
    amount: float


class OrderBookResponse(BaseModel):
    connector: str
    trading_pair: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]


class TradingRuleItem(BaseModel):
    trading_pair: str
    min_order_size: float = 0.0
    min_notional_size: float = 0.0
    min_price_increment: float = 0.0
    min_base_amount_increment: float = 0.0


class TradingRulesResponse(BaseModel):
    connector: str
    rules: list[TradingRuleItem]


# ── Deploy Bot ──


class ControllerConfigSummary(BaseModel):
    id: str
    controller_name: str
    controller_type: str
    connector_name: str = ""
    trading_pair: str = ""


class AvailableControllersResponse(BaseModel):
    configs: list[ControllerConfigSummary]
    controller_types: dict[str, list[str]]


class ControllerConfigDetail(BaseModel):
    id: str
    controller_name: str
    controller_type: str
    config: dict[str, Any]


class ControllerSourceResponse(BaseModel):
    controller_name: str
    controller_type: str
    source: str


class DeployBotRequest(BaseModel):
    bot_name: str
    controllers_config: list[str]
    account_name: str = "master_account"
    image: str = "hummingbot/hummingbot:latest"
    max_global_drawdown_quote: float | None = None
    max_controller_drawdown_quote: float | None = None


# ── Archived Bots ──


class ArchivedBotSummary(BaseModel):
    bot_name: str
    db_path: str
    total_trades: int = 0
    total_orders: int = 0
    trading_pairs: list[str] = []
    exchanges: list[str] = []
    start_time: Optional[float] = None
    end_time: Optional[float] = None


class PnlPoint(BaseModel):
    timestamp: float
    pnl: float


class NormalizedExecutor(BaseModel):
    id: str = ""
    type: str = ""
    connector: str = ""
    trading_pair: str = ""
    side: str = ""
    status: str = ""
    close_type: str = ""
    pnl: float = 0.0
    volume: float = 0.0
    timestamp: float = 0.0
    close_timestamp: float = 0.0
    entry_price: float = 0.0
    current_price: float = 0.0
    cum_fees_quote: float = 0.0
    net_pnl_pct: float = 0.0
    controller_id: str = ""
    custom_info: dict[str, Any] = {}
    config: dict[str, Any] = {}


class ArchivedBotPerformance(BaseModel):
    bot_name: str
    db_path: str
    total_pnl: float = 0.0
    total_fees: float = 0.0
    total_volume: float = 0.0
    trade_count: int = 0
    buy_count: int = 0
    sell_count: int = 0
    pnl_by_pair: dict[str, float] = {}
    cumulative_pnl: list[PnlPoint] = []
    trading_pairs: list[str] = []
    exchanges: list[str] = []
    executors: list[NormalizedExecutor] = []
    primary_connector: str = ""
    primary_trading_pair: str = ""
    executor_count: int = 0


class PaginatedExecutors(BaseModel):
    executors: list[NormalizedExecutor]
    total: int
    offset: int
    limit: int


# ── Reports ──


class ReportSummary(BaseModel):
    id: str
    title: str
    filename: str
    created_at: str
    source_type: str = ""
    source_name: str = ""
    tags: list[str] = []


class ReportsListResponse(BaseModel):
    reports: list[ReportSummary]
    total: int


# ── Settings ──


class AddServerRequest(BaseModel):
    name: str
    host: str
    port: int
    username: str
    password: str


class UpdateServerRequest(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None


class GatewayStartRequest(BaseModel):
    image: str = "hummingbot/gateway:latest"
    passphrase: str
    port: int = 15888
    dev_mode: bool = True


class CredentialInfo(BaseModel):
    connector_name: str
    connector_type: str = ""


class AddCredentialRequest(BaseModel):
    connector_name: str
    credentials: dict[str, Any]
