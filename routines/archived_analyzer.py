CATEGORY = "Bot Analysis"

import asyncio
import io
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_config_manager
from routines.base import RoutineResult

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """Analyze archived bot databases: list, summarize, or deep-dive into historical bot performance"""

    server_name: str = Field(default="brigado_2", description="Server to connect to")
    mode: str = Field(default="list", description="Mode: list | summary | detail")
    db_filter: str = Field(
        default="", description="Filter database names (substring match)"
    )
    db_path: str = Field(default="", description="Database path for detail mode")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_bot_name(db_path: str) -> str:
    name = os.path.basename(db_path)
    for suffix in (".sqlite", ".db"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def _parse_dt(dt) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt
    if isinstance(dt, str) and dt:
        try:
            if "T" in dt:
                return datetime.fromisoformat(dt.replace("Z", "+00:00"))
            return datetime.fromisoformat(dt)
        except Exception:
            pass
    return None


def _fmt_dt(dt) -> str:
    parsed = _parse_dt(dt)
    if not parsed:
        return "N/A"
    return parsed.strftime("%b %d %H:%M")


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


def _fmt_pnl(pnl: float) -> str:
    return f"+${pnl:,.2f}" if pnl >= 0 else f"-${abs(pnl):,.2f}"


def _extract_date_from_name(name: str) -> Optional[datetime]:
    """Extract deployment date from bot name (format: YYYYMMDD-HHMMSS)."""
    match = re.search(r"(\d{8})-(\d{6})$", name)
    if match:
        try:
            return datetime.strptime(
                f"{match.group(1)}-{match.group(2)}", "%Y%m%d-%H%M%S"
            )
        except Exception:
            pass
    match = re.search(r"\d{8}-(\d{8})-(\d{6})$", name)
    if match:
        try:
            return datetime.strptime(
                f"{match.group(1)}-{match.group(2)}", "%Y%m%d-%H%M%S"
            )
        except Exception:
            pass
    return None


async def _get_client(server_name: str):
    cm = get_config_manager()
    try:
        return await cm.get_client(server_name)
    except Exception as e:
        logger.error(f"Failed to connect to {server_name}: {e}")
        return None


async def _check_db_health(client, db_path: str) -> str:
    try:
        status = await client.archived_bots.get_database_status(db_path)
    except Exception:
        return "error"
    if not status:
        return "error"
    nested = status.get("status", {})
    if isinstance(nested, dict):
        if nested.get("trade_fill") == "Correct":
            return "healthy"
        if nested.get("orders") == "Correct":
            return "partial"
    if status.get("healthy"):
        return "healthy"
    return "unhealthy"


async def _fetch_all_trades(client, db_path: str) -> List[Dict[str, Any]]:
    all_trades: list[dict] = []
    offset = 0
    limit = 500
    while True:
        try:
            resp = await client.archived_bots.get_database_trades(
                db_path, limit=limit, offset=offset
            )
        except Exception:
            break
        if not resp:
            break
        trades = resp.get("trades", [])
        if not trades:
            break
        all_trades.extend(trades)
        if len(trades) < limit:
            break
        offset += limit
        if len(all_trades) > 50000:
            break
    return all_trades


# ---------------------------------------------------------------------------
# Mode: list
# ---------------------------------------------------------------------------


async def _mode_list(client, db_filter: str) -> RoutineResult:
    try:
        databases = await client.archived_bots.list_databases()
    except Exception as e:
        return RoutineResult(text=f"Failed to list databases: {e}")

    if isinstance(databases, dict):
        databases = databases.get("bots", [])
    if not databases:
        return RoutineResult(text="No archived databases found on this server.")

    # Normalise paths
    paths: list[str] = []
    for db in databases:
        if isinstance(db, str):
            paths.append(db)
        elif isinstance(db, dict):
            p = db.get("db_path") or db.get("path", "")
            if p:
                paths.append(p)

    if db_filter:
        paths = [p for p in paths if db_filter.lower() in p.lower()]

    if not paths:
        return RoutineResult(text=f"No databases matching '{db_filter}'.")

    # Check health in parallel (bounded)
    sem = asyncio.Semaphore(10)

    async def check(p):
        async with sem:
            return p, await _check_db_health(client, p)

    results = await asyncio.gather(*[check(p) for p in paths], return_exceptions=True)

    table = []
    healthy_count = 0
    healthy_entries = []
    for r in results:
        if isinstance(r, Exception):
            continue
        path, status = r
        name = _extract_bot_name(path)
        if status == "healthy":
            healthy_count += 1
            deploy_date = _extract_date_from_name(name)
            healthy_entries.append((name, path, deploy_date))
        table.append(
            {
                "Name": name,
                "Status": status,
                "Path": path,
            }
        )

    summary = f"Found {len(table)} archived databases ({healthy_count} healthy)"
    if db_filter:
        summary += f" matching '{db_filter}'"

    # Find the most recently deployed healthy bot and fetch its DB description
    latest_detail_md = ""
    if healthy_entries:
        healthy_entries.sort(key=lambda x: x[2] or datetime.min, reverse=True)
        latest_name, latest_path, _ = healthy_entries[0]

        try:
            s = await client.archived_bots.get_database_summary(latest_path)
            if s and isinstance(s, dict):
                pairs = s.get("trading_pairs", [])
                exchanges = s.get("exchanges", [])
                total_trades = s.get("total_trades", 0)
                total_orders = s.get("total_orders", 0)
                start = _fmt_dt(s.get("start_time"))
                end = _fmt_dt(s.get("end_time"))
                duration = _duration_str(s.get("start_time"), s.get("end_time"))

                latest_detail_md = (
                    f"\n\n---\n"
                    f"### Last Deployed Healthy Bot: `{latest_name}`\n\n"
                    f"| Field | Value |\n"
                    f"|-------|-------|\n"
                    f"| **Pairs** | {', '.join(pairs) if pairs else 'N/A'} |\n"
                    f"| **Exchanges** | {', '.join(exchanges) if exchanges else 'N/A'} |\n"
                    f"| **Trades** | {total_trades:,} |\n"
                    f"| **Orders** | {total_orders:,} |\n"
                    f"| **Start** | {start} |\n"
                    f"| **End** | {end} |\n"
                    f"| **Duration** | {duration} |\n"
                )

                # Fetch trade-level PnL data
                try:
                    trades = await _fetch_all_trades(client, latest_path)
                    if trades:
                        from handlers.bots.archived_chart import (
                            calculate_pnl_from_trades,
                        )

                        pnl_data = calculate_pnl_from_trades(trades)
                        total_pnl = pnl_data.get("total_pnl", 0)
                        total_volume = pnl_data.get("total_volume", 0)
                        total_fees = pnl_data.get("total_fees", 0)
                        buy_count = sum(
                            1
                            for t in trades
                            if t.get("trade_type", "").upper() == "BUY"
                        )
                        sell_count = len(trades) - buy_count

                        latest_detail_md += (
                            f"| **PnL** | {_fmt_pnl(total_pnl)} |\n"
                            f"| **Volume** | ${total_volume:,.0f} |\n"
                            f"| **Fees** | ${total_fees:,.2f} |\n"
                            f"| **Buy/Sell** | {buy_count} / {sell_count} |\n"
                        )

                        pnl_by_pair = pnl_data.get("pnl_by_pair", {})
                        if pnl_by_pair:
                            pair_lines = " | ".join(
                                f"{pair}: {_fmt_pnl(pnl)}"
                                for pair, pnl in sorted(
                                    pnl_by_pair.items(),
                                    key=lambda x: abs(x[1]),
                                    reverse=True,
                                )
                            )
                            latest_detail_md += f"| **PnL by Pair** | {pair_lines} |\n"
                except Exception as e:
                    logger.warning(f"Failed to fetch PnL for latest bot: {e}")
        except Exception as e:
            logger.warning(f"Failed to fetch summary for latest healthy bot: {e}")

    return RoutineResult(
        text=summary + latest_detail_md,
        table_data=table,
        table_columns=["Name", "Status", "Path"],
    )


# ---------------------------------------------------------------------------
# Mode: summary
# ---------------------------------------------------------------------------


async def _mode_summary(client, db_filter: str) -> RoutineResult:
    try:
        databases = await client.archived_bots.list_databases()
    except Exception as e:
        return RoutineResult(text=f"Failed to list databases: {e}")

    if isinstance(databases, dict):
        databases = databases.get("bots", [])

    paths: list[str] = []
    for db in databases:
        if isinstance(db, str):
            paths.append(db)
        elif isinstance(db, dict):
            p = db.get("db_path") or db.get("path", "")
            if p:
                paths.append(p)

    if db_filter:
        paths = [p for p in paths if db_filter.lower() in p.lower()]

    if not paths:
        return RoutineResult(text="No databases found.")

    sem = asyncio.Semaphore(10)

    async def fetch_summary(p):
        async with sem:
            health = await _check_db_health(client, p)
            if health not in ("healthy", "partial"):
                return None
            try:
                s = await client.archived_bots.get_database_summary(p)
                if s and isinstance(s, dict):
                    s["_db_path"] = p
                    return s
            except Exception:
                pass
            return None

    results = await asyncio.gather(
        *[fetch_summary(p) for p in paths], return_exceptions=True
    )

    table = []
    for r in results:
        if r is None or isinstance(r, Exception):
            continue
        s = r
        pairs = s.get("trading_pairs", [])
        exchanges = s.get("exchanges", [])
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

    if not table:
        return RoutineResult(text="No healthy databases with summaries.")

    total_trades = sum(r["Trades"] for r in table)
    text = f"{len(table)} archived bots | {total_trades:,} total trades"

    return RoutineResult(
        text=text,
        table_data=table,
        table_columns=[
            "Name",
            "Trades",
            "Orders",
            "Pairs",
            "Exchanges",
            "Start",
            "End",
            "Duration",
        ],
    )


# ---------------------------------------------------------------------------
# Mode: detail
# ---------------------------------------------------------------------------


async def _mode_detail(client, db_path: str) -> RoutineResult:
    if not db_path:
        return RoutineResult(
            text="detail mode requires db_path. Run with mode=list first to find paths."
        )

    # Fetch summary
    try:
        summary = await client.archived_bots.get_database_summary(db_path)
    except Exception as e:
        return RoutineResult(text=f"Failed to fetch summary for {db_path}: {e}")

    if not summary:
        return RoutineResult(text=f"No summary found for {db_path}")

    bot_name = summary.get("bot_name") or _extract_bot_name(db_path)

    # Fetch all trades
    trades = await _fetch_all_trades(client, db_path)
    if not trades:
        return RoutineResult(text=f"{bot_name}: no trade data found.")

    # Calculate PnL
    from handlers.bots.archived_chart import calculate_pnl_from_trades

    pnl_data = calculate_pnl_from_trades(trades)

    total_pnl = pnl_data.get("total_pnl", 0)
    total_fees = pnl_data.get("total_fees", 0)
    total_volume = pnl_data.get("total_volume", 0)
    pnl_by_pair = pnl_data.get("pnl_by_pair", {})
    cumulative_pnl = pnl_data.get("cumulative_pnl", [])

    buy_count = sum(1 for t in trades if t.get("trade_type", "").upper() == "BUY")
    sell_count = len(trades) - buy_count

    # Build text summary
    lines = [
        f"**{bot_name}**",
        f"PnL: {_fmt_pnl(total_pnl)} | Volume: ${total_volume:,.0f} | Fees: ${total_fees:,.2f}",
        f"Trades: {len(trades)} ({buy_count} buy / {sell_count} sell)",
        f"Pairs: {', '.join(summary.get('trading_pairs', []))}",
        f"Exchanges: {', '.join(summary.get('exchanges', []))}",
        f"Period: {_fmt_dt(summary.get('start_time'))} - {_fmt_dt(summary.get('end_time'))} ({_duration_str(summary.get('start_time'), summary.get('end_time'))})",
    ]

    # PnL by pair table
    pair_table = []
    for pair, pnl in sorted(pnl_by_pair.items(), key=lambda x: abs(x[1]), reverse=True):
        pair_table.append({"Pair": pair, "PnL": _fmt_pnl(pnl)})

    # Generate cumulative PnL chart
    chart_bytes = None
    pnl_fig = None
    if cumulative_pnl:
        try:
            pnl_fig = _build_pnl_figure(
                bot_name,
                cumulative_pnl,
                total_pnl,
                total_volume,
                total_fees,
                len(trades),
            )
            buf = io.BytesIO()
            pnl_fig.write_image(buf, format="png", scale=2)
            buf.seek(0)
            chart_bytes = buf.getvalue()
        except Exception as e:
            logger.warning(f"Chart generation failed: {e}")

    text = "\n".join(lines)

    result = RoutineResult(
        text=text,
        table_data=pair_table if pair_table else None,
        table_columns=["Pair", "PnL"] if pair_table else None,
        chart_image=chart_bytes,
    )
    result._pnl_fig = pnl_fig  # attach for report generation
    result._detail_kpis = {
        "total_pnl": total_pnl,
        "total_volume": total_volume,
        "total_fees": total_fees,
        "trade_count": len(trades),
        "duration": _duration_str(summary.get("start_time"), summary.get("end_time")),
    }
    return result


def _build_pnl_figure(
    bot_name: str,
    cumulative_pnl: list,
    total_pnl: float,
    total_volume: float,
    total_fees: float,
    trade_count: int,
):
    import plotly.graph_objects as go

    timestamps = [p["timestamp"] for p in cumulative_pnl]
    pnl_values = [p["pnl"] for p in cumulative_pnl]

    line_color = "#10b981" if total_pnl >= 0 else "#ef4444"
    r, g, b = (
        int(line_color[1:3], 16),
        int(line_color[3:5], 16),
        int(line_color[5:7], 16),
    )

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=timestamps,
            y=pnl_values,
            mode="lines",
            name="Cumulative PnL",
            line=dict(color=line_color, width=2),
            fill="tozeroy",
            fillcolor=f"rgba({r},{g},{b},0.15)",
            hovertemplate="<b>%{x|%b %d %H:%M}</b><br>PnL: $%{y:,.4f}<extra></extra>",
        )
    )
    fig.add_hline(y=0, line_dash="dash", line_color="#8b949e", opacity=0.5)

    fig.update_layout(
        title=dict(
            text=(
                f"<b>{bot_name}</b><br>"
                f"<sup>PnL: {_fmt_pnl(total_pnl)} | Vol: ${total_volume:,.0f} | "
                f"Fees: ${total_fees:,.2f} | Trades: {trade_count}</sup>"
            ),
            x=0.5,
            font=dict(size=16, color="#e6edf3"),
        ),
        paper_bgcolor="#0a0e14",
        plot_bgcolor="#131720",
        font=dict(
            family="'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
            color="#e6edf3",
        ),
        xaxis=dict(showgrid=True, gridcolor="#21262d", tickfont=dict(color="#8b949e")),
        yaxis=dict(
            showgrid=True,
            gridcolor="#21262d",
            tickfont=dict(color="#8b949e"),
            title="PnL ($)",
        ),
        margin=dict(l=70, r=30, t=80, b=50),
        height=500,
        width=1000,
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
    )

    return fig


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> RoutineResult:
    client = await _get_client(config.server_name)
    if not client:
        return RoutineResult(text=f"Could not connect to server '{config.server_name}'")

    if config.mode == "list":
        result = await _mode_list(client, config.db_filter)
    elif config.mode == "summary":
        result = await _mode_summary(client, config.db_filter)
    elif config.mode == "detail":
        result = await _mode_detail(client, config.db_path)
    else:
        return RoutineResult(
            text=f"Unknown mode '{config.mode}'. Use: list, summary, or detail"
        )

    # Generate persistent report
    try:
        from condor.reports import ReportBuilder

        title = f"Archived Bots — {config.mode.title()}"
        if config.mode == "detail" and config.db_path:
            title = f"Archived Bot — {_extract_bot_name(config.db_path)}"
        elif config.db_filter:
            title += f" ({config.db_filter})"

        builder = ReportBuilder(title)
        builder.source("routine", "archived_analyzer").tags(
            ["bots", "archived", config.mode]
        )

        if config.mode == "detail":
            kpis = getattr(result, "_detail_kpis", None)
            if kpis:
                builder.kpi(
                    "PnL",
                    _fmt_pnl(kpis["total_pnl"]),
                    delta=f"{kpis['duration']}",
                    trend="up" if kpis["total_pnl"] >= 0 else "down",
                )
                builder.kpi("Volume", f"${kpis['total_volume']:,.0f}")
                builder.kpi("Fees", f"${kpis['total_fees']:,.2f}")
                builder.kpi("Trades", str(kpis["trade_count"]))

            pnl_fig = getattr(result, "_pnl_fig", None)
            if pnl_fig:
                builder.plotly(pnl_fig)

        if result.table_data:
            builder.table(result.table_data, result.table_columns)

        if result.text:
            builder.markdown(result.text)

        builder.manual_order()
        await builder.save()
    except Exception as e:
        logger.warning(f"Report generation failed: {e}")

    return result
