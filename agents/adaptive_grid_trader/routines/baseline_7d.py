import logging

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"


class Config(BaseModel):
    """Compute 7-day baseline stats: ATR, range, trend for a perpetual pair."""

    trading_pair: str = Field(default="BTC-USDT")
    connector_name: str = Field(default="binance_perpetual")
    atr_period: int = Field(default=14, description="Candles for ATR calculation")


def _ema(values: list, period: int) -> list:
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    result = [seed]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _compute_atr(candles: list, period: int) -> float:
    if len(candles) < period + 1:
        return 0.0
    true_ranges = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        lo = float(candles[i]["low"])
        prev_c = float(candles[i - 1]["close"])
        tr = max(h - lo, abs(h - prev_c), abs(lo - prev_c))
        true_ranges.append(tr)
    recent_trs = true_ranges[-period:]
    return sum(recent_trs) / len(recent_trs)


def _price_slope_pct(closes: list, lookback: int = 48) -> float:
    """Return % change of close over the last `lookback` candles (or all available).
    Positive = price rising, negative = price falling."""
    n = min(lookback, len(closes))
    if n < 2:
        return 0.0
    start = closes[-n]
    end = closes[-1]
    return ((end - start) / start * 100) if start else 0.0


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    # --- Fetch 7 days of 1h candles ---
    try:
        result = await client.market_data.get_candles(
            config.connector_name,
            config.trading_pair,
            interval="1h",
            max_records=168,
        )
        records = (
            result
            if isinstance(result, list)
            else result.get("data", result.get("candles", []))
        )
    except Exception as e:
        return f"Failed to fetch candles: {e}"

    if not records or len(records) < 20:
        return (
            f"Insufficient candle data — got {len(records) if records else 0} candles, "
            "need at least 20."
        )

    try:
        records = sorted(records, key=lambda c: c["timestamp"])
    except Exception:
        pass

    highs = [float(c["high"]) for c in records]
    lows = [float(c["low"]) for c in records]
    closes = [float(c["close"]) for c in records]

    # --- 7D range ---
    seven_d_high = max(highs)
    seven_d_low = min(lows)
    range_pct = (seven_d_high - seven_d_low) / seven_d_low * 100

    # --- ATR ---
    atr = _compute_atr(records, config.atr_period)
    current_price = closes[-1]
    atr_pct = (atr / current_price * 100) if current_price else 0.0

    # --- EMAs ---
    ema20_series = _ema(closes, 20)
    ema50_series = _ema(closes, 50)

    if not ema20_series or not ema50_series:
        trend_direction = "NEUTRAL"
        trend_strength = "weak"
        ema20_val = ema50_val = 0.0
        sep_vs_atr = 0.0
        price_slope = 0.0
        price_vs_emas = "—"
    else:
        ema20_val = ema20_series[-1]
        ema50_val = ema50_series[-1]
        separation = ema20_val - ema50_val
        sep_vs_atr = abs(separation) / atr if atr > 0 else 0.0

        # --- Price slope over last 48h (2 days) ---
        price_slope = _price_slope_pct(closes, 48)

        # --- Price position relative to EMAs ---
        price_below_both = current_price < ema20_val and current_price < ema50_val
        price_above_both = current_price > ema20_val and current_price > ema50_val
        if price_below_both:
            price_vs_emas = "BELOW_BOTH"
        elif price_above_both:
            price_vs_emas = "ABOVE_BOTH"
        else:
            price_vs_emas = "BETWEEN"

        # --- Price-action override (checked first) ---
        # Strong price slope + price on one side of both EMAs = directional
        # regardless of EMA ordering. This catches trends early during EMA
        # crossovers where lagging EMAs would say NEUTRAL.
        price_action_override = None
        if price_below_both and price_slope < -1.0:
            price_action_override = "BEARISH"
        elif price_above_both and price_slope > 1.0:
            price_action_override = "BULLISH"

        # --- Trend direction ---
        # EMA separation threshold 0.15x ATR (was 0.3x): calls directional
        # sooner so the baseline doesn't lag behind obvious moves.
        # Micro-slope uses 6-candle lookback (was 3) for stability.
        if ema20_val > ema50_val:
            if sep_vs_atr >= 0.15:
                trend_direction = "BULLISH"
            else:
                rising = len(ema20_series) >= 6 and ema20_series[-1] > ema20_series[-6]
                trend_direction = "BULLISH" if rising else "NEUTRAL"
        elif ema20_val < ema50_val:
            if sep_vs_atr >= 0.15:
                trend_direction = "BEARISH"
            else:
                falling = len(ema20_series) >= 6 and ema20_series[-1] < ema20_series[-6]
                trend_direction = "BEARISH" if falling else "NEUTRAL"
        else:
            trend_direction = "NEUTRAL"

        # Apply price-action override if EMA-based logic said NEUTRAL
        if trend_direction == "NEUTRAL" and price_action_override:
            trend_direction = price_action_override

        # --- Soft price-vs-EMA nudge for remaining NEUTRAL ---
        # Lower threshold (0.25% slope, was 0.5%) so mild downtrends
        # with price below both EMAs still register as directional.
        if trend_direction == "NEUTRAL":
            if price_below_both and price_slope < -0.25:
                trend_direction = "BEARISH"
            elif price_above_both and price_slope > 0.25:
                trend_direction = "BULLISH"

        # --- Strength ---
        if sep_vs_atr < 0.15:
            trend_strength = "weak"
        elif sep_vs_atr < 0.8:
            trend_strength = "moderate"
        else:
            trend_strength = "strong"

    # --- Report ---
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"7D Baseline — {config.trading_pair}")
        builder.source("routine", "baseline_7d").tags(
            ["baseline", "adaptive_grid", "atr", "trend"]
        )

        builder.section(
            "7-Day Market Snapshot",
            f"{config.trading_pair} on {config.connector_name}  |  {len(records)} x 1h candles",
        )
        builder.kpi("Current Price", f"${current_price:,.2f}")
        builder.kpi("7D High", f"${seven_d_high:,.2f}")
        builder.kpi("7D Low", f"${seven_d_low:,.2f}")
        builder.kpi("7D Range %", f"{range_pct:.2f}%")

        builder.section(
            "Volatility — ATR",
            f"Average True Range over last {config.atr_period} x 1h candles",
        )
        builder.kpi("ATR (1h)", f"${atr:,.2f}")
        builder.kpi("ATR % of Price", f"{atr_pct:.3f}%")

        builder.section("Trend", "EMA20 vs EMA50 on 1h closes + price slope")
        builder.kpi("EMA20", f"${ema20_val:,.2f}")
        builder.kpi("EMA50", f"${ema50_val:,.2f}")
        builder.kpi("EMA Sep / ATR", f"{sep_vs_atr:.2f}x")
        builder.kpi("Price vs EMAs", price_vs_emas)
        builder.kpi("48h Price Slope", f"{price_slope:+.2f}%")
        builder.kpi("Trend Direction", trend_direction)
        builder.kpi("Trend Strength", trend_strength)

        builder.manual_order()
        report_id = await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")
        report_id = None

    summary = (
        f"7D Baseline — {config.trading_pair}\n"
        f"Price: ${current_price:,.2f}  |  High: ${seven_d_high:,.2f}  |  Low: ${seven_d_low:,.2f}\n"
        f"Range: {range_pct:.2f}%  |  ATR(1h,{config.atr_period}): ${atr:,.2f} ({atr_pct:.3f}%)\n"
        f"Trend: {trend_direction} / {trend_strength}  |  EMA20: ${ema20_val:,.2f}  |  EMA50: ${ema50_val:,.2f}\n"
        f"Price vs EMAs: {price_vs_emas}  |  48h Slope: {price_slope:+.2f}%"
    )
    if report_id:
        summary += f"\nReport: {report_id}"
    return summary
