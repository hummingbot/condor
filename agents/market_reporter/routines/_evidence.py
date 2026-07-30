"""Compact evidence normalization, deduplication, and checksumming."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any

from agents.market_reporter.routines._identity import REGISTRY_VERSION
from agents.market_reporter.routines._providers import MANIFEST_VERSION

ADAPTER_VERSION = "1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_text(value: Any, limit: int = 1200) -> str:
    text = unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def evidence_id(provider_id: str, identity: str, source_time: str = "") -> str:
    raw = f"{provider_id}|{identity}|{source_time}".encode()
    return f"ev_{hashlib.sha256(raw).hexdigest()[:16]}"


def safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def provider_receipt(result: Any) -> dict[str, Any]:
    return {
        "provider_id": result.provider_id,
        "status": result.status,
        "retrieved_at": result.retrieved_at,
        "url": result.url,
        "status_code": result.status_code,
        "byte_count": result.byte_count,
        "error": result.error,
    }


def finalize_bundle(
    *,
    source_type: str,
    strategy_key: str,
    scope: str,
    items: list[dict[str, Any]],
    provider_results: list[Any],
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    coverage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deduplicate and checksum one source bundle."""
    raw_count = len(items)
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        item = dict(item)
        item.setdefault("adapter_version", ADAPTER_VERSION)
        item.setdefault("retrieved_at", utc_now())
        item.setdefault("freshness", _freshness(item))
        item_id = str(item.get("evidence_id") or "")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        deduped.append(item)

    receipts = [provider_receipt(result) for result in provider_results]
    base_warnings = list(warnings or [])
    base_errors = list(errors or [])
    for receipt in receipts:
        if receipt["status"] != "complete":
            base_errors.append(
                f"{receipt['provider_id']}:{receipt.get('error') or 'unavailable'}"
            )

    envelope: dict[str, Any] = {
        "schema_version": "1.0",
        "source_type": source_type,
        "status": "complete",
        "strategy_key": strategy_key,
        "scope": scope,
        "as_of_utc": utc_now(),
        "mutation": False,
        "provider_manifest_version": MANIFEST_VERSION,
        "identity_registry_version": REGISTRY_VERSION,
        "adapter_versions": {source_type: ADAPTER_VERSION},
        "coverage": {
            "providers": receipts,
            **(coverage or {}),
        },
        "raw_item_count": raw_count,
        "retained_item_count": len(deduped),
        "truncation_reasons": [],
        "items": deduped,
        "warnings": sorted(set(base_warnings)),
        "errors": sorted(set(base_errors)),
    }

    if not envelope["items"]:
        envelope["status"] = "unavailable"
    elif envelope["errors"] or envelope["truncation_reasons"]:
        envelope["status"] = "partial"
    envelope["bundle_checksum"] = hashlib.sha256(
        canonical_json(envelope).encode()
    ).hexdigest()
    return envelope


def bundle_text(bundle: dict[str, Any]) -> str:
    return canonical_json(bundle)


def _freshness(item: dict[str, Any]) -> dict[str, Any]:
    value = (
        item.get("source_time")
        or item.get("published_at")
        or item.get("event_time_utc")
    )
    if not value:
        return {"status": "unknown", "age_seconds": None}
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return {"status": "unknown", "age_seconds": None}
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    age = (
        datetime.now(timezone.utc) - observed.astimezone(timezone.utc)
    ).total_seconds()
    return {
        "status": "future" if age < 0 else "observed",
        "age_seconds": max(0, round(age)),
    }
