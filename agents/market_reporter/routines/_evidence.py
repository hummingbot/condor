"""Compact evidence normalization, deduplication, and checksumming."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from copy import deepcopy
from datetime import datetime, timezone
from html import unescape
from typing import Any

from agents.market_reporter.routines._identity import REGISTRY_VERSION
from agents.market_reporter.routines._providers import MANIFEST_VERSION

ADAPTER_VERSION = "1.0"
_CACHE_TTL_SECONDS = 1_800
_BUNDLE_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_SNAPSHOT_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


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


def bundle_text(bundle: dict[str, Any], run_id: str | None = None) -> str:
    if run_id:
        _expire_bundle_cache()
        checksum = str(bundle.get("bundle_checksum") or "")
        if not checksum:
            raise ValueError("Source bundle checksum is missing")
        _BUNDLE_CACHE[(run_id, checksum)] = (time.monotonic(), deepcopy(bundle))
    return canonical_json(bundle)


def resolve_source_bundles(
    run_id: str,
    checksums: list[str],
) -> list[dict[str, Any]]:
    """Resolve exact current-run public evidence without an LLM JSON round trip."""
    _expire_bundle_cache()
    bundles = []
    for checksum in checksums:
        cached = _BUNDLE_CACHE.get((run_id, checksum))
        if cached is None:
            raise ValueError("Source bundle reference is unavailable for this run")
        bundles.append(deepcopy(cached[1]))
    return bundles


def cache_evidence_snapshot(
    run_id: str,
    bundles: list[dict[str, Any]],
    report_seed: dict[str, Any],
) -> str:
    """Cache one immutable current-run evidence set behind an opaque handle."""
    _expire_bundle_cache()
    if not run_id or not bundles:
        raise ValueError("Evidence snapshot requires a run and source bundles")
    checksums = []
    for bundle in bundles:
        checksum = str(bundle.get("bundle_checksum") or "")
        if not checksum:
            raise ValueError("Source bundle checksum is missing")
        checksums.append(checksum)
    token_material = canonical_json(
        {
            "run_id": run_id,
            "checksums": checksums,
            "nonce": secrets.token_hex(24),
        }
    )
    snapshot_id = f"es_{hashlib.sha256(token_material.encode()).hexdigest()[:40]}"
    for key in [key for key in _SNAPSHOT_CACHE if key[0] == run_id]:
        _SNAPSHOT_CACHE.pop(key, None)
    _SNAPSHOT_CACHE[(run_id, snapshot_id)] = (
        time.monotonic(),
        {
            "schema_version": "1.0",
            "source_bundles": deepcopy(bundles),
            "report_seed": deepcopy(report_seed),
        },
    )
    for key in [key for key in _BUNDLE_CACHE if key[0] == run_id]:
        _BUNDLE_CACHE.pop(key, None)
    return snapshot_id


def resolve_evidence_snapshot(run_id: str, snapshot_id: str) -> dict[str, Any]:
    """Resolve one exact current-run snapshot without an LLM evidence round trip."""
    _expire_bundle_cache()
    cached = _SNAPSHOT_CACHE.get((run_id, snapshot_id))
    if cached is None:
        raise ValueError("Evidence snapshot is unavailable for this run")
    return deepcopy(cached[1])


def clear_source_bundles(run_id: str) -> None:
    for key in [key for key in _BUNDLE_CACHE if key[0] == run_id]:
        _BUNDLE_CACHE.pop(key, None)
    for key in [key for key in _SNAPSHOT_CACHE if key[0] == run_id]:
        _SNAPSHOT_CACHE.pop(key, None)


def _expire_bundle_cache() -> None:
    cutoff = time.monotonic() - _CACHE_TTL_SECONDS
    for key, (created_at, _) in list(_BUNDLE_CACHE.items()):
        if created_at < cutoff:
            _BUNDLE_CACHE.pop(key, None)
    for key, (created_at, _) in list(_SNAPSHOT_CACHE.items()):
        if created_at < cutoff:
            _SNAPSHOT_CACHE.pop(key, None)


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
