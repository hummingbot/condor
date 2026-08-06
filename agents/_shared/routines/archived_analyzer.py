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

from condor.archived_pnl import calculate_pnl_from_trades
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

# A detail run pages through every fill. Past this the report stops being
# readable long before the fetch stops being cheap, so we cut it and say so.
MAX_TRADES = 50_000
TRADES_PAGE = 500


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


@dataclass
class ModeOutput:
    """What a mode produced: the inline answer plus the parts the report needs."""

    text: str
    table: list[dict] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    kpis: list[dict] = field(default_factory=list)
    markdown: str = ""
    figure: go.Figure | None = None


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


async def _fetch_all_trades(client, db_path: str) -> tuple[list[dict], bool]:
    """Page through every fill. Returns (trades, truncated)."""
    all_trades: list[dict] = []
    offset = 0
    while True:
        try:
            resp = await client.archived_bots.get_database_trades(
                db_path, limit=TRADES_PAGE, offset=offset
            )
        except Exception:  # noqa: BLE001 — keep the fills we already have
            logger.debug("Trade page failed at offset %s", offset, exc_info=True)
            break
        trades = (resp or {}).get("trades", [])
        if not trades:
            break
        all_trades.extend(trades)
        if len(trades) < TRADES_PAGE:
            break
        if len(all_trades) >= MAX_TRADES:
            return all_trades[:MAX_TRADES], True
        offset += TRADES_PAGE
    return all_trades, False


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


async def _latest_healthy_markdown(client, entries: list[tuple[str, str]]) -> str:
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

    trades, truncated = await _fetch_all_trades(client, path)
    if trades:
        pnl = calculate_pnl_from_trades(trades)
        buys = sum(1 for t in trades if t.get("trade_type", "").upper() == "BUY")
        rows += [
            ("PnL", _usd(pnl.get("total_pnl", 0))),
            ("Volume", _usd(pnl.get("total_volume", 0), 0)),
            ("Fees", _usd(pnl.get("total_fees", 0))),
            ("Buy/Sell", f"{buys} / {len(trades) - buys}"),
        ]
        by_pair = pnl.get("pnl_by_pair") or {}
        if by_pair:
            rows.append(
                (
                    "PnL by Pair",
                    " | ".join(
                        f"{pair}: {_usd(value)}"
                        for pair, value in sorted(
                            by_pair.items(), key=lambda x: abs(x[1]), reverse=True
                        )
                    ),
                )
            )
        if truncated:
            rows.append(("Note", f"capped at the first {MAX_TRADES:,} fills"))

    body = "\n".join(f"| **{label}** | {value} |" for label, value in rows)
    return (
        f"\n\n---\n### Last Deployed Healthy Bot: `{name}`\n\n"
        f"| Field | Value |\n|-------|-------|\n{body}\n"
    )


async def _mode_list(client, db_filter: str) -> ModeOutput:
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

    markdown = await _latest_healthy_markdown(client, healthy) if healthy else ""

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


def _pnl_figure(
    bot_name: str,
    cumulative_pnl: list[dict],
    total_pnl: float,
    total_volume: float,
    total_fees: float,
    trade_count: int,
) -> go.Figure:
    timestamps = [p["timestamp"] for p in cumulative_pnl]
    values = [p["pnl"] for p in cumulative_pnl]
    color = COLORS["green"] if total_pnl >= 0 else COLORS["red"]
    r, g, b = (int(color[i : i + 2], 16) for i in (1, 3, 5))

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=timestamps,
            y=values,
            mode="lines",
            name="Cumulative PnL",
            line=dict(color=color, width=2),
            fill="tozeroy",
            fillcolor=f"rgba({r},{g},{b},0.15)",
            hovertemplate="<b>%{x|%b %d %H:%M}</b><br>PnL: $%{y:,.4f}<extra></extra>",
        )
    )
    fig.add_hline(y=0, line_dash="dot", line_color=COLORS["zero"])

    fig.update_layout(
        title=dict(
            text=(
                f"<b>{bot_name}</b><br>"
                f"<sup>PnL: {_usd(total_pnl)} | Vol: {_usd(total_volume, 0)} | "
                f"Fees: {_usd(total_fees)} | Trades: {trade_count:,}</sup>"
            ),
            x=0.5,
            font=dict(size=14),
        ),
        paper_bgcolor=COLORS["bg"],
        plot_bgcolor=COLORS["plot_bg"],
        font=dict(color=COLORS["text"], size=11),
        xaxis=dict(gridcolor=COLORS["grid"], zerolinecolor=COLORS["zero"]),
        yaxis=dict(
            gridcolor=COLORS["grid"], zerolinecolor=COLORS["zero"], title_text="PnL ($)"
        ),
        margin=dict(l=70, r=30, t=80, b=50),
        height=500,
        width=1000,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
    )
    return fig


async def _mode_detail(client, db_path: str) -> ModeOutput:
    if not db_path:
        return ModeOutput(
            text="detail mode needs db_path. Run mode=list first to find one."
        )

    summary = await _get_summary(client, db_path)
    if not summary:
        return ModeOutput(text=f"No summary found for {db_path}")

    bot_name = summary.get("bot_name") or _extract_bot_name(db_path)
    trades, truncated = await _fetch_all_trades(client, db_path)
    if not trades:
        return ModeOutput(text=f"{bot_name}: no trade data found.")

    pnl = calculate_pnl_from_trades(trades)
    total_pnl = pnl.get("total_pnl", 0)
    total_fees = pnl.get("total_fees", 0)
    total_volume = pnl.get("total_volume", 0)
    by_pair = pnl.get("pnl_by_pair") or {}
    cumulative = pnl.get("cumulative_pnl") or []

    buys = sum(1 for t in trades if t.get("trade_type", "").upper() == "BUY")
    duration = _duration_str(summary.get("start_time"), summary.get("end_time"))

    lines = [
        f"**{bot_name}**",
        f"PnL: {_usd(total_pnl)} | Volume: {_usd(total_volume, 0)} | Fees: {_usd(total_fees)}",
        f"Trades: {len(trades):,} ({buys} buy / {len(trades) - buys} sell)",
        f"Pairs: {', '.join(summary.get('trading_pairs') or []) or 'N/A'}",
        f"Exchanges: {', '.join(summary.get('exchanges') or []) or 'N/A'}",
        f"Period: {_fmt_dt(summary.get('start_time'))} - {_fmt_dt(summary.get('end_time'))} ({duration})",
    ]
    if truncated:
        lines.append(
            f"Note: capped at the first {MAX_TRADES:,} fills — PnL below covers that slice only."
        )

    pair_table = [
        {"Pair": pair, "PnL": _usd(value)}
        for pair, value in sorted(
            by_pair.items(), key=lambda x: abs(x[1]), reverse=True
        )
    ]

    return ModeOutput(
        text="\n".join(lines),
        table=pair_table,
        columns=["Pair", "PnL"] if pair_table else [],
        kpis=[
            {
                "label": "PnL",
                "value": _usd(total_pnl),
                "delta": duration,
                "trend": "up" if total_pnl >= 0 else "down",
            },
            {"label": "Volume", "value": _usd(total_volume, 0)},
            {"label": "Fees", "value": _usd(total_fees)},
            {"label": "Trades", "value": _count(len(trades))},
        ],
        markdown="\n".join(lines),
        figure=(
            _pnl_figure(
                bot_name, cumulative, total_pnl, total_volume, total_fees, len(trades)
            )
            if cumulative
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _report_title(config: Config) -> str:
    if config.mode == "detail" and config.db_path:
        return f"Archived Bot — {_extract_bot_name(config.db_path)}"
    title = f"Archived Bots — {config.mode.title()}"
    if config.db_filter:
        title += f" ({config.db_filter})"
    return title


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> RoutineResult:
    if config.mode not in MODES:
        return RoutineResult(
            text=f"Unknown mode '{config.mode}'. Use one of: {', '.join(MODES)}."
        )

    # The run's own server, gated by the caller's access — never a server named
    # in the config, which would reach any configured server unchecked.
    client = await get_client(getattr(context, "_chat_id", None), context=context)
    if not client:
        return RoutineResult(text="No server available for this run.")

    if config.mode == "list":
        output = await _mode_list(client, config.db_filter)
    elif config.mode == "summary":
        output = await _mode_summary(client, config.db_filter)
    else:
        output = await _mode_detail(client, config.db_path)

    from condor.reports import ReportBuilder

    builder = ReportBuilder(_report_title(config))
    builder.source("routine", "archived_analyzer").tags(
        ["bots", "archived", config.mode]
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

    if output.table:
        if config.mode == "detail":
            builder.section("PnL by Pair", "Realized PnL per trading pair.")
        elif config.mode == "summary":
            builder.section("Archived Runs", "Every healthy database on this server.")
        else:
            builder.section("Databases", "Every archived database and its health.")
        builder.table(output.table, output.columns)

    builder.markdown(output.markdown or output.text)
    await builder.save()

    chart_image = None
    if output.figure is not None:
        buf = io.BytesIO()
        output.figure.write_image(buf, format="png", scale=2)
        chart_image = buf.getvalue()

    return RoutineResult(
        text=output.text,
        table_data=output.table or None,
        table_columns=output.columns or None,
        chart_image=chart_image,
    )
