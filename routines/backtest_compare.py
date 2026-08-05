"""Compare saved backtests: overlaid equity curves + a ranked metrics table."""

CATEGORY = "Bot Analysis"

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.backtest_store import get_backtest_store
from config_manager import get_config_manager
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

# Shared with routines/backtest_chart.py so both reports read as one family.
COLORS = {
    "bg": "#0e1117",
    "plot_bg": "#0e1117",
    "grid": "#1e2530",
    "zero": "#2a3441",
    "text": "#e0e0e0",
    "text_dim": "#78909c",
}

# One distinct colour per compared run, in assignment order.
SERIES_COLORS = ["#ffd54f", "#42a5f5", "#26a69a", "#ab47bc", "#ff6d00", "#ef5350"]

# Curves carry ~10k points each; more than this per run bloats the HTML report
# without adding anything the eye can resolve.
MAX_CURVE_POINTS = 2000

MIN_RUNS = 2
MAX_RUNS = 6


class Config(BaseModel):
    """Compare saved backtests with overlaid PnL curves and a ranked metrics table."""

    task_ids: str = Field(
        default="",
        description="Comma-separated saved backtest task IDs (blank = use the latest N)",
    )
    latest_n: int = Field(
        default=3,
        description="How many recent backtests to compare when task IDs are blank (2-6)",
    )
    normalize_time: bool = Field(
        default=False,
        description="Align curves at t=0 (elapsed hours) so different date ranges overlay",
    )


@dataclass
class Run:
    """One saved backtest, reduced to what the comparison needs."""

    task_id: str
    config_id: str
    controller: str
    connector: str
    pair: str
    resolution: str
    start_ts: int
    end_ts: int
    metrics: dict
    close_types: dict
    curve: list[tuple[float, float]] = field(default_factory=list)

    @property
    def label(self) -> str:
        parts = [self.config_id or self.task_id]
        if self.pair:
            parts.append(self.pair)
        parts.append(f"{_fmt_date(self.start_ts)}→{_fmt_date(self.end_ts)}")
        return " · ".join(parts)


# -- formatting --


def _fmt_date(ts: float | int) -> str:
    if not ts:
        return "?"
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%b %d")


def _usd(value: float | None, decimals: int = 2) -> str:
    """USD with a $ sign and comma separators; negatives as -$1,234.56."""
    if value is None:
        return "—"
    value = float(value)
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.{decimals}f}"


def _pct(value: float | None) -> str:
    """Backtest ratios are stored as fractions (0.0123 = 1.23%)."""
    if value is None:
        return "—"
    return f"{float(value) * 100:.2f}%"


def _num(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{decimals}f}"


# -- store access --


def _store_path(store, task_id: str) -> Path:
    """Path of a task file. Touches the store's internals in exactly one place."""
    return store._task_path(task_id)


def _known_task_ids(store) -> list[str]:
    """All task IDs the store knows about. Index-only, no file parsing."""
    return list(store._index.keys())


def _task_server(store, task_id: str) -> str:
    return store._index.get(task_id, {}).get("server", "")


def _resolve_ids(store, raw: str) -> tuple[list[str], list[str]]:
    """Resolve user-supplied IDs (exact or unique prefix) to real task IDs."""
    known = _known_task_ids(store)
    resolved: list[str] = []
    errors: list[str] = []

    for token in (t.strip() for t in raw.split(",")):
        if not token:
            continue
        if token in known:
            if token not in resolved:
                resolved.append(token)
            continue
        matches = [tid for tid in known if tid.startswith(token)]
        if not matches:
            errors.append(f"'{token}' matches no saved backtest")
        elif len(matches) > 1:
            errors.append(f"'{token}' is ambiguous ({', '.join(sorted(matches))})")
        elif matches[0] not in resolved:
            resolved.append(matches[0])

    return resolved, errors


def _latest_ids(store, count: int, server: str | None) -> list[str]:
    """Most recently written task IDs, newest first.

    Ordered by file mtime so picking candidates never parses the result files
    (they run to ~16 MB each).
    """
    candidates = _known_task_ids(store)
    if server:
        scoped = [t for t in candidates if _task_server(store, t) == server]
        # A chat pointed at a server with no saved backtests still gets an
        # answer rather than an empty comparison.
        candidates = scoped or candidates

    def mtime(task_id: str) -> float:
        try:
            return _store_path(store, task_id).stat().st_mtime
        except OSError:
            return 0.0

    return sorted(candidates, key=mtime, reverse=True)[:count]


# -- run loading --


def _downsample(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Stride-sample a curve, always keeping the first and last point."""
    if len(points) <= MAX_CURVE_POINTS:
        return points
    step = len(points) // MAX_CURVE_POINTS + 1
    sampled = points[::step]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def _build_curve(result: dict) -> list[tuple[float, float]]:
    """(timestamp, cumulative PnL) for a run."""
    pnl_ts = result.get("pnl_timeseries") or []
    if pnl_ts:
        points = [
            (float(p["timestamp"]), float(p.get("total_pnl") or 0))
            for p in pnl_ts
            if p.get("timestamp")
        ]
        return _downsample(points)

    # Older results predate pnl_timeseries: rebuild from closed executors, the
    # same fallback the dashboard uses. Position holds never close, so counting
    # them would double-count PnL still open at the end of the run.
    closed = [
        e
        for e in (result.get("executors") or [])
        if e.get("close_timestamp")
        and float(e.get("filled_amount_quote") or 0) > 0
        and str(e.get("close_type", "")).upper() != "POSITION_HOLD"
    ]
    closed.sort(key=lambda e: float(e["close_timestamp"]))
    points, cumulative = [], 0.0
    for ex in closed:
        cumulative += float(ex.get("net_pnl_quote") or 0)
        points.append((float(ex["close_timestamp"]), cumulative))
    return _downsample(points)


def _load_run(store, task_id: str) -> Run | None:
    """Load one completed backtest, or None if it is unusable."""
    try:
        task = store.get_result(task_id)
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read backtest %s", task_id, exc_info=True)
        return None
    if not task or task.get("status") != "completed":
        return None

    result = task.get("result") or {}
    if not result:
        return None

    task_config = task.get("config") or {}
    controller_config = task_config.get("config") or {}

    return Run(
        task_id=task_id,
        config_id=str(
            controller_config.get("id") or task_config.get("config_id") or ""
        ),
        controller=str(controller_config.get("controller_name") or ""),
        connector=str(controller_config.get("connector_name") or ""),
        pair=str(controller_config.get("trading_pair") or ""),
        resolution=str(task_config.get("backtesting_resolution") or ""),
        start_ts=int(task_config.get("start_time") or 0),
        end_ts=int(task_config.get("end_time") or 0),
        metrics=result.get("results") or {},
        close_types=result.get("results", {}).get("close_types") or {},
        curve=_build_curve(result),
    )


# -- figure --


def _equity_figure(runs: list[Run], normalize_time: bool) -> go.Figure:
    """Overlay every run's cumulative PnL curve on one axis."""
    fig = go.Figure()

    for idx, run in enumerate(runs):
        if not run.curve:
            continue
        color = SERIES_COLORS[idx % len(SERIES_COLORS)]
        timestamps = [t for t, _ in run.curve]
        values = [v for _, v in run.curve]

        if normalize_time:
            origin = timestamps[0]
            x = [(t - origin) / 3600 for t in timestamps]
            hover = "%{x:.1f}h — $%{y:,.2f}<extra>" + run.label + "</extra>"
        else:
            x = pd.to_datetime(timestamps, unit="s", utc=True)
            hover = "$%{y:,.2f}<extra>" + run.label + "</extra>"

        fig.add_trace(
            go.Scatter(
                x=x,
                y=values,
                mode="lines",
                name=run.label,
                line=dict(color=color, width=2),
                hovertemplate=hover,
            )
        )

    fig.add_hline(y=0, line_dash="dot", line_color=COLORS["zero"])

    fig.update_layout(
        plot_bgcolor=COLORS["plot_bg"],
        paper_bgcolor=COLORS["bg"],
        font=dict(color=COLORS["text"], size=11),
        height=520,
        margin=dict(l=60, r=30, t=70, b=40),
        hovermode="x unified",
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5,
            font=dict(size=10),
        ),
        title=dict(text="Cumulative PnL", font=dict(size=14)),
    )
    fig.update_xaxes(
        gridcolor=COLORS["grid"],
        zerolinecolor=COLORS["zero"],
        title_text="Elapsed hours" if normalize_time else None,
    )
    fig.update_yaxes(
        gridcolor=COLORS["grid"], zerolinecolor=COLORS["zero"], title_text="PnL ($)"
    )
    return fig


# -- tables --

# (column label, metrics key, formatter, "high"/"low"/None = better)
METRIC_COLUMNS = [
    ("Net PnL", "net_pnl_quote", lambda v: _usd(v), "high"),
    ("Return", "net_pnl", lambda v: _pct(v), "high"),
    ("Sharpe", "sharpe_ratio", lambda v: _num(v), "high"),
    ("Profit Factor", "profit_factor", lambda v: _num(v), "high"),
    ("Max DD $", "max_drawdown_usd", lambda v: _usd(v), "low"),
    ("Max DD %", "max_drawdown_pct", lambda v: _pct(v), "low"),
    ("Volume", "total_volume", lambda v: _usd(v, 0), "high"),
    ("Fees", "total_fees_quote", lambda v: _usd(v), "low"),
    ("Executors", "total_executors", lambda v: f"{int(v or 0):,}", None),
    ("Acc Long", "accuracy_long", lambda v: _pct(v), "high"),
    ("Acc Short", "accuracy_short", lambda v: _pct(v), "high"),
]


def _best_task_ids(runs: list[Run]) -> dict[str, str]:
    """Winning task ID per metric column. Drawdown is judged on magnitude."""
    winners: dict[str, str] = {}
    for _, key, _, better in METRIC_COLUMNS:
        if not better:
            continue
        scored = [
            (run.task_id, float(run.metrics[key]))
            for run in runs
            if run.metrics.get(key) is not None
        ]
        if not scored:
            continue
        if better == "low":
            winners[key] = min(scored, key=lambda p: abs(p[1]))[0]
        else:
            winners[key] = max(scored, key=lambda p: p[1])[0]
    return winners


def _metrics_rows(runs: list[Run]) -> list[dict]:
    winners = _best_task_ids(runs)
    rows = []
    for run in runs:
        row = {"Run": run.label}
        for label, key, fmt, _ in METRIC_COLUMNS:
            value = run.metrics.get(key)
            text = fmt(value) if value is not None else "—"
            if winners.get(key) == run.task_id:
                text = f"★ {text}"
            row[label] = text
        rows.append(row)
    return rows


def _close_type_rows(runs: list[Run]) -> list[dict]:
    all_types = sorted({t for run in runs for t in run.close_types})
    if not all_types:
        return []
    rows = []
    for close_type in all_types:
        row = {"Close Type": close_type}
        for run in runs:
            row[run.config_id or run.task_id] = (
                f"{int(run.close_types.get(close_type, 0)):,}"
            )
        rows.append(row)
    return rows


# -- summary --


def _summary_text(runs: list[Run], normalize_time: bool) -> str:
    lines = [
        f"Comparing {len(runs)} backtests"
        + (" (time-aligned)" if normalize_time else ""),
        "",
    ]
    ranked = sorted(
        runs, key=lambda r: float(r.metrics.get("net_pnl_quote") or 0), reverse=True
    )
    for run in ranked:
        m = run.metrics
        lines.append(
            f"{run.task_id} {run.label} @ {run.resolution or '?'}\n"
            f"  Net PnL: {_usd(m.get('net_pnl_quote'))} ({_pct(m.get('net_pnl'))}) | "
            f"Sharpe: {_num(m.get('sharpe_ratio'))} | "
            f"Profit Factor: {_num(m.get('profit_factor'))} | "
            f"Max DD: {_usd(m.get('max_drawdown_usd'))} ({_pct(m.get('max_drawdown_pct'))})"
        )
    lines.append("")
    winner = ranked[0]
    lines.append(
        f"Winner by net PnL: {winner.config_id or winner.task_id} "
        f"({_usd(winner.metrics.get('net_pnl_quote'))})"
    )
    return "\n".join(lines)


def _no_runs_message(store, requested: list[str], errors: list[str]) -> str:
    lines = ["Need at least 2 completed backtests to compare."]
    if errors:
        lines.append("")
        lines.extend(errors)
    known = _known_task_ids(store)
    if known:
        lines.append("")
        lines.append(f"Saved backtests ({len(known)}): {', '.join(sorted(known))}")
        if requested:
            lines.append(
                "Only completed runs with results can be compared — "
                "check the ones above in the dashboard Backtesting tab."
            )
    else:
        lines.append("")
        lines.append(
            "No backtests saved yet. Run some from the dashboard Backtesting tab first."
        )
    return "\n".join(lines)


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> RoutineResult:
    store = get_backtest_store()

    requested, errors = _resolve_ids(store, config.task_ids)
    if errors:
        # Explicit IDs that don't resolve are a mistake worth surfacing, never a
        # reason to quietly compare some other set of runs.
        return RoutineResult(text=_no_runs_message(store, requested, errors))

    if not requested:
        count = max(MIN_RUNS, min(MAX_RUNS, config.latest_n))
        chat_id = getattr(context, "_chat_id", None)
        server = None
        if chat_id is not None:
            try:
                server = get_config_manager().get_chat_default_server(chat_id)
            except Exception:
                logger.debug(
                    "Could not resolve chat server, comparing across all", exc_info=True
                )
        requested = _latest_ids(store, count, server)

    runs = [
        run_ for run_ in (_load_run(store, tid) for tid in requested[:MAX_RUNS]) if run_
    ]

    if len(runs) < MIN_RUNS:
        return RoutineResult(text=_no_runs_message(store, requested, errors))

    runs.sort(key=lambda r: float(r.metrics.get("net_pnl_quote") or 0), reverse=True)
    text = _summary_text(runs, config.normalize_time)

    metrics_rows = _metrics_rows(runs)
    close_rows = _close_type_rows(runs)

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(title=f"Backtest Comparison ({len(runs)} runs)")
        builder.source("routine", "backtest_compare").tags(["backtest", "compare"])

        winner = runs[0]
        builder.kpi(
            "Best Net PnL",
            _usd(winner.metrics.get("net_pnl_quote")),
            delta=winner.config_id or winner.task_id,
            trend=(
                "up" if float(winner.metrics.get("net_pnl_quote") or 0) >= 0 else "down"
            ),
        )
        builder.markdown(
            "\n".join(
                f"- `{r.task_id}` — **{r.label}** @ {r.resolution or '?'} "
                f"— {_usd(r.metrics.get('net_pnl_quote'))} ({_pct(r.metrics.get('net_pnl'))})"
                for r in runs
            )
        )

        builder.section("Equity Curves", "Cumulative PnL for every compared run.")
        builder.plotly(_equity_figure(runs, config.normalize_time))

        builder.section("Metrics", "★ marks the best value in each column.")
        builder.table(metrics_rows, columns=["Run"] + [c[0] for c in METRIC_COLUMNS])

        if close_rows:
            builder.section("Close Types", "Executor close reasons per run.")
            builder.table(close_rows, columns=list(close_rows[0].keys()))

        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}", exc_info=True)

    return RoutineResult(
        text=text,
        table_data=metrics_rows,
        table_columns=["Run"] + [c[0] for c in METRIC_COLUMNS],
    )
