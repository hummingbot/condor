"""Per-session trading agent performance from hummingbot-api executors."""

from __future__ import annotations

CATEGORY = "Bot Analysis"

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_client
from condor.trading_agent.performance import (
    _build_perf_from_rows,
    _executor_row,
    _extract_executors_list,
)
from routines.base import RoutineResult

logger = logging.getLogger(__name__)

_DATA_ROOT = Path(__file__).resolve().parent.parent / "trading_agents"
_TICK_RE = re.compile(
    r"tick#1\s*\|\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})",
    re.IGNORECASE,
)
_EXEC_ID_RE = re.compile(r"\b([1-9A-HJ-NP-Za-km-z]{40,50})\b")


class Config(BaseModel):
    """Report realized PnL and trade stats for trading agent sessions (API + session start filter)."""

    strategy_slug: str = Field(
        default="macdbb_scanner_aggressive",
        description="Strategy folder name under trading_agents/",
    )
    session_num: int | None = Field(
        default=None,
        description="Single session number (e.g. 5). Omit to summarize all sessions.",
    )
    connector_names: list[str] = Field(
        default_factory=lambda: ["hyperliquid_perpetual"],
        description="Filter executors by connector",
    )


def _parse_session_start(session_dir: Path) -> datetime | None:
    journal = session_dir / "journal.md"
    if journal.is_file():
        text = journal.read_text(encoding="utf-8", errors="replace")
        m = _TICK_RE.search(text)
        if m:
            try:
                return datetime.strptime(
                    f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M"
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    cfg = session_dir / "config.yml"
    if cfg.is_file():
        try:
            return datetime.fromtimestamp(cfg.stat().st_mtime, tz=timezone.utc)
        except OSError:
            pass
    try:
        return datetime.fromtimestamp(session_dir.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _parse_created_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _journal_executor_ids(session_dir: Path) -> set[str]:
    journal = session_dir / "journal.md"
    if not journal.is_file():
        return set()
    text = journal.read_text(encoding="utf-8", errors="replace")
    return set(_EXEC_ID_RE.findall(text))


def _side_label(raw: Any) -> str:
    if raw in (1, "1"):
        return "LONG"
    if raw in (2, "2"):
        return "SHORT"
    s = str(raw or "").upper()
    if s in ("BUY", "LONG"):
        return "LONG"
    if s in ("SELL", "SHORT"):
        return "SHORT"
    return s or "?"


async def _fetch_executor_pages(
    client: Any,
    controller_id: str,
    connector_names: list[str],
) -> list[dict]:
    rows: list[dict] = []
    cursor: str | None = None
    page_size = 50
    for _ in range(200):
        kwargs: dict[str, Any] = {
            "controller_ids": [controller_id],
            "limit": page_size,
        }
        if connector_names:
            kwargs["connector_names"] = connector_names
        if cursor:
            kwargs["cursor"] = cursor
        result = await client.executors.search_executors(**kwargs)
        page = _extract_executors_list(result)
        for ex in page:
            if isinstance(ex, dict):
                rows.append(ex)
        next_cursor = None
        if isinstance(result, dict):
            next_cursor = result.get("next_cursor") or result.get("cursor")
            pagination = result.get("pagination")
            if isinstance(pagination, dict):
                next_cursor = next_cursor or pagination.get("next_cursor") or pagination.get(
                    "cursor"
                )
        if not next_cursor or len(page) < page_size:
            break
        cursor = next_cursor
    return rows


def _summarize_session(
    session_num: int,
    controller_id: str,
    session_start: datetime | None,
    all_raw: list[dict],
    journal_ids: set[str],
) -> dict[str, Any]:
    in_session: list[dict] = []
    excluded = 0
    for ex in all_raw:
        created = _parse_created_at(ex.get("created_at"))
        if session_start and created and created < session_start:
            excluded += 1
            continue
        in_session.append(ex)

    rows = [_executor_row(ex) for ex in in_session]
    perf = _build_perf_from_rows(controller_id, rows)

    api_ids = {r["id"] for r in rows if r["id"]}
    orphans = sorted(journal_ids - api_ids)

    closed = [r for r in rows if r["status"] != "RUNNING"]
    sl_count = sum(1 for r in closed if "STOP" in r["close_type"].upper())
    tp_count = sum(1 for r in closed if "TAKE" in r["close_type"].upper())

    trade_rows: list[dict[str, Any]] = []
    for ex in in_session:
        row = _executor_row(ex)
        created = _parse_created_at(ex.get("created_at"))
        closed = _parse_created_at(ex.get("closed_at"))
        hold_h = ""
        if created and closed:
            hold_h = f"{(closed - created).total_seconds() / 3600:.1f}h"
        trade_rows.append(
            {
                "Session": session_num,
                "Pair": row["pair"],
                "Side": _side_label(row["side"]),
                "Status": row["status"],
                "Close": row["close_type"] or "—",
                "PnL $": round(row["pnl"], 2),
                "Volume $": round(row["volume"], 0),
                "Hold": hold_h,
                "Created": (created.isoformat()[:19] if created else "—"),
            }
        )

    return {
        "session_num": session_num,
        "controller_id": controller_id,
        "session_start": session_start.isoformat() if session_start else None,
        "perf": perf,
        "trade_rows": trade_rows,
        "excluded_pre_session": excluded,
        "orphan_journal_ids": orphans,
        "sl_count": sl_count,
        "tp_count": tp_count,
    }


def _format_session_block(s: dict[str, Any]) -> list[str]:
    p = s["perf"]
    lines = [
        f"### Session {s['session_num']} (`{s['controller_id']}`)",
        f"Session start (UTC): {s['session_start'] or 'unknown'}",
        f"Trades in window: {p.trade_count} (closed {p.closed_count}, open {p.open_count})",
        f"Realized PnL: ${p.realized_pnl:+.2f} | Win rate: {p.win_rate:.0%}",
        f"Volume: ${p.volume:.0f} | Fees: ${p.fees:.2f}",
        f"Exits: {s['sl_count']} SL, {s['tp_count']} TP",
    ]
    if s["excluded_pre_session"]:
        lines.append(
            f"Pre-session trades excluded (controller_id reuse): {s['excluded_pre_session']}"
        )
    if s["orphan_journal_ids"]:
        lines.append(
            "Journal executor IDs not in API window: "
            + ", ".join(s["orphan_journal_ids"][:5])
            + ("..." if len(s["orphan_journal_ids"]) > 5 else "")
        )
    return lines


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str | RoutineResult:
    chat_id = context._chat_id if hasattr(context, "_chat_id") else None
    client = await get_client(chat_id, context=context)
    if not client:
        return "No server available. Configure servers in /config."

    agent_dir = _DATA_ROOT / config.strategy_slug
    if not agent_dir.is_dir():
        return f"Strategy not found: {config.strategy_slug}"

    sessions_dir = agent_dir / "sessions"
    if not sessions_dir.is_dir():
        return f"No sessions directory for {config.strategy_slug}"

    session_nums: list[int] = []
    if config.session_num is not None:
        session_nums = [config.session_num]
    else:
        for p in sessions_dir.iterdir():
            if p.is_dir() and p.name.startswith("session_"):
                try:
                    session_nums.append(int(p.name.split("_", 1)[1]))
                except ValueError:
                    continue
        session_nums.sort()

    if not session_nums:
        return "No sessions found."

    summaries: list[dict[str, Any]] = []
    all_trade_rows: list[dict[str, Any]] = []

    for num in session_nums:
        session_dir = sessions_dir / f"session_{num}"
        if not session_dir.is_dir():
            continue
        controller_id = f"{config.strategy_slug}_{num}"
        session_start = _parse_session_start(session_dir)
        journal_ids = _journal_executor_ids(session_dir)

        try:
            raw = await _fetch_executor_pages(
                client, controller_id, config.connector_names
            )
        except Exception as e:
            logger.warning("Executor fetch failed for %s: %s", controller_id, e)
            raw = []

        s = _summarize_session(
            num, controller_id, session_start, raw, journal_ids
        )
        summaries.append(s)
        all_trade_rows.extend(s["trade_rows"])

    if not summaries:
        return "No session data to report."

    total_realized = sum(s["perf"].realized_pnl for s in summaries)
    total_closed = sum(s["perf"].closed_count for s in summaries)
    total_wins = sum(
        sum(1 for r in s["perf"].executors if r["status"] != "RUNNING" and r["pnl"] > 0)
        for s in summaries
    )
    total_sl = sum(s["sl_count"] for s in summaries)
    total_tp = sum(s["tp_count"] for s in summaries)
    total_excluded = sum(s["excluded_pre_session"] for s in summaries)
    win_rate = total_wins / total_closed if total_closed else 0.0

    text_lines = [
        f"Agent session performance — {config.strategy_slug}",
        f"Connectors: {', '.join(config.connector_names)}",
        "",
    ]
    for s in summaries:
        text_lines.extend(_format_session_block(s))
        text_lines.append("")

    text_lines.extend(
        [
            "### Rollup",
            f"Sessions: {len(summaries)} | Closed trades: {total_closed}",
            f"Total realized PnL: ${total_realized:+.2f} | Win rate: {win_rate:.0%}",
            f"Stop-loss / take-profit exits: {total_sl} / {total_tp}",
        ]
    )
    if total_excluded:
        text_lines.append(f"Total pre-session executors excluded: {total_excluded}")

    rollup_row = {
        "Session": "TOTAL",
        "Pair": "",
        "Side": "",
        "Status": "",
        "Close": "",
        "PnL $": round(total_realized, 2),
        "Volume $": round(sum(s["perf"].volume for s in summaries), 0),
        "Hold": "",
        "Created": "",
    }
    table_data = all_trade_rows + [rollup_row]
    table_columns = [
        "Session",
        "Pair",
        "Side",
        "Status",
        "Close",
        "PnL $",
        "Volume $",
        "Hold",
        "Created",
    ]

    sections = [
        {"type": "kpi", "label": "Realized PnL", "value": f"${total_realized:+.2f}"},
        {"type": "kpi", "label": "Win rate", "value": f"{win_rate:.0%}"},
        {"type": "kpi", "label": "Closed trades", "value": str(total_closed)},
        {"type": "kpi", "label": "SL / TP", "value": f"{total_sl} / {total_tp}"},
    ]

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"Session performance: {config.strategy_slug}")
        builder.source("routine", "agent_session_performance").tags(
            ["trading-agent", "performance"]
        )
        builder.kpi("Realized PnL", f"${total_realized:+.2f}")
        builder.kpi("Win rate", f"{win_rate:.0%}")
        builder.markdown("\n".join(text_lines))
        if table_data:
            builder.table(table_data, columns=table_columns)
        builder.save()
    except Exception as e:
        logger.warning("Report generation failed: %s", e)

    return RoutineResult(
        text="\n".join(text_lines),
        table_data=table_data,
        table_columns=table_columns,
        sections=sections,
    )
