"""Arbitrage Controller chart - shows candlestick of exchange 1 pair with grid analysis overlay."""

import io
from typing import Any, Dict, List, Optional

from handlers.dex.visualizations import generate_candlestick_chart


def generate_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
    grid_analysis: Optional[Dict[str, Any]] = None,
) -> io.BytesIO:
    """
    Generate chart with candlestick data and optional grid analysis overlay.
    
    Args:
        config: Controller configuration
        candles_data: Candlestick data for chart
        current_price: Current market price
        grid_analysis: Grid analysis results for overlay
    """
    pair1 = config.get("exchange_pair_1", {}).get("trading_pair", "Unknown")
    pair2 = config.get("exchange_pair_2", {}).get("trading_pair", "Unknown")
    c1 = config.get("exchange_pair_1", {}).get("connector_name", "")
    c2 = config.get("exchange_pair_2", {}).get("connector_name", "")
    min_prof = config.get("min_profitability", 0)
    
    # Build title with grid status
    grid_enabled = config.get("grid_analysis", {}).get("enabled", False)
    grid_status = " | Grid: ON" if grid_enabled else ""
    
    title = (
        f"ARB: {c1} {pair1} ↔ {c2} {pair2} | "
        f"min profit: {float(min_prof)*100:.2f}%{grid_status}"
    )
    
    data = candles_data if isinstance(candles_data, list) else candles_data.get("data", [])
    
    # Prepare horizontal lines for grid levels if available
    hlines = []
    hrects = []
    
    if grid_analysis and grid_analysis.get("enabled", False):
        # Add grid levels as horizontal lines
        optimal = grid_analysis.get("optimal_entry")
        if optimal:
            hlines.append({
                "price": optimal["price1"],
                "color": "green",
                "width": 2,
                "label": f"Optimal Entry: {optimal['price1']:.2f}"
            })
        
        # Add top grid levels
        for i, level in enumerate(grid_analysis.get("grid_levels", [])[:3]):
            hlines.append({
                "price": level["price1"],
                "color": "orange",
                "width": 1,
                "style": "dashed",
                "label": f"Grid Level {i+1}: {level['price1']:.2f} ({level['profitability']*100:.1f}%)"
            })
        
        # Add spread zone as rectangle
        if current_price:
            spread_pct = grid_analysis.get("spread", 0)
            if spread_pct > 0:
                hrects.append({
                    "y_min": current_price * (1 - spread_pct),
                    "y_max": current_price * (1 + spread_pct),
                    "color": "rgba(100, 100, 255, 0.1)",
                    "label": f"Spread Zone: ±{spread_pct*100:.1f}%"
                })
    
    return generate_candlestick_chart(
        candles=data,
        title=title,
        current_price=current_price,
        hlines=hlines,
        hrects=hrects
    )


def generate_preview_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None
) -> io.BytesIO:
    """Generate preview chart without grid analysis."""
    return generate_chart(config, candles_data, current_price, grid_analysis=None)
