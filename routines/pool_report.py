"""Generate a comprehensive DEX pool report with candlestick chart, GeckoTerminal data, and CLMM connector info."""

import asyncio
import io
import logging
from datetime import datetime
from typing import Any

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

GT_BASE = "https://api.geckoterminal.com/api/v2"
GT_HEADERS = {"Accept": "application/json;version=20230302"}

# Map GeckoTerminal network IDs to CLMM gateway network IDs
NETWORK_MAP = {
    "solana": "solana-mainnet-beta",
    "eth": "ethereum-mainnet",
    "bsc": "bsc-mainnet",
    "polygon_pos": "polygon-mainnet",
    "arbitrum": "arbitrum-mainnet",
    "base": "base-mainnet",
}

# Map shorthand intervals to GeckoTerminal (timeframe, aggregate) pairs
TIMEFRAME_MAP = {
    "1m": ("minute", 1),
    "5m": ("minute", 5),
    "15m": ("minute", 15),
    "1h": ("hour", 1),
    "4h": ("hour", 4),
    "12h": ("hour", 12),
    "1d": ("day", 1),
}


class Config(BaseModel):
    """Generate a pool report: candlestick chart, GeckoTerminal pool stats, and optional CLMM connector info."""

    pool_address: str = Field(default="", description="Pool contract address (required)")
    network: str = Field(default="solana", description="GeckoTerminal network ID (solana, eth, bsc, base, ...)")
    timeframe: str = Field(default="1h", description="Candle timeframe (1m, 5m, 15m, 1h, 4h, 1d)")
    candles_limit: int = Field(default=100, description="Number of candles to fetch (max 1000)")
    connector: str = Field(default="", description="Optional CLMM connector for on-chain pool info (meteora, raydium, uniswap)")


async def _fetch_json(session: aiohttp.ClientSession, url: str) -> dict[str, Any]:
    async with session.get(url, headers=GT_HEADERS) as resp:
        resp.raise_for_status()
        return await resp.json()


def _fmt(val, decimals: int = 4) -> str:
    """Format a numeric value with appropriate precision."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return str(val)
    if v == 0:
        return "0"
    abs_v = abs(v)
    if abs_v < 0.0001:
        return f"{v:.8f}"
    if abs_v < 1:
        return f"{v:.{decimals}f}"
    if abs_v < 1000:
        return f"{v:.2f}"
    return f"{v:,.0f}"


def _safe_get(d: dict, *keys, default=None):
    """Nested dict get with fallback."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> RoutineResult | str:
    if not config.pool_address:
        return "pool_address is required"

    client = await get_client(context._chat_id, context=context)
    if not client:
        return "No server available"

    chat_id = context._chat_id if hasattr(context, "_chat_id") else None

    tf_key = config.timeframe.lower()
    if tf_key not in TIMEFRAME_MAP:
        return f"Invalid timeframe '{config.timeframe}'. Use one of: {', '.join(TIMEFRAME_MAP)}"
    gt_timeframe, aggregate = TIMEFRAME_MAP[tf_key]

    pool_url = f"{GT_BASE}/networks/{config.network}/pools/{config.pool_address}?include=base_token,quote_token,dex"
    ohlcv_url = (
        f"{GT_BASE}/networks/{config.network}/pools/{config.pool_address}"
        f"/ohlcv/{gt_timeframe}?aggregate={aggregate}&limit={config.candles_limit}&currency=usd"
    )

    async with aiohttp.ClientSession() as session:
        try:
            pool_data, ohlcv_data = await asyncio.gather(
                _fetch_json(session, pool_url),
                _fetch_json(session, ohlcv_url),
            )
        except Exception as e:
            return f"Failed to fetch GeckoTerminal data: {e}"

    attrs = _safe_get(pool_data, "data", "attributes") or {}
    included = pool_data.get("included", [])
    base_symbol = quote_symbol = ""
    dex_name = ""
    for item in included:
        if item.get("type") == "token":
            sym = _safe_get(item, "attributes", "symbol") or ""
            tid = item.get("id", "")
            base_id = _safe_get(pool_data, "data", "relationships", "base_token", "data", "id")
            if tid == base_id:
                base_symbol = sym
            else:
                quote_symbol = sym
        elif item.get("type") == "dex":
            dex_name = _safe_get(item, "attributes", "name") or ""

    name = attrs.get("name") or f"{base_symbol}/{quote_symbol}"
    base_price = attrs.get("base_token_price_usd")
    quote_price = attrs.get("quote_token_price_usd")
    fdv = attrs.get("fdv_usd")
    mcap = attrs.get("market_cap_usd")
    liquidity = attrs.get("reserve_in_usd")
    created_at = attrs.get("pool_created_at", "") or ""

    vol_24h = _safe_get(attrs, "volume_usd", "h24") or 0
    vol_1h = _safe_get(attrs, "volume_usd", "h1") or 0
    vol_5m = _safe_get(attrs, "volume_usd", "m5") or 0
    chg_24h = _safe_get(attrs, "price_change_percentage", "h24")
    chg_1h = _safe_get(attrs, "price_change_percentage", "h1")
    chg_5m = _safe_get(attrs, "price_change_percentage", "m5")
    buys_24h = _safe_get(attrs, "transactions", "h24", "buys") or 0
    sells_24h = _safe_get(attrs, "transactions", "h24", "sells") or 0

    # Optional: CLMM connector pool info (merge get_pool_info + get_pools row)
    clmm_info: dict | None = None
    clmm_error: str | None = None
    if config.connector:
        gw_network = NETWORK_MAP.get(config.network, config.network)
        try:
            info_task = client.gateway_clmm.get_pool_info(
                connector=config.connector,
                network=gw_network,
                pool_address=config.pool_address,
            )
            pools_task = client.gateway_clmm.get_pools(
                connector=config.connector,
                search_term=config.pool_address,
                limit=5,
            )
            info_res, pools_res = await asyncio.gather(
                info_task, pools_task, return_exceptions=True
            )

            merged: dict = {}
            if not isinstance(pools_res, Exception):
                for row in (pools_res or {}).get("pools", []):
                    if row.get("address") == config.pool_address:
                        merged.update(row)
                        break
            if not isinstance(info_res, Exception) and isinstance(info_res, dict):
                for k, v in info_res.items():
                    if v is not None:
                        merged[k] = v

            if merged:
                clmm_info = merged
            elif isinstance(info_res, Exception):
                clmm_error = str(info_res)
            elif isinstance(pools_res, Exception):
                clmm_error = str(pools_res)
        except Exception as e:
            clmm_error = str(e)

    # Build summary text
    lines = [
        f"*{name}* ({dex_name or 'DEX'})",
        f"Network: `{config.network}` | Pool: `{config.pool_address[:8]}...{config.pool_address[-6:]}`",
        "",
        f"💰 {base_symbol or 'Base'}: ${_fmt(base_price)}",
        f"💧 Liquidity: ${_fmt(liquidity)}",
        f"📊 Volume — 24h: ${_fmt(vol_24h)} | 1h: ${_fmt(vol_1h)} | 5m: ${_fmt(vol_5m)}",
        f"📈 Change — 24h: {_fmt(chg_24h, 2)}% | 1h: {_fmt(chg_1h, 2)}% | 5m: {_fmt(chg_5m, 2)}%",
        f"🔄 24h Txns: {buys_24h} buys / {sells_24h} sells",
    ]
    if fdv:
        lines.append(f"💎 FDV: ${_fmt(fdv)}")
    if mcap:
        lines.append(f"🏛️ MCap: ${_fmt(mcap)}")
    if created_at:
        lines.append(f"📅 Created: {created_at[:10]}")

    if clmm_info:
        lines.append("")
        lines.append("*On-chain pool info:*")
        # Show common CLMM fields if present (covers Meteora, Raydium, Uniswap shapes)
        display_keys = (
            "trading_pair", "bin_step", "tickSpacing", "base_fee_percentage", "feePct",
            "current_price", "price", "activeId", "liquidity",
            "volume_24h", "fees_24h", "apr", "apy",
            "mint_x", "mint_y", "baseTokenAddress", "quoteTokenAddress",
        )
        for key in display_keys:
            val = clmm_info.get(key)
            if val is not None and val != "":
                if key in ("apr", "apy", "base_fee_percentage", "feePct"):
                    val = f"{_fmt(val, 2)}%"
                elif key in ("liquidity", "volume_24h", "fees_24h"):
                    val = f"${_fmt(val)}"
                lines.append(f"  {key}: `{val}`")
    elif clmm_error:
        lines.append(f"\n_Connector info unavailable: {clmm_error[:120]}_")

    summary = "\n".join(lines)

    # Build candlestick chart
    ohlcv_list = _safe_get(ohlcv_data, "data", "attributes", "ohlcv_list") or []
    chart_bytes: bytes | None = None
    plotly_fig = None
    if ohlcv_list:
        try:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots

            # GeckoTerminal returns newest-first; reverse to chronological
            sorted_candles = sorted(ohlcv_list, key=lambda c: c[0])
            timestamps = [datetime.fromtimestamp(c[0]) for c in sorted_candles]
            opens = [c[1] for c in sorted_candles]
            highs = [c[2] for c in sorted_candles]
            lows = [c[3] for c in sorted_candles]
            closes = [c[4] for c in sorted_candles]
            volumes = [c[5] for c in sorted_candles]

            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.04,
                row_heights=[0.72, 0.28],
                subplot_titles=("Price (USD)", "Volume (USD)"),
            )
            fig.add_trace(
                go.Candlestick(
                    x=timestamps, open=opens, high=highs, low=lows, close=closes,
                    name="OHLC",
                    increasing_line_color="#26a69a",
                    decreasing_line_color="#ef5350",
                ),
                row=1, col=1,
            )
            colors = [
                "#26a69a" if c >= o else "#ef5350"
                for o, c in zip(opens, closes)
            ]
            fig.add_trace(
                go.Bar(x=timestamps, y=volumes, name="Volume", marker_color=colors),
                row=2, col=1,
            )
            fig.update_layout(
                title=f"{name} — {config.timeframe} ({len(sorted_candles)} candles)",
                xaxis_rangeslider_visible=False,
                height=720,
                template="plotly_dark",
                showlegend=False,
                margin=dict(l=40, r=20, t=60, b=40),
            )
            plotly_fig = fig

            try:
                buf = io.BytesIO()
                fig.write_image(buf, format="png", width=1100, height=720, scale=2)
                buf.seek(0)
                chart_bytes = buf.getvalue()

                if chat_id and context.bot:
                    buf.seek(0)
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=buf,
                        caption=f"{name} — {config.timeframe}",
                    )
            except Exception as e:
                logger.warning(f"Chart PNG render failed: {e}")
        except Exception as e:
            logger.warning(f"Chart build failed: {e}")

    # Build HTML report
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"Pool Report: {name}")
        builder.source("routine", "pool_report").tags(
            ["pool", config.network, name, dex_name or "dex"]
        )
        builder.markdown(summary.replace("`", ""))
        if plotly_fig is not None:
            builder.plotly(plotly_fig)
        if clmm_info:
            builder.markdown("### On-chain pool data")
            builder.table(
                [{"Field": k, "Value": str(v)} for k, v in clmm_info.items()],
                columns=["Field", "Value"],
            )
        if ohlcv_list:
            recent = sorted(ohlcv_list, key=lambda c: c[0], reverse=True)[:30]
            builder.markdown("### Recent candles")
            builder.table(
                [
                    {
                        "Time": datetime.fromtimestamp(c[0]).strftime("%Y-%m-%d %H:%M"),
                        "Open": c[1], "High": c[2], "Low": c[3], "Close": c[4],
                        "Volume USD": round(float(c[5]), 2),
                    }
                    for c in recent
                ]
            )
        builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return RoutineResult(text=summary, chart_image=chart_bytes)
