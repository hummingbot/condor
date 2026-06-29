"""Logs Summary — error/warning diagnostics across all active bots on every
connected server.

Fans out across all the Hummingbot API servers Condor is connected to (via
`asyncio.gather`), queries the bot-orchestration status for every active bot on
each, and mines their `error_logs` / `general_logs`: counts errors & warnings,
finds the last failure time, clusters failures into normalized patterns
(NLP-style message templating), and surfaces the most common failures per bot
and the noisiest loggers. Powers the `log_analyzer` skill's retrospective
diagnostics.
"""

import asyncio
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from config_manager import get_config_manager

CATEGORY = "Bot Analysis"

# Levels we treat as failures (level_no >= 40 is ERROR/CRITICAL in Hummingbot).
_ERROR_LEVELS = {"ERROR", "CRITICAL", "FATAL"}
_WARN_LEVELS = {"WARNING", "WARN"}


class Config(BaseModel):
    """Summarize errors/warnings across active bots: counts, last failure, top patterns."""

    bot_name: str = Field(
        default="",
        description="Limit to one bot (substring match). Empty = all active bots.",
    )
    servers: str = Field(
        default="",
        description="Limit to these servers (comma-separated names, substring match). Empty = all connected servers.",
    )
    include_warnings: bool = Field(
        default=True, description="Include WARNING-level logs in the analysis"
    )
    top_patterns: int = Field(
        default=8, description="How many failure patterns to show in the summary"
    )
    recent_incident_min: int = Field(
        default=15,
        description="Flag a bot as an active incident if its last error is newer than this many minutes",
    )


# --- message normalization (turn a raw log line into a reusable template) -----

_NORMALIZERS = [
    (re.compile(r"x-[A-Za-z0-9]{6,}"), "<ORDER_ID>"),  # hummingbot client order ids
    (re.compile(r"\b0x[0-9a-fA-F]{6,}\b"), "<HEX>"),  # tx hashes / addresses
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<HEX>"),  # long hex blobs
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<UUID>",
    ),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*"), "<TS>"),
    (re.compile(r"\b\d+\.\d+\b"), "<NUM>"),  # floats (prices, pnl)
    (re.compile(r"\b\d+\b"), "<NUM>"),  # ints (counts, retries)
]


def _normalize(msg: str) -> str:
    """Collapse a log message into a pattern template so similar failures cluster."""
    text = str(msg).strip()
    for pattern, repl in _NORMALIZERS:
        text = pattern.sub(repl, text)
    text = re.sub(r"\s+", " ", text)
    return text[:160]


def _short_logger(name: str) -> str:
    """Shorten a dotted logger path to its last two components."""
    parts = str(name).split(".")
    return ".".join(parts[-2:]) if len(parts) > 1 else name


def _ts_to_dt(ts):
    """Coerce a unix epoch (float/int) or ISO string into an aware datetime, or None."""
    if ts in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        s = str(ts).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _ago(dt, now):
    """Human 'time ago' label for a datetime relative to now."""
    if dt is None:
        return "—"
    secs = (now - dt).total_seconds()
    if secs < 0:
        return "just now"
    if secs < 60:
        return f"{int(secs)}s ago"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


def _resolve_servers(cm, context, filter_str: str) -> list:
    """Resolve which connected servers to analyze.

    Scopes to the user's accessible servers when a user id is available
    (Telegram), otherwise falls back to every configured server (web/MCP).
    An optional comma-separated substring filter narrows the set further.
    """
    user_id = None
    if context is not None:
        user_data = context.user_data
        if user_data is None:
            user_data = getattr(context, "_user_data", None)
        if user_data:
            user_id = user_data.get("_user_id")

    all_servers = list(cm.list_servers().keys())
    if user_id:
        names = [s for s in cm.get_accessible_servers(user_id) if s in all_servers]
        names = names or all_servers
    else:
        names = all_servers

    wanted = [s.strip().lower() for s in filter_str.split(",") if s.strip()]
    if wanted:
        names = [n for n in names if any(w in n.lower() for w in wanted)]
    return names


async def _fetch_server_status(cm, server_name: str):
    """Fetch active-bot status for one server. Returns (name, data, error).

    Never raises — connection/API failures are returned as the error string so a
    single unreachable server can't sink the whole gather.
    """
    try:
        client = await cm.get_client(server_name)
        resp = await client.bot_orchestration.get_active_bots_status()
    except Exception as e:  # noqa: BLE001
        return server_name, {}, str(e)
    data = resp.get("data", resp) if isinstance(resp, dict) else {}
    return server_name, (data if isinstance(data, dict) else {}), None


def _entries(raw):
    """Yield (level_name, msg, datetime, logger) tuples from a logs list."""
    for log in raw or []:
        if isinstance(log, dict):
            level = str(log.get("level_name", log.get("level", ""))).upper()
            msg = log.get("msg", log.get("message", ""))
            dt = _ts_to_dt(log.get("timestamp", log.get("time", log.get("ts"))))
            logger = log.get("logger_name", log.get("logger", ""))
        else:
            level, msg, dt, logger = "", str(log), None, ""
        yield level, msg, dt, logger


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str:
    cm = get_config_manager()
    server_names = _resolve_servers(cm, context, config.servers)
    if not server_names:
        return "No servers connected to Condor — nothing to analyze."

    # Fan out: collect every server's active-bot status concurrently.
    results = await asyncio.gather(
        *(_fetch_server_status(cm, name) for name in server_names)
    )

    server_errors = {}  # server -> error string (unreachable / API failure)
    server_data = {}  # server -> {bot_name: info}
    for name, data, err in results:
        if err:
            server_errors[name] = err
        else:
            server_data[name] = data

    if not server_data and server_errors:
        detail = "; ".join(f"{n}: {e}" for n, e in server_errors.items())
        return f"Failed to fetch bot status from all servers — {detail}"

    multi_server = len(server_names) > 1

    now = datetime.now(timezone.utc)
    bot_rows = []  # per-bot summary rows
    global_patterns = Counter()  # normalized error pattern -> count
    pattern_bots = defaultdict(set)  # pattern -> set((server, bot))
    pattern_last = {}  # pattern -> latest datetime
    pattern_logger = defaultdict(Counter)
    total_errors = total_warns = bots_with_errors = 0
    servers_with_errors = set()

    # Flatten (server, bot, info) across every server, then analyze each bot.
    for server_name, data in server_data.items():
        for bot_name, info in data.items():
            if config.bot_name and config.bot_name.lower() not in bot_name.lower():
                continue
            if not isinstance(info, dict):
                continue

            err_count = warn_count = 0
            last_err_dt = None
            bot_patterns = Counter()
            bot_loggers = Counter()

            # error_logs is the curated failure list; general_logs may also carry WARNINGs.
            streams = [info.get("error_logs", [])]
            if config.include_warnings:
                streams.append(info.get("general_logs", []))

            for stream in streams:
                for level, msg, dt, logger in _entries(stream):
                    is_err = level in _ERROR_LEVELS
                    is_warn = level in _WARN_LEVELS
                    if not (is_err or is_warn):
                        continue
                    if is_warn and not config.include_warnings:
                        continue
                    if is_err:
                        err_count += 1
                        pattern = _normalize(msg)
                        bot_patterns[pattern] += 1
                        bot_loggers[_short_logger(logger)] += 1
                        global_patterns[pattern] += 1
                        pattern_bots[pattern].add((server_name, bot_name))
                        pattern_logger[pattern][_short_logger(logger)] += 1
                        if dt and (last_err_dt is None or dt > last_err_dt):
                            last_err_dt = dt
                        if dt and (
                            pattern not in pattern_last or dt > pattern_last[pattern]
                        ):
                            pattern_last[pattern] = dt
                    else:
                        warn_count += 1

            total_errors += err_count
            total_warns += warn_count
            if err_count:
                bots_with_errors += 1
                servers_with_errors.add(server_name)

            top_failure = bot_patterns.most_common(1)[0][0] if bot_patterns else "—"
            top_logger = bot_loggers.most_common(1)[0][0] if bot_loggers else "—"
            incident = bool(
                last_err_dt
                and (now - last_err_dt).total_seconds()
                <= config.recent_incident_min * 60
            )
            row = {
                "Server": server_name[:18],
                "Bot": bot_name[:34],
                "Status": (
                    ("🔴 incident" if incident else "🟢 ok")
                    if err_count
                    else "🟢 clean"
                ),
                "Errors": err_count,
                "Warns": warn_count,
                "Last Error": _ago(last_err_dt, now),
                "Top Failure": top_failure[:48],
                "Source": top_logger,
            }
            if not multi_server:
                row.pop("Server")
            bot_rows.append(row)

    bot_rows.sort(key=lambda r: r["Errors"], reverse=True)

    # Column layouts — the Server column only appears when >1 server was scanned.
    bot_cols = (["Server"] if multi_server else []) + [
        "Bot",
        "Status",
        "Errors",
        "Warns",
        "Last Error",
        "Top Failure",
        "Source",
    ]
    pattern_cols = ["Pattern", "Count", "Bots"]
    if multi_server:
        pattern_cols.append("Servers")
    pattern_cols += ["Last Seen", "Source"]

    # --- failure pattern table (cross-bot, cross-server clustering) -----------
    pattern_rows = []
    for pattern, count in global_patterns.most_common(config.top_patterns):
        top_src = pattern_logger[pattern].most_common(1)
        affected = pattern_bots[pattern]
        row = {
            "Pattern": pattern[:70],
            "Count": count,
            "Bots": len(affected),
            "Last Seen": _ago(pattern_last.get(pattern), now),
            "Source": top_src[0][0] if top_src else "—",
        }
        if multi_server:
            row["Servers"] = len({s for s, _ in affected})
        pattern_rows.append(row)

    # --- text summary ---------------------------------------------------------
    incidents = [r for r in bot_rows if "incident" in r["Status"]]
    analyzed = f"{len(bot_rows)} bot(s) across {len(server_data)} server(s)"
    lines = [
        f"📊 Logs Summary — {analyzed} analyzed @ {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"Errors: {total_errors} | Warnings: {total_warns} | Bots with errors: {bots_with_errors}"
        + (
            f" | Servers with errors: {len(servers_with_errors)}"
            if multi_server
            else ""
        ),
    ]
    if server_errors:
        lines.append(
            "⚠️ Unreachable: "
            + ", ".join(f"{n} ({e})"[:60] for n, e in server_errors.items())
        )
    if incidents:
        lines.append("")
        lines.append(
            f"🔴 Active incidents ({len(incidents)}): last error within {config.recent_incident_min}m"
        )
        for r in incidents:
            loc = f"[{r['Server']}] " if multi_server else ""
            lines.append(
                f"  • {loc}{r['Bot']} — {r['Errors']} err, last {r['Last Error']}: {r['Top Failure']}"
            )
    if pattern_rows:
        lines.append("")
        lines.append("Top failure patterns:")
        for r in pattern_rows[:5]:
            lines.append(
                f"  • [{r['Count']}× / {r['Bots']} bot(s)] {r['Pattern']}  ({r['Source']})"
            )
    if total_errors == 0:
        lines.append("")
        lines.append("✅ No errors found across active bots.")
    summary = "\n".join(lines)

    # --- persistent report ----------------------------------------------------
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("Bot Logs Summary")
        builder.source("routine", "logs_summary").tags(["logs", "diagnostics", "bots"])
        builder.kpi("Total Errors", total_errors)
        builder.kpi("Total Warnings", total_warns)
        builder.kpi("Bots w/ Errors", f"{bots_with_errors}/{len(bot_rows)}")
        builder.kpi("Servers", f"{len(server_data)}/{len(server_names)}")
        builder.kpi("Active Incidents", len(incidents))
        builder.markdown(summary)
        if bot_rows:
            builder.table(bot_rows, bot_cols)
        if pattern_rows:
            builder.table(pattern_rows, pattern_cols)
        builder.manual_order()
        await builder.save()
    except Exception as e:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning(f"Report generation failed: {e}")

    # --- rich inline result ---------------------------------------------------
    try:
        from routines.base import RoutineResult

        return RoutineResult(
            text=summary,
            table_data=bot_rows,
            table_columns=bot_cols,
        )
    except Exception:  # noqa: BLE001
        return summary
