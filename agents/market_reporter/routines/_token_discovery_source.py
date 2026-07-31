"""Private collector for a bounded identity-safe Memecoin discovery universe."""

import asyncio
from typing import Any, Literal

from pydantic import Field, field_validator

from agents.market_reporter.routines._dexscreener_discovery import (
    collect_dexscreener,
)
from agents.market_reporter.routines._evidence import bundle_text, finalize_bundle
from agents.market_reporter.routines._gecko_discovery import (
    collect_gecko,
    collect_gecko_details,
)
from agents.market_reporter.routines._identity import (
    SUPPORTED_DISCOVERY_CHAINS,
    registry_is_current,
)
from agents.market_reporter.routines._models import BaseSourceConfig
from agents.market_reporter.routines._robinhood_identity import (
    confirm_contracts,
    fetch_stock_token_exclusions,
)
from agents.market_reporter.routines._solana_identity import observe_concentration
from agents.market_reporter.routines._token_selection import (
    build_items,
    coverage,
    seed_tokens,
    select_pairs,
)
from routines.base import RoutineResult

CATEGORY = "Market Reporter"


class Config(BaseSourceConfig):
    """Discover exact Solana, Ethereum, and Robinhood Memecoin pairs."""

    chains: list[Literal["solana", "ethereum", "robinhood"]] = Field(
        default_factory=lambda: ["solana", "ethereum", "robinhood"],
        min_length=1,
        max_length=3,
    )
    min_pair_age_hours: float = Field(default=6, ge=0, le=720)
    min_liquidity_usd: float = Field(default=50_000, ge=0, le=100_000_000)
    max_discovery_candidates: int = Field(default=100, ge=10, le=100)
    max_detailed_candidates: int = Field(default=40, ge=5, le=40)
    max_gecko_detail_candidates: int = Field(default=1, ge=0, le=1)
    max_solana_concentration_candidates: int = Field(default=1, ge=0, le=1)

    @field_validator("chains")
    @classmethod
    def unique_supported_chains(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Discovery chains must be unique")
        if not set(values).issubset(SUPPORTED_DISCOVERY_CHAINS):
            raise ValueError("Unsupported discovery chain")
        return values


async def run(config: Config, context: Any) -> RoutineResult:
    del context
    if config.strategy_key != "memecoin_market_intelligence":
        raise ValueError("Token discovery is Memecoin Strategy-only")
    async with asyncio.timeout(40):
        registry_task = (
            fetch_stock_token_exclusions()
            if "robinhood" in config.chains
            else _empty_registry()
        )
        (exclusions, stock_symbols, registry_fresh, registry_results), (
            gecko_pairs,
            gecko_results,
        ) = await asyncio.gather(registry_task, collect_gecko(config.chains))
        seeds = seed_tokens(
            chains=config.chains,
            focus_assets=config.focus_assets,
            gecko_pairs=gecko_pairs,
        )
        dex_pairs, dex_results, promotion_flags = await collect_dexscreener(
            seeds[: config.max_discovery_candidates],
            maximum_details=config.max_detailed_candidates,
        )
        selected = select_pairs(
            gecko_pairs + dex_pairs,
            maximum=config.max_detailed_candidates,
        )
        robinhood_addresses = [
            pair["token_address"]
            for pair in selected
            if pair["chain_id"] == "robinhood"
        ]
        confirmation_task = (
            confirm_contracts(robinhood_addresses)
            if robinhood_addresses
            else _empty_observations()
        )
        solana_addresses = [
            pair["token_address"] for pair in selected if pair["chain_id"] == "solana"
        ]
        solana_task = (
            observe_concentration(
                solana_addresses[: config.max_solana_concentration_candidates]
            )
            if solana_addresses and config.max_solana_concentration_candidates
            else _empty_observations()
        )
        gecko_task = (
            collect_gecko_details(
                selected,
                maximum=config.max_gecko_detail_candidates,
            )
            if config.max_gecko_detail_candidates
            else _empty_observations()
        )
        (
            (confirmations, confirmation_results),
            (solana_observations, solana_results),
            (gecko_details, gecko_detail_results),
        ) = await asyncio.gather(
            confirmation_task,
            solana_task,
            gecko_task,
        )

    items = build_items(
        selected,
        min_pair_age_hours=config.min_pair_age_hours,
        min_liquidity_usd=config.min_liquidity_usd,
        exclusions=exclusions,
        stock_symbols=stock_symbols,
        registry_fresh=registry_fresh,
        promotion_flags=promotion_flags,
        confirmations=confirmations,
        solana_observations=solana_observations,
        robinhood_registry_retrieved_at=(
            registry_results[0].retrieved_at if registry_results else ""
        ),
        gecko_details=gecko_details,
    )
    provider_results = (
        registry_results
        + gecko_results
        + dex_results
        + confirmation_results
        + solana_results
        + gecko_detail_results
    )
    successful_dexscreener = any(
        result.provider_id == "dexscreener" and result.status == "complete"
        for result in provider_results
    )
    optional_degradations = [
        result
        for result in provider_results
        if result.status != "complete"
        and (
            result.provider_id
            in {
                "geckoterminal",
                "solana_rpc",
                "robinhood_blockscout",
            }
            or (result.provider_id == "dexscreener" and successful_dexscreener)
        )
    ]
    if items and optional_degradations:
        provider_results = [
            result for result in provider_results if result not in optional_degradations
        ]
    coverage_payload = coverage(
        items,
        chains=config.chains,
        registry_fresh=registry_fresh,
    )
    coverage_payload["optional_source_degradations"] = [
        {
            "provider_id": result.provider_id,
            "error": result.error,
            "status_code": result.status_code,
        }
        for result in optional_degradations
    ]
    optional_warnings = []
    for result in optional_degradations:
        label = (
            "optional_request_degraded"
            if result.provider_id == "dexscreener"
            else "optional_source_unavailable"
        )
        optional_warnings.append(f"{label}:{result.provider_id}:{result.error}")
    bundle = finalize_bundle(
        source_type="token_discovery",
        strategy_key=config.strategy_key,
        scope=config.scope,
        items=items,
        provider_results=provider_results,
        coverage=coverage_payload,
        warnings=(
            ["robinhood_stock_token_registry_unavailable"]
            if "robinhood" in config.chains and not registry_fresh
            else []
        )
        + (
            ["approved_quote_registry_stale"]
            if not registry_is_current("approved_quotes")
            else []
        )
        + ["geckoterminal_top_pool_scan_omitted_public_rate_budget"]
        + optional_warnings,
    )
    table = [
        {
            "chain": item["chain_id"],
            "symbol": item["market"].get("symbol"),
            "token": item["token_address"],
            "liquidity_usd": item["market"].get("liquidity_usd"),
            "eligibility": item["eligibility"],
            "paid_visibility": item["paid_visibility"],
        }
        for item in bundle["items"]
    ]
    return RoutineResult(
        text=bundle_text(bundle, config.run_id),
        table_data=table,
        table_columns=[
            "chain",
            "symbol",
            "token",
            "liquidity_usd",
            "eligibility",
            "paid_visibility",
        ],
    )


async def _empty_registry():
    return set(), {}, True, []


async def _empty_observations():
    return {}, []
