"""
DMan V3 analysis utilities.

Calculates suggested parameters from candle data:
- bb_long_threshold  → based on historical BBP distribution
- bb_short_threshold → based on historical BBP distribution
- dca_spreads        → based on NATR (volatility-scaled)
"""

from typing import Any, Dict, List, Optional, Tuple


def calculate_bbp_series(
    candles: List[Dict[str, Any]],
    bb_length: int = 20,
    bb_std: float = 2.0,
) -> List[float]:
    """
    Calculate Bollinger Band Percent (BBP) for each candle.

    BBP = (close - lower) / (upper - lower)
    - BBP = 0.0  → price at lower band
    - BBP = 0.5  → price at middle (SMA)
    - BBP = 1.0  → price at upper band
    - BBP < 0    → price below lower band (oversold)
    - BBP > 1    → price above upper band (overbought)

    Args:
        candles: List of candle dicts with 'close' key
        bb_length: Bollinger Bands period
        bb_std: Standard deviations

    Returns:
        List of BBP values (same length as candles, None for initial period)
    """
    import math

    closes = []
    for c in candles:
        close = c.get("close") or c.get("c")
        if close is not None:
            closes.append(float(close))

    if len(closes) < bb_length:
        return []

    bbp_values = []
    for i in range(len(closes)):
        if i < bb_length - 1:
            continue
        window = closes[i - bb_length + 1: i + 1]
        sma = sum(window) / bb_length
        variance = sum((x - sma) ** 2 for x in window) / bb_length
        std = math.sqrt(variance)
        upper = sma + bb_std * std
        lower = sma - bb_std * std
        band_width = upper - lower
        if band_width > 0:
            bbp = (closes[i] - lower) / band_width
        else:
            bbp = 0.5
        bbp_values.append(bbp)

    return bbp_values


def calculate_natr(candles: List[Dict[str, Any]], period: int = 14) -> Optional[float]:
    """Calculate Normalized ATR from candles."""
    if not candles or len(candles) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(candles)):
        high = float(candles[i].get("high", 0) or 0)
        low = float(candles[i].get("low", 0) or 0)
        prev_close = float(candles[i - 1].get("close", 0) or 0)
        if not all([high, low, prev_close]):
            continue
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    atr = sum(true_ranges[-period:]) / period
    current_close = float(candles[-1].get("close", 0) or 0)
    if current_close <= 0:
        return None

    return atr / current_close


def suggest_dca_spreads(natr: float, num_levels: int = 4) -> List[float]:
    """
    Suggest DCA spread levels based on NATR.

    Logic:
    - Level 1 (closest): ~0.5x NATR
    - Level 2: ~2x NATR
    - Level 3: ~5x NATR
    - Level 4 (deepest): ~10x NATR

    These multipliers ensure meaningful distance between orders
    proportional to the market's typical daily range.

    Args:
        natr: Normalized ATR as decimal (e.g. 0.005 = 0.5%)
        num_levels: Number of DCA levels (2-4)

    Returns:
        List of spread values sorted ascending
    """
    multipliers = [0.5, 2.0, 5.0, 10.0][:num_levels]
    spreads = [round(natr * m, 4) for m in multipliers]
    # Ensure minimum reasonable spreads
    spreads = [max(s, 0.0005) for s in spreads]
    return spreads


def suggest_bb_thresholds(
    bbp_values: List[float],
    long_percentile: float = 15.0,
    short_percentile: float = 85.0,
) -> Tuple[float, float]:
    """
    Suggest bb_long_threshold and bb_short_threshold based on
    historical BBP distribution.

    Logic:
    - bb_long_threshold: BBP value at the Nth percentile from below
      → "enter LONG when price is in the bottom X% of its historical range"
    - bb_short_threshold: BBP value at the Nth percentile from above
      → "enter SHORT when price is in the top X% of its historical range"

    Default: bottom 15% / top 15% of observations
    → conservative entry, avoids chasing

    Args:
        bbp_values: Historical BBP series
        long_percentile: Enter LONG below this percentile (default 15%)
        short_percentile: Enter SHORT above this percentile (default 85%)

    Returns:
        Tuple of (bb_long_threshold, bb_short_threshold)
    """
    if not bbp_values:
        return 0.0, 1.0

    sorted_bbp = sorted(bbp_values)
    n = len(sorted_bbp)

    long_idx = int(n * long_percentile / 100)
    short_idx = int(n * short_percentile / 100)

    long_idx = max(0, min(long_idx, n - 1))
    short_idx = max(0, min(short_idx, n - 1))

    long_threshold = round(sorted_bbp[long_idx], 3)
    short_threshold = round(sorted_bbp[short_idx], 3)

    return long_threshold, short_threshold


def analyze_candles_for_dman(
    candles: List[Dict[str, Any]],
    bb_length: int = 20,
    bb_std: float = 2.0,
    natr_period: int = 14,
    dca_levels: int = 4,
) -> Dict[str, Any]:
    """
    Full analysis of candle data for DMan V3 parameter suggestion.

    Returns a dict with:
    - bbp_current: Current BBP value
    - bb_upper/middle/lower: Current BB levels
    - natr: Normalized ATR
    - suggested_long_threshold: Suggested bb_long_threshold
    - suggested_short_threshold: Suggested bb_short_threshold
    - suggested_dca_spreads: Suggested DCA spreads list
    - pct_below_lower: % of time price was below lower band
    - pct_above_upper: % of time price was above upper band
    - analysis_candles: Number of candles used
    """
    import math

    result = {
        "bbp_current": None,
        "bb_upper": None,
        "bb_middle": None,
        "bb_lower": None,
        "natr": None,
        "suggested_long_threshold": 0.0,
        "suggested_short_threshold": 1.0,
        "suggested_dca_spreads": [0.001, 0.018, 0.15, 0.25],
        "pct_below_lower": 0.0,
        "pct_above_upper": 0.0,
        "analysis_candles": len(candles),
    }

    if not candles or len(candles) < bb_length + natr_period:
        return result

    # Calculate BBP series
    bbp_values = calculate_bbp_series(candles, bb_length, bb_std)
    if not bbp_values:
        return result

    result["bbp_current"] = round(bbp_values[-1], 3)
    result["analysis_candles"] = len(candles)

    # % time outside bands
    below = sum(1 for v in bbp_values if v < 0)
    above = sum(1 for v in bbp_values if v > 1)
    n = len(bbp_values)
    result["pct_below_lower"] = round(below / n * 100, 1)
    result["pct_above_upper"] = round(above / n * 100, 1)

    # Current BB levels
    closes = [float(c.get("close") or c.get("c") or 0) for c in candles]
    if len(closes) >= bb_length:
        window = closes[-bb_length:]
        sma = sum(window) / bb_length
        variance = sum((x - sma) ** 2 for x in window) / bb_length
        std = math.sqrt(variance)
        result["bb_upper"] = round(sma + bb_std * std, 6)
        result["bb_middle"] = round(sma, 6)
        result["bb_lower"] = round(sma - bb_std * std, 6)

    # NATR
    natr = calculate_natr(candles, natr_period)
    result["natr"] = natr

    # Suggested thresholds from BBP distribution
    long_thr, short_thr = suggest_bb_thresholds(bbp_values)
    result["suggested_long_threshold"] = long_thr
    result["suggested_short_threshold"] = short_thr

    # Suggested DCA spreads from NATR
    if natr and natr > 0:
        result["suggested_dca_spreads"] = suggest_dca_spreads(natr, dca_levels)

    return result


def format_dman_analysis(analysis: Dict[str, Any]) -> str:
    """
    Format analysis results for display in wizard final step.

    Returns a string block to append to the config text.
    """
    lines = []
    natr = analysis.get("natr")
    bbp = analysis.get("bbp_current")
    bb_upper = analysis.get("bb_upper")
    bb_middle = analysis.get("bb_middle")
    bb_lower = analysis.get("bb_lower")
    pct_below = analysis.get("pct_below_lower", 0)
    pct_above = analysis.get("pct_above_upper", 0)
    n_candles = analysis.get("analysis_candles", 0)

    lines.append(f"BB analysis ({n_candles} candles):")
    if bb_upper and bb_middle and bb_lower:
        lines.append(f"  Upper: {bb_upper:.4f} | Mid: {bb_middle:.4f} | Lower: {bb_lower:.4f}")
    if bbp is not None:
        pos = "oversold" if bbp < 0.2 else ("overbought" if bbp > 0.8 else "neutral")
        lines.append(f"  BBP now: {bbp:.3f} ({pos})")
    if natr:
        lines.append(f"  NATR(14): {natr*100:.3f}%")
    lines.append(f"  % below lower band: {pct_below:.1f}%")
    lines.append(f"  % above upper band: {pct_above:.1f}%")

    lines.append("")
    lines.append(f"  → bb_long_threshold: {analysis['suggested_long_threshold']}")
    lines.append(f"  → bb_short_threshold: {analysis['suggested_short_threshold']}")
    spreads = analysis.get("suggested_dca_spreads", [])
    if spreads:
        lines.append(f"  → dca_spreads: {','.join(str(s) for s in spreads)}")

    return "\n".join(lines)

def get_dca_strategy_suggestions(natr: float) -> Dict[str, Dict[str, Any]]:
    if not natr:
        natr = 0.01

    # AUTO: Usa la logica suggerita dall'analisi delle candele
    auto_spreads = suggest_dca_spreads(natr, 4)

    return {
        "scalping": {
            "label": "Target: Scalping (Ordini vicini e costanti)",
            "dca_spreads": [round(natr*0.2, 4), round(natr*0.4, 4), round(natr*0.6, 4), round(natr*0.9, 4)],
            "dca_amounts_pct": [0.25, 0.25, 0.25, 0.25] # Distribuzione piatta
        },
        "martingale": {
            "label": "Target: Martingala (Raddoppio)",
            "dca_spreads": [round(natr*0.5, 4), round(natr*1.2, 4), round(natr*2.5, 4), round(natr*5.0, 4)],
            "dca_amounts_pct": [0.10, 0.20, 0.30, 0.40] # Più capitale sui livelli profondi
        },
        "standard": {
            "label": "Target: Standard (Bilanciato)",
            "dca_spreads": [round(natr*1.0, 4), round(natr*2.0, 4), round(natr*3.0, 4), round(natr*4.0, 4)],
            "dca_amounts_pct": [0.20, 0.20, 0.30, 0.30]
        },
        "conservative": {
            "label": "Target: Conservativo (Protezione)",
            "dca_spreads": [round(natr*2.0, 4), round(natr*5.0, 4), round(natr*10.0, 4), round(natr*15.0, 4)],
            "dca_amounts_pct": [0.40, 0.30, 0.20, 0.10] # Più capitale vicino, meno se crolla tutto
        },
        "auto": {
            "label": "Target: Auto (Analisi NATR)",
            "dca_spreads": auto_spreads,
            "dca_amounts_pct": [0.25, 0.25, 0.25, 0.25]
        }
    }
