"""Collect bounded structured U.S. issuer fundamentals from SEC EDGAR."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import Field

from agents.market_reporter.routines._evidence import (
    bundle_text,
    evidence_id,
    finalize_bundle,
)
from agents.market_reporter.routines._fundamentals import normalize_company_facts
from agents.market_reporter.routines._http import FetchResult, fetch_json
from agents.market_reporter.routines._identity import TICKER_TO_CIK
from agents.market_reporter.routines._models import BaseSourceConfig
from routines.base import RoutineResult

CATEGORY = "Market Reporter"


class Config(BaseSourceConfig):
    """Collect structured SEC Company Facts for exact configured issuers."""

    max_issuers: int = Field(default=12, ge=1, le=12)


async def run(config: Config, context: Any) -> RoutineResult:
    del context
    if config.scope not in {"tradfi", "both"}:
        raise ValueError("Fundamentals are available only for TradFi or both")
    focus = [
        value.strip().upper()
        for value in config.focus_assets
        if value.strip().upper() in TICKER_TO_CIK
    ]
    symbols = (focus or list(TICKER_TO_CIK))[: config.max_issuers]
    tasks = [
        fetch_json(
            "sec",
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{TICKER_TO_CIK[symbol]}.json",
            retry=False,
        )
        for symbol in symbols
    ]
    async with asyncio.timeout(25):
        results: list[FetchResult] = list(await asyncio.gather(*tasks))

    items = []
    for symbol, result in zip(symbols, results):
        if result.status != "complete":
            continue
        facts = normalize_company_facts(result.data or {})
        available_count = sum(value is not None for value in facts.values())
        source_time = max(
            (
                str(value.get("filed") or "")
                for value in facts.values()
                if value is not None
            ),
            default=result.retrieved_at,
        )
        cik = TICKER_TO_CIK[symbol]
        items.append(
            {
                "evidence_id": evidence_id("sec", f"{cik}:companyfacts", source_time),
                "provider_id": "sec",
                "source_family": "fundamentals",
                "source_class": "official",
                "symbol": symbol,
                "cik": cik,
                "issuer_name": str((result.data or {}).get("entityName") or "")[:200],
                "source_time": source_time,
                "retrieved_at": result.retrieved_at,
                "available_metric_count": available_count,
                "facts": facts,
                "url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                "future_estimates": "unavailable",
                "earnings_date": "unavailable",
            }
        )

    bundle = finalize_bundle(
        source_type="fundamentals",
        strategy_key=config.strategy_key,
        scope=config.scope,
        items=items,
        provider_results=results,
        coverage={
            "requested_issuer_count": len(symbols),
            "available_issuer_count": len(items),
            "ticker_to_cik_exact": True,
        },
    )
    table = [
        {
            "symbol": item["symbol"],
            "cik": item["cik"],
            "source_time": item["source_time"],
            "available_metrics": item["available_metric_count"],
        }
        for item in bundle["items"]
    ]
    return RoutineResult(
        text=bundle_text(bundle),
        table_data=table,
        table_columns=["symbol", "cik", "source_time", "available_metrics"],
    )
