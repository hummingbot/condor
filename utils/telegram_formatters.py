"""
Telegram message formatters for Hummingbot data
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import re


def format_uptime(deployed_at: str) -> str:
    """
    Format time elapsed since deployment as a compact uptime string.

    Args:
        deployed_at: ISO format datetime string (e.g., "2025-12-24T22:22:50.879680+00:00")

    Returns:
        Formatted uptime string (e.g., "2h 15m", "1d 5h", "3d")
    """
    try:
        # Parse the deployed_at timestamp
        if deployed_at.endswith('Z'):
            deployed_at = deployed_at[:-1] + '+00:00'
        deploy_time = datetime.fromisoformat(deployed_at)

        # Get current time in UTC
        now = datetime.now(timezone.utc)

        # Calculate the difference
        delta = now - deploy_time

        total_seconds = int(delta.total_seconds())
        if total_seconds < 0:
            return "0m"

        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60

        if days > 0:
            if hours > 0:
                return f"{days}d {hours}h"
            return f"{days}d"
        elif hours > 0:
            if minutes > 0:
                return f"{hours}h {minutes}m"
            return f"{hours}h"
        else:
            return f"{minutes}m"
    except Exception:
        return ""


def escape_markdown_v2(text: str) -> str:
    """
    Escape special characters for Telegram MarkdownV2

    Characters that need escaping: _ * [ ] ( ) ~ ` > # + - = | { } . !
    """
    special_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(special_chars)}])', r'\\\1', str(text))


def format_header_with_server(
    title: str,
    server_name: Optional[str] = None,
    server_status: Optional[str] = None,
    max_width: int = 40
) -> str:
    """
    Format a header with title on the left and server info on the right

    Args:
        title: Main title text (e.g., "💼 Portfolio Details")
        server_name: Name of the server (e.g., "remote")
        server_status: Status of server - "online", "offline", "auth_error", "error"
        max_width: Maximum character width for alignment (default: 40 for Telegram mobile)

    Returns:
        Formatted header string with proper alignment

    Example:
        "💼 Portfolio Details\nServer: remote 🟢"
    """
    if not server_name:
        return title

    # Determine status emoji
    status_emoji = "🟢"  # Default to online
    if server_status == "offline":
        status_emoji = "🔴"
    elif server_status == "auth_error":
        status_emoji = "🟠"
    elif server_status == "error":
        status_emoji = "⚠️"

    # Build server text - now showing "Server: name emoji"
    server_text = f"Server: {server_name} {status_emoji}                                    "

    # Return title and server on same line, separated by spaces
    # Calculate spacing for alignment
    title_emojis = len([c for c in title if ord(c) > 0x1F300])
    title_display_len = len(title) + title_emojis

    server_emojis = len([c for c in server_text if ord(c) > 0x1F300])
    server_display_len = len(server_text) + server_emojis

    spaces_needed = max_width - title_display_len - server_display_len

    if spaces_needed < 1:
        spaces_needed = 1

    spaces = " " * spaces_needed

    return f"{title}{spaces}{server_text}"


def format_number(value: float, decimals: int = 2) -> str:
    """Format a number with commas and specified decimals"""
    if value >= 1000000:
        return f"${value/1000000:.{decimals}f}M"
    elif value >= 1000:
        return f"${value/1000:.{decimals}f}K"
    else:
        return f"${value:.{decimals}f}"


def format_amount(value: float, decimals: int = 4) -> str:
    """
    Format token amounts with appropriate precision
    Uses scientific notation for very small numbers, otherwise uses fixed decimals
    """
    if value == 0:
        return "0"
    elif abs(value) < 0.0001:
        # Use scientific notation for very small numbers
        return f"{value:.2e}"
    elif abs(value) < 1:
        # Show more decimals for small amounts
        return f"{value:.6f}".rstrip('0').rstrip('.')
    else:
        # Use specified decimals for normal amounts
        formatted = f"{value:.{decimals}f}".rstrip('0').rstrip('.')
        return formatted if '.' in f"{value:.{decimals}f}" else f"{value:.0f}"


def format_price(value: float) -> str:
    """
    Format token price with appropriate precision based on magnitude.
    Shows more decimals for lower prices, fewer for higher prices.
    """
    if value == 0:
        return "$0.00"
    elif value >= 1000:
        return f"${value:,.0f}"
    elif value >= 100:
        return f"${value:.1f}"
    elif value >= 1:
        return f"${value:.2f}"
    elif value >= 0.01:
        return f"${value:.4f}"
    elif value >= 0.0001:
        return f"${value:.6f}"
    else:
        return f"${value:.2e}"


def format_portfolio_summary(summary: Dict[str, Any]) -> str:
    """
    Format portfolio summary for Telegram MarkdownV2

    Args:
        summary: Portfolio summary from client.portfolio.get_portfolio_summary()

    Returns:
        Formatted Telegram message
    """
    total_value = summary.get("total_value", 0)
    token_count = summary.get("token_count", 0)
    account_count = summary.get("account_count", 0)
    top_tokens = summary.get("top_tokens", [])

    # Header
    message = "📊 *Portfolio Summary*\n\n"

    # Total value
    message += f"💰 *Total Value:* {escape_markdown_v2(format_number(total_value))}\n"
    message += f"🔢 *Tokens:* {escape_markdown_v2(str(token_count))}\n"
    message += f"👤 *Accounts:* {escape_markdown_v2(str(account_count))}\n\n"

    # Top holdings
    if top_tokens:
        message += "*🏆 Top Holdings:*\n"
        for i, token_data in enumerate(top_tokens[:5], 1):
            token = token_data.get("token", "Unknown")
            value = token_data.get("value", 0)
            percentage = token_data.get("percentage", 0)

            message += f"{escape_markdown_v2(f'{i}. {token}')}: "
            message += f"{escape_markdown_v2(format_number(value))} "
            message += f"_{escape_markdown_v2(f'({percentage:.1f}%)')}_\n"

    return message


def format_portfolio_state(
    state: Dict[str, Any],
    server_name: Optional[str] = None,
    server_status: Optional[str] = None
) -> str:
    """
    Format detailed portfolio state for Telegram with table format

    Args:
        state: Portfolio state from client.portfolio.get_state()
        server_name: Name of the server/exchange being queried
        server_status: Status of the server ("online", "offline", "auth_error", "error")

    Returns:
        Formatted Telegram message
    """
    # Build header with server info on same line
    if server_name:
        status_emoji = "🟢"
        if server_status == "offline":
            status_emoji = "🔴"
        elif server_status == "auth_error":
            status_emoji = "🟠"
        elif server_status == "error":
            status_emoji = "⚠️"

        message = f"💼 *Portfolio Details* \\| _Server: {escape_markdown_v2(server_name)} {status_emoji}_\n\n"
    else:
        message = "💼 *Portfolio Details*\n\n"

    total_value = 0.0
    all_balances = []

    # Collect all balances with metadata
    for account_name, account_data in state.items():
        for connector_name, balances in account_data.items():
            if balances:
                for balance in balances:
                    token = balance.get("token", "???")
                    units = balance.get("units", 0)
                    value = balance.get("value", 0)

                    if value > 1:  # Only show balances > $1
                        all_balances.append({
                            "account": account_name,
                            "connector": connector_name,
                            "token": token,
                            "units": units,
                            "value": value
                        })
                        total_value += value

    # Calculate percentages
    for balance in all_balances:
        balance["percentage"] = (balance["value"] / total_value * 100) if total_value > 0 else 0

    # Group by account and connector first, then sort within each group
    from collections import defaultdict
    grouped = defaultdict(lambda: defaultdict(list))

    for balance in all_balances:
        account = balance["account"]
        connector = balance["connector"]
        grouped[account][connector].append(balance)

    # Sort each group by value
    for account in grouped:
        for connector in grouped[account]:
            grouped[account][connector].sort(key=lambda x: x["value"], reverse=True)

    # Build the message by iterating through accounts and connectors
    for account, connectors in grouped.items():
        message += f"*Account:* {escape_markdown_v2(account)}\n"

        for connector, balances in connectors.items():
            # Calculate total value for this connector
            connector_total = sum(balance["value"] for balance in balances)
            connector_total_str = format_number(connector_total)

            message += f"  🏦 *{escape_markdown_v2(connector)}* \\- `{escape_markdown_v2(connector_total_str)}`\n\n"

            # Start table - show Token, Price, Value, %
            table_content = "```\n"
            table_content += f"{'Token':<10} {'Price':<12} {'Value':<12} {'%':>6}\n"
            table_content += f"{'─'*10} {'─'*12} {'─'*12} {'─'*6}\n"

            for balance in balances:
                token = balance["token"]
                units = balance["units"]
                value = balance["value"]
                percentage = balance["percentage"]

                # Calculate price per token
                price = value / units if units > 0 else 0
                price_str = format_price(price)
                value_str = format_number(value).replace('$', '')

                # Truncate long token names
                token_display = token[:9] if len(token) > 9 else token

                # Add row to table
                table_content += f"{token_display:<10} {price_str:<12} {value_str:<12} {percentage:>5.1f}%\n"

            # Close table
            table_content += "```\n\n"
            message += table_content

    # Show total
    if total_value > 0:
        message += f"💵 *Total Portfolio Value:* `{escape_markdown_v2(format_number(total_value))}`\n"
    else:
        message += f"💵 *Total Portfolio Value:* `{escape_markdown_v2('$0.00')}`\n"

    return message


def _shorten_controller_for_table(name: str, max_len: int = 28) -> str:
    """Shorten controller name for table display

    Example: gs_binance_SOL-USDT_1252
    Result:  binance_SOL-USDT_1252

    Example: grid_strike_binance_perpetual_SOL-FDUSD_long_0.0001_0.0002_1
    Result:  binance_SOL-FDUSD_L_1
    """
    if len(name) <= max_len:
        return name

    parts = name.split("_")
    connector = ""
    pair = ""
    side = ""
    seq_num = ""

    for p in parts:
        p_lower = p.lower()
        p_upper = p.upper()
        if p_upper in ("LONG", "SHORT"):
            side = "L" if p_upper == "LONG" else "S"
        elif "-" in p:
            pair = p.upper()  # SOL-FDUSD
        elif p_lower in ("binance", "hyperliquid", "kucoin", "okx", "bybit", "gate", "mexc"):
            connector = p_lower[:7]  # max 7 chars
        elif p.isdigit() and len(p) <= 5:
            # Capture sequence number (last numeric part)
            seq_num = p

    if pair:
        if connector and side and seq_num:
            short = f"{connector}_{pair}_{side}_{seq_num}"
        elif connector and seq_num:
            short = f"{connector}_{pair}_{seq_num}"
        elif connector and side:
            short = f"{connector}_{pair}_{side}"
        elif connector:
            short = f"{connector}_{pair}"
        elif side:
            short = f"{pair}_{side}"
        else:
            short = pair

        if len(short) <= max_len:
            return short
        # Truncate pair if needed
        return short[:max_len-1] + "."

    return name[:max_len-1] + "."


def format_active_bots(
    bots_data: Dict[str, Any],
    server_name: Optional[str] = None,
    server_status: Optional[str] = None,
    bot_runs: Optional[Dict[str, str]] = None
) -> str:
    """
    Format active bots status for Telegram with clean table layout.

    Args:
        bots_data: Active bots data from client.bot_orchestration.get_active_bots_status()
        server_name: Name of the server (optional)
        server_status: Status of the server (optional)
        bot_runs: Dict mapping bot_name -> deployed_at ISO timestamp (optional)

    Returns:
        Formatted Telegram message
    """
    message = "🤖 *Active Bots*\n\n"
    bot_runs = bot_runs or {}

    # Handle different response formats
    # New format: {"status": "success", "data": {"bot_name": {...}}}
    if isinstance(bots_data, dict):
        if "data" in bots_data and isinstance(bots_data["data"], dict):
            # New nested format - data is a dict of bot_name -> bot_info
            bots_dict = bots_data["data"]
        elif "data" in bots_data and isinstance(bots_data["data"], list):
            # Old format - data is a list
            bots_dict = {str(i): bot for i, bot in enumerate(bots_data["data"])}
        else:
            bots_dict = bots_data
    elif isinstance(bots_data, list):
        bots_dict = {str(i): bot for i, bot in enumerate(bots_data)}
    else:
        bots_dict = {}

    if not bots_dict:
        message += "_No active bots found_\n"
        return message

    for bot_name, bot_info in bots_dict.items():
        # Handle string bot_info (just name)
        if isinstance(bot_info, str):
            message += f"🟢 *{escape_markdown_v2(bot_info)}*\n\n"
            continue

        # Full dict format
        status = bot_info.get("status", "unknown")
        is_running = status == "running"
        status_emoji = "🟢" if is_running else "🔴"

        # Truncate long bot names for display
        display_name = bot_name[:45] + "..." if len(bot_name) > 45 else bot_name

        # Add uptime if available
        uptime_str = ""
        if bot_name in bot_runs:
            uptime = format_uptime(bot_runs[bot_name])
            if uptime:
                uptime_str = f" ⏱️ {uptime}"

        message += f"{status_emoji} `{escape_markdown_v2(display_name)}`{uptime_str}\n"

        # Performance is a dict of controller_name -> controller_info
        performance = bot_info.get("performance", {})

        if performance:
            total_pnl = 0
            total_volume = 0

            # Create table for controllers
            message += "```\n"
            message += f"{'Controller':<28} {'PnL':>8} {'Vol':>7}\n"
            message += f"{'─'*28} {'─'*8} {'─'*7}\n"

            for idx, (ctrl_name, ctrl_info) in enumerate(list(performance.items())):
                if isinstance(ctrl_info, dict):
                    ctrl_status = ctrl_info.get("status", "running")
                    ctrl_perf = ctrl_info.get("performance", {})
                    realized = ctrl_perf.get("realized_pnl_quote", 0) or 0
                    unrealized = ctrl_perf.get("unrealized_pnl_quote", 0) or 0
                    volume = ctrl_perf.get("volume_traded", 0) or 0
                    pnl = realized + unrealized

                    total_pnl += pnl
                    total_volume += volume

                    # Status prefix + full controller name
                    status_prefix = "▶" if ctrl_status == "running" else "⏸"
                    ctrl_display = f"{status_prefix}{ctrl_name}"[:27]

                    # Format PnL and volume compactly
                    pnl_str = f"{pnl:+.2f}"[:8]
                    if volume >= 1000000:
                        vol_str = f"{volume/1000000:.1f}M"
                    elif volume >= 1000:
                        vol_str = f"{volume/1000:.1f}k"
                    else:
                        vol_str = f"{volume:.0f}"
                    vol_str = vol_str[:7]

                    message += f"{ctrl_display:<28} {pnl_str:>8} {vol_str:>7}\n"

            # Show totals row
            if len(performance) >= 1:
                message += f"{'─'*28} {'─'*8} {'─'*7}\n"
                pnl_total_str = f"{total_pnl:+.2f}"[:8]
                if total_volume >= 1000000:
                    vol_total = f"{total_volume/1000000:.1f}M"
                elif total_volume >= 1000:
                    vol_total = f"{total_volume/1000:.1f}k"
                else:
                    vol_total = f"{total_volume:.0f}"
                vol_total = vol_total[:7]
                message += f"{'TOTAL':<28} {pnl_total_str:>8} {vol_total:>7}\n"

            message += "```\n"

        # Show error indicator if there are errors
        error_logs = bot_info.get("error_logs", [])
        if error_logs:
            message += f"⚠️ _{len(error_logs)} error\\(s\\)_\n"

        message += "\n"

    message += f"_{len(bots_dict)} bot\\(s\\) running_ • _Tap for details_"

    # Add server footer at the bottom right
    if server_name:
        status_emoji = "🟢"
        if server_status == "offline":
            status_emoji = "🔴"
        elif server_status == "auth_error":
            status_emoji = "🟠"
        elif server_status == "error":
            status_emoji = "⚠️"

        message += f"\n\n_Server: {escape_markdown_v2(server_name)} {status_emoji}_"

    return message


def format_bot_status(
    bot_status: Dict[str, Any],
    server_name: Optional[str] = None,
    server_status: Optional[str] = None
) -> str:
    """
    Format individual bot status for Telegram

    Args:
        bot_status: Bot status from client.bot_orchestration.get_bot_status()
        server_name: Name of the server (optional)
        server_status: Status of the server (optional)

    Returns:
        Formatted Telegram message
    """
    if bot_status.get("status") != "success":
        return format_error_message(
            bot_status.get('message', 'Unknown error'),
            server_name=server_name,
            server_status=server_status
        )

    data = bot_status.get("data", {})
    bot_name = data.get("name", "Unknown")
    is_running = data.get("is_running", False)

    # Format header
    bot_status_emoji = "🟢" if is_running else "🔴"
    message = f"{bot_status_emoji} *Bot:* {escape_markdown_v2(bot_name)}\n\n"

    # Performance
    performance = data.get("performance", {})
    if performance:
        message += "*📊 Performance:*\n"
        message += f"  Realized PnL: {escape_markdown_v2(format_number(performance.get('realized_pnl_quote', 0)))}\n"
        message += f"  Unrealized PnL: {escape_markdown_v2(format_number(performance.get('unrealized_pnl_quote', 0)))}\n"
        message += f"  Volume: {escape_markdown_v2(format_number(performance.get('volume_traded', 0)))}\n\n"

    # Controllers
    controllers = data.get("controllers", [])
    if controllers:
        message += f"*⚙️ Controllers \\({escape_markdown_v2(str(len(controllers)))}\\):*\n"
        for ctrl in controllers:
            ctrl_name = ctrl.get("controller_name", "Unknown")
            ctrl_type = ctrl.get("controller_type", "unknown")
            message += f"  • {escape_markdown_v2(ctrl_name)} _{escape_markdown_v2(f'({ctrl_type})')}_\n"

    # Add server footer at the bottom right
    if server_name:
        status_emoji = "🟢"
        if server_status == "offline":
            status_emoji = "🔴"
        elif server_status == "auth_error":
            status_emoji = "🟠"
        elif server_status == "error":
            status_emoji = "⚠️"

        message += f"\n{'⎯' * 30}\n"
        message += f"{' ' * 15}_Server: {escape_markdown_v2(server_name)} {status_emoji}_"

    return message


def format_error_message(
    error: str,
    server_name: Optional[str] = None,
    server_status: Optional[str] = None
) -> str:
    """
    Format error message for Telegram

    Args:
        error: Error message
        server_name: Name of the server (optional)
        server_status: Status of the server (optional)

    Returns:
        Formatted error message
    """
    msg = f"❌ *Error*\n\n{escape_markdown_v2(error)}"

    # Add server footer at the bottom right
    if server_name:
        status_emoji = "🟢"
        if server_status == "offline":
            status_emoji = "🔴"
        elif server_status == "auth_error":
            status_emoji = "🟠"
        elif server_status == "error":
            status_emoji = "⚠️"

        separator = '⎯' * 15
        msg += f"\n\n{separator} _Server: {escape_markdown_v2(server_name)} {status_emoji}_"

    return msg


def format_success_message(
    message: str,
    server_name: Optional[str] = None,
    server_status: Optional[str] = None
) -> str:
    """
    Format success message for Telegram

    Args:
        message: Success message
        server_name: Name of the server (optional)
        server_status: Status of the server (optional)

    Returns:
        Formatted success message
    """
    msg = f"✅ *Success*\n\n{escape_markdown_v2(message)}"

    # Add server footer at the bottom right
    if server_name:
        status_emoji = "🟢"
        if server_status == "offline":
            status_emoji = "🔴"
        elif server_status == "auth_error":
            status_emoji = "🟠"
        elif server_status == "error":
            status_emoji = "⚠️"

        separator = '⎯' * 15
        msg += f"\n\n{separator} _Server: {escape_markdown_v2(server_name)} {status_emoji}_"

    return msg

def format_perpetual_positions(positions_data: Dict[str, Any]) -> str:
    """
    Format perpetual positions for Telegram display

    Args:
        positions_data: Dictionary with 'positions' list and 'total' count

    Returns:
        Formatted string section for perpetual positions
    """
    positions = positions_data.get('positions', [])
    total = positions_data.get('total', 0)

    if not positions or total == 0:
        return "📊 *Perpetual Positions*\n_No open positions_\n"

    message = f"📊 *Perpetual Positions* \\({escape_markdown_v2(str(total))}\\)\n\n"

    # Group by account
    from collections import defaultdict
    by_account = defaultdict(list)

    for pos in positions:
        account_name = pos.get('account_name', 'Unknown')
        by_account[account_name].append(pos)

    # Format each account's positions
    for account_name, account_positions in by_account.items():
        message += f"*Account:* {escape_markdown_v2(account_name)}\n"

        # Create table - optimized for mobile width with minimal spacing
        # Columns: Connector(10) Pair(10) Side(4) Value(7) PnL$(7)
        table_content = "```\n"
        table_content += f"{'Connector':<10} {'Pair':<10} {'Side':<4} {'Value':<7} {'PnL($)':>7}\n"
        table_content += f"{'─'*10} {'─'*10} {'─'*4} {'─'*7} {'─'*7}\n"

        for pos in account_positions:
            connector = pos.get('connector_name', 'N/A')
            pair = pos.get('trading_pair', 'N/A')
            # Try multiple field names for side (same as clob_trading)
            side = pos.get('position_side') or pos.get('side') or pos.get('trade_type', 'N/A')
            amount = pos.get('amount', 0)
            entry_price = pos.get('entry_price', 0)
            unrealized_pnl = pos.get('unrealized_pnl', 0)

            # Truncate connector name if too long
            connector_display = connector[:9] if len(connector) > 9 else connector

            # Truncate pair name
            pair_display = pair[:9] if len(pair) > 9 else pair

            # Truncate side - use short form
            side_upper = side.upper() if side else 'N/A'
            if side_upper in ('LONG', 'BUY'):
                side_display = 'LONG'
            elif side_upper in ('SHORT', 'SELL'):
                side_display = 'SHRT'
            else:
                side_display = side[:4] if len(side) > 4 else side

            # Calculate position value (Size * Entry Price)
            try:
                position_value = float(amount) * float(entry_price)
                value_str = format_number(position_value).replace('$', '')[:6]
            except (ValueError, TypeError):
                value_str = "N/A"

            try:
                pnl_float = float(unrealized_pnl)
                if pnl_float >= 0:
                    pnl_str = f"+{pnl_float:.2f}"[:7]
                else:
                    pnl_str = f"{pnl_float:.2f}"[:7]
            except (ValueError, TypeError):
                pnl_str = str(unrealized_pnl)[:7]

            table_content += f"{connector_display:<10} {pair_display:<10} {side_display:<4} {value_str:<7} {pnl_str:>7}\n"

        table_content += "```\n\n"
        message += table_content

    return message


# Well-known token addresses for resolution
KNOWN_TOKENS = {
    # Solana Native
    "So11111111111111111111111111111111111111112": "SOL",
    # Stablecoins
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
    # LSTs
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": "mSOL",
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": "jitoSOL",
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1": "bSOL",
    # Major tokens
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "ETH",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONK",
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": "JUP",
    "orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE": "ORCA",
    "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3": "PYTH",
    "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof": "RNDR",
    # Mining/DeFi
    "oreoU2P8bN6jkk3jbaiVxYnG1dCXcYxwhwyK9jSybcp": "ORE",
    # Meteora
    "METvsvVRapdj9cFLzq4Tr43xK4tAjQfwX76z3n6mWQL": "MET",
    # Add more as needed
}

# Reverse lookup: symbol -> address
KNOWN_TOKEN_ADDRESSES = {symbol: address for address, symbol in KNOWN_TOKENS.items()}


def resolve_token_address(symbol: str, token_cache: Optional[Dict[str, str]] = None) -> Optional[str]:
    """
    Resolve a token symbol to its address.

    Args:
        symbol: Token symbol to resolve (e.g., "SOL", "USDC")
        token_cache: Optional cache from Gateway tokens {address: symbol}

    Returns:
        Token address or None if not found
    """
    if not symbol:
        return None

    symbol_upper = symbol.upper()

    # Check known tokens first
    if symbol_upper in KNOWN_TOKEN_ADDRESSES:
        return KNOWN_TOKEN_ADDRESSES[symbol_upper]

    # Check provided cache (reverse lookup)
    if token_cache:
        for address, sym in token_cache.items():
            if sym.upper() == symbol_upper:
                return address

    return None


def resolve_token_symbol(address: str, token_cache: Optional[Dict[str, str]] = None) -> str:
    """
    Resolve a token address to its symbol.

    Args:
        address: Token address to resolve
        token_cache: Optional cache from Gateway tokens {address: symbol}

    Returns:
        Token symbol or shortened address
    """
    if not address:
        return "???"

    # Check provided cache first
    if token_cache and address in token_cache:
        return token_cache[address]

    # Check known tokens
    if address in KNOWN_TOKENS:
        return KNOWN_TOKENS[address]

    # Shorten address: first 4 chars
    return address[:4] if len(address) > 4 else address


def _resolve_token_symbol(address: str) -> str:
    """Resolve a token address to its symbol (legacy wrapper)."""
    return resolve_token_symbol(address, None)


def _get_chain_from_network(network: str) -> str:
    """Extract chain name from network string (e.g., 'solana-mainnet-beta' -> 'solana')."""
    if not network:
        return "?"
    network = network.lower()
    if network.startswith("solana"):
        return "solana"
    elif network.startswith("ethereum") or network.startswith("eth"):
        return "ethereum"
    elif network.startswith("polygon"):
        return "polygon"
    elif network.startswith("arbitrum"):
        return "arbitrum"
    elif network.startswith("base"):
        return "base"
    # Fallback: first part before dash
    if '-' in network:
        return network.split('-')[0][:8]
    return network[:8]


def _looks_like_address(s: str) -> bool:
    """Check if a string looks like a blockchain address (long alphanumeric)."""
    if not s:
        return False
    # Addresses are typically 32+ chars and alphanumeric
    return len(s) > 20 and s.replace('1', '').replace('2', '').isalnum()


def _format_pnl_value(value: float) -> str:
    """Format PNL value with sign and compact notation."""
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    if abs(value) >= 1000:
        return f"{sign}{value/1000:.1f}k"
    elif abs(value) >= 1:
        return f"{sign}{value:.2f}"
    elif abs(value) >= 0.01:
        return f"{sign}{value:.3f}"
    else:
        return f"{sign}{value:.4f}"


def format_lp_positions(positions_data: Dict[str, Any], token_cache: Optional[Dict[str, str]] = None) -> str:
    """
    Format LP (CLMM) positions for Telegram display - compact summary with value and PNL.

    Only shows active positions with their value and PNL in a scannable format.

    Args:
        positions_data: Dictionary with 'positions' list and 'total' count
        token_cache: Optional {address: symbol} mapping for token resolution

    Returns:
        Formatted string section for LP positions
    """
    positions = positions_data.get('positions', [])
    total = positions_data.get('total', 0)
    token_cache = token_cache or {}

    if not positions or total == 0:
        return ""  # Don't show section if no positions

    # Calculate totals and filter active positions
    total_value_usd = 0.0
    total_pnl_usd = 0.0
    active_count = 0
    out_of_range_count = 0

    for pos in positions:
        in_range = pos.get('in_range', '')
        if in_range == 'IN_RANGE':
            active_count += 1
        elif in_range == 'OUT_OF_RANGE':
            out_of_range_count += 1

        # Get value from pnl_summary (in quote token)
        pnl_summary = pos.get('pnl_summary', {})
        current_value = pnl_summary.get('current_lp_value_quote', 0)
        total_pnl = pnl_summary.get('total_pnl_quote', 0)

        try:
            # For now, assume quote token is a stablecoin (value ~= $1)
            # A more accurate approach would use token_prices
            value_f = float(current_value) if current_value else 0
            pnl_f = float(total_pnl) if total_pnl else 0
            total_value_usd += value_f
            total_pnl_usd += pnl_f
        except (ValueError, TypeError):
            pass

    # Build compact message
    message = f"🏊 *LP Positions* \\({escape_markdown_v2(str(total))}\\)\n"

    # Summary line: 3 active 🟢 | 1 out 🔴 | Value: $1,234 | PnL: +$56
    parts = []
    if active_count > 0:
        parts.append(f"{active_count} 🟢")
    if out_of_range_count > 0:
        parts.append(f"{out_of_range_count} 🔴")

    if total_value_usd > 0:
        value_str = format_number(total_value_usd)
        parts.append(f"Value: {value_str}")

    if total_pnl_usd != 0:
        pnl_str = format_number(abs(total_pnl_usd))
        if total_pnl_usd >= 0:
            parts.append(f"PnL: +{pnl_str}")
        else:
            parts.append(f"PnL: -{pnl_str}")

    if parts:
        summary = " \\| ".join([escape_markdown_v2(p) for p in parts])
        message += summary
    else:
        message += "_No value data available_"

    message += "\n_Use /lp for details_\n\n"
    return message


def format_active_orders(orders_data: Dict[str, Any]) -> str:
    """
    Format active orders for Telegram display

    Args:
        orders_data: Dictionary with 'orders' list and 'total' count

    Returns:
        Formatted string section for active orders
    """
    orders = orders_data.get('orders', [])
    total = orders_data.get('total', 0)

    if not orders or total == 0:
        return "📋 *Active Orders*\n_No active orders_\n"

    message = f"📋 *Active Orders* \\({escape_markdown_v2(str(total))}\\)\n\n"

    # Group by account
    from collections import defaultdict
    by_account = defaultdict(list)

    for order in orders:
        account_name = order.get('account_name', 'Unknown')
        by_account[account_name].append(order)

    # Format each account's orders
    for account_name, account_orders in by_account.items():
        message += f"*Account:* {escape_markdown_v2(account_name)}\n"

        # Create table - optimized widths for mobile
        table_content = "```\n"
        table_content += f"{'Exch':<11} {'Pair':<9} {'Side':<5} {'Type':<6} {'Amt':<8} {'Price':<8}\n"
        table_content += f"{'─'*11} {'─'*9} {'─'*5} {'─'*6} {'─'*8} {'─'*8}\n"

        for order in account_orders[:10]:  # Show max 10 orders per account
            connector = order.get('connector_name', 'N/A')[:10]
            pair = order.get('trading_pair', 'N/A')[:8]
            side = order.get('trade_type', 'N/A')[:4]
            order_type = order.get('order_type', 'N/A')[:5]
            amount = order.get('amount', 0)
            price = order.get('price', 0)

            # Format amount
            amount_str = format_amount(amount)[:7]

            # Format price
            try:
                price_float = float(price)
                if price_float >= 1000:
                    price_str = f"{price_float:.0f}"[:7]
                elif price_float >= 1:
                    price_str = f"{price_float:.2f}"[:7]
                else:
                    price_str = f"{price_float:.4f}"[:7]
            except (ValueError, TypeError):
                price_str = str(price)[:7]

            table_content += f"{connector:<11} {pair:<9} {side:<5} {order_type:<6} {amount_str:<8} {price_str:<8}\n"

        if len(account_orders) > 10:
            table_content += f"\n... and {len(account_orders) - 10} more orders\n"

        table_content += "```\n\n"
        message += table_content

    return message


def format_pnl_indicator(value: Optional[float]) -> str:
    """Format a PNL percentage with color indicator"""
    if value is None:
        return "—"
    arrow = "▲" if value >= 0 else "▼"
    return f"{arrow}{abs(value):.1f}%"


def format_change_compact(value: Optional[float]) -> str:
    """Format a percentage change compactly for table columns"""
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f}%"


def format_exchange_distribution(
    accounts_distribution: Dict[str, Any],
    changes_24h: Optional[Dict[str, Any]] = None,
    total_value: float = 0.0
) -> str:
    """
    Format exchange/connector distribution as a compact table.

    Args:
        accounts_distribution: From client.portfolio.get_accounts_distribution()
        changes_24h: 24h change data with connector changes
        total_value: Total portfolio value for percentage calculation

    Returns:
        MarkdownV2 formatted string with exchange distribution table
    """
    if not accounts_distribution:
        return ""

    # Parse distribution data (supports both list and dict formats)
    accounts_list = accounts_distribution.get("distribution", [])
    accounts_dict = accounts_distribution.get("accounts", {})

    # Build connector -> value mapping (aggregated across accounts)
    connector_totals = {}  # {connector: {"value": float, "account": str}}
    connector_changes = changes_24h.get("connectors", {}) if changes_24h else {}

    if accounts_list:
        for account_info in accounts_list:
            account_name = account_info.get("account", account_info.get("name", "Unknown"))
            connectors = account_info.get("connectors", {})

            for connector_name, connector_value in connectors.items():
                if isinstance(connector_value, dict):
                    connector_value = connector_value.get("value", 0)
                if isinstance(connector_value, str):
                    try:
                        connector_value = float(connector_value)
                    except (ValueError, TypeError):
                        connector_value = 0

                key = f"{account_name}:{connector_name}"
                connector_totals[key] = {
                    "value": float(connector_value),
                    "account": account_name,
                    "connector": connector_name
                }

    elif accounts_dict:
        for account_name, account_info in accounts_dict.items():
            connectors = account_info.get("connectors", {})
            for connector_name, connector_info in connectors.items():
                if isinstance(connector_info, dict):
                    value = connector_info.get("value", 0)
                else:
                    value = connector_info

                if isinstance(value, str):
                    try:
                        value = float(value)
                    except (ValueError, TypeError):
                        value = 0

                key = f"{account_name}:{connector_name}"
                connector_totals[key] = {
                    "value": float(value),
                    "account": account_name,
                    "connector": connector_name
                }

    if not connector_totals:
        return ""

    # Sort by value descending
    sorted_connectors = sorted(connector_totals.items(), key=lambda x: x[1]["value"], reverse=True)

    # Build table
    message = "*Exchanges:*\n"
    message += "```\n"
    message += f"{'Exchange':<20} {'Value':<10} {'%':>6} {'24h':>8}\n"
    message += f"{'─'*20} {'─'*10} {'─'*6} {'─'*8}\n"

    for key, data in sorted_connectors:
        connector = data["connector"]
        account = data["account"]
        value = data["value"]

        if value < 1:  # Skip tiny values
            continue

        # Show full connector name (up to 19 chars)
        display_name = connector[:19] if len(connector) > 19 else connector

        # Format value
        value_str = format_number(value)

        # Calculate percentage
        pct = (value / total_value * 100) if total_value > 0 else 0
        pct_str = f"{pct:.1f}%" if pct < 100 else f"{pct:.0f}%"

        # Get 24h change
        conn_change = connector_changes.get(account, {}).get(connector, {})
        conn_pct = conn_change.get("pct_change")
        if conn_pct is not None:
            change_str = format_change_compact(conn_pct)
        else:
            change_str = "—"

        message += f"{display_name:<20} {value_str:<10} {pct_str:>6} {change_str:>8}\n"

    message += "```\n\n"
    return message


def format_aggregated_tokens(
    balances: Dict[str, Any],
    changes_24h: Optional[Dict[str, Any]] = None,
    total_value: float = 0.0,
    max_tokens: int = 10
) -> str:
    """
    Format aggregated token holdings across all exchanges.

    Args:
        balances: Portfolio state from get_state() {account: {connector: [holdings]}}
        changes_24h: 24h change data with token price changes
        total_value: Total portfolio value for percentage calculation
        max_tokens: Maximum number of tokens to display

    Returns:
        MarkdownV2 formatted string with token holdings table
    """
    if not balances:
        return ""

    # Aggregate tokens across all accounts/connectors
    token_totals = {}  # {token: {"units": float, "value": float}}

    for account_name, account_data in balances.items():
        for connector_name, connector_balances in account_data.items():
            if not connector_balances:
                continue
            for balance in connector_balances:
                token = balance.get("token", "???")
                units = balance.get("units", 0)
                value = balance.get("value", 0)

                if isinstance(units, str):
                    try:
                        units = float(units)
                    except (ValueError, TypeError):
                        units = 0
                if isinstance(value, str):
                    try:
                        value = float(value)
                    except (ValueError, TypeError):
                        value = 0

                if token not in token_totals:
                    token_totals[token] = {"units": 0.0, "value": 0.0}

                token_totals[token]["units"] += float(units)
                token_totals[token]["value"] += float(value)

    if not token_totals:
        return ""

    # Sort by value descending
    sorted_tokens = sorted(token_totals.items(), key=lambda x: x[1]["value"], reverse=True)

    # Filter out tiny values
    sorted_tokens = [(t, d) for t, d in sorted_tokens if d["value"] >= 1]

    if not sorted_tokens:
        return ""

    # Get 24h changes
    token_changes = changes_24h.get("tokens", {}) if changes_24h else {}

    # Build table
    message = "*Token Holdings:*\n"
    message += "```\n"
    message += f"{'Token':<6} {'Price':<9} {'Value':<8} {'%':>5} {'24h':>7}\n"
    message += f"{'─'*6} {'─'*9} {'─'*8} {'─'*5} {'─'*7}\n"

    for token, data in sorted_tokens[:max_tokens]:
        units = data["units"]
        value = data["value"]

        # Truncate token name
        token_display = token[:5] if len(token) > 5 else token

        # Calculate price
        price = value / units if units > 0 else 0
        price_str = format_price(price)[:9]

        # Format value
        value_str = format_number(value)[:8]

        # Calculate percentage
        pct = (value / total_value * 100) if total_value > 0 else 0
        pct_str = f"{pct:.1f}%" if pct < 100 else f"{pct:.0f}%"

        # Get 24h price change
        token_change = token_changes.get(token, {})
        price_change = token_change.get("price_change")
        if price_change is not None:
            change_str = format_change_compact(price_change)[:7]
        else:
            change_str = "—"

        message += f"{token_display:<6} {price_str:<9} {value_str:<8} {pct_str:>5} {change_str:>7}\n"

    # Show count if more tokens exist
    if len(sorted_tokens) > max_tokens:
        message += f"\n... +{len(sorted_tokens) - max_tokens} more tokens\n"

    message += "```\n\n"
    return message


def format_connector_detail(
    balances: Dict[str, Any],
    connector_key: str,
    changes_24h: Optional[Dict[str, Any]] = None,
    total_value: float = 0.0
) -> str:
    """
    Format detailed token holdings for a specific connector.

    Args:
        balances: Portfolio state from get_state()
        connector_key: "account:connector" identifier
        changes_24h: 24h change data
        total_value: Total portfolio value for percentage calculation

    Returns:
        MarkdownV2 formatted string with connector-specific token table
    """
    if not balances or not connector_key:
        return "_No data available_"

    # Parse connector key
    parts = connector_key.split(":", 1)
    if len(parts) != 2:
        return "_Invalid connector_"

    account_name, connector_name = parts

    # Get connector balances
    account_data = balances.get(account_name, {})
    connector_balances = account_data.get(connector_name, [])

    if not connector_balances:
        return f"_No holdings found for {escape_markdown_v2(connector_name)}_"

    # Calculate connector total
    connector_total = sum(b.get("value", 0) for b in connector_balances if b.get("value", 0) > 0)

    # Get changes
    token_changes = changes_24h.get("tokens", {}) if changes_24h else {}
    connector_changes = changes_24h.get("connectors", {}) if changes_24h else {}
    conn_change = connector_changes.get(account_name, {}).get(connector_name, {})
    conn_pct = conn_change.get("pct_change")

    # Build header
    message = f"🏦 *{escape_markdown_v2(connector_name)}* "
    message += f"\\| `{escape_markdown_v2(format_number(connector_total))}`"
    if conn_pct is not None:
        message += f" \\({escape_markdown_v2(format_change_compact(conn_pct))}\\)"
    message += "\n"
    message += f"_Account: {escape_markdown_v2(account_name)}_\n\n"

    # Sort balances by value
    sorted_balances = sorted(
        [b for b in connector_balances if b.get("value", 0) >= 1],
        key=lambda x: x.get("value", 0),
        reverse=True
    )

    if not sorted_balances:
        message += "_No significant holdings_"
        return message

    # Build table
    message += "```\n"
    message += f"{'Token':<6} {'Price':<9} {'Value':<8} {'%':>5} {'24h':>7}\n"
    message += f"{'─'*6} {'─'*9} {'─'*8} {'─'*5} {'─'*7}\n"

    for balance in sorted_balances:
        token = balance.get("token", "???")
        units = balance.get("units", 0)
        value = balance.get("value", 0)

        # Truncate token name
        token_display = token[:5] if len(token) > 5 else token

        # Calculate price
        price = value / units if units > 0 else 0
        price_str = format_price(price)[:9]

        # Format value
        value_str = format_number(value)[:8]

        # Calculate percentage of portfolio
        pct = (value / total_value * 100) if total_value > 0 else 0
        pct_str = f"{pct:.1f}%" if pct < 100 else f"{pct:.0f}%"

        # Get 24h price change
        token_change = token_changes.get(token, {})
        price_change = token_change.get("price_change")
        if price_change is not None:
            change_str = format_change_compact(price_change)[:7]
        else:
            change_str = "—"

        message += f"{token_display:<6} {price_str:<9} {value_str:<8} {pct_str:>5} {change_str:>7}\n"

    message += "```\n"
    return message


def format_portfolio_overview(
    overview_data: Dict[str, Any],
    server_name: Optional[str] = None,
    server_status: Optional[str] = None,
    pnl_indicators: Optional[Dict[str, Optional[float]]] = None,
    changes_24h: Optional[Dict[str, Any]] = None,
    token_cache: Optional[Dict[str, str]] = None,
    accounts_distribution: Optional[Dict[str, Any]] = None
) -> str:
    """
    Format complete portfolio overview with all sections

    Args:
        overview_data: Dictionary from get_portfolio_overview() containing:
            - balances: Portfolio state
            - perp_positions: Perpetual positions data
            - lp_positions: LP positions data
            - active_orders: Active orders data
        server_name: Name of the server (optional)
        server_status: Status of the server (optional)
        pnl_indicators: Dict with pnl_24h, pnl_7d, pnl_30d percentages (optional)
        changes_24h: Dict with token and connector 24h changes (optional)
        token_cache: Dict mapping token addresses to symbols for LP position resolution (optional)
        accounts_distribution: From get_accounts_distribution() for exchange breakdown (optional)

    Returns:
        Formatted Telegram message with all portfolio sections
    """
    # Build header
    if server_name:
        status_emoji = "🟢"
        if server_status == "offline":
            status_emoji = "🔴"
        elif server_status == "auth_error":
            status_emoji = "🟠"
        elif server_status == "error":
            status_emoji = "⚠️"

        message = f"💼 *Portfolio Details* \\| _Server: {escape_markdown_v2(server_name)} {status_emoji}_\n\n"
    else:
        message = "💼 *Portfolio Details*\n\n"

    # ============================================
    # SECTION 1: TOTAL VALUE AND PNL
    # ============================================
    balances = overview_data.get('balances') if overview_data else None

    # Calculate total portfolio value
    total_value = 0.0
    if balances:
        for account_data in balances.values():
            for connector_balances in account_data.values():
                if connector_balances:
                    for balance in connector_balances:
                        value = balance.get("value", 0)
                        if value > 0:
                            total_value += value

    # Show total value with all PNL indicators on one line
    pnl_24h = pnl_indicators.get("pnl_24h") if pnl_indicators else None
    pnl_7d = pnl_indicators.get("pnl_7d") if pnl_indicators else None
    pnl_30d = pnl_indicators.get("pnl_30d") if pnl_indicators else None
    detected_movements = pnl_indicators.get("detected_movements", []) if pnl_indicators else []

    if total_value > 0:
        total_str = format_number(total_value)
        line = f"💵 *Total:* `{escape_markdown_v2(total_str)}`"
        if pnl_24h is not None:
            line += f" \\({escape_markdown_v2(format_pnl_indicator(pnl_24h))} 24h\\)"
    else:
        line = f"💵 *Total:* `{escape_markdown_v2('$0.00')}`"

    # Add 7d/30d PNL on the same line
    pnl_parts = []
    if pnl_7d is not None:
        pnl_parts.append(f"7d: {escape_markdown_v2(format_pnl_indicator(pnl_7d))}")
    if pnl_30d is not None:
        pnl_parts.append(f"30d: {escape_markdown_v2(format_pnl_indicator(pnl_30d))}")

    if pnl_parts:
        line += " 📈 " + " \\| ".join(pnl_parts)

    message += line + "\n"

    if detected_movements:
        message += f"_\\({len(detected_movements)} movement\\(s\\) adjusted\\)_\n"

    message += "\n"

    # ============================================
    # SECTION 2: EXCHANGE DISTRIBUTION (compact)
    # ============================================
    if accounts_distribution:
        message += format_exchange_distribution(accounts_distribution, changes_24h, total_value)

    # ============================================
    # SECTION 3: AGGREGATED TOKEN HOLDINGS (compact)
    # ============================================
    if balances:
        message += format_aggregated_tokens(balances, changes_24h, total_value, max_tokens=10)

    # ============================================
    # SECTION 4: PERPETUAL POSITIONS
    # ============================================
    perp_positions = overview_data.get('perp_positions', {"positions": [], "total": 0})
    message += format_perpetual_positions(perp_positions)

    # ============================================
    # SECTION 3: LP POSITIONS
    # ============================================
    lp_positions = overview_data.get('lp_positions', {"positions": [], "total": 0})
    message += format_lp_positions(lp_positions, token_cache)

    # ============================================
    # SECTION 4: ACTIVE ORDERS
    # ============================================
    active_orders = overview_data.get('active_orders', {"orders": [], "total": 0})
    message += format_active_orders(active_orders)

    return message


# ============================================
# TRADING FORMATTERS
# ============================================

def format_orders_table(orders: List[Dict[str, Any]]) -> str:
    """
    Format orders as a monospace table for Telegram

    Args:
        orders: List of order dictionaries

    Returns:
        Formatted table string in code block
    """
    if not orders:
        return "No orders found"

    # Build monospace table
    table_content = ""
    table_content += f"{'Pair':<10} {'Side':<4} {'Amt':<8} {'Price':<8} {'Type':<6} {'Status':<7}\n"
    table_content += f"{'─'*10} {'─'*4} {'─'*8} {'─'*8} {'─'*6} {'─'*7}\n"

    for order in orders[:10]:  # Limit to 10 for Telegram
        pair = order.get('trading_pair', 'N/A')
        side = order.get('trade_type', 'N/A')
        amount = order.get('amount', 0)
        price = order.get('price', 0)
        order_type = order.get('order_type', 'N/A')
        status = order.get('status', 'N/A')

        # Truncate long values
        pair_display = pair[:9] if len(pair) > 9 else pair
        side_display = side[:4] if len(side) > 4 else side
        type_display = order_type[:6] if len(order_type) > 6 else order_type
        status_display = status[:7] if len(status) > 7 else status

        # Format amount
        try:
            amount_str = format_amount(float(amount))[:8]
        except (ValueError, TypeError):
            amount_str = str(amount)[:8]

        # Format price
        try:
            price_str = format_amount(float(price))[:8] if price else '-'
        except (ValueError, TypeError):
            price_str = str(price)[:8] if price else '-'

        table_content += f"{pair_display:<10} {side_display:<4} {amount_str:<8} {price_str:<8} {type_display:<6} {status_display:<7}\n"

    if len(orders) > 10:
        table_content += f"\n... and {len(orders) - 10} more orders\n"

    return table_content


def format_positions_table(positions: List[Dict[str, Any]]) -> str:
    """
    Format positions as a monospace table for Telegram

    Args:
        positions: List of position dictionaries

    Returns:
        Formatted table string in code block
    """
    if not positions:
        return "No positions found"

    # Group positions by account
    from collections import defaultdict
    by_account = defaultdict(list)

    for pos in positions:
        account_name = pos.get('account_name', 'master_account')
        by_account[account_name].append(pos)

    table_content = ""

    # Format each account's positions
    for account_name, account_positions in by_account.items():
        table_content += f"Account: {account_name}\n"

        # Create table - same format as format_perpetual_positions
        table_content += f"{'Connector':<10} {'Pair':<10} {'Side':<4} {'Value':<7} {'PnL($)':>7}\n"
        table_content += f"{'─'*10} {'─'*10} {'─'*4} {'─'*7} {'─'*7}\n"

        for pos in account_positions[:10]:  # Limit to 10 for Telegram
            connector = pos.get('connector_name', 'N/A')
            pair = pos.get('trading_pair', 'N/A')
            # Try multiple field names for side
            side = pos.get('position_side') or pos.get('side') or pos.get('trade_type', 'N/A')
            amount = pos.get('amount', 0)
            entry_price = pos.get('entry_price', 0)
            pnl = pos.get('unrealized_pnl', 0)

            # Truncate connector name if too long
            connector_display = connector[:9] if len(connector) > 9 else connector

            # Truncate pair name if too long
            pair_display = pair[:9] if len(pair) > 9 else pair

            # Truncate side - use short form
            side_upper = side.upper() if side else 'N/A'
            if side_upper in ('LONG', 'BUY'):
                side_display = 'LONG'
            elif side_upper in ('SHORT', 'SELL'):
                side_display = 'SHRT'
            else:
                side_display = side[:4] if len(side) > 4 else side

            # Calculate position value (Size * Entry Price)
            try:
                position_value = abs(float(amount) * float(entry_price))
                value_str = format_number(position_value).replace('$', '')[:6]
            except (ValueError, TypeError):
                value_str = "N/A"

            # Format PnL
            try:
                pnl_float = float(pnl)
                if pnl_float >= 0:
                    pnl_str = f"+{pnl_float:.2f}"[:7]
                else:
                    pnl_str = f"{pnl_float:.2f}"[:7]
            except (ValueError, TypeError):
                pnl_str = str(pnl)[:7]

            table_content += f"{connector_display:<10} {pair_display:<10} {side_display:<4} {value_str:<7} {pnl_str:>7}\n"

        if len(account_positions) > 10:
            table_content += f"\n... and {len(account_positions) - 10} more positions\n"

    return table_content
