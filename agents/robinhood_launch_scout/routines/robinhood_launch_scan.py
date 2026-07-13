# Purpose: discover and conservatively rank early Robinhood Chain token and NFT launches using read-only evidence sources.

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
from pydantic import BaseModel, Field, field_validator

try:
    from routines.base import RoutineResult
except ModuleNotFoundError:
    import sys

    sys.path.append(str(Path(__file__).resolve().parents[3]))
    from routines.base import RoutineResult


CATEGORY = "Robinhood Chain Research"
logger = logging.getLogger(__name__)

CHAIN_ID = 4663
CHAIN_SLUG = "robinhood"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
BURN_ADDRESSES = {
    ZERO_ADDRESS,
    "0x000000000000000000000000000000000000dead",
    "0x0000000000000000000000000000000000000001",
}
SHORTENER_DOMAINS = {
    "bit.ly",
    "buff.ly",
    "cutt.ly",
    "is.gd",
    "l1nq.com",
    "rebrand.ly",
    "shorturl.at",
    "t.co",
    "tiny.cc",
    "tinyurl.com",
}
POOL_NAME_MARKERS = ("pool", "uniswap", "pancake", "liquidity")
ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
X_HANDLE_RE = re.compile(
    r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})(?:[/?#]|$)", re.I
)


class Config(BaseModel):
    """Scan Robinhood Chain for early token and NFT launches and rank evidence conservatively."""

    gecko_api_base: str = Field(
        default="https://api.geckoterminal.com/api/v2",
        description="GeckoTerminal API base URL",
    )
    dexscreener_api_base: str = Field(
        default="https://api.dexscreener.com",
        description="DexScreener API base URL",
    )
    blockscout_api_base: str = Field(
        default="https://robinhoodchain.blockscout.com/api/v2",
        description="Robinhood Chain Blockscout API base URL",
    )
    explorer_base: str = Field(
        default="https://robinhoodchain.blockscout.com",
        description="Explorer link base",
    )
    rpc_url: str = Field(
        default="https://rpc.mainnet.chain.robinhood.com",
        description="Official Robinhood Chain mainnet RPC",
    )
    request_timeout_seconds: int = Field(default=20, ge=5, le=60)
    max_concurrency: int = Field(default=8, ge=1, le=20)
    new_pool_pages: int = Field(default=2, ge=1, le=5)
    include_trending_pools: bool = Field(default=True)
    discover_nfts: bool = Field(default=True)
    nft_lookback_blocks: int = Field(
        default=5_000,
        ge=100,
        le=25_000,
        description="Recent block range used to find ERC-721 Transfer activity",
    )
    max_nft_contracts: int = Field(default=20, ge=0, le=100)
    seed_contracts: list[str] = Field(
        default_factory=list,
        description="Extra exact ERC-20/ERC-721 contracts to inspect; useful for social-first leads",
    )
    max_candidates_to_enrich: int = Field(default=40, ge=1, le=100)
    max_ranked: int = Field(default=15, ge=1, le=50)
    max_valuation_usd: float = Field(
        default=1_000_000,
        gt=0,
        description="Preferred maximum circulating market cap, or FDV when market cap is unavailable",
    )
    exclude_above_max_valuation: bool = Field(default=True)
    min_liquidity_usd: float = Field(default=5_000, ge=0)
    preferred_liquidity_usd: float = Field(default=100_000, gt=0)
    min_watch_score: float = Field(default=40, ge=0, le=100)
    eligible_score: float = Field(default=68, ge=0, le=100)
    min_security_for_eligible: float = Field(default=20, ge=0, le=30)
    require_x_for_eligible: bool = Field(
        default=True,
        description="Require authenticated X evidence before assigning eligible",
    )
    enable_x_api: bool = Field(
        default=True,
        description="Use X_BEARER_TOKEN from the environment when available",
    )
    x_lookback_hours: int = Field(default=24, ge=1, le=168)
    x_max_candidates: int = Field(default=10, ge=0, le=25)
    report_title: str = Field(default="Robinhood Chain Launch Scout")

    @field_validator("seed_contracts", mode="before")
    @classmethod
    def coerce_contracts(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("report_title", mode="before")
    @classmethod
    def default_report_title(cls, value: Any) -> str:
        return "Robinhood Chain Launch Scout" if not value else str(value)


class FetchError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def number(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def integer(value: Any) -> int:
    parsed = number(value)
    return int(parsed) if parsed is not None else 0


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def log_score(value: float | None, target: float, maximum: float = 100.0) -> float:
    if value is None or value <= 0 or target <= 0:
        return 0.0
    return clamp(math.log10(1 + value) / math.log10(1 + target) * maximum, 0.0, maximum)


def lower_address(value: Any) -> str:
    return str(value or "").strip().lower()


def valid_address(value: Any) -> bool:
    return bool(ADDRESS_RE.fullmatch(str(value or "").strip()))


def money(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.2f}"


def valuation_value(candidate: dict[str, Any]) -> tuple[float | None, str]:
    market_cap = number(candidate.get("market_cap_usd"))
    if market_cap is not None:
        return market_cap, "circulating_market_cap"
    fdv = number(candidate.get("fdv_usd"))
    if fdv is not None:
        return fdv, "fdv_fallback"
    return None, "unknown"


def valuation_status(kind: str, market_cap: Any, fdv: Any, maximum: float) -> str:
    if kind == "nft":
        return "not_applicable"
    value = number(market_cap)
    if value is None:
        value = number(fdv)
    if value is None:
        return "unknown"
    return "under_target" if value <= maximum else "above_target"


def website_risk_flags(urls: list[str]) -> list[str]:
    flags: set[str] = set()
    for raw in urls:
        try:
            parsed = urlparse(str(raw).strip())
        except ValueError:
            flags.add("website_url_invalid")
            continue
        hostname = (parsed.hostname or "").lower().rstrip(".")
        text = str(raw).lower()
        if parsed.scheme != "https":
            flags.add("website_uses_http")
        if not hostname:
            flags.add("website_url_invalid")
        if hostname.startswith("xn--") or ".xn--" in hostname:
            flags.add("website_punycode")
        if hostname in SHORTENER_DOMAINS or any(
            hostname.endswith(f".{d}") for d in SHORTENER_DOMAINS
        ):
            flags.add("website_shortener")
        if parsed.username or parsed.password:
            flags.add("website_embedded_credentials")
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", hostname):
            flags.add("website_ip_literal")
        if any(
            term in text
            for term in (
                "connect",
                "wallet",
                "claim",
                "airdrop",
                "free-mint",
                "freemint",
            )
        ):
            flags.add("wallet_lure_in_url")
    return sorted(flags)


def blank_candidate(kind: str, address: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "address": address,
        "name": None,
        "symbol": None,
        "sources": set(),
        "pool_address": None,
        "pool_created_at": None,
        "market_cap_usd": None,
        "fdv_usd": None,
        "liquidity_usd": None,
        "volume_24h_usd": None,
        "transactions_24h": {},
        "websites": [],
        "socials": [],
        "contract": {},
        "holders": {},
        "x_metrics": {"available": False},
        "identity_collision_count": 1,
        "data_gaps": [],
        "risk_flags": [],
        "evidence": [],
    }


def merge_candidate(target: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    target["sources"] = set(target.get("sources") or set()) | set(
        incoming.get("sources") or set()
    )
    for key in ("websites", "socials", "data_gaps", "risk_flags", "evidence"):
        target[key] = list(
            dict.fromkeys([*(target.get(key) or []), *(incoming.get(key) or [])])
        )
    incoming_liquidity = number(incoming.get("liquidity_usd"))
    target_liquidity = number(target.get("liquidity_usd"))
    if incoming_liquidity is not None and (
        target_liquidity is None or incoming_liquidity > target_liquidity
    ):
        for key in (
            "pool_address",
            "pool_created_at",
            "market_cap_usd",
            "fdv_usd",
            "liquidity_usd",
            "volume_24h_usd",
            "transactions_24h",
        ):
            if incoming.get(key) is not None:
                target[key] = incoming[key]
    for key in (
        "name",
        "symbol",
        "market_cap_usd",
        "fdv_usd",
        "contract",
        "holders",
        "x_metrics",
    ):
        if not target.get(key) and incoming.get(key):
            target[key] = incoming[key]
    return target


def dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_address: dict[str, dict[str, Any]] = {}
    for item in candidates:
        address = lower_address(item.get("address"))
        if not valid_address(address):
            continue
        if address not in by_address:
            copied = dict(item)
            copied["address"] = address
            copied["sources"] = set(item.get("sources") or set())
            by_address[address] = copied
        else:
            merge_candidate(by_address[address], item)
    identities: dict[tuple[str, str], int] = {}
    for item in by_address.values():
        identity = (
            str(item.get("name") or "").lower(),
            str(item.get("symbol") or "").lower(),
        )
        if any(identity):
            identities[identity] = identities.get(identity, 0) + 1
    for item in by_address.values():
        identity = (
            str(item.get("name") or "").lower(),
            str(item.get("symbol") or "").lower(),
        )
        item["identity_collision_count"] = identities.get(identity, 1)
    return list(by_address.values())


async def fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
) -> Any:
    try:
        async with session.request(
            method, url, params=params, headers=headers, json=json_body
        ) as response:
            text = await response.text()
            if response.status >= 400:
                raise FetchError(f"HTTP {response.status} from {url}: {text[:160]}")
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise FetchError(f"Invalid JSON from {url}") from exc
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise FetchError(f"Request failed for {url}: {exc}") from exc


def gecko_candidate(
    pool: dict[str, Any], tokens: dict[str, dict[str, Any]], source_lens: str
) -> dict[str, Any] | None:
    attributes = pool.get("attributes") or {}
    relationships = pool.get("relationships") or {}
    base_id = str(
        (((relationships.get("base_token") or {}).get("data") or {}).get("id")) or ""
    )
    token = tokens.get(base_id) or {}
    token_attributes = token.get("attributes") or {}
    address = token_attributes.get("address")
    if not valid_address(address):
        return None
    tx = (attributes.get("transactions") or {}).get("h24") or {}
    value = blank_candidate("token", lower_address(address))
    value.update(
        {
            "name": token_attributes.get("name"),
            "symbol": token_attributes.get("symbol"),
            "sources": {"geckoterminal"},
            "pool_address": attributes.get("address"),
            "pool_created_at": attributes.get("pool_created_at"),
            "market_cap_usd": number(attributes.get("market_cap_usd")),
            "fdv_usd": number(attributes.get("fdv_usd")),
            "liquidity_usd": number(attributes.get("reserve_in_usd")),
            "volume_24h_usd": number((attributes.get("volume_usd") or {}).get("h24")),
            "transactions_24h": {
                "buys": integer(tx.get("buys")),
                "sells": integer(tx.get("sells")),
                "buyers": integer(tx.get("buyers")),
                "sellers": integer(tx.get("sellers")),
            },
            "evidence": [
                f"GeckoTerminal {source_lens}: https://www.geckoterminal.com/robinhood/pools/{attributes.get('address')}"
            ],
        }
    )
    return value


async def discover_gecko(
    session: aiohttp.ClientSession, config: Config
) -> tuple[list[dict[str, Any]], list[str]]:
    candidates: list[dict[str, Any]] = []
    gaps: list[str] = []
    lenses = ["new_pools"]
    if config.include_trending_pools:
        lenses.append("trending_pools")
    for lens in lenses:
        pages = config.new_pool_pages if lens == "new_pools" else 1
        for page in range(1, pages + 1):
            url = f"{config.gecko_api_base}/networks/{CHAIN_SLUG}/{lens}"
            try:
                payload = await fetch_json(
                    session,
                    url,
                    params={"page": page, "include": "base_token,quote_token"},
                )
            except FetchError as exc:
                gaps.append(f"geckoterminal_{lens}_page_{page}: {exc}")
                continue
            tokens = {
                item.get("id"): item
                for item in payload.get("included") or []
                if item.get("type") == "token"
            }
            for pool in payload.get("data") or []:
                item = gecko_candidate(pool, tokens, lens)
                if item:
                    candidates.append(item)
    return candidates, gaps


async def rpc_call(
    session: aiohttp.ClientSession, config: Config, method: str, params: list[Any]
) -> Any:
    payload = await fetch_json(
        session,
        config.rpc_url,
        method="POST",
        json_body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )
    if payload.get("error"):
        raise FetchError(f"RPC {method}: {payload['error']}")
    return payload.get("result")


async def discover_recent_nfts(
    session: aiohttp.ClientSession, config: Config
) -> tuple[list[dict[str, Any]], list[str]]:
    if not config.discover_nfts or config.max_nft_contracts == 0:
        return [], []
    try:
        latest_hex = await rpc_call(session, config, "eth_blockNumber", [])
        latest = int(latest_hex, 16)
        start = max(0, latest - config.nft_lookback_blocks)
        logs = await rpc_call(
            session,
            config,
            "eth_getLogs",
            [
                {
                    "fromBlock": hex(start),
                    "toBlock": hex(latest),
                    "topics": [TRANSFER_TOPIC],
                }
            ],
        )
    except (FetchError, TypeError, ValueError) as exc:
        return [], [f"nft_rpc_discovery: {exc}"]
    activity: dict[str, int] = {}
    for log in logs or []:
        topics = log.get("topics") or []
        address = lower_address(log.get("address"))
        if len(topics) == 4 and valid_address(address):
            activity[address] = activity.get(address, 0) + 1
    ranked = sorted(activity.items(), key=lambda item: item[1], reverse=True)[
        : config.max_nft_contracts
    ]
    results: list[dict[str, Any]] = []
    for address, transfers in ranked:
        item = blank_candidate("nft", address)
        item["sources"] = {"robinhood_rpc"}
        item["nft_transfer_events"] = transfers
        item["evidence"] = [
            f"Recent ERC-721-style Transfer logs: {config.explorer_base}/token/{address}"
        ]
        results.append(item)
    return results, []


def best_dex_pair(pairs: list[dict[str, Any]], address: str) -> dict[str, Any] | None:
    matches = [
        pair
        for pair in pairs
        if pair.get("chainId") == CHAIN_SLUG
        and lower_address((pair.get("baseToken") or {}).get("address"))
        == lower_address(address)
    ]
    if not matches:
        return None
    return max(
        matches,
        key=lambda pair: number((pair.get("liquidity") or {}).get("usd")) or 0.0,
    )


def enrich_from_dex(candidate: dict[str, Any], pairs: list[dict[str, Any]]) -> None:
    pair = best_dex_pair(pairs, candidate["address"])
    if not pair:
        candidate["data_gaps"].append("dexscreener_pair_missing")
        return
    info = pair.get("info") or {}
    websites = [
        str(item.get("url")) for item in info.get("websites") or [] if item.get("url")
    ]
    socials = [
        str(item.get("url")) for item in info.get("socials") or [] if item.get("url")
    ]
    tx = (pair.get("txns") or {}).get("h24") or {}
    incoming = blank_candidate(candidate["kind"], candidate["address"])
    incoming.update(
        {
            "name": (pair.get("baseToken") or {}).get("name"),
            "symbol": (pair.get("baseToken") or {}).get("symbol"),
            "sources": {"dexscreener"},
            "pool_address": pair.get("pairAddress"),
            "pool_created_at": pair.get("pairCreatedAt"),
            "market_cap_usd": number(pair.get("marketCap")),
            "fdv_usd": number(pair.get("fdv")),
            "liquidity_usd": number((pair.get("liquidity") or {}).get("usd")),
            "volume_24h_usd": number((pair.get("volume") or {}).get("h24")),
            "transactions_24h": {
                "buys": integer(tx.get("buys")),
                "sells": integer(tx.get("sells")),
                "buyers": integer(tx.get("buyers")),
                "sellers": integer(tx.get("sellers")),
            },
            "websites": websites,
            "socials": socials,
            "evidence": [str(pair.get("url"))] if pair.get("url") else [],
        }
    )
    merge_candidate(candidate, incoming)


def parse_holder_concentration(
    items: list[dict[str, Any]], total_supply: float | None
) -> dict[str, Any]:
    if not items or total_supply is None or total_supply <= 0:
        return {"top10_non_pool_pct": None, "pool_pct": None}
    non_pool: list[float] = []
    pool_value = 0.0
    for item in items:
        address = item.get("address") or {}
        holder = lower_address(address.get("hash"))
        value = number(item.get("value")) or 0.0
        if holder in BURN_ADDRESSES:
            continue
        name = str(address.get("name") or "").lower()
        if any(marker in name for marker in POOL_NAME_MARKERS):
            pool_value += value
            continue
        non_pool.append(value)
    return {
        "top10_non_pool_pct": sum(sorted(non_pool, reverse=True)[:10]) / total_supply,
        "pool_pct": pool_value / total_supply,
    }


async def enrich_candidate(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    candidate: dict[str, Any],
    config: Config,
) -> dict[str, Any]:
    address = candidate["address"]
    async with semaphore:
        dex_url = f"{config.dexscreener_api_base}/token-pairs/v1/{CHAIN_SLUG}/{address}"
        token_url = f"{config.blockscout_api_base}/tokens/{address}"
        address_url = f"{config.blockscout_api_base}/addresses/{address}"
        holder_url = f"{token_url}/holders"
        results = await asyncio.gather(
            fetch_json(session, dex_url),
            fetch_json(session, token_url),
            fetch_json(session, address_url),
            fetch_json(session, holder_url),
            return_exceptions=True,
        )
    dex, token, contract, holders = results
    if isinstance(dex, Exception):
        candidate["data_gaps"].append(f"dexscreener: {dex}")
    else:
        enrich_from_dex(candidate, dex if isinstance(dex, list) else [])
    if isinstance(token, BaseException):
        candidate["data_gaps"].append(f"blockscout_token: {token}")
    else:
        token_data = token if isinstance(token, dict) else {}
        candidate["sources"].add("blockscout")
        candidate["name"] = token_data.get("name") or candidate.get("name")
        candidate["symbol"] = token_data.get("symbol") or candidate.get("symbol")
        candidate["kind"] = (
            "nft"
            if token_data.get("type") in {"ERC-721", "ERC-1155"}
            else candidate["kind"]
        )
        candidate["token_type"] = token_data.get("type")
        candidate["market_cap_usd"] = number(
            token_data.get("circulating_market_cap")
        ) or candidate.get("market_cap_usd")
        candidate["holders_count"] = integer(token_data.get("holders_count"))
        candidate["total_supply_raw"] = number(token_data.get("total_supply"))
        candidate["evidence"].append(f"{config.explorer_base}/token/{address}")
    if isinstance(contract, BaseException):
        candidate["data_gaps"].append(f"blockscout_contract: {contract}")
    else:
        contract_data = contract if isinstance(contract, dict) else {}
        candidate["contract"] = {
            "is_verified": bool(contract_data.get("is_verified")),
            "is_scam": bool(contract_data.get("is_scam")),
            "reputation": contract_data.get("reputation"),
            "proxy_type": contract_data.get("proxy_type"),
            "name": contract_data.get("name"),
        }
    if isinstance(holders, BaseException):
        candidate["data_gaps"].append(f"blockscout_holders: {holders}")
    else:
        holder_data = holders if isinstance(holders, dict) else {}
        total_supply = number(candidate.get("total_supply_raw"))
        concentration = parse_holder_concentration(
            holder_data.get("items") or [], total_supply
        )
        candidate["holders"] = {
            "holders_count": candidate.get("holders_count", 0),
            **concentration,
        }
    return candidate


def x_handle(candidate: dict[str, Any]) -> str | None:
    for url in candidate.get("socials") or []:
        match = X_HANDLE_RE.search(str(url))
        if match and match.group(1).lower() not in {"i", "home", "search", "intent"}:
            return match.group(1)
    return None


def x_query(candidate: dict[str, Any]) -> str:
    terms: list[str] = []
    handle = x_handle(candidate)
    if handle:
        terms.append(f"from:{handle}")
        terms.append(f"@{handle}")
    name = str(candidate.get("name") or "").replace('"', "").strip()
    symbol = str(candidate.get("symbol") or "").replace('"', "").strip()
    if name:
        terms.append(f'"{name}"')
    if symbol and len(symbol) >= 2:
        terms.append(f'"${symbol}"')
    terms.append(candidate["address"])
    return f"({' OR '.join(terms[:5])}) (robinhood OR \"Robinhood Chain\") -is:retweet"


async def enrich_x(
    session: aiohttp.ClientSession,
    candidate: dict[str, Any],
    config: Config,
    bearer: str,
) -> None:
    params = {
        "query": x_query(candidate),
        "max_results": 100,
        "tweet.fields": "author_id,created_at,public_metrics",
        "expansions": "author_id",
        "user.fields": "public_metrics,verified",
    }
    try:
        payload = await fetch_json(
            session,
            "https://api.x.com/2/tweets/search/recent",
            params=params,
            headers={"Authorization": f"Bearer {bearer}"},
        )
    except FetchError as exc:
        candidate["data_gaps"].append(f"x_api: {exc}")
        return
    tweets = payload.get("data") or []
    users = {
        str(item.get("id")): item
        for item in (payload.get("includes") or {}).get("users") or []
    }
    now = datetime.now(timezone.utc)
    recent_mentions = 0
    engagements = 0
    authors: set[str] = set()
    max_followers = 0
    verified_authors = 0
    for tweet in tweets:
        authors.add(str(tweet.get("author_id")))
        metrics = tweet.get("public_metrics") or {}
        engagements += sum(
            integer(metrics.get(key))
            for key in ("like_count", "retweet_count", "reply_count", "quote_count")
        )
        created = tweet.get("created_at")
        if created:
            try:
                age_hours = (
                    now - datetime.fromisoformat(created.replace("Z", "+00:00"))
                ).total_seconds() / 3600
                if age_hours <= max(1, config.x_lookback_hours / 4):
                    recent_mentions += 1
            except ValueError:
                pass
    for author in authors:
        user = users.get(author) or {}
        max_followers = max(
            max_followers,
            integer((user.get("public_metrics") or {}).get("followers_count")),
        )
        verified_authors += int(bool(user.get("verified")))
    candidate["sources"].add("x_api")
    candidate["x_metrics"] = {
        "available": True,
        "mentions": len(tweets),
        "unique_authors": len(authors),
        "engagements": engagements,
        "recent_mentions": recent_mentions,
        "older_mentions": max(0, len(tweets) - recent_mentions),
        "max_author_followers": max_followers,
        "verified_authors": verified_authors,
        "query": params["query"],
    }


def score_virality(candidate: dict[str, Any]) -> tuple[float, list[str]]:
    metrics = candidate.get("x_metrics") or {}
    reasons: list[str] = []
    social_baseline = min(4.0, len(candidate.get("socials") or []) * 2.0)
    if not metrics.get("available"):
        reasons.append(
            "X API evidence unavailable; score is limited to linked social metadata"
        )
        return round(social_baseline, 2), reasons
    mentions = integer(metrics.get("mentions"))
    authors = integer(metrics.get("unique_authors"))
    engagements = integer(metrics.get("engagements"))
    recent = integer(metrics.get("recent_mentions"))
    older = integer(metrics.get("older_mentions"))
    max_followers = integer(metrics.get("max_author_followers"))
    score = social_baseline
    score += log_score(mentions, 100, 7)
    score += log_score(engagements, 5_000, 7)
    score += log_score(authors, 75, 5)
    score += log_score(max_followers, 100_000, 3)
    if recent > older / 3 and mentions >= 8:
        score += 4
        reasons.append("Recent mention rate is accelerating")
    if mentions and authors / mentions < 0.2:
        score -= 4
        reasons.append(
            "Low author diversity may indicate coordinated or repetitive promotion"
        )
    reasons.append(
        f"X observed {mentions} posts, {authors} authors, and {engagements} engagements"
    )
    return round(clamp(score, 0, 30), 2), reasons


def score_credibility(candidate: dict[str, Any]) -> tuple[float, list[str]]:
    contract = candidate.get("contract") or {}
    score = 0.0
    reasons: list[str] = []
    if contract.get("is_verified"):
        score += 8
        reasons.append("Explorer reports verified contract source")
    else:
        reasons.append("Contract source is not verified")
    if contract.get("reputation") == "ok" and not contract.get("is_scam"):
        score += 2
    if candidate.get("websites"):
        score += 2
    if candidate.get("socials"):
        score += 2
    if candidate.get("name") and candidate.get("symbol"):
        score += 2
    source_count = len(candidate.get("sources") or set())
    score += min(4.0, max(0, source_count - 1) * 2.0)
    collisions = integer(candidate.get("identity_collision_count"))
    if collisions > 1:
        score -= min(6.0, (collisions - 1) * 2.0)
        reasons.append(f"Name/ticker identity collides across {collisions} contracts")
    reasons.append(f"Evidence merged from {source_count} source classes")
    return round(clamp(score, 0, 20), 2), reasons


def score_market(candidate: dict[str, Any], config: Config) -> tuple[float, list[str]]:
    if candidate.get("kind") == "nft":
        holders = integer((candidate.get("holders") or {}).get("holders_count"))
        transfers = integer(candidate.get("nft_transfer_events"))
        score = log_score(holders, 2_000, 8) + log_score(transfers, 1_000, 5)
        return round(clamp(score, 0, 20), 2), [
            "NFT market cap is not comparable to ERC-20 valuation",
            f"Observed {holders} holders and {transfers} recent ERC-721-style transfers",
        ]
    valuation, basis = valuation_value(candidate)
    liquidity = number(candidate.get("liquidity_usd"))
    volume = number(candidate.get("volume_24h_usd"))
    tx = candidate.get("transactions_24h") or {}
    unique_traders = integer(tx.get("buyers")) + integer(tx.get("sellers"))
    score = 0.0
    reasons = [f"Valuation basis: {basis} ({money(valuation)})"]
    if valuation is not None and valuation <= config.max_valuation_usd:
        score += 5
    score += log_score(liquidity, config.preferred_liquidity_usd, 6)
    turnover = (
        volume / valuation
        if volume is not None and valuation and valuation > 0
        else None
    )
    if turnover is not None:
        score += clamp(turnover / 0.5 * 5, 0, 5)
        reasons.append(f"24h volume/valuation turnover: {turnover:.2f}x")
    score += log_score(unique_traders, 300, 4)
    return round(clamp(score, 0, 20), 2), reasons


def score_security(
    candidate: dict[str, Any], config: Config
) -> tuple[float, list[str], list[str], list[str]]:
    score = 30.0
    contract = candidate.get("contract") or {}
    holders = candidate.get("holders") or {}
    risk_flags = list(candidate.get("risk_flags") or [])
    manual: list[str] = []
    reasons: list[str] = []
    web_flags = website_risk_flags(candidate.get("websites") or [])
    risk_flags.extend(web_flags)
    if contract.get("is_scam") or contract.get("reputation") == "scam":
        risk_flags.append("explorer_scam_flag")
        score = 0
    if not contract.get("is_verified"):
        score -= 8
        manual.append("verify_contract_source_and_privileged_functions")
    if contract.get("proxy_type"):
        score -= 3
        manual.append("verify_proxy_implementation_and_upgrade_admin")
    top10 = number(holders.get("top10_non_pool_pct"))
    if top10 is None:
        score -= 5
        manual.append("verify_holder_concentration_and_insider_wallets")
    elif top10 > 0.80:
        score -= 12
        risk_flags.append("extreme_holder_concentration")
    elif top10 > 0.50:
        score -= 7
        risk_flags.append("high_holder_concentration")
    elif top10 > 0.30:
        score -= 3
        risk_flags.append("elevated_holder_concentration")
    liquidity = number(candidate.get("liquidity_usd"))
    if candidate.get("kind") == "token":
        if liquidity is None:
            score -= 5
            manual.append("verify_liquidity_depth_lock_and_sellability")
        elif liquidity < config.min_liquidity_usd:
            score -= 8
            risk_flags.append("liquidity_below_minimum")
        elif liquidity < config.min_liquidity_usd * 3:
            score -= 4
            risk_flags.append("thin_liquidity")
        manual.append("verify_honeypot_tax_blacklist_mint_and_pause_controls")
    else:
        manual.append("verify_nft_floor_volume_and_mint_contract")
        manual.append("verify_mint_site_without_connecting_primary_wallet")
    severe_web = {
        "website_punycode",
        "website_embedded_credentials",
        "website_ip_literal",
    }
    if severe_web.intersection(web_flags):
        score -= 15
    if "website_shortener" in web_flags:
        score -= 8
    if "wallet_lure_in_url" in web_flags:
        score -= 5
    if not candidate.get("websites"):
        manual.append("resolve_canonical_website_from_official_social_and_contract")
    if not candidate.get("socials"):
        manual.append("resolve_canonical_social_account")
    if integer(candidate.get("identity_collision_count")) > 1:
        risk_flags.append("name_ticker_collision")
        manual.append("resolve_name_ticker_collision")
    if not (candidate.get("x_metrics") or {}).get("available"):
        manual.append("verify_x_attention_manually")
    risk_flags = sorted(set(risk_flags))
    manual = list(dict.fromkeys(manual))
    if top10 is not None:
        reasons.append(f"Top 10 non-pool holders: {top10:.1%}")
    reasons.append("Candidate websites were not fetched or executed")
    reasons.append(
        "Automated honeypot/drainer clearance is unavailable on chain ID 4663; manual checks remain mandatory"
    )
    return round(clamp(score, 0, 30), 2), reasons, risk_flags, manual


def score_candidate(candidate: dict[str, Any], config: Config) -> dict[str, Any]:
    virality, virality_reasons = score_virality(candidate)
    credibility, credibility_reasons = score_credibility(candidate)
    market, market_reasons = score_market(candidate, config)
    security, security_reasons, risk_flags, manual_checks = score_security(
        candidate, config
    )
    status = valuation_status(
        str(candidate.get("kind")),
        candidate.get("market_cap_usd"),
        candidate.get("fdv_usd"),
        config.max_valuation_usd,
    )
    if status == "above_target":
        risk_flags.append("valuation_above_target")
    if status == "unknown" and candidate.get("kind") == "token":
        manual_checks.append("verify_circulating_supply_market_cap_and_fdv")
    total = round(virality + credibility + market + security, 2)
    blocked = "explorer_scam_flag" in risk_flags or bool(
        {
            "website_punycode",
            "website_embedded_credentials",
            "website_ip_literal",
        }.intersection(risk_flags)
    )
    if blocked:
        decision = "blocked"
    elif status == "above_target" and config.exclude_above_max_valuation:
        decision = "excluded"
    elif total < config.min_watch_score:
        decision = "watch"
    elif (
        total >= config.eligible_score
        and security >= config.min_security_for_eligible
        and not (
            config.require_x_for_eligible
            and "verify_x_attention_manually" in manual_checks
        )
        and not any(
            check in manual_checks
            for check in (
                "verify_contract_source_and_privileged_functions",
                "resolve_name_ticker_collision",
            )
        )
        and candidate.get("kind") == "token"
    ):
        decision = "eligible"
    elif manual_checks:
        decision = "manual_verification_required"
    else:
        decision = "watch"
    valuation, valuation_basis = valuation_value(candidate)
    result = dict(candidate)
    result.update(
        {
            "scores": {
                "virality": virality,
                "credibility": credibility,
                "market": market,
                "security": security,
            },
            "total_score": total,
            "valuation_status": status,
            "valuation_usd": valuation,
            "valuation_basis": (
                "not_applicable" if candidate.get("kind") == "nft" else valuation_basis
            ),
            "decision": decision,
            "risk_flags": sorted(set(risk_flags)),
            "manual_checks": list(dict.fromkeys(manual_checks)),
            "reasoning": {
                "virality": virality_reasons,
                "credibility": credibility_reasons,
                "market": market_reasons,
                "security": security_reasons,
            },
        }
    )
    return result


def discovery_priority(
    candidate: dict[str, Any], config: Config
) -> tuple[int, float, float, int]:
    """Spend bounded enrichment calls on under-target and unknown launches first."""
    status = valuation_status(
        str(candidate.get("kind")),
        candidate.get("market_cap_usd"),
        candidate.get("fdv_usd"),
        config.max_valuation_usd,
    )
    status_priority = {
        "under_target": 3,
        "not_applicable": 2,
        "unknown": 2,
        "above_target": 1,
    }.get(status, 0)
    return (
        status_priority,
        number(candidate.get("liquidity_usd")) or 0,
        number(candidate.get("volume_24h_usd")) or 0,
        integer(candidate.get("nft_transfer_events")),
    )


def serializable(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, dict):
        return {key: serializable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serializable(item) for item in value]
    return value


def ranking_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rank, item in enumerate(items, 1):
        scores = item["scores"]
        rows.append(
            {
                "Rank": rank,
                "Project": f"{item.get('name') or 'Unknown'} ({item.get('symbol') or 'n/a'})",
                "Type": item.get("kind"),
                "Contract": item.get("address"),
                "Decision": item.get("decision"),
                "Total": item.get("total_score"),
                "Virality /30": scores["virality"],
                "Credibility /20": scores["credibility"],
                "Market /20": scores["market"],
                "Security /30": scores["security"],
                "Valuation": money(item.get("valuation_usd")),
                "Basis": item.get("valuation_basis"),
                "Liquidity": money(number(item.get("liquidity_usd"))),
                "24h Volume": money(number(item.get("volume_24h_usd"))),
                "Flags": ", ".join(item.get("risk_flags") or []) or "none",
            }
        )
    return rows


def format_text(payload: dict[str, Any]) -> str:
    ranked = payload["ranked"]
    lines = [
        "Robinhood Chain Launch Scout",
        f"Observed: {payload['observed_at']}",
        f"Coverage: {payload['coverage_summary']}",
        f"Candidates: {payload['candidate_count']} discovered; {len(ranked)} shown",
        "",
        "RANKING",
    ]
    if not ranked:
        lines.append("No candidates survived discovery and ranking.")
    for index, item in enumerate(ranked, 1):
        lines.append(
            f"{index}. {item.get('name') or 'Unknown'} ({item.get('symbol') or 'n/a'}) "
            f"[{item.get('kind')}] — {item['total_score']}/100 — {item['decision']}"
        )
        lines.append(
            f"   {item['address']} | valuation {money(item.get('valuation_usd'))} "
            f"({item.get('valuation_basis')}) | liquidity {money(number(item.get('liquidity_usd')))}"
        )
        lines.append(
            "   scores: "
            + ", ".join(f"{key} {value}" for key, value in item["scores"].items())
        )
        if item.get("risk_flags"):
            lines.append(f"   flags: {', '.join(item['risk_flags'])}")
        if item.get("manual_checks"):
            lines.append(f"   verify: {', '.join(item['manual_checks'][:5])}")
    if payload["data_gaps"]:
        lines.extend(
            ["", "DATA GAPS", *[f"- {gap}" for gap in payload["data_gaps"][:10]]]
        )
    lines.extend(
        [
            "",
            "Security boundary: candidate websites were not opened or executed; no wallet, mint, approval, trade, or contract write occurred.",
            "Scores are research triage, not financial advice or proof of safety.",
        ]
    )
    return "\n".join(lines)


async def save_report(payload: dict[str, Any], config: Config) -> None:
    try:
        from condor.reports import ReportBuilder

        ranked = payload["ranked"]
        builder = ReportBuilder(config.report_title)
        builder.source("routine", "robinhood_launch_scan").tags(
            ["robinhood-chain", "launches", "memecoin", "nft", "security"]
        ).manual_order()
        builder.kpi("Candidates", str(payload["candidate_count"]))
        builder.kpi("Ranked", str(len(ranked)))
        builder.kpi(
            "Eligible", str(sum(item["decision"] == "eligible" for item in ranked))
        )
        builder.kpi(
            "Manual Review",
            str(
                sum(
                    item["decision"] == "manual_verification_required"
                    for item in ranked
                )
            ),
        )
        builder.markdown(
            "### Scope and safety boundary\n"
            f"Observed at `{payload['observed_at']}`. {payload['coverage_summary']}\n\n"
            "The routine queried read-only market, explorer, RPC, and optional X endpoints. "
            "It did **not** fetch candidate websites, execute project JavaScript, connect a wallet, sign, mint, approve, trade, or call contract write methods. "
            "A high score is triage evidence, not proof of safety or financial advice."
        )
        tokens = [item for item in ranked if item.get("kind") == "token"]
        nfts = [item for item in ranked if item.get("kind") == "nft"]
        if tokens:
            builder.markdown("### ERC-20 candidates")
            builder.table(ranking_rows(tokens))
        if nfts:
            builder.markdown("### NFT candidates")
            builder.table(ranking_rows(nfts))
        manual = [item for item in ranked if item.get("manual_checks")]
        if manual:
            rows = [
                {
                    "Project": item.get("name") or "Unknown",
                    "Contract": item.get("address"),
                    "Decision": item.get("decision"),
                    "Checks required": ", ".join(item.get("manual_checks") or []),
                }
                for item in manual
            ]
            builder.markdown("### Manual verification queue")
            builder.table(rows)
        excluded = payload.get("excluded") or []
        if excluded:
            builder.markdown("### Blocked or excluded")
            builder.table(ranking_rows(excluded[:20]))
        if payload["data_gaps"]:
            builder.markdown(
                "### Data gaps\n"
                + "\n".join(f"- {gap}" for gap in payload["data_gaps"][:30])
            )
        builder.markdown(
            "### Score interpretation\n"
            "- Virality: 30 points from linked social metadata and, when configured, recent X mentions, engagement, author diversity, and acceleration.\n"
            "- Credibility: 20 points from contract verification, canonical links, identity completeness, and independent source coverage.\n"
            "- Market: 20 points from valuation fit, liquidity, turnover, traders, or NFT holder/transfer activity.\n"
            "- Security: 30 points minus explorer, verification, concentration, liquidity, URL, collision, and data-gap penalties.\n"
            "- Default token focus: circulating market cap, or FDV fallback, at or below $1 million. NFT valuation is reported as not comparable."
        )
        builder.markdown(
            "### Debug JSON Payload\n```json\n"
            + json.dumps(serializable(payload), indent=2, sort_keys=True)
            + "\n```"
        )
        await builder.save()
    except Exception as exc:
        logger.warning("Robinhood launch report generation failed: %s", exc)


async def run(config: Config, context: Any) -> RoutineResult:
    del context
    observed_at = utc_now()
    timeout = aiohttp.ClientTimeout(total=config.request_timeout_seconds)
    headers = {
        "User-Agent": "CondorRobinhoodLaunchScout/1.0 (read-only research routine)"
    }
    data_gaps: list[str] = []
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        (gecko, gecko_gaps), (nfts, nft_gaps) = await asyncio.gather(
            discover_gecko(session, config),
            discover_recent_nfts(session, config),
        )
        data_gaps.extend(gecko_gaps)
        data_gaps.extend(nft_gaps)
        seeds = []
        for address in config.seed_contracts:
            if valid_address(address):
                seed = blank_candidate("token", lower_address(address))
                seed["sources"] = {"manual_seed"}
                seed["evidence"] = [
                    f"Operator seed: {config.explorer_base}/address/{address}"
                ]
                seeds.append(seed)
            else:
                data_gaps.append(f"invalid_seed_contract: {address}")
        candidates = dedupe_candidates([*gecko, *nfts, *seeds])
        candidates.sort(key=lambda item: discovery_priority(item, config), reverse=True)
        candidates = candidates[: config.max_candidates_to_enrich]
        semaphore = asyncio.Semaphore(config.max_concurrency)
        enriched = await asyncio.gather(
            *(enrich_candidate(session, semaphore, item, config) for item in candidates)
        )
        enriched = dedupe_candidates(list(enriched))
        bearer = (
            os.environ.get("X_BEARER_TOKEN", "").strip() if config.enable_x_api else ""
        )
        if bearer and config.x_max_candidates:
            x_targets = sorted(
                enriched,
                key=lambda item: (
                    number(item.get("volume_24h_usd")) or 0,
                    number(item.get("liquidity_usd")) or 0,
                ),
                reverse=True,
            )[: config.x_max_candidates]
            for item in x_targets:
                await enrich_x(session, item, config, bearer)
        else:
            data_gaps.append(
                "x_api_unavailable: set X_BEARER_TOKEN in the Condor runtime environment for authenticated attention scoring"
            )
            for item in enriched:
                item["data_gaps"].append("x_api_unavailable")
    scored = [score_candidate(item, config) for item in enriched]
    scored.sort(key=lambda item: item["total_score"], reverse=True)
    ranked = [
        item for item in scored if item["decision"] not in {"blocked", "excluded"}
    ][: config.max_ranked]
    excluded = [item for item in scored if item["decision"] in {"blocked", "excluded"}]
    source_names = sorted(
        {source for item in scored for source in item.get("sources") or set()}
    )
    coverage = ", ".join(source_names) or "no successful source"
    coverage_summary = (
        f"Sources observed: {coverage}. X attention: "
        + (
            "authenticated"
            if any((item.get("x_metrics") or {}).get("available") for item in scored)
            else "manual-verification fallback"
        )
        + ". NFT discovery covers recent ERC-721-style Transfer activity; ERC-1155 and pre-mint social leads require seed contracts."
    )
    payload = {
        "observed_at": observed_at,
        "chain": {"name": "Robinhood Chain", "chain_id": CHAIN_ID, "slug": CHAIN_SLUG},
        "candidate_count": len(scored),
        "ranked": ranked,
        "excluded": excluded,
        "coverage_summary": coverage_summary,
        "data_gaps": list(dict.fromkeys(data_gaps)),
        "config_summary": {
            "max_valuation_usd": config.max_valuation_usd,
            "exclude_above_max_valuation": config.exclude_above_max_valuation,
            "min_liquidity_usd": config.min_liquidity_usd,
            "new_pool_pages": config.new_pool_pages,
            "nft_lookback_blocks": config.nft_lookback_blocks,
            "require_x_for_eligible": config.require_x_for_eligible,
            "candidate_websites_fetched": False,
        },
    }
    await save_report(payload, config)
    rows = ranking_rows(ranked)
    return RoutineResult(
        text=format_text(payload),
        table_data=rows,
        table_columns=list(rows[0]) if rows else None,
        sections=[
            {"title": "Coverage", "content": coverage_summary},
            {
                "title": "Security Boundary",
                "content": "No candidate website fetch, wallet connection, signature, mint, approval, trade, or contract write occurred.",
            },
        ],
    )
