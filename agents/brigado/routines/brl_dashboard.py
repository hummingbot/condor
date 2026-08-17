"""Multi-timeframe USDT-BRL technical dashboard with Plotly chart."""

CATEGORY = "Market Data"

import asyncio
import io
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client
from routines.base import RoutineResult

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """USDT-BRL multi-timeframe dashboard: intraday, weekly, monthly, quarterly."""

    trading_pair: str = Field(default="USDT-BRL", description="Trading pair")
    connector_name: str = Field(default="binance", description="Exchange connector")


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------


def compute_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def find_sr_levels(
    highs: pd.Series, lows: pd.Series, window: int = 20
) -> tuple[list[float], list[float]]:
    """Find support/resistance using rolling min/max pivots."""
    n = len(highs)
    if n < window * 2:
        return [], []

    h = highs.values
    l = lows.values
    close = float(highs.iloc[-1] + lows.iloc[-1]) / 2

    levels: dict[float, int] = {}
    for w in (window, window * 2):
        if w >= n // 2:
            continue
        for i in range(w, n - w):
            if l[i] == min(l[i - w : i + w + 1]):
                lv = round(float(l[i]), 4)
                levels[lv] = levels.get(lv, 0) + 1
            if h[i] == max(h[i - w : i + w + 1]):
                lv = round(float(h[i]), 4)
                levels[lv] = levels.get(lv, 0) + 1

    if not levels:
        return [], []

    # Cluster nearby levels (within 0.3%)
    sorted_levels = sorted(levels.items())
    clusters: list[list[tuple[float, int]]] = [[sorted_levels[0]]]
    for price, hits in sorted_levels[1:]:
        cluster_avg = np.mean([p for p, _ in clusters[-1]])
        if abs(price - cluster_avg) / cluster_avg < 0.003:
            clusters[-1].append((price, hits))
        else:
            clusters.append([(price, hits)])

    merged = []
    for cluster in clusters:
        total_hits = sum(h for _, h in cluster)
        avg_price = np.average([p for p, _ in cluster], weights=[h for _, h in cluster])
        merged.append((round(float(avg_price), 4), total_hits))

    supports = sorted([lv for lv, _ in merged if lv < close], reverse=True)[:3]
    resistances = sorted([lv for lv, _ in merged if lv >= close])[:3]
    return supports, resistances


# ---------------------------------------------------------------------------
# Data fetching & processing
# ---------------------------------------------------------------------------

TIMEFRAMES = [
    {"label": "Intraday (1m)", "interval": "1m", "days": 1, "short_label": "1D"},
    {"label": "Semanal (15m)", "interval": "15m", "days": 7, "short_label": "1W"},
    {"label": "Mensual (1h)", "interval": "1h", "days": 30, "short_label": "1M"},
    {"label": "Trimestral (1d)", "interval": "1d", "days": 90, "short_label": "3M"},
]


async def fetch_candles(
    client, connector: str, pair: str, interval: str, days: int
) -> pd.DataFrame:
    """Fetch candles and return as DataFrame."""
    result = await client.market_data.get_candles_last_days(
        connector, pair, days=days, interval=interval
    )
    # API returns a list directly, or a dict with "candles"/"data" key
    if isinstance(result, list):
        records = result
    elif isinstance(result, dict):
        records = result.get("data", result.get("candles", []))
    else:
        records = []
    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    ts_col = df["timestamp"]
    unit = "s" if ts_col.iloc[0] > 1e12 / 1000 else "ms"
    df["datetime"] = pd.to_datetime(ts_col, unit=unit, utc=True)
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def compute_indicators(df: pd.DataFrame) -> dict:
    """Compute all indicators for a timeframe."""
    close = df["close"]
    sma20 = compute_sma(close, 20)
    sma50 = compute_sma(close, 50)
    ema9 = compute_ema(close, 9)
    ema21 = compute_ema(close, 21)
    rsi = compute_rsi(close, 14)

    current = float(close.iloc[-1])
    open_price = float(close.iloc[0])
    change_pct = (current - open_price) / open_price * 100

    high = float(df["high"].max())
    low = float(df["low"].min())

    supports, resistances = find_sr_levels(df["high"], df["low"])

    # Trend direction
    sma20_now = sma20.iloc[-1]
    sma50_now = sma50.iloc[-1]
    if pd.notna(sma20_now) and pd.notna(sma50_now):
        if current > sma20_now > sma50_now:
            trend = "ALCISTA"
        elif current < sma20_now < sma50_now:
            trend = "BAJISTA"
        else:
            trend = "LATERAL"
    else:
        trend = "N/A"

    return {
        "sma20": sma20,
        "sma50": sma50,
        "ema9": ema9,
        "ema21": ema21,
        "rsi": rsi,
        "current": current,
        "change_pct": change_pct,
        "high": high,
        "low": low,
        "supports": supports,
        "resistances": resistances,
        "trend": trend,
        "rsi_now": float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50,
        "sma20_now": float(sma20_now) if pd.notna(sma20_now) else current,
        "sma50_now": float(sma50_now) if pd.notna(sma50_now) else current,
    }


# ---------------------------------------------------------------------------
# Chart
# ---------------------------------------------------------------------------

COLORS = {
    "bg_paper": "#0d1117",
    "bg_plot": "#161b22",
    "grid": "#21262d",
    "text": "#c9d1d9",
    "text_dim": "#8b949e",
    "green": "#3fb950",
    "red": "#f85149",
    "sma20": "#f0883e",
    "sma50": "#58a6ff",
    "rsi_line": "#bc8cff",
    "support": "#3fb950",
    "resistance": "#f85149",
}


def build_chart(timeframe_data: list[dict], pair: str) -> io.BytesIO:
    """Build a 4x2 dashboard: candlestick+SMA (left col), RSI (right col) per timeframe."""
    fig = make_subplots(
        rows=4,
        cols=2,
        column_widths=[0.78, 0.22],
        row_heights=[0.25, 0.25, 0.25, 0.25],
        vertical_spacing=0.045,
        horizontal_spacing=0.03,
        subplot_titles=(
            [
                item["label"]
                for item in timeframe_data
                for _ in range(2)  # one title per subplot pair
            ]
            if False
            else None
        ),  # We'll add custom titles
    )

    current_price = timeframe_data[0]["indicators"]["current"] if timeframe_data else 0

    for i, item in enumerate(timeframe_data):
        row = i + 1
        df = item["df"]
        ind = item["indicators"]
        tf = item["tf"]
        dates = df["datetime"]

        # --- Candlestick ---
        fig.add_trace(
            go.Candlestick(
                x=dates,
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                increasing_line_color=COLORS["green"],
                decreasing_line_color=COLORS["red"],
                increasing_fillcolor=COLORS["green"],
                decreasing_fillcolor=COLORS["red"],
                showlegend=False,
                name=tf["short_label"],
            ),
            row=row,
            col=1,
        )

        # SMA20
        sma20_valid = ind["sma20"].dropna()
        if len(sma20_valid) > 0:
            fig.add_trace(
                go.Scatter(
                    x=dates.iloc[sma20_valid.index],
                    y=sma20_valid,
                    line=dict(color=COLORS["sma20"], width=1.2),
                    showlegend=(i == 0),
                    name="SMA 20",
                ),
                row=row,
                col=1,
            )

        # SMA50
        sma50_valid = ind["sma50"].dropna()
        if len(sma50_valid) > 0:
            fig.add_trace(
                go.Scatter(
                    x=dates.iloc[sma50_valid.index],
                    y=sma50_valid,
                    line=dict(color=COLORS["sma50"], width=1.2),
                    showlegend=(i == 0),
                    name="SMA 50",
                ),
                row=row,
                col=1,
            )

        # Support lines
        t0, t1 = dates.iloc[0], dates.iloc[-1]
        for s in ind["supports"]:
            fig.add_shape(
                type="line",
                x0=t0,
                x1=t1,
                y0=s,
                y1=s,
                line=dict(color=COLORS["support"], width=0.7, dash="dot"),
                opacity=0.5,
                row=row,
                col=1,
            )

        # Resistance lines
        for r in ind["resistances"]:
            fig.add_shape(
                type="line",
                x0=t0,
                x1=t1,
                y0=r,
                y1=r,
                line=dict(color=COLORS["resistance"], width=0.7, dash="dot"),
                opacity=0.5,
                row=row,
                col=1,
            )

        # --- RSI panel ---
        rsi_valid = ind["rsi"].dropna()
        if len(rsi_valid) > 0:
            fig.add_trace(
                go.Scatter(
                    x=dates.iloc[rsi_valid.index],
                    y=rsi_valid,
                    line=dict(color=COLORS["rsi_line"], width=1.5),
                    fill="tozeroy",
                    fillcolor="rgba(188,140,255,0.08)",
                    showlegend=False,
                ),
                row=row,
                col=2,
            )

            # Overbought/oversold bands
            fig.add_hline(
                y=70,
                line_dash="dot",
                line_color=COLORS["red"],
                opacity=0.4,
                row=row,
                col=2,
            )
            fig.add_hline(
                y=30,
                line_dash="dot",
                line_color=COLORS["green"],
                opacity=0.4,
                row=row,
                col=2,
            )

        # --- Row label annotation ---
        trend_color = COLORS["green"] if ind["change_pct"] >= 0 else COLORS["red"]
        arrow = "+" if ind["change_pct"] >= 0 else ""
        label_text = (
            f"<b>{tf['short_label']}</b> {tf['label'].split('(')[1].rstrip(')')}"
            f"  |  {ind['current']:.3f}"
            f"  |  <span style='color:{trend_color}'>{arrow}{ind['change_pct']:.2f}%</span>"
            f"  |  RSI: {ind['rsi_now']:.0f}"
            f"  |  {ind['trend']}"
        )
        fig.add_annotation(
            text=label_text,
            xref="paper",
            yref="paper",
            x=0,
            y=1 - (i * 0.25) + 0.005,
            showarrow=False,
            font=dict(size=11, color=COLORS["text"]),
            xanchor="left",
            yanchor="bottom",
        )

    # --- Header ---
    # Get quarterly change
    q_change = (
        timeframe_data[3]["indicators"]["change_pct"] if len(timeframe_data) > 3 else 0
    )
    m_change = (
        timeframe_data[2]["indicators"]["change_pct"] if len(timeframe_data) > 2 else 0
    )
    w_change = (
        timeframe_data[1]["indicators"]["change_pct"] if len(timeframe_data) > 1 else 0
    )
    d_change = timeframe_data[0]["indicators"]["change_pct"] if timeframe_data else 0

    title_text = (
        f"<b>{pair}</b>  |  {current_price:.3f}"
        f"  |  D: {d_change:+.2f}%  W: {w_change:+.2f}%  M: {m_change:+.2f}%  3M: {q_change:+.2f}%"
    )

    # --- Layout ---
    fig.update_layout(
        title=dict(text=title_text, font=dict(color=COLORS["text"], size=14), x=0.5),
        paper_bgcolor=COLORS["bg_paper"],
        plot_bgcolor=COLORS["bg_plot"],
        font=dict(color=COLORS["text_dim"], size=9),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.05,
            xanchor="center",
            x=0.4,
            font=dict(size=10, color=COLORS["text"]),
            bgcolor="rgba(0,0,0,0)",
        ),
        margin=dict(l=55, r=30, t=100, b=30),
        width=1300,
        height=1400,
        xaxis_rangeslider_visible=False,
        xaxis3_rangeslider_visible=False,
        xaxis5_rangeslider_visible=False,
        xaxis7_rangeslider_visible=False,
    )

    # Style axes
    for row in range(1, 5):
        # Price axis
        fig.update_yaxes(
            gridcolor=COLORS["grid"],
            zerolinecolor=COLORS["grid"],
            tickformat=".3f",
            row=row,
            col=1,
        )
        fig.update_xaxes(
            gridcolor=COLORS["grid"], showticklabels=(row == 4), row=row, col=1
        )
        # RSI axis
        fig.update_yaxes(
            gridcolor=COLORS["grid"],
            range=[0, 100],
            tickvals=[30, 50, 70],
            row=row,
            col=2,
        )
        fig.update_xaxes(gridcolor=COLORS["grid"], showticklabels=False, row=row, col=2)

    buf = io.BytesIO()
    fig.write_image(buf, format="png", scale=2)
    buf.seek(0)
    return buf, fig


# ---------------------------------------------------------------------------
# Text summary
# ---------------------------------------------------------------------------


def format_summary(timeframe_data: list[dict], pair: str) -> str:
    lines = [f"Dashboard {pair}"]
    for item in timeframe_data:
        ind = item["indicators"]
        tf = item["tf"]
        arrow = "+" if ind["change_pct"] >= 0 else ""
        sup_str = ", ".join(f"{s:.3f}" for s in ind["supports"][:2]) or "N/A"
        res_str = ", ".join(f"{r:.3f}" for r in ind["resistances"][:2]) or "N/A"
        lines.append(
            f"\n{tf['label']}:"
            f"\n  Precio: {ind['current']:.3f} ({arrow}{ind['change_pct']:.2f}%)"
            f"\n  Tendencia: {ind['trend']} | RSI: {ind['rsi_now']:.0f}"
            f"\n  SMA20: {ind['sma20_now']:.3f} | SMA50: {ind['sma50_now']:.3f}"
            f"\n  Soporte: {sup_str} | Resistencia: {res_str}"
            f"\n  Rango: {ind['low']:.3f} - {ind['high']:.3f}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram photo fallback
# ---------------------------------------------------------------------------


async def _send_photo_http(chat_id: int, buf: io.BytesIO, caption: str) -> None:
    import os

    import httpx

    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN", "")
    if not token:
        return

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    buf.seek(0)
    async with httpx.AsyncClient(timeout=30) as http_client:
        resp = await http_client.post(
            url,
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": ("dashboard.png", buf, "image/png")},
        )
        if not resp.json().get("ok"):
            logger.warning(f"sendPhoto failed: {resp.text}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = context._chat_id if hasattr(context, "_chat_id") else None
    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available"

    # Fetch all timeframes in parallel
    fetch_tasks = [
        fetch_candles(
            client,
            config.connector_name,
            config.trading_pair,
            tf["interval"],
            tf["days"],
        )
        for tf in TIMEFRAMES
    ]
    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    timeframe_data = []
    for tf, result in zip(TIMEFRAMES, results):
        if isinstance(result, Exception):
            logger.warning(f"Failed to fetch {tf['label']}: {result}")
            continue
        if result.empty or len(result) < 10:
            logger.warning(f"Not enough data for {tf['label']}: {len(result)} candles")
            continue

        indicators = compute_indicators(result)
        timeframe_data.append(
            {
                "tf": tf,
                "label": tf["label"],
                "df": result,
                "indicators": indicators,
            }
        )

    if not timeframe_data:
        return "No candle data available for any timeframe"

    # Generate chart
    chart_bytes: bytes | None = None
    plotly_fig = None
    try:
        buf, plotly_fig = build_chart(timeframe_data, config.trading_pair)
        chart_bytes = buf.getvalue()
        caption = f"{config.trading_pair} Multi-Timeframe Dashboard"
        if chat_id:
            buf.seek(0)
            if context.bot:
                await context.bot.send_photo(
                    chat_id=chat_id, photo=buf, caption=caption
                )
            else:
                await _send_photo_http(chat_id, buf, caption)
    except Exception as e:
        logger.warning(f"Chart generation/send failed: {e}")

    text = format_summary(timeframe_data, config.trading_pair)

    from condor.reports import ReportBuilder

    builder = ReportBuilder(f"Dashboard: {config.trading_pair}")
    builder.source("routine", "brl_dashboard").tags(["dashboard", config.trading_pair])
    builder.markdown(text)
    if plotly_fig is not None:
        builder.plotly(plotly_fig)
    await builder.save()

    if context.bot and chart_bytes:
        return RoutineResult(text=text, chart_image=chart_bytes)
    return text
