import asyncio
import logging
import math

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"


class Config(BaseModel):
    """Multi-timeframe analysis for the one-way-only adaptive grid trader."""

    trading_pair: str = Field(default="BTC-USDT")
    connector_name: str = Field(default="binance_perpetual")
    lifetime_hours: float = Field(
        default=8.0,
        description="Expected grid lifetime for range calculation (6-12h typical due to anti-flip rule)",
    )
    atr_period: int = Field(default=14)
    baseline_atr: float = Field(
        default=0.0,
        description="From baseline_7d — 0 means compute from 1h candles only",
    )
    atr_reference_7d: float = Field(
        default=0.0,
        description="Seven-day rolling 1h ATR median; classification only, never range sizing",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_candles(result) -> list:
    """Defensively parse candles from API response."""
    if result is None:
        return []
    if isinstance(result, list):
        return result
    return result.get("data", result.get("candles", []))


def _compute_ema(closes: list, period: int) -> list:
    """Compute EMA series for a list of close prices."""
    if len(closes) < period:
        return []
    k = 2.0 / (period + 1)
    ema = [sum(closes[:period]) / period]
    for price in closes[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


def _compute_atr(candles: list, period: int) -> float:
    """Compute ATR(period) from candle dicts."""
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        high = float(candles[i].get("high", 0) or 0)
        low = float(candles[i].get("low", 0) or 0)
        prev_close = float(candles[i - 1].get("close", 0) or 0)
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if not trs:
        return 0.0
    if len(trs) < period:
        return sum(trs) / len(trs)
    # Wilder's smoothed ATR
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _trend_direction(candles: list, fast: int = 9, slow: int = 21) -> str:
    """Determine trend via EMA crossover (fast / slow). Returns BULLISH / BEARISH / NEUTRAL."""
    if len(candles) < slow + 2:
        return "NEUTRAL"
    closes = [float(c.get("close", 0) or 0) for c in candles]
    fast_ema = _compute_ema(closes, fast)
    slow_ema = _compute_ema(closes, slow)
    if not fast_ema or not slow_ema:
        return "NEUTRAL"
    diff_pct = (fast_ema[-1] - slow_ema[-1]) / slow_ema[-1] * 100 if slow_ema[-1] else 0
    if diff_pct > 0.3:
        return "BULLISH"
    elif diff_pct < -0.3:
        return "BEARISH"
    return "NEUTRAL"


def _volatility_level(atr: float, atr_reference: float, candles_24: list) -> tuple:
    """Classify ATR against its 7d reference and return 24h range context."""
    highs = [float(c.get("high", 0) or 0) for c in candles_24]
    lows = [float(c.get("low", 0) or 0) for c in candles_24]
    closes = [float(c.get("close", 0) or 0) for c in candles_24]
    range_high = max(highs) if highs else 0.0
    range_low = min(lows) if lows else 0.0
    current = closes[-1] if closes else 0.0
    range_size = range_high - range_low
    range_pct = (range_size / current * 100) if current else 0.0
    range_pos = (current - range_low) / range_size if range_size > 0 else 0.5
    # This reference is report-only.  It must not feed D or actionable prices.
    reference = atr_reference if atr_reference > 0 else atr
    vol_ratio = atr / reference if reference > 0 else 1.0
    if vol_ratio > 1.2:
        vol_level = "HIGH"
    elif vol_ratio >= 0.8:
        vol_level = "MODERATE"
    else:
        vol_level = "LOW"
    return vol_level, range_high, range_low, range_pct, range_pos


def _confidence(trend_4h: str, trend_1d: str, closes_1h: list) -> str:
    """Return HIGH / MEDIUM / LOW based on 3-TF agreement."""
    if trend_4h == "NEUTRAL" and trend_1d == "NEUTRAL":
        return "MEDIUM"
    if trend_4h != trend_1d:
        return "LOW"
    # 4h and 1d agree on a direction — check 1h alignment
    fast = _compute_ema(closes_1h, 9)
    slow = _compute_ema(closes_1h, 21)
    if fast and slow and slow[-1]:
        diff = (fast[-1] - slow[-1]) / slow[-1] * 100
        aligned = (trend_4h == "BULLISH" and diff > 0.3) or (
            trend_4h == "BEARISH" and diff < -0.3
        )
        return "HIGH" if aligned else "MEDIUM"
    return "MEDIUM"


def _synthesise_profile(trend_4h: str, trend_1d: str) -> tuple[str, str, str]:
    """Return (profile, suggested_lean, rationale) without two-sided output."""
    if trend_4h == "BULLISH" and trend_1d == "BULLISH":
        return (
            "LONG_GRID",
            "LONG",
            "Both medium (4h) and macro (1d) timeframes confirm bullish momentum.",
        )
    if trend_4h == "BEARISH" and trend_1d == "BEARISH":
        return (
            "SHORT_GRID",
            "SHORT",
            "Both medium (4h) and macro (1d) timeframes confirm bearish pressure.",
        )

    if trend_4h == "BULLISH" and trend_1d != "BEARISH":
        lean = "LONG"
    elif trend_4h == "BEARISH" and trend_1d != "BULLISH":
        lean = "SHORT"
    elif trend_1d == "BULLISH" and trend_4h != "BEARISH":
        lean = "LONG"
    elif trend_1d == "BEARISH" and trend_4h != "BULLISH":
        lean = "SHORT"
    else:
        lean = "NONE"
    return (
        "HOLD",
        lean,
        "The 4h and 1d timeframes do not agree on one direction. "
        "HOLD has no actionable levels; the lean is context only.",
    )


def _price_levels(profile: str, current_price: float, distance: float) -> tuple:
    """Return actionable (start, end, limit), or all None for HOLD."""
    if profile == "LONG_GRID":
        return (
            current_price - distance,
            current_price + 3.0 * distance,
            current_price - 1.5 * distance,
        )
    if profile == "SHORT_GRID":
        return (
            current_price - 3.0 * distance,
            current_price + distance,
            current_price + 1.5 * distance,
        )
    return None, None, None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    # -- 1. Fetch three timeframes in parallel --
    try:
        raw_1h, raw_4h, raw_1d = await asyncio.gather(
            client.market_data.get_candles(
                config.connector_name, config.trading_pair, "1h", max_records=50
            ),
            # _trend_direction needs slow EMA period + 2 = 23 candles minimum,
            # so keep a margin above that or the timeframe reads NEUTRAL forever.
            client.market_data.get_candles(
                config.connector_name, config.trading_pair, "4h", max_records=60
            ),
            client.market_data.get_candles(
                config.connector_name, config.trading_pair, "1d", max_records=60
            ),
        )
    except Exception as e:
        logger.error(f"[hourly_mtf_check] candle fetch failed: {e}")
        return f"Error fetching market data: {e}"

    candles_1h = _parse_candles(raw_1h)
    candles_4h = _parse_candles(raw_4h)
    candles_1d = _parse_candles(raw_1d)

    if not candles_1h:
        return "No 1h candle data available — cannot proceed."

    # -- 2. Per-timeframe analysis --
    # 1h: ATR, range, volatility
    atr_1h = _compute_atr(candles_1h, config.atr_period)
    if config.baseline_atr > 0:
        atr_1h = (atr_1h + config.baseline_atr) / 2.0

    window_24 = candles_1h[-24:] if len(candles_1h) >= 24 else candles_1h
    vol_level, range_high, range_low, range_pct, range_pos = _volatility_level(
        atr_1h, config.atr_reference_7d, window_24
    )
    current_price = float(candles_1h[-1].get("close", 0) or 0)

    # 4h: trend direction
    trend_4h = _trend_direction(candles_4h) if candles_4h else "NEUTRAL"

    # 1d: trend confirmation
    trend_1d = _trend_direction(candles_1d) if candles_1d else "NEUTRAL"

    # -- 3. Signal synthesis --
    profile, suggested_lean, rationale = _synthesise_profile(trend_4h, trend_1d)

    closes_1h = [float(c.get("close", 0) or 0) for c in candles_1h]
    conf = _confidence(trend_4h, trend_1d, closes_1h)

    # -- 4. Price level computation: D = ATR(1h) × √(lifetime_hours) --
    D = atr_1h * math.sqrt(config.lifetime_hours)

    start_price, end_price, limit_price = _price_levels(profile, current_price, D)

    # -- Build levels table --
    if profile == "LONG_GRID":
        levels_rows = [
            {
                "Level": "limit_price",
                "Price": f"{limit_price:,.4f}",
                "Description": "Safety / invalidation boundary",
            },
            {
                "Level": "start_price",
                "Price": f"{start_price:,.4f}",
                "Description": "Grid lower bound",
            },
            {
                "Level": "current_price",
                "Price": f"{current_price:,.4f}",
                "Description": "Reference at analysis time",
            },
            {
                "Level": "end_price",
                "Price": f"{end_price:,.4f}",
                "Description": "Grid upper bound / TP zone",
            },
        ]
    elif profile == "SHORT_GRID":
        levels_rows = [
            {
                "Level": "end_price",
                "Price": f"{end_price:,.4f}",
                "Description": "Grid upper bound",
            },
            {
                "Level": "current_price",
                "Price": f"{current_price:,.4f}",
                "Description": "Reference at analysis time",
            },
            {
                "Level": "start_price",
                "Price": f"{start_price:,.4f}",
                "Description": "Grid lower bound / TP zone",
            },
            {
                "Level": "limit_price",
                "Price": f"{limit_price:,.4f}",
                "Description": "Safety / invalidation boundary",
            },
        ]
    else:
        levels_rows = [
            {
                "Level": "current_price",
                "Price": f"{current_price:,.4f}",
                "Description": "Reference only — HOLD has no actionable levels",
            },
        ]

    # -- 5. ReportBuilder --
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"MTF Check — {config.trading_pair}")
        builder.source("routine", "hourly_mtf_check").tags(
            ["grid", "mtf", "analysis", "adaptive"]
        )

        # Section 1: Timeframe Summary
        builder.section(
            "01 / TIMEFRAME SUMMARY",
            "Per-timeframe: trend direction, ATR/volatility, signal",
        )
        tf_rows = [
            {
                "Timeframe": "1h (Range & Vol)",
                "Candles": len(candles_1h),
                "ATR(14)": f"{atr_1h:.4f}",
                "24h Range": f"{range_low:,.2f} – {range_high:,.2f}",
                "Range %": f"{range_pct:.2f}%",
                "Volatility": vol_level,
                "Price in Range": f"{range_pos:.1%}",
                "Signal": "—",
            },
            {
                "Timeframe": "4h proxy (Trend)",
                "Candles": len(candles_4h),
                "ATR(14)": "—",
                "24h Range": "—",
                "Range %": "—",
                "Volatility": "—",
                "Price in Range": "—",
                "Signal": trend_4h,
            },
            {
                "Timeframe": "1d proxy (Confirm)",
                "Candles": len(candles_1d),
                "ATR(14)": "—",
                "24h Range": "—",
                "Range %": "—",
                "Volatility": "—",
                "Price in Range": "—",
                "Signal": trend_1d,
            },
        ]
        builder.table(
            tf_rows,
            [
                "Timeframe",
                "Candles",
                "ATR(14)",
                "24h Range",
                "Range %",
                "Volatility",
                "Price in Range",
                "Signal",
            ],
        )

        # Section 2: Signal Synthesis
        builder.section(
            "02 / SIGNAL SYNTHESIS",
            "Agreement analysis across timeframes and final profile decision",
        )
        builder.kpi("4h Trend", trend_4h)
        builder.kpi("1d Trend", trend_1d)
        builder.kpi("1h Volatility", vol_level)
        builder.kpi("Confidence", conf)
        agreement_str = "AGREE ✓" if trend_4h == trend_1d else "DISAGREE ✗"
        builder.markdown(
            f"**4h vs 1d agreement:** {agreement_str}  \n"
            f"**Current price:** {current_price:,.4f}  \n"
            f"**ATR(1h, {config.atr_period}):** {atr_1h:.6f}"
            + (
                f"  *(blended with baseline {config.baseline_atr})*"
                if config.baseline_atr > 0
                else ""
            )
            + f"  \n**D = ATR × √{config.lifetime_hours:.1f}h = {D:.6f}**"
        )

        # Section 3: Recommendation
        builder.section(
            "03 / RECOMMENDATION", "Actionable grid profile with concrete price levels"
        )
        builder.kpi("Profile", profile)
        builder.kpi("Suggested Lean", suggested_lean)
        builder.kpi("Confidence", conf)
        builder.kpi("D Value", f"{D:.4f}")
        builder.kpi("Current Price", f"{current_price:,.4f}")

        actionable = profile != "HOLD"
        if limit_price is not None:
            builder.kpi("Limit Price", f"{limit_price:,.4f}")
        if actionable:
            builder.kpi("Start Price", f"{start_price:,.4f}")
            builder.kpi("End Price", f"{end_price:,.4f}")

        builder.table(levels_rows, ["Level", "Price", "Description"])
        builder.markdown(f"**Rationale:** {rationale}")

        builder.manual_order()
        report_id = await builder.save()
        logger.info(f"[hourly_mtf_check] report saved: {report_id}")
    except Exception as e:
        logger.warning(f"[hourly_mtf_check] report generation failed: {e}")

    # -- Return structured text for agent parsing --
    lines = [
        f"MTF Check — {config.trading_pair}",
        f"profile: {profile}",
        f"suggested_lean: {suggested_lean}",
        f"confidence: {conf}",
        f"4h: {trend_4h} | 1d: {trend_1d} | Volatility: {vol_level}",
        f"Current price: {current_price:,.4f} | ATR(1h): {atr_1h:.6f} | D: {D:.6f}",
    ]
    if profile == "LONG_GRID":
        lines.append(
            f"limit_price={limit_price:.4f} | start_price={start_price:.4f} | end_price={end_price:.4f}"
        )
    elif profile == "SHORT_GRID":
        lines.append(
            f"start_price={start_price:.4f} | end_price={end_price:.4f} | limit_price={limit_price:.4f}"
        )
    else:
        lines.append("actionable_levels: NONE")
    lines.append(f"Rationale: {rationale}")
    return "\n".join(lines)
