"""Detect fresh EasyA launchpad graduations into Meteora DAMM v2, for early-LP entry.

Agent-local routine for meteora_launch_lp. EasyA-graduated tokens carry a vanity mint suffix
**`EASY`** and land in a **SOL-quoted, 2% static-fee** DAMM v2 pool (no fee scheduler), so graduations
are discoverable directly off the Meteora DAMM v2 data API (https://damm-v2.datapi.meteora.ag/pools)
— NO EasyA API needed. This routine filters the pool feed to `*EASY` base mints, recent `created_at`,
and TVL/volume floors, then ranks by 24h fee yield (traction) so the agent LPs the ones actually
earning rather than every graduation.

It does NOT decide entry — the agent still gates each candidate on sellability (honeypot), real
post-graduation demand, and quality, and must wait for the initial dump to clear before providing
liquidity. See AGENT.md.

Note: early LP here is directional-long the token (you must buy the base token to pair it with SOL),
so size small and capped.
"""
import logging
import time

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

DAMM_V2_API = "https://damm-v2.datapi.meteora.ag/pools"
CONNECTOR = "meteora"
NETWORK = "solana-mainnet-beta"  # hummingbot-api / Gateway network id for manage_amm
SOL_MINT = "So11111111111111111111111111111111111111112"
EASYA_MINT_SUFFIX = "EASY"  # EasyA vanity-suffix marker on graduated token mints


class Config(BaseModel):
    """Detect fresh EasyA graduations into Meteora DAMM v2, ranked by fee yield."""

    max_age_hours: float = Field(default=72.0, description="Only pools created within this many hours")
    min_tvl_usd: float = Field(default=10000.0, description="Minimum pool TVL in USD (graduation liquidity floor)")
    min_vol24h_usd: float = Field(default=3000.0, description="Minimum 24h volume in USD (real post-grad demand)")
    verified_only: bool = Field(default=False, description="Require the graduated token to be verified")
    require_static_fee: bool = Field(default=True, description="Exclude fee-scheduler pools (EasyA grads are static)")
    top_n: int = Field(default=15, description="Number of ranked graduations to return")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    # No created_at sort on the API, so fetch a wide TVL-sorted page and filter locally.
    # EasyA pools graduate with ~$10k+ TVL, so they are within the top page by TVL.
    params = {"page": 1, "page_size": 1000, "sort_by": "tvl:desc", "filter_by": "is_blacklisted=false"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(DAMM_V2_API, params=params, timeout=aiohttp.ClientTimeout(total=25)) as resp:
                if resp.status != 200:
                    return f"easya_graduation_monitor: Meteora DAMM v2 API returned HTTP {resp.status}"
                payload = await resp.json()
    except Exception as e:
        return f"easya_graduation_monitor: failed to reach Meteora DAMM v2 API: {e}"

    now_ms = time.time() * 1000.0
    max_age_ms = config.max_age_hours * 3.6e6
    candidates = []

    for p in payload.get("data", []):
        tx, ty = p.get("token_x", {}), p.get("token_y", {})
        # Identify the EasyA token side (mint suffix EASY, not SOL); quote must be SOL.
        easy_side = None
        for side in (tx, ty):
            addr = side.get("address", "")
            if addr != SOL_MINT and addr.upper().endswith(EASYA_MINT_SUFFIX):
                easy_side = side
        if easy_side is None:
            continue
        mints = {tx.get("address"), ty.get("address")}
        if SOL_MINT not in mints:
            continue  # EasyA graduations are SOL-quoted

        created = p.get("created_at")
        age_h = (now_ms - created) / 3.6e6 if created else None
        if age_h is None or (now_ms - created) > max_age_ms:
            continue
        if float(p.get("tvl") or 0) < config.min_tvl_usd:
            continue
        if float((p.get("volume") or {}).get("24h") or 0) < config.min_vol24h_usd:
            continue
        cfg = p.get("pool_config", {}) or {}
        if config.require_static_fee and (cfg.get("is_fee_scheduler_active") or cfg.get("has_fee_scheduler")):
            continue
        if config.verified_only and not easy_side.get("is_verified"):
            continue

        candidates.append({
            "pool": p.get("address"),
            "pair": p.get("name") or f"{easy_side.get('symbol','?')}-SOL",
            "token_symbol": easy_side.get("symbol") or easy_side.get("address", "")[:6],
            "base_mint": easy_side.get("address"),
            "verified": bool(easy_side.get("is_verified")),
            "base_fee_pct": float(cfg.get("base_fee_pct") or 0),
            "tvl": float(p.get("tvl") or 0),
            "vol24h": float((p.get("volume") or {}).get("24h") or 0),
            "fee_yield24h": float((p.get("fee_tvl_ratio") or {}).get("24h") or 0),
            "age_h": age_h,
            "price": float(p.get("current_price") or 0),
        })

    if not candidates:
        return (f"easya_graduation_monitor: no EasyA graduations in the last {config.max_age_hours:.0f}h "
                f"passed the filters (min TVL ${config.min_tvl_usd:,.0f}, min vol24h ${config.min_vol24h_usd:,.0f}, "
                f"verified_only={config.verified_only}).")

    # Rank by traction: 24h fee yield (fees/TVL) — the ones actually earning.
    candidates.sort(key=lambda c: c["fee_yield24h"], reverse=True)
    ranked = candidates[: config.top_n]

    columns = ["#", "Pair", "Age(h)", "FeeYield", "TVL", "Vol24h", "Verified", "Price", "Pool", "BaseMint"]
    rows = []
    for i, c in enumerate(ranked, 1):
        rows.append({
            "#": i,
            "Pair": c["pair"],
            "Age(h)": f"{c['age_h']:.1f}",
            "FeeYield": f"{c['fee_yield24h'] * 100:.1f}%",
            "TVL": f"${c['tvl']:,.0f}",
            "Vol24h": f"${c['vol24h']:,.0f}",
            "Verified": "yes" if c["verified"] else "no",
            "Price": f"{c['price']:.4g}",
            "Pool": c["pool"],
            "BaseMint": c["base_mint"],
            "quote_mint": SOL_MINT,
        })

    top = rows[0]
    summary = (
        f"Found {len(ranked)} EasyA→DAMM v2 graduations (< {config.max_age_hours:.0f}h) passing filters, "
        f"from the pool feed. Top by fee yield: **{top['Pair']}** — yield {top['FeeYield']}, "
        f"age {top['Age(h)']}h, TVL {top['TVL']}, verified {top['Verified']}, pool `{top['Pool']}`.\n"
        f"GATE each before LPing: sellability (SELL+BUY quote both return), real post-dump demand, quality. "
        f"Then early add_liquidity(connector='meteora', network='{NETWORK}', pool_address=<Pool>) — small, "
        f"capped size (directional-long the token). Journal the position_address."
    )

    try:
        from routines.base import RoutineResult
        return RoutineResult(text=summary, table_data=rows, table_columns=columns)
    except Exception:
        lines = [summary, ""]
        for r in rows:
            lines.append(f"{r['#']}. {r['Pair']} | age {r['Age(h)']}h | yield {r['FeeYield']} | "
                         f"TVL {r['TVL']} | vol {r['Vol24h']} | verified {r['Verified']} | pool {r['Pool']}")
        return "\n".join(lines)
