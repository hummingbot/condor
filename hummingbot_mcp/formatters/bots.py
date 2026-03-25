"""
Bot-related formatters for logs and status information.

This module provides table formatters for bot logs and active bot status.
"""
from typing import Any

from .base import (
    format_number,
    format_percentage,
    format_table_separator,
    format_time_only,
    get_field,
    truncate_string,
)


def format_bot_logs_as_table(logs: list[dict[str, Any]]) -> str:
    """
    Format bot logs as a table string for better LLM processing.

    Columns: time | level | category | message

    Args:
        logs: List of log entry dictionaries

    Returns:
        Formatted table string
    """
    if not logs:
        return "No logs found."

    # Header
    header = "time     | level | category | message"
    separator = format_table_separator()

    # Format each log as a row
    rows = []
    for log_entry in logs:
        time_str = format_time_only(get_field(log_entry, "timestamp", default=0))
        level = str(get_field(log_entry, "level_name", default="INFO"))[:4]
        category = str(get_field(log_entry, "log_category", default="gen"))[:3]
        message = truncate_string(str(get_field(log_entry, "msg", default="")))

        row = f"{time_str} | {level:4} | {category:3} | {message}"
        rows.append(row)

    return f"{header}\n{separator}\n" + "\n".join(rows)


def format_active_bots_as_table(bots_data: dict[str, Any]) -> str:
    """
    Format active bots data as a table string for better LLM processing.

    Columns: bot_name | controller | status | realized_pnl | unrealized_pnl | global_pnl | volume | errors

    Args:
        bots_data: Dictionary containing bot data

    Returns:
        Formatted table string
    """
    if not bots_data or "data" not in bots_data or not bots_data["data"]:
        return "No active bots found."

    # Header
    header = "bot_name | controller | status | realized_pnl | unrealized_pnl | global_pnl | volume | errors | recent_logs"
    separator = format_table_separator()

    # Format each bot as rows
    rows = []
    for bot_name, bot_data in bots_data["data"].items():
        if not isinstance(bot_data, dict):
            continue

        bot_status = get_field(bot_data, "status", default="unknown")
        error_count = len(bot_data.get("error_logs", []))
        log_count = len(bot_data.get("general_logs", []))

        # Get controller performance data
        performance = bot_data.get("performance", {})

        if not performance:
            # Bot with no controllers
            row = (
                f"{bot_name[:20]} | "
                f"N/A | "
                f"{bot_status} | "
                f"N/A | N/A | N/A | N/A | "
                f"{error_count} | "
                f"{log_count}"
            )
            rows.append(row)
        else:
            # Bot with controllers
            for controller_name, controller_data in performance.items():
                ctrl_status = get_field(controller_data, "status", default="unknown")
                ctrl_perf = controller_data.get("performance", {})

                realized_pnl = format_number(get_field(ctrl_perf, "realized_pnl_quote", default=None), compact=False)
                unrealized_pnl = format_number(get_field(ctrl_perf, "unrealized_pnl_quote", default=None), compact=False)
                global_pnl = format_number(get_field(ctrl_perf, "global_pnl_quote", default=None), compact=False)
                global_pnl_pct = format_percentage(get_field(ctrl_perf, "global_pnl_pct", default=None))
                volume = format_number(get_field(ctrl_perf, "volume_traded", default=None), compact=False)

                row = (
                    f"{bot_name[:20]} | "
                    f"{controller_name[:20]} | "
                    f"{ctrl_status} | "
                    f"{realized_pnl} | "
                    f"{unrealized_pnl} | "
                    f"{global_pnl} ({global_pnl_pct}) | "
                    f"{volume} | "
                    f"{error_count} | "
                    f"{log_count}"
                )
                rows.append(row)

    return f"{header}\n{separator}\n" + "\n".join(rows)
