"""
Telegram message formatters for Hummingbot data
"""

from typing import Dict, Any, List
import re


def escape_markdown_v2(text: str) -> str:
    """
    Escape special characters for Telegram MarkdownV2

    Characters that need escaping: _ * [ ] ( ) ~ ` > # + - = | { } . !
    """
    special_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(special_chars)}])', r'\\\1', str(text))


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


def format_portfolio_state(state: Dict[str, Any]) -> str:
    """
    Format detailed portfolio state for Telegram

    Args:
        state: Portfolio state from client.portfolio.get_state()

    Returns:
        Formatted Telegram message
    """
    message = "💼 *Portfolio Details*\n\n"

    total_value = 0.0

    for account_name, account_data in state.items():
        message += f"*Account:* {escape_markdown_v2(account_name)}\n"

        for connector_name, balances in account_data.items():
            if balances:
                message += f"  🔗 {escape_markdown_v2(connector_name)}\n"

                for balance in balances:
                    token = balance.get("token", "???")
                    units = balance.get("units", 0)
                    value = balance.get("value", 0)
                    total_value += value

                    if value > 1:  # Only show balances > $1
                        amount_str = format_amount(units)
                        value_str = format_number(value)
                        message += f"    • {escape_markdown_v2(token)}: "
                        message += f"{escape_markdown_v2(amount_str)} "
                        message += f"{escape_markdown_v2(value_str)}\n"

                # Add spacing between connectors
                message += "\n"

    message += f"💵 *Total:* {escape_markdown_v2(format_number(total_value))}\n"

    return message


def format_active_bots(bots_data: Dict[str, Any]) -> str:
    """
    Format active bots status for Telegram

    Args:
        bots_data: Active bots data from client.bot_orchestration.get_active_bots_status()

    Returns:
        Formatted Telegram message
    """
    message = "🤖 *Active Bots Status*\n\n"

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

    return message


def format_bot_status(bot_status: Dict[str, Any]) -> str:
    """
    Format individual bot status for Telegram

    Args:
        bot_status: Bot status from client.bot_orchestration.get_bot_status()

    Returns:
        Formatted Telegram message
    """
    if bot_status.get("status") != "success":
        return f"❌ *Error:* {escape_markdown_v2(bot_status.get('message', 'Unknown error'))}"

    data = bot_status.get("data", {})
    bot_name = data.get("name", "Unknown")
    is_running = data.get("is_running", False)

    status_emoji = "🟢" if is_running else "🔴"
    message = f"{status_emoji} *Bot:* {escape_markdown_v2(bot_name)}\n\n"

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

    return message


def format_error_message(error: str) -> str:
    """
    Format error message for Telegram

    Args:
        error: Error message

    Returns:
        Formatted error message
    """
    return f"❌ *Error*\n\n{escape_markdown_v2(error)}"


def format_success_message(message: str) -> str:
    """
    Format success message for Telegram

    Args:
        message: Success message

    Returns:
        Formatted success message
    """
    return f"✅ *Success*\n\n{escape_markdown_v2(message)}"
