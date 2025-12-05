"""
PMM Mister chart generation.

Generates candlestick charts with PMM spread visualization:
- Buy spread levels (green dashed lines)
- Sell spread levels (red dashed lines)
- Current price line
- Base percentage target zone indicator
"""

import io
from datetime import datetime
from typing import Any, Dict, List, Optional

import plotly.graph_objects as go

from .config import parse_spreads


# Dark theme (consistent with grid_strike)
DARK_THEME = {
    "bgcolor": "#0a0e14",
    "paper_bgcolor": "#0a0e14",
    "plot_bgcolor": "#131720",
    "font_color": "#e6edf3",
    "font_family": "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif",
    "grid_color": "#21262d",
    "axis_color": "#8b949e",
    "up_color": "#10b981",    # Green for bullish/buy
    "down_color": "#ef4444",  # Red for bearish/sell
    "line_color": "#3b82f6",  # Blue for lines
    "target_color": "#f59e0b",  # Orange for target
}


def generate_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None
) -> io.BytesIO:
    """
    Generate a candlestick chart with PMM spread overlay.

    The chart shows:
    - Candlestick price data
    - Buy spread levels (green dashed lines below price)
    - Sell spread levels (red dashed lines above price)
    - Current price line (orange solid)

    Args:
        config: PMM Mister configuration with spreads, take_profit, etc.
        candles_data: List of candles from API (each with open, high, low, close, timestamp)
        current_price: Current market price

    Returns:
        BytesIO object containing the PNG image
    """
    trading_pair = config.get("trading_pair", "Unknown")
    buy_spreads_str = config.get("buy_spreads", "0.01,0.02")
    sell_spreads_str = config.get("sell_spreads", "0.01,0.02")
    take_profit = float(config.get("take_profit", 0.0001))

    # Parse spreads
    buy_spreads = parse_spreads(buy_spreads_str)
    sell_spreads = parse_spreads(sell_spreads_str)

    # Handle both list and dict input
    data = candles_data if isinstance(candles_data, list) else candles_data.get("data", [])

    if not data:
        # Create empty chart with message
        fig = go.Figure()
        fig.add_annotation(
            text="No candle data available",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(
                family=DARK_THEME["font_family"],
                size=16,
                color=DARK_THEME["font_color"]
            )
        )
    else:
        # Extract OHLCV data
        timestamps = []
        opens = []
        highs = []
        lows = []
        closes = []

        for candle in data:
            raw_ts = candle.get("timestamp", "")
            # Parse timestamp
            dt = None
            try:
                if isinstance(raw_ts, (int, float)):
                    # Unix timestamp (seconds or milliseconds)
                    if raw_ts > 1e12:  # milliseconds
                        dt = datetime.fromtimestamp(raw_ts / 1000)
                    else:
                        dt = datetime.fromtimestamp(raw_ts)
                elif isinstance(raw_ts, str) and raw_ts:
                    # Try parsing ISO format
                    if "T" in raw_ts:
                        dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                    else:
                        dt = datetime.fromisoformat(raw_ts)
            except Exception:
                dt = None

            if dt:
                timestamps.append(dt)
            else:
                timestamps.append(str(raw_ts))

            opens.append(candle.get("open", 0))
            highs.append(candle.get("high", 0))
            lows.append(candle.get("low", 0))
            closes.append(candle.get("close", 0))

        # Use current_price or last close
        ref_price = current_price or (closes[-1] if closes else 0)

        # Create candlestick chart
        fig = go.Figure(data=[go.Candlestick(
            x=timestamps,
            open=opens,
            high=highs,
            low=lows,
            close=closes,
            increasing_line_color=DARK_THEME["up_color"],
            decreasing_line_color=DARK_THEME["down_color"],
            increasing_fillcolor=DARK_THEME["up_color"],
            decreasing_fillcolor=DARK_THEME["down_color"],
            name="Price"
        )])

        # Add buy spread levels (below current price)
        if ref_price and buy_spreads:
            for i, spread in enumerate(buy_spreads):
                buy_price = ref_price * (1 - spread)
                opacity = 0.8 - (i * 0.15)  # Fade out for further levels
                fig.add_hline(
                    y=buy_price,
                    line_dash="dash",
                    line_color=DARK_THEME["up_color"],
                    line_width=2,
                    opacity=max(0.3, opacity),
                    annotation_text=f"Buy L{i+1}: {buy_price:,.4f} (-{spread*100:.1f}%)",
                    annotation_position="left",
                    annotation_font=dict(color=DARK_THEME["up_color"], size=9)
                )

        # Add sell spread levels (above current price)
        if ref_price and sell_spreads:
            for i, spread in enumerate(sell_spreads):
                sell_price = ref_price * (1 + spread)
                opacity = 0.8 - (i * 0.15)
                fig.add_hline(
                    y=sell_price,
                    line_dash="dash",
                    line_color=DARK_THEME["down_color"],
                    line_width=2,
                    opacity=max(0.3, opacity),
                    annotation_text=f"Sell L{i+1}: {sell_price:,.4f} (+{spread*100:.1f}%)",
                    annotation_position="right",
                    annotation_font=dict(color=DARK_THEME["down_color"], size=9)
                )

        # Add take profit indicator as a shaded zone
        if ref_price and take_profit:
            tp_up = ref_price * (1 + take_profit)
            tp_down = ref_price * (1 - take_profit)
            fig.add_hrect(
                y0=tp_down,
                y1=tp_up,
                fillcolor="rgba(245, 158, 11, 0.1)",
                line_width=0,
                annotation_text=f"TP Zone ({take_profit*100:.2f}%)",
                annotation_position="top right",
                annotation_font=dict(color=DARK_THEME["target_color"], size=9)
            )

        # Current price line
        if current_price:
            fig.add_hline(
                y=current_price,
                line_dash="solid",
                line_color=DARK_THEME["target_color"],
                line_width=2,
                annotation_text=f"Current: {current_price:,.4f}",
                annotation_position="left",
                annotation_font=dict(color=DARK_THEME["target_color"], size=10)
            )

    # Build title
    title_text = f"<b>{trading_pair}</b> - PMM Mister"

    # Update layout with dark theme
    fig.update_layout(
        title=dict(
            text=title_text,
            font=dict(
                family=DARK_THEME["font_family"],
                size=18,
                color=DARK_THEME["font_color"]
            ),
            x=0.5,
            xanchor="center"
        ),
        paper_bgcolor=DARK_THEME["paper_bgcolor"],
        plot_bgcolor=DARK_THEME["plot_bgcolor"],
        font=dict(
            family=DARK_THEME["font_family"],
            color=DARK_THEME["font_color"]
        ),
        xaxis=dict(
            gridcolor=DARK_THEME["grid_color"],
            color=DARK_THEME["axis_color"],
            rangeslider_visible=False,
            showgrid=True,
            nticks=8,
            tickformat="%b %d\n%H:%M",
            tickangle=0,
        ),
        yaxis=dict(
            gridcolor=DARK_THEME["grid_color"],
            color=DARK_THEME["axis_color"],
            side="right",
            showgrid=True
        ),
        showlegend=False,
        width=900,
        height=500,
        margin=dict(l=10, r=140, t=50, b=50)
    )

    # Convert to PNG bytes
    img_bytes = io.BytesIO()
    fig.write_image(img_bytes, format='png', scale=2)
    img_bytes.seek(0)

    return img_bytes


def generate_preview_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None
) -> io.BytesIO:
    """
    Generate a smaller preview chart for config viewing.

    Same as generate_chart but with smaller dimensions.
    """
    return generate_chart(config, candles_data, current_price)
