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

from condor.backtesting import fetch_and_save, run_and_save
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
    chart: bool = Field(
        default=True,
        description="Send the chart image to the chat (off for sweeps)",
    )


def _parse_date(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _format_date(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def _retention_days() -> int:
    from condor.backtest_store import retention_days

    return retention_days()


def _is_saved(task_id: str) -> bool:
    """Whether a chart could be rendered from the store, by id or unique prefix.

    A miss is exactly the case that has to fall through to the server before it
    gives up. A run whose payload was pruned is a miss too -- asking the server
    for it once is the only thing that could bring the chart back.
    """
    from condor.backtest_store import get_backtest_store

    store = get_backtest_store()
    resolved = store.resolve_task_id(task_id)
    return isinstance(resolved, str) and store.has_payload(resolved)


def _load_saved_task(task_id: str) -> tuple[dict, "Config"] | str:
    """Load a stored backtest result, or return an error message.

    The returned Config mirrors the parameters the saved run was executed with,
    so titles and the summary describe that run rather than the form defaults.
    """
    from condor.backtest_store import get_backtest_store

    store = get_backtest_store()
    resolved = store.resolve_task_id(task_id)

    if resolved is None:
        saved = ", ".join(sorted(store.known_task_ids())) or "none"
        return f"No saved backtest matching '{task_id}'. Saved: {saved}"
    if isinstance(resolved, list):
        return f"'{task_id}' is ambiguous: {', '.join(resolved)}"
    task_id = resolved

    task = store.get_result(task_id)
    if task is None:
        # The run is real and its metrics are still in the index; only the
        # candles are gone. Saying "no saved backtest" here would be a lie.
        summary = store.get_summary(task_id) or {}
        ran_on = _format_date(summary.get("completed_at")) or "an earlier date"
        return (
            f"Backtest '{task_id}' was run on {ran_on} but its chart data has "
            f"expired (payloads are kept {_retention_days()} days). Its metrics "
            "are still available in backtest_compare."
        )
    if task.get("status") != "completed" or not task.get("result"):
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


# ---------------------------------------------------------------------------
# Indicator helpers — auto-detect columns from processed_data and render them
# ---------------------------------------------------------------------------


def _detect_bb_cols(df: pd.DataFrame) -> dict:
    """Return BB column names if Bollinger Bands are present, else empty dict."""
    for col in df.columns:
        if col.startswith("BBU_"):
            suffix = col[4:]
            lower = f"BBL_{suffix}"
            if lower in df.columns:
                mid = f"BBM_{suffix}"
                return {
                    "upper": col,
                    "mid": mid if mid in df.columns else None,
                    "lower": lower,
                    "label": f"BB({suffix})",
                }
    return {}


def _detect_macd_cols(df: pd.DataFrame) -> dict:
    """Return MACD column names if MACD is present, else empty dict."""
    for col in df.columns:
        if col.startswith("MACDh_"):
            suffix = col[6:]
            macd = f"MACD_{suffix}"
            sig = f"MACDs_{suffix}"
            if macd in df.columns:
                return {
                    "hist": col,
                    "macd": macd,
                    "signal": sig if sig in df.columns else None,
                    "label": f"MACD({suffix})",
                }
    return {}


def _add_signal_shading(fig, df: pd.DataFrame, row: int = 1, col: int = 1):
    """Paint subtle green/red background stripes where the signal is +1/-1."""
    if "signal" not in df.columns:
        return
    s = df[["datetime", "signal"]].copy()
    s["signal"] = pd.to_numeric(s["signal"], errors="coerce").fillna(0)
    s["block"] = (s["signal"] != s["signal"].shift()).cumsum()
    for _, block in s.groupby("block"):
        sig = block["signal"].iloc[0]
        if sig == 0:
            continue
        color = "rgba(38,166,154,0.09)" if sig > 0 else "rgba(239,83,80,0.09)"
        fig.add_vrect(
            x0=block["datetime"].iloc[0],
            x1=block["datetime"].iloc[-1],
            fillcolor=color,
            line_width=0,
            row=row,
            col=col,
        )


def _add_bb_bands(fig, df: pd.DataFrame, bb_cols: dict, row: int = 1, col: int = 1):
    """Overlay Bollinger Bands on the price panel with a subtle shaded fill."""
    x = df["datetime"]
    upper_y = pd.to_numeric(df[bb_cols["upper"]], errors="coerce")
    lower_y = pd.to_numeric(df[bb_cols["lower"]], errors="coerce")

    # Upper band — rendered first so the lower fill reaches it via tonexty
    fig.add_trace(
        go.Scatter(
            x=x,
            y=upper_y,
            mode="lines",
            line=dict(color="rgba(38,166,154,0.55)", width=1),
            showlegend=True,
            legendgroup="bb",
            name=bb_cols.get("label", "BB Upper"),
            hoverinfo="skip",
        ),
        row=row,
        col=col,
    )
    # Lower band fills the space between itself and the upper trace
    fig.add_trace(
        go.Scatter(
            x=x,
            y=lower_y,
            mode="lines",
            line=dict(color="rgba(239,83,80,0.55)", width=1),
            fill="tonexty",
            fillcolor="rgba(120,144,156,0.07)",
            showlegend=False,
            legendgroup="bb",
            name="BB Lower",
            hoverinfo="skip",
        ),
        row=row,
        col=col,
    )
    # Middle / basis line
    if bb_cols.get("mid") and bb_cols["mid"] in df.columns:
        fig.add_trace(
            go.Scatter(
                x=x,
                y=pd.to_numeric(df[bb_cols["mid"]], errors="coerce"),
                mode="lines",
                line=dict(color="rgba(255,213,79,0.35)", width=1, dash="dot"),
                showlegend=False,
                legendgroup="bb",
                name="BB Mid",
                hoverinfo="skip",
            ),
            row=row,
            col=col,
        )


def _add_macd_panel(fig, df: pd.DataFrame, macd_cols: dict, row: int = 2, col: int = 1):
    """Render MACD histogram + line + signal in its own sub-panel."""
    x = df["datetime"]
    hist = pd.to_numeric(df[macd_cols["hist"]], errors="coerce")
    bar_colors = np.where(hist >= 0, COLORS["green"], COLORS["red"])

    fig.add_trace(
        go.Bar(
            x=x,
            y=hist,
            marker_color=bar_colors,
            marker_line_width=0,
            opacity=0.75,
            name="MACD Hist",
            legendgroup="macd",
            hovertemplate="%{y:.5f}<extra>MACD Hist</extra>",
        ),
        row=row,
        col=col,
    )

    if macd_cols.get("macd") and macd_cols["macd"] in df.columns:
        fig.add_trace(
            go.Scatter(
                x=x,
                y=pd.to_numeric(df[macd_cols["macd"]], errors="coerce"),
                mode="lines",
                line=dict(color=COLORS["blue"], width=1.5),
                name=macd_cols.get("label", "MACD"),
                legendgroup="macd",
                hovertemplate="%{y:.5f}<extra>MACD</extra>",
            ),
            row=row,
            col=col,
        )

    if macd_cols.get("signal") and macd_cols["signal"] in df.columns:
        fig.add_trace(
            go.Scatter(
                x=x,
                y=pd.to_numeric(df[macd_cols["signal"]], errors="coerce"),
                mode="lines",
                line=dict(color=COLORS["orange"], width=1.5),
                name="Signal",
                legendgroup="macd",
                hovertemplate="%{y:.5f}<extra>Signal</extra>",
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
    render_png: bool = True,
) -> tuple[io.BytesIO | None, go.Figure]:
    """Generate a multi-panel backtest chart with indicator overlays.

    Panels (top → bottom): Price + BB + signal shading → MACD (if detected)
    → Cumulative PnL → Position held.

    ``render_png=False`` skips the kaleido PNG step; the web report renders the
    figure directly, so sweeps can leave chart=False and skip the slow half.
    """
    # --- Detect indicators present in processed data ---
    bb_cols = _detect_bb_cols(candle_df)
    macd_cols = _detect_macd_cols(candle_df)
    has_macd = bool(macd_cols)
    has_pnl = bool(pnl_ts)
    has_position = bool(position_held_ts)

    # Build ordered row list and index
    row_list = ["price"]
    if has_macd:
        row_list.append("macd")
    if has_pnl:
        row_list.append("pnl")
    if has_position:
        row_list.append("position")
    n_rows = len(row_list)
    row_idx = {name: i + 1 for i, name in enumerate(row_list)}

    _heights = {
        1: [1.0],
        2: [0.70, 0.30],
        3: [0.52, 0.22, 0.26],
        4: [0.44, 0.18, 0.21, 0.17],
    }
    row_heights = _heights.get(n_rows, [1.0 / n_rows] * n_rows)
    specs = [[{"secondary_y": True}] for _ in range(n_rows)]

    fig = make_subplots(
        rows=n_rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=row_heights,
        specs=specs,
    )

    # --- Row 1: Price panel ---
    c_col = "close_bt" if "close_bt" in candle_df.columns else "close"

    # Signal direction shading (subtle green/red backgrounds for long/short)
    _add_signal_shading(fig, candle_df, row=1, col=1)

    # Bollinger Bands rendered behind the price line
    if bb_cols:
        _add_bb_bands(fig, candle_df, bb_cols, row=1, col=1)

    # Price line — white for crisp contrast on dark background
    fig.add_trace(
        go.Scatter(
            x=candle_df["datetime"],
            y=candle_df[c_col],
            mode="lines",
            line=dict(color="#ffffff", width=1.5),
            name="Price",
            hovertemplate="%{y:.4f}<extra>Price</extra>",
        ),
        row=1,
        col=1,
    )

    # Entry/exit markers and grid overlays
    _add_executor_markers(fig, executors, row=1, col=1)
    _add_grid_visualization(fig, executors, row=1, col=1)

    # --- MACD panel ---
    if has_macd:
        _add_macd_panel(fig, candle_df, macd_cols, row=row_idx["macd"], col=1)

    # --- Cumulative PnL panel ---
    if has_pnl:
        _add_cumulative_pnl(fig, pnl_ts, row=row_idx["pnl"], col=1)

    # --- Position held panel ---
    if has_position:
        _add_position_held(fig, position_held_ts, row=row_idx["position"], col=1)

    # --- Title with color-coded PnL and key metrics ---
    net_pnl = results.get("net_pnl_quote", results.get("net_pnl", 0))
    net_pnl_pct = results.get("net_pnl", 0)
    sharpe = results.get("sharpe_ratio", 0)
    profit_factor = results.get("profit_factor", 0)
    total_executors = results.get("total_executors", 0)
    total_volume = results.get("total_volume", 0)
    accuracy = results.get("accuracy", 0)

    pnl_color = COLORS["green"] if net_pnl >= 0 else COLORS["red"]
    pnl_sign = "+" if net_pnl >= 0 else ""
    sharpe_color = (
        COLORS["green"]
        if sharpe >= 1
        else (COLORS["yellow"] if sharpe >= 0 else COLORS["red"])
    )

    title_text = (
        f"<b>{config.config_name}</b>  ·  "
        f"{config.start_date} → {config.end_date} @ {config.resolution}  │  "
        f"<span style='color:{pnl_color}'><b>{pnl_sign}${net_pnl:.2f} ({pnl_sign}{net_pnl_pct * 100:.2f}%)</b></span>  "
        f"<span style='color:{sharpe_color}'>Sharpe {sharpe:.2f}</span>  "
        f"PF {profit_factor:.2f}  "
        f"Acc {accuracy * 100:.0f}%  "
        f"{total_executors} trades  "
        f"Vol ${total_volume:,.0f}"
    )

    fig.update_layout(
        plot_bgcolor=COLORS["plot_bg"],
        paper_bgcolor=COLORS["bg"],
        font=dict(color=COLORS["text"], size=11, family="'Courier New', monospace"),
        height=1050 if n_rows >= 3 else 800,
        width=1400,
        margin=dict(l=70, r=30, t=90, b=40),
        hovermode="x unified",
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.03,
            xanchor="center",
            x=0.5,
            font=dict(size=10),
            bgcolor="rgba(14,17,23,0.75)",
            bordercolor=COLORS["grid"],
            borderwidth=1,
        ),
        title=dict(
            text=title_text,
            font=dict(size=12),
            y=0.99,
            yanchor="top",
        ),
        bargap=0,
        bargroupgap=0,
    )

    # Axis styling — crosshair spikes on hover, consistent grid
    for r in range(1, n_rows + 1):
        fig.update_xaxes(
            gridcolor=COLORS["grid"],
            zerolinecolor=COLORS["zero"],
            showspikes=True,
            spikecolor="#546e7a",
            spikethickness=1,
            spikedash="dot",
            spikemode="across",
            row=r,
            col=1,
        )
        fig.update_yaxes(
            gridcolor=COLORS["grid"],
            zerolinecolor=COLORS["zero"],
            showspikes=True,
            spikecolor="#546e7a",
            spikethickness=1,
            row=r,
            col=1,
        )

    fig.update_xaxes(rangeslider_visible=False, row=1, col=1)
    # Hide x-tick labels on all but the bottom panel to save vertical space
    for r in range(1, n_rows):
        fig.update_xaxes(showticklabels=False, row=r, col=1)

    fig.update_yaxes(title_text="Price", title_font=dict(size=10), row=1, col=1)
    if has_macd:
        fig.update_yaxes(
            title_text="MACD", title_font=dict(size=10), row=row_idx["macd"], col=1
        )
    if has_pnl:
        fig.update_yaxes(
            title_text="PnL ($)", title_font=dict(size=10), row=row_idx["pnl"], col=1
        )
        fig.update_yaxes(
            title_text="Vol ($)",
            title_font=dict(size=10),
            row=row_idx["pnl"],
            col=1,
            secondary_y=True,
            showgrid=False,
        )
    if has_position:
        fig.update_yaxes(
            title_text="Position",
            title_font=dict(size=10),
            row=row_idx["position"],
            col=1,
        )

    if not render_png:
        return None, fig

    buf = io.BytesIO()
    fig.write_image(buf, format="png", scale=2)
    buf.seek(0)
    return buf, fig


# One row per run, so N runs of this routine concatenate into a table an agent can
# rank without parsing the prose summary. Ratios are carried as percentages
# (net_pnl_pct=1.25 is +1.25%); everything else is in quote currency or a count.
METRIC_COLUMNS = (
    "task_id",
    "config_name",
    # The run's parameters travel WITH its metrics. A sweep concatenates N rows into
    # one table, and a Sharpe without the window, resolution and cost it was measured
    # under cannot be compared against the row above it. They are in the summary text
    # too, but readers are told to rank on table_data and never to parse the prose.
    "start_date",
    "end_date",
    "resolution",
    "trade_cost",
    "net_pnl_quote",
    "net_pnl_pct",
    "sharpe_ratio",
    "max_drawdown_pct",
    "accuracy_pct",
    "profit_factor",
    "total_executors",
    "win_signals",
    "loss_signals",
    "total_fees_quote",
    "total_volume",
)


# Below this many executors, no metric in the report means anything — a Sharpe over
# 8 trades is noise with a decimal point. Every backtesting playbook states the gate;
# stating it here too means a reader who skipped the playbook still cannot miss it.
MIN_VALID_TRADES = 20


def _trade_count_warning(results: dict) -> str | None:
    """The validity gate, as a line — or None when the run cleared it."""
    total = results.get("total_executors")
    try:
        total = int(total)
    except (TypeError, ValueError):
        return None
    if total >= MIN_VALID_TRADES:
        return None
    return (
        f"⚠️ {total} executors — below the {MIN_VALID_TRADES}-trade validity gate. "
        "Treat every metric below as noise: widen the window or loosen the filters "
        "and re-run before reading anything into these numbers."
    )


def _sections(results: dict) -> list[dict]:
    """Dashboard KPIs. The trade count leads because it gates reading the rest."""
    total = results.get("total_executors")
    below_gate = _trade_count_warning(results) is not None
    return [
        {
            "type": "kpi",
            "label": "Trades",
            "value": str(total if total is not None else "—"),
            "delta": f"below {MIN_VALID_TRADES}-trade gate" if below_gate else None,
            "trend": "down" if below_gate else "flat",
        }
    ]


def _round(value, digits: int = 4, scale: float = 1.0):
    """A metric as a number, or None when the engine did not report it."""
    if value is None:
        return None
    try:
        return round(float(value) * scale, digits)
    except (TypeError, ValueError):
        return None


def _metrics_row(results: dict, config: Config) -> dict:
    """The run reduced to the row an agent ranks on. Keys are ``METRIC_COLUMNS``."""
    return {
        "task_id": config.task_id,
        "config_name": config.config_name,
        "start_date": config.start_date,
        "end_date": config.end_date,
        "resolution": config.resolution,
        "trade_cost": config.trade_cost,
        "net_pnl_quote": _round(results.get("net_pnl_quote"), 2),
        "net_pnl_pct": _round(results.get("net_pnl"), 4, scale=100),
        "sharpe_ratio": _round(results.get("sharpe_ratio"), 4),
        "max_drawdown_pct": _round(results.get("max_drawdown_pct"), 4, scale=100),
        "accuracy_pct": _round(results.get("accuracy"), 4, scale=100),
        "profit_factor": _round(results.get("profit_factor"), 4),
        "total_executors": results.get("total_executors"),
        "win_signals": results.get("win_signals"),
        "loss_signals": results.get("loss_signals"),
        "total_fees_quote": _round(results.get("total_fees_quote"), 2),
        "total_volume": _round(results.get("total_volume"), 2),
    }


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
    ]
    # The handle for backtest_compare and for re-rendering this exact run.
    if config.task_id:
        lines.append(f"Task ID: {config.task_id}")
    # Above the numbers, not below them: a thin run has to be disqualified before
    # anyone reads its Sharpe, not after.
    warning = _trade_count_warning(results)
    if warning:
        lines += ["", warning]
    lines += [
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


def _server_name(chat_id: int | None, context: ContextTypes.DEFAULT_TYPE) -> str:
    """The server this run belongs to — the key the store files the result under.

    Works from every seat: web and agent runs arrive on a ``WebRoutineContext``, which
    names the server it was launched against; a Telegram chat resolves through its
    active server preference instead.
    """
    from config_manager import get_config_manager, get_effective_server

    launched_with = getattr(context, "server_name", None)
    if launched_with:
        return launched_with

    user_data = getattr(context, "user_data", None)
    if user_data is None:
        user_data = getattr(context, "_user_data", None)

    try:
        name = get_effective_server(chat_id, user_data)
    except Exception:
        logger.debug("Could not resolve the active server", exc_info=True)
        name = None

    return name or get_config_manager().get_default_server() or ""


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> RoutineResult:
    chat_id = context._chat_id if hasattr(context, "_chat_id") else None

    # Rendering a stored run needs neither the API nor the controller config --
    # unless the run is not stored. A backtest whose result read timed out finished
    # on the server and never reached the store, so a miss asks the server once and
    # saves what it gets; from then on this task renders locally like any other.
    if config.task_id.strip():
        task_id = config.task_id.strip()
        if not _is_saved(task_id):
            client = await get_client(chat_id, context=context)
            if client:
                await fetch_and_save(client, _server_name(chat_id, context), task_id)
        loaded = _load_saved_task(task_id)
        if isinstance(loaded, str):
            return RoutineResult(text=loaded)
        result, config = loaded
        return await _render(result, config, chat_id, context)

    client = await get_client(chat_id, context=context)
    if not client:
        return RoutineResult(text="No server available")

    start_ts = _parse_date(config.start_date)
    end_ts = _parse_date(config.end_date)

    # A failure raises rather than being returned as text. A returned string is a
    # *result*, and the store records a result as a successful run — so a backtest
    # the engine refused (a rate-limited candle fetch, a config that is not there)
    # was filed as status "completed" with error None, and the conversation was
    # told "✅ Routine backtest_chart done" followed by the failure it had just
    # been handed. Raising is what makes the instance, its dot and its note agree.
    ctrl_config = await client.controllers.get_controller_config(config.config_name)
    if not ctrl_config:
        raise ValueError(f"Config '{config.config_name}' not found")

    # The task API, not the sync endpoint: it is what produces the task_id the store
    # is keyed by, so every run — chat, web or agent — is rankable by backtest_compare.
    task_id, task = await run_and_save(
        client,
        _server_name(chat_id, context),
        ctrl_config,
        start_ts,
        end_ts,
        resolution=config.resolution,
        trade_cost=config.trade_cost,
    )

    # From here on the fresh run is indistinguishable from a stored one: carrying the
    # task_id on the config is what makes the summary, the table row and a later
    # re-render (task_id=…) all name the same record.
    config = config.model_copy(update={"task_id": task_id})
    return await _render(task["result"], config, chat_id, context)


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

    # Generate chart. A sweep runs with chart=False: no PNG, no photo, and no twenty
    # images pushed into the chat — the figure still reaches the web report, which is
    # where the charts of a sweep are read from, by task_id.
    chart_bytes = None
    fig = None
    try:
        buf, fig = generate_chart(
            candle_df,
            executors,
            pnl_ts,
            bt_results,
            config,
            position_held_ts,
            render_png=config.chart,
        )
        if buf is not None:
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
    from condor.reports import ReportBuilder

    builder = ReportBuilder(title=f"Backtest: {config.config_name}")
    builder.source("routine", "backtest_chart").tags(["backtest"])
    builder.markdown(text)
    if fig is not None:
        builder.plotly(fig)
    await builder.save()

    return RoutineResult(
        text=text,
        table_data=[_metrics_row(bt_results, config)],
        table_columns=list(METRIC_COLUMNS),
        chart_image=chart_bytes,
        sections=_sections(bt_results),
    )


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
