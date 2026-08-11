"""The nightly job: small permanent tables, and partition housekeeping.

Retention and DAU computed over raw events mean a full scan per dashboard
panel, and raw events expire at :func:`telemetry_server.config.retention_days`
anyway. So every question is answered from ``daily_metrics``, which this module
writes and which is never deleted. Backfilling a rollup after dropping the raw
data it came from is impossible, which is why this exists from day one rather
than "later".

The aggregation is deliberately split: SQL does the grouping (cheap, indexed,
and identical in both dialects), Python does the date arithmetic and the
cohort matching. Date arithmetic is where the two dialects diverge most, and at
a few hundred installs the rows involved fit in a dict comfortably. The result
is one rollup that runs unchanged against Postgres and SQLite, so the numbers
the tests check are the numbers production computes.

Partition maintenance is the one Postgres-only part, and it is a no-op
elsewhere. The table names it builds come from dates this module computed; no
payload value ever reaches a formatted statement.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from telemetry_server import config
from telemetry_server.store import PostgresStore, Store

log = logging.getLogger(__name__)

TOP_N = 25


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


async def run(store: Store, today: date | None = None) -> int:
    """Recompute every metric. Returns how many rows were written."""
    today = today or datetime.now(timezone.utc).date()
    written = 0
    written += await _activity(store)
    written += await _retention(store)
    written += await _shares(store, today)
    written += await _ranks(store)
    written += await _reliability(store, today)
    written += await _agents(store, today)
    await maintain_partitions(store, today)
    return written


async def _activity(store: Store) -> int:
    """DAU from ``install_days``; WAU and MAU by rolling the same rows."""
    rows = await store.fetch("SELECT install_id, day FROM install_days")
    seen: dict[date, set] = defaultdict(set)
    for install_id, day in rows:
        parsed = _as_date(day)
        if parsed is not None:
            seen[parsed].add(install_id)
    if not seen:
        return 0

    written = 0
    for day in sorted(seen):
        for metric, window in (("dau", 1), ("wau", 7), ("mau", 30)):
            active: set = set()
            for offset in range(window):
                active |= seen.get(day - timedelta(days=offset), set())
            await store.put_metric(day, metric, "", len(active))
            written += 1
    return written


async def _retention(store: Store) -> int:
    """D1 / D7 / D30 from first-seen cohorts.

    The cohort day is ``MIN(day)`` in ``install_days`` rather than
    ``installs.first_seen``, so retention keeps working for an install whose
    row was upserted long after its first event.
    """
    rows = await store.fetch("SELECT install_id, day FROM install_days")
    days: dict[object, set] = defaultdict(set)
    for install_id, day in rows:
        parsed = _as_date(day)
        if parsed is not None:
            days[install_id].add(parsed)
    if not days:
        return 0

    cohorts: dict[date, list] = defaultdict(list)
    for install_id, active in days.items():
        cohorts[min(active)].append(install_id)

    written = 0
    for cohort_day, members in sorted(cohorts.items()):
        await store.put_metric(cohort_day, "cohort_size", "", len(members))
        written += 1
        for horizon in (1, 7, 30):
            target = cohort_day + timedelta(days=horizon)
            retained = sum(1 for m in members if target in days[m])
            await store.put_metric(cohort_day, f"retention_d{horizon}", "", retained)
            written += 1
    return written


async def _shares(store: Store, today: date) -> int:
    """Version, OS and LLM-provider mix, as of the run.

    These describe the fleet now rather than a past day, so they are stamped
    with the run date: a series of daily snapshots is exactly how "how long does
    an upgrade take to propagate" gets answered.
    """
    written = 0
    for metric, column in (("version_share", "version"), ("os_share", "os")):
        rows = await store.fetch(
            f"SELECT {column}, COUNT(*) FROM installs GROUP BY {column}"
        )
        for value, count in rows:
            await store.put_metric(today, metric, str(value or "unknown"), count)
            written += 1

    counts: dict[str, int] = defaultdict(int)
    for (raw,) in await store.fetch("SELECT llm_providers FROM installs"):
        for provider in _providers(raw):
            counts[provider] += 1
    for provider, count in counts.items():
        await store.put_metric(today, "provider_share", provider, count)
        written += 1
    return written


def _providers(raw: object) -> list[str]:
    """One column, two shapes: a Postgres ``text[]`` or a SQLite JSON string."""
    if isinstance(raw, (list, tuple)):
        return [str(p) for p in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return []
        return [str(p) for p in parsed] if isinstance(parsed, list) else []
    return []


#: The one property of each event that carries the signal worth ranking.
RANK_DIMS = {
    "command": ("command_rank", "name"),
    "action": ("action_rank", "module"),
    "routine_run": ("routine_rank", "routine"),
    "agent_turn": ("model_rank", "model"),
    "feature_first_use": ("activation", "feature"),
    "trade": ("trade_rank", "connector"),
}


async def _ranks(store: Store) -> int:
    """What is actually used: event volume per day, then the key dimensions."""
    written = 0
    rows = await store.fetch(
        f"SELECT {store.DAY} AS d, name, COUNT(*) FROM events GROUP BY d, name"
    )
    for day, name, count in rows:
        parsed = _as_date(day)
        if parsed is not None:
            await store.put_metric(parsed, "event_rank", str(name), count)
            written += 1

    for event_name, (metric, prop) in RANK_DIMS.items():
        dim = store.json_get("props", prop)
        rows = await store.fetch(
            f"SELECT {dim} AS v, COUNT(*) AS n FROM events WHERE name = $1 "
            f"GROUP BY v ORDER BY n DESC",
            event_name,
        )
        today = datetime.now(timezone.utc).date()
        for value, count in rows[:TOP_N]:
            if value is None:
                continue
            await store.put_metric(today, metric, str(value), count)
            written += 1
    return written


async def _reliability(store: Store, today: date) -> int:
    """Error rate per install-day, the top failure groups, and honest drops."""
    written = 0
    rows = await store.fetch(
        "SELECT day, SUM(events), SUM(errors) FROM install_days GROUP BY day"
    )
    for day, events, errors in rows:
        parsed = _as_date(day)
        if parsed is None or not events:
            continue
        await store.put_metric(parsed, "error_rate", "", (errors or 0) / events)
        written += 1

    exc = store.json_get("props", "exc_type")
    sig = store.json_get("props", "sig")
    groups = await store.fetch(
        f"SELECT {exc} AS e, {sig} AS s, COUNT(*) AS n FROM events "
        f"WHERE name = $1 GROUP BY e, s ORDER BY n DESC",
        "error",
    )
    for exc_type, signature, count in groups[:TOP_N]:
        await store.put_metric(
            today,
            "error_group",
            f"{exc_type or 'unknown'}:{signature or 'unknown'}",
            count,
        )
        written += 1

    service = store.json_get("props", "service")
    upstream = await store.fetch(
        f"SELECT {service} AS s, COUNT(*) AS n FROM events WHERE name = $1 GROUP BY s",
        "upstream_error",
    )
    for name, count in upstream:
        await store.put_metric(
            today, "upstream_error_rank", str(name or "other"), count
        )
        written += 1

    # Client-side rate limiting must never masquerade as "things got quieter",
    # so the confession the envelope carries is charted next to the volume.
    (dropped,) = (await store.fetch("SELECT SUM(dropped_total) FROM installs"))[0]
    (total,) = (await store.fetch("SELECT COUNT(*) FROM events"))[0]
    await store.put_metric(today, "dropped_total", "", dropped or 0)
    await store.put_metric(
        today,
        "dropped_rate",
        "",
        (dropped or 0) / (total + (dropped or 0)) if total else 0,
    )
    return written + 2


#: ``(event name, property, metric)`` triples whose distribution answers "how
#: are agents used" — provider and model mix, dry-run versus live, and how
#: often a confirmation is actually granted.
AGENT_DIMS = (
    ("agent_turn", "provider", "agent_provider"),
    ("agent_turn", "outcome", "agent_outcome"),
    ("agent_turn", "kind", "agent_kind"),
    ("strategy_run", "mode", "strategy_mode"),
    ("confirmation", "decision", "confirmation_decision"),
)

#: Averages worth a number rather than a distribution.
AGENT_AVERAGES = (
    ("agent_turn", "tool_calls", "agent_tool_calls_avg"),
    ("agent_turn", "duration_ms", "agent_duration_ms_avg"),
    ("routine_run", "duration_ms", "routine_duration_ms_avg"),
)


async def _agents(store: Store, today: date) -> int:
    """Agent economics: the mix, the cost per turn, and the trust decisions."""
    written = 0
    for event_name, prop, metric in AGENT_DIMS:
        dim = store.json_get("props", prop)
        rows = await store.fetch(
            f"SELECT {dim} AS v, COUNT(*) FROM events WHERE name = $1 GROUP BY v",
            event_name,
        )
        for value, count in rows:
            await store.put_metric(today, metric, str(value or "unknown"), count)
            written += 1

    for event_name, prop, metric in AGENT_AVERAGES:
        column = store.json_num("props", prop)
        rows = await store.fetch(
            f"SELECT AVG({column}) FROM events WHERE name = $1", event_name
        )
        average = rows[0][0] if rows else None
        if average is not None:
            await store.put_metric(today, metric, "", float(average))
            written += 1
    return written


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _next_month(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


async def maintain_partitions(store: Store, today: date) -> list[str]:
    """Create next month's partition, drop what has aged out.

    Returns the partition names it touched, which is what the tests assert on.
    Postgres only: SQLite has no partitioning, and at the volume SQLite is used
    for, ``DELETE`` is the right answer anyway.
    """
    if not isinstance(store, PostgresStore):
        cutoff = today - timedelta(days=config.retention_days())
        await store.execute("DELETE FROM events WHERE received_at < $1", cutoff)
        return []

    touched = []
    current = _month_start(today)
    for start in (current, _next_month(current)):
        end = _next_month(start)
        name = f"events_{start:%Y_%m}"
        await store.execute(
            f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF events "
            f"FOR VALUES FROM ('{start:%Y-%m-%d}') TO ('{end:%Y-%m-%d}')"
        )
        touched.append(name)

    cutoff = _month_start(today - timedelta(days=config.retention_days()))
    existing = await store.fetch(
        "SELECT c.relname FROM pg_class c "
        "JOIN pg_inherits i ON i.inhrelid = c.oid "
        "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = 'events'"
    )
    for (name,) in existing:
        try:
            start = datetime.strptime(name.removeprefix("events_"), "%Y_%m").date()
        except ValueError:
            continue
        if start < cutoff:
            await store.execute(f"DROP TABLE IF EXISTS {name}")
            touched.append(f"-{name}")
    return touched
