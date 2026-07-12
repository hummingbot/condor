"""Agent-local routine: scan Solana pools for revival candidates, return structured data."""

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

CATEGORY = "Market Data"

logger = logging.getLogger(__name__)

GT_BASE = "https://api.geckoterminal.com/api/v2"
GT_HEADERS = {"Accept": "application/json;version=20230302"}


class Config(BaseModel):
    """Scan Solana pools for revival candidates and return structured candidate data."""

    pages: int = Field(default=5, description="Pages of top Solana pools to scan (20 pools/page)")
    min_age_days: int = Field(default=40, description="Minimum pool age in days")
    min_price_change_pct: float = Field(default=20.0, description="Minimum 24h price change %")
    min_volume_spike: float = Field(default=10.0, description="Minimum 24h vol / 30d avg vol ratio")
    min_liquidity_usd: float = Field(default=5000.0, description="Minimum pool liquidity in USD")


async def _get(session: aiohttp.ClientSession, url: str) -> dict:
    async with session.get(url, headers=GT_HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        resp.raise_for_status()
        return await resp.json()


def parse_float(val) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


async def fetch_all_sources(session: aiohttp.ClientSession, pages: int) -> list[dict]:
    """Fetch top pools by 24h volume (multiple pages) + trending pools, deduped by address."""
    pools_by_address: dict[str, dict] = {}

    for page in range(1, pages + 1):
        try:
            url = f"{GT_BASE}/networks/solana/pools?page={page}&sort=h24_volume_usd_desc"
            data = await _get(session, url)
            page_pools = data.get("data", [])
            if not page_pools:
                break
            for pool in page_pools:
                addr = pool.get("attributes", {}).get("address", "")
                if addr:
                    pools_by_address[addr] = pool
            await asyncio.sleep(0.3)
        except Exception as e:
            logger.warning(f"Top pools page {page} failed: {e}")

    try:
        url = f"{GT_BASE}/networks/solana/trending_pools"
        data = await _get(session, url)
        for pool in data.get("data", []):
            addr = pool.get("attributes", {}).get("address", "")
            if addr and addr not in pools_by_address:
                pools_by_address[addr] = pool
        await asyncio.sleep(0.2)
    except Exception as e:
        logger.warning(f"Trending pools fetch failed: {e}")

    return list(pools_by_address.values())


async def fetch_ohlcv_daily(
    session: aiohttp.ClientSession, address: str, semaphore: asyncio.Semaphore, limit: int = 45
) -> list:
    """Fetch daily OHLCV (USD) for a pool — used to infer age and volume history."""
    async with semaphore:
        url = f"{GT_BASE}/networks/solana/pools/{address}/ohlcv/day?limit={limit}&currency=usd"
        try:
            data = await _get(session, url)
            ohlcv = data.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
            await asyncio.sleep(0.1)
            return ohlcv
        except Exception as e:
            logger.debug(f"OHLCV fetch failed for {address}: {e}")
            return []


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE):
    now_ts = datetime.now(timezone.utc).timestamp()
    timestamp = datetime.now(timezone.utc).isoformat()

    # Step 1: Fetch top + trending Solana pools
    async with aiohttp.ClientSession() as session:
        all_pools = await fetch_all_sources(session, config.pages)

    total_scanned = len(all_pools)
    if total_scanned == 0:
        return {"candidates": [], "scanned": 0, "timestamp": timestamp, "error": "Failed to fetch pools from GeckoTerminal"}

    # Step 2: First-pass filter — price change + liquidity (fast, no extra API calls)
    pre_candidates = []
    for pool in all_pools:
        attrs = pool.get("attributes", {})
        address = attrs.get("address", "")
        if not address:
            continue

        price_chg = parse_float(attrs.get("price_change_percentage", {}).get("h24"))
        if price_chg < config.min_price_change_pct:
            continue

        liquidity = parse_float(attrs.get("reserve_in_usd"))
        if liquidity < config.min_liquidity_usd:
            continue

        vol_24h = parse_float(attrs.get("volume_usd", {}).get("h24"))
        if vol_24h <= 0:
            continue

        # Quick age hint from pool_created_at if available; OHLCV confirms in step 3
        created_at_str = attrs.get("pool_created_at") or ""
        if created_at_str:
            try:
                dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                quick_age = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
                if quick_age < config.min_age_days:
                    continue
            except Exception:
                pass

        pre_candidates.append({
            "attrs": attrs,
            "address": address,
            "price_chg_24h": price_chg,
            "vol_24h": vol_24h,
            "liquidity": liquidity,
            "name": attrs.get("name", address[:12]),
            "current_price": parse_float(attrs.get("base_token_price_usd")),
        })

    if not pre_candidates:
        return {"candidates": [], "scanned": total_scanned, "timestamp": timestamp}

    # Step 3: Fetch OHLCV — verify age, vol spike, ATH exclusion
    ohlcv_limit = max(config.min_age_days + 5, 45)
    semaphore = asyncio.Semaphore(4)
    ath_buffer_pct = 15.0  # excluded if within 15% of 30d high (still in initial pump)

    async with aiohttp.ClientSession() as session:
        tasks = [
            fetch_ohlcv_daily(session, c["address"], semaphore, limit=ohlcv_limit)
            for c in pre_candidates
        ]
        ohlcv_results = await asyncio.gather(*tasks, return_exceptions=True)

    candidates = []
    for cand, ohlcv in zip(pre_candidates, ohlcv_results):
        if isinstance(ohlcv, Exception) or not ohlcv:
            continue

        sorted_candles = sorted(ohlcv, key=lambda c: c[0])

        # Infer age from oldest candle timestamp
        oldest_ts = sorted_candles[0][0]
        inferred_age_days = (now_ts - oldest_ts) / 86400
        if inferred_age_days < config.min_age_days:
            continue

        if len(sorted_candles) < 5:
            continue

        # Avg daily vol over historical candles (exclude most recent partial day)
        historical_vols = [float(c[5]) for c in sorted_candles[:-1] if float(c[5]) > 0]
        if not historical_vols:
            continue
        avg_vol_30d = sum(historical_vols) / len(historical_vols)
        if avg_vol_30d <= 0:
            continue

        vol_spike = cand["vol_24h"] / avg_vol_30d
        if vol_spike < config.min_volume_spike:
            continue

        # ATH exclusion: skip pools still near their 30d high (initial pump, not revival)
        highs = [float(c[2]) for c in sorted_candles if float(c[2]) > 0]
        ath = max(highs) if highs else 0.0
        current_price = cand["current_price"]
        pct_below_30d_high = None
        if ath > 0 and current_price > 0:
            pct_below_30d_high = ((ath - current_price) / ath) * 100
            if pct_below_30d_high < ath_buffer_pct:
                continue

        candidates.append({
            "pool_address": cand["address"],
            "token_name": cand["name"],
            "price_change_24h_pct": round(cand["price_chg_24h"], 2),
            "volume_spike_x": round(vol_spike, 2),
            "volume_24h_usd": round(cand["vol_24h"], 2),
            "avg_30d_volume_usd": round(avg_vol_30d, 2),
            "pool_age_days": round(inferred_age_days, 1),
            "liquidity_usd": round(cand["liquidity"], 2),
            "pct_below_30d_high": round(pct_below_30d_high, 2) if pct_below_30d_high is not None else None,
        })

    candidates.sort(key=lambda x: x["volume_spike_x"], reverse=True)

    return {
        "candidates": candidates,
        "scanned": total_scanned,
        "timestamp": timestamp,
    }
