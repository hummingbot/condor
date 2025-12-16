"""
Pool Data Utilities

Provides unified data fetching for DEX pools:
- OHLCV data via GeckoTerminal (works for any pool on any DEX)
- Liquidity/bin data via Gateway CLMM (for supported DEXes)
- Pool info normalization across different sources
"""

import logging
from typing import Optional, Dict, Any, List, Tuple

from geckoterminal_py import GeckoTerminalAsyncClient

from servers import get_client
from ._shared import get_cached, set_cached

logger = logging.getLogger(__name__)

# Supported DEXes for liquidity data (via gateway CLMM)
LIQUIDITY_SUPPORTED_DEXES = {
    "meteora": "solana",
    "raydium": "solana",
    "orca": "solana",
}

# GeckoTerminal network mapping
NETWORK_TO_GECKO = {
    "solana": "solana",
    "solana-mainnet-beta": "solana",
    "ethereum": "eth",
    "ethereum-mainnet": "eth",
    "arbitrum": "arbitrum",
    "arbitrum-one": "arbitrum",
    "base": "base",
    "base-mainnet": "base",
    "bsc": "bsc",
    "binance-smart-chain": "bsc",
    "polygon": "polygon_pos",
    "polygon-mainnet": "polygon_pos",
    "avalanche": "avalanche",
    "optimism": "optimism",
}

# DEX ID to GeckoTerminal DEX mapping
DEX_TO_GECKO = {
    "meteora": "meteora",
    "raydium": "raydium",
    "orca": "orca",
    "uniswap": "uniswap",
    "uniswap_v3": "uniswap_v3",
    "sushiswap": "sushiswap",
}

# Cache TTLs
OHLCV_CACHE_TTL = 300  # 5 minutes
BINS_CACHE_TTL = 60  # 1 minute


def get_gecko_network(network: str) -> str:
    """Convert internal network name to GeckoTerminal network ID"""
    return NETWORK_TO_GECKO.get(network, network)


def can_fetch_liquidity(dex_id: str, network: str = None) -> bool:
    """Check if liquidity/bin data can be fetched for this DEX

    Args:
        dex_id: DEX identifier (e.g., "meteora", "raydium")
        network: Optional network to verify (must be Solana for now)

    Returns:
        True if liquidity data is available via gateway CLMM
    """
    dex_lower = dex_id.lower() if dex_id else ""

    if dex_lower not in LIQUIDITY_SUPPORTED_DEXES:
        return False

    if network:
        expected_network = LIQUIDITY_SUPPORTED_DEXES.get(dex_lower)
        gecko_network = get_gecko_network(network)
        if gecko_network != expected_network:
            return False

    return True


def get_connector_for_dex(dex_id: str) -> Optional[str]:
    """Get the gateway connector name for a DEX ID

    Args:
        dex_id: DEX identifier from GeckoTerminal

    Returns:
        Connector name for gateway CLMM or None
    """
    dex_lower = dex_id.lower() if dex_id else ""

    # Direct mapping
    if dex_lower in LIQUIDITY_SUPPORTED_DEXES:
        return dex_lower

    # Handle variations
    if "meteora" in dex_lower:
        return "meteora"
    if "raydium" in dex_lower:
        return "raydium"
    if "orca" in dex_lower:
        return "orca"

    return None


async def fetch_ohlcv(
    pool_address: str,
    network: str,
    timeframe: str = "1h",
    currency: str = "usd",
    user_data: dict = None
) -> Tuple[Optional[List], Optional[str]]:
    """Fetch OHLCV data for any pool via GeckoTerminal

    Args:
        pool_address: Pool contract address
        network: Network identifier (will be converted to GeckoTerminal format)
        timeframe: OHLCV timeframe ("1m", "5m", "15m", "1h", "4h", "1d")
        currency: Price currency - "usd" or "token" (quote token)
        user_data: Optional user_data dict for caching

    Returns:
        Tuple of (ohlcv_list, error_message)
        ohlcv_list: List of [timestamp, open, high, low, close, volume] or None
        error_message: Error string if failed, None on success
    """
    try:
        gecko_network = get_gecko_network(network)

        # Check cache
        if user_data is not None:
            cache_key = f"ohlcv_{gecko_network}_{pool_address}_{timeframe}_{currency}"
            cached = get_cached(user_data, cache_key, ttl=OHLCV_CACHE_TTL)
            if cached is not None:
                return cached, None

        client = GeckoTerminalAsyncClient()
        result = await client.get_ohlcv(gecko_network, pool_address, timeframe, currency=currency)

        # Parse response - handle different formats
        ohlcv_list = None

        try:
            import pandas as pd
            if isinstance(result, pd.DataFrame):
                if not result.empty:
                    # Convert DataFrame to list format
                    ohlcv_list = result.values.tolist()
        except ImportError:
            pass

        if ohlcv_list is None:
            if isinstance(result, list):
                ohlcv_list = result
            elif isinstance(result, dict):
                # Try nested structure
                data = result.get("data", result)
                if isinstance(data, dict):
                    attrs = data.get("attributes", data)
                    ohlcv_list = attrs.get("ohlcv_list", [])
                elif isinstance(data, list):
                    ohlcv_list = data

        if not ohlcv_list:
            return None, "No OHLCV data available"

        # Cache result
        if user_data is not None:
            set_cached(user_data, cache_key, ohlcv_list)

        return ohlcv_list, None

    except Exception as e:
        logger.error(f"Error fetching OHLCV: {e}", exc_info=True)
        return None, f"Failed to fetch OHLCV: {str(e)}"


async def fetch_liquidity_bins(
    pool_address: str,
    connector: str = "meteora",
    network: str = "solana-mainnet-beta",
    user_data: dict = None,
    chat_id: int = None
) -> Tuple[Optional[List], Optional[Dict], Optional[str]]:
    """Fetch liquidity bin data for CLMM pools via gateway

    Args:
        pool_address: Pool contract address
        connector: DEX connector (meteora, raydium, orca)
        network: Network identifier
        user_data: Optional user_data dict for caching
        chat_id: Chat ID for per-chat server selection

    Returns:
        Tuple of (bins_list, pool_info, error_message)
        bins_list: List of bin dicts with price, base_token_amount, quote_token_amount
        pool_info: Full pool info dict
        error_message: Error string if failed, None on success
    """
    try:
        if not can_fetch_liquidity(connector):
            return None, None, f"Liquidity data not available for {connector}"

        # Check cache
        cache_key = f"pool_bins_{connector}_{pool_address}"
        if user_data is not None:
            cached = get_cached(user_data, cache_key, ttl=BINS_CACHE_TTL)
            if cached is not None:
                return cached.get('bins'), cached, None

        client = await get_client(chat_id)
        if not client:
            return None, None, "Gateway client not available"

        pool_info = None

        # First try get_pool_info (works for pools known to gateway)
        try:
            pool_info = await client.gateway_clmm.get_pool_info(
                connector=connector,
                network=network,
                pool_address=pool_address
            )
        except Exception as e:
            # If get_pool_info fails (e.g., pool not in gateway config or not a DLMM pool),
            # try finding the pool via get_pools search
            error_str = str(e)
            if "validation error" in error_str.lower() or "Field required" in error_str:
                logger.info(f"Pool {pool_address[:12]}... not found via get_pool_info, trying get_pools search")
                try:
                    # Search for pool by address using get_pools
                    search_result = await client.gateway_clmm.get_pools(
                        connector=connector,
                        search_term=pool_address,
                        limit=1
                    )
                    pools = search_result.get("pools", [])
                    if pools:
                        # Found the pool, but get_pools doesn't include bins
                        # Return pool info without bins - caller can handle this
                        pool_info = pools[0]
                        pool_info['address'] = pool_address
                        logger.info(f"Found pool via get_pools: {pool_info.get('trading_pair', 'Unknown')}")
                    else:
                        # Pool not found in DLMM pools - might be an AMM pool or non-existent
                        logger.info(f"Pool {pool_address[:12]}... not found in {connector} DLMM pools")
                        return None, None, f"Pool not found in {connector} DLMM pools. This may be an AMM pool or not a {connector} pool."
                except Exception as search_e:
                    logger.warning(f"get_pools search also failed: {search_e}")
                    return None, None, f"Could not fetch pool info. Pool may not be a {connector} DLMM pool."

            if pool_info is None:
                # Re-raise with a cleaner message for non-validation errors
                return None, None, f"Failed to fetch pool: {str(e)[:100]}"

        if not pool_info:
            return None, None, "Pool not found"

        bins = pool_info.get('bins', [])

        # Cache result
        if user_data is not None:
            set_cached(user_data, cache_key, pool_info)

        return bins, pool_info, None

    except Exception as e:
        logger.error(f"Error fetching liquidity bins: {e}", exc_info=True)
        return None, None, f"Failed to fetch liquidity: {str(e)}"


def normalize_pool_data(
    pool: dict,
    source: str = "gecko"
) -> Dict[str, Any]:
    """Normalize pool data from different sources to a common format

    Args:
        pool: Raw pool data dict
        source: Data source ("gecko" or "gateway")

    Returns:
        Normalized pool dict with consistent keys
    """
    if source == "gecko":
        # GeckoTerminal format
        attrs = pool.get("attributes", pool)

        return {
            "address": attrs.get("address") or pool.get("id", "").split("_")[-1],
            "name": attrs.get("name", "Unknown"),
            "base_token_symbol": attrs.get("base_token_symbol", "???"),
            "quote_token_symbol": attrs.get("quote_token_symbol", "???"),
            "base_token_price_usd": attrs.get("base_token_price_usd"),
            "quote_token_price_usd": attrs.get("quote_token_price_usd"),
            "network": pool.get("network") or attrs.get("network", "solana"),
            "dex_id": attrs.get("dex_id", "unknown"),
            "reserve_usd": attrs.get("reserve_in_usd"),
            "volume_24h": _get_nested_float(attrs, "volume_usd", "h24"),
            "volume_6h": _get_nested_float(attrs, "volume_usd", "h6"),
            "volume_1h": _get_nested_float(attrs, "volume_usd", "h1"),
            "price_change_24h": _get_nested_float(attrs, "price_change_percentage", "h24"),
            "price_change_6h": _get_nested_float(attrs, "price_change_percentage", "h6"),
            "price_change_1h": _get_nested_float(attrs, "price_change_percentage", "h1"),
            "fdv_usd": attrs.get("fdv_usd"),
            "market_cap_usd": attrs.get("market_cap_usd"),
            "pool_created_at": attrs.get("pool_created_at"),
            "source": "gecko",
        }

    elif source == "gateway":
        # Gateway CLMM format
        return {
            "address": pool.get("pool_address") or pool.get("address", ""),
            "name": pool.get("trading_pair") or pool.get("name", "Unknown"),
            "base_token_symbol": pool.get("base_symbol", "???"),
            "quote_token_symbol": pool.get("quote_symbol", "???"),
            "base_token_price_usd": None,  # Not provided by gateway
            "quote_token_price_usd": None,
            "network": "solana",
            "dex_id": pool.get("connector", "meteora"),
            "reserve_usd": pool.get("liquidity") or pool.get("tvl"),
            "volume_24h": pool.get("volume_24h"),
            "price_change_24h": None,
            "current_price": pool.get("current_price") or pool.get("price"),
            "bin_step": pool.get("bin_step"),
            "apr": pool.get("apr"),
            "apy": pool.get("apy"),
            "base_fee_percentage": pool.get("base_fee_percentage"),
            "mint_x": pool.get("mint_x"),
            "mint_y": pool.get("mint_y"),
            "source": "gateway",
        }

    return pool


def _get_nested_float(data: dict, *keys) -> Optional[float]:
    """Get a nested float value from dict, trying multiple key patterns"""
    # Try nested access
    value = data
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            value = None
            break

    if value is not None:
        try:
            return float(value)
        except (ValueError, TypeError):
            pass

    # Try flattened key with underscore
    flat_key = "_".join(keys)
    value = data.get(flat_key)
    if value is not None:
        try:
            return float(value)
        except (ValueError, TypeError):
            pass

    # Try flattened key with dot
    flat_key = ".".join(keys)
    value = data.get(flat_key)
    if value is not None:
        try:
            return float(value)
        except (ValueError, TypeError):
            pass

    return None


def extract_pair_from_name(name: str) -> Tuple[str, str]:
    """Extract base and quote symbols from pool name

    Args:
        name: Pool name like "SOL/USDC" or "SOL-USDC" or "SOL / USDC"

    Returns:
        Tuple of (base_symbol, quote_symbol)
    """
    if not name:
        return "???", "???"

    # Try different separators
    for sep in ["/", " / ", "-", " - "]:
        if sep in name:
            parts = name.split(sep)
            if len(parts) >= 2:
                return parts[0].strip(), parts[1].strip()

    return name, "???"
