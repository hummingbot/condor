from __future__ import annotations

import datetime as dt
import html
import json
import re

from routines.macdbb_replay.models import ParsedReport, ReportMeta
from routines.macdbb_replay.paths import REPORTS_DIR, REPORTS_INDEX_PATH

_PAIR_TITLE_RE = re.compile(r"MACD\+BB:\s+([A-Z0-9:-]+)\s+\((1h|4h)\)")
_FIRST_TABLE_ROW_RE = re.compile(r"<tbody><tr>(.*?)</tr></tbody>", re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_COND_ROW_RE = re.compile(
    r"<tr><td>([^<]+)</td><td>([^<]+)</td><td>(True|False)</td></tr>", re.DOTALL
)


def parse_dt(value: str) -> dt.datetime:
    if "T" in value:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            dt.timezone.utc
        )
    return dt.datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)


def extract_td_value(raw_value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", raw_value)).strip()


def parse_report_html(report_html: str) -> ParsedReport | None:
    first_row_match = _FIRST_TABLE_ROW_RE.search(report_html)
    if not first_row_match:
        return None
    values = [extract_td_value(value) for value in _TD_RE.findall(first_row_match.group(1))]
    if len(values) < 12:
        return None

    pair = values[0]
    interval = values[1]
    signal = values[2]
    price = float(values[3])
    bb_pos_pct = float(values[4])
    bb_mid = float(values[5])
    bb_upper = float(values[6])
    macd = float(values[7])
    signal_line = float(values[8])
    histogram = float(values[9])
    trend = values[10].lower()
    momentum = values[11].lower()

    condition_map: dict[tuple[str, str], bool] = {}
    for rule, condition, met in _COND_ROW_RE.findall(report_html):
        condition_map[(rule.strip(), condition.strip())] = met.strip().lower() == "true"

    return ParsedReport(
        pair=pair,
        interval=interval,
        signal=signal,
        price=price,
        bb_pos_pct=bb_pos_pct,
        bb_mid=bb_mid,
        bb_upper=bb_upper,
        macd=macd,
        signal_line=signal_line,
        histogram=histogram,
        trend=trend,
        momentum=momentum,
        bullish_cross=condition_map.get(("LONG (2/2)", "Bullish crossover"), False),
        price_le_mid=condition_map.get(("LONG (2/2)", "Price <= midBB"), False),
        bearish_cross=condition_map.get(("SHORT (3/3)", "Bearish crossover"), False),
        price_ge_upper=condition_map.get(("SHORT (3/3)", "Price >= upperBB"), False),
        macd_lt_zero=condition_map.get(("SHORT (3/3)", "MACD < 0"), False),
    )


def load_reports_index() -> list[ReportMeta]:
    if not REPORTS_INDEX_PATH.exists():
        return []
    raw_entries = json.loads(REPORTS_INDEX_PATH.read_text(encoding="utf-8"))
    reports: list[ReportMeta] = []
    for entry in raw_entries:
        if entry.get("source_name") != "macd_bb_analysis":
            continue
        title_match = _PAIR_TITLE_RE.search(entry.get("title", ""))
        if not title_match:
            continue
        reports.append(
            ReportMeta(
                report_id=entry["id"],
                filename=entry["filename"],
                created_at=parse_dt(entry["created_at"]),
                pair=title_match.group(1),
                interval=title_match.group(2),
            )
        )
    return reports


def build_reports_by_pair(reports: list[ReportMeta]) -> dict[str, list[ReportMeta]]:
    by_pair: dict[str, list[ReportMeta]] = {}
    for report in reports:
        by_pair.setdefault(report.pair, []).append(report)
    for pair in by_pair:
        by_pair[pair].sort(key=lambda item: item.created_at)
    return by_pair


def nearest_report(
    reports_by_pair: dict[str, list[ReportMeta]],
    pair: str,
    tick_time: dt.datetime,
    max_window_minutes: int,
    interval: str = "1h",
) -> ReportMeta | None:
    candidates = reports_by_pair.get(pair, [])
    if not candidates:
        return None
    max_delta = dt.timedelta(minutes=max_window_minutes)
    nearest: tuple[dt.timedelta, ReportMeta] | None = None
    for candidate in candidates:
        if candidate.interval != interval:
            continue
        delta = abs(candidate.created_at - tick_time)
        if delta > max_delta:
            continue
        if nearest is None or delta < nearest[0]:
            nearest = (delta, candidate)
    return nearest[1] if nearest else None


def load_parsed_report(report_meta: ReportMeta) -> ParsedReport | None:
    report_path = REPORTS_DIR / report_meta.filename
    if not report_path.exists():
        return None
    return parse_report_html(report_path.read_text(encoding="utf-8"))
