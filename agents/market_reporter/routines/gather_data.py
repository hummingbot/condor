"""Gather every Strategy-relevant evidence bundle concurrently in one call."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from pydantic import Field, field_validator

from agents.market_reporter.routines import (
    _event_source,
    _fundamentals_source,
    _market_signal_source,
    _news_source,
    _social_source,
    _token_discovery_source,
)
from agents.market_reporter.routines._analysis_context import build_analysis_context
from agents.market_reporter.routines._evidence import (
    bundle_text,
    cache_evidence_snapshot,
    canonical_json,
    finalize_bundle,
    utc_now,
)
from agents.market_reporter.routines._models import BaseSourceConfig
from routines.base import RoutineResult

CATEGORY = "Market Reporter"

_SOURCE_TYPE = {
    "news": "news",
    "social": "social",
    "market": "market",
    "fundamentals": "fundamentals",
    "token_discovery": "token_discovery",
    "events": "events",
}


class Config(BaseSourceConfig):
    """Bound all public collection for one current-run market report."""

    run_id: str = Field(min_length=1, max_length=160)
    news_lookback_hours: int = Field(default=72, ge=1, le=168)
    market_history_days: int = Field(default=90, ge=30, le=365)
    event_future_days: int = Field(default=42, ge=1, le=42)
    max_news_items: int = Field(default=60, ge=1, le=60)
    max_social_items: int = Field(default=60, ge=1, le=60)
    max_event_items: int = Field(default=40, ge=1, le=40)
    max_issuers: int = Field(default=12, ge=1, le=12)
    source_collection_budget_sec: int = Field(default=60, ge=15, le=90)
    chains: list[str] = Field(
        default_factory=lambda: ["solana", "ethereum", "robinhood"],
        min_length=1,
        max_length=3,
    )
    min_pair_age_hours: float = Field(default=6, ge=0, le=720)
    min_liquidity_usd: float = Field(default=50_000, ge=0, le=100_000_000)
    max_discovery_candidates: int = Field(default=100, ge=10, le=100)
    max_detailed_candidates: int = Field(default=40, ge=5, le=40)

    @field_validator("chains")
    @classmethod
    def unique_chains(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Discovery chains must be unique")
        if not set(values).issubset({"solana", "ethereum", "robinhood"}):
            raise ValueError("Unsupported discovery chain")
        return values


async def run(config: Config, context: Any) -> RoutineResult:
    """Return completed bundles plus explicit receipts for deadline misses."""
    started = time.monotonic()
    started_at = utc_now()
    collectors = _collectors(config)
    trace_by_name: dict[str, dict[str, Any]] = {}
    tasks = {
        name: asyncio.create_task(
            _collect_with_trace(
                name,
                collector,
                source_config,
                context,
                trace_by_name,
            )
        )
        for name, collector, source_config in collectors
    }
    done, pending = await asyncio.wait(
        set(tasks.values()),
        timeout=config.source_collection_budget_sec,
    )
    del done
    pending_names = sorted(name for name, task in tasks.items() if task in pending)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    bundles = []
    failed_sources = []
    for name, _, _ in collectors:
        task = tasks[name]
        if name in pending_names:
            bundle = _unavailable_bundle(config, name, "gather_deadline_exceeded")
        else:
            try:
                result = task.result()
                bundle = json.loads(result.text)
            except (Exception, json.JSONDecodeError) as exc:
                bundle = _unavailable_bundle(
                    config,
                    name,
                    f"collector_failed:{type(exc).__name__}",
                )
        if bundle.get("status") != "complete":
            failed_sources.append(name)
        bundles.append(bundle)

    elapsed = round(time.monotonic() - started, 3)
    completed_at = utc_now()
    as_of_utc = utc_now()
    status = (
        "unavailable"
        if all(bundle.get("status") == "unavailable" for bundle in bundles)
        else "partial" if failed_sources else "complete"
    )
    analysis_context = build_analysis_context(
        strategy_key=config.strategy_key,
        scope=config.scope,
        as_of_utc=as_of_utc,
        display_timezone=config.report_timezone,
        bundles=bundles,
    )
    snapshot_id = cache_evidence_snapshot(
        config.run_id,
        bundles,
        {
            "strategy_key": config.strategy_key,
            "scope": config.scope,
            "as_of_utc": as_of_utc,
            "report_timezone": config.report_timezone,
            "focus_assets": config.focus_assets,
            "themes": config.themes,
            "chains": (
                config.chains
                if config.strategy_key == "memecoin_market_intelligence"
                else []
            ),
            "data_limitations": analysis_context.get("data_limitations") or [],
            "analysis_context": analysis_context,
        },
    )
    cached_source_bytes = sum(
        len(canonical_json(bundle).encode("utf-8")) for bundle in bundles
    )
    analysis_context_bytes = len(canonical_json(analysis_context).encode("utf-8"))
    payload = {
        "schema_version": "2.0",
        "status": status,
        "strategy_key": config.strategy_key,
        "scope": config.scope,
        "run_id": config.run_id,
        "as_of_utc": as_of_utc,
        "mutation": False,
        "elapsed_seconds": elapsed,
        "deadline_seconds": config.source_collection_budget_sec,
        "timed_out_sources": pending_names,
        "incomplete_sources": failed_sources,
        "debug_trace": {
            "schema_version": "1.0",
            "started_at": started_at,
            "completed_at": completed_at,
            "elapsed_seconds": elapsed,
            "deadline_seconds": config.source_collection_budget_sec,
            "concurrent_collector_count": len(collectors),
            "cached_source_bundle_bytes": cached_source_bytes,
            "analysis_context_bytes": analysis_context_bytes,
            "collectors": [
                trace_by_name.get(
                    name,
                    {
                        "collector": name,
                        "source_type": _SOURCE_TYPE[name],
                        "outcome": "trace_unavailable",
                    },
                )
                for name, _, _ in collectors
            ],
        },
        "evidence_snapshot_id": snapshot_id,
        "analysis_context": analysis_context,
        "coverage_summary": [
            {
                "source_type": bundle.get("source_type"),
                "status": bundle.get("status"),
                "retained_items": bundle.get("retained_item_count"),
                "errors": bundle.get("errors") or [],
                "warnings": bundle.get("warnings") or [],
            }
            for bundle in bundles
        ],
    }
    emitted_payload_bytes = 0
    for _ in range(3):
        payload["debug_trace"]["emitted_payload_bytes"] = emitted_payload_bytes
        measured = len(canonical_json(payload).encode("utf-8"))
        if measured == emitted_payload_bytes:
            break
        emitted_payload_bytes = measured
    payload["debug_trace"]["emitted_payload_bytes"] = emitted_payload_bytes
    return RoutineResult(
        text=canonical_json(payload),
        table_data=payload["coverage_summary"],
        table_columns=[
            "source_type",
            "status",
            "retained_items",
            "errors",
            "warnings",
        ],
    )


async def _collect_with_trace(
    name: str,
    collector: Any,
    source_config: BaseSourceConfig,
    context: Any,
    trace_by_name: dict[str, dict[str, Any]],
) -> RoutineResult:
    started = time.monotonic()
    trace = {
        "collector": name,
        "source_type": _SOURCE_TYPE[name],
        "started_at": utc_now(),
        "outcome": "running",
    }
    trace_by_name[name] = trace
    try:
        result = await collector.run(source_config, context)
    except asyncio.CancelledError:
        trace.update(
            {
                "finished_at": utc_now(),
                "duration_seconds": round(time.monotonic() - started, 3),
                "outcome": "deadline_cancelled",
            }
        )
        raise
    except Exception as exc:
        trace.update(
            {
                "finished_at": utc_now(),
                "duration_seconds": round(time.monotonic() - started, 3),
                "outcome": "failed",
                "error_type": type(exc).__name__,
            }
        )
        raise

    trace.update(
        {
            "finished_at": utc_now(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "outcome": "completed",
        }
    )
    try:
        bundle = json.loads(result.text)
    except (TypeError, json.JSONDecodeError) as exc:
        trace.update(
            {
                "outcome": "invalid_bundle",
                "error_type": type(exc).__name__,
            }
        )
    else:
        trace.update(
            {
                "bundle_status": bundle.get("status"),
                "retained_item_count": bundle.get("retained_item_count"),
                "provider_count": len(
                    (bundle.get("coverage") or {}).get("providers") or []
                ),
                "error_count": len(bundle.get("errors") or []),
                "warning_count": len(bundle.get("warnings") or []),
            }
        )
    return result


def _collectors(config: Config) -> list[tuple[str, Any, BaseSourceConfig]]:
    common = {
        "strategy_key": config.strategy_key,
        "scope": config.scope,
        "run_id": config.run_id,
        "focus_assets": config.focus_assets,
        "themes": config.themes,
        "report_timezone": config.report_timezone,
    }
    collectors: list[tuple[str, Any, BaseSourceConfig]] = [
        (
            "news",
            _news_source,
            _news_source.Config(
                **common,
                lookback_hours=config.news_lookback_hours,
                max_items=config.max_news_items,
            ),
        ),
        (
            "social",
            _social_source,
            _social_source.Config(**common, max_items=config.max_social_items),
        ),
        (
            "market",
            _market_signal_source,
            _market_signal_source.Config(
                **common,
                history_days=config.market_history_days,
            ),
        ),
        (
            "events",
            _event_source,
            _event_source.Config(
                **common,
                future_days=config.event_future_days,
                max_items=config.max_event_items,
            ),
        ),
    ]
    if config.scope in {"tradfi", "both"}:
        collectors.append(
            (
                "fundamentals",
                _fundamentals_source,
                _fundamentals_source.Config(
                    **common,
                    max_issuers=config.max_issuers,
                ),
            )
        )
    if config.strategy_key == "memecoin_market_intelligence":
        collectors.append(
            (
                "token_discovery",
                _token_discovery_source,
                _token_discovery_source.Config(
                    **common,
                    chains=config.chains,
                    min_pair_age_hours=config.min_pair_age_hours,
                    min_liquidity_usd=config.min_liquidity_usd,
                    max_discovery_candidates=config.max_discovery_candidates,
                    max_detailed_candidates=config.max_detailed_candidates,
                ),
            )
        )
    return collectors


def _unavailable_bundle(
    config: Config,
    collector_name: str,
    error: str,
) -> dict[str, Any]:
    bundle = finalize_bundle(
        source_type=_SOURCE_TYPE[collector_name],
        strategy_key=config.strategy_key,
        scope=config.scope,
        items=[],
        provider_results=[],
        errors=[error],
        coverage={"collector": collector_name, "deadline_partial_result": True},
    )
    bundle_text(bundle, config.run_id)
    return bundle
