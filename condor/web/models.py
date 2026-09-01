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


class ServerMember(BaseModel):
    """Someone a server is shared with, named rather than numbered."""

    user_id: int
    display_name: str


class ServerInfo(BaseModel):
    name: str
    host: str
    port: int
    online: bool = False
    permission: str = "trader"
    # The server this user's commands land on when none is named — the same
    # `chat_defaults` entry Telegram reads, so both surfaces agree on it.
    is_default: bool = False
    # Who else reaches this server (FEAT-088). Populated only for the owner and
    # for an admin: a trader seeing the rest of the member list would be telling
    # them something their own access does not entitle them to know. Empty for
    # everyone else, which is why the card only renders the line when it owns
    # the server.
    shared_with: list[ServerMember] = []


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
    # Path to this run's archived sqlite database, when one survived the bot.
    # Present iff the run has a deep history to open; the archived-bot routes take
    # it as their ``db_path``.
    archive_db_path: Optional[str] = None
    # The controller config ids this run was *deployed with*, straight from its
    # own ``deployment_config``.
    #
    # This is the authoritative run -> controller mapping, and it is the only one
    # that exists for a run old enough to have no performance snapshots left: the
    # deployment declared these ids before the bot ever traded. It is what lets a
    # closed executor be attributed to the run that created it rather than to
    # whichever live controller happens to share its config id (FEAT-089).
    controller_ids: list[str] = []
    # Whether this run is the live fleet rather than history.
    #
    # Derived, because ``run_status`` cannot answer it: upstream never writes
    # ``RUNNING``, and the eight bots trading right now on a real server all
    # report the literal string ``CREATED``. A container that is deployed and has
    # no stop time is what "still running" actually means here.
    is_live: bool = False


class BotRunsResponse(BaseModel):
    runs: list[BotRunInfo] = []
    total: int = 0


class TerminatedControllersResponse(BaseModel):
    """Every controller of every run that has finished.

    ``ControllerInfo`` rather than ``ControllerPerformanceSnapshot`` on purpose:
    a snapshot is a point on a chart and deliberately drops ``close_type_counts``
    (PERF-261), while the close-type strip leads the scope header and needs
    them. The two populations then hand the browser the same shape, which is
    what lets one tree, one fold and one set of panes describe both.
    """

    controllers: list[ControllerInfo] = []
    #: How many finished runs contributed a controller. The tree's own
    #: denominator: "12 of 137 runs are on screen" is a different fact from how
    #: many controllers there are, and only this route can count it.
    runs_seen: int = 0
    server_online: bool = True
    error_hint: Optional[str] = None


class RunHistoryResponse(BaseModel):
    """One finished run's sampled PnL curve, per controller (FEAT-089).

    The points are bare arrays rather than objects — ``[t_ms, realized,
    unrealized, net, volume, pct]`` — because there are up to a thousand of them
    per controller and the field names would be most of the bytes. The client
    expands them back into the same snapshot shape the live fleet's chart folds,
    so one ``aggregatePnlSeries`` draws both populations.
    """

    controllers: dict[str, list[list[float]]] = {}
    #: ``controller_id -> {"connector", "trading_pair"}``. Not decoration: a leaf
    #: with no pair is folded as though its quote were dollars.
    identities: dict[str, dict[str, str]] = {}
    interval: str = "5m"
    #: ``"snapshots"`` | ``"archive"`` | ``"none"``. Which source actually
    #: answered, so the chart's notice can say what was drawn rather than
    #: asserting one. ``"none"`` is an answer — a run older than the snapshot
    #: table's retention floor was never recorded — not an error.
    source: str = "snapshots"
    points: int = 0
    #: True when this came off disk. What proves the second open costs nothing.
    cached: bool = False
    detail: Optional[str] = None


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
    positions_summary: list[dict[str, Any]] = []

    @classmethod
    def from_raw(cls, raw: dict) -> "ControllerPerformanceSnapshot":
        """Build the wire model from one raw controller-performance dict.

        The upstream payload nests the numbers under ``performance`` while the
        identity fields stay at the top level. Both the REST routes and the
        ``controller_perf`` WS stream parse through here, so a snapshot means
        the same thing however it reached the dashboard -- the frontend merges
        the two into one cache entry and would otherwise read a socket frame's
        PnL as zero.

        A snapshot is a *point on a chart*, so it carries only what a point is
        drawn from. Upstream also hands back ``close_type_counts`` and a
        ``custom_info`` blob per snapshot, and history returns one of these per
        sampled interval -- for a grid or LP controller ``custom_info`` is by
        far the largest object in the payload, and tens of thousands of them
        dominated the response size, the JSON parse, react-query's structural
        sharing walk and the browser's retained memory. Nothing ever read
        either one off a snapshot: the dashboard reads ``close_type_counts``
        off ``ControllerInfo`` and the agent performance rows, and
        ``custom_info`` off executors. So they are dropped here rather than
        only on the history route, which keeps REST rows and WS frames the one
        identical shape they have to be (PERF-261).
        """
        perf = raw.get("performance", raw)
        if not isinstance(perf, dict):
            perf = {}

        return cls(
            timestamp=str(raw.get("timestamp", "")),
            bot_name=raw.get("bot_name", ""),
            controller_id=raw.get("controller_id", ""),
            controller_name=raw.get("controller_name", ""),
            connector=raw.get("connector", raw.get("connector_name", "")),
            trading_pair=raw.get("trading_pair", ""),
            realized_pnl_quote=float(perf.get("realized_pnl_quote", 0) or 0),
            unrealized_pnl_quote=float(perf.get("unrealized_pnl_quote", 0) or 0),
            global_pnl_quote=float(perf.get("global_pnl_quote", 0) or 0),
            global_pnl_pct=float(perf.get("global_pnl_pct", 0) or 0),
            volume_traded=float(perf.get("volume_traded", 0) or 0),
            positions_summary=perf.get("positions_summary", []),
        )


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


# ── The shared performance surface, over both populations (FEAT-087) ──


class PerformanceSnapshot(BaseModel):
    """One point of a performance series, for either subject.

    Deliberately **not** ``ControllerPerformanceSnapshot`` with extra fields.
    That model is the wire shape of the old controller-only route, whose
    ``from_raw`` digs the numbers out of a nested ``performance`` blob and whose
    ``volume_traded`` is spelled differently from this route's ``volume_quote``;
    the two stay separate so neither has to grow a branch for the other's
    payload, and so the existing controller path is untouched.

    Three fields carry meaning a reader must not flatten:

    * ``is_terminal`` is true only on a completed executor's final row. It is
      what makes a closed executor's series a single-table query — the terminal
      row *is* the last point, so nothing appends a final value afterwards.
    * ``cum_fees_quote`` is ``None`` for controllers, because
      ``PerformanceReport`` has no fees field. Unknown is not zero, and a fees
      chart must render the two differently.
    * ``close_type`` is set on an executor's terminal row. ``POSITION_HOLD``
      specifically means the position was handed to ``position_holds`` rather
      than settled, so its PnL stays *unrealized* — upstream already makes that
      split, and Condor must not re-derive it.

    ``performance`` and ``custom_info`` are dropped rather than carried. They
    are the raw controller passthrough — the largest objects in the payload for
    a grid or LP controller — and PERF-261 established that nothing downstream
    reads either off a snapshot.
    """

    timestamp: str = ""
    subject: str = ""
    scope_id: str = ""
    status: str = ""
    is_terminal: bool = False

    realized_pnl_quote: float = 0.0
    unrealized_pnl_quote: float = 0.0
    global_pnl_quote: float = 0.0
    global_pnl_pct: float = 0.0
    #: Volume *generated*, in quote. On every executor type including LP, where
    #: it is deliberately not the deposited capital.
    volume_quote: float = 0.0
    cum_fees_quote: Optional[float] = None

    bot_name: Optional[str] = None
    controller_id: Optional[str] = None
    executor_id: Optional[str] = None
    executor_type: Optional[str] = None
    account_name: Optional[str] = None
    connector_name: Optional[str] = None
    trading_pair: Optional[str] = None
    close_type: Optional[str] = None

    @classmethod
    def from_raw(cls, raw: dict) -> "PerformanceSnapshot":
        """Build the wire model from one upstream row.

        Numbers are coerced with an ``or 0`` guard because a JSON ``null`` in a
        float column would otherwise raise, and one malformed row must not take
        down a chart. ``cum_fees_quote`` is the exception: it is read without
        that guard precisely so its ``None`` survives as ``None``.
        """

        def num(field: str) -> float:
            try:
                return float(raw.get(field) or 0)
            except (TypeError, ValueError):
                return 0.0

        fees = raw.get("cum_fees_quote")
        try:
            fees = None if fees is None else float(fees)
        except (TypeError, ValueError):
            fees = None

        def text(field: str) -> Optional[str]:
            value = raw.get(field)
            return value if isinstance(value, str) and value else None

        return cls(
            timestamp=str(raw.get("timestamp", "")),
            subject=str(raw.get("subject", "")),
            scope_id=str(raw.get("scope_id", "")),
            status=str(raw.get("status", "")),
            is_terminal=bool(raw.get("is_terminal", False)),
            realized_pnl_quote=num("realized_pnl_quote"),
            unrealized_pnl_quote=num("unrealized_pnl_quote"),
            global_pnl_quote=num("global_pnl_quote"),
            global_pnl_pct=num("global_pnl_pct"),
            volume_quote=num("volume_quote"),
            cum_fees_quote=fees,
            bot_name=text("bot_name"),
            controller_id=text("controller_id"),
            executor_id=text("executor_id"),
            executor_type=text("executor_type"),
            account_name=text("account_name"),
            connector_name=text("connector_name"),
            trading_pair=text("trading_pair"),
            close_type=text("close_type"),
        )


class PerformanceHistoryResponse(BaseModel):
    """One page of the shared history.

    ``supported`` is the field that makes the fallback honest: false means this
    API predates ``/performance/history``, which is the ordinary case for every
    server running the published image. It is not an error and not an offline
    server, so ``server_online`` stays true and the chart draws its derived
    series with a notice saying why.
    """

    snapshots: list[PerformanceSnapshot] = []
    next_cursor: Optional[str] = None
    interval: str = "5m"
    subject: str = ""
    #: False when this server has no such route. See the class docstring.
    supported: bool = True
    server_online: bool = True
    error_hint: Optional[str] = None


class PerformanceCapabilityResponse(BaseModel):
    """Whether one server serves the shared performance surface.

    Cached with the other per-server fetches, so the answer costs one request
    per server rather than one per chart — a tree click must not be a round
    trip to ask a question whose answer only changes when the API is upgraded.

    ``unknown`` separates "asked, and the route is not there" from "could not
    ask": a server that was merely down must not have a fallback pinned to it
    for the whole TTL after it comes back.
    """

    supported: bool = False
    unknown: bool = False
    detail: Optional[str] = None


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
    # USD value of one unit of this market's quote currency. `pnl`, `volume` and
    # `cum_fees_quote` above stay quote-denominated so prices on the same row
    # remain comparable to the market's candles; renderers multiply by this.
    usd_rate: float = 1.0


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
    # Quote currency of the primary market, for labelling a converted figure.
    quote_currency: str = ""
    # USD rate per quote currency seen in the run.
    usd_rates: dict[str, float] = {}
    # False when some quote had no path to USD and its figures are reported at
    # face value in their own currency rather than silently passed off as USD.
    converted: bool = True
    # Which source the headline stats above were computed from. An archived
    # database with an empty trades table falls back to executors, and the UI
    # labels the count card accordingly instead of claiming zero trades.
    stats_source: str = "trades"


class ArchivedControllerRollup(BaseModel):
    """What one controller did inside an archived run. Money is USD.

    ``controller_id`` is ``""`` for the executors that ran under no controller
    at all — an LP or a manual run — which is a row, not an omission.
    """

    controller_id: str
    pnl_usd: float = 0.0
    volume_usd: float = 0.0
    fees_usd: float = 0.0
    executor_count: int = 0
    first_ts: float = 0.0
    last_ts: float = 0.0
    trading_pairs: list[str] = []
    connectors: list[str] = []


class ArchivedControllers(BaseModel):
    controllers: list[ArchivedControllerRollup] = []


class ArchivedRunReport(BaseModel):
    """The stored report for a run (or one of its controllers), if there is one.

    ``report_id`` is ``None`` for the ordinary case: nobody has charted this
    subject yet, or the report that did has since been pruned.
    """

    report_id: str | None = None
    created_at: str | None = None
    title: str = ""


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
    # What the report is about, if anything (FEAT-078); "" for entries saved
    # before the field existed. Keys come from condor.reports.subjects.
    subject: str = ""
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
