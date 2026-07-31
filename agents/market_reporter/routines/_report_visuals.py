"""Render the compact, plain-English Market Reporter v3 report."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from agents.market_reporter.routines._memecoin_catalog import (
    MEMECOIN_META_LABELS,
)
from agents.market_reporter.routines._models import AnalysisCard, ReportPackage
from condor.reports import ReportBuilder

_STANCE_LABELS = {
    "bullish": "Positive",
    "cautiously_bullish": "Cautiously positive",
    "neutral": "Neutral",
    "cautiously_bearish": "Cautiously negative",
    "bearish": "Negative",
    "mixed": "Mixed",
}
_STATE_LABELS = {
    "priority_research": "Priority research",
    "conditional_watch": "Watch if conditions improve",
    "risk_watch": "Risk watch",
    "avoid_for_now": "Avoid for now",
}
_DIRECTION_COLORS = {
    "bullish": "#22c55e",
    "bearish": "#ef4444",
    "mixed": "#d4a845",
    "unclear": "#94a3b8",
    "Rising": "#22c55e",
    "Falling": "#ef4444",
    "Flat": "#94a3b8",
    "Leader": "#22c55e",
    "Laggard": "#ef4444",
}
_MEMECOIN_CHAINS = (
    ("ethereum", "Ethereum"),
    ("solana", "Solana"),
    ("robinhood", "Robinhood Chain"),
)


async def render_report(package: ReportPackage) -> str:
    """Build and save one deliberately small v3 report."""
    builder = ReportBuilder(package.metadata.title)
    builder.source("routine", "build_market_report").tags(
        ["market-intelligence", package.metadata.strategy_key, package.metadata.scope]
    )
    builder.manual_order()

    leaders = _leader_rows(package)
    cross_market = _cross_market_rows(package)
    drivers = _driver_rows(package)
    meta_chain = _meta_chain_rows(package)
    evidence = _evidence_rows(package)

    for name, rows in (
        ("v3_leaders", leaders),
        ("v3_drivers", drivers),
        ("v3_evidence", evidence),
    ):
        if rows:
            builder.dataset(name, rows)
    for chain_key, _ in _MEMECOIN_CHAINS:
        rows = [
            row
            for row in meta_chain
            if row["chain_key"] == chain_key
            and row["size_usd"] is not None
            and row["size_usd"] > 0
            and row["change_24h_pct"] is not None
        ]
        if rows:
            builder.dataset(f"v3_metas_{chain_key}", rows)

    _market_section(
        builder,
        package,
        leaders,
        drivers,
        meta_chain,
        cross_market,
    )
    _events_section(builder, package)
    _analysis_section(builder, package)
    _audit_footer(builder, package, evidence)
    return await builder.save()


def _market_section(
    builder: ReportBuilder,
    package: ReportPackage,
    leaders: list[dict[str, Any]],
    drivers: list[dict[str, Any]],
    meta_chain: list[dict[str, Any]],
    cross_market: list[dict[str, Any]],
) -> None:
    builder.section(
        "Market snapshot and leaders",
        "The broad picture first, followed by the data that supports it.",
    )
    builder.markdown(
        f"**Evidence cutoff:** {_display_time(package.metadata.as_of_utc)} · "
        f"**Display timezone:** {_clean(package.metadata.report_timezone)} · "
        f"**Primary time frame:** {_clean(package.metadata.near_horizon)}"
    )
    for metric in package.analysis_context.snapshot_metrics[:6]:
        value, delta, trend = _kpi(metric)
        builder.kpi(str(metric.get("label") or "Market metric"), value, delta, trend)

    if package.market_view:
        builder.markdown(
            f"### Today’s read — {_clean(package.market_view.title)}\n\n"
            f"{_clean(package.market_view.interpretation)}"
        )
    else:
        builder.markdown(
            "### Today’s read\n\n"
            "The available data is too limited for a reliable market direction call."
        )
    if cross_market:
        builder.markdown("**Cross-market anchors requested in this session:**")
        builder.table(
            cross_market,
            ["Asset", "Market", "7d move", "30d move", "Last price"],
        )

    strategy = package.metadata.strategy_key
    if strategy == "memecoin_market_intelligence":
        _memecoin_charts(builder, meta_chain)
        if meta_chain:
            builder.table(
                [
                    {
                        "Meta": row["meta"],
                        "Chain": row["chain"],
                        "Sampled assets": row["sampled_assets"],
                        "Sampled market cap": _money(row["market_cap_usd"]),
                        "24h turnover": _money(row["volume_24h_usd"]),
                        "24h move": _percent(row["change_24h_pct"]),
                        "Representatives": row["representatives"],
                        "Coverage": row["coverage"],
                    }
                    for row in meta_chain[:18]
                ],
                [
                    "Meta",
                    "Chain",
                    "Sampled assets",
                    "Sampled market cap",
                    "24h turnover",
                    "24h move",
                    "Representatives",
                    "Coverage",
                ],
            )
    else:
        if leaders:
            builder.chart(
                "horizontal_bar",
                (
                    "S&P 500 stock sample — seven-day leaders and laggards"
                    if strategy == "tradfi_market_intelligence"
                    else "Seven-day leaders and laggards"
                ),
                "v3_leaders",
                "asset",
                "return_7d_pct",
                color="group",
                color_map=_DIRECTION_COLORS,
                cross_filter=False,
                x_label="Seven-day return (%)",
                category_order=[row["asset"] for row in leaders],
                width=12,
                height=340,
                component_id="v3_leaders_chart",
            )
        if drivers:
            builder.chart(
                "horizontal_bar",
                "Most important market drivers — research weight, not probability",
                "v3_drivers",
                "driver_axis_label",
                "importance",
                color="direction",
                color_map=_DIRECTION_COLORS,
                cross_filter=False,
                x_label="Research importance (1–5)",
                category_order=[row["driver_axis_label"] for row in drivers],
                width=12,
                height=320,
                component_id="v3_drivers_chart",
            )
            builder.markdown(
                "**Driver key and analysis:**\n\n"
                + "\n".join(
                    f"- **{row['short_label']} — {row['driver']} "
                    f"({_plain_label(row['direction'])}):** "
                    f"{row['explanation']}"
                    for row in drivers[:5]
                )
            )
        if leaders:
            builder.table(
                [
                    {
                        "Asset": row["asset"],
                        "Group": row["group"],
                        "7d move": _percent(row["return_7d_pct"]),
                        "30d move": _percent(row.get("return_30d_pct")),
                        "Last price": _compact_number(row.get("last_price")),
                    }
                    for row in leaders
                ],
                ["Asset", "Group", "7d move", "30d move", "Last price"],
            )


def _memecoin_charts(
    builder: ReportBuilder,
    meta_chain: list[dict[str, Any]],
) -> None:
    builder.markdown(
        "**Chain theme maps:** each panel uses provider-categorized coins from the "
        "bounded CoinGecko sample. Bar length is sampled market cap and color shows "
        "the market-cap-weighted 24-hour direction. Theme memberships can overlap, "
        "so compare themes within a panel and read each bar independently rather "
        "than adding them together."
    )
    for chain_key, chain_label in _MEMECOIN_CHAINS:
        chain_rows = [
            row
            for row in meta_chain
            if row["chain_key"] == chain_key
            and row["size_usd"] is not None
            and row["size_usd"] > 0
            and row["change_24h_pct"] is not None
        ]
        if not chain_rows:
            builder.markdown(
                f"### {chain_label}\n\n"
                "No provider-categorized assets in the bounded sample have both "
                "market-cap and 24-hour change data, so no zero or empty chart is "
                "shown."
            )
            continue
        builder.chart(
            "horizontal_bar",
            f"{chain_label} memecoin themes — sampled market cap and 24h direction",
            f"v3_metas_{chain_key}",
            "meta",
            "size_usd",
            color="direction",
            color_map=_DIRECTION_COLORS,
            cross_filter=False,
            x_label="Sampled category market cap (USD)",
            category_order=[row["meta"] for row in chain_rows],
            width=12,
            height=max(260, len(chain_rows) * 64),
            component_id=f"v3_meta_{chain_key}_chart",
        )


def _events_section(builder: ReportBuilder, package: ReportPackage) -> None:
    builder.section(
        "Events that could move the market",
        "Only verified, dated events selected by the analysis are shown.",
    )
    if package.event_outlook:
        builder.markdown(_analysis_card(package.event_outlook, compact=True))
    elif not package.event_impacts:
        builder.markdown(
            "No verified event has enough supporting data for a useful impact view."
        )

    event_lookup = {
        str(event.get("evidence_id") or ""): event
        for event in package.analysis_context.events
    }
    rows = []
    for impact in package.event_impacts[:5]:
        event = event_lookup.get(impact.event_evidence_id)
        if not event:
            continue
        rows.append(
            {
                "Date": _display_time(
                    event.get("display_time") or event.get("event_time_utc")
                ),
                "Event": _clean(event.get("title")),
                "Why it matters": _clean(impact.why_it_matters),
                "Most affected": ", ".join(impact.most_affected),
                "Watch for": _clean(impact.watch_for),
                "Priority": impact.priority.title(),
                "Source": (
                    f"[{_plain_label(event.get('provider_id'))} official source]"
                    f"({_clean(event.get('url'))})"
                ),
            }
        )
    if rows:
        columns = [
            "Date",
            "Event",
            "Why it matters",
            "Most affected",
            "Watch for",
            "Priority",
            "Source",
        ]
        builder.markdown(
            "| " + " | ".join(columns) + " |\n"
            "| "
            + " | ".join("---" for _ in columns)
            + " |\n"
            + "\n".join(
                "| "
                + " | ".join(str(row[column]).replace("|", r"\|") for column in columns)
                + " |"
                for row in rows
            )
        )
    elif package.event_impacts:
        builder.markdown(
            "The selected event notes could not be matched to the verified calendar."
        )


def _analysis_section(builder: ReportBuilder, package: ReportPackage) -> None:
    builder.section(
        "Analyst view and research highlights",
        "What the evidence suggests, what to watch, and what would change the view.",
    )
    if package.market_view:
        builder.markdown(_analysis_card(package.market_view))
    if package.movers_view:
        builder.markdown(_analysis_card(package.movers_view))
    if not package.market_view and not package.movers_view:
        builder.markdown(
            "Coverage is too limited for a responsible analyst view. "
            "Use the technical audit below to see what is missing."
        )
    headlines = _news_headlines(package)
    if headlines:
        builder.markdown(
            "### News headlines\n\n"
            "Recent English-language headlines retained for this market view:\n\n"
            + "\n".join(_headline_bullet(row) for row in headlines)
        )

    items = _items_by_evidence(package)
    fundamentals = {
        str(item.get("symbol") or ""): item
        for bundle in package.source_bundles
        if bundle.get("source_type") == "fundamentals"
        for item in bundle.get("items") or []
        if item.get("symbol")
    }
    if package.research_highlights:
        builder.markdown("### Research highlights")
    for highlight in sorted(package.research_highlights, key=lambda row: row.rank)[:3]:
        item = items.get(highlight.asset_evidence_id, {})
        name, facts = _asset_facts(item, fundamentals)
        fact_line = " · ".join(facts)
        detail = f"\n\n**Observed data:** {fact_line}" if fact_line else ""
        watch = "; ".join(highlight.invalidation_conditions)
        builder.markdown(
            f"#### {highlight.rank}. {_clean(name)} — "
            f"{_STATE_LABELS[highlight.research_state]}\n\n"
            f"**Why it matters now:** {_clean(highlight.why_now)}{detail}\n\n"
            f"**Direction:** {_STANCE_LABELS[highlight.stance]} · "
            f"**How sure:** {highlight.confidence.title()} — "
            f"{_clean(highlight.confidence_reason)} · "
            f"**Time frame:** {_clean(highlight.horizon)}\n\n"
            f"**Main risk:** {_clean(highlight.main_risk)}\n\n"
            f"**What would change this view:** {_clean(watch)}"
        )


def _audit_footer(
    builder: ReportBuilder,
    package: ReportPackage,
    evidence: list[dict[str, Any]],
) -> None:
    coverage = package.coverage_assessment
    builder.markdown(
        "### Technical audit\n\n"
        f"Coverage grade: **{coverage.grade.title()}** · "
        f"maximum analysis confidence: **{coverage.confidence_cap.title()}**. "
        "This area is for provenance and debugging; it is not part of the market view."
    )
    builder.markdown(f"*{_clean(package.metadata.disclaimer)}*")
    coverage_rows = []
    for row in package.analysis_context.coverage_summary:
        notes = [*list(row.get("warnings") or []), *list(row.get("errors") or [])]
        coverage_rows.append(
            {
                "Source": _plain_label(row.get("source_type")),
                "Status": _plain_label(row.get("status")),
                "Kept": row.get("retained_items", 0),
                "Raw": row.get("raw_items", 0),
                "Latest observation": _display_time(row.get("newest_source_time")),
                "Notes": "; ".join(_clean(note) for note in notes[:2]) or "None",
            }
        )
    if coverage_rows:
        builder.table(
            coverage_rows,
            ["Source", "Status", "Kept", "Raw", "Latest observation", "Notes"],
        )

    limitations = list(
        dict.fromkeys(
            [
                *package.data_limitations,
                *package.analysis_context.data_limitations,
            ]
        )
    )
    if limitations:
        builder.markdown(
            "**Known data limits:**\n\n"
            + "\n".join(f"- {_clean(value)}" for value in limitations[:8])
        )
    if evidence:
        builder.data_table(
            "v3_evidence",
            columns=[
                "evidence_id",
                "source_type",
                "provider",
                "observed_at",
                "item",
                "url",
            ],
            title="Evidence audit",
            searchable=True,
            sortable=True,
            page_size=15,
            width=12,
            component_id="v3_evidence_audit",
        )


def _leader_rows(package: ReportPackage) -> list[dict[str, Any]]:
    if package.metadata.strategy_key == "tradfi_market_intelligence":
        features = package.analysis_context.strategy_features
        groups = (
            ("Leader", features.get("stock_leaders") or []),
            ("Laggard", features.get("stock_laggards") or []),
        )
    else:
        assets = [
            row
            for row in package.analysis_context.market_snapshot.get("assets", [])
            if row.get("asset_class") == "crypto"
            and _finite(row.get("return_7d_pct")) is not None
        ]
        assets.sort(
            key=lambda row: _finite(row.get("return_7d_pct")) or 0.0,
            reverse=True,
        )
        groups = (
            ("Leader", assets[:3]),
            ("Laggard", list(reversed(assets[-3:]))),
        )
    rows = []
    seen = set()
    for group, values in groups:
        for item in values[:3]:
            symbol = _clean(item.get("symbol"))
            change = _finite(item.get("return_7d_pct"))
            if not symbol or change is None or symbol in seen:
                continue
            seen.add(symbol)
            company_name = _clean(item.get("company_name"))
            rows.append(
                {
                    "asset": (f"{company_name} ({symbol})" if company_name else symbol),
                    "symbol": symbol,
                    "group": group,
                    "return_7d_pct": change,
                    "return_30d_pct": _finite(item.get("return_30d_pct")),
                    "last_price": _finite(item.get("last_price")),
                }
            )
    return sorted(rows, key=lambda row: row["return_7d_pct"])


def _cross_market_rows(package: ReportPackage) -> list[dict[str, Any]]:
    if package.metadata.scope != "both":
        return []
    wanted = ("BTC", "ETH", "SPY", "QQQ")
    by_symbol = {
        str(row.get("symbol") or ""): row
        for row in package.analysis_context.market_snapshot.get("assets", [])
    }
    rows = []
    for symbol in wanted:
        item = by_symbol.get(symbol)
        if not item:
            continue
        rows.append(
            {
                "Asset": symbol,
                "Market": "Crypto" if symbol in {"BTC", "ETH"} else "U.S. stocks",
                "7d move": _percent(item.get("return_7d_pct")),
                "30d move": _percent(item.get("return_30d_pct")),
                "Last price": _compact_number(item.get("last_price")),
            }
        )
    return rows


def _driver_rows(package: ReportPackage) -> list[dict[str, Any]]:
    if package.metadata.strategy_key == "memecoin_market_intelligence":
        return []
    ordered = sorted(
        package.drivers,
        key=lambda value: value.importance,
        reverse=True,
    )[:5]
    return [
        {
            "driver": _clean(driver.title),
            "short_label": _clean(driver.short_label),
            "driver_axis_label": f"{_clean(driver.short_label)}\u2003\u2003",
            "importance": driver.importance,
            "direction": driver.direction,
            "explanation": _clean(driver.explanation),
        }
        for driver in ordered
    ]


def _meta_chain_rows(package: ReportPackage) -> list[dict[str, Any]]:
    if package.metadata.strategy_key != "memecoin_market_intelligence":
        return []
    chain_labels = dict(_MEMECOIN_CHAINS)
    features = package.analysis_context.strategy_features
    provider_rows = features.get("provider_meta_chain_samples") or []
    source_rows = provider_rows or features.get("meta_chain_overview", [])
    rows = []
    for row in source_rows:
        meta = MEMECOIN_META_LABELS.get(str(row.get("primary_meta") or "").casefold())
        chain_key = str(row.get("chain") or "").casefold()
        if not meta or chain_key not in chain_labels:
            continue
        provider_sample = bool(provider_rows)
        change = _finite(
            row.get("market_cap_weighted_change_24h_pct")
            if provider_sample
            else row.get("liquidity_weighted_change_24h_pct")
        )
        market_cap = _finite(
            row.get("sample_market_cap_usd")
            if provider_sample
            else row.get("observed_market_cap_usd")
        )
        liquidity = _finite(row.get("observed_liquidity_usd"))
        size_usd = market_cap if provider_sample else liquidity
        rows.append(
            {
                "meta": meta,
                "chain_key": chain_key,
                "chain": chain_labels[chain_key],
                "sampled_assets": int(
                    (
                        row.get("sampled_constituent_count")
                        if provider_sample
                        else row.get("eligible_pair_count")
                    )
                    or 0
                ),
                "market_cap_usd": market_cap,
                "size_usd": size_usd,
                "volume_24h_usd": _finite(
                    row.get("sample_volume_24h_usd")
                    if provider_sample
                    else row.get("observed_volume_24h_usd")
                ),
                "change_24h_pct": change,
                "direction": (
                    "Rising"
                    if change is not None and change > 0.1
                    else "Falling" if change is not None and change < -0.1 else "Flat"
                ),
                "representatives": ", ".join(
                    str(value) for value in row.get("representative_symbols") or []
                )
                or "Not identified",
                "coverage": (
                    "CoinGecko top categorized sample"
                    if provider_sample
                    else "Eligible exact-pair fallback"
                ),
            }
        )
    return rows


def _evidence_rows(package: ReportPackage) -> list[dict[str, Any]]:
    rows = []
    for bundle in package.source_bundles:
        source = _plain_label(bundle.get("source_type"))
        for item in bundle.get("items") or []:
            evidence_id = item.get("evidence_id")
            if not evidence_id:
                continue
            nested_market = item.get("market") or {}
            rows.append(
                {
                    "evidence_id": evidence_id,
                    "source_type": source,
                    "provider": item.get("provider_id") or item.get("publisher") or "",
                    "observed_at": item.get("source_time")
                    or item.get("published_at")
                    or item.get("event_time_utc")
                    or "",
                    "item": item.get("title")
                    or item.get("name")
                    or item.get("symbol")
                    or nested_market.get("symbol")
                    or item.get("metric")
                    or "Evidence item",
                    "url": item.get("url") or "",
                }
            )
    return rows


def _items_by_evidence(package: ReportPackage) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("evidence_id")): item
        for bundle in package.source_bundles
        for item in bundle.get("items") or []
        if item.get("evidence_id")
    }


def _asset_facts(
    item: dict[str, Any],
    fundamentals: dict[str, dict[str, Any]],
) -> tuple[str, list[str]]:
    market = item.get("market") or {}
    metrics = item.get("metrics") or {}
    name = (
        item.get("name")
        or item.get("symbol")
        or market.get("name")
        or market.get("symbol")
        or "Selected asset"
    )
    facts = []
    if item.get("chain_id"):
        facts.append(f"Chain: {_plain_label(item.get('chain_id'))}")
    for label, value, formatter in (
        ("Last price", metrics.get("last_price"), _compact_number),
        ("7d move", metrics.get("return_7d_pct"), _percent),
        (
            "Market cap",
            market.get("market_cap_usd") or item.get("market_cap_usd"),
            _money,
        ),
        (
            "24h move",
            market.get("price_change_24h_pct") or item.get("price_change_24h_pct"),
            _percent,
        ),
        ("Liquidity", market.get("liquidity_usd"), _money),
        (
            "24h turnover",
            market.get("volume_24h_usd") or item.get("volume_24h_usd"),
            _money,
        ),
    ):
        if _finite(value) is not None:
            facts.append(f"{label}: {formatter(value)}")
    fundamental = fundamentals.get(str(item.get("symbol") or ""))
    if fundamental:
        source_facts = fundamental.get("facts") or {}
        for label, key in (
            ("Revenue vs comparable period", "revenue"),
            ("Net income vs comparable period", "net_income"),
        ):
            change = _finite(
                ((source_facts.get(key) or {}).get("prior_comparable") or {}).get(
                    "change_pct"
                )
            )
            if change is not None:
                facts.append(f"{label}: {_percent(change)}")
    return _clean(name), facts[:5]


def _analysis_card(card: AnalysisCard, *, compact: bool = False) -> str:
    text = (
        f"### {_clean(card.title)}\n\n"
        f"**What we see:** {_clean(card.observation)}\n\n"
        f"**Why it matters:** {_clean(card.interpretation)}\n\n"
        f"**Direction:** {_STANCE_LABELS[card.stance]} · "
        f"**How sure:** {card.confidence.title()} — "
        f"{_clean(card.confidence_reason)} · "
        f"**Time frame:** {_clean(card.horizon)}"
    )
    if compact:
        return text
    return (
        text
        + "\n\n**Watch next:** "
        + "; ".join(_clean(value) for value in card.what_to_watch)
        + "\n\n**What would change this view:** "
        + "; ".join(_clean(value) for value in card.invalidation_conditions)
    )


def _kpi(metric: dict[str, Any]) -> tuple[str, str | None, str]:
    value = metric.get("value")
    unit = str(metric.get("unit") or "")
    if _finite(value) is not None:
        if unit == "USD":
            display = _money(value)
        elif unit == "%":
            display = _percent(value)
        else:
            display = f"{_compact_number(value)} {unit}".strip()
    else:
        display = _clean(value) or "Not available"

    change = _finite(metric.get("change"))
    if change is None:
        return display, None, "neutral"
    delta = _percent(change)
    adverse = bool(metric.get("adverse_when_up"))
    positive = change > 0
    trend = "down" if positive == adverse else "up"
    return display, delta, trend


def _display_time(value: Any) -> str:
    if not value:
        return "Not provided"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return _clean(value)
    return parsed.strftime("%d %b %Y, %H:%M %Z").strip()


def _money(value: Any) -> str:
    number = _finite(value)
    if number is None:
        return "Not available"
    absolute = abs(number)
    for divisor, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if absolute >= divisor:
            return f"${number / divisor:,.2f}{suffix}"
    return f"${number:,.2f}"


def _percent(value: Any) -> str:
    number = _finite(value)
    return "Not available" if number is None else f"{number:+.2f}%"


def _compact_number(value: Any) -> str:
    number = _finite(value)
    if number is None:
        return "Not available"
    if abs(number) >= 1_000:
        return f"{number:,.0f}"
    if abs(number) >= 1:
        return f"{number:,.2f}"
    return f"{number:,.6f}".rstrip("0").rstrip(".")


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _plain_label(value: Any) -> str:
    return _clean(value).replace("_", " ").title()


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _short_label(value: Any, maximum: int) -> str:
    text = _clean(value)
    if len(text) <= maximum:
        return text
    clipped = text[: maximum - 1].rsplit(" ", 1)[0]
    if len(clipped) < maximum // 2:
        clipped = text[: maximum - 1]
    return f"{clipped}…"


def _news_headlines(package: ReportPackage) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for cluster in package.analysis_context.news_clusters:
        for headline in cluster.get("highlights") or []:
            evidence_id = str(headline.get("evidence_id") or "")
            if not evidence_id or evidence_id in seen:
                continue
            seen.add(evidence_id)
            rows.append(headline)
            if len(rows) == 5:
                return rows
    return rows


def _headline_bullet(row: dict[str, Any]) -> str:
    title = _clean(row.get("title"))
    url = _clean(row.get("url"))
    linked_title = f"[{title}]({url})" if url.startswith("https://") else title
    summary = _short_label(row.get("summary"), 220)
    details = " · ".join(
        value
        for value in (
            _plain_label(row.get("publisher")),
            _display_time(row.get("published_at")),
        )
        if value
    )
    suffix = f" — {summary}" if summary else ""
    attribution = f" ({details})" if details else ""
    return f"- **{linked_title}**{suffix}{attribution}"
