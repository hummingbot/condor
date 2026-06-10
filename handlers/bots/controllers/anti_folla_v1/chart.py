"""
Anti-Folla V1 chart generation.

4 panels:
  1. Price   – candlesticks + Rolling VWAP + Donchian Channel (upper/lower)
  2. Volume  – colored bars; volume-spike candles highlighted in yellow
  3. OBV     – On-Balance Volume; bullish/bearish divergence shaded
  4. Score   – rolling composite score (-100…+100) computed from candle-only
               signals (VWAP, Donchian, OBV divergence, Volume Spike, Trade
               Flow). OBI and Funding Rate are excluded (require live data).
               score_buy_threshold and score_sell_threshold shown as dashed
               lines; BUY/SELL zones shaded.

Signal logic:
  BUY  when composite_score >= score_buy_threshold
  SELL when composite_score <= score_sell_threshold
"""

import io

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle


# ── PUBLIC API ───────────────────────────────────────────────────────
def generate_chart(config, candles_data, current_price=None, **kwargs):
    if not candles_data or len(candles_data) < 10:
        return _generate_simple_chart(candles_data, current_price)

    df = _prepare_dataframe(candles_data)
    full_df = df.copy()
    MAX_VISIBLE_CANDLES = 96
    for col in ['open', 'high', 'low', 'close', 'volume']:
        full_df[col] = pd.to_numeric(full_df.get(col, 0), errors='coerce').fillna(0)

    # ── CONFIG ───────────────────────────────────────────────────────
    vwap_period      = int(config.get('vwap_period', 20))
    donchian_period  = int(config.get('donchian_period', 20))
    vol_spike_thr    = float(config.get('volume_spike_threshold', 2.5))
    score_buy_thr    = float(config.get('score_buy_threshold', 50.0))
    score_sell_thr   = float(config.get('score_sell_threshold', -50.0))
    w_vwap           = float(config.get('weight_vwap', 15))
    w_donchian       = float(config.get('weight_donchian', 10))
    w_obv            = float(config.get('weight_obv', 15))
    w_vol_spike      = float(config.get('weight_volume_spike', 10))
    w_trade_flow     = float(config.get('weight_trade_flow', 15))
    obv_lookback     = int(config.get('obv_divergence_lookback', 10))

    # ── INDICATORI ───────────────────────────────────────────────────
    # Rolling VWAP
    pv = full_df['close'] * full_df['volume']
    full_df['vwap'] = (pv.rolling(vwap_period).sum() / full_df['volume'].rolling(vwap_period).sum())

    # Donchian Channel (shift=1 → excludes current candle)
    full_df['don_upper'] = full_df['high'].shift(1).rolling(donchian_period).max()
    full_df['don_lower'] = full_df['low'].shift(1).rolling(donchian_period).min()

    # OBV
    full_df['obv'] = _calc_obv(full_df)

    # Volume spike: current vol vs 20-candle rolling avg (shifted by 1)
    full_df['vol_avg'] = (
        full_df['volume']
        .shift(1)
        .rolling(20)
        .mean()
    )

    full_df['vol_ratio'] = (
        full_df['volume'] /
        full_df['vol_avg'].replace(0, np.nan)
    ).fillna(1.0)
    full_df['is_spike'] = full_df['vol_ratio'] >= vol_spike_thr

    # Rolling composite score (candle-only signals)
    full_df['score'] = _calc_rolling_score(
        full_df, vwap_period, donchian_period, obv_lookback,
        vol_spike_thr, w_vwap, w_donchian, w_obv, w_vol_spike, w_trade_flow
    )
    # SOLO DOPO TAGLI
    df = full_df.tail(MAX_VISIBLE_CANDLES).copy()
    vol_avg = df['vol_avg']
    # ── FIGURA ───────────────────────────────────────────────────────
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

        interval = config.get('interval', '5m')

        if interval in ['1m', '5m']:
            width_factor = 0.65
        else:
            width_factor = 0.85

        candle_width = (dates[1] - dates[0]) * width_factor
        volume_width = (dates[1] - dates[0]) * width_factor
    else:
        candle_width = volume_width = 0.0005

    # ── PANNELLO 1: PREZZO + VWAP + DONCHIAN ─────────────────────────
    for i in range(len(df)):
        o, h, l, c = df.iloc[i][['open', 'high', 'low', 'close']]
        color = '#2ecc71' if c >= o else '#e74c3c'
        ax1.plot([dates[i], dates[i]], [l, h], color=color, linewidth=1)
        ax1.add_patch(Rectangle(
            (dates[i] - candle_width / 2, min(o, c)),
            candle_width, abs(c - o) or 1e-8, color=color
        ))

    # Donchian band
    valid_don = df['don_upper'].notna() & df['don_lower'].notna()
    ax1.fill_between(df['datetime'], df['don_lower'], df['don_upper'], where=valid_don, alpha=0.14, color='#3498db', label='Donchian')
    ax1.plot(df['datetime'], df['don_upper'], linewidth=0.8, color='#3498db', linestyle='--', alpha=0.7)
    ax1.plot(df['datetime'], df['don_lower'], linewidth=0.8, color='#3498db', linestyle='--', alpha=0.7)

    # VWAP
    ax1.plot(df['datetime'], df['vwap'], linewidth=1.5, color='#f39c12', label=f'VWAP({vwap_period})')

    if current_price is not None:
        ax1.axhline(y=current_price, linestyle='--', alpha=0.6, color='gold')

    legend1 = ax1.legend(loc='upper left',fontsize=9,ncol=3,framealpha=0)
    for text in legend1.get_texts():
        text.set_color('white')
    ax1.set_ylabel('Price')
    ax1.set_xlim(df['datetime'].min(), df['datetime'].max())

    # ── PANNELLO 2: VOLUME (spike in giallo) ─────────────────────────
    vol_colors = np.where(
        df['is_spike'],
        np.where(
            df['close'] >= df['open'],
            '#7DFFB3',   # spike bullish
            '#FF9B9B'    # spike bearish
        ),
        np.where(
            df['close'] >= df['open'],
            '#2ecc71',
            '#e74c3c'
        )
    )

    ax2.bar(
        dates,
        df['volume'],
        width=volume_width,
        color=vol_colors,
        alpha=0.8
    )

    # Linea media volume (riferimento spike)
    ax2.plot(df['datetime'], vol_avg, linewidth=1, color='white', linestyle=':', alpha=0.6, label='Avg vol')
    ax2.plot(df['datetime'], vol_avg * vol_spike_thr, linewidth=1, color='#f39c12', linestyle='--', alpha=0.7,
             label=f'Spike ×{vol_spike_thr}')

    legend2 = ax2.legend(loc='upper left', fontsize=9, framealpha=0)
    for text in legend2.get_texts():
        text.set_color('white')
    ax2.set_ylabel('Volume')

    # ── PANNELLO 3: OBV ──────────────────────────────────────────────
    # colora il fill per evidenziare divergenze OBV/prezzo
    ax3.plot(df['datetime'], df['obv'], linewidth=1.3,
             color='#9b59b6', label='OBV')

    # Divergenza rolling semplice: se prezzo sale e OBV scende → bearish (rosso)
    price_trend = df['close'].diff(obv_lookback)
    obv_trend   = df['obv'].diff(obv_lookback)
    bull_div = ((price_trend < 0) & (obv_trend > 0) & df['obv'].notna())
    bear_div = ((price_trend > 0) & (obv_trend < 0) & df['obv'].notna())

    ax3.fill_between(df['datetime'], df['obv'],
                     where=bull_div, alpha=0.25, color='#2ecc71',
                     label='Bullish div')
    ax3.fill_between(df['datetime'], df['obv'],
                     where=bear_div, alpha=0.25, color='#e74c3c',
                     label='Bearish div')

    legend3 = ax3.legend(loc='upper left', fontsize=9, ncol=3, framealpha=0)
    for text in legend3.get_texts():
        text.set_color('white')
    ax3.set_ylabel('OBV')

    # ── PANNELLO 4: SCORE COMPOSITO ──────────────────────────────────
    score_vals = df['score'].values

    # Colora la linea: verde se > 0, rosso se < 0
    for i in range(1, len(df)):
        if np.isnan(score_vals[i]) or np.isnan(score_vals[i - 1]):
            continue
        seg_color = '#2ecc71' if score_vals[i] >= 0 else '#e74c3c'
        ax4.plot(
            [df['datetime'].iloc[i - 1], df['datetime'].iloc[i]],
            [score_vals[i - 1], score_vals[i]],
            color=seg_color, linewidth=1.3
        )

    # Soglie e zone
    ax4.axhline(score_buy_thr,  linestyle='--', color='#2ecc71', linewidth=1.2,
                label=f'Buy ≥{score_buy_thr:.0f}')
    ax4.axhline(score_sell_thr, linestyle='--', color='#e74c3c', linewidth=1.2,
                label=f'Sell ≤{score_sell_thr:.0f}')
    ax4.axhline(0, linestyle=':', color='gray', alpha=0.5)
    ax4.fill_between(
        df['datetime'],
        0,
        score_vals,
        where=score_vals >= 0,
        alpha=0.08,
        color='#2ecc71'
    )

    ax4.fill_between(
        df['datetime'],
        0,
        score_vals,
        where=score_vals < 0,
        alpha=0.08,
        color='#e74c3c'
    )

    ax4.set_ylim(-105, 105)
    ax4.set_ylabel('Score')
    legend4 = ax4.legend(loc='upper left', fontsize=9, ncol=4, framealpha=0)
    for text in legend4.get_texts():
        text.set_color('white')

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

        if minor_locator and interval not in ['1m', '5m']:
            ax.xaxis.set_minor_locator(minor_locator)

        plt.setp(ax.xaxis.get_majorticklabels(), rotation=0, ha='center')

        # grid verticale
        ax.grid(True, which='major', axis='x', linestyle='--', alpha=0.15)

        # grid orizzontale
        ax.grid(True, which='major', axis='y', alpha=0.25)

        # minor grid
        if interval not in ['1m', '5m']:
            ax.grid(True, which='minor', axis='x', linestyle=':', alpha=0.05)

# ── TITOLO ───────────────────────────────────────────────────────
    interval = config.get('interval', '5m')
    fig.suptitle(
        f"{config.get('trading_pair', 'Unknown')} - Anti-Folla V1 "
        f"(VWAP{vwap_period} | Don{donchian_period} | {interval})",
        fontsize=13
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

def _calc_obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume."""
    direction = np.sign(df['close'].diff().fillna(0))
    return (direction * df['volume']).cumsum()


def _calc_rolling_score(
    df: pd.DataFrame,
    vwap_period: int,
    donchian_period: int,
    obv_lookback: int,
    vol_spike_thr: float,
    w_vwap: float,
    w_donchian: float,
    w_obv: float,
    w_vol_spike: float,
    w_trade_flow: float,
) -> pd.Series:
    """
    Vectorised rolling composite score (candle-only signals).
    OBI and Funding Rate are excluded (require live data).
    Active weights are re-normalised to 100 each bar.

    Components:
      VWAP        → +w if close > vwap, -w if close < vwap
      Donchian    → +w if close > don_upper (breakout up),
                    -w if close < don_lower (breakout down)
      OBV div     → +w if bullish div, -w if bearish div
      Volume spike→ +w if spike AND bullish candle, -w if spike AND bearish
      Trade flow  → ±w scaled by buy_pressure centred on 0.5
    """
    close     = df['close']
    open_     = df['open']
    vwap      = df['vwap']
    don_upper = df['don_upper']
    don_lower = df['don_lower']
    obv       = df['obv']
    volume    = df['volume']
    is_spike  = df['is_spike']

    n = len(df)
    scores = np.full(n, np.nan)

    # Pre-compute rolling buy_pressure (10-bar window)
    tf_window = 10
    bull_mask = close >= open_

    bull_vol = (volume.where(bull_mask, 0).rolling(tf_window + 1).sum())
    bear_vol = (volume.where(~bull_mask, 0).rolling(tf_window + 1).sum())
    total_vol   = (bull_vol + bear_vol).replace(0, np.nan)
    buy_pressure = (bull_vol / total_vol).fillna(0.5)

    # OBV trend
    price_trend = close.diff(obv_lookback)
    obv_trend   = obv.diff(obv_lookback)

    start = max(vwap_period, donchian_period, obv_lookback) - 1

    for i in range(start, n):
        score  = 0.0
        active = 0.0

        # VWAP signal
        if not (np.isnan(vwap.iloc[i])):
            active += w_vwap
            score  += w_vwap if close.iloc[i] > vwap.iloc[i] else -w_vwap

        # Donchian breakout
        if not (np.isnan(don_upper.iloc[i]) or np.isnan(don_lower.iloc[i])):
            if close.iloc[i] > don_upper.iloc[i]:
                score  += w_donchian
                active += w_donchian
            elif close.iloc[i] < don_lower.iloc[i]:
                score  -= w_donchian
                active += w_donchian

        # OBV divergence
        if not (np.isnan(price_trend.iloc[i]) or np.isnan(obv_trend.iloc[i])):
            if price_trend.iloc[i] < 0 and obv_trend.iloc[i] > 0:
                score  += w_obv      # bullish divergence
                active += w_obv
            elif price_trend.iloc[i] > 0 and obv_trend.iloc[i] < 0:
                score  -= w_obv      # bearish divergence
                active += w_obv

        # Volume spike
        if is_spike.iloc[i]:
            bull_candle = close.iloc[i] >= open_.iloc[i]
            score  += w_vol_spike if bull_candle else -w_vol_spike
            active += w_vol_spike

        # Trade flow (buy pressure centred on 0.5, scaled to ±1)
        bp = buy_pressure.iloc[i]
        tf_contribution = (bp - 0.5) * 2 * w_trade_flow   # range: -w … +w
        score  += tf_contribution
        active += w_trade_flow

        # Normalise to -100…+100 based on active weights
        if active > 0:
            scores[i] = round((score / active) * 100, 2)

    return pd.Series(scores, index=df.index)

def _generate_simple_chart(candles_data, current_price):
    if not candles_data:
        return io.BytesIO()
    df = _prepare_dataframe(candles_data)
    MAX_VISIBLE_CANDLES = 96
    if len(df) > MAX_VISIBLE_CANDLES:
        df = df.tail(MAX_VISIBLE_CANDLES).reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(df['datetime'], pd.to_numeric(df.get('close', pd.Series(dtype=float)), errors='coerce'))
    if current_price is not None:
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
