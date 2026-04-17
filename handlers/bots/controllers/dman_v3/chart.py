"""
DMan V3 chart generation.

4 panels:
  1. Price  – candlesticks + BB + MA20/50/EMA9 + DCA lines
  2. Volume – colored bars
  3. RSI    – RSI indicator with overbought/oversold zones
  4. BBP    – Bollinger Band Percent with long/short thresholds
"""

import io
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle


def generate_chart(config, candles_data, current_price=None):
    if not candles_data or len(candles_data) < 5:
        return _generate_simple_chart(candles_data, current_price)

    df = _prepare_dataframe(candles_data)

    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df.get(col, 0), errors='coerce').fillna(0)

    # ── INDICATORI ──────────────────────────────────────────────────
    df['ma20'] = df['close'].rolling(20).mean()
    df['ma50'] = df['close'].rolling(50).mean()
    df['ema9'] = df['close'].ewm(span=9).mean()

    bb_length = int(config.get('bb_length', 20))
    bb_std_val = float(config.get('bb_std', 2.0))
    rolling = df['close'].rolling(bb_length)
    df['bb_mid'] = rolling.mean()
    bb_std_series = rolling.std()
    df['bb_upper'] = df['bb_mid'] + bb_std_val * bb_std_series
    df['bb_lower'] = df['bb_mid'] - bb_std_val * bb_std_series

    denom = (df['bb_upper'] - df['bb_lower']).replace(0, np.nan)
    df['bbp'] = ((df['close'] - df['bb_lower']) / denom).clip(-1, 2).fillna(0.5)

    # RSI (invece di MACD)
    df['rsi'] = _calc_rsi(df['close'])

    # ── FIGURA ──────────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 14))

    dates = mdates.date2num(df['datetime'])

    if len(dates) > 1:
        candle_width = (dates[1] - dates[0]) * 0.6
        volume_width = (dates[1] - dates[0]) * 0.8
    else:
        candle_width = volume_width = 0.0005

    # ── PANNELLO 1: PREZZO ──────────────────────────────────────────
    ax1 = plt.subplot(4, 1, 1)

    for i in range(len(df)):
        o, h, l, c = df.iloc[i][['open', 'high', 'low', 'close']]
        color = '#2ecc71' if c >= o else '#e74c3c'
        ax1.plot([dates[i], dates[i]], [l, h], color=color, linewidth=1)
        ax1.add_patch(Rectangle(
            (dates[i] - candle_width / 2, min(o, c)),
            candle_width, abs(c - o) or 1e-8,
            color=color
        ))

    ax1.plot(df['datetime'], df['ma20'],    label='MA20',     linewidth=1)
    ax1.plot(df['datetime'], df['ma50'],    label='MA50',     linewidth=1)
    ax1.plot(df['datetime'], df['ema9'],    label='EMA9',     linewidth=1.2)
    ax1.plot(df['datetime'], df['bb_upper'], '--', linewidth=1, label='BB Upper')
    ax1.plot(df['datetime'], df['bb_mid'],   ':',  linewidth=1, label='BB Mid')
    ax1.plot(df['datetime'], df['bb_lower'], '--', linewidth=1, label='BB Lower')

    if current_price:
        ax1.axhline(y=current_price, linestyle='--', alpha=0.7)

    # DCA lines
    spreads = config.get("dca_spreads", "")
    if isinstance(spreads, str) and current_price:
        try:
            spreads = [float(x.strip()) for x in spreads.split(",")]
            for s in spreads:
                ax1.axhline(current_price * (1 - s), linestyle=':', alpha=0.4, color='gray')
                ax1.axhline(current_price * (1 + s), linestyle=':', alpha=0.2, color='gray')
        except Exception:
            pass

    ax1.legend(loc='upper left', fontsize=7, ncol=3)
    ax1.set_ylabel('Price')
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(df['datetime'].min(), df['datetime'].max())

    # ── PANNELLO 2: VOLUME ──────────────────────────────────────────
    ax2 = plt.subplot(4, 1, 2, sharex=ax1)
    vol_colors = [
        '#2ecc71' if df['close'].iloc[i] >= df['open'].iloc[i] else '#e74c3c'
        for i in range(len(df))
    ]
    ax2.bar(dates, df['volume'], width=volume_width, color=vol_colors, alpha=0.7)
    ax2.set_ylabel('Volume')
    ax2.grid(True, alpha=0.3)

    # ── PANNELLO 3: RSI ─────────────────────────────────────────────
    ax3 = plt.subplot(4, 1, 3, sharex=ax1)
    ax3.plot(df['datetime'], df['rsi'], linewidth=1.5, color='steelblue')
    
    ax3.axhline(70, linestyle='--', color='red', alpha=0.7, label='Overbought (70)')
    ax3.axhline(30, linestyle='--', color='green', alpha=0.7, label='Oversold (30)')
    ax3.axhline(50, linestyle=':', color='gray', alpha=0.5)
    
    ax3.fill_between(df['datetime'], 30, 70, alpha=0.1, color='gray')
    ax3.set_ylim(0, 100)
    ax3.legend(loc='upper left', fontsize=7)
    ax3.set_ylabel('RSI')
    ax3.grid(True, alpha=0.3)

    # ── PANNELLO 4: BBP ─────────────────────────────────────────────
    ax4 = plt.subplot(4, 1, 4, sharex=ax1)
    ax4.plot(df['datetime'], df['bbp'], linewidth=1.5, color='steelblue')

    long_thr  = float(config.get('bb_long_threshold', 0.0))
    short_thr = float(config.get('bb_short_threshold', 1.0))

    ax4.axhline(long_thr,  linestyle='--', color='green',  alpha=0.8, label=f'Long  {long_thr}')
    ax4.axhline(short_thr, linestyle='--', color='red',    alpha=0.8, label=f'Short {short_thr}')
    ax4.axhline(0, linestyle=':', color='gray', alpha=0.5)
    ax4.axhline(1, linestyle=':', color='gray', alpha=0.5)

    # evidenzia zone di segnale
    ax4.fill_between(df['datetime'], -1, long_thr,  alpha=0.07, color='green')
    ax4.fill_between(df['datetime'], short_thr, 2,  alpha=0.07, color='red')

    ax4.legend(loc='upper left', fontsize=7)
    ax4.set_ylabel('BBP')
    ax4.grid(True, alpha=0.3)

    # ── FIX ASSE X: AutoDateLocator su TUTTI gli assi ────────────────
    locator = mdates.AutoDateLocator(minticks=6, maxticks=12)
    formatter = mdates.ConciseDateFormatter(locator)

    for ax in [ax1, ax2, ax3, ax4]:
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

    # ── TITOLO ──────────────────────────────────────────────────────
    interval = config.get('interval', '3m')
    fig.suptitle(
        f"{config.get('trading_pair', 'Unknown')} - DMan V3 "
        f"(BB{bb_length} | RSI14 | {interval})",
        fontsize=13
    )

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf


# ── HELPERS ─────────────────────────────────────────────────────────

def _prepare_dataframe(candles):
    df = pd.DataFrame(candles)
    
    ts_col = next((c for c in ['timestamp', 'time', 'ts', 'datetime'] if c in df.columns), None)
    
    if ts_col:
        sample = df[ts_col].iloc[0]
        if isinstance(sample, (int, float)):
            if sample > 10**12:
                df['datetime'] = pd.to_datetime(df[ts_col], unit='ns')
            elif sample > 10**10:
                df['datetime'] = pd.to_datetime(df[ts_col], unit='ms')
            else:
                df['datetime'] = pd.to_datetime(df[ts_col], unit='s')
        else:
            df['datetime'] = pd.to_datetime(df[ts_col])
    else:
        df['datetime'] = pd.date_range(end=pd.Timestamp.now(), periods=len(df), freq='3min')
    
    return df.sort_values('datetime').reset_index(drop=True)


def _calc_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.bfill().fillna(50)


def _generate_simple_chart(candles_data, current_price):
    if not candles_data:
        return io.BytesIO()
    df = _prepare_dataframe(candles_data)
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(df['datetime'], pd.to_numeric(df.get('close', pd.Series()), errors='coerce'))
    if current_price:
        ax.axhline(y=current_price, linestyle='--')
    locator = mdates.AutoDateLocator(minticks=6, maxticks=12)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_preview_chart(config, candles_data, current_price=None):
    return generate_chart(config, candles_data, current_price)
