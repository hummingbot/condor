"""Spot market discovery — top markets by volume across quote currencies with rebate focus."""

CATEGORY = "Market Data"

import asyncio
import logging
from typing import Any

import aiohttp
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

BINANCE_SPOT_TICKER = "https://api.binance.com/api/v3/ticker/24hr"

# Quote currencies we care about
REBATE_QUOTES = {
    "BRL",
    "ARS",
    "MXN",
    "EUR",
    "PLN",
    "JPY",
    "TRY",
    "RON",
    "UAH",
    "ZAR",
    "NGN",
    "COP",
}
STABLE_QUOTES = {"USDT", "USDC", "BUSD", "FDUSD", "TUSD"}

# Stablecoins / wrapped tokens to exclude as base
EXCLUDED_BASES = {"USDT", "USDC", "BUSD", "TUSD", "FDUSD", "DAI", "BFUSD"}

COLORS = {
    "bg_paper": "#0d1117",
    "bg_plot": "#161b22",
    "grid": "#21262d",
    "text": "#c9d1d9",
    "text_dim": "#8b949e",
    "green": "#3fb950",
    "red": "#f85149",
    "blue": "#58a6ff",
    "yellow": "#f0b90b",
    "purple": "#bc8cff",
    "orange": "#f78166",
}

QUOTE_COLORS = {
    "BRL": "#3fb950",
    "USDT": "#e6e6e6",
    "FDUSD": "#58a6ff",
    "USDC": "#2775ca",
    "ARS": "#75b5f0",
    "MXN": "#f0b90b",
    "EUR": "#f78166",
    "PLN": "#bc8cff",
    "JPY": "#ff7b72",
    "TRY": "#d2a8ff",
    "RON": "#7ee787",
    "UAH": "#79c0ff",
    "ZAR": "#ffa657",
    "NGN": "#56d364",
    "COP": "#e3b341",
}


class Config(BaseModel):
    """Spot market discovery — top pairs by volume, FIAT rebate markets, and USDT leaders."""

    top_n: int = Field(default=50, description="Top N markets per section")
    min_volume_usd: float = Field(
        default=100_000, description="Min 24h volume (USD) for FIAT pairs"
    )
    min_volume_usdt: float = Field(
        default=5_000_000, description="Min 24h volume (USD) for USDT pairs"
    )
    focus_quote: str = Field(
        default="BRL", description="Primary FIAT quote to highlight"
    )


def _parse_quote(symbol: str, known_quotes: set[str]) -> tuple[str, str] | None:
    """Parse a Binance symbol into (base, quote) using known quote list."""
    for q in sorted(known_quotes, key=len, reverse=True):
        if symbol.endswith(q):
            base = symbol[: -len(q)]
            if base:
                return base, q
    return None


async def fetch_spot_tickers() -> list[dict]:
    """Fetch all 24h tickers from Binance spot."""
    async with aiohttp.ClientSession() as session:
        async with session.get(BINANCE_SPOT_TICKER) as resp:
            resp.raise_for_status()
            return await resp.json()


def categorize_tickers(tickers: list[dict]) -> dict[str, list[dict]]:
    """Parse and categorize tickers by quote currency."""
    all_quotes = REBATE_QUOTES | STABLE_QUOTES | {"BTC", "ETH", "BNB"}
    by_quote: dict[str, list[dict]] = {}

    for t in tickers:
        symbol = t.get("symbol", "")
        parsed = _parse_quote(symbol, all_quotes)
        if not parsed:
            continue
        base, quote = parsed
        if base in EXCLUDED_BASES:
            continue

        quote_vol = float(t.get("quoteVolume", 0))
        if quote_vol <= 0:
            continue

        entry = {
            "symbol": symbol,
            "base": base,
            "quote": quote,
            "trading_pair": f"{base}-{quote}",
            "price": float(t.get("lastPrice", 0)),
            "price_change_pct": float(t.get("priceChangePercent", 0)),
            "volume_24h": float(t.get("volume", 0)),
            "quote_volume_24h": quote_vol,
            "trades_24h": int(t.get("count", 0)),
        }
        by_quote.setdefault(quote, []).append(entry)

    # Sort each group by quote volume
    for q in by_quote:
        by_quote[q].sort(key=lambda x: x["quote_volume_24h"], reverse=True)

    return by_quote


async def _fetch_usdt_rates(tickers: list[dict]) -> dict[str, float]:
    """Extract USDT rates for fiat currencies from ticker data."""
    rates = {"USDT": 1.0, "USDC": 1.0, "FDUSD": 1.0, "BUSD": 1.0, "TUSD": 1.0}
    # BTC-USDT price for cross-conversion
    btc_usdt = 0.0
    for t in tickers:
        sym = t.get("symbol", "")
        price = float(t.get("lastPrice", 0))
        if sym == "BTCUSDT":
            btc_usdt = price
        # Direct USDT/FIAT pairs (e.g., USDTBRL → 1 USDT = X BRL)
        if sym.startswith("USDT") and price > 0:
            fiat = sym[4:]
            if fiat in REBATE_QUOTES:
                rates[fiat] = 1.0 / price  # BRL per USDT → USDT per BRL
    # Also check FIAT/USDT pairs (unlikely on Binance but just in case)
    for t in tickers:
        sym = t.get("symbol", "")
        price = float(t.get("lastPrice", 0))
        if sym.endswith("USDT") and price > 0:
            base = sym[:-4]
            if base in REBATE_QUOTES and base not in rates:
                rates[base] = price
    return rates


def format_volume(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


def _build_report(
    by_quote: dict[str, list[dict]],
    usdt_rates: dict[str, float],
    config: Config,
) -> tuple[str, go.Figure, list[dict]]:
    """Build text summary, chart, and table data."""

    # --- Section 1: Focus FIAT quote (BRL by default) ---
    focus = config.focus_quote.upper()
    focus_pairs = by_quote.get(focus, [])
    focus_rate = usdt_rates.get(focus, 0)

    # --- Section 2: All FIAT markets summary ---
    fiat_summary = []
    for q in sorted(REBATE_QUOTES):
        pairs = by_quote.get(q, [])
        if not pairs:
            continue
        rate = usdt_rates.get(q, 0)
        total_vol_usdt = (
            sum(p["quote_volume_24h"] * rate for p in pairs) if rate > 0 else 0
        )
        fiat_summary.append(
            {
                "quote": q,
                "pair_count": len(pairs),
                "total_volume_usdt": total_vol_usdt,
                "top_pair": pairs[0]["trading_pair"] if pairs else "-",
                "top_volume": (
                    pairs[0]["quote_volume_24h"] * rate if pairs and rate > 0 else 0
                ),
            }
        )
    fiat_summary.sort(key=lambda x: x["total_volume_usdt"], reverse=True)

    # --- Section 3: Top USDT pairs ---
    usdt_pairs = [
        p
        for p in by_quote.get("USDT", [])
        if p["quote_volume_24h"] >= config.min_volume_usdt
    ]
    usdt_top = usdt_pairs[: config.top_n]

    # --- Build text ---
    lines = []

    # FIAT overview
    lines.append("FIAT MARKETS OVERVIEW (Rebate Eligible)")
    lines.append("-" * 45)
    total_fiat_vol = sum(f["total_volume_usdt"] for f in fiat_summary)
    lines.append(f"Total FIAT volume: {format_volume(total_fiat_vol)} USDT equiv.")
    lines.append("")
    for f in fiat_summary:
        lines.append(
            f"  {f['quote']:>4s}: {f['pair_count']:3d} pairs | "
            f"Vol: {format_volume(f['total_volume_usdt']):>8s} | "
            f"Top: {f['top_pair']}"
        )

    # Focus FIAT detail
    if focus_pairs and focus_rate > 0:
        filtered = [
            p
            for p in focus_pairs
            if p["quote_volume_24h"] * focus_rate >= config.min_volume_usd
        ]
        lines.append("")
        lines.append(f"TOP {focus}-QUOTED MARKETS (Rebates)")
        lines.append("-" * 45)
        for i, p in enumerate(filtered[: config.top_n], 1):
            vol_usdt = p["quote_volume_24h"] * focus_rate
            chg = p["price_change_pct"]
            chg_s = f"+{chg:.1f}%" if chg >= 0 else f"{chg:.1f}%"
            lines.append(
                f"  {i:2d}. {p['trading_pair']:<12s} "
                f"Vol: {format_volume(vol_usdt):>8s}  "
                f"24h: {chg_s:>7s}  "
                f"Trades: {p['trades_24h']:,}"
            )

    # Top USDT
    lines.append("")
    lines.append(f"TOP {config.top_n} USDT-QUOTED MARKETS")
    lines.append("-" * 45)
    for i, p in enumerate(usdt_top, 1):
        chg = p["price_change_pct"]
        chg_s = f"+{chg:.1f}%" if chg >= 0 else f"{chg:.1f}%"
        lines.append(
            f"  {i:2d}. {p['trading_pair']:<12s} "
            f"Vol: {format_volume(p['quote_volume_24h']):>8s}  "
            f"24h: {chg_s:>7s}  "
            f"Trades: {p['trades_24h']:,}"
        )

    text = "\n".join(lines)

    # --- Chart: two panels ---
    fig = make_subplots(
        rows=2,
        cols=1,
        subplot_titles=[
            f"FIAT Market Volume (USDT equiv.)",
            f"Top 30 USDT Markets — Volume vs 24h Change",
        ],
        row_heights=[0.35, 0.65],
        vertical_spacing=0.12,
    )

    # Panel 1: FIAT volume bar chart
    if fiat_summary:
        quotes = [f["quote"] for f in fiat_summary]
        volumes = [f["total_volume_usdt"] for f in fiat_summary]
        bar_colors = [QUOTE_COLORS.get(q, "#8b949e") for q in quotes]
        fig.add_trace(
            go.Bar(
                x=quotes,
                y=volumes,
                marker_color=bar_colors,
                text=[format_volume(v) for v in volumes],
                textposition="outside",
                textfont=dict(size=9, color=COLORS["text"]),
                hovertemplate="%{x}: %{y:,.0f} USDT<extra></extra>",
                showlegend=False,
            ),
            row=1,
            col=1,
        )

    # Panel 2: Scatter — volume vs price change for top USDT
    scatter_data = usdt_top[:30]
    if scatter_data:
        volumes_s = [p["quote_volume_24h"] for p in scatter_data]
        changes = [p["price_change_pct"] for p in scatter_data]
        labels = [p["base"] for p in scatter_data]
        scatter_colors = [COLORS["green"] if c >= 0 else COLORS["red"] for c in changes]

        fig.add_trace(
            go.Scatter(
                x=changes,
                y=volumes_s,
                mode="markers+text",
                marker=dict(
                    color=scatter_colors,
                    size=[max(8, min(25, v / max(volumes_s) * 25)) for v in volumes_s],
                    line=dict(width=1, color=COLORS["bg_paper"]),
                ),
                text=labels,
                textposition="top center",
                textfont=dict(size=8, color=COLORS["text_dim"]),
                hovertemplate="%{text}<br>Vol: %{y:,.0f}<br>Chg: %{x:.1f}%<extra></extra>",
                showlegend=False,
            ),
            row=2,
            col=1,
        )

    fig.update_layout(
        paper_bgcolor=COLORS["bg_paper"],
        plot_bgcolor=COLORS["bg_plot"],
        font=dict(color=COLORS["text_dim"], size=10),
        margin=dict(l=60, r=30, t=60, b=40),
        height=800,
    )
    for i in range(1, 3):
        fig.update_xaxes(gridcolor=COLORS["grid"], row=i, col=1)
        fig.update_yaxes(gridcolor=COLORS["grid"], row=i, col=1)
    fig.update_yaxes(type="log", title="Volume (USDT)", row=1, col=1)
    fig.update_yaxes(type="log", title="Volume 24h (USDT)", row=2, col=1)
    fig.update_xaxes(title="24h Change (%)", row=2, col=1)

    # Annotation styling for subplot titles
    for ann in fig.layout.annotations:
        ann.font = dict(size=12, color=COLORS["text"])

    # --- Table data for report ---
    table_rows = []

    # FIAT summary table
    for f in fiat_summary:
        table_rows.append(
            {
                "Section": "FIAT Overview",
                "Quote": f["quote"],
                "Pairs": f["pair_count"],
                "Total Volume": format_volume(f["total_volume_usdt"]),
                "Top Pair": f["top_pair"],
            }
        )

    # Focus pairs table
    if focus_pairs and focus_rate > 0:
        for p in focus_pairs[: config.top_n]:
            vol_usdt = p["quote_volume_24h"] * focus_rate
            if vol_usdt < config.min_volume_usd:
                continue
            table_rows.append(
                {
                    "Section": f"{focus} Markets",
                    "Pair": p["trading_pair"],
                    "Volume (USDT)": format_volume(vol_usdt),
                    "24h Change": f"{p['price_change_pct']:+.1f}%",
                    "Trades": f"{p['trades_24h']:,}",
                }
            )

    # USDT top pairs
    for p in usdt_top[: config.top_n]:
        table_rows.append(
            {
                "Section": "USDT Markets",
                "Pair": p["trading_pair"],
                "Volume (USDT)": format_volume(p["quote_volume_24h"]),
                "24h Change": f"{p['price_change_pct']:+.1f}%",
                "Trades": f"{p['trades_24h']:,}",
            }
        )

    return text, fig, table_rows


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> RoutineResult:
    """Discover spot markets across quote currencies."""
    # Fetch all tickers
    try:
        tickers = await fetch_spot_tickers()
    except Exception as e:
        return RoutineResult(text=f"Failed to fetch Binance spot tickers: {e}")

    if not tickers:
        return RoutineResult(text="No ticker data returned from Binance.")

    # Categorize and get rates
    by_quote = categorize_tickers(tickers)
    usdt_rates = await _fetch_usdt_rates(tickers)

    # Build output
    text, fig, table_rows = _build_report(by_quote, usdt_rates, config)

    # Generate report
    from condor.reports import ReportBuilder

    # Split tables by section
    fiat_overview = [r for r in table_rows if r.get("Section") == "FIAT Overview"]
    focus_rows = [
        r
        for r in table_rows
        if r.get("Section") == f"{config.focus_quote.upper()} Markets"
    ]
    usdt_rows = [r for r in table_rows if r.get("Section") == "USDT Markets"]

    # Clean section column
    for rows in [fiat_overview, focus_rows, usdt_rows]:
        for r in rows:
            r.pop("Section", None)

    builder = ReportBuilder("Spot Market Discovery")
    builder.source("routine", "spot_markets").tags(
        ["spot", "markets", "discovery", "fiat", "rebates"]
    )
    builder.plotly(fig)

    builder.markdown("### FIAT Markets Overview (Rebate Eligible)")
    if fiat_overview:
        builder.table(fiat_overview)

    if focus_rows:
        builder.markdown(f"### Top {config.focus_quote.upper()}-Quoted Markets")
        builder.table(focus_rows)

    builder.markdown("### Top USDT-Quoted Markets")
    if usdt_rows:
        builder.table(usdt_rows)

    await builder.save()

    return RoutineResult(text=text)
