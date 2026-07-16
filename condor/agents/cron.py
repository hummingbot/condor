"""Minimal 5-field cron evaluation (§5.4 scheduler execution).

Grammar is exactly what ``condor.agents.spec.validate_cron`` accepts:
``minute hour dom month dow`` with ``*``, numbers, ranges ``a-b``, steps
``/n``, and comma lists. Standard cron day semantics: when BOTH dom and dow
are restricted, a day matches if EITHER matches; dow 0 and 7 are Sunday.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_RANGES = {
    "minute": (0, 59),
    "hour": (0, 23),
    "dom": (1, 31),
    "month": (1, 12),
    "dow": (0, 7),
}


def _expand(field: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        out.update(range(start, end + 1, step))
    return out


def parse_cron(expr: str) -> dict[str, set[int]]:
    minute, hour, dom, month, dow = expr.split()
    fields = {
        "minute": _expand(minute, *_RANGES["minute"]),
        "hour": _expand(hour, *_RANGES["hour"]),
        "dom": _expand(dom, *_RANGES["dom"]),
        "month": _expand(month, *_RANGES["month"]),
        "dow": _expand(dow, *_RANGES["dow"]),
    }
    # cron: 0 and 7 are both Sunday
    if 7 in fields["dow"]:
        fields["dow"].add(0)
    # remember whether day fields were restricted (the dom/dow OR rule)
    fields["_dom_star"] = {1} if dom == "*" else set()
    fields["_dow_star"] = {1} if dow.split("/")[0] == "*" else set()
    return fields


def _day_matches(d: datetime, f: dict[str, set[int]]) -> bool:
    dom_ok = d.day in f["dom"]
    dow_ok = (d.weekday() + 1) % 7 in f["dow"]  # python Mon=0 → cron Sun=0
    dom_star = bool(f["_dom_star"])
    dow_star = bool(f["_dow_star"])
    if dom_star and dow_star:
        return True
    if dom_star:
        return dow_ok
    if dow_star:
        return dom_ok
    return dom_ok or dow_ok  # both restricted → OR (standard cron)


def next_fire(expr: str, tz: str, after: datetime) -> datetime:
    """First fire time strictly AFTER ``after`` (tz-aware, in ``tz``).

    Searches up to ~4 years to cover the sparsest valid schedules; raises
    ValueError beyond that (unsatisfiable, e.g. Feb 30).
    """
    f = parse_cron(expr)
    zone = ZoneInfo(tz)
    local = after.astimezone(zone)
    # Start at the next whole minute.
    cursor = (local + timedelta(minutes=1)).replace(second=0, microsecond=0)

    minutes = sorted(f["minute"])
    hours = sorted(f["hour"])

    day = cursor.date()
    for _ in range(366 * 4):
        d = datetime(day.year, day.month, day.day, tzinfo=zone)
        if d.month in f["month"] and _day_matches(d, f):
            for h in hours:
                for m in minutes:
                    candidate = d.replace(hour=h, minute=m)
                    if candidate >= cursor:
                        return candidate
        day = day + timedelta(days=1)
        cursor = datetime(
            day.year, day.month, day.day, tzinfo=zone
        )  # from the next day, any allowed time works
    raise ValueError(f"unsatisfiable cron expression: {expr!r}")


def fires_between(
    expr: str, tz: str, start: datetime, end: datetime, limit: int = 10
) -> list[datetime]:
    """Fire times in (start, end], oldest first, capped at ``limit``."""
    out: list[datetime] = []
    cursor = start
    while len(out) < limit:
        nxt = next_fire(expr, tz, cursor)
        if nxt > end:
            break
        out.append(nxt)
        cursor = nxt
    return out
