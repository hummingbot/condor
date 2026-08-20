"""Drive the swap / AMM / CLMM read surface through condor's own MCP tools.

Same entry points an agent session hits — manage_gateway_swaps, manage_amm,
manage_clmm — so this exercises condor's argument handling and formatting, not
just hapi's HTTP surface. Nothing here moves funds; see test_lp_write.py for that.

    uv run python scripts_lp_test/test_lp_read.py
"""

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_servers.hummingbot_api import server  # noqa: E402

def tool(name):
    """The registered MCP tool as a callable (FastMCP may hand back a wrapper)."""
    obj = getattr(server, name)
    return getattr(obj, "fn", obj)


manage_gateway_swaps = tool("manage_gateway_swaps")
manage_amm = tool("manage_amm")
manage_clmm = tool("manage_clmm")

NET = "solana-mainnet-beta"
WALLET = "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5"

MET_CLMM = "2sf5NYcY4zUPXUSmG6f66mskb24t5F8S11pC1Nz5nQT3"   # SOL-USDC
ORCA_CLMM = "Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE"  # SOL-USDC
RAY_AMM = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2"    # SOL-USDC
MET_DAMM = "Bv65dPQKpUo7vRELhEGBkkm5wq9J3MvKGyUj8WxYtunM"   # unlisted mint, no symbol


async def step(title, coro):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")
    try:
        print((await coro).strip()[:1200])
    except Exception as e:
        print(f"!! {type(e).__name__}: {e}")


async def main():
    # ---- swaps -------------------------------------------------------------
    for connector in ["jupiter", "meteora/clmm", "raydium/amm"]:
        await step(
            f"swap quote — {connector}",
            manage_gateway_swaps(action="quote", connector=connector, network=NET,
                                 trading_pair="SOL-USDC", side="SELL", amount="0.01"))

    await step(
        "swap quote — bare 'raydium' resolves to a type on its own",
        manage_gateway_swaps(action="quote", connector="raydium", network=NET,
                             trading_pair="SOL-USDC", side="SELL", amount="0.01"))

    await step(
        "swap quote — unknown extra_params key must be rejected",
        manage_gateway_swaps(action="quote", connector="meteora", network=NET,
                             trading_pair="SOL-USDC", side="SELL", amount="0.01",
                             extra_params={"bogusKey": 1}))

    await step("swap search — recent swaps", manage_gateway_swaps(action="search", limit=5))

    # get_status needs a hash that exists, so it reads one back out of the search above
    # rather than hard-coding a swap that will age out of the table.
    recent = await manage_gateway_swaps(action="search", limit=1)
    match = re.search(r"'transaction_hash': '([^']+)'", recent)
    if match:
        await step("swap get_status — resolve a recorded swap",
                   manage_gateway_swaps(action="get_status", transaction_hash=match.group(1)))
    else:
        print("\n(no recorded swaps yet — skipping get_status)")

    await step(
        "swap get_status — an unsubmitted hash must 404, not read as success",
        manage_gateway_swaps(action="get_status", transaction_hash="1" * 88))

    # ---- CLMM --------------------------------------------------------------
    await step("clmm guide (no action = progressive disclosure)", manage_clmm())

    await step(
        "clmm position_info — the wallet's live orca position, symbols resolved",
        manage_clmm(action="position_info", connector="orca", network=NET,
                    position_address="4G5GyCPi9U75GQ9NwLAov7psKngofiHThPAJQJ48cvUR"))

    # ---- AMM ---------------------------------------------------------------
    await step("amm guide (no action)", manage_amm())

    for connector, pool in [("raydium", RAY_AMM), ("meteora", MET_DAMM)]:
        await step(f"amm pool_info — {connector}",
                   manage_amm(action="pool_info", connector=connector, network=NET,
                              pool_address=pool))

    await step(
        "amm quote_liquidity — raydium SOL-USDC",
        manage_amm(action="quote_liquidity", connector="raydium", network=NET,
                   pool_address=RAY_AMM, base_token_amount="0.01", quote_token_amount="1"))

    await step(
        "amm positions_owned — meteora DAMM v2",
        manage_amm(action="positions_owned", connector="meteora", network=NET,
                   wallet_address=WALLET))

    await step(
        "amm positions_owned — raydium must be rejected (fungible LP)",
        manage_amm(action="positions_owned", connector="raydium", network=NET,
                   wallet_address=WALLET))

    await step(
        "amm position_info — raydium LP balance in pool",
        manage_amm(action="position_info", connector="raydium", network=NET,
                   pool_address=RAY_AMM, wallet_address=WALLET))


asyncio.run(main())
