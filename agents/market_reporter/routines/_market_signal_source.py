"""Private collector for deterministic cross-market metrics."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import Field

from agents.market_reporter.routines._crypto_source import collect_crypto
from agents.market_reporter.routines._evidence import bundle_text, finalize_bundle
from agents.market_reporter.routines._models import BaseSourceConfig
from agents.market_reporter.routines._tradfi_source import collect_tradfi
from routines.base import RoutineResult

CATEGORY = "Market Reporter"


class Config(BaseSourceConfig):
    """Calculate bounded market, breadth, positioning, and macro signals."""

    history_days: int = Field(default=90, ge=30, le=365)


async def run(config: Config, context: Any) -> RoutineResult:
    del context
    crypto_task = None
    tradfi_task = None
    if config.scope in {"crypto", "both", "memecoin"}:
        crypto_task = collect_crypto(
            scope=config.scope,
            focus_assets=config.focus_assets,
            history_days=config.history_days,
        )
    if config.scope in {"tradfi", "both"}:
        tradfi_task = collect_tradfi(
            focus_assets=config.focus_assets,
            history_days=config.history_days,
        )

    async with asyncio.timeout(40):
        collected = await asyncio.gather(
            *(task for task in (crypto_task, tradfi_task) if task is not None)
        )

    items = []
    provider_results = []
    coverage: dict[str, Any] = {}
    warnings = []
    cursor = 0
    if crypto_task is not None:
        crypto_items, crypto_results, crypto_coverage = collected[cursor]
        cursor += 1
        items.extend(crypto_items)
        provider_results.extend(crypto_results)
        coverage["crypto_universe"] = crypto_coverage
        rejected = [
            str(row.get("symbol"))
            for row in crypto_coverage.get("spot_selection_rejections") or []
            if row.get("reason")
            in {"primary_http_failure", "stale_or_unparseable_history"}
        ]
        if rejected:
            warnings.append("technical_universe_substitutions:" + ",".join(rejected))
    if tradfi_task is not None:
        tradfi_items, tradfi_results, tradfi_coverage, tradfi_warnings = collected[
            cursor
        ]
        items.extend(tradfi_items)
        provider_results.extend(tradfi_results)
        coverage["tradfi_universe"] = tradfi_coverage
        warnings.extend(tradfi_warnings)

    bundle = finalize_bundle(
        source_type="market",
        strategy_key=config.strategy_key,
        scope=config.scope,
        items=items,
        provider_results=provider_results,
        warnings=warnings,
        coverage=coverage,
    )
    table = [
        {
            "asset": item.get("symbol") or item.get("metric"),
            "provider": item.get("provider_id"),
            "last_observation": item.get("source_time"),
            "value": item.get("value") or (item.get("metrics") or {}).get("last_price"),
        }
        for item in bundle["items"][:30]
    ]
    return RoutineResult(
        text=bundle_text(bundle, config.run_id),
        table_data=table,
        table_columns=["asset", "provider", "last_observation", "value"],
    )
