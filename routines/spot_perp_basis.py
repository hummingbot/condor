CATEGORY = "Bot Analysis"

import asyncio
import io
import logging
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from routines.base import RoutineResult

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """Spot-Perp basis analysis: charts basis spread % and funding rate for arbitrage detection"""

    symbol: str = Field(default="KATUSDT", description="Binance symbol (no dash)")
    spot_interval: str = Field(
        default="1s", description="Spot candle interval (1s, 1m, 5m)"
    )
    perp_interval: str = Field(
        default="1m", description="Perp candle interval (1m, 5m, 15m)"
    )
    lookback_hours: int = Field(default=24, description="Hours of history to fetch")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    import aiohttp
    import pandas as pd

    now = datetime.now(timezone.utc)
    start_ms = int((now - timedelta(hours=config.lookback_hours)).timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    # Fetch spot candles, perp candles, and funding rate in parallel
    async with aiohttp.ClientSession() as session:
        spot_raw, perp_raw, funding_raw = await asyncio.gather(
            _fetch_klines(
                session,
                "https://api.binance.com/api/v3/klines",
                config.symbol,
                config.spot_interval,
                start_ms,
                end_ms,
            ),
            _fetch_klines(
                session,
                "https://fapi.binance.com/fapi/v1/klines",
                config.symbol,
                config.perp_interval,
                start_ms,
                end_ms,
            ),
            _fetch_funding(session, config.symbol, start_ms, end_ms),
        )

    if not spot_raw or not perp_raw:
        return f"No data returned. Check if {config.symbol} exists on Binance spot and perp."

    # Parse into DataFrames
    spot_df = _parse_klines(spot_raw).set_index("timestamp").sort_index()
    perp_df = _parse_klines(perp_raw).set_index("timestamp").sort_index()

    # Align spot to perp timestamps (merge_asof handles different intervals)
    merged = pd.merge_asof(
        perp_df[["close"]],
        spot_df[["close"]],
        left_index=True,
        right_index=True,
        suffixes=("_perp", "_spot"),
        direction="nearest",
        tolerance=pd.Timedelta("2m"),
    ).dropna()

    if merged.empty:
        return "Could not align spot and perp data. Try a larger tolerance or same interval."

    # Basis % = (perp - spot) / spot * 100
    merged["basis_pct"] = (
        (merged["close_perp"] - merged["close_spot"]) / merged["close_spot"] * 100
    )

    # Parse funding rate
    funding_df = pd.DataFrame(funding_raw)
    has_funding = not funding_df.empty
    if has_funding:
        funding_df["timestamp"] = pd.to_datetime(
            funding_df["fundingTime"], unit="ms", utc=True
        )
        funding_df["rate"] = funding_df["fundingRate"].astype(float) * 100  # to %

    # --- Build plotly chart ---
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots as _make_subplots

        fig = _make_subplots(
            rows=2,
            cols=1,
            row_heights=[0.65, 0.35],
            vertical_spacing=0.08,
            subplot_titles=["Basis %", "Funding Rate %"],
        )
        fig.add_trace(
            go.Scatter(
                x=merged.index,
                y=merged["basis_pct"],
                mode="lines",
                line=dict(color="#2196F3", width=1),
                name="Basis %",
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=merged.index,
                y=merged["basis_pct"].clip(lower=0),
                fill="tozeroy",
                fillcolor="rgba(76,175,80,0.2)",
                line=dict(width=0),
                showlegend=False,
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=merged.index,
                y=merged["basis_pct"].clip(upper=0),
                fill="tozeroy",
                fillcolor="rgba(244,67,54,0.2)",
                line=dict(width=0),
                showlegend=False,
            ),
            row=1,
            col=1,
        )
        fig.add_hline(
            y=0, line_dash="dash", line_color="gray", opacity=0.5, row=1, col=1
        )
        if has_funding:
            bar_colors = [
                "#4CAF50" if r >= 0 else "#F44336" for r in funding_df["rate"]
            ]
            fig.add_trace(
                go.Bar(
                    x=funding_df["timestamp"],
                    y=funding_df["rate"],
                    marker_color=bar_colors,
                    opacity=0.7,
                    name="Funding Rate %",
                ),
                row=2,
                col=1,
            )
            fig.add_hline(
                y=0, line_dash="dash", line_color="gray", opacity=0.5, row=2, col=1
            )
        fig.update_layout(
            title=f"{config.symbol} Spot-Perp Basis ({config.lookback_hours}h)",
            paper_bgcolor="#0d1117",
            plot_bgcolor="#161b22",
            font=dict(color="#c9d1d9", size=10),
            margin=dict(l=50, r=30, t=60, b=40),
            height=600,
            autosize=True,
        )
        for r in [1, 2]:
            fig.update_xaxes(gridcolor="#21262d", row=r, col=1)
            fig.update_yaxes(gridcolor="#21262d", row=r, col=1)
    except Exception as e:
        logger.warning(f"Chart generation failed: {e}")
        fig = None

    # Export PNG and send to Telegram
    chart_bytes = None
    if fig is not None:
        try:
            buf = io.BytesIO()
            fig.write_image(buf, format="png", scale=2)
            buf.seek(0)
            chart_bytes = buf.getvalue()

            chat_id = context._chat_id if hasattr(context, "_chat_id") else None
            caption = f"{config.symbol} Basis Analysis ({config.lookback_hours}h)"
            if chat_id:
                buf.seek(0)
                if context.bot:
                    await context.bot.send_photo(
                        chat_id=chat_id, photo=buf, caption=caption
                    )
                else:
                    await _send_photo_http(chat_id, buf, caption)
        except Exception as e:
            logger.warning(f"Chart send failed: {e}")

    # Text summary
    lines = [
        f"*{config.symbol} Basis Analysis* ({config.lookback_hours}h)",
        f"Spot interval: {config.spot_interval} | Perp interval: {config.perp_interval}",
        "",
        "*Basis Spread:*",
        f"  Mean: {merged['basis_pct'].mean():.4f}%",
        f"  Std Dev: {merged['basis_pct'].std():.4f}%",
        f"  Range: {merged['basis_pct'].min():.4f}% to {merged['basis_pct'].max():.4f}%",
        f"  Current: {merged['basis_pct'].iloc[-1]:.4f}%",
    ]

    if has_funding:
        last_fr = funding_df["rate"].iloc[-1]
        avg_fr = funding_df["rate"].mean()
        lines.extend(
            [
                "",
                "*Funding Rate:*",
                f"  Last: {last_fr:.4f}%",
                f"  Avg: {avg_fr:.4f}%",
                f"  Annualized: {avg_fr * 3 * 365:.2f}%",
            ]
        )

    text = "\n".join(lines)

    # Save HTML report
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"Spot-Perp Basis: {config.symbol}")
        builder.source("routine", "spot_perp_basis").tags(
            ["basis", "funding", config.symbol]
        )
        builder.kpi(
            "Current Basis",
            f"{merged['basis_pct'].iloc[-1]:.4f}%",
            trend="up" if merged["basis_pct"].iloc[-1] >= 0 else "down",
        )
        builder.kpi("Mean Basis", f"{merged['basis_pct'].mean():.4f}%")
        if has_funding:
            last_fr = funding_df["rate"].iloc[-1]
            avg_fr = funding_df["rate"].mean()
            builder.kpi(
                "Last Funding",
                f"{last_fr:.4f}%",
                delta=f"Ann. {avg_fr * 3 * 365:.1f}%",
                trend="up" if last_fr >= 0 else "down",
            )
        if fig is not None:
            builder.plotly(fig)
        builder.markdown(text)
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return RoutineResult(text=text, chart_image=chart_bytes)


# --- Telegram fallback ---


async def _send_photo_http(chat_id: int, buf: io.BytesIO, caption: str) -> None:
    """Send a photo via Telegram HTTP API when bot instance is unavailable."""
    import os

    import httpx

    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN", "")
    if not token:
        logger.warning("TELEGRAM_TOKEN not set, cannot send chart")
        return

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    buf.seek(0)
    files = {"photo": ("chart.png", buf, "image/png")}
    data = {"chat_id": chat_id, "caption": caption}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, data=data, files=files)
        if not resp.json().get("ok"):
            logger.warning(f"sendPhoto failed: {resp.text}")


# --- Helpers ---


def _parse_klines(raw):
    import pandas as pd

    rows = []
    for k in raw:
        rows.append(
            {
                "timestamp": pd.Timestamp(k[0], unit="ms", tz="UTC"),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
        )
    return pd.DataFrame(rows)


async def _fetch_klines(session, base_url, symbol, interval, start_ms, end_ms):
    all_data = []
    current_start = start_ms
    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": 1000,
        }
        async with session.get(base_url, params=params) as resp:
            data = await resp.json()
            if not data or isinstance(data, dict):
                break
            all_data.extend(data)
            if len(data) < 1000:
                break
            current_start = data[-1][0] + 1
    return all_data


async def _fetch_funding(session, symbol, start_ms, end_ms):
    all_data = []
    current_start = start_ms
    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": 1000,
        }
        async with session.get(
            "https://fapi.binance.com/fapi/v1/fundingRate", params=params
        ) as resp:
            data = await resp.json()
            if not data or isinstance(data, dict):
                break
            all_data.extend(data)
            if len(data) < 1000:
                break
            current_start = data[-1]["fundingTime"] + 1
    return all_data
