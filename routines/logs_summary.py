"""Logs Summary — error/warning diagnostics over the Condor process log.

Reads the condor-native log file (``store/condor.log``), mines ERROR/WARNING
lines, counts failures per logger, finds the last failure time, and clusters
failures into normalized patterns (NLP-style message templating). Powers the
`log_analyzer` skill's retrospective diagnostics.

(The Hummingbot bot-log fan-out was removed with the Hummingbot deletion —
simplification plan §9.2; this now analyzes Condor's own log.)
"""

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

CATEGORY = "Diagnostics"

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOG = _PROJECT_ROOT / "store" / "condor.log"

_ERROR_LEVELS = {"ERROR", "CRITICAL", "FATAL"}
_WARN_LEVELS = {"WARNING", "WARN"}

# "2026-07-15 08:55:28,161 - condor.control.server - INFO - message"
_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d+)?\s+-\s+"
    r"(?P<logger>\S+)\s+-\s+(?P<level>[A-Z]+)\s+-\s+(?P<msg>.*)$"
)


class Config(BaseModel):
    """Summarize errors/warnings in the Condor log: counts, last failure, top patterns."""

    log_file: str = Field(
        default="",
        description=f"Log file to analyze (default: {_DEFAULT_LOG})",
    )
    logger_filter: str = Field(
        default="",
        description="Limit to loggers containing this substring (e.g. 'executors'). Empty = all.",
    )
    max_lines: int = Field(
        default=20000, description="Analyze at most this many lines from the end of the file"
    )
    include_warnings: bool = Field(
        default=True, description="Include WARNING-level lines in the analysis"
    )
    top_patterns: int = Field(
        default=8, description="How many failure patterns to show in the summary"
    )
    recent_incident_min: int = Field(
        default=15,
        description="Flag a logger as an active incident if its last error is newer than this many minutes",
    )


# --- message normalization (turn a raw log line into a reusable template) -----

_NORMALIZERS = [
    (re.compile(r"\b0x[0-9a-fA-F]{6,}\b"), "<HEX>"),  # tx hashes / addresses
    (re.compile(r"\b[0-9a-fA-F]{16,}\b"), "<HEX>"),  # long hex blobs
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<UUID>",
    ),
    (re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b"), "<ULID>"),  # run/executor ids
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


def _tail_lines(path: Path, max_lines: int) -> list[str]:
    with open(path, "r", errors="replace") as f:
        lines = f.readlines()
    return lines[-max_lines:] if len(lines) > max_lines else lines


def _entries(lines: list[str]):
    """Yield (level, msg, datetime, logger) tuples from raw log lines.

    Non-matching lines (tracebacks, uvicorn access lines) are skipped —
    the leading ERROR line of a traceback still carries the message.
    """
    for line in lines:
        m = _LINE_RE.match(line.rstrip("\n"))
        if not m:
            continue
        try:
            dt = datetime.strptime(m["ts"], "%Y-%m-%d %H:%M:%S").astimezone()
            dt = dt.astimezone(timezone.utc)
        except ValueError:
            dt = None
        yield m["level"].upper(), m["msg"], dt, m["logger"]


async def run(config: Config, context=None) -> str:
    log_path = Path(config.log_file) if config.log_file else _DEFAULT_LOG
    if not log_path.exists():
        return f"Log file not found: {log_path}"

    lines = _tail_lines(log_path, config.max_lines)
    now = datetime.now(timezone.utc)

    per_logger: dict[str, dict] = defaultdict(
        lambda: {"errors": 0, "warns": 0, "last_err": None, "patterns": Counter()}
    )
    global_patterns = Counter()
    pattern_loggers = defaultdict(set)
    pattern_last = {}
    total_errors = total_warns = 0

    for level, msg, dt, logger_name in _entries(lines):
        if config.logger_filter and config.logger_filter.lower() not in logger_name.lower():
            continue
        is_err = level in _ERROR_LEVELS
        is_warn = level in _WARN_LEVELS
        if not (is_err or is_warn):
            continue
        if is_warn and not config.include_warnings:
            continue

        entry = per_logger[logger_name]
        if is_err:
            total_errors += 1
            entry["errors"] += 1
            pattern = _normalize(msg)
            entry["patterns"][pattern] += 1
            global_patterns[pattern] += 1
            pattern_loggers[pattern].add(_short_logger(logger_name))
            if dt and (entry["last_err"] is None or dt > entry["last_err"]):
                entry["last_err"] = dt
            if dt and (pattern not in pattern_last or dt > pattern_last[pattern]):
                pattern_last[pattern] = dt
        else:
            total_warns += 1
            entry["warns"] += 1

    # --- per-logger summary rows ----------------------------------------------
    logger_rows = []
    for logger_name, entry in per_logger.items():
        if not entry["errors"] and not entry["warns"]:
            continue
        incident = bool(
            entry["last_err"]
            and (now - entry["last_err"]).total_seconds()
            <= config.recent_incident_min * 60
        )
        top_failure = (
            entry["patterns"].most_common(1)[0][0] if entry["patterns"] else "—"
        )
        logger_rows.append(
            {
                "Logger": _short_logger(logger_name)[:34],
                "Status": (
                    ("🔴 incident" if incident else "🟢 ok")
                    if entry["errors"]
                    else "🟢 clean"
                ),
                "Errors": entry["errors"],
                "Warns": entry["warns"],
                "Last Error": _ago(entry["last_err"], now),
                "Top Failure": top_failure[:48],
            }
        )
    logger_rows.sort(key=lambda r: r["Errors"], reverse=True)
    logger_cols = ["Logger", "Status", "Errors", "Warns", "Last Error", "Top Failure"]

    # --- failure pattern table (cross-logger clustering) -----------------------
    pattern_rows = []
    for pattern, count in global_patterns.most_common(config.top_patterns):
        pattern_rows.append(
            {
                "Pattern": pattern[:70],
                "Count": count,
                "Loggers": len(pattern_loggers[pattern]),
                "Last Seen": _ago(pattern_last.get(pattern), now),
            }
        )
    pattern_cols = ["Pattern", "Count", "Loggers", "Last Seen"]

    # --- text summary ---------------------------------------------------------
    incidents = [r for r in logger_rows if "incident" in r["Status"]]
    lines_out = [
        f"📊 Logs Summary — {log_path.name}, last {len(lines)} lines "
        f"@ {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"Errors: {total_errors} | Warnings: {total_warns} | "
        f"Loggers with errors: {sum(1 for r in logger_rows if r['Errors'])}",
    ]
    if incidents:
        lines_out.append("")
        lines_out.append(
            f"🔴 Active incidents ({len(incidents)}): last error within {config.recent_incident_min}m"
        )
        for r in incidents:
            lines_out.append(
                f"  • {r['Logger']} — {r['Errors']} err, last {r['Last Error']}: {r['Top Failure']}"
            )
    if pattern_rows:
        lines_out.append("")
        lines_out.append("Top failure patterns:")
        for r in pattern_rows[:5]:
            lines_out.append(
                f"  • [{r['Count']}× / {r['Loggers']} logger(s)] {r['Pattern']}"
            )
    if total_errors == 0:
        lines_out.append("")
        lines_out.append("✅ No errors found in the analyzed window.")
    summary = "\n".join(lines_out)

    # --- persistent report ----------------------------------------------------
    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder("Condor Logs Summary")
        builder.source("routine", "logs_summary").tags(["logs", "diagnostics"])
        builder.kpi("Total Errors", total_errors)
        builder.kpi("Total Warnings", total_warns)
        builder.kpi("Loggers w/ Errors", sum(1 for r in logger_rows if r["Errors"]))
        builder.kpi("Active Incidents", len(incidents))
        builder.markdown(summary)
        if logger_rows:
            builder.table(logger_rows, logger_cols)
        if pattern_rows:
            builder.table(pattern_rows, pattern_cols)
        builder.manual_order()
        await builder.save()
    except Exception as e:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning(f"Report generation failed: {e}")

    # --- rich inline result ---------------------------------------------------
    from routines.base import RoutineResult

    return RoutineResult(
        text=summary,
        table_data=logger_rows,
        table_columns=logger_cols,
    )
