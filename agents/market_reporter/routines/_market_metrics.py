"""Deterministic metrics shared by Crypto and TradFi market sources."""

from __future__ import annotations

import math
from statistics import fmean, pstdev
from typing import Any

from agents.market_reporter.routines._evidence import safe_float


def _return_pct(values: list[float], periods: int) -> float | None:
    if len(values) <= periods or values[-periods - 1] == 0:
        return None
    return (values[-1] / values[-periods - 1] - 1) * 100


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return fmean(values[-period:])


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes[-period:]]
    losses = [max(-change, 0.0) for change in changes[-period:]]
    average_gain = fmean(gains)
    average_loss = fmean(losses)
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100 - 100 / (1 + relative_strength)


def _realized_volatility(values: list[float], period: int = 20) -> float | None:
    if len(values) <= period:
        return None
    returns = []
    for previous, current in zip(values[-period - 1 : -1], values[-period:]):
        if previous > 0 and current > 0:
            returns.append(math.log(current / previous))
    if len(returns) < period:
        return None
    return pstdev(returns) * math.sqrt(365) * 100


def _volume_zscore(volumes: list[float], period: int = 20) -> float | None:
    if len(volumes) < period:
        return None
    window = volumes[-period:]
    deviation = pstdev(window)
    if deviation == 0:
        return 0.0
    return (window[-1] - fmean(window)) / deviation


def calculate_ohlcv_metrics(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return transparent metrics for ascending daily OHLCV rows."""
    valid = []
    for row in rows:
        close = safe_float(row.get("close"))
        volume = safe_float(row.get("volume"))
        if close is None or close <= 0 or volume is None or volume < 0:
            continue
        valid.append({**row, "close": close, "volume": volume})
    if len(valid) < 2:
        return None
    closes = [row["close"] for row in valid]
    volumes = [row["volume"] for row in valid]
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    return {
        "last_observation": str(valid[-1].get("timestamp") or ""),
        "last_price": round(closes[-1], 10),
        "return_1d_pct": _rounded(_return_pct(closes, 1)),
        "return_7d_pct": _rounded(_return_pct(closes, 7)),
        "return_30d_pct": _rounded(_return_pct(closes, 30)),
        "sma20": _rounded(sma20, 10),
        "sma50": _rounded(sma50, 10),
        "above_sma20": closes[-1] > sma20 if sma20 is not None else None,
        "above_sma50": closes[-1] > sma50 if sma50 is not None else None,
        "rsi14": _rounded(_rsi(closes)),
        "realized_volatility_20d_pct": _rounded(_realized_volatility(closes)),
        "volume_zscore_20d": _rounded(_volume_zscore(volumes)),
        "observation_count": len(valid),
    }


def compact_series(
    rows: list[dict[str, Any]],
    *,
    symbol: str,
    maximum: int,
) -> list[dict[str, Any]]:
    """Return bounded chart rows without provider-only fields."""
    output = []
    for row in rows[-maximum:]:
        close = safe_float(row.get("close"))
        if close is None:
            continue
        output.append(
            {
                "timestamp": str(row.get("timestamp") or ""),
                "symbol": symbol,
                "open": safe_float(row.get("open")),
                "high": safe_float(row.get("high")),
                "low": safe_float(row.get("low")),
                "close": close,
                "volume": safe_float(row.get("volume")),
            }
        )
    return output


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


def _rounded(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None
