"""
Bollinger Grid chart generation.

3 panels:
  1. Price – candlesticks + BB + grid lines (start, end, limit)
  2. Volume – colored bars
  3. BBP – Bollinger Band Percent with long/short thresholds
"""

import io
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import logging
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
from datetime import datetime

def generate_chart(config, candles_data, current_price=None):
    if not candles_data or len(candles_data) < 5:
        return _generate_simple_chart(candles_data, current_price)

    df = _prepare_dataframe(candles_data)
    if df is None or df.empty:
        return _generate_simple_chart(candles_data, current_price)

    # 🔥 SAFETY PATCH (QUI)
    for col in ['open','high','low','close','volume']:
        if col not in df.columns:
            df[col] = 0

    full_df = df.copy()

    MAX_VISIBLE_CANDLES = 96

    for col in ['open', 'high', 'low', 'close', 'volume']:
        full_df[col] = pd.to_numeric(full_df.get(col, 0), errors='coerce').fillna(0)

    logger = logging.getLogger(__name__)

    # ── INDICATORI ──────────────────────────────────────────────────
    bb_length = int(config.get('bb_length', 100))
    bb_std_val = float(config.get('bb_std', 2.0))
    
    # Se non ci sono abbastanza dati, riduci bb_length
    if len(df) < bb_length:
        bb_length = max(20, len(df) // 2)
        logger.info(f"BG Chart: reduced bb_length to {bb_length} due to insufficient data")
    
    # Calcola BB con min_periods=1 per avere valori dall'inizio
    rolling = full_df['close'].rolling(window=bb_length, min_periods=1)
    full_df['bb_mid'] = rolling.mean()
    bb_std_series = rolling.std(ddof=0)
    bb_std_series = bb_std_series.replace(0, 0.0001)  # evita std=0
    
    full_df['bb_upper'] = full_df['bb_mid'] + bb_std_val * bb_std_series
    full_df['bb_lower'] = full_df['bb_mid'] - bb_std_val * bb_std_series

    # ── CALCOLO BBP ────────────────────────────────────────────────
    denom = full_df['bb_upper'] - full_df['bb_lower']
    denom = denom.replace(0, np.nan)
    bbp_values = (full_df['close'] - full_df['bb_lower']) / denom
    bbp_values = bbp_values.fillna(0.5)
    bbp_values = bbp_values.clip(-1, 2)
    full_df['bbp'] = bbp_values
    df = full_df.tail(MAX_VISIBLE_CANDLES).copy()

    # ── PREZZI DELLA GRIGLIA ────────────────────────────────────────
    start_price = config.get('start_price', 0)
    end_price = config.get('end_price', 0)
    limit_price = config.get('limit_price', 0)
    if df.empty:
        current_price = 0
    elif current_price is None:
        current_price = float(df['close'].iloc[-1])

    # ── FIGURA ──────────────────────────────────────────────────────

    fig, (ax1, ax2, ax3) = plt.subplots(
        3,
        1,
        figsize=(22, 12),
        sharex=True,
        gridspec_kw={
            'height_ratios': [4.5, 1.2, 1.5]
        }
    )

    fig.patch.set_facecolor('#111111')

    for ax in [ax1, ax2, ax3]:
        ax.set_facecolor('#111111')
        ax.tick_params(colors='white')
        ax.yaxis.label.set_color('white')
        ax.spines['bottom'].set_color('#444')
        ax.spines['top'].set_color('#444')
        ax.spines['left'].set_color('#444')
        ax.spines['right'].set_color('#444')

    dates = mdates.date2num(df['datetime'])

    if len(dates) > 1:

        candle_width = (dates[1] - dates[0]) * 0.85
        volume_width = (dates[1] - dates[0]) * 0.85

    else:

        candle_width = volume_width = 0.0005

    # ── PANNELLO 1: PREZZO + BOLLINGER BANDS + GRIGLIA ──────────────

    # Candele
    for i in range(len(df)):
        o, h, l, c = df.iloc[i][['open', 'high', 'low', 'close']]
        color = '#2ecc71' if c >= o else '#e74c3c'
        ax1.plot([dates[i], dates[i]], [l, h], color=color, linewidth=1)
        ax1.add_patch(Rectangle(
            (dates[i] - candle_width / 2, min(o, c)),
            candle_width, abs(c - o) or 1e-8,
            color=color
        ))

    # Bollinger Bands (colori diversi)
    ax1.plot(df['datetime'], df['bb_upper'], '--', linewidth=1, color='#1f77b4', alpha=0.8, label='BB Upper')
    ax1.plot(df['datetime'], df['bb_mid'],   ':',  linewidth=1, color='#ff7f0e', alpha=0.8, label='BB Mid')
    ax1.plot(df['datetime'], df['bb_lower'], '--', linewidth=1, color='#2ca02c', alpha=0.8, label='BB Lower')

    # Linee della griglia (sempre mostrate)
    if start_price > 0:
        ax1.axhline(y=start_price, linestyle='-', linewidth=1.5, color='green', alpha=0.9, label=f'Start ({start_price:.4f})')
    if end_price > 0:
        ax1.axhline(y=end_price, linestyle='-', linewidth=1.5, color='red', alpha=0.9, label=f'End ({end_price:.4f})')
    if limit_price > 0:
        ax1.axhline(y=limit_price, linestyle='--', linewidth=1.5, color='orange', alpha=0.9, label=f'Limit ({limit_price:.4f})')

    # Prezzo corrente
    if current_price:
        ax1.axhline(y=current_price, linestyle='--', linewidth=1, color='purple', alpha=0.6, label=f'Current ({current_price:.4f})')

    # ── GRID ZONE DINAMICA ─────────────────────────────────────────────

    visible_candles = len(df)

    # intensità fill adattiva
    if visible_candles <= 32:
        grid_alpha = 0.06
    elif visible_candles <= 64:
        grid_alpha = 0.08
    elif visible_candles <= 96:
        grid_alpha = 0.11
    else:
        grid_alpha = 0.14

    # colore meno "sporco"
    grid_color = '#d4c900'

    if start_price > 0 and end_price > 0 and start_price < end_price:

        ax1.axhspan(start_price, end_price, alpha=grid_alpha, color=grid_color, label='Grid Zone', zorder=0)

        # bordi zona più leggibili
        ax1.axhline(start_price, color='#00ff88', linewidth=1.2, alpha=0.75)

        ax1.axhline(end_price, color='#ff4d6d', linewidth=1.2, alpha=0.75)

    # Calcola i limiti Y includendo le linee della griglia
    price_min = df['low'].min()
    price_max = df['high'].max()
    
    # Includi start_price e limit_price se sono nel range
    if start_price > 0:
        price_min = min(price_min, start_price)
    if limit_price > 0:
        price_min = min(price_min, limit_price)
    if end_price > 0:
        price_max = max(price_max, end_price)
    
    # Aggiungi un margine del 5%
    margin = (price_max - price_min) * 0.05
    y_min = price_min - margin
    y_max = price_max + margin
    
    ax1.set_ylim(y_min, y_max)

    ax1.legend(loc='upper left', fontsize=9, ncol=2, framealpha=0)
    ax1.set_ylabel('Price')
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(df['datetime'].min(), df['datetime'].max())

    # ── PANNELLO 2: VOLUME ──────────────────────────────────────────
    if 'volume' in df.columns and df['volume'].sum() > 0:
        vol_colors = [
            '#2ecc71' if df['close'].iloc[i] >= df['open'].iloc[i] else '#e74c3c'
            for i in range(len(df))
        ]
        ax2.bar(dates, df['volume'], width=volume_width, color=vol_colors, alpha=0.7)
    ax2.set_ylabel('Volume')
    ax2.grid(True, alpha=0.3)

    # ── PANNELLO 3: BBP ─────────────────────────────────────────────


    long_thr = float(config.get('bb_long_threshold', 0.0))
    short_thr = float(config.get('bb_short_threshold', 1.0))
    ax3.plot(df['datetime'], df['bbp'], linewidth=1.5, color='#3da5ff')
    ax3.axhline(long_thr, linestyle='--', color='green', alpha=0.8, label=f'Long ({long_thr})')
    ax3.axhline(short_thr, linestyle='--', color='red', alpha=0.8, label=f'Short ({short_thr})')
    ax3.axhline(0, linestyle=':', color='gray', alpha=0.5)
    ax3.axhline(1, linestyle=':', color='gray', alpha=0.5)

# ── BBP SIGNAL ZONES ──────────────────────────────────────────────

    ax3.fill_between(df['datetime'], -1, long_thr, alpha=0.16, color='#00ff88')
    ax3.fill_between(df['datetime'], short_thr, 2, alpha=0.16, color='#ff4d6d')

    # Marca i punti di segnale
    long_signals = df[df['bbp'] < long_thr]
    short_signals = df[df['bbp'] > short_thr]
    
    if not long_signals.empty:
        ax3.scatter(long_signals['datetime'], long_signals['bbp'], 
                    color='green', marker='^', s=30, alpha=0.8, label='Long signal')
    if not short_signals.empty:
        ax3.scatter(short_signals['datetime'], short_signals['bbp'], 
                    color='red', marker='v', s=30, alpha=0.8, label='Short signal')

    ax3.legend(loc='upper left', fontsize=9, framealpha=0)
    ax3.set_ylabel('BBP')
    ax3.grid(True, alpha=0.3)

    # ── CONFIGURAZIONE ASSE X (come Grid Strike) ────────────────────
    interval = config.get('interval', '5m')
    _setup_x_axis(ax3, df, interval)
    
    # Imposta i limiti X per tutti i pannelli
    x_min = df['datetime'].min()
    x_max = df['datetime'].max()
    
    # Copia locator e formatter agli altri pannelli
    ax1.tick_params(labelbottom=False)
    ax2.tick_params(labelbottom=False)
    for ax in [ax1, ax2]:
        ax.set_xlim(x_min, x_max)
        ax.xaxis.set_minor_locator(ax3.xaxis.get_minor_locator())
        ax.xaxis.set_major_locator(ax3.xaxis.get_major_locator())
        ax.xaxis.set_major_formatter(ax3.xaxis.get_major_formatter())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=0, ha='center')
    

    # ── TITOLO ──────────────────────────────────────────────────────
    fig.suptitle(
        f"{config.get('trading_pair', 'Unknown')} - Bollinger Grid "
        f"(BB{bb_length} | Grid: {start_price:.4f} → {end_price:.4f} | {interval})",
        fontsize=13
    )

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf

def _setup_x_axis(ax, df, interval):
    import matplotlib.dates as mdates

    # ── TIMEFRAME CONFIG ──────────────────────────────────────────

    if interval == '1m':
        locator = mdates.MinuteLocator(byminute=[0, 15, 30, 45])
        formatter = mdates.DateFormatter('%H:%M')
        minor_locator = mdates.MinuteLocator(interval=5)

    elif interval == '5m':
        locator = mdates.HourLocator(interval=1)
        formatter = mdates.DateFormatter('%H:%M')
        minor_locator = mdates.MinuteLocator(byminute=[0, 15, 30, 45])

    elif interval == '15m':
        locator = mdates.HourLocator(interval=2)
        formatter = mdates.DateFormatter('%d %H:%M')
        minor_locator = mdates.HourLocator(interval=1)

    elif interval == '1h':
        locator = mdates.HourLocator(interval=4)
        formatter = mdates.DateFormatter('%d %H:%M')
        minor_locator = mdates.HourLocator(interval=1)

    elif interval == '4h':
        locator = mdates.HourLocator(interval=12)
        formatter = mdates.DateFormatter('%d %H:%M')
        minor_locator = mdates.HourLocator(interval=4)

    elif interval == '8h':
        locator = mdates.DayLocator(interval=2)
        formatter = mdates.DateFormatter('%d %b')
        minor_locator = mdates.HourLocator(interval=8)

    else:
        locator = mdates.AutoDateLocator(minticks=6, maxticks=12)
        formatter = mdates.ConciseDateFormatter(locator)
        minor_locator = None

    # ── APPLY ─────────────────────────────────────────────────────
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(formatter)

    if minor_locator:
        ax.xaxis.set_minor_locator(minor_locator)

    plt.setp(ax.xaxis.get_majorticklabels(), rotation=0, ha='center', fontsize=9)
    # major vertical grid
    ax.grid(True, which='major', axis='x', linestyle='--', alpha=0.15)
    ax.set_xlim(df['datetime'].min(), df['datetime'].max()    )

def _prepare_dataframe(candles, timezone=None):
    if timezone is None:
        timezone = datetime.now().astimezone().tzinfo
    if not candles:
        return pd.DataFrame({
            "datetime": pd.date_range(end=pd.Timestamp.now(), periods=1, freq="5min"),
            "open": [0], "high": [0], "low": [0], "close": [0], "volume": [0]
        })
    df = pd.DataFrame(candles)

    ts_col = next(
        (c for c in ['timestamp', 'time', 'ts', 'datetime'] if c in df.columns),
        None
    )

    if ts_col:
        sample = df[ts_col].iloc[0]

        if isinstance(sample, (int, float)):

            if sample > 10**12:
                # nanoseconds
                df['datetime'] = (
                    pd.to_datetime(df[ts_col], unit='ns', utc=True)
                    .dt.tz_convert(timezone)
                    .dt.tz_localize(None)
                )

            elif sample > 10**10:
                # milliseconds
                df['datetime'] = (
                    pd.to_datetime(df[ts_col], unit='ms', utc=True)
                    .dt.tz_convert(timezone)
                    .dt.tz_localize(None)
                )

            else:
                # seconds
                df['datetime'] = (
                    pd.to_datetime(df[ts_col], unit='s', utc=True)
                    .dt.tz_convert(timezone)
                    .dt.tz_localize(None)
                )

        else:

            df['datetime'] = (
                pd.to_datetime(df[ts_col], utc=True)
                .dt.tz_convert(timezone)
                .dt.tz_localize(None)
            )

    else:

        df['datetime'] = pd.date_range(
            end=pd.Timestamp.now(),
            periods=len(df),
            freq='5min'
        )

    return df.sort_values('datetime').reset_index(drop=True)

def _generate_simple_chart(candles_data, current_price):
    if not candles_data:
        return io.BytesIO()
    full_df = _prepare_dataframe(candles_data)
    MAX_VISIBLE_CANDLES = 96
    if len(full_df) > MAX_VISIBLE_CANDLES:
        df = full_df.tail(MAX_VISIBLE_CANDLES).reset_index(drop=True)
    else:
        df = full_df.copy()
    fig, ax = plt.subplots(figsize=(12, 6))
    if 'close' in df.columns:
        ax.plot(df['datetime'], pd.to_numeric(df['close'], errors='coerce'), linewidth=1.5, color='steelblue')
    if current_price:
        ax.axhline(y=current_price, linestyle='--', color='purple', alpha=0.7)

    ax.set_title('Bollinger Grid - Price Chart')
    ax.set_ylabel('Price')
    ax.grid(True, alpha=0.3)
    _setup_x_axis(ax, df, '5m')
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_preview_chart(config, candles_data, current_price=None):
    return generate_chart(config, candles_data, current_price)
