"""Arbitrage Controller chart - shows both exchange prices, spread % and Z-score with profit simulation."""

import io
from typing import Any, Dict, List, Optional
import logging
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
logger = logging.getLogger(__name__)

def _normalize_candles(candles):
    """Convert candles to standard list of dicts."""
    if not candles:
        return []
    if isinstance(candles[0], dict) and 'timestamp' in candles[0]:
        return candles
    normalized = []
    for item in candles:
        if len(item) >= 6:
            ts, o, h, l, c, v = item[:6]
            normalized.append({
                'timestamp': ts,
                'open': float(o),
                'high': float(h),
                'low': float(l),
                'close': float(c),
                'volume': float(v) if v else 0
            })
        elif len(item) == 5:
            ts, o, h, l, c = item
            normalized.append({
                'timestamp': ts,
                'open': float(o),
                'high': float(h),
                'low': float(l),
                'close': float(c),
                'volume': 0
            })
    return normalized


def _convert_timestamps_to_datetime(candles):
    """Convert numeric timestamps to datetime objects."""
    if not candles:
        return candles
    for c in candles:
        ts = c['timestamp']
        if isinstance(ts, (int, float)):
            if ts > 1e12:
                ts = ts / 1000.0
            c['timestamp'] = pd.to_datetime(ts, unit='s')
    return candles

def calculate_spread_series(candles1, candles2):
    """
    Align two candlestick series and compute:
    - spread %
    - rolling mean
    - z-score
    """

    # ==========================================
    # NORMALIZE INPUT
    # ==========================================

    df1 = pd.DataFrame(_normalize_candles(candles1))
    df2 = pd.DataFrame(_normalize_candles(candles2))

    if df1.empty or df2.empty:
        return []

    # ==========================================
    # TIMESTAMP HANDLING
    # ==========================================

    df1["timestamp"] = pd.to_datetime(df1["timestamp"])
    df2["timestamp"] = pd.to_datetime(df2["timestamp"])

    df1 = df1.sort_values("timestamp")
    df2 = df2.sort_values("timestamp")

    # ==========================================
    # ALIGN CANDLES
    # ==========================================

    df = pd.merge_asof(
        df1,
        df2,
        on="timestamp",
        suffixes=("_1", "_2"),
        direction="nearest",
        tolerance=pd.Timedelta("2min")
    )

    # Remove rows without valid aligned candles
    df = df.dropna(subset=["close_1", "close_2"])

    if df.empty:
        return []

    # ==========================================
    # NUMERIC CONVERSION
    # ==========================================

    df["close_1"] = pd.to_numeric(df["close_1"], errors="coerce")
    df["close_2"] = pd.to_numeric(df["close_2"], errors="coerce")

    df = df.dropna(subset=["close_1", "close_2"])

    if df.empty:
        return []

    # ==========================================
    # SPREAD CALCULATION
    # ==========================================

    # Log spread = more statistically stable
    spread_pct = np.log(df["close_2"] / df["close_1"]) * 100

    # ==========================================
    # ROLLING STATISTICS
    # ==========================================

    window = 20

    rolling_mean = spread_pct.rolling(window=window).mean()
    rolling_std = spread_pct.rolling(window=window).std()

    # Avoid division by zero
    zscores = (spread_pct - rolling_mean) / (rolling_std + 1e-9)

    # ==========================================
    # BUILD OUTPUT
    # ==========================================

    spreads = []

    for i in range(len(df)):

        spreads.append({
            "time": df["timestamp"].iloc[i],

            "spread": (
                float(spread_pct.iloc[i])
                if not np.isnan(spread_pct.iloc[i])
                else 0.0
            ),

            "mean": (
                float(rolling_mean.iloc[i])
                if not np.isnan(rolling_mean.iloc[i])
                else 0.0
            ),

            "zscore": (
                float(zscores.iloc[i])
                if not np.isnan(zscores.iloc[i])
                else 0.0
            ),
        })

    return spreads

def generate_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
    grid_analysis: Optional[Dict[str, Any]] = None,
) -> io.BytesIO:
    """Generate chart with 4 panels: Prices, Spread, Z-Score, Cumulative Profit."""

    # Normalizza e converti timestamp
    candles1 = _convert_timestamps_to_datetime(_normalize_candles(candles_data))
    candles2 = _convert_timestamps_to_datetime(_normalize_candles(config.get("candles_exchange_2", [])))

    # Limita a 80 candele
    MAX_CANDLES = 240
    if len(candles1) > MAX_CANDLES:
        candles1 = candles1[-MAX_CANDLES:]
    if len(candles2) > MAX_CANDLES:
        candles2 = candles2[-MAX_CANDLES:]

    if not candles1:
        buf = io.BytesIO()
        fig = go.Figure()
        fig.add_annotation(text="No candle data available", x=0.5, y=0.5, showarrow=False)
        fig.write_image(buf, format="png")
        buf.seek(0)
        return buf

    # Extract configuration
    ep1 = config.get("exchange_pair_1", {})
    ep2 = config.get("exchange_pair_2", {})
    connector1 = ep1.get("connector_name", "Exchange 1")
    connector2 = ep2.get("connector_name", "Exchange 2")
    pair1 = ep1.get("trading_pair", "Unknown")
    pair2 = ep2.get("trading_pair", "Unknown")
    min_profit = float(config.get("min_profitability", 0.005))

    # Parametri per il calcolo del profitto
    capital = float(config.get("total_amount_quote", 1000))
    fee_rate_1 = float(config.get("fee_rate_exchange_1", 0.0005))
    fee_rate_2 = float(config.get("fee_rate_exchange_2", 0.0005))
    slippage = float(config.get("slippage", 0.0005))

    title = (f"🔬 BACKTEST: {connector1} {pair1} ↔ {connector2} {pair2} | "
             f"min profit: {min_profit*100:.2f}% | capital: ${capital:,.0f} | "
             f"fees: {fee_rate_1*100:.2f}%/{fee_rate_2*100:.2f}%")

    # Calcola lo spread
    spread_data = calculate_spread_series(candles1, candles2)
    if spread_data:
        spreads = [s["spread"] for s in spread_data]
        logger.info(f"Spread min: {min(spreads):.4f}%, max: {max(spreads):.4f}%")
        logger.info(f"Z-Score min: {min([s['zscore'] for s in spread_data]):.2f}, max: {max([s['zscore'] for s in spread_data]):.2f}")



    if not spread_data:
        buf = io.BytesIO()
        fig = go.Figure()
        fig.add_annotation(text="No spread data available", x=0.5, y=0.5, showarrow=False)
        fig.write_image(buf, format="png")
        buf.seek(0)
        return buf

    # TROVA IL PRIMO INDICE DOVE ZSCORE È VALIDO
    start_idx = 0
    for i, s in enumerate(spread_data):
        if not np.isnan(s["zscore"]):
            start_idx = i
            break

    # Se non trovato o troppo vicino all'inizio, usa un offset minimo di 10
    if start_idx < 10:
        start_idx = 10

    # TAGLIA TUTTI I DATI DALLO STESSO PUNTO DI PARTENZA
    spread_data = spread_data[start_idx:]

    # Allinea anche le candele dei prezzi
    if len(candles1) > start_idx:
        candles1 = candles1[start_idx:]
    if len(candles2) > start_idx:
        candles2 = candles2[start_idx:]

    # Create 3 subplots
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.45, 0.30, 0.25],
        subplot_titles=(
            f"<b>Prices</b>: {connector1} (cyan) vs {connector2} (orange)",

            f"<b>Spread %</b> | Entry Thresholds ±{min_profit*100:.2f}%",

            f"<b>Cumulative Profit</b> "
            f"(capital: ${capital:,.0f} | fees: {fee_rate_1*100:.2f}% + {fee_rate_2*100:.2f}%)"
        )
    )

    # ==========================================
    # ROW 1: Price chart
    # ==========================================
    if candles1:
        df1 = pd.DataFrame(candles1).sort_values("timestamp")
        fig.add_trace(
            go.Scatter(
                x=df1["timestamp"],
                y=df1["close"],
                mode="lines",
                name=f"{connector1} {pair1}",
                line=dict(color="#00d4ff", width=2.5),
            ),
            row=1, col=1
        )

    if candles2:
        df2 = pd.DataFrame(candles2).sort_values("timestamp")
        fig.add_trace(
            go.Scatter(
                x=df2["timestamp"],
                y=df2["close"],
                mode="lines",
                name=f"{connector2} {pair2}",
                line=dict(color="orange", width=2.5),
            ),
            row=1, col=1
        )

        if candles1 and len(candles1) == len(candles2):
            df1_aligned = pd.DataFrame(candles1).sort_values("timestamp")
            fig.add_trace(
                go.Scatter(
                    x=df1_aligned["timestamp"],
                    y=df2["close"],
                    mode='lines',
                    fill='tonexty',
                    name="Differenza",
                    line=dict(width=0),
                    fillcolor='rgba(255,165,0,0.15)',
                    showlegend=False
                ),
                row=1, col=1
            )

    fig.add_annotation(
        xref="x domain", yref="y domain", x=0.02, y=0.98,
        xanchor="left", yanchor="top",
        text=f"<b>{connector1}</b> <span style='color:#00d4ff'>●</span> | <b>{connector2}</b> <span style='color:orange'>●</span>",
        showarrow=False, font=dict(size=11, color="white"),
        bgcolor='rgba(0,0,0,0.5)', borderpad=6, borderwidth=1, bordercolor='#444',
        row=1, col=1
    )

    # ==========================================
    # ROW 2: Spread %
    # ==========================================
    spread_vals = [s["spread"] for s in spread_data if s["spread"] != 0]
    if spread_vals:
        min_spread, max_spread = min(spread_vals), max(spread_vals)
        margin = (max_spread - min_spread) * 0.15 if max_spread != min_spread else 0.2

        fig.add_trace(
            go.Scatter(x=[s["time"] for s in spread_data], y=[s["spread"] for s in spread_data],
                       mode="lines", name="Spread %",
                       line=dict(color="cyan", width=2)),
            row=2, col=1
        )
        fig.add_trace(
            go.Scatter(x=[s["time"] for s in spread_data], y=[s["mean"] for s in spread_data],
                       mode="lines", name="Mean (20)",
                       line=dict(color="orange", width=1.5, dash="dash")),
            row=2, col=1
        )

        fig.update_yaxes(range=[min_spread - margin, max_spread + margin], row=2, col=1)

        fig.add_hline(y=min_profit * 100, line_dash="dot", line_color="green",
                      row=2, col=1, annotation_text=f"+{min_profit*100:.2f}%")
        fig.add_hline(y=-min_profit * 100, line_dash="dot", line_color="red",
                      row=2, col=1, annotation_text=f"-{min_profit*100:.2f}%")

        fig.add_annotation(
            xref="x domain", yref="y domain", x=0.02, y=0.98,
            xanchor="left", yanchor="top",
            text="<b>Spread %</b> <span style='color:cyan'>●</span> | <b>Mean (20)</b> <span style='color:orange'>---</span> | <b>Thresholds</b> <span style='color:green'>+</span> <span style='color:red'>-</span>",
            showarrow=False, font=dict(size=11, color="white"),
            bgcolor='rgba(0,0,0,0.5)', borderpad=6, borderwidth=1, bordercolor='#444',
            row=2, col=1
        )

    # ==========================================
    # ROW 3: REALIZED CUMULATIVE PROFIT
    # ==========================================

    realized_profit = 0.0
    position = None
    entry_spread = None

    total_fee_pct = (
        fee_rate_1 +
        fee_rate_2 +
        (slippage * 2)
    ) * 100

    # Calcola soglia dinamica basata sullo spread MASSIMO (non sulla media)
    if spread_vals:
        max_spread = max(spread_vals)
        # Entra quando lo spread supera l'80% del massimo storico
        ENTRY_SPREAD = max_spread * 0.8
        # Soglia minima per evitare rumore (0.01%)
        MIN_THRESHOLD = 0.01
        ENTRY_SPREAD = max(ENTRY_SPREAD, MIN_THRESHOLD)
        logger.info(f"MAX_SPREAD: {max_spread:.4f}%, ENTRY_SPREAD: {ENTRY_SPREAD:.4f}%")
    else:
        ENTRY_SPREAD = min_profit * 100

    EXIT_SPREAD = ENTRY_SPREAD * 0.25

    equity = []
    profit_times = []

    for s in spread_data:
        spread = s["spread"]

        # ======================================
        # ENTRY LOGIC
        # ======================================
        if position is None:
            if spread < -ENTRY_SPREAD:
                position = "long"
                entry_spread = spread
                logger.info(f"LONG ENTRY at spread: {spread:.4f}%")
            elif spread > ENTRY_SPREAD:
                position = "short"
                entry_spread = spread
                logger.info(f"SHORT ENTRY at spread: {spread:.4f}%")

        # ======================================
        # EXIT LONG
        # ======================================
        elif position == "long":
            if spread >= -EXIT_SPREAD:
                spread_move = spread - entry_spread
                trade_profit_pct = spread_move - total_fee_pct
                #trade_profit_pct = max(trade_profit_pct, 0)
                realized_profit += (trade_profit_pct / 100) * capital
                logger.info(f"LONG EXIT at spread: {spread:.4f}%, profit: ${realized_profit:.2f}")
                position = None
                entry_spread = None

        # ======================================
        # EXIT SHORT
        # ======================================
        elif position == "short":
            if spread <= EXIT_SPREAD:
                spread_move = entry_spread - spread
                trade_profit_pct = spread_move - total_fee_pct
                #trade_profit_pct = max(trade_profit_pct, 0)
                realized_profit += (trade_profit_pct / 100) * capital
                logger.info(f"SHORT EXIT at spread: {spread:.4f}%, profit: ${realized_profit:.2f}")
                position = None
                entry_spread = None

        equity.append(realized_profit)
        profit_times.append(s["time"])
    # ==========================================
    # SAFETY CHECK
    # ==========================================
    if len(equity) < 2:
        equity = [0, 0]
        profit_times = [spread_data[0]["time"], spread_data[-1]["time"]]

    final_profit_usd = equity[-1]

    line_color = "#00ff88" if final_profit_usd >= 0 else "#ff4444"
    area_color = "rgba(0,255,136,0.15)" if final_profit_usd >= 0 else "rgba(255,68,68,0.15)"

    # ==========================================
    # PLOT
    # ==========================================
    fig.add_trace(
        go.Scatter(
            x=profit_times,
            y=equity,
            mode="lines",
            name="Equity Curve",
            line=dict(color=line_color, width=2),
            fill="tozeroy",
            fillcolor=area_color
        ),
        row=3,
        col=1
    )

    fig.add_hline(
        y=0,
        line_dash="solid",
        line_color="gray",
        row=3,
        col=1,
        opacity=0.5
    )

    fig.update_yaxes(
        title_text="<b>Profit (USD)</b>",
        row=3,
        col=1,
        autorange=True
    )

    profit_color = "#00ff88" if final_profit_usd >= 0 else "#ff4444"

    # In ROW 3, modifica l'annotazione del profit
    fig.add_annotation(
        xref="x domain", yref="y domain", x=0.02, y=0.98,  # ← x=0.02 (sinistra)
        xanchor="left", yanchor="top",
        text=f"<b>Backtest Final Profit:</b> <span style='color:{profit_color}'>${final_profit_usd:.2f}</span> | "
             f"<b>ROI:</b> <span style='color:{profit_color}'>{final_profit_usd/capital*100:.2f}%</span>",
        showarrow=False, font=dict(size = 11, color="white"),
        bgcolor='rgba(15,18,25,0.82)', borderpad=6, borderwidth=1, bordercolor='#444',
        row=3, col=1
    )

    # ==========================================
    # LAYOUT
    # ==========================================
    fig.update_layout(
        title=dict(text=title, font=dict(size=13, color="white"), x=0.5),
        template="plotly_dark",
        height=1050,
        hovermode="x unified",
        plot_bgcolor='#0e1117',
        paper_bgcolor='#0e1117',
        showlegend=False
    )

    fig.update_xaxes(
        gridcolor='#2a2f3a',
        showgrid=True,
        gridwidth=0.5,
        title_font=dict(color="white", size=10),
        tickfont=dict(color="white", size=9),
        tickformat="%H:%M\n%d/%m"
    )
    fig.update_yaxes(
        gridcolor='#2a2f3a',
        showgrid=True,
        gridwidth=0.5,
        title_font=dict(color="white", size=10),
        tickfont=dict(color="white", size=9)
    )

    fig.update_yaxes(title_text="<b>Price (USD)</b>", row=1, col=1)
    fig.update_yaxes(title_text="<b>Spread (%)</b>", row=2, col=1)
    fig.update_yaxes(title_text="<b>Profit (USD)</b>", row=3, col=1)
    fig.update_xaxes(title_text="<b>Time</b>", row=3, col=1)

    buf = io.BytesIO()
    fig.write_image(buf, format="png", scale=2)
    buf.seek(0)
    return buf


def generate_preview_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None
) -> io.BytesIO:
    """Preview chart without grid analysis overlays."""
    return generate_chart(config, candles_data, current_price, grid_analysis=None)
