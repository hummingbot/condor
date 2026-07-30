"""Private parsers for official macro-event calendar formats."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from agents.market_reporter.routines._evidence import (
    clean_text,
    evidence_id,
)
from agents.market_reporter.routines._http import FetchResult

CALENDARS = [
    (
        "federal_reserve",
        "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        "FOMC",
    ),
    (
        "bls",
        "https://www.bls.gov/schedule/news_release/bls.ics",
        "BLS",
    ),
    (
        "bea",
        "https://www.bea.gov/news/schedule",
        "BEA",
    ),
]


def parse_calendar(
    result: FetchResult,
    source_url: str,
    label: str,
    now: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    if "BEGIN:VEVENT" in (result.text or ""):
        return _parse_ics(result, source_url, label, now, end)
    soup = BeautifulSoup(result.text or "", "html.parser")
    candidates = []
    for row in soup.select("tr, li"):
        text = clean_text(row.get_text(" ", strip=True), 600)
        if len(text) < 8:
            continue
        parsed = _find_date(text, now)
        if parsed is not None and now <= parsed <= end:
            candidates.append((parsed, text))
    output = []
    seen = set()
    for parsed, text in candidates:
        identity = f"{label}:{parsed.date().isoformat()}:{text[:160]}"
        if identity in seen:
            continue
        seen.add(identity)
        event_time = parsed.isoformat().replace("+00:00", "Z")
        output.append(
            {
                "evidence_id": evidence_id(result.provider_id, identity, event_time),
                "provider_id": result.provider_id,
                "source_family": "events",
                "source_class": "official",
                "title": text[:300],
                "event_time_utc": event_time,
                "time_precision": "date_or_published_time",
                "retrieved_at": result.retrieved_at,
                "url": source_url,
                "verified_scheduled": True,
            }
        )
    return output


def _find_date(text: str, now: datetime) -> datetime | None:
    month = re.search(
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
        r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
        r"Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}(?:,\s*\d{4})?",
        text,
        flags=re.IGNORECASE,
    )
    if not month:
        return None
    clock = re.search(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)\b", text, re.IGNORECASE)
    candidate = month.group(0)
    if clock:
        candidate += f" {clock.group(0)}"
    try:
        parsed = date_parser.parse(
            candidate,
            default=now.astimezone(ZoneInfo("America/New_York")).replace(
                hour=8,
                minute=30,
                second=0,
                microsecond=0,
            ),
        )
    except (ValueError, TypeError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("America/New_York"))
    return parsed.astimezone(timezone.utc)


def _parse_ics(
    result: FetchResult,
    source_url: str,
    label: str,
    now: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    unfolded = re.sub(r"\r?\n[ \t]", "", result.text or "")
    output = []
    for block in unfolded.split("BEGIN:VEVENT")[1:]:
        block = block.split("END:VEVENT", 1)[0]
        start_match = re.search(
            r"^DTSTART(?:;TZID=([^:]+))?:(.+)$",
            block,
            re.MULTILINE,
        )
        summary_match = re.search(r"^SUMMARY:(.+)$", block, re.MULTILINE)
        uid_match = re.search(r"^UID:(.+)$", block, re.MULTILINE)
        if not start_match or not summary_match:
            continue
        parsed = _ics_datetime(start_match.group(2).strip(), start_match.group(1))
        if parsed is None or not (now <= parsed <= end):
            continue
        title = clean_text(summary_match.group(1).replace("\\,", ","), 300)
        uid = clean_text(uid_match.group(1), 160) if uid_match else title
        event_time = parsed.isoformat().replace("+00:00", "Z")
        output.append(
            {
                "evidence_id": evidence_id(result.provider_id, uid, event_time),
                "provider_id": result.provider_id,
                "source_family": "events",
                "source_class": "official",
                "title": f"{label}: {title}",
                "event_time_utc": event_time,
                "time_precision": "published_time",
                "retrieved_at": result.retrieved_at,
                "url": source_url,
                "verified_scheduled": True,
            }
        )
    return output


def _ics_datetime(value: str, timezone_name: str | None) -> datetime | None:
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if value.endswith("Z"):
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            try:
                zone = ZoneInfo(timezone_name or "America/New_York")
            except Exception:
                zone = ZoneInfo("America/New_York")
            parsed = parsed.replace(tzinfo=zone)
        return parsed.astimezone(timezone.utc)
    return None
