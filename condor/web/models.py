from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

# ── Auth ──


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
    # The server this user's commands land on when none is named — the same
    # `chat_defaults` entry Telegram reads, so both surfaces agree on it.
    is_default: bool = False


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
    note: str | None = None


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


# ── Bot Runs ──


class BotRunInfo(BaseModel):
    bot_name: str
    bot_run_id: Optional[int] = None
    account_name: str = ""
    strategy_type: str = ""
    strategy_name: str = ""
    run_status: str = ""
    deployment_status: str = ""
    created_at: Optional[str] = None
    stopped_at: Optional[str] = None
    realized_pnl_quote: float = 0.0
    unrealized_pnl_quote: float = 0.0
    global_pnl_quote: float = 0.0
    volume_traded: float = 0.0
    num_controllers: int = 0


class BotRunsResponse(BaseModel):
    runs: list[BotRunInfo] = []
    total: int = 0


# ── Controller Performance ──


class ControllerPerformanceSnapshot(BaseModel):
    timestamp: str = ""
    bot_name: str = ""
    controller_id: str = ""
    controller_name: str = ""
    connector: str = ""
    trading_pair: str = ""
    realized_pnl_quote: float = 0.0
    unrealized_pnl_quote: float = 0.0
    global_pnl_quote: float = 0.0
    global_pnl_pct: float = 0.0
    volume_traded: float = 0.0
    close_type_counts: dict[str, int] = {}
    positions_summary: list[dict[str, Any]] = []
    custom_info: dict[str, Any] = {}


class ControllerPerformanceLatestResponse(BaseModel):
    snapshots: list[ControllerPerformanceSnapshot] = []
    server_online: bool = True
    error_hint: Optional[str] = None


class ControllerPerformanceHistoryResponse(BaseModel):
    snapshots: list[ControllerPerformanceSnapshot] = []
    next_cursor: Optional[str] = None
    interval: str = "5m"
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

    @classmethod
    def from_raw(cls, ex: Any) -> Optional["ExecutorInfo"]:
        """Build the wire model from one raw executor dict, or ``None``.

        The transform itself lives in
        :func:`condor.fetchers.executors.build_executor_row` -- shared with the
        agents rollup so a row cannot mean two different things depending on
        which layer built it. This method is only the renaming: the wire calls
        the pair ``trading_pair`` and the fees ``cum_fees_quote``, spells
        ``status``/``close_type`` lowercase, and reports ``type`` as the
        normalized label (``position``, ``grid``) rather than the API's raw
        class name.

        Both REST responses and WS broadcasts go through here, so the executors
        tab renders the same row however it arrived.
        """
        if not isinstance(ex, dict):
            return None

        from condor.fetchers.executors import build_executor_row, get_executor_type

        row = build_executor_row(ex)
        return cls(
            id=row["id"],
            type=get_executor_type(ex),
            connector=row["connector"],
            trading_pair=row["pair"],
            side=row["side"],
            status=row["status"].lower(),
            close_type=row["close_type"].lower(),
            pnl=row["pnl"],
            volume=row["volume"],
            timestamp=row["timestamp"],
            controller_id=row["controller_id"],
            cum_fees_quote=row["fees"],
            net_pnl_pct=float(ex.get("net_pnl_pct") or 0),
            entry_price=row["entry_price"],
            current_price=row["current_price"],
            close_timestamp=row["close_timestamp"],
            custom_info=row["custom_info"],
            config=row["config"],
        )


class ExecutorPeriodSummary(BaseModel):
    """Executor totals over a time window, computed across the whole history.

    ``pnl`` and ``volume`` are USD-denominated (the repo convention for anything
    aggregated across trading pairs); the dashboard converts once into whatever
    display currency the user picked. ``converted`` is False when at least one
    quote asset in the window had no path to USD, so its rows are counted in
    their own quote and the totals are approximate.
    """

    period: str
    pnl: float = 0.0
    volume: float = 0.0
    count: int = 0
    converted: bool = True


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


class TickerItem(BaseModel):
    trading_pair: str
    price: float = 0.0
    base_volume: float = 0.0
    quote_volume: float = 0.0
    # 24h volume converted to USD; None when the quote asset can't be priced.
    usd_volume: float | None = None
    # Price change against the snapshot closest to 24h ago, in percent. None
    # until `condor.ticker_history` has a reference — and for a pair that was
    # not listed when it was taken, which has no change rather than a change
    # against zero.
    change_pct: float | None = None
    # The window that change was actually measured over. It rides per row, not
    # per response: a pair listed 3h ago genuinely has a shorter window than its
    # neighbours, and the client labels the column from what it was given rather
    # than assuming 24h.
    change_window_s: float | None = None


class TickersResponse(BaseModel):
    connector: str
    tickers: list[TickerItem]
    updated_at: float | None = None


class RatesResponse(BaseModel):
    # Trading pair -> rate; None when the pair can't be resolved from the tickers.
    rates: dict[str, float | None]


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


class ControllerActionRequest(BaseModel):
    controller_names: list[str]


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
    agent: str = ""  # producing assistant/expert (e.g. "condor", "executor_manager")
    # Authenticated owner stamped at save time (SEC-196); None = legacy/ownerless,
    # visible to admins only.
    user_id: int | None = None


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
    # The Hummingbot API always runs the Gateway secured (TLS + mTLS) and manages the
    # certificates/passphrase itself (hummingbot-api SEC-048), so only image/port are sent.
    image: str = "hummingbot/gateway:development"
    port: int = 15888


class CredentialInfo(BaseModel):
    connector_name: str
    connector_type: str = ""


class GatewayPullRequest(BaseModel):
    image: str = "hummingbot/gateway:development"


class GatewayNetworkUpdateRequest(BaseModel):
    # Partial network config (snake_case keys, e.g. {"node_url": "https://..."}).
    # The Gateway validates values against its own JSON schema.
    config: dict[str, Any]


class GatewayWalletAddRequest(BaseModel):
    chain: str
    private_key: str
    set_default: bool = False


class GatewayWalletDefaultRequest(BaseModel):
    chain: str
    address: str


class AddCredentialRequest(BaseModel):
    connector_name: str
    credentials: dict[str, Any]
