import asyncio
import logging
from datetime import datetime, timezone

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORY = "Market Data"

# MM Program reward pools (USD/month)
REWARD_POOL = {"PERPETUALS": 200_000, "SPOT": 100_000}


class Config(BaseModel):
    """Backpack MM Program — volume landscape, reward estimates & capital needed per market."""

    min_volume_usd: float = Field(
        default=50_000, description="Min 24h volume USD to include"
    )
    top_n: int = Field(default=20, description="Top N markets per category")


def _normalize_symbol(symbol: str) -> tuple[str, str]:
    """Return (display_pair, category) from a Backpack ticker symbol."""
    if symbol.endswith("_PERP"):
        pair = symbol[:-5].replace("_", "-")
        return pair, "PERPETUALS"
    return symbol.replace("_", "-"), "SPOT"


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    async with aiohttp.ClientSession() as session:

        async def fetch_tickers():
            async with session.get(
                "https://api.backpack.exchange/api/v1/tickers",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Tickers returned HTTP {resp.status}")
                return await resp.json()

        async def fetch_mark_prices():
            async with session.get(
                "https://api.backpack.exchange/api/v1/markPrices",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"markPrices returned HTTP {resp.status}")
                    return []
                return await resp.json()

        try:
            tickers, mark_prices_raw = await asyncio.gather(
                fetch_tickers(), fetch_mark_prices()
            )
        except Exception as e:
            logger.warning(f"Backpack fetch failed: {e}")
            return f"Failed to fetch Backpack data: {e}"

    # Build funding rate lookup: symbol → funding rate %
    funding_by_symbol: dict[str, float] = {}
    for mp in mark_prices_raw or []:
        sym = mp.get("symbol", "")
        fr = mp.get("fundingRate")
        if fr is not None:
            try:
                funding_by_symbol[sym] = float(fr) * 100  # decimal → pct
            except (ValueError, TypeError):
                pass

    # Aggregate by category
    results_by_category: dict[str, list[dict]] = {"PERPETUALS": [], "SPOT": []}

    for t in tickers:
        symbol = t.get("symbol", "")
        quote_volume = float(t.get("quoteVolume", 0) or 0)
        last_price = float(t.get("lastPrice", 0) or 0)

        if quote_volume < config.min_volume_usd:
            continue

        pair, cat = _normalize_symbol(symbol)
        results_by_category[cat].append({
            "pair": pair,
            "symbol": symbol,
            "vol_24h_usd": quote_volume,
            "mid_price": last_price,
            "funding_rate_pct": funding_by_symbol.get(symbol),
        })

    for cat in results_by_category:
        results_by_category[cat].sort(key=lambda x: x["vol_24h_usd"], reverse=True)

    # Build dataset rows with program metrics
    all_rows: list[dict] = []
    total_counts: dict[str, int] = {}

    for cat, markets in results_by_category.items():
        total_counts[cat] = len(markets)
        pool = REWARD_POOL.get(cat, 0)

        for m in markets[: config.top_n]:
            vol = m["vol_24h_usd"]
            mid = m["mid_price"]

            # Volume needed to reach each share threshold on this specific market
            vol_needed_1_5pct = vol * 0.015
            vol_needed_5pct = vol * 0.05
            vol_needed_10pct = vol * 0.10

            # Upper-bound reward estimate:
            # If total category maker vol ≈ this market's vol, your reward ≈ pool * your_share
            # (This overstates for a multi-market program; treat as a directional indicator)
            est_reward_1_5pct = pool * 0.015
            est_reward_5pct = pool * 0.05
            est_reward_10pct = pool * 0.10

            # Capital efficiency at entry: reward per $1M of maker volume needed
            # efficiency = (pool * share) / (vol * share) * 1M = pool / vol * 1M
            # Lower vol market → higher reward per dollar traded
            efficiency_per_1m = round((pool / vol) * 1_000_000, 2) if vol > 0 else 0

            tile_label = f"{m['pair']} (PERP)" if cat == "PERPETUALS" else m["pair"]

            all_rows.append({
                "category": cat,
                "pair": m["pair"],
                "tile_label": tile_label,
                "vol_24h_usd": round(vol),
                "vol_needed_1_5pct": round(vol_needed_1_5pct),
                "vol_needed_5pct": round(vol_needed_5pct),
                "vol_needed_10pct": round(vol_needed_10pct),
                "est_reward_1_5pct": round(est_reward_1_5pct),
                "est_reward_5pct": round(est_reward_5pct),
                "est_reward_10pct": round(est_reward_10pct),
                "efficiency_per_1m": efficiency_per_1m,
                "mid_price": round(mid, 6),
                "band_low_100bps": round(mid * 0.99, 6),
                "band_high_100bps": round(mid * 1.01, 6),
                "funding_rate_pct": round(m["funding_rate_pct"], 4)
                if m["funding_rate_pct"] is not None
                else None,
            })

    n_perps = total_counts.get("PERPETUALS", 0)
    n_spot = total_counts.get("SPOT", 0)

    # ── ReportBuilder ─────────────────────────────────────────────────────────
    from condor.reports import ReportBuilder

    builder = ReportBuilder("Backpack MM Volume & Reward Analysis")
    builder.source("routine", "backpack_volume_scan").tags(
        ["backpack", "volume", "market_making", "rewards"]
    )
    builder.manual_order()

    builder.section(
        "Program Overview",
        f"Scanned {n_perps} perp and {n_spot} spot markets above ${config.min_volume_usd:,.0f} 24h vol — {timestamp}",
    )
    builder.kpi("Perp Reward Pool", "$200,000/mo")
    builder.kpi("Spot Reward Pool", "$100,000/mo")
    builder.kpi("Min Vol Share", "1.5%")
    builder.kpi("Min Order Size", "$2,000")
    builder.kpi("Max Spread", "100 bps from mid")
    builder.kpi("Blended Score", "Vol×0.8 + Liquidity×0.2")
    builder.kpi("Perp Markets Found", str(n_perps))
    builder.kpi("Spot Markets Found", str(n_spot))

    builder.section(
        "Reward Tiers",
        "Est. reward at each maker volume share tier (upper bound — actual depends on all competing MMs)",
    )
    builder.markdown(
        "| Share | Your Vol % | Perp Est. Reward | Spot Est. Reward | Note |\n"
        "|-------|-----------|-----------------|-----------------|------|\n"
        f"| Entry | **1.5%** | **${REWARD_POOL['PERPETUALS']*0.015:,.0f}** | **${REWARD_POOL['SPOT']*0.015:,.0f}** | ← minimum to qualify |\n"
        f"| Low   | 5%       | ${REWARD_POOL['PERPETUALS']*0.05:,.0f} | ${REWARD_POOL['SPOT']*0.05:,.0f} | Meaningful presence |\n"
        f"| Mid   | 10%      | ${REWARD_POOL['PERPETUALS']*0.10:,.0f} | ${REWARD_POOL['SPOT']*0.10:,.0f} | Competitive |\n"
        f"| High  | 20%      | ${REWARD_POOL['PERPETUALS']*0.20:,.0f} | ${REWARD_POOL['SPOT']*0.20:,.0f} | Dominant maker |\n\n"
        "*Blended Score = (Volume Score × 0.8) + (Liquidity Score × 0.2). "
        "Liquidity Score = (Avg Order Size / Avg Spread) × Uptime Score. "
        "Orders must be within **100 bps** of mid and avg size **≥ $2,000**.*"
    )

    if all_rows:
        builder.dataset("markets", all_rows)

        builder.section(
            "Volume Landscape",
            "Larger tile = more 24h volume. Lower-volume markets are easier to dominate and earn higher reward per dollar traded.",
        )
        builder.select_filter("cat-filter", "markets", "category", label="Category")
        builder.chart(
            "treemap",
            "24h Volume by Market",
            "markets",
            x="tile_label",
            y="vol_24h_usd",
            color="vol_24h_usd",
            value_label="Vol 24h",
            value_prefix="$",
            value_format=",.0f",
            color_label="Vol",
            color_prefix="$",
            color_format=",.0f",
        )

        builder.section(
            "Capital Efficiency",
            "Est. reward per $1M of maker volume at 1.5% share — lower-volume markets score higher here",
        )
        builder.chart(
            "horizontal_bar",
            "Reward per $1M Maker Volume (at 1.5% share)",
            "markets",
            x="pair",
            y="efficiency_per_1m",
            x_label="Market",
            y_label="Est. Reward / $1M Maker Vol ($)",
        )

        builder.section(
            "Market Details",
            "Volume needed at each share threshold and estimated reward (upper bound). "
            "Efficiency = pool/vol×$1M — purely a function of that market's total volume.",
        )
        builder.data_table(
            "markets",
            title="All Markets",
            columns=[
                "category",
                "pair",
                "vol_24h_usd",
                "vol_needed_1_5pct",
                "vol_needed_5pct",
                "vol_needed_10pct",
                "est_reward_1_5pct",
                "efficiency_per_1m",
                "funding_rate_pct",
                "mid_price",
                "band_low_100bps",
                "band_high_100bps",
            ],
        )

    await builder.save()

    # ── Inline text output ────────────────────────────────────────────────────
    lines = [f"Backpack MM Volume & Reward — {timestamp}", ""]

    for cat, markets in results_by_category.items():
        pool = REWARD_POOL.get(cat, 0)
        top = markets[: config.top_n]
        lines.append(f"{'='*54}")
        lines.append(f"{cat}  (pool: ${pool:,.0f}/mo)")
        lines.append(f"{'='*54}")
        if not top:
            lines.append("  No markets above threshold")
        for i, m in enumerate(top, 1):
            vol = m["vol_24h_usd"]
            v15 = vol * 0.015
            v5 = vol * 0.05
            mid = m["mid_price"]
            fr = m.get("funding_rate_pct")
            price_fmt = f"${mid:,.4f}" if mid < 100 else f"${mid:,.2f}"
            fr_str = f"  fr:{fr:+.4f}%" if fr is not None else ""
            lines.append(f"{i:>2}. {m['pair']:<22} ${vol:>14,.0f}{fr_str}")
            lines.append(
                f"    1.5%→${v15:>11,.0f} | 5%→${v5:>10,.0f}  mid:{price_fmt}"
            )
        lines.append("")

    lines += [
        "---",
        "Reward tiers (upper bound):  1.5%→ perp $3k/spot $1.5k | 5%→ $10k/$5k | 10%→ $20k/$10k",
        "Min: 1.5% vol share, ≥$2k avg order, spread ≤100bps from mid, ≥1% of allocation",
        "Score = (vol_score×0.8 + liquidity_score×0.2) × monthly_pool",
    ]

    return "\n".join(lines)
