"""Collect verified upcoming U.S. macro events from official calendars."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import Field

from agents.market_reporter.routines._event_adapters import (
    CALENDARS,
    parse_calendar,
)
from agents.market_reporter.routines._evidence import (
    bundle_text,
    finalize_bundle,
)
from agents.market_reporter.routines._http import FetchResult, fetch_text
from agents.market_reporter.routines._models import BaseSourceConfig
from routines.base import RoutineResult

CATEGORY = "Market Reporter"


class Config(BaseSourceConfig):
    """Collect primary-source macro events during a bounded future window."""

    future_days: int = Field(default=42, ge=1, le=42)
    max_items: int = Field(default=40, ge=1, le=40)


async def run(config: Config, context: Any) -> RoutineResult:
    del context
    async with asyncio.timeout(20):
        results: list[FetchResult] = list(
            await asyncio.gather(
                *(fetch_text(provider, url) for provider, url, _ in CALENDARS)
            )
        )
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=config.future_days)
    items = []
    for result, (_, url, label) in zip(results, CALENDARS):
        if result.status != "complete":
            continue
        items.extend(parse_calendar(result, url, label, now, end))
    items.sort(key=lambda item: item["event_time_utc"])
    items = items[: config.max_items]
    bundle = finalize_bundle(
        source_type="events",
        strategy_key=config.strategy_key,
        scope=config.scope,
        items=items,
        provider_results=results,
        coverage={"future_days": config.future_days, "primary_sources_only": True},
    )
    return RoutineResult(
        text=bundle_text(bundle),
        table_data=bundle["items"],
        table_columns=["event_time_utc", "provider_id", "title", "url"],
    )
