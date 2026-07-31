"""Private collector for bounded current news and primary-release metadata."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import Field

from agents.market_reporter.routines._evidence import (
    bundle_text,
    finalize_bundle,
)
from agents.market_reporter.routines._models import BaseSourceConfig
from agents.market_reporter.routines._news_adapters import collect_news
from routines.base import RoutineResult

CATEGORY = "Market Reporter"


class Config(BaseSourceConfig):
    """Collect bounded public news and official-release metadata."""

    lookback_hours: int = Field(default=72, ge=1, le=168)
    max_items: int = Field(default=60, ge=1, le=60)


async def run(config: Config, context: Any) -> RoutineResult:
    del context
    async with asyncio.timeout(25):
        items, provider_results, coverage = await collect_news(
            strategy_key=config.strategy_key,
            scope=config.scope,
            themes=config.themes,
            focus_assets=config.focus_assets,
            lookback_hours=config.lookback_hours,
            max_items=config.max_items,
        )
    bundle = finalize_bundle(
        source_type="news",
        strategy_key=config.strategy_key,
        scope=config.scope,
        items=items,
        provider_results=provider_results,
        coverage=coverage,
    )
    return RoutineResult(
        text=bundle_text(bundle, config.run_id),
        table_data=bundle["items"][:20],
        table_columns=["published_at", "provider_id", "title", "url"],
    )
