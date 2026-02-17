"""
PMM Mister chart generation.

Generates candlestick charts with PMM spread visualization:
- Buy spread levels (green dashed lines)
- Sell spread levels (red dashed lines)
- Current price line
- Take profit zone indicator

Uses the unified candlestick chart function from visualizations module.
"""

import io
from typing import Any, Dict, List, Optional

from handlers.dex.visualizations import DARK_THEME, generate_candlestick_chart

from .config import parse_spreads


def generate_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
) -> io.BytesIO:
    """
    Generate a candlestick chart with PMM spread overlay.

    The chart shows:
    - Candlestick price data
    - Buy spread levels (green dashed lines below price)
    - Sell spread levels (red dashed lines above price)
    - Take profit zone (shaded area around current price)
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
    data = (
        candles_data if isinstance(candles_data, list) else candles_data.get("data", [])
    )

    # Build title
    title = f"{trading_pair} - PMM Mister"

    # Get reference price for spread calculations
    ref_price = current_price
    if not ref_price and data:
        # Use last close if no current price provided
        last_candle = data[-1] if isinstance(data[-1], dict) else None
        if last_candle:
            ref_price = last_candle.get("close", 0)

    # Build horizontal lines for spread overlays
    hlines = []

    # Add buy spread levels (below current price)
    if ref_price and buy_spreads:
        for i, spread in enumerate(buy_spreads):
            buy_price = ref_price * (1 - spread)
            # Fade opacity for further levels
            opacity_suffix = "" if i == 0 else f" (L{i+1})"
            hlines.append(
                {
                    "y": buy_price,
                    "color": DARK_THEME["up_color"],
                    "dash": "dash",
                    "width": 2 if i == 0 else 1,
                    "label": f"Buy{opacity_suffix}: {buy_price:,.4f} (-{spread*100:.1f}%)",
                    "label_position": "left",
                }
            )

    # Add sell spread levels (above current price)
    if ref_price and sell_spreads:
        for i, spread in enumerate(sell_spreads):
            sell_price = ref_price * (1 + spread)
            opacity_suffix = "" if i == 0 else f" (L{i+1})"
            hlines.append(
                {
                    "y": sell_price,
                    "color": DARK_THEME["down_color"],
                    "dash": "dash",
                    "width": 2 if i == 0 else 1,
                    "label": f"Sell{opacity_suffix}: {sell_price:,.4f} (+{spread*100:.1f}%)",
                    "label_position": "right",
                }
            )

    # Build horizontal rectangles for take profit zone
    hrects = []
    if ref_price and take_profit:
        tp_up = ref_price * (1 + take_profit)
        tp_down = ref_price * (1 - take_profit)
        hrects.append(
            {
                "y0": tp_down,
                "y1": tp_up,
                "color": "rgba(245, 158, 11, 0.1)",  # Light orange
                "label": f"TP Zone ({take_profit*100:.2f}%)",
            }
        )

    # Use the unified candlestick chart function
    result = generate_candlestick_chart(
        candles=data,
        title=title,
        current_price=current_price,
        show_volume=False,  # PMM chart doesn't show volume
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
