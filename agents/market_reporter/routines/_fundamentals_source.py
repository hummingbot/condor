"""Private collector for bounded U.S. issuer fundamentals from SEC EDGAR."""

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

FALLBACK_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "OperatingIncomeLoss",
    "NetIncomeLoss",
    "EarningsPerShareDiluted",
    "NetCashProvidedByUsedInOperatingActivities",
)


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
        company_results: list[FetchResult] = list(await asyncio.gather(*tasks))
        fallback_symbols = [
            symbol
            for symbol, result in zip(symbols, company_results)
            if result.status != "complete"
        ]
        fallback_groups = list(
            await asyncio.gather(
                *[
                    _fetch_fallback_concepts(TICKER_TO_CIK[symbol])
                    for symbol in fallback_symbols
                ]
            )
        )

    items = []
    provider_results = []
    warnings = []
    fallback_by_symbol = dict(zip(fallback_symbols, fallback_groups))
    fallback_recovered = []
    for symbol, result in zip(symbols, company_results):
        payload = result.data or {}
        retrieved_at = result.retrieved_at
        source_url = result.url
        if result.status == "complete":
            provider_results.append(result)
        else:
            fallback_payload, fallback_results = fallback_by_symbol[symbol]
            complete_fallbacks = [
                fallback
                for fallback in fallback_results
                if fallback.status == "complete"
            ]
            provider_results.extend(complete_fallbacks)
            missing_concepts = [
                concept
                for concept, fallback in zip(FALLBACK_CONCEPTS, fallback_results)
                if fallback.status != "complete"
            ]
            if missing_concepts:
                warnings.append(
                    f"sec_concept_unavailable:{symbol}:" + ",".join(missing_concepts)
                )
            if complete_fallbacks:
                payload = fallback_payload
                retrieved_at = max(
                    fallback.retrieved_at for fallback in complete_fallbacks
                )
                source_url = complete_fallbacks[0].url
                fallback_recovered.append(symbol)
                warnings.append(f"sec_companyconcept_fallback:{symbol}")
            else:
                provider_results.append(result)
                continue
        item = _fundamental_item(
            symbol,
            payload,
            retrieved_at=retrieved_at,
            source_url=source_url,
        )
        if item:
            items.append(item)

    bundle = finalize_bundle(
        source_type="fundamentals",
        strategy_key=config.strategy_key,
        scope=config.scope,
        items=items,
        provider_results=provider_results,
        warnings=warnings,
        coverage={
            "requested_issuer_count": len(symbols),
            "available_issuer_count": len(items),
            "ticker_to_cik_exact": True,
            "companyconcept_fallback_symbols": sorted(fallback_recovered),
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
        text=bundle_text(bundle, config.run_id),
        table_data=table,
        table_columns=["symbol", "cik", "source_time", "available_metrics"],
    )


async def _fetch_fallback_concepts(
    cik: str,
) -> tuple[dict[str, Any], list[FetchResult]]:
    results = list(
        await asyncio.gather(
            *[
                fetch_json(
                    "sec",
                    f"https://data.sec.gov/api/xbrl/companyconcept/"
                    f"CIK{cik}/us-gaap/{concept}.json",
                    retry=False,
                )
                for concept in FALLBACK_CONCEPTS
            ]
        )
    )
    facts = {}
    entity_name = ""
    for concept, result in zip(FALLBACK_CONCEPTS, results):
        if result.status != "complete":
            continue
        data = result.data or {}
        entity_name = entity_name or str(data.get("entityName") or "")
        facts[concept] = {
            "units": data.get("units") or {},
        }
    return (
        {
            "entityName": entity_name,
            "facts": {"us-gaap": facts},
        },
        results,
    )


def _fundamental_item(
    symbol: str,
    payload: dict[str, Any],
    *,
    retrieved_at: str,
    source_url: str,
) -> dict[str, Any] | None:
    facts = normalize_company_facts(payload)
    available_count = sum(value is not None for value in facts.values())
    if not available_count:
        return None
    source_time = max(
        (
            str(value.get("filed") or "")
            for value in facts.values()
            if value is not None
        ),
        default=retrieved_at,
    )
    cik = TICKER_TO_CIK[symbol]
    return {
        "evidence_id": evidence_id("sec", f"{cik}:companyfacts", source_time),
        "provider_id": "sec",
        "source_family": "fundamentals",
        "source_class": "official",
        "symbol": symbol,
        "cik": cik,
        "issuer_name": str(payload.get("entityName") or "")[:200],
        "source_time": source_time,
        "retrieved_at": retrieved_at,
        "available_metric_count": available_count,
        "facts": facts,
        "url": source_url,
        "future_estimates": "unavailable",
        "earnings_date": "unavailable",
    }
