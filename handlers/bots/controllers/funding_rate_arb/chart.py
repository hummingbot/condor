"""
Funding Rate Arbitrage chart generation.

Simple chart showing funding rate history and net APY.
"""

import io
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np


def generate_chart(
    config: Dict[str, Any],
    candles_data: List[Dict[str, Any]],
    current_price: Optional[float] = None,
) -> io.BytesIO:
    """
    Generate a simple chart for funding rate arbitrage.

    Shows:
    - Funding rate history for both exchanges (normalized to hourly)
    - Net rate (difference)
    - Entry/exit thresholds
    """
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    conn_a = config.get("connector_pair_a_connector_name", "Exchange A")
    conn_b = config.get("connector_pair_b_connector_name", "Exchange B")
    pair = config.get("connector_pair_a_trading_pair", "Unknown")

    entry_threshold = config.get("entry_threshold", 0.000025)
    exit_threshold = config.get("exit_threshold", 0.000005)

    # Generate sample data for demonstration
    # In real implementation, this would use historical funding rate data
    times = [datetime.now() - timedelta(hours=x) for x in range(24, 0, -1)]

    # Simulate funding rates (replace with real data)
    np.random.seed(42)
    rate_a = 0.00002 + np.random.normal(0, 0.000005, 24)
    rate_b = 0.00001 + np.random.normal(0, 0.000008, 24)
    net_rate = rate_a - rate_b

    # Plot individual rates
    ax1.plot(times, rate_a * 100, label=f"{conn_a} (%/h)", linewidth=1.5)
    ax1.plot(times, rate_b * 100, label=f"{conn_b} (%/h)", linewidth=1.5)
    ax1.axhline(y=entry_threshold * 100, linestyle='--', color='green', alpha=0.7, label=f'Entry ({entry_threshold*100:.4f}%/h)')
    ax1.axhline(y=exit_threshold * 100, linestyle='--', color='red', alpha=0.7, label=f'Exit ({exit_threshold*100:.4f}%/h)')
    ax1.axhline(y=0, linestyle='-', color='gray', alpha=0.3)
    ax1.set_ylabel('Funding Rate (%/h)')
    ax1.legend(loc='upper left', fontsize=8)
    ax1.grid(True, alpha=0.3)
    ax1.set_title(f'Funding Rates - {pair}')

    # Plot net rate
    ax2.fill_between(times, 0, net_rate * 100, where=(net_rate > 0), color='green', alpha=0.3, label='Positive (Long A / Short B)')
    ax2.fill_between(times, 0, net_rate * 100, where=(net_rate < 0), color='red', alpha=0.3, label='Negative (Short A / Long B)')
    ax2.plot(times, net_rate * 100, color='blue', linewidth=2, label='Net Rate')
    ax2.axhline(y=entry_threshold * 100, linestyle='--', color='green', alpha=0.7, label=f'Entry')
    ax2.axhline(y=-entry_threshold * 100, linestyle='--', color='green', alpha=0.7)
    ax2.axhline(y=exit_threshold * 100, linestyle='--', color='red', alpha=0.7, label=f'Exit')
    ax2.axhline(y=-exit_threshold * 100, linestyle='--', color='red', alpha=0.7)
    ax2.axhline(y=0, linestyle='-', color='gray', alpha=0.5)
    ax2.set_ylabel('Net Rate (%/h)')
    ax2.set_xlabel('Time')
    ax2.legend(loc='upper left', fontsize=8)
    ax2.grid(True, alpha=0.3)
    ax2.set_title('Net Funding Rate (A - B)')

    # Format x-axis
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax2.xaxis.set_major_locator(mdates.HourLocator(interval=4))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha='right')

    fig.suptitle(f'Funding Rate Arbitrage - {conn_a} ↔ {conn_b}', fontsize=12)
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
    """Generate preview chart (same as main chart)."""
    return generate_chart(config, candles_data, current_price)