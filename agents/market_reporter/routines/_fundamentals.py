"""Deterministic SEC Company Facts normalization."""

from __future__ import annotations

from datetime import date
from typing import Any

from agents.market_reporter.routines._evidence import safe_float

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
