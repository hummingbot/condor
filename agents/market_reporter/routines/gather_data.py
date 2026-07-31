"""Gather every Strategy-relevant evidence bundle concurrently in one call."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any, Sequence

from pydantic import Field, field_validator

from agents.market_reporter.routines import (
    _event_source,
    _fundamentals_source,
    _news_source,
    _social_source,
    _token_discovery_source,
)
from agents.market_reporter.routines._analysis_context import build_analysis_context
from agents.market_reporter.routines._crypto_source import collect_crypto
from agents.market_reporter.routines._evidence import (
    bundle_text,
    cache_evidence_snapshot,
    canonical_json,
    finalize_bundle,
    utc_now,
)
from agents.market_reporter.routines._models import BaseSourceConfig
from agents.market_reporter.routines._tradfi_source import collect_tradfi
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

STRATEGIES = {
    "crypto": ("crypto_market_intelligence", "crypto"),
    "tradfi": ("tradfi_market_intelligence", "tradfi"),
    "memecoin": ("memecoin_market_intelligence", "memecoin"),
}
STRATEGY_DEFAULTS = {
    "crypto": {
        "news_lookback_hours": 72,
        "market_history_days": 90,
        "event_future_days": 42,
        "max_news_items": 60,
        "max_social_items": 60,
        "max_event_items": 30,
    },
    "tradfi": {
        "news_lookback_hours": 72,
        "market_history_days": 180,
        "event_future_days": 42,
        "max_news_items": 60,
        "max_social_items": 40,
        "max_event_items": 40,
    },
    "memecoin": {
        "news_lookback_hours": 24,
        "market_history_days": 30,
        "event_future_days": 3,
        "max_news_items": 40,
        "max_social_items": 60,
        "max_event_items": 20,
    },
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
        result = await collector(source_config, context)
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


async def _collect_market(config: Config, context: Any) -> RoutineResult:
    del context
    crypto_task = None
    tradfi_task = None
    if config.scope in {"crypto", "both", "memecoin"}:
        crypto_task = collect_crypto(
            scope=config.scope,
            focus_assets=config.focus_assets,
            history_days=config.market_history_days,
        )
    if config.scope in {"tradfi", "both"}:
        tradfi_task = collect_tradfi(
            focus_assets=config.focus_assets,
            history_days=config.market_history_days,
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
            _news_source.run,
            _news_source.Config(
                **common,
                lookback_hours=config.news_lookback_hours,
                max_items=config.max_news_items,
            ),
        ),
        (
            "social",
            _social_source.run,
            _social_source.Config(**common, max_items=config.max_social_items),
        ),
        (
            "market",
            _collect_market,
            config,
        ),
        (
            "events",
            _event_source.run,
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
                _fundamentals_source.run,
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
                _token_discovery_source.run,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Market Reporter gather routine without an agent session. "
            "The command is read-only and prints JSON to stdout."
        )
    )
    parser.add_argument("strategy", choices=sorted(STRATEGIES))
    parser.add_argument("--budget", type=int, default=60, choices=range(15, 91))
    parser.add_argument("--history-days", type=int, choices=range(30, 366))
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--focus", action="append", default=[])
    parser.add_argument("--theme", action="append", default=[])
    parser.add_argument(
        "--full",
        action="store_true",
        help="Print the compact gather payload instead of the diagnostic summary.",
    )
    return parser


def build_config(args: argparse.Namespace) -> Config:
    strategy_key, scope = STRATEGIES[args.strategy]
    defaults = dict(STRATEGY_DEFAULTS[args.strategy])
    if args.history_days is not None:
        defaults["market_history_days"] = args.history_days
    run_suffix = max(1, time.time_ns())
    return Config(
        strategy_key=strategy_key,
        scope=scope,
        run_id=f"market_reporter.{strategy_key}_e{run_suffix}",
        focus_assets=args.focus,
        themes=args.theme,
        report_timezone=args.timezone,
        **defaults,
        source_collection_budget_sec=args.budget,
    )


def diagnostic_summary(payload: dict[str, Any]) -> dict[str, Any]:
    analysis_context = payload.get("analysis_context") or {}
    return {
        "status": payload.get("status"),
        "strategy_key": payload.get("strategy_key"),
        "scope": payload.get("scope"),
        "run_id": payload.get("run_id"),
        "evidence_snapshot_id": payload.get("evidence_snapshot_id"),
        "as_of_utc": payload.get("as_of_utc"),
        "elapsed_seconds": payload.get("elapsed_seconds"),
        "deadline_seconds": payload.get("deadline_seconds"),
        "timed_out_sources": payload.get("timed_out_sources") or [],
        "incomplete_sources": payload.get("incomplete_sources") or [],
        "coverage_summary": payload.get("coverage_summary") or [],
        "debug_trace": payload.get("debug_trace") or {},
        "analysis_context": _diagnostic_analysis_context(analysis_context),
    }


def _diagnostic_analysis_context(value: dict[str, Any]) -> dict[str, Any]:
    features = value.get("strategy_features") or {}
    summary: dict[str, Any] = {
        "strategy_key": value.get("strategy_key"),
        "research_posture": value.get("research_posture"),
        "coverage_assessment": value.get("coverage_assessment") or {},
        "snapshot_metrics": value.get("snapshot_metrics") or [],
        "leaders_laggards": value.get("leaders_laggards") or {},
        "news_clusters": [
            {
                "topic": row.get("topic"),
                "item_count": row.get("item_count"),
                "publisher_count": row.get("publisher_count"),
                "headline_titles": [
                    highlight.get("title")
                    for highlight in (row.get("highlights") or [])[:2]
                ],
            }
            for row in (value.get("news_clusters") or [])
        ],
        "social_attention": (value.get("social_attention") or [])[:5],
        "next_events": (value.get("events") or [])[:6],
        "data_limitations": value.get("data_limitations") or [],
        "product": features.get("product"),
    }
    if features.get("product") == "crypto_market_brief_v3":
        summary["strategy_snapshot"] = {
            "breadth": features.get("breadth") or {},
            "global_market": features.get("global_market") or {},
            "current_ranked_symbols": [
                row.get("symbol")
                for row in (features.get("current_ranked_universe") or [])
                if row.get("eligible_for_liquid_universe") is True
            ][:16],
            "sentiment": features.get("sentiment") or [],
        }
    elif features.get("product") == "tradfi_market_brief_v3":
        summary["strategy_snapshot"] = {
            "benchmarks": features.get("benchmarks") or [],
            "sector_count": len(features.get("sectors") or []),
            "cross_asset_proxies": features.get("cross_asset_proxies") or [],
            "sp500_stock_count": len(features.get("sp500_stocks") or []),
            "macro_metrics": features.get("macro_metrics") or [],
            "fundamental_count": len(features.get("fundamentals") or []),
        }
    elif features.get("product") == "memecoin_meta_chain_brief_v3":
        summary["strategy_snapshot"] = {
            "provider_meta_categories": features.get("provider_meta_categories") or [],
            "exclusive_ranked_sample_metas": features.get(
                "exclusive_ranked_sample_metas"
            )
            or [],
            "provider_meta_chain_samples": (
                features.get("provider_meta_chain_samples") or []
            ),
            "categorized_asset_count": features.get("categorized_asset_count") or 0,
            "categorized_asset_sample": (
                features.get("categorized_asset_sample") or []
            )[:20],
            "chain_overview": features.get("chain_overview") or [],
            "exact_pair_counts": features.get("exact_pair_counts") or {},
            "eligible_token_highlights": (
                features.get("eligible_token_highlights") or []
            )[:10],
        }
    return summary


async def _run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    result = await run(build_config(args), None)
    payload = json.loads(result.text)
    output = payload if args.full else diagnostic_summary(payload)
    return (0 if payload.get("status") == "complete" else 1), output


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    exit_code, output = asyncio.run(_run(args))
    print(canonical_json(output))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
