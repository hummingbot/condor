"""PMM Config Analyzer — Compare controller configs side by side, highlight differences."""

CATEGORY = "Bot Analysis"

import asyncio
import logging
import re
from collections import defaultdict

import plotly.graph_objects as go
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_config_manager

logger = logging.getLogger(__name__)

_DARK_BG = "#1a1a2e"
_DARK_PAPER = "#16213e"
_GREEN = "#00d4aa"
_RED = "#ff6b6b"
_BLUE = "#58a6ff"
_YELLOW = "#ffdd57"
_COLORS = [
    "#58a6ff",
    "#00d4aa",
    "#ff6b6b",
    "#ffdd57",
    "#c084fc",
    "#fb923c",
    "#38bdf8",
    "#f472b6",
]

# Parameters to always skip in comparison (noise)
SKIP_PARAMS = {"id", "controller_type", "controller_name", "manual_kill_switch"}

# Parameters to highlight when different (key tuning params)
KEY_PARAMS = {
    "buy_spreads",
    "sell_spreads",
    "buy_amounts_pct",
    "sell_amounts_pct",
    "total_amount_quote",
    "stop_loss",
    "take_profit",
    "time_limit",
    "executor_refresh_time",
    "cooldown_time",
    "leverage",
    "dca_spreads",
    "dca_amounts",
    "trailing_stop",
    "executor_activation_bounds",
    "top_executor_refresh_time",
    "position_rebalance_threshold_pct",
}


class Config(BaseModel):
    """Analyze and compare PMM controller configs — saved vs running bots."""

    server_name: str = Field(
        default="brigado_2",
        description="Server to connect to",
    )
    controller_type: str = Field(
        default="",
        description="Filter by controller type (e.g. 'market_making'). Empty = all types.",
    )
    controller_filter: str = Field(
        default="pmm_mister",
        description="Filter by config name or controller_name containing this string. Empty = all.",
    )
    pair_filter: str = Field(
        default="",
        description="Filter by trading pair (e.g. 'BRL', 'BTC-BRL'). Empty = all.",
    )
    running_only: bool = Field(
        default=False,
        description="Show only configs that are currently running in active bots.",
    )
    show_common: bool = Field(
        default=False,
        description="Include parameters that are the same across all configs (verbose mode).",
    )


def _short_name(config_name: str) -> str:
    """Shorten config name for display."""
    name = config_name
    name = re.sub(r"^mm_", "", name)
    name = re.sub(r"pmm_?mister_?", "pm_", name)
    name = re.sub(r"^(binance|okx|bybit|kucoin)(_perpetual)?_", "", name)
    name = re.sub(r"config[_]?(\d+)", r"c\1", name)
    while "__" in name:
        name = name.replace("__", "_")
    return name.strip("_") or config_name[:25]


def _fmt_value(v) -> str:
    """Format a config value for display."""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "✓" if v else "✗"
    if isinstance(v, float):
        if v < 0.001:
            return f"{v:.5f}"
        if v < 0.01:
            return f"{v:.4f}"
        return f"{v:.3f}" if v < 10 else f"{v:.1f}"
    if isinstance(v, list):
        return ", ".join(_fmt_value(x) for x in v)
    if isinstance(v, dict):
        parts = [f"{k}={_fmt_value(vv)}" for k, vv in v.items()]
        return " | ".join(parts)
    return str(v)


def _normalize_value(v):
    """Normalize values for comparison."""
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return v.strip().lower()
    if isinstance(v, list):
        return [_normalize_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _normalize_value(vv) for k, vv in v.items()}
    return v


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    # Connect to server
    client = await get_config_manager().get_client(config.server_name)
    if not client:
        return f"Could not connect to server '{config.server_name}'"

    # Fetch all data in parallel
    all_configs_list, bots_data = await asyncio.gather(
        client.controllers.list_controller_configs(),
        _get_bots_data(client),
    )

    # --- Saved configs ---
    saved_configs = {}
    filt = config.controller_filter.lower() if config.controller_filter else ""
    for cfg_meta in all_configs_list or []:
        cname = cfg_meta.get("id") or cfg_meta.get("config_name", "")
        ctype = cfg_meta.get("controller_type", "")
        ctrl_name = cfg_meta.get("controller_name", "")

        if (
            config.controller_type
            and config.controller_type.lower() not in ctype.lower()
        ):
            continue
        if filt and filt not in cname.lower() and filt not in ctrl_name.lower():
            continue

        try:
            cfg_data = await client.controllers.get_controller_config(cname)
            # Also check controller_name inside the full config data
            if filt and filt not in cname.lower():
                inner_ctrl = cfg_data.get("controller_name", "")
                if filt not in inner_ctrl.lower():
                    continue
            if config.pair_filter:
                pair = cfg_data.get("trading_pair", "")
                if config.pair_filter.upper() not in pair.upper():
                    continue
            saved_configs[cname] = cfg_data
        except Exception as e:
            logger.warning(f"Failed to fetch config {cname}: {e}")

    # --- Running bot configs ---
    running_config_ids = set()
    running_configs = {}
    for bot_name in bots_data or {}:
        try:
            bot_cfgs = await client.controllers.get_bot_controller_configs(bot_name)
            if isinstance(bot_cfgs, list):
                for cfg in bot_cfgs:
                    cid = cfg.get("id") or cfg.get("controller_id", "")
                    ctype = cfg.get("controller_type", "")
                    if (
                        config.controller_type
                        and config.controller_type.lower() not in ctype.lower()
                    ):
                        continue
                    ctrl_name_inner = cfg.get("controller_name", "")
                    if (
                        filt
                        and filt not in cid.lower()
                        and filt not in ctrl_name_inner.lower()
                    ):
                        continue
                    if config.pair_filter:
                        pair = cfg.get("trading_pair", "")
                        if config.pair_filter.upper() not in pair.upper():
                            continue
                    running_configs[cid] = cfg
                    running_config_ids.add(cid)
        except Exception as e:
            logger.warning(f"Failed to fetch bot configs for {bot_name}: {e}")

    # Merge all configs with status prefix
    all_configs = {}
    for name, cfg in saved_configs.items():
        prefix = "🟢" if name in running_config_ids else "🔵"
        all_configs[f"{prefix} {name}"] = cfg
    for cid, cfg in running_configs.items():
        if cid not in saved_configs:
            all_configs[f"🟢 {cid}"] = cfg

    # Filter to running only if requested
    if config.running_only:
        all_configs = {
            k: v for k, v in all_configs.items() if k.startswith("\U0001f7e2")
        }

    if not all_configs:
        filters = []
        if config.controller_type:
            filters.append(f"type={config.controller_type}")
        if config.controller_filter:
            filters.append(f"filter={config.controller_filter}")
        if config.pair_filter:
            filters.append(f"pair={config.pair_filter}")
        return f"No configs found matching: {', '.join(filters) if filters else 'any'}"

    # --- Build comparison ---
    all_params = set()
    for cfg in all_configs.values():
        all_params.update(cfg.keys())
    all_params -= SKIP_PARAMS

    # Sort: key params first, then alphabetical
    sorted_params = sorted(all_params, key=lambda p: (0 if p in KEY_PARAMS else 1, p))

    # Find which params differ
    diff_params = set()
    for param in sorted_params:
        values = [_normalize_value(cfg.get(param)) for cfg in all_configs.values()]
        non_none = [v for v in values if v is not None]
        if len(set(str(v) for v in non_none)) > 1:
            diff_params.add(param)

    # Group configs by trading pair
    pair_groups = defaultdict(dict)
    for label, cfg in all_configs.items():
        pair = cfg.get("trading_pair", "unknown")
        pair_groups[pair][label] = cfg

    num_running = sum(1 for l in all_configs if l.startswith("🟢"))

    # --- Build HTML report ---
    from condor.reports import ReportBuilder

    builder = ReportBuilder("PMM Config Analyzer")
    builder.source("routine", "config_analyzer").tags(["configs", "pmm", "analysis"])
    builder.manual_order()

    # KPIs
    builder.kpi("Total Configs", str(len(all_configs)))
    builder.kpi("Running", str(num_running), trend="up" if num_running else "neutral")
    builder.kpi("Saved Only", str(len(all_configs) - num_running))
    builder.kpi(
        "Diffs Found",
        str(len(diff_params)),
        delta=f"of {len(sorted_params)} params",
    )
    builder.kpi("Trading Pairs", str(len(pair_groups)))

    # --- Per-pair comparison tables ---
    for pair, pair_cfgs in sorted(pair_groups.items(), key=lambda x: -len(x[1])):
        n_running = sum(1 for l in pair_cfgs if l.startswith("🟢"))

        labels = list(pair_cfgs.keys())
        shorts = [_short_name(l.replace("🟢 ", "").replace("🔵 ", "")) for l in labels]

        # Find pair-level diffs
        pair_diff_params = set()
        for param in sorted_params:
            values = [_normalize_value(pair_cfgs[l].get(param)) for l in labels]
            non_none = [v for v in values if v is not None]
            if len(non_none) > 0 and len(set(str(v) for v in non_none)) > 1:
                pair_diff_params.add(param)

        heading_md = f"## {pair} ({len(pair_cfgs)} configs, {n_running} running)"

        if len(labels) == 1:
            # Single config — show full params
            label = labels[0]
            cfg = pair_cfgs[label]
            rows = []
            for param in sorted_params:
                val = cfg.get(param)
                if val is not None:
                    is_key = "⚡ " if param in KEY_PARAMS else ""
                    rows.append(
                        {
                            "Parameter": f"{is_key}{param}",
                            "Value": _fmt_value(val),
                        }
                    )
            builder.markdown(f"{heading_md}\n\n**{label}** — single config")
            if rows:
                builder.table(rows)
        else:
            # Multiple configs — comparison view
            rows = []
            for param in sorted_params:
                values = [pair_cfgs[l].get(param) for l in labels]
                non_none = [v for v in values if v is not None]
                if not non_none:
                    continue

                is_diff = param in pair_diff_params
                if not config.show_common and not is_diff:
                    continue

                prefix = (
                    "🔴 "
                    if is_diff and param in KEY_PARAMS
                    else "🟡 " if is_diff else ""
                )
                row = {"Parameter": f"{prefix}{param}"}
                for short, val in zip(shorts, values):
                    row[short] = _fmt_value(val)
                rows.append(row)

            note = (
                f"Showing {len(rows)} differing params. 🔴 = key, 🟡 = other."
                if not config.show_common
                else ""
            )
            if not rows and not config.show_common:
                builder.markdown(
                    f"{heading_md}\n\nAll parameters identical across these configs."
                )
            else:
                builder.markdown(f"{heading_md}\n\n{note}" if note else heading_md)
                if rows:
                    builder.table(rows)

    # --- Cross-pair key differences summary ---
    if len(pair_groups) > 1 and diff_params:
        cross_sections = []
        for param in sorted_params:
            if param not in diff_params or param not in KEY_PARAMS:
                continue
            value_groups = defaultdict(list)
            for label, cfg in all_configs.items():
                val = _fmt_value(cfg.get(param))
                short = _short_name(label.replace("🟢 ", "").replace("🔵 ", ""))
                value_groups[val].append(short)
            if len(value_groups) <= 1:
                continue
            rows = []
            for val, names in sorted(value_groups.items(), key=lambda x: -len(x[1])):
                rows.append(
                    {
                        "Value": val,
                        "Count": str(len(names)),
                        "Configs": ", ".join(names[:8])
                        + (f" (+{len(names)-8})" if len(names) > 8 else ""),
                    }
                )
            cross_sections.append((param, rows))

        if cross_sections:
            builder.markdown(
                "## Cross-Pair Key Differences\n\nKey tuning parameters grouped by distinct values across all configs."
            )
            for param, rows in cross_sections:
                builder.markdown(f"**`{param}`** — {len(rows)} distinct values:")
                builder.table(rows)

    # --- Charts ---
    if len(all_configs) > 1:
        fig_pc = _build_parallel_coords(all_configs, running_config_ids)
        if fig_pc:
            builder.plotly(fig_pc)

        fig = _build_spread_chart(all_configs)
        if fig:
            builder.plotly(fig)

        fig2 = _build_capital_chart(all_configs)
        if fig2:
            builder.plotly(fig2)

    await builder.save()

    # --- Concise plain text summary ---
    lines = [f"**PMM Config Analyzer** — {config.server_name}", ""]
    lines.append(
        f"Found **{len(all_configs)}** configs ({num_running} running, {len(all_configs) - num_running} saved-only)"
    )
    lines.append(f"**{len(diff_params)}** parameters differ across configs")
    lines.append(
        f"**{len(pair_groups)}** pairs: {', '.join(sorted(pair_groups.keys()))}"
    )
    lines.append("")

    for pair, pair_cfgs in sorted(pair_groups.items(), key=lambda x: -len(x[1])):
        n_r = sum(1 for l in pair_cfgs if l.startswith("🟢"))
        lines.append(f"- **{pair}**: {len(pair_cfgs)} configs ({n_r} running)")

    lines.append("")
    lines.append("Full comparison report available in the dashboard.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_bots_data(client) -> dict:
    try:
        data = await client.bot_orchestration.get_active_bots_status()
        if isinstance(data, dict) and "data" in data:
            return data["data"]
    except Exception:
        pass
    return {}


def _build_parallel_coords(all_configs: dict, running_ids: set) -> go.Figure | None:
    """Parallel coordinates plot (HiPlot-style) for numeric config parameters."""
    # Extract numeric axes from key params
    NUMERIC_AXES = [
        ("total_amount_quote", "Capital", None),
        (
            "buy_spreads",
            "Buy Spread L1 %",
            lambda v: (v[0] if isinstance(v, list) and v else v) if v else None,
        ),
        (
            "sell_spreads",
            "Sell Spread L1 %",
            lambda v: (v[0] if isinstance(v, list) and v else v) if v else None,
        ),
        (
            "buy_spreads",
            "Buy Spread L2 %",
            lambda v: (
                (v[1] if isinstance(v, list) and len(v) > 1 else None) if v else None
            ),
        ),
        (
            "sell_spreads",
            "Sell Spread L2 %",
            lambda v: (
                (v[1] if isinstance(v, list) and len(v) > 1 else None) if v else None
            ),
        ),
        ("executor_refresh_time", "Refresh Time", None),
        ("cooldown_time", "Cooldown", None),
        ("leverage", "Leverage", None),
        ("stop_loss", "Stop Loss", None),
        ("take_profit", "Take Profit", None),
        ("time_limit", "Time Limit", None),
        ("top_executor_refresh_time", "Top Refresh", None),
        (
            "dca_spreads",
            "DCA Spread L1",
            lambda v: (v[0] if isinstance(v, list) and v else v) if v else None,
        ),
        (
            "dca_amounts",
            "DCA Amt L1",
            lambda v: (v[0] if isinstance(v, list) and v else v) if v else None,
        ),
        (
            "trailing_stop",
            "Trail Stop",
            lambda v: (
                (v.get("activation_price_delta") if isinstance(v, dict) else v)
                if v
                else None
            ),
        ),
        (
            "executor_activation_bounds",
            "Activation Bounds",
            lambda v: (v[0] if isinstance(v, list) and v else v) if v else None,
        ),
    ]

    # Collect data rows
    rows = []
    labels = []
    for label, cfg in all_configs.items():
        clean_label = label.replace("\U0001f7e2 ", "").replace("\U0001f535 ", "")
        is_running = clean_label in running_ids or label.startswith("\U0001f7e2")
        pair = cfg.get("trading_pair", "unknown")
        row = {"_running": 1 if is_running else 0, "_pair": pair}
        for param, axis_name, extractor in NUMERIC_AXES:
            raw = cfg.get(param)
            if extractor:
                val = extractor(raw)
            else:
                val = raw
            try:
                row[axis_name] = float(val) if val is not None else None
            except (TypeError, ValueError):
                row[axis_name] = None
        rows.append(row)
        labels.append(_short_name(clean_label))

    if len(rows) < 2:
        return None

    # Filter axes that have at least 2 distinct non-None values
    valid_axes = []
    for _, axis_name, _ in NUMERIC_AXES:
        vals = [r[axis_name] for r in rows if r[axis_name] is not None]
        if len(vals) >= 2 and len(set(vals)) > 1:
            valid_axes.append(axis_name)

    if len(valid_axes) < 2:
        return None

    # Build color by pair
    unique_pairs = sorted(set(r["_pair"] for r in rows))
    pair_to_idx = {p: i for i, p in enumerate(unique_pairs)}
    color_values = [pair_to_idx[r["_pair"]] for r in rows]

    # Build colorscale from our palette
    n_pairs = len(unique_pairs)
    if n_pairs == 1:
        colorscale = [[0, _BLUE], [1, _BLUE]]
    else:
        colorscale = []
        for i in range(n_pairs):
            pos = i / (n_pairs - 1)
            colorscale.append([pos, _COLORS[i % len(_COLORS)]])

    # Fill None values with axis min (so lines don't break)
    for axis_name in valid_axes:
        vals = [r[axis_name] for r in rows if r[axis_name] is not None]
        fill = min(vals) if vals else 0
        for r in rows:
            if r[axis_name] is None:
                r[axis_name] = fill

    # Build dimensions
    dimensions = []
    for axis_name in valid_axes:
        vals = [r[axis_name] for r in rows]
        # Multiply spread values by 100 for readability
        if "%" in axis_name:
            vals = [v * 100 for v in vals]
        dimensions.append(
            dict(
                label=axis_name,
                values=vals,
                range=(
                    [min(vals), max(vals)]
                    if min(vals) != max(vals)
                    else [min(vals) - 1, max(vals) + 1]
                ),
            )
        )

    fig = go.Figure(
        data=go.Parcoords(
            line=dict(
                color=color_values,
                colorscale=colorscale,
                showscale=False,
            ),
            dimensions=dimensions,
            customdata=labels,
        )
    )

    # Add pair legend via invisible scatter traces
    for i, pair in enumerate(unique_pairs):
        fig.add_trace(
            go.Scatter(
                x=[None],
                y=[None],
                mode="markers",
                marker=dict(size=10, color=_COLORS[i % len(_COLORS)]),
                name=pair,
                showlegend=True,
            )
        )

    fig.update_layout(
        title=dict(
            text=f"Config Parameter Space ({len(rows)} configs, {len(valid_axes)} axes)",
            font=dict(color="#e6edf3", size=14),
            x=0.5,
        ),
        paper_bgcolor=_DARK_PAPER,
        plot_bgcolor=_DARK_BG,
        font=dict(color="#ccc", size=11),
        margin=dict(l=80, r=80, t=80, b=40),
        width=1400,
        height=600,
        legend=dict(
            orientation="h", y=-0.05, x=0.5, xanchor="center", font=dict(size=12)
        ),
    )
    return fig


def _build_spread_chart(all_configs: dict) -> go.Figure | None:
    """Grouped bar chart comparing buy/sell spreads across configs."""

    def _to_list(v) -> list:
        if v is None:
            return []
        if isinstance(v, (int, float)):
            return [float(v)]
        if isinstance(v, list):
            return [float(x) for x in v]
        return []

    configs_with_spreads = []
    for label, cfg in all_configs.items():
        buy = _to_list(cfg.get("buy_spreads"))
        sell = _to_list(cfg.get("sell_spreads"))
        if buy or sell:
            short = _short_name(label.replace("🟢 ", "").replace("🔵 ", ""))
            pair = cfg.get("trading_pair", "")
            configs_with_spreads.append(
                {
                    "name": short,
                    "pair": pair,
                    "buy_l1": buy[0] * 100 if len(buy) >= 1 else 0,
                    "sell_l1": sell[0] * 100 if len(sell) >= 1 else 0,
                    "buy_l2": buy[1] * 100 if len(buy) >= 2 else 0,
                    "sell_l2": sell[1] * 100 if len(sell) >= 2 else 0,
                }
            )

    if not configs_with_spreads:
        return None

    configs_with_spreads.sort(key=lambda c: (c["pair"], c["name"]))
    names = [c["name"] for c in configs_with_spreads]

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=names,
            y=[c["buy_l1"] for c in configs_with_spreads],
            name="Buy L1",
            marker_color=_GREEN,
            opacity=0.9,
        )
    )
    fig.add_trace(
        go.Bar(
            x=names,
            y=[c["sell_l1"] for c in configs_with_spreads],
            name="Sell L1",
            marker_color=_RED,
            opacity=0.9,
        )
    )

    has_l2 = any(c["buy_l2"] > 0 or c["sell_l2"] > 0 for c in configs_with_spreads)
    if has_l2:
        fig.add_trace(
            go.Bar(
                x=names,
                y=[c["buy_l2"] for c in configs_with_spreads],
                name="Buy L2",
                marker_color=_GREEN,
                opacity=0.5,
            )
        )
        fig.add_trace(
            go.Bar(
                x=names,
                y=[c["sell_l2"] for c in configs_with_spreads],
                name="Sell L2",
                marker_color=_RED,
                opacity=0.5,
            )
        )

    fig.update_layout(
        title=dict(
            text="Spread Levels by Config (%)",
            font=dict(color="#e6edf3", size=14),
            x=0.5,
        ),
        paper_bgcolor=_DARK_PAPER,
        plot_bgcolor=_DARK_BG,
        font=dict(color="#ccc", size=10),
        barmode="group",
        yaxis=dict(gridcolor="#2a2a4a", title="Spread %"),
        xaxis=dict(gridcolor="#2a2a4a", tickangle=-45),
        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
        margin=dict(l=60, r=40, t=60, b=120),
        width=1200,
        height=500,
    )
    return fig


def _build_capital_chart(all_configs: dict) -> go.Figure | None:
    """Horizontal bar chart: capital allocation per config, colored by pair."""
    data = []
    for label, cfg in all_configs.items():
        amt = float(cfg.get("total_amount_quote", 0) or 0)
        if amt <= 0:
            continue
        short = _short_name(label.replace("🟢 ", "").replace("🔵 ", ""))
        pair = cfg.get("trading_pair", "")
        is_running = label.startswith("🟢")
        data.append({"name": short, "pair": pair, "amount": amt, "running": is_running})

    if not data:
        return None

    data.sort(key=lambda d: (d["pair"], d["amount"]))

    unique_pairs = list(dict.fromkeys(d["pair"] for d in data))
    pair_colors = {p: _COLORS[i % len(_COLORS)] for i, p in enumerate(unique_pairs)}

    fig = go.Figure()
    for pair in unique_pairs:
        pair_data = [d for d in data if d["pair"] == pair]
        fig.add_trace(
            go.Bar(
                y=[d["name"] for d in pair_data],
                x=[d["amount"] for d in pair_data],
                orientation="h",
                name=pair,
                marker_color=pair_colors[pair],
                opacity=0.85,
                text=[
                    f"{'🟢' if d['running'] else ''} {d['amount']:,.0f}"
                    for d in pair_data
                ],
                textposition="auto",
            )
        )

    height = max(400, len(data) * 22 + 100)
    fig.update_layout(
        title=dict(
            text="Capital Allocation by Config (quote currency)",
            font=dict(color="#e6edf3", size=14),
            x=0.5,
        ),
        paper_bgcolor=_DARK_PAPER,
        plot_bgcolor=_DARK_BG,
        font=dict(color="#ccc", size=10),
        barmode="stack",
        xaxis=dict(gridcolor="#2a2a4a", title="Amount (quote)"),
        yaxis=dict(gridcolor="#2a2a4a"),
        legend=dict(orientation="h", y=1.05, x=0.5, xanchor="center"),
        margin=dict(l=150, r=40, t=60, b=50),
        width=1200,
        height=height,
    )
    return fig
