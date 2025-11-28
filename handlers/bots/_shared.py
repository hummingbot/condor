"""
Shared utilities for Bots handlers

Contains:
- Server client helper
- Grid Strike controller defaults
- State management helpers
- Market data helpers for auto-pricing
- Candle chart generation
"""

import io
import logging
import time
from typing import Dict, Any, Optional, List, Tuple

import plotly.graph_objects as go

logger = logging.getLogger(__name__)

# Default cache TTL in seconds
DEFAULT_CACHE_TTL = 60

# Dark theme for charts (consistent with portfolio_graphs.py)
DARK_THEME = {
    "bgcolor": "#0a0e14",
    "paper_bgcolor": "#0a0e14",
    "plot_bgcolor": "#131720",
    "font_color": "#e6edf3",
    "font_family": "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif",
    "grid_color": "#21262d",
    "axis_color": "#8b949e",
    "up_color": "#10b981",    # Green for bullish
    "down_color": "#ef4444",  # Red for bearish
    "line_color": "#3b82f6",  # Blue for lines
}


# ============================================
# GRID STRIKE CONTROLLER DEFAULTS
# ============================================

GRID_STRIKE_DEFAULTS: Dict[str, Any] = {
    "controller_name": "grid_strike",
    "controller_type": "generic",
    "id": "",
    "connector_name": "",  # Will be set via selector
    "trading_pair": "",    # Will be set via input
    "side": 1,             # 1 = LONG, 2 = SHORT (Note: 2 not -1 for backend compatibility)
    "leverage": 1,
    "position_mode": "HEDGE",
    "total_amount_quote": 1000,
    "min_order_amount_quote": 6,
    "start_price": 0.0,    # Auto-calculated: current_price * 0.98 (for LONG)
    "end_price": 0.0,      # Auto-calculated: current_price * 1.02 (for LONG)
    "limit_price": 0.0,    # Auto-calculated: current_price * 0.97 for LONG, * 1.03 for SHORT
    "max_open_orders": 3,
    "max_orders_per_batch": 1,
    "min_spread_between_orders": 0.0002,
    "order_frequency": 3,
    "activation_bounds": 0.001,
    "keep_position": True,
    "triple_barrier_config": {
        "open_order_type": 3,
        "take_profit": 0.0001,
        "take_profit_order_type": 3,
    },
}

# Side value mapping
SIDE_LONG = 1
SIDE_SHORT = 2  # Backend expects 2 for SHORT (not -1)

# Field configurations for the form
GRID_STRIKE_FIELDS = {
    "id": {"label": "Config ID", "type": "str", "required": True, "hint": "Auto-generated, or custom"},
    "connector_name": {"label": "Connector", "type": "str", "required": True, "hint": "Select from available exchanges"},
    "trading_pair": {"label": "Trading Pair", "type": "str", "required": True, "hint": "e.g. SOL-FDUSD, BTC-USDT"},
    "side": {"label": "Side", "type": "int", "required": True, "hint": "LONG or SHORT"},
    "leverage": {"label": "Leverage", "type": "int", "required": True, "hint": "e.g. 1, 5, 10"},
    "total_amount_quote": {"label": "Total Amount (Quote)", "type": "float", "required": True, "hint": "e.g. 1000 USDT"},
    "start_price": {"label": "Start Price", "type": "float", "required": True, "hint": "Auto: -2% from current"},
    "end_price": {"label": "End Price", "type": "float", "required": True, "hint": "Auto: +2% from current"},
    "limit_price": {"label": "Limit Price", "type": "float", "required": True, "hint": "Auto: -3% LONG, +3% SHORT"},
    "max_open_orders": {"label": "Max Open Orders", "type": "int", "required": False, "hint": "Default: 3"},
    "min_spread_between_orders": {"label": "Min Spread", "type": "float", "required": False, "hint": "Default: 0.0002"},
    "take_profit": {"label": "Take Profit", "type": "float", "required": False, "hint": "Default: 0.0001"},
}

# Field display order for the menu
GRID_STRIKE_FIELD_ORDER = [
    "id", "connector_name", "trading_pair", "side", "leverage",
    "total_amount_quote", "start_price", "end_price", "limit_price",
    "max_open_orders", "min_spread_between_orders", "take_profit"
]


# ============================================
# SUPPORTED CONTROLLER TYPES
# ============================================

SUPPORTED_CONTROLLERS = {
    "grid_strike": {
        "name": "Grid Strike",
        "description": "Grid trading with stop-limit orders",
        "defaults": GRID_STRIKE_DEFAULTS,
        "fields": GRID_STRIKE_FIELDS,
        "field_order": GRID_STRIKE_FIELD_ORDER,
    },
}


# ============================================
# SERVER CLIENT HELPER
# ============================================

async def get_bots_client():
    """Get the API client for bot operations

    Returns:
        Client instance with bot_orchestration and controller endpoints

    Raises:
        ValueError: If no enabled servers available
    """
    from servers import server_manager

    servers = server_manager.list_servers()
    enabled_servers = [name for name, cfg in servers.items() if cfg.get("enabled", True)]

    if not enabled_servers:
        raise ValueError("No enabled API servers available")

    # Use default server if set, otherwise fall back to first enabled
    default_server = server_manager.get_default_server()
    if default_server and default_server in enabled_servers:
        server_name = default_server
    else:
        server_name = enabled_servers[0]

    logger.info(f"Bots using server: {server_name}")
    client = await server_manager.get_client(server_name)

    return client


# ============================================
# STATE MANAGEMENT
# ============================================

def clear_bots_state(context) -> None:
    """Clear all bots-related state from user context

    Args:
        context: Telegram context object
    """
    context.user_data.pop("bots_state", None)
    context.user_data.pop("controller_config_params", None)
    context.user_data.pop("controller_configs_list", None)
    context.user_data.pop("selected_controllers", None)
    context.user_data.pop("editing_controller_field", None)
    context.user_data.pop("deploy_params", None)
    context.user_data.pop("editing_deploy_field", None)


def get_controller_config(context) -> Dict[str, Any]:
    """Get the current controller config being edited

    Args:
        context: Telegram context object

    Returns:
        Controller config dict or empty dict
    """
    return context.user_data.get("controller_config_params", {})


def set_controller_config(context, config: Dict[str, Any]) -> None:
    """Set the current controller config

    Args:
        context: Telegram context object
        config: Controller config dict
    """
    context.user_data["controller_config_params"] = config


def init_new_controller_config(context, controller_type: str = "grid_strike") -> Dict[str, Any]:
    """Initialize a new controller config with defaults

    Args:
        context: Telegram context object
        controller_type: Type of controller (default: grid_strike)

    Returns:
        New controller config with defaults
    """
    controller_info = SUPPORTED_CONTROLLERS.get(controller_type, SUPPORTED_CONTROLLERS["grid_strike"])
    config = controller_info["defaults"].copy()
    # Deep copy triple_barrier_config
    if "triple_barrier_config" in config:
        config["triple_barrier_config"] = config["triple_barrier_config"].copy()
    context.user_data["controller_config_params"] = config
    return config


# ============================================
# FORMATTERS
# ============================================

def format_controller_config_summary(config: Dict[str, Any]) -> str:
    """Format a controller config for display

    Args:
        config: Controller config dict

    Returns:
        Formatted string (not escaped)
    """
    lines = []

    config_id = config.get("id", "Not set")
    controller_name = config.get("controller_name", "unknown")

    lines.append(f"ID: {config_id}")
    lines.append(f"Type: {controller_name}")
    lines.append(f"Connector: {config.get('connector_name', 'N/A')}")
    lines.append(f"Pair: {config.get('trading_pair', 'N/A')}")

    side = config.get("side", 1)
    side_str = "LONG" if side == SIDE_LONG else "SHORT"
    lines.append(f"Side: {side_str}")

    lines.append(f"Leverage: {config.get('leverage', 1)}x")
    lines.append(f"Total Amount: {config.get('total_amount_quote', 0)}")

    start = config.get("start_price", 0)
    end = config.get("end_price", 0)
    limit = config.get("limit_price", 0)
    lines.append(f"Grid: {start} - {end} (limit: {limit})")

    return "\n".join(lines)


def format_config_field_value(field_name: str, value: Any) -> str:
    """Format a field value for display

    Args:
        field_name: Name of the field
        value: Field value

    Returns:
        Formatted string
    """
    if field_name == "side":
        return "LONG" if value == SIDE_LONG else "SHORT"
    elif field_name == "keep_position":
        return "Yes" if value else "No"
    elif isinstance(value, float):
        if value == 0:
            return "Not set"
        return f"{value:g}"
    elif isinstance(value, dict):
        return "..."
    elif value == "" or value is None:
        return "Not set"
    return str(value)


# ============================================
# CACHE UTILITIES (borrowed from clob/_shared.py)
# ============================================

def get_cached(user_data: dict, key: str, ttl: int = DEFAULT_CACHE_TTL) -> Optional[Any]:
    """Get a cached value if still valid."""
    cache = user_data.get("_bots_cache", {})
    entry = cache.get(key)

    if entry is None:
        return None

    value, timestamp = entry
    if time.time() - timestamp > ttl:
        return None

    return value


def set_cached(user_data: dict, key: str, value: Any) -> None:
    """Store a value in the conversation cache."""
    if "_bots_cache" not in user_data:
        user_data["_bots_cache"] = {}

    user_data["_bots_cache"][key] = (value, time.time())


async def cached_call(
    user_data: dict,
    key: str,
    fetch_func,
    ttl: int = DEFAULT_CACHE_TTL,
    *args,
    **kwargs
) -> Any:
    """Execute an async function with caching."""
    cached = get_cached(user_data, key, ttl)
    if cached is not None:
        logger.debug(f"Bots cache hit for '{key}'")
        return cached

    logger.debug(f"Bots cache miss for '{key}', fetching...")
    result = await fetch_func(*args, **kwargs)
    set_cached(user_data, key, result)
    return result


# ============================================
# CEX CONNECTOR HELPERS
# ============================================

def is_cex_connector(connector_name: str) -> bool:
    """Check if a connector is a CEX (not DEX/on-chain)."""
    connector_lower = connector_name.lower()
    dex_prefixes = ["solana", "ethereum", "polygon", "arbitrum", "base", "optimism", "avalanche"]
    return not any(connector_lower.startswith(prefix) for prefix in dex_prefixes)


async def fetch_available_cex_connectors(client, account_name: str = "master_account") -> List[str]:
    """Fetch list of available CEX connectors with credentials configured."""
    try:
        configured_connectors = await client.accounts.list_account_credentials(account_name)
        return [c for c in configured_connectors if is_cex_connector(c)]
    except Exception as e:
        logger.error(f"Error fetching connectors: {e}", exc_info=True)
        return []


async def get_available_cex_connectors(
    user_data: dict,
    client,
    account_name: str = "master_account",
    ttl: int = 300
) -> List[str]:
    """Get available CEX connectors with caching."""
    cache_key = f"available_cex_connectors_{account_name}"
    return await cached_call(
        user_data,
        cache_key,
        fetch_available_cex_connectors,
        ttl,
        client,
        account_name
    )


# ============================================
# MARKET DATA HELPERS
# ============================================

async def fetch_current_price(client, connector_name: str, trading_pair: str) -> Optional[float]:
    """Fetch current price for a trading pair."""
    try:
        prices = await client.market_data.get_prices(
            connector_name=connector_name,
            trading_pairs=trading_pair
        )
        return prices.get("prices", {}).get(trading_pair)
    except Exception as e:
        logger.error(f"Error fetching price for {trading_pair}: {e}", exc_info=True)
        return None


async def fetch_candles(
    client,
    connector_name: str,
    trading_pair: str,
    interval: str = "1m",
    max_records: int = 100
) -> Optional[Dict[str, Any]]:
    """Fetch candles data for a trading pair."""
    try:
        candles = await client.market_data.get_candles(
            connector_name=connector_name,
            trading_pair=trading_pair,
            interval=interval,
            max_records=max_records
        )
        return candles
    except Exception as e:
        logger.error(f"Error fetching candles for {trading_pair}: {e}", exc_info=True)
        return None


def calculate_auto_prices(
    current_price: float,
    side: int,
    start_pct: float = 0.02,  # 2%
    end_pct: float = 0.02,    # 2%
    limit_pct: float = 0.03   # 3%
) -> Tuple[float, float, float]:
    """
    Calculate start, end, and limit prices based on current price and side.

    For LONG:
        - start_price: current_price - 2%
        - end_price: current_price + 2%
        - limit_price: current_price - 3%

    For SHORT:
        - start_price: current_price + 2%
        - end_price: current_price - 2%
        - limit_price: current_price + 3%

    Returns:
        Tuple of (start_price, end_price, limit_price)
    """
    if side == SIDE_LONG:
        start_price = current_price * (1 - start_pct)
        end_price = current_price * (1 + end_pct)
        limit_price = current_price * (1 - limit_pct)
    else:  # SHORT
        start_price = current_price * (1 + start_pct)
        end_price = current_price * (1 - end_pct)
        limit_price = current_price * (1 + limit_pct)

    return (
        round(start_price, 6),
        round(end_price, 6),
        round(limit_price, 6)
    )


def generate_config_id(
    connector_name: str,
    trading_pair: str,
    side: int,
    start_price: float,
    end_price: float,
    index: int = 1
) -> str:
    """
    Generate a unique config ID based on parameters.

    Format: grid_strike_{connector}_{pair}_{side}_{lower}_{upper}_{index}

    Example: grid_strike_binance_sol_fdusd_long_230_240_1
    """
    pair_clean = trading_pair.lower().replace("-", "_")
    side_str = "long" if side == SIDE_LONG else "short"

    # Format prices (remove decimals for cleaner ID)
    lower = int(min(start_price, end_price))
    upper = int(max(start_price, end_price))

    return f"grid_strike_{connector_name}_{pair_clean}_{side_str}_{lower}_{upper}_{index}"


# ============================================
# CANDLE CHART GENERATION
# ============================================

def generate_candles_chart(
    candles_data: Dict[str, Any],
    trading_pair: str,
    start_price: Optional[float] = None,
    end_price: Optional[float] = None,
    limit_price: Optional[float] = None,
    current_price: Optional[float] = None
) -> io.BytesIO:
    """
    Generate a candlestick chart with optional grid zone overlay.

    Args:
        candles_data: Candles data from API (with 'data' key containing list of candles)
        trading_pair: Trading pair name for title
        start_price: Grid start price line
        end_price: Grid end price line
        limit_price: Stop limit price line
        current_price: Current market price line

    Returns:
        BytesIO object containing the PNG image
    """
    data = candles_data.get("data", [])

    if not data:
        # Create empty chart with message
        fig = go.Figure()
        fig.add_annotation(
            text="No candle data available",
            xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(
                family=DARK_THEME["font_family"],
                size=16,
                color=DARK_THEME["font_color"]
            )
        )
    else:
        # Extract OHLCV data
        timestamps = []
        opens = []
        highs = []
        lows = []
        closes = []

        for candle in data:
            timestamps.append(candle.get("timestamp", ""))
            opens.append(candle.get("open", 0))
            highs.append(candle.get("high", 0))
            lows.append(candle.get("low", 0))
            closes.append(candle.get("close", 0))

        # Create candlestick chart
        fig = go.Figure(data=[go.Candlestick(
            x=timestamps,
            open=opens,
            high=highs,
            low=lows,
            close=closes,
            increasing_line_color=DARK_THEME["up_color"],
            decreasing_line_color=DARK_THEME["down_color"],
            increasing_fillcolor=DARK_THEME["up_color"],
            decreasing_fillcolor=DARK_THEME["down_color"],
            name="Price"
        )])

        # Add grid zone overlay (shaded area between start and end)
        if start_price and end_price:
            fig.add_hrect(
                y0=min(start_price, end_price),
                y1=max(start_price, end_price),
                fillcolor="rgba(59, 130, 246, 0.15)",  # Light blue
                line_width=0,
                annotation_text="Grid Zone",
                annotation_position="top left",
                annotation_font=dict(color=DARK_THEME["font_color"], size=11)
            )

            # Start price line
            fig.add_hline(
                y=start_price,
                line_dash="dash",
                line_color="#3b82f6",
                line_width=2,
                annotation_text=f"Start: {start_price:,.4f}",
                annotation_position="right",
                annotation_font=dict(color="#3b82f6", size=10)
            )

            # End price line
            fig.add_hline(
                y=end_price,
                line_dash="dash",
                line_color="#3b82f6",
                line_width=2,
                annotation_text=f"End: {end_price:,.4f}",
                annotation_position="right",
                annotation_font=dict(color="#3b82f6", size=10)
            )

        # Limit price line (stop loss)
        if limit_price:
            fig.add_hline(
                y=limit_price,
                line_dash="dot",
                line_color="#ef4444",
                line_width=2,
                annotation_text=f"Limit: {limit_price:,.4f}",
                annotation_position="right",
                annotation_font=dict(color="#ef4444", size=10)
            )

        # Current price line
        if current_price:
            fig.add_hline(
                y=current_price,
                line_dash="solid",
                line_color="#f59e0b",
                line_width=2,
                annotation_text=f"Current: {current_price:,.4f}",
                annotation_position="left",
                annotation_font=dict(color="#f59e0b", size=10)
            )

    # Update layout with dark theme
    fig.update_layout(
        title=dict(
            text=f"<b>{trading_pair}</b> - Grid Strike Setup",
            font=dict(
                family=DARK_THEME["font_family"],
                size=18,
                color=DARK_THEME["font_color"]
            ),
            x=0.5,
            xanchor="center"
        ),
        paper_bgcolor=DARK_THEME["paper_bgcolor"],
        plot_bgcolor=DARK_THEME["plot_bgcolor"],
        font=dict(
            family=DARK_THEME["font_family"],
            color=DARK_THEME["font_color"]
        ),
        xaxis=dict(
            gridcolor=DARK_THEME["grid_color"],
            color=DARK_THEME["axis_color"],
            rangeslider_visible=False,
            showgrid=True
        ),
        yaxis=dict(
            gridcolor=DARK_THEME["grid_color"],
            color=DARK_THEME["axis_color"],
            side="right",
            showgrid=True
        ),
        showlegend=False,
        width=900,
        height=500,
        margin=dict(l=10, r=120, t=50, b=30)
    )

    # Convert to PNG bytes
    img_bytes = io.BytesIO()
    fig.write_image(img_bytes, format='png', scale=2)
    img_bytes.seek(0)

    return img_bytes
