"""Replay adaptive entry logic using persisted MACD+BB routine reports."""

from __future__ import annotations

CATEGORY = "Bot Analysis"

import csv
import datetime as dt
import html
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from telegram.ext import ContextTypes

from routines.base import RoutineResult

logger = logging.getLogger(__name__)

_ROOT_DIR = Path(__file__).resolve().parent.parent
_TRADING_AGENTS_DIR = _ROOT_DIR / "trading_agents"
_REPORTS_DIR = _ROOT_DIR / "reports"
_REPORTS_INDEX_PATH = _REPORTS_DIR / "reports_index.json"

_TICK_RE = re.compile(r"- tick#(\d+)\s+\|\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+\|")
_DECISION_RE = re.compile(r"- \*\*#(\d+)\*\*.*")
_DECISION_TICK_RE = re.compile(r"tick=(\d+)")
_MACD_PAIRS_RE = re.compile(r"macd_pairs=([A-Z0-9:-]+(?:,[A-Z0-9:-]+)*)")
_NEUTRAL_STREAK_RE = re.compile(r"neutral_pressure_streak=(\d+)")
_ENTRY_CLASS_RE = re.compile(r"entry_class=([a-zA-Z0-9_]+)")
_REVIEWED_MACD_LIST_RE = re.compile(
    r"reviewed 5 MACD 1h(?: pairs)?:\s*([A-Za-z0-9,\sk]+?)\s*(?:—| - |\.|$)",
    re.IGNORECASE,
)
_PAREN_MACD_REVIEWS_RE = re.compile(
    r"five 1h reviews?\s*\(([A-Za-z0-9,\sk/]+)\)",
    re.IGNORECASE,
)
_TICK_SUMMARY_ENTRY_RE = re.compile(
    r"\*\*Tick #\d+ —\s*(HOLD|OPENED LONG(?:\s+[A-Z0-9:-]+)?|OPENED SHORT(?:\s+[A-Z0-9:-]+)?)",
    re.IGNORECASE,
)
_TICK_STREAK_RE = re.compile(
    r"neutral_pressure_streak(?:`|')?(?:=| reaches | is )[\s*]*(\d+)",
    re.IGNORECASE,
)
_TICK_STREAK_ALT_RE = re.compile(r"Adaptive streak \*\*(\d+)\*\*", re.IGNORECASE)
_TICK_STREAK_PAREN_RE = re.compile(r"\(streak (\d+)\)", re.IGNORECASE)
_SYMBOL_USD_RE = re.compile(r"\b([A-Z][A-Z0-9]*-USD)\b")

_PAIR_TITLE_RE = re.compile(r"MACD\+BB:\s+([A-Z0-9:-]+)\s+\((1h|4h)\)")
_FIRST_TABLE_ROW_RE = re.compile(r"<tbody><tr>(.*?)</tr></tbody>", re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_COND_ROW_RE = re.compile(
    r"<tr><td>([^<]+)</td><td>([^<]+)</td><td>(True|False)</td></tr>", re.DOTALL
)

_PRESET_OVERRIDES: dict[str, dict[str, float | int]] = {
    "safe": {
        "activation_ticks": 8,
        "adaptive_long_bb_pos_max": 46.0,
        "adaptive_short_bb_pos_min": 74.0,
        "adaptive_strong_long_bb_pos_max": 33.0,
        "adaptive_strong_short_bb_pos_min": 87.0,
        "adaptive_min_macd_gap_ratio": 0.10,
        "adaptive_min_hist_ratio": 0.16,
        "adaptive_score_open_min": 2.55,
        "adaptive_score_open_min_extreme": 2.30,
        "adaptive_hist_sign_bonus": 0.35,
        "adaptive_hist_sign_penalty": 0.40,
        "adaptive_momentum_bonus": 0.15,
        "adaptive_momentum_penalty": 0.15,
    },
    "balanced": {
        "activation_ticks": 6,
        "adaptive_long_bb_pos_max": 48.0,
        "adaptive_short_bb_pos_min": 72.0,
        "adaptive_strong_long_bb_pos_max": 35.0,
        "adaptive_strong_short_bb_pos_min": 85.0,
        "adaptive_min_macd_gap_ratio": 0.08,
        "adaptive_min_hist_ratio": 0.12,
        "adaptive_score_open_min": 2.40,
        "adaptive_score_open_min_extreme": 2.15,
        "adaptive_hist_sign_bonus": 0.35,
        "adaptive_hist_sign_penalty": 0.35,
        "adaptive_momentum_bonus": 0.20,
        "adaptive_momentum_penalty": 0.10,
    },
    "opportunistic": {
        "activation_ticks": 4,
        "adaptive_long_bb_pos_max": 55.0,
        "adaptive_short_bb_pos_min": 65.0,
        "adaptive_strong_long_bb_pos_max": 30.0,
        "adaptive_strong_short_bb_pos_min": 90.0,
        "adaptive_min_macd_gap_ratio": 0.06,
        "adaptive_min_hist_ratio": 0.09,
        "adaptive_score_open_min": 2.10,
        "adaptive_score_open_min_extreme": 1.85,
        "adaptive_hist_sign_bonus": 0.30,
        "adaptive_hist_sign_penalty": 0.30,
        "adaptive_momentum_bonus": 0.25,
        "adaptive_momentum_penalty": 0.05,
    },
    # Looser thresholds for replay sanity checks (HTML report scores run lower than live journal).
    "replay_probe": {
        "activation_ticks": 4,
        "time_window_min": 90,
        "adaptive_long_bb_pos_max": 90.0,
        "adaptive_short_bb_pos_min": 55.0,
        "adaptive_strong_long_bb_pos_max": 30.0,
        "adaptive_strong_short_bb_pos_min": 90.0,
        "adaptive_min_macd_gap_ratio": 0.06,
        "adaptive_min_hist_ratio": 0.09,
        "adaptive_score_open_min": 1.00,
        "adaptive_score_open_min_extreme": 0.75,
        "adaptive_hist_sign_bonus": 0.30,
        "adaptive_hist_sign_penalty": 0.30,
        "adaptive_momentum_bonus": 0.25,
        "adaptive_momentum_penalty": 0.05,
    },
}


class Config(BaseModel):
    """Replay adaptive strategy outcomes from saved routine reports with configurable thresholds."""

    preset: Literal["custom", "safe", "balanced", "opportunistic", "replay_probe"] = (
        Field(
            default="balanced",
            description=(
                "Preset profile for adaptive thresholds. "
                "'custom' uses explicitly provided values. "
                "'replay_probe' uses looser gates so replay can produce sample trades."
            ),
        )
    )
    strategy_slug: str = Field(
        default="macdbb_scanner_aggressive_hl",
        description="Strategy folder under trading_agents/",
    )
    session_nums: str = Field(
        default="all",
        description="Session selector: 'all' or comma-separated values like '35,36'",
    )
    time_window_min: int = Field(
        default=25,
        description="Max minutes for matching report file to tick/pair",
    )
    activation_ticks: int = Field(
        default=6,
        description="neutral_pressure_streak threshold for adaptive mode",
    )
    adaptive_long_bb_pos_max: float = Field(
        default=48.0,
        description="Adaptive LONG BB position upper bound",
    )
    adaptive_short_bb_pos_min: float = Field(
        default=72.0,
        description="Adaptive SHORT BB position lower bound",
    )
    adaptive_strong_long_bb_pos_max: float = Field(
        default=35.0,
        description="Deep pullback override bound for LONG",
    )
    adaptive_strong_short_bb_pos_min: float = Field(
        default=85.0,
        description="Strong extension override bound for SHORT",
    )
    adaptive_min_macd_gap_ratio: float = Field(
        default=0.08,
        description="Minimum MACD-signal spread ratio for strength gate",
    )
    adaptive_min_hist_ratio: float = Field(
        default=0.12,
        description="Minimum histogram ratio for strength gate",
    )
    adaptive_score_open_min: float = Field(
        default=2.40,
        description="Minimum adaptive directional score to open",
    )
    adaptive_score_open_min_extreme: float = Field(
        default=2.15,
        description="Minimum adaptive score for extreme BB displacement candidates",
    )
    adaptive_hist_sign_bonus: float = Field(
        default=0.35,
        description="Score bonus when histogram sign aligns with side",
    )
    adaptive_hist_sign_penalty: float = Field(
        default=0.35,
        description="Score penalty when histogram sign disagrees with side",
    )
    adaptive_momentum_bonus: float = Field(
        default=0.20,
        description="Score bonus when momentum aligns with side",
    )
    adaptive_momentum_penalty: float = Field(
        default=0.10,
        description="Score penalty when momentum disagrees with side",
    )
    notional_quote: float = Field(
        default=200.0,
        description="Notional per simulated adaptive trade in quote currency",
    )
    sl_pct: float = Field(
        default=1.5,
        description="Stop-loss percent for approximate simulation",
    )
    tp_pct: float = Field(
        default=3.0,
        description="Take-profit percent for approximate simulation",
    )
    max_holding_ticks: int = Field(
        default=8,
        description="Force-close after this many ticks if still open",
    )
    exit_on_opposite_formal: bool = Field(
        default=True,
        description="Exit position if opposite formal signal appears",
    )
    write_csv: bool = Field(
        default=True,
        description="Write replay CSV files under each session directory",
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


@dataclass
class SimTrade:
    session_num: int
    entry_tick: int
    exit_tick: int
    pair: str
    side: str
    entry_price: float
    exit_price: float
    hold_ticks: int
    exit_reason: str
    pnl_quote: float
    return_pct: float
    entry_score_long: float
    entry_score_short: float
    entry_neutral_streak: int


def _resolve_config_with_preset(config: Config) -> Config:
    if config.preset == "custom":
        return config
    overrides = _PRESET_OVERRIDES.get(config.preset)
    if not overrides:
        return config
    # Preset defaults first; explicit caller fields (exclude_unset) win.
    return Config(
        **{**overrides, **config.model_dump(exclude_unset=True), "preset": config.preset}
    )


def _parse_dt(value: str) -> dt.datetime:
    if "T" in value:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            dt.timezone.utc
        )
    return dt.datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)


def _parse_session_selector(session_selector: str, sessions_dir: Path) -> list[int]:
    if session_selector.strip().lower() == "all":
        session_numbers: list[int] = []
        for session_path in sessions_dir.iterdir():
            if session_path.is_dir() and session_path.name.startswith("session_"):
                try:
                    session_numbers.append(int(session_path.name.split("_", 1)[1]))
                except ValueError:
                    continue
        return sorted(session_numbers)
    parsed_numbers: list[int] = []
    for value in session_selector.split(","):
        value = value.strip()
        if not value:
            continue
        parsed_numbers.append(int(value))
    return sorted(set(parsed_numbers))


def _normalize_journal_pair_token(raw: str) -> str:
    token = raw.strip()
    if not token or token.lower() in {"all", "none", "hold"}:
        return ""
    if token.endswith("-USD"):
        return token
    return f"{token}-USD"


def _normalize_journal_pair_list(raw: str) -> list[str]:
    pairs: list[str] = []
    for part in re.split(r"[,/]", raw):
        normalized = _normalize_journal_pair_token(part)
        if normalized and normalized not in pairs:
            pairs.append(normalized)
    return pairs


def _extract_pairs_from_tick_narrative(line: str) -> list[str]:
    for pattern in (_REVIEWED_MACD_LIST_RE, _PAREN_MACD_REVIEWS_RE):
        match = pattern.search(line)
        if match:
            pairs = _normalize_journal_pair_list(match.group(1))
            if len(pairs) >= 2:
                return pairs
    pairs: list[str] = []
    for symbol_match in _SYMBOL_USD_RE.finditer(line):
        pair = symbol_match.group(1)
        if pair not in pairs:
            pairs.append(pair)
    # Avoid treating a lone near-miss mention as the full reviewed set.
    if len(pairs) >= 3:
        return pairs[:8]
    return []


def _extract_streak_from_tick_narrative(line: str) -> int | None:
    for pattern in (
        _NEUTRAL_STREAK_RE,
        _TICK_STREAK_RE,
        _TICK_STREAK_ALT_RE,
        _TICK_STREAK_PAREN_RE,
    ):
        match = pattern.search(line)
        if match:
            return int(match.group(1))
    return None


def _extract_entry_class_from_tick_narrative(line: str) -> str | None:
    entry_class_match = _ENTRY_CLASS_RE.search(line)
    if entry_class_match:
        return entry_class_match.group(1)
    summary_match = _TICK_SUMMARY_ENTRY_RE.search(line)
    if not summary_match:
        return None
    summary = summary_match.group(1).upper()
    if summary.startswith("OPENED LONG"):
        return "opened_long"
    if summary.startswith("OPENED SHORT"):
        return "opened_short"
    return "hold"


def _parse_journal_ticks(journal_text: str) -> dict[int, TickMeta]:
    tick_time_map: dict[int, dt.datetime] = {}
    tick_header_lines: dict[int, str] = {}
    for line in journal_text.splitlines():
        tick_match = _TICK_RE.match(line)
        if not tick_match:
            continue
        tick_number = int(tick_match.group(1))
        tick_time_map[tick_number] = _parse_dt(tick_match.group(2))
        tick_header_lines[tick_number] = line

    tick_meta_map: dict[int, TickMeta] = {}
    for line in journal_text.splitlines():
        decision_match = _DECISION_RE.match(line)
        if not decision_match:
            continue
        tick_field_match = _DECISION_TICK_RE.search(line)
        tick_number = (
            int(tick_field_match.group(1))
            if tick_field_match
            else int(decision_match.group(1))
        )
        if tick_number not in tick_time_map:
            continue
        pairs_match = _MACD_PAIRS_RE.search(line)
        reviewed_pairs = pairs_match.group(1).split(",") if pairs_match else []
        streak_match = _NEUTRAL_STREAK_RE.search(line)
        entry_class_match = _ENTRY_CLASS_RE.search(line)
        tick_meta_map[tick_number] = TickMeta(
            tick=tick_number,
            timestamp=tick_time_map[tick_number],
            macd_pairs=reviewed_pairs,
            neutral_pressure_streak=int(streak_match.group(1))
            if streak_match
            else None,
            entry_class=entry_class_match.group(1) if entry_class_match else None,
        )

    # Ticks 1..N are logged under "## Ticks"; structured "## Decisions" rows are newer.
    for tick_number, line in tick_header_lines.items():
        if tick_number in tick_meta_map:
            continue
        tick_meta_map[tick_number] = TickMeta(
            tick=tick_number,
            timestamp=tick_time_map[tick_number],
            macd_pairs=_extract_pairs_from_tick_narrative(line),
            neutral_pressure_streak=_extract_streak_from_tick_narrative(line),
            entry_class=_extract_entry_class_from_tick_narrative(line),
        )

    last_pairs: list[str] = []
    for tick_number in sorted(tick_meta_map):
        meta = tick_meta_map[tick_number]
        if len(meta.macd_pairs) >= 3:
            last_pairs = meta.macd_pairs
            continue
        if len(last_pairs) >= 3:
            tick_meta_map[tick_number] = TickMeta(
                tick=meta.tick,
                timestamp=meta.timestamp,
                macd_pairs=list(last_pairs),
                neutral_pressure_streak=meta.neutral_pressure_streak,
                entry_class=meta.entry_class,
            )
    return tick_meta_map


def _load_reports_index() -> list[ReportMeta]:
    if not _REPORTS_INDEX_PATH.exists():
        return []
    raw_entries = json.loads(_REPORTS_INDEX_PATH.read_text(encoding="utf-8"))
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
                created_at=_parse_dt(entry["created_at"]),
                pair=title_match.group(1),
                interval=title_match.group(2),
            )
        )
    return reports


def _extract_td_value(raw_value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", raw_value)).strip()


def _parse_report_html(report_html: str) -> ParsedReport | None:
    first_row_match = _FIRST_TABLE_ROW_RE.search(report_html)
    if not first_row_match:
        return None
    values = [_extract_td_value(v) for v in _TD_RE.findall(first_row_match.group(1))]
    if len(values) < 12:
        return None

    pair = values[0]
    interval = values[1]
    signal = values[2]
    price = float(values[3])
    bb_pos_pct = float(values[4])
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


def _nearest_report(
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
    nearest_report: tuple[dt.timedelta, ReportMeta] | None = None
    for candidate in candidates:
        if candidate.interval != interval:
            continue
        delta = abs(candidate.created_at - tick_time)
        if delta > max_delta:
            continue
        if nearest_report is None or delta < nearest_report[0]:
            nearest_report = (delta, candidate)
    return nearest_report[1] if nearest_report else None


def _compute_metrics(parsed: ParsedReport, config: Config) -> dict[str, float | bool]:
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

    adaptive_long_eligible = (
        parsed.trend == "bullish"
        and parsed.bb_pos_pct <= config.adaptive_long_bb_pos_max
    )
    adaptive_short_eligible = (
        parsed.trend == "bearish"
        and parsed.bb_pos_pct >= config.adaptive_short_bb_pos_min
    )
    extreme_long_candidate = (
        parsed.trend == "bullish"
        and parsed.bb_pos_pct <= config.adaptive_strong_long_bb_pos_max
    )
    extreme_short_candidate = (
        parsed.trend == "bearish"
        and parsed.bb_pos_pct >= config.adaptive_strong_short_bb_pos_min
    )
    strength_gate = (
        macd_gap_ratio >= config.adaptive_min_macd_gap_ratio
        or hist_ratio >= config.adaptive_min_hist_ratio
    )

    hist_sign_long = (
        config.adaptive_hist_sign_bonus
        if parsed.histogram >= 0
        else -config.adaptive_hist_sign_penalty
    )
    hist_sign_short = (
        config.adaptive_hist_sign_bonus
        if parsed.histogram <= 0
        else -config.adaptive_hist_sign_penalty
    )
    momentum_bonus_long = (
        config.adaptive_momentum_bonus
        if parsed.momentum == "increasing"
        else -config.adaptive_momentum_penalty
    )
    momentum_bonus_short = (
        config.adaptive_momentum_bonus
        if parsed.momentum == "decreasing"
        else -config.adaptive_momentum_penalty
    )

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

    long_open_threshold = (
        config.adaptive_score_open_min_extreme
        if extreme_long_candidate
        else config.adaptive_score_open_min
    )
    short_open_threshold = (
        config.adaptive_score_open_min_extreme
        if extreme_short_candidate
        else config.adaptive_score_open_min
    )

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


def _compute_return_pct(side: str, entry_price: float, current_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    if side == "long":
        return (current_price / entry_price) - 1.0
    return (entry_price / current_price) - 1.0


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _build_reports_by_pair(reports: list[ReportMeta]) -> dict[str, list[ReportMeta]]:
    by_pair: dict[str, list[ReportMeta]] = {}
    for report in reports:
        by_pair.setdefault(report.pair, []).append(report)
    for pair in by_pair:
        by_pair[pair].sort(key=lambda item: item.created_at)
    return by_pair


def _load_parsed_report(report_meta: ReportMeta) -> ParsedReport | None:
    report_path = _REPORTS_DIR / report_meta.filename
    if not report_path.exists():
        return None
    return _parse_report_html(report_path.read_text(encoding="utf-8"))


def _simulate_session(
    session_num: int,
    tick_meta_map: dict[int, TickMeta],
    reports_by_pair: dict[str, list[ReportMeta]],
    config: Config,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[SimTrade]]:
    per_pair_rows: list[dict[str, Any]] = []
    per_tick_rows: list[dict[str, Any]] = []
    simulated_trades: list[SimTrade] = []

    current_position: dict[str, Any] | None = None
    sl_threshold = config.sl_pct / 100.0
    tp_threshold = config.tp_pct / 100.0
    # Keep latest matched price context so open positions can be closed
    # at session end even when the pair is not reviewed every tick.
    last_seen_by_pair: dict[str, tuple[int, ParsedReport]] = {}

    for tick in sorted(tick_meta_map):
        meta = tick_meta_map[tick]
        candidate_rows: list[dict[str, Any]] = []
        best_pair = ""
        best_long_score = -1.0
        best_short_score = -1.0
        best_score = -1.0

        parsed_cache: dict[str, tuple[ParsedReport, dict[str, float | bool]]] = {}

        for pair in meta.macd_pairs:
            report_meta = _nearest_report(
                reports_by_pair, pair, meta.timestamp, config.time_window_min, interval="1h"
            )
            if report_meta is None:
                per_pair_rows.append(
                    {
                        "session": session_num,
                        "tick": tick,
                        "tick_time_utc": meta.timestamp.isoformat(),
                        "pair": pair,
                        "match_ok": 0,
                        "note": "no 1h report in window",
                    }
                )
                continue
            parsed = _load_parsed_report(report_meta)
            if parsed is None:
                per_pair_rows.append(
                    {
                        "session": session_num,
                        "tick": tick,
                        "tick_time_utc": meta.timestamp.isoformat(),
                        "pair": pair,
                        "match_ok": 0,
                        "note": "missing or unparsable report html",
                    }
                )
                continue
            metrics = _compute_metrics(parsed, config)
            parsed_cache[pair] = (parsed, metrics)
            last_seen_by_pair[pair] = (tick, parsed)

            long_score = float(metrics["adaptive_strength_long"])
            short_score = float(metrics["adaptive_strength_short"])
            max_score = max(long_score, short_score)
            if max_score > best_score:
                best_pair = pair
                best_score = max_score
                best_long_score = long_score
                best_short_score = short_score

            row = {
                "session": session_num,
                "tick": tick,
                "tick_time_utc": meta.timestamp.isoformat(),
                "pair": pair,
                "report_id": report_meta.report_id,
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
                "extreme_long_candidate": int(bool(metrics["extreme_long_candidate"])),
                "extreme_short_candidate": int(bool(metrics["extreme_short_candidate"])),
                "strength_gate": int(bool(metrics["strength_gate"])),
                "hist_sign_long": round(float(metrics["hist_sign_long"]), 4),
                "hist_sign_short": round(float(metrics["hist_sign_short"]), 4),
                "momentum_bonus_long": round(float(metrics["momentum_bonus_long"]), 4),
                "momentum_bonus_short": round(float(metrics["momentum_bonus_short"]), 4),
                "adaptive_strength_long": round(long_score, 4),
                "adaptive_strength_short": round(short_score, 4),
                "long_open_threshold": round(float(metrics["long_open_threshold"]), 4),
                "short_open_threshold": round(float(metrics["short_open_threshold"]), 4),
                "adaptive_long_open": int(bool(metrics["adaptive_long_open"])),
                "adaptive_short_open": int(bool(metrics["adaptive_short_open"])),
                "match_ok": 1,
                "note": "",
            }
            per_pair_rows.append(row)
            candidate_rows.append(row)

        action_taken = "hold"
        action_reason = ""

        if current_position is None and candidate_rows:
            adaptive_mode_active = (
                (meta.neutral_pressure_streak or 0) >= config.activation_ticks
            )
            if adaptive_mode_active:
                open_candidates: list[tuple[str, str, ParsedReport, dict[str, float | bool]]] = []
                for pair, (parsed, metrics) in parsed_cache.items():
                    if bool(metrics["adaptive_long_open"]):
                        open_candidates.append((pair, "long", parsed, metrics))
                    if bool(metrics["adaptive_short_open"]):
                        open_candidates.append((pair, "short", parsed, metrics))
                if open_candidates:
                    selected_pair, selected_side, selected_parsed, selected_metrics = max(
                        open_candidates,
                        key=lambda item: max(
                            float(item[3]["adaptive_strength_long"]),
                            float(item[3]["adaptive_strength_short"]),
                        ),
                    )
                    current_position = {
                        "entry_tick": tick,
                        "entry_time": meta.timestamp,
                        "pair": selected_pair,
                        "side": selected_side,
                        "entry_price": selected_parsed.price,
                        "entry_score_long": float(selected_metrics["adaptive_strength_long"]),
                        "entry_score_short": float(selected_metrics["adaptive_strength_short"]),
                        "entry_neutral_streak": meta.neutral_pressure_streak or 0,
                    }
                    action_taken = "open"
                    action_reason = f"adaptive_{selected_side}"

        if current_position is not None:
            open_pair = current_position["pair"]
            if open_pair in parsed_cache:
                open_parsed, open_metrics = parsed_cache[open_pair]
                current_return_pct = _compute_return_pct(
                    current_position["side"],
                    current_position["entry_price"],
                    open_parsed.price,
                )
                hold_ticks = tick - current_position["entry_tick"]

                exit_reason = ""
                if current_return_pct <= -sl_threshold:
                    exit_reason = "stop_loss_close_proxy"
                elif current_return_pct >= tp_threshold:
                    exit_reason = "take_profit_close_proxy"
                elif (
                    config.exit_on_opposite_formal
                    and current_position["side"] == "long"
                    and bool(open_metrics["formal_short"])
                ):
                    exit_reason = "opposite_formal"
                elif (
                    config.exit_on_opposite_formal
                    and current_position["side"] == "short"
                    and bool(open_metrics["formal_long"])
                ):
                    exit_reason = "opposite_formal"
                elif hold_ticks >= config.max_holding_ticks:
                    exit_reason = "max_holding_ticks"

                if exit_reason:
                    trade_pnl_quote = config.notional_quote * current_return_pct
                    simulated_trades.append(
                        SimTrade(
                            session_num=session_num,
                            entry_tick=current_position["entry_tick"],
                            exit_tick=tick,
                            pair=open_pair,
                            side=current_position["side"],
                            entry_price=current_position["entry_price"],
                            exit_price=open_parsed.price,
                            hold_ticks=hold_ticks,
                            exit_reason=exit_reason,
                            pnl_quote=trade_pnl_quote,
                            return_pct=current_return_pct * 100.0,
                            entry_score_long=current_position["entry_score_long"],
                            entry_score_short=current_position["entry_score_short"],
                            entry_neutral_streak=current_position["entry_neutral_streak"],
                        )
                    )
                    if action_taken == "hold":
                        action_taken = "close"
                        action_reason = exit_reason
                    else:
                        action_reason = f"{action_reason}+{exit_reason}"
                    current_position = None

        per_tick_rows.append(
            {
                "session": session_num,
                "tick": tick,
                "tick_time_utc": meta.timestamp.isoformat(),
                "entry_class_journal": meta.entry_class or "",
                "neutral_pressure_streak": meta.neutral_pressure_streak
                if meta.neutral_pressure_streak is not None
                else "",
                "macd_pairs_count": len(meta.macd_pairs),
                "best_candidate_pair": best_pair,
                "best_adaptive_score": round(best_score if best_score > 0 else 0.0, 4),
                "best_long_score": round(best_long_score if best_long_score > 0 else 0.0, 4),
                "best_short_score": round(
                    best_short_score if best_short_score > 0 else 0.0, 4
                ),
                "sim_action": action_taken,
                "sim_reason": action_reason,
            }
        )

    if current_position is not None:
        open_pair = current_position["pair"]
        last_seen = last_seen_by_pair.get(open_pair)
        if last_seen is not None:
            exit_tick, exit_parsed = last_seen
            return_pct = _compute_return_pct(
                current_position["side"],
                current_position["entry_price"],
                exit_parsed.price,
            )
            simulated_trades.append(
                SimTrade(
                    session_num=session_num,
                    entry_tick=current_position["entry_tick"],
                    exit_tick=exit_tick,
                    pair=open_pair,
                    side=current_position["side"],
                    entry_price=current_position["entry_price"],
                    exit_price=exit_parsed.price,
                    hold_ticks=max(0, exit_tick - current_position["entry_tick"]),
                    exit_reason="session_end_proxy",
                    pnl_quote=config.notional_quote * return_pct,
                    return_pct=return_pct * 100.0,
                    entry_score_long=current_position["entry_score_long"],
                    entry_score_short=current_position["entry_score_short"],
                    entry_neutral_streak=current_position["entry_neutral_streak"],
                )
            )

    return per_pair_rows, per_tick_rows, simulated_trades


async def run(config: Config, context: ContextTypes.DEFAULT_TYPE) -> str | RoutineResult:
    config = _resolve_config_with_preset(config)
    strategy_dir = _TRADING_AGENTS_DIR / config.strategy_slug
    sessions_dir = strategy_dir / "sessions"
    if not sessions_dir.is_dir():
        return f"Sessions directory not found: {sessions_dir}"

    try:
        selected_sessions = _parse_session_selector(config.session_nums, sessions_dir)
    except ValueError as error:
        return f"Invalid session_nums: {error}"

    if not selected_sessions:
        return "No sessions matched the requested selector."

    reports = _load_reports_index()
    if not reports:
        return "No macd_bb_analysis reports found in reports index."
    reports_by_pair = _build_reports_by_pair(reports)

    all_pair_rows: list[dict[str, Any]] = []
    all_tick_rows: list[dict[str, Any]] = []
    all_trades: list[SimTrade] = []
    session_rollup_rows: list[dict[str, Any]] = []

    for session_num in selected_sessions:
        journal_path = sessions_dir / f"session_{session_num}" / "journal.md"
        if not journal_path.is_file():
            logger.info("Skipping session %s (journal missing)", session_num)
            continue
        tick_meta_map = _parse_journal_ticks(journal_path.read_text(encoding="utf-8"))
        if not tick_meta_map:
            logger.info("Skipping session %s (no parsed ticks)", session_num)
            continue

        per_pair_rows, per_tick_rows, trades = _simulate_session(
            session_num=session_num,
            tick_meta_map=tick_meta_map,
            reports_by_pair=reports_by_pair,
            config=config,
        )
        all_pair_rows.extend(per_pair_rows)
        all_tick_rows.extend(per_tick_rows)
        all_trades.extend(trades)

        session_pnl = sum(trade.pnl_quote for trade in trades)
        session_wins = sum(1 for trade in trades if trade.pnl_quote > 0)
        session_rollup_rows.append(
            {
                "Session": session_num,
                "Ticks Parsed": len(per_tick_rows),
                "Pair Rows": sum(1 for row in per_pair_rows if row.get("match_ok") == 1),
                "Sim Trades": len(trades),
                "Win Rate %": round(
                    (session_wins / len(trades) * 100.0) if trades else 0.0, 1
                ),
                "Sim PnL $": round(session_pnl, 2),
            }
        )

        if config.write_csv:
            output_dir = sessions_dir / f"session_{session_num}"
            _write_csv(
                output_dir / "adaptive_replay_per_pair.csv",
                per_pair_rows,
                [
                    "session",
                    "tick",
                    "tick_time_utc",
                    "pair",
                    "report_id",
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
                    "extreme_long_candidate",
                    "extreme_short_candidate",
                    "strength_gate",
                    "hist_sign_long",
                    "hist_sign_short",
                    "momentum_bonus_long",
                    "momentum_bonus_short",
                    "adaptive_strength_long",
                    "adaptive_strength_short",
                    "long_open_threshold",
                    "short_open_threshold",
                    "adaptive_long_open",
                    "adaptive_short_open",
                    "match_ok",
                    "note",
                ],
            )
            _write_csv(
                output_dir / "adaptive_replay_per_tick.csv",
                per_tick_rows,
                [
                    "session",
                    "tick",
                    "tick_time_utc",
                    "entry_class_journal",
                    "neutral_pressure_streak",
                    "macd_pairs_count",
                    "best_candidate_pair",
                    "best_adaptive_score",
                    "best_long_score",
                    "best_short_score",
                    "sim_action",
                    "sim_reason",
                ],
            )

    if not session_rollup_rows:
        return "No session data could be replayed."

    total_trades = len(all_trades)
    total_wins = sum(1 for trade in all_trades if trade.pnl_quote > 0)
    total_pnl = sum(trade.pnl_quote for trade in all_trades)
    total_win_rate = (total_wins / total_trades) if total_trades else 0.0

    simulated_trades_rows = [
        {
            "Session": trade.session_num,
            "Pair": trade.pair,
            "Side": trade.side.upper(),
            "Entry Tick": trade.entry_tick,
            "Exit Tick": trade.exit_tick,
            "Hold Ticks": trade.hold_ticks,
            "Exit Reason": trade.exit_reason,
            "Entry Price": round(trade.entry_price, 8),
            "Exit Price": round(trade.exit_price, 8),
            "Return %": round(trade.return_pct, 3),
            "PnL $": round(trade.pnl_quote, 2),
            "Entry Score Long": round(trade.entry_score_long, 4),
            "Entry Score Short": round(trade.entry_score_short, 4),
            "Entry Streak": trade.entry_neutral_streak,
        }
        for trade in all_trades
    ]

    summary_lines = [
        f"Adaptive replay backtest — {config.strategy_slug}",
        f"Preset: {config.preset}",
        f"Sessions: {', '.join(str(value) for value in selected_sessions)}",
        f"Ticks replayed: {len(all_tick_rows)} | Pair snapshots: {sum(1 for row in all_pair_rows if row.get('match_ok') == 1)}",
        f"Sim trades: {total_trades} | Win rate: {total_win_rate:.1%} | Sim PnL: ${total_pnl:+.2f}",
        (
            "Config: "
            f"score_min={config.adaptive_score_open_min}, "
            f"score_min_extreme={config.adaptive_score_open_min_extreme}, "
            f"gate(macd_gap={config.adaptive_min_macd_gap_ratio}, hist={config.adaptive_min_hist_ratio}), "
            f"BB(L<={config.adaptive_long_bb_pos_max}, S>={config.adaptive_short_bb_pos_min}, "
            f"Lext<={config.adaptive_strong_long_bb_pos_max}, Sext>={config.adaptive_strong_short_bb_pos_min}), "
            f"hist(+{config.adaptive_hist_sign_bonus}/-{config.adaptive_hist_sign_penalty}), "
            f"mom(+{config.adaptive_momentum_bonus}/-{config.adaptive_momentum_penalty})"
        ),
    ]

    sections = [
        {"type": "kpi", "label": "Sessions", "value": str(len(session_rollup_rows))},
        {"type": "kpi", "label": "Sim Trades", "value": str(total_trades)},
        {"type": "kpi", "label": "Win Rate", "value": f"{total_win_rate:.1%}"},
        {"type": "kpi", "label": "Sim PnL", "value": f"${total_pnl:+.2f}"},
    ]

    table_data = session_rollup_rows
    table_columns = [
        "Session",
        "Ticks Parsed",
        "Pair Rows",
        "Sim Trades",
        "Win Rate %",
        "Sim PnL $",
    ]

    try:
        from condor.reports import ReportBuilder

        builder = ReportBuilder(f"Adaptive replay backtest: {config.strategy_slug}")
        builder.source("routine", "adaptive_replay_backtest").tags(
            ["trading-agent", "backtest", "adaptive"]
        )
        builder.kpi("Sim Trades", str(total_trades))
        builder.kpi("Win Rate", f"{total_win_rate:.1%}")
        builder.kpi("Sim PnL", f"${total_pnl:+.2f}")
        builder.markdown("\n".join(summary_lines))
        if session_rollup_rows:
            builder.table(session_rollup_rows, columns=table_columns)
        if simulated_trades_rows:
            builder.markdown("### Simulated Trades")
            builder.table(
                simulated_trades_rows,
                columns=[
                    "Session",
                    "Pair",
                    "Side",
                    "Entry Tick",
                    "Exit Tick",
                    "Hold Ticks",
                    "Exit Reason",
                    "Return %",
                    "PnL $",
                    "Entry Score Long",
                    "Entry Score Short",
                    "Entry Streak",
                ],
            )
        await builder.save()
    except Exception as error:
        logger.warning("Report generation failed: %s", error)

    return RoutineResult(
        text="\n".join(summary_lines),
        table_data=table_data,
        table_columns=table_columns,
        sections=sections,
    )
