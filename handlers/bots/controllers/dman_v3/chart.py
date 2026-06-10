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
plt.style.use('dark_background')

def generate_chart(config, candles_data, current_price=None):
    if not candles_data or len(candles_data) < 5:
        return _generate_simple_chart(candles_data, current_price)

    df = _prepare_dataframe(candles_data)
    full_df = df.copy()
    MAX_VISIBLE_CANDLES = 96
    for col in ['open', 'high', 'low', 'close', 'volume']:
        full_df[col] = pd.to_numeric(full_df.get(col, 0), errors='coerce').fillna(0)

    # ── INDICATORI ──────────────────────────────────────────────────
    full_df['ma20'] = full_df['close'].rolling(20).mean()
    full_df['ma50'] = full_df['close'].rolling(50).mean()
    full_df['ema9'] = full_df['close'].ewm(span=9).mean()
    bb_length = int(config.get('bb_length', 20))
    bb_std_val = float(config.get('bb_std', 2.0))
    rolling = full_df['close'].rolling(bb_length)
    full_df['bb_mid'] = rolling.mean()
    bb_std_series = rolling.std()
    full_df['bb_upper'] = (full_df['bb_mid'] + bb_std_val * bb_std_series)
    full_df['bb_lower'] = (full_df['bb_mid'] - bb_std_val * bb_std_series)
    denom = (full_df['bb_upper'] - full_df['bb_lower']).replace(0, np.nan)
    full_df['bbp'] = ((full_df['close'] - full_df['bb_lower']) / denom).clip(-1, 2).fillna(0.5)
    full_df['rsi'] = _calc_rsi(full_df['close'])
    # dataset visualizzato
    df = full_df.tail(MAX_VISIBLE_CANDLES).copy()

    # ── FIGURA ──────────────────────────────────────────────────────
    fig, (ax1, ax2, ax3, ax4) = plt.subplots(4, 1, figsize=(22, 14), sharex=True, gridspec_kw={
            'height_ratios': [4.5, 1.2, 1.3, 1.5]
        }
    )

    fig.patch.set_facecolor('#111111')
    for ax in [ax1, ax2, ax3, ax4]:
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

    # ── PANNELLO 1: PREZZO ──────────────────────────────────────────
    for i in range(len(df)):
        o, h, l, c = df.iloc[i][['open', 'high', 'low', 'close']]
        color = '#2ecc71' if c >= o else '#e74c3c'
        ax1.plot([dates[i], dates[i]], [l, h], color=color, linewidth=1)
        ax1.add_patch(Rectangle(
            (dates[i] - candle_width / 2, min(o, c)),
            candle_width, abs(c - o) or 1e-8,
            color=color
        ))

    ax1.plot(df['datetime'], df['ma20'], label='MA20', linewidth=1.4, color='#f39c12')
    ax1.plot(df['datetime'], df['ma50'], label='MA50', linewidth=1.4, color='#3498db')
    ax1.plot(df['datetime'], df['ema9'], label='EMA9', linewidth=1.4, color='#9b59b6')
    ax1.plot(df['datetime'], df['bb_upper'], '--', linewidth=1.1, color='#aaaaaa', alpha=0.95,label='BB Upper')
    ax1.plot(df['datetime'], df['bb_mid'], ':', linewidth=1.0, color='#aaaaaa', alpha=0.95, label='BB Mid')
    ax1.plot(df['datetime'], df['bb_lower'], '--', linewidth=1.1, color='#aaaaaa', alpha=0.95, label='BB Lower')
    ax1.fill_between(df['datetime'], df['bb_lower'], df['bb_upper'], color='#aaaaaa', alpha=0.18)

    if current_price:
        ax1.axhline(y=current_price, linestyle='--', alpha=0.8, color='gold', linewidth=1.3, label='Price')

    # DCA lines
    spreads = config.get("dca_spreads", "")
    if isinstance(spreads, str) and current_price:
        try:
            spreads = [float(x.strip()) for x in spreads.split(",")]
            for s in spreads:
                ax1.axhline(current_price * (1 - s), linestyle=':', alpha=0.4, color='#7f8c8d')
                ax1.axhline(current_price * (1 + s), linestyle=':', alpha=0.2, color='#7f8c8d')
        except Exception:
            pass

    legend1 = ax1.legend(loc='upper left',fontsize=9,ncol=3,framealpha=0)
    for text in legend1.get_texts():
        text.set_color('white')
    ax1.set_ylabel('Price')
    ax1.set_xlim(df['datetime'].min(), df['datetime'].max())

    # ── PANNELLO 2: VOLUME ──────────────────────────────────────────
    vol_colors = [
        '#2ecc71' if df['close'].iloc[i] >= df['open'].iloc[i] else '#e74c3c'
        for i in range(len(df))
    ]
    ax2.bar(dates, df['volume'], width=volume_width, color=vol_colors, alpha=0.7)
    ax2.set_ylabel('Volume')

    # ── PANNELLO 3: RSI ─────────────────────────────────────────────
    ax3.plot(df['datetime'], df['rsi'], linewidth=1.5, color='steelblue')

    ax3.axhline(70, linestyle='--', color='red', alpha=0.7, label='Overbought (70)')
    ax3.axhline(30, linestyle='--', color='green', alpha=0.7, label='Oversold (30)')
    ax3.axhline(50, linestyle=':', color='#7f8c8d', alpha=0.5)

    ax3.fill_between(df['datetime'], 30, 70, alpha=0.18, color='#aaaaaa')
    ax3.set_ylim(10, 90)
    legend3 = ax3.legend(loc='upper left', fontsize=9, framealpha=0)
    for text in legend3.get_texts():
        text.set_color('white')
    ax3.set_ylabel('RSI')

    # ── PANNELLO 4: BBP ─────────────────────────────────────────────
    ax4.plot(df['datetime'], df['bbp'], linewidth=1.5, color='steelblue')
    long_thr  = float(config.get('bb_long_threshold', 0.0))
    short_thr = float(config.get('bb_short_threshold', 1.0))

    ax4.axhline(long_thr,  linestyle='--', color='green',  alpha=0.8, label=f'Long  {long_thr}')
    ax4.axhline(short_thr, linestyle='--', color='red',    alpha=0.8, label=f'Short {short_thr}')
    ax4.axhline(0, linestyle=':', color='#7f8c8d', alpha=0.5)
    ax4.axhline(1, linestyle=':', color='#7f8c8d', alpha=0.5)

    # evidenzia zone di segnale
    ax4.fill_between(df['datetime'], -1, long_thr,  alpha=0.22, color='#00ff88')
    ax4.fill_between(df['datetime'], short_thr, 2,  alpha=0.22, color='#ff4d6d')

    legend4 = ax4.legend(loc='upper left', fontsize=9, framealpha=0)
    for text in legend4.get_texts():
        text.set_color('white')
    ax4.set_ylabel('BBP')

    # ── FIX ASSE X BASATO SUL TIMEFRAME ───────────────────────────────

    interval = config.get('interval', '5m')

    if interval == '1m':
        locator = mdates.MinuteLocator(byminute=[0, 15, 30, 45])
        formatter = mdates.DateFormatter('%H:%M')
        minor_locator = mdates.MinuteLocator(interval=5)

    elif interval == '5m':
        locator = mdates.HourLocator(interval=1)
        formatter = mdates.DateFormatter('%H:%M')
        minor_locator = mdates.MinuteLocator(byminute=[0, 15, 30, 45])

    elif interval == '15m':

        locator = mdates.HourLocator(interval=3)
        formatter = mdates.DateFormatter('%d %H:%M')
        minor_locator = mdates.HourLocator(interval=1)

    elif interval == '1h':

        locator = mdates.HourLocator(interval=12)
        formatter = mdates.DateFormatter('%b%d %H:%M')
        minor_locator = mdates.HourLocator(interval=3)

    elif interval == '8h':

        locator = mdates.DayLocator(interval=4)
        formatter = mdates.DateFormatter('%b%d')
        minor_locator = mdates.DayLocator(interval=1)

    else:

        locator = mdates.AutoDateLocator(minticks=6, maxticks=12)
        formatter = mdates.ConciseDateFormatter(locator)
        minor_locator = None

    # ── APPLICA A TUTTI GLI ASSI ──────────────────────────────────────

    ax1.tick_params(labelbottom=False)
    ax2.tick_params(labelbottom=False)
    ax3.tick_params(labelbottom=False)

    for ax in [ax1, ax2, ax3, ax4]:

        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)

        if minor_locator:
            ax.xaxis.set_minor_locator(minor_locator)

        plt.setp(ax.xaxis.get_majorticklabels(), rotation=0, ha='center')

        # grid verticale
        ax.grid(True, which='major', axis='x', linestyle='--', alpha=0.15)

        # grid orizzontale
        ax.grid( True, which='major', axis='y', alpha=0.25)

        # minor grid
        ax.grid(True, which='minor', axis='y',alpha=0.06)
    # ── TITOLO ──────────────────────────────────────────────────────
    interval = config.get('interval', '3m')
    fig.suptitle(
        f"{config.get('trading_pair', 'Unknown')} - DMan V3 "
        f"(BB{bb_length} | RSI14 | {interval})",
        fontsize=13, color='white'
    )

    plt.subplots_adjust(hspace=0.05, top=0.94, bottom=0.06)
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf


# ── HELPERS ─────────────────────────────────────────────────────────

def _prepare_dataframe(candles, timezone='UTC'):
    df = pd.DataFrame(candles)

    # Cerca colonna timestamp
    ts_col = next((c for c in ['timestamp', 'time', 'ts', 'datetime'] if c in df.columns), None)

    if ts_col:
        # Converti timestamp
        sample = df[ts_col].iloc[0]
        if isinstance(sample, (int, float)):
            # Determina se è millisecondi o secondi
            if sample > 10**12:  # nanosecondi
                df['datetime'] = (
                    pd.to_datetime(df[ts_col], unit='ns', utc=True)
                      .dt.tz_convert(timezone)
                      .dt.tz_localize(None)
                )
            elif sample > 10**10:  # millisecondi (dopo il 1970)
                df['datetime'] = (
                    pd.to_datetime(df[ts_col], unit='ms', utc=True)
                      .dt.tz_convert(timezone)
                      .dt.tz_localize(None)
                )
            else:  # secondi
                df['datetime'] = (
                    pd.to_datetime(df[ts_col], unit='s', utc=True)
                      .dt.tz_convert(timezone)
                      .dt.tz_localize(None)
                )
        else:
            df['datetime'] = (pd.to_datetime(df[ts_col], utc=True).dt.tz_convert(timezone).dt.tz_localize(None))
    else:
        # Fallback: crea date sequenziali usando l'intervallo dalla config
        # NOTA: questo è un fallback, idealmente dovresti avere timestamp reali
        freq = config.get('interval', '5m') if 'config' in locals() else '5m'
        df['datetime'] = pd.date_range(
            end=pd.Timestamp.now(),
            periods=len(df),
            freq=freq
        )

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
    MAX_VISIBLE_CANDLES = 96
    if len(df) > MAX_VISIBLE_CANDLES:
        df = df.tail(MAX_VISIBLE_CANDLES).reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(df['datetime'], pd.to_numeric(df.get('close', pd.Series()), errors='coerce'))
    if current_price:
        ax.axhline(y=current_price, linestyle='--')
    locator = mdates.AutoDateLocator(minticks=6, maxticks=12)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=0, ha='center')
    plt.subplots_adjust(hspace=0.05, top=0.94, bottom=0.06)
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_preview_chart(config, candles_data, current_price=None):
    return generate_chart(config, candles_data, current_price)
