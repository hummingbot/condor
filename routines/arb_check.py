"""CEX vs CEX order book arbitrage checker with depth visualization."""

CATEGORY = "Arbitrage"

import asyncio
import logging

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """Compare order books between two CEX exchanges to find arbitrage opportunities."""

    trading_pair: str = Field(
        default="SOL-USDC", description="Trading pair (e.g. SOL-USDC)"
    )
    amount: float = Field(default=1.0, description="Amount to quote (in base asset)")
    cex_connector: str = Field(default="binance", description="First CEX exchange")
    cex_connector_2: str = Field(default="kucoin", description="Second CEX exchange")
    ob_depth: int = Field(default=20, description="Order book depth (levels)")


async def _get_order_book(client, connector: str, trading_pair: str, depth: int) -> dict | None:
    """Fetch full order book with bids and asks."""
    try:
        result = await client.market_data.get_order_book(
            connector_name=connector,
            trading_pair=trading_pair,
            depth=depth,
        )
        if isinstance(result, dict):
            return result
        return None
    except Exception as e:
        logger.warning(f"Order book failed for {connector}/{trading_pair}: {e}")
        return None


async def _get_fill_price(client, connector: str, trading_pair: str, amount: float, is_buy: bool) -> float | None:
    """Get volume-weighted fill price from order book."""
    try:
        result = await client.market_data.get_price_for_volume(
            connector_name=connector,
            trading_pair=trading_pair,
            volume=amount,
            is_buy=is_buy,
        )
        if isinstance(result, dict):
            price = (
                result.get("result_price")
                or result.get("price")
                or result.get("average_price")
            )
            return float(price) if price else None
        return None
    except Exception as e:
        logger.warning(f"Fill price failed for {connector}/{trading_pair}: {e}")
        return None


def _parse_ob_levels(ob: dict) -> tuple[list, list]:
    """Parse order book into [(price, size, cumulative)] for bids and asks."""
    bids_raw = ob.get("bids", [])
    asks_raw = ob.get("asks", [])

    bids = []
    cum = 0.0
    for level in bids_raw:
        price, size = float(level[0]), float(level[1])
        cum += size
        bids.append((price, size, cum))

    asks = []
    cum = 0.0
    for level in asks_raw:
        price, size = float(level[0]), float(level[1])
        cum += size
        asks.append((price, size, cum))

    return bids, asks


def _ob_stats(bids: list, asks: list) -> dict:
    """Compute order book statistics."""
    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else None
    spread_pct = ((best_ask - best_bid) / best_bid * 100) if best_bid and best_ask else None

    bid_depth_usd = sum(p * s for p, s, _ in bids)
    ask_depth_usd = sum(p * s for p, s, _ in asks)

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_pct": spread_pct,
        "bid_depth_usd": bid_depth_usd,
        "ask_depth_usd": ask_depth_usd,
        "n_bid_levels": len(bids),
        "n_ask_levels": len(asks),
    }


def _spread_pct(buy_price: float, sell_price: float) -> float:
    return ((sell_price - buy_price) / buy_price) * 100


def _build_ob_chart(bids1, asks1, bids2, asks2, cex1: str, cex2: str, pair: str):
    """Build a Plotly figure with two order books stacked vertically."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(
        rows=2, cols=1,
        subplot_titles=[f"{cex1.upper()} Order Book", f"{cex2.upper()} Order Book"],
        vertical_spacing=0.12,
        shared_xaxes=True,
    )

    for row, (bids, asks, name) in enumerate(
        [(bids1, asks1, cex1), (bids2, asks2, cex2)], start=1
    ):
        if bids:
            bid_prices = [b[0] for b in bids]
            bid_cum = [b[2] for b in bids]
            fig.add_trace(
                go.Scatter(
                    x=bid_prices, y=bid_cum,
                    fill="tozeroy",
                    fillcolor="rgba(0, 200, 83, 0.15)",
                    line=dict(color="rgba(0, 200, 83, 0.8)", width=2, shape="hv"),
                    name=f"Bids",
                    hovertemplate="Price: %{x:.4f}<br>Cumul. Size: %{y:.4f}<extra></extra>",
                    showlegend=(row == 1),
                    legendgroup="bids",
                ),
                row=row, col=1,
            )

        if asks:
            ask_prices = [a[0] for a in asks]
            ask_cum = [a[2] for a in asks]
            fig.add_trace(
                go.Scatter(
                    x=ask_prices, y=ask_cum,
                    fill="tozeroy",
                    fillcolor="rgba(255, 82, 82, 0.15)",
                    line=dict(color="rgba(255, 82, 82, 0.8)", width=2, shape="hv"),
                    name=f"Asks",
                    hovertemplate="Price: %{x:.4f}<br>Cumul. Size: %{y:.4f}<extra></extra>",
                    showlegend=(row == 1),
                    legendgroup="asks",
                ),
                row=row, col=1,
            )

        # Mid-price vertical line
        if bids and asks:
            mid = (bids[0][0] + asks[0][0]) / 2
            fig.add_vline(
                x=mid, row=row, col=1,
                line=dict(color="rgba(255, 255, 255, 0.4)", width=1, dash="dash"),
                annotation_text=f"Mid: {mid:.4f}",
                annotation_font_size=10,
                annotation_font_color="rgba(255,255,255,0.6)",
            )

    # Shared x-axis range
    all_prices = (
        [b[0] for b in bids1] + [a[0] for a in asks1]
        + [b[0] for b in bids2] + [a[0] for a in asks2]
    )
    if all_prices:
        price_min, price_max = min(all_prices), max(all_prices)
        margin = (price_max - price_min) * 0.02
        fig.update_xaxes(range=[price_min - margin, price_max + margin])

    fig.update_layout(
        height=600,
        template="plotly_dark",
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        margin=dict(t=80, b=40, l=60, r=30),
    )
    fig.update_xaxes(title_text="Price", row=2, col=1)
    fig.update_yaxes(title_text="Cumulative Size", row=1, col=1)
    fig.update_yaxes(title_text="Cumulative Size", row=2, col=1)

    return fig


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Compare order books between two CEX exchanges."""
    chat_id = context._chat_id if hasattr(context, "_chat_id") else None
    client = await get_client(chat_id, context=context)

    if not client:
        return "No server available. Configure servers in /config."

    cex1 = config.cex_connector
    cex2 = config.cex_connector_2
    pair = config.trading_pair

    # Fetch everything in parallel
    ob1, ob2, fill_buy1, fill_sell1, fill_buy2, fill_sell2 = await asyncio.gather(
        _get_order_book(client, cex1, pair, config.ob_depth),
        _get_order_book(client, cex2, pair, config.ob_depth),
        _get_fill_price(client, cex1, pair, config.amount, True),
        _get_fill_price(client, cex1, pair, config.amount, False),
        _get_fill_price(client, cex2, pair, config.amount, True),
        _get_fill_price(client, cex2, pair, config.amount, False),
    )

    if not ob1 and not ob2:
        return f"Could not fetch order books from either {cex1} or {cex2}."

    # Parse order books
    bids1, asks1 = _parse_ob_levels(ob1) if ob1 else ([], [])
    bids2, asks2 = _parse_ob_levels(ob2) if ob2 else ([], [])
    stats1 = _ob_stats(bids1, asks1) if ob1 else {}
    stats2 = _ob_stats(bids2, asks2) if ob2 else {}

    # Build text output
    lines = [
        f"CEX-CEX Order Book Comparison: {pair}",
        f"{cex1.upper()} vs {cex2.upper()} | Quote: {config.amount} {pair.split('-')[0]}",
        "",
    ]

    for name, stats, fill_buy, fill_sell in [
        (cex1, stats1, fill_buy1, fill_sell1),
        (cex2, stats2, fill_buy2, fill_sell2),
    ]:
        lines.append(f"--- {name.upper()} ---")
        if stats:
            lines.append(f"  Best Bid: {stats['best_bid']:.6f} | Best Ask: {stats['best_ask']:.6f}")
            lines.append(f"  Mid: {stats['mid']:.6f} | Spread: {stats['spread_pct']:.4f}%")
            lines.append(f"  Depth: Bid ${stats['bid_depth_usd']:,.0f} | Ask ${stats['ask_depth_usd']:,.0f} ({stats['n_bid_levels']}/{stats['n_ask_levels']} levels)")
        if fill_buy:
            lines.append(f"  Fill BUY  {config.amount} @ {fill_buy:.6f}")
        if fill_sell:
            lines.append(f"  Fill SELL {config.amount} @ {fill_sell:.6f}")
        if not stats and not fill_buy:
            lines.append("  No data available")
        lines.append("")

    # Arb analysis
    lines.append("--- Arbitrage ---")
    opportunities = []

    if fill_buy1 and fill_sell2:
        s = _spread_pct(fill_buy1, fill_sell2)
        profit = (fill_sell2 - fill_buy1) * config.amount
        label = f"BUY {cex1} -> SELL {cex2}: {s:+.4f}% (${profit:.4f})"
        (opportunities if s > 0 else lines).append(label)

    if fill_buy2 and fill_sell1:
        s = _spread_pct(fill_buy2, fill_sell1)
        profit = (fill_sell1 - fill_buy2) * config.amount
        label = f"BUY {cex2} -> SELL {cex1}: {s:+.4f}% (${profit:.4f})"
        (opportunities if s > 0 else lines).append(label)

    if stats1.get("mid") and stats2.get("mid"):
        mid_diff = _spread_pct(stats1["mid"], stats2["mid"])
        lines.append(f"Mid-price diff: {mid_diff:+.4f}% ({cex1}: {stats1['mid']:.6f}, {cex2}: {stats2['mid']:.6f})")

    if opportunities:
        lines.append("")
        lines.append("OPPORTUNITIES FOUND:")
        lines.extend(opportunities)
    else:
        lines.append("No profitable arbitrage at current depth.")

    # Generate report
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"CEX Arb: {pair} ({cex1} vs {cex2})")
        builder.source("routine", "arb_check").tags(["arbitrage", "cex-cex", pair, cex1, cex2])
        builder.manual_order()

        # KPIs
        if stats1.get("spread_pct") is not None:
            builder.kpi(f"{cex1} Spread", f"{stats1['spread_pct']:.4f}%")
        if stats2.get("spread_pct") is not None:
            builder.kpi(f"{cex2} Spread", f"{stats2['spread_pct']:.4f}%")
        if stats1.get("mid") and stats2.get("mid"):
            md = _spread_pct(stats1["mid"], stats2["mid"])
            builder.kpi("Mid-Price Diff", f"{md:+.4f}%", trend="up" if abs(md) > 0.05 else "neutral")
        if fill_buy1 and fill_sell2:
            s = _spread_pct(fill_buy1, fill_sell2)
            builder.kpi(f"BUY {cex1} -> SELL {cex2}", f"{s:+.4f}%", trend="up" if s > 0 else "down")
        if fill_buy2 and fill_sell1:
            s = _spread_pct(fill_buy2, fill_sell1)
            builder.kpi(f"BUY {cex2} -> SELL {cex1}", f"{s:+.4f}%", trend="up" if s > 0 else "down")

        # Order book depth chart
        if (bids1 or asks1) and (bids2 or asks2):
            fig = _build_ob_chart(bids1, asks1, bids2, asks2, cex1, cex2, pair)
            builder.plotly(fig)

        # Comparison table
        builder.markdown(f"## Order Book Comparison ({config.ob_depth} levels)")
        table_rows = []
        for name, stats, fb, fs in [
            (cex1, stats1, fill_buy1, fill_sell1),
            (cex2, stats2, fill_buy2, fill_sell2),
        ]:
            row = {"Exchange": name.upper()}
            if stats:
                row["Best Bid"] = f"{stats['best_bid']:.6f}"
                row["Best Ask"] = f"{stats['best_ask']:.6f}"
                row["Mid Price"] = f"{stats['mid']:.6f}"
                row["Spread"] = f"{stats['spread_pct']:.4f}%"
                row["Bid Depth (USD)"] = f"${stats['bid_depth_usd']:,.0f}"
                row["Ask Depth (USD)"] = f"${stats['ask_depth_usd']:,.0f}"
            else:
                row["Best Bid"] = "N/A"
                row["Best Ask"] = "N/A"
            row[f"Fill BUY {config.amount}"] = f"{fb:.6f}" if fb else "N/A"
            row[f"Fill SELL {config.amount}"] = f"{fs:.6f}" if fs else "N/A"
            table_rows.append(row)
        builder.table(table_rows)

        # Top levels detail per exchange
        for name, bids, asks in [(cex1, bids1, asks1), (cex2, bids2, asks2)]:
            n = min(10, max(len(bids), len(asks)))
            builder.markdown(f"### {name.upper()} - Top {n} Levels")
            levels = []
            for i in range(n):
                row = {"#": i + 1}
                if i < len(bids):
                    row["Bid Price"] = f"{bids[i][0]:.6f}"
                    row["Bid Size"] = f"{bids[i][1]:.4f}"
                    row["Bid Cumul."] = f"{bids[i][2]:.4f}"
                if i < len(asks):
                    row["Ask Price"] = f"{asks[i][0]:.6f}"
                    row["Ask Size"] = f"{asks[i][1]:.4f}"
                    row["Ask Cumul."] = f"{asks[i][2]:.4f}"
                levels.append(row)
            if levels:
                builder.table(levels)

        builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return "\n".join(lines)
