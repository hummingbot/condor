"""The shapes the fetchers return, owned by the data layer.

These five pydantic models are what ``archived_run`` and ``run_history`` build
and hand back, and they are also the wire schema the web layer serializes —
``condor.web.models`` re-exports every one of them under its own name, so a
route's ``response_model`` and every ``from condor.web.models import
BotRunInfo`` importer are unaffected by their living here.

They live in the data layer because the direction matters: a fetcher must not
import ``condor.web`` (see this package's docstring), and the reverse edge that
rule prevents is real — ``condor.web.models.ExecutorInfo.from_raw`` calls into
``condor.fetchers.executors``. With the shapes down here both edges point the
same way, and an agent routine that reads an archived run no longer reaches
through the web package to name its own return type.

They stay ``BaseModel`` rather than becoming dataclasses precisely so the web
layer can keep using them as response models unchanged.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ControllerInfo(BaseModel):
    controller_name: str
    #: The coarse bucket upstream sorts every controller into (``generic``,
    #: ``directional_trading``, ``market_making``) — a fallback class for a
    #: terminated controller whose config lookup could not recover the specific
    #: one (see ``fill_classes_from_config``).
    controller_type: str = ""
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
