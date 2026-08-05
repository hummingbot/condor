"""HIP-3 market scanner — ranks xyz-issuer perps for volume-farming market-making."""
import asyncio
import logging
import math

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes
from config_manager import get_client

logger = logging.getLogger(__name__)

CATEGORY = "Market Data"

HL_URL = "https://api.hyperliquid.xyz/info"


class Config(BaseModel):
    """Scan all markets of a HIP-3 builder issuer and return a shortlist for volume-farming MM."""
    issuer: str = Field(default="xyz", description="HIP-3 builder issuer slug (e.g. 'xyz')")
    min_spread_bps: float = Field(default=3.0, description="Minimum impact spread in bps")
    max_daily_drift_pct: float = Field(default=3.0, description="Maximum daily price drift %")
    min_oi_notional: float = Field(default=1_000_000.0, description="Minimum open interest in USD")
    min_book_depth_usd: float = Field(default=10_000.0, description="Min resting book notional within depth_within_bps, per side (liquidity filter)")
    depth_within_bps: float = Field(default=10.0, description="Band (bps from mid) over which book depth is measured")
    depth_check_top_k: int = Field(default=12, description="How many top-scored survivors to depth-check via l2Book (bounds API calls)")
    top_n: int = Field(default=5, description="Number of top markets to return")
    all_in_fee_bps_roundtrip: float = Field(default=2.6, description="Informational: total roundtrip fee in bps (~1.3bps/side)")


async def _fetch_book_depth(session, coin, ctx_mid, within_bps):
    """Return (bid_depth_usd, ask_depth_usd) resting within `within_bps` of mid. (0,0) on failure/empty."""
    try:
        async with session.post(
            HL_URL, json={"type": "l2Book", "coin": coin},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return 0.0, 0.0
            book = await resp.json()
        levels = book.get("levels") if isinstance(book, dict) else None
        if not levels or len(levels) != 2 or not levels[0] or not levels[1]:
            return 0.0, 0.0  # empty book = closed / illiquid
        bids, asks = levels[0], levels[1]
        best_bid = float(bids[0]["px"]); best_ask = float(asks[0]["px"])
        mid = (best_bid + best_ask) / 2 or ctx_mid
        if mid <= 0:
            return 0.0, 0.0

        def _side(side_levels, is_bid):
            tot = 0.0
            for lvl in side_levels:
                px = float(lvl["px"]); sz = float(lvl["sz"])
                off = (mid - px) / mid * 1e4 if is_bid else (px - mid) / mid * 1e4
                if off > within_bps:
                    break
                tot += px * sz
            return tot

        return _side(bids, True), _side(asks, False)
    except Exception as e:
        logger.warning(f"l2Book depth fetch failed for {coin}: {e}")
        return 0.0, 0.0


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    # Client is optional — only used for report persistence, not for the scan itself.
    client = await get_client(context._chat_id, context=context)

    issuer = config.issuer.lower()
    issuer_upper = issuer.upper()

    # ── 1. Fetch universe + contexts from Hyperliquid public API ──────────────
    payload = {"type": "metaAndAssetCtxs", "dex": issuer}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                HL_URL, json=payload, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return f"Hyperliquid API error: HTTP {resp.status}"
                data = await resp.json()
    except Exception as e:
        return f"Failed to fetch Hyperliquid data: {e}"

    if not isinstance(data, list) or len(data) < 2:
        return f"Unexpected API response shape: {type(data)}"

    meta, ctxs = data[0], data[1]
    universe = meta.get("universe", [])
    if not universe:
        return f"No markets found for issuer '{issuer}'"

    # ── 2. Compute per-market metrics ─────────────────────────────────────────
    markets = []
    for asset, ctx in zip(universe, ctxs):
        try:
            name = asset.get("name", "")           # e.g. "xyz:SMSN" (l2Book coin)
            pair = name.upper() + "-USD"            # e.g. "XYZ:SMSN-USD" (trading_pair)

            mid_raw = ctx.get("midPx")
            mark_raw = ctx.get("markPx")
            prev_raw = ctx.get("prevDayPx")
            impact_pxs = ctx.get("impactPxs")

            volume = float(ctx.get("dayNtlVlm", 0) or 0)
            mid = float(mid_raw) if mid_raw else 0.0
            mark = float(mark_raw) if mark_raw else 0.0
            prev = float(prev_raw) if prev_raw else 0.0

            open_market = (
                mid > 0
                and isinstance(impact_pxs, list)
                and len(impact_pxs) == 2
            )

            spread_bps = None
            if open_market:
                try:
                    bid_impact = float(impact_pxs[0])
                    ask_impact = float(impact_pxs[1])
                    spread_bps = (ask_impact - bid_impact) / mid * 1e4
                except (ValueError, TypeError, ZeroDivisionError):
                    open_market = False

            daily_drift_pct = abs(mark / prev - 1) * 100 if prev else 999.0
            oi_notional = float(ctx.get("openInterest", 0) or 0) * mark

            markets.append({
                "pair": pair,
                "coin": name,
                "volume": volume,
                "mid": mid,
                "mark": mark,
                "spread_bps": spread_bps,
                "daily_drift_pct": daily_drift_pct,
                "oi_notional": oi_notional,
                "book_depth_usd": None,
                "open_market": open_market,
                "maxLeverage": int(asset.get("maxLeverage", 0)),
                "funding": ctx.get("funding", "N/A"),
            })
        except Exception as e:
            logger.warning(f"Error processing market {asset.get('name', '?')}: {e}")

    total_markets = len(markets)

    # ── 3. Pre-filters (open / spread / drift / OI) ───────────────────────────
    prelim = [
        m for m in markets
        if (
            m["open_market"]
            and m["spread_bps"] is not None
            and m["spread_bps"] >= config.min_spread_bps
            and m["daily_drift_pct"] <= config.max_daily_drift_pct
            and m["oi_notional"] >= config.min_oi_notional
        )
    ]

    # ── 4. Score and rank (before depth check) ────────────────────────────────
    for m in prelim:
        m["score"] = (
            math.log(max(m["volume"], 1))
            + 0.3 * min(m["spread_bps"], 8.0)
            - 0.4 * m["daily_drift_pct"]
        )
    prelim.sort(key=lambda m: m["score"], reverse=True)

    # ── 5. LIQUIDITY FILTER — real book depth on the top-scored candidates ────
    # Only depth-check the top_k (bounds l2Book calls; the rest can't outrank them anyway).
    candidates = prelim[: config.depth_check_top_k]
    if candidates:
        try:
            async with aiohttp.ClientSession() as session:
                depths = await asyncio.gather(*[
                    _fetch_book_depth(session, m["coin"], m["mid"], config.depth_within_bps)
                    for m in candidates
                ])
            for m, (bid_d, ask_d) in zip(candidates, depths):
                # Require BOTH sides liquid for two-sided MM → use the weaker side.
                m["book_depth_usd"] = min(bid_d, ask_d)
        except Exception as e:
            logger.warning(f"Depth-check batch failed: {e}")

    survivors = [
        m for m in candidates
        if m["book_depth_usd"] is not None
        and m["book_depth_usd"] >= config.min_book_depth_usd
    ]
    survivors.sort(key=lambda m: m["score"], reverse=True)
    shortlist = survivors[: config.top_n]

    # ── 6. Fallback if zero survivors ─────────────────────────────────────────
    no_survivors = len(survivors) == 0
    fallback = []
    if no_survivors:
        fallback = sorted(markets, key=lambda m: m["volume"], reverse=True)[:5]

    # ── 7. Build summary ──────────────────────────────────────────────────────
    top_pick = shortlist[0]["pair"] if shortlist else "NONE"

    lines = [
        f"**HIP-3 Market Scanner — issuer: {issuer_upper}**",
        f"Scanned: {total_markets} markets | Pre-filter pass: {len(prelim)} | Depth-checked: {len(candidates)} | Survivors: {len(survivors)} | Top-{config.top_n} shown",
        f"Filters: spread >= {config.min_spread_bps}bps | drift <= {config.max_daily_drift_pct}% | OI >= ${config.min_oi_notional:,.0f} | depth >= ${config.min_book_depth_usd:,.0f}/side within {config.depth_within_bps}bps",
        f"Fee context: {config.all_in_fee_bps_roundtrip}bps round-trip (~{config.all_in_fee_bps_roundtrip / 2:.2f}bps/side)",
        "",
    ]

    def _mrow(rank, m, note=""):
        spd = f"{m['spread_bps']:.2f}" if m["spread_bps"] is not None else "N/A"
        dep = f"${m['book_depth_usd']:,.0f}" if m.get("book_depth_usd") is not None else "n/a"
        flag = f" [{note}]" if note else ""
        score_str = f" | Score={m['score']:.3f}" if "score" in m else ""
        return (
            f"  {rank}. {m['pair']}: Vol=${m['volume']:,.0f} | Spread={spd}bps"
            f" | Drift={m['daily_drift_pct']:.2f}% | Depth={dep}/side | OI=${m['oi_notional']:,.0f}"
            f" | Lev={m['maxLeverage']}x{score_str}{flag}"
        )

    if no_survivors:
        lines.append("WARNING: NONE PASSED FILTERS — top 5 by volume (informational):")
        for rank, m in enumerate(fallback, 1):
            lines.append(_mrow(rank, m, "NO FILTER PASS"))
    else:
        lines.append(f"TOP PICK: {top_pick}")
        lines.append("")
        for rank, m in enumerate(shortlist, 1):
            lines.append(_mrow(rank, m))

    summary = "\n".join(lines)

    # ── 8. Persistent report ──────────────────────────────────────────────────
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"HIP-3 Scanner: {issuer_upper}")
        builder.source("routine", "hip3_market_scanner").tags(
            ["market-making", "hip3", issuer, "scanner"]
        )
        builder.kpi("Markets Scanned", str(total_markets))
        builder.kpi("Survivors", str(len(survivors)))
        builder.kpi("Top Pick", top_pick)
        builder.kpi("Fee RT", f"{config.all_in_fee_bps_roundtrip}bps")

        display_list = shortlist if not no_survivors else fallback
        note_col = "Score" if not no_survivors else "Note"
        table_rows = []
        for rank, m in enumerate(display_list, 1):
            table_rows.append({
                "Rank": rank,
                "Pair": m["pair"],
                "24h Vol ($)": f"${m['volume']:,.0f}",
                "Spread (bps)": f"{m['spread_bps']:.2f}" if m["spread_bps"] is not None else "N/A",
                "Drift %": f"{m['daily_drift_pct']:.2f}%",
                "Depth/side ($)": f"${m['book_depth_usd']:,.0f}" if m.get("book_depth_usd") is not None else "n/a",
                "OI ($)": f"${m['oi_notional']:,.0f}",
                "MaxLev": m["maxLeverage"],
                note_col: f"{m['score']:.3f}" if "score" in m else "NO FILTER PASS",
            })

        if table_rows:
            builder.table(
                table_rows,
                ["Rank", "Pair", "24h Vol ($)", "Spread (bps)", "Drift %", "Depth/side ($)", "OI ($)", "MaxLev", note_col],
            )

        builder.markdown(summary)
        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return summary
