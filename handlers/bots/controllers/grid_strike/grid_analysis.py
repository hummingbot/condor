"""
Grid Strike analysis utilities.

Provides:
- NATR (Normalized ATR) calculation from candles
- Volatility analysis for grid parameter suggestions
- Theoretical grid generation with trading rules validation
- Grid metrics calculation
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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


def suggest_grid_params(
    current_price: float,
    natr: float,
    side: int,
    total_amount: float,
    min_notional: float = 5.0,
    min_price_increment: float = 0.0001,
) -> Dict[str, Any]:
    """
    Suggest grid parameters based on volatility analysis.

    Uses NATR to determine appropriate grid spacing and range.

    Args:
        current_price: Current market price
        natr: Normalized ATR (as decimal, e.g., 0.02 for 2%)
        side: 1 for LONG, 2 for SHORT
        total_amount: Total amount in quote currency
        min_notional: Minimum order value from trading rules
        min_price_increment: Price tick size

    Returns:
        Dict with suggested parameters:
        - start_price, end_price, limit_price
        - min_spread_between_orders
        - take_profit
        - estimated_levels: Number of grid levels
        - reasoning: Explanation of suggestions
    """
    if not natr or natr <= 0:
        natr = 0.02  # Default 2% if no data

    # Grid range based on NATR
    # Use 3-5x daily NATR for the full grid range
    # For 1m candles, NATR is per-minute, so scale appropriately
    grid_range = natr * 3  # Grid covers ~3 NATR

    # Minimum spread should be at least 1-2x NATR
    suggested_spread = natr * 1.5

    # Take profit should be smaller than spread
    suggested_tp = natr * 0.5

    # Ensure minimums
    suggested_spread = max(suggested_spread, 0.0002)  # At least 0.02%
    suggested_tp = max(suggested_tp, 0.0001)  # At least 0.01%

    # Calculate prices based on side using 3:1 ratio
    # Total range = 4 units (1 unit on one side, 3 units on the other)
    unit = grid_range / 4

    if side == 1:  # LONG
        # LONG: small range below (-1 unit), larger range above (+3 units)
        start_price = current_price * (1 - unit)
        end_price = current_price * (1 + unit * 3)
        limit_price = start_price * (1 - unit)  # Stop below start
    else:  # SHORT
        # SHORT: larger range below (-3 units), small range above (+1 unit)
        start_price = current_price * (1 - unit * 3)
        end_price = current_price * (1 + unit)
        limit_price = end_price * (1 + unit)  # Stop above end

    # Estimate number of levels
    price_range = abs(end_price - start_price)
    price_per_level = current_price * suggested_spread
    estimated_levels = int(price_range / price_per_level) if price_per_level > 0 else 0

    # Check if we have enough capital for the levels
    min_levels = max(1, int(total_amount / min_notional))

    reasoning = []
    reasoning.append(f"NATR: {natr*100:.2f}%")
    reasoning.append(f"Grid range: {grid_range*100:.1f}%")
    reasoning.append(f"Est. levels: ~{estimated_levels}")

    if estimated_levels > min_levels:
        reasoning.append(
            f"Capital allows ~{min_levels} orders at ${min_notional:.0f} min"
        )

    return {
        "start_price": round(start_price, 8),
        "end_price": round(end_price, 8),
        "limit_price": round(limit_price, 8),
        "min_spread_between_orders": round(suggested_spread, 6),
        "take_profit": round(suggested_tp, 6),
        "estimated_levels": estimated_levels,
        "reasoning": " | ".join(reasoning),
    }


def generate_theoretical_grid(
    start_price: float,
    end_price: float,
    min_spread: float,
    total_amount: float,
    min_order_amount: float,
    current_price: float,
    side: int,
    trading_rules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Generate theoretical grid levels matching the executor's _generate_grid_levels logic.

    This implementation mirrors the actual GridStrikeExecutor._generate_grid_levels() method,
    including proper base amount quantization and level calculation.

    Args:
        start_price: Grid start price
        end_price: Grid end price
        min_spread: Minimum spread between orders (as decimal)
        total_amount: Total quote amount
        min_order_amount: Minimum order amount in quote
        current_price: Current market price
        side: 1 for LONG, 2 for SHORT
        trading_rules: Optional trading rules dict for validation

    Returns:
        Dict containing grid analysis results
    """
    import math

    warnings = []

    # Ensure proper ordering
    low_price = min(start_price, end_price)
    high_price = max(start_price, end_price)

    if low_price <= 0 or high_price <= low_price or current_price <= 0:
        return {
            "levels": [],
            "amount_per_level": 0,
            "num_levels": 0,
            "grid_range_pct": 0,
            "warnings": ["Invalid price range"],
            "valid": False,
        }

    # Calculate grid range as percentage (matches executor)
    grid_range = (high_price - low_price) / low_price
    grid_range_pct = grid_range * 100

    # Get trading rules values with defaults
    min_notional = min_order_amount
    min_price_increment = 0.0001
    min_base_increment = 0.0001

    if trading_rules:
        min_notional = max(min_order_amount, trading_rules.get("min_notional_size", 0))
        min_price_increment = trading_rules.get("min_price_increment", 0.0001) or 0.0001
        min_base_increment = (
            trading_rules.get("min_base_amount_increment", 0.0001) or 0.0001
        )

    # Add safety margin (executor uses 1.05)
    min_notional_with_margin = min_notional * 1.05

    # Calculate minimum base amount that satisfies both min_notional and quantization
    # (matches executor logic)
    min_base_from_notional = min_notional_with_margin / current_price
    min_base_from_quantization = min_base_increment * math.ceil(
        min_notional / (min_base_increment * current_price)
    )
    min_base_amount = max(min_base_from_notional, min_base_from_quantization)

    # Quantize the minimum base amount (round up to increment)
    min_base_amount = (
        math.ceil(min_base_amount / min_base_increment) * min_base_increment
    )

    # Calculate minimum quote amount from quantized base
    min_quote_amount = min_base_amount * current_price

    # Calculate minimum step size (matches executor)
    min_step_size = max(min_spread, min_price_increment / current_price)

    # Calculate maximum possible levels based on total amount
    max_possible_levels = (
        int(total_amount / min_quote_amount) if min_quote_amount > 0 else 0
    )

    if max_possible_levels == 0:
        return {
            "levels": [],
            "amount_per_level": 0,
            "num_levels": 0,
            "grid_range_pct": grid_range_pct,
            "warnings": [f"Need ${min_quote_amount:.2f} min, have ${total_amount:.2f}"],
            "valid": False,
        }

    # Calculate optimal number of levels (matches executor)
    max_levels_by_step = (
        int(grid_range / min_step_size) if min_step_size > 0 else max_possible_levels
    )
    n_levels = min(max_possible_levels, max_levels_by_step)

    if n_levels == 0:
        n_levels = 1
        quote_amount_per_level = min_quote_amount
    else:
        # Calculate base amount per level with quantization (matches executor)
        base_amount_per_level = max(
            min_base_amount,
            math.floor(total_amount / (current_price * n_levels) / min_base_increment)
            * min_base_increment,
        )
        quote_amount_per_level = base_amount_per_level * current_price

        # Adjust number of levels if total amount would be exceeded
        n_levels = min(n_levels, int(total_amount / quote_amount_per_level))

    # Ensure at least one level
    n_levels = max(1, n_levels)

    # Generate price levels with linear distribution (matches executor's Distributions.linear)
    levels = []
    if n_levels > 1:
        for i in range(n_levels):
            price = low_price + (high_price - low_price) * i / (n_levels - 1)
            levels.append(round(price, 8))
        step = grid_range / (n_levels - 1)
    else:
        mid_price = (low_price + high_price) / 2
        levels.append(round(mid_price, 8))
        step = grid_range

    # Recalculate final amount per level
    amount_per_level = total_amount / n_levels if n_levels > 0 else 0

    # Validation warnings
    if amount_per_level < min_notional:
        warnings.append(f"${amount_per_level:.2f}/lvl < ${min_notional:.2f} min")

    if trading_rules:
        min_order_size = trading_rules.get("min_order_size", 0)
        if min_order_size and current_price > 0:
            base_per_level = amount_per_level / current_price
            if base_per_level < min_order_size:
                warnings.append(f"Below min size ({min_order_size})")

    if n_levels > 1 and step < min_spread:
        warnings.append(f"Spread {step*100:.3f}% < min {min_spread*100:.3f}%")

    # Determine which levels are above/below current price
    levels_below = [l for l in levels if l < current_price]
    levels_above = [l for l in levels if l >= current_price]

    return {
        "levels": levels,
        "levels_below_current": len(levels_below),
        "levels_above_current": len(levels_above),
        "amount_per_level": round(amount_per_level, 2),
        "num_levels": n_levels,
        "grid_range_pct": round(grid_range_pct, 3),
        "price_step": round(step * low_price, 8) if n_levels > 1 else 0,
        "spread_pct": (
            round(step * 100, 3) if n_levels > 1 else round(min_spread * 100, 3)
        ),
        "max_levels_by_budget": max_possible_levels,
        "max_levels_by_spread": max_levels_by_step,
        "warnings": warnings,
        "valid": len(warnings) == 0,
    }


def format_grid_summary(
    grid: Dict[str, Any],
    natr: Optional[float] = None,
    take_profit: float = 0.0001,
) -> str:
    """
    Format grid analysis for display.

    Args:
        grid: Grid dict from generate_theoretical_grid
        natr: Optional NATR value
        take_profit: Take profit percentage (as decimal)

    Returns:
        Formatted summary string (not escaped for markdown)
    """
    lines = []

    # Grid levels info
    lines.append(f"Levels: {grid['num_levels']}")
    lines.append(f"  Below current: {grid.get('levels_below_current', 0)}")
    lines.append(f"  Above current: {grid.get('levels_above_current', 0)}")
    lines.append(f"Amount/level: ${grid['amount_per_level']:.2f}")
    lines.append(f"Spread: {grid.get('spread_pct', 0):.3f}%")
    lines.append(f"Take Profit: {take_profit*100:.3f}%")

    if natr:
        lines.append(f"NATR (14): {natr*100:.2f}%")

    if grid.get("warnings"):
        lines.append("Warnings:")
        for w in grid["warnings"]:
            lines.append(f"  - {w}")

    return "\n".join(lines)
