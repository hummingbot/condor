"""Analyze archived bot databases: list them, summarize them, or deep-dive one run."""

CATEGORY = "Bot Analysis"

import asyncio
import io
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime

import plotly.graph_objects as go
from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from condor.archived_chart_series import activity_range, build_chart_series
from condor.archived_controllers import group_by_controller
from condor.fetchers.archived_run import ArchivedRunUnavailable, fetch_archived_run
from condor.quote_conversion import QuoteRates
from condor.reports import subjects
from config_manager import get_client
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

# Shared with backtest_chart.py / backtest_compare.py so every report in this
# family reads as one.
COLORS = {
    "bg": "#0e1117",
    "plot_bg": "#0e1117",
    "grid": "#1e2530",
    "zero": "#2a3441",
    "text": "#e0e0e0",
    "text_dim": "#78909c",
    "green": "#26a69a",
    "red": "#ef5350",
}

MODES = ("list", "summary", "detail")

# Health probes and summaries are one API round-trip per database and a server
# can hold hundreds; this keeps the fan-out polite.
MAX_CONCURRENT_PROBES = 10


class Config(BaseModel):
    """Analyze archived bot databases: list, summarize, or deep-dive into historical bot performance"""

    mode: str = Field(
        default="list",
        description="list = every archived DB with health | summary = metrics per healthy DB | detail = one DB in depth",
    )
    db_filter: str = Field(
        default="", description="Only databases whose path contains this text"
    )
    db_path: str = Field(
        default="",
        description="Database to analyze (detail mode only — run list mode first to find one)",
    )
    controller_id: str = Field(
        default="",
        description="Chart only this controller of the run (detail mode; blank = the whole run)",
    )
    chart: bool = Field(
        default=True,
        description="Send the chart image to the chat (off for the dashboard, which embeds the report)",
    )


@dataclass
class ModeOutput:
    """What a mode produced: the inline answer plus the parts the report needs."""

    text: str
    table: list[dict] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    kpis: list[dict] = field(default_factory=list)
    markdown: str = ""
    figure: go.Figure | None = None
    volume_figure: go.Figure | None = None


# ---------------------------------------------------------------------------
# Formatting — same vocabulary as backtest_compare.py
# ---------------------------------------------------------------------------


def _usd(value: float | None, decimals: int = 2) -> str:
    """USD with a $ sign and comma separators; negatives as -$1,234.56."""
    if value is None:
        return "—"
    value = float(value)
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.{decimals}f}"


def _count(value: int | None) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}"


def _extract_bot_name(db_path: str) -> str:
    name = os.path.basename(db_path)
    for suffix in (".sqlite", ".db"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def _parse_dt(dt) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt
    if isinstance(dt, str) and dt:
        try:
            if "T" in dt:
                return datetime.fromisoformat(dt.replace("Z", "+00:00"))
            return datetime.fromisoformat(dt)
        except ValueError:
            logger.debug("Unparseable timestamp from API: %r", dt)
    return None


def _fmt_dt(dt) -> str:
    parsed = _parse_dt(dt)
    return parsed.strftime("%b %d %H:%M") if parsed else "N/A"


def _duration_str(start, end) -> str:
    s, e = _parse_dt(start), _parse_dt(end)
    if not s or not e:
        return "N/A"
    if s.tzinfo:
        s = s.replace(tzinfo=None)
    if e.tzinfo:
        e = e.replace(tzinfo=None)
    delta = e - s
    days, hours = delta.days, delta.seconds // 3600
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {(delta.seconds % 3600) // 60}m"
    return f"{delta.seconds // 60}m"


def _extract_date_from_name(name: str) -> datetime | None:
    """Deployment date out of a bot name (``…-YYYYMMDD-HHMMSS``)."""
    for pattern in (r"(\d{8})-(\d{6})$", r"\d{8}-(\d{8})-(\d{6})$"):
        match = re.search(pattern, name)
        if not match:
            continue
        try:
            return datetime.strptime(
                f"{match.group(1)}-{match.group(2)}", "%Y%m%d-%H%M%S"
            )
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# API access — defensive, because every response here is external input
# ---------------------------------------------------------------------------


async def _list_db_paths(client, db_filter: str) -> tuple[list[str], str | None]:
    """Every archived database path on the server, filtered. Returns (paths, error)."""
    try:
        databases = await client.archived_bots.list_databases()
    except Exception as e:  # noqa: BLE001 — an unreachable server is an answer
        logger.warning("Failed to list archived databases", exc_info=True)
        return [], f"Failed to list databases: {e}"

    if isinstance(databases, dict):
        databases = databases.get("bots", [])

    paths: list[str] = []
    for db in databases or []:
        if isinstance(db, str):
            paths.append(db)
        elif isinstance(db, dict):
            path = db.get("db_path") or db.get("path", "")
            if path:
                paths.append(path)

    if db_filter:
        paths = [p for p in paths if db_filter.lower() in p.lower()]
    return paths, None


async def _check_db_health(client, db_path: str) -> str:
    try:
        status = await client.archived_bots.get_database_status(db_path)
    except Exception:  # noqa: BLE001 — one corrupt DB must not sink the scan
        logger.debug("Health probe failed for %s", db_path, exc_info=True)
        return "error"
    if not status:
        return "error"
    nested = status.get("status", {})
    if isinstance(nested, dict):
        if nested.get("trade_fill") == "Correct":
            return "healthy"
        if nested.get("orders") == "Correct":
            return "partial"
    return "healthy" if status.get("healthy") else "unhealthy"


async def _get_summary(client, db_path: str) -> dict | None:
    try:
        summary = await client.archived_bots.get_database_summary(db_path)
    except Exception:  # noqa: BLE001 — same reason as _check_db_health
        logger.debug("Summary failed for %s", db_path, exc_info=True)
        return None
    return summary if isinstance(summary, dict) and summary else None


async def _gather_bounded(coro_factories) -> list:
    """Run the coroutines with a concurrency cap, dropping the ones that raised."""
    sem = asyncio.Semaphore(MAX_CONCURRENT_PROBES)

    async def guarded(factory):
        async with sem:
            return await factory()

    results = await asyncio.gather(
        *[guarded(f) for f in coro_factories], return_exceptions=True
    )
    return [r for r in results if not isinstance(r, Exception)]


# ---------------------------------------------------------------------------
# Mode: list
# ---------------------------------------------------------------------------


async def _latest_healthy_markdown(
    client, server: str, entries: list[tuple[str, str]]
) -> str:
    """Markdown block describing the most recently deployed healthy bot."""
    ranked = sorted(
        entries,
        key=lambda e: _extract_date_from_name(e[0]) or datetime.min,
        reverse=True,
    )
    name, path = ranked[0]

    summary = await _get_summary(client, path)
    if not summary:
        return f"\n\n---\n### Last Deployed Healthy Bot: `{name}`\n\nNo summary available.\n"

    pairs = summary.get("trading_pairs") or []
    exchanges = summary.get("exchanges") or []
    rows = [
        ("Pairs", ", ".join(pairs) if pairs else "N/A"),
        ("Exchanges", ", ".join(exchanges) if exchanges else "N/A"),
        ("Trades", _count(summary.get("total_trades", 0))),
        ("Orders", _count(summary.get("total_orders", 0))),
        ("Start", _fmt_dt(summary.get("start_time"))),
        ("End", _fmt_dt(summary.get("end_time"))),
        ("Duration", _duration_str(summary.get("start_time"), summary.get("end_time"))),
    ]

    # Through the shared fetcher rather than a trade walk of its own: it is the
    # same run the detail mode charts, so it is answered from the same cache,
    # falls back to executors when the trades table is empty, and reports USD
    # instead of passing a BRL figure off as dollars.
    try:
        perf = await fetch_archived_run(client, server, path)
    except ArchivedRunUnavailable:
        logger.debug("Could not read %s for the latest-bot block", path, exc_info=True)
        perf = None

    if perf is not None:
        rows += [
            ("PnL", _usd(perf.total_pnl)),
            ("Volume", _usd(perf.total_volume, 0)),
            ("Fees", _usd(perf.total_fees)),
            ("Buy/Sell", f"{perf.buy_count} / {perf.sell_count}"),
        ]
        if perf.pnl_by_pair:
            rows.append(
                (
                    "PnL by Pair",
                    " | ".join(
                        f"{pair}: {_usd(value)}"
                        for pair, value in sorted(
                            perf.pnl_by_pair.items(),
                            key=lambda x: abs(x[1]),
                            reverse=True,
                        )
                    ),
                )
            )
        if not perf.converted:
            rows.append(
                (
                    "Note",
                    f"no USD rate for {perf.quote_currency or 'this quote'} — "
                    "figures are in the run's own quote currency",
                )
            )

    body = "\n".join(f"| **{label}** | {value} |" for label, value in rows)
    return (
        f"\n\n---\n### Last Deployed Healthy Bot: `{name}`\n\n"
        f"| Field | Value |\n|-------|-------|\n{body}\n"
    )


async def _mode_list(client, server: str, db_filter: str) -> ModeOutput:
    paths, error = await _list_db_paths(client, db_filter)
    if error:
        return ModeOutput(text=error)
    if not paths:
        scope = f" matching '{db_filter}'" if db_filter else ""
        return ModeOutput(text=f"No archived databases found{scope}.")

    probed = await _gather_bounded(
        [lambda p=p: _probe(client, p) for p in paths],
    )

    table = []
    healthy: list[tuple[str, str]] = []
    for path, status in probed:
        name = _extract_bot_name(path)
        if status == "healthy":
            healthy.append((name, path))
        table.append({"Name": name, "Status": status, "Path": path})

    summary = f"Found {len(table)} archived databases ({len(healthy)} healthy)"
    if db_filter:
        summary += f" matching '{db_filter}'"

    markdown = (
        await _latest_healthy_markdown(client, server, healthy) if healthy else ""
    )

    return ModeOutput(
        text=summary + markdown,
        table=table,
        columns=["Name", "Status", "Path"],
        kpis=[
            {"label": "Databases", "value": _count(len(table))},
            {
                "label": "Healthy",
                "value": _count(len(healthy)),
                "delta": f"of {len(table)}",
                "trend": "up" if healthy else "down",
            },
        ],
        markdown=summary + markdown,
    )


async def _probe(client, path: str) -> tuple[str, str]:
    return path, await _check_db_health(client, path)


# ---------------------------------------------------------------------------
# Mode: summary
# ---------------------------------------------------------------------------


async def _summarize(client, path: str) -> dict | None:
    if await _check_db_health(client, path) not in ("healthy", "partial"):
        return None
    summary = await _get_summary(client, path)
    if summary:
        summary["_db_path"] = path
    return summary


async def _mode_summary(client, db_filter: str) -> ModeOutput:
    paths, error = await _list_db_paths(client, db_filter)
    if error:
        return ModeOutput(text=error)
    if not paths:
        return ModeOutput(text="No archived databases found.")

    summaries = [
        s
        for s in await _gather_bounded(
            [lambda p=p: _summarize(client, p) for p in paths]
        )
        if s
    ]
    if not summaries:
        return ModeOutput(text="No healthy databases with summaries.")

    table = []
    for s in summaries:
        pairs = s.get("trading_pairs") or []
        exchanges = s.get("exchanges") or []
        table.append(
            {
                "Name": s.get("bot_name") or _extract_bot_name(s["_db_path"]),
                "Trades": s.get("total_trades", 0),
                "Orders": s.get("total_orders", 0),
                "Pairs": ", ".join(pairs[:3]) + ("..." if len(pairs) > 3 else ""),
                "Exchanges": ", ".join(exchanges[:2]),
                "Start": _fmt_dt(s.get("start_time")),
                "End": _fmt_dt(s.get("end_time")),
                "Duration": _duration_str(s.get("start_time"), s.get("end_time")),
            }
        )

    total_trades = sum(row["Trades"] for row in table)
    return ModeOutput(
        text=f"{len(table)} archived bots | {_count(total_trades)} total trades",
        table=table,
        columns=[
            "Name",
            "Trades",
            "Orders",
            "Pairs",
            "Exchanges",
            "Start",
            "End",
            "Duration",
        ],
        kpis=[
            {"label": "Archived Bots", "value": _count(len(table))},
            {"label": "Total Trades", "value": _count(total_trades)},
        ],
    )


# ---------------------------------------------------------------------------
# Mode: detail
# ---------------------------------------------------------------------------


def _combined_pnl(series: dict) -> tuple[list[datetime], list[float], list[float]]:
    """One cumulative PnL curve across every market the selection traded.

    ``build_chart_series`` aggregates per market, each curve already restated in
    USD and thinned to at most a couple of thousand points. They cannot be
    concatenated -- each is its own running sum -- so each is differenced back
    into per-point deltas, merged in time order and re-accumulated. For the
    ordinary single-market run this reproduces that market's curve exactly, and
    the point count stays bounded by the series, never by the executor count.
    """
    events: list[tuple[float, float, float]] = []
    for payload in series.values():
        prev_net = 0.0
        prev_fees = 0.0
        for point in payload["pnl_evolution"]:
            net = float(point["net_pnl"])
            fees = float(point["cum_fees"])
            events.append((float(point["time"]), net - prev_net, fees - prev_fees))
            prev_net, prev_fees = net, fees

    events.sort(key=lambda event: event[0])

    times: list[datetime] = []
    nets: list[float] = []
    fees_curve: list[float] = []
    cum_net = 0.0
    cum_fees = 0.0
    for when, d_net, d_fees in events:
        cum_net += d_net
        cum_fees += d_fees
        times.append(datetime.fromtimestamp(when))
        nets.append(cum_net)
        fees_curve.append(cum_fees)
    return times, nets, fees_curve


def _combined_volume(series: dict) -> tuple[list[datetime], list[float], list[float]]:
    """Buy and sell volume per bucket, summed across markets.

    Bucket widths come from a fixed ladder where each width is a multiple of
    every finer one, so folding the finer markets' buckets into the coarsest
    width is exact rather than an approximation.
    """
    if not series:
        return [], [], []

    width = max(int(payload["interval_sec"]) for payload in series.values()) or 60
    buckets: dict[float, list[float]] = {}
    for payload in series.values():
        for bucket in payload["volume_buckets"]:
            when = (float(bucket["time"]) // width) * width
            slot = buckets.setdefault(when, [0.0, 0.0])
            slot[0] += float(bucket["buy_vol"])
            slot[1] += float(bucket["sell_vol"])

    times = sorted(buckets)
    return (
        [datetime.fromtimestamp(t) for t in times],
        [buckets[t][0] for t in times],
        [buckets[t][1] for t in times],
    )


def _base_layout(title: str, subtitle: str, height: int) -> dict:
    return dict(
        title=dict(
            text=f"<b>{title}</b><br><sup>{subtitle}</sup>", x=0.5, font=dict(size=14)
        ),
        paper_bgcolor=COLORS["bg"],
        plot_bgcolor=COLORS["plot_bg"],
        font=dict(color=COLORS["text"], size=11),
        xaxis=dict(gridcolor=COLORS["grid"], zerolinecolor=COLORS["zero"]),
        yaxis=dict(gridcolor=COLORS["grid"], zerolinecolor=COLORS["zero"]),
        margin=dict(l=70, r=30, t=80, b=50),
        height=height,
        width=1000,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
    )


def _pnl_figure(title: str, subtitle: str, series: dict) -> go.Figure | None:
    times, nets, fees = _combined_pnl(series)
    if not times:
        return None

    color = COLORS["green"] if nets[-1] >= 0 else COLORS["red"]
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=times,
            y=nets,
            mode="lines",
            name="Net PnL",
            line=dict(color=color, width=2),
            fill="tozeroy",
            fillcolor=f"rgba({r},{g},{b},0.15)",
            hovertemplate="<b>%{x|%b %d %H:%M}</b><br>Net: $%{y:,.4f}<extra></extra>",
        )
    )
    # Fees arrive as a negative running total, so the drag reads below zero
    # against the curve it was taken out of.
    fig.add_trace(
        go.Scatter(
            x=times,
            y=fees,
            mode="lines",
            name="Cumulative fees",
            line=dict(color=COLORS["text_dim"], width=1, dash="dot"),
            hovertemplate="Fees: $%{y:,.4f}<extra></extra>",
        )
    )
    fig.add_hline(y=0, line_dash="dot", line_color=COLORS["zero"])

    layout = _base_layout(title, subtitle, 460)
    layout["yaxis"]["title_text"] = "USD"
    fig.update_layout(**layout)
    return fig


def _volume_figure(title: str, subtitle: str, series: dict) -> go.Figure | None:
    times, buys, sells = _combined_volume(series)
    if not times:
        return None

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=times,
            y=buys,
            name="Buy",
            marker_color=COLORS["green"],
            hovertemplate="Buy: $%{y:,.0f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            x=times,
            y=sells,
            name="Sell",
            marker_color=COLORS["red"],
            hovertemplate="Sell: $%{y:,.0f}<extra></extra>",
        )
    )

    layout = _base_layout(title, subtitle, 320)
    layout["yaxis"]["title_text"] = "Volume (USD)"
    fig.update_layout(barmode="stack", **layout)
    return fig


def _pair_table(executors: list) -> tuple[list[dict], list[str]]:
    """Per-market rows for one controller's executors, USD, biggest PnL first."""
    rows: dict[str, dict] = {}
    for ex in executors:
        pair = ex.trading_pair or "—"
        row = rows.setdefault(
            pair, {"Pair": pair, "_pnl": 0.0, "_volume": 0.0, "_count": 0}
        )
        rate = ex.usd_rate or 1.0
        row["_pnl"] += ex.pnl * rate
        row["_volume"] += ex.volume * rate
        row["_count"] += 1

    ordered = sorted(rows.values(), key=lambda row: abs(row["_pnl"]), reverse=True)
    table = [
        {
            "Pair": row["Pair"],
            "PnL": _usd(row["_pnl"]),
            "Volume": _usd(row["_volume"], 0),
            "Executors": _count(row["_count"]),
        }
        for row in ordered
    ]
    return table, ["Pair", "PnL", "Volume", "Executors"]


def _controller_table(rollups: list) -> tuple[list[dict], list[str]]:
    """One row per controller of the run. The unattributed row is named as such."""
    table = [
        {
            "Controller": rollup.controller_id or "(no controller)",
            "PnL": _usd(rollup.pnl_usd),
            "Volume": _usd(rollup.volume_usd, 0),
            "Fees": _usd(rollup.fees_usd),
            "Executors": _count(rollup.executor_count),
            "Pairs": ", ".join(rollup.trading_pairs[:3])
            + ("…" if len(rollup.trading_pairs) > 3 else ""),
        }
        for rollup in rollups
    ]
    return table, ["Controller", "PnL", "Volume", "Fees", "Executors", "Pairs"]


def _rollups_reconcile(rollups: list, perf) -> bool:
    """Whether the executor-derived split adds up to the run's own headline.

    Within a cent, or a percent of it — the two are computed from different
    sources, so an exact match is not the bar; a run whose executors archived
    empty is what this is looking for.
    """
    rolled = sum(rollup.pnl_usd for rollup in rollups)
    volume = sum(rollup.volume_usd for rollup in rollups)
    for parts, whole in ((rolled, perf.total_pnl), (volume, perf.total_volume)):
        if abs(parts - whole) > max(0.01, abs(whole) * 0.01):
            return False
    return True


async def _mode_detail(
    client, server: str, db_path: str, controller_id: str
) -> ModeOutput:
    """One archived run, or one controller inside it, charted from its executors.

    The fetch is the web dashboard's own (``condor.fetchers.archived_run``): the
    paginated trade walk with retries, the executor fallback for a run whose
    trades table is empty, and one USD rate per quote resolved before anything
    is totalled. This routine used to walk the trades itself and print a
    BRL-quoted run behind a bare "$"; sharing the fetch is what fixes that, and
    it answers from the warm cache when the dashboard just rendered the table.
    """
    if not db_path:
        return ModeOutput(
            text="detail mode needs db_path. Run mode=list first to find one."
        )

    try:
        perf = await fetch_archived_run(client, server, db_path)
    except ArchivedRunUnavailable as e:
        return ModeOutput(text=f"{_extract_bot_name(db_path)}: {e.detail}")

    bot_name = perf.bot_name or _extract_bot_name(db_path)
    scope = f"{bot_name} · {controller_id}" if controller_id else bot_name

    executors = perf.executors
    if controller_id:
        executors = [ex for ex in executors if ex.controller_id == controller_id]
        if not executors:
            return ModeOutput(
                text=f"{bot_name}: no executors ran under controller '{controller_id}'."
            )
    if not executors:
        return ModeOutput(text=f"{bot_name}: no executors archived for this run.")

    rollups = group_by_controller(executors)
    start, end = activity_range(executors)
    duration = _duration_str(
        datetime.fromtimestamp(start) if start else None,
        datetime.fromtimestamp(end) if end else None,
    )

    # A whole run reports the header its own summary reports -- which is
    # trade-derived when the trades table answered -- so the report and the
    # dashboard's cards tell one story. A controller has no such header: it is
    # a slice of the executors, and its money is rolled up from them.
    if controller_id:
        total_pnl = rollups[0].pnl_usd
        total_volume = rollups[0].volume_usd
        total_fees = rollups[0].fees_usd
        source = "executors"
    else:
        total_pnl = perf.total_pnl
        total_volume = perf.total_volume
        total_fees = perf.total_fees
        source = perf.stats_source

    rates = QuoteRates(perf.usd_rates, perf.converted)
    series = build_chart_series(executors, rates)

    pairs = sorted({ex.trading_pair for ex in executors if ex.trading_pair})
    connectors = sorted({ex.connector for ex in executors if ex.connector})
    subtitle = (
        f"PnL: {_usd(total_pnl)} | Vol: {_usd(total_volume, 0)} | "
        f"Fees: {_usd(total_fees)} | Executors: {len(executors):,}"
    )

    lines = [
        f"**{scope}**",
        subtitle.replace(" | ", " | "),
        f"Pairs: {', '.join(pairs) or 'N/A'}",
        f"Exchanges: {', '.join(connectors) or 'N/A'}",
        f"Period: {_fmt_dt(datetime.fromtimestamp(start)) if start else 'N/A'} - "
        f"{_fmt_dt(datetime.fromtimestamp(end)) if end else 'N/A'} ({duration})",
    ]
    if not controller_id and len(rollups) > 1:
        lines.append(f"Controllers: {len(rollups)}")
    if source == "executors" and not controller_id:
        lines.append("Stats read from executors — this run archived no trade rows.")
    elif not controller_id and not _rollups_reconcile(rollups, perf):
        # Archived trade rows carry no controller_id, so the split can only be
        # rolled up from executors. The two sources are reconstructed
        # differently and a real run can have them disagree — say so rather
        # than leave a reader to wonder why the rows do not add up.
        lines.append(
            "Headline figures come from this run's trade rows; the controller "
            "split below is rolled up from its executors, and for this run the "
            "two do not total the same."
        )
    if not perf.converted:
        lines.append(
            f"No USD rate for {perf.quote_currency or 'this run\'s quote'} — "
            "figures are in the run's own quote currency."
        )

    if controller_id:
        table, columns = _pair_table(executors)
    else:
        table, columns = _controller_table(rollups)

    return ModeOutput(
        text="\n".join(lines),
        table=table,
        columns=columns,
        kpis=[
            {
                "label": "PnL",
                "value": _usd(total_pnl),
                "delta": duration,
                "trend": "up" if total_pnl >= 0 else "down",
            },
            {"label": "Volume", "value": _usd(total_volume, 0)},
            {"label": "Fees", "value": _usd(total_fees)},
            {"label": "Executors", "value": _count(len(executors))},
        ],
        markdown="\n".join(lines),
        figure=_pnl_figure(scope, subtitle, series),
        volume_figure=_volume_figure(scope, "Executor volume per bucket", series),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _report_title(config: Config) -> str:
    if config.mode == "detail" and config.db_path:
        title = f"Archived Bot — {_extract_bot_name(config.db_path)}"
        if config.controller_id:
            title += f" · {config.controller_id}"
        return title
    title = f"Archived Bots — {config.mode.title()}"
    if config.db_filter:
        title += f" ({config.db_filter})"
    return title


def _server_name(chat_id: int | None, context: ContextTypes.DEFAULT_TYPE) -> str:
    """The server this run belongs to — the key its report is filed under.

    Works from every seat: web and agent runs arrive on a ``WebRoutineContext``,
    which names the server it was launched against; a Telegram chat resolves
    through its active server preference instead. The subject includes it, so a
    run launched against the wrong server produces its own report rather than
    aliasing another server's.
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
    if config.mode not in MODES:
        return RoutineResult(
            text=f"Unknown mode '{config.mode}'. Use one of: {', '.join(MODES)}."
        )

    chat_id = getattr(context, "_chat_id", None)

    # One answer to "which server is this run": the box the archive is read from
    # and the key its report is filed under have to be the same server, or a
    # stored report would be found for a run it was never generated from.
    server = _server_name(chat_id, context)

    # Gated by the caller's access — never a server named in the config, which
    # would reach any configured server unchecked.
    client = await get_client(chat_id, context=context, server=server or None)
    if not client:
        return RoutineResult(text="No server available for this run.")

    if config.mode == "list":
        output = await _mode_list(client, server, config.db_filter)
    elif config.mode == "summary":
        output = await _mode_summary(client, config.db_filter)
    else:
        output = await _mode_detail(
            client, server, config.db_path, config.controller_id.strip()
        )

    from condor.reports import ReportBuilder

    builder = ReportBuilder(_report_title(config))
    builder.source("routine", "archived_analyzer").tags(
        ["bots", "archived", config.mode]
    )
    # What this report is *about*, so the dashboard finds it again instead of
    # regenerating one it already has (FEAT-078). An archived run is immutable:
    # the stored report is not a cache, it is the answer.
    if config.mode == "detail" and config.db_path:
        builder.subject(
            subjects.bot_run(server, config.db_path, config.controller_id.strip())
        )
    builder.manual_order()

    for kpi in output.kpis:
        builder.kpi(
            kpi["label"],
            kpi["value"],
            delta=kpi.get("delta"),
            trend=kpi.get("trend") or "neutral",
        )

    if output.figure is not None:
        builder.section("Cumulative PnL", "Realized PnL over the life of the run.")
        builder.plotly(output.figure)

    if output.volume_figure is not None:
        builder.section("Volume", "Executor volume per bucket, buys against sells.")
        builder.plotly(output.volume_figure)

    if output.table:
        if config.mode == "detail":
            if config.controller_id.strip():
                builder.section("By Market", "Realized PnL per trading pair.")
            else:
                builder.section(
                    "Controllers", "Every controller that ran inside this run."
                )
        elif config.mode == "summary":
            builder.section("Archived Runs", "Every healthy database on this server.")
        else:
            builder.section("Databases", "Every archived database and its health.")
        builder.table(output.table, output.columns)

    builder.markdown(output.markdown or output.text)
    await builder.save()

    # The PNG is for a chat. The dashboard embeds the report itself and turns
    # this off, which is the difference between a chart that appears in seconds
    # and one that waits on a kaleido render it will never show.
    chart_image = None
    if config.chart and output.figure is not None:
        buf = io.BytesIO()
        output.figure.write_image(buf, format="png", scale=2)
        chart_image = buf.getvalue()

    return RoutineResult(
        text=output.text,
        table_data=output.table or None,
        table_columns=output.columns or None,
        chart_image=chart_image,
    )
