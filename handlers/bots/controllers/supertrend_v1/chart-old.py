"""SuperTrend V1 chart generation - candlestick with SuperTrend line overlay."""

import io
from typing import Any, Dict, List, Optional

from handlers.dex.visualizations import generate_candlestick_chart


def generate_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
    supertrend_value: Optional[float] = None,
    direction: Optional[int] = None,
) -> io.BytesIO:
    trading_pair = config.get("trading_pair", "Unknown")
    interval = config.get("interval", "3m")
    length = config.get("length", 20)
    multiplier = config.get("multiplier", 4.0)
    title = f"{trading_pair} - SuperTrend (length={length}, mult={multiplier} | {interval})"
    data = candles_data if isinstance(candles_data, list) else candles_data.get("data", [])

    # Show SuperTrend line as hline if available
    hlines = []
    if supertrend_value is not None:
        hlines.append(supertrend_value)

    return generate_candlestick_chart(
        candles=data,
        title=title,
        current_price=current_price,
        hlines=[],
        hrects=[],
    )


def generate_preview_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
) -> io.BytesIO:
    return generate_chart(config, candles_data, current_price)
