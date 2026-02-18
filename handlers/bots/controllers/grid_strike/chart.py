"""
Grid Strike chart generation.

Generates candlestick charts with grid zone visualization:
- Grid zone (shaded area between start and end price)
- Start price line (entry zone start)
- End price line (entry zone end)
- Limit price line (stop loss)
- Current price line

Uses the unified candlestick chart function from visualizations module.
"""

import io
from typing import Any, Dict, List, Optional

from handlers.dex.visualizations import DARK_THEME, generate_candlestick_chart

from .config import SIDE_LONG


def generate_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
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
    data = (
        candles_data if isinstance(candles_data, list) else candles_data.get("data", [])
    )

    # Build title with side indicator
    side_str = "LONG" if side == SIDE_LONG else "SHORT"
    title = f"{trading_pair} - Grid Strike ({side_str})"

    # Build horizontal lines for grid strike overlays
    hlines = []

    if start_price:
        hlines.append(
            {
                "y": start_price,
                "color": DARK_THEME["line_color"],
                "dash": "dash",
                "label": f"Start: {start_price:,.4f}",
                "label_position": "right",
            }
        )

    if end_price:
        hlines.append(
            {
                "y": end_price,
                "color": DARK_THEME["line_color"],
                "dash": "dash",
                "label": f"End: {end_price:,.4f}",
                "label_position": "right",
            }
        )

    if limit_price:
        hlines.append(
            {
                "y": limit_price,
                "color": DARK_THEME["down_color"],
                "dash": "dot",
                "label": f"Limit: {limit_price:,.4f}",
                "label_position": "right",
            }
        )

    # Build horizontal rectangles for grid zone
    hrects = []

    if start_price and end_price:
        hrects.append(
            {
                "y0": min(start_price, end_price),
                "y1": max(start_price, end_price),
                "color": "rgba(59, 130, 246, 0.15)",  # Light blue
                "label": "Grid Zone",
            }
        )

    # Use the unified candlestick chart function
    result = generate_candlestick_chart(
        candles=data,
        title=title,
        current_price=current_price,
        show_volume=False,  # Grid strike doesn't show volume
        width=1100,
        height=500,
        hlines=hlines if hlines else None,
        hrects=hrects if hrects else None,
        reverse_data=False,  # CEX data is already in chronological order
    )

    # Handle empty chart case
    if result is None:
        import plotly.graph_objects as go

        fig = go.Figure()
        fig.add_annotation(
            text="No candle data available",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
            font=dict(
                family=DARK_THEME["font_family"],
                size=16,
                color=DARK_THEME["font_color"],
            ),
        )
        fig.update_layout(
            paper_bgcolor=DARK_THEME["paper_bgcolor"],
            plot_bgcolor=DARK_THEME["plot_bgcolor"],
            width=1100,
            height=500,
        )

        img_bytes = io.BytesIO()
        fig.write_image(img_bytes, format="png", scale=2)
        img_bytes.seek(0)
        return img_bytes

    return result


def generate_preview_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
) -> io.BytesIO:
    """
    Generate a smaller preview chart for config viewing.

    Same as generate_chart but with smaller dimensions.
    """
    # Use the same logic but we could customize dimensions here if needed
    return generate_chart(config, candles_data, current_price)
