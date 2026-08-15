"""
Analysis for arbitrage controller - historical spread analysis and fee assessment.
"""

import logging
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


class ArbitrageAnalyzer:
    """
    Analyzes historical spread data to suggest optimal parameters.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.spread_values = []
        self.zscore_values = []

    def load_spread_data(self, spread_data: List[Dict]) -> None:
        """Load spread data from historical analysis."""
        self.spread_values = [s["spread"] for s in spread_data if not np.isnan(s["spread"])]
        self.zscore_values = [s["zscore"] for s in spread_data if not np.isnan(s["zscore"])]

    def suggest_min_profitability(self, fee_total: float = 0.002, percentile: float = 75) -> float:
        if not self.spread_values:
            return max(fee_total, 0.005)

        spread_sorted = sorted(self.spread_values)

        # Se P75 è negativo, usa un percentile più alto (es. P90)
        p75_idx = int(len(spread_sorted) * 75 / 100)
        p75_val = spread_sorted[p75_idx]

        if p75_val <= 0:
            # Usa P90 invece di P75
            p90_idx = int(len(spread_sorted) * 90 / 100)
            suggested_pct = spread_sorted[p90_idx]
            logger.info(f"P75 negativo ({p75_val:.4f}%), usando P90 = {suggested_pct:.4f}%")
        else:
            suggested_pct = p75_val

        suggested_decimal = suggested_pct / 100
        suggested_decimal = max(suggested_decimal, fee_total * 1.1)

        return suggested_decimal

    def get_spread_statistics(self) -> Dict[str, float]:
        """Get comprehensive spread statistics."""
        if not self.spread_values:
            return {}

        return {
            "min": min(self.spread_values),
            "max": max(self.spread_values),
            "mean": np.mean(self.spread_values),
            "median": np.median(self.spread_values),
            "std": np.std(self.spread_values),
            "p25": np.percentile(self.spread_values, 25),
            "p50": np.percentile(self.spread_values, 50),
            "p75": np.percentile(self.spread_values, 75),
            "p90": np.percentile(self.spread_values, 90),
            "p95": np.percentile(self.spread_values, 95),
        }


async def analyze_historical_spread(
    candles1: List[Dict],
    candles2: List[Dict],
    config: Dict[str, Any],
    fee_1: float = None,
    fee_2: float = None
) -> Dict[str, Any]:
    """
    Run historical spread analysis and return statistics.

    Args:
        candles1: Historical candles for exchange 1
        candles2: Historical candles for exchange 2
        config: Configuration dict
        fee_1: Fee rate for exchange 1 (optional)
        fee_2: Fee rate for exchange 2 (optional)

    Returns:
        Dict with statistics and suggestions
    """
    from .chart import calculate_spread_series

    # Calcola spread storico
    spread_data = calculate_spread_series(candles1, candles2)

    if not spread_data:
        return {"error": "No spread data available"}

    # Fee totali per round trip
    total_fee = (fee_1 or 0.001) + (fee_2 or 0.001)

    # Analizza
    analyzer = ArbitrageAnalyzer(config)
    analyzer.load_spread_data(spread_data)

    statistics = analyzer.get_spread_statistics()
    suggested_min_profitability = analyzer.suggest_min_profitability(
        fee_total=total_fee,
        percentile=75
    )

    # Calcola se la coppia è arbitraggiabile (P75 > fees)
    p75 = statistics.get('p75', 0)
    is_arbitrageable = (p75 / 100) > total_fee if p75 > 0 else False

    return {
        "statistics": statistics,
        "suggested_min_profitability": suggested_min_profitability,
        "total_fees_percent": total_fee * 100,
        "is_arbitrageable": is_arbitrageable,
        "total_samples": len(analyzer.spread_values)
    }
