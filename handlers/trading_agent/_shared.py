"""Shared utilities for trading agent handlers."""

import logging
import time
from typing import Any, Dict

from utils.telegram_formatters import escape_markdown_v2

logger = logging.getLogger(__name__)

# State keys
TA_STATE_KEY = "ta_state"
TA_SELECTED_STRATEGY = "ta_selected_strategy"
TA_SELECTED_AGENT = "ta_selected_agent"
TA_CONFIG_PARAMS = "ta_config_params"


def clear_ta_state(context) -> None:
    """Clear all trading agent state."""
    context.user_data.pop(TA_STATE_KEY, None)
    context.user_data.pop(TA_SELECTED_STRATEGY, None)
    context.user_data.pop(TA_SELECTED_AGENT, None)
    context.user_data.pop(TA_CONFIG_PARAMS, None)


def format_agent_status(info: dict) -> str:
    """Format an agent info dict as display text (unescaped)."""
    status_emoji = {"running": "🟢", "paused": "⏸", "stopped": "🔴"}.get(info["status"], "❓")
    lines = [
        f"{status_emoji} {info['strategy']} ({info['agent_id']})",
        f"Status: {info['status']}",
        f"Pair: {info.get('pair', 'N/A')}",
        f"Ticks: {info['tick_count']}",
        f"Daily PnL: ${info['daily_pnl']:+.2f}",
        f"Total Volume: ${info.get('total_volume', 0):,.2f}",
        f"Open executors: {info['open_executors']}",
        f"Exposure: ${info.get('total_exposure', 0):,.2f}",
        f"Daily cost: ${info['daily_cost']:.2f}",
        f"Frequency: {info['frequency_sec']}s",
    ]
    if info.get("last_error"):
        lines.append(f"Last error: {info['last_error'][:100]}")
    return "\n".join(lines)


def format_strategy_summary(strategy) -> str:
    """Format a strategy for display (unescaped)."""
    skills_str = ", ".join(strategy.skills) if strategy.skills else "none"
    lines = [
        f"📋 {strategy.name}",
        f"Agent: {strategy.agent_key}",
        f"Skills: {skills_str}",
    ]
    if strategy.description:
        lines.append(f"Description: {strategy.description}")
    config = strategy.default_config
    if config.get("server_name"):
        lines.append(f"Server: {config['server_name']}")
    if config.get("connector_name"):
        lines.append(f"Connector: {config['connector_name']}")
    if config.get("trading_pair"):
        lines.append(f"Pair: {config['trading_pair']}")
    if config.get("frequency_sec"):
        lines.append(f"Frequency: {config['frequency_sec']}s")
    return "\n".join(lines)
