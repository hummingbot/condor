"""
Shared OHLCV analysis helpers for controller types.

Generic candle math with no strategy-specific concepts, used by every
controller package (grid_strike, pmm_mister, ...). Strategy-specific
analysis lives in each package's own ``*_analysis.py``.
"""

from typing import Any, Dict, List, Optional


def calculate_natr(candles: List[Dict[str, Any]], period: int = 14) -> Optional[float]:
    """
    Calculate Normalized Average True Range (NATR) from candles.

    NATR = (ATR / Close) * 100, expressed as a percentage.

    Args:
        candles: List of candle dicts with high, low, close keys
        period: ATR period (default 14)

    Returns:
        NATR as decimal (e.g., 0.025 for 2.5%), or None if insufficient data
    """
    if not candles or len(candles) < period + 1:
        return None

    # Calculate True Range for each candle
    true_ranges = []
    for i in range(1, len(candles)):
        high = candles[i].get("high", 0)
        low = candles[i].get("low", 0)
        prev_close = candles[i - 1].get("close", 0)

        if not all([high, low, prev_close]):
            continue

        # True Range = max(high - low, |high - prev_close|, |low - prev_close|)
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    # Calculate ATR as simple moving average of TR
    atr = sum(true_ranges[-period:]) / period

    # Normalize by current close price
    current_close = candles[-1].get("close", 0)
    if current_close <= 0:
        return None

    natr = atr / current_close
    return natr


def calculate_price_stats(
    candles: List[Dict[str, Any]], lookback: int = 100
) -> Dict[str, float]:
    """
    Calculate price statistics from candles.

    Args:
        candles: List of candle dicts
        lookback: Number of candles to analyze

    Returns:
        Dict with price statistics:
        - current_price: Latest close
        - high_price: Highest high in period
        - low_price: Lowest low in period
        - range_pct: (high - low) / current as percentage
        - avg_candle_range: Average (high-low)/close per candle
        - natr_14: 14-period NATR
        - natr_50: 50-period NATR (if enough data)
    """
    if not candles:
        return {}

    recent = candles[-lookback:] if len(candles) > lookback else candles

    current_price = recent[-1].get("close", 0)
    if current_price <= 0:
        return {}

    highs = [c.get("high", 0) for c in recent if c.get("high")]
    lows = [c.get("low", 0) for c in recent if c.get("low")]

    high_price = max(highs) if highs else current_price
    low_price = min(lows) if lows else current_price

    range_pct = (high_price - low_price) / current_price if current_price > 0 else 0

    # Average candle range
    candle_ranges = []
    for c in recent:
        h, l, close = c.get("high", 0), c.get("low", 0), c.get("close", 0)
        if h and l and close:
            candle_ranges.append((h - l) / close)
    avg_candle_range = sum(candle_ranges) / len(candle_ranges) if candle_ranges else 0

    return {
        "current_price": current_price,
        "high_price": high_price,
        "low_price": low_price,
        "range_pct": range_pct,
        "avg_candle_range": avg_candle_range,
        "natr_14": calculate_natr(candles, 14),
        "natr_50": calculate_natr(candles, 50) if len(candles) >= 51 else None,
    }
