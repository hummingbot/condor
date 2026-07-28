from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client
import logging
import statistics

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"


class Config(BaseModel):
    """7-day baseline analysis: trend direction, support/resistance levels, and ATR volatility for the adaptive grid trader."""

    connector_name: str = Field(default="binance_perpetual", description="Exchange connector name")
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair to analyze")
    lookback_days: int = Field(default=7, description="Number of days to look back (uses 4h candles)")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    try:
        max_records = config.lookback_days * 6 + 10  # 6 four-hour candles per day
        result = await client.market_data.get_candles(
            config.connector_name, config.trading_pair, interval="4h", max_records=max_records
        )
        records = result if isinstance(result, list) else result.get("data", result.get("candles", []))

        if not records or len(records) < 5:
            return f"Insufficient candle data for {config.trading_pair} on {config.connector_name}"

        closes = [float(c["close"]) for c in records]
        highs = [float(c["high"]) for c in records]
        lows = [float(c["low"]) for c in records]
        current_price = closes[-1]

        # Trend: SMA short vs SMA long
        sma_short = statistics.mean(closes[-7:])
        sma_long = statistics.mean(closes[-21:]) if len(closes) >= 21 else statistics.mean(closes)

        if sma_short > sma_long * 1.001:
            trend_direction = "up"
        elif sma_short < sma_long * 0.999:
            trend_direction = "down"
        else:
            trend_direction = "range"

        # ATR-based volatility
        true_ranges = []
        for i in range(1, len(records)):
            high = float(records[i]["high"])
            low = float(records[i]["low"])
            prev_close = float(records[i - 1]["close"])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)

        atr_period = min(14, len(true_ranges))
        atr = statistics.mean(true_ranges[-atr_period:])
        volatility_pct = (atr / current_price) * 100

        # Support/resistance via swing highs/lows
        window = 3
        resistance_levels = []
        support_levels = []
        for i in range(window, len(highs) - window):
            if highs[i] == max(highs[i - window : i + window + 1]):
                resistance_levels.append(highs[i])
            if lows[i] == min(lows[i - window : i + window + 1]):
                support_levels.append(lows[i])

        resistance_levels.sort(key=lambda x: abs(x - current_price))
        support_levels.sort(key=lambda x: abs(x - current_price))

        key_levels = []
        for level in resistance_levels[:3]:
            key_levels.append(
                {
                    "level": round(level, 4),
                    "type": "resistance",
                    "distance_pct": round((level - current_price) / current_price * 100, 2),
                }
            )
        for level in support_levels[:3]:
            key_levels.append(
                {
                    "level": round(level, 4),
                    "type": "support",
                    "distance_pct": round((level - current_price) / current_price * 100, 2),
                }
            )
        key_levels.sort(key=lambda x: abs(x["distance_pct"]))

        period_high = max(highs)
        period_low = min(lows)
        range_pct = (period_high - period_low) / period_low * 100

        # Report
        try:
            from condor.reports import ReportBuilder

            builder = ReportBuilder(f"{config.lookback_days}d Baseline: {config.trading_pair}")
            builder.source("routine", "baseline_7d").tags(["analysis", "grid", "baseline"])

            builder.section("01 / TREND", f"{config.lookback_days}d of 4h candles — SMA cross signal")
            builder.kpi("Trend Direction", trend_direction.upper())
            builder.kpi("Current Price", f"{current_price:,.4f}")
            builder.kpi("SMA Short (7c)", f"{sma_short:,.4f}")
            builder.kpi("SMA Long (21c)", f"{sma_long:,.4f}")

            builder.section("02 / VOLATILITY", "ATR (14-period) on 4h candles")
            builder.kpi("ATR (14-period)", f"{atr:,.4f}")
            builder.kpi("Volatility %", f"{volatility_pct:.2f}%")
            builder.kpi(f"{config.lookback_days}d High", f"{period_high:,.4f}")
            builder.kpi(f"{config.lookback_days}d Low", f"{period_low:,.4f}")
            builder.kpi("Price Range %", f"{range_pct:.2f}%")

            builder.section("03 / KEY LEVELS", "Nearest swing-high resistances and swing-low supports")
            if key_levels:
                builder.table(
                    [
                        {
                            "Level": f"{level['level']:,.4f}",
                            "Type": level["type"].capitalize(),
                            "Distance %": f"{level['distance_pct']:+.2f}%",
                        }
                        for level in key_levels
                    ],
                    ["Level", "Type", "Distance %"],
                )
            else:
                builder.markdown("_No significant swing levels detected in this window._")

            builder.manual_order()
            await builder.save()
        except Exception as e:
            logger.warning(f"Report generation failed: {e}")

        from routines.base import RoutineResult

        return RoutineResult(
            text=(
                f"{config.trading_pair} {config.lookback_days}d Baseline | "
                f"Trend: **{trend_direction.upper()}** | "
                f"Volatility: {volatility_pct:.2f}% | "
                f"Key levels: {len(key_levels)}"
            ),
            sections=[
                {"type": "kpi", "label": "Trend Direction", "value": trend_direction.upper()},
                {"type": "kpi", "label": "Volatility %", "value": f"{volatility_pct:.2f}%"},
                {"type": "kpi", "label": "ATR", "value": f"{atr:,.4f}"},
                {"type": "kpi", "label": "Key Levels", "value": str(len(key_levels))},
            ],
        )

    except Exception as e:
        logger.error(f"baseline_7d error: {e}")
        return f"Error running baseline analysis: {e}"
