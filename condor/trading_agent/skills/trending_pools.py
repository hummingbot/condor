"""Core skill: fetch trending Solana tokens from GeckoTerminal.

Fetches top 20 trending pools on first tick, extracts unique base token
addresses (any DEX), then caches the result. The agent uses these tokens
with explore_dex_pools to find the best Meteora pools by fee/TVL ratio.
"""

from __future__ import annotations

import logging
from typing import Any

from . import register_skill
from .base import BaseSkill, SkillResult

log = logging.getLogger(__name__)

# Wrapped SOL address on Solana
WRAPPED_SOL = "So11111111111111111111111111111111111111112"


def _safe_float(val: Any) -> float:
    """Safely convert a value to float, returning 0.0 on failure."""
    try:
        return float(val) if val else 0.0
    except (ValueError, TypeError):
        return 0.0


def _extract_pool_summary(pool: dict) -> dict:
    """Extract key fields from a GeckoTerminal pool record.

    Handles both nested API response format and flattened DataFrame rows.
    """
    attrs = pool.get("attributes", pool)
    relationships = pool.get("relationships", {})

    name = attrs.get("name", "")
    base_symbol = attrs.get("base_token_symbol", "")
    quote_symbol = attrs.get("quote_token_symbol", "")

    # Parse symbols from name if missing (e.g. "PENGU / SOL")
    if (not base_symbol or not quote_symbol) and "/" in str(name):
        parts = str(name).split("/")
        if len(parts) == 2:
            base_symbol = base_symbol or parts[0].strip()
            quote_symbol = quote_symbol or parts[1].strip()

    # Token addresses from relationships or direct fields
    base_token_id = attrs.get("base_token_id", "")
    quote_token_id = attrs.get("quote_token_id", "")
    if not base_token_id:
        try:
            base_token_id = relationships.get("base_token", {}).get("data", {}).get("id", "")
        except (AttributeError, TypeError):
            pass
    if not quote_token_id:
        try:
            quote_token_id = relationships.get("quote_token", {}).get("data", {}).get("id", "")
        except (AttributeError, TypeError):
            pass

    def _parse_addr(token_id: str) -> str:
        if not token_id:
            return ""
        # Format: "solana_<address>" -> extract address
        parts = token_id.split("_", 1)
        return parts[1] if len(parts) > 1 else token_id

    base_token_address = attrs.get("base_token_address", "") or _parse_addr(base_token_id)
    quote_token_address = attrs.get("quote_token_address", "") or _parse_addr(quote_token_id)

    # DEX name from relationships or direct field
    dex_id = attrs.get("dex_id", "")
    if not dex_id:
        try:
            dex_id = relationships.get("dex", {}).get("data", {}).get("id", "")
        except (AttributeError, TypeError):
            pass

    # Pool address
    pool_address = attrs.get("address", pool.get("id", ""))
    if "solana_" in str(pool_address):
        pool_address = str(pool_address).split("_", 1)[-1]
    elif "solana_" in str(pool.get("id", "")):
        pool_address = str(pool.get("id", "")).split("_", 1)[-1]

    return {
        "name": name or f"{base_symbol}/{quote_symbol}",
        "base_symbol": base_symbol,
        "quote_symbol": quote_symbol,
        "base_token_address": base_token_address,
        "quote_token_address": quote_token_address,
        "pool_address": pool_address,
        "dex": dex_id,
        "price_usd": attrs.get("base_token_price_usd", ""),
        "volume_24h": attrs.get("volume_usd", {}).get("h24", "") if isinstance(attrs.get("volume_usd"), dict) else attrs.get("volume_usd_h24", ""),
        "tvl": attrs.get("reserve_in_usd", attrs.get("tvl", "")),
        "price_change_24h": attrs.get("price_change_percentage", {}).get("h24", "") if isinstance(attrs.get("price_change_percentage"), dict) else attrs.get("price_change_percentage_h24", ""),
    }


class TrendingPoolsSkill(BaseSkill):
    name = "trending_pools"
    is_core = True

    # Cache: fetch trending tokens once at startup, reuse on subsequent ticks
    _cached_result: SkillResult | None = None

    async def execute(self, client: Any, config: dict, agent_id: str = "") -> SkillResult:
        if self._cached_result is not None:
            return self._cached_result

        try:
            from geckoterminal_py import GeckoTerminalAsyncClient

            gecko = GeckoTerminalAsyncClient()
            result = await gecko.get_trending_pools_by_network("solana")

            # Extract pools from response (handles DataFrame and dict formats)
            pools: list = []
            try:
                import pandas as pd
                if isinstance(result, pd.DataFrame):
                    pools = result.to_dict("records")
            except ImportError:
                pass

            if not pools:
                if isinstance(result, list):
                    pools = result
                elif isinstance(result, dict):
                    pools = result.get("data", [])
                elif hasattr(result, "data"):
                    data = result.data
                    pools = data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []

            pools = pools[:20]

            if not pools:
                skill_result = SkillResult(
                    name=self.name,
                    data={"tokens": [], "pools": []},
                    summary="Trending Solana Tokens: no data from GeckoTerminal",
                )
                self._cached_result = skill_result
                return skill_result

            summaries = [_extract_pool_summary(p) for p in pools]

            # Extract unique tokens (dedupe by address, exclude SOL/stablecoins)
            seen_addresses: set[str] = set()
            tokens: list[dict] = []
            stablecoins = {"USDC", "USDT", "BUSD", "DAI", "UST", "USDH", "UXD"}

            for p in summaries:
                # Get the non-SOL token from the pair
                if p["quote_symbol"].upper() == "SOL" or p["quote_token_address"] == WRAPPED_SOL:
                    token_symbol = p["base_symbol"]
                    token_address = p["base_token_address"]
                elif p["base_symbol"].upper() == "SOL" or p["base_token_address"] == WRAPPED_SOL:
                    token_symbol = p["quote_symbol"]
                    token_address = p["quote_token_address"]
                else:
                    # Neither is SOL, take base token
                    token_symbol = p["base_symbol"]
                    token_address = p["base_token_address"]

                # Skip if already seen, is SOL, or is a stablecoin
                if not token_address or token_address in seen_addresses:
                    continue
                if token_address == WRAPPED_SOL:
                    continue
                if token_symbol.upper() in stablecoins:
                    continue

                seen_addresses.add(token_address)
                tokens.append({
                    "symbol": token_symbol,
                    "address": token_address,
                    "source_pool": p["name"],
                    "source_dex": p["dex"],
                    "tvl": p["tvl"],
                    "volume_24h": p["volume_24h"],
                    "price_change_24h": p["price_change_24h"],
                })

            # Build summary
            lines = [f"Trending Solana Tokens ({len(tokens)} unique from top 20 pools):"]
            for i, t in enumerate(tokens, 1):
                vol = t["volume_24h"]
                tvl = t["tvl"]
                change = t["price_change_24h"]
                vol_str = f"${_safe_float(vol):,.0f}" if vol else "N/A"
                tvl_str = f"${_safe_float(tvl):,.0f}" if tvl else "N/A"
                change_str = f"{_safe_float(change):+.1f}%" if change else "N/A"
                lines.append(
                    f"  {i}. {t['symbol']} — from {t['source_dex']}, "
                    f"Vol: {vol_str}, TVL: {tvl_str}, 24h: {change_str}"
                )
                lines.append(f"     Address: {t['address']}")

            if not tokens:
                lines.append("  (no trending tokens found)")

            lines.append("")
            lines.append("Use explore_dex_pools(connector='meteora', search_term='{SYMBOL}', sort_key='feetvlratio') to find best Meteora pools.")

            skill_result = SkillResult(
                name=self.name,
                data={"tokens": tokens, "pools": summaries},
                summary="\n".join(lines),
            )
            self._cached_result = skill_result
            return skill_result

        except Exception as e:
            log.exception("TrendingPoolsSkill failed")
            return SkillResult(
                name=self.name,
                data={"error": str(e)},
                summary=f"Trending Solana Tokens: failed ({e})",
            )


register_skill(TrendingPoolsSkill())
