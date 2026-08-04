"""Run a backtest on a saved controller config and generate an interactive Plotly chart."""

CATEGORY = "Bot Analysis"

import io
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

# Close type enum mapping (hummingbot uses ints)
CLOSE_TYPE_MAP = {
    1: "TIME_LIMIT",
    2: "STOP_LOSS",
    3: "TAKE_PROFIT",
    4: "EXPIRED",
    5: "EARLY_STOP",
    6: "TRAILING_STOP",
    7: "INSUFFICIENT_BALANCE",
    8: "FAILED",
    9: "COMPLETED",
    10: "POSITION_HOLD",
    "TIME_LIMIT": "TIME_LIMIT",
    "STOP_LOSS": "STOP_LOSS",
    "TAKE_PROFIT": "TAKE_PROFIT",
    "EXPIRED": "EXPIRED",
    "EARLY_STOP": "EARLY_STOP",
    "TRAILING_STOP": "TRAILING_STOP",
    "INSUFFICIENT_BALANCE": "INSUFFICIENT_BALANCE",
    "FAILED": "FAILED",
    "COMPLETED": "COMPLETED",
    "POSITION_HOLD": "POSITION_HOLD",
}

# Side mapping
SIDE_MAP = {1: "BUY", 2: "SELL", "BUY": "BUY", "SELL": "SELL"}

# Colors matching hummingbot reference
COLORS = {
    "bg": "#0e1117",
    "plot_bg": "#0e1117",
    "grid": "#1e2530",
    "zero": "#2a3441",
    "text": "#e0e0e0",
    "text_dim": "#78909c",
    "green": "#26a69a",
    "red": "#ef5350",
    "blue": "#42a5f5",
    "yellow": "#ffd54f",
    "purple": "#ab47bc",
    "cyan": "#80cbc4",
    "orange": "#ff6d00",
    "white_dim": "#e0e0e0",
}

# Executor category definitions: (close_type, side) -> styling
EXECUTOR_CATEGORIES = {
    "Hold Buy": {"color": COLORS["blue"], "symbol": "circle", "dash": "dot"},
    "Hold Sell": {"color": COLORS["purple"], "symbol": "circle", "dash": "dot"},
    "Early Stop Buy": {"color": COLORS["white_dim"], "symbol": "x", "dash": "dash"},
    "Early Stop Sell": {"color": COLORS["white_dim"], "symbol": "x", "dash": "dash"},
    "TP Buy": {"color": COLORS["green"], "symbol": "triangle-up", "dash": None},
    "TP Sell": {"color": COLORS["red"], "symbol": "triangle-down", "dash": None},
    "SL Buy": {"color": COLORS["orange"], "symbol": "triangle-up", "dash": None},
    "SL Sell": {"color": COLORS["orange"], "symbol": "triangle-down", "dash": None},
    "Other Buy": {"color": COLORS["text_dim"], "symbol": "triangle-up", "dash": None},
    "Other Sell": {
        "color": COLORS["text_dim"],
        "symbol": "triangle-down",
        "dash": None,
    },
}


class Config(BaseModel):
    """Run backtest and generate interactive chart with entry/exit markers and PnL curve."""

    task_id: str = Field(
        default="",
        description="Render a saved backtest by task ID instead of running a new one",
    )
    config_name: str = Field(
        default="",
        description="Controller config",
        json_schema_extra={"widget": "select", "options_from": "controller_configs"},
    )
    start_date: str = Field(default="2025-04-22", description="Start date (YYYY-MM-DD)")
    end_date: str = Field(default="2025-04-23", description="End date (YYYY-MM-DD)")
    resolution: str = Field(
        default="1m", description="Candle resolution: 1m, 5m, 15m, 1h"
    )
    trade_cost: float = Field(
        default=0.0002, description="Trade cost as decimal (0.0002 = 0.02%)"
    )


def _parse_date(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _format_date(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def _load_saved_task(task_id: str) -> tuple[dict, "Config"] | str:
    """Load a stored backtest result, or return an error message.

    The returned Config mirrors the parameters the saved run was executed with,
    so titles and the summary describe that run rather than the form defaults.
    """
    from condor.backtest_store import get_backtest_store

    store = get_backtest_store()
    known = list(store._index.keys())

    if task_id not in known:
        matches = [t for t in known if t.startswith(task_id)]
        if not matches:
            saved = ", ".join(sorted(known)) or "none"
            return f"No saved backtest matching '{task_id}'. Saved: {saved}"
        if len(matches) > 1:
            return f"'{task_id}' is ambiguous: {', '.join(sorted(matches))}"
        task_id = matches[0]

    task = store.get_result(task_id)
    if not task or task.get("status") != "completed" or not task.get("result"):
        return f"Backtest '{task_id}' has no completed result to render"

    task_config = task.get("config") or {}
    controller_config = task_config.get("config") or {}
    return task["result"], Config(
        task_id=task_id,
        config_name=str(
            controller_config.get("id") or task_config.get("config_id") or task_id
        ),
        start_date=_format_date(task_config.get("start_time")),
        end_date=_format_date(task_config.get("end_time")),
        resolution=str(task_config.get("backtesting_resolution") or ""),
        trade_cost=float(task_config.get("trade_cost") or 0),
    )


def _close_type_label(ct) -> str:
    return CLOSE_TYPE_MAP.get(ct, str(ct))


def _side_label(s) -> str:
    return SIDE_MAP.get(s, str(s))


def _build_candle_df(processed_data: dict) -> pd.DataFrame:
    """Convert processed_data dict-of-dicts to a DataFrame with datetime index."""
    df = pd.DataFrame(processed_data)
    ts_col = "timestamp_bt" if "timestamp_bt" in df.columns else "timestamp"
    df["datetime"] = pd.to_datetime(df[ts_col].astype(float), unit="s", utc=True)
    for col in (
        "open_bt",
        "high_bt",
        "low_bt",
        "close_bt",
        "open",
        "high",
        "low",
        "close",
    ):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def _categorize_executor(ex: dict) -> str:
    """Map an executor dict to a category key like 'TP Buy', 'Hold Sell', etc."""
    config = ex.get("config", {})
    side_raw = ex.get("side", config.get("side", 0))
    side = _side_label(side_raw)
    side_label = "Buy" if side == "BUY" else "Sell"

    close_type = _close_type_label(ex.get("close_type", ""))
    if close_type == "POSITION_HOLD":
        return f"Hold {side_label}"
    elif close_type == "EARLY_STOP":
        return f"Early Stop {side_label}"
    elif close_type == "TAKE_PROFIT":
        return f"TP {side_label}"
    elif close_type == "STOP_LOSS":
        return f"SL {side_label}"
    else:
        return f"Other {side_label}"


def _get_executor_prices(ex: dict) -> tuple[float | None, float | None]:
    """Extract entry and exit prices following hummingbot reference logic."""
    config = ex.get("config", {})
    custom_info = ex.get("custom_info", {})

    # Entry price: prefer average position price, then config entry_price
    entry_price = custom_info.get("current_position_average_price")
    if entry_price is None:
        entry_price = config.get("entry_price")
    if entry_price is None:
        entry_price = custom_info.get("close_price")
    if entry_price is not None:
        entry_price = float(entry_price)

    # Exit price: for unfilled early stops use entry_price
    close_type = _close_type_label(ex.get("close_type", ""))
    filled = float(ex.get("filled_amount_quote", 0))
    if filled == 0 and close_type == "EARLY_STOP":
        exit_price = entry_price
    else:
        exit_price = custom_info.get("close_price")
        if exit_price is not None:
            exit_price = float(exit_price)
        else:
            exit_price = entry_price

    return entry_price, exit_price


def _add_executor_markers(fig, executors: list[dict], row=1, col=1):
    """Add entry/exit markers grouped by (close_type, side) category."""
    categories = {
        k: {"entries": [], "exits": [], "pnls": []} for k in EXECUTOR_CATEGORIES
    }

    for ex in executors:
        close_ts = ex.get("close_timestamp")
        if not close_ts:
            continue

        close_type = _close_type_label(ex.get("close_type", ""))
        filled = float(ex.get("filled_amount_quote", 0))

        # Skip unfilled executors except early stops
        if filled == 0 and close_type != "EARLY_STOP":
            continue

        entry_price, exit_price = _get_executor_prices(ex)
        if entry_price is None or exit_price is None:
            continue

        entry_time = pd.to_datetime(float(ex.get("timestamp", 0)), unit="s", utc=True)
        exit_time = pd.to_datetime(float(close_ts), unit="s", utc=True)
        pnl = float(ex.get("net_pnl_quote", 0))

        cat_key = _categorize_executor(ex)
        if cat_key not in categories:
            cat_key = "Other Buy"  # fallback
        categories[cat_key]["entries"].append((entry_time, entry_price))
        categories[cat_key]["exits"].append((exit_time, exit_price))
        categories[cat_key]["pnls"].append(pnl)

    for label, data in categories.items():
        if not data["entries"]:
            continue

        style = EXECUTOR_CATEGORIES[label]
        entry_x, entry_y = zip(*data["entries"])
        exit_x, exit_y = zip(*data["exits"])
        pnls = data["pnls"]

        # Entry-to-exit connection lines
        category_dash = style.get("dash")
        for i in range(len(entry_x)):
            is_profit = pnls[i] >= 0
            if category_dash:
                line_color = style["color"]
                line_dash = category_dash
                line_width = 1
            else:
                line_color = COLORS["green"] if is_profit else COLORS["red"]
                line_dash = "solid"
                line_width = 1.5
            fig.add_trace(
                go.Scatter(
                    x=[entry_x[i], exit_x[i]],
                    y=[float(entry_y[i]), float(exit_y[i])],
                    mode="lines",
                    line=dict(color=line_color, width=line_width, dash=line_dash),
                    showlegend=False,
                    hoverinfo="skip",
                ),
                row=row,
                col=col,
            )

        # Exit markers
        fig.add_trace(
            go.Scatter(
                x=list(exit_x),
                y=[float(p) for p in exit_y],
                mode="markers",
                marker=dict(color=style["color"], size=7, symbol=style["symbol"]),
                name=label,
            ),
            row=row,
            col=col,
        )


def _add_grid_visualization(fig, executors: list[dict], row=1, col=1):
    """Draw grid level lines and fill markers for grid executors."""
    grid_executors = [
        e for e in executors if e.get("custom_info", {}).get("grid_level_prices")
    ]
    if not grid_executors:
        return

    all_entry_x, all_entry_y, all_entry_text = [], [], []
    all_tp_x, all_tp_y, all_tp_text = [], [], []
    first_executor = True
    grid_side = "BUY"

    for idx, ex in enumerate(grid_executors):
        custom_info = ex.get("custom_info", {})
        level_prices = custom_info["grid_level_prices"]
        tp_prices = custom_info.get("grid_tp_prices", [])
        fill_events = custom_info.get("fill_events", [])
        grid_side = custom_info.get("grid_side", "BUY")

        start_dt = pd.to_datetime(float(ex.get("timestamp", 0)), unit="s", utc=True)
        close_ts = ex.get("close_timestamp") or ex.get("timestamp", 0)
        end_dt = pd.to_datetime(float(close_ts), unit="s", utc=True)
        exec_idx = idx + 1

        # Grid level lines
        for i, price in enumerate(level_prices):
            fig.add_trace(
                go.Scatter(
                    x=[start_dt, end_dt],
                    y=[price, price],
                    mode="lines",
                    line=dict(color="rgba(100,181,246,0.35)", width=1, dash="dash"),
                    showlegend=(first_executor and i == 0),
                    legendgroup="grid_level",
                    name="Grid Level",
                    hoverinfo="y",
                ),
                row=row,
                col=col,
            )

        # TP level lines
        for i, tp_price in enumerate(tp_prices):
            fig.add_trace(
                go.Scatter(
                    x=[start_dt, end_dt],
                    y=[tp_price, tp_price],
                    mode="lines",
                    line=dict(color="rgba(255,183,77,0.25)", width=1, dash="dot"),
                    showlegend=(first_executor and i == 0),
                    legendgroup="tp_level",
                    name="TP Level",
                    hoverinfo="y",
                ),
                row=row,
                col=col,
            )

        # Limit price line
        grid_limit_price = custom_info.get("grid_limit_price")
        if grid_limit_price is not None:
            fig.add_trace(
                go.Scatter(
                    x=[start_dt, end_dt],
                    y=[grid_limit_price, grid_limit_price],
                    mode="lines",
                    line=dict(color="rgba(239,83,80,0.7)", width=1.5, dash="dashdot"),
                    showlegend=first_executor,
                    legendgroup="limit_price",
                    name="Limit Price",
                    hoverinfo="y",
                ),
                row=row,
                col=col,
            )

        # Executor boundary
        y_min = min(level_prices) if level_prices else 0
        y_max = (
            max(tp_prices) if tp_prices else (max(level_prices) if level_prices else 0)
        )
        fig.add_trace(
            go.Scatter(
                x=[start_dt, start_dt],
                y=[y_min, y_max],
                mode="lines+text",
                line=dict(color="rgba(255,255,255,0.3)", width=1, dash="dot"),
                text=[f"#{exec_idx}", ""],
                textposition="top center",
                textfont=dict(size=9, color="rgba(255,255,255,0.6)"),
                showlegend=first_executor,
                legendgroup="exec_boundary",
                name="Executor Start",
                hovertext=f"Executor #{exec_idx} start",
                hoverinfo="text",
            ),
            row=row,
            col=col,
        )

        # Collect fill markers
        for ev in fill_events:
            dt = pd.to_datetime(ev["timestamp"], unit="s", utc=True)
            if ev["side"] == "entry":
                all_entry_x.append(dt)
                all_entry_y.append(ev["price"])
                all_entry_text.append(
                    f"E#{exec_idx} L{ev['level_idx']} entry ${ev['amount_quote']:.1f}"
                )
            else:
                all_tp_x.append(dt)
                all_tp_y.append(ev["price"])
                all_tp_text.append(
                    f"E#{exec_idx} L{ev['level_idx']} TP ${ev['amount_quote']:.1f}"
                )

        first_executor = False

    entry_symbol = "triangle-up" if grid_side == "BUY" else "triangle-down"
    tp_symbol = "triangle-down" if grid_side == "BUY" else "triangle-up"

    if all_entry_x:
        fig.add_trace(
            go.Scatter(
                x=all_entry_x,
                y=all_entry_y,
                mode="markers",
                marker=dict(
                    color=COLORS["green"],
                    size=8,
                    symbol=entry_symbol,
                    line=dict(width=1, color="#1b5e20"),
                ),
                name="Grid Entry Fill",
                legendgroup="grid_entry_fill",
                text=all_entry_text,
                hoverinfo="text+y",
            ),
            row=row,
            col=col,
        )

    if all_tp_x:
        fig.add_trace(
            go.Scatter(
                x=all_tp_x,
                y=all_tp_y,
                mode="markers",
                marker=dict(
                    color=COLORS["yellow"],
                    size=8,
                    symbol=tp_symbol,
                    line=dict(width=1, color="#f57f17"),
                ),
                name="Grid TP Fill",
                legendgroup="grid_tp_fill",
                text=all_tp_text,
                hoverinfo="text+y",
            ),
            row=row,
            col=col,
        )


def _add_cumulative_pnl(fig, pnl_ts: list[dict], row=2, col=1):
    """Add cumulative PnL panel matching hummingbot reference."""
    if not pnl_ts:
        fig.add_hline(y=0, line_dash="dot", line_color="#555", row=row, col=col)
        return

    pnl_df = pd.DataFrame(pnl_ts)
    pnl_df["dt"] = pd.to_datetime(pnl_df["timestamp"].astype(float), unit="s", utc=True)

    # Total PnL
    fig.add_trace(
        go.Scatter(
            x=pnl_df["dt"],
            y=pnl_df["total_pnl"],
            mode="lines",
            line=dict(color=COLORS["yellow"], width=2),
            fill="tozeroy",
            fillcolor="rgba(255,213,79,0.1)",
            name="Total PnL",
        ),
        row=row,
        col=col,
    )

    # Executor realized PnL
    if "executor_realized_pnl" in pnl_df.columns:
        fig.add_trace(
            go.Scatter(
                x=pnl_df["dt"],
                y=pnl_df["executor_realized_pnl"],
                mode="lines",
                line=dict(color=COLORS["green"], width=1.5, dash="dot"),
                name="Executor Realized PnL",
            ),
            row=row,
            col=col,
        )

    # Position realized PnL
    if "position_realized_pnl" in pnl_df.columns:
        fig.add_trace(
            go.Scatter(
                x=pnl_df["dt"],
                y=pnl_df["position_realized_pnl"],
                mode="lines",
                line=dict(color=COLORS["blue"], width=1.5, dash="dot"),
                name="Position Realized PnL",
            ),
            row=row,
            col=col,
        )

    # Position unrealized PnL
    if "position_unrealized_pnl" in pnl_df.columns:
        fig.add_trace(
            go.Scatter(
                x=pnl_df["dt"],
                y=pnl_df["position_unrealized_pnl"],
                mode="lines",
                line=dict(color=COLORS["purple"], width=1.5, dash="dot"),
                name="Position Unrealized PnL",
            ),
            row=row,
            col=col,
        )

    # Active executors (subtle filled area)
    if "active_executors" in pnl_df.columns:
        pnl_range = max(
            abs(pnl_df["total_pnl"].max()), abs(pnl_df["total_pnl"].min()), 1
        )
        max_active = max(pnl_df["active_executors"].max(), 1)
        scale = pnl_range * 0.3 / max_active
        fig.add_trace(
            go.Scatter(
                x=pnl_df["dt"],
                y=pnl_df["active_executors"] * scale,
                mode="lines",
                line=dict(color="rgba(255,255,255,0.2)", width=0),
                fill="tozeroy",
                fillcolor="rgba(255,255,255,0.07)",
                name="Active Executors",
                hovertemplate="Active: %{customdata}<extra></extra>",
                customdata=pnl_df["active_executors"],
            ),
            row=row,
            col=col,
        )

    # Cumulative volume on secondary y
    if "cumulative_volume" in pnl_df.columns:
        fig.add_trace(
            go.Scatter(
                x=pnl_df["dt"],
                y=pnl_df["cumulative_volume"],
                mode="lines",
                line=dict(color=COLORS["cyan"], width=1.5, dash="dashdot"),
                name="Cumulative Volume",
                hovertemplate="Volume: $%{y:,.0f}<extra></extra>",
            ),
            row=row,
            col=col,
            secondary_y=True,
        )

    fig.add_hline(y=0, line_dash="dot", line_color="#555", row=row, col=col)


def _add_position_held(fig, position_held_ts: list[dict], row=3, col=1):
    """Plot position held over time from tick-level timeseries."""
    if not position_held_ts:
        return

    ts_df = pd.DataFrame(position_held_ts)
    ts_df["dt"] = pd.to_datetime(ts_df["timestamp"].astype(float), unit="s", utc=True)

    # Long area
    if "long_amount" in ts_df.columns:
        fig.add_trace(
            go.Scatter(
                x=ts_df["dt"],
                y=ts_df["long_amount"],
                mode="lines",
                line=dict(color=COLORS["green"], width=0),
                fill="tozeroy",
                fillcolor="rgba(38,166,154,0.3)",
                name="Long Held",
            ),
            row=row,
            col=col,
        )

    # Short area (negative)
    if "short_amount" in ts_df.columns and ts_df["short_amount"].sum() > 0:
        fig.add_trace(
            go.Scatter(
                x=ts_df["dt"],
                y=-ts_df["short_amount"],
                mode="lines",
                line=dict(color=COLORS["red"], width=0),
                fill="tozeroy",
                fillcolor="rgba(239,83,80,0.3)",
                name="Short Held",
            ),
            row=row,
            col=col,
        )

    # Net position line
    if "net_amount" in ts_df.columns:
        fig.add_trace(
            go.Scatter(
                x=ts_df["dt"],
                y=ts_df["net_amount"],
                mode="lines",
                line=dict(color=COLORS["white_dim"], width=1.5),
                name="Net Position",
            ),
            row=row,
            col=col,
        )

    # Unrealized PnL
    if "unrealized_pnl" in ts_df.columns:
        fig.add_trace(
            go.Scatter(
                x=ts_df["dt"],
                y=ts_df["unrealized_pnl"],
                mode="lines",
                line=dict(color=COLORS["yellow"], width=1.5, dash="dot"),
                name="Unrealized PnL",
            ),
            row=row,
            col=col,
        )

    fig.add_hline(y=0, line_dash="dot", line_color="#555", row=row, col=col)


def generate_chart(
    candle_df: pd.DataFrame,
    executors: list[dict],
    pnl_ts: list[dict],
    results: dict,
    config: Config,
    position_held_ts: list[dict] | None = None,
) -> tuple[io.BytesIO, go.Figure]:
    """Generate a multi-panel backtest chart matching hummingbot reference."""
    has_pnl = bool(pnl_ts)
    has_position = bool(position_held_ts)

    n_rows = 1 + (1 if has_pnl else 0) + (1 if has_position else 0)
    if n_rows == 3:
        row_heights = [0.55, 0.2, 0.25]
    elif n_rows == 2:
        row_heights = [0.7, 0.3]
    else:
        row_heights = [1.0]

    subtitles = [""] * n_rows
    specs = [[{"secondary_y": True}] for _ in range(n_rows)]

    fig = make_subplots(
        rows=n_rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=row_heights,
        subplot_titles=subtitles,
        specs=specs,
    )

    # --- Row 1: Candlestick ---
    o_col = "open_bt" if "open_bt" in candle_df.columns else "open"
    h_col = "high_bt" if "high_bt" in candle_df.columns else "high"
    l_col = "low_bt" if "low_bt" in candle_df.columns else "low"
    c_col = "close_bt" if "close_bt" in candle_df.columns else "close"

    fig.add_trace(
        go.Candlestick(
            x=candle_df["datetime"],
            open=candle_df[o_col],
            high=candle_df[h_col],
            low=candle_df[l_col],
            close=candle_df[c_col],
            increasing_line_color=COLORS["green"],
            decreasing_line_color=COLORS["red"],
            increasing_fillcolor=COLORS["green"],
            decreasing_fillcolor=COLORS["red"],
            name="Price",
        ),
        row=1,
        col=1,
    )

    # Executor markers (category-based like reference)
    _add_executor_markers(fig, executors, row=1, col=1)

    # Grid visualization
    _add_grid_visualization(fig, executors, row=1, col=1)

    # --- Row 2: Cumulative PnL ---
    if has_pnl:
        _add_cumulative_pnl(fig, pnl_ts, row=2, col=1)

    # --- Row 3: Position held ---
    if has_position:
        pos_row = 3 if has_pnl else 2
        _add_position_held(fig, position_held_ts, row=pos_row, col=1)

    # --- Title ---
    net_pnl = results.get("net_pnl_quote", results.get("net_pnl", 0))
    net_pnl_pct = results.get("net_pnl", 0)
    total_volume = results.get("total_volume", 0)

    fig.update_layout(
        plot_bgcolor=COLORS["plot_bg"],
        paper_bgcolor=COLORS["bg"],
        font=dict(color=COLORS["text"], size=11),
        height=950,
        width=1400,
        margin=dict(l=60, r=30, t=120, b=40),
        hovermode="x unified",
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.06,
            xanchor="center",
            x=0.5,
            font=dict(size=10),
        ),
        title=dict(
            text=(
                f"{config.config_name} | "
                f"{config.start_date} to {config.end_date} @ {config.resolution} | "
                f"PnL: ${net_pnl:.2f} ({net_pnl_pct * 100:.2f}%) | "
                f"Volume: ${total_volume:,.0f}"
            ),
            font=dict(size=14),
            y=0.99,
            yanchor="top",
        ),
    )

    # Axis styling
    fig.update_xaxes(rangeslider_visible=False, row=1, col=1)
    for r in range(1, n_rows + 1):
        fig.update_xaxes(
            gridcolor=COLORS["grid"], zerolinecolor=COLORS["zero"], row=r, col=1
        )
        fig.update_yaxes(
            gridcolor=COLORS["grid"], zerolinecolor=COLORS["zero"], row=r, col=1
        )

    fig.update_yaxes(title_text="Price", row=1, col=1)
    if has_pnl:
        fig.update_yaxes(title_text="PnL ($)", row=2, col=1)
        fig.update_yaxes(
            title_text="Volume ($)", row=2, col=1, secondary_y=True, showgrid=False
        )
    if has_position:
        pos_row = 3 if has_pnl else 2
        fig.update_yaxes(title_text="Position ($)", row=pos_row, col=1)

    buf = io.BytesIO()
    fig.write_image(buf, format="png", scale=2)
    buf.seek(0)
    return buf, fig


def format_summary(results: dict, config: Config) -> str:
    """Format backtest results as text summary matching hummingbot reference."""
    net_pnl = results.get("net_pnl_quote", 0)
    net_pnl_pct = results.get("net_pnl", 0)
    total_fees = results.get("total_fees_quote", 0)
    unrealized_pnl = results.get("unrealized_pnl_quote", 0)
    position_realized_pnl = results.get("position_realized_pnl_quote", 0)
    max_dd = results.get("max_drawdown_usd", 0)
    max_dd_pct = results.get("max_drawdown_pct", 0)
    total_volume = results.get("total_volume", 0)
    sharpe = results.get("sharpe_ratio", 0)
    profit_factor = results.get("profit_factor", 0)
    total_executors = results.get("total_executors", 0)
    acc_long = results.get("accuracy_long", 0)
    acc_short = results.get("accuracy_short", 0)

    close_types = results.get("close_types", {})
    if not isinstance(close_types, dict):
        close_types = {}
    tp = close_types.get("TAKE_PROFIT", 0)
    sl = close_types.get("STOP_LOSS", 0)
    tl = close_types.get("TIME_LIMIT", 0)
    trailing = close_types.get("TRAILING_STOP", 0)
    early = close_types.get("EARLY_STOP", 0)
    pos_hold = close_types.get("POSITION_HOLD", 0)

    lines = [
        f"Backtest: {config.config_name}",
        f"Period: {config.start_date} to {config.end_date} @ {config.resolution}",
        f"Trade cost: {config.trade_cost:.4%}",
        "",
        f"Net PNL: ${net_pnl:.2f} ({net_pnl_pct * 100:.2f}%) | "
        f"Total Fees: ${total_fees:.2f} | "
        f"Unrealized PNL: ${unrealized_pnl:.2f} | "
        f"Position Realized PNL: ${position_realized_pnl:.2f} | "
        f"Max Drawdown: ${max_dd:.2f} ({max_dd_pct * 100:.2f}%)",
        f"Total Volume ($): {total_volume:.2f} | Sharpe Ratio: {sharpe:.2f} | "
        f"Profit Factor: {profit_factor:.2f}",
        f"Total Executors: {total_executors} | Accuracy Long: {acc_long:.2f} | "
        f"Accuracy Short: {acc_short:.2f}",
        f"Close Types: TP: {tp} | SL: {sl} | TL: {tl} | "
        f"Trailing: {trailing} | Early Stop: {early} | Position Hold: {pos_hold}",
    ]

    return "\n".join(lines)


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> RoutineResult:
    chat_id = context._chat_id if hasattr(context, "_chat_id") else None

    # Rendering a stored run needs neither the API nor the controller config.
    if config.task_id.strip():
        loaded = _load_saved_task(config.task_id.strip())
        if isinstance(loaded, str):
            return RoutineResult(text=loaded)
        result, config = loaded
        return await _render(result, config, chat_id, context)

    client = await get_client(chat_id, context=context)
    if not client:
        return RoutineResult(text="No server available")

    start_ts = _parse_date(config.start_date)
    end_ts = _parse_date(config.end_date)

    # Fetch controller config and run backtest
    try:
        ctrl_config = await client.controllers.get_controller_config(config.config_name)
        if not ctrl_config:
            return RoutineResult(text=f"Config '{config.config_name}' not found")
    except Exception as e:
        return RoutineResult(text=f"Failed to load config: {e}")

    # Coerce numeric values (YAML stores numbers as strings)
    def coerce(d):
        out = {}
        for k, v in d.items():
            if isinstance(v, str):
                for cast in (int, float):
                    try:
                        v = cast(v)
                        break
                    except ValueError:
                        pass
            elif isinstance(v, dict):
                v = coerce(v)
            out[k] = v
        return out

    try:
        result = await client.backtesting.run(
            start_time=start_ts,
            end_time=end_ts,
            backtesting_resolution=config.resolution,
            trade_cost=config.trade_cost,
            config=coerce(ctrl_config),
        )
    except Exception as e:
        return RoutineResult(text=f"Backtest failed: {e}")

    if not result or isinstance(result, dict) and "error" in result:
        return RoutineResult(text=f"Backtest error: {result.get('error', 'unknown')}")

    return await _render(result, config, chat_id, context)


async def _render(
    result: dict,
    config: Config,
    chat_id: int | None,
    context: ContextTypes.DEFAULT_TYPE,
) -> RoutineResult:
    """Turn a backtest result (fresh or stored) into a chart, report and summary."""
    # Parse results
    executors = result.get("executors", [])
    processed_data = result.get("processed_data", {})
    bt_results = result.get("results", {})
    pnl_ts = result.get("pnl_timeseries", [])
    position_held_ts = result.get("position_held_timeseries", [])

    if not processed_data:
        return RoutineResult(text=f"Backtest returned no candle data.\n\n{bt_results}")

    candle_df = _build_candle_df(processed_data)

    # Generate chart
    chart_bytes = None
    fig = None
    try:
        buf, fig = generate_chart(
            candle_df, executors, pnl_ts, bt_results, config, position_held_ts
        )
        chart_bytes = buf.getvalue()

        # Send to Telegram if available
        if chat_id:
            buf.seek(0)
            caption = f"Backtest: {config.config_name} ({config.start_date} to {config.end_date})"
            if context.bot:
                await context.bot.send_photo(
                    chat_id=chat_id, photo=buf, caption=caption
                )
            else:
                await _send_photo_http(chat_id, buf, caption)
    except Exception as e:
        logger.warning(f"Chart generation failed: {e}", exc_info=True)

    text = format_summary(bt_results, config)

    # Generate web report
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(title=f"Backtest: {config.config_name}")
        builder.source("routine", "backtest_chart").tags(["backtest"])
        builder.markdown(text)
        if fig is not None:
            builder.plotly(fig)
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}", exc_info=True)

    return RoutineResult(text=text, chart_image=chart_bytes)


async def _send_photo_http(chat_id: int, buf: io.BytesIO, caption: str) -> None:
    """Send a photo via Telegram HTTP API when bot instance is unavailable."""
    import os

    import httpx

    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN", "")
    if not token:
        return

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    buf.seek(0)
    async with httpx.AsyncClient(timeout=30) as http:
        await http.post(
            url,
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": ("chart.png", buf, "image/png")},
        )
