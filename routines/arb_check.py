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
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


async def _get_order_book(client, connector: str, trading_pair: str, depth: int) -> dict | None:
    try:
        result = await client.market_data.get_order_book(
            connector_name=connector, trading_pair=trading_pair, depth=depth,
        )
        return result if isinstance(result, dict) else None
    except Exception as e:
        logger.warning(f"Order book failed for {connector}/{trading_pair}: {e}")
        return None


async def _get_fill_price(client, connector: str, trading_pair: str, amount: float, is_buy: bool) -> float | None:
    try:
        result = await client.market_data.get_price_for_volume(
            connector_name=connector, trading_pair=trading_pair, volume=amount, is_buy=is_buy,
        )
        if isinstance(result, dict):
            price = result.get("result_price") or result.get("price") or result.get("average_price")
            return float(price) if price else None
        return None
    except Exception as e:
        logger.warning(f"Fill price failed for {connector}/{trading_pair}: {e}")
        return None


def _parse_level(level) -> tuple[float, float]:
    if isinstance(level, dict):
        price = float(level.get("price", level.get("Price", 0)))
        size = float(level.get("quantity", level.get("size", level.get("amount", level.get("Quantity", 0)))))
        return price, size
    return float(level[0]), float(level[1])


def _parse_ob_levels(ob: dict) -> tuple[list, list]:
    bids_raw = ob.get("bids", [])
    asks_raw = ob.get("asks", [])

    bids, cum = [], 0.0
    for level in bids_raw:
        price, size = _parse_level(level)
        cum += size
        bids.append((price, size, cum))

    asks, cum = [], 0.0
    for level in asks_raw:
        price, size = _parse_level(level)
        cum += size
        asks.append((price, size, cum))

    return bids, asks


def _ob_stats(bids: list, asks: list) -> dict:
    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else None
    spread_pct = ((best_ask - best_bid) / best_bid * 100) if best_bid and best_ask else None
    bid_depth_usd = sum(p * s for p, s, _ in bids)
    ask_depth_usd = sum(p * s for p, s, _ in asks)

    return {
        "best_bid": best_bid, "best_ask": best_ask, "mid": mid,
        "spread_pct": spread_pct, "bid_depth_usd": bid_depth_usd,
        "ask_depth_usd": ask_depth_usd, "n_bid_levels": len(bids), "n_ask_levels": len(asks),
    }


def _spread_pct(buy_price: float, sell_price: float) -> float:
    return ((sell_price - buy_price) / buy_price) * 100


def _compute_arb_routes(valid_exchanges: list[str], ex_data: dict, amount: float) -> list[dict]:
    """Compute all arb routes across exchange pairs. Returns list of dicts with route/spread/profit."""
    routes = []
    for ex_a, ex_b in combinations(valid_exchanges, 2):
        da, db = ex_data[ex_a], ex_data[ex_b]
        if da["fill_buy"] and db["fill_sell"]:
            s = _spread_pct(da["fill_buy"], db["fill_sell"])
            profit = (db["fill_sell"] - da["fill_buy"]) * amount
            routes.append({"route": f"BUY {ex_a.upper()} → SELL {ex_b.upper()}", "spread": s, "profit": profit})
        if db["fill_buy"] and da["fill_sell"]:
            s = _spread_pct(db["fill_buy"], da["fill_sell"])
            profit = (da["fill_sell"] - db["fill_buy"]) * amount
            routes.append({"route": f"BUY {ex_b.upper()} → SELL {ex_a.upper()}", "spread": s, "profit": profit})
    return routes


def _filter_and_sort_exchanges(exchanges: list[str], ex_data: dict) -> tuple[list[str], list[tuple[str, str]]]:
    """Filter out exchanges with invalid data and sort by spread (smallest first).

    Returns (valid_exchanges, invalid_list) where invalid_list is [(name, reason), ...].
    """
    # First pass: filter exchanges with no OB or no prices
    candidates = []
    invalid = []
    for ex in exchanges:
        d = ex_data[ex]
        s = d["stats"]
        if not d["ob"]:
            invalid.append((ex, "Order book fetch failed"))
        elif not s.get("best_bid") or not s.get("best_ask"):
            invalid.append((ex, "Missing bid/ask prices"))
        else:
            candidates.append(ex)

    # Second pass: cross-validate prices — filter outliers where mid differs from median by >90%
    if len(candidates) >= 2:
        mids = [(ex, ex_data[ex]["stats"]["mid"]) for ex in candidates]
        sorted_mids = sorted(m for _, m in mids)
        median_mid = sorted_mids[len(sorted_mids) // 2]
        still_valid = []
        for ex in candidates:
            mid = ex_data[ex]["stats"]["mid"]
            if median_mid > 0 and abs(mid - median_mid) / median_mid > 0.5:
                invalid.append((ex, f"Price outlier (mid={mid:.8g} vs median={median_mid:.6f})"))
            else:
                still_valid.append(ex)
        candidates = still_valid

    # Sort by spread (smallest first)
    candidates.sort(key=lambda ex: ex_data[ex]["stats"].get("spread_pct") or float("inf"))

    return candidates, invalid


def _build_ob_chart(exchange_data: list[tuple[str, list, list]], pair: str):
    """Build Plotly figure: left = shared-x OB depth, right = L1 dumbbell BPS chart."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    n = len(exchange_data)

    # Reference mid = first exchange (sorted by tightest spread)
    ref_name = exchange_data[0][0]
    ref_bids, ref_asks = exchange_data[0][1], exchange_data[0][2]
    ref_mid = (ref_bids[0][0] + ref_asks[0][0]) / 2 if ref_bids and ref_asks else None

    vs = 0.08 if n <= 4 else 0.05

    fig = make_subplots(
        rows=n, cols=2,
        shared_xaxes="columns",
        vertical_spacing=vs,
        horizontal_spacing=0.10,
        column_widths=[0.55, 0.45],
    )

    c_bid = "rgba(0, 200, 83, 0.8)"
    c_ask = "rgba(255, 82, 82, 0.8)"
    f_bid = "rgba(0, 200, 83, 0.15)"
    f_ask = "rgba(255, 82, 82, 0.15)"

    for row, (name, bids, asks) in enumerate(exchange_data, start=1):
        # --- Left column: order book depth ---
        if bids:
            fig.add_trace(go.Scatter(
                x=[b[0] for b in bids], y=[b[2] for b in bids],
                fill="tozeroy", fillcolor=f_bid,
                line=dict(color=c_bid, width=2, shape="hv"),
                name="Bids", hovertemplate="Price: %{x:.6f}<br>Cumul: %{y:.2f}<extra></extra>",
                showlegend=(row == 1), legendgroup="bids",
            ), row=row, col=1)

        if asks:
            fig.add_trace(go.Scatter(
                x=[a[0] for a in asks], y=[a[2] for a in asks],
                fill="tozeroy", fillcolor=f_ask,
                line=dict(color=c_ask, width=2, shape="hv"),
                name="Asks", hovertemplate="Price: %{x:.6f}<br>Cumul: %{y:.2f}<extra></extra>",
                showlegend=(row == 1), legendgroup="asks",
            ), row=row, col=1)

        if bids and asks:
            mid = (bids[0][0] + asks[0][0]) / 2
            fig.add_vline(
                x=mid, row=row, col=1,
                line=dict(color="rgba(255,255,255,0.4)", width=1, dash="dash"),
            )

        # --- Right column: L1 dumbbell BPS ---
        if bids and asks and ref_mid and ref_mid > 0:
            bid_bps = ((bids[0][0] - ref_mid) / ref_mid) * 10000
            ask_bps = ((asks[0][0] - ref_mid) / ref_mid) * 10000
            mid_bps = (((bids[0][0] + asks[0][0]) / 2 - ref_mid) / ref_mid) * 10000

            # Connecting line between bid and ask
            fig.add_trace(go.Scatter(
                x=[bid_bps, ask_bps], y=[0, 0],
                mode="lines",
                line=dict(color="rgba(255,255,255,0.25)", width=4),
                showlegend=False, hoverinfo="skip",
            ), row=row, col=2)

            # Bid dot with BPS label
            fig.add_trace(go.Scatter(
                x=[bid_bps], y=[0],
                mode="markers+text",
                marker=dict(color=c_bid, size=13, line=dict(width=1.5, color="#1a1a2e")),
                text=[f"{bid_bps:+.1f}"],
                textposition="bottom center",
                textfont=dict(size=10, color=c_bid),
                name="L1 Bid", showlegend=(row == 1), legendgroup="l1_bid",
                hovertemplate=f"Bid: {bids[0][0]:.6f} ({bid_bps:+.1f} BPS)<extra>{name.upper()}</extra>",
            ), row=row, col=2)

            # Ask dot with BPS label
            fig.add_trace(go.Scatter(
                x=[ask_bps], y=[0],
                mode="markers+text",
                marker=dict(color=c_ask, size=13, line=dict(width=1.5, color="#1a1a2e")),
                text=[f"{ask_bps:+.1f}"],
                textposition="top center",
                textfont=dict(size=10, color=c_ask),
                name="L1 Ask", showlegend=(row == 1), legendgroup="l1_ask",
                hovertemplate=f"Ask: {asks[0][0]:.6f} ({ask_bps:+.1f} BPS)<extra>{name.upper()}</extra>",
            ), row=row, col=2)

            # Mid diamond for non-reference exchanges
            if row > 1 and abs(mid_bps) > 0.5:
                fig.add_trace(go.Scatter(
                    x=[mid_bps], y=[0],
                    mode="markers+text",
                    marker=dict(color="rgba(88, 166, 255, 0.9)", size=9, symbol="diamond",
                                line=dict(width=1, color="#1a1a2e")),
                    text=[f"mid {mid_bps:+.1f}"],
                    textposition="bottom center",
                    textfont=dict(size=9, color="rgba(88, 166, 255, 0.7)"),
                    showlegend=False,
                    hovertemplate=f"Mid: {mid:.6f} ({mid_bps:+.1f} BPS)<extra>{name.upper()}</extra>",
                ), row=row, col=2)

            # Zero reference line
            fig.add_vline(
                x=0, row=row, col=2,
                line=dict(color="rgba(255,255,255,0.4)", width=1, dash="dash"),
            )

    # Shared x-axis range for left column (OB depth charts)
    all_prices = []
    for _, bids, asks in exchange_data:
        all_prices.extend(b[0] for b in bids)
        all_prices.extend(a[0] for a in asks)
    if all_prices:
        pmin, pmax = min(all_prices), max(all_prices)
        margin = (pmax - pmin) * 0.02
        for row in range(1, n + 1):
            fig.update_xaxes(range=[pmin - margin, pmax + margin], row=row, col=1)

    # Exchange name labels on the left side of each row
    row_h = (1 - (n - 1) * vs) / n
    for i, (name, _, _) in enumerate(exchange_data):
        row_top = 1 - i * (row_h + vs)
        row_center = row_top - row_h / 2
        fig.add_annotation(
            text=f"<b>{name.upper()}</b>",
            xref="paper", yref="paper",
            x=-0.07, y=row_center,
            showarrow=False,
            font=dict(size=13, color="#e0e0e0"),
            textangle=-90,
        )

    # Column header annotations
    fig.add_annotation(
        text="<b>Order Book Depth</b>", xref="paper", yref="paper",
        x=0.275, y=1.04, showarrow=False,
        font=dict(size=14, color="#e0e0e0"),
    )
    fig.add_annotation(
        text=f"<b>L1 BPS from {ref_name.upper()} Mid</b>",
        xref="paper", yref="paper",
        x=0.80, y=1.04, showarrow=False,
        font=dict(size=14, color="#e0e0e0"),
    )

    row_height = 220
    fig.update_layout(
        height=max(400, row_height * n),
        template="plotly_dark",
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"),
        legend=dict(orientation="h", yanchor="top", y=-0.06, xanchor="center", x=0.5),
        margin=dict(t=60, b=50, l=80, r=30),
    )

    # Axis labels — only on bottom row (shared x handles the rest)
    fig.update_xaxes(title_text="Price", row=n, col=1)
    fig.update_xaxes(title_text="BPS from Reference", row=n, col=2)
    for row in range(1, n + 1):
        # Hide y-axis on right column (dumbbell doesn't need it)
        fig.update_yaxes(
            showticklabels=False, showgrid=False, zeroline=False,
            range=[-1, 1], row=row, col=2,
        )

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

    # Fetch all order books and fill prices in parallel with asyncio.gather
    ob_tasks = [_get_order_book(client, ex, pair, config.ob_depth) for ex in exchanges]
    buy_tasks = [_get_fill_price(client, ex, pair, config.amount, True) for ex in exchanges]
    sell_tasks = [_get_fill_price(client, ex, pair, config.amount, False) for ex in exchanges]

    all_results = await asyncio.gather(*ob_tasks, *buy_tasks, *sell_tasks)

    # Unpack results
    n_ex = len(exchanges)
    ob_results = all_results[:n_ex]
    buy_results = all_results[n_ex:2 * n_ex]
    sell_results = all_results[2 * n_ex:]

    ex_data = {}
    for i, ex in enumerate(exchanges):
        ob = ob_results[i]
        bids, asks = _parse_ob_levels(ob) if ob else ([], [])
        stats = _ob_stats(bids, asks) if ob else {}
        ex_data[ex] = {
            "ob": ob, "bids": bids, "asks": asks, "stats": stats,
            "fill_buy": buy_results[i], "fill_sell": sell_results[i],
        }

    # Filter invalid exchanges and sort by spread
    valid_exchanges, invalid_exchanges = _filter_and_sort_exchanges(exchanges, ex_data)

    if not valid_exchanges:
        msg = f"Could not fetch valid order books from any exchange: {', '.join(exchanges)}"
        if invalid_exchanges:
            msg += "\n\nFiltered out:\n" + "\n".join(f"  {ex.upper()}: {reason}" for ex, reason in invalid_exchanges)
        return msg

    # Build text output (sorted by spread)
    lines = [
        f"CEX Order Book Comparison: {pair}",
        f"Exchanges: {', '.join(e.upper() for e in valid_exchanges)} | Quote: {config.amount} {pair.split('-')[0]}",
        "",
    ]

    for ex in valid_exchanges:
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
        lines.append("")

    if invalid_exchanges:
        lines.append("--- Filtered Out ---")
        for ex, reason in invalid_exchanges:
            lines.append(f"  {ex.upper()}: {reason}")
        lines.append("")

    # Arb analysis across valid pairs
    all_arb_routes = _compute_arb_routes(valid_exchanges, ex_data, config.amount)

    lines.append("--- Arbitrage Matrix ---")
    opportunities = []
    for r in all_arb_routes:
        label = f"{r['route']}: {r['spread']:+.4f}% (${r['profit']:.4f})"
        (opportunities if r["spread"] > 0 else lines).append(label)

    mids = {ex: d["stats"]["mid"] for ex, d in ex_data.items()
            if ex in valid_exchanges and d["stats"].get("mid")}
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

        ex_label = " vs ".join(e.upper() for e in valid_exchanges)
        builder = ReportBuilder(f"CEX Arb: {pair} ({ex_label})")
        builder.source("routine", "arb_check").tags(["arbitrage", "cex-cex", pair] + valid_exchanges)
        builder.manual_order()

        # KPIs - only essentials
        builder.kpi("Exchanges", str(len(valid_exchanges)))

        if len(mids) >= 2:
            mid_vals = list(mids.values())
            max_diff = max(mid_vals) - min(mid_vals)
            max_diff_pct = (max_diff / min(mid_vals)) * 100
            builder.kpi("Max Mid Diff", f"{max_diff_pct:+.4f}%",
                        trend="up" if max_diff_pct > 0.05 else "neutral")

        # Find best arb opportunity for KPI (all_arb_routes computed above)
        best_arb = max(all_arb_routes, key=lambda r: r["spread"]) if all_arb_routes else None

        if best_arb:
            builder.kpi("Best Route", f"{best_arb['route']}  {best_arb['spread']:+.4f}%",
                        trend="up" if best_arb["spread"] > 0 else "down")

        # Order book depth chart (valid exchanges, sorted by spread)
        chart_data = [
            (ex, ex_data[ex]["bids"], ex_data[ex]["asks"])
            for ex in valid_exchanges if ex_data[ex]["ob"]
        ]
        if len(chart_data) >= 2:
            fig = _build_ob_chart(chart_data, pair)
            builder.plotly(fig)

        # Comparison table (sorted by spread)
        builder.markdown(f"## Order Book Comparison ({config.ob_depth} levels)")
        table_rows = []
        for ex in valid_exchanges:
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
            row[f"Fill BUY {config.amount}"] = f"{d['fill_buy']:.6f}" if d["fill_buy"] else "N/A"
            row[f"Fill SELL {config.amount}"] = f"{d['fill_sell']:.6f}" if d["fill_sell"] else "N/A"
            table_rows.append(row)
        builder.table(table_rows)

        # Arb routes table — sorted by spread (best first)
        if all_arb_routes:
            all_arb_routes.sort(key=lambda r: r["spread"], reverse=True)
            arb_rows = []
            for r in all_arb_routes:
                arb_rows.append({
                    "Route": r["route"],
                    "Spread": f"{r['spread']:+.4f}%",
                    f"Profit ({config.amount} {pair.split('-')[0]})": f"${r['profit']:.4f}",
                    "Signal": "✅" if r["spread"] > 0 else "❌",
                })
            builder.markdown("## Arbitrage Routes (sorted by spread)")
            builder.table(arb_rows)

        # Mid-price differences
        if len(mids) >= 2:
            mid_rows = []
            for ex_a, ex_b in combinations(mids.keys(), 2):
                diff = _spread_pct(mids[ex_a], mids[ex_b])
                mid_rows.append({
                    "Pair": f"{ex_a.upper()} vs {ex_b.upper()}",
                    "Mid Diff": f"{diff:+.4f}%",
                    f"{ex_a.upper()} Mid": f"{mids[ex_a]:.6f}",
                    f"{ex_b.upper()} Mid": f"{mids[ex_b]:.6f}",
                    "_diff_abs": abs(diff),
                })
            mid_rows.sort(key=lambda r: r["_diff_abs"], reverse=True)
            for r in mid_rows:
                del r["_diff_abs"]
            builder.markdown("## Mid-Price Differences")
            builder.table(mid_rows)

        # Filtered exchanges note
        if invalid_exchanges:
            builder.markdown("## Filtered Exchanges")
            filtered_rows = [{"Exchange": ex.upper(), "Reason": reason} for ex, reason in invalid_exchanges]
            builder.table(filtered_rows)

        # Top levels detail per exchange
        for ex in valid_exchanges:
            d = ex_data[ex]
            bids, asks = d["bids"], d["asks"]
            nlvl = min(10, max(len(bids), len(asks)))
            if nlvl == 0:
                continue
            builder.markdown(f"### {ex.upper()} - Top {nlvl} Levels")
            levels = []
            for i in range(nlvl):
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

        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return "\n".join(lines)
