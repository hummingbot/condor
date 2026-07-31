"""Private collector for bounded current news and primary-release metadata."""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urljoin
from xml.etree import ElementTree

from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from pydantic import Field

from agents.market_reporter.routines._evidence import (
    bundle_text,
    clean_text,
    evidence_id,
    finalize_bundle,
)
from agents.market_reporter.routines._http import FetchResult, fetch_json, fetch_text
from agents.market_reporter.routines._identity import TICKER_TO_CIK
from agents.market_reporter.routines._models import BaseSourceConfig
from routines.base import RoutineResult

CATEGORY = "Market Reporter"


class Config(BaseSourceConfig):
    """Collect bounded public news and official-release metadata."""

    lookback_hours: int = Field(default=72, ge=1, le=168)
    max_items: int = Field(default=60, ge=1, le=60)


async def run(config: Config, context: Any) -> RoutineResult:
    del context
    async with asyncio.timeout(25):
        items, provider_results, coverage = await collect_news(
            strategy_key=config.strategy_key,
            scope=config.scope,
            themes=config.themes,
            focus_assets=config.focus_assets,
            lookback_hours=config.lookback_hours,
            max_items=config.max_items,
        )
    bundle = finalize_bundle(
        source_type="news",
        strategy_key=config.strategy_key,
        scope=config.scope,
        items=items,
        provider_results=provider_results,
        coverage=coverage,
    )
    return RoutineResult(
        text=bundle_text(bundle, config.run_id),
        table_data=bundle["items"][:20],
        table_columns=["published_at", "provider_id", "title", "url"],
    )


async def collect_news(
    *,
    strategy_key: str,
    scope: str,
    themes: list[str],
    focus_assets: list[str],
    lookback_hours: int,
    max_items: int,
) -> tuple[list[dict[str, Any]], list[FetchResult], dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    terms = list(
        dict.fromkeys(
            query_terms(strategy_key, themes)
            + [str(value).strip() for value in focus_assets if str(value).strip()]
        )
    )
    # Publisher RSS and primary releases are the default news path. GDELT is
    # intentionally omitted: its unauthenticated DOC endpoint repeatedly
    # rate-limits this workload, while adding retry latency and duplicate news.
    tasks = []
    for provider_id, url in feed_urls(scope):
        tasks.append(fetch_text(provider_id, url))
    for symbol in focus_tickers(focus_assets):
        tasks.append(
            fetch_json(
                "sec",
                f"https://data.sec.gov/submissions/CIK{TICKER_TO_CIK[symbol]}.json",
                headers={"Accept-Encoding": "gzip, deflate"},
                retry=False,
            )
        )
    results: list[FetchResult] = list(await asyncio.gather(*tasks))
    items = []
    for result in results:
        if result.status != "complete":
            continue
        if result.provider_id == "sec":
            items.extend(sec_items(result, cutoff))
        elif result.provider_id == "bea":
            items.extend(bea_items(result, cutoff))
        else:
            items.extend(rss_items(result, cutoff))
    raw_retained_count = len(items)
    items = relevant_items(items, terms=terms, scope=scope)
    relevant_count = len(items)
    items = deduplicate_news(items)
    deduplicated_count = len(items)
    items = balance_publishers(items, maximum=max_items)
    return (
        items,
        results,
        {
            "lookback_hours": lookback_hours,
            "query_terms": terms,
            "aggregation_source": "publisher_rss_and_primary_releases",
            "gdelt_omitted": "repeated_public_endpoint_rate_limits",
            "raw_retained_count": raw_retained_count,
            "relevance_retained_count": relevant_count,
            "deduplicated_count": deduplicated_count,
            "final_count": len(items),
            "publisher_count": len(
                {
                    item.get("publisher") or item.get("provider_id")
                    for item in items
                    if item.get("publisher") or item.get("provider_id")
                }
            ),
            "relevance_policy": (
                "crypto_and_memecoin_require_term_match; "
                "official_and_marketwide_tradfi_feeds_are_prequalified"
            ),
        },
    )


def query_terms(strategy_key: str, themes: list[str]) -> list[str]:
    defaults = {
        "crypto_market_intelligence": [
            "bitcoin",
            "ethereum",
            "crypto market",
            "crypto",
            "stablecoin",
        ],
        "tradfi_market_intelligence": [
            "stocks",
            "equities",
            "S&P 500",
            "Nasdaq",
            "Dow",
            "Federal Reserve",
            "FOMC",
            "Treasury",
            "bond yields",
            "inflation",
            "employment",
            "payrolls",
            "GDP",
            "oil",
            "dollar",
            "credit spreads",
            "earnings",
        ],
        "memecoin_market_intelligence": [
            "memecoin",
            "meme coin",
            "dogecoin",
            "shiba",
            "pepe",
            "bonk",
            "Solana token",
            "Ethereum token",
            "Robinhood Chain",
        ],
    }
    return list(dict.fromkeys(defaults[strategy_key] + themes))


def feed_urls(scope: str) -> list[tuple[str, str]]:
    crypto = [
        ("coindesk_rss", "https://www.coindesk.com/arc/outboundfeeds/rss"),
        ("decrypt_rss", "https://decrypt.co/feed"),
        ("cointelegraph_rss", "https://cointelegraph.com/rss"),
    ]
    memecoin_search = [
        (
            "google_news_rss",
            "https://news.google.com/rss/search?"
            "q=memecoin%20OR%20Dogecoin%20OR%20PEPE%20OR%20BONK"
            "%20when%3A2d%20-presale%20-%22price%20prediction%22"
            "%20-%22next%20crypto%22"
            "&hl=en-US&gl=US&ceid=US:en",
        )
    ]
    official = [
        (
            "marketwatch_rss",
            "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        ),
        (
            "federal_reserve",
            "https://www.federalreserve.gov/feeds/press_all.xml",
        ),
        ("bls", "https://www.bls.gov/feed/empsit.rss"),
        ("bls", "https://www.bls.gov/feed/cpi.rss"),
        ("bls", "https://www.bls.gov/feed/ppi.rss"),
        ("bea", "https://www.bea.gov/news/current-releases"),
        ("cftc", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml"),
    ]
    if scope == "memecoin":
        return crypto + memecoin_search
    if scope == "crypto":
        return crypto
    if scope == "tradfi":
        return official
    return crypto + official


def relevant_items(
    items: list[dict[str, Any]],
    *,
    terms: list[str],
    scope: str,
) -> list[dict[str, Any]]:
    """Apply declared query terms without discarding prequalified official news."""
    output = []
    for item in items:
        text = f"{item.get('title') or ''} {item.get('summary') or ''}".casefold()
        if item.get("provider_id") == "google_news_rss" and _is_promotional_news(text):
            continue
        if (
            scope == "memecoin"
            and item.get("provider_id") == "google_news_rss"
            and not _has_memecoin_market_context(text)
        ):
            continue
        if scope in {"tradfi", "both"} and _is_low_signal_tradfi_news(text):
            continue
        matched = [
            term for term in terms if _term_matches(text, str(term).strip().casefold())
        ]
        prequalified = (
            scope in {"tradfi", "both"}
            and item.get("source_class") == "official"
            and item.get("provider_id") in {"bls", "bea"}
        )
        if not prequalified and not matched:
            continue
        value = dict(item)
        value["matched_terms"] = matched[:8]
        value["relevance_score"] = len(matched)
        value["relevance_basis"] = (
            "prequalified_market_source" if prequalified else "query_term_match"
        )
        output.append(value)
    return output


def deduplicate_news(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one best representative for near-identical syndicated headlines."""
    selected: dict[str, dict[str, Any]] = {}
    for item in items:
        fingerprint = _headline_fingerprint(str(item.get("title") or ""))
        if not fingerprint:
            continue
        current = selected.get(fingerprint)
        if current is None or _news_priority(item) > _news_priority(current):
            selected[fingerprint] = item
    return list(selected.values())


def balance_publishers(
    items: list[dict[str, Any]],
    *,
    maximum: int,
) -> list[dict[str, Any]]:
    """Round-robin retained publishers so one feed cannot dominate the digest."""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        buckets[
            str(item.get("publisher") or item.get("provider_id") or "unknown")
        ].append(item)
    for rows in buckets.values():
        rows.sort(key=_news_priority, reverse=True)
    ordered = []
    provider_order = sorted(
        buckets,
        key=lambda provider: _news_priority(buckets[provider][0]),
        reverse=True,
    )
    while len(ordered) < maximum:
        added = False
        for provider in provider_order:
            if buckets[provider]:
                ordered.append(buckets[provider].pop(0))
                added = True
                if len(ordered) >= maximum:
                    break
        if not added:
            break
    return ordered


def _term_matches(text: str, term: str) -> bool:
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9]{2,12}", term):
        return (
            re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None
        )
    return term in text


def _headline_fingerprint(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9 ]+", " ", value.casefold())
    stop = {"a", "an", "and", "for", "in", "of", "on", "the", "to", "with"}
    words = [word for word in normalized.split() if word not in stop]
    return " ".join(words[:18])


def _is_promotional_news(value: str) -> bool:
    markers = (
        "best crypto to invest",
        "next crypto to explode",
        "presale",
        "price prediction",
        "top crypto to buy",
        "100x",
        "1000x",
        "millionaire",
        "sponsored",
        "press release",
        "blockdag",
        "prediction market",
        "tagged:",
        "casino",
        "giveaway",
        "offering up to",
    )
    return any(marker in value for marker in markers)


def _has_memecoin_market_context(value: str) -> bool:
    """Reject name collisions such as pets or people named Pepe."""
    markers = (
        "altcoin",
        "bitcoin",
        "blockchain",
        "bonk",
        "coinmarketcap",
        "coingecko",
        "crypto",
        "cryptocurrency",
        "dexscreener",
        "dogecoin",
        "ethereum",
        "exchange",
        "liquidity",
        "market cap",
        "meme coin",
        "memecoin",
        "shib",
        "solana",
        "token",
        "trading",
        "wallet",
    )
    return any(marker in value for marker in markers)


def _is_low_signal_tradfi_news(value: str) -> bool:
    markers = (
        "issues enforcement action with",
        "issues enforcement actions with",
        "enforcement action with former",
        "enforcement actions with former",
        "passport website",
        "previous marriage get their fair share",
    )
    return any(marker in value for marker in markers)


def _news_priority(item: dict[str, Any]) -> tuple[int, int, str]:
    return (
        int(item.get("source_class") == "official"),
        int(item.get("relevance_score") or 0),
        str(item.get("published_at") or ""),
    )


def gdelt_items(result: FetchResult, cutoff: datetime) -> list[dict[str, Any]]:
    output = []
    for article in (result.data or {}).get("articles") or []:
        published = timestamp(article.get("seendate"))
        if published and published < cutoff:
            continue
        url = str(article.get("url") or "")
        title = clean_text(article.get("title"), 400)
        if not title or not url.startswith(("http://", "https://")):
            continue
        published_at = iso(published)
        publisher = clean_text(
            article.get("domain") or article.get("source") or result.provider_id,
            160,
        )
        output.append(
            {
                "evidence_id": evidence_id("gdelt", url, published_at),
                "provider_id": "gdelt",
                "source_family": "news",
                "source_class": "journalism",
                "published_at": published_at,
                "retrieved_at": result.retrieved_at,
                "title": title,
                "publisher": publisher or result.provider_id,
                "summary": "",
                "url": url,
                "domain": clean_text(article.get("domain"), 120),
                "language": clean_text(article.get("language"), 40),
            }
        )
    return output


def rss_items(result: FetchResult, cutoff: datetime) -> list[dict[str, Any]]:
    try:
        root = ElementTree.fromstring(result.text or "")
    except ElementTree.ParseError:
        return []
    output = []
    entries = list(root.findall(".//item")) + list(
        root.findall(".//{http://www.w3.org/2005/Atom}entry")
    )
    for entry in entries:
        title = clean_text(first_text(entry, ["title"]), 400)
        link = entry_link(entry)
        published = timestamp(
            first_text(entry, ["pubDate", "published", "updated", "date"])
        )
        if published and published < cutoff:
            continue
        if not title or not link.startswith(("http://", "https://")):
            continue
        published_at = iso(published)
        publisher = clean_text(first_text(entry, ["source"]), 160)
        if result.provider_id == "google_news_rss" and " - " in title:
            headline, inferred_publisher = title.rsplit(" - ", 1)
            title = clean_text(headline, 400)
            publisher = publisher or clean_text(inferred_publisher, 160)
        output.append(
            {
                "evidence_id": evidence_id(result.provider_id, link, published_at),
                "provider_id": result.provider_id,
                "source_family": "news",
                "source_class": (
                    "official"
                    if result.provider_id in {"federal_reserve", "bls", "bea", "cftc"}
                    else "journalism"
                ),
                "published_at": published_at,
                "retrieved_at": result.retrieved_at,
                "title": title,
                "publisher": publisher or result.provider_id,
                "summary": clean_text(
                    first_text(entry, ["description", "summary", "content"]),
                    600,
                ),
                "url": link,
            }
        )
    return output


def sec_items(result: FetchResult, cutoff: datetime) -> list[dict[str, Any]]:
    payload = result.data or {}
    recent = (payload.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    output = []
    for index, form in enumerate(forms):
        if form not in {"8-K", "10-Q", "10-K"}:
            continue
        filed = _at(recent.get("filingDate"), index)
        published = timestamp(filed)
        if published and published < cutoff:
            continue
        accession = _at(recent.get("accessionNumber"), index)
        document = _at(recent.get("primaryDocument"), index)
        cik = str(payload.get("cik") or "").zfill(10)
        if not accession or not document or not cik.isdigit():
            continue
        accession_path = accession.replace("-", "")
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
            f"{quote(accession_path)}/{quote(document)}"
        )
        published_at = iso(published)
        output.append(
            {
                "evidence_id": evidence_id("sec", accession, published_at),
                "provider_id": "sec",
                "source_family": "official",
                "source_class": "official",
                "published_at": published_at,
                "retrieved_at": result.retrieved_at,
                "title": f"{clean_text(payload.get('name'), 160)} filed {form}",
                "summary": "",
                "url": url,
                "form": form,
                "accession": accession,
                "cik": cik,
            }
        )
    return output


def bea_items(result: FetchResult, cutoff: datetime) -> list[dict[str, Any]]:
    """Normalize BEA's public current-release table without inferring content."""
    soup = BeautifulSoup(result.text or "", "html.parser")
    output = []
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        link_node = row.find("a", href=True)
        title = clean_text(cells[0].get_text(" ", strip=True), 400)
        published = timestamp(cells[-1].get_text(" ", strip=True))
        if not link_node or not title or not published or published < cutoff:
            continue
        link = urljoin(
            "https://www.bea.gov/news/current-releases",
            str(link_node.get("href") or ""),
        )
        published_at = iso(published)
        output.append(
            {
                "evidence_id": evidence_id("bea", link, published_at),
                "provider_id": "bea",
                "source_family": "official",
                "source_class": "official",
                "published_at": published_at,
                "retrieved_at": result.retrieved_at,
                "title": title,
                "summary": "",
                "url": link,
            }
        )
    return output


def focus_tickers(values: list[str]) -> list[str]:
    return [
        value.strip().upper()
        for value in values
        if value.strip().upper() in TICKER_TO_CIK
    ][:12]


def entry_link(entry: ElementTree.Element) -> str:
    text = first_text(entry, ["link"])
    if text:
        return text
    for child in entry:
        if child.tag.endswith("link") and child.attrib.get("href"):
            return str(child.attrib["href"])
    return ""


def first_text(entry: ElementTree.Element, names: list[str]) -> str:
    for child in entry.iter():
        name = child.tag.rsplit("}", 1)[-1]
        if name in names and child.text:
            return child.text
    return ""


def timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date_parser.parse(str(value))
    except (ValueError, TypeError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(value: datetime | None) -> str:
    return (
        value.isoformat().replace("+00:00", "Z")
        if value
        else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )


def _at(values: Any, index: int) -> str:
    return (
        str(values[index]) if isinstance(values, list) and index < len(values) else ""
    )
