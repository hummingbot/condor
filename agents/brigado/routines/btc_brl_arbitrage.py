"""BTC-BRL arbitrage: spread analysis + quoting strategy simulation."""

CATEGORY = "Market Data"

import asyncio
import io
import logging

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

COLORS = {
    "bg_paper": "#0d1117",
    "bg_plot": "#161b22",
    "grid": "#21262d",
    "text": "#c9d1d9",
    "text_dim": "#8b949e",
    "usdt": "#f0b90b",
    "brl": "#3fb950",
    "spread": "#bc8cff",
    "long": "#3fb950",
    "short": "#f85149",
    "pnl": "#58a6ff",
    "fees": "#f78166",
}

LAYOUT_BASE = dict(
    paper_bgcolor=COLORS["bg_paper"],
    plot_bgcolor=COLORS["bg_plot"],
    font=dict(color=COLORS["text_dim"], size=10),
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.05,
        xanchor="center",
        x=0.5,
        font=dict(size=10, color=COLORS["text"]),
        bgcolor="rgba(0,0,0,0)",
    ),
    margin=dict(l=60, r=30, t=100, b=40),
)


class Config(BaseModel):
    """BTC-BRL arbitrage: spread analysis + quoting strategy simulation."""

    connector_name: str = Field(default="binance", description="Exchange connector")
    lookback_seconds: int = Field(
        default=3600, description="1s candles to fetch (3600=1h)"
    )
    trade_size_usdt: float = Field(
        default=100.0, description="Notional per trade in USDT"
    )
    fee_bps: float = Field(
        default=10.0, description="Round-trip fee in bps (maker+taker)"
    )
    min_spread_bps: float = Field(default=2.0, description="Min spread to enter (bps)")
    max_position_usdt: float = Field(
        default=5000.0, description="Max accumulated position (USDT)"
    )


async def _fetch_candles(
    client, connector: str, pair: str, interval: str, max_records: int
) -> pd.DataFrame:
    result = await client.market_data.get_candles(
        connector, pair, interval=interval, max_records=max_records
    )
    if isinstance(result, list):
        records = result
    elif isinstance(result, dict):
        records = result.get("data", result.get("candles", []))
    else:
        records = []
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    for col in ("open", "high", "low", "close", "volume", "quote_asset_volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    ts = df["timestamp"]
    unit = "s" if ts.iloc[0] > 1e12 / 1000 else "ms"
    df["datetime"] = pd.to_datetime(ts, unit=unit, utc=True)
    return df.sort_values("datetime").reset_index(drop=True)


def _style_axes(fig, rows):
    for row in range(1, rows + 1):
        fig.update_yaxes(
            gridcolor=COLORS["grid"], zerolinecolor=COLORS["grid"], row=row, col=1
        )
        fig.update_xaxes(gridcolor=COLORS["grid"], row=row, col=1)


def _merge_spread(
    price_usdt: pd.DataFrame, price_brl: pd.DataFrame, usdtbrl: pd.DataFrame
) -> pd.DataFrame:
    """Merge 1s candles and compute spread %."""
    for df in (price_usdt, price_brl, usdtbrl):
        df["ts"] = df["datetime"].dt.floor("s")

    merged = pd.merge(
        price_usdt[["ts", "close"]].rename(columns={"close": "btc_usdt"}),
        price_brl[["ts", "close"]].rename(columns={"close": "btc_brl"}),
        on="ts",
        how="inner",
    )
    merged = pd.merge(
        merged,
        usdtbrl[["ts", "close"]].rename(columns={"close": "usdt_brl_rate"}),
        on="ts",
        how="inner",
    )
    merged["btc_brl_usd"] = merged["btc_brl"] / merged["usdt_brl_rate"]
    merged["spread_pct"] = (
        (merged["btc_brl_usd"] - merged["btc_usdt"]) / merged["btc_usdt"]
    ) * 100
    merged["spread_bps"] = merged["spread_pct"] * 100
    return merged.sort_values("ts").reset_index(drop=True)


def simulate_quoting(
    merged: pd.DataFrame,
    trade_size_usdt: float,
    fee_bps: float,
    min_spread_bps: float,
    max_position_usdt: float,
) -> pd.DataFrame:
    """Simulate a one-sided quoting strategy that captures spread.

    Logic:
    - When BRL premium (spread > min_spread): sell on BRL side (capture premium)
      → accumulate short BRL / long USDT exposure
    - When USDT premium (spread < -min_spread): sell on USDT side (capture premium)
      → accumulate short USDT / long BRL exposure
    - Position flips naturally as spread oscillates
    - Fee deducted on each trade
    """
    n = len(merged)
    fee_pct = fee_bps / 10000

    # Track state
    position_usdt = np.zeros(
        n
    )  # net position in USDT terms (+ = long BRL, - = short BRL)
    cum_captured = np.zeros(n)  # cumulative spread captured (before fees)
    cum_fees = np.zeros(n)  # cumulative fees paid
    cum_pnl = np.zeros(n)  # net PnL (captured - fees)
    trade_count = np.zeros(n, dtype=int)
    actions = [""] * n

    pos = 0.0
    captured = 0.0
    fees = 0.0
    trades = 0

    for i in range(n):
        spread_bps = merged["spread_bps"].iloc[i]
        abs_spread = abs(spread_bps)

        if abs_spread > min_spread_bps:
            # Check position limits
            if spread_bps > 0 and pos > -max_position_usdt:
                # BRL premium → sell on BRL (go short BRL exposure)
                trade_pnl = (abs_spread / 10000) * trade_size_usdt
                trade_fee = fee_pct * trade_size_usdt
                captured += trade_pnl
                fees += trade_fee
                pos -= trade_size_usdt
                trades += 1
                actions[i] = "sell_brl"
            elif spread_bps < 0 and pos < max_position_usdt:
                # USDT premium → sell on USDT (go long BRL exposure)
                trade_pnl = (abs_spread / 10000) * trade_size_usdt
                trade_fee = fee_pct * trade_size_usdt
                captured += trade_pnl
                fees += trade_fee
                pos += trade_size_usdt
                trades += 1
                actions[i] = "sell_usdt"

        position_usdt[i] = pos
        cum_captured[i] = captured
        cum_fees[i] = fees
        cum_pnl[i] = captured - fees
        trade_count[i] = trades

    merged = merged.copy()
    merged["position_usdt"] = position_usdt
    merged["cum_captured"] = cum_captured
    merged["cum_fees"] = cum_fees
    merged["cum_pnl"] = cum_pnl
    merged["trade_count"] = trade_count
    merged["action"] = actions

    # Mark-to-market: unrealized PnL from position * current spread change
    # Position was entered at various spreads; approximate with running avg
    merged["unrealized"] = 0.0
    avg_entry_spread = 0.0
    total_pos = 0.0
    for i in range(n):
        if actions[i]:
            entry_spread = merged["spread_bps"].iloc[i]
            trade_pos = (
                trade_size_usdt if actions[i] == "sell_usdt" else -trade_size_usdt
            )
            if abs(total_pos + trade_pos) > 0.01:
                avg_entry_spread = (
                    avg_entry_spread * total_pos + entry_spread * trade_pos
                ) / (total_pos + trade_pos)
            total_pos += trade_pos
        current_spread = merged["spread_bps"].iloc[i]
        # If short BRL (pos < 0), we profit when spread decreases
        # If long BRL (pos > 0), we profit when spread increases
        spread_change = current_spread - avg_entry_spread
        merged.loc[merged.index[i], "unrealized"] = (spread_change / 10000) * total_pos

    merged["total_pnl"] = merged["cum_pnl"] + merged["unrealized"]

    return merged


def build_spread_chart(merged: pd.DataFrame) -> go.Figure:
    """Chart 1: Raw spread analysis."""
    avg_spread = merged["spread_pct"].mean()
    curr_spread = merged["spread_pct"].iloc[-1]
    std_spread = merged["spread_pct"].std()
    max_spread = merged["spread_pct"].max()
    min_spread = merged["spread_pct"].min()
    current_btc = float(merged["btc_usdt"].iloc[-1])

    fig = make_subplots(
        rows=2,
        cols=1,
        row_heights=[0.55, 0.45],
        vertical_spacing=0.08,
        subplot_titles=[
            "BTC: USDT vs BRL→USD (1s)",
            f"Spread % — avg {avg_spread:+.4f}%, std {std_spread:.4f}%",
        ],
    )

    fig.add_trace(
        go.Scatter(
            x=merged["ts"],
            y=merged["btc_usdt"],
            line=dict(color=COLORS["usdt"], width=1.5),
            name="BTC-USDT",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=merged["ts"],
            y=merged["btc_brl_usd"],
            line=dict(color=COLORS["brl"], width=1.5),
            name="BTC-BRL (USD)",
        ),
        row=1,
        col=1,
    )

    # Spread with color fill
    fig.add_trace(
        go.Scatter(
            x=merged["ts"],
            y=merged["spread_pct"],
            line=dict(color=COLORS["spread"], width=1.2),
            fill="tozeroy",
            fillcolor="rgba(188,140,255,0.08)",
            name="Spread %",
        ),
        row=2,
        col=1,
    )
    fig.add_hline(y=0, line_color=COLORS["grid"], row=2, col=1)
    fig.add_hline(
        y=avg_spread,
        line_dash="dash",
        line_color=COLORS["text_dim"],
        opacity=0.6,
        row=2,
        col=1,
        annotation_text=f"avg {avg_spread:+.4f}%",
        annotation_font_color=COLORS["text_dim"],
    )

    fig.update_layout(
        **LAYOUT_BASE,
        title=dict(
            text=(
                f"<b>BTC-BRL Spread</b>  |  BTC: ${current_btc:,.0f}"
                f"  |  Now: {curr_spread:+.4f}%"
                f"  |  Range: [{min_spread:+.4f}%, {max_spread:+.4f}%]"
            ),
            font=dict(color=COLORS["text"], size=13),
            x=0.5,
        ),
        width=1300,
        height=700,
    )
    fig.update_yaxes(title_text="USD", tickformat=",.0f", row=1, col=1)
    fig.update_yaxes(title_text="%", tickformat=".4f", row=2, col=1)
    for ann in fig.layout.annotations:
        ann.font = dict(color=COLORS["text_dim"], size=11)
    _style_axes(fig, 2)
    return fig


def build_strategy_chart(
    sim: pd.DataFrame, fee_bps: float, min_spread_bps: float, trade_size_usdt: float
) -> go.Figure:
    """Chart 2: Strategy simulation results."""
    total_trades = int(sim["trade_count"].iloc[-1])
    final_pnl = sim["cum_pnl"].iloc[-1]
    final_captured = sim["cum_captured"].iloc[-1]
    final_fees = sim["cum_fees"].iloc[-1]
    final_position = sim["position_usdt"].iloc[-1]
    final_total = sim["total_pnl"].iloc[-1]
    duration_min = (sim["ts"].iloc[-1] - sim["ts"].iloc[0]).total_seconds() / 60

    fig = make_subplots(
        rows=3,
        cols=1,
        row_heights=[0.4, 0.3, 0.3],
        vertical_spacing=0.06,
        subplot_titles=[
            f"Cumulative PnL — {total_trades} trades in {duration_min:.0f}min",
            f"Net Position (max ${sim['position_usdt'].abs().max():,.0f})",
            "Spread (bps) + Trade Signals",
        ],
    )

    # Row 1: PnL breakdown
    fig.add_trace(
        go.Scatter(
            x=sim["ts"],
            y=sim["cum_captured"],
            line=dict(color=COLORS["brl"], width=1.5),
            name="Captured (gross)",
            fill="tozeroy",
            fillcolor="rgba(63,185,80,0.08)",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=sim["ts"],
            y=sim["cum_fees"],
            line=dict(color=COLORS["fees"], width=1, dash="dot"),
            name="Fees paid",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=sim["ts"],
            y=sim["cum_pnl"],
            line=dict(color=COLORS["pnl"], width=2),
            name="Net PnL (realized)",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=sim["ts"],
            y=sim["total_pnl"],
            line=dict(color=COLORS["spread"], width=1.5, dash="dash"),
            name="Total PnL (realized + unrealized)",
        ),
        row=1,
        col=1,
    )

    # Row 2: Position
    pos_colors = [
        COLORS["long"] if v >= 0 else COLORS["short"] for v in sim["position_usdt"]
    ]
    fig.add_trace(
        go.Scatter(
            x=sim["ts"],
            y=sim["position_usdt"],
            line=dict(color=COLORS["pnl"], width=1.5),
            fill="tozeroy",
            fillcolor="rgba(88,166,255,0.08)",
            name="Position (USDT)",
            showlegend=True,
        ),
        row=2,
        col=1,
    )
    fig.add_hline(y=0, line_color=COLORS["grid"], row=2, col=1)

    # Row 3: Spread + signals
    fig.add_trace(
        go.Scatter(
            x=sim["ts"],
            y=sim["spread_bps"],
            line=dict(color=COLORS["spread"], width=1),
            name="Spread (bps)",
            showlegend=True,
        ),
        row=3,
        col=1,
    )
    fig.add_hline(
        y=min_spread_bps,
        line_dash="dash",
        line_color=COLORS["brl"],
        opacity=0.5,
        row=3,
        col=1,
    )
    fig.add_hline(
        y=-min_spread_bps,
        line_dash="dash",
        line_color=COLORS["brl"],
        opacity=0.5,
        row=3,
        col=1,
    )
    fig.add_hline(y=0, line_color=COLORS["grid"], row=3, col=1)

    # Trade markers
    sells_brl = sim[sim["action"] == "sell_brl"]
    sells_usdt = sim[sim["action"] == "sell_usdt"]
    if not sells_brl.empty:
        fig.add_trace(
            go.Scatter(
                x=sells_brl["ts"],
                y=sells_brl["spread_bps"],
                mode="markers",
                marker=dict(color=COLORS["short"], size=3, symbol="triangle-down"),
                name="Sell BRL",
                showlegend=True,
            ),
            row=3,
            col=1,
        )
    if not sells_usdt.empty:
        fig.add_trace(
            go.Scatter(
                x=sells_usdt["ts"],
                y=sells_usdt["spread_bps"],
                mode="markers",
                marker=dict(color=COLORS["long"], size=3, symbol="triangle-up"),
                name="Sell USDT",
                showlegend=True,
            ),
            row=3,
            col=1,
        )

    fig.update_layout(
        **LAYOUT_BASE,
        title=dict(
            text=(
                f"<b>Quoting Strategy Sim</b>"
                f"  |  ${trade_size_usdt:.0f}/trade"
                f"  |  fee: {fee_bps:.0f}bps"
                f"  |  min: {min_spread_bps:.0f}bps"
                f"  |  PnL: ${final_pnl:+.2f}"
                f"  |  Pos: ${final_position:+,.0f}"
            ),
            font=dict(color=COLORS["text"], size=12),
            x=0.5,
        ),
        width=1300,
        height=900,
    )
    fig.update_yaxes(title_text="USDT", tickformat=",.2f", row=1, col=1)
    fig.update_yaxes(title_text="USDT", tickformat=",.0f", row=2, col=1)
    fig.update_yaxes(title_text="bps", row=3, col=1)
    for ann in fig.layout.annotations:
        ann.font = dict(color=COLORS["text_dim"], size=11)
    _style_axes(fig, 3)
    return fig


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> RoutineResult:
    chat_id = context._chat_id
    client = await get_client(chat_id, context=context)
    if not client:
        return RoutineResult(text="No server available")

    # Fetch 1s candles in parallel
    btc_usdt, btc_brl, usdt_brl = await asyncio.gather(
        _fetch_candles(
            client, config.connector_name, "BTC-USDT", "1s", config.lookback_seconds
        ),
        _fetch_candles(
            client, config.connector_name, "BTC-BRL", "1s", config.lookback_seconds
        ),
        _fetch_candles(
            client, config.connector_name, "USDT-BRL", "1s", config.lookback_seconds
        ),
    )

    for name, df in [
        ("BTC-USDT", btc_usdt),
        ("BTC-BRL", btc_brl),
        ("USDT-BRL", usdt_brl),
    ]:
        if df.empty:
            return RoutineResult(text=f"No 1s data for {name}")

    # Compute spread
    merged = _merge_spread(btc_usdt, btc_brl, usdt_brl)
    if merged.empty:
        return RoutineResult(text="No overlapping data between pairs")

    # Run strategy simulation
    sim = simulate_quoting(
        merged,
        trade_size_usdt=config.trade_size_usdt,
        fee_bps=config.fee_bps,
        min_spread_bps=config.min_spread_bps,
        max_position_usdt=config.max_position_usdt,
    )

    # Build charts
    spread_fig = build_spread_chart(merged)
    strategy_fig = build_strategy_chart(
        sim, config.fee_bps, config.min_spread_bps, config.trade_size_usdt
    )

    # Summary stats
    duration_min = (merged["ts"].iloc[-1] - merged["ts"].iloc[0]).total_seconds() / 60
    avg_spread = merged["spread_pct"].mean()
    std_spread = merged["spread_pct"].std()
    curr_spread = merged["spread_pct"].iloc[-1]
    total_trades = int(sim["trade_count"].iloc[-1])
    final_captured = sim["cum_captured"].iloc[-1]
    final_fees = sim["cum_fees"].iloc[-1]
    final_pnl = sim["cum_pnl"].iloc[-1]
    final_position = sim["position_usdt"].iloc[-1]
    final_total = sim["total_pnl"].iloc[-1]

    # Spread distribution
    above_thresh = (merged["spread_bps"].abs() > config.min_spread_bps).mean() * 100
    time_positive = (merged["spread_pct"] > 0).mean() * 100

    # Annualize PnL
    hours = duration_min / 60
    if hours > 0:
        pnl_per_hour = final_pnl / hours
        projected_daily = pnl_per_hour * 24
        projected_monthly = projected_daily * 30
    else:
        pnl_per_hour = projected_daily = projected_monthly = 0

    text = "\n".join(
        [
            "BTC-BRL Arbitrage Analysis",
            f"Window: {duration_min:.0f} min ({len(merged):,} ticks)",
            "",
            "Spread Stats:",
            f"  Current: {curr_spread:+.4f}%  ({curr_spread*100:+.2f} bps)",
            f"  Average: {avg_spread:+.4f}%",
            f"  Std Dev: {std_spread:.4f}%",
            f"  Time |spread| > {config.min_spread_bps:.0f}bps: {above_thresh:.1f}%",
            f"  Time BRL premium: {time_positive:.1f}%",
            "",
            f"Strategy Sim (${config.trade_size_usdt:.0f}/trade, {config.fee_bps:.0f}bps fee, >{config.min_spread_bps:.0f}bps):",
            f"  Trades: {total_trades}",
            f"  Gross captured: ${final_captured:.2f}",
            f"  Fees paid: ${final_fees:.2f}",
            f"  Net realized PnL: ${final_pnl:+.2f}",
            f"  Current position: ${final_position:+,.0f}",
            f"  Total PnL (incl. unrealized): ${final_total:+.2f}",
            "",
            "Projections (if conditions persist):",
            f"  Per hour: ${pnl_per_hour:+.2f}",
            f"  Per day: ${projected_daily:+.2f}",
            f"  Per month: ${projected_monthly:+.2f}",
        ]
    )

    # Export primary chart
    img_bytes = spread_fig.to_image(format="png", width=1300, height=700, scale=2)
    chart = io.BytesIO(img_bytes)

    # Report
    from condor.reports import ReportBuilder

    builder = ReportBuilder("BTC-BRL Arbitrage")
    builder.source("routine", "btc_brl_arbitrage").tags(
        ["btc", "brl", "arbitrage", "strategy"]
    )
    builder.markdown(text)
    builder.plotly(spread_fig)
    builder.plotly(strategy_fig)
    await builder.save()

    return RoutineResult(text=text, chart_image=chart)
