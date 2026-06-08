"""
Stat Arb V2 analysis utilities.

Pure-Python implementation of cointegration analysis
(no sklearn/statsmodels dependency — usable directly from Condor/UI layer):

- Linear regression (beta, alpha, R²)
- ADF test approximation (stationarity)
- Half-life of mean reversion via OU process
- Parameter suggestions
"""

import math
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# LOW-LEVEL CALCULATIONS
# ---------------------------------------------------------------------------

def linear_regression(x: List[float], y: List[float]) -> Tuple[float, float, float]:
    """
    Calculate linear regression using pure Python (no numpy/sklearn).

    Returns:
        (slope, intercept, r_squared)
    """
    n = len(x)
    if n == 0:
        return 0.0, 0.0, 0.0

    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    sum_x2 = sum(xi * xi for xi in x)
    sum_y2 = sum(yi * yi for yi in y)

    denominator = n * sum_x2 - sum_x * sum_x
    if denominator == 0:
        return 0.0, sum_y / n, 0.0

    slope = (n * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n

    # R² calculation
    y_mean = sum_y / n
    ss_res = sum((yi - (intercept + slope * xi)) ** 2 for xi, yi in zip(x, y))
    ss_tot = sum((yi - y_mean) ** 2 for yi in y)

    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return slope, intercept, r_squared


def calculate_cumulative_returns(prices: List[float]) -> List[float]:
    """Calculate cumulative returns normalized to start at 1.0."""
    if len(prices) < 2:
        return [1.0]

    returns = [prices[i] / prices[i - 1] - 1 for i in range(1, len(prices))]
    cum_returns = [1.0]
    for r in returns:
        cum_returns.append(cum_returns[-1] * (1 + r))
    return cum_returns


def calculate_adf_approximation(series: List[float]) -> float:
    """
    Approximate ADF test p-value using pure Python.
    
    Returns a p-value (lower = more stationary).
    This is a heuristic approximation, not a full ADF implementation.
    """
    n = len(series)
    if n < 10:
        return 0.5

    # Calculate lagged series
    y = [series[i] - series[i - 1] for i in range(1, n)]
    x = series[:-1]

    if len(x) < 3:
        return 0.5

    # Regression y ~ x
    n_xy = len(x)
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    sum_x2 = sum(xi * xi for xi in x)

    denominator = n_xy * sum_x2 - sum_x * sum_x
    if denominator == 0:
        return 0.5

    gamma = (n_xy * sum_xy - sum_x * sum_y) / denominator

    # Critical approximation: gamma < 0 indicates mean reversion
    if gamma < -0.05:
        return 0.01   # Very stationary
    elif gamma < -0.02:
        return 0.05   # Stationary
    elif gamma < -0.01:
        return 0.10   # Moderately stationary
    elif gamma < 0:
        return 0.20   # Weakly stationary
    else:
        return 0.50   # Non-stationary


def calculate_half_life_ou(spread: List[float]) -> Optional[float]:
    """
    Calculate half-life of mean reversion using OU process approximation.
    """
    if len(spread) < 3:
        return None

    spread_lag = spread[:-1]
    delta_spread = [spread[i] - spread[i - 1] for i in range(1, len(spread))]

    n = len(spread_lag)
    if n < 2:
        return None

    # Linear regression: delta = lambda * lag + epsilon
    sum_lag = sum(spread_lag)
    sum_delta = sum(delta_spread)
    sum_lag_delta = sum(l * d for l, d in zip(spread_lag, delta_spread))
    sum_lag2 = sum(l * l for l in spread_lag)

    denominator = n * sum_lag2 - sum_lag * sum_lag
    if denominator == 0:
        return None

    lambda_ou = (n * sum_lag_delta - sum_lag * sum_delta) / denominator

    if lambda_ou < 0:
        return -math.log(2) / lambda_ou
    return None


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


# ---------------------------------------------------------------------------
# COINTEGRATION ANALYSIS
# ---------------------------------------------------------------------------

def analyze_cointegration(
    dominant_prices: List[float],
    hedge_prices: List[float],
) -> Dict[str, Any]:
    """
    Calculate cointegration metrics between two price series.

    Returns dict with:
    - beta: hedge vs dominant slope
    - r_squared: regression fit quality (0-1)
    - adf_pvalue: stationarity p-value (lower = more stationary)
    - half_life: mean reversion half-life in candles
    - spread_std: standard deviation of spread (%)
    """
    if len(dominant_prices) < 10 or len(hedge_prices) < 10:
        return {"error": "Insufficient price data"}

    # Use minimum length for alignment
    n = min(len(dominant_prices), len(hedge_prices))
    dom_prices = dominant_prices[-n:]
    hedge_prices = hedge_prices[-n:]

    # Calculate cumulative returns
    dom_cum = calculate_cumulative_returns(dom_prices)
    hedge_cum = calculate_cumulative_returns(hedge_prices)

    # Linear regression
    slope, intercept, r_squared = linear_regression(dom_cum, hedge_cum)

    # Spread as % deviation from predicted value
    y_pred = [intercept + slope * x for x in dom_cum]
    spread_pct = [(hedge_cum[i] - y_pred[i]) / y_pred[i] * 100 for i in range(len(dom_cum))]

    # Remove NaN/inf values
    spread_pct = [s for s in spread_pct if math.isfinite(s)]

    if len(spread_pct) < 5:
        return {"error": "Invalid spread calculation"}

    # Calculate metrics
    spread_std = math.sqrt(sum((s - sum(spread_pct) / len(spread_pct)) ** 2 for s in spread_pct) / len(spread_pct))
    half_life = calculate_half_life_ou(spread_pct)
    adf_pvalue = calculate_adf_approximation(spread_pct)

    return {
        "beta": round(slope, 4),
        "r_squared": round(r_squared, 4),
        "adf_pvalue": round(adf_pvalue, 4),
        "half_life": round(half_life, 1) if half_life else None,
        "spread_std_pct": round(spread_std, 4),
    }


# ---------------------------------------------------------------------------
# PARAMETER SUGGESTIONS
# ---------------------------------------------------------------------------

def suggest_entry_threshold(spread_std_pct: Optional[float]) -> float:
    """Suggest entry_threshold based on spread volatility."""
    if spread_std_pct is not None:
        # Higher volatility = higher threshold
        if spread_std_pct > 1.0:
            return 2.5
        elif spread_std_pct > 0.5:
            return 2.0
        else:
            return 1.5
    return 2.0


def suggest_take_profit(spread_std_pct: Optional[float]) -> float:
    """Suggest take_profit based on spread volatility."""
    if spread_std_pct is not None:
        # Take profit = 30% of typical spread movement
        tp = spread_std_pct / 100 * 0.3
        return max(0.0003, round(tp, 6))
    return 0.0008


def suggest_hedge_ratio_range(beta: float) -> Tuple[float, float]:
    """Suggest dynamic_hedge_ratio range based on beta."""
    if beta > 0:
        central = 1.0 / beta
        suggested_min = max(0.2, central * 0.5)
        suggested_max = min(3.0, central * 2.0)
    else:
        suggested_min, suggested_max = 0.5, 2.0
    return suggested_min, suggested_max


# ---------------------------------------------------------------------------
# FULL ANALYSIS (for Condor wizard)
# ---------------------------------------------------------------------------

def analyze_candles_for_stat_arb(
    dominant_candles: List[Dict[str, Any]],
    hedge_candles: List[Dict[str, Any]],
    lookback: int = 300,
) -> Dict[str, Any]:
    """
    Full analysis for StatArb parameter suggestions.

    Returns dict with:
    - beta, r_squared, adf_pvalue, half_life, spread_std_pct
    - suggested_entry_threshold, suggested_take_profit
    - suggested_hedge_ratio_range (min, max)
    - natr_dominant, natr_hedge
    - warnings
    """
    result = {
        "beta": None,
        "r_squared": None,
        "adf_pvalue": None,
        "half_life": None,
        "spread_std_pct": None,
        "suggested_entry_threshold": 2.0,
        "suggested_take_profit": 0.0008,
        "suggested_hedge_ratio_range": [0.5, 2.0],
        "natr_dominant": None,
        "natr_hedge": None,
        "warnings": [],
        "analysis_candles": min(len(dominant_candles), len(hedge_candles)),
    }

    # Extract close prices
    dom_closes = [float(c.get("close") or c.get("c") or 0) for c in dominant_candles if c.get("close")]
    hedge_closes = [float(c.get("close") or c.get("c") or 0) for c in hedge_candles if c.get("close")]

    if len(dom_closes) < lookback or len(hedge_closes) < lookback:
        result["error"] = f"Insufficient data: need {lookback} candles, got dom={len(dom_closes)} hedge={len(hedge_closes)}"
        return result

    # Use only last 'lookback' candles
    dom_array = dom_closes[-lookback:]
    hedge_array = hedge_closes[-lookback:]

    # Cointegration analysis
    coint = analyze_cointegration(dom_array, hedge_array)
    if "error" in coint:
        result["error"] = coint["error"]
        return result

    result["beta"] = coint["beta"]
    result["r_squared"] = coint["r_squared"]
    result["adf_pvalue"] = coint["adf_pvalue"]
    result["half_life"] = coint["half_life"]
    result["spread_std_pct"] = coint["spread_std_pct"]

    # Parameter suggestions
    result["suggested_entry_threshold"] = suggest_entry_threshold(coint["spread_std_pct"])
    result["suggested_take_profit"] = suggest_take_profit(coint["spread_std_pct"])

    if coint["beta"] is not None:
        min_r, max_r = suggest_hedge_ratio_range(coint["beta"])
        result["suggested_hedge_ratio_range"] = [min_r, max_r]

    # NATR for volatility context
    result["natr_dominant"] = calculate_natr(dominant_candles, 14)
    result["natr_hedge"] = calculate_natr(hedge_candles, 14)

    # Warnings
    if coint["r_squared"] is not None and coint["r_squared"] < 0.5:
        result["warnings"].append(f"Low R² ({coint['r_squared']:.2f}) – relationship may be weak")
    if coint["adf_pvalue"] is not None and coint["adf_pvalue"] > 0.05:
        result["warnings"].append(f"Spread not stationary (p={coint['adf_pvalue']:.3f})")
    if coint["half_life"] is not None and coint["half_life"] > 100:
        result["warnings"].append(f"Long half-life ({coint['half_life']:.0f} candles) – slow reversion")

    return result


def format_stat_arb_summary(analysis: Dict[str, Any]) -> str:
    """Format analysis results for display in Condor wizard final step."""
    if "error" in analysis:
        return f"⚠️ Analysis error: {analysis['error']}"

    lines = []
    lines.append("📊 Statistical Arbitrage Analysis")
    lines.append("")
    lines.append(f"Beta (hedge vs dominant): {analysis.get('beta', 'N/A')}")
    lines.append(f"R²: {analysis.get('r_squared', 'N/A')}")
    lines.append(f"ADF p-value (stationarity): {analysis.get('adf_pvalue', 'N/A')}")
    hl = analysis.get('half_life')
    lines.append(f"Half-life (candles): {hl if hl else 'N/A'}")
    lines.append(f"Spread std (%): {analysis.get('spread_std_pct', 'N/A')}")
    lines.append("")
    lines.append("💡 Suggested parameters:")
    lines.append(f"  entry_threshold: {analysis.get('suggested_entry_threshold', 2.0)}")
    lines.append(f"  take_profit: {analysis.get('suggested_take_profit', 0.0008)}")
    hr = analysis.get('suggested_hedge_ratio_range')
    if hr:
        lines.append(f"  dynamic_hedge_ratio range: [{hr[0]:.2f}, {hr[1]:.2f}]")
    lines.append("")
    if analysis.get("warnings"):
        lines.append("⚠️ Warnings:")
        for w in analysis["warnings"]:
            lines.append(f"  • {w}")
    return "\n".join(lines)
