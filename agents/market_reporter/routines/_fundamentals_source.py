"""Private collector for bounded U.S. issuer fundamentals from SEC EDGAR."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from pydantic import Field

from agents.market_reporter.routines._evidence import (
    bundle_text,
    evidence_id,
    finalize_bundle,
    safe_float,
)
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

CONCEPTS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "diluted_eps": ["EarningsPerShareDiluted"],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "debt": [
        "LongTermDebtCurrent",
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capital_expenditure": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForAdditionsToPropertyPlantAndEquipment",
    ],
}

EXPECTED_UNITS = {
    "diluted_eps": {"USD/shares"},
    "revenue": {"USD"},
    "operating_income": {"USD"},
    "net_income": {"USD"},
    "cash": {"USD"},
    "debt": {"USD"},
    "operating_cash_flow": {"USD"},
    "capital_expenditure": {"USD"},
}


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


def normalize_company_facts(payload: dict[str, Any]) -> dict[str, Any]:
    """Select recent filed facts with explicit concept/unit metadata."""
    us_gaap = (payload.get("facts") or {}).get("us-gaap") or {}
    output: dict[str, Any] = {}
    for label, concepts in CONCEPTS.items():
        selected = None
        for concept in concepts:
            fact = us_gaap.get(concept) or {}
            for unit, observations in (fact.get("units") or {}).items():
                if unit not in EXPECTED_UNITS[label]:
                    continue
                candidate = _latest_compatible(observations)
                if candidate and (
                    selected is None
                    or str(candidate.get("filed") or "")
                    > str(selected.get("filed") or "")
                ):
                    selected = {
                        "label": label,
                        "concept": concept,
                        "unit": unit,
                        **candidate,
                    }
        output[label] = selected
    return output


def _latest_compatible(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = []
    for observation in observations:
        value = safe_float(observation.get("val"))
        filed = str(observation.get("filed") or "")
        form = str(observation.get("form") or "")
        frame = str(observation.get("frame") or "")
        end = str(observation.get("end") or "")
        if value is None or form not in {"10-K", "10-Q", "20-F", "40-F"}:
            continue
        try:
            date.fromisoformat(filed)
            date.fromisoformat(end)
        except ValueError:
            continue
        valid.append(
            {
                "value": value,
                "start": observation.get("start"),
                "end": end,
                "filed": filed,
                "form": form,
                "fiscal_year": observation.get("fy"),
                "fiscal_period": observation.get("fp"),
                "frame": frame or None,
                "accession": observation.get("accn"),
            }
        )
    if not valid:
        return None
    valid.sort(key=lambda row: (row["filed"], row["end"], row["accession"] or ""))
    selected = dict(valid[-1])
    same_period = [
        row
        for row in valid
        if row["end"] == selected["end"]
        and row.get("fiscal_period") == selected.get("fiscal_period")
    ]
    selected["selection_metadata"] = {
        "rule": "latest_filed_compatible_concept_unit_period",
        "compatible_observation_count": len(valid),
        "same_period_filing_count": len(same_period),
        "restatement_history": [
            {
                "value": row["value"],
                "filed": row["filed"],
                "accession": row["accession"],
            }
            for row in same_period[-5:]
        ],
    }
    comparison = _prior_comparable(valid, selected)
    if comparison:
        selected["prior_comparable"] = {
            "value": comparison["value"],
            "end": comparison["end"],
            "filed": comparison["filed"],
            "fiscal_year": comparison["fiscal_year"],
            "fiscal_period": comparison["fiscal_period"],
            "change_pct": (
                round(
                    (selected["value"] / comparison["value"] - 1) * 100,
                    4,
                )
                if comparison["value"] != 0
                else None
            ),
            "compatibility_rule": (
                "same fiscal period and matching instant/duration shape"
            ),
        }
    return selected


def _prior_comparable(
    valid: list[dict[str, Any]], selected: dict[str, Any]
) -> dict[str, Any] | None:
    selected_duration = _duration_days(selected)
    candidates = []
    for row in valid:
        if row["end"] >= selected["end"]:
            continue
        if row.get("fiscal_period") != selected.get("fiscal_period"):
            continue
        duration = _duration_days(row)
        if (selected_duration is None) != (duration is None):
            continue
        if (
            selected_duration is not None
            and duration is not None
            and abs(selected_duration - duration) > 7
        ):
            continue
        candidates.append(row)
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row["end"], row["filed"]))
    return candidates[-1]


def _duration_days(row: dict[str, Any]) -> int | None:
    start = row.get("start")
    if not start:
        return None
    try:
        return (date.fromisoformat(row["end"]) - date.fromisoformat(str(start))).days
    except ValueError:
        return None
