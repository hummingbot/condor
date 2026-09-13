"""Render the exact frame the fly would see for a HIP-3 pair, and report it."""

from __future__ import annotations

import sys
from pathlib import Path

_AGENT_DIR = str(Path(__file__).resolve().parents[1])
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

import logging

import aiohttp
import plotly.graph_objects as go
from flybrain.chart import market_frame, normalize_candles, price_scale
from flybrain.market import fetch_l2_book, normalize_candle_payload
from flybrain.naming import pair_names
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.reports import ReportBuilder
from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Market Data"


class Config(BaseModel):
    """What the fly sees: the 320×180 OHLCV frame for one HIP-3 pair."""

    trading_pair: str = Field(
        default="XYZ:DRAM-USD", description="Uppercase HIP-3 pair"
    )
    connector_name: str = Field(
        default="hyperliquid_perpetual", description="Connector"
    )
    candle_interval: str = Field(default="5m", description="Candle interval")
    n_candles: int = Field(default=72, description="Candles on the chart")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"
    names = pair_names(config.trading_pair)
    candles = normalize_candle_payload(
        await client.market_data.get_candles(
            config.connector_name,
            config.trading_pair,
            interval=config.candle_interval,
            max_records=config.n_candles,
        )
    )
    async with aiohttp.ClientSession() as session:
        book = await fetch_l2_book(session, names.coin)
    if not book.open:
        return f"{config.trading_pair}: book is closed (no bid/ask) — nothing to render"
    frame = market_frame(
        config.trading_pair, candles, book.bid, book.ask, config.n_candles
    )
    rows = normalize_candles(candles, config.n_candles)
    lo, span = price_scale(rows)

    fig = go.Figure(go.Image(z=frame))
    fig.update_layout(
        margin=dict(l=0, r=0, t=0, b=0),
        height=360,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
    )
    builder = ReportBuilder(f"Fly view — {config.trading_pair}")
    builder.source("routine", "fly_chart")
    builder.tags(["fly", "chart", "hip3"])
    builder.manual_order()
    builder.section("01 / FRAME", "320×180 RGB, exactly what enters the retina")
    builder.plotly(fig)
    builder.kpi("Candles", str(len(rows)))
    builder.kpi("Interval", config.candle_interval)
    builder.kpi("Bid", f"{book.bid:g}")
    builder.kpi("Ask", f"{book.ask:g}")
    builder.kpi("Price floor", f"{lo:g}")
    builder.kpi("Price span", f"{span:g}")
    builder.markdown(
        "_Up candles blue, down candles red: the mapped R8p cells read blue and R8y "
        "read green; R1–R6 read luminance. No quotes, inventory or P&L are drawn._"
    )
    await builder.save()
    return (
        f"{config.trading_pair}: {len(rows)} × {config.candle_interval} candles, "
        f"bid {book.bid:g} ask {book.ask:g}, last close {rows[-1]['close']:g}"
    )
