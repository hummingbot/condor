"""MACD BB V1 chart generation - candlestick with no price overlays."""

import io
from typing import Any, Dict, List, Optional

from handlers.dex.visualizations import generate_candlestick_chart


def generate_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
) -> io.BytesIO:
    trading_pair = config.get("trading_pair", "Unknown")
    interval = config.get("interval", "3m")
    bb_length = config.get("bb_length", 100)
    macd_fast = config.get("macd_fast", 21)
    macd_slow = config.get("macd_slow", 42)
    title = f"{trading_pair} - MACD BB (BB{bb_length} | MACD{macd_fast}/{macd_slow} | {interval})"
    data = candles_data if isinstance(candles_data, list) else candles_data.get("data", [])
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
