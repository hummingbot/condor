"""Identify top movers in perpetual futures by 24h price change.

Flags potential delistings via exchange status + extreme price moves.
"""

CATEGORY = "Market Data"

import logging

import aiohttp
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from routines.base import RoutineResult

logger = logging.getLogger(__name__)

BINANCE_FUTURES_TICKER = "https://fapi.binance.com/fapi/v1/ticker/24hr"
BINANCE_FUTURES_EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
DELIST_THRESHOLD_PCT = 200  # flag moves above this as potential delisting


class Config(BaseModel):
    """Top perpetual movers by 24h price change — finds the most volatile markets."""

    top_n: int = Field(default=15, description="Number of top gainers/losers to show")
    min_volume_usd: float = Field(
        default=5_000_000, description="Min 24h quote volume (USD)"
    )
    show_losers: bool = Field(default=True, description="Also show top losers")
    send_to_telegram: bool = Field(
        default=True, description="Also send report via Telegram"
    )


def format_volume(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    return f"${v / 1_000:.0f}K"


def format_price(p: float) -> str:
    if p >= 1000:
        return f"${p:,.0f}"
    if p >= 1:
        return f"${p:.2f}"
    if p >= 0.01:
        return f"${p:.4f}"
    return f"${p:.6f}"


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Fetch 24h tickers and rank by price change percent."""

    # Fetch tickers + exchange info in parallel
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(BINANCE_FUTURES_TICKER) as resp:
                resp.raise_for_status()
                tickers = await resp.json()
            async with session.get(BINANCE_FUTURES_EXCHANGE_INFO) as resp:
                resp.raise_for_status()
                exchange_info = await resp.json()
    except Exception as e:
        return f"Failed to fetch data: {e}"

    # Build set of non-tradable symbols (SETTLING, PRE_DELIVERING, etc.)
    delist_symbols = set()
    for s in exchange_info.get("symbols", []):
        if s.get("status") != "TRADING":
            delist_symbols.add(s.get("symbol", ""))

    # Parse and filter USDT pairs with minimum volume
    pairs = []
    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        quote_vol = float(t.get("quoteVolume", 0))
        if quote_vol < config.min_volume_usd:
            continue

        base = symbol.replace("USDT", "")
        high = float(t.get("highPrice", 0))
        low = float(t.get("lowPrice", 0))
        last = float(t.get("lastPrice", 0))
        range_pct = ((high - low) / low * 100) if low > 0 else 0

        change_pct = float(t.get("priceChangePercent", 0))
        is_delist = symbol in delist_symbols or abs(change_pct) >= DELIST_THRESHOLD_PCT

        pairs.append(
            {
                "pair": f"{base}-USDT",
                "price": last,
                "change_pct": change_pct,
                "volume_usd": quote_vol,
                "high": high,
                "low": low,
                "range_pct": range_pct,
                "delist": is_delist,
            }
        )

    if not pairs:
        return "No pairs found matching volume criteria."

    # Separate delisting pairs from tradable pairs
    tradable = [p for p in pairs if not p["delist"]]
    delisting = [p for p in pairs if p["delist"]]

    # Sort by price change
    gainers = sorted(tradable, key=lambda x: x["change_pct"], reverse=True)[
        : config.top_n
    ]
    losers = sorted(tradable, key=lambda x: x["change_pct"])[: config.top_n]

    def format_table(items: list[dict], title: str) -> list[str]:
        """Build an aligned table from a list of pair dicts."""
        if not items:
            return []
        # Compute column widths
        col_pair = max(len(p["pair"]) for p in items)
        col_chg = max(len(f"{p['change_pct']:+.1f}%") for p in items)
        col_price = max(len(format_price(p["price"])) for p in items)
        col_vol = max(len(format_volume(p["volume_usd"])) for p in items)
        col_range = max(len(f"{p['range_pct']:.1f}%") for p in items)

        header = (
            f"{'Pair':<{col_pair}}  "
            f"{'Chg':>{col_chg}}  "
            f"{'Price':>{col_price}}  "
            f"{'Vol':>{col_vol}}  "
            f"{'Range':>{col_range}}"
        )
        sep = "─" * len(header)
        rows = [title, sep, header, sep]
        for p in items:
            chg = f"{p['change_pct']:+.1f}%"
            rows.append(
                f"{p['pair']:<{col_pair}}  "
                f"{chg:>{col_chg}}  "
                f"{format_price(p['price']):>{col_price}}  "
                f"{format_volume(p['volume_usd']):>{col_vol}}  "
                f"{p['range_pct']:.1f}%".rjust(col_range + 2).rstrip()
            )
        return rows

    lines = [
        f"Top Movers — Binance Perpetuals",
        f"{len(tradable)} pairs · vol > {format_volume(config.min_volume_usd)}"
        + (f" · {len(delisting)} delisting excluded" if delisting else ""),
        "",
    ]

    lines.extend(format_table(gainers, f"▲ TOP {config.top_n} GAINERS"))

    if config.show_losers:
        lines.append("")
        lines.extend(format_table(losers, f"▼ TOP {config.top_n} LOSERS"))

    if delisting:
        delisting.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
        lines.append("")
        lines.extend(format_table(delisting, f"⚠ DELISTING ({len(delisting)})"))

    report = "\n".join(lines)

    if config.send_to_telegram and getattr(context, "bot", None):
        chat_id = context._chat_id
        # Split into chunks if too long for Telegram (4096 char limit)
        if len(report) <= 4096:
            await context.bot.send_message(
                chat_id, f"```\n{report}\n```", parse_mode="MarkdownV2"
            )
        else:
            for i in range(0, len(report), 4000):
                chunk = report[i : i + 4000]
                await context.bot.send_message(
                    chat_id, f"```\n{chunk}\n```", parse_mode="MarkdownV2"
                )

    # Build structured table data for web dashboard
    table_cols = ["pair", "change_pct", "price", "volume_usd", "range_pct"]
    all_rows = gainers + (losers if config.show_losers else [])
    table_data = [{c: row[c] for c in table_cols} for row in all_rows]

    try:
        import plotly.graph_objects as go

        from condor.reports import ReportBuilder

        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=[p["pair"] for p in gainers],
                y=[p["change_pct"] for p in gainers],
                marker_color="#3fb950",
                name="Gainers",
            )
        )
        if config.show_losers:
            fig.add_trace(
                go.Bar(
                    x=[p["pair"] for p in losers],
                    y=[p["change_pct"] for p in losers],
                    marker_color="#f85149",
                    name="Losers",
                )
            )
        fig.update_layout(
            title="24h Price Change %",
            paper_bgcolor="#0d1117",
            plot_bgcolor="#161b22",
            font=dict(color="#c9d1d9", size=10),
            yaxis_title="Change %",
            xaxis_tickangle=-45,
            margin=dict(l=50, r=30, t=50, b=80),
        )

        report_table = [
            {
                "Pair": p["pair"],
                "Change": f"{p['change_pct']:+.1f}%",
                "Price": format_price(p["price"]),
                "Volume": format_volume(p["volume_usd"]),
                "Range": f"{p['range_pct']:.1f}%",
            }
            for p in all_rows
        ]

        builder = ReportBuilder("Top Movers — Binance Perpetuals")
        builder.source("routine", "top_movers").tags(["market", "movers"])
        builder.kpi("Pairs", str(len(tradable)))
        if gainers:
            builder.kpi(
                "Top Gainer",
                gainers[0]["pair"],
                delta=f"{gainers[0]['change_pct']:+.1f}%",
                trend="up",
            )
        if losers:
            builder.kpi(
                "Top Loser",
                losers[0]["pair"],
                delta=f"{losers[0]['change_pct']:+.1f}%",
                trend="down",
            )
        builder.markdown(
            f"{len(tradable)} pairs · vol > {format_volume(config.min_volume_usd)}"
        )
        builder.plotly(fig)
        builder.table(report_table)
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    # Build KPI sections for inline dashboard view
    kpi_sections = [{"type": "kpi", "label": "Pairs", "value": str(len(tradable))}]
    if gainers:
        kpi_sections.append(
            {
                "type": "kpi",
                "label": "Top Gainer",
                "value": gainers[0]["pair"],
                "delta": f"{gainers[0]['change_pct']:+.1f}%",
                "trend": "up",
            }
        )
    if losers:
        kpi_sections.append(
            {
                "type": "kpi",
                "label": "Top Loser",
                "value": losers[0]["pair"],
                "delta": f"{losers[0]['change_pct']:+.1f}%",
                "trend": "down",
            }
        )

    return RoutineResult(
        text=report,
        table_data=table_data,
        table_columns=table_cols,
        sections=kpi_sections,
    )
