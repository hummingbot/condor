"""Strategy-wide performance digest for trading agents.

Computes time-window stats, category breakdowns, and rule-based insights from
executor API rows and journal metadata. Used by /performance Telegram command.
"""

from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from condor.trading_agent.performance import fetch_agent_performance_batch

_DATA_ROOT = Path(__file__).resolve().parent.parent.parent / "trading_agents"

WINDOWS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

_ENTRY_CLASS_RE = re.compile(
    r"entry_class=(?P<cls>formal|regime_adaptive_half_size|exception_formal_half_size|hold)"
)
_PAIR_RE = re.compile(r"pair=(?P<pair>[A-Za-z0-9:._-]+(?:-USD)?)")
_DECISION_LINE_RE = re.compile(
    r"^\-\s+\*\*#(?P<tick>\d+)\*\*\s+\((?P<time>\d{2}:\d{2})\)\s+(?P<body>.+)$"
)


def parse_timestamp(raw: str | None) -> datetime | None:
    """Parse ISO or epoch timestamp from executor row."""
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (ValueError, OSError):
            return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    except (ValueError, OSError):
        return None


def row_closed_at(row: dict[str, Any]) -> datetime | None:
    """Best-effort closed timestamp for a normalized executor row."""
    closed = parse_timestamp(row.get("closed_at"))
    if closed:
        return closed
    ts = row.get("close_timestamp")
    if ts:
        return parse_timestamp(str(ts))
    return None


def row_created_at(row: dict[str, Any]) -> datetime | None:
    created = parse_timestamp(row.get("created_at"))
    if created:
        return created
    ts = row.get("timestamp")
    if ts:
        return parse_timestamp(str(ts))
    return None


def row_has_reliable_notional(row: dict[str, Any]) -> bool:
    """True when executor row has actual size data (not inferred from config)."""
    if float(row.get("amount") or 0) > 0:
        return True
    return float(row.get("net_pnl_pct") or 0) != 0


def row_notional_reliable(row: dict[str, Any]) -> float:
    """Notional from API fields only — no config fallback."""
    amount = float(row.get("amount") or 0)
    if amount > 0:
        return amount
    pnl = float(row.get("pnl") or 0)
    pct = float(row.get("net_pnl_pct") or 0)
    if pct != 0:
        derived = abs(pnl / (pct / 100.0))
        if derived > 0:
            return derived
    return 0.0


def row_notional(row: dict[str, Any], default_quote: float) -> float:
    """Estimate position notional for entry-class heuristics (may use config fallback)."""
    reliable = row_notional_reliable(row)
    if reliable > 0:
        return reliable
    if default_quote > 0:
        return default_quote
    return 0.0


def side_label(raw: Any) -> str:
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


def exit_bucket(close_type: str) -> str:
    ct = (close_type or "").upper().replace(" ", "_").replace("-", "_")
    if "TAKE" in ct and "PROFIT" in ct:
        return "TP"
    if "STOP" in ct and "LOSS" in ct:
        return "SL"
    return "Agent"


def enumerate_strategy_agent_ids(
    slug: str, agent_dir: Path | None = None, sessions_only: bool = True
) -> list[tuple[str, int, str]]:
    """Return (agent_id, session_num, kind) for every session on disk."""
    root = agent_dir or (_DATA_ROOT / slug)
    ids: list[tuple[str, int, str]] = []
    for dirname in ("sessions", "trading_sessions"):
        d = root / dirname
        if not d.exists():
            continue
        for sd in d.iterdir():
            if not sd.is_dir() or not sd.name.startswith("session_"):
                continue
            try:
                n = int(sd.name.split("_", 1)[1])
            except (ValueError, IndexError):
                continue
            ids.append((f"{slug}_{n}", n, "session"))
    if not sessions_only:
        for dirname in ("dry_runs", "experiments"):
            d = root / dirname
            if not d.exists():
                continue
            for f in d.glob("experiment_*.md"):
                m = re.match(r"experiment_(\d+)\.md", f.name)
                if not m:
                    continue
                n = int(m.group(1))
                ids.append((f"{slug}_e{n}", n, "experiment"))
    seen: set[str] = set()
    unique: list[tuple[str, int, str]] = []
    for tup in ids:
        if tup[0] in seen:
            continue
        seen.add(tup[0])
        unique.append(tup)
    return unique


@dataclass
class BucketStats:
    label: str
    count: int = 0
    pnl: float = 0.0
    notional: float = 0.0
    wins: int = 0

    @property
    def win_rate(self) -> float:
        return self.wins / self.count if self.count else 0.0

    @property
    def return_pct(self) -> float:
        return (self.pnl / self.notional * 100) if self.notional else 0.0

    @property
    def avg_pnl(self) -> float:
        return self.pnl / self.count if self.count else 0.0


@dataclass
class WindowStats:
    label: str
    count: int = 0
    pnl: float = 0.0
    wins: int = 0
    sized_count: int = 0
    sized_notional: float = 0.0
    sized_pnl: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.count if self.count else 0.0

    @property
    def return_pct(self) -> float:
        return (self.sized_pnl / self.sized_notional * 100) if self.sized_notional else 0.0


@dataclass
class PerformanceDigest:
    slug: str
    session_filter: int | None = None
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    open_count: int = 0
    closed_count: int = 0
    windows: dict[str, WindowStats] = field(default_factory=dict)
    by_exit: dict[str, BucketStats] = field(default_factory=dict)
    by_entry: dict[str, BucketStats] = field(default_factory=dict)
    by_side: dict[str, BucketStats] = field(default_factory=dict)
    insights: list[str] = field(default_factory=list)
    running_agent_id: str | None = None
    running_tick: int | None = None


def parse_journal_decisions(journal_text: str) -> list[dict[str, Any]]:
    """Parse Decisions section lines for entry_class and pair."""
    entries: list[dict[str, Any]] = []
    in_decisions = False
    for line in journal_text.splitlines():
        if line.strip() == "## Decisions":
            in_decisions = True
            continue
        if in_decisions and line.startswith("## "):
            break
        if not in_decisions or not line.startswith("- "):
            continue
        m = _DECISION_LINE_RE.match(line.strip())
        body = m.group("body") if m else line[2:].strip()
        tick = int(m.group("tick")) if m else 0
        cls_m = _ENTRY_CLASS_RE.search(body)
        pair_m = _PAIR_RE.search(body)
        entry_class = cls_m.group("cls") if cls_m else ""
        pair = pair_m.group("pair") if pair_m else ""
        if entry_class in ("formal", "regime_adaptive_half_size", "exception_formal_half_size"):
            entries.append({"tick": tick, "entry_class": entry_class, "pair": pair, "body": body})
    return entries


def load_journal_entries(agent_dir: Path, session_nums: list[int] | None = None) -> list[dict[str, Any]]:
    """Load entry_class decisions from all (or selected) session journals."""
    sessions_dir = agent_dir / "sessions"
    if not sessions_dir.is_dir():
        sessions_dir = agent_dir / "trading_sessions"
    if not sessions_dir.is_dir():
        return []

    entries: list[dict[str, Any]] = []
    for sd in sorted(sessions_dir.iterdir()):
        if not sd.is_dir() or not sd.name.startswith("session_"):
            continue
        try:
            num = int(sd.name.split("_", 1)[1])
        except (ValueError, IndexError):
            continue
        if session_nums is not None and num not in session_nums:
            continue
        journal = sd / "journal.md"
        if not journal.is_file():
            continue
        text = journal.read_text(encoding="utf-8", errors="replace")
        for e in parse_journal_decisions(text):
            e["session_num"] = num
            entries.append(e)
    return entries


def _entry_class_label(raw: str) -> str:
    if raw in ("regime_adaptive_half_size", "exception_formal_half_size"):
        return "Adaptive"
    if raw == "formal":
        return "Formal"
    return "Unknown"


def classify_entry_class(
    row: dict[str, Any],
    journal_entries: list[dict[str, Any]],
    total_amount_quote: float,
) -> str:
    """Classify closed row as Formal or Adaptive."""
    pair = row.get("pair") or ""

    candidates = [e for e in journal_entries if e.get("pair") == pair and e.get("entry_class")]
    if candidates:
        best = candidates[-1]
        label = _entry_class_label(best.get("entry_class", ""))
        if label != "Unknown":
            return label

    amount = float(row.get("amount") or 0)
    if total_amount_quote > 0 and amount > 0:
        ratio = amount / total_amount_quote
        if 0.35 <= ratio <= 0.65:
            return "Adaptive"
        if ratio >= 0.75:
            return "Formal"
    return "Formal"


def _accumulate_bucket(
    buckets: dict[str, BucketStats],
    label: str,
    row: dict[str, Any],
    default_quote: float,
) -> None:
    if label not in buckets:
        buckets[label] = BucketStats(label=label)
    b = buckets[label]
    pnl = float(row.get("pnl") or 0)
    notional = row_notional(row, default_quote)
    b.count += 1
    b.pnl += pnl
    b.notional += notional
    if pnl > 0:
        b.wins += 1


def compute_window_stats(
    closed_rows: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, WindowStats]:
    now = now or datetime.now(timezone.utc)
    out: dict[str, WindowStats] = {}
    for label, delta in WINDOWS.items():
        cutoff = now - delta
        ws = WindowStats(label=label)
        for row in closed_rows:
            closed = row_closed_at(row)
            if closed is None or closed < cutoff:
                continue
            pnl = float(row.get("pnl") or 0)
            ws.count += 1
            ws.pnl += pnl
            if pnl > 0:
                ws.wins += 1
            if row_has_reliable_notional(row):
                ws.sized_count += 1
                ws.sized_notional += row_notional_reliable(row)
                ws.sized_pnl += pnl
        out[label] = ws
    return out


def compute_category_stats(
    closed_rows: list[dict[str, Any]],
    journal_entries: list[dict[str, Any]],
    total_amount_quote: float,
) -> tuple[dict[str, BucketStats], dict[str, BucketStats], dict[str, BucketStats]]:
    by_exit: dict[str, BucketStats] = {}
    by_entry: dict[str, BucketStats] = {}
    by_side: dict[str, BucketStats] = {}

    for row in closed_rows:
        _accumulate_bucket(by_exit, exit_bucket(row.get("close_type", "")), row, total_amount_quote)
        entry_label = classify_entry_class(row, journal_entries, total_amount_quote)
        _accumulate_bucket(by_entry, entry_label, row, total_amount_quote)
        _accumulate_bucket(by_side, side_label(row.get("side")), row, total_amount_quote)

    return by_exit, by_entry, by_side


def generate_insights(
    by_exit: dict[str, BucketStats],
    by_entry: dict[str, BucketStats],
    by_side: dict[str, BucketStats],
) -> list[str]:
    """Rule-based insight bullets (max 3)."""
    insights: list[str] = []

    formal = by_entry.get("Formal")
    adaptive = by_entry.get("Adaptive")
    if adaptive and adaptive.count >= 2:
        if adaptive.pnl < 0 and (not formal or adaptive.pnl < formal.pnl):
            insights.append(
                f"Adaptive entries: {adaptive.count} trades, ${adaptive.pnl:+.2f} — "
                "review NEUTRAL_PRESSURE threshold or disable adaptive mode."
            )
        elif adaptive.pnl > 0 and formal and formal.pnl < 0:
            insights.append(
                f"Adaptive entries outperform formal (${adaptive.pnl:+.2f} vs ${formal.pnl:+.2f})."
            )

    long_b = by_side.get("LONG")
    short_b = by_side.get("SHORT")
    if long_b and short_b and long_b.count >= 2 and short_b.count >= 2:
        if long_b.win_rate < short_b.win_rate - 0.15:
            insights.append(
                f"LONG win rate {long_b.win_rate:.0%} vs SHORT {short_b.win_rate:.0%} — "
                "check 4h filter on long entries."
            )
        elif short_b.win_rate < long_b.win_rate - 0.15:
            insights.append(
                f"SHORT win rate {short_b.win_rate:.0%} vs LONG {long_b.win_rate:.0%} — "
                "review short trigger rules."
            )

    sl = by_exit.get("SL")
    tp = by_exit.get("TP")
    if sl and sl.count >= 2 and sl.pnl < 0:
        insights.append(
            f"Stop-loss exits: {sl.count} trades, ${sl.pnl:+.2f} "
            f"(${sl.avg_pnl:+.2f}/trade) — review SL distance or entry timing."
        )
    if tp and sl and tp.count >= 2 and sl.count >= 2 and len(insights) < 3:
        if sl.count > tp.count:
            insights.append(
                f"More stop-losses ({sl.count}) than take-profits ({tp.count}) — "
                "entries may be timing poorly or SL too tight."
            )
        elif tp.pnl > 0:
            insights.append(
                f"Take-profit exits: {tp.count} trades, ${tp.pnl:+.2f} "
                f"(${tp.avg_pnl:+.2f}/trade)."
            )

    all_buckets: list[BucketStats] = []
    for side_name, b in by_side.items():
        if b.count >= 2:
            all_buckets.append(b)
    if all_buckets and len(insights) < 3:
        best = max(all_buckets, key=lambda b: b.pnl)
        if best.pnl > 0:
            insights.append(
                f"{best.label} side: {best.count} trades, ${best.pnl:+.2f} ({best.win_rate:.0%} WR) — strongest bucket."
            )

    return insights[:3]


def build_digest(
    slug: str,
    perf_rows: list[dict[str, Any]],
    journal_entries: list[dict[str, Any]],
    total_amount_quote: float,
    session_filter: int | None = None,
    running_agent_id: str | None = None,
    running_tick: int | None = None,
    now: datetime | None = None,
) -> PerformanceDigest:
    running = [r for r in perf_rows if r.get("status") == "RUNNING"]
    closed = [r for r in perf_rows if r.get("status") != "RUNNING"]

    realized = sum(float(r.get("pnl") or 0) for r in closed)
    unrealized = sum(float(r.get("pnl") or 0) for r in running)

    windows = compute_window_stats(closed, now=now)
    by_exit, by_entry, by_side = compute_category_stats(
        closed, journal_entries, total_amount_quote
    )
    insights = generate_insights(by_exit, by_entry, by_side)

    return PerformanceDigest(
        slug=slug,
        session_filter=session_filter,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        open_count=len(running),
        closed_count=len(closed),
        windows=windows,
        by_exit=by_exit,
        by_entry=by_entry,
        by_side=by_side,
        insights=insights,
        running_agent_id=running_agent_id,
        running_tick=running_tick,
    )


def _fmt_money(value: float) -> str:
    return f"${value:+.2f}"


def _fmt_pct(value: float, sized_count: int) -> str:
    if sized_count <= 0:
        return "—"
    return f"{value:+.1f}%"


def _fmt_window_line(ws: WindowStats) -> str:
    pct = _fmt_pct(ws.return_pct, ws.sized_count)
    wr = f"{ws.win_rate:.0%} WR" if ws.count else "—"
    sized = f" · {ws.sized_count} sized" if ws.count and ws.sized_count < ws.count else ""
    if ws.sized_count and ws.sized_count == ws.count:
        sized = ""
    return (
        f"  {ws.label:>3}  {_fmt_money(ws.pnl):>8}  {pct:>7}  "
        f"{ws.count:>2} trades  {wr}{sized}"
    )


def _h(text: str) -> str:
    return html_module.escape(str(text))


def _money_html(value: float) -> str:
    text = f"${value:+.2f}"
    if value > 0:
        return f"<b>{_h(text)}</b>"
    if value < 0:
        return f"<b>{_h(text)}</b>"
    return _h(text)


def format_performance_report_html(digest: PerformanceDigest) -> str:
    """Telegram HTML report (parse_mode=HTML)."""
    scope = f"session {digest.session_filter}" if digest.session_filter else "all sessions"
    parts = [
        f"<b>📊 Performance</b>",
        f"<code>{_h(digest.slug)}</code>",
        f"<i>{_h(scope)} · {digest.closed_count} closed · {digest.open_count} open</i>",
    ]
    if digest.running_agent_id:
        tick = f" · tick #{digest.running_tick}" if digest.running_tick else ""
        parts.append(f"<i>Live:</i> <code>{_h(digest.running_agent_id)}</code>{_h(tick)}")

    parts.append("")
    parts.append("<b>Windows</b> <i>(return % from sized trades only)</i>")
    for label in ("1h", "24h", "7d", "30d"):
        ws = digest.windows.get(label, WindowStats(label=label))
        pct = _fmt_pct(ws.return_pct, ws.sized_count)
        wr = f"{ws.win_rate:.0%} WR" if ws.count else "—"
        line = f"• <b>{label}</b>  {_money_html(ws.pnl)}"
        if pct != "—":
            line += f"  ({_h(pct)})"
        line += f"  · {ws.count} trades · {_h(wr)}"
        if ws.count and ws.sized_count < ws.count:
            line += f"  · <i>{ws.sized_count} sized</i>"
        parts.append(line)

    parts.extend([
        "",
        "<b>Totals</b>",
        f"Realized {_money_html(digest.realized_pnl)} · "
        f"Unrealized {_money_html(digest.unrealized_pnl)}",
    ])

    exit_labels = {"TP": "Take profit", "SL": "Stop loss", "Agent": "Agent close"}
    entry_labels = {"Formal": "Formal", "Adaptive": "Adaptive", "Unknown": "Unknown"}
    side_labels = {"LONG": "Long", "SHORT": "Short", "?": "Other"}

    def _section(title: str, buckets: dict[str, BucketStats], labels: dict[str, str], order: list[str], *, avg: bool = False) -> None:
        rows = []
        for key in order:
            b = buckets.get(key)
            if not b or not b.count:
                continue
            name = labels.get(key, key)
            row = f"• {_h(name)} — {b.count} · {_money_html(b.pnl)}"
            if avg:
                row += f" · {_h(_fmt_money(b.avg_pnl))}/trade"
            rows.append(row)
        if rows:
            parts.extend(["", f"<b>{title}</b>", *rows])

    _section("By exit type", digest.by_exit, exit_labels, ["TP", "SL", "Agent"], avg=True)
    _section("By entry class", digest.by_entry, entry_labels, ["Formal", "Adaptive", "Unknown"])
    _section("By side", digest.by_side, side_labels, ["LONG", "SHORT", "?"])

    if digest.insights:
        parts.append("")
        for tip in digest.insights:
            parts.append(f"💡 {_h(tip)}")

    return "\n".join(parts)


def _fmt_bucket_rows(
    buckets: dict[str, BucketStats],
    labels: dict[str, str],
    order: list[str],
    *,
    show_avg: bool = False,
) -> list[str]:
    lines: list[str] = []
    for key in order:
        b = buckets.get(key)
        if not b or not b.count:
            continue
        name = labels.get(key, key)
        avg = f"  ({_fmt_money(b.avg_pnl)}/trade)" if show_avg else ""
        lines.append(f"  {name:<14} {b.count:>3}  {_fmt_money(b.pnl):>8}{avg}")
    return lines


def format_performance_report(digest: PerformanceDigest) -> str:
    """Plain-text fallback when HTML rendering fails."""
    scope = f"session {digest.session_filter}" if digest.session_filter else "all sessions"
    lines = [
        f"📊 PERFORMANCE — {digest.slug}",
        f"{scope} · {digest.closed_count} closed · {digest.open_count} open",
    ]

    if digest.running_agent_id:
        tick_part = f" · tick #{digest.running_tick}" if digest.running_tick else ""
        lines.append(f"Live: {digest.running_agent_id}{tick_part}")

    lines.extend(["", "Windows (return % from sized trades only)", "       PnL      Ret%  Trades"])
    for label in ("1h", "24h", "7d", "30d"):
        ws = digest.windows.get(label, WindowStats(label=label))
        lines.append(_fmt_window_line(ws))

    lines.extend([
        "",
        "Totals",
        f"  Realized {_fmt_money(digest.realized_pnl)} · "
        f"Unrealized {_fmt_money(digest.unrealized_pnl)}",
    ])

    exit_labels = {"TP": "Take profit", "SL": "Stop loss", "Agent": "Agent close"}
    entry_labels = {"Formal": "Formal", "Adaptive": "Adaptive", "Unknown": "Unknown"}
    side_labels = {"LONG": "Long", "SHORT": "Short", "?": "Other"}

    exit_rows = _fmt_bucket_rows(digest.by_exit, exit_labels, ["TP", "SL", "Agent"], show_avg=True)
    if exit_rows:
        lines.extend(["", "By exit type", *exit_rows])

    entry_rows = _fmt_bucket_rows(digest.by_entry, entry_labels, ["Formal", "Adaptive", "Unknown"])
    if entry_rows:
        lines.extend(["", "By entry class", *entry_rows])

    side_rows = _fmt_bucket_rows(digest.by_side, side_labels, ["LONG", "SHORT", "?"])
    if side_rows:
        lines.extend(["", "By side", *side_rows])

    if digest.insights:
        lines.append("")
        for tip in digest.insights:
            lines.append(f"💡 {tip}")

    return "\n".join(lines)


async def fetch_strategy_performance_rows(
    client: Any,
    slug: str,
    agent_dir: Path,
    session_nums: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Fetch and flatten executor rows for a strategy (optionally filtered sessions)."""
    ids = enumerate_strategy_agent_ids(slug, agent_dir, sessions_only=True)
    if session_nums is not None:
        ids = [t for t in ids if t[1] in session_nums]
    if not ids:
        return []

    agent_ids = [aid for aid, _, _ in ids]
    perf_map = await fetch_agent_performance_batch(client, agent_ids)
    rows: list[dict[str, Any]] = []
    for aid in agent_ids:
        perf = perf_map.get(aid)
        if perf:
            rows.extend(perf.executors)
    return rows


async def build_strategy_digest(
    client: Any,
    slug: str,
    agent_dir: Path,
    total_amount_quote: float,
    session_filter: int | None = None,
    running_agent_id: str | None = None,
    running_tick: int | None = None,
) -> PerformanceDigest:
    session_nums = [session_filter] if session_filter is not None else None
    rows = await fetch_strategy_performance_rows(
        client, slug, agent_dir, session_nums=session_nums
    )
    journal_entries = load_journal_entries(agent_dir, session_nums=session_nums)
    return build_digest(
        slug=slug,
        perf_rows=rows,
        journal_entries=journal_entries,
        total_amount_quote=total_amount_quote,
        session_filter=session_filter,
        running_agent_id=running_agent_id,
        running_tick=running_tick,
    )
