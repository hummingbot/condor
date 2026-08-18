"""Scan Backpack Exchange perp markets: 24h volume, MM program thresholds, and tractability ranking."""

import logging

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.reports import ReportBuilder
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

CATEGORY = "Market Data"

BACKPACK_API = "https://api.backpack.exchange/api/v1"


class Config(BaseModel):
    """Backpack perp volume scan — MM program threshold analysis"""

    target_share_pct: float = Field(
        default=0.5, description="Target maker volume share % (e.g. 0.5 = 0.5%)"
    )
    min_volume_score_pct: float = Field(
        default=1.5, description="Min Volume Score % to qualify on a market"
    )


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    async with aiohttp.ClientSession() as session:
        # Fetch tickers and markets in parallel
        async with session.get(f"{BACKPACK_API}/tickers") as resp:
            if resp.status != 200:
                return f"Backpack API error: {resp.status}"
            tickers = await resp.json()

        async with session.get(f"{BACKPACK_API}/markets") as resp:
            if resp.status != 200:
                return f"Backpack markets API error: {resp.status}"
            markets = await resp.json()

    # Build market info lookup
    market_info = {m["symbol"]: m for m in markets if m.get("marketType") == "PERP"}

    # Filter perp tickers and parse volumes
    perps = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("_PERP"):
            continue
        quote_vol = float(t.get("quoteVolume", 0))
        last_price = float(t.get("lastPrice", 0))
        trades = int(t.get("trades", 0))
        price_change_pct = float(t.get("priceChangePercent", 0)) * 100
        base = sym.replace("_USDC_PERP", "").replace("k", "")

        # Get margin requirements from market info
        info = market_info.get(sym, {})
        imf_base = float(info.get("imfFunction", {}).get("base", 0))
        max_leverage = round(1 / imf_base, 1) if imf_base > 0 else 0

        perps.append(
            {
                "symbol": sym,
                "base": base,
                "quote_volume_24h": quote_vol,
                "last_price": last_price,
                "trades_24h": trades,
                "price_change_pct": round(price_change_pct, 2),
                "max_leverage": max_leverage,
            }
        )

    if not perps:
        return "No perpetual markets found on Backpack"

    # Sort by volume descending
    perps.sort(key=lambda x: x["quote_volume_24h"], reverse=True)

    total_volume = sum(p["quote_volume_24h"] for p in perps)

    # Calculate thresholds
    target_share = config.target_share_pct / 100
    min_vol_score = config.min_volume_score_pct / 100

    target_total_volume = total_volume * target_share  # 0.5% of total perp volume
    monthly_target = target_total_volume * 30

    # Per-market analysis
    table_data = []
    for p in perps:
        vol = p["quote_volume_24h"]
        vol_share = (vol / total_volume * 100) if total_volume > 0 else 0
        # Volume needed for 1.5% Volume Score on this market
        needed_for_qual = vol * min_vol_score / (1 - min_vol_score)
        # Volume needed for target share on this market
        needed_for_target = vol * target_share / (1 - target_share)

        table_data.append(
            {
                "Market": p["base"],
                "24h Vol": f"${vol:,.0f}",
                "Vol Share": f"{vol_share:.1f}%",
                "Trades": f"{p['trades_24h']:,}",
                "Price": (
                    f"${p['last_price']:,.4f}"
                    if p["last_price"] < 1
                    else f"${p['last_price']:,.2f}"
                ),
                "24h Chg": f"{p['price_change_pct']:+.1f}%",
                "Max Lev": f"{p['max_leverage']:.0f}x",
                "Need 1.5% VS": f"${needed_for_qual:,.0f}",
                "Need 0.5% Total": f"${needed_for_target:,.0f}",
                "_vol": vol,
                "_needed_qual": needed_for_qual,
                "_needed_target": needed_for_target,
            }
        )

    # Build report
    builder = ReportBuilder("Backpack Perp Volume Scan")
    builder.source("routine", "backpack_volume_scan")
    builder.tags(["backpack", "volume", "market-making", "perps"])
    builder.manual_order()

    builder.section("01 / PROGRAM OVERVIEW")
    builder.kpi("Total Perp Volume (24h)", f"${total_volume:,.0f}")
    builder.kpi("Perp Markets", str(len(perps)))
    builder.kpi(
        f"Target Share ({config.target_share_pct}%)", f"${target_total_volume:,.0f}/day"
    )
    builder.kpi("Monthly Target (30d)", f"${monthly_target:,.0f}")
    builder.kpi("Min Vol Score Threshold", f"{config.min_volume_score_pct}%")

    builder.section("02 / VOLUME BY MARKET")
    builder.markdown(
        "**Need 1.5% VS** = maker volume needed on that market to hit the 1.5% Volume Score minimum.\n\n"
        f"**Need {config.target_share_pct}% Total** = maker volume needed on that market alone to reach "
        f"{config.target_share_pct}% of total perp maker volume."
    )

    # Interactive dataset
    dataset_rows = []
    for p in perps:
        vol = p["quote_volume_24h"]
        vol_share = (vol / total_volume * 100) if total_volume > 0 else 0
        needed_qual = vol * min_vol_score / (1 - min_vol_score)
        needed_target = vol * target_share / (1 - target_share)
        dataset_rows.append(
            {
                "market": p["base"],
                "volume_24h": round(vol, 2),
                "vol_share_pct": round(vol_share, 2),
                "trades_24h": p["trades_24h"],
                "last_price": p["last_price"],
                "change_pct": p["price_change_pct"],
                "max_leverage": p["max_leverage"],
                "need_1_5_pct_vs": round(needed_qual, 2),
                "need_target_share": round(needed_target, 2),
            }
        )

    builder.dataset("perps", dataset_rows)

    builder.chart(
        "bar",
        "24h Volume by Market (USD)",
        "perps",
        "market",
        "volume_24h",
        y_label="Volume (USD)",
        x_label="Market",
    )

    # Display table (clean columns without internal fields)
    display_cols = [
        "Market",
        "24h Vol",
        "Vol Share",
        "Trades",
        "Price",
        "24h Chg",
        "Max Lev",
        "Need 1.5% VS",
        f"Need {config.target_share_pct}% Total",
    ]
    clean_table = [{k: row[k] for k in display_cols} for row in table_data]
    builder.table(clean_table, display_cols)

    builder.section("03 / TRACTABILITY RANKING")
    builder.markdown(
        "Markets sorted by **lowest maker volume needed** to hit 1.5% Volume Score — "
        "these are the easiest to qualify on."
    )

    # Sort by needed_for_qual ascending (most tractable first)
    tractable = sorted(table_data, key=lambda x: x["_needed_qual"])
    tract_table = []
    for i, row in enumerate(tractable[:15], 1):
        tract_table.append(
            {
                "#": i,
                "Market": row["Market"],
                "24h Vol": row["24h Vol"],
                "Need for 1.5% VS": row["Need 1.5% VS"],
                "Daily @ Target": row[f"Need {config.target_share_pct}% Total"],
            }
        )
    builder.table(
        tract_table, ["#", "Market", "24h Vol", "Need for 1.5% VS", "Daily @ Target"]
    )

    builder.section("04 / STRATEGY NOTES")
    builder.markdown(
        f"**Reward pool**: $200k/month for perps\n\n"
        f"**Blended Score** = (Volume Score × 0.8) + (Liquidity Score × 0.2)\n\n"
        f"**Volume Score** = your maker vol / total maker vol on that market\n\n"
        f"**Liquidity Score** = (Avg Order Size / Avg Spread) × Uptime\n\n"
        f"**Minimum thresholds per market**: Volume Score ≥ 1.5%, Avg order ≥ $2,000\n\n"
        f"**Tier Score** = max(Total Maker Share, Adjusted Maker Share) — "
        f"lower-liquidity markets get multipliers in the adjusted calculation, "
        f"making them more rewarding per dollar traded.\n\n"
        f"**First month**: MM5 maker fees (best tier) regardless of volume."
    )

    report_id = await builder.save()

    # Build inline summary
    top5 = perps[:5]
    top5_lines = "\n".join(
        f"  {p['base']}: ${p['quote_volume_24h']:,.0f} (need ${p['quote_volume_24h'] * min_vol_score / (1 - min_vol_score):,.0f} for 1.5% VS)"
        for p in top5
    )
    easiest3 = sorted(perps, key=lambda x: x["quote_volume_24h"])[:3]
    easiest_lines = "\n".join(
        f"  {p['base']}: need only ${p['quote_volume_24h'] * min_vol_score / (1 - min_vol_score):,.0f}/day"
        for p in easiest3
    )

    summary = (
        f"Backpack Perp Volume Scan\n"
        f"Total 24h perp volume: ${total_volume:,.0f}\n"
        f"Target ({config.target_share_pct}% share): ${target_total_volume:,.0f}/day\n\n"
        f"Top 5 by volume:\n{top5_lines}\n\n"
        f"Easiest to qualify (1.5% VS):\n{easiest_lines}\n\n"
        f"Report: {report_id}"
    )

    return RoutineResult(
        text=summary,
        table_data=[
            {k: row[k] for k in ["Market", "24h Vol", "Vol Share", "Need 1.5% VS"]}
            for row in table_data[:10]
        ],
        table_columns=["Market", "24h Vol", "Vol Share", "Need 1.5% VS"],
    )
