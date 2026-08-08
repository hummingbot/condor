"""
Multi Grid Strike analysis utilities.

Adapts the grid_strike analysis functions for multi-grid configurations.

Provides:
- NATR calculation (identical to grid_strike)
- Per-grid parameter suggestions based on volatility
- Theoretical grid generation for each grid in the config
- Combined summary across all grids
- Multi-grid generation based on strategy type
"""

import logging
import math
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================
# Side constants
# ============================================

SIDE_LONG = 1
SIDE_SHORT = 2


def side_str(side: int) -> str:
    """Convert side int to string"""
    return "LONG" if side == SIDE_LONG else "SHORT"


# ============================================
# NATR & price stats (identical to grid_strike)
# ============================================

def calculate_natr(candles: List[Dict[str, Any]], period: int = 14) -> Optional[float]:
    """
    Calculate Normalized Average True Range (NATR) from candles.

    NATR = (ATR / Close) * 100, expressed as a decimal (e.g. 0.025 = 2.5%).

    Args:
        candles: List of candle dicts with high, low, close keys
        period: ATR period (default 14)

    Returns:
        NATR as decimal, or None if insufficient data
    """
    if not candles or len(candles) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(candles)):
        high = candles[i].get("high", 0)
        low = candles[i].get("low", 0)
        prev_close = candles[i - 1].get("close", 0)

        if not all([high, low, prev_close]):
            continue

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    atr = sum(true_ranges[-period:]) / period
    current_close = candles[-1].get("close", 0)
    if current_close <= 0:
        return None

    return atr / current_close


def calculate_price_stats(
    candles: List[Dict[str, Any]], lookback: int = 100
) -> Dict[str, float]:
    """
    Calculate price statistics from candles.

    Returns:
        Dict with current_price, high_price, low_price, range_pct,
        avg_candle_range, natr_14, natr_50
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


# ============================================
# Per-grid theoretical generation
# ============================================

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
    Generate theoretical grid levels for a single grid, mirroring the
    GridExecutor._generate_grid_levels() logic.

    Args:
        start_price: Grid start price
        end_price: Grid end price
        min_spread: Minimum spread between orders (as decimal)
        total_amount: Quote amount allocated to this grid
        min_order_amount: Minimum order amount in quote
        current_price: Current market price
        side: 1=LONG, 2=SHORT
        trading_rules: Optional trading rules dict for validation

    Returns:
        Dict with levels, num_levels, amount_per_level, spread_pct,
        warnings, valid, etc.
    """
    warnings = []

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

    grid_range = (high_price - low_price) / low_price
    grid_range_pct = grid_range * 100

    # Trading rules
    min_notional = min_order_amount
    min_price_increment = 0.0001
    min_base_increment = 0.0001

    if trading_rules:
        min_notional = max(min_order_amount, trading_rules.get("min_notional_size", 0))
        min_price_increment = trading_rules.get("min_price_increment", 0.0001) or 0.0001
        min_base_increment = trading_rules.get("min_base_amount_increment", 0.0001) or 0.0001

    min_notional_with_margin = min_notional * 1.05

    min_base_from_notional = min_notional_with_margin / current_price
    min_base_from_quantization = min_base_increment * math.ceil(
        min_notional / (min_base_increment * current_price)
    )
    min_base_amount = max(min_base_from_notional, min_base_from_quantization)
    min_base_amount = math.ceil(min_base_amount / min_base_increment) * min_base_increment
    min_quote_amount = min_base_amount * current_price

    min_step_size = max(min_spread, min_price_increment / current_price)

    max_possible_levels = int(total_amount / min_quote_amount) if min_quote_amount > 0 else 0

    if max_possible_levels == 0:
        return {
            "levels": [],
            "amount_per_level": 0,
            "num_levels": 0,
            "grid_range_pct": grid_range_pct,
            "warnings": [f"Need ${min_quote_amount:.2f} min, have ${total_amount:.2f}"],
            "valid": False,
        }

    max_levels_by_step = int(grid_range / min_step_size) if min_step_size > 0 else max_possible_levels
    n_levels = min(max_possible_levels, max_levels_by_step)

    if n_levels == 0:
        n_levels = 1
        quote_amount_per_level = min_quote_amount
    else:
        base_amount_per_level = max(
            min_base_amount,
            math.floor(total_amount / (current_price * n_levels) / min_base_increment)
            * min_base_increment,
        )
        quote_amount_per_level = base_amount_per_level * current_price
        n_levels = min(n_levels, int(total_amount / quote_amount_per_level))

    n_levels = max(1, n_levels)

    # Generate price levels (linear distribution)
    levels = []
    if n_levels > 1:
        for i in range(n_levels):
            price = low_price + (high_price - low_price) * i / (n_levels - 1)
            levels.append(round(price, 8))
        step = grid_range / (n_levels - 1)
    else:
        levels.append(round((low_price + high_price) / 2, 8))
        step = grid_range

    amount_per_level = total_amount / n_levels if n_levels > 0 else 0

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

    levels_below = [lv for lv in levels if lv < current_price]
    levels_above = [lv for lv in levels if lv >= current_price]

    return {
        "levels": levels,
        "levels_below_current": len(levels_below),
        "levels_above_current": len(levels_above),
        "amount_per_level": round(amount_per_level, 2),
        "num_levels": n_levels,
        "grid_range_pct": round(grid_range_pct, 3),
        "price_step": round(step * low_price, 8) if n_levels > 1 else 0,
        "spread_pct": round(step * 100, 3) if n_levels > 1 else round(min_spread * 100, 3),
        "max_levels_by_budget": max_possible_levels,
        "max_levels_by_spread": max_levels_by_step,
        "warnings": warnings,
        "valid": len(warnings) == 0,
    }


# ============================================
# MULTI-GRID GENERATION FUNCTIONS (NEW)
# ============================================

def calculate_optimal_multi_grids(
    current_price: float,
    natr: float,
    total_amount: float,
    min_order_amount: float,
    num_grids: int = 2,
    grid_type: str = "accumulation_distribution"
) -> List[Dict[str, Any]]:
    """
    Calculate optimal multi-grid configurations based on NATR.
    """
    if not natr or natr <= 0:
        natr = 0.02  # 2% default fallback

    total_range_pct = natr * 3
    grids = []

    if grid_type == "accumulation_distribution":
        # 🔧 FIX: Supporta num_grids > 2
        if num_grids == 2:
            # Comportamento originale: 1 buy + 1 sell
            buy_range_pct = total_range_pct * 0.6
            sell_range_pct = total_range_pct * 0.4

            buy_start = current_price * (1 - buy_range_pct)
            buy_end = current_price * (1 - buy_range_pct * 0.2)
            buy_limit = buy_start * 0.998
            grids.append({
                "grid_id": "accumulation",
                "start_price": round(buy_start, 6),
                "end_price": round(buy_end, 6),
                "limit_price": round(buy_limit, 6),
                "side": SIDE_LONG,
                "amount_quote_pct": 0.5,
                "enabled": True,
            })

            sell_start = current_price * (1 + sell_range_pct * 0.2)
            sell_end = current_price * (1 + sell_range_pct)
            sell_limit = sell_end * 1.002
            grids.append({
                "grid_id": "distribution",
                "start_price": round(sell_start, 6),
                "end_price": round(sell_end, 6),
                "limit_price": round(sell_limit, 6),
                "side": SIDE_SHORT,
                "amount_quote_pct": 0.5,
                "enabled": True,
            })
        else:
            # 🔧 NUOVO: Multiplo accumulation/distribution
            # Alterna LONG e SHORT per ogni grid
            for i in range(num_grids):
                is_long = (i % 2 == 0)
                if is_long:
                    # Accumulation (BUY) - range sotto il prezzo
                    range_pct = total_range_pct * (0.3 + (i / num_grids) * 0.3)
                    start = current_price * (1 - range_pct)
                    end = current_price * (1 - range_pct * 0.2)
                    limit = start * 0.998
                    side = SIDE_LONG
                else:
                    # Distribution (SELL) - range sopra il prezzo
                    range_pct = total_range_pct * (0.2 + (i / num_grids) * 0.3)
                    start = current_price * (1 + range_pct * 0.2)
                    end = current_price * (1 + range_pct)
                    limit = end * 1.002
                    side = SIDE_SHORT
                
                grids.append({
                    "grid_id": f"grid_{i+1}",
                    "start_price": round(start, 6),
                    "end_price": round(end, 6),
                    "limit_price": round(limit, 6),
                    "side": side,
                    "amount_quote_pct": round(1.0 / num_grids, 4),
                    "enabled": True,
                })

    elif grid_type == "range_trading":
        # OK - già funzionante
        range_low = current_price * (1 - total_range_pct)
        range_high = current_price * (1 + total_range_pct)
        step = (range_high - range_low) / num_grids
        amount_per_grid = 1.0 / num_grids

        for i in range(num_grids):
            start = range_low + (step * i)
            end = start + step
            side = SIDE_LONG if i % 2 == 0 else SIDE_SHORT

            if side == SIDE_LONG:
                limit = start * 0.998
            else:
                limit = end * 1.002

            grids.append({
                "grid_id": f"grid_{i+1}",
                "start_price": round(start, 6),
                "end_price": round(end, 6),
                "limit_price": round(limit, 6),
                "side": side,
                "amount_quote_pct": round(amount_per_grid, 4),
                "enabled": True,
            })

    elif grid_type == "pyramid":
        # 🔧 FIX: Genera dinamicamente in base a num_grids
        # Distribuzione esponenziale: più vicino al prezzo, più allocazione
        allocations = []
        levels = []
        
        for i in range(num_grids):
            # Distanza dal prezzo: più vicino per i primi grid
            distance = 0.01 * (i + 1)  # 1%, 2%, 3%, ...
            # Allocazione decrescente: più lontano = meno capitale
            weight = 1.0 / (i + 1)  # 1, 1/2, 1/3, 1/4, ...
            levels.append(distance)
            allocations.append(weight)
        
        # Normalizza allocazioni
        total_weight = sum(allocations)
        allocations = [w / total_weight for w in allocations]
        
        for i, (dist_pct, alloc) in enumerate(zip(levels, allocations)):
            price = current_price * (1 - dist_pct)
            start = price * 0.99
            end = price
            limit = price * 0.998

            grids.append({
                "grid_id": f"dca_{i+1}",
                "start_price": round(start, 6),
                "end_price": round(end, 6),
                "limit_price": round(limit, 6),
                "side": SIDE_LONG,
                "amount_quote_pct": round(alloc, 4),
                "enabled": True,
            })

    return grids

def suggest_multi_grid_params(
    current_price: float,
    natr: float,
    total_amount: float,
    min_order_amount: float,
    num_grids: int = 2,
    grid_type: str = "accumulation_distribution"
) -> Dict[str, Any]:
    """
    Suggest parameters for multiple grids with validation.
    """
    # 🔧 FIX: Usa num_grids (già passato correttamente)
    grids = calculate_optimal_multi_grids(
        current_price, natr, total_amount, min_order_amount,
        num_grids, grid_type  # ← num_grids è qui
    )

    # Validate sum of amount_quote_pct = 1.0
    total_pct = sum(g["amount_quote_pct"] for g in grids)
    if abs(total_pct - 1.0) > 0.01:
        for g in grids:
            g["amount_quote_pct"] = round(g["amount_quote_pct"] / total_pct, 4)

    return {
        "grids": grids,
        "num_grids": len(grids),
        "total_pct": sum(g["amount_quote_pct"] for g in grids),
    }

def format_multi_grid_summary(
    config: Dict[str, Any],
    current_price: float,
    natr: Optional[float] = None,
    trading_rules: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Format a human-readable summary of all grids.

    Example output:
        Grid accumulation (LONG, $500, 50%):
          15 levels (↓4 ↑11) @ $33.33/lvl | step: 0.583%
        Grid distribution (SHORT, $500, 50%):
          8 levels (↓5 ↑3) @ $37.50/lvl | step: 1.102%
        NATR (14): 1.45% | Total grids: 2 | Capital used: 100%

    Args:
        config: Full MultiGridStrike config
        current_price: Current market price
        natr: Optional pre-calculated NATR
        trading_rules: Optional trading rules

    Returns:
        Formatted summary string
    """
    grids = config.get("grids", [])
    total_amount = float(config.get("total_amount_quote", 0))
    min_spread = float(config.get("min_spread_between_orders", 0.001))
    min_order_amount = float(config.get("min_order_amount_quote", 5))

    lines = []
    total_pct = 0.0

    for grid in grids:
        if not grid.get("enabled", True):
            continue

        grid_id = grid.get("grid_id", "?")
        side = grid.get("side", SIDE_LONG)
        side_str = "LONG" if side == SIDE_LONG else "SHORT"
        pct = float(grid.get("amount_quote_pct", 0))
        allocated = total_amount * pct
        total_pct += pct

        start = float(grid.get("start_price", 0))
        end = float(grid.get("end_price", 0))

        analysis = generate_theoretical_grid(
            start_price=start,
            end_price=end,
            min_spread=min_spread,
            total_amount=allocated,
            min_order_amount=min_order_amount,
            current_price=current_price,
            side=side,
            trading_rules=trading_rules,
        )

        header = f"Grid {grid_id} ({side_str}, ${allocated:.0f}, {pct*100:.0f}%):"
        lines.append(header)

        if not analysis.get("valid"):
            for w in analysis.get("warnings", []):
                lines.append(f"  ⚠ {w}")
            continue

        n = analysis["num_levels"]
        below = analysis.get("levels_below_current", 0)
        above = analysis.get("levels_above_current", 0)
        amt = analysis["amount_per_level"]
        spread = analysis.get("spread_pct", 0)
        lines.append(
            f"  {n} levels (↓{below} ↑{above}) @ ${amt:.2f}/lvl | step: {spread:.3f}%"
        )

    if lines:
        footer_parts = []
        if natr is not None:
            footer_parts.append(f"NATR (14): {natr*100:.2f}%")
        footer_parts.append(f"Total grids: {len([g for g in grids if g.get('enabled')])}")
        footer_parts.append(f"Capital used: {total_pct*100:.0f}%")
        lines.append(" | ".join(footer_parts))

    return "\n".join(lines)

# ============================================
# Multi-grid specific: analyze all grids at once (BACKWARD COMPATIBILITY)
# ============================================

def analyze_all_grids(
    config: Dict[str, Any],
    current_price: float,
    trading_rules: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Run generate_theoretical_grid for every enabled grid in a MultiGridStrike config.

    This function is kept for backward compatibility with existing code.

    Args:
        config: Full MultiGridStrike config dict (with 'grids' list)
        current_price: Current market price
        trading_rules: Optional trading rules

    Returns:
        List of dicts, one per grid, each containing the grid_id and
        the result of generate_theoretical_grid for that grid.
    """
    results = []
    total_amount = float(config.get("total_amount_quote", 0))
    min_spread = float(config.get("min_spread_between_orders", 0.001))
    min_order_amount = float(config.get("min_order_amount_quote", 5))

    for grid in config.get("grids", []):
        if not grid.get("enabled", True):
            continue

        grid_id = grid.get("grid_id", "?")
        pct = float(grid.get("amount_quote_pct", 0))
        grid_amount = total_amount * pct

        analysis = generate_theoretical_grid(
            start_price=float(grid.get("start_price", 0)),
            end_price=float(grid.get("end_price", 0)),
            min_spread=min_spread,
            total_amount=grid_amount,
            min_order_amount=min_order_amount,
            current_price=current_price,
            side=int(grid.get("side", 1)),
            trading_rules=trading_rules,
        )
        analysis["grid_id"] = grid_id
        analysis["amount_allocated"] = round(grid_amount, 2)
        analysis["amount_pct"] = pct
        results.append(analysis)

    return results


