"""
GeckoTerminal tools for Hummingbot MCP Server

Provides free, public DEX market data from GeckoTerminal API:
- Network and DEX discovery
- Pool exploration (trending, top, new, by token)
- Pool details and multi-pool lookup
- OHLCV candle data
- Recent trades
"""
import logging
from typing import Any

from hummingbot_mcp.exceptions import ToolError
from hummingbot_mcp.formatters.base import format_currency, format_number, format_timestamp, truncate_address

logger = logging.getLogger("hummingbot-mcp")

BASE_URL = "https://api.geckoterminal.com/api/v2"

OHLCV_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "12h", "1d"]

TIMEFRAME_UNIT_MAP = {"m": "minute", "h": "hour", "d": "day"}


def _parse_timeframe(timeframe: str) -> tuple[str, str]:
    """Parse '1h' into ('hour', '1')."""
    if timeframe not in OHLCV_TIMEFRAMES:
        raise ToolError(f"Unsupported timeframe '{timeframe}'. Use one of: {OHLCV_TIMEFRAMES}")
    period, unit = timeframe[:-1], timeframe[-1]
    return TIMEFRAME_UNIT_MAP[unit], period


def _extract_networks(response: dict) -> list[dict[str, Any]]:
    return [
        {"id": item["id"], "type": item["type"], "name": item["attributes"]["name"],
         "coingecko_asset_platform_id": item["attributes"].get("coingecko_asset_platform_id")}
        for item in response.get("data", [])
    ]


def _extract_dexes(response: dict) -> list[dict[str, Any]]:
    return [
        {"id": item["id"], "type": item["type"], "name": item["attributes"]["name"]}
        for item in response.get("data", [])
    ]


def _extract_pools(response: dict) -> list[dict[str, Any]]:
    data = response.get("data", [])
    if isinstance(data, dict):
        data = [data]

    pools = []
    for item in data:
        attrs = item.get("attributes", {})
        rels = item.get("relationships", {})
        price_change = attrs.get("price_change_percentage", {})
        txns = attrs.get("transactions", {})
        volume = attrs.get("volume_usd", {})

        pool = {
            "id": item.get("id", ""),
            "name": attrs.get("name"),
            "address": attrs.get("address"),
            "base_token_price_usd": attrs.get("base_token_price_usd"),
            "quote_token_price_usd": attrs.get("quote_token_price_usd"),
            "reserve_in_usd": attrs.get("reserve_in_usd"),
            "fdv_usd": attrs.get("fdv_usd"),
            "market_cap_usd": attrs.get("market_cap_usd"),
            "pool_created_at": attrs.get("pool_created_at"),
            "price_change_h1": price_change.get("h1"),
            "price_change_h24": price_change.get("h24"),
            "txns_h1_buys": txns.get("h1", {}).get("buys"),
            "txns_h1_sells": txns.get("h1", {}).get("sells"),
            "txns_h24_buys": txns.get("h24", {}).get("buys"),
            "txns_h24_sells": txns.get("h24", {}).get("sells"),
            "volume_h24": volume.get("h24"),
            "dex_id": rels.get("dex", {}).get("data", {}).get("id"),
            "base_token_id": rels.get("base_token", {}).get("data", {}).get("id"),
            "quote_token_id": rels.get("quote_token", {}).get("data", {}).get("id"),
        }
        # Derive network_id from the compound id (e.g., "solana_0x...")
        parts = pool["id"].split("_", 1)
        if len(parts) > 1:
            pool["network_id"] = parts[0]
        pools.append(pool)

    return pools


def _extract_trades(response: dict) -> list[dict[str, Any]]:
    return [
        {
            "block_timestamp": item["attributes"].get("block_timestamp"),
            "side": item["attributes"].get("kind"),
            "volume_usd": item["attributes"].get("volume_in_usd"),
            "from_token_amount": item["attributes"].get("from_token_amount"),
            "to_token_amount": item["attributes"].get("to_token_amount"),
            "price_from_in_usd": item["attributes"].get("price_from_in_usd"),
            "price_to_in_usd": item["attributes"].get("price_to_in_usd"),
            "tx_hash": item["attributes"].get("tx_hash"),
        }
        for item in response.get("data", [])
    ]


def _extract_ohlcv(response: dict) -> list[dict[str, Any]]:
    ohlcv_list = response.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
    return [
        {"timestamp": row[0], "open": row[1], "high": row[2], "low": row[3], "close": row[4], "volume_usd": row[5]}
        for row in ohlcv_list
    ]


def _extract_token_info(response: dict) -> dict[str, Any]:
    data = response.get("data", {})
    attrs = data.get("attributes", {})
    return {
        "id": data.get("id"),
        "name": attrs.get("name"),
        "symbol": attrs.get("symbol"),
        "address": attrs.get("address"),
        "decimals": attrs.get("decimals"),
        "total_supply": attrs.get("total_supply"),
        "price_usd": attrs.get("price_usd"),
        "fdv_usd": attrs.get("fdv_usd"),
        "market_cap_usd": attrs.get("market_cap_usd"),
        "total_reserve_in_usd": attrs.get("total_reserve_in_usd"),
        "volume_usd_h24": attrs.get("volume_usd", {}).get("h24") if isinstance(attrs.get("volume_usd"), dict) else None,
    }


# ── Formatters ───────────────────────────────────────────────────────────────


def format_networks_table(networks: list[dict]) -> str:
    if not networks:
        return "No networks found."
    header = "id                              | name"
    sep = "-" * 80
    rows = [f"{n['id']:31} | {n['name']}" for n in networks]
    return f"{header}\n{sep}\n" + "\n".join(rows)


def format_dexes_table(dexes: list[dict]) -> str:
    if not dexes:
        return "No DEXes found."
    header = "id                              | name"
    sep = "-" * 80
    rows = [f"{d['id']:31} | {d['name']}" for d in dexes]
    return f"{header}\n{sep}\n" + "\n".join(rows)


def format_pools_table(pools: list[dict]) -> str:
    if not pools:
        return "No pools found."
    header = "name                            | address                                      | price_usd        | reserve_usd      | volume_h24       | chg_h24"
    sep = "-" * 180
    rows = []
    for p in pools:
        name = (p.get("name") or "N/A")[:31]
        addr = p.get("address") or "N/A"
        price = format_currency(p.get("base_token_price_usd"), decimals=4)
        reserve = format_number(p.get("reserve_in_usd"))
        vol = format_number(p.get("volume_h24"))
        chg = f"{float(p['price_change_h24']):.2f}%" if p.get("price_change_h24") is not None else "N/A"
        rows.append(f"{name:31} | {addr:44} | {price:16} | {reserve:16} | {vol:16} | {chg}")
    return f"{header}\n{sep}\n" + "\n".join(rows)


def format_ohlcv_table(candles: list[dict]) -> str:
    if not candles:
        return "No candle data found."
    header = "datetime            | open             | high             | low              | close            | volume_usd"
    sep = "-" * 120
    rows = []
    for c in candles:
        dt = format_timestamp(c["timestamp"], "%Y-%m-%d %H:%M")
        rows.append(
            f"{dt:19} | {format_number(c['open'], 4, False):16} | {format_number(c['high'], 4, False):16} | "
            f"{format_number(c['low'], 4, False):16} | {format_number(c['close'], 4, False):16} | {format_number(c['volume_usd'])}"
        )
    return f"{header}\n{sep}\n" + "\n".join(rows)


def format_trades_table(trades: list[dict]) -> str:
    if not trades:
        return "No trades found."
    header = "time                | side | volume_usd       | from_amount      | to_amount        | tx_hash"
    sep = "-" * 130
    rows = []
    for t in trades:
        dt = format_timestamp(t.get("block_timestamp"), "%Y-%m-%d %H:%M")
        side = (t.get("side") or "N/A").upper()[:4]
        vol = format_number(t.get("volume_usd"))
        from_amt = format_number(t.get("from_token_amount"), 4, False)
        to_amt = format_number(t.get("to_token_amount"), 4, False)
        tx = truncate_address(t.get("tx_hash") or "N/A", 8, 6)
        rows.append(f"{dt:19} | {side:4} | {vol:16} | {from_amt:16} | {to_amt:16} | {tx}")
    return f"{header}\n{sep}\n" + "\n".join(rows)


def format_token_info(token: dict) -> str:
    lines = [
        f"Name: {token.get('name', 'N/A')} ({token.get('symbol', 'N/A')})",
        f"Address: {token.get('address', 'N/A')}",
        f"Price: {format_currency(token.get('price_usd'), decimals=6)}",
        f"Market Cap: {format_number(token.get('market_cap_usd'))}",
        f"FDV: {format_number(token.get('fdv_usd'))}",
        f"Total Reserve: {format_number(token.get('total_reserve_in_usd'))}",
        f"24h Volume: {format_number(token.get('volume_usd_h24'))}",
    ]
    return "\n".join(lines)


# ── Main entry point ─────────────────────────────────────────────────────────


async def explore_geckoterminal(
    action: str,
    network: str | None = None,
    dex_id: str | None = None,
    pool_address: str | None = None,
    pool_addresses: list[str] | None = None,
    token_address: str | None = None,
    timeframe: str = "1h",
    before_timestamp: int | None = None,
    currency: str = "usd",
    token: str = "base",
    limit: int = 1000,
    trade_volume_filter: float | None = None,
) -> dict[str, Any]:
    """Execute a GeckoTerminal API action and return formatted results."""
    import aiohttp

    async with aiohttp.ClientSession() as session:

        async def _get(path: str, params: dict | None = None) -> dict:
            url = f"{BASE_URL}/{path}"
            headers = {"Accept": "application/json;version=20230302"}
            async with session.get(url, headers=headers, params=params) as resp:
                resp.raise_for_status()
                return await resp.json()

        # ── Networks ─────────────────────────────────────────────────
        if action == "networks":
            data = await _get("networks")
            networks = _extract_networks(data)
            return {"formatted_output": f"Available Networks ({len(networks)}):\n\n{format_networks_table(networks)}"}

        # ── DEXes by network ─────────────────────────────────────────
        elif action == "dexes":
            if not network:
                raise ToolError("'network' is required for action='dexes'")
            data = await _get(f"networks/{network}/dexes")
            dexes = _extract_dexes(data)
            return {"formatted_output": f"DEXes on {network} ({len(dexes)}):\n\n{format_dexes_table(dexes)}"}

        # ── Trending pools ───────────────────────────────────────────
        elif action == "trending_pools":
            if network:
                data = await _get(f"networks/{network}/trending_pools")
                title = f"Trending Pools on {network}"
            else:
                data = await _get("networks/trending_pools")
                title = "Trending Pools (All Networks)"
            pools = _extract_pools(data)
            return {"formatted_output": f"{title} ({len(pools)}):\n\n{format_pools_table(pools)}"}

        # ── Top pools ────────────────────────────────────────────────
        elif action == "top_pools":
            if not network:
                raise ToolError("'network' is required for action='top_pools'")
            if dex_id:
                data = await _get(f"networks/{network}/dexes/{dex_id}/pools")
                title = f"Top Pools on {network} / {dex_id}"
            else:
                data = await _get(f"networks/{network}/pools")
                title = f"Top Pools on {network}"
            pools = _extract_pools(data)
            return {"formatted_output": f"{title} ({len(pools)}):\n\n{format_pools_table(pools)}"}

        # ── New pools ────────────────────────────────────────────────
        elif action == "new_pools":
            if network:
                data = await _get(f"networks/{network}/new_pools")
                title = f"New Pools on {network}"
            else:
                data = await _get("networks/new_pools")
                title = "New Pools (All Networks)"
            pools = _extract_pools(data)
            return {"formatted_output": f"{title} ({len(pools)}):\n\n{format_pools_table(pools)}"}

        # ── Pool detail ──────────────────────────────────────────────
        elif action == "pool_detail":
            if not network or not pool_address:
                raise ToolError("'network' and 'pool_address' are required for action='pool_detail'")
            data = await _get(f"networks/{network}/pools/{pool_address}")
            pools = _extract_pools(data)
            if pools:
                p = pools[0]
                lines = [
                    f"Pool: {p.get('name', 'N/A')}",
                    f"Address: {p.get('address', 'N/A')}",
                    f"DEX: {p.get('dex_id', 'N/A')}",
                    f"Base Token Price: {format_currency(p.get('base_token_price_usd'), decimals=6)}",
                    f"Quote Token Price: {format_currency(p.get('quote_token_price_usd'), decimals=6)}",
                    f"Reserve (USD): {format_number(p.get('reserve_in_usd'))}",
                    f"FDV: {format_number(p.get('fdv_usd'))}",
                    f"Market Cap: {format_number(p.get('market_cap_usd'))}",
                    f"24h Volume: {format_number(p.get('volume_h24'))}",
                    f"1h Change: {p.get('price_change_h1', 'N/A')}%",
                    f"24h Change: {p.get('price_change_h24', 'N/A')}%",
                    f"24h Txns: {p.get('txns_h24_buys', 'N/A')} buys / {p.get('txns_h24_sells', 'N/A')} sells",
                    f"Created: {p.get('pool_created_at', 'N/A')}",
                ]
                return {"formatted_output": "\n".join(lines)}
            return {"formatted_output": "Pool not found."}

        # ── Multiple pools ───────────────────────────────────────────
        elif action == "multi_pools":
            if not network or not pool_addresses:
                raise ToolError("'network' and 'pool_addresses' are required for action='multi_pools'")
            addresses_str = ",".join(pool_addresses)
            data = await _get(f"networks/{network}/pools/multi/{addresses_str}")
            pools = _extract_pools(data)
            return {"formatted_output": f"Pools on {network} ({len(pools)}):\n\n{format_pools_table(pools)}"}

        # ── Pools by token ───────────────────────────────────────────
        elif action == "token_pools":
            if not network or not token_address:
                raise ToolError("'network' and 'token_address' are required for action='token_pools'")
            data = await _get(f"networks/{network}/tokens/{token_address}/pools")
            pools = _extract_pools(data)
            return {"formatted_output": f"Top Pools for token on {network} ({len(pools)}):\n\n{format_pools_table(pools)}"}

        # ── Token info ───────────────────────────────────────────────
        elif action == "token_info":
            if not network or not token_address:
                raise ToolError("'network' and 'token_address' are required for action='token_info'")
            data = await _get(f"networks/{network}/tokens/{token_address}")
            token_data = _extract_token_info(data)
            return {"formatted_output": format_token_info(token_data)}

        # ── OHLCV candles ────────────────────────────────────────────
        elif action == "ohlcv":
            if not network or not pool_address:
                raise ToolError("'network' and 'pool_address' are required for action='ohlcv'")
            tf_unit, tf_period = _parse_timeframe(timeframe)
            params: dict[str, Any] = {"aggregate": tf_period, "limit": limit, "currency": currency, "token": token}
            if before_timestamp:
                params["before_timestamp"] = before_timestamp
            data = await _get(f"networks/{network}/pools/{pool_address}/ohlcv/{tf_unit}", params=params)
            candles = _extract_ohlcv(data)
            # Sort by timestamp ascending and deduplicate
            seen: set[int] = set()
            unique = []
            for c in candles:
                if c["timestamp"] not in seen:
                    seen.add(c["timestamp"])
                    unique.append(c)
            unique.sort(key=lambda x: x["timestamp"])
            return {"formatted_output": (
                f"OHLCV for pool {truncate_address(pool_address)} on {network} ({timeframe}, {len(unique)} candles):\n\n"
                f"{format_ohlcv_table(unique)}"
            )}

        # ── Trades ───────────────────────────────────────────────────
        elif action == "trades":
            if not network or not pool_address:
                raise ToolError("'network' and 'pool_address' are required for action='trades'")
            params = {}
            if trade_volume_filter is not None:
                params["trade_volume_in_usd_greater_than"] = trade_volume_filter
            data = await _get(f"networks/{network}/pools/{pool_address}/trades", params=params or None)
            trades = _extract_trades(data)
            return {"formatted_output": (
                f"Recent Trades for pool {truncate_address(pool_address)} on {network} ({len(trades)}):\n\n"
                f"{format_trades_table(trades)}"
            )}

        else:
            raise ToolError(
                f"Unknown action '{action}'. Available actions: networks, dexes, trending_pools, top_pools, "
                f"new_pools, pool_detail, multi_pools, token_pools, token_info, ohlcv, trades"
            )
