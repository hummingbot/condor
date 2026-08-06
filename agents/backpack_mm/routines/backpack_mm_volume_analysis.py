import asyncio
import logging

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Market Data"


class Config(BaseModel):
    """Backpack exchange volume analysis for Market Maker Program qualification."""

    connector: str = Field(
        default="backpack_perpetual",
        description="Connector (backpack_perpetual or backpack)",
    )
    min_volume_usd: float = Field(
        default=100_000, description="Min 24h volume USD to include a market"
    )
    top_n: int = Field(default=20, description="Show top N markets by volume")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    # 1. Discover all pairs via prices endpoint
    try:
        prices = await client.market_data.get_prices(config.connector, trading_pairs=[])
    except Exception as e:
        return f"Failed to fetch prices from '{config.connector}': {e}"

    if not prices:
        return f"No markets returned for connector '{config.connector}'"

    pairs = list(prices.keys())
    logger.info(f"Discovered {len(pairs)} pairs on {config.connector}")

    # 2. Fetch 24x 1h candles per pair, parallel with rate-limit
    sem = asyncio.Semaphore(10)

    async def fetch_candles(pair: str):
        async with sem:
            try:
                result = await client.market_data.get_candles(
                    config.connector, pair, "1h", max_records=24
                )
                records = (
                    result
                    if isinstance(result, list)
                    else result.get("data", result.get("candles", []))
                )
                return pair, records if isinstance(records, list) else []
            except Exception as e:
                logger.warning(f"Candles fetch failed for {pair}: {e}")
                return pair, []

    candle_results = await asyncio.gather(*[fetch_candles(p) for p in pairs])

    # 3. Compute per-market metrics
    markets_raw = []
    for pair, candles in candle_results:
        if not candles:
            continue
        mid_price = float(prices.get(pair, 0))
        if mid_price <= 0:
            continue
        # Volume in USD = sum(base_volume * close_price) per candle
        vol_usd = sum(
            float(c.get("volume", 0)) * float(c.get("close", mid_price))
            for c in candles
        )
        if vol_usd < config.min_volume_usd:
            continue

        markets_raw.append(
            {
                "pair": pair,
                "mid_price": mid_price,
                "vol_usd": vol_usd,
                "t_1_5": vol_usd * 0.015,
                "t_5": vol_usd * 0.05,
                "t_10": vol_usd * 0.10,
                "t_20": vol_usd * 0.20,
                "band_low": mid_price * 0.99,
                "band_high": mid_price * 1.01,
            }
        )

    if not markets_raw:
        return f"No markets passed ${config.min_volume_usd:,.0f} volume filter on '{config.connector}'"

    # 4. Sort descending by volume, cap to top_n
    markets_raw.sort(key=lambda x: x["vol_usd"], reverse=True)
    markets_raw = markets_raw[: config.top_n]

    # 5. ReportBuilder report
    from condor.reports import ReportBuilder

    builder = ReportBuilder("Backpack MM Volume Analysis")
    builder.source("routine", "backpack_mm_volume_analysis").tags(
        ["backpack", "market-making", "volume"]
    )

    total_vol = sum(m["vol_usd"] for m in markets_raw)

    builder.section("Overview", f"Top {len(markets_raw)} markets on {config.connector}")
    builder.kpi("Markets Shown", str(len(markets_raw)))
    builder.kpi("Combined 24h Vol", f"${total_vol:,.0f}")
    builder.kpi("Min Vol Filter", f"${config.min_volume_usd:,.0f}")
    builder.kpi("Total Pairs Discovered", str(len(pairs)))

    # Dataset for interactive chart + table
    dataset_rows = [
        {
            "pair": m["pair"],
            "vol_usd": round(m["vol_usd"]),
            "threshold_1_5": round(m["t_1_5"]),
            "threshold_5": round(m["t_5"]),
            "threshold_10": round(m["t_10"]),
            "threshold_20": round(m["t_20"]),
            "mid_price": m["mid_price"],
            "band_low": round(m["band_low"], 6),
            "band_high": round(m["band_high"], 6),
        }
        for m in markets_raw
    ]
    builder.dataset("markets", dataset_rows)

    builder.section(
        "Volume Landscape",
        "Lower total volume = easier to reach the 1.5% threshold with less capital",
    )
    builder.chart(
        "horizontal_bar",
        "24h Volume by Market (USD)",
        "markets",
        x="pair",
        y="vol_usd",
        x_label="Market",
        y_label="24h Volume (USD)",
    )

    builder.section(
        "Market Details",
        "Capital needed per maker-volume tier (orders must stay within 100bps of mid)",
    )
    builder.data_table(
        "markets",
        title="Markets",
        columns=[
            "pair",
            "vol_usd",
            "threshold_1_5",
            "threshold_5",
            "threshold_10",
            "threshold_20",
            "mid_price",
        ],
    )

    builder.markdown(
        "### Maker Volume Share Tiers\n\n"
        "| Tier | Share | Use Case |\n"
        "|------|-------|----------|\n"
        "| Entry | 1.5% | Minimum to qualify for rewards |\n"
        "| Low | 5% | Meaningful presence |\n"
        "| Mid | 10% | Competitive |\n"
        "| High | 20% | Dominant maker |\n\n"
        "*Blended Score = (Volume Score × 0.8) + (Liquidity Score × 0.2)*\n"
        "*Avg order size ≥ $2k and ≥ 1% of total allocation also required.*"
    )

    builder.manual_order()
    await builder.save()

    # 6. Inline summary via RoutineResult
    from routines.base import RoutineResult

    header = f"{'#':<3} {'Pair':<22} {'24h Vol (USD)':>15} {'1.5% Target':>13} {'Mid Price':>12}"
    sep = "-" * 68
    rows = [header, sep]
    for i, m in enumerate(markets_raw, 1):
        rows.append(
            f"{i:<3} {m['pair']:<22}"
            f"${m['vol_usd']:>14,.0f}"
            f"${m['t_1_5']:>12,.0f}"
            f"${m['mid_price']:>11,.4f}"
        )

    summary = (
        f"Backpack MM Volume — {config.connector}\n"
        f"{len(markets_raw)} markets above ${config.min_volume_usd:,.0f} 24h vol\n\n"
        + "\n".join(rows)
    )
    return RoutineResult(text=summary)
