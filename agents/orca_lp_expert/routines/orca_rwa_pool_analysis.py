"""Orca RWA Pool Analysis — scans Orca CLMM for tokenized real-world asset pools
and ranks them by fee yield.

Uses https://api.orca.so/v2/solana directly (same base as condor/orca_api.py).
Cloudflare-fronted: a custom User-Agent header is required or the request is 403'd.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.reports import ReportBuilder
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

_BASE_URL = "https://api.orca.so/v2/solana"
_USER_AGENT = "condor-dex-browser/1.0 (+https://github.com/hummingbot)"
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class Config(BaseModel):
    """Orca RWA Pool Analysis — scans Orca CLMM for tokenized real-world asset pools and ranks them by fee yield."""

    min_tvl: float = Field(
        default=1000.0, description="Minimum TVL in USD to include a pool"
    )
    include_symbols: str = Field(
        default="SPCX,GME,SILV,TSLA,AAPL,NVDA,COIN,MSFT",
        description="Comma-separated RWA token symbols to search",
    )
    top_n: int = Field(default=10, description="Number of top pools to show in summary")


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


async def _search_symbol(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, symbol: str
) -> list[dict]:
    """Search Orca for pools containing a given RWA symbol and filter to close matches."""
    async with sem:
        try:
            r = await client.get("/pools/search", params={"q": symbol, "stats": "24h"})
            r.raise_for_status()
            body = r.json()
            rows = (
                body.get("data", [])
                if isinstance(body, dict)
                else (body if isinstance(body, list) else [])
            )
            sym_upper = symbol.upper()
            filtered = []
            for p in rows:
                if not isinstance(p, dict):
                    continue
                sym_a = ((p.get("tokenA") or {}).get("symbol") or "").upper()
                sym_b = ((p.get("tokenB") or {}).get("symbol") or "").upper()
                # Accept only when a token symbol starts with the searched RWA symbol.
                # startswith avoids false positives like "COIN" matching "BITCOIN".
                if sym_a.startswith(sym_upper) or sym_b.startswith(sym_upper):
                    filtered.append(p)
            return filtered
        except Exception as exc:
            logger.warning("Orca search for %s failed: %s", symbol, exc)
            return []


def _parse_pool(pool: dict) -> dict | None:
    """Extract and compute metrics from a raw Orca whirlpool row.

    Returns None if the pool has no TVL or fails to parse.
    """
    try:
        address = pool.get("address") or ""
        sym_a = (pool.get("tokenA") or {}).get("symbol") or "?"
        sym_b = (pool.get("tokenB") or {}).get("symbol") or "?"
        fee_rate_raw = float(pool.get("feeRate") or 0)
        fee_tier = fee_rate_raw / 10000  # e.g. 1600 → 0.16 (percent)
        tick_spacing = int(pool.get("tickSpacing") or 0)
        tvl = float(pool.get("tvlUsdc") or 0)
        if tvl <= 0:
            return None
        stats_24h = (pool.get("stats") or {}).get("24h") or {}
        volume_24h = float(stats_24h.get("volume") or 0)
        fees_24h = float(stats_24h.get("fees") or 0)

        fee_tvl_ratio = fees_24h / tvl * 100 if tvl > 0 else 0.0
        vol_tvl_ratio = volume_24h / tvl if tvl > 0 else 0.0
        score = fee_tvl_ratio * 0.6 + vol_tvl_ratio * 0.4

        addr_short = (
            f"{address[:6]}...{address[-4:]}" if len(address) >= 10 else address
        )

        return {
            "address": address,
            "address_short": addr_short,
            "token_a": sym_a,
            "token_b": sym_b,
            "pool_label": f"{sym_a}-{sym_b} ({addr_short})",
            "fee_tier": round(fee_tier, 4),
            "tick_spacing": tick_spacing,
            "tvl": round(tvl, 2),
            "volume_24h": round(volume_24h, 2),
            "fees_24h": round(fees_24h, 4),
            "fee_tvl_ratio": round(fee_tvl_ratio, 6),
            "vol_tvl_ratio": round(vol_tvl_ratio, 6),
            "score": round(score, 6),
        }
    except Exception as exc:
        logger.warning("Failed to parse pool %s: %s", pool.get("address", "?"), exc)
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> RoutineResult:
    symbols = [
        s.strip().upper() for s in config.include_symbols.split(",") if s.strip()
    ]

    # --- Fetch: parallel per-symbol searches with rate-limiting ---
    async with httpx.AsyncClient(
        base_url=_BASE_URL,
        timeout=_TIMEOUT,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    ) as http:
        sem = asyncio.Semaphore(5)
        batches = await asyncio.gather(
            *[_search_symbol(http, sem, sym) for sym in symbols]
        )

    # --- Deduplicate by pool address ---
    seen: set[str] = set()
    raw_pools: list[dict] = []
    for batch in batches:
        for p in batch:
            addr = p.get("address") or ""
            if addr and addr not in seen:
                seen.add(addr)
                raw_pools.append(p)

    # --- Parse + filter by min_tvl ---
    pools: list[dict] = []
    for p in raw_pools:
        m = _parse_pool(p)
        if m and m["tvl"] >= config.min_tvl:
            pools.append(m)

    if not pools:
        return RoutineResult(
            text=f"No Orca RWA pools found above ${config.min_tvl:,.0f} TVL for symbols: {', '.join(symbols)}"
        )

    # Sort descending by score
    pools.sort(key=lambda x: x["score"], reverse=True)

    # --- Aggregate KPIs ---
    total_tvl = sum(p["tvl"] for p in pools)
    total_vol = sum(p["volume_24h"] for p in pools)
    best_fee_tvl = max(p["fee_tvl_ratio"] for p in pools)

    # --- Risk flags (computed before report) ---
    thin = [p for p in pools if p["tvl"] < 10_000]
    wrong_fee = [p for p in pools if p["fee_tier"] < 0.1]
    stagnant = [p for p in pools if p["vol_tvl_ratio"] < 0.05]

    risk_lines = ["## Risk Flags\n"]
    if thin:
        risk_lines.append("### Thin Liquidity (TVL < $10,000)")
        for p in thin:
            risk_lines.append(
                f"- **{p['token_a']}/{p['token_b']}** (`{p['address_short']}`): TVL = ${p['tvl']:,.2f}"
            )
    if wrong_fee:
        risk_lines.append("\n### Low Fee Tier for RWA (fee < 0.1%)")
        for p in wrong_fee:
            risk_lines.append(
                f"- **{p['token_a']}/{p['token_b']}** (`{p['address_short']}`): fee = {p['fee_tier']:.4f}%"
            )
    if stagnant:
        risk_lines.append("\n### Stagnant Pools (vol/TVL < 0.05)")
        for p in stagnant:
            risk_lines.append(
                f"- **{p['token_a']}/{p['token_b']}** (`{p['address_short']}`): vol/TVL = {p['vol_tvl_ratio']:.4f}"
            )
    if not thin and not wrong_fee and not stagnant:
        risk_lines.append("_No risk flags._")

    # --- Build report ---
    builder = ReportBuilder("Orca RWA Pool Analysis")
    builder.source("routine", "orca_rwa_pool_analysis")
    builder.tags(["orca", "rwa", "lp", "tokenized-assets"])

    builder.section("PROTOCOL OVERVIEW")
    builder.kpi("Total RWA Pools Found", str(len(pools)))
    builder.kpi("Total TVL", f"${total_tvl:,.2f}")
    builder.kpi("Total 24h Volume", f"${total_vol:,.2f}")
    builder.kpi("Best Fee/TVL Ratio", f"{best_fee_tvl:.4f}%")

    builder.section("RWA POOL RANKINGS")
    builder.dataset("pools", pools)
    builder.data_table(
        "pools",
        title="All RWA Pools",
        columns=[
            "token_a",
            "token_b",
            "fee_tier",
            "tick_spacing",
            "tvl",
            "volume_24h",
            "fees_24h",
            "fee_tvl_ratio",
            "vol_tvl_ratio",
            "score",
            "address_short",
        ],
    )

    builder.section("POOL YIELD CHART")
    builder.chart(
        "bar",
        "Fee Yield by Pool (fee/TVL %)",
        "pools",
        "pool_label",
        "fee_tvl_ratio",
        x_label="Pool",
        y_label="Fee/TVL Ratio (%)",
    )

    builder.section("RISK FLAGS")
    builder.markdown("\n".join(risk_lines))

    builder.manual_order()
    await builder.save()

    # --- Inline summary: top N ---
    top = pools[: config.top_n]
    table_data = [
        {
            "Pool": f"{p['token_a']}/{p['token_b']}",
            "Fee%": f"{p['fee_tier']:.4f}%",
            "TVL": f"${p['tvl']:,.2f}",
            "Vol 24h": f"${p['volume_24h']:,.2f}",
            "Fee/TVL%": f"{p['fee_tvl_ratio']:.4f}%",
            "Score": f"{p['score']:.4f}",
        }
        for p in top
    ]

    summary = (
        f"Found {len(pools)} Orca RWA pools across {len(symbols)} symbols — "
        f"best Fee/TVL: {best_fee_tvl:.4f}% | Total TVL: ${total_tvl:,.2f}"
    )

    return RoutineResult(
        text=summary,
        table_data=table_data,
        table_columns=["Pool", "Fee%", "TVL", "Vol 24h", "Fee/TVL%", "Score"],
    )
