"""Estimated OHLCV footprint figures for reports."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import plotly.graph_objects as go
from plotly.subplots import make_subplots


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _finite_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _valid_candles(candles: list[dict], volume_field: str) -> list[dict]:
    valid = []
    for candle in candles:
        values = [
            _finite_number(candle.get(field))
            for field in ("open", "high", "low", "close")
        ]
        volume = _finite_number(candle.get(volume_field))
        if any(value is None for value in values) or volume is None:
            continue
        open_, high, low, close = values
        if (
            low <= 0
            or high < low
            or not low <= open_ <= high
            or not low <= close <= high
            or volume < 0
        ):
            continue
        valid.append(candle)
    return valid


def _compact_number(value: float) -> str:
    absolute = abs(value)
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if absolute >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,.1f}"


def candle_timestamps(candles: list[dict]) -> list[Any]:
    """Return Plotly-compatible timestamps, using indexes if any value is invalid."""
    timestamps: list[Any] = []
    for index, candle in enumerate(candles):
        value = candle.get("timestamp", candle.get("time", candle.get("open_time")))
        if isinstance(value, datetime):
            timestamps.append(value)
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return list(range(len(candles)))
        try:
            numeric = float(value)
            if numeric > 1_000_000_000_000:
                timestamps.append(
                    datetime.fromtimestamp(numeric / 1000, tz=timezone.utc)
                )
            elif numeric > 1_000_000_000:
                timestamps.append(datetime.fromtimestamp(numeric, tz=timezone.utc))
            else:
                return list(range(len(candles)))
        except (TypeError, ValueError, OSError):
            try:
                datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return list(range(len(candles)))
            timestamps.append(value)
    return timestamps


def estimate_buy_sell_volume(
    candle: dict, *, volume_field: str = "volume"
) -> tuple[float, float]:
    """Estimate buy and sell volume from the close's position in the candle range."""
    high = _number(candle.get("high"))
    low = _number(candle.get("low"))
    close = min(high, max(low, _number(candle.get("close"))))
    volume = max(0.0, _number(candle.get(volume_field)))
    candle_range = high - low
    if candle_range <= 0:
        return volume / 2, volume / 2
    buy = volume * (close - low) / candle_range
    return buy, volume - buy


def estimate_volume_profile(
    candles: list[dict], *, volume_field: str = "volume", buckets: int = 25
) -> tuple[list[float], list[float], list[float]]:
    """Aggregate estimated buy and sell volume into numeric price buckets."""
    candles = _valid_candles(candles, volume_field)
    if not candles or buckets < 1:
        return [], [], []
    price_low = min(_number(candle.get("low")) for candle in candles)
    price_high = max(_number(candle.get("high")) for candle in candles)
    price_range = price_high - price_low
    if price_range <= 0:
        return [], [], []

    bucket_size = price_range / buckets
    buy_by_bucket = [0.0] * buckets
    sell_by_bucket = [0.0] * buckets
    for candle in candles:
        midpoint = (_number(candle.get("high")) + _number(candle.get("low"))) / 2
        index = min(buckets - 1, max(0, int((midpoint - price_low) / bucket_size)))
        buy, sell = estimate_buy_sell_volume(candle, volume_field=volume_field)
        buy_by_bucket[index] += buy
        sell_by_bucket[index] += sell

    prices = [price_low + (index + 0.5) * bucket_size for index in range(buckets)]
    return prices, buy_by_bucket, sell_by_bucket


def build_estimated_footprint_figure(
    candles: list[dict],
    *,
    volume_field: str = "volume",
    buckets: int = 25,
    title: str = "Estimated OHLCV footprint",
    candle_title: str = "Candlesticks",
    profile_title: str = "Estimated volume profile",
    candle_name: str = "Price",
    height: int = 600,
) -> go.Figure | None:
    """Build a candlestick and estimated volume-profile figure with a shared price axis."""
    candles = _valid_candles(candles, volume_field)
    prices, buys, sells = estimate_volume_profile(
        candles, volume_field=volume_field, buckets=buckets
    )
    if not prices:
        return None

    price_low = min(_number(candle.get("low")) for candle in candles)
    price_high = max(_number(candle.get("high")) for candle in candles)
    half_bucket = (price_high - price_low) / buckets / 2
    total_volume = [buy + sell for buy, sell in zip(buys, sells)]
    net_volume = [buy - sell for buy, sell in zip(buys, sells)]
    if not any(total_volume):
        return None

    total_x: list[float | None] = []
    total_y: list[float | None] = []
    buy_x: list[float | None] = []
    buy_y: list[float | None] = []
    sell_x: list[float | None] = []
    sell_y: list[float | None] = []
    for price, total, net in zip(prices, total_volume, net_volume):
        bounds = [
            price - half_bucket,
            price - half_bucket,
            price + half_bucket,
            price + half_bucket,
            None,
        ]
        total_x.extend([0, total, total, 0, None])
        total_y.extend(bounds)
        if net >= 0:
            buy_x.extend([0, net, net, 0, None])
            buy_y.extend(bounds)
        else:
            sell_x.extend([0, net, net, 0, None])
            sell_y.extend(bounds)

    figure = make_subplots(
        rows=1,
        cols=2,
        column_widths=[0.68, 0.32],
        shared_yaxes=True,
        subplot_titles=[candle_title, profile_title],
        horizontal_spacing=0.015,
    )
    figure.add_trace(
        go.Candlestick(
            x=candle_timestamps(candles),
            open=[_number(candle.get("open")) for candle in candles],
            high=[_number(candle.get("high")) for candle in candles],
            low=[_number(candle.get("low")) for candle in candles],
            close=[_number(candle.get("close")) for candle in candles],
            name=candle_name,
            increasing_line_color="#22c55e",
            decreasing_line_color="#ef4444",
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=total_x,
            y=total_y,
            fill="toself",
            fillcolor="rgba(156, 163, 175, 0.25)",
            line={"width": 0},
            mode="lines",
            name="Total volume",
        ),
        row=1,
        col=2,
    )
    if buy_x:
        figure.add_trace(
            go.Scatter(
                x=buy_x,
                y=buy_y,
                fill="toself",
                fillcolor="rgba(34, 197, 94, 0.75)",
                line={"width": 0},
                mode="lines",
                name="Estimated buy delta",
            ),
            row=1,
            col=2,
        )
    if sell_x:
        figure.add_trace(
            go.Scatter(
                x=sell_x,
                y=sell_y,
                fill="toself",
                fillcolor="rgba(239, 68, 68, 0.75)",
                line={"width": 0},
                mode="lines",
                name="Estimated sell delta",
            ),
            row=1,
            col=2,
        )

    visible = [
        (price, total, net)
        for price, total, net in zip(prices, total_volume, net_volume)
        if total > 0
    ]
    maximum_total = max(total_volume, default=0)
    minimum_net = min(net_volume, default=0)
    figure.add_trace(
        go.Scatter(
            x=[total for _, total, _ in visible],
            y=[price for price, _, _ in visible],
            mode="text",
            text=[_compact_number(total) for _, total, _ in visible],
            textposition="middle right",
            textfont={"size": 9},
            hoverinfo="skip",
            showlegend=False,
            name="Total volume values",
        ),
        row=1,
        col=2,
    )
    visible_delta = [
        (price, net) for price, _, net in visible if abs(net) >= maximum_total * 0.015
    ]
    figure.add_trace(
        go.Scatter(
            x=[net / 2 for _, net in visible_delta],
            y=[price for price, _ in visible_delta],
            mode="text",
            text=[f"{net:+,.1f}" for _, net in visible_delta],
            textposition="middle center",
            textfont={"size": 9},
            hoverinfo="skip",
            showlegend=False,
            name="Net delta values",
        ),
        row=1,
        col=2,
    )

    figure.update_layout(
        title={"text": title, "x": 0.01, "xanchor": "left"},
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": "#e8e6e3"},
        xaxis_rangeslider_visible=False,
        legend={
            "orientation": "h",
            "y": 1.18,
            "yanchor": "bottom",
            "x": 1,
            "xanchor": "right",
        },
        margin={"l": 75, "r": 45, "t": 145, "b": 70},
    )
    figure.update_xaxes(title_text="Time (UTC)", type="date", row=1, col=1)
    figure.update_xaxes(
        title_text="Volume (net delta | total)",
        range=[min(0.0, minimum_net * 1.35), maximum_total * 1.3],
        row=1,
        col=2,
    )
    figure.update_yaxes(
        type="linear",
        range=[price_low * 0.999, price_high * 1.001],
        title_text="Price",
        zeroline=False,
    )
    figure.update_yaxes(showticklabels=False, title_text=None, row=1, col=2)
    return figure
