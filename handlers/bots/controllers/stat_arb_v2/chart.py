"""
Statistical Arbitrage V2 chart generation.

Generates a chart with:
- Normalized price series of both assets
- Spread between the two assets (as percentage)
- Z-score with entry thresholds
"""

import io
import math

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np


def generate_chart(
    config: dict,
    candles_data: list,
    current_price: float = None,
) -> io.BytesIO:
    """
    Generate a chart for Statistical Arbitrage V2.
    
    Shows:
    - Normalized price comparison (both assets starting at 1)
    - Spread between the two assets (percentage)
    - Z-score with entry thresholds
    """
    if not candles_data or len(candles_data) < 10:
        return _generate_simple_chart(config, candles_data, current_price)
    
    # Ottieni le coppie
    dom_pair = config.get("trading_pair_dominant", "Dominant")
    hedge_pair = config.get("trading_pair_hedge", "Hedge")
    interval = config.get("interval", "5m")
    entry_threshold = config.get("entry_threshold", 2.0)
    
    # Prepara i dati
    df = _prepare_dataframe(candles_data)
    
    # Verifica se abbiamo i dati di entrambe le coppie
    has_dom = 'close_dom' in df.columns or 'close' in df.columns
    has_hedge = 'close_hedge' in df.columns
    
    if has_dom and has_hedge:
        # Dati combinati - usa close_dom e close_hedge
        dom_closes = pd.to_numeric(df['close_dom'], errors='coerce').fillna(0).values
        hedge_closes = pd.to_numeric(df['close_hedge'], errors='coerce').fillna(0).values
    elif 'close' in df.columns:
        # Solo una coppia - usa simulazione per demo
        dom_closes = pd.to_numeric(df['close'], errors='coerce').fillna(0).values
        # Crea una serie fittizia per la hedge (sposta leggermente)
        hedge_closes = dom_closes * (1 + np.random.randn(len(dom_closes)) * 0.01)
        # Applica smoothing
        hedge_closes = pd.Series(hedge_closes).rolling(window=5, min_periods=1).mean().values
    else:
        return _generate_simple_chart(config, candles_data, current_price)
    
    if len(dom_closes) < 10 or len(hedge_closes) < 10:
        return _generate_simple_chart(config, candles_data, current_price)
    
    # Normalizza i prezzi (partono da 1)
    dom_norm = dom_closes / dom_closes[0] if dom_closes[0] > 0 else dom_closes
    hedge_norm = hedge_closes / hedge_closes[0] if hedge_closes[0] > 0 else hedge_closes
    
    # Calcola lo spread percentuale
    # spread = (dominant - hedge) / hedge * 100
    spread = (dom_norm - hedge_norm) / hedge_norm * 100
    
    # Calcola z-score
    mean_spread = np.mean(spread)
    std_spread = np.std(spread)
    if std_spread > 0:
        z_score = (spread - mean_spread) / std_spread
    else:
        z_score = np.zeros_like(spread)
    
    dates = df['datetime'].values
    
    # Crea la figura con 2 pannelli
    fig = plt.figure(figsize=(14, 10))
    
    # PANNELLO 1: Prezzi normalizzati
    ax1 = plt.subplot(2, 1, 1)
    
    ax1.plot(dates, dom_norm, label=f"{dom_pair} (normalized)", linewidth=1.5, color='cyan')
    ax1.plot(dates, hedge_norm, label=f"{hedge_pair} (normalized)", linewidth=1.5, color='orange')
    ax1.set_ylabel('Normalized Price')
    ax1.set_title(f'Statistical Arbitrage: {dom_pair} vs {hedge_pair}')
    ax1.legend(loc='upper left')
    ax1.grid(True, alpha=0.3)
    ax1.axhline(y=1.0, linestyle='--', alpha=0.5, color='gray')
    
    # PANNELLO 2: Spread e Z-score
    ax2 = plt.subplot(2, 1, 2)
    
    # Spread come area
    ax2.fill_between(dates, 0, spread, alpha=0.3, color='blue', label='Spread %')
    ax2.plot(dates, spread, linewidth=1, color='blue', alpha=0.7)
    
    # Z-score (secondo asse)
    ax2_twin = ax2.twinx()
    ax2_twin.plot(dates, z_score, linewidth=1.5, color='purple', label='Z-Score')
    ax2_twin.axhline(y=entry_threshold, linestyle='--', alpha=0.7, color='red', linewidth=1, label=f'Entry +{entry_threshold}')
    ax2_twin.axhline(y=-entry_threshold, linestyle='--', alpha=0.7, color='green', linewidth=1, label=f'Entry -{entry_threshold}')
    ax2_twin.axhline(y=0, linestyle='-', alpha=0.5, color='gray', linewidth=0.8)
    ax2_twin.set_ylabel('Z-Score', color='purple')
    ax2_twin.tick_params(axis='y', labelcolor='purple')
    
    ax2.set_ylabel('Spread (%)', color='blue')
    ax2.tick_params(axis='y', labelcolor='blue')
    ax2.set_xlabel('Time')
    ax2.grid(True, alpha=0.3)
    
    # Legenda combinata
    lines1, labels1 = ax2.get_legend_handles_labels()
    lines2, labels2 = ax2_twin.get_legend_handles_labels()
    ax2.legend(lines1 + lines2, labels1 + labels2, loc='upper left', fontsize=9)
    # Formatta l'asse x
    if len(dates) > 1:
        # Converti in Timestamp per il calcolo
        start_date = pd.Timestamp(dates[0])
        end_date = pd.Timestamp(dates[-1])
        date_range = end_date - start_date
        total_seconds = date_range.total_seconds()
        days = date_range.days
    else:
        total_seconds = 3600
        days = 0
    
    if days >= 3:
        locator = mdates.DayLocator(interval=max(1, days // 6))
        formatter = mdates.DateFormatter('%b%d')
        rotation = 0
    elif total_seconds < 3600 * 2:
        locator = mdates.MinuteLocator(interval=15)
        formatter = mdates.DateFormatter('%H:%M')
        rotation = 45
    else:
        locator = mdates.HourLocator(interval=max(1, int(total_seconds / 3600 // 4)))
        formatter = mdates.DateFormatter('%H:%M')
        rotation = 45
    
    for ax in [ax1, ax2]:
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=rotation, ha='right')
    
    # Titolo con info
    fig.suptitle(
        f"{dom_pair} vs {hedge_pair} - Spread Analysis (Z-Score threshold: {entry_threshold}) | {interval}",
        fontsize=12,
        y=0.98
    )
    
    plt.tight_layout()
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_preview_chart(config: dict, candles_data: list, current_price: float = None) -> io.BytesIO:
    """Alias for generate_chart."""
    return generate_chart(config, candles_data, current_price)


def _prepare_dataframe(candles: list) -> pd.DataFrame:
    """Prepara il DataFrame dalle candele."""
    if not candles:
        return pd.DataFrame()
    
    df = pd.DataFrame(candles)
    
    # Cerca colonna timestamp
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
        # Crea date sequenziali
        df['datetime'] = pd.date_range(end=pd.Timestamp.now(), periods=len(df), freq='5min')
    
    # Converti colonne numeriche
    for col in ['close', 'close_dom', 'close_hedge', 'open', 'open_dom', 'open_hedge', 'high', 'low']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    return df.sort_values('datetime').reset_index(drop=True)


def _generate_simple_chart(config: dict, candles_data: list, current_price: float = None) -> io.BytesIO:
    """Genera un chart semplice quando non ci sono abbastanza dati."""
    fig, ax = plt.subplots(figsize=(12, 6))
    
    dom_pair = config.get("trading_pair_dominant", "Dominant")
    hedge_pair = config.get("trading_pair_hedge", "Hedge")
    
    if not candles_data or len(candles_data) == 0:
        ax.text(0.5, 0.5, f"Waiting for candle data...\n{dom_pair} vs {hedge_pair}",
                transform=ax.transAxes, ha='center', va='center', fontsize=12)
    else:
        ax.text(0.5, 0.5, f"Not enough data to generate chart for\n{dom_pair} vs {hedge_pair}\n\nNeed at least 10 candles, got {len(candles_data)}",
                transform=ax.transAxes, ha='center', va='center', fontsize=12)
    
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')
    
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    plt.close(fig)
    buf.seek(0)
    return buf
