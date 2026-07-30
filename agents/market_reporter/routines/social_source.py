"""Collect a transparent bounded sample of keyless public social discussion."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

from pydantic import Field

from agents.market_reporter.routines._evidence import (
    bundle_text,
    clean_text,
    evidence_id,
    finalize_bundle,
    safe_float,
)
from agents.market_reporter.routines._http import FetchResult, fetch_json
from agents.market_reporter.routines._models import BaseSourceConfig
from routines.base import RoutineResult

CATEGORY = "Market Reporter"


class Config(BaseSourceConfig):
    """Collect public Bluesky and Mastodon discussion without scoring sentiment."""

    max_items: int = Field(default=60, ge=1, le=60)


async def run(config: Config, context: Any) -> RoutineResult:
    del context
    terms = _query_terms(config)
    tasks = [
        fetch_json(
            "bluesky",
            "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
            params={"q": " OR ".join(terms[:8]), "limit": min(100, config.max_items)},
        )
    ]
    for hashtag in _hashtags(config):
        tasks.append(
            fetch_json(
                "mastodon",
                f"https://mastodon.social/api/v1/timelines/tag/{quote(hashtag)}",
                params={"limit": min(40, config.max_items)},
            )
        )

    async with asyncio.timeout(25):
        results: list[FetchResult] = list(await asyncio.gather(*tasks))
    items = []
    for result in results:
        if result.status != "complete":
            continue
        if result.provider_id == "bluesky":
            items.extend(_bluesky_items(result))
        else:
            items.extend(_mastodon_items(result))
    items.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
    items = items[: config.max_items]
    warnings = []
    networks = {item["provider_id"] for item in items}
    if len(items) < 10:
        warnings.append("small_public_social_sample")
    if len(networks) < 2:
        warnings.append("single_public_social_network")
    warnings.append("x_and_reddit_not_covered")
    bundle = finalize_bundle(
        source_type="social",
        strategy_key=config.strategy_key,
        scope=config.scope,
        items=items,
        provider_results=results,
        warnings=warnings,
        coverage={
            "query_terms": terms,
            "network_count": len(networks),
            "oldest_observation": min(
                (str(item.get("published_at") or "") for item in items),
                default="",
            ),
            "newest_observation": max(
                (str(item.get("published_at") or "") for item in items),
                default="",
            ),
            "narrative_confidence_cap": (
                "low" if len(items) < 10 or len(networks) < 2 else "moderate"
            ),
        },
    )
    return RoutineResult(
        text=bundle_text(bundle),
        table_data=bundle["items"][:20],
        table_columns=[
            "published_at",
            "provider_id",
            "author_handle",
            "excerpt",
            "url",
        ],
    )


def _query_terms(config: Config) -> list[str]:
    defaults = {
        "crypto_market_intelligence": ["bitcoin", "ethereum", "crypto"],
        "tradfi_market_intelligence": ["stocks", "SPY", "Federal Reserve"],
        "memecoin_market_intelligence": [
            "memecoin",
            "Solana",
            "Ethereum",
            "Robinhood Chain",
        ],
    }
    return list(dict.fromkeys(defaults[config.strategy_key] + config.themes))


def _hashtags(config: Config) -> list[str]:
    return {
        "crypto_market_intelligence": ["bitcoin", "ethereum"],
        "tradfi_market_intelligence": ["stocks", "economics"],
        "memecoin_market_intelligence": ["memecoin", "solana"],
    }[config.strategy_key]


def _bluesky_items(result: FetchResult) -> list[dict[str, Any]]:
    output = []
    for post in (result.data or {}).get("posts") or []:
        record = post.get("record") or {}
        uri = str(post.get("uri") or "")
        handle = clean_text((post.get("author") or {}).get("handle"), 120)
        rkey = uri.rsplit("/", 1)[-1]
        url = f"https://bsky.app/profile/{quote(handle)}/post/{quote(rkey)}"
        published = str(record.get("createdAt") or post.get("indexedAt") or "")
        excerpt = clean_text(record.get("text"), 280)
        if not uri or not handle or not excerpt:
            continue
        output.append(
            {
                "evidence_id": evidence_id("bluesky", uri, published),
                "provider_id": "bluesky",
                "source_family": "social",
                "published_at": published,
                "retrieved_at": result.retrieved_at,
                "author_handle": handle,
                "excerpt": excerpt,
                "url": url,
                "engagement": {
                    "likes": int(safe_float(post.get("likeCount")) or 0),
                    "reposts": int(safe_float(post.get("repostCount")) or 0),
                    "replies": int(safe_float(post.get("replyCount")) or 0),
                },
            }
        )
    return output


def _mastodon_items(result: FetchResult) -> list[dict[str, Any]]:
    output = []
    data = result.data if isinstance(result.data, list) else []
    for post in data:
        post_id = str(post.get("id") or "")
        excerpt = clean_text(post.get("content"), 280)
        url = str(post.get("url") or "")
        account = post.get("account") or {}
        handle = clean_text(account.get("acct"), 120)
        published = str(post.get("created_at") or "")
        if not post_id or not excerpt or not url.startswith("https://"):
            continue
        output.append(
            {
                "evidence_id": evidence_id("mastodon", post_id, published),
                "provider_id": "mastodon",
                "source_family": "social",
                "published_at": published,
                "retrieved_at": result.retrieved_at,
                "author_handle": handle,
                "excerpt": excerpt,
                "url": url,
                "engagement": {
                    "likes": int(safe_float(post.get("favourites_count")) or 0),
                    "reposts": int(safe_float(post.get("reblogs_count")) or 0),
                    "replies": int(safe_float(post.get("replies_count")) or 0),
                },
            }
        )
    return output
