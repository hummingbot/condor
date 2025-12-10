"""
Portfolio command handler using hummingbot_api_client
"""

import logging
import time
from datetime import timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from utils.auth import restricted
from utils.telegram_formatters import (
    format_portfolio_summary,
    format_portfolio_state,
    format_portfolio_overview,
    format_error_message,
    escape_markdown_v2
)
from handlers.config import clear_config_state
from handlers.config.user_preferences import (
    get_portfolio_prefs,
    set_portfolio_days,
    PORTFOLIO_DAYS_OPTIONS,
    get_all_enabled_networks,
)
from utils.portfolio_graphs import generate_portfolio_dashboard
from utils.trading_data import get_portfolio_overview

logger = logging.getLogger(__name__)


def _calculate_start_time(days: int) -> int:
    """Calculate start_time as now - days in Unix timestamp"""
    return int(time.time()) - (days * 24 * 60 * 60)


def _get_optimal_interval(days: int, max_points: int = 100) -> str:
    """
    Calculate the optimal interval based on days and max data points.

    With limit=100, we need to choose an interval that covers the full period.
    - 1 day = 96 points at 15m, 24 points at 1h
    - 3 days = 288 points at 15m (too many!), 72 points at 1h
    - 7 days = 168 points at 1h (too many!), 56 points at 3h
    - 30 days = 720 points at 1h, 240 at 3h, 120 at 6h, 30 at 1d

    Returns the smallest interval that fits within max_points.
    """
    total_hours = days * 24

    # Available intervals in hours
    intervals = [
        (0.25, "15m"),   # 15 minutes
        (1, "1h"),       # 1 hour
        (3, "3h"),       # 3 hours
        (6, "6h"),       # 6 hours
        (12, "12h"),     # 12 hours
        (24, "1d"),      # 1 day
    ]

    for interval_hours, interval_str in intervals:
        points_needed = total_hours / interval_hours
        if points_needed <= max_points:
            return interval_str

    # Fallback to 1d if nothing else works
    return "1d"


def _filter_balances_by_networks(balances: dict, enabled_networks: set) -> dict:
    """
    Filter portfolio balances to only include enabled networks.

    The connector name in the portfolio state corresponds to the network
    (e.g., 'solana-mainnet-beta', 'ethereum-mainnet', 'base').

    Args:
        balances: Portfolio state dict {account: {connector: [balances]}}
        enabled_networks: Set of enabled network IDs, or None for no filtering

    Returns:
        Filtered balances dict with same structure
    """
    if enabled_networks is None:
        return balances

    if not balances:
        return balances

    filtered = {}
    for account_name, account_data in balances.items():
        filtered_account = {}
        for connector_name, connector_balances in account_data.items():
            # Check if this connector/network is enabled
            connector_lower = connector_name.lower()
            if connector_lower in enabled_networks:
                filtered_account[connector_name] = connector_balances
        if filtered_account:
            filtered[account_name] = filtered_account

    return filtered


def _parse_snapshot_tokens(state: dict) -> dict:
    """
    Parse a state snapshot and return token holdings aggregated.

    Returns: {token: {"units": float, "value": float}}
    """
    tokens = {}
    for account_name, connectors in state.items():
        if not isinstance(connectors, dict):
            continue
        for connector_name, holdings in connectors.items():
            if not isinstance(holdings, list):
                continue
            for holding in holdings:
                if isinstance(holding, dict):
                    token = holding.get("token", "")
                    if not token:
                        continue

                    units = holding.get("units", 0)
                    value = holding.get("value", 0)

                    # Convert to float
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

                    if token not in tokens:
                        tokens[token] = {"units": 0.0, "value": 0.0}
                    tokens[token]["units"] += float(units)
                    tokens[token]["value"] += float(value)

    return tokens


def _detect_deposit_withdrawals(parsed_points: list, threshold_pct: float = 10.0) -> list:
    """
    Detect deposits/withdrawals by analyzing changes in token units between snapshots.

    A deposit/withdrawal is detected when:
    - Token units change significantly (>threshold_pct) between consecutive snapshots
    - The change is too large to be explained by normal trading

    Returns: List of detected movements with structure:
        {
            "timestamp": datetime,
            "token": str,
            "type": "deposit" | "withdrawal",
            "units_change": float,
            "value_estimate": float (value at time of movement)
        }
    """
    if len(parsed_points) < 2:
        return []

    movements = []

    for i in range(1, len(parsed_points)):
        prev = parsed_points[i - 1]
        curr = parsed_points[i]

        prev_tokens = prev["tokens"]
        curr_tokens = curr["tokens"]

        # Check all tokens in both snapshots
        all_tokens = set(prev_tokens.keys()) | set(curr_tokens.keys())

        for token in all_tokens:
            prev_units = prev_tokens.get(token, {}).get("units", 0)
            curr_units = curr_tokens.get(token, {}).get("units", 0)
            curr_value = curr_tokens.get(token, {}).get("value", 0)
            prev_value = prev_tokens.get(token, {}).get("value", 0)

            # Skip tokens with very small values (< $1)
            if max(curr_value, prev_value) < 1:
                continue

            units_change = curr_units - prev_units

            # Calculate percentage change in units
            if prev_units > 0:
                pct_change = abs(units_change / prev_units) * 100
            elif curr_units > 0:
                # New token appeared - likely deposit
                pct_change = 100
            else:
                continue

            # Detect significant unit changes (threshold%)
            if pct_change > threshold_pct and abs(units_change) > 0.0001:
                # Estimate value of the movement
                if curr_units > 0:
                    price = curr_value / curr_units
                elif prev_units > 0:
                    price = prev_value / prev_units
                else:
                    price = 0

                value_estimate = abs(units_change) * price

                # Only track movements worth more than $10
                if value_estimate > 10:
                    movements.append({
                        "timestamp": curr["timestamp"],
                        "token": token,
                        "type": "deposit" if units_change > 0 else "withdrawal",
                        "units_change": units_change,
                        "value_estimate": value_estimate
                    })

    return movements


def _calculate_pnl_indicators(history_data: dict, current_value: float) -> dict:
    """
    Calculate PNL indicators from historical data, adjusted for deposits/withdrawals.

    Returns dict with keys:
        - pnl_24h, pnl_7d, pnl_30d: percentage change adjusted for deposits/withdrawals
        - detected_movements: list of suspected deposits/withdrawals
    """
    from datetime import datetime, timedelta

    result = {
        "pnl_24h": None,
        "pnl_7d": None,
        "pnl_30d": None,
        "detected_movements": [],
    }

    if not history_data or not current_value:
        return result

    data_points = history_data.get("data", [])
    if not data_points:
        return result

    # Parse all data points with detailed token info
    parsed_points = []
    for point in data_points:
        timestamp_str = point.get("timestamp", "")
        state = point.get("state", {})

        try:
            ts = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            continue

        tokens = _parse_snapshot_tokens(state)
        total_value = sum(t["value"] for t in tokens.values())

        parsed_points.append({
            "timestamp": ts,
            "value": total_value,
            "tokens": tokens
        })

    if not parsed_points:
        return result

    # Sort by timestamp (oldest first)
    parsed_points.sort(key=lambda x: x["timestamp"])

    # Detect deposits/withdrawals
    movements = _detect_deposit_withdrawals(parsed_points)
    result["detected_movements"] = movements

    # Calculate cumulative deposit/withdrawal value over time
    # This will be used to adjust the PNL calculation
    movement_adjustments = {}  # timestamp -> cumulative adjustment
    cumulative = 0.0
    for m in sorted(movements, key=lambda x: x["timestamp"]):
        if m["type"] == "deposit":
            cumulative += m["value_estimate"]
        else:  # withdrawal
            cumulative -= m["value_estimate"]
        movement_adjustments[m["timestamp"]] = cumulative

    now = datetime.now(parsed_points[-1]["timestamp"].tzinfo) if parsed_points[-1]["timestamp"].tzinfo else datetime.utcnow()

    # Calculate total adjustment (all movements up to now)
    total_adjustment = cumulative

    # Find values closest to target times
    targets = {
        "pnl_24h": now - timedelta(days=1),
        "pnl_7d": now - timedelta(days=7),
        "pnl_30d": now - timedelta(days=30),
    }

    for key, target_time in targets.items():
        closest_point = None
        min_diff = float('inf')

        for point in parsed_points:
            diff = abs((point["timestamp"] - target_time).total_seconds())
            if diff < min_diff and diff < 12 * 3600:
                min_diff = diff
                closest_point = point

        if closest_point and closest_point["value"] > 0:
            # Calculate adjustment at target time (movements that happened after target)
            adjustment_at_target = 0.0
            for m in movements:
                if m["timestamp"] > closest_point["timestamp"]:
                    if m["type"] == "deposit":
                        adjustment_at_target += m["value_estimate"]
                    else:
                        adjustment_at_target -= m["value_estimate"]

            # Adjusted current value = current - deposits + withdrawals (since target)
            adjusted_current = current_value - adjustment_at_target

            # Calculate PNL percentage
            if closest_point["value"] > 0:
                pnl_pct = ((adjusted_current - closest_point["value"]) / closest_point["value"]) * 100
                result[key] = pnl_pct

    return result


def _calculate_24h_changes(history_data: dict, current_balances: dict) -> dict:
    """
    Calculate 24h changes for tokens and connectors.

    Args:
        history_data: Historical data from API
        current_balances: Current balances from overview_data['balances']

    Returns:
        {
            "tokens": {token: {"price_change": float, "units_change": float}},
            "connectors": {account: {connector: {"value_change": float, "pct_change": float}}}
        }
    """
    from datetime import datetime, timedelta

    result = {
        "tokens": {},
        "connectors": {},
    }

    if not history_data or not current_balances:
        return result

    data_points = history_data.get("data", [])
    if not data_points:
        return result

    # Parse current state - detailed by account/connector/token
    current_detailed = {}  # {account: {connector: {token: {units, value}}}}
    current_tokens = {}  # {token: {units, value}} aggregated

    for account_name, account_data in current_balances.items():
        if account_name not in current_detailed:
            current_detailed[account_name] = {}
        for connector_name, holdings in account_data.items():
            if connector_name not in current_detailed[account_name]:
                current_detailed[account_name][connector_name] = {}
            connector_value = 0.0
            if holdings:
                for h in holdings:
                    token = h.get("token", "")
                    units = float(h.get("units", 0))
                    value = float(h.get("value", 0))
                    if token:
                        current_detailed[account_name][connector_name][token] = {
                            "units": units, "value": value
                        }
                        connector_value += value
                        # Aggregate tokens
                        if token not in current_tokens:
                            current_tokens[token] = {"units": 0.0, "value": 0.0}
                        current_tokens[token]["units"] += units
                        current_tokens[token]["value"] += value
            current_detailed[account_name][connector_name]["_total"] = connector_value

    # Find snapshot closest to 24h ago
    now = datetime.now(timezone.utc)
    target_time = now - timedelta(days=1)

    closest_point = None
    min_diff = float('inf')

    for point in data_points:
        timestamp_str = point.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            continue

        diff = abs((ts - target_time).total_seconds())
        if diff < min_diff and diff < 12 * 3600:  # Within 12 hours tolerance
            min_diff = diff
            closest_point = point

    if not closest_point:
        return result

    # Parse 24h ago state
    state_24h = closest_point.get("state", {})
    tokens_24h = {}  # {token: {units, value}} aggregated
    connectors_24h = {}  # {account: {connector: total_value}}

    for account_name, connectors in state_24h.items():
        if not isinstance(connectors, dict):
            continue
        if account_name not in connectors_24h:
            connectors_24h[account_name] = {}
        for connector_name, holdings in connectors.items():
            if not isinstance(holdings, list):
                continue
            connector_value = 0.0
            for h in holdings:
                if isinstance(h, dict):
                    token = h.get("token", "")
                    units = float(h.get("units", 0) or 0)
                    value = float(h.get("value", 0) or 0)
                    if token:
                        connector_value += value
                        if token not in tokens_24h:
                            tokens_24h[token] = {"units": 0.0, "value": 0.0}
                        tokens_24h[token]["units"] += units
                        tokens_24h[token]["value"] += value
            connectors_24h[account_name][connector_name] = connector_value

    # Calculate token changes (price and units)
    for token, current in current_tokens.items():
        past = tokens_24h.get(token, {"units": 0, "value": 0})

        current_units = current["units"]
        current_value = current["value"]
        past_units = past["units"]
        past_value = past["value"]

        # Calculate price (value / units)
        current_price = current_value / current_units if current_units > 0 else 0
        past_price = past_value / past_units if past_units > 0 else 0

        # Price change %
        if past_price > 0:
            price_change = ((current_price - past_price) / past_price) * 100
        elif current_price > 0:
            price_change = 100.0  # New token
        else:
            price_change = 0.0

        # Units change %
        if past_units > 0:
            units_change = ((current_units - past_units) / past_units) * 100
        elif current_units > 0:
            units_change = 100.0  # New token
        else:
            units_change = 0.0

        result["tokens"][token] = {
            "price_change": price_change,
            "units_change": units_change,
        }

    # Calculate connector changes
    for account_name, connectors in current_detailed.items():
        if account_name not in result["connectors"]:
            result["connectors"][account_name] = {}
        for connector_name, tokens in connectors.items():
            current_total = tokens.get("_total", 0)
            past_total = connectors_24h.get(account_name, {}).get(connector_name, 0)

            if past_total > 0:
                pct_change = ((current_total - past_total) / past_total) * 100
            elif current_total > 0:
                pct_change = 100.0  # New connector
            else:
                pct_change = 0.0

            result["connectors"][account_name][connector_name] = {
                "value_change": current_total - past_total,
                "pct_change": pct_change,
            }

    return result


async def _fetch_dashboard_data(client, days: int):
    """
    Fetch all data needed for the portfolio dashboard.

    Returns:
        Tuple of (overview_data, history, token_distribution, accounts_distribution, pnl_history, graph_interval)
    """
    import asyncio

    # Calculate start_time based on days for graph
    start_time = _calculate_start_time(days)
    # For PNL indicators, we need 30 days of history
    pnl_start_time = _calculate_start_time(30)

    # Calculate optimal interval for the graph based on days
    graph_interval = _get_optimal_interval(days)
    logger.info(f"Fetching portfolio data: days={days}, optimal_interval={graph_interval}, start_time={start_time}")

    # Fetch all data in parallel
    overview_task = get_portfolio_overview(
        client,
        account_names=None,
        include_balances=True,
        include_perp_positions=True,
        include_lp_positions=True,
        include_active_orders=True
    )

    history_task = client.portfolio.get_history(
        start_time=start_time,
        limit=100,
        interval=graph_interval
    )

    # Fetch 30-day history for PNL calculations (use 1d interval for efficiency)
    pnl_history_task = client.portfolio.get_history(
        start_time=pnl_start_time,
        limit=100,
        interval="1d"
    )

    token_dist_task = client.portfolio.get_distribution()
    accounts_dist_task = client.portfolio.get_accounts_distribution()

    results = await asyncio.gather(
        overview_task,
        history_task,
        token_dist_task,
        accounts_dist_task,
        pnl_history_task,
        return_exceptions=True
    )

    # Handle any exceptions
    overview_data = results[0] if not isinstance(results[0], Exception) else None
    history = results[1] if not isinstance(results[1], Exception) else None
    token_distribution = results[2] if not isinstance(results[2], Exception) else None
    accounts_distribution = results[3] if not isinstance(results[3], Exception) else None
    pnl_history = results[4] if not isinstance(results[4], Exception) else None

    # Log what the API returned for history
    if history and not isinstance(history, Exception):
        pagination = history.get("pagination", {})
        data_count = len(history.get("data", []))
        logger.info(f"History API response: {data_count} data points, pagination={pagination}")

    if isinstance(results[0], Exception):
        logger.error(f"Error fetching overview: {results[0]}")
    if isinstance(results[1], Exception):
        logger.error(f"Error fetching history: {results[1]}")
    if isinstance(results[2], Exception):
        logger.error(f"Error fetching token distribution: {results[2]}")
    if isinstance(results[3], Exception):
        logger.error(f"Error fetching accounts distribution: {results[3]}")
    if isinstance(results[4], Exception):
        logger.error(f"Error fetching PNL history: {results[4]}")

    return overview_data, history, token_distribution, accounts_distribution, pnl_history, graph_interval


@restricted
async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /portfolio command - Display comprehensive portfolio dashboard

    Usage:
        /portfolio - Show portfolio dashboard with all graphs and information

    Progressive loading: Fetches all data in parallel and updates UI as each piece arrives.
    """
    import asyncio

    # Clear any config state to prevent interference
    clear_config_state(context)

    # Get the appropriate message object for replies
    message = update.message or (update.callback_query.message if update.callback_query else None)
    chat_id = update.effective_chat.id
    if not message:
        logger.error("No message object available for portfolio_command")
        return

    try:
        from servers import server_manager
        from utils.trading_data import get_lp_positions, get_perpetual_positions, get_active_orders, get_tokens_for_networks

        # Get first enabled server
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            error_message = format_error_message("No enabled API servers. Edit servers.yml to enable a server.")
            await message.reply_text(error_message, parse_mode="MarkdownV2")
            return

        # Use per-chat default server, falling back to global default
        default_server = server_manager.get_default_server_for_chat(chat_id)
        if default_server and default_server in enabled_servers:
            server_name = default_server
        else:
            server_name = enabled_servers[0]

        # Send initial loading message immediately
        text_msg = await message.reply_text(
            f"💼 *Portfolio Details* \\| _Server: {escape_markdown_v2(server_name)} ⏳_\n\n"
            f"_Loading\\.\\.\\._",
            parse_mode="MarkdownV2"
        )

        client = await server_manager.get_client(server_name)

        # Check server status
        server_status_info = await server_manager.check_server_status(server_name)
        server_status = server_status_info.get("status", "online")

        # Get portfolio config
        config = get_portfolio_prefs(context.user_data)
        days = config.get("days", 3)
        start_time = _calculate_start_time(days)
        pnl_start_time = _calculate_start_time(30)
        graph_interval = _get_optimal_interval(days)

        # ========================================
        # START ALL FETCHES IN PARALLEL
        # ========================================
        balances_task = asyncio.create_task(client.portfolio.get_state())
        perp_task = asyncio.create_task(get_perpetual_positions(client))
        lp_task = asyncio.create_task(get_lp_positions(client))
        orders_task = asyncio.create_task(get_active_orders(client))
        history_task = asyncio.create_task(client.portfolio.get_history(start_time=start_time, limit=100, interval=graph_interval))
        pnl_history_task = asyncio.create_task(client.portfolio.get_history(start_time=pnl_start_time, limit=100, interval="1d"))
        token_dist_task = asyncio.create_task(client.portfolio.get_distribution())
        accounts_dist_task = asyncio.create_task(client.portfolio.get_accounts_distribution())

        # Initialize data holders
        balances = None
        perp_positions = {"positions": [], "total": 0}
        lp_positions = {"positions": [], "total": 0}
        active_orders = {"orders": [], "total": 0}
        pnl_indicators = None
        changes_24h = None
        current_value = 0.0
        token_cache = {}  # Will be populated with Gateway tokens

        # Helper to update UI
        async def update_ui(loading_text: str = None):
            nonlocal current_value
            # Recalculate current value
            if balances:
                current_value = 0.0
                for account_data in balances.values():
                    for connector_balances in account_data.values():
                        if connector_balances:
                            for balance in connector_balances:
                                value = balance.get("value", 0)
                                if value > 0:
                                    current_value += value

            overview_data = {
                'balances': balances,
                'perp_positions': perp_positions,
                'lp_positions': lp_positions,
                'active_orders': active_orders,
            }
            message = format_portfolio_overview(
                overview_data,
                server_name=server_name,
                server_status=server_status,
                pnl_indicators=pnl_indicators,
                changes_24h=changes_24h,
                token_cache=token_cache
            )
            if loading_text:
                message += f"\n_{escape_markdown_v2(loading_text)}_"
            try:
                await text_msg.edit_text(message, parse_mode="MarkdownV2")
            except Exception:
                pass

        # ========================================
        # WAIT FOR BALANCES FIRST (usually fast)
        # ========================================
        try:
            balances = await balances_task
            # Filter balances by enabled networks from wallet preferences
            enabled_networks = get_all_enabled_networks(context.user_data)
            if enabled_networks:
                logger.info(f"Filtering portfolio by enabled networks: {enabled_networks}")
                balances = _filter_balances_by_networks(balances, enabled_networks)
            await update_ui("Loading positions & 24h data...")
        except Exception as e:
            logger.error(f"Failed to fetch balances: {e}")

        # ========================================
        # WAIT FOR POSITIONS AND 24H DATA IN PARALLEL
        # ========================================
        # Gather remaining fast tasks
        try:
            results = await asyncio.gather(
                perp_task, lp_task, orders_task, pnl_history_task,
                return_exceptions=True
            )

            if not isinstance(results[0], Exception):
                perp_positions = results[0]
            if not isinstance(results[1], Exception):
                lp_positions = results[1]
                # Populate token_cache from LP positions networks
                lp_networks = list(set(
                    pos.get('network', 'solana-mainnet-beta')
                    for pos in lp_positions.get('positions', [])
                ))
                if lp_networks:
                    try:
                        token_cache = await get_tokens_for_networks(client, lp_networks)
                    except Exception as e:
                        logger.debug(f"Failed to fetch tokens for LP networks: {e}")

            if not isinstance(results[2], Exception):
                active_orders = results[2]

            # Calculate 24h changes if we have history
            pnl_history = results[3] if not isinstance(results[3], Exception) else None
            if pnl_history and balances:
                pnl_indicators = _calculate_pnl_indicators(pnl_history, current_value)
                changes_24h = _calculate_24h_changes(pnl_history, balances)

            await update_ui("Generating graphs...")
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")

        # ========================================
        # WAIT FOR GRAPH DATA
        # ========================================
        history = None
        token_distribution = None
        accounts_distribution = None

        try:
            graph_results = await asyncio.gather(
                history_task, token_dist_task, accounts_dist_task,
                return_exceptions=True
            )
            history = graph_results[0] if not isinstance(graph_results[0], Exception) else None
            token_distribution = graph_results[1] if not isinstance(graph_results[1], Exception) else None
            accounts_distribution = graph_results[2] if not isinstance(graph_results[2], Exception) else None
        except Exception as e:
            logger.error(f"Error fetching graph data: {e}")

        # Final UI update (no loading text)
        await update_ui()

        # Send "upload_photo" status
        await message.reply_chat_action("upload_photo")

        # Generate the comprehensive dashboard
        dashboard_bytes = generate_portfolio_dashboard(
            history_data=history,
            token_distribution_data=token_distribution,
            accounts_distribution_data=accounts_distribution
        )

        # Create settings button
        keyboard = [[
            InlineKeyboardButton(f"⚙️ Settings ({days}d)", callback_data="portfolio:settings")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Send the dashboard image with buttons
        photo_msg = await message.reply_photo(
            photo=dashboard_bytes,
            caption=f"📊 Portfolio Dashboard - {server_name}",
            reply_markup=reply_markup
        )

        # Store message IDs and data for later updates
        context.user_data["portfolio_text_message_id"] = text_msg.message_id
        context.user_data["portfolio_photo_message_id"] = photo_msg.message_id
        context.user_data["portfolio_chat_id"] = message.chat_id
        context.user_data["portfolio_graph_interval"] = graph_interval
        context.user_data["portfolio_server_name"] = server_name
        context.user_data["portfolio_server_status"] = server_status
        context.user_data["portfolio_current_value"] = current_value

    except Exception as e:
        logger.error(f"Error fetching portfolio: {e}", exc_info=True)
        error_message = format_error_message(f"Failed to fetch portfolio: {str(e)}")
        await message.reply_text(error_message, parse_mode="MarkdownV2")


# ============================================
# PORTFOLIO SETTINGS CALLBACK HANDLERS
# ============================================

@restricted
async def portfolio_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button callbacks for portfolio operations"""
    query = update.callback_query
    await query.answer()

    logger.info(f"Portfolio callback received: {query.data}")

    try:
        callback_parts = query.data.split(":", 1)
        action = callback_parts[1] if len(callback_parts) > 1 else query.data

        logger.info(f"Portfolio action: {action}")

        if action == "settings":
            await show_portfolio_settings(update, context)
        elif action.startswith("set_days:"):
            days = int(action.split(":")[1])
            set_portfolio_days(context.user_data, days)
            # Calculate the new optimal interval for display
            new_interval = _get_optimal_interval(days)
            await show_portfolio_settings(update, context, message=f"Days set to {days} (interval: {new_interval})")
        elif action == "close":
            # Close settings menu and refresh dashboard with new settings
            try:
                await query.message.delete()
            except Exception:
                pass
            await refresh_portfolio_dashboard(update, context)
        else:
            logger.warning(f"Unknown portfolio action: {action}")

    except Exception as e:
        logger.error(f"Error in portfolio callback handler: {e}", exc_info=True)
        error_message = format_error_message(f"Operation failed: {str(e)}")
        try:
            await query.message.reply_text(error_message, parse_mode="MarkdownV2")
        except Exception as e2:
            logger.error(f"Failed to send error message: {e2}")


async def refresh_portfolio_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh both the text message and photo with new settings"""
    query = update.callback_query
    bot = query.get_bot()

    chat_id = context.user_data.get("portfolio_chat_id")
    text_message_id = context.user_data.get("portfolio_text_message_id")
    photo_message_id = context.user_data.get("portfolio_photo_message_id")

    if not chat_id or not photo_message_id:
        logger.warning("Missing message IDs for refresh")
        return

    try:
        from servers import server_manager
        from utils.trading_data import get_tokens_for_networks

        # Use per-chat default server from server_manager
        servers = server_manager.list_servers()
        enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

        if not enabled_servers:
            return

        default_server = server_manager.get_default_server_for_chat(chat_id)
        if default_server and default_server in enabled_servers:
            server_name = default_server
        else:
            server_name = enabled_servers[0]

        # Update caption to show "Updating..." status
        try:
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=photo_message_id,
                caption="🔄 Updating graph..."
            )
        except Exception as e:
            logger.warning(f"Failed to update caption to 'Updating': {e}")

        client = await server_manager.get_client(server_name)
        server_status_info = await server_manager.check_server_status(server_name)
        server_status = server_status_info.get("status", "online")

        # Get current config (only days, interval is auto-calculated)
        config = get_portfolio_prefs(context.user_data)
        days = config.get("days", 3)

        # Fetch all data (interval is calculated based on days)
        overview_data, history, token_distribution, accounts_distribution, pnl_history, graph_interval = await _fetch_dashboard_data(
            client, days
        )

        # Filter balances by enabled networks from wallet preferences
        enabled_networks = get_all_enabled_networks(context.user_data)
        if enabled_networks and overview_data and overview_data.get('balances'):
            logger.info(f"Filtering portfolio refresh by enabled networks: {enabled_networks}")
            overview_data['balances'] = _filter_balances_by_networks(overview_data['balances'], enabled_networks)

        # Calculate current portfolio value for PNL
        current_value = 0.0
        if overview_data and overview_data.get('balances'):
            for account_data in overview_data['balances'].values():
                for connector_balances in account_data.values():
                    if connector_balances:
                        for balance in connector_balances:
                            value = balance.get("value", 0)
                            if value > 0:
                                current_value += value

        # Calculate PNL indicators and 24h changes
        pnl_indicators = _calculate_pnl_indicators(pnl_history, current_value)
        changes_24h = _calculate_24h_changes(pnl_history, overview_data.get('balances', {})) if pnl_history else None

        # Fetch tokens for LP positions
        token_cache = {}
        lp_positions = overview_data.get('lp_positions', {}) if overview_data else {}
        if lp_positions and lp_positions.get('positions'):
            lp_networks = list(set(
                pos.get('network', 'solana-mainnet-beta')
                for pos in lp_positions.get('positions', [])
            ))
            if lp_networks:
                try:
                    token_cache = await get_tokens_for_networks(client, lp_networks)
                except Exception as e:
                    logger.debug(f"Failed to fetch tokens for LP networks: {e}")

        # Update text message if we have it
        if text_message_id:
            message = format_portfolio_overview(
                overview_data,
                server_name=server_name,
                server_status=server_status,
                pnl_indicators=pnl_indicators,
                changes_24h=changes_24h,
                token_cache=token_cache
            )
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=text_message_id,
                    text=message,
                    parse_mode="MarkdownV2"
                )
            except Exception as e:
                logger.warning(f"Failed to update text message: {e}")

        # Generate new dashboard
        dashboard_bytes = generate_portfolio_dashboard(
            history_data=history,
            token_distribution_data=token_distribution,
            accounts_distribution_data=accounts_distribution
        )

        # Create settings button
        keyboard = [[
            InlineKeyboardButton(f"⚙️ Settings ({days}d)", callback_data="portfolio:settings")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Update photo with new image
        from telegram import InputMediaPhoto
        await bot.edit_message_media(
            chat_id=chat_id,
            message_id=photo_message_id,
            media=InputMediaPhoto(
                media=dashboard_bytes,
                caption=f"📊 Portfolio Dashboard - {server_name}"
            ),
            reply_markup=reply_markup
        )

        # Store data for callbacks
        context.user_data["portfolio_graph_interval"] = graph_interval
        context.user_data["portfolio_server_name"] = server_name
        context.user_data["portfolio_server_status"] = server_status
        context.user_data["portfolio_current_value"] = current_value

    except Exception as e:
        logger.error(f"Failed to refresh portfolio dashboard: {e}", exc_info=True)


async def show_portfolio_settings(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str = None) -> None:
    """Display portfolio settings menu"""
    query = update.callback_query

    config = get_portfolio_prefs(context.user_data)
    current_days = config.get("days", 3)
    # Get the auto-calculated interval from context or calculate it
    current_interval = context.user_data.get("portfolio_graph_interval", _get_optimal_interval(current_days))

    # Build settings message
    settings_text = "⚙️ *Portfolio Graph Settings*\n\n"
    settings_text += f"📅 *Days:* `{current_days}`\n"
    settings_text += f"⏱️ *Interval:* `{current_interval}` \\(auto\\)\n"

    if message:
        settings_text += f"\n_{escape_markdown_v2(message)}_"

    # Build keyboard with days options only (interval is auto-calculated)
    days_buttons = []
    for days in PORTFOLIO_DAYS_OPTIONS:
        label = f"{'✓ ' if days == current_days else ''}{days}d"
        days_buttons.append(InlineKeyboardButton(label, callback_data=f"portfolio:set_days:{days}"))

    keyboard = [
        days_buttons,
        [
            InlineKeyboardButton("✅ Apply & Close", callback_data="portfolio:close")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Check if message has text (settings menu) or photo (dashboard image)
    if query.message.text:
        # Edit existing text message
        await query.edit_message_text(
            settings_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    else:
        # Message is a photo, send new text message for settings
        await query.message.reply_text(
            settings_text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )


def get_portfolio_callback_handler():
    """Returns the callback query handler for portfolio operations"""
    return CallbackQueryHandler(portfolio_callback_handler, pattern="^portfolio:")
