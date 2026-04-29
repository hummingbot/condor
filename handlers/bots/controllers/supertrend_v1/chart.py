"""
SuperTrend V1 chart generation.

4 panels:
  1. Price    – candlesticks + SuperTrend line (green=UP / red=DOWN)
                + MA20/MA50/EMA9
  2. Volume   – colored bars
  3. ATR      – raw ATR series (Wilder smoothing) showing volatility
  4. Distance – % distance between close and ST line,
                with percentage_threshold shown as a dashed line.
                Signal fires when distance < threshold.

Signal logic:
  LONG  when direction == UP   AND distance < percentage_threshold
  SHORT when direction == DOWN AND distance < percentage_threshold
"""

import io
import math

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle


# ── PUBLIC API ───────────────────────────────────────────────────────

def generate_chart(config, candles_data, current_price=None, **kwargs):
    if not candles_data or len(candles_data) < 5:
        return _generate_simple_chart(candles_data, current_price)

    df = _prepare_dataframe(candles_data)

    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df.get(col, 0), errors='coerce').fillna(0)

    length     = int(config.get('length', 20))
    multiplier = float(config.get('multiplier', 4.0))
    pct_thr    = float(config.get('percentage_threshold', 0.01))

    # ── INDICATORI ───────────────────────────────────────────────────
    df['ma20'] = df['close'].rolling(20).mean()
    df['ma50'] = df['close'].rolling(50).mean()
    df['ema9'] = df['close'].ewm(span=9).mean()

    # ATR (Wilder / RMA)
    df['atr'] = _calc_atr_rma(df, length)

    # SuperTrend
    df['st_line'], df['st_dir'] = _calc_supertrend(df, multiplier)

    # % distance close → ST line
    df['st_dist'] = ((df['close'] - df['st_line']).abs() / df['close']).fillna(np.nan)

    # ── FIGURA ───────────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 14))

    dates = mdates.date2num(df['datetime'])
    if len(dates) > 1:
        candle_width = (dates[1] - dates[0]) * 0.6
        volume_width = (dates[1] - dates[0]) * 0.8
    else:
        candle_width = volume_width = 0.0005

    # ── PANNELLO 1: PREZZO + SUPERTREND ──────────────────────────────
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

    ax1.plot(df['datetime'], df['ma20'], label='MA20', linewidth=1)
    ax1.plot(df['datetime'], df['ma50'], label='MA50', linewidth=1)
    ax1.plot(df['datetime'], df['ema9'], label='EMA9', linewidth=1.2)

    # SuperTrend: segmenti colorati per direzione
    _plot_supertrend_colored(ax1, df)

    if current_price:
        ax1.axhline(y=current_price, linestyle='--', alpha=0.6, color='gold', label='Price')

    ax1.legend(loc='upper left', fontsize=7, ncol=4)
    ax1.set_ylabel('Price')
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(df['datetime'].min(), df['datetime'].max())

    # ── PANNELLO 2: VOLUME ───────────────────────────────────────────
    ax2 = plt.subplot(4, 1, 2, sharex=ax1)
    vol_colors = [
        '#2ecc71' if df['close'].iloc[i] >= df['open'].iloc[i] else '#e74c3c'
        for i in range(len(df))
    ]
    ax2.bar(dates, df['volume'], width=volume_width, color=vol_colors, alpha=0.7)
    ax2.set_ylabel('Volume')
    ax2.grid(True, alpha=0.3)

    # ── PANNELLO 3: ATR ──────────────────────────────────────────────
    ax3 = plt.subplot(4, 1, 3, sharex=ax1)
    ax3.plot(df['datetime'], df['atr'], linewidth=1.5, color='steelblue', label=f'ATR({length})')
    ax3.set_ylabel(f'ATR({length})')
    ax3.legend(loc='upper left', fontsize=7)
    ax3.grid(True, alpha=0.3)

    # ── PANNELLO 4: DISTANZA % ───────────────────────────────────────
    ax4 = plt.subplot(4, 1, 4, sharex=ax1)

    # colora la linea: verde se direction UP, rosso se DOWN
    dist_vals = df['st_dist'].values
    dir_vals  = df['st_dir'].values
    dt_vals   = df['datetime'].values

    # Plotta segmenti per direzione
    for i in range(1, len(df)):
        if np.isnan(dist_vals[i]) or np.isnan(dist_vals[i - 1]):
            continue
        seg_color = '#2ecc71' if dir_vals[i] == 1 else '#e74c3c'
        ax4.plot(
            [dt_vals[i - 1], dt_vals[i]],
            [dist_vals[i - 1], dist_vals[i]],
            color=seg_color, linewidth=1.3
        )

    # Soglia: zona verde sotto la linea = signal attivo
    ax4.axhline(pct_thr, linestyle='--', color='gold', linewidth=1.2,
                label=f'Threshold {pct_thr*100:.2f}%')
    ax4.fill_between(df['datetime'], 0, pct_thr, alpha=0.12, color='green',
                     label='Signal zone')

    ax4.set_ylabel('Distance %')
    ax4.set_ylim(bottom=0)
    ax4.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f'{v*100:.2f}%'))
    ax4.legend(loc='upper left', fontsize=7)
    ax4.grid(True, alpha=0.3)

    # ── FIX ASSE X: configurazione intelligente basata sul timeframe ──
    interval = config.get('interval', '3m')
    
    # Calcola il range temporale totale
    date_range = df['datetime'].max() - df['datetime'].min()
    range_days = date_range.days
    range_seconds = date_range.total_seconds()
    
    # Estrai il valore numerico del timeframe
    if interval.endswith('m'):
        tf_minutes = int(interval[:-1])
    elif interval.endswith('h'):
        tf_minutes = int(interval[:-1]) * 60
    elif interval.endswith('d'):
        tf_minutes = int(interval[:-1]) * 1440
    else:
        tf_minutes = 5
    
    # Scegli locator e formatter in base al range e al timeframe
    if range_days >= 3:
        # Più di 3 giorni: mostra le date (Mar30, Apr1, Apr3...)
        locator = mdates.DayLocator(interval=max(1, range_days // 6))
        formatter = mdates.DateFormatter('%b%d')
        rotation = 0
        
    elif range_seconds < 3600 * 2:  # meno di 2 ore
        # Range molto piccolo: mostra minuti (13:00, 13:05, 13:10...)
        # Determina intervallo appropriato per i tick
        if tf_minutes <= 5:
            tick_interval = 5  # ogni 5 minuti
        elif tf_minutes <= 15:
            tick_interval = 15
        else:
            tick_interval = 30
        
        # Crea tick ogni N minuti a partire dall'ora piena
        from datetime import time
        start_hour = df['datetime'].min().hour
        minutes = list(range(0, 60, tick_interval))
        locator = mdates.MinuteLocator(byminute=minutes, interval=1)
        formatter = mdates.DateFormatter('%H:%M')
        rotation = 45
        
    elif range_seconds < 86400:  # meno di 1 giorno
        # Tra 2 ore e 1 giorno: mostra ore (06:00, 12:00, 18:00...)
        # Decidi intervallo ore in base al range
        hour_range = range_seconds / 3600
        if hour_range <= 6:
            hour_interval = 1
        elif hour_range <= 12:
            hour_interval = 2
        elif hour_range <= 24:
            hour_interval = 4
        else:
            hour_interval = 6
        
        locator = mdates.HourLocator(interval=hour_interval)
        formatter = mdates.DateFormatter('%H:%M')
        rotation = 45
        
    else:
        # Tra 1 e 3 giorni: mostra giorno+ora
        locator = mdates.AutoDateLocator(minticks=6, maxticks=10)
        formatter = mdates.DateFormatter('%b%d %H:%M')
        rotation = 45
    
    # Applica a TUTTI gli assi (non solo ax4)
    for ax in [ax1, ax2, ax3, ax4]:
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=rotation, ha='right')
    
    # Opzionale: aggiungi griglia minore per timeframe piccoli
    if range_seconds < 86400 and tf_minutes <= 5:
        for ax in [ax1, ax2, ax3, ax4]:
            ax.xaxis.set_minor_locator(mdates.MinuteLocator(byminute=range(0, 60, 15)))
            ax.grid(True, which='minor', alpha=0.1)

    # ── TITOLO ───────────────────────────────────────────────────────
    interval = config.get('interval', '3m')
    fig.suptitle(
        f"{config.get('trading_pair', 'Unknown')} - SuperTrend V1 "
        f"(length={length}, mult={multiplier} | {interval})",
        fontsize=13
    )

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_preview_chart(config, candles_data, current_price=None, **kwargs):
    return generate_chart(config, candles_data, current_price)


# ── HELPERS ──────────────────────────────────────────────────────────

def _prepare_dataframe(candles):
    df = pd.DataFrame(candles)
    
    # Cerca colonna timestamp
    ts_col = next((c for c in ['timestamp', 'time', 'ts', 'datetime'] if c in df.columns), None)
    
    if ts_col:
        # Converti timestamp
        sample = df[ts_col].iloc[0]
        if isinstance(sample, (int, float)):
            # Determina se è millisecondi o secondi
            if sample > 10**12:  # nanosecondi
                df['datetime'] = pd.to_datetime(df[ts_col], unit='ns')
            elif sample > 10**10:  # millisecondi (dopo il 1970)
                df['datetime'] = pd.to_datetime(df[ts_col], unit='ms')
            else:  # secondi
                df['datetime'] = pd.to_datetime(df[ts_col], unit='s')
        else:
            df['datetime'] = pd.to_datetime(df[ts_col])
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


def _calc_atr_rma(df: pd.DataFrame, period: int) -> pd.Series:
    """ATR with Wilder's RMA smoothing, matching analysis.py logic."""
    high  = df['high']
    low   = df['low']
    close = df['close']

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    # RMA (Wilder): seed with simple mean, then smooth
    atr = tr.copy().astype(float)
    seed_end = period  # first valid ATR at index `period`
    if len(tr) < period + 1:
        return pd.Series([np.nan] * len(df), index=df.index)

    seed = tr.iloc[1:period + 1].mean()  # skip row 0 (no prev_close)
    atr.iloc[:period + 1] = np.nan
    atr.iloc[period] = seed
    alpha = 1.0 / period
    for i in range(period + 1, len(atr)):
        atr.iloc[i] = atr.iloc[i - 1] * (1 - alpha) + tr.iloc[i] * alpha

    return atr


def _calc_supertrend(df: pd.DataFrame, multiplier: float):
    """
    Compute SuperTrend line and direction series.
    Returns (st_line: pd.Series, st_dir: pd.Series)
    direction: 1 = UP (bullish), -1 = DOWN (bearish)
    """
    hl2   = (df['high'] + df['low']) / 2
    atr   = df['atr']

    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    n = len(df)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    st    = np.full(n, np.nan)
    direc = np.zeros(n, dtype=int)

    close = df['close'].values
    bu    = basic_upper.values
    bl    = basic_lower.values

    # find first valid index (where atr is not nan)
    start = df['atr'].first_valid_index()
    if start is None:
        return pd.Series(st, index=df.index), pd.Series(direc, index=df.index)
    si = df.index.get_loc(start)

    upper[si] = bu[si]
    lower[si] = bl[si]
    direc[si] = 1

    for i in range(si + 1, n):
        # tighten upper band
        upper[i] = bu[i] if bu[i] < upper[i - 1] or close[i - 1] > upper[i - 1] else upper[i - 1]
        # widen lower band
        lower[i] = bl[i] if bl[i] > lower[i - 1] or close[i - 1] < lower[i - 1] else lower[i - 1]

        if direc[i - 1] == -1:
            direc[i] = 1 if close[i] > upper[i] else -1
        else:
            direc[i] = -1 if close[i] < lower[i] else 1

    # ST line: lower when UP, upper when DOWN
    for i in range(si, n):
        st[i] = lower[i] if direc[i] == 1 else upper[i]

    # set pre-start to nan/0
    upper[:si] = np.nan
    lower[:si] = np.nan
    st[:si]    = np.nan
    direc[:si] = 0

    return (
        pd.Series(st,    index=df.index),
        pd.Series(direc, index=df.index),
    )


def _plot_supertrend_colored(ax, df: pd.DataFrame):
    """
    Draw the SuperTrend line in segments:
      green  where direction == 1 (UP  / support)
      red    where direction == -1 (DOWN / resistance)
    """
    dt    = df['datetime'].values
    st    = df['st_line'].values
    direc = df['st_dir'].values

    for i in range(1, len(df)):
        if np.isnan(st[i]) or np.isnan(st[i - 1]):
            continue
        color = '#27ae60' if direc[i] == 1 else '#c0392b'
        lw    = 2.0
        ax.plot([dt[i - 1], dt[i]], [st[i - 1], st[i]],
                color=color, linewidth=lw, solid_capstyle='round')

    # Legend proxy patches
    from matplotlib.lines import Line2D
    ax.add_artist(ax.legend(
        handles=[
            Line2D([0], [0], color='#27ae60', linewidth=2, label='ST UP'),
            Line2D([0], [0], color='#c0392b', linewidth=2, label='ST DOWN'),
        ] + ax.get_lines()[:3],  # MA20, MA50, EMA9 already plotted
        loc='upper left', fontsize=7, ncol=5
    ))


def _generate_simple_chart(candles_data, current_price):
    if not candles_data:
        return io.BytesIO()
    df = _prepare_dataframe(candles_data)
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(df['datetime'], pd.to_numeric(df.get('close', pd.Series(dtype=float)), errors='coerce'))
    if current_price:
        ax.axhline(y=current_price, linestyle='--')
    locator = mdates.AutoDateLocator(minticks=6, maxticks=12)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    plt.close(fig)
    buf.seek(0)
    return buf
