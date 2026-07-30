"""Fixed public provider manifest for Market Reporter source adapters."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

MANIFEST_VERSION = "2026-07-31.1"

PROVIDERS: dict[str, dict[str, Any]] = {
    "gdelt": {
        "family": "news",
        "hosts": ["api.gdeltproject.org"],
        "docs": "https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/",
        "auth": "keyless",
        "timeout": 10,
        "max_bytes": 1_000_000,
        "attribution": "GDELT Project",
        "stability": "documented",
    },
    "coindesk_rss": {
        "family": "news",
        "hosts": ["www.coindesk.com"],
        "docs": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "auth": "keyless",
        "timeout": 8,
        "max_bytes": 500_000,
        "attribution": "CoinDesk RSS",
        "stability": "documented",
    },
    "decrypt_rss": {
        "family": "news",
        "hosts": ["decrypt.co"],
        "docs": "https://decrypt.co/feed",
        "auth": "keyless",
        "timeout": 8,
        "max_bytes": 500_000,
        "attribution": "Decrypt RSS",
        "stability": "documented",
    },
    "cointelegraph_rss": {
        "family": "news",
        "hosts": ["cointelegraph.com"],
        "docs": "https://cointelegraph.com/rss",
        "auth": "keyless",
        "timeout": 8,
        "max_bytes": 500_000,
        "attribution": "Cointelegraph RSS",
        "stability": "documented",
    },
    "federal_reserve": {
        "family": "official",
        "hosts": ["www.federalreserve.gov"],
        "docs": "https://www.federalreserve.gov/feeds/feeds.htm",
        "auth": "keyless",
        "timeout": 8,
        "max_bytes": 750_000,
        "attribution": "Board of Governors of the Federal Reserve System",
        "stability": "documented",
    },
    "bls": {
        "family": "official",
        "hosts": ["www.bls.gov"],
        "docs": "https://www.bls.gov/schedule/",
        "auth": "keyless",
        "timeout": 8,
        "max_bytes": 750_000,
        "attribution": "U.S. Bureau of Labor Statistics",
        "stability": "documented",
    },
    "bea": {
        "family": "official",
        "hosts": ["www.bea.gov"],
        "docs": "https://www.bea.gov/news/schedule",
        "auth": "keyless",
        "timeout": 8,
        "max_bytes": 750_000,
        "attribution": "U.S. Bureau of Economic Analysis",
        "stability": "documented",
    },
    "cftc": {
        "family": "official",
        "hosts": ["www.cftc.gov", "publicreporting.cftc.gov"],
        "docs": "https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm",
        "auth": "keyless",
        "timeout": 10,
        "max_bytes": 1_000_000,
        "attribution": "U.S. Commodity Futures Trading Commission",
        "stability": "documented",
    },
    "sec": {
        "family": "official",
        "hosts": ["data.sec.gov", "www.sec.gov"],
        "docs": "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
        "auth": "keyless",
        "timeout": 10,
        "max_bytes": 5_000_000,
        "attribution": "U.S. Securities and Exchange Commission",
        "stability": "documented",
    },
    "bluesky": {
        "family": "social",
        "hosts": ["public.api.bsky.app"],
        "docs": "https://docs.bsky.app/docs/api/app-bsky-feed-search-posts",
        "auth": "keyless",
        "timeout": 8,
        "max_bytes": 1_000_000,
        "attribution": "Bluesky public AppView",
        "stability": "documented",
    },
    "mastodon": {
        "family": "social",
        "hosts": ["mastodon.social"],
        "docs": "https://docs.joinmastodon.org/methods/timelines/",
        "auth": "keyless",
        "timeout": 8,
        "max_bytes": 1_000_000,
        "attribution": "mastodon.social public timeline",
        "stability": "documented",
    },
    "binance_spot": {
        "family": "market",
        "hosts": ["api.binance.com"],
        "docs": "https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints",
        "auth": "keyless",
        "timeout": 8,
        "max_bytes": 2_000_000,
        "attribution": "Binance public market data",
        "stability": "documented",
    },
    "binance_futures": {
        "family": "derivatives",
        "hosts": ["fapi.binance.com"],
        "docs": "https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api",
        "auth": "keyless",
        "timeout": 8,
        "max_bytes": 1_000_000,
        "attribution": "Binance Futures public market data",
        "stability": "documented",
    },
    "kraken": {
        "family": "market",
        "hosts": ["api.kraken.com"],
        "docs": "https://docs.kraken.com/api/docs/rest-api/get-ohlc-data",
        "auth": "keyless",
        "timeout": 8,
        "max_bytes": 1_000_000,
        "attribution": "Kraken public market data",
        "stability": "documented",
    },
    "alternative_fng": {
        "family": "sentiment",
        "hosts": ["api.alternative.me"],
        "docs": "https://alternative.me/crypto/fear-and-greed-index/",
        "auth": "keyless",
        "timeout": 8,
        "max_bytes": 250_000,
        "attribution": "Alternative.me Crypto Fear & Greed Index",
        "stability": "documented",
    },
    "defillama": {
        "family": "liquidity",
        "hosts": ["api.llama.fi", "stablecoins.llama.fi"],
        "docs": "https://defillama.com/docs/api",
        "auth": "keyless",
        "timeout": 10,
        "max_bytes": 2_000_000,
        "attribution": "DefiLlama",
        "stability": "documented",
    },
    "stooq": {
        "family": "market",
        "hosts": ["stooq.com"],
        "docs": "https://stooq.com/q/d/l/",
        "auth": "keyless",
        "timeout": 10,
        "max_bytes": 2_000_000,
        "attribution": "Stooq",
        "stability": "experimental",
    },
    "fred": {
        "family": "macro",
        "hosts": ["api.stlouisfed.org"],
        "docs": "https://fred.stlouisfed.org/docs/api/fred/",
        "auth": "optional_free_key",
        "env": "FRED_API_KEY",
        "timeout": 10,
        "max_bytes": 1_000_000,
        "attribution": "Federal Reserve Bank of St. Louis FRED",
        "stability": "documented",
    },
    "treasury": {
        "family": "macro",
        "hosts": ["home.treasury.gov"],
        "docs": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates",
        "auth": "keyless",
        "timeout": 10,
        "max_bytes": 1_000_000,
        "attribution": "U.S. Department of the Treasury",
        "stability": "documented",
    },
    "geckoterminal": {
        "family": "token_discovery",
        "hosts": ["api.geckoterminal.com"],
        "docs": "https://docs.coingecko.com/docs/keyless-public-api",
        "auth": "keyless",
        "timeout": 10,
        "max_bytes": 2_000_000,
        "attribution": "GeckoTerminal",
        "stability": "documented",
    },
    "dexscreener": {
        "family": "token_market",
        "hosts": ["api.dexscreener.com"],
        "docs": "https://docs.dexscreener.com/api/reference",
        "auth": "keyless",
        "timeout": 10,
        "max_bytes": 2_000_000,
        "attribution": "DEX Screener",
        "stability": "documented",
    },
    "solana_rpc": {
        "family": "identity",
        "hosts": ["api.mainnet-beta.solana.com"],
        "docs": "https://solana.com/docs/rpc",
        "auth": "keyless",
        "timeout": 10,
        "max_bytes": 1_000_000,
        "attribution": "Solana public RPC",
        "stability": "documented",
    },
    "robinhood_registry": {
        "family": "identity",
        "hosts": ["api.robinhood.com"],
        "docs": "https://docs.robinhood.com/chain/stock-token-apis/",
        "auth": "keyless",
        "timeout": 10,
        "max_bytes": 2_000_000,
        "attribution": "Robinhood Chain",
        "stability": "documented",
    },
    "robinhood_rpc": {
        "family": "identity",
        "hosts": ["rpc.mainnet.chain.robinhood.com"],
        "docs": "https://docs.robinhood.com/chain/connecting/",
        "auth": "keyless",
        "timeout": 10,
        "max_bytes": 500_000,
        "attribution": "Robinhood Chain public RPC",
        "stability": "documented",
    },
    "robinhood_blockscout": {
        "family": "identity",
        "hosts": ["robinhoodchain.blockscout.com"],
        "docs": "https://docs.blockscout.com/devs/apis/rest",
        "auth": "keyless",
        "timeout": 10,
        "max_bytes": 500_000,
        "attribution": "Robinhood Chain Blockscout",
        "stability": "documented",
    },
}

_RATE_LIMITS = {
    "geckoterminal": "10 requests/minute public allowance",
    "dexscreener": "60 or 300 requests/minute by endpoint",
    "robinhood_registry": "60 requests/second documented limit",
}
_FRESHNESS = {
    "news": "current collection window",
    "official": "provider publication cadence",
    "social": "current collection window",
    "market": "last completed daily observation",
    "derivatives": "current public snapshot",
    "sentiment": "daily",
    "liquidity": "provider publication cadence",
    "macro": "daily or series cadence",
    "token_discovery": "current public pool feed",
    "token_market": "current public pair snapshot",
    "identity": "current registry or RPC response",
}
_FALLBACKS = {
    "binance_spot": ["kraken"],
}

# Keep the compact keys used by adapters while exposing every field required by
# the versioned public provider contract.
for _provider_id, _provider in PROVIDERS.items():
    _provider.update(
        {
            "provider_id": _provider_id,
            "source_family": _provider["family"],
            "fixed_hosts": list(_provider["hosts"]),
            "documentation_url": _provider["docs"],
            "terms_or_attribution_url": _provider["docs"],
            "auth_mode": _provider["auth"],
            "named_secret_environment_variable": _provider.get("env"),
            "rate_limit": _RATE_LIMITS.get(
                _provider_id, "provider policy; client calls remain bounded"
            ),
            "expected_freshness": _FRESHNESS.get(
                _provider["family"], "provider publication cadence"
            ),
            "request_timeout_sec": _provider["timeout"],
            "maximum_response_bytes": _provider["max_bytes"],
            "fallback_provider_ids": _FALLBACKS.get(_provider_id, []),
            "report_attribution": _provider["attribution"],
        }
    )


def get_provider(provider_id: str) -> dict[str, Any]:
    """Return a defensive copy of one declared provider."""
    if provider_id not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider_id}")
    return deepcopy(PROVIDERS[provider_id])


def validate_provider_url(provider_id: str, url: str) -> str:
    """Validate a fixed HTTPS host and return the canonical URL."""
    provider = get_provider(provider_id)
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("Provider URL must be credential-free HTTPS")
    host = (parsed.hostname or "").lower()
    if host not in provider["hosts"]:
        raise ValueError(f"Host is not allowed for {provider_id}")
    if parsed.fragment:
        raise ValueError("Provider URL fragments are not allowed")
    return url


def public_manifest(provider_ids: list[str]) -> dict[str, Any]:
    """Return report-safe provider contract rows."""
    providers = {}
    for provider_id in provider_ids:
        value = get_provider(provider_id)
        providers[provider_id] = {
            key: value[key]
            for key in (
                "provider_id",
                "source_family",
                "documentation_url",
                "terms_or_attribution_url",
                "auth_mode",
                "named_secret_environment_variable",
                "rate_limit",
                "expected_freshness",
                "fallback_provider_ids",
                "report_attribution",
                "stability",
            )
        }
    return {"version": MANIFEST_VERSION, "providers": providers}
