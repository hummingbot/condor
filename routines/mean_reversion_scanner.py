"""Scan top Binance perp markets for mean reversion upside — ATH, VWAP vs current price."""

CATEGORY = "Market Data"

import asyncio
import logging

import aiohttp
import numpy as np
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

BINANCE_SPOT_TICKER = "https://api.binance.com/api/v3/ticker/24hr"
BINANCE_SPOT_KLINES = "https://api.binance.com/api/v3/klines"
BINANCE_FUTURES_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
MAX_CONCURRENT = 10

# Exclude stablecoins and wrapped tokens
EXCLUDE_BASES = {
    "USDC",
    "BUSD",
    "DAI",
    "TUSD",
    "FDUSD",
    "BFUSD",
    "WBTC",
    "WETH",
    "STETH",
    "WSTETH",
    "RETH",
}


class Config(BaseModel):
    """Scan top perp markets for mean reversion upside — ATH, VWAP vs current price."""

    connector: str = Field(
        default="binance_perpetual", description="Exchange connector"
    )
    top_n: int = Field(default=80, description="Top N pairs by 24h volume to scan")
    show_n: int = Field(default=25, description="Show top N results")
    min_volume_usd: float = Field(
        default=10_000_000, description="Min 24h volume (USD)"
    )
    min_history_days: int = Field(default=90, description="Min days of candle history")
    sort_by: str = Field(
        default="upside_ath",
        description="Sort: upside_ath, upside_vwap, discount_pct",
    )


# ---------------------------------------------------------------------------
# Step 1: Fetch perpetual symbols to filter, then get spot tickers
# ---------------------------------------------------------------------------


async def fetch_top_pairs(
    session: aiohttp.ClientSession, min_volume: float, top_n: int
) -> list[dict]:
    # Get list of active perpetual USDT symbols
    async with session.get(
        BINANCE_FUTURES_INFO, timeout=aiohttp.ClientTimeout(total=10)
    ) as resp:
        if resp.status != 200:
            # Fallback: skip futures filter, just use spot tickers
            perp_bases = None
        else:
            info = await resp.json()
            perp_bases = set()
            for s in info.get("symbols", []):
                if (
                    s.get("contractType") == "PERPETUAL"
                    and s.get("quoteAsset") == "USDT"
                    and s.get("status") == "TRADING"
                ):
                    perp_bases.add(s.get("baseAsset", ""))

    # Get 24h tickers from spot (more reliable, same prices)
    async with session.get(
        BINANCE_SPOT_TICKER, timeout=aiohttp.ClientTimeout(total=15)
    ) as resp:
        resp.raise_for_status()
        tickers = await resp.json()

    pairs = []
    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        base = symbol.replace("USDT", "")
        if base in EXCLUDE_BASES:
            continue
        # Only include coins that have a perpetual contract
        if perp_bases is not None and base not in perp_bases:
            continue
        quote_vol = float(t.get("quoteVolume", 0))
        if quote_vol < min_volume:
            continue
        pairs.append(
            {
                "trading_pair": f"{base}-USDT",
                "binance_symbol": symbol,
                "price": float(t.get("lastPrice", 0)),
                "change_24h": float(t.get("priceChangePercent", 0)),
                "volume_24h_usd": quote_vol,
            }
        )

    pairs.sort(key=lambda x: x["volume_24h_usd"], reverse=True)
    return pairs[:top_n]


# ---------------------------------------------------------------------------
# Step 2: Fetch daily candles directly from Binance spot API
# ---------------------------------------------------------------------------


async def fetch_daily_candles(
    session: aiohttp.ClientSession,
    binance_symbol: str,
) -> list[list] | None:
    """Fetch up to 1000 daily candles from Binance spot klines API."""
    try:
        params = {
            "symbol": binance_symbol,
            "interval": "1d",
            "limit": 1000,
        }
        async with session.get(
            BINANCE_SPOT_KLINES,
            params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception as e:
        logger.debug(f"Klines failed for {binance_symbol}: {e}")
        return None


async def fetch_all_candles(
    session: aiohttp.ClientSession,
    pairs: list[dict],
    batch_size: int = 10,
) -> list[list[list] | None]:
    """Fetch candles in batches of batch_size to avoid rate limits."""
    results = []
    for i in range(0, len(pairs), batch_size):
        batch = pairs[i : i + batch_size]
        batch_results = await asyncio.gather(
            *[fetch_daily_candles(session, p["binance_symbol"]) for p in batch],
            return_exceptions=True,
        )
        results.extend(r if isinstance(r, list) else None for r in batch_results)
        # Small pause between batches
        if i + batch_size < len(pairs):
            await asyncio.sleep(0.5)
    return results


# ---------------------------------------------------------------------------
# Step 3: Analyze a single pair
# ---------------------------------------------------------------------------


def analyze_pair(pair_info: dict, klines: list[list]) -> dict | None:
    """Analyze Binance klines: [open_time, open, high, low, close, volume, ...]"""
    if len(klines) < 10:
        return None

    try:
        opens = np.array([float(k[1]) for k in klines])
        highs = np.array([float(k[2]) for k in klines])
        lows = np.array([float(k[3]) for k in klines])
        closes = np.array([float(k[4]) for k in klines])
        volumes = np.array([float(k[5]) for k in klines])
    except (IndexError, ValueError):
        return None

    current_price = pair_info["price"]
    if current_price <= 0:
        current_price = closes[-1]
    if current_price <= 0:
        return None

    ath = float(np.max(highs))
    positive_lows = lows[lows > 0]
    atl = (
        float(np.min(positive_lows))
        if len(positive_lows) > 0
        else float(np.min(closes[closes > 0]))
    )

    # VWAP = sum(volume * typical_price) / sum(volume)
    typical = (highs + lows + closes) / 3
    total_vol = np.sum(volumes)
    vwap = (
        float(np.sum(volumes * typical) / total_vol)
        if total_vol > 0
        else float(np.mean(closes))
    )

    upside_ath = ((ath / current_price) - 1) * 100
    upside_vwap = ((vwap / current_price) - 1) * 100
    discount_pct = ((ath - current_price) / ath) * 100 if ath > 0 else 0

    price_range = ath - atl
    position_pct = (
        ((current_price - atl) / price_range * 100) if price_range > 0 else 50
    )

    return {
        "trading_pair": pair_info["trading_pair"],
        "current_price": current_price,
        "ath": ath,
        "atl": atl,
        "vwap": vwap,
        "volume_24h_usd": pair_info["volume_24h_usd"],
        "change_24h": pair_info["change_24h"],
        "upside_ath": round(upside_ath, 1),
        "upside_vwap": round(upside_vwap, 1),
        "discount_pct": round(discount_pct, 1),
        "position_pct": round(position_pct, 1),
        "history_days": len(klines),
    }


# ---------------------------------------------------------------------------
# Step 4: Format output
# ---------------------------------------------------------------------------


def fmt_price(p: float) -> str:
    if p >= 100:
        return f"${p:,.0f}"
    if p >= 1:
        return f"${p:,.2f}"
    if p >= 0.01:
        return f"${p:.4f}"
    return f"${p:.6g}"


def fmt_vol(v: float) -> str:
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v / 1e3:.0f}K"


def format_results(results: list[dict], sort_by: str, show_n: int) -> str:
    lines = [
        f"Mean Reversion Scanner — {len(results)} pairs analyzed",
        f"Sorted by: {sort_by}",
        "",
    ]

    for i, r in enumerate(results[:show_n], 1):
        arrow = "🟢" if r["upside_vwap"] > 0 else "🔴"
        lines.append(
            f"{i}. {arrow} {r['trading_pair']} — {fmt_price(r['current_price'])}"
        )
        lines.append(
            f"   ATH: {fmt_price(r['ath'])} ({r['upside_ath']:+.0f}%) | "
            f"VWAP: {fmt_price(r['vwap'])} ({r['upside_vwap']:+.0f}%)"
        )
        lines.append(
            f"   Discount: {r['discount_pct']:.0f}% | "
            f"Range: {r['position_pct']:.0f}% | "
            f"{r['history_days']}d history | "
            f"Vol: {fmt_vol(r['volume_24h_usd'])}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = context._chat_id
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. Fetch top perpetual pairs by volume
        try:
            pairs = await fetch_top_pairs(session, config.min_volume_usd, config.top_n)
        except Exception as e:
            return f"Failed to fetch tickers: {e}"

        if not pairs:
            return "No pairs found matching criteria."

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Fetching daily candles for {len(pairs)} pairs...",
        )

        # 2. Fetch daily candles in batches of 10
        kline_results = await fetch_all_candles(
            session, pairs, batch_size=MAX_CONCURRENT
        )

    # 3. Analyze each pair
    results = []
    for pair_info, klines in zip(pairs, kline_results):
        if klines and len(klines) >= config.min_history_days:
            analysis = analyze_pair(pair_info, klines)
            if analysis:
                results.append(analysis)

    if not results:
        return "Analysis failed — no valid candle data."

    # 4. Sort
    sort_key = (
        config.sort_by
        if config.sort_by in ("upside_ath", "upside_vwap", "discount_pct")
        else "upside_ath"
    )
    results.sort(key=lambda x: x[sort_key], reverse=True)

    text = format_results(results, sort_key, config.show_n)

    # 5. Report
    try:
        import plotly.graph_objects as go

        from condor.reports import ReportBuilder

        # --- Scatter: VWAP upside vs ATH upside ---
        sizes = np.array([r["volume_24h_usd"] for r in results])
        sizes_norm = (
            (sizes / sizes.max() * 30 + 8)
            if sizes.max() > 0
            else np.full(len(results), 15)
        )
        colors = ["#3fb950" if r["upside_vwap"] > 0 else "#f85149" for r in results]

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=[r["upside_vwap"] for r in results],
                y=[r["upside_ath"] for r in results],
                mode="markers+text",
                marker=dict(
                    size=sizes_norm,
                    color=colors,
                    line=dict(width=1, color="#0d1117"),
                    opacity=0.8,
                ),
                text=[r["trading_pair"].replace("-USDT", "") for r in results],
                textposition="top center",
                textfont=dict(size=7, color="#8b949e"),
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "VWAP upside: %{x:.0f}%<br>"
                    "ATH upside: %{y:.0f}%<extra></extra>"
                ),
            )
        )
        fig.update_layout(
            title="Mean Reversion — ATH vs VWAP Upside",
            xaxis_title="VWAP Upside (%)",
            yaxis_title="ATH Upside (%)",
            paper_bgcolor="#0d1117",
            plot_bgcolor="#161b22",
            font=dict(color="#c9d1d9", size=11),
            margin=dict(l=60, r=30, t=80, b=60),
            showlegend=False,
        )
        fig.update_xaxes(gridcolor="#21262d", zeroline=True, zerolinecolor="#30363d")
        fig.update_yaxes(gridcolor="#21262d", zeroline=True, zerolinecolor="#30363d")
        fig.update_layout(
            legend=dict(
                orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5
            )
        )

        # --- Bar chart: top 20 by discount from ATH ---
        top_discount = sorted(results, key=lambda x: x["discount_pct"], reverse=True)[
            :20
        ]
        fig2 = go.Figure()
        fig2.add_trace(
            go.Bar(
                x=[r["trading_pair"].replace("-USDT", "") for r in top_discount],
                y=[r["discount_pct"] for r in top_discount],
                marker_color=[
                    "#3fb950" if r["upside_vwap"] > 0 else "#f85149"
                    for r in top_discount
                ],
                hovertemplate="%{x}<br>Discount: %{y:.0f}%<extra></extra>",
            )
        )
        fig2.update_layout(
            title="Discount from ATH (%)",
            xaxis_title="",
            yaxis_title="Discount %",
            paper_bgcolor="#0d1117",
            plot_bgcolor="#161b22",
            font=dict(color="#c9d1d9", size=11),
            margin=dict(l=50, r=20, t=60, b=80),
            xaxis_tickangle=-45,
        )
        fig2.update_xaxes(gridcolor="#21262d")
        fig2.update_yaxes(gridcolor="#21262d")
        fig2.update_layout(
            legend=dict(
                orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5
            )
        )

        # --- Table ---
        table_data = [
            {
                "Pair": r["trading_pair"],
                "Price": fmt_price(r["current_price"]),
                "ATH": fmt_price(r["ath"]),
                "ATH Up": f"{r['upside_ath']:+.0f}%",
                "VWAP": fmt_price(r["vwap"]),
                "VWAP Up": f"{r['upside_vwap']:+.0f}%",
                "Discount": f"{r['discount_pct']:.0f}%",
                "Range": f"{r['position_pct']:.0f}%",
                "Vol 24h": fmt_vol(r["volume_24h_usd"]),
                "Days": r["history_days"],
            }
            for r in results
        ]

        # KPIs
        top_ath_r = results[0]
        top_vwap_r = max(results, key=lambda x: x["upside_vwap"])
        below_vwap = sum(1 for r in results if r["upside_vwap"] > 0)
        avg_discount = np.mean([r["discount_pct"] for r in results])

        builder = ReportBuilder(f"Mean Reversion Scanner ({len(results)} pairs)")
        builder.source("routine", "mean_reversion_scanner").tags(
            ["scanner", "mean-reversion", "perpetual"]
        )
        builder.kpi(
            "Top ATH Upside",
            f"{top_ath_r['trading_pair']} +{top_ath_r['upside_ath']:.0f}%",
        )
        builder.kpi(
            "Top VWAP Upside",
            f"{top_vwap_r['trading_pair']} +{top_vwap_r['upside_vwap']:.0f}%",
        )
        builder.kpi("Below VWAP", f"{below_vwap}/{len(results)} pairs")
        builder.kpi("Avg ATH Discount", f"{avg_discount:.0f}%")
        builder.plotly(fig)
        builder.plotly(fig2)
        builder.markdown(
            "## Methodology\n"
            "- **ATH**: All-time high from daily candles (up to 1000 days)\n"
            "- **VWAP**: Volume-weighted average price over full history\n"
            "- **Upside**: Potential gain if price reverts to ATH or VWAP\n"
            "- **Range Position**: 0% = near all-time low, 100% = near ATH\n"
            "- Green = below VWAP (potential mean reversion up)\n"
            "- Red = above VWAP (already extended)"
        )
        builder.table(table_data)
        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return text
