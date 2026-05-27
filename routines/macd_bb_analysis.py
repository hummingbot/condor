"""MACD + Bollinger Bands signal analysis for a single trading pair."""

CATEGORY = "Technical Analysis"

import logging

import numpy as np
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client
from routines.base import RoutineResult
from routines.hl_candles import fetch_hl_candles

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """Compute MACD + Bollinger Bands and return a LONG / SHORT / NEUTRAL signal."""

    connector_name: str = Field(
        default="binance_perpetual",
        description="Exchange connector for signal analysis",
    )
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair to analyze")
    interval: str = Field(default="1h", description="Candle interval")
    max_records: int = Field(default=200, description="Number of candles to fetch")
    macd_fast: int = Field(default=12, description="MACD fast EMA period")
    macd_slow: int = Field(default=26, description="MACD slow EMA period")
    macd_signal_period: int = Field(default=9, description="MACD signal EMA period")
    bb_period: int = Field(default=20, description="Bollinger Bands period")
    bb_std: float = Field(default=2.0, description="Bollinger Bands std dev multiplier")


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1)
    result = np.empty_like(values)
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
    return result


async def run(
    config: Config, context: ContextTypes.DEFAULT_TYPE
) -> str | RoutineResult:
    chat_id = context._chat_id if hasattr(context, "_chat_id") else None
    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available"

    # --- Fetch candles ---
    try:
        if config.connector_name == "hyperliquid_perpetual":
            candles = await fetch_hl_candles(
                config.trading_pair,
                config.interval,
                config.max_records,
            )
        else:
            candles = await client.market_data.get_candles(
                connector_name=config.connector_name,
                trading_pair=config.trading_pair,
                interval=config.interval,
                max_records=config.max_records,
            )
            if isinstance(candles, dict):
                candles = candles.get("data", [])
    except Exception as e:
        return f"Failed to fetch candles for {config.trading_pair}: {e}"

    min_required = config.macd_slow + config.macd_signal_period + config.bb_period
    if not candles or len(candles) < min_required:
        got = len(candles) if candles else 0
        return f"Failed to fetch candles for {config.trading_pair}: got {got}, need {min_required}"

    # --- Parse closes ---
    try:
        closes = np.array([float(c["close"]) for c in candles])
    except (KeyError, ValueError) as e:
        return f"Failed to parse candle data: {e}"

    # --- MACD ---
    ema_fast = _ema(closes, config.macd_fast)
    ema_slow = _ema(closes, config.macd_slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, config.macd_signal_period)
    histogram = macd_line - signal_line

    # --- Bollinger Bands ---
    n = len(closes)
    p = config.bb_period
    bb_mid = np.array([np.mean(closes[max(0, i - p + 1) : i + 1]) for i in range(n)])
    bb_std_arr = np.array([np.std(closes[max(0, i - p + 1) : i + 1]) for i in range(n)])
    bb_upper = bb_mid + config.bb_std * bb_std_arr
    bb_lower = bb_mid - config.bb_std * bb_std_arr

    # --- Current values ---
    close = closes[-1]
    macd_curr, macd_prev = macd_line[-1], macd_line[-2]
    sig_curr, sig_prev = signal_line[-1], signal_line[-2]
    hist_curr, hist_prev = histogram[-1], histogram[-2]
    bb_up, bb_mid_val, bb_lo = bb_upper[-1], bb_mid[-1], bb_lower[-1]

    bb_range = bb_up - bb_lo
    bb_pos = (close - bb_lo) / bb_range if bb_range > 0 else 0.5

    # --- Crossover ---
    bullish_cross = macd_prev < sig_prev and macd_curr >= sig_curr
    bearish_cross = macd_prev > sig_prev and macd_curr <= sig_curr

    # --- Signal logic ---
    # LONG: bullish crossover + price at or below midBB (2 conditions)
    c_long_cross = bullish_cross
    c_long_bb = close <= bb_mid_val
    long_signal = c_long_cross and c_long_bb

    # SHORT: bearish crossover + price at or above upperBB + MACD < 0 (3 conditions)
    c_short_cross = bearish_cross
    c_short_bb = close >= bb_up
    c_short_macd_neg = macd_curr < 0
    short_signal = c_short_cross and c_short_bb and c_short_macd_neg

    if long_signal:
        signal = "LONG"
    elif short_signal:
        signal = "SHORT"
    else:
        signal = "NEUTRAL"

    trend = "bullish" if macd_curr > 0 else "bearish"
    momentum = "increasing" if abs(hist_curr) > abs(hist_prev) else "decreasing"

    def _tick(val: bool) -> str:
        return "✓" if val else "✗"

    lines = [
        f"MACD+BB — {config.trading_pair} ({config.interval})",
        f"Signal: {signal}",
        "",
        f"Price:      {close:.6g}",
        f"BB upper:   {bb_up:.6g}",
        f"BB mid:     {bb_mid_val:.6g}",
        f"BB lower:   {bb_lo:.6g}",
        f"BB pos:     {bb_pos:.1%}  (0%=lower, 50%=mid, 100%=upper)",
        "",
        f"MACD:       {macd_curr:.6g}",
        f"Signal:     {sig_curr:.6g}",
        f"Histogram:  {hist_curr:.6g}  (prev: {hist_prev:.6g})",
        f"Trend:      {trend}  |  Momentum: {momentum}",
        "",
        f"LONG  [2/2]: crossover={_tick(c_long_cross)}  price≤midBB={_tick(c_long_bb)}",
        f"SHORT [3/3]: crossover={_tick(c_short_cross)}  price≥upperBB={_tick(c_short_bb)}  MACD<0={_tick(c_short_macd_neg)}",
    ]

    text = "\n".join(lines)

    table_columns = [
        "Pair",
        "Interval",
        "Signal",
        "Price",
        "BB Pos %",
        "BB Mid",
        "BB Upper",
        "MACD",
        "Signal Line",
        "Histogram",
        "Trend",
        "Momentum",
    ]
    table_row = {
        "Pair": config.trading_pair,
        "Interval": config.interval,
        "Signal": signal,
        "Price": round(float(close), 8),
        "BB Pos %": round(float(bb_pos * 100), 2),
        "BB Mid": round(float(bb_mid_val), 8),
        "BB Upper": round(float(bb_up), 8),
        "MACD": round(float(macd_curr), 8),
        "Signal Line": round(float(sig_curr), 8),
        "Histogram": round(float(hist_curr), 8),
        "Trend": trend,
        "Momentum": momentum,
    }
    conditions_columns = ["Rule", "Condition", "Met"]
    conditions_rows = [
        {"Rule": "LONG (2/2)", "Condition": "Bullish crossover", "Met": c_long_cross},
        {"Rule": "LONG (2/2)", "Condition": "Price <= midBB", "Met": c_long_bb},
        {"Rule": "SHORT (3/3)", "Condition": "Bearish crossover", "Met": c_short_cross},
        {"Rule": "SHORT (3/3)", "Condition": "Price >= upperBB", "Met": c_short_bb},
        {"Rule": "SHORT (3/3)", "Condition": "MACD < 0", "Met": c_short_macd_neg},
    ]

    # Lightweight report (no chart) so this routine surfaces in dashboard reports.
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"MACD+BB: {config.trading_pair} ({config.interval})")
        builder.source("routine", "macd_bb_analysis").tags(
            ["technical-analysis", "macd", "bollinger"]
        )
        builder.kpi(
            "Signal",
            signal,
            trend=(
                "positive"
                if signal == "LONG"
                else "negative" if signal == "SHORT" else "neutral"
            ),
        )
        builder.kpi("BB Position", f"{bb_pos * 100:.1f}%")
        builder.kpi("Histogram", f"{hist_curr:.6g}")
        builder.markdown(
            f"Connector: `{config.connector_name}`  \n"
            f"Pair: `{config.trading_pair}`  \n"
            f"Interval: `{config.interval}`"
        )
        builder.table([table_row], columns=table_columns)
        builder.markdown("### Entry Rules Check")
        builder.table(conditions_rows, columns=conditions_columns)
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    sections = [
        {
            "type": "kpi",
            "label": "Signal",
            "value": signal,
            "trend": (
                "positive"
                if signal == "LONG"
                else "negative" if signal == "SHORT" else "neutral"
            ),
        },
        {"type": "kpi", "label": "BB Position", "value": f"{bb_pos * 100:.1f}%"},
        {"type": "kpi", "label": "Histogram", "value": f"{hist_curr:.6g}"},
    ]

    return RoutineResult(
        text=text,
        table_data=[table_row],
        table_columns=table_columns,
        sections=sections,
    )
