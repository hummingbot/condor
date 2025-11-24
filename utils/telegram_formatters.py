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
        table_content = "```\n"
        table_content += f"{'Connector':<11} {'Pair':<9} {'Side':<5} {'Value':<7} {'PnL':>6}\n"
        table_content += f"{'─'*11} {'─'*9} {'─'*5} {'─'*7} {'─'*6}\n"

        for pos in account_positions:
            connector = pos.get('connector_name', 'N/A')
            pair = pos.get('trading_pair', 'N/A')
            # Try multiple field names for side (same as clob_trading)
            side = pos.get('position_side') or pos.get('side') or pos.get('trade_type', 'N/A')
            amount = pos.get('amount', 0)
            entry_price = pos.get('entry_price', 0)
            unrealized_pnl = pos.get('unrealized_pnl', 0)

            # Truncate connector name if too long
            connector_display = connector[:10] if len(connector) > 10 else connector

            # Truncate pair name if too long
            pair_display = pair[:8] if len(pair) > 8 else pair

            # Truncate side if too long
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
                    pnl_str = f"+{pnl_float:.1f}"[:5]
                else:
                    pnl_str = f"{pnl_float:.1f}"[:5]
            except (ValueError, TypeError):
                pnl_str = str(unrealized_pnl)[:5]

            table_content += f"{connector_display:<11} {pair_display:<9} {side_display:<5} {value_str:<7} {pnl_str:>6}\n"

        table_content += "```\n\n"
        message += table_content

    return message


def format_lp_positions(positions_data: Dict[str, Any]) -> str:
    """
    Format LP (CLMM) positions for Telegram display

    Args:
        positions_data: Dictionary with 'positions' list and 'total' count

    Returns:
        Formatted string section for LP positions
    """
    positions = positions_data.get('positions', [])
    total = positions_data.get('total', 0)

    if not positions or total == 0:
        return "🏊 *LP Positions \\(CLMM\\)*\n_No active LP positions_\n"

    message = f"🏊 *LP Positions \\(CLMM\\)* \\({escape_markdown_v2(str(total))}\\)\n\n"

    # Create table
    table_content = "```\n"
    table_content += f"{'Connector':<12} {'Network':<10} {'Pair':<12} {'Range':<18}\n"
    table_content += f"{'─'*12} {'─'*10} {'─'*12} {'─'*18}\n"

    for pos in positions[:10]:  # Show max 10 positions
        connector = pos.get('connector', 'N/A')[:11]
        network = pos.get('network', 'N/A')[:9]
        trading_pair = pos.get('trading_pair', 'N/A')[:11]
        lower_price = pos.get('lower_price', 0)
        upper_price = pos.get('upper_price', 0)

        # Format price range
        if lower_price and upper_price:
            try:
                range_str = f"{float(lower_price):.2f}-{float(upper_price):.2f}"[:17]
            except:
                range_str = "N/A"
        else:
            range_str = "N/A"

        table_content += f"{connector:<12} {network:<10} {trading_pair:<12} {range_str:<18}\n"

    if total > 10:
        table_content += f"\n... and {total - 10} more positions\n"

    table_content += "```\n\n"
    message += table_content

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


def format_portfolio_overview(
    overview_data: Dict[str, Any],
    server_name: Optional[str] = None,
    server_status: Optional[str] = None
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
    # SECTION 1: BALANCES - Detailed tables by account and connector
    # ============================================
    balances = overview_data.get('balances')
    if balances:
        total_value = 0.0
        all_balances = []

        # Collect all balances with metadata
        for account_name, account_data in balances.items():
            for connector_name, connector_balances in account_data.items():
                if connector_balances:
                    for balance in connector_balances:
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

        # Group by account and connector
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

        # Build the balances section by iterating through accounts and connectors
        for account, connectors in grouped.items():
            message += f"*Account:* {escape_markdown_v2(account)}\n"

            for connector, balances_list in connectors.items():
                # Calculate total value for this connector
                connector_total = sum(balance["value"] for balance in balances_list)
                connector_total_str = format_number(connector_total)

                message += f"  🏦 *{escape_markdown_v2(connector)}* \\- `{escape_markdown_v2(connector_total_str)}`\n\n"

                # Start table
                table_content = "```\n"
                table_content += f"{'Token':<12} {'Amount':<15} {'Value':<12} {'%':>6}\n"
                table_content += f"{'─'*12} {'─'*15} {'─'*12} {'─'*6}\n"

                for balance in balances_list:
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
            message += f"💵 *Total Portfolio Value:* `{escape_markdown_v2(format_number(total_value))}`\n\n"
        else:
            message += f"💵 *Total Portfolio Value:* `{escape_markdown_v2('$0.00')}`\n\n"

    # ============================================
    # SECTION 2: PERPETUAL POSITIONS
    # ============================================
    perp_positions = overview_data.get('perp_positions', {"positions": [], "total": 0})
    message += format_perpetual_positions(perp_positions)

    # ============================================
    # SECTION 3: LP POSITIONS
    # ============================================
    lp_positions = overview_data.get('lp_positions', {"positions": [], "total": 0})
    message += format_lp_positions(lp_positions)

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
    table_content += f"{'Pair':<12} {'Side':<5} {'Amount':<12} {'Type':<8} {'Status':<8}\n"
    table_content += f"{'─'*12} {'─'*5} {'─'*12} {'─'*8} {'─'*8}\n"

    for order in orders[:10]:  # Limit to 10 for Telegram
        pair = order.get('trading_pair', 'N/A')
        side = order.get('trade_type', 'N/A')
        amount = order.get('amount', 0)
        order_type = order.get('order_type', 'N/A')
        status = order.get('status', 'N/A')

        # Truncate long values
        pair_display = pair[:11] if len(pair) > 11 else pair
        side_display = side[:4] if len(side) > 4 else side
        type_display = order_type[:7] if len(order_type) > 7 else order_type
        status_display = status[:7] if len(status) > 7 else status

        # Format amount
        try:
            amount_str = format_amount(float(amount))[:11]
        except (ValueError, TypeError):
            amount_str = str(amount)[:11]

        table_content += f"{pair_display:<12} {side_display:<5} {amount_str:<12} {type_display:<8} {status_display:<8}\n"

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

    # Build monospace table - optimized for mobile width
    table_content = ""
    table_content += f"{'Exchange':<13} {'Pair':<10} {'Side':<5} {'Size':<7} {'Entry':<8} {'PnL':>7}\n"
    table_content += f"{'─'*13} {'─'*10} {'─'*5} {'─'*7} {'─'*8} {'─'*7}\n"

    for pos in positions[:10]:  # Limit to 10 for Telegram
        connector = pos.get('connector_name', 'N/A')
        pair = pos.get('trading_pair', 'N/A')
        # Try multiple field names for side
        side = pos.get('position_side') or pos.get('side') or pos.get('trade_type', 'N/A')
        size = pos.get('amount', 0)
        entry = pos.get('entry_price', 0)
        pnl = pos.get('unrealized_pnl', 0)

        # Truncate connector name if too long
        connector_display = connector[:12] if len(connector) > 12 else connector

        # Truncate pair name if too long
        pair_display = pair[:9] if len(pair) > 9 else pair

        # Truncate side if too long
        side_display = side[:4] if len(side) > 4 else side

        # Format numbers
        try:
            size_str = format_amount(float(size))[:6]
        except (ValueError, TypeError):
            size_str = str(size)[:6]

        try:
            entry_str = f"{float(entry):.2f}"[:7]
        except (ValueError, TypeError):
            entry_str = str(entry)[:7]

        try:
            pnl_float = float(pnl)
            if pnl_float >= 0:
                pnl_str = f"+{pnl_float:.2f}"[:6]
            else:
                pnl_str = f"{pnl_float:.2f}"[:6]
        except (ValueError, TypeError):
            pnl_str = str(pnl)[:6]

        table_content += f"{connector_display:<13} {pair_display:<10} {side_display:<5} {size_str:<7} {entry_str:<8} {pnl_str:>7}\n"

    if len(positions) > 10:
        table_content += f"\n... and {len(positions) - 10} more positions\n"

    return table_content
