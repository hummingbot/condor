"""
PMM Mister analysis utilities.

Provides:
- NATR (Normalized ATR) calculation from candles
- Volatility analysis for spread parameter suggestions
- Theoretical spread level generation
- PMM metrics calculation and summary formatting
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


def suggest_pmm_params(
    current_price: float,
    natr: float,
    portfolio_value: float,
    allocation_pct: float = 0.05,
    min_notional: float = 5.0,
) -> Dict[str, Any]:
    """
    Suggest PMM parameters based on volatility analysis.

    Uses NATR to determine appropriate spread levels and take profit.

    Args:
        current_price: Current market price
        natr: Normalized ATR (as decimal, e.g., 0.02 for 2%)
        portfolio_value: Total portfolio value in quote currency
        allocation_pct: Fraction of portfolio to allocate
        min_notional: Minimum order value from trading rules

    Returns:
        Dict with suggested parameters:
        - buy_spreads: Suggested buy spread levels
        - sell_spreads: Suggested sell spread levels
        - take_profit: Suggested take profit
        - min_price_distance_pct: Suggested min price distance
        - reasoning: Explanation of suggestions
    """
    if not natr or natr <= 0:
        natr = 0.02  # Default 2% if no data

    # First spread level should be slightly above NATR to avoid immediate fills
    # Second spread level should be 2-3x NATR for deeper liquidity
    first_spread = natr * 1.2  # ~120% of NATR
    second_spread = natr * 2.5  # ~250% of NATR

    # Ensure minimums
    first_spread = max(first_spread, 0.0002)  # At least 0.02%
    second_spread = max(second_spread, 0.001)  # At least 0.1%

    # Take profit should be fraction of first spread
    suggested_tp = first_spread * 0.3
    suggested_tp = max(suggested_tp, 0.0001)  # At least 0.01%

    # Min price distance should be close to first spread
    min_price_distance = first_spread * 0.8
    min_price_distance = max(min_price_distance, 0.001)  # At least 0.1%

    # Calculate position sizing
    allocated_amount = portfolio_value * allocation_pct
    estimated_orders = int(allocated_amount / min_notional) if min_notional > 0 else 0

    reasoning = []
    reasoning.append(f"NATR: {natr*100:.2f}%")
    reasoning.append(f"L1 spread: {first_spread*100:.2f}%")
    reasoning.append(f"L2 spread: {second_spread*100:.2f}%")
    reasoning.append(f"Allocation: ${allocated_amount:,.0f}")

    if estimated_orders > 0:
        reasoning.append(f"Est. orders: ~{estimated_orders}")

    return {
        "buy_spreads": f"{round(first_spread, 4)},{round(second_spread, 4)}",
        "sell_spreads": f"{round(first_spread, 4)},{round(second_spread, 4)}",
        "take_profit": round(suggested_tp, 6),
        "min_buy_price_distance_pct": round(min_price_distance, 4),
        "min_sell_price_distance_pct": round(min_price_distance, 4),
        "estimated_orders": estimated_orders,
        "reasoning": " | ".join(reasoning),
    }


def generate_theoretical_levels(
    current_price: float,
    buy_spreads: List[float],
    sell_spreads: List[float],
    take_profit: float,
    portfolio_value: float,
    allocation_pct: float,
    buy_amounts_pct: Optional[List[float]] = None,
    sell_amounts_pct: Optional[List[float]] = None,
    min_notional: float = 5.0,
    trading_rules: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Generate theoretical PMM spread levels and order amounts.

    Args:
        current_price: Current market price
        buy_spreads: List of buy spread percentages (as decimals)
        sell_spreads: List of sell spread percentages (as decimals)
        take_profit: Take profit percentage (as decimal)
        portfolio_value: Total portfolio value
        allocation_pct: Portfolio allocation fraction
        buy_amounts_pct: Buy amount percentages per level
        sell_amounts_pct: Sell amount percentages per level
        min_notional: Minimum order notional
        trading_rules: Optional trading rules dict

    Returns:
        Dict containing level analysis results
    """
    warnings = []

    if current_price <= 0 or portfolio_value <= 0:
        return {
            "buy_levels": [],
            "sell_levels": [],
            "total_buy_amount": 0,
            "total_sell_amount": 0,
            "warnings": ["Invalid price or portfolio value"],
            "valid": False,
        }

    allocated_amount = portfolio_value * allocation_pct

    # Get trading rules values with defaults
    min_notional_val = min_notional
    if trading_rules:
        min_notional_val = max(min_notional, trading_rules.get("min_notional_size", 0))

    # Default amount percentages if not provided
    if not buy_amounts_pct:
        buy_amounts_pct = [1.0] * len(buy_spreads)
    if not sell_amounts_pct:
        sell_amounts_pct = [1.0] * len(sell_spreads)

    # Normalize amounts
    total_buy_pct = sum(buy_amounts_pct)
    total_sell_pct = sum(sell_amounts_pct)

    # Generate buy levels (below current price)
    buy_levels = []
    total_buy_amount = 0
    for i, spread in enumerate(buy_spreads):
        price = current_price * (1 - spread)
        pct = buy_amounts_pct[i] if i < len(buy_amounts_pct) else 1.0
        amount = (
            (allocated_amount / 2) * (pct / total_buy_pct) if total_buy_pct > 0 else 0
        )
        total_buy_amount += amount

        level = {
            "level": i + 1,
            "price": round(price, 8),
            "spread_pct": round(spread * 100, 3),
            "amount_quote": round(amount, 2),
            "tp_price": round(price * (1 + take_profit), 8),
        }
        buy_levels.append(level)

        if amount < min_notional_val:
            warnings.append(f"Buy L{i+1}: ${amount:.2f} < ${min_notional_val:.2f} min")

    # Generate sell levels (above current price)
    sell_levels = []
    total_sell_amount = 0
    for i, spread in enumerate(sell_spreads):
        price = current_price * (1 + spread)
        pct = sell_amounts_pct[i] if i < len(sell_amounts_pct) else 1.0
        amount = (
            (allocated_amount / 2) * (pct / total_sell_pct) if total_sell_pct > 0 else 0
        )
        total_sell_amount += amount

        level = {
            "level": i + 1,
            "price": round(price, 8),
            "spread_pct": round(spread * 100, 3),
            "amount_quote": round(amount, 2),
            "tp_price": round(price * (1 - take_profit), 8),
        }
        sell_levels.append(level)

        if amount < min_notional_val:
            warnings.append(f"Sell L{i+1}: ${amount:.2f} < ${min_notional_val:.2f} min")

    # Validate take profit vs spread
    if buy_spreads and take_profit >= min(buy_spreads):
        warnings.append(
            f"TP {take_profit*100:.2f}% >= min spread {min(buy_spreads)*100:.2f}%"
        )
    if sell_spreads and take_profit >= min(sell_spreads):
        warnings.append(
            f"TP {take_profit*100:.2f}% >= min spread {min(sell_spreads)*100:.2f}%"
        )

    return {
        "buy_levels": buy_levels,
        "sell_levels": sell_levels,
        "total_buy_amount": round(total_buy_amount, 2),
        "total_sell_amount": round(total_sell_amount, 2),
        "total_allocated": round(allocated_amount, 2),
        "num_buy_levels": len(buy_levels),
        "num_sell_levels": len(sell_levels),
        "warnings": warnings,
        "valid": len(warnings) == 0,
    }


def format_pmm_summary(
    levels: Dict[str, Any],
    natr: Optional[float] = None,
    take_profit: float = 0.0001,
) -> str:
    """
    Format PMM analysis for display.

    Args:
        levels: Levels dict from generate_theoretical_levels
        natr: Optional NATR value
        take_profit: Take profit percentage (as decimal)

    Returns:
        Formatted summary string (not escaped for markdown)
    """
    lines = []

    # Buy levels
    lines.append(f"Buy Levels: {levels.get('num_buy_levels', 0)}")
    for lvl in levels.get("buy_levels", []):
        lines.append(
            f"  L{lvl['level']}: {lvl['price']:,.4f} (-{lvl['spread_pct']:.2f}%) ${lvl['amount_quote']:.0f}"
        )

    # Sell levels
    lines.append(f"Sell Levels: {levels.get('num_sell_levels', 0)}")
    for lvl in levels.get("sell_levels", []):
        lines.append(
            f"  L{lvl['level']}: {lvl['price']:,.4f} (+{lvl['spread_pct']:.2f}%) ${lvl['amount_quote']:.0f}"
        )

    lines.append(f"Total Buy: ${levels.get('total_buy_amount', 0):,.2f}")
    lines.append(f"Total Sell: ${levels.get('total_sell_amount', 0):,.2f}")
    lines.append(f"Take Profit: {take_profit*100:.3f}%")

    if natr:
        lines.append(f"NATR (14): {natr*100:.2f}%")

    if levels.get("warnings"):
        lines.append("Warnings:")
        for w in levels["warnings"]:
            lines.append(f"  - {w}")

    return "\n".join(lines)


def calculate_effective_spread(
    buy_spreads: List[float],
    sell_spreads: List[float],
    buy_amounts_pct: List[float],
    sell_amounts_pct: List[float],
) -> Dict[str, float]:
    """
    Calculate effective weighted average spreads.

    Args:
        buy_spreads: List of buy spreads (as decimals)
        sell_spreads: List of sell spreads (as decimals)
        buy_amounts_pct: Relative amounts per buy level
        sell_amounts_pct: Relative amounts per sell level

    Returns:
        Dict with:
        - weighted_buy_spread: Amount-weighted average buy spread
        - weighted_sell_spread: Amount-weighted average sell spread
        - min_buy_spread: Smallest buy spread
        - min_sell_spread: Smallest sell spread
        - max_buy_spread: Largest buy spread
        - max_sell_spread: Largest sell spread
    """
    # Calculate weighted buy spread
    total_buy_pct = sum(buy_amounts_pct) if buy_amounts_pct else 0
    if total_buy_pct > 0 and buy_spreads:
        weighted_buy = (
            sum(s * p for s, p in zip(buy_spreads, buy_amounts_pct)) / total_buy_pct
        )
    else:
        weighted_buy = buy_spreads[0] if buy_spreads else 0

    # Calculate weighted sell spread
    total_sell_pct = sum(sell_amounts_pct) if sell_amounts_pct else 0
    if total_sell_pct > 0 and sell_spreads:
        weighted_sell = (
            sum(s * p for s, p in zip(sell_spreads, sell_amounts_pct)) / total_sell_pct
        )
    else:
        weighted_sell = sell_spreads[0] if sell_spreads else 0

    return {
        "weighted_buy_spread": weighted_buy,
        "weighted_sell_spread": weighted_sell,
        "min_buy_spread": min(buy_spreads) if buy_spreads else 0,
        "min_sell_spread": min(sell_spreads) if sell_spreads else 0,
        "max_buy_spread": max(buy_spreads) if buy_spreads else 0,
        "max_sell_spread": max(sell_spreads) if sell_spreads else 0,
    }
