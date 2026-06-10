"""XEMM Multiple Levels chart - simple candlestick of maker pair."""

import io
from typing import Any, Dict, List, Optional

from handlers.dex.visualizations import generate_candlestick_chart


def generate_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
) -> io.BytesIO:
    maker = config.get("maker_connector", "")
    taker = config.get("taker_connector", "")
    pair = config.get("maker_trading_pair", "Unknown")
    title = f"XEMM: {maker} → {taker} | {pair}"
    data = candles_data if isinstance(candles_data, list) else candles_data.get("data", [])
    return generate_candlestick_chart(candles=data, title=title, current_price=current_price, hlines=[], hrects=[])


def generate_preview_chart(config, candles_data, current_price=None):
    return generate_chart(config, candles_data, current_price)
