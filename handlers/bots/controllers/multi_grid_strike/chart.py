"""
Multi Grid Strike chart generation.

Generates a candlestick chart with multiple grid zones overlaid,
one per enabled grid in the configuration.

Each grid is shown with:
- A shaded zone between start_price and end_price (unique color per grid)
- Start/End price lines (dashed)
- Limit price line (dotted red) — stop loss level
- Current price line (orange)
"""

import io
from typing import Any, Dict, List, Optional

from handlers.dex.visualizations import DARK_THEME, generate_candlestick_chart

from .config import SIDE_LONG

# Distinct colors for up to 6 grids
GRID_COLORS = [
    "#4A9EFF",  # blue
    "#50C878",  # green
    "#FFB347",  # orange
    "#DA70D6",  # orchid
    "#FF6B6B",  # red
    "#87CEEB",  # sky blue
]


def generate_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
) -> io.BytesIO:
    """
    Generate a candlestick chart with all multi-grid zones overlaid.

    Args:
        config: MultiGridStrike configuration dict (with 'grids' list)
        candles_data: OHLCV candle data
        current_price: Current market price

    Returns:
        BytesIO containing PNG image
    """
    trading_pair = config.get("trading_pair", "Unknown")
    grids = config.get("grids", [])

    data = (
        candles_data if isinstance(candles_data, list) else candles_data.get("data", [])
    )

    total_amount = config.get("total_amount_quote", 0)
    active_grids = [g for g in grids if g.get("enabled", True)]
    title = f"{trading_pair} - Multi Grid Strike ({len(active_grids)} grids | ${total_amount:.0f} total)"

    hlines = []
    hrects = []

    for i, grid in enumerate(active_grids):
        color = GRID_COLORS[i % len(GRID_COLORS)]
        grid_id = grid.get("grid_id", f"grid_{i+1}")
        side = grid.get("side", SIDE_LONG)
        side_str = "L" if side == SIDE_LONG else "S"
        start_price = grid.get("start_price")
        end_price = grid.get("end_price")
        limit_price = grid.get("limit_price")
        pct = grid.get("amount_quote_pct", 0)
        amount = total_amount * pct

        if start_price:
            hlines.append({
                "y": start_price,
                "color": color,
                "dash": "dash",
                "label": f"[{grid_id}] Start: {start_price:,.4f}",
                "label_position": "right",
            })

        if end_price:
            hlines.append({
                "y": end_price,
                "color": color,
                "dash": "dash",
                "label": f"[{grid_id}] End: {end_price:,.4f} ({side_str} ${amount:.0f})",
                "label_position": "right",
            })

        if limit_price:
            hlines.append({
                "y": limit_price,
                "color": DARK_THEME["down_color"],
                "dash": "dot",
                "label": f"[{grid_id}] Limit: {limit_price:,.4f}",
                "label_position": "left",
            })

        if start_price and end_price:
            hrects.append({
                "y0": min(start_price, end_price),
                "y1": max(start_price, end_price),
                "color": color,
                "opacity": 0.07,
            })

    return generate_candlestick_chart(
        candles=data,
        title=title,
        current_price=current_price,
        hlines=hlines,
        hrects=hrects,
    )


def generate_preview_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
) -> io.BytesIO:
    """Alias for generate_chart — used during wizard preview."""
    return generate_chart(config, candles_data, current_price)
