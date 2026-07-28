from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client
import logging
import statistics
import asyncio

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"


class Config(BaseModel):
    """Multi-timeframe grid direction check (1h/6h/12h). Recommends LONG_GRID/SHORT_GRID/TWO_SIDED_GRID/HOLD with confidence 1-5."""

    connector_name: str = Field(default="binance_perpetual", description="Exchange connector name")
    trading_pair: str = Field(default="BTC-USDT", description="Trading pair to analyze")
    short_tf: str = Field(default="1h", description="Short timeframe interval")
    mid_tf: str = Field(default="6h", description="Mid timeframe interval")
    long_tf: str = Field(default="12h", description="Long timeframe interval")
    rsi_period: int = Field(default=14, description="RSI calculation period")


def _momentum_roc(closes: list, period: int = 14) -> float:
    """Rate of change momentum: (current - n_ago) / n_ago * 100."""
    if len(closes) < period + 1:
        return 0.0
    return (closes[-1] - closes[-period]) / closes[-period] * 100


def _rsi(closes: list, period: int = 14) -> float:
    """Simple RSI."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = statistics.mean(gains[-period:])
    avg_loss = statistics.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


async def _fetch(client, connector: str, pair: str, interval: str, records: int) -> list:
    result = await client.market_data.get_candles(connector, pair, interval=interval, max_records=records)
    return result if isinstance(result, list) else result.get("data", result.get("candles", []))


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    try:
        raw_short, raw_mid, raw_long = await asyncio.gather(
            _fetch(client, config.connector_name, config.trading_pair, config.short_tf, 50),
            _fetch(client, config.connector_name, config.trading_pair, config.mid_tf, 30),
            _fetch(client, config.connector_name, config.trading_pair, config.long_tf, 20),
        )

        tf_configs = [
            (config.short_tf, raw_short),
            (config.mid_tf, raw_mid),
            (config.long_tf, raw_long),
        ]

        tf_data = {}
        for tf_name, raw in tf_configs:
            if not raw or len(raw) < 5:
                continue
            closes = [float(c["close"]) for c in raw]
            sma_short = statistics.mean(closes[-5:])
            sma_long = statistics.mean(closes)
            mom = _momentum_roc(closes, period=min(config.rsi_period, len(closes) - 1))
            rsi = _rsi(closes, period=config.rsi_period)

            if sma_short > sma_long * 1.001:
                trend = "bullish"
            elif sma_short < sma_long * 0.999:
                trend = "bearish"
            else:
                trend = "neutral"

            tf_data[tf_name] = {
                "closes": closes,
                "momentum": mom,
                "rsi": rsi,
                "current": closes[-1],
                "trend": trend,
                "sma_short": sma_short,
                "sma_long": sma_long,
            }

        if not tf_data:
            return "Could not fetch candle data for any timeframe"

        # Alignment scoring
        bullish_count = sum(1 for d in tf_data.values() if d["trend"] == "bullish")
        bearish_count = sum(1 for d in tf_data.values() if d["trend"] == "bearish")
        total = len(tf_data)

        # Divergence: momentum sign differs across timeframes
        mom_signs = [1 if d["momentum"] >= 0 else -1 for d in tf_data.values()]
        has_divergence = len(set(mom_signs)) > 1

        # RSI overbought/oversold flags
        rsi_flags = []
        for tf_name, d in tf_data.items():
            if d["rsi"] > 70:
                rsi_flags.append(f"{tf_name} RSI overbought ({d['rsi']:.1f})")
            elif d["rsi"] < 30:
                rsi_flags.append(f"{tf_name} RSI oversold ({d['rsi']:.1f})")

        # Recommendation logic
        if bullish_count == total:
            recommendation = "LONG_GRID"
            confidence = 5 if not has_divergence else 4
            reasoning = f"All {total} timeframes bullish"
        elif bearish_count == total:
            recommendation = "SHORT_GRID"
            confidence = 5 if not has_divergence else 4
            reasoning = f"All {total} timeframes bearish"
        elif bullish_count > bearish_count:
            recommendation = "LONG_GRID"
            confidence = 4 if not has_divergence else 3
            reasoning = f"{bullish_count}/{total} timeframes bullish"
        elif bearish_count > bullish_count:
            recommendation = "SHORT_GRID"
            confidence = 4 if not has_divergence else 3
            reasoning = f"{bearish_count}/{total} timeframes bearish"
        elif has_divergence:
            recommendation = "HOLD"
            confidence = 2
            reasoning = "Mixed signals with momentum divergence — unsafe to commit direction"
        else:
            recommendation = "TWO_SIDED_GRID"
            confidence = 3
            reasoning = "Balanced momentum across timeframes — ranging/neutral conditions"

        if rsi_flags:
            reasoning += f"; {', '.join(rsi_flags)}"

        divergence_list = []
        if has_divergence:
            divergence_list.append("Momentum direction divergence across timeframes")
        divergence_list.extend(rsi_flags)

        tf_table = [
            {
                "Timeframe": tf_name,
                "Trend": d["trend"].capitalize(),
                "Momentum": f"{d['momentum']:+.2f}%",
                "RSI": f"{d['rsi']:.1f}",
                "SMA Short": f"{d['sma_short']:,.4f}",
                "SMA Long": f"{d['sma_long']:,.4f}",
            }
            for tf_name, d in tf_data.items()
        ]

        # Report
        try:
            from condor.reports import ReportBuilder

            builder = ReportBuilder(f"MTF Grid Check: {config.trading_pair}")
            builder.source("routine", "hourly_mtf_check").tags(["analysis", "grid", "mtf"])

            builder.section("01 / RECOMMENDATION")
            builder.kpi("Recommendation", recommendation)
            builder.kpi("Confidence", f"{confidence} / 5")
            builder.kpi("Bullish TFs", f"{bullish_count}/{total}")
            builder.kpi("Bearish TFs", f"{bearish_count}/{total}")

            builder.section(
                "02 / TIMEFRAME BREAKDOWN",
                f"Timeframes analyzed: {config.short_tf}, {config.mid_tf}, {config.long_tf}",
            )
            builder.table(tf_table, ["Timeframe", "Trend", "Momentum", "RSI", "SMA Short", "SMA Long"])

            if divergence_list:
                builder.section("03 / DIVERGENCES & FLAGS")
                builder.markdown("\n".join(f"- {d}" for d in divergence_list))

            builder.section("04 / REASONING")
            builder.markdown(reasoning)

            builder.manual_order()
            await builder.save()
        except Exception as e:
            logger.warning(f"Report generation failed: {e}")

        from routines.base import RoutineResult

        return RoutineResult(
            text=(
                f"{config.trading_pair} MTF Check → **{recommendation}** "
                f"(confidence {confidence}/5) | {reasoning}"
            ),
            sections=[
                {"type": "kpi", "label": "Recommendation", "value": recommendation},
                {"type": "kpi", "label": "Confidence", "value": f"{confidence}/5"},
                {"type": "kpi", "label": "Bullish TFs", "value": f"{bullish_count}/{total}"},
                {"type": "kpi", "label": "Bearish TFs", "value": f"{bearish_count}/{total}"},
            ],
        )

    except Exception as e:
        logger.error(f"hourly_mtf_check error: {e}")
        return f"Error running MTF check: {e}"
