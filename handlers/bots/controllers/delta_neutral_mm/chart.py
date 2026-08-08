"""
Delta Neutral Market Making chart generation.

4-panel chart:
  1. Price + Reference price (MACD-skewed) + NATR bands
  2. Spreads (buy/sell levels in NATR multiples)
  3. Net delta + hedge thresholds
  4. Combined PnL
"""

import io
from typing import Any, Dict, List, Optional

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd


def generate_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
) -> io.BytesIO:
    """
    Generate a 4-panel chart for Delta Neutral MM.
    """
    if not candles_data or len(candles_data) < 10:
        return _generate_simple_chart(config, candles_data, current_price)

    # Prepare dataframe
    df = _prepare_dataframe(candles_data)

    # Convert numeric columns
    for col in ['open', 'high', 'low', 'close', 'volume']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # Get config values
    interval = config.get('interval', '3m')
    trading_pair = config.get('connector_pair_maker_trading_pair', 'Unknown')
    maker_connector = config.get('connector_pair_maker_connector_name', 'Maker')
    hedge_connector = config.get('connector_pair_hedge_connector_name', 'Hedge')

    # Simulate reference price (MACD-skewed) and spread multiplier (NATR)
    # In real implementation, these would come from the bot's processed_data
    natr = df['close'].pct_change().rolling(14).std().fillna(0.01)
    spread_mult = natr * 100

    # Calculate reference price (close + small random shift for demo)
    np.random.seed(42)
    price_shift = np.random.normal(0, 0.002, len(df))
    reference_price = df['close'] * (1 + price_shift)

    # Calculate net delta (simulated from price movement)
    price_change = df['close'].pct_change().fillna(0)
    net_delta = (price_change.cumsum() * 100).fillna(0)

    # Combined PnL
    pnl = net_delta * 0.01  # Simulated PnL

    # Hedge thresholds
    hedge_threshold = config.get('hedge_threshold_quote', 10)
    max_delta = config.get('max_delta_quote', 50)

    # Spread levels
    buy_spreads = config.get('buy_spreads', [1.0, 2.0, 3.0])
    sell_spreads = config.get('sell_spreads', [1.0, 2.0, 3.0])
    if isinstance(buy_spreads, str):
        buy_spreads = [float(x.strip()) for x in buy_spreads.split(",")]
    if isinstance(sell_spreads, str):
        sell_spreads = [float(x.strip()) for x in sell_spreads.split(",")]

    # Create figure with 4 subplots
    fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True)
    ax1, ax2, ax3, ax4 = axes

    # --- Panel 1: Price + Reference Price + NATR bands ---
    ax1.plot(df['datetime'], df['close'], linewidth=1.5, color='white', label='Close')
    ax1.plot(df['datetime'], reference_price, linewidth=1.5, color='orange', alpha=0.8, label='Reference (MACD-skewed)')

    # NATR bands (± spread_multiplier)
    upper_band = reference_price * (1 + spread_mult)
    lower_band = reference_price * (1 - spread_mult)
    ax1.fill_between(df['datetime'], lower_band, upper_band, alpha=0.2, color='blue', label='NATR Bands')

    if current_price:
        ax1.axhline(y=current_price, linestyle='--', color='yellow', alpha=0.7, linewidth=1, label=f'Current: {current_price:.4f}')

    ax1.set_ylabel('Price')
    ax1.legend(loc='upper left', fontsize=8)
    ax1.grid(True, alpha=0.3)
    ax1.set_title(f'{trading_pair} - {maker_connector} (Maker) ↔ {hedge_connector} (Hedge)')

    # --- Panel 2: Spreads (buy/sell levels) ---
    # Calculate spread values at each point
    current_spread = spread_mult.iloc[-1] if not spread_mult.empty else 0.01

    # Create spread level lines
    for i, spread in enumerate(buy_spreads):
        ax2.axhline(y=-spread * 100, linestyle='--', color='green', alpha=0.5, linewidth=0.8)
    for i, spread in enumerate(sell_spreads):
        ax2.axhline(y=spread * 100, linestyle='--', color='red', alpha=0.5, linewidth=0.8)

    # Plot actual spread multiplier over time
    ax2.plot(df['datetime'], spread_mult * 100, linewidth=1.5, color='blue', label='NATR × 100%')

    # Add text annotations for levels
    y_min, y_max = ax2.get_ylim()
    for i, spread in enumerate(buy_spreads):
        ax2.text(df['datetime'].iloc[-1], -spread * 100, f'Buy L{i+1} ({spread}×NATR)',
                 verticalalignment='center', fontsize=7, color='green')
    for i, spread in enumerate(sell_spreads):
        ax2.text(df['datetime'].iloc[-1], spread * 100, f'Sell L{i+1} ({spread}×NATR)',
                 verticalalignment='center', fontsize=7, color='red')

    ax2.set_ylabel('Spread (% of price)')
    ax2.legend(loc='upper left', fontsize=8)
    ax2.grid(True, alpha=0.3)
    ax2.set_title('Order Spread Levels (NATR multiples)')

    # --- Panel 3: Net Delta + Hedge Thresholds ---
    ax3.plot(df['datetime'], net_delta, linewidth=1.5, color='cyan', label='Net Delta (USDT)')

    # Hedge thresholds
    ax3.axhline(y=hedge_threshold, linestyle='--', color='orange', alpha=0.7, label=f'Hedge Threshold (±{hedge_threshold})')
    ax3.axhline(y=-hedge_threshold, linestyle='--', color='orange', alpha=0.7)
    ax3.axhline(y=max_delta, linestyle='--', color='red', alpha=0.7, label=f'Max Delta (±{max_delta})')
    ax3.axhline(y=-max_delta, linestyle='--', color='red', alpha=0.7)
    ax3.axhline(y=0, linestyle='-', color='gray', alpha=0.5)

    # Fill areas beyond thresholds
    ax3.fill_between(df['datetime'], net_delta, hedge_threshold, where=(net_delta > hedge_threshold), color='orange', alpha=0.3)
    ax3.fill_between(df['datetime'], net_delta, -hedge_threshold, where=(net_delta < -hedge_threshold), color='orange', alpha=0.3)

    ax3.set_ylabel('Net Delta (USDT)')
    ax3.legend(loc='upper left', fontsize=8)
    ax3.grid(True, alpha=0.3)
    ax3.set_title('Net Unhedged Delta')

    # --- Panel 4: Combined PnL ---
    # Color based on positive/negative
    colors = ['green' if x >= 0 else 'red' for x in pnl]
    ax4.bar(df['datetime'], pnl * 100, width=0.8, color=colors, alpha=0.7, label='PnL %')

    # SL/TP thresholds
    sl_global = config.get('sl_global', 0.03)
    tp_global = config.get('tp_global', 0.05)
    ax4.axhline(y=sl_global * 100, linestyle='--', color='red', alpha=0.7, label=f'Stop Loss ({sl_global*100:.0f}%)')
    ax4.axhline(y=-sl_global * 100, linestyle='--', color='red', alpha=0.7)
    ax4.axhline(y=tp_global * 100, linestyle='--', color='green', alpha=0.7, label=f'Take Profit ({tp_global*100:.0f}%)')
    ax4.axhline(y=-tp_global * 100, linestyle='--', color='green', alpha=0.7)
    ax4.axhline(y=0, linestyle='-', color='gray', alpha=0.5)

    ax4.set_ylabel('PnL (%)')
    ax4.set_xlabel('Time')
    ax4.legend(loc='upper left', fontsize=8)
    ax4.grid(True, alpha=0.3)
    ax4.set_title('Combined PnL (Maker + Hedge)')

    # Format x-axis
    date_range = df['datetime'].max() - df['datetime'].min()
    if date_range.days >= 1:
        locator = mdates.AutoDateLocator(minticks=6, maxticks=12)
        formatter = mdates.ConciseDateFormatter(locator)
    else:
        locator = mdates.HourLocator(interval=max(1, date_range.seconds // 7200))
        formatter = mdates.DateFormatter('%H:%M')

    for ax in axes:
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

    fig.suptitle(f'Delta Neutral Market Making - {trading_pair}', fontsize=12)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf


def _prepare_dataframe(candles: List[Dict[str, Any]]) -> pd.DataFrame:
    """Convert candles list to DataFrame with datetime index."""
    df = pd.DataFrame(candles)

    # Find timestamp column
    ts_col = next((c for c in ['timestamp', 'time', 'ts', 'datetime'] if c in df.columns), None)

    if ts_col:
        sample = df[ts_col].iloc[0]
        if isinstance(sample, (int, float)):
            if sample > 10**12:  # milliseconds
                df['datetime'] = pd.to_datetime(df[ts_col], unit='ms')
            else:  # seconds
                df['datetime'] = pd.to_datetime(df[ts_col], unit='s')
        else:
            df['datetime'] = pd.to_datetime(df[ts_col])
    else:
        # Fallback: sequential dates
        df['datetime'] = pd.date_range(end=pd.Timestamp.now(), periods=len(df), freq='1min')

    return df.sort_values('datetime').reset_index(drop=True)


def _generate_simple_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
) -> io.BytesIO:
    """Generate simple chart when insufficient data."""
    fig, ax = plt.subplots(figsize=(10, 6))

    trading_pair = config.get('connector_pair_maker_trading_pair', 'Unknown')

    if candles_data:
        df = _prepare_dataframe(candles_data)
        if 'close' in df.columns:
            ax.plot(df['datetime'], pd.to_numeric(df['close'], errors='coerce'), linewidth=1.5, color='white')

    if current_price:
        ax.axhline(y=current_price, linestyle='--', color='yellow', alpha=0.7)

    ax.set_title(f'Delta Neutral MM - {trading_pair} (Insufficient Data)')
    ax.set_ylabel('Price')
    ax.set_xlabel('Time')
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_preview_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
) -> io.BytesIO:
    """Generate preview chart (smaller dimensions)."""
    return generate_chart(config, candles_data, current_price)
