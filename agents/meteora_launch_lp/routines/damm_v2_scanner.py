"""Scan & rank Meteora DAMM v2 (AMM) pools by fee yield for LP entry.

Agent-local routine for meteora_launch_lp. Sources pools directly from the **Meteora DAMM v2 data API**
(https://damm-v2.datapi.meteora.ag/pools) — NOT GeckoTerminal, which does not cover DAMM v2 AMM
pools well — filters to the configured quote asset, drops launch-fee-scheduler and unverified pools
by default, ranks by the API's native `fee_tvl_ratio` (fees(window)/TVL), and returns a shortlist
with the exact fields to act via `manage_amm` (connector=meteora, network=solana-mainnet-beta,
pool_address, base/quote mints).

Related DAMM v2 endpoints for deeper analysis (not used here, available to the agent):
- OHLCV:            https://docs.meteora.ag/api-reference/damm-v2/pools/ohlcv
- Historical volume: https://docs.meteora.ag/api-reference/damm-v2/pools/historical-volume

The scanner deliberately skips pools with an active fee scheduler (base fee often starts near 99%
and decays — a token-launch trap) unless include_launch_pools is set.
"""
import logging

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

DAMM_V2_API = "https://damm-v2.datapi.meteora.ag/pools"
CONNECTOR = "meteora"
NETWORK = "solana-mainnet-beta"  # hummingbot-api / Gateway network id for manage_amm

_QUOTE_MINTS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}
_WINDOWS = {"1h", "2h", "4h", "12h", "24h"}


class Config(BaseModel):
    """Rank Meteora DAMM v2 pools by fee yield (fees/TVL) for AMM LP entry."""

    quote_asset: str = Field(default="SOL", description="Quote token to require on one side: SOL, USDC, or USDT")
    query: str | None = Field(default=None, description="Optional search (token symbol/name/address), e.g. 'JUP'")
    ranking_window: str = Field(default="24h", description="Fee-yield window: 1h, 2h, 4h, 12h, or 24h")
    top_n: int = Field(default=10, description="Number of ranked pools to return")
    min_tvl_usd: float = Field(default=25000.0, description="Minimum pool TVL in USD")
    include_launch_pools: bool = Field(default=False, description="Include active-fee-scheduler (launch) pools")
    verified_only: bool = Field(default=True, description="Require both tokens verified")
    exclude_pools: list[str] = Field(default=[], description="Pool addresses to exclude (already held)")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    quote = config.quote_asset.upper()
    if quote not in _QUOTE_MINTS:
        return f"damm_v2_scanner: unsupported quote_asset '{quote}' — supported: {', '.join(_QUOTE_MINTS)}."
    quote_mint = _QUOTE_MINTS[quote]

    window = config.ranking_window if config.ranking_window in _WINDOWS else "24h"

    # Fetch by TVL (surfaces REAL liquid pools) then rank by fee yield locally. Sorting the API by
    # fee_tvl_ratio instead floods the page with near-zero-TVL junk pools whose ratio is astronomical.
    params = {
        "page": 1,
        "page_size": 300,
        "sort_by": "tvl:desc",
        "filter_by": "is_blacklisted=false",
    }
    if config.query:
        params["query"] = config.query

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(DAMM_V2_API, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    return f"damm_v2_scanner: Meteora DAMM v2 API returned HTTP {resp.status}"
                payload = await resp.json()
    except Exception as e:
        return f"damm_v2_scanner: failed to reach Meteora DAMM v2 API: {e}"

    pools = payload.get("data", [])
    excl = set(config.exclude_pools)
    candidates = []

    for p in pools:
        tx, ty = p.get("token_x", {}), p.get("token_y", {})
        mints = {tx.get("address"), ty.get("address")}
        if quote_mint not in mints:
            continue  # quote asset must be one side
        if p.get("address") in excl:
            continue
        if float(p.get("tvl") or 0) < config.min_tvl_usd:
            continue
        cfg = p.get("pool_config", {}) or {}
        if not config.include_launch_pools and cfg.get("is_fee_scheduler_active"):
            continue  # skip launch pools whose base fee starts ~99%
        if config.verified_only and not (tx.get("is_verified") and ty.get("is_verified")):
            continue

        # Base = the side that is NOT the quote asset.
        base, quote_tok = (ty, tx) if tx.get("address") == quote_mint else (tx, ty)
        fee_yield = float((p.get("fee_tvl_ratio") or {}).get(window) or 0)
        candidates.append({
            "pool": p.get("address"),
            "pair": p.get("name") or f"{base.get('symbol','?')}-{quote_tok.get('symbol','?')}",
            "base_symbol": base.get("symbol") or base.get("address", "")[:6],
            "quote_symbol": quote_tok.get("symbol") or quote,
            "base_mint": base.get("address"),
            "quote_mint": quote_tok.get("address"),
            "base_fee_pct": float(cfg.get("base_fee_pct") or 0),
            "tvl": float(p.get("tvl") or 0),
            "vol_win": float((p.get("volume") or {}).get(window) or 0),
            "fee_yield": fee_yield,
            "price": float(p.get("current_price") or 0),
        })

    if not candidates:
        return (f"damm_v2_scanner: no {quote}-quoted DAMM v2 pools passed the filters "
                f"(min TVL ${config.min_tvl_usd:,.0f}, verified_only={config.verified_only}, "
                f"launch_pools={config.include_launch_pools}).")

    candidates.sort(key=lambda c: c["fee_yield"], reverse=True)
    ranked = candidates[: config.top_n]

    columns = ["#", "Pair", "FeeYield", "BaseFee", "TVL", f"Vol{window}", "Price", "Pool", "BaseMint"]
    rows = []
    for i, c in enumerate(ranked, 1):
        rows.append({
            "#": i,
            "Pair": c["pair"],
            "FeeYield": f"{c['fee_yield'] * 100:.3f}%",
            "BaseFee": f"{c['base_fee_pct']:.3f}%",
            "TVL": f"${c['tvl']:,.0f}",
            f"Vol{window}": f"${c['vol_win']:,.0f}",
            "Price": f"{c['price']:.4g}",
            "Pool": c["pool"],
            "BaseMint": c["base_mint"],
            # manage_amm hints (constant across rows): connector=meteora, network=solana-mainnet-beta.
            "quote_mint": c["quote_mint"],
        })

    top = rows[0]
    summary = (
        f"Ranked {len(ranked)} {quote}-quoted Meteora DAMM v2 pools by fee yield ({window}) "
        f"from {len(candidates)} candidates. Top: **{top['Pair']}** — yield {top['FeeYield']}, "
        f"base fee {top['BaseFee']}, TVL {top['TVL']}, pool `{top['Pool']}`.\n"
        f"Act via manage_amm(connector='meteora', network='{NETWORK}', pool_address=<Pool>, "
        f"base_token=<BaseMint>, quote_token=<quote_mint>). Launch (fee-scheduler) pools "
        f"{'included' if config.include_launch_pools else 'excluded'}."
    )

    try:
        from routines.base import RoutineResult
        return RoutineResult(text=summary, table_data=rows, table_columns=columns)
    except Exception:
        lines = [summary, ""]
        for r in rows:
            lines.append(f"{r['#']}. {r['Pair']} | yield {r['FeeYield']} | fee {r['BaseFee']} | "
                         f"TVL {r['TVL']} | pool {r['Pool']} | base {r['BaseMint']}")
        return "\n".join(lines)
