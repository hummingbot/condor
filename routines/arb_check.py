"""CEX vs CEX order book arbitrage checker with depth visualization."""

CATEGORY = "Arbitrage"

import asyncio
import logging
from itertools import combinations

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """Compare order books across multiple CEX exchanges to find arbitrage opportunities."""

    trading_pair: str = Field(
        default="SOL-USDC", description="Trading pair (e.g. SOL-USDC)"
    )
    exchanges: str = Field(
        default="binance,kucoin",
        description="Comma-separated exchanges (e.g. binance,kucoin,htx,gate_io)",
    )
    amount: float = Field(default=1.0, description="Amount to quote (in base asset)")
    ob_depth: int = Field(default=20, description="Order book depth (levels)")


def _parse_exchanges(raw: str) -> list[str]:
    """Parse comma-separated exchange list, stripping whitespace."""
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


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


def _parse_level(level) -> tuple[float, float]:
    """Parse a single OB level from list [price, size] or dict {price, quantity}."""
    if isinstance(level, dict):
        price = float(level.get("price", level.get("Price", 0)))
        size = float(level.get("quantity", level.get("size", level.get("amount", level.get("Quantity", 0)))))
        return price, size
    return float(level[0]), float(level[1])


def _parse_ob_levels(ob: dict) -> tuple[list, list]:
    """Parse order book into [(price, size, cumulative)] for bids and asks."""
    bids_raw = ob.get("bids", [])
    asks_raw = ob.get("asks", [])

    bids = []
    cum = 0.0
    for level in bids_raw:
        price, size = _parse_level(level)
        cum += size
        bids.append((price, size, cum))

    asks = []
    cum = 0.0
    for level in asks_raw:
        price, size = _parse_level(level)
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


def _build_ob_chart(exchange_data: list[tuple[str, list, list]], pair: str):
    """Build a Plotly figure with N order books stacked vertically (one per exchange)."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    n = len(exchange_data)
    fig = make_subplots(
        rows=n, cols=1,
        subplot_titles=[f"{name.upper()} Order Book" for name, _, _ in exchange_data],
        vertical_spacing=0.08 if n <= 4 else 0.05,
        shared_xaxes=True,
    )

    for row, (name, bids, asks) in enumerate(exchange_data, start=1):
        if bids:
            bid_prices = [b[0] for b in bids]
            bid_cum = [b[2] for b in bids]
            fig.add_trace(
                go.Scatter(
                    x=bid_prices, y=bid_cum,
                    fill="tozeroy",
                    fillcolor="rgba(0, 200, 83, 0.15)",
                    line=dict(color="rgba(0, 200, 83, 0.8)", width=2, shape="hv"),
                    name="Bids",
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
                    name="Asks",
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
    all_prices = []
    for _, bids, asks in exchange_data:
        all_prices.extend(b[0] for b in bids)
        all_prices.extend(a[0] for a in asks)
    if all_prices:
        price_min, price_max = min(all_prices), max(all_prices)
        margin = (price_max - price_min) * 0.02
        fig.update_xaxes(range=[price_min - margin, price_max + margin])

    row_height = 250
    fig.update_layout(
        height=max(400, row_height * n),
        template="plotly_dark",
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"),
        legend=dict(orientation="h", yanchor="top", y=-0.05, xanchor="center", x=0.5),
        margin=dict(t=80, b=40, l=60, r=30),
    )
    fig.update_xaxes(title_text="Price", row=n, col=1)
    for row in range(1, n + 1):
        fig.update_yaxes(title_text="Cumul. Size", row=row, col=1)

    return fig


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Compare order books across multiple CEX exchanges."""
    chat_id = context._chat_id if hasattr(context, "_chat_id") else None
    client = await get_client(chat_id, context=context)

    if not client:
        return "No server available. Configure servers in /config."

    exchanges = _parse_exchanges(config.exchanges)
    if len(exchanges) < 2:
        return "Need at least 2 exchanges (comma-separated). Example: binance,kucoin,htx"

    pair = config.trading_pair

    # Fetch all order books and fill prices in parallel
    tasks = []
    for ex in exchanges:
        tasks.append(_get_order_book(client, ex, pair, config.ob_depth))
        tasks.append(_get_fill_price(client, ex, pair, config.amount, True))   # buy
        tasks.append(_get_fill_price(client, ex, pair, config.amount, False))  # sell

    results = await asyncio.gather(*tasks)

    # Unpack: every 3 results = (ob, fill_buy, fill_sell) per exchange
    ex_data = {}
    for i, ex in enumerate(exchanges):
        ob = results[i * 3]
        fill_buy = results[i * 3 + 1]
        fill_sell = results[i * 3 + 2]
        bids, asks = _parse_ob_levels(ob) if ob else ([], [])
        stats = _ob_stats(bids, asks) if ob else {}
        ex_data[ex] = {
            "ob": ob, "bids": bids, "asks": asks, "stats": stats,
            "fill_buy": fill_buy, "fill_sell": fill_sell,
        }

    # Check at least some data came back
    if not any(d["ob"] for d in ex_data.values()):
        return f"Could not fetch order books from any exchange: {', '.join(exchanges)}"

    # Build text output
    lines = [
        f"CEX Order Book Comparison: {pair}",
        f"Exchanges: {', '.join(e.upper() for e in exchanges)} | Quote: {config.amount} {pair.split('-')[0]}",
        "",
    ]

    for ex in exchanges:
        d = ex_data[ex]
        lines.append(f"--- {ex.upper()} ---")
        if d["stats"]:
            s = d["stats"]
            lines.append(f"  Best Bid: {s['best_bid']:.6f} | Best Ask: {s['best_ask']:.6f}")
            lines.append(f"  Mid: {s['mid']:.6f} | Spread: {s['spread_pct']:.4f}%")
            lines.append(f"  Depth: Bid ${s['bid_depth_usd']:,.0f} | Ask ${s['ask_depth_usd']:,.0f} ({s['n_bid_levels']}/{s['n_ask_levels']} levels)")
        if d["fill_buy"]:
            lines.append(f"  Fill BUY  {config.amount} @ {d['fill_buy']:.6f}")
        if d["fill_sell"]:
            lines.append(f"  Fill SELL {config.amount} @ {d['fill_sell']:.6f}")
        if not d["stats"] and not d["fill_buy"]:
            lines.append("  No data available")
        lines.append("")

    # Arb analysis across all pairs
    lines.append("--- Arbitrage Matrix ---")
    opportunities = []

    for ex_a, ex_b in combinations(exchanges, 2):
        da, db = ex_data[ex_a], ex_data[ex_b]

        # Buy on A, sell on B
        if da["fill_buy"] and db["fill_sell"]:
            s = _spread_pct(da["fill_buy"], db["fill_sell"])
            profit = (db["fill_sell"] - da["fill_buy"]) * config.amount
            label = f"BUY {ex_a.upper()} -> SELL {ex_b.upper()}: {s:+.4f}% (${profit:.4f})"
            (opportunities if s > 0 else lines).append(label)

        # Buy on B, sell on A
        if db["fill_buy"] and da["fill_sell"]:
            s = _spread_pct(db["fill_buy"], da["fill_sell"])
            profit = (da["fill_sell"] - db["fill_buy"]) * config.amount
            label = f"BUY {ex_b.upper()} -> SELL {ex_a.upper()}: {s:+.4f}% (${profit:.4f})"
            (opportunities if s > 0 else lines).append(label)

    # Mid-price diffs
    mids = {ex: d["stats"]["mid"] for ex, d in ex_data.items() if d["stats"].get("mid")}
    if len(mids) >= 2:
        lines.append("")
        for ex_a, ex_b in combinations(mids.keys(), 2):
            diff = _spread_pct(mids[ex_a], mids[ex_b])
            lines.append(f"Mid diff {ex_a.upper()} vs {ex_b.upper()}: {diff:+.4f}%")

    if opportunities:
        lines.append("")
        lines.append("OPPORTUNITIES FOUND:")
        lines.extend(opportunities)
    else:
        lines.append("")
        lines.append("No profitable arbitrage at current depth.")

    # Generate report
    try:
        from condor.reports import ReportBuilder

        ex_label = " vs ".join(e.upper() for e in exchanges)
        builder = ReportBuilder(f"CEX Arb: {pair} ({ex_label})")
        builder.source("routine", "arb_check").tags(["arbitrage", "cex-cex", pair] + exchanges)
        builder.manual_order()

        # KPIs - spreads per exchange
        for ex in exchanges:
            s = ex_data[ex]["stats"]
            if s.get("spread_pct") is not None:
                builder.kpi(f"{ex.upper()} Spread", f"{s['spread_pct']:.4f}%")

        # KPIs - mid diffs
        if len(mids) >= 2:
            mid_vals = list(mids.values())
            max_diff = max(mid_vals) - min(mid_vals)
            max_diff_pct = (max_diff / min(mid_vals)) * 100
            builder.kpi("Max Mid-Price Diff", f"{max_diff_pct:+.4f}%",
                        trend="up" if max_diff_pct > 0.05 else "neutral")

        # KPIs - arb opportunities
        for ex_a, ex_b in combinations(exchanges, 2):
            da, db = ex_data[ex_a], ex_data[ex_b]
            if da["fill_buy"] and db["fill_sell"]:
                s = _spread_pct(da["fill_buy"], db["fill_sell"])
                builder.kpi(f"BUY {ex_a.upper()} -> SELL {ex_b.upper()}", f"{s:+.4f}%",
                            trend="up" if s > 0 else "down")
            if db["fill_buy"] and da["fill_sell"]:
                s = _spread_pct(db["fill_buy"], da["fill_sell"])
                builder.kpi(f"BUY {ex_b.upper()} -> SELL {ex_a.upper()}", f"{s:+.4f}%",
                            trend="up" if s > 0 else "down")

        # Order book depth chart (all exchanges stacked)
        chart_data = [
            (ex, ex_data[ex]["bids"], ex_data[ex]["asks"])
            for ex in exchanges if ex_data[ex]["ob"]
        ]
        if len(chart_data) >= 2:
            fig = _build_ob_chart(chart_data, pair)
            builder.plotly(fig)

        # Comparison table
        builder.markdown(f"## Order Book Comparison ({config.ob_depth} levels)")
        table_rows = []
        for ex in exchanges:
            d = ex_data[ex]
            row = {"Exchange": ex.upper()}
            if d["stats"]:
                s = d["stats"]
                row["Best Bid"] = f"{s['best_bid']:.6f}"
                row["Best Ask"] = f"{s['best_ask']:.6f}"
                row["Mid Price"] = f"{s['mid']:.6f}"
                row["Spread"] = f"{s['spread_pct']:.4f}%"
                row["Bid Depth (USD)"] = f"${s['bid_depth_usd']:,.0f}"
                row["Ask Depth (USD)"] = f"${s['ask_depth_usd']:,.0f}"
            else:
                row["Best Bid"] = "N/A"
                row["Best Ask"] = "N/A"
            row[f"Fill BUY {config.amount}"] = f"{d['fill_buy']:.6f}" if d["fill_buy"] else "N/A"
            row[f"Fill SELL {config.amount}"] = f"{d['fill_sell']:.6f}" if d["fill_sell"] else "N/A"
            table_rows.append(row)
        builder.table(table_rows)

        # Arb matrix table
        arb_rows = []
        for ex_a, ex_b in combinations(exchanges, 2):
            da, db = ex_data[ex_a], ex_data[ex_b]
            row = {"Route": f"{ex_a.upper()} <-> {ex_b.upper()}"}
            if da["fill_buy"] and db["fill_sell"]:
                s = _spread_pct(da["fill_buy"], db["fill_sell"])
                row[f"BUY {ex_a.upper()} SELL {ex_b.upper()}"] = f"{s:+.4f}%"
            else:
                row[f"BUY {ex_a.upper()} SELL {ex_b.upper()}"] = "N/A"
            if db["fill_buy"] and da["fill_sell"]:
                s = _spread_pct(db["fill_buy"], da["fill_sell"])
                row[f"BUY {ex_b.upper()} SELL {ex_a.upper()}"] = f"{s:+.4f}%"
            else:
                row[f"BUY {ex_b.upper()} SELL {ex_a.upper()}"] = "N/A"
            if da["stats"].get("mid") and db["stats"].get("mid"):
                row["Mid Diff"] = f"{_spread_pct(da['stats']['mid'], db['stats']['mid']):+.4f}%"
            arb_rows.append(row)
        if arb_rows:
            builder.markdown("## Arbitrage Matrix")
            builder.table(arb_rows)

        # Top levels detail per exchange
        for ex in exchanges:
            d = ex_data[ex]
            bids, asks = d["bids"], d["asks"]
            n = min(10, max(len(bids), len(asks)))
            if n == 0:
                continue
            builder.markdown(f"### {ex.upper()} - Top {n} Levels")
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
