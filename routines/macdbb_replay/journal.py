from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from routines.macdbb_replay.models import Filter4h, JournalSignal1h, TickMeta

_TICK_RE = re.compile(r"- tick#(\d+)\s+\|\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+\|")
_DECISION_RE = re.compile(r"- \*\*#(\d+)\*\*.*")
_DECISION_TICK_RE = re.compile(r"tick=(\d+)")
_MACD_PAIRS_RE = re.compile(r"macd_pairs=([A-Z0-9:-]+(?:,[A-Z0-9:-]+)*)")
_NEUTRAL_STREAK_RE = re.compile(r"neutral_pressure_streak=(\d+)")
_ENTRY_CLASS_RE = re.compile(r"entry_class=([a-zA-Z0-9_]+)")
_TRADEABLE_COUNT_RE = re.compile(r"tradeable_count=(\d+)")
_SCANNER_ANALYZED_RE = re.compile(r"scanner_analyzed=(\d+)")
_QUEUE_TOTAL_RE = re.compile(r"queue_total=([A-Z0-9:-]+(?:,[A-Z0-9:-]+)*)")
_SIGNALS_1H_RE = re.compile(r"signals_1h=([^\s]+)")
_FILTER_4H_RE = re.compile(r"filter_4h=([^\s]+)")
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
_PRE_OPEN_STREAK_RE = re.compile(
    r"neutral streak\s*\*?\*?(\d+)\*?\*?(?:/6|→| -|→reset)",
    re.IGNORECASE,
)
_SYMBOL_USD_RE = re.compile(r"\b([A-Z][A-Z0-9]*-USD)\b")
_BOLD_PAIR_LIST_RE = re.compile(
    r"\*\*([A-Z][A-Z0-9]*(?:,\s*[A-Z][A-Z0-9]*)+)\*\*"
)
_OPENED_PAIR_RE = re.compile(
    r"OPENED\s+(?:LONG|SHORT)\s+([A-Z][A-Z0-9]*-USD)",
    re.IGNORECASE,
)

_SIGNAL_TUPLE_RE = re.compile(
    r"([A-Z0-9:-]+):bb=([^,]+),macd=([^,]+),sig=([^,]+),hist=([^,]+),"
    r"gap=([^,]+),hr=([^,]+),tr=([^,]+),mom=([^,]+),"
    r"fL=([^,]+),fS=([^,]+),aL=([^,]+),aS=([^,]+),sL=([^,|;\s]+),sS=([^,|;\s]+)"
)
_FILTER_4H_TUPLE_RE = re.compile(
    r"([A-Z0-9:-]+):tr=([^,]+)(?:,bb=([^,]+))?(?:,macd=([^,]+))?"
    r"(?:,sig=([^,]+))?(?:,hist=([^,]+))?,pass=([01])"
)


def parse_dt(value: str) -> dt.datetime:
    if "T" in value:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            dt.timezone.utc
        )
    return dt.datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)


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


def _normalize_trend(value: str) -> str:
    token = value.strip().lower()
    if token in {"bull", "bullish"}:
        return "bullish"
    if token in {"bear", "bearish"}:
        return "bearish"
    return token


def _normalize_momentum(value: str) -> str:
    token = value.strip().lower()
    if token in {"inc", "increasing"}:
        return "increasing"
    if token in {"dec", "decreasing"}:
        return "decreasing"
    return token


def _parse_bool_flag(value: str) -> bool:
    return value.strip() in {"1", "true", "True"}


def _parse_signals_1h(raw: str) -> dict[str, JournalSignal1h]:
    signals: dict[str, JournalSignal1h] = {}
    for match in _SIGNAL_TUPLE_RE.finditer(raw):
        pair = match.group(1)
        signals[pair] = JournalSignal1h(
            pair=pair,
            bb_pos_pct=float(match.group(2)),
            macd=float(match.group(3)),
            signal_line=float(match.group(4)),
            histogram=float(match.group(5)),
            macd_gap_ratio=float(match.group(6)),
            hist_ratio=float(match.group(7)),
            trend=_normalize_trend(match.group(8)),
            momentum=_normalize_momentum(match.group(9)),
            formal_long=_parse_bool_flag(match.group(10)),
            formal_short=_parse_bool_flag(match.group(11)),
            adaptive_long=_parse_bool_flag(match.group(12)),
            adaptive_short=_parse_bool_flag(match.group(13)),
            strength_long=float(match.group(14)),
            strength_short=float(match.group(15)),
        )
    return signals


def _parse_filter_4h(raw: str) -> dict[str, Filter4h]:
    filters: dict[str, Filter4h] = {}
    for match in _FILTER_4H_TUPLE_RE.finditer(raw):
        pair = match.group(1)
        bb_raw = match.group(3)
        macd_raw = match.group(4)
        sig_raw = match.group(5)
        hist_raw = match.group(6)
        filters[pair] = Filter4h(
            pair=pair,
            trend=_normalize_trend(match.group(2)),
            bb_pos_pct=float(bb_raw) if bb_raw else None,
            macd=float(macd_raw) if macd_raw else None,
            signal_line=float(sig_raw) if sig_raw else None,
            histogram=float(hist_raw) if hist_raw else None,
            passed=match.group(7) == "1",
        )
    return filters


def _extract_pairs_from_tick_narrative(line: str) -> list[str]:
    for pattern in (_REVIEWED_MACD_LIST_RE, _PAREN_MACD_REVIEWS_RE):
        match = pattern.search(line)
        if match:
            pairs = _normalize_journal_pair_list(match.group(1))
            if len(pairs) >= 2:
                return pairs
    bold_match = _BOLD_PAIR_LIST_RE.search(line)
    if bold_match:
        pairs = _normalize_journal_pair_list(bold_match.group(1))
        if pairs:
            return pairs[:8]
    opened_match = _OPENED_PAIR_RE.search(line)
    pairs: list[str] = []
    if opened_match:
        pairs.append(opened_match.group(1).upper())
    for symbol_match in _SYMBOL_USD_RE.finditer(line):
        pair = symbol_match.group(1)
        if pair not in pairs:
            pairs.append(pair)
    if pairs:
        return pairs[:8]
    return []


def _extract_pre_open_streak_from_narrative(line: str) -> int | None:
    match = _PRE_OPEN_STREAK_RE.search(line)
    if match:
        return int(match.group(1))
    return None


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


def _parse_decision_line(line: str, tick_time_map: dict[int, dt.datetime]) -> TickMeta | None:
    decision_match = _DECISION_RE.match(line)
    if not decision_match:
        return None
    tick_field_match = _DECISION_TICK_RE.search(line)
    tick_number = (
        int(tick_field_match.group(1))
        if tick_field_match
        else int(decision_match.group(1))
    )
    if tick_number not in tick_time_map:
        return None

    pairs_match = _MACD_PAIRS_RE.search(line)
    reviewed_pairs = pairs_match.group(1).split(",") if pairs_match else []
    streak_match = _NEUTRAL_STREAK_RE.search(line)
    entry_class_match = _ENTRY_CLASS_RE.search(line)
    tradeable_match = _TRADEABLE_COUNT_RE.search(line)
    analyzed_match = _SCANNER_ANALYZED_RE.search(line)
    queue_match = _QUEUE_TOTAL_RE.search(line)
    signals_match = _SIGNALS_1H_RE.search(line)
    filter_match = _FILTER_4H_RE.search(line)

    return TickMeta(
        tick=tick_number,
        timestamp=tick_time_map[tick_number],
        macd_pairs=reviewed_pairs,
        neutral_pressure_streak=int(streak_match.group(1)) if streak_match else None,
        entry_class=entry_class_match.group(1) if entry_class_match else None,
        tradeable_count=int(tradeable_match.group(1)) if tradeable_match else None,
        scanner_analyzed=int(analyzed_match.group(1)) if analyzed_match else None,
        queue_total=_normalize_journal_pair_list(queue_match.group(1))
        if queue_match
        else [],
        signals_1h=_parse_signals_1h(signals_match.group(1)) if signals_match else {},
        filter_4h=_parse_filter_4h(filter_match.group(1)) if filter_match else {},
    )


def parse_journal_ticks(
    journal_text: str,
    session_dir: Path | None = None,
) -> dict[int, TickMeta]:
    tick_time_map: dict[int, dt.datetime] = {}
    tick_header_lines: dict[int, str] = {}
    for line in journal_text.splitlines():
        tick_match = _TICK_RE.match(line)
        if not tick_match:
            continue
        tick_number = int(tick_match.group(1))
        tick_time_map[tick_number] = parse_dt(tick_match.group(2))
        tick_header_lines[tick_number] = line

    tick_meta_map: dict[int, TickMeta] = {}
    for line in journal_text.splitlines():
        parsed = _parse_decision_line(line, tick_time_map)
        if parsed is not None:
            tick_meta_map[parsed.tick] = parsed

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

    if session_dir is not None:
        tick_meta_map = enrich_ticks_from_snapshots(tick_meta_map, session_dir)

    last_pairs: list[str] = []
    last_signals: dict[str, JournalSignal1h] = {}
    last_filter_4h: dict[str, Filter4h] = {}
    last_tradeable_count: int | None = None
    last_scanner_analyzed: int | None = None
    last_queue_total: list[str] = []

    for tick_number in sorted(tick_meta_map):
        meta = tick_meta_map[tick_number]
        header_line = tick_header_lines.get(tick_number, "")

        pre_open_streak = _extract_pre_open_streak_from_narrative(header_line)
        if pre_open_streak is not None and (
            meta.neutral_pressure_streak is None
            or meta.neutral_pressure_streak < pre_open_streak
        ):
            meta = TickMeta(
                tick=meta.tick,
                timestamp=meta.timestamp,
                macd_pairs=meta.macd_pairs,
                neutral_pressure_streak=pre_open_streak,
                entry_class=meta.entry_class,
                tradeable_count=meta.tradeable_count,
                scanner_analyzed=meta.scanner_analyzed,
                queue_total=meta.queue_total,
                signals_1h=meta.signals_1h,
                filter_4h=meta.filter_4h,
            )
            tick_meta_map[tick_number] = meta

        opened_match = _OPENED_PAIR_RE.search(header_line)
        if opened_match and "4h bullish" in header_line.lower():
            opened_pair = opened_match.group(1).upper()
            filter_map = dict(meta.filter_4h)
            filter_map[opened_pair] = Filter4h(
                pair=opened_pair,
                trend="bullish",
                passed=True,
            )
            meta = TickMeta(
                tick=meta.tick,
                timestamp=meta.timestamp,
                macd_pairs=meta.macd_pairs,
                neutral_pressure_streak=meta.neutral_pressure_streak,
                entry_class=meta.entry_class or "opened_long",
                tradeable_count=meta.tradeable_count,
                scanner_analyzed=meta.scanner_analyzed,
                queue_total=meta.queue_total,
                signals_1h=meta.signals_1h,
                filter_4h=filter_map,
            )
            tick_meta_map[tick_number] = meta
        elif opened_match and "4h bearish" in header_line.lower():
            opened_pair = opened_match.group(1).upper()
            filter_map = dict(meta.filter_4h)
            filter_map[opened_pair] = Filter4h(
                pair=opened_pair,
                trend="bearish",
                passed=True,
            )
            meta = TickMeta(
                tick=meta.tick,
                timestamp=meta.timestamp,
                macd_pairs=meta.macd_pairs,
                neutral_pressure_streak=meta.neutral_pressure_streak,
                entry_class=meta.entry_class or "opened_short",
                tradeable_count=meta.tradeable_count,
                scanner_analyzed=meta.scanner_analyzed,
                queue_total=meta.queue_total,
                signals_1h=meta.signals_1h,
                filter_4h=filter_map,
            )
            tick_meta_map[tick_number] = meta

        if meta.macd_pairs:
            if len(meta.macd_pairs) >= len(last_pairs):
                last_pairs = meta.macd_pairs
        if meta.signals_1h:
            last_signals = dict(meta.signals_1h)
        if meta.filter_4h:
            last_filter_4h = dict(meta.filter_4h)
        if meta.tradeable_count is not None:
            last_tradeable_count = meta.tradeable_count
        if meta.scanner_analyzed is not None:
            last_scanner_analyzed = meta.scanner_analyzed
        if meta.queue_total:
            last_queue_total = list(meta.queue_total)

        needs_carry = (
            not meta.macd_pairs
            or not meta.signals_1h
            or meta.tradeable_count is None
        )
        if not needs_carry:
            continue

        tick_meta_map[tick_number] = TickMeta(
            tick=meta.tick,
            timestamp=meta.timestamp,
            macd_pairs=list(meta.macd_pairs or last_pairs),
            neutral_pressure_streak=meta.neutral_pressure_streak,
            entry_class=meta.entry_class,
            tradeable_count=meta.tradeable_count
            if meta.tradeable_count is not None
            else last_tradeable_count,
            scanner_analyzed=meta.scanner_analyzed
            if meta.scanner_analyzed is not None
            else last_scanner_analyzed,
            queue_total=list(meta.queue_total or last_queue_total),
            signals_1h=dict(meta.signals_1h or last_signals),
            filter_4h=dict(meta.filter_4h or last_filter_4h),
        )

        if meta.neutral_pressure_streak is None:
            streak = _extract_streak_from_tick_narrative(header_line)
            if streak is not None:
                carried = tick_meta_map[tick_number]
                tick_meta_map[tick_number] = TickMeta(
                    tick=carried.tick,
                    timestamp=carried.timestamp,
                    macd_pairs=carried.macd_pairs,
                    neutral_pressure_streak=streak,
                    entry_class=carried.entry_class,
                    tradeable_count=carried.tradeable_count,
                    scanner_analyzed=carried.scanner_analyzed,
                    queue_total=carried.queue_total,
                    signals_1h=carried.signals_1h,
                    filter_4h=carried.filter_4h,
                )

    return tick_meta_map


def enrich_ticks_from_snapshots(
    tick_meta_map: dict[int, TickMeta],
    session_dir: Path,
) -> dict[int, TickMeta]:
    """Merge structured decision telemetry from snapshot files when journal rows are missing."""
    snapshots_dir = session_dir / "snapshots"
    if not snapshots_dir.is_dir():
        return tick_meta_map

    tick_time_map = {tick: meta.timestamp for tick, meta in tick_meta_map.items()}
    enriched = dict(tick_meta_map)

    for snapshot_path in sorted(snapshots_dir.glob("snapshot_*.md")):
        for line in snapshot_path.read_text(encoding="utf-8").splitlines():
            parsed = _parse_decision_line(line, tick_time_map)
            if parsed is None:
                continue
            existing = enriched.get(parsed.tick)
            if existing is None:
                enriched[parsed.tick] = parsed
                continue
            use_snapshot = (
                len(parsed.signals_1h) > len(existing.signals_1h)
                or (
                    parsed.filter_4h
                    and not existing.filter_4h
                )
                or (
                    parsed.neutral_pressure_streak is not None
                    and existing.neutral_pressure_streak is None
                )
            )
            if not use_snapshot:
                continue
            enriched[parsed.tick] = TickMeta(
                tick=parsed.tick,
                timestamp=existing.timestamp,
                macd_pairs=parsed.macd_pairs or existing.macd_pairs,
                neutral_pressure_streak=parsed.neutral_pressure_streak
                if parsed.neutral_pressure_streak is not None
                else existing.neutral_pressure_streak,
                entry_class=parsed.entry_class or existing.entry_class,
                tradeable_count=parsed.tradeable_count
                if parsed.tradeable_count is not None
                else existing.tradeable_count,
                scanner_analyzed=parsed.scanner_analyzed
                if parsed.scanner_analyzed is not None
                else existing.scanner_analyzed,
                queue_total=parsed.queue_total or existing.queue_total,
                signals_1h=parsed.signals_1h or existing.signals_1h,
                filter_4h={**existing.filter_4h, **parsed.filter_4h},
            )

    return enriched
