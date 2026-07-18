"""Scan trending Solana memecoins via the GeckoTerminal public API.

Agent-local by design (docs/condor-simple.md: read surfaces stay per-agent;
this client serves ONLY memecoin_trender). Self-contained: the GT client
lives in this file — adapted from mcp_servers/hummingbot_api/tools/
geckoterminal.py — so the routine has no imports beyond stdlib + aiohttp.

Returns JSON candidates that pass hard liquidity/volume floors, with the
momentum fields the agent judges and the exact position-executor config
skeleton for the entry.
"""

import json
import logging
import os
from typing import Any, Optional

import aiohttp
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

# CoinGecko onchain (GeckoTerminal) backend, chosen by the configured key:
#   - Pro key   → pro-api.coingecko.com  + x-cg-pro-api-key
#   - Demo key  → api.coingecko.com      + x-cg-demo-api-key (authenticated quota)
#   - No key    → api.geckoterminal.com  (anonymous, ~30 req/min, throttles fast)
# Onchain route paths are identical across all three; only base URL + header differ.
# CONDOR_COINGECKO_PLAN forces "pro"/"demo" when the CG- key prefix is ambiguous.
GT_PUBLIC_BASE = "https://api.geckoterminal.com/api/v2"
GT_DEMO_BASE = "https://api.coingecko.com/api/v3/onchain"
GT_PRO_BASE = "https://pro-api.coingecko.com/api/v3/onchain"


def _gt_backend() -> tuple[str, dict]:
    key = os.environ.get("COINGECKO_API_KEY", "").strip()
    if not key:
        return GT_PUBLIC_BASE, {"Accept": "application/json;version=20230302"}
    plan = os.environ.get("CONDOR_COINGECKO_PLAN", "demo").strip().lower()
    if plan == "pro":
        return GT_PRO_BASE, {"accept": "application/json", "x-cg-pro-api-key": key}
    return GT_DEMO_BASE, {"accept": "application/json", "x-cg-demo-api-key": key}

# Never trade the plumbing
EXCLUDED_SYMBOLS = {"SOL", "WSOL", "USDC", "USDT", "JITOSOL", "MSOL", "BSOL", "JUPSOL"}

# The floors reject many pools, so gathering `max_candidates` usually spans
# several pages of trending results.
GT_MAX_PAGES = 10


class Config(BaseModel):
    """Scan GeckoTerminal trending pools on Solana for tradeable memecoin momentum."""

    min_liquidity_usd: float = Field(default=200_000, description="Pool reserve floor")
    min_volume_24h_usd: float = Field(default=500_000)
    min_txns_24h: int = Field(default=1000, description="Trade-count floor (anti-wash thinness)")
    max_candidates: int = Field(default=20)
    timeframe: str = Field(default="24h", description="Trending window: 5m | 1h | 6h | 24h")


async def _gt_get(session: aiohttp.ClientSession, path: str, params: Optional[dict] = None) -> dict:
    base, headers = _gt_backend()
    async with session.get(f"{base}/{path}", params=params or {}, headers=headers) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"geckoterminal {path} -> HTTP {resp.status}: {body[:300]}")
        return await resp.json()


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


async def run(config: Config, context=None) -> str:
    candidates: list = []
    seen_mints: set = set()
    rejected = {"excluded_symbol": 0, "liquidity": 0, "volume": 0, "txns": 0, "no_token": 0,
                "duplicate": 0}
    pages_scanned = 0

    async with aiohttp.ClientSession() as session:
        for page in range(1, GT_MAX_PAGES + 1):
            data = await _gt_get(
                session,
                "networks/solana/trending_pools",
                {"include": "base_token", "duration": config.timeframe, "page": page},
            )
            pool_list = data.get("data", [])
            if not pool_list:
                break
            pages_scanned = page

            # id ("solana_<mint>") -> token attributes (per page)
            tokens = {
                item["id"]: item.get("attributes", {})
                for item in data.get("included", [])
                if item.get("type") == "token"
            }

            for pool in pool_list:
                attrs = pool.get("attributes", {})
                token_ref = (((pool.get("relationships") or {}).get("base_token") or {}).get("data") or {})
                token = tokens.get(token_ref.get("id", ""))
                if not token:
                    rejected["no_token"] += 1
                    continue
                symbol = (token.get("symbol") or "").upper()
                if symbol in EXCLUDED_SYMBOLS:
                    rejected["excluded_symbol"] += 1
                    continue
                if token.get("address") in seen_mints:
                    rejected["duplicate"] += 1
                    continue

                liquidity = _f(attrs.get("reserve_in_usd"))
                volume_24h = _f((attrs.get("volume_usd") or {}).get("h24"))
                tx = attrs.get("transactions") or {}
                txns_24h = sum(
                    int((tx.get(k) or {}).get("buys", 0)) + int((tx.get(k) or {}).get("sells", 0))
                    for k in ("h24",)
                )
                if liquidity < config.min_liquidity_usd:
                    rejected["liquidity"] += 1
                    continue
                if volume_24h < config.min_volume_24h_usd:
                    rejected["volume"] += 1
                    continue
                if txns_24h < config.min_txns_24h:
                    rejected["txns"] += 1
                    continue

                changes = attrs.get("price_change_percentage") or {}
                seen_mints.add(token.get("address"))
                candidates.append({
                    "symbol": symbol,
                    "name": token.get("name"),
                    "mint": token.get("address"),
                    "pool_address": attrs.get("address"),
                    "pool_name": attrs.get("name"),
                    "price_usd": _f(attrs.get("base_token_price_usd")),
                    "liquidity_usd": liquidity,
                    "volume_24h_usd": volume_24h,
                    "txns_24h": txns_24h,
                    "price_change_pct": {
                        "m5": _f(changes.get("m5")),
                        "h1": _f(changes.get("h1")),
                        "h6": _f(changes.get("h6")),
                        "h24": _f(changes.get("h24")),
                    },
                })
                if len(candidates) >= config.max_candidates:
                    break
            if len(candidates) >= config.max_candidates:
                break

    return json.dumps({
        "network": "solana",
        "trending_window": config.timeframe,
        "pages_scanned": pages_scanned,
        "candidates": candidates,
        "rejected_counts": rejected,
        "entry_template": {
            "executor_type": "position",
            "config": {
                "chain_network": "solana-mainnet-beta",
                "wallet_address": "<wallet>",
                "base_token": "<candidate mint address — NEVER the symbol>",
                "quote_token": "SOL",
                "amount_quote": "<SOL amount, e.g. 0.02>",
                "take_profit_pct": "<e.g. 0.10>",
                "stop_loss_pct": "<e.g. 0.05>",
                "time_limit_s": "<e.g. 3600>",
                "slippage_pct": "1.0",
            },
        },
    }, indent=2)
