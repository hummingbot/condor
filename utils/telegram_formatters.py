"""
Telegram message formatters for Hummingbot data
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def format_compact_number(value, decimals: int = 2) -> str:
    """Format number with K/M suffix for readability (no currency prefix).

    Handles large numbers (K/M suffixes), small crypto prices with
    progressively higher precision, and scientific notation for
    extremely small values. Returns "—" for None/invalid input.
    """
    if value is None:
        return "—"
    try:
        num = float(value)
        if num == 0:
            return "0"
        if abs(num) >= 1_000_000:
            return f"{num/1_000_000:.{decimals}f}M"
        if abs(num) >= 1_000:
            return f"{num/1_000:.{decimals}f}K"
        if abs(num) >= 1:
            return f"{num:.{decimals}f}"
        if abs(num) >= 0.01:
            return f"{num:.4f}"
        if abs(num) >= 0.0001:
            return f"{num:.6f}"
        if abs(num) >= 0.000001:
            return f"{num:.8f}"
        if abs(num) >= 0.00000001:
            return f"{num:.10f}"
        # For extremely small numbers, use scientific notation
        return f"{num:.2e}"
    except (ValueError, TypeError):
        return "—"


def format_network_display(network_id: str) -> str:
    """Format network ID for button display.

    Examples:
        solana-mainnet-beta -> Solana
        ethereum-mainnet -> Ethereum
        solana-devnet -> Solana Dev
    """
    if not network_id:
        return "Network"

    parts = network_id.split("-")
    chain = parts[0].capitalize()

    if len(parts) > 1:
        net = parts[1]
        if net in ("mainnet", "mainnet-beta"):
            return chain
        elif net == "devnet":
            return f"{chain} Dev"
        elif net == "testnet":
            return f"{chain} Test"
        else:
            return f"{chain} {net[:4]}"

    return chain


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
        if deployed_at.endswith("Z"):
            deployed_at = deployed_at[:-1] + "+00:00"
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
    special_chars = r"_*[]()~`>#+-=|{}.!"
    return re.sub(f"([{re.escape(special_chars)}])", r"\\\1", str(text))


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
        return f"{value:.6f}".rstrip("0").rstrip(".")
    else:
        # Use specified decimals for normal amounts
        formatted = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
        return formatted if "." in f"{value:.{decimals}f}" else f"{value:.0f}"


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


def format_active_bots(
    bots_data: Dict[str, Any],
    server_name: Optional[str] = None,
    server_status: Optional[str] = None,
    bot_runs: Optional[Dict[str, str]] = None,
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
    server_status: Optional[str] = None,
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
            bot_status.get("message", "Unknown error"),
            server_name=server_name,
            server_status=server_status,
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
        message += (
            f"*⚙️ Controllers \\({escape_markdown_v2(str(len(controllers)))}\\):*\n"
        )
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
        message += (
            f"{' ' * 15}_Server: {escape_markdown_v2(server_name)} {status_emoji}_"
        )

    return message


def format_error_message(
    error: str, server_name: Optional[str] = None, server_status: Optional[str] = None
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

        separator = "⎯" * 15
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
    positions = positions_data.get("positions", [])
    total = positions_data.get("total", 0)

    if not positions or total == 0:
        return "📊 *Perpetual Positions*\n_No open positions_\n"

    message = f"📊 *Perpetual Positions* \\({escape_markdown_v2(str(total))}\\)\n\n"

    # Group by account
    from collections import defaultdict

    by_account = defaultdict(list)

    for pos in positions:
        account_name = pos.get("account_name", "Unknown")
        by_account[account_name].append(pos)

    # Format each account's positions
    for account_name, account_positions in by_account.items():
        message += f"*Account:* {escape_markdown_v2(account_name)}\n"

        # Create table - optimized for mobile width with minimal spacing
        # Columns: Connector(10) Pair(10) Side(4) Value(7) PnL$(7)
        table_content = "```\n"
        table_content += (
            f"{'Connector':<10} {'Pair':<10} {'Side':<4} {'Value':<7} {'PnL($)':>7}\n"
        )
        table_content += f"{'─'*10} {'─'*10} {'─'*4} {'─'*7} {'─'*7}\n"

        for pos in account_positions:
            connector = pos.get("connector_name", "N/A")
            pair = pos.get("trading_pair", "N/A")
            # Try multiple field names for side (same as clob_trading)
            side = (
                pos.get("position_side")
                or pos.get("side")
                or pos.get("trade_type", "N/A")
            )
            amount = pos.get("amount", 0)
            entry_price = pos.get("entry_price", 0)
            unrealized_pnl = pos.get("unrealized_pnl", 0)

            # Truncate connector name if too long
            connector_display = connector[:9] if len(connector) > 9 else connector

            # Truncate pair name
            pair_display = pair[:9] if len(pair) > 9 else pair

            # Truncate side - use short form
            side_upper = side.upper() if side else "N/A"
            if side_upper in ("LONG", "BUY"):
                side_display = "LONG"
            elif side_upper in ("SHORT", "SELL"):
                side_display = "SHRT"
            else:
                side_display = side[:4] if len(side) > 4 else side

            # Calculate position value (Size * Entry Price)
            try:
                position_value = float(amount) * float(entry_price)
                value_str = format_number(position_value).replace("$", "")[:6]
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


def resolve_token_symbol(
    address: str, token_cache: Optional[Dict[str, str]] = None
) -> str:
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


def format_change_compact(value: Optional[float]) -> str:
    """Format a percentage change compactly for table columns"""
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f}%"


def format_connector_detail(
    balances: Dict[str, Any],
    connector_key: str,
    changes_24h: Optional[Dict[str, Any]] = None,
    total_value: float = 0.0,
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
    connector_total = sum(
        b.get("value", 0) for b in connector_balances if b.get("value", 0) > 0
    )

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
        reverse=True,
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
) -> str:
    """
    Format complete portfolio overview with balances.

    Args:
        overview_data: Dictionary containing:
            - balances: Portfolio state
        server_name: Name of the server (optional)
        server_status: Status of the server (optional)

    Returns:
        Formatted Telegram message with portfolio sections
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
    # SECTION 1: TOTAL VALUE
    # ============================================
    balances = overview_data.get("balances") if overview_data else None

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

    if total_value > 0:
        total_str = format_number(total_value)
        line = f"💵 *Total:* `{escape_markdown_v2(total_str)}`"
    else:
        line = f"💵 *Total:* `{escape_markdown_v2('$0.00')}`"

    message += line + "\n\n"

    # ============================================
    # SECTION 2: PER-ACCOUNT, PER-CONNECTOR BREAKDOWN
    # ============================================
    if balances:
        for account_name, account_data in balances.items():
            # Collect connectors with value > 0, sorted by total descending
            connector_list = []
            for connector_name, connector_balances in account_data.items():
                if not connector_balances:
                    continue
                conn_total = sum(
                    b.get("value", 0)
                    for b in connector_balances
                    if b.get("value", 0) > 0
                )
                if conn_total > 0:
                    connector_list.append(
                        (connector_name, connector_balances, conn_total)
                    )

            if not connector_list:
                continue

            connector_list.sort(key=lambda x: x[2], reverse=True)

            message += f"*Account:* _{escape_markdown_v2(account_name)}_\n"

            for connector_name, connector_balances, conn_total in connector_list:
                conn_total_str = format_number(conn_total)
                message += f"  🏦 *{escape_markdown_v2(connector_name)}* \\- `{escape_markdown_v2(conn_total_str)}`\n\n"

                # Sort tokens by value descending, filter > $1
                sorted_tokens = sorted(
                    [b for b in connector_balances if b.get("value", 0) >= 1],
                    key=lambda x: x.get("value", 0),
                    reverse=True,
                )

                if sorted_tokens:
                    message += "```\n"
                    message += f"{'Token':<6} {'Price':<9} {'Value':<8} {'%Tot':>5}\n"
                    message += f"{'─'*6} {'─'*9} {'─'*8} {'─'*5}\n"

                    for balance in sorted_tokens[:10]:
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

                        token_display = token[:5] if len(token) > 5 else token
                        price = value / units if units > 0 else 0
                        price_str = format_price(price)[:9]
                        value_str = format_number(value)[:8]
                        pct = (value / total_value * 100) if total_value > 0 else 0
                        pct_str = f"{pct:.1f}%" if pct < 100 else f"{pct:.0f}%"

                        message += f"{token_display:<6} {price_str:<9} {value_str:<8} {pct_str:>5}\n"

                    if len(sorted_tokens) > 10:
                        message += f"\n... +{len(sorted_tokens) - 10} more\n"

                    message += "```\n"

            message += "\n"

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
    table_content += (
        f"{'Pair':<10} {'Side':<4} {'Amt':<8} {'Price':<8} {'Type':<6} {'Status':<7}\n"
    )
    table_content += f"{'─'*10} {'─'*4} {'─'*8} {'─'*8} {'─'*6} {'─'*7}\n"

    for order in orders[:10]:  # Limit to 10 for Telegram
        pair = order.get("trading_pair", "N/A")
        side = order.get("trade_type", "N/A")
        amount = order.get("amount", 0)
        price = order.get("price", 0)
        order_type = order.get("order_type", "N/A")
        status = order.get("status", "N/A")

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
            price_str = format_amount(float(price))[:8] if price else "-"
        except (ValueError, TypeError):
            price_str = str(price)[:8] if price else "-"

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
        account_name = pos.get("account_name", "master_account")
        by_account[account_name].append(pos)

    table_content = ""

    # Format each account's positions
    for account_name, account_positions in by_account.items():
        table_content += f"Account: {account_name}\n"

        # Create table - same format as format_perpetual_positions
        table_content += (
            f"{'Connector':<10} {'Pair':<10} {'Side':<4} {'Value':<7} {'PnL($)':>7}\n"
        )
        table_content += f"{'─'*10} {'─'*10} {'─'*4} {'─'*7} {'─'*7}\n"

        for pos in account_positions[:10]:  # Limit to 10 for Telegram
            connector = pos.get("connector_name", "N/A")
            pair = pos.get("trading_pair", "N/A")
            # Try multiple field names for side
            side = (
                pos.get("position_side")
                or pos.get("side")
                or pos.get("trade_type", "N/A")
            )
            amount = pos.get("amount", 0)
            entry_price = pos.get("entry_price", 0)
            pnl = pos.get("unrealized_pnl", 0)

            # Truncate connector name if too long
            connector_display = connector[:9] if len(connector) > 9 else connector

            # Truncate pair name if too long
            pair_display = pair[:9] if len(pair) > 9 else pair

            # Truncate side - use short form
            side_upper = side.upper() if side else "N/A"
            if side_upper in ("LONG", "BUY"):
                side_display = "LONG"
            elif side_upper in ("SHORT", "SELL"):
                side_display = "SHRT"
            else:
                side_display = side[:4] if len(side) > 4 else side

            # Calculate position value (Size * Entry Price)
            try:
                position_value = abs(float(amount) * float(entry_price))
                value_str = format_number(position_value).replace("$", "")[:6]
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
