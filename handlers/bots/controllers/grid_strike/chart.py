"""
Grid Strike chart generation.

Generates candlestick charts with grid zone visualization:
- Grid zone (shaded area between start and end price)
- Start price line (entry zone start)
- End price line (entry zone end)
- Limit price line (stop loss)
- Current price line
"""

import io
from datetime import datetime
from typing import Any, Dict, List, Optional

import plotly.graph_objects as go

from .config import SIDE_LONG


# Dark theme (consistent with portfolio_graphs.py)
DARK_THEME = {
    "bgcolor": "#0a0e14",
    "paper_bgcolor": "#0a0e14",
    "plot_bgcolor": "#131720",
    "font_color": "#e6edf3",
    "font_family": "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif",
    "grid_color": "#21262d",
    "axis_color": "#8b949e",
    "up_color": "#10b981",    # Green for bullish
    "down_color": "#ef4444",  # Red for bearish
    "line_color": "#3b82f6",  # Blue for lines
}


def generate_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None
) -> io.BytesIO:
    """
    Generate a candlestick chart with grid strike zone overlay.

    The chart shows:
    - Candlestick price data
    - Grid zone (shaded area between start and end prices)
    - Start price line (blue dashed)
    - End price line (blue dashed)
    - Limit price line (red dotted) - stop loss level
    - Current price line (orange solid)

    Args:
        config: Grid strike configuration with start_price, end_price, limit_price
        candles_data: List of candles from API (each with open, high, low, close, timestamp)
        current_price: Current market price

    Returns:
        BytesIO object containing the PNG image
    """
    start_price = config.get("start_price")
    end_price = config.get("end_price")
    limit_price = config.get("limit_price")
    trading_pair = config.get("trading_pair", "Unknown")
    side = config.get("side", SIDE_LONG)

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
        datetime_objs = []  # Store datetime objects for intelligent tick labeling
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
                datetime_objs.append(dt)
                timestamps.append(dt)  # Use datetime directly for x-axis
            else:
                timestamps.append(str(raw_ts))
                datetime_objs.append(None)

            opens.append(candle.get("open", 0))
            highs.append(candle.get("high", 0))
            lows.append(candle.get("low", 0))
            closes.append(candle.get("close", 0))

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

        # Add grid zone overlay (shaded area between start and end)
        if start_price and end_price:
            fig.add_hrect(
                y0=min(start_price, end_price),
                y1=max(start_price, end_price),
                fillcolor="rgba(59, 130, 246, 0.15)",  # Light blue
                line_width=0,
                annotation_text="Grid Zone",
                annotation_position="top left",
                annotation_font=dict(color=DARK_THEME["font_color"], size=11)
            )

            # Start price line
            fig.add_hline(
                y=start_price,
                line_dash="dash",
                line_color="#3b82f6",
                line_width=2,
                annotation_text=f"Start: {start_price:,.4f}",
                annotation_position="right",
                annotation_font=dict(color="#3b82f6", size=10)
            )

            # End price line
            fig.add_hline(
                y=end_price,
                line_dash="dash",
                line_color="#3b82f6",
                line_width=2,
                annotation_text=f"End: {end_price:,.4f}",
                annotation_position="right",
                annotation_font=dict(color="#3b82f6", size=10)
            )

        # Limit price line (stop loss)
        if limit_price:
            fig.add_hline(
                y=limit_price,
                line_dash="dot",
                line_color="#ef4444",
                line_width=2,
                annotation_text=f"Limit: {limit_price:,.4f}",
                annotation_position="right",
                annotation_font=dict(color="#ef4444", size=10)
            )

        # Current price line
        if current_price:
            fig.add_hline(
                y=current_price,
                line_dash="solid",
                line_color="#f59e0b",
                line_width=2,
                annotation_text=f"Current: {current_price:,.4f}",
                annotation_position="left",
                annotation_font=dict(color="#f59e0b", size=10)
            )

    # Build title with side indicator
    side_str = "LONG" if side == SIDE_LONG else "SHORT"
    title_text = f"<b>{trading_pair}</b> - Grid Strike ({side_str})"

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
            nticks=8,  # Limit number of ticks to prevent crowding
            tickformat="%b %d\n%H:%M",  # Multi-line format: "Dec 4" on first line, "20:00" on second
            tickangle=0,  # Keep labels horizontal
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
        margin=dict(l=10, r=120, t=50, b=50)  # Increased bottom margin for multi-line x-axis labels
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
    # Use the same logic but we could customize dimensions here if needed
    return generate_chart(config, candles_data, current_price)
