"""
SuperTrend V1 analysis utilities.

Calculates SuperTrend indicator manually (pure Python, no pandas_ta):
- SuperTrend line and direction
- Suggested parameters based on candle data
- Signal statistics
"""

import math
from typing import Any, Dict, List, Optional, Tuple


def calculate_atr(candles: List[Dict[str, Any]], period: int = 14) -> List[float]:
    """Calculate ATR series."""
    if len(candles) < period + 1:
        return []

    true_ranges = []
    for i in range(1, len(candles)):
        high = float(candles[i].get("high", 0) or 0)
        low = float(candles[i].get("low", 0) or 0)
        prev_close = float(candles[i - 1].get("close", 0) or 0)
        if not all([high, low, prev_close]):
            true_ranges.append(0.0)
            continue
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return []

    # Wilder's smoothing (RMA)
    atr_values = [sum(true_ranges[:period]) / period]
    for tr in true_ranges[period:]:
        atr_values.append((atr_values[-1] * (period - 1) + tr) / period)

    return atr_values


def calculate_supertrend(
    candles: List[Dict[str, Any]],
    length: int = 20,
    multiplier: float = 4.0,
) -> List[Dict[str, Any]]:
    """
    Calculate SuperTrend indicator.

    Returns list of dicts with:
    - supertrend: SuperTrend line value
    - direction: 1 = uptrend (bullish), -1 = downtrend (bearish)
    - close: candle close price
    - percentage_distance: distance from close to ST line as %
    """
    if len(candles) < length + 1:
        return []

    atr_values = calculate_atr(candles, length)
    if not atr_values:
        return []

    # Align ATR with candles (ATR starts at index `length`)
    atr_start = length

    results = []
    prev_upper = None
    prev_lower = None
    prev_direction = 1

    for i in range(len(atr_values)):
        candle_idx = i + atr_start
        if candle_idx >= len(candles):
            break

        high = float(candles[candle_idx].get("high", 0) or 0)
        low = float(candles[candle_idx].get("low", 0) or 0)
        close = float(candles[candle_idx].get("close", 0) or 0)
        atr = atr_values[i]

        hl2 = (high + low) / 2
        basic_upper = hl2 + multiplier * atr
        basic_lower = hl2 - multiplier * atr

        # Adjust bands
        if prev_upper is None:
            upper = basic_upper
            lower = basic_lower
        else:
            upper = basic_upper if basic_upper < prev_upper or float(candles[candle_idx - 1].get("close", 0)) > prev_upper else prev_upper
            lower = basic_lower if basic_lower > prev_lower or float(candles[candle_idx - 1].get("close", 0)) < prev_lower else prev_lower

        # Determine direction
        if prev_direction == -1:
            direction = 1 if close > upper else -1
        else:
            direction = -1 if close < lower else 1

        supertrend = lower if direction == 1 else upper
        pct_distance = abs(close - supertrend) / close if close > 0 else 0

        results.append({
            "supertrend": supertrend,
            "direction": direction,
            "close": close,
            "percentage_distance": pct_distance,
            "upper": upper,
            "lower": lower,
        })

        prev_upper = upper
        prev_lower = lower
        prev_direction = direction

    return results


def calculate_natr(candles: List[Dict[str, Any]], period: int = 14) -> Optional[float]:
    """Calculate Normalized ATR."""
    if not candles or len(candles) < period + 1:
        return None
    atr_values = calculate_atr(candles, period)
    if not atr_values:
        return None
    current_close = float(candles[-1].get("close", 0) or 0)
    return atr_values[-1] / current_close if current_close > 0 else None


def suggest_percentage_threshold(
    st_results: List[Dict[str, Any]],
    signal_percentile: float = 75.0,
) -> float:
    """
    Suggest percentage_threshold based on historical distance distribution.

    Logic: use the Nth percentile of historical distances — captures most
    signal opportunities while filtering out very distant entries.
    """
    if not st_results:
        return 0.01

    distances = [r["percentage_distance"] for r in st_results if r["percentage_distance"] > 0]
    if not distances:
        return 0.01

    sorted_d = sorted(distances)
    idx = max(0, min(int(len(sorted_d) * signal_percentile / 100), len(sorted_d) - 1))
    return round(sorted_d[idx], 4)


def analyze_candles_for_supertrend(
    candles: List[Dict[str, Any]],
    length: int = 20,
    multiplier: float = 4.0,
    percentage_threshold: float = 0.01,
    natr_period: int = 14,
) -> Dict[str, Any]:
    """
    Full analysis of candle data for SuperTrend V1.

    Returns:
    - current_direction: 1 (up) or -1 (down)
    - current_supertrend: current ST line value
    - current_pct_distance: current distance from price to ST line
    - signal_now: True if current candle would trigger a signal
    - natr: Normalized ATR
    - suggested_percentage_threshold: auto-suggested threshold
    - signal_count_long/short: historical signal counts
    - trend_changes: number of direction flips
    - pct_time_long/short: % of time in each trend
    - analysis_candles: candle count used
    """
    result = {
        "current_direction": 0,
        "current_supertrend": None,
        "current_pct_distance": None,
        "signal_now": False,
        "natr": None,
        "suggested_percentage_threshold": percentage_threshold,
        "signal_count_long": 0,
        "signal_count_short": 0,
        "trend_changes": 0,
        "pct_time_long": 0.0,
        "pct_time_short": 0.0,
        "analysis_candles": len(candles),
    }

    if not candles or len(candles) < length + natr_period + 1:
        return result

    st_results = calculate_supertrend(candles, length, multiplier)
    if not st_results:
        return result

    # Current state
    current = st_results[-1]
    result["current_direction"] = current["direction"]
    result["current_supertrend"] = round(current["supertrend"], 6)
    result["current_pct_distance"] = round(current["percentage_distance"] * 100, 3)
    result["signal_now"] = current["percentage_distance"] < percentage_threshold

    # Historical stats
    long_count = sum(1 for r in st_results if r["direction"] == 1 and r["percentage_distance"] < percentage_threshold)
    short_count = sum(1 for r in st_results if r["direction"] == -1 and r["percentage_distance"] < percentage_threshold)
    result["signal_count_long"] = long_count
    result["signal_count_short"] = short_count

    # Trend changes
    changes = sum(1 for i in range(1, len(st_results)) if st_results[i]["direction"] != st_results[i-1]["direction"])
    result["trend_changes"] = changes

    # % time in each trend
    n = len(st_results)
    long_time = sum(1 for r in st_results if r["direction"] == 1)
    result["pct_time_long"] = round(long_time / n * 100, 1)
    result["pct_time_short"] = round((n - long_time) / n * 100, 1)

    # Suggested threshold
    result["suggested_percentage_threshold"] = suggest_percentage_threshold(st_results)

    # NATR
    result["natr"] = calculate_natr(candles, natr_period)

    return result

def get_st_strategy_suggestions(natr: float, analysis: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Ritorna suggerimenti per SuperTrend V1 basati sulla volatilità.
    I valori TP/SL e threshold vengono scalati in base alla volatilità (NATR).
    """
    if not natr or natr <= 0:
        natr = 0.01  # Default 1% volatility

    # Determina il regime di volatilità
    if natr < 0.005:      # <0.5%
        vol_regime = "very_low"
        vol_mult = 0.7
    elif natr < 0.01:     # 0.5-1%
        vol_regime = "low"
        vol_mult = 1.0
    elif natr < 0.02:     # 1-2%
        vol_regime = "moderate"
        vol_mult = 1.3
    elif natr < 0.03:     # 2-3%
        vol_regime = "high"
        vol_mult = 1.6
    else:                 # >3%
        vol_regime = "very_high"
        vol_mult = 2.0

    # Valori base
    base_length = 20
    base_multiplier = 4.0
    base_threshold = 0.01
    base_tp = 0.03
    base_sl = 0.05
    base_ts_activation = 0.015
    base_ts_delta = 0.005

    return {
        "scalping": {
            "label": "Target: Scalping (Reattivo)",
            "length": 10,                           # ATR più corto → più reattivo
            "multiplier": 3.0,                      # Bande più strette
            "percentage_threshold": round(base_threshold * 0.8, 4),  # Soglia più stretta
            "take_profit": round(base_tp * vol_mult * 0.6, 4),
            "stop_loss": round(base_sl * vol_mult * 0.7, 4),
            "trailing_stop_activation": round(base_ts_activation * vol_mult * 0.8, 4),
            "trailing_stop_delta": round(base_ts_delta * vol_mult * 0.8, 4),
            "volatility_regime": vol_regime
        },
        "swing": {
            "label": "Target: Swing (Filtro stretto)",
            "length": 30,                           # ATR più lungo → più stabile
            "multiplier": 5.0,                      # Bande più larghe
            "percentage_threshold": round(base_threshold * 1.5, 4),  # Soglia più larga
            "take_profit": round(base_tp * vol_mult * 1.6, 4),
            "stop_loss": round(base_sl * vol_mult * 1.3, 4),
            "trailing_stop_activation": round(base_ts_activation * vol_mult * 1.2, 4),
            "trailing_stop_delta": round(base_ts_delta * vol_mult * 1.2, 4),
            "volatility_regime": vol_regime
        },
        "auto": {
            "label": "Target: Auto (Analisi Live)",
            "length": base_length,
            "multiplier": base_multiplier,
            "percentage_threshold": analysis.get("suggested_percentage_threshold", base_threshold),
            "take_profit": round(base_tp * vol_mult, 4),
            "stop_loss": round(base_sl * vol_mult, 4),
            "trailing_stop_activation": round(base_ts_activation * vol_mult, 4),
            "trailing_stop_delta": round(base_ts_delta * vol_mult, 4),
            "volatility_regime": vol_regime
        }
    }

def format_supertrend_analysis(analysis: Dict[str, Any]) -> str:
    """Format analysis results for display in wizard final step."""
    lines = []
    n_candles = analysis.get("analysis_candles", 0)
    direction = analysis.get("current_direction", 0)
    st_val = analysis.get("current_supertrend")
    pct_dist = analysis.get("current_pct_distance")
    signal_now = analysis.get("signal_now", False)
    natr = analysis.get("natr")
    long_signals = analysis.get("signal_count_long", 0)
    short_signals = analysis.get("signal_count_short", 0)
    trend_changes = analysis.get("trend_changes", 0)
    pct_long = analysis.get("pct_time_long", 0)
    pct_short = analysis.get("pct_time_short", 0)
    suggested_thr = analysis.get("suggested_percentage_threshold", 0.01)

    dir_str = "📈 UP (Bullish)" if direction == 1 else ("📉 DOWN (Bearish)" if direction == -1 else "—")

    lines.append(f"SuperTrend analysis ({n_candles} candles):")
    lines.append(f"  Direction now: {dir_str}")
    if st_val is not None:
        lines.append(f"  ST line: {st_val:.6g}")
    if pct_dist is not None:
        lines.append(f"  Distance: {pct_dist:.3f}%  {'✅ signal active' if signal_now else '⚠️ no signal (too far)'}")
    if natr:
        lines.append(f"  NATR(14): {natr*100:.3f}%")
    lines.append(f"  % time bullish: {pct_long:.1f}% | bearish: {pct_short:.1f}%")
    lines.append(f"  Trend changes: {trend_changes}")
    lines.append(f"  Signals (history): LONG={long_signals} SHORT={short_signals}")
    lines.append("")
    lines.append(f"  → suggested percentage_threshold: {suggested_thr}")

    return "\n".join(lines)
