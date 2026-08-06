"""Bot Performance Report — Clean 2-message report with USD values and rebate tracking."""

CATEGORY = "Bot Analysis"

import asyncio
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timezone

import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client, get_config_manager

logger = logging.getLogger(__name__)

REBATE_RATE = 0.00015  # 0.015% of volume — only applies to BRL-quoted pairs

# Config analysis constants
_CONFIG_SKIP_PARAMS = {
    "id",
    "controller_type",
    "controller_name",
    "manual_kill_switch",
    "_config_name",
}
_CONFIG_KEY_PARAMS = {
    "buy_spreads",
    "sell_spreads",
    "buy_amounts_pct",
    "sell_amounts_pct",
    "total_amount_quote",
    "stop_loss",
    "take_profit",
    "time_limit",
    "executor_refresh_time",
    "cooldown_time",
    "leverage",
    "dca_spreads",
    "dca_amounts",
    "trailing_stop",
    "executor_activation_bounds",
    "top_executor_refresh_time",
    "position_rebalance_threshold_pct",
}


def _pair_has_rebate(pair: str, quote_currency: str) -> bool:
    """Rebate only applies to pairs quoted in the rebate currency (BRL)."""
    if not pair or "-" not in pair:
        return False
    return pair.split("-")[-1].upper() == quote_currency.upper()


class Config(BaseModel):
    """Generate a formatted performance report for all active bots."""

    quote_currency: str = Field(
        default="BRL",
        description="Quote currency of the bots (for USD conversion)",
    )
    target_chat_id: int | None = Field(
        default=None,
        description="Send report to this chat ID instead of the current chat (e.g. a group chat)",
    )
    server_name: str = Field(
        default="brigado_2",
        description="Server to connect to for bot data (bots run on this server)",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt(n: float, decimals: int = 2) -> str:
    if abs(n) >= 1_000:
        return f"{n:,.{decimals}f}"
    return f"{n:.{decimals}f}"


def _sign(n: float) -> str:
    return "+" if n >= 0 else "-"


def _emoji(n: float) -> str:
    return "🟢" if n > 0 else "🔴" if n < 0 else "⚪"


def _md_escape(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!"
    result = []
    for ch in text:
        if ch in special:
            result.append(f"\\{ch}")
        else:
            result.append(ch)
    return "".join(result)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


async def _get_bots_data(client) -> dict:
    data = await client.bot_orchestration.get_active_bots_status()
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return {}


async def _get_bot_runs(client) -> dict:
    runs = {}
    try:
        data = await client.bot_orchestration.get_bot_runs()
        if isinstance(data, dict) and "data" in data:
            for run in data["data"]:
                if run.get("deployment_status") == "DEPLOYED" and run.get(
                    "deployed_at"
                ):
                    runs[run["bot_name"]] = run["deployed_at"]
    except Exception:
        pass
    return runs


async def _get_prices(client, connector: str, pairs: list[str]) -> dict[str, float]:
    try:
        result = await client.market_data.get_prices(
            connector_name=connector, trading_pairs=",".join(pairs)
        )
        return result.get("prices", {}) if result else {}
    except Exception as e:
        logger.warning(f"Failed to get prices: {e}")
        return {}


async def _get_balances(client) -> dict:
    """Fetch portfolio balances from all accounts."""
    try:
        return await client.portfolio.get_state(refresh=False)
    except Exception as e:
        logger.warning(f"Failed to get balances: {e}")
        return {}


async def _get_market_volume_since(
    client, connector: str, pair: str, since_dt: datetime | None
) -> tuple[float, float]:
    """Return (base_volume, quote_volume) accumulated from candles since *since_dt*.

    Uses 1h candles spanning from *since_dt* to now so the market volume window
    matches the bot's runtime, giving an accurate market-share calculation.
    Falls back to a single 1d candle when *since_dt* is unavailable.
    """
    try:
        if since_dt:
            now = datetime.now(timezone.utc)
            days = max(1, int((now - since_dt).total_seconds() / 86400) + 1)
            candles = await client.market_data.get_candles_last_days(
                connector_name=connector,
                trading_pair=pair,
                days=days,
                interval="1h",
            )
        else:
            candles = await client.market_data.get_candles(
                connector_name=connector,
                trading_pair=pair,
                interval="1d",
                max_records=1,
            )

        if candles:
            data = candles if isinstance(candles, list) else candles.get("data", [])
            if not data:
                return 0.0, 0.0

            since_ts = since_dt.timestamp() if since_dt else 0
            total_base = 0.0
            total_quote = 0.0
            for row in data:
                # Only count candles that start at or after the deploy time
                ts = float(row.get("timestamp", 0) or 0)
                # Normalise ms → s if needed
                if ts > 1e12:
                    ts /= 1000
                if ts >= since_ts:
                    total_base += float(row.get("volume", 0) or 0)
                    total_quote += float(row.get("quote_asset_volume", 0) or 0)
            return total_base, total_quote
    except Exception as e:
        logger.warning(f"Failed to get volume for {pair}: {e}")
    return 0.0, 0.0


# ---------------------------------------------------------------------------
# Chart builders (Plotly)
# ---------------------------------------------------------------------------

_DARK_BG = "#1a1a2e"
_DARK_PAPER = "#16213e"
_GREEN = "#00d4aa"
_RED = "#ff6b6b"
_BLUE = "#58a6ff"
_YELLOW = "#ffdd57"
_COLORS = [
    "#58a6ff",
    "#00d4aa",
    "#ff6b6b",
    "#ffdd57",
    "#c084fc",
    "#fb923c",
    "#38bdf8",
    "#f472b6",
]


def _chart_layout(title: str, **kwargs) -> dict:
    base = dict(
        title=dict(text=title, font=dict(color="#e6edf3", size=14), x=0.5),
        paper_bgcolor=_DARK_PAPER,
        plot_bgcolor=_DARK_BG,
        font=dict(color="#ccc", size=11),
        margin=dict(l=60, r=80, t=50, b=80),
        width=1100,
        height=450,
    )
    base.update(kwargs)
    return base


def _quote_is_usd(pair: str) -> bool:
    """Check if a pair's quote currency is a USD stablecoin."""
    if not pair or "-" not in pair:
        return False
    return pair.split("-")[-1].upper() in ("USD", "USDT", "USDC", "BUSD", "FDUSD")


def _vol_to_usd(ctrl: dict, to_usd: float) -> float:
    """Convert controller volume to USD based on the pair's quote currency."""
    if _quote_is_usd(ctrl.get("pair", "")):
        return ctrl["vol"]
    return ctrl["vol"] * to_usd


def _amount_quote_to_usd(ctrl: dict, to_usd: float) -> float:
    """Convert total_amount_quote to USD based on the pair's quote currency."""
    amt = ctrl.get("total_amount_quote", 0)
    if _quote_is_usd(ctrl.get("pair", "")):
        return amt
    return amt * to_usd


def _market_vol_to_usd(
    pair: str,
    vol_base: float,
    vol_quote: float,
    to_usd: float,
    prices: dict[str, float],
) -> float:
    """Convert market candle volume to USD for any pair."""
    base = pair.split("-")[0].upper() if "-" in pair else ""
    quote = pair.split("-")[-1].upper() if "-" in pair else ""

    # If base is a stablecoin, vol_base is already ~USD
    if base in ("USDT", "USDC", "BUSD", "DAI", "FDUSD"):
        return vol_base

    # If quote is USD stablecoin, vol_quote is already USD
    if quote in ("USD", "USDT", "USDC", "BUSD", "FDUSD"):
        return (
            vol_quote
            if vol_quote > 0
            else vol_base * (prices.get(f"{base}-USDT", 0) or 0)
        )

    # Non-USD quote (e.g. BRL) — convert quote volume, or use base price
    if vol_quote > 0:
        return vol_quote * to_usd
    bp = prices.get(f"{base}-USDT", 0) or prices.get(f"{base}-USD", 0)
    return vol_base * bp if bp else 0


def _short_chart_name(ctrl_id: str, pair: str = "") -> str:
    """Clean controller name for chart labels."""
    name = ctrl_id
    # Strip common prefixes
    name = re.sub(r"^mm_", "", name)
    name = re.sub(r"pmm_?mister_?", "", name)
    # Strip connector prefixes (binance_, binance_perpetual_, etc.)
    name = re.sub(r"^(binance|okx|bybit|kucoin)(_perpetual)?_", "", name)
    # Strip pair fragment
    if pair:
        pair_frag = pair.replace("-", "_")
        name = name.replace(pair_frag, "").replace(pair, "")
    while "__" in name:
        name = name.replace("__", "_")
    name = name.strip("_")
    name = re.sub(r"config[_]?(\d+)", r"c\1", name)
    return name or ctrl_id[:20]


def _build_pnl_chart(
    controllers: list[dict],
    to_usd: float,
    quote_currency: str = "BRL",
    ctrl_configs: dict | None = None,
) -> go.Figure:
    """Horizontal bar chart: PnL breakdown, Total PnL, and Vol/Capital by controller."""
    # Sort by net PnL
    sorted_ctrls = sorted(controllers, key=lambda c: c["net"] * to_usd)
    names = [_short_chart_name(c["id"], c["pair"]) for c in sorted_ctrls]
    realized = [c["realized"] * to_usd for c in sorted_ctrls]
    unrealized = [c["unrealized"] * to_usd for c in sorted_ctrls]
    rebates = [
        (
            c["vol"] * to_usd * REBATE_RATE
            if _pair_has_rebate(c["pair"], quote_currency)
            else 0
        )
        for c in sorted_ctrls
    ]
    totals = [r + u + rb for r, u, rb in zip(realized, unrealized, rebates)]
    volumes = [_vol_to_usd(c, to_usd) for c in sorted_ctrls]

    # Capital from configs (converted to USD)
    has_capital = False
    capitals = []
    if ctrl_configs:
        for c in sorted_ctrls:
            cfg = ctrl_configs.get(c["id"], {})
            amt = float(cfg.get("total_amount_quote", 0) or 0)
            ctrl_for_conv = {"total_amount_quote": amt, "pair": c["pair"]}
            capitals.append(_amount_quote_to_usd(ctrl_for_conv, to_usd))
            if amt > 0:
                has_capital = True

    # Vol/Capital turnover ratio
    vol_cap_ratios = []
    if has_capital:
        for v, cap in zip(volumes, capitals):
            vol_cap_ratios.append(v / cap if cap > 0 else 0)

    ncols = 3 if has_capital else 2
    col_widths = [0.40, 0.30, 0.30] if has_capital else [0.55, 0.45]
    subtitles = (
        ["PnL Breakdown (USD)", "Total PnL (USD)", "Vol / Capital"]
        if has_capital
        else ["PnL Breakdown (USD)", "Total PnL (USD)"]
    )

    fig = make_subplots(
        rows=1,
        cols=ncols,
        shared_yaxes=True,
        column_widths=col_widths,
        horizontal_spacing=0.03,
        subplot_titles=subtitles,
    )

    # Col 1: PnL breakdown (stacked)
    fig.add_trace(
        go.Bar(
            y=names,
            x=realized,
            orientation="h",
            name="Realized",
            marker_color=_GREEN,
            opacity=0.85,
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            y=names,
            x=unrealized,
            orientation="h",
            name="Unrealized",
            marker_color=_BLUE,
            opacity=0.85,
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            y=names,
            x=rebates,
            orientation="h",
            name="Rebate",
            marker_color=_YELLOW,
            opacity=0.7,
        ),
        row=1,
        col=1,
    )

    # Col 2: Total PnL (single bar per controller)
    total_colors = [_GREEN if t >= 0 else _RED for t in totals]
    fig.add_trace(
        go.Bar(
            y=names,
            x=totals,
            orientation="h",
            name="Total",
            marker_color=total_colors,
            opacity=0.9,
            text=[f"{'+'if t>=0 else ''}{t:.1f}" for t in totals],
            textposition="inside",
            textfont=dict(size=10, color="white"),
            insidetextanchor="middle",
            showlegend=False,
        ),
        row=1,
        col=2,
    )

    # Col 3: Vol/Capital turnover
    if has_capital:
        fig.add_trace(
            go.Bar(
                y=names,
                x=vol_cap_ratios,
                orientation="h",
                name="Vol/Capital",
                marker_color="#8b5cf6",
                opacity=0.85,
                text=[f"{r:.0f}x" if r > 0 else "-" for r in vol_cap_ratios],
                textposition="outside",
                textfont=dict(size=10),
                showlegend=False,
            ),
            row=1,
            col=3,
        )

    height = max(350, len(names) * 40 + 100)
    fig.update_layout(
        **_chart_layout("PnL Breakdown · Total · Vol/Capital", height=height),
        barmode="relative",
        legend=dict(orientation="h", y=-0.12, x=0.5, xanchor="center"),
    )
    fig.update_xaxes(gridcolor="#2a2a4a", title="USD", row=1, col=1)
    fig.update_xaxes(gridcolor="#2a2a4a", title="USD", row=1, col=2)
    if has_capital:
        fig.update_xaxes(gridcolor="#2a2a4a", title="Turnover (x)", row=1, col=3)
    fig.update_yaxes(gridcolor="#2a2a4a", row=1, col=1)
    # Fix subplot title colors
    for ann in fig.layout.annotations:
        ann.font = dict(color="#aaa", size=11)
    return fig


def _build_pair_overview_chart(
    controllers: list[dict],
    to_usd: float,
    volumes_since: dict[str, tuple[float, float]],
    prices: dict[str, float],
    quote_currency: str = "BRL",
) -> go.Figure:
    """Unified horizontal bar chart: PnL, Volume, and Market Share by pair."""
    # Aggregate data per pair
    pair_data: dict[str, dict] = defaultdict(
        lambda: {"realized": 0, "unrealized": 0, "vol": 0}
    )
    for c in controllers:
        key = c["pair"] or "unknown"
        pair_data[key]["realized"] += c["realized"] * to_usd
        pair_data[key]["unrealized"] += c["unrealized"] * to_usd
        pair_data[key]["vol"] += _vol_to_usd(c, to_usd)

    # Sort pairs by total volume (highest at top)
    pairs = sorted(pair_data.keys(), key=lambda p: pair_data[p]["vol"])

    # Compute market share data
    pair_market: dict[str, dict] = {}
    for pair in pairs:
        vol_data = volumes_since.get(pair, (0, 0))
        vol_base, vol_quote = vol_data if isinstance(vol_data, tuple) else (vol_data, 0)
        mkt_usd = (
            _market_vol_to_usd(pair, vol_base, vol_quote, to_usd, prices)
            if (vol_base > 0 or vol_quote > 0)
            else 0
        )
        our_vol = pair_data[pair]["vol"]
        share = (our_vol / mkt_usd * 100) if mkt_usd > 0 else 0
        pair_market[pair] = {"market": mkt_usd, "ours": our_vol, "share": share}

    has_market = any(d["market"] > 0 for d in pair_market.values())
    ncols = 3 if has_market else 2
    col_widths = [0.4, 0.35, 0.25] if has_market else [0.5, 0.5]
    subtitles = (
        ["PnL (USD)", "Volume (USD)", "Market Share"]
        if has_market
        else ["PnL (USD)", "Volume (USD)"]
    )

    fig = make_subplots(
        rows=1,
        cols=ncols,
        shared_yaxes=True,
        column_widths=col_widths,
        horizontal_spacing=0.04,
        subplot_titles=subtitles,
    )

    # Col 1: PnL (realized + unrealized + rebate)
    fig.add_trace(
        go.Bar(
            y=pairs,
            x=[pair_data[p]["realized"] for p in pairs],
            orientation="h",
            name="Realized",
            marker_color=_GREEN,
            opacity=0.85,
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            y=pairs,
            x=[pair_data[p]["unrealized"] for p in pairs],
            orientation="h",
            name="Unrealized",
            marker_color=_BLUE,
            opacity=0.85,
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            y=pairs,
            x=[
                (
                    pair_data[p]["vol"] * REBATE_RATE
                    if _pair_has_rebate(p, quote_currency)
                    else 0
                )
                for p in pairs
            ],
            orientation="h",
            name="Rebate",
            marker_color=_YELLOW,
            opacity=0.7,
        ),
        row=1,
        col=1,
    )

    # Col 2: Volume
    fig.add_trace(
        go.Bar(
            y=pairs,
            x=[pair_data[p]["vol"] for p in pairs],
            orientation="h",
            name="Volume",
            marker_color="#8b5cf6",
            opacity=0.8,
        ),
        row=1,
        col=2,
    )

    # Col 3: Market Share as percentage bar
    if has_market:
        shares = [pair_market[p]["share"] for p in pairs]
        fig.add_trace(
            go.Bar(
                y=pairs,
                x=shares,
                orientation="h",
                name="Market Share",
                marker_color=[_BLUE if s > 0 else "#30363d" for s in shares],
                opacity=0.85,
                text=[f"{s:.2f}%" if s > 0 else "N/A" for s in shares],
                textposition="outside",
                textfont=dict(size=11, color=_BLUE),
                hovertemplate="%{y}<br>Share: %{x:.2f}%<br>"
                "Ours: $%{customdata[0]:,.0f}<br>"
                "Market: $%{customdata[1]:,.0f}<extra></extra>",
                customdata=[
                    [pair_market[p]["ours"], pair_market[p]["market"]] for p in pairs
                ],
            ),
            row=1,
            col=ncols,
        )

    height = max(350, len(pairs) * 55 + 120)
    fig.update_layout(
        **_chart_layout("Pair Overview: PnL · Volume · Market Share", height=height),
        barmode="relative",
        legend=dict(orientation="h", y=-0.12, x=0.5, xanchor="center"),
    )
    for col in range(1, ncols + 1):
        fig.update_xaxes(gridcolor="#2a2a4a", row=1, col=col)
    if has_market:
        fig.update_xaxes(title="%", row=1, col=ncols)
    fig.update_yaxes(gridcolor="#2a2a4a", row=1, col=1)
    # Fix subplot title colors
    for ann in fig.layout.annotations:
        ann.font = dict(color="#aaa", size=11)
    return fig


# ---------------------------------------------------------------------------
# Config analysis helpers
# ---------------------------------------------------------------------------


def _cfg_fmt_value(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "✓" if v else "✗"
    if isinstance(v, float):
        if v < 0.001:
            return f"{v:.5f}"
        if v < 0.01:
            return f"{v:.4f}"
        return f"{v:.3f}" if v < 10 else f"{v:.1f}"
    if isinstance(v, list):
        return ", ".join(_cfg_fmt_value(x) for x in v)
    if isinstance(v, dict):
        return " | ".join(f"{k}={_cfg_fmt_value(vv)}" for k, vv in v.items())
    return str(v)


def _cfg_normalize(v):
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return v.strip().lower()
    if isinstance(v, list):
        return [_cfg_normalize(x) for x in v]
    if isinstance(v, dict):
        return {k: _cfg_normalize(vv) for k, vv in v.items()}
    return v


def _build_config_parallel_coords(
    all_configs: dict, running_ids: set
) -> go.Figure | None:
    NUMERIC_AXES = [
        ("total_amount_quote", "Capital", None),
        (
            "buy_spreads",
            "Buy Spread L1 %",
            lambda v: (v[0] if isinstance(v, list) and v else v) if v else None,
        ),
        (
            "sell_spreads",
            "Sell Spread L1 %",
            lambda v: (v[0] if isinstance(v, list) and v else v) if v else None,
        ),
        (
            "buy_spreads",
            "Buy Spread L2 %",
            lambda v: (
                (v[1] if isinstance(v, list) and len(v) > 1 else None) if v else None
            ),
        ),
        (
            "sell_spreads",
            "Sell Spread L2 %",
            lambda v: (
                (v[1] if isinstance(v, list) and len(v) > 1 else None) if v else None
            ),
        ),
        ("executor_refresh_time", "Refresh Time", None),
        ("cooldown_time", "Cooldown", None),
        ("leverage", "Leverage", None),
        ("stop_loss", "Stop Loss", None),
        ("take_profit", "Take Profit", None),
        ("time_limit", "Time Limit", None),
        ("top_executor_refresh_time", "Top Refresh", None),
        (
            "dca_spreads",
            "DCA Spread L1",
            lambda v: (v[0] if isinstance(v, list) and v else v) if v else None,
        ),
        (
            "dca_amounts",
            "DCA Amt L1",
            lambda v: (v[0] if isinstance(v, list) and v else v) if v else None,
        ),
        (
            "trailing_stop",
            "Trail Stop",
            lambda v: (
                (v.get("activation_price_delta") if isinstance(v, dict) else v)
                if v
                else None
            ),
        ),
        (
            "executor_activation_bounds",
            "Activation Bounds",
            lambda v: (v[0] if isinstance(v, list) and v else v) if v else None,
        ),
    ]

    rows = []
    labels = []
    for label, cfg in all_configs.items():
        clean_label = label.replace("\U0001f7e2 ", "").replace("\U0001f535 ", "")
        is_running = clean_label in running_ids or label.startswith("\U0001f7e2")
        pair = cfg.get("trading_pair", "unknown")
        row = {"_running": 1 if is_running else 0, "_pair": pair}
        for param, axis_name, extractor in NUMERIC_AXES:
            raw = cfg.get(param)
            val = extractor(raw) if extractor else raw
            try:
                row[axis_name] = float(val) if val is not None else None
            except (TypeError, ValueError):
                row[axis_name] = None
        rows.append(row)
        labels.append(_short_chart_name(clean_label, pair))

    if len(rows) < 2:
        return None

    valid_axes = []
    for _, axis_name, _ in NUMERIC_AXES:
        vals = [r[axis_name] for r in rows if r[axis_name] is not None]
        if len(vals) >= 2 and len(set(vals)) > 1:
            valid_axes.append(axis_name)

    if len(valid_axes) < 2:
        return None

    unique_pairs = sorted(set(r["_pair"] for r in rows))
    pair_to_idx = {p: i for i, p in enumerate(unique_pairs)}
    color_values = [pair_to_idx[r["_pair"]] for r in rows]

    n_pairs = len(unique_pairs)
    if n_pairs == 1:
        colorscale = [[0, _BLUE], [1, _BLUE]]
    else:
        colorscale = [
            [i / (n_pairs - 1), _COLORS[i % len(_COLORS)]] for i in range(n_pairs)
        ]

    for axis_name in valid_axes:
        vals = [r[axis_name] for r in rows if r[axis_name] is not None]
        fill = min(vals) if vals else 0
        for r in rows:
            if r[axis_name] is None:
                r[axis_name] = fill

    dimensions = []
    for axis_name in valid_axes:
        vals = [r[axis_name] for r in rows]
        if "%" in axis_name:
            vals = [v * 100 for v in vals]
        dimensions.append(
            dict(
                label=axis_name,
                values=vals,
                range=(
                    [min(vals), max(vals)]
                    if min(vals) != max(vals)
                    else [min(vals) - 1, max(vals) + 1]
                ),
            )
        )

    fig = go.Figure(
        data=go.Parcoords(
            line=dict(color=color_values, colorscale=colorscale, showscale=False),
            dimensions=dimensions,
            customdata=labels,
        )
    )
    for i, pair in enumerate(unique_pairs):
        fig.add_trace(
            go.Scatter(
                x=[None],
                y=[None],
                mode="markers",
                marker=dict(size=10, color=_COLORS[i % len(_COLORS)]),
                name=pair,
                showlegend=True,
            )
        )
    fig.update_layout(
        **_chart_layout(
            f"Config Parameter Space ({len(rows)} configs, {len(valid_axes)} axes)",
            width=1400,
            height=600,
        ),
        legend=dict(
            orientation="h", y=-0.05, x=0.5, xanchor="center", font=dict(size=12)
        ),
    )
    return fig


def _build_config_spread_chart(all_configs: dict) -> go.Figure | None:
    def _to_list(v) -> list:
        if v is None:
            return []
        if isinstance(v, (int, float)):
            return [float(v)]
        if isinstance(v, list):
            return [float(x) for x in v]
        return []

    items = []
    for label, cfg in all_configs.items():
        buy = _to_list(cfg.get("buy_spreads"))
        sell = _to_list(cfg.get("sell_spreads"))
        if buy or sell:
            short = _short_chart_name(
                label.replace("\U0001f7e2 ", "").replace("\U0001f535 ", ""),
                cfg.get("trading_pair", ""),
            )
            items.append(
                {
                    "name": short,
                    "pair": cfg.get("trading_pair", ""),
                    "buy_l1": buy[0] * 100 if len(buy) >= 1 else 0,
                    "sell_l1": sell[0] * 100 if len(sell) >= 1 else 0,
                    "buy_l2": buy[1] * 100 if len(buy) >= 2 else 0,
                    "sell_l2": sell[1] * 100 if len(sell) >= 2 else 0,
                }
            )

    if not items:
        return None

    items.sort(key=lambda c: (c["pair"], c["name"]))
    names = [c["name"] for c in items]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=names,
            y=[c["buy_l1"] for c in items],
            name="Buy L1",
            marker_color=_GREEN,
            opacity=0.9,
        )
    )
    fig.add_trace(
        go.Bar(
            x=names,
            y=[c["sell_l1"] for c in items],
            name="Sell L1",
            marker_color=_RED,
            opacity=0.9,
        )
    )
    if any(c["buy_l2"] > 0 or c["sell_l2"] > 0 for c in items):
        fig.add_trace(
            go.Bar(
                x=names,
                y=[c["buy_l2"] for c in items],
                name="Buy L2",
                marker_color=_GREEN,
                opacity=0.5,
            )
        )
        fig.add_trace(
            go.Bar(
                x=names,
                y=[c["sell_l2"] for c in items],
                name="Sell L2",
                marker_color=_RED,
                opacity=0.5,
            )
        )

    fig.update_layout(
        **_chart_layout("Spread Levels by Controller (%)", width=1200, height=500),
        barmode="group",
        yaxis=dict(gridcolor="#2a2a4a", title="Spread %"),
        xaxis=dict(gridcolor="#2a2a4a", tickangle=-45),
        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
    )
    fig.update_layout(margin=dict(l=60, r=40, t=60, b=120))
    return fig


def _build_config_capital_chart(all_configs: dict) -> go.Figure | None:
    data = []
    for label, cfg in all_configs.items():
        amt = float(cfg.get("total_amount_quote", 0) or 0)
        if amt <= 0:
            continue
        short = _short_chart_name(
            label.replace("\U0001f7e2 ", "").replace("\U0001f535 ", ""),
            cfg.get("trading_pair", ""),
        )
        data.append({"name": short, "pair": cfg.get("trading_pair", ""), "amount": amt})

    if not data:
        return None

    data.sort(key=lambda d: (d["pair"], d["amount"]))
    unique_pairs = list(dict.fromkeys(d["pair"] for d in data))
    pair_colors = {p: _COLORS[i % len(_COLORS)] for i, p in enumerate(unique_pairs)}

    fig = go.Figure()
    for pair in unique_pairs:
        pair_data = [d for d in data if d["pair"] == pair]
        fig.add_trace(
            go.Bar(
                y=[d["name"] for d in pair_data],
                x=[d["amount"] for d in pair_data],
                orientation="h",
                name=pair,
                marker_color=pair_colors[pair],
                opacity=0.85,
                text=[f"{d['amount']:,.0f}" for d in pair_data],
                textposition="auto",
            )
        )

    height = max(400, len(data) * 22 + 100)
    fig.update_layout(
        **_chart_layout(
            "Capital Allocation by Controller (quote)", width=1200, height=height
        ),
        barmode="stack",
        xaxis=dict(gridcolor="#2a2a4a", title="Amount (quote)"),
        yaxis=dict(gridcolor="#2a2a4a"),
        legend=dict(orientation="h", y=1.05, x=0.5, xanchor="center"),
    )
    fig.update_layout(margin=dict(l=150, r=40, t=60, b=50))
    return fig


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------


def _build_report(
    bots_data: dict,
    bot_runs: dict,
    prices: dict[str, float],
    volumes_since: dict[str, tuple[float, float]],
    quote_usd_rate: float,
    quote_currency: str = "BRL",
    ctrl_configs: dict | None = None,
    balances: dict | None = None,
) -> list[str]:
    ctrl_configs = ctrl_configs or {}

    # ---- Parse controller data ----
    controllers = []

    for bot_name, bot_data in bots_data.items():
        if isinstance(bot_data, str):
            continue

        performance = {}
        if isinstance(bot_data, dict):
            performance = bot_data.get("performance", {})
        elif isinstance(bot_data, list):
            performance = {
                f"ctrl_{i}": {"performance": c} for i, c in enumerate(bot_data)
            }

        for ctrl_name, ctrl_data in performance.items():
            if not isinstance(ctrl_data, dict):
                continue
            perf = ctrl_data.get("performance", ctrl_data)
            cfg = ctrl_configs.get(ctrl_name, {})
            trading_pair = cfg.get("trading_pair", "") or perf.get("trading_pair", "")

            vol = float(perf.get("volume_traded", 0) or 0)
            realized = float(perf.get("realized_pnl_quote", 0) or 0)
            unrealized = float(perf.get("unrealized_pnl_quote", 0) or 0)
            total_amount_quote = float(cfg.get("total_amount_quote", 0) or 0)

            controllers.append(
                {
                    "id": ctrl_name,
                    "bot": bot_name,
                    "pair": trading_pair,
                    "vol": vol,
                    "realized": realized,
                    "unrealized": unrealized,
                    "net": realized + unrealized,
                    "total_amount_quote": total_amount_quote,
                }
            )

    if not controllers:
        return ["No controller data found\\."]

    # ---- Runtime ----
    earliest_dt = None
    for bn in {c["bot"] for c in controllers}:
        ts = bot_runs.get(bn)
        if ts:
            try:
                ts_str = str(ts)
                if ts_str.endswith("Z"):
                    ts_str = ts_str[:-1] + "+00:00"
                dt = datetime.fromisoformat(ts_str)
                if earliest_dt is None or dt < earliest_dt:
                    earliest_dt = dt
            except (ValueError, TypeError):
                pass

    now_dt = datetime.now(timezone.utc)
    runtime_h = (now_dt - earliest_dt).total_seconds() / 3600 if earliest_dt else 0
    if earliest_dt:
        start_str = earliest_dt.strftime("%b %d, %H:%M UTC")
    else:
        start_str = "N/A"

    # ---- Totals ----
    total_vol_q = sum(c["vol"] for c in controllers)
    total_realized_q = sum(c["realized"] for c in controllers)
    total_unrealized_q = sum(c["unrealized"] for c in controllers)
    total_net_q = total_realized_q + total_unrealized_q

    to_usd = quote_usd_rate
    total_vol = sum(_vol_to_usd(c, to_usd) for c in controllers)
    total_realized = total_realized_q * to_usd
    total_unrealized = total_unrealized_q * to_usd
    total_net = total_net_q * to_usd
    total_rebate_vol = sum(
        _vol_to_usd(c, to_usd)
        for c in controllers
        if _pair_has_rebate(c["pair"], quote_currency)
    )
    total_rebate = total_rebate_vol * REBATE_RATE

    vol_hr = total_vol / runtime_h if runtime_h > 0 else 0
    realized_hr = total_realized / runtime_h if runtime_h > 0 else 0
    rebate_hr = total_rebate / runtime_h if runtime_h > 0 else 0
    net_after_rebate = total_net + total_rebate

    # ---- Market volumes ----
    unique_pairs = {c["pair"] for c in controllers if c["pair"]}
    market_lines = []
    for pair in sorted(unique_pairs):
        vol_data = volumes_since.get(pair, (0, 0))
        vol_24h_base, vol_24h_quote = (
            vol_data if isinstance(vol_data, tuple) else (vol_data, 0)
        )
        if vol_24h_base <= 0 and vol_24h_quote <= 0:
            continue
        mkt_usd = _market_vol_to_usd(pair, vol_24h_base, vol_24h_quote, to_usd, prices)
        our_vol = sum(_vol_to_usd(c, to_usd) for c in controllers if c["pair"] == pair)
        share = (our_vol / mkt_usd * 100) if mkt_usd > 0 else 0
        market_lines.append(
            f"  {_md_escape(pair)}: mkt \\${_md_escape(_fmt(mkt_usd))} "
            f"\\| ours \\${_md_escape(_fmt(our_vol))} \\({_md_escape(f'{share:.2f}')}%\\)"
        )

    # ---- FX rate display ----
    qc = quote_currency.upper()
    if qc not in ("USD", "USDT"):
        fx_val = _fmt(1 / to_usd if to_usd > 0 else 0)
        fx_display = f"1 USD \\= {_md_escape(fx_val)} {_md_escape(qc)}"
    else:
        fx_display = ""

    # ---- MSG 1: Overview ----
    num_bots = len({c["bot"] for c in controllers})
    num_ctrls = len(controllers)

    # ---- Balance summary ----
    balance_lines = []
    total_balance_usd = 0.0
    if balances:
        token_totals: dict[str, float] = defaultdict(float)
        token_values: dict[str, float] = defaultdict(float)
        for account_data in balances.values():
            for connector_balances in account_data.values():
                if not isinstance(connector_balances, list):
                    continue
                for b in connector_balances:
                    token = b.get("token", "")
                    value = float(b.get("value", 0) or 0)
                    units = float(b.get("units", 0) or 0)
                    if value > 0:
                        token_totals[token] += units
                        token_values[token] += value
        total_balance_usd = sum(token_values.values())
        # Top tokens by value
        sorted_tokens = sorted(token_values.items(), key=lambda x: x[1], reverse=True)
        for token, value in sorted_tokens[:5]:
            pct = (value / total_balance_usd * 100) if total_balance_usd > 0 else 0
            balance_lines.append(
                f"  {_md_escape(token)}: \\${_md_escape(_fmt(value))} \\({_md_escape(f'{pct:.0f}')}%\\)"
            )

    msg1_parts = [
        f"*📊 TRADING REPORT*",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"",
        f"⏱ {_md_escape(f'{runtime_h:.1f}')}h runtime \\(since {_md_escape(start_str)}\\)",
        f"🤖 {num_bots} bots \\| {num_ctrls} controllers",
    ]
    if fx_display:
        msg1_parts.append(f"💱 {fx_display}")

    if balance_lines:
        msg1_parts += [
            f"",
            f"💰 *Balance: \\${_md_escape(_fmt(total_balance_usd))}*",
        ] + balance_lines

    msg1_parts += [
        f"",
        f"{_emoji(total_net)} *Performance \\(USD\\)*",
        f"Volume: \\${_md_escape(_fmt(total_vol))}",
        f"Realized: {_md_escape(_sign(total_realized))}\\${_md_escape(_fmt(abs(total_realized)))}",
        f"Unrealized: {_md_escape(_sign(total_unrealized))}\\${_md_escape(_fmt(abs(total_unrealized)))}",
        f"Net PnL: {_md_escape(_sign(total_net))}\\${_md_escape(_fmt(abs(total_net)))}",
        f"Rebate: \\+\\${_md_escape(_fmt(total_rebate))}",
        f"Net\\+Rebate: {_md_escape(_sign(net_after_rebate))}\\${_md_escape(_fmt(abs(net_after_rebate)))}",
        f"",
        f"⏳ *Hourly Rates*",
        f"Vol/hr: \\${_md_escape(_fmt(vol_hr))}",
        f"Realized/hr: {_md_escape(_sign(realized_hr))}\\${_md_escape(_fmt(abs(realized_hr)))}",
        f"Rebate/hr: \\+\\${_md_escape(_fmt(rebate_hr))}",
    ]

    if market_lines:
        msg1_parts += [f"", f"📈 *Market Share*"] + market_lines

    # ---- MSG 2: Controllers grouped by pair ----
    msg2_parts = [
        f"*📋 CONTROLLERS BY PAIR*",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    pair_groups: dict[str, list[dict]] = defaultdict(list)
    for c in controllers:
        pair_groups[c["pair"] or "unknown"].append(c)

    sorted_pairs = sorted(
        pair_groups.items(), key=lambda x: sum(c["vol"] for c in x[1]), reverse=True
    )

    for pair, ctrls in sorted_pairs:
        grp_vol = sum(_vol_to_usd(c, to_usd) for c in ctrls)
        grp_realized = sum(c["realized"] for c in ctrls) * to_usd
        grp_unrealized = sum(c["unrealized"] for c in ctrls) * to_usd
        grp_net = grp_realized + grp_unrealized
        grp_rebate = (
            grp_vol * REBATE_RATE if _pair_has_rebate(pair, quote_currency) else 0
        )
        grp_adj = grp_net + grp_rebate

        pair_escaped = _md_escape(pair)
        msg2_parts.append(f"")
        msg2_parts.append(
            f"▸ *{pair_escaped}* \\({len(ctrls)} ctrls\\) "
            f"{_emoji(grp_adj)} {_md_escape(_sign(grp_adj))}\\${_md_escape(_fmt(abs(grp_adj)))}"
        )

        # Build controller table rows
        sorted_ctrls = sorted(ctrls, key=lambda c: c["vol"], reverse=True)

        def _short_name(ctrl_id: str, pair: str) -> str:
            name = ctrl_id
            if name.startswith("mm_"):
                name = name[3:]
            # Strip all variations of pmm_mister prefix
            name = re.sub(r"pmm_?mister_?", "", name)
            pair_frag = pair.replace("-", "_") if pair else ""
            if pair_frag:
                name = name.replace(pair_frag, "").replace(pair, "")
            while "__" in name:
                name = name.replace("__", "_")
            name = name.strip("_")
            name = re.sub(r"config[_]?(\d+)", r"c\1", name)
            return name or ctrl_id[:20]

        rows = []
        for c in sorted_ctrls:
            vol_usd = _vol_to_usd(c, to_usd)
            net_usd = c["net"] * to_usd
            reb_usd = (
                vol_usd * REBATE_RATE if _pair_has_rebate(pair, quote_currency) else 0
            )
            adj_usd = net_usd + reb_usd
            short = _short_name(c["id"], pair)
            rows.append((short, vol_usd, net_usd, adj_usd))

        # Calculate column widths
        name_w = max(len(r[0]) for r in rows)
        vol_strs = [f"${_fmt(r[1])}" for r in rows]
        pnl_strs = [f"{_sign(r[2])}${_fmt(abs(r[2]))}" for r in rows]
        adj_strs = [f"{_sign(r[3])}${_fmt(abs(r[3]))}" for r in rows]

        vol_w = max(len(s) for s in vol_strs)
        pnl_w = max(len(s) for s in pnl_strs)
        adj_w = max(len(s) for s in adj_strs)

        # Header + separator + rows inside a code block
        hdr = (
            f"{'Name':<{name_w}}  {'Vol':>{vol_w}}  {'PnL':>{pnl_w}}  {'Adj':>{adj_w}}"
        )
        sep = "─" * len(hdr)

        table_lines = [hdr, sep]
        for i, (short, vol_usd, net_usd, adj_usd) in enumerate(rows):
            e = "+" if adj_usd >= 0 else "-"
            table_lines.append(
                f"{short:<{name_w}}  {vol_strs[i]:>{vol_w}}  {pnl_strs[i]:>{pnl_w}}  {adj_strs[i]:>{adj_w}}"
            )

        # Pair subtotal row
        sub_vol = f"${_fmt(grp_vol)}"
        sub_pnl = f"{_sign(grp_net)}${_fmt(abs(grp_net))}"
        sub_adj = f"{_sign(grp_adj)}${_fmt(abs(grp_adj))}"
        table_lines.append(sep)
        table_lines.append(
            f"{'TOTAL':<{name_w}}  {sub_vol:>{vol_w}}  {sub_pnl:>{pnl_w}}  {sub_adj:>{adj_w}}"
        )

        msg2_parts.append("```")
        msg2_parts.extend(table_lines)
        msg2_parts.append("```")

    return ["\n".join(msg1_parts), "\n".join(msg2_parts)]


# ---------------------------------------------------------------------------
# Telegram HTTP fallback (when context.bot is unavailable)
# ---------------------------------------------------------------------------


async def _send_telegram_http(
    chat_id: int, text: str, parse_mode: str = "MarkdownV2"
) -> bool:
    """Send a message via Telegram HTTP API directly. Used as fallback."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set, cannot send via HTTP fallback")
        return False
    try:
        import httpx

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        async with httpx.AsyncClient(timeout=10) as http_client:
            resp = await http_client.post(url, json=payload)
            data = resp.json()
            if data.get("ok"):
                return True
            # Retry without parse_mode if formatting fails
            if "can't parse" in data.get("description", "").lower():
                payload.pop("parse_mode")
                resp = await http_client.post(url, json=payload)
                data = resp.json()
                if data.get("ok"):
                    return True
            logger.error(f"Telegram HTTP send failed: {data.get('description')}")
    except Exception as e:
        logger.error(f"Telegram HTTP fallback error: {e}")
    return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = context._chat_id if hasattr(context, "_chat_id") else None
    if not chat_id:
        return "No chat_id available"

    # Auto-resolve target_chat_id from notes if not explicitly set
    send_to = config.target_chat_id
    if not send_to:
        try:
            from condor.preferences import get_note

            user_data = context.user_data if hasattr(context, "user_data") else {}
            # Look up note for the active server's group chat
            server_name = (
                config.server_name
                or get_config_manager().get_chat_default_server(chat_id)
            )
            if server_name:
                note_val = get_note(user_data, f"server.{server_name}.group_chat_id")
                if note_val:
                    send_to = int(note_val)
        except Exception:
            pass
    if not send_to:
        # Fallback to CONDOR_CHAT_ID env var (the invoking user's chat)
        env_chat = os.environ.get("CONDOR_CHAT_ID", "")
        send_to = int(env_chat) if env_chat else chat_id

    # Connect to the specified server (default: brigado_2 where bots run)
    if config.server_name:
        client = await get_config_manager().get_client(config.server_name)
    else:
        client = await get_client(chat_id, context=context)
    if not client:
        return "Could not connect to API server"

    bots_data, bot_runs, balances = await asyncio.gather(
        _get_bots_data(client),
        _get_bot_runs(client),
        _get_balances(client),
    )

    if not bots_data:
        await context.bot.send_message(chat_id=send_to, text="No active bots found.")
        return "No active bots"

    # Fetch controller configs for trading_pair
    ctrl_configs = {}
    for bot_name in bots_data:
        try:
            configs = await client.controllers.get_bot_controller_configs(bot_name)
            if isinstance(configs, list):
                for cfg in configs:
                    cid = cfg.get("id") or cfg.get("controller_id", "")
                    if cid:
                        ctrl_configs[cid] = cfg
        except Exception:
            pass

    # Determine trading pairs and connector
    trading_pairs = set()
    connector = "binance"
    for bot_data in bots_data.values():
        if not isinstance(bot_data, dict):
            continue
        for ctrl_name, ctrl_data in bot_data.get("performance", {}).items():
            if not isinstance(ctrl_data, dict):
                continue
            cfg = ctrl_configs.get(ctrl_name, {})
            perf = ctrl_data.get("performance", ctrl_data)
            pair = cfg.get("trading_pair", "") or perf.get("trading_pair", "")
            if pair:
                trading_pairs.add(pair)
            conn = cfg.get("connector_name", "") or perf.get("connector_name", "")
            if conn:
                connector = conn.split("_")[0]

    # Build price pairs for USD conversion
    price_pairs = list(trading_pairs)
    for bt in {p.split("-")[0] for p in trading_pairs if "-" in p}:
        if f"{bt}-USDT" not in price_pairs:
            price_pairs.append(f"{bt}-USDT")
    for qt in {p.split("-")[-1] for p in trading_pairs if "-" in p}:
        if qt not in ("USDT", "USD") and f"USDT-{qt}" not in price_pairs:
            price_pairs.append(f"USDT-{qt}")

    prices = await _get_prices(client, connector, price_pairs)

    # FX rate fallback
    qc = config.quote_currency.upper()
    fx_pair = f"USDT-{qc}" if qc not in ("USD", "USDT") else ""
    if fx_pair and not prices.get(fx_pair):
        fx_prices = await _get_prices(client, "binance", [fx_pair])
        prices.update(fx_prices)

    # Market volumes since deploy
    earliest_dt = None
    for bn in bots_data:
        ts = bot_runs.get(bn)
        if ts:
            try:
                ts_str = str(ts)
                if ts_str.endswith("Z"):
                    ts_str = ts_str[:-1] + "+00:00"
                dt = datetime.fromisoformat(ts_str)
                if earliest_dt is None or dt < earliest_dt:
                    earliest_dt = dt
            except (ValueError, TypeError):
                pass

    volumes_since = {}
    vol_tasks = {
        pair: _get_market_volume_since(client, connector, pair, earliest_dt)
        for pair in trading_pairs
    }
    vol_results = await asyncio.gather(*vol_tasks.values())
    for pair, vol_tuple in zip(vol_tasks.keys(), vol_results):
        volumes_since[pair] = vol_tuple

    # Quote → USD rate
    quote_usd_rate = 1.0
    if qc not in ("USD", "USDT"):
        usdt_quote = prices.get(f"USDT-{qc}", 0)
        if usdt_quote and usdt_quote > 0:
            quote_usd_rate = 1.0 / usdt_quote
        else:
            quote_usdt = prices.get(f"{qc}-USDT", 0)
            if quote_usdt and quote_usdt > 0:
                quote_usd_rate = quote_usdt

    # Build and send
    messages = _build_report(
        bots_data,
        bot_runs,
        prices,
        volumes_since,
        quote_usd_rate,
        quote_currency=qc,
        ctrl_configs=ctrl_configs,
        balances=balances,
    )

    sent = 0
    for i, msg in enumerate(messages):
        if i > 0:
            await asyncio.sleep(1)
        for attempt in range(3):
            try:
                await context.bot.send_message(
                    chat_id=send_to,
                    text=msg,
                    parse_mode="MarkdownV2",
                )
                sent += 1
                break
            except Exception as e:
                err = str(e).lower()
                if "flood control" in err or "retry in" in err:
                    match = re.search(r"retry in (\d+)", err)
                    wait = int(match.group(1)) + 1 if match else 10
                    await asyncio.sleep(wait)
                else:
                    logger.error(f"Failed to send msg {i} via bot: {e}")
                    # Fallback: send via HTTP directly to Telegram API
                    if await _send_telegram_http(send_to, msg):
                        sent += 1
                    break

    # Strip MarkdownV2 escaping for plain-text return
    def _strip_md(text: str) -> str:
        return text.replace("\\", "")

    plain_report = "\n\n".join(_strip_md(m) for m in messages)

    from condor.reports import ReportBuilder

    builder = ReportBuilder("Trading Report")
    builder.source("routine", "bot_report").tags(["bots", "performance"])
    builder.manual_order()

    # ---- Parse controller data for charts ----
    controllers_parsed = []
    for bot_name, bot_data in bots_data.items():
        if isinstance(bot_data, str):
            continue
        performance = {}
        if isinstance(bot_data, dict):
            performance = bot_data.get("performance", {})
        elif isinstance(bot_data, list):
            performance = {
                f"ctrl_{i}": {"performance": c} for i, c in enumerate(bot_data)
            }
        for ctrl_name, ctrl_data in performance.items():
            if not isinstance(ctrl_data, dict):
                continue
            perf = ctrl_data.get("performance", ctrl_data)
            cfg = ctrl_configs.get(ctrl_name, {})
            trading_pair = cfg.get("trading_pair", "") or perf.get("trading_pair", "")
            vol = float(perf.get("volume_traded", 0) or 0)
            realized = float(perf.get("realized_pnl_quote", 0) or 0)
            unrealized = float(perf.get("unrealized_pnl_quote", 0) or 0)
            total_amount_quote = float(cfg.get("total_amount_quote", 0) or 0)
            controllers_parsed.append(
                {
                    "id": ctrl_name,
                    "bot": bot_name,
                    "pair": trading_pair,
                    "vol": vol,
                    "realized": realized,
                    "unrealized": unrealized,
                    "net": realized + unrealized,
                    "total_amount_quote": total_amount_quote,
                }
            )

    # ---- Compute totals for KPIs ----
    total_vol_usd = sum(_vol_to_usd(c, quote_usd_rate) for c in controllers_parsed)
    total_realized_usd = sum(c["realized"] for c in controllers_parsed) * quote_usd_rate
    total_unrealized_usd = (
        sum(c["unrealized"] for c in controllers_parsed) * quote_usd_rate
    )
    total_net_usd = total_realized_usd + total_unrealized_usd
    rebate_vol_usd = sum(
        _vol_to_usd(c, quote_usd_rate)
        for c in controllers_parsed
        if _pair_has_rebate(c["pair"], qc)
    )
    total_rebate_usd = rebate_vol_usd * REBATE_RATE
    net_after_rebate_usd = total_net_usd + total_rebate_usd
    num_bots = len({c["bot"] for c in controllers_parsed})
    num_ctrls = len(controllers_parsed)

    # Runtime
    earliest_deploy = None
    for bn in {c["bot"] for c in controllers_parsed}:
        ts = bot_runs.get(bn)
        if ts:
            try:
                ts_str = str(ts)
                if ts_str.endswith("Z"):
                    ts_str = ts_str[:-1] + "+00:00"
                dt = datetime.fromisoformat(ts_str)
                if earliest_deploy is None or dt < earliest_deploy:
                    earliest_deploy = dt
            except (ValueError, TypeError):
                pass
    now_utc = datetime.now(timezone.utc)
    runtime_h = (
        (now_utc - earliest_deploy).total_seconds() / 3600 if earliest_deploy else 0
    )

    # Balance
    total_balance_usd = 0.0
    if balances:
        for account_data in balances.values():
            for connector_balances in account_data.values():
                if not isinstance(connector_balances, list):
                    continue
                for b in connector_balances:
                    total_balance_usd += float(b.get("value", 0) or 0)

    # ---- KPI cards ----
    pnl_trend = "up" if net_after_rebate_usd >= 0 else "down"
    builder.kpi(
        "Net PnL + Rebate",
        f"{'+'if net_after_rebate_usd>=0 else ''}${_fmt(abs(net_after_rebate_usd))}",
        delta=f"${_fmt(abs(net_after_rebate_usd / runtime_h) if runtime_h else 0)}/hr",
        trend=pnl_trend,
    )
    total_capital_usd = sum(
        _amount_quote_to_usd(c, quote_usd_rate) for c in controllers_parsed
    )
    builder.kpi(
        "Volume",
        f"${_fmt(total_vol_usd)}",
        delta=f"${_fmt(total_vol_usd / runtime_h if runtime_h else 0)}/hr",
    )
    builder.kpi(
        "Rebate",
        f"${_fmt(total_rebate_usd)}",
        delta=f"${_fmt(total_rebate_usd / runtime_h if runtime_h else 0)}/hr",
        trend="up",
    )
    if total_capital_usd > 0:
        builder.kpi(
            "Capital Deployed",
            f"${_fmt(total_capital_usd)}",
            delta=f"{num_ctrls} controllers",
        )
    builder.kpi("Balance", f"${_fmt(total_balance_usd)}")
    builder.kpi(
        "Runtime",
        f"{runtime_h:.1f}h",
        delta=f"{num_bots} bots, {num_ctrls} controllers",
    )

    # ---- Charts ----
    if controllers_parsed:
        builder.plotly(
            _build_pair_overview_chart(
                controllers_parsed, quote_usd_rate, volumes_since, prices, qc
            )
        )
        builder.plotly(
            _build_pnl_chart(controllers_parsed, quote_usd_rate, qc, ctrl_configs)
        )

    # ---- Controller details table ----
    ctrl_rows = []
    for c in sorted(
        controllers_parsed, key=lambda x: x["net"] * quote_usd_rate, reverse=True
    ):
        vol_usd = _vol_to_usd(c, quote_usd_rate)
        net_usd = c["net"] * quote_usd_rate
        reb_usd = vol_usd * REBATE_RATE if _pair_has_rebate(c["pair"], qc) else 0
        adj_usd = net_usd + reb_usd
        cap_usd = _amount_quote_to_usd(c, quote_usd_rate)
        ctrl_rows.append(
            {
                "Controller": _short_chart_name(c["id"], c["pair"]),
                "Pair": c["pair"],
                "Capital": f"${_fmt(cap_usd)}" if cap_usd > 0 else "-",
                "Volume": f"${_fmt(vol_usd)}",
                "Realized": f"{'+'if c['realized']*quote_usd_rate>=0 else '-'}${_fmt(abs(c['realized']*quote_usd_rate))}",
                "Unrealized": f"{'+'if c['unrealized']*quote_usd_rate>=0 else '-'}${_fmt(abs(c['unrealized']*quote_usd_rate))}",
                "Rebate": f"+${_fmt(reb_usd)}",
                "Total": f"{'+'if adj_usd>=0 else '-'}${_fmt(abs(adj_usd))}",
            }
        )
    if ctrl_rows:
        builder.table(ctrl_rows)

    # ---- Pair summary table ----
    pair_groups: dict[str, list[dict]] = defaultdict(list)
    for c in controllers_parsed:
        pair_groups[c["pair"] or "unknown"].append(c)
    pair_rows = []
    for pair, ctrls in sorted(
        pair_groups.items(), key=lambda x: sum(c["vol"] for c in x[1]), reverse=True
    ):
        grp_vol = sum(_vol_to_usd(c, quote_usd_rate) for c in ctrls)
        grp_realized = sum(c["realized"] for c in ctrls) * quote_usd_rate
        grp_unrealized = sum(c["unrealized"] for c in ctrls) * quote_usd_rate
        grp_net = grp_realized + grp_unrealized
        grp_rebate = grp_vol * REBATE_RATE if _pair_has_rebate(pair, qc) else 0
        grp_adj = grp_net + grp_rebate
        # Market share
        vol_data = volumes_since.get(pair, (0, 0))
        vol_base, vol_quote = vol_data if isinstance(vol_data, tuple) else (vol_data, 0)
        mkt_usd = _market_vol_to_usd(pair, vol_base, vol_quote, quote_usd_rate, prices)
        share = f"{grp_vol / mkt_usd * 100:.2f}%" if mkt_usd > 0 else "N/A"
        grp_capital = sum(_amount_quote_to_usd(c, quote_usd_rate) for c in ctrls)
        pair_rows.append(
            {
                "Pair": pair,
                "Controllers": str(len(ctrls)),
                "Capital": f"${_fmt(grp_capital)}" if grp_capital > 0 else "-",
                "Volume": f"${_fmt(grp_vol)}",
                "Realized": f"{'+'if grp_realized>=0 else '-'}${_fmt(abs(grp_realized))}",
                "Net PnL": f"{'+'if grp_net>=0 else '-'}${_fmt(abs(grp_net))}",
                "Rebate": f"+${_fmt(grp_rebate)}",
                "Adjusted": f"{'+'if grp_adj>=0 else '-'}${_fmt(abs(grp_adj))}",
                "Mkt Share": share,
            }
        )
    if pair_rows:
        builder.table(pair_rows)

    # ---- Balance table ----
    if balances:
        token_totals: dict[str, float] = defaultdict(float)
        token_values: dict[str, float] = defaultdict(float)
        for account_data in balances.values():
            for connector_balances in account_data.values():
                if not isinstance(connector_balances, list):
                    continue
                for b in connector_balances:
                    token = b.get("token", "")
                    value = float(b.get("value", 0) or 0)
                    units = float(b.get("units", 0) or 0)
                    if value > 0:
                        token_totals[token] += units
                        token_values[token] += value
        if token_values:
            bal_rows = []
            for token, value in sorted(
                token_values.items(), key=lambda x: x[1], reverse=True
            ):
                pct = (value / total_balance_usd * 100) if total_balance_usd > 0 else 0
                bal_rows.append(
                    {
                        "Token": token,
                        "Amount": _fmt(token_totals[token], 4),
                        "Value (USD)": f"${_fmt(value)}",
                        "Weight": f"{pct:.1f}%",
                    }
                )
            builder.table(bal_rows)

    # ---- Config Analysis section ----
    if len(ctrl_configs) > 1:
        all_params = set()
        for cfg in ctrl_configs.values():
            all_params.update(cfg.keys())
        all_params -= _CONFIG_SKIP_PARAMS
        sorted_params = sorted(
            all_params, key=lambda p: (0 if p in _CONFIG_KEY_PARAMS else 1, p)
        )

        # Group configs by pair
        cfg_pair_groups: dict[str, dict] = defaultdict(dict)
        for cid, cfg in ctrl_configs.items():
            pair = cfg.get("trading_pair", "unknown")
            cfg_pair_groups[pair][cid] = cfg

        # Count total diffs
        total_diffs = 0
        pair_sections = []
        for pair, pair_cfgs in sorted(
            cfg_pair_groups.items(), key=lambda x: -len(x[1])
        ):
            if len(pair_cfgs) < 2:
                continue
            labels = list(pair_cfgs.keys())
            shorts = [_short_chart_name(l, pair) for l in labels]

            diff_rows = []
            for param in sorted_params:
                values = [_cfg_normalize(pair_cfgs[l].get(param)) for l in labels]
                non_none = [v for v in values if v is not None]
                if len(non_none) < 2 or len(set(str(v) for v in non_none)) <= 1:
                    continue
                is_key = param in _CONFIG_KEY_PARAMS
                row = {"Parameter": f"{'🔴 ' if is_key else '🟡 '}{param}"}
                for short, label in zip(shorts, labels):
                    row[short] = _cfg_fmt_value(pair_cfgs[label].get(param))
                diff_rows.append(row)

            if diff_rows:
                total_diffs += len(diff_rows)
                pair_sections.append((pair, len(pair_cfgs), diff_rows))

        if pair_sections:
            builder.markdown(
                "## Config Analysis\n\nParameter differences across running controllers. 🔴 = key tuning param, 🟡 = other."
            )
            for pair, n_cfgs, rows in pair_sections:
                builder.markdown(
                    f"### {pair} — {len(rows)} diffs across {n_cfgs} controllers"
                )
                builder.table(rows)

        # Config charts
        labeled_configs = {f"\U0001f7e2 {k}": v for k, v in ctrl_configs.items()}
        running_ids = set(ctrl_configs.keys())

        fig_pc = _build_config_parallel_coords(labeled_configs, running_ids)
        if fig_pc:
            if not pair_sections:
                builder.markdown("## Config Analysis")
            builder.plotly(fig_pc)

        fig_spread = _build_config_spread_chart(labeled_configs)
        if fig_spread:
            builder.plotly(fig_spread)

    await builder.save()

    return f"Report sent: {sent}/{len(messages)} messages\n\n{plain_report}"
