"""Scan & rank Solana CLMM memecoin pools by fee yield (fees/TVL) for LP entry.

Agent-local routine for solana_dex_lp_expert. Sources pools from GeckoTerminal
(trending + top-per-venue), filters to the configured quote asset + venues
(quote matched by mint on either pool side — GeckoTerminal orientation varies),
enriches the top candidates with on-chain CLMM pool-info (fee %, bin_step/tick,
mints), computes fee_yield = fees(window)/TVL, and returns a ranked shortlist
with the exact fields to open an lp_executor — including the MEMECOIN mint and a
mint-based trading pair (memecoins are not resolvable by symbol on Gateway, so
swaps/opens must use the mint). Base/quote orientation can differ between
GeckoTerminal and the connector (Orca reports SOL as base), so the memecoin mint
is resolved as the pool side that is NOT the quote asset.

Diversification: pass `exclude_pools` (held pool addresses) and/or `exclude_mints`
(held base mints) to keep the ranking from returning pools/tokens you already
hold — the agent should never open a 2nd slot on the same pool or token.
"""
import logging
import asyncio
import re
import aiohttp
from pydantic import BaseModel, Field, field_validator
from telegram.ext import ContextTypes
from config_manager import get_client
from handlers.dex.geckoterminal import _extract_pool_data

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

GECKO_BASE = "https://api.geckoterminal.com/api/v2"
GECKO_NETWORK = "solana"          # GeckoTerminal network id
CLMM_NETWORK = "solana-mainnet-beta"  # hummingbot-api CLMM network id
_WINDOW_TO_FIELD = {"1h": "volume_1h", "6h": "volume_6h", "24h": "volume_24h"}
_QUOTE_MINTS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
}


class Config(BaseModel):
    """Rank Solana CLMM memecoin pools by fee yield (fees/TVL) for LP entry."""
    quote_asset: str = Field(default="SOL", description="Quote token to require (SOL or USDC)")
    venues: list[str] = Field(default=["meteora", "orca", "raydium"], description="CLMM venues to allow")
    ranking_window: str = Field(default="24h", description="Volume window for fees/TVL: 1h, 6h, or 24h")
    top_n: int = Field(default=10, description="Number of ranked pools to return")
    min_tvl_usd: float = Field(default=25000.0, description="Minimum pool TVL (reserve) in USD")
    exclude_pools: list[str] = Field(default=[], description="Pool addresses to exclude (already held)")
    exclude_mints: list[str] = Field(default=[], description="Base token mints to exclude (already held)")

    @field_validator("venues", "exclude_pools", "exclude_mints", mode="before")
    @classmethod
    def _coerce_list(cls, v):
        # Telegram config forms submit these as text ("" when left blank).
        if isinstance(v, str):
            return [s for s in re.split(r"[,\s]+", v.strip()) if s]
        return v


async def _gecko_get(session: aiohttp.ClientSession, path: str, params: dict | None = None) -> dict:
    url = f"{GECKO_BASE}/{path}"
    headers = {"Accept": "application/json;version=20230302"}
    async with session.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=25)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"GeckoTerminal {path} -> HTTP {resp.status}")
        return await resp.json()


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available — cannot fetch CLMM pool-info."

    quote = config.quote_asset.strip().upper()
    quote_mint = _QUOTE_MINTS.get(quote)
    if not quote_mint:
        return f"lp_scanner: unsupported quote_asset '{quote}' — supported: {', '.join(_QUOTE_MINTS)}."
    venues = [v.strip().lower() for v in config.venues]
    vol_field = _WINDOW_TO_FIELD.get(config.ranking_window, "volume_24h")
    excl_pools = {p for p in config.exclude_pools if p}
    excl_mints = {m for m in config.exclude_mints if m}

    # 1. Source candidates from GeckoTerminal: global trending + top-per-venue.
    raw: list[dict] = []
    try:
        async with aiohttp.ClientSession() as session:
            tasks = [_gecko_get(session, f"networks/{GECKO_NETWORK}/trending_pools")]
            for v in venues:
                tasks.append(_gecko_get(session, f"networks/{GECKO_NETWORK}/dexes/{v}/pools", {"page": 1}))
            results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"lp_scanner: gecko source failed: {r}")
                continue
            raw.extend(r.get("data", []) or [])
    except Exception as e:
        return f"lp_scanner: failed to reach GeckoTerminal: {e}"

    # 2. Parse via shared extractor, filter (quote, venue, TVL floor, excludes), dedupe by address.
    seen: set[str] = set()
    candidates: list[dict] = []
    for p in raw:
        d = _extract_pool_data(p)
        addr = d.get("address") or ""
        if not addr or addr in excl_pools:
            continue
        base_sym = d.get("base_token_symbol") or ""
        quote_sym = d.get("quote_token_symbol") or ""
        if not base_sym or not quote_sym or "?" in (base_sym, quote_sym):
            continue
        dex = d.get("dex_id") or ""
        if addr in seen:
            continue
        if dex not in venues:
            continue
        # Match the quote side by MINT and accept either GeckoTerminal orientation:
        # a SOL-quoted scan must also find pools Gecko lists as SOL/USDC (SOL as base).
        gecko_base_mint = d.get("base_token_address") or ""
        gecko_quote_mint = d.get("quote_token_address") or ""
        price_usd = _num(d.get("base_token_price_usd"))
        if gecko_quote_mint == quote_mint:
            pass
        elif gecko_base_mint == quote_mint:
            base_sym, quote_sym = quote_sym, base_sym
            gecko_base_mint, gecko_quote_mint = gecko_quote_mint, gecko_base_mint
            price_usd = _num(d.get("quote_token_price_usd"))
        else:
            continue
        tvl = _num(d.get("reserve_usd"))
        if tvl < config.min_tvl_usd:
            continue
        vol_window = _num(d.get(vol_field))
        if vol_window <= 0:
            continue
        seen.add(addr)
        candidates.append({
            "pool_address": addr,
            "dex": dex,
            "base_symbol": base_sym,
            "quote_symbol": quote_sym,
            "trading_pair": f"{base_sym}-{quote_sym}",
            "gecko_base_mint": gecko_base_mint,
            "gecko_quote_mint": gecko_quote_mint,
            "tvl_usd": tvl,
            "vol_window": vol_window,
            "price_usd": price_usd,
        })

    if not candidates:
        return (f"lp_scanner: no {quote}-quoted pools on {venues} passed the filters "
                f"(TVL >= ${config.min_tvl_usd:,.0f}, window {config.ranking_window}, "
                f"{len(excl_pools)} pools/{len(excl_mints)} mints excluded).")

    # Pre-rank by turnover (vol/TVL) so we only enrich the most promising ones.
    candidates.sort(key=lambda c: c["vol_window"] / max(c["tvl_usd"], 1.0), reverse=True)
    shortlist = candidates[: max(config.top_n * 2, config.top_n)]

    # 3. Enrich top candidates with CLMM pool-info (fee %, bin/tick, price, mints).
    async def _enrich(c: dict) -> dict | None:
        try:
            info = await client.gateway_clmm.get_pool_info(
                connector=c["dex"], network=CLMM_NETWORK, pool_address=c["pool_address"]
            )
        except Exception as e:
            logger.info(f"lp_scanner: pool-info failed for {c['dex']} {c['pool_address']}: {e}")
            return None
        fee_pct = _num(info.get("fee_pct") or info.get("base_fee_percentage"))
        if fee_pct <= 0:
            return None
        # Resolve the MEMECOIN mint = the pool side that is NOT the quote asset.
        mints = [
            info.get("base_token_address"), info.get("quote_token_address"),
            c.get("gecko_base_mint"), c.get("gecko_quote_mint"),
        ]
        mints = [m for m in mints if m]
        memecoin_mint = next((m for m in mints if m != quote_mint), "")
        if not memecoin_mint:
            memecoin_mint = c.get("gecko_base_mint") or (mints[0] if mints else "")
        # Diversification: drop tokens already held.
        if memecoin_mint and memecoin_mint in excl_mints:
            return None
        c["fee_pct"] = fee_pct
        c["bin_step_or_tick"] = info.get("bin_step") or info.get("tick_spacing")
        price = info.get("price")
        if price is not None:
            c["price"] = _num(price)
        c["base_mint"] = memecoin_mint
        c["mint_pair"] = f"{memecoin_mint}-{quote}" if memecoin_mint else c["trading_pair"]
        c["fee_yield"] = (c["vol_window"] * (fee_pct / 100.0)) / max(c["tvl_usd"], 1.0)
        c["lp_provider"] = f"{c['dex']}/clmm"
        return c

    enriched = [r for r in await asyncio.gather(*[_enrich(c) for c in shortlist]) if r]
    if not enriched:
        return (f"lp_scanner: found {len(candidates)} {quote} pools but none usable after "
                f"pool-info + excludes ({len(excl_mints)} mints held).")

    # 4. Final rank by fee yield.
    enriched.sort(key=lambda c: c["fee_yield"], reverse=True)
    ranked = enriched[: config.top_n]

    rows = []
    for i, c in enumerate(ranked, 1):
        rows.append({
            "#": i,
            "Pair": c["trading_pair"],
            "MintPair": c.get("mint_pair"),
            "BaseMint": c.get("base_mint"),
            "Venue": c["dex"],
            "lp_provider": c["lp_provider"],
            "Pool": c["pool_address"],
            "TVL": f"${c['tvl_usd']:,.0f}",
            f"Vol({config.ranking_window})": f"${c['vol_window']:,.0f}",
            "Fee%": f"{c['fee_pct']:.3f}",
            "FeeYield": f"{c['fee_yield'] * 100:.3f}%",
            "Bin/Tick": c.get("bin_step_or_tick"),
            "Price": f"{c.get('price', c['price_usd']):.6g}",
        })

    columns = ["#", "Pair", "MintPair", "BaseMint", "Venue", "lp_provider", "Pool", "TVL",
               f"Vol({config.ranking_window})", "Fee%", "FeeYield", "Bin/Tick", "Price"]

    try:
        from condor.reports import ReportBuilder
        builder = ReportBuilder(f"LP Scanner — {quote}-quoted CLMM yield ranking")
        builder.source("routine", "lp_scanner").tags(["lp", "solana", "clmm", quote.lower()])
        builder.kpi("Candidates", str(len(candidates)))
        builder.kpi("Ranked", str(len(ranked)))
        builder.kpi("Top FeeYield", rows[0]["FeeYield"] if rows else "-")
        builder.kpi("Window", config.ranking_window)
        builder.markdown(
            f"Fee yield = fees(**{config.ranking_window}**)/TVL (fees ≈ vol × fee%). "
            f"Venues: {', '.join(venues)}. Quote: **{quote}**. Min TVL: ${config.min_tvl_usd:,.0f}. "
            f"Excluded {len(excl_pools)} pools / {len(excl_mints)} held mints. "
            f"Use **MintPair** for the entry swap and lp_executor trading_pair."
        )
        builder.table(rows, columns)
        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.warning(f"lp_scanner: report generation failed: {e}")

    summary = (f"Ranked {len(ranked)} {quote} CLMM pools by fee yield "
               f"(from {len(candidates)} candidates). Top: {rows[0]['Pair']} @ "
               f"{rows[0]['Venue']} ({rows[0]['FeeYield']} yield). Use MintPair for swap + lp_executor.")

    try:
        from routines.base import RoutineResult
        return RoutineResult(text=summary, table_data=rows, table_columns=columns)
    except Exception:
        lines = [summary, ""]
        for r in rows:
            lines.append(f"{r['#']}. {r['Pair']} @ {r['Venue']} | yield {r['FeeYield']} | "
                         f"TVL {r['TVL']} | {r['lp_provider']} | pool {r['Pool']} | mintpair {r['MintPair']}")
        return "\n".join(lines)
