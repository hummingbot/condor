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

    timezone = config.get('timezone', 'Europe/Rome')
    df = _prepare_dataframe(candles_data, timezone=timezone)

    # ── MOSTRA SOLO ULTIME 96 CANDELE ─────────────────────
    MAX_VISIBLE_CANDLES = 96

    # mantieni dataset completo per ATR/ST
    full_df = df.copy()

    # df visualizzato
    if len(df) > MAX_VISIBLE_CANDLES:
        df = df.tail(MAX_VISIBLE_CANDLES).reset_index(drop=True)

    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df.get(col, 0), errors='coerce').fillna(0)

    length     = int(config.get('length', 20))
    multiplier = float(config.get('multiplier', 4.0))
    pct_thr    = float(config.get('percentage_threshold', 0.01))

    # ── INDICATORI ───────────────────────────────────────────────────
    full_df['ma20'] = full_df['close'].rolling(20).mean()
    full_df['ma50'] = full_df['close'].rolling(50).mean()
    full_df['ema9']  = full_df['close'].ewm(span=9).mean()

    # ── CALCOLI SU DATASET COMPLETO ───────────────────────

    full_df['atr'] = _calc_atr_rma(full_df, length)

    full_df['st_line'], full_df['st_dir'] = _calc_supertrend(
        full_df,
        multiplier
    )

    full_df['st_dist'] = (
        (full_df['close'] - full_df['st_line']).abs()
        / full_df['close']
    ).fillna(np.nan)

    # ── PRENDI SOLO ULTIME 96 CANDELE VISUALI ────────────

    df = full_df.tail(MAX_VISIBLE_CANDLES).reset_index(drop=True)

    # ── FIGURA ───────────────────────────────────────────────────────
    fig, (ax1, ax2, ax3, ax4) = plt.subplots(
        4,
        1,
        figsize=(22, 14),
        sharex=True,
        gridspec_kw={
            'height_ratios': [4.5, 1.2, 1.3, 1.5]
        }
    )
    fig.patch.set_facecolor('#111111')
    dates = mdates.date2num(df['datetime'])
    if len(dates) > 1:
        candle_width = (dates[1] - dates[0]) * 0.85
        volume_width = (dates[1] - dates[0]) * 0.85
    else:
        candle_width = volume_width = 0.0005
    for ax in [ax1, ax2, ax3, ax4]:

        ax.set_facecolor('#111111')

        ax.tick_params(colors='white')

        ax.yaxis.label.set_color('white')

        ax.spines['bottom'].set_color('#444')
        ax.spines['top'].set_color('#444')
        ax.spines['left'].set_color('#444')
        ax.spines['right'].set_color('#444')

    # ── PANNELLO 1: PREZZO + SUPERTREND ──────────────────────────────
    for i in range(len(df)):
        o, h, l, c = df.iloc[i][['open', 'high', 'low', 'close']]
        color = '#2ecc71' if c >= o else '#e74c3c'
        ax1.plot([dates[i], dates[i]], [l, h], color=color, linewidth=1.2)
        ax1.add_patch(Rectangle(
            (dates[i] - candle_width / 2, min(o, c)),
            candle_width, abs(c - o) or 1e-8,
            color=color
        ))

    ax1.plot(df['datetime'], df['ma20'], label='MA20', linewidth=1.2, color='#f39c12' )
    ax1.plot(df['datetime'], df['ma50'], label='MA50', linewidth=1.2, color='#3498db')
    ax1.plot(df['datetime'], df['ema9'], label='EMA9', linewidth=1.2, color='#9b59b6')

    # SuperTrend: segmenti colorati per direzione
    _plot_supertrend_colored(ax1, df)

    if current_price:
        ax1.axhline(y=current_price, linestyle='--', alpha=0.6, color='gold', label='Price')
    handles, labels = ax1.get_legend_handles_labels()
    legend_map = dict(zip(labels, handles))
    desired_order = ['MA20', 'ST UP', 'MA50', 'ST DOWN', 'EMA9', 'Price']
    ordered_handles = []
    ordered_labels = []
    for label in desired_order:
        if label in legend_map:
            ordered_handles.append(legend_map[label])
            ordered_labels.append(label)
    legend1 = ax1.legend(ordered_handles, ordered_labels, loc='upper left', fontsize=9, ncol=3, framealpha=0)
    for text in legend1.get_texts():
        text.set_color('white')
    ax1.set_ylabel('Price')
    ax1.grid(True, alpha=0.3)
    ax1.set_xlim(df['datetime'].min(), df['datetime'].max())

    # ── PANNELLO 2: VOLUME ───────────────────────────────────────────
    vol_colors = [
        '#2ecc71' if df['close'].iloc[i] >= df['open'].iloc[i] else '#e74c3c'
        for i in range(len(df))
    ]
    ax2.bar(dates, df['volume'], width=volume_width, color=vol_colors, alpha=0.7)
    ax2.set_ylabel('Volume')
    ax2.grid(True, alpha=0.3)

    # ── PANNELLO 3: ATR ──────────────────────────────────────────────
    ax3.plot(df['datetime'], df['atr'], linewidth=1.5, color='steelblue', label=f'ATR({length})')
    first_valid_atr = df['atr'].notna().to_numpy().argmax()

    if first_valid_atr is not None and first_valid_atr > 0:

        ax3.axvspan(
            df['datetime'].iloc[0],
            df['datetime'].iloc[first_valid_atr],
            color='gray',
            alpha=0.10,
            label='ATR warmup'
        )
    ax3.set_ylabel(f'ATR({length})')
    legend3 = ax3.legend(loc='upper left', fontsize=9,framealpha=0)
    for text in legend3.get_texts():
        text.set_color('white')
    ax3.grid(True, alpha=0.3)

    # ── PANNELLO 4: DISTANZA % ───────────────────────────────────────
    # colora la linea: verde se direction UP, rosso se DOWN
    dist_vals = df['st_dist'].values
    dir_vals  = df['st_dir'].values
    dt_vals   = df['datetime'].values

    # Plotta segmenti per direzione
    up_label_added = False
    down_label_added = False

    # Evidenzia area warmup indicatori
    first_valid = df['st_dist'].first_valid_index()

    if first_valid is not None and first_valid > 0:

        ax4.axvspan(
            df['datetime'].iloc[0],
            df['datetime'].iloc[first_valid],
            color='gray',
            alpha=0.10,
            label='Indicator warmup'
        )
    for i in range(1, len(df)):

        if np.isnan(dist_vals[i]) or np.isnan(dist_vals[i - 1]):
            continue

        is_up = dir_vals[i] == 1

        seg_color = '#2ecc71' if is_up else '#e74c3c'

        label = None

        if is_up and not up_label_added:
            label = 'Distance UP'
            up_label_added = True

        elif not is_up and not down_label_added:
            label = 'Distance DOWN'
            down_label_added = True

        ax4.plot(
            [dt_vals[i - 1], dt_vals[i]],
            [dist_vals[i - 1], dist_vals[i]],
            color=seg_color,
            linewidth=1.4,
            label=label
        )

    # Soglia: zona verde sotto la linea = signal attivo
    ax4.axhline(pct_thr, linestyle='--', color='#f1c40f', linewidth=1.8,
                label=f'Threshold {pct_thr*100:.2f}%')
    ax4.fill_between(df['datetime'], 0, pct_thr, alpha=0.2, color='green',
                     label='Signal zone')

    ax4.set_ylabel('Distance %')
    ax4.set_ylim(bottom=0)
    ax4.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f'{v*100:.2f}%'))
    legend4 = ax4.legend(loc='upper left', fontsize=9, ncol=2, framealpha=0)
    for text in legend4.get_texts():
        text.set_color('white')
    ax4.grid(True, alpha=0.3)

# ── FIX ASSE X BASATO SUL TIMEFRAME ───────────────────────────────

    interval = config.get('interval', '5m')
    # Mapping personalizzato timeframe -> tick principali
    if interval == '1m':
        # Tick ogni 15 minuti
        locator = mdates.MinuteLocator(byminute=[0, 15, 30, 45])
        formatter = mdates.DateFormatter('%H:%M')

        # Tick minori ogni 5 minuti
        minor_locator = mdates.MinuteLocator(interval=5)

    elif interval == '5m':
        # Tick ogni ora
        locator = mdates.HourLocator(interval=1)
        formatter = mdates.DateFormatter('%H:%M')

        # Tick minori ogni 15 minuti
        minor_locator = mdates.MinuteLocator(byminute=[0, 15, 30, 45])

    elif interval == '15m':
        # Tick ogni 3 ore
        locator = mdates.HourLocator(interval=3)
        formatter = mdates.DateFormatter('%d %H:%M')

        # Tick minori ogni ora
        minor_locator = mdates.HourLocator(interval=1)

    elif interval == '1h':
        # Tick ogni 12 ore
        locator = mdates.HourLocator(interval=12)
        formatter = mdates.DateFormatter('%b%d %H:%M')
        minor_locator = mdates.HourLocator(interval=3)

    elif interval == '8h':
        # Tick ogni 4 giorni
        locator = mdates.DayLocator(interval=4)
        formatter = mdates.DateFormatter('%b%d')
        minor_locator = mdates.DayLocator(interval=1)

    else:
        # fallback intelligente
        locator = mdates.AutoDateLocator(minticks=6, maxticks=12)
        formatter = mdates.ConciseDateFormatter(locator)
        minor_locator = None
    
    # Applica a TUTTI gli assi (non solo ax4)
    ax1.tick_params(labelbottom=False)
    ax2.tick_params(labelbottom=False)
    ax3.tick_params(labelbottom=False)
    for ax in [ax1, ax2, ax3, ax4]:
        ax.grid(True, axis='x', which='major', linestyle='--', alpha=0.30, linewidth=0.8)
        ax.grid(False, which='minor', axis='x')
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)

        if minor_locator:
            ax.xaxis.set_minor_locator(minor_locator)

        plt.setp(
            ax.xaxis.get_majorticklabels(),
            rotation=0,
            ha='center'
        )
        # Grid orizzontale
        ax.grid(True, which='major', axis='y', alpha=0.30)
        # Grid verticale tratteggiato
        ax.grid(True, which='major', axis='x', linestyle='--', alpha=0.15)

        # Minor grid molto leggera
        ax.grid(True, which='minor', axis='y', alpha=0.05)
    # ── TITOLO ───────────────────────────────────────────────────────
    interval = config.get('interval', '5m')
    fig.suptitle(
        f"{config.get('trading_pair', 'Unknown')} - SuperTrend V1 "
        f"(length={length}, mult={multiplier} | {interval})",
        fontsize=13, color='white'
    )

    plt.subplots_adjust(hspace=0.05, top=0.94, bottom=0.06)
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_preview_chart(config, candles_data, current_price=None, **kwargs):
    return generate_chart(config, candles_data, current_price)


# ── HELPERS ──────────────────────────────────────────────────────────

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

    dt    = df['datetime'].values
    st    = df['st_line'].values
    direc = df['st_dir'].values

    up_added = False
    down_added = False

    for i in range(1, len(df)):

        if np.isnan(st[i]) or np.isnan(st[i - 1]):
            continue

        is_up = direc[i] == 1

        color = '#27ae60' if is_up else '#c0392b'

        label = None

        if is_up and not up_added:
            label = 'ST UP'
            up_added = True

        elif not is_up and not down_added:
            label = 'ST DOWN'
            down_added = True

        ax.plot(
            [dt[i - 1], dt[i]],
            [st[i - 1], st[i]],
            color=color,
            linewidth=2.4,
            solid_capstyle='round',
            label=label
        )

def _generate_simple_chart(candles_data, current_price):
    if not candles_data:
        return io.BytesIO()
    df = _prepare_dataframe(candles_data, timezone=timezone)

# ── LIMITA CANDELE VISUALIZZATE ────────────────────────────────────
    MAX_VISIBLE_CANDLES = 96
    if len(df) > MAX_VISIBLE_CANDLES:
        df = df.tail(MAX_VISIBLE_CANDLES).reset_index(drop=True)

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
