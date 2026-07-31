"""Versioned canonical asset identities and bounded universes."""

from __future__ import annotations

from datetime import date
from typing import Any

REGISTRY_VERSION = "2026-07-31.2"
REGISTRY_RETRIEVED_AT = "2026-07-31"
REGISTRIES = {
    "liquid_crypto_pairs": {
        "source_url": "https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints",
        "retrieved_at": REGISTRY_RETRIEVED_AT,
        "maximum_accepted_age_days": 90,
    },
    "tradfi_identifiers": {
        "source_url": "https://www.sec.gov/files/company_tickers.json",
        "retrieved_at": REGISTRY_RETRIEVED_AT,
        "maximum_accepted_age_days": 90,
    },
    "sp500_sample": {
        "source_url": "https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-500-etf-trust-spy",
        "retrieved_at": REGISTRY_RETRIEVED_AT,
        "maximum_accepted_age_days": 30,
    },
    "established_memecoins": {
        "source_url": "https://api.coingecko.com/api/v3/coins/list?include_platform=true",
        "retrieved_at": REGISTRY_RETRIEVED_AT,
        "maximum_accepted_age_days": 90,
    },
    "approved_quotes": {
        "source_url": "https://docs.robinhood.com/chain/contracts/",
        "retrieved_at": REGISTRY_RETRIEVED_AT,
        "maximum_accepted_age_days": 30,
    },
}

CRYPTO_UNIVERSE = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "BNB": "BNBUSDT",
    "XRP": "XRPUSDT",
    "ADA": "ADAUSDT",
    "DOGE": "DOGEUSDT",
    "AVAX": "AVAXUSDT",
    "LINK": "LINKUSDT",
    "SUI": "SUIUSDT",
}

TRADFI_BENCHMARKS = ["SPY", "QQQ", "DIA", "IWM"]
TRADFI_SECTORS = [
    "XLC",
    "XLY",
    "XLP",
    "XLE",
    "XLF",
    "XLV",
    "XLI",
    "XLB",
    "XLRE",
    "XLK",
    "XLU",
]
TRADFI_PROXIES = ["TLT", "HYG", "GLD", "USO", "UUP"]
TRADFI_SP500_STOCKS = [
    "NVDA",
    "AAPL",
    "MSFT",
    "AMZN",
    "GOOGL",
    "AVGO",
    "META",
    "TSLA",
    "MU",
    "JPM",
    "XOM",
    "LLY",
]
TRADFI_SP500_NAMES = {
    "NVDA": "NVIDIA",
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "AMZN": "Amazon",
    "GOOGL": "Alphabet",
    "AVGO": "Broadcom",
    "META": "Meta",
    "TSLA": "Tesla",
    "MU": "Micron",
    "JPM": "JPMorgan",
    "XOM": "Exxon Mobil",
    "LLY": "Eli Lilly",
}

TICKER_TO_CIK = {
    "NVDA": "0001045810",
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "AMZN": "0001018724",
    "GOOGL": "0001652044",
    "AVGO": "0001730168",
    "META": "0001326801",
    "TSLA": "0001318605",
    "MU": "0000723125",
    "JPM": "0000019617",
    "XOM": "0000034088",
    "LLY": "0000059478",
}

ESTABLISHED_MEMECOINS: list[dict[str, str]] = [
    {
        "symbol": "DOGE",
        "chain": "dogecoin",
        "token_address": "native:DOGE",
        "cohort": "established",
        "primary_meta": "dog",
    },
    {
        "symbol": "SHIB",
        "chain": "ethereum",
        "token_address": "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce",
        "cohort": "established",
        "primary_meta": "dog",
    },
    {
        "symbol": "PEPE",
        "chain": "ethereum",
        "token_address": "0x6982508145454ce325ddbe47a25d4ec3d2311933",
        "cohort": "established",
        "primary_meta": "frog",
    },
    {
        "symbol": "BONK",
        "chain": "solana",
        "token_address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "cohort": "established",
        "primary_meta": "dog",
    },
    {
        "symbol": "WIF",
        "chain": "solana",
        "token_address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
        "cohort": "established",
        "primary_meta": "dog",
    },
    {
        "symbol": "FLOKI",
        "chain": "ethereum",
        "token_address": "0xcf0c122c6b73ff809c693db761e7baebe62b6a2e",
        "cohort": "established",
        "primary_meta": "dog",
    },
]

APPROVED_QUOTES = {
    "solana": {
        "So11111111111111111111111111111111111111112",
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    },
    "ethereum": {
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "0xdac17f958d2ee523a2206206994597c13d831ec7",
    },
    "robinhood": set(),
}

ROBINHOOD_CANONICAL_QUOTES = {
    "weth": "0x0bd7d308f8e1639fab988df18a8011f41eacad73",
    "usdg": "0x5fc5360d0400a0fd4f2af552add042d716f1d168",
}
APPROVED_QUOTES["robinhood"] = set(ROBINHOOD_CANONICAL_QUOTES.values())

MEMECOIN_INFRASTRUCTURE_SYMBOLS = {
    "AAVE",
    "BNB",
    "BTC",
    "DAI",
    "ETH",
    "FRAX",
    "JUP",
    "LINK",
    "PUMP",
    "RAY",
    "SOL",
    "UNI",
    "USDC",
    "USDG",
    "USDT",
    "WBTC",
    "WETH",
    "WSOL",
}

SUPPORTED_DISCOVERY_CHAINS = {"solana", "ethereum", "robinhood"}

GECKO_NETWORKS = {
    "solana": "solana",
    "ethereum": "eth",
}

DEXSCREENER_CHAINS = {
    "solana": "solana",
    "ethereum": "ethereum",
    "robinhood": "robinhood",
}


def normalize_chain(value: str) -> str:
    chain = value.strip().lower()
    aliases = {
        "eth": "ethereum",
        "mainnet": "ethereum",
        "sol": "solana",
        "robinhood chain": "robinhood",
        "4663": "robinhood",
    }
    return aliases.get(chain, chain)


def normalize_address(chain: str, value: str) -> str:
    address = value.strip()
    if normalize_chain(chain) in {"ethereum", "robinhood"}:
        return address.lower()
    return address


def crypto_symbols(focus: list[str] | None = None) -> list[str]:
    symbols = list(CRYPTO_UNIVERSE)
    for value in focus or []:
        symbol = value.strip().upper()
        if symbol in CRYPTO_UNIVERSE and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def tradfi_symbols(focus: list[str] | None = None) -> list[str]:
    symbols = TRADFI_BENCHMARKS + TRADFI_SECTORS + TRADFI_PROXIES + TRADFI_SP500_STOCKS
    out = list(dict.fromkeys(symbols))
    for value in focus or []:
        symbol = value.strip().upper()
        if symbol in TICKER_TO_CIK and symbol not in out:
            out.append(symbol)
    return out


def registry_metadata() -> dict[str, Any]:
    return {
        "version": REGISTRY_VERSION,
        "retrieved_at": REGISTRY_RETRIEVED_AT,
        "crypto_count": len(CRYPTO_UNIVERSE),
        "tradfi_count": len(tradfi_symbols()),
        "established_memecoin_count": len(ESTABLISHED_MEMECOINS),
        "supported_discovery_chains": sorted(SUPPORTED_DISCOVERY_CHAINS),
        "registries": {
            name: {
                **metadata,
                "age_days": registry_age_days(name),
                "fresh": registry_is_current(name),
            }
            for name, metadata in REGISTRIES.items()
        },
    }


def registry_age_days(name: str) -> int:
    metadata = REGISTRIES.get(name)
    if not metadata:
        raise ValueError(f"Unknown registry: {name}")
    retrieved = date.fromisoformat(str(metadata["retrieved_at"]))
    return max(0, (date.today() - retrieved).days)


def registry_is_current(name: str) -> bool:
    metadata = REGISTRIES.get(name)
    if not metadata:
        return False
    return registry_age_days(name) <= int(metadata["maximum_accepted_age_days"])
