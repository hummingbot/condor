"""
MACD BB V1 analysis utilities.
Aggiornato con supporto Perpetual e suggerimenti strategie.
"""

import math
from typing import Any, Dict, List, Optional, Tuple


def _ema(values: List[float], period: int) -> List[float]:
    """Calculate EMA for a list of values."""
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def calculate_bbp_series(
    candles: List[Dict[str, Any]],
    bb_length: int = 100,
    bb_std: float = 2.0,
) -> List[float]:
    """Calculate Bollinger Band Percent (BBP) series."""
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
        bbp_values.append((closes[i] - lower) / band_width if band_width > 0 else 0.5)

    return bbp_values


def calculate_macd_series(
    candles: List[Dict[str, Any]],
    fast: int = 21,
    slow: int = 42,
    signal: int = 9,
) -> Tuple[List[float], List[float], List[float]]:
    """
    Calculate MACD, Signal line, and Histogram.
    """
    closes = [float(c.get("close") or c.get("c") or 0) for c in candles]
    if len(closes) < slow + signal:
        return [], [], []

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)

    offset = slow - fast
    ema_fast_aligned = ema_fast[offset:]
    macd_line = [f - s for f, s in zip(ema_fast_aligned, ema_slow)]

    signal_line = _ema(macd_line, signal)
    macd_aligned = macd_line[signal - 1:]
    histogram = [m - s for m, s in zip(macd_aligned, signal_line)]

    return macd_aligned, signal_line, histogram


def suggest_bb_thresholds(
    bbp_values: List[float],
    long_percentile: float = 15.0,
    short_percentile: float = 85.0,
) -> Tuple[float, float]:
    """Suggest bb_long_threshold and bb_short_threshold from BBP distribution."""
    if not bbp_values:
        return 0.0, 1.0

    sorted_bbp = sorted(bbp_values)
    n = len(sorted_bbp)
    long_idx = max(0, min(int(n * long_percentile / 100), n - 1))
    short_idx = max(0, min(int(n * short_percentile / 100), n - 1))

    return round(sorted_bbp[long_idx], 3), round(sorted_bbp[short_idx], 3)


def calculate_natr(candles: List[Dict[str, Any]], period: int = 14) -> Optional[float]:
    """Calculate Normalized ATR."""
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
    return atr / current_close if current_close > 0 else None


def analyze_candles_for_macd_bb(
    candles: List[Dict[str, Any]],
    bb_length: int = 100,
    bb_std: float = 2.0,
    macd_fast: int = 21,
    macd_slow: int = 42,
    macd_signal: int = 9,
    natr_period: int = 14,
) -> Dict[str, Any]:
    """
    Analisi completa per MACD BB V1.
    """
    result = {
        "bbp_current": None,
        "bb_upper": None,
        "bb_middle": None,
        "bb_lower": None,
        "natr": None,
        "macd_current": None,
        "macd_histogram_current": None,
        "suggested_long_threshold": 0.0,
        "suggested_short_threshold": 1.0,
        "signal_count_long": 0,
        "signal_count_short": 0,
        "pct_below_lower": 0.0,
        "pct_above_upper": 0.0,
        "analysis_candles": len(candles),
    }

    if not candles or len(candles) < max(bb_length, macd_slow + macd_signal) + natr_period:
        return result

    bbp_values = calculate_bbp_series(candles, bb_length, bb_std)
    if not bbp_values:
        return result

    result["bbp_current"] = round(bbp_values[-1], 3)
    n = len(bbp_values)
    result["pct_below_lower"] = round(sum(1 for v in bbp_values if v < 0) / n * 100, 1)
    result["pct_above_upper"] = round(sum(1 for v in bbp_values if v > 1) / n * 100, 1)

    closes = [float(c.get("close") or c.get("c") or 0) for c in candles]
    if len(closes) >= bb_length:
        window = closes[-bb_length:]
        sma = sum(window) / bb_length
        variance = sum((x - sma) ** 2 for x in window) / bb_length
        std = math.sqrt(variance)
        result["bb_upper"] = round(sma + bb_std * std, 6)
        result["bb_middle"] = round(sma, 6)
        result["bb_lower"] = round(sma - bb_std * std, 6)

    macd_line, signal_line, histogram = calculate_macd_series(candles, macd_fast, macd_slow, macd_signal)
    if macd_line: result["macd_current"] = round(macd_line[-1], 6)
    if histogram: result["macd_histogram_current"] = round(histogram[-1], 6)

    long_thr, short_thr = suggest_bb_thresholds(bbp_values)
    result["suggested_long_threshold"] = long_thr
    result["suggested_short_threshold"] = short_thr

    if histogram and bbp_values:
        h_len = len(histogram)
        b_aligned = bbp_values[-h_len:]
        m_aligned = macd_line[-h_len:]
        result["signal_count_long"] = sum(1 for b, m, h in zip(b_aligned, m_aligned, histogram) if b < long_thr and h > 0 and m < 0)
        result["signal_count_short"] = sum(1 for b, m, h in zip(b_aligned, m_aligned, histogram) if b > short_thr and h < 0 and m > 0)

    result["natr"] = calculate_natr(candles, natr_period)
    return result


def get_macd_bb_strategy_suggestions(natr: float, analysis: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Ritorna suggerimenti per MACD BB basati sulla volatilità e analisi statistica.
    I valori TP/SL vengono scalati in base alla volatilità (NATR).
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

    l_thr = analysis.get("suggested_long_threshold", 0.15)
    s_thr = analysis.get("suggested_short_threshold", 0.85)

    # TP/SL base scalati con volatilità
    base_tp = 0.025
    base_sl = 0.015
    
    # ========== AGGIUNGI TRAILING STOP ==========
    # Trailing stop base (activation, delta)
    base_ts_activation = 0.015  # 1.5%
    base_ts_delta = 0.005       # 0.5%

    return {
        "scalping": {
            "label": "Target: Scalping (Reattivo)",
            "bb_length": 21,
            "bb_std": 2.0,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "bb_long_threshold": 0.2,
            "bb_short_threshold": 0.8,
            "take_profit": round(base_tp * vol_mult * 0.6, 4),
            "stop_loss": round(base_sl * vol_mult * 0.7, 4),
            "trailing_stop_activation": round(base_ts_activation * vol_mult * 0.8, 4),
            "trailing_stop_delta": round(base_ts_delta * vol_mult * 0.8, 4),
            "volatility_regime": vol_regime
        },
        "swing": {
            "label": "Target: Swing (Filtro stretto)",
            "bb_length": 100,
            "bb_std": 2.5,
            "macd_fast": 21,
            "macd_slow": 42,
            "macd_signal": 9,
            "bb_long_threshold": l_thr,
            "bb_short_threshold": s_thr,
            "take_profit": round(base_tp * vol_mult * 1.6, 4),
            "stop_loss": round(base_sl * vol_mult * 1.3, 4),
            "trailing_stop_activation": round(base_ts_activation * vol_mult * 1.2, 4),
            "trailing_stop_delta": round(base_ts_delta * vol_mult * 1.2, 4),
            "volatility_regime": vol_regime
        },
        "auto": {
            "label": "Target: Auto (Analisi Live)",
            "bb_length": 50,
            "bb_std": 2.0,
            "macd_fast": 21,
            "macd_slow": 42,
            "macd_signal": 9,
            "bb_long_threshold": l_thr,
            "bb_short_threshold": s_thr,
            "take_profit": round(base_tp * vol_mult, 4),
            "stop_loss": round(base_sl * vol_mult, 4),
            "trailing_stop_activation": round(base_ts_activation * vol_mult, 4),
            "trailing_stop_delta": round(base_ts_delta * vol_mult, 4),
            "volatility_regime": vol_regime
        }
    }

def format_macd_bb_analysis(analysis: Dict[str, Any]) -> str:
    """Format analysis results for display in wizard final step."""
    lines = []
    n_candles = analysis.get("analysis_candles", 0)
    natr = analysis.get("natr")
    bbp = analysis.get("bbp_current")
    bb_upper = analysis.get("bb_upper")
    bb_lower = analysis.get("bb_lower")
    macd = analysis.get("macd_current")
    hist = analysis.get("macd_histogram_current")
    l_sig = analysis.get("signal_count_long", 0)
    s_sig = analysis.get("signal_count_short", 0)
    pct_below = analysis.get("pct_below_lower", 0)
    pct_above = analysis.get("pct_above_upper", 0)

    lines.append(f"BB+MACD analysis ({n_candles} candles):")
    if bb_upper and bb_lower:
        lines.append(f"  Range: {bb_lower:.4f} - {bb_upper:.4f}")
    if bbp is not None:
        # Determina posizione BBP
        if bbp < 0.2:
            pos = "OVERSOLD"
        elif bbp > 0.8:
            pos = "OVERBOUGHT"
        else:
            pos = "neutral"
        lines.append(f"  BBP now: {bbp:.3f} ({pos})")
    if macd is not None and hist is not None:
        # Determina segnale MACD
        if hist > 0 and macd < 0:
            macd_signal = "BULLISH"
        elif hist < 0 and macd > 0:
            macd_signal = "BEARISH"
        else:
            macd_signal = "neutral"
        lines.append(f"  MACD: {macd:.6f} | Hist: {hist:.6f} ({macd_signal})")
    if natr:
        # Valutazione volatilità
        if natr < 0.005:
            vol_assessment = "Very Low (<0.5%) → use tighter stops"
        elif natr < 0.01:
            vol_assessment = "Low (0.5-1%) → standard stops"
        elif natr < 0.02:
            vol_assessment = "Moderate (1-2%) → adjust stops"
        elif natr < 0.03:
            vol_assessment = "High (2-3%) → wider stops"
        else:
            vol_assessment = "Very High (>3%) → use wider stops"
        lines.append(f"  NATR(14): {natr*100:.3f}% ({vol_assessment})")

    lines.append(f"  % below lower band: {pct_below:.1f}%")
    lines.append(f"  % above upper band: {pct_above:.1f}%")
    lines.append(f"  Combined signals: LONG={l_sig} SHORT={s_sig}")
    lines.append(f"  → bb_long_threshold: {analysis['suggested_long_threshold']}")
    lines.append(f"  → bb_short_threshold: {analysis['suggested_short_threshold']}")
    # Aggiungi raccomandazione basata sui segnali
    if l_sig > s_sig and l_sig > 0:
        lines.append(f"  → Bias: LONG ({(l_sig/(l_sig+s_sig)*100):.0f}% signals)")
    elif s_sig > l_sig and s_sig > 0:
        lines.append(f"  → Bias: SHORT ({(s_sig/(l_sig+s_sig)*100):.0f}% signals)")
    elif l_sig == 0 and s_sig == 0:
        lines.append("  → Bias: NEUTRAL (no signals detected)")

    return "\n".join(lines)
