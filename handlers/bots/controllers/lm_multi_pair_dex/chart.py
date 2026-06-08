"""
LMMultiPairDEX chart generation for Condor.
"""

import io
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np


def generate_chart(config: dict, candles_data: list, current_price: float = None) -> io.BytesIO:
    """Genera chart per LMMultiPairDEX."""
    if not candles_data or len(candles_data) < 5:
        return _generate_simple_chart(config, candles_data)

    connector = config.get("connector_name", "unknown")
    markets = config.get("markets", ["XRP-RLUSD"])

    fig = plt.figure(figsize=(14, 10))

    # Prezzo
    ax1 = plt.subplot(2, 1, 1)
    _plot_price(ax1, candles_data, config, markets[0] if markets else "Unknown")

    # Depth e allocazione
    ax2 = plt.subplot(2, 1, 2)
    _plot_depth_and_allocation(ax2, config, markets[0] if markets else "Unknown")

    dex_name = "HYPERLIQUID" if "hyperliquid" in connector.lower() else "XRPL"
    fig.suptitle(f"{dex_name} | LMMultiPairDEX - {', '.join(markets[:3])}", fontsize=12)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf


def _plot_price(ax, candles_data, config, pair):
    """Plot prezzo con livelli spread."""
    df = _prepare_dataframe(candles_data)
    if df.empty or 'close' not in df.columns:
        ax.text(0.5, 0.5, "Waiting for price data...", transform=ax.transAxes, ha='center', va='center')
        return

    closes = pd.to_numeric(df['close'], errors='coerce').values
    dates = df['datetime'].values

    ax.plot(dates, closes, linewidth=1.5, color='white', label=pair)

    buy_spreads = config.get("buy_spreads", [0.005, 0.01, 0.02])
    sell_spreads = config.get("sell_spreads", [0.005, 0.01, 0.02])
    current_price = closes[-1] if len(closes) > 0 else 0

    for i, s in enumerate(buy_spreads):
        ax.axhline(y=current_price * (1 - s), linestyle='--', alpha=0.5, color='green', linewidth=0.8)
    for i, s in enumerate(sell_spreads):
        ax.axhline(y=current_price * (1 + s), linestyle='--', alpha=0.5, color='red', linewidth=0.8)

    ax.set_ylabel('Price')
    ax.set_title(f'Price with Spread Levels - {pair}')
    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper left')


def _plot_depth_and_allocation(ax, config, pair):
    """Plot depth e allocazione."""
    buy_spreads = config.get("buy_spreads", [0.005, 0.01, 0.02])
    sell_spreads = config.get("sell_spreads", [0.005, 0.01, 0.02])
    markets = config.get("markets", [])

    # Depth
    amounts = [1000, 2000, 3000]
    bid_prices = [-s * 100 for s in buy_spreads]
    ask_prices = [s * 100 for s in sell_spreads]

    ax.barh(bid_prices, amounts, color='green', alpha=0.7, label='Buy orders', height=0.3)
    ax.barh(ask_prices, amounts, color='red', alpha=0.7, label='Sell orders', height=0.3)

    # Allocazione (testo)
    target = config.get("target_base_pct", 0.5)
    ax.text(0.02, 0.95, f"Target base: {target*100:.0f}%", transform=ax.transAxes, fontsize=9, verticalalignment='top')
    ax.text(0.02, 0.88, f"Coppie: {len(markets)}", transform=ax.transAxes, fontsize=9, verticalalignment='top')

    ax.axhline(y=0, linestyle='-', color='white', alpha=0.5, linewidth=1)
    ax.set_xlabel('Amount (USD)')
    ax.set_ylabel('Spread from mid (%)')
    ax.set_title(f'Order Book Depth - {pair}')
    ax.legend(loc='upper right')
    ax.grid(True, alpha=0.3, axis='x')


def _prepare_dataframe(candles: list) -> pd.DataFrame:
    """Prepara DataFrame dalle candele."""
    if not candles:
        return pd.DataFrame()

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
        df['datetime'] = pd.date_range(end=pd.Timestamp.now(), periods=len(df), freq='5min')

    for col in ['close', 'open', 'high', 'low']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    return df.sort_values('datetime').reset_index(drop=True)


def _generate_simple_chart(config: dict, candles_data: list) -> io.BytesIO:
    """Chart semplice quando mancano dati."""
    fig, ax = plt.subplots(figsize=(12, 6))
    markets = config.get("markets", ["Unknown"])

    if not candles_data:
        msg = f"Waiting for candle data...\n{', '.join(markets)}"
    else:
        msg = f"Not enough data ({len(candles_data)} candles)\nNeed at least 5 candles"

    ax.text(0.5, 0.5, msg, transform=ax.transAxes, ha='center', va='center', fontsize=12)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100)
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_preview_chart(config: dict, candles_data: list, current_price: float = None) -> io.BytesIO:
    return generate_chart(config, candles_data, current_price)
