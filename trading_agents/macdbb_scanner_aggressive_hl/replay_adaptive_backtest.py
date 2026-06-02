#!/usr/bin/env python3
"""Replay session decisions using persisted MACD+BB routine reports.

This utility reconstructs per-tick, per-pair signal snapshots from:
1) session journal (`journal.md`) for tick timeline + reviewed pairs
2) reports index + report HTML payloads for numeric indicator values

It then applies the current adaptive scoring rules from `agent.md` and exports:
- `adaptive_replay_per_pair.csv`
- `adaptive_replay_per_tick.csv`
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path


TICK_RE = re.compile(r"- tick#(\d+)\s+\|\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+\|")
DECISION_RE = re.compile(r"- \*\*#(\d+)\*\*.*")
MACD_PAIRS_RE = re.compile(r"macd_pairs=([A-Z0-9:-]+(?:,[A-Z0-9:-]+)*)")
NEUTRAL_STREAK_RE = re.compile(r"neutral_pressure_streak=(\d+)")
ENTRY_CLASS_RE = re.compile(r"entry_class=([a-zA-Z0-9_]+)")

PAIR_TITLE_RE = re.compile(r"MACD\+BB:\s+([A-Z0-9:-]+)\s+\((1h|4h)\)")

# Extract all <td> values from the first metrics table row.
FIRST_TABLE_ROW_RE = re.compile(r"<tbody><tr>(.*?)</tr></tbody>", re.DOTALL)
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)

# Extract boolean checks from "Entry Rules Check" table.
COND_ROW_RE = re.compile(
    r"<tr><td>([^<]+)</td><td>([^<]+)</td><td>(True|False)</td></tr>", re.DOTALL
)


@dataclass
class TickMeta:
    tick: int
    timestamp: dt.datetime
    macd_pairs: list[str]
    neutral_pressure_streak: int | None
    entry_class: str | None


@dataclass
class ReportMeta:
    report_id: str
    filename: str
    created_at: dt.datetime
    pair: str
    interval: str


@dataclass
class ParsedReport:
    pair: str
    interval: str
    signal: str
    price: float
    bb_pos_pct: float
    bb_mid: float
    bb_upper: float
    macd: float
    signal_line: float
    histogram: float
    trend: str
    momentum: str
    bullish_cross: bool
    price_le_mid: bool
    bearish_cross: bool
    price_ge_upper: bool
    macd_lt_zero: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay adaptive logic from journal + reports."
    )
    parser.add_argument(
        "--journal",
        default="trading_agents/macdbb_scanner_aggressive_hl/sessions/session_35/journal.md",
        help="Path to session journal.md",
    )
    parser.add_argument(
        "--reports-index",
        default="reports/reports_index.json",
        help="Path to reports index JSON",
    )
    parser.add_argument(
        "--reports-dir",
        default="reports",
        help="Directory containing report HTML files",
    )
    parser.add_argument(
        "--time-window-min",
        type=int,
        default=25,
        help="Max time distance in minutes for matching report to tick/pair",
    )
    parser.add_argument(
        "--output-dir",
        default="trading_agents/macdbb_scanner_aggressive_hl/sessions/session_35",
        help="Output directory for CSV files",
    )
    return parser.parse_args()


def parse_dt(value: str) -> dt.datetime:
    if "T" in value:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            dt.timezone.utc
        )
    return dt.datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_journal_ticks(journal_text: str) -> dict[int, TickMeta]:
    tick_time: dict[int, dt.datetime] = {}
    for match in TICK_RE.finditer(journal_text):
        tick = int(match.group(1))
        timestamp = parse_dt(match.group(2))
        tick_time[tick] = timestamp

    tick_meta: dict[int, TickMeta] = {}
    for line in journal_text.splitlines():
        decision_match = DECISION_RE.match(line)
        if not decision_match:
            continue
        tick = int(decision_match.group(1))
        pairs_match = MACD_PAIRS_RE.search(line)
        pairs = pairs_match.group(1).split(",") if pairs_match else []
        streak_match = NEUTRAL_STREAK_RE.search(line)
        entry_match = ENTRY_CLASS_RE.search(line)
        if tick not in tick_time:
            continue
        tick_meta[tick] = TickMeta(
            tick=tick,
            timestamp=tick_time[tick],
            macd_pairs=pairs,
            neutral_pressure_streak=int(streak_match.group(1))
            if streak_match
            else None,
            entry_class=entry_match.group(1) if entry_match else None,
        )
    return tick_meta


def load_reports_index(index_path: Path) -> list[ReportMeta]:
    raw = json.loads(index_path.read_text(encoding="utf-8"))
    reports: list[ReportMeta] = []
    for item in raw:
        if item.get("source_name") != "macd_bb_analysis":
            continue
        title = item.get("title", "")
        title_match = PAIR_TITLE_RE.search(title)
        if not title_match:
            continue
        pair = title_match.group(1)
        interval = title_match.group(2)
        reports.append(
            ReportMeta(
                report_id=item["id"],
                filename=item["filename"],
                created_at=parse_dt(item["created_at"]),
                pair=pair,
                interval=interval,
            )
        )
    return reports


def extract_td_value(raw_value: str) -> str:
    cleaned = re.sub(r"<[^>]+>", "", raw_value)
    cleaned = html.unescape(cleaned).strip()
    return cleaned


def parse_bool(value: str) -> bool:
    return value.strip().lower() == "true"


def parse_report_html(report_html: str) -> ParsedReport | None:
    first_row_match = FIRST_TABLE_ROW_RE.search(report_html)
    if not first_row_match:
        return None
    tds = [extract_td_value(v) for v in TD_RE.findall(first_row_match.group(1))]
    if len(tds) < 12:
        return None
    (
        pair,
        interval,
        signal,
        price,
        bb_pos_pct,
        bb_mid,
        bb_upper,
        macd,
        signal_line,
        histogram,
        trend,
        momentum,
    ) = tds[:12]

    conditions: dict[tuple[str, str], bool] = {}
    for rule, condition, met in COND_ROW_RE.findall(report_html):
        conditions[(rule.strip(), condition.strip())] = parse_bool(met)

    return ParsedReport(
        pair=pair,
        interval=interval,
        signal=signal,
        price=float(price),
        bb_pos_pct=float(bb_pos_pct),
        bb_mid=float(bb_mid),
        bb_upper=float(bb_upper),
        macd=float(macd),
        signal_line=float(signal_line),
        histogram=float(histogram),
        trend=trend.lower(),
        momentum=momentum.lower(),
        bullish_cross=conditions.get(("LONG (2/2)", "Bullish crossover"), False),
        price_le_mid=conditions.get(("LONG (2/2)", "Price <= midBB"), False),
        bearish_cross=conditions.get(("SHORT (3/3)", "Bearish crossover"), False),
        price_ge_upper=conditions.get(("SHORT (3/3)", "Price >= upperBB"), False),
        macd_lt_zero=conditions.get(("SHORT (3/3)", "MACD < 0"), False),
    )


def nearest_report_for_pair(
    reports_by_pair: dict[str, list[ReportMeta]],
    pair: str,
    tick_ts: dt.datetime,
    max_minutes: int,
) -> ReportMeta | None:
    candidates = reports_by_pair.get(pair, [])
    if not candidates:
        return None
    window = dt.timedelta(minutes=max_minutes)
    nearest: tuple[dt.timedelta, ReportMeta] | None = None
    for candidate in candidates:
        if candidate.interval != "1h":
            continue
        delta = abs(candidate.created_at - tick_ts)
        if delta > window:
            continue
        if nearest is None or delta < nearest[0]:
            nearest = (delta, candidate)
    return nearest[1] if nearest else None


def compute_metrics(parsed: ParsedReport) -> dict[str, float | bool]:
    macd_gap_ratio = abs(parsed.macd - parsed.signal_line) / max(
        abs(parsed.signal_line), 1e-6
    )
    hist_ratio = abs(parsed.histogram) / max(abs(parsed.macd), 1e-6)

    formal_long = parsed.bullish_cross or (
        parsed.price_le_mid
        and parsed.trend == "bullish"
        and parsed.momentum == "increasing"
        and parsed.histogram > 0
    )
    formal_short = (parsed.bearish_cross and parsed.macd_lt_zero) or (
        parsed.price_ge_upper
        and parsed.trend == "bearish"
        and parsed.momentum == "decreasing"
        and parsed.histogram < 0
    )
    has_formal = formal_long or formal_short

    adaptive_long_eligible = parsed.trend == "bullish" and parsed.bb_pos_pct <= 48
    adaptive_short_eligible = parsed.trend == "bearish" and parsed.bb_pos_pct >= 72
    extreme_long_candidate = parsed.trend == "bullish" and parsed.bb_pos_pct <= 35
    extreme_short_candidate = parsed.trend == "bearish" and parsed.bb_pos_pct >= 85
    strength_gate = macd_gap_ratio >= 0.08 or hist_ratio >= 0.12

    hist_sign_long = 0.35 if parsed.histogram >= 0 else -0.35
    hist_sign_short = 0.35 if parsed.histogram <= 0 else -0.35
    momentum_bonus_long = 0.20 if parsed.momentum == "increasing" else -0.10
    momentum_bonus_short = 0.20 if parsed.momentum == "decreasing" else -0.10

    adaptive_strength_long = (
        min(1.4, max(0.0, (50.0 - parsed.bb_pos_pct) / 12.0))
        + min(1.0, macd_gap_ratio)
        + min(0.6, hist_ratio)
        + hist_sign_long
        + momentum_bonus_long
    )
    adaptive_strength_short = (
        min(1.4, max(0.0, (parsed.bb_pos_pct - 70.0) / 12.0))
        + min(1.0, macd_gap_ratio)
        + min(0.6, hist_ratio)
        + hist_sign_short
        + momentum_bonus_short
    )

    long_open_threshold = 2.15 if extreme_long_candidate else 2.40
    short_open_threshold = 2.15 if extreme_short_candidate else 2.40

    adaptive_long_open = (
        adaptive_long_eligible
        and strength_gate
        and adaptive_strength_long >= long_open_threshold
        and not has_formal
    )
    adaptive_short_open = (
        adaptive_short_eligible
        and strength_gate
        and adaptive_strength_short >= short_open_threshold
        and not has_formal
    )

    return {
        "macd_gap_ratio": macd_gap_ratio,
        "hist_ratio": hist_ratio,
        "formal_long": formal_long,
        "formal_short": formal_short,
        "has_formal": has_formal,
        "adaptive_long_eligible": adaptive_long_eligible,
        "adaptive_short_eligible": adaptive_short_eligible,
        "extreme_long_candidate": extreme_long_candidate,
        "extreme_short_candidate": extreme_short_candidate,
        "strength_gate": strength_gate,
        "hist_sign_long": hist_sign_long,
        "hist_sign_short": hist_sign_short,
        "momentum_bonus_long": momentum_bonus_long,
        "momentum_bonus_short": momentum_bonus_short,
        "adaptive_strength_long": adaptive_strength_long,
        "adaptive_strength_short": adaptive_strength_short,
        "long_open_threshold": long_open_threshold,
        "short_open_threshold": short_open_threshold,
        "adaptive_long_open": adaptive_long_open,
        "adaptive_short_open": adaptive_short_open,
    }


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    journal_path = Path(args.journal)
    reports_index_path = Path(args.reports_index)
    reports_dir = Path(args.reports_dir)
    output_dir = Path(args.output_dir)

    journal_text = read_text(journal_path)
    tick_meta = parse_journal_ticks(journal_text)
    reports = load_reports_index(reports_index_path)

    reports_by_pair: dict[str, list[ReportMeta]] = {}
    for report in reports:
        reports_by_pair.setdefault(report.pair, []).append(report)
    for pair in reports_by_pair:
        reports_by_pair[pair].sort(key=lambda report: report.created_at)

    per_pair_rows: list[dict] = []
    per_tick_rows: list[dict] = []

    for tick in sorted(tick_meta):
        meta = tick_meta[tick]
        if not meta.macd_pairs:
            continue
        tick_candidates: list[tuple[str, float, float]] = []
        for pair in meta.macd_pairs:
            matched = nearest_report_for_pair(
                reports_by_pair, pair, meta.timestamp, args.time_window_min
            )
            if matched is None:
                per_pair_rows.append(
                    {
                        "tick": tick,
                        "tick_time_utc": meta.timestamp.isoformat(),
                        "pair": pair,
                        "report_id": "",
                        "report_time_utc": "",
                        "match_ok": 0,
                        "note": "no report within time window",
                    }
                )
                continue

            html_path = reports_dir / matched.filename
            if not html_path.exists():
                per_pair_rows.append(
                    {
                        "tick": tick,
                        "tick_time_utc": meta.timestamp.isoformat(),
                        "pair": pair,
                        "report_id": matched.report_id,
                        "report_time_utc": matched.created_at.isoformat(),
                        "match_ok": 0,
                        "note": "report file missing",
                    }
                )
                continue

            parsed = parse_report_html(read_text(html_path))
            if parsed is None:
                per_pair_rows.append(
                    {
                        "tick": tick,
                        "tick_time_utc": meta.timestamp.isoformat(),
                        "pair": pair,
                        "report_id": matched.report_id,
                        "report_time_utc": matched.created_at.isoformat(),
                        "match_ok": 0,
                        "note": "failed to parse report html",
                    }
                )
                continue

            metrics = compute_metrics(parsed)
            max_score = max(
                float(metrics["adaptive_strength_long"]),
                float(metrics["adaptive_strength_short"]),
            )
            tick_candidates.append(
                (
                    pair,
                    float(metrics["adaptive_strength_long"]),
                    float(metrics["adaptive_strength_short"]),
                )
            )
            per_pair_rows.append(
                {
                    "tick": tick,
                    "tick_time_utc": meta.timestamp.isoformat(),
                    "pair": pair,
                    "report_id": matched.report_id,
                    "report_time_utc": matched.created_at.isoformat(),
                    "entry_class_journal": meta.entry_class or "",
                    "neutral_pressure_streak": meta.neutral_pressure_streak
                    if meta.neutral_pressure_streak is not None
                    else "",
                    "signal": parsed.signal,
                    "bb_pos_pct": round(parsed.bb_pos_pct, 2),
                    "macd": round(parsed.macd, 6),
                    "signal_line": round(parsed.signal_line, 6),
                    "histogram": round(parsed.histogram, 6),
                    "trend": parsed.trend,
                    "momentum": parsed.momentum,
                    "macd_gap_ratio": round(float(metrics["macd_gap_ratio"]), 4),
                    "hist_ratio": round(float(metrics["hist_ratio"]), 4),
                    "formal_long": int(bool(metrics["formal_long"])),
                    "formal_short": int(bool(metrics["formal_short"])),
                    "adaptive_long_eligible": int(bool(metrics["adaptive_long_eligible"])),
                    "adaptive_short_eligible": int(bool(metrics["adaptive_short_eligible"])),
                    "strength_gate": int(bool(metrics["strength_gate"])),
                    "adaptive_strength_long": round(
                        float(metrics["adaptive_strength_long"]), 4
                    ),
                    "adaptive_strength_short": round(
                        float(metrics["adaptive_strength_short"]), 4
                    ),
                    "adaptive_long_open": int(bool(metrics["adaptive_long_open"])),
                    "adaptive_short_open": int(bool(metrics["adaptive_short_open"])),
                    "max_adaptive_score": round(max_score, 4),
                    "match_ok": 1,
                    "note": "",
                }
            )

        if tick_candidates:
            best = max(tick_candidates, key=lambda value: max(value[1], value[2]))
            best_score = max(best[1], best[2])
            per_tick_rows.append(
                {
                    "tick": tick,
                    "tick_time_utc": meta.timestamp.isoformat(),
                    "entry_class_journal": meta.entry_class or "",
                    "neutral_pressure_streak": meta.neutral_pressure_streak
                    if meta.neutral_pressure_streak is not None
                    else "",
                    "macd_pairs_count": len(meta.macd_pairs),
                    "best_candidate_pair": best[0],
                    "best_adaptive_score": round(best_score, 4),
                    "best_long_score": round(best[1], 4),
                    "best_short_score": round(best[2], 4),
                }
            )

    per_pair_fields = [
        "tick",
        "tick_time_utc",
        "pair",
        "report_id",
        "report_time_utc",
        "entry_class_journal",
        "neutral_pressure_streak",
        "signal",
        "bb_pos_pct",
        "macd",
        "signal_line",
        "histogram",
        "trend",
        "momentum",
        "macd_gap_ratio",
        "hist_ratio",
        "formal_long",
        "formal_short",
        "adaptive_long_eligible",
        "adaptive_short_eligible",
        "strength_gate",
        "adaptive_strength_long",
        "adaptive_strength_short",
        "adaptive_long_open",
        "adaptive_short_open",
        "max_adaptive_score",
        "match_ok",
        "note",
    ]
    per_tick_fields = [
        "tick",
        "tick_time_utc",
        "entry_class_journal",
        "neutral_pressure_streak",
        "macd_pairs_count",
        "best_candidate_pair",
        "best_adaptive_score",
        "best_long_score",
        "best_short_score",
    ]

    per_pair_out = output_dir / "adaptive_replay_per_pair.csv"
    per_tick_out = output_dir / "adaptive_replay_per_tick.csv"
    write_csv(per_pair_out, per_pair_rows, per_pair_fields)
    write_csv(per_tick_out, per_tick_rows, per_tick_fields)

    print(f"Wrote {len(per_pair_rows)} rows to {per_pair_out}")
    print(f"Wrote {len(per_tick_rows)} rows to {per_tick_out}")


if __name__ == "__main__":
    main()
