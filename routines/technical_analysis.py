"""Technical analysis with MACD, EMAs, NATR, support/resistance and grid suggestion."""

CATEGORY = "Analysis"

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
    """Analyze trend, volatility, S/R levels and suggest a grid direction with chart."""

    connector_name: str = Field(
        default="binance_perpetual", description="Exchange connector"
    )
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair")
    interval: str = Field(
        default="1m", description="Candle interval: 1m, 5m, 15m, 30m, 1h, 4h, 1d"
    )
    lookback: str = Field(
        default="6h", description="Lookback period: e.g. 6h, 2d, 12h, 7d"
    )
    ema_fast: int = Field(default=9, description="Fast EMA period")
    ema_mid: int = Field(default=21, description="Mid EMA period")
    ema_slow: int = Field(default=55, description="Slow EMA period")
    macd_fast: int = Field(default=12, description="MACD fast period")
    macd_slow: int = Field(default=26, description="MACD slow period")
    macd_signal: int = Field(default=9, description="MACD signal period")
    natr_period: int = Field(default=14, description="NATR period")
    sr_window: int = Field(
        default=20, description="Window for local min/max S/R detection"
    )
    grid_levels: int = Field(default=5, description="Number of grid levels to suggest")
    send_chart: bool = Field(default=True, description="Send chart to Telegram")


def _parse_lookback(lookback: str) -> int:
    """Parse lookback string like '6h', '2d' into hours."""
    s = lookback.strip().lower()
    if s.endswith("d"):
        return int(s[:-1]) * 24
    if s.endswith("h"):
        return int(s[:-1])
    # Fallback: try as raw hours
    return int(s)


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_macd(close: pd.Series, fast: int, slow: int, signal: int):
    ema_fast = compute_ema(close, fast)
    ema_slow = compute_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_natr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int
) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(window=period).mean()
    natr = (atr / close) * 100
    return natr


def find_support_resistance(
    df: pd.DataFrame, window: int
) -> tuple[list[float], list[float]]:
    """Find S/R levels using multi-window pivot detection, classified by current price.

    Uses three window sizes (window, 2x, 3x) to capture both short-term and
    structural levels. Levels touched multiple times or across multiple windows
    are stronger and ranked higher.
    """
    highs = df["high"].values
    lows = df["low"].values
    close = float(df["close"].iloc[-1])
    n = len(df)

    # Collect pivots at multiple window sizes for robustness
    level_hits: dict[float, int] = {}  # level → hit count (strength)
    for w in (window, window * 2, window * 3):
        if w >= n // 2:
            continue
        for i in range(w, n - w):
            if lows[i] == min(lows[i - w : i + w + 1]):
                level_hits[float(lows[i])] = level_hits.get(float(lows[i]), 0) + 1
            if highs[i] == max(highs[i - w : i + w + 1]):
                level_hits[float(highs[i])] = level_hits.get(float(highs[i]), 0) + 1

    if not level_hits:
        return [], []

    # Cluster nearby levels (within threshold), keep weighted average by hit count
    clustered = _cluster_levels_weighted(level_hits, threshold_pct=0.5)

    # Classify by current price
    supports = sorted([lv for lv, _ in clustered if lv < close], reverse=True)
    resistances = sorted([lv for lv, _ in clustered if lv >= close])

    # Return top levels sorted by strength (hit count), max 5 each
    sup_strength = {lv: s for lv, s in clustered if lv < close}
    res_strength = {lv: s for lv, s in clustered if lv >= close}
    supports = sorted(supports, key=lambda x: sup_strength[x], reverse=True)[:5]
    resistances = sorted(resistances, key=lambda x: res_strength[x], reverse=True)[:5]

    return supports, resistances


def _cluster_levels_weighted(
    level_hits: dict[float, int],
    threshold_pct: float = 0.5,
) -> list[tuple[float, int]]:
    """Cluster nearby price levels, returning (avg_price, total_hits) per cluster."""
    items = sorted(level_hits.items())
    if not items:
        return []

    clusters: list[list[tuple[float, int]]] = [[items[0]]]
    for price, hits in items[1:]:
        cluster_avg = np.mean([p for p, _ in clusters[-1]])
        if (price - cluster_avg) / cluster_avg * 100 < threshold_pct:
            clusters[-1].append((price, hits))
        else:
            clusters.append([(price, hits)])

    result = []
    for cluster in clusters:
        total_hits = sum(h for _, h in cluster)
        avg_price = np.average([p for p, _ in cluster], weights=[h for _, h in cluster])
        result.append((round(float(avg_price), 6), total_hits))

    return result


# ---------------------------------------------------------------------------
# Grid suggestion
# ---------------------------------------------------------------------------


def suggest_grid(
    close: float,
    ema_fast_val: float,
    ema_mid_val: float,
    ema_slow_val: float,
    macd_hist: float,
    natr_val: float,
    supports: list[float],
    resistances: list[float],
    grid_levels: int,
) -> dict:
    """Suggest grid direction and S/R-anchored price range."""
    # Trend scoring (-2 to +2)
    trend_score = 0
    if ema_fast_val > ema_mid_val:
        trend_score += 1
    if ema_mid_val > ema_slow_val:
        trend_score += 1
    if close > ema_mid_val:
        trend_score += 1
    if macd_hist > 0:
        trend_score += 1
    trend_score = trend_score - 2

    if trend_score >= 1:
        direction = "LONG"
    elif trend_score <= -1:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    # NATR-based fallback spread
    natr_spread = natr_val / 100 * close * 3

    # Anchor grid on nearest S/R levels
    nearest_sup = max(supports) if supports else None
    nearest_res = min(resistances) if resistances else None

    # Grid bottom: nearest support below, fallback to NATR
    grid_bottom = nearest_sup if nearest_sup else close - natr_spread / 2
    # Grid top: nearest resistance above, fallback to NATR
    grid_top = nearest_res if nearest_res else close + natr_spread / 2

    # Ensure minimum spread (at least 1x NATR)
    min_spread = natr_val / 100 * close
    if grid_top - grid_bottom < min_spread:
        mid = (grid_top + grid_bottom) / 2
        grid_bottom = mid - min_spread / 2
        grid_top = mid + min_spread / 2

    # Ensure price is within the grid range
    if close <= grid_bottom:
        grid_bottom = close - min_spread
    if close >= grid_top:
        grid_top = close + min_spread

    grid_prices = np.linspace(grid_bottom, grid_top, grid_levels).tolist()

    return {
        "direction": direction,
        "trend_score": trend_score,
        "grid_bottom": round(grid_bottom, 6),
        "grid_top": round(grid_top, 6),
        "grid_prices": [round(p, 6) for p in grid_prices],
        "spread_pct": round((grid_top - grid_bottom) / close * 100, 2),
    }


# ---------------------------------------------------------------------------
# Chart generation
# ---------------------------------------------------------------------------


def generate_chart(
    df: pd.DataFrame,
    macd_line: pd.Series,
    signal_line: pd.Series,
    macd_hist: pd.Series,
    natr: pd.Series,
    supports: list[float],
    resistances: list[float],
    grid_info: dict,
    config: Config,
) -> io.BytesIO:
    """Generate a multi-panel Plotly chart and return as PNG bytes buffer."""
    times = df["datetime"]
    direction = grid_info["direction"]
    grid_color = (
        "#00d4aa"
        if direction == "LONG"
        else "#ff6b6b" if direction == "SHORT" else "#888"
    )

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.6, 0.2, 0.2],
        subplot_titles=["", "MACD", "NATR %"],
    )

    # --- Panel 1: Candlestick ---
    fig.add_trace(
        go.Candlestick(
            x=times,
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            increasing_line_color="#00d4aa",
            decreasing_line_color="#ff6b6b",
            increasing_fillcolor="#00d4aa",
            decreasing_fillcolor="#ff6b6b",
            name="Price",
            showlegend=False,
        ),
        row=1,
        col=1,
    )

    # EMAs
    for col, color, period in [
        ("ema_fast", "#ffdd57", config.ema_fast),
        ("ema_mid", "#48dbfb", config.ema_mid),
        ("ema_slow", "#ff9ff3", config.ema_slow),
    ]:
        fig.add_trace(
            go.Scatter(
                x=times,
                y=df[col],
                mode="lines",
                line=dict(color=color, width=1.2),
                name=f"EMA {period}",
                opacity=0.85,
            ),
            row=1,
            col=1,
        )

    # Support / Resistance lines
    price_min, price_max = df["low"].min(), df["high"].max()
    price_margin = (price_max - price_min) * 0.05
    t0, t1 = times.iloc[0], times.iloc[-1]

    for s in supports:
        if price_min - price_margin <= s <= price_max + price_margin:
            fig.add_shape(
                type="line",
                x0=t0,
                x1=t1,
                y0=s,
                y1=s,
                line=dict(color="#00d4aa", width=0.8, dash="dash"),
                opacity=0.5,
                row=1,
                col=1,
            )
            fig.add_annotation(
                x=t1,
                y=s,
                text=f"S {s:.6g}",
                showarrow=False,
                font=dict(color="#00d4aa", size=9),
                xanchor="left",
                xshift=5,
                row=1,
                col=1,
            )

    for r in resistances:
        if price_min - price_margin <= r <= price_max + price_margin:
            fig.add_shape(
                type="line",
                x0=t0,
                x1=t1,
                y0=r,
                y1=r,
                line=dict(color="#ff6b6b", width=0.8, dash="dash"),
                opacity=0.5,
                row=1,
                col=1,
            )
            fig.add_annotation(
                x=t1,
                y=r,
                text=f"R {r:.6g}",
                showarrow=False,
                font=dict(color="#ff6b6b", size=9),
                xanchor="left",
                xshift=5,
                row=1,
                col=1,
            )

    # Grid zone shading
    fig.add_shape(
        type="rect",
        x0=t0,
        x1=t1,
        y0=grid_info["grid_bottom"],
        y1=grid_info["grid_top"],
        fillcolor=grid_color,
        opacity=0.08,
        line=dict(width=0),
        row=1,
        col=1,
    )

    # Grid level lines
    for gp in grid_info["grid_prices"]:
        fig.add_shape(
            type="line",
            x0=t0,
            x1=t1,
            y0=gp,
            y1=gp,
            line=dict(color=grid_color, width=0.8, dash="dot"),
            opacity=0.45,
            row=1,
            col=1,
        )

    # --- Panel 2: MACD ---
    hist_colors = ["#00d4aa" if v >= 0 else "#ff6b6b" for v in macd_hist]
    fig.add_trace(
        go.Bar(
            x=times,
            y=macd_hist,
            marker_color=hist_colors,
            opacity=0.6,
            name="Histogram",
            showlegend=False,
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=times,
            y=macd_line,
            mode="lines",
            line=dict(color="#48dbfb", width=1.2),
            name="MACD",
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=times,
            y=signal_line,
            mode="lines",
            line=dict(color="#ff9ff3", width=1.2),
            name="Signal",
        ),
        row=2,
        col=1,
    )

    # --- Panel 3: NATR ---
    fig.add_trace(
        go.Scatter(
            x=times,
            y=natr,
            mode="lines",
            line=dict(color="#ffdd57", width=1.2),
            fill="tozeroy",
            fillcolor="rgba(255,221,87,0.15)",
            name="NATR",
            showlegend=False,
        ),
        row=3,
        col=1,
    )

    # --- Layout ---
    fig.update_layout(
        title=dict(
            text=f"{config.trading_pair} | {config.connector_name} | {config.lookback} @ {config.interval} | Grid: {direction}",
            font=dict(color="white", size=14),
            x=0.5,
        ),
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#ccc", size=10),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.04,
            xanchor="left",
            x=0,
            font=dict(size=9),
            bgcolor="rgba(0,0,0,0)",
        ),
        margin=dict(l=60, r=80, t=100, b=40),
        autosize=True,
        height=800,
        xaxis_rangeslider_visible=False,
        xaxis3=dict(title="Time (UTC)"),
    )

    # Style all y-axes
    for i in range(1, 4):
        fig.update_yaxes(gridcolor="#2a2a4a", zerolinecolor="#333", row=i, col=1)
        fig.update_xaxes(gridcolor="#2a2a4a", row=i, col=1)
    fig.update_yaxes(title_text="Price", row=1, col=1)

    # Subtitle annotations styling
    for ann in fig.layout.annotations:
        ann.font = dict(color="#aaa", size=11)

    buf = io.BytesIO()
    fig.write_image(buf, format="png", scale=2)
    buf.seek(0)
    return buf, fig


# ---------------------------------------------------------------------------
# Text summary
# ---------------------------------------------------------------------------


def format_analysis(
    df: pd.DataFrame,
    supports: list[float],
    resistances: list[float],
    grid_info: dict,
    config: Config,
) -> str:
    last = df.iloc[-1]
    close = last["close"]
    natr_now = last["natr"]
    natr_avg = df["natr"].mean()
    macd_hist_now = last["macd_hist"]

    # Trend
    ema_order = ""
    if last["ema_fast"] > last["ema_mid"] > last["ema_slow"]:
        ema_order = "BULLISH alignment (fast > mid > slow)"
    elif last["ema_fast"] < last["ema_mid"] < last["ema_slow"]:
        ema_order = "BEARISH alignment (fast < mid < slow)"
    else:
        ema_order = "MIXED (no clear alignment)"

    macd_str = "bullish" if macd_hist_now > 0 else "bearish"

    # Volatility
    if natr_now < 0.1:
        vol_label = "LOW"
    elif natr_now < 0.3:
        vol_label = "MODERATE"
    else:
        vol_label = "HIGH"

    lines = [
        f"Technical Analysis: {config.trading_pair}",
        f"Connector: {config.connector_name} | {config.lookback} @ {config.interval}",
        f"Price: {close:.6g}",
        "",
        "TREND",
        f"  EMAs: {ema_order}",
        f"  EMA {config.ema_fast}: {last['ema_fast']:.6g} | EMA {config.ema_mid}: {last['ema_mid']:.6g} | EMA {config.ema_slow}: {last['ema_slow']:.6g}",
        f"  MACD histogram: {macd_hist_now:.6g} ({macd_str})",
        "",
        "VOLATILITY",
        f"  NATR current: {natr_now:.4f}% ({vol_label})",
        f"  NATR avg (period): {natr_avg:.4f}%",
        "",
        "SUPPORT / RESISTANCE",
    ]

    # Sort by proximity to current price
    sorted_sup = sorted(supports, key=lambda s: close - s)[:3]
    sorted_res = sorted(resistances, key=lambda r: r - close)[:3]

    if not sorted_sup:
        lines.append("  S: none detected")
    for i, s in enumerate(sorted_sup):
        dist = (close - s) / close * 100
        tag = " ← nearest" if i == 0 else ""
        lines.append(f"  S: {s:.6g} (-{dist:.2f}%){tag}")

    if not sorted_res:
        lines.append("  R: none detected")
    for i, r in enumerate(sorted_res):
        dist = (r - close) / close * 100
        tag = " ← nearest" if i == 0 else ""
        lines.append(f"  R: {r:.6g} (+{dist:.2f}%){tag}")

    lines += [
        "",
        "GRID SUGGESTION",
        f"  Direction: {grid_info['direction']} (score: {grid_info['trend_score']:+d}/2)",
        f"  Range: {grid_info['grid_bottom']:.6g} — {grid_info['grid_top']:.6g} ({grid_info['spread_pct']:.2f}%)",
        f"  Levels: {', '.join(f'{p:.6g}' for p in grid_info['grid_prices'])}",
        "",
        f"BIAS: {grid_info['direction']}",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram photo fallback (for MCP context where context.bot is None)
# ---------------------------------------------------------------------------


async def _send_photo_http(chat_id: int, buf: io.BytesIO, caption: str) -> None:
    """Send a photo via Telegram HTTP API when bot instance is unavailable."""
    import os

    import httpx

    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN", "")
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set, cannot send chart")
        return

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    buf.seek(0)
    files = {"photo": ("chart.png", buf, "image/png")}
    data = {"chat_id": chat_id, "caption": caption}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, data=data, files=files)
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

    lookback_hours = _parse_lookback(config.lookback)

    # Calculate max records based on interval
    interval_minutes = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
        "1d": 1440,
    }
    mins_per_candle = interval_minutes.get(config.interval, 1)
    max_records = (lookback_hours * 60) // mins_per_candle

    # Fetch candles — prefer historical (works without active feed), fallback to real-time
    result = None
    try:
        result = await client.market_data.get_candles_last_days(
            connector_name=config.connector_name,
            trading_pair=config.trading_pair,
            days=max(1, (lookback_hours + 23) // 24),
            interval=config.interval,
        )
    except Exception as e:
        logger.warning(f"Historical candles failed, trying real-time: {e}")

    records = (
        result
        if isinstance(result, list)
        else (
            result.get("data", result.get("candles", []))
            if isinstance(result, dict)
            else []
        )
    )

    if not records:
        try:
            result = await client.market_data.get_candles(
                connector_name=config.connector_name,
                trading_pair=config.trading_pair,
                interval=config.interval,
                max_records=max_records,
            )
        except Exception as e:
            return f"Failed to fetch candles: {e}"

        records = (
            result
            if isinstance(result, list)
            else (
                result.get("data", result.get("candles", []))
                if isinstance(result, dict)
                else []
            )
        )

    if len(records) < config.ema_slow + 10:
        return f"Not enough candles: got {len(records)}, need at least {config.ema_slow + 10}"

    df = pd.DataFrame(records)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["datetime"] = pd.to_datetime(
        df["timestamp"],
        unit="s" if df["timestamp"].iloc[0] > 1e12 / 1000 else "ms",
        utc=True,
    )
    df = df.sort_values("datetime").reset_index(drop=True)

    # Compute indicators
    df["ema_fast"] = compute_ema(df["close"], config.ema_fast)
    df["ema_mid"] = compute_ema(df["close"], config.ema_mid)
    df["ema_slow"] = compute_ema(df["close"], config.ema_slow)

    macd_line, signal_line, macd_hist = compute_macd(
        df["close"], config.macd_fast, config.macd_slow, config.macd_signal
    )
    df["macd"] = macd_line
    df["macd_signal"] = signal_line
    df["macd_hist"] = macd_hist

    df["natr"] = compute_natr(df["high"], df["low"], df["close"], config.natr_period)

    supports, resistances = find_support_resistance(df, config.sr_window)

    # Grid suggestion
    last = df.iloc[-1]
    grid_info = suggest_grid(
        close=last["close"],
        ema_fast_val=last["ema_fast"],
        ema_mid_val=last["ema_mid"],
        ema_slow_val=last["ema_slow"],
        macd_hist=last["macd_hist"],
        natr_val=last["natr"] if pd.notna(last["natr"]) else 0.2,
        supports=supports,
        resistances=resistances,
        grid_levels=config.grid_levels,
    )

    # Generate chart
    chart_bytes: bytes | None = None
    plotly_fig = None
    if config.send_chart:
        try:
            buf, plotly_fig = generate_chart(
                df,
                macd_line,
                signal_line,
                macd_hist,
                df["natr"],
                supports,
                resistances,
                grid_info,
                config,
            )
            chart_bytes = buf.getvalue()
            caption = f"📊 {config.trading_pair} Technical Analysis ({config.lookback} @ {config.interval})"
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

    # Return rich result with chart for web dashboard
    text = format_analysis(df, supports, resistances, grid_info, config)

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"Technical Analysis: {config.trading_pair}")
        builder.source("routine", "technical_analysis").tags(
            ["technical", config.trading_pair]
        )
        builder.markdown(text)
        if plotly_fig is not None:
            builder.plotly(plotly_fig)
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return RoutineResult(text=text, chart_image=chart_bytes)
