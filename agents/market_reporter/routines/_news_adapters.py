"""Private news fetching and normalization adapters."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urljoin
from xml.etree import ElementTree

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from agents.market_reporter.routines._evidence import clean_text, evidence_id
from agents.market_reporter.routines._http import FetchResult, fetch_json, fetch_text
from agents.market_reporter.routines._identity import TICKER_TO_CIK


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
    terms = query_terms(strategy_key, themes)
    tasks = [
        fetch_json(
            "gdelt",
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={
                "query": " OR ".join(f'"{term}"' for term in terms[:12]),
                "mode": "ArtList",
                "maxrecords": min(75, max_items * 2),
                "format": "json",
                "sort": "HybridRel",
                "timespan": f"{lookback_hours}h",
            },
        )
    ]
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
        if result.provider_id == "gdelt":
            items.extend(gdelt_items(result, cutoff))
        elif result.provider_id == "sec":
            items.extend(sec_items(result, cutoff))
        elif result.provider_id == "bea":
            items.extend(bea_items(result, cutoff))
        else:
            items.extend(rss_items(result, cutoff))
    items.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    items.sort(key=lambda item: item.get("source_class") != "official")
    return (
        items[:max_items],
        results,
        {"lookback_hours": lookback_hours, "query_terms": terms},
    )


def query_terms(strategy_key: str, themes: list[str]) -> list[str]:
    defaults = {
        "crypto_market_intelligence": [
            "bitcoin",
            "ethereum",
            "crypto market",
            "stablecoin",
        ],
        "tradfi_market_intelligence": [
            "US stocks",
            "Federal Reserve",
            "Treasury yields",
            "earnings",
        ],
        "memecoin_market_intelligence": [
            "memecoin",
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
    official = [
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
    if scope in {"crypto", "memecoin"}:
        return crypto
    if scope == "tradfi":
        return official
    return crypto + official


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
        output.append(
            {
                "evidence_id": evidence_id("gdelt", url, published_at),
                "provider_id": "gdelt",
                "source_family": "news",
                "source_class": "journalism",
                "published_at": published_at,
                "retrieved_at": result.retrieved_at,
                "title": title,
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
