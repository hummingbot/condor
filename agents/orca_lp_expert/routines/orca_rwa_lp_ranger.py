"""Orca RWA LP Ranger — picks the top 4 RWA pools and suggests an LP price range from 5m candles.

Uses https://api.orca.so/v2/solana (Cloudflare-fronted; custom User-Agent required).
GeckoTerminal provides 5-minute OHLCV candles for range computation.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.reports import ReportBuilder
from handlers.dex.pool_data import fetch_ohlcv
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

CATEGORY = "Analysis"

_ORCA_BASE = "https://api.orca.so/v2/solana"
_USER_AGENT = "condor-dex-browser/1.0 (+https://github.com/hummingbot)"
_ORCA_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class Config(BaseModel):
    """Orca RWA LP Ranger — picks the top 4 RWA pools and suggests an LP price range from 5m candles."""

    min_tvl: float = Field(default=1000.0, description="Minimum pool TVL in USD")
    include_symbols: str = Field(
        default="SPCX,GME,SILV,TSLA,AAPL,NVDA,COIN,MSFT",
        description="Comma-separated RWA token symbols to scan",
    )
    candle_limit: int = Field(
        default=200, description="Number of 5m candles to fetch per pool (max 500)"
    )
    atr_periods: int = Field(
        default=14, description="ATR period for LP range calculation"
    )
    atr_multiplier: float = Field(
        default=3.0, description="ATR multiplier for LP range width"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _ts_to_dt(ts) -> datetime:
    """Convert Orca/GeckoTerminal timestamp (int s or ms) to UTC datetime."""
    ts_f = float(ts)
    if ts_f > 1_000_000_000_000:  # milliseconds
        return datetime.fromtimestamp(ts_f / 1000, tz=timezone.utc)
    return datetime.fromtimestamp(ts_f, tz=timezone.utc)  # seconds


def _compute_atr(highs, lows, closes, period: int) -> float:
    h = np.array(highs, dtype=float)
    l = np.array(lows, dtype=float)
    c = np.array(closes, dtype=float)
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    return float(np.mean(tr[-period:]))


def _parse_pool(pool: dict) -> dict | None:
    """Extract and normalise metrics from a raw Orca v2 whirlpool row."""
    try:
        address = pool.get("address") or ""
        if not address:
            return None
        sym_a = (pool.get("tokenA") or {}).get("symbol") or "?"
        sym_b = (pool.get("tokenB") or {}).get("symbol") or "?"
        # feeRate is raw basis-points * 100 (e.g. 1600 → 0.16%)
        fee_rate_raw = _safe_float(pool.get("feeRate"))
        fee_tier = fee_rate_raw / 10_000
        tvl = _safe_float(pool.get("tvlUsdc"))
        if tvl <= 0:
            return None
        stats_24h = (pool.get("stats") or {}).get("24h") or {}
        volume_24h = _safe_float(stats_24h.get("volume"))
        fees_24h = _safe_float(stats_24h.get("fees"))
        if fees_24h == 0.0 and fee_tier > 0.0 and volume_24h > 0.0:
            fees_24h = volume_24h * fee_tier
        fee_tvl_ratio = fees_24h / tvl * 100 if tvl > 0 else 0.0
        vol_tvl_ratio = volume_24h / tvl if tvl > 0 else 0.0
        score = fee_tvl_ratio * 0.6 + vol_tvl_ratio * 0.4
        addr_short = f"{address[:6]}…{address[-4:]}" if len(address) >= 10 else address
        return {
            "address": address,
            "address_short": addr_short,
            "token_a_symbol": sym_a,
            "token_b_symbol": sym_b,
            "fee_tier": round(fee_tier, 4),
            "tvl": round(tvl, 2),
            "volume_24h": round(volume_24h, 2),
            "fees_24h": round(fees_24h, 4),
            "fee_tvl_ratio": round(fee_tvl_ratio, 6),
            "vol_tvl_ratio": round(vol_tvl_ratio, 6),
            "score": round(score, 6),
        }
    except Exception as exc:
        logger.warning("Pool parse error %s: %s", pool.get("address", "?"), exc)
        return None


async def _search_symbol(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, symbol: str
) -> list[dict]:
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
            return [
                p
                for p in rows
                if isinstance(p, dict)
                and (
                    ((p.get("tokenA") or {}).get("symbol") or "")
                    .upper()
                    .startswith(sym_upper)
                    or ((p.get("tokenB") or {}).get("symbol") or "")
                    .upper()
                    .startswith(sym_upper)
                )
            ]
        except Exception as exc:
            logger.warning("Orca search '%s' failed: %s", symbol, exc)
            return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> RoutineResult:
    symbols = [
        s.strip().upper() for s in config.include_symbols.split(",") if s.strip()
    ]

    # ── Step 1: Fetch & rank RWA pools ────────────────────────────────────────
    sem = asyncio.Semaphore(5)
    async with httpx.AsyncClient(
        base_url=_ORCA_BASE,
        timeout=_ORCA_TIMEOUT,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    ) as orca:
        batches = await asyncio.gather(
            *[_search_symbol(orca, sem, sym) for sym in symbols]
        )

    seen: set[str] = set()
    all_pools: list[dict] = []
    for batch in batches:
        for raw in batch:
            parsed = _parse_pool(raw)
            if parsed and parsed["address"] not in seen:
                seen.add(parsed["address"])
                all_pools.append(parsed)

    filtered = [p for p in all_pools if p["tvl"] >= config.min_tvl]
    filtered.sort(key=lambda x: x["score"], reverse=True)
    top4 = filtered[:4]

    if not top4:
        builder = ReportBuilder("Orca RWA LP Ranger")
        builder.source("routine", "orca_rwa_lp_ranger")
        builder.tags(["orca", "rwa", "lp"])
        builder.section("RESULT", None)
        builder.markdown(
            f"No RWA pools found above min_tvl=${config.min_tvl:,.0f}. "
            f"Scanned {len(all_pools)} pools across {len(symbols)} symbols."
        )
        builder.manual_order()
        await builder.save()
        return RoutineResult(
            text=(
                f"No RWA pools found above min_tvl=${config.min_tvl:,.0f}. "
                f"Scanned {len(all_pools)} pools."
            )
        )

    # ── Step 2: Fetch 5m candles ──────────────────────────────────────────────
    # Through pool_data, not a raw URL: GeckoTerminal's ~30 req/min is per *IP*,
    # shared with the dashboard and the candle poll loop. fetch_ohlcv carries the
    # rate gate, the circuit breaker and the single-flight, so no manual pacing.
    candle_data: dict[str, list] = {}
    for pool in top4:
        addr = pool["address"]
        ohlcv, err = await fetch_ohlcv(
            addr,
            "solana",
            timeframe="5m",
            currency="usd",
            limit=config.candle_limit,
        )
        if err or not ohlcv:
            logger.warning("GeckoTerminal candles for %s failed: %s", addr, err)
            candle_data[addr] = []
            continue
        candle_data[addr] = sorted(ohlcv, key=lambda x: x[0])  # ascending by timestamp

    # ── Steps 3 & 4: ATR ranges + 2×2 chart ──────────────────────────────────
    subplot_titles = [f"{p['token_a_symbol']}-{p['token_b_symbol']}" for p in top4]
    while len(subplot_titles) < 4:
        subplot_titles.append("")

    fig = make_subplots(
        rows=2,
        cols=2,
        shared_xaxes=False,
        subplot_titles=subplot_titles,
    )

    range_table_data: list[dict] = []

    for i, pool in enumerate(top4):
        row = i // 2 + 1
        col = i % 2 + 1
        addr = pool["address"]
        ohlcv = candle_data.get(addr, [])
        ta = pool["token_a_symbol"]
        tb = pool["token_b_symbol"]

        if len(ohlcv) < config.atr_periods + 1:
            logger.warning("Insufficient candles for %s-%s: %d", ta, tb, len(ohlcv))
            range_table_data.append(
                {
                    "Pool": f"{ta}-{tb}",
                    "Current Price": "N/A",
                    "Lower": "—",
                    "Upper": "—",
                    "Range %": "—",
                    "ATR": "—",
                    "Fee Tier": f"{pool['fee_tier']:.4f}%",
                    "TVL": f"${pool['tvl']:,.0f}",
                    "24h Fees": f"${pool['fees_24h']:,.2f}",
                }
            )
            continue

        timestamps = [_ts_to_dt(c[0]) for c in ohlcv]
        opens = [c[1] for c in ohlcv]
        highs = [c[2] for c in ohlcv]
        lows = [c[3] for c in ohlcv]
        closes = [c[4] for c in ohlcv]

        atr = _compute_atr(highs, lows, closes, config.atr_periods)
        current_price = closes[-1]
        lower_price = max(
            current_price - config.atr_multiplier * atr, current_price * 0.01
        )
        upper_price = current_price + config.atr_multiplier * atr
        range_pct = (upper_price - lower_price) / current_price * 100

        pool["current_price"] = current_price
        pool["lower_price"] = lower_price
        pool["upper_price"] = upper_price
        pool["range_pct"] = range_pct
        pool["atr"] = atr

        # Candlestick
        fig.add_trace(
            go.Candlestick(
                x=timestamps,
                open=opens,
                high=highs,
                low=lows,
                close=closes,
                name=f"{ta}-{tb}",
                increasing_line_color="#22c55e",
                decreasing_line_color="#ef4444",
                showlegend=False,
            ),
            row=row,
            col=col,
        )

        # LP range shaded band
        x_band = timestamps + timestamps[::-1]
        y_band = [upper_price] * len(timestamps) + [lower_price] * len(timestamps)
        fig.add_trace(
            go.Scatter(
                x=x_band,
                y=y_band,
                fill="toself",
                fillcolor="rgba(99, 102, 241, 0.15)",
                line=dict(width=0),
                mode="lines",
                name="LP Range",
                showlegend=(i == 0),
                hoverinfo="skip",
            ),
            row=row,
            col=col,
        )

        # Upper bound dashed line
        fig.add_shape(
            type="line",
            x0=timestamps[0],
            x1=timestamps[-1],
            y0=upper_price,
            y1=upper_price,
            line=dict(color="#6366f1", width=1.5, dash="dash"),
            row=row,
            col=col,
        )
        # Lower bound dashed line
        fig.add_shape(
            type="line",
            x0=timestamps[0],
            x1=timestamps[-1],
            y0=lower_price,
            y1=lower_price,
            line=dict(color="#6366f1", width=1.5, dash="dash"),
            row=row,
            col=col,
        )
        # Current price dotted line
        fig.add_shape(
            type="line",
            x0=timestamps[0],
            x1=timestamps[-1],
            y0=current_price,
            y1=current_price,
            line=dict(color="#f59e0b", width=1, dash="dot"),
            row=row,
            col=col,
        )

        # Price labels at right edge
        fig.add_annotation(
            x=timestamps[-1],
            y=upper_price,
            text=f"  ${upper_price:.4f}",
            showarrow=False,
            font=dict(color="#6366f1", size=10),
            xanchor="left",
            row=row,
            col=col,
        )
        fig.add_annotation(
            x=timestamps[-1],
            y=lower_price,
            text=f"  ${lower_price:.4f}",
            showarrow=False,
            font=dict(color="#6366f1", size=10),
            xanchor="left",
            row=row,
            col=col,
        )

        fig.update_xaxes(type="date", rangeslider_visible=False, row=row, col=col)

        range_table_data.append(
            {
                "Pool": f"{ta}-{tb}",
                "Current Price": f"${current_price:.4f}",
                "Lower": f"${lower_price:.4f}",
                "Upper": f"${upper_price:.4f}",
                "Range %": f"{range_pct:.1f}%",
                "ATR": f"{atr:.6f}",
                "Fee Tier": f"{pool['fee_tier']:.4f}%",
                "TVL": f"${pool['tvl']:,.0f}",
                "24h Fees": f"${pool['fees_24h']:,.2f}",
            }
        )

    fig.update_layout(
        title="Orca RWA — Top 4 Pools LP Range Suggestion (5m candles)",
        template="plotly_dark",
        height=800,
        legend=dict(orientation="h", yanchor="top", y=-0.05, xanchor="center", x=0.5),
    )

    # ── Step 5: ReportBuilder ─────────────────────────────────────────────────
    builder = ReportBuilder("Orca RWA LP Ranger")
    builder.source("routine", "orca_rwa_lp_ranger")
    builder.tags(["orca", "rwa", "lp", "range", "candles"])
    builder.manual_order()

    builder.section(
        "TOP 4 RWA POOLS", "Ranked by composite score (fee/TVL × 0.6 + vol/TVL × 0.4)"
    )
    builder.kpi("Pools Analyzed", str(len(all_pools)))
    builder.kpi("Pools Selected", str(len(top4)))
    for p in top4:
        builder.kpi(
            f"{p['token_a_symbol']}-{p['token_b_symbol']}", f"Score: {p['score']:.3f}"
        )

    builder.section(
        "LP RANGE CHART", "5m candles with ATR-based LP range overlay (purple band)"
    )
    builder.plotly(fig)

    builder.section("RANGE DETAILS", "Suggested ranges per pool")
    builder.table(
        range_table_data,
        [
            "Pool",
            "Current Price",
            "Lower",
            "Upper",
            "Range %",
            "ATR",
            "Fee Tier",
            "TVL",
            "24h Fees",
        ],
    )

    builder.section("NOTES", None)
    builder.markdown(
        f"**Range logic:** ATR({config.atr_periods}) × {config.atr_multiplier} centered on current price\n\n"
        "**Candles:** 5m from GeckoTerminal\n\n"
        "**Warning:** Tokenized RWAs track equity market hours — range may widen significantly at "
        "open/close. Consider widening range further for overnight positions."
    )

    report_id = await builder.save()

    # ── Step 6: RoutineResult ─────────────────────────────────────────────────
    pools_with_ranges = [p for p in top4 if "current_price" in p]
    return RoutineResult(
        text=(
            f"Top {len(top4)} RWA pools ranked. "
            f"Ranges computed from ATR({config.atr_periods})×{config.atr_multiplier}. "
            f"Report: {report_id}"
        ),
        table_data=[
            {
                "Pool": f"{p['token_a_symbol']}-{p['token_b_symbol']}",
                "Current": f"${p['current_price']:.4f}",
                "Lower": f"${p['lower_price']:.4f}",
                "Upper": f"${p['upper_price']:.4f}",
                "Range %": f"{p['range_pct']:.1f}%",
                "Score": f"{p['score']:.3f}",
            }
            for p in pools_with_ranges
        ],
        table_columns=["Pool", "Current", "Lower", "Upper", "Range %", "Score"],
    )
