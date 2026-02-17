"""
Archived Bots Chart Generation

Provides:
- Timeline chart (Gantt-style) showing all bots with PnL-colored bars
- Performance chart for individual bots with cumulative PnL
- PnL calculation from trade data (OPEN/CLOSE positions)
"""

import io
import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Reuse the dark theme from visualizations
DARK_THEME = {
    "bgcolor": "#0a0e14",
    "paper_bgcolor": "#0a0e14",
    "plot_bgcolor": "#131720",
    "font_color": "#e6edf3",
    "font_family": "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif",
    "grid_color": "#21262d",
    "axis_color": "#8b949e",
    "up_color": "#10b981",  # Green for profit
    "down_color": "#ef4444",  # Red for loss
    "neutral_color": "#6b7280",  # Gray for zero
}


def _parse_timestamp(ts) -> Optional[datetime]:
    """Parse timestamp from various formats including milliseconds."""
    if ts is None:
        return None

    try:
        # Handle millisecond timestamp (integer or float)
        if isinstance(ts, (int, float)):
            # If timestamp > 1e12, it's milliseconds
            if ts > 1e12:
                ts = ts / 1000
            return datetime.fromtimestamp(ts)

        if isinstance(ts, datetime):
            return ts

        if hasattr(ts, "to_pydatetime"):  # pandas Timestamp
            return ts.to_pydatetime()

        if isinstance(ts, str) and ts:
            # Try parsing as ISO format
            if "T" in ts:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            else:
                return datetime.fromisoformat(ts)
    except Exception as e:
        logger.debug(f"Failed to parse timestamp {ts}: {e}")

    return None


def _format_pnl(pnl: float) -> str:
    """Format PnL for display on chart."""
    if pnl >= 0:
        return f"+${pnl:,.2f}"
    else:
        return f"-${abs(pnl):,.2f}"


def _get_pnl_color(pnl: float) -> str:
    """Get color based on PnL value."""
    if pnl > 0:
        return DARK_THEME["up_color"]
    elif pnl < 0:
        return DARK_THEME["down_color"]
    return DARK_THEME["neutral_color"]


def _extract_bot_name(db_path: str) -> str:
    """Extract readable bot name from database path."""
    # db_path: "bots/archived/trend_follower_grid-20251015-155015/data/trend_follower_grid-20251015-155015.sqlite"
    name = os.path.basename(db_path)
    if name.endswith(".sqlite"):
        name = name[:-7]
    elif name.endswith(".db"):
        name = name[:-3]
    return name


def calculate_pnl_from_trades(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate realized PnL from a list of trades using position tracking.

    Supports two modes:
    1. Perpetual futures: Uses OPEN/CLOSE position tracking
    2. Spot/Market Making (NIL positions): Uses average cost basis inventory tracking

    Args:
        trades: List of trade dicts with timestamp, trading_pair, trade_type,
                position, price, amount, trade_fee_in_quote

    Returns:
        Dict with:
        - total_pnl: Total realized PnL
        - total_fees: Total fees paid
        - pnl_by_pair: Dict mapping trading_pair to PnL
        - cumulative_pnl: List of (timestamp, pnl) for charting
        - total_volume: Total traded volume in quote
    """
    if not trades:
        return {
            "total_pnl": 0,
            "total_fees": 0,
            "pnl_by_pair": {},
            "cumulative_pnl": [],
            "total_volume": 0,
        }

    # Detect if this is OPEN/CLOSE mode or NIL mode (market making)
    position_types = set(t.get("position", "").upper() for t in trades)
    has_open_close = "OPEN" in position_types or "CLOSE" in position_types
    is_nil_mode = "NIL" in position_types and not has_open_close

    if is_nil_mode:
        return _calculate_pnl_average_cost(trades)
    else:
        return _calculate_pnl_open_close(trades)


def _calculate_pnl_average_cost(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate PnL using average cost basis for spot/market making trades.

    This handles NIL position trades where:
    - BUY adds to inventory at that price
    - SELL realizes PnL based on weighted average cost of inventory
    """
    # Track inventory per trading pair using average cost
    # inventory = {amount: float, total_cost: float}
    inventory: Dict[str, Dict[str, float]] = {}

    pnl_by_pair: Dict[str, float] = defaultdict(float)
    cumulative_pnl: List[Dict[str, Any]] = []
    running_pnl = 0.0
    total_fees = 0.0
    total_volume = 0.0

    # Debug counters
    buy_count = 0
    sell_count = 0
    realized_trades = 0

    # Sort trades by timestamp
    sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", 0))

    for trade in sorted_trades:
        pair = trade.get("trading_pair", "Unknown")
        amount = float(trade.get("amount", 0))
        price = float(trade.get("price", 0))
        trade_type = trade.get("trade_type", "").upper()
        fee = float(trade.get("trade_fee_in_quote", 0))
        timestamp = trade.get("timestamp", 0)

        total_fees += fee
        total_volume += amount * price

        # Parse timestamp for cumulative chart
        ts = _parse_timestamp(timestamp)

        # Initialize inventory for this pair if needed
        if pair not in inventory:
            inventory[pair] = {"amount": 0.0, "total_cost": 0.0}

        inv = inventory[pair]

        if trade_type == "BUY":
            buy_count += 1
            # Add to inventory at this price
            inv["amount"] += amount
            inv["total_cost"] += amount * price

        elif trade_type == "SELL":
            sell_count += 1
            # Realize PnL if we have inventory
            if inv["amount"] > 0:
                realized_trades += 1
                # Calculate average cost of inventory
                avg_cost = inv["total_cost"] / inv["amount"] if inv["amount"] > 0 else 0

                # Determine how much we can actually sell from inventory
                sell_amount = min(amount, inv["amount"])

                # PnL = (sell_price - avg_cost) * amount - fee
                pnl = (price - avg_cost) * sell_amount - fee

                pnl_by_pair[pair] += pnl
                running_pnl += pnl

                # Reduce inventory
                if sell_amount >= inv["amount"]:
                    # Fully depleted
                    inv["amount"] = 0.0
                    inv["total_cost"] = 0.0
                else:
                    # Partially depleted - reduce proportionally
                    ratio = sell_amount / inv["amount"]
                    inv["amount"] -= sell_amount
                    inv["total_cost"] -= inv["total_cost"] * ratio
            else:
                # Short selling (no inventory) - track as negative PnL for now
                # This means we're selling something we don't have (going short)
                # For simplicity, just count fees
                running_pnl -= fee

        # Record cumulative PnL point for charting
        if ts:
            cumulative_pnl.append(
                {
                    "timestamp": ts,
                    "pnl": running_pnl,
                    "pair": pair,
                }
            )

    logger.info(
        f"PnL calculation (avg cost): {len(trades)} trades, {buy_count} BUY, {sell_count} SELL, "
        f"{realized_trades} realized, total_pnl=${running_pnl:.4f}"
    )

    return {
        "total_pnl": running_pnl,
        "total_fees": total_fees,
        "pnl_by_pair": dict(pnl_by_pair),
        "cumulative_pnl": cumulative_pnl,
        "total_volume": total_volume,
    }


def _calculate_pnl_open_close(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calculate PnL using OPEN/CLOSE position tracking for perpetual futures.

    For perpetual futures:
    - OPEN trades establish positions (long or short)
    - CLOSE trades realize PnL
    """
    # Track positions per trading pair
    # position = {amount: float, total_cost: float, direction: int (1=long, -1=short)}
    positions: Dict[str, Dict[str, Any]] = {}

    pnl_by_pair: Dict[str, float] = defaultdict(float)
    cumulative_pnl: List[Dict[str, Any]] = []
    running_pnl = 0.0
    total_fees = 0.0
    total_volume = 0.0

    # Debug counters
    open_count = 0
    close_count = 0
    close_with_position = 0

    # Sort trades by timestamp
    sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", 0))

    for trade in sorted_trades:
        pair = trade.get("trading_pair", "Unknown")
        amount = float(trade.get("amount", 0))
        price = float(trade.get("price", 0))
        trade_type = trade.get("trade_type", "").upper()  # BUY or SELL
        position_action = trade.get("position", "").upper()  # OPEN or CLOSE
        fee = float(trade.get("trade_fee_in_quote", 0))
        timestamp = trade.get("timestamp", 0)

        total_fees += fee
        total_volume += amount * price

        # Parse timestamp for cumulative chart
        ts = _parse_timestamp(timestamp)

        if position_action == "OPEN":
            open_count += 1
            # Opening a new position or adding to existing
            if pair not in positions:
                positions[pair] = {"amount": 0, "total_cost": 0, "direction": 0}

            pos = positions[pair]

            if trade_type == "BUY":
                # Opening/adding to long position
                pos["amount"] += amount
                pos["total_cost"] += price * amount
                pos["direction"] = 1
            else:  # SELL
                # Opening/adding to short position
                pos["amount"] += amount
                pos["total_cost"] += price * amount
                pos["direction"] = -1

        elif position_action == "CLOSE":
            close_count += 1
            # Closing a position - realize PnL
            pos = positions.get(pair)

            if pos and pos["amount"] > 0:
                close_with_position += 1
                # Calculate average entry price
                avg_entry = pos["total_cost"] / pos["amount"]

                if trade_type == "SELL":
                    # Closing long: PnL = (exit - entry) * amount
                    pnl = (price - avg_entry) * amount
                else:  # BUY
                    # Closing short: PnL = (entry - exit) * amount
                    pnl = (avg_entry - price) * amount

                # Subtract fee from PnL
                pnl -= fee

                pnl_by_pair[pair] += pnl
                running_pnl += pnl

                # Update position
                if amount >= pos["amount"]:
                    # Fully closed
                    del positions[pair]
                else:
                    # Partially closed
                    close_ratio = amount / pos["amount"]
                    pos["amount"] -= amount
                    pos["total_cost"] -= pos["total_cost"] * close_ratio

        # Record cumulative PnL point for charting
        if ts:
            cumulative_pnl.append(
                {
                    "timestamp": ts,
                    "pnl": running_pnl,
                    "pair": pair,
                }
            )

    logger.info(
        f"PnL calculation (open/close): {len(trades)} trades, {open_count} OPEN, {close_count} CLOSE, "
        f"{close_with_position} CLOSE with matching position, total_pnl=${running_pnl:.4f}"
    )

    return {
        "total_pnl": running_pnl,
        "total_fees": total_fees,
        "pnl_by_pair": dict(pnl_by_pair),
        "cumulative_pnl": cumulative_pnl,
        "total_volume": total_volume,
    }


def get_time_range_from_trades(
    trades: List[Dict[str, Any]],
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Extract start and end time from trades list."""
    if not trades:
        return None, None

    timestamps = []
    for trade in trades:
        ts = _parse_timestamp(trade.get("timestamp"))
        if ts:
            timestamps.append(ts)

    if not timestamps:
        return None, None

    return min(timestamps), max(timestamps)


def generate_timeline_chart(
    bots_data: List[Dict[str, Any]],
    width: int = 1100,
    height: int = 600,
) -> Optional[io.BytesIO]:
    """
    Generate a Gantt-style timeline chart showing all archived bots.

    Args:
        bots_data: List of dicts with 'db_path', 'summary', 'trades', 'pnl_data' for each bot
        width: Chart width in pixels
        height: Chart height in pixels

    Returns:
        BytesIO buffer with PNG image or None if failed
    """
    try:
        import plotly.graph_objects as go

        if not bots_data:
            return None

        # Process bot data
        processed = []
        for bot in bots_data:
            db_path = bot.get("db_path", "")
            summary = bot.get("summary", {})
            trades = bot.get("trades", [])
            pnl_data = bot.get("pnl_data", {})

            # Get bot name from summary or db_path
            bot_name = summary.get("bot_name") or _extract_bot_name(db_path)

            # Get time range from trades
            start_time, end_time = get_time_range_from_trades(trades)

            # Get PnL - prefer pre-calculated
            total_pnl = pnl_data.get("total_pnl", 0) if pnl_data else 0

            if start_time and end_time:
                # Remove timezone for consistency
                if start_time.tzinfo:
                    start_time = start_time.replace(tzinfo=None)
                if end_time.tzinfo:
                    end_time = end_time.replace(tzinfo=None)

                processed.append(
                    {
                        "name": bot_name,
                        "start": start_time,
                        "end": end_time,
                        "pnl": total_pnl,
                        "color": _get_pnl_color(total_pnl),
                        "trades": summary.get("total_trades", len(trades)),
                    }
                )

        if not processed:
            logger.warning("No bots with valid time data for timeline")
            return None

        # Sort by start time
        processed.sort(key=lambda x: x["start"])

        # Create figure
        fig = go.Figure()

        # Add bars for each bot
        for i, bot in enumerate(processed):
            # Calculate duration in hours for bar width
            duration = (bot["end"] - bot["start"]).total_seconds() / 3600

            # Create the bar using a horizontal bar chart approach
            fig.add_trace(
                go.Bar(
                    y=[bot["name"]],
                    x=[duration],
                    base=[bot["start"]],
                    orientation="h",
                    marker_color=bot["color"],
                    marker_line_width=0,
                    text=f'{_format_pnl(bot["pnl"])}',
                    textposition="inside",
                    textfont=dict(color="white", size=11),
                    hovertemplate=(
                        f"<b>{bot['name']}</b><br>"
                        f"Start: {bot['start'].strftime('%b %d %H:%M')}<br>"
                        f"End: {bot['end'].strftime('%b %d %H:%M')}<br>"
                        f"PnL: {_format_pnl(bot['pnl'])}<br>"
                        f"Trades: {bot['trades']}<extra></extra>"
                    ),
                    showlegend=False,
                )
            )

        # Calculate totals for subtitle
        total_pnl = sum(b["pnl"] for b in processed)
        total_trades = sum(b["trades"] for b in processed)

        # Update layout
        fig.update_layout(
            title=dict(
                text=f"Archived Bots Timeline<br><sup>{len(processed)} bots | Total PnL: {_format_pnl(total_pnl)} | {total_trades} trades</sup>",
                x=0.5,
                font=dict(size=16, color=DARK_THEME["font_color"]),
            ),
            paper_bgcolor=DARK_THEME["paper_bgcolor"],
            plot_bgcolor=DARK_THEME["plot_bgcolor"],
            font=dict(
                family=DARK_THEME["font_family"],
                color=DARK_THEME["font_color"],
            ),
            xaxis=dict(
                type="date",
                showgrid=True,
                gridcolor=DARK_THEME["grid_color"],
                tickformat="%b %d",
                tickfont=dict(color=DARK_THEME["axis_color"]),
            ),
            yaxis=dict(
                showgrid=False,
                tickfont=dict(color=DARK_THEME["axis_color"]),
                autorange="reversed",  # First bot at top
            ),
            barmode="overlay",
            bargap=0.3,
            margin=dict(l=150, r=30, t=80, b=50),
            height=max(
                height, 100 + len(processed) * 40
            ),  # Dynamic height based on number of bots
            width=width,
        )

        # Export to PNG
        img_bytes = io.BytesIO()
        fig.write_image(img_bytes, format="png", scale=2)
        img_bytes.seek(0)

        return img_bytes

    except ImportError as e:
        logger.error(f"Missing required package for chart generation: {e}")
        return None
    except Exception as e:
        logger.error(f"Error generating timeline chart: {e}", exc_info=True)
        return None


def generate_performance_chart(
    summary: Dict[str, Any],
    performance: Optional[Dict[str, Any]],
    trades: List[Dict[str, Any]],
    db_path: str = "",
    width: int = 1100,
    height: int = 600,
) -> Optional[io.BytesIO]:
    """
    Generate a performance chart for a single bot showing cumulative PnL.

    Args:
        summary: BotSummary data
        performance: BotPerformanceResponse data (often None or incomplete)
        trades: List of TradeDetail objects
        db_path: Database path for extracting bot name
        width: Chart width in pixels
        height: Chart height in pixels

    Returns:
        BytesIO buffer with PNG image or None if failed
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        bot_name = summary.get("bot_name") or _extract_bot_name(db_path)

        # Calculate PnL from trades
        pnl_data = calculate_pnl_from_trades(trades)
        cumulative_pnl = pnl_data.get("cumulative_pnl", [])
        pnl_by_pair = pnl_data.get("pnl_by_pair", {})
        total_pnl = pnl_data.get("total_pnl", 0)
        total_fees = pnl_data.get("total_fees", 0)
        total_volume = pnl_data.get("total_volume", 0)

        # Create figure with subplots
        fig = make_subplots(
            rows=2,
            cols=2,
            specs=[
                [{"colspan": 2}, None],
                [{"type": "bar"}, {"type": "pie"}],
            ],
            subplot_titles=(
                "Cumulative PnL Over Time",
                "PnL by Trading Pair",
                "Trade Distribution",
            ),
            vertical_spacing=0.15,
            horizontal_spacing=0.1,
            row_heights=[0.6, 0.4],
        )

        # Panel 1: Cumulative PnL line chart
        if cumulative_pnl:
            timestamps = [p["timestamp"] for p in cumulative_pnl]
            pnl_values = [p["pnl"] for p in cumulative_pnl]

            line_color = _get_pnl_color(total_pnl)

            fig.add_trace(
                go.Scatter(
                    x=timestamps,
                    y=pnl_values,
                    mode="lines",
                    name="Net PnL",
                    line=dict(color=line_color, width=2),
                    fill="tozeroy",
                    fillcolor=f'rgba{tuple(list(int(line_color.lstrip("#")[i:i+2], 16) for i in (0, 2, 4)) + [0.15])}',
                    hovertemplate="<b>%{x|%b %d %H:%M}</b><br>PnL: $%{y:,.4f}<extra></extra>",
                ),
                row=1,
                col=1,
            )

            # Add zero line
            fig.add_hline(
                y=0,
                line_dash="dash",
                line_color=DARK_THEME["axis_color"],
                opacity=0.5,
                row=1,
                col=1,
            )

        # Panel 2: PnL by trading pair (bar chart)
        if pnl_by_pair:
            # Sort by absolute PnL
            sorted_pairs = sorted(
                pnl_by_pair.items(), key=lambda x: abs(x[1]), reverse=True
            )[:8]
            pairs = [p[0] for p in sorted_pairs]
            pnls = [p[1] for p in sorted_pairs]
            colors = [_get_pnl_color(p) for p in pnls]

            fig.add_trace(
                go.Bar(
                    x=pairs,
                    y=pnls,
                    marker_color=colors,
                    showlegend=False,
                    hovertemplate="<b>%{x}</b><br>PnL: $%{y:,.2f}<extra></extra>",
                ),
                row=2,
                col=1,
            )

        # Panel 3: Trade type distribution (pie chart)
        buy_count = sum(1 for t in trades if t.get("trade_type", "").upper() == "BUY")
        sell_count = len(trades) - buy_count

        if trades:
            fig.add_trace(
                go.Pie(
                    labels=["Buy", "Sell"],
                    values=[buy_count, sell_count],
                    marker_colors=[DARK_THEME["up_color"], DARK_THEME["down_color"]],
                    hole=0.4,
                    textinfo="label+percent",
                    textfont=dict(color="white"),
                    showlegend=False,
                ),
                row=2,
                col=2,
            )

        # Get time range
        start_time, end_time = get_time_range_from_trades(trades)
        time_info = ""
        if start_time and end_time:
            time_info = f" | {start_time.strftime('%b %d')} - {end_time.strftime('%b %d %H:%M')}"

        # Update layout
        fig.update_layout(
            title=dict(
                text=(
                    f"<b>{bot_name}</b><br>"
                    f"<sup>PnL: {_format_pnl(total_pnl)} | "
                    f"Volume: ${total_volume:,.0f} | "
                    f"Fees: ${total_fees:,.2f} | "
                    f"Trades: {len(trades)}{time_info}</sup>"
                ),
                x=0.5,
                font=dict(size=16, color=DARK_THEME["font_color"]),
            ),
            paper_bgcolor=DARK_THEME["paper_bgcolor"],
            plot_bgcolor=DARK_THEME["plot_bgcolor"],
            font=dict(family=DARK_THEME["font_family"], color=DARK_THEME["font_color"]),
            margin=dict(l=70, r=30, t=80, b=50),
            height=height,
            width=width,
        )

        # Update axes styling
        fig.update_xaxes(
            showgrid=True,
            gridcolor=DARK_THEME["grid_color"],
            tickfont=dict(color=DARK_THEME["axis_color"]),
        )
        fig.update_yaxes(
            showgrid=True,
            gridcolor=DARK_THEME["grid_color"],
            tickfont=dict(color=DARK_THEME["axis_color"]),
        )

        # Export to PNG
        img_bytes = io.BytesIO()
        fig.write_image(img_bytes, format="png", scale=2)
        img_bytes.seek(0)

        return img_bytes

    except ImportError as e:
        logger.error(f"Missing required package for chart generation: {e}")
        return None
    except Exception as e:
        logger.error(f"Error generating performance chart: {e}", exc_info=True)
        return None


def generate_report_chart(
    summary: Dict[str, Any],
    performance: Optional[Dict[str, Any]],
    trades: List[Dict[str, Any]],
    executors: List[Dict[str, Any]],
    db_path: str = "",
    width: int = 1400,
    height: int = 800,
) -> Optional[io.BytesIO]:
    """
    Generate a comprehensive report chart with multiple panels.

    Args:
        summary: BotSummary data
        performance: BotPerformanceResponse data
        trades: List of TradeDetail objects
        executors: List of ExecutorInfo objects
        db_path: Database path for extracting bot name
        width: Chart width in pixels
        height: Chart height in pixels

    Returns:
        BytesIO buffer with PNG image or None if failed
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        bot_name = summary.get("bot_name") or _extract_bot_name(db_path)

        # Calculate PnL from trades
        pnl_data = calculate_pnl_from_trades(trades)
        cumulative_pnl = pnl_data.get("cumulative_pnl", [])
        pnl_by_pair = pnl_data.get("pnl_by_pair", {})
        total_pnl = pnl_data.get("total_pnl", 0)
        total_fees = pnl_data.get("total_fees", 0)
        total_volume = pnl_data.get("total_volume", 0)

        # Create 2x2 subplot layout
        fig = make_subplots(
            rows=2,
            cols=2,
            specs=[
                [{"type": "scatter"}, {"type": "bar"}],
                [{"type": "bar", "colspan": 2}, None],
            ],
            subplot_titles=(
                "Cumulative PnL",
                "PnL by Trading Pair",
                "Volume by Market",
            ),
            vertical_spacing=0.15,
            horizontal_spacing=0.1,
        )

        # Panel 1: Cumulative PnL
        if cumulative_pnl:
            timestamps = [p["timestamp"] for p in cumulative_pnl]
            pnl_values = [p["pnl"] for p in cumulative_pnl]

            line_color = _get_pnl_color(total_pnl)

            fig.add_trace(
                go.Scatter(
                    x=timestamps,
                    y=pnl_values,
                    mode="lines",
                    line=dict(color=line_color, width=2),
                    fill="tozeroy",
                    fillcolor=f'rgba{tuple(list(int(line_color.lstrip("#")[i:i+2], 16) for i in (0, 2, 4)) + [0.15])}',
                    showlegend=False,
                ),
                row=1,
                col=1,
            )

        # Panel 2: PnL by trading pair
        if pnl_by_pair:
            sorted_pairs = sorted(
                pnl_by_pair.items(), key=lambda x: abs(x[1]), reverse=True
            )[:8]
            pairs = [p[0] for p in sorted_pairs]
            pnls = [p[1] for p in sorted_pairs]
            colors = [_get_pnl_color(p) for p in pnls]

            fig.add_trace(
                go.Bar(
                    x=pairs,
                    y=pnls,
                    marker_color=colors,
                    showlegend=False,
                ),
                row=1,
                col=2,
            )

        # Panel 3: Volume by market (bar chart)
        market_volume: Dict[str, float] = {}
        for trade in trades:
            pair = trade.get("trading_pair", "Unknown")
            amount = float(trade.get("amount", 0))
            price = float(trade.get("price", 0))
            volume = amount * price
            market_volume[pair] = market_volume.get(pair, 0) + volume

        if market_volume:
            sorted_markets = sorted(
                market_volume.items(), key=lambda x: x[1], reverse=True
            )[:10]
            markets = [m[0] for m in sorted_markets]
            volumes = [m[1] for m in sorted_markets]

            fig.add_trace(
                go.Bar(
                    x=markets,
                    y=volumes,
                    marker_color=DARK_THEME["up_color"],
                    showlegend=False,
                ),
                row=2,
                col=1,
            )

        # Update layout
        fig.update_layout(
            title=dict(
                text=(
                    f"<b>{bot_name} Report</b><br>"
                    f"<sup>PnL: {_format_pnl(total_pnl)} | Volume: ${total_volume:,.0f} | "
                    f"Fees: ${total_fees:,.2f} | Trades: {len(trades)}</sup>"
                ),
                x=0.5,
                font=dict(size=18, color=DARK_THEME["font_color"]),
            ),
            paper_bgcolor=DARK_THEME["paper_bgcolor"],
            plot_bgcolor=DARK_THEME["plot_bgcolor"],
            font=dict(
                family=DARK_THEME["font_family"],
                color=DARK_THEME["font_color"],
            ),
            margin=dict(l=60, r=30, t=100, b=50),
            height=height,
            width=width,
        )

        # Update subplot axes
        fig.update_xaxes(showgrid=True, gridcolor=DARK_THEME["grid_color"])
        fig.update_yaxes(showgrid=True, gridcolor=DARK_THEME["grid_color"])

        # Export to PNG
        img_bytes = io.BytesIO()
        fig.write_image(img_bytes, format="png", scale=2)
        img_bytes.seek(0)

        return img_bytes

    except ImportError as e:
        logger.error(f"Missing required package for chart generation: {e}")
        return None
    except Exception as e:
        logger.error(f"Error generating report chart: {e}", exc_info=True)
        return None
