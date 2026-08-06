"""
Toxic Flow Detector — Market microstructure analysis.
Polls the order book to detect informed trading (toxic flow) vs random noise.
"""

import asyncio
import logging
import time
from collections import deque
from datetime import datetime

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.reports import LiveReport
from config_manager import get_client

logger = logging.getLogger(__name__)

CONTINUOUS = True
CATEGORY = "Analysis"


class Config(BaseModel):
    """Toxic Flow Detector -- Detects informed vs noise trading via order book microstructure."""

    connector_name: str = Field(default="binance", description="Exchange connector")
    trading_pair: str = Field(default="BTC-BRL", description="Trading pair")
    poll_interval_sec: float = Field(
        default=2.0, description="Order book poll interval (seconds)"
    )
    candle_refresh_sec: int = Field(
        default=30, description="Candle data refresh interval (seconds)"
    )
    history_window: int = Field(
        default=300, description="Rolling history size (data points)"
    )
    book_depth: int = Field(default=20, description="Order book depth levels")


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    chat_id = context._chat_id
    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available"

    report = LiveReport(
        "Toxic Flow Detector",
        source_name="toxic_flow_detector",
        tags=["microstructure", "btc-brl", "live"],
    )

    # --- Rolling data stores ---
    W = config.history_window
    timestamps: deque[datetime] = deque(maxlen=W)
    mid_prices: deque[float] = deque(maxlen=W)
    micro_prices: deque[float] = deque(maxlen=W)
    spreads_bps: deque[float] = deque(maxlen=W)
    book_imbalances: deque[float] = deque(maxlen=W)
    bid_depths: deque[float] = deque(maxlen=W)
    ask_depths: deque[float] = deque(maxlen=W)
    micro_deltas_bps: deque[float] = deque(maxlen=W)
    book_change_rates: deque[float] = deque(maxlen=W)

    # Adverse selection: (epoch_sec, mid_price) for future-price lookups
    price_snapshots: deque[tuple[float, float]] = deque(maxlen=W * 2)

    # Candle-derived buy/sell volume approximation
    candle_buy_vol: deque[float] = deque(maxlen=60)
    candle_sell_vol: deque[float] = deque(maxlen=60)

    last_candle_fetch = 0.0
    last_book_depth_state: tuple[float, float] | None = None
    tick = 0

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"Starting Toxic Flow Detector\n"
            f"Pair: {config.trading_pair} | Connector: {config.connector_name}\n"
            f"Poll: every {config.poll_interval_sec}s"
        ),
    )

    try:
        while True:
            now = time.time()
            now_dt = datetime.now()
            tick += 1

            try:
                # ── Fetch order book ──────────────────────────────────
                ob = await client.market_data.get_order_book(
                    config.connector_name,
                    config.trading_pair,
                    depth=config.book_depth,
                )
                bids = ob.get("bids", [])
                asks = ob.get("asks", [])
                if not bids or not asks:
                    await asyncio.sleep(config.poll_interval_sec)
                    continue

                best_bid_px, best_bid_sz = float(bids[0][0]), float(bids[0][1])
                best_ask_px, best_ask_sz = float(asks[0][0]), float(asks[0][1])
                mid = (best_bid_px + best_ask_px) / 2
                spread = best_ask_px - best_bid_px
                spread_bp = (spread / mid) * 10_000

                # Microprice: volume-weighted mid
                microprice = (best_bid_px * best_ask_sz + best_ask_px * best_bid_sz) / (
                    best_bid_sz + best_ask_sz
                )
                micro_delta = ((microprice - mid) / mid) * 10_000

                # Depth & imbalance across all levels
                tot_bid = sum(float(b[1]) for b in bids)
                tot_ask = sum(float(a[1]) for a in asks)
                tot = tot_bid + tot_ask
                imbalance = (tot_bid - tot_ask) / tot if tot > 0 else 0.0

                # Book change rate (proxy for order arrival / cancellation)
                book_chg = 0.0
                if last_book_depth_state is not None:
                    prev_b, prev_a = last_book_depth_state
                    book_chg = abs(tot_bid - prev_b) + abs(tot_ask - prev_a)
                last_book_depth_state = (tot_bid, tot_ask)

                # Store
                timestamps.append(now_dt)
                mid_prices.append(mid)
                micro_prices.append(microprice)
                spreads_bps.append(spread_bp)
                book_imbalances.append(imbalance)
                bid_depths.append(tot_bid)
                ask_depths.append(tot_ask)
                micro_deltas_bps.append(micro_delta)
                book_change_rates.append(book_chg)
                price_snapshots.append((now, mid))

                # ── Refresh candle data periodically ──────────────────
                if now - last_candle_fetch > config.candle_refresh_sec:
                    try:
                        raw = await client.market_data.get_candles(
                            config.connector_name,
                            config.trading_pair,
                            interval="1m",
                            max_records=60,
                        )
                        records = (
                            raw
                            if isinstance(raw, list)
                            else raw.get("data", raw.get("candles", []))
                        )
                        candle_buy_vol.clear()
                        candle_sell_vol.clear()
                        for c in records[-60:]:
                            o = float(c.get("open", c.get("o", 0)))
                            cl = float(c.get("close", c.get("c", 0)))
                            v = float(c.get("volume", c.get("v", 0)))
                            # Approximate buy/sell split from candle direction
                            if cl >= o:
                                candle_buy_vol.append(v * 0.6)
                                candle_sell_vol.append(v * 0.4)
                            else:
                                candle_buy_vol.append(v * 0.4)
                                candle_sell_vol.append(v * 0.6)
                        last_candle_fetch = now
                    except Exception as e:
                        logger.warning(f"Candle fetch error: {e}")

                # ── Adverse Selection ─────────────────────────────────
                horizons = [5, 10, 30, 60]  # seconds
                adverse_sel: dict[int, list[float]] = {h: [] for h in horizons}
                snaps = list(price_snapshots)
                for i, (ts_i, px_i) in enumerate(snaps):
                    for h in horizons:
                        target = ts_i + h
                        for j in range(i + 1, len(snaps)):
                            if snaps[j][0] >= target:
                                change_bps = ((snaps[j][1] - px_i) / px_i) * 10_000
                                adverse_sel[h].append(abs(change_bps))
                                break

                avg_as = {
                    h: float(np.mean(adverse_sel[h])) if adverse_sel[h] else 0.0
                    for h in horizons
                }

                # ── Summary statistics ────────────────────────────────
                r_imb = list(book_imbalances)[-30:]
                avg_imb = float(np.mean(r_imb)) if r_imb else 0.0
                imb_vol = float(np.std(r_imb)) if len(r_imb) > 1 else 0.0

                r_md = list(micro_deltas_bps)[-30:]
                avg_md = float(np.mean(r_md)) if r_md else 0.0

                r_sp = list(spreads_bps)[-30:]
                avg_spread = float(np.mean(r_sp)) if r_sp else 0.0

                tot_buy = sum(candle_buy_vol) if candle_buy_vol else 0.0
                tot_sell = sum(candle_sell_vol) if candle_sell_vol else 0.0
                trade_imb = (
                    (tot_buy - tot_sell) / (tot_buy + tot_sell)
                    if (tot_buy + tot_sell) > 0
                    else 0.0
                )

                r_cr = list(book_change_rates)[-30:]
                avg_cr = float(np.mean(r_cr)) if r_cr else 0.0
                std_cr = float(np.std(r_cr)) if len(r_cr) > 1 else 0.0
                poisson_cv = std_cr / avg_cr if avg_cr > 0 else 0.0

                px_arr = list(mid_prices)
                if len(px_arr) > 2:
                    rets = np.diff(px_arr) / np.array(px_arr[:-1])
                    rvol_bps = float(np.std(rets) * 10_000)
                else:
                    rvol_bps = 0.0

                # ── Toxicity composite score (0-100) ──────────────────
                tox = 0.0
                if avg_spread > 0:
                    tox += min((avg_as.get(10, 0) / avg_spread) * 30, 40)
                tox += min(abs(avg_imb) * 100, 30)
                tox += min(abs(avg_md) * 5, 20)
                tox += min(abs(trade_imb) * 20, 10)
                tox = min(tox, 100)

                if tox < 25:
                    tox_lbl, tox_trend = "Low (Noise)", "neutral"
                elif tox < 50:
                    tox_lbl, tox_trend = "Moderate", "neutral"
                elif tox < 75:
                    tox_lbl, tox_trend = "High", "down"
                else:
                    tox_lbl, tox_trend = "Extreme", "down"

                if avg_imb > 0.05 and avg_md > 0.5:
                    flow_dir, flow_trend = "BUY pressure", "up"
                elif avg_imb < -0.05 and avg_md < -0.5:
                    flow_dir, flow_trend = "SELL pressure", "down"
                else:
                    flow_dir, flow_trend = "Neutral", "neutral"

                # ── Build live report (every 3 ticks) ─────────────────
                if tick % 3 == 0 or tick <= 3:
                    report.clear()
                    report.builder.manual_order()

                    # KPIs — row 1: price & spread
                    report.builder.kpi("Mid Price", f"R$ {mid:,.2f}")
                    report.builder.kpi(
                        "Spread", f"{spread_bp:.1f} bps", trend="neutral"
                    )
                    report.builder.kpi(
                        "Microprice Delta",
                        f"{micro_delta:+.2f} bps",
                        delta=f"avg {avg_md:+.2f}",
                        trend=(
                            "up"
                            if micro_delta > 0
                            else "down" if micro_delta < 0 else "neutral"
                        ),
                    )
                    report.builder.kpi(
                        "Toxicity Score", f"{tox:.0f}/100 {tox_lbl}", trend=tox_trend
                    )

                    # KPIs — row 2: flow
                    report.builder.kpi(
                        "Book Imbalance",
                        f"{imbalance:+.1%}",
                        delta=f"avg {avg_imb:+.1%}",
                        trend="up" if imbalance > 0 else "down",
                    )
                    report.builder.kpi("Flow Direction", flow_dir, trend=flow_trend)
                    report.builder.kpi(
                        "Trade Imbalance",
                        f"{trade_imb:+.1%}",
                        trend="up" if trade_imb > 0 else "down",
                    )
                    report.builder.kpi(
                        "Realized Vol", f"{rvol_bps:.2f} bps/tick", trend="neutral"
                    )

                    # KPIs — row 3: adverse selection horizons
                    for h in horizons:
                        report.builder.kpi(
                            f"Adv.Sel. t+{h}s",
                            f"{avg_as[h]:.2f} bps",
                            delta=f"n={len(adverse_sel[h])}",
                            trend="down" if avg_as[h] > avg_spread else "neutral",
                        )

                    ts_list = list(timestamps)

                    # ── Chart 1: Mid Price vs Microprice + Spread ─────
                    if len(ts_list) > 5:
                        fig1 = make_subplots(
                            rows=2,
                            cols=1,
                            shared_xaxes=True,
                            row_heights=[0.7, 0.3],
                            vertical_spacing=0.08,
                            subplot_titles=("Mid Price vs Microprice", "Spread (bps)"),
                        )
                        fig1.add_trace(
                            go.Scatter(
                                x=ts_list,
                                y=list(mid_prices),
                                name="Mid Price",
                                line=dict(color="#2196F3", width=1.5),
                            ),
                            row=1,
                            col=1,
                        )
                        fig1.add_trace(
                            go.Scatter(
                                x=ts_list,
                                y=list(micro_prices),
                                name="Microprice",
                                line=dict(color="#FF9800", width=1.5, dash="dot"),
                            ),
                            row=1,
                            col=1,
                        )
                        fig1.add_trace(
                            go.Scatter(
                                x=ts_list,
                                y=list(spreads_bps),
                                name="Spread",
                                line=dict(color="#9C27B0", width=1),
                                fill="tozeroy",
                                fillcolor="rgba(156,39,176,0.1)",
                            ),
                            row=2,
                            col=1,
                        )
                        fig1.update_layout(
                            height=450,
                            template="plotly_dark",
                            margin=dict(l=60, r=20, t=40, b=40),
                            legend=dict(
                                orientation="h",
                                yanchor="top",
                                y=-0.15,
                                xanchor="center",
                                x=0.5,
                            ),
                        )
                        fig1.update_yaxes(title_text="Price (BRL)", row=1, col=1)
                        fig1.update_yaxes(title_text="bps", row=2, col=1)
                        report.builder.plotly(fig1)

                    # ── Chart 2: Book Imbalance + Microprice Delta ────
                    if len(ts_list) > 5:
                        fig2 = make_subplots(
                            rows=2,
                            cols=1,
                            shared_xaxes=True,
                            row_heights=[0.5, 0.5],
                            vertical_spacing=0.08,
                            subplot_titles=(
                                "Order Book Imbalance",
                                "Microprice Delta (bps)",
                            ),
                        )
                        imb_arr = list(book_imbalances)
                        fig2.add_trace(
                            go.Bar(
                                x=ts_list,
                                y=imb_arr,
                                name="Imbalance",
                                marker_color=[
                                    "#4CAF50" if v >= 0 else "#F44336" for v in imb_arr
                                ],
                            ),
                            row=1,
                            col=1,
                        )
                        fig2.add_hline(
                            y=0, line_dash="dash", line_color="gray", row=1, col=1
                        )

                        md_arr = list(micro_deltas_bps)
                        fig2.add_trace(
                            go.Bar(
                                x=ts_list,
                                y=md_arr,
                                name="Microprice Delta",
                                marker_color=[
                                    "#4CAF50" if v >= 0 else "#F44336" for v in md_arr
                                ],
                            ),
                            row=2,
                            col=1,
                        )
                        fig2.add_hline(
                            y=0, line_dash="dash", line_color="gray", row=2, col=1
                        )
                        fig2.update_layout(
                            height=400,
                            template="plotly_dark",
                            margin=dict(l=60, r=20, t=40, b=40),
                            showlegend=False,
                        )
                        report.builder.plotly(fig2)

                    # ── Chart 3: Adverse Selection vs Spread ──────────
                    if any(adverse_sel[h] for h in horizons):
                        fig3 = go.Figure()
                        h_labels = [f"t+{h}s" for h in horizons]
                        h_vals = [avg_as[h] for h in horizons]
                        fig3.add_trace(
                            go.Bar(
                                x=h_labels,
                                y=h_vals,
                                name="Avg Adverse Selection",
                                marker_color=[
                                    (
                                        "#4CAF50"
                                        if v <= avg_spread
                                        else (
                                            "#FF9800"
                                            if v <= avg_spread * 2
                                            else "#F44336"
                                        )
                                    )
                                    for v in h_vals
                                ],
                                text=[f"{v:.2f}" for v in h_vals],
                                textposition="outside",
                            )
                        )
                        fig3.add_hline(
                            y=avg_spread,
                            line_dash="dash",
                            line_color="#2196F3",
                            annotation_text=f"Avg Spread: {avg_spread:.1f} bps",
                        )
                        fig3.update_layout(
                            title="Adverse Selection by Horizon vs Spread",
                            yaxis_title="bps",
                            height=300,
                            template="plotly_dark",
                            margin=dict(l=60, r=20, t=50, b=40),
                            legend=dict(
                                orientation="h",
                                yanchor="top",
                                y=-0.15,
                                xanchor="center",
                                x=0.5,
                            ),
                        )
                        report.builder.plotly(fig3)

                    # ── Chart 4: Book Depth (bid vs ask) ──────────────
                    if len(ts_list) > 5:
                        fig4 = go.Figure()
                        fig4.add_trace(
                            go.Scatter(
                                x=ts_list,
                                y=list(bid_depths),
                                name="Bid Depth",
                                fill="tozeroy",
                                line=dict(color="#4CAF50", width=1),
                                fillcolor="rgba(76,175,80,0.2)",
                            )
                        )
                        fig4.add_trace(
                            go.Scatter(
                                x=ts_list,
                                y=[-d for d in ask_depths],
                                name="Ask Depth",
                                fill="tozeroy",
                                line=dict(color="#F44336", width=1),
                                fillcolor="rgba(244,67,54,0.2)",
                            )
                        )
                        fig4.update_layout(
                            title="Order Book Depth (Bid vs Ask)",
                            yaxis_title="BTC",
                            height=300,
                            template="plotly_dark",
                            margin=dict(l=60, r=20, t=50, b=40),
                            legend=dict(
                                orientation="h",
                                yanchor="top",
                                y=-0.15,
                                xanchor="center",
                                x=0.5,
                            ),
                        )
                        report.builder.plotly(fig4)

                    # ── Chart 5: Order Arrival Rate ───────────────────
                    cr_list = list(book_change_rates)
                    if len(cr_list) > 5:
                        cr_ts = ts_list[-len(cr_list) :]
                        fig5 = go.Figure()
                        fig5.add_trace(
                            go.Scatter(
                                x=cr_ts,
                                y=cr_list,
                                name="Book Change Rate",
                                line=dict(color="#FF9800", width=1),
                                fill="tozeroy",
                                fillcolor="rgba(255,152,0,0.1)",
                            )
                        )
                        if len(cr_list) > 10:
                            win = 10
                            rmean = [
                                float(np.mean(cr_list[max(0, i - win) : i + 1]))
                                for i in range(len(cr_list))
                            ]
                            fig5.add_trace(
                                go.Scatter(
                                    x=cr_ts,
                                    y=rmean,
                                    name=f"Rolling Mean ({win})",
                                    line=dict(color="#2196F3", width=2),
                                )
                            )
                        fig5.update_layout(
                            title="Order Arrival Intensity (Book Change Rate)",
                            yaxis_title="BTC depth change",
                            height=300,
                            template="plotly_dark",
                            margin=dict(l=60, r=20, t=50, b=40),
                            legend=dict(
                                orientation="h",
                                yanchor="top",
                                y=-0.15,
                                xanchor="center",
                                x=0.5,
                            ),
                        )
                        report.builder.plotly(fig5)

                    # ── Summary markdown ──────────────────────────────
                    as_vs_spread = (
                        "Toxic" if avg_as.get(10, 0) > avg_spread else "Healthy"
                    )
                    imb_nature = (
                        "Sustained (informed)"
                        if imb_vol < 0.1
                        else "Fluctuating (noise)"
                    )
                    mp_behavior = "Leading mid" if abs(avg_md) > 1 else "Tracking mid"
                    arrival_nature = (
                        "Bursty (non-Poisson)"
                        if poisson_cv > 1.5
                        else "Steady (Poisson-like)"
                    )

                    report.builder.markdown(
                        f"## Toxicity Assessment\n\n"
                        f"**Score: {tox:.0f}/100 -- {tox_lbl}**\n\n"
                        f"- Adverse sel. t+10s ({avg_as.get(10, 0):.2f} bps) vs spread ({avg_spread:.1f} bps) -> {as_vs_spread}\n"
                        f"- Book imbalance vol: {imb_vol:.3f} -> {imb_nature}\n"
                        f"- Microprice delta avg: {avg_md:+.2f} bps -> {mp_behavior}\n"
                        f"- Arrival rate CV: {poisson_cv:.2f} -> {arrival_nature}\n\n"
                        f"*Data points: {len(timestamps)} | Tick: {tick} | Updated: {now_dt.strftime('%H:%M:%S')}*"
                    )

                    report.update()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Tick {tick} error: {e}")

            await asyncio.sleep(config.poll_interval_sec)

    except asyncio.CancelledError:
        return f"Stopped after {tick} ticks, {len(timestamps)} data points collected"
