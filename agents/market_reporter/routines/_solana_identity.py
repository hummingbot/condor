"""Optional bounded Solana concentration observations for final candidates."""

from __future__ import annotations

from typing import Any

from agents.market_reporter.routines._evidence import safe_float
from agents.market_reporter.routines._http import FetchResult, fetch_json


async def observe_concentration(
    addresses: list[str],
) -> tuple[dict[str, dict[str, Any]], list[FetchResult]]:
    unique = list(dict.fromkeys(addresses))[:10]
    batch = []
    for index, address in enumerate(unique):
        batch.extend(
            [
                {
                    "jsonrpc": "2.0",
                    "id": index + 1,
                    "method": "getTokenLargestAccounts",
                    "params": [address, {"commitment": "confirmed"}],
                },
                {
                    "jsonrpc": "2.0",
                    "id": len(unique) + index + 1,
                    "method": "getTokenSupply",
                    "params": [address, {"commitment": "confirmed"}],
                },
            ]
        )
    result = await fetch_json(
        "solana_rpc",
        "https://api.mainnet-beta.solana.com",
        method="POST",
        json_body=batch,
        retry=False,
    )
    responses = {}
    if result.status == "complete" and isinstance(result.data, list):
        responses = {
            int(row["id"]): row
            for row in result.data
            if isinstance(row, dict) and row.get("id") is not None
        }
    observations = {}
    for index, address in enumerate(unique):
        largest_response = responses.get(index + 1) or {}
        supply_response = responses.get(len(unique) + index + 1) or {}
        largest_rows = (largest_response.get("result") or {}).get("value") or []
        total_supply = safe_float(
            ((supply_response.get("result") or {}).get("value") or {}).get("amount")
        )
        top_amounts = [
            value
            for value in (safe_float(row.get("amount")) for row in largest_rows)
            if value is not None and value >= 0
        ]
        observations[address] = {
            "largest_accounts_status": (
                "complete"
                if largest_response.get("result") is not None
                else "unavailable"
            ),
            "token_supply_status": (
                "complete"
                if supply_response.get("result") is not None
                else "unavailable"
            ),
            "top_account_count_observed": len(top_amounts),
            "top_10_share_of_supply": (
                round(sum(top_amounts[:10]) / total_supply, 6)
                if total_supply and total_supply > 0 and top_amounts
                else None
            ),
            "interpretation_limit": (
                "Token-account concentration is observable; beneficial ownership "
                "and exchange custody are not identified."
            ),
        }
    return observations, [result]
