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


class PortfolioHistoryResponse(BaseModel):
    server: str
    points: list[PortfolioHistoryPoint]
    interval: str


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


class BotSummary(BaseModel):
    bot_name: str
    status: str = "unknown"
    num_controllers: int = 0
    error_count: int = 0
    deployed_at: Optional[str] = None


class BotsPageResponse(BaseModel):
    controllers: list[ControllerInfo] = []
    bots: list[BotSummary] = []
    total_pnl: float = 0.0
    total_volume: float = 0.0
    server_online: bool = True
    error_hint: Optional[str] = None


# ── Executors ──


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
