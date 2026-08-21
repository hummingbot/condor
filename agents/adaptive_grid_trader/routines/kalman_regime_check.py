import logging
from datetime import datetime, timezone

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.reports import ReportBuilder
from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"


def _candle_minutes(interval: str) -> float:
    """Parse '1m'→1, '1h'→60, '4h'→240, '1d'→1440."""
    if interval.endswith("m"):
        return float(interval[:-1])
    elif interval.endswith("h"):
        return float(interval[:-1]) * 60
    elif interval.endswith("d"):
        return float(interval[:-1]) * 1440
    return 1.0


class Config(BaseModel):
    """Kalman filter regime detection — outputs regime signal and Kalman-sized grid params."""

    connector_name: str = Field(
        default="bitget_perpetual", description="Exchange connector"
    )
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair")
    candle_interval: str = Field(
        default="1m", description="Candle interval (1m, 5m, 1h, etc.)"
    )
    lookback_candles: int = Field(
        default=60, description="Candles to fetch (60 × 1m = last 1 hour)"
    )
    q_level: float = Field(default=100.0, description="Process noise: level variance")
    q_slope: float = Field(default=1.0, description="Process noise: slope variance")
    r_obs: float = Field(default=40.0, description="Observation noise variance")
    snr_trend_threshold: float = Field(
        default=0.3, description="SNR >= this → TRENDING"
    )
    snr_range_threshold: float = Field(default=0.1, description="SNR <= this → RANGING")
    target_grid_hours: float = Field(
        default=8.0, description="Target grid lifetime (hours) for D sizing"
    )


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    result = await client.market_data.get_candles(
        config.connector_name,
        config.trading_pair,
        interval=config.candle_interval,
        max_records=config.lookback_candles,
    )
    records = (
        result
        if isinstance(result, list)
        else result.get("data", result.get("candles", []))
    )
    if not records or len(records) < 20:
        return f"Insufficient candle data: got {len(records) if records else 0} records"

    closes, timestamps = [], []
    for r in records:
        try:
            closes.append(float(r["close"]))
            ts = r.get("timestamp", r.get("time", 0))
            if isinstance(ts, (int, float)) and ts > 0:
                t = ts / 1000 if ts > 1e10 else ts
                timestamps.append(
                    datetime.fromtimestamp(t, tz=timezone.utc).strftime("%m-%d %H:%M")
                )
            else:
                timestamps.append(str(ts))
        except (KeyError, TypeError, ValueError):
            continue

    if len(closes) < 20:
        return f"Could not parse enough closes: got {len(closes)}"

    prices = np.array(closes)
    n = len(prices)
    x_axis = timestamps[:n] if len(timestamps) == n else list(range(n))

    # Local Linear Trend Kalman Filter
    F = np.array([[1.0, 1.0], [0.0, 1.0]])
    H = np.array([[1.0, 0.0]])
    Q = np.diag([config.q_level, config.q_slope])
    R = np.array([[config.r_obs]])
    x = np.array([prices[0], 0.0])
    P = np.eye(2) * 1e6

    levels, slopes, innovations = [], [], []
    for price in prices:
        x_pred = F @ x
        P_pred = F @ P @ F.T + Q
        inn = price - float((H @ x_pred)[0])
        S = float((H @ P_pred @ H.T + R)[0, 0])
        K = (P_pred @ H.T / S).flatten()
        x = x_pred + K * inn
        P = (np.eye(2) - np.outer(K, H)) @ P_pred
        levels.append(float(x[0]))
        slopes.append(float(x[1]))
        innovations.append(float(inn))

    levels = np.array(levels)
    slopes = np.array(slopes)
    innovations = np.array(innovations)

    window = min(24, max(n // 6, 4))
    recent_slope = slopes[-1]
    recent_price = prices[-1]
    rms_inn = float(np.sqrt(np.mean(innovations[-window:] ** 2)))
    sigma_inn = max(rms_inn, 1.0)
    slope_pct = recent_slope / recent_price * 100
    snr = abs(recent_slope) / sigma_inn

    # D: scale by candle duration so grid width is timeframe-agnostic
    candle_min = _candle_minutes(config.candle_interval)
    grid_D = sigma_inn * float(np.sqrt(config.target_grid_hours * 60 / candle_min))

    if n < 30:
        regime, profile = "UNCERTAIN", "HOLD"
    elif snr >= config.snr_trend_threshold:
        regime = "TRENDING_UP" if recent_slope > 0 else "TRENDING_DOWN"
        profile = "LONG" if recent_slope > 0 else "SHORT"
    elif snr <= config.snr_range_threshold:
        regime, profile = "RANGING", "TWO_SIDED"
    else:
        regime, profile = "UNCERTAIN", "HOLD"

    if regime == "TRENDING_UP":
        g_start = recent_price - grid_D
        g_end = recent_price + 3 * grid_D
        g_limit = recent_price - 1.5 * grid_D
    elif regime == "TRENDING_DOWN":
        g_start = recent_price - 3 * grid_D
        g_end = recent_price + grid_D
        g_limit = recent_price + 1.5 * grid_D
    else:
        g_start = recent_price - 1.5 * grid_D
        g_end = recent_price + 1.5 * grid_D
        g_limit = None

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        subplot_titles=[
            f"Price vs Kalman Level — {config.trading_pair}",
            "Kalman Slope ($/candle)",
            "Innovation ($)",
        ],
        row_heights=[0.5, 0.25, 0.25],
        vertical_spacing=0.07,
    )
    fig.add_trace(
        go.Scatter(
            x=x_axis,
            y=prices.tolist(),
            name="Close",
            line=dict(color="#94a3b8", width=1),
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x_axis,
            y=levels.tolist(),
            name="Kalman Level",
            line=dict(color="#60a5fa", width=2),
        ),
        row=1,
        col=1,
    )
    slope_colors = ["#22c55e" if s > 0 else "#ef4444" for s in slopes]
    fig.add_trace(
        go.Bar(
            x=x_axis,
            y=slopes.tolist(),
            name="Slope",
            marker_color=slope_colors,
            showlegend=False,
        ),
        row=2,
        col=1,
    )
    fig.add_hline(y=0, line_dash="dash", line_color="gray", line_width=1, row=2, col=1)
    inn_colors = [
        "#f97316" if abs(i) > 2 * sigma_inn else "#64748b" for i in innovations
    ]
    fig.add_trace(
        go.Bar(
            x=x_axis,
            y=innovations.tolist(),
            name="Innovation",
            marker_color=inn_colors,
            showlegend=False,
        ),
        row=3,
        col=1,
    )
    fig.add_hline(
        y=sigma_inn, line_dash="dot", line_color="#22c55e", line_width=1, row=3, col=1
    )
    fig.add_hline(
        y=-sigma_inn, line_dash="dot", line_color="#22c55e", line_width=1, row=3, col=1
    )
    fig.update_layout(
        height=700,
        template="plotly_dark",
        title=f"Kalman Regime: {regime} | SNR={snr:.3f} | {config.candle_interval} × {n}",
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
    )

    builder = ReportBuilder("Kalman Regime Check")
    builder.source("routine", "kalman_regime_check")
    builder.tags(["analysis", "regime", "kalman", "adaptive-grid-trader"])

    builder.section(
        "01 / REGIME SIGNAL", "Kalman filter output and profile recommendation"
    )
    builder.kpi("Regime", regime)
    builder.kpi("Profile", profile)
    builder.kpi("Current Price", f"${recent_price:,.2f}")
    builder.kpi("Kalman Slope", f"{slope_pct:+.5f}%/candle")
    builder.kpi("Innovation RMS", f"${sigma_inn:,.2f}")
    builder.kpi("SNR", f"{snr:.3f}")
    builder.kpi("Candles Used", str(n))

    builder.section("02 / GRID SIZING", "Kalman-derived grid parameters")
    builder.kpi("Grid D", f"${grid_D:,.2f}")
    builder.kpi("Grid Start", f"${g_start:,.2f}")
    builder.kpi("Grid End", f"${g_end:,.2f}")
    builder.kpi(
        "Limit Price", f"${g_limit:,.2f}" if g_limit else "N/A (ranging/uncertain)"
    )

    builder.section("03 / CHARTS", "Price, slope, and innovation over lookback window")
    builder.plotly(fig)

    builder.manual_order()
    await builder.save()

    return (
        f"Regime: {regime} → {profile}\n"
        f"Price ${recent_price:,.2f} | Slope {slope_pct:+.5f}%/candle | SNR {snr:.3f}\n"
        f"Grid D ${grid_D:,.2f} | Range ${g_start:,.2f}–${g_end:,.2f}"
        + (f" | Limit ${g_limit:,.2f}" if g_limit else "")
    )
