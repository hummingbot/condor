"""TradFi breadth, curve, and relative-strength calculations."""

from __future__ import annotations

from statistics import fmean
from typing import Any

from agents.market_reporter.routines._evidence import safe_float


def breadth_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in rows if row.get("metrics")]
    above20 = [row for row in eligible if row["metrics"].get("above_sma20") is True]
    above50 = [row for row in eligible if row["metrics"].get("above_sma50") is True]
    returns = [safe_float(row["metrics"].get("return_7d_pct")) for row in eligible]
    valid_returns = [value for value in returns if value is not None]
    return {
        "eligible_count": len(eligible),
        "above_sma20_pct": (
            round(len(above20) / len(eligible) * 100, 2) if eligible else None
        ),
        "above_sma50_pct": (
            round(len(above50) / len(eligible) * 100, 2) if eligible else None
        ),
        "average_return_7d_pct": (
            round(fmean(valid_returns), 4) if valid_returns else None
        ),
    }


def treasury_curve(points: dict[str, float | None]) -> dict[str, Any]:
    two_year = safe_float(points.get("2y"))
    ten_year = safe_float(points.get("10y"))
    three_month = safe_float(points.get("3m"))
    thirty_year = safe_float(points.get("30y"))
    return {
        "points_pct": points,
        "slope_2s10s_bps": (
            round((ten_year - two_year) * 100, 2)
            if two_year is not None and ten_year is not None
            else None
        ),
        "slope_3m10y_bps": (
            round((ten_year - three_month) * 100, 2)
            if three_month is not None and ten_year is not None
            else None
        ),
        "slope_10s30s_bps": (
            round((thirty_year - ten_year) * 100, 2)
            if ten_year is not None and thirty_year is not None
            else None
        ),
    }


def relative_strength(
    asset_return: float | None,
    benchmark_return: float | None,
) -> float | None:
    asset = safe_float(asset_return)
    benchmark = safe_float(benchmark_return)
    if asset is None or benchmark is None:
        return None
    return round(asset - benchmark, 4)
