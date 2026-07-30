"""Build the professional interactive Condor report from a validated package."""

from __future__ import annotations

import json
from typing import Any

from agents.market_reporter.routines._models import ReportPackage
from agents.market_reporter.routines._providers import public_manifest
from condor.reports import ReportBuilder

STANCE_LABELS = {
    "bullish": "Bullish",
    "cautiously_bullish": "Cautiously bullish",
    "neutral": "Neutral",
    "cautiously_bearish": "Cautiously bearish",
    "bearish": "Bearish",
    "mixed": "Mixed",
}


async def render_report(package: ReportPackage) -> str:
    builder = ReportBuilder(package.metadata.title)
    builder.source("routine", "build_market_report").tags(
        ["market-intelligence", package.metadata.strategy_key, package.metadata.scope]
    )
    builder.manual_order()

    summary = _summary_rows(package)
    series = _series_rows(package)
    candidates = _candidate_rows(package)
    evidence = _evidence_rows(package)
    events = _event_rows(package)
    token_rows = _token_rows(package)
    theme_rows = _theme_rows(package)
    provider_rows = _provider_rows(package)
    coverage_rows = _coverage_rows(package)
    strategy_rows = _strategy_rows(package)
    chain_rows = _chain_rows(package)

    _executive(builder, package)
    if summary:
        builder.dataset("market_summary", summary)
    if series:
        builder.dataset("market_series", series)
    if candidates:
        builder.dataset("research_candidates", candidates)
    if evidence:
        builder.dataset("evidence", evidence)
    if events:
        builder.dataset("events", events)
    if token_rows:
        builder.dataset("token_discovery", token_rows)
    if theme_rows:
        builder.dataset("themes", theme_rows)
    if provider_rows:
        builder.dataset("providers", provider_rows)
    if coverage_rows:
        builder.dataset("source_coverage", coverage_rows)
    if strategy_rows:
        builder.dataset("strategy_lens", strategy_rows)
    if chain_rows:
        builder.dataset("chain_coverage", chain_rows)

    _market_structure(builder, summary, series, strategy_rows)
    _narratives(builder, theme_rows, evidence)
    _candidate_section(builder, candidates, token_rows, chain_rows)
    _events_and_risks(builder, package, events)
    _methodology(builder, package, evidence, provider_rows, coverage_rows)
    return await builder.save()


def _executive(builder: ReportBuilder, package: ReportPackage) -> None:
    view = package.market_views[0] if package.market_views else None
    builder.section(
        "01 / EXECUTIVE DASHBOARD",
        f"As of {package.metadata.as_of_utc} · display timezone "
        f"{package.metadata.report_timezone}",
    )
    builder.kpi(
        "Market stance",
        STANCE_LABELS.get(view.stance, "Unavailable") if view else "Unavailable",
        trend=_trend(view.stance) if view else "neutral",
    )
    builder.kpi(
        "Confidence",
        view.confidence.title() if view else "Unavailable",
    )
    builder.kpi(
        "Coverage",
        package.coverage_assessment.grade.title(),
        "truncated" if package.coverage_assessment.truncated else None,
    )
    builder.kpi("Research candidates", str(len(package.research_candidates)))
    builder.kpi("Top risk", _top_risk(package))
    if package.metadata.strategy_key == "memecoin_market_intelligence":
        token_rows = _token_rows(package)
        eligible = sum(row["eligibility"] == "eligible" for row in token_rows)
        excluded = sum(row["eligibility"] == "excluded" for row in token_rows)
        paid = sum(bool(row["paid_visibility"]) for row in token_rows)
        builder.kpi("Eligible pairs", str(eligible))
        builder.kpi("Excluded pairs", str(excluded))
        builder.kpi(
            "Paid visibility",
            f"{(paid / len(token_rows) * 100):.1f}%" if token_rows else "Unavailable",
        )
    takeaways = "\n".join(f"- {value}" for value in package.executive_takeaways)
    builder.markdown(f"### Key takeaways\n\n{takeaways}")


def _market_structure(
    builder: ReportBuilder,
    summary: list[dict[str, Any]],
    series: list[dict[str, Any]],
    strategy_rows: list[dict[str, Any]],
) -> None:
    builder.section(
        "02 / MARKET REGIME & STRUCTURE",
        "Observed market metrics; direction and risk are written as text as well as color.",
    )
    if series:
        builder.select_filter(
            "series-symbol-filter",
            "market_series",
            "symbol",
            label="Benchmark",
        )
        builder.range_filter(
            "series-date-filter",
            "market_series",
            "timestamp",
            label="Observation period",
            value_type="date",
        )
        builder.chart(
            "line",
            "Benchmark close",
            "market_series",
            "timestamp",
            "close",
            color="symbol",
            x_label="Date (source timezone shown in audit)",
            y_label="Close price",
            component_id="benchmark-close-chart",
        )
    else:
        builder.markdown("- Benchmark time series unavailable for this evidence set.")
    if summary:
        builder.chart(
            "scatter",
            "Momentum versus realized volatility",
            "market_summary",
            "return_7d_pct",
            "realized_volatility_20d_pct",
            color="asset_class",
            text="symbol",
            x_label="7-day return (%)",
            y_label="20-day realized volatility (%)",
            component_id="momentum-volatility-chart",
        )
        builder.data_table(
            "market_summary",
            title="Market structure observations",
            columns=[
                "symbol",
                "asset_class",
                "last_price",
                "return_7d_pct",
                "return_30d_pct",
                "rsi14",
                "realized_volatility_20d_pct",
                "source_time",
            ],
            component_id="market-summary-table",
        )
    else:
        builder.markdown("- Market-summary observations are unavailable.")
    if strategy_rows:
        builder.data_table(
            "strategy_lens",
            title="Strategy-specific regime components",
            columns=["block", "details"],
            component_id="strategy-lens-table",
        )


def _narratives(
    builder: ReportBuilder,
    themes: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> None:
    builder.section(
        "03 / NEWS, NARRATIVES & ATTENTION",
        "LLM interpretations remain separate from linked source observations.",
    )
    if themes:
        builder.chart(
            "treemap",
            "Narrative map",
            "themes",
            "title",
            "evidence_count",
            color="direction_score",
            value_label="Evidence count",
            color_label="Direction score",
            component_id="narrative-treemap",
        )
        builder.data_table(
            "themes",
            title="Theme interpretations",
            component_id="theme-table",
        )
    else:
        builder.markdown("- No narrative met the retained evidence threshold.")
    if evidence:
        builder.select_filter(
            "evidence-family-filter",
            "evidence",
            "source_family",
            label="Source family",
        )
        builder.data_table(
            "evidence",
            title="Linked evidence",
            columns=[
                "evidence_id",
                "source_family",
                "provider_id",
                "source_time",
                "title",
                "url",
            ],
            component_id="evidence-table",
        )
    else:
        builder.markdown("- No linked evidence was retained.")


def _candidate_section(
    builder: ReportBuilder,
    candidates: list[dict[str, Any]],
    tokens: list[dict[str, Any]],
    chains: list[dict[str, Any]],
) -> None:
    builder.section(
        "04 / RANKED RESEARCH CANDIDATES",
        "Research priority only; not personalized buy or sell instructions.",
    )
    if candidates:
        builder.select_filter(
            "candidate-state-filter",
            "research_candidates",
            "candidate_state",
            label="Research state",
        )
        builder.data_table(
            "research_candidates",
            title="Candidate theses, invalidations, and risks",
            component_id="candidate-table",
        )
    else:
        builder.markdown("- No candidate passed the active Strategy gates.")
    if chains:
        builder.chart(
            "bar",
            "Eligible pairs by chain",
            "chain_coverage",
            "chain",
            "eligible",
            color="maturity",
            x_label="Chain",
            y_label="Eligible pairs",
            component_id="chain-coverage-chart",
        )
        builder.data_table(
            "chain_coverage",
            title="Chain cohorts and discovery coverage",
            component_id="chain-coverage-table",
        )
    if tokens:
        builder.select_filter(
            "token-chain-filter",
            "token_discovery",
            "chain",
            label="Chain cohort",
        )
        builder.chart(
            "scatter",
            "Memecoin liquidity versus turnover",
            "token_discovery",
            "liquidity_usd",
            "volume_to_liquidity",
            color="eligibility",
            size="volume_24h_usd",
            text="symbol",
            x_label="Liquidity (USD)",
            y_label="24h volume / liquidity",
            x_scale="log",
            component_id="token-liquidity-turnover-chart",
        )
        builder.chart(
            "scatter",
            "Memecoin pair age versus turnover",
            "token_discovery",
            "pair_age_hours",
            "volume_to_liquidity",
            color="eligibility",
            size="liquidity_usd",
            text="symbol",
            x_label="Pair age (hours)",
            y_label="24h volume / liquidity",
            component_id="token-age-turnover-chart",
        )
        builder.data_table(
            "token_discovery",
            title="Exact token and pair evidence",
            component_id="token-discovery-table",
        )


def _events_and_risks(
    builder: ReportBuilder,
    package: ReportPackage,
    events: list[dict[str, Any]],
) -> None:
    builder.section(
        "05 / CATALYSTS, OPPORTUNITIES & RISKS",
        "Verified events remain separate from inferred watch conditions.",
    )
    if events:
        builder.range_filter(
            "event-date-filter",
            "events",
            "event_time_utc",
            label="Event window",
            value_type="datetime",
        )
        builder.data_table(
            "events",
            title="Verified events and watch conditions",
            component_id="event-table",
        )
    else:
        builder.markdown("- No verified event or evidence-linked watch condition.")
    builder.markdown(
        "### Opportunities\n\n"
        + _json_bullets(package.opportunities)
        + "\n\n### Risks\n\n"
        + _json_bullets(package.risks)
        + "\n\n### Scenarios\n\n"
        + _json_bullets(package.scenarios)
    )


def _methodology(
    builder: ReportBuilder,
    package: ReportPackage,
    evidence: list[dict[str, Any]],
    providers: list[dict[str, Any]],
    coverage: list[dict[str, Any]],
) -> None:
    builder.section(
        "06 / METHODOLOGY & AUDIT",
        "Coverage, source versions, truncation, attribution, and limitations.",
    )
    manifest = package.evidence_manifest
    builder.kpi(
        "Provider manifest",
        str(manifest.get("provider_manifest_version") or "Unavailable"),
    )
    builder.kpi(
        "Identity registry",
        str(manifest.get("identity_registry_version") or "Unavailable"),
    )
    builder.kpi("Retained evidence", str(len(evidence)))
    if coverage:
        builder.data_table(
            "source_coverage",
            title="Source coverage and truncation",
            component_id="source-coverage-table",
        )
    if providers:
        builder.data_table(
            "providers",
            title="Provider attribution and operating contract",
            component_id="provider-attribution-table",
        )
    limitations = "\n".join(f"- {value}" for value in package.data_limitations)
    builder.markdown(
        "### Coverage reason codes\n\n"
        + "\n".join(f"- {value}" for value in package.coverage_assessment.reason_codes)
        + "\n\n### Data limitations\n\n"
        + (limitations or "- None declared")
        + "\n\n### Disclaimer\n\n"
        + package.metadata.disclaimer
        + "\n\n### Artifact retention\n\n"
        + "The report ID is immutable while retained. Retention duration is "
        + "controlled by the current Condor report store and is not promised "
        + "here. This artifact does not depend on prior report files."
    )


def _summary_rows(package: ReportPackage) -> list[dict[str, Any]]:
    rows = []
    for bundle in package.source_bundles:
        for item in bundle.get("items") or []:
            metrics = item.get("metrics")
            if not isinstance(metrics, dict) or not item.get("symbol"):
                continue
            rows.append(
                {
                    "symbol": item["symbol"],
                    "asset_class": item.get("asset_class", ""),
                    "source_time": item.get("source_time", ""),
                    **metrics,
                }
            )
    return rows


def _series_rows(package: ReportPackage) -> list[dict[str, Any]]:
    rows = []
    for bundle in package.source_bundles:
        for item in bundle.get("items") or []:
            rows.extend(item.get("series") or [])
    return rows


def _candidate_rows(package: ReportPackage) -> list[dict[str, Any]]:
    rows = []
    for candidate in package.research_candidates:
        identity = candidate.asset_identity
        rows.append(
            {
                "rank": candidate.rank,
                "asset": identity.get("symbol")
                or identity.get("token_address")
                or identity.get("ticker"),
                "chain": identity.get("chain_id"),
                "contract": identity.get("token_address"),
                "pair": identity.get("pair_address"),
                "candidate_state": candidate.candidate_state,
                "stance": STANCE_LABELS[candidate.stance],
                "confidence": candidate.confidence,
                "horizon": candidate.horizon,
                "why_now": candidate.why_now,
                "invalidation": "; ".join(candidate.invalidation_conditions),
                "risks": "; ".join(candidate.key_risks),
                "evidence_ids": ", ".join(candidate.supporting_evidence_ids),
            }
        )
    return rows


def _evidence_rows(package: ReportPackage) -> list[dict[str, Any]]:
    rows = []
    for bundle in package.source_bundles:
        for item in bundle.get("items") or []:
            rows.append(
                {
                    "evidence_id": item.get("evidence_id"),
                    "source_family": item.get("source_family")
                    or bundle.get("source_type"),
                    "provider_id": item.get("provider_id"),
                    "source_time": item.get("source_time") or item.get("published_at"),
                    "title": item.get("title")
                    or item.get("metric")
                    or item.get("symbol"),
                    "url": item.get("url"),
                }
            )
    return rows


def _event_rows(package: ReportPackage) -> list[dict[str, Any]]:
    return [
        {
            "event_time_utc": event.get("event_time_utc") or event.get("time") or "",
            "title": event.get("title") or event.get("condition") or "",
            "kind": (
                "verified_event"
                if event.get("verified_scheduled")
                else "watch_condition"
            ),
            "url": event.get("url"),
        }
        for event in package.events_and_watch_conditions
    ]


def _token_rows(package: ReportPackage) -> list[dict[str, Any]]:
    rows = []
    for bundle in package.source_bundles:
        if bundle.get("source_type") != "token_discovery":
            continue
        for item in bundle.get("items") or []:
            market = item.get("market") or {}
            rows.append(
                {
                    "chain": item.get("chain_id"),
                    "symbol": market.get("symbol"),
                    "token_address": item.get("token_address"),
                    "pair_address": item.get("pair_address"),
                    "eligibility": item.get("eligibility"),
                    "liquidity_usd": market.get("liquidity_usd"),
                    "volume_24h_usd": market.get("volume_24h_usd"),
                    "volume_to_liquidity": market.get("volume_to_liquidity"),
                    "pair_age_hours": market.get("pair_age_hours"),
                    "paid_visibility": item.get("paid_visibility"),
                    "reason_codes": "; ".join(item.get("reason_codes") or []),
                    "url": item.get("url"),
                    "explorer_url": (item.get("robinhood_identity") or {}).get(
                        "explorer_url"
                    ),
                }
            )
    return rows


def _theme_rows(package: ReportPackage) -> list[dict[str, Any]]:
    rows = []
    for theme in package.themes:
        rows.append(
            {
                "title": theme.get("title") or theme.get("name") or "Theme",
                "summary": theme.get("interpretation") or theme.get("summary") or "",
                "evidence_count": max(
                    1, len(theme.get("supporting_evidence_ids") or [])
                ),
                "direction_score": theme.get("direction_score", 0),
            }
        )
    return rows


def _provider_rows(package: ReportPackage) -> list[dict[str, Any]]:
    provider_ids = sorted(
        {
            str(item.get("provider_id"))
            for bundle in package.source_bundles
            for item in bundle.get("items") or []
            if item.get("provider_id")
        }
    )
    manifest = public_manifest(provider_ids)
    return list(manifest["providers"].values())


def _coverage_rows(package: ReportPackage) -> list[dict[str, Any]]:
    return [
        {
            "source_type": bundle.get("source_type"),
            "status": bundle.get("status"),
            "as_of_utc": bundle.get("as_of_utc"),
            "raw_items": bundle.get("raw_item_count"),
            "retained_items": bundle.get("retained_item_count"),
            "truncation_reasons": "; ".join(bundle.get("truncation_reasons") or []),
            "errors": "; ".join(bundle.get("errors") or []),
        }
        for bundle in package.source_bundles
    ]


def _strategy_rows(package: ReportPackage) -> list[dict[str, Any]]:
    return [
        {
            "block": str(block).replace("_", " ").title(),
            "details": json.dumps(value, ensure_ascii=False, default=str),
        }
        for block, value in package.strategy_payload.items()
    ]


def _chain_rows(package: ReportPackage) -> list[dict[str, Any]]:
    discovery = next(
        (
            bundle
            for bundle in package.source_bundles
            if bundle.get("source_type") == "token_discovery"
        ),
        None,
    )
    counts = ((discovery or {}).get("coverage") or {}).get("chain_counts") or {}
    return [
        {
            "chain": chain,
            "maturity": "emerging" if chain == "robinhood" else "mature",
            "discovery_coverage": (
                "promotion_biased"
                if chain == "robinhood"
                else "organic_oriented_plus_attention"
            ),
            **values,
        }
        for chain, values in sorted(counts.items())
        if isinstance(values, dict)
    ]


def _top_risk(package: ReportPackage) -> str:
    if not package.risks:
        return "No evidence-linked risk supplied"
    risk = package.risks[0]
    value = (
        risk.get("observation")
        or risk.get("title")
        or risk.get("risk")
        or risk.get("condition")
        or "See risk register"
    )
    return str(value)[:100]


def _json_bullets(values: list[dict[str, Any]]) -> str:
    if not values:
        return "- None supported by current evidence"
    return "\n".join(
        f"- {json.dumps(value, ensure_ascii=False, default=str)}" for value in values
    )


def _trend(stance: str) -> str:
    if stance in {"bullish", "cautiously_bullish"}:
        return "up"
    if stance in {"bearish", "cautiously_bearish"}:
        return "down"
    return "neutral"
