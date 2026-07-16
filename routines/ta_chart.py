import io
import logging
import time

import aiohttp
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pydantic import BaseModel, Field

from routines.base import RoutineResult

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

# Public Binance REST — no credentials needed.
_KLINES_URLS = {
    "binance": "https://api.binance.com/api/v3/klines",
    "binance_perpetual": "https://fapi.binance.com/fapi/v1/klines",
}


class Config(BaseModel):
    """Candlestick chart with BB + EMAs and a band-breakout signal evaluated over a fixed horizon."""

    connector_name: str = Field(
        default="binance_perpetual",
        description="Data source: binance (spot) or binance_perpetual (futures)",
    )
    trading_pair: str = Field(default="JUP-USDT", description="Trading pair")
    interval: str = Field(default="15m", description="Candle interval")
    days: int = Field(default=7, description="Days of history to plot")
    bb_length: int = Field(default=20, description="Bollinger Bands period")
    bb_std: float = Field(default=2.0, description="Bollinger Bands std dev")
    ema_fast: int = Field(default=9, description="Fast EMA period")
    ema_mid: int = Field(default=21, description="Mid EMA period")
    ema_slow: int = Field(default=50, description="Slow EMA period")
    min_bb_width_pct: float = Field(
        default=1.0, description="Min BB width % of mid for a breakout to count (dead-market filter)"
    )
    max_bb_width_pct: float = Field(
        default=3.0,
        description="Max BB width % at trigger (squeeze filter: only breakouts from tight bands)",
    )
    min_breakout_pct: float = Field(
        default=0.1, description="Min close penetration beyond the band, % of price"
    )
    signal_hours: float = Field(default=4.0, description="Max signal duration in hours")


def _interval_minutes(interval: str) -> int:
    units = {"m": 1, "h": 60, "d": 1440}
    return int(interval[:-1]) * units.get(interval[-1], 1)


async def _fetch_klines(
    connector_name: str, trading_pair: str, interval: str, days: float
) -> list[dict]:
    """Fetch klines from Binance public REST, paginating past the per-request cap."""
    if connector_name not in _KLINES_URLS:
        raise ValueError(
            f"Unsupported connector {connector_name!r} — "
            f"supported: {', '.join(sorted(_KLINES_URLS))}"
        )
    url = _KLINES_URLS[connector_name]
    symbol = trading_pair.replace("-", "").upper()
    interval_ms = _interval_minutes(interval) * 60_000
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(days * 86_400_000)
    per_request = 1000

    records: list[dict] = []
    async with aiohttp.ClientSession() as session:
        cursor = start_ms
        while cursor < end_ms:
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": per_request,
            }
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                raw = await resp.json()
            if not raw:
                break
            records.extend(
                {
                    "timestamp": r[0],
                    "open": r[1],
                    "high": r[2],
                    "low": r[3],
                    "close": r[4],
                    "volume": r[5],
                }
                for r in raw
            )
            cursor = raw[-1][0] + interval_ms
            if len(raw) < per_request:
                break
    return records


def _compute_signals(plot_df: pd.DataFrame, config: Config):
    """Breakout signal: close crosses a Bollinger band with EMA trend confirmation.

    Long  = close crosses above BB upper, EMA fast > EMA mid, close > EMA slow.
    Short = close crosses below BB lower, EMA fast < EMA mid, close < EMA slow.
    Filters: BB width >= min_bb_width_pct, penetration >= min_breakout_pct.
    Only one signal active at a time; each lasts max signal_hours.
    """
    n = len(plot_df)
    bars = max(1, int(config.signal_hours * 60 / _interval_minutes(config.interval)))
    width_series = (plot_df["bb_upper"] - plot_df["bb_lower"]) / plot_df["bb_mid"] * 100
    signal = np.zeros(n)
    signals: list = []
    ema_f, ema_m, ema_s = (
        f"ema_{config.ema_fast}", f"ema_{config.ema_mid}", f"ema_{config.ema_slow}"
    )
    active_until = -1
    raw_crossings = 0

    for i in range(1, n):
        row = plot_df.iloc[i]
        prev = plot_df.iloc[i - 1]
        if pd.isna(row["bb_upper"]) or pd.isna(prev["bb_upper"]) or pd.isna(row[ema_s]):
            continue

        cross_up = prev["close"] <= prev["bb_upper"] and row["close"] > row["bb_upper"]
        cross_dn = prev["close"] >= prev["bb_lower"] and row["close"] < row["bb_lower"]
        if cross_up or cross_dn:
            raw_crossings += 1

        if i <= active_until:
            continue

        width_pct = width_series.iloc[i]
        if not (config.min_bb_width_pct <= width_pct <= config.max_bb_width_pct):
            continue

        long_trig = (
            cross_up
            and (row["close"] - row["bb_upper"]) / row["close"] * 100 >= config.min_breakout_pct
            and row[ema_f] > row[ema_m]
            and row["close"] > row[ema_s]
        )
        short_trig = (
            cross_dn
            and (row["bb_lower"] - row["close"]) / row["close"] * 100 >= config.min_breakout_pct
            and row[ema_f] < row[ema_m]
            and row["close"] < row[ema_s]
        )
        if not (long_trig or short_trig):
            continue

        direction = 1 if long_trig else -1
        end_i = min(i + bars, n - 1)
        signal[i : end_i + 1] = direction
        active_until = end_i

        entry = row["close"]
        exit_ = plot_df["close"].iloc[end_i]
        pnl_pct = direction * (exit_ - entry) / entry * 100
        win_high = plot_df["high"].iloc[i : end_i + 1].max()
        win_low = plot_df["low"].iloc[i : end_i + 1].min()
        if direction == 1:
            mfe = (win_high - entry) / entry * 100
            mae = (win_low - entry) / entry * 100
        else:
            mfe = (entry - win_low) / entry * 100
            mae = (entry - win_high) / entry * 100
        signals.append(
            {
                "entry_i": i,
                "exit_i": end_i,
                "Entry Time": row["dt"].strftime("%m-%d %H:%M"),
                "Dir": "LONG" if direction == 1 else "SHORT",
                "Entry": round(entry, 4),
                "Exit": round(exit_, 4),
                "PnL %": round(pnl_pct, 2),
                "MFE %": round(mfe, 2),
                "MAE %": round(mae, 2),
                "BB Width %": round(width_pct, 2),
                "Status": "open" if end_i - i < bars else "closed",
            }
        )

    return signal, signals, raw_crossings


async def run(config: Config, context=None):
    # Fetch extra days so BB/EMA warm up before the displayed window
    warmup_minutes = config.ema_slow * _interval_minutes(config.interval)
    warmup_days = max(2, warmup_minutes // (60 * 24) + 1)
    records = await _fetch_klines(
        config.connector_name,
        config.trading_pair,
        config.interval,
        config.days + warmup_days,
    )
    if not records:
        return f"No candles returned for {config.trading_pair} on {config.connector_name}"

    df = pd.DataFrame(records)
    ts_col = "timestamp" if "timestamp" in df.columns else "time"
    df[ts_col] = pd.to_numeric(df[ts_col])
    unit = "ms" if df[ts_col].iloc[0] > 1e11 else "s"
    df["dt"] = pd.to_datetime(df[ts_col], unit=unit)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col])
    df = df.sort_values("dt").reset_index(drop=True)

    # Indicators
    df["bb_mid"] = df["close"].rolling(config.bb_length).mean()
    rolling_std = df["close"].rolling(config.bb_length).std()
    df["bb_upper"] = df["bb_mid"] + config.bb_std * rolling_std
    df["bb_lower"] = df["bb_mid"] - config.bb_std * rolling_std
    for period in (config.ema_fast, config.ema_mid, config.ema_slow):
        df[f"ema_{period}"] = df["close"].ewm(span=period, adjust=False).mean()

    # Trim to the display window after warmup
    cutoff = df["dt"].max() - pd.Timedelta(days=config.days)
    plot_df = df[df["dt"] >= cutoff].copy().reset_index(drop=True)
    if plot_df.empty:
        return "Not enough candle data after indicator warmup"

    last = plot_df.iloc[-1]
    first_close = plot_df["close"].iloc[0]
    change_pct = (last["close"] - first_close) / first_close * 100
    bb_width_pct = (last["bb_upper"] - last["bb_lower"]) / last["bb_mid"] * 100
    bb_pos = (last["close"] - last["bb_lower"]) / (last["bb_upper"] - last["bb_lower"]) * 100
    median_width = (
        (plot_df["bb_upper"] - plot_df["bb_lower"]) / plot_df["bb_mid"] * 100
    ).median()

    # Breakout signal
    signal, signals, raw_crossings = _compute_signals(plot_df, config)
    closed = [s for s in signals if s["Status"] == "closed"]
    wins = [s for s in closed if s["PnL %"] > 0]
    win_rate = len(wins) / len(closed) * 100 if closed else 0.0
    total_pnl = sum(s["PnL %"] for s in closed)
    avg_pnl = total_pnl / len(closed) if closed else 0.0
    longs = [s for s in signals if s["Dir"] == "LONG"]
    shorts = [s for s in signals if s["Dir"] == "SHORT"]

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.03,
        row_heights=[0.58, 0.18, 0.24],
    )
    fig.add_trace(
        go.Candlestick(
            x=plot_df["dt"], open=plot_df["open"], high=plot_df["high"],
            low=plot_df["low"], close=plot_df["close"], name=config.trading_pair,
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=plot_df["dt"], y=plot_df["bb_upper"],
            name=f"BB Upper ({config.bb_length}, {config.bb_std})",
            line=dict(color="rgba(150,150,255,0.6)", width=1),
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=plot_df["dt"], y=plot_df["bb_lower"], name="BB Lower",
            line=dict(color="rgba(150,150,255,0.6)", width=1),
            fill="tonexty", fillcolor="rgba(150,150,255,0.08)",
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=plot_df["dt"], y=plot_df["bb_mid"], name=f"BB Mid (SMA {config.bb_length})",
            line=dict(color="rgba(150,150,255,0.9)", width=1, dash="dot"),
        ),
        row=1, col=1,
    )
    ema_colors = {config.ema_fast: "#f5c518", config.ema_mid: "#ff7f0e", config.ema_slow: "#d62728"}
    for period, color in ema_colors.items():
        fig.add_trace(
            go.Scatter(
                x=plot_df["dt"], y=plot_df[f"ema_{period}"], name=f"EMA {period}",
                line=dict(color=color, width=1.5),
            ),
            row=1, col=1,
        )

    # Regime shading + entry/exit markers
    for s in signals:
        color = "rgba(38,166,154,0.16)" if s["Dir"] == "LONG" else "rgba(239,83,80,0.16)"
        fig.add_vrect(
            x0=plot_df["dt"].iloc[s["entry_i"]], x1=plot_df["dt"].iloc[s["exit_i"]],
            fillcolor=color, line_width=0, layer="below", row=1, col=1,
        )
    if longs:
        fig.add_trace(
            go.Scatter(
                x=[plot_df["dt"].iloc[s["entry_i"]] for s in longs],
                y=[s["Entry"] for s in longs],
                mode="markers", name="Long entry",
                marker=dict(symbol="triangle-up", size=12, color="#26a69a",
                            line=dict(width=1, color="white")),
            ),
            row=1, col=1,
        )
    if shorts:
        fig.add_trace(
            go.Scatter(
                x=[plot_df["dt"].iloc[s["entry_i"]] for s in shorts],
                y=[s["Entry"] for s in shorts],
                mode="markers", name="Short entry",
                marker=dict(symbol="triangle-down", size=12, color="#ef5350",
                            line=dict(width=1, color="white")),
            ),
            row=1, col=1,
        )
    if signals:
        fig.add_trace(
            go.Scatter(
                x=[plot_df["dt"].iloc[s["exit_i"]] for s in signals],
                y=[s["Exit"] for s in signals],
                mode="markers", name="Exit",
                marker=dict(symbol="x", size=9, color="#888"),
            ),
            row=1, col=1,
        )

    # Signal subplot
    fig.add_trace(
        go.Scatter(
            x=plot_df["dt"], y=signal, name="Signal",
            line=dict(color="#9467bd", width=1.5, shape="hv"),
            fill="tozeroy", fillcolor="rgba(148,103,189,0.25)",
        ),
        row=2, col=1,
    )
    fig.update_yaxes(
        title_text="Signal", tickvals=[-1, 0, 1], ticktext=["Short", "Flat", "Long"],
        range=[-1.3, 1.3], row=2, col=1,
    )

    vol_colors = ["#26a69a" if c >= o else "#ef5350" for c, o in zip(plot_df["close"], plot_df["open"])]
    fig.add_trace(
        go.Bar(x=plot_df["dt"], y=plot_df["volume"], name="Volume", marker_color=vol_colors),
        row=3, col=1,
    )
    fig.update_layout(
        title=(
            f"{config.trading_pair} {config.interval} — BB+EMA Breakout Signal "
            f"(max {config.signal_hours:g}h hold, {config.days}d) — {config.connector_name}"
        ),
        height=900,
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="center", x=0.5),
        margin=dict(l=60, r=30, t=60, b=90),
    )
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=3, col=1)

    buf = io.BytesIO()
    fig.write_image(buf, format="png", scale=2, width=1200, height=900)
    png_bytes = buf.getvalue()

    trend = "above" if last["close"] > last[f"ema_{config.ema_slow}"] else "below"
    summary = (
        f"{config.trading_pair} {config.interval} ({config.days}d)\n"
        f"Close: {last['close']:.4f} ({change_pct:+.2f}% over period)\n"
        f"BB position: {bb_pos:.0f}% of band, width {bb_width_pct:.2f}% (median {median_width:.2f}%)\n"
        f"Price is {trend} EMA{config.ema_slow}\n"
        f"\n"
        f"Breakout signal (BB cross + EMA{config.ema_fast}/{config.ema_mid} trend + "
        f"close vs EMA{config.ema_slow}, width {config.min_bb_width_pct}-{config.max_bb_width_pct}%):\n"
        f"Raw band crossings: {raw_crossings} | Confirmed signals: {len(signals)} "
        f"({len(longs)}L/{len(shorts)}S)\n"
        f"Closed: {len(closed)} | Win rate: {win_rate:.0f}% | "
        f"Avg PnL: {avg_pnl:+.2f}% | Total PnL: {total_pnl:+.2f}%"
    )

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"{config.trading_pair} {config.interval} TA Chart")
        builder.source("routine", "ta_chart").tags(
            ["ta", "bollinger", "ema", "signal", config.trading_pair.lower()]
        )
        builder.manual_order()
        builder.kpi(
            "Close", f"{last['close']:.4f}", delta=f"{change_pct:+.2f}%",
            trend="up" if change_pct >= 0 else "down",
        )
        builder.kpi("Signals", f"{len(signals)} ({len(longs)}L/{len(shorts)}S)")
        builder.kpi("Win Rate", f"{win_rate:.0f}%")
        builder.kpi(
            "Total PnL", f"{total_pnl:+.2f}%",
            trend="up" if total_pnl >= 0 else "down",
        )
        builder.kpi("Avg PnL / Signal", f"{avg_pnl:+.2f}%")
        builder.plotly(fig)
        if signals:
            table_cols = [
                "Entry Time", "Dir", "Entry", "Exit", "PnL %", "MFE %", "MAE %",
                "BB Width %", "Status",
            ]
            builder.table([{k: s[k] for k in table_cols} for s in signals])
        builder.markdown(
            "## Signal Definition\n"
            f"- **Long**: close crosses above BB upper, EMA{config.ema_fast} > EMA{config.ema_mid}, "
            f"close > EMA{config.ema_slow}\n"
            f"- **Short**: close crosses below BB lower, EMA{config.ema_fast} < EMA{config.ema_mid}, "
            f"close < EMA{config.ema_slow}\n"
            f"- **Width filter**: BB width {config.min_bb_width_pct}-{config.max_bb_width_pct}% of mid "
            f"(median width this period: {median_width:.2f}%) — breakouts from a squeeze, "
            f"not from already-stretched bands\n"
            f"- **One signal at a time**, max hold {config.signal_hours:g}h "
            f"— PnL measured close-to-close over the hold window\n\n"
            "## Summary\n" + summary.replace("\n", "  \n")
        )
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return RoutineResult(text=summary, chart_image=png_bytes)
