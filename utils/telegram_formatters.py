"""
Telegram message formatters for Hummingbot data
"""

from typing import Dict, Any, List, Optional
import re


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
    server_text = f"Server: {server_name} {status_emoji}"

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

            # Start table
            table_content = "```\n"
            table_content += f"{'Token':<12} {'Amount':<15} {'Value':<12} {'%':>6}\n"
            table_content += f"{'─'*12} {'─'*15} {'─'*12} {'─'*6}\n"

            for balance in balances:
                token = balance["token"]
                units = balance["units"]
                value = balance["value"]
                percentage = balance["percentage"]

                # Format values - remove $ signs from amounts in table
                amount_str = format_amount(units)
                value_str = format_number(value).replace('$', '')

                # Truncate long token names
                token_display = token[:10] if len(token) > 10 else token

                # Add row to table
                table_content += f"{token_display:<12} {amount_str:<15} {value_str:<12} {percentage:>5.1f}%\n"

            # Close table
            table_content += "```\n\n"
            message += table_content

    # Show total
    if total_value > 0:
        message += f"💵 *Total Portfolio Value:* `{escape_markdown_v2(format_number(total_value))}`\n"
    else:
        message += f"💵 *Total Portfolio Value:* `{escape_markdown_v2('$0.00')}`\n"

    return message


def format_active_bots(
    bots_data: Dict[str, Any],
    server_name: Optional[str] = None,
    server_status: Optional[str] = None
) -> str:
    """
    Format active bots status for Telegram

    Args:
        bots_data: Active bots data from client.bot_orchestration.get_active_bots_status()
        server_name: Name of the server (optional)
        server_status: Status of the server (optional)

    Returns:
        Formatted Telegram message
    """
    message = "🤖 *Active Bots*\n\n"

    bots = bots_data.get("data", [])

    if not bots:
        message += "_No active bots found_\n"
        return message

    for bot in bots:
        bot_name = bot.get("name", "Unknown")
        is_running = bot.get("is_running", False)
        controllers = bot.get("controllers", [])

        # Bot header
        status_emoji = "🟢" if is_running else "🔴"
        message += f"{status_emoji} *{escape_markdown_v2(bot_name)}*\n"

        # Performance metrics
        performance = bot.get("performance", {})
        realized_pnl = performance.get("realized_pnl_quote", 0)
        unrealized_pnl = performance.get("unrealized_pnl_quote", 0)
        total_pnl = realized_pnl + unrealized_pnl
        volume = performance.get("volume_traded", 0)

        pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        message += f"  {pnl_emoji} *PnL:* {escape_markdown_v2(format_number(total_pnl))}\n"
        message += f"    \\- Realized: {escape_markdown_v2(format_number(realized_pnl))}\n"
        message += f"    \\- Unrealized: {escape_markdown_v2(format_number(unrealized_pnl))}\n"
        message += f"  📊 *Volume:* {escape_markdown_v2(format_number(volume))}\n"

        # Controllers
        if controllers:
            message += f"  ⚙️ *Controllers:* {escape_markdown_v2(str(len(controllers)))}\n"
            for ctrl in controllers[:3]:  # Show max 3 controllers
                ctrl_name = ctrl.get("controller_name", "Unknown")
                ctrl_status = ctrl.get("status", "unknown")
                message += f"    • {escape_markdown_v2(ctrl_name)} _{escape_markdown_v2(f'({ctrl_status})')}_\n"

        # Latest errors
        error_logs = bot.get("error_logs", [])
        if error_logs:
            message += f"  ⚠️ *Latest Error:*\n"
            latest_error = error_logs[0].get("message", "Unknown error")
            # Truncate long errors
            if len(latest_error) > 50:
                latest_error = latest_error[:50] + "..."
            message += f"    _{escape_markdown_v2(latest_error)}_\n"

        message += "\n"

    message += f"*Total Active Bots:* {escape_markdown_v2(str(len(bots)))}\n"

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
