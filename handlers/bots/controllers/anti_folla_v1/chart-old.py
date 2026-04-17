"""Anti-Folla V1 chart generation - candlestick with VWAP and Donchian levels."""

import io
from typing import Any, Dict, List, Optional

from handlers.dex.visualizations import generate_candlestick_chart

from .analysis import calculate_rolling_vwap, calculate_donchian


def generate_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
) -> io.BytesIO:
    trading_pair = config.get("trading_pair", "Unknown")
    interval = config.get("interval", "3m")
    vwap_period = config.get("vwap_period", 20)
    donchian_period = config.get("donchian_period", 20)
    title = f"{trading_pair} - Anti-Folla (VWAP{vwap_period} | DC{donchian_period} | {interval})"

    data = candles_data if isinstance(candles_data, list) else candles_data.get("data", [])

    # Build hlines: VWAP, Donchian upper/lower for current bar
    hlines = []
    try:
        if data and len(data) >= max(vwap_period, donchian_period):
            vwap_series = calculate_rolling_vwap(data, vwap_period)
            if vwap_series:
                hlines.append(vwap_series[-1])

            dup, dlo = calculate_donchian(data, donchian_period)
            if dup:
                hlines.append(dup[-1])
            if dlo:
                hlines.append(dlo[-1])
    except Exception:
        pass

    return generate_candlestick_chart(
        candles=data,
        title=title,
        current_price=current_price,
        hlines=hlines,
        hrects=[],
    )


def generate_preview_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
) -> io.BytesIO:
    return generate_chart(config, candles_data, current_price)
