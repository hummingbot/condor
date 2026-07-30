"""Robinhood Chain canonical asset exclusions and bounded identity checks."""

from __future__ import annotations

import asyncio
from typing import Any

from agents.market_reporter.routines._http import FetchResult, fetch_json
from agents.market_reporter.routines._identity import normalize_address


async def fetch_stock_token_exclusions() -> (
    tuple[set[str], dict[str, str], bool, list[FetchResult]]
):
    result = await fetch_json(
        "robinhood_registry",
        "https://api.robinhood.com/rhj/assets",
        retry=False,
    )
    exclusions: set[str] = set()
    symbols: dict[str, str] = {}
    if result.status == "complete":
        for asset in (result.data or {}).get("assets") or []:
            symbol = str(asset.get("tokenSymbol") or "")
            for deployment in asset.get("deployments") or []:
                if int(deployment.get("chainId") or 0) != 4663:
                    continue
                address = normalize_address(
                    "robinhood", str(deployment.get("contractAddress") or "")
                )
                if address:
                    exclusions.add(address)
                    symbols[address] = symbol
    return exclusions, symbols, result.status == "complete", [result]


async def confirm_contracts(
    addresses: list[str],
) -> tuple[dict[str, dict[str, Any]], list[FetchResult]]:
    unique = list(dict.fromkeys(addresses))[:10]
    rpc_tasks = [
        fetch_json(
            "robinhood_rpc",
            "https://rpc.mainnet.chain.robinhood.com",
            method="POST",
            json_body={
                "jsonrpc": "2.0",
                "id": index + 1,
                "method": "eth_getCode",
                "params": [address, "latest"],
            },
            retry=False,
        )
        for index, address in enumerate(unique)
    ]
    explorer_tasks = [
        fetch_json(
            "robinhood_blockscout",
            f"https://robinhoodchain.blockscout.com/api/v2/addresses/{address}",
            retry=False,
        )
        for address in unique
    ]
    results: list[FetchResult] = list(
        await asyncio.gather(*(rpc_tasks + explorer_tasks))
    )
    output = {}
    for index, address in enumerate(unique):
        rpc = results[index]
        explorer = results[len(unique) + index]
        code = (rpc.data or {}).get("result") if rpc.status == "complete" else None
        output[address] = {
            "chain_id_numeric": 4663,
            "contract_code_present": bool(code and code != "0x"),
            "rpc_status": rpc.status,
            "explorer_status": explorer.status,
            "explorer_url": (
                f"https://robinhoodchain.blockscout.com/address/{address}"
            ),
        }
    return output, results
