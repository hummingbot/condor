"""Read-only command-line entry point for manually verifying data gathering."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any, Sequence

from agents.market_reporter.routines import gather_data
from agents.market_reporter.routines._evidence import canonical_json

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


def build_config(args: argparse.Namespace) -> gather_data.Config:
    strategy_key, scope = STRATEGIES[args.strategy]
    defaults = dict(STRATEGY_DEFAULTS[args.strategy])
    if args.history_days is not None:
        defaults["market_history_days"] = args.history_days
    run_suffix = max(1, time.time_ns())
    return gather_data.Config(
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
    result = await gather_data.run(build_config(args), None)
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
