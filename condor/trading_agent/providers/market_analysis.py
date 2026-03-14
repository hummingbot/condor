"""Market analysis provider: NATR, swing highs/lows, market structure from candles.

Fetches candle data via the Hummingbot API, computes technical indicators
using pure numpy/pandas (no external TA library), and provides a structured
summary for the trading agent prompt.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from . import register_provider
from .base import BaseProvider, ProviderResult

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pure numpy/pandas indicator calculations
# ---------------------------------------------------------------------------


def compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute Average True Range (ATR) using Wilder's smoothing."""
    high = np.asarray(highs, dtype=float)
    low = np.asarray(lows, dtype=float)
    close = np.asarray(closes, dtype=float)

    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]

    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))

    atr = np.full_like(tr, np.nan)
    if len(tr) < period:
        return atr

    # Wilder's smoothing: first ATR = SMA, then EMA-like
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    return atr


def compute_natr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute Normalized ATR (ATR / close * 100) — volatility as percentage."""
    atr = compute_atr(highs, lows, closes, period)
    close = np.asarray(closes, dtype=float)
    natr = np.where(close > 0, (atr / close) * 100, np.nan)
    return natr


def detect_swing_points(
    highs: np.ndarray,
    lows: np.ndarray,
    lookback: int = 5,
) -> tuple[list[dict], list[dict]]:
    """Detect swing highs and swing lows.

    A swing high is a candle whose high is the highest in a window of
    `lookback` candles on each side. Same logic inverted for swing lows.

    Returns (swing_highs, swing_lows) as lists of {index, price}.
    """
    swing_highs: list[dict] = []
    swing_lows: list[dict] = []

    n = len(highs)
    for i in range(lookback, n - lookback):
        window_highs = highs[i - lookback : i + lookback + 1]
        window_lows = lows[i - lookback : i + lookback + 1]

        if highs[i] == np.max(window_highs):
            swing_highs.append({"index": i, "price": float(highs[i])})
        if lows[i] == np.min(window_lows):
            swing_lows.append({"index": i, "price": float(lows[i])})

    return swing_highs, swing_lows


def determine_market_structure(
    swing_highs: list[dict],
    swing_lows: list[dict],
) -> dict[str, Any]:
    """Determine market structure from swing points.

    Returns dict with:
    - trend: "bullish", "bearish", or "ranging"
    - last_swing_high / last_swing_low
    - higher_highs / higher_lows / lower_highs / lower_lows counts
    """
    result: dict[str, Any] = {
        "trend": "ranging",
        "last_swing_high": None,
        "last_swing_low": None,
        "higher_highs": 0,
        "higher_lows": 0,
        "lower_highs": 0,
        "lower_lows": 0,
    }

    if len(swing_highs) >= 2:
        result["last_swing_high"] = swing_highs[-1]["price"]
        # Count consecutive HH/LH from recent
        for i in range(len(swing_highs) - 1, 0, -1):
            if swing_highs[i]["price"] > swing_highs[i - 1]["price"]:
                result["higher_highs"] += 1
            else:
                result["lower_highs"] += 1
                break

    if len(swing_lows) >= 2:
        result["last_swing_low"] = swing_lows[-1]["price"]
        for i in range(len(swing_lows) - 1, 0, -1):
            if swing_lows[i]["price"] > swing_lows[i - 1]["price"]:
                result["higher_lows"] += 1
            else:
                result["lower_lows"] += 1
                break

    # Determine trend
    hh = result["higher_highs"]
    hl = result["higher_lows"]
    lh = result["lower_highs"]
    ll = result["lower_lows"]

    if hh > 0 and hl > 0:
        result["trend"] = "bullish"
    elif lh > 0 and ll > 0:
        result["trend"] = "bearish"
    else:
        result["trend"] = "ranging"

    return result


def compute_support_resistance(
    swing_highs: list[dict],
    swing_lows: list[dict],
    current_price: float,
    n_levels: int = 3,
) -> dict[str, list[float]]:
    """Extract nearest support and resistance levels from swing points."""
    resistances = sorted(
        [sh["price"] for sh in swing_highs if sh["price"] > current_price],
    )[:n_levels]

    supports = sorted(
        [sl["price"] for sl in swing_lows if sl["price"] < current_price],
        reverse=True,
    )[:n_levels]

    return {"resistance": resistances, "support": supports}


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class MarketAnalysisProvider(BaseProvider):
    name = "market_analysis"
    is_core = True

    async def execute(self, client: Any, config: dict, agent_id: str = "") -> ProviderResult:
        connector = config.get("connector_name", "binance_perpetual")
        pair = config.get("trading_pair", "RIVER-USDT")
        natr_period = int(config.get("natr_period", 14))
        swing_lookback = int(config.get("swing_lookback", 5))
        candle_interval = config.get("candle_interval", "1s")
        candle_days = int(config.get("candle_days", 1))

        try:
            # Fetch candles via the HummingbotAPIClient router
            candles_raw = await client.market_data.get_candles_last_days(
                connector_name=connector,
                trading_pair=pair,
                interval=candle_interval,
                days=candle_days,
            )

            if not candles_raw or (isinstance(candles_raw, list) and len(candles_raw) == 0):
                return ProviderResult(
                    name=self.name,
                    data={"error": "no candle data"},
                    summary=f"Market Analysis ({pair}): no candle data available",
                )

            # Convert to dataframe
            df = pd.DataFrame(candles_raw)

            # Normalize column names (API may return different formats)
            col_map = {}
            for col in df.columns:
                cl = col.lower()
                if "high" in cl:
                    col_map[col] = "high"
                elif "low" in cl:
                    col_map[col] = "low"
                elif "close" in cl:
                    col_map[col] = "close"
                elif "open" in cl and "open_time" not in cl:
                    col_map[col] = "open"
                elif "volume" in cl and "quote" not in cl:
                    col_map[col] = "volume"
            df = df.rename(columns=col_map)

            for req_col in ["high", "low", "close", "open"]:
                if req_col not in df.columns:
                    return ProviderResult(
                        name=self.name,
                        data={"error": f"missing column: {req_col}"},
                        summary=f"Market Analysis ({pair}): candle data missing {req_col} column",
                    )

            # Ensure numeric
            for col in ["open", "high", "low", "close"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            df = df.dropna(subset=["open", "high", "low", "close"])

            if len(df) < natr_period + swing_lookback * 2:
                return ProviderResult(
                    name=self.name,
                    data={"error": "insufficient candles"},
                    summary=f"Market Analysis ({pair}): only {len(df)} candles, need more data",
                )

            highs = df["high"].values
            lows = df["low"].values
            closes = df["close"].values
            current_price = float(closes[-1])

            # Compute indicators
            natr = compute_natr(highs, lows, closes, period=natr_period)
            current_natr = float(natr[-1]) if not np.isnan(natr[-1]) else 0.0
            avg_natr_20 = float(np.nanmean(natr[-20:])) if len(natr) >= 20 else current_natr

            atr = compute_atr(highs, lows, closes, period=natr_period)
            current_atr = float(atr[-1]) if not np.isnan(atr[-1]) else 0.0

            # Swing detection
            swing_highs, swing_lows = detect_swing_points(highs, lows, lookback=swing_lookback)

            # Market structure
            structure = determine_market_structure(swing_highs, swing_lows)

            # Support/resistance
            sr_levels = compute_support_resistance(swing_highs, swing_lows, current_price)

            # Price action summary
            price_change_pct = ((current_price - float(closes[0])) / float(closes[0])) * 100
            high_of_period = float(np.max(highs))
            low_of_period = float(np.min(lows))
            range_pct = ((high_of_period - low_of_period) / current_price) * 100

            # Build result data
            data = {
                "pair": pair,
                "price": current_price,
                "natr": round(current_natr, 4),
                "natr_avg_20": round(avg_natr_20, 4),
                "atr": round(current_atr, 6),
                "trend": structure["trend"],
                "last_swing_high": structure["last_swing_high"],
                "last_swing_low": structure["last_swing_low"],
                "higher_highs": structure["higher_highs"],
                "higher_lows": structure["higher_lows"],
                "lower_highs": structure["lower_highs"],
                "lower_lows": structure["lower_lows"],
                "resistance_levels": sr_levels["resistance"],
                "support_levels": sr_levels["support"],
                "price_change_pct": round(price_change_pct, 4),
                "range_pct": round(range_pct, 4),
                "candle_count": len(df),
                "interval": candle_interval,
            }

            # Build summary for prompt
            res_str = ", ".join(f"{r:.6f}" for r in sr_levels["resistance"][:3]) if sr_levels["resistance"] else "none"
            sup_str = ", ".join(f"{s:.6f}" for s in sr_levels["support"][:3]) if sr_levels["support"] else "none"

            summary_lines = [
                f"Market Analysis ({pair} | {candle_interval} | {len(df)} candles):",
                f"  Price: {current_price:.6f} | Change: {price_change_pct:+.2f}% | Range: {range_pct:.2f}%",
                f"  ATR({natr_period}): {current_atr:.6f} | NATR: {current_natr:.4f}% (avg20: {avg_natr_20:.4f}%)",
                f"  Structure: {structure['trend'].upper()} | HH:{structure['higher_highs']} HL:{structure['higher_lows']} LH:{structure['lower_highs']} LL:{structure['lower_lows']}",
                f"  Last Swing High: {structure['last_swing_high']:.6f}" if structure["last_swing_high"] else "  Last Swing High: none",
                f"  Last Swing Low: {structure['last_swing_low']:.6f}" if structure["last_swing_low"] else "  Last Swing Low: none",
                f"  Resistance: {res_str}",
                f"  Support: {sup_str}",
            ]

            return ProviderResult(
                name=self.name,
                data=data,
                summary="\n".join(summary_lines),
            )

        except Exception as e:
            log.exception("MarketAnalysisProvider failed for %s", pair)
            return ProviderResult(
                name=self.name,
                data={"error": str(e)},
                summary=f"Market Analysis ({pair}): error — {e}",
            )


register_provider(MarketAnalysisProvider())
